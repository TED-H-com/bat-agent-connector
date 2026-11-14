"""Public shared reads conceal private freezes and withdrawn reports across restart."""
import copy
import json

import pytest

from bat_agent_connector import pm_runs, pm_source_lineage
from bat_agent_connector.task_daemon import TaskDaemon
from tests.operation_helpers import settle_operations
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
from tests.test_pm_roadmap_report_materialization import request as save_request
from tests.test_pm_roadmap_report_materialization_api import GET
from tests.test_pm_roadmap_report_materialization_visibility import withdraw
from tests.test_pm_run_api import unpack

fixed = fixed_fixture
SENTINELS = ('PRIVATE-REPORT-CONTENT-52', 'PRIVATE-MANIFEST-BODY-83',
    'PRIVATE-FILE-MAP-KEY-07', 'PRIVATE-SOURCE-MANIFEST-91')


def poison_private_freeze(ops, operation_id):
    frozen = json.loads(ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? "
        "AND name='pm.roadmap.report.freeze'", (operation_id,)).fetchone()[0])
    files = frozen['revision']['files']
    files[next(path for path in files if '/report.' in path)] += SENTINELS[0]
    manifest_path = next(path for path in files if path.endswith('/manifest.json'))
    manifest = json.loads(files[manifest_path])
    manifest['path'] = SENTINELS[1]
    files[manifest_path] = json.dumps(manifest)
    files[SENTINELS[2]] = 'private map entry'
    frozen['report']['path'] = SENTINELS[1]
    frozen['source_manifest'].append({'path': SENTINELS[3], 'count': 777})
    raw = json.dumps(frozen)
    ops.db.execute("UPDATE operation_steps SET response=? WHERE operation_id=? "
        "AND name='pm.roadmap.report.freeze'", (raw, operation_id))
    return raw


def concealed(values, saved, *, hidden):
    encoded = json.dumps(values, ensure_ascii=False)
    assert all(sentinel not in encoded for sentinel in SENTINELS)
    if hidden:
        assert saved['result']['materialization_id'] not in encoded
        assert saved['result']['output_digest'] not in encoded
        assert 'docs/pm/reports/' not in encoded


async def packets(read, credential, path, method, tool, params, *, hidden=False):
    status, http_body = await http(read.port, 'GET', path, tok=credential)
    rpc_status, rpc = await http(read.port, 'POST', '/rpc', tok=credential,
        body={'method': method, 'params': params})
    if hidden:
        assert status == 404 and rpc_status == 400, (http_body, rpc)
        mcp = await call(read.server, tool, params)
        assert any(code in mcp for code in ('NOT_FOUND', 'PM_RUN_NOT_FOUND')), mcp
        return [http_body, rpc, mcp]
    assert status == rpc_status == 200, (http_body, rpc)
    mcp = unpack(await read.server.call_tool(tool, params))
    if method == 'op_get':
        assert http_body['operation'] == rpc['result']['operation'] == mcp['operation']
        assert http_body['work_items'] == http_body['cleanup_receipts'] == []
    else:
        assert http_body == rpc['result'] == mcp
    return [http_body, rpc, mcp]


async def shared_reads(w, saved, monkeypatch, *, hidden):
    d, wid = w['d'], w['workspace_id']
    oid, mid = saved['operation_id'], saved['result']['materialization_id']
    run = pm_runs.get_by_operation(d.ops.db, oid)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    async with transports(w, monkeypatch) as read:
        for role, credential in (('member', auth), ('admin', d._admin_token)):
            monkeypatch.setenv('BATC_API_TOKEN', credential)
            paths = [
                ('/api/v1/operations?action=pm.roadmap.report.materialize', 'op_list', 'operations_list',
                    {'action': 'pm.roadmap.report.materialize'}, False),
                ('/api/v1/events?limit=500', 'api_events', 'events_list', {'limit': 500}, False),
                (f'/api/v1/pm/workspaces/{wid}/runs', 'pm_runs_list', 'pm_runs_list', {'workspace_id': wid}, False),
                (f'/api/v1/pm/workspaces/{wid}/roadmap-report-materializations', RECEIPTS, RECEIPTS,
                    {'workspace_id': wid}, False),
                (f'/api/v1/operations/{oid}', 'op_get', 'operation_get', {'operation_id': oid}, hidden),
                (f'/api/v1/pm/workspaces/{wid}/runs/{run["run_id"]}', 'pm_run_get', 'pm_run_get',
                    {'workspace_id': wid, 'run_id': run['run_id']}, hidden),
                (f'/api/v1/pm/workspaces/{wid}/roadmap-report-materializations/{mid}', GET, GET,
                    {'workspace_id': wid, 'materialization_id': mid}, hidden),
                (f'/api/v1/pm/workspaces/{wid}/threads', 'pm_threads_list', 'pm_threads_list',
                    {'workspace_id': wid}, False),
                (f'/api/v1/pm/workspaces/{wid}/threads/{run["thread_id"]}', 'pm_thread_get', 'pm_thread_get',
                    {'workspace_id': wid, 'thread_id': run['thread_id']}, False),
                (f'/api/v1/pm/workspaces/{wid}/threads/{run["thread_id"]}/context', 'pm_context_get', 'pm_context_get',
                    {'workspace_id': wid, 'thread_id': run['thread_id']}, False),
            ]
            for path, method, tool, params, missing in paths:
                values = await packets(read, credential, path, method, tool, params, hidden=missing)
                concealed(values, saved, hidden=hidden)
                if method == RECEIPTS:
                    assert len(values[0]['materializations']) == (0 if hidden else 1), role


async def test_public_sentinels_and_parity_before_after_restart(fixed, monkeypatch):
    w, d = fixed, fixed['d']
    saved = await materialize(w)
    raw = poison_private_freeze(d.ops, saved['operation_id'])
    authority = d.ops.db.execute('SELECT controller_authority FROM tasks WHERE task_id=?',
        (w['source_task']['task_id'],)).fetchone()[0]
    for restarted in (False, True):
        await shared_reads(w, saved, monkeypatch, hidden=False)
        withdraw(w)
        await shared_reads(w, saved, monkeypatch, hidden=True)
        assert w['d'].ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? "
            "AND name='pm.roadmap.report.freeze'", (saved['operation_id'],)).fetchone()[0] == raw
        w['d'].ops.db.execute('UPDATE tasks SET controller_authority=? WHERE task_id=?',
            (authority, w['source_task']['task_id']))
        if not restarted:
            path = d.ops.db.execute('PRAGMA database_list').fetchone()['file']
            config = d.fleet.config
            w['server'].close()
            await w['server'].wait_closed()
            await d.fleet.close()
            await d.inventory.close()
            d.journal.close()
            w['d'] = TaskDaemon(config, path)


@pytest.mark.parametrize('transport', ['http', 'rpc', 'mcp'])
async def test_public_save_wait_and_replay_recheck_original_sources(fixed, monkeypatch, transport):
    w, d = fixed, fixed['d']
    auth = token(d, OWNER.actor, *OWNER.scopes)
    envelope = save_request(w)
    async with transports(w, monkeypatch, read_only=False) as read:
        monkeypatch.setenv('BATC_API_TOKEN', auth)
        status, accepted = await http(read.port, 'POST', '/api/v1/operations', tok=auth, body=envelope)
        assert status == 202 and accepted['created'] is True
        await settle_operations(d.ops)
        saved = d.ops.get(accepted['operation']['operation_id'])
        assert saved['status'] == 'succeeded'
        poison_private_freeze(d.ops, saved['operation_id'])
        changes = d.ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
        phases = list(w['runner'].phases)
        original_wait = d.ops.wait
        async def wait_then_withdraw(*args, **kwargs):
            await original_wait(*args, **kwargs)
            withdraw(w)
        with monkeypatch.context() as patch:
            patch.setattr(d.ops, 'wait', wait_then_withdraw)
            if transport == 'http':
                status, value = await http(read.port, 'POST', '/api/v1/operations?wait=1', tok=auth, body=envelope)
                assert status == 404 and 'admission_refused' not in value['error']
            elif transport == 'rpc':
                status, value = await http(read.port, 'POST', '/rpc', tok=auth,
                    body={'method': 'op_submit', 'params': {**envelope, 'wait_s': 1}})
                assert status == 400 and value['error'] == 'NOT_FOUND'
            else:
                value = await call(read.server, 'operation_submit', {**envelope, 'wait_s': 1, 'confirm': True})
                assert 'NOT_FOUND' in value
            concealed(value, saved, hidden=True)
        # Changed intent still conceals the original source before conflict diagnostics.
        changed = copy.deepcopy(envelope)
        changed['params']['locale'] = 'zh-TW'
        for original in (envelope, changed):
            status, value = await http(read.port, 'POST', '/api/v1/operations', tok=auth, body=original)
            assert status == 404 and 'admission_refused' not in value['error']
            concealed(value, saved, hidden=True)
            status, value = await http(read.port, 'POST', '/rpc', tok=auth,
                body={'method': 'op_submit', 'params': {**original, 'wait_s': 0}})
            assert status == 400 and value['error'] == 'NOT_FOUND'
            concealed(value, saved, hidden=True)
            value = await call(read.server, 'operation_submit', {**original, 'wait_s': 0, 'confirm': True})
            assert 'NOT_FOUND' in value
            concealed(value, saved, hidden=True)
        assert d.ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == changes
        assert w['runner'].phases == phases


@pytest.mark.parametrize('transport', ['http', 'rpc', 'mcp'])
@pytest.mark.parametrize('reader', ['operations', 'events', 'runs', 'receipts'])
async def test_report_withdrawal_between_handler_and_final_barrier(fixed, monkeypatch, transport, reader):
    w, d = fixed, fixed['d']
    saved = await materialize(w)
    auth = token(d, OWNER.actor, *OWNER.scopes)
    original = pm_source_lineage.read_response
    seen = []
    def withdraw_before_barrier(db, principal, value):
        seen.append(json.dumps(value))
        withdraw(w)
        return original(db, principal, value)
    monkeypatch.setattr(pm_source_lineage, 'read_response', withdraw_before_barrier)
    params = {'operations': {'action': 'pm.roadmap.report.materialize'}, 'events': {'limit': 500},
        'runs': {'workspace_id': w['workspace_id']}, 'receipts': {'workspace_id': w['workspace_id']}}[reader]
    async with transports(w, monkeypatch) as read:
        status, value = await read(transport, reader, auth, params)
        assert status == (0 if transport == 'mcp' else 200), value
        assert saved['result']['materialization_id'] in seen[0]
        concealed(value, saved, hidden=True)
