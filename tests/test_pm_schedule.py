"""Recorded project schedule: three time lines, no computed forecast."""
from __future__ import annotations

import base64
import json
import re

import pytest

from bat_agent_connector import (
    mcp_server,
    pm_issue_mappings,
    pm_package_handoff,
    pm_plans,
    pm_reads,
    pm_runs,
    pm_schedule,
    pm_source_lineage,
    pm_work_packages,
    work_items,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_pm_api import OTHER, seed
from tests.test_pm_api import OWNER as API_OWNER
from tests.test_pm_discussions import CONTRIBUTOR, OUTSIDER, OWNER, READER, act, prepared
from tests.test_pm_evidence import bind_work, item, link, session, source_operation, task
from tests.test_pm_plans import adopt, adoption, draft, planning
from tests.test_pm_roadmap import mapped, milestone, revision
from tests.test_pm_roadmap import ops as ops_fixture
from tests.test_pm_run_api import grant, unpack
from tests.test_pm_snapshots import accepted, snapshot, work_for

ops = ops_fixture
daemon, served = api_daemon, api_served

TASK_AT = 1700000000
EARLY_AT = 1000
LATE_AT = 5000
MERGE_AT = 5000
APPROVED_AT = 1700000100
WAITING_AT = 1700000200
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
STATUSES = {"not_started", "in_progress", "waiting", "in_review", "accepted", "blocked", "done", "merged",
            "deployed", "unknown"}


def schedule(service, wid, principal=OWNER, **params):
    return pm_schedule.read(service, principal, wid, **params)


def stable(body):
    return {key: value for key, value in body.items() if key != "as_of"}


def nodes(body):
    return body["rows"] + body["unscheduled"]


def by_id(body):
    return {node["id"]: node for node in nodes(body)}


def reasons(body):
    return {(item["field"], item["reason"]) for item in body["unknown"]}


def instant(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def check(body, wid):
    assert body["version"] == 1 and body["workspace_id"] == wid
    assert ISO.fullmatch(body["as_of"])
    assert "total" not in body
    assert body["capabilities"] == pm_schedule.capabilities()
    assert body["coverage"]["network_checked"] is False
    assert body["coverage"]["critical_path"] is False
    assert body["coverage"]["token_attribution"] is False
    assert body["coverage"]["forecast"] == "recorded_revision_only"
    assert body["coverage"]["basis"] == "explicit_roadmap_scope_and_links"
    assert isinstance(body["coverage"]["visibility_filtered"], bool)
    assert isinstance(body["coverage"]["attempts_unavailable"], bool)
    keys = set()

    def walk(value):
        if isinstance(value, dict):
            keys.update(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(body)
    assert {"tokens", "token_count", "usage"} & keys == set()
    found = nodes(body)
    assert len({node["id"] for node in found}) == len(found)
    for node in found:
        assert node["status"] in STATUSES and node["kind"] != "phase" and node["blocked"] is None
        assert {"parent_id", "depends_on", "refs", "waiting"} <= set(node)
    raw = json.dumps(body)
    assert "PRIVATE" not in raw and "/private" not in raw
    assert body["unknown"] == sorted(body["unknown"], key=lambda item: (item["field"], item["reason"]))
    return body


def retitle(service, sid="mock-session", *, runtime="idle", streaming=None, access="read_only", provenance="manual"):
    body = {"streaming": streaming, "pending": None, "cwd": "/private/path", "title": "Membership session",
            "model": "fixture-model", "runtime_status": runtime}
    service.db.execute("""UPDATE sessions_observed SET body=?, gone_at=?, api_access=?, provenance=?
        WHERE host=? AND session_id=?""", (json.dumps(body), 30, access, provenance, "mock-host", sid))


async def lined(service):
    target, plan, work, binding, _selected, baseline = await mapped(service)
    wid = target["workspace_id"]
    await revision(service, wid, "forecast", updates=[{"milestone_id": "release", "forecast_date": "2026-11-08"}])
    source = task(service)
    await link(service, work, "task", source["task_id"])
    service.db.execute("UPDATE tasks SET state='running', submitted_at=? WHERE task_id=?", (TASK_AT, source["task_id"]))
    ref = session(service)
    retitle(service)
    await link(service, work, "session", ref)
    await link(service, work, "pull_request", "acme/widget#12")
    return {"wid": wid, "plan": plan, "work": work, "binding": binding, "baseline": baseline, "task": source,
            "session": ref}


def approve(service, work_id, at):
    service.db.execute("UPDATE work_items SET state='done' WHERE work_item_id=?", (work_id,))
    current = work_items._get_item(service.db, work_id)
    service.db.execute("""UPDATE work_items SET approved_fingerprint=?, approved_by=?, approved_at=?
        WHERE work_item_id=?""", (work_items.fingerprint(current), OWNER.actor, at, work_id))


def mark_waiting(service, work_id, at=None):
    service.db.execute("UPDATE work_items SET state='waiting', updated_at=? WHERE work_item_id=?",
                       (9_000_000_000, work_id))
    if at is not None:
        service.db.execute("""INSERT INTO api_events(resource_type,resource_id,kind,body,actor,created_at)
            VALUES('work_item',?,'work_item.state',?,?,?)""",
            (work_id, json.dumps({"from": "todo", "to": "waiting"}), OWNER.actor, at))


def withdraw(service, kind, identity):
    pm_source_lineage.schema(service.journal)
    missing = "op_" + "cd" * 16
    service.db.execute("""INSERT INTO pm_source_lineage(resource_type,resource_id,original_operation_id,
        creation_operation_id) VALUES(?,?,?,?)""", (kind, identity, missing, missing))


def merge_receipt(service, operation_id, finished_at):
    seq = service.db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM operation_steps WHERE operation_id=?",
                             (operation_id,)).fetchone()[0]
    service.db.execute("""INSERT INTO operation_steps(operation_id,seq,name,status,request,response,started_at,finished_at)
        VALUES(?,?, 'merge.verify','succeeded','{}',?,?,?)""",
        (operation_id, seq, json.dumps({"verified": True, "merged_sha": "a" * 40}), finished_at - 1, finished_at))


def deployment(service, operation_id, verified):
    service.db.execute("""INSERT INTO deployments(deployment_id,operation_id,recipe,environment_key,identity,
        recipe_snapshot,recipe_digest,state,document,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        ("dep_" + operation_id[3:], operation_id, "fixture", "env-" + operation_id[-8:],
         json.dumps({"source_sha": "b" * 40}), "{}", "c" * 64, "succeeded", json.dumps({"verified": verified}),
         100, 100))


async def test_empty_plan_does_not_invent_a_schedule(ops):
    target, *_ = await accepted(ops)
    wid = target["workspace_id"]
    plan = pm_plans.accepted_plan(ops, OWNER, wid)["plan"]
    text = plan["document"]["requirements"][0]["text"]
    assert text == "Members can log in"
    version = ops.db.execute("PRAGMA user_version").fetchone()[0]
    assert ops.db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='events_task'").fetchone()
    changes = ops.db.total_changes
    body = check(schedule(ops, wid), wid)
    assert ops.db.total_changes == changes
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == version
    assert body["baseline"] is body["forecast"] is body["range"] is None
    assert body["rows"] == body["unscheduled"] == []
    assert body["range_scope"] == "complete" and body["partial"] is body["truncated"] is False
    assert body["next_cursor"] is None
    assert text not in json.dumps(body) and "A useful deliverable" not in json.dumps(body)
    assert ("baseline", "no_adopted_schedule_baseline") in reasons(body)
    assert ("phase", "no_explicit_phase_records") in reasons(body)
    assert ("blocked", "no_explicit_blocked_record") in reasons(body)
    assert ("ci_wait", "no_explicit_check_binding") in reasons(body)
    assert ("token_usage", "no_explicit_attribution") in reasons(body)
    assert ("milestone.actual", "no_explicit_finish_evidence") not in reasons(body)
    assert ("work.depends_on", "no_work_dependency_contract") not in reasons(body)
    assert not any(node["kind"] == "issue" for node in nodes(body))


async def test_recorded_lines_keep_sessions_under_work_and_drop_unscoped_rows(ops):
    built = await lined(ops)
    wid, work, plan = built["wid"], built["work"], built["plan"]
    pid = work_items._get_item(ops.db, work["work_item_id"])["project_id"]
    extra = await item(ops, pid, title="Unscoped extra")
    await bind_work(ops, {"workspace_id": wid, "work_item_id": extra})
    changes = ops.db.total_changes
    body = check(schedule(ops, wid), wid)
    assert ops.db.total_changes == changes
    found = by_id(body)
    milestone_id = "milestone:release"
    requirement_id = f"requirement:{plan['revision_id']}:login"
    work_id = f"work:{work['work_item_id']}"
    task_id = f"attempt:task:{work['work_item_id']}:{built['task']['task_id']}"
    session_id = f"attempt:session:{work['work_item_id']}:{built['session']}"
    assert set(found) == {milestone_id, requirement_id, work_id, task_id, session_id}
    point = found[milestone_id]
    assert point in body["rows"] and point["date_kind"] == "milestone" and point["parent_id"] is None
    assert point["baseline"] == {"start": "2026-11-01", "end": "2026-11-01"}
    assert point["forecast"] == {"start": "2026-11-08", "end": "2026-11-08"}
    assert point["actual"] is None and point["depends_on"] == [] and point["status"] == "unknown"
    assert body["baseline"]["revision_id"] == built["baseline"]["revision_id"]
    assert body["baseline"]["set_by"] == OWNER.actor and ISO.fullmatch(body["baseline"]["set_at"])
    assert body["forecast"]["revision_id"].startswith("pmrf_") and "set_by" not in body["forecast"]
    requirement = found[requirement_id]
    assert requirement in body["unscheduled"] and "baseline" not in requirement
    assert requirement["parent_id"] == milestone_id and requirement["title"] == "Members can log in"
    assert requirement["depends_on"] == [] and requirement["refs"]["milestone_id"] == "release"
    assert json.dumps(body).count("Members can log in") == 1
    work_row = found[work_id]
    assert work_row in body["rows"] and work_row["parent_id"] == requirement_id
    assert work_row["title"] == "A useful deliverable" and work_row["status"] == "in_progress"
    assert work_row["depends_on"] == [] and work_row["actual"]["start"] == instant(TASK_AT)
    assert work_row["actual"]["end"] is None and work_row["actual"]["open"] is True
    assert work_row["baseline"] is work_row["forecast"] is None
    attempt = found[task_id]
    assert attempt in body["rows"] and attempt["parent_id"] == work_id and attempt["title"] == built["task"]["task_id"]
    assert attempt["status"] == "in_progress" and attempt["refs"]["agent"] == {"cli": "codex"}
    assert attempt["actual"] == {"start": instant(TASK_AT), "end": None, "open": True}
    observed = found[session_id]
    assert observed in body["unscheduled"] and observed["status"] == "unknown" and observed["title"] == "Membership session"
    assert observed["refs"]["session"] == {"host": "mock-host", "session_id": "mock-session", "read_only": True}
    assert observed["refs"]["agent"] == {"model": "fixture-model"} and "actual" not in observed
    assert "1970-01-01T00:00:20.000Z" not in json.dumps(body)
    assert "acme/widget" not in json.dumps(body) and "Unscoped extra" not in json.dumps(body)
    assert work_row["status"] != "merged"
    recorded = {"2026-11-01", "2026-11-08", instant(TASK_AT)[:10]}
    assert body["range"] == {"start": min(recorded), "end": max(recorded)}
    assert body["as_of"][:10] not in recorded
    assert body["range_scope"] == "complete" and body["next_cursor"] is None and body["partial"] is False
    assert ("work.depends_on", "no_work_dependency_contract") in reasons(body)
    assert ("milestone.actual", "no_explicit_finish_evidence") in reasons(body)
    assert ("token_usage", "no_explicit_attribution") in reasons(body)
    assert ("baseline", "no_adopted_schedule_baseline") not in reasons(body)


async def test_running_session_is_not_an_interval_and_managed_sessions_stay_explicit(ops):
    built = await lined(ops)
    retitle(ops, runtime="running", access="managed", provenance="observed")
    body = check(schedule(ops, built["wid"]), built["wid"])
    observed = by_id(body)[f"attempt:session:{built['work']['work_item_id']}:{built['session']}"]
    assert observed in body["unscheduled"] and observed["status"] == "in_progress"
    assert observed["refs"]["session"]["read_only"] is False and "actual" not in observed
    work_row = by_id(body)[f"work:{built['work']['work_item_id']}"]
    assert work_row["actual"]["start"] == instant(TASK_AT) and work_row["actual"]["end"] is None


async def test_one_requirement_under_two_milestones_has_no_invented_parent(ops):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    await revision(ops, wid, milestones=[milestone(plan, "alpha"), milestone(plan, "beta", baseline_date="2026-12-01",
                                                                              depends_on=["alpha"])])
    body = check(schedule(ops, wid), wid)
    requirement = by_id(body)[f"requirement:{plan['revision_id']}:login"]
    assert requirement["parent_id"] is None and requirement["refs"]["milestone_ids"] == ["alpha", "beta"]
    assert "milestone_id" not in requirement["refs"]
    assert ("requirement.parent", "multiple_explicit_milestones") in reasons(body)
    assert json.dumps(body).count("Members can log in") == 1
    assert by_id(body)["milestone:beta"]["depends_on"] == ["milestone:alpha"]


async def test_requirement_dependencies_use_only_in_baseline_ids(ops):
    target, confirmed, assumption, acceptance = await planning(ops)
    saved = await draft(ops, target, requirements=[
        {"id": "login", "source_input_id": confirmed, "depends_on": ["billing"]},
        {"id": "billing", "source_input_id": assumption, "depends_on": ["outside"]},
        {"id": "outside", "source_input_id": acceptance}])
    adopt(adoption(ops, target, saved["revision_id"]))
    plan = {"revision_id": saved["revision_id"]}
    wid = target["workspace_id"]
    await revision(ops, wid, milestones=[milestone(plan, requirement_ids=["login", "billing"])])
    body = check(schedule(ops, wid), wid)
    login = by_id(body)[f"requirement:{saved['revision_id']}:login"]
    billing = by_id(body)[f"requirement:{saved['revision_id']}:billing"]
    assert login["depends_on"] == [billing["id"]] and billing["depends_on"] == []
    assert f"requirement:{saved['revision_id']}:outside" not in by_id(body)
    assert "Password reset restores account access" not in json.dumps(body)
    assert ("requirement.depends_on", "requirement_dependency_outside_baseline") in reasons(body)


async def test_work_with_two_requirement_parents_is_not_assigned_one(ops):
    target, confirmed, assumption, _acceptance = await planning(ops)
    saved = await draft(ops, target, requirements=[
        {"id": "login", "source_input_id": confirmed},
        {"id": "billing", "source_input_id": assumption}])
    adopt(adoption(ops, target, saved["revision_id"]))
    plan = {"revision_id": saved["revision_id"]}
    wid = target["workspace_id"]
    work = await work_for(ops, target)
    await bind_work(ops, work, plan_revision_id=saved["revision_id"], requirement_ids=["login", "billing"])
    await revision(ops, wid, milestones=[milestone(plan, "alpha", requirement_ids=["login"]),
                                         milestone(plan, "beta", requirement_ids=["billing"], baseline_date="2026-12-01")])
    body = check(schedule(ops, wid), wid)
    row = by_id(body)[f"work:{work['work_item_id']}"]
    assert row in body["unscheduled"] and row["parent_id"] is None and row["depends_on"] == []
    assert ("work.parent", "multiple_explicit_parents") in reasons(body)
    assert ("work.depends_on", "no_work_dependency_contract") in reasons(body)


async def test_undated_milestone_keeps_its_dependency_and_a_forecast_point_is_explicit(ops):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    await revision(ops, wid, milestones=[milestone(plan, "alpha"),
                                         milestone(plan, "bare", baseline_date=None, depends_on=["alpha"]),
                                         milestone(plan, "point", baseline_date=None)])
    await revision(ops, wid, "forecast", updates=[{"milestone_id": "point", "forecast_date": "2026-11-08"}])
    body = check(schedule(ops, wid), wid)
    found = by_id(body)
    bare = found["milestone:bare"]
    assert bare in body["unscheduled"] and "baseline" not in bare and "date_kind" not in bare
    assert bare["depends_on"] == ["milestone:alpha"] and bare["parent_id"] is None
    point = found["milestone:point"]
    assert point in body["rows"] and point["date_kind"] == "milestone"
    assert point["baseline"] is None and point["forecast"] == {"start": "2026-11-08", "end": "2026-11-08"}
    assert point["actual"] is None
    assert body["range"] == {"start": "2026-11-01", "end": "2026-11-08"}


async def test_replaced_baseline_drops_the_previous_forecast(ops):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    await revision(ops, wid, milestones=[milestone(plan)])
    await revision(ops, wid, "forecast", updates=[{"milestone_id": "release", "forecast_date": "2026-11-08"}])
    await revision(ops, wid, milestones=[milestone(plan, baseline_date="2027-01-15")])
    body = check(schedule(ops, wid), wid)
    assert body["forecast"] is None
    row = by_id(body)["milestone:release"]
    assert row["baseline"] == {"start": "2027-01-15", "end": "2027-01-15"} and row["forecast"] is None
    assert "2026-11-08" not in json.dumps(body)


async def test_observation_does_not_finish_a_milestone(ops):
    target, *_ = await mapped(ops)
    wid = target["workspace_id"]
    snap = await snapshot(ops, wid)
    await revision(ops, wid, "observation", snapshot_id=snap["snapshot_id"], who=CONTRIBUTOR)
    body = check(schedule(ops, wid, READER), wid)
    assert by_id(body)["milestone:release"]["actual"] is None
    assert ("milestone.actual", "no_explicit_finish_evidence") in reasons(body)
    assert "actual_finish_date" not in json.dumps(body)


async def test_pages_do_not_overlap_or_pretend_to_be_a_total(ops):
    built = await lined(ops)
    wid = built["wid"]
    full = schedule(ops, wid)
    first = check(schedule(ops, wid, limit=1), wid)
    assert first["partial"] is first["truncated"] is True and first["range_scope"] == "returned_page"
    assert first["next_cursor"] == pm_schedule._encode_cursor("milestone:release")
    assert [row["id"] for row in first["rows"]] == ["milestone:release"]
    assert first["unscheduled"] == [] and instant(TASK_AT)[:10] not in json.dumps(first["range"])
    assert "total" not in first
    second = schedule(ops, wid, after=first["next_cursor"], limit=100)
    assert {node["id"] for node in nodes(first)} & {node["id"] for node in nodes(second)} == set()
    assert {node["id"] for node in nodes(full)} == {node["id"] for node in nodes(first)} | {node["id"] for node in nodes(second)}
    assert full["next_cursor"] is None and full["range_scope"] == "complete"
    assert full["range"]["start"] == min("2026-11-01", instant(TASK_AT)[:10])
    with pytest.raises(OperationError, match="INVALID_PARAMS") as bad_limit:
        schedule(ops, wid, limit=101)
    assert bad_limit.value.status == 422
    with pytest.raises(OperationError, match="after must be a schedule cursor"):
        schedule(ops, wid, after="phase:a")
    for value in (True, False, 0, "1"):
        with pytest.raises(OperationError, match="limit must be 1-100"):
            schedule(ops, wid, limit=value)
    absent = schedule(ops, wid, after=pm_schedule._encode_cursor("milestone:not-a-real-row"))
    assert "milestone:release" in by_id(absent)
    with pytest.raises(OperationError, match="Unknown PM read parameters"):
        pm_reads.read(ops, OWNER, "pm_schedule_get", {"workspace_id": wid, "refresh": True})


async def test_membership_and_scope_hide_the_projection(ops):
    built = await lined(ops)
    wid = built["wid"]
    with pytest.raises(OperationError, match="NOT_FOUND") as missing:
        schedule(ops, wid, OUTSIDER)
    assert missing.value.status == 404
    assert "Usable membership" not in str(missing.value) and "A useful deliverable" not in str(missing.value)
    with pytest.raises(OperationError, match="FORBIDDEN") as forbidden:
        schedule(ops, wid, Principal(OWNER.actor, frozenset({"manage"})))
    assert forbidden.value.status == 403
    owner = stable(schedule(ops, wid))
    reader = stable(schedule(ops, wid, READER))
    assert reader == owner


def attach(service, work_id, kind, ref):
    service.db.execute("""INSERT INTO work_item_links(work_item_id,kind,ref,linked_by,linked_at,link_operation)
        VALUES(?,?,?,?,?,?)""", (work_id, kind, ref, OWNER.actor, 1.0, "op_" + "ef" * 16))


def ordered_ids(body):
    rank = {"milestone": 0, "requirement": 1, "issue": 2, "work": 3, "attempt": 4}
    return [node["id"] for node in sorted(nodes(body), key=lambda node: (rank[node["kind"]], node["id"]))]


async def test_byte_cap_resumes_after_the_last_returned_row(ops, monkeypatch):
    built = await lined(ops)
    wid = built["wid"]
    full = schedule(ops, wid)
    expected = ordered_ids(full)
    assert len(expected) >= 3
    full_size = len(json.dumps(full, ensure_ascii=False).encode())
    # One byte under the complete page. A single row still fits; the whole page must be cut.
    monkeypatch.setattr(pm_schedule, "MAX_BYTES", full_size - 1)
    builds = {"n": 0}
    original = pm_schedule._body

    def wrapped(*args, **kwargs):
        builds["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pm_schedule, "_body", wrapped)
    seen, cursor, pages = [], None, 0
    while pages < 20:
        body = check(schedule(ops, wid, after=cursor), wid)
        assert len(json.dumps(body, ensure_ascii=False).encode()) <= pm_schedule.MAX_BYTES
        page = ordered_ids(body)
        assert page == expected[len(seen):len(seen) + len(page)]
        seen.extend(page)
        pages += 1
        if body["next_cursor"] is None:
            break
        assert body["partial"] is True and body["truncated"] is True and body["range_scope"] == "returned_page"
        assert body["next_cursor"] == pm_schedule._encode_cursor(page[-1])
        cursor = body["next_cursor"]
    assert seen == expected and pages >= 2
    assert builds["n"] == pages
    monkeypatch.setattr(pm_schedule, "MAX_BYTES", 80)
    with pytest.raises(OperationError, match="PM_SCHEDULE_TOO_LARGE") as too_big:
        schedule(ops, wid)
    assert too_big.value.status == 413


async def test_missing_operation_and_task_records_stay_partial_without_ids(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid, work_id = target["workspace_id"], work["work_item_id"]
    present = await source_operation(ops, action="fixture.present")
    await link(ops, work, "operation", present["operation_id"])
    missing_op = "op_" + "ab" * 16
    missing_task = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    attach(ops, work_id, "operation", missing_op)
    attach(ops, work_id, "task", missing_task)
    body = check(schedule(ops, wid), wid)
    raw = json.dumps(body)
    assert missing_op not in raw and missing_task not in raw
    assert present["operation_id"] in raw
    assert body["coverage"]["attempts_unavailable"] is True
    assert body["coverage"]["visibility_filtered"] is False
    assert body["partial"] is True and body["truncated"] is True
    assert body["next_cursor"] is None and body["range_scope"] == "returned_page"
    assert ("attempt.operation", "operation_record_missing") in reasons(body)
    assert ("attempt.task", "task_record_missing") in reasons(body)


async def test_visible_unobserved_session_stays_an_unknown_attempt(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid, work_id = target["workspace_id"], work["work_item_id"]
    visible = session(ops, "pending-session")
    hidden = session(ops, "hidden-unobserved")
    retitle(ops, "pending-session", access="managed", provenance="observed", runtime="running")
    await link(ops, work, "session", visible)
    await link(ops, work, "session", hidden)
    ops.db.execute("DELETE FROM sessions_observed WHERE session_id IN ('pending-session','hidden-unobserved')")
    withdraw(ops, "session", hidden)
    body = check(schedule(ops, wid), wid)
    row = by_id(body)[f"attempt:session:{work_id}:{visible}"]
    assert row["status"] == "unknown" and row["title"] == "pending-session"
    assert row["parent_id"] == f"work:{work_id}" and row["depends_on"] == [] and row["waiting"] is None
    assert row["refs"]["session"] == {"host": "mock-host", "session_id": "pending-session", "read_only": True}
    assert "baseline" not in row and "forecast" not in row and "actual" not in row
    assert row in body["unscheduled"]
    assert ("attempt.session", "session_not_observed") in reasons(body)
    assert "hidden-unobserved" not in json.dumps(body)
    assert body["coverage"]["visibility_filtered"] is True
    assert "managed" not in json.dumps(row)


async def test_withdrawn_sources_leave_with_the_dates_they_produced(ops):
    target, _plan, work, _binding, _selected, _baseline = await mapped(ops)
    wid = target["workspace_id"]
    early = await source_operation(ops, action="fixture.early")
    late = await source_operation(ops, action="fixture.late")
    await link(ops, work, "operation", early["operation_id"])
    await link(ops, work, "operation", late["operation_id"])
    ops.db.execute("UPDATE operations SET created_at=? WHERE operation_id=?", (EARLY_AT, early["operation_id"]))
    ops.db.execute("UPDATE operations SET created_at=? WHERE operation_id=?", (LATE_AT, late["operation_id"]))
    before = check(schedule(ops, wid), wid)
    assert by_id(before)[f"work:{work['work_item_id']}"]["actual"]["start"] == instant(EARLY_AT)
    assert early["operation_id"] in json.dumps(before) and before["coverage"]["visibility_filtered"] is False
    withdraw(ops, "operation", early["operation_id"])
    hidden = check(schedule(ops, wid), wid)
    assert early["operation_id"] not in json.dumps(hidden)
    assert by_id(hidden)[f"work:{work['work_item_id']}"]["actual"]["start"] == instant(LATE_AT)
    assert hidden["coverage"]["visibility_filtered"] is True
    assert stable(schedule(ops, wid)) == stable(hidden)
    ref = session(ops, "hidden-session")
    await link(ops, work, "session", ref)
    visible = schedule(ops, wid)
    assert "hidden-session" in json.dumps(visible)
    withdraw(ops, "session", ref)
    gone = check(schedule(ops, wid), wid)
    assert "hidden-session" not in json.dumps(gone)
    assert by_id(gone)[f"work:{work['work_item_id']}"]["actual"]["start"] == instant(LATE_AT)
    assert stable(schedule(ops, wid)) == stable(gone)


async def test_truncated_attempt_links_do_not_publish_a_work_interval(ops, monkeypatch):
    target, _plan, work, *_ = await mapped(ops)
    wid = target["workspace_id"]
    first = await source_operation(ops, action="fixture.early")
    second = await source_operation(ops, action="fixture.late")
    await link(ops, work, "operation", first["operation_id"])
    await link(ops, work, "operation", second["operation_id"])
    monkeypatch.setattr(pm_schedule, "MAX_LINKS_TOTAL", 1)
    body = check(schedule(ops, wid), wid)
    attempts = [node for node in nodes(body) if node["kind"] == "attempt"]
    assert len(attempts) == 1 and attempts[0].get("actual")
    work_row = by_id(body)[f"work:{work['work_item_id']}"]
    assert work_row in body["unscheduled"] and "actual" not in work_row
    assert body["coverage"]["attempts_truncated"] is True and body["partial"] is True
    assert body["next_cursor"] is None and body["range_scope"] == "returned_page"
    assert ("work.actual", "attempt_links_truncated") in reasons(body)
    assert work_row["status"] != "not_started"


async def test_approval_ends_work_without_using_the_session_clock(ops):
    built = await lined(ops)
    approve(ops, built["work"]["work_item_id"], APPROVED_AT)
    body = check(schedule(ops, built["wid"]), built["wid"])
    work_row = by_id(body)[f"work:{built['work']['work_item_id']}"]
    assert work_row["status"] == "accepted"
    assert work_row["actual"]["end"] == instant(APPROVED_AT) and work_row["actual"]["open"] is False
    assert work_row["actual"]["start"] == instant(TASK_AT)
    session_row = by_id(body)[f"attempt:session:{built['work']['work_item_id']}:{built['session']}"]
    assert session_row in body["unscheduled"] and "actual" not in session_row


async def test_verified_merge_ends_work_and_a_pull_request_link_does_not(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid = target["workspace_id"]
    await link(ops, work, "pull_request", "acme/widget#12")
    operation = await source_operation(ops, action="github.pr.merge")
    await link(ops, work, "operation", operation["operation_id"])
    ops.db.execute("UPDATE operations SET created_at=? WHERE operation_id=?", (EARLY_AT, operation["operation_id"]))
    before = check(schedule(ops, wid), wid)
    assert by_id(before)[f"work:{work['work_item_id']}"]["status"] != "merged"
    assert (by_id(before)[f"work:{work['work_item_id']}"].get("actual") or {}).get("end") is None
    assert "acme/widget" not in json.dumps(before)
    merge_receipt(ops, operation["operation_id"], MERGE_AT)
    after = check(schedule(ops, wid), wid)
    row = by_id(after)[f"work:{work['work_item_id']}"]
    assert row["status"] == "merged" and row["actual"]["end"] == instant(MERGE_AT)
    assert row["actual"]["start"] == instant(EARLY_AT) and row["actual"]["open"] is False


async def test_verified_deployment_does_not_end_the_bar(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid = target["workspace_id"]
    operation = await source_operation(ops, action="deployment.start")
    await link(ops, work, "operation", operation["operation_id"])
    ops.db.execute("UPDATE operations SET created_at=? WHERE operation_id=?", (EARLY_AT, operation["operation_id"]))
    deployment(ops, operation["operation_id"], 1)
    unverified = by_id(schedule(ops, wid))[f"work:{work['work_item_id']}"]
    assert unverified["status"] != "deployed"
    ops.db.execute("UPDATE deployments SET document=? WHERE operation_id=?",
                   (json.dumps({"verified": True}), operation["operation_id"]))
    body = check(schedule(ops, wid), wid)
    row = by_id(body)[f"work:{work['work_item_id']}"]
    assert row["status"] == "deployed" and row["actual"]["start"] == instant(EARLY_AT)
    assert row["actual"]["end"] is None and row["actual"]["open"] is True


async def test_waiting_uses_the_state_event_and_blocked_stays_unknown(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid, work_id = target["workspace_id"], work["work_item_id"]
    mark_waiting(ops, work_id)
    missing = check(schedule(ops, wid), wid)
    row = by_id(missing)[f"work:{work_id}"]
    assert row["status"] == "waiting" and row["waiting"] == {"reason": "work_state", "since": None}
    assert row["blocked"] is None
    assert ("waiting.since", "state_time_unrecorded") in reasons(missing)
    assert instant(9_000_000_000) not in json.dumps(missing)
    mark_waiting(ops, work_id, WAITING_AT)
    recorded = check(schedule(ops, wid), wid)
    assert by_id(recorded)[f"work:{work_id}"]["waiting"]["since"] == instant(WAITING_AT)
    assert ("waiting.since", "state_time_unrecorded") not in reasons(recorded)


async def test_http_rpc_and_mcp_read_the_same_empty_schedule(served, mock, monkeypatch):
    daemon_under_test, port = served
    workspace, _thread = await seed(daemon_under_test)
    wid = workspace["workspace_id"]
    auth = token(daemon_under_test, API_OWNER.actor, *API_OWNER.scopes)
    path = f"/api/v1/pm/workspaces/{wid}/schedule"
    changes = daemon_under_test.journal.db.total_changes
    status, body = await http(port, "GET", path, tok=auth)
    assert status == 200
    check(body, wid)
    assert body["rows"] == [] and body["baseline"] is None
    direct = pm_reads.read(daemon_under_test.ops, API_OWNER, "pm_schedule_get", {"workspace_id": wid})
    rpc = await daemon_under_test.call_api("pm_schedule_get", {"workspace_id": wid}, API_OWNER)
    assert stable(direct) == stable(body) == stable(rpc)
    assert daemon_under_test.journal.db.total_changes == changes
    assert (await http(port, "GET", path + "?refresh=1", tok=auth))[0] == 422
    assert (await http(port, "GET", path + "?limit=101", tok=auth))[0] == 422
    assert (await http(port, "GET", path + "?limit=1&limit=2", tok=auth))[0] == 422
    assert (await http(port, "GET", path + "?after=phase:a", tok=auth))[0] == 422
    assert (await http(port, "POST", path, tok=auth, body={}))[0] in (404, 405)
    cursor = pm_schedule._encode_cursor("milestone:release")
    paged, page = await http(port, "GET", path + "?limit=1&after=" + cursor, tok=auth)
    assert paged == 200 and page["rows"] == [] and page["unscheduled"] == []
    outsider = await http(port, "GET", path, tok=token(daemon_under_test, OTHER.actor, *OTHER.scopes))
    assert outsider[0] == 404 and "rows" not in outsider[1] and "unscheduled" not in outsider[1]
    assert (await http(port, "GET", path, tok=token(daemon_under_test, API_OWNER.actor, "manage")))[0] == 403
    caps = (await http(port, "GET", "/api/v1/capabilities", tok=auth))[1]
    assert caps["features"]["pm_schedule"]["version"] == 1
    assert caps["features"]["pm_schedule"]["critical_path"] is False
    assert caps["features"]["pm_schedule"]["forecast"] == "recorded_revision_only"
    await grant(daemon_under_test, wid, OTHER.actor, "reader")
    assert (await http(port, "GET", path, tok=token(daemon_under_test, OTHER.actor, *OTHER.scopes)))[0] == 200
    await grant(daemon_under_test, wid, OTHER.actor, None)
    assert (await http(port, "GET", path, tok=token(daemon_under_test, OTHER.actor, *OTHER.scopes)))[0] == 404
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(daemon_under_test.fleet.config, read_only=True, principal_only=True)
    try:
        names = {tool.name for tool in await server.list_tools()}
        assert "pm_schedule_get" in names
        payload = unpack(await server.call_tool("pm_schedule_get", {"workspace_id": wid, "limit": 1}))
        assert stable(payload) == stable(pm_reads.read(daemon_under_test.ops, API_OWNER, "pm_schedule_get",
                                                       {"workspace_id": wid, "limit": 1}))
        assert not write_frames(mock)
    finally:
        await fleet.close()


def exposed_cursor(body):
    cursor = body.get("next_cursor") if isinstance(body, dict) else None
    if not cursor:
        return ""
    padded = cursor + "=" * (-len(cursor) % 4)
    return base64.urlsafe_b64decode(padded.encode()).decode()


def barrier(service, principal, params):
    pm_source_lineage.read_request(service.db, principal, params)
    result = pm_reads.read(service, principal, "pm_schedule_get", dict(params))
    return pm_source_lineage.read_response(service.db, principal, result)


def walk_ids(service, wid, *, limit=100, principal=OWNER):
    seen, cursor, pages = [], None, 0
    while pages < 30:
        body = check(schedule(service, wid, principal, after=cursor, limit=limit), wid)
        page = ordered_ids(body)
        assert page, "an empty page with a cursor would loop"
        seen.extend(page)
        pages += 1
        if body["next_cursor"] is None:
            return seen, pages
        assert body["next_cursor"] == pm_schedule._encode_cursor(page[-1])
        cursor = body["next_cursor"]
    raise AssertionError("schedule walk did not finish")


async def test_hidden_work_is_absent_on_http_rpc_and_mcp(served, mock, monkeypatch):
    daemon_under_test, port = served
    # The served daemon admits adoption only with a bound worktree. This read test uses the fixture adopter.
    production = daemon_under_test.ops.actions.pop("pm.plan.adopt", None)
    try:
        target, _plan, work, *_ = await mapped(daemon_under_test.ops)
    finally:
        if production is not None:
            daemon_under_test.ops.actions["pm.plan.adopt"] = production
    wid, work_id = target["workspace_id"], work["work_item_id"]
    visible = await source_operation(daemon_under_test.ops)
    await link(daemon_under_test.ops, work, "operation", visible["operation_id"])
    withdraw(daemon_under_test.ops, "work_item", work_id)
    direct = check(schedule(daemon_under_test.ops, wid), wid)
    raw = json.dumps(direct)
    assert work_id not in raw and work_id not in exposed_cursor(direct)
    assert visible["operation_id"] not in raw
    assert direct["coverage"]["visibility_filtered"] is True
    assert "milestone:release" in by_id(direct)
    auth = token(daemon_under_test, OWNER.actor, *OWNER.scopes)
    path = f"/api/v1/pm/workspaces/{wid}/schedule"
    after = pm_schedule._encode_cursor("work:" + work_id[:-1] + "0")
    statuses = []
    for suffix in ("", "?limit=1", "?after=" + after + "&limit=1"):
        status, body = await http(port, "GET", path + suffix, tok=auth)
        statuses.append(status)
        assert status == 200 and work_id not in json.dumps(body) and work_id not in exposed_cursor(body)
    rpc_default = await daemon_under_test.call_api("pm_schedule_get", {"workspace_id": wid}, OWNER)
    rpc_after = await daemon_under_test.call_api(
        "pm_schedule_get", {"workspace_id": wid, "after": after, "limit": 1}, OWNER)
    assert statuses == [200, 200, 200]
    assert work_id not in json.dumps(rpc_default) and work_id not in exposed_cursor(rpc_default)
    assert work_id not in json.dumps(rpc_after) and work_id not in exposed_cursor(rpc_after)
    assert stable(rpc_default) == stable(direct)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(daemon_under_test.fleet.config, read_only=True, principal_only=True)
    try:
        payload = unpack(await server.call_tool("pm_schedule_get", {"workspace_id": wid}))
        paged = unpack(await server.call_tool("pm_schedule_get", {"workspace_id": wid, "after": after, "limit": 1}))
        assert stable(payload) == stable(direct)
        assert work_id not in json.dumps(paged) and work_id not in exposed_cursor(paged)
        assert work_id not in exposed_cursor(payload)
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_hidden_work_cannot_be_recovered_from_status_codes(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid, work_id = target["workspace_id"], work["work_item_id"]
    visible = await source_operation(ops)
    await link(ops, work, "operation", visible["operation_id"])
    withdraw(ops, "work_item", work_id)
    seen = set()
    for probe in (None, pm_schedule._encode_cursor("work:z"), pm_schedule._encode_cursor(f"work:{work_id}")):
        params = {"workspace_id": wid, "limit": 1}
        if probe is not None:
            params["after"] = probe
        body = barrier(ops, OWNER, params)
        seen.add(200)
        assert work_id not in json.dumps(body)
    assert seen == {200}


async def plan_with(ops, values, requirements):
    target = await prepared(ops)
    ids = []
    for index, value in enumerate(values):
        made = await act(ops, target, "pm.input.record", {"slot": "scope", "value": value, "status": "user_confirmed",
                                                          "source_ref": {"kind": "statement", "ref": f"statement {index}"}})
        ids.append(made["input_id"])
    await act(ops, target, "pm.thread.plan", {"selected_input_ids": ids, "rationale": "Plan these"})
    entries = [{**{key: value for key, value in entry.items() if key != "source"}, "source_input_id": ids[entry["source"]]}
               for entry in requirements]
    saved = await draft(ops, target, requirements=entries)
    adopt(adoption(ops, target, saved["revision_id"]))
    return target, saved


def rewrite_requirement(service, revision_id, requirement_id, text):
    """Blank one stored requirement. Plan rows are immutable, so the trigger is lifted for this rewrite only."""
    document = json.loads(service.db.execute(
        "SELECT document FROM pm_plan_revisions WHERE revision_id=?", (revision_id,)).fetchone()[0])
    for requirement in document["requirements"]:
        if requirement["id"] == requirement_id:
            requirement["text"] = text
    raw = json.dumps(document)
    digest = pm_plans._files(revision_id, json.loads(raw))["document_digest"]
    service.db.execute("DROP TRIGGER IF EXISTS pm_plan_revisions_update_immutable")
    service.db.execute("UPDATE pm_plan_revisions SET document=?, document_digest=? WHERE revision_id=?",
                       (raw, digest, revision_id))
    service.db.execute("CREATE TRIGGER pm_plan_revisions_update_immutable BEFORE UPDATE ON pm_plan_revisions "
                       "BEGIN SELECT RAISE(ABORT, 'PM plan history is immutable'); END")


@pytest.mark.parametrize("text", ["Members can log in\nwith email and password", "/login redirects members home",
                                  "Members\tcan log in"])
async def test_requirement_text_is_kept_and_edges_name_emitted_rows(ops, text):
    target, saved = await plan_with(ops, [text, "Billing works"], [
        {"id": "login", "source": 0}, {"id": "billing", "source": 1, "depends_on": ["login"]}])
    wid, rev = target["workspace_id"], saved["revision_id"]
    work = await work_for(ops, target)
    await bind_work(ops, work, plan_revision_id=rev, requirement_ids=["login"])
    await revision(ops, wid, milestones=[milestone({"revision_id": rev}, requirement_ids=["login", "billing"])])
    body = check(schedule(ops, wid), wid)
    found = by_id(body)
    login, billing = f"requirement:{rev}:login", f"requirement:{rev}:billing"
    assert found[login]["title"] == text
    assert found[billing]["depends_on"] == [login]
    assert login in found and found[f"work:{work['work_item_id']}"]["parent_id"] == login
    assert ("requirement", "requirement_text_unavailable") not in reasons(body)
    assert body["partial"] is False and body["range_scope"] == "complete"


async def test_missing_requirement_text_still_emits_the_row(ops):
    target, saved = await plan_with(ops, ["Members can log in", "Billing works"], [
        {"id": "login", "source": 0}, {"id": "billing", "source": 1, "depends_on": ["login"]}])
    wid, rev = target["workspace_id"], saved["revision_id"]
    rewrite_requirement(ops, rev, "login", " \n\t ")
    work = await work_for(ops, target)
    await bind_work(ops, work, plan_revision_id=rev, requirement_ids=["login"])
    await revision(ops, wid, milestones=[milestone({"revision_id": rev}, requirement_ids=["login", "billing"])])
    body = check(schedule(ops, wid), wid)
    found = by_id(body)
    login = f"requirement:{rev}:login"
    assert found[login]["title"] == "login"
    assert found[f"requirement:{rev}:billing"]["depends_on"] == [login]
    assert found[f"work:{work['work_item_id']}"]["parent_id"] == login
    assert ("requirement", "requirement_text_unavailable") in reasons(body)
    assert body["partial"] is False and body["range_scope"] == "complete"


async def test_lineage_root_budget_pages_before_64_roots(ops, monkeypatch):
    monkeypatch.setattr(pm_package_handoff, "source_visible", lambda db, principal, op: True)
    target, plan, work, *_ = await mapped(ops)
    wid = target["workspace_id"]
    pid = work_items._get_item(ops.db, work["work_item_id"])["project_id"]
    second = {"workspace_id": wid, "work_item_id": await item(ops, pid, title="Second deliverable")}
    await bind_work(ops, second, plan_revision_id=plan["revision_id"], requirement_ids=["login"])
    pm_source_lineage.schema(ops.journal)
    for owner in (work, second):
        for _ in range(35):
            operation = await source_operation(ops, action="fixture.root")
            ops.db.execute("INSERT INTO pm_source_lineage VALUES('operation',?,?,?)",
                           (operation["operation_id"], operation["operation_id"], operation["operation_id"]))
            attach(ops, owner["work_item_id"], "operation", operation["operation_id"])
    seen, pages = walk_ids(ops, wid, limit=100)
    assert pages >= 2
    assert len([row_id for row_id in seen if row_id.startswith("attempt:")]) == 70
    assert seen == walk_ids(ops, wid, limit=50)[0]
    assert barrier(ops, OWNER, {"workspace_id": wid})["next_cursor"]


async def test_links_to_missing_records_make_the_work_unknown(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid, work_id = target["workspace_id"], work["work_item_id"]
    missing = "op_" + "ab" * 16
    attach(ops, work_id, "operation", missing)
    body = check(schedule(ops, wid), wid)
    row = by_id(body)[f"work:{work_id}"]
    assert row["status"] == "unknown" and "actual" not in row
    assert body["coverage"]["attempts_unavailable"] is True
    assert missing not in json.dumps(body)
    assert ("attempt.operation", "operation_record_missing") in reasons(body)


def issue_row(mapping_id, state, selected, relation, issue):
    return {"mapping_id": mapping_id, "document": {
        "state": state, "issue": issue, "selected": selected, "relation": relation}}


def insert_revision(service, wid, mapping_id, work_id, document, revision_id, operation_id):
    service.db.execute("""INSERT INTO pm_issue_revisions(revision_id,mapping_id,workspace_id,work_item_id,
        previous_revision_id,document,document_digest,operation_id,created_at,actor)
        VALUES(?,?,?,?,NULL,?,?,?,?,?)""",
        (revision_id, mapping_id, wid, work_id, json.dumps(document), "d" * 64, operation_id, 1.0, OWNER.actor))


async def test_issue_rows_keep_titles_cancelled_state_and_plan_revision(ops, monkeypatch):
    target, plan, work, binding, *_ = await mapped(ops)
    wid = target["workspace_id"]
    pm_issue_mappings.schema(ops.journal)
    issue = {"id": 1, "number": 7, "repository": "o/r", "repository_id": 1}
    relation = {"work_item_id": work["work_item_id"], "binding_id": binding["binding_id"],
                "requirement_ids": ["login"], "repository_id": 1, "repository": "o/r"}
    other = "pmwb_" + "a" * 32
    ops.db.execute("""INSERT INTO pm_work_bindings(binding_id,workspace_id,work_item_id,project_binding_id,project_id,
        work_version,previous_binding_id,plan_revision_id,requirement_ids,rationale,actor,created_at,operation_id)
        SELECT ?,workspace_id,work_item_id,project_binding_id,project_id,work_version,NULL,?,requirement_ids,rationale,
        actor,created_at,? FROM pm_work_bindings WHERE binding_id=?""",
        (other, "pmrev_" + "b" * 32, "op_" + "ab" * 16, binding["binding_id"]))
    cancelled_id, slash_id, foreign_id = "pmim_" + "d" * 32, "pmim_" + "e" * 32, "pmim_" + "f" * 32
    insert_revision(ops, wid, cancelled_id, work["work_item_id"],
                    {"selected": {"public_title": "Login fell over\nagain"}}, "pmir_" + "1" * 32, "op_" + "11" * 16)
    foreign = {**relation, "binding_id": other}
    mappings = [
        issue_row(cancelled_id, "cancelled", {"rationale": "Stop tracking"}, relation, issue),
        issue_row(slash_id, "tracking", {"public_title": "/login returns 500", "public_body": "x"}, relation, issue),
        issue_row(foreign_id, "tracking", {"public_title": "Other plan"}, foreign, issue),
    ]
    monkeypatch.setattr(pm_issue_mappings, "list_mappings",
                        lambda ops, principal, workspace_id, *, after=0, limit=50: {
                            "mappings": mappings, "next_cursor": None,
                            "coverage": {"visibility_filtered": False, "has_more": False}})
    body = check(schedule(ops, wid), wid)
    found = by_id(body)
    assert found[f"issue:{cancelled_id}"]["title"] == "Login fell over\nagain"
    assert found[f"issue:{cancelled_id}"]["refs"]["tracking_state"] == "cancelled"
    assert found[f"issue:{cancelled_id}"]["parent_id"] == "milestone:release"
    assert found[f"issue:{slash_id}"]["title"] == "/login returns 500"
    assert found[f"issue:{foreign_id}"]["parent_id"] is None
    withdraw(ops, "work_item", work["work_item_id"])
    hidden = check(schedule(ops, wid), wid)
    assert work["work_item_id"] not in json.dumps(hidden)
    assert hidden["coverage"]["visibility_filtered"] is True
    assert not any(node["kind"] == "issue" for node in nodes(hidden))


def _tracked_issue(service, wid, work_id, binding, title, *, mapping_id, package_id, revision_id, operation_id):
    """Insert one digest-valid package and issue revision that list_mappings can read."""
    row = service.db.execute("SELECT * FROM pm_work_bindings WHERE workspace_id=? AND binding_id=?",
                             (wid, binding["binding_id"])).fetchone()
    parsed = {**dict(row), "requirement_ids": json.loads(row["requirement_ids"])}
    plan = pm_plans.get_plan(service, OWNER, wid, row["plan_revision_id"])["plan"]
    plan_ref = pm_work_packages._plan_ref(plan)
    item_row = work_items._get_item(service.db, work_id)
    project = work_items._get_project(service.db, item_row["project_id"])
    server_id = service.db.execute("SELECT server_id FROM pm_workspaces WHERE workspace_id=?", (wid,)).fetchone()[0]
    context = {"server_id": server_id, "plan_ref": plan_ref,
               "work_ref": {"binding_id": binding["binding_id"], "work_item_id": work_id,
                            "project_id": item_row["project_id"],
                            "binding_document_digest": pm_work_packages._digest(parsed),
                            "work_version": item_row["version"], "work_fingerprint": work_items.fingerprint(item_row),
                            "project_version": project["version"]},
               "evidence_refs": [], "dod_ref": None, "git_binding": None}
    package_op = "op_" + package_id[-32:]
    document = {"workspace_id": wid, "package_id": package_id, "work_item_id": work_id,
                "previous_package_id": None, "operation_id": package_op, "context": context}
    pm_work_packages.schema(service.journal)
    pm_issue_mappings.schema(service.journal)
    service.db.execute("""INSERT INTO pm_work_packages(package_id,workspace_id,work_item_id,previous_package_id,
        document_digest,document,actor,created_at,operation_id) VALUES(?,?,?,?,?,?,?,?,?)""",
        (package_id, wid, work_id, None, pm_work_packages._digest(document), pm_work_packages._json(document),
         OWNER.actor, 1.0, package_op))
    issue = {"revision_id": revision_id, "mapping_id": mapping_id, "workspace_id": wid, "work_item_id": work_id,
             "operation_id": operation_id, "state": "tracking",
             "issue": {"id": 1, "number": 7, "repository": "o/r", "repository_id": 1},
             "selected": {"public_title": title},
             "relation": {"work_item_id": work_id, "binding_id": binding["binding_id"], "requirement_ids": ["login"],
                          "repository_id": 1, "repository": "o/r"},
             "package_ref": {"package_id": package_id, "document_digest": pm_work_packages._digest(document)}}
    service.db.execute("""INSERT INTO pm_issue_revisions(revision_id,mapping_id,workspace_id,work_item_id,
        previous_revision_id,document,document_digest,operation_id,created_at,actor) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (revision_id, mapping_id, wid, work_id, None, pm_work_packages._json(issue), pm_work_packages._digest(issue),
         operation_id, 1.0, OWNER.actor))


async def test_hidden_work_issue_does_not_consume_the_issue_cap(ops, monkeypatch):
    target, plan, work, binding, *_ = await mapped(ops)
    wid = target["workspace_id"]
    pid = work_items._get_item(ops.db, work["work_item_id"])["project_id"]
    hidden = {"workspace_id": wid, "work_item_id": await item(ops, pid, title="Hidden deliverable")}
    hidden_binding = await bind_work(ops, hidden, plan_revision_id=plan["revision_id"], requirement_ids=["login"])
    _tracked_issue(ops, wid, hidden["work_item_id"], hidden_binding, "Hidden issue",
                   mapping_id="pmim_" + "a" * 32, package_id="pmwp_" + "a" * 32,
                   revision_id="pmir_" + "a" * 32, operation_id="op_" + "a" * 16)
    _tracked_issue(ops, wid, work["work_item_id"], binding, "Visible issue",
                   mapping_id="pmim_" + "b" * 32, package_id="pmwp_" + "b" * 32,
                   revision_id="pmir_" + "b" * 32, operation_id="op_" + "b" * 16)
    withdraw(ops, "work_item", hidden["work_item_id"])
    monkeypatch.setattr(pm_schedule, "MAX_ISSUES", 1)
    listed = pm_issue_mappings.list_mappings(ops, OWNER, wid)
    assert [row["mapping_id"] for row in listed["mappings"]] == ["pmim_" + "a" * 32, "pmim_" + "b" * 32]
    body = check(schedule(ops, wid), wid)
    assert f"issue:pmim_{'b' * 32}" in by_id(body)
    assert f"issue:pmim_{'a' * 32}" not in by_id(body)
    assert hidden["work_item_id"] not in json.dumps(body)
    assert body["coverage"]["issues_truncated"] is False and body["partial"] is False
    assert body["coverage"]["visibility_filtered"] is True


async def test_schedule_cursor_round_trips_any_session_id(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid = target["workspace_id"]
    ref = session(ops, "mock@session")
    await link(ops, work, "session", ref)
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    seen, pages = walk_ids(ops, wid, limit=4)
    assert pages >= 2
    assert any("mock@session" in row_id for row_id in seen)
    first = schedule(ops, wid, limit=4)
    cursor = first["next_cursor"]
    assert cursor and all(char not in cursor for char in "+/=")
    resumed = schedule(ops, wid, after=cursor)
    assert ordered_ids(first) + ordered_ids(resumed) == seen


async def test_hidden_run_does_not_change_the_visible_run(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid = target["workspace_id"]
    ref = session(ops, "run-session")
    await link(ops, work, "session", ref)
    pm_runs.schema(ops.journal)
    thread = ops.db.execute("SELECT thread_id FROM pm_threads WHERE workspace_id=?", (wid,)).fetchone()[0]
    visible_run, hidden_op = "pmr_" + "1" * 32, "op_" + "2" * 32

    def run_row(generation, run, operation, binding):
        ops.db.execute("""INSERT INTO pm_runs(run_id,operation_id,workspace_id,thread_id,generation,requester,
            request_authority,execution_authority,adapter,purpose,thread_version,context,context_digest,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run, operation, wid, thread, generation, OWNER.actor, "{}", "{}", "fixture", "analysis", 1, "{}", "0" * 64, 1.0))
        ops.db.execute("INSERT INTO pm_run_state(run_id,status,session_binding,gaps,updated_at) VALUES(?,?,?,?,?)",
                       (run, "running", json.dumps(binding) if binding else None, "[]", 1.0))

    run_row(1, visible_run, "op_" + "1" * 32, {"host": "mock-host", "session_id": "run-session"})
    run_row(2, "pmr_" + "2" * 32, hidden_op, {"host": "mock-host", "session_id": "run-session"})
    withdraw(ops, "operation", hidden_op)
    body = check(schedule(ops, wid), wid)
    row = by_id(body)[f"attempt:session:{work['work_item_id']}:{ref}"]
    assert row["refs"]["run_id"] == visible_run
    assert ("attempt.run_id", "multiple_explicit_runs") not in reasons(body)
    assert body["coverage"]["visibility_filtered"] is True
    for generation in range(3, 203):
        run_row(generation, f"pmr_{generation:032x}", f"op_{generation:032x}", None)
    again = by_id(schedule(ops, wid))[f"attempt:session:{work['work_item_id']}:{ref}"]
    assert again["refs"]["run_id"] == visible_run
    assert ("attempt.run_id", "run_index_truncated") not in reasons(schedule(ops, wid))


def _run_row(service, wid, thread, generation, run, operation, binding, created):
    service.db.execute("""INSERT INTO pm_runs(run_id,operation_id,workspace_id,thread_id,generation,requester,
        request_authority,execution_authority,adapter,purpose,thread_version,context,context_digest,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run, operation, wid, thread, generation, OWNER.actor, "{}", "{}", "fixture", "analysis", 1, "{}", "0" * 64, created))
    service.db.execute("INSERT INTO pm_run_state(run_id,status,session_binding,gaps,updated_at) VALUES(?,?,?,?,?)",
                       (run, "running", json.dumps(binding), "[]", created))


async def test_older_hidden_runs_do_not_remove_the_visible_run(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid = target["workspace_id"]
    ref = session(ops, "run-session")
    await link(ops, work, "session", ref)
    pm_runs.schema(ops.journal)
    thread = ops.db.execute("SELECT thread_id FROM pm_threads WHERE workspace_id=?", (wid,)).fetchone()[0]
    bound = {"host": "mock-host", "session_id": "run-session"}
    visible_run = "pmr_" + "f" * 32
    _run_row(ops, wid, thread, 1, visible_run, "op_" + "f" * 32, bound, 9.0)
    row_id = f"attempt:session:{work['work_item_id']}:{ref}"
    for generation in (2, 3, 4):
        hidden_op = f"op_{generation:032x}"
        _run_row(ops, wid, thread, generation, f"pmr_{generation:032x}", hidden_op, bound, float(generation - 1))
        withdraw(ops, "operation", hidden_op)
    body = check(schedule(ops, wid), wid)
    row = by_id(body)[row_id]
    assert row["refs"]["run_id"] == visible_run
    assert ("attempt.run_id", "multiple_explicit_runs") not in reasons(body)
    assert ("attempt.run_id", "run_lookup_truncated") not in reasons(body)
    assert body["partial"] is False


async def test_a_second_visible_run_omits_run_id(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid = target["workspace_id"]
    ref = session(ops, "run-session")
    await link(ops, work, "session", ref)
    pm_runs.schema(ops.journal)
    thread = ops.db.execute("SELECT thread_id FROM pm_threads WHERE workspace_id=?", (wid,)).fetchone()[0]
    bound = {"host": "mock-host", "session_id": "run-session"}
    first, last = "pmr_" + "1" * 32, "pmr_" + "9" * 32
    _run_row(ops, wid, thread, 1, first, "op_" + "1" * 32, bound, 1.0)
    for generation in (2, 3):
        hidden_op = f"op_{generation:032x}"
        _run_row(ops, wid, thread, generation, f"pmr_{generation:032x}", hidden_op, bound, float(generation))
        withdraw(ops, "operation", hidden_op)
    _run_row(ops, wid, thread, 4, last, "op_" + "9" * 32, bound, 4.0)
    body = check(schedule(ops, wid), wid)
    row = by_id(body)[f"attempt:session:{work['work_item_id']}:{ref}"]
    assert "run_id" not in row["refs"]
    assert ("attempt.run_id", "multiple_explicit_runs") in reasons(body)
    assert body["partial"] is False


async def test_run_scan_bound_omits_run_id_before_a_decision(ops, monkeypatch):
    target, _plan, work, *_ = await mapped(ops)
    wid = target["workspace_id"]
    ref = session(ops, "run-session")
    await link(ops, work, "session", ref)
    pm_runs.schema(ops.journal)
    thread = ops.db.execute("SELECT thread_id FROM pm_threads WHERE workspace_id=?", (wid,)).fetchone()[0]
    bound = {"host": "mock-host", "session_id": "run-session"}
    monkeypatch.setattr(pm_schedule, "_RUN_SCAN", 1)
    _run_row(ops, wid, thread, 1, "pmr_" + "1" * 32, "op_" + "1" * 32, bound, 1.0)
    hidden_op = "op_" + "2" * 32
    _run_row(ops, wid, thread, 2, "pmr_" + "2" * 32, hidden_op, bound, 2.0)
    withdraw(ops, "operation", hidden_op)
    body = check(schedule(ops, wid), wid)
    row = by_id(body)[f"attempt:session:{work['work_item_id']}:{ref}"]
    assert "run_id" not in row["refs"]
    assert ("attempt.run_id", "multiple_explicit_runs") not in reasons(body)
    assert ("attempt.run_id", "run_lookup_truncated") in reasons(body)


async def test_a_lineage_error_hides_one_session_or_task_and_keeps_the_page(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid = target["workspace_id"]
    ref = session(ops, "busy-session")
    await link(ops, work, "session", ref)
    pm_source_lineage.schema(ops.journal)
    for index in range(65):
        original = "op_" + f"{index:032x}"
        ops.db.execute("INSERT INTO pm_source_lineage VALUES('session',?,?,?)", (ref, original, original))
    body = check(schedule(ops, wid), wid)
    assert ref not in json.dumps(body) and body["coverage"]["visibility_filtered"] is True
    operation = await source_operation(ops, action="fixture.many")
    await link(ops, work, "operation", operation["operation_id"])
    for index in range(65):
        original = "op_" + f"{index + 100:032x}"
        ops.db.execute("INSERT INTO pm_source_lineage VALUES('operation',?,?,?)",
                       (operation["operation_id"], original, original))
    hidden = check(schedule(ops, wid), wid)
    assert operation["operation_id"] not in json.dumps(hidden)
    assert hidden["coverage"]["visibility_filtered"] is True
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    ops.db.execute("UPDATE tasks SET host=?, session_id=? WHERE task_id=?", ("mock-host", "task-session", source["task_id"]))
    session(ops, "task-session")
    withdraw(ops, "session", "mock-host/task-session")
    gone = check(schedule(ops, wid), wid)
    assert source["task_id"] not in json.dumps(gone) and "task-session" not in json.dumps(gone)


async def test_scope_filter_runs_before_the_binding_cap(ops, monkeypatch):
    target, plan, *_ = await accepted(ops)
    wid = target["workspace_id"]
    older = await work_for(ops, target)
    pid = work_items._get_item(ops.db, older["work_item_id"])["project_id"]
    newer = {"workspace_id": wid, "work_item_id": await item(ops, pid, title="Newer scheduled deliverable")}
    binding = await bind_work(ops, newer, plan_revision_id=plan["revision_id"], requirement_ids=["login"])
    await revision(ops, wid, milestones=[milestone(plan, work_binding_ids=[binding["binding_id"]])])
    monkeypatch.setattr(pm_schedule, "MAX_WORK", 1)
    body = check(schedule(ops, wid), wid)
    assert f"work:{newer['work_item_id']}" in by_id(body)
    assert f"work:{older['work_item_id']}" not in by_id(body)


async def test_verifying_task_has_no_waiting_object(ops):
    target, _plan, work, *_ = await mapped(ops)
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    ops.db.execute("UPDATE tasks SET state='verifying', verifying_started_at=? WHERE task_id=?",
                   (1700000000, source["task_id"]))
    row = by_id(schedule(ops, target["workspace_id"]))[f"attempt:task:{work['work_item_id']}:{source['task_id']}"]
    assert row["status"] == "in_review" and row["waiting"] is None


async def test_waiting_since_uses_the_latest_state_event(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid, work_id = target["workspace_id"], work["work_item_id"]
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    ops.db.execute("UPDATE tasks SET state='needs_ted' WHERE task_id=?", (source["task_id"],))
    ops.db.execute("INSERT INTO events(task_id,kind,body,created_at) VALUES(?,?,?,?)",
                   (source["task_id"], "state", json.dumps({"from": "running", "to": "queued"}), 1600000000.0))
    ops.db.execute("INSERT INTO events(task_id,kind,body,created_at) VALUES(?,?,?,?)",
                   (source["task_id"], "state", json.dumps({"from": "running", "to": "needs_ted"}), 1700000000.0))
    body = check(schedule(ops, wid), wid)
    task_row = by_id(body)[f"attempt:task:{work_id}:{source['task_id']}"]
    assert task_row["waiting"] == {"reason": "human", "since": instant(1700000000)}
    assert ("waiting.since", "state_time_unrecorded") not in reasons(body)
    operation = await source_operation(ops, action="fixture.wait")
    await link(ops, work, "operation", operation["operation_id"])
    ops.db.execute("UPDATE operations SET status='waiting_external' WHERE operation_id=?", (operation["operation_id"],))
    ops.db.execute("""INSERT INTO api_events(resource_type,resource_id,kind,body,actor,created_at)
        VALUES('operation',?,'operation.waiting_external',?,?,?)""",
        (operation["operation_id"], json.dumps({"from": "running", "to": "waiting_external"}), OWNER.actor, 1700000000.0))
    waited = check(schedule(ops, wid), wid)
    op_row = by_id(waited)[f"attempt:operation:{work_id}:{operation['operation_id']}"]
    assert op_row["waiting"]["since"] == instant(1700000000)
    mark_waiting(ops, work_id, WAITING_AT)
    ops.db.execute("""INSERT INTO api_events(resource_type,resource_id,kind,body,actor,created_at)
        VALUES('work_item',?,'work_item.state',?,?,?)""",
        (work_id, json.dumps({"from": "waiting", "to": "doing"}), OWNER.actor, WAITING_AT + 10))
    stale = check(schedule(ops, wid), wid)
    assert by_id(stale)[f"work:{work_id}"]["waiting"]["since"] is None
    assert ("waiting.since", "state_time_unrecorded") in reasons(stale)


async def test_finished_attempt_note_requires_a_terminal_end(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid, work_id = target["workspace_id"], work["work_item_id"]
    ref = session(ops, "pending-session")
    await link(ops, work, "session", ref)
    ops.db.execute("DELETE FROM sessions_observed WHERE session_id='pending-session'")
    body = check(schedule(ops, wid), wid)
    assert ("work.status", "attempt_finished_is_not_work_completion") not in reasons(body)
    operation = await source_operation(ops, action="fixture.done")
    await link(ops, work, "operation", operation["operation_id"])
    ops.db.execute("UPDATE operations SET status='succeeded', created_at=? WHERE operation_id=?", (EARLY_AT, operation["operation_id"]))
    seq = ops.db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM operation_steps WHERE operation_id=?",
                         (operation["operation_id"],)).fetchone()[0]
    ops.db.execute("""INSERT INTO operation_steps(operation_id,seq,name,status,request,response,started_at,finished_at)
        VALUES(?,?, 'settle','succeeded','{}','{}',?,?)""", (operation["operation_id"], seq, EARLY_AT, LATE_AT))
    finished = check(schedule(ops, wid), wid)
    assert ("work.status", "attempt_finished_is_not_work_completion") in reasons(finished)
    assert by_id(finished)[f"work:{work_id}"]["status"] == "unknown"


async def test_failed_operation_ends_at_its_terminal_step(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid, work_id = target["workspace_id"], work["work_item_id"]
    operation = await source_operation(ops, action="fixture.fail")
    await link(ops, work, "operation", operation["operation_id"])
    ops.db.execute("UPDATE operations SET status='failed', created_at=? WHERE operation_id=?", (EARLY_AT, operation["operation_id"]))
    ops.db.execute("DELETE FROM operation_steps WHERE operation_id=?", (operation["operation_id"],))
    ops.db.execute("""INSERT INTO operation_steps(operation_id,seq,name,status,request,response,started_at,finished_at)
        VALUES(?,1,'work','succeeded','{}','{}',?,?),(?,2,'give-up','failed','{}',NULL,?,?)""",
        (operation["operation_id"], EARLY_AT, 2000, operation["operation_id"], 2000, LATE_AT))
    row = by_id(schedule(ops, wid))[f"attempt:operation:{work_id}:{operation['operation_id']}"]
    assert row["actual"]["end"] == instant(LATE_AT)
    assert row["actual"]["start"] == instant(EARLY_AT)


async def test_work_end_before_start_is_cleared(ops):
    target, _plan, work, *_ = await mapped(ops)
    wid, work_id = target["workspace_id"], work["work_item_id"]
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    ops.db.execute("UPDATE tasks SET submitted_at=? WHERE task_id=?", (LATE_AT, source["task_id"]))
    approve(ops, work_id, EARLY_AT)
    body = check(schedule(ops, wid), wid)
    row = by_id(body)[f"work:{work_id}"]
    assert row["status"] == "accepted"
    assert row["actual"]["start"] == instant(LATE_AT) and row["actual"]["end"] is None
    assert ("work.actual", "inconsistent_dates") in reasons(body)


async def test_one_row_over_the_byte_cap_is_413_and_multibyte_pages_have_no_gaps(ops, monkeypatch):
    target, plan, work, *_ = await mapped(ops)
    wid = target["workspace_id"]
    pid = work_items._get_item(ops.db, work["work_item_id"])["project_id"]
    for index in range(4):
        extra = {"workspace_id": wid, "work_item_id": await item(ops, pid, title=f"會員登入🚀{index} " * 4)}
        await bind_work(ops, extra, plan_revision_id=plan["revision_id"], requirement_ids=["login"])
    body = check(schedule(ops, wid), wid)
    expected = ordered_ids(body)
    first = by_id(body)[expected[0]]
    row_size = len(json.dumps(first, ensure_ascii=False).encode())
    monkeypatch.setattr(pm_schedule, "MAX_BYTES", row_size - 1)
    assert row_size > pm_schedule.MAX_BYTES
    with pytest.raises(OperationError, match="PM_SCHEDULE_TOO_LARGE") as too_big:
        schedule(ops, wid)
    assert too_big.value.status == 413
    full_size = len(json.dumps(body, ensure_ascii=False).encode())
    monkeypatch.setattr(pm_schedule, "MAX_BYTES", full_size - 1)
    seen, pages = walk_ids(ops, wid, limit=3)
    assert pages >= 2 and seen == expected
    cursor = None
    while True:
        page = schedule(ops, wid, after=cursor, limit=3)
        assert len(json.dumps(page, ensure_ascii=False).encode()) <= pm_schedule.MAX_BYTES
        cursor = page["next_cursor"]
        if cursor is None:
            break


async def test_schedule_read_uses_a_deferred_snapshot(ops):
    target, *_ = await mapped(ops)
    statements = []
    ops.db.set_trace_callback(statements.append)
    try:
        schedule(ops, target["workspace_id"])
    finally:
        ops.db.set_trace_callback(None)
    assert not any(line.startswith("BEGIN IMMEDIATE") for line in statements)
    assert any(line == "BEGIN" or line.startswith("BEGIN DEFERRED") for line in statements)
