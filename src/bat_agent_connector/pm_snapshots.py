"""Fixed project context shared by Brief, future views and document renderers.

Snapshots preserve selected central facts, not an agent's inferred project state.
Reading a historical snapshot still requires current source visibility.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from . import pm_evidence, pm_plans, pm_workspace, work_items
from .operations import ActionDef, OperationError

TEMPLATE_VERSION = 1
MAX_PROJECTS = 100
MAX_WORK = 100
MAX_EVIDENCE = 500
MAX_SNAPSHOT_BYTES = 2_000_000
SNAPSHOT_ID = re.compile(r"pmsnap_[0-9a-f]{32}")
ACTION = "pm.snapshot.create"


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _bad(code, message, status=422):
    return OperationError(code, message, status)


def schema(journal):
    pm_plans.schema(journal)
    pm_evidence.schema(journal)
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_document_snapshots (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, title TEXT NOT NULL, as_of REAL NOT NULL,
            journal_cursor INTEGER NOT NULL, template_version INTEGER NOT NULL,
            snapshot_digest TEXT NOT NULL, document TEXT NOT NULL,
            created_by TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE)""")
        journal.db.execute("CREATE INDEX IF NOT EXISTS pm_snapshot_workspace ON pm_document_snapshots(workspace_id,sequence)")
        journal.db.execute("""CREATE TABLE IF NOT EXISTS pm_snapshot_applied (
            operation_id TEXT PRIMARY KEY, result TEXT NOT NULL)""")
        for table in ("pm_document_snapshots", "pm_snapshot_applied"):
            for verb in ("UPDATE", "DELETE"):
                journal.db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable BEFORE {verb} ON {table} "
                                   "BEGIN SELECT RAISE(ABORT, 'PM document snapshots are immutable'); END")


def install(ops):
    schema(ops.journal)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, "manage", "Freeze sourced project context for a shared Brief and documents", _run,
                               _admit, ("workspace_id",), authorize_existing=_authorize_existing))


def _member(ops, principal, workspace_id, *, write=False):
    if not principal.allows("observe") or write and not principal.allows("manage"):
        raise _bad("FORBIDDEN", "Snapshots require observe; creating one also requires manage", 403)
    return pm_workspace.require_member(ops.db, principal, workspace_id, write=write)


def _check(ops, principal, target, params, pre):
    workspace = _member(ops, principal, target.get("workspace_id"), write=True)
    if set(target) != {"workspace_id"}:
        raise _bad("INVALID_TARGET", "Select exactly one workspace")
    if set(params) - {"title"}:
        raise _bad("INVALID_PARAMS", "Snapshot facts come from the central journal; only a title may be supplied")
    if "title" in params:
        pm_workspace._text(params["title"], "title", 200)
    if (set(pre) != {"expected_plan_version"} or type(pre["expected_plan_version"]) is not int
            or pre["expected_plan_version"] < 0):
        raise _bad("PRECONDITION_REQUIRED", "Supply the current plan head version, including zero before any plan")
    current = pm_plans.head(ops.db, workspace["workspace_id"])
    if pre["expected_plan_version"] != current["version"]:
        raise _bad("VERSION_CONFLICT", "Read the current plan version before creating a snapshot", 409)
    return workspace, current


def _admit(ops, principal, target, params, pre):
    _check(ops, principal, target, params, pre)
    return {"principal": pm_workspace.principal_binding(principal)}


def _authorize_existing(ops, principal, op, control):
    _member(ops, principal, op["target"]["workspace_id"], write=True)


def _baseline(ops, principal, wid, current):
    baseline = {"status": "unavailable", "plan_head_version": current["version"],
                "document_revision": None, "scope_revision": None, "document": None, "adoption": None}
    if current["accepted_revision_id"]:
        plan = pm_plans.get_plan(ops, principal, wid, current["accepted_revision_id"])["plan"]
        pm_plans.revision_files(ops.db, wid, plan["revision_id"])
        if not plan["adoption"]:
            raise _bad("PM_PLAN_CORRUPT", "The accepted plan has no adoption record", 409)
        baseline.update(status="accepted", document_revision=plan["revision_id"],
                        scope_revision=plan["adoption"]["scope_revision"], document=plan["document"],
                        document_digest=plan["document_digest"], structured_scope_digest=plan["structured_scope_digest"],
                        adoption=plan["adoption"])
    candidate = None
    if current["latest_revision_id"] and current["latest_revision_id"] != current["accepted_revision_id"]:
        plan = pm_plans.get_plan(ops, principal, wid, current["latest_revision_id"])["plan"]
        candidate = {"revision_id": plan["revision_id"], "document_digest": plan["document_digest"],
                     "title": plan["document"]["title"], "status": "historically_adopted" if plan["adoption"] else "draft",
                     "is_current_commitment": False, "created_at": plan["created_at"]}
    return baseline, candidate


def _item(kind, text, status, source, details=None):
    value = {"kind": kind, "text": text, "status": status, "source": source}
    if details is not None:
        value["details"] = details
    return value


def _quality_summary(document):
    """Count each original scope requirement once, across explicitly selected work."""
    baseline = document['baseline']
    facts = {entry['evidence_id']: entry['document']['facts'] for entry in document['evidence']
             if entry['source_kind'] == 'quality_assessment'}
    if not facts:
        return None
    requirements = {item['id']: [] for item in (baseline['document'] or {}).get('requirements', [])}
    qualified_work = set()
    for work in document['work']:
        binding = work['binding']
        selected = [facts[eid] for eid in work['evidence_ids'] if eid in facts]
        quality = selected[0] if len(selected) == 1 else None
        matching = bool(quality and quality['work_item_id'] == work['work_item_id']
            and quality['binding_id'] == binding['binding_id']
            and quality['scope_revision'] == baseline['scope_revision']
            and quality['applicable_at_selection'])
        if matching and quality['qualified']:
            qualified_work.add(work['work_item_id'])
        statuses = {item['requirement_id']: item['status'] for item in quality['requirements']} if matching else {}
        for rid in binding['requirement_ids']:
            if rid not in requirements:
                continue
            status = statuses.get(rid, 'unassessed')
            requirements[rid].append({'work_item_id': work['work_item_id'], 'acceptance': status,
                'dod': quality['dod'] if matching else 'unassessed'})
    values = []
    for rid, contributions in requirements.items():
        def combined(key, rows=contributions):
            states = [item[key] for item in rows]
            return 'rejected' if 'rejected' in states else 'accepted' if states and all(v == 'accepted' for v in states) else 'unassessed'
        acceptance, dod = combined('acceptance'), combined('dod')
        values.append({'requirement_id': rid, 'acceptance': acceptance, 'dod': dod,
            'qualified': acceptance == dod == 'accepted', 'contributions': contributions})
    # A truncated page cannot establish that every contribution was accounted for.
    complete = not any(document['coverage'][key] for key in ('work_truncated', 'evidence_truncated', 'bytes_truncated'))
    if not complete:
        for value in values:
            value['qualified'] = False
    return {'scope_revision': baseline['scope_revision'], 'requirements': values,
        'total_requirements': len(values), 'qualified_requirements': sum(v['qualified'] for v in values),
        'qualified_deliverables': len(qualified_work), 'contribution_coverage_complete': complete,
        'basis': 'explicit_fixed_assessments_all_selected_work_contributions',
        'current_applicability': 'not_evaluated', 'current_environment': 'not_assessed'}


def _brief(document):
    """Version-one deterministic projection; no narrative or quality inference."""
    baseline, work, evidence = document["baseline"], document["work"], document["evidence"]
    quality = _quality_summary(document)
    accepted = baseline["document"]
    goals, deliveries, attention, decisions = [], [], [], []
    if accepted:
        selected = {item["input_id"]: item for item in accepted["selected_inputs"]}
        goal_ids = dict.fromkeys(item for domain in ("governance", "scope")
                                 for item in accepted["sections"][domain]["source_input_ids"])
        for input_id in goal_ids:
            item = selected[input_id]
            goals.append(_item("attributed_direction", item["value"], item["status"],
                {"kind": "plan_input", "revision_id": baseline["document_revision"], "input_id": input_id,
                 "source_ref": item["source_ref"]}, {"confirmed_by": item["confirmed_by"],
                 "evidence_verification": item.get("evidence_verification", "unassessed")}))
        adoption = baseline["adoption"]
        decisions.append(_item("adoption", adoption["rationale"], "adopted",
            {"kind": "plan_adoption", "revision_id": baseline["document_revision"], "decision_id": adoption["decision_id"]},
            {"adopted_by": adoption["adopted_by"], "created_at": adoption["created_at"], "scope_revision": baseline["scope_revision"]}))
        for item in accepted["selected_inputs"]:
            if item["status"] in {"unresolved", "conflicting"}:
                decisions.append(_item("recorded_open_input", item["value"], item["status"],
                    {"kind": "plan_input", "revision_id": baseline["document_revision"], "input_id": item["input_id"],
                     "source_ref": item["source_ref"]}))
    for entry in evidence:
        kind, fact = entry["source_kind"], entry["document"]["facts"]
        source = {"kind": "evidence", "evidence_id": entry["evidence_id"]}
        state = fact.get("state") or fact.get("status")
        if kind == "observed_verification":
            text = f"Recorded verification {fact['verification']} for commit {fact['candidate_commit']}"
            deliveries.append(_item(kind, text, fact["verification"], source, fact))
        elif kind == "pr_merge":
            deliveries.append(_item(kind, f"Merge verified at commit {fact['merged_sha']}", "merged", source, fact))
        elif kind == "deployment":
            text = f"Recorded deployment {fact['deployment']}; current environment not assessed"
            deliveries.append(_item(kind, text, fact["deployment"], source, fact))
        elif kind == "integration_delivery":
            text = f"Integration {fact['delivery']}; PR state remains unknown"
            deliveries.append(_item(kind, text, fact["delivery"], source, fact))
        elif kind == "work_claim":
            text = f"Work state recorded as {state}" + ('; quality unassessed' if quality is None else '')
            deliveries.append(_item(kind, text, state or "unknown", source, fact))
        elif kind == 'quality_assessment':
            deliveries.append(_item(kind, f"Original acceptance {fact['acceptance']}; DoD {fact['dod']}",
                'qualified' if fact['qualified'] else 'not_qualified', source, fact))
        if kind in {"work_claim", "execution_state", "execution_command", "operation_status"} and state in {
                "waiting", "blocked", "failed", "needs_attention", "needs_ted", "uncertain"}:
            attention.append(_item("recorded_attention_state", f"Recorded {kind} state: {state}", state, source))
    for entry in work:
        if entry["capture"] is None:
            attention.append(_item("missing_capture", "No saved evidence capture is selected for this work", "unknown",
                {"kind": "work_binding", "work_item_id": entry["work_item_id"], "binding_id": entry["binding"]["binding_id"]}))
    result = {
        "goals": {"status": "accepted_sources" if accepted else "unavailable", "items": goals,
                  "coverage": {"basis": "accepted_governance_and_scope_inputs", "source_statuses_preserved": True}},
        "delivery": {"status": "quality_unassessed", "items": deliveries,
                     "coverage": {"basis": "selected_capture_facts", "dod": "unassessed", "qualified_deliverables": None,
                                  "completion_ratio": None, "current_environment": "not_assessed"}},
        "blockers": {"status": "partial_source_states", "items": attention,
                     "coverage": {"basis": "recorded_attention_states_and_missing_captures", "complete_diagnosis": False}},
        "deviations": {"status": "unassessed", "items": [],
                       "coverage": {"baseline_result_assessment": "not_performed", "old_binding_is_not_drift": True}},
        "decisions": {"status": "recorded_sources" if accepted else "unavailable", "items": decisions,
                      "coverage": {"basis": "accepted_adoption_and_fixed_open_inputs", "prioritization": "not_assessed"}},
    }
    if quality is not None:
        result['delivery'].update(status='fixed_quality_assessments', quality=quality)
        result['delivery']['coverage'].update(dod='per_selected_original_revision',
            qualified_deliverables=quality['qualified_deliverables'],
            requirement_denominator='distinct_original_scope_requirement', current_applicability='not_evaluated')
    return result


def _response(document, sequence, snapshot_digest, *, filtered=False):
    out = {**document, "sequence": sequence, "snapshot_digest": snapshot_digest, "brief": _brief(document),
           "visibility": {"scope": "workspace", "source_policy": "current_membership_and_source_access", "filtered": filtered}}
    out["coverage"] = {**out["coverage"], "inaccessible_sources": filtered}
    out["view_digest"] = _digest(out)
    return {"version": 1, "workspace_id": document["workspace_id"], "snapshot": out}


def _encoded_size(document):
    # Count the actual UTF-8 response, including facts and Brief. Reserve the
    # maximum SQLite sequence width and the final fixed-width content digest.
    return len(_json(_response(document, 9223372036854775807, "0" * 64)).encode("utf-8"))


def _fits(document):
    return _encoded_size(document) <= MAX_SNAPSHOT_BYTES


def _build(ctx, principal, workspace, current):
    ops, wid = ctx.service, workspace["workspace_id"]
    baseline, candidate = _baseline(ops, principal, wid, current)
    doc = {"snapshot_id": "pmsnap_" + ctx.operation_id[3:], "server_id": pm_workspace.server_id(ops.db),
           "workspace_id": wid, "title": ctx.params.get("title", "Project Brief"), "as_of": time.time(),
           "journal_cursor": ops.journal.api_head(), "template_version": TEMPLATE_VERSION,
           "created_by": ctx.actor, "operation_id": ctx.operation_id, "workspace_version": workspace["version"],
           "execution_authorized": False,
           "workspace_name": workspace["name"], "plan_head": current, "baseline": baseline,
           "candidate_document": candidate, "projects": [], "work": [], "evidence": [],
           "coverage": {"basis": "selected_saved_workspace_sources", "network_checked": False,
                        "projects_truncated": False, "work_truncated": False, "evidence_truncated": False,
                        "bytes_truncated": False, "unlinked_activity": "not_scanned", "capture_refresh": "not_requested",
                        "freshness": "per_source_saved_times", "cost": "unknown", "dod": "unassessed",
                        "milestones": "not_projected", "sprints": "not_projected", "risk_assessment": "not_performed"}}
    if not _fits(doc):
        raise _bad("PM_SNAPSHOT_BASELINE_TOO_LARGE", "The fixed baseline and its attributed Brief exceed the snapshot byte bound")
    projects = pm_evidence.list_projects(ops, principal, wid, limit=MAX_PROJECTS)
    doc["coverage"]["projects_truncated"] = projects["next_cursor"] is not None
    for binding in projects["bindings"]:
        project = work_items._get_project(ops.db, binding["project_id"])
        entry = {"binding": binding, "project_id": project["project_id"], "name": project["name"],
                 "metadata_version": project["version"]}
        doc["projects"].append(entry)
        if not _fits(doc):
            doc["projects"].pop()
            doc["coverage"].update(projects_truncated=True, bytes_truncated=True)
            break
    work = pm_evidence.list_work(ops, principal, wid, limit=MAX_WORK)
    doc["coverage"]["work_truncated"] = work["next_cursor"] is not None
    known, bytes_exhausted = set(), False
    for binding in work["bindings"]:
        item = work_items._get_item(ops.db, binding["work_item_id"])
        capture = pm_evidence.latest_capture(ops, principal, wid, item["work_item_id"])["capture"]
        entry = {"work_item_id": item["work_item_id"], "project_id": item["project_id"], "title": item["title"],
                 "metadata_version": item["version"], "binding": binding, "capture": None, "evidence_ids": [],
                 "coverage": {"evidence_truncated": False, "capture_matches_binding": None,
                              "capture_matches_work_version": None, "inaccessible_sources": False}}
        if capture:
            entry["capture"] = {key: capture[key] for key in (
                "capture_id", "sequence", "captured_at", "journal_cursor", "binding_id", "plan_revision_id",
                "requirement_ids", "source_work_version", "coverage")}
            entry["coverage"]["capture_matches_binding"] = capture["binding_id"] == binding["binding_id"]
            entry["coverage"]["capture_matches_work_version"] = capture["source_work_version"] == item["version"]
        doc["work"].append(entry)
        encoded_size = _encoded_size(doc)
        if encoded_size > MAX_SNAPSHOT_BYTES:
            doc["work"].pop()
            doc["coverage"].update(work_truncated=True, bytes_truncated=True)
            break
        for evidence_id in capture["evidence_ids"] if capture else ():
            if evidence_id not in known and (len(known) >= MAX_EVIDENCE or bytes_exhausted):
                entry["coverage"]["evidence_truncated"] = doc["coverage"]["evidence_truncated"] = True
                continue
            evidence = None
            if evidence_id not in known:
                evidence = pm_evidence.get_evidence(ops, principal, wid, evidence_id)["evidence"]
                doc["evidence"].append(evidence)
            reference_bytes = len(_json(evidence_id).encode("utf-8")) + bool(entry["evidence_ids"])
            entry["evidence_ids"].append(evidence_id)
            # A repeated immutable fact only adds its ID to this work; its Brief
            # projection is unchanged. Avoid reserializing the whole snapshot
            # for every shared source in a large work collection.
            proposed_size = _encoded_size(doc) if evidence else encoded_size + reference_bytes
            if proposed_size > MAX_SNAPSHOT_BYTES:
                entry["evidence_ids"].pop()
                if evidence:
                    doc["evidence"].pop()
                entry["coverage"]["evidence_truncated"] = True
                doc["coverage"].update(evidence_truncated=True, bytes_truncated=True)
                bytes_exhausted = True
            else:
                known.add(evidence_id)
                encoded_size = proposed_size
    return doc


def _insert(ctx, workspace, document):
    """Insert one already-built snapshot inside its caller's journal transaction.

    Internal callers may add a source-gated maintenance extension before this
    shared insertion. Public snapshot.create still accepts only its title.
    """
    ops = ctx.service
    if not ops.db.in_transaction or document['snapshot_id'] != 'pmsnap_' + ctx.operation_id[3:]:
        raise _bad('PM_SNAPSHOT_TRANSACTION_REQUIRED', 'Insert the original snapshot inside its journal transaction', 409)
    if not _fits(document):
        raise _bad('PM_SNAPSHOT_BASELINE_TOO_LARGE', 'The complete fixed snapshot exceeds its byte bound', 413)
    digest = _digest(document)
    sequence = ops.db.execute("""INSERT INTO pm_document_snapshots(snapshot_id,workspace_id,title,as_of,journal_cursor,
        template_version,snapshot_digest,document,created_by,operation_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (document["snapshot_id"], workspace["workspace_id"], document["title"], document["as_of"], document["journal_cursor"],
         TEMPLATE_VERSION, digest, _json(document), ctx.actor, ctx.operation_id)).lastrowid
    result = {key: document[key] for key in ("snapshot_id", "workspace_id", "as_of", "journal_cursor", "template_version")}
    result.update(sequence=sequence, snapshot_digest=digest, execution_authorized=False)
    ops.db.execute("INSERT INTO pm_snapshot_applied VALUES(?,?)", (ctx.operation_id, _json(result)))
    ops.journal.api_event("pm_snapshot", document["snapshot_id"], "pm.snapshot.created", result, actor=ctx.actor)
    return result


async def _run(ctx):
    ops = ctx.service
    with ops.journal.tx():
        saved = ops.db.execute("SELECT result FROM pm_snapshot_applied WHERE operation_id=?", (ctx.operation_id,)).fetchone()
        if saved:
            return json.loads(saved[0])
        from .pm_lifecycle_gates import require_local
        require_local(ctx.service.db, ctx.operation_id)
        principal = pm_workspace.admitted_principal(ctx)
        workspace, current = _check(ops, principal, ctx.target, ctx.params, ctx.preconditions)
        document = _build(ctx, principal, workspace, current)
        return _insert(ctx, workspace, document)


def _out(ops, principal, row):
    document = json.loads(row["document"])
    if _digest(document) != row["snapshot_digest"] or document["template_version"] != TEMPLATE_VERSION:
        raise _bad("PM_SNAPSHOT_UNAVAILABLE", "The fixed snapshot or its template cannot be read", 409)
    if 'maintenance' in document:
        from . import pm_maintenance_review_sources
        pm_maintenance_review_sources.access(ops, principal, document['workspace_id'], document['maintenance']['access_refs'])
    # Never use newly captured facts here. The evidence ID and digest must match
    # the original snapshot; current lookup only authorizes those fixed bytes.
    visible = []
    for evidence in document["evidence"]:
        try:
            source = pm_evidence.get_evidence(ops, principal, document["workspace_id"], evidence["evidence_id"])["evidence"]
        except OperationError as error:
            if error.status != 404:
                raise
            continue
        if source["fact_digest"] == evidence["fact_digest"]:
            visible.append(evidence)
    filtered = len(visible) != len(document["evidence"])
    document["evidence"] = visible
    visible_ids = {item["evidence_id"] for item in visible}
    for item in document["work"]:
        item["coverage"]["inaccessible_sources"] = any(eid not in visible_ids for eid in item["evidence_ids"])
        item["evidence_ids"] = [eid for eid in item["evidence_ids"] if eid in visible_ids]
    return _response(document, row["sequence"], row["snapshot_digest"], filtered=filtered)


def list_snapshots(ops, principal, workspace_id, *, after=0, limit=50):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        pm_evidence._page(after, limit)
        rows = ops.db.execute("""SELECT sequence,snapshot_id,workspace_id,title,as_of,journal_cursor,template_version,
            snapshot_digest,created_by,operation_id FROM pm_document_snapshots WHERE workspace_id=? AND sequence>?
            ORDER BY sequence LIMIT ?""", (workspace_id, after, limit + 1)).fetchall()
        visible, filtered = [], False
        for row in rows[:limit]:
            # Maintenance titles/context metadata must not become an alternate
            # read path after one of the report's original sources is withdrawn.
            stored = ops.db.execute('SELECT document FROM pm_document_snapshots WHERE snapshot_id=?', (row['snapshot_id'],)).fetchone()
            document = json.loads(stored['document'])
            if 'maintenance' in document:
                from . import pm_maintenance_review_sources
                try:
                    pm_maintenance_review_sources.access(ops, principal, workspace_id, document['maintenance']['access_refs'])
                except OperationError as exc:
                    if exc.status not in (403, 404):
                        raise
                    filtered = True
                    continue
            visible.append(dict(row))
        return {"version": 1, "workspace_id": workspace_id, "snapshots": visible,
                "next_cursor": rows[limit - 1]["sequence"] if len(rows) > limit else None,
                "visibility_filtered": filtered}


def get_snapshot(ops, principal, workspace_id, snapshot_id):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        if not isinstance(snapshot_id, str) or not SNAPSHOT_ID.fullmatch(snapshot_id):
            raise _bad("PM_SNAPSHOT_NOT_FOUND", "Snapshot not found", 404)
        row = ops.db.execute("SELECT * FROM pm_document_snapshots WHERE workspace_id=? AND snapshot_id=?",
                             (workspace_id, snapshot_id)).fetchone()
        if not row:
            raise _bad("PM_SNAPSHOT_NOT_FOUND", "Snapshot not found", 404)
        return _out(ops, principal, row)


def latest_snapshot(ops, principal, workspace_id):
    with ops.journal.tx():
        _member(ops, principal, workspace_id)
        row = ops.db.execute("SELECT * FROM pm_document_snapshots WHERE workspace_id=? ORDER BY sequence DESC LIMIT 1",
                             (workspace_id,)).fetchone()
        return _out(ops, principal, row) if row else {"version": 1, "workspace_id": workspace_id, "snapshot": None}
