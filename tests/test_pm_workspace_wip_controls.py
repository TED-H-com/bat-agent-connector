"""Control lineage and proven-unsent originals are not new Work inputs."""
import pytest

from bat_agent_connector import checkpoints, pm_work_start, work_items
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests import test_pm_attempt_results as results
from tests import test_pm_work_start as starting
from tests.operation_helpers import settle_operations
from tests.test_pm_work_packages import action
from tests.test_pm_workspace_wip import install, read
from tests.test_pm_workspace_wip_classification import build_action
from tests.test_pm_workspace_wip_lineage import closed_attempt

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


async def close_work(w):
    ops = w['d'].ops
    wi = work_items._get_item(ops.db, w['target']['work_item_id'])
    await action(w, 'work_item.update', {'work_item_id': wi['work_item_id']}, {'state': 'done'},
                 {'expected_version': wi['version']})
    wi = work_items._get_item(ops.db, wi['work_item_id'])
    await action(w, 'work_item.approve', {'work_item_id': wi['work_item_id']},
                 {'note': 'Reviewed synthetic output'}, {'expected_fingerprint': work_items.fingerprint(wi)},
                 who=Principal(starting.STARTER_OWNER.actor, starting.STARTER_OWNER.scopes | {'approve'}))


@pytest.mark.parametrize('kind', ['session.interrupt.idle', 'session.interrupt.active',
                                 'session.answer.idle', 'session.answer.pending'])
async def test_controls_never_extend_completed_turn(start_ready, mock, kind):
    w = start_ready
    att, _ = await closed_attempt(w, mock)
    principal, op = await build_action(w, mock, att, kind)
    before = read(w['d'].ops, w['target'], principal)
    await settle_operations(w['d'].ops)
    done = w['d'].ops.get(op['operation_id'])
    assert done['status'] == ('failed' if kind == 'session.answer.idle' else 'succeeded')
    if kind == 'session.answer.idle':
        assert done['error_code'] == 'REFUSED'
    assert before['used'] == read(w['d'].ops, w['target'], principal)['used'] == 0


async def test_pending_question_waits_for_original_terminal_receipt(start_ready, mock):
    w = start_ready
    ops = w['d'].ops
    install(ops)
    att = await results._start_managed_attempt(w, mock)
    await close_work(w)
    pending = {'toolUseId': 'review-question', 'questions': [{'question': 'Choose a fixture option',
        'header': 'Option', 'options': [{'label': 'One', 'description': 'One'}], 'multiSelect': False}]}
    results._set_turn_messages(mock, att['session_id'], att['turn_marker'], pending_ask=pending)
    assert not await w['d'].result_collector.collect_host('h1')
    assert read(ops, w['target'], starting.STARTER_OWNER)['used'] == 1
    _, op = await build_action(w, mock, att, 'session.answer.pending')
    await settle_operations(ops)
    assert ops.get(op['operation_id'])['status'] == 'succeeded'
    assert read(ops, w['target'], starting.STARTER_OWNER)['used'] == 1
    results._set_turn_messages(mock, att['session_id'], att['turn_marker'], pending_ask=None)
    assert await w['d'].result_collector.collect_host('h1')
    await w['d'].inventory.refresh_host('h1')
    assert read(ops, w['target'], starting.STARTER_OWNER)['used'] == 0


async def test_unsent_original_does_not_recreate_commitment(start_ready, mock, monkeypatch):
    w = start_ready
    ops = w['d'].ops
    install(ops)
    original = checkpoints.start_in_worktree

    async def stop_before_send(ctx, **kwargs):
        previous = kwargs.get('before_send')
        async def refuse():
            if previous:
                await previous()
            raise OperationError('FIXTURE_UNSENT', 'Synthetic refusal before send step', 409)
        return await original(ctx, **{**kwargs, 'before_send': refuse})

    monkeypatch.setattr(checkpoints, 'start_in_worktree', stop_before_send)
    parent, _ = ops.create(starting.STARTER_OWNER, **(await starting._start_envelope(w)))
    done = await results._advance_start_public(ops, parent['operation_id'])
    assert done['status'] == 'failed'
    handoff = pm_work_start._children(ops, done)['handoff']
    assert handoff['status'] == 'failed' and handoff['error_code'] == 'FIXTURE_UNSENT'
    assert not ops.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? AND name='send'",
                              (handoff['operation_id'],)).fetchone()
    session = pm_work_start._child_session(handoff)
    assert session
    await close_work(w)
    later, _ = ops.create(starting.STARTER_OWNER, action='session.send', target=session,
        params={'text': 'Synthetic direct input after unsent original'}, preconditions={},
        idempotency_key='unsent-original-later')
    await settle_operations(ops)
    assert ops.get(later['operation_id'])['status'] == 'succeeded'
    assert not w['d'].result_collector._eligible_candidates('h1')
    value = read(ops, w['target'], starting.STARTER_OWNER)
    assert value['coverage']['status'] == 'complete' and value['used'] == 0
