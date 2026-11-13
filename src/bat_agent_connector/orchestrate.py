"""Worktree status (read tier) and the opt-in orchestrate tier.

BAT 3.2.12 host semantics this module relies on (see docs/ORCHESTRATE.md):

* ``worktree:create {sessionId, cwd, installPnpm}``: the HOST picks the folder
  (``<git root>/.bat-worktrees/<8 hex>``) and branch (``bat/worktree-<8 hex>``);
  a client cannot choose the branch name.
* ``claude:start-session {sessionId, options}``: Codex when ``options.agentPreset``
  is a codex preset, otherwise Claude (Node sidecar). Worktree sessions pass
  ``useWorktree/worktreePath/worktreeBranch`` with ``cwd`` = the worktree folder.
* ``worktree:status`` returns diff/branch/sourceBranch/merged/mergedKind
  (ancestor | patch-equivalent | ahead | diverged | unknown) from host memory.
* ``worktree:merge`` runs ``git checkout <source>`` (if needed) + ``merge --no-ff``
  in the MAIN checkout and does not abort on conflicts, so we only call it when the
  merge is provably conflict-free (mergedKind == ahead) and the main checkout is
  clean and already on the source branch.
* ``worktree:remove`` always force-removes the folder; ``deleteBranch`` runs
  ``git branch -D``. We pre-check dirty files and unmerged commits.
* A started session gets no GUI tab unless it is appended to the workspace
  document (``workspace:save``, whole-document replace). That is opt-in
  (``orchestrate_register_tabs``) and done append-only with verification.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import confinement, registry, resource_policy, task_control
from .errors import BatError, ResourceReadOnly, WriteRefused
from .fleet import Fleet
from .resource_policy import WriteGrant
from .safety import Audit, audit_error_outcome
from .service import _err, _meta, _resolve_session, _workspace, _write_lock, agent_kind

MERGED_KINDS = {"ancestor", "patch-equivalent"}


def diff_stats(diff: str | None) -> dict:
    files, add, dele = 0, 0, 0
    for line in (diff or "").splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            add += 1
        elif line.startswith("-") and not line.startswith("---"):
            dele += 1
    return {"files": files, "additions": add, "deletions": dele}


async def _git_dirty(c, cwd: str | None) -> list[dict] | None:
    if not cwd:
        return None
    root = await c.invoke("git:getRoot", {"cwd": cwd})
    if not root:
        return None  # not a repo / unknown -> caller treats as unknown
    st = await c.invoke("git:status", {"cwd": cwd})
    return [e for e in st or [] if isinstance(e, dict)] if isinstance(st, list) else None


def _origin_cwd(t: dict, ws: dict) -> str | None:
    if t.get("_origin_cwd"):
        return t["_origin_cwd"]
    w = next((x for x in ws.get("workspaces") or [] if x.get("id") == t.get("workspaceId")), {})
    return w.get("folderPath")


async def _wt_status(c, t: dict, *, rehydrate: WriteGrant | None = None) -> tuple[dict | None, bool]:
    """Host worktree state; with a policy grant, re-register a tracked worktree BAT forgot."""
    st = await c.invoke("worktree:status", {"sessionId": t["id"]})
    if isinstance(st, dict):
        return st, False
    snap = await c.invoke("claude:get-worktree-status", {"sessionId": t["id"]})
    if isinstance(snap, dict) and snap:
        return snap, False
    if rehydrate is not None and t.get("worktreePath") and t.get("worktreeBranch"):
        await c.invoke(
            "worktree:rehydrate",
            {
                "sessionId": t["id"],
                "cwd": t.get("_origin_cwd") or t.get("cwd"),
                "worktreePath": t["worktreePath"],
                "branchName": t["worktreeBranch"],
            },
            grant=rehydrate,
        )
        st = await c.invoke("worktree:status", {"sessionId": t["id"]})
        return (st if isinstance(st, dict) else None), True
    return None, False


def _summ(host: str, t: dict, st: dict | None, include_diff: bool, max_diff_chars: int) -> dict:
    d: dict[str, Any] = {
        "host": host,
        "session_id": t.get("id"),
        "agent_kind": agent_kind(t.get("agentPreset")),
        "worktree_path": (st or {}).get("worktreePath") or t.get("worktreePath"),
        "branch": (st or {}).get("branchName") or t.get("worktreeBranch"),
        "source_branch": (st or {}).get("sourceBranch"),
        "merged": (st or {}).get("merged"),
        "merged_kind": (st or {}).get("mergedKind"),
        "tracked_by_host": st is not None,
        "diff_stats": diff_stats((st or {}).get("diff")),
    }
    if include_diff and st:
        diff = st.get("diff") or ""
        d["diff"] = diff[:max_diff_chars]
        d["diff_truncated"] = len(diff) > max_diff_chars
    if st is None:
        d["note"] = (
            "host has no worktree state for this session (e.g. after a host restart); orchestrate tier can rehydrate"
        )
    return d


# --------------------------------------------------------------------------- read tier
async def worktree_status(fleet: Fleet, host: str, workspace: str | None = None) -> dict:
    """All worktree sessions on a host (tabs with a worktree + orchestrated sessions)."""
    c = fleet.client(host)
    ws = await _workspace(c)
    ws_by_id = {w.get("id"): w for w in ws.get("workspaces") or []}
    terms = [t for t in ws.get("terminals") or [] if t.get("worktreePath")]
    known = {t.get("id") for t in terms}
    entries = registry.list_entries(host)
    from .service import registry_terminal

    for e in registry.list_entries(host, active_only=True):
        if e.get("session_id") not in known and e.get("worktree_path"):
            terms.append(registry_terminal(e))
    if workspace:
        wl = workspace.lower()
        terms = [
            t
            for t in terms
            if wl in str((ws_by_id.get(t.get("workspaceId")) or {}).get("name", "")).lower()
            or str(t.get("workspaceId", "")).startswith(workspace)
        ]
    out = []
    for t in terms:
        try:
            st, _ = await _wt_status(c, t)
            row = _summ(host, t, st, False, 0)
        except BatError as e:
            row = {"host": host, "session_id": t.get("id"), "error": _err(e)}
        row["workspace"] = (ws_by_id.get(t.get("workspaceId")) or {}).get("name")
        row["orchestrated"] = registry.get(host, t.get("id")) is not None
        row.update(resource_policy.classify_row_for_read(
            c.host, t.get("id"), has_tab=not t.get("_orchestrated", False), entries=entries))
        out.append(row)
    return {"host": host, "worktrees": out, "count": len(out)}


async def session_worktree_status(
    fleet: Fleet, host: str, session_id: str, include_diff: bool = False, max_diff_chars: int = 20000
) -> dict:
    c = fleet.client(host)
    t, ws = await _resolve_session(c, session_id)
    max_diff_chars = max(0, min(100_000, int(max_diff_chars)))
    st, _ = await _wt_status(c, t)
    d = _summ(host, t, st, include_diff, max_diff_chars)
    wt = d["worktree_path"]
    dirty = await _git_dirty(c, wt) if wt else None
    d["worktree_dirty_files"] = None if dirty is None else len(dirty)
    d["worktree_dirty_preview"] = [f"{e.get('status')} {e.get('file')}" for e in (dirty or [])[:20]]
    origin = _origin_cwd(t, ws)
    if origin and any((registry.get(host, t["id"]) or {}).get(k) for k in ("checkpoint_id", "published_sha")):
        # A checkpoint session's recorded main checkout is the person's folder: merges into it are refused, and
        # BAT's git:status there could rewrite their index (a plain `git status`), so it is not read at all.
        d["main_checkout_note"] = "not read: the person's folder (checkpoint sessions never merge into it)"
    elif origin:
        d["main_checkout_branch"] = await c.invoke("git:branch", {"cwd": origin})
        md = await _git_dirty(c, origin)
        d["main_checkout_dirty_files"] = None if md is None else len(md)
    meta = await _meta(c, t["id"])
    d["loaded"] = meta is not None
    d["streaming"] = bool((meta or {}).get("isStreaming"))
    return d


# --------------------------------------------------------------------------- orchestrate tier
def _guard(fleet: Fleet, host: str, confirm: bool) -> None:
    if not fleet.orchestrate_enabled(host):
        raise WriteRefused(
            f"orchestrate tier is disabled for host {host!r} (needs writes = true and orchestrate = true)"
        )
    if confirm is not True:
        raise WriteRefused("orchestrate tools require confirm=true")


def permission_options(agent: str, mode: str, claude_mode: str | None = None) -> dict:
    """Start/resume options that mirror BAT's GUI permission setting.

    ``allow_all`` = what BAT does with "allow bypass permissions" on: Claude runs in
    ``bypassPermissions``; Codex starts with sandbox ``danger-full-access`` and approval ``never``.
    ``default`` sends nothing and preserves BAT defaults and inherited rules.
    ``confined`` requests restricted options; the creation path also checks host-account evidence.
    """
    return confinement.policy_options(agent, mode, claude_mode)


# BAT's acceptEdits callback allows file tools without a path check. Use default unless a host account was
# checked; cwd is never protection. Codex options require W12 live enforcement evidence (plan §06, A10).
CONFINED_OPTIONS = confinement.CONFINED_OPTIONS


def registry_permission_fields(opts: dict) -> dict:
    ap = {}
    if opts.get("codexSandboxMode"):
        ap["sandboxMode"] = opts["codexSandboxMode"]
    if opts.get("codexApprovalPolicy"):
        ap["approvalPolicy"] = opts["codexApprovalPolicy"]
    return {"permission_mode_claude": opts.get("permissionMode"), "agent_params": ap or None}


PRESETS = {
    ("claude", True): "claude-code-worktree",
    ("claude", False): "claude-code",
    ("codex", True): "codex-agent-worktree",
    ("codex", False): "codex-agent",
}


@registry.start_call
async def session_start(
    fleet: Fleet,
    host: str,
    workspace: str,
    agent: str = "claude",
    confirm: bool = False,
    prompt: str | None = None,
    model: str | None = None,
    use_worktree: bool = True,
    title: str | None = None,
    permission_mode: str | None = None,
    session_id: str | None = None,
    retain_on_error: bool = False,
    register_tab: bool | None = None,
    base_branch: str | None = None,
    cwd_override: str | None = None,
    external_branch: str | None = None,
    task_id: str | None = None,
    write_scope: str | None = None,
    confinement_role: str | None = None,
    _task_start_guard: Callable[[], None] | None = None,
    _start_frame_check=None,
    _creation_fields: dict | None = None,
    _on_reservation: Callable[[dict], None] | None = None,
) -> dict:
    _guard(fleet, host, confirm)
    if write_scope not in (None, "confined"):
        raise WriteRefused("write_scope must be confined or omitted")
    if agent not in ("claude", "codex"):
        raise WriteRefused("agent must be claude or codex")
    if prompt is not None and len(prompt) > 20_000:
        raise WriteRefused("prompt is longer than 20000 characters")
    hc = fleet.config.host(host)
    c = fleet.client(host)
    audit = Audit(fleet.config.safety)
    ws = await _workspace(c)
    wsl = [w for w in ws.get("workspaces") or [] if w.get("id") == workspace or w.get("name") == workspace]
    if not wsl:
        wsl = [w for w in ws.get("workspaces") or [] if workspace.lower() in str(w.get("name", "")).lower()]
    if len(wsl) != 1:
        raise WriteRefused(
            f"workspace {workspace!r} matched {len(wsl)} workspaces on {host}; use the exact name or id"
        )
    w = wsl[0]
    folder = str(w.get("folderPath") or "").strip()
    if not folder:
        raise WriteRefused("workspace has no folderPath")
    preset = PRESETS[(agent, bool(use_worktree))]
    if agent == "codex" and not model and hc.codex_model:
        model = hc.codex_model
    sid = session_id or str(uuid.uuid4())
    previous = registry.get(host, sid) or {}
    confinement.guard_new_start(previous)
    registry.claim_unsent(host, sid)
    # Read-only: how the host resolves the destination, so links into a human checkout are caught up front.
    git_roots = {}
    for path in {folder, cwd_override} - {None}:
        root = await c.invoke("git:getRoot", {"cwd": path})
        git_roots[resource_policy.norm(path)] = root if isinstance(root, str) else None
    grant = resource_policy.authorize_new_session(hc, sid, folder=folder, use_worktree=use_worktree,
                                                  cwd_override=cwd_override, task_id=task_id,
                                                  git_roots=git_roots)
    async with _write_lock(host):
        if _task_start_guard:
            _task_start_guard()
        try:
            perm, write_scope, confinement_record = await confinement.start_decision(
                fleet, host, agent, confined=write_scope == "confined", task=bool(task_id),
                planner=confinement_role == "planner", claude_mode=permission_mode)
        finally:
            if _task_start_guard:
                _task_start_guard()
        audit.check_rate(host, "#orchestrate-start-" + sid)
        registry.reserve(
            host,
            {
                "session_id": sid,
                "workspace_id": w.get("id"),
                "workspace_name": w.get("name"),
                "agent_preset": preset,
                "origin_cwd": folder,
                "model": model,
                "title": title,
                "isolation": grant.isolation,
                "start_sent": False,
                **(_creation_fields or {}),
                # Preserve a retained carrier until its identity has been rechecked or removal confirmed.
                **({k: previous[k] for k in ("cwd", "worktree_path", "branch", "worktree_rolled_back",
                                             "rolled_back_worktree_path", "rolled_back_branch") if k in previous}
                   if previous.get("start_sent") is False else {}),
                # Recorded with the reservation, so a start proven later by read-back keeps them too.
                **registry_permission_fields(perm),
                "confinement": confinement_record,
                **({"write_scope": write_scope} if write_scope else {}),
                **({"task_id": task_id, "role": "lead"} if task_id else {}),
            },
            hc.orchestrate_max_sessions,
        )
        if _on_reservation:
            _on_reservation(registry.get(host, sid))
        if task_id:
            confinement.record_task_start(getattr(fleet, "confinement_journal", None), task_id, sid,
                                          registry.get(host, sid))
        base = {"actor": fleet.actor, "tool": "session_start", "host": host, "session_id": sid}
        wt: dict = {}
        worktree_created = False
        base_commit = None
        start_confirmed = False
        start_frame = confinement.StartFrame(host, sid, journal=getattr(fleet, "confinement_journal", None),
                                             task_id=task_id)
        meta = None

        async def rollback_worktree():
            removed = await c.invoke("worktree:remove", {"sessionId": sid, "deleteBranch": True}, grant=grant)
            if not isinstance(removed, dict) or removed.get("success") is not True:
                raise WriteRefused("unsent start's worktree rollback was not confirmed")
            # BAT also returns success when a restart lost its in-memory mapping.
            # Preserve the durable identity until an independent read proves absence.
            remaining_root = await c.invoke("git:getRoot", {"cwd": wt["worktreePath"]})
            if remaining_root is not None:
                raise WriteRefused("unsent start's worktree rollback absence was not confirmed")
            registry.update(host, sid, cwd=folder, worktree_path=None, branch=None,
                            worktree_rolled_back=True, rolled_back_worktree_path=wt.get("worktreePath"),
                            rolled_back_branch=wt.get("branchName"))
            audit.record_after_effect(effect_outcome="accepted", **base, channel="worktree:remove", phase="rollback", ok=True,
                         worktree_path=wt.get("worktreePath"), branch=wt.get("branchName"))

        try:
            if use_worktree:
                if previous.get("start_sent") is False and previous.get("worktree_path"):
                    wt = await c.invoke("worktree:status", {"sessionId": sid})
                    if (not isinstance(wt, dict) or wt.get("worktreePath") != previous["worktree_path"]
                            or wt.get("branchName") != previous.get("branch")):
                        raise WriteRefused("unsent start's worktree identity is unavailable or changed")
                else:
                    audit.record(**base, channel="worktree:create", phase="attempt")
                    wt = await c.invoke(
                        "worktree:create", {"sessionId": sid, "cwd": folder, "installPnpm": False,
                                             **({"baseBranch": base_branch} if base_branch else {})},
                        grant=grant, before_send=_task_start_guard,
                    )
                    worktree_created = True
                if not isinstance(wt, dict) or wt.get("success") is False or not wt.get("worktreePath"):
                    err = (wt or {}).get("error") if isinstance(wt, dict) else "unexpected reply"
                    audit.record_after_effect(effect_outcome="error", **base, channel="worktree:create", phase="result", ok=False, error=str(err))
                    raise WriteRefused(f"worktree:create failed: {err}")
                if base_branch and wt.get("sourceBranch") != base_branch:
                    audit.record_after_effect(effect_outcome="error", **base, channel="worktree:create", phase="result", ok=False,
                                 error="host ignored requested base branch", requested_base_branch=base_branch,
                                 source_branch=wt.get("sourceBranch"))
                    raise WriteRefused("worktree:create did not honor requested base branch")
                audit.record_after_effect(effect_outcome="accepted",
                    **base, channel="worktree:create", phase="result", ok=True, branch=wt.get("branchName"),
                    source_branch=wt.get("sourceBranch"), requested_base_branch=base_branch
                )
                origin_root = git_roots.get(resource_policy.norm(folder))
                # No rollback on refusal: a worktree at an unexpected path may be a person's checkout, and
                # worktree:remove with deleteBranch could delete their branch. Fail closed and leave it.
                grant = resource_policy.check_new_worktree(grant, hc, folder, wt.get("worktreePath"), origin_root)
            cwd = cwd_override or wt.get("worktreePath") or folder
            if use_worktree:
                registry.update(host, sid, cwd=cwd, worktree_path=wt.get("worktreePath"), branch=wt.get("branchName"))
                rows = await c.invoke("git:log", {"cwd": cwd, "count": 1})
                if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                    base_commit = rows[0].get("hash")
            opts = {
                "cwd": cwd,
                "agentPreset": preset,
                "workspaceId": w.get("id"),
                "workspaceName": w.get("name"),
            }
            if model:
                opts["model"] = model
            opts.update(perm)
            if use_worktree:
                opts.update(
                    useWorktree=True, worktreePath=wt["worktreePath"], worktreeBranch=wt.get("branchName")
                )
            registry.update(host, sid, cwd=cwd, worktree_path=cwd if cwd_override else wt.get("worktreePath"),
                            branch=external_branch if cwd_override else wt.get("branchName"))
            audit.record(**base, channel="claude:start-session", phase="attempt", preset=preset)
            async def before_start_frame():
                if _start_frame_check:
                    await _start_frame_check()
                if _task_start_guard:
                    _task_start_guard()
                try:
                    await confinement.guard_start_frame(fleet, host, confinement_record)
                finally:
                    if _task_start_guard:
                        _task_start_guard()

            try:
                started = await c.invoke("claude:start-session", {"sessionId": sid, "options": opts}, grant=grant,
                                         before_frame=before_start_frame, before_send=_task_start_guard,
                                         on_transport=start_frame.on_transport)
                if (not isinstance(started, dict) or started.get("ok") is False or
                        started.get("sessionId") != sid):
                    raise WriteRefused("BAT start reply did not confirm the reserved session ID")
                start_confirmed = True
                try:
                    meta = await _meta(c, sid)
                except Exception:  # noqa: BLE001 - an evidence read cannot undo an acknowledged start
                    meta = None
                if isinstance(meta, dict):
                    confinement.guard_start_cwd({"cwd": cwd}, meta)
                confinement.ensure_confirmed(confinement_record, meta, allow_unknown=write_scope != "confined")
                confinement_record = confinement.confirm(confinement_record, meta)
            except confinement.ConfinementRefused:
                if start_frame.sent or start_confirmed:
                    retain_on_error = True
                elif worktree_created and not retain_on_error:
                    await rollback_worktree()
                raise
            except BatError as e:
                if start_confirmed or start_frame.sent:
                    retain_on_error = True  # Even invoke-error may follow creation of the BAT session.
                audit.record_after_effect(effect_outcome=audit_error_outcome(e), **base, channel="claude:start-session", phase="result", ok=False, error=_err(e))
                if worktree_created and not retain_on_error:  # may have reached BAT on timeout
                    await rollback_worktree()
                raise
            audit.record_after_effect(effect_outcome="accepted", **base, channel="claude:start-session", phase="result", ok=True)
        except BaseException as e:
            unsent = not start_frame.sent and not start_confirmed
            retain_on_error = retain_on_error or start_confirmed or start_frame.sent
            registry.update(host, sid, status="failed" if unsent else
                            "uncertain" if retain_on_error else "failed",
                            error_code=getattr(e, "code", None) or ("CONFINEMENT_START_UNSETTLED" if not unsent else None),
                            start_sent=not unsent,
                            **({"cwd": cwd, "worktree_path": cwd if cwd_override else wt.get("worktreePath"),
                                "branch": external_branch if cwd_override else wt.get("branchName"),
                                "confinement": confinement.confirm(confinement_record, meta)}
                               if start_confirmed else {}))
            raise
        registry.update(
            host,
            sid,
            status="active",
            cwd=cwd,
            worktree_path=cwd if cwd_override else wt.get("worktreePath"),
            branch=external_branch if cwd_override else wt.get("branchName"),
            **({"worktree_made_by": "connector"} if cwd_override else {}),
            origin_root=(_creation_fields or {}).get("origin_root", git_roots.get(resource_policy.norm(folder))),
            **registry_permission_fields(opts),
            confinement=confinement_record,
        )
        tab = None
        if hc.orchestrate_register_tabs and register_tab is not False:
            term = {
                "id": sid,
                "workspaceId": w.get("id"),
                "title": title or f"{agent} (orchestrated)",
                "type": "terminal",
                "cwd": cwd,
                "agentPreset": preset,
            }
            if model:
                term["model"] = model
            pf = registry_permission_fields(opts)
            if pf["permission_mode_claude"]:
                term["permissionMode"] = pf["permission_mode_claude"]
            if pf["agent_params"]:
                term["agentParams"] = pf["agent_params"]
            if use_worktree:
                term.update(worktreePath=wt["worktreePath"], worktreeBranch=wt.get("branchName"))
            tab_outcome = "unknown"
            try:
                tab = await c.append_workspace_terminal(hc.profile_id, term,
                                                        grant=resource_policy.authorize_register_tab(host, sid),
                                                        before_send=_task_start_guard if _start_frame_check else None,
                                                        before_frame=_start_frame_check)
            except BatError as e:
                tab = {"appended": False, "error": _err(e)}
                tab_outcome = audit_error_outcome(e)
            audit.record_after_effect(effect_outcome="accepted" if bool(tab.get("appended")) else tab_outcome,
                **base,
                channel="workspace:save",
                phase="append-terminal",
                ok=bool(tab.get("appended")),
                lost_terminals=len(tab.get("lost_terminals") or []),
            )
            registry.update(host, sid, tab_registered=bool(tab.get("appended")))
        mid = None
        if prompt:
            mid = f"batc-{uuid.uuid4()}"
            audit.record(**base, channel="claude:send-message", phase="attempt", message_id=mid, text=prompt)
            try:
                ack = await c.invoke(
                    "claude:send-message", {"sessionId": sid, "prompt": prompt, "clientMessageId": mid},
                    retry_on_disconnect=agent == "claude", grant=grant,
                    before_frame=lambda: confinement.guard_frame(c, host, sid),
                )
                if not isinstance(ack, dict) or not (ack.get("accepted") or ack.get("ok")):
                    raise WriteRefused("initial prompt was not accepted by BAT")
                audit.record_after_effect(effect_outcome="accepted", **base, channel="claude:send-message", phase="result", ok=True, message_id=mid)
            except BatError as e:
                audit.record_after_effect(effect_outcome=audit_error_outcome(e), **base, channel="claude:send-message", phase="result", ok=False, error=_err(e))
                return {
                    "host": host,
                    "session_id": sid,
                    "started": True,
                    "prompt_sent": False,
                    "error": _err(e),
                    "worktree_path": wt.get("worktreePath"),
                    "branch": wt.get("branchName"),
                    "tab": tab,
                }
    return {
        "host": host,
        "session_id": sid,
        "started": True,
        "agent_preset": preset,
        "workspace": w.get("name"),
        "worktree_path": cwd if cwd_override else wt.get("worktreePath"),
        "branch": external_branch if cwd_override else wt.get("branchName"),
        "source_branch": wt.get("sourceBranch"),
        "base_branch": base_branch,
        "base_commit": base_commit,
        "tab": tab,
        "prompt_sent": bool(prompt),
        "message_id": mid,
        "permissions": write_scope or permission_mode or hc.default_permission_mode,
        "write_scope": write_scope,
        "confinement": confinement_record,
        "isolation": grant.isolation,
        "note": None
        if tab and tab.get("appended")
        else "no GUI tab registered (orchestrate_register_tabs=false); tracked in the local registry",
    }


async def worktree_merge(fleet: Fleet, host: str, session_id: str, confirm: bool = False) -> dict:
    _guard(fleet, host, confirm)
    c = fleet.client(host)
    audit = Audit(fleet.config.safety)
    async with _write_lock(host):
        t, ws = await _resolve_session(c, session_id)
        sid = t["id"]
        grant = await resource_policy.authorize_session(fleet, host, "worktree.merge", t)
        task_control.refuse_owned(fleet, host, sid)
        origin = resource_policy.merge_origin(fleet.config.host(host), sid, t, ws)
        resource_policy.check_merge_destination(fleet.config.host(host), origin)
        audit.check_rate(host, sid + "#merge")
        st, rehydrated = await _wt_status(c, t, rehydrate=grant)
        if not st:
            raise WriteRefused("host does not track a worktree for this session")
        kind = st.get("mergedKind")
        report = _summ(host, t, st, False, 0)
        report["rehydrated"] = rehydrated
        if kind in MERGED_KINDS:
            return {**report, "merged_now": False, "reason": "already merged"}
        if kind == "unknown":
            return {
                **report,
                "merged_now": False,
                "reason": "no new commits on the worktree branch (or unknown state)",
            }
        if kind != "ahead":
            return {
                **report,
                "merged_now": False,
                "reason": f"branch is {kind}: merging could conflict. Ask the session to rebase onto "
                f"{st.get('sourceBranch')} (or merge manually); never forced",
            }
        meta = await _meta(c, sid)
        if (meta or {}).get("isStreaming"):
            return {**report, "merged_now": False, "reason": "session is still streaming; wait for turn end"}
        dirty = await _git_dirty(c, st.get("worktreePath"))
        if dirty is None:
            return {**report, "merged_now": False, "reason": "cannot read worktree git status"}
        if dirty:
            return {
                **report,
                "merged_now": False,
                "worktree_dirty_files": len(dirty),
                "reason": "worktree has uncommitted changes (they would not be merged); ask the session to commit",
            }
        if not origin:
            return {**report, "merged_now": False, "reason": "cannot determine the main checkout folder"}
        cur = await c.invoke("git:branch", {"cwd": origin})
        if cur != st.get("sourceBranch"):
            return {
                **report,
                "merged_now": False,
                "reason": f"main checkout is on {cur!r}, not {st.get('sourceBranch')!r}; BAT would switch "
                "branches there, so refusing",
            }
        mdirty = await _git_dirty(c, origin)
        if mdirty is None or mdirty:
            return {
                **report,
                "merged_now": False,
                "reason": "main checkout has uncommitted changes (or status unreadable); refusing",
            }
        base = {"actor": fleet.actor, "tool": "worktree_merge", "host": host, "session_id": sid + "#merge"}
        audit.record(**base, channel="worktree:merge", phase="attempt", branch=st.get("branchName"))
        r = await c.invoke("worktree:merge", {"sessionId": sid, "strategy": "merge"}, grant=grant)
        ok = isinstance(r, dict) and r.get("success") is True
        audit.record_after_effect(effect_outcome="accepted" if ok else "error",
            **base,
            channel="worktree:merge",
            phase="result",
            ok=ok,
            error=None if ok else str((r or {}).get("error") if isinstance(r, dict) else r)[:300],
        )
        after = await _git_dirty(c, origin)
        return {
            **report,
            "merged_now": ok,
            "result": r,
            "main_checkout_clean_after": after == [] if after is not None else None,
        }


async def worktree_remove(
    fleet: Fleet,
    host: str,
    session_id: str,
    confirm: bool = False,
    delete_branch: bool = False,
    allow_unmerged: bool = False,
    discard_uncommitted: bool = False,
) -> dict:
    """Refuse legacy deletion; resource cleanup owns reviewed destructive effects.

    Keep the original tier, source policy and Task Service boundary before reporting
    the unavailable compatibility action. Neither discard nor unmerged overrides can
    replace the canonical consumer, retention and durable-receipt checks.
    """
    _guard(fleet, host, confirm)
    c = fleet.client(host)
    async with _write_lock(host):
        t, _ = await _resolve_session(c, session_id)
        await resource_policy.authorize_session(fleet, host, "worktree.remove", t)
        task_control.refuse_owned(fleet, host, t["id"])
        raise ResourceReadOnly(
            "LEGACY_WORKTREE_REMOVE_DISABLED",
            "legacy worktree removal cannot prove shared consumers and retained content; "
            "use cleanup_preview and cleanup_apply, or batc resource-cleanup preview/apply",
        )


# --------------------------------------------------------------------------- fan-out planning (CLI/skill helper)
_TASK_RE = re.compile(r"^\s*(?:[-*]\s+\[\s?\]\s+|\d+[.)]\s+|#{2,3}\s+)(.+?)\s*$")


def fanout_plan(plan_text: str, *, max_tasks: int = 8, context_chars: int = 1500) -> dict:
    """Split a markdown plan into independent task prompts.

    Tasks are unchecked checklist items (``- [ ] ...``), numbered items, or ``##``/``###``
    headings (with the text under them). The orchestrator should review and edit the
    result; it is a starting point, not a decision.
    """
    lines = plan_text.splitlines()
    tasks: list[dict] = []
    cur: dict | None = None
    for ln in lines:
        m = _TASK_RE.match(ln)
        if m:
            if cur:
                tasks.append(cur)
            cur = {"title": m.group(1).strip()[:200], "body": []}
        elif cur is not None and ln.strip():
            cur["body"].append(ln.rstrip())
    if cur:
        tasks.append(cur)
    header = "\n".join(lines[:40])[:context_chars]
    out = []
    for i, tk in enumerate(tasks[:max_tasks], 1):
        body = "\n".join(tk["body"])[:4000]
        prompt = (
            f"You are one of several parallel agents, working in your own git worktree/branch.\n"
            f"Task {i}/{min(len(tasks), max_tasks)}: {tk['title']}\n\n{body}\n\n"
            f"Plan context (excerpt):\n{header}\n\n"
            "Rules: stay within this task's scope; do not edit files other tasks own; commit your work on "
            "this branch with clear messages; run the relevant tests; when done, reply with a short summary, "
            "the files changed and test results. Do not merge or push."
        )
        out.append({"index": i, "title": tk["title"], "prompt": prompt})
    return {"tasks": out, "count": len(out), "total_found": len(tasks), "truncated": len(tasks) > max_tasks}


def read_plan(path: str) -> str:
    p = Path(path).expanduser()
    if p.stat().st_size > 512 * 1024:
        raise BatError("plan file is larger than 512 KiB")
    return p.read_text()
