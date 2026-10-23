"""Private append-only AHP creation evidence, explicitly installed by its future route.

Snapshot/ACK source: microsoft/vscode@2a59476c9bfcb90b3ddc372c36762471b7dfad1c,
src/vs/platform/agentHost/common/state/protocol/channels-session/{commands,state}.ts.
No BAT registry adoption, transport, startup hook or data-step migration.
"""
from __future__ import annotations

import json
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


def reserve(journal, resource_id, binding):
    authority = controller_authority.require()
    if pm_workspace.server_id(journal.db) != authority['server_id']:
        refuse('CONTROLLER_SERVER_CHANGED')
    controller_authority.require_binding(authority)
    if (set(binding) != BINDING_KEYS or authority.get('pm_workspace_id') != binding['workspace_id']
            or any(not isinstance(binding[k], str) or not binding[k] for k in BINDING_KEYS - {'lifecycle_epoch', 'work_started_at'})
            or type(binding['lifecycle_epoch']) is not int or type(binding['work_started_at']) not in (float, int)):
        refuse('AHP_BINDING_UNPROVEN')
    with journal.tx():
        old = get(journal, resource_id, 'reserved')
        incarnation = old['intent_controller_incarnation'] if old else uuid.uuid4().hex
        # Deliberately not the require_entry projection: reservation is only intent.
        return _append(journal, resource_id, 'reserved', {'binding': binding,
            'server_id': authority['server_id'], 'intent_controller_authority': authority,
            'intent_controller_incarnation': incarnation})


def _intent(journal, resource_id):
    intent = get(journal, resource_id, 'reserved')
    if intent is None:
        refuse('AHP_CREATION_UNPROVEN')
    controller_authority.require_binding(intent['intent_controller_authority'])
    return intent


def create_sent(journal, resource_id, request, connection):
    _intent(journal, resource_id)
    return _append(journal, resource_id, 'create_sent', {'request_digest': digest(request), 'connection': connection})


@dataclass(frozen=True)
class CreationEvidence:
    ack_connection: str
    snapshot_connection: str
    request: dict
    response: dict
    snapshot: dict
    chat_snapshot: dict
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
        valid = (evidence.ack_connection == evidence.snapshot_connection == fence['connection']
            and bool(evidence.ack_connection) and digest(request) == fence['request_digest']
            and request['method'] == 'createSession' and params['channel'] == b['session_uri']
            and params['provider'] == b['provider'] and params['workingDirectories'] == ['file://' + b['workdir']]
            and digest(params.get('config', {})) == b['config_digest']
            and response == {'jsonrpc': '2.0', 'id': request['id'], 'result': None}
            and snapshot['resource'] == b['session_uri'] and state['provider'] == b['provider']
            and state['workingDirectories'] == params['workingDirectories'] and digest(state['config']['values']) == b['config_digest']
            and state['lifecycle'] == 'ready' and state['defaultChat'] == b['chat_uri']
            and any(chat['resource'] == b['chat_uri'] for chat in state['chats'])
            and evidence.chat_snapshot['resource'] == evidence.chat_snapshot['state']['resource'] == b['chat_uri'])
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
    return _append(journal, resource_id, 'created', {**intent,
        'controller_authority': intent['intent_controller_authority'],
        'controller_incarnation': intent['intent_controller_incarnation'],
        'native_created_at': native, 'creation_evidence_level': 'same_connection_catalog_timestamp' if native else 'endpoint_only',
        'request_digest': fence['request_digest'], 'response_digest': digest(response), 'create_send_fence': fence,
        'readback_coverage': {'session': digest(snapshot), 'chat': digest(evidence.chat_snapshot),
                              'catalog': digest(summary) if summary else None, 'same_connection': True}})


def owned(journal, resource_id, live):
    receipt = get(journal, resource_id, 'created')
    controller_authority.require_entry(receipt)
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
