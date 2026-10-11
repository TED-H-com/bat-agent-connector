"""Controller boundaries through real central operations, HTTP and mock BAT."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import api_auth, controller_authority, registry
from bat_agent_connector.errors import InvokeTimeout
from bat_agent_connector.operations import OperationError
from tests import test_api_v1 as api
from tests.operation_helpers import settle_operations

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


async def test_legacy_lost_reply_can_reconcile_without_new_authority(daemon, mock, monkeypatch):
    from bat_agent_connector import service
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
