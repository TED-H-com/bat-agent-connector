"""Real journal/Git sources and shared assessment entries, with a fake native turn."""
import json
import uuid

import pytest

from bat_agent_connector import (
    api_auth,
    controller_authority,
    mcp_server,
    pm_discussions,
    pm_follow_up,
    pm_native_runtime,
    pm_plans,
)
from bat_agent_connector.operations import OperationError
from tests import test_pm_native_operations as native_fixture
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token, write_frames
from tests.test_mcp_principal import call
from tests.test_pm_discussions import CONTRIBUTOR, OWNER, READER, member
from tests.test_pm_evidence import capture, link, task
from tests.test_pm_follow_up_api import request as intake_request
from tests.test_pm_follow_up_api import setup as prepare_workspace
from tests.test_pm_governance_api import send
from tests.test_pm_native_entrypoints import native_fixture as install_fake
from tests.test_pm_native_runtime import MODEL, TURN, protocol
from tests.test_pm_operation_wait_visibility import wait_and_withdraw
from tests.test_pm_run_api import unpack
from tests.test_pm_snapshots import snapshot
from tests.test_pm_sprint_api import pm as pm_fixture
from tests.test_pm_sprint_api import served as served_fixture
from tests.test_pm_sprint_api import sprint_clock as sprint_clock_fixture
from tests.test_pm_sprint_api import world as repository_world

pm, world, served, sprint_clock = pm_fixture, repository_world, served_fixture, sprint_clock_fixture
ANALYST = api_auth.Principal(CONTRIBUTOR.actor, frozenset({'observe', 'manage', 'start'}))
SUMMARY = 'Compare this fixed source before deciding the next step.'


def install_runtime(d, monkeypatch):
    settings, instances = install_fake(d)
    document = {'version': 1, 'summary': {'text': SUMMARY, 'source_refs': ['knowledge.scope']},
                'findings': [], 'tradeoffs': [], 'questions': [], 'recommendations': []}
    raw = json.dumps(document)

    def reply(prompt, client_id):
        events = protocol(prompt, client_id)
        events[2]['params']['item']['text'] = raw
        events[-1]['params']['turn']['items'][-1]['text'] = raw
        return events

    monkeypatch.setattr(native_fixture, 'protocol', reply)
    return settings, instances, document


async def setup(d, port, selection, *, private=False):
    data, snap = await prepare_workspace(d, selection)
    wid = data['workspace_id']
    data['thread_id'] = d.ops.db.execute('SELECT thread_id FROM pm_threads WHERE workspace_id=?', (wid,)).fetchone()[0]
    if private:
        work = {'workspace_id': wid, 'work_item_id': data['work'][0]}
        with controller_authority.use(controller_authority.binding(d.ops.db, OWNER), d.journal):
            source = task(d.ops)
            await link(d.ops, work, 'task', source['task_id'])
            await capture(d.ops, work)
        data['private_task'] = source
        snap = await snapshot(d.ops, wid)
    selected = intake_request(d, wid, snap, thread_id=data['thread_id'])
    selected['preconditions']['expected_thread_version'] = pm_discussions._thread(d.ops.db, wid, data['thread_id'])['version']
    intake = await send(d, port, selected, CONTRIBUTOR)
    data['intake'] = pm_follow_up.get_intake(d.ops, OWNER, wid, intake['result']['intake_id'])['intake']
    return data


def request(d, data):
    return {'action': 'pm.follow_up.analyze',
        'target': {key: data[key] for key in ('workspace_id', 'thread_id')},
        'params': {'intake_id': data['intake']['intake_id'], 'purpose': 'Compare the selected fixed intake', 'model': MODEL},
        'preconditions': {'expected_version': pm_discussions._thread(d.ops.db, data['workspace_id'], data['thread_id'])['version'],
                          'expected_intake_digest': data['intake']['document_digest']},
        'idempotency_key': uuid.uuid4().hex}


def withdraw(d, data):
    d.ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (data['private_task']['task_id'],))


async def test_assessment_read_parity_exact_queries_capabilities_and_no_chat_effect(served, monkeypatch, mock):
    d, port, selection = served
    data = await setup(d, port, selection)
    _, instances, document = install_runtime(d, monkeypatch)
    wid = data['workspace_id']
    before_thread = pm_discussions.get_thread(d.ops, OWNER, wid, data['thread_id'])
    before_plan = pm_plans.head(d.ops.db, wid)
    op = await send(d, port, request(d, data), ANALYST)
    aid = op['result']['outcome']['assessment_id']
    root = f'/api/v1/pm/workspaces/{wid}/follow-up-assessments'
    auth = token(d, READER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    try:
        tools = {tool.name: tool for tool in await server.list_tools()}
        before = d.ops.db.total_changes
        for method, fields, suffix in [
            ('pm_follow_up_assessments_list', {'after': 0, 'limit': 1}, '?after=0&limit=1'),
            ('pm_follow_up_assessments_list', {'intake_id': data['intake']['intake_id']}, '?intake_id=' + data['intake']['intake_id']),
            ('pm_follow_up_assessment_get', {'assessment_id': aid}, '/' + aid),
        ]:
            assert tools[method].annotations.read_only_hint
            status, expected = await http(port, 'GET', root + suffix, tok=auth)
            assert status == 200, expected
            params = {'workspace_id': wid, **fields}
            assert await d.call_api(method, params.copy(), READER) == expected
            assert unpack(await server.call_tool(method, params)) == expected
        assert d.ops.db.total_changes == before
        detail = (await http(port, 'GET', root + '/' + aid, tok=auth))[1]['assessment']
        assert detail['document'] == document and detail['validation']['status'] == 'format_valid'
        assert detail['provenance']['native_turn_id'] == TURN and detail['provenance']['actual_model'] == MODEL
        assert detail['assessment_basis'] == 'unadopted_agent_analysis'
        assert not detail['conclusions_verified'] and not detail['execution_authorized']
        for suffix in ['?after=', '?limit=', '?actor=', '?after=0&after=1', '?limit=&limit=1',
                       '?limit=0', '?limit=101', '?after=-1', '?after=1000000000', '?intake_id=',
                       '?intake_id=' + data['intake']['intake_id'] + '&intake_id=' + data['intake']['intake_id'],
                       '/' + aid + '?limit=1', '/' + aid + '?actor=']:
            assert (await http(port, 'GET', root + suffix, tok=auth))[0] in {404, 422}, suffix
        for extra in [{'limit': True}, {'after': '0'}, {'after': None}, {'actor': OWNER.actor}]:
            with pytest.raises(OperationError):
                await d.call_api('pm_follow_up_assessments_list', {'workspace_id': wid, **extra}, READER)
        installation = {'adapter': 'codex-native-linux', 'eligible_for_preflight': True,
                        'options_confirmed': False, 'auth_validity': 'unverified', 'model_execution_verified': False}
        monkeypatch.setattr(pm_native_runtime, 'installation_eligibility', lambda: installation.copy())
        global_cap = (await http(port, 'GET', '/api/v1/capabilities', tok=auth))[1]['features']['pm_workspaces']
        assert global_cap['follow_up_analysis'] and global_cap['follow_up_prepare'] and not global_cap['runner']
        cap = await d.call_api('pm_capabilities_get', {'workspace_id': wid}, ANALYST)
        assert cap['follow_up_analysis'] == {**installation, 'action_registered': True, 'authorized': True,
            'authority': 'context_only', 'output_destination': 'follow_up_assessment',
            'conclusions_verified': False, 'execution_authorized': False}
        assert not (await d.call_api('pm_capabilities_get', {'workspace_id': wid}, READER))['follow_up_analysis']['authorized']
        profiles = await d.call_api('pm_profiles_get', {}, READER)
        assert 'follow_up' not in profiles['executable_modes']
        assert len(instances) == 1 and instances[0].sends == 1 and instances[0].closed
        assert pm_discussions.get_thread(d.ops, OWNER, wid, data['thread_id'])['messages'] == before_thread['messages']
        assert pm_plans.head(d.ops.db, wid) == before_plan and not write_frames(mock)
    finally:
        await fleet.close()


async def test_completed_and_pending_analysis_keep_original_sources_across_all_reads(served, monkeypatch):
    d, port, selection = served
    data = await setup(d, port, selection, private=True)
    _, instances, _ = install_runtime(d, monkeypatch)
    original = request(d, data)
    done = await send(d, port, original, ANALYST)
    aid, wid = done['result']['outcome']['assessment_id'], data['workspace_id']
    # Admission exists before the worker, run or assessment. No event-loop yield
    # between accepting this request and withdrawing its source.
    pending, _ = d.ops.create(ANALYST, **request(d, data))
    withdraw(d, data)
    auth = token(d, READER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    root = f'/api/v1/pm/workspaces/{wid}/follow-up-assessments'
    try:
        assert (await http(port, 'GET', root + '/' + aid, tok=auth))[0] == 404
        with pytest.raises(OperationError):
            await d.call_api('pm_follow_up_assessment_get', {'workspace_id': wid, 'assessment_id': aid}, READER)
        assert 'PM_FOLLOW_UP_SOURCE_UNAVAILABLE' in await call(server, 'pm_follow_up_assessment_get', {'workspace_id': wid, 'assessment_id': aid})
        for oid in [done['operation_id'], pending['operation_id']]:
            assert (await http(port, 'GET', '/api/v1/operations/' + oid, tok=auth))[0] == 404
            assert 'NOT_FOUND' in await call(server, 'operation_get', {'operation_id': oid})
        for method, params, path in [
            ('pm_follow_up_assessments_list', {'workspace_id': wid}, root),
            ('operations_list', {'action': 'pm.follow_up.analyze'}, '/api/v1/operations?action=pm.follow_up.analyze'),
            ('events_list', {'after': 0, 'limit': 500}, '/api/v1/events?after=0&limit=500'),
        ]:
            rpc = {'operations_list': 'op_list', 'events_list': 'api_events'}.get(method, method)
            response = (await http(port, 'GET', path, tok=auth))[1]
            for encoded in [json.dumps(response), json.dumps(await d.call_api(rpc, params.copy(), READER)), await call(server, method, params)]:
                assert aid not in encoded and SUMMARY not in encoded
                assert done['operation_id'] not in encoded and pending['operation_id'] not in encoded
        body = {key: value for key, value in original.items() if key != 'idempotency_key'}
        status, response = await http(port, 'POST', '/api/v1/operations', tok=token(d, ANALYST.actor, *ANALYST.scopes),
            body=body, headers={'Idempotency-Key': original['idempotency_key']})
        assert status == 404 and 'admission_refused' not in response['error'], response
        await settle_operations(d.ops)
        assert d.ops.get(done['operation_id'])['result'] == done['result']
        assert len(instances) == 1 and d.ops.db.execute('SELECT COUNT(*) FROM pm_follow_up_assessments').fetchone()[0] == 1
    finally:
        await fleet.close()


async def test_waited_assessment_http_rpc_mcp_hide_withdrawn_sources_without_new_send(served, monkeypatch):
    d, port, selection = served
    data = await setup(d, port, selection, private=True)
    _, instances, _ = install_runtime(d, monkeypatch)
    source_id = data['private_task']['task_id']
    original_authority = d.ops.db.execute('SELECT controller_authority FROM tasks WHERE task_id=?', (source_id,)).fetchone()[0]
    auth = token(d, ANALYST.actor, *ANALYST.scopes)
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, principal_only=True)

    async def revoke():
        withdraw(d, data)

    try:
        for surface in ['http', 'rpc', 'mcp']:
            original = request(d, data)
            op = await wait_and_withdraw(d, port, auth, server, surface, original, revoke, monkeypatch)
            body = {key: value for key, value in original.items() if key != 'idempotency_key'}
            status, response = await http(port, 'POST', '/api/v1/operations', tok=auth, body=body,
                headers={'Idempotency-Key': original['idempotency_key']})
            assert status == 404 and 'admission_refused' not in response['error'], response
            d.ops.db.execute('UPDATE tasks SET controller_authority=? WHERE task_id=?', (original_authority, source_id))
            replay, created = d.ops.create(ANALYST, **original)
            assert not created and replay['operation_id'] == op['operation_id'] and replay['result'] == op['result']
        assert len(instances) == 3 and all(runtime.sends == 1 and runtime.closed for runtime in instances)
        assert d.ops.db.execute('SELECT COUNT(*) FROM pm_follow_up_assessments').fetchone()[0] == 3
    finally:
        await fleet.close()


async def test_assessment_reads_require_current_workspace_member_and_observe(served, monkeypatch):
    d, port, selection = served
    data = await setup(d, port, selection)
    install_runtime(d, monkeypatch)
    op = await send(d, port, request(d, data), ANALYST)
    wid, aid = data['workspace_id'], op['result']['outcome']['assessment_id']
    root = f'/api/v1/pm/workspaces/{wid}/follow-up-assessments'
    auth = token(d, READER.actor, 'observe')
    await member(d.ops, {'workspace_id': wid}, READER.actor, None)
    for suffix in ['', '/' + aid]:
        assert (await http(port, 'GET', root + suffix, tok=auth))[0] == 404
    for method, fields in [('pm_follow_up_assessments_list', {}), ('pm_follow_up_assessment_get', {'assessment_id': aid})]:
        with pytest.raises(OperationError):
            await d.call_api(method, {'workspace_id': wid, **fields}, READER)
    assert (await http(port, 'GET', root, tok=token(d, OWNER.actor, 'manage')))[0] == 403
