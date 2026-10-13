"""Issues, actions and changes register: identities, explicit links and honest totals."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bat_agent_connector import mcp_server, pm_register
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import http, token, write_frames
from tests.test_pm_discussions import CONTRIBUTOR, OUTSIDER, OWNER, READER, workspace
from tests.test_pm_evidence import capture, link, task
from tests.test_pm_governance import ops as ops
from tests.test_pm_governance import prepared, record
from tests.test_pm_governance_api import finding
from tests.test_pm_run_api import unpack
from tests.test_pm_snapshots import ops as snapshot_ops_fixture
from tests.test_pm_snapshots import snapshot, work_for
from tests.test_pm_sprint_api import pm as pm
from tests.test_pm_sprint_api import served as served
from tests.test_pm_worktree import world as world

snapshot_ops = snapshot_ops_fixture

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$")
ROW_KEYS = {"row_id", "type", "title", "source", "impact", "priority", "owner", "due", "status", "next_step",
            "created_at", "updated_at", "related", "available_actions"}


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
    assert risk_row["row_id"] == "governance:" + risk["record_id"]
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
        pm_register.get_register(ops, OWNER, data["wid"], "governance:" + hidden["record_id"])
    assert missing.value.status == 404 and "Private source issue" not in str(missing.value)
    detail = pm_register.get_register(ops, OWNER, data["wid"], "governance:" + kept["record_id"])
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
        detail = pm_register.get_register(ops, OWNER, data["wid"], "governance:" + result["record_id"])
        assert detail["row"]["source"]["id"] == result["record_id"]
        assert detail["timeline_coverage"]["complete"] is False
    with pytest.raises(OperationError) as absent:
        pm_register.get_register(ops, OWNER, data["wid"], "governance:pmgov_" + "ab" * 16)
    assert absent.value.status == 404 and absent.value.code == "NOT_FOUND"
    with pytest.raises(OperationError) as incomplete:
        pm_register.get_register(ops, OWNER, data["wid"], "meeting:pmmeet_" + "ab" * 16 + "~follow_up~verify_login")
    assert incomplete.value.status == 409 and incomplete.value.code == "PM_REGISTER_INCOMPLETE"


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
    finally:
        await fleet.close()
