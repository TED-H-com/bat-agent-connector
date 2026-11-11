"""Uninstalled create capability: real Work service, disk Git and Unix peer.

Wire shapes are synthetic, from the provenance-labelled pinned AHP fixture.
A test-only handoff builder installs the unregistered adapter boundary; the real
Work handler still admits the child and records its immutable parent relation.
"""
import asyncio
import copy
import json
import sqlite3
import time
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import ahp_owned_resources as ledger
from bat_agent_connector import ahp_owned_transport as transport
from bat_agent_connector import api_auth, pm_source_lineage, pm_work_start
from bat_agent_connector import controller_authority as authority
from bat_agent_connector import native_carrier as carrier
from bat_agent_connector import resource_policy as policy
from bat_agent_connector.errors import ResourceReadOnly
from bat_agent_connector.operations import ActionDef, OpContext
from tests.test_ahp_owned import GOLDEN
from tests.test_pm_work_start import (
    _hold_before_handoff,
    pm,  # noqa: F401
    ready,  # noqa: F401
    start_ready,  # noqa: F401
    world,  # noqa: F401
)


def test_no_production_caller_installs_creation_authority():
    source = Path(policy.__file__).parent
    assert [p.name for p in source.glob('*.py') if 'mint_ahp_create_grant(' in p.read_text()] == ['resource_policy.py']


@pytest.fixture
async def prepared(start_ready, monkeypatch):  # noqa: F811
    w = start_ready
    ops = w['d'].ops
    original = pm_work_start._handoff_request
    def handoff(*args):
        return {**original(*args), 'action': 'synthetic.ahp.create'}
    def admit(service, principal, target, params, pre):
        return {'controller_authority': authority.binding(service.db, principal,
            pm_workspace_id=params['pm_package_ref']['workspace_id'])}
    async def held(ctx):
        raise AssertionError('Only the policy seam is exercised in this fixture')
    ops.register(ActionDef('synthetic.ahp.create', 'start', 'Synthetic handoff', held, admit,
                           ('repository', 'host', 'workspace_id')))
    monkeypatch.setattr(pm_work_start, '_handoff_request', handoff)
    parent_id, _ = await _hold_before_handoff(w)
    await ops._execute(parent_id)
    parent = ops.get(parent_id)
    ctx = OpContext(ops, ops.get(parent['result']['children']['handoff']))
    intent = pm_work_start._intent(parent)
    token = api_auth.issue(ops.db, ctx.actor, ['observe', 'operate', 'manage', 'start'])
    principal = api_auth.authenticate(ops.db, token, 'unused')
    bound = authority.operation_binding(ctx.op)
    async def check():
        ctx.require_controller()
    with authority.use(bound, ops.journal), pm_source_lineage.execution(ops, ctx.operation_id):
        receipt = await carrier.prepare(ctx, carrier.FixedSource('h1', 'o/r', w['sha'], str(w['remote']), 'refs/heads/main'),
            attempt_id='att_' + parent_id[3:], managed_root=str(w['managed']),
            artifact_manifest_digest=carrier.EMPTY_MANIFEST, check=check)
        directory = {'host': 'h1', 'path': receipt.reference()['cwd'], 'creation_ref': receipt.reference()}
        ctx.effect('ahp.directory.receipt', lambda: directory)
    b = {'workspace_id': w['workspace_id'], 'work_id': intent['work_item_id'], 'attempt_id': 'att_' + parent_id[3:],
        'adapter_binding': 'synthetic-linux-profile', 'host': 'h1', 'endpoint_incarnation': 'synthetic-editor',
        'session_uri': 'ahp-session:/' + uuid.uuid4().hex, 'provider': 'copilotcli',
        'profile_digest': ledger.digest({'profile': 'synthetic'}), 'config_digest': ledger.digest({}),
        'managed_operation_id': ctx.operation_id, 'managed_step': 'ahp.directory.receipt',
        'managed_receipt_digest': ledger.digest(directory), 'workdir': directory['path'],
        'source_digest': ledger.digest(intent['code_source']), 'lifecycle_epoch': intent['execution_epoch'],
        'work_started_at': parent['created_at']}
    request = copy.deepcopy(GOLDEN['create'])
    request['params'].update(channel=b['session_uri'], workingDirectories=['file://' + b['workdir']])
    absence = {'connection': 'synthetic-connection', 'endpoint_incarnation': b['endpoint_incarnation'],
               'observed_at': time.time(), 'complete': True, 'resources': []}
    create = {'operation_id': ctx.operation_id, 'parent_id': parent_id, 'client_id': request['params']['activeClient']['clientId'],
              'request': request, 'step_id': 'ahp.create', 'carrier': receipt.reference(), 'absence': absence}
    live = {'binding': b, 'busy': False, 'permission_allowed': True, 'cancelled': False,
            'native_created_at': None, 'absence': copy.deepcopy(absence)}
    return SimpleNamespace(ctx=ctx, principal=principal, bound=bound, journal=ops.journal, live=live,
        receipt=receipt, request=request, create=create, rid=uuid.uuid4().hex, runner=w['runner'])


@pytest.fixture
def creation(prepared):
    with authority.use(prepared.bound, prepared.journal), pm_source_lineage.execution(prepared.ctx.service, prepared.ctx.operation_id):
        ledger.schema(prepared.journal)
        ledger.reserve(prepared.journal, prepared.rid, prepared.live['binding'], creation=prepared.create)
        yield prepared


@pytest.fixture
async def peer(creation, tmp_path):
    w = creation
    state = {'frames': [], 'fences': [], 'mutate': lambda barrier: None}
    arrived = asyncio.Event()
    async def receive(reader, writer):
        try:
            while line := await reader.readline():
                state['frames'].append(json.loads(line))
                arrived.set()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_unix_server(receive, path=str(tmp_path / 'a'))
    connections = []
    async def connect():
        _, writer = await asyncio.open_unix_connection(str(tmp_path / 'a'))
        connections.append(writer)
        state['mutate']('connect')
        class Writer:
            def write(self, payload):
                with sqlite3.connect(w.journal.path) as db:
                    state['fences'].append(db.execute("SELECT COUNT(*) FROM ahp_owned_sessions WHERE record_id='create_sent'").fetchone()[0])
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


async def mint(w):
    return await policy.mint_ahp_create_grant(w.ctx, w.rid, w.principal, w.request,
        carrier=w.receipt, current=lambda: copy.deepcopy(w.live))


async def send(w, peer, grant, frame=None):
    await transport.guarded_frame(w.ctx, grant, frame or w.request, current=lambda: copy.deepcopy(w.live),
        **{k: peer[k] for k in ('connect', 'semaphore', 'fresh_read')})


def evidence(w, *, chat=None):
    b = w.live['binding']
    chat_uri = 'ahp-chat:/advertised-chat'
    snapshot = {'resource': b['session_uri'], 'fromSeq': 3, 'state': {'provider': b['provider'],
        'workingDirectories': w.request['params']['workingDirectories'], 'config': {'values': {}},
        'lifecycle': 'ready', 'defaultChat': chat_uri, 'chats': [{'resource': chat_uri}]}}
    return ledger.CreationEvidence('synthetic-connection', 'synthetic-connection', w.request,
        GOLDEN['create_reply'], snapshot, chat)


def bind(w):
    receipt = ledger.record_created(w.journal, w.rid, evidence(w))
    w.live['binding'] = receipt['binding']
    chat = {'resource': receipt['binding']['chat_uri'], 'state': {'resource': receipt['binding']['chat_uri']}}
    ledger.bind_chat(w.journal, w.rid, chat)
    return chat


async def test_create_fence_and_lost_reply_never_resend(creation, peer):
    w = creation
    value = await mint(w)
    assert w.runner.phases[-1] == 'read.carrier'
    await send(w, peer, value)
    assert peer['frames'] == [w.request] and peer['fences'] == [1]
    fence = ledger.get(w.journal, w.rid, 'create_sent')
    assert fence['request_digest'] == ledger.digest(w.request)
    code = None
    try:
        await send(w, peer, value)
    except ResourceReadOnly as error:
        code = error.code
    assert len(peer['frames']) == 1 and code == 'AHP_SENT_UNKNOWN'
    with pytest.raises(ResourceReadOnly, match='AHP_SENT_UNKNOWN'):
        await mint(w)
    assert ledger.get(w.journal, w.rid, 'created') is None


async def test_grant_construction_and_both_cross_kind_uses_refuse(creation, peer):
    w = creation
    value = await mint(w)
    for forged in (replace(value), copy.copy(value), policy.AhpCreateGrant(**vars(value))):
        with pytest.raises(ResourceReadOnly, match='AHP_GRANT_REQUIRED'):
            await send(w, peer, forged)
    altered = await mint(w)
    object.__setattr__(altered, 'connection', 'changed')
    with pytest.raises(ResourceReadOnly, match='AHP_GRANT_REQUIRED'):
        await send(w, peer, altered)
    with pytest.raises(ResourceReadOnly, match='AHP_GRANT_MISMATCH'):
        await send(w, peer, value, GOLDEN['turn'])
    assert peer['frames'] == []
    await send(w, peer, value)
    bind(w)
    turn = copy.deepcopy(GOLDEN['turn'])
    turn['params']['channel'] = w.live['binding']['chat_uri']
    with authority.use(authority.binding(w.journal.db, w.principal, pm_workspace_id=w.live['binding']['workspace_id']), w.journal):
        value = policy.mint_ahp_grant(w.ctx, w.rid, w.principal, turn, current=lambda: copy.deepcopy(w.live))
        with pytest.raises(ResourceReadOnly, match='AHP_GRANT_MISMATCH'):
            await send(w, peer, value)
    assert len(peer['frames']) == 1


async def test_chat_binding_is_required_append_only_and_never_preselected(creation, peer):
    w = creation
    assert 'chat_uri' not in ledger.get(w.journal, w.rid, 'reserved')['binding']
    with pytest.raises(ResourceReadOnly, match='CONTROLLER_OWNER_UNPROVEN'):
        policy.mint_ahp_grant(w.ctx, w.rid, w.principal, GOLDEN['turn'], current=lambda: w.live)
    with pytest.raises(ResourceReadOnly):
        authority.require_entry(ledger.get(w.journal, w.rid, 'reserved'))
    await send(w, peer, await mint(w))
    receipt = ledger.record_created(w.journal, w.rid, evidence(w))
    w.live['binding'] = receipt['binding']
    with pytest.raises(ResourceReadOnly, match='AHP_CHAT_UNPROVEN'):
        ledger.owned(w.journal, w.rid, w.live)
    chat = bind(w)
    first = ledger.get(w.journal, w.rid, 'chat_bound')
    assert ledger.bind_chat(w.journal, w.rid, chat) == first
    assert first['created_digest'] == ledger.digest(receipt)
    with pytest.raises(ResourceReadOnly, match='AHP_EVIDENCE_CONFLICT'):
        ledger.bind_chat(w.journal, w.rid, {'resource': 'ahp-chat:/other', 'state': {'resource': 'ahp-chat:/other'}})
    assert ledger.get(w.journal, w.rid, 'chat_bound') == first
    assert ledger.owned(w.journal, w.rid, w.live) == receipt


@pytest.mark.parametrize('change', ['no_snapshot', 'foreign_uri', 'creating', 'creationFailed', 'error'])
async def test_ack_and_exact_snapshot_are_both_required(creation, peer, change):
    w = creation
    await send(w, peer, await mint(w))
    proof = evidence(w)
    if change == 'no_snapshot':
        proof = replace(proof, snapshot={})
    elif change == 'foreign_uri':
        proof.snapshot['resource'] = 'ahp-session:/persons-session'
    elif change == 'error':
        proof = replace(proof, response=GOLDEN['error_SessionAlreadyExists'])
    else:
        proof.snapshot['state']['lifecycle'] = change
    with pytest.raises(ResourceReadOnly, match='AHP_CREATION_UNPROVEN'):
        ledger.record_created(w.journal, w.rid, proof)
    assert ledger.get(w.journal, w.rid, 'created') is None and len(peer['frames']) == 1


@pytest.mark.parametrize('barrier', ['connect', 'semaphore', 'fresh_read', 'carrier'])
@pytest.mark.parametrize('change', ['scope', 'membership', 'busy', 'endpoint', 'attempt', 'cancel'])
async def test_create_rechecks_each_await(creation, peer, barrier, change):
    w = creation
    value = await mint(w)
    def mutate(where):
        if where != barrier:
            return
        if change == 'scope':
            w.journal.db.execute("UPDATE api_principals SET scopes='[\"observe\"]'")
        elif change == 'membership':
            w.journal.db.execute('DELETE FROM pm_workspace_members')
        elif change in {'endpoint', 'attempt'}:
            w.live['binding'][{'endpoint': 'endpoint_incarnation', 'attempt': 'attempt_id'}[change]] = 'changed'
        else:
            w.live[{'busy': 'busy', 'cancel': 'cancelled'}[change]] = True
    peer['mutate'] = mutate
    w.runner.after['read.carrier'] = lambda _: mutate('carrier')
    try:
        await send(w, peer, value)
    except Exception as error:
        assert getattr(error, 'code', None)
    assert peer['frames'] == [] and ledger.get(w.journal, w.rid, 'create_sent') is None


@pytest.mark.parametrize('change', ['attempt', 'host', 'client', 'occupied', 'incomplete', 'stale'])
def test_reservation_rejects_unproven_create_intents(creation, change):
    w = creation
    create = copy.deepcopy(w.create)
    binding = {**w.live['binding'], 'session_uri': 'ahp-session:/' + uuid.uuid4().hex}
    create['request']['params']['channel'] = binding['session_uri']
    if change in {'attempt', 'host'}:
        create['carrier'][{'attempt': 'attempt_id', 'host': 'host'}[change]] = 'other'
    elif change == 'client':
        create['client_id'] = 'other'
    elif change == 'occupied':
        create['absence']['resources'] = [binding['session_uri']]
    elif change == 'incomplete':
        create['absence']['complete'] = False
    else:
        create['absence']['observed_at'] -= 31
    code = 'AHP_CREATE_INTENT_UNPROVEN' if change in {'attempt', 'host', 'client'} else 'AHP_ABSENCE_UNPROVEN'
    with pytest.raises(ResourceReadOnly, match=code):
        ledger.reserve(w.journal, 'another', binding, creation=create)
    assert ledger.get(w.journal, 'another', 'reserved') is None


async def test_original_intent_replays_but_needs_fresh_absence(creation, monkeypatch):
    w = creation
    first = ledger.get(w.journal, w.rid, 'reserved')
    now = time.time() + 60
    monkeypatch.setattr(ledger, 'time', SimpleNamespace(time=lambda: now))
    assert ledger.reserve(w.journal, w.rid, w.live['binding'], creation=w.create) == first
    with pytest.raises(ResourceReadOnly, match='AHP_ABSENCE_UNPROVEN'):
        await mint(w)
    w.live['absence']['observed_at'] = now
    assert await mint(w)


def test_same_uri_cannot_hide_another_intent(creation):
    w = creation
    create = copy.deepcopy(w.create)
    create['operation_id'] = create['carrier']['creation_operation_id'] = 'op_' + uuid.uuid4().hex
    with pytest.raises(ResourceReadOnly, match='AHP_EVIDENCE_CONFLICT'):
        ledger.reserve(w.journal, 'replacement', w.live['binding'], creation=create)
    assert ledger.get(w.journal, 'replacement', 'reserved') is None


async def test_unknown_create_cannot_be_replaced_with_a_new_uri(creation, peer):
    w = creation
    await send(w, peer, await mint(w))
    binding = {**w.live['binding'], 'session_uri': 'ahp-session:/' + uuid.uuid4().hex}
    create = copy.deepcopy(w.create)
    create['request']['params']['channel'] = binding['session_uri']
    with pytest.raises(ResourceReadOnly, match='AHP_EVIDENCE_CONFLICT'):
        ledger.reserve(w.journal, 'replacement', binding, creation=create)
    assert ledger.get(w.journal, 'replacement', 'reserved') is None and len(peer['frames']) == 1


async def test_foreign_admin_and_post_fence_loss(creation, peer):
    w = creation
    value = await mint(w)
    admin = authority.binding(w.journal.db, api_auth.Principal('other-admin', frozenset(api_auth.SCOPES), admin=True))
    with authority.use(admin, w.journal), pytest.raises(ResourceReadOnly, match='FOREIGN_CONTROLLER'):
        await send(w, peer, value)
    assert peer['frames'] == []
    connect = peer['connect']
    async def failing():
        writer = await connect()
        drain = writer.drain
        async def lost():
            await drain()
            raise ConnectionError('Synthetic lost reply')
        writer.drain = lost
        return writer
    peer['connect'] = failing
    with pytest.raises(ResourceReadOnly, match='AHP_SENT_UNKNOWN'):
        await send(w, peer, value)
    assert peer['fences'] == [1] and len(peer['frames']) == 1
    with pytest.raises(ResourceReadOnly, match='AHP_SENT_UNKNOWN'):
        await mint(w)


@pytest.mark.parametrize('change', ['attempt', 'carrier', 'unavailable', 'absence', 'occupied', 'child'])
async def test_unproven_create_has_zero_frames(creation, peer, change):
    w = creation
    if change == 'attempt':
        w.live['binding']['attempt_id'] = 'other-attempt'
    elif change == 'carrier':
        from tests.test_checkpoints import git
        git(w.receipt.reference()['cwd'], 'checkout', '-qb', 'foreign')
    elif change == 'unavailable':
        w.runner.available = lambda _: False
    elif change == 'absence':
        w.live['absence']['complete'] = False
    elif change == 'occupied':
        w.live['absence']['resources'] = [w.request['params']['channel']]
    else:
        w.ctx.op = {**w.ctx.op, 'operation_id': w.create['parent_id']}
    try:
        await send(w, peer, await mint(w))
    except Exception as error:
        assert getattr(error, 'code', None)
    assert peer['frames'] == [] and ledger.get(w.journal, w.rid, 'create_sent') is None
