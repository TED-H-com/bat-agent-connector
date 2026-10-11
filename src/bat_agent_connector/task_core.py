"""Deterministic task coordinator; BAT is a replaceable adapter and chat stays outside."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
import weakref
from typing import Protocol

from . import registry, task_control
from .errors import (
    BatError,
    ResourceReadOnly,
    TaskControlRefused,
    TaskDispatchCancelled,
    TaskIdentityMismatch,
    WriteRefused,
)
from .model_router import MinimalReviewGate, ModelRouter
from .operations import AMBIGUOUS, AmbiguousOutcome, StepFailed, _error_code
from .relay import parse_status
from .task_journal import Journal
from .task_recipes import limits, verification_reworks


class TaskAdapter(Protocol):
    async def start(self, task: dict, *, role: str, agent: str, session_id: str) -> str: ...
    async def recover_start(self, task: dict, *, role: str, session_id: str) -> bool: ...
    async def prepare_send(self, task: dict, session_id: str) -> dict: ...
    async def session_presence(self, task: dict, session_id: str) -> str: ...
    async def send(self, task: dict, session_id: str, text: str, message_id: str) -> dict: ...
    async def reconcile_send(self, task: dict, session_id: str, prompt_sha256: str,
                             before: dict, message_id: str) -> dict | None: ...
    async def read(self, task: dict, session_id: str, marker: str | None) -> dict: ...
    async def interrupt(self, task: dict, session_id: str) -> None: ...
    async def failover(self, task: dict, session_id: str, successor_id: str, *,
                       handoff_message_id: str, handoff_command_id: str) -> dict: ...
    async def recover_failover(self, task: dict, *, successor_id: str,
                               handoff_message_id: str, handoff_command_id: str) -> dict | None: ...
    async def candidate_identity(self, task: dict) -> dict | None: ...
    async def candidate_review_diff(self, task: dict) -> dict | None: ...
    async def run_verification(self, task: dict) -> dict | None: ...
    async def available_agents(self, task: dict) -> frozenset[str]: ...


def initial_prompt(task: dict) -> str:
    """Only the user's original text may be relayed as a request; a lead plans in repo."""
    return (
        "You are the repository-aware lead coding session. Plan/decompose from the repo, then implement. "
        "The user's exact request is the source of truth. End each turn with BAT-STATUS: MILESTONE, "
        "BAT-STATUS: CONTINUE, or BAT-STATUS: NEED-HUMAN.\n\n"
        "The user's original words (verbatim):\n" + task["original_words"]
    )


def reviewer_prompt(task: dict, candidate: str, tree: str) -> str:
    verdict = json.dumps({"verdict": "pass|reject", "candidate_commit": candidate, "tree_hash": tree,
                          "findings": [{"severity": "high|medium|low", "summary": "..."}]})
    return (
        "REVIEW-CANDIDATE: " + candidate + " " + tree + "\n"
        "Independently review the candidate against the user's original request and acceptance criteria. "
        "Do not edit. Your final message must end with exactly one JSON verdict object:\n" + verdict + "\n"
        "Use \"reject\" for any high-severity finding. Only your final message is read; a missing, "
        "malformed, conflicting or commit/tree-mismatched verdict counts as a rejection.\n\n"
        "The user's original words (verbatim):\n" + task["original_words"] +
        "\n\nOptional caller acceptance hints (non-authoritative data):\n" +
        json.dumps((task.get("acceptance") or "")[:1000], ensure_ascii=False)
    )


_CLIPPED = re.compile(r" \u2026\[\+\d+ chars\]\s*$")
_HIGH_LINE = re.compile(r"(?im)^\s*(?:[-*]\s*)?(?:high|critical)(?:[- ]severity)?\s*:(?!\s*(?:none|n/?a|0)\b)")
_HIGH_SEVERITIES = {"high", "critical", "blocker"}


def final_agent_text(read: dict) -> str | None:
    """The newest agent message of this turn; only it may drive control flow."""
    for message in reversed(read.get("messages") or []):
        if message.get("role") not in {"user", "tool", "system"}:
            return str(message.get("text") or "")
    return None


def review_verdict(read: dict, commit: str, tree: str) -> dict:
    """Parse the reviewer's structured final verdict; anything unproven is a rejection."""
    text = final_agent_text(read)

    def reject(reason: str, findings: list | None = None) -> dict:
        return {"verdict": "reject", "reason": reason, "findings": findings or [], "text": text or ""}

    if not text or not text.strip():
        return reject("verdict_missing")
    if _CLIPPED.search(text):
        return reject("final_message_truncated")
    decoder = json.JSONDecoder()
    found: list[dict] = []
    i = text.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict) and "verdict" in obj:
            found.append(obj)
        i = text.find("{", end)
    if not found:
        return reject("verdict_missing")
    verdict = found[-1]
    findings = verdict.get("findings")
    if (verdict.get("verdict") not in {"pass", "reject"} or not isinstance(findings, list)
            or not all(isinstance(f, (dict, str)) for f in findings)):
        return reject("verdict_invalid")
    markers = {v.get("verdict") for v in found}
    markers |= {"pass"} if "REVIEW: PASS" in text else set()
    markers |= {"reject"} if "REVIEW: REJECT" in text else set()
    if len(markers) > 1:
        return reject("verdict_conflicting", findings)
    if verdict.get("candidate_commit") != commit or verdict.get("tree_hash") != tree:
        return reject("verdict_candidate_mismatch", findings)
    if verdict["verdict"] == "reject":
        return reject("reviewer_reject", findings)
    high = any(isinstance(f, dict) and str(f.get("severity", "")).strip().lower() in _HIGH_SEVERITIES
               for f in findings)
    if high or _HIGH_LINE.search(text):
        return reject("pass_with_high_findings", findings)
    return {"verdict": "pass", "reason": "reviewer_pass", "findings": findings, "text": text}


_QUOTA = re.compile(r"quota (?:exhausted|exceeded)|usage limit reached|out of credits", re.I)


def classify_read(read: dict) -> str:
    if read.get("pending"):
        return "waiting_permission" if read["pending"].get("kind") == "permission" else "needs_ted"
    output = "\n".join(str(m.get("text") or "") for m in read.get("messages") or [] if m.get("role") != "user")
    if _QUOTA.search(output):
        return "quota_limited"
    if read.get("turn_done") is not True:
        return "running"
    # Only the last BAT-STATUS line of the final agent message counts; quoted
    # markers earlier in the turn cannot move the task.
    final = final_agent_text(read)
    status = parse_status(final) if final and not _CLIPPED.search(final) else None
    if status and status["kind"] == "NEED_HUMAN":
        return "needs_ted"
    if status and status["kind"] == "MILESTONE":
        return "verifying"
    return "continue"


class TaskCoordinator:
    def __init__(self, journal: Journal, adapter: TaskAdapter, *, max_continuations: int = 5,
                 max_review_rejections: int = 2, router: ModelRouter | None = None,
                 minimal_review_gate: MinimalReviewGate | None = None,
                 verification_quiet_s: float = 0.0):
        self.journal = journal
        self.adapter = adapter
        self.max_continuations = max_continuations
        self.max_review_rejections = max_review_rejections
        self.router = router
        self.minimal_review_gate = minimal_review_gate
        self._writers: dict[tuple[str, str], asyncio.Lock] = {}
        self._task_locks: dict[str, asyncio.Lock] = {}
        self._failover_authorities = weakref.WeakSet()
        self.verification_quiet_s = max(0.0, verification_quiet_s)
        self._verification_stability: dict[str, tuple[tuple[str, str] | None, float]] = {}
        self._verification_activity: dict[str, float] = {}
        if getattr(adapter, "fleet", None) is not None:
            adapter.fleet.task_coordinator = self

    def cleanup_verdict(self, item):
        from .task_cleanup import verdict
        return verdict(self, item)

    def cleanup_authority(self, ctx, item):
        from .task_cleanup import authority
        return authority(self, ctx, item)

    def _lock(self, host: str, sid: str) -> asyncio.Lock:
        return self._writers.setdefault((host, sid), asyncio.Lock())

    async def session_control(self, task_id: str, host: str, sid: str, action: str,
                              params: dict, fn) -> dict:
        from .task_control import FrameGuard, check
        context = None
        if params.get("operation_id") and getattr(self, "operations", None):
            from .operations import OpContext
            from .task_control import check_binding
            context = OpContext(self.operations, self.operations._row(params["operation_id"]))
            check_binding(context)
            if action == "permissions":
                from .session_permissions import _check_owner
                _check_owner(context, self.adapter.fleet, host, sid)
            if context.admission_binding and context.admission_binding.get("task_id") not in {None, task_id}:
                raise TaskControlRefused("TASK_BINDING_MISMATCH", "session task owner changed since admission")
            params = {**params, "control_version": context.effective_preconditions.get("control_version")}
        # The original permissions command may continue only its missing frame intents.
        prior_command_id = None
        if context and action == "permissions":
            prior_command_id = (context.op.get("external_refs") or {}).get("command_id")
            if prior_command_id:
                prior = self.journal.command_get(prior_command_id)
                if (prior["kind"] != "permissions" or prior["task_id"] != task_id or prior["session_id"] != sid
                        or json.loads(prior["payload"]).get("operation_id") != context.operation_id):
                    raise TaskControlRefused("TASK_BINDING_MISMATCH", "permissions command identity changed")
        # Admission never waits behind verification. Freeze the version before any lock/await.
        admitted = check(self.journal, task_id, host, sid, action, params.get("control_version"), command_id=prior_command_id)
        params = {**params, "control_version": admitted["control_version"]}
        # Pause commits independently of this lock, so it can invalidate an in-flight verifier/send.
        async with self._task_locks.setdefault(task_id, asyncio.Lock()):
            task = check(self.journal, task_id, host, sid, action, params["control_version"], command_id=prior_command_id)
            async with self._lock(host, sid):
                task = check(self.journal, task_id, host, sid, action, task["control_version"], command_id=prior_command_id)
                before = await self.adapter.prepare_send(task, sid) if action == "send" else {}
                resolved_prompt = False
                if action == "answer" and not params.get("tool_use_id"):
                    from . import service
                    answers, permission = params.get("answers"), params.get("permission")
                    if (answers is None) == (permission is None):
                        raise WriteRefused("pass exactly one of answers (for ask-user) or permission (allow|deny)")
                    client = self.adapter.fleet.client(host)
                    tab, _ = await service._resolve_session(client, sid)
                    meta = await service._meta(client, sid)
                    if not service._state_safe(service.agent_kind(tab.get("agentPreset")), meta):
                        raise WriteRefused("session is not loaded on the host; nothing to answer")
                    state = await client.invoke("claude:get-session-state", {"sessionId": sid})
                    field = "pendingAskUser" if answers is not None else "pendingPermission"
                    prompt = state.get(field) if isinstance(state, dict) else None
                    if not isinstance(prompt, dict) or not prompt.get("toolUseId"):
                        label = "ask-user question" if answers is not None else "permission request"
                        raise WriteRefused("session has no pending " + label)
                    params = {**params, "tool_use_id": prompt["toolUseId"]}
                    resolved_prompt = True
                if context:
                    check_binding(context)
                check(self.journal, task_id, host, sid, action, task["control_version"], command_id=prior_command_id)
                key = params.get("operation_id") or "legacy:" + str(uuid.uuid4())
                payload = {"purpose": "runtime:" + action, "control_version": task["control_version"],
                           "before": before, "operation_id": params.get("operation_id"),
                           "prompt_sha256": hashlib.sha256(str(params.get("text", "")).encode()).hexdigest(),
                           "tool_use_id": params.get("tool_use_id"), "mode": params.get("mode")}
                if resolved_prompt:
                    payload["tool_use_id_source"] = "service"
                def intent():
                    with self.journal.tx():
                        command, fresh = self.journal.command(task_id, action, sid, payload, "runtime:" + key)
                        if action == "send" and params.get("message_id"):
                            self.journal.db.execute("UPDATE commands SET message_id=? WHERE command_id=?",
                                                    (params["message_id"], command["command_id"]))
                            command = self.journal.command_get(command["command_id"])
                        return {**command, "fresh": fresh}
                command = context.effect("task_command", intent, refs=task_control.command_refs) if context else intent()
                fresh = command["fresh"]
                if not fresh:
                    raise TaskControlRefused("TASK_RECONCILIATION_REQUIRED", "existing command must be read back")
                guard = FrameGuard(self.journal, task_id, host, sid, task["control_version"],
                                   command["command_id"], action, prompt_sha256=payload["prompt_sha256"] if action == "send" else None)
                params = dict(params)
                params["_task_guard"] = guard
                if action == "send":
                    params["message_id"] = command["message_id"]
                    params["retry_on_disconnect"] = False
                try:
                    result = await fn(self.adapter.fleet, host, sid, **params)
                except WriteRefused:
                    if context and action == "permissions":
                        from .session_permissions import has_dispatched_receipt, mark_task_uncertain
                        if guard.frames or has_dispatched_receipt(context):
                            mark_task_uncertain(context, command)
                        else:
                            self.journal.command_status(command["command_id"], "rejected")
                        raise
                    self.journal.command_status(command["command_id"], "uncertain" if guard.frames else "rejected")
                    if guard.frames:
                        self.journal.change(task_id, "uncertain")
                    raise
                except Exception as exc:
                    if context and action == "permissions":
                        from .session_permissions import has_dispatched_receipt, mark_task_uncertain
                        if guard.frames or has_dispatched_receipt(context):
                            mark_task_uncertain(context, command)
                        else:
                            self.journal.command_status(command["command_id"], "rejected")
                        raise
                    refused = (action in {"send", "answer", "interrupt"}
                               and isinstance(exc, BatError) and not isinstance(exc, AMBIGUOUS))
                    uncertain = bool(guard.frames) and not refused
                    self.journal.command_status(command["command_id"], "uncertain" if uncertain else "rejected")
                    if uncertain:
                        self.journal.change(task_id, "uncertain")
                        if context and not isinstance(exc, (*AMBIGUOUS, OSError)):
                            raise AmbiguousOutcome("runtime control reply could not be recorded: " + type(exc).__name__) from exc
                    elif context and isinstance(exc, (*AMBIGUOUS, OSError)):
                        # Transport loss before the effect frame has a definitive no-send outcome.
                        raise StepFailed(_error_code(exc), str(exc)) from exc
                    raise
                if action == "send" and not result.get("accepted"):
                    with self.journal.tx():
                        self.journal.command_status(command["command_id"], "rejected")
                        self.journal.change(task_id, "needs_ted")
                    return result
                if action == "send" and before.get("agent_kind") == "codex" and result.get("turn_attribution") != "exact_echo":
                    # Preserve the legacy reply, but let the original command readback own task progress.
                    self.journal.command_status(command["command_id"], "uncertain", marker=result.get("turn_marker"))
                    await self._reconcile_command(self.journal.change(task_id, "uncertain"),
                                                  self.journal.command_get(command["command_id"]))
                    return result
                with self.journal.tx():
                    self.journal.command_status(command["command_id"], "accepted" if action == "send" else "settled",
                                                marker=result.get("turn_marker"))
                    if action == "send":
                        self.journal.change(task_id, "running", fields={"turn_marker": result.get("turn_marker")})
                return result

    async def _route(self, task: dict, step: str, step_type: str, *, high_stakes: bool = False,
                     provider: str | None = None, reason: str | None = None) -> None:
        """Model choice belongs to Goose. The service records no per-step route."""
        return None

    async def _available_agents(self, task: dict) -> frozenset[str]:
        reader = getattr(self.adapter, "available_agents", None)
        try:
            found = await reader(task) if callable(reader) else None
        except TaskControlRefused:
            raise
        except Exception:  # noqa: BLE001 - unknown availability means Codex only
            found = None
        return frozenset(a for a in (found or ()) if a in {"claude", "codex"}) | {"codex"}

    @staticmethod
    def _pick_agent(role: str, task: dict, available: frozenset[str]) -> tuple[str, str]:
        """The BAT agent a session really runs, by the user's order.

        Review: Claude Opus 5.5, then Codex (AGY is not a BAT runtime), from a
        different family than the lead when possible. Lead: the task's
        lead_agent while it is available, else Codex.
        """
        if role == "lead":
            lead = task["lead_agent"]
            return (lead, "lead_agent") if lead in available else ("codex", "lead_fallback_unavailable")
        order = [a for a in ("claude", "codex") if a in available]
        other = [a for a in order if a != task["lead_agent"]]
        return (other[0], "review_other_family") if other else (order[0], "review_same_family_only")

    async def pause(self, task_id: str, *, abort_current: bool = False) -> dict:
        task_control.require_authority(self.journal.get(task_id))
        task = self.journal.pause(task_id, abort_current=abort_current)
        if abort_current and task["session_id"]:
            async with self._lock(task["host"], task["session_id"]):
                await self.adapter.interrupt(task, task["session_id"])
        return self.journal.get(task_id)

    async def tick(self, task_id: str) -> dict:
        from . import controller_authority
        task = self.journal.get(task_id)
        with controller_authority.use(task_control.authority(task), self.journal):
            return await self._tick_bound(task_id)

    async def _tick_bound(self, task_id: str) -> dict:
        async with self._task_locks.setdefault(task_id, asyncio.Lock()):
            try:
                return await self._tick(task_id)
            except (TaskControlRefused, ResourceReadOnly) as exc:
                # Control cancellation is not a failed verifier or an unknown BAT outcome.
                logging.info("Task %s tick cancelled by %s", task_id[:8], exc.code)
                return self.journal.get(task_id)

    def _check_control(self, task: dict) -> dict:
        """Keep awaited work bound to its original task incarnation before local effects."""
        return task_control.check_incarnation(self.journal, task)

    async def _tick(self, task_id: str) -> dict:
        task = self.journal.get(task_id)
        if task["paused"] or task["state"] in {"done", "failed", "human_owned", "needs_ted"}:
            return task
        self._check_control(task)
        cmds = self.journal.commands(task_id)
        pending = next((c for c in cmds if c["kind"] == "failover" and
                        c["status"] in {"intent", "uncertain"}), None)
        if pending is None:
            pending = next((c for c in reversed(cmds)
                            if c["status"] in {"intent", "needs_review", "uncertain"}), None)
        if pending:
            operation_id = json.loads(pending["payload"]).get("operation_id")
            if pending["kind"] == "permissions" and operation_id and getattr(self, "operations", None):
                from .operations import OpContext
                from .session_permissions import mark_task_uncertain
                op = self.operations._row(operation_id)
                if op and op["action"] == "session.permissions":
                    mark_task_uncertain(OpContext(self.operations, op), pending)
                    self.operations.kick()
                    return self.journal.get(task_id)
            if pending["kind"] == "send" and operation_id and getattr(self, "operations", None):
                from .operations import OpContext
                context = OpContext(self.operations, self.operations._row(operation_id))
                try:
                    await self._recover_unsent_send(task, pending, operation=context)
                except StepFailed:
                    return self.journal.get(task_id)  # a committed refusal needs no BAT proof
            # An intent may have reached BAT before a crash. Never dispatch it again.
            if pending["status"] in {"intent", "needs_review"}:
                self.journal.command_status(pending["command_id"], "uncertain")
                task = self.journal.change(task_id, "uncertain")
            return await self._reconcile_command(task, pending)
        if task["state"] == "uncertain":
            # A presence probe before any prompt may have been inconclusive
            # during BAT restart. No send intent exists, so checking again is
            # safe; a prompt is still created only after positive identity.
            sid = task.get("reviewer_session_id") or task.get("session_id")
            if sid and not any(c["kind"] == "send" and c["session_id"] == sid for c in cmds):
                if await self.adapter.session_presence(task, sid) == "present":
                    return self.journal.change(task_id, "verifying" if task.get("reviewer_session_id")
                                               else "accepted", event="session_presence_restored")
            return task
        task_control.require_authority(task)
        rejected = next((c for c in reversed(cmds) if c["kind"] == "send"
                         and c["session_id"] == task.get("session_id")), None)
        if rejected and rejected["status"] == "rejected":
            payload = json.loads(rejected["payload"])
            if (not payload.get("operation_id") and not str(payload.get("purpose")).startswith("runtime:")
                    and payload.get("control_version") == task["control_version"]):
                return await self._recover_unsent_send(task, rejected)
        if task["state"] == "queued":
            return await self._start(task, role="lead")
        if task["state"] == "verifying":
            return await self._verify_and_review(task)
        if task["state"] == "quota_limited":
            # No mid-task failover. Goose (or the user) picks another model; the service stops.
            return self.journal.change(task["task_id"], "needs_ted", fields={
                "result": "Provider quota exhausted; not failing over inside the service"})
        if task["state"] == "accepted" and task["session_id"] and not any(
            c["kind"] == "send" and c["session_id"] == task["session_id"]
            and c["status"] != "cancelled" for c in cmds
        ):
            return await self._send(task, task["session_id"], initial_prompt(task), "lead:initial")
        if task["state"] in {"accepted", "running", "dispatching", "waiting_permission"}:
            return await self._observe(task, cmds)
        return task

    async def _start(self, task: dict, *, role: str) -> dict:
        task_control.require_authority(task)
        stage = "review" if role == "reviewer" else "planning"
        agent, reason = self._pick_agent(role, task, await self._available_agents(task))
        await self._route(task, f"{stage}:start:{task.get('review_commit') or 'lead'}:"
                          f"{task['control_version']}:{task['review_rejections']}:"
                          f"{task['session_replacements']}", stage, high_stakes=True,
                          provider=agent, reason=reason)
        task = self._check_control(task)
        if task["paused"]:
            return task
        candidate_key = task.get("review_commit") if role == "reviewer" else "lead"
        # A Claude reviewer that hits quota is replaced by Codex for the same
        # candidate, so the agent is part of that start's idempotency key.
        key = (f"{task['task_id']}:{role}:start:{candidate_key}:{task['review_rejections']}:"
               f"{task['control_version']}:{task['session_replacements']}"
               + (":claude" if role == "reviewer" and agent == "claude" else ""))
        warm_id = None
        if role == "lead" and task.get("task_path") == "minimal" and not task.get("base_branch"):
            finder = getattr(self.adapter, "find_warm", None)
            if callable(finder):
                try:
                    warm_id = await finder({**task, "lead_agent": agent})
                except TaskControlRefused:
                    raise
                except Exception:  # noqa: BLE001 - an unproven warm session is never adopted
                    warm_id = None
        self._check_control(task)
        sid = warm_id or str(uuid.uuid4())
        command, fresh = self.journal.command(task["task_id"], "start_" + role, sid,
                                               {"role": role, "agent": agent,
                                                "warm_session_id": warm_id,
                                                "control_version": task["control_version"],
                                                "start_sent": None if warm_id else False}, key)
        if not fresh:
            return self.journal.change(task["task_id"], "uncertain")
        self.journal.change(task["task_id"], "dispatching",
                            fields={"lead_agent": agent} if role == "lead" and agent != task["lead_agent"] else None)
        task = self.journal.get(task["task_id"])
        if task["paused"]:
            self.journal.command_status(command["command_id"], "cancelled")
            return self.journal.change(task["task_id"], "verifying" if role == "reviewer" else "queued")
        try:
            started_sid = await self.adapter.start({**task, "_warm_session_id": warm_id},
                                                   role=role, agent=agent, session_id=sid)
            if started_sid != sid:
                raise TaskIdentityMismatch("BAT start changed the reserved task session ID")
        except TaskControlRefused as exc:
            if exc.code != "TASK_OWNER_UNAVAILABLE":
                self.journal.command_status(command["command_id"], "cancelled")
                current = self.journal.get(task["task_id"])
                if current["state"] == "dispatching":
                    self.journal.change(task["task_id"], "verifying" if role == "reviewer" else "queued")
            raise
        except Exception as exc:
            if getattr(exc, "sent", None) is False:
                code = getattr(exc, "code", "REFUSED")
                self.journal.command_status(command["command_id"], "rejected")
                self.journal._event(task["task_id"], "start_rejected", {
                    "command_id": command["command_id"], "session_id": sid, "role": role, "code": code})
                return self.journal.change(task["task_id"], "needs_ted", fields={"result": str(exc)})
            self.journal.command_status(command["command_id"], "uncertain")
            current = self.journal.get(task["task_id"])
            if current["paused"] or current["control_version"] != task["control_version"]:
                return current
            return self.journal.change(task["task_id"], "uncertain")
        # Evidence only; engine, recipes and command identity stay unchanged.
        from . import registry
        entry = registry.get(task["host"], sid) or {}
        # start() persisted its original creation proof before the BAT frame.
        # Merge into that durable receipt, not the pre-reservation command copy.
        payload = json.loads(self.journal.command_get(command["command_id"])["payload"])
        payload.update({k: entry[k] for k in ("confinement", "write_scope", "permission_mode_claude", "agent_params", "start_sent")
                        if k in entry})
        self.journal.db.execute("UPDATE commands SET payload=? WHERE command_id=?",
                                (json.dumps(payload), command["command_id"]))
        self.journal.command_status(command["command_id"], "settled")
        self.journal.provider_use(agent, "success")  # a real session on this provider
        self.journal.add_branch(task["task_id"], session_id=sid, provider=agent, role=role,
                                reason=("warm_reuse" if warm_id else "vanished_replacement"
                                        if role == "lead" and task["session_replacements"] else "start"),
                                parent_branch_id=(task["branches"][-1]["branch_id"]
                                                                   if task["branches"] else None))
        field = "reviewer_session_id" if role == "reviewer" else "session_id"
        self.journal.change(task["task_id"], "verifying" if role == "reviewer" else "accepted", fields={field: sid})
        if self.journal.get(task["task_id"])["paused"]:
            return self.journal.get(task["task_id"])
        prompt = (reviewer_prompt(task, task["review_commit"], task["review_tree"])
                  if role == "reviewer" else initial_prompt(task))
        return await self._send(self.journal.get(task["task_id"]), sid, prompt, role + ":initial")

    async def _recover_unsent_send(self, task: dict, cmd: dict, *, operation=None) -> dict | None:
        """Replay terminal local send outcomes without reconciling or dispatching a frame."""
        if operation and cmd["status"] in {"intent", "needs_review", "uncertain", "rejected"}:
            failed = operation.service.db.execute(
                "SELECT error FROM operation_steps WHERE operation_id=? AND name='task_dispatch' AND status='failed'",
                (operation.operation_id,)).fetchone()
            if failed:
                # The step can commit before the command status, including across a process crash.
                if cmd["status"] != "rejected":
                    self.journal.command_status(cmd["command_id"], "rejected")
                error = json.loads(failed["error"] or "{}")
                raise StepFailed(error.get("code", "STEP_FAILED"), error.get("message", "step failed earlier"))
        if cmd["status"] not in {"cancelled", "rejected"}:
            return None
        task = self.journal.get(task["task_id"])
        payload = json.loads(cmd["payload"])
        version = payload.get("control_version")
        if cmd["status"] == "cancelled":
            if operation:
                if task["paused"]:
                    raise TaskControlRefused("TASK_PAUSED", "task paused before send dispatch")
                if version is not None and version != task["control_version"]:
                    from .task_control import check
                    check(self.journal, task["task_id"], task["host"], cmd["session_id"], "send", version)
                raise TaskControlRefused("TASK_SEND_NOT_DISPATCHED", "task send command was cancelled before dispatch")
            return task
        # Do not overwrite a later control or a local outcome already committed before the crash.
        if (task["paused"] or version is not None and version != task["control_version"]
                or cmd["session_id"] not in {task.get("session_id"), task.get("reviewer_session_id")}
                or task["state"] not in {"accepted", "running", "verifying", "dispatching"}
                or any(c["kind"] == "send" and c["session_id"] == cmd["session_id"]
                       and c["created_at"] > cmd["created_at"] for c in self.journal.commands(task["task_id"]))):
            return task
        if payload.get("purpose") == "lead:initial" and task["state"] == "accepted":
            try:
                presence = await self.adapter.session_presence(task, cmd["session_id"])
            except Exception:  # noqa: BLE001 - no presence proof means a human decides, never a resend
                presence = "unknown"
            current = self.journal.get(task["task_id"])
            if current != task:
                return current
            if presence == "vanished":
                return self.journal.mark_initial_session_vanished(task["task_id"], cmd["session_id"])
        return self.journal.change(task["task_id"], "needs_ted")

    async def _send(self, task: dict, sid: str, text: str, purpose: str,
                    *, prepared_command: dict | None = None, operation=None) -> dict:
        expected_version = task["control_version"]
        def paused_result(current):
            if operation:
                raise TaskControlRefused("TASK_PAUSED", "task paused before send dispatch")
            return current
        async with self._lock(task["host"], sid):
            task = self.journal.get(task["task_id"])
            if operation:
                task_control.replay_command_refs(operation)
                receipt = operation.service.db.execute(
                    "SELECT response FROM operation_steps WHERE operation_id=? AND name='task_send_result' AND status='succeeded'",
                    (operation.operation_id,)).fetchone()
                if receipt:
                    return json.loads(receipt["response"])
                receipt = operation.service.db.execute(
                    "SELECT response FROM operation_steps WHERE operation_id=? AND name='task_send_command' AND status='succeeded'",
                    (operation.operation_id,)).fetchone()
                if receipt:
                    cmd = self.journal.command_get(json.loads(receipt["response"])["command_id"])
                    unsent = await self._recover_unsent_send(task, cmd, operation=operation)
                    if unsent is not None:
                        return unsent
                    if cmd["status"] in {"intent", "needs_review", "uncertain"}:
                        dispatch = operation.service.db.execute(
                            "SELECT response FROM operation_steps WHERE operation_id=? AND name='task_dispatch' AND status='succeeded'",
                            (operation.operation_id,)).fetchone()
                        if dispatch:
                            payload = json.loads(cmd["payload"])
                            return await self._finish_send(task, cmd, json.loads(dispatch["response"]),
                                initial_lead=payload["purpose"] == "lead:initial", operation=operation)
                        self.journal.command_status(cmd["command_id"], "uncertain")
                        task = await self._reconcile_command(task, cmd)
                    if self.journal.command_get(cmd["command_id"])["status"] in {"accepted", "settled"}:
                        self._settle_dispatch_receipt(operation, self.journal.command_get(cmd["command_id"]))
                        return operation.effect("task_send_result", lambda: self.journal.get(task["task_id"]))
                    return self.journal.change(task["task_id"], "uncertain")
            if task["paused"]:
                return paused_result(task)
            task_control.require_authority(task)
            send_type = "review" if purpose.startswith("reviewer:") else "implementation"
            # Record the agent that really receives this prompt (the session's branch).
            agent = next((b["provider"] for b in reversed(task["branches"]) if b["session_id"] == sid), None)
            await self._route(task, f"{send_type}:send:{purpose}:{sid}:"
                              f"{task['control_version']}:{task['continuations']}:"
                              f"{task['review_rejections']}", send_type,
                              high_stakes=send_type == "review",
                              provider=agent, reason="session_agent" if agent else None)
            task = self.journal.get(task["task_id"])
            if task["paused"]:
                return paused_result(task)
            initial_lead = purpose == "lead:initial" and prepared_command is None and sid == task["session_id"]
            initial_reviewer = (purpose == "reviewer:initial" and prepared_command is None
                                and sid == task.get("reviewer_session_id"))
            if initial_lead or initial_reviewer:
                presence = await self.adapter.session_presence(task, sid)
                task = self.journal.get(task["task_id"])
                if task["paused"]:
                    return paused_result(task)
                if initial_lead and presence == "vanished":
                    return self.journal.mark_initial_session_vanished(task["task_id"], sid)
                if presence != "present":
                    return self.journal.change(task["task_id"], "uncertain",
                                               event="initial_session_unproven")
            if prepared_command:
                cmd = self.journal.command_get(prepared_command["command_id"])
                payload = json.loads(cmd["payload"])
                if (cmd["status"] != "needs_review" or cmd["session_id"] != sid or
                        payload.get("prompt_sha256") != hashlib.sha256(text.encode()).hexdigest()):
                    raise ValueError("prepared operator prompt does not match journal intent")
                before = payload["before"]
                if operation:
                    operation.effect("task_send_command", lambda: {**cmd, "fresh": True},
                                     refs=task_control.command_refs)
            else:
                key = (f"{task['task_id']}:{purpose}:{task['review_rejections']}:{task['continuations']}:"
                       f"{task['control_version']}:"
                       f"{task.get('review_commit') if sid == task.get('reviewer_session_id') else sid}"
                       + (f":vf{task['verification_failures']}" if task.get("verification_failures") else ""))
                before = await self.adapter.prepare_send(task, sid)
                current = self.journal.get(task["task_id"])
                if current["paused"]:
                    return paused_result(current)
                if operation:
                    from .task_control import check
                    check(self.journal, task["task_id"], task["host"], sid, "send", expected_version)
                def command_effect():
                    cmd, fresh = self.journal.command(task["task_id"], "send", sid,
                        {"purpose": purpose, "before": before, "control_version": task["control_version"],
                         "operation_id": operation.operation_id if operation else None,
                         "prompt_sha256": hashlib.sha256(text.encode()).hexdigest()},
                        "op:" + operation.operation_id if operation else key)
                    return {**cmd, "fresh": fresh}
                cmd = operation.effect("task_send_command", command_effect,
                                       refs=task_control.command_refs) if operation else command_effect()
                fresh = cmd["fresh"]
                if not fresh:
                    unsent = await self._recover_unsent_send(task, cmd)
                    if unsent is not None:
                        return unsent
                    return self.journal.change(task["task_id"], "uncertain")
            if initial_lead or initial_reviewer:
                presence = await self.adapter.session_presence(task, sid)
                task = self.journal.get(task["task_id"])
                if task["paused"]:
                    self.journal.command_status(cmd["command_id"], "cancelled")
                    return paused_result(task)
                if presence != "present":
                    self.journal.command_status(cmd["command_id"],
                                                "rejected" if presence == "vanished" else "uncertain")
                    if initial_lead and presence == "vanished":
                        return self.journal.mark_initial_session_vanished(task["task_id"], sid)
                    return self.journal.change(task["task_id"], "uncertain", event="initial_session_unproven")
            task = self.journal.get(task["task_id"])
            if task["paused"]:
                self.journal.command_status(cmd["command_id"], "cancelled")
                return paused_result(task)
            reconciled = False
            try:
                async def dispatch():
                    if operation:
                        from .task_control import check_binding
                        check_binding(operation)
                    return await self.adapter.send(task, sid, text, cmd["message_id"])
                async def readback(_):
                    proof = await self.adapter.reconcile_send(task, sid, hashlib.sha256(text.encode()).hexdigest(),
                                                              before, cmd["message_id"])
                    return proof if proof and proof.get("accepted") and proof.get("turn_attribution") == "exact_echo" else None
                r = await operation.step("task_dispatch", dispatch, request={"command_id": cmd["command_id"]},
                                         reconcile=readback) if operation else await dispatch()
            except TaskDispatchCancelled:
                self.journal.command_status(cmd["command_id"], "cancelled")
                return self.journal.get(task["task_id"])
            except StepFailed as exc:
                if operation:
                    self.journal.command_status(cmd["command_id"], "rejected")
                    raise
                current = self.journal.get(task["task_id"])
                if current["paused"] or current["control_version"] != task["control_version"]:
                    self.journal.command_status(cmd["command_id"], "cancelled")
                    return current
                with self.journal.tx():
                    self.journal.command_status(cmd["command_id"], "rejected")
                    self.journal.note(task["task_id"], "send_rejected", {
                        "command_id": cmd["command_id"], "code": exc.code})
                if initial_lead:
                    try:
                        presence = await self.adapter.session_presence(current, sid)
                    except Exception:  # noqa: BLE001 - a failed read is not proof of disappearance
                        presence = "unknown"
                    current = self.journal.get(task["task_id"])
                    if current["paused"] or current["control_version"] != task["control_version"]:
                        self.journal.command_status(cmd["command_id"], "cancelled")
                        return current
                    if presence == "vanished":
                        return self.journal.mark_initial_session_vanished(task["task_id"], sid)
                return self.journal.change(task["task_id"], "needs_ted", fields={
                    "result": "Send rejected before BAT prompt: " + exc.code})
            except TaskControlRefused as exc:
                if exc.code == "TASK_OWNER_UNAVAILABLE":
                    raise  # the next owner owns the intent and its settlement
                self.journal.command_status(cmd["command_id"], "cancelled")
                return self.journal.get(task["task_id"])
            except WriteRefused:
                # Local streaming/rate guard rejected before BAT send-message.
                current = self.journal.get(task["task_id"])
                if current["paused"] or current["control_version"] != task["control_version"]:
                    self.journal.command_status(cmd["command_id"], "cancelled")
                    return current
                self.journal.command_status(cmd["command_id"], "rejected")
                if initial_lead and await self.adapter.session_presence(task, sid) == "vanished":
                    return self.journal.mark_initial_session_vanished(task["task_id"], sid)
                return self.journal.change(task["task_id"], "needs_ted")
            except Exception:
                # The BAT frame may have been accepted before its reply was lost.
                # Read only; the durable send intent is never dispatched again.
                try:
                    r = await asyncio.wait_for(self.adapter.reconcile_send(
                        task, sid, hashlib.sha256(text.encode()).hexdigest(), before,
                        cmd["message_id"]), timeout=5)
                except TaskControlRefused:
                    raise
                except Exception:  # noqa: BLE001 - missing proof stays uncertain
                    r = None
                if (not r or not r.get("accepted") or r.get("turn_attribution") != "exact_echo"
                        or not r.get("turn_marker")):
                    self.journal.command_status(cmd["command_id"], "uncertain")
                    return self.journal.change(task["task_id"], "uncertain")
                reconciled = True
            return await self._finish_send(task, cmd, r, initial_lead=initial_lead,
                                           reconciled=reconciled, operation=operation)

    async def _finish_send(self, task: dict, cmd: dict, r: dict, *, initial_lead: bool,
                           reconciled: bool = False, operation=None) -> dict:
        """Apply the same receipt checks to a live dispatch and a saved dispatch response."""
        sid = cmd["session_id"]
        before = json.loads(cmd["payload"])["before"]
        if not r.get("accepted"):
            self.journal.command_status(cmd["command_id"], "rejected")
            if initial_lead and await self.adapter.session_presence(task, sid) == "vanished":
                return self.journal.mark_initial_session_vanished(task["task_id"], sid)
            return self.journal.change(task["task_id"], "needs_ted")
        if before.get("agent_kind") == "codex" and r.get("turn_attribution") != "exact_echo":
            # An accepted Codex ACK can still carry only a timestamp. Read
            # back the exact user echo before deciding it is unproven.
            try:
                proof = await asyncio.wait_for(self.adapter.reconcile_send(
                    task, sid, json.loads(cmd["payload"])["prompt_sha256"], before,
                    cmd["message_id"]), timeout=5)
            except TaskControlRefused:
                raise
            except Exception:  # noqa: BLE001 - absent proof stays uncertain
                proof = None
            if (proof and proof.get("accepted") and proof.get("turn_attribution") == "exact_echo"
                    and proof.get("turn_marker")):
                r = proof
                reconciled = True
            else:
                # A later timestamped assistant reply cannot own this turn.
                marker = r.get("turn_marker") or before.get("before_cursor")
                self.journal.command_status(cmd["command_id"], "uncertain", marker=marker)
                return self.journal.change(task["task_id"], "uncertain")
        marker = r.get("turn_marker") or (before.get("before_cursor") if before.get("agent_kind") == "codex"
                                           else cmd["message_id"])
        if not marker:
            self.journal.command_status(cmd["command_id"], "uncertain")
            return self.journal.change(task["task_id"], "uncertain")
        def delivered():
            self.journal.command_status(cmd["command_id"], "accepted", marker=marker)
            if operation:
                self._settle_dispatch_receipt(operation, self.journal.command_get(cmd["command_id"]))
            state = "verifying" if sid == task.get("reviewer_session_id") else "running"
            field = "review_marker" if state == "verifying" else "turn_marker"
            return self.journal.change(task["task_id"], state, fields={field: marker},
                                       event="send_reconciled_delivered" if reconciled else None)
        if operation:
            return operation.effect("task_send_result", delivered)
        with self.journal.tx():
            return delivered()

    def _settle_dispatch_receipt(self, operation, cmd: dict) -> None:
        if operation.service.db.execute(
                "SELECT 1 FROM operation_steps WHERE operation_id=? AND name='task_dispatch' AND status IN ('started','uncertain')",
                (operation.operation_id,)).fetchone():
            operation.service._step_done(operation.operation_id, "task_dispatch", {
                "accepted": True, "turn_marker": cmd["marker"], "settled_by": "task_command"}, reconciled=True)

    async def _observe(self, task: dict, cmds: list[dict]) -> dict:
        sid = task["session_id"]
        if not sid:
            return self.journal.change(task["task_id"], "uncertain")
        read = await self.adapter.read(task, sid, task["turn_marker"])
        if read.get("turn_attribution") in {"unknown", "uncertain", "echo_not_visible",
                                            "queued_unconfirmed", "timestamp_cursor"}:
            self._mark_unproven_send(task["task_id"], sid)
            return self.journal.change(task["task_id"], "uncertain")
        if read.get("turn_done") and (read.get("turn_started") is not True or
                                      read.get("turn_attribution") not in {
                                          "correlated", "correlated_after_prior_turn",
                                      }):
            self._mark_unproven_send(task["task_id"], sid)
            return self.journal.change(task["task_id"], "uncertain")
        decision = classify_read(read)
        if decision == "quota_limited":
            self.journal.provider_use(task["lead_agent"], "quota_error")
        if decision == "continue":
            if task["continuations"] >= min(self.max_continuations, limits(task["recipe"])[0]):
                return self.journal.change(task["task_id"], "needs_ted")
            self.journal.change(task["task_id"], "accepted", fields={"continuations": task["continuations"] + 1})
            return await self._send(self.journal.get(task["task_id"]), sid,
                                    "Continue toward the user's acceptance criteria. Report a blocker or milestone.",
                                    "continue")
        if decision == "verifying":
            return self.journal.change(task["task_id"], "verifying")
        return self.journal.change(task["task_id"], decision)

    async def _reconcile_command(self, task: dict, cmd: dict) -> dict:
        kind, sid = cmd["kind"], cmd["session_id"]
        if kind.startswith("start_"):
            role = kind.removeprefix("start_")
            if await self.adapter.recover_start(task, role=role, session_id=sid):
                self.journal.command_status(cmd["command_id"], "settled")
                self.journal.add_branch(task["task_id"], session_id=sid,
                                        provider=json.loads(cmd["payload"])["agent"], role=role,
                                        reason="recovered_start")
                field = "reviewer_session_id" if role == "reviewer" else "session_id"
                return self.journal.change(task["task_id"], "verifying" if role == "reviewer" else "accepted",
                                           fields={field: sid})
            return self.journal.change(task["task_id"], "uncertain")
        if kind == "failover":
            if json.loads(cmd["payload"]).get("operator_only"):
                return self.journal.change(task["task_id"], "uncertain")
            handoff = self._bound_handoff(task, cmd)
            if not handoff:
                self.journal.command_operator_only(cmd["command_id"], "handoff_command_binding")
                return self.journal.change(task["task_id"], "uncertain")
            found = await self.adapter.recover_failover(
                task, successor_id=sid, handoff_message_id=handoff["message_id"],
                handoff_command_id=handoff["command_id"])
            if found and (found.get("identity_mismatch") or found.get("session_id") != sid
                          or found.get("marker") != handoff["message_id"]):
                self.journal.command_operator_only(cmd["command_id"], "successor_identity")
                return self.journal.change(task["task_id"], "uncertain")
            if found:
                self.journal.command_status(cmd["command_id"], "settled", marker=found.get("marker"))
                self.journal.command_status(handoff["command_id"], "uncertain", marker=found.get("marker"))
                self.journal.add_branch(task["task_id"], session_id=found["session_id"],
                                        provider="codex", role="lead", reason="recovered_failover",
                                        parent_branch_id=(task["branches"][-1]["branch_id"]
                                                          if task["branches"] else None))
                return self.journal.change(task["task_id"], "uncertain",
                                           fields={"session_id": found["session_id"],
                                                   "turn_marker": None, "lead_agent": "codex"})
            return self.journal.change(task["task_id"], "uncertain")
        if kind in {"answer", "interrupt", "permissions"}:
            from . import service
            client = self.adapter.fleet.client(task["host"])
            meta = await service._meta(client, sid)
            payload = json.loads(cmd["payload"])
            proven = kind == "interrupt" and isinstance(meta, dict) and meta.get("isStreaming") is False
            if kind == "answer" and payload.get("tool_use_id"):
                state = await service._live_state(client, sid, service.agent_kind(
                    (registry.get(task["host"], sid) or {}).get("agent_preset")), meta)
                if isinstance(state, dict):
                    prompts = [state.get("pendingAskUser"), state.get("pendingPermission")]
                    proven = not any(isinstance(p, dict) and p.get("toolUseId") == payload["tool_use_id"] for p in prompts)
            if proven:
                with self.journal.tx():
                    self.journal.command_status(cmd["command_id"], "settled")
                    if task["state"] == "uncertain":
                        return self.journal.change(task["task_id"], "running", event="runtime_control_reconciled")
                    return self.journal.get(task["task_id"])
            return self.journal.change(task["task_id"], "uncertain")
        if kind != "send":
            return self.journal.change(task["task_id"], "uncertain")
        before = json.loads(cmd["payload"]).get("before") or {}
        proof = None
        try:
            proof = await asyncio.wait_for(self.adapter.reconcile_send(
                task, sid, json.loads(cmd["payload"])["prompt_sha256"], before,
                cmd["message_id"]), timeout=5)
        except TaskControlRefused:
            raise
        except Exception:  # noqa: BLE001 - recovery must fail closed
            proof = None
        if proof and proof.get("accepted") and proof.get("turn_attribution") == "exact_echo":
            marker = proof.get("turn_marker")
            if marker:
                self.journal.command_status(cmd["command_id"], "accepted", marker=marker)
                reviewer = sid == task.get("reviewer_session_id")
                return self.journal.change(task["task_id"], "verifying" if reviewer else "running",
                                           fields={"review_marker" if reviewer else "turn_marker": marker},
                                           event="send_reconciled_delivered")
        marker = cmd["marker"] or (before.get("before_cursor") if before.get("agent_kind") == "codex"
                                   else cmd["message_id"])
        if not marker:
            return self.journal.change(task["task_id"], "uncertain")
        read = await self.adapter.read(task, sid, marker)
        attribution = read.get("turn_attribution")
        if read.get("turn_started") is True and attribution in {"correlated", "correlated_after_prior_turn"}:
            pass
        else:
            return self.journal.change(task["task_id"], "uncertain")
        self.journal.command_status(cmd["command_id"], "accepted", marker=marker)
        reviewer = sid == task.get("reviewer_session_id")
        return self.journal.change(task["task_id"], "verifying" if reviewer else "running",
                                   fields={"review_marker" if reviewer else "turn_marker": marker})

    def _failover_authority(self, task, successor_id, handoff_command_id, handoff_message_id, **callbacks):
        """Issue only against this coordinator's durable successor and handoff reservation."""
        from .task_control import FAILOVER_CALLBACKS, TaskFailoverAuthority
        self._check_control(task)
        candidates = [c for c in self.journal.commands(task["task_id"])
                      if c["kind"] == "failover" and c["session_id"] == successor_id
                      and c["status"] in {"intent", "needs_review", "uncertain"}]
        failover = next((c for c in candidates
                         if (self._bound_handoff(task, c) or {}).get("command_id") == handoff_command_id), None)
        handoff = self._bound_handoff(task, failover) if failover else None
        if (not handoff or handoff["message_id"] != handoff_message_id
                or set(callbacks) != set(FAILOVER_CALLBACKS) or not all(map(callable, callbacks.values()))):
            raise TaskControlRefused("TASK_OWNED_CONTROL_REQUIRED", "failover has no complete coordinator reservation")
        authority = object.__new__(TaskFailoverAuthority)
        fields = dict(task_id=task["task_id"], host=task["host"], session_id=task["session_id"],
                      successor_session_id=successor_id, failover_command_id=failover["command_id"],
                      handoff_command_id=handoff_command_id, handoff_message_id=handoff_message_id,
                      control_version=task["control_version"], _issuer=self, **callbacks)
        for name, value in fields.items():
            object.__setattr__(authority, name, value)
        self._failover_authorities.add(authority)
        return authority

    def _valid_failover_authority(self, authority):
        try:
            task = self.journal.get(authority.task_id)
            failover = self.journal.command_get(authority.failover_command_id)
            handoff = self._bound_handoff(task, failover)
            return (task["state"] == "quota_limited"
                    and task["host"] == authority.host and task["session_id"] == authority.session_id
                    and failover["session_id"] == authority.successor_session_id
                    and failover["status"] in {"intent", "needs_review", "uncertain"}
                    and handoff is not None and handoff["command_id"] == authority.handoff_command_id
                    and handoff["message_id"] == authority.handoff_message_id)
        except (ValueError, KeyError):
            return False

    async def _failover(self, task: dict) -> dict:
        task_control.require_authority(task)
        successor = str(uuid.uuid4())
        cmd, handoff = self.journal.reserve_failover(task["task_id"], task["session_id"], successor)
        if self.journal.get(task["task_id"])["paused"]:
            # No BAT call has started; both durable intents can be cancelled.
            self.journal.command_status(cmd["command_id"], "cancelled")
            self.journal.command_status(handoff["command_id"], "cancelled")
            return self.journal.get(task["task_id"])
        try:
            result = await self.adapter.failover(task, task["session_id"], successor,
                                                 handoff_message_id=handoff["message_id"],
                                                 handoff_command_id=handoff["command_id"])
        except TaskDispatchCancelled:
            self.journal.command_operator_only(cmd["command_id"], "paused_before_handoff_send")
            self.journal.command_status(handoff["command_id"], "uncertain")
            return self.journal.change(task["task_id"], "uncertain")
        except TaskIdentityMismatch:
            self.journal.command_operator_only(cmd["command_id"], "successor_identity")
            self.journal.command_status(handoff["command_id"], "uncertain")
            return self.journal.change(task["task_id"], "uncertain")
        except Exception:
            self.journal.command_status(cmd["command_id"], "uncertain")
            self.journal.command_status(handoff["command_id"], "uncertain")
            return self.journal.change(task["task_id"], "uncertain")
        if (not self._bound_handoff(task, cmd) or result.get("session_id") != successor
                or result.get("marker") != handoff["message_id"]):
            self.journal.command_operator_only(cmd["command_id"], "successor_or_handoff_response")
            self.journal.command_status(handoff["command_id"], "uncertain")
            return self.journal.change(task["task_id"], "uncertain")
        self.journal.command_status(cmd["command_id"], "settled", marker=result.get("marker"))
        self.journal.command_status(handoff["command_id"], "uncertain", marker=result.get("marker"))
        self.journal.add_branch(task["task_id"], session_id=result["session_id"], provider="codex",
                                role="lead", reason="quota_failover",
                                parent_branch_id=(task["branches"][-1]["branch_id"]
                                                  if task["branches"] else None))
        return self.journal.change(task["task_id"], "uncertain",
                                   fields={"session_id": result["session_id"], "turn_marker": None,
                                           "lead_agent": "codex"})

    def _bound_handoff(self, task: dict, failover: dict) -> dict | None:
        """Only the journal's reserved successor and handoff command may advance failover."""
        try:
            payload = json.loads(failover["payload"])
            handoff = self.journal.command_get(payload["handoff_command_id"])
            hp = json.loads(handoff["payload"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if (failover["task_id"] != task["task_id"] or failover["kind"] != "failover"
                or payload.get("old_session_id") != task["session_id"]
                or handoff["task_id"] != task["task_id"] or handoff["kind"] != "send"
                or handoff["session_id"] != failover["session_id"]
                or handoff["status"] not in {"needs_review", "uncertain"}
                or hp.get("purpose") != "failover_handoff"
                or hp.get("old_session_id") != task["session_id"]
                or not handoff.get("message_id")):
            return None
        return handoff

    def _verification_wait(self, task: dict, fingerprint: tuple[str, str] | None,
                           reason: str, *, working: bool = False) -> tuple[dict, bool]:
        """Wait for a stable candidate; only real activity counts as progress.

        The daemon enforces two clocks: time since the last meaningful progress
        and an absolute per-recipe cap since the verifying phase began.
        """
        now = asyncio.get_running_loop().time()
        previous = self._verification_stability.get(task["task_id"])
        if fingerprint is not None and (previous is None or previous[0] != fingerprint):
            self._verification_stability[task["task_id"]] = (fingerprint, now)
            self.journal.progress(task["task_id"])
            changed = self.journal.change(task["task_id"], "verifying", event="verification_stability_started")
            return changed, self.verification_quiet_s <= 0
        if fingerprint is None:
            self._verification_stability[task["task_id"]] = (None, now)
            if working:
                self.journal.progress(task["task_id"])
            return task, False
        if previous and now - previous[1] < self.verification_quiet_s:
            return task, False
        return task, True

    async def _verify_and_review(self, task: dict) -> dict:
        task_control.require_authority(task)
        lead_read = await self.adapter.read(task, task["session_id"], task["turn_marker"])
        self._check_control(task)
        if lead_read.get("streaming") is True or lead_read.get("pending"):
            return self._verification_wait(task, None, "lead_not_idle",
                                           working=lead_read.get("streaming") is True)[0]
        candidate = await self.adapter.candidate_identity(task)
        self._check_control(task)
        if not candidate or not candidate.get("clean"):
            return self._verification_wait(task, None, "candidate_not_stable")[0]
        commit, tree = candidate["candidate_commit"], candidate["tree_hash"]
        task, stable = self._verification_wait(task, (commit, tree), "candidate_changed")
        if not stable:
            return task
        # The trusted runner, not a model, verifies; no routing row is recorded for it.
        if (task["verification_commit"], task["verification_tree"]) != (commit, tree):
            task = self.journal.change(task["task_id"], "verifying", fields={
                "verification_commit": None, "verification_tree": None, "reviewer_session_id": None,
                "review_commit": None, "review_tree": None, "review_marker": None, "review_passed": 0,
            }, event="candidate_changed")
        evidence = self.journal.observed_verification(task["task_id"])
        if evidence and task.get("verifying_started_at") and evidence["created_at"] < task["verifying_started_at"]:
            evidence = None  # resume starts a new run, including a cancelled dependency retry
        if not evidence or (evidence["candidate_commit"], evidence["tree_hash"]) != (commit, tree):
            observed = await self.adapter.run_verification(task)
            self._check_control(task)
            if not observed:
                return task  # no configured trusted runner; no caller-supplied evidence accepted
            evidence = self.journal.record_observed_verification(task["task_id"], observed)
            self.journal.progress(task["task_id"])
        if evidence["exit_code"] != 0:
            return await self._verification_failed(task, evidence, commit, tree)
        lead_read = await self.adapter.read(task, task["session_id"], task["turn_marker"])
        self._check_control(task)
        if lead_read.get("streaming") is True or lead_read.get("pending"):
            return self._verification_wait(task, None, "lead_not_idle_after_tests",
                                           working=lead_read.get("streaming") is True)[0]
        if not task["verification_commit"]:
            task = self.journal.change(task["task_id"], "verifying", fields={
                "verification_commit": commit, "verification_tree": tree,
            })
        return self.journal.change(task["task_id"], "done", fields={
            "result": "Trusted tests passed on the clean candidate",
        }, event="delivered")

    async def _verification_failed(self, task: dict, evidence: dict, commit: str, tree: str) -> dict:
        """Stuck handler for a failed trusted run.

        Missing dependencies get one lockfile install per candidate and a
        retry; code/test failures go back to the lead within a bounded budget;
        permission, login and unclear environment problems go to the user.
        """
        task_id = task["task_id"]
        classify = getattr(self.adapter, "verification_failure", None)

        async def failure_of(ev: dict) -> dict:
            try:
                found = await classify(task, ev) if callable(classify) else None
            except TaskControlRefused:
                raise
            except Exception:  # noqa: BLE001 - unknown failure class is escalated
                found = None
            return found if isinstance(found, dict) else {"kind": "environment", "summary": ""}

        failure = await failure_of(evidence)
        self._check_control(task)
        if failure.get("kind") == "missing_dependencies":
            if self.journal.has_note(task_id, "dependency_install", commit):
                failure["kind"] = "code"  # the one install did not help; the candidate must fix it
            else:
                self.journal.note(task_id, "dependency_install", {"candidate_commit": commit, "tree_hash": tree})
                installer = getattr(self.adapter, "install_dependencies", None)
                try:
                    result = await installer(task) if callable(installer) else {"ok": False, "reason": "unsupported"}
                except TaskControlRefused:
                    raise
                except Exception as exc:  # noqa: BLE001 - install failure is an environment issue
                    result = {"ok": False, "reason": type(exc).__name__}
                self._check_control(task)
                self.journal.note(task_id, "dependency_install_result", {
                    "candidate_commit": commit, "ok": bool(result.get("ok")),
                    "reason": str(result.get("reason"))[:200], "lockfile": result.get("lockfile")})
                if not result.get("ok"):
                    return self.journal.change(task_id, "needs_ted", event="verification_failed", fields={
                        "result": "Trusted tests need dependencies; lockfile install failed: "
                                  + str(result.get("reason"))[:200]})
                observed = await self.adapter.run_verification(self._check_control(task))
                self._check_control(task)
                if not observed:
                    return self.journal.get(task_id)
                evidence = self.journal.record_observed_verification(task_id, observed)
                self.journal.progress(task_id)
                if evidence["exit_code"] == 0:
                    return self.journal.get(task_id)
                failure = await failure_of(evidence)
                self._check_control(task)
                if failure.get("kind") == "missing_dependencies":
                    failure["kind"] = "code"
        if failure.get("kind") != "code":
            return self.journal.change(task_id, "needs_ted", event="verification_failed", fields={
                "result": "Trusted tests could not run cleanly (" + str(failure.get("kind")) + ")"})
        n = task["verification_failures"] + 1
        if n > verification_reworks(task["recipe"]):
            return self.journal.change(task_id, "needs_ted", event="verification_failed", fields={
                "verification_failures": n, "result": "Trusted tests failed; rework budget exhausted"})
        # Same session. No new task, no reviewer, no failover.
        self.journal.change(task_id, "accepted", fields={
            "verification_failures": n, "reviewer_session_id": None, "review_marker": None,
            "review_commit": None, "review_tree": None,
            "verification_commit": None, "verification_tree": None}, event="verification_rework")
        return await self._send(self.journal.get(task_id), task["session_id"],
                                "The service's trusted test run failed on candidate " + commit[:12]
                                + " (exit " + str(evidence["exit_code"]) + "). Fix the failure, commit, "
                                "rerun the tests, and report a new milestone. Do not re-plan. "
                                "Output tail (redacted):\n"
                                + str(failure.get("summary") or "")[-2500:], "verification_rework")

    def _mark_unproven_send(self, task_id: str, session_id: str):
        for cmd in reversed(self.journal.commands(task_id)):
            if cmd["kind"] == "send" and cmd["session_id"] == session_id and cmd["status"] == "accepted":
                self.journal.command_status(cmd["command_id"], "uncertain")
                break

    async def resolve_command(self, task_id: str, command_id: str, *, token: str, outcome: str,
                              actor: str, source: str, evidence: str,
                              observed_result: str = "none", turn_ref: str | None = None,
                              candidate_commit: str | None = None, tree_hash: str | None = None,
                              next_prompt: str | None = None, token_hash: str | None = None, operation=None) -> dict:
        """An explicit operator attestation for one uncertain command, never a replay."""
        async with self._task_locks.setdefault(task_id, asyncio.Lock()):
            task = self.journal.get(task_id)
            command = self.journal.command_get(command_id)
            expected_version = operation.effective_preconditions.get("control_version", task["control_version"]) if operation else None
            def reconciliation_refs(result):
                if result.get("_next_command_id"):
                    return task_control.command_refs(self.journal.command_get(result["_next_command_id"]))
                return {"task_id": task_id, "command_id": command_id,
                        "control_version": operation.effective_preconditions.get("control_version", result.get("control_version"))}
            if operation:
                receipt = operation.service.db.execute(
                    "SELECT response FROM operation_steps WHERE operation_id=? AND name='task_reconcile' AND status='succeeded'",
                    (operation.operation_id,)).fetchone()
                if receipt:
                    result = operation.effect("task_reconcile", lambda: json.loads(receipt["response"]),
                                              refs=reconciliation_refs)
                    if next_prompt is not None and result.get("_next_command_id"):
                        return await self._send(result, command["session_id"], next_prompt,
                                                "operator:" + command_id,
                                                prepared_command={"command_id": result["_next_command_id"]},
                                                operation=operation)
                    return result
            if operation:
                from .task_control import check_binding
                check_binding(operation)
            def check_version():
                if operation:
                    check_binding(operation)
                if expected_version is not None and self.journal.get(task_id)["control_version"] != expected_version:
                    raise TaskControlRefused("CONTROL_VERSION_CONFLICT", "task control changed before command resolution")
            if command["task_id"] != task_id or command["status"] != "uncertain":
                raise ValueError("command is not uncertain for this task")
            if command["kind"] == "failover":
                if next_prompt is not None or observed_result != "none" or turn_ref:
                    raise ValueError("failover reconciliation cannot send or attest a turn")
                handoff = self._bound_handoff(task, command)
                if not handoff:
                    raise ValueError("failover handoff command binding is invalid")
                def resolve_failover():
                    check_version()
                    return self.journal.resolve_failover(task_id, command_id, handoff["command_id"],
                        token=token, token_hash=token_hash, outcome=outcome, actor=actor, source=source, evidence=evidence)
                return operation.effect("task_reconcile", resolve_failover, refs=reconciliation_refs) if operation else resolve_failover()
            if next_prompt is not None:
                if not isinstance(next_prompt, str) or not next_prompt.strip() or len(next_prompt) > 18_000:
                    raise ValueError("invalid new prompt")
                read = await self.adapter.read(task, command["session_id"], command["marker"])
                if read.get("streaming") is not False or read.get("pending"):
                    raise ValueError("BAT session must be confirmed idle before new prompt")
                next_before = await self.adapter.prepare_send(task, command["session_id"])
            else:
                next_before = None
            if observed_result == "review_pass":
                lead_read = await self.adapter.read(task, task["session_id"], task["turn_marker"])
                if lead_read.get("streaming") is not False or lead_read.get("pending"):
                    raise ValueError("lead session must be settled before review resolution")
                identity = await self.adapter.candidate_identity(task)
                if (not identity or not identity.get("clean") or
                        (identity["candidate_commit"], identity["tree_hash"]) !=
                        (candidate_commit, tree_hash)):
                    raise ValueError("candidate changed before operator review resolution")
                read = await self.adapter.read(task, command["session_id"], command["marker"])
                if read.get("streaming") is not False:
                    raise ValueError("reviewer turn is still active")
            def resolve():
                check_version()
                return self.journal.resolve_send(
                    task_id, command_id, token=token, token_hash=token_hash, outcome=outcome, actor=actor,
                    source=source, evidence=evidence, observed_result=observed_result,
                    turn_ref=turn_ref, candidate_commit=candidate_commit, tree_hash=tree_hash,
                    next_prompt_sha256=hashlib.sha256(next_prompt.encode()).hexdigest()
                    if next_prompt is not None else None, next_before=next_before,
                    operation_id=operation.operation_id if operation else None,
                )
            result = operation.effect("task_reconcile", resolve, refs=reconciliation_refs) if operation else resolve()
            if next_prompt is not None:
                return await self._send(result, command["session_id"], next_prompt,
                                        "operator:" + command_id,
                                        prepared_command={"command_id": result["_next_command_id"]}, operation=operation)
            return result
