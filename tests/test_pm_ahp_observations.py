"""Original AHP read receipts, authority and lifecycle with actual offline sockets."""
from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from bat_agent_connector import api_auth, pm_access
from bat_agent_connector import pm_ahp_observations as ahp
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.operation_helpers import settle_operations
from tests.test_ahp_observer import SESSION, TOKEN, stable_010, unproven_reconnect
from tests.test_ahp_observer import host as ahp_host
from tests.test_pm_workspace import OWNER, create, member
from tests.test_pm_workspace import ops as workspace_ops

ops, host = workspace_ops, ahp_host


@pytest.fixture
async def ready(ops, host):
    workspace = await create(ops)
    with ops.journal.tx():
        token = api_auth.issue(ops.db, 'observer', ['observe'])
    principal = api_auth.authenticate(ops.db, token, 'unrelated-admin')
    await member(ops, workspace, principal.actor, 'reader')
    host.adapter = replace(host.adapter, workspace_ids=(workspace['workspace_id'],))
    ops.context['fleet'] = SimpleNamespace(config=SimpleNamespace(ahp={'local': host.adapter}))
    ahp.install(ops)
    return ops, host, workspace['workspace_id'], principal


def request(wid, **changes):
    return {'action': ahp.ACTION, 'target': {'workspace_id': wid, 'adapter_id': 'local'},
            'params': {'session_uris': [SESSION], 'include_changesets': True}, 'preconditions': {},
            'idempotency_key': 'original-observation', **changes}


async def run(ready, **changes):
    ops, host, wid, principal = ready
    op, created = ops.create(principal, **request(wid, **changes))
    await settle_operations(ops)
    return ops.get(op['operation_id'])


async def test_reader_observes_manual_source_once_and_all_receipts_are_immutable(ready):
    ops, host, wid, principal = ready
    done = await run(ready)
    assert done['status'] == 'succeeded', done
    oid = done['result']['observation_id']
    read = ahp.get_observation(ops, principal, wid, oid)['observation']
    assert read['document']['evidence']['status'] == 'observed'
    assert ahp.operation_visible(ops.db, principal, done)
    assert ahp.current_observation(ops, principal, wid)['observation'] == read
    page = ahp.list_observations(ops, principal, wid)
    assert page['observations'][0]['observation_id'] == oid and 'document' not in page['observations'][0]
    count = len(host.requests)
    replay = await run(ready)
    assert replay['operation_id'] == done['operation_id'] and replay['result'] == done['result']
    assert len(host.requests) == count
    with pytest.raises(sqlite3.IntegrityError):
        ops.db.execute('UPDATE pm_ahp_observations SET document=?', ('{}',))
    with pytest.raises(sqlite3.IntegrityError):
        ops.db.execute('DELETE FROM pm_ahp_observations')
    dump = '\n'.join(ops.db.iterdump())
    assert TOKEN not in dump and host.adapter.endpoint not in dump and host.adapter.credential_ref not in dump
    assert ahp.capabilities()['create_own'] == 'unsupported'


async def test_actual_query_credential_echo_never_reaches_saved_document_or_events(ready, monkeypatch):
    ops, host, wid, principal = ready
    host.token = 'synthetic +/encoded=credential%測試'
    monkeypatch.setenv('AHP_FIXTURE_TOKEN', host.token)
    encoded = urlencode({'tkn': host.token})[4:]
    async def echo(ws, req, connection):
        host.catalog[0]['title'] = host.token + ' ' + ws.request.path
        host.session_state['title'] = ws.request.path
        return False
    host.on_request = echo
    done = await run(ready)
    assert done['status'] == 'succeeded', done
    read = ahp.get_observation(ops, principal, wid, done['result']['observation_id'])['observation']
    assert read['document']['evidence']['catalog'][0]['title'] == '<redacted> /ahp?tkn=<redacted>'
    assert ahp.operation_visible(ops.db, principal, done)
    dump = '\n'.join(ops.db.iterdump())
    assert host.token not in dump and encoded not in dump
    assert all('<redacted>' not in row[0] for row in ops.db.execute('SELECT resource FROM pm_ahp_resource_access'))
    before = len(host.requests)
    assert (await run(ready))['result'] == done['result']
    assert len(host.requests) == before


@pytest.mark.parametrize('identity_echo', ['short_token', 'encoded_token'])
async def test_credential_identity_collision_never_publishes_synthetic_source(ready, monkeypatch, identity_echo):
    ops, host, wid, principal = ready
    original = await run(ready)
    assert original['status'] == 'succeeded', original
    access = [tuple(row) for row in ops.db.execute('SELECT * FROM pm_ahp_resource_access ORDER BY resource')]
    saved = tuple(ops.db.execute('SELECT document,document_digest FROM pm_ahp_observations').fetchone())
    host.token = 'one' if identity_echo == 'short_token' else 'synthetic +/identity=credential%測試'
    monkeypatch.setenv('AHP_FIXTURE_TOKEN', host.token)
    if identity_echo == 'encoded_token':
        host.catalog[0]['resource'] = 'ahp-session:/' + urlencode({'tkn': host.token})[4:]
    done = await run(ready, params={}, idempotency_key='private-identity-refusal')
    assert done['status'] == 'failed' and done['error_code'] == 'AHP_PRIVATE_IDENTITY_REFUSED', done
    assert ops.db.execute('SELECT count(*) FROM pm_ahp_observations').fetchone()[0] == 1
    assert [tuple(row) for row in ops.db.execute('SELECT * FROM pm_ahp_resource_access ORDER BY resource')] == access
    assert tuple(ops.db.execute('SELECT document,document_digest FROM pm_ahp_observations').fetchone()) == saved
    assert ahp.operation_visible(ops.db, principal, original)
    assert ahp.get_observation(ops, principal, wid, original['result']['observation_id'])['observation']['document_digest'] == saved[1]
    assert not ops.context['ahp_active']
    assert not any(event['kind'] == 'pm.ahp.observed' and event['body'].get('operation_id') == done['operation_id']
        for event in ops.journal.api_events(0, 500)['events'])


async def test_crash_after_local_receipt_preserves_original_result_without_reopening_socket(ready, monkeypatch):
    ops, host, wid, principal = ready
    monkeypatch.setattr(ops, 'kick', lambda: None)
    original, _ = ops.create(principal, **request(wid))
    result = await ops.actions[ahp.ACTION].run(OpContext(ops, original))
    count = len(host.requests)
    recovered_journal = Journal(ops.journal.path)
    try:
        recovered = OperationService(recovered_journal)
        recovered.context['fleet'] = ops.context['fleet']
        ahp.install(recovered)
        await settle_operations(recovered)
        assert recovered.get(original['operation_id'])['result'] == result
        assert len(host.requests) == count
        assert recovered.db.execute('SELECT count(*) FROM pm_ahp_observations').fetchone()[0] == 1
    finally:
        recovered_journal.close()


@pytest.mark.parametrize('withdraw', ['membership', 'adapter', 'binding'])
async def test_original_source_withdrawal_hides_detail_list_events_and_original_replay(ready, withdraw):
    ops, host, wid, principal = ready
    done = await run(ready)
    assert done['status'] == 'succeeded', done
    if withdraw == 'membership':
        ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (wid, principal.actor))
    elif withdraw == 'adapter':
        ops.context['fleet'].config.ahp = {}
    else:
        ops.context['fleet'].config.ahp = {'local': replace(host.adapter, endpoint='ws://127.0.0.1:9876/')}
    ahp.sync_config(ops)
    assert not pm_access.visible(ops.db, principal, done)
    events = ops.journal.api_events(0, 500)['events']
    assert not any(pm_access.event_visible(ops.db, principal, event) for event in events
                   if event['body'].get('operation_id') == done['operation_id'] or event['resource_id'] == done['operation_id'])
    with pytest.raises(OperationError, match='NOT_FOUND'):
        ahp.get_observation(ops, principal, wid, done['result']['observation_id'])
    with pytest.raises(OperationError, match='NOT_FOUND'):
        ops.create(principal, **request(wid))
    if withdraw != 'membership':
        assert ahp.list_observations(ops, principal, wid)['observations'] == []
        assert ahp.current_observation(ops, principal, wid) == {'workspace_id': wid, 'observation': None, 'visibility_filtered': True}


@pytest.mark.parametrize('change', ['credential', 'membership', 'config', 'pause', 'reopen'])
async def test_current_authority_and_lifecycle_stop_remaining_frames_and_publication(ready, change):
    ops, host, wid, principal = ready
    async def revoke(ws, req, connection):
        if req['method'] == 'listSessions':
            if change == 'credential':
                api_auth.revoke(ops.db, principal.actor)
            elif change == 'membership':
                ops.db.execute('DELETE FROM pm_workspace_members WHERE actor=?', (principal.actor,))
            elif change == 'config':
                ops.context['fleet'].config.ahp = {}
            else:
                stage = 'paused' if change == 'pause' else 'development'
                ops.db.execute('UPDATE pm_lifecycle_heads SET stage=?,revision=revision+1,execution_epoch=execution_epoch+1 WHERE workspace_id=?', (stage, wid))
        return False
    host.on_request = revoke
    done = await run(ready)
    assert done['status'] == 'failed', done
    assert [r['method'] for _, r in host.requests] == ['initialize', 'listSessions']
    assert ops.db.execute('SELECT count(*) FROM pm_ahp_observations').fetchone()[0] == 0
    assert not ops.context['ahp_active']


@pytest.mark.parametrize('stage', ['paused', 'closed'])
async def test_explicit_new_read_record_allowed_while_stopped_and_original_read_after_later_stop(ready, stage):
    ops, host, wid, principal = ready
    ops.db.execute('UPDATE pm_lifecycle_heads SET stage=?,revision=revision+1 WHERE workspace_id=?', (stage, wid))
    done = await run(ready)
    assert done['status'] == 'succeeded', done
    ops.db.execute('UPDATE pm_lifecycle_heads SET revision=revision+1,execution_epoch=execution_epoch+1 WHERE workspace_id=?', (wid,))
    assert ahp.get_observation(ops, principal, wid, done['result']['observation_id'])['observation']
    assert (await run(ready))['result'] == done['result']
    assert host.connections == 1


async def test_exact_request_schema_scopes_and_configured_ids_only(ready):
    ops, host, wid, principal = ready
    for changes in [{'target': {'workspace_id': wid, 'adapter_id': 'arbitrary-url'}},
                    {'params': {'endpoint': host.adapter.endpoint}},
                    {'params': {'session_uris': ['file:///human/checkout']}},
                    {'params': {'session_uris': [SESSION] * 9}},
                    {'params': {'include_changesets': 1}}, {'preconditions': {'force': True}}]:
        with pytest.raises(OperationError):
            ops.create(principal, **request(wid, **changes))
    with pytest.raises(OperationError, match='FORBIDDEN'):
        ops.create(api_auth.Principal(OWNER.actor, frozenset({'manage'})), **request(wid))
    assert not host.requests
    caps = ahp.adapters(ops, principal, wid)
    assert [a['adapter_id'] for a in caps['adapters']] == ['local']
    assert 'endpoint' not in json.dumps(caps) and 'credential' not in json.dumps(caps)
    for values in [{'after': True}, {'after': -1}, {'after': 2**63}, {'limit': 51}, {'limit': False}]:
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            ahp.list_observations(ops, principal, wid, **values)


async def test_explicit_peer_withdrawal_hides_whole_history_and_same_native_identity_can_restore(ready):
    ops, host, wid, principal = ready
    original = await run(ready)
    oid = original['result']['observation_id']
    host.catalog = []
    withdrawn = await run(ready, idempotency_key='new-catalog')
    assert withdrawn['status'] == 'succeeded'
    assert not ahp.operation_visible(ops.db, principal, original)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        ahp.get_observation(ops, principal, wid, oid)
    from tests.test_ahp_observer import SUMMARY
    host.catalog = [{**SUMMARY, 'title': 'Same title, new native incarnation', 'createdAt': '2026-10-12T00:00:00Z'}]
    await run(ready, idempotency_key='reused-uri-new-incarnation')
    assert not ahp.operation_visible(ops.db, principal, original)
    host.catalog = [dict(SUMMARY)]
    await run(ready, idempotency_key='original-identity-positive')
    assert ahp.operation_visible(ops.db, principal, original)
    assert ahp.get_observation(ops, principal, wid, oid)['observation']['document_digest'] == original['result']['document_digest']


async def test_incomplete_catalog_cannot_withdraw_previously_readable_sources(ready):
    ops, host, wid, principal = ready
    original = await run(ready)
    async def incomplete(ws, req, connection):
        if req['method'] == 'listSessions':
            await ws.send(json.dumps({'jsonrpc': '2.0', 'id': req['id'], 'result': {'items': [], 'nextCursor': 'cycle'}}))
            return True
        return False
    host.on_request = incomplete
    done = await run(ready, idempotency_key='incomplete-refresh')
    assert done['status'] == 'succeeded'
    assert done['result']['coverage_status'] == 'incomplete'
    assert ahp.operation_visible(ops.db, principal, original)
    assert ahp.get_observation(ops, principal, wid, original['result']['observation_id'])['observation']


async def test_explicit_native_changeset_withdrawal_hides_original_detail_and_operation(ready):
    from tests.test_ahp_observer import CHANGESET
    ops, host, wid, principal = ready
    original = await run(ready)
    first_connection = host.connections + 1
    host.reconnect = {'type': 'replay', 'actions': [], 'missing': [CHANGESET]}
    async def disconnect(ws, req, connection):
        if connection == first_connection and req['method'] == 'subscribe' and req['params']['channel'] == CHANGESET:
            await ws.close()
            return True
        return False
    host.on_request = disconnect
    refreshed = await run(ready, idempotency_key='changeset-withdrawal')
    assert refreshed['status'] == 'succeeded'
    assert not ahp.operation_visible(ops.db, principal, original)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        ahp.get_observation(ops, principal, wid, original['result']['observation_id'])


async def test_replacing_revoked_credential_does_not_upgrade_original_pending_authority(ready, monkeypatch):
    ops, host, wid, principal = ready
    monkeypatch.setattr(ops, 'kick', lambda: None)
    original, _ = ops.create(principal, **request(wid))
    api_auth.revoke(ops.db, principal.actor)
    api_auth.issue(ops.db, principal.actor, ['observe'])
    with pytest.raises(OperationError, match='AHP_AUTHORITY_UNAVAILABLE'):
        await ops.actions[ahp.ACTION].run(OpContext(ops, original))
    assert not host.requests


@pytest.mark.parametrize('catalog', ['failed', 'empty', 'reused_uri'])
async def test_reconnect_unverified_metadata_never_becomes_old_source_visible_receipt(ready, catalog):
    ops, host, wid, principal = ready
    stable_010(host)
    params = {'session_uris': [host.session], 'include_changesets': True}
    original = await run(ready, params=params)
    assert original['status'] == 'succeeded', original
    original_row = tuple(ops.db.execute('SELECT document,document_digest FROM pm_ahp_observations').fetchone())
    proven, _, _ = unproven_reconnect(host, mode='snapshot', catalog=catalog)
    done = await run(ready, params=params, idempotency_key='unverified-incarnation')
    assert done['status'] == 'succeeded' and done['result']['coverage_status'] == 'incomplete', done
    oid = done['result']['observation_id']
    document = json.loads(ops.db.execute('SELECT document FROM pm_ahp_observations WHERE observation_id=?', (oid,)).fetchone()[0])
    evidence = document['evidence']
    assert evidence['reconnect_evidence'] == 'unverified'
    assert [e['original_digest'] for e in evidence['events']] == [ahp.provider.digest(proven)]
    assert 'unconfirmed' not in '\n'.join(ops.db.iterdump()).lower()
    assert tuple(ops.db.execute('SELECT document,document_digest FROM pm_ahp_observations WHERE operation_id=?',
        (original['operation_id'],)).fetchone()) == original_row
    if catalog == 'failed':
        assert ahp.operation_visible(ops.db, principal, done)
        assert ahp.get_observation(ops, principal, wid, oid)['observation']['document'] == document
        before = len(host.requests)
        assert (await run(ready, params=params, idempotency_key='unverified-incarnation'))['result'] == done['result']
        assert len(host.requests) == before
    else:
        assert not ahp.operation_visible(ops.db, principal, done)
        with pytest.raises(OperationError, match='NOT_FOUND'):
            ahp.get_observation(ops, principal, wid, oid)
        with pytest.raises(OperationError, match='NOT_FOUND'):
            ops.create(principal, **request(wid, params=params, idempotency_key='unverified-incarnation'))
        assert oid not in {item['observation_id'] for item in ahp.list_observations(ops, principal, wid)['observations']}
        assert not any(pm_access.event_visible(ops.db, principal, event)
            for event in ops.journal.api_events(0, 500)['events']
            if event['body'].get('operation_id') == done['operation_id'] or event['resource_id'] == done['operation_id'])


@pytest.mark.parametrize('catalog', ['failed', 'empty', 'reused_uri'])
async def test_reconnect_cannot_orphan_retained_snapshots_or_events_from_original_source(ready, catalog):
    from tests.test_ahp_observer import CHANGESET, SUMMARY
    ops, host, wid, principal = ready
    original = await run(ready)
    assert original['status'] == 'succeeded'
    first_connection = host.connections + 1
    async def change(ws, req, connection):
        if connection == first_connection and req['method'] == 'subscribe' and req['params']['channel'] == CHANGESET:
            await ws.send(json.dumps({'jsonrpc': '2.0', 'method': 'action', 'params': {
                'channel': SESSION, 'serverSeq': 13, 'action': {'type': 'session/titleChanged'}}}))
            await ws.close()
            return True
        if connection > first_connection and req['method'] == 'listSessions':
            reply = {'jsonrpc': '2.0', 'id': req['id']}
            if catalog == 'failed':
                reply['error'] = {'code': -32603, 'message': 'offline fixture unavailable'}
            else:
                reply['result'] = {'items': [] if catalog == 'empty' else [
                    {**SUMMARY, 'createdAt': '2026-10-12T00:00:00Z'}]}
            await ws.send(json.dumps(reply))
            return True
        return False
    host.on_request = change
    refreshed = await run(ready, idempotency_key='interrupted-original-source')
    assert refreshed['status'] == 'succeeded' and refreshed['result']['coverage_status'] == 'incomplete'
    oid = refreshed['result']['observation_id']
    document = json.loads(ops.db.execute('SELECT document FROM pm_ahp_observations WHERE observation_id=?', (oid,)).fetchone()[0])
    original_source = {'resource': SESSION, 'source_kind': 'session', 'source_identity': ahp.provider.digest({
        key: SUMMARY[key] for key in ('resource', 'provider', 'createdAt')})}
    assert original_source in document['original_sources']
    assert document['evidence']['events'][0]['original_sources'] == [original_source]
    if catalog == 'failed':
        assert ahp.operation_visible(ops.db, principal, refreshed)
        session = next(item for item in document['evidence']['snapshots'] if item['resource'] == SESSION)
        assert session['original_sources'] == [original_source]
        host.on_request, host.catalog = None, []
        withdrawn = await run(ready, idempotency_key='later-original-source-withdrawal', params={})
        assert withdrawn['status'] == 'succeeded'
    else:
        assert not ahp.operation_visible(ops.db, principal, refreshed)
    requests = len(host.requests)
    assert not ahp.operation_visible(ops.db, principal, refreshed)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        ahp.get_observation(ops, principal, wid, oid)
    assert oid not in {item['observation_id'] for item in ahp.list_observations(ops, principal, wid)['observations']}
    assert not any(pm_access.event_visible(ops.db, principal, event)
        for event in ops.journal.api_events(0, 500)['events']
        if event['body'].get('operation_id') == refreshed['operation_id'] or event['resource_id'] == refreshed['operation_id'])
    assert len(host.requests) == requests


@pytest.mark.parametrize('withdraw', ['empty_catalog', 'new_incarnation', 'session_notification', 'changeset_missing'])
async def test_stable_original_sources_gate_whole_receipt_operation_and_events(ready, withdraw):
    ops, host, wid, principal = ready
    stable_010(host)
    params = {'session_uris': [host.session], 'include_changesets': True}
    original = await run(ready, params=params)
    assert original['status'] == 'succeeded', original
    oid = original['result']['observation_id']
    detail = ahp.get_observation(ops, principal, wid, oid)['observation']
    evidence = detail['document']['evidence']
    assert evidence['status'] == 'observed' and evidence['negotiated_version'] == '0.10.0'
    assert {s['source_kind'] for s in detail['document']['original_sources']} == {'session', 'changeset'}
    assert ahp.operation_visible(ops.db, principal, original)
    if withdraw == 'empty_catalog':
        host.catalog = []
    elif withdraw == 'new_incarnation':
        host.catalog[0]['createdAt'] = '2026-10-12T00:00:00Z'
    else:
        first = host.connections + 1
        async def change(ws, req, connection):
            if req['method'] == 'subscribe' and req['params']['channel'] == host.changeset:
                if withdraw == 'session_notification':
                    await ws.send(json.dumps({'jsonrpc': '2.0', 'method': 'root/sessionRemoved',
                        'params': {'channel': ahp.provider.ROOT, 'session': host.session}}))
                elif connection == first:
                    await ws.close()
                    return True
            return False
        host.on_request = change
        host.reconnect = {'type': 'replay', 'actions': [], 'missing': [host.changeset]}
    refreshed = await run(ready, params=params, idempotency_key='stable-withdrawal')
    assert refreshed['status'] == 'succeeded', refreshed
    assert not pm_access.visible(ops.db, principal, original)
    assert not any(pm_access.event_visible(ops.db, principal, event)
        for event in ops.journal.api_events(0, 500)['events']
        if event['body'].get('operation_id') == original['operation_id'] or event['resource_id'] == original['operation_id'])
    with pytest.raises(OperationError, match='NOT_FOUND'):
        ahp.get_observation(ops, principal, wid, oid)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        ops.create(principal, **request(wid, params=params))
    assert oid not in {item['observation_id'] for item in ahp.list_observations(ops, principal, wid)['observations']}
    saved = ops.db.execute('SELECT document,document_digest FROM pm_ahp_observations WHERE observation_id=?', (oid,)).fetchone()
    assert json.loads(saved[0]) == detail['document'] and saved[1] == detail['document_digest']


async def test_stable_over_limit_catalog_cannot_revoke_original_identity(ready):
    ops, host, wid, principal = ready
    stable_010(host)
    params = {'session_uris': [host.session], 'include_changesets': True}
    original = await run(ready, params=params)
    host.catalog = [{**host.catalog[0], 'resource': f'copilotcli:/large-{i}'} for i in range(201)]
    refreshed = await run(ready, params=params, idempotency_key='oversized-catalog')
    assert refreshed['status'] == 'succeeded' and refreshed['result']['coverage_status'] == 'incomplete'
    assert ahp.operation_visible(ops.db, principal, original)


async def test_7a_projection_upgrade_uses_frozen_relations_and_preserves_document_bytes(ready, tmp_path):
    ops, host, wid, principal = ready
    original = await run(ready)
    row = dict(ops.db.execute('SELECT * FROM pm_ahp_observations WHERE operation_id=?', (original['operation_id'],)).fetchone())
    document = copy.deepcopy(json.loads(row['document']))
    def old_shape(value):
        if isinstance(value, dict):
            value.pop('source_kind', None)
            for item in value.values():
                old_shape(item)
        elif isinstance(value, list):
            for item in value:
                old_shape(item)
    old_shape(document)
    for key in ('negotiated_version', 'decoder_profile', 'reconnect_evidence'):
        document['evidence'].pop(key)
    row['document'], row['document_digest'] = json.dumps(document), ahp.provider.digest(document)
    legacy = Journal(tmp_path / 'legacy-ahp.sqlite')
    try:
        legacy.db.execute(ops.db.execute("SELECT sql FROM sqlite_master WHERE name='pm_ahp_observations'").fetchone()[0])
        legacy.db.execute('''CREATE TABLE pm_ahp_resource_access (
            adapter_id TEXT NOT NULL, binding_digest TEXT NOT NULL, resource TEXT NOT NULL,
            source_identity TEXT NOT NULL, available INTEGER NOT NULL,
            PRIMARY KEY(adapter_id,binding_digest,resource,source_identity))''')
        legacy.db.execute('INSERT INTO pm_ahp_observations VALUES(' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        legacy.db.executemany('INSERT INTO pm_ahp_resource_access VALUES(?,?,?,?,?)',
            [tuple(r) for r in ops.db.execute('SELECT adapter_id,binding_digest,resource,source_identity,available FROM pm_ahp_resource_access')])
        legacy.db.execute('INSERT INTO pm_ahp_resource_access VALUES(?,?,?,?,?)',
            ('local', host.adapter.binding_digest(), 'copilotcli:/unproven', 'f' * 64, 1))
        version = legacy.db.execute('PRAGMA user_version').fetchone()[0]
        ahp.schema(legacy)
        ahp.schema(legacy)
        assert legacy.db.execute('PRAGMA user_version').fetchone()[0] == version
        assert dict(legacy.db.execute('SELECT * FROM pm_ahp_observations').fetchone()) == row
        assert {(r['source_kind'], r['available']) for r in legacy.db.execute('SELECT * FROM pm_ahp_resource_access')} == {
            ('session', 1), ('changeset', 1), (None, 0)}
        # Historical documents have no kind field; access remains original-fact gated.
        ahp._sources_access(legacy.db, 'local', host.adapter.binding_digest(), document['original_sources'])
    finally:
        legacy.close()


def earlier_document(document, generation):
    value = copy.deepcopy(document)
    if generation == 'modern':
        return value
    def strip_kind(item):
        if isinstance(item, dict):
            item.pop('source_kind', None)
            for child in item.values():
                strip_kind(child)
        elif isinstance(item, list):
            for child in item:
                strip_kind(child)
    strip_kind(value)
    evidence = value['evidence']
    evidence.pop('negotiated_version')
    evidence.pop('decoder_profile')
    evidence.pop('reconnect_evidence')
    if generation == '40f':
        for receipt in [*evidence['snapshots'], *evidence['events']]:
            receipt.pop('original_sources')
    return value


def historical_copy(ops, original, document, path):
    """Seed an independent pre-upgrade database; production receipts stay immutable."""
    journal = Journal(path)
    ops.db.backup(journal.db)
    row = dict(journal.db.execute('SELECT * FROM pm_ahp_observations WHERE operation_id=?', (original['operation_id'],)).fetchone())
    row['document'], row['document_digest'] = json.dumps(document), ahp.provider.digest(document)
    definition = journal.db.execute("SELECT sql FROM sqlite_master WHERE name='pm_ahp_observations'").fetchone()[0]
    access = [tuple(r) for r in journal.db.execute(
        'SELECT adapter_id,binding_digest,resource,source_identity,available FROM pm_ahp_resource_access')]
    with journal.tx():
        # This fixture represents the earlier persisted format, including its
        # original operation receipt. No UPDATE bypass exists in production.
        journal.db.execute('DROP TABLE pm_ahp_observations')
        journal.db.execute(definition)
        journal.db.execute('INSERT INTO pm_ahp_observations VALUES(' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        journal.db.execute('DROP TABLE pm_ahp_resource_access')
        journal.db.execute('''CREATE TABLE pm_ahp_resource_access (
            adapter_id TEXT NOT NULL, binding_digest TEXT NOT NULL, resource TEXT NOT NULL,
            source_identity TEXT NOT NULL, available INTEGER NOT NULL,
            PRIMARY KEY(adapter_id,binding_digest,resource,source_identity))''')
        journal.db.executemany('INSERT INTO pm_ahp_resource_access VALUES(?,?,?,?,?)', access)
        result = {**original['result'], 'document_digest': row['document_digest']}
        journal.db.execute('UPDATE operations SET result=? WHERE operation_id=?',
            (json.dumps(result), original['operation_id']))
    recovered = OperationService(journal)
    recovered.context['fleet'] = ops.context['fleet']
    ahp.install(recovered)
    return recovered, row


@pytest.mark.parametrize('generation', ['40f', '7a'])
@pytest.mark.parametrize('available', [True, False])
async def test_earlier_complete_receipts_keep_original_bytes_and_existing_access_only(ready, tmp_path, generation, available):
    ops, host, wid, principal = ready
    original = await run(ready)
    original_doc = ahp.get_observation(ops, principal, wid, original['result']['observation_id'])['observation']['document']
    document = earlier_document(original_doc, generation)
    if not available:
        ops.db.execute('UPDATE pm_ahp_resource_access SET available=0')
    historical, frozen = historical_copy(ops, original, document, tmp_path / 'earlier.sqlite')
    try:
        count = len(host.requests)
        assert ahp.operation_visible(historical.db, principal, original) is available
        assert {r[0] for r in historical.db.execute('SELECT available FROM pm_ahp_resource_access')} == {int(available)}
        oid = original['result']['observation_id']
        if available:
            assert ahp.get_observation(historical, principal, wid, oid)['observation']['document'] == document
            replay, created = historical.create(principal, **request(wid))
            assert not created and replay['result']['document_digest'] == frozen['document_digest']
        else:
            with pytest.raises(OperationError, match='NOT_FOUND'):
                ahp.get_observation(historical, principal, wid, oid)
        ahp.schema(historical.journal)
        assert dict(historical.db.execute('SELECT * FROM pm_ahp_observations').fetchone()) == frozen
        assert len(host.requests) == count
    finally:
        historical.journal.close()


@pytest.mark.parametrize('failure', ['orphan_reconnect', 'reconnect', 'gap', 'missing_timestamp',
    'changed_incarnation', 'unadvertised_changeset', 'missing_original_reference', 'empty_original_references',
    'modern_empty_receipt_references'])
async def test_unproven_historical_coverage_hides_every_read_even_after_new_positive_capture(ready, tmp_path, failure):
    ops, host, wid, principal = ready
    original = await run(ready)
    oid = original['result']['observation_id']
    original_doc = ahp.get_observation(ops, principal, wid, oid)['observation']['document']
    modern = failure in {'missing_original_reference', 'empty_original_references', 'modern_empty_receipt_references'}
    document = earlier_document(original_doc, 'modern' if modern else '40f')
    evidence = document['evidence']
    session = next(item for item in evidence['snapshots'] if item['resource'] == SESSION)
    if failure == 'orphan_reconnect':
        evidence.update(catalog=[], positive_catalog=[], catalog_complete=False,
            reconnect='replay', status='incomplete', reason='peer_read_refused')
        document['original_sources'] = []
        evidence['events'].append({'channel': SESSION, 'server_seq': 13,
            'action_type': 'session/titleChanged', 'original_digest': 'a' * 64,
            'received_at': 1, 'payload': 'not_retained'})
    elif failure == 'reconnect':
        evidence['reconnect'] = 'replay'
    elif failure == 'gap':
        evidence['gaps'] = [{'reason': 'snapshot_fallback', 'after_server_seq': 10}]
    elif failure == 'missing_timestamp':
        session['state'].pop('createdAt')
    elif failure == 'changed_incarnation':
        session['state']['createdAt'] = '2026-10-12T00:00:00Z'
    elif failure == 'unadvertised_changeset':
        session['state']['changesets'] = []
    elif failure == 'missing_original_reference':
        document['original_sources'].pop()
    elif failure == 'empty_original_references':
        document['original_sources'] = []
    else:
        session['original_sources'] = []
    historical, frozen = historical_copy(ops, original, document, tmp_path / 'unproven.sqlite')
    try:
        assert not pm_access.visible(historical.db, principal, original)
        assert ahp.current_observation(historical, principal, wid) == {
            'workspace_id': wid, 'observation': None, 'visibility_filtered': True}
        newer = await run((historical, host, wid, principal), idempotency_key='new-positive-original-identities')
        assert newer['status'] == 'succeeded', newer
        assert {r[0] for r in historical.db.execute('SELECT available FROM pm_ahp_resource_access')} == {1}
        count = len(host.requests)
        assert not pm_access.visible(historical.db, principal, original)
        with pytest.raises(OperationError, match='NOT_FOUND'):
            ahp.get_observation(historical, principal, wid, oid)
        with pytest.raises(OperationError, match='NOT_FOUND'):
            historical.create(principal, **request(wid))
        page = ahp.list_observations(historical, principal, wid)
        assert page['visibility_filtered'] and [item['observation_id'] for item in page['observations']] == [newer['result']['observation_id']]
        assert not any(pm_access.event_visible(historical.db, principal, event)
            for event in historical.journal.api_events(0, 500)['events']
            if event['body'].get('operation_id') == original['operation_id'] or event['resource_id'] == original['operation_id'])
        assert dict(historical.db.execute('SELECT * FROM pm_ahp_observations WHERE observation_id=?', (oid,)).fetchone()) == frozen
        assert len(host.requests) == count
    finally:
        historical.journal.close()
