"""Cleanup-only discovery uses unlink authority, never source-reader authority."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_external_history as refs
from bat_agent_connector import pm_external_history_links as links
from bat_agent_connector import pm_reads, pm_source_lineage
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from bat_agent_connector.session_history_config import HistoryConfig
from bat_agent_connector.task_daemon import TaskDaemon
from tests.test_external_history_links import daemon, linked_work, ready, retained, submit  # noqa: F401
from tests.test_pm_api import OWNER
from tests.test_work_items import act

METHOD = 'pm_history_cleanup_list'
PEER = Principal('cleanup-member', OWNER.scopes)
MISSING_WORK = 'wi_' + 'f' * 20
MISSING_LINK = 'hxl_' + 'f' * 32
MISSING_WORKSPACE = 'ws_' + 'f' * 32


async def member(d, wid, actor, role):
    version = d.ops.db.execute('SELECT version FROM pm_workspaces WHERE workspace_id=?', (wid,)).fetchone()[0]
    await act(d, OWNER, 'pm.workspace.member.set', {'workspace_id': wid},
        {'actor': actor, 'role': role}, {'expected_version': version})


@pytest.fixture
async def cleanup_case(linked_work):  # noqa: F811
    d, req, source = linked_work
    op = await submit(d, req)
    return d, req['target'], source, op['result']['link_id']


def project(page, work):
    assert set(page) == {'links', 'next_after'}
    for row in page['links']:
        assert set(row) == {'link_id', 'work_item_id', 'version'}
        assert row['work_item_id'] == work and type(row['version']) is int and row['version'] > 0
    return [row['link_id'] for row in page['links']]


def add_rows(d, target, original, count, *, actor=OWNER.actor, state='active'):
    """Synthetic persisted relationships; distinct Attempt IDs exercise cleanup's boundary."""
    first = d.ops.db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM pm_external_links').fetchone()[0]
    identities = [f'hxl_{number:032x}' for number in range(first, first + count)]
    with d.journal.tx():
        d.ops.db.executemany('''INSERT INTO pm_external_links
            (link_id,workspace_id,work_item_id,attempt_id,excerpt_id,purpose,version,state,
             created_by,created_at,creation_operation_id,last_operation_id)
            SELECT ?,?,?,?,excerpt_id,purpose,?,?,?,created_at,creation_operation_id,last_operation_id
            FROM pm_external_links WHERE link_id=?''',
            [(lid, target['workspace_id'], target['work_item_id'], f'att_{first + i:032x}',
              1 if state == 'active' else 2, state, actor, original) for i, lid in enumerate(identities)])
    return identities


def withdraw_rows(d, identities):
    with d.journal.tx():
        d.ops.db.executemany("UPDATE pm_external_links SET state='withdrawn',version=version+1 WHERE link_id=?",
            [(identity,) for identity in identities])


def existing_refusal(d, target, who=OWNER):
    with pytest.raises(OperationError) as error:
        pm_reads.read(d.ops, who, 'pm_history_link_get',
            {'workspace_id': target['workspace_id'], 'link_id': MISSING_LINK})
    return error.value.status, error.value.code, error.value.message


@pytest.mark.parametrize('scopes', [frozenset({'observe'}), frozenset({'manage'}), frozenset()])
async def test_cleanup_scopes_precede_any_database_lookup(scopes):
    class NoQueries:
        def execute(self, *args, **kwargs):
            pytest.fail('Scope refusal must precede every database lookup')
    db = NoQueries()
    ops = SimpleNamespace(db=db)
    d = SimpleNamespace(ops=ops, journal=SimpleNamespace(db=db))
    who = Principal('scope-only', scopes)
    for params in ({'workspace_id': 'ws_' + '1' * 32, 'work_item_id': 'wi_' + '2' * 20},
                   {'workspace_id': MISSING_WORKSPACE, 'work_item_id': MISSING_WORK, 'after': MISSING_LINK}):
        with pytest.raises(OperationError) as error:
            await TaskDaemon.call_api(d, METHOD, params, who)
        assert error.value.status == 403 and error.value.code == 'FORBIDDEN'
        with pytest.raises(OperationError) as direct:
            links.cleanup_list(ops, who, params)
        assert (direct.value.status, direct.value.code, direct.value.message) == (
            error.value.status, error.value.code, error.value.message)


@pytest.mark.parametrize('role,creator,admin', [
    ('owner', False, False), ('contributor', True, False), ('contributor', False, False),
    ('reader', True, False), ('reader', False, False), (None, True, False), (None, False, False),
    (None, False, True), ('contributor', False, True),
])
async def test_cleanup_role_matrix_and_reader_creator_unlink(linked_work, role, creator, admin):  # noqa: F811
    d, req, source = linked_work
    who = Principal(PEER.actor, OWNER.scopes, admin=admin)
    if role is not None or creator:
        await member(d, source.workspace_id, who.actor, 'contributor')
    d.history_collector.config = HistoryConfig(10, (replace(source, readers=(OWNER.actor, who.actor)),))
    lid = (await submit(d, req, who if creator else OWNER))['result']['link_id']
    if role is not None or creator:
        await member(d, source.workspace_id, who.actor, role)
    d.history_collector.config = HistoryConfig(10, ())
    if role is None:
        expected = existing_refusal(d, req['target'], who)
        with pytest.raises(OperationError) as error:
            await d.call_api(METHOD, req['target'], who)
        assert (error.value.status, error.value.code, error.value.message) == expected
        return
    page = await d.call_api(METHOD, req['target'], who)
    allowed = role == 'owner' or creator
    assert project(page, req['target']['work_item_id']) == ([lid] if allowed else [])
    if allowed:
        request = {'action': 'pm.history.unlink', 'target': req['target'] | {'link_id': lid},
            'params': {}, 'preconditions': {'expected_link_version': page['links'][0]['version']},
            'idempotency_key': 'cleanup-role'}
        assert (await submit(d, request, who))['result'] == {'link_id': lid, 'version': 2, 'state': 'withdrawn'}
    else:
        with pytest.raises(OperationError) as error:
            await d.call_api(METHOD, req['target'] | {'after': lid}, who)
        assert (error.value.status, error.value.code, error.value.message) == existing_refusal(d, req['target'], who)


async def test_cleanup_owner_discovers_former_creator_without_attribution(linked_work):  # noqa: F811
    d, req, source = linked_work
    await member(d, source.workspace_id, PEER.actor, 'contributor')
    d.history_collector.config = HistoryConfig(10, (replace(source, readers=(OWNER.actor, PEER.actor)),))
    lid = (await submit(d, req, PEER))['result']['link_id']
    await member(d, source.workspace_id, PEER.actor, None)
    page = await d.call_api(METHOD, req['target'], OWNER)
    assert project(page, req['target']['work_item_id']) == [lid]


async def test_cleanup_is_read_only_and_does_not_access_source_state(cleanup_case, monkeypatch, mock):
    d, target, source, lid = cleanup_case
    expected = await d.call_api(METHOD, target, OWNER)
    assert project(expected, target['work_item_id']) == [lid]
    d.history_collector.config = HistoryConfig(10, ())
    source.path.unlink()
    d.ops.db.execute('DROP TABLE temp.session_history_current_grants')
    d.ops.db.execute('DROP TABLE temp.session_history_grant_state')
    def forbidden(*args, **kwargs):
        pytest.fail('Cleanup must not resolve, collect or activate source grants')
    monkeypatch.setattr(refs, 'current_grants', forbidden)
    monkeypatch.setattr(refs, 'preview', forbidden)
    monkeypatch.setattr(d.history_collector, 'collect_once', forbidden)
    tables = ('operations', 'api_events', 'pm_external_links', 'pm_external_excerpts',
        'pm_external_link_events', 'session_history_entries')
    before = [d.ops.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in tables]
    changes, invocations = d.ops.db.total_changes, list(mock.invokes)
    assert await d.call_api(METHOD, target, OWNER) == expected
    assert d.ops.db.total_changes == changes
    assert [d.ops.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in tables] == before
    assert not d.ops.db.execute("SELECT name FROM sqlite_temp_master WHERE name LIKE 'session_history_%'").fetchall()
    assert mock.invokes == invocations


@pytest.mark.parametrize('who', [OWNER, PEER], ids=['owner', 'creator'])
async def test_cleanup_120_row_walk_has_no_duplicates_and_allows_new_links(cleanup_case, who):
    d, target, _, original = cleanup_case
    await member(d, target['workspace_id'], PEER.actor, 'contributor')
    withdraw_rows(d, [original])
    expected = add_rows(d, target, original, 120, actor=who.actor)
    if who == PEER:
        add_rows(d, target, original, 60, actor=OWNER.actor)
    first = await d.call_api(METHOD, target | {'limit': 17}, who)
    found = project(first, target['work_item_id'])
    after = first['next_after']
    assert after == expected[16]
    # A once-returned, now-withdrawn anchor must still resolve for continuation.
    withdraw_rows(d, [after])
    added = add_rows(d, target, original, 1, actor=who.actor)
    while after is not None:
        page = await d.call_api(METHOD, target | {'after': after, 'limit': 17}, who)
        found.extend(project(page, target['work_item_id']))
        assert page['next_after'] != after
        after = page['next_after']
    assert found == expected + added and len(set(found)) == 121
    current = await d.call_api(METHOD, target, who)
    assert len(current['links']) == 50  # default
    assert current['next_after'] == (expected[:16] + expected[17:])[49]


async def test_cleanup_empty_page_retains_last_scanned_cursor(cleanup_case, monkeypatch):
    d, target, _, original = cleanup_case
    later = add_rows(d, target, original, 2)
    real = links._cleanup
    once = False
    def withdraw_after_selection(db, principal, wid, identity):
        nonlocal once
        if not once:
            once = True
            withdraw_rows(d, [original, later[0]])
        return real(db, principal, wid, identity)
    with monkeypatch.context() as patch:
        patch.setattr(links, '_cleanup', withdraw_after_selection)
        page = await d.call_api(METHOD, target | {'limit': 2}, OWNER)
    assert page == {'links': [], 'next_after': later[0]}
    remaining = await d.call_api(METHOD, target | {'limit': 2, 'after': page['next_after']}, OWNER)
    assert project(remaining, target['work_item_id']) == [later[1]]
    assert remaining['next_after'] is None


async def test_cleanup_invalid_parameters(cleanup_case):
    d, target, _, _ = cleanup_case
    for params in [
        {'limit': 0}, {'limit': 51}, {'limit': -1}, {'limit': True}, {'limit': 1.0}, {'limit': '1'},
        {'after': 0}, {'after': True}, {'after': ''}, {'after': 'null'}, {'after': 'hxl_' + 'A' * 32},
        {'after': 'hxl_' + '1' * 31}, {'after': 'hxl_' + '1' * 33}, {'after': []},
        {'workspace_id': None}, {'work_item_id': 1}, {'actor': 'other'},
    ]:
        with pytest.raises(OperationError) as error:
            await d.call_api(METHOD, target | params, OWNER)
        assert error.value.status == 422, params


@pytest.mark.parametrize('change', ['member', 'hidden', 'role', 'binding'])
async def test_cleanup_final_scope_guard_refuses_changes(cleanup_case, monkeypatch, change):
    d, target, _, _ = cleanup_case
    expected = existing_refusal(d, target)
    original = pm_source_lineage.read_response
    def revoke(db, principal, value):
        result = original(db, principal, value)
        if change == 'member':
            db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?',
                (target['workspace_id'], principal.actor))
        elif change == 'hidden':
            db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)',
                ('work_item', target['work_item_id'], 'op_' + 'e' * 32, 'fixture'))
        elif change == 'role':
            db.execute("UPDATE pm_workspace_members SET role='contributor' WHERE workspace_id=? AND actor=?",
                (target['workspace_id'], principal.actor))
        else:
            db.execute('''INSERT INTO pm_work_bindings
                (binding_id,workspace_id,work_item_id,project_binding_id,project_id,work_version,
                 previous_binding_id,plan_revision_id,requirement_ids,rationale,actor,created_at,operation_id)
                SELECT ?,workspace_id,work_item_id,project_binding_id,project_id,work_version,
                    binding_id,plan_revision_id,requirement_ids,rationale,actor,created_at,?
                FROM pm_work_bindings WHERE workspace_id=? AND work_item_id=? ORDER BY sequence DESC LIMIT 1''',
                ('pmb_' + 'e' * 32, 'op_' + 'e' * 32, target['workspace_id'], target['work_item_id']))
        return result
    monkeypatch.setattr(pm_source_lineage, 'read_response', revoke)
    with pytest.raises(OperationError) as error:
        await d.call_api(METHOD, target, OWNER)
    assert (error.value.status, error.value.code, error.value.message) == expected


async def test_cleanup_partial_indexes_bound_candidates_to_selected_authority(cleanup_case, monkeypatch):
    d, target, _, original = cleanup_case
    await member(d, target['workspace_id'], PEER.actor, 'contributor')
    own = add_rows(d, target, original, 60, actor=PEER.actor)
    add_rows(d, target, original, 1000, state='withdrawn')
    add_rows(d, target | {'workspace_id': MISSING_WORKSPACE}, original, 1000)
    add_rows(d, target | {'work_item_id': MISSING_WORK}, original, 1000)
    add_rows(d, target, original, 1000)
    # Verified with the installed Python 3.10 and 3.13 SQLite versions. Pin only
    # the indexed seek, not the full version-dependent EXPLAIN text.
    for name, sql, params in [('owner', links._CLEANUP_OWNER_SQL, (*target.values(), 0, 51)),
                             ('creator', links._CLEANUP_CREATOR_SQL, (*target.values(), PEER.actor, 0, 51))]:
        plan = [row[3] for row in d.ops.db.execute('EXPLAIN QUERY PLAN ' + sql, params)]
        print(name, plan)
        index = 'pm_external_links_cleanup_work' if name == 'owner' else 'pm_external_links_cleanup_creator'
        assert any('SEARCH' in step and index in step for step in plan)
        assert not any('TEMP B-TREE' in step for step in plan)
    calls, statements = [], []
    real = links._cleanup
    def checked(db, principal, wid, identity):
        calls.append(identity)
        return real(db, principal, wid, identity)
    monkeypatch.setattr(links, '_cleanup', checked)
    d.ops.db.set_trace_callback(statements.append)
    try:
        page = await d.call_api(METHOD, target | {'limit': 50}, PEER)
    finally:
        d.ops.db.set_trace_callback(None)
    assert project(page, target['work_item_id']) == own[:50] and page['next_after'] == own[49]
    assert set(calls) <= set(own[:50]) and len(calls) <= 3 * 50 + 2
    candidate_queries = [s for s in statements if s.startswith('SELECT link_id,sequence FROM pm_external_links')]
    assert len(candidate_queries) == 1 and candidate_queries[0].endswith('LIMIT 51')


@pytest.mark.parametrize('change', ['membership', 'work_visibility', 'owner_role'])
async def test_cleanup_revocation_between_pages_refuses_continuation(cleanup_case, change):
    d, target, _, original = cleanup_case
    await member(d, target['workspace_id'], PEER.actor, 'owner')
    add_rows(d, target, original, 2)
    first = await d.call_api(METHOD, target | {'limit': 1}, PEER)
    assert first['next_after'] == original
    expected = existing_refusal(d, target, PEER)
    if change == 'membership':
        await member(d, target['workspace_id'], PEER.actor, None)
    elif change == 'owner_role':
        await member(d, target['workspace_id'], PEER.actor, 'contributor')
    else:
        d.ops.db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)',
            ('work_item', target['work_item_id'], 'op_' + 'f' * 32, 'fixture'))
    with pytest.raises(OperationError) as error:
        await d.call_api(METHOD, target | {'after': first['next_after'], 'limit': 1}, PEER)
    assert (error.value.status, error.value.code, error.value.message) == expected


async def test_cleanup_withdrawal_at_final_response_omits_row_without_losing_cursor(cleanup_case, monkeypatch):
    d, target, _, original = cleanup_case
    add_rows(d, target, original, 1)
    final = pm_source_lineage.read_response
    def withdrawn(db, principal, page):
        result = final(db, principal, page)
        withdraw_rows(d, [original])
        return result
    monkeypatch.setattr(pm_source_lineage, 'read_response', withdrawn)
    assert await d.call_api(METHOD, target | {'limit': 1}, OWNER) == {'links': [], 'next_after': original}
