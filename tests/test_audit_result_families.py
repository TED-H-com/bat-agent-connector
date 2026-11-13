"""Real reviewed effect families, with result-only file faults after mock transport.

Every reachable warning assertion fails on d62093b8. Explicit skips identify
branches with no result append today; adding new audit obligations is outside
this change. The synthetic boundary matrix covers all four phases separately.
"""
import pytest

from bat_agent_connector import lifecycle, safety
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.operations import ActionDef
from tests import test_api_v1 as api
from tests import test_failover_operations as failover
from tests import test_fanout_operations as fanout
from tests import test_operations_unification as task_tests
from tests import test_session_start_operations as starts
from tests import test_verification_operations as verification
from tests import test_worktree_merge_operations as merge
from tests.conftest import adopt
from tests.operation_helpers import settle_operations
from tests.test_audit_result_warnings import inject_append_fault
from tests.test_lifecycle import _add_wt_claude

daemon, owned = api.daemon, task_tests.owned
carriers, host_git_context = merge.carriers, merge.host_git_context

FAMILIES = [
    ('A01-resume', 'claude:client-resume'), ('A02-send', 'claude:send-message'),
    ('A03-interrupt', 'claude:interrupt-turn'), ('A04-answer', 'claude:resolve-ask-user'),
    ('A05-permissions', 'claude:set-permission-mode'), ('A06-create', 'worktree:create'),
    ('A07-start', 'claude:start-session'), ('A08-prompt', 'claude:send-message'),
    ('A11-successor', 'claude:start-session'), ('A12-handoff', 'claude:send-message'),
    ('A13-planner-stop', 'claude:stop-session'), ('A23-cleanup-stop', 'claude:stop-session'),
    ('A24-verification', None),
]


def fail_result(monkeypatch, d, mock, channel, outcome, stage, persistent):
    active = False
    original_record, original_roundtrip = safety.Audit.record_after_effect, d.fleet.client('h1')._roundtrip
    calls = inject_append_fault(monkeypatch, stage, persistent, active=lambda: active)

    def record(self, **fields):
        nonlocal active
        active = fields.get('channel') == channel
        try:
            return original_record(self, **fields)
        finally:
            active = False

    if outcome == 'error' and channel:
        def error(_):
            raise RuntimeError('synthetic remote error body')
        monkeypatch.setitem(mock.handlers, channel, error)

    async def roundtrip(frame, *args, **kw):
        result = await original_roundtrip(frame, *args, **kw)
        if frame["channel"] == channel and outcome == 'unknown':
            raise InvokeTimeout('synthetic lost real mock reply')
        return result

    monkeypatch.setattr(safety.Audit, 'record_after_effect', record)
    monkeypatch.setattr(d.fleet.client('h1'), '_roundtrip', roundtrip)
    return calls


def assert_warning(d, out, outcome, stage):
    warnings = out['external_refs'].get('audit_results', {})
    assert len(warnings) == 1, out
    assert next(iter(warnings.values()))['failure_stage'] == stage
    assert next(iter(warnings.values()))['effect_outcome'] == outcome
    events = [e for e in d.journal.api_events(0, 500, resource_id=out['operation_id'])['events']
              if e['kind'] == 'operation.audit_result_failed']
    assert len(events) == 1
    assert 'synthetic remote error body' not in str(events)
    assert out['error_code'] != 'REFUSED', out


@pytest.mark.parametrize('family,channel', FAMILIES, ids=[r[0] for r in FAMILIES])
@pytest.mark.parametrize('outcome', ['accepted', 'error', 'unknown'])
@pytest.mark.parametrize('stage', ['mkdir', 'open', 'write', 'flush', 'close'])
@pytest.mark.parametrize('persistent', [False, True], ids=['one-shot', 'persistent'])
async def test_real_family(daemon, mock, monkeypatch, family, channel, outcome, stage, persistent):
    if (family == 'A01-resume' and outcome == 'accepted'
            or family == 'A04-answer' and outcome == 'unknown'
            or family in {'A23-cleanup-stop', 'A24-verification'} and outcome != 'accepted'):
        pytest.skip('This branch has no post-effect audit append; no new audit obligation in this change')
    d = daemon
    mock.echo_sends = True
    if family.startswith(('A06', 'A07', 'A08', 'A11', 'A12', 'A13')):
        mock.handlers['git:log'] = lambda _: [{'hash': starts.SHA}]
    if family.startswith(('A11', 'A12')):
        _add_wt_claude(mock, failover.SID)
    elif not family.startswith(('A06', 'A07', 'A08', 'A24')):
        adopt(api.MANUAL, **({'role': 'planner'} if family.startswith('A13') else {}))
    if family == 'A01-resume':
        mock.metas[api.MANUAL] = None
    if family == 'A04-answer':
        mock.states[api.MANUAL]['pendingAskUser'] = {'toolUseId': 'ask-1', 'questions': [{'question': 'Choice?'}]}
    if family == 'A13-planner-stop':
        d.fleet.config.host('h1').orchestrate_max_sessions = 3
        fanout.replies(mock)
    calls = fail_result(monkeypatch, d, mock, channel, outcome, stage, persistent)
    try:
        if family.startswith(('A06', 'A07', 'A08')):
            out = await starts.create(d, params={'prompt': 'synthetic initial prompt'})
        elif family.startswith(('A11', 'A12')):
            out = await failover.create(d)
        elif family == 'A13-planner-stop':
            out = await fanout.create(d, target={'host': 'h1', 'session_id': api.MANUAL}, params={})
        elif family == 'A24-verification':
            out = await verification.submit(d)
        else:
            if family == 'A23-cleanup-stop':
                async def run(ctx):
                    return await ctx.step('stop', lambda: lifecycle._stop(d.fleet, 'h1', api.MANUAL,
                        safety.Audit(d.fleet.config.safety), cleanup=True))
                d.ops.register(ActionDef('test.stop', 'operate', 'test', run))
                action, params = 'test.stop', {}
            else:
                action, params = {
                    'A01-resume': ('session.send', {'text': 'synthetic prompt'}),
                    'A02-send': ('session.send', {'text': 'synthetic prompt'}),
                    'A03-interrupt': ('session.interrupt', {'mode': 'soft'}),
                    'A04-answer': ('session.answer', {'answers': ['yes'], 'tool_use_id': 'ask-1'}),
                    'A05-permissions': ('session.permissions', {'mode': 'default'}),
                }[family]
            op, _ = d.ops.create(api.dashboard_principal(), action=action,
                target={'host': 'h1', 'session_id': api.MANUAL}, params=params, idempotency_key='family-once')
            await settle_operations(d.ops)
            out = d.ops.get(op['operation_id'])
        assert calls, out
        assert_warning(d, out, outcome, stage)
        if outcome == 'accepted':
            assert out['status'] == 'succeeded', out
        # Each targeted effect crossed real mock transport exactly once, except
        # testimony, whose effect is a local file write.
        if channel:
            assert mock.channels().count(channel) == 1
        before = list(mock.invokes)
        assert d.ops.get(out['operation_id']) == out
        assert mock.invokes == before
    finally:
        await d.fleet.close()
        await d.inventory.close()


@pytest.mark.parametrize('channel', ['worktree:rehydrate', 'worktree:merge'], ids=['A09', 'A10'])
@pytest.mark.parametrize('outcome', ['accepted', 'error', 'unknown'])
@pytest.mark.parametrize('stage', ['mkdir', 'open', 'write', 'flush', 'close'])
@pytest.mark.parametrize('persistent', [False, True])
async def test_real_merge_family(daemon, mock, carriers, monkeypatch, channel, outcome, stage, persistent):
    if channel == 'worktree:rehydrate':
        status = mock.worktrees.pop(merge.SID)
        mock.handlers['claude:get-worktree-status'] = lambda _: status
        def rehydrate(_):
            mock.worktrees[merge.SID] = status
            return {'success': True}
        mock.handlers[channel] = rehydrate
    calls = fail_result(monkeypatch, daemon, mock, channel, outcome, stage, persistent)
    try:
        out = await merge.create(daemon)
        assert calls, out
        assert_warning(daemon, out, outcome, stage)
        assert out['status'] == ('succeeded' if outcome == 'accepted' else 'uncertain'), out
        assert mock.channels().count(channel) == 1
        again = await merge.create(daemon)
        assert again == out and mock.channels().count(channel) == 1
    finally:
        await daemon.fleet.close()
        await daemon.inventory.close()


@pytest.mark.parametrize('scoped', [False, True], ids=['runtime-command', 'task-command'])
@pytest.mark.parametrize('stage', ['mkdir', 'open', 'write', 'flush', 'close'])
async def test_task_command_uses_existing_operation_context(owned, mock, monkeypatch, scoped, stage):
    d, tid = owned
    mock.echo_sends = True
    fail_result(monkeypatch, d, mock, 'claude:send-message', 'accepted', stage, True)
    principal, request, op = task_tests.admission_control(d, tid, mock, 'send', scoped=scoped)
    await settle_operations(d.ops)
    out = d.ops.get(op['operation_id'])
    assert out['status'] == 'succeeded', out
    assert_warning(d, out, 'accepted', stage)
    assert d.journal.commands(tid)[0]['status'] == 'accepted'
    replay, fresh = d.ops.create(principal, **request)
    assert not fresh and replay['operation_id'] == op['operation_id']
    assert mock.channels().count('claude:send-message') == 1


@pytest.mark.parametrize('phase,channel', [('append-terminal', 'workspace:save'), ('rollback', 'worktree:remove')])
@pytest.mark.parametrize('outcome', ['accepted', 'error', 'unknown'])
@pytest.mark.parametrize('stage', ['mkdir', 'open', 'write', 'flush', 'close'])
@pytest.mark.parametrize('persistent', [False, True])
async def test_legacy_phases_inside_existing_operation(daemon, mock, monkeypatch, phase, channel, outcome, stage, persistent):
    from bat_agent_connector import orchestrate
    from bat_agent_connector.errors import InvokeError
    if phase == 'rollback' and outcome != 'accepted':
        pytest.skip('Rollback has no audit append until removal and absence are confirmed')
    d = daemon
    d.fleet.config.host('h1').orchestrate_register_tabs = True
    mock.handlers['git:log'] = lambda _: [{'hash': starts.SHA}]
    if phase == 'rollback':
        original = d.fleet.client('h1').invoke
        async def refuse_start(channel, *args, **kw):
            if channel == 'claude:start-session':
                raise InvokeError('synthetic start refused before transport')
            return await original(channel, *args, **kw)
        monkeypatch.setattr(d.fleet.client('h1'), 'invoke', refuse_start)
    calls = fail_result(monkeypatch, d, mock, channel, outcome, stage, persistent)

    async def run(ctx):
        return await ctx.step('legacy-start', lambda: orchestrate.session_start(
            d.fleet, 'h1', 'demo-project', confirm=True))
    d.ops.register(ActionDef('test.legacy-start', 'operate', 'test', run))
    try:
        op, _ = d.ops.create(api.dashboard_principal(), action='test.legacy-start', idempotency_key='legacy-phase')
        await settle_operations(d.ops)
        out = d.ops.get(op['operation_id'])
        assert calls, out
        assert_warning(d, out, outcome, stage)
        assert next(iter(out['external_refs']['audit_results'].values()))['phase'] == phase
        assert mock.channels().count(channel) == 1
        if phase == 'rollback':
            assert (out['status'], out['error_code']) == ('failed', 'BAT_ERROR')
            assert 'synthetic start refused' in out['status_reason']
            assert 'claude:start-session' not in mock.channels()
        else:
            assert out['status'] == 'succeeded' and out['result']['started'] is True
    finally:
        await d.fleet.close()
        await d.inventory.close()


@pytest.mark.parametrize('kind', ['send', 'interrupt', 'answer', 'permissions', 'start', 'failover'])
async def test_intent_open_failure_is_still_refused(daemon, mock, monkeypatch, kind):
    """Passes on baseline; no sent effect and no post-effect warning may be invented."""
    import os
    d = daemon
    if kind == 'failover':
        _add_wt_claude(mock, failover.SID)
    elif kind != 'start':
        adopt(api.MANUAL)
    mock.handlers['git:log'] = lambda _: [{'hash': starts.SHA}]
    mock.states[api.MANUAL]['pendingAskUser'] = {'toolUseId': 'ask-1', 'questions': [{'question': 'Choice?'}]}
    original = safety.open_private
    def refused(path, flags, *a, **kw):
        if flags & os.O_APPEND:
            raise OSError('synthetic intent storage unavailable')
        return original(path, flags, *a, **kw)
    monkeypatch.setattr(safety, 'open_private', refused)
    try:
        if kind == 'start':
            out = await starts.create(d)
        elif kind == 'failover':
            out = await failover.create(d)
        else:
            params = {'send': {'text': 'synthetic prompt'}, 'interrupt': {'mode': 'soft'},
                      'answer': {'answers': ['yes'], 'tool_use_id': 'ask-1'}, 'permissions': {'mode': 'default'}}[kind]
            op, _ = d.ops.create(api.dashboard_principal(), action='session.' + kind,
                target={'host': 'h1', 'session_id': api.MANUAL}, params=params, idempotency_key='intent-once')
            await settle_operations(d.ops)
            out = d.ops.get(op['operation_id'])
        assert (out['status'], out['error_code']) == ('failed', 'REFUSED'), out
        assert not out['external_refs'].get('audit_results')
        assert not api.write_frames(mock)
    finally:
        await d.fleet.close()
        await d.inventory.close()
