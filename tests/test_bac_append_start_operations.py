"""Host-route tab registration settlement in the durable session.start operation."""
from __future__ import annotations

import asyncio
import copy
import json

import pytest

from bat_agent_connector import client as client_mod
from bat_agent_connector import registry, safety
from tests import test_api_v1 as api
from tests.mockbat import MockBac
from tests.operation_helpers import settle_operations
from tests.test_bac_append import calc_revision, expected_document
from tests.test_session_start_operations import START, create

daemon = api.daemon
ADD = "bac:workspace-add-terminal"
READ = "bac:workspace-read"
# What the rest of the operation sends after the tab step (prompt included).
FLOW_TAIL = ['workspace:load', 'git:getRoot', 'git:getRoot', 'worktree:status', 'git:log', 'git:branch',
             'claude:get-session-meta', 'claude:send-message']


@pytest.fixture(autouse=True)
def fixed_git(mock):
    mock.handlers['git:log'] = lambda p: [{'hash': 'a' * 40}]


@pytest.fixture
def append_host(mock, daemon):
    mock.bac = MockBac()
    mock.bac.enable_append(mock)
    daemon.fleet.config.host('h1').orchestrate_register_tabs = True
    return mock


def frames(mock, channel):
    return [f for f in mock.bac.frames if f.get("channel") == channel]


def after(mock, channel):
    channels = mock.channels()
    return channels[channels.index(channel) + 1:]


def tab_rows():
    rows = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    return [(r.get('channel'), r.get('phase')) for r in rows if r.get('channel') in (ADD, 'workspace:save')]


def tab_refs(out):
    return {k: v for k, v in (out.get('external_refs') or {}).items() if k.startswith('tab')}


async def again(d, out):
    d.ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (out['operation_id'],))
    await settle_operations(d.ops)
    return d.ops.get(out['operation_id'])


async def test_appended_by_host(daemon, append_host):
    mock = append_host
    out = await create(daemon, params={'title': 'An exact title', 'prompt': 'words'})
    assert out['status'] == 'succeeded'
    assert out['result']['tab'] == {
        'appended': True,
        'saved': True,
        'route': 'bac',
        'source': 'persisted',
        'window_id': None,
        'revision': calc_revision(expected_document(mock)),
        'settled_by': 'host_receipt',
    }
    assert tab_refs(out) == {'tab_route': ADD}
    assert after(mock, ADD) == FLOW_TAIL
    assert len(frames(mock, ADD)) == 1
    assert len(frames(mock, READ)) == 1
    assert tab_rows() == [(ADD, 'attempt')]
    assert registry.list_entries('h1')[0]['tab_registered'] is True
    assert mock.ws_doc['terminals'][-1] == frames(mock, ADD)[0]['params']['terminal']
    replay = await create(daemon, params={'title': 'An exact title', 'prompt': 'words'})
    assert replay['operation_id'] == out['operation_id']
    assert len(frames(mock, ADD)) == 1


async def test_reply_lost_registration_applied(daemon, append_host):
    mock = append_host
    mock.bac.append_fault = 'lost_reply'
    out = await create(daemon, params={'prompt': 'after registration'})
    assert out['status'] == 'uncertain'
    assert tab_refs(out) == {'tab_route': ADD}
    assert after(mock, ADD) == []
    assert 'claude:send-message' not in mock.channels()
    out = await again(daemon, out)
    assert out['status'] == 'succeeded'
    assert out['result']['tab'] == {
        'appended': True,
        'settled_by': 'effective_registration',
        'route': 'bac',
    }
    assert after(mock, ADD) == ['bac:hello', READ] + FLOW_TAIL
    assert len(frames(mock, ADD)) == 1
    assert 'workspace:save' not in mock.channels()
    assert registry.list_entries('h1')[0]['tab_registered'] is True


async def test_request_lost_nothing_applied_never_sent_again(daemon, append_host):
    mock = append_host
    mock.bac.append_fault = 'lost_request'
    out = await create(daemon, params={'prompt': 'after registration'})
    assert out['status'] == 'uncertain'
    out = await again(daemon, out)
    assert out['status'] == 'uncertain'
    assert after(mock, ADD) == ['bac:hello', READ]
    out = await again(daemon, out)
    assert out['status'] == 'uncertain'
    assert after(mock, ADD) == ['bac:hello', READ, READ]
    mock.ws_doc['terminals'].append(copy.deepcopy(frames(mock, ADD)[0]['params']['terminal']))
    out = await again(daemon, out)
    assert out['status'] == 'succeeded'
    assert out['result']['tab'] == {
        'appended': True,
        'settled_by': 'effective_registration',
        'route': 'bac',
    }
    assert len(frames(mock, ADD)) == 1
    assert mock.channels().count('claude:send-message') == 1


async def test_unknown_outcome_registration_applied(daemon, append_host):
    mock = append_host
    mock.bac.append_fault = 'write_failed_applied'
    out = await create(daemon, params={'prompt': 'after registration'})
    assert out['status'] == 'uncertain'
    out = await again(daemon, out)
    assert out['status'] == 'succeeded'
    assert out['result']['tab'] == {
        'appended': True,
        'settled_by': 'effective_registration',
        'route': 'bac',
    }
    assert after(mock, ADD) == [READ] + FLOW_TAIL
    assert len(frames(mock, ADD)) == 1


async def test_readback_uses_host_comparison(daemon, append_host):
    mock = append_host
    daemon.fleet.config.host('h1').default_permission_mode = 'allow_all'

    def overlay(t):
        t['sdkSessionId'] = 'sdk-1'
        t['agentParams'] = {**(t.get('agentParams') or {}), 'effortLevel': 'high'}

    mock.bac.overlay = overlay
    mock.bac.append_fault = 'lost_reply'
    out = await create(daemon, params={'agent': 'codex', 'prompt': 'p'})
    assert out['status'] == 'uncertain'
    assert 'agentParams' in frames(mock, ADD)[0]['params']['terminal']
    stored = mock.ws_doc['terminals'][-1]
    assert 'effortLevel' in (stored.get('agentParams') or {})
    assert 'sdkSessionId' in stored
    out = await again(daemon, out)
    assert out['status'] == 'succeeded'
    assert out['result']['tab'] == {
        'appended': True,
        'settled_by': 'effective_registration',
        'route': 'bac',
    }


async def test_different_stored_registration_not_accepted(daemon, append_host):
    mock = append_host

    def overlay(t):
        t['title'] = 'Renamed by the host'

    mock.bac.overlay = overlay
    mock.bac.append_fault = 'lost_reply'
    out = await create(daemon, params={'prompt': 'p'})
    assert out['status'] == 'uncertain'
    out = await again(daemon, out)
    assert out['status'] == 'needs_attention'
    assert out['error_code'] == 'START_TAB_MISMATCH'
    assert after(mock, ADD) == ['bac:hello', READ]
    daemon.ops.resume(START, out['operation_id'])
    await settle_operations(daemon.ops)
    out = daemon.ops.get(out['operation_id'])
    assert out['status'] == 'needs_attention'
    assert out['error_code'] == 'START_TAB_MISMATCH'
    assert len(frames(mock, ADD)) == 1
    assert 'claude:send-message' not in mock.channels()
    assert 'workspace:save' not in mock.channels()


async def test_host_answers_another_registration_holds_id(daemon, append_host):
    mock = append_host

    def before_append():
        other = copy.deepcopy(mock.bac.frames[-1]['params']['terminal'])
        other['title'] = 'Someone else'
        mock.ws_doc['terminals'].append(other)

    mock.bac.before_append = before_append
    out = await create(daemon, params={'prompt': 'p'})
    assert out['status'] == 'needs_attention'
    assert out['error_code'] == 'START_TAB_MISMATCH'
    assert tab_refs(out) == {'tab_route': ADD}
    assert after(mock, ADD) == []
    daemon.ops.resume(START, out['operation_id'])
    await settle_operations(daemon.ops)
    out = daemon.ops.get(out['operation_id'])
    assert out['status'] == 'needs_attention'
    assert out['error_code'] == 'START_TAB_MISMATCH'
    assert after(mock, ADD) == [READ]
    assert len(frames(mock, ADD)) == 1
    assert 'claude:send-message' not in mock.channels()


async def test_another_registration_found_by_client_read(daemon, append_host, monkeypatch):
    mock = append_host
    client = daemon.fleet.client('h1')
    original = client.append_workspace_terminal

    async def wrapper(profile_id, terminal, *args, **kwargs):
        mock.ws_doc['terminals'].append({**copy.deepcopy(terminal), 'model': 'other-model'})
        return await original(profile_id, terminal, *args, **kwargs)

    monkeypatch.setattr(client, 'append_workspace_terminal', wrapper)
    out = await create(daemon, params={'prompt': 'p'})
    assert out['status'] == 'needs_attention'
    assert out['error_code'] == 'START_TAB_MISMATCH'
    assert frames(mock, ADD) == []
    assert tab_rows() == []
    assert tab_refs(out) == {'tab_route': ADD}
    daemon.ops.resume(START, out['operation_id'])
    await settle_operations(daemon.ops)
    out = daemon.ops.get(out['operation_id'])
    assert out['status'] == 'needs_attention'
    assert out['error_code'] == 'START_TAB_MISMATCH'
    assert len(frames(mock, READ)) == 2
    assert 'claude:send-message' not in mock.channels()


async def test_equal_registration_found_by_client_read(daemon, append_host, monkeypatch):
    mock = append_host
    client = daemon.fleet.client('h1')
    original = client.append_workspace_terminal

    async def wrapper(profile_id, terminal, *args, **kwargs):
        mock.ws_doc['terminals'].append(copy.deepcopy(terminal))
        return await original(profile_id, terminal, *args, **kwargs)

    monkeypatch.setattr(client, 'append_workspace_terminal', wrapper)
    out = await create(daemon, params={'prompt': 'p'})
    assert out['status'] == 'succeeded'
    assert out['result']['tab'] == {
        'appended': True,
        'identical': True,
        'reason': 'already present',
        'revision': calc_revision(expected_document(mock)),
        'route': 'bac',
        'settled_by': 'effective_registration',
    }
    assert frames(mock, ADD) == []
    assert tab_rows() == []
    assert tab_refs(out) == {}
    assert registry.list_entries('h1')[0]['tab_registered'] is True
    assert mock.channels().count('claude:send-message') == 1


async def test_certain_refusal_does_not_block_start(daemon, append_host):
    mock = append_host
    mock.bac.append_error = 'WORKSPACE_UNAVAILABLE'
    out = await create(daemon, params={'prompt': 'p'})
    assert out['status'] == 'succeeded'
    assert out['result']['tab'] == {
        'appended': False,
        'saved': False,
        'reason': 'host refused the registration',
        'refusal': 'WORKSPACE_UNAVAILABLE',
        'route': 'bac',
    }
    assert registry.list_entries('h1')[0]['tab_registered'] is False
    assert len(frames(mock, ADD)) == 1
    assert after(mock, ADD) == FLOW_TAIL
    assert 'workspace:save' not in mock.channels()


async def test_host_read_gone_at_readback_no_substitute(daemon, append_host):
    mock = append_host
    mock.bac.append_fault = 'lost_reply'
    out = await create(daemon, params={'prompt': 'p'})
    assert out['status'] == 'uncertain'
    mock.bac.features['workspaceRead'] = False
    out = await again(daemon, out)
    assert out['status'] == 'uncertain'
    assert after(mock, ADD) == ['bac:hello']
    mock.bac.features['workspaceRead'] = True
    await daemon.fleet.client('h1').close()
    out = await again(daemon, out)
    assert out['status'] == 'succeeded'
    assert out['result']['tab'] == {
        'appended': True,
        'settled_by': 'effective_registration',
        'route': 'bac',
    }
    assert len(frames(mock, ADD)) == 1


async def test_one_request_at_a_time(daemon, append_host):
    daemon.fleet.client('h1')._sem = asyncio.Semaphore(1)
    out = await asyncio.wait_for(create(daemon, params={'prompt': 'original'}), 5)
    assert out['status'] == 'succeeded'
    assert out['result']['tab']['settled_by'] == 'host_receipt'


async def test_host_without_append_is_unchanged(daemon, mock):
    daemon.fleet.config.host('h1').orchestrate_register_tabs = True
    out = await create(daemon, params={'prompt': 'p'})
    assert out['status'] == 'succeeded'
    assert out['result']['tab'] == {
        'appended': True,
        'saved': True,
        'verified_previous_terminals_kept': True,
        'lost_terminals': [],
        'lost_workspaces': [],
        'settled_by': 'exact_terminal',
    }
    assert tab_refs(out) == {}
    assert tab_rows() == [('workspace:save', 'attempt')]


async def test_retried_frame_is_gated_and_audited_again(daemon, append_host, monkeypatch):
    mock = append_host
    daemon.fleet.config.safety.write_min_interval_s = 60
    pauses = []

    async def record_pause(delay):
        pauses.append(delay)

    monkeypatch.setattr(client_mod, '_retry_pause', record_pause)
    calls = 0

    def before_append():
        nonlocal calls
        calls += 1
        if calls == 1:
            mock.ws_doc['terminals'].append({
                'id': 'gui-1',
                'workspaceId': 'ws-1',
                'title': 'G',
                'type': 'terminal',
                'cwd': '/srv/demo',
                'agentPreset': 'claude-code',
            })

    mock.bac.before_append = before_append
    out = await create(daemon, params={'prompt': 'p'})
    assert out['status'] == 'succeeded'
    assert out['result']['tab']['settled_by'] == 'host_receipt'
    assert len(frames(mock, ADD)) == 2
    assert len(frames(mock, READ)) == 2
    assert tab_rows() == [(ADD, 'attempt'), (ADD, 'attempt')]
    assert pauses == [0.5]
    ids = [t['id'] for t in mock.ws_doc['terminals']]
    assert ids.count('gui-1') == 1
    assert ids.count(out['result']['session_id']) == 1
