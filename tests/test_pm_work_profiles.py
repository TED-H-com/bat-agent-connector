"""Tests for PM work profiles and readiness (v1.3 E01)."""
from __future__ import annotations

import sqlite3
import time

import pytest

from bat_agent_connector import pm_work_profiles
from bat_agent_connector.task_journal import Journal


def test_schema_is_idempotent_and_allocates_no_user_version(tmp_path):
    journal = Journal(tmp_path / "tasks.sqlite3")
    before = journal.db.execute("PRAGMA user_version").fetchone()[0]
    pm_work_profiles.schema(journal)
    pm_work_profiles.schema(journal)
    assert journal.db.execute("PRAGMA user_version").fetchone()[0] == before
    names = {
        row[0]
        for row in journal.db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")
    }
    assert {"work_profiles", "work_profile_defaults", "operations_continue_succeeded"} <= names
    index_sql = journal.db.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'operations_continue_succeeded'"
    ).fetchone()[0]
    assert "WHERE action = 'repository.continue' AND status = 'succeeded'" in index_sql

    now = time.time()
    profile_id = "wpf_" + "a" * 32
    workspace_id = "ws_" + "b" * 32
    with journal.tx():
        journal.db.execute(
            """INSERT INTO work_profiles(
                profile_id, workspace_id, revision, withdrawn, document, digest, operation_id, created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (profile_id, workspace_id, 1, 0, "{}", "d" * 64, "op_" + "c" * 32, now),
        )
        journal.db.execute(
            """INSERT INTO work_profile_defaults(workspace_id, profile_id, operation_id, created_at)
               VALUES(?,?,?,?)""",
            (workspace_id, profile_id, "op_" + "e" * 32, now),
        )
    pm_work_profiles.schema(journal)
    with pytest.raises(sqlite3.IntegrityError, match="work profile history is immutable"):
        journal.db.execute("UPDATE work_profiles SET withdrawn = 1 WHERE profile_id = ?", (profile_id,))
    with pytest.raises(sqlite3.IntegrityError, match="work profile history is immutable"):
        journal.db.execute("DELETE FROM work_profiles WHERE profile_id = ?", (profile_id,))
    with pytest.raises(sqlite3.IntegrityError, match="work profile default history is immutable"):
        journal.db.execute(
            "UPDATE work_profile_defaults SET profile_id = NULL WHERE workspace_id = ?", (workspace_id,)
        )
    with pytest.raises(sqlite3.IntegrityError, match="work profile default history is immutable"):
        journal.db.execute("DELETE FROM work_profile_defaults WHERE workspace_id = ?", (workspace_id,))
