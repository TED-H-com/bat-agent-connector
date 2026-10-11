"""Adopt bounded maintenance intent against a real, fixed publication baseline.

This module records policy, not a second execution engine. A scheduler or repair
must separately enforce its current revision, source coverage, budget and the
existing operation permissions before starting any work.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import controller_authority, pm_lifecycle_gates, pm_workspace
from . import pm_release_baselines as baselines
from .operations import ActionDef, OperationError, OperationService

ACTION = "pm.maintenance_policy.adopt"
POLICY = re.compile(r"pmmp_[0-9a-f]{32}")
SOURCES = frozenset({"issues", "pull_requests", "dependencies", "documentation", "research"})
WORK_KINDS = frozenset({"dependency_fix", "bug_fix", "documentation"})
DELIVERY = ("report", "pull_request", "merge", "deploy")
MAX_HISTORY = 256


def _bad(message, code="INVALID_PARAMS", status=422):
    return OperationError(code, message, status)


def _exact(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise _bad("Use the exact maintenance policy fields")


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise _bad(f"{label} must be an integer from {low} to {high}")


def _text(value, maximum, label):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
        raise _bad(f"{label} requires 1–{maximum} characters")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _bad(f"{label} must be UTF-8") from None


def _selection(value, allowed, label, *, empty=False):
    if (not isinstance(value, list) or len(value) > len(allowed) or not value and not empty
            or any(not isinstance(item, str) or item not in allowed for item in value)
            or len(value) != len(set(value))):
        raise _bad(f"Select distinct supported {label}")


def validate_policy(policy, baseline):
    """No caller-provided low-risk label, controller, authority or completeness."""
    _exact(policy, {"enabled", "after_release_policy", "sources", "allowed_work_kinds", "allowed_paths",
                    "delivery", "observation", "schedule", "limits", "verification"})
    if (type(policy["enabled"]) is not bool or not isinstance(policy["after_release_policy"], str) or policy["after_release_policy"] not in {
            "maintain_when_idle", "continue_development", "manual"}):
        raise _bad("Select an enabled state and explicit after-release behavior")
    _selection(policy["sources"], SOURCES, "maintenance sources")
    _selection(policy["allowed_work_kinds"], WORK_KINDS, "work kinds", empty=True)
    paths = policy["allowed_paths"]
    if not isinstance(paths, list) or not 1 <= len(paths) <= 100 or any(not isinstance(p, str) for p in paths):
        raise _bad("Select 1–100 repository-relative paths or directory prefixes")
    if len(set(paths)) != len(paths):
        raise _bad("Repository paths must be distinct")
    for path in paths:
        _text(path, 512, "Repository path")
        if path == ".":
            continue  # Explicit whole repository, still confined to an owned checkout.
        parts = path.removesuffix("/").split("/")
        if (any(part in {"", ".", ".."} for part in parts) or any(c in path for c in "\\:*?[]")
                or any(ord(c) < 32 for c in path)):
            raise _bad("Use exact relative files or trailing-slash directories; wildcards are unsupported")
    delivery = policy["delivery"]
    _exact(delivery, {"maximum", "environment"})
    if delivery["maximum"] not in DELIVERY:
        raise _bad("Select the maximum permitted delivery action")
    if delivery["maximum"] == "deploy":
        _text(delivery["environment"], 256, "Deployment environment")
    elif delivery["environment"] is not None:
        raise _bad("Only a deployment policy selects an environment")
    maintenance = baseline["document"]["proof"]["maintenance_branch"]
    if policy["allowed_work_kinds"]:
        if delivery["maximum"] == "report" or maintenance.get("status") != "verified_at_observation":
            raise _bad("Automated repairs need a verified maintenance branch and at least a pull-request ceiling")
    observation = policy["observation"]
    _exact(observation, {"max_age_seconds", "stable_for_seconds"})
    _integer(observation["max_age_seconds"], 1, 86400, "Observation age")
    _integer(observation["stable_for_seconds"], 0, 604800, "Stable observation period")
    schedule = policy["schedule"]
    _exact(schedule, {"timezone", "check_interval_seconds", "review_weekday", "review_local_time"})
    _text(schedule["timezone"], 128, "Schedule time zone")
    try:
        ZoneInfo(schedule["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        raise _bad("Select an available IANA time zone") from None
    _integer(schedule["check_interval_seconds"], 60, 2678400, "Check interval")
    _integer(schedule["review_weekday"], 0, 6, "Review weekday")
    if not isinstance(schedule["review_local_time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule["review_local_time"]):
        raise _bad("Review time must be an exact local HH:MM")
    limits = policy["limits"]
    _exact(limits, {"max_parallel", "run_seconds", "period_seconds", "period_runs", "cost_microusd", "retries"})
    for key, low, high in (("max_parallel", 1, 16), ("run_seconds", 1, 86400), ("period_seconds", 60, 2678400),
                           ("period_runs", 1, 1000), ("retries", 0, 10)):
        _integer(limits[key], low, high, key)
    if limits["cost_microusd"] is not None:
        _integer(limits["cost_microusd"], 1, 10**12, "Cost budget in millionths of USD")
    verification = policy["verification"]
    _exact(verification, {"required_ci", "review_required", "acceptance_evidence"})
    if type(verification["required_ci"]) is not bool or type(verification["review_required"]) is not bool:
        raise _bad("CI and review requirements must be explicit booleans")
    evidence = verification["acceptance_evidence"]
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 40:
        raise _bad("Select 1–40 required acceptance evidence descriptions")
    for item in evidence:
        _text(item, 2000, "Acceptance evidence")
    # An upper bound is not an override of the repository's required checks.
    return json.loads(baselines._json(policy))


def schema(journal):
    baselines.schema(journal)
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_maintenance_policies (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, policy_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, revision INTEGER NOT NULL,
            previous_policy_id TEXT UNIQUE, baseline_id TEXT NOT NULL,
            document TEXT NOT NULL, document_digest TEXT NOT NULL,
            actor TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE,
            UNIQUE(workspace_id,revision))""")
        for verb in ("UPDATE", "DELETE"):
            journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS pm_maintenance_policies_{verb.lower()}_immutable "
                f"BEFORE {verb} ON pm_maintenance_policies BEGIN SELECT RAISE(ABORT, 'Maintenance policy history is immutable'); END")


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "manage", "Adopt bounded maintenance policy without starting work", _run,
                               _admit, ("workspace_id",), authorize_existing=_authorize_existing))


def capabilities():
    return {"version": 1, "sources": sorted(SOURCES), "work_kinds": sorted(WORK_KINDS),
            "policy_adoption": True, "scheduler_available": False, "repair_dispatch_available": False,
            "after_release_policies": ["maintain_when_idle", "continue_development", "manual"]}


def head(db, wid):
    row = db.execute("SELECT policy_id,revision FROM pm_maintenance_policies WHERE workspace_id=? ORDER BY revision DESC LIMIT 1", (wid,)).fetchone()
    return dict(row) if row else {"policy_id": None, "revision": 0}


def _record(db, wid, policy_id):
    if not isinstance(policy_id, str) or not POLICY.fullmatch(policy_id):
        raise _bad("Maintenance policy not found", "NOT_FOUND", 404)
    row = db.execute("SELECT * FROM pm_maintenance_policies WHERE workspace_id=? AND policy_id=?", (wid, policy_id)).fetchone()
    if row is None:
        raise _bad("Maintenance policy not found", "NOT_FOUND", 404)
    try:
        doc = json.loads(row["document"])
        if baselines.sources.digest(doc) != row["document_digest"] or any(doc[key] != row[key] for key in (
                "policy_id", "workspace_id", "revision", "previous_policy_id", "baseline_id", "operation_id")):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise _bad("The original policy cannot be verified", "PM_MAINTENANCE_POLICY_CORRUPT", 409) from None
    return {**dict(row), "document": doc}


def _access(db, principal, wid, record):
    pm_workspace.require_member(db, principal, wid)
    reader = baselines._Reader(db)
    seen = set()
    while record:
        if record["policy_id"] in seen or len(seen) >= MAX_HISTORY:
            raise _bad("Policy history cannot be verified", "PM_MAINTENANCE_POLICY_HISTORY_LIMIT", 409)
        seen.add(record["policy_id"])
        source = baselines._record(db, wid, record["baseline_id"])
        if source["document_digest"] != record["document"]["baseline_digest"]:
            raise _bad("Original baseline source changed", "NOT_FOUND", 404)
        baselines._access(reader, principal, wid, source)
        previous = record["previous_policy_id"]
        record = _record(db, wid, previous) if previous else None
    return len(seen)


def _check(ops, principal, target, params, pre):
    _exact(target, {"workspace_id"})
    _exact(params, {"policy", "rationale"})
    _exact(pre, {"expected_policy_revision", "expected_previous_policy_id", "expected_baseline_id", "expected_lifecycle_revision"})
    wid = target["workspace_id"]
    baselines._member(ops, principal, wid, decide=True)
    _text(params["rationale"], 4000, "Policy decision rationale")
    _integer(pre["expected_policy_revision"], 0, 2**63 - 2, "Policy revision")
    _integer(pre["expected_lifecycle_revision"], 0, 2**63 - 2, "Lifecycle revision")
    current = head(ops.db, wid)
    if current != {"policy_id": pre["expected_previous_policy_id"], "revision": pre["expected_policy_revision"]}:
        raise _bad("Read the current policy before adopting a revision", "PM_MAINTENANCE_POLICY_CHANGED", 409)
    if pm_lifecycle_gates.head(ops.db, wid)["revision"] != pre["expected_lifecycle_revision"]:
        raise _bad("Read the current lifecycle before adopting policy", "VERSION_CONFLICT", 409)
    if current["policy_id"] and _access(ops.db, principal, wid, _record(ops.db, wid, current["policy_id"])) >= MAX_HISTORY:
        raise _bad("Policy history cannot be extended", "PM_MAINTENANCE_POLICY_HISTORY_LIMIT", 409)
    baseline = baselines._record(ops.db, wid, pre["expected_baseline_id"])
    if baselines._head(ops.db, wid)["baseline_id"] != baseline["baseline_id"]:
        raise _bad("Select the current primary release baseline", "PM_BASELINE_VERSION_CONFLICT", 409)
    baselines._access(ops, principal, wid, baseline, decide=True)
    policy = validate_policy(params["policy"], baseline)
    # The adopting owner's actual server-bound controller is authoritative.
    # An arbitrary role/controller string in a request can never become a worker.
    authority = controller_authority.binding(ops.db, principal, pm_workspace_id=wid)
    doc = baseline["document"]
    return {"workspace_id": wid, "baseline_id": baseline["baseline_id"], "baseline_digest": baseline["document_digest"],
            "previous_policy_id": current["policy_id"], "previous_revision": current["revision"],
            "lifecycle_revision": pre["expected_lifecycle_revision"], "policy": policy, "rationale": params["rationale"],
            "scope": _scope(ops, doc),
            "controller": {key: authority[key] for key in ("server_id", "controller_id", "actor")}}


def _bat_bindings(ops, repository):
    config = ops.context["github_config"].repos[repository.lower()]
    fleet = ops.context.get("fleet")
    hosts = fleet.config.hosts if fleet else {}
    result = []
    for host, workspace_id in sorted(config.sync.bindings if config.sync else ()):
        hc = hosts.get(host)
        profile_id = hc.profile_id if hc else None
        # Same immutable observation scope used by inventory._binding; no URL,
        # fingerprint, credentials or local checkout path appears in the record.
        binding_digest = hashlib.sha256(json.dumps([hc.url, hc.fingerprint, profile_id],
            sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest() if hc else None
        result.append({"host": host, "workspace_id": workspace_id, "profile_id": profile_id, "binding_digest": binding_digest})
    return result


def _scope(ops, document):
    if baselines.sources.configured(ops, document["repository"]) != document["intent"]["config"]:
        raise _bad("The baseline's original provider configuration changed", "PM_MAINTENANCE_SCOPE_CHANGED", 409)
    return {"repository": document["repository"], "repository_id": document["repository_id"], "channel": document["channel"],
            "project_binding": document["intent"]["project_binding"], "maintenance_branch": document["proof"]["maintenance_branch"],
            "provider_digest": baselines.sources.digest(document["intent"]["config"]),
            "bat_workspaces": _bat_bindings(ops, document["repository"]), "github_scope": "repository", "central_project": True}


def _admit(ops, principal, target, params, pre):
    with ops.journal.tx():
        intent = _check(ops, principal, target, params, pre)
    return {"principal": pm_workspace.principal_binding(principal), "maintenance_policy_intent": intent,
            "maintenance_policy_intent_digest": baselines.sources.digest(intent)}


def operation_visible(db, principal, operation):
    with baselines._snapshot(db):
        row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation.get("operation_id"),)).fetchone()
        if row is None or row["action"] != ACTION:
            return False
        op = OperationService._decode(row)
        binding = (op.get("external_refs") or {}).get("admission_binding") or {}
        intent = binding.get("maintenance_policy_intent")
        try:
            if baselines.sources.digest(intent) != binding.get("maintenance_policy_intent_digest"):
                return False
            wid = op["target"]["workspace_id"]
            pm_workspace.require_member(db, principal, wid)
            baseline = baselines._record(db, wid, intent["baseline_id"])
            if baseline["document_digest"] != intent["baseline_digest"]:
                return False
            baselines._access(baselines._Reader(db), principal, wid, baseline)
            if intent["previous_policy_id"]:
                _access(db, principal, wid, _record(db, wid, intent["previous_policy_id"]))
            return True
        except (OperationError, TypeError, KeyError, ValueError):
            return False


def _authorize_existing(ops, principal, op, verb):
    if verb == "replay":
        pm_workspace.require_member(ops.db, principal, op["target"]["workspace_id"])
    else:
        baselines._member(ops, principal, op["target"]["workspace_id"], decide=True)
    if not principal.allows("manage") or not operation_visible(ops.db, principal, op):
        raise _bad("Original maintenance policy operation unavailable", "NOT_FOUND", 404)


async def _run(ctx):
    def record():
        intent = _check(ctx.service, pm_workspace.admitted_principal(ctx), ctx.target, ctx.params, ctx.preconditions)
        binding = ctx.admission_binding or {}
        if (intent != binding.get("maintenance_policy_intent")
                or baselines.sources.digest(intent) != binding.get("maintenance_policy_intent_digest")):
            raise _bad("The original policy context changed", "PM_MAINTENANCE_POLICY_CHANGED", 409)
        policy_id, now = "pmmp_" + ctx.operation_id[3:], time.time()
        document = {"schema_version": 1, **intent, "policy_id": policy_id, "operation_id": ctx.operation_id,
                    "revision": intent["previous_revision"] + 1, "created_at": now,
                    "automatic_execution_started": False, "scope_adoption": "maintenance_only"}
        baselines._bounded(document)
        digest = baselines.sources.digest(document)
        ctx.service.db.execute("""INSERT INTO pm_maintenance_policies(policy_id,workspace_id,revision,previous_policy_id,
            baseline_id,document,document_digest,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (policy_id, intent["workspace_id"], document["revision"], intent["previous_policy_id"], intent["baseline_id"],
             baselines._json(document), digest, ctx.actor, now, ctx.operation_id))
        result = {"workspace_id": intent["workspace_id"], "policy_id": policy_id, "revision": document["revision"],
                  "document_digest": digest, "automatic_execution_started": False}
        ctx.service.journal.api_event("pm_maintenance_policy", policy_id, "pm.maintenance_policy.adopted",
                                      {**result, "operation_id": ctx.operation_id}, actor=ctx.actor)
        return result
    return ctx.effect("pm.maintenance_policy.record", record)


def fixed_record(ops, principal, workspace_id, policy_id):
    """Internal original-source view; newer policy/baseline heads do not hide it.

    Callers enforce their entry's scope. This visibility helper permits admitted
    manage-only result recovery and never grants observe, start or extra roles.
    """
    with baselines._snapshot(ops.db):
        record = _record(ops.db, workspace_id, policy_id)
        _access(ops.db, principal, workspace_id, record)
        doc = record["document"]
        observation = doc["policy"]["observation"]
        return {"policy_id": policy_id, "version": record["revision"], "baseline_id": record["baseline_id"],
                "adopted_baseline_id": record["baseline_id"], "baseline_digest": doc["baseline_digest"],
                "document_digest": record["document_digest"], "scope": doc["scope"], "controller": doc["controller"],
                "policy": doc["policy"], "observation": {"max_age_seconds": observation["max_age_seconds"],
                                                         "stability_seconds": observation["stable_for_seconds"]}}


def active_record(ops, principal, workspace_id):
    """Current adopted policy and compatible current release, not execution authority.

    A verified patch in the same adopted channel/branch need not ask the owner to
    adopt the same maintenance policy again. New repository, scope or branch does.
    """
    with baselines._snapshot(ops.db):
        policy_id = head(ops.db, workspace_id)["policy_id"]
        current = fixed_record(ops, principal, workspace_id, policy_id)
        bid = baselines._head(ops.db, workspace_id)["baseline_id"]
        baseline = baselines._record(ops.db, workspace_id, bid)
        baselines._access(ops, principal, workspace_id, baseline)
        observed = _scope(ops, baseline["document"])
        adopted = current["scope"]
        fields = ("repository", "repository_id", "channel", "project_binding", "bat_workspaces", "github_scope", "central_project", "provider_digest")
        if any(observed[key] != adopted[key] for key in fields):
            raise _bad("Current release or workspace coverage is outside the adopted policy", "PM_MAINTENANCE_SCOPE_CHANGED", 409)
        original_branch, new_branch = adopted["maintenance_branch"], observed["maintenance_branch"]
        if (original_branch.get("status") != new_branch.get("status")
                or original_branch.get("ref") != new_branch.get("ref")):
            raise _bad("The current release uses another maintenance branch", "PM_MAINTENANCE_SCOPE_CHANGED", 409)
        return {**current, "baseline_id": bid, "baseline_digest": baseline["document_digest"]}


@baselines._snapshot_read
def get_policy(ops, principal, workspace_id, policy_id):
    baselines._member(ops, principal, workspace_id)
    record = _record(ops.db, workspace_id, policy_id)
    _access(ops.db, principal, workspace_id, record)
    return baselines._bounded({"workspace_id": workspace_id, "policy": record})


@baselines._snapshot_read
def current_policy(ops, principal, workspace_id):
    baselines._member(ops, principal, workspace_id)
    current = head(ops.db, workspace_id)
    record, filtered = None, False
    if current["policy_id"]:
        try:
            record = get_policy(ops, principal, workspace_id, current["policy_id"])["policy"]
        except OperationError as exc:
            if exc.status not in (403, 404):
                raise
            filtered = True
    return baselines._bounded({"workspace_id": workspace_id, "head": {**current, "policy_id": None if filtered else current["policy_id"]},
                              "policy": record, "visibility_filtered": filtered, "capabilities": capabilities()})


@baselines._snapshot_read
def list_policies(ops, principal, workspace_id, *, after=0, limit=20):
    baselines._member(ops, principal, workspace_id)
    _integer(after, 0, 2**63 - 1, "Cursor")
    _integer(limit, 1, 100, "Page size")
    rows = ops.db.execute("SELECT policy_id,sequence FROM pm_maintenance_policies WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                          (workspace_id, after, limit + 1)).fetchall()
    result = {"workspace_id": workspace_id, "policies": [], "next_cursor": None, "visibility_filtered": False}
    consumed = after
    for stub in rows[:limit]:
        record = _record(ops.db, workspace_id, stub["policy_id"])
        try:
            _access(ops.db, principal, workspace_id, record)
        except OperationError as exc:
            if exc.status not in (403, 404):
                raise
            result["visibility_filtered"], consumed = True, stub["sequence"]
            continue
        candidate = {**result, "policies": [*result["policies"], record], "next_cursor": stub["sequence"]}
        if baselines._size(candidate) > baselines.MAX_READ_BYTES:
            if consumed == after:
                return baselines._bounded(candidate)
            result["next_cursor"] = consumed
            return baselines._bounded(result)
        result["policies"].append(record)
        consumed = stub["sequence"]
    result["next_cursor"] = consumed if len(rows) > limit else None
    return baselines._bounded(result)
