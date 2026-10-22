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
    return {"source": {"kind": "bat_profile", "profile_reference": "profile-a"},
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
    status, response = await request("GET", f"/api/v1/operations/{operation}?installation_ref={installation}", **kwargs)
    assert status == 200, response
    return response["operation"].get("native_handoff")


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
            body.update(outcome=outcome, roles=[{"role": "inventory", "before_digest": digest(before),
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
    elif fault == "profile":
        body["params"]["remote_profile_id"] = "other-profile"
    else:
        body["preconditions"]["expected_revision"] = 0
    status, response = await served_setup("POST", PREVIEW, body, **kwargs)
    assert status in {404, 409}
    if fault in {"endpoint", "pin", "profile"}:
        assert "add a separate connection" in response["error"]["message"]
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1


@pytest.mark.parametrize("phase", ["before_dispatch", "after_effect", "expired_new", "expired_readback", "uncertain"])
async def test_h_disk_participant_disconnect_expiry_and_third_value(installed, served_setup, source, tmp_path, phase):
    daemon, _, _ = installed
    body, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "waiting_external"
    if phase == "before_dispatch":
        assert daemon.ops.get(ident)["external_refs"].get("native_effects_sent") is False
        await served_setup.disconnect()
        assert not (tmp_path / "participant.json").exists()
        status, _ = await served_setup("GET", f"/api/v1/operations/{ident}?installation_ref=desktop-b")
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
    if phase == "expired_new":
        # Expire using the module's clock, without replacing stdlib classes.
        from bat_agent_connector import connection_handoff
        with patch.object(connection_handoff, "now", return_value=handoff["expires_at"] + 1):
            (status, _), _ = await participant.send(served_setup, handoff)
        assert status == 409 and not daemon.journal.db.execute(
            "SELECT response FROM operation_steps WHERE name='native_effects'").fetchone()[0]
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
        status, _ = await served_setup("GET", f"/api/v1/operations/{ident}?installation_ref=desktop-a")
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
    body, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    result = daemon.ops.get(ident)
    assert result["status"] == "waiting_external", result
    assert [step["name"] for step in result["steps"]] == ["managed_configuration", "connection_binding", "native_effects"]
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
    assert op["status"] == ("uncertain" if dispatched else "needs_attention")
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
    assert daemon.ops.get(ident)["status"] == ("succeeded" if outcome == "unchanged" else "needs_attention")
    if outcome == "refused":
        status, _ = await served_setup("POST", f"/api/v1/operations/{ident}/resume", {})
        assert status == 200
        await settle_operations(daemon.ops)
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
    status, response = await served_setup("GET", f"/api/v1/operations/{ident}?installation_ref=desktop-a")
    assert status == 200 and response["operation"]["external_refs"]["native_effects_sent"] is True
    native = response["operation"]["native_handoff"]
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
