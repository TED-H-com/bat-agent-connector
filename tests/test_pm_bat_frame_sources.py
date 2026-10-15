"""Actual BAT frames reuse original sources without reusing current authority."""
import asyncio

import pytest

from bat_agent_connector import pm_access, pm_runs, pm_snapshots, pm_source_reads, pm_workspace
from bat_agent_connector import pm_bat_operations as bat
from bat_agent_connector import pm_conversation_proposals as proposals
from tests.test_pm_bat_structured_analysis import (
    ACTIONS,
    OWNER,
    bat_world,  # noqa: F401
    daemon,  # noqa: F401
    execute,
    pm,  # noqa: F401
    ready,  # noqa: F401
    request,
    result,
    structured,  # noqa: F401
    world,  # noqa: F401
)
from tests.test_pm_publication_source_reads import measured


def prior_check(ctx, run, *, sending=False):
    """Original checks and ordering, before the synchronous source scope."""
    principal = pm_workspace.admitted_principal(ctx)
    bat.pm_native_operations._scopes(principal)
    bat.pm_run_operations.require_execution(ctx.service.journal, run['execution_authority'], run['workspace_id'])
    pm_runs.require_requester(ctx.service.db, run)
    binding = ctx.admission_binding['pm_bat_binding']
    if (pm_runs._digest(binding) != ctx.admission_binding['pm_bat_binding_digest']
            or bat._binding(ctx.service, run['workspace_id']) != binding):
        raise bat.OperationError('PM_BAT_BINDING_CHANGED', 'The original PM workspace binding changed', 409)
    if sending:
        from bat_agent_connector.pm_lifecycle_gates import require_local
        ctx.check_cancel()
        require_local(ctx.service.db, ctx.operation_id)
        if bat.time.time() >= run['created_at'] + ctx.params.get('timeout_s', 300):
            raise bat.OperationError('PM_BAT_DEADLINE', 'The original PM analysis deadline elapsed', 409)


async def test_each_pm_frame_rechecks_writer_and_requester_with_one_source_read(structured, mock, monkeypatch, record_property):  # noqa: F811
    w, ops = structured, structured['d'].ops
    check, requester, visible = bat._check, pm_runs.require_requester, proposals._operation_visible
    active, observed, failures = [], [], []

    def count_requester(db, run):
        if active:
            active[-1]['requester'] += 1
        return requester(db, run)

    def count_visible(db, principal, operation):
        if active and operation['operation_id'] == active[-1]['operation_id']:
            active[-1]['sources'] += 1
        return visible(db, principal, operation)

    def checked(ctx, run, **kwargs):
        if pm_source_reads._READ.get() is not None:
            return check(ctx, run, **kwargs)
        if not observed:
            old = {'operation_id': ctx.operation_id, 'requester': 0, 'sources': 0}
            active.append(old)
            try:
                previous, old_queries = measured(ops.db, lambda: prior_check(ctx, run, **kwargs))
            finally:
                active.pop()
        before = ops.db.total_changes
        row = {'operation_id': ctx.operation_id, 'requester': 0, 'sources': 0}
        active.append(row)
        try:
            value, new_queries = measured(ops.db, lambda: check(ctx, run, **kwargs))
        finally:
            active.pop()
        try:
            assert ops.db.total_changes == before and pm_source_reads._READ.get() is None
            assert row['requester'] == 2 and row['sources'] == 1, row
            if not observed:
                assert value == previous is None
                # Prove an entire original-source traversal disappeared while
                # both live checks remain. Fixed scope bookkeeping is material
                # in this small graph; a percentage threshold would conflate it
                # with source fan-out in the separately measured joined graph.
                assert old['requester'] == old['sources'] == 2, old
                assert new_queries < old_queries, (old_queries, new_queries)
                record_property('pm_frame_prior_selects', old_queries)
                record_property('pm_frame_scoped_selects', new_queries)
        except AssertionError as error:
            failures.append(error)
            raise
        observed.append(row)
        return value

    monkeypatch.setattr(pm_runs, 'require_requester', count_requester)
    monkeypatch.setattr(proposals, '_operation_visible', count_visible)
    monkeypatch.setattr(bat, '_check', checked)
    out = await execute(w, request(w, proposals.ACTION))
    if failures:
        raise failures[0]
    assert out['status'] == 'succeeded', out
    assert len(observed) > 3  # Distinct fresh guards across actual native IO.
    assert result(w, out)['validation']['status'] == 'format_valid'
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('change', ['writer', 'source'])
async def test_next_pm_input_frame_rechecks_changed_writer_and_original_source(structured, mock, monkeypatch, action, change):  # noqa: F811
    w, ops = structured, structured['d'].ops
    client = ops.context['fleet'].client('h1')
    invoke, check, visible = client._invoke_checked, bat._check, pm_access.visible
    snapshot_read = pm_snapshots._out
    checks, changed = [], []
    original = ops.db.execute('SELECT operation_id FROM pm_messages WHERE message_id=?',
        (w['message']['message_id'],)).fetchone()[0]

    def observed_check(ctx, run, **kwargs):
        value = check(ctx, run, **kwargs)
        checks.append(ctx.operation_id)
        return value

    async def change_before_frame(channel, params, *args, **kwargs):
        if channel == 'claude:send-message':
            assert checks and pm_source_reads._READ.get() is None
            await asyncio.sleep(0)
            if change == 'writer':
                ops.db.execute('UPDATE pm_writer_slots SET generation=generation+1 WHERE workspace_id=?', (w['workspace_id'],))
            elif action == proposals.ACTION:
                monkeypatch.setattr(pm_access, 'visible', lambda db, principal, operation:
                    operation['operation_id'] != original and visible(db, principal, operation))
            else:
                source = next(ref for ref in w['intake']['document']['access_refs'] if ref['kind'] == 'snapshot')
                def withdrawn(reader, principal, row):
                    if row['snapshot_id'] == source['id']:
                        raise bat.OperationError('NOT_FOUND', 'The original intake snapshot is unavailable', 404)
                    return snapshot_read(reader, principal, row)
                monkeypatch.setattr(pm_snapshots, '_out', withdrawn)
            changed.append(channel)
        return await invoke(channel, params, *args, **kwargs)

    monkeypatch.setattr(bat, '_check', observed_check)
    monkeypatch.setattr(client, '_invoke_checked', change_before_frame)
    out = await execute(w, request(w, action))
    assert changed and out['status'] != 'succeeded', out
    assert 'claude:send-message' not in mock.channels()
    run = pm_runs.get_by_operation(ops.db, out['operation_id'])
    assert run['state'] not in {'completed', 'aborted'}
    assert ops.db.execute('SELECT run_id FROM pm_writer_slots WHERE workspace_id=?', (w['workspace_id'],)).fetchone()[0] == run['run_id']
    if change == 'source':
        assert not pm_access.visible(ops.db, OWNER, out)
        events = ops.journal.api_events(resource_id=out['operation_id'], limit=500)['events']
        assert events and not any(pm_access.event_visible(ops.db, OWNER, event) for event in events)
