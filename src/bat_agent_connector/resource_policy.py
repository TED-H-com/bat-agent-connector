"""Resource ownership policy: which BAT sessions, worktrees and checkouts the connector may change.

Every BAT write frame needs a ``WriteGrant`` from this module; ``BatClient`` refuses a write
channel without one, so no tool, CLI command or task path can reach BAT around these checks.
The design and the evidence rules are in docs/design/resource-policy.md.

Provenance of a session:

* ``connector_managed``: the connector's own registry row shows it created the session (or the
  task journal proved it on recovery) and BAT acknowledged the start.
* ``manual``: a BAT workspace tab with no connector creation record. Permanently read-only.
* ``unknown``: no proof either way, or a creation that BAT never acknowledged. Read-only until
  the creation is reconciled.

Session ownership and folder ownership are separate checks. A connector-managed session may only
be driven while its working folder is one the connector owns: a configured managed root, or a
worktree the connector itself created. A connector session in a human checkout is a legacy
boundary and stays read-only too.
"""

from __future__ import annotations

import fnmatch
import hashlib
import posixpath
import re
from dataclasses import dataclass, field, replace
from typing import Any
from weakref import WeakKeyDictionary

from . import controller_authority, registry
from .channels import GUARDED_CHANNELS, ORCHESTRATE_CHANNELS, WRITE_CHANNELS
from .config import HostConfig
from .errors import BatError, ResourceReadOnly
from .resource_ids import connector_made, registry_worktree_root

MANUAL = "manual"
MANAGED = "connector_managed"
UNKNOWN = "unknown"

# Folder ownership of a session's working folder.
OWNER_MANAGED_ROOT = "managed_root"
OWNER_CONNECTOR_WORKTREE = "connector_worktree"
OWNED = frozenset({OWNER_MANAGED_ROOT, OWNER_CONNECTOR_WORKTREE})

# Isolation level of a connector-owned folder. ``legacy_shared_clone`` worktrees live inside a
# human clone and share its refs and object store (git-worktree(1)); only a managed clone is
# fully separate from the human's local Git state.
MANAGED_CLONE = "managed_clone"
LEGACY_SHARED_CLONE = "legacy_shared_clone"

BAT_WORKTREES_DIR = ".bat-worktrees"
_UNCONFIRMED = {"starting": "BAT has not acknowledged its start", "uncertain": "its start outcome is uncertain",
                "failed": "its start failed"}


@dataclass(frozen=True)
class Mutation:
    action: str
    via: str  # "bat" (protocol frame), "ssh-git" (host git), or "github" (REST)
    scope: str  # "session" | "create" | "tab" | "path" | "remote"
    channels: frozenset[str]
    entry_points: tuple[str, ...]
    rule: str
    live_folder: bool = False  # the session's folder must exist and resolve to the recorded path
    tier: str = "orchestrate"  # host tier that must be on: "write" or "orchestrate"


_SESSION_RULE = "connector-managed session whose working folder the connector owns"
MUTATIONS: tuple[Mutation, ...] = (
    Mutation("session.send", "bat", "session", frozenset({"claude:send-message", "claude:client-resume"}),
             ("session_send", "session_continue", "session_relay", "task service send"), _SESSION_RULE,
             live_folder=True, tier="write"),
    Mutation("session.answer", "bat", "session",
             frozenset({"claude:resolve-ask-user", "claude:resolve-permission"}),
             ("session_answer", "approve_pending"), _SESSION_RULE, live_folder=True, tier="write"),
    Mutation("session.permissions", "bat", "session",
             frozenset({"claude:set-permission-mode", "claude:set-codex-sandbox-mode",
                        "claude:set-codex-approval-policy"}),
             ("session_set_permissions", "approve_pending"), _SESSION_RULE, live_folder=True, tier="write"),
    Mutation("session.interrupt", "bat", "session", frozenset({"claude:interrupt-turn", "claude:abort-session"}),
             ("session_interrupt", "task service interrupt / work_pause(abort_current)"), _SESSION_RULE,
             tier="write"),
    Mutation("session.stop", "bat", "session", frozenset({"claude:stop-session"}),
             ("session_cleanup",), _SESSION_RULE),
    Mutation("worktree.rehydrate", "bat", "session", frozenset({"worktree:rehydrate"}),
             ("worktree_merge", "worktree_remove", "session_cleanup"), _SESSION_RULE),
    Mutation("worktree.merge", "bat", "session", frozenset({"worktree:merge", "worktree:rehydrate"}),
             ("worktree_merge", "session_cleanup"),
             _SESSION_RULE + "; the merge destination (main checkout) must be inside a managed root",
             live_folder=True),
    Mutation("worktree.remove", "bat", "session", frozenset({"worktree:remove", "worktree:rehydrate"}),
             ("worktree_remove", "session_cleanup"), _SESSION_RULE),
    Mutation("session.create", "bat", "create",
             frozenset({"claude:start-session", "worktree:create", "worktree:remove", "claude:send-message"}),
             ("session_start", "session_failover", "session_relay(start_if_missing)", "fanout_plan_session",
              "fanout_from_plan", "batc fanout --start", "task service lead/reviewer start", "checkpoint.continue",
              "integration.handoff", "repository.continue"),
             "new session ID reserved in the registry first; it works in a managed root, a worktree the "
             "connector creates, or a connector-owned worktree it shares; never in a human checkout"),
    Mutation("workspace.register_tab", "bat", "tab", frozenset({"workspace:save"}),
             ("session_start", "task service reviewer start"),
             "append-only tab for a session the connector just created; other tabs are verified unchanged"),
    Mutation("task.external_worktree", "ssh-git", "path", frozenset(),
             ("task service start with base_branch", "task service cleanup"),
             "only <workspace>/.bat-worktrees/batc-task-<12 hex> and branch batc/task-<12 hex>"),
    Mutation("checkpoint.managed_worktree", "ssh-git", "path", frozenset(),
             ("checkpoint.continue",),
             "a connector clone <managed root>/<name> (marked batc.managed-clone) that only reads the person's "
             "repository, and the worktree <clone>/.bat-worktrees/batc-cp-<12 hex> on branch batc/cp-<12 hex>"),
    Mutation("artifact.storage", "connector-storage", "path", frozenset(), ("artifact.upload", "artifact.capture"),
             "configured connector-owned storage; no symlinks or adoption of unknown content"),
    Mutation("repository.managed_worktree", "ssh-git", "path", frozenset(), ("repository.continue",),
             "fixed published GitHub SHA in a fresh operation-owned clone/worktree under a managed root; "
             "explicit repository/host/workspace binding and marker checks; no manual checkout writes or push"),
    Mutation("artifact.materialize", "ssh-files", "path", frozenset(), ("checkpoint.continue",),
             "only .batc-inputs in the continuation's fixed managed worktree; no-follow host read-back"),
    Mutation("integration.area", "ssh-git", "path", frozenset(),
             ("integration.preview", "integration.apply", "integration.handoff", "batc integrate"),
             "only <first managed root>/.batc-integration/<name>-<8 hex>/repo.git, a bare repository whose identity "
             "(real path, batc.* markers, a local-config allowlist, no grafts, alternates or replace refs) is "
             "checked before every write; refs only under refs/batc/; other repositories, the person's included, "
             "are only read as the source of git fetch or ls-remote"),
    Mutation("integration.push", "ssh-git", "remote", frozenset(), ("integration.apply",),
             "one normal push of one recorded 40-hex commit to refs/heads/<head ref> of an open, same-repository "
             "PR whose [[github.repos]] entry has integrate, sent to integrate.remote_url with the host's git "
             "credentials; never a +refspec, an empty source, --force, --force-with-lease, --mirror, --all, "
             "--tags, --delete or --prune; never the base, default or a protected branch"),
    Mutation("github.pr.publish", "github", "remote", frozenset(), ("operation_submit",),
             "observe/manage/integrate principal; exact owned PM handoff result, registered bare integration area, "
             "one generated refs/heads/batc/pr-<operation> branch with an exact empty-ref create-only lease; "
             "never replace an existing remote ref; explicit bounded public text; original native HTTP 201 receipt "
             "is the only creation proof; an unknown POST is never resent or adopted from a matching remote PR"),
    Mutation("github.pr.merge", "github", "remote", frozenset(),
             ("github_pr_merge", "operation_submit", "batc delivery merge", "Delivery merge preview"),
             "merge principal and configured repository/method; immutable reviewed head/base/scope checked "
             "before merge-async; unsupported stacks refused; no bypass or local Git writes"),
    Mutation("delivery.merge_and_deploy", "github", "remote", frozenset(),
             ("github_pr_merge", "operation_submit", "batc delivery merge --recipe", "Delivery merge preview"),
             "merge and deploy principal; verified actual merged SHA through the configured recipe; "
             "no dispatch on unverified or expanded merge result"),
    Mutation("github.pr.update", "github", "remote", frozenset(),
             ("github_pr_update", "operation_submit", "batc delivery update-pr", "Delivery metadata drawer"),
             "integrate principal and configured repository with allow_pr_update; title/body only, recorded "
             "read-compare-write-readback; never a BAT session, local folder or PR head update"),
    Mutation("deployment.start", "github", "remote", frozenset(),
             ("deployment_start", "deployment_retry", "operation_submit", "batc delivery deploy", "batc delivery retry"),
             "deploy principal; fixed source reachable from recipe ref, repository identity, recipe digest and "
             "environment generation; configured workflow only; no local Git/BAT mutation"),
    Mutation("deployment.rollback", "github", "remote", frozenset(),
             ("deployment_rollback", "operation_submit", "batc delivery rollback"),
             "deploy principal; a saved verified identity of the same recipe/environment, explicit rollback "
             "support and not_undone limits; a new deployment through that recipe, never a history edit"),
)
BY_ACTION = {m.action: m for m in MUTATIONS}
# The only granted write channel whose frame names no session (its terminal carries the ID).
SESSIONLESS_CHANNELS = frozenset({"workspace:save"})
SESSION_ACTIONS = tuple(m.action for m in MUTATIONS if m.scope == "session")
BAT_WRITE_CHANNELS = WRITE_CHANNELS | ORCHESTRATE_CHANNELS | GUARDED_CHANNELS


@dataclass(frozen=True)
class WriteGrant:
    """Permission for the frames of one checked action on one BAT session ID."""

    host: str
    action: str
    session_id: str
    channels: frozenset[str]
    workdir: str | None = None
    isolation: str | None = None
    controller_authority: dict | None = None
    controller_incarnation: str | None = None


def check_grant(grant: WriteGrant | None, host: str, channel: str, params: dict | None) -> None:
    """Called by BatClient before any write frame; raises before anything is sent."""
    if not isinstance(grant, WriteGrant):
        raise ResourceReadOnly("GRANT_REQUIRED", f"write channel {channel!r} needs a resource policy grant")
    sid = (params or {}).get("sessionId")
    if (grant.host != host or channel not in grant.channels
            or (channel not in SESSIONLESS_CHANNELS and sid != grant.session_id)):
        raise ResourceReadOnly("GRANT_MISMATCH",
                               f"the policy grant for {grant.action} does not cover {channel!r} on this session")
    controller_authority.require_binding(grant.controller_authority)
    row = registry.get(host, grant.session_id)
    controller_authority.require_entry(row, expected_incarnation=grant.controller_incarnation)
    if not grant.controller_incarnation:
        raise ResourceReadOnly("CONTROLLER_OWNER_UNPROVEN", "The grant has no reserved resource incarnation")
    # Sharing a carrier never transfers its controller, even when a helper omitted
    # an explicit shared-session relationship during initial admission.
    source_only = (grant.action == "session.create" and channel == "worktree:create" and grant.workdir is None
                   and registry.is_pending_worktree_source(row))
    carrier = None if source_only else grant.workdir or row.get("worktree_path") or row.get("cwd")
    if carrier:
        for other in registry.list_entries(host):
            if other.get("session_id") != grant.session_id and carrier in {
                    other.get("worktree_path"), None if registry.is_pending_worktree_source(other) else other.get("cwd")}:
                from .pm_bat_operations import permits_completed_carrier
                if permits_completed_carrier(host, row, other):
                    continue
                controller_authority.require_entry(other)
    from .failover_operations import check_writer
    check_writer(host, grant.session_id, channel, workdir=grant.workdir)
    from .worktree_merge_operations import check_writer as check_merge_writer
    check_merge_writer(host, grant.session_id, channel, workdir=grant.workdir or (params or {}).get("cwd") or ((params or {}).get("options") or {}).get("cwd"))


# --------------------------------------------------------------------------- paths
def norm(path: Any) -> str | None:
    """Canonical text form of a host path. Symlinks are resolved by the host (git root), not here."""
    if not isinstance(path, str) or not path.strip().startswith("/"):
        return None
    p = posixpath.normpath(path.strip())
    return "/" + p.lstrip("/")


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def in_managed_root(hc: HostConfig, path: Any) -> bool:
    p = norm(path)
    return bool(p) and ".." not in p.split("/") and any(_within(p, r) for r in hc.managed_roots)


def in_bat_worktrees(path: Any, origin: Any) -> bool:
    """``path`` is ``<dir>/.bat-worktrees/<name>`` where <dir> is ``origin`` or one of its parents."""
    p, o = norm(path), norm(origin)
    if not p or not o:
        return False
    parent = posixpath.dirname(p)
    if posixpath.basename(parent) != BAT_WORKTREES_DIR or not posixpath.basename(p):
        return False
    return _within(o, posixpath.dirname(parent))


# --------------------------------------------------------------------------- classification
@dataclass
class Classification:
    host: str
    session_id: str
    provenance: str
    registry_status: str | None = None
    has_tab: bool = False
    workdir: str | None = None
    workdir_owner: str = UNKNOWN
    isolation: str | None = None
    evidence: list[str] = field(default_factory=list)
    code: str | None = None  # why session-scoped writes are refused (None = allowed)
    reason: str | None = None
    worktree_path: str | None = None
    worktree_made_by: str = "bat"  # "connector": SSH git; "unknown": reviewer carrier is unproven

    @property
    def writable(self) -> bool:
        return self.code is None

    def to_dict(self) -> dict:
        return {
            "provenance": self.provenance,
            "api_access": "managed" if self.writable else "read_only",
            "registry_status": self.registry_status,
            "has_tab": self.has_tab,
            "workdir": self.workdir,
            "workdir_owner": self.workdir_owner,
            "isolation": self.isolation,
            "evidence": list(self.evidence),
            "worktree_made_by": self.worktree_made_by,
            **({"read_only_code": self.code, "read_only_reason": self.reason} if self.code else {}),
        }


def _latest(entries: list[dict], sid: str | None) -> dict | None:
    rows = [e for e in entries if sid and e.get("session_id") == sid]
    if not rows:
        return None
    return max(rows, key=lambda e: (float(e.get("updated_at") or 0), float(e.get("created_at") or 0)))


def _creation_evidence(row: dict) -> str | None:
    if row.get("recovered_from") == "task_journal":
        return "recovered from the task journal with BAT identity checks"
    if row.get("created_at"):
        return "reserved in the connector registry before its BAT start"
    return None


def folder_owner(hc: HostConfig, row: dict, entries: list[dict], depth: int = 0) -> tuple[str, str | None, str]:
    """(owner, isolation, why) for the folder a registry row says its session works in."""
    cwd = norm(row.get("cwd") or row.get("worktree_path"))
    if not cwd:
        return UNKNOWN, None, "the connector record has no working folder"
    if in_managed_root(hc, cwd):
        return OWNER_MANAGED_ROOT, MANAGED_CLONE, "inside a managed root"
    wt = norm(row.get("worktree_path"))
    if not wt or wt != cwd:
        return MANUAL, None, f"{cwd} is not a worktree the connector created (a human checkout)"
    if not (in_bat_worktrees(wt, row.get("origin_cwd")) or in_bat_worktrees(wt, row.get("origin_root"))):
        return MANUAL, None, f"{wt} is not under the workspace's {BAT_WORKTREES_DIR} folder"
    owner_sid = row.get("shares_worktree_with") or row.get("lead_session_id")
    if not owner_sid:
        return OWNER_CONNECTOR_WORKTREE, LEGACY_SHARED_CLONE, "worktree created by the connector"
    parent = _latest(entries, owner_sid)
    if parent is None:
        return MANUAL, None, f"shares the worktree of {str(owner_sid)[:8]}, which the connector did not create"
    if depth >= 4 or not _creation_evidence(parent) or parent.get("status") in _UNCONFIRMED:
        return UNKNOWN, None, f"the owner of the shared worktree ({str(owner_sid)[:8]}) is unproven"
    if norm(parent.get("worktree_path")) != wt:
        return MANUAL, None, f"the shared worktree no longer matches {str(owner_sid)[:8]}'s record"
    return folder_owner(hc, parent, entries, depth + 1)


# BAT's worktree channels act on the repository BAT recorded for the session. For a worktree the connector made
# over SSH, BAT has no record: a rehydrate would register it under the workspace folder (possibly a person's
# checkout, copying its env files in), and a remove would prune that repository.
BAT_WORKTREE_ACTIONS = frozenset({"worktree.rehydrate", "worktree.merge", "worktree.remove"})


def worktree_maker(row: dict, entries: list[dict] | None = None, lead_of=None, *, host: str | None = None) -> str:
    root = registry_worktree_root(entries if entries is not None else [row],
                                  host if host is not None else row.get("host", ""), row.get("session_id"), lead_of)
    # Preserve every previous refusal, including conflicting current-row creation evidence.
    if connector_made(row) or connector_made(root or {}):
        return "connector"
    if root and root.get("role") == "reviewer":
        # A reviewer shares its lead's worktree; its session creation is not worktree creation proof.
        # Observation can resolve legacy rows through its journal, but raw CLI policy has no such authority.
        return "unknown"
    return "bat"


def classify(hc: HostConfig, session_id: str, *, terminal: dict | None, entries: list[dict]) -> Classification:
    """Registry and workspace-document classification; no host round trips."""
    tab = terminal if terminal and not terminal.get("_orchestrated") else None
    row = _latest(entries, session_id)
    cls = Classification(host=hc.name, session_id=session_id, provenance=UNKNOWN, has_tab=tab is not None)
    if row is None:
        cls.workdir = norm((tab or terminal or {}).get("worktreePath") or (tab or terminal or {}).get("cwd"))
        if tab is not None:
            cls.provenance, cls.workdir_owner = MANUAL, MANUAL
            cls.evidence.append("BAT workspace tab without a connector creation record")
            cls.code, cls.reason = ("MANUAL_READ_ONLY",
                                    "this session was created in BAT, not by the connector; the API only reads it")
        else:
            cls.evidence.append("no connector creation record and no BAT workspace tab")
            cls.code, cls.reason = "UNKNOWN_READ_ONLY", "the session's origin cannot be proven"
        return cls
    cls.registry_status = row.get("status")
    cls.worktree_path = norm(row.get("worktree_path"))
    cls.worktree_made_by = worktree_maker(row, entries, host=hc.name)
    created = _creation_evidence(row)
    if not created:
        cls.code, cls.reason = "UNKNOWN_READ_ONLY", "the connector record carries no creation evidence"
        return cls
    cls.evidence.append(created)
    if cls.registry_status in _UNCONFIRMED:
        cls.code, cls.reason = ("UNKNOWN_READ_ONLY",
                                f"connector-reserved session but {_UNCONFIRMED[cls.registry_status]}; reconcile it first")
        return cls
    cls.provenance = MANAGED
    cls.workdir_owner, cls.isolation, why = folder_owner(hc, row, entries)
    cls.workdir = norm(row.get("cwd") or row.get("worktree_path"))
    cls.evidence.append(why)
    if tab is not None:
        for tab_key, row_key in (("cwd", "cwd"), ("worktreePath", "worktree_path")):
            a, b = norm(tab.get(tab_key)), norm(row.get(row_key))
            # A tab folder the record lacks counts too: worktree actions would otherwise act on the tab's path.
            if a and a != b and (b or tab_key == "worktreePath"):
                cls.code, cls.reason = ("BINDING_MISMATCH",
                                        f"the BAT tab's {tab_key} {a} differs from the connector record {b}")
                return cls
    if cls.workdir_owner not in OWNED:
        cls.code = "WORKDIR_NOT_MANAGED" if cls.workdir_owner == MANUAL else "UNKNOWN_READ_ONLY"
        cls.reason = f"legacy boundary: {why}; handle this session in BAT"
    return cls


def classify_row_for_read(hc: HostConfig, session_id: str, *, has_tab: bool, entries: list[dict]) -> dict:
    """Small provenance block for list views (registry and tab facts only)."""
    terminal = {"id": session_id} if has_tab else {"id": session_id, "_orchestrated": True}
    cls = classify(hc, session_id, terminal=terminal, entries=entries)
    out = {"provenance": cls.provenance, "api_access": "managed" if cls.writable else "read_only"}
    if cls.code:
        out["read_only_code"] = cls.code
    if cls.isolation:
        out["isolation"] = cls.isolation
    return out


# --------------------------------------------------------------------------- live checks
@dataclass
class LiveCheck:
    issue: tuple[str, str] | None = None  # binding mismatch (code, reason)
    folder_missing: bool = False
    observed: dict = field(default_factory=dict)


async def live_check(c, cls: Classification, *, worktree: bool = True, folder: bool = True) -> LiveCheck:
    """Compare BAT's view of the session and its folder with the connector record (reads only).

    ``worktree:status`` computes the branch diff and can take a minute on a large branch, so only worktree
    actions ask for it; session.send/answer/permissions bind the session through its meta cwd and git root,
    and interrupt/stop (which do not touch the folder) through the meta cwd alone.
    """
    out = LiveCheck()
    meta = await c.invoke("claude:get-session-meta", {"sessionId": cls.session_id})
    meta_cwd = norm(meta.get("cwd")) if isinstance(meta, dict) else None
    out.observed["meta_cwd"] = meta_cwd
    if meta_cwd and cls.workdir and meta_cwd != cls.workdir:
        out.issue = ("BINDING_MISMATCH", f"BAT runs the session in {meta_cwd}, the connector record says {cls.workdir}")
        return out
    if worktree and cls.worktree_path:
        st = await c.invoke("worktree:status", {"sessionId": cls.session_id})
        st_path = norm(st.get("worktreePath")) if isinstance(st, dict) else None
        out.observed["worktree_status_path"] = st_path
        if st_path and st_path != cls.worktree_path:
            out.issue = ("BINDING_MISMATCH",
                         f"BAT tracks worktree {st_path}, the connector record says {cls.worktree_path}")
            return out
    if folder and cls.workdir:
        root = await c.invoke("git:getRoot", {"cwd": cls.workdir})
        root_n = norm(root) if isinstance(root, str) else None
        out.observed["git_root"] = root_n
        if not root_n:
            out.folder_missing = True
        elif cls.workdir_owner == OWNER_CONNECTOR_WORKTREE and root_n != cls.workdir:
            out.issue = ("BINDING_MISMATCH",
                         f"the git root of {cls.workdir} is {root_n} (a link into another checkout?)")
        elif cls.workdir_owner == OWNER_MANAGED_ROOT and not in_managed_root(c.host, root_n):
            out.issue = ("BINDING_MISMATCH", f"the git root of {cls.workdir} is {root_n}, outside the managed roots")
    return out


def _decide(cls: Classification, m: Mutation, live: LiveCheck | None) -> tuple[str, str] | None:
    if cls.code:
        return cls.code, cls.reason or "read-only"
    if cls.registry_status in registry.RETIRED and m.action.startswith("session.") and m.action != "session.stop":
        return "SESSION_RETIRED", "this session ID left the host cap; start a new session ID"
    if m.action in BAT_WORKTREE_ACTIONS and cls.worktree_made_by == "connector":
        return ("NOT_A_BAT_WORKTREE", "the connector made this worktree over SSH and BAT has no record of it; "
                "BAT's worktree actions would act on the workspace folder's repository")
    if m.action in BAT_WORKTREE_ACTIONS and cls.worktree_made_by == "unknown":
        return "NOT_A_BAT_WORKTREE", "the reviewer's shared worktree creation root cannot be proven from the registry"
    if m.action in BAT_WORKTREE_ACTIONS and not cls.worktree_path:
        return "NOT_A_BAT_WORKTREE", "the connector has no recorded worktree for this session"
    if live is not None:
        if live.issue:
            return live.issue
        if live.folder_missing and m.live_folder:
            return "WORKDIR_MISSING", f"the session's folder {cls.workdir} is not readable on the host"
    return None


# --------------------------------------------------------------------------- authorization
def _entries(host: str) -> list[dict]:
    return registry.list_entries(host)


def _live_scope(action: str | None) -> dict:
    if action is None:  # read views: every check
        return {"worktree": True, "folder": True}
    m = BY_ACTION[action]
    on_worktree = action.startswith("worktree.")
    return {"worktree": on_worktree, "folder": on_worktree or m.live_folder}


async def classify_live(fleet, host: str, t: dict, action: str | None = None
                        ) -> tuple[Classification, LiveCheck | None]:
    hc = fleet.config.host(host)
    cls = classify(hc, t["id"], terminal=t, entries=_entries(host))
    live = await live_check(fleet.client(host), cls, **_live_scope(action)) if cls.writable else None
    return cls, live


async def authorize_session(fleet, host: str, action: str, t: dict, *, live: LiveCheck | None = None,
                            cls: Classification | None = None, expected_incarnation: str | None = None) -> WriteGrant:
    """Grant one session-scoped action, or raise ResourceReadOnly before any write frame."""
    from .cleanup import guard
    guard(host, session_id=t["id"], path=t.get("cwd") or t.get("worktreePath"),
          writer=action not in {"session.stop", "session.interrupt"})
    m = BY_ACTION[action]
    if m.scope != "session":
        raise BatError(f"internal: {action} is not a session action")
    original = registry.get(host, t["id"])
    if cls is None:
        cls, live = await classify_live(fleet, host, t, action)
    refusal = _decide(cls, m, live)
    if refusal:
        raise ResourceReadOnly(refusal[0], f"{action} refused for session {t['id'][:8]}: {refusal[1]}")
    # Pin before live reads; an admitted operation additionally supplies its
    # durable nonce so an earlier resolution await cannot select a replacement.
    controller_authority.require_entry(original, expected_incarnation=expected_incarnation)
    row = controller_authority.require_entry(registry.get(host, t["id"]),
                                            expected_incarnation=original["controller_incarnation"])
    return WriteGrant(host, action, t["id"], m.channels, cls.workdir, cls.isolation,
                      controller_authority.require(), row["controller_incarnation"])


def _create_grant(host: str, session_id: str, workdir: str | None, isolation: str | None) -> WriteGrant:
    m = BY_ACTION["session.create"]
    return WriteGrant(host, m.action, session_id, m.channels, workdir, isolation,
                      controller_authority.require(), controller_authority.reservation_identity(host, session_id))


def _check_resolved(hc: HostConfig, path: str, git_roots: dict | None) -> None:
    """A managed-root path must also be managed after the host resolves it (a symlink can point anywhere)."""
    from .cleanup import guard
    guard(hc.name, path=path)
    real = norm((git_roots or {}).get(path))
    if real and not in_managed_root(hc, real):
        raise ResourceReadOnly("DESTINATION_MANUAL",
                               f"{path} resolves to {real}, outside the managed roots (a link into another "
                               "checkout?); list real paths in managed_roots")


def authorize_new_session(hc: HostConfig, session_id: str, *, folder: str, use_worktree: bool,
                          cwd_override: str | None = None, task_id: str | None = None,
                          git_roots: dict | None = None) -> WriteGrant:
    """Destination check for session_start: where may a brand-new session work?

    ``git_roots`` maps a normalized path to the git root the host reports for it (``git:getRoot``), so a
    managed-root path that is really a link into a human checkout is refused before anything is written.
    """
    root = norm(folder)
    if not root:
        raise ResourceReadOnly("DESTINATION_UNKNOWN", f"workspace folder {folder!r} is not an absolute path")
    if cwd_override is not None:
        path = norm(cwd_override)
        if path and in_managed_root(hc, path):
            _check_resolved(hc, path, git_roots)
            return _create_grant(hc.name, session_id, path, MANAGED_CLONE)
        suffix = (task_id or "").replace("-", "")[:12]
        expected = posixpath.join(root, BAT_WORKTREES_DIR, f"batc-task-{suffix}")
        if not (task_id and len(suffix) == 12 and path == expected):
            raise ResourceReadOnly("DESTINATION_MANUAL", f"{cwd_override} is not a connector task worktree")
        if not (hc.shared_clone_worktrees or in_managed_root(hc, root)):
            raise ResourceReadOnly("DESTINATION_MANUAL", "shared_clone_worktrees = false on this host")
        return _create_grant(hc.name, session_id, path, LEGACY_SHARED_CLONE)
    if in_managed_root(hc, root):
        _check_resolved(hc, root, git_roots)
        return _create_grant(hc.name, session_id, None if use_worktree else root, MANAGED_CLONE)
    if not use_worktree:
        raise ResourceReadOnly(
            "DESTINATION_MANUAL",
            f"{root} is a human checkout: a new session may not work in it directly; use a worktree "
            "(use_worktree=true) or a managed root")
    if not hc.shared_clone_worktrees:
        raise ResourceReadOnly(
            "DESTINATION_MANUAL",
            f"{root} is not inside a managed root and shared_clone_worktrees = false on this host")
    return _create_grant(hc.name, session_id, None, LEGACY_SHARED_CLONE)


def check_new_worktree(grant: WriteGrant, hc: HostConfig, folder: str, worktree_path: Any,
                       git_root: str | None = None) -> WriteGrant:
    """After worktree:create: the host-chosen folder must be where the connector expects it.

    BAT puts the worktree under the folder's git root as the host resolves it, so a workspace opened through a
    symlink gets a worktree under the resolved root; ``git_root`` (from ``git:getRoot``) accepts that.
    """
    path = norm(worktree_path)
    if not path or not (in_bat_worktrees(path, folder) or (git_root and in_bat_worktrees(path, git_root))
                        or in_managed_root(hc, path)):
        raise ResourceReadOnly("DESTINATION_UNKNOWN", f"BAT created the worktree at an unexpected path {worktree_path!r}")
    if grant.isolation == MANAGED_CLONE and not in_managed_root(hc, path):
        raise ResourceReadOnly("DESTINATION_UNKNOWN", f"BAT created the worktree outside the managed root: {path}")
    return replace(grant, workdir=path)


async def authorize_shared_session(fleet, host: str, session_id: str, owner: dict) -> WriteGrant:
    """A new session that works in an existing connector-owned folder (failover successor, reviewer)."""
    hc = fleet.config.host(host)
    from .failover_operations import check_writer
    check_writer(host, owner['id'], shared=True)
    from .worktree_merge_operations import check_writer as check_merge_writer
    check_merge_writer(host, owner['id'], workdir=owner.get('worktreePath') or owner.get('cwd'))
    cls = classify(hc, owner["id"], terminal=owner, entries=_entries(host))
    if cls.code:
        raise ResourceReadOnly(cls.code, f"a new session cannot share {owner['id'][:8]}'s folder: {cls.reason}")
    controller_authority.require_entry(registry.get(host, owner["id"]))
    live = await live_check(fleet.client(host), cls, worktree=False)
    if live.issue:
        raise ResourceReadOnly(live.issue[0], live.issue[1])
    if live.folder_missing:
        raise ResourceReadOnly("WORKDIR_MISSING", f"{cls.workdir} is not readable on the host")
    return _create_grant(host, session_id, cls.workdir, cls.isolation)


def authorize_register_tab(host: str, session_id: str) -> WriteGrant:
    """Append-only tab registration, only for a session the connector has just started."""
    row = _latest(_entries(host), session_id)
    if not row or row.get("status") != "active" or not _creation_evidence(row):
        raise ResourceReadOnly("UNKNOWN_READ_ONLY", "tabs are only registered for sessions the connector started")
    m = BY_ACTION["workspace.register_tab"]
    controller_authority.require_entry(row)
    return WriteGrant(host, m.action, session_id, m.channels, controller_authority=controller_authority.require(),
                      controller_incarnation=row["controller_incarnation"])


def merge_origin(hc: HostConfig, session_id: str, t: dict, ws: dict) -> str | None:
    """The main checkout a worktree merge would write: the folder recorded at start, which must still be the
    workspace's folder. A tab whose workspace now points elsewhere is a binding mismatch, not a new target."""
    recorded = norm((_latest(_entries(hc.name), session_id) or {}).get("origin_cwd"))
    w = next((x for x in ws.get("workspaces") or [] if x.get("id") == t.get("workspaceId")), {})
    current = norm(t.get("_origin_cwd") or w.get("folderPath"))
    if recorded and current and recorded != current:
        raise ResourceReadOnly("BINDING_MISMATCH",
                               f"the workspace folder is now {current}; the session was started from {recorded}")
    return recorded or current


def check_merge_destination(hc: HostConfig, origin: Any) -> None:
    """worktree:merge runs in the main checkout; only a managed clone may receive it."""
    if not in_managed_root(hc, origin):
        raise ResourceReadOnly(
            "DESTINATION_MANUAL",
            f"the merge destination {origin} is a human checkout; the connector never merges into it "
            "(integrate in a managed clone or through a pull request)")


def authorize_external_worktree(hc: HostConfig, root: str, path: str, branch: str, task_id: str) -> None:
    """Before any SSH git: the task worktree goes inside the workspace's clone only where that is allowed."""
    check_external_worktree(root, path, branch, task_id)
    if not (hc.shared_clone_worktrees or in_managed_root(hc, root)):
        raise ResourceReadOnly("DESTINATION_MANUAL",
                               f"{root} is not inside a managed root and shared_clone_worktrees = false on this host")


def check_checkpoint_worktree(hc: HostConfig, clone: str, path: str, branch: str) -> None:
    """A checkpoint execution's clone and worktree: fixed names one level inside a managed root."""
    from .cleanup import guard
    guard(hc.name, path=path, branch=branch)
    c, p = norm(clone), norm(path)
    root = next((r for r in hc.managed_roots if c and posixpath.dirname(c) == r.rstrip("/")), None)
    name = posixpath.basename(p or "")
    suffix = name[len("batc-cp-"):]
    if (not root or ".." in (c or "").split("/") or p != posixpath.join(c, BAT_WORKTREES_DIR, name)
            or not name.startswith("batc-cp-") or len(suffix) != 12
            or any(ch not in "0123456789abcdef" for ch in suffix) or branch != f"batc/cp-{suffix}"):
        raise ResourceReadOnly("DESTINATION_UNKNOWN", f"{path} is not a connector checkpoint worktree in a managed root")


def check_published_worktree(hc: HostConfig, clone: str, path: str, branch: str) -> None:
    from .cleanup import guard
    guard(hc.name, path=path, branch=branch)
    c, p = norm(clone), norm(path)
    name = posixpath.basename(c or "")
    suffix = name.removeprefix("batc-published-")
    if (not re.fullmatch(r"[0-9a-f]{32}", suffix) or
            posixpath.dirname(c or "") not in tuple(r.rstrip("/") for r in hc.managed_roots) or
            p != c + "/.bat-worktrees/batc-published-" + suffix[:12] or branch != "batc/published-" + suffix[:12]):
        raise ResourceReadOnly("DESTINATION_UNKNOWN", "not an operation-owned published repository carrier")


# --------------------------------------------------------------------------- integration (W06)
INTEGRATION_DIR = ".batc-integration"
_AREA_NAME = re.compile(r"[A-Za-z0-9._-]+-[0-9a-f]{8}")
# A PR head ref integration may push to: no leading '-', no '..', '//' or '@{', no trailing '.' or '/'.
HEAD_REF = re.compile(r"(?!-)(?!.*\.\.)(?!.*//)(?!.*@\{)[A-Za-z0-9._/-]{1,200}(?<![./])")
_HEX40 = re.compile(r"[0-9a-f]{40}")


def integration_area_path(hc: HostConfig, host: str, repository: str, remote_url: str) -> str:
    """One bare integration repository per (host, repository, remote_url); a new URL never relabels an old one."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", repository.split("/", 1)[-1]).strip(".-") or "repo"
    digest = hashlib.sha256(f"{host}\0{repository.lower()}\0{remote_url}".encode()).hexdigest()[:8]
    return posixpath.join(norm(hc.managed_roots[0]) or "", INTEGRATION_DIR, f"{name}-{digest}")


def check_integration_area(hc: HostConfig, path: str) -> None:
    """The integration area: a fixed name directly under <first managed root>/.batc-integration/."""
    from .cleanup import guard
    guard(hc.name, path=path)
    p = norm(path) or ""
    parent = posixpath.join(norm(hc.managed_roots[0]) or "", INTEGRATION_DIR) if hc.managed_roots else ""
    if (not parent or posixpath.dirname(p) != parent or not _AREA_NAME.fullmatch(posixpath.basename(p))
            or not in_managed_root(hc, p)):
        raise ResourceReadOnly("DESTINATION_MANUAL", f"{path} is not a connector integration area")


def check_repair_worktree(hc: HostConfig, area: str, path: str, branch: str) -> None:
    """A conflict-resolving worktree: <area>/wt/batc-fix-<12 hex> on branch batc/fix-<12 hex>, nothing else."""
    from .cleanup import guard
    guard(hc.name, path=path, branch=branch)
    check_integration_area(hc, area)
    name = posixpath.basename(norm(path) or "")
    suffix = name[len("batc-fix-"):]
    if (norm(path) != posixpath.join(norm(area) or "", "wt", name) or not name.startswith("batc-fix-")
            or not re.fullmatch(r"[0-9a-f]{12}", suffix) or branch != f"batc/fix-{suffix}"):
        raise ResourceReadOnly("DESTINATION_UNKNOWN", f"{path} is not a connector repair worktree")


def classify_integration_source(hc: HostConfig, kind: str, location: str | None) -> tuple[str, bool]:
    """(location_class, fetch_only) for a source. Only a managed clone may be more than a fetch source; a person's
    checkout is only ever the repository argument of git fetch or ls-remote."""
    if kind == "branch":
        return "remote", True
    if kind == "checkpoint_run":
        if not in_managed_root(hc, location):
            raise ResourceReadOnly("DESTINATION_MANUAL", "an agent result must live in a connector clone")
        return MANAGED_CLONE, False
    if kind == "checkpoint":
        return "human_checkout", True
    raise ResourceReadOnly("DESTINATION_UNKNOWN", f"unknown source kind {kind!r}")


def check_push_target(protected: tuple[str, ...], pr: dict) -> str:
    """The PR head ref integration may push to, or why not (blocking codes, never a fallback)."""
    head, base = pr.get("head") or {}, pr.get("base") or {}
    if pr.get("state") != "open" or pr.get("merged"):
        raise ResourceReadOnly("PR_CLOSED", f"PR #{pr.get('number')} is {pr.get('state')}")
    if not head.get("repo") or (head.get("repo") or {}).get("id") != (base.get("repo") or {}).get("id"):
        raise ResourceReadOnly("PR_HEAD_IN_FORK", "the PR's head branch is not in the same repository")
    ref = str(head.get("ref") or "")
    default = (base.get("repo") or {}).get("default_branch")
    if (not HEAD_REF.fullmatch(ref) or ref in {base.get("ref"), default}
            or any(fnmatch.fnmatchcase(ref, g) for g in protected)):
        raise ResourceReadOnly("TARGET_REF_FORBIDDEN", f"{ref!r} is the base, default or a protected branch")
    return ref


def push_refspec(sha: str, head_ref: str) -> str:
    """The only refspec integration pushes: an exact commit to one branch. It can never be forced or delete."""
    if not _HEX40.fullmatch(sha or "") or not HEAD_REF.fullmatch(head_ref or ""):
        raise ResourceReadOnly("INTERNAL_PUSH_SHAPE", "push needs a 40-hex commit and a valid branch name")
    return f"{sha}:refs/heads/{head_ref}"


def check_external_worktree(root: str, path: str, branch: str, task_id: str) -> None:
    """The task service's SSH-created worktree may only use its fixed connector-owned name."""
    from .cleanup import guard
    guard(path=path, branch=branch)
    suffix = task_id.replace("-", "")[:12]
    if (len(suffix) != 12 or any(ch not in "0123456789abcdef" for ch in suffix)
            or norm(path) != posixpath.join(norm(root) or "", BAT_WORKTREES_DIR, f"batc-task-{suffix}")
            or branch != f"batc/task-{suffix}"):
        raise ResourceReadOnly("DESTINATION_MANUAL", "external worktree identity is not connector-owned")



def check_cleanup_worktree(hc: HostConfig, repository: str, path: str | None, branch: str) -> None:
    """SSH cleanup destination; creation intent and live Git binding are checked by the cleanup handler."""
    from .cleanup import guard
    guard(hc.name, path=path or repository, branch=branch)
    if not hc.writes or not hc.orchestrate:
        raise ResourceReadOnly("TIER_DISABLED", "cleanup needs the host write and orchestrate tiers")
    if (not in_managed_root(hc, repository) or (path and (not in_managed_root(hc, path) or
            not path.startswith(repository.rstrip("/") + "/")))):
        raise ResourceReadOnly("WORKDIR_NOT_MANAGED", "cleanup requires a managed clone or integration area")
    if not branch or not branch.startswith(("batc/", "bat/")):
        raise ResourceReadOnly("UNKNOWN_READ_ONLY", "cleanup requires a proven connector branch")

# --------------------------------------------------------------------------- read views
async def session_policy(fleet, host: str, session_id: str | None = None) -> dict:
    """Host policy and the mutation table, or one session's classification and per-action verdicts."""
    hc = fleet.config.host(host)
    base = {
        "host": host,
        "writes_enabled": fleet.writes_enabled(host),
        "orchestrate_enabled": fleet.orchestrate_enabled(host),
        "managed_roots": list(hc.managed_roots),
        "shared_clone_worktrees": hc.shared_clone_worktrees,
    }
    if not session_id:
        return {**base, "mutations": [
            {"action": m.action, "via": m.via, "scope": m.scope, "tier": m.tier, "channels": sorted(m.channels),
             "entry_points": list(m.entry_points), "rule": m.rule} for m in MUTATIONS]}
    from .orchestrate import _origin_cwd
    from .service import _resolve_session

    t, ws = await _resolve_session(fleet.client(host), session_id)
    cls, live = await classify_live(fleet, host, t)
    tiers = {"write": base["writes_enabled"], "orchestrate": base["orchestrate_enabled"]}
    caller_refusal = None
    presentation = cls.to_dict()
    if cls.writable:
        try:
            controller_authority.require_entry(registry.get(host, t["id"]))
        except ResourceReadOnly as error:
            caller_refusal = (error.code, str(error))
            presentation.update(api_access="read_only", read_only_code=error.code, read_only_reason=str(error))
    actions = {}
    for action in SESSION_ACTIONS:
        m = BY_ACTION[action]
        refusal = _decide(cls, m, live)
        if refusal is None:
            refusal = caller_refusal
        if refusal is None and not tiers[m.tier]:
            refusal = ("TIER_DISABLED", f"the {m.tier} tier is off for host {host}")
        actions[action] = ({"allowed": False, "code": refusal[0], "reason": refusal[1]} if refusal
                           else {"allowed": True})
        if action == "worktree.merge" and not refusal:
            try:
                check_merge_destination(hc, _origin_cwd(t, ws))
            except ResourceReadOnly as e:
                actions[action] = {"allowed": False, "code": e.code, "reason": str(e)}
    return {**base, "session_id": t["id"], **presentation,
            "observed": live.observed if live else None, "actions": actions}


def check_artifact_storage(path) -> None:
    """Configured local store: reject symlinks before setup or any write."""
    from pathlib import Path

    p = Path(path)
    if not p.is_absolute() or ".." in p.parts or p == Path(p.anchor):
        raise ResourceReadOnly("DESTINATION_UNKNOWN", "artifact store must be a real absolute directory")
    for component in (p, *p.parents):
        if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()):
            raise ResourceReadOnly("DESTINATION_UNKNOWN", "artifact store may not contain symlinks or junctions")


def check_artifact_destination(hc: HostConfig, clone: str, worktree: str, branch: str,
                               relative_path: str, *, published: bool = False) -> None:
    """Inputs use an exact checkpoint or published carrier, never arbitrary managed paths."""
    import re

    (check_published_worktree if published else check_checkpoint_worktree)(hc, clone, worktree, branch)
    parts = relative_path.split("/") if isinstance(relative_path, str) else []
    if (len(parts) != 3 or parts[0] != ".batc-inputs"
            or not re.fullmatch(r"art_[0-9a-f]{32}-r[1-9][0-9]*", parts[1])
            or not parts[2] or parts[2] in {".", "..", ".git"}
            or any(c in parts[2] for c in "/\\") or any(ord(c) < 32 or ord(c) == 127 for c in parts[2])):
        raise ResourceReadOnly("DESTINATION_UNKNOWN", "not a continuation input path")


# AHP capability issuance lives beside BAT issuance, never in an adapter or route.
@dataclass(frozen=True, eq=False)
class AhpGrant:
    resource_id: str
    operation_id: str
    binding_json: str
    controller_incarnation: str
    credential_id: str
    frame_digest: str
    turn_id: str
    methods: frozenset[str]
    actions: frozenset[str]


# Object identity prevents construction, copying or dataclass replacement from
# minting a capability. The stored digest also detects mutation of a minted one.
_AHP_GRANTS = WeakKeyDictionary()


def _ahp_signature(grant):
    return (grant.resource_id, grant.operation_id, grant.binding_json, grant.controller_incarnation,
            grant.credential_id, grant.frame_digest, grant.turn_id, grant.methods, grant.actions)


def mint_ahp_grant(ctx, resource_id, principal, frame, *, current):
    """Internal seam only: no registered action calls this policy issuer."""
    import json

    from . import ahp_owned_resources as ahp
    live = current()
    receipt = ahp.owned(ctx.service.journal, resource_id, live)
    value = AhpGrant(resource_id, ctx.operation_id, json.dumps(receipt['binding'], sort_keys=True),
        receipt['controller_incarnation'], principal.credential_id, ahp.digest(frame),
        frame['params']['action']['turnId'], frozenset({'dispatchAction'}), frozenset({'chat/turnStarted'}))
    _AHP_GRANTS[value] = _ahp_signature(value)
    try:
        check_ahp_grant(value, ctx, frame, current=current)
    except BaseException:
        del _AHP_GRANTS[value]
        raise
    return value


def check_ahp_grant(grant, ctx, frame, *, current):
    """Synchronous current authority, identity and execution checks at every barrier."""
    import json
    import time

    from . import ahp_owned_resources as ahp
    from . import pm_lifecycle_gates, task_control
    if type(grant) is not AhpGrant or _AHP_GRANTS.get(grant) != _ahp_signature(grant):
        ahp.refuse('AHP_GRANT_REQUIRED')
    b = json.loads(grant.binding_json)
    params = frame.get('params', {})
    if (ctx.operation_id != grant.operation_id or frame.get('method') not in grant.methods
            or params.get('action', {}).get('type') not in grant.actions
            or params.get('channel') != b['chat_uri'] or params.get('action', {}).get('turnId') != grant.turn_id
            or ahp.digest(frame) != grant.frame_digest):
        ahp.refuse('AHP_GRANT_MISMATCH')
    ctx.require_controller()
    live = current()
    if not 0 <= time.time() - live['binding']['work_started_at'] < 1800:
        ahp.refuse('AHP_BUDGET_EXPIRED')
    receipt = ahp.owned(ctx.service.journal, grant.resource_id, live)
    controller_authority.require_entry(receipt, expected_incarnation=grant.controller_incarnation)
    if live['binding'] != b:
        ahp.refuse('AHP_BINDING_CHANGED')
    principal = ctx.service.db.execute('SELECT actor,scopes,expires_at,revoked_at FROM api_principals WHERE token_hash=?',
                                       (grant.credential_id,)).fetchone()
    actual = controller_authority.require()
    if (principal is None or principal['actor'] != actual['actor'] or principal['revoked_at'] is not None
            or principal['expires_at'] is not None and principal['expires_at'] <= time.time()
            or not {'observe', 'operate'} <= set(json.loads(principal['scopes']))
            or not {'observe', 'operate'} <= set(actual['scopes'])):
        ahp.refuse('AHP_SCOPE_CHANGED')
    task_control.check_binding(ctx)
    pm_lifecycle_gates.require_local(ctx.service.db, ctx.operation_id)
    pm_lifecycle_gates._require(pm_lifecycle_gates.head(ctx.service.db, b['workspace_id']), b['lifecycle_epoch'])
    ctx.check_cancel()
    row = ctx.service.db.execute('''SELECT response FROM operation_steps
        WHERE operation_id=? AND name=? AND status='succeeded' ''',
        (b['managed_operation_id'], b['managed_step'])).fetchone()
    managed_op = ctx.service._row(b['managed_operation_id'])
    controller_authority.require_binding(controller_authority.operation_binding(managed_op or {}))
    if row is None or ahp.digest(json.loads(row['response'])) != b['managed_receipt_digest']:
        ahp.refuse('AHP_MANAGED_DIRECTORY_CHANGED')
    directory = json.loads(row['response'])
    if directory.get('host') != b['host'] or directory.get('path') != b['workdir'] or not directory.get('creation_ref'):
        ahp.refuse('AHP_MANAGED_DIRECTORY_CHANGED')
    if live['cancelled'] is not False:
        ahp.refuse('AHP_CANCELLED')
    if live['permission_allowed'] is not True:
        ahp.refuse('AHP_PERMISSION_REQUIRED')
    if live['busy'] is not False:
        ahp.refuse('AHP_BUSY')
    if ahp.pending(ctx.service.journal, grant.resource_id):
        ahp.refuse('AHP_SENT_UNKNOWN')
