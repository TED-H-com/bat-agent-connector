"""Real admission fixtures: unchanged envelopes, legacy selections and holds."""
from __future__ import annotations

import copy
import json

import pytest

from bat_agent_connector import pm_attempt_results, work_items
from bat_agent_connector.api_auth import Principal
from tests import test_pm_attempt_results as results
from tests import test_pm_work_adopt_and_start as adoption
from tests import test_pm_work_batches as batches
from tests import test_pm_work_start as starting
from tests.test_pm_work_packages import action
from tests.test_pm_workspace_wip import install, read, request, save

world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready
fixed = batches.fixed
batch_ready = batches.batch_ready
proposal_world = adoption.proposal_world
selected = adoption.selected


def envelope(ops, op):
    value = {key: copy.deepcopy(op[key]) for key in
             ("action", "target", "params", "preconditions", "idempotency_key")}
    value["request_hash"] = ops.db.execute("SELECT request_hash FROM operations WHERE operation_id=?",
                                          (op["operation_id"],)).fetchone()[0]
    return value


async def test_prechild_work_start_and_unchanged_envelope(start_ready):
    ops = start_ready["d"].ops
    install(ops)
    env = await starting._start_envelope(start_ready)
    parent, _ = ops.create(starting.STARTER_OWNER, **env)
    original = envelope(ops, parent)
    value = read(ops, start_ready["target"], starting.STARTER_OWNER)
    assert value["used"] == 1, value
    assert value["breakdown"]["accepted_selection"] == 1
    ops.cancel(starting.STARTER_OWNER, parent["operation_id"])
    assert read(ops, start_ready["target"], starting.STARTER_OWNER)["used"] == 0
    await save(ops, request(start_ready["workspace_id"], limit=1), starting.STARTER_OWNER)
    replay, fresh = ops.create(starting.STARTER_OWNER, **env)
    assert not fresh and envelope(ops, replay) == original
    later = await starting._start_envelope(start_ready, key="wip-second-attempt")
    next_attempt, fresh = ops.create(starting.STARTER_OWNER, **later)
    assert fresh and next_attempt["operation_id"] != parent["operation_id"]
    assert ops.db.execute("SELECT COUNT(*) FROM work_attempts WHERE workspace_id=?", (start_ready["workspace_id"],)).fetchone()[0] == 2
    assert read(ops, start_ready["target"], starting.STARTER_OWNER)["used"] == 1


async def test_legacy_batch_prechild_selection_and_direct_handoff_union(batch_ready):
    w = batch_ready
    ops = w["d"].ops
    second = await batches.extra(w, "Second selected commitment")
    env = batches.envelope(w, [await batches.selected(w, "first"), await batches.selected(second, "second")], max_parallel=1)
    parent, _ = ops.create(batches.OWNER, **env)
    original = envelope(ops, parent)
    # Acceptance predates installation of the policy module and any child row.
    install(ops)
    value = read(ops, w["target"], batches.OWNER)
    assert value["used"] == 2 and value["breakdown"]["accepted_selection"] == 2, value
    direct_env = await batches.request(w)
    direct, _ = ops.create(batches.OWNER, **direct_env)
    direct_original = envelope(ops, direct)
    value = read(ops, w["target"], batches.OWNER)
    assert value["used"] == 2 and value["breakdown"]["unresolved_execution"] == 1, value
    ops.cancel(batches.OWNER, parent["operation_id"])
    ops.cancel(batches.OWNER, direct["operation_id"])
    await save(ops, request(w["workspace_id"], limit=1), batches.OWNER)
    for old, selected_env in ((original, env), (direct_original, direct_env)):
        replay, fresh = ops.create(batches.OWNER, **selected_env)
        assert not fresh and envelope(ops, replay) == old
    # Advisory policy cannot reject a new two-Work selection above its limit.
    above, fresh = ops.create(batches.OWNER, **{**env, "idempotency_key": "wip-above-advisory-limit"})
    assert fresh and above["status"] == "accepted"
    value = read(ops, w["target"], batches.OWNER)
    assert value["used"] == 2 and value["over_limit"] is True


async def test_adopt_prechild_selection_and_unchanged_envelope(selected):
    ops, env = selected["d"].ops, selected["compound_request"]
    parent, _ = ops.create(adoption.OWNER, **env)
    original = envelope(ops, parent)
    install(ops)
    value = read(ops, selected["target"], adoption.OWNER)
    assert value["used"] == 1 and value["breakdown"]["accepted_selection"] == 1, value
    ops.cancel(adoption.OWNER, parent["operation_id"])
    assert read(ops, selected["target"], adoption.OWNER)["used"] == 0
    await save(ops, request(selected["workspace_id"]), adoption.OWNER)
    replay, fresh = ops.create(adoption.OWNER, **env)
    assert not fresh and envelope(ops, replay) == original


@pytest.mark.parametrize("proof", ["none", "matching", "wrong_generation", "wrong_marker", "queued_input"])
async def test_release_requires_closure_and_original_turn_evidence(start_ready, mock, proof):
    w = start_ready
    ops = w["d"].ops
    install(ops)
    state = await results._start_managed_attempt(w, mock)
    target = w["target"]
    value = read(ops, target, starting.STARTER_OWNER)
    assert value["used"] == 1, value
    await w["d"].inventory.refresh_host("h1")
    # No transcript/runtime probe occurs during the WIP read.
    before = len(mock.channels())
    item = work_items._get_item(ops.db, target["work_item_id"])
    await action(w, "work_item.update", {"work_item_id": target["work_item_id"]}, {"state": "done"},
                 {"expected_version": item["version"]})
    item = work_items._get_item(ops.db, target["work_item_id"])
    await action(w, "work_item.approve", {"work_item_id": target["work_item_id"]}, {"note": "Reviewed synthetic result"},
                 {"expected_fingerprint": work_items.fingerprint(item)},
                 who=Principal(starting.STARTER_OWNER.actor, starting.STARTER_OWNER.scopes | {"approve"}))
    assert read(ops, target, starting.STARTER_OWNER)["used"] == 1
    if proof != "none":
        aid = state["attempt_id"]
        handoff = ops.get(state["handoff_operation_id"])
        sid, marker = state["session_id"], state["turn_marker"]
        from bat_agent_connector import registry
        origin = registry.get("h1", sid)
        generation = pm_attempt_results.source_generation_from_registry("h1", sid, origin)
        ops.db.execute("UPDATE sessions_observed SET body=json_set(body,'$.registry_origin',json(?)) WHERE host=? AND session_id=?",
            (json.dumps({key: origin[key] for key in ("controller_incarnation", "created_at", "start_operation_id")}), "h1", sid))
        if proof == "wrong_generation":
            generation = "different:1"
        if proof == "wrong_marker":
            marker = "another-turn"
        pm_attempt_results.record_receipt(ops.journal, workspace_id=w["workspace_id"], attempt_id=aid,
            source_key=f"bat:h1:{sid}", source_generation=generation, turn_marker=marker, last_message_id="fixture-last",
            digest="sha256:" + "a" * 64, document={"turn_marker": marker, "boundary": "turn_done", "turn_attribution": "correlated"},
            observed_at=1, operation_id=handoff["operation_id"])
        if proof == "queued_input":
            # Real original acceptance, before the worker can create a send fence.
            queued, fresh = ops.create(starting.STARTER_OWNER, action="session.send", target=target,
                params={"text": "Synthetic later instruction", "schedule": "after_current"},
                preconditions={}, idempotency_key="wip-later-input")
            assert fresh and queued["status"] == "accepted"
            assert ops.db.execute("SELECT 1 FROM work_inputs WHERE operation_id=?", (queued["operation_id"],)).fetchone()
        value = read(ops, target, starting.STARTER_OWNER)
        assert value["used"] == (0 if proof == "matching" else 1), value
        if proof == "matching":
            item = work_items._get_item(ops.db, target["work_item_id"])
            await action(w, "work_item.continue", {"work_item_id": target["work_item_id"]}, {"note": "Reopen synthetic scope"},
                {"expected_fingerprint": work_items.fingerprint(item)},
                who=Principal(starting.STARTER_OWNER.actor, starting.STARTER_OWNER.scopes | {"approve"}))
            # Saved terminal evidence alone cannot release an open management commitment.
            value = read(ops, target, starting.STARTER_OWNER)
            assert value["used"] == value["breakdown"]["management_wip"] == 1
    assert len(mock.channels()) == before
