"""Whole selected-source closure wins over derived refusals at production limits."""
import json

import pytest

from bat_agent_connector import pm_plan_impact as impact
from bat_agent_connector.operations import OperationError
from tests.test_pm_plan_impact import (
    attach_source_and_withdraw,
    read,
    selector,
)
from tests.test_pm_plan_impact import mapped as mapped
from tests.test_pm_plan_impact import pm as pm
from tests.test_pm_plan_impact import ready as ready
from tests.test_pm_plan_impact import selected as selected
from tests.test_pm_plan_impact import world as world
from tests.test_pm_plan_operations import OWNER


def clone_admissions(ops, original_id, count):
    original = dict(ops.db.execute('SELECT * FROM operations WHERE operation_id=?', (original_id,)).fetchone())
    body = json.loads(ops.db.execute("SELECT body FROM api_events WHERE kind='operation.accepted' AND resource_id=?", (original_id,)).fetchone()[0])
    keys, ids = list(original), []
    with ops.journal.tx():
        for i in range(count):
            oid = 'op_' + f'{i:032x}'
            value = original | {'operation_id': oid, 'idem_key': f'impact-limit-{i}', 'status': 'accepted',
                                'result': None, 'next_run_at': 9999999999}
            ops.db.execute(f"INSERT INTO operations({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", [value[k] for k in keys])
            ops.journal.api_event('operation', oid, 'operation.accepted', body, actor=OWNER.actor)
            ids.append(oid)
    return ids


async def overflow(w, limit, hidden):
    ops = w['d'].ops
    original = w['package']['operation_id']
    # The fixture has two subjects / four discovery events already.
    ids = clone_admissions(ops, original, 99 if limit == 'subjects' else 997)
    if hidden != 'none':
        await attach_source_and_withdraw(w, original if hidden == 'first' else ids[-1], w['impact_selector'])
    return await selector(w)


@pytest.mark.parametrize('limit', ['subjects', 'candidates'])
@pytest.mark.parametrize('hidden', ['first', 'last', 'none'])
async def test_impact_real_limits_authorize_before_derived_errors(selected, limit, hidden):
    params = await overflow(selected, limit, hidden)
    with pytest.raises(OperationError) as error:
        read(selected, params)
    unavailable = limit == 'candidates' or hidden != 'none'
    assert (error.value.code, error.value.status) == (
        ('PM_IMPACT_SOURCE_UNAVAILABLE', 404) if unavailable else ('PM_IMPACT_TOO_LARGE', 413))


async def conflicting_sources(w, monkeypatch, conflict):
    ops = w['d'].ops
    await attach_source_and_withdraw(w, w['publication']['operation_id'], w['impact_selector'])
    if conflict == 'comparison':
        monkeypatch.setattr(impact.diff, 'MAX_RESPONSE_BYTES', 1)
    else:
        row = ops.db.execute("SELECT * FROM api_events WHERE kind='pm.work_package.created'").fetchone()
        body = json.loads(row['body'])
        if conflict == 'duplicate':
            ops.journal.api_event(row['resource_type'], row['resource_id'], row['kind'], body, actor=OWNER.actor)
        else:
            body['document_digest'] = '0' * 64
            ops.db.execute('UPDATE api_events SET body=? WHERE seq=?', (json.dumps(body), row['seq']))
    return await selector(w)


@pytest.mark.parametrize('conflict', ['comparison', 'duplicate', 'contradiction'])
async def test_impact_unreadable_source_dominates_conflicting_evidence(selected, monkeypatch, conflict):
    w = selected
    params = await conflicting_sources(w, monkeypatch, conflict)
    with pytest.raises(OperationError) as error:
        read(w, params)
    assert (error.value.code, error.value.status) == ('PM_IMPACT_SOURCE_UNAVAILABLE', 404)


@pytest.mark.parametrize('hidden', [False, True])
async def test_impact_actual_comparison_bytes_follow_source_authorization(selected, hidden):
    from tests.test_pm_plan_operations import draft
    from tests.test_pm_snapshots import snapshot
    w, ops = selected, selected['d'].ops
    if hidden:
        await attach_source_and_withdraw(w, w['package']['operation_id'], w['impact_selector'])
    later = await draft(w, requirements=w['requirements'], markdown='範' * 100000)
    snap = await snapshot(ops, w['workspace_id'], who=OWNER)
    params = w['impact_selector'] | {'to_revision_id': later['revision_id'], 'snapshot_id': snap['snapshot_id']}
    with pytest.raises(OperationError) as error:
        read(w, params)
    assert (error.value.code, error.value.status) == (
        ('PM_IMPACT_SOURCE_UNAVAILABLE', 404) if hidden else ('PM_IMPACT_TOO_LARGE', 413))


@pytest.mark.parametrize('hidden', ['first', 'last', 'none'])
async def test_impact_readable_subject_prefix_cannot_mask_a_later_denial(selected, hidden):
    w, ops = selected, selected['d'].ops
    # 101 readable admissions can overflow construction before either original
    # subject is visited. The last case reproduced the old early-413 defect.
    ids = clone_admissions(ops, w['package']['operation_id'], 101)
    if hidden != 'none':
        oid = ids[0] if hidden == 'first' else max(w['package']['operation_id'], w['publication']['operation_id'])
        await attach_source_and_withdraw(w, oid, w['impact_selector'])
    params = await selector(w)
    with pytest.raises(OperationError) as error:
        read(w, params)
    assert (error.value.code, error.value.status) == (
        ('PM_IMPACT_TOO_LARGE', 413) if hidden == 'none' else ('PM_IMPACT_SOURCE_UNAVAILABLE', 404))
