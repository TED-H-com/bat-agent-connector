"""Routed Result relations use the real admitted AHP child, without an editor."""
import json

from bat_agent_connector import pm_result_requirements, pm_work_packages, pm_work_start
from tests.test_ahp_work_start_route import (
    pm,  # noqa: F401
    ready,  # noqa: F401
    routed,  # noqa: F401
    start_ready,  # noqa: F401
    world,  # noqa: F401
)
from tests.test_pm_work_start import STARTER_OWNER


async def test_admitted_ahp_handoff_has_explicit_read_only_requirements(routed):  # noqa: F811
    w, _saved, parent_id, module = routed
    ops = w['d'].ops
    await ops._execute(parent_id)
    child_id = ops.get(parent_id)['result']['children']['handoff']
    assert ops.get(child_id)['action'] == module.ACTION
    assert pm_work_start._child(ops, ops.get(parent_id), 'handoff')['operation_id'] == child_id
    ops.context['work_start_routes'].clear()  # Historical reads do not need execution readiness.
    before = (ops.db.total_changes, ops.journal.api_head())
    section = pm_result_requirements.read(ops, STARTER_OWNER, w['workspace_id'], child_id, None, 'unavailable')
    assert section['relation'] == 'explicit'
    assert section['package_ref'] == ops.get(child_id)['params']['package_ref']
    package = pm_work_packages.get_package(ops, STARTER_OWNER, w['workspace_id'], section['package_ref']['package_id'])['package']
    assert child_id in {row['operation_id'] for row in pm_result_requirements._handoffs(ops, w['workspace_id'], package, 50)}
    assert section['items'] and section['items'][0]['state'] == 'no_conclusion'
    assert (ops.db.total_changes, ops.journal.api_head()) == before
    # Caller params alone are not a relation. The immutable recorded request must match.
    op = ops.get(child_id)
    params = {**op['params'], 'package_ref': {**op['params']['package_ref'], 'document_digest': '0' * 64}}
    ops.db.execute('UPDATE operations SET params=? WHERE operation_id=?', (json.dumps(params), child_id))
    assert pm_result_requirements.read(ops, STARTER_OWNER, w['workspace_id'], child_id, None, 'unavailable')['relation'] == 'none'
