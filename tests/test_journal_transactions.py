"""Every journal-owned BEGIN ends even when entry or schema installation fails."""
import sqlite3
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_source_history as history
from bat_agent_connector import task_journal as journal_module
from bat_agent_connector.task_journal import Journal


def test_schema_script_failure_rolls_back_its_partial_schema(tmp_path, monkeypatch):
    path = tmp_path / 'incompatible-journal.sqlite'
    original = sqlite3.connect(path, isolation_level=None)
    original.execute('CREATE TABLE events(event_id INTEGER PRIMARY KEY)')
    original.close()
    connections = []

    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs)
        connections.append(connection)
        return connection

    # Inspect the production initializer's actual connection after failure,
    # without changing sqlite3.connect for any other module.
    monkeypatch.setattr(journal_module, 'sqlite3', SimpleNamespace(connect=connect, Row=sqlite3.Row))
    try:
        with pytest.raises(sqlite3.OperationalError, match='no such column: task_id'):
            Journal(path)
        connection, = connections
        assert not connection.in_transaction
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='tasks'").fetchone()
        other = sqlite3.connect(path, isolation_level=None, timeout=0)
        try:
            other.execute('INSERT INTO events(event_id) VALUES(1)')
            assert connection.execute('SELECT event_id FROM events').fetchone()[0] == 1
        finally:
            other.close()
    finally:
        for connection in connections:
            connection.close()


@pytest.mark.parametrize('nested', [False, True])
def test_journal_history_entry_failure_unwinds_only_its_transaction(tmp_path, monkeypatch, nested):
    journal = Journal(tmp_path / 'journal.sqlite')
    try:
        journal.db.execute('CREATE TABLE entry_probe(value INTEGER)')
        if nested:
            journal.db.execute('BEGIN')
            journal.db.execute('INSERT INTO entry_probe VALUES(1)')

        def unavailable(*args, **kwargs):
            raise OSError('Synthetic journal history entry failure')

        monkeypatch.setattr(history, 'begin_transaction', unavailable)
        with pytest.raises(OSError, match='journal history entry'):
            with journal.tx():
                pytest.fail('The entry hook must fail before the transaction body')
        assert journal.db.in_transaction is nested
        if nested:
            assert journal.db.execute('SELECT value FROM entry_probe').fetchone()[0] == 1
            with pytest.raises(sqlite3.OperationalError, match='no such savepoint'):
                journal.db.execute('RELEASE task_effect')
            journal.db.execute('COMMIT')
        journal.db.execute('INSERT INTO entry_probe VALUES(2)')
        other = sqlite3.connect(journal.path, isolation_level=None)
        try:
            assert other.execute('SELECT value FROM entry_probe ORDER BY value').fetchall() == (
                [(1,), (2,)] if nested else [(2,)])
        finally:
            other.close()
    finally:
        journal.close()
