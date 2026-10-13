"""Live status is visible, bounded central evidence; no snapshots or writes."""
from __future__ import annotations

import uuid

import pytest

from bat_agent_connector import mcp_server, pm_overview, pm_reads, pm_source_lineage
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests import test_pm_governance as governance
from tests import test_pm_quality_reviews as quality
from tests import test_pm_roadmap as roadmap
from tests.test_api_v1 import daemon, http, served, token  # noqa: F401
from tests.test_pm_api import seed
from tests.test_pm_discussions import OUTSIDER, OWNER, workspace
from tests.test_pm_run_api import unpack
from tests.test_pm_thread_contexts import base_ops, ops, origins  # noqa: F401

world, pm, ready = quality.world, quality.pm, quality.ready
fixed, reviewed = quality.fixed, quality.reviewed


async def test_empty_workspace_and_missing_sources_are_unknown_not_zero(ops):  # noqa: F811
    ws = await workspace(ops)
    before = ops.db.total_changes
    result = pm_overview.read(ops, OWNER, ws['workspace_id'])
    assert result['plan'] is result['progress'] is result['next_milestone'] is None
    assert result['attention'] == result['recent_deliveries'] == result['milestones'] == []
    assert {'field': 'progress', 'reason': 'no_adopted_plan'} in result['unknown']
    assert ops.db.total_changes == before
    for who in (OUTSIDER, Principal(OWNER.actor, frozenset({'manage'}))):
        with pytest.raises(OperationError) as exc:
            pm_overview.read(ops, who, ws['workspace_id'])
        assert exc.value.status in (403, 404)


async def test_live_adopted_plan_blocker_decision_and_forecast_deviation(ops):  # noqa: F811
    target, refs = await origins(ops)
    wid = target['workspace_id']
    record = governance.read(ops, {'workspace_id': wid, 'record_id': refs[5]['id']})
    data = {'wid': wid, 'snapshot': {'snapshot_id': refs[0]['id']},
            'assumption': record['revision']['document']['sources'][0]['id']}
    await governance.record(ops, data, kind='dependency', root_cause_key='external-contract', title='Recovery contract is blocked')
    await governance.record(ops, data, kind='change', root_cause_key='recovery-budget', title='Decide recovery support budget')
    await roadmap.revision(ops, wid, 'forecast', updates=[{'milestone_id': 'release', 'forecast_date': '2026-11-04'}])
    before = ops.db.total_changes
    result = pm_overview.read(ops, OWNER, wid)
    assert result['plan']['revision_id'] == refs[1]['id']
    assert result['progress']['requirements'] == 1
    assert result['progress']['blocked'] == 1 and result['progress']['accepted'] == 0
    assert result['progress']['complete']
    assert result['next_milestone']['delta_days'] == 3
    assert result['next_milestone']['status'] == 'blocked'
    assert {'blocker', 'decision', 'deviation', 'risk'} <= {a['kind'] for a in result['attention']}
    assert result['attention'][0]['kind'] == 'blocker'
    assert ops.db.total_changes == before


async def test_partial_work_page_never_becomes_total_progress(ops, monkeypatch):  # noqa: F811
    target, _ = await origins(ops)
    # Exercise the real existing page boundary with a small projection scan budget.
    from tests.test_pm_follow_up import work_for
    await work_for(ops, target)
    monkeypatch.setattr(pm_overview, 'SOURCE_LIMIT', 1)
    result = pm_overview.read(ops, OWNER, target['workspace_id'])
    assert not result['progress']['complete']
    assert result['progress']['requirements'] == 1  # complete adopted document, not the work page
    assert result['progress']['accepted'] is result['progress']['not_started'] is None
    assert result['coverage']['sources']['work']['next_cursor'] is not None
    assert result['attention_truncated']


async def test_missing_roadmap_source_cannot_report_zero_deviations(ops, monkeypatch):  # noqa: F811
    target, _ = await origins(ops)
    actual = pm_source_lineage._table
    monkeypatch.setattr(pm_source_lineage, '_table', lambda db, name: name != 'pm_roadmap_heads' and actual(db, name))
    result = pm_overview.read(ops, OWNER, target['workspace_id'])
    assert result['attention_counts']['deviation'] is None
    assert not result['coverage']['sources']['roadmap']['complete']
    assert {'field': 'milestones', 'reason': 'source_unavailable'} in result['unknown']


async def test_withdrawn_plan_and_work_cannot_influence_labels_counts_or_order(ops, monkeypatch):  # noqa: F811
    target, refs = await origins(ops)
    wid = target['workspace_id']
    before = pm_overview.read(ops, OWNER, wid)
    assert before['progress']['requirements'] == 1
    from bat_agent_connector import pm_access
    actual = pm_access.operation_source_visible
    plan_op = ops.db.execute('SELECT operation_id FROM pm_plan_revisions WHERE revision_id=?', (refs[1]['id'],)).fetchone()[0]
    monkeypatch.setattr(pm_access, 'operation_source_visible', lambda db, p, oid: oid != plan_op and actual(db, p, oid))
    result = pm_overview.read(ops, OWNER, wid)
    assert result['plan'] is result['progress'] is None
    assert result['milestones'] == []
    assert result['attention'] == []
    assert result['recent_deliveries'] == []
    assert refs[1]['id'] not in str(result)
    assert pm_source_lineage.read_response(ops.db, OWNER, result) == result


async def test_quality_acceptance_is_explicit_and_revocation_is_a_deviation(reviewed):
    w, service = reviewed, reviewed['d'].ops
    review = await quality.new_review(w)
    await quality.execute(w, quality.assessment(w, review), actor=quality.REVIEWER)
    result = pm_overview.read(service, quality.OWNER, w['workspace_id'])
    assert result['progress']['complete'] and result['progress']['accepted'] == 1
    assert result['recent_deliveries'][0]['state'] == 'accepted'
    await quality.execute(w, dict(action=quality.quality.REVOKE,
        target={'workspace_id': w['workspace_id'], 'review_id': review['review_id']},
        params={'reason': 'The accepted quality evidence was explicitly revoked'},
        preconditions={'expected_version': 2}, idempotency_key=uuid.uuid4().hex), actor=quality.REVIEWER)
    result = pm_overview.read(service, quality.OWNER, w['workspace_id'])
    assert result['progress']['accepted'] == 0
    assert any(a.get('reason') == 'quality_revoked' for a in result['attention'])
    assert not any(d['state'] == 'accepted' for d in result['recent_deliveries'])


async def test_http_rpc_mcp_parity_capability_and_observe_only(served, monkeypatch):  # noqa: F811
    d, port = served
    ws, _ = await seed(d)
    wid = ws['workspace_id']
    from tests.test_pm_api import OWNER as API_OWNER
    auth = token(d, API_OWNER.actor, *API_OWNER.scopes)
    path = f'/api/v1/pm/workspaces/{wid}/overview'
    before = d.journal.db.total_changes
    status, result = await http(port, 'GET', path, tok=auth)
    assert status == 200, result
    rpc = await d.call_api('pm_overview_get', {'workspace_id': wid}, API_OWNER)
    assert {k: v for k, v in result.items() if k != 'as_of'} == {k: v for k, v in rpc.items() if k != 'as_of'}
    assert d.journal.db.total_changes == before
    assert (await http(port, 'GET', '/api/v1/capabilities', tok=auth))[1]['features']['pm_overview'] == {'version': 1, 'read_only': True}
    assert (await http(port, 'GET', path + '?refresh=1', tok=auth))[0] == 422
    assert (await http(port, 'GET', path, tok=token(d, 'outsider', 'observe')))[0] == 404
    assert (await http(port, 'GET', path, tok=token(d, API_OWNER.actor, 'manage')))[0] == 403
    assert (await http(port, 'POST', path, tok=auth, body={}))[0] in (404, 405)
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        pm_reads.read(d.ops, API_OWNER, 'pm_overview_get', {'workspace_id': wid, 'snapshot_id': 'latest'})
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        assert 'pm_overview_get' in {t.name for t in await server.list_tools()}
        payload = unpack(await server.call_tool('pm_overview_get', {'workspace_id': wid}))
        assert payload['progress'] is None and payload['workspace_id'] == wid
    finally:
        await fleet.close()


async def test_lifecycle_pending_decision_and_context_are_source_bound(ops):  # noqa: F811
    from bat_agent_connector import pm_discussions, pm_lifecycle
    from tests import test_pm_lifecycle as life
    from tests.test_pm_thread_contexts import create
    pm_lifecycle.install(ops)
    ws = await workspace(ops)
    wid = ws['workspace_id']
    async def effect(ctx):
        return {}
    life.register(ops, effect)
    pending = life.submit(ops, wid)
    paused = await life.decide(ops, wid)
    out = pm_overview.read(ops, OWNER, wid)
    assert out['lifecycle']['stage'] == 'paused'
    item = next(a for a in out['attention'] if a.get('reason') == 'lifecycle_inflight')
    assert item['pending_operations'][0]['operation_id'] == pending['operation_id']
    assert item['ref']['id'] == paused['result']['transition_id']
    # Creation remains an investment; reopen to bind the historical transition.
    await life.decide(ops, wid, 'reopen', params={'stage': 'planning'})
    done, _ = await create(ops, wid, [item['ref']])
    tid = done['result']['thread_id']
    ctx = pm_discussions.context(ops, OWNER, wid, tid)
    assert ctx['bound_context'][0]['summary']['to_stage'] == 'paused'
    await life.decide(ops, wid)
    rename = ops.create(OWNER, action='pm.thread.rename', target={'workspace_id': wid, 'thread_id': tid},
        params={'title': 'Discuss the paused project'}, preconditions={'expected_version': 1}, idempotency_key=uuid.uuid4().hex)[0]
    from tests.test_pm_discussions import settle
    assert (await settle(ops, rename))['status'] == 'succeeded'


from tests import test_pm_delivery_lineage as delivery_tests  # noqa: E402

delivery_ready = delivery_tests.delivery_ready


async def test_merge_deploy_and_requirement_acceptance_stay_distinct(delivery_ready):
    w, service = delivery_ready, delivery_ready['d'].ops
    delivered = await delivery_tests.settle(w, delivery_tests.apply(w, await delivery_tests.preview(w)))
    assert delivered['status'] == 'succeeded'
    preview = (await delivery_tests.delivery.pr_preview(service, 'o/r', 1))['merge_preview']
    env = {**delivery_tests.pr_delivery.merge_envelope(preview), 'idempotency_key': uuid.uuid4().hex}
    merged = await delivery_tests.settle(w, service.create(delivery_tests.DELIVERER, **env)[0])
    assert merged['status'] == 'succeeded'
    before = len(w['gh'].requests), len(w['runner'].scripts), service.db.total_changes
    out = pm_overview.read(service, delivery_tests.DELIVERER, w['workspace_id'])
    assert out['recent_deliveries'][0]['state'] == 'merged'
    assert out['recent_deliveries'][0]['pr'] == {'repository': 'o/r', 'number': 1}
    assert out['progress']['accepted'] == 0
    assert before == (len(w['gh'].requests), len(w['runner'].scripts), service.db.total_changes)
    preview = await delivery_tests.deployment.preview(service, 'prod')
    op = delivery_tests.submit(w, 'deployment.start', {'recipe': 'prod'}, {'source_sha': merged['result']['merged_sha']}, preview['preconditions'])
    pending = await delivery_tests.settle(w, op, rounds=2)
    run = pending['external_refs']['deploy_run_id']
    w['gh'].runs[run].update(status='completed', conclusion='success')
    w['gh'].jobs[run][1]['conclusion'] = 'success'
    deployed = await delivery_tests.settle(w, op)
    assert deployed['status'] == 'succeeded'
    out = pm_overview.read(service, delivery_tests.DELIVERER, w['workspace_id'])
    assert len(out['recent_deliveries']) == 1 and out['recent_deliveries'][0]['state'] == 'deployed'
    assert out['progress']['accepted'] == 0
    from tests.test_deployment_rollback import allow
    allow(w['d'])
    preview = await delivery_tests.deployment.preview(service, 'prod')
    rollback = delivery_tests.submit(w, 'deployment.rollback', {'recipe': 'prod'},
        {'deployment_id': deployed['result']['deployment_id']}, preview['preconditions'])
    pending = await delivery_tests.settle(w, rollback, rounds=2)
    run = pending['external_refs']['deploy_run_id']
    w['gh'].runs[run].update(status='completed', conclusion='success')
    w['gh'].jobs[run][1]['conclusion'] = 'success'
    rolled_back = await delivery_tests.settle(w, rollback)
    assert rolled_back['status'] == 'succeeded', rolled_back
    out = pm_overview.read(service, delivery_tests.DELIVERER, w['workspace_id'])
    assert len(out['recent_deliveries']) == 1 and out['recent_deliveries'][0]['state'] == 'rolled_back'
    assert pm_source_lineage.read_response(service.db, delivery_tests.DELIVERER, out) == out
    delivery_tests.revoke(w)
    with pytest.raises(OperationError) as exc:
        pm_overview.read(service, delivery_tests.DELIVERER, w['workspace_id'])
    assert exc.value.status == 404


async def _revoked(w):
    review = await quality.new_review(w)
    await quality.execute(w, quality.assessment(w, review), actor=quality.REVIEWER)
    await quality.execute(w, dict(action=quality.quality.REVOKE,
        target={'workspace_id': w['workspace_id'], 'review_id': review['review_id']},
        params={'reason': 'Explicitly revoke the original quality evidence'},
        preconditions={'expected_version': 2}, idempotency_key=uuid.uuid4().hex), actor=quality.REVIEWER)
    return review


async def test_deviation_count_requires_complete_work_page(reviewed, monkeypatch):
    from bat_agent_connector import pm_evidence
    w, service = reviewed, reviewed['d'].ops
    await _revoked(w)
    assert pm_overview.read(service, quality.OWNER, w['workspace_id'])['attention_counts']['deviation'] == 1
    original = pm_evidence.list_work
    def truncated(*args, **kwargs):
        return {**original(*args, **kwargs), 'bindings': [], 'next_cursor': 10**6}
    monkeypatch.setattr(pm_evidence, 'list_work', truncated)
    result = pm_overview.read(service, quality.OWNER, w['workspace_id'])
    assert result['attention_counts']['deviation'] is None and result['attention_truncated']
    assert not any(a.get('reason') == 'quality_revoked' for a in result['attention'])


async def test_reacceptance_clears_superseded_revocation(reviewed):
    w, service = reviewed, reviewed['d'].ops
    old = await _revoked(w)
    newer = await quality.new_review(w, previous=old['review_id'])
    await quality.execute(w, quality.assessment(w, newer), actor=quality.REVIEWER)
    result = pm_overview.read(service, quality.OWNER, w['workspace_id'])
    assert result['progress']['accepted'] == 1
    assert result['recent_deliveries'][0]['state'] == 'accepted'
    assert not any(a.get('reason') == 'quality_revoked' for a in result['attention'])


async def test_revocation_survives_open_review_until_reaccepted(reviewed):
    w, service = reviewed, reviewed['d'].ops
    old = await _revoked(w)
    before = pm_overview.read(service, quality.OWNER, w['workspace_id'])
    deviation = next(a for a in before['attention'] if a.get('reason') == 'quality_revoked')
    newer = await quality.new_review(w, previous=old['review_id'])
    opened = pm_overview.read(service, quality.OWNER, w['workspace_id'])
    assert opened['attention_counts']['deviation'] == 1
    assert opened['progress']['accepted'] == 0 and opened['progress']['in_review'] == 1
    assert next(a for a in opened['attention'] if a.get('reason') == 'quality_revoked') == deviation
    await quality.execute(w, quality.assessment(w, newer), actor=quality.REVIEWER)
    accepted = pm_overview.read(service, quality.OWNER, w['workspace_id'])
    assert accepted['progress']['accepted'] == 1
    assert accepted['attention_counts']['deviation'] == 0
    assert not any(a.get('reason') == 'quality_revoked' for a in accepted['attention'])


async def test_quality_history_does_not_consume_current_review_budget(reviewed):
    w, service = reviewed, reviewed['d'].ops
    for dod in (False, True):
        previous = None
        for _ in range(26):
            row = await quality.new_review(w, previous=previous, dod=dod)
            previous = row['review_id']
    await quality.execute(w, quality.assessment(w, row), actor=quality.REVIEWER)
    result = pm_overview.read(service, quality.OWNER, w['workspace_id'])
    assert result['coverage']['sources']['quality']['complete']
    assert result['progress']['accepted'] == 1
    assert result['attention_counts']['deviation'] == 0


async def test_unresolved_governance_ignores_resolved_history_and_monitoring_blocks(ops):  # noqa: F811
    target, refs = await origins(ops)
    record = governance.read(ops, {'workspace_id': target['workspace_id'], 'record_id': refs[5]['id']})
    data = {'wid': target['workspace_id'], 'snapshot': {'snapshot_id': refs[0]['id']},
        'assumption': record['revision']['document']['sources'][0]['id']}
    prior = [refs[5]['id']]
    for index in range(52):
        row = await governance.record(ops, data, root_cause_key=f'old-{index}', status='reported_resolved',
            disambiguation={'record_ids': prior, 'rationale': 'Separate historical risk decisions'})
        prior.append(row['record_id'])
    await governance.record(ops, data, kind='dependency', root_cause_key='monitoring', status='monitoring')
    out = pm_overview.read(ops, OWNER, target['workspace_id'])
    assert out['coverage']['sources']['governance']['complete']
    assert out['attention_counts']['blocker'] == 1 and out['progress']['blocked'] == 1


async def test_no_maintenance_policy_is_complete_empty_risk(served):  # noqa: F811
    from tests import test_pm_lifecycle as life
    from tests.test_pm_api import OWNER as API_OWNER
    d, _ = served
    ws, _ = await seed(d)
    for stage in ('idea', 'planning'):
        if stage == 'planning':
            await life.decide(d.ops, ws['workspace_id'], 'set_stage', who=API_OWNER, params={'stage': stage})
        out = pm_overview.read(d.ops, API_OWNER, ws['workspace_id'])
        assert out['coverage']['sources']['findings'] == {'complete': True, 'next_cursor': None, 'status': 'not_configured'}
        assert out['attention_counts']['risk'] == 0
        assert not out['attention_truncated']


async def test_stopped_findings_are_not_applicable(reviewed, monkeypatch):
    from bat_agent_connector import pm_maintenance_findings, pm_maintenance_policy
    w, service = reviewed, reviewed['d'].ops
    monkeypatch.setattr(pm_maintenance_policy, 'head', lambda *a: {'policy_id': 'fixture-policy'})
    monkeypatch.setattr(pm_maintenance_findings, 'current_findings', lambda *a: {
        'sources': {'issues': {'status': 'stopped', 'findings': []}}, 'observation_ref': None})
    out = pm_overview.read(service, quality.OWNER, w['workspace_id'])
    assert out['coverage']['sources']['findings']['status'] == 'not_applicable'
    assert out['coverage']['sources']['findings']['complete']
    assert out['attention_counts']['risk'] == 0 and not out['attention_truncated']


@pytest.mark.parametrize('kind,field', [('work_item', 'work'), ('milestone', 'roadmap')])
@pytest.mark.parametrize('status', [409, 413])
async def test_oversized_work_and_milestones_mark_partial_sources(ops, monkeypatch, kind, field, status):  # noqa: F811
    from bat_agent_connector import pm_thread_contexts
    target, _ = await origins(ops)
    original = pm_thread_contexts.resolve
    def limited(service, principal, wid, ref):
        if ref['kind'] == kind:
            raise OperationError('SOURCE_LIMIT', 'Fixture source limit', status)
        return original(service, principal, wid, ref)
    monkeypatch.setattr(pm_thread_contexts, 'resolve', limited)
    out = pm_overview.read(ops, OWNER, target['workspace_id'])
    assert not out['coverage']['sources'][field]['complete']
    assert {'field': field, 'reason': 'source_incomplete'} in out['unknown']
    assert out['attention_counts']['deviation'] is None and out['attention_truncated']
    if field == 'work':
        assert out['progress']['not_started'] is None
    projection = pm_overview.Projection(ops, OWNER, target['workspace_id'])
    def refuse(*args):
        raise OperationError('SOURCE_LIMIT', 'Fixture source limit', status)
    monkeypatch.setattr(pm_source_lineage, 'require_document', refuse)
    assert projection.visible({}, 'quality') is False
    assert projection.coverage['quality']['complete'] is False


async def test_withdrawn_forecast_is_unavailable_not_unrecorded(ops, monkeypatch):  # noqa: F811
    target, _ = await origins(ops)
    forecast = await roadmap.revision(ops, target['workspace_id'], 'forecast', updates=[{'milestone_id': 'release', 'forecast_date': '2026-11-04'}])
    original = pm_source_lineage.require_document
    def visible(db, principal, document):
        if document.get('revision', {}).get('revision_id') == forecast['revision_id']:
            raise OperationError('NOT_FOUND', 'Withdrawn forecast', 404)
        return original(db, principal, document)
    monkeypatch.setattr(pm_source_lineage, 'require_document', visible)
    out = pm_overview.read(ops, OWNER, target['workspace_id'])
    assert out['milestones'][0]['forecast_date'] is None
    assert not out['coverage']['sources']['forecast']['complete']
    assert {'field': 'milestones.forecast_date', 'reason': 'source_unavailable'} in out['unknown']
    assert {'field': 'milestones.forecast_date', 'reason': 'not_recorded'} not in out['unknown']


async def test_partially_accepted_milestone_is_in_progress(ops):  # noqa: F811
    target, plan, *_ = await roadmap.mapped(ops)
    projection = pm_overview.Projection(ops, OWNER, target['workspace_id'])
    # Existing milestone has one selected requirement; another explicitly linked
    # requirement is supplied through the original bounded roadmap reader.
    original = roadmap.pm_roadmap.get_revision
    from unittest.mock import patch
    def two_requirements(*args, **kwargs):
        value = original(*args, **kwargs)
        value['revision']['document']['milestones'][0]['requirement_ids'].append('recovery')
        return value
    with patch.object(roadmap.pm_roadmap, 'get_revision', two_requirements):
        result = projection.roadmap(plan, {'login': 'accepted', 'recovery': 'not_started'})
    assert result[0]['status'] == 'in_progress'


async def test_fifty_work_items_use_bounded_sql_and_read_snapshot(ops):  # noqa: F811
    import statistics
    import time

    from tests.test_pm_follow_up import work_for
    target, plan, *_ = await roadmap.mapped(ops)
    for _ in range(49):
        work = await work_for(ops, target)
        await roadmap.bind_work(ops, work, plan_revision_id=plan['revision_id'], requirement_ids=['login'])
    times, statements = [], []
    before = ops.db.total_changes
    ops.db.set_trace_callback(statements.append)
    try:
        for _ in range(5):
            start = time.perf_counter()
            result = pm_overview.read(ops, OWNER, target['workspace_id'])
            times.append(time.perf_counter() - start)
            assert result['progress']['requirements'] == 1 and result['progress']['complete']
    finally:
        ops.db.set_trace_callback(None)
    assert len(statements) / 5 < 2000
    assert 'BEGIN IMMEDIATE' not in statements
    assert statements.count('BEGIN') == 5
    assert ops.db.total_changes == before
    print('50-work overview median seconds:', statistics.median(times), 'SQL/read:', len(statements) / 5)


@pytest.mark.parametrize('withdrawal', ['lineage', 'list_filter', 'transition'])
async def test_hidden_pending_source_is_omitted_through_http_rpc_and_mcp(served, monkeypatch, withdrawal):  # noqa: F811
    from tests import test_pm_lifecycle as life
    from tests.test_pm_api import OWNER as API_OWNER
    from tests.test_pm_workspace import member
    d, port = served
    ws, _ = await seed(d)
    reader = Principal('overview-reader', frozenset({'observe'}))
    await member(d.ops, ws, reader.actor, 'reader', who=API_OWNER)
    async def effect(ctx):
        return {}
    life.register(d.ops, effect)
    pending = d.ops.create(API_OWNER, action='pm.synthetic.invest', target={'workspace_id': ws['workspace_id']},
        params={}, preconditions={}, idempotency_key=uuid.uuid4().hex)[0]
    await life.decide(d.ops, ws['workspace_id'], who=API_OWNER)
    from bat_agent_connector import pm_lifecycle
    if withdrawal == 'lineage':
        root = d.ops.db.execute("SELECT operation_id FROM operations WHERE action='pm.workspace.create'").fetchone()[0]
        d.ops.db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)', ('operation', pending['operation_id'], root, pending['operation_id']))
        assert not pm_source_lineage.visible(d.ops.db, reader, 'operation', pending['operation_id'])
    elif withdrawal == 'list_filter':
        original = pm_lifecycle._visible
        monkeypatch.setattr(pm_lifecycle, '_visible', lambda db, principal, oid:
            oid != pending['operation_id'] and original(db, principal, oid))
        page = pm_lifecycle.list_inflight(d.ops, reader, ws['workspace_id'])
        assert page['visibility_filtered'] and page['next_cursor'] is None
    else:
        monkeypatch.setattr(pm_lifecycle, '_transition_visible', lambda *args: False)
    auth = token(d, reader.actor, *reader.scopes)
    status, http_out = await http(port, 'GET', f"/api/v1/pm/workspaces/{ws['workspace_id']}/overview", tok=auth)
    assert status == 200, http_out
    rpc_out = await d.call_api('pm_overview_get', {'workspace_id': ws['workspace_id']}, reader)
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        mcp_out = unpack(await server.call_tool('pm_overview_get', {'workspace_id': ws['workspace_id']}))
    finally:
        await fleet.close()
    for result in (http_out, rpc_out, mcp_out):
        assert pending['operation_id'] not in str(result)
        assert result['coverage']['sources']['lifecycle_inflight']['omitted'] == (None if withdrawal == 'transition' else 1)
        assert {'field': 'lifecycle_inflight', 'reason': 'source_unavailable'} in result['unknown']
        assert not result['coverage']['sources']['lifecycle_inflight']['complete']
        assert result['attention_counts']['decision'] is None and result['attention_truncated']


@pytest.mark.parametrize('status', ['agent_assumption', 'user_confirmed', 'repo_evidence'])
async def test_goal_requires_confirmed_or_repository_input(ops, status):  # noqa: F811
    from tests import test_pm_plans as plans
    from tests.test_pm_discussions import act, prepared
    target = await prepared(ops)
    value = 'Restore member access\nwithout support delays'
    row = await act(ops, target, 'pm.input.record', {'slot': 'value', 'value': value, 'status': status,
        'source_ref': {'kind': 'repository', 'ref': 'example/product', 'revision': '1' * 40, 'path': 'README.md'}
            if status == 'repo_evidence' else {'kind': 'statement', 'ref': 'Explicitly recorded input'}})
    await act(ops, target, 'pm.thread.plan', {'selected_input_ids': [row['input_id']], 'rationale': 'Discuss project purpose'})
    plan = await plans.draft(ops, target)
    plans.adopt(plans.adoption(ops, target, plan['revision_id']))
    out = pm_overview.read(ops, OWNER, target['workspace_id'])
    if status == 'agent_assumption':
        assert out['plan']['goal'] is None
        assert {'field': 'plan.goal', 'reason': 'goal_unconfirmed'} in out['unknown']
    else:
        assert out['plan']['goal'] == ' '.join(value.splitlines())


def _copy_operation(db, original, **changes):
    row = dict(db.execute('SELECT * FROM operations WHERE operation_id=?', (original,)).fetchone())
    row.update(operation_id='op_' + uuid.uuid4().hex, idem_key=uuid.uuid4().hex, **changes)
    db.execute(f"INSERT INTO operations ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
    return row['operation_id']


async def test_overview_budgets_combined_work_and_operation_roots(delivery_ready):
    from tests.test_pm_work_packages import action
    from tests.test_work_items import item
    w, service = delivery_ready, delivery_ready['d'].ops
    delivered = await delivery_tests.settle(w, delivery_tests.apply(w, await delivery_tests.preview(w)))
    assert delivered['status'] == 'succeeded'
    preview = (await delivery_tests.delivery.pr_preview(service, 'o/r', 1))['merge_preview']
    env = {**delivery_tests.pr_delivery.merge_envelope(preview), 'idempotency_key': uuid.uuid4().hex}
    merged = await delivery_tests.settle(w, service.create(delivery_tests.DELIVERER, **env)[0])
    original_work = w['target']['work_item_id']
    (root,) = pm_source_lineage.roots(service.db, 'work_item', original_work)
    project = service.db.execute('SELECT project_id FROM work_items WHERE work_item_id=?', (original_work,)).fetchone()[0]
    works, operations = [], []
    for index in range(3):
        work = await item(w['d'], project, f'Explicitly associated work {index}', who=quality.OWNER)
        await action(w, 'pm.work.bind', {'workspace_id': w['workspace_id'], 'work_item_id': work},
            {'rationale': 'Explicit test association', 'plan_revision_id': w['revision']['revision_id'], 'requirement_ids': ['member.login']},
            {'expected_binding_id': None, 'expected_work_version': 1})
        oid = _copy_operation(service.db, merged['operation_id'], updated_at=merged['updated_at'] + index + 1)
        works.append(work)
        operations.append(oid)
        # Each resource remains independently readable; the union exceeds 64.
        for kind, identity in (('work_item', work), ('operation', oid)):
            for _ in range(22):
                source = _copy_operation(service.db, root)
                service.db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)', (kind, identity, source, oid))
        # The delivery operation is explicitly linked to this work's original roots.
        source = next(iter(pm_source_lineage.roots(service.db, 'work_item', work)))
        service.db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)', ('operation', oid, source, oid))
        assert pm_source_lineage.visible(service.db, delivery_tests.DELIVERER, 'work_item', work)
        assert pm_source_lineage.visible(service.db, delivery_tests.DELIVERER, 'operation', oid)
    out = pm_overview.read(service, delivery_tests.DELIVERER, w['workspace_id'])
    assert pm_source_lineage.read_response(service.db, delivery_tests.DELIVERER, out) == out
    assert len(out['recent_deliveries']) == 2
    assert out['attention_truncated'] and out['recent_deliveries_complete']
    assert any(u['reason'] == 'source_limit' for u in out['unknown'])
    # Also exercise the ordered shedding when pending and deviation references
    # accompany deliveries; these use the very same explicit central lineages.
    projection = pm_overview.Projection(service, delivery_tests.DELIVERER, w['workspace_id'])
    output = {'attention': [{'pending_operations': [{'operation_id': oid} for oid in operations]},
        *[{'reason': 'quality_revoked', 'context': {'work_item_id': wid}} for wid in works]],
        'recent_deliveries': [{'work_item_id': wid, 'operation_id': oid} for wid, oid in zip(works, operations)]}
    projection.fit_sources(output)
    assert len(pm_source_lineage.document_roots(service.db, output)) <= 64
    assert all('operation_id' not in row for row in output['attention'][0]['pending_operations'])
    assert all('work_item_id' not in row['context'] for row in output['attention'][1:])
    assert projection.unknown == [{'field': 'attention_references', 'reason': 'source_limit'}]


async def test_trimming_attention_references_preserves_complete_source_counts(reviewed, monkeypatch):
    w, service = reviewed, reviewed['d'].ops
    await _revoked(w)
    before = pm_overview.read(service, quality.OWNER, w['workspace_id'])
    original = pm_source_lineage.document_roots

    def limited(db, value):
        if isinstance(value, dict) and 'attention' in value and 'recent_deliveries' in value and any(
                'work_item_id' in item['context'] for item in value['attention'] if item.get('reason') == 'quality_revoked'):
            raise OperationError('PM_SOURCE_LINEAGE_LIMIT', 'Fixture union exceeds the document budget', 409)
        return original(db, value)

    monkeypatch.setattr(pm_source_lineage, 'document_roots', limited)
    after = pm_overview.read(service, quality.OWNER, w['workspace_id'])
    assert before['progress']['complete'] and before['progress']['in_review'] == 1
    assert after['progress'] == before['progress']
    assert after['attention_counts'] == before['attention_counts']
    assert after['recent_deliveries_complete'] == before['recent_deliveries_complete'] is True
    assert after['coverage']['sources']['quality']['complete']
    assert after['attention_truncated']
    assert not after['coverage']['sources']['attention_references']['complete']
    assert after['unknown'] == [*before['unknown'], {'field': 'attention_references', 'reason': 'source_limit'}]
    assert all('work_item_id' not in item['context'] for item in after['attention'] if item.get('reason') == 'quality_revoked')
    assert len(pm_source_lineage.document_roots(service.db, after)) <= 64
