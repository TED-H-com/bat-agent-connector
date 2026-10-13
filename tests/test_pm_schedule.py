"""Recorded project schedule: three time lines, no computed forecast."""
from __future__ import annotations

import json
import re

import pytest

from bat_agent_connector import mcp_server, pm_plans, pm_reads, pm_schedule, pm_source_lineage, work_items
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import daemon as api_daemon
from tests.test_api_v1 import http, token, write_frames
from tests.test_api_v1 import served as api_served
from tests.test_pm_api import OTHER, seed
from tests.test_pm_api import OWNER as API_OWNER
from tests.test_pm_discussions import CONTRIBUTOR, OUTSIDER, OWNER, READER
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
    assert first["next_cursor"] == "milestone:release" and [row["id"] for row in first["rows"]] == ["milestone:release"]
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
    with pytest.raises(OperationError, match="after must be a schedule row id"):
        schedule(ops, wid, after="phase:a")
    for value in (True, False, 0, "1"):
        with pytest.raises(OperationError, match="limit must be 1-100"):
            schedule(ops, wid, limit=value)
    absent = schedule(ops, wid, after="milestone:not-a-real-row")
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
    paged, page = await http(port, "GET", path + "?limit=1&after=milestone:release", tok=auth)
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
