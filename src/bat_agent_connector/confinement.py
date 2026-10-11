"""Execution evidence, separate from resource ownership. BAT source: docs/design/confinement.md.

No settings files, probes, privilege changes or new BAT channels. OS sandbox settings are evidence of options,
not of enforcement: only the W12 live run can prove A10. Existing creation snapshots are never upgraded by reads.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import shlex
import sqlite3
import time

from . import registry
from .errors import WriteRefused

BAT_SOURCE = "b7419892fbc9946799b64cca24c2ec8c7fa15c42"
CONFINED_OPTIONS = {
    "claude": {"permissionMode": "default"},
    "codex": {"codexSandboxMode": "workspace-write", "codexApprovalPolicy": "on-request"},
}
OPTION_KEYS = ("permissionMode", "codexSandboxMode", "codexApprovalPolicy")
START_IDENTITY_MISMATCH_CODES = {"START_SESSION_MISMATCH", "FAILOVER_SUCCESSOR_MISMATCH"}
CLOSURE_VERSIONS = tuple(f"3.{minor}" for minor in range(6, 15))
CLOSURE_TREE_PREFIXES = ("/usr/lib/python", "/usr/lib64/python")
CLOSURE_NATIVE_PREFIXES = ("/usr/lib", "/usr/lib64", "/usr/local/lib", "/lib", "/lib64")
ACCOUNT_CHECK_MAX_ENTRIES = 50000
ACCOUNT_CHECK_TOOLS = ('/usr/bin/find', '/usr/bin/head', '/usr/bin/readlink', '/usr/bin/dirname',
                       '/usr/bin/printf', '/usr/bin/tr', '/usr/bin/env', '/usr/bin/timeout', '/bin/sh',
                       '/usr/bin/sudo', '/usr/sbin/sshd')
_ACCOUNT_TOOLS_PROGRAM = 'CHECK_TOOLS = ' + repr(ACCOUNT_CHECK_TOOLS) + '\n'
_ACCOUNT_NATIVE_PROGRAM = r'''
def native_paths():
    paths = []
    for line in pathlib.Path('/proc/self/maps').read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5].startswith('/'):
            paths.append(pathlib.Path(fields[5]))
    return paths
'''


class ConfinementRefused(WriteRefused):
    def __init__(self, code: str, message: str, *, sent: bool | None = None) -> None:
        self.code, self.sent = code, sent
        super().__init__(f"[{code}] {message}")


class StartFrame:
    """Per-invocation transport evidence; before_frame/connect/semaphore waits are still unsent."""

    def __init__(self, host: str, sid: str, *, journal=None, task_id: str | None = None) -> None:
        self.host, self.sid, self.sent = host, sid, False
        self.journal, self.task_id = journal, task_id

    def on_transport(self) -> None:
        registry.update(self.host, self.sid, start_sent=True)
        if self.task_id:
            record_task_start(self.journal, self.task_id, self.sid, registry.get(self.host, self.sid) or {})
        self.sent = True

    def __enter__(self):
        return self

    def __exit__(self, kind, exc, traceback):
        if isinstance(exc, asyncio.CancelledError):
            # Synchronous local bookkeeping only. Never start SSH/BAT rollback I/O
            # while unwinding cancellation; a later recovery reuses the worktree.
            if self.sent:
                registry.update(self.host, self.sid, status="uncertain")
            else:
                registry.fail_reservation(self.host, self.sid)
                registry.update(self.host, self.sid, start_sent=False)
        return False


def guard_start_record(entry: dict) -> None:
    """A recorded start mismatch cannot become a successful start on a later read."""
    record = entry.get("confinement") or {}
    if record.get("verification", {}).get("status") == "mismatch":
        raise ConfinementRefused("CONFINEMENT_MISMATCH", "reserved start already recorded a permission mismatch")
    if entry.get("error_code") in START_IDENTITY_MISMATCH_CODES:
        raise ConfinementRefused(entry["error_code"], "reserved start already recorded a different session folder")


def guard_new_start(entry: dict) -> None:
    if entry.get("status") in registry.RETIRED:
        from .errors import ResourceReadOnly
        raise ResourceReadOnly("SESSION_RETIRED", "this session ID left the host cap; start a new session ID")
    guard_start_record(entry)
    if entry.get("start_sent") is True:
        raise ConfinementRefused("CONFINEMENT_START_UNSETTLED",
                                 "reserved start already reached transport; use read-back recovery")


def guard_start_cwd(entry: dict, meta: dict | None, *, code: str = "START_SESSION_MISMATCH") -> None:
    from .resource_policy import norm

    expected = norm(entry.get("cwd"))
    observed = norm(meta.get("cwd")) if isinstance(meta, dict) else None
    if not expected or not observed:
        raise ConfinementRefused("CONFINEMENT_START_UNSETTLED", "reserved or observed session folder is missing")
    if observed != expected:
        raise ConfinementRefused(code, "BAT session folder differs from the reserved start folder")


def policy_options(agent: str, mode: str, claude_mode: str | None = None) -> dict:
    if mode == "confined":
        return dict(CONFINED_OPTIONS[agent])
    if agent == "claude":
        return {"permissionMode": claude_mode} if claude_mode else (
            {"permissionMode": "bypassPermissions"} if mode == "allow_all" else {})
    return {"codexSandboxMode": "danger-full-access", "codexApprovalPolicy": "never"} if mode == "allow_all" else {}


def recorded_options(entry: dict) -> dict:
    if "execution_options" in entry:
        return dict(entry["execution_options"])
    snapshot = entry.get("confinement") or {}
    if "options" in snapshot:
        return dict(snapshot["options"])
    params = entry.get("agent_params") or {}
    options = {"permissionMode": entry.get("permission_mode_claude"),
               "codexSandboxMode": params.get("sandboxMode"), "codexApprovalPolicy": params.get("approvalPolicy")}
    return {k: v for k, v in options.items() if v}


def snapshot(agent: str, options: dict, *, account: dict | None = None, task: bool = False,
             inherited: dict | None = None) -> dict:
    options = {k: options[k] for k in OPTION_KEYS if options.get(k)}
    full = options.get("permissionMode") in {"bypassPermissions", "bypassPlan", "acceptEdits", "auto"}
    full = full or options.get("codexSandboxMode") == "danger-full-access"
    level = "none"
    mechanisms, limits = [], ["individual_approval_can_escape", "network_not_configurable"]
    account_ok = (account or {}).get("status") == "verified"
    if account_ok:
        mechanisms.append("host_account")
        limits.extend(["host_account_declared_roots_only", "account_check_is_point_in_time",
                       "hardlinks_and_privilege_paths_unverified", "no_hostile_same_uid_during_account_check",
                       "system_bootstrap_trusted"])
        limits.extend(account.get("limits", []))
    if agent == "codex" and options.get("codexSandboxMode") in {"workspace-write", "read-only"}:
        level = "os_sandbox"
        mechanisms.append("codex_" + options["codexSandboxMode"].replace("-", "_"))
        limits.append("writable_roots_not_configurable")
    elif agent == "claude" and options.get("permissionMode") in {"default", "plan", "dontAsk"}:
        level = "prompt_gated"
        mechanisms.append("claude_permissions")
        limits.append("preapproved_rules_and_shell_can_write_outside")
    if account_ok and options.get("permissionMode") != "bypassPermissions" and not (
            options.get("codexSandboxMode") == "danger-full-access"):
        level = "host_account"
    gap = ("task_recipe_compatibility" if task and (full or not options) else
           "sandbox_enforcement_unverified" if level == "os_sandbox" else
           "prompt_rules_are_not_os_isolation" if level == "prompt_gated" else
           "execution_restriction_unverified" if level == "none" else None)
    return {"schema_version": 1, "level": level, "requested_level": level, "mechanisms": mechanisms,
            "options": options, "protected_roots": list(account.get("protected_roots") or []) if account_ok else [],
            "evidence": {"source": "start_intent", "bat_source_commit": BAT_SOURCE,
                         "host_check": copy.deepcopy(account), "inherited_from": inherited,
                         "agent": agent, "task_owned": task},
            "verification": {"status": "pending", "reason": "start_not_confirmed"},
            "limits": limits, "gap": gap}


def verify(record: dict, meta: dict | None) -> dict:
    if not isinstance(meta, dict):
        return {"status": "unknown", "reason": "session_unloaded", "observed_options": {}}
    options = record.get("options") or {}
    observed = {k: meta[k] for k in OPTION_KEYS if meta.get(k)}
    if any(k in observed and observed[k] != v for k, v in options.items()):
        return {"status": "mismatch", "reason": "permission_options_changed", "observed_options": observed}
    if not options or any(k not in observed for k in options):
        return {"status": "unknown", "reason": "permission_options_missing", "observed_options": observed}
    account = (record.get("evidence") or {}).get("host_check") or {}
    return {"status": "verified" if record.get("level") == "host_account" and account.get("status") == "verified"
            else "options_confirmed", "reason": record.get("gap"), "observed_options": observed}


def confirm(record: dict, meta: dict | None) -> dict:
    result = copy.deepcopy(record)
    if result.get("verification", {}).get("status") == "mismatch":
        return result
    if not result.get("options") and isinstance(meta, dict):
        actual = {k: meta[k] for k in OPTION_KEYS if meta.get(k)}
        if actual:
            evidence = result["evidence"]
            result = snapshot(evidence["agent"], actual, account=evidence.get("host_check"),
                              task=evidence.get("task_owned", False), inherited=evidence.get("inherited_from"))
            if record.get("gap") == "task_recipe_compatibility":
                result["gap"] = record["gap"]
    result["verification"] = verify(result, meta)
    result["evidence"].update(source="start_intent_and_bat_meta",
                                observed_options=result["verification"]["observed_options"], checked_at=time.time())
    return result


def ensure_confirmed(record: dict, meta: dict | None, *, allow_unknown: bool = False) -> None:
    guard_start_record({"confinement": record})
    state = verify(record, meta)
    if (state["status"] == "mismatch" or state["status"] == "unknown" and not allow_unknown) and record.get("options"):
        raise ConfinementRefused("CONFINEMENT_MISMATCH", state["reason"])


def session_fields(host: str, sid: str, meta: dict | None = None, *, account: dict | None = None) -> dict:
    entry = registry.get(host, sid) or {}
    record = entry.get("confinement") or {
        "schema_version": 1, "level": "none", "requested_level": "none", "mechanisms": [],
        "options": recorded_options(entry), "evidence": {"source": "legacy_registry"},
        "verification": {"status": "unknown", "reason": "legacy_evidence_missing"},
        "limits": ["legacy_evidence_missing"], "gap": "legacy_evidence_missing"}
    current = verify({**record, "options": recorded_options(entry)}, meta)
    current["matches_creation"] = recorded_options(entry) == record.get("options")
    if record.get("level") == "host_account":
        current["host_check"] = copy.deepcopy(account)
        if not account or account.get("status") != "verified":
            current.update(status="unknown", reason="host_account_unverified")
    return {"write_scope": entry.get("write_scope"), "confinement": copy.deepcopy(record),
            "current_verification": current}


def guard_raise(host: str, sid: str) -> None:
    if (registry.get(host, sid) or {}).get("write_scope") == "confined":
        raise ConfinementRefused("CONFINEMENT_RAISE_REFUSED", "confined sessions cannot raise permissions")


def guard_permissions(host: str, sid: str, options: dict) -> None:
    entry = registry.get(host, sid) or {}
    if entry.get("write_scope") == "confined" and options != recorded_options(entry):
        raise ConfinementRefused("CONFINEMENT_RAISE_REFUSED", "confined permission changes must preserve its policy")


def guard_loaded(host: str, sid: str, meta: dict | None) -> None:
    entry = registry.get(host, sid) or {}
    record = entry.get("confinement")
    if entry.get("write_scope") == "confined" and record and record.get("options"):
        ensure_confirmed(record, meta)


def resume_options(host: str, sid: str, agent: str) -> dict:
    entry = registry.get(host, sid) or {}
    options = recorded_options(entry)
    required = ("permissionMode",) if agent == "claude" else ("codexSandboxMode", "codexApprovalPolicy")
    if entry.get("write_scope") == "confined" and any(not options.get(k) for k in required):
        raise ConfinementRefused("CONFINEMENT_EVIDENCE_MISSING", "confined resume requires its original policy")
    return options


def guard_answer(host: str, sid: str, tool: str | None, *, dont_ask_again: bool, allow: bool) -> None:
    if allow and (registry.get(host, sid) or {}).get("write_scope") == "confined":
        if dont_ask_again or tool == "ExitPlanMode":
            raise ConfinementRefused("CONFINEMENT_RAISE_REFUSED", "confined approval cannot persist wider permissions")


def account_status(fleet, host: str) -> dict:
    if host not in fleet.config.hosts:
        # Historical inventory remains readable after a host leaves the active scope.
        return {"declared": False, "status": "unknown", "reason": "host_not_configured",
                "protected_roots": [], "checked_at": None, "config_sha256": None}
    config = fleet.config.host(host).confinement
    result = getattr(fleet, "_confinement_checks", {}).get(host)
    # Same-account login checks cannot establish an authentic verdict.
    signature = hashlib.sha256(json.dumps(["trusted-account-closure-v4", config], sort_keys=True).encode()).hexdigest()
    if config.get("host_account") and not config.get("check_ssh_alias"):
        return {"declared": True, "status": "unknown", "reason": "check_channel_untrusted",
                "protected_roots": list(config.get("protected_roots") or []), "checked_at": None,
                "config_sha256": signature}
    if config.get("host_account") and not result and getattr(fleet, "confinement_journal", None):
        try:
            row = fleet.confinement_journal.db.execute(
                "SELECT evidence FROM confinement_host_checks WHERE host=?", (host,)).fetchone()
            result = json.loads(row[0]) if row else None
        except (sqlite3.Error, ValueError, TypeError):
            result = None  # Read failures never certify a boundary or block an existing job.
    if isinstance(result, dict) and result.get("config_sha256") == signature:
        checked = result.get("checked_at")
        if isinstance(checked, (int, float)) and 0 <= time.time() - checked <= config.get("check_max_age_s", 300):
            if result.get("status") != "verified" or channel_matches(config, result):
                return copy.deepcopy(result)
    return {"declared": bool(config.get("host_account")), "status": "unknown", "reason": "unchecked_or_stale",
            "protected_roots": list(config.get("protected_roots") or []), "checked_at": None,
            "config_sha256": signature}


async def check_account(fleet, host: str) -> dict:
    config = fleet.config.host(host).confinement
    result = account_status(fleet, host)
    if not config.get("host_account") or not config.get("check_ssh_alias"):
        return result
    try:
        runner = getattr(fleet, "confinement_runner", None)
        if runner is None:
            from .checkpoints import SshGitRunner
            from .task_verifier import load_settings
            runner = SshGitRunner(load_settings().ssh_hosts)
        raw = await runner.run_account_check(host, account_script(config),
                                             timeout_s=config.get("check_timeout_s", 10) + 2,
                                             ssh_alias=config["check_ssh_alias"])
        observation = json.loads(raw)
        if observation.get("status") not in {"verified", "unknown", "mismatch"}:
            raise ValueError("invalid_account_check")
        if observation["status"] == "verified" and not channel_matches(config, observation):
            observation.update(status="unknown", reason="check_channel_untrusted")
        result.update(observation)
    except Exception as exc:  # noqa: BLE001 - absence of a check never proves protection
        result.update(status="unknown", reason=type(exc).__name__)
    result.update(declared=True, checked_at=time.time(), protected_roots=list(config.get("protected_roots") or []))
    result["evidence_ref"] = "host_account:" + host + ":" + str(result["checked_at"])
    if not hasattr(fleet, "_confinement_checks"):
        fleet._confinement_checks = {}
    fleet._confinement_checks[host] = result
    journal = getattr(fleet, "confinement_journal", None)
    if journal:
        journal.db.execute("INSERT OR REPLACE INTO confinement_host_checks(host,evidence) VALUES(?,?)",
                           (host, json.dumps(result, sort_keys=True)))
    return copy.deepcopy(result)


def channel_matches(config: dict, result: dict) -> bool:
    channel = result.get("channel") or {}
    return (isinstance(channel, dict) and config.get("check_ssh_alias") is not None
            and type(result.get("checked_uid")) is int and result.get("checked_uid") == config.get("expected_uid")
            and config.get("check_uid") != config.get("expected_uid")
            and channel.get("status") == "verified" and channel.get("method") == "sudo_exec"
            and channel.get("ssh_alias") == config["check_ssh_alias"]
            and channel.get("auditor_uid") == config.get("check_uid")
            and channel.get("bat_uid") == config.get("expected_uid")
            and channel.get("bat_account") == config.get("bat_account")
            and closure_matches(channel.get("closure")))


def closure_matches(proof: dict | None) -> bool:
    if not isinstance(proof, dict):
        return False
    version = next((v for v in CLOSURE_VERSIONS if proof.get("interpreter") == "/usr/bin/python" + v), None)
    roots = proof.get("roots")
    return (version is not None and proof.get("status") == "proven" and proof.get("schema_version") == 1
            and isinstance(roots, list) and bool(roots)
            and all(root in [p + version for p in CLOSURE_TREE_PREFIXES] for root in roots)
            and type(proof.get("entries_remaining")) is int and proof["entries_remaining"] > 0)


def account_start_effect(result: dict) -> str:
    """Project the start gate without checking a host. Undeclared accounts use CLI defaults."""
    if result.get("declared") is False:
        return "fallback_default"
    if result.get("status") == "verified":
        return "verified"
    if result.get("status") == "unknown":
        if result.get("reason") == "unchecked_or_stale":
            return "recheck"
        if result.get("reason") in ACCOUNT_HARDENING_GAPS:
            return "fallback_default"
    return "refused"


async def start_account(fleet, host: str) -> dict:
    result = await check_account(fleet, host)
    # A live check must settle recheck before any start can proceed.
    if account_start_effect(result) in {"refused", "recheck"}:
        raise ConfinementRefused("HOST_ACCOUNT_UNVERIFIED", result["reason"], sent=False)
    return result


async def start_decision(fleet, host: str, agent: str, *, confined: bool = False, task: bool = False,
                         planner: bool = False, claude_mode: str | None = None, predecessor: dict | None = None):
    account = await start_account(fleet, host)
    mode = fleet.config.host(host).default_permission_mode
    if task and mode == "confined":
        mode = "default"  # Keep the existing Task Service engine/recipe behavior.
    confined = confined or (not task and mode == "confined") or planner
    if confined and claude_mode not in (None, "default", "plan", "dontAsk"):
        raise ConfinementRefused("CONFINEMENT_RAISE_REFUSED", "confined start cannot request a wider mode")
    options = dict(CONFINED_OPTIONS[agent]) if confined else policy_options(agent, mode, claude_mode)
    if confined and agent == "claude":
        options["permissionMode"] = claude_mode or "default"
        if options["permissionMode"] == "default" and account["status"] == "verified":
            options["permissionMode"] = "acceptEdits"
    if planner:
        options = {"codexSandboxMode": "read-only", "codexApprovalPolicy": "never"}
    if predecessor:
        from .service import agent_kind
        previous = resume_options(host, predecessor["session_id"],
                                  (predecessor.get("confinement") or {}).get("evidence", {}).get("agent")
                                  or agent_kind(predecessor.get("agent_preset")) or "claude")
        protected = (predecessor.get("confinement") or {}).get("protected_roots") or []
        if protected and (account["status"] != "verified" or not set(protected) <= set(account["protected_roots"])):
            raise ConfinementRefused("CONFINEMENT_UNSUPPORTED", "successor cannot preserve protected roots")
        if previous.get("codexSandboxMode") == "read-only" or previous.get("permissionMode") == "plan":
            options = {"codexSandboxMode": "read-only", "codexApprovalPolicy": "never"}
        elif previous.get("codexApprovalPolicy") == "never" or previous.get("permissionMode") == "dontAsk":
            options["codexApprovalPolicy"] = "never"
    record = snapshot(agent, options, account=account, task=task,
                      inherited={"session_id": predecessor["session_id"],
                                 "limits": (predecessor.get("confinement") or {}).get("limits", [])}
                      if predecessor else None)
    if predecessor:
        record["limits"] = list(dict.fromkeys(record["limits"] +
                                             (predecessor.get("confinement") or {}).get("limits", [])))
    if task and fleet.config.host(host).default_permission_mode == "confined":
        record["gap"] = "task_recipe_compatibility"
    return options, "confined" if confined else None, record


def host_capability(fleet, host: str) -> dict:
    account = account_status(fleet, host)
    account["start_effect"] = account_start_effect(account)
    verified = account["status"] == "verified"
    return {"policy": fleet.config.host(host).default_permission_mode, "host_account": account,
            "network_configurable": False, "writable_roots_configurable": False,
            "agents": {"claude": {"requested_level": "host_account" if verified else "prompt_gated",
                                   "verified_level": "host_account" if verified else "none",
                                   "reachable_options": ["permissionMode"],
                                   "limits": ["preapproved_rules_and_shell_can_write_outside",
                                              "host_account_declared_roots_only"], "gap": None if verified else
                                   "plain_default_requires_approvals"},
                       "codex": {"requested_level": "os_sandbox", "verified_level": "none",
                                 "reachable_options": ["codexSandboxMode", "codexApprovalPolicy"],
                                 "limits": ["individual_approval_can_escape", "writable_roots_not_configurable",
                                            "network_not_configurable"], "gap": "sandbox_enforcement_unverified"}}}


def account_script(config: dict) -> str:
    """Linux-only metadata scan. GNU find evaluates ACLs; no probe or hand-written ACL evaluator."""
    payload = json.dumps({"uid": config["expected_uid"], "roots": config["protected_roots"],
                          "entries": config.get("check_max_entries", ACCOUNT_CHECK_MAX_ENTRIES),
                          "seconds": config.get("check_timeout_s", 10), "port": config.get("bat_port", 9876),
                          "auditor_uid": config["check_uid"], "bat_account": config["bat_account"],
                          "ssh_alias": config["check_ssh_alias"]})
    gate = (_CLOSURE_SHELL + '\nproof=$(prove_closure) || exit 1\n'
            + 'exec /usr/bin/python3 -I -S -B -c ' + shlex.quote(_ACCOUNT_CHANNEL_PROGRAM)
            + ' "$2" "$proof" < /dev/null')
    return ("cd / && /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/timeout "
            + str(config.get("check_timeout_s", 10)) + " /bin/sh -s -- "
            + str(config.get("check_max_entries", ACCOUNT_CHECK_MAX_ENTRIES)) + " " + shlex.quote(payload)
            + " <<'BATC_CLOSURE'\n" + gate + "\nBATC_CLOSURE\n"
            + "if [ $? -ne 0 ]; then /usr/bin/printf '%s\\n' "
            + shlex.quote('{"status":"unknown","reason":"check_executable_untrusted"}') + "; fi")


# Both the pre-interpreter gate and the in-program rechecks execute this definition.
# Fixed system layouts; never ask the unproven interpreter where it imports code.
_CLOSURE_SHELL = r'''
remaining=$1
newline='
'
consume() { remaining=$((remaining - 1)); [ "$remaining" -gt 0 ] || return 1; }
point() {
    local hit
    consume || return 1
    hit=$(/usr/bin/find -P "$1" -maxdepth 0 \( ! -uid 0 -o \( ! -type l -a -perm /022 \) -o ! -readable \) -printf X 2>&1) || return 1
    [ -z "$hit" ]
}
parents() {
    local parent
    parent=$(/usr/bin/dirname -- "$1") || return 1
    while :; do
        if [ -e "$parent" ] || [ -L "$parent" ]; then
            point "$parent" || return 1
            [ -d "$parent" ] && [ -x "$parent" ] && [ ! -L "$parent" ] || return 1
        fi
        [ "$parent" = / ] && break
        parent=$(/usr/bin/dirname -- "$parent") || return 1
    done
}
target() {
    local path next
    path=$1
    while :; do
        case "$path" in /*) ;; *) return 1 ;; esac
        case "$path" in *"$newline"*) return 1 ;; esac
        parents "$path" && point "$path" || return 1
        if [ ! -L "$path" ]; then break; fi
        next=$(/usr/bin/readlink -- "$path") || return 1
        case "$next" in /*) path=$next ;; *) path="$(/usr/bin/dirname -- "$path")/$next" ;; esac
    done
    if [ -d "$path" ]; then
        [ -x "$path" ] && { [ "${2-}" = metadata ] || tree "$path"; }
    else
        [ -f "$path" ]
    fi
}
absent() {
    parents "$1" || return 1
    [ ! -e "$1" ] && [ ! -L "$1" ]
}
complete_records() {
    local record complete
    complete=false
    while IFS= read -r record; do
        [ "$complete" = false ] || return 1
        case "$record" in
            E) consume || return 1 ;;
            L*) consume && target "${record#L}" || return 1 ;;
            DONE) complete=true ;;
            *) return 1 ;;
        esac
    done <<EOF
$1
EOF
    [ "$complete" = true ]
}
tree() {
    local records
    # The terminal marker proves find completed. head bounds captured records;
    # find errors, truncation, special files and newline names never count as proof.
    records=$({ /usr/bin/find -P "$1" \( ! -uid 0 -o \( ! -type l -a -perm /022 \) -o ! -readable -o -name "*$newline*" -o \( ! -type f -a ! -type d -a ! -type l \) \) -printf 'X\n' -quit -o -type l -printf 'L%p\n' -o -printf 'E\n' 2>&1
                [ $? -eq 0 ] && /usr/bin/printf 'DONE\n' || /usr/bin/printf 'X\n'
              } | /usr/bin/head -n "$((remaining + 2))") || return 1
    complete_records "$records"
}
prove_closure() {
    local interpreter version config roots prefix root zip library resolved hidden
    interpreter=$(/usr/bin/readlink -e /usr/bin/python3) || return 1
    case "$interpreter" in @INTERPRETERS@) ;; *) return 1 ;; esac
    version=${interpreter#/usr/bin/python}
    target /usr/bin/python3 && target "$interpreter" || return 1
    # _pth overrides -I/-S; build markers and venvs also redirect path discovery.
    # None is part of the supported system-package layout, even if root-owned.
    for config in /usr/bin/pyvenv.cfg /usr/pyvenv.cfg /usr/bin/python3._pth \
                  "$interpreter._pth" /usr/bin/pybuilddir.txt /usr/bin/Modules/Setup.local; do
        absent "$config" || return 1
    done
    # Shared libpython can have its own _pth, with precedence over the executable.
    # Inspect the standard loader directories and immediate multiarch children;
    # prove library symlink chains and also reject overrides at their real targets.
    for prefix in @NATIVE_PREFIXES@; do
        parents "$prefix" || return 1
        if [ ! -e "$prefix" ] && [ ! -L "$prefix" ]; then continue; fi
        [ -d "$prefix" ] && target "$prefix" metadata || return 1
        prefix=$(/usr/bin/readlink -e "$prefix") || return 1
        # Shell globs silently omit inaccessible multiarch children. Such an
        # incomplete search cannot establish that library overrides are absent.
        hidden=$({ /usr/bin/find -L "$prefix" -mindepth 1 -maxdepth 1 -type d \( ! -readable -o ! -executable \) -printf 'X\n' -quit -o -printf 'E\n' 2>&1
                   [ $? -eq 0 ] && /usr/bin/printf 'DONE\n' || /usr/bin/printf 'X\n'
                 } | /usr/bin/head -n "$((remaining + 2))") || return 1
        complete_records "$hidden" || return 1
        for library in "$prefix"/libpython"$version"*.so* "$prefix"/*/libpython"$version"*.so*; do
            if [ ! -e "$library" ] && [ ! -L "$library" ]; then continue; fi
            case "$library" in *._pth) return 1 ;; esac
            [ -f "$library" ] && target "$library" || return 1
            resolved=$(/usr/bin/readlink -e "$library") || return 1
            absent "$library._pth" && absent "$resolved._pth" || return 1
        done
    done
    roots=''
    for prefix in @TREE_PREFIXES@; do
        root=$prefix$version
        parents "$root" || return 1
        if [ -e "$root" ] || [ -L "$root" ]; then
            [ -d "$root" ] && target "$root" || return 1
            roots="${roots}${roots:+,}\"$root\""
        fi
        zip=$prefix$(/usr/bin/printf '%s' "$version" | /usr/bin/tr -d .).zip
        parents "$zip" || return 1
        if [ -e "$zip" ] || [ -L "$zip" ]; then target "$zip" || return 1; fi
    done
    [ -n "$roots" ] || return 1
    /usr/bin/printf '{"schema_version":1,"status":"proven","interpreter":"%s","roots":[%s],"entries_remaining":%s}\n' "$interpreter" "$roots" "$remaining"
}
'''.replace('@INTERPRETERS@', '|'.join('/usr/bin/python' + v for v in CLOSURE_VERSIONS)).replace(
    '@TREE_PREFIXES@', ' '.join(CLOSURE_TREE_PREFIXES)).replace(
    '@NATIVE_PREFIXES@', ' '.join(CLOSURE_NATIVE_PREFIXES))

_ACCOUNT_CLOSURE_PROGRAM = r'''
def check_closure():
    global remaining
    try:
        budget()
        result = subprocess.run(['/usr/bin/timeout', str(max(.01, deadline-time.monotonic())),
                                 '/bin/sh', '-c', CLOSURE_SHELL + '\nprove_closure\n', 'closure', str(remaining)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=max(.01, deadline-time.monotonic()))
        proof = json.loads(result.stdout)
        if result.returncode or result.stderr or not closure_matches(proof): raise ValueError('closure_unproven')
        if proof['entries_remaining'] >= remaining: raise ValueError('closure_budget_unproven')
        remaining = proof['entries_remaining']; c['closure'] = proof
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        finish('unknown', 'check_executable_untrusted')
'''.replace('CLOSURE_SHELL', repr(_CLOSURE_SHELL))
_ACCOUNT_CLOSURE_PROGRAM = ('\n' + _ACCOUNT_CLOSURE_PROGRAM.replace('closure_matches(proof)',
    "(proof.get('status') == 'proven' and proof.get('schema_version') == 1 and "
    "proof.get('interpreter') in " + repr(['/usr/bin/python' + v for v in CLOSURE_VERSIONS]) + " and "
    "isinstance(proof.get('roots'), list) and proof['roots'] and all(root in "
    + repr([p + v for p in CLOSURE_TREE_PREFIXES for v in CLOSURE_VERSIONS]) + " for root in proof['roots']) and "
    "type(proof.get('entries_remaining')) is int and proof['entries_remaining'] > 0)"))


ACCOUNT_HARDENING_GAPS = {"check_executable_untrusted", "login_environment_writable", "login_shell_unsupported",
                        "check_channel_untrusted"}

# Kept separately so synthetic fixtures can exercise the actual integrity checks
# without altering stdlib classes or inspecting a developer's real account.
_ACCOUNT_INTEGRITY_PROGRAM = r'''
def check_integrity(account_uid=None):
    global remaining
    account = pwd.getpwuid(c['uid'] if account_uid is None else account_uid)
    shell = pathlib.Path(account.pw_shell)
    startup = {'sh': ['.profile'], 'dash': ['.profile'],
               'bash': ['.bashrc', '.bash_profile', '.bash_login', '.profile'],
               'zsh': ['.zshenv', '.zprofile', '.zshrc', '.zlogin']}.get(shell.name)
    if startup is None: return 'login_shell_unsupported', {'login_shell': str(shell)}
    executables = [pathlib.Path(sys.executable), shell,
                  *[pathlib.Path(p) for p in c['closure']['roots']],
                  *[pathlib.Path(p) for p in CHECK_TOOLS], *native_paths()]
    trusted = set()
    for path in executables:
        resolved = path.resolve(strict=True)
        for target in (path, resolved):
            for part in [target, *target.parents]:
                budget()
                info = part.lstat()
                if (info.st_uid != 0 or not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022
                        or os.access(part, os.W_OK, effective_ids=True)):
                    return 'check_executable_untrusted', {'paths': [str(part)]}
                trusted.add(str(part))
    # Bootstrap above rejects a replaceable find before running it. GNU find's
    # access(2) checks below also evaluate ACLs using this account's effective IDs.
    for target in sorted(trusted):
        budget(); remaining -= 1
        if remaining <= 0: finish('unknown', 'entry_budget_exhausted')
        scan = subprocess.run(['/usr/bin/find', target, '-maxdepth', '0', '-writable', '-printf', 'W'],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=max(.01, deadline - time.monotonic()))
        if scan.returncode or scan.stderr: finish('unknown', 'check_incomplete')
        if scan.stdout: return 'check_executable_untrusted', {'paths': [target]}
    home = pathlib.Path(account.pw_dir)
    paths = [home, home/'.ssh', home/'.pam_environment', *[home/name for name in startup]]
    # A writable ancestor could replace an otherwise protected home directory.
    child = home
    for parent in home.parents:
        budget(); info = parent.stat(); victim = child.stat()
        if (info.st_uid == c['uid'] or os.access(parent, os.W_OK | os.X_OK, effective_ids=True)
                and (not info.st_mode & stat.S_ISVTX or c['uid'] in (info.st_uid, victim.st_uid))):
            return 'login_environment_writable', {'paths': [str(parent)]}
        child = parent
    for path in paths:
        budget()
        if not path.exists() and not path.is_symlink():
            path = path.parent  # Absence is safe only if it cannot be created.
        if path.is_symlink(): return 'login_environment_writable', {'paths': [str(path)]}
        args = ['/usr/bin/find', str(path)]
        if path != home/'.ssh': args += ['-maxdepth', '0']
        args += ['-printf', 'E\\0', '(', '-uid', str(c['uid']), '-o', '-writable', '-o', '-type', 'l',
                 ')', '-printf', 'W:%p\\0', '-quit']
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        selector = selectors.DefaultSelector(); selector.register(proc.stdout, selectors.EVENT_READ)
        try:
            pending = b''
            while True:
                budget()
                if not selector.select(max(0, deadline - time.monotonic())): finish('unknown', 'time_budget_exhausted')
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk: break
                pending += chunk; lines = pending.split(b'\0'); pending = lines.pop()
                for line in lines:
                    if line.startswith(b'W:'):
                        return 'login_environment_writable', {'paths': [os.fsdecode(line[2:])]}
                    if line != b'E': finish('unknown', 'find_output_invalid')
                    remaining -= 1
                    if remaining <= 0: finish('unknown', 'entry_budget_exhausted')
            _, errors = proc.communicate(timeout=max(.01, deadline - time.monotonic()))
            if pending or proc.returncode or errors: finish('unknown', 'check_incomplete')
        finally:
            selector.close()
            if proc.poll() is None: proc.kill(); proc.wait()
    return None, {'home': str(home), 'login_shell': str(shell), 'trusted_paths': sorted(trusted)}
'''


_ACCOUNT_PROGRAM = r'''
import json, os, pathlib, pwd, selectors, stat, subprocess, sys, time
c = json.loads(sys.argv[1]); deadline = time.monotonic() + c['seconds']; remaining = c['entries']
def finish(status, reason, **evidence):
    print(json.dumps(dict(status=status, reason=reason, checked_uid=os.geteuid(), channel=c.get('channel'), entries_remaining=remaining, **evidence))); raise SystemExit
def budget():
    if time.monotonic() >= deadline: finish('unknown', 'time_budget_exhausted')
if not sys.platform.startswith('linux'): finish('unknown', 'linux_only')
if os.geteuid() != c['uid'] or os.getuid() != c['uid']: finish('unknown', 'ssh_uid_mismatch')
if not c.get('channel'): finish('unknown', 'check_channel_untrusted')
groups = sorted(set(os.getgroups() + [os.getegid()]))
''' + _ACCOUNT_TOOLS_PROGRAM + _ACCOUNT_NATIVE_PROGRAM + _ACCOUNT_CLOSURE_PROGRAM + _ACCOUNT_INTEGRITY_PROGRAM + r'''
def identity(pid):
    text = pathlib.Path('/proc', str(pid), 'status').read_text()
    values = dict(line.split(':', 1) for line in text.splitlines() if ':' in line)
    return dict(pid=int(pid), ppid=int(values['PPid']), uid=[int(x) for x in values['Uid'].split()],
                gid=[int(x) for x in values['Gid'].split()], groups=sorted(int(x) for x in values['Groups'].split()),
                capabilities=int(values['CapEff'].strip(), 16),
                capabilities_permitted=int(values['CapPrm'].strip(), 16),
                capabilities_ambient=int(values['CapAmb'].strip(), 16))
try:
    check_closure()
    reason, integrity = check_integrity()
    if reason: finish('unknown', reason, **integrity)
    reason, auditor_integrity = check_integrity(c['channel']['auditor_uid'])
    if reason: finish('unknown', 'check_channel_untrusted', channel_reason=reason, **auditor_integrity)
    if c.get('channel_only'): finish('verified', 'channel_preflight', integrity=integrity, auditor_integrity=auditor_integrity)
    if c['channel'].get('status') != 'verified': finish('unknown', 'check_channel_untrusted')
    sockets = set()
    for table in ('tcp', 'tcp6'):
        for line in pathlib.Path('/proc/net', table).read_text().splitlines()[1:]:
            parts = line.split()
            if parts[3] == '0A' and int(parts[1].split(':')[1], 16) == c['port']: sockets.add(parts[9])
    procs = {}; bats = []; names = {}
    for proc in pathlib.Path('/proc').iterdir():
        budget()
        if not proc.name.isdigit(): continue
        try:
            info = identity(proc.name); procs[info['pid']] = info
            name = (proc/'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace'); names[info['pid']] = name
            if 'bat-server' in name or 'better-agent-terminal' in name:
                if any(os.readlink(fd) in {'socket:[' + x + ']' for x in sockets} for fd in (proc/'fd').iterdir()):
                    bats.append(info)
        except (OSError, ValueError, KeyError): continue
    if len(bats) != 1: finish('unknown', 'bat_process_unidentified')
    bat = bats[0]; descendants = {bat['pid']}
    for _ in range(len(procs)):
        more = {p for p, i in procs.items() if i['ppid'] in descendants}
        if more <= descendants: break
        descendants |= more
    runtimes = [procs[p] for p in descendants - {bat['pid']} if any(
        token in names[p] for token in ('claude', 'codex', 'node-sidecar', 'server.mjs'))]
    for i in [bat] + runtimes:
        if any(u != c['uid'] for u in i['uid']) or len(set(i['gid'])) != 1 or i['gid'][1] != os.getegid() or i['groups'] != sorted(os.getgroups()):
            finish('unknown', 'runtime_identity_mismatch')
        if any(i[k] for k in ('capabilities', 'capabilities_permitted', 'capabilities_ambient')):
            finish('mismatch', 'runtime_has_capabilities')
    before = []
    for root in c['roots']:
        budget(); path = pathlib.Path(root)
        if not path.is_dir() or path.is_symlink() or str(path.resolve()) != str(path): finish('unknown', 'root_unresolved')
        info = path.stat(); before.append((root, info.st_dev, info.st_ino))
        child = path
        for parent in path.parents:
            budget(); info = parent.stat(); victim = child.stat()
            if info.st_uid == os.geteuid(): finish('mismatch', 'owned_ancestor_can_chmod')
            if os.access(parent, os.W_OK | os.X_OK, effective_ids=True) and (not info.st_mode & stat.S_ISVTX or os.geteuid() in (info.st_uid, victim.st_uid)):
                finish('mismatch', 'writable_ancestor')
            child = parent
        proc = subprocess.Popen(['/usr/bin/find', root, '-xdev', '-printf', 'E:%D\n', '-writable', '-printf', 'W\n', '-quit', '-o', '-uid', str(c['uid']), '-printf', 'O\n', '-quit', '-o', '-type', 'l', '-printf', 'L\n', '-quit'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        selector = selectors.DefaultSelector(); selector.register(proc.stdout, selectors.EVENT_READ)
        try:
            pending = b''
            while True:
                budget()
                ready = selector.select(max(0, deadline - time.monotonic()))
                if not ready: finish('unknown', 'time_budget_exhausted')
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk: break
                pending += chunk
                lines = pending.split(b'\n'); pending = lines.pop()
                for line in lines:
                    if line == b'W': finish('mismatch', 'writable_entry')
                    if line == b'O': finish('mismatch', 'owned_entry_can_chmod')
                    if line == b'L': finish('unknown', 'symlink_unchecked')
                    if not line.startswith(b'E:'): finish('unknown', 'find_output_invalid')
                    if int(line[2:]) != path.stat().st_dev: finish('unknown', 'mount_unchecked')
                    remaining -= 1
                    if remaining <= 0: finish('unknown', 'entry_budget_exhausted')
            _, errors = proc.communicate(timeout=max(.01, deadline - time.monotonic()))
            if proc.returncode or errors: finish('unknown', 'find_incomplete')
        finally:
            selector.close()
            if proc.poll() is None: proc.kill(); proc.wait()
    if any((os.stat(p).st_dev, os.stat(p).st_ino) != (dev, ino) for p, dev, ino in before): finish('unknown', 'root_changed')
    if any(identity(i['pid']) != i for i in [bat] + runtimes): finish('unknown', 'runtime_changed')
    finish('verified', 'read_only_account_check', integrity=integrity, alias_uid=os.geteuid(), bat=bat, runtimes=runtimes, roots=before,
           limits=[] if runtimes else ['runtime_identity_inherited_unobserved'])
except (OSError, ValueError, KeyError, subprocess.TimeoutExpired): finish('unknown', 'check_incomplete')
'''


# The auditor establishes a trusted bootstrap before the BAT-UID preflight.
# ACL-bearing bootstrap paths are refused conservatively. The preflight uses
# GNU find's effective-ID/ACL evaluation for both passwd-derived environments.
_ACCOUNT_CHANNEL_INTEGRITY_PROGRAM = r'''
def channel_preconditions():
    global remaining
    if os.getuid() != c['auditor_uid'] or os.geteuid() != c['auditor_uid'] or c['auditor_uid'] == c['uid']:
        return 'check_channel_untrusted', {'channel_reason': 'auditor_identity_mismatch'}
    bat_identity = pwd.getpwnam(c['bat_account'])
    if bat_identity.pw_uid != c['uid']:
        return 'check_channel_untrusted', {'channel_reason': 'bat_account_uid_mismatch'}
    auditor = pwd.getpwuid(c['auditor_uid'])
    trusted = set()
    for path in [pathlib.Path(sys.executable), pathlib.Path(auditor.pw_shell),
                 *[pathlib.Path(p) for p in c['closure']['roots']],
                 *[pathlib.Path(p) for p in CHECK_TOOLS], *native_paths()]:
        for target in (path, path.resolve(strict=True)):
            for part in [target, *target.parents]:
                budget(); remaining -= 1
                if remaining <= 0: finish('unknown', 'entry_budget_exhausted')
                info = part.lstat()
                if info.st_uid != 0 or not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022:
                    return 'check_executable_untrusted', {'paths': [str(part)]}
                for attribute in ('system.posix_acl_access', 'system.posix_acl_default'):
                    try: os.getxattr(part, attribute)
                    except OSError as exc:
                        if exc.errno not in (errno.ENODATA, errno.ENOTSUP): raise
                    else: return 'check_executable_untrusted', {'paths': [str(part)], 'channel_reason': 'bootstrap_acl_unproven'}
                trusted.add(str(part))
    home = pathlib.Path(auditor.pw_dir)
    startup = {'sh': ['.profile'], 'dash': ['.profile'],
               'bash': ['.bashrc', '.bash_profile', '.bash_login', '.profile'],
               'zsh': ['.zshenv', '.zprofile', '.zshrc', '.zlogin']}.get(pathlib.Path(auditor.pw_shell).name)
    if startup is None: return 'check_channel_untrusted', {'channel_reason': 'auditor_login_shell_unsupported'}
    groups = set(os.getgrouplist(c['bat_account'], bat_identity.pw_gid))
    paths = [home, home/'.ssh', home/'.ssh/rc', home/'.ssh/environment', home/'.ssh/authorized_keys',
             home/'.pam_environment', *[home/name for name in startup]]
    for path in paths:
        for part in [path, *path.parents]:
            budget(); remaining -= 1
            if remaining <= 0: finish('unknown', 'entry_budget_exhausted')
            try: info = part.lstat()
            except FileNotFoundError: continue  # Its parent must still pass before absence is safe.
            if (info.st_uid == c['uid'] or stat.S_ISLNK(info.st_mode) or info.st_mode & 0o002
                    or info.st_gid in groups and info.st_mode & 0o020):
                return 'check_channel_untrusted', {'channel_reason': 'auditor_login_environment_writable', 'paths': [str(part)]}
            for attribute in ('system.posix_acl_access', 'system.posix_acl_default'):
                try: os.getxattr(part, attribute)
                except OSError as exc:
                    if exc.errno not in (errno.ENODATA, errno.ENOTSUP): raise
                else: return 'check_channel_untrusted', {'channel_reason': 'auditor_login_acl_unproven', 'paths': [str(part)]}
    return None, {'trusted_paths': sorted(trusted), 'auditor_home': str(home)}
'''

_ACCOUNT_CHANNEL_PROGRAM = r'''
import errno, json, os, pathlib, pwd, stat, subprocess, sys, time
c = json.loads(sys.argv[1]); c['closure'] = json.loads(sys.argv[2])
deadline = time.monotonic() + c['seconds']; remaining = c['closure']['entries_remaining']
channel = dict(status='pending', method='sudo_exec', ssh_alias=c['ssh_alias'], auditor_uid=os.geteuid(),
               bat_uid=c['uid'], bat_account=c['bat_account'])
def finish(status, reason, **evidence):
    print(json.dumps(dict(status=status, reason=reason, checked_uid=os.geteuid(), channel=channel, **evidence))); raise SystemExit
def budget():
    if time.monotonic() >= deadline: finish('unknown', 'time_budget_exhausted')
if not sys.platform.startswith('linux'): finish('unknown', 'linux_only')
''' + _ACCOUNT_TOOLS_PROGRAM + _ACCOUNT_NATIVE_PROGRAM + _ACCOUNT_CLOSURE_PROGRAM + _ACCOUNT_CHANNEL_INTEGRITY_PROGRAM + r'''
try:
    check_closure()
    reason, integrity = channel_preconditions()
    if reason: finish('unknown', reason, **integrity)
    argv = ['/usr/bin/sudo', '-n', '-u', c['bat_account'], '--', '/usr/bin/env', '-i',
            'PATH=/usr/bin:/bin', 'LC_ALL=C', '/usr/bin/python3', '-I', '-S', '-B']
    def run_stage(preflight):
        check_closure()  # Reprove the same closure before starting another interpreter as BAT.
        budget()
        payload = dict(c, entries=remaining, seconds=max(.01, deadline-time.monotonic()),
                       channel=channel, channel_only=preflight)
        program = 'import sys\nsys.argv = ["-", ' + repr(json.dumps(payload)) + ']\n' + CHILD_PROGRAM
        result = subprocess.run(argv + ['-c', program], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=max(.01, deadline-time.monotonic()))
        if result.returncode or result.stderr: finish('unknown', 'check_channel_untrusted', channel_reason='direct_exec_failed')
        observed = json.loads(result.stdout)
        if observed.get('checked_uid') != c['uid']:
            finish('unknown', 'check_channel_untrusted', channel_reason='checker_uid_mismatch')
        return observed
    preflight = run_stage(True)
    if preflight.get('status') != 'verified':
        if preflight.get('reason') not in ('check_executable_untrusted', 'login_environment_writable', 'login_shell_unsupported', 'check_channel_untrusted'):
            preflight.update(channel_reason=preflight.get('reason'), status='unknown', reason='check_channel_untrusted')
        print(json.dumps(preflight)); raise SystemExit
    remaining = preflight['entries_remaining']
    if remaining <= 0: finish('unknown', 'entry_budget_exhausted')
    try:
        ptrace_scope = int(pathlib.Path('/proc/sys/kernel/yama/ptrace_scope').read_text().strip())
    except (OSError, ValueError):
        ptrace_scope = None
    channel.update(status='verified', bootstrap=integrity, auditor_integrity=preflight['auditor_integrity'],
                   closure=c['closure'], ptrace_scope=ptrace_scope)
    observed = run_stage(False)
    print(json.dumps(observed))
except (OSError, ValueError, KeyError, subprocess.TimeoutExpired): finish('unknown', 'check_channel_untrusted', channel_reason='channel_check_incomplete')
'''
_ACCOUNT_CHANNEL_PROGRAM = _ACCOUNT_CHANNEL_PROGRAM.replace('CHILD_PROGRAM', repr(_ACCOUNT_PROGRAM))


def record_task_start(journal, task_id: str, sid: str, entry: dict) -> None:
    """Add evidence at the existing start-command write point, before BAT gets the frame."""
    if journal is None:
        return
    command = next((c for c in journal.commands(task_id) if c["session_id"] == sid
                    and c["kind"] in {"start_lead", "start_reviewer"}), None)
    if command:
        payload = json.loads(command["payload"])
        payload.update({k: entry[k] for k in ("confinement", "write_scope", "permission_mode_claude", "agent_params", "start_sent",
                                             "controller_authority", "controller_incarnation")
                        if k in entry})
        journal.db.execute("UPDATE commands SET payload=? WHERE command_id=?",
                           (json.dumps(payload), command["command_id"]))


async def guard_frame(client, host: str, sid: str) -> None:
    entry = registry.get(host, sid) or {}
    if entry.get("write_scope") == "confined" and entry.get("confinement"):
        guard_loaded(host, sid, await client.guard_read("claude:get-session-meta", {"sessionId": sid}))


def guard_resume_frame(host: str, sid: str, agent: str, frame: dict) -> None:
    original = resume_options(host, sid, agent)
    if (registry.get(host, sid) or {}).get("write_scope") == "confined":
        actual = frame["params"].get("options") or {}
        if any(actual.get(k) != v for k, v in original.items()):
            raise ConfinementRefused("CONFINEMENT_MISMATCH", "resume frame differs from the recorded policy")


async def guard_start_frame(fleet, host: str, record: dict | None = None) -> None:
    # Fresh identity/ACL evidence at the start boundary, after worktree preparation.
    if fleet.config.host(host).confinement.get("host_account"):
        account = await start_account(fleet, host)
        if record and record.get("evidence", {}).get("host_check", {}).get("status") == "verified":
            if account["status"] != "verified":
                raise ConfinementRefused("HOST_ACCOUNT_UNVERIFIED", account["reason"], sent=False)
