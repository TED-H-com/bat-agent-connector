"""Register issue mappings and repairs: visibility before caps, a 409 skip is partial, not hidden."""
from __future__ import annotations

from bat_agent_connector import pm_issue_mappings, pm_register, pm_schedule, pm_work_packages, work_items
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector.operations import OperationError
from tests.test_pm_discussions import OWNER
from tests.test_pm_evidence import bind_work, item
from tests.test_pm_roadmap import mapped
from tests.test_pm_roadmap import ops as ops  # noqa: F401,PLC0414
from tests.test_pm_schedule import _tracked_issue, withdraw

VISIBLE = "pmim_" + "b" * 32
HIDDEN = "pmim_" + "a" * 32


def _issue(service, wid, work_id, binding, title, mapping_id):
    letter = mapping_id[-1]
    _tracked_issue(service, wid, work_id, binding, title, mapping_id=mapping_id,
                   package_id="pmwp_" + letter * 32, revision_id="pmir_" + letter * 32,
                   operation_id="op_" + letter * 16)


def _mappings(view):
    return [row["source"]["id"] for row in view["rows"] if row["source"]["kind"] == "issue_mapping"]


async def test_lineage_hidden_newest_mapping_takes_no_register_slot(ops, monkeypatch):
    target, plan, work, binding, *_ = await mapped(ops)
    wid = target["workspace_id"]
    pid = work_items._get_item(ops.db, work["work_item_id"])["project_id"]
    hidden = {"workspace_id": wid, "work_item_id": await item(ops, pid, title="Hidden deliverable")}
    hidden_binding = await bind_work(ops, hidden, plan_revision_id=plan["revision_id"], requirement_ids=["login"])
    # The visible mapping is older; the hidden mapping is the newest record.
    _issue(ops, wid, work["work_item_id"], binding, "Visible issue", VISIBLE)
    _issue(ops, wid, hidden["work_item_id"], hidden_binding, "Hidden issue", HIDDEN)
    withdraw(ops, "work_item", hidden["work_item_id"])
    # list_mappings checks package sources only, so the hidden mapping still reaches the register scan.
    listed = pm_issue_mappings.list_mappings(ops, OWNER, wid)
    assert [row["mapping_id"] for row in listed["mappings"]] == [VISIBLE, HIDDEN]
    monkeypatch.setattr(pm_register, "MAX_SCAN", 1)
    for row_type in (None, "issue"):
        view = pm_register.list_register(ops, OWNER, wid, row_type=row_type)
        assert _mappings(view) == [VISIBLE]
        assert view["coverage"]["complete"] is True and "issue_mapping" not in view["coverage"]["sources"]
        assert view["total_by_type"]["issue"] == 1 and view["summary"] is not None
        assert "Hidden issue" not in str(view)


async def test_a_409_mapping_makes_the_register_source_incomplete(ops, monkeypatch):
    target, _plan, work, binding, *_ = await mapped(ops)
    wid = target["workspace_id"]
    _issue(ops, wid, work["work_item_id"], binding, "Issue A", HIDDEN)
    _issue(ops, wid, work["work_item_id"], binding, "Issue B", VISIBLE)
    baseline = pm_register.list_register(ops, OWNER, wid)
    assert sorted(_mappings(baseline)) == [HIDDEN, VISIBLE] and baseline["total_by_type"]["issue"] == 2
    original = pm_work_packages._access

    def limited(service, principal, record):
        if record["package_id"] == "pmwp_" + "a" * 32:
            raise OperationError("PM_SOURCE_LINEAGE_LIMIT", "Too many original sources for one resource", 409)
        return original(service, principal, record)
    monkeypatch.setattr(pm_work_packages, "_access", limited)

    listed = pm_issue_mappings.list_mappings(ops, OWNER, wid)
    assert [row["mapping_id"] for row in listed["mappings"]] == [VISIBLE]
    assert listed["coverage"]["source_incomplete"] is True
    assert listed["coverage"]["visibility_filtered"] is False

    view = pm_register.list_register(ops, OWNER, wid)
    assert _mappings(view) == [VISIBLE]
    assert view["coverage"]["complete"] is False
    assert view["coverage"]["partial_reason"] == "source_incomplete"
    assert "issue_mapping" in view["coverage"]["sources"]
    assert view["total_by_type"] is None and view["summary"] is None
    issues = pm_register.list_register(ops, OWNER, wid, row_type="issue")
    assert issues["total_by_type"] is None and issues["coverage"]["partial_reason"] == "source_incomplete"

    # The schedule keeps its documented handling: a 409 mapping is hidden, not partial.
    body = pm_schedule.read(ops, OWNER, wid)
    ids = {row["id"] for row in body["rows"] + body["unscheduled"]}
    assert f"issue:{VISIBLE}" in ids and f"issue:{HIDDEN}" not in ids
    assert body["coverage"]["visibility_filtered"] is True
    assert body["coverage"]["issues_truncated"] is False and body["partial"] is False


def _repair(index, work_item_id):
    return {"repair_id": f"pmrp_{index:032x}", "operation_status": "succeeded", "work_item_id": work_item_id,
            "created_at": 1.0 + index, "finished_at": 2.0 + index, "sequence": index + 1}


async def test_lineage_hidden_newest_repair_takes_no_register_slot(ops, monkeypatch):
    target, *_ = await mapped(ops)
    wid = target["workspace_id"]
    visible_work, hidden_work = "wi-visible-repair", "wi-hidden-repair"
    withdraw(ops, "work_item", hidden_work)
    records = [_repair(0, visible_work), _repair(1, hidden_work)]
    monkeypatch.setattr(repairs, "list_repairs", lambda service, principal, workspace_id, after=0, limit=50: {
        "repairs": [record for record in records if record["sequence"] > after][:limit], "next_after": None})
    table = pm_register._table
    monkeypatch.setattr(pm_register, "_table", lambda db, name: name == "pm_maintenance_repairs" or table(db, name))
    monkeypatch.setattr(pm_register, "MAX_SCAN", 1)
    rows, truncated, bad = pm_register._collect_repairs(ops, OWNER, wid)
    assert [row["source"]["id"] for row in rows] == [records[0]["repair_id"]]
    assert (truncated, bad) == (False, False)


def test_newest_admits_before_the_window_and_marks_a_conflict_partial():
    pages = {0: {"items": [1, 2, 3], "next_cursor": 3}, 3: {"items": [4, 5], "next_cursor": None}}

    def fetch(after, _size):
        return pages[after]

    hidden = {4, 5}
    kept, truncated, bad = pm_register._newest(fetch, "items", 2, admit=lambda value: value not in hidden)
    assert (kept, truncated, bad) == ([2, 3], True, False)
    hidden = {1, 4, 5}
    kept, truncated, bad = pm_register._newest(fetch, "items", 2, admit=lambda value: value not in hidden)
    assert (kept, truncated, bad) == ([2, 3], False, False)

    def conflicted(value):
        if value == 5:
            raise OperationError("PM_SOURCE_LINEAGE_LIMIT", "Too many original sources", 409)
        return True
    kept, truncated, bad = pm_register._newest(fetch, "items", 10, admit=conflicted)
    assert (kept, truncated, bad) == ([1, 2, 3, 4], True, True)
