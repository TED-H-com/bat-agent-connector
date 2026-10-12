"""The shared scheduler reads and real child hooks preserve original authority."""
import asyncio
import json

import pytest

from bat_agent_connector import mcp_server
from bat_agent_connector import pm_maintenance_checks as checks
from bat_agent_connector.github import GitHubReadLimit
from bat_agent_connector.operations import OpContext, OperationError
from tests import test_pm_github_observation_api as githubapi
from tests import test_pm_maintenance_checks as checktests
from tests.test_api_v1 import http, token, write_frames
from tests.test_pm_github_observation_api import (
    baseline_ready,  # noqa: F401 - fixture dependencies
    daemon,  # noqa: F401 - fixture dependencies
    release_ready,  # noqa: F401 - fixture dependencies
    served,  # noqa: F401 - fixture dependencies
)
from tests.test_pm_lifecycle import decide
from tests.test_pm_maintenance_checks import finish, request
from tests.test_pm_maintenance_checks import prepared as domain_prepared
from tests.test_pm_release_baselines import OWNER, READER
from tests.test_pm_run_api import unpack

github_ready, check_ops = githubapi.ready, checktests.ops


@pytest.fixture
async def ready(github_ready):
    d, port, wid, project = github_ready
    await decide(d.ops, wid, 'set_stage', params={'stage': 'development'})
    token(d, OWNER.actor, 'observe', 'manage')
    return d, port, wid, project


async def submit(d, port, wid):
    original = request(d.ops, {'workspace_id': wid})
    status, payload = await http(port, 'POST', '/api/v1/operations', tok=token(d, OWNER.actor, 'observe', 'manage'),
        body={k: v for k, v in original.items() if k != 'idempotency_key'},
        headers={'Idempotency-Key': original['idempotency_key']})
    assert status in {200, 202}, payload
    done = await finish(d.ops, payload['operation'])
    assert done['status'] == 'succeeded', done
    return original, done


async def test_shared_read_parity_never_schedules_and_original_key_preserves_children(ready, monkeypatch, mock):
    d, port, wid, _ = ready
    original, done = await submit(d, port, wid)
    rid = done['result']['run_id']
    auth = token(d, READER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    changes, reads = d.ops.db.total_changes, list(d.ops.context['github'].reads)
    try:
        discovered = {tool.name: tool for tool in await server.list_tools()}
        for method, fields, suffix in [
            ('pm_maintenance_checks_list', {'after': 0, 'limit': 1}, '/maintenance-checks?after=0&limit=1'),
            ('pm_maintenance_check_current_get', {}, '/maintenance-checks/current'),
            ('pm_maintenance_check_get', {'run_id': rid}, '/maintenance-checks/' + rid),
            ('pm_maintenance_schedule_get', {}, '/maintenance-schedule'),
        ]:
            assert discovered[method].annotations.read_only_hint
            status, expected = await http(port, 'GET', f'/api/v1/pm/workspaces/{wid}' + suffix, tok=auth)
            assert status == 200, expected
            params = {'workspace_id': wid, **fields}
            assert await d.call_api(method, params, READER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        assert d.ops.db.total_changes == changes
        replay, created = d.ops.create(OWNER, **original)
        assert not created and replay['operation_id'] == done['operation_id'] and replay['result'] == done['result']
        assert d.ops.context['github'].reads == reads
        assert d.ops.db.execute('SELECT COUNT(*) FROM pm_maintenance_check_children').fetchone()[0] == 2
        status, caps = await http(port, 'GET', '/api/v1/capabilities', tok=auth)
        assert status == 200 and caps['features']['pm_workspaces']['maintenance_checks'] == checks.capabilities(d.ops)
        assert caps['features']['pm_workspaces']['maintenance_policy']['scheduler_available'] is True
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_source_withdrawal_hides_parent_children_events_and_original_replay(ready):
    d, port, wid, project = ready
    original, done = await submit(d, port, wid)
    run = checks.get_run(d.ops, OWNER, wid, done['result']['run_id'])['run']
    ids = [done['operation_id'], *[row['operation_id'] for row in run['document']['children']], run['run_id']]
    reads = list(d.ops.context['github'].reads)
    d.ops.db.execute("UPDATE projects SET repositories='[]' WHERE project_id=?", (project,))
    auth = token(d, READER.actor, 'observe')
    for operation_id in ids[:3]:
        assert (await http(port, 'GET', '/api/v1/operations/' + operation_id, tok=auth))[0] == 404
    for method, route in [('pm_maintenance_checks_list', '/maintenance-checks'),
                          ('pm_maintenance_check_current_get', '/maintenance-checks/current'),
                          ('pm_maintenance_schedule_get', '/maintenance-schedule')]:
        status, value = await http(port, 'GET', f'/api/v1/pm/workspaces/{wid}' + route, tok=auth)
        assert status == 200 and value['visibility_filtered'] is True
        assert value == await d.call_api(method, {'workspace_id': wid}, READER)
        assert not any(ident in json.dumps(value) for ident in ids)
    status, events = await http(port, 'GET', '/api/v1/events?after=0&limit=500', tok=auth)
    assert status == 200 and not any(ident in json.dumps(events) for ident in ids)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        d.ops.create(OWNER, **original)
    assert d.ops.context['github'].reads == reads


async def test_exact_routes_cursors_and_current_scopes(ready):
    d, port, wid, _ = ready
    _, done = await submit(d, port, wid)
    root = f'/api/v1/pm/workspaces/{wid}/maintenance-checks'
    auth = token(d, READER.actor, 'observe')
    for suffix in ['?after=', '?after=-1', '?after=0&after=1', '?after=9223372036854775808',
                   '?limit=0', '?limit=101', '?refresh=true', '/current?after=0', '/' + done['result']['run_id'] + '?limit=1']:
        assert (await http(port, 'GET', root + suffix, tok=auth))[0] == 422, suffix
    for suffix in ['/latest', '/pmck_bad', '/current/start']:
        assert (await http(port, 'GET', root + suffix, tok=auth))[0] == 404
    for cursor in (10**9, 2**63 - 1):
        status, page = await http(port, 'GET', root + f'?after={cursor}', tok=auth)
        assert status == 200 and page['runs'] == []
    assert (await http(port, 'GET', root, tok=token(d, OWNER.actor, 'manage')))[0] == 403
    assert (await http(port, 'GET', root, tok=token(d, 'stranger', 'observe')))[0] == 404
    assert (await http(port, 'GET', f'/api/v1/pm/workspaces/{wid}/maintenance-schedule?run=true', tok=auth))[0] == 422


# Exercise the actual shared Traversal hook with the real children, not a handler substitute.
base_ops, release_ops = checktests.base_ops, checktests.release_ops
workload_ops, github_ops = checktests.workload_ops, checktests.github_ops


async def test_real_provider_call_is_clamped_to_original_parent_deadline(check_ops, monkeypatch):
    ops = check_ops
    data = await domain_prepared(ops, monkeypatch)
    parent = ops.create(OWNER, **request(ops, data))[0]
    checks._reserve(OpContext(ops, parent))
    row = checks._row(ops, parent['operation_id'])
    ops.context['check_test_clock'][0] = row['deadline_at'] - .04
    provider = ops.context['github']
    waits = []

    async def delayed(path, *, query, max_bytes, timeout_s):
        waits.append(timeout_s)
        await asyncio.sleep(timeout_s)
        raise GitHubReadLimit('Synthetic header deadline')

    monkeypatch.setattr(provider, 'bounded_get', delayed)
    done = await asyncio.wait_for(finish(ops, parent), 2)
    assert done['status'] == 'succeeded' and done['result']['outcome'] == 'incomplete'
    assert len(waits) == 1 and 0 < waits[0] <= .05
