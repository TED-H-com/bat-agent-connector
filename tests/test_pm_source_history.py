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
from tests.test_pm_github_observation import base_ops, ops, release_ops, workload_ops  # noqa: F401

OWNER = Principal('owner', frozenset({'observe'}), credential_id='original')


OPERATION_CHANGES = {
    'operation_id': 'replacement', 'actor': 'another-actor', 'entry': 'another-entry',
    'idem_key': 'another-key', 'request_hash': 'another-hash', 'action': 'fixture.changed',
    'target': '{"changed":true}', 'params': '{"changed":true}', 'preconditions': '{"changed":true}',
    'status': 'cancelled', 'status_reason': 'changed', 'error_code': 'CHANGED', 'result': '{}',
    'external_refs': '{}', 'cancel_requested': 1, 'attempts': 1, 'uncertain_tries': 1,
    'next_run_at': 2, 'created_at': 2,
}
STEP_CHANGES = {
    'operation_id': 'other', 'seq': 2, 'name': 'pm.github.page.2.001',
    'request': '{"changed":true}', 'external_ref': 'changed', 'error': 'changed', 'started_at': 2,
}


def ignore_cases():
    cases = []

    def add(table, verb, changes, ignored=False, subject='observer', label=None):
        cases.append(pytest.param(table, verb, subject, changes, ignored,
            id='-'.join((table, verb, subject, label or '+'.join(changes) or 'row'))))

    add('operations', 'UPDATE', {'version': 1, 'updated_at': 2}, True)
    add('operations', 'UPDATE', {'version': 1, 'updated_at': 2}, subject='other')
    for column, value in OPERATION_CHANGES.items():
        add('operations', 'UPDATE', {column: value})
    add('operations', 'INSERT', {})
    add('operations', 'DELETE', {})

    page_names = ('pm.github.started', 'pm.github.page.1.001', 'pm.github.page.2.001',
        'pm.github.page.1.001.preserve', 'pm.github.page.2.001.preserve')
    outside = ('pm.github.record', 'pm.github.page.3.001', 'pm.github.page.1.01',
        'pm.github.page.1.0001', 'pm.github.page.1.001.preserved')
    for table in ('operation_steps', 'pm_lifecycle_steps'):
        for name in page_names:
            add(table, 'INSERT', {'name': name}, True, label=name)
        for name in outside:
            add(table, 'INSERT', {'name': name}, label=name)
        add(table, 'INSERT', {}, subject='other')
        add(table, 'DELETE', {})
    add('operation_steps', 'UPDATE', {'status': 'succeeded', 'response': '{}', 'finished_at': 2}, True)
    add('operation_steps', 'UPDATE', {'status': 'succeeded'}, subject='other')
    add('operation_steps', 'UPDATE', {'status': 'succeeded'}, subject='non-page')
    add('operation_steps', 'UPDATE', {'status': 'succeeded', 'name': 'pm.github.record'})
    for column, value in STEP_CHANGES.items():
        add('operation_steps', 'UPDATE', {column: value})
    for column, value in {'name': 'pm.github.record', 'workspace_id': 'another-workspace',
            'execution_epoch': 1, 'kind': 'investment', 'admitted_at': 2}.items():
        add('pm_lifecycle_steps', 'UPDATE', {column: value})

    add('pm_github_observation_pages', 'INSERT', {}, True)
    add('pm_github_observation_pages', 'INSERT', {}, subject='other')
    add('pm_github_observation_pages', 'UPDATE', {'receipt': '{"changed":true}'})
    add('pm_github_observation_pages', 'DELETE', {})

    add('api_events', 'INSERT', {}, True)
    for column, value in {'resource_type': 'workspace', 'resource_id': 'other',
            'kind': 'operation.step', 'body': '{"changed":true}'}.items():
        add('api_events', 'INSERT', {column: value}, column == 'body')
    add('api_events', 'UPDATE', {'body': '{"changed":true}'})
    add('api_events', 'DELETE', {})
    for table, column in (('api_event_context', 'context'), ('api_event_resources', 'evidence_ref')):
        add(table, 'INSERT', {}, True)
        for subject in ('other', 'another-type', 'another-kind'):
            add(table, 'INSERT', {}, subject=subject)
        add(table, 'UPDATE', {column: '{}'})
        add(table, 'DELETE', {})
    return cases


@pytest.mark.parametrize('table,verb,subject,changes,ignored', ignore_cases())
def test_only_observers_exact_bookkeeping_writes_preserve_history(ops, table, verb, subject, changes, ignored):  # noqa: F811
    """Pin the SQL ignore boundary against real production tables and columns."""
    from bat_agent_connector import pm_lifecycle
    from bat_agent_connector.operations import ActionDef

    pm_lifecycle.schema(ops.journal)
    ops.register(ActionDef('fixture.observer', 'observe', 'Unexecuted fixture operation', lambda ctx: {}))
    ids = {key: ops.create(OWNER, action='fixture.observer', target={}, params={}, idempotency_key=key)[0]['operation_id']
        for key in ('observer', 'other')}
    ids['replacement'] = 'op_' + 'e' * 32
    ids['non-page'] = ids['observer']
    db = ops.db
    assert {row[1] for row in db.execute('PRAGMA table_info(operations)')} == (
        set(OPERATION_CHANGES) | {'version', 'updated_at'})
    assert {row[1] for row in db.execute('PRAGMA table_info(operation_steps)')} == (
        set(STEP_CHANGES) | {'status', 'response', 'finished_at'})

    # Exercise the AFTER invalidation trackers independently of the separate
    # immutable-write guards. Keep the production tables/columns intact.
    for name, in db.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name=?", (table,)).fetchall():
        if name in {table + '_update_immutable', table + '_delete_immutable'}:
            db.execute('DROP TRIGGER "' + name + '"')

    events = {}
    for key, resource_type, resource_id, kind in (
            ('observer', 'operation', ids['observer'], 'operation.step.started'),
            ('other', 'operation', ids['other'], 'operation.step.started'),
            ('another-type', 'workspace', ids['observer'], 'operation.step.started'),
            ('another-kind', 'operation', ids['observer'], 'operation.accepted')):
        events[key] = db.execute('''INSERT INTO api_events(resource_type,resource_id,kind,body,actor,created_at)
            VALUES(?,?,?,'{}',?,1)''', (resource_type, resource_id, kind, OWNER.actor)).lastrowid
    events['non-page'] = events['observer']
    rows = {
        'operations': dict(db.execute('SELECT * FROM operations WHERE operation_id=?', (ids[subject],)).fetchone())
            if table == 'operations' else {},
        'operation_steps': {'operation_id': ids.get(subject), 'seq': 1,
            'name': 'pm.github.record' if subject == 'non-page' else 'pm.github.page.1.001',
            'status': 'started', 'request': '{}', 'response': None, 'external_ref': None, 'error': None,
            'started_at': 1, 'finished_at': None},
        'pm_lifecycle_steps': {'operation_id': ids.get(subject), 'name': 'pm.github.page.1.001',
            'workspace_id': 'ws_' + 'a' * 32, 'execution_epoch': 0, 'kind': 'record', 'admitted_at': 1},
        'pm_github_observation_pages': {'operation_id': ids.get(subject), 'step': 'pm.github.page.1.001',
            'receipt': '{}', 'receipt_digest': 'a' * 64},
        'api_events': {'resource_type': 'operation', 'resource_id': ids['observer'], 'kind': 'operation.step.started',
            'body': '{}', 'actor': OWNER.actor, 'created_at': 1},
        'api_event_context': {'seq': events[subject], 'context': '{}'},
        'api_event_resources': {'seq': events[subject], 'resource_type': 'operation',
            'resource_id': ids['observer'], 'linked_at_seq': events[subject], 'evidence_ref': None},
    }
    row = rows[table]

    def insert(value):
        columns = ','.join('"' + key + '"' for key in value)
        db.execute(f'INSERT INTO {table}({columns}) VALUES({",".join("?" for _ in value)})', tuple(value.values()))

    if table not in {'operations', 'api_events'} and verb != 'INSERT':
        insert(row)
    if table == 'operations' and verb == 'INSERT':
        row.update(operation_id='op_' + 'f' * 32, idem_key='inserted')
    key, value = ('seq', events['observer']) if table == 'api_events' else next(iter(row.items()))
    changed = {key: ids.get(value, value) if key in {'operation_id', 'resource_id'} else value
        for key, value in changes.items()}
    calls = []

    def predicate():
        calls.append(True)
        return bool(db.execute('SELECT COUNT(*) FROM operations').fetchone()[0])

    def check():
        return reads.check(db, OWNER, 'baseline_sources', 'fixed-proof', predicate)

    with history.operation(db, ids['observer']):
        assert check() and check() and len(calls) == 1
        memo = history._CURRENT.get().memo
        assert memo
        if verb == 'INSERT':
            insert({**row, **changed})
        elif verb == 'UPDATE':
            assignments = ','.join('"' + column + '"=?' for column in changed)
            db.execute(f'UPDATE {table} SET {assignments} WHERE "{key}"=?', (*changed.values(), value))
        else:
            db.execute(f'DELETE FROM {table} WHERE "{key}"=?', (value,))
        assert bool(memo) is ignored
        assert check() and len(calls) == (1 if ignored else 2)
        assert check() and len(calls) == (1 if ignored else 2)


async def test_observer_scope_entry_failure_cannot_poison_writes_after_rollback(ops, monkeypatch):  # noqa: F811
    from bat_agent_connector.operations import OpContext
    from tests import test_pm_github_observation as github

    data = await github.prepared(ops, monkeypatch)
    op = ops.create(github.OWNER, **github.request(ops, data))[0]
    closed, close = [], history._History.close

    def captured(self):
        closed.append((self, self.db.in_transaction))
        return close(self)

    def unavailable():
        raise OSError('Synthetic savepoint identity allocation failed')

    monkeypatch.setattr(history._History, 'close', captured)
    with monkeypatch.context() as entry:
        # scope() has executed BEGIN but has not entered its try/except yet.
        # Replace this module's dependency, never the global UUID function.
        entry.setattr(reads, 'uuid', SimpleNamespace(uuid4=unavailable))
        with pytest.raises(OSError, match='savepoint identity'):
            await github.observation._run(OpContext(ops, op))
    tracker, in_transaction = closed[0]
    assert len(closed) == 1 and in_transaction and ops.db.in_transaction
    assert not tracker.active and not tracker.memo
    ops.db.execute('ROLLBACK')
    # ROLLBACK restores the TEMP triggers dropped by close(). They must be
    # inert, rather than calling an unregistered function on the next write.
    assert ops.db.execute("SELECT 1 FROM temp.sqlite_schema WHERE type='trigger' AND name GLOB ?",
        (tracker.name + '_*',)).fetchone()
    ops.db.execute('UPDATE operations SET cancel_requested=1 WHERE operation_id=?', (op['operation_id'],))
    assert ops.get(op['operation_id'])['cancel_requested']


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


def test_schema_change_during_tracker_install_cannot_certify_untracked_writes(db, monkeypatch):
    other, calls, created = other_connection(db), [], []
    install = history._History._install

    def raced(self):
        result = install(self)
        if not created:
            # DDL commits after the tracked table list, before its version stamp.
            other.execute('CREATE TABLE later_source(visible INTEGER)')
            other.execute('INSERT INTO later_source VALUES(1)')
            created.append(True)
        return result

    def source():
        calls.append(1)
        return bool(db.execute('SELECT visible FROM later_source').fetchone()[0])

    def selected():
        return reads.check(db, OWNER, 'baseline_sources', 'late', source)

    try:
        monkeypatch.setattr(history._History, '_install', raced)
        with history.operation(db, 'observer'):
            with Journal.tx(SimpleNamespace(db=db)):
                assert selected()
                db.execute('UPDATE later_source SET visible=0')
                assert not selected()
                assert len(calls) == 2
            # The next fresh frame installs complete coverage and may reuse it.
            db.execute('UPDATE later_source SET visible=1')
            assert selected() and selected() and len(calls) == 3
            db.execute('UPDATE later_source SET visible=0')
            assert not selected() and len(calls) == 4
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
