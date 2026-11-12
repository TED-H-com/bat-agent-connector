"""Whole-closure authority precedes every report size/proof diagnostic."""
import copy

import pytest

from bat_agent_connector import pm_access, pm_plans, pm_roadmap, pm_snapshots
from bat_agent_connector import pm_roadmap_report_sources as reports
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.test_pm_roadmap import OWNER, READER, mapped, milestone, revision
from tests.test_pm_roadmap import ops as ops_fixture
from tests.test_pm_snapshots import adopt, adoption, draft, snapshot

ops = ops_fixture


async def selected(ops):
    target, plan, *_ = await mapped(ops)
    wid = target['workspace_id']
    frozen = await snapshot(ops, wid)
    observation = await revision(ops, wid, 'observation', snapshot_id=frozen['snapshot_id'])
    return target, plan, frozen, observation


def read(ops, chosen, who=READER):
    return reports.load_fixed(ops, who, chosen[0]['workspace_id'], chosen[3]['revision_id'])


async def test_fixed_closure_survives_adoption_rebaseline_forecast_without_live_reads(ops, monkeypatch):
    chosen = await selected(ops)
    before = read(ops, chosen)
    target, old, _, _ = chosen
    newer = await draft(ops, target, title='Another accepted scope')
    adopt(adoption(ops, target, newer['revision_id'], previous=old['revision_id']))
    await revision(ops, target['workspace_id'], milestones=[milestone(old)])
    await revision(ops, target['workspace_id'], 'forecast', updates=[{'milestone_id': 'release', 'forecast_date': '2027-01-01'}])
    def forbidden(*a, **kw):
        pytest.fail('A report must not read current state')
    for module, name in [(pm_plans, 'head'), (pm_plans, 'get_plan'), (pm_roadmap, 'head'),
                         (pm_roadmap, 'get_roadmap'), (pm_snapshots, 'latest_snapshot')]:
        monkeypatch.setattr(module, name, forbidden)
    changes = ops.db.total_changes
    assert read(ops, chosen) == before
    assert ops.db.total_changes == changes
    assert before['observation']['document']['milestones'][0]['actual']['actual_finish_date'] is None


@pytest.mark.parametrize('source', ['observation', 'baseline', 'snapshot', 'plan', 'adoption'])
async def test_hidden_original_is_generic_before_oversize_diagnostics(ops, monkeypatch, source):
    chosen = await selected(ops)
    fixed = read(ops, chosen)
    sources = {'observation': chosen[3]['operation_id'], 'baseline': fixed['baseline']['operation_id'],
        'snapshot': chosen[2]['operation_id'], 'plan': pm_plans._revision(ops.db, chosen[0]['workspace_id'], chosen[1]['revision_id'])['operation_id'],
        'adoption': 'op_' + chosen[2]['baseline']['adoption']['decision_id'][6:]}
    original = pm_access.require_operation_sources
    def denied(db, principal, ids):
        if sources[source] in ids:
            raise OperationError('SECRET_DIAGNOSTIC', 'Hidden source details', 413)
        return original(db, principal, ids)
    monkeypatch.setattr(pm_access, 'require_operation_sources', denied)
    with pytest.raises(OperationError) as first:
        read(ops, chosen)
    monkeypatch.setattr(reports, 'MAX_SOURCE_BYTES', 1)
    with pytest.raises(OperationError) as oversized:
        read(ops, chosen)
    assert first.value.code == oversized.value.code == 'PM_REPORT_SOURCE_UNAVAILABLE'
    assert first.value.status == oversized.value.status == 404
    assert str(first.value) == str(oversized.value)


async def test_authorization_budget_is_hidden_but_authorized_projection_size_is_413(ops, monkeypatch):
    chosen = await selected(ops)
    with monkeypatch.context() as limits:
        limits.setattr(reports, 'MAX_AUTH_RECORDS', 1)
        with pytest.raises(OperationError) as hidden:
            read(ops, chosen)
        assert (hidden.value.code, hidden.value.status) == ('PM_REPORT_SOURCE_UNAVAILABLE', 404)
    monkeypatch.setattr(reports, 'MAX_SOURCE_BYTES', 1)
    with pytest.raises(OperationError) as large:
        read(ops, chosen)
    assert (large.value.code, large.value.status) == ('PM_EXPORT_TOO_LARGE', 413)


async def test_read_membership_scope_and_exact_identity(ops):
    chosen = await selected(ops)
    for principal, code in [(Principal(OWNER.actor, frozenset({'manage'})), 'FORBIDDEN'),
                            (Principal('unrelated', frozenset({'observe'})), 'NOT_FOUND')]:
        with pytest.raises(OperationError, match=code):
            read(ops, chosen, principal)
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        reports.load_fixed(ops, READER, chosen[0]['workspace_id'], chosen[2]['snapshot_id'])
    with pytest.raises(OperationError, match='PM_ROADMAP_NOT_FOUND'):
        reports.load_fixed(ops, READER, chosen[0]['workspace_id'], 'pmro_' + 'f' * 32)


async def test_proof_checks_are_after_the_whole_closure(ops, monkeypatch):
    chosen = await selected(ops)
    original = reports._Closure.collect
    def corrupt(self, identity):
        value = copy.deepcopy(original(self, identity))
        value[1]['document_digest'] = '0' * 64
        return value
    monkeypatch.setattr(reports._Closure, 'collect', corrupt)
    with pytest.raises(OperationError) as error:
        read(ops, chosen)
    assert (error.value.code, error.value.status) == ('PM_REPORT_SOURCE_CHANGED', 409)


async def test_unexpanded_snapshot_evidence_is_still_required(ops, monkeypatch):
    from tests.test_pm_evidence import capture, link, task
    target, _, work, *_ = await mapped(ops)
    source = task(ops)
    await link(ops, work, 'task', source['task_id'])
    await capture(ops, work)
    frozen = await snapshot(ops, target['workspace_id'])
    # No milestone expands this evidence, but it remains in the original snapshot.
    await revision(ops, target['workspace_id'], milestones=[])
    observed = await revision(ops, target['workspace_id'], 'observation', snapshot_id=frozen['snapshot_id'])
    reports.load_fixed(ops, READER, target['workspace_id'], observed['revision_id'])
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))
    monkeypatch.setattr(reports, 'MAX_SOURCE_BYTES', 1)
    with pytest.raises(OperationError) as error:
        reports.load_fixed(ops, READER, target['workspace_id'], observed['revision_id'])
    assert (error.value.code, error.value.status) == ('PM_REPORT_SOURCE_UNAVAILABLE', 404)


async def test_historical_plan_and_original_forecast_are_fixed(ops):
    chosen = await selected(ops)
    target, old, *_ = chosen
    newer = await draft(ops, target, title='Later scope')
    adopt(adoption(ops, target, newer['revision_id'], previous=old['revision_id']))
    forecast = await revision(ops, target['workspace_id'], 'forecast', updates=[{'milestone_id': 'release', 'forecast_date': None}])
    frozen = await snapshot(ops, target['workspace_id'])
    observed = await revision(ops, target['workspace_id'], 'observation', snapshot_id=frozen['snapshot_id'])
    result = reports.load_fixed(ops, READER, target['workspace_id'], observed['revision_id'])
    assert result['snapshot']['baseline']['document_revision'] == newer['revision_id']
    assert result['historical_plans'][0]['revision_id'] == old['revision_id']
    assert result['forecast']['revision_id'] == forecast['revision_id']


async def test_authorization_byte_budget_and_tainted_traversal_are_concealed(ops, monkeypatch):
    from bat_agent_connector import pm_read_cache
    chosen = await selected(ops)
    with monkeypatch.context() as limits:
        limits.setattr(reports, 'MAX_AUTH_BYTES', 1)
        with pytest.raises(OperationError, match='PM_REPORT_SOURCE_UNAVAILABLE') as error:
            read(ops, chosen)
        assert error.value.status == 404
    original = pm_access.require_operation_sources
    def refused(*args):
        original(*args)
        pm_read_cache.refuse()
    monkeypatch.setattr(pm_access, 'require_operation_sources', refused)
    with pytest.raises(OperationError, match='PM_REPORT_SOURCE_UNAVAILABLE'):
        read(ops, chosen)
