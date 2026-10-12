"""Fixed Follow-up sources with real journal recovery and a fake native process."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid

import pytest

from bat_agent_connector import (
    pm_access,
    pm_discussions,
    pm_follow_up,
    pm_native_journal,
    pm_native_operations,
    pm_profiles,
    pm_runs,
    pm_sprints,
    pm_workspace,
)
from bat_agent_connector import (
    pm_follow_up_analysis as analysis,
)
from bat_agent_connector import (
    pm_follow_up_format as format_,
)
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests import test_pm_native_operations as native_fixture
from tests.test_pm_discussions import OUTSIDER, READER
from tests.test_pm_follow_up import ops as base_ops_fixture
from tests.test_pm_follow_up import prepare, prepared
from tests.test_pm_follow_up import request as intake_request
from tests.test_pm_follow_up import snapshot_ops as snapshot_ops_fixture
from tests.test_pm_governance import execution_state
from tests.test_pm_governance import prepared as private_prepared
from tests.test_pm_native_operations import OWNER, FakeRuntime, await_runtime, finish, retry, run, writer
from tests.test_pm_native_runtime import MODEL, TURN, protocol

snapshot_ops = snapshot_ops_fixture
base_ops = base_ops_fixture


def envelope():
    return {"version": 1, "summary": {"text": "登入與忘記密碼仍需對照驗證；推薦折扣未採用。", "source_refs": ["knowledge.scope"]},
        "findings": [{"kind": "assumption", "text": "付費方案仍有假設。", "impact": "先確認近期接受條件。", "source_refs": ["knowledge.finance"]}],
        "tradeoffs": [], "questions": [], "recommendations": [{"text": "比較已知接受條件與驗證缺口。", "proposed_owner": None,
                                                              "source_refs": ["knowledge.quality"]}]}


@pytest.fixture
def world(base_ops, monkeypatch):
    ops = base_ops
    version = ops.db.execute("PRAGMA user_version").fetchone()[0]
    pm_native_operations.install(ops)
    analysis.install(ops)
    analysis.install(ops)
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == version
    settings, instances = {"raw_reply": json.dumps(envelope(), ensure_ascii=False)}, []

    def reply_protocol(prompt, client_id):
        values = protocol(prompt, client_id)
        values[2]["params"]["item"]["text"] = settings["raw_reply"]
        values[-1]["params"]["turn"]["items"][-1]["text"] = settings["raw_reply"]
        return values

    monkeypatch.setattr(native_fixture, "protocol", reply_protocol)
    ops.context["pm_native_runtime_factory"] = lambda *args, **kwargs: FakeRuntime(*args, **kwargs, settings=settings, instances=instances)
    ops.context["pm_native_process_inspector"] = lambda identity: {
        "state": "unknown", "process": identity, "proof": "identity_unavailable", "observed_at": 1.0}
    yield ops, settings, instances
    assert all(task.done() for task in ops._active.values())


async def setup(ops, *, private=False):
    data = await private_prepared(ops, evidence=True) if private else await prepared(ops)
    intake = await prepare(ops, data, thread_id=data["target"]["thread_id"])
    data["intake"] = pm_follow_up.get_intake(ops, OWNER, data["wid"], intake["intake_id"])["intake"]
    return data


def request(ops, data, *, who=OWNER, params=None, pre=None, key=None):
    return {"action": analysis.ACTION, "target": data["target"],
        "params": {"intake_id": data["intake"]["intake_id"], "purpose": "Compare the fixed evidence before asking important questions", **(params or {})},
        "preconditions": {"expected_version": pm_discussions._thread(ops.db, data["wid"], data["target"]["thread_id"])["version"],
                          "expected_intake_digest": data["intake"]["document_digest"], **(pre or {})},
        "idempotency_key": key or uuid.uuid4().hex}


def create(ops, data, *, who=OWNER, **kwargs):
    return ops.create(who, **request(ops, data, **kwargs))[0]


def read(ops, op, who=OWNER):
    return analysis.get_assessment(ops, who, op["target"]["workspace_id"], op["result"]["outcome"]["assessment_id"])["assessment"]


async def execute_one(ops, op):
    # Drive an independent operation without waiting for the deliberately
    # blocked native writer. This is the same registered worker entry point.
    await ops._execute(op["operation_id"])
    return ops.get(op["operation_id"])


def withdraw(ops, data):
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (data["task"]["task_id"],))


@pytest.mark.parametrize("summary", [False, True])
@pytest.mark.parametrize("adapter", [None, pm_native_journal.ADAPTER])
async def test_fixed_intake_imports_one_assessment_raw_proof_and_no_chat_or_adoption(world, summary, adapter):
    ops, settings, instances = world
    data = await setup(ops)
    settings["summary_terminal"] = summary
    before = execution_state(ops)
    thread_before = pm_discussions.get_thread(ops, OWNER, **data["target"])
    original = request(ops, data)
    if adapter is not None:
        original['params']['adapter'] = adapter
    op = await finish(ops, ops.create(OWNER, **original)[0])
    assert op["status"] == "succeeded", op
    result = read(ops, op)
    assert result["document"] == envelope() and result["raw_reply"] == settings["raw_reply"]
    assert result["validation"] == {"status": "format_valid", "errors": [], "conclusions_verified": False}
    assert result["assessment_basis"] == "unadopted_agent_analysis" and not result["accepted_plan_changed"]
    proof = result["provenance"]
    assert proof["native_turn_id"] == TURN and proof["actual_model"] == MODEL and proof["completeness"] == "complete"
    assert proof["intake_digest"] == data["intake"]["document_digest"]
    assert proof["native_event_seqs"] == (list(range(1, 8)) if summary else [4])
    saved = run(ops, op)
    assert saved["requester"] == OWNER.actor and saved["execution_authority"]["actor"] == "pm:" + data["wid"]
    assert saved["context"]["intake"] == data["intake"]["document"]
    assert saved["context"]["profile"]["mode"] == "follow_up" and not saved["context"]["profile"]["executable"]
    assert pm_native_journal.events(ops.db, saved["run_id"]) == instances[0].rows
    assert len(instances) == 1 and instances[0].sends == 1 and instances[0].closed and writer(ops, data["target"]) is None
    assert pm_discussions.get_thread(ops, OWNER, **data["target"])["messages"] == thread_before["messages"]
    assert pm_discussions._thread(ops.db, data["wid"], data["target"]["thread_id"]) == thread_before["thread"]
    assert execution_state(ops) == before
    assert ops.create(OWNER, **original)[0]["operation_id"] == op["operation_id"]
    with pytest.raises(OperationError, match="PM_OUTPUT_DESTINATION_REFUSED"):
        pm_native_journal.import_reply(ops.journal, saved["run_id"], saved["generation"])
    with pytest.raises(OperationError, match="PM_OUTPUT_DESTINATION_REFUSED"):
        pm_runs.import_reply(ops.journal, saved["run_id"], saved["generation"], content=result["raw_reply"],
                             native_turn_id=TURN, sdk_session_id="not-a-BAT-session")
    for verb in ("UPDATE pm_follow_up_assessments SET raw_reply='replacement'", "DELETE FROM pm_follow_up_assessments"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            ops.db.execute(verb)


async def test_admitted_context_does_not_follow_later_intake_profile_or_thread_notes(world, monkeypatch):
    ops, settings, instances = world
    data = await setup(ops)
    settings["block_preflight"] = True
    op = create(ops, data)
    original_profile = pm_profiles.get
    def changed_profile(mode):
        result = original_profile(mode)
        return {**result, "purpose": "LATER PROFILE MUST NOT REPLACE ADMISSION"} if mode == "follow_up" else result
    monkeypatch.setattr(pm_profiles, "get", changed_profile)
    runtime = await await_runtime(ops, instances, op)
    original = copy.deepcopy(run(ops, op)["context"])
    assert "LATER PROFILE" not in json.dumps(original)
    note = ops.create(OWNER, action="pm.message.append", target=data["target"], params={"content": "LATER PRIVATE THREAD WORDS"},
        preconditions={"expected_version": original["thread_version"]}, idempotency_key=uuid.uuid4().hex)[0]
    assert (await execute_one(ops, note))["status"] == "succeeded"
    later = ops.create(OWNER, **intake_request(ops, data, thread_id=data["target"]["thread_id"]))[0]
    assert (await execute_one(ops, later))["status"] == "succeeded"
    monkeypatch.setattr(format_, "prompt", lambda _: "REPLACEMENT PROMPT MUST NOT APPEAR")
    runtime.release.set()
    done = await finish(ops, op)
    assert done["status"] == "succeeded", done
    saved = run(ops, done)
    assert saved["context"] == original
    launch = pm_native_journal.lifecycle(ops.db, saved["run_id"])["launch_intent"]
    assert "LATER PRIVATE THREAD WORDS" not in launch["prompt"] and "REPLACEMENT PROMPT" not in launch["prompt"]
    assert read(ops, done)["intake_id"] == data["intake"]["intake_id"]


@pytest.mark.parametrize("changes", [{"params": {"auth_file": "/fixture/auth"}}, {"params": {"tools": ["shell"]}},
    {"params": {"max_chars": True}}, {"params": {"timeout_s": 0}}, {"pre": {"expected_intake_digest": "0" * 64}},
    {"pre": {"expected_version": 999}}])
async def test_admission_refuses_changed_or_unbounded_intent_before_launch(world, changes):
    ops, _, instances = world
    data = await setup(ops)
    with pytest.raises(OperationError):
        create(ops, data, **changes)
    assert not instances and not writer(ops, data["target"])


@pytest.mark.parametrize("who", [Principal(OWNER.actor, frozenset({"observe", "manage"})),
    Principal(READER.actor, OWNER.scopes), Principal(OUTSIDER.actor, OWNER.scopes)])
async def test_current_caller_requires_native_scopes_and_writer_membership(world, who):
    ops, _, instances = world
    data = await setup(ops)
    with pytest.raises(OperationError):
        create(ops, data, who=who)
    assert not instances


async def test_complete_context_budget_does_not_silently_drop_original_answers(world):
    ops, _, instances = world
    data = await setup(ops)
    with pytest.raises(OperationError, match="PM_FOLLOW_UP_CONTEXT_TOO_LARGE"):
        create(ops, data, params={"max_chars": 2000})
    assert not instances and not writer(ops, data["target"])


@pytest.mark.parametrize("fault", ["plain_text", "four_questions", "foreign_ref", "confirmed_by", "duplicate_key", "invalid_unicode"])
async def test_invalid_pm_format_keeps_original_native_reply_without_adopting_claims(world, fault):
    ops, settings, instances = world
    data = await setup(ops)
    value = envelope()
    if fault == "four_questions":
        value["questions"] = [{"question": "What matters next?", "why_now": "A decision affects acceptance.",
            "options": [{"label": "A", "consequence": "One tradeoff"}, {"label": "B", "consequence": "Another tradeoff"}],
            "source_refs": ["knowledge.scope"]}] * 4
    elif fault == "foreign_ref":
        value["summary"]["source_refs"] = ["https://private.invalid/invented"]
    elif fault == "confirmed_by":
        value["confirmed_by"] = OWNER.actor
    settings["raw_reply"] = "Preserve literal partial assessment." if fault == "plain_text" else json.dumps(value, ensure_ascii=False)
    if fault == "duplicate_key":
        settings["raw_reply"] = settings["raw_reply"].replace('"version": 1', '"version": 1, "version": 1')
    if fault == "invalid_unicode":
        value["summary"]["text"] = "\ud800"
        settings["raw_reply"] = json.dumps(value, ensure_ascii=True)
    op = await finish(ops, create(ops, data))
    assert op["status"] == "succeeded", op
    saved = read(ops, op)
    assert saved["raw_reply"] == settings["raw_reply"] and saved["document"] is None
    assert saved["validation"]["status"] == "needs_review" and not saved["conclusions_verified"]
    assert not op["result"]["outcome"]["assistant_imported"] and not writer(ops, data["target"])
    assert instances[0].sends == 1


async def test_original_source_withdrawal_hides_pending_completed_runs_operations_and_events(world):
    ops, _, instances = world
    data = await setup(ops, private=True)
    original = request(ops, data)
    first = await finish(ops, ops.create(OWNER, **original)[0])
    second = await finish(ops, create(ops, data))
    pending = create(ops, data)
    saved = run(ops, first)
    assert read(ops, first, READER)
    assert pm_runs.read(ops, READER, data["wid"], saved["run_id"])
    withdraw(ops, data)
    for op in (first, second, pending):
        assert not pm_access.visible(ops.db, READER, op)
    with pytest.raises(OperationError, match="SOURCE_UNAVAILABLE"):
        read(ops, first, READER)
    with pytest.raises(OperationError, match="PM_RUN_NOT_FOUND"):
        pm_runs.read(ops, READER, data["wid"], saved["run_id"])
    # Replay hides the original operation before domain diagnostics can reveal
    # which private source was withdrawn; direct domain reads retain their code.
    with pytest.raises(OperationError, match="NOT_FOUND") as replay_error:
        ops.create(OWNER, **original)
    assert replay_error.value.status == 404
    for listing, entries in ((analysis.list_assessments, "assessments"), (pm_runs.list_runs, "runs")):
        page = listing(ops, READER, data["wid"], limit=1)
        assert page[entries] == [] and page["next_cursor"] and page["coverage"]["visibility_filtered"]
        page = listing(ops, READER, data["wid"], limit=1, after=page["next_cursor"])
        assert page[entries] == [] and page["next_cursor"] is None
    feed = pm_access.filter_events(ops.db, READER, ops.journal.api_events(0, 500))
    assert all(event["body"].get("operation_id") not in {first["operation_id"], second["operation_id"], pending["operation_id"]}
               for event in feed["events"])
    # Recover a committed effect even though no public caller can now retrieve it.
    assert await analysis._run(OpContext(ops, first)) == first["result"]
    assert analysis._import(ops.journal, saved["run_id"], saved["generation"])["outcome"] == saved["outcome"]
    failed = await finish(ops, pending)
    assert failed["status"] == "failed" and len(instances) == 2


@pytest.mark.parametrize("phase", ["before_send", "running", "before_import"])
async def test_source_withdrawal_during_native_turn_releases_only_after_confirmed_exit(world, phase):
    ops, settings, instances = world
    data = await setup(ops, private=True)
    settings["block_preflight" if phase == "before_send" else "block_turn"] = True
    op = create(ops, data)
    runtime = await await_runtime(ops, instances, op)
    if phase == "before_import":
        original = runtime.sink
        async def on_lifecycle(value):
            result = await native_fixture.invoke(original, value)
            if value["kind"] == "process_exited":
                withdraw(ops, data)
            return result
        runtime.sink = on_lifecycle
        runtime.release.set()
    else:
        withdraw(ops, data)
    done = await finish(ops, op)
    assert done["status"] == "failed", done
    assert runtime.closed and runtime.sends == (0 if phase == "before_send" else 1)
    assert run(ops, done)["state"] == "aborted" and not writer(ops, data["target"])
    assert not ops.db.execute("SELECT 1 FROM pm_follow_up_assessments").fetchone()


@pytest.mark.parametrize("unknown", [False, True])
async def test_public_cancel_uses_original_process_reconciliation(world, unknown):
    ops, settings, instances = world
    data = await setup(ops)
    settings.update(block_turn=True, exit_unknown=unknown)
    op = create(ops, data)
    runtime = await await_runtime(ops, instances, op)
    ops.cancel(OWNER, op["operation_id"])
    done = await finish(ops, op)
    assert done["status"] == ("uncertain" if unknown else "cancelled"), done
    assert runtime.closed and runtime.sends == 1
    assert bool(writer(ops, data["target"])) is unknown
    assert not ops.db.execute("SELECT 1 FROM pm_follow_up_assessments").fetchone()
    if unknown:
        ops.context["pm_native_process_inspector"] = lambda identity: {"state": "dead", "process": identity,
            "proof": "original_process_exited", "observed_at": 2.0}
        done = await retry(ops, op)
        assert done["status"] == "cancelled" and not writer(ops, data["target"]) and len(instances) == 1


@pytest.mark.parametrize("step", ["pm.native.freeze", "pm.native.execute", "pm.native.import"])
async def test_lost_receipt_recovers_original_assessment_without_second_process(world, monkeypatch, step):
    ops, _, instances = world
    data = await setup(ops)
    original = ops._step_done
    lost = False
    def lose(operation_id, name, *args, **kwargs):
        nonlocal lost
        if name == step and not lost:
            lost = True
            raise sqlite3.OperationalError("original local receipt was interrupted")
        return original(operation_id, name, *args, **kwargs)
    monkeypatch.setattr(ops, "_step_done", lose)
    op = await finish(ops, create(ops, data))
    assert op["status"] == "uncertain", op
    assert not ops.db.execute("SELECT 1 FROM pm_follow_up_assessments").fetchone()
    recovered = await retry(ops, op)
    assert recovered["status"] == "succeeded", recovered
    assert read(ops, recovered)["validation"]["status"] == "format_valid"
    assert len(instances) == 1 and instances[0].sends == 1 and not writer(ops, data["target"])
    assert ops.db.execute("SELECT COUNT(*) FROM pm_follow_up_assessments").fetchone()[0] == 1


async def test_import_transaction_rolls_back_assessment_event_and_writer_release_together(world, monkeypatch):
    ops, _, instances = world
    data = await setup(ops)
    original = ops.journal.api_event
    lost = False
    def lose(kind, resource_id, name, *args, **kwargs):
        nonlocal lost
        result = original(kind, resource_id, name, *args, **kwargs)
        if kind == "pm_follow_up_assessment" and not lost:
            lost = True
            assert ops.db.execute("SELECT COUNT(*) FROM pm_follow_up_assessments").fetchone()[0] == 1
            assert writer(ops, data["target"]) is None
            raise sqlite3.OperationalError("event receipt lost after insert")
        return result
    monkeypatch.setattr(ops.journal, "api_event", lose)
    op = await finish(ops, create(ops, data))
    assert op["status"] == "uncertain" and writer(ops, data["target"])
    assert not ops.db.execute("SELECT 1 FROM pm_follow_up_assessments").fetchone()
    assert all(event["resource_type"] != "pm_follow_up_assessment" for event in ops.journal.api_events(0, 500)["events"])
    done = await retry(ops, op)
    assert done["status"] == "succeeded" and not writer(ops, data["target"])
    assert len(instances) == 1 and instances[0].sends == 1


async def test_follow_up_and_ordinary_analysis_share_one_generation_writer(world):
    ops, settings, instances = world
    data = await setup(ops)
    settings["block_turn"] = True
    first = create(ops, data)
    runtime = await await_runtime(ops, instances, first)
    second = native_fixture.request(ops, data["target"])
    done = await execute_one(ops, second)
    assert done["status"] == "failed" and done["error_code"] == "PM_WRITER_BUSY"
    runtime.release.set()
    assert (await finish(ops, first))["status"] == "succeeded" and len(instances) == 1


async def test_read_wire_budget_uses_actual_utf8_json_and_raw_reply_remains_literal(world, monkeypatch):
    ops, settings, _ = world
    data = await setup(ops)
    value = envelope()
    value["summary"]["text"] = "漢" * 8000
    settings["raw_reply"] = "  " + json.dumps(value, ensure_ascii=False) + "\n"
    op = await finish(ops, create(ops, data))
    out = analysis.get_assessment(ops, OWNER, data["wid"], op["result"]["outcome"]["assessment_id"])
    assert out["assessment"]["raw_reply"] == settings["raw_reply"]
    measured = pm_sprints._read_size(out)
    monkeypatch.setattr(analysis, "MAX_READ_BYTES", measured)
    assert analysis.get_assessment(ops, OWNER, data["wid"], out["assessment"]["assessment_id"]) == out
    monkeypatch.setattr(analysis, "MAX_READ_BYTES", measured - 1)
    with pytest.raises(OperationError, match="PM_FOLLOW_UP_ANALYSIS_TOO_LARGE"):
        analysis.get_assessment(ops, OWNER, data["wid"], out["assessment"]["assessment_id"])


async def test_assessment_list_uses_insertion_order_even_after_clock_rollback(world, monkeypatch):
    from types import SimpleNamespace
    ops, _, _ = world
    data = await setup(ops)
    monkeypatch.setattr(analysis, "time", SimpleNamespace(time=lambda: 20.0))
    first = await finish(ops, create(ops, data))
    monkeypatch.setattr(analysis, "time", SimpleNamespace(time=lambda: 10.0))
    second = await finish(ops, create(ops, data))
    page = analysis.list_assessments(ops, OWNER, data["wid"], limit=1)
    assert page["assessments"][0]["assessment_id"] == first["result"]["outcome"]["assessment_id"]
    following = analysis.list_assessments(ops, OWNER, data["wid"], after=page["next_cursor"], limit=1)
    assert following["assessments"][0]["assessment_id"] == second["result"]["outcome"]["assessment_id"]
    assert following["next_cursor"] is None


@pytest.mark.parametrize("bound", [False, True])
async def test_unbound_intake_can_use_same_workspace_thread_but_bound_intake_cannot_switch(world, bound):
    ops, _, instances = world
    data = await setup(ops)
    if not bound:
        intake = await prepare(ops, data)
        data["intake"] = pm_follow_up.get_intake(ops, OWNER, data["wid"], intake["intake_id"])["intake"]
    created = ops.create(OWNER, action="pm.thread.create", target={"workspace_id": data["wid"]}, params={"title": "Independent PM discussion"},
        preconditions={"expected_version": pm_workspace.get_workspace(ops.db, OWNER, data["wid"])["version"]}, idempotency_key=uuid.uuid4().hex)[0]
    data["target"] = {"workspace_id": data["wid"], "thread_id": (await finish(ops, created))["result"]["thread_id"]}
    if bound:
        with pytest.raises(OperationError, match="PM_FOLLOW_UP_THREAD_MISMATCH"):
            create(ops, data)
        assert not instances
    else:
        done = await finish(ops, create(ops, data))
        assert done["status"] == "succeeded" and read(ops, done)["thread_id"] == data["target"]["thread_id"]


@pytest.mark.parametrize("later", ["cancel", "source_withdrawal", "member_loss"])
async def test_lost_execute_ack_cannot_import_after_later_permission_or_intent_loss(world, monkeypatch, later):
    ops, _, instances = world
    data = await setup(ops, private=True)
    original = ops._step_done
    lost = False
    def lose(operation_id, name, *args, **kwargs):
        nonlocal lost
        if name == "pm.native.execute" and not lost:
            lost = True
            raise OSError("lost original process receipt")
        return original(operation_id, name, *args, **kwargs)
    monkeypatch.setattr(ops, "_step_done", lose)
    op = await finish(ops, create(ops, data))
    assert op["status"] == "uncertain" and writer(ops, data["target"])
    if later == "cancel":
        ops.cancel(OWNER, op["operation_id"])
    elif later == "source_withdrawal":
        withdraw(ops, data)
    else:
        ops.db.execute("UPDATE pm_workspace_members SET role='reader' WHERE workspace_id=? AND actor=?", (data["wid"], OWNER.actor))
    result = await retry(ops, op)
    assert result["status"] == ("cancelled" if later == "cancel" else "failed")
    assert run(ops, result)["state"] == "aborted" and not writer(ops, data["target"])
    assert not ops.db.execute("SELECT 1 FROM pm_follow_up_assessments").fetchone()
    assert len(instances) == instances[0].sends == 1


@pytest.mark.parametrize("death", [False, True])
async def test_restart_never_relaunches_original_follow_up_or_imports_without_terminal_proof(world, death):
    ops, settings, instances = world
    data = await setup(ops)
    settings.update(exit_unknown=True, ack_only=True)
    op = await finish(ops, create(ops, data))
    assert op["status"] == "uncertain"
    reopened = Journal(ops.journal.path)
    recovered = OperationService(reopened)
    analysis.install(recovered)
    def forbidden_factory(*args, **kwargs):
        pytest.fail("restart tried to send a new native turn")
    recovered.context["pm_native_runtime_factory"] = forbidden_factory
    recovered.context["pm_native_process_inspector"] = lambda identity: {
        "state": "dead" if death else "unknown", "process": identity,
        "proof": "pid_reused" if death else "identity_unavailable", "observed_at": 2.0}
    try:
        done = await retry(recovered, op)
        assert done["status"] == ("failed" if death else "uncertain")
        assert bool(writer(recovered, data["target"])) is not death
        assert not recovered.db.execute("SELECT 1 FROM pm_follow_up_assessments").fetchone()
        if death:
            assert run(recovered, done)["state"] == "aborted"
        assert len(instances) == instances[0].sends == 1
    finally:
        reopened.close()


async def test_public_provenance_selects_only_original_process_proof_fields(world):
    ops, settings, instances = world
    data = await setup(ops)
    settings["block_turn"] = True
    op = create(ops, data)
    runtime = await await_runtime(ops, instances, op)
    original = runtime.sink
    async def diagnostic(value):
        if value["kind"] == "process_exited":
            value = {**value, "private_config_path": "/fixture/private/runtime-config", "environment": {"PRIVATE": "diagnostic only"}}
        return await native_fixture.invoke(original, value)
    runtime.sink = diagnostic
    runtime.release.set()
    done = await finish(ops, op)
    saved = run(ops, done)
    assert "private_config_path" in pm_native_journal.lifecycle(ops.db, saved["run_id"])["process_exited"]
    proof = read(ops, done)["provenance"]
    assert set(proof["process"]) == {"pid", "start_ticks", "boot_id_digest"}
    assert "private_config_path" not in proof["process_proof"] and "environment" not in proof["process_proof"]


async def test_run_list_bytes_are_whole_record_prefix_and_cursor_does_not_skip(world, monkeypatch):
    ops, _, _ = world
    data = await setup(ops)
    for _ in range(3):
        done = await finish(ops, create(ops, data, params={"purpose": "漢" * 4000}))
        assert done["status"] == "succeeded", done
    full = pm_runs.list_runs(ops, OWNER, data["wid"])
    first = {**full, "runs": full["runs"][:1], "next_cursor": full["runs"][0]["run_id"],
             "coverage": {"visibility_filtered": False, "scanned": 1}}
    one_record_budget = max(pm_sprints._read_size({**first, "runs": [row], "next_cursor": row["run_id"]}) for row in full["runs"])
    monkeypatch.setattr(pm_runs, "MAX_READ_BYTES", one_record_budget)
    page = pm_runs.list_runs(ops, OWNER, data["wid"])
    assert page == first
    following = pm_runs.list_runs(ops, OWNER, data["wid"], after=page["next_cursor"])
    assert following["runs"] == full["runs"][1:2]
    last = pm_runs.list_runs(ops, OWNER, data["wid"], after=following["next_cursor"])
    assert last["runs"] == full["runs"][2:] and last["next_cursor"] is None
    monkeypatch.setattr(pm_runs, "MAX_READ_BYTES", pm_sprints._read_size(first) - 1)
    with pytest.raises(OperationError, match="PM_RUN_TOO_LARGE"):
        pm_runs.list_runs(ops, OWNER, data["wid"])


@pytest.mark.parametrize("count", [0, 1, 3])
def test_format_allows_bounded_consequential_questions_without_claiming_verification(count):
    value = envelope()
    value["questions"] = [{"question": "Which target matters first?", "why_now": "It changes the next acceptance criteria.",
        "options": [{"label": "Keep scope", "consequence": "Preserve the current promise"},
                    {"label": "Propose a change", "consequence": "Requires an explicit direction decision"}],
        "source_refs": ["knowledge.scope"]}] * count
    document, validation = format_.validate(json.dumps(value, ensure_ascii=False), {"knowledge.scope", "knowledge.finance", "knowledge.quality"})
    assert document == value and validation == {"status": "format_valid", "errors": [], "conclusions_verified": False}


@pytest.mark.parametrize("after", [-1, True, 2**63])
async def test_assessment_cursor_rejects_non_sqlite_sequences(world, after):
    ops, _, _ = world
    data = await setup(ops)
    with pytest.raises(OperationError, match="PM_FOLLOW_UP_ANALYSIS_INVALID"):
        analysis.list_assessments(ops, OWNER, data["wid"], after=after)


@pytest.mark.parametrize("external_refs", [None, "{}"])
async def test_missing_original_admission_never_exposes_or_launches_analysis(world, external_refs):
    ops, _, instances = world
    data = await setup(ops)
    op = create(ops, data)
    ops.db.execute("UPDATE operations SET external_refs=? WHERE operation_id=?", (external_refs, op["operation_id"]))
    assert not analysis.operation_visible(ops.db, OWNER, op)
    done = await finish(ops, op)
    assert done["status"] == "failed" and not instances and not writer(ops, data["target"])


async def test_hidden_run_page_budgets_metadata_growth_without_skipping_cursor(world, monkeypatch):
    ops, _, _ = world
    data = await setup(ops, private=True)
    for _ in range(11):
        assert (await finish(ops, create(ops, data)))["status"] == "succeeded"
    identifiers = [row[0] for row in ops.db.execute("SELECT run_id FROM pm_runs ORDER BY run_id")]
    withdraw(ops, data)
    prefix = {"workspace_id": data["wid"], "runs": [], "next_cursor": identifiers[8],
              "coverage": {"visibility_filtered": True, "scanned": 9}}
    monkeypatch.setattr(pm_runs, "MAX_READ_BYTES", pm_sprints._read_size(prefix))
    page = pm_runs.list_runs(ops, READER, data["wid"])
    assert page == prefix
    rest = pm_runs.list_runs(ops, READER, data["wid"], after=page["next_cursor"])
    assert rest["runs"] == [] and rest["coverage"]["scanned"] == 2 and rest["next_cursor"] is None
