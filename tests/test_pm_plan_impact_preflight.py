"""Public document lineage must be authorized before interpreting impact content."""
import asyncio
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import pm_plan_impact as impact
from bat_agent_connector import pm_plans, pm_workspace_adapter
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import token
from tests.test_pm_plan_impact import mapped as mapped
from tests.test_pm_plan_impact import pm as pm
from tests.test_pm_plan_impact import read
from tests.test_pm_plan_impact import ready as ready
from tests.test_pm_plan_impact import selected as selected
from tests.test_pm_plan_impact import start_ready as start_ready
from tests.test_pm_plan_impact import world as world
from tests.test_pm_plan_impact_proofs import contradict
from tests.test_pm_plan_impact_transport import METHOD, path
from tests.test_pm_plan_operations import OWNER, draft
from tests.test_pm_quality_assessment_transport import mcp
from tests.test_pm_snapshots import snapshot
from tests.test_work_items import act


async def source_edge(w, kind, identity):
    """Retained provenance between real independently authorized source records."""
    ops = w['d'].ops
    wid = (await act(w['d'], OWNER, 'pm.workspace.create', params={'name': 'Independent source'}))['result']['workspace']['workspace_id']
    source = await act(w['d'], OWNER, pm_workspace_adapter.ACTION, {'workspace_id': wid},
        {'adapter': pm_workspace_adapter.ADAPTERS[0], 'rationale': 'Original synthetic source'},
        pm_workspace_adapter.read(ops, OWNER, wid)['preconditions'])
    ops.db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)',
                   (kind, identity, source['operation_id'], source['operation_id']))
    return wid


def withdraw(w, wid):
    w['d'].ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (wid, OWNER.actor))


async def conflict_selector(w, conflict):
    if conflict == 'proof':
        contradict(w['d'].ops, 'pm.issue.recorded', 'issue', {'id': 999, 'number': 999})
        return w['impact_selector']
    later = await draft(w, requirements=w['requirements'], markdown='範' * 100000)
    snap = await snapshot(w['d'].ops, w['workspace_id'], who=OWNER)
    return w['impact_selector'] | {'to_revision_id': later['revision_id'], 'snapshot_id': snap['snapshot_id']}


async def wire(w, auth, method, url, body=None):
    """Return actual response bytes, preserving each adapter's error envelope."""
    data = b'' if body is None else json.dumps(body).encode()
    reader, writer = await asyncio.open_connection('127.0.0.1', w['port'])
    try:
        writer.write((f'{method} {url} HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {auth}\r\n'
                      f'Content-Length: {len(data)}\r\nConnection: close\r\n\r\n').encode() + data)
        await writer.drain()
        head = await reader.readuntil(b'\r\n\r\n')
        length = int(next(line.split(b':', 1)[1] for line in head.split(b'\r\n') if line.lower().startswith(b'content-length:')))
        return int(head.split(b' ')[1]), await reader.readexactly(length)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize('conflict', ['bytes', 'proof'])
async def test_impact_work_lineage_precedes_derived_errors_in_central_and_adapters(selected, monkeypatch, conflict):
    w = selected
    source = await source_edge(w, 'work_item', w['target']['work_item_id'])
    read(w, w['impact_selector'])
    withdraw(w, source)
    auth = token(w['d'], OWNER.actor, 'observe')
    server, fleet = mcp(w, monkeypatch, auth)
    async def errors(params):
        with pytest.raises(OperationError) as central:
            read(w, params)
        h = await wire(w, auth, 'GET', path(params))
        r = await wire(w, auth, 'POST', '/rpc', {'method': METHOD, 'params': params})
        with pytest.raises(ToolError) as tool:
            await server.call_tool(METHOD, params)
        return (central.value.status, central.value.code, central.value.message), h, r, str(tool.value).encode()
    try:
        baseline = await errors(w['impact_selector'])
        assert baseline[0][:2] == (404, 'PM_IMPACT_SOURCE_UNAVAILABLE')
        params = await conflict_selector(w, conflict)
        actual = await errors(params)
        assert actual == baseline
    finally:
        await fleet.close()


@pytest.mark.parametrize('conflict', ['bytes', 'proof'])
async def test_impact_work_lineage_stops_before_comparison_or_subject_builder(selected, monkeypatch, conflict):
    w = selected
    source = await source_edge(w, 'work_item', w['target']['work_item_id'])
    read(w, w['impact_selector'])
    params = await conflict_selector(w, conflict)
    withdraw(w, source)
    calls = []
    compare, subject = impact.diff.compare, impact._subject
    def comparison(*args, **kwargs):
        calls.append('comparison')
        return compare(*args, **kwargs)
    def builder(*args, **kwargs):
        calls.append('subject')
        return subject(*args, **kwargs)
    monkeypatch.setattr(impact.diff, 'compare', comparison)
    monkeypatch.setattr(impact, '_subject', builder)
    with pytest.raises(OperationError) as error:
        read(w, params)
    assert calls == []
    assert (error.value.status, error.value.code) == (404, 'PM_IMPACT_SOURCE_UNAVAILABLE')


@pytest.mark.parametrize('source', ['snapshot', 'from', 'to', 'binding', 'package', 'issue'])
@pytest.mark.parametrize('conflict', ['bytes', 'proof'])
async def test_impact_other_original_sources_dominate_real_bytes_and_proof(selected, monkeypatch, source, conflict):
    w, ops = selected, selected['d'].ops
    # Attach before corrupting evidence, then withdraw only after proving access.
    params = w['impact_selector']
    ids = {
        'snapshot': ops.db.execute('SELECT operation_id FROM pm_document_snapshots WHERE snapshot_id=?', (params['snapshot_id'],)).fetchone()[0],
        'from': pm_plans._revision(ops.db, w['workspace_id'], params['from_revision_id'])['operation_id'],
        'to': pm_plans._revision(ops.db, w['workspace_id'], params['to_revision_id'])['operation_id'],
        'binding': ops.db.execute('SELECT operation_id FROM pm_work_bindings WHERE binding_id=?', (params['binding_ids'][0],)).fetchone()[0],
        'package': w['package']['operation_id'], 'issue': w['publication']['operation_id']}
    # Bytes require a new selector: attach to its exact source, not the old one.
    if conflict == 'bytes':
        params = await conflict_selector(w, conflict)
        ids['snapshot'] = ops.db.execute('SELECT operation_id FROM pm_document_snapshots WHERE snapshot_id=?', (params['snapshot_id'],)).fetchone()[0]
        ids['to'] = pm_plans._revision(ops.db, w['workspace_id'], params['to_revision_id'])['operation_id']
    root = await source_edge(w, 'operation', ids[source])
    withdraw(w, root)
    if conflict == 'proof':
        params = await conflict_selector(w, conflict)
    with pytest.raises(OperationError) as error:
        read(w, params)
    assert (error.value.status, error.value.code) == (404, 'PM_IMPACT_SOURCE_UNAVAILABLE')
    auth = token(w['d'], OWNER.actor, 'observe')
    server, fleet = mcp(w, monkeypatch, auth)
    try:
        h = await wire(w, auth, 'GET', path(params))
        r = await wire(w, auth, 'POST', '/rpc', {'method': METHOD, 'params': params})
        assert h[0] == 404
        assert json.loads(h[1]) == {'error': {'code': error.value.code, 'message': error.value.message}}
        assert json.loads(r[1]) == {'error': error.value.code, 'message': error.value.message}
        with pytest.raises(ToolError) as tool:
            await server.call_tool(METHOD, params)
        assert error.value.code in str(tool.value) and error.value.message in str(tool.value)
    finally:
        await fleet.close()


async def test_impact_every_response_reference_has_a_preflight_lineage_check(selected, monkeypatch):
    from bat_agent_connector import pm_source_lineage
    w = selected
    phase = ['preflight']
    authorized = set()
    guard, compare = pm_source_lineage.require_document, impact.diff.compare
    def checked(db, principal, document):
        guard(db, principal, document)
        if phase[0] == 'preflight':
            authorized.update(pm_source_lineage.references(document))
    def compared(*args, **kwargs):
        phase[0] = 'derived'
        return compare(*args, **kwargs)
    monkeypatch.setattr(pm_source_lineage, 'require_document', checked)
    monkeypatch.setattr(impact.diff, 'compare', compared)
    result = read(w, w['impact_selector'])
    assert set(pm_source_lineage.references(result)) <= authorized


async def large_comparison_selector(w):
    """Reach the comparison's own 2 MB bound using valid source documents/bindings."""
    from bat_agent_connector import pm_evidence, work_items
    from tests.test_pm_plan_operations import act as thread_action
    from tests.test_pm_plan_operations import adopt
    from tests.test_pm_work_packages import action
    ops = w['d'].ops
    scope_id = w['requirements'][0]['source_input_id']
    async def revision(text):
        acceptance = await thread_action(w, 'pm.input.record', {'slot': 'quality', 'value': text * 4000,
            'status': 'user_confirmed', 'source_ref': {'kind': 'statement', 'ref': 'Synthetic acceptance'}})
        thread = ops.db.execute('SELECT version,draft_id FROM pm_threads WHERE workspace_id=?', (w['workspace_id'],)).fetchone()
        await thread_action(w, 'pm.thread.plan.revise', {'selected_input_ids': [scope_id, acceptance['input_id']],
            'rationale': 'Prepare explicit bounded comparison sources'},
            pre={'expected_version': thread['version'], 'expected_draft_id': thread['draft_id']})
        requirements = [{'id': f'requirement.{i:02d}.' + 'x' * 65, 'source_input_id': scope_id,
                         'acceptance_input_ids': [acceptance['input_id']]} for i in range(100)]
        return await draft(w, requirements=requirements, markdown='Fixed structured proposal'), requirements
    before, requirements = await revision('A')
    done = await adopt(w, before)
    assert done['status'] == 'succeeded', done
    item = work_items._get_item(ops.db, w['target']['work_item_id'])
    bindings = []
    for i in range(50):
        if i:
            work = await action(w, 'work_item.create', {'project_id': item['project_id']},
                                {'title': f'Comparison Work {i}', 'goal': 'Synthetic bounded comparison'})
            identity = work['work_item_id']
        else:
            identity = item['work_item_id']
        target = {'workspace_id': w['workspace_id'], 'work_item_id': identity}
        old = pm_evidence._binding(ops.db, **target)
        binding = await action(w, 'pm.work.bind', target,
            {'plan_revision_id': before['revision_id'], 'requirement_ids': [r['id'] for r in requirements],
             'rationale': '範' * 4000},
            {'expected_binding_id': old['binding_id'] if old else None,
             'expected_work_version': work_items._get_item(ops.db, identity)['version']})
        bindings.append(binding['binding_id'])
    after, _ = await revision('B')
    snap = await snapshot(ops, w['workspace_id'], who=OWNER)
    params = {'workspace_id': w['workspace_id'], 'snapshot_id': snap['snapshot_id'],
              'from_revision_id': before['revision_id'], 'to_revision_id': after['revision_id'], 'binding_ids': bindings}
    with pytest.raises(OperationError, match='PM_DIFF_TOO_LARGE'):
        impact.diff.compare(ops, OWNER, w['workspace_id'], before['revision_id'], after['revision_id'],
                            binding_ids=bindings, journal_cursor=snap['journal_cursor'])
    return params


async def test_impact_work_lineage_precedes_real_comparison_overflow(selected, monkeypatch):
    w = selected
    params = await large_comparison_selector(w)
    root = await source_edge(w, 'work_item', w['target']['work_item_id'])
    read(w, w['impact_selector'])
    withdraw(w, root)
    with pytest.raises(OperationError) as central:
        read(w, params)
    auth = token(w['d'], OWNER.actor, 'observe')
    server, fleet = mcp(w, monkeypatch, auth)
    try:
        h = await wire(w, auth, 'GET', path(params))
        r = await wire(w, auth, 'POST', '/rpc', {'method': METHOD, 'params': params})
        with pytest.raises(ToolError) as tool:
            await server.call_tool(METHOD, params)
        assert central.value.code == 'PM_IMPACT_SOURCE_UNAVAILABLE' and central.value.status == h[0] == 404
        assert json.loads(h[1]) == {'error': {'code': central.value.code, 'message': central.value.message}}
        assert json.loads(r[1]) == {'error': central.value.code, 'message': central.value.message}
        assert central.value.code in str(tool.value) and central.value.message in str(tool.value)
    finally:
        await fleet.close()


@pytest.mark.parametrize('source', ['attempt', 'execution'])
@pytest.mark.parametrize('conflict', ['bytes', 'proof'])
async def test_impact_attempt_sources_dominate_real_bytes_and_proof(start_ready, monkeypatch, source, conflict):
    from tests import test_pm_work_start as starting
    from tests.test_pm_plan_impact import selector
    w, ops = start_ready, start_ready['d'].ops
    op, _ = ops.create(starting.STARTER_OWNER, **await starting._start_envelope(w))
    done = await starting._advance_start(ops, op['operation_id'])
    assert done['status'] == 'succeeded', done
    w['impact_selector'] = await selector(w)
    matrix = read(w, w['impact_selector'])['matrix']
    identity = next(s['operation_id'] for s in matrix['subjects'] if s['kind'] == source)
    root = await source_edge(w, 'operation', identity)
    read(w, w['impact_selector'])
    if conflict == 'bytes':
        params = await conflict_selector(w, conflict)
    else:
        contradict(ops, 'pm.work_package.created', 'execution_authorized', True)
        params = w['impact_selector']
    withdraw(w, root)
    with pytest.raises(OperationError) as error:
        read(w, params)
    assert (error.value.status, error.value.code) == (404, 'PM_IMPACT_SOURCE_UNAVAILABLE')
    auth = token(w['d'], OWNER.actor, 'observe')
    server, fleet = mcp(w, monkeypatch, auth)
    try:
        h = await wire(w, auth, 'GET', path(params))
        r = await wire(w, auth, 'POST', '/rpc', {'method': METHOD, 'params': params})
        assert h[0] == 404
        assert json.loads(h[1]) == {'error': {'code': error.value.code, 'message': error.value.message}}
        assert json.loads(r[1]) == {'error': error.value.code, 'message': error.value.message}
        with pytest.raises(ToolError) as tool:
            await server.call_tool(METHOD, params)
        assert error.value.code in str(tool.value) and error.value.message in str(tool.value)
    finally:
        await fleet.close()


async def test_impact_attempt_and_execution_references_are_preflighted(start_ready, monkeypatch):
    from tests import test_pm_work_start as starting
    from tests.test_pm_plan_impact import selector
    w, ops = start_ready, start_ready['d'].ops
    op, _ = ops.create(starting.STARTER_OWNER, **await starting._start_envelope(w))
    done = await starting._advance_start(ops, op['operation_id'])
    assert done['status'] == 'succeeded', done
    w['impact_selector'] = await selector(w)
    await test_impact_every_response_reference_has_a_preflight_lineage_check(w, monkeypatch)
