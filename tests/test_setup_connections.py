"""Connection intents through real HTTP, a disk journal and a disk participant."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from unittest.mock import patch

import pytest

from bat_agent_connector import managed_setup as setup
from bat_agent_connector import platform_files
from bat_agent_connector.connection_source_descriptor import build_source_descriptor
from tests import test_managed_setup as producer
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token

installed = producer.installed
PREVIEW = "/api/v1/connections/preview"
RECEIPT = "/api/v1/connections/receipt"
HANDOFF = "/api/v1/connections/handoff"


@pytest.fixture
async def served_setup(installed):
    daemon, principal, _ = installed
    bearer = token(daemon, principal.actor, *principal.scopes)
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async def request(method, path, body=None, *, credential=bearer, key=None):
        return await http(port, method, path, tok=credential, body=body,
            headers={"Idempotency-Key": key} if key else None)
    async def disconnect():
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()
    request.disconnect = disconnect
    async def raw_request(path, raw):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        headers = (f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {bearer}\r\n"
            f"Content-Length: {len(raw)}\r\n\r\n").encode()
        writer.write(headers + raw)
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 10)
        writer.close()
        await writer.wait_closed()
        return int(response.split(b" ", 2)[1]), response
    request.raw_request = raw_request
    yield request
    server.close()
    await server.wait_closed()


def digest(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def source(installed):
    daemon, _, root = installed
    directory = root / "profiles"
    directory.mkdir(mode=0o700)
    index = {"profiles": [{"id": "profile-a", "type": "remote", "remoteHost": "bat.example",
        "remotePort": 9876, "remoteFingerprint": "A" * 64, "remoteProfileId": "default"}]}
    platform_files.atomic_write(directory / "index.json", json.dumps(index).encode())
    platform_files.atomic_write(directory / "remote-tokens.enc.json", json.dumps({"enc": False,
        "data": json.dumps({"tokens": {"profile-a": "synthetic-token"}})}).encode())
    path, _, document, _ = setup._configuration(daemon)
    document["bat"] = {"profiles_dir": str(directory)}
    platform_files.atomic_write(path, setup._encode(document))
    descriptor = build_source_descriptor(kind="bat_profile", profile_reference="profile-a",
        association={"url": "wss://bat.example:9876/", "fingerprint": "A" * 64},
        availability_reason="SOURCE_AVAILABLE", source_state={
            "directory": digest(str(directory.resolve()).encode()),
            "index": digest((directory / "index.json").read_bytes()),
            "credential": digest((directory / "remote-tokens.enc.json").read_bytes())})
    return {"source": descriptor,
        "source_reference": descriptor["reference"], "remote_profile_id": "default",
        "label": "Fixture connection", "preferences": {"favorite": True},
        "service_ref": None, "workspace_ref": None}


def intent(source, *, action="connection.reference.apply", installation="desktop-a", connection=None):
    return {"action": action, "target": {"installation_ref": installation,
        **({"connection_id": connection} if connection else {})}, "params": copy.deepcopy(source),
        "preconditions": {"expected_revision": 0}}


async def review(request, body, **kwargs):
    status, response = await request("POST", PREVIEW, body, **kwargs)
    assert status == 200, response
    return {**body, "preconditions": {**body["preconditions"], "review_digest": response["preview"]["review_digest"]}}


async def accept(request, body, *, key="original-key", **kwargs):
    body = await review(request, body, **kwargs)
    status, response = await request("POST", "/api/v1/operations", body, key=key, **kwargs)
    assert status == 202, response
    return body, response["operation"]["operation_id"]


async def read_handoff(request, operation, installation="desktop-a", **kwargs):
    status, response = await request("POST", HANDOFF, {"operation_id": operation, "installation_ref": installation}, **kwargs)
    assert status == 200, response
    return response["native_handoff"]


class DiskParticipant:
    """One bounded effect identity persisted before reporting via the real route."""
    def __init__(self, path):
        self.path = path

    @property
    def writes(self):
        counter = self.path.with_suffix(".count")
        return int(counter.read_text()) if counter.exists() else 0

    def effect(self, handoff, *, outcome="applied"):
        if self.path.exists():
            body = json.loads(self.path.read_text())
        else:
            role = self.path.with_suffix(".role")
            before = role.read_bytes() if role.exists() else b""
            after = (b"fixture-third-value" if outcome == "uncertain" else b"fixture-after") if outcome in {"applied", "uncertain"} else before
            assert len(before) <= 512
            if after != before:
                self.path.with_suffix(".count").write_text(str(self.writes + 1))
                role.write_bytes(after)
            body = {key: handoff[key] for key in ("contract_version", "installation_ref", "operation_id",
                "step", "review_digest", "expected_revision", "epoch", "effect_id", "challenge")}
            body.update(outcome=outcome, code=None, roles=[{"role": "inventory", "before_digest": digest(before),
                "after_digest": digest(after)}])
            self.path.write_text(json.dumps(body))
        assert self.path.stat().st_size < 4096
        return body

    async def send(self, request, handoff, *, outcome="applied"):
        body = self.effect(handoff, outcome=outcome)
        return await request("POST", RECEIPT, body), body


async def test_c_observer_association_has_zero_configuration_or_manage_effects(installed, served_setup, source):
    daemon, _, root = installed
    observer = token(daemon, "reader", "observe")
    before = (root / "config/hosts.toml").read_bytes()
    body = intent(source)
    _, ident = await accept(served_setup, body, credential=observer)
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "succeeded"
    assert (root / "config/hosts.toml").read_bytes() == before
    assert [s[0] for s in daemon.journal.db.execute("SELECT name FROM operation_steps")] == ["connection_binding"]
    for action in ("setup.connection.apply", "setup.host", "setup.repository", "setup.verification"):
        status, _ = await served_setup("POST", "/api/v1/operations", {**body, "action": action},
            credential=observer, key="refused-" + action)
        assert status == 403
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1


async def test_c_nonpersonal_setup_refused_before_journal(installed, served_setup, source):
    daemon, _, _ = installed
    outsider = token(daemon, "other-person", "observe", "manage")
    body = intent(source, action="setup.connection.apply")
    for path in (PREVIEW, "/api/v1/operations"):
        status, _ = await served_setup("POST", path, body, credential=outsider, key="refused")
        assert status == 403
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()


async def test_e_same_key_lost_reply_and_two_installations(installed, served_setup, source):
    daemon, _, _ = installed
    body, ident = await accept(served_setup, intent(source))
    await settle_operations(daemon.ops)
    status, replay = await served_setup("POST", "/api/v1/operations", body, key="original-key")
    assert status == 200 and replay["operation"]["operation_id"] == ident
    first = daemon.ops.get(ident)["result"]["connection_id"]
    _, second = await accept(served_setup, intent(source, installation="desktop-b"), key="second-key")
    await settle_operations(daemon.ops)
    assert daemon.ops.get(second)["result"]["connection_id"] != first
    for installation in ("desktop-a", "desktop-b"):
        status, rows = await served_setup("GET", "/api/v1/connections?installation_ref=" + installation)
        assert status == 200 and len(rows["connections"]) == 1
        assert rows["connections"][0]["revision"] == 1
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 2


@pytest.mark.parametrize("fault", ["principal", "installation", "endpoint", "pin", "profile", "revision"])
async def test_e_existing_binding_refuses_rebind_or_foreign_access(installed, served_setup, source, fault):
    daemon, principal, root = installed
    _, ident = await accept(served_setup, intent(source))
    await settle_operations(daemon.ops)
    connection = daemon.ops.get(ident)["result"]["connection_id"]
    body = intent(source, connection=connection)
    body["preconditions"]["expected_revision"] = 1
    kwargs = {}
    if fault == "principal":
        kwargs["credential"] = token(daemon, "foreign", "observe")
    elif fault == "installation":
        body["target"]["installation_ref"] = "desktop-b"
    elif fault in {"endpoint", "pin"}:
        path = root / "profiles/index.json"
        index = json.loads(path.read_text())
        index["profiles"][0]["remoteHost" if fault == "endpoint" else "remoteFingerprint"] = (
            "other.example" if fault == "endpoint" else "B" * 64)
        path.write_text(json.dumps(index))
        from bat_agent_connector import setup_connections
        body["params"]["source_reference"] = setup_connections.source_descriptor(
            daemon.ops, principal, body["params"]["source"])["reference"]
        body["params"]["source"] = setup_connections.source_descriptor(daemon.ops, principal, body["params"]["source"])
    elif fault == "profile":
        body["params"]["remote_profile_id"] = "other-profile"
    else:
        body["preconditions"]["expected_revision"] = 0
    status, response = await served_setup("POST", PREVIEW, body, **kwargs)
    assert status in {404, 409}
    if fault in {"endpoint", "pin", "profile"}:
        assert "add a separate connection" in response["error"]["message"]
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1


@pytest.mark.parametrize("phase", ["never_claimed", "after_effect", "late_first", "expired_readback", "uncertain"])
async def test_h_disk_participant_disconnect_expiry_and_third_value(installed, served_setup, source, tmp_path, phase):
    daemon, _, _ = installed
    body, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "waiting_external"
    if phase == "never_claimed":
        assert daemon.ops.get(ident)["external_refs"].get("native_effects_sent") is False
        assert (await served_setup("GET", f"/api/v1/operations/{ident}"))[0] == 200
        assert not (tmp_path / "participant.json").exists()
        status, _ = await served_setup("POST", HANDOFF, {"operation_id": ident, "installation_ref": "desktop-b"})
        assert status == 403
    handoff = await read_handoff(served_setup, ident)
    assert handoff and handoff["step"] == "native_effects"
    participant = DiskParticipant(tmp_path / "participant.json")
    if phase == "after_effect":
        participant.effect(handoff)
        written = participant.path.stat().st_mtime_ns
        await served_setup.disconnect()  # Local effect sent, receipt not delivered.
        assert not daemon.journal.db.execute("SELECT response FROM operation_steps WHERE name='native_effects'").fetchone()[0]
        participant = DiskParticipant(participant.path)
    if phase == "late_first":
        from bat_agent_connector import connection_handoff
        participant.effect(handoff)
        with patch.object(connection_handoff, "now", return_value=handoff["expires_at"] + 1):
            (status, response), receipt = await participant.send(served_setup, handoff)
        assert status == 200, response
        await settle_operations(daemon.ops)
        assert daemon.ops.get(ident)["status"] == "succeeded"
    else:
        (status, response), receipt = await participant.send(served_setup, handoff,
            outcome="uncertain" if phase == "uncertain" else "applied")
        assert status == 200, response
        if phase == "expired_readback":
            from bat_agent_connector import connection_handoff
            with patch.object(connection_handoff, "now", return_value=handoff["expires_at"] + 1):
                (status, _), _ = await participant.send(served_setup, handoff)
            assert status == 200
        await settle_operations(daemon.ops)
        assert daemon.ops.get(ident)["status"] == ("uncertain" if phase == "uncertain" else "succeeded")
        (status, _), again = await participant.send(served_setup, handoff)
        assert status == 200 and again == receipt
        changed = {**receipt, "roles": [{**receipt["roles"][0], "after_digest": "c" * 64}]}
        status, _ = await served_setup("POST", RECEIPT, changed)
        assert status == 409
        if phase == "after_effect":
            assert participant.path.stat().st_mtime_ns == written
    status, replay = await served_setup("POST", "/api/v1/operations", body, key="original-key")
    assert status == 200 and replay["operation"]["operation_id"] == ident
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
    assert participant.writes == 1


async def test_preview_zero_effect_and_update_revision_cas(installed, served_setup, source, monkeypatch):
    daemon, _, root = installed
    async def forbidden(*args):
        pytest.fail("preview must not contact BAT or SSH")
    monkeypatch.setattr(setup, "_probe_host", forbidden)
    monkeypatch.setattr(setup, "_probe_ssh", forbidden)
    files = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file() and "state" not in p.parts}
    body = intent(source)
    reviewed = await review(served_setup, body)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == files
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()
    assert not daemon.journal.db.execute("SELECT 1 FROM operation_steps").fetchone()
    status, response = await served_setup("POST", "/api/v1/operations", reviewed, key="create")
    assert status == 202
    await settle_operations(daemon.ops)
    connection = daemon.ops.get(response["operation"]["operation_id"])["result"]["connection_id"]
    update = intent(source, connection=connection)
    update["params"]["label"] = "Updated label"
    update["preconditions"]["expected_revision"] = 1
    reviewed = await review(served_setup, update)
    status, accepted = await served_setup("POST", "/api/v1/operations", reviewed, key="update")
    assert status == 202
    # Competing intent is admitted against revision 1; its local transaction must fail CAS.
    status, competing = await served_setup("POST", "/api/v1/operations", reviewed, key="competing")
    assert status == 202
    await settle_operations(daemon.ops)
    assert daemon.ops.get(accepted["operation"]["operation_id"])["status"] == "succeeded"
    assert daemon.ops.get(competing["operation"]["operation_id"])["error_code"] == "SETUP_REVIEW_CHANGED"
    status, response = await served_setup("POST", "/api/v1/operations", reviewed, key="stale")
    assert status == 409 and response["error"]["admission_refused"] is True
    _, page = await served_setup("GET", "/api/v1/connections?installation_ref=desktop-a&limit=1")
    assert page["connections"][0]["connection_id"] == connection
    assert page["connections"][0]["revision"] == 2


@pytest.mark.parametrize("fault", ["principal", "installation", "operation", "step", "digest", "revision", "epoch", "challenge", "effect"])
async def test_receipt_is_bound_to_exact_existing_intent(installed, served_setup, source, tmp_path, fault):
    daemon, _, _ = installed
    _, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    native = await read_handoff(served_setup, ident)
    participant = DiskParticipant(tmp_path / "participant.json")
    body = participant.effect(native)
    kwargs = {}
    if fault == "principal":
        kwargs["credential"] = token(daemon, "foreign", "observe", "manage")
    else:
        field, value = {"installation": ("installation_ref", "desktop-b"), "operation": ("operation_id", "op_" + "f" * 32),
            "step": ("step", "managed_configuration"), "digest": ("review_digest", "e" * 64),
            "revision": ("expected_revision", 10), "epoch": ("epoch", 2), "challenge": ("challenge", "e" * 64),
            "effect": ("effect_id", "effect_" + "e" * 64)}[fault]
        body[field] = value
    status, _ = await served_setup("POST", RECEIPT, body, **kwargs)
    assert status in {403, 404, 409}
    assert not daemon.journal.db.execute("SELECT response FROM operation_steps WHERE name='native_effects'").fetchone()[0]
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1


async def test_foreign_principal_cannot_read_replay_or_control_connection(installed, served_setup, source):
    daemon, principal, _ = installed
    body, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    credentials = [token(daemon, "foreign", "observe", "manage"), token(daemon, principal.actor, "observe")]
    for credential in credentials:
        for method, path, payload in (("GET", f"/api/v1/operations/{ident}", None),
                ("POST", "/api/v1/operations", body), ("POST", f"/api/v1/operations/{ident}/cancel", {}),
                ("POST", f"/api/v1/operations/{ident}/resume", {})):
            status, _ = await served_setup(method, path, payload, credential=credential, key="original-key")
            assert status in {403, 404}
        _, page = await served_setup("GET", "/api/v1/operations", credential=credential)
        assert not page["operations"]
        _, page = await served_setup("GET", "/api/v1/connections?installation_ref=desktop-a", credential=credential)
        assert not page["connections"]
    status, _ = await served_setup("POST", "/api/v1/operations", {
        **body, "target": {"installation_ref": "desktop-b"}}, key="original-key")
    assert status == 409  # Same key cannot silently become a second installation's intent.
    assert (await read_handoff(served_setup, ident))["challenge"]


@pytest.mark.parametrize("fault", ["action", "preferences", "connection_id", "service", "workspace", "extra"])
async def test_malformed_or_unreadable_choices_refused_without_operation(installed, served_setup, source, fault):
    daemon, _, _ = installed
    body = intent(source)
    if fault == "action":
        body["action"] = []
    elif fault == "preferences":
        body["params"]["preferences"] = {"raw_toml": "private"}
    elif fault == "connection_id":
        body["params"]["connection_id"] = "conn_" + "1" * 32
    elif fault == "service":
        body["params"]["service_ref"] = "unreadable"
    elif fault == "workspace":
        body["params"]["workspace_ref"] = "unobserved"
    else:
        body["params"]["raw_ssh"] = "private"
    status, _ = await served_setup("POST", PREVIEW, body)
    assert status in {403, 409, 422}
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()


@pytest.mark.parametrize("dispatched", [False, True])
async def test_cancel_cannot_hide_dispatched_effect_or_dispatch_cancelled_step(installed, served_setup, source, dispatched):
    daemon, _, _ = installed
    _, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    if dispatched:
        await read_handoff(served_setup, ident)
    status, response = await served_setup("POST", f"/api/v1/operations/{ident}/cancel", {})
    if dispatched:
        assert status == 409
        assert daemon.ops.get(ident)["status"] == "waiting_external"
        assert not daemon.ops.get(ident)["cancel_requested"]
    else:
        assert status == 200 and response["operation"]["status"] == "cancelled"
        status, _ = await served_setup("POST", HANDOFF, {"operation_id": ident, "installation_ref": "desktop-a"})
        assert status == 409
        assert daemon.ops.get(ident)["external_refs"]["native_effects_sent"] is False


async def test_configuration_is_guarded_inside_same_operation_then_native_wait(installed, served_setup, source, mock):
    from bat_agent_connector import setup_connections
    daemon, principal, root = installed
    params = producer.host_params(daemon, principal, mock)
    selected = {"kind": "staged_secret", "secret_ref": params["secret_ref"],
        "url": params["url"], "fingerprint": params["fingerprint"]}
    descriptor = setup_connections.source_descriptor(daemon.ops, principal, selected)
    source = {**source, "source": selected, "source_reference": descriptor["reference"], "service_ref": "fixture",
        "server_configuration": {"target": {"host": "fixture"}, "params": params,
            "preconditions": {"config_revision": setup._configuration(daemon)[3]}}}
    status, response = await served_setup("POST", PREVIEW, intent(source, action="setup.connection.apply"))
    assert status == 200
    assert {change["field"] for change in response["preview"]["server_configuration"]["changes"]} >= {
        "url", "fingerprint", "writes", "orchestrate", "managed_roots"}
    assert not mock.auth_frames and not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()
    body, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    result = daemon.ops.get(ident)
    assert result["status"] == "waiting_external", result
    assert [step["name"] for step in result["steps"]] == ["managed_configuration", "native_effects"]
    assert setup.tomllib.loads((root / "config/hosts.toml").read_text())["hosts"]["fixture"]["url"] == setup._url(mock.url)
    assert mock.auth_frames
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
    configured = (root / "config/hosts.toml").stat().st_mtime_ns
    calls = len(mock.auth_frames)
    native = await read_handoff(served_setup, ident)
    (status, _), _ = await DiskParticipant(root / "participant.json").send(served_setup, native)
    assert status == 200
    await settle_operations(daemon.ops)
    status, replay = await served_setup("POST", "/api/v1/operations", body, key="original-key")
    assert status == 200 and replay["operation"]["operation_id"] == ident
    assert daemon.ops.get(ident)["status"] == "succeeded"
    assert len(mock.auth_frames) == calls
    assert (root / "config/hosts.toml").stat().st_mtime_ns == configured


@pytest.mark.parametrize("dispatched", [False, True])
async def test_expiry_preserves_unsent_versus_uncertain_transport_fence(installed, served_setup, source, dispatched):
    from bat_agent_connector import connection_handoff
    daemon, _, _ = installed
    _, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    if dispatched:
        await read_handoff(served_setup, ident)
    request = json.loads(daemon.journal.db.execute(
        "SELECT request FROM operation_steps WHERE name='native_effects'").fetchone()[0])
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0")
    with patch.object(connection_handoff, "now", return_value=request["expires_at"] + 1):
        await settle_operations(daemon.ops)
    op = daemon.ops.get(ident)
    assert op["status"] == ("uncertain" if dispatched else "waiting_external")
    assert op["external_refs"]["native_effects_sent"] is dispatched
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
    assert daemon.journal.db.execute("SELECT count(*) FROM operation_steps WHERE name='native_effects'").fetchone()[0] == 1


async def test_restart_recovers_original_challenge_receipt_and_projection(installed, served_setup, source, tmp_path):
    from bat_agent_connector.config import parse_config
    from bat_agent_connector.task_daemon import TaskDaemon
    daemon, principal, root = installed
    body, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    native = await read_handoff(served_setup, ident)
    participant = DiskParticipant(tmp_path / "participant.json")
    participant.effect(native)
    written = participant.path.stat().st_mtime_ns
    bearer = token(daemon, principal.actor, *principal.scopes)
    await daemon.ops.drain()
    await daemon.fleet.close()
    await daemon.inventory.close()
    daemon.journal.close()
    path = root / "config/hosts.toml"
    restarted = TaskDaemon(parse_config(setup.tomllib.loads(path.read_text()), path), root / "state/tasks.sqlite3")
    restarted.managed_installation = dict(daemon.managed_installation)
    setup.install(restarted)
    server = await asyncio.start_server(restarted._handle, "127.0.0.1", 0)
    async def request(method, route, body=None, *, key=None):
        return await http(server.sockets[0].getsockname()[1], method, route, tok=bearer, body=body,
            headers={"Idempotency-Key": key} if key else None)
    try:
        restored = await read_handoff(request, ident)
        assert restored == native
        status, replay = await request("POST", "/api/v1/operations", body, key="original-key")
        assert status == 200 and replay["operation"]["operation_id"] == ident
        (status, _), _ = await DiskParticipant(participant.path).send(request, restored)
        assert status == 200
        await settle_operations(restarted.ops)
        assert restarted.ops.get(ident)["status"] == "succeeded"
        _, page = await request("GET", "/api/v1/connections?installation_ref=desktop-a")
        assert len(page["connections"]) == 1 and page["connections"][0]["receipt_refs"]
        assert participant.path.stat().st_mtime_ns == written
        assert participant.writes == 1
        assert restarted.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
        assert restarted.journal.db.execute("SELECT count(*) FROM operation_steps WHERE name='connection_binding'").fetchone()[0] == 1
    finally:
        server.close()
        await server.wait_closed()
        await restarted.ops.drain()
        await restarted.fleet.close()
        await restarted.inventory.close()
        restarted.journal.close()


def test_connection_ddl_is_empty_and_idempotent_without_new_data_step(tmp_path):
    from bat_agent_connector.task_journal import LATEST_DATA_STEP, Journal
    path = tmp_path / "journal.db"
    journal = Journal(path)
    version = journal.db.execute("PRAGMA user_version").fetchone()[0]
    ddl = journal.db.execute("SELECT sql FROM sqlite_master WHERE name='connection_bindings'").fetchone()[0]
    assert journal.db.execute("SELECT count(*) FROM connection_bindings").fetchone()[0] == 0
    journal.close()
    journal = Journal(path)
    try:
        assert LATEST_DATA_STEP == 3
        assert journal.db.execute("PRAGMA user_version").fetchone()[0] == version
        assert journal.db.execute("SELECT sql FROM sqlite_master WHERE name='connection_bindings'").fetchone()[0] == ddl
        assert journal.db.execute("SELECT count(*) FROM connection_bindings").fetchone()[0] == 0
    finally:
        journal.close()


async def test_native_final_receipt_loss_keeps_original_uncertain_then_recovers(installed, served_setup, source, tmp_path, monkeypatch):
    daemon, _, _ = installed
    body, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    native = await read_handoff(served_setup, ident)
    participant = DiskParticipant(tmp_path / "participant.json")
    (status, _), _ = await participant.send(served_setup, native)
    assert status == 200
    written = participant.path.stat().st_mtime_ns
    original = daemon.ops._step_done
    def lose_receipt(operation, step, response, **kwargs):
        if step == "native_effects":
            raise OSError("synthetic loss of final journal receipt")
        return original(operation, step, response, **kwargs)
    monkeypatch.setattr(daemon.ops, "_step_done", lose_receipt)
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "uncertain"
    monkeypatch.setattr(daemon.ops, "_step_done", original)
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (ident,))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "succeeded"
    (status, _), _ = await participant.send(served_setup, native)
    assert status == 200 and participant.path.stat().st_mtime_ns == written
    status, replay = await served_setup("POST", "/api/v1/operations", body, key="original-key")
    assert status == 200 and replay["operation"]["operation_id"] == ident


@pytest.mark.parametrize("outcome", ["unchanged", "refused"])
async def test_native_definitive_no_effect_outcomes_and_refusal_resume(installed, served_setup, source, tmp_path, outcome):
    daemon, _, _ = installed
    _, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    native = await read_handoff(served_setup, ident)
    participant = DiskParticipant(tmp_path / "participant.json")
    (status, _), _ = await participant.send(served_setup, native, outcome=outcome)
    assert status == 200
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == ("succeeded" if outcome == "unchanged" else "failed")
    if outcome == "refused":
        status, _ = await served_setup("POST", f"/api/v1/operations/{ident}/resume", {})
        assert status == 409
        assert daemon.ops.get(ident)["error_code"] == "CONNECTION_NATIVE_REFUSED"
    assert daemon.journal.db.execute("SELECT count(*) FROM operation_steps WHERE name='native_effects'").fetchone()[0] == 1


async def test_uncertain_budget_and_resume_never_renew_challenge_or_effect(installed, served_setup, source, tmp_path):
    from bat_agent_connector.operations import UNCERTAIN_RETRY_S
    daemon, _, _ = installed
    _, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    native = await read_handoff(served_setup, ident)
    participant = DiskParticipant(tmp_path / "participant.json")
    (status, _), _ = await participant.send(served_setup, native, outcome="uncertain")
    assert status == 200
    for _ in range(len(UNCERTAIN_RETRY_S) + 1):
        daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (ident,))
        await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "needs_attention"
    assert daemon.ops.get(ident)["error_code"] == "UNCERTAIN_UNRESOLVED"
    status, _ = await served_setup("POST", f"/api/v1/operations/{ident}/resume", {})
    assert status == 200
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "uncertain"
    restored = await read_handoff(served_setup, ident)
    assert all(restored[key] == native[key] for key in native if key != "receipt")
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1


async def test_challenge_is_private_and_dispatch_read_reports_persisted_fence(installed, served_setup, source, tmp_path):
    daemon, _, _ = installed
    _, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    status, response = await served_setup("POST", HANDOFF, {"operation_id": ident, "installation_ref": "desktop-a"})
    assert status == 200 and daemon.ops.get(ident)["external_refs"]["native_effects_sent"] is True
    native = response["native_handoff"]
    assert set(native) == {"contract_version", "server_id", "principal_id", "installation_ref", "operation_id", "step",
        "review_digest", "expected_revision", "epoch", "effect_id", "expires_at", "challenge", "receipt"}
    assert native["challenge"] not in "\n".join(daemon.journal.db.iterdump())
    for route in (f"/api/v1/operations/{ident}", "/api/v1/operations", "/api/v1/bootstrap"):
        status, response = await served_setup("GET", route)
        assert status == 200 and native["challenge"] not in json.dumps(response)
    (status, response), _ = await DiskParticipant(tmp_path / "participant.json").send(served_setup, native)
    assert status == 200 and native["challenge"] not in json.dumps(response)
    assert native["challenge"] not in "\n".join(daemon.journal.db.iterdump())


async def test_observer_reference_does_not_require_personal_managed_installation(installed, served_setup, source):
    daemon, _, root = installed
    daemon.managed_installation = None
    before = (root / "config/hosts.toml").read_bytes()
    observer = token(daemon, "reader", "observe")
    _, ident = await accept(served_setup, intent(source), credential=observer)
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "succeeded"
    assert (root / "config/hosts.toml").read_bytes() == before
    status, _ = await served_setup("POST", PREVIEW, intent(source, action="setup.connection.apply"))
    assert status == 403


@pytest.mark.parametrize("phase", ["before_execution", "during_probe"])
async def test_source_snapshot_guard_precedes_configuration_publication(installed, served_setup, source, mock, monkeypatch, phase):
    from urllib.parse import urlsplit

    from bat_agent_connector import setup_connections
    daemon, principal, root = installed
    path = root / "profiles/index.json"
    index = json.loads(path.read_text())
    endpoint = urlsplit(mock.url)
    index["profiles"][0].update(remoteHost=endpoint.hostname, remotePort=endpoint.port, remoteFingerprint=mock.fingerprint)
    path.write_text(json.dumps(index))
    platform_files.atomic_write(root / "profiles/remote-tokens.enc.json", json.dumps({"enc": False,
        "data": json.dumps({"tokens": {"profile-a": mock.token}})}).encode())
    descriptor = setup_connections.source_descriptor(daemon.ops, principal, source["source"])
    source = {**source, "source_reference": descriptor["reference"], "service_ref": "fixture",
        "server_configuration": {"target": {"host": "fixture"}, "params": {"import_profile_id": "profile-a"},
            "preconditions": {"config_revision": setup._configuration(daemon)[3]}}}
    _, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    before = (root / "config/hosts.toml").read_bytes()
    def drift():
        index["profiles"][0]["name"] = "Changed source snapshot"
        path.write_text(json.dumps(index))
    if phase == "before_execution":
        drift()
    else:
        original = setup._probe_host
        async def probe(config, host):
            result = await original(config, host)
            drift()
            return result
        monkeypatch.setattr(setup, "_probe_host", probe)
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "failed"
    assert (root / "config/hosts.toml").read_bytes() == before
    if phase == "before_execution":
        assert not mock.auth_frames


async def _r2_dispatch(request, ident):
    status, response = await request("POST", HANDOFF, {"operation_id": ident, "installation_ref": "desktop-a"})
    assert status == 200, response
    return response["native_handoff"]


async def _r2_waiting(installed, request, source, *, connection=None):
    body = intent(source, action="setup.connection.apply", connection=connection)
    if connection:
        body["preconditions"]["expected_revision"] = 1
    body, ident = await accept(request, body, key="pending")
    await settle_operations(installed[0].ops)
    return body, ident


@pytest.mark.parametrize("outcome", ["applied", "unchanged", "refused", "uncertain"])
async def test_r2_a_late_first_receipt(installed, served_setup, source, tmp_path, outcome):
    from bat_agent_connector import connection_handoff
    daemon, _, _ = installed
    _, ident = await _r2_waiting(installed, served_setup, source)
    native = await _r2_dispatch(served_setup, ident)
    participant = DiskParticipant(tmp_path / "participant.json")
    receipt = participant.effect(native, outcome=outcome)  # Effect/evidence precedes laptop sleep.
    with patch.object(connection_handoff, "now", return_value=native["expires_at"] + 1):
        status, response = await served_setup("POST", RECEIPT, receipt)
    assert status == 200, response
    await settle_operations(daemon.ops)
    expected = {"applied": "succeeded", "unchanged": "succeeded", "refused": "failed", "uncertain": "uncertain"}
    assert daemon.ops.get(ident)["status"] == expected[outcome]
    assert participant.writes <= 1


@pytest.mark.parametrize("update", [False, True])
async def test_r2_a_abandonment_releases_busy_and_stop(installed, served_setup, source, update):
    from bat_agent_connector import connection_handoff
    from bat_agent_connector.browser_sessions import BrowserSessions
    from bat_agent_connector.operations import UNCERTAIN_RETRY_S
    daemon, principal, root = installed
    daemon.api.browser_sessions = BrowserSessions(daemon, root, daemon.managed_installation["installation_id"])
    connection = None
    if update:
        _, first = await accept(served_setup, intent(source), key="initial")
        await settle_operations(daemon.ops)
        connection = daemon.ops.get(first)["result"]["connection_id"]
    _, ident = await _r2_waiting(installed, served_setup, source, connection=connection)
    native = await _r2_dispatch(served_setup, ident)
    with patch.object(connection_handoff, "now", return_value=native["expires_at"] + 1):
        for _ in range(len(UNCERTAIN_RETRY_S) + 1):
            daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (ident,))
            await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "needs_attention"
    status, response = await served_setup("POST", "/api/v1/managed/stop", {})
    assert status == 409 and response["error"]["code"] == "SERVICE_BUSY"
    status, response = await served_setup("POST", f"/api/v1/operations/{ident}/cancel", {})
    assert status == 200, response
    assert response["operation"]["status"] == "cancelled"
    row = daemon.journal.db.execute("SELECT response,error FROM operation_steps WHERE operation_id=? AND name='native_effects'", (ident,)).fetchone()
    assert "LOCAL_EFFECT_NEVER_PROVEN" in str(tuple(row))
    assert not setup._busy(daemon)
    rows = daemon.journal.db.execute("SELECT * FROM connection_bindings").fetchall()
    assert len(rows) == int(update)
    if update:
        assert rows[0]["revision"] == 1 and rows[0]["connection_id"] == connection
    body = {key: native[key] for key in ("contract_version", "installation_ref", "operation_id", "step",
        "review_digest", "expected_revision", "epoch", "effect_id", "challenge")}
    body.update(outcome="refused", code=None, roles=[{"role": "inventory", "before_digest": "a" * 64, "after_digest": "a" * 64}])
    status, response = await served_setup("POST", RECEIPT, body)
    assert status == 409 and response["error"]["code"] == "CONNECTION_OPERATION_TERMINAL"
    assert daemon.ops.get(ident)["status"] == "cancelled"
    assert (await served_setup("POST", PREVIEW, intent(source, action="setup.connection.apply")))[0] == 200
    daemon.managed_stop = asyncio.Event()
    status, response = await served_setup("POST", "/api/v1/managed/stop", {})
    assert status == 200, response


@pytest.mark.parametrize("update", [False, True])
@pytest.mark.parametrize("end", ["cancelled", "failed"])
async def test_r2_a_projection_follows_terminal_operation(installed, served_setup, source, monkeypatch, update, end):
    from bat_agent_connector import connection_handoff
    from bat_agent_connector.operations import OperationError
    daemon, _, _ = installed
    connection = None
    if update:
        _, first = await accept(served_setup, intent(source), key="initial")
        await settle_operations(daemon.ops)
        connection = daemon.ops.get(first)["result"]["connection_id"]
    if end == "failed":
        def refuse(ctx):
            raise OperationError("FIXTURE_PARTICIPANT_REFUSED", "Synthetic participant preparation failure", 409)
        monkeypatch.setattr(connection_handoff, "prepare", refuse)
    _, ident = await _r2_waiting(installed, served_setup, source, connection=connection)
    if end == "cancelled":
        assert (await served_setup("POST", f"/api/v1/operations/{ident}/cancel", {}))[0] == 200
    assert daemon.ops.get(ident)["status"] == end
    rows = daemon.journal.db.execute("SELECT * FROM connection_bindings").fetchall()
    assert len(rows) == int(update)
    if update:
        assert rows[0]["revision"] == 1 and rows[0]["connection_id"] == connection


@pytest.mark.parametrize("outcome", ["applied", "unchanged", "refused"])
async def test_r2_b_definitive_readback_appends_uncertain_history(installed, served_setup, source, tmp_path, outcome):
    daemon, _, _ = installed
    _, ident = await _r2_waiting(installed, served_setup, source)
    native = await _r2_dispatch(served_setup, ident)
    uncertain = DiskParticipant(tmp_path / "participant.json").effect(native, outcome="uncertain")
    assert (await served_setup("POST", RECEIPT, uncertain))[0] == 200
    await settle_operations(daemon.ops)
    assert (await served_setup("POST", f"/api/v1/operations/{ident}/cancel", {}))[0] == 409
    definitive = {**uncertain, "outcome": outcome, "roles": [{**uncertain["roles"][0],
        "after_digest": uncertain["roles"][0]["before_digest"] if outcome in {"unchanged", "refused"} else digest(b"settled")} ]}
    status, response = await served_setup("POST", RECEIPT, definitive)
    assert status == 200, response
    await settle_operations(daemon.ops)
    row = daemon.journal.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='native_effects'", (ident,)).fetchone()
    history = json.loads(row[0])["receipts"]
    assert [item["outcome"] for item in history] == ["uncertain", outcome]
    assert history[0]["roles"] == uncertain["roles"]
    for body in (uncertain, definitive):
        assert (await served_setup("POST", RECEIPT, body))[0] == 200
    assert len(json.loads(daemon.journal.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='native_effects'", (ident,)).fetchone()[0])["receipts"]) == 2
    changed = {**definitive, "roles": [{**definitive["roles"][0], "after_digest": "f" * 64}]}
    assert (await served_setup("POST", RECEIPT, changed))[0] in {409, 422}
    assert daemon.ops.get(ident)["status"] == ("failed" if outcome == "refused" else "succeeded")


@pytest.mark.parametrize("change", ["index", "token"])
async def test_r2_s2_unrelated_source_refresh_allows_reference_update(installed, served_setup, source, change):
    from bat_agent_connector import setup_connections
    daemon, principal, root = installed
    _, first = await accept(served_setup, intent(source), key="first")
    await settle_operations(daemon.ops)
    connection = daemon.ops.get(first)["result"]["connection_id"]
    if change == "index":
        path = root / "profiles/index.json"
        index = json.loads(path.read_text())
        index["profiles"].append({"id": "unrelated", "name": "Unrelated profile"})
        path.write_text(json.dumps(index))
    else:
        platform_files.atomic_write(root / "profiles/remote-tokens.enc.json", json.dumps({"enc": False,
            "data": json.dumps({"tokens": {"profile-a": "refreshed-synthetic-token"}})}).encode())
    body = intent(source, connection=connection)
    body["params"]["source_reference"] = setup_connections.source_descriptor(daemon.ops, principal, source["source"])["reference"]
    body["params"]["label"] = "Updated after source refresh"
    body["preconditions"]["expected_revision"] = 1
    await accept(served_setup, body, key="update")
    await settle_operations(daemon.ops)
    assert daemon.journal.db.execute("SELECT revision FROM connection_bindings").fetchone()[0] == 2


async def test_r2_s2_reference_update_survives_expired_staged_secret(installed, served_setup, source, mock):
    from bat_agent_connector import setup_connections
    daemon, principal, root = installed
    params = producer.host_params(daemon, principal, mock)
    selected = {"kind": "staged_secret", "secret_ref": params["secret_ref"], "url": params["url"], "fingerprint": params["fingerprint"]}
    descriptor = setup_connections.source_descriptor(daemon.ops, principal, selected)
    body = intent({**source, "source": selected, "source_reference": descriptor["reference"]})
    _, first = await accept(served_setup, body, key="first")
    await settle_operations(daemon.ops)
    connection = daemon.ops.get(first)["result"]["connection_id"]
    path = root / "config/setup-secrets" / (params["secret_ref"] + ".json")
    metadata = json.loads(path.read_text())
    metadata["expires_at"] = 0
    platform_files.atomic_write(path, json.dumps(metadata).encode())
    body["target"]["connection_id"] = connection
    body["preconditions"]["expected_revision"] = 1
    body["params"]["label"] = "Updated without a credential"
    await accept(served_setup, body, key="update")
    await settle_operations(daemon.ops)
    assert daemon.journal.db.execute("SELECT revision FROM connection_bindings").fetchone()[0] == 2


@pytest.mark.parametrize("profile", ["profile-a", "not-present"])
async def test_r2_s3_source_resolution_requires_personal_manage(installed, served_setup, source, profile):
    daemon, _, _ = installed
    body = intent(source)
    body["params"]["source"] = {"kind": "bat_profile", "profile_reference": profile}
    status, _ = await served_setup("POST", PREVIEW, body, credential=token(daemon, "reader", "observe"))
    assert status == 403
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()


async def test_r2_s3_reference_preview_observe_returns_no_source_details(installed, served_setup, source):
    from bat_agent_connector import setup_connections
    daemon, principal, _ = installed
    descriptor = setup_connections.source_descriptor(daemon.ops, principal, source["source"])
    body = intent({**source, "source": descriptor})
    observer = token(daemon, "reader", "observe")
    status, response = await served_setup("POST", PREVIEW, body, credential=observer)
    assert status == 200, response
    assert response["preview"]["source"] is None
    assert descriptor["association"]["url"] not in json.dumps(response)
    assert descriptor["association"]["fingerprint"] not in json.dumps(response)


async def test_r2_s4_reference_update_preserves_receipt_refs(installed, served_setup, source, tmp_path):
    daemon, _, _ = installed
    _, ident = await _r2_waiting(installed, served_setup, source)
    native = await _r2_dispatch(served_setup, ident)
    assert (await DiskParticipant(tmp_path / "participant.json").send(served_setup, native))[0][0] == 200
    await settle_operations(daemon.ops)
    before = dict(daemon.journal.db.execute("SELECT * FROM connection_bindings").fetchone())
    body = intent(source, connection=before["connection_id"])
    body["preconditions"]["expected_revision"] = 1
    body["params"]["label"] = "New display label"
    await accept(served_setup, body, key="update")
    await settle_operations(daemon.ops)
    assert daemon.journal.db.execute("SELECT receipt_refs FROM connection_bindings").fetchone()[0] == before["receipt_refs"]


async def test_r2_create_intent_binding_is_enforced_on_replay(installed, served_setup, source):
    daemon, _, _ = installed
    body, ident = await accept(served_setup, intent(source))
    refs = daemon.ops.get(ident)["external_refs"]
    refs["admission_binding"]["create_intent"] = "0" * 64
    daemon.journal.db.execute("UPDATE operations SET external_refs=? WHERE operation_id=?", (json.dumps(refs), ident))
    status, _ = await served_setup("POST", "/api/v1/operations", body, key="original-key")
    assert status == 409


@pytest.mark.parametrize("restart", [False, True])
async def test_r2_s1_published_configuration_survives_source_drift(installed, served_setup, source, mock, monkeypatch, tmp_path, restart):
    from urllib.parse import urlsplit

    from bat_agent_connector import setup_connections
    from bat_agent_connector.config import parse_config
    from bat_agent_connector.task_daemon import TaskDaemon
    daemon, principal, root = installed
    index_path = root / "profiles/index.json"
    index = json.loads(index_path.read_text())
    endpoint = urlsplit(mock.url)
    index["profiles"][0].update(remoteHost=endpoint.hostname, remotePort=endpoint.port, remoteFingerprint=mock.fingerprint)
    index_path.write_text(json.dumps(index))
    platform_files.atomic_write(root / "profiles/remote-tokens.enc.json", json.dumps({"enc": False,
        "data": json.dumps({"tokens": {"profile-a": mock.token}})}).encode())
    descriptor = setup_connections.source_descriptor(daemon.ops, principal, source["source"])
    body = intent({**source, "source_reference": descriptor["reference"], "service_ref": "fixture",
        "server_configuration": {"target": {"host": "fixture"}, "params": {"import_profile_id": "profile-a"},
            "preconditions": {"config_revision": setup._configuration(daemon)[3]}}}, action="setup.connection.apply")
    original = daemon.ops._step_done
    def publication(operation, step, response, **kwargs):
        if step == "managed_configuration":
            index["profiles"][0]["name"] = "Changed after publication"
            index_path.write_text(json.dumps(index))
            if restart:
                raise OSError("Synthetic loss of configuration receipt before restart")
        return original(operation, step, response, **kwargs)
    monkeypatch.setattr(daemon.ops, "_step_done", publication)
    _, ident = await accept(served_setup, body)
    await settle_operations(daemon.ops)
    path = root / "config/hosts.toml"
    published = path.read_bytes()
    assert "fixture" in setup.tomllib.loads(published.decode())["hosts"]
    if not restart:
        assert daemon.ops.get(ident)["status"] == "waiting_external"
        native = await _r2_dispatch(served_setup, ident)
        assert (await DiskParticipant(tmp_path / "participant.json").send(served_setup, native))[0][0] == 200
        await settle_operations(daemon.ops)
        assert daemon.ops.get(ident)["status"] == "succeeded"
        assert path.read_bytes() == published
        return
    assert daemon.ops.get(ident)["status"] == "uncertain"
    bearer = token(daemon, principal.actor, *principal.scopes)
    await daemon.ops.drain()
    await daemon.fleet.close()
    await daemon.inventory.close()
    daemon.journal.close()
    restored = TaskDaemon(parse_config(setup.tomllib.loads(path.read_text()), path), root / "state/tasks.sqlite3")
    restored.managed_installation = dict(daemon.managed_installation)
    setup.install(restored)
    server = await asyncio.start_server(restored._handle, "127.0.0.1", 0)
    async def request(method, route, body=None, *, key=None):
        return await http(server.sockets[0].getsockname()[1], method, route, tok=bearer, body=body,
            headers={"Idempotency-Key": key} if key else None)
    try:
        restored.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (ident,))
        await settle_operations(restored.ops)
        assert restored.ops.get(ident)["status"] == "waiting_external"
        native = await _r2_dispatch(request, ident)
        assert (await DiskParticipant(tmp_path / "participant.json").send(request, native))[0][0] == 200
        await settle_operations(restored.ops)
        assert restored.ops.get(ident)["status"] == "succeeded"
        assert path.read_bytes() == published
    finally:
        server.close()
        await server.wait_closed()
        await restored.ops.drain()
        await restored.fleet.close()
        await restored.inventory.close()
        restored.journal.close()
