"""Real operation/session transport with fake dedicated BAT runtime evidence."""
from __future__ import annotations

import asyncio
import copy

import pytest

from bat_agent_connector import pm_bat_operations as bat
from bat_agent_connector import pm_bat_runtime as runtime
from bat_agent_connector import pm_runs, pm_runtime, registry
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.errors import InvokeTimeout
from tests import test_api_v1 as api
from tests.operation_helpers import settle_operations
from tests.test_pm_discussions import prepared, read

daemon = api.daemon
OWNER = Principal("project-owner", frozenset({"observe", "manage", "start"}))


def capability(sid, digest):
    return {"sessionId": sid, "pmRuntime": {"profile": runtime.PROFILE, "profileVersion": 1,
        "runtimeId": "runtime-" + sid, "clientContextDigest": digest, "sessionId": sid, "launchAttempted": True,
        "batVersion": "fixture", "batSourceRevision": "a" * 40,
        "batBinaryDigest": "e" * 64, "codexBinaryDigest": "f" * 64,
        "codexVersion": runtime.CODEX_VERSION, "codexSourceRevision": runtime.CODEX_SOURCE,
        "process": {"pid": 98765, "startTicks": 99, "bootIdDigest": "b" * 64},
        "threadId": "thread-" + sid, "actualModel": "observed-model", "configDigest": "c" * 64,
        "runtimeCwd": "/fixture/private-empty", "attestation": {
            "optionsConfirmed": True, "isolatedConfigConfirmed": True, "externalToolsDisabled": True,
            "environmentAccess": False, "skills": [], "mcpServers": [], "freshThread": True, "initialNumTurns": 0,
            "toolCatalog": "not_exposed_by_protocol", "internalToolOptionsReadback": "not_exposed_by_protocol"},
        "send": None, "terminal": None, "exit": None, "status": "ready"}}


@pytest.fixture
def bat_world(daemon, mock, monkeypatch):
    settings, proofs, events = {}, {}, {}
    selection = {"target": {"host": "h1", "workspace_id": "ws-1", "repository": "owner/repo"}, "fixture": "already-prepared"}
    # PM worktree creation/configuration is independently covered by its real
    # Git integration suite. This fixture exercises the original BAT start path.
    monkeypatch.setattr(bat, "_binding", lambda ops, wid: copy.deepcopy(selection))
    async def no_carrier(_):
        return None
    monkeypatch.setattr(bat, 'start_carrier', no_carrier)
    mock.handlers["git:log"] = lambda p: [{"hash": "a" * 40}]
    original = mock.dispatch
    def dispatch(channel, params):
        sid = params.get("sessionId")
        if channel == "claude:start-session":
            result = original(channel, params)
            value = capability(sid, params["options"]["pmContextDigest"])
            if settings.get("unattested"):
                value["pmRuntime"]["attestation"]["externalToolsDisabled"] = False
            if settings.get("wrong_inner_session"):
                value["pmRuntime"]["sessionId"] = "different-original-session"
            proofs[sid] = value
            if failure := settings.get('preflight_failure'):
                partial = value['pmRuntime']
                partial.update(attestation=None, threadId=None, actualModel=None, configDigest=None,
                    status='preparing' if failure == 'preparing' else 'failed', failureReason='PM_RUNTIME_CONFIG_REJECTED',
                    launchAttempted=failure == 'launched')
                if failure == 'launched':
                    partial['exit'] = {'confirmedDead': True, 'exitCode': None, 'signal': 9,
                        'action': 'preflight_failed', 'process': partial['process'].copy()}
                else:
                    partial['process'] = None
                raise RuntimeError('fixture preflight stopped before any model input')
            mock.metas[sid].update(sdkSessionId=value["pmRuntime"]["threadId"], numTurns=0,
                model="observed-model", pmRuntime=value["pmRuntime"])
            return result
        if channel == "claude:get-pm-runtime":
            return copy.deepcopy(proofs[sid])
        if channel == "claude:sync-session":
            cursor = {"epoch": "original-epoch", "seq": 0}
            if "cursor" not in params or settings.get("gap"):
                return {"sessionId": sid, "mode": "snapshot", "cursor": cursor,
                    "state": {"sessionId": sid, "meta": copy.deepcopy(mock.metas[sid]), "isStreaming": False, "messages": []}}
            items = [event for event in events.get(sid, []) if event["seq"] > params["cursor"]["seq"]]
            more = bool(settings.get('page_size') and len(items) > settings['page_size'])
            items = items[:settings['page_size']] if settings.get('page_size') else items
            return {"sessionId": sid, "mode": "delta", "events": copy.deepcopy(items), "hasMore": more,
                "cursor": {**cursor, "seq": items[-1]["seq"] if items else params["cursor"]["seq"]}}
        if channel == "claude:send-message":
            result = original(channel, params)
            value = proofs[sid]["pmRuntime"]
            assert params["pmRuntimeId"] == value["runtimeId"]
            assert params["pmContextDigest"] == value["clientContextDigest"]
            assert params["clientMessageId"]
            value["send"] = {"attempted": True, "promptDigest": pm_runtime.prompt_digest(params["prompt"]),
                "clientMessageId": params["clientMessageId"], "turnId": "original-turn", "acknowledged": True,
                "nativeStarted": True, "observedClientMessageId": params["clientMessageId"]}
            if not settings.get("ack_only"):
                def event(seq, channel, **data):
                    return {"seq": seq, "channel": channel, "params": {"sessionId": sid, **data}}
                events[sid] = [event(1, "claude:message", message={"id": params["clientMessageId"], "role": "user", "content": params["prompt"]}),
                    event(2, "claude:message", message={"id": "answer-1", "role": "assistant", "content": "Discuss the original evidence."}),
                    event(3, "claude:turn-end", payload={"reason": "completed", "turnId": "original-turn",
                        "sdkSessionId": value["threadId"], "result": "Discuss the original evidence."})]
                if settings.get('page_size'):
                    for item in events[sid]:
                        item['seq'] += 9
                    events[sid] = [event(i, 'claude:status', meta={'sdkSessionId': value['threadId'],
                        'model': 'observed-model', **pm_runtime.readonly_start_options()}) for i in range(1, 10)] + events[sid]
                native = {"method": "turn/completed", "params": {"threadId": value["threadId"],
                    "turn": {"id": "original-turn", "status": "completed", "items": [{"type": "agentMessage",
                        "id": "native-answer", "text": "Discuss the original evidence."}], "itemsView": "summary"}}}
                value["terminal"] = {"native": True, "threadId": value["threadId"], "turnId": "original-turn",
                    "status": "completed", "eventDigest": pm_runs._digest(native), "event": native,
                    "nativeMessageId": "native-answer", "outputDigest": pm_runtime.prompt_digest("Discuss the original evidence.")}
            if not settings.get("missing_exit"):
                value["exit"] = {"confirmedDead": True, "exitCode": 0, "signal": None,
                    "action": "native_terminal", "process": copy.deepcopy(value["process"])}
            return result
        return original(channel, params)
    monkeypatch.setattr(mock, "dispatch", dispatch)
    return daemon.ops, settings, proofs, events


async def request(ops, target, key="original"):
    op, _ = ops.create(OWNER, action=bat.ACTION, target=target, params={"purpose": "Explore the current evidence"},
        preconditions={"expected_version": read(ops, target)["thread"]["version"]}, idempotency_key=key)
    await settle_operations(ops)
    return ops.get(op["operation_id"])


async def test_complete_turn_retains_sources_and_releases_only_dead_original_writer(bat_world, mock):
    ops, _, proofs, events = bat_world
    target = await prepared(ops)
    out = await request(ops, target)
    assert out["status"] == "succeeded", out
    run = pm_runs.get_by_operation(ops.db, out["operation_id"])
    assert run["state"] == "completed" and run["adapter"] == runtime.ADAPTER
    assert pm_runs.events(ops.db, run["run_id"]) == next(iter(events.values()))
    assert runtime.death_proof(ops.db, run)["process"] == next(iter(proofs.values()))["pmRuntime"]["process"]
    assert ops.db.execute("SELECT run_id FROM pm_writer_slots WHERE workspace_id=?", (target["workspace_id"],)).fetchone()[0] is None
    assert read(ops, target)["messages"][-1]["actor"] == "pm:" + target["workspace_id"]
    provenance = read(ops, target)['messages'][-1]['provenance']['source_refs']['bat_runtime']
    original = next(iter(proofs.values()))['pmRuntime']
    assert provenance['native_event_digest'] == original['terminal']['eventDigest']
    assert provenance['native_message_id'] == 'native-answer'
    assert provenance['process_completion']['process'] == original['process']
    assert pm_runs.read(ops, OWNER, target['workspace_id'], run['run_id'])['bat_runtime_provenance'] == provenance
    assert mock.channels().count("claude:start-session") == mock.channels().count("claude:send-message") == 1
    assert ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    row = registry.get("h1", run["session_binding"]["session_id"])
    assert row["controller_authority"]["actor"] == "pm:" + target["workspace_id"]


async def test_missing_exit_holds_writer_and_original_recovery_never_resends(bat_world, mock):
    ops, settings, proofs, _ = bat_world
    settings["missing_exit"] = True
    target = await prepared(ops)
    out = await request(ops, target)
    assert out["status"] == "uncertain", out
    run = pm_runs.get_by_operation(ops.db, out["operation_id"])
    assert run["state"] != "completed"
    assert ops.db.execute("SELECT run_id FROM pm_writer_slots WHERE workspace_id=?", (target["workspace_id"],)).fetchone()[0] == run["run_id"]
    value = next(iter(proofs.values()))["pmRuntime"]
    value["exit"] = {"confirmedDead": True, "exitCode": 0, "signal": None, "action": "native_terminal", "process": value["process"].copy()}
    ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (out["operation_id"],))
    await settle_operations(ops)
    assert ops.get(out["operation_id"])["status"] == "succeeded"
    assert mock.channels().count("claude:start-session") == mock.channels().count("claude:send-message") == 1


@pytest.mark.parametrize("phase", ["claude:start-session", "claude:send-message"])
async def test_lost_transport_reply_reads_original_effect_once(bat_world, mock, monkeypatch, phase):
    ops, _, _, _ = bat_world
    client = ops.context["fleet"].client("h1")
    original, dropped = client.invoke, False
    async def lost(channel, *args, **kwargs):
        nonlocal dropped
        result = await original(channel, *args, **kwargs)
        if channel == phase and not dropped:
            dropped = True
            raise InvokeTimeout("fixture dropped original reply")
        return result
    monkeypatch.setattr(client, "invoke", lost)
    out = await request(ops, await prepared(ops))
    assert out["status"] == "uncertain", out
    ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (out["operation_id"],))
    await settle_operations(ops)
    assert ops.get(out["operation_id"])["status"] == "succeeded", ops.get(out["operation_id"])
    assert mock.channels().count(phase) == 1


@pytest.mark.parametrize("setting", ["ack_only", "gap", "unattested", "wrong_inner_session"])
async def test_unproven_runtime_never_imports_assistant(bat_world, mock, setting):
    ops, settings, _, _ = bat_world
    settings[setting] = True
    target = await prepared(ops)
    out = await request(ops, target)
    assert out["status"] != "succeeded", out
    assert read(ops, target)["messages"] == []
    if setting in {"unattested", "wrong_inner_session"}:
        assert "claude:send-message" not in mock.channels()


async def test_more_than_eight_replay_pages_recover_without_aborting_dead_original(bat_world, mock):
    ops, settings, _, _ = bat_world
    settings['page_size'] = 1
    target = await prepared(ops)
    out = await request(ops, target)
    assert out['status'] == 'uncertain', out
    run = pm_runs.get_by_operation(ops.db, out['operation_id'])
    assert run['has_more'] and run['state'] not in {'aborted', 'failed', 'completed'}
    assert runtime.death_proof(ops.db, run)
    assert read(ops, target)['messages'] == []
    ops.db.execute('UPDATE operations SET next_run_at=0 WHERE operation_id=?', (out['operation_id'],))
    await settle_operations(ops)
    assert ops.get(out['operation_id'])['status'] == 'succeeded', ops.get(out['operation_id'])
    assert mock.channels().count('claude:start-session') == mock.channels().count('claude:send-message') == 1


async def test_final_frame_membership_withdrawal_prevents_input(bat_world, mock, monkeypatch):
    ops, _, _, _ = bat_world
    target = await prepared(ops)
    client = ops.context["fleet"].client("h1")
    client._sem = asyncio.Semaphore(1)
    original = client._invoke_checked
    async def withdraw(channel, params, *args, **kwargs):
        if channel == "claude:send-message":
            ops.db.execute("UPDATE pm_workspace_members SET role='reader' WHERE workspace_id=? AND actor=?",
                           (target["workspace_id"], OWNER.actor))
        return await original(channel, params, *args, **kwargs)
    monkeypatch.setattr(client, "_invoke_checked", withdraw)
    out = await asyncio.wait_for(request(ops, target), 15)
    assert out["status"] != "succeeded"
    assert "claude:send-message" not in mock.channels()


@pytest.mark.parametrize('phase', ['allocated', 'launched', 'preparing'])
async def test_preflight_abort_requires_positive_original_no_process_or_death(bat_world, mock, phase):
    ops, settings, _, _ = bat_world
    settings['preflight_failure'] = phase
    target = await prepared(ops)
    out = await request(ops, target)
    run = pm_runs.get_by_operation(ops.db, out['operation_id'])
    writer = ops.db.execute('SELECT run_id FROM pm_writer_slots WHERE workspace_id=?', (target['workspace_id'],)).fetchone()[0]
    if phase == 'preparing':
        assert out['status'] == 'uncertain' and writer == run['run_id']
    else:
        assert out['status'] == 'failed' and run['state'] == 'aborted' and writer is None, out
    assert mock.channels().count('claude:start-session') == 1 and 'claude:send-message' not in mock.channels()
    assert read(ops, target)['messages'] == []
