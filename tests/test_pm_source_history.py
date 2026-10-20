"""An observer can reuse historical booleans only at a proved fresh read point."""
import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_read_cache
from bat_agent_connector import pm_source_history as history
from bat_agent_connector import pm_source_reads as reads
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_journal import Journal

OWNER = Principal('owner', frozenset({'observe'}), credential_id='original')


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / 'source-history.sqlite', isolation_level=None)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('CREATE TABLE original_source(visible INTEGER)')
    connection.execute('INSERT INTO original_source VALUES(1)')
    yield connection
    connection.close()


def decision(db, calls, *, principal=OWNER):
    def source():
        calls.append(1)
        return principal.credential_id == 'original' and bool(db.execute('SELECT visible FROM original_source').fetchone()[0])
    return reads.check(db, principal, 'baseline_sources', 'original', source)


async def test_fresh_frames_reuse_only_completed_history_and_close_every_transaction(db):
    calls = []
    with history.operation(db, 'observer'):
        assert decision(db, calls)
        assert not db.in_transaction
        await asyncio.sleep(0)
        assert decision(db, calls)
        assert calls == [1]
        assert not db.in_transaction
    assert not db.execute("SELECT 1 FROM temp.sqlite_schema WHERE type='trigger'").fetchone()
    assert decision(db, calls) and len(calls) == 2


@pytest.mark.parametrize('rollback', [False, True])
def test_same_connection_changes_invalidate_even_if_transaction_rolls_back(db, rollback):
    calls = []
    # Exercise an already prepared statement: installing the tracker must also
    # cover SQLite's statement cache, not only newly prepared SQL.
    db.execute('UPDATE original_source SET visible=?', (1,))
    with history.operation(db, 'observer'):
        assert decision(db, calls)
        db.execute('BEGIN')
        db.execute('UPDATE original_source SET visible=?', (0,))
        db.execute('ROLLBACK' if rollback else 'COMMIT')
        assert decision(db, calls) is rollback
        assert len(calls) == 2


def other_connection(db):
    path = next(row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main')
    return sqlite3.connect(path, isolation_level=None)


def test_external_commits_invalidate_without_a_local_trigger_callback(db):
    calls, other = [], other_connection(db)
    try:
        with history.operation(db, 'observer'):
            assert decision(db, calls)
            other.execute('UPDATE original_source SET visible=0')
            assert not decision(db, calls)
            assert len(calls) == 2
    finally:
        other.close()


def test_backup_restore_on_the_connection_cannot_reuse_previous_data(db, tmp_path):
    replacement = sqlite3.connect(tmp_path / 'replacement.sqlite', isolation_level=None)
    replacement.execute('CREATE TABLE original_source(visible INTEGER)')
    replacement.execute('INSERT INTO original_source VALUES(0)')
    calls = []
    try:
        with history.operation(db, 'observer'):
            assert decision(db, calls)
            replacement.backup(db)
            assert not decision(db, calls) and len(calls) == 2
    finally:
        replacement.close()


def test_commit_between_version_read_and_anchor_cannot_seed_an_old_grant(db, monkeypatch):
    calls, other = [], other_connection(db)
    stamp, armed = history._History.stamp, []
    def raced(self):
        result = stamp(self)
        if armed and not db.in_transaction:
            armed.clear()
            other.execute('UPDATE original_source SET visible=0')
        return result
    try:
        with history.operation(db, 'observer'):
            assert decision(db, calls)
            monkeypatch.setattr(history._History, 'stamp', raced)
            armed.append(True)
            assert not decision(db, calls)
            assert len(calls) == 2
    finally:
        other.close()


def test_callers_old_snapshot_cannot_publish_a_grant_under_a_new_version(db):
    calls, other = [], other_connection(db)
    try:
        with history.operation(db, 'observer'):
            assert decision(db, calls)
            db.execute('BEGIN')
            db.execute('SELECT * FROM original_source').fetchone()
            other.execute('UPDATE original_source SET visible=0')
            assert decision(db, calls)  # The caller still sees its old snapshot.
            db.execute('COMMIT')
            assert not decision(db, calls)
            assert len(calls) == 3
    finally:
        if db.in_transaction:
            db.execute('ROLLBACK')
        other.close()


def test_journals_fresh_write_snapshot_can_reuse_a_proved_read_point(db):
    calls, journal = [], SimpleNamespace(db=db)
    with history.operation(db, 'observer'):
        assert decision(db, calls)
        with Journal.tx(journal):
            assert decision(db, calls)
            assert len(calls) == 1


def test_nested_journal_savepoint_cannot_borrow_a_callers_older_snapshot(db):
    calls, other, journal = [], other_connection(db), SimpleNamespace(db=db)
    try:
        with history.operation(db, 'observer'):
            assert decision(db, calls)
            db.execute('BEGIN')
            db.execute('SELECT * FROM original_source').fetchone()
            other.execute('UPDATE original_source SET visible=0')
            with Journal.tx(journal):
                assert decision(db, calls)
                assert len(calls) == 2
            db.execute('COMMIT')
            assert not decision(db, calls) and len(calls) == 3
    finally:
        if db.in_transaction:
            db.execute('ROLLBACK')
        other.close()


def test_commit_before_journal_begin_cannot_advance_an_old_proof(db, monkeypatch):
    calls, other, journal = [], other_connection(db), SimpleNamespace(db=db)
    stamp, armed = history._History.stamp, []
    def raced(self):
        result = stamp(self)
        if armed and not db.in_transaction:
            armed.clear()
            other.execute('UPDATE original_source SET visible=0')
        return result
    try:
        with history.operation(db, 'observer'):
            assert decision(db, calls)
            monkeypatch.setattr(history._History, 'stamp', raced)
            armed.append(True)
            with Journal.tx(journal):
                assert not decision(db, calls) and len(calls) == 2
    finally:
        other.close()


def test_new_main_tables_are_tracked_and_temp_shadow_tables_disable_reuse(db):
    calls = []
    with history.operation(db, 'observer'):
        assert decision(db, calls)
        db.execute('CREATE TABLE later(value INTEGER)')
        db.execute('INSERT INTO later VALUES(1)')
        assert decision(db, calls)
        db.execute('UPDATE later SET value=2')
        assert decision(db, calls) and len(calls) == 3
        db.execute('CREATE TEMP TABLE original_source(visible INTEGER)')
        db.execute('INSERT INTO temp.original_source VALUES(0)')
        assert not decision(db, calls)
        db.execute('UPDATE temp.original_source SET visible=1')
        assert decision(db, calls) and len(calls) == 5
        db.execute('DROP TABLE temp.original_source')


def test_attached_database_keeps_the_original_cold_read_path(db, tmp_path):
    calls = []
    with history.operation(db, 'observer'):
        assert decision(db, calls)
        db.execute('ATTACH DATABASE ? AS additional', (str(tmp_path / 'additional.sqlite'),))
        try:
            assert decision(db, calls)
            assert decision(db, calls) and len(calls) == 3
        finally:
            db.execute('DETACH DATABASE additional')


def test_removed_tracker_cannot_publish_a_new_cached_answer(db):
    calls = []
    with history.operation(db, 'observer'):
        assert decision(db, calls)
        name = db.execute("SELECT name FROM temp.sqlite_schema WHERE type='trigger' LIMIT 1").fetchone()[0]
        db.execute('DROP TRIGGER temp."' + name + '"')
        assert decision(db, calls) and len(calls) == 2
        assert decision(db, calls) and len(calls) == 3


def test_changed_source_bounds_cannot_borrow_an_earlier_proof(db, monkeypatch):
    with history.operation(db, 'observer'):
        assert decision(db, [])
        monkeypatch.setattr(reads, 'MAX_NODES', 0)
        assert not decision(db, [])


def test_changed_bounds_inside_journals_snapshot_do_not_reuse_an_old_proof(db, monkeypatch):
    with history.operation(db, 'observer'):
        assert decision(db, [])
        with Journal.tx(SimpleNamespace(db=db)):
            monkeypatch.setattr(reads, 'MAX_NODES', 0)
            assert not decision(db, [])


def test_credential_partial_guard_and_refusal_answers_do_not_escape(db):
    calls = []
    with history.operation(db, 'observer'):
        for _ in range(2):
            with pm_read_cache.guarded():
                assert decision(db, calls)
        assert len(calls) == 2
        assert decision(db, calls)
        rotated = Principal(OWNER.actor, OWNER.scopes, credential_id='rotated')
        assert not decision(db, calls, principal=rotated)
        assert decision(db, calls) and len(calls) == 4
    for _ in range(2):
        with history.operation(db, 'observer'):
            def refused():
                calls.append(1)
                pm_read_cache.refuse()
                return True
            assert reads.check(db, OWNER, 'baseline_sources', 'refused', refused)
            assert reads.check(db, OWNER, 'baseline_sources', 'refused', refused)
    assert len(calls) == 8


async def test_inherited_task_cannot_borrow_the_observers_completed_proof(db):
    calls = []
    with history.operation(db, 'observer'):
        assert decision(db, calls)
        async def child():
            return decision(db, calls)
        assert await asyncio.create_task(child())
        assert decision(db, calls) and len(calls) == 2


async def test_source_scope_still_refuses_an_await_and_cleanup_handles_cancellation(db):
    with pytest.raises(OperationError, match='fresh snapshot'):
        with history.operation(db, 'observer'), reads.scope(db, OWNER):
            assert decision(db, [])
            await asyncio.sleep(0)
    assert not db.in_transaction
    with pytest.raises(asyncio.CancelledError):
        with history.operation(db, 'observer'):
            assert decision(db, [])
            raise asyncio.CancelledError
    assert not db.execute("SELECT 1 FROM temp.sqlite_schema WHERE type='trigger'").fetchone()


@pytest.mark.parametrize('boundary', ['function', 'trigger'])
def test_interruption_after_sqlite_creates_a_resource_still_cleans_it(db, boundary):
    class Interrupted:
        name = None
        interrupted = False

        def __getattr__(self, key):
            return getattr(db, key)

        def create_function(self, name, count, callback):
            db.create_function(name, count, callback)
            self.name = name
            if boundary == 'function' and callback is not None and not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt

        def execute(self, sql, *args):
            result = db.execute(sql, *args)
            if boundary == 'trigger' and sql.startswith('CREATE TEMP TRIGGER') and not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt
            return result

    connection = Interrupted()
    with pytest.raises(KeyboardInterrupt):
        with history.operation(connection, 'observer'):
            decision(connection, [])
    assert not db.execute("SELECT 1 FROM temp.sqlite_schema WHERE type='trigger'").fetchone()
    with pytest.raises(sqlite3.OperationalError):
        db.execute('SELECT ' + connection.name + '()').fetchone()
