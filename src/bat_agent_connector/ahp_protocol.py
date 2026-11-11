"""Pure owned-work wire subset; no transport, authorization or write caller.

Sources: microsoft/agent-host-protocol@5f16d81bb7045b66d7bc768d244feab75002d943,
types/{common,channels-session,channels-chat}; microsoft/vscode@
2a59476c9bfcb90b3ddc372c36762471b7dfad1c, src/vs/platform/agentHost/common/state/protocol.
See docs/design/pm-ahp-observer.md for the pin and disabled-write boundary.
Decoding a shape never establishes ownership, completion or permission to send it.
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from types import MappingProxyType

PINNED_COMMITS = MappingProxyType({
    '1.0.0': '5f16d81bb7045b66d7bc768d244feab75002d943',
    '0.10.0': '2a59476c9bfcb90b3ddc372c36762471b7dfad1c',
})
# common/messages.ts: CommandMap versus Client/ServerNotificationMap.
RPC_METHODS = frozenset({'createSession', 'createChat', 'fetchTurns'})
NOTIFICATIONS = frozenset({'dispatchAction', 'action'})
OWNED_WRITES_ENABLED = False
# common/errors.ts; Conflict belongs to resource preconditions, not conditional turns.
ERROR_CODES = MappingProxyType({
    'ParseError': -32700, 'InvalidRequest': -32600, 'MethodNotFound': -32601,
    'InvalidParams': -32602, 'InternalError': -32603, 'SessionNotFound': -32001,
    'ProviderNotFound': -32002, 'SessionAlreadyExists': -32003, 'TurnInProgress': -32004,
    'UnsupportedProtocolVersion': -32005, 'AuthRequired': -32007, 'NotFound': -32008,
    'PermissionDenied': -32009, 'AlreadyExists': -32010, 'Conflict': -32011,
})
IDLE, ERROR, IN_PROGRESS, INPUT_NEEDED, IS_READ, IS_ARCHIVED = 1, 2, 8, 24, 32, 64


class WireRefusal(ValueError):
    """Stable local refusal; never includes peer payloads or credentials."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class DecodedFrame:
    kind: str
    method: str
    payload: object
    request_id: int | None


def require_owned_write():
    """No enable switch: later integration must supply the existing authority gates."""
    raise WireRefusal('owned_writes_disabled')


def _fields(value, required, optional=''):
    required, optional = set(required.split()), set(optional.split())
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise WireRefusal('invalid_shape')
    return value


def _text(value, *, empty=False):
    if not isinstance(value, str) or not empty and not value:
        raise WireRefusal('invalid_text')


def _integer(value):
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise WireRefusal('invalid_integer')


def _uri(value):
    _text(value)
    if not re.match(r'^[a-z][a-z0-9+.-]*:', value) or any(ord(c) < 33 or c in '\\{}' for c in value):
        raise WireRefusal('invalid_uri')


def _session(value, version, provider=None):
    _uri(value)
    match = re.fullmatch(r'([a-z][a-z0-9+.-]*):/([^/?#\\]+)', value)
    if version == '1.0.0':
        valid = bool(match and match[1] == 'ahp-session')
    else:
        valid = bool(match and not match[1].startswith('ahp-') and match[1] not in {
            'file', 'http', 'https', 'ws', 'wss', 'data', 'javascript', 'ftp', 'ssh', 'mailto'}
            and (provider is None or provider == match[1]))
    if not valid:
        raise WireRefusal('session_uri_refused')


def _chat(value, version):
    _uri(value)
    if re.fullmatch(r'ahp-chat:/[^/?#\\]+', value):
        return
    if version == '0.10.0' and re.fullmatch(r'.+/chat/[^/?#\\]+', value):
        _session(value.rsplit('/chat/', 1)[0], version)
        return
    raise WireRefusal('chat_uri_refused')


def decode_status(status):
    """SessionStatus is a bitset. Even observed idle is not an atomic write grant."""
    _integer(status)
    if status & ~(IDLE | ERROR | INPUT_NEEDED | IS_READ | IS_ARCHIVED):
        raise WireRefusal('status_bits_unsupported')
    busy = bool(status & IN_PROGRESS)
    return {'idle': bool(status & IDLE) and not busy, 'busy': busy,
            'input_needed': status & INPUT_NEEDED == INPUT_NEEDED, 'error': bool(status & ERROR),
            'is_read': bool(status & IS_READ), 'archived': bool(status & IS_ARCHIVED)}


def _optional_objects(value, names):
    # Provider config/metadata remain opaque. No tools, model or policy are inferred.
    for name in names.split():
        if name in value and not isinstance(value[name], dict):
            raise WireRefusal('invalid_object')


def _directories(value):
    if not isinstance(value, list):
        raise WireRefusal('invalid_directories')
    for uri in value:
        _uri(uri)


def _message(value, client):
    # Text-only input subset. Attachments/model/agent selections need separate validation.
    _fields(value, 'text origin', '_meta')
    _text(value['text'], empty=True)
    origin = _fields(value['origin'], 'kind')['kind']
    if origin not in ('user', 'agent', 'tool', 'automation', 'systemNotification'):
        raise WireRefusal('message_origin_unsupported')
    if client and origin != 'user':
        raise WireRefusal('client_message_refused')
    _optional_objects(value, '_meta')


def _request(method, params, version):
    if method == 'createSession':
        _fields(params, 'channel', 'provider workingDirectories config activeClient progressToken')
        if 'provider' in params:
            _text(params['provider'])
        _session(params['channel'], version, params.get('provider'))
        _optional_objects(params, 'config')
        if 'progressToken' in params:
            _text(params['progressToken'])
        if 'activeClient' in params:
            active = _fields(params['activeClient'], 'clientId tools', 'displayName')
            _text(active['clientId'])
            if 'displayName' in active:
                _text(active['displayName'], empty=True)
            if active['tools'] != []:
                raise WireRefusal('client_tools_unsupported')
    elif method == 'createChat':
        # Forks/side chats are outside this subset, not silently discarded.
        _fields(params, 'channel chat', 'initialMessage workingDirectories')
        _session(params['channel'], version)
        _chat(params['chat'], version)
        if 'initialMessage' in params:
            _message(params['initialMessage'], True)
    else:  # fetchTurns schedules chat/turnsLoaded; its RPC result is only {}.
        _fields(params, 'channel', 'cursor')
        _chat(params['channel'], version)
        if 'cursor' in params:
            _text(params['cursor'])
    if 'workingDirectories' in params:
        _directories(params['workingDirectories'])


def _action(action, client):
    if not isinstance(action, dict):
        raise WireRefusal('invalid_action')
    kind = action.get('type')
    if kind not in ('chat/turnStarted', 'chat/turnCancelled', 'chat/turnComplete', 'chat/error'):
        raise WireRefusal('action_unsupported')
    if client and kind not in ('chat/turnStarted', 'chat/turnCancelled'):
        raise WireRefusal('client_action_refused')
    if kind == 'chat/turnStarted':
        _fields(action, 'type turnId startedAt message', 'queuedMessageId _meta')
        _text(action['startedAt'])
        _message(action['message'], client)
        if 'queuedMessageId' in action:
            _text(action['queuedMessageId'])
    else:
        _fields(action, 'type turnId duration' + (' part' if kind == 'chat/error' else ''), '_meta')
        duration = action['duration']
        if type(duration) not in (int, float) or not 0 <= duration <= 2**53 - 1 or not math.isfinite(duration):
            raise WireRefusal('invalid_duration')
        if kind == 'chat/error':
            part = _fields(action['part'], 'kind error', 'resumable')
            if part['kind'] != 'error' or 'resumable' in part and type(part['resumable']) is not bool:
                raise WireRefusal('invalid_error_part')
            error = _fields(part['error'], 'errorType message', 'stack _meta')
            for name in ('errorType', 'message', 'stack'):
                if name in error:
                    _text(error[name], empty=True)
            _optional_objects(error, '_meta')
    _text(action['turnId'])
    _optional_objects(action, '_meta')


def _notification(method, params, version):
    client = method == 'dispatchAction'
    _fields(params, 'channel action ' + ('clientSeq' if client else 'serverSeq'),
            '' if client else 'origin rejectionReason')
    _chat(params['channel'], version)
    _integer(params['clientSeq' if client else 'serverSeq'])
    if 'origin' in params:
        origin = _fields(params['origin'], 'clientId clientSeq')
        _text(origin['clientId'])
        _integer(origin['clientSeq'])
    if 'rejectionReason' in params:
        _text(params['rejectionReason'], empty=True)
    _action(params['action'], client)


def _error(error):
    _fields(error, 'code message', 'data')
    if type(error['code']) is not int or error['code'] not in ERROR_CODES.values():
        raise WireRefusal('error_code_unsupported')
    _text(error['message'], empty=True)
    if error['code'] == ERROR_CODES['UnsupportedProtocolVersion']:
        data = _fields(error.get('data'), 'supportedVersions')
        if not isinstance(data['supportedVersions'], list):
            raise WireRefusal('invalid_versions')
        for version in data['supportedVersions']:
            _text(version)  # Ranges are advertisements, never automatic decoder selection.
    if error['code'] == ERROR_CODES['AuthRequired']:
        data = _fields(error.get('data'), 'resources')
        if not isinstance(data['resources'], list):
            raise WireRefusal('invalid_resources')
        for resource in data['resources']:
            if not isinstance(resource, dict):
                raise WireRefusal('invalid_resource')
            _text(resource.get('resource'))
    if error['code'] == ERROR_CODES['PermissionDenied'] and 'data' in error:
        data = _fields(error['data'], '', 'request')
        _optional_objects(data, 'request')  # Preserve only; never negotiate a grant.


def _decode(version, frame, response_to):
    if not isinstance(frame, dict) or frame.get('jsonrpc') != '2.0':
        raise WireRefusal('invalid_rpc')
    if 'method' in frame:
        method = frame['method']
        if not isinstance(method, str) or method not in RPC_METHODS | NOTIFICATIONS:
            raise WireRefusal('method_unsupported')
        if response_to is not None:
            raise WireRefusal('unexpected_response_context')
        if method in NOTIFICATIONS:
            if 'id' in frame:
                raise WireRefusal('notification_has_id')
            _fields(frame, 'jsonrpc method params')
            _notification(method, frame['params'], version)
            kind = 'notification'
        else:
            _fields(frame, 'jsonrpc id method params')
            _integer(frame['id'])
            _request(method, frame['params'], version)
            kind = 'request'
        payload = frame['params']
    else:
        # Negotiation failures precede decoder selection; successful initialize
        # remains the observer's contract, not an owned-write admission path.
        response_methods = RPC_METHODS | ({'initialize'} if 'error' in frame else set())
        if not isinstance(response_to, str) or response_to not in response_methods:
            raise WireRefusal('response_method_unsupported')
        method = response_to
        kind = 'error' if 'error' in frame else 'result'
        _fields(frame, 'jsonrpc id ' + kind)
        _integer(frame['id'])
        payload = frame[kind]
        if kind == 'error':
            _error(payload)
        elif (method == 'fetchTurns' and payload != {}) or (method != 'fetchTurns' and payload is not None):
            raise WireRefusal('invalid_result')
    return DecodedFrame(kind, method, copy.deepcopy(payload), frame.get('id'))


def decode_1_0(frame, *, response_to=None):
    return _decode('1.0.0', frame, response_to)


def decode_0_10(frame, *, response_to=None):
    return _decode('0.10.0', frame, response_to)


def decoder_for(version):
    """Select only by negotiated wire version, never a distribution/serverInfo string."""
    if version == '1.0.0':
        return decode_1_0
    if version == '0.10.0':
        return decode_0_10
    raise WireRefusal('protocol_version_unsupported')
