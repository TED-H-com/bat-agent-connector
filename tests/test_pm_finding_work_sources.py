"""Findings reuse exact Connector Issue work identity and all its original sources."""
import pytest

from bat_agent_connector import pm_maintenance_findings as findings
from bat_agent_connector.operations import OperationError
from tests import test_pm_issue_mappings as issues

world = issues.world
pm = issues.pm
ready = issues.ready
mapped = issues.mapped
OWNER = issues.OWNER


def source(issue):
    return {'issues': {'status': 'complete', 'reason': None, 'result': 'matched', 'findings': [
        {'native': {'kind': 'issues', 'id': issue['id'], 'number': issue['number'], 'repository_id': issue['repository_id']}}]}}


async def test_existing_work_is_exact_native_identity_and_original_package_history_remains_required(mapped):
    w, ops = mapped, mapped['d'].ops
    task, evidence = await issues.add_task_evidence(w)
    w['package'] = issues.read(w, await issues.create(w, previous=w['package']['package_id'], evidence_refs=[evidence]))
    published = await issues.publish(w)
    state = source(published['result']['issue'])
    refs = findings.attach_work(ops, OWNER, w['workspace_id'], state)
    assert len(refs) == 1 and refs[0]['work_item_id'] == w['package']['work_item_id']
    assert state['issues']['findings'][0]['existing_work'][0]['tracking_state'] == 'tracking'
    findings.require_sources(ops, OWNER, w['workspace_id'], refs)
    wrong = source({**published['result']['issue'], 'repository_id': 999})
    assert findings.attach_work(ops, OWNER, w['workspace_id'], wrong) == []
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (task['task_id'],))
    with pytest.raises(OperationError):
        findings.require_sources(ops, OWNER, w['workspace_id'], refs)
    with pytest.raises(OperationError):
        findings.attach_work(ops, OWNER, w['workspace_id'], state)


async def test_cancelled_mapping_is_existing_history_not_automatic_new_work(mapped):
    w, ops = mapped, mapped['d'].ops
    published = await issues.publish(w)
    cancelled = await issues.run(w, issues.cancellation(w, published))
    assert cancelled['status'] == 'succeeded'
    state = source(published['result']['issue'])
    refs = findings.attach_work(ops, OWNER, w['workspace_id'], state)
    assert refs[0]['revision_id'] == cancelled['result']['revision_id']
    assert state['issues']['findings'][0]['existing_work'][0]['tracking_state'] == 'cancelled'
