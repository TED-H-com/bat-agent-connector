"""Schema-aware row preservation, independent of any product table's columns."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

from tests.upgrade_continuity_helpers import assert_rows_preserved, snapshot_rows


@pytest.fixture
def database(tmp_path):
    path = tmp_path / 'continuity.sqlite3'
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE entries(entry_id INTEGER PRIMARY KEY, value TEXT)')
        db.executemany('INSERT INTO entries VALUES(?,?)', [(1, 'first'), (2, 'second')])
        db.commit()
        yield db, path


def test_snapshot_keeps_cursor_column_names_even_without_rows(database):
    db, _ = database
    saved = json.loads(json.dumps(snapshot_rows(db, 'entries')))
    assert saved == {'columns': ['entry_id', 'value'], 'rows': [[1, 'first'], [2, 'second']]}
    db.execute('DELETE FROM entries')
    empty = snapshot_rows(db, 'entries')
    assert empty == {'columns': saved['columns'], 'rows': []}
    assert_rows_preserved(db, 'entries', empty)


@pytest.mark.parametrize('declaration,expected,changed', [
    ('TEXT', None, 'unexpected'),
    ('TEXT DEFAULT NULL', None, 'unexpected'),
    ('INTEGER NOT NULL DEFAULT 0', 0, 1),
    ("TEXT DEFAULT 'it''s preserved'", "it's preserved", None),
    ('TEXT DEFAULT 0', '0', '00'),
    ('REAL DEFAULT 1.25', 1.25, 2.5),
    ("BLOB DEFAULT X'00ff'", b'\x00\xff', b'\x00\xfe'),
    ("TEXT COLLATE NOCASE DEFAULT 'original'", 'original', 'ORIGINAL'),
])
def test_added_column_defaults_are_required_on_every_preserved_row(database, declaration, expected, changed):
    db, path = database
    saved = json.loads(json.dumps(snapshot_rows(db, 'entries')))
    db.execute(f'ALTER TABLE entries ADD COLUMN observation {declaration}')
    assert db.execute('SELECT observation FROM entries').fetchall() == [(expected,), (expected,)]
    db.commit()
    # The packaged fixture verifies an opened journal without writing to it.
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as reader:
        assert_rows_preserved(reader, 'entries', saved)
    db.execute('UPDATE entries SET observation=? WHERE entry_id=2', (changed,))
    with pytest.raises(AssertionError, match=r'entries\.observation: added column differs from its declared default'):
        assert_rows_preserved(db, 'entries', saved)


@pytest.mark.parametrize('mutation', [
    "UPDATE entries SET value='changed' WHERE entry_id=2",
    'DELETE FROM entries WHERE entry_id=2',
    "INSERT INTO entries(entry_id,value) VALUES(3,'appended')",
    'ALTER TABLE entries RENAME COLUMN value TO renamed',
])
def test_old_values_columns_and_row_count_remain_strict(database, mutation):
    db, _ = database
    saved = snapshot_rows(db, 'entries')
    db.execute('ALTER TABLE entries ADD COLUMN observation TEXT')
    db.execute(mutation)
    with pytest.raises(AssertionError, match='entries:'):
        assert_rows_preserved(db, 'entries', saved)


def test_preserved_columns_are_selected_by_name_after_column_reordering(database):
    db, _ = database
    saved = snapshot_rows(db, 'entries')
    db.execute('ALTER TABLE entries RENAME TO previous')
    db.execute('CREATE TABLE entries(observation TEXT, value TEXT, entry_id INTEGER PRIMARY KEY)')
    db.execute('INSERT INTO entries(value,entry_id) SELECT value,entry_id FROM previous')
    db.execute('DROP TABLE previous')
    assert_rows_preserved(db, 'entries', saved)


@pytest.mark.parametrize('mutation', [
    "UPDATE entries SET value='changed' WHERE entry_id=2",
    'DELETE FROM entries WHERE entry_id=2',
    "UPDATE entries SET observation='changed' WHERE entry_id=2",
])
def test_append_permission_still_rejects_changes_to_preserved_rows(database, mutation):
    db, _ = database
    saved = snapshot_rows(db, 'entries')
    db.execute('ALTER TABLE entries ADD COLUMN observation TEXT')
    db.execute("INSERT INTO entries VALUES(3,'appended','new action')")
    assert_rows_preserved(db, 'entries', saved, allow_append=True)
    db.execute(mutation)
    with pytest.raises(AssertionError, match='entries[.:]'):
        assert_rows_preserved(db, 'entries', saved, allow_append=True)


def test_filtered_event_prefix_checks_only_preserved_rows(database):
    db, _ = database
    selection = {'where': 'WHERE value=?', 'args': ('second',)}
    saved = snapshot_rows(db, 'entries', **selection)
    db.execute('ALTER TABLE entries ADD COLUMN observation TEXT')
    db.execute("UPDATE entries SET observation='unrelated action' WHERE entry_id=1")
    db.execute("INSERT INTO entries VALUES(3,'second','later action')")
    assert_rows_preserved(db, 'entries', saved, allow_append=True, **selection)
    db.execute("UPDATE entries SET observation='changed' WHERE entry_id=2")
    with pytest.raises(AssertionError, match=r'entries\.observation:'):
        assert_rows_preserved(db, 'entries', saved, allow_append=True, **selection)


def test_quoted_table_and_column_names_are_preserved(database):
    db, _ = database
    db.execute('ALTER TABLE entries RENAME TO "quoted""entries"')
    db.execute('ALTER TABLE "quoted""entries" RENAME COLUMN value TO "quoted""value"')
    saved = snapshot_rows(db, 'quoted"entries')
    db.execute('ALTER TABLE "quoted""entries" ADD COLUMN "quoted""observation" TEXT')
    assert_rows_preserved(db, 'quoted"entries', saved)
