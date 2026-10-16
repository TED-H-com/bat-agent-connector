"""PM work profiles (v1.3 E01): append-only revisions, defaults and readiness."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from .api_auth import Principal
from .operations import ActionDef, OperationError
from .pm_workspace import admitted_principal, principal_binding, require_member

ACTION = "pm.work_profile.save"
MAX_DOCUMENT_BYTES = 16384
_PROFILE_ID = re.compile(r"wpf_[0-9a-f]{32}")
_DOCUMENT_KEYS = frozenset({
    "version", "binding", "agent", "model", "delivery_policy_ref", "budget_policy_ref",
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
    if not isinstance(value, dict) or set(value) != {"goal"} or value.get("goal") not in DELIVERY_GOALS:
        raise _bad("INVALID_PARAMS", "delivery_policy_ref must be null or a known delivery goal")
    return {"goal": value["goal"]}


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
    binding = document.get("binding")
    if not isinstance(binding, dict) or set(binding) != {"repository", "host", "workspace_id"}:
        raise _bad("INVALID_PARAMS", "binding must be repository, host and workspace_id")
    agent = document.get("agent")
    if agent not in _AGENTS:
        raise _bad("INVALID_PARAMS", "agent must be claude or codex")
    if "model" in document and document["model"] is not None:
        model = document["model"]
        if not isinstance(model, str) or not 1 <= len(model) <= 256:
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
    if row["revision"] != expected:
        raise _bad("PM_WORK_PROFILE_REVISION_CONFLICT", "Work profile revision changed; read its current revision", 409)
    if row["withdrawn"]:
        raise _bad("PM_WORK_PROFILE_WITHDRAWN", "Work profile is withdrawn", 409)
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
           WHERE wp.workspace_id=? AND wp.withdrawn=0""",
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
    require_member(db, principal, workspace_id)
    rows = list(_live_rows(db, workspace_id))
    default_id = _current_default_id(db, workspace_id)
    default = next((row for row in rows if row["profile_id"] == default_id), None)
    ordered = []
    if default is not None and default["sequence"] > cursor:
        ordered.append(default)
    rest = [
        row for row in rows
        if row["sequence"] > cursor and (default is None or row["profile_id"] != default["profile_id"])
    ]
    rest.sort(key=lambda row: (row["created_at"], row["profile_id"]))
    ordered.extend(rest)
    page = ordered[:limit]
    return {
        "profiles": [_project(db, row, default_id) for row in page],
        "count": len(rows),
        "next_cursor": page[-1]["sequence"] if len(ordered) > limit else None,
    }


def get_profile(db, principal: Principal, workspace_id: str, profile_id: str) -> dict:
    row = _require_live(db, principal, workspace_id, profile_id)
    return _project(db, row, _current_default_id(db, workspace_id))


def readiness(ops, principal: Principal, workspace_id: str, profile_id: str) -> dict:
    """Member and withdrawal gate. The success body is the readiness reader."""
    _require_live(ops.db, principal, workspace_id, profile_id)
    return _readiness_body(ops, principal, workspace_id, profile_id)


def _readiness_body(ops, principal: Principal, workspace_id: str, profile_id: str) -> dict:
    raise NotImplementedError(profile_id)
