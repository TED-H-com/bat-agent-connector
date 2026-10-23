"""Direct work start compound action (v1.3 E02, slice P3): work.start and start preview."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from contextvars import ContextVar

from . import (
    api_auth,
    checkpoints,
    controller_authority,
    pm_evidence,
    pm_lifecycle_gates,
    pm_package_handoff,
    pm_plans,
    pm_source_lineage,
    pm_work_packages,
    pm_workspace,
    pm_worktree,
    repository_sync,
    service,
    work_items,
)
from . import pm_release_baselines as snapshots
from . import pm_work_package_format as fmt
from .api_auth import Principal
from .operations import TERMINAL as OP_TERMINAL
from .operations import ActionDef, NeedsAttention, OperationError, OperationService, Wait

ACTION = "work.start"
PREVIEW = "pm_work_start_preview"
ATTEMPTS_LIST = "pm_work_attempts_list"
ATTEMPT_GET = "pm_work_attempt_get"
ATTEMPT_ID = re.compile(r"att_[0-9a-f]{32}")
COMPOSITION_PROFILE = "work_start_thin_v1"
REQUIRED_SCOPES = frozenset({"observe", "manage", "start"})
SLOTS = ("bind", "package", "handoff")
SLOT_ORDINAL = {"bind": 0, "package": 1, "handoff": 2}
LINK = "work_start_parent"
_HANDOFF_ADMISSION = ContextVar("work_handoff_admission", default=None)
CHILD_STOPPED = frozenset({"succeeded", "failed", "cancelled", "needs_attention"})
PRECONDITIONS = frozenset({
    "expected_work_item_fingerprint",
    "expected_work_version",
    "expected_binding_id",
    "expected_scope_revision",
    "expected_previous_package_id",
    "expected_project_version",
    "repository_id",
    "binding_digest",
    "expected_source_sha",
    "expected_latest_attempt_id",
})
PARAMS_REQUIRED = frozenset({"instruction", "agent", "delivery", "code_target"})
PARAMS_OPTIONAL = frozenset({"model", "requirement_ids", "profile_id"})
CODE_TARGET_KEYS = ("repository", "host", "workspace_id", "source_ref")
DELIVERY_GOALS = frozenset({"pull_request", "reviewable_changes"})
AGENTS = frozenset({"claude", "codex"})
DESIGN_TEXT = (
    "The change is limited to the adopted requirements; "
    "no separate design was authored for this small change."
)
VERIFICATION_METHOD = (
    "Verify the resulting changes against the adopted acceptance statement before delivery."
)


def _bad(code: str, message: str, status: int = 409) -> OperationError:
    return OperationError(code, message, status)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def register_handoff_route(ops, route, builder, readiness):
    """Install a deterministic builder and a service-local availability check."""
    if route in _HANDOFF_ROUTES and _HANDOFF_ROUTES[route] is not builder:
        raise _bad("PM_WORK_ROUTE_CONFLICT", "Work route already has a different builder")
    _HANDOFF_ROUTES[route] = builder
    ops.context.setdefault("work_start_routes", {})[route] = readiness


def route_readiness(ops, document):
    route = document.get("adapter", "bat") + "_work_start"
    check = ops.context.get("work_start_routes", {}).get(route)
    if route not in _HANDOFF_ROUTES or check is None:
        return {"available": False, "reason": "PM_WORK_ROUTE_UNAVAILABLE"}
    return check(ops, document)


def profile_route(ops, document, *, require_ready=False):
    route = document.get("adapter", "bat") + "_work_start"
    if route != "bat_work_start":
        if route not in ops.context.get("work_start_routes", {}) or route not in _HANDOFF_ROUTES:
            raise _bad("PM_WORK_ROUTE_UNAVAILABLE", "The selected Work adapter is not installed")
        if require_ready:
            state = route_readiness(ops, document)
            if state["available"] is not True:
                raise _bad(state["reason"], "The selected Work adapter cannot create an owned session")
    return route


def handoff_parent(ops, target, params, preconditions):
    """Only the synchronous handoff slot holds this admission capability."""
    active = _HANDOFF_ADMISSION.get()
    if active is None or active[0] is not ops:
        return None
    _, parent, request = active
    if (target, params, preconditions) != (request["target"], request["params"], request["preconditions"]):
        raise _bad("PM_WORK_START_CHILD_CHANGED", "Handoff admission differs from its original request")
    return parent


def installed(db) -> bool:
    return pm_source_lineage._table(db, "work_attempts")


def schema(journal) -> None:
    """Idempotent additive DDL; does not allocate user_version or a data step."""
    with journal.tx():
        for ddl in (
            """CREATE TABLE IF NOT EXISTS pm_work_start_children (
                parent_id TEXT NOT NULL,
                slot TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                operation_id TEXT NOT NULL UNIQUE,
                request TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(parent_id, slot),
                UNIQUE(parent_id, ordinal))""",
            """CREATE TABLE IF NOT EXISTS work_attempts (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL,
                work_item_id TEXT NOT NULL,
                origin_kind TEXT NOT NULL,
                origin_id TEXT NOT NULL,
                start_operation_id TEXT UNIQUE,
                predecessor_attempt_id TEXT,
                admission_digest TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(origin_kind, origin_id))""",
            """CREATE INDEX IF NOT EXISTS work_attempts_by_work
                ON work_attempts(workspace_id, work_item_id, sequence DESC)""",
        ):
            journal.db.execute(ddl)
        for table, label in (
            ("pm_work_start_children", "Work start child is immutable"),
            ("work_attempts", "Work attempt history is immutable"),
        ):
            for verb in ("UPDATE", "DELETE"):
                journal.db.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable "
                    f"BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT, '{label}'); END"
                )
        journal.db.execute("DROP TRIGGER IF EXISTS pm_work_start_child_settled")
        journal.db.execute("""CREATE TRIGGER IF NOT EXISTS pm_work_start_child_settled
            AFTER UPDATE OF status ON operations
            WHEN NEW.status != OLD.status AND NEW.status IN ('succeeded','failed','cancelled','needs_attention','uncertain')
            BEGIN
                UPDATE operations SET next_run_at=0
                WHERE action='work.start' AND status='waiting_external'
                    AND operation_id=(SELECT parent_id FROM pm_work_start_children
                        WHERE operation_id=NEW.operation_id);
            END""")


def install(ops) -> None:
    schema(ops.journal)
    pm_evidence.install(ops)
    pm_work_packages.install(ops)
    repository_sync.install(ops)
    if ACTION not in ops.actions:
        ops.register(
            ActionDef(
                ACTION,
                "start",
                "Start one bound Work item in its own managed BAT worktree and session",
                _run,
                _admit,
                ("workspace_id", "work_item_id"),
                authorize_existing=_authorize_existing,
            )
        )


def _member(ops, principal: Principal, wid: str, *, write: bool = False, decide: bool = False) -> dict:
    scopes = REQUIRED_SCOPES if write else {"observe"}
    if not all(principal.allows(scope) for scope in scopes):
        raise _bad("FORBIDDEN", "Starting work requires observe, manage and start scopes", 403)
    return pm_workspace.require_member(ops.db, principal, wid, write=write, decide=decide)


def _op_controller(db, operation_id: str) -> dict | None:
    row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
    if row is None:
        return None
    op = OperationService._decode(row)
    saved = ((op.get("external_refs") or {}).get("admission_binding") or {})
    intent = saved.get("work_start")
    if isinstance(intent, dict) and isinstance(intent.get("controller"), dict):
        return intent["controller"]
    return controller_authority.operation_binding(op)


def work_controller(db, workspace_id: str, work_item_id: str) -> dict | None:
    """Return the controller anchored by the Work's earliest attempt, or None if never started."""
    if not installed(db):
        return None
    row = db.execute(
        """SELECT start_operation_id, origin_id FROM work_attempts
        WHERE workspace_id=? AND work_item_id=? ORDER BY sequence ASC LIMIT 1""",
        (workspace_id, work_item_id),
    ).fetchone()
    if row is None:
        return None
    op_id = row["start_operation_id"] or row["origin_id"]
    controller = _op_controller(db, op_id)
    if controller is None:
        raise _bad("PM_WORK_CONTROLLER_CONFLICT", "Original work attempt controller authority cannot be verified", 409)
    return controller


def require_work_controller(db, workspace_id: str, work_item_id: str, candidate: dict) -> None:
    """Reject a different controller from taking over a Work that already has an attempt anchor."""
    anchored = work_controller(db, workspace_id, work_item_id)
    if anchored is not None and not controller_authority.same_controller(candidate, anchored):
        raise _bad(
            "PM_WORK_CONTROLLER_CONFLICT",
            "This work item is already anchored to another controller",
            409,
        )


def _uncertain_db(db, operation_id: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM operation_steps s LEFT JOIN pm_lifecycle_steps l "
            "ON l.operation_id=s.operation_id AND l.name=s.name WHERE s.operation_id=? "
            "AND s.status IN ('started','uncertain') AND (l.kind IS NULL OR l.kind='external') LIMIT 1",
            (operation_id,),
        ).fetchone()
        is not None
    )


def require_latest_attempt_settled(db, workspace_id: str, work_item_id: str) -> None:
    """Verify that every part of the Work's latest attempt has settled and no external step is unproven."""
    if not installed(db):
        return
    row = db.execute(
        """SELECT start_operation_id, origin_id FROM work_attempts
        WHERE workspace_id=? AND work_item_id=? ORDER BY sequence DESC LIMIT 1""",
        (workspace_id, work_item_id),
    ).fetchone()
    if row is None:
        return
    op_id = row["start_operation_id"] or row["origin_id"]
    parent_row = db.execute("SELECT status FROM operations WHERE operation_id=?", (op_id,)).fetchone()
    child_rows = db.execute(
        """SELECT c.slot, c.operation_id, o.status
        FROM pm_work_start_children c
        JOIN operations o ON o.operation_id = c.operation_id
        WHERE c.parent_id = ?
        ORDER BY c.ordinal ASC""",
        (op_id,),
    ).fetchall()
    for c in child_rows:
        if c["status"] in CHILD_STOPPED and _uncertain_db(db, c["operation_id"]):
            raise _bad(
                "PM_WORK_START_OUTCOME_UNPROVEN",
                f"Earlier handoff child {c['operation_id']} (from start {op_id}) has an unproven external step and "
                "may have started a session; check the Work's sessions on BAT before starting again",
                409,
            )
    if parent_row is None or parent_row["status"] not in OP_TERMINAL:
        raise _bad(
            "PM_WORK_START_IN_PROGRESS",
            f"Work item already has an active start in progress ({op_id}); follow, resume, or cancel that operation first",
            409,
        )
    for c in child_rows:
        if c["status"] not in OP_TERMINAL:
            raise _bad(
                "PM_WORK_START_IN_PROGRESS",
                f"Work start {op_id} still has unsettled child operation {c['operation_id']} ({c['status']}); "
                "wait for it to settle first",
                409,
            )
        if _uncertain_db(db, c["operation_id"]):
            raise _bad(
                "PM_WORK_START_OUTCOME_UNPROVEN",
                f"Earlier handoff child {c['operation_id']} (from start {op_id}) has an unproven external step and "
                "may have started a session; check the Work's sessions on BAT before starting again",
                409,
            )


require_no_active_attempt = require_latest_attempt_settled


def require_expected_latest_attempt(
    db, workspace_id: str, work_item_id: str, expected_attempt_id: str | None
) -> None:
    """Enforce optimistic concurrency on the Work's attempt chain."""
    if not installed(db):
        if expected_attempt_id is not None:
            raise _bad(
                "PM_WORK_ATTEMPT_CHANGED",
                "Work item has no recorded attempts (latest_attempt_id=null); preview again before starting",
                409,
            )
        return
    row = db.execute(
        """SELECT attempt_id, start_operation_id, origin_id FROM work_attempts
        WHERE workspace_id=? AND work_item_id=? ORDER BY sequence DESC LIMIT 1""",
        (workspace_id, work_item_id),
    ).fetchone()
    actual_attempt_id = row["attempt_id"] if row is not None else None
    if expected_attempt_id != actual_attempt_id:
        if row is not None:
            op_id = row["start_operation_id"] or row["origin_id"]
            raise _bad(
                "PM_WORK_ATTEMPT_CHANGED",
                f"Work item latest attempt is now {actual_attempt_id} (start operation {op_id}); "
                f"follow {op_id} or preview again before starting",
                409,
            )
        raise _bad(
            "PM_WORK_ATTEMPT_CHANGED",
            "Work item has no recorded attempts (latest_attempt_id=null); preview again before starting",
            409,
        )


def latest_attempt_id(db, workspace_id: str, work_item_id: str) -> str | None:
    if not installed(db):
        return None
    row = db.execute(
        """SELECT attempt_id FROM work_attempts
        WHERE workspace_id=? AND work_item_id=? ORDER BY sequence DESC LIMIT 1""",
        (workspace_id, work_item_id),
    ).fetchone()
    return row["attempt_id"] if row else None


def admit_operation(db, operation_id: str, action: str, target: dict, binding: dict | None, now: float | None = None) -> None:
    """INSERT-transaction hook: re-verify controller anchor and record the immutable work_attempts row."""
    if action != ACTION:
        return
    if not installed(db):
        raise _bad("PM_WORK_START_UNINITIALIZED", "work_attempts schema is not initialized", 409)
    saved = binding or {}
    intent = saved.get("work_start")
    digest = saved.get("work_start_digest")
    if not isinstance(intent, dict) or not isinstance(digest, str) or _digest(intent) != digest:
        raise _bad("PM_WORK_START_CONTEXT_INVALID", "Work start admission binding is missing or corrupt", 409)
    wid = target["workspace_id"]
    work_item_id = target["work_item_id"]
    controller = intent.get("controller") or saved.get("controller_authority")
    if not isinstance(controller, dict):
        raise _bad("PM_WORK_START_CONTEXT_INVALID", "Work start controller authority is missing", 409)
    require_work_controller(db, wid, work_item_id, controller)
    if "expected_latest_attempt_id" in intent:
        require_expected_latest_attempt(db, wid, work_item_id, intent["expected_latest_attempt_id"])
    require_latest_attempt_settled(db, wid, work_item_id)
    predecessor = latest_attempt_id(db, wid, work_item_id)
    created_at = time.time() if now is None else now
    attempt_id = "att_" + operation_id[3:]
    db.execute(
        """INSERT INTO work_attempts(
            attempt_id, workspace_id, work_item_id, origin_kind, origin_id,
            start_operation_id, predecessor_attempt_id, admission_digest, created_at
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            attempt_id,
            wid,
            work_item_id,
            "operation",
            operation_id,
            operation_id,
            predecessor,
            digest,
            created_at,
        ),
    )
    pkg_plan = intent.get("package_plan") if isinstance(intent.get("package_plan"), dict) else {}
    initial_result = {
        "attempt_id": attempt_id,
        "predecessor_attempt_id": predecessor,
        "workspace_id": wid,
        "work_item_id": work_item_id,
        "project_id": intent.get("project_id"),
        "stage": "package_prepared" if (pkg_plan.get("package_reused") and not intent.get("bind_needed")) else "preparing",
        "composition_profile": pkg_plan.get("composition_profile"),
        "binding_id": intent.get("expected_binding_id") if not intent.get("bind_needed") else None,
        "package_id": pkg_plan.get("package_id"),
        "package_reused": bool(pkg_plan.get("package_reused")),
        "code_source": intent.get("code_source"),
        "delivery": intent.get("delivery"),
        "children": {"bind": None, "package": None, "handoff": None},
        "execution_operation_id": None,
        "session": None,
        "existing_sessions": "continue_under_original_authority",
    }
    db.execute("UPDATE operations SET result=? WHERE operation_id=?", (_json(initial_result), operation_id))


def _package_title(title: str) -> str:
    return title if len(title) <= 200 else title[:199] + "…"


def _handoff_rationale(code_source: dict) -> str:
    return (
        f"Start from reviewed published head {code_source['source_ref']} "
        f"at {code_source['source_sha']} for work.start ({COMPOSITION_PROFILE})."
    )


def _bind_rationale(instruction: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", instruction).strip()


def compose_thin_package(
    ops,
    work: dict,
    plan: dict,
    requirement_ids: list[str],
    code_source: dict,
    delivery_goal: str,
    instruction: str,
    previous_package_id: str | None = None,
) -> tuple[dict, dict]:
    """Deterministically compose a work_start_thin_v1 package description without any model call."""
    req_map = {r["id"]: r for r in plan["document"]["requirements"]}
    selected = {rid: req_map[rid] for rid in requirement_ids}
    title = _package_title(work["title"])
    tasks = [
        {
            "key": f"req_{idx}",
            "description": selected[rid]["text"],
            "requirement_ids": [rid],
            "depends_on": [],
            "source_ids": [],
        }
        for idx, rid in enumerate(requirement_ids, 1)
    ]
    verification = []
    for idx, rid in enumerate(requirement_ids, 1):
        for jdx, acc in enumerate(selected[rid]["acceptance"], 1):
            verification.append(
                {
                    "key": f"ver_{idx}_{jdx}",
                    "requirement_id": rid,
                    "acceptance_input_ids": [acc["source_input_id"]],
                    "method": VERIFICATION_METHOD,
                    "expected_evidence": acc["text"],
                    "source_ids": [],
                }
            )
    tailoring = {
        "status": "not_applicable",
        "text": (
            f"Not part of this attempt: delivery stops at {delivery_goal}; "
            "merge and deployment are not authorized. Reviewers assess any migration in the resulting changes."
        ),
    }
    delivery_obj = {
        "repository": code_source["repository"],
        "repository_id": code_source["repository_id"],
        "base_ref": code_source["source_ref"],
        "goal": delivery_goal,
        "migration": dict(tailoring),
        "rollout": dict(tailoring),
        "rollback": dict(tailoring),
        "observation": dict(tailoring),
    }
    package_params = {
        "title": title,
        "requirements": list(requirement_ids),
        "source_refs": [],
        "code_source": copy.deepcopy(code_source),
        "design": {"status": "not_applicable", "text": DESIGN_TEXT, "source_ids": []},
        "tasks": tasks,
        "verification": verification,
        "delivery": delivery_obj,
        "rationale": instruction,
    }
    if previous_package_id is not None:
        package_params["previous_package_id"] = previous_package_id
    pm_worktree.validate_selection(ops, package_params["code_source"])
    if verification:
        doc = fmt.descriptions(ops, package_params, selected)
    else:
        doc = {
            "title": fmt.text(package_params["title"], "title", 200),
            "requirements": list(selected.values()),
            "source_refs": [],
            "code_source": copy.deepcopy(code_source),
            "design": fmt.design(package_params["design"], set()),
            "tasks": fmt.tasks(package_params["tasks"], set(requirement_ids), set()),
            "verification": [],
            "delivery": fmt.delivery(package_params["delivery"], code_source),
            "unresolved": [],
            "rationale": fmt.text(package_params["rationale"], "rationale"),
            "evidence_refs": [],
        }
    return package_params, doc


def evaluate_package_plan(
    ops,
    principal: Principal,
    wid: str,
    work: dict,
    project: dict,
    binding: dict | None,
    current_plan: dict,
    plan_ref: dict,
    requirement_ids: list[str],
    code_source: dict,
    delivery_goal: str,
    instruction: str,
) -> tuple[dict, dict]:
    """Decide reuse vs compose and evaluate readiness checks using the shared helper."""
    latest_id = pm_work_packages._latest(ops.db, wid, work["work_item_id"])
    prior_record = None
    if latest_id is not None:
        prior_record = pm_work_packages._record(ops.db, wid, latest_id)
        if (
            prior_record["work_item_id"] != work["work_item_id"]
            or pm_work_packages._access(ops, principal, prior_record) >= pm_work_packages.MAX_ANCESTORS
        ):
            raise _bad("PM_PACKAGE_HISTORY_LIMIT", "The original work package lineage cannot be extended", 409)
    if (
        prior_record is not None
        and binding is not None
        and binding["project_id"] == work["project_id"]
        and binding["plan_revision_id"] == current_plan["revision_id"]
        and binding["requirement_ids"] == requirement_ids
    ):
        doc = prior_record["document"]
        if (
            doc["context"]["plan_ref"]["scope_revision"] == plan_ref["scope_revision"]
            and doc["context"]["work_ref"]["binding_id"] == binding["binding_id"]
            and doc["context"]["work_ref"]["work_fingerprint"] == work_items.fingerprint(work)
            and doc["context"]["work_ref"]["project_id"] == work["project_id"]
            and [r["id"] for r in doc["requirements"]] == requirement_ids
            and doc["code_source"] == code_source
            and doc["delivery"]["goal"] == delivery_goal
            and doc["delivery"]["base_ref"] == code_source["source_ref"]
        ):
            real_readiness = pm_work_packages.readiness(ops, principal, wid, latest_id)
            checks = real_readiness["checks"]
            blocked = [
                row["check"]
                for row in checks
                if row["status"] != "passed" and row["check"] != "spec_code_alignment"
            ]
            return (
                {
                    "mode": "reuse",
                    "package_reused": True,
                    "package_id": latest_id,
                    "document_digest": prior_record["document_digest"],
                    "previous_package_id": prior_record["previous_package_id"],
                    "package_ref": {
                        "workspace_id": wid,
                        "package_id": latest_id,
                        "document_digest": prior_record["document_digest"],
                    },
                    "composition_profile": None,
                    "description": doc,
                },
                {"status": "blocked" if blocked else "ready", "checks": checks},
            )
    package_params, composed_doc = compose_thin_package(
        ops,
        work,
        current_plan,
        requirement_ids,
        code_source,
        delivery_goal,
        instruction,
        latest_id,
    )
    checks = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "status": "passed" if passed else "blocked", "detail": detail})

    current = pm_plans.accepted_plan(ops, principal, wid)["plan"]
    check(
        "scope",
        current is not None and pm_work_packages._plan_ref(current)["scope_revision"] == plan_ref["scope_revision"],
        "The package retains its original plan; a different current scope needs explicit replanning.",
    )
    check(
        "work",
        not work["archived"]
        and not project["archived"]
        and (binding is None or binding["project_id"] == work["project_id"]),
        "Current active work and its explicit association must match the frozen package.",
    )
    try:
        repository_sync.config_snapshot(
            ops, {key: code_source[key] for key in ("repository", "host", "workspace_id")}
        )
        configured = True
    except OperationError:
        configured = False
    check("code_configuration", configured, "Published head and adapter readiness are not refreshed by this local read.")
    check(
        "project_repository",
        code_source["repository"].lower() in {name.lower() for name in project["repositories"]},
        "The current work project must still name the selected code repository.",
    )
    checks.extend(pm_work_packages.document_readiness_checks(composed_doc))
    blocked = [
        row["check"]
        for row in checks
        if row["status"] != "passed" and row["check"] != "spec_code_alignment"
    ]
    return (
        {
            "mode": "compose",
            "package_reused": False,
            "package_id": None,
            "document_digest": None,
            "previous_package_id": latest_id,
            "package_ref": None,
            "composition_profile": COMPOSITION_PROFILE,
            "description": package_params,
        },
        {"status": "blocked" if blocked else "ready", "checks": checks},
    )


def _require_ready(selected_requirements: list[dict], package_plan: dict, readiness: dict) -> None:
    if package_plan["mode"] == "compose":
        prereq_ids = [r["id"] for r in selected_requirements if r.get("depends_on")]
        if prereq_ids:
            raise _bad(
                "PM_WORK_START_PREREQUISITES",
                "Requirements declaring depends_on prerequisites ("
                + ", ".join("dependency:" + rid for rid in prereq_ids)
                + ") are not supported by the thin work.start path; use pm.work_package.create and pm.work_batch.start",
                409,
            )
    blocked = [
        row["check"]
        for row in readiness["checks"]
        if row["status"] != "passed" and row["check"] != "spec_code_alignment"
    ]
    if blocked:
        raise _bad(
            "PM_WORK_START_NOT_READY",
            "Work is not ready to start; blocked readiness checks: " + ", ".join(blocked),
            409,
        )


def _resolve_requirement_ids(current_plan: dict, binding: dict | None, raw_ids) -> tuple[list[str], bool]:
    plan_reqs = {r["id"]: r for r in current_plan["document"]["requirements"]}
    bound_to_current = bool(
        binding is not None
        and binding["plan_revision_id"] == current_plan["revision_id"]
        and binding["requirement_ids"]
        and all(rid in plan_reqs for rid in binding["requirement_ids"])
    )
    if raw_ids is None:
        if not bound_to_current:
            raise _bad(
                "INVALID_PARAMS",
                "requirement_ids are required when the work item is not bound to the current adopted plan",
                422,
            )
        requirement_ids = list(binding["requirement_ids"])
    else:
        if (
            not isinstance(raw_ids, list)
            or not 1 <= len(raw_ids) <= 50
            or any(not isinstance(rid, str) or rid not in plan_reqs for rid in raw_ids)
            or len(set(raw_ids)) != len(raw_ids)
        ):
            raise _bad(
                "INVALID_PARAMS",
                "requirement_ids must be 1-50 distinct requirement IDs from the current adopted plan",
                422,
            )
        requirement_ids = list(raw_ids)
    bind_needed = not (
        binding is not None
        and binding["plan_revision_id"] == current_plan["revision_id"]
        and binding["requirement_ids"] == requirement_ids
    )
    return requirement_ids, bind_needed


def _validate_preconditions_shape(pre: dict, *, pending_scope: bool = False) -> None:
    expected = PRECONDITIONS - {"expected_scope_revision"} if pending_scope else PRECONDITIONS
    if not isinstance(pre, dict) or set(pre) != expected:
        raise _bad("PRECONDITION_REQUIRED", "work.start requires all ten preview preconditions", 422)
    if (
        not isinstance(pre["expected_work_item_fingerprint"], str)
        or not pm_plans.DIGEST.fullmatch(pre["expected_work_item_fingerprint"])
        or type(pre["expected_work_version"]) is not int
        or pre["expected_work_version"] < 0
        or (
            pre["expected_binding_id"] is not None
            and (
                not isinstance(pre["expected_binding_id"], str)
                or not pm_evidence._BINDING.fullmatch(pre["expected_binding_id"])
            )
        )
        or (not pending_scope and (
            not isinstance(pre["expected_scope_revision"], str)
            or not re.fullmatch(r"pmscope_[0-9a-f]{32}", pre["expected_scope_revision"])
        ))
        or (
            pre["expected_previous_package_id"] is not None
            and (
                not isinstance(pre["expected_previous_package_id"], str)
                or not pm_work_packages.PACKAGE.fullmatch(pre["expected_previous_package_id"])
            )
        )
        or type(pre["expected_project_version"]) is not int
        or pre["expected_project_version"] < 0
        or type(pre["repository_id"]) is not int
        or pre["repository_id"] <= 0
        or not isinstance(pre["binding_digest"], str)
        or not repository_sync.DIGEST.fullmatch(pre["binding_digest"])
        or not isinstance(pre["expected_source_sha"], str)
        or not repository_sync.SHA.fullmatch(pre["expected_source_sha"])
        or (
            pre["expected_latest_attempt_id"] is not None
            and (
                not isinstance(pre["expected_latest_attempt_id"], str)
                or not ATTEMPT_ID.fullmatch(pre["expected_latest_attempt_id"])
            )
        )
    ):
        raise _bad("PRECONDITION_REQUIRED", "Supply valid preview preconditions for work.start", 422)


def _validate_profile_preconditions(pre: dict) -> None:
    expected = PRECONDITIONS | {"expected_profile_revision", "expected_profile_digest"}
    if not isinstance(pre, dict) or set(pre) != expected:
        raise _bad("PRECONDITION_REQUIRED", "work.start requires all twelve preview preconditions", 422)
    _validate_preconditions_shape({key: pre[key] for key in PRECONDITIONS})
    revision = pre["expected_profile_revision"]
    digest = pre["expected_profile_digest"]
    if (
        type(revision) is not int
        or revision < 1
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        raise _bad("PRECONDITION_REQUIRED", "Supply valid preview preconditions for work.start", 422)


def check_handoff_size(ops, wid: str, instruction: str, code_source: dict, package_plan: dict) -> None:
    """Bound the same complete handoff contract before allocating any child."""
    # Verify bounded prompt contract length before admitting any operation or child.
    synthetic_contract = {
        "contract": pm_package_handoff.CONTRACT,
        "package_ref": package_plan["package_ref"]
        or {"workspace_id": wid, "package_id": "pmwp_" + "0" * 32, "document_digest": "0" * 64},
        "package": package_plan["description"],
        "code_basis": {
            "mode": pm_package_handoff.MODE,
            "rationale": _handoff_rationale(code_source),
            "compatibility": "unverified",
            "scope_change_authorized": False,
        },
        "execution_assignment": "this_original_operation_only",
        "verification": "unassessed",
        "delivery": "not_observed",
    }
    text = pm_package_handoff.prompt(instruction, synthetic_contract)
    if len(checkpoints._input_instructions(ops.db, text, [])) > service.MAX_PROMPT_CHARS - 100:
        raise _bad(
            "PM_HANDOFF_TOO_LARGE",
            "The complete engineering contract and instructions exceed the prompt limit",
            413,
        )


def check_work_preconditions(ops, principal: Principal, wid: str, wi_id: str,
                             params: dict, pre: dict, *, controller: dict,
                             pending_scope: bool = False) -> dict:
    """Shared local Work checks; a pending adoption never fabricates a scope ID.

    The caller checks workspace membership. This performs no writes or network
    calls; the real work.start still checks the adopted plan at child admission.
    """
    require_work_controller(ops.db, wid, wi_id, controller)
    require_latest_attempt_settled(ops.db, wid, wi_id)
    if (
        not isinstance(params, dict)
        or not PARAMS_REQUIRED <= set(params)
        or set(params) - PARAMS_REQUIRED - PARAMS_OPTIONAL
    ):
        raise _bad("INVALID_PARAMS", "Supply exact work.start parameters", 422)
    if pending_scope and "profile_id" in params:
        raise _bad("INVALID_PARAMS", "Adopt-and-start requires an explicit code target", 422)
    if "profile_id" in params:
        _validate_profile_preconditions(pre)
    else:
        _validate_preconditions_shape(pre, pending_scope=pending_scope)
    instruction = pm_plans._text(params.get("instruction"), "instruction", 4000)
    if not _bind_rationale(instruction):
        raise _bad("INVALID_PARAMS", "instruction must contain non-whitespace characters", 422)
    agent = params.get("agent")
    if not isinstance(agent, str) or agent not in AGENTS:
        raise _bad("INVALID_PARAMS", "agent must be claude or codex", 422)
    model = params.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip() or len(model) > 256):
        raise _bad("INVALID_PARAMS", "model must contain 1-256 characters", 422)
    delivery_goal = params.get("delivery")
    if not isinstance(delivery_goal, str) or delivery_goal not in DELIVERY_GOALS:
        raise _bad("INVALID_PARAMS", "delivery must be pull_request or reviewable_changes", 422)
    code_target = params.get("code_target")
    if (
        not isinstance(code_target, dict)
        or set(code_target) != set(CODE_TARGET_KEYS)
        or any(not isinstance(code_target.get(k), str) or not code_target[k].strip() for k in CODE_TARGET_KEYS)
    ):
        raise _bad("INVALID_PARAMS", "code_target requires repository, host, workspace_id and source_ref", 422)
    repository_sync.check_ref(code_target["source_ref"])

    work = work_items._get_item(ops.db, wi_id, active=True)
    project = work_items._get_project(ops.db, work["project_id"], active=True)
    if not ops.db.execute(
        "SELECT 1 FROM pm_project_bindings WHERE workspace_id=? AND project_id=?",
        (wid, work["project_id"]),
    ).fetchone():
        raise _bad("PM_PROJECT_BINDING_REQUIRED", "Explicitly bind this work's project first", 409)
    if pre["expected_work_version"] != work["version"] or pre["expected_work_item_fingerprint"] != work_items.fingerprint(work):
        raise _bad("WORK_ITEM_CHANGED", "Read this work item's current version and fingerprint before starting", 409)
    if pre["expected_project_version"] != project["version"]:
        raise _bad("PROJECT_CHANGED", "Review the current project before starting", 409)
    binding = pm_evidence._binding(ops.db, wid, wi_id)
    actual_binding_id = binding["binding_id"] if binding else None
    if pre["expected_binding_id"] != actual_binding_id:
        raise _bad("PM_WORK_BINDING_CHANGED", "Read the current work association before starting", 409)
    if binding is not None and binding["project_id"] != work["project_id"]:
        raise _bad("PM_WORK_BINDING_CHANGED", "The work item moved to another project; reassociate it first", 409)

    latest_package_id = pm_work_packages._latest(ops.db, wid, wi_id)
    if pre["expected_previous_package_id"] != latest_package_id:
        raise _bad("PM_PACKAGE_VERSION_CONFLICT", "Read the latest work package before starting", 409)
    require_expected_latest_attempt(ops.db, wid, wi_id, pre["expected_latest_attempt_id"])

    route_pin = {}
    if "profile_id" in params:
        from .pm_work_profiles import pinned_revision
        pinned = pinned_revision(
            ops.db, principal, wid, params.get("profile_id"), pre["expected_profile_revision"],
            digest=pre["expected_profile_digest"], admission=True,
        )
        _profile_admission_target(params, code_target, delivery_goal, pinned)
        route = profile_route(ops, pinned["document"], require_ready=True)
        if route != "bat_work_start":
            route_pin = {"route": route, "profile": pinned}
    if code_target["repository"].lower() not in {r.lower() for r in project["repositories"]}:
        raise _bad("PROJECT_REPOSITORY_CHANGED", "Selected repository does not belong to this work item's project", 409)
    target_3 = {k: code_target[k] for k in ("repository", "host", "workspace_id")}
    config = repository_sync.config_snapshot(ops, target_3)
    code_source = {
        "repository": config["repository"],
        "host": code_target["host"],
        "workspace_id": code_target["workspace_id"],
        "source_ref": code_target["source_ref"],
        "source_sha": pre["expected_source_sha"],
        "repository_id": pre["repository_id"],
        "binding_digest": pre["binding_digest"],
    }
    pm_worktree.validate_selection(ops, code_source)

    return {
        "work": work, "project": project, "binding": binding,
        "actual_binding_id": actual_binding_id, "latest_package_id": latest_package_id,
        "config": config, "code_source": code_source, "instruction": instruction,
        "agent": agent, "model": model, "delivery": delivery_goal,
        **route_pin,
    }


def _check_admission(ops, principal: Principal, target: dict, params: dict, pre: dict) -> dict:
    if not isinstance(target, dict) or set(target) != {"workspace_id", "work_item_id"}:
        raise _bad("INVALID_TARGET", "Exact workspace_id and work_item_id are required", 422)
    wid, wi_id = target["workspace_id"], target["work_item_id"]
    if not isinstance(wi_id, str) or not work_items.WORK_ITEM_ID.fullmatch(wi_id):
        raise _bad("INVALID_TARGET", "work_item_id must identify a Connector work item", 422)
    workspace = _member(ops, principal, wid, write=True)
    controller = controller_authority.binding(ops.db, principal, pm_workspace_id=wid)
    local = check_work_preconditions(ops, principal, wid, wi_id, params, pre, controller=controller)
    work, project, binding = local["work"], local["project"], local["binding"]
    actual_binding_id, latest_package_id = local["actual_binding_id"], local["latest_package_id"]
    config, code_source = local["config"], local["code_source"]
    instruction, agent, model = local["instruction"], local["agent"], local["model"]
    delivery_goal = local["delivery"]

    current_plan = pm_plans.accepted_plan(ops, principal, wid)["plan"]
    if current_plan is None:
        raise _bad("PM_ADOPTED_PLAN_REQUIRED", "Starting work requires an explicitly adopted plan", 409)
    plan_ref = pm_work_packages._plan_ref(current_plan)
    if pre["expected_scope_revision"] != plan_ref["scope_revision"]:
        raise _bad("PM_PACKAGE_SCOPE_CHANGED", "Prepare work against the current explicitly adopted scope", 409)

    requirement_ids, bind_needed = _resolve_requirement_ids(current_plan, binding, params.get("requirement_ids"))
    if bind_needed:
        _member(ops, principal, wid, write=True, decide=True)

    req_map = {r["id"]: r for r in current_plan["document"]["requirements"]}
    selected_reqs = [req_map[rid] for rid in requirement_ids]
    package_plan, readiness = evaluate_package_plan(
        ops,
        principal,
        wid,
        work,
        project,
        binding,
        current_plan,
        plan_ref,
        requirement_ids,
        code_source,
        delivery_goal,
        instruction,
    )
    _require_ready(selected_reqs, package_plan, readiness)

    check_handoff_size(ops, wid, instruction, code_source, package_plan)

    lifecycle = pm_lifecycle_gates.head(ops.db, wid)
    return {
        "version": 1,
        "composition_profile": COMPOSITION_PROFILE,
        "workspace_id": wid,
        "server_id": workspace["server_id"],
        "work_item_id": wi_id,
        "project_id": work["project_id"],
        "controller": controller,
        "execution_epoch": lifecycle["execution_epoch"],
        "plan_ref": plan_ref,
        "requirement_ids": requirement_ids,
        "bind_needed": bind_needed,
        "expected_binding_id": actual_binding_id,
        "expected_work_version": work["version"],
        "expected_work_item_fingerprint": work_items.fingerprint(work),
        "expected_project_version": project["version"],
        "expected_previous_package_id": latest_package_id,
        "expected_latest_attempt_id": pre["expected_latest_attempt_id"],
        "code_source": code_source,
        "code_config_digest": repository_sync.digest(config),
        "delivery": delivery_goal,
        "agent": agent,
        "model": model,
        **({"route": local["route"], "profile": local["profile"]} if "route" in local else {}),
        "instruction": instruction,
        "title": work["title"][:256],
        "package_plan": {
            "mode": package_plan["mode"],
            "package_reused": package_plan["package_reused"],
            "package_id": package_plan["package_id"],
            "document_digest": package_plan["document_digest"],
            "previous_package_id": package_plan["previous_package_id"],
            "package_ref": package_plan["package_ref"],
            "composition_profile": package_plan["composition_profile"],
            "description": package_plan["description"],
        },
    }


def _admit(ops, principal: Principal, target: dict, params: dict, pre: dict) -> dict:
    with ops.journal.tx():
        intent = _check_admission(ops, principal, target, params, pre)
    return {
        "principal": pm_workspace.principal_binding(principal),
        "controller_authority": intent["controller"],
        "pm_lineage": {"workspace_id": target["workspace_id"]},
        "work_start": intent,
        "work_start_digest": _digest(intent),
    }


def _op(db, operation_id: str) -> dict:
    row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
    if row is None:
        raise _bad("NOT_FOUND", "Original work.start operation not found", 404)
    return OperationService._decode(row)


def _intent(op: dict) -> dict:
    saved = ((op.get("external_refs") or {}).get("admission_binding") or {})
    intent = saved.get("work_start")
    if not isinstance(intent, dict) or _digest(intent) != saved.get("work_start_digest"):
        raise _bad("PM_WORK_START_CONTEXT_INVALID", "Original work.start admission binding cannot be verified", 409)
    return intent


class _Reader:
    def __init__(self, db):
        self.db = db
        self.journal = self

    def tx(self):
        return snapshots._snapshot(self.db)

    def get(self, operation_id: str, **kwargs) -> dict:
        row = self.db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None or row["action"].startswith("pm.") or row["action"] in {ACTION, "work.adopt_and_start"}:
            raise _bad("NOT_FOUND", "Original source operation not found", 404)
        return OperationService._decode(row)


def _access_intent(ops, principal: Principal, op: dict) -> None:
    intent = _intent(op)
    wid = op["target"]["workspace_id"]
    if not principal.allows("observe"):
        raise _bad("FORBIDDEN", "Observing work.start requires observe", 403)
    workspace = pm_workspace.require_member(ops.db, principal, wid)
    if intent.get("server_id") is not None and workspace["server_id"] != intent.get("server_id"):
        raise _bad("PM_WORK_START_SOURCE_UNAVAILABLE", "Original workspace identity is unavailable", 404)
    if "plan_ref" in intent:
        pm_work_packages._original_plan(ops, principal, wid, intent["plan_ref"])
    if isinstance(intent.get("package_plan"), dict):
        previous = intent["package_plan"].get("previous_package_id")
        if previous:
            pm_work_packages._access(ops, principal, pm_work_packages._record(ops.db, wid, previous))
        reused_id = intent["package_plan"].get("package_id")
        if reused_id:
            pm_work_packages._access(ops, principal, pm_work_packages._record(ops.db, wid, reused_id))


def operation_visible(db, principal: Principal, operation: dict) -> bool:
    with snapshots._snapshot(db):
        try:
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation.get("operation_id"),)).fetchone()
            if row is None or row["action"] != ACTION:
                return False
            op = OperationService._decode(row)
            _access_intent(_Reader(db), principal, op)
            return True
        except (OperationError, KeyError, TypeError, ValueError):
            return False


source_visible = operation_visible


def _authorize_existing(ops, principal: Principal, op: dict, verb: str) -> None:
    if not operation_visible(ops.db, principal, op):
        raise _bad("NOT_FOUND", "Original work.start sources are unavailable", 404)
    _member(ops, principal, op["target"]["workspace_id"], write=True)
    if verb != "replay" and principal.actor != op["actor"]:
        raise _bad("FORBIDDEN", "Steering requires the original work.start actor", 403)


def _current_principal(ops, op: dict) -> Principal:
    saved = op["external_refs"]["admission_binding"]["principal"]
    now, actor = time.time(), op["actor"]
    if saved["actor"] != actor or not (saved["admin"] or REQUIRED_SCOPES <= set(saved["scopes"])):
        raise _bad("PM_WORK_START_AUTHORITY_UNAVAILABLE", "Original work.start authority is unavailable", 403)
    principal = None
    for row in ops.db.execute(
        "SELECT token_hash,scopes FROM api_principals WHERE actor=? AND revoked_at IS NULL "
        "AND created_at<=? AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC LIMIT 64",
        (actor, now, now),
    ):
        try:
            scopes = json.loads(row["scopes"])
        except (ValueError, TypeError):
            continue
        if isinstance(scopes, list) and REQUIRED_SCOPES <= set(scopes):
            principal = api_auth.Principal(actor, REQUIRED_SCOPES, credential_id=row["token_hash"])
            break
    if principal is None and saved["admin"]:
        daemon = ops.context.get("daemon")
        token = vars(daemon).get("_admin_token") if daemon else None
        principal = api_auth.authenticate(ops.db, token, token) if isinstance(token, str) and token else None
        if principal and principal.actor != actor:
            principal = None
    if principal is None:
        raise _bad(
            "PM_WORK_START_AUTHORITY_UNAVAILABLE",
            "No single current credential authorizes the original work.start",
            403,
        )
    intent = _intent(op)
    _member(ops, principal, op["target"]["workspace_id"], write=True, decide=bool(intent.get("bind_needed")))
    current = controller_authority.binding(ops.db, principal, pm_workspace_id=op["target"]["workspace_id"])
    if not controller_authority.same_controller(current, intent["controller"]):
        raise _bad("PM_WORK_START_CONTROLLER_CHANGED", "The original work.start controller changed", 403)
    require_work_controller(ops.db, op["target"]["workspace_id"], op["target"]["work_item_id"], current)
    return principal


def _bind_request(parent_id: str, intent: dict) -> dict:
    return {
        "action": "pm.work.bind",
        "target": {
            "workspace_id": intent["workspace_id"],
            "work_item_id": intent["work_item_id"],
        },
        "params": {
            "rationale": _bind_rationale(intent["instruction"]),
            "plan_revision_id": intent["plan_ref"]["revision_id"],
            "requirement_ids": list(intent["requirement_ids"]),
        },
        "preconditions": {
            "expected_binding_id": intent["expected_binding_id"],
            "expected_work_version": intent["expected_work_version"],
        },
        "idempotency_key": f"work-start:{parent_id}:bind",
    }


def _package_request(parent_id: str, intent: dict, binding_id: str) -> dict:
    return {
        "action": "pm.work_package.create",
        "target": {
            "workspace_id": intent["workspace_id"],
            "work_item_id": intent["work_item_id"],
        },
        "params": copy.deepcopy(intent["package_plan"]["description"]),
        "preconditions": {
            "expected_binding_id": binding_id,
            "expected_work_fingerprint": intent["expected_work_item_fingerprint"],
            "expected_scope_revision": intent["plan_ref"]["scope_revision"],
            "expected_previous_package_id": intent["expected_previous_package_id"],
        },
        "idempotency_key": f"work-start:{parent_id}:package",
    }


def _bat_handoff_request(parent_id: str, intent: dict, package_ref: dict) -> dict:
    code_source = intent["code_source"]
    params = {
        "source_ref": code_source["source_ref"],
        "source_sha": code_source["source_sha"],
        "agent": intent["agent"],
        "prompt": intent["instruction"],
        "title": intent["title"],
        "project_id": intent["project_id"],
        "work_item_id": intent["work_item_id"],
        "pm_package_ref": copy.deepcopy(package_ref),
        "pm_code_basis": {
            "mode": pm_package_handoff.MODE,
            "rationale": _handoff_rationale(code_source),
        },
    }
    if intent.get("model") is not None:
        params["model"] = intent["model"]
    return {
        "action": "repository.continue",
        "target": {
            "repository": code_source["repository"],
            "host": code_source["host"],
            "workspace_id": code_source["workspace_id"],
        },
        "params": params,
        "preconditions": {
            "repository_id": code_source["repository_id"],
            "binding_digest": code_source["binding_digest"],
            "expected_project_version": intent["expected_project_version"],
            "expected_work_item_fingerprint": intent["expected_work_item_fingerprint"],
        },
        "idempotency_key": f"work-start:{parent_id}:handoff",
    }


_HANDOFF_ROUTES = {"bat_work_start": _bat_handoff_request}


def _handoff_request(parent_id: str, intent: dict, package_ref: dict) -> dict:
    builder = _HANDOFF_ROUTES.get(intent.get("route", "bat_work_start"))
    if builder is None:
        raise _bad("PM_WORK_ROUTE_UNAVAILABLE", "The original handoff route is not installed")
    return builder(parent_id, intent, package_ref)


def _expected_child_request(ops, parent: dict, slot: str) -> dict:
    intent = _intent(parent)
    parent_id = parent["operation_id"]
    if slot == "bind":
        if not intent.get("bind_needed"):
            raise _bad("PM_WORK_START_CHILD_CHANGED", "Unexpected bind child for already bound work", 409)
        return _bind_request(parent_id, intent)
    if slot == "package":
        if intent["package_plan"]["mode"] != "compose":
            raise _bad("PM_WORK_START_CHILD_CHANGED", "Unexpected package child on package reuse path", 409)
        if intent.get("bind_needed"):
            bind_child = _child(ops, parent, "bind")
            binding_id = (bind_child.get("result") or {}).get("binding_id") if bind_child else None
            if not isinstance(binding_id, str):
                raise _bad("PM_WORK_START_CHILD_CHANGED", "Package child is missing original bind receipt", 409)
        else:
            binding_id = intent["expected_binding_id"]
        return _package_request(parent_id, intent, binding_id)
    if slot == "handoff":
        if intent["package_plan"]["mode"] == "reuse":
            pkg_ref = intent["package_plan"]["package_ref"]
        else:
            pkg_child = _child(ops, parent, "package")
            pkg_res = (pkg_child.get("result") or {}) if pkg_child else {}
            if not isinstance(pkg_res.get("package_id"), str) or not isinstance(pkg_res.get("document_digest"), str):
                raise _bad("PM_WORK_START_CHILD_CHANGED", "Handoff child is missing original package receipt", 409)
            pkg_ref = {
                "workspace_id": intent["workspace_id"],
                "package_id": pkg_res["package_id"],
                "document_digest": pkg_res["document_digest"],
            }
        return _handoff_request(parent_id, intent, pkg_ref)
    raise _bad("PM_WORK_START_CHILD_CHANGED", "Unknown work.start child slot", 409)


def _child(ops, parent: dict, slot: str) -> dict | None:
    link = ops.db.execute(
        "SELECT * FROM pm_work_start_children WHERE parent_id=? AND slot=?",
        (parent["operation_id"], slot),
    ).fetchone()
    if link is None:
        return None
    if link["ordinal"] != SLOT_ORDINAL.get(slot):
        raise _bad("PM_WORK_START_CHILD_CHANGED", "Original child slot ordinal does not match", 409)
    stored = json.loads(link["request"])
    expected = _expected_child_request(ops, parent, slot)
    child = _op(ops.db, link["operation_id"])
    if (
        _digest(stored) != link["request_digest"]
        or stored != expected
        or any(child[k] != stored[k] for k in ("action", "target", "params", "preconditions", "idempotency_key"))
        or child["actor"] != parent["actor"]
        or (child.get("external_refs") or {}).get(LINK) != {"operation_id": parent["operation_id"], "slot": slot}
        or not controller_authority.same_controller(
            controller_authority.operation_binding(child), _intent(parent)["controller"]
        )
    ):
        raise _bad("PM_WORK_START_CHILD_CHANGED", "The original work.start child receipt does not match", 409)
    return child


def _children(ops, parent: dict) -> dict[str, dict]:
    rows = ops.db.execute(
        "SELECT slot FROM pm_work_start_children WHERE parent_id=? ORDER BY ordinal ASC",
        (parent["operation_id"],),
    ).fetchall()
    out: dict[str, dict] = {}
    for row in rows:
        child = _child(ops, parent, row["slot"])
        if child is not None:
            out[row["slot"]] = child
    return out


def _uncertain(ops, child: dict) -> bool:
    return _uncertain_db(ops.db, child["operation_id"])


def _child_session(child: dict | None) -> dict | None:
    if child is None:
        return None
    res = child.get("result") or {}
    refs = child.get("external_refs") or {}
    host = res.get("host") or refs.get("host") or (child.get("target") or {}).get("host")
    sid = res.get("session_id") or refs.get("session_id")
    if isinstance(host, str) and host and isinstance(sid, str) and sid:
        return {"host": host, "session_id": sid}
    return None


def _failure_message(code: str, message: str | None) -> str:
    text = (message or f"work.start stopped with {code}").strip()
    if code in {"PM_PACKAGE_CODE_SOURCE_CHANGED", "REPOSITORY_REF_CHANGED"} and "review again" not in text.lower():
        text = f"{text}; review again with pm_work_start_preview before starting"
    return text


def _failure_reason(child: dict, fallback: str) -> str:
    code = child.get("error_code") or child.get("status") or "CHILD_FAILED"
    reason = child.get("status_reason") or fallback
    return _failure_message(code, reason)


def _check_readiness_for_intent(
    ops,
    principal: Principal,
    intent: dict,
    work: dict,
    current_plan: dict,
    created_package_id: str | None = None,
) -> None:
    wid = intent["workspace_id"]
    req_map = {r["id"]: r for r in current_plan["document"]["requirements"]}
    selected_reqs = [req_map[rid] for rid in intent["requirement_ids"]]
    if created_package_id is not None or intent["package_plan"]["mode"] == "reuse":
        pkg_id = created_package_id or intent["package_plan"]["package_id"]
        real_readiness = pm_work_packages.readiness(ops, principal, wid, pkg_id)
        _require_ready(selected_reqs, {"mode": "reuse"}, real_readiness)
        return
    _, composed_doc = compose_thin_package(
        ops,
        work,
        current_plan,
        intent["requirement_ids"],
        intent["code_source"],
        intent["delivery"],
        intent["instruction"],
        intent["expected_previous_package_id"],
    )
    checks = pm_work_packages.document_readiness_checks(composed_doc)
    _require_ready(selected_reqs, {"mode": "compose"}, {"checks": checks})


def _guard(ops, parent: dict, *, allow_cancelled_parent: bool = False) -> Principal:
    if parent["action"] != ACTION:
        raise _bad("PM_WORK_START_STOPPED", "The original work.start has stopped", 409)
    if not allow_cancelled_parent and (parent["cancel_requested"] or parent["status"] in OP_TERMINAL):
        raise _bad("PM_WORK_START_STOPPED", "The original work.start has stopped new child admission", 409)
    intent = _intent(parent)
    wid = parent["target"]["workspace_id"]
    wi_id = parent["target"]["work_item_id"]
    current_lc = pm_lifecycle_gates.head(ops.db, wid)
    pm_lifecycle_gates._require(current_lc, intent["execution_epoch"])
    principal = _current_principal(ops, parent)
    _access_intent(ops, principal, parent)
    if "route" in intent:
        from .pm_work_profiles import pinned_revision
        pin = intent["profile"]
        current = pinned_revision(ops.db, principal, wid, pin["profile_id"], pin["revision"],
                                  digest=pin["digest"], admission=True)
        if current["document"].get("adapter", "bat") + "_work_start" != intent["route"]:
            raise _bad("PM_WORK_ROUTE_CHANGED", "The admitted profile no longer names the original route")
        if hasattr(ops, "context"):
            profile_route(ops, current["document"], require_ready=True)

    work = work_items._get_item(ops.db, wi_id, active=True)
    project = work_items._get_project(ops.db, intent["project_id"], active=True)
    if (
        work["project_id"] != intent["project_id"]
        or work["version"] != intent["expected_work_version"]
        or work_items.fingerprint(work) != intent["expected_work_item_fingerprint"]
    ):
        raise _bad("WORK_ITEM_CHANGED", "Work item changed after work.start admission", 409)
    if project["version"] != intent["expected_project_version"]:
        raise _bad("PROJECT_CHANGED", "Project changed after work.start admission", 409)
    if not ops.db.execute(
        "SELECT 1 FROM pm_project_bindings WHERE workspace_id=? AND project_id=?",
        (wid, project["project_id"]),
    ).fetchone():
        raise _bad("PM_PROJECT_BINDING_REQUIRED", "Project binding is no longer present", 409)
    if intent["code_source"]["repository"].lower() not in {r.lower() for r in project["repositories"]}:
        raise _bad("PROJECT_REPOSITORY_CHANGED", "Selected repository does not belong to this work item's project", 409)
    config = repository_sync.config_snapshot(
        ops, {k: intent["code_source"][k] for k in ("repository", "host", "workspace_id")}
    )
    if repository_sync.digest(config) != intent["code_config_digest"]:
        raise _bad("REPOSITORY_BINDING_CHANGED", "Repository configuration changed after work.start admission", 409)

    current_plan = pm_plans.accepted_plan(ops, principal, wid)["plan"]
    if current_plan is None or pm_work_packages._plan_ref(current_plan)["scope_revision"] != intent["plan_ref"]["scope_revision"]:
        raise _bad("PM_PACKAGE_SCOPE_CHANGED", "Adopted scope changed after work.start admission", 409)

    children = _children(ops, parent)
    binding = pm_evidence._binding(ops.db, wid, wi_id)
    bind_child = children.get("bind")
    if bind_child is not None and ops.db.execute(
        "SELECT 1 FROM pm_evidence_applied WHERE operation_id=?", (bind_child["operation_id"],)
    ).fetchone():
        expected_b_id = "pmwb_" + bind_child["operation_id"][3:]
        if (
            binding is None
            or binding["binding_id"] != expected_b_id
            or binding["previous_binding_id"] != intent["expected_binding_id"]
            or binding["plan_revision_id"] != intent["plan_ref"]["revision_id"]
            or binding["requirement_ids"] != intent["requirement_ids"]
        ):
            raise _bad("PM_WORK_BINDING_CHANGED", "Work association changed after work.start admission", 409)
    else:
        if (binding["binding_id"] if binding else None) != intent["expected_binding_id"]:
            raise _bad("PM_WORK_BINDING_CHANGED", "Work association changed after work.start admission", 409)

    latest_pkg = pm_work_packages._latest(ops.db, wid, wi_id)
    pkg_child = children.get("package")
    created_pkg_id = None
    if pkg_child is not None and ops.db.execute(
        "SELECT 1 FROM pm_work_packages WHERE operation_id=?", (pkg_child["operation_id"],)
    ).fetchone():
        created_pkg_id = "pmwp_" + pkg_child["operation_id"][3:]
        if latest_pkg != created_pkg_id:
            raise _bad("PM_PACKAGE_VERSION_CONFLICT", "Work package lineage changed after work.start admission", 409)
    else:
        if latest_pkg != intent["expected_previous_package_id"]:
            raise _bad("PM_PACKAGE_VERSION_CONFLICT", "Work package lineage changed after work.start admission", 409)

    _check_readiness_for_intent(ops, principal, intent, work, current_plan, created_pkg_id)
    return principal


def require_child(ops_or_db, op_or_id) -> dict | None:
    """Registered unsent-step, local-effect and final-frame guard for work.start children."""
    if hasattr(ops_or_db, "db"):
        ops, db = ops_or_db, ops_or_db.db
    else:
        db = ops_or_db
        current = pm_source_lineage._EXECUTION.get()
        ops = current[0] if current is not None and current[0].db is db else _Reader(db)
    if isinstance(op_or_id, dict):
        op = op_or_id
        operation_id = op["operation_id"]
    else:
        operation_id = op_or_id
        row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            return None
        op = OperationService._decode(row)
    link = (op.get("external_refs") or {}).get(LINK)
    if not installed(db):
        if link is None:
            return None
        raise _bad("PM_WORK_START_CHILD_CHANGED", "Unproven work.start parent", 409)
    row = db.execute(
        "SELECT parent_id, slot, ordinal FROM pm_work_start_children WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if link is None and row is None:
        return None
    if (
        row is None
        or not isinstance(link, dict)
        or link != {"operation_id": row["parent_id"], "slot": row["slot"]}
    ):
        raise _bad("PM_WORK_START_CHILD_CHANGED", "Unproven work.start parent link", 409)
    parent = _op(db, row["parent_id"])
    child = _child(ops, parent, row["slot"])
    if child is None or child["operation_id"] != operation_id:
        raise _bad("PM_WORK_START_CHILD_CHANGED", "Child is outside the original work.start slots", 409)
    _guard(ops, parent, allow_cancelled_parent=True)
    return parent


def admit_restart(ops, operation_id: str, step_name: str) -> None:
    """Re-verify parent/child invariants inside the existing journal transaction before a proven-unsent step restart."""
    if require_child(ops, operation_id) is None:
        return
    if not ops.db.in_transaction:
        raise _bad("PM_WORK_START_RETRY_TRANSACTION", "Retry check requires the existing journal transaction", 409)
    step = ops.db.execute(
        "SELECT status FROM operation_steps WHERE operation_id=? AND name=?",
        (operation_id, step_name),
    ).fetchone()
    if not step or step["status"] not in {"started", "uncertain"}:
        raise _bad("PM_WORK_START_RETRY_UNPROVEN", "Only an unresolved step can perform a proven-unsent restart", 409)


def resume_children(ops, principal: Principal, op: dict) -> None:
    if op["action"] != ACTION or op["cancel_requested"] or not installed(ops.db):
        return
    for child in _children(ops, op).values():
        if child["status"] == "needs_attention" and not _uncertain(ops, child):
            ops.resume(principal, child["operation_id"])


def cancel_children(ops, principal: Principal, op: dict) -> None:
    if op["action"] != ACTION or not installed(ops.db):
        return
    for child in _children(ops, op).values():
        if child["status"] not in OP_TERMINAL:
            ops.cancel(principal, child["operation_id"])


def _admit_child(ctx, slot: str, request: dict) -> dict:
    def admit():
        parent = _op(ctx.service.db, ctx.operation_id)
        principal = _guard(ctx.service, parent)
        token = _HANDOFF_ADMISSION.set((ctx.service, parent, request) if slot == "handoff" else None)
        try:
            child, fresh = ctx.service.create(principal, **request, entry="work-start")
        finally:
            _HANDOFF_ADMISSION.reset(token)
        if not fresh:
            raise _bad(
                "PM_WORK_START_CHILD_CHANGED",
                "A child key already exists without its exact original relation",
                409,
            )
        ctx.service.db.execute(
            "INSERT INTO pm_work_start_children VALUES(?,?,?,?,?,?,?)",
            (
                ctx.operation_id,
                slot,
                SLOT_ORDINAL[slot],
                child["operation_id"],
                _json(request),
                _digest(request),
                time.time(),
            ),
        )
        ctx.service._merge_refs(
            child["operation_id"],
            {LINK: {"operation_id": ctx.operation_id, "slot": slot}},
        )
        if pm_source_lineage.installed(ctx.service.db):
            for root_id in pm_source_lineage.roots(ctx.service.db, "operation", ctx.operation_id):
                ctx.service.db.execute(
                    "INSERT OR IGNORE INTO pm_source_lineage VALUES(?,?,?,?)",
                    ("operation", child["operation_id"], root_id, ctx.operation_id),
                )
        return {"operation_id": child["operation_id"]}

    return ctx.effect("work.start.child." + slot, admit, request={"slot": slot, "action": request["action"]})


def _build_state(
    parent: dict,
    intent: dict,
    children: dict[str, dict],
    *,
    stage: str,
    binding_id: str | None = None,
    package_id: str | None = None,
    session: dict | None = None,
) -> dict:
    bind_child = children.get("bind")
    pkg_child = children.get("package")
    handoff_child = children.get("handoff")
    resolved_binding = (
        binding_id
        or ((bind_child.get("result") or {}).get("binding_id") if bind_child else None)
        or (intent.get("expected_binding_id") if not intent.get("bind_needed") else None)
    )
    resolved_package = (
        package_id
        or ((pkg_child.get("result") or {}).get("package_id") if pkg_child else None)
        or intent["package_plan"].get("package_id")
    )
    resolved_session = session or _child_session(handoff_child)
    prev_res = parent.get("result") if isinstance(parent.get("result"), dict) else {}
    return {
        "attempt_id": "att_" + parent["operation_id"][3:],
        "predecessor_attempt_id": prev_res.get("predecessor_attempt_id"),
        "workspace_id": intent["workspace_id"],
        "work_item_id": intent["work_item_id"],
        "project_id": intent["project_id"],
        "stage": stage,
        "composition_profile": intent["package_plan"].get("composition_profile"),
        "binding_id": resolved_binding,
        "package_id": resolved_package,
        "package_reused": bool(intent["package_plan"].get("package_reused")),
        "code_source": intent["code_source"],
        "delivery": intent["delivery"],
        "children": {
            "bind": bind_child["operation_id"] if bind_child else None,
            "package": pkg_child["operation_id"] if pkg_child else None,
            "handoff": handoff_child["operation_id"] if handoff_child else None,
        },
        "execution_operation_id": handoff_child["operation_id"] if handoff_child else None,
        "session": resolved_session,
        "existing_sessions": "continue_under_original_authority",
    }


def _save_progress(ctx, state: dict) -> dict:
    with ctx.service.journal.tx():
        ctx.service.db.execute(
            "UPDATE operations SET result=?, updated_at=? WHERE operation_id=?",
            (_json(state), time.time(), ctx.operation_id),
        )
    refs = {
        "work_start_state": state,
        "attempt_id": state["attempt_id"],
    }
    if state.get("binding_id"):
        refs["binding_id"] = state["binding_id"]
    if state.get("package_id"):
        refs["package_id"] = state["package_id"]
    if state.get("execution_operation_id"):
        refs["execution_operation_id"] = state["execution_operation_id"]
    if state.get("session"):
        refs["host"] = state["session"]["host"]
        refs["session_id"] = state["session"]["session_id"]
    ctx.set_refs(**refs)
    return state


def _finish(ctx, state: dict) -> dict:
    def record():
        ctx.service.journal.api_event(
            "pm_work_attempt",
            state["attempt_id"],
            "work.start.succeeded",
            {
                "workspace_id": state["workspace_id"],
                "work_item_id": state["work_item_id"],
                "attempt_id": state["attempt_id"],
                "operation_id": ctx.operation_id,
                "package_id": state["package_id"],
                "package_reused": state["package_reused"],
                "execution_operation_id": state["execution_operation_id"],
                "session": state["session"],
            },
            actor=ctx.actor,
        )
        return state

    return ctx.effect("work.start.finish", record, receipt_only=True)


# A resume of this same admission can never pass these codes. Every other
# OperationError stays needs_attention, including PM_WORK_START_STOPPED so a
# cancel that arrives inside _guard is still recorded as cancelled.
_UNRESUMABLE_PRE_CHILD = frozenset({
    "PM_LIFECYCLE_STOPPED",
    "PM_LIFECYCLE_EPOCH_CHANGED",
    "PM_PACKAGE_SCOPE_CHANGED",
    "WORK_ITEM_CHANGED",
    "PROJECT_CHANGED",
    "PM_WORK_BINDING_CHANGED",
    "PM_PACKAGE_VERSION_CONFLICT",
    "WORK_ITEM_ARCHIVED",
    "PROJECT_ARCHIVED",
})


def _stop_before_child(
    ctx,
    parent: dict,
    intent: dict,
    children: dict[str, dict],
    exc: OperationError,
    *,
    stage: str,
    binding_id: str | None = None,
    package_id: str | None = None,
) -> None:
    """Fail a pre-child stop no resume can pass; pause every other OperationError."""
    failed = exc.code in _UNRESUMABLE_PRE_CHILD
    _save_progress(
        ctx,
        _build_state(
            parent,
            intent,
            children,
            stage="start_failed" if failed else stage,
            binding_id=binding_id,
            package_id=package_id,
        ),
    )
    message = _failure_message(exc.code, exc.message)
    if failed:
        raise _bad(exc.code, message, exc.status) from exc
    raise NeedsAttention(exc.code, message) from exc


def _raise_child_stopped(
    ctx,
    parent: dict,
    intent: dict,
    children: dict[str, dict],
    child: dict,
    *,
    in_progress_stage: str,
    default_code: str,
    fallback: str,
    binding_id: str | None = None,
    package_id: str | None = None,
    session: dict | None = None,
) -> None:
    # A parent with its own pending cancel must end cancelled rather than failing on a cancelled child.
    ctx.check_cancel()
    is_uncertain = child["status"] == "uncertain" or _uncertain(ctx.service, child)
    is_failed = not is_uncertain and child["status"] in {"failed", "cancelled"}
    stage = "start_unknown" if is_uncertain else ("start_failed" if is_failed else in_progress_stage)
    _save_progress(
        ctx,
        _build_state(
            parent,
            intent,
            children,
            stage=stage,
            binding_id=binding_id,
            package_id=package_id,
            session=session,
        ),
    )
    reason = _failure_reason(child, fallback)
    if is_uncertain:
        code = child["error_code"] or "UNCERTAIN_UNRESOLVED"
        raise NeedsAttention(code, reason)
    if is_failed:
        code = child["error_code"] or ("CANCELLED" if child["status"] == "cancelled" else default_code)
        raise _bad(code, reason, 409)
    code = child["error_code"] or default_code
    raise NeedsAttention(code, reason)


async def _run(ctx):
    parent = _op(ctx.service.db, ctx.operation_id)
    intent = _intent(parent)
    children = _children(ctx.service, parent)

    # 1. Slot 0: bind (only when intent["bind_needed"] is True)
    if intent.get("bind_needed"):
        bind_child = children.get("bind")
        if bind_child is None:
            ctx.check_cancel()
            try:
                _guard(ctx.service, parent)
                _admit_child(ctx, "bind", _bind_request(ctx.operation_id, intent))
            except OperationError as exc:
                _stop_before_child(ctx, parent, intent, children, exc, stage="preparing")
            children = _children(ctx.service, parent)
            bind_child = children["bind"]
        if bind_child["status"] not in CHILD_STOPPED:
            _save_progress(ctx, _build_state(parent, intent, children, stage="preparing"))
            raise Wait("waiting_external", "Waiting for work binding child operation", delay_s=1.0)
        if bind_child["status"] != "succeeded":
            _raise_child_stopped(
                ctx,
                parent,
                intent,
                children,
                bind_child,
                in_progress_stage="preparing",
                default_code="PM_WORK_BIND_FAILED",
                fallback="Work binding did not complete",
            )
        binding_id = (bind_child.get("result") or {}).get("binding_id")
        if not isinstance(binding_id, str):
            _save_progress(ctx, _build_state(parent, intent, children, stage="preparing"))
            raise NeedsAttention("PM_WORK_START_CHILD_CHANGED", "Work binding child did not return binding_id")
    else:
        binding_id = intent["expected_binding_id"]

    # 2. Slot 1: package (only when intent["package_plan"]["mode"] == "compose")
    if intent["package_plan"]["mode"] == "reuse":
        package_id = intent["package_plan"]["package_id"]
        package_ref = intent["package_plan"]["package_ref"]
        _save_progress(
            ctx,
            _build_state(
                parent,
                intent,
                children,
                stage="package_prepared",
                binding_id=binding_id,
                package_id=package_id,
            ),
        )
    else:
        pkg_child = children.get("package")
        if pkg_child is None:
            ctx.check_cancel()
            try:
                _guard(ctx.service, _op(ctx.service.db, ctx.operation_id))
                _admit_child(ctx, "package", _package_request(ctx.operation_id, intent, binding_id))
            except OperationError as exc:
                _stop_before_child(
                    ctx, parent, intent, children, exc, stage="preparing", binding_id=binding_id,
                )
            children = _children(ctx.service, parent)
            pkg_child = children["package"]
        if pkg_child["status"] not in CHILD_STOPPED:
            _save_progress(
                ctx,
                _build_state(parent, intent, children, stage="preparing", binding_id=binding_id),
            )
            raise Wait("waiting_external", "Waiting for work package creation child operation", delay_s=1.0)
        if pkg_child["status"] != "succeeded":
            _raise_child_stopped(
                ctx,
                parent,
                intent,
                children,
                pkg_child,
                in_progress_stage="preparing",
                default_code="PM_WORK_PACKAGE_FAILED",
                fallback="Work package creation did not complete",
                binding_id=binding_id,
            )
        pkg_res = pkg_child.get("result") or {}
        package_id = pkg_res.get("package_id")
        doc_digest = pkg_res.get("document_digest")
        if not isinstance(package_id, str) or not isinstance(doc_digest, str):
            _save_progress(
                ctx,
                _build_state(parent, intent, children, stage="preparing", binding_id=binding_id),
            )
            raise NeedsAttention("PM_WORK_START_CHILD_CHANGED", "Work package child did not return package identity")
        package_ref = {
            "workspace_id": intent["workspace_id"],
            "package_id": package_id,
            "document_digest": doc_digest,
        }
        _save_progress(
            ctx,
            _build_state(
                parent,
                intent,
                children,
                stage="package_prepared",
                binding_id=binding_id,
                package_id=package_id,
            ),
        )

    # 3. Slot 2: handoff (repository.continue)
    handoff_child = children.get("handoff")
    if handoff_child is None:
        ctx.check_cancel()
        try:
            _guard(ctx.service, _op(ctx.service.db, ctx.operation_id))
            _admit_child(ctx, "handoff", _handoff_request(ctx.operation_id, intent, package_ref))
        except OperationError as exc:
            _stop_before_child(
                ctx,
                parent,
                intent,
                children,
                exc,
                stage="starting",
                binding_id=binding_id,
                package_id=package_id,
            )
        children = _children(ctx.service, parent)
        handoff_child = children["handoff"]

    session = _child_session(handoff_child)
    is_uncertain = handoff_child["status"] == "uncertain" or _uncertain(ctx.service, handoff_child)
    if handoff_child["status"] not in CHILD_STOPPED:
        stage = "start_unknown" if is_uncertain else "starting"
        _save_progress(
            ctx,
            _build_state(
                parent,
                intent,
                children,
                stage=stage,
                binding_id=binding_id,
                package_id=package_id,
                session=session,
            ),
        )
        reason = (
            "Handoff outcome is uncertain; reading back original child without resending"
            if is_uncertain
            else "Waiting for repository handoff child operation"
        )
        raise Wait("waiting_external", reason, delay_s=1.0)

    if handoff_child["status"] != "succeeded":
        _raise_child_stopped(
            ctx,
            parent,
            intent,
            children,
            handoff_child,
            in_progress_stage="starting",
            default_code="PM_HANDOFF_FAILED",
            fallback="Repository handoff did not complete",
            binding_id=binding_id,
            package_id=package_id,
            session=session,
        )

    final_state = _save_progress(
        ctx,
        _build_state(
            parent,
            intent,
            children,
            stage="started",
            binding_id=binding_id,
            package_id=package_id,
            session=session,
        ),
    )
    return _finish(ctx, final_state)


def _load_preview_local(ops, principal: Principal, wid: str, wi_id: str, body: dict):
    _member(ops, principal, wid, write=False)
    if not isinstance(wi_id, str) or not work_items.WORK_ITEM_ID.fullmatch(wi_id):
        raise _bad("INVALID_TARGET", "work_item_id must identify a Connector work item", 422)
    pm_source_lineage.require(ops.db, principal, "work_item", wi_id)
    work = work_items._get_item(ops.db, wi_id, active=True)
    project = work_items._get_project(ops.db, work["project_id"], active=True)
    if not ops.db.execute(
        "SELECT 1 FROM pm_project_bindings WHERE workspace_id=? AND project_id=?",
        (wid, work["project_id"]),
    ).fetchone():
        raise _bad("PM_PROJECT_BINDING_REQUIRED", "Explicitly bind this work's project first", 409)
    binding = pm_evidence._binding(ops.db, wid, wi_id)
    if binding is not None and binding["project_id"] != work["project_id"]:
        raise _bad("PM_WORK_BINDING_CHANGED", "The work item moved to another project; reassociate it first", 409)
    current_plan = pm_plans.accepted_plan(ops, principal, wid)["plan"]
    if current_plan is None:
        raise _bad("PM_ADOPTED_PLAN_REQUIRED", "Starting work requires an explicitly adopted plan", 409)
    plan_ref = pm_work_packages._plan_ref(current_plan)
    latest_package_id = pm_work_packages._latest(ops.db, wid, wi_id)
    requirement_ids, bind_needed = _resolve_requirement_ids(current_plan, binding, body.get("requirement_ids"))
    return work, project, binding, current_plan, plan_ref, latest_package_id, requirement_ids, bind_needed


def _profile_code_target(raw_target: dict, pinned: dict) -> None:
    for key in ("repository", "host", "workspace_id"):
        if not isinstance(raw_target.get(key), str) or not raw_target[key].strip():
            raise _bad("INVALID_PARAMS", "code_target requires repository, host and workspace_id", 422)
    binding = pinned["binding"]
    if raw_target["repository"].strip().lower() != str(binding.get("repository", "")).lower():
        raise _bad("PM_WORK_PROFILE_TARGET_MISMATCH", "code_target.repository does not match the work profile", 409)
    if raw_target["host"].strip() != binding.get("host"):
        raise _bad("PM_WORK_PROFILE_TARGET_MISMATCH", "code_target.host does not match the work profile", 409)
    if raw_target["workspace_id"].strip() != binding.get("workspace_id"):
        raise _bad("PM_WORK_PROFILE_TARGET_MISMATCH", "code_target.workspace_id does not match the work profile", 409)


def _delivery_for_profile(body: dict, goal) -> str:
    sent = body.get("delivery")
    if goal is None:
        delivery_goal = sent if sent is not None else "pull_request"
        if not isinstance(delivery_goal, str) or delivery_goal not in DELIVERY_GOALS:
            raise _bad("INVALID_PARAMS", "delivery must be pull_request or reviewable_changes", 422)
        return delivery_goal
    if sent is None:
        raise _bad("INVALID_PARAMS", f"delivery must be {goal}", 422)
    if sent != goal:
        raise _bad("PM_WORK_PROFILE_TARGET_MISMATCH", "delivery does not match the work profile", 409)
    return goal


def _profile_admission_target(params: dict, code_target: dict, delivery_goal: str, pinned: dict) -> None:
    if params.get("agent") != pinned["agent"]:
        raise _bad("PM_WORK_PROFILE_TARGET_MISMATCH", "agent does not match the work profile", 409)
    if pinned["model"] is not None and params.get("model") != pinned["model"]:
        raise _bad("PM_WORK_PROFILE_TARGET_MISMATCH", "model does not match the work profile", 409)
    if pinned["delivery_goal"] is not None and delivery_goal != pinned["delivery_goal"]:
        raise _bad("PM_WORK_PROFILE_TARGET_MISMATCH", "delivery does not match the work profile", 409)
    _profile_code_target(code_target, pinned)


async def preview(
    ops,
    principal: Principal,
    workspace_id: str,
    work_item_id: str,
    body: dict | None = None,
) -> dict:
    """Read-only start preview: resolve candidate bindings, default branch and SHA, package plan, readiness and preconditions."""
    body = {} if body is None else body
    if not isinstance(body, dict):
        raise _bad("INVALID_PARAMS", "start preview accepts code_target, delivery, requirement_ids and instruction", 422)
    allowed = {"code_target", "delivery", "requirement_ids", "instruction"}
    if "profile_id" in body or "profile_revision" in body:
        allowed |= {"profile_id", "profile_revision"}
    if set(body) - allowed:
        raise _bad("INVALID_PARAMS", "start preview accepts code_target, delivery, requirement_ids and instruction", 422)
    if ("profile_id" in body) != ("profile_revision" in body):
        raise _bad("INVALID_PARAMS", "profile_id and profile_revision are required together", 422)
    profile_requested = "profile_id" in body
    if not profile_requested:
        delivery_goal = body.get("delivery") if body.get("delivery") is not None else "pull_request"
        if not isinstance(delivery_goal, str) or delivery_goal not in DELIVERY_GOALS:
            raise _bad("INVALID_PARAMS", "delivery must be pull_request or reviewable_changes", 422)
    raw_target = body.get("code_target") if body.get("code_target") is not None else {}
    if not isinstance(raw_target, dict) or set(raw_target) - set(CODE_TARGET_KEYS):
        raise _bad("INVALID_PARAMS", "code_target may only contain repository, host, workspace_id and source_ref", 422)
    for k, v in raw_target.items():
        if v is not None and (not isinstance(v, str) or not v.strip() or len(v) > 256):
            raise _bad("INVALID_PARAMS", f"code_target.{k} must be a non-empty string", 422)
    if raw_target.get("source_ref") is not None:
        repository_sync.check_ref(raw_target["source_ref"].strip())
    if body.get("instruction") is not None:
        instruction_override = pm_plans._text(body.get("instruction"), "instruction", 4000)
        if not _bind_rationale(instruction_override):
            raise _bad("INVALID_PARAMS", "instruction must contain non-whitespace characters", 422)
    else:
        instruction_override = None

    profile = None
    with snapshots._snapshot(ops.db):
        (
            work,
            project,
            binding,
            current_plan,
            plan_ref,
            latest_package_id,
            requirement_ids,
            bind_needed,
        ) = _load_preview_local(ops, principal, workspace_id, work_item_id, body)
        if profile_requested:
            from .pm_work_profiles import pinned_revision
            profile = pinned_revision(
                ops.db, principal, workspace_id, body.get("profile_id"), body.get("profile_revision"),
            )
            _profile_code_target(raw_target, profile)
            delivery_goal = _delivery_for_profile(body, profile["delivery_goal"])
        proj_repos = {r.lower() for r in project["repositories"]}
        all_candidates = [
            b for b in repository_sync.capabilities(ops) if b["repository"].lower() in proj_repos
        ]
        matching = [
            b
            for b in all_candidates
            if (not raw_target.get("repository") or b["repository"].lower() == raw_target["repository"].strip().lower())
            and (not raw_target.get("host") or b["host"] == raw_target["host"].strip())
            and (not raw_target.get("workspace_id") or b["workspace_id"] == raw_target["workspace_id"].strip())
        ]
        binding_state = {
            "bound": not bind_needed,
            "bind_needed": bind_needed,
            "binding_id": binding["binding_id"] if binding else None,
            "plan_revision_id": current_plan["revision_id"],
            "scope_revision": plan_ref["scope_revision"],
            "requirement_ids": requirement_ids,
        }
        if len(matching) != 1:
            return {
                "workspace_id": workspace_id,
                "work_item_id": work_item_id,
                "code_target": None,
                "candidates": matching,
                "source": None,
                "binding": binding_state,
                "package_plan": None,
                "readiness": {
                    "status": "blocked",
                    "checks": [
                        {
                            "check": "code_target",
                            "status": "blocked",
                            "detail": (
                                "Multiple repository bindings match this project; select an explicit code_target."
                                if len(matching) > 1
                                else "No configured repository binding matches this work item's project."
                            ),
                        }
                    ],
                },
                "preconditions": None,
                "observed_at": time.time(),
            }
        selected_target = {k: matching[0][k] for k in ("repository", "host", "workspace_id")}
        config = repository_sync.config_snapshot(ops, selected_target)

    if raw_target.get("source_ref") is not None:
        source_ref = raw_target["source_ref"].strip()
        repository_sync.check_ref(source_ref)
    else:
        repo_doc = await repository_sync.gh_read(ops.context["github"].repository(config["repository"]))
        default_branch = repo_doc.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch.strip():
            raise _bad("REPOSITORY_SOURCE_UNAVAILABLE", "GitHub repository default_branch is unavailable", 409)
        source_ref = f"refs/heads/{default_branch.strip()}"
        repository_sync.check_ref(source_ref)

    src_preview = await repository_sync.source_preview(ops, {**selected_target, "source_ref": source_ref})
    full_code_target = {
        "repository": src_preview["target"]["repository"],
        "host": src_preview["target"]["host"],
        "workspace_id": src_preview["target"]["workspace_id"],
        "source_ref": src_preview["source_ref"],
    }
    source = {
        "source_ref": src_preview["source_ref"],
        "source_sha": src_preview["source_sha"],
        "repository_id": src_preview["repository_id"],
        "binding_digest": src_preview["binding_digest"],
    }
    code_source = {
        **full_code_target,
        "source_sha": source["source_sha"],
        "repository_id": source["repository_id"],
        "binding_digest": source["binding_digest"],
    }

    with snapshots._snapshot(ops.db):
        (
            work,
            project,
            binding,
            current_plan,
            plan_ref,
            latest_package_id,
            requirement_ids,
            bind_needed,
        ) = _load_preview_local(ops, principal, workspace_id, work_item_id, body)
        instruction_text = (
            instruction_override
            or (work.get("request") or work.get("goal") or work["title"] or "Start work").strip()[:4000]
            or "Start work"
        )
        package_plan, readiness = evaluate_package_plan(
            ops,
            principal,
            workspace_id,
            work,
            project,
            binding,
            current_plan,
            plan_ref,
            requirement_ids,
            code_source,
            delivery_goal,
            instruction_text,
        )
        binding_state = {
            "bound": not bind_needed,
            "bind_needed": bind_needed,
            "binding_id": binding["binding_id"] if binding else None,
            "plan_revision_id": current_plan["revision_id"],
            "scope_revision": plan_ref["scope_revision"],
            "requirement_ids": requirement_ids,
        }
        preconditions = {
            "expected_work_item_fingerprint": work_items.fingerprint(work),
            "expected_work_version": work["version"],
            "expected_binding_id": binding["binding_id"] if binding else None,
            "expected_scope_revision": plan_ref["scope_revision"],
            "expected_previous_package_id": latest_package_id,
            "expected_project_version": project["version"],
            "repository_id": source["repository_id"],
            "binding_digest": source["binding_digest"],
            "expected_source_sha": source["source_sha"],
            "expected_latest_attempt_id": latest_attempt_id(ops.db, workspace_id, work_item_id),
        }
        if profile is not None:
            preconditions["expected_profile_revision"] = profile["revision"]
            preconditions["expected_profile_digest"] = profile["digest"]
        response = {
            "workspace_id": workspace_id,
            "work_item_id": work_item_id,
            "code_target": full_code_target,
            "candidates": matching,
            "source": source,
            "binding": binding_state,
            "package_plan": package_plan,
            "readiness": readiness,
            "preconditions": preconditions,
            "observed_at": time.time(),
        }
        if profile is not None:
            response["profile"] = {
                "profile_id": profile["profile_id"],
                "revision": profile["revision"],
                "digest": profile["digest"],
                "delivery_goal": profile["delivery_goal"],
            }
        return response


def capabilities(ops=None) -> dict:
    avail = True if ops is None else ACTION in ops.actions
    return {
        "version": 1,
        "supported": avail,
        "available": avail,
        "action": ACTION,
        "preview_read": PREVIEW,
        "attempts_reads": [ATTEMPTS_LIST, ATTEMPT_GET],
        "reads": [PREVIEW, ATTEMPTS_LIST, ATTEMPT_GET],
        "mcp_tools": [PREVIEW, ATTEMPTS_LIST, ATTEMPT_GET],
        "routes": {
            "preview": "/api/v1/pm/workspaces/{workspace_id}/work/{work_item_id}/start-preview",
            "attempts_list": "/api/v1/pm/workspaces/{workspace_id}/work/{work_item_id}/attempts",
            "attempt_get": "/api/v1/pm/workspaces/{workspace_id}/work-attempts/{attempt_id}",
        },
        "composition_profile": COMPOSITION_PROFILE,
        "required_scopes": sorted(REQUIRED_SCOPES),
        "child_actions": ["pm.work.bind", "pm.work_package.create", "repository.continue"],
        "delivery_goals": sorted(DELIVERY_GOALS),
        "agents": sorted(AGENTS),
        "existing_sessions": "continue_under_original_authority",
    }


def _attempt_view(ops, row: dict) -> dict:
    op_id = row["start_operation_id"] or row["origin_id"]
    parent = _op(ops.db, op_id)
    intent = _intent(parent)
    children = _children(ops, parent)
    bind_child = children.get("bind")
    pkg_child = children.get("package")
    handoff_child = children.get("handoff")
    res = parent.get("result") if isinstance(parent.get("result"), dict) else {}

    binding_id = res.get("binding_id")
    if binding_id is None:
        if bind_child and bind_child.get("status") == "succeeded" and isinstance(bind_child.get("result"), dict):
            binding_id = bind_child["result"].get("binding_id")
        elif not intent.get("bind_needed"):
            binding_id = intent.get("expected_binding_id")

    package_id = res.get("package_id")
    if package_id is None:
        if pkg_child and pkg_child.get("status") == "succeeded" and isinstance(pkg_child.get("result"), dict):
            package_id = pkg_child["result"].get("package_id")
        else:
            package_id = intent["package_plan"].get("package_id")

    session = res.get("session") or _child_session(handoff_child)
    execution_operation_id = res.get("execution_operation_id")
    if execution_operation_id is None and handoff_child is not None:
        execution_operation_id = handoff_child["operation_id"]

    if parent["status"] == "cancelled":
        stage = "cancelled"
    elif isinstance(res.get("stage"), str):
        stage = res["stage"]
    elif parent["status"] == "succeeded":
        stage = "started"
    elif parent["status"] == "failed":
        stage = "start_failed"
    elif handoff_child is not None:
        stage = "start_unknown" if _uncertain(ops, handoff_child) else "starting"
    elif package_id is not None:
        stage = "package_prepared"
    else:
        stage = "preparing"

    return {
        "sequence": row["sequence"],
        "seq": row["sequence"],
        "attempt_id": row["attempt_id"],
        "workspace_id": row["workspace_id"],
        "work_item_id": row["work_item_id"],
        "project_id": intent.get("project_id"),
        "origin_kind": row["origin_kind"],
        "origin_id": row["origin_id"],
        "start_operation_id": row["start_operation_id"],
        "predecessor_attempt_id": row["predecessor_attempt_id"],
        "admission_digest": row["admission_digest"],
        "created_at": row["created_at"],
        "status": parent["status"],
        "operation_status": parent["status"],
        "error_code": parent.get("error_code"),
        "status_reason": parent.get("status_reason"),
        "stage": stage,
        "composition_profile": res.get("composition_profile") or intent["package_plan"].get("composition_profile"),
        "binding_id": binding_id,
        "package_id": package_id,
        "package_reused": bool(res.get("package_reused", intent["package_plan"].get("package_reused"))),
        "code_source": res.get("code_source") or intent.get("code_source"),
        "delivery": res.get("delivery") or intent.get("delivery"),
        "children": {
            "bind": bind_child["operation_id"] if bind_child else None,
            "package": pkg_child["operation_id"] if pkg_child else None,
            "handoff": handoff_child["operation_id"] if handoff_child else None,
        },
        "execution_operation_id": execution_operation_id,
        "execution_operation": handoff_child,
        "session": session,
        "existing_sessions": "continue_under_original_authority",
    }


@snapshots._snapshot_read
def get_attempt(ops, principal: Principal, workspace_id: str, attempt_id: str) -> dict:
    if not principal.allows("observe"):
        raise _bad("FORBIDDEN", "Observing work attempts requires observe scope", 403)
    pm_workspace.require_member(ops.db, principal, workspace_id, write=False)
    if not isinstance(attempt_id, str) or not ATTEMPT_ID.fullmatch(attempt_id):
        raise _bad("INVALID_PARAMS", "attempt_id must be a valid att_ identifier", 422)
    if not installed(ops.db):
        raise _bad("NOT_FOUND", "Work attempt not found", 404)
    row = ops.db.execute(
        "SELECT * FROM work_attempts WHERE workspace_id=? AND attempt_id=?",
        (workspace_id, attempt_id),
    ).fetchone()
    if row is None:
        raise _bad("NOT_FOUND", "Work attempt not found", 404)
    op_id = row["start_operation_id"] or row["origin_id"]
    if not operation_visible(ops.db, principal, {"operation_id": op_id}):
        raise _bad("NOT_FOUND", "Work attempt not found", 404)
    view = _attempt_view(ops, dict(row))
    for child_id in view["children"].values():
        if child_id is not None:
            pm_source_lineage.require(ops.db, principal, "operation", child_id)
    return {"workspace_id": workspace_id, "attempt": view}


@snapshots._snapshot_read
def list_attempts(
    ops,
    principal: Principal,
    workspace_id: str,
    work_item_id: str,
    *,
    after: int = 0,
    limit: int = 50,
) -> dict:
    if not principal.allows("observe"):
        raise _bad("FORBIDDEN", "Observing work attempts requires observe scope", 403)
    pm_workspace.require_member(ops.db, principal, workspace_id, write=False)
    if not isinstance(work_item_id, str) or not work_items.WORK_ITEM_ID.fullmatch(work_item_id):
        raise _bad("INVALID_PARAMS", "work_item_id must identify a Connector work item", 422)
    if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 100:
        raise _bad("INVALID_PARAMS", "Use a signed 64-bit cursor and limit 1-100", 422)
    pm_source_lineage.require(ops.db, principal, "work_item", work_item_id)
    work_items._get_item(ops.db, work_item_id, active=True)
    result = {
        "workspace_id": workspace_id,
        "work_item_id": work_item_id,
        "attempts": [],
        "next_cursor": None,
        "visibility_filtered": False,
    }
    if not installed(ops.db):
        return result
    rows = ops.db.execute(
        """SELECT * FROM work_attempts
        WHERE workspace_id=? AND work_item_id=? AND sequence>?
        ORDER BY sequence ASC LIMIT ?""",
        (workspace_id, work_item_id, after, limit + 1),
    ).fetchall()
    scanned = after
    for row in rows[:limit]:
        row_dict = dict(row)
        op_id = row_dict["start_operation_id"] or row_dict["origin_id"]
        if not operation_visible(ops.db, principal, {"operation_id": op_id}):
            result["visibility_filtered"] = True
            scanned = row_dict["sequence"]
            continue
        item = _attempt_view(ops, row_dict)
        child_visible = True
        for child_id in item["children"].values():
            if child_id is not None:
                try:
                    pm_source_lineage.require(ops.db, principal, "operation", child_id)
                except OperationError:
                    child_visible = False
                    break
        if not child_visible:
            result["visibility_filtered"] = True
            scanned = row_dict["sequence"]
            continue
        result["attempts"].append(item)
        scanned = row_dict["sequence"]
    result["next_cursor"] = scanned if len(rows) > limit else None
    return result


