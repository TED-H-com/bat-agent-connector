"""Report quality is original; source withdrawal is not a filtered export."""
import pytest

from bat_agent_connector import pm_roadmap_reports as reports
from bat_agent_connector.operations import OperationError
from tests import test_pm_roadmap_quality as shared
from tests.test_pm_plan_operations import OWNER

world, pm, ready, fixed, reviewed = shared.world, shared.pm, shared.ready, shared.fixed, shared.reviewed


async def test_original_quality_keeps_actual_date_unknown_and_withdrawal_refuses_whole_report(reviewed, monkeypatch):
    w = reviewed
    reference, _, observed = await shared.assessed(w)
    args = w['d'].ops, OWNER, w['workspace_id'], observed['revision_id']
    before = reports.get_report(*args)
    assert 'qualified' in before['content']
    assert 'actual_finish_date": null' in before['content']
    await shared.shared.revoke(w, reference)
    assert reports.get_report(*args) == before
    shared.shared.withdraw_proof(w)
    monkeypatch.setattr(reports, 'MAX_CONTENT_BYTES', 1)
    with pytest.raises(OperationError) as error:
        reports.get_report(*args)
    assert (error.value.code, error.value.status) == ('PM_REPORT_SOURCE_UNAVAILABLE', 404)
