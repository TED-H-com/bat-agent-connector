"""PM work profiles (v1.3 E01): append-only revisions, defaults and readiness."""
from __future__ import annotations

ACTION = "pm.work_profile.save"

_PROFILE_DDL = (
    """CREATE TABLE IF NOT EXISTS work_profiles (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        withdrawn INTEGER NOT NULL CHECK (withdrawn IN (0, 1)),
        document TEXT NOT NULL,
        digest TEXT NOT NULL,
        operation_id TEXT NOT NULL UNIQUE,
        created_at REAL NOT NULL,
        UNIQUE (profile_id, revision)
    )""",
    """CREATE INDEX IF NOT EXISTS work_profiles_workspace
        ON work_profiles(workspace_id, profile_id, revision)""",
    """CREATE INDEX IF NOT EXISTS work_profiles_cursor
        ON work_profiles(workspace_id, sequence)""",
    """CREATE TRIGGER IF NOT EXISTS work_profiles_update_immutable
        BEFORE UPDATE ON work_profiles
        BEGIN SELECT RAISE(ABORT, 'work profile history is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS work_profiles_delete_immutable
        BEFORE DELETE ON work_profiles
        BEGIN SELECT RAISE(ABORT, 'work profile history is immutable'); END""",
    """CREATE TABLE IF NOT EXISTS work_profile_defaults (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_id TEXT NOT NULL,
        profile_id TEXT,
        operation_id TEXT NOT NULL UNIQUE,
        created_at REAL NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS work_profile_defaults_workspace
        ON work_profile_defaults(workspace_id, sequence)""",
    """CREATE TRIGGER IF NOT EXISTS work_profile_defaults_update_immutable
        BEFORE UPDATE ON work_profile_defaults
        BEGIN SELECT RAISE(ABORT, 'work profile default history is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS work_profile_defaults_delete_immutable
        BEFORE DELETE ON work_profile_defaults
        BEGIN SELECT RAISE(ABORT, 'work profile default history is immutable'); END""",
    """CREATE INDEX IF NOT EXISTS operations_continue_succeeded
        ON operations(json_extract(target, '$.host'), json_extract(target, '$.workspace_id'), created_at, operation_id)
        WHERE action = 'repository.continue' AND status = 'succeeded'""",
)


def schema(journal) -> None:
    """Idempotent additive DDL; does not allocate user_version or a data step."""
    with journal.tx():
        for statement in _PROFILE_DDL:
            journal.db.execute(statement)


def install(ops) -> None:
    schema(ops.journal)
