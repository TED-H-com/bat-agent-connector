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
    delivery_tests.revoke(w)
    with pytest.raises(OperationError) as exc:
        pm_overview.read(service, delivery_tests.DELIVERER, w['workspace_id'])
    assert exc.value.status == 404
