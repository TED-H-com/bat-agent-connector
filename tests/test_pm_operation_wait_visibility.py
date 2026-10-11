"""A waited submission is a read boundary, not permission to disclose revoked PM data."""
import asyncio
import json
import uuid

import pytest

from bat_agent_connector import controller_authority, mcp_server, pm_evidence, pm_workspace
from bat_agent_connector.operations import ActionDef, OperationError
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import daemon as daemon_fixture
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as served_fixture
from tests.test_mcp_principal import call
from tests.test_pm_discussions import CONTRIBUTOR, OWNER, member, prepared
from tests.test_pm_evidence import capture, facts, link, task
from tests.test_pm_evidence import prepared as evidence_prepared

served, daemon = served_fixture, daemon_fixture


async def submit(surface, d, port, auth, server, request):
    body = {key: value for key, value in request.items() if key != 'idempotency_key'}
    if surface == 'http':
        return await http(port, 'POST', '/api/v1/operations?wait=5', tok=auth, body=body,
                          headers={'Idempotency-Key': request['idempotency_key']})
    if surface == 'rpc':
        return await http(port, 'POST', '/rpc', tok=auth,
                          body={'method': 'op_submit', 'params': {**request, 'wait_s': 5}})
    return None, await call(server, 'operation_submit', {**request, 'wait_s': 5, 'confirm': True})


async def wait_and_withdraw(d, port, auth, server, surface, request, withdraw, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original_wait, captured = d.ops.wait, {}

    async def held_wait(operation_id, timeout):
        # Hold only the public wait. The real worker/settlement and its journal
        # outcome remain unchanged; internal waits still use the original method.
        if not entered.is_set():
            captured['operation_id'] = operation_id
            entered.set()
            await release.wait()
        return await original_wait(operation_id, timeout)

    with monkeypatch.context() as patch:
        patch.setattr(d.ops, 'wait', held_wait)
        pending = asyncio.create_task(submit(surface, d, port, auth, server, request))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await settle_operations(d.ops)
            before = d.ops.get(captured['operation_id'])
            assert before['status'] == 'succeeded', before
            await withdraw()
            release.set()
            status, response = await asyncio.wait_for(pending, 10)
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    encoded = json.dumps(response) if not isinstance(response, str) else response
    assert 'NOT_FOUND' in encoded, response
    assert 'admission_refused' not in encoded, response
    assert 'operation_id' not in encoded and 'params' not in encoded and 'external_refs' not in encoded
    if surface == 'http':
        assert status == 404
    elif surface == 'rpc':
        assert status == 400  # Existing RPC errors encode the domain code in the body.
    after = d.ops.get(captured['operation_id'])
    assert after == before  # Rejected disclosure neither cancels nor resends an admitted effect.
    assert (await http(port, 'GET', '/api/v1/operations/' + after['operation_id'], tok=auth))[0] == 404
    return after


@pytest.mark.parametrize('surface', ['http', 'rpc', 'mcp'])
async def test_waited_submission_rechecks_membership_and_retains_original_key(served, surface, monkeypatch, mock):
    d, port = served
    target = await prepared(d.ops)
    auth = token(d, CONTRIBUTOR.actor, *CONTRIBUTOR.scopes)
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, principal_only=True)
    request = {'action': 'pm.message.append', 'target': target,
               'params': {'content': 'PRIVATE WAITED NOTE'}, 'preconditions': {'expected_version': 1},
               'idempotency_key': uuid.uuid4().hex}

    async def withdraw():
        await member(d.ops, target, CONTRIBUTOR.actor, None)

    try:
        op = await wait_and_withdraw(d, port, auth, server, surface, request, withdraw, monkeypatch)
        body = {key: value for key, value in request.items() if key != 'idempotency_key'}
        status, response = await http(port, 'POST', '/api/v1/operations', tok=auth, body=body,
                                      headers={'Idempotency-Key': request['idempotency_key']})
        assert status == 404 and 'admission_refused' not in response['error'], response
        assert d.ops.db.execute('SELECT COUNT(*) FROM pm_messages WHERE thread_id=?', (target['thread_id'],)).fetchone()[0] == 1
        await member(d.ops, target, CONTRIBUTOR.actor, 'contributor')
        replay, created = d.ops.create(CONTRIBUTOR, **request)
        assert not created and replay['operation_id'] == op['operation_id'] and replay['result'] == op['result']
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_waited_http_rpc_mcp_recheck_original_source_and_keep_receipt(served, monkeypatch, mock):
    d, port = served
    work = await evidence_prepared(d.ops)
    wid = work['workspace_id']
    with controller_authority.use(controller_authority.binding(d.ops.db, OWNER), d.journal):
        source = task(d.ops)
        await link(d.ops, work, 'task', source['task_id'])
        await capture(d.ops, work)
    evidence_id = next(value['evidence_id'] for value in facts(d.ops, work) if value['source_kind'] == 'execution_state')

    def source_read(principal):
        return pm_evidence.get_evidence(d.ops, principal, wid, evidence_id)

    def admit(ops, principal, target, params, pre):
        source_read(principal)
        return {'source_workspace_id': wid, 'evidence_id': evidence_id}

    def authorize(ops, principal, op, verb):
        source_read(principal)

    async def run(ctx):
        return source_read(CONTRIBUTOR)  # A local source projection, no external effect.

    action = 'pm.fixture.source_projection'
    d.ops.register(ActionDef(action, 'manage', 'Fixture exercising existing evidence policy', run, admit,
                             ('workspace_id',), authorize_existing=authorize))
    original_visible = pm_workspace.operation_visible

    def source_visible(db, principal, op):
        if not original_visible(db, principal, op):
            return False
        if op['action'] == action:
            try:
                source_read(principal)
            except OperationError:
                return False
        return True

    # Use the same shared privacy extension point as sourced PM commands. The
    # actual evidence check and controller withdrawal remain production code;
    # this test can backport independently of those newer command modules.
    monkeypatch.setattr(pm_workspace, 'operation_visible', source_visible)
    original_authority = d.ops.db.execute('SELECT controller_authority FROM tasks WHERE task_id=?', (source['task_id'],)).fetchone()[0]
    auth = token(d, CONTRIBUTOR.actor, *CONTRIBUTOR.scopes)
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, principal_only=True)

    async def withdraw():
        d.ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source['task_id'],))

    try:
        for surface in ['http', 'rpc', 'mcp']:
            original = {'action': action, 'target': {'workspace_id': wid}, 'params': {},
                        'preconditions': {}, 'idempotency_key': uuid.uuid4().hex}
            op = await wait_and_withdraw(d, port, auth, server, surface, original, withdraw, monkeypatch)
            status, response = await http(port, 'POST', '/api/v1/operations', tok=auth,
                body={key: value for key, value in original.items() if key != 'idempotency_key'},
                headers={'Idempotency-Key': original['idempotency_key']})
            assert status == 404 and 'admission_refused' not in response['error'], response
            d.ops.db.execute('UPDATE tasks SET controller_authority=? WHERE task_id=?', (original_authority, source['task_id']))
            replay, created = d.ops.create(CONTRIBUTOR, **original)
            assert not created and replay['operation_id'] == op['operation_id'] and replay['result'] == op['result']
        assert d.ops.db.execute('SELECT COUNT(*) FROM operations WHERE action=?', (action,)).fetchone()[0] == 3
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_only_new_admission_rejections_allow_new_key_and_actor_namespaces_stay_private(served):
    d, port = served
    target = await prepared(d.ops)
    key = 'same-private-key'
    request = {'action': 'pm.message.append', 'target': target,
               'params': {'content': 'Original'}, 'preconditions': {'expected_version': 1}, 'idempotency_key': key}
    original, _ = d.ops.create(OWNER, **request)
    await settle_operations(d.ops)
    body = {k: v for k, v in request.items() if k != 'idempotency_key'}
    body['params'] = {'unexpected': 'invalid'}
    # OWNER's existing same key cannot be described as a new rejected admission,
    # even when the altered envelope conflicts. CONTRIBUTOR cannot see that key.
    for actor, refused in [(OWNER, False), (CONTRIBUTOR, True)]:
        status, result = await http(port, 'POST', '/api/v1/operations', tok=token(d, actor.actor, *actor.scopes),
                                    body=body, headers={'Idempotency-Key': key})
        assert status in {409, 422}
        assert result['error'].get('admission_refused', False) is refused, result
        assert original['operation_id'] not in json.dumps(result)
    assert d.ops.db.execute("SELECT COUNT(*) FROM operations WHERE action='pm.message.append'").fetchone()[0] == 1
    # Workspace creation normally omits target; that shares create()'s {} namespace.
    created_workspace = d.ops.list(action='pm.workspace.create')['operations'][0]
    status, result = await http(port, 'POST', '/api/v1/operations', tok=token(d, OWNER.actor, *OWNER.scopes),
        body={'action': 'pm.workspace.create', 'params': {'name': 'Changed envelope'}},
        headers={'Idempotency-Key': created_workspace['idempotency_key']})
    assert status == 409 and 'admission_refused' not in result['error'], result
