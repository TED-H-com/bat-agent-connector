"""Public receipt registration, strict selectors and one capability authority."""
import json

from bat_agent_connector import api_auth, pm_roadmap_reports
from bat_agent_connector import pm_roadmap_report_materialization as reports
from tests.pm_report_transport_helpers import RECEIPTS, transports
from tests.test_api_v1 import http, token
from tests.test_mcp_principal import call
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_roadmap_report_materialization import fixed as fixed_fixture
from tests.test_pm_roadmap_report_materialization import (  # noqa: F401
    materialize,
    pm,
    ready,
    spec_fixed,
    world,
)
from tests.test_pm_run_api import unpack

fixed = fixed_fixture
GET = 'pm_roadmap_report_materialization_get'


async def test_original_receipt_get_list_and_strict_public_arguments(fixed, monkeypatch):
    w, d = fixed, fixed['d']
    saved = await materialize(w)
    wid, mid = w['workspace_id'], saved['result']['materialization_id']
    auth = token(d, OWNER.actor, *OWNER.scopes)
    async with transports(w, monkeypatch) as read:
        monkeypatch.setenv('BATC_API_TOKEN', auth)
        root = f'/api/v1/pm/workspaces/{wid}/roadmap-report-materializations'
        tools = {tool.name: tool for tool in await read.server.list_tools()}
        changes = d.ops.db.total_changes
        for method, params, path in ((RECEIPTS, {'workspace_id': wid}, root),
                (GET, {'workspace_id': wid, 'materialization_id': mid}, root + '/' + mid)):
            tool = tools[method]
            assert tool.annotations.read_only_hint
            assert tool.input_schema['additionalProperties'] is False
            assert set(tool.input_schema['properties']) == (set(params) | {'after', 'limit'} if method == RECEIPTS else set(params))
            status, page = await http(read.port, 'GET', path, tok=auth)
            assert status == 200, page
            rpc_status, rpc = await http(read.port, 'POST', '/rpc', tok=auth,
                body={'method': method, 'params': params})
            assert rpc_status == 200 and rpc['result'] == page
            assert unpack(await read.server.call_tool(method, params)) == page
            row = page['materializations'][0] if method == RECEIPTS else page['materialization']
            assert row['kind'] == 'roadmap_report_materialization' and row['materialization_id'] == mid
        assert d.ops.db.total_changes == changes
        for suffix in ('?after=', '?after=-1', '?after=9223372036854775808', '?limit=51', '?limit=0',
                '?after=0&after=1', '?limit=20&limit=20', '?body=x', '?format=html', '/' + mid + '?limit=1'):
            status, body = await http(read.port, 'GET', root + suffix, tok=auth)
            assert status == 422 and body['error']['code'] in {'INVALID_REQUEST', 'INVALID_PARAMS'}, (suffix, body)
        for changes in ({'after': True}, {'after': None}, {'after': '0'}, {'after': 0.0},
                {'after': 2**63}, {'limit': False}, {'limit': 51}, {'body': 'x'}, {'format': 'html'}):
            params = {'workspace_id': wid, **changes}
            status, body = await http(read.port, 'POST', '/rpc', tok=auth,
                body={'method': RECEIPTS, 'params': params})
            assert status == 400 and body['error'] == 'INVALID_PARAMS', (changes, body)
            refusal = await call(read.server, RECEIPTS, params)
            assert 'INVALID_PARAMS' in refusal or 'validation error' in refusal, (changes, refusal)
        for changes in ({'materialization_id': None}, {'materialization_id': []},
                {'workspace_id': []}, {'materialization_id': 'null'}, {'unknown': 1}):
            refusal = await call(read.server, GET, {'workspace_id': wid, 'materialization_id': mid, **changes})
            assert any(code in refusal for code in ('NOT_FOUND', 'INVALID_PARAMS', 'validation error')), refusal
        status, page = await http(read.port, 'GET', root + '?after=9223372036854775807', tok=auth)
        assert status == 200 and page['materializations'] == [] and page['next_after'] is None
        for suffix in ('/current', '/pmrpt_bad', '/' + mid + '/start'):
            assert (await http(read.port, 'GET', root + suffix, tok=auth))[0] == 404
        assert (await http(read.port, 'GET', root, tok=token(d, 'report-outsider', 'observe')))[0] == 404
        assert (await http(read.port, 'GET', root, tok=token(d, OWNER.actor, 'manage')))[0] == 403
        assert mid not in json.dumps((await http(read.port, 'GET', root,
            tok=token(d, 'report-outsider', 'observe')))[1])


async def test_report_save_capability_has_one_registration_authority_and_all_scopes(fixed, monkeypatch):
    d = fixed['d']
    for missing in (None, 'observe', 'manage', 'start'):
        principal = api_auth.Principal(OWNER.actor, OWNER.scopes - {missing})
        status, caps = await d.api.capabilities(principal)
        assert status == 200
        features = caps['features']['pm_workspaces']
        assert features['roadmap_report'] == {**pm_roadmap_reports.capabilities(), 'materialization': True}
        assert features['roadmap_report_materialization'] == reports.capabilities()
        assert next(row for row in caps['actions'] if row['action'] == reports.ACTION)['allowed'] is (missing is None)
    with monkeypatch.context() as patch:
        patch.delitem(d.ops.actions, reports.ACTION)
        _, caps = await d.api.capabilities(OWNER)
        features = caps['features']['pm_workspaces']
        assert features['roadmap_report'] == pm_roadmap_reports.capabilities()
        assert 'roadmap_report_materialization' not in features
        assert reports.ACTION not in {row['action'] for row in caps['actions']}
