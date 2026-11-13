"""Shared audit boundary fault matrix; synthetic effects use the real operation journal.

Every matrix case fails on d62093b8 (no post-effect API or durable warning).
Unbound and intent behavior is the unchanged baseline contract. The family labels
cover table A's audit boundaries; transport integration lives in
 test_audit_result_operations.py, not in this synthetic effect harness.
"""
import json

import pytest

from bat_agent_connector import safety
from bat_agent_connector.errors import InvokeError, InvokeTimeout, WriteRefused
from bat_agent_connector.operations import ActionDef
from tests import test_api_v1 as api
from tests.operation_helpers import settle_operations

daemon = api.daemon

# Stable selectors for distinct effects, including composed callers that inherit
# the enclosing operation step's context and legacy callers that remain unbound.
FAMILIES = [
    ('A01-resume', 'claude:client-resume', 'result'),
    ('A02-send', 'claude:send-message', 'result'),
    ('A03-interrupt', 'claude:interrupt-turn', 'result'),
    ('A04-answer', 'claude:resolve-ask-user', 'result'),
    ('A05-permissions', 'claude:set-permission-mode', 'result'),
    ('A06-create', 'worktree:create', 'result'),
    ('A07-start', 'claude:start-session', 'result'),
    ('A08-prompt', 'claude:send-message', 'result'),
    ('A09-rehydrate', 'worktree:rehydrate', 'result'),
    ('A10-merge', 'worktree:merge', 'result'),
    ('A11-successor', 'claude:start-session', 'result'),
    ('A12-handoff', 'claude:send-message', 'result'),
    ('A13-planner-stop', 'claude:stop-session', 'result'),
    ('A14-legacy-permissions', 'claude:set-permission-mode', 'result'),
    ('A15-legacy-create', 'worktree:create', 'result'),
    ('A16-legacy-start', 'claude:start-session', 'result'),
    ('A17-append-terminal', 'workspace:save', 'append-terminal'),
    ('A18-legacy-prompt', 'claude:send-message', 'result'),
    ('A19-rollback', 'worktree:remove', 'rollback'),
    ('A20-legacy-merge', 'worktree:merge', 'result'),
    ('A21-legacy-successor', 'claude:start-session', 'result'),
    ('A22-legacy-handoff', 'claude:send-message', 'result'),
    ('A23-cleanup-stop', 'claude:stop-session', 'result'),
    ('A24-verification', None, 'record'),
    ('A25-legacy-verification', None, 'record'),
]


def inject_append_fault(monkeypatch, stage, persistent, active=lambda: True):
    """Patch only safety module references, and always release the real FD."""
    calls = []
    real_mkdir, real_open, real_fdopen = safety.mkdir_private, safety.open_private, safety.fdopen

    def fault(current):
        if active() and current == stage and (persistent or not calls):
            calls.append(current)
            raise OSError('synthetic private storage error body')

    def mkdir(*args, **kw):
        fault('mkdir')
        return real_mkdir(*args, **kw)

    def opened(*args, **kw):
        fault('open')
        return real_open(*args, **kw)

    class File:
        def __init__(self, *args, **kw):
            self.file = real_fdopen(*args, **kw)

        def __enter__(self):
            return self

        def write(self, line):
            fault('write')
            return self.file.write(line)

        def flush(self):
            fault('flush')
            return self.file.flush()

        def __exit__(self, *_):
            self.file.close()
            fault('close')

    monkeypatch.setattr(safety, 'mkdir_private', mkdir)
    monkeypatch.setattr(safety, 'open_private', opened)
    monkeypatch.setattr(safety, 'fdopen', File)
    return calls


@pytest.mark.parametrize('family,channel,phase', FAMILIES, ids=[r[0] for r in FAMILIES])
@pytest.mark.parametrize('outcome', ['accepted', 'error', 'unknown'])
@pytest.mark.parametrize('stage', ['mkdir', 'open', 'write', 'flush', 'close'])
@pytest.mark.parametrize('persistent', [False, True], ids=['one-shot', 'persistent'])
async def test_shared_effect_boundary(daemon, monkeypatch, tmp_path, family, channel, phase, outcome, stage, persistent):
    ops, audit = daemon.ops, safety.Audit(daemon.fleet.config.safety, tmp_path / 'audit.jsonl')
    effects = []
    calls = inject_append_fault(monkeypatch, stage, persistent)

    async def run(ctx):
        async def effect():
            effects.append(channel)
            # Two observations of the same effect deliberately exercise warning
            # deduplication, even when the append failure itself is persistent.
            for _ in range(2):
                audit.record_after_effect(effect_sent=True, effect_outcome=outcome, phase=phase, tool=family,
                    host='h1', session_id='synthetic-session', channel=channel,
                    text='synthetic private prompt', error='synthetic private error')
            if outcome == 'error':
                raise InvokeError('synthetic remote error')
            if outcome == 'unknown':
                raise InvokeTimeout('synthetic lost reply')
            return {'accepted': True, 'original_receipt': 'synthetic-receipt'}
        return await ctx.step('effect', effect)

    ops.register(ActionDef('test.audit', 'operate', 'test', run))
    request = dict(action='test.audit', idempotency_key='original-audit-key')
    op, _ = ops.create(api.dashboard_principal(), **request)
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert (done['status'], done['error_code']) == {
        'accepted': ('succeeded', None), 'error': ('failed', 'BAT_ERROR'),
        'unknown': ('uncertain', 'UNCERTAIN')}[outcome]
    warnings = done['external_refs']['audit_results']
    assert len(warnings) == 1
    effect_id, warning = next(iter(warnings.items()))
    assert warning == dict(state='failed', code='AUDIT_RESULT_WRITE_FAILED', phase=phase,
                           failure_stage=stage, effect_outcome=outcome)
    events = [e for e in daemon.journal.api_events(0, 500)['events']
              if e['kind'] == 'operation.audit_result_failed']
    assert len(events) == 1 and events[0]['body'] == dict(effect_id=effect_id, **warning)
    assert 'private' not in json.dumps([warnings, events])
    assert len(calls) == (2 if persistent else 1)
    assert len(effects) == 1
    replay, reused = ops.create(api.dashboard_principal(), **request)
    assert not reused and replay["operation_id"] == done["operation_id"]
    assert ops.get(replay["operation_id"]) == done
    if outcome == 'unknown':
        # Reconciliation re-enters the step but must not call the effect again.
        api.due_now(daemon, op['operation_id'])
        await settle_operations(ops)
    assert len(effects) == 1
    assert ops.get(op['operation_id'])['external_refs']['audit_results'] == warnings
    assert len([e for e in daemon.journal.api_events(0, 500)['events']
                if e['kind'] == 'operation.audit_result_failed']) == 1


@pytest.mark.parametrize('stage', ['mkdir', 'open', 'write', 'flush', 'close'])
@pytest.mark.parametrize('bound', [False, True])
def test_intent_always_strict_and_unbound_result_strict(daemon, monkeypatch, tmp_path, stage, bound):
    audit = safety.Audit(daemon.fleet.config.safety, tmp_path / 'audit.jsonl')
    inject_append_fault(monkeypatch, stage, True)
    warnings = []
    with pytest.raises((OSError, WriteRefused)):
        if bound:
            with safety.result_audit_sink(lambda *args: warnings.append(args)):
                audit.record(phase='attempt')
        else:
            audit.record_after_effect(effect_sent=True, effect_outcome='accepted', phase='result')
    assert not warnings


def test_observation_rejects_unbounded_warning_fields():
    # Baseline already drops these unknown fields; the new allowlist must not
    # make arbitrary paths, bodies, or enum-like input public.
    from bat_agent_connector.observation import summary
    assert summary(dict(effect_id='a' * 64, phase='result', failure_stage='open', effect_outcome='accepted')) == {}
    assert summary(dict(code='AUDIT_RESULT_WRITE_FAILED', effect_id='private', phase='private', failure_stage='/private/path',
                        effect_outcome='private', error_body='private', token='private')) == {'code': 'AUDIT_RESULT_WRITE_FAILED'}


async def test_warning_and_step_receipt_rollback_together(daemon, monkeypatch, tmp_path):
    """New behavior: no independently committed warning if settlement fails (B01)."""
    import sqlite3
    ops = daemon.ops
    audit = safety.Audit(daemon.fleet.config.safety, tmp_path / 'audit.jsonl')
    inject_append_fault(monkeypatch, 'open', True)
    original = ops._observation_event

    def fail_event(op_id, kind, data):
        original(op_id, kind, data)
        if kind == 'operation.audit_result_failed':
            raise sqlite3.OperationalError('synthetic journal outage')

    monkeypatch.setattr(ops, '_observation_event', fail_event)

    async def run(ctx):
        async def effect():
            audit.record_after_effect(effect_sent=True, effect_outcome='accepted', phase='result')
            return {'accepted': True}
        return await ctx.step('effect', effect)

    ops.register(ActionDef('test.audit', 'operate', 'test', run))
    op, _ = ops.create(api.dashboard_principal(), action='test.audit', idempotency_key='atomic')
    await settle_operations(ops)
    done = ops.get(op['operation_id'])
    assert done['error_code'] == 'INTERNAL'
    assert not done['external_refs'].get('audit_results')
    assert done['steps'][0]['status'] == 'started'
    assert not [e for e in daemon.journal.api_events(0, 500)['events']
                if e['kind'] == 'operation.audit_result_failed']


def test_unsent_result_record_keeps_original_refusal(daemon, monkeypatch, tmp_path):
    """Baseline refusal invariant; the new entry point must not hide an intent fault."""
    audit = safety.Audit(daemon.fleet.config.safety, tmp_path / 'audit.jsonl')
    inject_append_fault(monkeypatch, 'open', True)
    warnings = []
    with safety.result_audit_sink(lambda *args: warnings.append(args)), pytest.raises(WriteRefused):
        audit.record_after_effect(effect_outcome='error', effect_sent=False, phase='result')
    assert not warnings


@pytest.mark.parametrize('reply,outcome', [(True, 'accepted'), (False, 'error'), ({'ok': True}, 'accepted'),
    ({'accepted': False, 'ok': True}, 'error'), ({'success': True}, 'accepted'), ({}, 'unknown'),
    (None, 'unknown'), ({'accepted': 'true'}, 'unknown')])
def test_warning_classification_requires_explicit_acceptance(reply, outcome):
    """New helper absent on baseline; ACK wording must not invent acceptance."""
    assert safety.audit_reply_outcome(reply) == outcome


async def test_warning_contexts_are_isolated_between_workers(daemon, monkeypatch, tmp_path):
    """New sink absent on baseline; concurrent operations cannot share warnings."""
    import asyncio
    ops = daemon.ops
    audit = safety.Audit(daemon.fleet.config.safety, tmp_path / 'audit.jsonl')
    inject_append_fault(monkeypatch, 'open', True)
    arrived = []
    both = asyncio.Event()
    async def run(ctx):
        async def effect():
            arrived.append(ctx.operation_id)
            if len(arrived) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 5)
            audit.record_after_effect(effect_sent=True, effect_outcome='accepted', phase=ctx.params['phase'])
            return {'accepted': True}
        return await ctx.step('effect', effect)
    ops.register(ActionDef('test.audit', 'operate', 'test', run))
    ids = [ops.create(api.dashboard_principal(), action='test.audit', params={'phase': phase},
                      idempotency_key=phase)[0]['operation_id'] for phase in ('result', 'record')]
    await settle_operations(ops)
    for oid, phase in zip(ids, ('result', 'record')):
        op = ops.get(oid)
        assert op['status'] == 'succeeded'
        warnings = op['external_refs']['audit_results']
        assert len(warnings) == 1 and next(iter(warnings.values()))['phase'] == phase
    with pytest.raises(WriteRefused):
        audit.record_after_effect(effect_sent=True, effect_outcome='accepted', phase='result')
