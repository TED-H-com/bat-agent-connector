"""Synthetic saved-report journals and complete mock transport requests."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from bat_agent_connector import mcp_server, pm_source_lineage
from bat_agent_connector import pm_roadmap_report_materialization as reports
from tests.test_api_v1 import http
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_roadmap import revision
from tests.test_pm_roadmap_report_cost import insert
from tests.test_pm_roadmap_report_materialization import materialize, request
from tests.test_pm_run_api import unpack

RECEIPTS = 'pm_roadmap_report_materializations_list'
EVENT_KIND = 'fixture.report.completed'


async def report_rows(w, *, distinct, count):
    """One real mock-carrier save, actual observations, synthetic immutable copies.

    No extra Git writes or model calls. The copies retain canonical admission,
    freeze, run and receipt relationships; every observation uses the real loader.
    """
    ops, wid = w['d'].ops, w['workspace_id']
    db = ops.db
    first = await materialize(w)
    original = w['observation']
    selected = []
    for index in range(distinct):
        if index:
            w['observation'] = await revision(ops, wid, 'observation', who=OWNER,
                snapshot_id=original['document']['snapshot_id'])
        envelope = request(w)
        source = reports.pm_roadmap_report_sources.load_fixed(ops, OWNER, **envelope['target'])
        rendered = reports.pm_roadmap_reports.render_fixed(source, **envelope['params'])
        selected.append((envelope, source, rendered))
    base = {table: dict(db.execute('SELECT * FROM ' + table + ' WHERE operation_id=?',
        (first['operation_id'],)).fetchone()) for table in ('operations', 'pm_runs', reports.TABLE)}
    run = base['pm_runs']
    state = dict(db.execute('SELECT * FROM pm_run_state WHERE run_id=?', (run['run_id'],)).fetchone())
    steps = [dict(db.execute('SELECT * FROM operation_steps WHERE operation_id=? AND name=?',
        (first['operation_id'], name)).fetchone()) for name in ('pm.roadmap.report.freeze', 'pm.revision.commit')]
    with ops.journal.tx():
        for index in range(1, count):
            envelope, source, rendered = selected[index % distinct]
            oid, rid, mid = ('op_' + f'{index:032x}', 'pmr_' + f'{index:032x}', 'pmrpt_' + f'{index:032x}')
            replacements = ((first['operation_id'], oid), (run['run_id'], rid),
                (first['result']['materialization_id'], mid))
            def replace(value):
                value = copy.deepcopy(value)
                for key, text in value.items():
                    if isinstance(text, str):
                        for old, new in replacements:
                            text = text.replace(old, new)
                        value[key] = text
                return value
            op, r, s, rec, freeze, step = map(replace,
                (base['operations'], run, state, base[reports.TABLE], *steps))
            op.update(idem_key='transport-' + oid, target=json.dumps(envelope['target']),
                preconditions=json.dumps(envelope['preconditions']))
            r['generation'] += index
            refs = json.loads(op['external_refs'])
            refs['admission_binding']['pm_roadmap_report_materialization'].update(**envelope['target'],
                **{key: rendered[key] for key in ('source_set_digest', 'view_digest', 'output_digest')},
                operation_ids=reports._operation_ids(db, source))
            op['external_refs'] = json.dumps(refs)
            files = reports.pm_roadmap_report_files.files(rendered, source['source_manifest'], oid)
            frozen = json.loads(freeze['response'])
            frozen.update(report={key: rendered[key] for key in reports.pm_revision_paths.REPORT_METADATA},
                source_manifest=source['source_manifest'], revision=files)
            freeze['response'] = json.dumps(frozen)
            receipt = json.loads(rec['git_receipt'])
            receipt.update({key: files[key] for key in ('revision_id', 'file_digests', 'document_digest',
                *reports.pm_revision_paths.REPORT_FIELDS)})
            rec.pop('sequence')
            rec.update({key: rendered[key] for key in reports.FIELDS}, git_receipt=json.dumps(receipt))
            intent = json.loads(step['request'])
            intent.update({key: value for key, value in files.items() if key != 'files'})
            step.update(request=json.dumps(intent), response=json.dumps(receipt))
            result = reports._result({**rec, 'git_receipt': receipt})
            op['result'], s['outcome'] = json.dumps(result), json.dumps(result)
            for table, value in (('operations', op), ('pm_runs', r), ('pm_run_state', s),
                    (reports.TABLE, rec), ('operation_steps', freeze), ('operation_steps', step)):
                insert(db, table, value)
            pm_source_lineage.bind_operation(db, oid, envelope['target'], envelope['params'], refs['admission_binding'])
    ids = {row[0] for row in db.execute('SELECT operation_id FROM operations WHERE action=?', (reports.ACTION,))}
    cursor = ops.journal.api_head()
    for oid in sorted(ids):
        ops.journal.api_event('operation', oid, EVENT_KIND, {'operation_id': oid}, actor=OWNER.actor)
    return first, ids, cursor


@asynccontextmanager
async def transports(w, monkeypatch, *, read_only=True):
    """Use the registered HTTP/RPC/MCP reads and both real transport barriers."""
    d, wid = w['d'], w['workspace_id']
    listener = await asyncio.start_server(d._handle, '127.0.0.1', int(os.environ.get('BATC_UI_TEST_PORT', '0')))
    port = listener.sockets[0].getsockname()[1]
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=read_only, principal_only=True)
    paths = {'operations': '/api/v1/operations', 'events': '/api/v1/events',
        'runs': f'/api/v1/pm/workspaces/{wid}/runs', 'receipts': f'/api/v1/pm/workspaces/{wid}/roadmap-report-materializations'}
    methods = {'operations': ('op_list', 'operations_list'), 'events': ('api_events', 'events_list'),
        'runs': ('pm_runs_list', 'pm_runs_list'), 'receipts': (RECEIPTS, RECEIPTS)}
    async def read(transport, reader, auth, params):
        monkeypatch.setenv('BATC_API_TOKEN', auth)
        method, tool = methods[reader]
        if transport == 'http':
            status, value = await http(port, 'GET', paths[reader] + '?' + urlencode(
                {key: value for key, value in params.items() if key != 'workspace_id'}), tok=auth)
            return status, value
        if transport == 'rpc':
            status, value = await http(port, 'POST', '/rpc', tok=auth,
                body={'method': method, 'params': params})
            return status, value.get('result', value)
        try:
            result = unpack(await server.call_tool(tool, params))
        except mcp_server.ToolError as error:
            if 'NOT_FOUND' not in str(error):
                raise
            return 1, {'error': str(error)}
        return (1 if 'error' in result else 0), result
    read.server = server
    read.port = port
    try:
        yield read
    finally:
        listener.close()
        await listener.wait_closed()
        await fleet.close()
