"""PM work profiles (v1.3 E01): append-only revisions, defaults and readiness."""
from __future__ import annotations

import calendar
import hashlib
import json
import re
import time
import uuid

from . import pm_source_reads
from .api_auth import Principal
from .errors import TokenUnavailable
from .host_git import LOCAL_GIT_REASONS
from .inventory import _observation_reason, _valid_until
from .operations import ActionDef, OperationError
from .pm_workspace import admitted_principal, principal_binding, require_member

ACTION = "pm.work_profile.save"
MAX_DOCUMENT_BYTES = 16384
_PROFILE_ID = re.compile(r"wpf_[0-9a-f]{32}")
_DOCUMENT_KEYS = frozenset({
    "version", "binding", "agent", "model", "delivery_policy_ref", "budget_policy_ref", "adapter",
})
_PARAM_KEYS = frozenset({
    "profile_id", "expected_revision", "document", "withdrawn", "make_default",
    "expected_default_sequence",
})
_FORBIDDEN = frozenset({
    "secret_ref", "token", "token_ref", "password", "authorization", "credential",
    "fingerprint", "url", "endpoint",
})
_AGENTS = frozenset({"claude", "codex"})
_OMITTED = object()
_ABSENT = object()
_CONTRACT = "work-profile-readiness/v1"
_FIX_ROUTE = "POST /api/v1/operations"
_FIX_HASH = "#/settings?section=connection"
_DISCOVERY_REASONS = frozenset({"DISCOVERY_SCOPE_CHANGED", "SOURCE_UNAVAILABLE"})
_STATE_NAMES = (
    "discovered", "signed_in_or_available", "can_observe", "can_start_own",
    "can_continue_own", "last_verified_execution",
)
# action and status stay literals so the plan does not depend on bound values.
_CONTINUE_SQL = """SELECT operation_id, target, params, result, external_refs, created_at
FROM operations
WHERE action = 'repository.continue'
  AND status = 'succeeded'
  AND json_extract(target, '$.host') = ?
  AND json_extract(target, '$.workspace_id') = ?
ORDER BY created_at DESC, operation_id DESC"""

_PROFILE_DDL = (
    """CREATE TABLE IF NOT EXISTS work_profiles (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        withdrawn INTEGER NOT NULL CHECK (withdrawn IN (0, 1)),
        document TEXT NOT NULL,
        digest TEXT NOT NULL,
        operation_id TEXT NOT NULL UNIQUE,
        created_at REAL NOT NULL,
        UNIQUE (profile_id, revision)
    )""",
    """CREATE INDEX IF NOT EXISTS work_profiles_workspace
        ON work_profiles(workspace_id, profile_id, revision)""",
    """CREATE INDEX IF NOT EXISTS work_profiles_cursor
        ON work_profiles(workspace_id, sequence)""",
    """CREATE TRIGGER IF NOT EXISTS work_profiles_update_immutable
        BEFORE UPDATE ON work_profiles
        BEGIN SELECT RAISE(ABORT, 'work profile history is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS work_profiles_delete_immutable
        BEFORE DELETE ON work_profiles
        BEGIN SELECT RAISE(ABORT, 'work profile history is immutable'); END""",
    """CREATE TABLE IF NOT EXISTS work_profile_defaults (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_id TEXT NOT NULL,
        profile_id TEXT,
        operation_id TEXT NOT NULL UNIQUE,
        created_at REAL NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS work_profile_defaults_workspace
        ON work_profile_defaults(workspace_id, sequence)""",
    """CREATE TRIGGER IF NOT EXISTS work_profile_defaults_update_immutable
        BEFORE UPDATE ON work_profile_defaults
        BEGIN SELECT RAISE(ABORT, 'work profile default history is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS work_profile_defaults_delete_immutable
        BEFORE DELETE ON work_profile_defaults
        BEGIN SELECT RAISE(ABORT, 'work profile default history is immutable'); END""",
    """CREATE INDEX IF NOT EXISTS operations_continue_succeeded
        ON operations(json_extract(target, '$.host'), json_extract(target, '$.workspace_id'), created_at, operation_id)
        WHERE action = 'repository.continue' AND status = 'succeeded'""",
)


def schema(journal) -> None:
    """Idempotent additive DDL; does not allocate user_version or a data step."""
    with journal.tx():
        for statement in _PROFILE_DDL:
            journal.db.execute(statement)


def install(ops) -> None:
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(
            ACTION, "manage", "Append one PM work profile revision", _run, _admit, ("workspace_id",),
        ))


def capabilities(ops=None) -> dict:
    """The same object for features.pm_workspaces and pm_capabilities_get."""
    available = True if ops is None else ACTION in ops.actions
    return {
        "version": 1,
        "contract": _CONTRACT,
        "supported": available,
        "available": available,
        "write_action": ACTION,
        "reads": ["pm_work_profiles_list", "pm_work_profile_get", "pm_work_profile_readiness_get"],
        "mcp_tools": ["pm_work_profiles_list", "pm_work_profile_get", "pm_work_profile_readiness_get"],
        "routes": {
            "list": "/api/v1/pm/workspaces/{workspace_id}/work-profiles",
            "get": "/api/v1/pm/workspaces/{workspace_id}/work-profiles/{profile_id}",
            "readiness": "/api/v1/pm/workspaces/{workspace_id}/work-profiles/{profile_id}/readiness",
        },
        "required_scopes": {"read": ["observe"], "write": ["observe", "manage"]},
        "reads_never_write": True,
        "calls_bat": False,
        "adapters": {"bat": {"discover": "stored_observation_only", "start_owned": "not_probed"}},
    }


def _bad(code: str, message: str, status: int = 422) -> OperationError:
    return OperationError(code, message, status)


def _json(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise _bad("INVALID_PARAMS", "Work profile document must be JSON") from None


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _reject_forbidden(value) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _FORBIDDEN:
                raise _bad("INVALID_PARAMS", "Work profile document contains a forbidden field")
            _reject_forbidden(child)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden(child)


def _binding_text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or not 1 <= len(value) <= 256:
        raise _bad("INVALID_PARAMS", f"{label} must be a non-blank string of 1-256 characters")
    return value


def _delivery(value):
    from .pm_work_start import DELIVERY_GOALS
    if value is None:
        return None
    goal = value.get("goal") if isinstance(value, dict) else None
    if (not isinstance(value, dict) or set(value) != {"goal"}
            or not isinstance(goal, str) or goal not in DELIVERY_GOALS):
        raise _bad("INVALID_PARAMS", "delivery_policy_ref must be null or a known delivery goal")
    return {"goal": goal}


def normalize_document(document) -> dict:
    """Return the canonical version-1 document. Omitted optional keys are null."""
    if not isinstance(document, dict):
        raise _bad("INVALID_PARAMS", "Work profile document must be an object")
    _reject_forbidden(document)
    if "budget_policy_ref" in document and document["budget_policy_ref"] is not None:
        raise _bad("PM_WORK_PROFILE_BUDGET_UNSUPPORTED", "budget_policy_ref must be null")
    if len(_json(document).encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise _bad("PM_WORK_PROFILE_TOO_LARGE", "Work profile document exceeds 16384 bytes", 413)
    if set(document) - _DOCUMENT_KEYS or not {"version", "binding", "agent"} <= set(document):
        raise _bad("INVALID_PARAMS", "Unexpected work profile document fields")
    if type(document.get("version")) is not int or document["version"] != 1:
        raise _bad("INVALID_PARAMS", "version must be 1")
    if "adapter" in document and document["adapter"] not in ("bat", "ahp"):
        raise _bad("INVALID_PARAMS", "adapter must be bat or ahp")
    binding = document.get("binding")
    if not isinstance(binding, dict) or set(binding) != {"repository", "host", "workspace_id"}:
        raise _bad("INVALID_PARAMS", "binding must be repository, host and workspace_id")
    agent = document.get("agent")
    if not isinstance(agent, str) or agent not in _AGENTS:
        raise _bad("INVALID_PARAMS", "agent must be claude or codex")
    if "model" in document and document["model"] is not None:
        model = document["model"]
        if not isinstance(model, str) or not model.strip() or not 1 <= len(model) <= 256:
            raise _bad("INVALID_PARAMS", "model must be a string of 1-256 characters or null")
    else:
        model = None
    if "delivery_policy_ref" in document:
        delivery = _delivery(document["delivery_policy_ref"])
    else:
        delivery = None
    normalized = {
        "version": 1,
        "binding": {
            "repository": _binding_text(binding["repository"], "repository"),
            "host": _binding_text(binding["host"], "host"),
            "workspace_id": _binding_text(binding["workspace_id"], "workspace_id"),
        },
        "agent": agent,
        "model": model,
        "delivery_policy_ref": delivery,
        "budget_policy_ref": None,
        **({"adapter": document["adapter"]} if "adapter" in document else {}),
    }
    if len(_json(normalized).encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise _bad("PM_WORK_PROFILE_TOO_LARGE", "Work profile document exceeds 16384 bytes", 413)
    return normalized


def _require_binding(ops, binding: dict) -> None:
    """Match repository_sync.capabilities only. Do not call configured()."""
    from .repository_sync import capabilities
    repository = binding["repository"].lower()
    found = any(
        isinstance(row.get("repository"), str) and row["repository"].lower() == repository
        and row.get("host") == binding["host"] and row.get("workspace_id") == binding["workspace_id"]
        for row in capabilities(ops)
    )
    if not found:
        raise _bad("REPOSITORY_NOT_BOUND", "no explicit repository/host/workspace binding", 403)


def _flag(params: dict, key: str):
    if key not in params:
        return _OMITTED
    value = params[key]
    if type(value) is not bool:
        raise _bad("INVALID_PARAMS", f"{key} must be a boolean")
    return value


def _latest(db, workspace_id: str, profile_id: str):
    return db.execute(
        """SELECT * FROM work_profiles WHERE workspace_id=? AND profile_id=?
           ORDER BY revision DESC LIMIT 1""",
        (workspace_id, profile_id),
    ).fetchone()


def _default_row(db, workspace_id: str):
    return db.execute(
        """SELECT sequence, profile_id FROM work_profile_defaults
           WHERE workspace_id=? ORDER BY sequence DESC LIMIT 1""",
        (workspace_id,),
    ).fetchone()


def _default_sequence(db, workspace_id: str):
    row = _default_row(db, workspace_id)
    return None if row is None else row["sequence"]


def _current_default_id(db, workspace_id: str):
    """Latest default row, or None when it is missing, null, or points at a withdrawn profile."""
    row = _default_row(db, workspace_id)
    if row is None or row["profile_id"] is None:
        return None
    latest = _latest(db, workspace_id, row["profile_id"])
    if latest is None or latest["withdrawn"]:
        return None
    return row["profile_id"]


def _default_write(params: dict, current_id, profile_id):
    """Return 'set', 'clear', or None. None does not read expected_default_sequence."""
    make_default = _flag(params, "make_default")
    withdrawn = _flag(params, "withdrawn")
    if withdrawn is _OMITTED:
        withdrawn = False
    if withdrawn is True and make_default is True:
        raise _bad("INVALID_PARAMS", "withdrawn and make_default cannot both be true")
    if make_default is True:
        return "set", withdrawn
    if profile_id is not None and current_id == profile_id and (make_default is False or withdrawn is True):
        return "clear", withdrawn
    return None, withdrawn


def _stated_default_sequence(params: dict):
    if "expected_default_sequence" not in params:
        return _OMITTED
    expected = params["expected_default_sequence"]
    if expected is not None and type(expected) is not int:
        raise _bad("INVALID_PARAMS", "expected_default_sequence must be an integer or null")
    return expected


def _expect_default_sequence(params: dict, actual, *, required: bool):
    expected = _stated_default_sequence(params)
    if expected is _OMITTED:
        if required:
            raise _bad("INVALID_PARAMS", "expected_default_sequence is required when changing the default")
        raise _bad("PM_WORK_PROFILE_DEFAULT_CHANGED", "Work profile default changed; read its current sequence", 409)
    if expected != actual:
        raise _bad("PM_WORK_PROFILE_DEFAULT_CHANGED", "Work profile default changed; read its current sequence", 409)


def _check_update(db, workspace_id: str, params: dict):
    profile_id = params.get("profile_id")
    if profile_id is None:
        if params.get("expected_revision") is not None:
            raise _bad("INVALID_PARAMS", "A new work profile has no expected_revision")
        if _flag(params, "withdrawn") is True:
            raise _bad("INVALID_PARAMS", "A new work profile cannot be withdrawn")
        return None, 1
    if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
        raise _bad("INVALID_PARAMS", "profile_id must be a wpf_ identifier")
    expected = params.get("expected_revision")
    if "expected_revision" not in params or type(expected) is not int or expected < 1:
        raise _bad("INVALID_PARAMS", "expected_revision must be the current revision")
    row = _latest(db, workspace_id, profile_id)
    if row is None:
        raise _bad("NOT_FOUND", "Work profile not found", 404)
    if row["withdrawn"]:
        raise _bad("PM_WORK_PROFILE_WITHDRAWN", "Work profile is withdrawn", 409)
    if row["revision"] != expected:
        raise _bad("PM_WORK_PROFILE_REVISION_CONFLICT", "Work profile revision changed; read its current revision", 409)
    return profile_id, row["revision"] + 1


def _admit(ops, principal: Principal, target: dict, params: dict, pre: dict) -> dict:
    if set(target) != {"workspace_id"}:
        raise _bad("INVALID_TARGET", "target must be exactly workspace_id")
    if set(pre) != set():
        raise _bad("INVALID_PARAMS", "Unexpected precondition fields")
    if not isinstance(params, dict) or set(params) - _PARAM_KEYS or not isinstance(params.get("document"), dict):
        raise _bad("INVALID_PARAMS", "Unexpected work profile params")
    workspace = require_member(ops.db, principal, target.get("workspace_id"), write=True)
    if not principal.allows("observe"):
        raise _bad("FORBIDDEN", "Saving a work profile requires the observe scope", 403)
    document = normalize_document(params["document"])
    from .pm_work_start import profile_route
    profile_route(ops, document)
    _require_binding(ops, document["binding"])
    profile_id, _revision = _check_update(ops.db, workspace["workspace_id"], params)
    kind, _withdrawn = _default_write(params, _current_default_id(ops.db, workspace["workspace_id"]), profile_id)
    if kind is not None:
        _expect_default_sequence(params, _default_sequence(ops.db, workspace["workspace_id"]), required=True)
    return {"principal": principal_binding(principal), "document": document}


def _receipt(db, operation_id: str):
    row = db.execute("SELECT * FROM work_profiles WHERE operation_id=?", (operation_id,)).fetchone()
    if row is None:
        return None
    result = {
        "profile_id": row["profile_id"],
        "workspace_id": row["workspace_id"],
        "revision": row["revision"],
        "digest": row["digest"],
        "withdrawn": bool(row["withdrawn"]),
    }
    default = db.execute(
        "SELECT sequence FROM work_profile_defaults WHERE operation_id=?", (operation_id,)
    ).fetchone()
    if default is not None:
        result["default_sequence"] = default["sequence"]
    return result


def _apply(ctx) -> dict:
    db = ctx.service.db
    params = ctx.params
    workspace_id = ctx.target["workspace_id"]
    document = (ctx.admission_binding or {}).get("document")
    if not isinstance(document, dict):
        raise _bad("AUTHORITY_UNAVAILABLE", "The operation has no admitted work profile", 409)
    from .pm_work_start import profile_route
    profile_route(ctx.service, document)
    profile_id, revision = _check_update(db, workspace_id, params)
    current_id = _current_default_id(db, workspace_id)
    kind, withdrawn = _default_write(params, current_id, profile_id)
    if kind is not None:
        _expect_default_sequence(params, _default_sequence(db, workspace_id), required=False)
    if profile_id is None:
        profile_id = "wpf_" + uuid.uuid4().hex
    text = _json(document)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    now = time.time()
    db.execute(
        """INSERT INTO work_profiles(
            profile_id, workspace_id, revision, withdrawn, document, digest, operation_id, created_at
        ) VALUES(?,?,?,?,?,?,?,?)""",
        (profile_id, workspace_id, revision, 1 if withdrawn is True else 0, text, digest, ctx.operation_id, now),
    )
    result = {
        "profile_id": profile_id,
        "workspace_id": workspace_id,
        "revision": revision,
        "digest": digest,
        "withdrawn": withdrawn is True,
    }
    if kind is not None:
        db.execute(
            """INSERT INTO work_profile_defaults(workspace_id, profile_id, operation_id, created_at)
               VALUES(?,?,?,?)""",
            (workspace_id, profile_id if kind == "set" else None, ctx.operation_id, now),
        )
        result["default_sequence"] = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return result


async def _run(ctx):
    with ctx.service.journal.tx():
        existing = _receipt(ctx.service.db, ctx.operation_id)
        if existing is not None:
            return existing
        principal = admitted_principal(ctx)
        if not principal.allows("manage") or not principal.allows("observe"):
            raise _bad("FORBIDDEN", "Saving a work profile requires observe and manage scopes", 403)
        require_member(ctx.service.db, principal, ctx.target["workspace_id"], write=True)
        return _apply(ctx)


def _live_rows(db, workspace_id: str):
    return db.execute(
        """SELECT wp.* FROM work_profiles wp
           JOIN (
               SELECT profile_id, MAX(revision) AS revision
               FROM work_profiles WHERE workspace_id=? GROUP BY profile_id
           ) latest ON latest.profile_id=wp.profile_id AND latest.revision=wp.revision
           WHERE wp.workspace_id=? AND wp.withdrawn=0
           ORDER BY wp.sequence ASC""",
        (workspace_id, workspace_id),
    ).fetchall()


def _project(db, row, default_id) -> dict:
    document = json.loads(row["document"])
    binding = document.get("binding") if isinstance(document, dict) else {}
    if not isinstance(binding, dict):
        binding = {}
    delivery = document.get("delivery_policy_ref") if isinstance(document, dict) else None
    actor = db.execute("SELECT actor FROM operations WHERE operation_id=?", (row["operation_id"],)).fetchone()
    return {
        "profile_id": row["profile_id"],
        "revision": row["revision"],
        "digest": row["digest"],
        "is_default": default_id is not None and row["profile_id"] == default_id and not row["withdrawn"],
        "agent": document.get("agent") if isinstance(document, dict) else None,
        "model": document.get("model") if isinstance(document, dict) else None,
        "delivery_goal": delivery.get("goal") if isinstance(delivery, dict) else None,
        "repository": binding.get("repository"),
        "host": binding.get("host"),
        "workspace_id": binding.get("workspace_id"),
        "created_at": row["created_at"],
        "operation_id": row["operation_id"],
        "actor": actor["actor"] if actor else None,
    }


def _require_live(db, principal: Principal, workspace_id: str, profile_id: str):
    require_member(db, principal, workspace_id)
    if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
        raise _bad("NOT_FOUND", "Work profile not found", 404)
    row = _latest(db, workspace_id, profile_id)
    if row is None:
        raise _bad("NOT_FOUND", "Work profile not found", 404)
    if row["withdrawn"]:
        raise _bad("PM_WORK_PROFILE_WITHDRAWN", "Work profile is withdrawn", 404)
    return row


def list_profiles(db, principal: Principal, workspace_id: str, query: dict | None = None) -> dict:
    query = {} if query is None else query
    if not isinstance(query, dict) or set(query) - {"limit", "cursor"}:
        raise _bad("INVALID_PARAMS", "Unexpected query fields")
    if "limit" in query:
        limit = query["limit"]
        if type(limit) is not int or not 1 <= limit <= 100:
            raise _bad("INVALID_PARAMS", "limit must be an integer from 1 to 100")
    else:
        limit = 50
    if "cursor" in query:
        cursor = query["cursor"]
        if type(cursor) is not int or cursor < 0:
            raise _bad("INVALID_PARAMS", "cursor must be a work profile sequence")
    else:
        cursor = 0
    # One read point. The barrier's own scopes close before and after this handler.
    with pm_source_reads.scope(db, principal):
        require_member(db, principal, workspace_id)
        default_id = _current_default_id(db, workspace_id)
        rows = list(_live_rows(db, workspace_id))
        visible = [row for row in rows if row["sequence"] > cursor]
        page = visible[:limit]
        return {
            "profiles": [_project(db, row, default_id) for row in page],
            "count": len(rows),
            "default_profile_id": default_id,
            "next_cursor": page[-1]["sequence"] if len(visible) > limit else None,
        }


def get_profile(db, principal: Principal, workspace_id: str, profile_id: str) -> dict:
    row = _require_live(db, principal, workspace_id, profile_id)
    return _project(db, row, _current_default_id(db, workspace_id))


def _changed(revision: int) -> OperationError:
    return _bad("PM_WORK_PROFILE_CHANGED", f"Work profile changed; current revision is {revision}", 409)


def pinned_revision(db, principal, workspace_id, profile_id, revision, *, digest=None, admission=False):
    """Load one revision only when it is still the latest live row.

    Order is membership, id shape, unknown, withdrawn, then revision and digest. A withdrawn
    latest row is PM_WORK_PROFILE_WITHDRAWN even for an older pin. Preview uses 404 and
    admission uses 409. This read does not write.
    """
    require_member(db, principal, workspace_id)
    if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
        raise _bad("INVALID_PARAMS", "profile_id must be a wpf_ identifier")
    if type(revision) is not int or revision < 1:
        raise _bad("INVALID_PARAMS", "profile_revision must be the current revision")
    if not _table(db, "work_profiles"):
        raise _bad("NOT_FOUND", "Work profile not found", 404)
    latest = _latest(db, workspace_id, profile_id)
    if latest is None:
        raise _bad("NOT_FOUND", "Work profile not found", 404)
    if latest["withdrawn"]:
        raise _bad("PM_WORK_PROFILE_WITHDRAWN", "Work profile is withdrawn", 409 if admission else 404)
    if latest["revision"] != revision or (digest is not None and digest != latest["digest"]):
        raise _changed(int(latest["revision"]))
    document = json.loads(latest["document"])
    delivery = document.get("delivery_policy_ref") if isinstance(document, dict) else None
    binding = document.get("binding") if isinstance(document, dict) else None
    return {
        "profile_id": latest["profile_id"],
        "revision": int(latest["revision"]),
        "digest": latest["digest"],
        "document": document,
        "binding": binding if isinstance(binding, dict) else {},
        "agent": document.get("agent") if isinstance(document, dict) else None,
        "model": document.get("model") if isinstance(document, dict) else None,
        "delivery_goal": delivery.get("goal") if isinstance(delivery, dict) else None,
    }


def readiness(ops, principal: Principal, workspace_id: str, profile_id: str) -> dict:
    """Member and withdrawal gate, then a local readiness document. No BAT call and no write."""
    row = _require_live(ops.db, principal, workspace_id, profile_id)
    return _readiness_body(ops, principal, workspace_id, row)


def _work_follow_up_readiness(ops, agent: str) -> dict:
    # Use the Work route's registered capability, not generic session continuation
    # evidence. This observation never admits a send or probes a BAT session.
    from .pm_work_follow_up import capabilities

    route = capabilities(ops)
    reason = None
    if not route["available"]:
        reason = "action_unavailable"
    elif agent == "codex" and not route["codex_supported"]:
        reason = "codex_uncorrelated_turn_boundary"
    return {
        "route": "bat_work_start",
        "agent": agent,
        "state": "unavailable" if reason else "requires_attempt",
        "reason": reason or "owned_attempt_required",
        "admission_checked": False,
    }


def _table(db, name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _object(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def _epoch(value):
    if type(value) in (int, float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))
        except (TypeError, ValueError):
            return None
    return None


def _state(value, reason, evidence=None, verified=None) -> dict:
    return {"value": value, "reason": reason, "evidence_ref": evidence, "verified": verified}


def _verified(value, reason, evidence, observed_at) -> dict:
    return {"value": value, "reason": reason, "evidence_ref": evidence, "verified": None, "observed_at": observed_at}


def _gap(reason, fix_action=None, last_success_at=_ABSENT) -> dict:
    fix = None if fix_action is None else {
        "action": fix_action, "route": _FIX_ROUTE, "ui_hash": _FIX_HASH,
    }
    gap = {"reason": reason, "fix": fix, "fallback": None}
    if last_success_at is not _ABSENT:
        gap["last_success_at"] = last_success_at
    return gap


def _host_config(ops, host):
    fleet = ops.context.get("fleet")
    hosts = getattr(getattr(fleet, "config", None), "hosts", None)
    if isinstance(hosts, dict) and host in hosts:
        return hosts[host]
    return None


def _token_available(host_cfg) -> bool:
    method = getattr(host_cfg, "token_available", None)
    if method is None:
        return False
    try:
        return bool(method())
    except TokenUnavailable:
        return False


def _orchestrate_enabled(ops, host, host_cfg) -> bool:
    fleet = ops.context.get("fleet")
    method = getattr(fleet, "orchestrate_enabled", None)
    if host_cfg is None or method is None:
        return False
    return bool(method(host))


def _git_status(ops, host) -> dict:
    runner = ops.context.get("git_runner")
    status = runner.status(host) if runner is not None and hasattr(runner, "status") else None
    if not isinstance(status, dict) or "reason" not in status:
        return {"available": False, "reason": "host_unknown", "details": {}}
    return status


def _stale_after_s(ops) -> float:
    inventory = ops.context.get("inventory")
    settings = getattr(inventory, "settings", None)
    if settings is not None and getattr(settings, "stale_after_s", None) is not None:
        return float(settings.stale_after_s)
    api = getattr(getattr(getattr(ops.context.get("fleet"), "config", None), "api", None), "stale_after_s", None)
    if api is not None:
        return float(api)
    return 180.0


def _covers(body, bat_workspace: str) -> bool:
    coverage = body.get("coverage")
    ids = coverage.get("workspace_ids") if isinstance(coverage, dict) else None
    return body.get("status") == "succeeded" and isinstance(ids, list) and bat_workspace in ids


def _usage_provider(db, host, agent):
    if not _table(db, "product_host_observations"):
        return None
    row = db.execute(
        "SELECT * FROM product_host_observations WHERE host=? AND kind=? AND scope=?",
        (host, "usage", ""),
    ).fetchone()
    document = _object(row["document"]) if row is not None else {}
    providers = document.get("providers")
    if not isinstance(providers, list):
        return None
    for item in providers:
        if isinstance(item, dict) and item.get("provider") == agent:
            return item
    return None


def _binding_known(ops, repository: str, host: str, bat_workspace: str) -> bool:
    from . import repository_sync
    for item in repository_sync.capabilities(ops):
        repo = item.get("repository")
        if (isinstance(repo, str) and isinstance(repository, str) and repo.lower() == repository.lower()
                and item.get("host") == host and item.get("workspace_id") == bat_workspace):
            return True
    return False


def _continue_matches(row, repository, agent) -> bool:
    target = _object(row["target"])
    params = _object(row["params"])
    repo = target.get("repository")
    if not isinstance(repo, str) or not isinstance(repository, str) or repo.lower() != repository.lower():
        return False
    if "agent" in params:
        return params.get("agent") == agent
    return agent == "claude"


def _matching_continue(db, principal, host, bat_workspace, repository, agent):
    """Newest visible succeeded continue. An invisible receipt is not a match and is not counted."""
    if not isinstance(host, str) or not isinstance(bat_workspace, str):
        return None
    from .pm_source_lineage import visible
    # The cursor stays open only inside this scope, so visible() joins it instead of beginning
    # another transaction. The barrier's scopes are already closed around this handler.
    with pm_source_reads.scope(db, principal):
        cursor = db.execute(_CONTINUE_SQL, (host, bat_workspace))
        found = None
        try:
            row = cursor.fetchone()
            while row is not None:
                if _continue_matches(row, repository, agent) and visible(
                    db, principal, "operation", row["operation_id"],
                ):
                    found = {key: row[key] for key in row.keys()}
                    break
                row = cursor.fetchone()
        finally:
            cursor.close()
        return found


def _session_of(row) -> tuple[str, str] | None:
    """Same host and session_id rule as pm_work_start._child_session."""
    result = _object(row["result"])
    refs = _object(row["external_refs"])
    target = _object(row["target"])
    host = result.get("host") or refs.get("host") or target.get("host")
    session_id = result.get("session_id") or refs.get("session_id")
    if isinstance(host, str) and host and isinstance(session_id, str) and session_id:
        return host, session_id
    return None


def _observed_session(db, host: str, session_id: str):
    if not _table(db, "sessions_observed"):
        return None
    return db.execute(
        "SELECT provenance, api_access, gone_at, body FROM sessions_observed WHERE host=? AND session_id=?",
        (host, session_id),
    ).fetchone()


def _execution(db, principal, row, agent, bat_workspace):
    """The one latest matching repository.continue, its handoff parent, and its observed session."""
    if row is None:
        return None
    parent = None
    if _table(db, "pm_work_start_children"):
        child = db.execute(
            "SELECT parent_id, slot FROM pm_work_start_children WHERE operation_id=? AND slot='handoff'",
            (row["operation_id"],),
        ).fetchone()
        parent = child["parent_id"] if child is not None else None
    evidence = {"kind": "operation", "operation_id": row["operation_id"]}
    if parent is not None:
        from .pm_source_lineage import visible
        if visible(db, principal, "operation", parent):
            evidence["work_start_operation_id"] = parent
    located = _session_of(row)
    observed = _observed_session(db, located[0], located[1]) if located is not None else None
    body = _object(observed["body"]) if observed is not None else {}
    owned = (observed is not None and observed["provenance"] == "connector_managed"
             and observed["api_access"] == "managed" and body.get("agent_kind") == agent
             and body.get("workspace_id") == bat_workspace)
    return {
        "evidence": evidence,
        "owned": owned,
        "gone_at": observed["gone_at"] if observed is not None else None,
        "observed_at": row["created_at"],
    }


def _pm_analysis(ops, principal, workspace_id: str) -> dict:
    from . import pm_bat_operations, pm_native_capabilities, pm_workspace_adapter
    workspace = require_member(ops.db, principal, workspace_id)
    native = pm_native_capabilities.read(ops, principal, workspace)
    bat = pm_bat_operations.capabilities(ops, principal, workspace)
    choice = pm_workspace_adapter.read(ops, principal, workspace_id) if pm_workspace_adapter.ACTION in ops.actions else None
    effective = pm_workspace_adapter.effective_route(choice, native, bat,
        [scope for scope in pm_workspace_adapter.SCOPES if principal.allows(scope)])
    return {"pm_ready": effective["ready"], "native_analysis": native, "bat_analysis": bat, "effective_pm_route": effective}


def _readiness_body(ops, principal: Principal, workspace_id: str, row) -> dict:
    observed_at = time.time()
    document = _object(row["document"])
    if document.get("adapter", "bat") != "bat":
        return _adapter_readiness(ops, principal, workspace_id, row, document, observed_at)
    binding = document.get("binding") if isinstance(document.get("binding"), dict) else {}
    repository = binding.get("repository")
    host = binding.get("host")
    bat_workspace = binding.get("workspace_id")
    agent = document.get("agent")
    delivery = document.get("delivery_policy_ref")
    goal = delivery.get("goal") if isinstance(delivery, dict) else None
    host_cfg = _host_config(ops, host)
    token = _token_available(host_cfg)
    git = _git_status(ops, host)
    git_reason = git.get("reason")
    roots = bool(getattr(host_cfg, "managed_roots", None))
    orchestrate = _orchestrate_enabled(ops, host, host_cfg)
    continue_registered = "repository.continue" in ops.actions
    send_registered = "session.send" in ops.actions
    bound = _binding_known(ops, repository, host, bat_workspace)
    inventory = ops.context.get("inventory")
    source = inventory.current_source_evidence(host) if inventory is not None else None
    host_row = source.observation if source is not None else None
    source_reason = source.reason if source is not None else None
    stale_after = _stale_after_s(ops)
    observed_reason = _observation_reason(host_row, observed_at, stale_after)
    last_success = host_row["last_success_at"] if host_row is not None else None
    last_attempt = host_row["last_attempt_at"] if host_row is not None else None
    bodies = source.discovery_bodies if source is not None else []
    covering = [body for body in bodies if _covers(body, bat_workspace)]
    provider = _usage_provider(ops.db, host, agent)
    receipt = _matching_continue(ops.db, principal, host, bat_workspace, repository, agent)
    execution = _execution(ops.db, principal, receipt, agent, bat_workspace)

    if not bound:
        discovered = _state("missing", "REPOSITORY_NOT_BOUND")
    elif git_reason == "host_unknown":
        discovered = _state("unavailable", "host_unknown")
    elif observed_reason is None and covering:
        found = covering[0]
        discovered = _state("ready", None, {
            "kind": "discovery_latest", "host": host, "scan_id": found.get("scan_id"), "status": found.get("status"),
        })
    else:
        coded = next((body.get("error_code") for body in bodies if body.get("error_code") in _DISCOVERY_REASONS), None)
        discovered = _state("unknown", source_reason or coded or "configured_unverified")
    if covering:
        past = covering[0]
        discovered["verified"] = _verified("ready", None, {
            "kind": "discovery_latest", "host": host, "scan_id": past.get("scan_id"),
            "status": past.get("status"), "finished_at": past.get("finished_at"),
        }, _epoch(past.get("finished_at")))

    if not token:
        signed_in = _state("missing", "CREDENTIAL_REQUIRED")
    elif isinstance(provider, dict) and provider.get("reason") == "unauthorized":
        signed_in = _state("unavailable", "unauthorized")
    else:
        signed_in = _state("unknown", "auth_validity_unverified", {"auth_validity": "unverified"})

    if not continue_registered:
        can_observe = _state("unavailable", "action_unavailable")
    elif git_reason in LOCAL_GIT_REASONS | {"git_execution_location_changed", "host_unknown"}:
        can_observe = _state("unavailable", git_reason)
    elif git_reason == "ssh_alias_missing":
        can_observe = _state("missing", "ssh_alias_missing")
    elif not orchestrate or not roots:
        can_observe = _state("unavailable", "REPOSITORY_HOST_UNAVAILABLE")
    elif source_reason:
        can_observe = _state("unknown", source_reason)
    elif observed_reason == "never_observed":
        can_observe = _state("unknown", "never_observed")
    elif observed_reason == "host_unreachable":
        can_observe = _state("unavailable", "host_unreachable")
    elif observed_reason == "host_not_refreshed":
        can_observe = _state("unavailable", "host_not_refreshed")
    else:
        can_observe = _state("ready", None, {"kind": "hosts_observed", "last_success_at": last_success})
    if last_success is not None:
        can_observe["verified"] = _verified(
            "ready", None, {"kind": "hosts_observed", "last_success_at": last_success}, last_success,
        )

    blockers = []
    if not bound:
        blockers.append(("missing", "REPOSITORY_NOT_BOUND"))
    if git_reason == "host_unknown":
        blockers.append(("unavailable", "host_unknown"))
    if git_reason in LOCAL_GIT_REASONS:
        blockers.append(("unavailable", git_reason))
    if git_reason == "ssh_alias_missing":
        blockers.append(("missing", "ssh_alias_missing"))
    if git_reason == "git_execution_location_changed":
        blockers.append(("unavailable", "git_execution_location_changed"))
    if observed_reason == "host_unreachable":
        blockers.append(("unavailable", "host_unreachable"))
    if observed_reason == "host_not_refreshed":
        blockers.append(("unavailable", "host_not_refreshed"))
    if not continue_registered:
        blockers.append(("unavailable", "action_unavailable"))
    if not orchestrate or not roots:
        blockers.append(("unavailable", "REPOSITORY_HOST_UNAVAILABLE"))
    if blockers:
        can_start = _state(blockers[0][0], blockers[0][1])
    else:
        can_start = _state("unknown", "configured_unverified")

    if not send_registered:
        can_continue = _state("unavailable", "action_unavailable")
    else:
        can_continue = _state("unknown", "no_owned_session_verified")
    if (execution is not None and execution["owned"] and execution["gone_at"] is None and send_registered):
        can_continue["verified"] = _verified("ready", None, execution["evidence"], execution["observed_at"])

    if execution is None or not execution["owned"]:
        last_execution = _state("unknown", "execution_not_verified")
    else:
        last_execution = _state("ready", None, execution["evidence"])
        last_execution["verified"] = _verified("ready", None, execution["evidence"], execution["observed_at"])

    states = {
        "discovered": discovered,
        "signed_in_or_available": signed_in,
        "can_observe": can_observe,
        "can_start_own": can_start,
        "can_continue_own": can_continue,
        "last_verified_execution": last_execution,
    }
    # Only host-observation-derived values declare validity. Configuration,
    # authentication and execution history have no time-based readiness claim.
    observation_states = [state for state in (can_observe, can_start)
        if state["reason"] in {"never_observed", "host_unreachable", "host_not_refreshed"}]
    if can_observe["value"] == "ready":
        observation_states.append(can_observe)
    if discovered["value"] == "ready":
        observation_states.append(discovered)
    for state in observation_states:
        state.update(observed_at=last_attempt, valid_until=_valid_until(last_success, stale_after))

    if not bound:
        smallest = _gap("REPOSITORY_NOT_BOUND", "setup.repository")
    elif host_cfg is None:
        smallest = _gap("host_unknown", "setup.host")
    elif git_reason in LOCAL_GIT_REASONS:
        smallest = _gap(git_reason)
    elif git_reason == "ssh_alias_missing" or not token or not orchestrate or not roots:
        if git_reason == "ssh_alias_missing":
            gap_reason = "ssh_alias_missing"
        elif not token:
            gap_reason = "CREDENTIAL_REQUIRED"
        else:
            gap_reason = "REPOSITORY_HOST_UNAVAILABLE"
        smallest = _gap(gap_reason, "setup.host")
    elif git_reason == "git_execution_location_changed":
        smallest = _gap("git_execution_location_changed")
    elif observed_reason in {"host_unreachable", "host_not_refreshed"}:
        smallest = _gap(observed_reason, last_success_at=last_success)
    elif not continue_registered or not send_registered:
        smallest = _gap("action_unavailable")
    else:
        smallest = None
    return {
        "contract": _CONTRACT,
        "workspace_id": workspace_id,
        "profile_id": row["profile_id"],
        "revision": row["revision"],
        "digest": row["digest"],
        "observed_at": observed_at,
        "calls_bat": False,
        "coding_route_ready": states["can_start_own"]["value"] == "ready",
        "work_follow_up": _work_follow_up_readiness(ops, agent),
        "configured": {
            "agent": agent,
            "model": document.get("model"),
            "delivery_goal": goal,
            "binding": {"repository": repository, "host": host, "workspace_id": bat_workspace},
        },
        "states": states,
        "smallest_gap": smallest,
        "unverified": [name for name in _STATE_NAMES if states[name]["value"] == "unknown"],
        "resume": {"kind": "client_draft", "stored_on_server": False},
        "pm_analysis": _pm_analysis(ops, principal, workspace_id),
    }


def _adapter_readiness(ops, principal, workspace_id, row, document, observed_at):
    from .pm_work_start import route_readiness
    availability = route_readiness(ops, document)
    reason = availability['reason']
    states = {name: _state('unknown', 'not_verified') for name in _STATE_NAMES}
    states['can_start_own'] = _state('ready' if availability['available'] else 'unavailable', reason)
    states['can_continue_own'] = _state('unavailable', 'owned_turn_unavailable')
    return {'contract': _CONTRACT, 'workspace_id': workspace_id, 'profile_id': row['profile_id'],
        'revision': row['revision'], 'digest': row['digest'], 'observed_at': observed_at, 'calls_bat': False,
        'coding_route_ready': availability['available'], 'states': states,
        'configured': {**{key: document.get(key) for key in ('adapter', 'agent', 'model', 'binding')},
                       'delivery_goal': (document.get('delivery_policy_ref') or {}).get('goal')},
        'work_follow_up': {'route': document['adapter'] + '_work_start', 'agent': document['agent'],
                          'state': 'unavailable', 'reason': 'owned_turn_unavailable', 'admission_checked': False},
        'smallest_gap': _gap(reason) if reason else None,
        'unverified': [name for name in _STATE_NAMES if states[name]['value'] == 'unknown'],
        'resume': {'kind': 'client_draft', 'stored_on_server': False},
        'pm_analysis': _pm_analysis(ops, principal, workspace_id)}
