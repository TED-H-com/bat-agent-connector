"""Real adopted prerequisites must survive readiness, handoff and final frames."""
from __future__ import annotations

import copy
import json
import uuid
from urllib.parse import urlencode

import pytest

from bat_agent_connector import mcp_server, pm_package_handoff, pm_work_packages, work_items
from bat_agent_connector.operations import OperationError
from tests import test_pm_execution_verification as producer
from tests import test_pm_package_handoff as handoffs
from tests import test_pm_quality_reviews as reviews
from tests import test_pm_requirement_acceptance as acceptance
from tests import test_pm_work_packages as packages
from tests.test_api_v1 import http, token
from tests.test_pm_plan_operations import adopt, draft
from tests.test_pm_run_api import unpack

world, pm, ready = packages.world, packages.pm, packages.ready
owned, qualified = producer.owned, acceptance.qualified
OWNER = producer.OWNER


@pytest.fixture
async def fixed(ready):
    w = ready
    requirements = copy.deepcopy(w['requirements'])
    requirements.append({**copy.deepcopy(requirements[0]), 'id': 'member.recovery', 'depends_on': ['member.login']})
    w['revision'] = await draft(w, requirements=requirements)
    assert (await adopt(w, w['revision']))['status'] == 'succeeded'
    await packages.associate(w)
    w['package'] = packages.read(w, await packages.create(w))
    handoffs.dispatch_runner(w)
    return w


@pytest.fixture
async def downstream(qualified):
    w, ops = qualified, qualified['d'].ops
    project_id = w['child']['params']['project_id']
    item = (await packages.action(w, 'work_item.create', {'project_id': project_id},
        {'title': 'Dependent recovery', 'goal': 'Use accepted login', 'acceptance': 'Original recovery behavior'}))['work_item_id']
    target = {'workspace_id': w['workspace_id'], 'work_item_id': item}
    await packages.action(w, 'pm.work.bind', target,
        {'rationale': 'Explicit original dependency', 'plan_revision_id': w['revision']['revision_id'], 'requirement_ids': ['member.recovery']},
        {'expected_binding_id': None, 'expected_work_version': work_items._get_item(ops.db, item)['version']})
    dw = {**w, 'target': target, 'params': copy.deepcopy(w['params'])}
    dw['params']['requirements'] = ['member.recovery']
    dw['params']['tasks'][0].update(key='identity.recovery', requirement_ids=['member.recovery'])
    dw['params']['verification'][0].update(key='verify.recovery', requirement_id='member.recovery')
    dw['package'] = packages.read(dw, await packages.create(dw))
    refs = {'member.login': {'condition': w['condition'], 'reference': w['quality_ref'], 'execution_operation_id': w['child']['operation_id']}}
    return w, dw, refs


async def revoke(w):
    return await reviews.execute(w, {'action': 'pm.quality.review.revoke',
        'target': {'workspace_id': w['workspace_id'], 'review_id': w['review']['review_id']},
        'params': {'reason': 'Later counterexample'}, 'preconditions': {'expected_version': w['review']['version']},
        'idempotency_key': uuid.uuid4().hex})


async def test_original_quality_readiness_all_transports_and_frozen_handoff(downstream, monkeypatch):
    w, dw, refs = downstream
    d, wid, package_id = w['d'], w['workspace_id'], dw['package']['package_id']
    without = pm_work_packages.readiness(d.ops, OWNER, wid, package_id)
    assert next(c for c in without['checks'] if c['check'].startswith('dependency:'))['status'] == 'blocked'
    with pytest.raises(OperationError, match='PM_PACKAGE_NOT_READY'):
        d.ops.create(OWNER, **await handoffs.request(dw))
    selected = pm_work_packages.readiness(d.ops, OWNER, wid, package_id, quality_refs=refs)
    assert next(c for c in selected['checks'] if c['check'].startswith('dependency:'))['assessment'] == 'accepted'
    auth = token(d, OWNER.actor, *OWNER.scopes)
    monkeypatch.setenv('BATC_TASK_URL', f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    method = 'pm_work_package_readiness_get'
    params = {'workspace_id': wid, 'package_id': package_id, 'quality_refs': refs}
    path = f'/api/v1/pm/workspaces/{wid}/work-packages/{package_id}/readiness'
    before = d.ops.db.total_changes
    try:
        status, data = await http(w['port'], 'GET', path+'?'+urlencode({'quality_refs': json.dumps(refs)}), tok=auth)
        assert status == 200 and data == selected
        assert await d.call_api(method, dict(params), OWNER) == data
        assert unpack(await server.call_tool(method, params)) == data
        assert d.ops.db.total_changes == before
        for malformed in ('null', '[]', '{', json.dumps({'r': 'x'*16_384})):
            assert (await http(w['port'], 'GET', path+'?'+urlencode({'quality_refs': malformed}), tok=auth))[0] == 422
    finally:
        await fleet.close()
    env = await handoffs.request(dw, pm_requirement_quality_refs=refs)
    done = await handoffs.run(dw, env)
    assert done['status'] == 'succeeded', done
    assert pm_package_handoff.binding(done)['contract']['pm_requirement_quality_refs'] == refs
    await revoke(w)
    # Historical handoff remains readable; new investment loses prerequisite acceptance.
    assert pm_package_handoff.source_visible(d.ops.db, OWNER, done)
    replay, fresh = d.ops.create(OWNER, **env)
    assert not fresh and replay['operation_id'] == done['operation_id']
    with pytest.raises(OperationError, match='PM_PACKAGE_NOT_READY'):
        d.ops.create(OWNER, **await handoffs.request(dw, pm_requirement_quality_refs=refs))


async def test_revocation_after_admission_prevents_dependent_bat_start(downstream, mock):
    w, dw, refs = downstream
    ops = w['d'].ops
    op = ops.create(OWNER, **await handoffs.request(dw, pm_requirement_quality_refs=refs))[0]
    before = len(mock.invokes)
    await revoke(w)
    await ops._execute(op['operation_id'])
    failed = ops.get(op['operation_id'])
    assert failed['status'] == 'failed' and failed['error_code'] == 'PM_PACKAGE_NOT_READY', failed
    assert not any(call['channel'] in {'claude:start-session', 'claude:send-message'} for call in mock.invokes[before:])
