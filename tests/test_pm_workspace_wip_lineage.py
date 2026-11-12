"""Original managed-session descendants retain capacity without new admission rules."""
import pytest

from bat_agent_connector import pm_attempt_results, pm_wip_count, work_items
from bat_agent_connector import pm_workspace_wip as policy
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests import test_pm_attempt_results as results
from tests import test_pm_work_start as starting
from tests.operation_helpers import settle_operations
from tests.test_pm_work_packages import action
from tests.test_pm_workspace_wip import install, read

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


async def closed_attempt(w, mock):
    ops = w['d'].ops
    install(ops)
    attempt = await results._start_managed_attempt(w, mock, key='wip-lineage')
    results._set_turn_messages(mock, attempt['session_id'], attempt['turn_marker'], streaming=False, pending_ask=None)
    receipts = await w['d'].result_collector.collect_host('h1')
    assert len(receipts) == 1
    wi = work_items._get_item(ops.db, w['target']['work_item_id'])
    await action(w, 'work_item.update', {'work_item_id': wi['work_item_id']}, {'state': 'done'},
                 {'expected_version': wi['version']})
    wi = work_items._get_item(ops.db, wi['work_item_id'])
    await action(w, 'work_item.approve', {'work_item_id': wi['work_item_id']}, {'note': 'Reviewed fixture output'},
                 {'expected_fingerprint': work_items.fingerprint(wi)},
                 who=Principal(starting.STARTER_OWNER.actor, starting.STARTER_OWNER.scopes | {'approve'}))
    await w['d'].inventory.refresh_host('h1')
    assert read(ops, w['target'], starting.STARTER_OWNER)['used'] == 0
    return attempt, receipts[0]


@pytest.mark.parametrize('outcome', ['cancelled', 'sent', 'hidden', 'hidden_open', 'overflow'])
async def test_direct_input_requires_original_release_evidence(start_ready, mock, monkeypatch, outcome):
    w = start_ready
    ops = w['d'].ops
    attempt, receipt = await closed_attempt(w, mock)
    collector = pm_wip_count._Collector(ops, starting.STARTER_OWNER, w['workspace_id'], policy.time.time())
    collector.scan()
    baseline = collector.used_records
    later, _ = ops.create(starting.STARTER_OWNER, action='session.send',
                         target={'host': 'h1', 'session_id': attempt['session_id']},
                         params={'text': 'Synthetic later instruction'}, preconditions={},
                         idempotency_key='wip-direct-input')
    assert not ops.db.execute('SELECT 1 FROM work_inputs WHERE operation_id=?', (later['operation_id'],)).fetchone()
    if outcome == 'hidden_open':
        wi = work_items._get_item(ops.db, w['target']['work_item_id'])
        await action(w, 'work_item.continue', {'work_item_id': wi['work_item_id']}, {'note': 'Reopen fixture'},
                     {'expected_fingerprint': work_items.fingerprint(wi)},
                     who=Principal(starting.STARTER_OWNER.actor, starting.STARTER_OWNER.scopes | {'approve'}))
    if outcome in {'hidden', 'hidden_open'}:
        original = ops.get
        def get(oid, **kwargs):
            if oid == later['operation_id'] and kwargs.get('principal'):
                raise OperationError('NOT_FOUND', 'Hidden fixture source', 404)
            return original(oid, **kwargs)
        monkeypatch.setattr(ops, 'get', get)
    elif outcome == 'overflow':
        for n in range(40):
            ops.create(starting.STARTER_OWNER, action='session.send',
                target={'host': 'h1', 'session_id': attempt['session_id']},
                params={'text': 'Bounded synthetic instruction'}, preconditions={},
                idempotency_key=f'wip-lineage-overflow-{n}')
        monkeypatch.setattr(policy, 'MAX_RECORDS', baseline + 1)
    before = len(mock.invokes)
    ticks = 0
    def progress():
        nonlocal ticks
        ticks += 1
    ops.db.set_progress_handler(progress, 1)
    try:
        value = read(ops, w['target'], starting.STARTER_OWNER)
    finally:
        ops.db.set_progress_handler(None, 0)
    print('ASSOCIATED INPUT READ', outcome, ticks, value['used'])
    assert len(mock.invokes) == before
    if outcome in {'hidden', 'hidden_open', 'overflow'}:
        assert value['coverage']['reasons'] == ['coverage_unavailable']
        assert value['used'] is None and 'breakdown' not in value and 'known_used' not in value
        return
    assert value['used'] == value['breakdown']['unresolved_execution'] == 1
    plan = [row[3] for row in ops.db.execute('EXPLAIN QUERY PLAN ' + pm_wip_count.LINEAGE_SQL,
        (attempt['handoff_operation_id'], 50001))]
    print('LINEAGE PLAN', plan)
    assert any('SEARCH' in row and 'pm_source_original_lookup' in row for row in plan)
    assert all('TEMP B-TREE' not in row for row in plan)
    if outcome == 'cancelled':
        ops.cancel(starting.STARTER_OWNER, later['operation_id'])
    else:
        await settle_operations(ops)
        sent = ops.get(later['operation_id'])
        assert sent['status'] == 'succeeded'
        assert read(ops, w['target'], starting.STARTER_OWNER)['used'] == 1  # ACK is not completion.
        marker = sent['result']['message_id']
        pm_attempt_results.record_receipt(ops.journal, workspace_id=w['workspace_id'],
            attempt_id=attempt['attempt_id'], source_key=receipt['source_key'],
            source_generation=receipt['source_generation'], turn_marker=marker, last_message_id='later-final',
            digest='sha256:' + 'b' * 64, document={'turn_marker': marker, 'boundary': 'turn_done',
                'turn_attribution': 'correlated'}, observed_at=policy.time.time(), operation_id=sent['operation_id'])
    assert read(ops, w['target'], starting.STARTER_OWNER)['used'] == 0
