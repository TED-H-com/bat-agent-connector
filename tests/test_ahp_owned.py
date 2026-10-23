"""Synthetic pinned AHP shapes with a real journal and disk-backed Unix peer.

Wire source: microsoft/vscode@2a59476c9bfcb90b3ddc372c36762471b7dfad1c,
src/vs/platform/agentHost/common/state/protocol/channels-session/{commands,state}.ts.
No editor, account, managed-directory creation or real write is exercised.
"""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from bat_agent_connector import ahp_owned_resources as ledger
from bat_agent_connector import ahp_owned_transport as transport
from bat_agent_connector import api_auth, pm_source_lineage, pm_workspace, registry
from bat_agent_connector import controller_authority as authority
from bat_agent_connector import resource_policy as policy
from bat_agent_connector.errors import ResourceReadOnly
from bat_agent_connector.operations import OpContext, OperationService
from bat_agent_connector.task_journal import Journal

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux Unix endpoint only')
ROWS = [json.loads(line) for line in (Path(__file__).parent / 'fixtures/ahp-owned/protocol-shapes.jsonl')
        .read_text().splitlines()]
GOLDEN = {r['name']: r['frame'] for r in ROWS if r['version'] == '0.10.0' and 'frame' in r}
RID, OID, WID = 'synthetic-resource', 'op_' + '1' * 32, 'ws_' + '2' * 32


@pytest.fixture
def world(tmp_path):
    journal = Journal(tmp_path / 'journal.db')
    pm_workspace.schema(journal)
    db = journal.db
    db.execute('INSERT INTO pm_workspaces VALUES(?,?,?,?,?,?,?,?,?)',
        (WID, pm_workspace.server_id(db), 'Synthetic', '[]', 1, 'writer', 1, 1, 'workspace-create'))
    token = api_auth.issue(db, 'writer', ['observe', 'operate', 'manage', 'start'])
    principal = api_auth.authenticate(db, token, 'unused')
    bound = authority.binding(db, principal)
    db.execute('INSERT INTO pm_workspace_members VALUES(?,?,?,?,?,?)',
        (WID, 'writer', 'contributor', bound['controller_id'], 1, 1))
    bound = authority.binding(db, principal, pm_workspace_id=WID)
    db.execute('''INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,
        target,params,preconditions,status,external_refs,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (OID, 'writer', 'test', 'synthetic-intent', 'digest', 'synthetic.ahp.seam', '{}', '{}', '{}', 'running',
         json.dumps({'admission_binding': {'controller_authority': bound}}), time.time(), time.time()))
    managed = {'host': 'synthetic-host', 'path': '/srv/managed/synthetic', 'creation_ref': 'synthetic-marker'}
    db.execute('INSERT INTO operation_steps VALUES(?,?,?,?,?,?,?,?,?,?)',
        (OID, 1, 'managed-directory', 'succeeded', '{}', json.dumps(managed), None, None, 1, 2))
    binding = {'workspace_id': WID, 'work_id': 'synthetic-work', 'attempt_id': 'synthetic-attempt',
        'adapter_binding': 'synthetic-linux-profile', 'host': managed['host'], 'endpoint_incarnation': 'instance-one',
        'session_uri': GOLDEN['create']['params']['channel'], 'chat_uri': GOLDEN['turn']['params']['channel'],
        'provider': 'copilotcli', 'profile_digest': 'a' * 64, 'config_digest': ledger.digest({}),
        'managed_operation_id': OID, 'managed_step': 'managed-directory', 'managed_receipt_digest': ledger.digest(managed),
        'workdir': managed['path'], 'source_digest': 'b' * 64, 'lifecycle_epoch': 0, 'work_started_at': time.time()}
    ops = OperationService(journal)
    ctx = OpContext(ops, ops._row(OID))
    live = {'binding': binding, 'busy': False, 'permission_allowed': True, 'cancelled': False, 'native_created_at': None}
    ledger.schema(journal)
    with authority.use(bound, journal):
        yield journal, principal, ctx, live
    journal.close()


def producer(world, *, record=True):
    """Test-only creation producer: original ACK and exact same-connection snapshots."""
    journal, _, _, live = world
    binding = live['binding']
    ledger.reserve(journal, RID, binding)
    request = copy.deepcopy(GOLDEN['create'])
    request['params']['workingDirectories'] = ['file://' + binding['workdir']]
    ledger.create_sent(journal, RID, request, 'synthetic-connection')
    snapshot = {'resource': binding['session_uri'], 'fromSeq': 3, 'state': {
        'provider': binding['provider'], 'workingDirectories': request['params']['workingDirectories'],
        'title': 'Synthetic', 'activeClients': [],
        'config': {'schema': {'type': 'object', 'properties': {}}, 'values': {}}, 'lifecycle': 'ready', 'status': 1,
        'chats': [{'resource': binding['chat_uri'], 'title': 'Synthetic', 'status': 1, 'modifiedAt': '2026-01-01T00:00:00Z'}],
        'defaultChat': binding['chat_uri']}}
    evidence = ledger.CreationEvidence('synthetic-connection', 'synthetic-connection', request,
        copy.deepcopy(GOLDEN['create_reply']), snapshot,
        {'resource': binding['chat_uri'], 'fromSeq': 3, 'state': {'resource': binding['chat_uri'], 'status': 1,
            'title': 'Synthetic', 'modifiedAt': '2026-01-01T00:00:00Z', 'turns': []}})
    if record:
        ledger.record_created(journal, RID, evidence)
    return evidence


def grant(world, *, resource_id=RID):
    _, principal, ctx, live = world
    return policy.mint_ahp_grant(ctx, resource_id, principal, GOLDEN['turn'], current=lambda: copy.deepcopy(live))


@pytest.fixture
async def peer(world, tmp_path):
    journal, _, _, _ = world
    state = {'frames': [], 'fences_before_write': [], 'mutate': lambda barrier: None}
    arrived = asyncio.Event()
    async def receive(reader, writer):
        try:
            while line := await reader.readline():
                state['frames'].append(json.loads(line))
                arrived.set()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_unix_server(receive, path=str(tmp_path / 's'))
    connections = []
    async def connect():
        reader, writer = await asyncio.open_unix_connection(str(tmp_path / 's'))
        connections.append(writer)
        state['mutate']('connect')
        class Writer:
            def write(self, payload):
                with sqlite3.connect(journal.path) as db:
                    state['fences_before_write'].append(db.execute(
                        "SELECT COUNT(*) FROM ahp_owned_sessions WHERE record_id LIKE 'sent:%'").fetchone()[0])
                writer.write(payload + b'\n')
            async def drain(self):
                await writer.drain()
                await asyncio.wait_for(arrived.wait(), 2)
        return Writer()
    class Permit:
        async def __aenter__(self):
            await asyncio.sleep(0)
            state['mutate']('semaphore')
        async def __aexit__(self, *args):
            return False
    async def fresh():
        await asyncio.sleep(0)
        state['mutate']('fresh_read')
    state.update(connect=connect, semaphore=Permit(), fresh_read=fresh)
    yield state
    for writer in connections:
        writer.close()
        await writer.wait_closed()
    server.close()
    await server.wait_closed()


async def send(world, peer, value):
    _, _, ctx, live = world
    return await transport.guarded_frame(ctx, value, GOLDEN['turn'], current=lambda: copy.deepcopy(live),
        connect=peer['connect'], semaphore=peer['semaphore'], fresh_read=peer['fresh_read'])


def test_explicit_schema_append_only_and_typed_creation(world, tmp_path):
    journal, _, _, live = world
    empty = Journal(tmp_path / 'empty.db')
    assert not empty.db.execute("SELECT 1 FROM sqlite_master WHERE name='ahp_owned_sessions'").fetchone()
    version = empty.db.execute('PRAGMA user_version').fetchone()[0]
    ledger.schema(empty)
    ledger.schema(empty)
    assert empty.db.execute('PRAGMA user_version').fetchone()[0] == version
    empty.close()
    first = ledger.reserve(journal, RID, live['binding'])
    assert ledger.reserve(journal, RID, dict(reversed(list(live['binding'].items())))) == first
    with pytest.raises(ResourceReadOnly, match='AHP_EVIDENCE_CONFLICT'):
        ledger.reserve(journal, RID, {**live['binding'], 'work_id': 'changed'})
    with pytest.raises(ResourceReadOnly):
        authority.require_entry(first)
    with pytest.raises(ResourceReadOnly, match='CONTROLLER_OWNER_UNPROVEN'):
        grant(world)
    evidence = producer(world)
    assert ledger.record_created(journal, RID, evidence) == ledger.get(journal, RID, 'created')
    assert registry.list_entries() == []
    with pytest.raises(sqlite3.IntegrityError):
        journal.db.execute("UPDATE ahp_owned_sessions SET digest='changed'")
    with pytest.raises(sqlite3.IntegrityError):
        journal.db.execute('DELETE FROM ahp_owned_sessions')


@pytest.mark.parametrize('field', ['response', 'snapshot_connection', 'snapshot', 'chat_snapshot'])
def test_bad_creation_evidence_does_not_create_ownership(world, field):
    journal, _, _, _ = world
    evidence = producer(world, record=False)
    evidence = replace(evidence, **{field: 'different' if field == 'snapshot_connection' else {}})
    with pytest.raises(ResourceReadOnly, match='AHP_CREATION_UNPROVEN'):
        ledger.record_created(journal, RID, evidence)
    with pytest.raises(ResourceReadOnly, match='AHP_CREATION_UNPROVEN'):
        ledger.record_created(journal, RID, {})
    assert ledger.get(journal, RID, 'created') is None


async def test_manual_catalog_or_managed_path_never_confer_ownership(world, peer):
    # Current readback may name a person's URI under a managed root; there is no receipt.
    code = None
    try:
        await send(world, peer, grant(world))
    except ResourceReadOnly as error:
        code = error.code
    assert peer['frames'] == []
    assert code == 'CONTROLLER_OWNER_UNPROVEN'


async def test_foreign_admin_is_not_controller(world, peer):
    journal, _, _, _ = world
    producer(world)
    other = authority.binding(journal.db, api_auth.Principal('other-admin', frozenset(api_auth.SCOPES), admin=True))
    with authority.use(other, journal), pytest.raises(ResourceReadOnly, match='FOREIGN_CONTROLLER'):
        await send(world, peer, grant(world))
    assert peer['frames'] == []


async def test_grant_copies_and_external_construction_are_refused(world, peer):
    producer(world)
    original = grant(world)
    for forged in (replace(original), copy.copy(original), policy.AhpGrant(**vars(original))):
        with pytest.raises(ResourceReadOnly, match='AHP_GRANT_REQUIRED'):
            await send(world, peer, forged)
    assert peer['frames'] == []


@pytest.mark.parametrize('barrier', ['connect', 'semaphore', 'fresh_read'])
@pytest.mark.parametrize('change,code', [('scope', 'AHP_SCOPE_CHANGED'), ('member', 'CONTROLLER_MEMBERSHIP_CHANGED'),
    ('controller', 'FOREIGN_CONTROLLER')])
async def test_revocation_at_every_await_barrier(world, peer, barrier, change, code):
    journal, _, _, _ = world
    producer(world)
    value = grant(world)
    entered = []
    def mutate(where):
        if where != barrier:
            return
        if change == 'scope':
            journal.db.execute("UPDATE api_principals SET scopes='[\"observe\"]'")
        elif change == 'member':
            journal.db.execute("DELETE FROM pm_workspace_members WHERE actor='writer'")
        else:
            context = authority.use(authority.binding(journal.db, api_auth.Principal('changed', frozenset(api_auth.SCOPES))), journal)
            context.__enter__()
            entered.append(context)
    peer['mutate'] = mutate
    observed = None
    try:
        await send(world, peer, value)
    except ResourceReadOnly as error:
        observed = error.code
    finally:
        for context in reversed(entered):
            context.__exit__(None, None, None)
    assert peer['frames'] == []
    assert observed == code
    assert ledger.get(journal, RID, 'sent:' + OID) is None


@pytest.mark.parametrize('change,code', [('restart', 'AHP_ENDPOINT_CHANGED'), ('native', 'AHP_NATIVE_IDENTITY_CHANGED'),
    ('profile', 'AHP_BINDING_CHANGED'), ('work', 'AHP_BINDING_CHANGED'), ('attempt', 'AHP_BINDING_CHANGED'),
    ('source', 'AHP_BINDING_CHANGED'), ('busy', 'AHP_BUSY'), ('permission', 'AHP_PERMISSION_REQUIRED'),
    ('cancel', 'AHP_CANCELLED'), ('budget', 'AHP_BUDGET_EXPIRED')])
async def test_current_identity_and_execution_gates(world, peer, change, code):
    _, _, _, live = world
    producer(world)
    value = grant(world)
    def mutate(where):
        if where != 'fresh_read':
            return
        if change == 'restart':
            live['binding']['endpoint_incarnation'] = 'replacement-editor'
            live['native_created_at'] = '2026-01-02T00:00:00Z'
        elif change == 'native':
            live['native_created_at'] = '2026-01-01T00:00:00Z'
        elif change in {'profile', 'source'}:
            live['binding'][change + '_digest'] = 'c' * 64
        elif change in {'work', 'attempt'}:
            live['binding'][change + '_id'] = 'replacement'
        elif change == 'budget':
            live['binding']['work_started_at'] -= 1801
        else:
            live[{'busy': 'busy', 'permission': 'permission_allowed', 'cancel': 'cancelled'}[change]] = change != 'permission'
    peer['mutate'] = mutate
    with pytest.raises(ResourceReadOnly, match=code):
        await send(world, peer, value)
    assert peer['frames'] == []


async def test_fence_is_committed_before_write_and_unknown_is_never_resent(world, peer, monkeypatch):
    journal, _, _, _ = world
    producer(world)
    value = grant(world)
    gates = []
    original = pm_source_lineage.require_frame
    def lineage():
        gates.append('lineage')
        original()
    monkeypatch.setattr(pm_source_lineage, 'require_frame', lineage)
    await send(world, peer, value)
    assert gates and peer['fences_before_write'] == [1] and len(peer['frames']) == 1
    fence = ledger.get(journal, RID, 'sent:' + OID)
    assert fence['state'] == 'unknown' and fence['turn_id'] == GOLDEN['turn']['params']['action']['turnId']
    with pytest.raises(ResourceReadOnly, match='AHP_SENT_UNKNOWN'):
        await send(world, peer, value)
    assert len(peer['frames']) == 1 and ledger.get(journal, RID, 'sent:' + OID) == fence


async def test_lineage_refusal_precedes_fence(world, peer, monkeypatch):
    producer(world)
    value = grant(world)
    def refuse():
        raise ResourceReadOnly('SYNTHETIC_SOURCE_WITHDRAWN', 'Synthetic source withdrawn')
    monkeypatch.setattr(pm_source_lineage, 'require_frame', refuse)
    with pytest.raises(ResourceReadOnly, match='SYNTHETIC_SOURCE_WITHDRAWN'):
        await send(world, peer, value)
    assert peer['frames'] == [] and ledger.get(world[0], RID, 'sent:' + OID) is None


def test_native_creation_timestamp_comes_from_exact_catalog_readback(world):
    journal, _, _, live = world
    evidence = producer(world, record=False)
    summary = {'resource': live['binding']['session_uri'], 'provider': 'copilotcli', 'title': 'Synthetic',
        'status': 1, 'createdAt': '2026-01-01T00:00:00Z', 'modifiedAt': '2026-01-01T00:00:00Z'}
    saved = ledger.record_created(journal, RID, replace(evidence, catalog_summary=summary))
    assert saved['native_created_at'] == summary['createdAt']
    assert saved['creation_evidence_level'] == 'same_connection_catalog_timestamp'
    live['native_created_at'] = summary['createdAt']
    assert grant(world)


async def test_transport_loss_after_enqueue_stays_unknown(world, peer):
    producer(world)
    value = grant(world)
    connect = peer['connect']
    async def failing():
        writer = await connect()
        async def drain():
            raise ConnectionError('synthetic lost reply')
        writer.drain = drain
        return writer
    peer['connect'] = failing
    with pytest.raises(ResourceReadOnly, match='AHP_SENT_UNKNOWN'):
        await send(world, peer, value)
    assert ledger.get(world[0], RID, 'sent:' + OID)['state'] == 'unknown'
    with pytest.raises(ResourceReadOnly, match='AHP_SENT_UNKNOWN'):
        grant(world)


async def test_outer_transaction_cannot_leave_fence_uncommitted(world, peer):
    producer(world)
    value = grant(world)
    with world[0].tx(), pytest.raises(ResourceReadOnly, match='AHP_FENCE_NOT_DURABLE'):
        await send(world, peer, value)
    assert peer['frames'] == [] and ledger.get(world[0], RID, 'sent:' + OID) is None


def test_only_policy_mints_and_production_write_gate_stays_closed():
    from bat_agent_connector import ahp_protocol
    source = Path(policy.__file__).parent
    assert [p.name for p in source.glob('*.py') if 'mint_ahp_grant(' in p.read_text()] == ['resource_policy.py']
    with pytest.raises(ahp_protocol.WireRefusal, match='owned_writes_disabled'):
        ahp_protocol.require_owned_write()


async def test_restart_cannot_mint_from_a_later_catalog_identity(world, peer):
    producer(world)
    world[3]['binding']['endpoint_incarnation'] = 'new-editor'
    world[3]['native_created_at'] = '2026-02-01T00:00:00Z'
    code = None
    try:
        await send(world, peer, grant(world))
    except ResourceReadOnly as error:
        code = error.code
    assert peer['frames'] == [] and code == 'AHP_ENDPOINT_CHANGED'


def test_admin_never_bypasses_the_shared_ledger_owner_check(world):
    journal, _, _, live = world
    producer(world)
    admin = authority.binding(journal.db, api_auth.Principal('other-admin', frozenset(api_auth.SCOPES), admin=True))
    with authority.use(admin, journal), pytest.raises(ResourceReadOnly, match='FOREIGN_CONTROLLER'):
        ledger.owned(journal, RID, live)


@pytest.mark.parametrize('change,code', [('lifecycle', 'PM_LIFECYCLE_EPOCH_CHANGED'),
    ('coordinator', 'CONTROL_VERSION_CONFLICT'), ('directory', 'AHP_MANAGED_DIRECTORY_CHANGED'),
    ('credential', 'AHP_SCOPE_CHANGED')])
async def test_shared_durable_guards_are_current(world, peer, change, code):
    journal, _, ctx, _ = world
    producer(world)
    if change == 'coordinator':
        task = journal.submit(project='synthetic', host='synthetic-host', workspace='/srv/managed/synthetic',
            original_words='Synthetic task', acceptance='Synthetic', idempotency_key='synthetic')
        ctx.op['external_refs']['admission_binding'].update(task_id=task['task_id'], control_version=task['control_version'])
    value = grant(world)
    def mutate(where):
        if where != 'fresh_read':
            return
        if change == 'lifecycle':
            journal.db.execute('INSERT OR REPLACE INTO pm_lifecycle_heads VALUES(?,?,?,?,?,?,?)',
                (WID, 'active', 1, 1, None, time.time(), 'synthetic'))
        elif change == 'coordinator':
            journal.db.execute('UPDATE tasks SET control_version=control_version+1 WHERE task_id=?', (task['task_id'],))
        elif change == 'directory':
            journal.db.execute("UPDATE operation_steps SET response='{}' WHERE operation_id=?", (OID,))
        else:
            api_auth.revoke(journal.db, 'writer')
    peer['mutate'] = mutate
    with pytest.raises(Exception) as error:
        await send(world, peer, value)
    assert error.value.code == code and peer['frames'] == []
    assert ledger.get(journal, RID, 'sent:' + OID) is None


def test_reservation_cannot_cross_the_context_journal_server(world, tmp_path):
    other = Journal(tmp_path / 'other.db')
    try:
        ledger.schema(other)
        with pytest.raises(ResourceReadOnly, match='CONTROLLER_SERVER_CHANGED'):
            ledger.reserve(other, RID, world[3]['binding'])
        assert ledger.get(other, RID, 'reserved') is None
    finally:
        other.close()
