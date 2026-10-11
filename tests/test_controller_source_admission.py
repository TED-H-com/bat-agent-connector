"""Source selection retains admission identity across awaited reads and retries."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bat_agent_connector import controller_authority, registry, service
from bat_agent_connector import failover_operations as failover
from bat_agent_connector import orchestration_operations as relay
from bat_agent_connector import worktree_merge_operations as merge
from bat_agent_connector.operations import OpContext, StepFailed
from tests import test_api_v1 as api
from tests import test_worktree_merge_operations as merge_tests
from tests.conftest import controller_scope
from tests.operation_helpers import settle_operations
from tests.test_controller_paths import OTHER, OWNER, SID
from tests.test_lifecycle import _add_wt_claude

daemon = api.daemon
carriers, host_git_context = merge_tests.carriers, merge_tests.host_git_context


def replace_source(daemon, sid, change):
    path = registry.registry_path()
    document = json.loads(path.read_text())
    row = next(row for row in document['sessions'] if row['session_id'] == sid)
    if change == 'foreign':
        row['controller_authority'] = controller_authority.binding(daemon.journal.db, OTHER)
    else:
        row['controller_incarnation'] = 'f' * 32
    path.write_text(json.dumps(document))


@pytest.mark.parametrize('action', ['session.failover', 'fanout.start', 'worktree.merge', 'session.relay'])
@pytest.mark.parametrize('phase', ['accepted', 'resolving'])
@pytest.mark.parametrize('change', ['nonce', 'foreign'])
async def test_explicit_source_cannot_adopt_replacement_before_plan(daemon, mock, monkeypatch, action, phase, change):
    with controller_scope(daemon.journal, OWNER):
        _add_wt_claude(mock, SID)
    daemon.ops.context['git_runner'] = SimpleNamespace(available=lambda host: host == 'h1')
    # Prefix lookup is intentionally supported, but the admitted matching row is fixed.
    op, _ = daemon.ops.create(OWNER, action=action, target={'host': 'h1', 'session_id': 'wt-claude'},
                              params={'message': 'same original source'} if action == 'session.relay' else {},
                              idempotency_key='admitted-source')
    if phase == 'accepted':
        replace_source(daemon, SID, change)
    else:
        original = service._resolve_session

        async def replaced(*args, **kwargs):
            result = await original(*args, **kwargs)
            replace_source(daemon, SID, change)
            return result

        monkeypatch.setattr(service, '_resolve_session', replaced)
    await settle_operations(daemon.ops)
    result = daemon.ops.get(op['operation_id'])
    assert result['status'] == 'failed', result
    assert result['error_code'] == 'SESSION_SOURCE_CHANGED', result
    assert not api.write_frames(mock)
    assert len(registry.list_entries('h1')) == 1
    assert not [row for row in daemon.ops.list()['operations'] if row['action'] == 'session.start']


async def test_resolved_prefix_remains_exact_after_interrupted_plan_read(daemon, mock):
    from tests.conftest import adopt

    with controller_scope(daemon.journal, OWNER):
        _add_wt_claude(mock, SID)
        adopt('wt-claude-other', cwd='/srv/other', agent_preset='claude-code')
    op, _ = daemon.ops.create(OWNER, action='fanout.start', target={'host': 'h1', 'session_id': 'wt-claude'},
                              params={}, idempotency_key='fixed-prefix')
    ctx = OpContext(daemon.ops, op)
    with controller_scope(daemon.journal, OWNER):
        tab, _, _ = await relay.resolve_source(daemon.ops, ctx.target, ctx=ctx)
        assert tab['id'] == SID
        # The next read sees only a different matching terminal. Registry fallback
        # for the saved exact ID must win over re-running the original prefix.
        mock.ws_doc['terminals'] = [tab for tab in mock.ws_doc['terminals'] if tab['id'] != SID]
        mock.ws_doc['terminals'].append({'id': 'wt-claude-other', 'workspaceId': 'ws-1',
                                         'agentPreset': 'claude-code', 'cwd': '/srv/other'})
        ctx = OpContext(daemon.ops, daemon.ops.get(op['operation_id']))
        again, _, saved = await relay.resolve_source(daemon.ops, ctx.target, ctx=ctx)
    assert again['id'] == saved['session_id'] == SID
    assert not api.write_frames(mock)


async def test_generic_failover_selection_retains_first_identity_before_next_read(daemon, mock, monkeypatch):
    with controller_scope(daemon.journal, OWNER):
        _add_wt_claude(mock, SID)
    op, _ = daemon.ops.create(OWNER, action='session.failover', target={'host': 'h1'},
                              params={'all_exhausted': True}, idempotency_key='fixed-sweep')
    ctx = OpContext(daemon.ops, op)
    with controller_scope(daemon.journal, OWNER):
        chosen = await failover.selection(daemon.ops, ctx.target, ctx.params, ctx=ctx)
        assert chosen['session_ids'] == [SID]
        replace_source(daemon, SID, 'nonce')
        again = await failover.selection(daemon.ops, ctx.target, ctx.params, ctx=ctx)
        assert again == chosen

        async def no_read(*args, **kwargs):
            pytest.fail('replacement must be refused before another remote source read')

        monkeypatch.setattr(service, '_resolve_session', no_read)
        with pytest.raises(StepFailed) as error:
            await failover.resolve(daemon.ops, ctx.target, ctx.params, SID, operation_id=ctx.operation_id, index=1,
                                   source_identity=again['source_identities'][SID])
        assert error.value.code == 'SESSION_SOURCE_CHANGED'
    assert not api.write_frames(mock)


async def test_unregistered_fanout_source_cannot_gain_ownership_during_first_read(daemon, mock, monkeypatch):
    from tests.conftest import adopt

    op, _ = daemon.ops.create(OWNER, action='fanout.start', target={'host': 'h1', 'session_id': api.MANUAL},
                              params={}, idempotency_key='absent-source')
    original = service._resolve_session

    async def registered(*args, **kwargs):
        result = await original(*args, **kwargs)
        adopt(api.MANUAL, agent_preset='claude-code')
        return result

    monkeypatch.setattr(service, '_resolve_session', registered)
    await settle_operations(daemon.ops)
    result = daemon.ops.get(op['operation_id'])
    assert result['status'] == 'failed' and result['error_code'] == 'SESSION_SOURCE_CHANGED', result
    assert not api.write_frames(mock)


@pytest.mark.parametrize('change', ['nonce', 'foreign'])
async def test_merge_cannot_capture_new_source_identity_after_awaited_git_proof(daemon, mock, carriers, monkeypatch, change):
    origin, _ = carriers
    original_head = merge_tests.git(origin, 'rev-parse', 'HEAD')
    original = merge.proof

    async def replaced(*args, **kwargs):
        result = await original(*args, **kwargs)
        replace_source(daemon, merge_tests.SID, change)
        return result

    monkeypatch.setattr(merge, 'proof', replaced)
    op, _ = daemon.ops.create(OWNER, **merge_tests.intent())
    await settle_operations(daemon.ops)
    result = daemon.ops.get(op['operation_id'])
    assert result['status'] == 'failed', result
    assert result['error_code'] in {'SESSION_SOURCE_CHANGED', 'FOREIGN_CONTROLLER'}, result
    assert merge_tests.git(origin, 'rev-parse', 'HEAD') == original_head
    assert not api.write_frames(mock)
