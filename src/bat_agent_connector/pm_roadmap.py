"""Explicit scheduling baselines, forecasts and fixed-source observations.

Milestone scope comes from selected adopted plans; no input-to-milestone inference,
execution dispatch, automatic dates, or quality acceptance occurs in this module.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from datetime import date

from . import pm_evidence, pm_plans, pm_snapshots, pm_workspace
from .operations import ActionDef, OperationError

MAX_MILESTONES = 100
MAX_WORK_BINDINGS = 500
MAX_OBSERVATION_REFS = 1000
MAX_DOCUMENT_BYTES = 1_000_000
MAX_OBSERVATION_BYTES = 2_000_000
REVISION_ID = re.compile(r"pmr[bfo]_[0-9a-f]{32}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
ACTIONS = {"pm.roadmap.baseline.set": "baseline", "pm.roadmap.forecast.set": "forecast", "pm.roadmap.observe": "observation"}
_PREFIX = {"baseline": "pmrb_", "forecast": "pmrf_", "observation": "pmro_"}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _bad(code, message, status=422):
    return OperationError(code, message, status)


def _only(value, fields):
    if not isinstance(value, dict) or set(value) - set(fields):
        raise _bad("INVALID_PARAMS", "Unexpected Roadmap fields")


def _ids(value, label, *, maximum=100, pattern=pm_plans.ITEM_ID):
    if (not isinstance(value, list) or len(value) > maximum
            or not all(isinstance(item, str) and pattern.fullmatch(item) for item in value)
            or len(set(value)) != len(value)):
        raise _bad("INVALID_PARAMS", f"{label} must contain distinct valid IDs, at most {maximum}")
    return value


def _date(value):
    if value is None:
        return None
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise _bad("INVALID_PARAMS", "Dates must be YYYY-MM-DD calendar dates or null for unknown")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise _bad("INVALID_PARAMS", "The calendar date does not exist") from None
    return value


def schema(journal):
    pm_snapshots.schema(journal)
    with journal.tx():
        for statement in (
            """CREATE TABLE IF NOT EXISTS pm_roadmap_heads (
                workspace_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
                baseline_revision_id TEXT, forecast_revision_id TEXT, observation_revision_id TEXT)""",
            """CREATE TABLE IF NOT EXISTS pm_roadmap_revisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, kind TEXT NOT NULL, previous_revision_id TEXT,
                baseline_revision_id TEXT NOT NULL, document TEXT NOT NULL, document_digest TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at REAL NOT NULL, operation_id TEXT NOT NULL UNIQUE)""",
            "CREATE INDEX IF NOT EXISTS pm_roadmap_history ON pm_roadmap_revisions(workspace_id,sequence)",
            """CREATE TABLE IF NOT EXISTS pm_roadmap_milestones (
                workspace_id TEXT NOT NULL, milestone_id TEXT NOT NULL, first_baseline_revision_id TEXT NOT NULL,
                original_baseline_date TEXT, created_by TEXT NOT NULL, created_at REAL NOT NULL,
                PRIMARY KEY(workspace_id,milestone_id))""",
            "CREATE TABLE IF NOT EXISTS pm_roadmap_applied (operation_id TEXT PRIMARY KEY, result TEXT NOT NULL)",
        ):
            journal.db.execute(statement)
        for table in ("pm_roadmap_revisions", "pm_roadmap_milestones", "pm_roadmap_applied"):
            for verb in ("UPDATE", "DELETE"):
                journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable BEFORE {verb} ON {table} "
                                   "BEGIN SELECT RAISE(ABORT, 'Roadmap history is immutable'); END")


def install(ops):
    schema(ops.journal)
    for action in ACTIONS:
        if action not in ops.actions:
            ops.register(ActionDef(action, "manage", "Record explicit Roadmap scheduling and fixed-source observations", _run,
                                   _admitter(action), ("workspace_id",), authorize_existing=_authorize_existing))


def head(db, workspace_id):
    row = db.execute("SELECT * FROM pm_roadmap_heads WHERE workspace_id=?", (workspace_id,)).fetchone()
    return dict(row) if row else {"workspace_id": workspace_id, "version": 0, "baseline_revision_id": None,
                                 "forecast_revision_id": None, "observation_revision_id": None}


def _member(ops, principal, wid, *, write=False, decide=False):
    if not principal.allows("observe") or write and not principal.allows("manage"):
        raise _bad("FORBIDDEN", "Roadmap reads require observe; changes also require manage", 403)
    return pm_workspace.require_member(ops.db, principal, wid, write=write, decide=decide)


def _revision(db, wid, rid):
    if not isinstance(rid, str) or not REVISION_ID.fullmatch(rid):
        raise _bad("PM_ROADMAP_NOT_FOUND", "Roadmap revision not found", 404)
    row = db.execute("SELECT * FROM pm_roadmap_revisions WHERE workspace_id=? AND revision_id=?", (wid, rid)).fetchone()
    if not row:
        raise _bad("PM_ROADMAP_NOT_FOUND", "Roadmap revision not found", 404)
    out = dict(row)
    out["document"] = json.loads(out["document"])
    if _digest(out["document"]) != out["document_digest"]:
        raise _bad("PM_ROADMAP_CORRUPT", "The fixed Roadmap revision digest does not match", 409)
    return out


def _binding(ops, wid, binding_id):
    row = ops.db.execute("SELECT * FROM pm_work_bindings WHERE workspace_id=? AND binding_id=?", (wid, binding_id)).fetchone()
    if not row:
        raise _bad("PM_WORK_BINDING_NOT_FOUND", "The exact workspace work binding was not found", 404)
    return {**dict(row), "requirement_ids": json.loads(row["requirement_ids"])}


def _scope_refs(milestones):
    return {(item["plan_revision_id"], rid) for item in milestones for rid in item["requirement_ids"]}


def _ref_list(values):
    return [{"plan_revision_id": revision, "requirement_id": requirement} for revision, requirement in sorted(values)]


def _baseline(ops, principal, wid, params, current, rid):
    _only(params, ("title", "rationale", "milestones"))
    title = pm_workspace._text(params.get("title", "Roadmap"), "title", 200)
    rationale = pm_workspace._text(params.get("rationale"), "rationale", 4000)
    raw = params.get("milestones")
    if not isinstance(raw, list) or len(raw) > MAX_MILESTONES:
        raise _bad("INVALID_PARAMS", f"milestones must be an explicit list of at most {MAX_MILESTONES}")
    prior = _revision(ops.db, wid, current["baseline_revision_id"])["document"] if current["baseline_revision_id"] else None
    previous = {m["milestone_id"]: m for m in prior["milestones"]} if prior else {}
    milestones, seen, plans, reintroduced = [], set(), {}, []
    total_bindings = 0
    for item in raw:
        _only(item, ("milestone_id", "title", "plan_revision_id", "requirement_ids", "work_binding_ids", "baseline_date", "depends_on"))
        mid = item.get("milestone_id")
        _ids([mid], "milestone_id")
        if mid in seen:
            raise _bad("INVALID_PARAMS", "Milestone IDs must be unique within the baseline")
        seen.add(mid)
        name = pm_workspace._text(item.get("title"), "milestone title", 200)
        plan_id = item.get("plan_revision_id")
        if not isinstance(plan_id, str) or not pm_plans.REVISION_ID.fullmatch(plan_id):
            raise _bad("INVALID_PARAMS", "Each milestone requires a fixed adopted plan revision")
        if plan_id not in plans:
            plan = pm_plans.get_plan(ops, principal, wid, plan_id)["plan"]
            pm_plans.revision_files(ops.db, wid, plan_id)
            if not plan["adoption"]:
                raise _bad("PM_ADOPTED_PLAN_REQUIRED", "Milestones must refer to a historically adopted plan", 409)
            plans[plan_id] = plan
        plan = plans[plan_id]
        requirements = _ids(item.get("requirement_ids", []), "requirement_ids")
        known = {r["id"] for r in plan["document"]["requirements"]}
        if not set(requirements) <= known:
            raise _bad("INVALID_PARAMS", "Requirement IDs must occur in the milestone's exact plan revision")
        bindings = _ids(item.get("work_binding_ids", []), "work_binding_ids", maximum=MAX_WORK_BINDINGS, pattern=pm_evidence._BINDING)
        total_bindings += len(bindings)
        if total_bindings > MAX_WORK_BINDINGS:
            raise _bad("INVALID_PARAMS", f"The baseline may select at most {MAX_WORK_BINDINGS} work binding references")
        work_ids = set()
        for binding_id in bindings:
            binding = _binding(ops, wid, binding_id)
            if binding["plan_revision_id"] != plan_id or (binding["requirement_ids"] and not set(requirements) & set(binding["requirement_ids"])):
                raise _bad("PM_MILESTONE_BINDING_SCOPE", "A work binding must use this fixed plan and overlap selected requirements", 409)
            if binding["work_item_id"] in work_ids:
                raise _bad("INVALID_PARAMS", "One milestone cannot select two revisions of the same work binding")
            work_ids.add(binding["work_item_id"])
        depends = _ids(item.get("depends_on", []), "depends_on")
        if mid in depends:
            raise _bad("INVALID_PARAMS", "A milestone cannot depend on itself")
        baseline_date = _date(item.get("baseline_date"))
        origin = ops.db.execute("SELECT * FROM pm_roadmap_milestones WHERE workspace_id=? AND milestone_id=?", (wid, mid)).fetchone()
        if origin and mid not in previous:
            reintroduced.append(mid)
        milestones.append({"milestone_id": mid, "title": name, "plan_revision_id": plan_id,
            "plan_document_digest": plan["document_digest"], "scope_revision": plan["adoption"]["scope_revision"],
            "requirement_ids": requirements, "work_binding_ids": bindings, "baseline_date": baseline_date, "depends_on": depends,
            "original_baseline_date": origin["original_baseline_date"] if origin else baseline_date,
            "first_baseline_revision_id": origin["first_baseline_revision_id"] if origin else rid})
    edges = {m["milestone_id"]: m["depends_on"] for m in milestones}
    if any(dep not in edges for values in edges.values() for dep in values):
        raise _bad("INVALID_PARAMS", "Dependencies must name milestones in this baseline")
    visiting, visited = set(), set()
    def visit(mid):
        if mid in visiting:
            raise _bad("INVALID_PARAMS", "Milestone dependency cycle")
        if mid in visited:
            return
        visiting.add(mid)
        for dep in edges[mid]:
            visit(dep)
        visiting.remove(mid)
        visited.add(mid)
    for mid in edges:
        visit(mid)
    old_scope = _scope_refs(previous.values())
    new_scope = _scope_refs(milestones)
    return {"title": title, "rationale": rationale, "plan_head_at_creation": pm_plans.head(ops.db, wid), "milestones": milestones,
            "changes": {"added_milestone_ids": sorted(seen - previous.keys()), "removed_milestone_ids": sorted(previous.keys() - seen),
                        "reintroduced_milestone_ids": sorted(reintroduced), "added_requirement_refs": _ref_list(new_scope - old_scope),
                        "removed_requirement_refs": _ref_list(old_scope - new_scope)},
            "status": "accepted_scheduling_baseline", "git_materialization": "not_recorded", "execution_authorized": False}


def _forecast(ops, wid, params, current, baseline):
    _only(params, ("baseline_revision_id", "rationale", "updates"))
    rationale = pm_workspace._text(params.get("rationale"), "rationale", 4000)
    updates = params.get("updates")
    if not isinstance(updates, list) or not 1 <= len(updates) <= MAX_MILESTONES:
        raise _bad("INVALID_PARAMS", "Supply 1-100 explicit milestone forecast updates")
    values = {m["milestone_id"]: None for m in baseline["milestones"]}
    if current["forecast_revision_id"]:
        prior = _revision(ops.db, wid, current["forecast_revision_id"])["document"]
        values.update({m["milestone_id"]: m["forecast_date"] for m in prior["milestones"]})
    seen = set()
    for item in updates:
        _only(item, ("milestone_id", "forecast_date"))
        mid = item.get("milestone_id")
        if not isinstance(mid, str) or mid not in values or mid in seen or "forecast_date" not in item:
            raise _bad("INVALID_PARAMS", "Forecast updates require distinct current milestones and explicit dates or null")
        seen.add(mid)
        values[mid] = _date(item["forecast_date"])
    return {"baseline_revision_id": current["baseline_revision_id"], "rationale": rationale,
            "milestones": [{"milestone_id": mid, "forecast_date": value} for mid, value in values.items()],
            "updated_milestone_ids": sorted(seen)}


def _observation(ops, principal, wid, params, current, baseline):
    from . import pm_roadmap_quality
    _only(params, ("baseline_revision_id", "snapshot_id"))
    snapshot = pm_snapshots.get_snapshot(ops, principal, wid, params.get("snapshot_id"))["snapshot"]
    work = {entry["work_item_id"]: entry for entry in snapshot["work"]}
    milestones, total_refs, truncated = [], 0, False
    for milestone in baseline["milestones"]:
        matches, evidence_ids, partial = [], [], False
        for binding_id in milestone["work_binding_ids"]:
            binding = _binding(ops, wid, binding_id)
            found = work.get(binding["work_item_id"])
            status, capture_id = "missing_work", None
            if found:
                status = "binding_mismatch"
                if found["binding"]["binding_id"] == binding_id:
                    status = "missing_capture"
                    if found["capture"]:
                        status = "capture_binding_mismatch"
                        if found["capture"]["binding_id"] == binding_id:
                            status, capture_id = "exact", found["capture"]["capture_id"]
                            for eid in found["evidence_ids"]:
                                if eid in evidence_ids:
                                    continue
                                if total_refs >= MAX_OBSERVATION_REFS:
                                    partial = truncated = True
                                else:
                                    evidence_ids.append(eid)
                                    total_refs += 1
            matches.append({"work_binding_id": binding_id, "work_item_id": binding["work_item_id"],
                            "capture_id": capture_id, "match_status": status, "requirement_ids": binding['requirement_ids']})
        milestones.append({"milestone_id": milestone["milestone_id"], "work": matches, "evidence_ids": evidence_ids,
                           "quality_context": pm_roadmap_quality.context(milestone),
                           "coverage": {"evidence_truncated": partial, "scope_assessment": "unassessed",
                                        "work_mapping": "explicit_bindings" if matches else "unmapped"}})
    document = {"baseline_revision_id": current["baseline_revision_id"], "forecast_revision_id": current["forecast_revision_id"],
            "snapshot_id": snapshot["snapshot_id"], "snapshot_digest": snapshot["snapshot_digest"],
            "as_of": snapshot["as_of"], "journal_cursor": snapshot["journal_cursor"], "milestones": milestones,
            "coverage": {"basis": "fixed_snapshot_and_exact_work_bindings", "source_snapshot": snapshot["coverage"],
                         "evidence_truncated": truncated, "bytes_truncated": False, "network_checked": False, "dod": "unassessed"}}
    return _bound_observation(document, snapshot)


def _check(ops, principal, action, target, params, pre, rid):
    kind = ACTIONS[action]
    workspace = _member(ops, principal, target.get("workspace_id"), write=True, decide=kind != "observation")
    if set(target) != {"workspace_id"}:
        raise _bad("INVALID_TARGET", "Select exactly one workspace")
    expected = {"expected_roadmap_version", "expected_plan_version"} if kind == "baseline" else {"expected_roadmap_version"}
    if set(pre) != expected or any(type(v) is not int or v < 0 for v in pre.values()):
        raise _bad("PRECONDITION_REQUIRED", "Supply exact nonnegative Roadmap and applicable plan versions")
    wid, current = workspace["workspace_id"], head(ops.db, workspace["workspace_id"])
    if pre["expected_roadmap_version"] != current["version"]:
        raise _bad("VERSION_CONFLICT", "Read the current Roadmap before recording a revision", 409)
    if kind == "baseline":
        if pre["expected_plan_version"] != pm_plans.head(ops.db, wid)["version"]:
            raise _bad("VERSION_CONFLICT", "Read the current plan before recording its scheduling baseline", 409)
        document = _baseline(ops, principal, wid, params, current, rid)
    else:
        if not current["baseline_revision_id"]:
            raise _bad("PM_ROADMAP_BASELINE_REQUIRED", "Record an explicit scheduling baseline first", 409)
        if params.get("baseline_revision_id") != current["baseline_revision_id"]:
            raise _bad("PM_ROADMAP_BASELINE_CHANGED", "This operation must name the current fixed baseline", 409)
        baseline = _revision(ops.db, wid, current["baseline_revision_id"])["document"]
        document = _forecast(ops, wid, params, current, baseline) if kind == "forecast" else _observation(ops, principal, wid, params, current, baseline)
    if len(_json(document).encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise _bad("PM_ROADMAP_TOO_LARGE", "Roadmap revision exceeds 1,000,000 UTF-8 bytes")
    return current, document


def _admitter(action):
    def admit(ops, principal, target, params, pre):
        _check(ops, principal, action, target, params, pre, _PREFIX[ACTIONS[action]] + "0" * 32)
        return {"principal": pm_workspace.principal_binding(principal)}
    return admit


def _authorize_existing(ops, principal, op, control):
    _member(ops, principal, op["target"]["workspace_id"], write=True, decide=ACTIONS[op["action"]] != "observation")


async def _run(ctx):
    ops, kind = ctx.service, ACTIONS[ctx.op["action"]]
    with ops.journal.tx():
        saved = ops.db.execute("SELECT result FROM pm_roadmap_applied WHERE operation_id=?", (ctx.operation_id,)).fetchone()
        if saved:
            return json.loads(saved[0])
        from .pm_lifecycle_gates import require_local
        require_local(ctx.service.db, ctx.operation_id)
        principal = pm_workspace.admitted_principal(ctx)
        rid = _PREFIX[kind] + ctx.operation_id[3:]
        current, document = _check(ops, principal, ctx.op["action"], ctx.target, ctx.params, ctx.preconditions, rid)
        wid, now = ctx.target["workspace_id"], time.time()
        baseline_id = rid if kind == "baseline" else current["baseline_revision_id"]
        digest = _digest(document)
        seq = ops.db.execute("""INSERT INTO pm_roadmap_revisions(revision_id,workspace_id,kind,previous_revision_id,
            baseline_revision_id,document,document_digest,created_by,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (rid, wid, kind, current[kind + "_revision_id"], baseline_id, _json(document), digest, ctx.actor, now, ctx.operation_id)).lastrowid
        updated = {**current, "version": current["version"] + 1, kind + "_revision_id": rid}
        if kind == "baseline":
            updated.update(forecast_revision_id=None, observation_revision_id=None)
            for milestone in document["milestones"]:
                ops.db.execute("INSERT OR IGNORE INTO pm_roadmap_milestones VALUES(?,?,?,?,?,?)",
                    (wid, milestone["milestone_id"], milestone["first_baseline_revision_id"], milestone["original_baseline_date"], ctx.actor, now))
        ops.db.execute("""INSERT INTO pm_roadmap_heads VALUES(?,?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET
            version=excluded.version,baseline_revision_id=excluded.baseline_revision_id,
            forecast_revision_id=excluded.forecast_revision_id,observation_revision_id=excluded.observation_revision_id""",
            (wid, updated["version"], updated["baseline_revision_id"], updated["forecast_revision_id"], updated["observation_revision_id"]))
        result = {"workspace_id": wid, "revision_id": rid, "kind": kind, "sequence": seq, "head": updated,
                  "document_digest": digest, "execution_authorized": False}
        ops.db.execute("INSERT INTO pm_roadmap_applied VALUES(?,?)", (ctx.operation_id, _json(result)))
        ops.journal.api_event("pm_roadmap", rid, ctx.op["action"], result, actor=ctx.actor)
        return result


def _fact_summary(evidence):
    fact = evidence["document"]["facts"]
    out = {key: value for key, value in evidence.items() if key != "document"}
    out["assessment"] = pm_evidence._fields(fact, ("status", "state", "verification", "deployment", "delivery", "merge_verified"))
    out["version_refs"] = pm_evidence._fields(fact, ("candidate_commit", "tree_hash", "head_sha", "base_sha", "merged_sha", "delivered_sha"))
    if isinstance(fact.get("identity"), dict):
        out["version_refs"].update(pm_evidence._fields(fact["identity"], ("source_sha", "artifact_id", "artifact_digest", "release_id")))
    return out


def _project_observation(document, snapshot):
    from . import pm_roadmap_quality
    doc = copy.deepcopy(document)
    facts = {entry["evidence_id"]: entry for entry in snapshot["evidence"]}
    filtered = False
    for milestone in doc["milestones"]:
        original = milestone["evidence_ids"]
        milestone["evidence_ids"] = [eid for eid in original if eid in facts]
        hidden = len(original) != len(milestone["evidence_ids"])
        filtered |= hidden
        milestone["coverage"]["inaccessible_sources"] = hidden
        milestone["actual"] = {"completion": "unassessed", "dod": "unassessed", "actual_finish_date": None,
                               "facts": [_fact_summary(facts[eid]) for eid in milestone["evidence_ids"]]}
        quality = pm_roadmap_quality.project(milestone, snapshot)
        if quality is not None:
            milestone['actual'].update(pm_roadmap_quality.outcome(quality))
    doc["coverage"]["inaccessible_sources"] = filtered
    return doc


def _bound_observation(document, snapshot):
    if len(_json(_project_observation(document, snapshot)).encode("utf-8")) <= MAX_OBSERVATION_BYTES:
        return document
    # Keep a deterministic prefix of complete fact references; retain every
    # milestone/work match and explicitly report the resulting coverage gap.
    originals = [m["evidence_ids"][:] for m in document["milestones"]]
    def limited(count):
        candidate = copy.deepcopy(document)
        for milestone, ids in zip(candidate["milestones"], originals, strict=True):
            kept = ids[:count]
            count -= len(kept)
            milestone["evidence_ids"] = kept
            milestone["coverage"]["evidence_truncated"] |= len(kept) != len(ids)
        candidate["coverage"].update(evidence_truncated=True, bytes_truncated=True)
        return candidate
    low, high = 0, sum(map(len, originals))
    empty = limited(0)
    if len(_json(_project_observation(empty, snapshot)).encode("utf-8")) > MAX_OBSERVATION_BYTES:
        raise _bad("PM_ROADMAP_TOO_LARGE", "Observation source metadata exceeds its UTF-8 response bound")
    while low < high:
        middle = (low + high + 1) // 2
        if len(_json(_project_observation(limited(middle), snapshot)).encode("utf-8")) <= MAX_OBSERVATION_BYTES:
            low = middle
        else:
            high = middle - 1
    return limited(low)


def _out(ops, principal, revision):
    if revision["kind"] != "observation":
        return revision
    out = copy.deepcopy(revision)
    doc = out["document"]
    snapshot = pm_snapshots.get_snapshot(ops, principal, out["workspace_id"], doc["snapshot_id"])["snapshot"]
    if snapshot["snapshot_digest"] != doc["snapshot_digest"]:
        raise _bad("PM_ROADMAP_SOURCE_CHANGED", "The referenced fixed snapshot digest does not match", 409)
    out["document"] = _project_observation(doc, snapshot)
    out["view_digest"] = _digest(out)
    return out


def get_roadmap(ops, principal, workspace_id):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        current = head(ops.db, workspace_id)
        return {"version": 1, "workspace_id": workspace_id, "head": current,
                **{kind: _out(ops, principal, _revision(ops.db, workspace_id, current[kind + "_revision_id"]))
                   if current[kind + "_revision_id"] else None for kind in _PREFIX}}


def get_revision(ops, principal, workspace_id, revision_id):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        return {"version": 1, "workspace_id": workspace_id,
                "revision": _out(ops, principal, _revision(ops.db, workspace_id, revision_id))}


def list_revisions(ops, principal, workspace_id, *, kind=None, after=0, limit=50):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        pm_evidence._page(after, limit)
        if kind is not None and (not isinstance(kind, str) or kind not in _PREFIX):
            raise _bad("INVALID_PARAMS", "kind must be baseline, forecast or observation")
        rows = ops.db.execute("""SELECT sequence,revision_id,workspace_id,kind,previous_revision_id,baseline_revision_id,
            document_digest,created_by,created_at,operation_id FROM pm_roadmap_revisions WHERE workspace_id=? AND sequence>?
            AND (? IS NULL OR kind=?) ORDER BY sequence LIMIT ?""", (workspace_id, after, kind, kind, limit + 1)).fetchall()
        return {"version": 1, "workspace_id": workspace_id, "head": head(ops.db, workspace_id),
                "revisions": [dict(row) for row in rows[:limit]],
                "next_cursor": rows[limit - 1]["sequence"] if len(rows) > limit else None}
