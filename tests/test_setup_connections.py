"""Connection intents through real HTTP, a disk journal and a disk participant."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from bat_agent_connector import managed_setup as setup
from bat_agent_connector import platform_files
from bat_agent_connector.connection_source_descriptor import build_source_descriptor
from tests import test_managed_setup as producer
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import token
from tests.test_managed_setup_preview import served_setup

installed = producer.installed
PREVIEW = "/api/v1/connections/preview"
RECEIPT = "/api/v1/connections/receipt"


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

    async def send(self, request, handoff, *, outcome="applied"):
        if self.path.exists():
            body = json.loads(self.path.read_text())
        else:
            body = {key: handoff[key] for key in ("contract_version", "installation_ref", "operation_id",
                "step", "review_digest", "expected_revision", "epoch", "effect_id", "challenge")}
            body.update(outcome=outcome, roles=[{"role": "inventory", "before_digest": "a" * 64,
                "after_digest": "b" * 64}])
            self.path.write_text(json.dumps(body))
        assert self.path.stat().st_size < 4096
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
    daemon, _, root = installed
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
    elif fault == "profile":
        body["params"]["remote_profile_id"] = "other-profile"
    else:
        body["preconditions"]["expected_revision"] = 0
    status, _ = await served_setup("POST", PREVIEW, body, **kwargs)
    assert status in {404, 409}
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1


@pytest.mark.parametrize("phase", ["before_dispatch", "after_effect", "expired_new", "expired_readback", "uncertain"])
async def test_h_disk_participant_disconnect_expiry_and_third_value(installed, served_setup, source, tmp_path, phase):
    daemon, _, _ = installed
    body, ident = await accept(served_setup, intent(source, action="setup.connection.apply"))
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "waiting_external"
    if phase == "before_dispatch":
        assert daemon.ops.get(ident)["external_refs"].get("native_effects_sent") is False
        assert await read_handoff(served_setup, ident, "desktop-b") is None
    handoff = await read_handoff(served_setup, ident)
    assert handoff and handoff["step"] == "native_effects"
    participant = DiskParticipant(tmp_path / "participant.json")
    if phase == "expired_new":
        # Expire using the module's clock, without replacing stdlib classes.
        from bat_agent_connector import connection_handoff
        from unittest.mock import patch
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
            from unittest.mock import patch
            with patch.object(connection_handoff, "now", return_value=handoff["expires_at"] + 1):
                (status, _), _ = await participant.send(served_setup, handoff)
            assert status == 200
        await settle_operations(daemon.ops)
        assert daemon.ops.get(ident)["status"] == ("uncertain" if phase == "uncertain" else "succeeded")
        (status, _), again = await participant.send(served_setup, handoff)
        assert status == 200 and again == receipt
        changed = {**receipt, "outcome": "refused"}
        status, _ = await served_setup("POST", RECEIPT, changed)
        assert status == 409
    status, replay = await served_setup("POST", "/api/v1/operations", body, key="original-key")
    assert status == 200 and replay["operation"]["operation_id"] == ident
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
