"""A report cannot bypass original maintenance snapshot ancestors."""
import pytest

from bat_agent_connector import pm_roadmap
from bat_agent_connector import pm_roadmap_reports as reports
from bat_agent_connector.operations import OperationError
from tests import test_pm_maintenance_review as shared
from tests.test_pm_roadmap import revision

base_ops, release_ops, workload_ops = shared.base_ops, shared.release_ops, shared.workload_ops
github_ops, ops = shared.github_ops, shared.ops


async def test_maintenance_source_withdrawal_refuses_report(ops, monkeypatch):
    data = await shared.gh.prepared(ops, monkeypatch)
    _, view = await shared.prepare(ops, data)
    pm_roadmap.install(ops)
    wid = data['workspace_id']
    await revision(ops, wid, milestones=[])
    observed = await revision(ops, wid, 'observation', snapshot_id=view['snapshot']['snapshot_id'])
    assert reports.get_report(ops, shared.OWNER, wid, observed['revision_id'])
    await shared.remove_repo(ops, data['project_id'])
    with pytest.raises(OperationError) as error:
        reports.get_report(ops, shared.OWNER, wid, observed['revision_id'])
    assert (error.value.code, error.value.status) == ('PM_REPORT_SOURCE_UNAVAILABLE', 404)
