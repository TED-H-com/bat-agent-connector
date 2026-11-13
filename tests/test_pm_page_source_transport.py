"""Real HTTP/daemon read barriers with independent persisted source roots."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bat_agent_connector import api_auth, pm_discussions, pm_runs, pm_workspace
from bat_agent_connector import pm_source_lineage as lineage
from bat_agent_connector import pm_workspace_adapter as adapter
from bat_agent_connector.operations import OperationError
from tests.pm_page_source_reference import namespace
from tests.test_api_v1 import daemon as daemon  # noqa: F401
from tests.test_api_v1 import http, token
from tests.test_api_v1 import served as served  # noqa: F401
from tests.test_work_items import act

OWNER = api_auth.Principal('page-fixture-owner', frozenset({'observe', 'manage', 'start', 'operate'}))


@pytest.fixture
async def pages(served, request):
    d, port = served
    project = (await act(d, OWNER, 'project.create', params={'name': 'Bootstrap source fixture'}))['result']['project_id']
    work = []
    for i in range(2 if getattr(request, 'param', None) == 'early_failure' else 1):
        item = await act(d, OWNER, 'work_item.create', {'project_id': project}, {'title': f'Source group {i}'})
        work.append(item['result']['work_item_id'])
    originals, workspaces = [], []
    for i in range(66):
        workspace = await act(d, OWNER, 'pm.workspace.create', params={'name': f'Page fixture {i}'})
        wid = workspace['result']['workspace']['workspace_id']
        operation = await act(d, OWNER, adapter.ACTION, {'workspace_id': wid},
            {'adapter': adapter.ADAPTERS[0], 'rationale': 'PRIVATE-SENTINEL' if i == 0 else f'Independent source {i}'},
            adapter.read(d.ops, OWNER, wid)['preconditions'])
        originals.append(operation['operation_id'])
        workspaces.append(wid)
    # Exercise real run creation/projection, without starting a model. Existing
    # adapter operation IDs provide synthetic, independently readable lineage.
    workspace = (await act(d, OWNER, 'pm.workspace.create', params={'name': 'Run projections'}))['result']['workspace']
    wid = workspace['workspace_id']
    thread = (await act(d, OWNER, 'pm.thread.create', {'workspace_id': wid},
        {'title': 'Run projection fixture'}, {'expected_version': workspace['version']}))['result']
    tid = thread['thread_id']
    for oid in originals:
        authority = {**pm_runs.execution_identity(d.ops.db, wid),
            'server_id': pm_workspace.server_id(d.ops.db), 'scopes': sorted(OWNER.scopes), 'admin': False,
            'pm_workspace_id': wid, 'pm_operation_id': oid, 'pm_requester': pm_workspace.principal_binding(OWNER)}
        run = pm_runs.create(d.ops, OWNER, operation_id=oid, workspace_id=wid, thread_id=tid,
            expected_version=pm_discussions._thread(d.ops.db, wid, tid)['version'],
            adapter='bat.codex', purpose='Fixture only; no provider execution', execution_authority=authority)
        pm_runs.fail(d.journal, run['run_id'], run['generation'], 'Fixture ends before provider execution', before_send=True)
    # Keep the real 50-row bootstrap limit. WorkItems predate the operation
    # page and carry the roots absent from that page, so each part stays <=64
    # and only the outer bootstrap union reaches 66. The separate early-failure
    # fixture deliberately puts all 66 roots in the first source-bearing part.
    operation_roots = set()
    for operation in d.ops.list(limit=50, principal=OWNER)['operations']:
        operation_roots.update(lineage.document_roots(d.ops.db, operation))
    groups = [sorted(set(originals) - operation_roots)] if len(work) == 1 else [originals[:33], originals[33:]]
    with d.journal.tx():
        for item, group in zip(work, groups):
            for oid in group:
                d.ops.db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)', ('work_item', item, oid, oid))
    return d, port, wid, originals, workspaces, work


async def request_page(pages, monkeypatch, shape, transport, principal, credential):
    d, port, wid, *_ = pages
    options = {
        'operations': ('/api/v1/operations?action=pm.workspace.adapter.select&limit=200', 'op_list', {'action': adapter.ACTION, 'limit': 200}),
        'events': ('/api/v1/events?limit=500&kind=pm.workspace.adapter.select', 'api_events', {'kind': adapter.ACTION, 'limit': 500}),
        'runs': (f'/api/v1/pm/workspaces/{wid}/runs?limit=100', 'pm_runs_list', {'workspace_id': wid, 'limit': 100}),
        'bootstrap': ('/api/v1/bootstrap', 'inventory_hosts', {}),
    }
    path, method, params = options[shape]
    if transport == 'http':
        return await http(port, 'GET', path, tok=credential)
    if shape == 'bootstrap':
        # There is no bootstrap RPC on main. This test-only adapter feeds the
        # actual HTTP handler's bootstrap into the unchanged call_api barrier;
        # it does not add a production method/capability or bypass that barrier.
        async def bootstrap_adapter(method, params, principal, **kwargs):
            return (await d.api.bootstrap(principal))[1]
        monkeypatch.setattr(d, '_call_api_bound', bootstrap_adapter)
    try:
        return 200, await d.call_api(method, dict(params), principal)
    except OperationError as error:
        return error.status, {'error': {'code': error.code, 'message': error.message}}


@pytest.mark.parametrize('shape', ['operations', 'events', 'runs', 'bootstrap'])
@pytest.mark.parametrize('transport', ['http', 'call_api'])
@pytest.mark.parametrize('role', ['member', 'admin'])
async def test_66_sources_pass_real_read_barriers(pages, monkeypatch, shape, transport, role):
    d, _, _, originals, _, work = pages
    credential = token(d, OWNER.actor, *OWNER.scopes) if role == 'member' else d._admin_token
    principal = api_auth.authenticate(d.ops.db, credential, d._admin_token)
    current, original_visible = lineage.read_response, lineage._visible_roots
    counts = []

    def counted(db, principal, identities):
        counts.extend(identities)
        return original_visible(db, principal, identities)

    monkeypatch.setattr(lineage, '_visible_roots', counted)
    monkeypatch.setattr(lineage, 'read_response', namespace()['read_response'])
    before_status, before = await request_page(pages, monkeypatch, shape, transport, principal, credential)
    before_count = len(counts)
    assert before_status == 409 and before['error'] == {'code': 'PM_SOURCE_LINEAGE_LIMIT', 'message': 'Too many original sources for one document'}
    counts.clear()
    monkeypatch.setattr(lineage, 'read_response', current)
    status, body = await request_page(pages, monkeypatch, shape, transport, principal, credential)
    after_count = len(counts)
    assert status == 200, body
    if shape == 'bootstrap':
        assert {r['work_item_id'] for r in body['snapshot']['work_items']['work_items']} == set(work)
        found = set()
        for part in body['snapshot'].values():
            for key in lineage._COLLECTIONS:
                for item in part.get(key, []) if isinstance(part, dict) else []:
                    found.update(lineage.document_roots(d.ops.db, item))
        assert found == set(originals)
    else:
        assert len(body[shape]) == 66
        if shape != 'events':
            assert {r['operation_id'] for r in body[shape]} == set(originals)
    if shape == 'runs':
        assert (before_count, after_count) == (66, 132)
    elif shape in {'operations', 'events'}:
        assert (before_count, after_count) == (132, 132)
    else:
        assert after_count <= before_count
    if os.environ.get('BAC_EVIDENCE'):
        directory = Path(os.environ['BAC_EVIDENCE']) / 'r1'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'cost-{shape}-{transport}-{role}.json').write_text(json.dumps({
            'shape': shape, 'transport': transport, 'role': role, 'before': before_count, 'after': after_count,
            'before_status': before_status, 'after_status': status}, indent=2) + '\n')


@pytest.mark.parametrize('shape', ['operations', 'events', 'runs', 'bootstrap'])
@pytest.mark.parametrize('transport', ['http', 'call_api'])
async def test_withdrawal_after_handler_is_enforced(pages, monkeypatch, shape, transport):
    d, _, _, originals, workspaces, _ = pages
    credential = token(d, OWNER.actor, *OWNER.scopes)
    principal = api_auth.authenticate(d.ops.db, credential, d._admin_token)
    barrier = lineage.read_response
    seen = []

    def withdraw(db, principal, value):
        seen.append(value)
        with d.journal.tx():
            db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (workspaces[0], OWNER.actor))
        return barrier(db, principal, value)

    monkeypatch.setattr(lineage, 'read_response', withdraw)
    status, body = await request_page(pages, monkeypatch, shape, transport, principal, credential)
    assert seen and status == 200, body
    assert originals[0] not in json.dumps(body) and 'PRIVATE-SENTINEL' not in json.dumps(body)
    if shape == 'bootstrap':
        assert len(seen[0]['snapshot']['work_items']['work_items']) == 1
        assert len(body['snapshot']['work_items']['work_items']) == 0
        assert body['snapshot']['work_items']['source_visibility_filtered']
    else:
        assert len(seen[0][shape]) == 66
        assert len(body[shape]) == 65 and body['source_visibility_filtered']
