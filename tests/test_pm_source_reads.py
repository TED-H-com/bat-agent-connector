"""A synchronous source decision cannot become a reusable authority grant."""
import asyncio
import contextvars
import sqlite3

import pytest

from bat_agent_connector import pm_read_cache
from bat_agent_connector import pm_source_reads as reads
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError

OWNER = Principal('reader', frozenset({'observe'}), credential_id='original-credential')


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / 'source-reads.db', isolation_level=None)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('CREATE TABLE original_source(id TEXT PRIMARY KEY, visible INTEGER)')
    connection.execute("INSERT INTO original_source VALUES('original',1)")
    yield connection
    connection.close()


def decision(db, principal=OWNER, calls=None):
    def check():
        if calls is not None:
            calls.append(principal.credential_id)
        return (principal.allows('observe') and principal.credential_id == OWNER.credential_id
            and db.execute("SELECT visible FROM original_source WHERE id='original'").fetchone()[0] == 1)
    return reads.check(db, principal, 'original', 'original', check)


def test_only_one_synchronous_snapshot_reuses_original_decision(db):
    calls = []
    with reads.scope(db, OWNER):
        assert decision(db, calls=calls) and decision(db, calls=calls)
        assert calls == [OWNER.credential_id]
    assert not db.in_transaction
    assert decision(db, calls=calls)
    assert len(calls) == 2


def test_nested_guards_reuse_only_their_own_completed_source_proof(db):
    calls, changes = [], db.total_changes
    with reads.scope(db, OWNER):
        with pm_read_cache.guarded():
            assert decision(db, calls=calls)
            with pm_read_cache.guarded():
                assert decision(db, calls=calls)
            assert decision(db, calls=calls)
            assert len(calls) == 2
        with pm_read_cache.guarded():
            assert decision(db, calls=calls)
            assert len(calls) == 3  # A new guard cannot borrow the old proof.
        assert decision(db, calls=calls)
        assert len(calls) == 4  # No partial proof becomes ordinary authority.
    assert db.total_changes == changes


def test_distinct_guard_contexts_keep_the_original_source_node_bound(db, monkeypatch):
    monkeypatch.setattr(reads, 'MAX_NODES', 4)
    calls = []
    with reads.scope(db, OWNER):
        for _ in range(4):
            with pm_read_cache.guarded():
                assert decision(db, calls=calls)
        with pm_read_cache.guarded():
            assert not decision(db, calls=calls)
        assert len(calls) == 4


def test_step_snapshot_reuses_completed_source_across_independent_readers(db):
    from bat_agent_connector.pm_release_baselines import _snapshot
    calls = []
    rotated = Principal(OWNER.actor, OWNER.scopes, credential_id='other-credential')
    unscoped = Principal(OWNER.actor, frozenset(), credential_id=OWNER.credential_id)
    with _snapshot(db), pm_read_cache.scope(db):
        assert decision(db, calls=calls)
        assert decision(db, calls=calls)
        assert calls == [OWNER.credential_id]
        assert not decision(db, rotated, calls)
        assert not decision(db, unscoped, calls)
        assert decision(db, calls=calls)
        assert len(calls) == 3
        db.execute('UPDATE original_source SET visible=0')
        assert not decision(db, calls=calls)
        assert not decision(db, calls=calls)
        db.execute('UPDATE original_source SET visible=1')
        assert decision(db, calls=calls)
        assert decision(db, calls=calls)
        assert len(calls) == 7  # No reuse after the first write, even on restoration.
    assert not db.in_transaction
    assert decision(db, calls=calls)
    assert len(calls) == 8


def test_request_memo_does_not_retain_source_budget_refusal(db, monkeypatch):
    from bat_agent_connector.pm_release_baselines import _snapshot
    with _snapshot(db), pm_read_cache.scope(db):
        with monkeypatch.context() as bounded:
            bounded.setattr(reads, 'MAX_NODES', 1)
            assert not reads.check(db, OWNER, 'outer', 'parent', lambda: decision(db))
        assert reads.check(db, OWNER, 'outer', 'parent', lambda: decision(db))


@pytest.mark.parametrize('kind', ['original', 'conversation'])
def test_step_snapshot_reuses_nested_source_across_different_roots(db, kind):
    from bat_agent_connector import pm_conversation_proposals
    from bat_agent_connector.pm_release_baselines import _snapshot
    calls = []
    with _snapshot(db), pm_read_cache.scope(db):
        for parent in ('first-root', 'second-root', 'third-root'):
            if kind == 'conversation':
                assert pm_conversation_proposals._bounded_access(db, OWNER, 'proposal', parent,
                    lambda: decision(db, calls=calls))
            else:
                assert reads.check(db, OWNER, 'outer', parent, lambda: decision(db, calls=calls))
        assert calls == [OWNER.credential_id]


def test_public_metadata_does_not_grant_anonymous_original_source_access(db):
    from bat_agent_connector.pm_source_lineage import visible_roots
    assert visible_roots(db, None, set())
    assert not visible_roots(db, None, {'op_' + '1' * 32})
    # An anonymous public read also cannot borrow a surrounding caller's scope.
    with reads.scope(db, OWNER):
        assert decision(db)
        assert not visible_roots(db, None, {'op_' + '1' * 32})
        assert decision(db)


@pytest.mark.parametrize('kind', ['execution_verifier', 'quality_sources', 'workload_sources', 'repair_sources'])
def test_typed_wrappers_preserve_original_depth_and_keep_their_own_bound(db, kind):
    def descend(level, maximum):
        if level == maximum:
            return True
        return reads.check(db, OWNER, 'original_root', level, lambda:
            reads.check(db, OWNER, kind, level, lambda: descend(level + 1, maximum)))
    assert descend(0, reads.MAX_DEPTH)
    assert not descend(0, reads.MAX_DEPTH + 1)


def test_quality_verifier_workload_and_original_wrappers_coexist_at_supported_depth(db):
    def descend(level, maximum):
        if level == maximum:
            return True
        return reads.check(db, OWNER, 'original_root', level, lambda:
            reads.check(db, OWNER, 'execution_verifier', level, lambda:
                reads.check(db, OWNER, 'quality_sources', level, lambda:
                    reads.check(db, OWNER, 'workload_sources', level, lambda: descend(level + 1, maximum)))))
    assert descend(0, reads.MAX_DEPTH)
    assert not descend(0, reads.MAX_DEPTH + 1)
    # One outer repair reader must not halve any previously supported chain.
    assert reads.check(db, OWNER, 'repair_sources', 'outer', lambda: descend(0, reads.MAX_DEPTH))


def test_credential_and_scope_are_distinct_even_for_same_actor(db):
    rotated = Principal(OWNER.actor, OWNER.scopes, credential_id='other-credential')
    unscoped = Principal(OWNER.actor, frozenset(), credential_id=OWNER.credential_id)
    assert OWNER == rotated  # Dataclass equality is not the authorization key.
    calls = []
    with reads.scope(db, OWNER):
        assert decision(db, calls=calls)
        assert not decision(db, rotated, calls)
        assert not decision(db, unscoped, calls)
        assert decision(db, calls=calls)
    assert len(calls) == 3


def test_mutation_refuses_old_scope_and_rollback_starts_fresh_read(db):
    db.execute('BEGIN')
    with pytest.raises(OperationError, match='fresh snapshot'):
        with reads.scope(db, OWNER):
            assert decision(db)
            db.execute('UPDATE original_source SET visible=0')
            decision(db)
    # The source reader must not roll back its caller's transaction.
    assert db.in_transaction
    assert db.execute('SELECT visible FROM original_source').fetchone()[0] == 0
    db.execute('ROLLBACK')
    assert decision(db)
    db.execute('UPDATE original_source SET visible=0')
    assert not decision(db)
    db.execute('UPDATE original_source SET visible=1')
    assert decision(db)


@pytest.mark.parametrize('boundary', ['COMMIT', 'ROLLBACK'])
def test_transaction_replacement_cannot_publish_old_decision(db, boundary):
    changes = db.total_changes
    with pytest.raises(OperationError, match='fresh snapshot'):
        with reads.scope(db, OWNER):
            assert decision(db)
            db.execute(boundary)
            db.execute('BEGIN')
            assert db.total_changes == changes
            decision(db)
    # This is a replacement transaction, not the reader's original ownership.
    assert db.in_transaction
    db.execute('ROLLBACK')
    assert decision(db)


def test_copied_closed_context_never_reuses_old_source_permission(db):
    with reads.scope(db, OWNER):
        assert decision(db)
        copied = contextvars.copy_context()
    db.execute('UPDATE original_source SET visible=0')
    assert not copied.run(decision, db)


async def test_same_task_cannot_carry_source_decision_across_await(db):
    with pytest.raises(OperationError, match='fresh snapshot'):
        with reads.scope(db, OWNER):
            assert decision(db)
            await asyncio.sleep(0)
            decision(db)
    assert not db.in_transaction
    assert decision(db)


async def test_child_task_cannot_borrow_open_source_scope(db):
    async def child():
        with pytest.raises(OperationError, match='fresh snapshot'):
            decision(db)
    with pytest.raises(OperationError, match='fresh snapshot'):
        with reads.scope(db, OWNER):
            assert decision(db)
            await asyncio.create_task(child())
    assert not db.in_transaction


def test_external_withdrawal_is_seen_by_next_read_not_cached_globally(db):
    path = db.execute('PRAGMA database_list').fetchone()[2]
    other = sqlite3.connect(path, isolation_level=None)
    try:
        with reads.scope(db, OWNER):
            assert decision(db)
            other.execute('UPDATE original_source SET visible=0')
            assert decision(db)  # One original WAL read point, still consistent.
        assert not decision(db)
    finally:
        other.close()


def test_cycle_and_bounded_traversal_refuse_without_recursion_error(db, monkeypatch):
    def cycle():
        return reads.check(db, OWNER, 'source', 'cycle', cycle)
    assert not reads.check(db, OWNER, 'source', 'cycle', cycle)
    monkeypatch.setattr(reads, 'MAX_DEPTH', 3)
    def chain(index):
        return reads.check(db, OWNER, 'source', index, lambda: True if index == 3 else chain(index + 1))
    assert not chain(0)
    monkeypatch.setattr(reads, 'MAX_NODES', 3)
    with reads.scope(db, OWNER):
        assert all(reads.check(db, OWNER, 'source', index, lambda: True) for index in range(3))
        assert not reads.check(db, OWNER, 'source', 4, lambda: True)


def test_reader_preserves_callers_nested_savepoints(db):
    db.execute('BEGIN')
    db.execute('SAVEPOINT outside_reader')
    with reads.scope(db, OWNER):
        db.execute('SAVEPOINT nested_reader')
        assert decision(db)
        db.execute('RELEASE nested_reader')
    db.execute('ROLLBACK TO outside_reader')
    db.execute('RELEASE outside_reader')
    db.execute('COMMIT')
    assert not db.in_transaction


def test_root_wrappers_preserve_existing_conversation_depth_allowance(db):
    def prior_conversation(index):
        # Existing conversation checker admits 64 active proposal/source nodes.
        return index < 64 and (index == 63 or prior_conversation(index + 1))
    def now(index):
        return reads.check(db, OWNER, 'original_root', index,
            lambda: reads.check(db, OWNER, 'conversation.proposal', index,
                lambda: True if index == 63 else now(index + 1)))
    assert prior_conversation(0) and now(0)


def test_alternating_principals_cannot_reset_traversal_bound(db, monkeypatch):
    monkeypatch.setattr(reads, 'MAX_DEPTH', 4)
    people = [OWNER, Principal('different-reader', OWNER.scopes, credential_id='different-credential')]
    calls = []
    def cycle(index):
        calls.append(index)
        return reads.check(db, people[index % 2], 'original_root', 'same-source', lambda: cycle(index + 1))
    with pytest.raises(OperationError, match='fresh snapshot'):
        cycle(0)
    assert len(calls) == 5 and not db.in_transaction
