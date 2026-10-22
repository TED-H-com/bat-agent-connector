"""A10: options and evidence are checked without writing to a person's folder; W12 proves enforcement."""
from __future__ import annotations

import copy
import json
import os
import pathlib
import sys
import time
import types

import pytest

from bat_agent_connector import confinement, lifecycle, orchestrate, registry, service
from bat_agent_connector.config import parse_config
from bat_agent_connector.errors import ConfigError
from bat_agent_connector.task_journal import LATEST_DATA_STEP, Journal
from tests.conftest import adopt
from tests.test_journal_compatibility import snapshot as journal_snapshot

MANAGED = {"managed_roots": ["/srv/demo"], "legacy_shared_worktrees": True}


@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize("policy", ["default", "allow_all", "confined"])
async def test_a10_general_start_preserves_operator_policy_and_records_evidence(fleet_factory, mock, agent, policy):
    f = fleet_factory(writes=True, orchestrate=True, default_permission_mode=policy,
                      safety={"write_min_interval_s": 0}, **MANAGED)
    r = await orchestrate.session_start(f, "h1", "demo-project", agent, confirm=True)
    options = [i["params"]["options"] for i in mock.invokes if i["channel"] == "claude:start-session"][0]
    expected = confinement.policy_options(agent, policy)
    assert {k: options[k] for k in confinement.OPTION_KEYS if k in options} == expected
    assert r["write_scope"] == ("confined" if policy == "confined" else None)
    record = r["confinement"]
    assert record["level"] == ("none" if policy == "allow_all" else
                               "prompt_gated" if agent == "claude" else "os_sandbox")
    assert record["verification"]["status"] == "options_confirmed"
    if agent == "codex" and policy != "allow_all":
        assert record["gap"] == "sandbox_enforcement_unverified"
    read = await service.session_read(f, "h1", r["session_id"])
    assert read["confinement"] == record and read["current_verification"]["status"] == "options_confirmed"
    await f.close()


class AccountRunner:
    def __init__(self, status="verified"):
        self.status, self.scripts = status, []

    def available(self, host):
        return True

    async def run_account_check(self, host, script, timeout_s=None, *, ssh_alias):
        self.scripts.append(script)
        assert ssh_alias == ACCOUNT["check_ssh_alias"]
        return json.dumps(account_observation(self.status, "read_only_account_check"))


ACCOUNT = {"host_account": True, "expected_uid": 2001, "protected_roots": ["/srv/personal"],
           "check_ssh_alias": "fixture-auditor", "check_uid": 2002, "bat_account": "fixture-bat"}


def account_observation(status, reason):
    return {"status": status, "reason": reason, "checked_uid": ACCOUNT["expected_uid"],
            "channel": {"status": "verified", "method": "sudo_exec", "ssh_alias": ACCOUNT["check_ssh_alias"],
                        "auditor_uid": ACCOUNT["check_uid"], "bat_uid": ACCOUNT["expected_uid"],
                        "bat_account": ACCOUNT["bat_account"], "closure": closure_observation()}}


def closure_observation(remaining=9000):
    return {"schema_version": 1, "status": "proven", "interpreter": "/usr/bin/python3.10",
            "roots": ["/usr/lib/python3.10"], "entries_remaining": remaining}


@pytest.mark.parametrize("status,reason,declared,effect", [
    ("verified", "read_only_account_check", True, "verified"),
    ("unknown", "check_executable_untrusted", True, "fallback_default"),
    ("unknown", "login_environment_writable", True, "fallback_default"),
    ("unknown", "login_shell_unsupported", True, "fallback_default"),
    ("unknown", "check_channel_untrusted", True, "fallback_default"),
    ("mismatch", "protected_root_writable", True, "refused"),
    ("unknown", "ssh_alias_unavailable", True, "refused"),
    ("unknown", "unchecked_or_stale", False, "fallback_default"),
])
async def test_a10_account_start_effect_and_gate_agree(fleet_factory, status, reason, declared, effect):
    f = fleet_factory(confinement=ACCOUNT if declared else {})

    class Runner(AccountRunner):
        async def run_account_check(self, host, script, timeout_s=None, *, ssh_alias):
            self.scripts.append(script)
            return json.dumps(account_observation(status, reason))

    f.confinement_runner = Runner()
    try:
        # Seed a fresh cache using the actual checker; undeclared hosts skip it.
        result = await confinement.check_account(f, "h1")
        projection = confinement.host_capability(f, "h1")["host_account"]
        assert projection["start_effect"] == confinement.account_start_effect(result) == effect
        calls = len(f.confinement_runner.scripts)
        if effect == "refused":
            with pytest.raises(confinement.ConfinementRefused, match="HOST_ACCOUNT_UNVERIFIED") as error:
                await confinement.start_account(f, "h1")
            assert error.value.code == "HOST_ACCOUNT_UNVERIFIED" and error.value.sent is False
        else:
            account = await confinement.start_account(f, "h1")
            assert confinement.account_start_effect(account) == effect
            opts, _, _ = await confinement.start_decision(f, "h1", "claude", confined=True)
            assert opts == {"permissionMode": "acceptEdits" if effect == "verified" else "default"}
        assert len(f.confinement_runner.scripts) > calls if declared else not f.confinement_runner.scripts
    finally:
        await f.close()


@pytest.mark.parametrize("cached", [False, True], ids=["unchecked", "stale"])
async def test_a10_recheck_passes_live_and_confined_claude_uses_accept_edits(fleet_factory, mock, cached):
    f = fleet_factory(writes=True, orchestrate=True, confinement={**ACCOUNT, "check_max_age_s": 45},
                      safety={"write_min_interval_s": 0}, **MANAGED)
    f.confinement_runner = AccountRunner()
    if cached:
        await confinement.check_account(f, "h1")
        f._confinement_checks["h1"]["checked_at"] = time.time() - 46
        f.confinement_runner.scripts.clear()
    try:
        projection = confinement.host_capability(f, "h1")["host_account"]
        assert projection["start_effect"] == "recheck" and projection["reason"] == "unchecked_or_stale"
        assert not f.confinement_runner.scripts
        result = await orchestrate.session_start(f, "h1", "demo-project", confirm=True, write_scope="confined")
        assert result["confinement"]["options"] == {"permissionMode": "acceptEdits"}
        starts = [i for i in mock.invokes if i["channel"] == "claude:start-session"]
        assert len(starts) == 1 and starts[0]["params"]["options"]["permissionMode"] == "acceptEdits"
        assert len(f.confinement_runner.scripts) == 2  # fresh admission, then frame check
        assert confinement.host_capability(f, "h1")["host_account"]["start_effect"] == "verified"
    finally:
        await f.close()


async def test_a10_accept_edits_requires_verified_account_and_checks_are_read_only(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, confinement=ACCOUNT, **MANAGED)
    f.confinement_runner = AccountRunner()
    r = await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True, write_scope="confined")
    assert r["confinement"]["options"] == {"permissionMode": "acceptEdits"}
    assert r["confinement"]["level"] == "host_account"
    assert r["confinement"]["verification"]["status"] == "verified"
    script = f.confinement_runner.scripts[0]
    assert "-xdev" in script and "-writable" in script and "/proc" in script
    assert "/usr/bin/sudo" in script and "write_text" not in script
    assert "'sudo', '-i'" not in script and "'sudo', '-s'" not in script
    await f.close()


@pytest.mark.parametrize("mode", ["plan", "dontAsk"])
@pytest.mark.parametrize("account", [False, True])
async def test_a10_confined_start_preserves_stronger_explicit_claude_mode(fleet_factory, mock, mode, account):
    f = fleet_factory(writes=True, orchestrate=True, default_permission_mode="confined",
                      confinement=ACCOUNT if account else {}, **MANAGED)
    if account:
        f.confinement_runner = AccountRunner()
    r = await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True, permission_mode=mode)
    assert r["confinement"]["options"] == {"permissionMode": mode}
    assert r["write_scope"] == "confined"
    await f.close()


@pytest.mark.parametrize("status", ["unknown", "mismatch"])
async def test_a10_declared_unverified_account_blocks_new_start(fleet_factory, mock, status):
    f = fleet_factory(writes=True, orchestrate=True, confinement=ACCOUNT, **MANAGED)
    f.confinement_runner = AccountRunner(status)
    with pytest.raises(confinement.ConfinementRefused, match="HOST_ACCOUNT_UNVERIFIED"):
        await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True)
    assert "claude:start-session" not in mock.channels() and "worktree:create" not in mock.channels()
    await f.close()


async def test_a10_host_account_cache_expires_without_upgrading_creation(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, confinement=ACCOUNT, **MANAGED)
    f.confinement_runner = AccountRunner()
    r = await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True, write_scope="confined")
    original = copy.deepcopy(r["confinement"])
    f._confinement_checks["h1"]["checked_at"] = time.time() - 1000
    read = await service.session_read(f, "h1", r["session_id"])
    assert read["confinement"] == original and read["current_verification"]["status"] == "unknown"
    assert read["current_verification"]["reason"] == "host_account_unverified"
    await f.close()


async def test_a10_closed_host_evidence_journal_returns_unknown(fleet_factory, tmp_path):
    f = fleet_factory(confinement=ACCOUNT)
    journal = Journal(tmp_path / "tasks.db")
    f.confinement_journal = journal
    journal.close()
    try:
        evidence = confinement.host_capability(f, "h1")["host_account"]
        assert evidence["declared"] and evidence["status"] == "unknown"
        assert evidence["checked_at"] is None
    finally:
        await f.close()


@pytest.mark.parametrize("force", [False, True])
async def test_a10_raise_and_deferred_raise_are_refused(fleet_factory, mock, force):
    f = fleet_factory(writes=True, default_permission_mode="allow_all", **MANAGED)
    adopt("sess-claude-0001", write_scope="confined", permission_raise_pending="allow_all")
    with pytest.raises(confinement.ConfinementRefused, match="CONFINEMENT_RAISE_REFUSED"):
        await lifecycle.session_set_permissions(f, "h1", "sess-claude-0001", confirm=True, force=force)
    dry = await lifecycle._raise_deferred(f, "h1", dry_run=True)
    assert dry[0]["error_code"] == "LEGACY_PERMISSION_RAISE_DISABLED"
    actual = await lifecycle._raise_deferred(f, "h1", dry_run=False)
    assert actual[0]["error_code"] == "LEGACY_PERMISSION_RAISE_DISABLED"
    assert registry.get("h1", "sess-claude-0001")["permission_raise_pending"] == "allow_all"
    assert not mock.perm_calls
    await f.close()


async def test_a10_loaded_legacy_send_works_but_missing_evidence_resume_is_blocked(fleet_factory, mock):
    f = fleet_factory(writes=True, safety={"write_min_interval_s": 0}, **MANAGED)
    adopt("sess-claude-0001", write_scope="confined")
    r = await service.session_send(f, "h1", "sess-claude-0001", "Continue", confirm=True)
    assert r["accepted"]
    mock.metas.pop("sess-claude-0001")
    with pytest.raises(confinement.ConfinementRefused, match="CONFINEMENT_EVIDENCE_MISSING"):
        await service.session_send(f, "h1", "sess-claude-0001", "Continue", confirm=True)
    assert "claude:client-resume" not in mock.channels()
    await f.close()


async def test_a10_resume_uses_original_options_not_stale_tab(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, tabs=True, default_permission_mode="allow_all",
                      safety={"write_min_interval_s": 0}, **MANAGED)
    r = await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True, write_scope="confined")
    sid = r["session_id"]
    mock.metas.pop(sid)
    for terminal in mock.ws_doc["terminals"]:
        if terminal["id"] == sid:
            terminal["permissionMode"] = "bypassPermissions"
    await service.session_send(f, "h1", sid, "Continue", confirm=True)
    resume = [i for i in mock.invokes if i["channel"] == "claude:client-resume"][-1]
    assert resume["params"]["options"]["permissionMode"] == "default"
    await f.close()


@pytest.mark.parametrize("tool,dont", [("Bash", True), ("ExitPlanMode", False), ("ExitPlanMode", True)])
async def test_a10_persistent_and_exit_plan_approvals_are_refused(fleet_factory, mock, tool, dont):
    f = fleet_factory(writes=True, **MANAGED)
    adopt("sess-claude-0001", write_scope="confined")
    mock.states["sess-claude-0001"]["pendingPermission"] = {"toolUseId": "test", "toolName": tool, "input": {}}
    with pytest.raises(confinement.ConfinementRefused, match="CONFINEMENT_RAISE_REFUSED"):
        await service.session_answer(f, "h1", "sess-claude-0001", permission="allow",
                                     dont_ask_again=dont, confirm=True)
    assert "claude:resolve-permission" not in mock.channels()
    await f.close()


async def test_a10_creation_snapshot_survives_runtime_drift_and_send_refuses(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0}, **MANAGED)
    r = await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True, write_scope="confined")
    mock.metas[r["session_id"]]["permissionMode"] = "acceptEdits"
    read = await service.session_read(f, "h1", r["session_id"])
    assert read["confinement"] == r["confinement"] and read["current_verification"]["status"] == "mismatch"
    with pytest.raises(confinement.ConfinementRefused, match="CONFINEMENT_MISMATCH"):
        await service.session_send(f, "h1", r["session_id"], "Continue", confirm=True)
    await f.close()


async def test_a10_planner_is_read_only_never_and_successor_preserves_limits(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, default_permission_mode="allow_all", **MANAGED)
    r = await orchestrate.session_start(f, "h1", "demo-project", "codex", confirm=True, confinement_role="planner")
    previous = registry.get("h1", r["session_id"])
    opts, scope, record = await confinement.start_decision(f, "h1", "codex", confined=True, predecessor=previous)
    assert r["confinement"]["options"] == opts == {"codexSandboxMode": "read-only", "codexApprovalPolicy": "never"}
    assert scope == "confined" and record["evidence"]["inherited_from"]["session_id"] == r["session_id"]
    await f.close()


async def test_a10_task_service_keeps_engine_policy_and_reports_gap(fleet_factory):
    for policy in ("allow_all", "confined"):
        f = fleet_factory(writes=True, default_permission_mode=policy)
        options, scope, record = await confinement.start_decision(f, "h1", "codex", task=True)
        assert options == confinement.policy_options("codex", "allow_all" if policy == "allow_all" else "default")
        assert scope is None and record["gap"] == "task_recipe_compatibility"
        await f.close()


def test_a10_host_account_configuration_rejects_uncheckable_claims():
    for config in ({"host_account": True}, {**ACCOUNT, "expected_uid": 0},
                   {**ACCOUNT, "protected_roots": ["/srv/demo"]}, {"sandbox_evidence_file": "/tmp/proof"},
                   {**ACCOUNT, "check_uid": 2001}, {**ACCOUNT, "check_ssh_alias": "-invalid"},
                   {**ACCOUNT, "bat_account": "-invalid"}, {**ACCOUNT, "check_uid": None}):
        with pytest.raises(ConfigError):
            parse_config({"hosts": {"h1": {"url": "wss://example.invalid", "fingerprint": "a" * 64, "token_ref": "env:EXAMPLE", "managed_roots": ["/srv/demo"],
                                          "confinement": config}}})


def test_a10_host_evidence_migration_is_idempotent_and_persists(tmp_path):
    journal = Journal(tmp_path / "tasks.db")
    journal.db.execute("INSERT INTO confinement_host_checks VALUES(?,?)", ("h1", '{"status":"unknown"}'))
    version = journal.db.execute("PRAGMA user_version").fetchone()[0]
    journal.close()
    journal = Journal(tmp_path / "tasks.db")
    assert journal.db.execute("SELECT evidence FROM confinement_host_checks").fetchone()[0] == '{"status":"unknown"}'
    assert journal.db.execute("PRAGMA user_version").fetchone()[0] == max(version, LATEST_DATA_STEP)
    journal.close()


@pytest.mark.parametrize("version", [2, 3, 17])
def test_host_check_table_is_created_without_consuming_schema_version(tmp_path, version):
    """Additive DDL preserves supported steps; unknown future steps must refuse before DDL."""
    path = tmp_path / "tasks.db"
    journal = Journal(path)
    journal.db.execute("DROP TABLE confinement_host_checks")
    journal.db.execute(f"PRAGMA user_version={version}")
    journal.close()
    if version > LATEST_DATA_STEP:
        before = journal_snapshot(path)
        with pytest.raises(ValueError, match="JOURNAL_NEWER_DATA_STEP"):
            Journal(path)
        assert journal_snapshot(path) == before
        return
    journal = Journal(path)
    assert journal.db.execute("SELECT count(*) FROM confinement_host_checks").fetchone()[0] == 0
    assert journal.db.execute("PRAGMA user_version").fetchone()[0] == max(version, LATEST_DATA_STEP)
    journal.close()


@pytest.mark.parametrize("case,expected", [("safe", "verified"), ("writable", "mismatch"),
                                          ("budget", "unknown"), ("identity", "unknown"),
                                          ("capabilities", "mismatch"), ("ancestor", "mismatch"),
                                          ("idle", "verified"), ("permitted", "mismatch"), ("ambient", "mismatch"),
                                          ("time", "unknown"), ("process", "unknown"), ("symlink", "unknown"),
                                          ("ownership", "mismatch"), ("ancestor_owner", "mismatch")])
def test_a10_linux_read_only_scan_uses_find_and_process_identity(tmp_path, monkeypatch, capsys, case, expected):
    """Use real GNU find on a disposable fixture; simulate /proc, alias and ancestor metadata only."""
    root = tmp_path / "protected"
    root.mkdir()
    (root / "file").write_text("fixture")
    (root / "file").chmod(0o600 if case == "writable" else 0o444)
    if case == "symlink":
        (root / "link").symlink_to(root / "file")
    root.chmod(0o555)
    real_path = pathlib.Path
    actual_uid = os.getuid()
    uid = actual_uid if case in {"ownership", "ancestor_owner"} else actual_uid + 1000
    monkeypatch.setattr(os, "getuid", lambda: uid)
    monkeypatch.setattr(os, "geteuid", lambda: uid)
    gid = os.getgid()
    groups = " ".join(str(g) for g in sorted(os.getgroups()))
    def status(parent):
        return (f"PPid:\t{parent}\nUid:\t{uid} {uid} {uid} {uid}\n"
                            f"Gid:\t{gid} {gid} {gid} {gid}\nGroups:\t{groups}\n"
                            f"CapEff:\t{'1' if case == 'capabilities' else '0'}\n"
                            f"CapPrm:\t{'1' if case == 'permitted' else '0'}\n"
                            f"CapAmb:\t{'1' if case == 'ambient' else '0'}\n")

    class Proc:
        def __init__(self, *parts):
            self.parts = parts
            self.name = parts[-1]
        def __truediv__(self, part):
            return Proc(*self.parts, part)
        def read_text(self):
            if self.parts == ('/proc/self/maps',):
                return '1-2 r-xp 0 0:0 1 /usr/lib/fixture-native.so\n'
            if self.name in {"tcp", "tcp6"}:
                return "header\n0: 0100007F:2694 0:0 0A 0 0 0 0 0 101\n"
            return status(1 if self.parts[1] == "2" else 0)
        def read_bytes(self):
            return b"unidentified" if case == "process" else (
                b"bat-server" if self.parts[1] == "1" else b"codex")
        def iterdir(self):
            return ([Proc("/proc", "1")] if case == "idle" else [Proc("/proc", "1"), Proc("/proc", "2")]) \
                if self.parts == ("/proc",) else [Proc("fd")]

    class TrustedPath(type(real_path("/"))):
        def lstat(self):
            try:
                values = list(super().lstat())
            except FileNotFoundError:
                values = [0o100555, 1, 1, 1, 0, 0, 0, 0, 0, 0]
            values[4] = 0  # Synthetic root-owned bootstrap layout, independent of the container image.
            values[0] &= ~0o022
            return os.stat_result(values)

        def stat(self, **kwargs):
            values = list(super().stat(**kwargs))
            values[4] = 0
            return os.stat_result(values)

        def resolve(self, strict=False):
            return self

    def path(*parts):
        if str(parts[0]).startswith("/proc"):
            return Proc(*parts)
        return (TrustedPath if str(parts[0]).startswith(("/usr", "/bin")) else real_path)(*parts)

    real_readlink = os.readlink
    monkeypatch.setattr(os, "readlink", lambda p, **k: "socket:[101]" if isinstance(p, Proc) else real_readlink(p, **k))
    monkeypatch.setattr(os, "access", lambda p, *a, **k: case == "ancestor" and str(p) == str(tmp_path))
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr('sys.argv', ["check", json.dumps({"uid": uid + 1 if case == "identity" else uid,
                                                        "roots": [str(root)], "entries": 1 if case == "budget" else 1000,
                                                        "seconds": .000001 if case == "time" else 5, "port": 9876,
                                                        "channel": {"status": "verified", "auditor_uid": uid + 1}})])
    # Isolate the program's import: replacing the real Path breaks Path.__new__
    # and pytest's failure reporting on Python 3.10/3.11.
    with monkeypatch.context() as program_imports:
        program_imports.setitem(sys.modules, "pathlib", types.SimpleNamespace(Path=path))
        program_imports.setitem(sys.modules, "pwd", types.SimpleNamespace(getpwuid=lambda _: types.SimpleNamespace(
            pw_dir="/usr", pw_shell="/bin/sh")))
        real_subprocess = __import__('subprocess')
        def run(args, **kwargs):
            if args[0] == '/usr/bin/timeout':
                proof = closure_observation(max(0, int(args[-1])-1))
                proof.update(interpreter='/usr/bin/python3.14', roots=['/usr/lib/python3.14'])
                return types.SimpleNamespace(returncode=0, stdout=json.dumps(proof).encode(), stderr=b'')
            return types.SimpleNamespace(returncode=0, stdout=b'', stderr=b'')  # Synthetic bootstrap metadata.
        program_imports.setitem(sys.modules, "subprocess", types.SimpleNamespace(
            run=run, Popen=real_subprocess.Popen, PIPE=real_subprocess.PIPE, TimeoutExpired=real_subprocess.TimeoutExpired))
        with pytest.raises(SystemExit):
            exec(confinement._ACCOUNT_PROGRAM, {})
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == expected, result
    if case == "idle":
        assert result["runtimes"] == [] and result["limits"] == ["runtime_identity_inherited_unobserved"]
        record = confinement.snapshot("claude", {"permissionMode": "acceptEdits"}, account=result)
        assert "runtime_identity_inherited_unobserved" in record["limits"]
    if case == "ancestor_owner":
        assert result["reason"] == "owned_ancestor_can_chmod"
    root.chmod(0o700)
    (root / "file").chmod(0o600)


@pytest.mark.parametrize("agent,options,level", [
    ("claude", {"permissionMode": "acceptEdits"}, "none"),
    ("claude", {"permissionMode": "bypassPermissions"}, "none"),
    ("claude", {"permissionMode": "default"}, "prompt_gated"),
    ("codex", {"codexSandboxMode": "workspace-write", "codexApprovalPolicy": "on-request"}, "os_sandbox"),
])
def test_a10_level_requires_options_not_cwd(agent, options, level):
    record = confinement.confirm(confinement.snapshot(agent, options), {"cwd": "/same", **options})
    assert record["level"] == level and record["verification"]["status"] != "verified"
    assert confinement.verify(record, {"cwd": "/same"})["status"] == "unknown"


async def test_a10_partial_permission_ack_preserves_creation_and_reports_drift(fleet_factory, mock):
    from bat_agent_connector.errors import InvokeTimeout
    f = fleet_factory(writes=True, orchestrate=True, default_permission_mode="allow_all",
                      safety={"write_min_interval_s": 0}, **MANAGED)
    r = await orchestrate.session_start(f, "h1", "demo-project", "codex", confirm=True)
    sid = r["session_id"]
    client = f.client("h1")
    real = client.invoke

    async def lost_second_reply(channel, params=None, **kw):
        result = await real(channel, params, **kw)
        if channel == "claude:set-codex-approval-policy":
            raise InvokeTimeout("lost permission acknowledgement")
        return result

    client.invoke = lost_second_reply
    with pytest.raises(InvokeTimeout):
        await lifecycle.session_set_permissions(f, "h1", sid, "default", confirm=True)
    read = await service.session_read(f, "h1", sid)
    assert read["confinement"] == r["confinement"] and read["current_verification"]["status"] == "mismatch"
    # Retry after a partial/lost reply records the actual current options; it never edits creation evidence.
    client.invoke = real
    await lifecycle.session_set_permissions(f, "h1", sid, "default", confirm=True)
    read = await service.session_read(f, "h1", sid)
    assert read["confinement"] == r["confinement"] and read["current_verification"]["matches_creation"] is False
    await f.close()


async def test_a10_start_mismatch_stays_uncertain_and_never_sends_prompt(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, **MANAGED)
    client = f.client("h1")
    real = client.invoke

    async def changed_before_readback(channel, params=None, **kw):
        result = await real(channel, params, **kw)
        if channel == "claude:start-session":
            mock.metas[params["sessionId"]]["permissionMode"] = "acceptEdits"
        return result

    client.invoke = changed_before_readback
    with pytest.raises(confinement.ConfinementRefused, match="CONFINEMENT_MISMATCH"):
        await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True,
                                        write_scope="confined", prompt="Work")
    assert "claude:send-message" not in mock.channels()
    assert registry.list_entries("h1")[0]["status"] == "uncertain"
    await f.close()


async def test_a10_frame_guard_catches_drift_after_first_read(fleet_factory, mock):
    f = fleet_factory(writes=True, orchestrate=True, safety={"write_min_interval_s": 0}, **MANAGED)
    r = await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True, write_scope="confined")
    sid = r["session_id"]
    def drift():
        mock.metas[sid]["permissionMode"] = "bypassPermissions"
    with pytest.raises(confinement.ConfinementRefused, match="CONFINEMENT_MISMATCH"):
        await service.session_send(f, "h1", sid, "Work", confirm=True, before_invoke=drift)
    assert "claude:send-message" not in mock.channels()
    await f.close()


def test_a10_account_script_quotes_paths_without_shell_expansion():
    roots = ["/srv/space $(touch probe) 'root'"]
    script = confinement.account_script({**ACCOUNT, "protected_roots": roots})
    import shlex
    assert json.loads(shlex.split(script.splitlines()[0])[-2])["roots"] == roots


async def test_a10_lost_confined_start_ack_retains_options_and_worktree(fleet_factory, mock):
    from bat_agent_connector.errors import InvokeTimeout
    f = fleet_factory(writes=True, orchestrate=True, **MANAGED)
    client = f.client("h1")
    real = client.invoke

    async def lost(channel, params=None, **kw):
        result = await real(channel, params, **kw)
        if channel == "claude:start-session":
            raise InvokeTimeout("lost start acknowledgement")
        return result

    client.invoke = lost
    with pytest.raises(InvokeTimeout):
        await orchestrate.session_start(f, "h1", "demo-project", "claude", confirm=True, write_scope="confined")
    entry = registry.list_entries("h1")[0]
    assert entry["status"] == "uncertain" and entry["confinement"]["options"] == {"permissionMode": "default"}
    assert "worktree:remove" not in mock.channels()
    await f.close()
