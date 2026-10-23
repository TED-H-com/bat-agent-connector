"""SQLite authority for task, command and event state.

The service process is the only writer. Each short operation is a transaction;
network calls happen after committing an intent, never inside a transaction.
The service never delivers chat messages itself: callers read the milestone
feed (``Journal.milestones``) and publish wherever they own a channel.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from . import platform_files, pm_source_history

# Latest allocated data step; individual backfills retain their own version gates.
LATEST_DATA_STEP = 3


class JournalNewerDataStep(ValueError):
    """This Connector cannot safely write a journal advanced by a newer build."""

    code = "JOURNAL_NEWER_DATA_STEP"

    def __init__(self, recorded: int):
        self.recorded = recorded
        super().__init__(f"{self.code}: journal records data step {recorded}; "
                         f"this Connector supports up to {LATEST_DATA_STEP}; "
                         "reopen with the newer Connector")


STATES = frozenset({
    "queued", "dispatching", "accepted", "running", "waiting_permission",
    "quota_limited", "human_owned", "needs_ted", "verifying", "done", "failed", "uncertain",
})
TERMINAL = frozenset({"done", "failed"})
ALLOWED = {
    "queued": {"dispatching", "uncertain", "failed", "needs_ted"},
    "dispatching": {"queued", "accepted", "running", "verifying", "uncertain", "failed", "needs_ted"},
    "accepted": {"running", "dispatching", "verifying", "uncertain", "needs_ted", "failed"},
    "running": {"accepted", "verifying", "waiting_permission", "quota_limited", "human_owned",
                "needs_ted", "uncertain", "failed"},
    "waiting_permission": {"running", "needs_ted", "human_owned", "uncertain", "failed"},
    "quota_limited": {"running", "needs_ted", "uncertain", "failed"},
    "human_owned": {"running", "needs_ted", "failed"},
    "needs_ted": {"running", "accepted", "failed", "uncertain"},
    "verifying": {"dispatching", "accepted", "running", "done", "needs_ted", "uncertain", "failed"},
    "uncertain": {"running", "accepted", "waiting_permission", "verifying", "human_owned", "done", "needs_ted", "failed"},
    "done": set(), "failed": set(),
}



_CONTEXT_REF_KEYS = {"attachments", "previous_message_id", "plan", "commit"}


def _context_refs(refs: dict | None) -> dict | None:
    """References that came with the user's words: stored as data, never interpreted."""
    if refs is None:
        return None
    if not isinstance(refs, dict) or set(refs) - _CONTEXT_REF_KEYS:
        raise ValueError("context_refs keys: attachments, previous_message_id, plan, commit")
    out: dict = {}
    attachments = refs.get("attachments")
    if attachments is not None:
        if (not isinstance(attachments, list) or len(attachments) > 20
                or not all(isinstance(a, str) and a.strip() and len(a) <= 500 for a in attachments)):
            raise ValueError("attachments must be up to 20 reference strings")
        if attachments:
            out["attachments"] = [a.strip() for a in attachments]
    for key in ("previous_message_id", "plan"):
        value = refs.get(key)
        if value is not None:
            if not isinstance(value, str) or not value.strip() or len(value) > 500:
                raise ValueError(f"{key} must be a short reference string")
            out[key] = value.strip()
    commit = refs.get("commit")
    if commit is not None:
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
            raise ValueError("commit must be a 7-40 character hex id")
        out["commit"] = commit.lower()
    return out or None

class Journal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if platform_files.WINDOWS:
            platform_files.ensure_private_directory(self.path.parent)
            # Pin the chain while SQLite opens its WAL/SHM siblings.
            self._storage_directory = platform_files.native.open_dir(self.path.parent)
            try:
                fd = platform_files.open_private_file(self.path, os.O_RDWR | os.O_CREAT)
                os.close(fd)
            except BaseException:
                self._storage_directory.close()
                raise
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.path.parent.chmod(0o700)
            if not self.path.exists():
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            self.path.chmod(0o600)
        try:
            # Even closing a writable reader can checkpoint an unfamiliar WAL into
            # the main file. Refuse through a read-only connection before any write.
            with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)) as reader:
                recorded = reader.execute("PRAGMA user_version").fetchone()[0]
                if recorded > LATEST_DATA_STEP:
                    raise JournalNewerDataStep(recorded)
            self.db = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        except BaseException:
            if getattr(self, "_storage_directory", None):
                self._storage_directory.close()
            raise
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        # Intent must survive a host crash before the network dispatch begins.
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        try:
            self.db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS confinement_host_checks (
                host TEXT PRIMARY KEY, evidence TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, idem_key TEXT UNIQUE NOT NULL, payload_hash TEXT NOT NULL,
                project TEXT NOT NULL, host TEXT NOT NULL, workspace TEXT NOT NULL,
                original_words TEXT NOT NULL, interpretation TEXT, discord_thread_id TEXT,
                recipe TEXT NOT NULL, acceptance TEXT NOT NULL, engine TEXT NOT NULL,
                task_path TEXT NOT NULL DEFAULT 'standard',
                state TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0, control_version INTEGER NOT NULL DEFAULT 0,
                session_id TEXT, reviewer_session_id TEXT, turn_marker TEXT,
                submitted_at REAL NOT NULL, updated_at REAL NOT NULL, delivered_at REAL,
                delivered INTEGER NOT NULL DEFAULT 0, review_rejections INTEGER NOT NULL DEFAULT 0,
                ted_interventions INTEGER NOT NULL DEFAULT 0, continuations INTEGER NOT NULL DEFAULT 0,
                session_replacements INTEGER NOT NULL DEFAULT 0,
                review_passed INTEGER NOT NULL DEFAULT 0, verification_commit TEXT,
                verification_tree TEXT, review_commit TEXT, review_tree TEXT, review_marker TEXT,
                lead_agent TEXT NOT NULL DEFAULT 'codex', pm_provider TEXT, result TEXT,
                base_branch TEXT, base_commit TEXT, external_worktree_path TEXT, external_branch TEXT
            );
            CREATE TABLE IF NOT EXISTS commands (
                command_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
                idem_key TEXT NOT NULL UNIQUE, session_id TEXT, kind TEXT NOT NULL,
                status TEXT NOT NULL, message_id TEXT, marker TEXT, payload TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(task_id),
                kind TEXT NOT NULL, body TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_task ON events(task_id, kind, event_id);
            CREATE TABLE IF NOT EXISTS routing (
                route_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(task_id),
                step TEXT NOT NULL, step_type TEXT NOT NULL, provider TEXT NOT NULL,
                confidence REAL, stakes TEXT NOT NULL, reason TEXT NOT NULL,
                jev_backend TEXT, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS minimal_review_gates (
                task_id TEXT NOT NULL REFERENCES tasks(task_id), candidate_commit TEXT NOT NULL,
                tree_hash TEXT NOT NULL, diff_sha256 TEXT NOT NULL,
                verdict TEXT NOT NULL, confidence REAL, jev_backend TEXT,
                reason TEXT, threshold REAL NOT NULL, created_at REAL NOT NULL,
                PRIMARY KEY(task_id,candidate_commit,tree_hash)
            );
            CREATE TABLE IF NOT EXISTS provider_usage (
                usage_id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL,
                outcome TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capabilities (
                token_hash TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(task_id),
                scope TEXT NOT NULL, command_id TEXT, expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS command_reconciliations (
                command_id TEXT PRIMARY KEY REFERENCES commands(command_id),
                task_id TEXT NOT NULL REFERENCES tasks(task_id), outcome TEXT NOT NULL,
                actor TEXT NOT NULL, source TEXT NOT NULL, evidence TEXT NOT NULL,
                observed_result TEXT NOT NULL, turn_ref TEXT, candidate_commit TEXT,
                tree_hash TEXT, next_prompt_sha256 TEXT, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observed_verifications (
                verification_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(task_id), candidate_commit TEXT NOT NULL,
                tree_hash TEXT NOT NULL, command TEXT NOT NULL, exit_code INTEGER NOT NULL,
                log_ref TEXT NOT NULL, output_sha256 TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daemon_owner (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), owner_id TEXT NOT NULL,
                pid INTEGER NOT NULL, heartbeat_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ted_actions (
                task_id TEXT NOT NULL REFERENCES tasks(task_id), source_message_id TEXT NOT NULL,
                action TEXT NOT NULL, created_at REAL NOT NULL,
                PRIMARY KEY(task_id,source_message_id)
            );
            CREATE TABLE IF NOT EXISTS feed_push (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), cursor INTEGER NOT NULL,
                failures INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0,
                last_error TEXT, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS branches (
                branch_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
                session_id TEXT, provider TEXT NOT NULL, role TEXT NOT NULL,
                parent_branch_id TEXT, reason TEXT NOT NULL, created_at REAL NOT NULL,
                UNIQUE(task_id,session_id,role)
            );
            -- /api/v1: one monotonic cursor for task, operation and inventory events.
            CREATE TABLE IF NOT EXISTS api_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, actor TEXT,
                task_event_id INTEGER UNIQUE, created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS api_events_resource ON api_events(resource_type, resource_id, seq);
            CREATE TABLE IF NOT EXISTS api_principals (
                token_hash TEXT PRIMARY KEY, actor TEXT NOT NULL, scopes TEXT NOT NULL, label TEXT,
                created_at REAL NOT NULL, expires_at REAL, revoked_at REAL
            );
            CREATE TABLE IF NOT EXISTS browser_sessions (
                token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, parent_hash TEXT NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY, actor TEXT NOT NULL, entry TEXT NOT NULL,
                idem_key TEXT NOT NULL, request_hash TEXT NOT NULL, action TEXT NOT NULL,
                target TEXT NOT NULL, params TEXT NOT NULL, preconditions TEXT NOT NULL,
                status TEXT NOT NULL, status_reason TEXT, error_code TEXT, result TEXT,
                external_refs TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                uncertain_tries INTEGER NOT NULL DEFAULT 0, next_run_at REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(actor, idem_key)
            );
            CREATE INDEX IF NOT EXISTS operations_due ON operations(status, next_run_at);
            CREATE TABLE IF NOT EXISTS operation_steps (
                operation_id TEXT NOT NULL REFERENCES operations(operation_id), seq INTEGER NOT NULL,
                name TEXT NOT NULL, status TEXT NOT NULL, request TEXT NOT NULL, response TEXT,
                external_ref TEXT, error TEXT, started_at REAL NOT NULL, finished_at REAL,
                PRIMARY KEY(operation_id, seq), UNIQUE(operation_id, name)
            );
            CREATE TABLE IF NOT EXISTS connection_bindings (
                principal TEXT NOT NULL, installation_ref TEXT NOT NULL, connection_id TEXT NOT NULL,
                revision INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('active','detached')),
                reference TEXT NOT NULL, source_digest TEXT NOT NULL, kind TEXT NOT NULL,
                policy_version INTEGER NOT NULL, profile_reference TEXT, association TEXT NOT NULL,
                remote_profile_id TEXT NOT NULL, service_ref TEXT, workspace_ref TEXT,
                label TEXT NOT NULL, preferences TEXT NOT NULL,
                operation_id TEXT NOT NULL REFERENCES operations(operation_id), receipt_refs TEXT NOT NULL,
                PRIMARY KEY(principal, installation_ref, connection_id)
            );
            CREATE TABLE IF NOT EXISTS hosts_observed (
                host TEXT PRIMARY KEY, reachable INTEGER NOT NULL DEFAULT 0, error TEXT,
                server_version TEXT, last_attempt_at REAL, last_success_at REAL,
                consecutive_failures INTEGER NOT NULL DEFAULT 0, session_count INTEGER
            );
            CREATE TABLE IF NOT EXISTS sessions_observed (
                host TEXT NOT NULL, session_id TEXT NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL,
                provenance TEXT NOT NULL, api_access TEXT NOT NULL, attention INTEGER NOT NULL DEFAULT 0,
                sort_key INTEGER NOT NULL DEFAULT 0, first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL,
                missing_count INTEGER NOT NULL DEFAULT 0, gone_at REAL, PRIMARY KEY(host, session_id)
            );
            CREATE INDEX IF NOT EXISTS sessions_observed_activity ON sessions_observed(sort_key, host, session_id);
            CREATE TABLE IF NOT EXISTS checkpoints (
                checkpoint_id TEXT PRIMARY KEY, host TEXT NOT NULL, source_session_id TEXT NOT NULL,
                source_provenance TEXT NOT NULL, workspace_id TEXT, workspace_name TEXT, cwd TEXT NOT NULL,
                repo_root TEXT NOT NULL, branch TEXT, commit_sha TEXT NOT NULL, head_sha TEXT NOT NULL,
                dirty INTEGER NOT NULL DEFAULT 0, excerpt TEXT NOT NULL, excerpt_sha256 TEXT NOT NULL, note TEXT,
                actor TEXT NOT NULL, operation_id TEXT NOT NULL, captured_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS checkpoints_source ON checkpoints(host, source_session_id, captured_at);
            CREATE TABLE IF NOT EXISTS checkpoint_runs (
                checkpoint_id TEXT NOT NULL REFERENCES checkpoints(checkpoint_id),
                operation_id TEXT PRIMARY KEY, host TEXT NOT NULL, session_id TEXT NOT NULL, clone_path TEXT NOT NULL,
                worktree_path TEXT NOT NULL, branch TEXT NOT NULL, agent TEXT NOT NULL, actor TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            -- integration.preview results; immutable once written. `sources` keeps host paths (journal only).
            CREATE TABLE IF NOT EXISTS integration_previews (
                preview_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE, actor TEXT NOT NULL,
                host TEXT NOT NULL, repository TEXT NOT NULL, repository_id INTEGER NOT NULL,
                pull_number INTEGER NOT NULL, head_ref TEXT NOT NULL, head_sha TEXT NOT NULL, base_ref TEXT NOT NULL,
                base_sha TEXT NOT NULL, remote_url TEXT NOT NULL, area_path TEXT NOT NULL, sources TEXT NOT NULL,
                document TEXT NOT NULL, predicted_tree TEXT, digest TEXT NOT NULL, ready INTEGER NOT NULL,
                blocking TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS integration_previews_pr ON integration_previews(repository, pull_number,
                created_at);
            -- One row per source per integration.apply: its intent, progress and the "received" fact.
            CREATE TABLE IF NOT EXISTS integration_receipts (
                operation_id TEXT NOT NULL, seq INTEGER NOT NULL, preview_id TEXT NOT NULL,
                repository TEXT NOT NULL, pull_number INTEGER NOT NULL, head_ref TEXT NOT NULL,
                source_kind TEXT NOT NULL, source_id TEXT NOT NULL, source_host TEXT NOT NULL,
                location_class TEXT NOT NULL, pinned_sha TEXT NOT NULL, mode TEXT NOT NULL, commits TEXT,
                source_key TEXT NOT NULL, status TEXT NOT NULL, method TEXT, base_sha TEXT, integrated_sha TEXT,
                picked TEXT, conflict_files TEXT, resolution_sha TEXT, remerge_stat TEXT,
                resolver_operation_id TEXT, resolver_session_id TEXT, repair_worktree TEXT, repair_branch TEXT,
                delivered_sha TEXT, delivered_at REAL,
                actor TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(operation_id, seq)
            );
            CREATE INDEX IF NOT EXISTS integration_receipts_source ON integration_receipts(repository, head_ref,
                source_key, status);
            -- Projects and work items (work_items.py). Archived rows stay, so an ID is never reused.
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                parent_id TEXT, derived_from TEXT, repositories TEXT NOT NULL DEFAULT '[]', task_project TEXT,
                pinned INTEGER NOT NULL DEFAULT 0, archived_at REAL, archived_by TEXT,
                version INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS work_items (
                work_item_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(project_id),
                parent_id TEXT, derived_from TEXT, title TEXT NOT NULL, goal TEXT NOT NULL DEFAULT '',
                request TEXT NOT NULL DEFAULT '', acceptance TEXT NOT NULL DEFAULT '',
                steps TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT 'todo', done_by TEXT, done_at REAL,
                approved_fingerprint TEXT, approved_by TEXT, approved_at REAL, continued_steps TEXT,
                pinned INTEGER NOT NULL DEFAULT 0, archived_at REAL, archived_by TEXT, archive_operation TEXT,
                version INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS work_items_tree ON work_items(project_id, parent_id);
            CREATE INDEX IF NOT EXISTS work_items_changed ON work_items(updated_at);
            -- Saved sibling order per parent ("projects" or "items:<project_id>"); "" is the top level.
            CREATE TABLE IF NOT EXISTS tree_order (
                scope TEXT NOT NULL, parent TEXT NOT NULL, ids TEXT NOT NULL, PRIMARY KEY(scope, parent)
            );
            CREATE TABLE IF NOT EXISTS work_item_links (
                link_id INTEGER PRIMARY KEY AUTOINCREMENT, work_item_id TEXT NOT NULL, kind TEXT NOT NULL,
                ref TEXT NOT NULL, note TEXT, linked_by TEXT NOT NULL, linked_at REAL NOT NULL,
                link_operation TEXT NOT NULL, removed_by TEXT, removed_at REAL, remove_operation TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS work_item_links_active ON work_item_links(work_item_id, kind, ref)
                WHERE removed_at IS NULL;
            CREATE INDEX IF NOT EXISTS work_item_links_ref ON work_item_links(kind, ref);
            -- One row per applied management operation, written in the same transaction as the change.
            CREATE TABLE IF NOT EXISTS management_applied (
                operation_id TEXT PRIMARY KEY, result TEXT NOT NULL, applied_at REAL NOT NULL
            );
            COMMIT;
            """)
        except BaseException:
            # executescript leaves its explicit BEGIN open if schema SQL fails.
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(tasks)")}
        for name, sql_type in (("verification_tree", "TEXT"), ("review_commit", "TEXT"),
                               ("review_tree", "TEXT"), ("review_marker", "TEXT"),
                               ("lead_agent", "TEXT NOT NULL DEFAULT 'codex'"),
                               ("pm_provider", "TEXT"), ("base_branch", "TEXT"), ("base_commit", "TEXT"),
                               ("external_worktree_path", "TEXT"), ("external_branch", "TEXT"),
                               ("session_replacements", "INTEGER NOT NULL DEFAULT 0"),
                               ("verification_failures", "INTEGER NOT NULL DEFAULT 0"),
                               ("verifying_started_at", "REAL"), ("progress_at", "REAL"),
                               ("parent_task_id", "TEXT"), ("continuation", "INTEGER NOT NULL DEFAULT 0"),
                               ("context_refs", "TEXT"), ("controller_authority", "TEXT"),
                               ("task_path", "TEXT NOT NULL DEFAULT 'standard'")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {sql_type}")  # noqa: S608 - fixed local identifiers
        receipt_columns = {r[1] for r in self.db.execute("PRAGMA table_info(integration_receipts)")}
        for name in ("remerge_stat", "resolver_operation_id", "resolver_session_id", "repair_worktree",
                     "repair_branch"):
            if name not in receipt_columns:
                self.db.execute(f"ALTER TABLE integration_receipts ADD COLUMN {name} TEXT")  # noqa: S608 - fixed names
        if "uncertain_tries" not in {r[1] for r in self.db.execute("PRAGMA table_info(operations)")}:
            self.db.execute("ALTER TABLE operations ADD COLUMN uncertain_tries INTEGER NOT NULL DEFAULT 0")
        cap_columns = {r[1] for r in self.db.execute("PRAGMA table_info(capabilities)")}
        if "controller_authority" not in {r[1] for r in self.db.execute("PRAGMA table_info(commands)")}:
            self.db.execute("ALTER TABLE commands ADD COLUMN controller_authority TEXT")
        if "command_id" not in cap_columns:
            self.db.execute("ALTER TABLE capabilities ADD COLUMN command_id TEXT")
        route_columns = {r[1] for r in self.db.execute("PRAGMA table_info(routing)")}
        if "jev_backend" not in route_columns:
            self.db.execute("ALTER TABLE routing ADD COLUMN jev_backend TEXT")
        self._drop_legacy_outbox()
        if self.db.execute("PRAGMA user_version").fetchone()[0] < 1:
            # One-time projection of older task events into the /api/v1 cursor.
            with self.tx():
                self.db.execute("""INSERT OR IGNORE INTO api_events(resource_type,resource_id,kind,body,
                    task_event_id,created_at) SELECT 'task',task_id,'task.'||kind,body,event_id,created_at
                    FROM events ORDER BY event_id""")
                self.db.execute("PRAGMA user_version=1")
        # Delivery A. Idempotent DDL runs on every open and takes no data-migration version.
        with self.tx():
            self.db.execute("""CREATE TABLE IF NOT EXISTS pr_merge_previews (
                preview_id TEXT PRIMARY KEY, repository TEXT NOT NULL, pull_number INTEGER NOT NULL,
                document TEXT NOT NULL, digest TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL
            )""")
            self.db.execute("""CREATE INDEX IF NOT EXISTS pr_merge_previews_pr
                ON pr_merge_previews(repository, pull_number, created_at)""")
        # Delivery A review fixes. These tables follow the same idempotent DDL rule.
        with self.tx():
            self.db.execute("""CREATE TABLE IF NOT EXISTS pr_metadata_settlements (
                operation_id TEXT PRIMARY KEY, document TEXT NOT NULL
            )""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS pr_merge_scope_reads (
                repository TEXT NOT NULL, pull_number INTEGER NOT NULL, method TEXT NOT NULL,
                preview_id TEXT NOT NULL, checked_at REAL NOT NULL,
                PRIMARY KEY(repository, pull_number, method)
            )""")
        from . import deployment_store
        deployment_store.schema(self)

        from .observation import install
        install(self)
        deployment_store.backfill(self)
        from .dashboard_sync import install as install_sync
        install_sync(self)

        from .cleanup import migrate
        migrate(self)
        self._migrate_artifacts()
        from .session_metadata import schema as session_metadata_schema
        session_metadata_schema(self)
        from .work_item_reads import schema as work_item_reads_schema
        work_item_reads_schema(self)
        from .session_reading import schema as session_reading_schema
        session_reading_schema(self)

    def _migrate_artifacts(self):
        # Additive DDL runs after numbered data migrations without claiming their versions.
        with self.tx():
            for table in ("checkpoints", "work_items"):
                if "attachments" not in {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
            statements = (
                """CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY, latest_revision INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, actor TEXT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS artifact_revisions (
                    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id), revision INTEGER NOT NULL,
                    digest TEXT NOT NULL, size_bytes INTEGER NOT NULL, media_type TEXT NOT NULL,
                    display_name TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
                    created_at REAL NOT NULL, PRIMARY KEY(artifact_id,revision))""",
                """CREATE TABLE IF NOT EXISTS artifact_uploads (
                    operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
                    artifact_id TEXT NOT NULL, revision INTEGER NOT NULL, deadline REAL NOT NULL,
                    reserved_bytes INTEGER NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                    receive_state TEXT NOT NULL DEFAULT 'waiting', received_size INTEGER NOT NULL DEFAULT 0,
                    received_digest TEXT, released_at REAL)""",
                """CREATE TABLE IF NOT EXISTS artifact_acceptances (
                    operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
                    artifact_id TEXT NOT NULL, revision INTEGER NOT NULL, document TEXT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS artifact_capture_sources (
                    operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
                    document TEXT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS artifact_references (
                    owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, digest TEXT NOT NULL, role TEXT NOT NULL,
                    operation_id TEXT NOT NULL, created_at REAL NOT NULL, released_at REAL,
                    release_operation_id TEXT,
                    PRIMARY KEY(owner_kind,owner_id,artifact_id,revision,role,operation_id))""",
                """CREATE TABLE IF NOT EXISTS artifact_materializations (
                    materialization_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                    artifact_id TEXT NOT NULL, revision INTEGER NOT NULL, digest TEXT NOT NULL,
                    host TEXT NOT NULL, managed_path TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    attempt INTEGER NOT NULL DEFAULT 0, evidence TEXT, updated_at REAL NOT NULL,
                    UNIQUE(operation_id,artifact_id,revision))""",
                """CREATE TABLE IF NOT EXISTS checkpoint_source_confirmations (
                    operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
                    parent_operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                    source_head_sha TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL)""",
            )
            for statement in statements:
                self.db.execute(statement)

    def _drop_legacy_outbox(self):
        """Remove the retired chat outbox so no historical event can ever be published.

        Older journals carried per-event delivery columns and a board table.
        Delivery now belongs to whichever caller reads ``milestones``.
        """
        event_columns = {r[1] for r in self.db.execute("PRAGMA table_info(events)")}
        legacy = [c for c in ("discord_status", "discord_message_id") if c in event_columns]
        has_board = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='board'").fetchone()
        if not legacy and not has_board:
            return
        with self.tx():
            for column in legacy:
                self.db.execute(f"ALTER TABLE events DROP COLUMN {column}")  # noqa: S608 - fixed local identifiers
            self.db.execute("DROP TABLE IF EXISTS board")

    @contextmanager
    def tx(self):
        nested = self.db.in_transaction
        source_version = None if nested else pm_source_history.before_transaction(self.db)
        source_token = None
        self.db.execute("SAVEPOINT task_effect" if nested else "BEGIN IMMEDIATE")
        try:
            source_token = pm_source_history.begin_transaction(self.db, source_version, nested=nested)
            yield
            self.db.execute("RELEASE task_effect" if nested else "COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK TO task_effect" if nested else "ROLLBACK")
            if nested:
                self.db.execute("RELEASE task_effect")
            raise
        finally:
            pm_source_history.end_transaction(source_token)

    def close(self):
        self.db.close()
        if getattr(self, "_storage_directory", None):
            self._storage_directory.close()
        if getattr(self, "on_close", None):
            self.on_close()

    def issue_capability(self, task_id: str, *, ttl_s: int = 3600) -> str:
        self.get(task_id)
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.db.execute("INSERT INTO capabilities(token_hash,task_id,scope,expires_at) VALUES(?,?,?,?)",
                        (digest, task_id, "task", time.time() + ttl_s))
        return token

    def authorize_capability(self, token: str, task_id: str) -> bool:
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self.db.execute("SELECT task_id,expires_at FROM capabilities WHERE token_hash=? AND scope='task'",
                              (digest,)).fetchone()
        return bool(row and row["task_id"] == task_id and row["expires_at"] > time.time())

    def revoke_task_capabilities(self, task_id: str) -> None:
        with self.tx():
            self.db.execute("DELETE FROM capabilities WHERE task_id=? AND scope='task'", (task_id,))
            self._event(task_id, "task_capabilities_revoked", {"reason": "warm_session_transfer"})

    def issue_reconcile_capability(self, task_id: str, command_id: str, *, ttl_s: int = 600,
                                   token: str | None = None) -> str:
        task = self.get(task_id)
        command = self.command_get(command_id)
        send = (command["kind"] in {"send", "answer", "interrupt", "permissions"} and
                command["session_id"] in {task["session_id"], task["reviewer_session_id"]})
        failover = (command["kind"] == "failover" and
                    json.loads(command["payload"]).get("old_session_id") == task["session_id"])
        if (command["task_id"] != task_id or not (send or failover)
                or command["status"] != "uncertain" or task["state"] != "uncertain"):
            raise ValueError("only an uncertain task command can be reconciled")
        token = token or secrets.token_urlsafe(32)
        self.db.execute("""INSERT INTO capabilities(token_hash,task_id,scope,command_id,expires_at)
            VALUES(?,?,?,?,?) ON CONFLICT(token_hash) DO UPDATE SET expires_at=excluded.expires_at""", (hashlib.sha256(token.encode()).hexdigest(), task_id,
                                  "reconcile", command_id, time.time() + ttl_s))
        return token

    def authorize_reconcile_capability(self, token: str, task_id: str, command_id: str) -> bool:
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self.db.execute("""SELECT expires_at FROM capabilities WHERE token_hash=?
            AND task_id=? AND command_id=? AND scope='reconcile'""",
            (digest, task_id, command_id)).fetchone()
        return bool(row and row["expires_at"] > time.time())

    def command_get(self, command_id: str) -> dict:
        row = self.db.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()
        if row is None:
            raise KeyError(command_id)
        return dict(row)

    def send_for_message(self, task_id: str, session_id: str, message_id: str) -> dict | None:
        row = self.db.execute("""SELECT * FROM commands WHERE task_id=? AND session_id=?
            AND message_id=? AND kind='send'""", (task_id, session_id, message_id)).fetchone()
        return dict(row) if row else None

    def resolve_send(self, task_id: str, command_id: str, *, token: str, outcome: str,
                     actor: str, source: str, evidence: str, observed_result: str = "none",
                     turn_ref: str | None = None, candidate_commit: str | None = None,
                     tree_hash: str | None = None, next_prompt_sha256: str | None = None,
                     next_before: dict | None = None, token_hash: str | None = None,
                     operation_id: str | None = None) -> dict:
        if outcome not in {"delivered", "not_delivered", "superseded"}:
            raise ValueError("invalid reconciliation outcome")
        if observed_result not in {"none", "milestone", "review_pass"}:
            raise ValueError("invalid observed result")
        if (actor not in {"operator", "ted"} or not isinstance(source, str) or not source.strip()
                or len(source) > 256 or not isinstance(evidence, str) or not evidence.strip()
                or len(evidence) > 2000 or (turn_ref is not None and len(turn_ref) > 256)):
            raise ValueError("operator provenance and evidence are required")
        if observed_result != "none" and (outcome != "delivered" or not turn_ref):
            raise ValueError("observed result requires delivered prompt and exact turn reference")
        if next_prompt_sha256 and observed_result != "none":
            raise ValueError("new prompt cannot also attest an observed result")
        if next_prompt_sha256 and not isinstance(next_before, dict):
            raise ValueError("new prompt needs a recorded pre-send baseline")
        digest = token_hash or hashlib.sha256(token.encode()).hexdigest()
        with self.tx():
            task = self.get(task_id)
            command = self.command_get(command_id)
            if (task["state"] != "uncertain" or command["task_id"] != task_id
                    or command["kind"] not in {"send", "answer", "interrupt", "permissions"} or command["status"] != "uncertain"):
                raise ValueError("command is not an uncertain runtime control for this task")
            cap = self.db.execute("""SELECT expires_at FROM capabilities WHERE token_hash=?
                AND task_id=? AND command_id=? AND scope='reconcile'""",
                (digest, task_id, command_id)).fetchone()
            if not cap or cap["expires_at"] <= time.time():
                raise ValueError("reconciliation capability is invalid")
            payload = json.loads(command["payload"])
            if command["kind"] != "send" and observed_result != "none":
                raise ValueError("only a send command can attest an observed turn")
            if next_prompt_sha256 and next_prompt_sha256 == payload.get("prompt_sha256"):
                raise ValueError("next prompt must be a new command, not replay of uncertain text")
            reviewer = command["session_id"] == task["reviewer_session_id"]
            if observed_result == "milestone" and (reviewer or command["session_id"] != task["session_id"]):
                raise ValueError("milestone must belong to lead command")
            if observed_result == "review_pass":
                observed = self.observed_verification(task_id)
                if (not reviewer or payload.get("purpose") != "reviewer:initial"
                        or not candidate_commit or not tree_hash
                        or (task["review_commit"], task["review_tree"]) != (candidate_commit, tree_hash)
                        or not observed or observed["exit_code"] != 0
                        or (observed["candidate_commit"], observed["tree_hash"]) != (candidate_commit, tree_hash)):
                    raise ValueError("review PASS is not bound to verified candidate")
            if next_prompt_sha256 and (task["paused"] or reviewer or
                                       command["session_id"] != task["session_id"]):
                raise ValueError("next prompt requires the lead session")
            now = time.time()
            target = ("done" if observed_result == "review_pass" else
                      "verifying" if observed_result == "milestone" else
                      "accepted" if next_prompt_sha256 else "human_owned")
            self.db.execute("""INSERT INTO command_reconciliations(command_id,task_id,outcome,actor,source,
                evidence,observed_result,turn_ref,candidate_commit,tree_hash,next_prompt_sha256,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (command_id, task_id, outcome, actor, source, evidence, observed_result,
                 turn_ref, candidate_commit, tree_hash, next_prompt_sha256, now))
            self.db.execute("DELETE FROM capabilities WHERE token_hash=?", (digest,))
            self.db.execute("UPDATE commands SET status=?,updated_at=? WHERE command_id=?",
                            ("resolved_" + outcome, now, command_id))
            next_command_id = None
            if next_prompt_sha256:
                next_command_id = str(uuid.uuid4())
                self.db.execute("""INSERT INTO commands(command_id,task_id,idem_key,session_id,kind,status,
                    message_id,payload,created_at,updated_at,controller_authority) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (next_command_id, task_id, f"{task_id}:operator:{command_id}", command["session_id"],
                     "send", "needs_review", f"batc-{next_command_id}",
                     json.dumps({"purpose": "operator:" + command_id, "before": next_before,
                                 "control_version": task["control_version"], "operation_id": operation_id,
                                 "controller_incarnation": payload.get("controller_incarnation"),
                                 "prompt_sha256": next_prompt_sha256}), now, now,
                     json.dumps(task["controller_authority"], sort_keys=True) if task.get("controller_authority") else None))
                self._event(task_id, "command_intent", {"command_id": next_command_id, "kind": "send",
                                                         "needs_review": True, "operator_followup": command_id})
            if actor == "ted":
                action = self.db.execute("""INSERT OR IGNORE INTO ted_actions(task_id,source_message_id,action,created_at)
                    VALUES(?,?,?,?)""", (task_id, source, "reconcile", now))
                if action.rowcount:
                    self.db.execute("UPDATE tasks SET ted_interventions=ted_interventions+1 WHERE task_id=?",
                                    (task_id,))
            if target == "done":
                self.db.execute("""UPDATE tasks SET state='done',review_passed=1,delivered=1,
                    delivered_at=?,updated_at=?,result=? WHERE task_id=?""",
                    (now, now, evidence[:1000], task_id))
            elif target == "verifying":
                self.db.execute("""UPDATE tasks SET state=?,updated_at=?,verifying_started_at=?,
                    progress_at=? WHERE task_id=?""", (target, now, now, now, task_id))
            else:
                self.db.execute("UPDATE tasks SET state=?,updated_at=? WHERE task_id=?", (target, now, task_id))
            self._event(task_id, "command_reconciled", {"command_id": command_id, "outcome": outcome,
                                                       "actor": actor, "source": source,
                                                       "observed_result": observed_result, "state": target})
        result = self.get(task_id)
        if next_command_id:
            result["_next_command_id"] = next_command_id
        return result

    def record_observed_verification(self, task_id: str, evidence: dict) -> dict:
        required = {"candidate_commit", "tree_hash", "command", "exit_code", "log_ref", "output_sha256"}
        if required - evidence.keys() or evidence.get("source") != "observed_runner":
            raise ValueError("only observed runner evidence is accepted")
        with self.tx():
            cur = self.db.execute("""INSERT INTO observed_verifications
                (task_id,candidate_commit,tree_hash,command,exit_code,log_ref,output_sha256,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", (task_id, evidence["candidate_commit"], evidence["tree_hash"],
                evidence["command"], evidence["exit_code"], evidence["log_ref"], evidence["output_sha256"],
                time.time()))
            self._event(task_id, "verification_observed", {"verification_id": cur.lastrowid,
                                                           "exit_code": evidence["exit_code"]})
        return dict(self.db.execute("SELECT * FROM observed_verifications WHERE verification_id=?",
                                    (cur.lastrowid,)).fetchone())

    def observed_verification(self, task_id: str) -> dict | None:
        row = self.db.execute("""SELECT * FROM observed_verifications WHERE task_id=?
            ORDER BY verification_id DESC LIMIT 1""", (task_id,)).fetchone()
        return dict(row) if row else None

    def _event(self, task_id: str, kind: str, body: dict | None = None):
        raw, now = json.dumps(body or {}, ensure_ascii=False), time.time()
        cur = self.db.execute("INSERT INTO events(task_id,kind,body,created_at) VALUES(?,?,?,?)",
                              (task_id, kind, raw, now))
        from .observation import writer_context
        context = writer_context.get() or {}
        api_cur = self.db.execute("""INSERT INTO api_events(resource_type,resource_id,kind,body,task_event_id,created_at,actor)
            VALUES('task',?,?,?,?,?,?)""", (task_id, "task." + kind, raw, cur.lastrowid, now, context.get("actor")))
        if getattr(self, "_observation_ready", False):
            self._project_event(api_cur.lastrowid, context)

    def api_event(self, resource_type: str, resource_id: str, kind: str, body: dict | None = None,
                  actor: str | None = None, *, context: dict | None = None) -> int:
        """Append one /api/v1 event; call it inside the transaction that made the change."""
        if resource_type == "execution":
            from .observation import writer_context
            source = writer_context.get() or {}
            actor = actor or source.get("actor")
            context = {**source, **(context or {})}
        cur = self.db.execute("""INSERT INTO api_events(resource_type,resource_id,kind,body,actor,created_at)
            VALUES(?,?,?,?,?,?)""", (resource_type, resource_id, kind,
                                     json.dumps(body or {}, ensure_ascii=False, default=str), actor, time.time()))
        if getattr(self, "_observation_ready", False):
            self._project_event(cur.lastrowid, context)
        return int(cur.lastrowid)

    def _project_event(self, seq: int, context: dict | None, *, legacy: bool = False) -> None:
        # A projection bug must not roll back the authoritative writer's transaction.
        savepoint = f"observation_{int(seq)}"
        self.db.execute(f"SAVEPOINT {savepoint}")
        try:
            from .observation import record_event
            record_event(self, seq, extra=context, legacy=legacy)
        except Exception as exc:  # noqa: BLE001 - preserve the core write and expose the projection gap
            self.db.execute(f"ROLLBACK TO {savepoint}")
            self.db.execute(f"RELEASE {savepoint}")
            error = type(exc).__name__
            self.db.execute("INSERT INTO api_event_context VALUES(?,?) ON CONFLICT(seq) DO UPDATE SET context=excluded.context",
                            (seq, json.dumps({"projection_error": error})))
            logging.warning("observation projection failed for event %s: %s", seq, error)
        else:
            self.db.execute(f"RELEASE {savepoint}")

    def api_head(self) -> int:
        # The durable allocator survives retained-event deletion; absence never rewinds a cursor.
        return int(self.db.execute("SELECT MAX(COALESCE((SELECT seq FROM sqlite_sequence WHERE name='api_events'),0), "
                                   "COALESCE((SELECT MAX(seq) FROM api_events),0))").fetchone()[0])

    def api_events(self, after: int = 0, limit: int = 100, *, resource_type: str | None = None,
                   resource_id: str | None = None, kind: str | None = None,
                   related_resource_type: str | None = None, related_resource_id: str | None = None) -> dict:
        """Persistent global cursor; hidden migration facts still advance it."""
        from .observation import RESOURCE_TYPES, event_out
        if (isinstance(after, bool) or not isinstance(after, int) or after < 0
                or isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 500):
            raise ValueError("after must be >= 0 and limit between 0 and 500")
        if bool(related_resource_type) != bool(related_resource_id) or (related_resource_type and related_resource_type not in RESOURCE_TYPES):
            raise ValueError("related resource needs a type and full ID")
        head = self.api_head()
        if after > head:
            raise ValueError("after exceeds the journal head")
        sql, args = "SELECT e.* FROM api_events e WHERE e.seq>? AND e.seq<=?", [after, head]
        for column, value in (("resource_type", resource_type), ("resource_id", resource_id), ("kind", kind)):
            if value:
                sql += f" AND e.{column}=?"  # fixed identifiers
                args.append(value)
        if not kind:
            sql += " AND e.kind!='history.backfilled'"
        if related_resource_type:
            sql += """ AND EXISTS(SELECT 1 FROM api_event_resources r WHERE r.seq=e.seq
                AND r.resource_type=? AND r.resource_id=? AND r.linked_at_seq<=?)"""
            args.extend([related_resource_type, related_resource_id, head])
        rows = self.db.execute(sql + " ORDER BY e.seq LIMIT ?", (*args, limit + 1)).fetchall() if limit else []
        events = [event_out(self.db, r) for r in rows[:limit]]
        more = len(rows) > limit if limit else after < head
        last = events[-1]["seq"] if more and events else (after if not limit else head)
        return {"events": events, "next_cursor": last, "head_cursor": head, "has_more": more}

    def submit(self, *, project: str, host: str, workspace: str, original_words: str,
               discord_thread_id: str | None = None, recipe: str = "feature-to-staging",
               acceptance: str = "", engine: str = "rules", interpretation: str | None = None,
               lead_agent: str = "codex", pm_provider: str | None = None,
               base_branch: str | None = None, idempotency_key: str,
               task_path: str = "standard", engine_decision: dict | None = None,
               parent_task_id: str | None = None, continuation: bool = False,
               context_refs: dict | None = None) -> dict:
        if not all(isinstance(x, str) and x.strip() for x in (project, host, workspace, original_words, idempotency_key)):
            raise ValueError("project, host, workspace, original_words and idempotency_key are required")
        # The initial lead prompt adds a short wrapper under BAT's 20k limit.
        # Longer accepted requests would need the same verified archive path as
        # failover before their first send; reject them instead of truncating.
        if len(original_words) > 19_000 or len(idempotency_key) > 256 or len(acceptance) > 4000:
            raise ValueError("task input is too long")
        if interpretation is not None and len(interpretation) > 4000:
            raise ValueError("interpretation note is too long")
        if engine not in {"rules", "goose"}:
            raise ValueError("unknown engine")
        if task_path not in {"standard", "minimal"}:
            raise ValueError("unknown task path")
        if task_path == "standard" and engine_decision is not None:
            raise ValueError("engine decision requires minimal task path")
        if task_path == "minimal" and (not isinstance(engine_decision, dict)
                                        or engine_decision.get("selected") not in {"rules_engine", "goose"}
                                        or engine_decision.get("effective") != engine):
            raise ValueError("minimal path requires a bound engine decision")
        if lead_agent not in {"codex", "claude"}:
            raise ValueError("lead_agent must be codex or claude")
        if pm_provider is not None and (not isinstance(pm_provider, str) or not pm_provider
                                        or len(pm_provider) > 100):
            raise ValueError("invalid PM provider id")
        if base_branch is not None and (not isinstance(base_branch, str) or not base_branch
                                        or len(base_branch) > 256 or base_branch.startswith("-")):
            raise ValueError("invalid base branch")
        if not isinstance(continuation, bool):
            raise ValueError("continuation must be a boolean")
        refs = _context_refs(context_refs)
        from . import controller_authority
        authority = controller_authority.current()
        if authority is not None:
            with controller_authority.use(authority, self):
                pass  # Reject a binding inherited from a different central journal.
        if parent_task_id is not None:
            parent = self.db.execute("SELECT project,host,workspace FROM tasks WHERE task_id=?",
                                     (parent_task_id,)).fetchone() if isinstance(parent_task_id, str) else None
            if not parent or (parent["project"], parent["host"], parent["workspace"]) != (project, host, workspace):
                raise ValueError("parent task must exist in the same project, host and workspace")
        from .task_recipes import load

        load(recipe)
        payload = dict(project=project, host=host, workspace=workspace, original_words=original_words,
                       discord_thread_id=discord_thread_id, recipe=recipe, acceptance=acceptance,
                       engine=engine, interpretation=interpretation, lead_agent=lead_agent,
                       pm_provider=pm_provider, base_branch=base_branch)
        if task_path == "minimal":
            payload["task_path"] = task_path
        # Optional workstream/context fields join the digest only when set, so older keys still match.
        if parent_task_id is not None:
            payload["parent_task_id"] = parent_task_id
        if continuation:
            payload["continuation"] = True
        if refs:
            payload["context_refs"] = refs
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.tx():
            old = self.db.execute("SELECT * FROM tasks WHERE idem_key=?", (idempotency_key,)).fetchone()
            if old:
                if authority is not None:
                    controller_authority.require_binding(json.loads(old["controller_authority"])
                                                         if old["controller_authority"] else None)
                if old["payload_hash"] != digest:
                    raise ValueError("idempotency_key already belongs to different task content")
                return self.get(old["task_id"])
            now = time.time()
            task_id = str(uuid.uuid4())
            self.db.execute("""INSERT INTO tasks(task_id,idem_key,payload_hash,project,host,workspace,
                original_words,interpretation,discord_thread_id,recipe,acceptance,engine,lead_agent,
                pm_provider,base_branch,task_path,state,submitted_at,updated_at,
                parent_task_id,continuation,context_refs,controller_authority)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, idempotency_key, digest, project, host, workspace, original_words,
                 interpretation, discord_thread_id, recipe, acceptance, engine, lead_agent,
                 pm_provider, base_branch, task_path, "queued", now, now,
                 parent_task_id, int(continuation), json.dumps(refs, sort_keys=True) if refs else None,
                 json.dumps(authority, sort_keys=True) if authority else None))
            self._event(task_id, "submitted", {"state": "queued"})
            if engine_decision is not None:
                self._event(task_id, "engine_decision", engine_decision)
        return self.get(task_id)

    def by_idempotency_key(self, key: str) -> dict | None:
        row = self.db.execute("SELECT task_id FROM tasks WHERE idem_key=?", (key,)).fetchone()
        return self.get(row["task_id"]) if row else None

    def engine_decision(self, task_id: str) -> dict | None:
        row = self.db.execute("SELECT body FROM events WHERE task_id=? AND kind='engine_decision'",
                              (task_id,)).fetchone()
        return json.loads(row["body"]) if row else None

    def minimal_review_gate(self, task_id: str, commit: str, tree: str) -> dict | None:
        row = self.db.execute("""SELECT * FROM minimal_review_gates WHERE task_id=?
            AND candidate_commit=? AND tree_hash=?""", (task_id, commit, tree)).fetchone()
        return dict(row) if row else None

    def reserve_minimal_review(self, task_id: str, commit: str, tree: str,
                               diff_sha256: str, threshold: float, *, diff_chars: int | None = None,
                               paths: list[str] | None = None) -> dict:
        with self.tx():
            old = self.minimal_review_gate(task_id, commit, tree)
            if old:
                if old["diff_sha256"] != diff_sha256:
                    raise ValueError("candidate diff changed under review gate")
                return old
            self.db.execute("""INSERT INTO minimal_review_gates(task_id,candidate_commit,tree_hash,
                diff_sha256,verdict,threshold,created_at) VALUES(?,?,?,?,?,?,?)""",
                (task_id, commit, tree, diff_sha256, "pending", threshold, time.time()))
            self._event(task_id, "minimal_review_reserved", {"candidate_commit": commit,
                                                               "tree_hash": tree,
                                                               "diff_sha256": diff_sha256,
                                                               "diff_chars": diff_chars,
                                                               "paths": (paths or [])[:20]})
        return self.minimal_review_gate(task_id, commit, tree)

    def finish_minimal_review(self, task_id: str, commit: str, tree: str,
                              decision: dict) -> dict:
        if (decision.get("verdict") not in {"pass", "escalate"}
                or decision.get("reason") is None):
            raise ValueError("invalid minimal review decision")
        with self.tx():
            old = self.minimal_review_gate(task_id, commit, tree)
            if not old or old["verdict"] != "pending":
                raise ValueError("minimal review has no pending intent")
            if decision["verdict"] == "pass" and (
                    decision.get("jev_backend") not in {"typesafe", "openrouter_jev"}
                    or not isinstance(decision.get("confidence"), (int, float))
                    or isinstance(decision["confidence"], bool)
                    or not math.isfinite(decision["confidence"])
                    or decision["confidence"] > 1
                    or decision["confidence"] < old["threshold"]):
                raise ValueError("unproven Jev PASS")
            self.db.execute("""UPDATE minimal_review_gates SET verdict=?,confidence=?,
                jev_backend=?,reason=? WHERE task_id=? AND candidate_commit=? AND tree_hash=?""",
                (decision["verdict"], decision.get("confidence"), decision.get("jev_backend"),
                 decision["reason"], task_id, commit, tree))
            self._event(task_id, "minimal_review_decision", {
                "candidate_commit": commit, "tree_hash": tree, "verdict": decision["verdict"],
                "confidence": decision.get("confidence"), "jev_backend": decision.get("jev_backend"),
                "reason": decision["reason"]})
        return self.minimal_review_gate(task_id, commit, tree)

    def warm_candidates(self, task: dict) -> list[dict]:
        """Completed tasks in the same workstream whose lead session may be reused.

        Same workstream means an explicit parent (the parent itself or a sibling
        with the same parent) or an explicit continuation in the same origin
        thread. Independent requests get no candidates and start fresh.
        """
        parent, thread = task.get("parent_task_id"), task.get("discord_thread_id")
        if parent:
            scope, args = "(task_id=? OR parent_task_id=?)", (parent, parent)
        elif task.get("continuation") and thread:
            scope, args = "discord_thread_id=?", (thread,)
        else:
            return []
        rows = self.db.execute(f"""SELECT task_id FROM tasks WHERE project=? AND host=? AND workspace=?
            AND lead_agent=? AND state='done' AND session_id IS NOT NULL AND verification_commit IS NOT NULL
            AND external_worktree_path IS NULL AND reviewer_session_id IS NULL AND task_id<>? AND {scope}
            ORDER BY delivered_at DESC LIMIT 5""",  # noqa: S608 - fixed local SQL fragments
            (task["project"], task["host"], task["workspace"], task["lead_agent"], task["task_id"],
             *args)).fetchall()
        return [self.get(row["task_id"]) for row in rows]

    DELIVERY_STAGES = ("verified", "adopted", "merged", "deployed")

    def mark_stage(self, task_id: str, *, stage: str, ref: str, actor: str) -> dict:
        """Record that a verified task was adopted, merged or deployed (the service does none of these)."""
        if stage not in self.DELIVERY_STAGES[1:]:
            raise ValueError("stage must be adopted, merged or deployed")
        if not isinstance(ref, str) or not ref.strip() or len(ref) > 300:
            raise ValueError("stage ref is required (commit, PR or deploy reference, max 300 chars)")
        if actor not in {"service", "ted", "hermes", "executor"}:
            raise ValueError("invalid actor")
        with self.tx():
            task = self.get(task_id)
            if task["state"] != "done" or not task["verification_commit"]:
                raise ValueError("only a verified (done) task can be marked adopted, merged or deployed")
            if not self.db.execute("SELECT 1 FROM events WHERE task_id=? AND kind=?",
                                   (task_id, "stage_" + stage)).fetchone():
                self._event(task_id, "stage_" + stage, {"stage": stage, "ref": ref.strip(), "actor": actor,
                                                       "verification_commit": task["verification_commit"]})
        return self.delivery(task_id)

    def delivery(self, task_id: str) -> dict:
        """Distinguish verified / adopted / merged / deployed from recorded facts only."""
        task = self.get(task_id)
        verified = None
        if task["state"] == "done" and task["verification_commit"]:
            gate = (self.minimal_review_gate(task_id, task["verification_commit"], task["verification_tree"])
                    if task["verification_tree"] else None)
            basis = ("independent_review" if task["review_passed"] else
                     "jev_review_gate" if gate and gate["verdict"] == "pass" else "reconciled")
            verified = {"commit": task["verification_commit"], "tree": task["verification_tree"],
                        "basis": basis, "at": task["delivered_at"]}
        stages: dict = {"verified": verified}
        for stage in self.DELIVERY_STAGES[1:]:
            row = self.db.execute("SELECT body,created_at FROM events WHERE task_id=? AND kind=?",
                                  (task_id, "stage_" + stage)).fetchone()
            body = self._body(row["body"]) if row else None
            stages[stage] = ({"ref": body.get("ref"), "actor": body.get("actor"), "at": row["created_at"]}
                             if body else None)
        reached = [stage for stage in self.DELIVERY_STAGES if stages[stage]]
        return {"stage": reached[-1] if reached else None, **stages,
                "note": "merged/deployed are reported only when recorded; the task service never merges or deploys"}

    def get(self, task_id: str) -> dict:
        row = self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        d = dict(row)
        d["controller_authority"] = json.loads(d["controller_authority"]) if d.get("controller_authority") else None
        d["continuation"] = bool(d.get("continuation"))
        d["context_refs"] = json.loads(d["context_refs"]) if d.get("context_refs") else None
        d["paused"] = bool(d["paused"])
        d["delivered"] = bool(d["delivered"])
        d["review_passed"] = bool(d["review_passed"])
        d["time_to_deliver_s"] = (d["delivered_at"] - d["submitted_at"]) if d["delivered_at"] else None
        d["branches"] = self.branches(task_id)
        d["branch_ids"] = [b["branch_id"] for b in d["branches"]]
        return d

    def add_branch(self, task_id: str, *, session_id: str | None, provider: str,
                   role: str, reason: str, parent_branch_id: str | None = None) -> dict:
        with self.tx():
            old = (self.db.execute("SELECT * FROM branches WHERE task_id=? AND session_id=? AND role=?",
                                   (task_id, session_id, role)).fetchone() if session_id is not None else None)
            if old:
                return dict(old)
            branch_id = str(uuid.uuid4())
            self.db.execute("""INSERT INTO branches(branch_id,task_id,session_id,provider,role,
                parent_branch_id,reason,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (branch_id, task_id, session_id, provider, role, parent_branch_id, reason, time.time()))
            self._event(task_id, "task_branch", {"branch_id": branch_id, "provider": provider,
                                                 "role": role, "reason": reason})
        return dict(self.db.execute("SELECT * FROM branches WHERE branch_id=?", (branch_id,)).fetchone())

    def branches(self, task_id: str) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM branches WHERE task_id=? ORDER BY created_at, rowid", (task_id,))]

    def list_active(self) -> list[dict]:
        ids = self.db.execute("SELECT task_id FROM tasks WHERE state NOT IN ('done','failed') ORDER BY submitted_at").fetchall()
        return [self.get(r[0]) for r in ids]

    def list_cleanup_pending(self) -> list[dict]:
        ids = self.db.execute("""SELECT task_id FROM tasks WHERE state IN ('done','failed')
            AND external_worktree_path IS NOT NULL ORDER BY updated_at""").fetchall()
        return [self.get(r[0]) for r in ids]

    def change(self, task_id: str, state: str, *, fields: dict | None = None, event: str | None = None):
        if state not in STATES:
            raise ValueError("invalid task state")
        fields = fields or {}
        allowed = {"session_id", "reviewer_session_id", "turn_marker", "result", "continuations",
                   "review_rejections", "review_passed", "verification_commit", "verification_tree",
                   "review_commit", "review_tree", "review_marker", "ted_interventions", "lead_agent",
                   "base_branch", "base_commit", "external_worktree_path", "external_branch",
                   "verification_failures"}
        if fields.keys() - allowed:
            raise ValueError("invalid task fields")
        with self.tx():
            old = self.get(task_id)
            if old["state"] in TERMINAL and old["state"] != state:
                raise ValueError("terminal task cannot transition")
            if old["state"] != state and state not in ALLOWED[old["state"]]:
                raise ValueError(f"invalid transition {old['state']} -> {state}")
            values = {**old, **fields, "state": state, "updated_at": time.time()}
            # A new verifying phase starts the absolute clock. Reviewer start
            # (via dispatching) and uncertainty recovery continue the same phase.
            if state == "verifying" and old["state"] != "verifying" and (
                    old["state"] not in {"dispatching", "uncertain"} or not old["verifying_started_at"]):
                values.update(verifying_started_at=values["updated_at"], progress_at=values["updated_at"])
            if state == "done":
                # The trusted runner is the verdict. Goose reviews inside its own loop.
                observed = self.observed_verification(task_id)
                if (not observed or observed["exit_code"] != 0
                        or values["verification_commit"] != observed["candidate_commit"]
                        or values["verification_tree"] != observed["tree_hash"]):
                    raise ValueError("observed commit/tree verification required")
                values.update(delivered=1, delivered_at=time.time())
            self.db.execute("""UPDATE tasks SET state=?,updated_at=?,session_id=?,reviewer_session_id=?,
                turn_marker=?,result=?,continuations=?,review_rejections=?,review_passed=?,
                verification_commit=?,verification_tree=?,review_commit=?,review_tree=?,review_marker=?,
                lead_agent=?,ted_interventions=?,base_branch=?,base_commit=?,external_worktree_path=?,external_branch=?,delivered=?,delivered_at=?,
                verification_failures=?,verifying_started_at=?,progress_at=? WHERE task_id=?""",
                (values["state"], values["updated_at"], values["session_id"], values["reviewer_session_id"],
                 values["turn_marker"], values["result"], values["continuations"], values["review_rejections"],
                 values["review_passed"], values["verification_commit"], values["verification_tree"],
                 values["review_commit"], values["review_tree"], values["review_marker"], values["lead_agent"],
                 values["ted_interventions"], values["base_branch"], values["base_commit"],
                 values["external_worktree_path"], values["external_branch"], values["delivered"], values["delivered_at"],
                 values["verification_failures"], values["verifying_started_at"], values["progress_at"], task_id))
            if old["state"] != state or event:
                body = {"from": old["state"], "to": state}
                if state in {"needs_ted", "failed"} and old["state"] != state and values["result"]:
                    body["reason"] = str(values["result"])[:1000]
                self._event(task_id, event or "state", body)
        return self.get(task_id)

    def complete_external_cleanup(self, task_id: str, proof: dict) -> dict:
        """Clear a terminal worktree pointer only with proof of a retained Git ref."""
        with self.tx():
            task = self.get(task_id)
            suffix = task_id.replace("-", "")[:12]
            shared = False
            if proof.get("operation_id") and proof.get("resource_id"):
                receipt = self.db.execute("SELECT status,plan FROM cleanup_receipts WHERE operation_id=? AND resource_id=?",
                    (proof["operation_id"], proof["resource_id"])).fetchone()
                retained = self.db.execute("SELECT 1 FROM cleanup_retained WHERE operation_id=? AND resource_id=? "
                    "AND ref=? AND commit_sha=?", (proof["operation_id"], proof["resource_id"],
                    proof.get("retained_ref"), proof.get("commit"))).fetchone()
                plan = json.loads(receipt["plan"]) if receipt else {}
                shared = bool(receipt and receipt["status"] == "succeeded" and retained
                    and plan.get("flavor") == "task" and plan.get("creation_evidence", {}).get("intent") == task_id
                    and plan.get("path") == proof.get("path") and plan.get("branch") == proof.get("branch"))
            if (task["state"] not in TERMINAL or not task["external_worktree_path"]
                    or proof.get("path") != task["external_worktree_path"]
                    or proof.get("branch") != task["external_branch"]
                    or not shared and proof.get("retained_ref") != f"refs/batc/tasks/{suffix}"
                    or not isinstance(proof.get("commit"), str)
                    or not re.fullmatch(r"[0-9a-f]{40}", proof["commit"])
                    or proof.get("mode") not in {"removed", "already_removed"}
                    or (task["state"] == "done" and proof["commit"] != task["verification_commit"])):
                raise ValueError("external cleanup proof does not match terminal task")
            self.db.execute("""UPDATE tasks SET external_worktree_path=NULL,external_branch=NULL,
                updated_at=? WHERE task_id=?""", (time.time(), task_id))
            self._event(task_id, "external_worktree_retained", proof)
        return self.get(task_id)

    def mark_initial_session_vanished(self, task_id: str, session_id: str) -> dict:
        """One replacement at most; an unresolved prompt always blocks replacement."""
        with self.tx():
            task = self.get(task_id)
            if (task["session_id"] != session_id or task["state"] != "accepted"
                    or task["paused"]):
                raise ValueError("vanished session is not the active initial lead")
            unsafe = self.db.execute("""SELECT 1 FROM commands WHERE task_id=? AND session_id=?
                AND kind='send' AND status NOT IN ('rejected','cancelled') LIMIT 1""",
                (task_id, session_id)).fetchone()
            if unsafe:
                target, outcome = "uncertain", "prompt_requires_reconciliation"
            elif task["session_replacements"] >= 1:
                target, outcome = "needs_ted", "replacement_limit_reached"
            else:
                target, outcome = "queued", "replacement_reserved"
            count = task["session_replacements"] + (target == "queued")
            self.db.execute("""UPDATE tasks SET state=?,session_id=?,turn_marker=NULL,
                session_replacements=?,updated_at=? WHERE task_id=?""",
                (target, None if target == "queued" else session_id, count, time.time(), task_id))
            self._event(task_id, "initial_session_vanished", {"session_id": session_id,
                                                              "outcome": outcome,
                                                              "session_replacements": count})
        return self.get(task_id)

    def pause(self, task_id: str, *, abort_current: bool = False):
        with self.tx():
            old = self.get(task_id)
            if old["state"] in TERMINAL:
                return old
            self.db.execute("UPDATE tasks SET paused=1,control_version=control_version+1,updated_at=? WHERE task_id=?",
                            (time.time(), task_id))
            self._event(task_id, "paused", {"abort_current": abort_current})
        return self.get(task_id)

    def resume(self, task_id: str):
        with self.tx():
            old = self.get(task_id)
            if old["state"] in TERMINAL:
                return old
            now = time.time()
            self.db.execute("UPDATE tasks SET paused=0,control_version=control_version+1,updated_at=? WHERE task_id=?",
                            (now, task_id))
            if old["state"] == "verifying":
                # The user's resume restarts the verification clocks; paused time is not stall time.
                self.db.execute("UPDATE tasks SET verifying_started_at=?,progress_at=? WHERE task_id=?",
                                (now, now, task_id))
            self._event(task_id, "resumed")
        return self.get(task_id)

    def ted_action(self, task_id: str, *, action: str, source_message_id: str):
        if not action or not source_message_id:
            raise ValueError("User action requires provenance")
        with self.tx():
            cur = self.db.execute("""INSERT OR IGNORE INTO ted_actions(task_id,source_message_id,action,created_at)
                VALUES(?,?,?,?)""", (task_id, source_message_id, action, time.time()))
            if not cur.rowcount:
                return
            self.db.execute("UPDATE tasks SET ted_interventions=ted_interventions+1 WHERE task_id=?", (task_id,))
            self._event(task_id, "caller_reported_ted_intervention",
                        {"action": action, "source_message_id": source_message_id})

    def progress(self, task_id: str) -> None:
        """Record meaningful verification progress without an event or updated_at bump."""
        self.db.execute("UPDATE tasks SET progress_at=? WHERE task_id=? AND state='verifying'",
                        (time.time(), task_id))

    def has_note(self, task_id: str, kind: str, candidate_commit: str) -> bool:
        return self.db.execute("""SELECT 1 FROM events WHERE task_id=? AND kind=? AND json_valid(body)
            AND json_extract(body,'$.candidate_commit')=? LIMIT 1""",
            (task_id, kind, candidate_commit)).fetchone() is not None

    def record_continuation(self, task_id: str, key: str, words: str) -> bool:
        """Attach the user's steering to an existing task. Same session, no new task, no re-plan."""
        self.get(task_id)
        if not isinstance(key, str) or not key.strip() or len(key) > 256:
            raise ValueError("invalid continuation key")
        if not isinstance(words, str) or not words.strip() or len(words) > 19_000:
            raise ValueError("invalid continuation")
        with self.tx():
            rows = self.db.execute("SELECT body FROM events WHERE task_id=? AND kind='continuation'",
                                   (task_id,)).fetchall()
            for row in rows:
                body = self._body(row["body"])
                if body.get("idempotency_key") == key:
                    return False
            self._event(task_id, "continuation", {"idempotency_key": key, "words": words,
                                                 "replan": False})
            self.db.execute("UPDATE tasks SET continuations=continuations+1, updated_at=? WHERE task_id=?",
                            (time.time(), task_id))
        return True

    def note(self, task_id: str, kind: str, body: dict):
        """Append an audit event without changing task state."""
        with self.tx():
            self._event(task_id, kind, body)

    def request_ted(self, task_id: str, reason: str):
        with self.tx():
            self._event(task_id, "ted_requested", {"reason": reason[:1000]})

    def command(self, task_id: str, kind: str, session_id: str | None, payload: dict, idem_key: str):
        with self.tx():
            old = self.db.execute("SELECT * FROM commands WHERE idem_key=?", (idem_key,)).fetchone()
            if old:
                return dict(old), False
            task = self.get(task_id)
            if (task["paused"] or task["state"] in TERMINAL or
                    task["state"] in {"human_owned", "uncertain", "needs_ted"}):
                raise ValueError("task does not allow dispatch")
            if kind == "send" and task["state"] not in {"accepted", "running", "verifying", "dispatching"}:
                raise ValueError("task state does not allow a prompt")
            unresolved = self.db.execute("""SELECT 1 FROM commands WHERE task_id=?
                AND status IN ('intent','needs_review','uncertain')
                AND (kind IN ('send','answer','interrupt','permissions','failover') OR kind LIKE 'start_%') LIMIT 1""",
                (task_id,)).fetchone()
            if unresolved:
                raise ValueError("task has a command requiring reconciliation")
            command_id = str(uuid.uuid4())
            now = time.time()
            from . import registry
            owner = registry.get(task["host"], session_id) if session_id else None
            if owner and owner.get("controller_incarnation"):
                payload = {**payload, "controller_incarnation": owner["controller_incarnation"]}
            self.db.execute("""INSERT INTO commands(command_id,task_id,idem_key,session_id,kind,status,
                message_id,payload,created_at,updated_at,controller_authority) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (command_id, task_id, idem_key, session_id, kind,
                 "needs_review" if kind == "send" else "intent", f"batc-{command_id}",
                 json.dumps(payload, ensure_ascii=False), now, now,
                 json.dumps(task["controller_authority"], sort_keys=True) if task.get("controller_authority") else None))
            self._event(task_id, "command_intent", {"command_id": command_id, "kind": kind,
                                                     "needs_review": kind == "send"})
        return dict(self.db.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()), True

    def reserve_failover(self, task_id: str, old_session_id: str, successor_id: str) -> tuple[dict, dict]:
        """Reserve a successor and its separate, reconcilable handoff send atomically."""
        with self.tx():
            task = self.get(task_id)
            if task["state"] != "quota_limited" or task["paused"] or task["session_id"] != old_session_id:
                raise ValueError("task does not allow failover")
            pending = self.db.execute("""SELECT 1 FROM commands WHERE task_id=?
                AND status IN ('intent','needs_review','uncertain')
                AND (kind IN ('send','answer','interrupt','permissions','failover') OR kind LIKE 'start_%') LIMIT 1""", (task_id,)).fetchone()
            if pending:
                raise ValueError("task has a command requiring reconciliation")
            now = time.time()
            failover_id, send_id = str(uuid.uuid4()), str(uuid.uuid4())
            rows = (
                (failover_id, f"{task_id}:failover:{old_session_id}:{task['control_version']}",
                 "failover", "intent", {"old_session_id": old_session_id, "handoff_command_id": send_id}),
                (send_id, f"{task_id}:handoff:{successor_id}", "send", "needs_review",
                 {"purpose": "failover_handoff", "old_session_id": old_session_id,
                  "before": {"agent_kind": "codex"}, "prompt_sha256": None}),
            )
            for command_id, key, kind, status, payload in rows:
                self.db.execute("""INSERT INTO commands(command_id,task_id,idem_key,session_id,kind,status,
                    message_id,payload,created_at,updated_at,controller_authority) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (command_id, task_id, key, successor_id, kind, status, f"batc-{command_id}",
                     json.dumps(payload), now, now,
                     json.dumps(task["controller_authority"], sort_keys=True) if task.get("controller_authority") else None))
                self._event(task_id, "command_intent", {"command_id": command_id, "kind": kind,
                                                         "needs_review": status == "needs_review"})
        return self.command_get(failover_id), self.command_get(send_id)

    def command_prompt_hash(self, command_id: str, digest: str):
        with self.tx():
            cmd = self.command_get(command_id)
            if cmd["kind"] != "send" or cmd["status"] != "needs_review":
                raise ValueError("handoff prompt intent is no longer pending")
            payload = json.loads(cmd["payload"])
            if payload.get("purpose") != "failover_handoff":
                raise ValueError("not a failover handoff command")
            payload["prompt_sha256"] = digest
            self.db.execute("UPDATE commands SET payload=?,updated_at=? WHERE command_id=?",
                            (json.dumps(payload), time.time(), command_id))

    def command_operator_only(self, command_id: str, reason: str):
        """A concrete identity conflict must not become auto-recoverable on a later tick."""
        with self.tx():
            cmd = self.command_get(command_id)
            payload = json.loads(cmd["payload"])
            payload["operator_only"] = True
            self.db.execute("UPDATE commands SET payload=?,status='uncertain',updated_at=? WHERE command_id=?",
                            (json.dumps(payload), time.time(), command_id))
            self._event(cmd["task_id"], "command_identity_conflict",
                        {"command_id": command_id, "reason": reason})

    def resolve_failover(self, task_id: str, command_id: str, handoff_command_id: str, *,
                         token: str, outcome: str, actor: str, source: str, evidence: str,
                         token_hash: str | None = None) -> dict:
        """Close a conflicted failover by operator attestation without adopting a successor."""
        if (outcome not in {"delivered", "not_delivered", "superseded"}
                or actor not in {"operator", "ted"} or not source.strip() or len(source) > 256
                or not evidence.strip() or len(evidence) > 2000):
            raise ValueError("valid operator outcome and provenance are required")
        digest = token_hash or hashlib.sha256(token.encode()).hexdigest()
        with self.tx():
            task = self.get(task_id)
            cmd = self.command_get(command_id)
            handoff = self.command_get(handoff_command_id)
            cap = self.db.execute("""SELECT expires_at FROM capabilities WHERE token_hash=?
                AND task_id=? AND command_id=? AND scope='reconcile'""",
                (digest, task_id, command_id)).fetchone()
            if (task["state"] != "uncertain" or cmd["task_id"] != task_id or cmd["kind"] != "failover"
                    or cmd["status"] != "uncertain" or handoff["task_id"] != task_id
                    or handoff["kind"] != "send" or handoff["session_id"] != cmd["session_id"]
                    or json.loads(cmd["payload"]).get("handoff_command_id") != handoff_command_id
                    or not cap or cap["expires_at"] <= time.time()):
                raise ValueError("failover reconciliation identity or capability is invalid")
            now = time.time()
            self.db.execute("""INSERT INTO command_reconciliations(command_id,task_id,outcome,actor,source,
                evidence,observed_result,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (command_id, task_id, outcome, actor, source, evidence, "none", now))
            self.db.execute("DELETE FROM capabilities WHERE token_hash=?", (digest,))
            self.db.execute("UPDATE commands SET status=?,updated_at=? WHERE command_id IN (?,?)",
                            ("resolved_" + outcome, now, command_id, handoff_command_id))
            self.db.execute("UPDATE tasks SET state='human_owned',updated_at=? WHERE task_id=?", (now, task_id))
            if actor == "ted":
                action = self.db.execute("""INSERT OR IGNORE INTO ted_actions(task_id,source_message_id,action,created_at)
                    VALUES(?,?,?,?)""", (task_id, source, "reconcile_failover", now))
                if action.rowcount:
                    self.db.execute("UPDATE tasks SET ted_interventions=ted_interventions+1 WHERE task_id=?",
                                    (task_id,))
            self._event(task_id, "failover_operator_reconciled", {"command_id": command_id,
                        "outcome": outcome, "actor": actor, "source": source, "state": "human_owned"})
        return self.get(task_id)

    def command_status(self, command_id: str, status: str, *, marker: str | None = None):
        if status not in {"intent", "needs_review", "accepted", "running", "settled", "uncertain",
                          "rejected", "cancelled"}:
            raise ValueError("invalid command status")
        with self.tx():
            row = self.db.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()
            if row is None:
                raise KeyError(command_id)
            self.db.execute("UPDATE commands SET status=?,marker=COALESCE(?,marker),updated_at=? WHERE command_id=?",
                            (status, marker, time.time(), command_id))
            self._event(row["task_id"], "command_" + status, {"command_id": command_id})

    def command_bind_session(self, command_id: str, session_id: str):
        with self.tx():
            row = self.db.execute("SELECT task_id,session_id FROM commands WHERE command_id=?", (command_id,)).fetchone()
            self.db.execute("UPDATE commands SET session_id=?,updated_at=? WHERE command_id=?",
                            (session_id, time.time(), command_id))
            if row and row["session_id"] != session_id:
                self._event(row["task_id"], "command_bound", {"command_id": command_id, "session_id": session_id, "previous_session_id": row["session_id"]})

    def commands(self, task_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM commands WHERE task_id=? ORDER BY created_at", (task_id,))]

    def reconciliations(self, task_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM command_reconciliations WHERE task_id=? ORDER BY created_at", (task_id,))]

    def events(self, task_id: str | None = None) -> list[dict]:
        if task_id:
            rows = self.db.execute("SELECT * FROM events WHERE task_id=? ORDER BY event_id", (task_id,))
        else:
            rows = self.db.execute("SELECT * FROM events ORDER BY event_id")
        return [dict(r) for r in rows]

    def route(self, task_id: str, *, step: str, step_type: str, provider: str,
              confidence: float | None, stakes: str, reason: str,
              jev_backend: str | None = None) -> dict:
        with self.tx():
            existing = self.route_for_step(task_id, step)
            if existing:
                return existing
            now = time.time()
            cur = self.db.execute("""INSERT INTO routing(task_id,step,step_type,provider,confidence,stakes,reason,
                jev_backend,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (task_id, step, step_type, provider, confidence, stakes, reason, jev_backend, now))
            self._event(task_id, "model_route", {"route_id": cur.lastrowid, "step_type": step_type,
                                                  "provider": provider, "confidence": confidence,
                                                  "stakes": stakes, "jev_backend": jev_backend})
        return dict(self.db.execute("SELECT * FROM routing WHERE route_id=?", (cur.lastrowid,)).fetchone())

    def routes(self, task_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM routing WHERE task_id=? ORDER BY route_id", (task_id,))]

    def route_for_step(self, task_id: str, step: str) -> dict | None:
        row = self.db.execute("SELECT * FROM routing WHERE task_id=? AND step=? ORDER BY route_id LIMIT 1",
                              (task_id, step)).fetchone()
        return dict(row) if row else None

    def record_jev_prescreen(self, task_id: str, *, commit: str, tree: str, verdict: str,
                             confidence: float, tests_ok: float,
                             jev_backend: str | None = None) -> None:
        if verdict not in {"safe_complete", "incomplete", "unsafe", "unsure"}:
            raise ValueError("invalid Jev pre-screen verdict")
        with self.tx():
            if self.jev_prescreen_for_candidate(task_id, commit, tree):
                return
            self._event(task_id, "jev_prescreen", {"candidate_commit": commit, "tree_hash": tree,
                                                  "verdict": verdict, "confidence": confidence,
                                                  "tests_ok": tests_ok, "jev_backend": jev_backend,
                                                  "advisory_only": True})

    def jev_prescreen_for_candidate(self, task_id: str, commit: str, tree: str) -> bool:
        rows = self.db.execute("SELECT body FROM events WHERE task_id=? AND kind='jev_prescreen'",
                               (task_id,))
        return any((body.get("candidate_commit"), body.get("tree_hash")) == (commit, tree)
                   for body in (json.loads(row[0]) for row in rows))

    def provider_use(self, provider: str, outcome: str):
        if outcome not in {"success", "quota_error", "rate_limited", "auth_error"}:
            raise ValueError("invalid provider outcome")
        self.db.execute("INSERT INTO provider_usage(provider,outcome,created_at) VALUES(?,?,?)",
                        (provider, outcome, time.time()))

    def provider_daily_count(self, provider: str, *, since: float) -> int:
        return self.db.execute("SELECT count(*) FROM provider_usage WHERE provider=? AND created_at>=? AND outcome='success'",
                               (provider, since)).fetchone()[0]

    def provider_unavailable(self, provider: str, *, since: float) -> bool:
        row = self.db.execute("""SELECT outcome FROM provider_usage WHERE provider=? AND created_at>=?
            ORDER BY usage_id DESC LIMIT 1""", (provider, since)).fetchone()
        return bool(row and row[0] in {"quota_error", "rate_limited", "auth_error"})

    MILESTONE_KINDS = ("started", "needs_ted", "done", "failed")
    _PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")

    def push_state(self) -> dict | None:
        row = self.db.execute("SELECT * FROM feed_push WHERE singleton=1").fetchone()
        return dict(row) if row else None

    def push_init(self, cursor: int) -> dict:
        self.db.execute("INSERT OR IGNORE INTO feed_push(singleton,cursor,updated_at) VALUES(1,?,?)",
                        (cursor, time.time()))
        return self.push_state()

    def push_advance(self, cursor: int) -> None:
        self.db.execute("""UPDATE feed_push SET cursor=MAX(cursor,?),failures=0,next_attempt_at=0,
            last_error=NULL,updated_at=? WHERE singleton=1""", (cursor, time.time()))

    def push_failed(self, error: str, next_attempt_at: float) -> None:
        self.db.execute("""UPDATE feed_push SET failures=failures+1,next_attempt_at=?,last_error=?,
            updated_at=? WHERE singleton=1""", (next_attempt_at, error[:200], time.time()))

    def head_cursor(self) -> int:
        """Newest event cursor; a new feed reader starts here to skip history."""
        return int(self.db.execute("SELECT COALESCE(MAX(event_id),0) FROM events").fetchone()[0])

    @staticmethod
    def _body(raw: str) -> dict:
        try:
            body = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return body if isinstance(body, dict) else {}

    def _transition_before(self, task_id: str, event_id: int, targets: tuple[str, ...]) -> bool:
        marks = ",".join("?" * len(targets))
        return self.db.execute(f"""SELECT 1 FROM events WHERE task_id=? AND event_id<?
            AND json_valid(body) AND json_extract(body,'$.to') IN ({marks})
            AND json_extract(body,'$.to') IS NOT COALESCE(json_extract(body,'$.from'),'')
            LIMIT 1""", (task_id, event_id, *targets)).fetchone() is not None  # noqa: S608 - placeholders only

    def _milestone_reason(self, task: dict, event_id: int, body: dict, to: str) -> str | None:
        if isinstance(body.get("reason"), str) and body["reason"].strip():
            return body["reason"]
        # The last event before this transition may be an explicit user request.
        row = self.db.execute("""SELECT kind,body FROM events WHERE task_id=? AND event_id<?
            AND (kind='ted_requested' OR (json_valid(body) AND json_extract(body,'$.to') IS NOT NULL))
            ORDER BY event_id DESC LIMIT 1""", (task["task_id"], event_id)).fetchone()
        if row and row["kind"] == "ted_requested":
            reason = self._body(row["body"]).get("reason")
            if isinstance(reason, str) and reason.strip():
                return reason
        # Older journals did not store the reason on the transition; the task
        # result still describes it while this is the task's latest transition.
        later = self.db.execute("""SELECT 1 FROM events WHERE task_id=? AND event_id>?
            AND json_valid(body) AND json_extract(body,'$.to') IS NOT NULL
            AND json_extract(body,'$.to') IS NOT COALESCE(json_extract(body,'$.from'),'') LIMIT 1""",
            (task["task_id"], event_id)).fetchone()
        if not later and task["state"] == to and task["result"]:
            return str(task["result"])
        return None

    def milestones(self, since_cursor: int = 0, limit: int = 50, *, scan_limit: int = 5000,
                   repo_urls: dict[str, str] | None = None) -> dict:
        """Read-only milestone feed: started, needs_ted, done and failed transitions.

        ``cursor`` is the journal event id, so it is monotonic. ``next_cursor``
        also advances past scanned non-milestone events; a reader persists it
        only after it has published every returned event.
        """
        if (isinstance(since_cursor, bool) or not isinstance(since_cursor, int) or since_cursor < 0
                or isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 200):
            raise ValueError("since_cursor must be >= 0 and limit between 0 and 200")
        head = self.head_cursor()
        result = {"events": [], "next_cursor": since_cursor, "head_cursor": head, "has_more": False}
        if limit == 0:
            result["has_more"] = since_cursor < head
            return result
        rows = self.db.execute("""SELECT event_id,task_id,kind,body,created_at FROM events
            WHERE event_id>? ORDER BY event_id LIMIT ?""", (since_cursor, scan_limit)).fetchall()
        tasks: dict[str, dict] = {}
        for row in rows:
            result["next_cursor"] = row["event_id"]
            body = self._body(row["body"])
            to, frm = body.get("to"), body.get("from")
            if not isinstance(to, str) or to == frm:
                continue
            resumed = False
            if to in {"done", "failed", "needs_ted"}:
                kind = to
            elif to in {"accepted", "running", "verifying"} and frm == "needs_ted":
                kind, resumed = "started", True
            elif to in {"accepted", "running"} and not self._transition_before(
                    row["task_id"], row["event_id"], ("accepted", "running")):
                kind = "started"
            else:
                continue
            if row["task_id"] not in tasks:
                task_row = self.db.execute("""SELECT task_id,project,host,workspace,discord_thread_id,
                    original_words,state,result,verification_commit FROM tasks WHERE task_id=?""",
                    (row["task_id"],)).fetchone()
                tasks[row["task_id"]] = dict(task_row) if task_row else None
            task = tasks[row["task_id"]]
            if task is None:
                continue
            reason = (self._milestone_reason(task, row["event_id"], body, to)
                      if kind in {"needs_ted", "failed"} else None)
            reason = reason.strip()[:500] if reason else None
            commit = task["verification_commit"] if kind == "done" else None
            pr = self._PR_URL.search((task["result"] or "") if kind == "done" else (reason or ""))
            pr_url = pr.group(0) if pr else None
            repo = (repo_urls or {}).get(task["project"])
            commit_url = f"{repo.rstrip('/')}/commit/{commit}" if repo and commit else None
            code = row["kind"] if row["kind"] != "state" else None
            detail = reason or (code.replace("_", " ") if code else None)
            if kind == "started":
                summary = "Resumed after needs_ted" if resumed else "Task started"
            elif kind == "done":
                summary = "Done" + (f": verified commit {commit[:10]}" if commit else "")
            else:
                summary = ("Needs user input" if kind == "needs_ted" else "Failed") + (f": {detail}" if detail else "")
            title = next((line.strip() for line in task["original_words"].splitlines() if line.strip()), "")
            result["events"].append({
                "cursor": row["event_id"], "task_id": task["task_id"], "project": task["project"],
                "workspace": task["workspace"], "host": task["host"],
                "origin_thread_id": task["discord_thread_id"], "kind": kind,
                "summary": summary[:300], "reason": reason, "reason_code": code,
                "from_state": frm, "to_state": to, "resumed": resumed,
                "commit": commit, "pr_url": pr_url, "commit_url": commit_url,
                "link": pr_url or commit_url, "title": title[:120], "task_state": task["state"],
                "at": row["created_at"],
            })
            if len(result["events"]) >= limit:
                break
        result["has_more"] = result["next_cursor"] < head
        return result
