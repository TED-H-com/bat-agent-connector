"""One central read, exact selectors, and unchanged adapter error envelopes."""
from __future__ import annotations

from urllib.parse import urlencode

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import api_auth, mcp_server, pm_reads
from bat_agent_connector import pm_plan_impact as impact
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_daemon import API_RPC
from tests.test_api_v1 import http, token
from tests.test_pm_plan_impact import mapped as mapped
from tests.test_pm_plan_impact import pm as pm
from tests.test_pm_plan_impact import ready as ready
from tests.test_pm_plan_impact import selected as selected
from tests.test_pm_plan_impact import world as world
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_quality_assessment_transport import mcp, rpc, table_counts
from tests.test_pm_run_api import unpack
from tests.test_pm_work_packages import member

METHOD = 'pm_plan_impact_preview'


def path(params):
    query = {key: ','.join(value) if isinstance(value, list) else value
             for key, value in params.items() if key != 'workspace_id'}
    return f"/api/v1/pm/workspaces/{params['workspace_id']}/plan-impact?" + urlencode(query)


async def test_impact_http_rpc_mcp_success_parity_and_read_only_capability(selected, monkeypatch, mock):
    w, d = selected, selected['d']
    params = w['impact_selector']
    await member(w, 'impact-reader', 'reader')
    for auth in [token(d, OWNER.actor, 'observe'), token(d, 'impact-reader', 'observe'), d._admin_token]:
        principal = api_auth.authenticate(d.ops.db, auth, d._admin_token)
        expected = impact.preview(d.ops, principal, **params)
        server, fleet = mcp(w, monkeypatch, auth)
        try:
            tools = {tool.name: tool for tool in await server.list_tools()}
            assert API_RPC[METHOD] == 'observe' and METHOD in mcp_server.READ_TOOLS
            assert tools[METHOD].annotations.read_only_hint
            assert tools[METHOD].input_schema['additionalProperties'] is False
            assert set(tools[METHOD].input_schema['required']) == set(params)
            before = (table_counts(d.ops.db), d.ops.db.total_changes, len(mock.invokes), len(w['gh'].requests))
            assert await http(w['port'], 'GET', path(params), tok=auth) == (200, expected)
            assert await rpc(w, auth, METHOD, params) == {'result': expected}
            assert unpack(await server.call_tool(METHOD, params)) == expected
            assert before == (table_counts(d.ops.db), d.ops.db.total_changes, len(mock.invokes), len(w['gh'].requests))
            _, global_caps = await http(w['port'], 'GET', '/api/v1/capabilities', tok=auth)
            workspace_caps = await d.call_api('pm_capabilities_get', {'workspace_id': w['workspace_id']}, principal)
            assert global_caps['features']['pm_workspaces']['plan_impact'] == workspace_caps['plan_impact'] == impact.capabilities()
            assert set(impact.capabilities()) == {'version', 'read_only', 'read', 'required_scopes',
                                                'max_bindings', 'max_subjects', 'max_candidate_rows', 'max_bytes'}
        finally:
            await fleet.close()


async def test_impact_mcp_strict_keys_and_json_looking_strings_keep_central_errors(selected, monkeypatch):
    w, d = selected, selected['d']
    params, received = w['impact_selector'], []
    original = pm_reads.read
    def capture(ops, principal, method, query):
        if method == METHOD:
            received.append(query.copy())
        return original(ops, principal, method, query)
    monkeypatch.setattr(pm_reads, 'read', capture)
    auth = token(d, OWNER.actor, 'observe')
    server, fleet = mcp(w, monkeypatch, auth)
    try:
        with pytest.raises(ToolError, match='extra_forbidden'):
            await server.call_tool(METHOD, params | {'actor': 'injected'})
        assert received == []
        for field in params:
            for value in ('null', '[]', '{}', 'true', 'false', '"null"'):
                query = params | {field: value}
                received.clear()
                with pytest.raises(ToolError) as error:
                    await server.call_tool(METHOD, query)
                assert received == [query]
                with pytest.raises(OperationError) as central:
                    original(d.ops, OWNER, METHOD, query)
                expected = {'error': central.value.code, 'message': central.value.message}
                assert await rpc(w, auth, METHOD, query) == expected
                assert expected['error'] in str(error.value) and expected['message'] in str(error.value)
                if field != 'workspace_id':  # HTTP rejects a malformed path at the exact route guard.
                    assert await http(w['port'], 'GET', path(query), tok=auth) == (
                        central.value.status, {'error': {'code': central.value.code, 'message': central.value.message}})
    finally:
        await fleet.close()


async def test_impact_http_exact_path_keys_required_selectors_no_duplicates_or_body(selected):
    w, d = selected, selected['d']
    params = w['impact_selector']
    auth = token(d, OWNER.actor, 'observe')
    for field in params.keys() - {'workspace_id'}:
        for invalid in [params | {field: ''}, {k: v for k, v in params.items() if k != field}]:
            assert (await http(w['port'], 'GET', path(invalid), tok=auth))[0] == 422
        assert (await http(w['port'], 'GET', path(params) + f'&{field}=duplicate', tok=auth))[0] == 422
    for extra in ('actor=x', 'journal_cursor=0', 'limit=1'):
        assert (await http(w['port'], 'GET', path(params) + '&' + extra, tok=auth))[0] == 422
    for suffix in ('/', '/raw'):
        url = path(params).replace('/plan-impact?', '/plan-impact' + suffix + '?')
        assert (await http(w['port'], 'GET', url, tok=auth))[0] == 404
    assert (await http(w['port'], 'GET', path(params), tok=auth, body={}))[0] == 422
    assert (await http(w['port'], 'POST', path(params), tok=auth, body={}))[0] in {404, 405}
    for invalid in [params['binding_ids'] * 2, ['pmwb_' + f'{i:032x}' for i in range(51)]]:
        assert (await http(w['port'], 'GET', path(params | {'binding_ids': invalid}), tok=auth))[0] == 422


async def test_impact_revoked_membership_returns_only_generic_central_refusal(selected, monkeypatch):
    w, d = selected, selected['d']
    params = w['impact_selector']
    await member(w, 'revoked-impact-reader', 'reader')
    principal = api_auth.Principal('revoked-impact-reader', frozenset({'observe'}))
    impact.preview(d.ops, principal, **params)
    await member(w, principal.actor, None)
    auth = token(d, principal.actor, 'observe')
    server, fleet = mcp(w, monkeypatch, auth)
    try:
        with pytest.raises(OperationError) as central:
            impact.preview(d.ops, principal, **params)
        assert central.value.code == 'PM_IMPACT_SOURCE_UNAVAILABLE'
        assert await http(w['port'], 'GET', path(params), tok=auth) == (404, {'error': {
            'code': central.value.code, 'message': central.value.message}})
        assert await rpc(w, auth, METHOD, params) == {'error': central.value.code, 'message': central.value.message}
        with pytest.raises(ToolError) as error:
            await server.call_tool(METHOD, params)
        assert central.value.code in str(error.value) and central.value.message in str(error.value)
        assert params['snapshot_id'] not in str(error.value)
    finally:
        await fleet.close()


@pytest.mark.parametrize('limit', ['subjects', 'candidates'])
@pytest.mark.parametrize('hidden', ['first', 'last', 'none'])
async def test_impact_real_bound_errors_have_identical_transport_code_and_message(selected, monkeypatch, limit, hidden):
    from tests.test_pm_plan_impact_limits import overflow
    w, d = selected, selected['d']
    params = await overflow(w, limit, hidden)
    auth = token(d, OWNER.actor, 'observe')
    server, fleet = mcp(w, monkeypatch, auth)
    try:
        with pytest.raises(OperationError) as central:
            impact.preview(d.ops, OWNER, **params)
        code, message, status = central.value.code, central.value.message, central.value.status
        assert await http(w['port'], 'GET', path(params), tok=auth) == (status, {'error': {'code': code, 'message': message}})
        assert await rpc(w, auth, METHOD, params) == {'error': code, 'message': message}
        with pytest.raises(ToolError) as error:
            await server.call_tool(METHOD, params)
        assert code in str(error.value) and message in str(error.value)
    finally:
        await fleet.close()


@pytest.mark.parametrize('conflict', ['comparison', 'duplicate', 'contradiction'])
async def test_impact_source_denial_dominates_derived_errors_in_every_adapter(selected, monkeypatch, conflict):
    from tests.test_pm_plan_impact_limits import conflicting_sources
    w, d = selected, selected['d']
    params = await conflicting_sources(w, monkeypatch, conflict)
    auth = token(d, OWNER.actor, 'observe')
    server, fleet = mcp(w, monkeypatch, auth)
    code = 'PM_IMPACT_SOURCE_UNAVAILABLE'
    message = "The selected impact sources are unavailable or cannot be established within the read's bounds; select fewer bindings"
    try:
        assert await http(w['port'], 'GET', path(params), tok=auth) == (404, {'error': {'code': code, 'message': message}})
        assert await rpc(w, auth, METHOD, params) == {'error': code, 'message': message}
        with pytest.raises(ToolError) as error:
            await server.call_tool(METHOD, params)
        assert code in str(error.value) and message in str(error.value)
    finally:
        await fleet.close()
