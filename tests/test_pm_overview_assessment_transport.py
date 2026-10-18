"""HTTP/RPC/MCP use one source-gated, read-only assessment contract."""
from __future__ import annotations

import asyncio
import json

from bat_agent_connector import mcp_server
from tests import test_pm_overview_assessments as assessment
from tests.test_api_v1 import http, token

world, pm, ready = assessment.world, assessment.pm, assessment.ready
proposal_world = assessment.proposal_world


def output(result):
    if isinstance(result, tuple):
        content, structured = result
        return structured if structured is not None else json.loads(content[0].text)
    content = result.content if hasattr(result, 'content') else result
    return json.loads(content[0].text)


async def test_preview_and_history_match_all_transports_without_model_work(proposal_world, monkeypatch):
    w, owner = proposal_world, assessment.OWNER
    d = w['d']
    saved = await assessment.execute(w, assessment.request(w))
    target = assessment.plans.target(w)
    root = f"/api/v1/pm/workspaces/{w['workspace_id']}"
    server = await asyncio.start_server(d._handle, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    auth = token(d, owner.actor, *owner.scopes)
    foreign = token(d, 'foreign-observer', 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    mcp, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    before = d.ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
    sends = sum(instance.sends for instance in w['instances'])
    try:
        preview = root + f"/threads/{target['thread_id']}/context-bundle"
        status, first = await http(port, 'GET', preview + '?kind=overview_assessment', tok=auth)
        assert status == 200, first
        rpc = await d.call_api('pm_context_bundle_preview', {**target, 'kind': 'overview_assessment'}, owner)
        via_mcp = output(await mcp.call_tool('pm_context_bundle_preview', {**target, 'kind': 'overview_assessment'}))
        assert first['selector'] == rpc['selector'] == via_mcp['selector']
        assert all(value['calls_provider'] is False for value in (first, rpc, via_mcp))
        for path, method, params in [
            (root + '/overview-assessments', 'pm_overview_assessments_list', {'workspace_id': w['workspace_id']}),
            (root + '/overview-assessments/' + saved['assessment']['assessment_id'], 'pm_overview_assessment_get',
             {'workspace_id': w['workspace_id'], 'assessment_id': saved['assessment']['assessment_id']}),
        ]:
            status, expected = await http(port, 'GET', path, tok=auth)
            assert status == 200, expected
            assert expected == await d.call_api(method, params, owner)
            assert expected == output(await mcp.call_tool(method, params))
            assert (await http(port, 'GET', path))[0] == 401
            assert (await http(port, 'GET', path, tok=foreign))[0] == 404
        for suffix in ['kind=other', 'kind=', 'kind=overview_assessment&kind=overview_assessment',
                       'kind=overview_assessment&max_chars=32001', 'kind=overview_assessment&execute=true']:
            assert (await http(port, 'GET', preview + '?' + suffix, tok=auth))[0] == 422
        for suffix in ['limit=51', 'limit=', 'before=', 'before=x&before=y', 'actor=owner', 'after=0']:
            assert (await http(port, 'GET', root + '/overview-assessments?' + suffix, tok=auth))[0] == 422
        caps = (await http(port, 'GET', root + '/capabilities', tok=auth))[1]['overview_assessment']
        assert caps['version'] == 1 and caps['limits']['timeout_s'] == 300 and caps['limits']['monetary_cap'] is None
        assert before == d.ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
        assert sends == sum(instance.sends for instance in w['instances'])
    finally:
        server.close()
        await server.wait_closed()
        await fleet.close()
