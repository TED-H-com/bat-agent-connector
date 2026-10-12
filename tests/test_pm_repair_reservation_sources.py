"""A real reserve may recheck its baseline without excusing another effect."""
import traceback
from contextlib import contextmanager

from bat_agent_connector import pm_execution_runtime as runtime
from bat_agent_connector import pm_maintenance_repairs as repairs
from tests.test_pm_joined_lifecycle import bat_world, daemon, joined, world  # noqa: F401
from tests.test_pm_joined_lifecycle import test_same_ledger_development_and_semantic_dependency as run_story


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


async def test_exact_reserve_self_check_keeps_other_original_uncertainty_blocking(joined, mock, monkeypatch):  # noqa: F811
    original_check, inspected, failures = repairs._check, [], []
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
                ops._status(verifier_id, 'uncertain', reason='Original verifier outcome reconciliation pending')
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
        except AssertionError:
            failures.append(traceback.format_exc())
            raise
    monkeypatch.setattr(repairs, '_check', guarded_check)
    try:
        await run_story(joined, mock, monkeypatch)
    except BaseException:
        if failures:
            raise AssertionError(failures[0]) from None
        raise
    assert len(inspected) == 1
    assert repairs._RESERVING_WORKLOAD.get() is None
