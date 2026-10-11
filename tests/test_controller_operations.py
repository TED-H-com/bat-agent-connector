"""Controller boundaries through real central operations, HTTP and mock BAT."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bat_agent_connector import (
    api_auth,
    cli,
    controller_authority,
    platform_files,
    registry,
    resource_policy,
    service,
)
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.mcp_server import build_server
from bat_agent_connector.operations import OperationError
from tests import test_api_v1 as api
from tests.operation_helpers import settle_operations
from tests.test_mcp_principal import call

daemon = api.daemon
served = api.served
OWNER = api_auth.Principal("operator-dashboard", frozenset({"observe", "start", "operate"}))
OTHER = api_auth.Principal("other-agent", OWNER.scopes)
ADMIN = api_auth.Principal(api_auth.ADMIN_ACTOR, frozenset(), admin=True)


async def start(d, mock):
    mock.handlers["git:log"] = lambda p: [{"hash": "a" * 40}]
    op, _ = d.ops.create(OWNER, action="session.start", target={"host": "h1", "workspace": "ws-1"},
                         idempotency_key="own-start")
    await settle_operations(d.ops)
    done = d.ops.get(op["operation_id"])
    assert done["status"] == "succeeded", done
    return done["result"]["session_id"], done


def send(d, sid, principal=OWNER, key="send"):
    return d.ops.create(principal, action="session.send", target={"host": "h1", "session_id": sid},
                        params={"text": "Discuss the current goal"}, idempotency_key=key)[0]


@pytest.mark.parametrize("caller", [OTHER, ADMIN])
async def test_foreign_controller_cannot_send_or_steer_even_as_admin(daemon, mock, caller):
    sid, created = await start(daemon, mock)
    before = len(api.write_frames(mock))
    with pytest.raises(OperationError) as error:
        send(daemon, sid, caller)
    assert error.value.code == "FOREIGN_CONTROLLER"
    for verb in (daemon.ops.cancel, daemon.ops.resume):
        with pytest.raises(OperationError, match="original resource controller"):
            verb(caller, created["operation_id"])
    assert len(api.write_frames(mock)) == before


async def test_token_rotation_and_multiple_clients_keep_same_controller(served, mock):
    d, port = served
    sid, _ = await start(d, mock)
    authority = registry.get("h1", sid)["controller_authority"]
    first = api.token(d, OWNER.actor, "observe", "operate")
    second = api.token(d, OWNER.actor, "observe", "operate")
    for index, token in enumerate((first, second)):
        status, result = await api.http(port, "POST", "/api/v1/operations", tok=token, body={
            "action": "session.send", "target": {"host": "h1", "session_id": sid},
            "params": {"text": "same controller"}, "idempotency_key": f"client-{index}"})
        assert status in (200, 201, 202), result
        saved = controller_authority.operation_binding(result["operation"])
        assert saved["controller_id"] == authority["controller_id"]
        await settle_operations(d.ops)
    assert mock.channels().count("claude:send-message") == 2


async def test_foreign_inventory_is_read_only_without_relabeling_provenance(served, mock):
    d, port = served
    sid, _ = await start(d, mock)
    await d.inventory.refresh_host("h1")
    own = api.token(d, OWNER.actor, "observe", "operate")
    other = api.token(d, OTHER.actor, "observe", "operate")
    for token, access in ((own, "managed"), (other, "read_only")):
        status, body = await api.http(port, "GET", f"/api/v1/sessions/h1/{sid}?live=true", tok=token)
        assert status == 200, body
        assert body["session"]["api_access"] == access
        assert body["session"]["provenance"] == "connector_managed"
        if access == "read_only":
            assert body["session"]["read_only_code"] == "FOREIGN_CONTROLLER"
    for access in ("managed", "read_only"):
        status, listing = await api.http(port, "GET", f"/api/v1/sessions?access={access}", tok=other)
        assert status == 200
        assert all(row["api_access"] == access for row in listing["sessions"])
        assert (sid in {row["session_id"] for row in listing["sessions"]}) == (access == "read_only")


async def test_bootstrap_session_cursor_continues_for_original_controller(served, mock):
    d, port = served
    sample = next(t for t in mock.ws_doc["terminals"] if t["id"] == "sess-unload-0003")
    mock.ws_doc["terminals"] += [{**sample, "id": f"unloaded-{i:03d}"} for i in range(55)]
    await d.inventory.refresh_host("h1")
    own = api.token(d, OWNER.actor, "observe")
    other = api.token(d, OTHER.actor, "observe")
    status, bootstrap = await api.http(port, "GET", "/api/v1/bootstrap", tok=own)
    assert status == 200
    first = bootstrap["snapshot"]["sessions"]
    assert first["next_cursor"] and len(first["sessions"]) == 50
    path = "/api/v1/sessions?order=id&include_gone=true&cursor=" + first["next_cursor"]
    status, second = await api.http(port, "GET", path, tok=own)
    assert status == 200, second
    assert second["sessions"]
    assert not ({r["resource_id"] for r in first["sessions"]} & {r["resource_id"] for r in second["sessions"]})
    status, rejected = await api.http(port, "GET", path, tok=other)
    assert status == 422, rejected


async def test_legacy_policy_reads_use_authenticated_controller(served, mock, monkeypatch):
    d, port = served
    sid, _ = await start(d, mock)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    server, fleet = build_server(d.fleet.config)
    before = len(api.write_frames(mock))
    try:
        for actor, expected in ((OWNER.actor, "managed"), (OTHER.actor, "read_only")):
            monkeypatch.setenv("BATC_API_TOKEN", api.token(d, actor, "observe"))
            policy, _ = await cli._run(SimpleNamespace(cmd="policy", host="h1", session=sid))
            assert policy["api_access"] == expected
            result = await call(server, "session_policy", {"host": "h1", "session_id": sid})
            assert ("FOREIGN_CONTROLLER" in result) == (expected == "read_only"), result
        assert len(api.write_frames(mock)) == before
    finally:
        await fleet.close()


async def test_unproven_legacy_creation_is_never_assigned_to_caller(daemon, mock):
    sid, _ = await start(daemon, mock)
    # Simulate an old registry file, not an API that grants ownership.
    path = registry.registry_path()
    data = json.loads(path.read_text())
    rows = data if isinstance(data, list) else data["sessions"]
    row = next(item for item in rows if item["session_id"] == sid)
    row.pop("controller_authority")
    row.pop("controller_incarnation")
    path.write_text(json.dumps(data))
    before = len(api.write_frames(mock))
    with pytest.raises(OperationError) as error:
        send(daemon, sid)
    assert error.value.code == "CONTROLLER_AUTHORITY_REQUIRED"
    assert "controller_authority" not in registry.get("h1", sid)
    assert len(api.write_frames(mock)) == before


async def test_replacement_after_admission_cannot_receive_original_send(daemon, mock):
    sid, _ = await start(daemon, mock)
    pending = send(daemon, sid)
    path = registry.registry_path()
    data = json.loads(path.read_text())
    rows = data if isinstance(data, list) else data["sessions"]
    next(row for row in rows if row["session_id"] == sid)["controller_incarnation"] = "f" * 32
    path.write_text(json.dumps(data))
    before = len(api.write_frames(mock))
    await settle_operations(daemon.ops)
    done = daemon.ops.get(pending["operation_id"])
    assert done["error_code"] == "CONTROLLER_INCARNATION_CHANGED", done
    assert len(api.write_frames(mock)) == before


@pytest.mark.parametrize("phase", ["resolution", "live_check", "transport_connect"])
@pytest.mark.parametrize("action,params,channel", [
    ("session.send", {"text": "original target only"}, "claude:send-message"),
    ("session.answer", {"answers": ["yes"], "tool_use_id": "question-1"}, "claude:resolve-ask-user"),
    ("session.interrupt", {"mode": "soft"}, "claude:interrupt-turn"),
    ("session.send", {"text": "resume original target only"}, "claude:client-resume"),
    ("session.permissions", {"mode": "default"}, "claude:set-permission-mode"),
])
async def test_admitted_incarnation_survives_async_reads_and_connect(
        daemon, mock, monkeypatch, phase, action, params, channel):
    sid, _ = await start(daemon, mock)
    original_row = registry.get("h1", sid)
    if channel == "claude:client-resume":
        mock.metas[sid] = None
    if action == "session.answer":
        mock.states[sid] = {"pendingAskUser": {"toolUseId": "question-1", "questions": [{"question": "Continue?"}]}}
    pending, _ = daemon.ops.create(OWNER, action=action, target={"host": "h1", "session_id": sid},
                                   params=params, idempotency_key="fixed-incarnation")
    before = len(api.write_frames(mock))
    changed = False

    def replace_incarnation():
        nonlocal changed
        if changed:
            return
        document = json.loads(registry.registry_path().read_text())
        row = next(row for row in document["sessions"] if row["session_id"] == sid)
        assert row == original_row
        row["controller_incarnation"] = "f" * 32
        assert row["controller_incarnation"] != original_row["controller_incarnation"]
        platform_files.atomic_write(registry.registry_path(), json.dumps(document).encode())
        changed = True

    if phase == "resolution":
        resolve = service._resolve_session

        async def resolve_then_replace(*args, **kwargs):
            result = await resolve(*args, **kwargs)
            replace_incarnation()
            return result

        monkeypatch.setattr(service, "_resolve_session", resolve_then_replace)
    elif phase == "live_check":
        classify_live = resource_policy.classify_live

        async def check_then_replace(*args, **kwargs):
            result = await classify_live(*args, **kwargs)
            replace_incarnation()
            return result

        monkeypatch.setattr(resource_policy, "classify_live", check_then_replace)
    else:
        client = daemon.fleet.client("h1")
        invoke_checked, connect = client._invoke_checked, client.connect
        effect_connect = False

        async def invoke_with_connect_race(canonical, *args, **kwargs):
            nonlocal effect_connect
            effect_connect = canonical == channel
            try:
                return await invoke_checked(canonical, *args, **kwargs)
            finally:
                effect_connect = False

        async def connect_then_replace():
            await connect()
            if effect_connect:
                replace_incarnation()

        monkeypatch.setattr(client, "_invoke_checked", invoke_with_connect_race)
        monkeypatch.setattr(client, "connect", connect_then_replace)

    await settle_operations(daemon.ops)
    done = daemon.ops.get(pending["operation_id"])
    assert changed, done
    assert done["error_code"] == "CONTROLLER_INCARNATION_CHANGED", done
    assert len(api.write_frames(mock)) == before
    assert registry.get("h1", sid)["controller_authority"] == original_row["controller_authority"]


async def test_legacy_lost_reply_can_reconcile_without_new_authority(daemon, mock, monkeypatch):
    sid, _ = await start(daemon, mock)
    mock.echo_sends = True
    original = service.session_send

    async def lost(*args, **kwargs):
        await original(*args, **kwargs)
        raise InvokeTimeout("mock acknowledgement lost")

    monkeypatch.setattr(service, "session_send", lost)
    pending = send(daemon, sid)
    await settle_operations(daemon.ops)
    assert daemon.ops.get(pending["operation_id"])["status"] == "uncertain"
    op = daemon.ops.get(pending["operation_id"])
    refs = op["external_refs"]
    refs.pop("admission_binding")
    daemon.ops.db.execute("UPDATE operations SET external_refs=?,next_run_at=0 WHERE operation_id=?",
                          (json.dumps(refs), pending["operation_id"]))
    before = len(api.write_frames(mock))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(pending["operation_id"])["status"] == "succeeded"
    assert len(api.write_frames(mock)) == before
