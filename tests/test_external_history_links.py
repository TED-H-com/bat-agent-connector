"""Explicit retained-history relations through real operations and current grants."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from bat_agent_connector import pm_evidence, pm_reads, session_history, work_items
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.session_history_config import HistoryConfig
from tests.operation_helpers import settle_operations
from tests.test_external_history_refs import daemon, preview, ready, retained  # noqa: F401
from tests.test_pm_api import OWNER
from tests.test_work_items import act


@pytest.fixture
async def linked_work(retained):  # noqa: F811
    d, wid, source, entry, _ = retained
    pid = (await act(d, OWNER, 'project.create', params={'name': 'Example'}))['result']['project_id']
    wi = (await act(d, OWNER, 'work_item.create', {'project_id': pid}, {'title': 'Review result'}))['result']['work_item_id']
    await act(d, OWNER, 'pm.project.bind', {'workspace_id': wid, 'project_id': pid}, {'rationale': 'Track'},
        {'expected_workspace_version': 1, 'expected_project_version': 1})
    target = {'workspace_id': wid, 'work_item_id': wi}
    await act(d, OWNER, 'pm.work.bind', target, {'rationale': 'Track'},
        {'expected_work_version': 1, 'expected_binding_id': None})
    request = {'action': 'pm.history.link', 'target': target, 'params': {'fixed_ref': preview(retained)['ref']},
        'preconditions': {'expected_work_version': 1, 'expected_binding_id': pm_evidence._binding(d.ops.db, wid, wi)['binding_id']},
        'idempotency_key': 'link-original'}
    return d, request, source


async def submit(d, request, who=OWNER):
    op, _ = d.ops.create(who, **request)
    await settle_operations(d.ops)
    result = d.ops.get(op['operation_id'], principal=who)
    assert result['status'] == 'succeeded', result
    return result


def read_link(d, request, link_id, who=OWNER):
    return pm_reads.read(d.ops, who, 'pm_history_link_get',
        {'workspace_id': request['target']['workspace_id'], 'link_id': link_id})['link']


def counts(d):
    return tuple(d.ops.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        for table in ('pm_external_excerpts', 'pm_external_links', 'pm_external_link_events'))


def invariant(d, target):
    return {'work': work_items.work_item_get(d.ops.db, target['work_item_id']),
        'attempts': [tuple(r) for r in d.ops.db.execute('SELECT * FROM work_attempts')],
        'progress': pm_reads.read(d.ops, OWNER, 'pm_overview_get', {'workspace_id': target['workspace_id']})['progress'],
        'sources': session_history.list_sources(d.ops, OWNER, target['workspace_id']),
        'capabilities': session_history.capabilities()}


async def test_link_duplicate_replace_unlink_and_unchanged_delivery(linked_work):
    d, req, _ = linked_work
    before = invariant(d, req['target'])
    assert counts(d) == (0, 0, 0)  # Matching checkout/session metadata never creates a relation.
    op = await submit(d, req)
    link = read_link(d, req, op['result']['link_id'])
    assert link['purpose'] == 'reference' and link['state'] == 'active' and link['version'] == 1
    assert link['fixed_ref'] == req['params']['fixed_ref']
    assert (await submit(d, req))['operation_id'] == op['operation_id']
    assert (await submit(d, req | {'idempotency_key': 'different'}))['result'] == op['result']
    assert counts(d) == (1, 1, 1)
    with pytest.raises(OperationError, match='IDEMPOTENCY_CONFLICT'):
        d.ops.create(OWNER, **(req | {'params': req['params'] | {'attempt_id': 'att_' + '1' * 32}}))
    from bat_agent_connector import pm_external_history as refs
    ref = req['params']['fixed_ref']
    selected = refs.preview(d.ops.db, OWNER, ref['workspace_id'], ref['source_id'], ref['entry_id'],
        ref['content_digest'], {'kind': 'text', 'part_index': 0, 'start_codepoint': 1, 'end_codepoint': 4})['ref']
    replacement = req | {'idempotency_key': 'replace', 'params': {'fixed_ref': selected,
        'replaces_link_id': link['link_id'], 'expected_old_link_version': 1}}
    new = await submit(d, replacement)
    assert read_link(d, req, link['link_id'])['state'] == 'withdrawn'
    assert counts(d) == (2, 2, 3)
    assert invariant(d, req['target']) == before
    unlink = {'action': 'pm.history.unlink', 'target': req['target'] | {'link_id': new['result']['link_id']},
        'params': {}, 'preconditions': {'expected_link_version': 1}, 'idempotency_key': 'unlink'}
    result = (await submit(d, unlink))['result']
    assert result == {'link_id': new['result']['link_id'], 'version': 2, 'state': 'withdrawn'}
    with pytest.raises(OperationError, match='PM_HISTORY_LINK_CHANGED'):
        d.ops.create(OWNER, **(unlink | {'idempotency_key': 'stale'}))
    assert invariant(d, req['target']) == before


@pytest.mark.parametrize('role,grant,admin,allowed', [
    ('owner', True, False, True), ('contributor', True, False, True), ('reader', True, False, False),
    ('owner', False, False, False), ('contributor', False, False, False), ('reader', False, False, False),
    (None, False, True, False), (None, True, True, False), (None, True, False, False)])
async def test_membership_and_source_grant_are_both_required(linked_work, role, grant, admin, allowed):
    d, req, source = linked_work
    actor = Principal('reviewer', frozenset({'manage', 'observe'}), admin=admin)
    if role:
        await act(d, OWNER, 'pm.workspace.member.set', {'workspace_id': source.workspace_id},
            {'actor': actor.actor, 'role': role}, {'expected_version': 1})
    d.history_collector.config = HistoryConfig(10, (replace(source, readers=(OWNER.actor, actor.actor) if grant else (OWNER.actor,)),))
    if allowed:
        await submit(d, req, actor)
    else:
        with pytest.raises(OperationError) as exc:
            d.ops.create(actor, **req)
        assert exc.value.status == 404
        assert counts(d) == (0, 0, 0)


async def test_grant_withdrawal_before_effect_and_after_commit_hides_global_reads(linked_work):
    d, req, source = linked_work
    op, _ = d.ops.create(OWNER, **req)
    from bat_agent_connector import managed_setup
    managed_setup._activate(d, replace(d._config, session_history=HistoryConfig()), {}, 'before-effect')
    await settle_operations(d.ops)
    assert counts(d) == (0, 0, 0)
    assert d.ops.get(op['operation_id'])['status'] != 'succeeded'
    d.history_collector.config = HistoryConfig(10, (source,))
    op = await submit(d, req | {'idempotency_key': 'authorized'})
    d.history_collector.config = HistoryConfig(10, ())
    for read in (lambda: d.ops.get(op['operation_id'], principal=OWNER),
                 lambda: read_link(d, req, op['result']['link_id'])):
        with pytest.raises(OperationError) as exc:
            read()
        assert exc.value.status == 404
    assert op['operation_id'] not in json.dumps(d.ops.list(principal=OWNER))
    assert op['operation_id'] not in json.dumps(await d.call_api('api_events', {}, OWNER))
    with pytest.raises(OperationError):
        d.ops.create(OWNER, **(req | {'idempotency_key': 'authorized'}))
    clean = {'action': 'pm.history.unlink', 'target': req['target'] | {'link_id': op['result']['link_id']},
        'preconditions': {'expected_link_version': 1}, 'idempotency_key': 'cleanup'}
    assert set((await submit(d, clean))['result']) == {'link_id', 'state', 'version'}
    assert counts(d) == (1, 1, 2)


@pytest.mark.parametrize('barrier', ['admission', 'local_commit', 'receipt_return'])
async def test_receipt_recovery_does_not_repeat_effect(linked_work, monkeypatch, barrier):
    d, req, _ = linked_work
    class Interrupted(BaseException):
        pass
    original = OpContext.effect
    def interrupted(ctx, name, fn, **kwargs):
        if name == 'pm.history.apply':
            if barrier == 'local_commit':
                def abort():
                    fn()
                    raise Interrupted()
                return original(ctx, name, abort, **kwargs)
            original(ctx, name, fn, **kwargs)
            raise Interrupted()
        return original(ctx, name, fn, **kwargs)
    monkeypatch.setattr(OpContext, 'effect', interrupted)
    op, _ = d.ops.create(OWNER, **req)
    if barrier != 'admission':
        with pytest.raises(Interrupted):
            await d.ops._execute(op['operation_id'])
    assert counts(d) == ((1, 1, 1) if barrier == 'receipt_return' else (0, 0, 0))
    monkeypatch.setattr(OpContext, 'effect', original)
    restarted = OperationService(d.journal, actions=list(d.ops.actions.values()))
    restarted.context.update(d.ops.context)
    d.ops = restarted
    await settle_operations(d.ops)
    assert counts(d) == (1, 1, 1)
    assert (await submit(d, req))['operation_id'] == op['operation_id']


async def test_schema_is_idempotent_and_history_is_immutable(linked_work):
    from bat_agent_connector import pm_external_history_links as links
    d, req, _ = linked_work
    before = d.ops.db.execute('PRAGMA user_version').fetchone()[0]
    links.install(d.ops)
    links.install(d.ops)
    await submit(d, req)
    assert d.ops.db.execute('PRAGMA user_version').fetchone()[0] == before
    for table in ('pm_external_excerpts', 'pm_external_link_events'):
        with pytest.raises(sqlite3.IntegrityError):
            d.ops.db.execute(f'DELETE FROM {table}')
    with pytest.raises(sqlite3.IntegrityError):
        d.ops.db.execute("UPDATE pm_external_links SET purpose='derived_origin'")
    assert 'parts' not in d.ops.db.execute('SELECT canonical_ref FROM pm_external_excerpts').fetchone()[0]


@pytest.mark.parametrize('change', ['projection', 'connection', 'workspace', 'binding', 'digest'])
async def test_unproven_sources_and_preconditions_write_nothing(linked_work, change):
    d, req, source = linked_work
    if change == 'projection':
        d.ops.db.execute('DROP TABLE temp.session_history_current_grants')
    elif change == 'connection':
        other = sqlite3.connect(d.journal.path)
        other.row_factory = sqlite3.Row
        from bat_agent_connector import pm_external_history_links as links
        try:
            with pytest.raises(OperationError) as exc:
                links._admitter('pm.history.link')(type('Reader', (), {'db': other})(), OWNER,
                    req['target'], req['params'], req['preconditions'])
            assert exc.value.status == 404
        finally:
            other.close()
        return
    elif change == 'workspace':
        req['target'] = req['target'] | {'workspace_id': 'ws_' + 'f' * 32}
    elif change == 'binding':
        req['preconditions']['expected_binding_id'] = 'pmwb_' + 'f' * 32
    else:
        req['params']['fixed_ref']['source_digest'] = 'f' * 64
    with pytest.raises(OperationError) as exc:
        d.ops.create(OWNER, **req)
    assert exc.value.status == (409 if change in {'binding', 'digest'} else 404)
    assert counts(d) == (0, 0, 0)


async def test_unlink_requires_creator_or_owner_and_replacement_is_atomic(linked_work):
    d, req, source = linked_work
    op = await submit(d, req)
    contributor = Principal('contributor', OWNER.scopes)
    await act(d, OWNER, 'pm.workspace.member.set', {'workspace_id': source.workspace_id},
        {'actor': contributor.actor, 'role': 'contributor'}, {'expected_version': 1})
    d.history_collector.config = HistoryConfig(10, (replace(source, readers=(OWNER.actor, contributor.actor)),))
    unlink = {'action': 'pm.history.unlink', 'target': req['target'] | {'link_id': op['result']['link_id']},
        'preconditions': {'expected_link_version': 1}, 'idempotency_key': 'denied'}
    with pytest.raises(OperationError) as exc:
        d.ops.create(contributor, **unlink)
    assert exc.value.status == 404
    with pytest.raises(OperationError):
        d.ops.create(contributor, **(req | {'idempotency_key': 'bad-replace', 'params': req['params'] | {
            'replaces_link_id': op['result']['link_id'], 'expected_old_link_version': 1}}))
    assert counts(d) == (1, 1, 1)


async def test_activation_between_resolution_and_response_refuses_cached_read(linked_work, monkeypatch):
    from bat_agent_connector import managed_setup
    from bat_agent_connector import pm_external_history_links as links
    d, req, _ = linked_work
    op = await submit(d, req)
    original = links.link_get
    def withdraw(*args, **kwargs):
        result = original(*args, **kwargs)
        managed_setup._activate(d, replace(d._config, session_history=HistoryConfig()), {}, 'withdraw-source')
        return result
    monkeypatch.setattr(links, 'link_get', withdraw)
    with pytest.raises(OperationError, match='PM_HISTORY_SOURCE_UNAVAILABLE'):
        read_link(d, req, op['result']['link_id'])
    assert counts(d) == (1, 1, 1)


async def test_ungranted_relations_do_not_create_pagination_hints(linked_work, retained):  # noqa: F811
    d, req, _ = linked_work
    await submit(d, req)
    ref = preview(retained, {'kind': 'text', 'part_index': 0, 'start_codepoint': 0, 'end_codepoint': 1})['ref']
    await submit(d, req | {'idempotency_key': 'second-excerpt', 'params': {'fixed_ref': ref}})
    d.history_collector.config = HistoryConfig(10, ())
    result = pm_reads.read(d.ops, OWNER, 'pm_history_links_list', req['target'] | {'limit': 1})
    assert result == {'links': [], 'next_after': None}


async def test_unavailable_work_lineage_and_missing_work_share_refusal(linked_work):
    d, req, _ = linked_work
    errors = []
    for missing in (True, False):
        target = req['target'] | {'work_item_id': 'wi_' + 'f' * 20} if missing else req['target']
        if not missing:
            d.ops.db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)', ('work_item', target['work_item_id'], 'op_' + 'f' * 32, 'fixture'))
        for invoke in (lambda: d.ops.create(OWNER, **(req | {'target': target})),
                       lambda: pm_reads.read(d.ops, OWNER, 'pm_history_links_list', target)):
            with pytest.raises(OperationError) as exc:
                invoke()
            errors.append((exc.value.status, exc.value.code, exc.value.message))
    assert len(set(errors)) == 1 and errors[0][0] == 404
    assert counts(d) == (0, 0, 0)
