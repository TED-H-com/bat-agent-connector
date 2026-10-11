"""Fixed engineering handoffs exercise real disk Git and mock BAT only."""
from __future__ import annotations

import base64
import copy
import json
import shlex
import sqlite3
import uuid
import zlib

import pytest

from bat_agent_connector import pm_package_handoff as handoff
from bat_agent_connector import pm_source_lineage as lineage
from bat_agent_connector import pm_work_packages, repository_sync, work_items
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import LocalRunner, bat_writes, git, snapshot
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_work_packages import create, read
from tests.test_pm_work_packages import pm as pm_fixture
from tests.test_pm_work_packages import ready as ready_fixture
from tests.test_pm_work_packages import world as world_fixture
from tests.test_repository_sync import REQUEST, TARGET

world = world_fixture
pm = pm_fixture
ready = ready_fixture


class HandoffRunner(LocalRunner):
    """Execute real disk Git scripts for both existing PM and dispatch protocols."""
    def __init__(self):
        super().__init__()
        self.before, self.after, self.phases = {}, {}, []

    async def run(self, host, script, timeout_s=None):
        raw = base64.b64decode(shlex.split(script)[-1])
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass  # Published repository scripts use the existing plain envelope.
        phase = json.loads(raw)["phase"]
        self.phases.append(phase)
        if phase in self.before:
            self.before.pop(phase)(phase)
        result = await super().run(host, script, timeout_s)
        if phase in self.after:
            self.after.pop(phase)(phase)
        return result


def dispatch_runner(w):
    w["runner"] = HandoffRunner()
    w["d"].ops.context["git_runner"] = w["runner"]


@pytest.fixture
async def fixed(ready):
    op = await create(ready)
    ready["package"] = read(ready, op)
    dispatch_runner(ready)
    return ready


async def request(w, **changes):
    ops, package = w["d"].ops, w["package"]
    preview = await repository_sync.preview(ops, OWNER, REQUEST)
    work = work_items._get_item(ops.db, w["target"]["work_item_id"])
    project = work_items._get_project(ops.db, work["project_id"])
    return {"action": "repository.continue", "target": TARGET,
        "params": {"source_ref": preview["source_ref"], "source_sha": preview["source_sha"],
            "prompt": "Implement only this selected engineering work.", "project_id": project["project_id"],
            "work_item_id": work["work_item_id"], "pm_package_ref": {"workspace_id": w["workspace_id"],
                "package_id": package["package_id"], "document_digest": package["document_digest"]},
            "pm_code_basis": {"mode": handoff.MODE, "rationale": "Use the explicitly published baseline for this implementation."},
            **changes}, "preconditions": {**preview["preconditions"], "expected_project_version": project["version"],
                "expected_work_item_fingerprint": work_items.fingerprint(work)}, "idempotency_key": uuid.uuid4().hex}


async def run(w, envelope):
    op, _ = w["d"].ops.create(OWNER, **envelope)
    await settle_operations(w["d"].ops)
    result = w["d"].ops.get(op["operation_id"])
    assert result["error_code"] != "INTERNAL", [(row["name"], row["error"]) for row in result["steps"] if row["error"]]
    return result


def revoke(w):
    w["d"].ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (w["workspace_id"], OWNER.actor))


async def test_complete_contract_is_the_original_prompt_and_replay_does_not_restart(fixed, mock):
    before = snapshot(fixed["human"])
    env = await request(fixed)
    op = await run(fixed, env)
    assert op["status"] == "succeeded", op
    saved = handoff.binding(op)
    assert saved["contract"]["package"] == fixed["package"]["document"]
    assert saved["contract_digest"] == handoff.digest(saved["contract"])
    assert saved["contract"]["code_basis"]["compatibility"] == "unverified"
    send = next(invocation["params"] for invocation in mock.invokes if invocation["channel"] == "claude:send-message")
    assert handoff.canonical(saved["contract"]) in send["prompt"]
    assert "Valid credentials allow access" in send["prompt"]
    assert json.dumps(fixed["package"]["document"]["sources"]["documents"][0]["content"], ensure_ascii=False) in send["prompt"]
    assert snapshot(fixed["human"]) == before
    assert git(op["result"]["worktree_path"], "rev-parse", "HEAD") == fixed["package"]["document"]["code_source"]["source_sha"]
    replay, created = fixed["d"].ops.create(OWNER, **env)
    assert not created and replay["operation_id"] == op["operation_id"]
    assert mock.channels().count("claude:start-session") == mock.channels().count("claude:send-message") == 1
    root = op["operation_id"]
    assert lineage.roots(fixed["d"].ops.db, "session", "h1/" + op["result"]["session_id"]) == {root}
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        fixed["d"].ops.db.execute("DELETE FROM pm_source_lineage")


async def test_explicit_distinct_code_and_spec_versions_are_not_false_compatibility(ready, mock):
    w = ready
    # Publish a code change in a separate synthetic checkout; preserve the human
    # working directory and the PM source carrier's fixed specification commit.
    code = w["human"].parent / "new-code"
    git(w["human"].parent, "clone", "-q", str(w["remote"]), str(code))
    git(code, "config", "user.name", "fixture")
    git(code, "config", "user.email", "fixture@example.invalid")
    (code / "implementation.txt").write_text("A different implementation starting point\n")
    git(code, "add", ".")
    git(code, "commit", "-qm", "New implementation base")
    git(code, "push", "-q", "origin", "main")
    w["state"]["sha"] = git(code, "rev-parse", "HEAD")
    preview = await repository_sync.preview(w["d"].ops, OWNER, REQUEST)
    w["params"]["code_source"] = {**preview["target"], **{key: preview[key]
        for key in ("source_ref", "source_sha", "repository_id", "binding_digest")}}
    package = await create(w)
    w["package"] = read(w, package)
    dispatch_runner(w)
    assert w["package"]["document"]["source_refs"][0]["commit_sha"] != w["state"]["sha"]
    readiness = pm_work_packages.readiness(w["d"].ops, OWNER, w["workspace_id"], w["package"]["package_id"])
    assert [check["check"] for check in readiness["checks"] if check["status"] != "passed"] == ["spec_code_alignment"]
    done = await run(w, await request(w))
    assert done["status"] == "succeeded", done
    assert handoff.binding(done)["contract"]["code_basis"]["compatibility"] == "unverified"
    assert done["result"]["source_sha"] == w["state"]["sha"]


@pytest.mark.parametrize("change,code", [("digest", "PM_PACKAGE_VERSION_CONFLICT"),
    ("basis", "INVALID_PARAMS"), ("work", "PM_PACKAGE_NOT_READY"), ("large", "PM_HANDOFF_TOO_LARGE")])
async def test_no_effect_for_changed_or_incomplete_contract(fixed, mock, change, code):
    env = await request(fixed)
    if change == "digest":
        env["params"]["pm_package_ref"]["document_digest"] = "f" * 64
    elif change == "basis":
        env["params"].pop("pm_code_basis")
    elif change == "work":
        fixed["d"].ops.db.execute("UPDATE work_items SET title='Changed requirement' WHERE work_item_id=?", (fixed["target"]["work_item_id"],))
        work = work_items._get_item(fixed["d"].ops.db, fixed["target"]["work_item_id"])
        env["preconditions"]["expected_work_item_fingerprint"] = work_items.fingerprint(work)
        code = "PM_PACKAGE_TARGET_CHANGED"
    else:
        env["params"]["prompt"] = "界" * 12000
    with pytest.raises(OperationError) as caught:
        fixed["d"].ops.create(OWNER, **env)
    assert caught.value.code == code
    assert not bat_writes(mock)


@pytest.mark.parametrize("phase", ["fetch", "prepare"])
async def test_withdrawn_source_prevents_next_unsent_step(fixed, mock, phase):
    fixed["runner"].after[phase] = lambda _: revoke(fixed)
    done = await run(fixed, await request(fixed))
    assert done["status"] == "failed" and done["error_code"] == "PM_HANDOFF_SOURCE_UNAVAILABLE", done
    assert not bat_writes(mock)


@pytest.mark.parametrize("channel", ["claude:start-session", "claude:send-message"])
async def test_sent_reply_loss_reconciles_original_after_revocation_without_resend(fixed, mock, monkeypatch, channel):
    client = fixed["d"].fleet.client("h1")
    invoke, dropped = client.invoke, False
    async def lose(name, *args, **kwargs):
        nonlocal dropped
        result = await invoke(name, *args, **kwargs)
        if name == channel and not dropped:
            dropped = True
            revoke(fixed)
            raise InvokeTimeout("Original synthetic response was lost")
        return result
    monkeypatch.setattr(client, "invoke", lose)
    env = await request(fixed)
    original = await run(fixed, env)
    assert original["status"] == "uncertain", original
    ops = fixed["d"].ops
    ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (original["operation_id"],))
    await settle_operations(ops)
    done = ops.get(original["operation_id"])
    assert done["status"] == ("succeeded" if channel == "claude:send-message" else "failed"), done
    assert mock.channels().count(channel) == 1
    if channel == "claude:start-session":
        assert mock.channels().count("claude:send-message") == 0
    with pytest.raises(OperationError, match="sources|operation not found"):
        ops.create(OWNER, **env)
    assert not ops.list(principal=OWNER, action="repository.continue")["operations"]


async def test_frame_source_guard_runs_after_async_frame_preparation(fixed, mock, monkeypatch):
    client = fixed["d"].fleet.client("h1")
    invoke = client.invoke
    async def withdraw(name, *args, **kwargs):
        if name == "claude:send-message":
            frame = kwargs.get("before_frame")
            async def before_frame():
                if frame:
                    await frame()
                revoke(fixed)
            kwargs["before_frame"] = before_frame
        return await invoke(name, *args, **kwargs)
    monkeypatch.setattr(client, "invoke", withdraw)
    done = await run(fixed, await request(fixed))
    assert done["status"] == "failed" and done["error_code"] == "PM_HANDOFF_SOURCE_UNAVAILABLE", done
    assert mock.channels().count("claude:start-session") == 1
    assert "claude:send-message" not in mock.channels()


def test_bound_prompt_never_mutates_or_truncates_source_document():
    contract = {"requirements": [{"text": "Requirement", "status": "agent_proposal"}], "unknown": "Unresolved"}
    before = copy.deepcopy(contract)
    assert json.loads(handoff.canonical(contract)) == before
    assert '"status":"agent_proposal"' in handoff.prompt("Implement", contract)
    with pytest.raises(OperationError) as caught:
        handoff.prompt("Implement", {"requirements": ["Complete condition" * 2500]})
    assert caught.value.code == "PM_HANDOFF_TOO_LARGE" and contract == before


@pytest.mark.parametrize("channel", ["claude:start-session", "claude:send-message"])
async def test_admitted_frame_may_finish_after_pause_but_next_step_cannot(fixed, mock, monkeypatch, channel):
    from tests.test_pm_lifecycle import decide
    client = fixed["d"].fleet.client("h1")
    invoke, paused = client.invoke, False
    async def stop_after_frame_preparation(name, *args, **kwargs):
        nonlocal paused
        if name == channel and not paused:
            original = kwargs.get("before_frame")
            async def before_frame():
                nonlocal paused
                if original:
                    await original()
                paused = True
                await decide(fixed["d"].ops, fixed["workspace_id"], who=OWNER)
            kwargs["before_frame"] = before_frame
        return await invoke(name, *args, **kwargs)
    monkeypatch.setattr(client, "invoke", stop_after_frame_preparation)
    done = await run(fixed, await request(fixed))
    assert paused and mock.channels().count(channel) == 1
    if channel == "claude:start-session":
        assert done["status"] == "failed" and done["error_code"] == "PM_LIFECYCLE_STOPPED", done
        assert "claude:send-message" not in mock.channels()
    else:
        assert done["status"] == "succeeded", done


async def test_reopen_does_not_dispatch_a_queued_derived_intent(fixed, mock, monkeypatch):
    from bat_agent_connector.api_auth import Principal
    from tests.test_pm_lifecycle import decide
    done = await run(fixed, await request(fixed))
    assert done["status"] == "succeeded", done
    sid, ops = done["result"]["session_id"], fixed["d"].ops
    mock.metas[sid]["isStreaming"] = False
    await fixed["d"].inventory.refresh_host("h1")
    controller = Principal(OWNER.actor, OWNER.scopes | {"operate"})
    # Hold only this test-owned queue while creating the pre-stop intent.
    monkeypatch.setattr(ops, "kick", lambda: None)
    queued, _ = ops.create(controller, action="session.send", target={"host": "h1", "session_id": sid},
        params={"text": "Continue only the original contract."}, idempotency_key="derived-before-stop")
    await decide(ops, fixed["workspace_id"], who=OWNER)
    with pytest.raises(OperationError) as caught:
        ops.create(controller, action="session.send", target={"host": "h1", "session_id": sid},
            params={"text": "A new stopped intent."}, idempotency_key="derived-during-stop")
    assert caught.value.code == "PM_LIFECYCLE_STOPPED"
    await decide(ops, fixed["workspace_id"], "reopen", who=OWNER, params={"stage": "development"})
    await ops._execute(queued["operation_id"])
    old = ops.get(queued["operation_id"])
    assert old["status"] == "failed" and old["error_code"] == "PM_LIFECYCLE_EPOCH_CHANGED", old
    assert mock.channels().count("claude:send-message") == 1


@pytest.mark.parametrize("withdraw", [False, True])
async def test_local_derived_work_edit_rechecks_sources_and_epoch(fixed, monkeypatch, withdraw):
    from tests.test_pm_lifecycle import decide
    done = await run(fixed, await request(fixed))
    assert done["status"] == "succeeded", done
    ops, wid = fixed["d"].ops, fixed["target"]["work_item_id"]
    before = work_items._get_item(ops.db, wid)
    monkeypatch.setattr(ops, "kick", lambda: None)
    op, _ = ops.create(OWNER, action="work_item.update", target={"work_item_id": wid},
        params={"title": "Queued engineering edit"}, preconditions={"expected_version": before["version"]},
        idempotency_key="edit-before-stop")
    if withdraw:
        revoke(fixed)
    else:
        await decide(ops, fixed["workspace_id"], who=OWNER)
    await ops._execute(op["operation_id"])
    result = ops.get(op["operation_id"])
    expected = "PM_HANDOFF_SOURCE_UNAVAILABLE" if withdraw else "PM_LIFECYCLE_STOPPED"
    assert result["status"] == "failed" and result["error_code"] == expected, result
    assert work_items._get_item(ops.db, wid) == before


async def test_unrelated_legacy_start_and_prose_remain_outside_pm_lineage(fixed, mock):
    from tests.test_pm_lifecycle import decide
    from tests.test_repository_sync import STARTER, envelope
    done = await run(fixed, await request(fixed))
    assert done["status"] == "succeeded", done
    await decide(fixed["d"].ops, fixed["workspace_id"], who=OWNER)
    pv = await repository_sync.preview(fixed["d"].ops, OWNER, REQUEST)
    env = envelope(pv, key="ordinary-existing-contract", prompt=done["operation_id"])
    ops = fixed["d"].ops
    ordinary, _ = ops.create(STARTER, **env)
    await settle_operations(ops)
    ordinary = ops.get(ordinary["operation_id"])
    assert ordinary["status"] == "succeeded", ordinary
    assert not lineage.roots(ops.db, "operation", ordinary["operation_id"])
    assert not lineage.document_roots(ops.db, {"title": done["operation_id"], "prompt": done["operation_id"],
        "path": done["result"]["worktree_path"], "name": fixed["target"]["work_item_id"]})
    assert lineage.document_roots(ops.db, {"operation_id": done["operation_id"]}) == {done["operation_id"]}
    assert mock.channels().count("claude:start-session") == 2
    frozen = {"version": 1, "snapshot": {"projection": {"work": [{"work_item_id": fixed["target"]["work_item_id"]}]}}}
    saved = copy.deepcopy(frozen)
    revoke(fixed)
    with pytest.raises(OperationError) as caught:
        lineage.read_response(ops.db, OWNER, frozen)
    assert caught.value.code == "NOT_FOUND" and frozen == saved
