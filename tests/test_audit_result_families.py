"""Real reviewed effect families, with result-only file faults after mock transport.

Every reachable warning assertion fails on d62093b8. Explicit skips identify
branches with no result append today; adding new audit obligations is outside
this change. The synthetic boundary matrix covers all four phases separately.
"""
import json

import pytest

from bat_agent_connector import lifecycle, safety
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.operations import ActionDef
from tests import test_api_v1 as api
from tests import test_cleanup as cleanup_tests
from tests import test_failover_operations as failover
from tests import test_fanout_operations as fanout
from tests import test_operations_unification as task_tests
from tests import test_session_start_operations as starts
from tests import test_verification_operations as verification
from tests import test_worktree_merge_operations as merge
from tests.conftest import adopt
from tests.operation_helpers import settle_operations, stop_workers
from tests.test_audit_result_warnings import inject_append_fault
from tests.test_lifecycle import _add_wt_claude

daemon, owned = api.daemon, task_tests.owned
carriers, host_git_context = merge.carriers, merge.host_git_context
human = cleanup_tests.human


@pytest.fixture
def cleanup_daemon(mock, human, tmp_path):
    # Keep cleanup's live-terminal setup local to these tests, not autouse for
    # the rest of the family matrix.
    cleanup_tests.known_live_terminals.__wrapped__(mock)
    yield from cleanup_tests.daemon.__wrapped__(mock, human, tmp_path)


@pytest.mark.parametrize('reply,outcome', [({'ok': False}, 'error'), ({}, 'unknown')])
@pytest.mark.parametrize('fault', [False, True])
async def test_planner_stop_records_unproven_ack(daemon, mock, monkeypatch, reply, outcome, fault):
    """Fails on 0c83349d: a non-positive planner ACK had no result audit record."""
    d = daemon
    adopt(api.MANUAL, role='planner')
    mock.handlers['git:log'] = lambda _: [{'hash': starts.SHA}]
    fanout.replies(mock, tasks=fanout.ITEMS[:1])
    mock.handlers['claude:stop-session'] = lambda _: reply
    records = []
    active = False
    record = safety.Audit.record_after_effect
    inject_append_fault(monkeypatch, 'open', True, active=lambda: active)

    def recorded(self, **fields):
        nonlocal active
        selected = fields.get('channel') == 'claude:stop-session' and fields.get('phase') == 'result'
        if selected:
            records.append(fields)
        active = selected and fault
        try:
            return record(self, **fields)
        finally:
            active = False

    monkeypatch.setattr(safety.Audit, 'record_after_effect', recorded)
    envelope = fanout.intent(target={'host': 'h1', 'session_id': api.MANUAL}, params={})
    try:
        op, _ = d.ops.create(fanout.P, **envelope)
        for _ in range(4):
            d.ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (op['operation_id'],))
            await settle_operations(d.ops)
            done = d.ops.get(op['operation_id'])
            if done['status'] == 'uncertain':
                break
        assert (done['status'], done['error_code']) == ('uncertain', 'UNCERTAIN')
        assert len(records) == 1
        assert records[0]['effect_sent'] is True and records[0]['ok'] is False
        assert records[0]['effect_outcome'] == outcome
        if fault:
            assert_warning(d, done, outcome, 'open')
        else:
            assert not done['external_refs'].get('audit_results')
            events = d.journal.api_events(0, 500, resource_id=op['operation_id'])['events']
            assert not any(e['kind'] == 'operation.audit_result_failed' for e in events)
            lines = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
            results = [r for r in lines if r.get('channel') == 'claude:stop-session' and r.get('phase') == 'result']
            assert len(results) == 1 and results[0]['ok'] is False
        replay, created = d.ops.create(fanout.P, **envelope)
        assert not created and replay == d.ops.get(op['operation_id'], steps=False)
        assert d.ops.get(op['operation_id']) == done
        assert mock.channels().count('claude:stop-session') == 1
    finally:
        await stop_workers(d.ops)
        await d.fleet.close()
        await d.inventory.close()


@pytest.mark.parametrize('fault', [False, True])
async def test_negative_cleanup_ack_keeps_primary_outcome(cleanup_daemon, mock, monkeypatch, fault):
    """A negative cleanup ACK keeps its outcome; the fault case fails on 70de7942."""
    from bat_agent_connector import cleanup
    d = cleanup_daemon
    try:
        cp, created = await cleanup_tests.setup_work(d, mock)
        sid = created['result']['session_id']
        doc = await cleanup.preview(d.ops, cleanup_tests.CLEANER,
                                    {'kind': 'checkpoint', 'checkpoint_id': cp['checkpoint_id']})
        mock.handlers['claude:stop-session'] = lambda _: {'ok': False}
        if fault:
            fail_result(monkeypatch, d, mock, 'claude:stop-session', 'accepted', 'open', True)
        done = await cleanup_tests.apply(d, doc)
        assert (done['status'], done['error_code']) == ('uncertain', 'UNCERTAIN')
        assert sum(f['channel'] == 'claude:stop-session' and f['params']['sessionId'] == sid
                   for f in mock.invokes) == 1
        if fault:
            assert_warning(d, done, 'error', 'open')
        else:
            assert not done['external_refs'].get('audit_results')
    finally:
        await stop_workers(d.ops)
        await d.fleet.close()
        await d.inventory.close()


@pytest.mark.parametrize('case', ['guard', 'load', 'grant', 'lost-reply'])
async def test_append_terminal_transport_boundary(cleanup_daemon, mock, monkeypatch, case):
    """Workspace save requires its own transport signal; pre-save cases fail on 70de7942."""
    from bat_agent_connector import client as client_module
    from bat_agent_connector.errors import WriteRefused
    d = cleanup_daemon
    c = d.fleet.client('h1')
    try:
        cp = await cleanup_tests.make_checkpoint(d)
        d.fleet.config.host('h1').orchestrate_register_tabs = True
        in_tab = False
        append, checked, grant = c.append_workspace_terminal, c._invoke_checked, client_module.check_grant

        async def tab(*args, **kw):
            nonlocal in_tab
            in_tab = True
            try:
                return await append(*args, **kw)
            finally:
                in_tab = False

        async def invoke(channel, *args, **kw):
            if in_tab and case == 'load' and channel == 'workspace:load':
                raise WriteRefused('synthetic tab load refused')
            if in_tab and case == 'guard' and channel == 'workspace:save':
                async def refuse():
                    raise WriteRefused('synthetic save guard refused')
                kw['before_frame'] = refuse
            return await checked(channel, *args, **kw)

        def check_grant(g, host, channel, params):
            grant(g, host, channel, params)
            if in_tab and case == 'grant' and channel == 'workspace:save':
                raise WriteRefused('synthetic save grant refused')

        monkeypatch.setattr(c, 'append_workspace_terminal', tab)
        monkeypatch.setattr(c, '_invoke_checked', invoke)
        monkeypatch.setattr(client_module, 'check_grant', check_grant)
        calls = fail_result(monkeypatch, d, mock, 'workspace:save',
                            'unknown' if case == 'lost-reply' else 'accepted', 'open', True)
        done = await cleanup_tests.run(d, 'checkpoint.continue', {'checkpoint_id': cp['checkpoint_id']},
                                       {'instructions': 'synthetic tab boundary'}, key='tab-boundary')
        assert calls
        assert mock.channels().count('workspace:save') == (1 if case == 'lost-reply' else 0)
        if case == 'lost-reply':
            assert done['status'] == 'succeeded'
            assert_warning(d, done, 'unknown', 'open')
            assert next(iter(done['external_refs']['audit_results'].values()))['phase'] == 'append-terminal'
        else:
            assert (done['status'], done['error_code']) == ('failed', 'REFUSED')
            assert not done['external_refs'].get('audit_results')
            assert not [e for e in d.journal.api_events(0, 500, resource_id=done['operation_id'])['events']
                        if e['kind'] == 'operation.audit_result_failed']
        before = list(mock.invokes)
        again = await cleanup_tests.run(d, 'checkpoint.continue', {'checkpoint_id': cp['checkpoint_id']},
                                        {'instructions': 'synthetic tab boundary'}, key='tab-boundary')
        assert again == done and mock.invokes == before
    finally:
        await stop_workers(d.ops)
        await d.fleet.close()
        await d.inventory.close()


@pytest.mark.parametrize('reply,outcome', [({'ok': True}, 'accepted'), ({'ok': False}, 'error'),
    ({}, 'unknown'), ({'accepted': True}, 'unknown'), ({'ok': 'yes'}, 'unknown')])
async def test_stop_warning_requires_its_own_boolean(daemon, mock, monkeypatch, reply, outcome):
    """Non-positive cases fail on 70de7942; _stop's primary result is unchanged."""
    d = daemon
    adopt(api.MANUAL)
    mock.handlers['claude:stop-session'] = lambda _: reply
    fail_result(monkeypatch, d, mock, 'claude:stop-session', 'accepted', 'open', True)

    async def run(ctx):
        return await ctx.step('stop', lambda: lifecycle._stop(d.fleet, 'h1', api.MANUAL,
                              safety.Audit(d.fleet.config.safety), cleanup=True))
    d.ops.register(ActionDef('test.stop', 'operate', 'test', run))
    try:
        op, _ = d.ops.create(api.dashboard_principal(), action='test.stop', idempotency_key='stop-ack')
        await settle_operations(d.ops)
        done = d.ops.get(op['operation_id'])
        assert done['status'] == 'succeeded'
        assert done['result'] == {'stopped': True, 'result': reply}
        assert_warning(d, done, outcome, 'open')
        assert mock.channels().count('claude:stop-session') == 1
    finally:
        await stop_workers(d.ops)
        await d.fleet.close()
        await d.inventory.close()


async def test_failover_recovery_fence_is_not_audit_transport(daemon, mock, monkeypatch):
    """Fails on 70de7942: its conservative sent flag precedes registry writes."""
    from bat_agent_connector import failover_operations
    from bat_agent_connector.errors import WriteRefused
    d = daemon
    _add_wt_claude(mock, failover.SID)
    mock.handlers['git:log'] = lambda _: [{'hash': starts.SHA}]
    update = failover_operations.registry.update

    def refused(host, sid, **fields):
        if fields.get('start_sent') is True:
            raise WriteRefused('synthetic recovery-fence storage refusal')
        return update(host, sid, **fields)

    monkeypatch.setattr(failover_operations.registry, 'update', refused)
    fail_result(monkeypatch, d, mock, 'claude:start-session', 'accepted', 'open', True)
    try:
        done = await failover.create(d)
        assert (done['status'], done['error_code']) == ('failed', 'REFUSED')
        assert not done['external_refs'].get('audit_results')
        assert 'claude:start-session' not in mock.channels()
        assert not [e for e in d.journal.api_events(0, 500)['events']
                    if e['kind'] == 'operation.audit_result_failed']
    finally:
        await stop_workers(d.ops)
        await d.fleet.close()
        await d.inventory.close()


@pytest.mark.parametrize('kind,channel,key', [
    ('permission', 'claude:set-permission-mode', None),
    ('start', 'claude:start-session', 'ok'),
    ('prompt', 'claude:send-message', 'accepted'),
    ('create', 'worktree:create', 'success'),
])
@pytest.mark.parametrize('ack', [False, None], ids=['negative', 'missing'])
@pytest.mark.parametrize('fault', [False, True])
async def test_legacy_ack_warning_preserves_primary(daemon, mock, monkeypatch, kind, channel, key, ack, fault):
    """70de7942 fails negative permission/prompt and all missing-ACK warnings.

    No-fault controls and negative create/start already pass there.
    """
    from bat_agent_connector import orchestrate
    d = daemon
    adopt(api.MANUAL)
    mock.handlers['git:log'] = lambda _: [{'hash': starts.SHA}]
    c = d.fleet.client('h1')
    roundtrip = c._roundtrip

    async def reply(frame, *args, **kw):
        result = await roundtrip(frame, *args, **kw)
        if frame.get('channel') == channel:
            result = dict(result)
            if key is None:
                result['result'] = ack
            else:
                payload = dict(result['result'])
                if ack is None:
                    payload.pop(key, None)
                    if key == "accepted":
                        payload.pop("ok", None)
                else:
                    payload[key] = ack
                result['result'] = payload
        return result

    monkeypatch.setattr(c, '_roundtrip', reply)
    if fault:
        fail_result(monkeypatch, d, mock, channel, 'accepted', 'open', True)

    async def run(ctx):
        async def effect():
            if kind == 'permission':
                return await lifecycle.session_set_permissions(d.fleet, 'h1', api.MANUAL, 'default', confirm=True)
            return await orchestrate.session_start(d.fleet, 'h1', 'demo-project', confirm=True,
                use_worktree=kind == 'create', prompt='synthetic initial prompt' if kind == 'prompt' else None)
        return await ctx.step('legacy-ack', effect)
    d.ops.register(ActionDef('test.legacy-ack', 'operate', 'test', run))
    try:
        op, _ = d.ops.create(api.dashboard_principal(), action='test.legacy-ack', idempotency_key='legacy-ack')
        await settle_operations(d.ops)
        done = d.ops.get(op['operation_id'])
        expected = ('failed', 'REFUSED') if ack is False and kind in {'start', 'create'} else ('succeeded', None)
        assert (done['status'], done['error_code']) == expected
        assert mock.channels().count(channel) == 1
        warnings = done['external_refs'].get('audit_results', {})
        if fault:
            assert len(warnings) == 1
            assert next(iter(warnings.values()))['effect_outcome'] == ('error' if ack is False else 'unknown')
            events = [e for e in d.journal.api_events(0, 500, resource_id=op['operation_id'])['events']
                      if e['kind'] == 'operation.audit_result_failed']
            assert len(events) == 1
        else:
            assert not warnings
    finally:
        await stop_workers(d.ops)
        await d.fleet.close()
        await d.inventory.close()

FAMILIES = [
    ('resume', 'claude:client-resume'), ('send', 'claude:send-message'),
    ('interrupt', 'claude:interrupt-turn'), ('answer', 'claude:resolve-ask-user'),
    ('permissions', 'claude:set-permission-mode'), ('create', 'worktree:create'),
    ('start', 'claude:start-session'), ('prompt', 'claude:send-message'),
    ('successor', 'claude:start-session'), ('handoff', 'claude:send-message'),
    ('planner-stop', 'claude:stop-session'), ('cleanup-stop', 'claude:stop-session'),
    ('verification', None),
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
    if (family == 'resume' and outcome == 'accepted'
            or family == 'answer' and outcome == 'unknown'
            or family in {'cleanup-stop', 'verification'} and outcome != 'accepted'):
        pytest.skip('This branch has no post-effect audit append; no new audit obligation in this change')
    d = daemon
    mock.echo_sends = True
    if family.startswith(('create', 'start', 'prompt', 'successor', 'handoff', 'planner-stop')):
        mock.handlers['git:log'] = lambda _: [{'hash': starts.SHA}]
    if family.startswith(('successor', 'handoff')):
        _add_wt_claude(mock, failover.SID)
    elif not family.startswith(('create', 'start', 'prompt', 'verification')):
        adopt(api.MANUAL, **({'role': 'planner'} if family.startswith('planner-stop') else {}))
    if family == 'resume':
        mock.metas[api.MANUAL] = None
    if family == 'answer':
        mock.states[api.MANUAL]['pendingAskUser'] = {'toolUseId': 'ask-1', 'questions': [{'question': 'Choice?'}]}
    if family == 'planner-stop':
        d.fleet.config.host('h1').orchestrate_max_sessions = 3
        fanout.replies(mock)
    calls = fail_result(monkeypatch, d, mock, channel, outcome, stage, persistent)
    try:
        if family.startswith(('create', 'start', 'prompt')):
            out = await starts.create(d, params={'prompt': 'synthetic initial prompt'})
        elif family.startswith(('successor', 'handoff')):
            out = await failover.create(d)
        elif family == 'planner-stop':
            out = await fanout.create(d, target={'host': 'h1', 'session_id': api.MANUAL}, params={})
        elif family == 'verification':
            out = await verification.submit(d)
        else:
            if family == 'cleanup-stop':
                async def run(ctx):
                    return await ctx.step('stop', lambda: lifecycle._stop(d.fleet, 'h1', api.MANUAL,
                        safety.Audit(d.fleet.config.safety), cleanup=True))
                d.ops.register(ActionDef('test.stop', 'operate', 'test', run))
                action, params = 'test.stop', {}
            else:
                action, params = {
                    'resume': ('session.send', {'text': 'synthetic prompt'}),
                    'send': ('session.send', {'text': 'synthetic prompt'}),
                    'interrupt': ('session.interrupt', {'mode': 'soft'}),
                    'answer': ('session.answer', {'answers': ['yes'], 'tool_use_id': 'ask-1'}),
                    'permissions': ('session.permissions', {'mode': 'default'}),
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


@pytest.mark.parametrize('channel', ['worktree:rehydrate', 'worktree:merge'], ids=['rehydrate', 'merge'])
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
