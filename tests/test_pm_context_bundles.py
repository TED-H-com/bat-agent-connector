"""Accepted files enter an isolated analysis only through fixed original receipts."""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from bat_agent_connector import pm_bat_operations as bat
from bat_agent_connector import pm_context_bundles as bundles
from bat_agent_connector import pm_discussions, pm_plans, pm_runs, registry
from bat_agent_connector.operations import OperationError
from tests import test_pm_plan_operations as plans
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import snapshot
from tests.test_pm_bat_operations import bat_world  # noqa: F401
from tests.test_pm_native_operations import FakeRuntime

world = plans.world
pm = plans.pm
ready = plans.ready
ORIGINAL_BINDING, ORIGINAL_CARRIER = bat._binding, bat.start_carrier


@pytest.fixture
def daemon(ready):
    return ready['d']


@pytest.fixture
async def bundle_world(ready, bat_world, mock, monkeypatch):  # noqa: F811
    w = ready
    monkeypatch.setattr(bat, '_binding', ORIGINAL_BINDING)
    monkeypatch.setattr(bat, 'start_carrier', ORIGINAL_CARRIER)
    mock.handlers.pop('git:log', None)
    adopted = await plans.adopt(w)
    assert adopted['status'] == 'succeeded', adopted
    w['adopted'] = adopted
    settings, instances = {}, []
    w['d'].ops.context['pm_native_runtime_factory'] = lambda *args, **kwargs: FakeRuntime(
        *args, **kwargs, settings=settings, instances=instances)
    w['d'].ops.context['pm_native_process_inspector'] = lambda identity: {
        'state': 'unknown', 'process': identity, 'proof': 'identity_unavailable', 'observed_at': 1.0}
    w.update(native_settings=settings, native_instances=instances, bat_settings=bat_world[1])
    return w


def preview(w, **kwargs):
    return bundles.preview(w['d'].ops, plans.OWNER, **plans.target(w), **kwargs)


def request(w, action='pm.run.analyze', *, ref=None, purpose='Discuss the fixed accepted login requirement', **params):
    ops = w['d'].ops
    return ops.create(plans.OWNER, action=action, target=plans.target(w),
        params={'purpose': purpose, **({'context_bundle_ref': ref} if ref is not None else {}), **params},
        preconditions={'expected_version': plans.version(w)}, idempotency_key=uuid.uuid4().hex)[0]


async def finish(w, operation):
    await settle_operations(w['d'].ops)
    return w['d'].ops.get(operation['operation_id'])


@pytest.mark.parametrize('action', ['pm.run.analyze', 'pm.run.bat.analyze'])
async def test_exact_accepted_document_bytes_reach_existing_runtime(bundle_world, mock, action):
    w = bundle_world
    human = snapshot(w['human'])
    selected = preview(w)
    assert selected['coverage']['accepted_document_complete'] is True
    assert not selected['execution_authorized']
    fixed = pm_plans.revision_files(w['d'].ops.db, w['workspace_id'], w['revision']['revision_id'])
    out = await finish(w, request(w, action, ref=selected['context_bundle_ref']))
    assert out['status'] == 'succeeded', json.dumps(out, indent=2)
    run = pm_runs.get_by_operation(w['d'].ops.db, out['operation_id'])
    context = run['context']
    assert context['version'] == 2
    assert context['context_bundle']['files'] == fixed['files']
    assert context['context_bundle']['ref'] == selected['context_bundle_ref']
    assert context['context_bundle']['decision']['revision_id'] == fixed['revision_id']
    assert context['context_bundle']['git_receipt']['commit_sha'] == w['adopted']['result']['commit_sha']
    assert len(pm_runs._json(context).encode('utf-8')) <= bundles.MAX_BYTES
    assert run['context_digest'] == pm_runs._digest(context)
    assert w['adopted']['operation_id'] in context['source_operation_ids']
    assert context['context_bundle']['coverage']['adoption_authorized'] is False
    assert snapshot(w['human']) == human
    assert not w['d'].ops.db.execute('SELECT 1 FROM tasks').fetchone()
    if action == bat.ACTION:
        sid = run['session_binding']['session_id']
        assert registry.get('h1', sid)['status'] == 'stopped'
        assert 'worktree:create' not in mock.channels()
    else:
        assert len(w['native_instances']) == 1 and w['native_instances'][0].sends == 1


async def test_absent_selector_keeps_exact_legacy_schema_and_digest(bundle_world):
    w = bundle_world
    expected = pm_discussions.context(w['d'].ops, plans.OWNER, **plans.target(w), max_chars=32000)
    purpose = 'Preserve legacy context unchanged'
    expected['purpose'] = purpose
    out = await finish(w, request(w, purpose=purpose))
    assert out['status'] == 'succeeded', out
    run = pm_runs.get_by_operation(w['d'].ops.db, out['operation_id'])
    assert run['context'] == expected
    assert run['context_digest'] == pm_runs._digest(expected)
    assert run['context']['version'] == 1 and 'context_bundle' not in run['context']


@pytest.mark.parametrize('field', ['revision_id', 'document_digest', 'adoption_operation_id', 'git_receipt_digest', 'extra'])
async def test_selector_cannot_substitute_revision_decision_or_git_receipt(bundle_world, field):
    w = bundle_world
    ref = preview(w)['context_bundle_ref']
    ref[field] = ('pmrev_' + '0' * 32 if field == 'revision_id' else
        'op_' + '0' * 32 if field == 'adoption_operation_id' else '0' * 64)
    with pytest.raises(OperationError):
        request(w, ref=ref)
    assert not w['native_instances']


async def test_old_selection_does_not_become_new_adopted_revision(bundle_world):
    w = bundle_world
    original = preview(w)['context_bundle_ref']
    later = await plans.draft(w, markdown='# New owner-adopted document\nAn additional acceptance condition.\n')
    adopted = await plans.adopt(w, later)
    assert adopted['status'] == 'succeeded', adopted
    with pytest.raises(OperationError, match='PM_CONTEXT_BUNDLE_CHANGED'):
        request(w, ref=original)
    assert not w['native_instances']


async def test_multibyte_files_are_whole_or_explicitly_omitted(bundle_world):
    w = bundle_world
    large = await plans.draft(w, markdown='界' * 100000)
    adopted = await plans.adopt(w, large)
    assert adopted['status'] == 'succeeded', adopted
    selected = preview(w, max_chars=64000)
    assert selected['coverage']['accepted_document_complete'] is False
    assert selected['coverage']['acceptance_coverage'] == 'incomplete_original_document'
    omitted = [row for row in selected['manifest'] if not row['included']]
    assert len(omitted) == 1 and omitted[0]['path'].endswith('/plan.md') and omitted[0]['utf8_bytes'] == 300000
    out = await finish(w, request(w, ref=selected['context_bundle_ref'], purpose='界' * 4000, max_chars=64000))
    assert out['status'] == 'succeeded', out
    context = pm_runs.get_by_operation(w['d'].ops.db, out['operation_id'])['context']
    assert context['context_bundle']['manifest'] == selected['manifest']
    assert omitted[0]['path'] not in context['context_bundle']['files']
    assert context['context_bundle']['coverage']['accepted_document_complete'] is False
    assert len(pm_runs._json(context).encode('utf-8')) <= bundles.MAX_BYTES
    assert context['context_bundle']['coverage']['execution_authorized'] is False


@pytest.mark.parametrize('action', ['pm.run.analyze', 'pm.run.bat.analyze'])
async def test_configuration_withdrawal_after_admission_sends_no_model_input(bundle_world, action):
    w = bundle_world
    op = request(w, action, ref=preview(w)['context_bundle_ref'])
    config = w['d'].fleet.config.github.repos['o/r']
    from dataclasses import replace
    w['d'].fleet.config.github.repos['o/r'] = replace(config, sync=None)
    result = await finish(w, op)
    assert result['status'] != 'succeeded'
    assert not w['native_instances']


async def test_frozen_bundle_is_not_rebuilt_after_unknown_native_ack(bundle_world, monkeypatch):
    w = bundle_world
    from bat_agent_connector.operations import OpContext
    original_step = OpContext.step
    lost = False
    async def lost_receipt(ctx, name, *args, **kwargs):
        nonlocal lost
        result = await original_step(ctx, name, *args, **kwargs)
        if name == 'pm.native.execute' and not lost:
            lost = True
            from bat_agent_connector.operations import Uncertain
            raise Uncertain(name, 'fixture loss after original execution receipt')
        return result
    monkeypatch.setattr(OpContext, 'step', lost_receipt)
    op = await finish(w, request(w, ref=preview(w)['context_bundle_ref']))
    assert op['status'] == 'uncertain', op
    frozen = copy.deepcopy(pm_runs.get_by_operation(w['d'].ops.db, op['operation_id'])['context'])
    await plans.act(w, 'pm.message.append', {'content': 'Later discussion must not rewrite original bytes.'})
    w['d'].ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (op['operation_id'],))
    recovered = await finish(w, op)
    assert recovered['status'] == 'succeeded', recovered
    assert pm_runs.get_by_operation(w['d'].ops.db, op['operation_id'])['context'] == frozen
    assert len(w['native_instances']) == 1 and w['native_instances'][0].sends == 1


async def test_bundle_http_rpc_mcp_share_original_preview_and_reject_ambiguous_queries(bundle_world, monkeypatch):
    import asyncio

    from bat_agent_connector import mcp_server
    from tests.test_api_v1 import http, token
    w = bundle_world
    d = w['d']
    server = await asyncio.start_server(d._handle, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    auth = token(d, plans.OWNER.actor, *plans.OWNER.scopes)
    target = plans.target(w)
    path = f"/api/v1/pm/workspaces/{target['workspace_id']}/threads/{target['thread_id']}/context-bundle"
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    mcp, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = d.ops.db.execute('SELECT count(*) FROM operations').fetchone()[0]
    try:
        status, expected = await http(port, 'GET', path, tok=auth)
        assert status == 200, expected
        assert expected == await d.call_api('pm_context_bundle_preview', target.copy(), plans.OWNER)
        result = await mcp.call_tool('pm_context_bundle_preview', target)
        if isinstance(result, tuple):
            content, structured = result
            actual = structured if structured is not None else json.loads(content[0].text)
        else:
            content = result.content if hasattr(result, 'content') else result
            actual = json.loads(content[0].text)
        assert actual == expected
        for query in ['max_chars=1', 'max_chars=64001', 'max_chars=', 'max_chars=2000&max_chars=3000',
                      'workspace_id=foreign', 'actor=owner', 'limit=1']:
            status, body = await http(port, 'GET', path + '?' + query, tok=auth)
            assert status == 422, (query, body)
        assert (await http(port, 'GET', path))[0] == 401
        foreign = token(d, 'foreign-observer', 'observe')
        assert (await http(port, 'GET', path, tok=foreign))[0] == 404
        caps = (await http(port, 'GET', '/api/v1/capabilities', tok=auth))[1]
        assert caps['features']['pm_workspaces']['accepted_context_bundle']['schema'] == bundles.SCHEMA
        assert before == d.ops.db.execute('SELECT count(*) FROM operations').fetchone()[0]
        assert not w['native_instances']
    finally:
        server.close()
        await server.wait_closed()
        await fleet.close()


async def test_withdrawn_workspace_repository_hides_bundle_run_reply_and_operation(bundle_world):
    w = bundle_world
    op = await finish(w, request(w, ref=preview(w)['context_bundle_ref']))
    assert op['status'] == 'succeeded', op
    ops = w['d'].ops
    run = pm_runs.get_by_operation(ops.db, op['operation_id'])
    message_id = op['result']['outcome']['message_id']
    assert bundles.operation_visible(ops.db, plans.OWNER, op)
    ops.db.execute('UPDATE pm_workspaces SET repositories=? WHERE workspace_id=?', ('[]', w['workspace_id']))
    with pytest.raises(OperationError):
        preview(w)
    with pytest.raises(OperationError):
        pm_runs.read(ops, plans.OWNER, w['workspace_id'], run['run_id'])
    assert not bundles.operation_visible(ops.db, plans.OWNER, op)
    assert message_id not in json.dumps(pm_discussions.get_thread(ops, plans.OWNER, **plans.target(w)))
    assert op['operation_id'] not in json.dumps(ops.list(principal=plans.OWNER))
    # Removing access does not rewrite or erase the immutable original receipt.
    assert pm_runs.get(ops.db, run['run_id'])['context_digest'] == run['context_digest']


async def test_complete_saved_bundle_survives_fresh_service_journal_without_new_native_process(bundle_world, monkeypatch):
    from bat_agent_connector import pm_native_operations, pm_workspace
    from bat_agent_connector.operations import OpContext, OperationService, Uncertain
    from bat_agent_connector.task_journal import Journal
    w = bundle_world
    original_step = OpContext.step
    lost = False
    async def lose_ack(ctx, name, *args, **kwargs):
        nonlocal lost
        value = await original_step(ctx, name, *args, **kwargs)
        if name == 'pm.native.execute' and not lost:
            lost = True
            raise Uncertain(name, 'fixture lost execution ACK before import')
        return value
    monkeypatch.setattr(OpContext, 'step', lose_ack)
    op = await finish(w, request(w, ref=preview(w)['context_bundle_ref']))
    assert op['status'] == 'uncertain', op
    original = pm_runs.get_by_operation(w['d'].ops.db, op['operation_id'])
    journal = Journal(w['d'].journal.path)
    resumed = OperationService(journal)
    pm_workspace.install(resumed)
    pm_discussions.install(resumed)
    pm_native_operations.install(resumed)
    resumed.context.update(w['d'].ops.context)
    def no_new_process(*args, **kwargs):
        pytest.fail('Original bundle recovery launched a second native process')
    resumed.context['pm_native_runtime_factory'] = no_new_process
    try:
        journal.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (op['operation_id'],))
        await settle_operations(resumed)
        result = resumed.get(op['operation_id'])
        assert result['status'] == 'succeeded', result
        recovered = pm_runs.get_by_operation(journal.db, op['operation_id'])
        assert recovered['context'] == original['context'] and recovered['context_digest'] == original['context_digest']
        assert len(w['native_instances']) == 1 and w['native_instances'][0].sends == 1
    finally:
        journal.close()
