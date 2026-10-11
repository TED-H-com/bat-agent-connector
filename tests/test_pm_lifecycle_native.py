"""Lifecycle with the existing native runner and exact original process receipts."""
from __future__ import annotations

import sqlite3

import pytest

from bat_agent_connector import pm_lifecycle as life
from bat_agent_connector import pm_native_journal, pm_runs
from bat_agent_connector.operations import OperationError
from tests.test_pm_discussions import prepared, read
from tests.test_pm_lifecycle import decide
from tests.test_pm_native_operations import OWNER, await_runtime, finish, request, retry, run, writer
from tests.test_pm_native_operations import world as native_world

base_world = native_world


@pytest.fixture
def world(base_world):
    life.install(base_world[0])
    return base_world


@pytest.mark.parametrize('decision', ['pause', 'close'])
async def test_queued_native_analysis_cannot_freeze_or_launch_after_stop(world, decision):
    ops, settings, instances = world
    target = await prepared(ops)
    op = request(ops, target)
    await decide(ops, target['workspace_id'], decision)
    await ops._execute(op['operation_id'])
    stopped = ops.get(op['operation_id'])
    assert stopped['error_code'] == 'PM_LIFECYCLE_STOPPED'
    assert not instances and run(ops, op) is None and writer(ops, target) is None
    await decide(ops, target['workspace_id'], 'reopen', params={'stage': 'planning'})
    # The original intent stays terminal; retrying the same key cannot create a turn.
    original = {key: op[key] for key in ('action', 'target', 'params', 'preconditions', 'idempotency_key')}
    assert ops.create(OWNER, **original)[0]['operation_id'] == op['operation_id']
    assert not instances


@pytest.mark.parametrize('phase', ['block_preflight', 'block_turn'])
async def test_already_admitted_single_turn_can_finish_after_stop_with_verified_reply(world, phase):
    ops, settings, instances = world
    target = await prepared(ops)
    before = read(ops, target)
    settings[phase] = True
    op = request(ops, target)
    runtime = await await_runtime(ops, instances, op)
    stopped = await decide(ops, target['workspace_id'])
    assert stopped['result']['status'] == 'pausing'
    inflight = life.list_inflight(ops, OWNER, target['workspace_id'])['inflight']
    assert inflight[0]['operation_id'] == op['operation_id'] and inflight[0]['writer']['run_id'] == runtime.run_id
    assert runtime.sends == int(phase == 'block_turn')
    runtime.release.set()
    done = await finish(ops, op)
    assert done['status'] == 'succeeded', done
    assert len(instances) == 1 and runtime.sends == 1 and runtime.closed
    assert run(ops, op)['state'] == 'completed' and writer(ops, target) is None
    assert read(ops, target)['messages'][-1]['role'] == 'assistant'
    assert read(ops, target)['thread']['mode'] == before['thread']['mode']
    assert life.get_lifecycle(ops, OWNER, target['workspace_id'])['lifecycle']['status'] == 'paused'
    assert life.list_inflight(ops, OWNER, target['workspace_id'])['inflight'][0]['disposition'] == 'settled'
    with pytest.raises(OperationError, match='PM_LIFECYCLE_STOPPED'):
        request(ops, target)


async def test_unknown_native_process_retains_writer_even_after_explicit_closure_retention(world):
    ops, settings, instances = world
    target = await prepared(ops)
    settings['block_turn'], settings['exit_unknown'] = True, True
    op = request(ops, target)
    runtime = await await_runtime(ops, instances, op)
    stopped = await decide(ops, target['workspace_id'], 'close')
    runtime.release.set()
    unknown = await finish(ops, op)
    assert unknown['status'] == 'uncertain' and writer(ops, target) == runtime.run_id
    retained = await decide(ops, target['workspace_id'], 'retain_inflight', params={
        'transition_id': stopped['result']['transition_id'], 'operation_ids': [op['operation_id']],
        'reason': 'Keep this exact unknown process result for later observation'})
    assert retained['result']['status'] == 'closed'
    assert writer(ops, target) == runtime.run_id
    assert pm_native_journal.death_proof(ops.db, runtime.run_id) is None
    assert not read(ops, target)['messages']
    await decide(ops, target['workspace_id'], 'reopen', params={'stage': 'idea'})
    still_unknown = await retry(ops, op)
    assert still_unknown['status'] == 'uncertain' and runtime.sends == 1 and len(instances) == 1
    assert writer(ops, target) == runtime.run_id
    competing = await finish(ops, request(ops, target))
    assert competing['error_code'] == 'PM_WRITER_BUSY'


async def test_verified_terminal_import_receipt_recovery_after_pause_never_resends(world):
    ops, settings, instances = world
    target = await prepared(ops)
    # Valid original native terminal receipts survive an atomic chat import failure.
    ops.db.execute("CREATE TRIGGER lifecycle_fail_message BEFORE INSERT ON pm_messages BEGIN SELECT RAISE(ABORT,'local receipt unavailable'); END")
    op = await finish(ops, request(ops, target))
    assert op['status'] == 'uncertain', op
    original = run(ops, op)
    assert pm_native_journal.death_proof(ops.db, original['run_id'])
    assert writer(ops, target) == original['run_id']
    await decide(ops, target['workspace_id'])
    ops.db.execute('DROP TRIGGER lifecycle_fail_message')
    done = await retry(ops, op)
    assert done['status'] == 'succeeded', done
    assert len(instances) == 1 and instances[0].sends == 1 and writer(ops, target) is None
    assert read(ops, target)['messages'][-1]['provenance']['run_id'] == original['run_id']
    assert life.get_lifecycle(ops, OWNER, target['workspace_id'])['lifecycle']['status'] == 'paused'
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ops.db.execute("DELETE FROM pm_lifecycle_operations WHERE operation_id=?", (op['operation_id'],))
    assert pm_runs.get(ops.db, original['run_id'])['state'] == 'completed'
