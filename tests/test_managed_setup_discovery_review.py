"""Source discovery and credential snapshot contracts at the HTTP boundary."""
import asyncio
import hashlib
import json

import pytest

from bat_agent_connector import managed_setup as setup
from bat_agent_connector import platform_files
from bat_agent_connector import setup_source_discovery as discovery
from tests import test_managed_setup_discovery as producer
from tests.operation_helpers import settle_operations

installed = producer.installed
served_setup = producer.served_setup


@pytest.mark.parametrize("reported", [{"profiles": None}, {"profiles": [{"id": ["x"]}, {"id": "default"}]}])
@pytest.mark.parametrize("caller", ["apply", "discovery"])
async def test_malformed_profiles_preserve_apply_refusal(installed, served_setup, mock, reported, caller):
    body = producer.candidate(installed, mock)
    mock.handlers["profile:list"] = lambda _: reported
    if caller == "discovery":
        status, result = await served_setup("POST", producer.ROUTE, body)
        assert status == 409 and result["error"]["code"] == "SETUP_PROBE_FAILED", result
    else:
        request = {"action": "setup.host", "target": {"host": "bat"}, "params": {
            **{k: body[k] for k in ("url", "fingerprint", "secret_ref")}, "profile_id": "default"},
            "preconditions": {"config_revision": body["revision"]}}
        status, accepted = await served_setup("POST", "/api/v1/operations", request, key="malformed-profiles")
        assert status == 202, accepted
        await settle_operations(installed[0].ops)
        result = installed[0].ops.get(accepted["operation"]["operation_id"])
        assert result["status"] == "failed" and result["error_code"] == "SETUP_PROBE_FAILED", result
    assert setup._configuration(installed[0])[3] == body["revision"]


@pytest.mark.parametrize("url", ["wss://h:1/?", "wss://h:1/#", "wss://" + "h" * 504 + ":1"])
@pytest.mark.parametrize("caller", ["discovery", "preview", "apply"])
async def test_url_edges_are_invalid_host(installed, served_setup, mock, url, caller):
    body = {**producer.candidate(installed, mock), "url": url}
    request = body
    route = producer.ROUTE
    if caller != "discovery":
        route = "/api/v1/managed/setup/preview" if caller == "preview" else "/api/v1/operations"
        request = {"action": "setup.host", "target": {"host": "bat"}, "params": {
            **{k: body[k] for k in ("url", "fingerprint", "secret_ref")}, "source_digest": "a" * 64},
            "preconditions": {"config_revision": body["revision"]}}
    status, response = await served_setup("POST", route, request, key="url-edge")
    assert status == 422 and response["error"]["code"] == "INVALID_HOST", response
    assert response["error"].get("admission_refused") is (None if caller == "preview" else True)
    assert not mock.auth_frames
    assert not installed[0].journal.db.execute("SELECT 1 FROM operations").fetchone()


async def test_discovery_has_one_total_deadline(installed, served_setup, mock, monkeypatch):
    body = producer.candidate(installed, mock)
    monkeypatch.setattr(discovery, "DISCOVERY_TIMEOUT_S", 0.03, raising=False)
    async def slow(_):
        await asyncio.sleep(0.2)
        return {"profiles": [{"id": "default", "name": "Default"}]}
    mock.handlers["profile:list"] = slow
    status, response = await served_setup("POST", producer.ROUTE, body)
    assert status == 409 and response["error"]["code"] == "SETUP_DISCOVERY_TIMEOUT", response
    assert response["error"]["admission_refused"] is True
    assert not installed[0].journal.db.execute("SELECT 1 FROM operations").fetchone()


async def test_steps_share_remaining_budget_but_apply_keeps_its_timeouts(installed, served_setup, mock, monkeypatch):
    body = producer.candidate(installed, mock)
    monkeypatch.setattr(discovery, "DISCOVERY_TIMEOUT_S", 0.03)
    closed = []
    class Client:
        def __init__(self, host, **kwargs):
            self.host = host
            assert kwargs["allow_writes"] is False and kwargs["allow_orchestrate"] is False
        async def ping(self, timeout):
            assert timeout == 10
            await asyncio.sleep(0.02)
        async def invoke(self, channel, _):
            await asyncio.sleep(0.02)
            return {"profiles": [{"id": "default", "name": "Default"}]} if channel == "profile:list" else {"workspaces": []}
        async def close(self):
            closed.append(True)
    monkeypatch.setattr(setup, "BatClient", Client)
    status, response = await served_setup("POST", producer.ROUTE, body)
    assert status == 409 and response["error"]["code"] == "SETUP_DISCOVERY_TIMEOUT", response
    assert closed == [True]
    _, host = discovery.resolve_source(installed[0], setup._configuration(installed[0])[2], body)
    result = await setup._read_host(host)
    assert result["profile_id"] == "default" and result["workspaces"] == []
    assert closed == [True, True]


async def test_existing_connection_review_reuses_credentials_without_discovery(installed, served_setup, mock, monkeypatch):
    from tests.test_managed_setup import create, host_params, settle
    first = await settle(installed, create(installed, "setup.host", {"host": "bat"}, host_params(*installed[:2], mock)))
    assert first["status"] == "succeeded", first
    calls = []
    async def forbidden(*args, **kwargs):
        pytest.fail("Existing connection review must not discover")
    monkeypatch.setattr(discovery, "discover", forbidden)
    original = setup._read_host
    async def read(*args, **kwargs):
        calls.append(True)
        return await original(*args, **kwargs)
    monkeypatch.setattr(setup, "_read_host", read)
    request = {"action": "setup.host", "target": {"host": "bat"}, "params": {"url": mock.url,
        "fingerprint": mock.fingerprint, "profile_id": "default", "managed_roots": ["/srv/new-managed"]},
        "preconditions": {"config_revision": setup._configuration(installed[0])[3]}}
    status, result = await served_setup("POST", "/api/v1/managed/setup/preview", request)
    assert status == 200 and result["preview"]["source"]["kind"] == "existing_connection", result
    assert calls == []
    request["preconditions"]["review_digest"] = result["preview"]["review_digest"]
    status, accepted = await served_setup("POST", "/api/v1/operations", request, key="existing-credentials")
    assert status == 202, accepted
    await settle_operations(installed[0].ops)
    result = installed[0].ops.get(accepted["operation"]["operation_id"])
    assert result["status"] == "succeeded", result
    assert calls == [True]


@pytest.mark.parametrize("patch", [{"source_digest": None}, {"source_digest": "é" * 64},
    {"profile_id": []}, {"secret_ref": None}, {"import_profile_id": "mixed"}, {"fingerprint": "malformed"}])
async def test_invalid_snapshot_params_are_not_review_changes(installed, served_setup, mock, patch):
    body = producer.candidate(installed, mock)
    request = {"action": "setup.host", "target": {"host": "bat"}, "params": {
        **{k: body[k] for k in ("url", "fingerprint", "secret_ref")}, "source_digest": "a" * 64, **patch},
        "preconditions": {"config_revision": body["revision"]}}
    status, result = await served_setup("POST", "/api/v1/managed/setup/preview", request)
    assert status == 422 and result["error"]["code"] == "INVALID_PARAMS", result
    assert "admission_refused" not in result["error"]


async def test_preview_error_keeps_original_contract(installed, served_setup, mock):
    body = producer.candidate(installed, mock)
    status, result = await served_setup("POST", "/api/v1/managed/setup/preview", {
        "action": "setup.host", "target": {}, "params": {}, "preconditions": {"config_revision": body["revision"]}})
    assert status == 422 and result["error"]["code"] == "INVALID_TARGET", result
    assert set(result["error"]) == {"code", "message"}


@pytest.mark.parametrize("kind", ["staged", "profile"])
async def test_secret_snapshot_is_keyed_and_changes_with_secret(installed, mock, monkeypatch, kind):
    if kind == "staged":
        body = producer.candidate(installed, mock)
        path = installed[2] / "config/setup-secrets" / (body["secret_ref"] + ".token")
    else:
        body, index = producer.import_source(installed, mock)
        path = index.parent / "remote-tokens.enc.json"
    snapshots = []
    original = discovery.build_source_descriptor
    def capture(**kwargs):
        snapshots.append(kwargs["source_state"])
        return original(**kwargs)
    monkeypatch.setattr(discovery, "build_source_descriptor", capture)
    document = setup._configuration(installed[0])[2]
    first, _ = discovery.resolve_source(installed[0], document, body)
    assert snapshots[-1]["credential"] != hashlib.sha256(path.read_bytes()).hexdigest()
    if kind == "staged":
        platform_files.atomic_write(path, path.read_bytes() + b"x")
        metadata_path = path.with_suffix(".json")
        metadata = json.loads(metadata_path.read_text())
        metadata["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        platform_files.atomic_write(metadata_path, json.dumps(metadata).encode())
    else:
        platform_files.atomic_write(path, path.read_bytes().replace(mock.token.encode(), (mock.token + "x").encode()))
    second, _ = discovery.resolve_source(installed[0], document, body)
    assert second["source_digest"] != first["source_digest"]
    assert snapshots[-1]["credential"] != hashlib.sha256(path.read_bytes()).hexdigest()
