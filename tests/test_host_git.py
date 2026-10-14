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


def test_binding_ddl_is_idempotent_without_a_data_step(tmp_path):
    journal = Journal(tmp_path / "bindings.sqlite3")
    try:
        version = journal.db.execute("PRAGMA user_version").fetchone()[0]
        host_git.install(journal)
        snapshot = list(journal.db.iterdump())
        host_git.install(journal)
        assert list(journal.db.iterdump()) == snapshot
        assert journal.db.execute("PRAGMA user_version").fetchone()[0] == version
    finally:
        journal.close()


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


def test_execution_metadata_is_separate_from_the_complete_task_incarnation():
    from bat_agent_connector.orchestration_operations import task_binding

    location = host_git.HostGitRunner(config(), {"fixture": "fixture-bat"}).execution_binding("fixture")
    task = {"task_id": "fixture-task", "control_version": 7, "host": "fixture", "session_id": "fixture-session",
            "role": "lead", "future_task_field": "must-remain-bound"}
    metadata = {"git_execution": {"fixture": location}, "controller_authority": {"actor": "fixture-operator"},
                "session_controller": {"controller_incarnation": "fixture-incarnation"}}
    assert task_binding({**task, **metadata}) == task
    assert task_binding(metadata) is None


def test_local_reader_binds_installation_server_transport_and_host():
    first = host_git.HostGitRunner(config("local"), {"fixture": "unused"}, installation_id="fixture-install", server_id="fixture-server")
    second = host_git.HostGitRunner(config("local"), {}, installation_id="other-install", server_id="fixture-server")
    third = host_git.HostGitRunner(config("local"), {}, installation_id="fixture-install", server_id="other-server")
    assert len({first.reader_binding("fixture"), second.reader_binding("fixture"), third.reader_binding("fixture"), GOLDEN}) == 4
    assert first.execution_binding("fixture")["host_identity"] == {"name": "fixture", "endpoint_digest": host_git._digest(BASE["url"]),
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


@pytest.mark.parametrize("field", ["installation_id", "server_id"])
async def test_locked_gate_rechecks_current_identity_after_await(monkeypatch, field):
    identity = {"installation_id": "fixture-install", "server_id": "fixture-server"}
    runner = host_git.HostGitRunner(config(), {"fixture": "fixture-bat"}, **identity,
                                   current_identity=lambda: dict(identity))
    original = runner.execution_binding("fixture")
    async def check():
        identity[field] = "different-identity"
    async def run(argv, timeout_s):
        await checkpoints._LOCKED_CHECK.get()()
        raise AssertionError("proceed must be denied after identity changes")
    monkeypatch.setattr(checkpoints, "_run", run)
    token = checkpoints._LOCKED_CHECK.set(check)
    try:
        with pytest.raises(NeedsAttention) as error:
            await runner.run("fixture", "fixed-locked-script", 25)
        assert error.value.code == "GIT_EXECUTION_LOCATION_CHANGED"
        assert runner.execution_binding("fixture") == original
        assert not runner.available("fixture")
    finally:
        checkpoints._LOCKED_CHECK.reset(token)


@pytest.mark.parametrize("change", ["installation_id", "server_id", "server_withdrawn"])
async def test_daemon_runner_rechecks_central_identity_without_reactivation(tmp_path, monkeypatch, change):
    from bat_agent_connector.task_daemon import TaskDaemon

    daemon = TaskDaemon(config(), tmp_path / "tasks.sqlite3")
    runner = daemon.ops.context["git_runner"]
    async def spawn(*args, **kwargs):
        raise AssertionError("identity drift must refuse before a subprocess")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    try:
        if change == "installation_id":
            daemon.managed_installation = {"installation_id": "different-installation"}
        else:
            with daemon.journal.tx():
                if change == "server_id":
                    daemon.journal.db.execute("UPDATE api_sync_metadata SET server_id='different-server'")
                else:
                    daemon.journal.db.execute("DELETE FROM api_sync_metadata")
        with pytest.raises(NeedsAttention) as error:
            await runner.run("fixture", "fixed-script")
        assert error.value.code == "GIT_EXECUTION_LOCATION_CHANGED"
        assert runner.status("fixture")["reason"] == "git_execution_location_changed"
        assert not runner.available("fixture")
    finally:
        await daemon.ops.drain()
        await daemon.fleet.close()
        await daemon.inventory.close()
        daemon.journal.close()


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
        return await ctx.step("git-effect", effect, request={"fixture_request": "preserve-exact"})
    op = create(ops, handler)
    location = op["external_refs"]["admission_binding"]["git_execution"]["fixture"]
    assert location["transport"] == "ssh" and location["server_id"] == "fixture-server"
    await ops._execute(op["operation_id"])
    step = ops.db.execute("SELECT request FROM operation_steps WHERE operation_id=?", (op["operation_id"],)).fetchone()
    assert json.loads(step["request"]) == {"fixture_request": "preserve-exact"}
    saved = ops.db.execute("SELECT binding FROM git_execution_steps WHERE operation_id=? AND name='git-effect'",
                           (op["operation_id"],)).fetchone()
    assert json.loads(saved[0])["fixture"] == location
    assert ops._row(op["operation_id"])["external_refs"] == op["external_refs"]
    assert len(calls) == 1
    # A persisted completed step remains readable without invoking a new runner.
    from bat_agent_connector.operations import OpContext
    ops.context["git_runner"] = host_git.HostGitRunner(config("local"), {})
    with host_git.execution(ops, op["operation_id"]):
        assert await handler(OpContext(ops, ops._row(op["operation_id"]))) == {"evidence": "fixed-evidence"}
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["step_disagrees", "admission_lost"])
async def test_step_location_fences_new_effects_while_completed_receipts_remain_readable(operations, change):
    from bat_agent_connector.operations import OpContext

    ops = operations
    calls = []
    async def handler(ctx):
        async def effect():
            calls.append("effect")
            return {"fixed": "receipt"}
        return await ctx.step("git-effect", effect)
    op = create(ops, handler)
    await ops._execute(op["operation_id"])
    refs = copy.deepcopy(ops._row(op["operation_id"])["external_refs"])
    with ops.journal.tx():
        if change == "admission_lost":
            del refs["admission_binding"]["git_execution"]
            ops.context["git_runner"] = host_git.HostGitRunner(config("local"), {})
        else:
            saved = ops.db.execute("SELECT binding FROM git_execution_steps WHERE operation_id=?",
                                   (op["operation_id"],)).fetchone()
            locations = json.loads(saved[0])
            locations["fixture"]["transport"] = "local"
            ops.db.execute("UPDATE git_execution_steps SET binding=? WHERE operation_id=?",
                           (json.dumps(locations), op["operation_id"]))
        ops.db.execute("UPDATE operations SET external_refs=? WHERE operation_id=?", (json.dumps(refs), op["operation_id"]))
    with host_git.execution(ops, op["operation_id"]):
        assert await handler(OpContext(ops, ops._row(op["operation_id"]))) == {"fixed": "receipt"}
        with pytest.raises(NeedsAttention) as error:
            host_git.require_execution("fixture")
        assert error.value.code == "GIT_EXECUTION_LOCATION_CHANGED"
    assert calls == ["effect"]


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
            ops.db.execute("DELETE FROM git_execution_steps WHERE operation_id=?", (op["operation_id"],))
    ops.context["git_runner"] = host_git.HostGitRunner(config("local"), {"fixture": "fixture-bat"})
    await ops._execute(op["operation_id"])
    result = ops.get(op["operation_id"])
    assert result["status"] == "needs_attention" and result["error_code"] == "GIT_EXECUTION_LOCATION_CHANGED"
    assert len(calls) == 1
    assert result["idempotency_key"] == "original-key"


def test_reserved_missing_helpers_projection_has_exact_names():
    error = host_git.GitRunnerUnavailable("local_git_tools_missing", missing_helpers=("git", "flock", "python3"))
    assert error.code == "GIT_RUNNER_UNAVAILABLE" and error.reason in host_git.LOCAL_GIT_REASONS
    assert error.details == {"missing_helpers": ["git", "flock", "python3"]}


@pytest.mark.parametrize("change", ["alias", "url", "fingerprint", "profile_id", "managed_roots", "installation_id", "server_id"])
async def test_configuration_digest_and_identity_drift_block_original_intent(operations, change):
    ops = operations
    async def unused(ctx):
        raise AssertionError("only admission is used")
    op = create(ops, unused)
    cfg, aliases = config(), {"fixture": "fixture-bat"}
    identities = {"server_id": "fixture-server"}
    if change == "alias":
        aliases["fixture"] = "different-alias"
    elif change in {"installation_id", "server_id"}:
        identities[change] = "different-identity"
    else:
        setattr(cfg.host("fixture"), change, {"url": "wss://fixture.example.invalid:9876/", "fingerprint": "bb" * 32,
            "profile_id": "other-profile", "managed_roots": ("/srv/other",)}[change])
    ops.context["git_runner"] = host_git.HostGitRunner(cfg, aliases, **identities)
    with host_git.execution(ops, op["operation_id"]):
        with pytest.raises(NeedsAttention) as error:
            host_git.require_execution("fixture")
    assert error.value.code == "GIT_EXECUTION_LOCATION_CHANGED"


async def test_ssh_missing_alias_retains_original_runner_error_before_process(monkeypatch):
    async def spawn(*args, **kwargs):
        raise AssertionError("missing alias must not spawn")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    runner = host_git.HostGitRunner(config(), {})
    assert runner.status("fixture") == {"available": False, "reason": "ssh_alias_missing", "details": {}}
    with pytest.raises(checkpoints.GitCommandFailed) as error:
        await runner.run("fixture", "fixed-script", 25)
    assert error.value.code == "GIT_FAILED"
    assert error.value.message == "no SSH alias is configured for host fixture"


def test_execution_binding_does_not_copy_secret_bearing_standalone_url():
    cfg = config()
    cfg.host("fixture").url = "wss://fixture-user:fixture-password@fixture.example.invalid:9876/?token=fixture-private"
    runner = host_git.HostGitRunner(cfg, {"fixture": "fixture-bat"})
    binding = runner.execution_binding("fixture")
    serialized = json.dumps(binding)
    for secret in ("fixture-password", "fixture-private", "?token=", "fixture-user"):
        assert secret not in serialized
    cfg.host("fixture").url = "wss://fixture.example.invalid:9876/"
    assert not runner.available("fixture")  # exact endpoint remains frozen


@pytest.mark.parametrize("legacy", [False, True])
async def test_unresolved_location_drift_precedes_domain_availability_refusal(operations, monkeypatch, legacy):
    from bat_agent_connector.operations import OperationError

    ops = operations
    calls = []
    async def run(argv, timeout_s):
        calls.append(argv)
        raise AmbiguousOutcome("fixture lost effect receipt")
    monkeypatch.setattr(checkpoints, "_run", run)
    async def handler(ctx):
        runner = ops.context["git_runner"]
        # Existing consumers can reject readiness before reaching runner.run.
        if not runner.available("fixture"):
            raise OperationError("GIT_RUNNER_UNAVAILABLE", "fixture domain refusal", 409)
        async def effect():
            return {"evidence": await runner.run("fixture", "fixed-script")}
        return await ctx.step("git-effect", effect)
    op = create(ops, handler)
    await ops._execute(op["operation_id"])
    assert ops.get(op["operation_id"])["status"] == "uncertain"
    if legacy:
        refs = copy.deepcopy(ops._row(op["operation_id"])["external_refs"])
        del refs["admission_binding"]["git_execution"]
        with ops.journal.tx():
            ops.db.execute("UPDATE operations SET external_refs=? WHERE operation_id=?", (json.dumps(refs), op["operation_id"]))
            ops.db.execute("DELETE FROM git_execution_steps WHERE operation_id=?", (op["operation_id"],))
    ops.context["git_runner"] = host_git.HostGitRunner(config("local"), {"fixture": "fixture-bat"})
    await ops._execute(op["operation_id"])
    result = ops.get(op["operation_id"])
    assert result["status"] == "needs_attention" and result["error_code"] == "GIT_EXECUTION_LOCATION_CHANGED"
    assert result["steps"][0]["status"] == "uncertain"
    assert result["idempotency_key"] == "original-key" and len(calls) == 1


async def test_legacy_ssh_recovery_ignores_unrelated_local_declaration(operations, monkeypatch):
    ops = operations
    calls = []
    async def run(argv, timeout_s):
        calls.append(argv)
        if len(calls) == 1:
            raise AmbiguousOutcome("fixture lost reply")
        return "fixed-receipt"
    monkeypatch.setattr(checkpoints, "_run", run)
    async def handler(ctx):
        async def effect():
            return {"evidence": await ops.context["git_runner"].run("fixture", "fixed-effect")}
        async def reconcile(request):
            return {"evidence": await ops.context["git_runner"].run("fixture", "fixed-readback")}
        return await ctx.step("git-effect", effect, reconcile=reconcile)
    op = create(ops, handler)
    await ops._execute(op["operation_id"])
    refs = copy.deepcopy(ops._row(op["operation_id"])["external_refs"])
    del refs["admission_binding"]["git_execution"]
    with ops.journal.tx():
        ops.db.execute("UPDATE operations SET external_refs=? WHERE operation_id=?", (json.dumps(refs), op["operation_id"]))
        ops.db.execute("DELETE FROM git_execution_steps WHERE operation_id=?", (op["operation_id"],))
    cfg = config()
    from dataclasses import replace
    cfg.hosts["unrelated"] = replace(cfg.host("fixture"), name="unrelated", git="local")
    ops.context["git_runner"] = host_git.HostGitRunner(cfg, {"fixture": "fixture-bat", "unrelated": "unused"})
    await ops._execute(op["operation_id"])
    result = ops.get(op["operation_id"])
    assert result["status"] == "succeeded" and result["result"] == {"evidence": "fixed-receipt"}
    assert len(calls) == 2 and all(argv[3] == "fixture-bat" for argv in calls)
    assert "fixed-effect" in calls[0][-1] and "fixed-readback" in calls[1][-1]
    assert result["idempotency_key"] == "original-key"
