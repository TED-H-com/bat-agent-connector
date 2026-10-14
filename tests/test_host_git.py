"""Disabled local transport, frozen intent and exact legacy SSH identity."""
from __future__ import annotations

import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from bat_agent_connector import api_auth, checkpoints, host_git
from bat_agent_connector import worktree_merge_operations as merge
from bat_agent_connector.config import parse_config
from bat_agent_connector.operations import ActionDef, AmbiguousOutcome, NeedsAttention, OperationService
from bat_agent_connector.task_journal import Journal

BASE = {"url": "wss://127.0.0.1:9876/", "fingerprint": "aa" * 32, "token_ref": "env:FIXTURE_TOKEN"}
GOLDEN = "0e3cc495ea380b792522b4be1296e22c8a6effe5fb8df967d9e2e96f10bec574"


def config(mode="ssh"):
    return parse_config({"hosts": {"fixture": {**BASE, "git": mode}}})


@pytest.mark.parametrize("mode,alias,expected", [("ssh", "fixture-bat", True), ("ssh", "", False),
                                                ("local", "fixture-bat", False), ("local", "", False)])
def test_logical_host_dispatch(mode, alias, expected):
    runner = host_git.HostGitRunner(config(mode), {"fixture": alias, "unknown": "fixture-other"})
    assert runner.available("fixture") is expected
    assert not runner.available("unknown")
    assert not runner.available("127.0.0.1")
    assert runner.aliases == {"fixture": alias, "unknown": "fixture-other"}


@pytest.mark.parametrize("platform,reason", [("linux", "local_git_not_enabled"), ("darwin", "local_git_not_enabled"),
                                           ("win32", "local_git_platform_unsupported")])
async def test_local_refuses_before_any_process_and_never_uses_alias(monkeypatch, platform, reason):
    monkeypatch.setattr(host_git.sys, "platform", platform)
    calls = []
    async def spawn(*args, **kwargs):
        calls.append(args)
        raise AssertionError("local dispatch must not spawn")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    runner = host_git.HostGitRunner(config("local"), {"fixture": "fixture-bat"})
    assert runner.status("fixture") == {"available": False, "reason": reason, "details": {}}
    for _ in range(3):
        assert not runner.available("fixture")
        with pytest.raises(host_git.GitRunnerUnavailable) as error:
            await runner.run("fixture", "must never execute", 25)
        assert error.value.code == "GIT_RUNNER_UNAVAILABLE"
        assert error.value.reason == reason
    assert calls == []


async def test_ssh_delegates_script_deadline_and_exception_unchanged(monkeypatch):
    calls = []
    async def run(argv, timeout_s):
        calls.append((argv, timeout_s))
        if len(calls) == 2:
            raise AmbiguousOutcome("fixture lost reply")
        return "exact structured bytes"
    monkeypatch.setattr(checkpoints, "_run", run)
    runner = host_git.HostGitRunner(config(), {"fixture": "fixture-bat"})
    assert await runner.run("fixture", "printf 'fixture'", 25) == "exact structured bytes"
    with pytest.raises(AmbiguousOutcome):
        await runner.run("fixture", "printf 'fixture'", 60)
    assert calls == [(("ssh", "-o", "BatchMode=yes", "fixture-bat", "sh -lc 'printf '\"'\"'fixture'\"'\"''"), 25),
                     (("ssh", "-o", "BatchMode=yes", "fixture-bat", "sh -lc 'printf '\"'\"'fixture'\"'\"''"), 60)]


async def test_unknown_host_refuses_before_process(monkeypatch):
    async def spawn(*args, **kwargs):
        raise AssertionError("unknown host must not spawn")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    runner = host_git.HostGitRunner(config(), {"unknown": "fixture-other"})
    with pytest.raises(host_git.GitRunnerUnavailable) as error:
        await runner.run("unknown", "unused")
    assert error.value.reason == "host_unknown"


def test_readiness_computed_once_off_event_loop(monkeypatch):
    calls = []
    original = host_git._readiness
    def readiness(*args):
        calls.append(threading.get_ident())
        return original(*args)
    monkeypatch.setattr(host_git, "_readiness", readiness)
    runner = host_git.HostGitRunner(config("local"), {})
    for _ in range(10):
        assert not runner.available("fixture")
        runner.status("fixture")
    assert len(calls) == 1 and calls[0] != threading.get_ident()


def test_golden_legacy_reader_binding():
    # Computed before edits from origin/main dd7b62aa's actual _reader_binding
    # and _digest functions with SshGitRunner({'fixture': 'fixture-bat'}).
    old = checkpoints.SshGitRunner({"fixture": "fixture-bat"})
    new = host_git.HostGitRunner(config(), {"fixture": "fixture-bat"})
    for runner in (old, new):
        assert merge._reader_binding(SimpleNamespace(context={"git_runner": runner}), "fixture") == GOLDEN


def test_local_reader_binds_installation_server_transport_and_host():
    first = host_git.HostGitRunner(config("local"), {"fixture": "unused"}, installation_id="fixture-install", server_id="fixture-server")
    second = host_git.HostGitRunner(config("local"), {}, installation_id="other-install", server_id="fixture-server")
    third = host_git.HostGitRunner(config("local"), {}, installation_id="fixture-install", server_id="other-server")
    assert len({first.reader_binding("fixture"), second.reader_binding("fixture"), third.reader_binding("fixture"), GOLDEN}) == 4
    assert first.execution_binding("fixture")["host_identity"] == {"name": "fixture", "url": BASE["url"],
        "fingerprint": BASE["fingerprint"].upper(), "profile_id": "default"}
    assert first.reader_binding("fixture") == host_git.HostGitRunner(config("local"), {"fixture": "another-unused"},
        installation_id="fixture-install", server_id="fixture-server").reader_binding("fixture")


@pytest.mark.parametrize("mode", ["ssh", "local"])
async def test_account_check_only_uses_explicit_trusted_auditor(monkeypatch, mode):
    cfg = config(mode)
    cfg.host("fixture").confinement = {"check_ssh_alias": "fixture-auditor"}
    calls = []
    async def run(argv, timeout_s):
        calls.append((argv, timeout_s))
        return '{"status":"unknown"}'
    monkeypatch.setattr(checkpoints, "_run", run)
    runner = host_git.HostGitRunner(cfg, {"fixture": "fixture-bat"})
    assert await runner.run_account_check("fixture", "fixed-audit", 12, ssh_alias="fixture-auditor") == '{"status":"unknown"}'
    for alias in ("", "fixture-bat", "another-auditor"):
        with pytest.raises(host_git.GitRunnerUnavailable):
            await runner.run_account_check("fixture", "fixed-audit", ssh_alias=alias)
    absent = host_git.HostGitRunner(config(mode), {"fixture": "fixture-bat"})
    with pytest.raises(host_git.GitRunnerUnavailable):
        await absent.run_account_check("fixture", "fixed-audit", ssh_alias="fixture-bat")
    assert calls == [(("ssh", "-o", "BatchMode=yes", "fixture-auditor", "fixed-audit"), 12)]


def test_standalone_mutation_of_bound_config_fails_closed():
    cfg = config()
    runner = host_git.HostGitRunner(cfg, {"fixture": "fixture-bat"})
    cfg.host("fixture").git = "local"
    assert not runner.available("fixture")
    assert runner.status("fixture")["reason"] == "git_execution_location_changed"
    assert runner.execution_binding("fixture")["transport"] == "ssh"


async def test_locked_gate_rechecks_config_after_await(monkeypatch):
    cfg = config()
    runner = host_git.HostGitRunner(cfg, {"fixture": "fixture-bat"})
    async def check():
        cfg.host("fixture").git = "local"
    async def run(argv, timeout_s):
        await checkpoints._LOCKED_CHECK.get()()
        raise AssertionError("proceed must be denied")
    monkeypatch.setattr(checkpoints, "_run", run)
    token = checkpoints._LOCKED_CHECK.set(check)
    try:
        with pytest.raises(NeedsAttention) as error:
            await runner.run("fixture", "fixed-locked-script", 25)
        assert error.value.code == "GIT_EXECUTION_LOCATION_CHANGED"
    finally:
        checkpoints._LOCKED_CHECK.reset(token)


@pytest.fixture
async def operations(tmp_path):
    journal = Journal(tmp_path / "journal" / "operations.sqlite3")
    ops = OperationService(journal)
    ops.context["git_runner"] = host_git.HostGitRunner(config(), {"fixture": "fixture-bat"}, server_id="fixture-server")
    yield ops
    await ops.drain()
    journal.close()


def create(ops, fn):
    ops.register(ActionDef("fixture.git", "manage", "Fixture Git effect", fn))
    return ops.create(api_auth.Principal("fixture-operator", frozenset({"manage"})), action="fixture.git",
                      target={"host": "fixture"}, idempotency_key="original-key")[0]


async def test_new_admission_and_step_bind_location_and_replay_completed_receipt(operations, monkeypatch):
    ops = operations
    calls = []
    async def run(argv, timeout_s):
        calls.append(argv)
        return "fixed-evidence"
    monkeypatch.setattr(checkpoints, "_run", run)
    async def handler(ctx):
        async def effect():
            return {"evidence": await ops.context["git_runner"].run("fixture", "fixed-script")}
        return await ctx.step("git-effect", effect)
    op = create(ops, handler)
    location = op["external_refs"]["admission_binding"]["git_execution"]["fixture"]
    assert location["transport"] == "ssh" and location["server_id"] == "fixture-server"
    await ops._execute(op["operation_id"])
    step = ops.db.execute("SELECT request FROM operation_steps WHERE operation_id=?", (op["operation_id"],)).fetchone()
    assert json.loads(step["request"])["git_execution"]["fixture"] == location
    assert len(calls) == 1
    # A persisted completed step remains readable without invoking a new runner.
    from bat_agent_connector.operations import OpContext
    ops.context["git_runner"] = host_git.HostGitRunner(config("local"), {})
    assert await handler(OpContext(ops, ops._row(op["operation_id"]))) == {"evidence": "fixed-evidence"}
    assert len(calls) == 1


@pytest.mark.parametrize("legacy", [False, True])
async def test_unresolved_ssh_never_redirects_to_local_after_reload(operations, monkeypatch, legacy):
    ops = operations
    calls = []
    async def run(argv, timeout_s):
        calls.append(argv)
        raise AmbiguousOutcome("fixture lost reply")
    monkeypatch.setattr(checkpoints, "_run", run)
    async def handler(ctx):
        async def effect():
            return {"evidence": await ops.context["git_runner"].run("fixture", "fixed-script")}
        async def reconcile(request):
            return {"evidence": await ops.context["git_runner"].run("fixture", "fixed-readback")}
        return await ctx.step("git-effect", effect, reconcile=reconcile)
    op = create(ops, handler)
    await ops._execute(op["operation_id"])
    assert ops.get(op["operation_id"])["status"] == "uncertain"
    assert len(calls) == 1 and calls[0][3] == "fixture-bat"
    if legacy:
        refs = copy.deepcopy(ops._row(op["operation_id"])["external_refs"])
        del refs["admission_binding"]["git_execution"]
        with ops.journal.tx():
            ops.db.execute("UPDATE operations SET external_refs=? WHERE operation_id=?", (json.dumps(refs), op["operation_id"]))
    ops.context["git_runner"] = host_git.HostGitRunner(config("local"), {"fixture": "fixture-bat"})
    await ops._execute(op["operation_id"])
    result = ops.get(op["operation_id"])
    assert result["status"] == "needs_attention" and result["error_code"] == "GIT_EXECUTION_LOCATION_CHANGED"
    assert len(calls) == 1
    assert result["idempotency_key"] == "original-key"
