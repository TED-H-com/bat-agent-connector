"""Exact R2 product triggers, adapted only from characterization to required refusals."""
import copy

import pytest

from bat_agent_connector import pm_access, pm_plans
from bat_agent_connector import pm_roadmap_report_sources as s
from bat_agent_connector import pm_roadmap_reports as r
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import token
from tests.test_pm_plans import OWNER, planning
from tests.test_pm_roadmap import revision
from tests.test_pm_roadmap_report_api import daemon as daemon_fixture
from tests.test_pm_roadmap_report_api import served as served_fixture
from tests.test_pm_roadmap_report_review import document, fault, raw_http
from tests.test_pm_roadmap_report_sources import READER, selected
from tests.test_pm_roadmap_report_sources import ops as ops_fixture
from tests.test_pm_snapshots import draft, snapshot

ops = ops_fixture
daemon, served = daemon_fixture, served_fixture

def result(fn):
    try:
        return ('ok', fn())
    except OperationError as e:
        return ('domain', e.status, e.code)
    except Exception as e:
        return ('exception', type(e).__name__)

async def test_maintenance_reference_scalar_gap_http(served, monkeypatch):
    d, port = served
    target, *_ = await planning(d.ops)
    p = await draft(d.ops, target)
    wid = target['workspace_id']
    await revision(d.ops, wid, milestones=[])
    snap = await snapshot(d.ops, wid)
    obs = await revision(d.ops, wid, 'observation', snapshot_id=snap['snapshot_id'])
    path = f"/api/v1/pm/workspaces/{wid}/roadmap/revisions/{obs['revision_id']}/report"
    auth = token(d, OWNER.actor, 'observe')
    original = s.load_fixed(d.ops, OWNER, wid, obs['revision_id'])['snapshot']
    gate = pm_access.require_operation_sources
    later = pm_plans._revision(d.ops.db, wid, p['revision_id'])['operation_id']

    def hidden(db, principal, ids):
        if later in ids:
            raise OperationError('SECRET', 'hidden later', 404)
        return gate(db, principal, ids)
    with monkeypatch.context() as m:
        m.setattr(pm_access, 'require_operation_sources', hidden)
        generic = await raw_http(port, path, auth)
    assert generic[0] == 404
    for bad in ([], {}, 10 ** 100):
        for condition in ('ordinary', 'large', 'hidden_later', 'auth_budget'):
            with fault(d.ops), monkeypatch.context() as m:
                value = copy.deepcopy(original)
                value['maintenance'] = {'access_refs': [{'kind': 'snapshot_identity', 'id': bad, 'digest': 'x'}]}
                if condition == 'large':
                    value['padding'] = 'x' * 2000001
                if condition == 'auth_budget':
                    value['padding'] = 'x' * 16000000
                if condition == 'hidden_later':
                    m.setattr(pm_access, 'require_operation_sources', hidden)
                document(d.ops, 'pm_document_snapshots', 'snapshot_id', snap['snapshot_id'], value)
                out = await raw_http(port, path, auth)
                expected = generic
                assert out == expected
                print('maintenance id', type(bad).__name__, condition, out)

async def test_proof_shapes_and_legacy(ops):
    chosen = await selected(ops)
    base = s._Closure(ops, READER, chosen[0]['workspace_id']).collect(chosen[3]['revision_id'])
    for bad in (None, {}, [None], [{'milestone_id': []}], [{'milestone_id': 'release', 'evidence_ids': []}, {'milestone_id': 'release', 'evidence_ids': []}]):
        c = copy.deepcopy(base)
        c[0]['document']['milestones'] = bad
        c[0]['document_digest'] = s.digest(c[0]['document'])
        out = result(lambda: s._verify(c))
        print('observation milestone shape', repr(bad), out)
        assert out == ('domain', 409, 'PM_REPORT_SOURCE_CHANGED')
    c = copy.deepcopy(base)
    del c[0]['document']['milestones'][0]['quality_context']
    c[0]['document_digest'] = s.digest(c[0]['document'])
    s._verify(c)
    projected = s.project(c)
    assert 'quality' not in projected['observation']['document']['milestones'][0]['actual']
    assert projected['observation']['document']['milestones'][0]['actual']['actual_finish_date'] is None
    print('legacy missing quality context remains unassessed')

async def test_exact_accepted_adoption_in_empty_baseline(ops):
    chosen = await selected(ops)
    wid = chosen[0]['workspace_id']
    expected = chosen[2]['baseline']['adoption']['decision_id']
    initial = s.load_fixed(ops, READER, wid, chosen[3]['revision_id'])
    assert [a['id'] for a in initial['source_manifest'] if a['kind'] == 'adoption'] == [expected]
    await revision(ops, wid, milestones=[])
    observed = await revision(ops, wid, 'observation', snapshot_id=chosen[2]['snapshot_id'])
    closure = s._Closure(ops, READER, wid).collect(observed['revision_id'])
    after = s.load_fixed(ops, READER, wid, observed['revision_id'])
    assert [a['id'] for a in after['source_manifest'] if a['kind'] == 'adoption'] == [expected]
    # Characterize the old omission without depending on repository history in CI.
    omitted = s.project((*closure[:-1], {}))
    assert omitted['source_set_digest'] != after['source_set_digest']
    assert omitted['view_digest'] != after['view_digest']
    for locale in ('en', 'zh-TW'):
        for fmt in r.FORMATS:
            assert r.render_fixed(omitted, locale=locale, format=fmt)['output_digest'] != r.render_fixed(
                after, locale=locale, format=fmt)['output_digest']
    print('Empty milestone baseline retains its exact accepted adoption; omission changes all six output digests')

async def test_relationship_scalar_mismatches_and_malformed_http(ops, served):
    chosen = await selected(ops)
    original = s._Closure(ops, READER, chosen[0]['workspace_id']).collect(chosen[3]['revision_id'])
    for field in ('workspace_id', 'revision_id', 'previous_revision_id', 'scope_revision', 'decision_id', 'adopted_by', 'document_revision', 'document_digest', 'structured_scope_digest'):
        c = copy.deepcopy(original)
        next(iter(c[-1].values()))['result'][field] = 'mismatch'
        with pytest.raises(OperationError, match='PM_REPORT_SOURCE_CHANGED') as e:
            s._verify(c)
        assert e.value.status == 409
    for quality in (None, {}, [], False, {'scope_revision': 'wrong'}):
        c = copy.deepcopy(original)
        c[0]['document']['milestones'][0]['quality_context'] = quality
        c[0]['document_digest'] = s.digest(c[0]['document'])
        with pytest.raises(OperationError, match='PM_REPORT_SOURCE_CHANGED') as e:
            s._verify(c)
        assert e.value.status == 409
    d, port = served
    target, *_ = await planning(d.ops)
    wid = target['workspace_id']
    await revision(d.ops, wid, milestones=[])
    snap = await snapshot(d.ops, wid)
    obs = await revision(d.ops, wid, 'observation', snapshot_id=snap['snapshot_id'])
    fixed = s.load_fixed(d.ops, OWNER, wid, obs['revision_id'])
    path = f"/api/v1/pm/workspaces/{wid}/roadmap/revisions/{obs['revision_id']}/report"
    for bad in (None, [None], [{'milestone_id': []}]):
        with fault(d.ops):
            value = copy.deepcopy(fixed['observation']['document'])
            value['milestones'] = bad
            document(d.ops, 'pm_roadmap_revisions', 'revision_id', obs['revision_id'], value)
            d.ops.db.execute('UPDATE pm_roadmap_revisions SET document_digest=? WHERE revision_id=?', (s.digest(value), obs['revision_id']))
            status, body = await raw_http(port, path, token(d, OWNER.actor, 'observe'))
            print('malformed authorized milestones', repr(bad), status, body)
            assert status == 409

async def test_evidence_fact_reference_shape_gap(served):
    from bat_agent_connector import controller_authority
    from tests.test_pm_api import OWNER
    from tests.test_pm_evidence import task
    from tests.test_pm_evidence_api import capture, prepared
    from tests.test_pm_roadmap_report_api import anchor
    from tests.test_work_items import act
    d, port = served
    target, _, binding = await prepared(d)
    wid = target['workspace_id']
    with controller_authority.use(controller_authority.binding(d.ops.db, OWNER), d.journal):
        source = task(d.ops)
    await act(d, OWNER, 'work_item.link', {'work_item_id': target['work_item_id']}, {'kind': 'task', 'ref': source['task_id']})
    await capture(d, target, binding)
    fields, path = await anchor(d, wid)
    fixed = s.load_fixed(d.ops, OWNER, wid, fields['observation_revision_id'])
    evidence = next(e for e in fixed['snapshot']['evidence'] if e['document']['facts'].get('task_id'))
    for field in ('task_id', 'command_id'):
        for bad in ([1], {'wrong': 'type'}, 10 ** 100):
            with fault(d.ops):
                damaged = copy.deepcopy(evidence['document'])
                damaged['facts'][field] = bad
                document(d.ops, 'pm_evidence', 'evidence_id', evidence['evidence_id'], damaged)
                status, body = await raw_http(port, path, token(d, OWNER.actor, 'observe'))
                print('evidence authority scalar', field, type(bad).__name__, status, body)
                assert status == 404 and b'PM_REPORT_SOURCE_UNAVAILABLE' in body
