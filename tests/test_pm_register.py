"""Issues, actions and changes register: identities, explicit links and honest totals."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import (
    controller_authority,
    mcp_server,
    pm_governance,
    pm_maintenance_checks,
    pm_maintenance_findings,
    pm_maintenance_policy,
    pm_maintenance_repairs,
    pm_meetings,
    pm_plans,
    pm_register,
    pm_source_lineage,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import http, token, write_frames
from tests.test_pm_discussions import CONTRIBUTOR, OUTSIDER, OWNER, READER, settle, workspace
from tests.test_pm_evidence import capture, link, task
from tests.test_pm_governance import change, fields, prepared, record
from tests.test_pm_governance import ops as ops
from tests.test_pm_governance_api import finding, send
from tests.test_pm_meetings import apply, complete, draft_params, meeting, note
from tests.test_pm_meetings import draft as meeting_draft
from tests.test_pm_plans import adopt, adoption
from tests.test_pm_plans import draft as plan_draft
from tests.test_pm_run_api import unpack
from tests.test_pm_snapshots import ops as snapshot_ops_fixture
from tests.test_pm_snapshots import snapshot, work_for
from tests.test_pm_sprint_api import pm as pm
from tests.test_pm_sprint_api import prepared as sprint_prepared
from tests.test_pm_sprint_api import served as served
from tests.test_pm_worktree import world as world

snapshot_ops = snapshot_ops_fixture


@pytest.fixture
def meeting_ops(tmp_path, monkeypatch):
    from bat_agent_connector import pm_snapshots
    from tests.test_pm_sprints import ops as sprint_ops
    generator = sprint_ops.__wrapped__(tmp_path, monkeypatch)
    service = next(generator)
    try:
        version = service.db.execute("PRAGMA user_version").fetchone()[0]
        pm_snapshots.install(service)
        pm_meetings.install(service)
        pm_meetings.install(service)
        assert service.db.execute("PRAGMA user_version").fetchone()[0] == version
        yield service
    finally:
        generator.close()

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$")
ROW_KEYS = {"row_id", "type", "title", "source", "impact", "priority", "owner", "due", "status", "next_step",
            "created_at", "updated_at", "related", "available_actions"}


def _shows(detail, kind, ident):
    return any(step.get("ref") == {"kind": kind, "id": ident} for step in detail["timeline"])


def _install_follow_up(ops, monkeypatch):
    from bat_agent_connector import pm_follow_up, pm_sprints
    from tests.test_pm_sprints import NOW
    version = ops.db.execute("PRAGMA user_version").fetchone()[0]
    pm_sprints.install(ops)
    pm_meetings.install(ops)
    pm_follow_up.install(ops)
    pm_follow_up.install(ops)
    monkeypatch.setattr(pm_sprints, "time", SimpleNamespace(time=lambda: NOW))
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == version


def _walk(value):
    if isinstance(value, dict):
        assert all(not key.startswith("_") for key in value)
        for item in value.values():
            _walk(item)
    elif isinstance(value, list):
        for item in value:
            _walk(item)


def _by_source(view, kind, ident):
    return next(row for row in view["rows"] if row["source"] == {"kind": kind, "id": ident})


def _without_clock(payload):
    body = json.loads(json.dumps(payload))
    assert ISO.fullmatch(body.pop("as_of"))
    return body


def _summary_row(created, *, open_row=True, resolved=None, reason=None):
    return {"_open": open_row, "_resolved_at": resolved, "_blocking_reason": reason, "_unclassified": False,
            "_unknown_status": False, "created_at": created}


async def test_empty_workspace_is_a_complete_zero(ops):
    ws = await workspace(ops)
    view = pm_register.list_register(ops, OWNER, ws["workspace_id"])
    assert view["rows"] == [] and view["next_cursor"] is None and view["coverage"]["complete"] is True
    assert view["total_by_type"] == {name: 0 for name in pm_register.TYPES}
    assert [item["count"] for item in view["summary"]["open_by_age"]] == [0, 0, 0, 0]
    assert view["summary"]["blocking_reasons"] == []
    assert len(view["summary"]["weekly"]) == 12
    assert all(item["opened"] == 0 and item["resolved"] == 0 for item in view["summary"]["weekly"])
    assert "requirement_count" not in json.dumps(view)


def test_neutral_kinds_do_not_inherit_a_type_from_the_title():
    ops = SimpleNamespace(actions=set())
    record = {"record_id": "pmgov_" + "a" * 32, "created_at": 1}
    document = {"title": "risk of outage", "status": "open", "owner_actor": CONTRIBUTOR.actor,
                "affected_requirement_ids": [], "response": None, "review_at": "2026-10-13T00:00:00Z",
                "decision": None, "adoption": None}
    revision = {"created_at": 1, "actor": CONTRIBUTOR.actor, "document": document}
    for kind in ("assumption", "dependency", "drift"):
        row = pm_register._governance_row(ops, "ws_" + "b" * 32, {"record": {**record, "kind": kind}, "revision": revision})
        assert row["type"] == "issue" and row["type_basis"] == "neutral"
        assert row["title"] == "risk of outage"
        assert row["due"] == "2026-10-13T00:00:00.000+00:00"
    document["review_at"] = "2026-10-13T00:00:00"
    naive = pm_register._governance_row(ops, "ws_" + "b" * 32, {"record": {**record, "kind": "assumption"}, "revision": revision})
    assert naive["due"] is None and naive["unknowns"]["due"] == "not_recorded"
    assert naive["impact"] is None and naive["priority"] is None
    assert naive["unknowns"]["impact"] == "not_recorded"
    record["kind"] = "story"
    assert pm_register._governance_row(ops, "ws_" + "b" * 32, {"record": record, "revision": revision}) is None


def test_unknown_dates_and_statuses_are_not_reported_as_zero_age():
    as_of = datetime(2026, 10, 13, tzinfo=timezone.utc)
    recent = (as_of - timedelta(days=3)).isoformat()
    month = (as_of - timedelta(days=10)).isoformat()
    quarter = (as_of - timedelta(days=40)).isoformat()
    old = (as_of - timedelta(days=100)).isoformat()
    future = (as_of + timedelta(days=2)).isoformat()
    rows = [_summary_row(recent), _summary_row(month), _summary_row(quarter), _summary_row(old),
            _summary_row(future), _summary_row(None), _summary_row(recent, open_row=False),
            _summary_row(recent, reason="missing evidence"), _summary_row(month, reason="missing evidence"),
            _summary_row(old, reason="")]
    summary = pm_register._summary(rows, as_of)
    assert [item["bucket"] for item in summary["open_by_age"]] == ["0-7d", "8-30d", "31-90d", "90d+"]
    assert [item["count"] for item in summary["open_by_age"]] == [2, 2, 1, 2]
    assert summary["blocking_reasons"] == [{"reason": "missing evidence", "count": 2}]
    assert len(summary["weekly"]) == 12
    assert all(date.fromisoformat(item["week_start"]).weekday() == 0 for item in summary["weekly"])
    coverage = pm_register._coverage(True, [], rows, as_of)
    assert coverage["open_without_known_age"] == 2
    assert pm_register._coverage(False, ["governance"], rows, as_of)["open_without_known_age"] is None
    assert pm_register._coverage(False, ["governance"], rows, as_of)["unknown_status"] is None


def test_repair_action_is_offered_only_for_a_matching_registered_rule():
    ops = SimpleNamespace(actions={"pm.maintenance.repair"})
    ref = {"observation_id": "obs", "document_digest": "digest", "finding_id": "finding-1"}
    rules = [{"key": "ci-bug", "labels_all": ["bug"]}, {"key": "docs", "labels_all": ["docs"]}]
    item = {"native": {"kind": "issues"}, "facts": {"labels": ["bug", "ci"]}, "finding_id": "finding-1"}
    actions = pm_register._finding_actions(ops, "ws", item, rules, "policy-1", ref, set())
    assert [entry["required"]["rule_key"] for entry in actions] == ["ci-bug"]
    assert actions[0]["action"] == "pm.maintenance.repair"
    assert actions[0]["target"] == {"workspace_id": "ws"}
    assert actions[0]["required"]["policy_id"] == "policy-1"
    assert actions[0]["required"]["finding_ref"] == ref
    assert pm_register._finding_actions(ops, "ws", item, rules, "policy-1", ref, {"finding-1"}) == []
    reserved = {**item, "existing_pull_request": {"number": 4}}
    assert pm_register._finding_actions(ops, "ws", reserved, rules, "policy-1", ref, set()) == []
    other = {**item, "native": {"kind": "pull_request"}}
    assert pm_register._finding_actions(ops, "ws", other, rules, "policy-1", ref, set()) == []
    assert pm_register._finding_actions(SimpleNamespace(actions=set()), "ws", item, rules, "policy-1", ref, set()) == []


async def test_realistic_register_keeps_identities_filters_and_explicit_links(ops):
    data = await prepared(ops)
    risk = await record(ops, data, title="登入風險")
    assumption = await record(ops, data, kind="assumption", root_cause_key="pricing-assumption", title="登入風險",
                              source_refs=[{"kind": "requirement", "id": "login"}])
    linked = await record(ops, data, kind="assumption", root_cause_key="second-assumption", title="另一個假設",
                          source_refs=[{"kind": "requirement", "id": "login"}],
                          disambiguation={"record_ids": [assumption["record_id"]], "rationale": "Same source, separate record"})
    change = await record(ops, data, kind="change", root_cause_key="direction", title="明確的方向",
                          source_refs=[{"kind": "requirement", "id": "login"}])
    before = ops.db.total_changes
    view = pm_register.list_register(ops, OWNER, data["wid"])
    assert ops.db.total_changes == before
    assert view["coverage"]["complete"] is True and view["next_cursor"] is None
    assert set(view) >= {"workspace_id", "as_of", "rows", "next_cursor", "total_by_type", "summary"}
    assert set(view["summary"]) == {"open_by_age", "weekly", "blocking_reasons"}
    assert ISO.fullmatch(view["as_of"])
    assert sum(view["total_by_type"].values()) == len(view["rows"])
    assert view["total_by_type"]["risk"] == 1
    assert view["total_by_type"]["issue"] == 2
    assert view["total_by_type"]["change"] == 1
    assert view["total_by_type"]["action"] == 0
    assert view["total_by_type"]["decision"] >= 1
    _walk(view)
    assert "requirement_count" not in json.dumps(view)
    risk_row = _by_source(view, "governance", risk["record_id"])
    assert ROW_KEYS <= set(risk_row)
    assert risk_row["row_id"] == "governance." + risk["record_id"]
    assert risk_row["type"] == "risk" and risk_row["type_basis"] == "explicit" and risk_row["status"] == "open"
    assert risk_row["owner"] == CONTRIBUTOR.actor and risk_row["next_step"] == "Arrange a focused verification"
    assert risk_row["related"]["requirement_ids"] == ["login"]
    assert risk_row["impact"] is None and risk_row["priority"] is None
    assert risk_row["unknowns"]["impact"] == "not_recorded"
    assert ISO.fullmatch(risk_row["created_at"]) and ISO.fullmatch(risk_row["updated_at"])
    assert [item["action"] for item in risk_row["available_actions"]] == ["pm.governance.revise"]
    assert all(set(item) == {"action", "target", "required"} for item in risk_row["available_actions"])
    assumption_row = _by_source(view, "governance", assumption["record_id"])
    assert assumption_row["type"] == "issue" and assumption_row["type_basis"] == "neutral"
    linked_row = _by_source(view, "governance", linked["record_id"])
    assert linked_row["type"] == "issue" and linked_row["type_basis"] == "neutral"
    change_row = _by_source(view, "governance", change["record_id"])
    change_actions = [item["action"] for item in change_row["available_actions"]]
    assert change_actions == ["pm.governance.revise", "pm.governance.decide"]
    assert "pm.governance.adoption.link" not in change_actions
    plan = next(row for row in view["rows"] if row["source"]["kind"] == "plan_revision")
    assert plan["type"] == "decision" and plan["status"] == "adopted" and plan["type_basis"] == "explicit"
    assert view["summary"]["blocking_reasons"] == []
    assert [item["bucket"] for item in view["summary"]["open_by_age"]] == ["0-7d", "8-30d", "31-90d", "90d+"]
    assert view["summary"]["open_by_age"][0]["count"] >= 1
    assert len(view["summary"]["weekly"]) == 12
    risks = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    assert [row["row_id"] for row in risks["rows"]] == [risk_row["row_id"]]
    assert risks["total_by_type"] == {"risk": 1, "issue": 0, "action": 0, "change": 0, "decision": 0}
    owned = pm_register.list_register(ops, OWNER, data["wid"], owner=CONTRIBUTOR.actor)
    assert {row["source"]["id"] for row in owned["rows"]} == {risk["record_id"], assumption["record_id"], linked["record_id"], change["record_id"]}
    assert pm_register.list_register(ops, OWNER, data["wid"], owner="missing-actor")["rows"] == []
    matched = pm_register.list_register(ops, OWNER, data["wid"], q="登入")
    assert {row["source"]["id"] for row in matched["rows"]} == {risk["record_id"], assumption["record_id"]}
    assert pm_register.list_register(ops, OWNER, data["wid"], q="FOCUSED")["rows"] == []
    opened = pm_register.list_register(ops, OWNER, data["wid"], status="open")
    assert plan["row_id"] not in {row["row_id"] for row in opened["rows"]}
    seen, cursor = [], None
    while True:
        page = pm_register.list_register(ops, OWNER, data["wid"], limit=1, after=cursor)
        assert sum(page["total_by_type"].values()) == len(view["rows"])
        if not page["rows"]:
            break
        assert len(page["rows"]) == 1
        seen.append(page["rows"][0]["row_id"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [row["row_id"] for row in view["rows"]]
    detail = pm_register.get_register(ops, OWNER, data["wid"], risk_row["row_id"])
    assert ops.db.total_changes == before
    refs = {(step["ref"]["kind"], step["ref"]["id"]) for step in detail["timeline"]}
    assert ("governance", risk["record_id"]) in refs
    assert ("governance", assumption["record_id"]) not in refs
    assert ("plan_revision", data["plan"]["revision_id"]) not in refs
    assert any(step["step"] == "finding" and step["status"] == "open" for step in detail["timeline"])
    linked_detail = pm_register.get_register(ops, OWNER, data["wid"], linked_row["row_id"])
    assert assumption["record_id"] in {step["ref"]["id"] for step in linked_detail["timeline"]}
    change_detail = pm_register.get_register(ops, OWNER, data["wid"], change_row["row_id"])
    assert assumption["record_id"] not in {step["ref"]["id"] for step in change_detail["timeline"]}
    plan_detail = pm_register.get_register(ops, OWNER, data["wid"], plan["row_id"])
    assert all(step["ref"]["kind"] != "governance" for step in plan_detail["timeline"])
    assert any(step["step"] == "decision" and step["status"] == "adopted" for step in plan_detail["timeline"])
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("UPDATE pm_governance_records SET context='{}'")
    for kwargs in ({"row_type": "story"}, {"status": "Open"}, {"limit": 0}, {"limit": 101}, {"limit": True},
                   {"owner": ""}, {"q": ""}, {"after": "nocursor"},
                   {"after": "yesterday~" + risk_row["row_id"]}):
        with pytest.raises(OperationError) as refused:
            pm_register.list_register(ops, OWNER, data["wid"], **kwargs)
        assert refused.value.status == 422
    with pytest.raises(OperationError) as missing:
        pm_register.get_register(ops, OWNER, data["wid"], "governance:not a record")
    assert missing.value.status == 422
    with pytest.raises(OperationError) as hidden:
        pm_register.list_register(ops, OUTSIDER, data["wid"])
    assert hidden.value.status == 404 and hidden.value.message == "Workspace not found"
    assert "登入風險" not in str(hidden.value)
    manage = Principal(OWNER.actor, frozenset({"manage"}))
    with pytest.raises(OperationError) as forbidden:
        pm_register.get_register(ops, manage, data["wid"], risk_row["row_id"])
    assert forbidden.value.status == 403 and "observe" in forbidden.value.message
    assert "登入風險" not in str(forbidden.value)


async def test_withdrawn_source_drops_the_row_and_the_count_together(ops):
    data = await prepared(ops)
    kept = await record(ops, data, title="Visible recovery risk")
    work = await work_for(ops, data["target"])
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    await capture(ops, work)
    data["snapshot"] = await snapshot(ops, data["wid"])
    evidence = next(item for item in data["snapshot"]["evidence"] if item["source_kind"] == "execution_state")
    hidden = await record(ops, data, kind="issue", root_cause_key="private-source", title="Private source issue",
                          source_refs=[{"kind": "evidence", "id": evidence["evidence_id"]}])
    before = pm_register.list_register(ops, OWNER, data["wid"])
    assert _by_source(before, "governance", kept["record_id"])["type"] == "risk"
    assert _by_source(before, "governance", hidden["record_id"])["type"] == "issue"
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source["task_id"],))
    after = pm_register.list_register(ops, OWNER, data["wid"])
    assert after["coverage"]["complete"] is True
    assert "Private source issue" not in json.dumps(after)
    assert all(row["source"]["id"] != hidden["record_id"] for row in after["rows"])
    assert _by_source(after, "governance", kept["record_id"])["title"] == "Visible recovery risk"
    assert after["total_by_type"]["issue"] == before["total_by_type"]["issue"] - 1
    assert after["total_by_type"]["risk"] == before["total_by_type"]["risk"]
    with pytest.raises(OperationError) as missing:
        pm_register.get_register(ops, OWNER, data["wid"], "governance." + hidden["record_id"])
    assert missing.value.status == 404 and "Private source issue" not in str(missing.value)
    detail = pm_register.get_register(ops, OWNER, data["wid"], "governance." + kept["record_id"])
    assert detail["row"]["title"] == "Visible recovery risk"
    assert "Private source issue" not in json.dumps(detail)


async def test_partial_scan_withholds_totals_and_proved_absence_stays_not_found(ops, monkeypatch):
    monkeypatch.setattr(pm_register, "MAX_SCAN", 1)
    data = await prepared(ops)
    first = await record(ops, data, title="First visible risk")
    second = await record(ops, data, kind="assumption", root_cause_key="second-cause", title="Second assumption",
                          source_refs=[{"kind": "requirement", "id": "login"}])
    view = pm_register.list_register(ops, OWNER, data["wid"])
    assert view["coverage"]["complete"] is False and "governance" in view["coverage"]["sources"]
    assert view["total_by_type"] is None and view["summary"] is None
    assert view["coverage"]["unclassified_status"] is None and view["coverage"]["unknown_status"] is None
    assert view["rows"]
    for result in (first, second):
        detail = pm_register.get_register(ops, OWNER, data["wid"], "governance." + result["record_id"])
        assert detail["row"]["source"]["id"] == result["record_id"]
        assert detail["timeline_coverage"]["complete"] is True
    with pytest.raises(OperationError) as absent:
        pm_register.get_register(ops, OWNER, data["wid"], "governance.pmgov_" + "ab" * 16)
    assert absent.value.status == 404 and absent.value.code == "NOT_FOUND"
    with pytest.raises(OperationError) as missing_meeting:
        pm_register.get_register(ops, OWNER, data["wid"], "meeting.pmmeet_" + "ab" * 16 + ".follow_up.verify_login")
    assert missing_meeting.value.status == 404 and missing_meeting.value.code == "NOT_FOUND"


async def test_http_and_mcp_match_and_do_not_write_or_leak(served, monkeypatch, mock):
    daemon, port, selection = served
    data, _, operation = await finding(daemon, port, selection)
    wid = data["workspace_id"]
    auth = token(daemon, READER.actor, "observe")
    outsider = token(daemon, OUTSIDER.actor, "observe")
    manage = token(daemon, OWNER.actor, "manage")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(daemon.fleet.config, read_only=True, principal_only=True)
    prefix = f"/api/v1/pm/workspaces/{wid}/register"
    before = daemon.ops.db.total_changes
    try:
        names = {tool.name for tool in await server.list_tools()}
        assert {"pm_register_list", "pm_register_get"} <= names
        status, body = await http(port, "GET", prefix, tok=auth)
        assert status == 200, body
        params = {"workspace_id": wid}
        assert _without_clock(await daemon.call_api("pm_register_list", params, READER)) == _without_clock(body)
        assert _without_clock(unpack(await server.call_tool("pm_register_list", params))) == _without_clock(body)
        row = next(item for item in body["rows"] if item["title"] == "Login verification needs review")
        assert row["type"] == "risk" and row["status"] == "open" and row["type_basis"] == "explicit"
        assert row["source"] == {"kind": "governance", "id": operation["result"]["record_id"]}
        assert row["related"]["requirement_ids"] == ["membership.login"]
        assert "requirement_count" not in json.dumps(body)
        assert body["coverage"]["complete"] is True and body["total_by_type"]["risk"] >= 1
        detail_params = {"workspace_id": wid, "row_id": row["row_id"]}
        status, detail = await http(port, "GET", prefix + "/" + row["row_id"], tok=auth)
        assert status == 200, detail
        assert _without_clock(await daemon.call_api("pm_register_get", detail_params, READER)) == _without_clock(detail)
        assert _without_clock(unpack(await server.call_tool("pm_register_get", detail_params))) == _without_clock(detail)
        assert any(step["step"] == "finding" for step in detail["timeline"])
        assert all(step["ref"]["kind"] != "plan_revision" for step in detail["timeline"])
        status, caps = await http(port, "GET", "/api/v1/capabilities", tok=auth)
        assert status == 200 and caps["features"]["pm_register"]["version"] == 1
        assert caps["features"]["pm_register"]["read_only"] is True
        for suffix in ("?type=story", "?status=Open", "?limit=0", "?limit=101", "?type=risk&type=issue",
                       "?q=", "?after=nocursor", "?owner="):
            assert (await http(port, "GET", prefix + suffix, tok=auth))[0] == 422
        status, hidden = await http(port, "GET", prefix, tok=outsider)
        assert status == 404 and "Login verification needs review" not in json.dumps(hidden)
        status, forbidden = await http(port, "GET", prefix, tok=manage)
        assert status == 403 and "Login verification needs review" not in json.dumps(forbidden)
        assert daemon.ops.db.total_changes == before
        assert not write_frames(mock)
        status, page = await http(port, "GET", prefix + "?limit=1", tok=auth)
        assert status == 200 and page["rows"] and page["next_cursor"]
        cursor = page["next_cursor"]
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,400}", cursor)
        raw_status, raw_page = await http(port, "GET", prefix + "?limit=1&after=" + cursor, tok=auth)
        encoded_status, encoded_page = await http(port, "GET", prefix + "?limit=1&after=" + quote(cursor, safe=""), tok=auth)
        assert raw_status == encoded_status == 200, (raw_page, encoded_page)
        assert _without_clock(raw_page) == _without_clock(encoded_page)
        assert raw_page["rows"][0]["row_id"] != page["rows"][0]["row_id"]
        await _assert_row_errors(daemon, port, server, auth, wid, "governance." + operation["result"]["record_id"])
    finally:
        await fleet.close()


def _force_table(monkeypatch, *names):
    real = pm_register._table
    forced = set(names)

    def table(db, name):
        return name in forced or real(db, name)

    monkeypatch.setattr(pm_register, "_table", table)


def _visible_synthetic(monkeypatch):
    real = pm_source_lineage.visible_roots

    def visible(db, principal, original_ids):
        synthetic = [item for item in original_ids if str(item).startswith("op_reg")]
        rest = [item for item in original_ids if not str(item).startswith("op_reg")]
        if synthetic and not rest:
            return True
        if synthetic and rest:
            return real(db, principal, rest)
        return real(db, principal, original_ids)

    monkeypatch.setattr(pm_source_lineage, "visible_roots", visible)


def _ensure_lineage(ops):
    if not pm_source_lineage.installed(ops.db):
        pm_source_lineage.schema(ops.journal)


def _synthetic_repair(index, work_item, *, sequence):
    return pm_register._base(
        kind="repair", ident=f"pmrp_{index:032x}", row_type="action", basis="explicit", title=None,
        title_reason="not_recorded", status="running", status_reason="not_recorded", owner=None,
        owner_reason="not_recorded", due=None, due_reason="not_recorded", impact=None, impact_reason="not_recorded",
        priority=None, priority_reason="not_recorded", next_step=None, next_reason="not_recorded",
        created="2026-10-12T00:00:00.000+00:00", updated="2026-10-12T00:00:00.000+00:00", requirements=[],
        work_items=[work_item], prs=[], actions=[], steps=[], refs={("work_item", work_item)}, resolved_at=None,
        blocking=None, sequence=sequence)


async def _assert_row_errors(daemon, port, server, auth, wid, visible_id):
    prefix = f"/api/v1/pm/workspaces/{wid}/register/"
    cases = (
        ("work_item.wi_" + "0" * 20, 422, "INVALID_PARAMS"),
        ("governance:pmgov_" + "a" * 32, 422, "INVALID_PARAMS"),
        ("governance.not a record", 422, "INVALID_PARAMS"),
        ("meeting.pmmeet_" + "ab" * 16 + "~follow_up~verify", 422, "INVALID_PARAMS"),
        ("governance.pmgov_" + "ab" * 16, 404, "NOT_FOUND"),
    )
    for row_id, expect_status, expect_code in cases:
        status, body = await http(port, "GET", prefix + quote(row_id, safe=""), tok=auth)
        assert (status, body.get("error", {}).get("code")) == (expect_status, expect_code), (row_id, body)
        with pytest.raises(OperationError) as rpc:
            await daemon.call_api("pm_register_get", {"workspace_id": wid, "row_id": row_id}, READER)
        assert (rpc.value.status, rpc.value.code) == (expect_status, expect_code)
        with pytest.raises(ToolError, match=expect_code):
            await server.call_tool("pm_register_get", {"workspace_id": wid, "row_id": row_id})
    status, body = await http(port, "GET", prefix + quote(visible_id, safe=""), tok=auth)
    assert status == 200 and body["row"]["row_id"] == visible_id


def test_cursor_is_an_opaque_sequence_token():
    row_id = "governance.pmgov_" + "ab" * 16
    token = pm_register._encode_cursor(12, row_id)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token)
    assert pm_register._decode_cursor(token) == (12, row_id)
    meeting_id = "meeting.pmmeet_" + "ab" * 16 + ".follow_up.verify_login"
    assert pm_register._ROW_ID.fullmatch(row_id) and pm_register._ROW_ID.fullmatch(meeting_id)
    assert pm_register._ROW_ID.fullmatch("governance:pmgov_" + "ab" * 16) is None
    with pytest.raises(OperationError) as refused:
        pm_register._params(None, None, None, None, "2026-10-13T00:00:00.1Z~" + row_id, 50)
    assert refused.value.status == 422


def test_age_bucket_keeps_day_90_with_the_previous_band():
    as_of = datetime(2026, 10, 13, tzinfo=timezone.utc)
    assert pm_register._age(as_of - timedelta(days=30), as_of) == "8-30d"
    assert pm_register._age(as_of - timedelta(days=31), as_of) == "31-90d"
    assert pm_register._age(as_of - timedelta(days=90), as_of) == "31-90d"
    assert pm_register._age(as_of - timedelta(days=91), as_of) == "90d+"


def test_hidden_observation_is_partial_like_a_missing_one(monkeypatch):
    monkeypatch.setattr(pm_register, "_table", lambda db, name: True)
    monkeypatch.setattr(pm_maintenance_policy, "head", lambda db, wid: {"policy_id": "pmmp_" + "1" * 32})
    sources = {"issues": {"status": "missing", "reason": "missing", "findings": []}}
    ops = SimpleNamespace(db=None, actions=set())
    monkeypatch.setattr(pm_maintenance_findings, "current_findings",
                        lambda *_: {"visibility_filtered": True, "observation_ref": None, "sources": sources})
    hidden = pm_register._collect_findings(ops, READER, "ws", None, set())
    monkeypatch.setattr(pm_maintenance_findings, "current_findings",
                        lambda *_: {"visibility_filtered": False, "observation_ref": None, "sources": sources})
    missing = pm_register._collect_findings(ops, READER, "ws", None, set())
    assert hidden[:2] == ([], True) and missing[:2] == ([], True)
    assert hidden[2] is False and missing[2] is False

    def conflict(*_args, **_kwargs):
        raise OperationError("PM_FINDINGS_CONFLICT", "observation is too large", 413)

    monkeypatch.setattr(pm_maintenance_findings, "current_findings", conflict)
    assert pm_register._collect_findings(ops, READER, "ws", None, set()) == ([], True, True)


def test_blocked_rows_without_a_recorded_reason_are_counted():
    as_of = datetime(2026, 10, 13, tzinfo=timezone.utc)
    created = (as_of - timedelta(days=2)).isoformat()
    blocked = {"_open": True, "_resolved_at": None, "_blocking_reason": None, "_unclassified": False,
               "_unknown_status": False, "created_at": created, "status": "blocked"}
    explained = {**blocked, "_blocking_reason": "A later source is restricted"}
    summary = pm_register._summary([blocked, blocked, blocked, explained], as_of)
    coverage = pm_register._coverage(True, [], [blocked, blocked, blocked, explained], as_of)
    assert summary["blocking_reasons"] == [{"reason": "A later source is restricted", "count": 1}]
    assert coverage["blocked_without_reason"] == 3
    assert pm_register._coverage(False, ["meeting"], [blocked], as_of)["blocked_without_reason"] is None


async def test_direct_repair_lookup_keeps_actions_links_and_steps(ops, monkeypatch):
    data = await prepared(ops)
    repair = {"repair_id": "pmrp_" + "a" * 32, "operation_status": "running", "finding_id": "pmfn_" + "b" * 32,
              "work_item_id": "wi_" + "1" * 20, "created_at": 1_760_000_000.0, "finished_at": None,
              "deadline_at": 1_760_003_600.0, "sequence": 4}
    ops.actions["pm.maintenance.repair.evidence.record"] = object()
    calls = []
    monkeypatch.setattr(pm_maintenance_repairs, "get_repair", lambda *args, **kwargs: {"repair": repair})
    monkeypatch.setattr(pm_maintenance_repairs, "list_repairs", lambda *args, **kwargs: calls.append(1))
    detail = pm_register.get_register(ops, OWNER, data["wid"], "repair." + repair["repair_id"])
    assert calls == []
    assert [item["action"] for item in detail["row"]["available_actions"]] == ["pm.maintenance.repair.evidence.record"]
    assert detail["row"]["related"]["work_item_ids"] == [repair["work_item_id"]]
    assert any(step["step"] == "work" and step["ref"] == {"kind": "work_item", "id": repair["work_item_id"]}
               for step in detail["timeline"])


async def test_repair_detail_outside_the_scan_does_not_rescan(ops, monkeypatch):
    data = await prepared(ops)
    repair = {"repair_id": "pmrp_" + "c" * 32, "operation_status": "running", "finding_id": "pmfn_" + "d" * 32,
              "work_item_id": "wi_" + "2" * 20, "created_at": 1_760_000_000.0, "finished_at": None,
              "deadline_at": 1_760_003_600.0, "sequence": 9}
    calls = []
    monkeypatch.setattr(pm_maintenance_repairs, "list_repairs", lambda *args, **kwargs: calls.append(1))
    monkeypatch.setattr(pm_maintenance_repairs, "get_repair", lambda *args, **kwargs: {"repair": repair})
    detail = pm_register.get_register(ops, OWNER, data["wid"], "repair." + repair["repair_id"])
    assert calls == []
    assert detail["row"]["related"]["work_item_ids"] == [repair["work_item_id"]]
    assert any(step["step"] == "work" and step["ref"]["id"] == repair["work_item_id"] for step in detail["timeline"])


async def test_newest_governance_window_and_irrelevant_sources(ops, monkeypatch):
    monkeypatch.setattr(pm_register, "PAGE", 1)
    monkeypatch.setattr(pm_register, "MAX_SCAN", 2)
    data = await prepared(ops)
    oldest = await record(ops, data, title="Oldest risk", root_cause_key="oldest")
    middle = await record(ops, data, title="Middle risk", root_cause_key="middle",
                          source_refs=[{"kind": "plan_input", "id": data["confirmed"]}])
    newest = await record(ops, data, title="Newest risk", root_cause_key="newest",
                          source_refs=[{"kind": "requirement", "id": "login"}])
    view = pm_register.list_register(ops, OWNER, data["wid"])
    gov = [row["source"]["id"] for row in view["rows"] if row["source"]["kind"] == "governance"]
    assert gov == [newest["record_id"], middle["record_id"]]
    assert oldest["record_id"] not in gov
    assert view["coverage"]["complete"] is False and "governance" in view["coverage"]["sources"]
    assert view["coverage"]["scan_limit_per_source"] == 2
    risks = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    assert risks["coverage"]["complete"] is False and risks["total_by_type"] is None
    decisions = pm_register.list_register(ops, OWNER, data["wid"], row_type="decision")
    assert decisions["coverage"]["complete"] is True and "governance" not in decisions["coverage"]["sources"]
    assert decisions["total_by_type"]["decision"] >= 1


async def test_plan_cap_keeps_the_newest_and_spares_risks(ops, monkeypatch):
    monkeypatch.setattr(pm_register, "DETAIL_CAP", 1)
    data = await prepared(ops)
    candidate = await plan_draft(ops, data["target"], title="Newer adopted plan")
    ctx = adoption(ops, data["target"], candidate["revision_id"], previous=data["plan"]["revision_id"])
    adopt(ctx)
    await settle(ops, ctx.op)
    view = pm_register.list_register(ops, OWNER, data["wid"])
    plans = [row["source"]["id"] for row in view["rows"] if row["source"]["kind"] == "plan_revision"]
    assert plans == [candidate["revision_id"]]
    assert view["coverage"]["complete"] is False and "plan_revision" in view["coverage"]["sources"]
    risks = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    assert risks["coverage"]["complete"] is True and "plan_revision" not in risks["coverage"]["sources"]


async def test_repair_cap_keeps_the_newest(ops, monkeypatch):
    monkeypatch.setattr(pm_register, "MAX_SCAN", 2)
    _force_table(monkeypatch, "pm_maintenance_repairs")
    repairs = [{"repair_id": f"pmrp_{index:032x}", "operation_status": "running", "sequence": index,
                "created_at": 1_760_000_000.0 + index, "finished_at": None, "finding_id": None, "work_item_id": None}
               for index in (1, 2, 3)]

    def list_repairs(service, principal, workspace_id, *, after=0, limit=20):
        visible = [row for row in repairs if row["sequence"] > after]
        page = visible[:limit]
        nxt = page[-1]["sequence"] if len(visible) > limit else None
        return {"repairs": page, "next_after": nxt}

    monkeypatch.setattr(pm_maintenance_repairs, "list_repairs", list_repairs)
    data = await prepared(ops)
    view = pm_register.list_register(ops, OWNER, data["wid"], row_type="action")
    assert [row["source"]["id"] for row in view["rows"]] == [repairs[2]["repair_id"], repairs[1]["repair_id"]]
    assert view["coverage"]["complete"] is False and "repair" in view["coverage"]["sources"]
    risks = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    assert risks["coverage"]["complete"] is True and "repair" not in risks["coverage"]["sources"]


async def test_meeting_cap_does_not_make_risks_partial(ops, monkeypatch):
    monkeypatch.setattr(pm_register, "DETAIL_CAP", 1)
    _force_table(monkeypatch, "pm_meetings")
    stubs = [{"meeting_id": f"pmmeet_{index:032x}", "sequence": index, "completion_id": "done"} for index in (1, 2, 3)]

    def list_meetings(service, principal, workspace_id, *, after=0, limit=50, sprint_id=None):
        visible = [row for row in stubs if row["sequence"] > after]
        page = visible[:limit]
        return {"meetings": page, "next_cursor": page[-1]["sequence"] if len(visible) > limit else None}

    def get_meeting(service, principal, workspace_id, meeting_id, *, history_limit=50, follow_up_after=0):
        stub = next(row for row in stubs if row["meeting_id"] == meeting_id)
        return {"meeting": stub, "completion": {"created_at": 1_700_000_000, "document": {
            "decisions": [{"id": "keep", "disposition": "recorded_discussion_decision", "text": "Keep"}],
            "follow_ups": []}}, "follow_up_events": [], "coverage": {"histories": {"follow_ups": {"has_more": False}}}}

    monkeypatch.setattr(pm_meetings, "list_meetings", list_meetings)
    monkeypatch.setattr(pm_meetings, "get_meeting", get_meeting)
    data = await prepared(ops)
    view = pm_register.list_register(ops, OWNER, data["wid"])
    kept = [row["source"]["id"] for row in view["rows"] if row["source"]["kind"] == "meeting"]
    assert kept == [f"pmmeet_{3:032x}.decision.keep"]
    assert "meeting" in view["coverage"]["sources"]
    risks = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    assert risks["coverage"]["complete"] is True and "meeting" not in risks["coverage"]["sources"]
    assert risks["summary"] is not None


async def test_meeting_history_cap_marks_only_that_source(ops, monkeypatch):
    monkeypatch.setattr(pm_register, "HISTORY_MEETING", 1)
    _force_table(monkeypatch, "pm_meetings")
    meeting_id = "pmmeet_" + "c" * 32
    ops.db.execute("CREATE TABLE pm_meeting_follow_up_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id TEXT NOT NULL)")
    ops.db.executemany("INSERT INTO pm_meeting_follow_up_events(meeting_id) VALUES(?)", [(meeting_id,)] * 3)
    stub = {"meeting_id": meeting_id, "sequence": 7, "completion_id": "done"}
    monkeypatch.setattr(pm_meetings, "list_meetings",
                        lambda *args, **kwargs: {"meetings": [stub], "next_cursor": None})
    monkeypatch.setattr(pm_meetings, "get_meeting", lambda *args, **kwargs: {"meeting": stub, "completion": {
        "created_at": 1_700_000_000, "document": {"decisions": [], "follow_ups": [
            {"id": "check_quality", "text": "Check the source", "status": "open"}]}},
        "follow_up_events": [], "coverage": {"histories": {"follow_ups": {"has_more": False}}}})
    data = await prepared(ops)
    view = pm_register.list_register(ops, OWNER, data["wid"])
    assert any(row["source"]["id"] == meeting_id + ".follow_up.check_quality" for row in view["rows"])
    assert view["coverage"]["complete"] is False and "meeting" in view["coverage"]["sources"]
    risks = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    assert risks["coverage"]["complete"] is True and risks["total_by_type"]["risk"] == 0


async def test_meeting_follow_up_cutoff_is_reported_on_the_history(meeting_ops):
    data, _sprint, _snap, target = await meeting(meeting_ops, kind="retro")
    await apply(meeting_ops, target, "draft", who=CONTRIBUTOR, **draft_params("retro"))
    await note(meeting_ops, target)
    await complete(meeting_ops, target)
    for status, result in (("blocked", "First block"), ("open", "Unblocked"), ("reported_done", "Done now")):
        await apply(meeting_ops, target, "follow_up.record", follow_up_id="check_quality", status=status, result=result)
    wid, mid = data["workspace_id"], target["meeting_id"]
    cutoff = meeting_ops.db.execute("SELECT sequence FROM pm_meeting_follow_up_events WHERE meeting_id=? ORDER BY sequence LIMIT 1",
                                    (mid,)).fetchone()["sequence"]
    detail = pm_meetings.get_meeting(meeting_ops, READER, wid, mid, follow_up_after=cutoff)
    assert detail["coverage"]["histories"]["follow_ups"]["after"] == cutoff
    assert detail["coverage"]["histories"]["notes"]["after"] == 0
    plain = pm_meetings.get_meeting(meeting_ops, READER, wid, mid)
    zero = pm_meetings.get_meeting(meeting_ops, READER, wid, mid, follow_up_after=0)
    assert json.dumps(plain, sort_keys=True, default=str) == json.dumps(zero, sort_keys=True, default=str)


async def test_review_history_cap_keeps_the_newest_event(ops, monkeypatch):
    from bat_agent_connector import pm_maintenance_review
    monkeypatch.setattr(pm_register, "HISTORY_REVIEW", 1)
    _force_table(monkeypatch, "pm_maintenance_reviews")
    review_id = "pmrevw_" + "a" * 32
    events = [{"event_id": f"pmrve_{index:032x}", "kind": "candidate.record", "sequence": index, "created_at": index,
               "document": {"disposition": "proposal", "title": f"Proposal {index}"}} for index in (1, 2, 3)]

    def list_events(service, principal, workspace_id, review, *, after=0, limit=20):
        visible = [row for row in events if row["sequence"] > after]
        page = visible[:limit]
        return {"events": page, "next_cursor": page[-1]["sequence"] if len(visible) > limit else None}

    review = {"review_id": review_id, "sequence": 4, "status": "completed", "title": "Review", "created_at": 1}
    monkeypatch.setattr(pm_maintenance_review, "list_reviews",
                        lambda *args, **kwargs: {"reviews": [review], "next_cursor": None})
    monkeypatch.setattr(pm_maintenance_review, "list_events", list_events)
    monkeypatch.setattr(pm_maintenance_review, "get_review", lambda *args, **kwargs: {
        "review": review, "completion": {"actor": OWNER.actor, "created_at": 2, "document": {
            "direction": "accepted", "follow_ups": []}}, "events": []})
    data = await prepared(ops)
    view = pm_register.list_register(ops, OWNER, data["wid"], row_type="change")
    assert [row["source"]["id"] for row in view["rows"]] == [events[-1]["event_id"]]
    assert view["coverage"]["complete"] is False and "review" in view["coverage"]["sources"]
    risks = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    assert risks["coverage"]["complete"] is True and "review" not in risks["coverage"]["sources"]


def _newest_page(rows, key, after, limit):
    visible = [row for row in rows if row["sequence"] > after]
    page = visible[:limit]
    return {key: page, "next_cursor": page[-1]["sequence"] if len(visible) > limit else None}


async def test_review_cap_keeps_the_newest(ops, monkeypatch):
    from bat_agent_connector import pm_maintenance_review
    monkeypatch.setattr(pm_register, "DETAIL_CAP", 1)
    _force_table(monkeypatch, "pm_maintenance_reviews")
    reviews = [{"review_id": f"pmrevw_{index:032x}", "sequence": index, "status": "completed",
                "title": f"Review {index}", "created_at": index} for index in (1, 2, 3)]

    def get_review(service, principal, workspace_id, review_id, *, history_limit=1):
        review = next(row for row in reviews if row["review_id"] == review_id)
        return {"review": review, "completion": {"actor": OWNER.actor, "created_at": 2, "document": {
            "direction": "accepted", "follow_ups": []}}, "events": []}

    monkeypatch.setattr(pm_maintenance_review, "list_reviews",
                        lambda *args, after=0, limit=20, **kwargs: _newest_page(reviews, "reviews", after, limit))
    monkeypatch.setattr(pm_maintenance_review, "list_events", lambda *args, **kwargs: {"events": [], "next_cursor": None})
    monkeypatch.setattr(pm_maintenance_review, "get_review", get_review)
    data = await prepared(ops)
    view = pm_register.list_register(ops, OWNER, data["wid"], row_type="decision")
    kept = [row["source"]["id"] for row in view["rows"] if row["source"]["kind"] == "meeting"]
    assert kept == [reviews[-1]["review_id"]]
    assert view["coverage"]["complete"] is False and "review" in view["coverage"]["sources"]
    risks = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    assert risks["coverage"]["complete"] is True and "review" not in risks["coverage"]["sources"]


async def test_intake_cap_keeps_the_newest(ops, monkeypatch):
    from bat_agent_connector import pm_follow_up
    monkeypatch.setattr(pm_register, "DETAIL_CAP", 1)
    _force_table(monkeypatch, "pm_follow_up_intakes")
    intakes = [{"intake_id": f"pmfu_{index:032x}", "sequence": index, "created_at": index, "document": {}}
               for index in (1, 2, 3)]
    monkeypatch.setattr(pm_follow_up, "list_intakes",
                        lambda *args, after=0, limit=50, **kwargs: _newest_page(intakes, "intakes", after, limit))
    monkeypatch.setattr(pm_follow_up, "get_intake",
                        lambda *args, **kwargs: {"intake": next(row for row in intakes if row["intake_id"] == args[3])})
    data = await prepared(ops)
    view = pm_register.list_register(ops, OWNER, data["wid"], row_type="action")
    kept = [row["source"]["id"] for row in view["rows"] if row["source"]["kind"] == "follow_up"]
    assert kept == [intakes[-1]["intake_id"]]
    assert view["coverage"]["complete"] is False and "follow_up" in view["coverage"]["sources"]
    risks = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    assert risks["coverage"]["complete"] is True and "follow_up" not in risks["coverage"]["sources"]


async def test_proposal_cap_keeps_the_newest(ops, monkeypatch):
    from bat_agent_connector import pm_conversation_proposals
    monkeypatch.setattr(pm_register, "DETAIL_CAP", 1)
    _force_table(monkeypatch, "pm_conversation_proposals")
    proposals = [{"proposal_id": f"pmcp_{index:032x}", "sequence": index, "created_at": index,
                  "document": {"title": f"Proposal {index}"}, "validation": {"status": "proposed"}}
                 for index in (1, 2, 3)]
    monkeypatch.setattr(pm_conversation_proposals, "list_proposals",
                        lambda *args, after=0, limit=50, **kwargs: _newest_page(proposals, "proposals", after, limit))
    monkeypatch.setattr(pm_conversation_proposals, "get_proposal",
                        lambda *args, **kwargs: {"proposal": next(row for row in proposals if row["proposal_id"] == args[3])})
    data = await prepared(ops)
    view = pm_register.list_register(ops, OWNER, data["wid"], row_type="change")
    kept = [row["source"]["id"] for row in view["rows"] if row["source"]["kind"] == "proposal"]
    assert kept == [proposals[-1]["proposal_id"]]
    assert view["coverage"]["complete"] is False and "proposal" in view["coverage"]["sources"]
    risks = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    assert risks["coverage"]["complete"] is True and "proposal" not in risks["coverage"]["sources"]


async def test_mapping_cap_keeps_the_newest(ops, monkeypatch):
    from bat_agent_connector import pm_issue_mappings
    monkeypatch.setattr(pm_register, "MAX_SCAN", 2)
    data = await prepared(ops)
    version = ops.db.execute("PRAGMA user_version").fetchone()[0]
    ops.db.execute("""CREATE TABLE IF NOT EXISTS pm_issue_revisions (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL UNIQUE,
        mapping_id TEXT NOT NULL, workspace_id TEXT NOT NULL, work_item_id TEXT NOT NULL,
        previous_revision_id TEXT UNIQUE, document TEXT NOT NULL, document_digest TEXT NOT NULL,
        operation_id TEXT NOT NULL UNIQUE, created_at REAL NOT NULL, actor TEXT NOT NULL)""")
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == version
    mappings = []
    for index in (1, 2, 3):
        mapping_id = f"pmim_{index:032x}"
        mappings.append({"mapping_id": mapping_id, "sequence": index, "created_at": float(index),
                         "document": {"state": "tracking", "selected": {"public_title": f"Issue {index}"}}})
        ops.db.execute("""INSERT INTO pm_issue_revisions(
            revision_id,mapping_id,workspace_id,work_item_id,document,document_digest,operation_id,created_at,actor)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (f"pmir_{index:032x}", mapping_id, data["wid"], f"wi_{index:020x}", "{}", "digest",
             f"op_{index:032x}", index, OWNER.actor))
    monkeypatch.setattr(pm_issue_mappings, "list_mappings",
                        lambda *args, after=0, limit=50, **kwargs: _newest_page(mappings, "mappings", after, limit))
    view = pm_register.list_register(ops, OWNER, data["wid"], row_type="issue")
    kept = [row["source"]["id"] for row in view["rows"] if row["source"]["kind"] == "issue_mapping"]
    assert kept == [mappings[2]["mapping_id"], mappings[1]["mapping_id"]]
    assert view["coverage"]["complete"] is False and "issue_mapping" in view["coverage"]["sources"]
    assert view["coverage"]["scan_limit_per_source"] == 2
    risks = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    assert risks["coverage"]["complete"] is True and "issue_mapping" not in risks["coverage"]["sources"]


async def test_governance_history_cap_marks_only_the_detail(ops, monkeypatch):
    data = await prepared(ops)
    made = await record(ops, data, title="First title", root_cause_key="history-cap")
    await change(ops, made, "revise", **fields(data, title="Second title"))
    await change(ops, made, "revise", **fields(data, title="Third title"))
    open_detail = pm_register.get_register(ops, OWNER, data["wid"], "governance." + made["record_id"])
    assert open_detail["timeline_coverage"]["complete"] is True
    monkeypatch.setattr(pm_register, "MAX_SCAN", 1)
    listed = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    assert [row["source"]["id"] for row in listed["rows"]] == [made["record_id"]]
    assert listed["coverage"]["complete"] is True
    capped = pm_register.get_register(ops, OWNER, data["wid"], "governance." + made["record_id"])
    assert capped["timeline_coverage"]["complete"] is False
    assert capped["timeline_coverage"]["sources"] == ["governance"]
    assert capped["timeline_coverage"]["partial_reason"] == "incomplete"
    assert any(step.get("title") == "Third title" for step in capped["timeline"])


async def test_hidden_repairs_do_not_change_coverage_counts_or_actions(ops, monkeypatch):
    monkeypatch.setattr(pm_register, "MAX_SCAN", 1)
    data = await prepared(ops)
    pm_maintenance_repairs.schema(ops.journal)
    before = _without_clock(pm_register.list_register(ops, OWNER, data["wid"]))
    context = {"hidden": True}
    digest = pm_maintenance_checks._digest(context)
    for index in range(3):
        ops.db.execute("""INSERT INTO pm_maintenance_repairs(
            repair_id,operation_id,workspace_id,policy_id,finding_id,work_item_id,context,context_digest,
            created_at,deadline_at,budget_period) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (f"pmrp_{index:032x}", f"op_{index:032x}", data["wid"], "pmmp_" + "a" * 32, f"finding-{index}",
             f"wi_{index:020x}", json.dumps(context), digest, 1, 2, 0))
    after = _without_clock(pm_register.list_register(ops, OWNER, data["wid"]))
    stored = ops.db.execute("SELECT COUNT(*) FROM pm_maintenance_repairs WHERE workspace_id=?", (data["wid"],)).fetchone()[0]
    assert stored > pm_register.MAX_SCAN
    assert after == before
    assert after["coverage"]["complete"] is True


async def test_lineage_hidden_repair_does_not_remove_the_finding_action(ops, monkeypatch):
    """A repair dropped by lineage must not reserve its finding or mark the source partial."""
    data = await prepared(ops)
    finding_id = "pmfn_" + "1" * 32
    work_item = "wi_" + "e" * 20
    repair = {"repair_id": "pmrp_" + "7" * 32, "operation_status": "running", "sequence": 5,
              "created_at": 1_760_000_000.0, "finished_at": None, "deadline_at": None,
              "finding_id": finding_id, "work_item_id": work_item}
    ops.actions["pm.maintenance.repair"] = object()
    _force_table(monkeypatch, "pm_maintenance_policies", "pm_maintenance_repairs")
    monkeypatch.setattr(pm_maintenance_policy, "head", lambda db, wid: {"policy_id": "pmmp_" + "a" * 32, "revision": 1})
    monkeypatch.setattr(pm_maintenance_policy, "active_record", lambda *args, **kwargs: {
        "policy_id": "pmmp_" + "a" * 32, "policy": {"repair_rules": [{"key": "ci-bug", "labels_all": ["bug"]}]}})
    monkeypatch.setattr(pm_maintenance_findings, "current_findings", lambda *args, **kwargs: {
        "visibility_filtered": False,
        "observation_ref": {"observation_id": "pmobs_" + "a" * 27, "document_digest": "d" * 64},
        "sources": {"issues": {"status": "complete", "findings": [{
            "finding_id": finding_id, "state": "observed_open", "native": {"kind": "issues"},
            "facts": {"labels": ["bug", "ci"]}}]}}})
    monkeypatch.setattr(pm_maintenance_repairs, "list_repairs",
                        lambda *args, after=0, limit=20, **kwargs: {"repairs": [repair] if repair["sequence"] > after else [], "next_after": None})

    def offered(view):
        row = next(item for item in view["rows"] if item["source"] == {"kind": "finding", "id": finding_id})
        return [action["action"] for action in row["available_actions"]]

    visible = pm_register.list_register(ops, OWNER, data["wid"])
    assert repair["repair_id"] in {row["source"]["id"] for row in visible["rows"]}
    assert offered(visible) == []
    _ensure_lineage(ops)
    ops.db.execute("INSERT INTO pm_source_lineage VALUES(?,?,?,?)",
                   ("work_item", work_item, "op_" + uuid.uuid4().hex, "op_" + "f" * 32))
    hidden = pm_register.list_register(ops, OWNER, data["wid"])
    assert repair["repair_id"] not in {row["source"]["id"] for row in hidden["rows"]}
    assert offered(hidden) == ["pm.maintenance.repair"]
    assert hidden["coverage"]["complete"] is True and "repair" not in hidden["coverage"]["sources"]
    detail = pm_register.get_register(ops, OWNER, data["wid"], "finding." + finding_id)
    assert [action["action"] for action in detail["row"]["available_actions"]] == ["pm.maintenance.repair"]
    assert detail["timeline_coverage"]["complete"] is True


async def test_revision_between_pages_keeps_the_row_and_shows_the_new_title(ops):
    data = await prepared(ops)
    made = [
        await record(ops, data, title="Row a", root_cause_key="a"),
        await record(ops, data, title="Row b", root_cause_key="b",
                     source_refs=[{"kind": "plan_input", "id": data["confirmed"]}]),
        await record(ops, data, title="Row c", root_cause_key="c",
                     source_refs=[{"kind": "requirement", "id": "login"}]),
    ]
    by_id = {item["record_id"]: item for item in made}
    original = pm_register.list_register(ops, OWNER, data["wid"])
    order = [row["row_id"] for row in original["rows"]]
    target = next(row_id for row_id in reversed(order) if row_id.startswith("governance."))
    seen, titles, cursor, revised = [], {}, None, False
    while True:
        page = pm_register.list_register(ops, OWNER, data["wid"], limit=1, after=cursor)
        if page["rows"] and not revised and all(row["row_id"] != target for row in page["rows"]):
            await change(ops, by_id[target.split(".", 1)[1]], "revise", **fields(data, title="Row revised"))
            revised = True
        for row in page["rows"]:
            seen.append(row["row_id"])
            titles[row["row_id"]] = row["title"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert revised and seen == order and titles[target] == "Row revised"


async def test_change_timeline_keeps_one_adoption_and_only_resolvable_refs(ops, monkeypatch):
    data = await prepared(ops)
    candidate = await plan_draft(ops, data["target"], title="Fixed next plan")
    first = await record(ops, data, kind="change", proposed_revision_id=candidate["revision_id"])
    await change(ops, first, "decide", disposition="adopted_direction", rationale="Choose this exact direction")
    ctx = adoption(ops, data["target"], candidate["revision_id"], previous=data["plan"]["revision_id"])
    adopt(ctx)
    await settle(ops, ctx.op)
    await change(ops, first, "adoption.link", adoption_operation_id=ctx.operation_id, rationale="Link the original adoption fact")
    calls = []
    real_get = pm_plans.get_plan

    def counting(service, principal, workspace_id, revision_id, *args, **kwargs):
        calls.append(revision_id)
        return real_get(service, principal, workspace_id, revision_id, *args, **kwargs)

    monkeypatch.setattr(pm_plans, "get_plan", counting)
    detail = pm_register.get_register(ops, OWNER, data["wid"], "governance." + first["record_id"])
    decisions = [step for step in detail["timeline"] if step["step"] == "decision" and step["status"] == "adopted_direction"]
    adopted = [step for step in detail["timeline"] if step["ref"]["id"] == candidate["revision_id"] and step["step"] == "revision"]
    assert len(decisions) == 1
    assert decisions[0]["ref"] == {"kind": "governance", "id": first["record_id"]}
    assert len(adopted) == 1 and adopted[0]["status"] == "adopted"
    assert data["plan"]["revision_id"] not in {step["ref"]["id"] for step in detail["timeline"]}
    assert all(not str(step["ref"]["id"]).startswith("pmgrev_") for step in detail["timeline"])
    assert set(calls) == {candidate["revision_id"]}
    for step in detail["timeline"]:
        kind, ident = step["ref"]["kind"], step["ref"]["id"]
        if kind in {"governance", "plan_revision"}:
            followed = pm_register.get_register(ops, OWNER, data["wid"], f"{kind}.{ident}")
            assert followed["row"]["source"] == {"kind": kind, "id": ident}


async def test_one_oversized_row_skips_that_row_and_marks_the_source(ops, monkeypatch):
    _visible_synthetic(monkeypatch)
    data = await prepared(ops)
    _ensure_lineage(ops)
    work_item = "wi_" + "f" * 20
    for index in range(65):
        original = f"op_reg{index:032x}"
        ops.db.execute("INSERT INTO pm_source_lineage VALUES(?,?,?,?)", ("work_item", work_item, original, original))
    kept = _synthetic_repair(1, "wi_" + "1" * 20, sequence=2)
    ops.db.execute("INSERT INTO pm_source_lineage VALUES(?,?,?,?)", ("work_item", "wi_" + "1" * 20, "op_reg" + "a" * 32, "op_reg" + "a" * 32))
    oversized = _synthetic_repair(2, work_item, sequence=3)
    real = pm_register._collect

    def collect(service, principal, workspace_id):
        rows, notes = real(service, principal, workspace_id)
        return [*rows, kept, oversized], notes

    monkeypatch.setattr(pm_register, "_collect", collect)
    view = pm_register.list_register(ops, OWNER, data["wid"])
    assert view["coverage"]["complete"] is False
    assert view["coverage"]["partial_reason"] == "source_incomplete"
    assert "repair" in view["coverage"]["sources"]
    assert oversized["row_id"] not in {row["row_id"] for row in view["rows"]}
    assert kept["row_id"] in {row["row_id"] for row in view["rows"]}
    pm_source_lineage.read_response(ops.db, OWNER, view)


async def test_timeline_stops_at_the_root_budget(ops, monkeypatch):
    monkeypatch.setattr(pm_source_lineage, "MAX_ROOTS", 2)
    _visible_synthetic(monkeypatch)
    await prepared(ops)
    _ensure_lineage(ops)
    row = _synthetic_repair(1, "wi_" + "0" * 20, sequence=1)
    row["related"]["work_item_ids"] = []
    row["_roots"] = set()
    steps = []
    for index in range(4):
        work_item = f"wi_{index:020x}"
        original = f"op_reg{index:032x}"
        ops.db.execute("INSERT INTO pm_source_lineage VALUES(?,?,?,?)", ("work_item", work_item, original, original))
        steps.append({"step": "work", "at": None, "actor": None, "title": None, "status": "running",
                      "ref": {"kind": "work_item", "id": work_item}, "work_item_id": work_item})
    notes = pm_register._Scan()
    bounded = pm_register._bound_timeline(ops, OWNER, row, steps, notes)
    assert len(bounded) == 2 and "repair" in notes
    public = pm_register._public(row)
    public["related"]["work_item_ids"] = []
    pm_source_lineage.read_response(ops.db, OWNER, {"row": public, "timeline": bounded})


async def test_register_list_shares_one_read_snapshot(ops):
    data = await prepared(ops, evidence=True)
    await record(ops, data, title="Seed risk")
    source = ops.db.execute("SELECT * FROM pm_governance_records WHERE workspace_id=? ORDER BY sequence LIMIT 1", (data["wid"],)).fetchone()
    head = ops.db.execute("SELECT * FROM pm_governance_heads WHERE record_id=?", (source["record_id"],)).fetchone()
    revision = ops.db.execute("SELECT * FROM pm_governance_revisions WHERE revision_id=?", (head["revision_id"],)).fetchone()
    for index in range(120):
        suffix = uuid.uuid4().hex
        rid, rev = "pmgov_" + suffix, "pmgrev_" + suffix
        ops.db.execute("""INSERT INTO pm_governance_records(
            record_id,workspace_id,kind,root_cause_key,context,context_digest,created_by,created_at,operation_id)
            VALUES(?,?,?,?,?,?,?,?,?)""", (rid, data["wid"], source["kind"], f"clone-{index}", source["context"],
                                            source["context_digest"], source["created_by"], source["created_at"] + index, "op_" + suffix))
        ops.db.execute("""INSERT INTO pm_governance_revisions(
            revision_id,record_id,previous_revision_id,version,action,document,document_digest,actor,created_at,operation_id)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (rev, rid, None, 1, revision["action"], revision["document"], revision["document_digest"],
                                              revision["actor"], revision["created_at"] + index, "op_r" + suffix[2:]))
        ops.db.execute("INSERT INTO pm_governance_heads VALUES(?,?,?)", (rid, 1, rev))
    statements = []
    ops.db.set_trace_callback(statements.append)
    try:
        view = pm_register.list_register(ops, OWNER, data["wid"], limit=1)
        list_immediate = sum(1 for statement in statements if statement.strip().upper().startswith("BEGIN IMMEDIATE"))
        list_statements = len(statements)
        print(f"\nM2_SQL list statements={list_statements} BEGIN_IMMEDIATE={list_immediate} "
              f"risks={view['total_by_type']['risk'] if view['total_by_type'] else None} complete={view['coverage']['complete']}")
        statements.clear()
        pm_register.get_register(ops, OWNER, data["wid"], view["rows"][0]["row_id"])
        get_statements = len(statements)
        get_immediate = sum(1 for statement in statements if statement.strip().upper().startswith("BEGIN IMMEDIATE"))
        print(f"M2_SQL get statements={get_statements} BEGIN_IMMEDIATE={get_immediate}")
    finally:
        ops.db.set_trace_callback(None)
    assert view["coverage"]["complete"] is True
    assert view["total_by_type"]["risk"] >= 121
    assert list_immediate == 0 and get_immediate == 0
    assert list_statements < 2500 and get_statements < list_statements


async def test_withdrawn_meeting_source_is_partial_while_the_meeting_stays_listed(meeting_ops):
    data, _sprint, _snap, target = await meeting(meeting_ops, kind="retro")
    await meeting_draft(meeting_ops, target, "retro")
    await note(meeting_ops, target)
    await complete(meeting_ops, target)
    wid, meeting_id = data["workspace_id"], target["meeting_id"]
    work = {"workspace_id": wid, "work_item_id": data["work"][0]}
    source = task(meeting_ops)
    await link(meeting_ops, work, "task", source["task_id"])
    await capture(meeting_ops, work)
    later = await snapshot(meeting_ops, wid)
    await apply(meeting_ops, target, "follow_up.record", follow_up_id="check_quality", status="blocked",
                result="A later source is restricted", snapshot_id=later["snapshot_id"])
    blocked = pm_register.list_register(meeting_ops, READER, wid)
    assert any(item["reason"] == "A later source is restricted" for item in blocked["summary"]["blocking_reasons"])
    meeting_ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source["task_id"],))
    after = pm_register.list_register(meeting_ops, READER, wid)
    listed = pm_meetings.list_meetings(meeting_ops, READER, wid)
    still = [item for item in listed["meetings"] if item["meeting_id"] == meeting_id]
    assert still and still[0].get("completion_id")
    assert not [row for row in after["rows"] if row["source"]["kind"] == "meeting" and meeting_id in row["source"]["id"]]
    assert after["coverage"]["complete"] is False and "meeting" in after["coverage"]["sources"]
    assert after["summary"] is None
    risks = pm_register.list_register(meeting_ops, READER, wid, row_type="risk")
    assert risks["coverage"]["complete"] is True and risks["summary"] is not None and "meeting" not in risks["coverage"]["sources"]


def _risk_request(daemon, wid, snap, key, title):
    return {"action": "pm.governance.record", "target": {"workspace_id": wid},
            "params": {"kind": "risk", "root_cause_key": key, "snapshot_id": snap["snapshot_id"], "title": title,
                       "description": "A sourced finding.", "owner_actor": CONTRIBUTOR.actor,
                       "source_refs": [{"kind": "requirement", "id": "membership.login"}], "rationale": "Keep it visible"},
            "preconditions": {"expected_plan_version": pm_plans.head(daemon.ops.db, wid)["version"]},
            "idempotency_key": uuid.uuid4().hex}


async def _same(daemon, port, server, auth, method, params, path):
    status, body = await http(port, "GET", path, tok=auth)
    assert status == 200, body
    rpc = await daemon.call_api(method, dict(params), READER)
    mcp = unpack(await server.call_tool(method, dict(params)))
    assert _without_clock(body) == _without_clock(rpc) == _without_clock(mcp)
    return body


async def test_lineage_page_survives_a_hidden_row_and_pages_at_the_root_budget(served, monkeypatch):
    daemon, port, selection = served
    data = await sprint_prepared(daemon, selection)
    wid = data["workspace_id"]
    snap = await snapshot(daemon.ops, wid)
    visible = await send(daemon, port, _risk_request(daemon, wid, snap, "visible", "Visible risk"), CONTRIBUTOR)
    work = {"workspace_id": wid, "work_item_id": data["work"][0]}
    with controller_authority.use(controller_authority.binding(daemon.ops.db, OWNER), daemon.journal):
        source = task(daemon.ops)
        await link(daemon.ops, work, "task", source["task_id"])
        await capture(daemon.ops, work)
    private_snap = await snapshot(daemon.ops, wid)
    private_request = _risk_request(daemon, wid, private_snap, "private", "PRIVATE register title")
    private_request["params"]["disambiguation"] = {"record_ids": [visible["result"]["record_id"]],
                                                   "rationale": "A separate concern on the same source"}
    private = await send(daemon, port, private_request, CONTRIBUTOR)
    auth = token(daemon, READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(daemon.fleet.config, read_only=True, principal_only=True)
    prefix = f"/api/v1/pm/workspaces/{wid}/register"
    _visible_synthetic(monkeypatch)
    _ensure_lineage(daemon.ops)
    extra = []
    real = pm_register._collect

    def collect(service, principal, workspace_id):
        rows, notes = real(service, principal, workspace_id)
        return [*rows, *extra], notes

    monkeypatch.setattr(pm_register, "_collect", collect)
    try:
        await _assert_row_errors(daemon, port, server, auth, wid, "governance." + visible["result"]["record_id"])
        daemon.ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source["task_id"],))
        hidden_id = "governance." + private["result"]["record_id"]
        status, body = await http(port, "GET", prefix + "/" + hidden_id, tok=auth)
        assert status == 404 and body["error"]["code"] == "NOT_FOUND" and "PRIVATE register title" not in json.dumps(body)
        with pytest.raises(OperationError) as rpc_hidden:
            await daemon.call_api("pm_register_get", {"workspace_id": wid, "row_id": hidden_id}, READER)
        assert rpc_hidden.value.status == 404 and rpc_hidden.value.code == "NOT_FOUND"
        with pytest.raises(ToolError, match="NOT_FOUND"):
            await server.call_tool("pm_register_get", {"workspace_id": wid, "row_id": hidden_id})
        listed = await _same(daemon, port, server, auth, "pm_register_list", {"workspace_id": wid}, prefix)
        assert "PRIVATE register title" not in json.dumps(listed)
        before_totals = listed["total_by_type"]
        hidden_work = "wi_" + "e" * 20
        dangling = "op_" + uuid.uuid4().hex
        daemon.ops.db.execute("INSERT INTO pm_source_lineage VALUES(?,?,?,?)", ("work_item", hidden_work, dangling, dangling))
        hidden_row = _synthetic_repair(0, hidden_work, sequence=50)
        extra.append(hidden_row)
        leak = {"rows": [pm_register._public(hidden_row)]}
        with pytest.raises(OperationError) as leaked:
            pm_source_lineage.read_response(daemon.ops.db, READER, leak)
        assert leaked.value.status == 404
        unfiltered = await _same(daemon, port, server, auth, "pm_register_list", {"workspace_id": wid}, prefix)
        filtered = await _same(daemon, port, server, auth, "pm_register_list", {"workspace_id": wid, "type": "risk"}, prefix + "?type=risk")
        assert hidden_row["row_id"] not in json.dumps(unfiltered)
        assert unfiltered["total_by_type"] == before_totals
        assert filtered["coverage"]["complete"] is True
        expected = []
        extra.clear()
        for index in range(65):
            work_item = f"wi_{index:020x}"
            original = f"op_reg{index:032x}"
            daemon.ops.db.execute("INSERT INTO pm_source_lineage VALUES(?,?,?,?)", ("work_item", work_item, original, original))
            row = _synthetic_repair(index, work_item, sequence=10_000 + index)
            extra.append(row)
            expected.append(row["source"]["id"])
        expected.reverse()
        seen, cursor, pages = [], None, 0
        first_cursor = None
        while True:
            params = {"workspace_id": wid, "limit": 100}
            query = "?limit=100"
            if cursor is not None:
                params["after"] = cursor
                query += "&after=" + cursor
            body = await _same(daemon, port, server, auth, "pm_register_list", params, prefix + query)
            pages += 1
            if pages == 1:
                first_cursor = body["next_cursor"]
                assert [row["source"]["id"] for row in body["rows"]] == expected[:64]
            seen.extend(row["source"]["id"] for row in body["rows"] if row["source"]["kind"] == "repair")
            assert body["total_by_type"]["action"] == before_totals["action"] + 65
            cursor = body["next_cursor"]
            if cursor is None:
                break
            assert pages < 6
        assert pages >= 2 and seen == expected
        sequence, row_id = pm_register._decode_cursor(first_cursor)
        assert row_id == "repair." + expected[64]
        assert sequence == 10_000
        quoted_status, quoted = await http(port, "GET", prefix + "?limit=100&after=" + quote(first_cursor, safe=""), tok=auth)
        assert quoted_status == 200 and quoted["rows"][0]["row_id"] == row_id
    finally:
        await fleet.close()


async def test_one_unverifiable_governance_record_keeps_the_readable_rows(ops, monkeypatch):
    data = await prepared(ops)
    older = await record(ops, data, title="Older risk", root_cause_key="older")
    broken = await record(ops, data, title="Unverifiable risk", root_cause_key="broken",
                          source_refs=[{"kind": "plan_input", "id": data["confirmed"]}])
    newer = await record(ops, data, title="Newer risk", root_cause_key="newer",
                         source_refs=[{"kind": "requirement", "id": "login"}])
    bad_revision = pm_governance.get_record(ops, OWNER, data["wid"], broken["record_id"])["revision"]["revision_id"]
    real = pm_governance._revision

    def revision(db, revision_id):
        if revision_id == bad_revision:
            raise OperationError("PM_GOVERNANCE_UNAVAILABLE", "Original governance revision cannot be verified", 409)
        return real(db, revision_id)

    monkeypatch.setattr(pm_governance, "_revision", revision)
    view = pm_register.list_register(ops, OWNER, data["wid"])
    gov = [row["source"]["id"] for row in view["rows"] if row["source"]["kind"] == "governance"]
    assert older["record_id"] in gov and newer["record_id"] in gov
    assert broken["record_id"] not in gov
    assert view["coverage"]["complete"] is False
    assert view["coverage"]["partial_reason"] == "source_incomplete"
    assert view["total_by_type"] is None and view["summary"] is None
    risks = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    risk_ids = [row["source"]["id"] for row in risks["rows"]]
    assert older["record_id"] in risk_ids and newer["record_id"] in risk_ids
    assert risks["total_by_type"] is None and risks["summary"] is None


async def test_oversized_first_repair_keeps_the_newer_records(ops, monkeypatch):
    data = await prepared(ops)
    pm_maintenance_repairs.schema(ops.journal)
    context = {"repair": "page"}
    digest = pm_maintenance_checks._digest(context)
    for index in (1, 2):
        ops.db.execute("""INSERT INTO pm_maintenance_repairs(
            repair_id,operation_id,workspace_id,policy_id,finding_id,work_item_id,context,context_digest,
            created_at,deadline_at,budget_period) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (f"pmrp_{index:032x}", f"op_{index:032x}", data["wid"], "pmmp_" + "a" * 32, f"finding-{index}",
             f"wi_{index:020x}", json.dumps(context), digest, 1, 2, 0))
    stored = list(ops.db.execute("SELECT repair_id, sequence FROM pm_maintenance_repairs WHERE workspace_id=? ORDER BY sequence",
                                 (data["wid"],)))
    oversized_id, newer_id = stored[0]["repair_id"], stored[1]["repair_id"]
    records = [{"repair_id": row["repair_id"], "sequence": row["sequence"], "operation_status": "running",
                "created_at": 1_760_000_000.0, "finished_at": None, "finding_id": None, "work_item_id": None,
                "deadline_at": None} for row in stored]

    def list_repairs(service, principal, workspace_id, *, after=0, limit=20):
        visible = [row for row in records if row["sequence"] > after]
        if visible and visible[0]["repair_id"] == oversized_id:
            raise OperationError("PM_REPAIR_READ_TOO_LARGE", "One whole repair record exceeds the read bound", 413)
        page = visible[:limit]
        return {"repairs": page, "next_after": page[-1]["sequence"] if len(visible) > limit else None}

    monkeypatch.setattr(pm_maintenance_repairs, "list_repairs", list_repairs)
    view = pm_register.list_register(ops, OWNER, data["wid"], row_type="action")
    listed = [row["source"]["id"] for row in view["rows"]]
    assert newer_id in listed and oversized_id not in listed
    assert view["coverage"]["complete"] is False
    assert view["coverage"]["partial_reason"] == "source_incomplete"
    assert view["total_by_type"] is None and view["summary"] is None


async def test_one_unverifiable_governance_revision_keeps_later_steps(ops, monkeypatch):
    data = await prepared(ops)
    first = await record(ops, data, kind="change", title="Direction")
    await change(ops, first, "decide", disposition="rejected", rationale="Not this direction")
    rejected = pm_governance.get_record(ops, OWNER, data["wid"], first["record_id"])["revision"]["revision_id"]
    await change(ops, first, "decide", disposition="adopted_direction", rationale="Choose this direction")
    real = pm_governance._revision

    def revision(db, revision_id):
        if revision_id == rejected:
            raise OperationError("PM_GOVERNANCE_UNAVAILABLE", "Original governance revision cannot be verified", 409)
        return real(db, revision_id)

    monkeypatch.setattr(pm_governance, "_revision", revision)
    detail = pm_register.get_register(ops, OWNER, data["wid"], "governance." + first["record_id"])
    statuses = [step["status"] for step in detail["timeline"] if step["step"] == "decision"]
    assert "adopted_direction" in statuses
    assert "rejected" not in statuses
    assert detail["timeline_coverage"]["partial_reason"] == "source_incomplete"
    assert "governance" in detail["timeline_coverage"]["sources"]


async def test_one_unreadable_review_event_keeps_the_newer_candidate(ops, monkeypatch):
    from bat_agent_connector import pm_maintenance_review
    _force_table(monkeypatch, "pm_maintenance_reviews")
    data = await prepared(ops)
    review_id = "pmrevw_" + "b" * 32
    ops.db.execute("""CREATE TABLE pm_maintenance_review_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL,
        review_id TEXT NOT NULL, workspace_id TEXT NOT NULL)""")
    events = []
    for index, title in ((1, "Older"), (2, "Broken"), (3, "Newer")):
        event_id = f"pmrve_{index:032x}"
        ops.db.execute("INSERT INTO pm_maintenance_review_events(event_id, review_id, workspace_id) VALUES(?,?,?)",
                       (event_id, review_id, data["wid"]))
        sequence = ops.db.execute("SELECT sequence FROM pm_maintenance_review_events WHERE event_id=?", (event_id,)).fetchone()[0]
        events.append({"event_id": event_id, "kind": "candidate.record", "sequence": sequence, "created_at": index,
                       "document": {"disposition": "proposal", "title": title}})
    broken = events[1]["sequence"]

    def list_events(service, principal, workspace_id, review, *, after=0, limit=20):
        visible = [row for row in events if row["sequence"] > after]
        page = visible[:limit]
        if any(row["sequence"] == broken for row in page):
            raise OperationError("PM_MAINTENANCE_REVIEW_TOO_LARGE", "One whole review event exceeds the read bound", 413)
        return {"events": page, "next_cursor": page[-1]["sequence"] if len(visible) > limit else None}

    review = {"review_id": review_id, "sequence": 4, "status": "completed", "title": "Review", "created_at": 1}
    monkeypatch.setattr(pm_maintenance_review, "list_reviews", lambda *args, **kwargs: {"reviews": [review], "next_cursor": None})
    monkeypatch.setattr(pm_maintenance_review, "list_events", list_events)
    monkeypatch.setattr(pm_maintenance_review, "get_review", lambda *args, **kwargs: {
        "review": review, "completion": {"actor": OWNER.actor, "created_at": 2, "document": {
            "direction": "accepted", "follow_ups": []}}, "events": []})
    view = pm_register.list_register(ops, OWNER, data["wid"], row_type="change")
    assert [row["source"]["id"] for row in view["rows"]] == [events[2]["event_id"], events[0]["event_id"]]
    assert view["coverage"]["partial_reason"] == "source_incomplete"
    assert view["total_by_type"] is None and view["summary"] is None
    risks = pm_register.list_register(ops, OWNER, data["wid"], row_type="risk")
    assert risks["coverage"]["complete"] is True and "review" not in risks["coverage"]["sources"]


async def test_dotted_meeting_item_ids_open_on_http_rpc_and_mcp(served, monkeypatch, mock):
    from bat_agent_connector import pm_sprints
    from tests.test_pm_meetings import create_request as meeting_create
    from tests.test_pm_sprints import NOW, active
    monkeypatch.setattr(pm_sprints, "time", SimpleNamespace(time=lambda: NOW))
    daemon, port, selection = served
    data = await sprint_prepared(daemon, selection)
    sprint = await active(daemon.ops, data)
    snap = await snapshot(daemon.ops, data["workspace_id"])
    operation, _ = daemon.ops.create(CONTRIBUTOR, **meeting_create(daemon.ops, sprint, snap, "retro"))
    result = (await settle(daemon.ops, operation))["result"]
    target = {"workspace_id": data["workspace_id"], "meeting_id": result["meeting_id"]}
    params = draft_params("retro")
    params["follow_ups"][0]["id"] = "check.quality"
    params["decisions"][0]["id"] = "retain.direction"
    await apply(daemon.ops, target, "draft", who=CONTRIBUTOR, **params)
    await note(daemon.ops, target)
    await complete(daemon.ops, target, decision_ids=["retain.direction"])
    wid, mid = data["workspace_id"], target["meeting_id"]
    follow = f"meeting.{mid}.follow_up.check.quality"
    decision = f"meeting.{mid}.decision.retain.direction"
    auth = token(daemon, READER.actor, "observe")
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", auth)
    server, fleet = mcp_server.build_server(daemon.fleet.config, read_only=True, principal_only=True)
    prefix = f"/api/v1/pm/workspaces/{wid}/register"
    before = daemon.ops.db.total_changes
    try:
        status, body = await http(port, "GET", prefix, tok=auth)
        assert status == 200, body
        listed = {row["row_id"] for row in body["rows"]}
        assert follow in listed and decision in listed
        assert body["coverage"]["complete"] is True
        rpc = await daemon.call_api("pm_register_list", {"workspace_id": wid}, READER)
        mcp_list = unpack(await server.call_tool("pm_register_list", {"workspace_id": wid}))
        assert {row["row_id"] for row in rpc["rows"]} >= {follow, decision}
        assert {row["row_id"] for row in mcp_list["rows"]} >= {follow, decision}
        for row_id in (follow, decision):
            status, detail = await http(port, "GET", prefix + "/" + row_id, tok=auth)
            assert status == 200, detail
            assert detail["row"]["row_id"] == row_id
            detail_params = {"workspace_id": wid, "row_id": row_id}
            rpc_detail = await daemon.call_api("pm_register_get", detail_params, READER)
            mcp_detail = unpack(await server.call_tool("pm_register_get", detail_params))
            assert rpc_detail["row"]["source"] == detail["row"]["source"]
            assert mcp_detail["row"]["source"] == detail["row"]["source"]
        assert daemon.ops.db.total_changes == before
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_repair_timeline_links_the_finding_both_ways(ops, monkeypatch):
    data = await prepared(ops)
    pm_maintenance_repairs.schema(ops.journal)
    finding_id, repair_id = "pmfn_" + "b" * 32, "pmrp_" + "a" * 32
    work_item = "wi_" + "1" * 20
    context = {"visible": True}
    ops.db.execute("""INSERT INTO pm_maintenance_repairs(
        repair_id,operation_id,workspace_id,policy_id,finding_id,work_item_id,context,context_digest,
        created_at,deadline_at,budget_period) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (repair_id, "op_" + "a" * 32, data["wid"], "pmmp_" + "a" * 32, finding_id, work_item,
         json.dumps(context), pm_maintenance_checks._digest(context), 1_760_000_000.0, 1_760_003_600.0, 0))
    repair = {"repair_id": repair_id, "operation_status": "running", "finding_id": finding_id,
              "work_item_id": work_item, "created_at": 1_760_000_000.0, "finished_at": None,
              "deadline_at": 1_760_003_600.0, "sequence": 4}
    monkeypatch.setattr(pm_maintenance_repairs, "get_repair", lambda *args, **kwargs: {"repair": repair})

    def readable_finding(service, principal, workspace_id, ident):
        assert ident == finding_id
        return pm_register._finding_row(service, workspace_id, {"finding_id": finding_id, "state": "observed_open",
                                                                "facts": {}, "native": {}}, "issues", None, None, None, set())

    monkeypatch.setattr(pm_register, "_finding_by_id", readable_finding)
    repair_detail = pm_register.get_register(ops, OWNER, data["wid"], "repair." + repair_id)
    finding_detail = pm_register.get_register(ops, OWNER, data["wid"], "finding." + finding_id)
    assert _shows(repair_detail, "finding", finding_id)
    assert any(step["step"] == "work" and step["ref"]["id"] == work_item for step in repair_detail["timeline"])
    assert _shows(finding_detail, "repair", repair_id)
    assert repair_detail["timeline_coverage"]["complete"] is True
    assert finding_detail["timeline_coverage"]["complete"] is True

    def broken_finding(*args, **kwargs):
        raise OperationError("PM_FINDINGS_TOO_LARGE", "One finding exceeds the read bound", 413)

    monkeypatch.setattr(pm_register, "_finding_by_id", broken_finding)
    partial = pm_register.get_register(ops, OWNER, data["wid"], "repair." + repair_id)
    assert any(step["step"] == "work" and step["ref"]["id"] == work_item for step in partial["timeline"])
    assert not _shows(partial, "finding", finding_id)
    assert partial["timeline_coverage"]["partial_reason"] == "source_incomplete"
    assert "finding" in partial["timeline_coverage"]["sources"]

    monkeypatch.setattr(pm_register, "_finding_by_id", readable_finding)

    def broken_repair(*args, **kwargs):
        raise OperationError("PM_REPAIR_READ_TOO_LARGE", "One repair exceeds the read bound", 413)

    monkeypatch.setattr(pm_maintenance_repairs, "get_repair", broken_repair)
    hidden_repair = pm_register.get_register(ops, OWNER, data["wid"], "finding." + finding_id)
    assert not _shows(hidden_repair, "repair", repair_id)
    assert hidden_repair["timeline_coverage"]["partial_reason"] == "source_incomplete"
    assert "repair" in hidden_repair["timeline_coverage"]["sources"]


async def test_intake_timeline_links_selected_governance_both_ways(ops, monkeypatch):
    from tests.test_pm_follow_up import prepare
    from tests.test_pm_follow_up import prepared as follow_prepared
    _install_follow_up(ops, monkeypatch)
    data = await follow_prepared(ops)
    original = await record(ops, data, kind="risk", root_cause_key="linked-cause", title="Linked risk")
    intake = await prepare(ops, data, governance_revision_ids=[original["revision_id"]])
    detail = pm_register.get_register(ops, OWNER, data["wid"], "follow_up." + intake["intake_id"])
    reverse = pm_register.get_register(ops, OWNER, data["wid"], "governance." + original["record_id"])
    assert _shows(detail, "governance", original["record_id"])
    assert _shows(reverse, "follow_up", intake["intake_id"])
    assert detail["timeline_coverage"]["complete"] is True
    assert reverse["timeline_coverage"]["complete"] is True


async def test_intake_timeline_links_selected_meeting_follow_up_both_ways(ops, monkeypatch):
    from tests.test_pm_follow_up import completed_meeting, prepare
    _install_follow_up(ops, monkeypatch)
    data, target, completion_id = await completed_meeting(ops)
    recorded = await apply(ops, target, "follow_up.record", follow_up_id="check_quality", status="reported_done",
                           result="The reviewer says it is all fixed")
    intake = await prepare(ops, data, meeting_follow_up_refs=[{
        "completion_id": completion_id, "follow_up_id": "check_quality", "result_event_id": recorded["event_id"]}])
    follow_ident = target["meeting_id"] + ".follow_up.check_quality"
    detail = pm_register.get_register(ops, OWNER, data["wid"], "follow_up." + intake["intake_id"])
    reverse = pm_register.get_register(ops, OWNER, data["wid"], "meeting." + follow_ident)
    assert _shows(detail, "meeting", follow_ident)
    assert _shows(reverse, "follow_up", intake["intake_id"])


async def test_disambiguation_links_both_governance_records(ops):
    data = await prepared(ops)
    first = await record(ops, data, title="First risk", root_cause_key="first-cause")
    second = await record(ops, data, title="Second risk", root_cause_key="second-cause",
                          source_refs=[{"kind": "plan_input", "id": data["assumption"]}],
                          disambiguation={"record_ids": [first["record_id"]], "rationale": "Same source, separate record"})
    forward = pm_register.get_register(ops, OWNER, data["wid"], "governance." + second["record_id"])
    reverse = pm_register.get_register(ops, OWNER, data["wid"], "governance." + first["record_id"])
    assert _shows(forward, "governance", first["record_id"])
    assert _shows(reverse, "governance", second["record_id"])
    assert forward["timeline_coverage"]["complete"] is True
    assert reverse["timeline_coverage"]["complete"] is True


def _review_detail(review_id, *, follow_ups=None, events=None):
    review = {"review_id": review_id, "sequence": 4, "status": "completed", "title": "Review the gap",
              "created_at": 1_760_000_000.0}
    completion = {"actor": OWNER.actor, "created_at": 1_760_000_100.0, "document": {
        "direction": "accepted", "follow_ups": follow_ups or []}}
    return review, completion, events or []


async def test_review_follow_up_timeline_links_the_review_both_ways(ops, monkeypatch):
    from bat_agent_connector import pm_maintenance_review
    data = await prepared(ops)
    review_id = "pmrevw_" + "a" * 32
    review, completion, events = _review_detail(review_id, follow_ups=[{
        "id": "coverage", "text": "Inspect the gap", "status": "open", "owner_actor": OWNER.actor}])
    monkeypatch.setattr(pm_maintenance_review, "get_review", lambda *args, **kwargs: {
        "review": review, "completion": completion, "events": []})
    monkeypatch.setattr(pm_maintenance_review, "list_events", lambda *args, **kwargs: {"events": events, "next_cursor": None})
    follow_ident = review_id + ".follow_up.coverage"
    detail = pm_register.get_register(ops, OWNER, data["wid"], "follow_up." + follow_ident)
    reverse = pm_register.get_register(ops, OWNER, data["wid"], "meeting." + review_id)
    assert _shows(detail, "meeting", review_id)
    assert _shows(reverse, "follow_up", follow_ident)
    assert detail["timeline_coverage"]["complete"] is True
    assert reverse["timeline_coverage"]["complete"] is True


async def test_review_candidate_timeline_links_the_review_both_ways(ops, monkeypatch):
    from bat_agent_connector import pm_maintenance_review
    data = await prepared(ops)
    pm_maintenance_review.schema(ops.journal)
    review_id, event_id = "pmrevw_" + "c" * 32, "pmrve_" + "d" * 32
    ops.db.execute("""INSERT INTO pm_maintenance_review_events(
        event_id,review_id,workspace_id,kind,document,document_digest,source_binding,actor,created_at,operation_id)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (event_id, review_id, data["wid"], "candidate.record", "{}", "digest", "{}", OWNER.actor, 1.0, "op_" + "e" * 32))
    event = {"event_id": event_id, "kind": "candidate.record", "sequence": 1, "created_at": 1_760_000_050.0,
             "document": {"disposition": "proposal", "title": "Narrow a verification gap"}}
    review, completion, events = _review_detail(review_id, events=[event])
    monkeypatch.setattr(pm_maintenance_review, "get_review", lambda *args, **kwargs: {
        "review": review, "completion": completion, "events": []})
    monkeypatch.setattr(pm_maintenance_review, "list_events", lambda *args, **kwargs: {"events": events, "next_cursor": None})
    detail = pm_register.get_register(ops, OWNER, data["wid"], "proposal." + event_id)
    reverse = pm_register.get_register(ops, OWNER, data["wid"], "meeting." + review_id)
    assert _shows(detail, "meeting", review_id)
    assert _shows(reverse, "proposal", event_id)


async def test_plan_timeline_links_the_visible_proposal_both_ways(ops, monkeypatch):
    from bat_agent_connector import pm_conversation_proposals
    data = await prepared(ops)
    candidate = await plan_draft(ops, data["target"], title="Fixed next plan")
    ctx = adoption(ops, data["target"], candidate["revision_id"], previous=data["plan"]["revision_id"])
    adopt(ctx)
    await settle(ops, ctx.op)
    proposal_id, proposal_op = "pmcp_" + "ab" * 16, "op_reg" + "c" * 32
    ops.db.execute("""CREATE TABLE pm_conversation_proposals (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL UNIQUE,
        workspace_id TEXT NOT NULL, thread_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE)""")
    ops.db.execute("INSERT INTO pm_conversation_proposals(proposal_id,workspace_id,thread_id,operation_id) VALUES(?,?,?,?)",
                   (proposal_id, data["wid"], "thread", proposal_op))
    # The adoption's own sources stay readable; this root only names the proposal operation.
    _visible_synthetic(monkeypatch)
    _ensure_lineage(ops)
    ops.db.execute("INSERT INTO pm_source_lineage VALUES(?,?,?,?)", ("operation", ctx.operation_id, proposal_op, ctx.operation_id))
    calls = []

    def get_proposal(service, principal, workspace_id, ident):
        calls.append(ident)
        return {"proposal": {"proposal_id": proposal_id, "sequence": 1, "created_at": 1_760_000_000.0,
                             "document": {"title": "Narrow the gap", "requirements": [{"id": "login"}]},
                             "validation": {"status": "valid"}}}

    monkeypatch.setattr(pm_conversation_proposals, "get_proposal", get_proposal)
    listed = pm_register.list_register(ops, OWNER, data["wid"])
    assert calls == []
    assert proposal_id not in {row["source"]["id"] for row in listed["rows"]}
    detail = pm_register.get_register(ops, OWNER, data["wid"], "plan_revision." + candidate["revision_id"])
    reverse = pm_register.get_register(ops, OWNER, data["wid"], "proposal." + proposal_id)
    baseline = pm_register.get_register(ops, OWNER, data["wid"], "plan_revision." + data["plan"]["revision_id"])
    assert _shows(detail, "proposal", proposal_id)
    assert _shows(reverse, "plan_revision", candidate["revision_id"])
    assert not _shows(baseline, "proposal", proposal_id)
    assert calls and set(calls) == {proposal_id}
