"""Setup draft recovery reads the central ledger without replaying redacted bodies."""
from __future__ import annotations

import json
import time
from urllib.parse import urlencode

import pytest

from bat_agent_connector import managed_setup as setup
from bat_agent_connector import platform_files
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import token
from tests.test_managed_setup import host_params, installed
from tests.test_managed_setup_preview import served_setup

_ = installed, served_setup


async def test_used_and_expired_selector_recovers_by_original_actor_key_without_effects(installed, served_setup, mock):
    daemon, principal, root = installed
    params = host_params(daemon, principal, mock)
    original = {"action": "setup.host", "target": {"host": "first-host"}, "params": params,
                "preconditions": {"config_revision": setup.state(daemon, principal)["revision"]}}
    status, before = await served_setup("GET", "/api/v1/operations")
    assert status == 200 and before["operations"] == []  # staging has no journal operation
    status, first = await served_setup("POST", "/api/v1/operations", original, key="original-setup-key")
    assert status in {200, 202}, first
    await settle_operations(daemon.ops)
    operation_id = first["operation"]["operation_id"]
    assert daemon.ops.get(operation_id)["status"] == "succeeded"

    # There is no redemption/consumption flag: the same selector can be admitted
    # for another target and key while live, under the original identity.
    other = {**original, "target": {"host": "second-host"},
             "preconditions": {"config_revision": setup.state(daemon, principal)["revision"]}}
    manage_only = token(daemon, principal.actor, "manage")
    status, denied = await served_setup("GET", "/api/v1/operations", credential=manage_only)
    assert status == 403, denied
    status, denied = await served_setup("POST", "/api/v1/operations", other, key="second-setup-key", credential=manage_only)
    assert status == 403, denied  # scopes are part of the installation's principal ID
    status, second = await served_setup("POST", "/api/v1/operations", other, key="second-setup-key")
    assert status in {200, 202}, second
    await settle_operations(daemon.ops)
    assert daemon.ops.get(second["operation"]["operation_id"])["status"] == "succeeded"

    metadata_path = root / "config/setup-secrets" / (params["secret_ref"] + ".json")
    metadata = json.loads(metadata_path.read_text())
    metadata["expires_at"] = time.time() - 1
    platform_files.atomic_write(metadata_path, json.dumps(metadata).encode())
    setup.stage_secret(daemon, principal, {"kind": "bat", "value": mock.token})
    assert metadata_path.exists()  # configuration references keep it alive
    probes = len(mock.auth_frames)
    body = {**other, "target": {"host": "third-host"},
            "preconditions": {"config_revision": setup.state(daemon, principal)["revision"]}}
    status, refused = await served_setup("POST", "/api/v1/operations", body, key="expired-new-key")
    assert status == 409 and refused["error"]["code"] == "INVALID_SECRET"

    query = urlencode({"action": "setup.host", "actor": principal.actor, "limit": 200})
    status, page = await served_setup("GET", "/api/v1/operations?" + query)
    assert status == 200
    recovered = [op for op in page["operations"] if op["idempotency_key"] == "original-setup-key"]
    assert len(recovered) == 1 and recovered[0]["operation_id"] == operation_id
    assert {name: recovered[0][name] for name in original} == original
    status, detail = await served_setup("GET", "/api/v1/operations/" + operation_id)
    assert status == 200 and detail["operation"]["params"] == params
    status, replay = await served_setup("POST", "/api/v1/operations", original, key="original-setup-key")
    assert status == 200 and replay["created"] is False and replay["operation"]["operation_id"] == operation_id
    redacted = {**original, "params": {key: value for key, value in params.items() if key != "secret_ref"}}
    status, conflict = await served_setup("POST", "/api/v1/operations", redacted, key="original-setup-key")
    assert status == 409 and conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(mock.auth_frames) == probes
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 2


@pytest.mark.parametrize("remote", [
    "https://fixture-user:synthetic-secret@github.com/o/r.git",
    "https://synthetic-secret@github.com/o/r.git",
    "https://github.com/o/r.git?token=synthetic-secret",
    "ssh://git:synthetic-secret@github.com/o/r.git",
])
async def test_central_refuses_credential_remotes_before_admission(installed, served_setup, mock, remote):
    daemon, principal, _ = installed
    body = {"action": "setup.host", "target": {"host": "fixture"}, "params": host_params(daemon, principal, mock),
            "preconditions": {"config_revision": setup.state(daemon, principal)["revision"]}}
    status, response = await served_setup("POST", "/api/v1/operations", body, key="fixture-host")
    assert status in {200, 202}, response
    await settle_operations(daemon.ops)
    ref = setup.stage_secret(daemon, principal, {"kind": "github", "value": "synthetic-github-token"})["secret_ref"]
    body = {"action": "setup.repository", "target": {"repository": "o/r"}, "params": {
        "host": "fixture", "workspace_id": "ws-1", "remote_url": remote, "secret_ref": ref},
        "preconditions": {"config_revision": setup.state(daemon, principal)["revision"]}}
    probes = len(mock.auth_frames)
    status, response = await served_setup("POST", "/api/v1/operations", body, key="unsafe-repository")
    assert status == 422 and response["error"]["code"] == "INVALID_CONFIGURATION"
    assert response["error"]["admission_refused"] is True
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
    assert len(mock.auth_frames) == probes
