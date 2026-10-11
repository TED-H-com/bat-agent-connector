"""計畫 §08/§11/§19, B01/B02/B03: server observation contracts and read-only evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import replace

import pytest

from bat_agent_connector import api_auth, cli, mcp_server, registry
from bat_agent_connector.inventory import Inventory
from bat_agent_connector.observation import (
    Observation,
    bind_worktree,
    close_relations,
    cursor_out,
    dump,
    index,
    remember,
    saved_fact,
    worktree,
)
from bat_agent_connector.operations import OperationError
from bat_agent_connector.resource_ids import worktree_id
from bat_agent_connector.task_journal import LATEST_DATA_STEP, Journal
from tests.conftest import adopt, controller_scope, make_config
from tests.test_api_v1 import (  # noqa: F401 - shared fixtures
    MANUAL,
    http,
    token,
    write_frames,
)
from tests.test_api_v1 import (
    daemon as _daemon,
)
from tests.test_api_v1 import (
    served as _served,
)

daemon = _daemon
served = _served


def task(j, key, **kw):
    return j.submit(project="p", host="h1", workspace="w", original_words="private request", idempotency_key=key, **kw)


def bind(j, t, sid, role="lead", reason="start"):
    c, _ = j.command(t["task_id"], "start_" + role, sid, {"agent": "codex"}, t["task_id"] + ":" + role + ":" + sid)
    j.command_status(c["command_id"], "settled")
    j.add_branch(t["task_id"], session_id=sid, provider="codex", role=role, reason=reason)
    return c


async def test_projection_failure_keeps_core_write_and_flags_event(tmp_path, monkeypatch, caplog):
    """B01/B03, §08/§11: a broken read projection cannot fail a task or an operation step."""
    from bat_agent_connector import observation
    from bat_agent_connector.operations import ActionDef, OperationService

    j = Journal(tmp_path / "j.db")
    t = task(j, "projection")
    original = observation.record_event
    failed = []

    def fail(journal, seq, **kwargs):
        original(journal, seq, **kwargs)
        kind = journal.db.execute("SELECT kind FROM api_events WHERE seq=?", (seq,)).fetchone()[0]
        if kind in {"task.state", "operation.step.started"}:
            # Include nested relation events and resource/revision rows in the partial projection.
            observation.relation(journal, t, "partial", "lead", str(seq), seq)
            failed.append(seq)
            raise KeyError("private exception text")

    monkeypatch.setattr(observation, "record_event", fail)
    j.change(t["task_id"], "dispatching")
    assert j.get(t["task_id"])["state"] == "dispatching"
    calls = []

    async def run(ctx):
        async def call():
            calls.append("ran")
            return {"status": "succeeded"}
        return await ctx.step("call", call)

    ops = OperationService(j, actions=[ActionDef("fixture.projection", "observe", "Fixture only", run)])
    principal = api_auth.Principal("agent", frozenset({"observe"}))
    op, _ = ops.create(principal, action="fixture.projection", target={"host": "h1", "session_id": "sid"}, idempotency_key="projection")
    await ops.drain()
    assert calls == ["ran"] and ops.get(op["operation_id"])["status"] == "succeeded"
    assert j.db.execute("SELECT status FROM operation_steps WHERE operation_id=?", (op["operation_id"],)).fetchone()[0] == "succeeded"
    assert len(failed) == 2
    for seq in failed:
        assert j.db.execute("SELECT COUNT(*) FROM api_event_resources WHERE seq=?", (seq,)).fetchone()[0] == 0
        assert j.db.execute("SELECT COUNT(*) FROM relation_revisions WHERE seq=?", (seq,)).fetchone()[0] == 0
    assert j.db.execute("SELECT COUNT(*) FROM observation_relations WHERE session_resource_id='h1/partial'").fetchone()[0] == 0
    assert j.db.execute("SELECT COUNT(*) FROM observation_resources WHERE resource_id='h1/partial'").fetchone()[0] == 0
    events = {e["seq"]: e for e in j.api_events()["events"]}
    obs = Observation(j)
    history = obs.history("execution", t["task_id"])["events"] + obs.history("session", "h1/sid")["events"]
    assert set(failed) <= {e["seq"] for e in history}
    assert all(events[seq]["context"] == {"projection_error": "KeyError"} for seq in failed)
    assert all(e["context"] == {"projection_error": "KeyError"} for e in history if e["seq"] in failed)
    assert "private exception text" not in caplog.text
    assert caplog.text.count("observation projection failed") == 2
    j.close()


def test_b01_warm_reuse_reviewer_followup_and_command_ranges(tmp_path):
    j = Journal(tmp_path / "j.db")
    obs = Observation(j)
    a = task(j, "a")
    first = bind(j, a, "shared")
    bind(j, a, "review", "reviewer")
    j.change(a["task_id"], "failed")
    b = task(j, "b", parent_task_id=a["task_id"], continuation=True)
    second = bind(j, b, "shared", reason="warm_reuse")
    bind(j, b, "replacement", reason="replacement")
    rels = obs.relations("session", "h1/shared")["relations"]
    assert len(rels) == 2 and {r["execution_id"] for r in rels} == {a["task_id"], b["task_id"]}
    assert all(r["status"] == "closed" and r["end_seq"] > r["start_seq"] for r in rels)
    assert rels[0]["start_command_id"] == first["command_id"]
    assert rels[1]["command_ids"] == [second["command_id"]]
    assert rels[1]["follow_up_of_execution_id"] == a["task_id"]
    execution = obs.relations("execution", a["task_id"])["relations"]
    assert {r["role"] for r in execution} == {"lead", "reviewer"}
    assert len({r["relation_id"] for r in execution}) == 2
    j.close()


def test_b01_pending_bind_and_snapshot_relations(tmp_path):
    j = Journal(tmp_path / "j.db")
    t = task(j, "a")
    c, _ = j.command(t["task_id"], "start_lead", None, {}, "start")
    j.command_bind_session(c["command_id"], "sid")
    pending = Observation(j).relations("session", "h1/sid")["relations"][0]
    assert pending["status"] == "pending"
    j.command_status(c["command_id"], "settled")
    bound = Observation(j).relations("session", "h1/sid")["relations"][0]
    assert bound["status"] == "bound" and bound["relation_id"] == pending["relation_id"]
    j.add_branch(t["task_id"], session_id="review", provider="codex", role="reviewer", reason="start")
    page = Observation(j).relations("execution", t["task_id"], limit=1)
    j.change(t["task_id"], "failed")
    tail = Observation(j).relations("execution", t["task_id"], limit=1, cursor=page["next_cursor"])
    assert tail["as_of"] == page["as_of"] and tail["relations"][0]["status"] == "bound"
    j.close()


def replay_version_one(j, path):
    for table in ("api_event_context", "api_event_resources", "command_relations", "relation_revisions", "observation_relations"):
        j.db.execute(f"DELETE FROM {table}")
    j.db.execute("PRAGMA user_version=1")
    j.close()
    return Journal(path)


def observation_operation(j, key):
    from bat_agent_connector.operations import ActionDef, OperationService

    async def observe(ctx):
        return {}

    ops = OperationService(j, actions=[ActionDef("fixture.observe", "observe", "Fixture only", observe)])
    op, _ = ops.create(api_auth.Principal("fixture", frozenset({"observe"})), action="fixture.observe",
                       target={"host": "h1"}, idempotency_key=key)
    return ops, op["operation_id"]


@pytest.mark.parametrize("late_kind", ["event", "resource.bound"])
@pytest.mark.parametrize("fact_kind,reference_kind", [
    ("operation_steps", "operation"), ("integration_receipts", "operation"),
    ("integration_receipts", "checkpoint_run"), ("work_item_links", "operation"),
    ("work_item_links", "checkpoint_run"), ("operation_target", "operation"),
    ("operation_source", "operation"), ("operation_source", "checkpoint_run"),
])
def test_b01_b03_saved_operation_refs_exclude_later_events_and_links(tmp_path, late_kind, fact_kind, reference_kind):
    """B01/B03, §08/§10/§11: saved facts see only operation resources proven before their position."""
    from bat_agent_connector import observation
    path = tmp_path / "j.db"
    j = Journal(path)
    ops, oid = observation_operation(j, "historical-operation")
    with j.tx():
        if late_kind == "event":
            # A continuation step records its creation intent and path in the saved response.
            j.db.execute("UPDATE operations SET action='checkpoint.continue' WHERE operation_id=?", (oid,))
            wid = worktree(j, "h1", "checkpoint.continue", oid, "worktree")
        else:
            wid = worktree(j, "h1", "registry", "later@1", "worktree")
    ops._merge_refs(oid, {"host": "h1", "session_id": "early"})
    with j.tx():
        early = j.api_event("operation", oid, "operation.running", {})
    if late_kind == "resource.bound":
        ops._merge_refs(oid, {"host": "h1", "session_id": "later", "worktree_id": wid})
        later = j.api_head()
        assert j.db.execute("SELECT linked_at_seq FROM api_event_resources WHERE seq=? AND resource_id=?", (early, wid)).fetchone()[0] == later
    else:
        with j.tx():
            later = j.api_event("operation", oid, "operation.step.succeeded",
                                {"response": {"session_id": "later", "worktree_path": "/srv/later"}})
    with j.tx():
        after = j.api_event("operation", oid, "operation.running", {})
    original = {e["seq"]: e["context"] for e in j.api_events(limit=200)["events"]}
    assert original[early]["session_resource_ids"] == ["h1/early"]
    assert original[after]["session_resource_ids"] == ["h1/early", "h1/later"]
    assert original[after]["worktree_ids"] == [wid]
    # Deterministic fact placement, exactly at the early event (first strictly later event is P).
    j.db.execute("UPDATE api_events SET created_at=seq*10")
    at = early * 10
    if fact_kind == "operation_steps":
        j.db.execute("""INSERT INTO operation_steps(operation_id,seq,name,status,request,started_at)
            VALUES(?,1,'saved','succeeded','{}',?)""", (oid, at))
    elif fact_kind == "integration_receipts":
        j.db.execute("""INSERT INTO integration_receipts(operation_id,seq,preview_id,repository,
            pull_number,head_ref,source_kind,source_id,source_host,location_class,pinned_sha,mode,source_key,
            status,actor,created_at,updated_at) VALUES(?,1,'preview','o/r',1,'feature',?,?,'h1',
            'connector',?,'merge','source','delivered','fixture',?,?)""", (oid, reference_kind, oid, "a" * 40, at, at))
    elif fact_kind == "work_item_links":
        j.db.execute("""INSERT INTO work_item_links(work_item_id,kind,ref,linked_by,linked_at,link_operation)
            VALUES('wi_fixture',?,?,'fixture',?,'op_link')""", (reference_kind, oid, at))
    else:
        target = {"host": "h1", "operation_id": oid} if fact_kind == "operation_target" else {"host": "h1"}
        params = {"sources": [{"kind": reference_kind, "id": oid}]} if fact_kind == "operation_source" else {}
        j.db.execute("""INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,target,
            params,preconditions,status,created_at,updated_at) VALUES('op_saved','fixture','cli','saved','hash',
            'fixture.observe',?,?,'{}','succeeded',?,?)""", (dump(target), dump(params), at, at))
    expected = {("session", "h1/early")}
    assert set(observation.snapshot_refs(j.db, reference_kind, oid, later)) == expected
    assert set(observation._refs(j.db, "operation", oid, seq=later - 1)) == expected
    assert set(observation._refs(j.db, "operation", oid, seq=later)) == expected | {("session", "h1/later"), ("worktree", wid)}
    assert set(observation._refs(j.db, "operation", oid)) == expected | {("session", "h1/later"), ("worktree", wid)}
    assert observation.snapshot_refs(j.db, reference_kind, oid, None) == []
    j = replay_version_one(j, path)
    table = "operations" if fact_kind.startswith("operation_") and fact_kind != "operation_steps" else fact_kind
    facts = [e for e in j.api_events(kind="history.backfilled", limit=500)["events"] if e["body"]["source_table"] == table]
    assert len(facts) == 1
    fact = facts[0]
    assert fact["context"]["fact_at_seq"] == later
    assert fact["context"]["session_resource_ids"] == ["h1/early"] and fact["context"]["worktree_ids"] == []
    assert "projection_error" not in fact["context"]
    obs = Observation(j)
    assert fact["seq"] in {e["seq"] for e in obs.history("session", "h1/early", limit=200)["events"]}
    for kind, rid in (("session", "h1/later"), ("worktree", wid)):
        events = {e["seq"] for e in obs.history(kind, rid, limit=200)["events"]}
        assert fact["seq"] not in events and {later, after} <= events
    replayed = {e["seq"]: e["context"] for e in j.api_events(limit=200)["events"]}
    for seq in (early, later, after):
        for field in ("session_resource_ids", "worktree_ids"):
            assert replayed[seq][field] == original[seq][field]
    head, changes = j.api_head(), j.db.total_changes
    observation.backfill(j)
    assert j.api_head() == head and j.db.total_changes == changes
    j.close()
    j = Journal(path)
    assert j.api_head() == head and j.db.total_changes == 0
    j.close()


@pytest.mark.parametrize("run_evidence", ["event", "row_time", "unknown_time"])
def test_b01_b03_checkpoint_refs_exclude_later_runs(tmp_path, run_evidence):
    """B01/B03, §08/§10/§11: fixed capture sources stay available; future runs never enter old facts."""
    from bat_agent_connector import observation
    path = tmp_path / "j.db"
    j = Journal(path)
    _, oid = observation_operation(j, "run-operation")
    j.db.execute("""INSERT INTO checkpoints(checkpoint_id,host,source_session_id,source_provenance,cwd,
        repo_root,commit_sha,head_sha,excerpt,excerpt_sha256,actor,operation_id,captured_at)
        VALUES('cp_fixture','h1','captured','connector','/srv/repo','/srv/repo',?,?,'','hash','fixture',?,1)""", ("a" * 40, "a" * 40, oid))
    with j.tx():
        first = j.api_event("work_item", "wi_fixture", "work_item.linked", {"kind": "checkpoint", "ref": "cp_fixture"})
    j.db.execute("""INSERT INTO checkpoint_runs(checkpoint_id,operation_id,host,session_id,clone_path,
        worktree_path,branch,agent,actor,created_at) VALUES('cp_fixture',?,'h1','run','/srv/clone',
        '/srv/worktree','feature','codex','fixture',?)""", (oid, "" if run_evidence == "unknown_time" else (first + 1) * 10 - 1))
    with j.tx():
        if run_evidence == "event":
            created = j.api_event("checkpoint", "cp_fixture", "checkpoint.continued", {"operation_id": oid, "host": "h1", "session_id": "run"})
        else:
            created = j.api_event("operation", oid, "operation.running", {})
        after = j.api_event("work_item", "wi_fixture", "work_item.unlinked", {"kind": "checkpoint", "ref": "cp_fixture"})
    j.db.execute("UPDATE api_events SET created_at=seq*10")
    expected = [("session", "h1/captured")]
    assert observation._refs(j.db, "checkpoint", "cp_fixture", seq=first, include_runs=True) == expected
    assert observation._refs(j.db, "checkpoint", "cp_fixture", include_runs=True) == expected + [("session", "h1/run")]
    assert observation.snapshot_refs(j.db, "checkpoint_run", oid, created) == []
    run_refs = [("session", "h1/run")] if run_evidence != "unknown_time" else []
    assert observation._refs(j.db, "checkpoint_run", oid, seq=created) == run_refs
    assert observation.snapshot_refs(j.db, "checkpoint_run", oid, after) == run_refs
    original = {e["seq"]: e["context"] for e in j.api_events(limit=200)["events"]}
    j = replay_version_one(j, path)
    obs = Observation(j)
    assert first in {e["seq"] for e in obs.history("session", "h1/captured", limit=200)["events"]}
    events = {e["seq"]: e for e in obs.history("session", "h1/run", limit=200)["events"]}
    assert first not in events
    assert (after in events) == (run_evidence != "unknown_time")
    contexts = {e["seq"]: e["context"] for e in j.api_events(limit=200)["events"]}
    assert contexts[first]["session_resource_ids"] == ["h1/captured"]
    assert contexts[after]["session_resource_ids"] == ["h1/captured", *(["h1/run"] if run_refs else [])]
    for seq in (first, after):
        assert contexts[seq]["session_resource_ids"] == original[seq]["session_resource_ids"]
    assert observation._refs(j.db, "checkpoint_run", oid, seq=j.api_head()) == [("session", "h1/run")]
    j.close()


def test_b02_related_event_feed_bounds_links_by_its_captured_head(tmp_path, monkeypatch):
    """B02, §10/§11: a link written after a captured feed head is not visible inside that read."""
    j = Journal(tmp_path / "j.db")
    with j.tx():
        first = j.api_event("operation", "op_fixture", "operation.accepted", {})
        later = j.api_event("session", "h1/later", "resource.bound", {})
        index(j.db, first, "session", "h1/later", later)
    assert {e["seq"] for e in j.api_events(related_resource_type="session", related_resource_id="h1/later")["events"]} == {first, later}
    monkeypatch.setattr(j, "api_head", lambda: first)
    assert j.api_events(related_resource_type="session", related_resource_id="h1/later")["events"] == []
    j.close()


@pytest.mark.parametrize("backfilled", [False, True])
@pytest.mark.parametrize("command_count", [0, 2])
def test_b01_b03_relation_closed_body_keeps_the_final_command(tmp_path, monkeypatch, backfilled, command_count):
    """B01/B03, §08/§10/§11: closure history and relation reads share the same command boundary."""
    from bat_agent_connector import observation
    path = tmp_path / "j.db"
    j = Journal(path)
    t = task(j, "closed-body")
    commands = []
    if command_count:
        commands.append(bind(j, t, "lead"))
        j.change(t["task_id"], "dispatching")
        c, _ = j.command(t["task_id"], "send", "lead", {}, "second")
        j.command_status(c["command_id"], "settled")
        commands.append(c)
        for n, c in enumerate(commands):
            j.db.execute("UPDATE commands SET created_at=? WHERE command_id=?", (100 + n, c["command_id"]))
    else:
        j.add_branch(t["task_id"], session_id="lead", provider="codex", role="lead", reason="start")
    original_iso = observation.iso
    ticks = iter(range(100))

    def advancing_iso(value):
        return original_iso(value + next(ticks)) if value is not None else None

    # Make separate timestamp evaluations observably different without patching a stdlib class.
    with monkeypatch.context() as closure:
        closure.setattr(observation, "iso", advancing_iso)
        j.change(t["task_id"], "failed")
    if backfilled:
        j = replay_version_one(j, path)
    obs = Observation(j)
    event = next(e for e in obs.history("session", "h1/lead", limit=200)["events"] if e["kind"] == "relation.closed")
    rid = event["body"]["relation_id"]
    current = json.loads(j.db.execute("SELECT body FROM observation_relations WHERE relation_id=?", (rid,)).fetchone()[0])
    revision = json.loads(j.db.execute("SELECT body FROM relation_revisions WHERE relation_id=? AND seq=?", (rid, event["seq"])).fetchone()[0])
    endpoint = obs.relations("session", "h1/lead")["relations"][0]
    assert endpoint.pop("command_ids") == [c["command_id"] for c in commands]
    assert current == revision == endpoint
    expected = commands[-1]["command_id"] if commands else None
    assert all(data["end_command_id"] == expected for data in (event["body"], current, revision, endpoint))
    assert event["body"]["end_seq"] == revision["end_seq"]
    if backfilled:
        # Original facts stay immutable; reconstructed legacy time boundaries remain unknown.
        assert current["start_seq"] is current["started_at"] is current["ended_at"] is None
    else:
        assert event["body"] == current and current["ended_at"] is not None
    j.close()


@pytest.mark.parametrize("backfilled", [False, True])
def test_b01_b03_relation_events_equal_their_lifecycle_revisions(tmp_path, backfilled):
    """B01/B03, §08/§10/§11: open, bind, replace and close finish their body before emitting it."""
    path = tmp_path / "j.db"
    j = Journal(path)
    t = task(j, "relation-lifecycle")
    c, _ = j.command(t["task_id"], "start_lead", None, {}, "start")
    j.command_bind_session(c["command_id"], "old")
    j.command_status(c["command_id"], "settled")
    old = j.add_branch(t["task_id"], session_id="old", provider="codex", role="lead", reason="start")
    bind(j, t, "reviewer", "reviewer")
    c, _ = j.command(t["task_id"], "start_lead", None, {}, "replacement")
    j.command_bind_session(c["command_id"], "new")
    j.command_status(c["command_id"], "settled")
    j.add_branch(t["task_id"], session_id="new", provider="codex", role="lead", reason="replacement", parent_branch_id=old["branch_id"])
    j.change(t["task_id"], "failed")
    if backfilled:
        j = replay_version_one(j, path)
    events = j.db.execute("SELECT seq,kind,body FROM api_events WHERE kind LIKE 'relation.%' ORDER BY seq").fetchall()
    assert {e["kind"] for e in events} == {"relation.opened", "relation.bound", "relation.closed"}
    assert {json.loads(e["body"])["session_resource_id"] for e in events} == {"h1/old", "h1/new", "h1/reviewer"}
    for event in events:
        expected = json.loads(event["body"])
        revision = json.loads(j.db.execute("SELECT body FROM relation_revisions WHERE relation_id=? AND seq=?", (expected["relation_id"], event["seq"])).fetchone()[0])
        if backfilled:
            expected.update(start_seq=None, started_at=None)
            if event["kind"] == "relation.closed":
                expected["ended_at"] = None
        assert revision == expected
    j.close()


def test_b03_version_one_closure_reconstructs_the_final_command(tmp_path, monkeypatch):
    """B03, §08/§11: pre-observation task events reconstruct a closure without guessing its time."""
    from bat_agent_connector import observation
    path = tmp_path / "j.db"
    with monkeypatch.context() as legacy:
        legacy.setattr(observation, "install", lambda journal: None)
        j = Journal(path)
    t = task(j, "legacy-closed-body")
    first = bind(j, t, "lead")
    j.change(t["task_id"], "dispatching")
    last, _ = j.command(t["task_id"], "send", "lead", {}, "second")
    j.command_status(last["command_id"], "settled")
    j.change(t["task_id"], "failed")
    closed_at = j.api_head()
    assert j.db.execute("SELECT COUNT(*) FROM api_events WHERE kind LIKE 'relation.%'").fetchone()[0] == 0
    j.close()
    j = Journal(path)
    r = Observation(j).relations("session", "h1/lead")["relations"][0]
    revision = json.loads(j.db.execute("SELECT body FROM relation_revisions WHERE relation_id=? AND seq=?", (r["relation_id"], closed_at)).fetchone()[0])
    assert r["command_ids"] == [first["command_id"], last["command_id"]]
    assert r["end_command_id"] == revision["end_command_id"] == last["command_id"]
    assert r["status"] == revision["status"] == "closed"
    assert r["ended_at"] is revision["ended_at"] is None
    j.close()


def test_b03_legacy_closure_event_cannot_erase_its_reconstructed_command(tmp_path):
    """B03, §08/§11: an older relation.closed body may lack the final linked command."""
    path = tmp_path / "j.db"
    j = Journal(path)
    t = task(j, "legacy-null-command")
    command = bind(j, t, "lead")
    j.change(t["task_id"], "failed")
    row = j.db.execute("SELECT seq,body FROM api_events WHERE kind='relation.closed'").fetchone()
    original = {**json.loads(row["body"]), "end_command_id": None}
    j.db.execute("UPDATE api_events SET body=? WHERE seq=?", (dump(original), row["seq"]))
    j = replay_version_one(j, path)
    r = Observation(j).relations("session", "h1/lead")["relations"][0]
    revision = json.loads(j.db.execute("SELECT body FROM relation_revisions WHERE relation_id=? AND seq=?", (r["relation_id"], row["seq"])).fetchone()[0])
    assert r["end_command_id"] == revision["end_command_id"] == command["command_id"]
    assert r["ended_at"] is revision["ended_at"] is None
    assert json.loads(j.db.execute("SELECT body FROM api_events WHERE seq=?", (row["seq"],)).fetchone()[0]) == original
    j.close()


@pytest.mark.parametrize("table", ["work_item_links", "integration_receipts", "operations", "operation_steps", "operation_task_link"])
@pytest.mark.parametrize("when", ["during_a", "gap", "missing", "after_last"])
def test_b01_b03_saved_task_facts_use_their_own_time_and_keep_execution(tmp_path, table, when):
    """B01/B03, §08/§10/§11: eventless facts use historic participation, never backfill-time ownership."""
    from bat_agent_connector import observation
    path = tmp_path / "j.db"
    j = Journal(path)
    t = task(j, "saved-task-facts")
    with j.tx():
        old_wid = worktree(j, "h1", "registry", "a@1", "worktree", session_id="a")
    bind(j, t, "a")
    last_a = j.api_head()
    with j.tx():
        closed_a = j.api_event("task", t["task_id"], "task.paused", {})
        close_relations(j, t["task_id"], closed_a)
        new_wid = worktree(j, "h1", "registry", "a@2", "worktree", session_id="a")
    gap = j.api_head()
    bind(j, t, "b")
    if when != "missing":
        j.change(t["task_id"], "failed")
    boundary = j.api_head() + 1
    # Fixed journal timestamps, including a fact equal to the final A event (the map must use >).
    j.db.execute("UPDATE api_events SET created_at=seq*10")
    at = {"during_a": last_a * 10, "gap": gap * 10 + 1,
          "missing": "", "after_last": boundary * 10}[when]
    if table == "work_item_links":
        j.db.execute("""INSERT INTO work_item_links(work_item_id,kind,ref,linked_by,linked_at,link_operation)
            VALUES('wi_fixture','task',?,'fixture',?,'op_link')""", (t["task_id"], at))
    elif table == "integration_receipts":
        j.db.execute("""INSERT INTO integration_receipts(operation_id,seq,preview_id,repository,
            pull_number,head_ref,source_kind,source_id,source_host,location_class,pinned_sha,mode,source_key,
            status,actor,created_at,updated_at) VALUES('op_receipt',1,'preview','o/r',1,'feature','task',?,
            'h1','connector',?,'merge','task-source','delivered','fixture',?,999999)""", (t["task_id"], "a" * 40, at))
    else:
        params = {"kind": "task", "ref": t["task_id"]} if table == "operation_task_link" else {
            "sources": [{"kind": "task", "id": t["task_id"]}]}
        j.db.execute("""INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,target,
            params,preconditions,status,created_at,updated_at) VALUES('op_saved','fixture','cli','saved','hash',
            'fixture.observe','{}',?,'{}','succeeded',?,999999)""",
            (dump(params), at if table != "operation_steps" else gap * 10 + 1))
        if when == "missing":
            j.db.execute("UPDATE operations SET target=? WHERE operation_id='op_saved'",
                         (dump({"host": "h1", "session_id": "b"}),))
        if table == "operation_steps":
            j.db.execute("""INSERT INTO operation_steps(operation_id,seq,name,status,request,started_at,finished_at)
                VALUES('op_saved',1,'read','succeeded','{}',?,999999)""", (at,))
    j = replay_version_one(j, path)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP
    facts = [e for e in Observation(j).history("execution", t["task_id"], limit=200)["events"]
             if e["kind"] == "history.backfilled" and e["body"]["source_table"] == ("operations" if table == "operation_task_link" else table)]
    assert len(facts) == 1
    fact = facts[0]
    assert fact["context"]["execution_id"] == t["task_id"]
    assert fact["context"]["session_resource_ids"] == (["h1/a"] if when == "during_a" else [])
    assert fact["context"]["worktree_ids"] == ([old_wid] if when == "during_a" else [])
    assert fact["context"]["occurred_at_epoch"] == (None if when == "missing" else at)
    assert fact["context"]["fact_at_seq"] == {"during_a": closed_a, "gap": gap + 1,
                                              "missing": None, "after_last": boundary}[when]
    for sid in ("a", "b"):
        ids = {e["seq"] for e in Observation(j).history("session", f"h1/{sid}", limit=200)["events"]}
        assert (fact["seq"] in ids) == (sid == "a" and when == "during_a")
    assert fact["seq"] not in {e["seq"] for e in Observation(j).history("worktree", new_wid, limit=200)["events"]}
    assert "projection_error" not in dump(j.api_events(kind="history.backfilled"))
    head, changes = j.api_head(), j.db.total_changes
    observation.backfill(j)
    assert j.api_head() == head and j.db.total_changes == changes
    j.close()
    j = Journal(path)
    assert j.api_head() == head and j.db.total_changes == 0
    assert Observation(j).history("execution", t["task_id"], limit=200)["events"][0]["seq"] >= fact["seq"]
    j.close()


@pytest.mark.parametrize("value", [None, "", "unknown", True, float("inf"), float("nan"), 1e300])
def test_b03_saved_fact_missing_or_unusable_timestamp_never_fails_backfill(tmp_path, value):
    """B03, §08/§11: absent, invalid and out-of-range dates leave task sessions unknown."""
    from bat_agent_connector.observation import fact_time
    j = Journal(tmp_path / "j.db")
    t = task(j, "unknown-time")
    assert fact_time("work_item_links", {}) is None
    assert fact_time("work_item_links", {"linked_at": value, "updated_at": 123}) is None
    assert fact_time("work_item_links", {"linked_at": 0}) == 0
    with j.tx():
        seq = saved_fact(j, "work_item_links", "missing", {"linked_at": value}, [("execution", t["task_id"])])
    fact = Observation(j).history("execution", t["task_id"])["events"][0]
    assert fact["seq"] == seq and fact["context"]["occurred_at_epoch"] is None
    assert fact["context"]["fact_at_seq"] is None and fact["context"]["session_resource_ids"] == []
    assert fact["body"]["saved_snapshot"]["linked_at"] is None
    assert "projection_error" not in fact["context"]
    j.close()


@pytest.mark.parametrize("backfilled", [False, True])
def test_b01_b03_pending_replacement_closure_links_only_its_own_session(tmp_path, backfilled):
    """B01/B03, §08/§10/§11: a branchless replacement never lends its closure to an old session."""
    path = tmp_path / "j.db"
    j = Journal(path)
    t = task(j, "replacement-closure")
    bind(j, t, "old")
    with j.tx():
        prior = j.api_event("task", t["task_id"], "task.paused", {})
        close_relations(j, t["task_id"], prior)
    c, _ = j.command(t["task_id"], "start_lead", None, {}, "replacement")
    j.command_bind_session(c["command_id"], "new")
    pending = Observation(j).relations("session", "h1/new")["relations"][0]
    assert pending["status"] == "pending" and pending["branch_id"] is None
    with j.tx():
        linked = j.api_event("work_item", "wi_fixture", "work_item.linked", {"kind": "task", "ref": t["task_id"]})
    j.change(t["task_id"], "failed")
    if backfilled:
        j = replay_version_one(j, path)
    obs = Observation(j)
    old = obs.history("session", "h1/old", limit=200)["events"]
    new = obs.history("session", "h1/new", limit=200)["events"]
    assert all(e["body"].get("session_resource_id") != "h1/new" for e in old)
    assert all("h1/new" not in e["context"]["session_resource_ids"] for e in old)
    assert linked not in {e["seq"] for e in old} and linked in {e["seq"] for e in new}
    assert any(e["kind"] == "relation.closed" and e["body"]["session_resource_id"] == "h1/new" for e in new)
    assert not any(e["kind"] == "task.state" and e["body"].get("to") == "failed" for e in old)
    execution, page = [], obs.history("execution", t["task_id"], limit=2)
    while True:
        execution.extend(page["events"])
        if not page["next_cursor"]:
            break
        page = obs.history("execution", t["task_id"], limit=2, cursor=page["next_cursor"])
    expected = {r[0] for r in j.db.execute("SELECT seq FROM api_events WHERE resource_id=? OR seq=?", (t["task_id"], linked))}
    assert len(execution) == len({e["seq"] for e in execution}) == len(expected)
    assert {e["seq"] for e in execution} == expected
    assert all(e["body"].get("relation_id") and e["body"].get("session_resource_id")
               for e in execution if e["kind"].startswith("relation."))
    # Reproject the earlier milestone with today's closed relations: its original members still apply.
    with j.tx():
        j.db.execute("DELETE FROM api_event_context WHERE seq=?", (prior,))
        j.db.execute("DELETE FROM api_event_resources WHERE seq=?", (prior,))
        j._project_event(prior, None, legacy=backfilled)
    assert [r[0] for r in j.db.execute("SELECT resource_id FROM api_event_resources WHERE seq=? AND resource_type='session'", (prior,))] == ["h1/old"]
    j.close()


@pytest.mark.parametrize("backfilled", [False, True])
def test_b01_b03_parallel_lead_reviewer_relation_events_have_exact_links(tmp_path, backfilled):
    """B01/B03, §08/§10/§11: shared task milestones fan out, named relation facts do not."""
    path = tmp_path / "j.db"
    j = Journal(path)
    t = task(j, "parallel-roles")
    bind(j, t, "lead")
    bind(j, t, "reviewer", "reviewer")
    with j.tx():
        milestone = j.api_event("task", t["task_id"], "task.paused", {})
    j.change(t["task_id"], "failed")
    if backfilled:
        j = replay_version_one(j, path)
    obs = Observation(j)
    for sid, role in (("lead", "lead"), ("reviewer", "reviewer")):
        events = obs.history("session", f"h1/{sid}", limit=200)["events"]
        assert milestone in {e["seq"] for e in events}
        relation_events = [e for e in events if e["kind"].startswith("relation.")]
        assert {e["kind"] for e in relation_events} == {"relation.opened", "relation.bound", "relation.closed"}
        for e in relation_events:
            assert e["body"]["role"] == role and e["body"]["session_resource_id"] == f"h1/{sid}"
            assert e["context"]["session_resource_ids"] == [f"h1/{sid}"]
            assert e["context"]["relation_ids"] == [e["body"]["relation_id"]]
            links = {r[0] for r in j.db.execute("SELECT resource_id FROM api_event_resources WHERE seq=? AND resource_type='session'", (e["seq"],))}
            assert links == {f"h1/{sid}"}
    j.close()


@pytest.mark.parametrize("kind", ["relation.opened", "relation.bound", "relation.closed"])
@pytest.mark.parametrize("missing", ["relation_id", "session_resource_id"])
@pytest.mark.parametrize("backfilled", [False, True])
def test_b03_malformed_relation_events_log_evidence_without_links(tmp_path, caplog, kind, missing, backfilled):
    """B03, §08/§11: missing explicit identities cannot be guessed from the task or branch."""
    path = tmp_path / "j.db"
    j = Journal(path)
    t = task(j, "malformed")
    bind(j, t, "lead")
    rel = Observation(j).relations("execution", t["task_id"])["relations"][0]
    data = {**rel, "note": "private malformed prose"}
    data.pop(missing)
    with j.tx():
        seq = j.api_event("execution", t["task_id"], kind, data,
            context={"resources": [("session", "h1/lead")], "relation_ids": [rel["relation_id"]]})
    if backfilled:
        j = replay_version_one(j, path)
    event = next(e for e in j.api_events()["events"] if e["seq"] == seq)
    assert event["context"]["evidence"] == [{"table": "api_events", "id": seq, "code": "MALFORMED_RELATION_EVENT"}]
    assert event["context"]["relation_ids"] == event["context"]["session_resource_ids"] == []
    assert j.db.execute("SELECT COUNT(*) FROM api_event_resources WHERE seq=?", (seq,)).fetchone()[0] == 0
    assert not any(e["seq"] == seq for e in Observation(j).history("session", "h1/lead")["events"])
    assert "malformed relation event" in caplog.text and "private" not in caplog.text
    j.close()


@pytest.mark.parametrize("backfilled", [False, True])
def test_b01_b03_relation_history_keeps_roles_and_strips_free_text(tmp_path, backfilled):
    """B01/B03, §08/§10/§11: roles survive live reads and replay of saved version-1 relation facts."""
    path = tmp_path / "j.db"
    j = Journal(path)
    t = task(j, "roles")
    for role in ("lead", "reviewer"):
        bind(j, t, "shared", role)
    j.change(t["task_id"], "failed")
    kinds = {"relation.opened", "relation.bound", "relation.closed"}
    # These are persisted original facts, including text from an older writer we must not expose.
    for row in j.db.execute("SELECT seq,body FROM api_events WHERE kind LIKE 'relation.%'").fetchall():
        data = json.loads(row["body"])
        data.update(note="private relation prose", instructions="private prompt",
            source_versions=[{"kind": "git", "sha": "a" * 40, "role": "captured_head", "message": "private commit message"}],
            result_versions=[{"kind": "git", "sha": "b" * 40, "role": "integrated_sha", "text": "private text"}])
        j.db.execute("UPDATE api_events SET body=? WHERE seq=?", (dump(data), row["seq"]))
    if backfilled:
        for table in ("api_event_context", "api_event_resources", "command_relations", "relation_revisions", "observation_relations"):
            j.db.execute(f"DELETE FROM {table}")
        j.db.execute("PRAGMA user_version=1")
        j.close()
        j = Journal(path)
        assert j.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP
    obs = Observation(j)
    expected = {(kind, role) for kind in kinds for role in ("lead", "reviewer")}
    for resource_type, resource_id in (("execution", t["task_id"]), ("session", "h1/shared")):
        events = obs.history(resource_type, resource_id, kind=sorted(kinds), limit=200)["events"]
        assert {(e["kind"], e["body"]["role"]) for e in events} == expected
        assert "private" not in dump(events)
        for e in events:
            assert e["body"]["source_versions"] == [{"kind": "git", "sha": "a" * 40, "role": "captured_head"}]
            assert e["body"]["result_versions"] == [{"kind": "git", "sha": "b" * 40, "role": "integrated_sha"}]
    if backfilled:
        head = j.api_head()
        j.close()
        j = Journal(path)
        assert j.api_head() == head and j.db.total_changes == 0
    j.close()


def test_b03_backfilled_summary_retains_bounded_metadata_and_nested_roles(tmp_path):
    """B03, §08/§11: recursive snapshot filtering retains identities/flags without prose containers."""
    j = Journal(tmp_path / "j.db")
    snapshot = {
        "role": "repair", "agent": "claude", "source_provenance": "connector_managed",
        "resolver_operation_id": "op_repair", "resolver_session_id": "repair",
        "apply_operation_id": "op_apply", "integration_operation_id": "op_apply", "integration_seq": 1,
        "parent_id": "wi_parent", "verification_id": 1, "route_id": 2, "old_session_id": "old",
        "handoff_command_id": "cmd_handoff", "operator_followup": "cmd_followup", "source_message_id": "message_source",
        "linked_at_seq": 3, "evidence_ref": "api_events:3", "cancel_requested": 0,
        "candidate_commit": "a" * 40, "start_commit": "a" * 40, "old_head": "a" * 40,
        "new_head": "b" * 40, "pushed_sha": "b" * 40, "push_old_sha": "a" * 40,
        "composed_tree": "c" * 40, "predicted_tree": "c" * 40,
        "request_hash": "d" * 64, "excerpt_sha256": "e" * 64, "diff_sha256": "f" * 64,
        "approved_fingerprint": "d" * 64, "outcome": "pushed", "verdict": "pass", "write_scope": "confined",
        "advisory_only": True, "abort_current": False, "ready": True, "stale": False, "attention": 0,
        "pushed": True, "local_checkouts_changed": False,
        "request": {"expected_head_sha": "a" * 40, "expected_base_sha": "b" * 40, "preview_digest": "c" * 64},
        "response": {"target": {"head_repo_id": 2}, "affected_prs": [{"number": 8, "would_merge": False, "effect": "dependency"}],
            "files_may_be_truncated": False, "write_acknowledged": True, "observed_intent": False, "read_refused": False,
            "conflict_before_write": False, "conflict_after_write": True, "before_digest": "d" * 64, "after_digest": "e" * 64},
        "source_versions": [{"kind": "git", "sha": "a" * 40, "role": "pinned", "evidence_ref": "checkpoints:cp_fixture"}],
        "result_versions": [{"kind": "git", "sha": "b" * 40, "role": "delivered"}],
    }
    with j.tx():
        seq = saved_fact(j, "fixture", "metadata", {**snapshot, "prompt": "private prompt",
            "note": "private note", "message": "private commit message", "description": "private prose",
            "provider": "https://provider.example/private", "params": {"goal": "private requirements"},
            "dirty": 12, "confidence": 0.9, "old": "private old text", "new": "private new text"}, [("session", "h1/fixture")])
    event = Observation(j).history("session", "h1/fixture")["events"][0]
    assert event["seq"] == seq and event["body"]["saved_snapshot"] == snapshot
    assert "private" not in dump(event)
    j.close()


def relation_pages(obs, wid, first=None):
    page = first or obs.relations("worktree", wid, limit=1)
    rows, as_of = list(page["relations"]), page["as_of"]
    while page["next_cursor"]:
        page = obs.relations("worktree", wid, limit=1, cursor=page["next_cursor"])
        assert page["as_of"] == as_of
        rows.extend(page["relations"])
    assert len(rows) == len({r["relation_id"] for r in rows})
    return rows


def test_b01_worktree_relations_exclude_late_bindings_from_existing_cursor(tmp_path):
    """B01, §08/§10: a binding added after page one cannot change its remaining pages."""
    j = Journal(tmp_path / "j.db")
    with j.tx():
        wid = worktree(j, "h1", "registry", "root@123", "worktree", session_id="early-a")
        bind_worktree(j, "h1/early-b", wid)
    for sid in ("early-a", "early-b", "late"):
        bind(j, task(j, sid), sid)
    obs = Observation(j)
    first = obs.relations("worktree", wid, limit=1)
    assert first["has_more"]
    history = obs.history("worktree", wid, limit=1)
    with j.tx():
        bind_worktree(j, "h1/late", wid)
    assert j.api_head() > first["as_of"]
    old = relation_pages(obs, wid, first)
    assert {r["session_resource_id"] for r in old} == {"h1/early-a", "h1/early-b"}
    assert {r["session_resource_id"] for r in relation_pages(obs, wid)} == {"h1/early-a", "h1/early-b", "h1/late"}
    # Current identity metadata is allowed to change; history membership stays bounded by link seq.
    while history["next_cursor"]:
        history = obs.history("worktree", wid, limit=1, cursor=history["next_cursor"])
        assert all("h1/late" not in e["context"].get("session_resource_ids", []) for e in history["events"])
    j.close()


def test_b01_worktree_moves_preserve_relation_ranges_across_pages(tmp_path):
    """B01/B03, §08/§10: a move clips participation, retaining the old worktree's ranges."""
    j = Journal(tmp_path / "j.db")
    obs = Observation(j)
    with j.tx():
        a = worktree(j, "h1", "registry", "root@123", "worktree", session_id="shared")
        b = worktree(j, "h1", "checkpoint.continue", "new", "worktree")
    previous = []
    for key in ("past-a", "past-b"):
        t = task(j, key)
        c = bind(j, t, "shared")
        previous.append((t, c))
        j.change(t["task_id"], "failed")
    ongoing = task(j, "ongoing")
    before = bind(j, ongoing, "shared")
    j.change(ongoing["task_id"], "dispatching")
    first = obs.relations("worktree", a, limit=1)
    with j.tx():
        bind_worktree(j, "h1/shared", b)
    move_seq = j.api_head()
    after, _ = j.command(ongoing["task_id"], "send", "shared", {}, "after-move")
    j.command_status(after["command_id"], "settled")
    new = task(j, "only-b")
    newest = bind(j, new, "shared")
    old_snapshot = relation_pages(obs, a, first)
    assert old_snapshot[-1]["worktree_ranges"][0]["end_seq"] is None
    assert old_snapshot[-1]["command_ids"] == [before["command_id"]]
    on_a = {r["execution_id"]: r for r in relation_pages(obs, a)}
    on_b = {r["execution_id"]: r for r in relation_pages(obs, b)}
    assert set(on_a) == {t["task_id"] for t, _ in previous} | {ongoing["task_id"]}
    assert set(on_b) == {ongoing["task_id"], new["task_id"]}
    for t, c in previous:
        assert on_a[t["task_id"]]["command_ids"] == [c["command_id"]]
    assert on_a[ongoing["task_id"]]["command_ids"] == [before["command_id"]]
    assert on_a[ongoing["task_id"]]["worktree_ranges"][0]["end_seq"] == move_seq
    assert on_b[ongoing["task_id"]]["command_ids"] == [after["command_id"]]
    assert on_b[ongoing["task_id"]]["worktree_ranges"][0]["start_seq"] == move_seq
    assert on_b[new["task_id"]]["command_ids"] == [newest["command_id"]]
    # A stale creation receipt must not move a session back to the old worktree.
    with j.tx():
        seq = j.api_event("session", "h1/shared", "fixture.receipt", {})
        worktree(j, "h1", "registry", "root@123", "worktree", session_id="shared", seq=seq)
    assert obs.resource("session", "h1/shared")["worktree_id"] == b
    assert all(e["kind"] != "fixture.receipt" for e in obs.history("worktree", a)["events"])
    # Re-entry adds another range to one relation, rather than another copy of that relation.
    with j.tx():
        bind_worktree(j, "h1/shared", a)
    return_seq = j.api_head()
    returned, _ = j.command(ongoing["task_id"], "send", "shared", {}, "return")
    j.command_status(returned["command_id"], "settled")
    returning = next(r for r in relation_pages(obs, a) if r["execution_id"] == ongoing["task_id"])
    assert [r["start_seq"] for r in returning["worktree_ranges"]] == [returning["start_seq"], return_seq]
    assert returning["command_ids"] == [before["command_id"], returned["command_id"]]
    assert Observation(j).relations("execution", ongoing["task_id"])["relations"][0]["command_ids"] == [before["command_id"], after["command_id"], returned["command_id"]]
    j.close()


def test_b03_worktree_binding_projection_failure_preserves_core_event(tmp_path, monkeypatch, caplog):
    """B03, §08/§11: a partially projected move rolls back without losing its core event."""
    from bat_agent_connector import observation
    j = Journal(tmp_path / "j.db")
    with j.tx():
        a = worktree(j, "h1", "registry", "root@123", "worktree", session_id="sid")
        b = worktree(j, "h1", "checkpoint.continue", "new", "worktree")
    before = [tuple(r) for r in j.db.execute("SELECT * FROM session_worktree_bindings")]
    original = observation.record_event

    def fail(journal, seq, **kwargs):
        original(journal, seq, **kwargs)
        raise KeyError("private projection text")

    monkeypatch.setattr(observation, "record_event", fail)
    with j.tx():
        bind_worktree(j, "h1/sid", b)
    seq = j.api_head()
    assert before == [tuple(r) for r in j.db.execute("SELECT * FROM session_worktree_bindings")]
    assert Observation(j).resource("session", "h1/sid")["worktree_id"] == a
    assert j.db.execute("SELECT COUNT(*) FROM api_event_resources WHERE seq=?", (seq,)).fetchone()[0] == 0
    assert j.db.execute("SELECT COUNT(*) FROM relation_revisions").fetchone()[0] == 0
    e = Observation(j).history("session", "h1/sid")["events"][0]
    assert e["seq"] == seq and e["context"] == {"projection_error": "KeyError"}
    assert "private projection text" not in caplog.text
    j.close()


def test_b03_worktree_binding_backfill_matches_live_and_reopens_without_writes(tmp_path, monkeypatch):
    """B01/B03, §08/§11: step 2 seeds proven binding seqs, preserving live command participation."""
    from bat_agent_connector import observation
    from bat_agent_connector.operations import ActionDef, OperationService

    async def never_run(ctx):
        raise AssertionError("read-model fixture must not execute operations")

    def populate(j):
        ops = OperationService(j, actions=[ActionDef("checkpoint.continue", "observe", "Fixture only", never_run)])
        principal = api_auth.Principal("fixture", frozenset({"observe"}))
        op, _ = ops.create(principal, action="checkpoint.continue", target={"host": "h1"}, idempotency_key="creation")
        ops._merge_refs(op["operation_id"], {"host": "h1", "session_id": "shared", "worktree_path": "/srv/wt"})
        binding_seq = j.api_head()
        t = task(j, "past")
        bind(j, t, "shared")
        j.change(t["task_id"], "failed")
        bind(j, task(j, "current"), "shared")
        return worktree_id("h1", "checkpoint.continue", op["operation_id"], "worktree"), binding_seq

    def participation(j, wid):
        return sorted((j.get(r["execution_id"])["idem_key"], r["status"],
                       [j.command_get(cid)["kind"] for cid in r["command_ids"]]) for r in relation_pages(Observation(j), wid))

    live = Journal(tmp_path / "live.db")
    live_wid, live_seq = populate(live)
    assert live.db.execute("SELECT start_seq FROM session_worktree_bindings").fetchone()[0] == live_seq
    expected = participation(live, live_wid)
    path = tmp_path / "legacy.db"
    with monkeypatch.context() as legacy:
        legacy.setattr(observation, "install", lambda journal: None)
        j = Journal(path)
    wid, seq = populate(j)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == 1
    j.close()
    original = observation.backfill
    calls = []

    def capture(journal):
        calls.append(True)
        original(journal)

    monkeypatch.setattr(observation, "backfill", capture)
    j = Journal(path)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP
    binding = j.db.execute("SELECT * FROM session_worktree_bindings").fetchone()
    assert binding["start_seq"] == binding["linked_at_seq"] == seq
    assert binding["evidence_ref"] == f"api_events:{seq}"
    assert participation(j, wid) == expected
    # Command ownership is proven; an old relation's exact start still stays unknown.
    assert all(r["start_seq"] is None for r in relation_pages(Observation(j), wid))
    tables = ("session_worktree_bindings", "relation_revisions", "api_event_resources", "observation_backfill")
    saved = {table: [tuple(r) for r in j.db.execute(f"SELECT * FROM {table}")] for table in tables}
    head = j.api_head()
    j.close()
    j = Journal(path)
    assert calls == [True] and j.api_head() == head and j.db.total_changes == 0
    assert saved == {table: [tuple(r) for r in j.db.execute(f"SELECT * FROM {table}")] for table in tables}
    assert participation(j, wid) == expected
    j.close()
    live.close()


def test_b03_saved_worktree_binding_uses_backfill_link_seq_without_inventing_earlier_range(tmp_path):
    """B03, §08: a saved current pointer alone does not prove an earlier worktree assignment."""
    path = tmp_path / "j.db"
    j = Journal(path)
    past = task(j, "past")
    bind(j, past, "sid")
    j.change(past["task_id"], "failed")
    current = task(j, "current")
    bind(j, current, "sid")
    with j.tx():
        wid = worktree(j, "h1", "registry", "root@123", "worktree")
        remember(j.db, "session", "h1/sid", worktree_id=wid)
    j.db.execute("PRAGMA user_version=1")
    j.close()
    j = Journal(path)
    binding = j.db.execute("SELECT * FROM session_worktree_bindings").fetchone()
    seed = j.db.execute("SELECT seq FROM observation_backfill WHERE source_key='session_worktree_bindings:h1/sid'").fetchone()[0]
    assert binding["start_seq"] == binding["linked_at_seq"] == seed
    assert j.db.execute("SELECT linked_at_seq FROM api_event_resources WHERE seq=? AND resource_type='session'", (seed,)).fetchone()[0] == seed
    rows = relation_pages(Observation(j), wid)
    assert [r["execution_id"] for r in rows] == [current["task_id"]]
    assert rows[0]["command_ids"] == []  # Their earlier commands have no proven worktree binding.
    assert rows[0]["worktree_ranges"][0]["start_seq"] == seed
    assert {e["seq"] for e in Observation(j).history("worktree", wid)["events"]} == {seed}
    head = j.api_head()
    j.close()
    j = Journal(path)
    assert j.api_head() == head and j.db.total_changes == 0
    j.close()


def test_b01_id_paging_complete_and_filter_changes_via_events(mock, tmp_path):
    j = Journal(tmp_path / "j.db")
    inv = Inventory(j, make_config(mock))
    rows = [{"session_id": f"sid-{n:03}", "agent_kind": "codex", "has_tab": n % 2 == 0,
             "provenance": "unknown", "api_access": "read_only"} for n in range(205)]
    inv._record_success("h1", time.time(), rows, "v-test")
    cursor, ids = None, []
    while True:
        page = inv.list_sessions(order="id", limit=13, cursor=cursor)
        ids += [s["resource_id"] for s in page["sessions"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(ids) == 205 == len(set(ids)) and ids == sorted(ids)
    first = inv.list_sessions(order="id", has_tab=False, limit=1)
    rows[0]["has_tab"] = False
    inv._record_success("h1", time.time(), rows, "v-test")
    changes = j.api_events(first["as_of"])["events"]
    assert any(e["kind"] == "session.updated" and e["resource_id"] == "h1/sid-000" for e in changes)
    with pytest.raises(ValueError):
        inv.list_sessions(order="id", cursor=first["next_cursor"], has_tab=True)
    tables = {r[0] for r in j.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "observation_revisions" not in tables and "discovery_scans" not in tables
    j.close()


def test_b01_history_as_of_late_binding_and_invalid_cursors(tmp_path):
    j = Journal(tmp_path / "j.db")
    with j.tx():
        a = j.api_event("session", "h1/sid", "session.added", {})
        old = j.api_event("operation", "old", "operation.accepted", {})
        j.api_event("session", "h1/sid", "session.updated", {})
    obs = Observation(j)
    page = obs.history("session", "h1/sid", limit=1)
    with j.tx():
        bound = j.api_event("session", "h1/sid", "resource.bound", {})
        index(j.db, old, "session", "h1/sid", bound)
    tail = obs.history("session", "h1/sid", limit=1, cursor=page["next_cursor"])
    assert [e["seq"] for e in tail["events"]] == [a]
    assert old in [e["seq"] for e in obs.history("session", "h1/sid")["events"]]
    for kw in ({"cursor": "bad"}, {"limit": 0}, {"limit": True}, {"order": "time"}, {"since": float("nan")},
               {"cursor": page["next_cursor"], "order": "asc"}):
        with pytest.raises(OperationError):
            obs.history("session", "h1/sid", **kw)
    j.close()


def test_b01_worktree_shared_creation_identity_and_reuse(mock, tmp_path):
    from bat_agent_connector.observation import registry_bindings
    j = Journal(tmp_path / "j.db")
    raw = json.dumps(["worktree", "h1", "task", "t", "external_worktree"], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert worktree_id("h1", "task", "t", "external_worktree") == "wt_" + hashlib.sha256(raw.encode()).hexdigest()[:32]
    entries = [{"session_id": "original", "created_at": 123.456, "worktree_path": "/srv/wt"},
               {"session_id": "review", "created_at": 124.0, "worktree_path": "/srv/wt", "lead_session_id": "original"},
               {"session_id": "successor", "created_at": 125.0, "worktree_path": "/srv/wt", "failover_of": "original"}]
    registry_bindings(j, "h1", entries)
    ids = {Observation(j).resource("session", f"h1/{e['session_id']}")["worktree_id"] for e in entries}
    assert ids == {worktree_id("h1", "registry", "original@123.456", "worktree")}
    entries[0]["task_id"] = "new-task"
    registry_bindings(j, "h1", list(reversed(entries)))
    assert Observation(j).resource("session", "h1/original")["worktree_id"] in ids
    with j.tx():
        newer = worktree(j, "h1", "checkpoint.continue", "another", "worktree", path="/srv/wt")
    assert newer not in ids
    j.close()


@pytest.mark.parametrize("slot", ["task", "checkpoint"])
@pytest.mark.parametrize("legacy", [False, True])
def test_b01_b03_legacy_connector_branch_keeps_journaled_slot_and_history(tmp_path, slot, legacy):
    """B01/B03, §08/§11: legacy batc/ rows retain connector IDs, including step-2 replay."""
    from bat_agent_connector.observation import registry_bindings
    from bat_agent_connector.operations import ActionDef, OperationService

    path = tmp_path / "j.db"
    j = Journal(path)
    t = task(j, "legacy-connector")
    if slot == "task":
        j.change(t["task_id"], "dispatching", fields={"session_id": "root"})
        with j.tx():
            j.api_event("task", t["task_id"], "task.external_worktree_retained",
                        {"path": "/srv/wt", "branch": "batc/task-fixture"})
        wid = worktree_id("h1", "task", t["task_id"], "external_worktree")
    else:
        async def never_run(ctx):
            raise AssertionError("identity fixture must not execute operations")
        ops = OperationService(j, actions=[ActionDef("checkpoint.continue", "observe", "Fixture only", never_run)])
        op, _ = ops.create(api_auth.Principal("fixture", frozenset({"observe"})), action="checkpoint.continue",
                           target={"host": "h1"}, idempotency_key="creation")
        ops._merge_refs(op["operation_id"], {"host": "h1", "session_id": "root", "worktree_path": "/srv/wt"})
        wid = worktree_id("h1", "checkpoint.continue", op["operation_id"], "worktree")
    entries = [{"host": "h1", "session_id": "root", "created_at": 123, "worktree_path": "/srv/wt",
                "branch": "batc/task-fixture"},
               {"host": "h1", "session_id": "review", "created_at": 124, "worktree_path": "/srv/wt",
                "role": "reviewer", "lead_session_id": "root", "branch": "batc/task-fixture"}]
    registry_bindings(j, "h1", list(reversed(entries)))
    bind(j, t, "root")
    bind(j, t, "review", role="reviewer")
    if legacy:
        for table in ("session_worktree_bindings", "observation_resources", "observation_backfill"):
            j.db.execute(f"DELETE FROM {table}")
        j = replay_version_one(j, path)
        assert j.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP
        registry_bindings(j, "h1", entries)
    obs = Observation(j)
    worktree_events = {e["seq"] for e in obs.history("worktree", wid)["events"]}
    for sid in ("root", "review"):
        assert obs.resource("session", f"h1/{sid}")["worktree_id"] == wid
        relations = obs.relations("session", f"h1/{sid}")["relations"]
        assert len(relations) == 1 and relations[0]["execution_id"] == t["task_id"]
        session_events = obs.history("session", f"h1/{sid}")["events"]
        own_events = {e["seq"] for e in session_events if e["kind"].startswith("relation.")}
        assert own_events and own_events <= worktree_events
    assert {r["session_resource_id"] for r in obs.relations("worktree", wid)["relations"]} == {"h1/root", "h1/review"}
    assert {r["resource_id"] for r in j.db.execute("SELECT resource_id FROM observation_resources WHERE resource_type='worktree'")} == {wid}
    head = j.api_head()
    state = [tuple(r) for r in j.db.execute("SELECT * FROM session_worktree_bindings")]
    j.close()
    j = Journal(path)
    assert j.api_head() == head and j.db.total_changes == 0
    registry_bindings(j, "h1", entries)
    assert j.db.total_changes == 0 and j.api_head() == head
    assert [tuple(r) for r in j.db.execute("SELECT * FROM session_worktree_bindings")] == state
    j.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_b01_legacy_connector_branch_without_slot_has_no_worktree_identity(tmp_path, legacy):
    """B01, §08: a legacy connector branch alone is no BAT creation or ownership proof."""
    from bat_agent_connector.observation import registry_bindings

    path = tmp_path / "j.db"
    j = Journal(path)
    t = task(j, "unknown-slot")
    bind(j, t, "root")
    entries = [{"session_id": "root", "created_at": 123, "worktree_path": "/srv/wt", "branch": "batc/task-fixture"},
               {"session_id": "child", "created_at": 124, "worktree_path": "/srv/wt", "failover_of": "root"}]
    registry_bindings(j, "h1", entries)
    if legacy:
        j = replay_version_one(j, path)
        registry_bindings(j, "h1", entries)
    obs = Observation(j)
    assert all(obs.resource("session", f"h1/{e['session_id']}").get("worktree_id") is None for e in entries)
    assert j.db.execute("SELECT COUNT(*) FROM observation_resources WHERE resource_type='worktree'").fetchone()[0] == 0
    assert j.db.execute("SELECT COUNT(*) FROM session_worktree_bindings").fetchone()[0] == 0
    j.close()


@pytest.mark.parametrize("parent_maker", ["bat", "connector"])
@pytest.mark.parametrize("legacy", [False, True])
def test_b01_b03_nonsharing_failover_never_links_old_worktree(tmp_path, parent_maker, legacy):
    """B01/B03, §08/§11: a main-checkout successor has no carrier binding, live or after step-2 replay."""
    from bat_agent_connector.observation import registry_bindings
    from bat_agent_connector.resource_ids import registry_worktree_intent

    path = tmp_path / "j.db"
    j = Journal(path)
    past = task(j, "past")
    root = {"host": "h1", "session_id": "root", "created_at": 123, "worktree_path": "/srv/wt",
            "branch": "bat/worktree-fixture" if parent_maker == "bat" else "batc/task-fixture"}
    if parent_maker == "connector":
        j.change(past["task_id"], "dispatching", fields={"session_id": "root"})
        with j.tx():
            j.api_event("task", past["task_id"], "task.external_worktree_retained",
                        {"path": root["worktree_path"], "branch": root["branch"]})
    registry_bindings(j, "h1", [root])
    wid = Observation(j).resource("session", "h1/root")["worktree_id"]
    bind(j, past, "root")
    j.change(past["task_id"], "failed")
    cutoff = j.api_head()
    child = {"host": "h1", "session_id": "child", "created_at": 124, "worktree_path": None,
             "branch": None, "failover_of": "root", "shares_worktree_with": None, "cwd": "/srv/origin"}
    entries = [child, root]
    assert registry_worktree_intent(entries, "h1", "child") is None
    registry_bindings(j, "h1", entries)
    with j.tx():
        j.api_event("session", "h1/child", "session.added", {"has_tab": False})
    current = task(j, "after-failover")
    bind(j, current, "child")
    j.change(current["task_id"], "failed")
    if legacy:
        for table in ("session_worktree_bindings", "observation_resources", "observation_backfill"):
            j.db.execute(f"DELETE FROM {table}")
        j = replay_version_one(j, path)
        assert j.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP
        registry_bindings(j, "h1", entries)
    obs = Observation(j)
    assert obs.resource("session", "h1/child").get("worktree_id") is None
    assert j.db.execute("SELECT COUNT(*) FROM session_worktree_bindings WHERE session_resource_id='h1/child'").fetchone()[0] == 0
    child_events = {e["seq"] for e in obs.history("session", "h1/child")["events"]}
    assert child_events and min(child_events) > cutoff
    old_events = obs.history("worktree", wid)["events"]
    assert not child_events.intersection(e["seq"] for e in old_events)
    assert all("h1/child" not in e["context"]["session_resource_ids"] for e in old_events)
    assert {r["session_resource_id"] for r in obs.relations("worktree", wid)["relations"]} == {"h1/root"}
    head = j.api_head()
    j.close()
    j = Journal(path)
    assert j.api_head() == head and j.db.total_changes == 0
    assert j.db.execute("SELECT COUNT(*) FROM session_worktree_bindings WHERE session_resource_id='h1/child'").fetchone()[0] == 0
    j.close()


@pytest.mark.parametrize("explicit_lead", [False, True])
@pytest.mark.parametrize("task_path", ["/srv/wt", "/srv/other", None])
def test_b01_registry_reviewer_without_path_requires_recorded_task_carrier(tmp_path, explicit_lead, task_path):
    """B01, §08: the shared fallback uses task carrier evidence before inheriting a connector slot."""
    from bat_agent_connector.observation import registry_bindings

    j = Journal(tmp_path / "j.db")
    t = task(j, "review")
    with j.tx():
        wid = worktree(j, "h1", "checkpoint.continue", "creation", "worktree", path="/srv/wt", session_id="root")
        j.db.execute("UPDATE tasks SET session_id='root',external_worktree_path=? WHERE task_id=?", (task_path, t["task_id"]))
    entries = [{"session_id": "root", "created_at": 123, "worktree_path": "/srv/wt", "branch": "batc/cp-fixture"},
               {"session_id": "review", "created_at": 124, "worktree_path": None, "role": "reviewer",
                "task_id": t["task_id"], "lead_session_id": "root" if explicit_lead else None}]
    registry_bindings(j, "h1", entries)
    assert Observation(j).resource("session", "h1/review").get("worktree_id") == (wid if task_path == "/srv/wt" else None)
    j.close()


def test_b01_relation_scope_and_cross_project_link_history(mock, tmp_path):
    j = Journal(tmp_path / "j.db")
    inv = Inventory(j, make_config(mock))
    t = task(j, "a")
    bind(j, t, "sid")
    # Use the existing work-item operation writer, with no provider call.
    from bat_agent_connector import work_items
    from bat_agent_connector.artifacts import ArtifactSettings, ArtifactStore
    from bat_agent_connector.operations import OperationService
    ops = OperationService(j, actions=work_items.ACTIONS)
    ops.context["fleet"] = inv.fleet
    ops.context["artifact_store"] = ArtifactStore(ops, ArtifactSettings(store_root=str(tmp_path / "artifacts")))
    principal = api_auth.Principal("agent", frozenset({"observe", "manage"}))
    async def create():
        p, _ = ops.create(principal, action="project.create", params={"name": "P"}, idempotency_key="p")
        await ops.drain()
        pid = ops.get(p["operation_id"])["result"]["project_id"]
        w, _ = ops.create(principal, action="work_item.create", target={"project_id": pid}, params={"title": "W", "request": "private", "acceptance": "checked"}, idempotency_key="w")
        await ops.drain()
        wid = ops.get(w["operation_id"])["result"]["work_item_id"]
        link, _ = ops.create(principal, action="work_item.link", target={"work_item_id": wid}, params={"kind": "task", "ref": t["task_id"]}, idempotency_key="l")
        await ops.drain()
        assert ops.get(link["operation_id"])["status"] == "succeeded"
        unlink, _ = ops.create(principal, action="work_item.link", target={"work_item_id": wid}, params={"kind": "task", "ref": t["task_id"], "remove": True}, idempotency_key="u")
        await ops.drain()
        assert ops.get(unlink["operation_id"])["status"] == "succeeded"
        return pid, wid
    pid, wid = asyncio.run(create())
    assert inv.list_sessions(order="id", project_id=[pid], work_item_id=wid)["count"] == 1
    assert inv.list_sessions(order="id", project_id=[pid], relation_scope="current")["count"] == 0
    history = Observation(j).history("session", "h1/sid")["events"]
    links = [e for e in history if e["kind"] in {"work_item.linked", "work_item.unlinked"}]
    assert len(links) == 2 and all(e["body"]["link_id"] and e["context"]["work_item_ids"] == [wid] for e in links)
    assert links[0]["context"]["actor"] == "agent"
    assert "private" not in dump(history)
    j.close()


async def test_b02_discovery_latest_no_poll_rows_and_no_host_fanout(mock, tmp_path):
    j = Journal(tmp_path / "j.db")
    inv = Inventory(j, make_config(mock))
    await inv.refresh_host("h1")
    before = j.api_head()
    for _ in range(7):
        await inv.refresh_host("h1")
    assert j.api_head() == before
    assert j.db.execute("SELECT COUNT(*) FROM discovery_latest").fetchone()[0] == 1
    scope = inv.discovery("h1")["scopes"][0]
    assert scope["authority"]["verified"] and scope["complete_enumeration"]
    assert any(x["scope"] == "background_git_state" for x in scope["outside_scan"])
    inv._record_failure("h1", time.time(), RuntimeError("offline"))
    kinds = [e["kind"] for e in j.api_events(before)["events"]]
    assert kinds == ["host.unreachable", "discovery.changed"]
    assert inv.get_session("h1", MANUAL)["state"]["connection"] == "not_connected"
    assert all(s["stale"] for s in inv.list_sessions()["sessions"])
    await inv.close()
    j.close()


async def test_b02_unchanged_poll_does_not_rewrite_observation_identities(mock, tmp_path, monkeypatch):
    from bat_agent_connector import inventory
    j = Journal(tmp_path / "j.db")
    inv = Inventory(j, make_config(mock))
    with controller_scope(j):
        adopt("registry-only", worktree_path="/srv/wt", worktree_made_by="bat")
    changes = []
    original = inventory.registry_bindings

    def capture(journal, host, entries):
        before = journal.db.total_changes
        original(journal, host, entries)
        changes.append(journal.db.total_changes - before)

    monkeypatch.setattr(inventory, "registry_bindings", capture)
    await inv.refresh_host("h1")
    tables = ("observation_resources", "session_worktree_bindings", "observation_relations", "relation_revisions", "command_relations",
              "api_event_context", "api_event_resources", "observation_backfill")
    before = {table: [tuple(r) for r in j.db.execute(f"SELECT * FROM {table}")] for table in tables}
    await inv.refresh_host("h1")
    assert changes[0] > 0 and changes[1] == 0
    assert before == {table: [tuple(r) for r in j.db.execute(f"SELECT * FROM {table}")] for table in tables}
    await inv.close()
    j.close()


def freshness_sessions(mock):
    sids = [MANUAL, "sess-unload-0003"]
    mock.ws_doc["terminals"] = [t for t in mock.ws_doc["terminals"] if t["id"] in sids]
    mock.metas[sids[1]] = dict(mock.metas[sids[0]])
    failing = set()

    def meta(params):
        sid = params["sessionId"]
        if sid in failing:
            raise RuntimeError("private meta failure details")
        return mock.metas[sid]

    mock.handlers["claude:get-session-meta"] = meta
    return sids, failing


async def test_b02_b03_field_freshness_swap_catches_up_through_events(served, mock, monkeypatch, capsys):
    """B02/B03, §11/§19: swapping meta failures stays visible even with unchanged discovery coverage."""
    d, port = served
    sids, failing = freshness_sessions(mock)
    viewer = token(d, "viewer", "observe")
    monkeypatch.setenv("BATC_API_TOKEN", viewer)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    await d.inventory.refresh_host("h1")
    added = [e for e in d.journal.api_events()["events"] if e["kind"] == "session.added"]
    assert len(added) == 2 and all(e["body"]["fields_stale"] is False for e in added)
    failing.add(sids[0])
    await d.inventory.refresh_host("h1")
    status, baseline = await http(port, "GET", "/api/v1/sessions?order=id", tok=viewer)
    assert status == 200
    assert {s["session_id"]: s["fields_stale"] for s in baseline["sessions"]} == {sids[0]: True, sids[1]: False}
    scope = d.inventory.discovery("h1")["scopes"][0]
    assert scope["status"] == "partial"
    failing.remove(sids[0])
    failing.add(sids[1])
    await d.inventory.refresh_host("h1")
    current_scope = d.inventory.discovery("h1")["scopes"][0]
    assert current_scope["status"] == "partial" and current_scope["coverage"] == scope["coverage"]
    status, catchup = await http(port, "GET", f"/api/v1/events?after={baseline['as_of']}", tok=viewer)
    assert status == 200
    assert [e["kind"] for e in catchup["events"]] == ["session.updated", "session.updated"]
    updates = {e["resource_id"]: e["body"] for e in catchup["events"]}
    assert set(updates) == {"h1/" + sid for sid in sids}
    assert "private meta failure details" not in dump(catchup)
    status, current = await http(port, "GET", "/api/v1/sessions?order=id", tok=viewer)
    assert status == 200
    for session in current["sessions"]:
        stale = session["session_id"] == sids[1]
        event = updates[session["resource_id"]]
        assert event["fields_stale"] is session["fields_stale"] is stale
        assert event["field_evidence"] == session["field_evidence"]
        assert set(event["changed_fields"]) == {"fields_stale", "field_evidence"}
        assert session["state"]["evidence"]["loading"]["stale"] is stale
        assert session["state"]["evidence"]["activity"]["stale"] is stale
        assert event["field_evidence"]["loaded"] == ("previous_session_meta" if stale else "session_meta")

    # Legacy or injected extra text stays out of summaries, including inside the evidence map.
    seq = catchup["events"][0]["seq"]
    saved = json.loads(d.journal.db.execute("SELECT body FROM api_events WHERE seq=?", (seq,)).fetchone()[0])
    saved["error"] = "private meta failure details"
    saved["field_evidence"]["note"] = "private meta failure details"
    with d.journal.tx():
        d.journal.db.execute("UPDATE api_events SET body=? WHERE seq=?", (dump(saved), seq))
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True)
    try:
        for sid, states in zip(sids, ([False, True, False], [False, True])):
            status, expected = await http(port, "GET", f"/api/v1/sessions/h1/{sid}/history?order=asc", tok=viewer)
            assert status == 200 and "private meta failure details" not in dump(expected)
            events = [e for e in expected["events"] if e["kind"] in {"session.added", "session.updated"}]
            assert [e["body"]["fields_stale"] for e in events] == states
            for event, stale in zip(events, states):
                source = "previous_session_meta" if stale else "session_meta"
                assert event["body"]["field_evidence"] == {"loaded": source, "streaming": source, "has_tab": "workspace_document"}
            result = await server.call_tool("resource_history", {"resource_type": "session", "resource_id": "h1/" + sid, "order": "asc"})
            if isinstance(result, tuple):
                content, structured = result
                actual = structured if structured is not None else json.loads(content[0].text)
            else:
                content = result.content if hasattr(result, "content") else result
                actual = json.loads(content[0].text)
            assert actual == expected
            assert await asyncio.to_thread(cli.main, ["--json", "history", "session", "h1", sid, "--order", "asc"]) == 0
            assert json.loads(capsys.readouterr().out) == expected
    finally:
        await fleet.close()
    assert write_frames(mock) == []


async def test_b02_unchanged_field_freshness_polls_emit_no_events(mock, tmp_path):
    """B02, §11: repeated failures/successes and advancing observation/activity times stay quiet."""
    j = Journal(tmp_path / "j.db")
    inv = Inventory(j, make_config(mock))
    sids, failing = freshness_sessions(mock)
    await inv.refresh_host("h1")
    failing.add(sids[0])
    await inv.refresh_host("h1")
    before = j.api_head()
    await inv.refresh_host("h1")
    assert j.api_head() == before
    failing.clear()
    await inv.refresh_host("h1")
    before = j.api_head()
    await inv.refresh_host("h1")
    assert j.api_head() == before
    rows = [json.loads(r[0]) for r in j.db.execute("SELECT body FROM sessions_observed")]
    for row in rows:
        row["last_activity_ms"] += 1000
    inv._record_success("h1", time.time() + 120, rows, "v-test")
    assert j.api_head() == before
    for row in rows:
        current = inv.get_session("h1", row["session_id"])
        assert current["field_observed_at"]["loading"] != row["field_observed_at"]["loading"]
        assert current["last_activity_ms"] == row["last_activity_ms"]
    await inv.close()
    j.close()


async def test_b02_field_evidence_only_change_and_old_digest_upgrade(mock, tmp_path):
    """B02, §11: evidence itself is material; upgrading the cached digest is not a transition."""
    from bat_agent_connector.inventory import MATERIAL

    j = Journal(tmp_path / "j.db")
    inv = Inventory(j, make_config(mock))
    mock.ws_doc["terminals"] = [t for t in mock.ws_doc["terminals"] if t["id"] == MANUAL]
    mock.metas[MANUAL] = {}
    await inv.refresh_host("h1")
    before = j.api_head()
    row = json.loads(j.db.execute("SELECT body FROM sessions_observed").fetchone()[0])
    old_material = {k: row.get(k) for k in MATERIAL if k not in {"fields_stale", "field_evidence"}}
    old_digest = hashlib.sha256(json.dumps(old_material, sort_keys=True, default=str).encode()).hexdigest()
    with j.tx():
        j.db.execute("UPDATE sessions_observed SET digest=?", (old_digest,))
    await inv.refresh_host("h1")
    assert j.api_head() == before
    mock.metas[MANUAL] = {"numTurns": 0}
    await inv.refresh_host("h1")
    events = j.api_events(before)["events"]
    assert len(events) == 1 and events[0]["kind"] == "session.updated"
    assert events[0]["body"]["changed_fields"] == ["field_evidence"]
    assert events[0]["body"]["fields_stale"] is False
    assert events[0]["body"]["field_evidence"]["streaming"] == "session_meta"
    assert inv.get_session("h1", MANUAL)["loaded"] is True
    assert inv.get_session("h1", MANUAL)["streaming"] is None
    await inv.close()
    j.close()


async def test_b02_two_hosts_one_offline_and_scope_change(mock, tmp_path):
    j = Journal(tmp_path / "j.db")
    cfg = make_config(mock)
    cfg.hosts["h2"] = replace(cfg.host("h1"), name="h2", url="wss://127.0.0.1:1/")
    inv = Inventory(j, cfg)
    inv._record_success("h2", time.time(), [{"session_id": "old", "loaded": True}], "v-test")
    results = await inv.refresh_all()
    assert {r["host"]: r["reachable"] for r in results} == {"h1": True, "h2": False}
    assert inv.list_sessions(order="id")["count"] == 4
    assert inv.get_session("h2", "old")["loaded"] is True
    cfg.hosts["h1"] = replace(cfg.host("h1"), profile_id="another-profile")
    frames = len(mock.invokes)
    result = await inv.refresh_host("h1")
    assert "DISCOVERY_SCOPE_CHANGED" in result["error"] and len(mock.invokes) == frames
    assert inv.get_session("h1", MANUAL)["stale_reason"] == "scope_changed"
    assert inv.get_session("h1", MANUAL)["profile_id"] == "default"
    cfg.hosts.pop("h1")
    assert inv.get_session("h1", MANUAL)["scope_status"] == "outside_current_config"
    assert Observation(j).history("session", "h1/" + MANUAL)["events"]
    await inv.close()
    j.close()


@pytest.mark.parametrize("changed_field", ["url", "fingerprint"])
async def test_b02_scope_change_stays_blocked_on_later_refreshes(mock, tmp_path, changed_field):
    """B02, §11: the refused scope never replaces the stored scope or starts later BAT reads."""
    j = Journal(tmp_path / "j.db")
    cfg = make_config(mock)
    inv = Inventory(j, cfg)
    await inv.refresh_host("h1")
    original = cfg.host("h1")
    stored_binding = inv._binding("h1")
    ids = {s["resource_id"] for s in inv.list_sessions(order="id")["sessions"]}
    obs = Observation(j)
    histories = {rid: {e["seq"]: e for e in obs.history("session", rid)["events"]} for rid in ids}
    assert "attempted_binding_version" not in inv.discovery("h1")["scopes"][0]
    fields = {changed_field: original.url + "?scope=changed" if changed_field == "url" else "0" * 64}
    cfg.hosts["h1"] = replace(original, **fields)
    assert cfg.host("h1").profile_id == original.profile_id
    attempted = inv._binding("h1")
    assert attempted != stored_binding
    first_head = None
    for _ in range(2):
        frames = list(mock.frames)
        result = await inv.refresh_host("h1")
        assert not result["reachable"] and "DISCOVERY_SCOPE_CHANGED" in result["error"]
        assert mock.frames == frames
        row = j.db.execute("SELECT binding,body FROM discovery_latest WHERE host=? AND profile_id=?", ("h1", original.profile_id)).fetchone()
        scope = json.loads(row["body"])
        assert row["binding"] == scope["binding_version"] == stored_binding
        assert scope["attempted_binding_version"] == attempted
        assert inv.discovery("h1")["scopes"][0] == scope
        assert {s["resource_id"] for s in inv.list_sessions(order="id")["sessions"]} == ids
        for rid, history in histories.items():
            current = {e["seq"]: e for e in obs.history("session", rid)["events"]}
            assert all(current[seq] == event for seq, event in history.items())
        if first_head is None:
            first_head = j.api_head()
        else:
            assert j.api_head() == first_head
    cfg.hosts["h1"] = original
    inv._record_failure("h1", time.time(), RuntimeError("offline"))
    assert "attempted_binding_version" not in inv.discovery("h1")["scopes"][0]
    await inv.close()
    j.close()


async def test_b02_null_workspace_preserves_missing_counts_even_with_registry(mock, tmp_path):
    j = Journal(tmp_path / "j.db")
    inv = Inventory(j, make_config(mock))
    with controller_scope(j):
        adopt("registry-only")
    await inv.refresh_host("h1")
    mock.handlers["workspace:load"] = lambda p: None
    for _ in range(3):
        assert not (await inv.refresh_host("h1"))["reachable"]
    assert all(r[0] == 0 for r in j.db.execute("SELECT missing_count FROM sessions_observed"))
    assert inv.get_session("h1", "registry-only")["has_tab"] is False
    await inv.close()
    j.close()


async def test_b02_session_specific_stale_gone_fresh_and_get_no_writes(mock, tmp_path):
    j = Journal(tmp_path / "j.db")
    inv = Inventory(j, make_config(mock))
    await inv.refresh_host("h1")
    original = mock.ws_doc["terminals"]
    mock.ws_doc["terminals"] = []
    start = j.api_head()
    await inv.refresh_host("h1")
    assert inv.get_session("h1", MANUAL)["state"]["enumeration"] == "missing"
    assert inv.get_session("h1", MANUAL)["state"]["tab"] == "no_tab"
    assert not inv.get_session("h1", MANUAL)["state"]["evidence"]["connection"]["stale"]
    await inv.refresh_host("h1")
    assert inv.get_session("h1", MANUAL)["state"]["enumeration"] == "gone"
    await inv.refresh_host("h1")
    kinds = [e["kind"] for e in j.api_events(start, resource_id="h1/" + MANUAL)["events"]]
    assert kinds == ["session.stale", "session.gone", "session.stale"]
    mock.ws_doc["terminals"] = original
    await inv.refresh_host("h1")
    tail = j.api_events(start, resource_id="h1/" + MANUAL)["events"]
    assert [e["kind"] for e in tail][-2:] == ["session.reappeared", "session.fresh"]
    before = j.db.total_changes
    for _ in range(3):
        inv.get_session("h1", MANUAL)
        inv.list_sessions(stale=True)
        inv.discovery("h1")
    assert j.db.total_changes == before
    await inv.close()
    j.close()


async def test_b03_states_unknown_no_tab_and_field_times(mock, tmp_path):
    j = Journal(tmp_path / "j.db")
    inv = Inventory(j, make_config(mock))
    bind(j, task(j, "known"), "unobserved")
    unknown = inv.get_session("h1", "unobserved")
    assert unknown["loaded"] is unknown["has_tab"] is unknown["streaming"] is None
    assert unknown["state"]["lifecycle"] == "unknown" and unknown["api_access"] == "read_only"
    with controller_scope(j):
        adopt("no-tab")
    await inv.refresh_host("h1")
    unloaded = inv.get_session("h1", "sess-unload-0003")
    assert unloaded["state"]["loading"] == "not_loaded" and unloaded["state"]["activity"] == "unknown"
    assert inv.get_session("h1", "no-tab")["state"]["tab"] == "no_tab"
    old = inv.get_session("h1", MANUAL)
    mock.handlers["claude:get-session-meta"] = lambda p: (_ for _ in ()).throw(RuntimeError("unavailable"))
    await inv.refresh_host("h1")
    new = inv.get_session("h1", MANUAL)
    assert new["state"]["loading"] == "loaded"
    assert new["state"]["evidence"]["activity"]["observed_at"] == old["state"]["evidence"]["activity"]["observed_at"]
    assert new["state"]["evidence"]["activity"]["stale"]
    await inv.close()
    j.close()


def test_b03_unknown_human_claim_is_not_api_actor_or_git_author(tmp_path):
    j = Journal(tmp_path / "j.db")
    t = task(j, "a")
    bind(j, t, "sid")
    j.note(t["task_id"], "manual_evidence", {"actor": "ted", "git_author": "Some Author", "words": "private prompt"})
    e = next(e for e in Observation(j).history("session", "h1/sid")["events"] if e["kind"] == "task.manual_evidence")
    assert e["context"]["actor"] is None and e["context"]["actor_basis"] == "unknown"
    assert e["context"]["claimed_actor"] == "ted" and "actor" in e["context"]["unknown_fields"]
    assert "private prompt" not in dump(e)
    j.close()


@pytest.mark.parametrize("backfilled", [False, True])
async def test_b03_history_drops_task_prose_and_keeps_codes_over_http_mcp_cli(served, monkeypatch, capsys, backfilled):
    """B03, §08/§10/§11: caller reasons stay in core rows, never in live/replayed resource history."""
    from bat_agent_connector import observation

    d, port = served
    j = d.journal
    t = task(j, "private-reasons")
    command = bind(j, t, "lead")
    requested = "Distinctive private request Ted reason"
    diagnostic = "Distinctive private needs Ted diagnostic"
    failure = "Distinctive private failed diagnostic"
    decision = "Distinctive private verification decision"
    conflict = "Distinctive private command conflict"
    j.request_ted(t["task_id"], requested)
    j.change(t["task_id"], "needs_ted", fields={"result": diagnostic})
    j.reserve_minimal_review(t["task_id"], "a" * 40, "b" * 40, "c" * 64, threshold=0.8, diff_chars=10)
    j.finish_minimal_review(t["task_id"], "a" * 40, "b" * 40, {"verdict": "escalate", "reason": decision})
    j.command_operator_only(command["command_id"], conflict)
    j.note(t["task_id"], "diagnostic_code", {"reason": diagnostic, "reason_code": "HUMAN_REVIEW_REQUIRED", "error_code": "CHECK_FAILED"})
    j.change(t["task_id"], "failed", fields={"result": failure})
    with j.tx():
        j.api_event("session", "h1/lead", "session.stale", {"reason": "not_enumerated"}, context={"resources": [("execution", t["task_id"])]})
        j.api_event("session", "h1/lead", "session.fresh", {"previous_reason": "not_enumerated"}, context={"resources": [("execution", t["task_id"])]})
    if backfilled:
        with j.tx():
            for table in ("api_event_context", "api_event_resources", "command_relations", "relation_revisions", "observation_relations"):
                j.db.execute(f"DELETE FROM {table}")
            j.db.execute("PRAGMA user_version=1")
        observation.install(j)
    assert j.get(t["task_id"])["result"] == failure
    core = dump(j.api_events(limit=200)["events"])
    assert all(text in core for text in (requested, diagnostic, failure, decision, conflict))
    viewer = token(d, "viewer", "observe")
    monkeypatch.setenv("BATC_API_TOKEN", viewer)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    status, expected = await http(port, "GET", f"/api/v1/tasks/{t['task_id']}/history?order=asc&limit=200", tok=viewer)
    assert status == 200
    assert all(text not in dump(expected) for text in (requested, diagnostic, failure, decision, conflict))
    codes = next(e for e in expected["events"] if e["kind"] == "task.diagnostic_code")["body"]
    assert codes == {"reason_code": "HUMAN_REVIEW_REQUIRED", "error_code": "CHECK_FAILED"}
    state = next(e for e in expected["events"] if e["kind"] == "task.state")["body"]
    assert state["to"] == "needs_ted" and "reason" not in state
    relations = [e for e in expected["events"] if e["kind"] == "relation.bound"]
    assert {e["body"]["reason"] for e in relations} == {None, "start"}
    events = Observation(j).history("session", "h1/lead", limit=200)["events"]
    assert next(e for e in events if e["kind"] == "session.stale")["body"]["reason"] == "not_enumerated"
    assert next(e for e in events if e["kind"] == "session.fresh")["body"]["previous_reason"] == "not_enumerated"
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True)
    try:
        result = await server.call_tool("resource_history", {"resource_type": "execution", "resource_id": t["task_id"], "order": "asc", "limit": 200})
        if isinstance(result, tuple):
            content, structured = result
            actual = structured if structured is not None else json.loads(content[0].text)
        else:
            content = result.content if hasattr(result, "content") else result
            actual = json.loads(content[0].text)
        assert actual == expected
        assert await asyncio.to_thread(cli.main, ["--json", "history", "execution", t["task_id"], "--order", "asc", "--limit", "200"]) == 0
        assert json.loads(capsys.readouterr().out) == expected
    finally:
        await fleet.close()


def test_b03_history_summary_filters_prose_recursively_in_bodies_snapshots_and_resource(tmp_path):
    """B03, §08/§11: prose cannot hide in a formerly allowed scalar or nested structure."""
    j = Journal(tmp_path / "j.db")
    private = "Distinctive private prose through a summary field"
    prose = {key: private for key in ("reason", "previous_reason", "title", "status_reason", "git_author", "source", "evidence", "request", "response", "body", "errors", "blocking", "ref", "external_ref", "code", "error_code", "reason_code", "verification_error", "read_only_code",
        "target", "refs", "before", "after", "saved_snapshot", "field_evidence", "field_observed_at", "coverage", "methods", "authority", "source_versions", "result_versions", "affected_prs", "stacks", "merge_receipt", "metadata_settlement", "metadata_reconciliation")}
    with j.tx():
        remember(j.db, "session", "h1/sid", host="h1", session_id="sid", title=private)
        live = j.api_event("session", "h1/sid", "session.updated", {**prose,
            "request": {**prose, "reason_code": "CHECK_FAILED"},
            "response": {**prose, "source": {**prose, "session_id": "sid"}},
            "evidence": [private, {**prose, "table": "api_events", "id": 1}],
            "errors": [private, {"error_code": "OFFLINE", "error": private}],
            "blocking": [private, {"code": "MERGE_SCOPE_UNPROVEN", "message": private}],
            "source_versions": [{"kind": "git", "sha": "a" * 40, **prose}]})
        snapshot = saved_fact(j, "legacy", "prose", {**prose, "body": {**prose, "reason": "gone"},
            "source": "observed_runner", "created_at": 123}, [("session", "h1/sid")])
    history = Observation(j).history("session", "h1/sid", order="asc")
    assert private not in dump(history)
    events = {e["seq"]: e["body"] for e in history["events"]}
    assert events[live]["request"] == {"reason_code": "CHECK_FAILED"}
    assert events[live]["response"] == {"source": {"session_id": "sid"}}
    assert events[live]["evidence"] == [{"table": "api_events", "id": 1}]
    assert events[live]["errors"] == [{"error_code": "OFFLINE"}]
    assert events[live]["blocking"] == [{"code": "MERGE_SCOPE_UNPROVEN"}]
    assert events[live]["source_versions"] == [{"kind": "git", "sha": "a" * 40}]
    assert events[snapshot]["saved_snapshot"]["body"] == {"reason": "gone"}
    assert events[snapshot]["saved_snapshot"]["source"] == "observed_runner"
    t = task(j, "stage-prose")
    with j.tx():
        j.db.execute("UPDATE tasks SET state='done',verification_commit=? WHERE task_id=?", ("a" * 40, t["task_id"]))
    j.mark_stage(t["task_id"], stage="deployed", ref=private, actor="executor")
    deployed = next(e for e in Observation(j).history("execution", t["task_id"])["events"] if e["kind"] == "task.stage_deployed")
    assert "ref" not in deployed["body"] and private not in dump(deployed)
    j.mark_stage(t["task_id"], stage="merged", ref="https://github.com/o/r/pull/9", actor="executor")
    merged = next(e for e in Observation(j).history("execution", t["task_id"])["events"] if e["kind"] == "task.stage_merged")
    assert merged["body"]["ref"] == "https://github.com/o/r/pull/9"
    j.close()


def test_b03_backfill_hidden_idempotent_and_unknown_boundaries(tmp_path):
    path = tmp_path / "j.db"
    j = Journal(path)
    t = task(j, "a")
    bind(j, t, "sid")
    j.db.execute("DELETE FROM api_event_context")
    j.db.execute("DELETE FROM api_event_resources")
    j.db.execute("DELETE FROM command_relations")
    j.db.execute("DELETE FROM relation_revisions")
    j.db.execute("DELETE FROM observation_relations")
    j.db.execute("PRAGMA user_version=1")
    j.close()
    j = Journal(path)
    assert all(r["start_seq"] is None for r in Observation(j).relations("session", "h1/sid")["relations"])
    with j.tx():
        saved_fact(j, "commands", "orphan", {"request": {"text": "private"}, "created_at": 123}, [("session", "h1/sid")])
    head = j.api_head()
    assert all(e["kind"] != "history.backfilled" for e in j.api_events()["events"])
    assert j.api_events()["next_cursor"] == head
    assert j.api_events(kind="history.backfilled")["events"]
    h = Observation(j).history("session", "h1/sid")["events"]
    assert any(e["kind"] == "history.backfilled" and e["context"]["backfilled"] for e in h)
    assert "private" not in dump(h)
    j.close()
    j = Journal(path)
    assert j.api_head() == head
    j.close()


async def test_b02_cursor_catchup_sse_resume_and_hidden_backfill(served):
    d, port = served
    viewer = token(d, "viewer", "observe")
    with d.journal.tx():
        baseline = d.journal.api_event("session", "h1/sid", "session.added", {})
        for n in range(5):
            saved_fact(d.journal, "legacy", str(n), {}, [("session", "h1/sid")])
        for n in range(6):
            d.journal.api_event("session", "h1/sid", "session.updated", {"n": n})
    cursor, ids = baseline, []
    while True:
        status, page = await http(port, "GET", f"/api/v1/events?after={cursor}&limit=2", tok=viewer)
        assert status == 200
        ids += [e["seq"] for e in page["events"]]
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert len(ids) == len(set(ids)) == 6 and cursor == d.journal.api_head()
    with d.journal.tx():
        latest = d.journal.api_event("session", "h1/sid", "session.updated", {"n": 7})
    async def stream(after):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write((f"GET /api/v1/events/stream HTTP/1.1\r\nHost: localhost:{port}\r\nAuthorization: Bearer {viewer}\r\nLast-Event-ID: {after}\r\n\r\n").encode())
        await writer.drain()
        await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        frame = await asyncio.wait_for(reader.readuntil(b"\n\n"), 5)
        writer.close()
        await writer.wait_closed()
        return frame
    frame = await stream(cursor)
    assert f"id: {latest}\n".encode() in frame and b"history.backfilled" not in frame
    with d.journal.tx():
        newest = d.journal.api_event("session", "h1/sid", "session.updated", {"n": 8})
    frame2 = await stream(latest)
    assert f"id: {newest}\n".encode() in frame2 and f"id: {latest}\n".encode() not in frame2
    status, reset = await http(port, "GET", f"/api/v1/events/stream?after={newest+100}", tok=viewer)
    assert status == 409 and reset["error"]["code"] == "EVENT_CURSOR_RESET"
    assert reset["error"]["reason"] == "cursor_ahead"


async def test_b01_b02_b03_http_mcp_cli_contract_parity(served, mock, monkeypatch, capsys):
    d, port = served
    viewer = token(d, "viewer", "observe")
    monkeypatch.setenv("BATC_API_TOKEN", viewer)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    await d.inventory.refresh_host("h1")
    t = task(d.journal, "a")
    bind(d.journal, t, MANUAL)
    with d.journal.tx():
        wid = worktree(d.journal, "h1", "task", t["task_id"], "external_worktree", path="/srv/wt", session_id=MANUAL)
        d.journal.api_event("session", "h1/" + MANUAL, "resource.bound", {})
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True)
    cases = [
        ("/api/v1/sessions?order=id&limit=1&provider=claude", "inventory_sessions", {"order": "id", "limit": 1, "provider": "claude"}, ["inventory", "sessions", "--order", "id", "--limit", "1", "--provider", "claude"]),
        ("/api/v1/sessions/h1/" + MANUAL, "inventory_session", {"host": "h1", "session_id": MANUAL}, ["inventory", "session", "h1", MANUAL]),
        ("/api/v1/sessions/h1/" + MANUAL + "/history?limit=2&order=asc", "resource_history", {"resource_type": "session", "resource_id": "h1/" + MANUAL, "limit": 2, "order": "asc"}, ["history", "session", "h1", MANUAL, "--limit", "2", "--order", "asc"]),
        ("/api/v1/sessions/h1/" + MANUAL + "/relations", "resource_relations", {"resource_type": "session", "resource_id": "h1/" + MANUAL}, ["relations", "session", "h1", MANUAL]),
        ("/api/v1/tasks/" + t["task_id"] + "/sessions", "resource_relations", {"resource_type": "execution", "resource_id": t["task_id"]}, ["relations", "execution", t["task_id"]]),
        ("/api/v1/worktrees/" + wid, "inventory_worktree", {"worktree_id": wid}, ["inventory", "worktree", wid]),
        ("/api/v1/worktrees/" + wid + "/history", "resource_history", {"resource_type": "worktree", "resource_id": wid}, ["history", "worktree", wid]),
        ("/api/v1/worktrees/" + wid + "/relations", "resource_relations", {"resource_type": "worktree", "resource_id": wid}, ["relations", "worktree", wid]),
        ("/api/v1/hosts/h1/discovery", "inventory_hosts", {"host": "h1", "discovery": True}, ["inventory", "discovery", "h1"]),
    ]
    for path, tool, params, command in cases:
        status, expected = await http(port, "GET", path, tok=viewer)
        assert status == 200, expected
        result = await server.call_tool(tool, params)
        if isinstance(result, tuple):
            content, structured = result
            actual = structured if structured is not None else json.loads(content[0].text)
        else:
            content = result.content if hasattr(result, "content") else result
            actual = json.loads(content[0].text)
        assert actual == expected, (tool, actual, expected)
        assert await asyncio.to_thread(cli.main, ["--json", *command]) == 0
        assert json.loads(capsys.readouterr().out) == expected
    assert len({"inventory_session", "inventory_worktree", "resource_history", "resource_relations"} & set(mcp_server.READ_TOOLS)) == 4
    assert (await http(port, "GET", "/api/v1/worktrees/wt_" + "0" * 32, tok=viewer))[0] == 404
    assert (await http(port, "GET", "/api/v1/sessions/h1/" + MANUAL + "/history?cursor=bad", tok=viewer))[0] == 422
    assert (await http(port, "GET", "/api/v1/sessions/h1/" + MANUAL + "/history"))[0] == 401
    cannot_read = token(d, "writer", "manage")
    assert (await http(port, "GET", "/api/v1/sessions/h1/" + MANUAL + "/relations", tok=cannot_read))[0] == 403
    assert write_frames(mock) == []
    await fleet.close()


@pytest.mark.parametrize(("method", "key"), [
    ("relations", key) for key in ("x", [1], [True, "r"], [1, 2], [None, "r"], [1, None], None)
] + [("history", key) for key in ("x", [1], True, None)])
def test_b01_cursor_keys_are_rejected_before_any_journal_read(tmp_path, method, key):
    """B01, §10/§11: even head/resource reads happen only after key validation."""
    j = Journal(tmp_path / "j.db")
    remember(j.db, "session", "h1/empty")
    filters = ["session", "h1/empty", None, True] if method == "relations" else ["session", "h1/empty", "desc", [], None, None]
    cursor = cursor_out(hashlib.sha256(dump(filters).encode()).hexdigest(), j.api_head(), key)
    reads = []
    j.db.set_trace_callback(reads.append)
    with pytest.raises(OperationError) as error:
        getattr(Observation(j), method)("session", "h1/empty", cursor=cursor)
    assert error.value.code == "INVALID_CURSOR" and error.value.status == 422
    assert reads == []
    j.db.set_trace_callback(None)
    j.close()


@pytest.mark.parametrize("key", ["x", [1], [True, "r"], [1, 2], [None, "r"], [1, None], None])
async def test_b01_b02_relation_cursor_key_contract_is_independent_of_rows(served, monkeypatch, capsys, key):
    """B01/B02, §10/§11: HTTP/MCP/CLI reject keys for empty, filtered and populated reads."""
    d, port = served
    viewer = token(d, "viewer", "observe")
    monkeypatch.setenv("BATC_API_TOKEN", viewer)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    remember(d.journal.db, "session", "h1/empty")
    empty = task(d.journal, "cursor-empty")
    empty_worktree = "wt_" + "0" * 32
    remember(d.journal.db, "worktree", empty_worktree)
    t = task(d.journal, "cursor-open")
    bind(d.journal, t, "cursor-session")
    closed = task(d.journal, "cursor-closed")
    bind(d.journal, closed, "closed-cursor-session")
    d.journal.change(closed["task_id"], "failed")
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True)

    async def check(path, tool, params, command):
        status, error = await http(port, "GET", path, tok=viewer)
        assert status == 422 and error["error"]["code"] == "INVALID_CURSOR", error
        with pytest.raises(mcp_server.ToolError, match="INVALID_CURSOR"):
            await server.call_tool(tool, params)
        assert await asyncio.to_thread(cli.main, ["--json", *command]) == 1
        output = capsys.readouterr()
        assert output.out == "" and "INVALID_CURSOR" in output.err

    try:
        cases = [
            ("session", "h1/empty", None, True),
            ("execution", empty["task_id"], None, True),
            ("worktree", empty_worktree, None, True),
            ("session", "h1/cursor-session", "other-execution", True),
            ("execution", closed["task_id"], None, False),
            ("session", "h1/cursor-session", None, True),
        ]
        for resource_type, resource_id, execution_id, include_closed in cases:
            params = {"resource_type": resource_type, "resource_id": resource_id,
                      "execution_id": execution_id, "include_closed": include_closed}
            baseline = d.inventory.observation.relations(**params)
            assert baseline["count"] == (1 if resource_id == "h1/cursor-session" and execution_id is None else 0)
            filters = [resource_type, resource_id, execution_id, include_closed]
            cursor = cursor_out(hashlib.sha256(dump(filters).encode()).hexdigest(), baseline["as_of"], key)
            params["cursor"] = cursor
            if resource_type == "session":
                host, sid = resource_id.split("/", 1)
                path, command = f"/api/v1/sessions/{host}/{sid}/relations", ["relations", "session", host, sid]
            elif resource_type == "execution":
                path, command = f"/api/v1/tasks/{resource_id}/sessions", ["relations", "execution", resource_id]
            else:
                path, command = f"/api/v1/worktrees/{resource_id}/relations", ["relations", "worktree", resource_id]
            query = f"?cursor={cursor}&include_closed={str(include_closed).lower()}"
            command += ["--cursor", cursor, "--include-closed", str(include_closed).lower()]
            if execution_id is not None:
                query += f"&execution_id={execution_id}"
                command += ["--execution-id", execution_id]
            await check(path + query, "resource_relations", params, command)

        # This identity has neither relations nor events, independently of the populated tasks.
        filters = ["session", "h1/empty", "desc", [], None, None]
        cursor = cursor_out(hashlib.sha256(dump(filters).encode()).hexdigest(), d.journal.api_head(), key)
        assert d.inventory.observation.history("session", "h1/empty")["events"] == []
        await check(f"/api/v1/sessions/h1/empty/history?cursor={cursor}", "resource_history",
                    {"resource_type": "session", "resource_id": "h1/empty", "cursor": cursor},
                    ["history", "session", "h1", "empty", "--cursor", cursor])
    finally:
        await fleet.close()


async def test_b03_observation_never_starts_resumes_rehydrates_or_locks_git(mock, tmp_path, monkeypatch):
    from bat_agent_connector import checkpoints, service
    from tests.test_checkpoints import LocalRunner, git, snapshot
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "dev@example.invalid")
    git(repo, "config", "user.name", "dev")
    (repo / "file.txt").write_text("one\n")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-qm", "one")
    (repo / "file.txt").write_text("two\n")
    before = snapshot(repo)
    j = Journal(tmp_path / "j.db")
    inv = Inventory(j, make_config(mock))
    def refuse(*args, **kw):
        raise AssertionError("observation crossed a write or lock boundary")
    for name in ("claim_warm", "reserve", "update"):
        monkeypatch.setattr(registry, name, refuse)
    mock.metas[MANUAL] = {"cwd": str(repo), "isStreaming": False}
    # Claude without cwd is unsafe for get-session-state; retain the existing Codex-safe path.
    mock.metas["sess-unload-0003"] = {"isStreaming": True}
    mock.handlers["git:status"] = refuse
    await inv.refresh_host("h1")
    state_ids = {f["params"]["sessionId"] for f in mock.invokes if f["channel"] == "claude:get-session-state"}
    assert "sess-unload-0003" not in state_ids
    assert write_frames(mock) == []
    assert not any(f["channel"] == "git:status" for f in mock.invokes)
    # The existing explicit on-demand source probe remains lock-free.
    runner = LocalRunner()
    await runner.run("h1", checkpoints.source_state_script(str(repo)))
    assert "git --no-optional-locks" in runner.scripts[0]
    assert snapshot(repo) == before and not list((repo / ".git").glob("*.lock"))
    async def refuse_async(*args, **kw):
        refuse()
    monkeypatch.setattr(service, "_host_sessions", refuse_async)
    monkeypatch.setattr(registry, "list_entries", refuse)
    changes = j.db.total_changes
    inv.session_document("h1", MANUAL)
    inv.observation.history("session", "h1/" + MANUAL)
    inv.observation.relations("session", "h1/" + MANUAL)
    assert j.db.total_changes == changes and snapshot(repo) == before
    await inv.close()
    j.close()


def test_b01_warm_binding_closes_reserved_intent_without_overwriting(tmp_path):
    j = Journal(tmp_path / "j.db")
    t = task(j, "a")
    cmd, _ = j.command(t["task_id"], "start_lead", "reserved", {"warm_session_id": "reused"}, "warm")
    # A cursor captured before the bind must still list the command's old pending relation.
    j.add_branch(t["task_id"], session_id="earlier", provider="codex", role="reviewer", reason="start")
    obs = Observation(j)
    baseline = obs.relations("execution", t["task_id"], limit=1)
    j.command_bind_session(cmd["command_id"], "reused")
    j.command_status(cmd["command_id"], "settled")
    obs = Observation(j)
    assert obs.relations("session", "h1/reserved")["relations"][0]["status"] == "closed"
    assert obs.relations("session", "h1/reused")["relations"][0]["status"] == "bound"
    original = obs.relations("session", "h1/reserved")["relations"][0]
    assert original["command_ids"] == [cmd["command_id"]]
    tail = obs.relations("execution", t["task_id"], limit=1, cursor=baseline["next_cursor"])
    assert tail["as_of"] == baseline["as_of"]
    assert baseline["relations"][0]["command_ids"] == [cmd["command_id"]]
    j.close()


def test_b03_migration_failure_rolls_back_and_restart_recovers(tmp_path, monkeypatch):
    from bat_agent_connector import observation
    path = tmp_path / "j.db"
    j = Journal(path)
    task(j, "a")
    before = j.api_head()
    j.db.execute("PRAGMA user_version=1")
    j.close()
    original = observation.backfill
    def fail(journal):
        journal.api_event("session", "h1/sid", "session.added", {})
        raise RuntimeError("migration interrupted")
    monkeypatch.setattr(observation, "backfill", fail)
    with pytest.raises(RuntimeError, match="migration interrupted"):
        Journal(path)
    monkeypatch.setattr(observation, "backfill", original)
    j = Journal(path)
    assert j.api_head() == before
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP
    assert j.db.execute("SELECT COUNT(*) FROM observation_resources WHERE resource_id='h1/sid'").fetchone()[0] == 0
    j.close()


def test_b03_version_one_journal_runs_observation_backfill_once(tmp_path, monkeypatch):
    """B03, §08/§11: data step 2 runs once; idempotent DDL runs independently on every open."""
    from bat_agent_connector import observation
    path = tmp_path / "j.db"
    with monkeypatch.context() as legacy:
        legacy.setattr(observation, "install", lambda journal: None)
        j = Journal(path)
    t = task(j, "legacy")
    bind(j, t, "sid")
    j.db.execute("""INSERT INTO sessions_observed(host,session_id,body,digest,provenance,api_access,first_seen_at,last_seen_at)
        VALUES('h1','saved','{}','legacy','unknown','read_only',123,124)""")
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == 1
    original = observation.backfill
    calls = []

    def capture(journal):
        assert journal.db.execute("PRAGMA user_version").fetchone()[0] == 1
        calls.append("backfill")
        original(journal)

    monkeypatch.setattr(observation, "backfill", capture)
    j.close()
    j = Journal(path)
    assert observation.MIGRATION_VERSION == 2
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP
    assert calls == ["backfill"]
    saved = Observation(j).history("session", "h1/saved")["events"]
    assert len(saved) == 1 and saved[0]["kind"] == "history.backfilled"
    assert Observation(j).relations("session", "h1/sid")["relations"]
    head = j.api_head()
    snapshot = [tuple(r) for r in j.db.execute("SELECT * FROM observation_backfill")]
    j.close()
    for _ in range(2):
        j = Journal(path)
        assert j.api_head() == head
        assert snapshot == [tuple(r) for r in j.db.execute("SELECT * FROM observation_backfill")]
        j.close()
    assert calls == ["backfill"]

    # DDL must still install on a version-2 journal without invoking its completed data step.
    already_path = tmp_path / "already.db"
    with monkeypatch.context() as legacy:
        legacy.setattr(observation, "install", lambda journal: None)
        j = Journal(already_path)
    task(j, "already")
    head = j.api_head()
    j.db.execute("PRAGMA user_version=2")
    j.close()
    j = Journal(already_path)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP
    assert j.api_head() == head and calls == ["backfill"]
    assert j.db.execute("SELECT COUNT(*) FROM observation_backfill").fetchone()[0] == 0
    assert j.db.execute("SELECT COUNT(*) FROM api_event_context").fetchone()[0] == 0
    j.close()

    fresh_path = tmp_path / "fresh.db"
    j = Journal(fresh_path)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP
    assert calls == ["backfill", "backfill"]
    assert j.api_head() == 0 and j.db.execute("SELECT COUNT(*) FROM observation_backfill").fetchone()[0] == 0
    j.close()
    j = Journal(fresh_path)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP
    assert j.api_head() == 0 and calls == ["backfill", "backfill"]
    assert j.db.execute("SELECT COUNT(*) FROM observation_backfill").fetchone()[0] == 0
    j.close()


async def test_b03_rpc_admin_identity_is_not_claimed_human(served, monkeypatch):
    from bat_agent_connector.task_daemon import request
    d, port = served
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    principal = api_auth.authenticate(d.journal.db, d._admin_token, d._admin_token)
    assert principal is not None and principal.admin
    # The real RPC caller owns this task; claimed human fields do not grant control.
    with controller_scope(d.journal, principal):
        t = task(d.journal, "a")
        bind(d.journal, t, "sid")
    await asyncio.to_thread(request, "work_pause", _auth_token=d._admin_token, task_id=t["task_id"], actor="ted", source_message_id="claimed-human-message")
    events = Observation(d.journal).history("execution", t["task_id"])["events"]
    paused = next(e for e in events if e["kind"] == "task.paused")
    assert paused["actor"] == "local-admin" and paused["context"]["actor"] == "local-admin"
    assert paused["context"]["entry_point"] == "rpc"
    assert paused["context"]["actor_evidence"]["source"] == "operations.actor"
    assert paused["context"]["operation_id"] == paused["context"]["actor_evidence"]["operation_id"]
    assert all(e["context"]["actor"] != "ted" for e in events)


async def test_b03_scheduled_task_effects_keep_each_operation_principal(tmp_path):
    from bat_agent_connector.observation import event_context, writer_context
    from bat_agent_connector.operations import ActionDef, OperationService
    from tests.operation_helpers import settle_operations

    journal = Journal(tmp_path / "principals.db")
    started = set()
    both_started = asyncio.Event()
    contexts = {}

    async def submit(ctx):
        started.add(ctx.actor)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 2)
        contexts[ctx.actor] = dict(writer_context.get())
        return ctx.effect("submit", lambda: task(journal, ctx.operation_id))

    ops = OperationService(journal, actions=[ActionDef("fixture.submit", "start", "Fixture", submit)])
    admitted = [(actor, entry, ops.create(api_auth.Principal(actor, frozenset({"start"})),
                    action="fixture.submit", idempotency_key=actor, entry=entry)[0])
                for actor, entry in [("fixture-one", "http"), ("fixture-two", "mcp")]]
    ambient = {"actor": "local-admin", "actor_basis": "authenticated_principal", "entry_point": "rpc",
               "operation_id": "unrelated-operation", "scopes": ["start"], "admin": True}
    try:
        with event_context(**ambient):
            # Observation context is not authority: an observe-only caller still cannot admit a write.
            with pytest.raises(OperationError) as denied:
                ops.create(api_auth.Principal("fixture-viewer", frozenset({"observe"})),
                           action="fixture.submit", idempotency_key="forbidden")
            assert denied.value.code == "FORBIDDEN"
            await settle_operations(ops)
            assert writer_context.get() == ambient
        assert writer_context.get() is None
        assert len(ops.list()["operations"]) == 2
        for actor, entry, admitted_op in admitted:
            op = ops.get(admitted_op["operation_id"])
            assert op["status"] == "succeeded" and op["actor"] == actor
            assert contexts[actor]["actor"] == actor and contexts[actor]["entry_point"] == entry
            assert contexts[actor]["operation_id"] == op["operation_id"]
            assert "scopes" not in contexts[actor] and "admin" not in contexts[actor]
            task_events = journal.api_events(resource_type="task", resource_id=op["result"]["task_id"])["events"]
            operation_events = journal.api_events(resource_type="operation", resource_id=op["operation_id"])["events"]
            assert task_events and operation_events
            assert all(event["actor"] == actor and event["context"]["actor"] == actor
                       for event in task_events + operation_events)
            assert all(event["context"]["operation_id"] == op["operation_id"]
                       for event in task_events + operation_events)
            assert all(event["context"]["operation_entry_point"] == entry
                       for event in task_events + operation_events)
    finally:
        await ops.drain()
        journal.close()


def test_b03_backfilled_occurrence_time_filters_are_not_migration_time(tmp_path):
    j = Journal(tmp_path / "j.db")
    with j.tx():
        seq = saved_fact(j, "legacy", "old", {"created_at": 123.5}, [("session", "h1/sid")])
    obs = Observation(j)
    assert [e["seq"] for e in obs.history("session", "h1/sid", since=123, until=124)["events"]] == [seq]
    assert obs.history("session", "h1/sid", since=124)["count"] == 0
    j.close()


@pytest.mark.parametrize("absent_context", [False, True])
def test_b03_unknown_occurrence_times_never_match_bounds_and_live_absence_falls_back(tmp_path, absent_context):
    """B03, §10/§11: missing/invalid saved dates stay unknown; only absent live metadata uses record time."""
    from bat_agent_connector.observation import iso

    j = Journal(tmp_path / "j.db")
    start = time.time() - 1
    with j.tx():
        missing = saved_fact(j, "legacy", "missing-date", {}, [("session", "h1/sid")])
        invalid = saved_fact(j, "legacy", "invalid-date", {"created_at": "not-a-date"}, [("session", "h1/sid")])
        valid = saved_fact(j, "legacy", "valid-date", {"created_at": 123.5}, [("session", "h1/sid")])
        live = j.api_event("session", "h1/sid", "session.updated", {"fields_stale": False})
        if absent_context:
            j.db.execute("DELETE FROM api_event_context WHERE seq=?", (live,))
        else:
            context = json.loads(j.db.execute("SELECT context FROM api_event_context WHERE seq=?", (live,)).fetchone()[0])
            context.pop("occurred_at_epoch")
            j.db.execute("UPDATE api_event_context SET context=? WHERE seq=?", (dump(context), live))
    end = time.time() + 1
    obs = Observation(j)
    unbounded = obs.history("session", "h1/sid", order="asc")
    assert [e["seq"] for e in unbounded["events"]] == [missing, invalid, valid, live]
    for event in unbounded["events"][:2]:
        assert event["context"]["occurred_at"] is None and event["context"]["occurred_at_epoch"] is None
    assert not unbounded["coverage"]["unknown_occurrence_times_excluded"]
    for bounds, expected in (({"since": start, "until": end}, [live]), ({"since": start}, [live]),
                             ({"until": end}, [valid, live]), ({"since": 123, "until": 124}, [valid])):
        page = obs.history("session", "h1/sid", order="asc", **bounds)
        assert [e["seq"] for e in page["events"]] == expected
        assert page["coverage"]["unknown_occurrence_times_excluded"] is True
        recorded = j.db.execute("SELECT MIN(created_at) FROM api_events").fetchone()[0]
        assert page["coverage"]["first_recorded_at"] == iso(recorded)
    j.close()


def test_b01_legacy_task_external_creation_keeps_shared_identity(mock, tmp_path):
    path = tmp_path / "j.db"
    j = Journal(path)
    t = task(j, "external")
    bind(j, t, "sid")
    j.db.execute("UPDATE tasks SET session_id='sid',external_worktree_path='/srv/ext',external_branch='batc/task-fixture' WHERE task_id=?", (t["task_id"],))
    j.db.execute("PRAGMA user_version=1")
    j.close()
    j = Journal(path)
    wid = worktree_id("h1", "task", t["task_id"], "external_worktree")
    assert Observation(j).resource("session", "h1/sid")["worktree_id"] == wid
    assert Observation(j).history("worktree", wid)["events"][0]["kind"] == "history.backfilled"
    assert all(e["kind"] != "history.backfilled" for e in j.api_events()["events"])
    j.close()
