"""A rolled-back Connector must refuse a newer journal before any schema write."""

import asyncio
import hashlib
import sqlite3
from contextlib import closing

import pytest

from bat_agent_connector import cli, registry
from bat_agent_connector.config import parse_config
from bat_agent_connector.task_daemon import TaskDaemon
from bat_agent_connector.task_journal import LATEST_DATA_STEP, Journal


def snapshot(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        schema = db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        counts = {
            name: db.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
            for kind, name, _ in schema if kind == "table"
        }
        version = db.execute("PRAGMA user_version").fetchone()[0]
    return digest, schema, counts, version


def newer_journal(path, mode):
    journal = Journal(path)
    journal.submit(project="p", host="h1", workspace="w", original_words="Keep this history",
                   acceptance="preserved", idempotency_key="compatibility-test")
    # A newer schema need not retain an older, empty table. Recreating it is a write too.
    journal.db.execute("DROP TABLE pr_merge_previews")
    journal.db.execute(f"PRAGMA user_version={LATEST_DATA_STEP + 1}")
    journal.close()
    with closing(sqlite3.connect(path)) as db:
        db.execute(f"PRAGMA journal_mode={mode}")
    return snapshot(path)


def assert_refusal(error):
    message = str(error)
    assert "JOURNAL_NEWER_DATA_STEP" in message
    assert f"data step {LATEST_DATA_STEP + 1}" in message
    assert f"supports up to {LATEST_DATA_STEP}" in message
    assert "reopen with the newer Connector" in message


@pytest.mark.parametrize("mode", ["WAL", "DELETE"])
def test_journal_refuses_newer_step_without_any_committed_change(tmp_path, mode):
    path = tmp_path / "journal.db"
    before = newer_journal(path, mode)
    try:
        with pytest.raises(ValueError) as refused:
            with closing(Journal(path)):
                pass
        assert_refusal(refused.value)
        assert refused.value.code == "JOURNAL_NEWER_DATA_STEP"
    finally:
        # Counts/schema/version also see committed WAL changes; main-file hash alone cannot.
        assert snapshot(path) == before


@pytest.mark.parametrize("mode", ["WAL", "DELETE"])
async def test_real_service_startup_refuses_before_binding_or_installing_api(tmp_path, monkeypatch, mode):
    path = tmp_path / "journal.db"
    before = newer_journal(path, mode)
    daemon = TaskDaemon(parse_config({"hosts": {}}), path)

    async def unexpected_listener(*args, **kwargs):
        pytest.fail("a refused journal must never open an HTTP listener")

    monkeypatch.setattr(asyncio, "start_server", unexpected_listener)
    try:
        with pytest.raises(ValueError) as refused:
            await daemon.serve(port=0)
        assert_refusal(refused.value)
        assert daemon._lease_fd is None
        assert not daemon._initialized
        assert "api" not in daemon.__dict__
        assert not (registry.registry_path().parent / "task-service.json").exists()
    finally:
        if journal := daemon.__dict__.get("journal"):
            journal.close()
        daemon.release_owner()
        assert snapshot(path) == before


def test_cli_serve_reports_stable_refusal_and_exit_one(tmp_path, monkeypatch, capsys):
    path = tmp_path / "journal.db"
    before = newer_journal(path, "WAL")
    config = tmp_path / "hosts.toml"
    config.write_text("[hosts]\n")

    async def unexpected_listener(*args, **kwargs):
        pytest.fail("CLI must refuse before opening its HTTP listener")

    monkeypatch.setattr(asyncio, "start_server", unexpected_listener)
    try:
        assert cli.main(["--config", str(config), "serve", "--db", str(path), "--port", "0"]) == 1
        output = capsys.readouterr()
        assert not output.out
        assert_refusal(output.err)
        assert "Traceback" not in output.err
    finally:
        assert snapshot(path) == before


@pytest.mark.parametrize("step", range(LATEST_DATA_STEP + 1))
def test_supported_data_steps_still_open_and_preserve_history(tmp_path, step):
    path = tmp_path / "journal.db"
    with closing(Journal(path)) as journal:
        task = journal.submit(project="p", host="h1", workspace="w", original_words="Original words",
                              acceptance="preserved", idempotency_key="supported-step")
        journal.db.execute(f"PRAGMA user_version={step}")
    with closing(Journal(path)) as journal:
        assert journal.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP
        assert journal.get(task["task_id"])["original_words"] == "Original words"


def test_fresh_journal_still_initializes(tmp_path):
    with closing(Journal(tmp_path / "fresh" / "journal.db")) as journal:
        assert journal.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP
        assert journal.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert journal.db.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert journal.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert journal.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
