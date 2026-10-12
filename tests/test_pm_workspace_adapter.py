"""Original workspace choice, real owned Git context and fake bounded runtimes."""
from __future__ import annotations

import json
import uuid

import pytest

from bat_agent_connector import pm_access, pm_discussions, pm_runs, pm_workspace
from bat_agent_connector import pm_workspace_adapter as adapters
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests import test_pm_bat_structured_analysis as structured_analysis
from tests import test_pm_context_bundles as bundles
from tests import test_pm_conversation_proposals as conversation
from tests import test_pm_plan_operations as plans
from tests.test_checkpoints import snapshot
from tests.test_pm_native_operations import await_runtime

world, pm, ready, daemon = bundles.world, bundles.pm, bundles.ready, bundles.daemon
bat_world, bundle_world = bundles.bat_world, bundles.bundle_world
structured = structured_analysis.structured


@pytest.fixture
def chosen_world(bundle_world):
    adapters.install(bundle_world['d'].ops)
    return bundle_world


def request(w, adapter=adapters.ADAPTERS[0], *, principal=plans.OWNER, key=None, pre=None):
    ops = w['d'].ops
    return ops.create(principal, action=adapters.ACTION, target={'workspace_id': w['workspace_id']},
        params={'adapter': adapter, 'rationale': 'Continue the same accepted PM documents with this bounded adapter'},
        preconditions=pre or adapters.read(ops, principal, w['workspace_id'])['preconditions'],
        idempotency_key=key or uuid.uuid4().hex)[0]


async def select(w, adapter=adapters.ADAPTERS[0]):
    out = await bundles.finish(w, request(w, adapter))
    assert out['status'] == 'succeeded', out
    return out


async def test_choice_then_completed_native_to_bat_keeps_exact_files_and_original_receipts(chosen_world):
    w, ops = chosen_world, chosen_world['d'].ops
    human = snapshot(w['human'])
    first = await select(w)
    fixed = bundles.preview(w)['context_bundle_ref']
    native = await bundles.finish(w, bundles.request(w, ref=fixed))
    assert native['status'] == 'succeeded', native
    before = adapters.read(ops, plans.OWNER, w['workspace_id'])
    switched = await select(w, adapters.ADAPTERS[1])
    result = switched['result']
    assert result['previous_choice'] == adapters._ref(adapters._row(ops.db, w['workspace_id'], 1))
    assert result['continuity']['accepted_context_ref'] == fixed
    assert result['writer_generation'] == before['writer']['generation']
    assert not result['execution_authorized'] and not result['successor_started']
    assert result['runtime_preflight'] == 'not_performed'
    bat = await bundles.finish(w, bundles.request(w, 'pm.run.bat.analyze', ref=fixed))
    assert bat['status'] == 'succeeded', bat
    runs = [pm_runs.get_by_operation(ops.db, op['operation_id']) for op in (native, bat)]
    assert runs[1]['generation'] > runs[0]['generation']
    assert runs[0]['context']['context_bundle']['files'] == runs[1]['context']['context_bundle']['files']
    assert ops.get(first['operation_id'])['result'] == first['result']
    assert snapshot(w['human']) == human
    assert not ops.db.execute('SELECT 1 FROM tasks').fetchone()


async def test_same_original_choice_key_recovers_before_changed_head_and_writer(chosen_world):
    w, ops = chosen_world, chosen_world['d'].ops
    key = uuid.uuid4().hex
    original = request(w, key=key)
    done = await bundles.finish(w, original)
    await select(w, adapters.ADAPTERS[1])
    replay = request(w, key=key, pre=original['preconditions'])
    assert replay['operation_id'] == original['operation_id']
    assert replay['result'] == done['result']
    assert ops.db.execute('SELECT count(*) FROM pm_adapter_choices').fetchone()[0] == 2


@pytest.mark.parametrize('exit_unknown', [False, True])
async def test_running_or_unknown_writer_never_released_by_selection(chosen_world, exit_unknown):
    w, ops = chosen_world, chosen_world['d'].ops
    await select(w)
    w['native_settings'].update(block_turn=True, exit_unknown=exit_unknown)
    op = bundles.request(w)
    runtime = await await_runtime(ops, w['native_instances'], op)
    try:
        run = pm_runs.get_by_operation(ops.db, op['operation_id'])
        public = adapters.read(ops, plans.OWNER, w['workspace_id'])
        assert public['writer'] == {'generation': run['generation'], 'occupied': True}
        assert run['run_id'] not in json.dumps(public)
        with pytest.raises(OperationError, match='PM_WRITER_BUSY'):
            request(w, adapters.ADAPTERS[1])
    finally:
        runtime.release.set()
    result = await bundles.finish(w, op)
    if exit_unknown:
        assert result['status'] != 'succeeded', result
        with pytest.raises(OperationError, match='PM_WRITER_BUSY'):
            request(w, adapters.ADAPTERS[1])
    else:
        assert result['status'] == 'succeeded', result
        assert (await select(w, adapters.ADAPTERS[1]))['result']['version'] == 2


async def test_same_workspace_reader_cannot_discover_private_active_writer_id(chosen_world, monkeypatch):
    w, ops = chosen_world, chosen_world['d'].ops
    reader = Principal('limited-source-reader', frozenset({'observe'}))
    workspace = pm_workspace.get_workspace(ops.db, plans.OWNER, w['workspace_id'])
    member = ops.create(plans.OWNER, action='pm.workspace.member.set', target={'workspace_id': w['workspace_id']},
        params={'actor': reader.actor, 'role': 'reader'}, preconditions={'expected_version': workspace['version']},
        idempotency_key=uuid.uuid4().hex)[0]
    assert (await bundles.finish(w, member))['status'] == 'succeeded'
    await select(w)
    message = await plans.act(w, 'pm.message.append', {'content': 'A separately protected original discussion source'})
    source = ops.db.execute('SELECT operation_id FROM pm_messages WHERE message_id=?', (message['message_id'],)).fetchone()[0]
    w['native_settings']['block_turn'] = True
    op = ops.create(plans.OWNER, **conversation.propose_request(w))[0]
    runtime = await await_runtime(ops, w['native_instances'], op)
    try:
        run = pm_runs.get_by_operation(ops.db, op['operation_id'])
        assert pm_runs.read(ops, reader, w['workspace_id'], run['run_id'])['run_id'] == run['run_id']
        original = pm_access.visible
        # Withdraw one actual original message at the central source access seam,
        # keeping both membership and the accepted document carrier readable.
        monkeypatch.setattr(pm_access, 'visible', lambda db, principal, value:
            not (principal.actor == reader.actor and value['operation_id'] == source) and original(db, principal, value))
        with pytest.raises(OperationError):
            pm_runs.read(ops, reader, w['workspace_id'], run['run_id'])
        public = adapters.read(ops, reader, w['workspace_id'])
        assert public['choice'] is not None
        assert public['writer'] == {'occupied': True, 'generation': run['generation']}
        assert run['run_id'] not in json.dumps(public) and op['operation_id'] not in json.dumps(public)
    finally:
        runtime.release.set()
        await bundles.finish(w, op)


async def test_queued_analysis_cannot_start_after_choice_changes(chosen_world):
    w, ops = chosen_world, chosen_world['d'].ops
    await select(w)
    pending = bundles.request(w)
    switch = request(w, adapters.ADAPTERS[1])
    # Execute only the local choice first; the already admitted analysis has no writer yet.
    await ops._execute(switch['operation_id'])
    assert ops.get(switch['operation_id'])['status'] == 'succeeded'
    result = await bundles.finish(w, pending)
    assert result['status'] == 'failed', result
    assert result['error_code'] in {'PM_ADAPTER_CHANGED', 'PM_ADAPTER_MISMATCH'}
    assert not w['native_instances']
    assert pm_runs.get_by_operation(ops.db, pending['operation_id']) is None


async def test_first_choice_also_fences_an_already_queued_legacy_intent(chosen_world):
    w, ops = chosen_world, chosen_world['d'].ops
    pending = bundles.request(w)
    choice = request(w)
    await ops._execute(choice['operation_id'])
    result = await bundles.finish(w, pending)
    assert result['status'] == 'failed', result
    assert result['error_code'] == 'PM_ADAPTER_CHANGED'
    assert not w['native_instances']


async def test_stale_choice_cas_and_mismatched_adapter_cannot_create_new_effect(chosen_world):
    w, ops = chosen_world, chosen_world['d'].ops
    pre = adapters.read(ops, plans.OWNER, w['workspace_id'])['preconditions']
    await select(w, adapters.ADAPTERS[1])
    with pytest.raises(OperationError, match='PM_ADAPTER_CHANGED'):
        request(w, pre=pre)
    with pytest.raises(OperationError, match='PM_ADAPTER_MISMATCH'):
        bundles.request(w)
    assert not w['native_instances']


@pytest.mark.parametrize('change', ['plan', 'membership'])
async def test_final_local_frame_rechecks_original_plan_and_owner(chosen_world, change):
    w, ops = chosen_world, chosen_world['d'].ops
    op = request(w)
    if change == 'plan':
        # A real plan operation wins before the queued local selection executes.
        current = plans.pm_plans.head(ops.db, w['workspace_id'])
        other = ops.create(plans.OWNER, action='pm.plan.draft', target=plans.target(w),
            params={'previous_revision_id': current['latest_revision_id'], 'markdown': '# Updated exact proposal'},
            preconditions={'expected_version': plans.version(w), 'expected_plan_version': current['version']},
            idempotency_key=uuid.uuid4().hex)[0]
        await ops._execute(other['operation_id'])
        assert ops.get(other['operation_id'])['status'] == 'succeeded'
    else:
        plans.revoke(w)
    await ops._execute(op['operation_id'])
    assert ops.get(op['operation_id'])['status'] == 'failed'
    assert not ops.db.execute('SELECT 1 FROM pm_adapter_choices').fetchone()


@pytest.mark.parametrize('action', structured_analysis.ACTIONS)
async def test_both_structured_actions_retain_the_selected_original_choice(structured, action):
    w, ops = structured, structured['d'].ops
    adapters.install(ops)
    choice = await select(w, adapters.ADAPTERS[1])
    value = structured_analysis.request(w, action)
    wrong = {**value, 'params': {**value['params'], 'adapter': adapters.ADAPTERS[0]}}
    with pytest.raises(OperationError, match='PM_ADAPTER_MISMATCH'):
        ops.create(plans.OWNER, **wrong)
    result = await structured_analysis.execute(w, value)
    assert result['status'] == 'succeeded', result
    assert result['external_refs']['admission_binding']['pm_adapter_choice']['operation_id'] == choice['operation_id']
    assert structured_analysis.result(w, result)['provenance']['adapter'] == adapters.ADAPTERS[1]


@pytest.mark.parametrize('scope', ['observe', 'manage', 'start'])
async def test_selection_requires_owner_and_all_original_scopes(chosen_world, scope):
    w = chosen_world
    limited = Principal(plans.OWNER.actor, plans.OWNER.scopes - {scope})
    with pytest.raises(OperationError):
        request(w, principal=limited)
    plans.revoke(w)
    with pytest.raises(OperationError):
        request(w)


async def test_source_withdrawal_hides_original_choice_and_derived_reply(chosen_world):
    w, ops = chosen_world, chosen_world['d'].ops
    choice = await select(w)
    out = await bundles.finish(w, bundles.request(w))
    assert out['status'] == 'succeeded', out
    assert adapters.operation_visible(ops.db, plans.OWNER, choice)
    ops.db.execute('UPDATE pm_workspaces SET repositories=? WHERE workspace_id=?', ('[]', w['workspace_id']))
    assert not adapters.operation_visible(ops.db, plans.OWNER, choice)
    assert not pm_access.visible(ops.db, plans.OWNER, out)
    value = adapters.read(ops, plans.OWNER, w['workspace_id'])
    assert value['choice'] is None and value['preconditions'] is None and not value['available']
    with pytest.raises(OperationError):
        pm_discussions.get_thread(ops, plans.OWNER, **plans.target(w))


async def test_later_choice_keeps_preceding_original_decision_source_access(chosen_world, monkeypatch):
    w, ops = chosen_world, chosen_world['d'].ops
    first = await select(w)
    second = await select(w, adapters.ADAPTERS[1])
    saved = ops.db.execute('SELECT document FROM pm_adapter_choices WHERE operation_id=?', (second['operation_id'],)).fetchone()[0]
    original = pm_access.operation_source_visible
    # The earlier original decision becomes unavailable independently of the
    # currently accepted carrier. No row or current membership is replaced.
    monkeypatch.setattr(pm_access, 'operation_source_visible', lambda db, principal, identity:
        identity != first['operation_id'] and original(db, principal, identity))
    value = adapters.read(ops, plans.OWNER, w['workspace_id'])
    assert value['choice'] is None and value['reason'] == 'original_sources_unavailable'
    assert not adapters.operation_visible(ops.db, plans.OWNER, second)
    with pytest.raises(OperationError):
        bundles.request(w, 'pm.run.bat.analyze')
    assert ops.db.execute('SELECT document FROM pm_adapter_choices WHERE operation_id=?', (second['operation_id'],)).fetchone()[0] == saved


async def test_lost_local_receipt_recovers_after_owner_downgrade_without_new_selection(chosen_world, monkeypatch):
    w, ops = chosen_world, chosen_world['d'].ops
    op = request(w)
    from bat_agent_connector.operations import OpContext, Uncertain
    original = OpContext.effect
    def lost(ctx, name, fn, **kwargs):
        result = original(ctx, name, fn, **kwargs)
        if ctx.operation_id == op['operation_id']:
            raise Uncertain(name, 'Original local selection saved before outer completion')
        return result
    # The local effect's saved row is independent of the outer operation result.
    with monkeypatch.context() as patch:
        patch.setattr(OpContext, 'effect', lost)
        await ops._execute(op['operation_id'])
    row = ops.db.execute('SELECT document FROM pm_adapter_choices WHERE operation_id=?', (op['operation_id'],)).fetchone()
    assert row is not None
    plans.revoke(w)
    from bat_agent_connector.operations import OperationService
    from bat_agent_connector.task_journal import Journal
    journal = Journal(w['d'].journal.path)
    resumed = OperationService(journal)
    adapters.install(resumed)
    resumed.context.update(ops.context)
    try:
        await resumed._execute(op['operation_id'])
        saved = resumed.get(op['operation_id'])
        assert saved['status'] == 'succeeded', saved
        assert saved['result'] == json.loads(row[0])
        assert journal.db.execute('SELECT count(*) FROM pm_adapter_choices').fetchone()[0] == 1
    finally:
        journal.close()


async def test_shared_http_rpc_mcp_read_and_registered_action_use_original_choice(chosen_world, monkeypatch):
    import asyncio

    from bat_agent_connector import mcp_server
    from tests.test_api_v1 import http, token
    w, d = chosen_world, chosen_world['d']
    auth = token(d, plans.OWNER.actor, *plans.OWNER.scopes)
    server = await asyncio.start_server(d._handle, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    mcp, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    path = f"/api/v1/pm/workspaces/{w['workspace_id']}/capabilities"
    try:
        status, read = await http(port, 'GET', path, tok=auth)
        assert status == 200, read
        body = {'action': adapters.ACTION, 'target': {'workspace_id': w['workspace_id']},
            'params': {'adapter': adapters.ADAPTERS[1], 'rationale': 'Use the original owned BAT carrier'},
            'preconditions': read['workspace_adapter']['preconditions']}
        status, admitted = await http(port, 'POST', '/api/v1/operations?wait=3', tok=auth,
            body=body, headers={'Idempotency-Key': uuid.uuid4().hex})
        assert status == 202, admitted  # Newly admitted operations retain HTTP202 even after wait succeeds.
        assert admitted['operation']['status'] == 'succeeded', admitted
        expected = (await http(port, 'GET', path, tok=auth))[1]
        target = {'workspace_id': w['workspace_id']}
        assert expected == await d.call_api('pm_capabilities_get', target, plans.OWNER)
        result = await mcp.call_tool('pm_capabilities_get', target)
        if isinstance(result, tuple):
            content, structured = result
            actual = structured if structured is not None else json.loads(content[0].text)
        else:
            content = result.content if hasattr(result, 'content') else result
            actual = json.loads(content[0].text)
        assert actual == expected
        assert expected['workspace_adapter']['choice']['operation_id'] == admitted['operation']['operation_id']
        caps = (await http(port, 'GET', '/api/v1/capabilities', tok=auth))[1]
        assert caps['features']['pm_workspaces']['workspace_adapter'] == {'version': 1, 'action': adapters.ACTION}
        assert any(row['action'] == adapters.ACTION and row['allowed'] for row in caps['actions'])
        assert not w['native_instances']
    finally:
        server.close()
        await server.wait_closed()
        await fleet.close()
