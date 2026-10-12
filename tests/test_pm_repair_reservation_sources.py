"""A real reserve may recheck its baseline without excusing another effect."""
import traceback
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_access, pm_source_reads
from bat_agent_connector import pm_execution_runtime as runtime
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests import pm_joined_maintenance_helpers as maintenance
from tests.pm_joined_lifecycle_helpers import OWNER
from tests.test_pm_joined_lifecycle import bat_world, daemon, joined, world  # noqa: F401
from tests.test_pm_joined_lifecycle import test_same_ledger_development_and_semantic_dependency as run_story
from tests.test_pm_source_reads_joined import measured


class SourcesChecked(Exception):
    """Stop this focused source case before the independent delivery story."""


@contextmanager
def journal_change(db):
    # Test-owned rollback isolates a negative persisted state. Production read
    # scopes never roll back a caller's writes to probe transaction identity.
    db.execute('SAVEPOINT fixture_reservation_negative')
    try:
        yield
    finally:
        db.execute('ROLLBACK TO fixture_reservation_negative')
        db.execute('RELEASE fixture_reservation_negative')


def source_queries(ops, principal, parent, *, scoped):
    count = 0
    def trace(sql):
        nonlocal count
        if sql.lstrip().upper().startswith('SELECT'):
            count += 1
    ops.db.set_trace_callback(trace)
    try:
        if scoped:
            with pm_source_reads.scope(ops.db, principal):
                repairs._full_access(ops, principal, parent)
        else:
            repairs._full_access(ops, principal, parent)
    finally:
        ops.db.set_trace_callback(None)
    return count


def saved_sources(w, parent, recorded, monkeypatch, record_property):
    """Actual daemon operations/events retain whole original quality sources."""
    ops = w['d'].ops
    child = repairs._child(ops, parent)
    originals = (parent, child, recorded)
    # Compare the same immutable saved-repair graph with the original traversal.
    old, old_queries, old_roots = measured(ops.db, parent, monkeypatch, legacy=True)
    new, new_queries, new_roots = measured(ops.db, parent, monkeypatch, legacy=False)
    assert old is new is True
    assert new_queries < old_queries
    assert sum(new_roots.values()) < sum(old_roots.values())
    record_property('repair_original_source_queries', old_queries)
    record_property('repair_scoped_source_queries', new_queries)
    record_property('repair_original_root_visits', sum(old_roots.values()))
    record_property('repair_scoped_root_visits', sum(new_roots.values()))
    direct = source_queries(ops, OWNER, parent, scoped=False)
    scoped = source_queries(ops, OWNER, parent, scoped=True)
    record_property('repair_direct_full_access_queries', direct)
    record_property('repair_scoped_full_access_queries', scoped)
    assert scoped <= direct
    for op in originals:
        assert ops.get(op['operation_id'], principal=OWNER)['operation_id'] == op['operation_id']
    events = []
    for identity in {op['operation_id'] for op in originals} | {'pmrp_' + parent['operation_id'][3:]}:
        events.extend(ops.journal.api_events(resource_id=identity, limit=500)['events'])
    assert events and all(pm_access.event_visible(ops.db, OWNER, event) for event in events)
    saved = repairs.get_repair(ops, OWNER, w['workspace_id'], 'pmrp_' + parent['operation_id'][3:])
    assert saved['repair']['progress']['quality'], saved
    # Withdraw only one original discussion source, not the caller's membership
    # or any saved repair record. Every derivative must disappear together.
    first = ops.db.execute('''SELECT m.operation_id FROM pm_messages m JOIN operations o ON o.operation_id=m.operation_id
        WHERE m.workspace_id=? AND o.action='pm.message.append' ORDER BY m.sequence LIMIT 1''',
        (w['workspace_id'],)).fetchone()[0]
    visible = pm_access.visible
    with monkeypatch.context() as withdrawn:
        withdrawn.setattr(pm_access, 'visible', lambda db, principal, op:
            op['operation_id'] != first and visible(db, principal, op))
        for op in originals:
            with pytest.raises(OperationError) as error:
                ops.get(op['operation_id'], principal=OWNER)
            assert error.value.status == 404
        assert all(not pm_access.event_visible(ops.db, OWNER, event) for event in events)
        with pytest.raises(OperationError) as error:
            repairs.get_repair(ops, OWNER, w['workspace_id'], 'pmrp_' + parent['operation_id'][3:])
        assert error.value.status == 404
    assert all(pm_access.visible(ops.db, OWNER, op) for op in originals)
    assert repairs.get_repair(ops, OWNER, w['workspace_id'], 'pmrp_' + parent['operation_id'][3:]) == saved


async def test_exact_reserve_self_check_keeps_other_original_uncertainty_blocking(joined, mock, monkeypatch, record_property):  # noqa: F811
    original_check, inspected, failures = repairs._check, [], []
    original_settle, source_reads = maintenance.settle, []
    async def settle(w, request):
        if request['action'] == repairs.RECORD and request['params']['evidence']['kind'] == 'quality':
            # Wait for this local receipt, then inspect it before the story's
            # next explicit parent advance admits publication/delivery work.
            # run_due() here would also wait on unrelated scheduled children.
            ops = w['d'].ops
            created = ops.create(OWNER, **request)[0]
            await ops._execute(created['operation_id'])
            result = ops.get(created['operation_id'])
            assert result['status'] == 'succeeded', result
            parent_id = repairs._row(w['d'].ops, w['workspace_id'], request['target']['repair_id'])['operation_id']
            saved_sources(w, w['d'].ops.get(parent_id), result, monkeypatch, record_property)
            source_reads.append(parent_id)
            raise SourcesChecked
        return await original_settle(w, request)
    monkeypatch.setattr(maintenance, 'settle', settle)
    def check(ops, principal, target, params, pre, *, reserving=False):
        if reserving:
            ctx = repairs._RESERVING_WORKLOAD.get()
            parent = ops.get(ctx.operation_id)
            roots = {row[0] for row in ops.db.execute(
                "SELECT original_operation_id FROM pm_source_lineage WHERE resource_type='operation' AND resource_id=?", (ctx.operation_id,))}
            original_id = next(oid for oid in joined['ledger'].references['execution_ids'] if oid in roots)
            child = ops.get(original_id)
            reader = runtime.observer(ops, child)
            assert repairs._only_current_local_reservation(ops, parent)
            assert runtime._settled(ops, child, reader['operation_id'])
            # An ordinary workload read of these identical journal rows must
            # still see the running repair. No completed-turn proof is changed.
            token = repairs._RESERVING_WORKLOAD.set(None)
            try:
                assert not runtime._settled(ops, child, reader['operation_id'])
            finally:
                repairs._RESERVING_WORKLOAD.reset(token)
            assert not repairs._only_current_local_reservation(ops, child)
            assert not repairs._only_current_local_reservation(repairs.workload._Reader(ops.db), parent)

            # A real original verifier is a different derivative of this same
            # baseline. Its uncertain status cannot borrow the reserve scope.
            original_index = joined['ledger'].references['execution_ids'].index(original_id)
            verifier_id = joined['ledger'].references['passing_verification_ids'][original_index]
            assert original_id in {row[0] for row in ops.db.execute(
                "SELECT original_operation_id FROM pm_source_lineage WHERE resource_type='operation' AND resource_id=?", (verifier_id,))}
            with journal_change(ops.db):
                # Reproduce recovery after the original verifier receipt was
                # saved but its overall operation outcome is still uncertain.
                ops.db.execute("UPDATE operations SET status='uncertain' WHERE operation_id=?", (verifier_id,))
                assert not runtime._settled(ops, child, reader['operation_id'])
            assert runtime._settled(ops, child, reader['operation_id'])

            # Even the matching operation loses the exception if its original
            # step lacks the central local-effect proof, or is already unknown.
            with journal_change(ops.db):
                ops.db.execute("UPDATE pm_lifecycle_steps SET kind='external' WHERE operation_id=? AND name='pm.repair.reserve'", (ctx.operation_id,))
                assert not runtime._settled(ops, child, reader['operation_id'])
            with journal_change(ops.db):
                ops.db.execute("UPDATE operation_steps SET status='uncertain' WHERE operation_id=? AND name='pm.repair.reserve'", (ctx.operation_id,))
                assert not runtime._settled(ops, child, reader['operation_id'])
            inspected.append(ctx.operation_id)
        return original_check(ops, principal, target, params, pre, reserving=reserving)
    def guarded_check(*args, **kwargs):
        try:
            return check(*args, **kwargs)
        except OperationError:
            # The joined story deliberately exercises duplicate admission.
            # Preserve that expected domain error without masking later failures.
            raise
        except Exception:
            failures.append(traceback.format_exc())
            raise
    monkeypatch.setattr(repairs, '_check', guarded_check)
    try:
        await run_story(joined, mock, monkeypatch, record_property)
    except SourcesChecked:
        pass
    except BaseException:
        if failures:
            raise AssertionError(failures[0]) from None
        raise
    assert len(inspected) == 1
    assert source_reads == inspected
    assert repairs._RESERVING_WORKLOAD.get() is None


def test_repair_backedge_does_not_reuse_another_credential(monkeypatch):
    ops, op, seen = SimpleNamespace(db=object()), {'operation_id': 'original-repair'}, []
    first = Principal('same-actor', frozenset({'observe'}), credential_id='first')
    other = Principal('same-actor', frozenset({'observe'}), credential_id='other')
    def access(ops, principal, op):
        seen.append(principal.credential_id)
        repairs._full_access(ops, principal, op)  # The same original backedge is bounded.
        if principal.credential_id == 'first':
            repairs._full_access(ops, other, op)
        else:
            raise OperationError('NOT_FOUND', 'Other original credential is unavailable', 404)
    monkeypatch.setattr(repairs, '_access', access)
    with pytest.raises(OperationError, match='Other original credential'):
        repairs._full_access(ops, first, op)
    assert seen == ['first', 'other']
    assert not repairs._READING.get()
