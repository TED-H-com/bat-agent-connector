"""BAT adapter for the task coordinator. No host-side changes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import (
    confinement,
    lifecycle,
    orchestrate,
    platform_files,
    registry,
    resource_policy,
    service,
    task_control,
)
from .errors import BatError, TaskControlRefused, TaskDispatchCancelled, TaskIdentityMismatch, WriteRefused
from .fleet import Fleet
from .operations import AMBIGUOUS, AmbiguousOutcome, StepFailed, _error_code
from .redact import redact_secrets
from .safety import Audit
from .task_handoff import history_excerpt, ledger_summary, original_words_archive
from .task_verifier import ObservedVerifier, VerificationSettings, classify_failure, failure_tail

# BAT's model id for Opus 5.5 (from claude:get-supported-models). Claude sessions
# always pin it, so a BAT default can never silently pick Sonnet.
CLAUDE_BAT_MODEL = "claude-opus-5-5:auto-compact-300k"
# Claude is offered only while the host's own usage snapshot is fresh and below
# these utilizations; anything unknown falls back to Codex.
CLAUDE_MAX_UTILIZATION = {"fiveHour": 0.85, "sevenDay": 0.90}
USAGE_SNAPSHOT_MAX_AGE_S = 1800
PROVIDER_ERROR_LATCH_S = 3600


class BatTaskAdapter:
    def __init__(self, fleet: Fleet, verifier: ObservedVerifier | None = None, journal=None):
        self.fleet = fleet
        self.verifier = verifier or ObservedVerifier(VerificationSettings())
        self.register_tabs = self.verifier.settings.register_tabs
        self.journal = journal
        if journal is not None:
            self.fleet.confinement_journal = journal
        self._agents_cache: dict[str, tuple[float, frozenset[str]]] = {}

    async def available_agents(self, task: dict) -> frozenset[str]:
        """BAT agents this host can run now. Codex is the always-available default.

        AGY claude-opus-4-6-thinking is not a BAT runtime (BAT runs only Claude
        Code and Codex), so it is never offered for BAT sessions.
        """
        host = task["host"]
        cached = self._agents_cache.get(host)
        if cached and time.time() - cached[0] < 60:
            return cached[1]
        agents = {"codex"}
        try:
            snap = await self.fleet.client(host).invoke("agent:usage-snapshot", {}, retry_on_disconnect=False)
        except Exception:  # noqa: BLE001 - unknown usage means Codex only
            snap = None
        claude = snap.get("claude") if isinstance(snap, dict) else None
        if isinstance(claude, dict):
            fetched = claude.get("fetchedAt")
            fresh = (isinstance(fetched, (int, float)) and not isinstance(fetched, bool)
                     and 0 <= time.time() - fetched / 1000 <= USAGE_SNAPSHOT_MAX_AGE_S)
            under = all(isinstance(w := claude.get(name), dict)
                        and isinstance(w.get("utilization"), (int, float))
                        and not isinstance(w.get("utilization"), bool)
                        and 0 <= w["utilization"] < limit
                        for name, limit in CLAUDE_MAX_UTILIZATION.items())
            latched = bool(self.journal) and self.journal.provider_unavailable(
                "claude", since=time.time() - PROVIDER_ERROR_LATCH_S)
            if fresh and under and not latched:
                agents.add("claude")
        result = frozenset(agents)
        self._agents_cache[host] = (time.time(), result)
        return result

    async def _ssh_script(self, task: dict, script: str) -> str:
        alias = self.verifier.settings.ssh_hosts.get(task["host"])
        if not alias:
            raise ValueError("base_branch requires a configured SSH verification host")
        cmd = ("ssh", "-o", "BatchMode=yes", alias, "sh -lc " + shlex.quote(script))
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise ValueError((err or out).decode(errors="replace")[-1000:] or "remote git command failed")
        return out.decode(errors="replace").strip()

    async def _workspace_folder(self, task: dict) -> str:
        doc = await service._workspace(self.fleet.client(task["host"]))
        matches = [w for w in doc.get("workspaces") or []
                   if task["workspace"] in {w.get("id"), w.get("name")} ]
        if len(matches) != 1 or not matches[0].get("folderPath"):
            raise ValueError("task workspace has no unique folder")
        return str(matches[0]["folderPath"])

    async def _ensure_external_worktree(self, task: dict) -> dict:
        task_control.require_authority(task)
        root = await self._workspace_folder(task)
        suffix = task["task_id"].replace("-", "")[:12]
        path = f"{root}/.bat-worktrees/batc-task-{suffix}"
        branch = f"batc/task-{suffix}"
        resource_policy.authorize_external_worktree(self.fleet.config.host(task["host"]), root, path, branch,
                                                    task["task_id"])
        base = task["base_branch"]
        qroot, qpath, qbranch, qbase = map(shlex.quote, (root, path, branch, base))
        script = (f"mkdir -p {shlex.quote(root + '/.bat-worktrees')} && "
                  f"if git -C {qroot} worktree list --porcelain | "
                  f"grep -Fxq {shlex.quote('worktree ' + path)}; then exit 0; fi; "
                  f"if git -C {qroot} show-ref --verify --quiet refs/heads/{qbranch}; then "
                  f"git -C {qroot} worktree add {qpath} {qbranch}; "
                  f"else if git -C {qroot} fetch origin {qbase}; then ref=FETCH_HEAD; else ref={qbase}; fi; "
                  f"git -C {qroot} worktree add -b {qbranch} {qpath} \"$ref\"; fi")
        await self._ssh_script(task, script)
        identity = await self.verifier.identity(task, path)
        if not identity or not identity.get("clean"):
            raise ValueError("connector-created worktree identity is unavailable or dirty")
        result = {"path": path, "branch": branch, "base_commit": identity["candidate_commit"],
                  "base_branch": base}
        if self.journal:
            self.journal.change(task["task_id"], "dispatching", fields={
                "external_worktree_path": path, "external_branch": branch,
                "base_commit": identity["candidate_commit"]}, event="external_worktree_created")
        return result

    async def cleanup_external_worktree(self, task: dict) -> dict | None:
        task_control.require_authority(task)
        path, branch = task.get("external_worktree_path"), task.get("external_branch")
        if not path or not branch:
            return None
        root = await self._workspace_folder(task)
        suffix = task["task_id"].replace("-", "")[:12]
        try:
            resource_policy.check_external_worktree(root, path, branch, task["task_id"])
        except WriteRefused:
            raise ValueError("external worktree cleanup identity mismatch") from None
        branch_ref = f"refs/heads/{branch}"
        retained_ref = f"refs/batc/tasks/{suffix}"
        qroot, qpath, qbranch_ref, qretained_ref = map(
            shlex.quote, (root, path, branch_ref, retained_ref))
        worktree_line = shlex.quote("worktree " + path)
        expected = task.get("verification_commit") if task.get("state") == "done" else None
        if task.get("state") == "done" and (
                not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{40}", expected)):
            raise ValueError("done task has no verified cleanup commit")
        expected_check = f"test \"$head\" = {shlex.quote(expected)}; " if expected else ""
        script = (
            "set -eu; "
            f"if test -d {qpath}; then "
            f"git -C {qroot} worktree list --porcelain | grep -Fxq {worktree_line}; "
            f"head=$(git -C {qpath} rev-parse HEAD); "
            f"branch_head=$(git -C {qroot} rev-parse --verify {qbranch_ref}); "
            "test \"$head\" = \"$branch_head\"; "
            f"{expected_check}"
            f"status=$(git -C {qpath} status --porcelain); test -z \"$status\"; "
            f"if git -C {qroot} show-ref --verify --quiet {qretained_ref}; then "
            f"kept=$(git -C {qroot} rev-parse --verify {qretained_ref}); "
            "test \"$kept\" = \"$head\"; "
            f"else git -C {qroot} update-ref {qretained_ref} \"$head\" \"\"; fi; "
            f"git -C {qroot} worktree remove {qpath}; mode=removed; "
            "else "
            f"head=$(git -C {qroot} rev-parse --verify {qretained_ref}); "
            f"branch_head=$(git -C {qroot} rev-parse --verify {qbranch_ref}); "
            "test \"$head\" = \"$branch_head\"; "
            f"{expected_check}"
            f"if git -C {qroot} worktree list --porcelain | grep -Fxq {worktree_line}; then exit 1; fi; "
            "mode=already_removed; fi; "
            f"test \"$(git -C {qroot} rev-parse --verify {qretained_ref})\" = \"$head\"; "
            f"test \"$(git -C {qroot} rev-parse --verify {qbranch_ref})\" = \"$head\"; "
            "printf '%s\\n%s\\n' \"$head\" \"$mode\""
        )
        lines = (await self._ssh_script(task, script)).splitlines()
        if (len(lines) != 2 or not re.fullmatch(r"[0-9a-f]{40}", lines[0])
                or lines[1] not in {"removed", "already_removed"}):
            raise ValueError("external worktree cleanup proof unavailable")
        return {"path": path, "branch": branch, "retained_ref": retained_ref,
                "commit": lines[0], "mode": lines[1]}

    async def _warm_identity(self, task: dict, previous: dict) -> dict | None:
        from . import controller_authority
        task_control.require_authority(task)
        controller_authority.require_binding(task_control.authority(previous))
        sid = previous.get("session_id")
        if (not sid or previous.get("state") != "done" or previous.get("external_worktree_path")
                or previous.get("reviewer_session_id")
                or task.get("base_branch")):
            return None
        entry = registry.get(task["host"], sid)
        controller_authority.require_entry(entry or {})
        if (not entry or entry.get("task_id") != previous["task_id"]
                or entry.get("role") != "lead" or entry.get("status") != "active"
                or task["workspace"] not in {entry.get("workspace_id"), entry.get("workspace_name")}
                or entry.get("agent_preset") != orchestrate.PRESETS[(task["lead_agent"], True)]
                or not entry.get("worktree_path") or entry.get("cwd") != entry["worktree_path"]
                or not entry.get("branch") or entry.get("origin_cwd") != await self._workspace_folder(task)):
            return None
        Audit(self.fleet.config.safety).check_rate(task["host"], sid)
        client = self.fleet.client(task["host"])
        meta = await client.invoke("claude:get-session-meta", {"sessionId": sid}, retry_on_disconnect=False)
        status = await client.invoke("worktree:status", {"sessionId": sid}, retry_on_disconnect=False)
        if (not isinstance(meta, dict) or meta.get("cwd") != entry["cwd"]
                or not isinstance(status, dict) or status.get("worktreePath") != entry["cwd"]
                or status.get("branchName") != entry["branch"]
                or await client.invoke("git:getRoot", {"cwd": entry["cwd"]}, retry_on_disconnect=False)
                != entry["cwd"]):
            return None
        confinement.ensure_confirmed(entry.get("confinement") or {}, meta, allow_unknown=True)
        read = await service.session_read(self.fleet, task["host"], sid, last_n=1)
        identity = await self.verifier.identity(task, entry["cwd"])
        # Reuse only the exact verified state: HEAD must still be the previous task's verified commit.
        if (read.get("streaming") is not False or read.get("pending")
                or not identity or not identity.get("clean")
                or not previous.get("verification_commit")
                or identity.get("candidate_commit") != previous["verification_commit"]):
            return None
        return entry

    async def find_warm(self, task: dict) -> str | None:
        """Reuse a clean, idle lead branch only for a follow-up in the same workstream."""
        if not self.journal or task.get("task_path") != "minimal" or task.get("base_branch"):
            return None
        for previous in self.journal.warm_candidates(task):
            try:
                if await self._warm_identity(task, previous):
                    return previous["session_id"]
            except Exception:  # noqa: BLE001, S112 - an unproven warm branch is skipped
                continue
        return None

    @registry.start_call
    async def start(self, task: dict, *, role: str, agent: str, session_id: str) -> str:
        task_control.require_authority(task)
        host = task["host"]
        entry = registry.get(host, session_id) or {}
        if task.get("_warm_session_id") == session_id:
            confinement.guard_start_record(entry)
        else:
            confinement.guard_new_start(entry)
            registry.claim_unsent(host, session_id)
        before_start = (task_control.start_guard(self.journal, task, session_id, role)
                        if self.journal else None)
        if role == "lead":
            if task.get("_warm_session_id") == session_id:
                previous = next((item for item in self.journal.warm_candidates(task)
                                 if item["session_id"] == session_id), None) if self.journal else None
                identity = await self._warm_identity(task, previous) if previous else None
                task_control.check_incarnation(self.journal, task)
                if not identity:
                    raise TaskIdentityMismatch("warm session identity is unproven")
                self.journal.revoke_task_capabilities(previous["task_id"])
                registry.claim_warm(host, session_id, previous_task_id=previous["task_id"],
                                    task_id=task["task_id"], workspace_id=identity["workspace_id"],
                                    cwd=identity["cwd"], branch=identity["branch"])
                confinement.record_task_start(self.journal, task["task_id"], session_id,
                                              registry.get(host, session_id) or {})
                # The reused branch's verified HEAD is the next task's diff baseline.
                self.journal.change(task["task_id"], "dispatching", fields={
                    "base_commit": previous["verification_commit"]}, event="warm_base_recorded")
                return session_id
            external = await self._ensure_external_worktree(task) if task.get("base_branch") else None
            last_error = None
            for attempt in range(3):
                try:
                    r = await orchestrate.session_start(
                        self.fleet, host, task["workspace"], agent, confirm=True, prompt=None,
                        model=CLAUDE_BAT_MODEL if agent == "claude" else None,
                        use_worktree=external is None, title="task " + task["task_id"][:8],
                        session_id=session_id, retain_on_error=True, register_tab=self.register_tabs,
                        base_branch=task.get("base_branch") if external is None else None,
                        cwd_override=external["path"] if external else None,
                        external_branch=external["branch"] if external else None,
                        task_id=task["task_id"], _task_start_guard=before_start)
                    break
                except TaskControlRefused:
                    raise
                except Exception as exc:  # noqa: BLE001 - retained sent starts are fenced by guard_new_start
                    if getattr(exc, "sent", None) is False:
                        raise
                    last_error = exc
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.25 * (2 ** attempt))
            else:  # pragma: no cover
                raise last_error or RuntimeError("session start failed")
            if self.journal and (r.get("base_branch") or r.get("base_commit")):
                self.journal.change(task["task_id"], "dispatching", fields={
                    "base_branch": r.get("source_branch") or r.get("base_branch") or task.get("base_branch"),
                    "base_commit": r.get("base_commit") or (external or {}).get("base_commit"),
                }, event="base_recorded")
            return r["session_id"]
        # An independent reviewer must inspect the same candidate worktree while
        # the lead has stopped writing. A new BAT session gets no write permission.
        if await self.session_presence(task, task["session_id"]) != "present":
            raise ValueError("lead worktree identity is unproven for reviewer")
        lead = registry.get(host, task["session_id"])
        if not lead or not lead.get("cwd"):
            raise ValueError("lead worktree unavailable for reviewer")
        hc = self.fleet.config.host(host)
        sid = session_id
        grant = await resource_policy.authorize_shared_session(self.fleet, host, sid,
                                                               service.registry_terminal(lead))
        preset = orchestrate.PRESETS[(agent, False)]
        entry = {"session_id": sid, "workspace_id": lead["workspace_id"],
                 "workspace_name": lead["workspace_name"], "agent_preset": preset,
                 "origin_cwd": lead["origin_cwd"], "cwd": lead["cwd"],
                 "worktree_path": lead.get("worktree_path"), "branch": lead.get("branch"),
                 "lead_session_id": task["session_id"], "task_id": task["task_id"],
                 "title": "review " + task["task_id"][:8], "role": "reviewer"}
        opts = {"cwd": lead["cwd"], "agentPreset": preset,
                "workspaceId": lead["workspace_id"], "workspaceName": lead["workspace_name"]}
        if agent == "codex":
            opts.update(codexSandboxMode="read-only", codexApprovalPolicy="never")
        else:
            opts.update(permissionMode="plan", model=CLAUDE_BAT_MODEL)
        if before_start:
            before_start()
        try:
            account = await confinement.start_account(self.fleet, host)
        finally:
            if before_start:
                before_start()
        record = confinement.snapshot(agent, opts, account=account, task=True)
        entry.update(confinement=record, start_sent=False, **orchestrate.registry_permission_fields(opts))
        registry.reserve(host, entry, hc.orchestrate_max_sessions)
        confinement.record_task_start(self.journal, task["task_id"], sid, registry.get(host, sid) or {})
        client = self.fleet.client(host)
        async def before_start_frame():
            if before_start:
                before_start()
            try:
                await confinement.guard_start_frame(self.fleet, host, record)
            finally:
                if before_start:
                    before_start()
        with confinement.StartFrame(host, sid, journal=self.journal, task_id=task["task_id"]) as start_frame:
            try:
                started = await client.invoke(
                    "claude:start-session", {"sessionId": sid, "options": opts}, retry_on_disconnect=False,
                    grant=grant, before_send=before_start, before_frame=before_start_frame,
                    on_transport=start_frame.on_transport)
                if (not isinstance(started, dict) or started.get("ok") is False
                        or started.get("sessionId") != sid):
                    raise WriteRefused("BAT reviewer start did not confirm the reserved session ID")
            except Exception as exc:  # noqa: BLE001 - a sent start permits only read-back, never another frame
                if not start_frame.sent:
                    registry.fail_reservation(host, sid)
                    registry.update(host, sid, start_sent=False)
                    raise
                for attempt in range(3):
                    try:
                        meta = await client.invoke("claude:get-session-meta", {"sessionId": sid},
                                                   retry_on_disconnect=False)
                    except Exception:  # noqa: BLE001 - retry only the read of the reserved ID
                        meta = None
                    if isinstance(meta, dict):
                        break
                    if attempt < 2:
                        await asyncio.sleep(0.25 * (2 ** attempt))
                else:
                    registry.update(host, sid, status="uncertain", error_code="CONFINEMENT_START_UNSETTLED")
                    raise confinement.ConfinementRefused(
                        "CONFINEMENT_START_UNSETTLED", "BAT reviewer start did not settle; use read-back recovery",
                        sent=True) from exc
            else:
                try:
                    meta = await client.invoke("claude:get-session-meta", {"sessionId": sid}, retry_on_disconnect=False)
                except Exception:  # noqa: BLE001 - evidence reads must not fail an acknowledged reviewer start
                    meta = None
            confinement.guard_start_record(registry.get(host, sid) or {})
            if isinstance(meta, dict):
                try:
                    confinement.guard_start_cwd(entry, meta)
                    confinement.ensure_confirmed(record, meta, allow_unknown=True)
                except confinement.ConfinementRefused as refusal:
                    registry.update(host, sid, status="uncertain", error_code=refusal.code,
                                    **({"confinement": confinement.confirm(record, meta)}
                                       if refusal.code == "CONFINEMENT_MISMATCH" else {}))
                    if refusal.code == "CONFINEMENT_MISMATCH":
                        confinement.record_task_start(self.journal, task["task_id"], sid, registry.get(host, sid) or {})
                    raise
            registry.update(host, sid, status="active", cwd=lead["cwd"], confinement=confinement.confirm(record, meta))
            if self.register_tabs and hc.orchestrate_register_tabs:
                try:
                    tab = await self.fleet.client(host).append_workspace_terminal(hc.profile_id, {
                        "id": sid, "workspaceId": lead["workspace_id"], "title": entry["title"],
                        "type": "terminal", "cwd": lead["cwd"], "agentPreset": preset,
                        "permissionMode": opts.get("permissionMode"),
                        "agentParams": orchestrate.registry_permission_fields(opts)["agent_params"],
                    }, grant=resource_policy.authorize_register_tab(host, sid))
                    registry.update(host, sid, tab_registered=bool(tab.get("appended")))
                except Exception:  # noqa: BLE001 - registration is visibility only
                    registry.update(host, sid, tab_registered=False)
            return sid

    async def recover_start(self, task: dict, *, role: str, session_id: str) -> bool:
        intent = next((c for c in self.journal.commands(task["task_id"])
                       if c["kind"] == "start_" + role and c["session_id"] == session_id), None) if self.journal else None
        try:
            confinement.guard_start_record(registry.get(task["host"], session_id) or {})
            if intent:
                confinement.guard_start_record({"confinement": json.loads(intent["payload"]).get("confinement")})
        except (confinement.ConfinementRefused, TypeError, ValueError):
            return False
        entry = registry.get(task["host"], session_id)
        if ((entry or {}).get("start_sent") is False
                or entry is None and intent and json.loads(intent["payload"]).get("start_sent") is False):
            if not intent:
                return False
            try:
                await self.start(task, role=role, agent=json.loads(intent["payload"])["agent"], session_id=session_id)
            except Exception:  # noqa: BLE001 - only durable unsent evidence permits this same-ID attempt
                return False
            return True
        if role == "lead" and task.get("base_branch"):
            try:
                await self._ensure_external_worktree(task)
            except Exception:
                return False
        meta = await self.fleet.client(task["host"]).invoke(
            "claude:get-session-meta", {"sessionId": session_id}, retry_on_disconnect=False,
        )
        if not isinstance(meta, dict):
            return False
        if not self.journal:
            return False
        if not intent:
            return False
        try:
            await self._restore_headless_lookup(task, session_id, meta, role=role,
                                                agent=json.loads(intent["payload"])["agent"])
        except Exception:  # noqa: BLE001 - recovery must fail closed on host/identity errors
            return False
        return True

    async def session_presence(self, task: dict, session_id: str) -> str:
        """Confirm BAT identity, restoring only journal-owned headless lookups.

        A missing workspace tab is expected when registration is off. BAT null
        metadata or worktree status can also mean an unloaded/restarting host;
        these replies never prove a session vanished.
        """
        if not self.journal or not any(
            b["session_id"] == session_id and b["role"] in {"lead", "reviewer"}
            for b in self.journal.branches(task["task_id"])
        ):
            return "uncertain"
        client = self.fleet.client(task["host"])
        for attempt in range(4):
            try:
                meta = await client.invoke("claude:get-session-meta", {"sessionId": session_id},
                                           retry_on_disconnect=False)
            except Exception:  # noqa: BLE001 - transport failure is not proof of absence
                if attempt < 3:
                    await asyncio.sleep(0.25 * (2 ** attempt))
                    continue
                return "uncertain"
            if isinstance(meta, dict):
                try:
                    await self._restore_headless_lookup(task, session_id, meta)
                except Exception:  # noqa: BLE001 - no verified lookup, so no prompt
                    return "uncertain"
                return "present"
            if attempt < 3:
                await asyncio.sleep(0.25 * (2 ** attempt))
        # Worktree status may also be null after a BAT restart while its disk
        # directory persists. Only a future host-side explicit absence proof
        # may return "vanished"; journal replacement remains fail closed today.
        return "uncertain"

    async def _restore_headless_lookup(self, task: dict, session_id: str, meta: dict,
                                       *, role: str | None = None, agent: str | None = None) -> None:
        branch = next((b for b in self.journal.branches(task["task_id"])
                       if b["session_id"] == session_id and b["role"] in {"lead", "reviewer"}), None)
        if (not (branch or role) or (branch and role and branch["role"] != role)
                or not meta.get("cwd")):
            raise ValueError("journal-owned BAT session has no verified folder")
        role = role or branch["role"]
        agent = agent or (branch["provider"] if branch else task["lead_agent"])
        if agent not in {"codex", "claude"}:
            raise ValueError("journal-owned BAT session has an invalid agent")
        client = self.fleet.client(task["host"])
        workspace_doc = await service._workspace(client)
        workspace = next((w for w in workspace_doc.get("workspaces") or []
                          if task["workspace"] in {w.get("id"), w.get("name")}), None)
        if not workspace:
            raise ValueError("task workspace no longer exists on BAT host")
        existing = registry.get(task["host"], session_id)
        confinement.guard_start_record(existing or {})
        if existing and (existing.get("task_id") not in {None, task["task_id"]}
                         or existing.get("role") not in {None, role}):
            raise ValueError("local session entry belongs to another task or role")
        if task.get("external_worktree_path"):
            worktree = None
        else:
            try:
                worktree = await client.invoke(
                    "worktree:status", {"sessionId": session_id}, retry_on_disconnect=False)
            except Exception as exc:  # noqa: BLE001 - no worktree proof on transport failure
                raise ValueError("BAT worktree identity is unavailable") from exc
        lead_id = task.get("session_id") if role == "reviewer" else None
        if role == "reviewer":
            if not lead_id or not any(b["session_id"] == lead_id and b["role"] == "lead"
                                      for b in self.journal.branches(task["task_id"])):
                raise ValueError("reviewer has no journal-owned lead")
            lead = registry.get(task["host"], lead_id)
            if not lead:
                lead_meta = await client.invoke("claude:get-session-meta", {"sessionId": lead_id},
                                                retry_on_disconnect=False)
                if not isinstance(lead_meta, dict):
                    raise ValueError("lead worktree identity is unavailable")
                await self._restore_headless_lookup(task, lead_id, lead_meta)
                lead = registry.get(task["host"], lead_id)
            if (not lead or lead.get("task_id") not in {None, task["task_id"]}
                    or lead.get("workspace_id") != workspace.get("id")
                    or lead.get("origin_cwd") != workspace.get("folderPath")
                    or not lead.get("worktree_path") or lead.get("cwd") != lead["worktree_path"]):
                raise ValueError("reviewer lead worktree identity does not match task")
            expected_cwd = lead["worktree_path"]
            if not task.get("external_worktree_path"):
                lead_status = await client.invoke("worktree:status", {"sessionId": lead_id},
                                                  retry_on_disconnect=False)
                if (not isinstance(lead_status, dict) or lead_status.get("worktreePath") != expected_cwd
                        or lead_status.get("branchName") != lead.get("branch")):
                    raise ValueError("lead worktree changed before reviewer lookup")
            if isinstance(worktree, dict) and worktree.get("worktreePath") != expected_cwd:
                raise ValueError("reviewer worktree differs from lead")
            branch_name = lead.get("branch")
        else:
            if task.get("external_worktree_path"):
                expected_cwd = task["external_worktree_path"]
                branch_name = task.get("external_branch")
            else:
                if (not isinstance(worktree, dict) or not worktree.get("worktreePath")
                        or not worktree.get("branchName")):
                    raise ValueError("lead worktree is not registered on BAT host")
                expected_cwd = worktree["worktreePath"]
                branch_name = worktree["branchName"]
        try:
            confinement.guard_start_cwd({"cwd": (existing or {}).get("cwd") or expected_cwd}, meta)
            confinement.guard_start_cwd({"cwd": expected_cwd}, meta)
        except confinement.ConfinementRefused as refusal:
            if existing and refusal.code in confinement.START_IDENTITY_MISMATCH_CODES:
                registry.update(task["host"], session_id, error_code=refusal.code)
            raise
        if await client.invoke("git:getRoot", {"cwd": expected_cwd}, retry_on_disconnect=False) != expected_cwd:
            raise ValueError("BAT session folder does not match the journal-owned workspace")
        preset = orchestrate.PRESETS[(agent, role == "lead" and not task.get("external_worktree_path"))]
        if existing and any(existing.get(key) not in {None, expected}
                            for key, expected in (
                                ("workspace_id", workspace.get("id")),
                                ("origin_cwd", workspace.get("folderPath")),
                                ("cwd", expected_cwd), ("worktree_path", expected_cwd),
                                ("branch", branch_name), ("agent_preset", preset))):
            raise ValueError("local session entry conflicts with BAT and task identity")
        intent = next((c for c in self.journal.commands(task["task_id"])
                       if c["kind"] == "start_" + role and c["session_id"] == session_id), None)
        evidence = json.loads(intent["payload"]) if intent else {}
        # Missing registry is recoverable only from the original stamped creation,
        # never by lending today's task controller to an unknown old session.
        from . import controller_authority
        creation = existing or evidence
        controller_authority.require_entry(creation)
        record = (existing or {}).get("confinement") or evidence.get("confinement")
        if record:
            confinement.guard_start_record({"confinement": record})
            try:
                confinement.ensure_confirmed(record, meta, allow_unknown=True)
            except confinement.ConfinementRefused as refusal:
                if record.get("verification", {}).get("status") == "pending":
                    record = confinement.confirm(record, meta)
                    confinement.record_task_start(self.journal, task["task_id"], session_id, {"confinement": record})
                if existing:
                    registry.update(task["host"], session_id, status="uncertain", error_code=refusal.code,
                                    confinement=record)
                raise
            if record.get("verification", {}).get("status") == "pending":
                record = confinement.confirm(record, meta)
                if existing:
                    registry.update(task["host"], session_id, confinement=record)
        registry.ensure_existing(task["host"], {
            "session_id": session_id, "workspace_id": workspace.get("id"),
            "workspace_name": workspace.get("name"), "agent_preset": preset,
            "origin_cwd": workspace.get("folderPath"), "cwd": expected_cwd,
            "worktree_path": expected_cwd, "branch": branch_name,
            "role": role, "task_id": task["task_id"], "lead_session_id": lead_id, "error_code": None,
            "title": ("review " if role == "reviewer" else "task ") + task["task_id"][:8],
            "controller_authority": creation["controller_authority"],
            "controller_incarnation": creation["controller_incarnation"],
            **({} if existing else {"confinement": record or {
                **confinement.session_fields(task["host"], session_id)["confinement"],
                "options": {k: meta[k] for k in confinement.OPTION_KEYS if meta.get(k)},
                "evidence": {"source": "task_journal_and_bat_meta"}},
                **{k: evidence[k] for k in ("write_scope", "permission_mode_claude", "agent_params") if k in evidence}}),
        })

    async def send(self, task: dict, session_id: str, text: str, message_id: str) -> dict:
        task_control.require_authority(task)
        command = (self.journal.send_for_message(task["task_id"], session_id, message_id)
                   if self.journal else None)
        purpose = json.loads(command["payload"]).get("purpose") if command else None
        initial_task_send = bool(command and command["status"] == "needs_review" and (
            (purpose == "lead:initial" and session_id == task.get("session_id")) or
            (purpose == "reviewer:initial" and session_id == task.get("reviewer_session_id"))
        ))

        def before_invoke() -> None:
            if not self.journal:
                return
            current = self.journal.get(task["task_id"])
            command = self.journal.send_for_message(task["task_id"], session_id, message_id)
            if (current["paused"] or current["control_version"] != task["control_version"]
                    or session_id not in {current.get("session_id"), current.get("reviewer_session_id")}
                    or not command or command["status"] != "needs_review"
                    or json.loads(command["payload"]).get("prompt_sha256") != hashlib.sha256(text.encode()).hexdigest()):
                raise TaskDispatchCancelled("task send was cancelled before BAT invoke")

        from .task_control import FrameGuard
        guard = FrameGuard(self.journal, task["task_id"], task["host"], session_id,
                           task["control_version"], command["command_id"] if command else None,
                           internal=not str(purpose).startswith("goose:"),
                           prompt_sha256=hashlib.sha256(text.encode()).hexdigest())
        try:
            return await service.session_send(self.fleet, task["host"], session_id, text, confirm=True,
                                              message_id=message_id, retry_on_disconnect=False,
                                              before_invoke=before_invoke, _task_guard=guard,
                                              initial_task_send=initial_task_send)
        except WriteRefused:
            raise
        except Exception as exc:
            if not guard.frames:
                # The coordinator must not reconcile a prompt that could not have reached BAT.
                raise StepFailed(_error_code(exc), str(exc)) from exc
            if isinstance(exc, BatError) and not isinstance(exc, AMBIGUOUS):
                raise StepFailed(_error_code(exc), str(exc)) from exc
            if not isinstance(exc, (*AMBIGUOUS, BatError, OSError)):
                raise AmbiguousOutcome("send reply could not be recorded: " + type(exc).__name__) from exc
            raise

    async def prepare_send(self, task: dict, session_id: str) -> dict:
        entry = registry.get(task["host"], session_id)
        kind = "codex" if str((entry or {}).get("agent_preset") or "").startswith("codex") else "claude"
        snapshot = await service.session_read(self.fleet, task["host"], session_id, last_n=1)
        last = (snapshot.get("messages") or [])[-1:]
        cursor = last[0].get("ts") if last else None
        if kind == "codex" and not cursor:
            cursor = datetime.now(timezone.utc).isoformat()
        return {"agent_kind": kind, "before_cursor": cursor,
                "before_message_id": last[0].get("id") if last else None,
                "before_was_empty": not bool(last)}

    async def reconcile_send(self, task: dict, session_id: str, prompt_sha256: str,
                             before: dict, message_id: str) -> dict | None:
        """Prove delivery from the exact BAT user echo after the pre-send fence.

        A timestamped assistant reply, message ID supplied by the caller, or
        a previous identical prompt is insufficient. This never sends a frame.
        """
        for attempt in range(2):
            snapshot = await service.session_read(
                self.fleet, task["host"], session_id, last_n=100,
                max_chars=60_000, max_message_chars=20_050)
            messages = snapshot.get("messages") or []
            baseline_id = before.get("before_message_id")
            if baseline_id:
                positions = [i for i, item in enumerate(messages) if item.get("id") == baseline_id]
                if not positions:
                    return None  # the pre-send fence fell outside the bounded read
                newer = messages[positions[-1] + 1:]
            elif before.get("before_was_empty") and snapshot.get("next_offset") is None:
                newer = messages
            else:
                return None
            users = [item for item in newer if item.get("role") == "user"]
            if (len(users) == 1 and isinstance(users[0].get("id"), str)
                    and hashlib.sha256((users[0].get("text") or "").encode()).hexdigest() == prompt_sha256):
                marker = users[0]["id"]
                registry.record_turn(task["host"], session_id, marker,
                                     queued=False, baseline_turns=None)
                return {"accepted": True, "turn_marker": marker,
                        "turn_attribution": "exact_echo", "reconciled": True}
            if attempt == 0:
                await asyncio.sleep(0.2)
        return None

    async def read(self, task: dict, session_id: str, marker: str | None) -> dict:
        # Control flow reads the tail of the final agent message, so do not let
        # the default 2k per-message clip drop a status line or verdict.
        result = await service.session_read(self.fleet, task["host"], session_id, after=marker,
                                            max_chars=40_000, max_message_chars=20_000)
        if session_id == task.get("reviewer_session_id") and task.get("review_commit"):
            all_turns = await service.session_read(self.fleet, task["host"], session_id,
                                                   last_n=30, max_message_chars=5000)
            users = [m for m in all_turns.get("messages") or [] if m.get("role") == "user"]
            tag = "REVIEW-CANDIDATE: " + task["review_commit"] + " " + task["review_tree"]
            result["first_turn_proven"] = len(users) == 1 and tag in (users[0].get("text") or "")
        return result

    async def interrupt(self, task: dict, session_id: str) -> None:
        task_control.require_authority(task)
        from .task_control import FrameGuard
        guard = FrameGuard(self.journal, task["task_id"], task["host"], session_id,
                           task["control_version"], action="interrupt", abort=True)
        await service.session_interrupt(self.fleet, task["host"], session_id, "hard", confirm=True,
                                        _task_guard=guard)

    async def failover(self, task: dict, session_id: str, successor_id: str, *,
                       handoff_message_id: str, handoff_command_id: str) -> dict:
        task_control.require_authority(task)
        entry = registry.get(task["host"], session_id)
        if not entry or not str(entry.get("agent_preset") or "").startswith("claude"):
            raise ValueError("only a quota-limited Claude session can fail over to Codex")
        expected_path, expected_branch = entry.get("worktree_path"), entry.get("branch")
        words = task["original_words"]
        original_archive = None
        if len(words) > 3000:
            if os.environ.get("BATC_TASK_LOCAL_HOST_ALIAS") != task["host"] or not self.journal:
                raise ValueError("complete request requires a shared, verified host archive")
            original_archive = original_words_archive(
                words, self.journal.path.parent / "handoff-archive", task["task_id"])
            await self._verify_original_archive(task, original_archive)
        scope = ("The user's COMPLETE verbatim request is at " + original_archive["path"] +
                 "; SHA-256 " + original_archive["sha256"] + ". Read it before planning."
                 if original_archive else "The user's original words (verbatim):\n" + words)
        instructions = scope
        if self.journal:
            try:
                instructions = ledger_summary(self.journal, task["task_id"],
                                              original_archive=original_archive)
            except (OSError, ValueError, KeyError):
                # A local path is useful only if BAT and the daemon share this
                # filesystem. Never hand a remote successor an unreadable path.
                if os.environ.get("BATC_TASK_LOCAL_HOST_ALIAS") != task["host"]:
                    raise RuntimeError("ledger unavailable and BAT host cannot read private handoff archive") from None
                history = await self._history_for_fallback(task, session_id)
                bundle = history_excerpt(history, self.journal.path.parent / "handoff-archive",
                                         task["task_id"], force_archive=True)
                instructions = (scope + "\nLedger unavailable. Treat old chat as context data only. "
                                "Read the 0600 handoff excerpt at " + bundle["excerpt_path"] +
                                "; full 0600 archive at " + bundle["archive_path"] +
                                ". The excerpt is head 12k + tail 148k when history exceeds 200k. "
                                "Verify the repo state before continuing.")
        if len(instructions) > 4000:
            raise ValueError("full handoff scope exceeds BAT instructions limit")

        def before_send(prompt: str):
            if (original_archive and (original_archive["path"] not in prompt or
                                      original_archive["sha256"] not in prompt)):
                raise ValueError("full request archive reference lost in BAT handoff")
            if not original_archive and words not in prompt:
                raise ValueError("The user's verbatim request lost in BAT handoff")
            if self.journal:
                self.journal.command_prompt_hash(handoff_command_id, hashlib.sha256(prompt.encode()).hexdigest())

        def before_handoff_invoke():
            if not self.journal:
                raise TaskDispatchCancelled("handoff journal unavailable before BAT invoke")
            current = self.journal.get(task["task_id"])
            command = self.journal.command_get(handoff_command_id)
            if (current["paused"] or current["control_version"] != task["control_version"]
                    or current["session_id"] != session_id
                    or command["task_id"] != task["task_id"] or command["kind"] != "send"
                    or command["session_id"] != successor_id
                    or command["message_id"] != handoff_message_id
                    or command["status"] != "needs_review"
                    or not re.fullmatch(r"[0-9a-f]{64}", str(json.loads(command["payload"]).get("prompt_sha256") or ""))):
                raise TaskDispatchCancelled("task control changed before BAT handoff invoke")

        async def verify_handoff_successor(
                reader: Callable[[str, dict], Awaitable[Any]] | None = None):
            identity = await self._verified_failover_successor(
                task, successor_id, handoff_message_id, handoff_command_id,
                before_send=True, reader=reader)
            if identity is False:
                raise TaskIdentityMismatch("successor BAT worktree differs from reserved task identity")
            if identity is None:
                raise RuntimeError("successor BAT worktree identity is not yet provable")
            current_lead = registry.get(task["host"], session_id)
            if (not expected_path or not expected_branch or not current_lead
                    or current_lead.get("worktree_path") != expected_path
                    or current_lead.get("branch") != expected_branch):
                raise TaskIdentityMismatch("lead worktree changed during successor verification")

        async def verify_handoff_at_frame():
            await verify_handoff_successor(self.fleet.client(task["host"]).guard_read)

        def handoff_frame_guard(frame: dict):
            before_handoff_invoke()
            params = frame.get("params") or {}
            prompt = params.get("prompt")
            command = self.journal.command_get(handoff_command_id)
            intended = json.loads(command["payload"]).get("prompt_sha256")
            if (frame.get("channel") != "claude:send-message" or not isinstance(prompt, str)
                    or params.get("sessionId") != successor_id
                    or params.get("clientMessageId") != handoff_message_id
                    or hashlib.sha256(prompt.encode()).hexdigest() != intended):
                raise TaskIdentityMismatch("actual BAT handoff frame differs from journal intent")
            registry.record_handoff_frame(
                task["host"], old_session_id=session_id, successor_id=successor_id,
                task_id=task["task_id"], worktree_path=expected_path, branch=expected_branch,
                command_id=handoff_command_id, message_id=handoff_message_id,
                prompt_sha256=intended)
            before_handoff_invoke()

        coordinator = getattr(self.fleet, "task_coordinator", None)
        if coordinator is None or coordinator.adapter is not self or coordinator.journal is not self.journal:
            raise TaskControlRefused("TASK_OWNED_CONTROL_REQUIRED", "failover requires the owning coordinator")
        authority = coordinator._failover_authority(
            task, successor_id, handoff_command_id, handoff_message_id,
            before_handoff_send=before_send, verify_handoff_successor=verify_handoff_successor,
            verify_handoff_at_frame=verify_handoff_at_frame, before_handoff_invoke=before_handoff_invoke,
            handoff_frame_guard=handoff_frame_guard)
        r = await lifecycle.session_failover(self.fleet, task["host"], session_id, confirm=True,
                                             instructions=instructions, ledger_only=bool(self.journal),
                                             task_authority=authority, authoritative_original=True)
        if not r.get("prompt_sent") and not r.get("skipped"):
            raise RuntimeError("failover handoff outcome is uncertain")
        if r.get("new_session_id") != successor_id:
            raise TaskIdentityMismatch("BAT returned a different failover successor")
        identity = await self._verified_failover_successor(
            task, successor_id, handoff_message_id, handoff_command_id)
        if identity is False:
            raise TaskIdentityMismatch("reserved failover successor conflicts with BAT identity")
        if identity is None:
            raise RuntimeError("reserved failover successor and handoff identity are unproven")
        return {"session_id": successor_id, "marker": handoff_message_id}

    async def _verify_original_archive(self, task: dict, archive: dict):
        path = Path(archive["path"])
        alias = os.environ.get("BATC_TASK_ARCHIVE_VERIFY_SSH_HOST", "")
        if platform_files.WINDOWS:
            local = platform_files.read_private(path)
            private = True
        else:
            local = path.read_bytes()
            private = stat.S_IMODE(path.stat().st_mode) == 0o600
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+", alias) or not private or
                hashlib.sha256(local).hexdigest() != archive["sha256"]):
            raise ValueError("complete request archive is not locally valid or host verifier is unset")
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", alias,
                   "sha256sum -- " + shlex.quote(str(path))]
        result = await asyncio.to_thread(subprocess.run, command, capture_output=True,
                                         text=True, timeout=8, check=False)
        if result.returncode != 0 or result.stdout.split(maxsplit=1)[0:1] != [archive["sha256"]]:
            raise ValueError("BAT successor host cannot verify the complete request archive")

    async def _history_for_fallback(self, task: dict, session_id: str) -> str:
        pages = []
        offset = 0
        for _ in range(3000):
            page = await service.session_read(self.fleet, task["host"], session_id,
                                              last_n=100, offset=offset, max_chars=60_000,
                                              max_message_chars=60_000)
            pages.append([str(m.get("role") or "?") + ": " + str(m.get("text") or "")
                          for m in page.get("messages") or []])
            next_offset = page.get("next_offset")
            if next_offset is None:
                break
            if not isinstance(next_offset, int) or next_offset <= offset:
                raise RuntimeError("BAT history pagination is incomplete")
            offset = next_offset
        else:
            raise RuntimeError("BAT history exceeds safe pagination limit")
        if not any(pages):
            raise RuntimeError("ledger and BAT history are unavailable for failover")
        return "\n".join(message for page in reversed(pages) for message in page)

    async def _verified_failover_successor(self, task: dict, successor_id: str,
                                           handoff_message_id: str, handoff_command_id: str, *,
                                           before_send: bool = False,
                                           reader: Callable[[str, dict], Awaitable[Any]] | None = None) -> bool | None:
        """Require exact journal, registry and BAT host identity; null host data is unknown."""
        if not self.journal:
            return None
        try:
            handoff = self.journal.command_get(handoff_command_id)
            hp = json.loads(handoff["payload"])
            failover = next(c for c in self.journal.commands(task["task_id"])
                            if c["kind"] == "failover" and c["session_id"] == successor_id
                            and json.loads(c["payload"]).get("handoff_command_id") == handoff_command_id)
            fp = json.loads(failover["payload"])
            old = registry.get(task["host"], task["session_id"])
            entry = registry.get(task["host"], successor_id)
            if not old or not entry:
                return (False if any(e.get("failover_of") == task["session_id"] and
                                     e.get("status") in {"active", "starting", "uncertain"}
                                     for e in registry.list_entries(task["host"])) else None)
            confinement.guard_start_record(entry)
            path, branch = old["worktree_path"], old["branch"]
            if (handoff["task_id"] != task["task_id"] or handoff["kind"] != "send"
                    or handoff["session_id"] != successor_id or handoff["message_id"] != handoff_message_id
                    or hp.get("purpose") != "failover_handoff"
                    or hp.get("old_session_id") != task["session_id"]
                    or not re.fullmatch(r"[0-9a-f]{64}", str(hp.get("prompt_sha256") or ""))
                    or fp.get("old_session_id") != task["session_id"]
                    or not path or not branch
                    or entry.get("session_id") != successor_id
                    or entry.get("task_id") != task["task_id"]
                    or entry.get("failover_of") != task["session_id"]
                    or entry.get("shares_worktree_with") != task["session_id"]
                    or entry.get("worktree_path") != path or entry.get("branch") != branch
                    or entry.get("handoff_message_id") != handoff_message_id
                    or entry.get("handoff_command_id") != handoff_command_id
                    or entry.get("handoff_status") not in ({"pending"} if before_send else {"sent", "uncertain"})
                    or (not before_send and entry.get("handoff_frame_sha256") != hp.get("prompt_sha256"))):
                return False
            client = self.fleet.client(task["host"])
            if reader is None:
                async def reader(channel: str, params: dict) -> Any:
                    return await client.invoke(channel, params, retry_on_disconnect=False)
            meta = await reader("claude:get-session-meta", {"sessionId": successor_id})
            old_status = await reader("worktree:status", {"sessionId": task["session_id"]})
            new_status = await reader("worktree:status", {"sessionId": successor_id})
            root = await reader("git:getRoot", {"cwd": path})
        except (confinement.ConfinementRefused, KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
            return False
        except Exception:  # noqa: BLE001 - missing/unreachable BAT identity remains uncertain
            return None
        if (not isinstance(meta, dict) or not isinstance(old_status, dict)
                or not isinstance(new_status, dict) or root is None):
            return None
        return (confinement.verify(entry.get("confinement") or {}, meta)["status"] != "mismatch"
                and meta.get("cwd") == path and root == path
                and all(st.get("worktreePath") == path and st.get("branchName") == branch
                        for st in (old_status, new_status)))

    async def recover_failover(self, task: dict, *, successor_id: str,
                               handoff_message_id: str, handoff_command_id: str) -> dict | None:
        entry = registry.get(task["host"], successor_id) or {}
        if (entry.get("start_sent") is False and entry.get("handoff_status") == "pending"
                and "handoff_frame_sha256" in entry and entry["handoff_frame_sha256"] is None):
            try:
                # Reuse the reserved IDs and all original handoff guards. No frame
                # was handed to BAT; this is not a resend of an unsettled start.
                await self.failover(task, task["session_id"], successor_id,
                                    handoff_message_id=handoff_message_id, handoff_command_id=handoff_command_id)
            except Exception:  # noqa: BLE001 - unavailable or refused starts keep the journal unsettled
                return None
        identity = await self._verified_failover_successor(task, successor_id, handoff_message_id,
                                                           handoff_command_id)
        if identity is False:
            return {"identity_mismatch": True}
        if identity is None:
            return None
        # This proves a successor exists, not that Codex accepted its prompt.
        return {"session_id": successor_id, "marker": handoff_message_id}

    def _cwd(self, task: dict) -> str | None:
        row = registry.get(task["host"], task["session_id"])
        return (row.get("worktree_path") or row.get("cwd")) if row else None

    async def candidate_identity(self, task: dict) -> dict | None:
        cwd = self._cwd(task)
        return await self.verifier.identity(task, cwd) if cwd else None

    async def candidate_review_diff(self, task: dict) -> dict | None:
        """Read the complete small diff and changed paths; missing/large output escalates."""
        cwd, base = self._cwd(task), task.get("base_commit")
        if not cwd or not isinstance(base, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", base):
            return None
        try:
            path_rc, names = await self.verifier._run(
                task["host"], cwd, ("git", "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z",
                                    base, "HEAD", "--"),
                timeout=15)
            diff_rc, diff = await self.verifier._run(
                task["host"], cwd, ("git", "diff", "--no-ext-diff", "--no-renames", "--unified=1",
                                    base, "HEAD", "--"),
                timeout=15)
        except (OSError, ValueError, asyncio.TimeoutError):
            return None
        if (path_rc or diff_rc or not names or not diff or "\ufffd" in names
                or "Binary files " in diff or "GIT binary patch" in diff):
            return None
        paths = [name for name in names.split("\0") if name]
        return {"diff": diff, "paths": paths} if paths else None

    async def verification_failure(self, task: dict, evidence: dict) -> dict:
        kind = classify_failure(evidence)
        summary = redact_secrets(failure_tail(str(evidence.get("log_ref") or ""))) if kind == "code" else ""
        return {"kind": kind, "summary": summary}

    async def install_dependencies(self, task: dict) -> dict:
        task_control.require_authority(task)
        cwd = self._cwd(task)
        if not cwd:
            return {"ok": False, "reason": "worktree_unavailable"}
        guard = (task_control.FrameGuard(self.journal, task["task_id"], task["host"], task["session_id"],
                                        task["control_version"], action="verify", internal=True) if self.journal else None)
        if guard:
            guard.check()
        if isinstance(self.verifier, ObservedVerifier):
            result = await self.verifier.install_dependencies(task, cwd, before_run=guard.check if guard else None)
        else:
            result = await self.verifier.install_dependencies(task, cwd)
        if guard:
            guard.check()
        return result

    async def run_verification(self, task: dict) -> dict | None:
        task_control.require_authority(task)
        cwd = self._cwd(task)
        if not cwd:
            return None
        from .task_control import FrameGuard
        guard = (FrameGuard(self.journal, task["task_id"], task["host"], task["session_id"],
                            task["control_version"], action="verify", internal=True) if self.journal else None)
        if guard:
            guard.check()
        if isinstance(self.verifier, ObservedVerifier):
            evidence = await self.verifier.observe(task, cwd, before_run=guard.check if guard else None)
        else:
            evidence = await self.verifier.observe(task, cwd)
        if guard:
            guard.check()
        if not evidence:
            return None
        # The service, not Goose/Hermes, owns the PR #1 record after observing
        # process exit and matching clean commit/tree before and after the run.
        await lifecycle.session_record_verification(
            self.fleet, task["host"], task["session_id"], evidence["candidate_commit"],
            evidence["command"], evidence["exit_code"], "task-service:" + task["host"],
            evidence["log_ref"], confirm=True, _task_guard=guard,
        )
        return evidence
