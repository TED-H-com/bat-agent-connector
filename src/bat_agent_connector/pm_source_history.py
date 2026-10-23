"""Versioned historical source booleans for one GitHub observation only.

TEMP triggers observe writes on this connection; data_version observes commits
on other connections. No durable schema or proof survives this operation. Live
authority, scope, cancellation and deadlines never enter this memo.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

_CURRENT = ContextVar('pm_observer_source_history', default=None)
_TRANSACTION = ContextVar('pm_observer_source_transaction', default=None)
KINDS = frozenset({'baseline_sources', 'package_sources', 'requirement_sources', 'work_batch_sources',
    'execution_runtime_sources', 'execution_verifier', 'quality_sources', 'workload_sources', 'repair_sources'})


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


def _quoted(value):
    return '"' + value.replace('"', '""') + '"'


def _temporary_tables(db):
    from .pm_external_history import GRANT_TABLES

    if any(row[1] not in {'main', 'temp'} for row in db.execute('PRAGMA database_list')):
        return None
    rows = db.execute("SELECT t.name,t.sql,m.name FROM temp.sqlite_schema t LEFT JOIN main.sqlite_schema m "
        "ON m.name=t.name WHERE t.type='table' ORDER BY t.name").fetchall()
    if any(name not in GRANT_TABLES or shadow is not None for name, _, shadow in rows):
        return None
    return tuple((name, sql) for name, sql, _ in rows)


class _History:
    def __init__(self, db, operation_id):
        self.db, self.operation_id, self.owner = db, operation_id, _owner()
        self.name = 'pm_source_history_' + uuid.uuid4().hex
        self.generation, self.memo, self.triggers = 0, {}, []
        self.schema, self.active = None, True

    def changed(self):
        self.generation += 1
        self.memo.clear()
        return 0

    def usable(self, db):
        return self.active and self.db is db and self.owner == _owner()

    def _ignore(self, table, verb, columns):
        # Only receipts produced by this observer are irrelevant to its already
        # proved historical sources. Changes to its request, authority, status
        # or cancellation still invalidate, as does every other operation.
        oid = "'" + self.operation_id.replace("'", "''") + "'"
        def owned(prefix):
            return f'{prefix}.operation_id={oid}'
        def equal_except(allowed):
            return ' AND '.join(f'OLD.{_quoted(c)} IS NEW.{_quoted(c)}' for c in columns if c not in allowed)
        def page(prefix):
            return (f"({prefix}.name='pm.github.started' OR {prefix}.name GLOB 'pm.github.page.[12].[0-9][0-9][0-9]'"
                f" OR {prefix}.name GLOB 'pm.github.page.[12].[0-9][0-9][0-9].preserve')")
        if table == 'operations' and verb == 'UPDATE' and 'operation_id' in columns:
            return f'({owned("OLD")} AND {owned("NEW")} AND {equal_except({"version", "updated_at"})})'
        if table == 'operation_steps' and {'operation_id', 'name'} <= set(columns):
            if verb == 'INSERT':
                return f'({owned("NEW")} AND {page("NEW")})'
            if verb == 'UPDATE':
                same = equal_except({'status', 'response', 'finished_at'})
                return f'({owned("OLD")} AND {owned("NEW")} AND {page("NEW")} AND {same})'
        if table == 'pm_lifecycle_steps' and verb == 'INSERT' and {'operation_id', 'name'} <= set(columns):
            return f'({owned("NEW")} AND {page("NEW")})'
        if table == 'pm_github_observation_pages' and verb == 'INSERT' and 'operation_id' in columns:
            return owned('NEW')
        if table == 'api_events' and verb == 'INSERT' and {'resource_type', 'resource_id', 'kind'} <= set(columns):
            return f"(NEW.resource_type='operation' AND NEW.resource_id={oid} AND NEW.kind GLOB 'operation.step.*')"
        if table in {'api_event_context', 'api_event_resources'} and verb == 'INSERT' and 'seq' in columns:
            return (f"EXISTS(SELECT 1 FROM main.api_events WHERE seq=NEW.seq AND resource_type='operation'"  # noqa: S608
                f" AND resource_id={oid} AND kind GLOB 'operation.step.*')")
        return '0'

    def _install(self):
        for name in self.triggers:
            self.db.execute('DROP TRIGGER IF EXISTS temp.' + _quoted(name))
        self.triggers.clear()
        # Only the known grant projection may coexist with main trackers. Its
        # writes also invalidate historical proofs; it must never shadow main.
        temporary = _temporary_tables(self.db)
        if temporary is None:
            return False
        main = self.db.execute("SELECT name,sql FROM main.sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*'").fetchall()
        tables = [('main', *row) for row in main] + [('temp', *row) for row in temporary]
        if any('CREATE VIRTUAL TABLE' in (sql or '').upper() for _, _, sql in tables):
            return False
        for namespace, table, _ in tables:
            columns = [row[1] for row in self.db.execute(f'PRAGMA {namespace}.table_info(' + _quoted(table) + ')')]
            for verb in ('INSERT', 'UPDATE', 'DELETE'):
                name = self.name + '_' + str(len(self.triggers))
                ignored = self._ignore(table, verb, columns)
                # Own the name before SQLite executes: interruption after its
                # CREATE succeeds must still drop that trigger in finally.
                self.triggers.append(name)
                self.db.execute(f'CREATE TEMP TRIGGER {_quoted(name)} AFTER {verb} ON {namespace}.{_quoted(table)} '
                    f'WHEN NOT ({ignored}) BEGIN SELECT {self.name}(); END')
        self.temporary = temporary
        return True

    def stamp(self):
        return (self.db.execute('PRAGMA main.data_version').fetchone()[0],
            self.db.execute('PRAGMA main.schema_version').fetchone()[0],
            self.db.execute('PRAGMA temp.schema_version').fetchone()[0], self.generation)

    def before(self, bounds):
        if getattr(self, 'bounds', None) != bounds:
            self.changed()
            self.bounds = bounds
        temporary = _temporary_tables(self.db)
        if temporary is None:
            self.changed()
            return None
        schema = self.db.execute('PRAGMA main.schema_version').fetchone()[0]
        if schema != self.schema or temporary != getattr(self, 'temporary', None):
            self.changed()
            if not self._install():
                return None
            self.schema = schema
        stamp = self.stamp()
        if getattr(self, 'temp_schema', stamp[2]) != stamp[2]:
            names = {row[0] for row in self.db.execute("SELECT name FROM temp.sqlite_schema WHERE type='trigger' AND name GLOB ?",
                (self.name + '_*',))}
            if names != set(self.triggers):
                self.changed()
                return None
        self.temp_schema = stamp[2]
        if getattr(self, 'previous', None) != stamp:
            self.memo.clear()
        self.previous = stamp
        return self, stamp

    def valid(self, ticket):
        try:
            # DDL can commit while _install builds its table list. A newer
            # snapshot is not proof that its new tables already have trackers.
            return self.usable(self.db) and self.schema == ticket[1][1] and ticket[1] == self.stamp()
        except sqlite3.Error:
            self.memo.clear()
            return False

    def close(self):
        self.active = False
        self.memo.clear()
        for name in self.triggers:
            self.db.execute('DROP TRIGGER IF EXISTS temp.' + _quoted(name))
        self.triggers.clear()
        # The observer's synchronous source scopes unwind their own BEGIN on
        # entry failure too, before control reaches this outer operation exit.
        self.db.create_function(self.name, 0, None)


@contextmanager
def operation(db, operation_id):
    """Track one observer; all ordinary source scopes retain their old lifetime."""
    history = _History(db, operation_id)
    token = _CURRENT.set(history)
    try:
        try:
            db.create_function(history.name, 0, history.changed)
        except sqlite3.Error:
            history.active = False  # Fall back without borrowing an outer observer.
        yield
    finally:
        _CURRENT.reset(token)
        try:
            history.close()
        except sqlite3.Error:
            # Closing the connection already destroys all TEMP triggers. No
            # cached answer can escape even if shutdown beat this finally.
            history.active = False
            history.memo.clear()


def before(db, bounds):
    """Called only before a source scope opens its own fresh transaction."""
    history = _CURRENT.get()
    if history is None or not history.usable(db):
        return None
    try:
        return history.before(bounds)
    except sqlite3.Error:
        history.changed()
        return None  # Unsupported tracking retains the original cold checks.


def before_transaction(db):
    history = _CURRENT.get()
    if history is None or not history.usable(db) or db.in_transaction:
        return None
    from .pm_source_reads import MAX_DEPTH, MAX_NODES
    return before(db, (MAX_DEPTH, MAX_NODES))


def begin_transaction(db, ticket, *, nested):
    """A journal-owned BEGIN IMMEDIATE establishes a new, anchored read point."""
    if nested or _CURRENT.get() is None:
        return None
    if ticket is not None:
        db.execute('SELECT name FROM main.sqlite_schema LIMIT 1').fetchone()
        if not ticket[0].usable(db) or not ticket[0].valid(ticket):
            ticket = None
    return _TRANSACTION.set(ticket)


def end_transaction(token):
    if token is not None:
        _TRANSACTION.reset(token)


def transaction_snapshot(db, bounds):
    ticket = _TRANSACTION.get()
    if ticket is None or not ticket[0].usable(db):
        return None
    if ticket[0].bounds != bounds:
        ticket[0].changed()
        return None
    return ticket  # seed/save recheck the version in this transaction.


def seed(ticket, principal):
    if ticket is None or not ticket[0].valid(ticket):
        return {}
    identity = principal.actor, principal.scopes, principal.admin, principal.credential_id
    return {key: True for (caller, key) in tuple(ticket[0].memo) if caller == identity}


def save(ticket, principal, state, limit):
    if ticket is None or not ticket[0].valid(ticket):
        return
    identity = principal.actor, principal.scopes, principal.admin, principal.credential_id
    history = ticket[0]
    for key, value in state['memo'].items():
        if (value is True and key[0] in KINDS and key not in state['local_only']
                and key not in state['refused'] and key not in state['guard_contexts']
                and len(history.memo) < limit):
            history.memo[identity, key] = True


def reject(ticket):
    if ticket is not None:
        ticket[0].memo.clear()
