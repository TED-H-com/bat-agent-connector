"""Private append-only AHP creation evidence, explicitly installed by its future route.

Snapshot/ACK source: microsoft/vscode@2a59476c9bfcb90b3ddc372c36762471b7dfad1c,
src/vs/platform/agentHost/common/state/protocol/channels-session/{commands,state}.ts.
No BAT registry adoption, transport, startup hook or data-step migration.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from . import controller_authority, pm_workspace
from .ahp_observer import digest
from .errors import ResourceReadOnly

BINDING_KEYS = frozenset(('workspace_id work_id attempt_id adapter_binding host endpoint_incarnation '
    'session_uri chat_uri provider profile_digest config_digest managed_operation_id managed_step '
    'managed_receipt_digest workdir source_digest lifecycle_epoch work_started_at').split())


def refuse(code):
    raise ResourceReadOnly(code, 'AHP evidence does not permit a new effect')


def schema(journal):
    with journal.tx():
        journal.db.execute('''CREATE TABLE IF NOT EXISTS ahp_owned_sessions (
            resource_id TEXT NOT NULL, record_id TEXT NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY(resource_id,record_id))''')
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS ahp_owned_sessions_no_update
            BEFORE UPDATE ON ahp_owned_sessions BEGIN SELECT RAISE(ABORT, 'immutable AHP evidence'); END''')
        journal.db.execute('''CREATE TRIGGER IF NOT EXISTS ahp_owned_sessions_no_delete
            BEFORE DELETE ON ahp_owned_sessions BEGIN SELECT RAISE(ABORT, 'immutable AHP evidence'); END''')


def get(journal, resource_id, record_id):
    row = journal.db.execute('SELECT digest,body FROM ahp_owned_sessions WHERE resource_id=? AND record_id=?',
                             (resource_id, record_id)).fetchone()
    if row is None:
        return None
    value = json.loads(row['body'])
    if digest(value) != row['digest']:
        refuse('AHP_EVIDENCE_CONFLICT')
    return value


def _append(journal, resource_id, record_id, value):
    canonical = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    with journal.tx():
        old = get(journal, resource_id, record_id)
        if old is not None:
            if digest(old) != digest(value):
                refuse('AHP_EVIDENCE_CONFLICT')
            return old
        journal.db.execute('INSERT INTO ahp_owned_sessions VALUES(?,?,?,?)',
                           (resource_id, record_id, digest(value), canonical))
    return json.loads(canonical)


def reserve(journal, resource_id, binding, *, creation=None):
    authority = controller_authority.require()
    if pm_workspace.server_id(journal.db) != authority['server_id']:
        refuse('CONTROLLER_SERVER_CHANGED')
    controller_authority.require_binding(authority)
    keys = BINDING_KEYS if creation is None else BINDING_KEYS - {'chat_uri'}
    if (set(binding) != keys or authority.get('pm_workspace_id') != binding['workspace_id']
            or any(not isinstance(binding[k], str) or not binding[k] for k in keys - {'lifecycle_epoch', 'work_started_at'})
            or type(binding['lifecycle_epoch']) is not int or type(binding['work_started_at']) not in (float, int)):
        refuse('AHP_BINDING_UNPROVEN')
    with journal.tx():
        old = get(journal, resource_id, 'reserved')
        if creation is not None:
            validate_creation(binding, creation, fresh=old is None)
            _append(journal, 'create:' + creation['operation_id'], 'reservation', {'resource_id': resource_id})
            # A second resource ID must not conceal an earlier reservation or unknown create.
            _append(journal, 'uri:' + digest([binding['host'], binding['session_uri']]),
                    'reservation', {'resource_id': resource_id})
        incarnation = old['intent_controller_incarnation'] if old else uuid.uuid4().hex
        # Deliberately not the require_entry projection: reservation is only intent.
        return _append(journal, resource_id, 'reserved', {'binding': binding,
            'server_id': authority['server_id'], 'intent_controller_authority': authority,
            'intent_controller_incarnation': incarnation, **({'creation': creation} if creation is not None else {})})


def require_absence(binding, proof, *, fresh=True):
    if (not isinstance(proof, dict) or proof.get('complete') is not True
            or not isinstance(proof.get('connection'), str) or not proof['connection']
            or proof.get('endpoint_incarnation') != binding['endpoint_incarnation']
            or type(proof.get('observed_at')) not in (float, int)
            or fresh and not 0 <= time.time() - proof['observed_at'] <= 30
            or not isinstance(proof.get('resources'), list) or len(proof['resources']) > 200
            or any(not isinstance(uri, str) for uri in proof['resources'])
            or binding['session_uri'] in proof['resources']):
        refuse('AHP_ABSENCE_UNPROVEN')


def validate_creation(binding, creation, *, fresh=True):
    try:
        if set(creation) != {'operation_id', 'parent_id', 'client_id', 'request', 'step_id', 'carrier', 'absence'}:
            raise ValueError
        request, carrier = creation['request'], creation['carrier']
        params = request['params']
        if (any(not isinstance(creation[k], str) or not creation[k] for k in ('operation_id', 'parent_id', 'client_id', 'step_id'))
                or request['jsonrpc'] != '2.0' or type(request['id']) not in (str, int)
                or request['method'] != 'createSession' or params['channel'] != binding['session_uri']
                or params['provider'] != binding['provider'] or params['workingDirectories'] != ['file://' + binding['workdir']]
                or digest(params.get('config', {})) != binding['config_digest']
                or params['activeClient']['clientId'] != creation['client_id']
                or carrier['creation_operation_id'] != creation['operation_id']
                or carrier['attempt_id'] != binding['attempt_id'] or carrier['host'] != binding['host']
                or carrier['cwd'] != binding['workdir']):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        refuse('AHP_CREATE_INTENT_UNPROVEN')
    require_absence(binding, creation['absence'], fresh=fresh)


def _intent(journal, resource_id):
    intent = get(journal, resource_id, 'reserved')
    if intent is None:
        refuse('AHP_CREATION_UNPROVEN')
    controller_authority.require_binding(intent['intent_controller_authority'])
    return intent


def create_sent(journal, resource_id, request, connection):
    intent = _intent(journal, resource_id)
    if 'creation' in intent and (request != intent['creation']['request'] or connection != intent['creation']['absence']['connection']):
        refuse('AHP_CREATE_INTENT_UNPROVEN')
    return _append(journal, resource_id, 'create_sent', {'request_digest': digest(request), 'connection': connection})


@dataclass(frozen=True)
class CreationEvidence:
    ack_connection: str
    snapshot_connection: str
    request: dict
    response: dict
    snapshot: dict
    chat_snapshot: dict | None
    catalog_summary: dict | None = None


def record_created(journal, resource_id, evidence):
    intent = _intent(journal, resource_id)
    fence = get(journal, resource_id, 'create_sent')
    b = intent['binding']
    try:
        if type(evidence) is not CreationEvidence or fence is None:
            raise ValueError
        request, response, snapshot = evidence.request, evidence.response, evidence.snapshot
        params, state = request['params'], snapshot['state']
        chats = [chat['resource'] for chat in state['chats']]
        chat_uri = state.get('defaultChat') or (chats[0] if len(chats) == 1 else None)
        valid = (evidence.ack_connection == evidence.snapshot_connection == fence['connection']
            and bool(evidence.ack_connection) and digest(request) == fence['request_digest']
            and request['method'] == 'createSession' and params['channel'] == b['session_uri']
            and params['provider'] == b['provider'] and params['workingDirectories'] == ['file://' + b['workdir']]
            and digest(params.get('config', {})) == b['config_digest']
            and response == {'jsonrpc': '2.0', 'id': request['id'], 'result': None}
            and snapshot['resource'] == b['session_uri'] and state['provider'] == b['provider']
            and state['workingDirectories'] == params['workingDirectories'] and digest(state['config']['values']) == b['config_digest']
            and state['lifecycle'] == 'ready' and bool(chat_uri) and chat_uri in chats
            and b.get('chat_uri', chat_uri) == chat_uri
            and (evidence.chat_snapshot is None or
                 evidence.chat_snapshot['resource'] == evidence.chat_snapshot['state']['resource'] == chat_uri))
        if not valid:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        refuse('AHP_CREATION_UNPROVEN')
    summary = evidence.catalog_summary
    if summary is not None and (summary.get('resource') != b['session_uri'] or summary.get('provider') != b['provider']):
        refuse('AHP_CREATION_UNPROVEN')
    native = summary.get('createdAt') if summary else None
    if native is not None and (not isinstance(native, str) or not native):
        refuse('AHP_CREATION_UNPROVEN')
    receipt = _append(journal, resource_id, 'created', {**intent, 'binding': {**b, 'chat_uri': chat_uri},
        'controller_authority': intent['intent_controller_authority'],
        'controller_incarnation': intent['intent_controller_incarnation'],
        'native_created_at': native, 'creation_evidence_level': 'same_connection_catalog_timestamp' if native else 'endpoint_only',
        'request_digest': fence['request_digest'], 'response_digest': digest(response), 'create_send_fence': fence,
        'readback_coverage': {'session': digest(snapshot),
                              **({'chat': digest(evidence.chat_snapshot)} if evidence.chat_snapshot is not None else {}),
                              'catalog': digest(summary) if summary else None, 'same_connection': True}})
    if evidence.chat_snapshot is not None:
        bind_chat(journal, resource_id, evidence.chat_snapshot)
    return receipt


def bind_chat(journal, resource_id, snapshot):
    receipt = get(journal, resource_id, 'created')
    controller_authority.require_entry(receipt)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('state'), dict):
        refuse('AHP_CHAT_UNPROVEN')
    old = get(journal, resource_id, 'chat_bound')
    if old is not None and snapshot.get('resource') != old['chat_uri']:
        refuse('AHP_EVIDENCE_CONFLICT')
    if (snapshot.get('resource') != receipt['binding']['chat_uri']
            or snapshot.get('state', {}).get('resource') != receipt['binding']['chat_uri']):
        refuse('AHP_CHAT_UNPROVEN')
    return _append(journal, resource_id, 'chat_bound', {'created_digest': digest(receipt),
        'chat_uri': receipt['binding']['chat_uri'], 'snapshot_digest': digest(snapshot)})


def owned(journal, resource_id, live):
    receipt = get(journal, resource_id, 'created')
    controller_authority.require_entry(receipt)
    chat = get(journal, resource_id, 'chat_bound')
    if not chat or chat['created_digest'] != digest(receipt) or chat['chat_uri'] != receipt['binding']['chat_uri']:
        refuse('AHP_CHAT_UNPROVEN')
    if live['binding']['endpoint_incarnation'] != receipt['binding']['endpoint_incarnation']:
        refuse('AHP_ENDPOINT_CHANGED')
    if live['native_created_at'] != receipt['native_created_at']:
        refuse('AHP_NATIVE_IDENTITY_CHANGED')
    if live['binding'] != receipt['binding']:
        refuse('AHP_BINDING_CHANGED')
    return receipt


def pending(journal, resource_id):
    return journal.db.execute("SELECT 1 FROM ahp_owned_sessions WHERE resource_id=? AND record_id LIKE 'sent:%' LIMIT 1",
                              (resource_id,)).fetchone() is not None


def sent_fence(journal, resource_id, operation_id, turn_id, frame_digest):
    if pending(journal, resource_id):
        refuse('AHP_SENT_UNKNOWN')
    return _append(journal, resource_id, 'sent:' + operation_id,
        {'operation_id': operation_id, 'turn_id': turn_id, 'frame_digest': frame_digest, 'state': 'unknown'})
