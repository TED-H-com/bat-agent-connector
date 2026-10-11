"""B01, 計畫 §08: observation and cleanup share the same registry creation intent."""

import pytest

from bat_agent_connector import registry
from bat_agent_connector.resource_ids import connector_made, registry_worktree_intent, registry_worktree_root
from bat_agent_connector.resource_policy import worktree_maker


@pytest.mark.parametrize("case", ["failover", "reviewer", "reviewer_lookup", "warm_reuse", "out_of_order", "missing_parent"])
def test_registry_worktree_intent_creation_root(case):
    root = {"host": "h1", "session_id": "root", "created_at": "123.4560", "worktree_path": "/srv/wt"}
    middle = {"host": "h1", "session_id": "middle", "created_at": 124, "worktree_path": "/srv/wt", "failover_of": "root"}
    successor = {"host": "h1", "session_id": "successor", "created_at": 125, "worktree_path": "/srv/wt", "failover_of": "middle"}
    entries, sid, lead_of = [root, middle, successor], "successor", None
    if case == "reviewer":
        successor = {**successor, "failover_of": None, "role": "reviewer", "lead_session_id": "root"}
        entries = [root, successor]
    elif case == "reviewer_lookup":
        successor = {**successor, "failover_of": None, "role": "reviewer", "task_id": "task"}
        entries = [root, successor]
        def lead_of(task_id):
            return "root" if task_id == "task" else None
    elif case == "warm_reuse":
        root.update(task_id="new-task", warm_from_task_id="old-task")
        successor = {**successor, "failover_of": None, "shares_worktree_with": "root"}
        entries = [root, successor]
        assert registry_worktree_intent(entries, "h1", "root") == ("registry", "root@123.4560")
    elif case == "out_of_order":
        entries = [successor, middle, root]
    elif case == "missing_parent":
        root["failover_of"] = "missing"
    before = [dict(e) for e in entries]
    assert registry_worktree_intent(entries, "h1", sid, lead_of) == ("registry", "root@123.4560")
    assert entries == before


def test_registry_worktree_intent_cycle_is_unknown():
    entries = [{"session_id": "a", "created_at": 1, "worktree_path": "/srv/wt", "failover_of": "b"},
               {"session_id": "b", "created_at": 2, "worktree_path": "/srv/wt", "shares_worktree_with": "a"}]
    assert registry_worktree_intent(entries, "h1", "a") is None
    assert registry_worktree_intent(list(reversed(entries)), "h1", "b") is None


@pytest.mark.parametrize("fields", [{"worktree_made_by": "connector"}, {"checkpoint_id": "cp"},
                                   {"integration_operation_id": "op"}, {"branch": "batc/task-fixture"},
                                   {"created_at": None}, {"worktree_path": None}])
def test_registry_worktree_intent_excludes_other_creation_slots(fields):
    root = {"session_id": "root", "created_at": 0, "worktree_path": "/srv/wt", **fields}
    successor = {"session_id": "successor", "created_at": 1, "worktree_path": root.get("worktree_path"), "failover_of": "root"}
    assert registry_worktree_intent([successor, root], "h1", "successor") is None


def test_registry_worktree_intent_keeps_hosts_and_loaded_number_format():
    entries = [{"host": "h2", "session_id": "root", "created_at": 1, "worktree_path": "/srv/wt"},
               {"host": "h1", "session_id": "root", "created_at": 2.0, "worktree_path": "/srv/wt"}]
    assert registry_worktree_intent(entries, "h1", "root") == ("registry", "root@2.0")
    assert registry_worktree_intent(entries, "h2", "root") == ("registry", "root@1")
    assert registry_worktree_intent(entries, "h1", "missing") is None


@pytest.mark.parametrize("marker", [{}, {"worktree_made_by": "connector"}, {"checkpoint_id": "cp"},
                                  {"integration_operation_id": "op"}, {"branch": "batc/task-fixture"}])
@pytest.mark.parametrize("shape", ["root", "failover", "sharing_failover", "nonsharing_failover",
                                  "warm_reuse", "reviewer", "reviewer_lookup", "shared"])
def test_b01_registry_identity_and_policy_agree_on_creation_root(marker, shape):
    """B01, §08/§06: IDs and the ownership classifier share creation-root maker evidence."""
    root = {"host": "h1", "session_id": "root", "created_at": "123.4560", "worktree_path": "/srv/wt",
            "role": "lead", "task_id": "task", "branch": "bat/worktree-fixture", **marker}
    row = {"host": "h1", "session_id": "child", "created_at": 124, "worktree_path": "/srv/wt",
           "branch": root["branch"]}  # Failover/review copies branch but not the explicit creation markers.
    lead_of = None
    if shape in {"root", "warm_reuse"}:
        if shape == "warm_reuse":
            root.update(task_id="new-task", warm_from_task_id="task")
        row = root
    elif shape in {"failover", "sharing_failover"}:
        row["failover_of"] = "root"
        if shape == "sharing_failover":
            row["shares_worktree_with"] = "root"
    elif shape == "nonsharing_failover":
        row.update(failover_of="root", shares_worktree_with=None, worktree_path=None, branch=None, cwd="/srv/origin")
    elif shape == "shared":
        row["shares_worktree_with"] = "root"
    elif shape == "reviewer":
        row.update(role="reviewer", task_id="task", lead_session_id="root")
    else:
        row.update(role="reviewer", task_id="task")
        def lead_of(task_id):
            return "root" if task_id == "task" else None
    entries = [row, root] if row is not root else [root]  # Root may be appended after its successor.
    before = [dict(e) for e in entries]
    expected_root = row if shape == "nonsharing_failover" else root
    assert registry_worktree_root(entries, "h1", row["session_id"], lead_of) is expected_root
    intent = registry_worktree_intent(entries, "h1", row["session_id"], lead_of)
    assert (worktree_maker(row, entries, lead_of) == "connector") == connector_made(expected_root)
    if shape == "nonsharing_failover":
        assert intent is None and expected_root["worktree_path"] is None
    else:
        assert (intent is None) == connector_made(root)
    if not marker and shape != "nonsharing_failover":
        assert intent == ("registry", "root@123.4560")
    assert entries == before


@pytest.mark.parametrize("child_path", [None, "/srv/new-wt"])
def test_b01_nonsharing_failover_has_only_its_own_creation_intent(child_path):
    """B01, §08: failover ancestry alone is not worktree sharing."""
    root = {"session_id": "root", "created_at": 123, "worktree_path": "/srv/old-wt"}
    child = {"session_id": "child", "created_at": 124, "failover_of": "root", "shares_worktree_with": None,
             "worktree_path": child_path, "cwd": child_path or "/srv/origin"}
    assert registry_worktree_root([root, child], "h1", "child") is child
    assert registry_worktree_intent([root, child], "h1", "child") == (
        ("registry", "child@124") if child_path else None)


@pytest.mark.parametrize("explicit_lead", [False, True])
@pytest.mark.parametrize("child_path", ["/srv/wt", "/srv/other", None])
@pytest.mark.parametrize("task_path", ["/srv/wt", "/srv/other", None])
def test_b01_reviewer_root_requires_matching_carrier_or_recorded_task_path(explicit_lead, child_path, task_path):
    """B01, §08: a missing reviewer path needs the task's recorded lead/path, not just a task ID."""
    root = {"session_id": "root", "created_at": 123, "worktree_path": "/srv/wt"}
    child = {"session_id": "review", "created_at": 124, "role": "reviewer", "task_id": "task",
             "worktree_path": child_path, "lead_session_id": "root" if explicit_lead else None}

    def lead_of(task_id):
        assert task_id == "task"
        return {"session_id": "root", "worktree_path": task_path}

    shares = child_path == "/srv/wt" or child_path is None and task_path == "/srv/wt"
    assert registry_worktree_root([root, child], "h1", "review", lead_of) is (root if shares else child)
    if child_path is None:
        # A legacy ID-only callback cannot supply the missing path evidence.
        assert registry_worktree_root([root, child], "h1", "review", lambda tid: "root") is child


def test_b01_reviewer_task_path_must_name_the_same_lead():
    """B01, §08: task carrier evidence for another lead cannot confirm an explicit parent."""
    root = {"session_id": "root", "created_at": 123, "worktree_path": "/srv/wt"}
    child = {"session_id": "review", "created_at": 124, "role": "reviewer", "task_id": "task",
             "lead_session_id": "root", "worktree_path": None}
    assert registry_worktree_root([root, child], "h1", "review",
                                  lambda tid: {"session_id": "other", "worktree_path": "/srv/wt"}) is child


def test_b01_current_connector_evidence_preserves_policy_refusal_and_has_no_bat_identity():
    """B01, §06/§08: parent disagreement cannot relax the existing BAT-action refusal."""
    root = {"host": "h1", "session_id": "root", "created_at": 1, "worktree_path": "/srv/wt"}
    row = {"host": "h1", "session_id": "child", "created_at": 2, "worktree_path": "/srv/wt",
           "failover_of": "root", "checkpoint_id": "cp"}
    assert worktree_maker(row, [row, root]) == "connector"
    assert registry_worktree_intent([row, root], "h1", "child") is None


@pytest.mark.parametrize("marker", [{}, {"worktree_made_by": "connector"}, {"checkpoint_id": "cp"},
                                  {"integration_operation_id": "op"}, {"branch": "batc/task-fixture"}])
def test_b01_warm_claim_keeps_creation_markers_and_identity(marker, controller_context):
    """B01, §08: actual warm reuse transfers task ownership without changing creation evidence."""
    registry.reserve("h1", {"session_id": "root", "workspace_id": "ws", "cwd": "/srv/wt",
                            "worktree_path": "/srv/wt", "branch": "bat/worktree-fixture", "task_id": "old",
                            "role": "lead", **marker}, 8)
    registry.update("h1", "root", status="active")
    before = registry.get("h1", "root")
    intent = registry_worktree_intent([before], "h1", "root")
    registry.claim_warm("h1", "root", previous_task_id="old", task_id="new", workspace_id="ws",
                        cwd="/srv/wt", branch=before["branch"], expected_incarnation=before["controller_incarnation"])
    after = registry.get("h1", "root")
    assert after["task_id"] == "new" and after["warm_from_task_id"] == "old"
    for key in ("worktree_made_by", "checkpoint_id", "integration_operation_id", "branch", "created_at", "worktree_path"):
        assert after.get(key) == before.get(key)
    assert registry_worktree_intent([after], "h1", "root") == intent
    assert worktree_maker(after, [after]) == worktree_maker(before, [before])
