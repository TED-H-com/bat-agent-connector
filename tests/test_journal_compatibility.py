"""A rolled-back Connector must refuse a newer journal before any schema write."""

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

from bat_agent_connector import cli, managed_runtime, registry
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


@pytest.mark.parametrize("change", [
    {"error": "UNKNOWN_FAILURE"}, {"recorded_data_step": True}, {"recorded_data_step": -1},
    {"recorded_data_step": 2147483648}, {"supported_data_step": -1},
])
def test_managed_refusal_forwarding_rejects_unrecognized_diagnostics(tmp_path, change):
    failure = {"protocol": 1, "error": "JOURNAL_NEWER_DATA_STEP", "recorded_data_step": 4,
               "supported_data_step": LATEST_DATA_STEP, "message": "private-provider-diagnostic", **change}
    managed_runtime.atomic_write(tmp_path / "service.log", json.dumps(failure).encode())
    assert managed_runtime._journal_startup_failure(tmp_path) is None


def test_managed_refusal_forwarding_never_copies_arbitrary_child_text(tmp_path):
    failure = {"protocol": 1, "error": "JOURNAL_NEWER_DATA_STEP", "recorded_data_step": LATEST_DATA_STEP + 1,
               "supported_data_step": LATEST_DATA_STEP, "message": "private-provider-diagnostic"}
    managed_runtime.atomic_write(tmp_path / "service.log", json.dumps(failure).encode())
    error = managed_runtime._journal_startup_failure(tmp_path)
    assert_refusal(error)
    assert "private-provider-diagnostic" not in str(error)


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


def test_newer_step_committed_in_wal_is_refused_without_another_commit(tmp_path):
    path = tmp_path / "journal.db"
    with closing(Journal(path)) as writer:
        writer.db.execute(f"PRAGMA user_version={LATEST_DATA_STEP + 1}")
        before = snapshot(path)
        wal = path.with_name(path.name + "-wal")
        wal_before = wal.read_bytes()
        with pytest.raises(ValueError) as refused:
            with closing(Journal(path)):
                pass
        assert_refusal(refused.value)
        assert snapshot(path) == before
        assert wal.read_bytes() == wal_before


def test_refusing_a_crashed_newer_writer_does_not_checkpoint_its_wal(tmp_path):
    path = tmp_path / "journal.db"
    subprocess.run([sys.executable, "-c", """
import os, sys
from bat_agent_connector.task_journal import Journal, LATEST_DATA_STEP
journal = Journal(sys.argv[1])
journal.submit(project='p', host='h1', workspace='w', original_words='Preserve this history',
               acceptance='preserved', idempotency_key='crashed-newer-writer')
journal.db.execute(f'PRAGMA user_version={LATEST_DATA_STEP + 1}')
os._exit(0)  # Simulate a crash, leaving committed WAL frames without a live connection.
""", str(path)], check=True, timeout=10)
    before = snapshot(path)
    wal = path.with_name(path.name + "-wal")
    wal_before = wal.read_bytes()
    with pytest.raises(ValueError) as refused:
        with closing(Journal(path)):
            pass
    assert_refusal(refused.value)
    assert snapshot(path) == before
    assert wal.read_bytes() == wal_before


@pytest.mark.parametrize("command", ["serve", "ensure"])
def test_managed_startup_preserves_the_specific_safe_refusal(tmp_path, monkeypatch, capsys, command):
    root = tmp_path / "managed"
    managed_runtime._prepare(root)
    path = root / "state" / "tasks.sqlite3"
    before = newer_journal(path, "WAL")
    children = []
    spawn = managed_runtime._spawn

    def tracked_spawn(root):
        child = spawn(root)
        children.append(child)
        return child

    monkeypatch.setattr(managed_runtime, "_spawn", tracked_spawn)
    monkeypatch.setattr(sys, "argv", ["managed-runtime", command, "--data-dir", str(root)])
    environment_keys = ("BATC_STATE_DIR", "BATC_CONFIG_DIR", "BATC_CONFIG", "BATC_TASK_SETTINGS",
                        "BATC_TASK_ADMIN_TOKEN_FILE", "BATC_TASK_CAPABILITY", "BATC_TASK_URL",
                        "BATC_PM_PROVIDER_CONFIG", "BATC_DEVICE_ID")
    expected_environment = {key: os.environ.get(key) for key in environment_keys}
    try:
        # serve() normally runs in a dedicated process and intentionally changes
        # its environment. Bound those changes when exercising it in this process.
        with monkeypatch.context() as environment:
            for key in environment_keys:
                environment.setenv(key, expected_environment[key] or "")
            with pytest.raises(SystemExit) as refused:
                managed_runtime.main()
        assert all(os.environ.get(key) == value for key, value in expected_environment.items())
        assert refused.value.code == 1
        output = capsys.readouterr()
        failure = json.loads(output.err if command == "serve" else output.out)
        assert failure["error"] == "JOURNAL_NEWER_DATA_STEP"
        assert failure["recorded_data_step"] == LATEST_DATA_STEP + 1
        assert failure["supported_data_step"] == LATEST_DATA_STEP
        assert_refusal(failure["message"])
        assert str(root) not in json.dumps(failure)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)
        assert snapshot(path) == before
