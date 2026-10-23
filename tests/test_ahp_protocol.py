"""Offline, synthetic protocol shapes; no editor traces or execution acceptance."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from bat_agent_connector import ahp_observer, ahp_protocol
from bat_agent_connector.pm_ahp_observations import capabilities

FIXTURES = Path(__file__).parent / 'fixtures' / 'ahp-owned'
PINS = json.loads((FIXTURES / 'pins.json').read_text(encoding='utf-8'))
SHAPES = [json.loads(line) for line in (FIXTURES / 'protocol-shapes.jsonl').read_text(encoding='utf-8').splitlines()]
VERSIONS = ('1.0.0', '0.10.0')


def shape(version, name):
    return copy.deepcopy(next(row for row in SHAPES if row['version'] == version and row['name'] == name))


def decode(row):
    return ahp_protocol.decoder_for(row['version'])(row['frame'], response_to=row.get('response_to'))


@pytest.mark.parametrize('row', SHAPES, ids=lambda row: row['version'] + '-' + row['name'])
def test_pinned_synthetic_shapes(row):
    assert row['synthetic'] is True
    pin = PINS['profiles'][row['version']]
    assert row['source']['repository'] == pin['repository']
    assert row['source']['commit'] == pin['commit'] == ahp_protocol.PINNED_COMMITS[row['version']]
    assert row['source']['path'] in pin['paths']
    if 'status' in row:
        assert ahp_protocol.decode_status(row['status']) == row['expected']
    else:
        original = copy.deepcopy(row['frame'])
        result = decode(row)
        assert result.kind == row['expected_kind']
        assert result.method == row.get('response_to', row['frame'].get('method'))
        assert result.payload == row['frame'].get('params', row['frame'].get('error', row['frame'].get('result')))
        assert row['frame'] == original


def test_fixture_provenance_and_canonical_lf_bytes():
    assert PINS['synthetic'] is True and PINS['tested_host_versions'] == []
    assert set(PINS['profiles']) == set(VERSIONS)
    assert PINS['profiles']['1.0.0']['commit'] == '5f16d81bb7045b66d7bc768d244feab75002d943'
    assert PINS['profiles']['0.10.0']['commit'] == '2a59476c9bfcb90b3ddc372c36762471b7dfad1c'
    assert PINS['profiles']['0.10.0']['distribution_version'] == '1.141.0'
    assert len({(row['version'], row['name']) for row in SHAPES}) == len(SHAPES)
    expected = ''.join(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n' for row in SHAPES)
    assert (FIXTURES / 'protocol-shapes.jsonl').read_bytes() == expected.encode('utf-8')
    assert dict(ahp_protocol.ERROR_CODES) == PINS['error_codes']


@pytest.mark.parametrize('version', [None, '', '1.1.0', '0.9.0', '1.141.0', 'latest', {}, True])
def test_unknown_wire_version_refused(version):
    with pytest.raises(ahp_protocol.WireRefusal, match='protocol_version_unsupported'):
        ahp_protocol.decoder_for(version)


@pytest.mark.parametrize('version', VERSIONS)
def test_notification_is_not_an_rpc_or_a_result(version):
    row = shape(version, 'turn')
    row['frame']['id'] = 12
    with pytest.raises(ahp_protocol.WireRefusal, match='notification_has_id'):
        decode(row)
    for method in ('dispatchAction', 'action'):
        with pytest.raises(ahp_protocol.WireRefusal, match='response_method_unsupported'):
            ahp_protocol.decoder_for(version)({'jsonrpc': '2.0', 'id': 12, 'result': None}, response_to=method)
    row = shape(version, 'turn')
    row['frame']['result'] = None
    with pytest.raises(ahp_protocol.WireRefusal):
        decode(row)


@pytest.mark.parametrize('version', VERSIONS)
@pytest.mark.parametrize('method', ['getResult', 'getBusy', 'startTurnIfIdle', 'sendMessage', 'cancelTurn'])
def test_no_invented_rpc(version, method):
    with pytest.raises(ahp_protocol.WireRefusal, match='method_unsupported'):
        ahp_protocol.decoder_for(version)({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': {}})


@pytest.mark.parametrize('version', VERSIONS)
def test_create_null_reply_is_not_a_native_identity_or_completion(version):
    row = shape(version, 'create_reply')
    assert decode(row).payload is None
    row['frame']['result'] = {'sessionId': 'synthetic-invented-id'}
    with pytest.raises(ahp_protocol.WireRefusal, match='invalid_result'):
        decode(row)
    row = shape(version, 'create')
    del row['frame']['id']
    with pytest.raises(ahp_protocol.WireRefusal):
        decode(row)


@pytest.mark.parametrize('version', VERSIONS)
@pytest.mark.parametrize('mutation', ['no_turn', 'no_duration', 'bool_duration', 'nan_duration', 'negative_duration'])
def test_cancel_requires_original_turn_and_producer_duration(version, mutation):
    row = shape(version, 'cancel')
    action = row['frame']['params']['action']
    if mutation == 'no_turn':
        del action['turnId']
    elif mutation == 'no_duration':
        del action['duration']
    else:
        action['duration'] = {'bool_duration': True, 'nan_duration': float('nan'), 'negative_duration': -1}[mutation]
    with pytest.raises(ahp_protocol.WireRefusal):
        decode(row)


@pytest.mark.parametrize('version', VERSIONS)
def test_dispatch_and_echo_have_different_sequence_and_action_authority(version):
    row = shape(version, 'turn')
    row['frame']['params']['action']['message']['origin']['kind'] = 'agent'
    with pytest.raises(ahp_protocol.WireRefusal, match='client_message_refused'):
        decode(row)
    row['frame']['method'] = 'action'
    row['frame']['params'].pop('clientSeq')
    row['frame']['params']['serverSeq'] = 2
    assert decode(row).kind == 'notification'  # An agent-origin server event is not a client turn.
    row = shape(version, 'complete')
    row['frame']['method'] = 'dispatchAction'
    row['frame']['params'].pop('serverSeq')
    row['frame']['params']['clientSeq'] = 2
    with pytest.raises(ahp_protocol.WireRefusal, match='client_action_refused'):
        decode(row)
    row = shape(version, 'rejected_turn')
    assert decode(row).payload['rejectionReason'] == 'Synthetic busy refusal'
    row['frame']['params']['origin']['clientSeq'] = True
    with pytest.raises(ahp_protocol.WireRefusal):
        decode(row)


@pytest.mark.parametrize('version', VERSIONS)
def test_error_details_do_not_authorize_authentication_or_retry(version):
    for name in ('AuthRequired', 'UnsupportedProtocolVersion'):
        row = shape(version, 'error_' + name)
        del row['frame']['error']['data']
        with pytest.raises(ahp_protocol.WireRefusal):
            decode(row)
    row = shape(version, 'error_PermissionDenied')
    assert decode(row).kind == 'error'
    row['frame']['error']['code'] = -31999
    with pytest.raises(ahp_protocol.WireRefusal, match='error_code_unsupported'):
        decode(row)
    row = shape(version, 'error_TurnInProgress')
    row['frame']['result'] = None
    with pytest.raises(ahp_protocol.WireRefusal):
        decode(row)


@pytest.mark.parametrize('version', VERSIONS)
def test_version_specific_native_session_uri_is_preserved(version):
    row = shape(version, 'create')
    assert decode(row).payload['channel'] == row['frame']['params']['channel']
    other = shape('0.10.0' if version == '1.0.0' else '1.0.0', 'create')
    row['frame']['params']['channel'] = other['frame']['params']['channel']
    with pytest.raises(ahp_protocol.WireRefusal, match='session_uri_refused'):
        decode(row)
    row['frame']['params']['channel'] = 'file:///synthetic/worktree'
    with pytest.raises(ahp_protocol.WireRefusal):
        decode(row)


@pytest.mark.parametrize('version', VERSIONS)
@pytest.mark.parametrize('mutation', ['string_origin', 'no_timestamp', 'no_message', 'conditional_turn', 'attachment'])
def test_unknown_or_malformed_turn_fields_are_not_silently_discarded(version, mutation):
    row = shape(version, 'turn')
    action = row['frame']['params']['action']
    if mutation == 'string_origin':
        action['message']['origin'] = 'user'
    elif mutation == 'no_timestamp':
        del action['startedAt']
    elif mutation == 'no_message':
        del action['message']
    elif mutation == 'conditional_turn':
        action['ifIdle'] = True
    else:
        action['message']['attachments'] = []  # Valid upstream option, outside this text-only subset.
    with pytest.raises(ahp_protocol.WireRefusal):
        decode(row)


@pytest.mark.parametrize('version', VERSIONS)
def test_refuses_forks_client_tools_and_uncorrelated_responses(version):
    row = shape(version, 'create_chat')
    row['frame']['params']['source'] = {'kind': 'fork', 'chat': 'ahp-chat:/synthetic', 'turnId': 'synthetic'}
    with pytest.raises(ahp_protocol.WireRefusal):
        decode(row)
    row = shape(version, 'create')
    row['frame']['params']['activeClient']['tools'] = [{'name': 'synthetic-tool'}]
    with pytest.raises(ahp_protocol.WireRefusal, match='client_tools_unsupported'):
        decode(row)
    row = shape(version, 'create_reply')
    del row['response_to']
    with pytest.raises(ahp_protocol.WireRefusal, match='response_method_unsupported'):
        decode(row)


def test_status_is_a_bitset_and_input_needed_is_busy():
    assert ahp_protocol.decode_status(1 | 32)['idle'] is True
    assert ahp_protocol.decode_status(1 | 64)['idle'] is True
    for status in (8, 24, 24 | 32, 1 | 8):
        state = ahp_protocol.decode_status(status)
        assert state['busy'] is True and state['idle'] is False
    assert ahp_protocol.decode_status(24)['input_needed'] is True
    for status in (True, -1, 4, 128, 1.0, '1', None):
        with pytest.raises(ahp_protocol.WireRefusal):
            ahp_protocol.decode_status(status)


async def test_observer_allowlist_is_unchanged_and_blocks_writes_before_transport():
    assert ahp_observer.METHODS == frozenset({'initialize', 'reconnect', 'listSessions', 'subscribe'})
    observer = ahp_observer.Observer(None, client_id='synthetic', selected=[], changesets=False,
                                    check=lambda: pytest.fail('write reached frame check'))
    for method in ('createSession', 'createChat', 'dispatchAction', 'fetchTurns', 'authenticate'):
        with pytest.raises(ahp_observer.ObserverError, match='read_method_refused'):
            await observer.request(method, {})
    caps = capabilities()
    assert all(caps[k] == 'unsupported' for k in ('create_own', 'send', 'cancel', 'transcript'))
    assert caps['manual_foreign_access'] == 'read_only' and caps['tested_host_versions'] == []


def test_owned_wire_has_no_production_caller_and_cannot_enable_writes():
    assert ahp_protocol.OWNED_WRITES_ENABLED is False
    with pytest.raises(ahp_protocol.WireRefusal, match='owned_writes_disabled'):
        ahp_protocol.require_owned_write()
    source = Path(ahp_protocol.__file__).parent
    for file in source.glob('*.py'):
        if file.name == 'ahp_protocol.py':
            continue
        for node in ast.walk(ast.parse(file.read_text(encoding='utf-8'))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] + [getattr(node, 'module', '') or '']
                assert all('ahp_protocol' not in name.split('.') for name in names), file.name
