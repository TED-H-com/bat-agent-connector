"""Direct work start compound action (v1.3 E02, slice P3): work.start and start preview."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time

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
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "needs_attention"})
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
})
PARAMS_REQUIRED = frozenset({"instruction", "agent", "delivery", "code_target"})
PARAMS_OPTIONAL = frozenset({"model", "requirement_ids"})
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


def require_no_active_attempt(db, workspace_id: str, work_item_id: str) -> None:
    """Reject a second concurrent work.start when the Work's latest attempt is not terminal."""
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
    op_row = db.execute("SELECT status FROM operations WHERE operation_id=?", (op_id,)).fetchone()
    if op_row is None or op_row["status"] not in TERMINAL:
        raise _bad(
            "PM_WORK_START_IN_PROGRESS",
            f"Work item already has an active start in progress ({op_id}); follow or settle that operation first",
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
    require_no_active_attempt(db, wid, work_item_id)
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


def _validate_preconditions_shape(pre: dict) -> None:
    if not isinstance(pre, dict) or set(pre) != PRECONDITIONS:
        raise _bad("PRECONDITION_REQUIRED", "work.start requires all nine preview preconditions", 422)
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
        or not isinstance(pre["expected_scope_revision"], str)
        or not re.fullmatch(r"pmscope_[0-9a-f]{32}", pre["expected_scope_revision"])
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
    ):
        raise _bad("PRECONDITION_REQUIRED", "Supply valid preview preconditions for work.start", 422)


def _check_admission(ops, principal: Principal, target: dict, params: dict, pre: dict) -> dict:
    if not isinstance(target, dict) or set(target) != {"workspace_id", "work_item_id"}:
        raise _bad("INVALID_TARGET", "Exact workspace_id and work_item_id are required", 422)
    wid, wi_id = target["workspace_id"], target["work_item_id"]
    if not isinstance(wi_id, str) or not work_items.WORK_ITEM_ID.fullmatch(wi_id):
        raise _bad("INVALID_TARGET", "work_item_id must identify a Connector work item", 422)
    workspace = _member(ops, principal, wid, write=True)
    controller = controller_authority.binding(ops.db, principal, pm_workspace_id=wid)
    require_work_controller(ops.db, wid, wi_id, controller)
    require_no_active_attempt(ops.db, wid, wi_id)
    if (
        not isinstance(params, dict)
        or not PARAMS_REQUIRED <= set(params)
        or set(params) - PARAMS_REQUIRED - PARAMS_OPTIONAL
    ):
        raise _bad("INVALID_PARAMS", "Supply exact work.start parameters", 422)
    _validate_preconditions_shape(pre)
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

    current_plan = pm_plans.accepted_plan(ops, principal, wid)["plan"]
    if current_plan is None:
        raise _bad("PM_ADOPTED_PLAN_REQUIRED", "Starting work requires an explicitly adopted plan", 409)
    plan_ref = pm_work_packages._plan_ref(current_plan)
    if pre["expected_scope_revision"] != plan_ref["scope_revision"]:
        raise _bad("PM_PACKAGE_SCOPE_CHANGED", "Prepare work against the current explicitly adopted scope", 409)

    latest_package_id = pm_work_packages._latest(ops.db, wid, wi_id)
    if pre["expected_previous_package_id"] != latest_package_id:
        raise _bad("PM_PACKAGE_VERSION_CONFLICT", "Read the latest work package before starting", 409)

    requirement_ids, bind_needed = _resolve_requirement_ids(current_plan, binding, params.get("requirement_ids"))
    if bind_needed:
        _member(ops, principal, wid, write=True, decide=True)

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
        "code_source": code_source,
        "code_config_digest": repository_sync.digest(config),
        "delivery": delivery_goal,
        "agent": agent,
        "model": model,
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
        if row is None or row["action"].startswith("pm.") or row["action"] == ACTION:
            raise _bad("NOT_FOUND", "Original source operation not found", 404)
        return OperationService._decode(row)


def _access_intent(ops, principal: Principal, op: dict) -> None:
    intent = _intent(op)
    wid = op["target"]["workspace_id"]
    if not principal.allows("observe"):
        raise _bad("FORBIDDEN", "Observing work.start requires observe", 403)
    workspace = pm_workspace.require_member(ops.db, principal, wid)
    if workspace["server_id"] != intent.get("server_id"):
        raise _bad("PM_WORK_START_SOURCE_UNAVAILABLE", "Original workspace identity is unavailable", 404)
    pm_work_packages._original_plan(ops, principal, wid, intent["plan_ref"])
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


def _handoff_request(parent_id: str, intent: dict, package_ref: dict) -> dict:
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
    return (
        ops.db.execute(
            "SELECT 1 FROM operation_steps s LEFT JOIN pm_lifecycle_steps l "
            "ON l.operation_id=s.operation_id AND l.name=s.name WHERE s.operation_id=? "
            "AND s.status IN ('started','uncertain') AND (l.kind IS NULL OR l.kind='external') LIMIT 1",
            (child["operation_id"],),
        ).fetchone()
        is not None
    )


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
    if not allow_cancelled_parent and (parent["cancel_requested"] or parent["status"] in TERMINAL):
        raise _bad("PM_WORK_START_STOPPED", "The original work.start has stopped new child admission", 409)
    intent = _intent(parent)
    wid = parent["target"]["workspace_id"]
    wi_id = parent["target"]["work_item_id"]
    current_lc = pm_lifecycle_gates.head(ops.db, wid)
    pm_lifecycle_gates._require(current_lc, intent["execution_epoch"])
    principal = _current_principal(ops, parent)
    _access_intent(ops, principal, parent)

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


def _admit_child(ctx, slot: str, request: dict) -> dict:
    def admit():
        parent = _op(ctx.service.db, ctx.operation_id)
        principal = _guard(ctx.service, parent)
        child, fresh = ctx.service.create(principal, **request, entry="work-start")
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
    return {
        "attempt_id": "att_" + parent["operation_id"][3:],
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
                _save_progress(ctx, _build_state(parent, intent, children, stage="start_failed"))
                raise NeedsAttention(exc.code, _failure_message(exc.code, exc.message)) from exc
            children = _children(ctx.service, parent)
            bind_child = children["bind"]
        if bind_child["status"] not in TERMINAL:
            _save_progress(ctx, _build_state(parent, intent, children, stage="preparing"))
            raise Wait("waiting_external", "Waiting for work binding child operation", delay_s=1.0)
        if bind_child["status"] != "succeeded":
            _save_progress(ctx, _build_state(parent, intent, children, stage="start_failed"))
            code = bind_child["error_code"] or "PM_WORK_BIND_FAILED"
            raise NeedsAttention(code, _failure_reason(bind_child, "Work binding did not complete"))
        binding_id = (bind_child.get("result") or {}).get("binding_id")
        if not isinstance(binding_id, str):
            _save_progress(ctx, _build_state(parent, intent, children, stage="start_failed"))
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
                _save_progress(
                    ctx,
                    _build_state(parent, intent, children, stage="start_failed", binding_id=binding_id),
                )
                raise NeedsAttention(exc.code, _failure_message(exc.code, exc.message)) from exc
            children = _children(ctx.service, parent)
            pkg_child = children["package"]
        if pkg_child["status"] not in TERMINAL:
            _save_progress(
                ctx,
                _build_state(parent, intent, children, stage="preparing", binding_id=binding_id),
            )
            raise Wait("waiting_external", "Waiting for work package creation child operation", delay_s=1.0)
        if pkg_child["status"] != "succeeded":
            _save_progress(
                ctx,
                _build_state(parent, intent, children, stage="start_failed", binding_id=binding_id),
            )
            code = pkg_child["error_code"] or "PM_WORK_PACKAGE_FAILED"
            raise NeedsAttention(code, _failure_reason(pkg_child, "Work package creation did not complete"))
        pkg_res = pkg_child.get("result") or {}
        package_id = pkg_res.get("package_id")
        doc_digest = pkg_res.get("document_digest")
        if not isinstance(package_id, str) or not isinstance(doc_digest, str):
            _save_progress(
                ctx,
                _build_state(parent, intent, children, stage="start_failed", binding_id=binding_id),
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
            _save_progress(
                ctx,
                _build_state(
                    parent,
                    intent,
                    children,
                    stage="start_failed",
                    binding_id=binding_id,
                    package_id=package_id,
                ),
            )
            raise NeedsAttention(exc.code, _failure_message(exc.code, exc.message)) from exc
        children = _children(ctx.service, parent)
        handoff_child = children["handoff"]

    session = _child_session(handoff_child)
    is_uncertain = handoff_child["status"] == "uncertain" or _uncertain(ctx.service, handoff_child)
    if handoff_child["status"] not in TERMINAL:
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
        stage = "start_unknown" if is_uncertain else "start_failed"
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
        code = handoff_child["error_code"] or ("UNCERTAIN_UNRESOLVED" if is_uncertain else "PM_HANDOFF_FAILED")
        raise NeedsAttention(code, _failure_reason(handoff_child, "Repository handoff did not complete"))

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
