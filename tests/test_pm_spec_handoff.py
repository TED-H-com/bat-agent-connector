"""Large fixed specification handoff uses ordinary verified artifact transport."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from bat_agent_connector import pm_package_handoff, pm_source_lineage
from bat_agent_connector.operations import OperationError
from tests.operation_helpers import settle_operations
from tests.test_artifacts import LocalArtifactHost
from tests.test_checkpoints import bat_writes, snapshot
from tests.test_pm_package_handoff import request as handoff_request
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_spec_materialization import fixed as fixed_fixture
from tests.test_pm_spec_materialization import materialize, record
from tests.test_pm_spec_materialization import pm as pm_fixture
from tests.test_pm_spec_materialization import ready as ready_fixture
from tests.test_pm_spec_materialization import world as world_fixture
from tests.test_pm_work_packages import create, read

world = world_fixture
pm = pm_fixture
ready = ready_fixture
fixed = fixed_fixture


@pytest.fixture
async def large(fixed):
    w = fixed
    design = {**w["params"]["design"], "text": "A complete fixed design paragraph. " * 2400}
    w["package"] = read(w, await create(w, previous=w["package"]["package_id"], design=design))
    w["spec"] = record(w, await materialize(w))
    w["d"].ops.context["artifact_host"] = LocalArtifactHost()
    return w


async def envelope(w):
    value = w["spec"]
    return await handoff_request(w, pm_spec_ref={key: value[key] for key in ("workspace_id", "materialization_id", "document_digest")},
                                 artifacts=[value["artifact_ref"]])


async def dispatch(w, env):
    op, _ = w["d"].ops.create(OWNER, **env)
    await settle_operations(w["d"].ops)
    return w["d"].ops.get(op["operation_id"])


async def test_full_large_package_is_transferred_unchanged_and_prompt_is_bounded(large, mock):
    w, ops = large, large["d"].ops
    human = snapshot(w["human"])
    with pytest.raises(OperationError) as error:
        ops.create(OWNER, **(await handoff_request(w)))
    assert error.value.code == "PM_HANDOFF_TOO_LARGE"
    env = await envelope(w)
    result = await dispatch(w, env)
    assert result["status"] == "succeeded", result
    binding = pm_package_handoff.binding(result)
    assert binding["contract"]["package"] == w["package"]["document"]
    assert len(pm_package_handoff.canonical(binding["contract"]).encode()) > 64_000
    transferred = Path(result["result"]["worktree_path"]) / binding["contract"]["spec_materialization"]["input_path"]
    assert transferred.read_bytes() == ops.context["artifact_store"].read_content(w["spec"]["artifact_ref"]["artifact_id"], 1)
    sends = [row["params"]["prompt"] for row in mock.invokes if row["channel"] == "claude:send-message"]
    assert len(sends) == 1 and len(sends[0]) < 10_000
    assert str(transferred.relative_to(result["result"]["worktree_path"])) in sends[0]
    assert w["spec"]["document_digest"] in sends[0]
    assert "A complete fixed design paragraph." not in sends[0]
    assert pm_source_lineage.roots(ops.db, "operation", result["operation_id"]) == {result["operation_id"], w["spec"]["operation_id"]}
    before = len(bat_writes(mock))
    replay, created = ops.create(OWNER, **env)
    assert not created and replay["operation_id"] == result["operation_id"] and len(bat_writes(mock)) == before
    assert snapshot(w["human"]) == human


@pytest.mark.parametrize("change", ["digest", "missing_artifact", "other_artifact", "package", "artifact_bytes"])
async def test_exact_artifact_package_and_digest_required_before_dispatch(large, mock, change):
    w, ops = large, large["d"].ops
    env = await envelope(w)
    if change == "digest":
        env["params"]["pm_spec_ref"]["document_digest"] = "a" * 64
    elif change == "missing_artifact":
        env["params"].pop("artifacts")
    elif change == "other_artifact":
        env["params"]["artifacts"][0] = {**w["spec"]["artifact_ref"], "digest": "a" * 64}
    elif change == "package":
        env["params"]["pm_package_ref"]["package_id"] = w["package"]["previous_package_id"]
    else:
        path = ops.context["artifact_store"].content_path(w["spec"]["artifact_ref"]["artifact_id"], 1)
        path.chmod(0o600)
        path.write_bytes(b"Changed immutable source")
    with pytest.raises(OperationError):
        ops.create(OWNER, **env)
    assert not bat_writes(mock)


@pytest.mark.parametrize("boundary", ["claude:start-session", "claude:send-message"])
async def test_actual_final_bat_frame_rehashes_transferred_spec(large, mock, monkeypatch, boundary):
    w, ops = large, large["d"].ops
    env = await envelope(w)
    op, _ = ops.create(OWNER, **env)
    client = w["d"].fleet.client("h1")
    invoke = client._invoke_checked
    async def race(channel, params, *args, **kwargs):
        if channel == boundary:
            original = ops.get(op["operation_id"], steps=False)
            bound = pm_package_handoff.binding(original)
            path = Path(original["external_refs"]["worktree_path"]) / bound["contract"]["spec_materialization"]["input_path"]
            path.chmod(0o600)
            path.write_bytes(b"Changed after the earlier input check")
        return await invoke(channel, params, *args, **kwargs)
    monkeypatch.setattr(client, "_invoke_checked", race)
    await settle_operations(ops)
    done = ops.get(op["operation_id"])
    assert done["status"] in {"failed", "needs_attention"}, done
    assert boundary not in mock.channels()
    assert not [row for row in mock.invokes if row["channel"] == "claude:send-message"]


async def test_removed_original_package_source_hides_spec_artifact_and_execution(large, mock):
    w, ops = large, large["d"].ops
    result = await dispatch(w, await envelope(w))
    assert result["status"] == "succeeded", result
    # Revoke the original PM source association, not a mutable prompt path.
    original = copy.deepcopy(w["plan"])
    ops.db.execute("DROP TRIGGER pm_workspace_worktrees_update_immutable")
    ops.db.execute("UPDATE pm_workspace_worktrees SET binding='{}' WHERE workspace_id=?", (w["workspace_id"],))
    assert original
    assert not pm_source_lineage.visible(ops.db, OWNER, "operation", result["operation_id"])
    assert not pm_source_lineage.visible(ops.db, OWNER, "artifact", w["spec"]["artifact_ref"]["artifact_id"])
    assert not pm_source_lineage.visible(ops.db, OWNER, "session", "h1/" + result["result"]["session_id"])
