"""New observations need current writer/scope; old receipts remain historical."""
import copy
import json
import math
import time
from types import SimpleNamespace

import pytest

from bat_agent_connector import inventory
from bat_agent_connector import pm_session_quiescence as manual
from bat_agent_connector import pm_workload_sources as sources
from bat_agent_connector.operations import OperationError
from tests import pm_joined_lifecycle_helpers as story
from tests.test_checkpoints import MANUAL
from tests.test_pm_joined_lifecycle import bat_world, daemon, joined, world  # noqa: F401
from tests.test_pm_session_quiescence import linked, request, run  # noqa: F401


def role(w, value):
    w['d'].ops.db.execute('UPDATE pm_workspace_members SET role=? WHERE workspace_id=? AND actor=?',
        (value, w['workspace_id'], story.OWNER.actor))


async def test_current_writer_required_but_original_completed_receipt_survives_downgrade(linked, mock):  # noqa: F811
    w, ops = linked, linked['d'].ops
    story.complete_native_turn(w, mock, MANUAL)
    operation = ops.create(story.OWNER, **request(w))[0]
    role(w, 'reader')
    await ops._execute(operation['operation_id'])
    assert ops.get(operation['operation_id'])['status'] == 'waiting_external'
    assert manual.proof(ops, story.OWNER, operation) is None
    assert manual.saved._saved(ops, operation['operation_id'], manual.ORIGIN) is None
    role(w, 'owner')
    await ops._execute(operation['operation_id'])
    operation = ops.get(operation['operation_id'])
    assert operation['status'] == 'succeeded', operation
    original = manual.proof(ops, story.OWNER, operation)
    role(w, 'reader')
    # Original result recovery reads the saved receipt before new read admission.
    recovered = await manual._observe(SimpleNamespace(service=ops, op=operation,
        operation_id=operation['operation_id']))
    assert recovered == original == manual.proof(ops, story.OWNER, operation)
    assert manual.operation_visible(ops.db, story.OWNER, operation)
    with pytest.raises(OperationError, match='membership'):
        ops.create(story.OWNER, **request(w))
    story.assert_manual_preserved(w, mock)


@pytest.mark.parametrize('at_final_frame', [False, True])
async def test_original_manual_workspace_is_rechecked_before_each_new_record(linked, mock, monkeypatch, at_final_frame):  # noqa: F811
    w, ops = linked, linked['d'].ops
    story.complete_native_turn(w, mock, MANUAL)
    operation = ops.create(story.OWNER, **request(w))[0]
    dispatch, reads = mock.dispatch, []
    def moved(channel, params):
        value = dispatch(channel, params)
        if channel == 'workspace:load':
            assert params == {'profileId': ops.context['fleet'].config.host('h1').profile_id}
            reads.append(channel)
            if not at_final_frame or len(reads) >= 2:
                document = json.loads(value) if isinstance(value, str) else copy.deepcopy(value)
                for tab in document['terminals']:
                    if tab['id'] == MANUAL:
                        tab['workspaceId'] = 'different-original-bat-workspace'
                value = json.dumps(document) if isinstance(value, str) else document
        return value
    monkeypatch.setattr(mock, 'dispatch', moved)
    await ops._execute(operation['operation_id'])
    result = ops.get(operation['operation_id'])
    assert result['status'] == 'waiting_external', result
    assert manual.proof(ops, story.OWNER, result) is None
    assert bool(manual.saved._saved(ops, result['operation_id'], manual.ORIGIN)) is at_final_frame
    assert len(reads) == (2 if at_final_frame else 1)
    story.assert_manual_preserved(w, mock)


async def test_changed_native_identity_cannot_reuse_original_unsent_selector(linked):  # noqa: F811
    w, ops = linked, linked['d'].ops
    envelope = request(w)
    row = ops.db.execute('SELECT body FROM sessions_observed WHERE host=? AND session_id=?', ('h1', MANUAL)).fetchone()
    data = json.loads(row[0])
    data['sdk_session_id'] = 'new-independently-observed-native-incarnation'
    ops.db.execute('UPDATE sessions_observed SET body=? WHERE host=? AND session_id=?', (json.dumps(data), 'h1', MANUAL))
    before = ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
    with pytest.raises(OperationError, match='identity changed'):
        ops.create(story.OWNER, **envelope)
    assert ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == before


async def test_same_second_complete_inventory_uses_exact_original_scan_identity(linked, mock, monkeypatch):  # noqa: F811
    w, ops = linked, linked['d'].ops
    story.complete_native_turn(w, mock, MANUAL)
    recorded = math.floor(time.time()) + 1.25
    monkeypatch.setattr(manual, 'time', SimpleNamespace(time=lambda: recorded))
    operation = await run(w, request(w))
    assert operation['status'] == 'succeeded', operation
    proof = manual.proof(ops, story.OWNER, operation)
    origin = manual.saved._saved(ops, operation['operation_id'], manual.ORIGIN)
    scan_at = recorded + .25
    monkeypatch.setattr(inventory, 'time', SimpleNamespace(time=lambda: scan_at,
        strftime=time.strftime, gmtime=time.gmtime))
    await w['d'].inventory.refresh_host('h1')
    row = dict(ops.db.execute('SELECT * FROM sessions_observed WHERE host=? AND session_id=?', ('h1', MANUAL)).fetchone())
    discovery = ops.db.execute('SELECT * FROM discovery_latest WHERE host=?', ('h1',)).fetchone()
    scan = json.loads(discovery['body'])
    assert sources.epoch(scan['finished_at']) < proof['recorded_at'] < row['last_seen_at']
    hc = ops.context['fleet'].config.host('h1')
    pair = {'host': 'h1', 'workspace_id': origin['bat_workspace_id'], 'profile_id': origin['profile_id'],
        'binding_digest': sources.digest([hc.url, hc.fingerprint, hc.profile_id])}
    policy = {'scope': {'bat_workspaces': [pair], 'project_binding': {'project_id': w['project_id']}},
        'observation': {'max_age_seconds': 300}}
    collector = sources.Collector(ops, story.OWNER, w['workspace_id'], policy, scan_at + .125)
    assert collector.current_repair_session(row, proof, origin, manual=True)
    assert not collector.current_repair_session({**row, 'last_seen_at': recorded - 1}, proof, origin, manual=True)
    # Equal displayed seconds do not authorize a different saved scan or a
    # moved session, even if the policy independently covers both workspaces.
    ops.db.execute('UPDATE discovery_latest SET body=? WHERE host=?',
        (json.dumps({**scan, 'scan_id': 'scan_unrelated'}), 'h1'))
    assert not collector.current_repair_session(row, proof, origin, manual=True)
    moved_workspace = 'other-explicitly-covered-workspace'
    collector.scope['bat_workspaces'].append({**pair, 'workspace_id': moved_workspace})
    scan['coverage']['workspace_ids'].append(moved_workspace)
    ops.db.execute('UPDATE discovery_latest SET body=? WHERE host=?', (json.dumps(scan), 'h1'))
    moved = {**json.loads(row['body']), 'workspace_id': moved_workspace}
    assert not collector.current_repair_session({**row, 'body': json.dumps(moved)}, proof, origin, manual=True)
    assert collector.current_repair_session(row, proof, origin, manual=True)
    assert manual.proof(ops, story.OWNER, operation) == proof
    story.assert_manual_preserved(w, mock)
