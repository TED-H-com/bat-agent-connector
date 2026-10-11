"""Real journal lifecycle barriers; no process, remote repo or model calls."""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import replace

import pytest

from bat_agent_connector import pm_lifecycle as life
from bat_agent_connector import pm_lifecycle_gates as gates
from bat_agent_connector import pm_workspace
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import (
    RERUN,
    ActionDef,
    AmbiguousOutcome,
    OpContext,
    OperationError,
)
from tests.test_pm_discussions import (
    CONTRIBUTOR,
    OUTSIDER,
    OWNER,
    READER,
    act,
    member,
    prepared,
    read,
    workspace,
)
from tests.test_pm_discussions import ops as discussion_ops

base_ops = discussion_ops


@pytest.fixture
def ops(base_ops):
    life.install(base_ops)
    return base_ops


def request(ops, wid, action='pause', *, who=OWNER, params=None, expected=None, key=None):
    current = gates.head(ops.db, wid)
    return {'action': 'pm.lifecycle.' + action, 'target': {'workspace_id': wid},
            'params': {'reason': 'Explicit decision to preserve context and stop new investment', **(params or {})},
            'preconditions': {'expected_lifecycle_revision': current['revision'] if expected is None else expected},
            'idempotency_key': key or uuid.uuid4().hex}


async def decide(ops, wid, action='pause', *, who=OWNER, success=True, **kwargs):
    op = ops.create(who, **request(ops, wid, action, **kwargs))[0]
    await ops._execute(op['operation_id'])
    result = ops.get(op['operation_id'])
    if success:
        assert result['status'] == 'succeeded', result
    return result


def register(ops, fn, *, name='pm.synthetic.invest', admit=None):
    def admission(service, principal, target, params, pre):
        pm_workspace.require_member(service.db, principal, target['workspace_id'], write=True)
        return {'principal': pm_workspace.principal_binding(principal)}
    ops.register(ActionDef(name, 'manage', 'Synthetic effect for barrier verification', fn, admit or admission, ('workspace_id',)))


def submit(ops, wid, action='pm.synthetic.invest', *, key=None):
    return ops.create(OWNER, action=action, target={'workspace_id': wid}, params={}, preconditions={}, idempotency_key=key or uuid.uuid4().hex)[0]


async def test_lifecycle_is_independent_from_idea_thread_sprint_attempt_and_schema_version(ops):
    version = ops.db.execute('PRAGMA user_version').fetchone()[0]
    life.install(ops)
    assert ops.db.execute('PRAGMA user_version').fetchone()[0] == version
    target = await prepared(ops)
    wid = target['workspace_id']
    before = read(ops, target)
    assert life.get_lifecycle(ops, OWNER, wid)['lifecycle']['stage'] == 'idea'
    planned = await decide(ops, wid, 'set_stage', params={'stage': 'planning'})
    developed = await decide(ops, wid, 'set_stage', params={'stage': 'development'})
    assert planned['result']['execution_epoch'] == developed['result']['execution_epoch'] == 0
    assert read(ops, target) == before
    assert ops.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
    assert ops.db.execute('SELECT COUNT(*) FROM work_items').fetchone()[0] == 0
    with pytest.raises(OperationError, match='PM_MAINTENANCE_UNAVAILABLE'):
        await decide(ops, wid, 'set_stage', params={'stage': 'maintain'})
    assert life.get_lifecycle(ops, OWNER, wid)['maintenance']['available'] is False
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ops.db.execute("UPDATE pm_lifecycle_transitions SET reason='replacement'")


async def test_legacy_phase_remains_unknown_until_explicit_decision_and_reads_do_not_write(ops):
    wid = (await workspace(ops))['workspace_id']
    # Represents a pre-feature workspace, not an inferred migration from threads.
    ops.db.execute('DELETE FROM pm_lifecycle_heads WHERE workspace_id=?', (wid,))
    changes = ops.db.total_changes
    current = life.get_lifecycle(ops, OWNER, wid)['lifecycle']
    assert current['stage'] is None and current['coverage'] == 'legacy_unclassified' and current['revision'] == 0
    assert ops.db.total_changes == changes
    await decide(ops, wid)
    assert life.get_lifecycle(ops, OWNER, wid)['lifecycle']['stage'] == 'paused'


@pytest.mark.parametrize('who', [CONTRIBUTOR, READER, OUTSIDER, Principal('administrator', frozenset(), admin=True)])
async def test_only_actual_current_owner_can_decide_even_with_admin_capability(ops, who):
    wid = (await prepared(ops))['workspace_id']
    with pytest.raises(OperationError) as error:
        await decide(ops, wid, who=who)
    assert error.value.status in (403, 404)
    assert gates.head(ops.db, wid)['revision'] == 0


async def test_owner_revocation_after_admission_and_cas_preserve_original_head(ops):
    target = await prepared(ops)
    wid = target['workspace_id']
    await member(ops, pm_workspace.get_workspace(ops.db, OWNER, wid), CONTRIBUTOR.actor, 'owner')
    old = ops.create(OWNER, **request(ops, wid, 'pause'))[0]
    # The ordinary membership helper settles every pending operation. Execute
    # only this demotion so the admitted pause really runs after withdrawal.
    current = pm_workspace.get_workspace(ops.db, OWNER, wid)
    demotion = ops.create(OWNER, action='pm.workspace.member.set', target={'workspace_id': wid},
        params={'actor': OWNER.actor, 'role': 'contributor'}, preconditions={'expected_version': current['version']},
        idempotency_key=uuid.uuid4().hex)[0]
    await ops._execute(demotion['operation_id'])
    assert ops.get(demotion['operation_id'])['status'] == 'succeeded'
    assert gates.head(ops.db, wid)['revision'] == 0
    await ops._execute(old['operation_id'])
    assert ops.get(old['operation_id'])['error_code'] == 'FORBIDDEN'
    assert gates.head(ops.db, wid)['revision'] == 0
    first = ops.create(CONTRIBUTOR, **request(ops, wid, 'pause'))[0]
    second = ops.create(CONTRIBUTOR, **request(ops, wid, 'close'))[0]
    await ops._execute(first['operation_id'])
    await ops._execute(second['operation_id'])
    assert ops.get(second['operation_id'])['error_code'] == 'VERSION_CONFLICT'


async def test_stop_allows_explicit_notes_result_records_membership_and_original_key_replay(ops):
    target = await prepared(ops)
    wid = target['workspace_id']
    original = request(ops, wid)
    only_manage = Principal(OWNER.actor, frozenset({'manage'}))
    stopped = ops.create(only_manage, **original)[0]
    await ops._execute(stopped['operation_id'])
    assert ops.create(only_manage, **original)[0]['operation_id'] == stopped['operation_id']
    before = read(ops, target)['thread']['mode']
    note = await act(ops, target, 'pm.message.append', {'content': 'Keep this unanswered question for reopening'})
    assert note['message_id']
    await act(ops, target, 'pm.input.record', {'slot': 'scope', 'value': 'An unadopted possibility',
        'status': 'agent_assumption', 'source_ref': {'kind': 'statement', 'ref': 'Manual note'}})
    assert read(ops, target)['thread']['mode'] == before
    current = life.get_lifecycle(ops, OWNER, wid)
    assert 'pm.thread.plan' in current['blocked_actions'] and 'pm.message.append' not in current['blocked_actions']
    assert 'pm.workspace.create' not in life.capabilities(ops)['gated_actions']
    second = await workspace(ops)
    assert second['workspace_id'] != wid
    await member(ops, pm_workspace.get_workspace(ops.db, OWNER, wid), READER.actor, None)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        life.get_lifecycle(ops, READER, wid)


async def test_new_operation_final_admission_is_serialized_after_its_domain_precheck(ops, monkeypatch):
    wid = (await workspace(ops))['workspace_id']
    register(ops, lambda ctx: None)
    # An accepted callback is not authority to skip a later stop. Reproduce the
    # precheck-to-INSERT gap without starting a thread or holding an async SQL TX.
    original = ops.actions['pm.synthetic.invest'].admit
    original_request = request(ops, wid)
    stop = ops.create(OWNER, **original_request)[0]
    await ops._execute(stop['operation_id'])
    called = []
    def admission(*args):
        called.append(True)
        return original(*args)
    monkeypatch.setitem(ops.actions, 'pm.synthetic.invest', replace(ops.actions['pm.synthetic.invest'], admit=admission))
    count = ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
    with pytest.raises(OperationError, match='PM_LIFECYCLE_STOPPED'):
        submit(ops, wid)
    assert called and ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == count


async def test_queued_and_rolled_back_local_investment_never_resumes_in_reopened_epoch(ops):
    wid = (await workspace(ops))['workspace_id']
    applied = []
    async def work(ctx):
        return ctx.effect('local.prepare', lambda: (applied.append(True) or {'prepared': True}))
    register(ops, work)
    old = submit(ops, wid)
    # Started local transaction has rolled back: it is not an admitted external effect.
    ops._step_start(old['operation_id'], 'local.prepare', {}, local=True)
    paused = await decide(ops, wid)
    assert paused['result']['status'] == 'paused'
    await decide(ops, wid, 'reopen', params={'stage': 'planning'})
    await ops._execute(old['operation_id'])
    assert ops.get(old['operation_id'])['error_code'] == 'PM_LIFECYCLE_EPOCH_CHANGED' and not applied
    fresh = submit(ops, wid)
    await ops._execute(fresh['operation_id'])
    assert ops.get(fresh['operation_id'])['status'] == 'succeeded' and applied == [True]
    assert life.get_lifecycle(ops, OWNER, wid)['transition']['snapshot']['coverage']['automatic_resumption'] is False


async def test_direct_journal_planning_effect_checks_stop_after_original_receipt_lookup(ops):
    target = await prepared(ops)
    wid = target['workspace_id']
    statement = await act(ops, target, 'pm.input.record', {'slot': 'scope', 'value': 'Login only',
        'status': 'user_confirmed', 'source_ref': {'kind': 'statement', 'ref': 'Owner statement'}})
    old = ops.create(OWNER, action='pm.thread.plan', target=target,
        params={'selected_input_ids': [statement['input_id']], 'rationale': 'Prepare selected direction'},
        preconditions={'expected_version': read(ops, target)['thread']['version']}, idempotency_key=uuid.uuid4().hex)[0]
    await decide(ops, wid)
    await ops._execute(old['operation_id'])
    assert ops.get(old['operation_id'])['error_code'] == 'PM_LIFECYCLE_STOPPED'
    assert read(ops, target)['thread']['mode'] == 'idea'
    assert read(ops, target)['draft'] is None


async def test_prestop_external_admission_finishes_but_no_next_effect_after_stop(ops):
    wid = (await workspace(ops))['workspace_id']
    calls = []
    async def work(ctx):
        async def first():
            pause = await decide(ops, wid)
            assert pause['result']['status'] == 'pausing'
            calls.append('first')
            return {'external_ref': 'synthetic-original-ack'}
        await ctx.step('first', first)
        async def second():
            calls.append('second')
            return {}
        return await ctx.step('next', second)
    register(ops, work)
    old = submit(ops, wid)
    await ops._execute(old['operation_id'])
    assert calls == ['first']
    assert ops.get(old['operation_id'])['error_code'] == 'PM_LIFECYCLE_STOPPED'
    assert life.get_lifecycle(ops, OWNER, wid)['lifecycle']['status'] == 'paused'
    transition = life.get_lifecycle(ops, OWNER, wid)['transition']
    startseq = ops.db.execute("SELECT MIN(seq) FROM api_events WHERE resource_id=? AND kind='operation.step.started'", (old['operation_id'],)).fetchone()[0]
    assert startseq < transition['effective_sequence']


@pytest.mark.parametrize('proven_unsent', [False, True])
async def test_unknown_reconciliation_survives_stop_but_proven_unsent_rerun_is_denied(ops, proven_unsent):
    wid = (await workspace(ops))['workspace_id']
    calls, recovery = [], {'value': None}
    async def work(ctx):
        async def send():
            calls.append('send')
            raise AmbiguousOutcome('Synthetic acknowledgement lost')
        async def reconcile(_):
            return recovery['value']
        ack = await ctx.step('send', send, reconcile=reconcile)
        return ctx.effect('original.receipt', lambda: ack, receipt_only=True)
    register(ops, work)
    old = submit(ops, wid)
    await ops._execute(old['operation_id'])
    assert ops.get(old['operation_id'])['status'] == 'uncertain'
    await decide(ops, wid)
    assert life.get_lifecycle(ops, OWNER, wid)['lifecycle']['status'] == 'pausing'
    recovery['value'] = RERUN if proven_unsent else {'external_ref': 'original-result'}
    await ops._execute(old['operation_id'])
    done = ops.get(old['operation_id'])
    assert done['status'] == ('failed' if proven_unsent else 'succeeded')
    assert calls == ['send']
    assert life.get_lifecycle(ops, OWNER, wid)['lifecycle']['status'] == 'paused'
    if proven_unsent:
        assert done['error_code'] == 'PM_LIFECYCLE_STOPPED' and done['steps'][0]['status'] == 'failed'
    else:
        assert done['result']['external_ref'] == 'original-result'
        assert await ops.actions[old['action']].run(OpContext(ops, old)) == done['result']


async def test_retention_is_explicit_not_a_fake_receipt_or_implicit_writer_release(ops):
    wid = (await workspace(ops))['workspace_id']
    async def work(ctx):
        async def missing():
            raise AmbiguousOutcome('Original external state cannot be observed')
        return await ctx.step('unknown', missing)
    register(ops, work)
    old = submit(ops, wid)
    await ops._execute(old['operation_id'])
    closed = await decide(ops, wid, 'close')
    assert closed['result']['status'] == 'closing'
    retention = await decide(ops, wid, 'retain_inflight', params={'transition_id': closed['result']['transition_id'],
        'operation_ids': [old['operation_id']], 'reason': 'Keep the unresolved original operation available for read-back'})
    assert retention['result']['status'] == 'closed'
    row = life.list_inflight(ops, OWNER, wid)['inflight'][0]
    assert row['disposition'] == 'retained' and row['steps'][0]['status'] == 'uncertain'
    assert ops.get(old['operation_id'])['status'] == 'uncertain'
    await decide(ops, wid, 'reopen', params={'stage': 'idea'})
    await ops._execute(old['operation_id'])
    assert ops.get(old['operation_id'])['status'] == 'uncertain'
    assert life.list_inflight(ops, OWNER, wid)['inflight'][0]['retention']['reason']


async def test_server_bound_non_pm_lineage_uses_same_barrier_without_touching_other_actions(ops):
    wid = (await workspace(ops))['workspace_id']
    calls = []
    async def work(ctx):
        async def send():
            calls.append(ctx.operation_id)
            return {'sent': True}
        return await ctx.step('send', send)
    def admit(_ops, principal, target, params, pre):
        return {'principal': pm_workspace.principal_binding(principal), 'pm_lineage': {'workspace_id': wid}}
    register(ops, work, name='synthetic.bound', admit=admit)
    register(ops, work, name='synthetic.unrelated')
    old = submit(ops, wid, 'synthetic.bound')
    await decide(ops, wid)
    await ops._execute(old['operation_id'])
    assert ops.get(old['operation_id'])['error_code'] == 'PM_LIFECYCLE_STOPPED'
    with pytest.raises(OperationError, match='PM_LIFECYCLE_STOPPED'):
        submit(ops, wid, 'synthetic.bound')
    unrelated = submit(ops, wid, 'synthetic.unrelated')
    await ops._execute(unrelated['operation_id'])
    assert calls == [unrelated['operation_id']]


@pytest.mark.parametrize('cursor', [-1, True, 2**63, '0'])
async def test_cursors_reject_non_sqlite_or_ambiguous_values(ops, cursor):
    wid = (await workspace(ops))['workspace_id']
    for read_page in (life.list_inflight, life.list_transitions):
        with pytest.raises(OperationError, match='INVALID_PARAMS'):
            read_page(ops, OWNER, wid, after=cursor)


@pytest.mark.parametrize('tid', ['', False, 0, [], 'pmlt_bad'])
async def test_invalid_transition_selector_never_falls_back_to_current(ops, tid):
    wid = (await workspace(ops))['workspace_id']
    await decide(ops, wid)
    with pytest.raises(OperationError, match='NOT_FOUND'):
        life.list_inflight(ops, OWNER, wid, transition_id=tid)


async def test_history_whole_record_bytes_utf8_and_cursor_progression(ops, monkeypatch):
    wid = (await workspace(ops))['workspace_id']
    for action, params in [('pause', {}), ('reopen', {'stage': 'idea'}), ('close', {})]:
        await decide(ops, wid, action, params={**params, 'reason': '維護紀錄' * 800})
    page = life.list_transitions(ops, OWNER, wid, limit=1)
    first = page['transitions'][0]
    monkeypatch.setattr(life, 'MAX_READ_BYTES', len(json.dumps(page).encode()) + 30)
    pages, cursor = [], 0
    while True:
        result = life.list_transitions(ops, OWNER, wid, after=cursor, limit=100)
        assert len(json.dumps(result).encode()) <= life.MAX_READ_BYTES
        pages += result['transitions']
        if result['next_cursor'] is None:
            break
        assert result['next_cursor'] > cursor
        cursor = result['next_cursor']
    assert len(pages) == 3 and pages[0] == first
    monkeypatch.setattr(life, 'MAX_READ_BYTES', 100)
    with pytest.raises(OperationError) as error:
        life.list_transitions(ops, OWNER, wid)
    assert error.value.status == 413


async def test_another_journal_stop_commits_between_precheck_and_original_operation_insert(ops, monkeypatch):
    """An actual second client wins the journal writer before the new intent."""
    import asyncio
    import concurrent.futures

    from bat_agent_connector.operations import OperationService
    from bat_agent_connector.task_journal import Journal

    wid = (await workspace(ops))['workspace_id']
    stop = ops.create(OWNER, **request(ops, wid))[0]
    path = ops.journal.path
    def commit_stop():
        journal = Journal(path)
        try:
            other = OperationService(journal)
            pm_workspace.install(other)
            life.install(other)
            asyncio.run(other._execute(stop['operation_id']))
            assert other.get(stop['operation_id'])['status'] == 'succeeded'
        finally:
            journal.close()
    def precheck(service, principal, target, params, pre):
        assert gates.head(service.db, wid)['stage'] == 'idea'
        assert not service.db.in_transaction
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(commit_stop).result(timeout=10)
        return {'principal': pm_workspace.principal_binding(principal)}
    register(ops, lambda ctx: None, admit=precheck)
    before = ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
    with pytest.raises(OperationError, match='PM_LIFECYCLE_STOPPED'):
        submit(ops, wid)
    assert ops.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == before
    assert gates.head(ops.db, wid)['stage'] == 'paused'


@pytest.mark.parametrize('public_comment', [False, True])
async def test_only_canonical_admitted_local_issue_cancel_is_a_stopped_record_variant(ops, public_comment):
    from bat_agent_connector import pm_work_packages

    wid = (await workspace(ops))['workspace_id']
    selected = {'rationale': 'Stop tracking this work locally'}
    if public_comment:
        selected['public_reason'] = 'Optional new external comment'
    intent = {'workspace_id': wid, 'kind': 'cancel', 'selected': selected}
    def admit(service, principal, target, params, pre):
        return {'principal': pm_workspace.principal_binding(principal), 'issue_intent': intent,
                'issue_intent_digest': pm_work_packages._digest(intent)}
    writes = []
    async def work(ctx):
        return ctx.effect('local.cancel', lambda: writes.append('local') or {'state': 'cancelled'})
    register(ops, work, name='pm.issue.cancel', admit=admit)
    await decide(ops, wid)
    caps = life.get_lifecycle(ops, OWNER, wid)
    assert 'pm.issue.cancel' in caps['blocked_actions']
    assert caps['record_variants'] == {'pm.issue.cancel': {'absent_params': ['public_reason']}}
    if public_comment:
        # Empty caller params cannot disguise the trusted intent's publication.
        with pytest.raises(OperationError, match='PM_LIFECYCLE_STOPPED'):
            submit(ops, wid, 'pm.issue.cancel')
        assert writes == []
    else:
        original = submit(ops, wid, 'pm.issue.cancel')
        await ops._execute(original['operation_id'])
        assert ops.get(original['operation_id'])['result'] == {'state': 'cancelled'} and writes == ['local']
        assert not ops.db.execute("SELECT 1 FROM pm_lifecycle_steps WHERE kind='external'").fetchone()


@pytest.mark.parametrize('bad', ['missing_intent', 'mismatched_digest', 'extra_field'])
async def test_issue_cancel_raw_params_or_corrupt_binding_never_bypass_stopped_admission(ops, bad):
    from bat_agent_connector import pm_work_packages

    wid = (await workspace(ops))['workspace_id']
    intent = {'workspace_id': wid, 'kind': 'cancel', 'selected': {'rationale': 'A local note'}}
    if bad == 'extra_field':
        intent['selected']['unexpected_public_text'] = 'must not be treated as local'
    binding = {'issue_intent': intent, 'issue_intent_digest': pm_work_packages._digest(intent)}
    if bad == 'missing_intent':
        binding.pop('issue_intent')
    elif bad == 'mismatched_digest':
        binding['issue_intent_digest'] = '0' * 64
    register(ops, lambda ctx: None, name='pm.issue.cancel', admit=lambda *args: binding)
    await decide(ops, wid)
    with pytest.raises(OperationError, match='PM_LIFECYCLE_STOPPED'):
        submit(ops, wid, 'pm.issue.cancel')
