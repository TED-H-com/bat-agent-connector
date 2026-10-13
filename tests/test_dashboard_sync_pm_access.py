"""Membership changes invalidate visibility continuity without changing draft identity."""
from __future__ import annotations

import asyncio
import base64
import hmac
import json
import uuid
from urllib.parse import urlencode

import pytest

from bat_agent_connector import dashboard_sync, pm_access, pm_read_cache, pm_workspace
from bat_agent_connector.operations import OperationError
from bat_agent_connector.pm_release_baselines import _snapshot
from bat_agent_connector.task_journal import Journal
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import daemon as daemon  # noqa: F401
from tests.test_api_v1 import http, token
from tests.test_api_v1 import served as served  # noqa: F401
from tests.test_dashboard_sync import replay
from tests.test_pm_workspace import OWNER, READER, create, member
from tests.test_pm_workspace import ops as ops  # noqa: F401


def same_identity(before, after):
    assert {key: before[key] for key in ('server_id', 'principal_id')} == {
        key: after[key] for key in ('server_id', 'principal_id')}


async def test_operation_visibility_reuses_read_but_withdraws_after_membership_write(ops):
    workspace = await create(ops)
    operation = ops.get(ops.db.execute("SELECT operation_id FROM operations WHERE action='pm.workspace.create'").fetchone()[0])
    selects = []
    ops.db.set_trace_callback(lambda sql: selects.append(sql) if sql.lstrip().upper().startswith('SELECT') else None)
    try:
        with _snapshot(ops.db), pm_read_cache.scope(ops.db, OWNER):
            before = ops.db.total_changes
            assert pm_access.visible(ops.db, OWNER, operation)
            first = len(selects)
            assert pm_access.visible(ops.db, OWNER, operation)
            # Each independent source reader anchors its own WAL snapshot;
            # none of the PM visibility queries should repeat.
            assert selects[first:] == ['SELECT name FROM sqlite_schema LIMIT 1']
            assert ops.db.total_changes == before
            # Same ID with a changed projection cannot borrow the original
            # workspace's cached permission. The key includes the full input.
            changed = {**operation, 'target': {'workspace_id': 'ws_' + '0' * 32}}
            assert not pm_access.visible(ops.db, OWNER, changed)
            assert pm_access.visible(ops.db, OWNER, operation)
            ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?',
                (workspace['workspace_id'], OWNER.actor))
            assert not pm_access.visible(ops.db, OWNER, operation)
            with pytest.raises(OperationError, match='operation not found'):
                ops.get(operation['operation_id'], principal=OWNER)
            assert ops.list(principal=OWNER)['operations'] == []
    finally:
        ops.db.set_trace_callback(None)


@pytest.mark.parametrize('old_role,new_role', [(None, 'reader'), ('reader', 'contributor'),
                                             ('contributor', 'reader'), ('reader', None)])
async def test_grant_revoke_and_role_changes_reset_only_affected_actor(ops, old_role, new_role):
    workspace = await create(ops)
    if old_role:
        workspace = await member(ops, workspace, READER.actor, old_role)
    before = dashboard_sync.checkpoint(ops.journal, READER)
    owner_before = dashboard_sync.checkpoint(ops.journal, OWNER)
    await member(ops, workspace, READER.actor, new_role)
    with pytest.raises(dashboard_sync.ResetRequired, match='access_changed') as changed:
        replay(ops.journal, before, READER)
    assert changed.value.document()['error']['preserve_drafts'] is True
    fresh = dashboard_sync.checkpoint(ops.journal, READER)
    same_identity(before, fresh)
    assert replay(ops.journal, fresh, READER)['events'] == []
    assert replay(ops.journal, owner_before, OWNER)['events']
    # No membership IDs/roles are disclosed in the signed (not encrypted) claims.
    payload = fresh['checkpoint']['token'].split('.')[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    assert workspace['workspace_id'] not in str(claims)
    assert claims['access_id'] and len(claims['access_id']) == 64


async def test_revoke_regrant_same_membership_cannot_resume_old_checkpoint(ops):
    workspace = await member(ops, await create(ops), READER.actor, 'reader')
    before = dashboard_sync.checkpoint(ops.journal, READER)
    workspace = await member(ops, workspace, READER.actor, None)
    await member(ops, workspace, READER.actor, 'reader')
    with pytest.raises(dashboard_sync.ResetRequired, match='access_changed'):
        replay(ops.journal, before, READER)
    # Access invalidation is durable beyond event retention and process restart.
    ops.db.execute('DELETE FROM api_events')
    other = Journal(ops.journal.path)
    try:
        version = other.db.execute('PRAGMA user_version').fetchone()[0]
        pm_workspace.schema(other)
        pm_workspace.schema(other)
        assert other.db.execute('PRAGMA user_version').fetchone()[0] == version
        with pytest.raises(dashboard_sync.ResetRequired, match='access_changed'):
            replay(other, before, READER)
        fresh = dashboard_sync.checkpoint(other, READER)
        same_identity(before, fresh)
        assert replay(other, fresh, READER)['events'] == []
    finally:
        other.close()


async def test_noop_and_rolled_back_changes_do_not_invalidate(ops):
    workspace = await member(ops, await create(ops), READER.actor, 'reader')
    before = dashboard_sync.checkpoint(ops.journal, READER)
    await member(ops, workspace, READER.actor, 'reader')
    replay(ops.journal, before, READER)
    with pytest.raises(RuntimeError):
        with ops.journal.tx():
            ops.db.execute('DELETE FROM pm_workspace_members WHERE actor=?', (READER.actor,))
            raise RuntimeError('rollback')
    replay(ops.journal, before, READER)
    # Updating non-authority metadata cannot create a spurious reset loop.
    ops.db.execute('UPDATE pm_workspace_members SET updated_at=updated_at+1 WHERE actor=?', (READER.actor,))
    replay(ops.journal, before, READER)


async def test_current_membership_digest_covers_legacy_rows_before_trigger_install(ops):
    workspace = await member(ops, await create(ops), READER.actor, 'reader')
    # Upgrade an existing membership table with no invalidation history.
    for suffix in ('insert', 'update', 'delete'):
        ops.db.execute(f'DROP TRIGGER api_sync_pm_access_{suffix}')  # noqa: S608 - fixed test names
    ops.db.execute('DELETE FROM api_sync_pm_access')
    before = dashboard_sync.checkpoint(ops.journal, READER)
    ops.db.execute("UPDATE pm_workspace_members SET role='contributor' WHERE workspace_id=? AND actor=?",
                   (workspace['workspace_id'], READER.actor))
    pm_workspace.schema(ops.journal)
    with pytest.raises(dashboard_sync.ResetRequired, match='access_changed'):
        replay(ops.journal, before, READER)


def test_signed_pre_upgrade_checkpoint_requires_one_safe_resnapshot(ops):
    current = dashboard_sync.checkpoint(ops.journal, READER)
    cp = current['checkpoint']
    raw = cp['token'].split('.')[1]
    claims = json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
    claims.pop('access_id')
    payload = dashboard_sync._canonical(claims)
    signature = hmac.digest(bytes.fromhex(dashboard_sync._metadata(ops.journal)['signing_key']), payload, 'sha256')
    legacy_token = 's1.' + dashboard_sync._b64(payload) + '.' + dashboard_sync._b64(signature)
    with pytest.raises(dashboard_sync.ResetRequired, match='access_changed'):
        dashboard_sync.event_page(ops.journal, READER, cp['cursor'], token=legacy_token)
    assert replay(ops.journal, current, READER)['events'] == []


async def http_operation(d, port, auth, action, target=None, params=None, preconditions=None):
    status, response = await http(port, 'POST', '/api/v1/operations', tok=auth,
        headers={'Idempotency-Key': uuid.uuid4().hex}, body={
            'action': action, 'target': target or {}, 'params': params or {}, 'preconditions': preconditions or {}})
    assert status in (200, 202), response
    operation_id = response['operation']['operation_id']
    await settle_operations(d.ops)
    status, response = await http(port, 'GET', '/api/v1/operations/' + operation_id, tok=auth)
    assert status == 200 and response['operation']['status'] == 'succeeded', response
    return response['operation']


async def http_member(d, port, auth, workspace, actor, role):
    operation = await http_operation(d, port, auth, 'pm.workspace.member.set',
        {'workspace_id': workspace['workspace_id']}, {'actor': actor, 'role': role},
        {'expected_version': workspace['version']})
    return operation['result']['workspace'], operation


async def http_world(d, port):
    owner_auth = token(d, OWNER.actor, 'observe', 'manage')
    reader_auth = token(d, READER.actor, 'observe')
    operation = await http_operation(d, port, owner_auth, 'pm.workspace.create', params={'name': 'Private source'})
    workspace, _ = await http_member(d, port, owner_auth, operation['result']['workspace'], READER.actor, 'reader')
    return owner_auth, reader_auth, workspace


async def test_real_two_principal_http_revoke_resets_and_does_not_publish_hidden_event(served):
    d, port = served
    owner_auth, reader_auth, workspace = await http_world(d, port)
    # Another still-visible workspace must not mask a source workspace's revocation.
    other = await http_operation(d, port, owner_auth, 'pm.workspace.create', params={'name': 'Current project'})
    current, _ = await http_member(d, port, owner_auth, other['result']['workspace'], READER.actor, 'reader')
    status, before = await http(port, 'GET', '/api/v1/bootstrap', tok=reader_auth)
    assert status == 200
    cp = before['sync']['checkpoint']
    workspace, removal = await http_member(d, port, owner_auth, workspace, READER.actor, None)
    path = '/api/v1/events?' + urlencode({'after': cp['cursor'], 'checkpoint': cp['token']})
    status, reset = await http(port, 'GET', path, tok=reader_auth)
    assert status == 409 and reset['error']['reason'] == 'access_changed'
    assert reset['error']['preserve_drafts'] and reset['error']['resnapshot']
    assert workspace['workspace_id'] not in str(reset) and removal['operation_id'] not in str(reset)
    assert (await http(port, 'GET', '/api/v1/pm/workspaces/' + workspace['workspace_id'], tok=reader_auth))[0] == 404
    assert (await http(port, 'GET', '/api/v1/pm/workspaces/' + current['workspace_id'], tok=reader_auth))[0] == 200
    status, legacy = await http(port, 'GET', '/api/v1/events?' + urlencode({'after': cp['cursor']}), tok=reader_auth)
    assert status == 200 and legacy['events'] == [] and legacy['next_cursor'] > cp['cursor']
    assert (await http(port, 'GET', '/api/v1/operations/' + removal['operation_id'], tok=reader_auth))[0] == 404
    status, fresh = await http(port, 'GET', '/api/v1/bootstrap', tok=reader_auth)
    assert status == 200
    same_identity(before['sync'], fresh['sync'])
    assert workspace['workspace_id'] not in str(fresh['snapshot']['operations'])
    cp = fresh['sync']['checkpoint']
    assert (await http(port, 'GET', '/api/v1/events?' + urlencode({'after': cp['cursor'], 'checkpoint': cp['token']}),
                       tok=reader_auth))[0] == 200


async def test_real_sse_rechecks_access_even_when_revocation_events_are_filtered(served):
    d, port = served
    owner_auth, reader_auth, workspace = await http_world(d, port)
    _, bootstrap = await http(port, 'GET', '/api/v1/bootstrap', tok=reader_auth)
    cp = bootstrap['sync']['checkpoint']
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    path = '/api/v1/events/stream?' + urlencode({'after': cp['cursor'], 'checkpoint': cp['token']})
    writer.write(f'GET {path} HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {reader_auth}\r\n\r\n'.encode())
    await writer.drain()
    try:
        assert b'200 OK' in await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
        await http_member(d, port, owner_auth, workspace, READER.actor, None)
        # The first loop may already have issued an empty checkpoint. No revoked
        # event or operation content may be sent while waiting for the reset.
        for _ in range(5):
            frame = await asyncio.wait_for(reader.readuntil(b'\n\n'), 5)
            assert workspace['workspace_id'].encode() not in frame
            if frame.startswith(b'event: sync.reset\n'):
                reset = json.loads(frame.split(b'data: ', 1)[1])
                assert reset['error']['reason'] == 'access_changed'
                assert reset['error']['preserve_drafts']
                break
            assert frame.startswith((b'event: sync.checkpoint\n', b':'))
        else:
            pytest.fail('The existing SSE stream did not invalidate revoked access')
    finally:
        writer.close()
        await writer.wait_closed()
