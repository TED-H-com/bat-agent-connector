"""Direct holds end only through the ordinary original-marker collector."""
import pytest

from bat_agent_connector import pm_wip_count, pm_workspace_wip
from bat_agent_connector.operations import OperationError
from tests import test_pm_attempt_results as results
from tests import test_pm_attempt_results_direct as producer
from tests.operation_helpers import settle_operations
from tests.test_pm_attempt_result_marker import terminal
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip import read
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


@pytest.mark.parametrize('variant', ['one', 'two', 'work_first', 'work_last'])
async def test_collected_direct_and_work_inputs_release(start_ready, mock, variant):
    await producer.test_direct_markers_use_real_collector(start_ready, mock, variant)
    value = read(start_ready['d'].ops, start_ready['target'], STARTER_OWNER)
    assert value['coverage']['status'] == 'complete' and value['used'] == 0


async def test_queued_direct_receipt_releases(start_ready, mock):
    await producer.test_queued_direct_marker_records_original_attribution(start_ready, mock)
    value = read(start_ready['d'].ops, start_ready['target'], STARTER_OWNER)
    assert value['coverage']['status'] == 'complete' and value['used'] == 0


@pytest.mark.parametrize('case', ['terminal', 'process_ended', 'hidden', 'budget'])
async def test_direct_hold_from_acceptance_to_original_receipt(start_ready, mock, monkeypatch, case):
    w = start_ready
    ops = w['d'].ops
    att, _ = await closed_attempt(w, mock)
    op, _ = ops.create(STARTER_OWNER, action='session.send',
        target={'host': 'h1', 'session_id': att['session_id']}, params={'text': 'Synthetic later input'},
        preconditions={}, idempotency_key='direct-wip-end-to-end')
    assert read(ops, w['target'], STARTER_OWNER)['breakdown']['unresolved_execution'] == 1
    await settle_operations(ops)
    op = ops.get(op['operation_id'])
    assert op['status'] == 'succeeded'
    assert read(ops, w['target'], STARTER_OWNER)['used'] == 1
    if case == 'hidden':
        original = ops.get
        def hidden(oid, **kwargs):
            if oid == op['operation_id'] and kwargs.get('principal'):
                raise OperationError('NOT_FOUND', 'Hidden synthetic source', 404)
            return original(oid, **kwargs)
        monkeypatch.setattr(ops, 'get', hidden)
    elif case == 'budget':
        collector = pm_wip_count._Collector(ops, STARTER_OWNER, w['workspace_id'], pm_workspace_wip.time.time())
        collector.scan()
        monkeypatch.setattr(pm_workspace_wip, 'MAX_RECORDS', collector.used_records - 1)
    else:
        terminal(mock, att, op['result']['message_id'])
        if case == 'process_ended':
            mock.metas[att['session_id']]['runtimeStatus'] = 'exited'
            await w['d'].inventory.refresh_host('h1')
        rows = await producer.collect(w)
        rows = [r for r in rows if r['operation_id'] == op['operation_id']]
        assert len(rows) == 1
        assert rows[0]['document']['boundary'] == ('process_ended' if case == 'process_ended' else 'turn_done')
    value = read(ops, w['target'], STARTER_OWNER)
    if case in {'hidden', 'budget'}:
        assert value['used'] is None and value['coverage']['reasons'] == ['coverage_unavailable']
        assert 'known_used' not in value and 'breakdown' not in value
    else:
        assert value['used'] == (1 if case == 'process_ended' else 0)


async def test_direct_input_bound_is_advertised_and_unknown(start_ready, mock):
    w = start_ready
    ops = w['d'].ops
    att, _ = await closed_attempt(w, mock)
    for n in range(501):
        ops.create(STARTER_OWNER, action='session.send',
            target={'host': 'h1', 'session_id': att['session_id']},
            params={'text': 'Synthetic bounded accepted input'}, preconditions={}, idempotency_key=f'wip-bound-{n}')
        if n == 499:
            value = read(ops, w['target'], STARTER_OWNER)
            assert value['coverage']['status'] == 'complete' and value['used'] == 1
    value = read(ops, w['target'], STARTER_OWNER)
    assert value['used'] is None and value['coverage']['reasons'] == ['coverage_unavailable']
    assert pm_workspace_wip.capabilities(ops)['bounds']['per_execution_direct_inputs'] == 500
