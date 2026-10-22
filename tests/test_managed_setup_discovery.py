"""Explicit read-only discovery through the real authenticated HTTP boundary."""
import json
import time
from urllib.parse import urlsplit

import pytest

from bat_agent_connector import managed_setup as setup
from bat_agent_connector import platform_files
from tests import test_managed_setup_preview as preview_tests
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import token
from tests.test_connection_sources import windows_readers
from tests.test_managed_setup import host_params

installed = preview_tests.installed
served_setup = preview_tests.served_setup

ROUTE = "/api/v1/managed/setup/discover"


def candidate(installed, mock):
    daemon, principal, _ = installed
    params = host_params(daemon, principal, mock)
    return {"revision": setup._configuration(daemon)[3], **{k: params[k] for k in
        ("url", "fingerprint", "secret_ref")}}


def import_source(installed, mock, *, string_data=True):
    daemon, _, root = installed
    directory = root / "profiles"
    directory.mkdir(mode=0o700)
    endpoint = urlsplit(mock.url)
    index = directory / "index.json"
    index.write_text(json.dumps({"profiles": [{"id": "profile-a", "name": "Source A", "type": "remote",
        "remoteHost": endpoint.hostname, "remotePort": endpoint.port, "remoteFingerprint": mock.fingerprint}]}))
    data = {"tokens": {"profile-a": mock.token}}
    platform_files.atomic_write(directory / "remote-tokens.enc.json", json.dumps({"enc": False,
        "data": json.dumps(data) if string_data else data}).encode())
    path = root / "config/hosts.toml"
    document = setup.tomllib.loads(path.read_text())
    document["bat"] = {"profiles_dir": str(directory)}
    platform_files.atomic_write(path, setup._encode(document))
    return {"revision": setup._configuration(daemon)[3], "import_profile_id": "profile-a"}, index


def snapshot(root, daemon):
    return ({str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*")
        if p.is_file() and "state" not in p.parts}, list(daemon.journal.db.iterdump()))


async def test_discovery_http_reports_server_choices_without_writes(installed, served_setup, mock):
    daemon, _, root = installed
    body = candidate(installed, mock)
    before = snapshot(root, daemon)
    status, result = await served_setup("POST", ROUTE, body)
    assert status == 200, result
    assert result["profiles"] == [{"id": "default", "name": "Default"}]
    assert result["profile_id"] == "default"
    assert result["workspaces"] == [{"workspace_id": "ws-1", "name": "demo-project"}, {"workspace_id": "ws-2", "name": "other"}]
    assert result["source"]["kind"] == "staged_secret"
    assert snapshot(root, daemon) == before
    assert mock.token not in json.dumps(result) and str(root) not in json.dumps(result)
    assert {f["channel"] for f in mock.invokes} == {"profile:list", "workspace:load"}


@pytest.mark.parametrize("at", ["preview", "apply"])
async def test_discovery_source_change_refuses_review(installed, served_setup, mock, at):
    body, index = import_source(installed, mock)
    status, found = await served_setup("POST", ROUTE, body)
    assert status == 200, found
    request = {"action": "setup.host", "target": {"host": "bat"}, "params": {
        "import_profile_id": "profile-a", "profile_id": found["profile_id"], "source_digest": found["source"]["source_digest"]},
        "preconditions": {"config_revision": body["revision"]}}
    if at == "apply":
        status, preview = await served_setup("POST", "/api/v1/managed/setup/preview", request)
        assert status == 200, preview
        request["preconditions"]["review_digest"] = preview["preview"]["review_digest"]
    index.write_text(index.read_text() + "\n")
    probes = len(mock.auth_frames)
    route = "/api/v1/managed/setup/preview" if at == "preview" else "/api/v1/operations"
    status, response = await served_setup("POST", route, request, key="changed-source")
    assert status == 409 and response["error"]["code"] == "SETUP_REVIEW_CHANGED", response
    assert response["error"].get("admission_refused") is (None if at == "preview" else True)
    assert not installed[0].journal.db.execute("SELECT 1 FROM operations").fetchone()
    assert len(mock.auth_frames) == probes


async def test_discovery_expired_secret_is_refused(installed, served_setup, mock):
    body = candidate(installed, mock)
    path = installed[2] / "config/setup-secrets" / (body["secret_ref"] + ".json")
    metadata = json.loads(path.read_text())
    metadata["expires_at"] = time.time() - 1
    platform_files.atomic_write(path, json.dumps(metadata).encode())
    status, response = await served_setup("POST", ROUTE, body)
    assert status == 409 and response["error"]["code"] == "INVALID_SECRET", response
    assert response["error"]["admission_refused"] is True
    assert not mock.auth_frames


@pytest.mark.parametrize("string_data", [False, True])
async def test_discovery_uses_source_policy_admission(installed, served_setup, mock, string_data):
    body, _ = import_source(installed, mock, string_data=string_data)
    status, response = await served_setup("POST", ROUTE, body)
    if string_data:
        assert status == 200 and response["source"]["policy_version"] == 1, response
    else:
        assert status == 409 and response["error"]["code"] == "PROFILE_UNAVAILABLE", response
        assert response["error"]["admission_refused"] is True
        assert not mock.auth_frames


@pytest.mark.parametrize("fault", ["scope", "actor", "principal", "unmanaged", "mixed", "extra", "revision", "query", "method", "pin", "invalid_pin"])
async def test_discovery_authority_and_fixed_inputs(installed, served_setup, mock, fault):
    daemon, principal, _ = installed
    body = candidate(installed, mock)
    route, method = ROUTE, "POST"
    credential = token(daemon, principal.actor, *principal.scopes)
    if fault == "scope":
        credential = token(daemon, principal.actor, "observe")
    elif fault == "actor":
        credential = token(daemon, "other-person", "manage")
    elif fault == "principal":
        daemon.managed_installation["principal_id"] = "other-principal"
    elif fault == "unmanaged":
        daemon.managed_installation = None
    elif fault == "mixed":
        body["import_profile_id"] = "profile-a"
    elif fault == "extra":
        body["token"] = "never-accepted"
    elif fault == "revision":
        body["revision"] = "0" * 64
    elif fault == "query":
        route += "?host=other"
    elif fault == "method":
        method = "GET"
    elif fault == "pin":
        body["fingerprint"] = "0" * 64
    elif fault == "invalid_pin":
        body["fingerprint"] = "malformed"
    status, response = await served_setup(method, route, body, credential=credential)
    assert status in {403, 405, 409, 422}, response
    assert not mock.auth_frames
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()


@pytest.mark.parametrize("at", ["preview", "admission", "execution"])
async def test_expiry_invalidates_discovered_snapshot(installed, served_setup, mock, at):
    body = candidate(installed, mock)
    _, found = await served_setup("POST", ROUTE, body)
    request = {"action": "setup.host", "target": {"host": "bat"}, "params": {
        **{k: body[k] for k in ("url", "fingerprint", "secret_ref")}, "profile_id": "default",
        "source_digest": found["source"]["source_digest"]}, "preconditions": {"config_revision": body["revision"]}}
    if at != "preview":
        status, response = await served_setup("POST", "/api/v1/managed/setup/preview", request)
        assert status == 200, response
        request["preconditions"]["review_digest"] = response["preview"]["review_digest"]
    if at == "execution":
        status, operation = await served_setup("POST", "/api/v1/operations", request, key="expiry-execution")
        assert status == 202, operation
    path = installed[2] / "config/setup-secrets" / (body["secret_ref"] + ".json")
    metadata = json.loads(path.read_text())
    metadata["expires_at"] = time.time() - 1
    platform_files.atomic_write(path, json.dumps(metadata).encode())
    if at == "execution":
        await settle_operations(installed[0].ops)
        result = installed[0].ops.get(operation["operation"]["operation_id"])
        assert result["status"] == "failed" and result["error_code"] == "SETUP_REVIEW_CHANGED", result
        assert setup._configuration(installed[0])[3] == body["revision"]
    else:
        route = "/api/v1/managed/setup/preview" if at == "preview" else "/api/v1/operations"
        status, response = await served_setup("POST", route, request, key="expiry")
        assert status == 409 and response["error"]["code"] == "SETUP_REVIEW_CHANGED", response
        assert response["error"].get("admission_refused") is (None if at == "preview" else True)


async def test_new_windows_data_directory_invalidates_discovery(installed, served_setup, mock, monkeypatch):
    body, index = import_source(installed, mock)
    roaming = installed[2] / "roaming"
    windows_readers(monkeypatch, roaming)
    legacy = roaming / "org.tonyq.better-agent-terminal"
    legacy.mkdir(parents=True)
    index.parent.rename(legacy / "profiles")
    path = installed[2] / "config/hosts.toml"
    document = setup.tomllib.loads(path.read_text())
    document.pop("bat")
    platform_files.atomic_write(path, setup._encode(document))
    body["revision"] = setup._configuration(installed[0])[3]
    status, found = await served_setup("POST", ROUTE, body)
    assert status == 200, found
    (roaming / "BetterAgentTerminal").mkdir()
    request = {"action": "setup.host", "target": {"host": "bat"}, "params": {"import_profile_id": "profile-a",
        "profile_id": "default", "source_digest": found["source"]["source_digest"]},
        "preconditions": {"config_revision": body["revision"]}}
    status, response = await served_setup("POST", "/api/v1/managed/setup/preview", request)
    assert status == 409 and response["error"]["code"] == "SETUP_REVIEW_CHANGED", response


async def test_selected_profile_reaches_workspace_read_and_apply(installed, served_setup, mock):
    body = candidate(installed, mock)
    mock.handlers["profile:list"] = lambda _: {"profiles": [{"id": "default", "name": "Default"}, {"id": "work", "name": "Work"}]}
    status, initial = await served_setup("POST", ROUTE, body)
    assert status == 200 and initial["profile_id"] is None and initial["workspaces"] == []
    assert not any(frame["channel"] == "workspace:load" for frame in mock.invokes)
    status, found = await served_setup("POST", ROUTE, {**body, "profile_id": "work"})
    assert status == 200 and found["profile_id"] == "work", found
    assert found["source"] == initial["source"]  # Server selection deliberately excluded from source digest.
    assert mock.invokes[-1]["params"]["profileId"] == "work"
    request = {"action": "setup.host", "target": {"host": "bat"}, "params": {
        **{k: body[k] for k in ("url", "fingerprint", "secret_ref")}, "profile_id": "work",
        "source_digest": found["source"]["source_digest"]}, "preconditions": {"config_revision": body["revision"]}}
    status, preview = await served_setup("POST", "/api/v1/managed/setup/preview", request)
    assert status == 200, preview
    request["preconditions"]["review_digest"] = preview["preview"]["review_digest"]
    status, operation = await served_setup("POST", "/api/v1/operations", request, key="selected-profile")
    assert status == 202, operation
    await settle_operations(installed[0].ops)
    result = installed[0].ops.get(operation["operation"]["operation_id"])
    assert result["status"] == "succeeded", result
    assert installed[0].fleet.config.host("bat").profile_id == "work"


async def test_discovered_preview_and_get_stay_probe_free(installed, served_setup, mock, monkeypatch):
    body = candidate(installed, mock)
    _, found = await served_setup("POST", ROUTE, body)
    async def forbidden(*args, **kwargs):
        pytest.fail("GET and preview must never read BAT or SSH")
    monkeypatch.setattr(setup, "_read_host", forbidden)
    monkeypatch.setattr(setup, "_probe_host", forbidden)
    monkeypatch.setattr(setup, "_probe_ssh", forbidden)
    probes = len(mock.auth_frames)
    status, state = await served_setup("GET", "/api/v1/managed/setup")
    assert status == 200 and state["discovery_contract_version"] == 1
    status, response = await served_setup("POST", "/api/v1/managed/setup/preview", {
        "action": "setup.host", "target": {"host": "bat"}, "params": {
            **{k: body[k] for k in ("url", "fingerprint", "secret_ref")}, "profile_id": "default",
            "source_digest": found["source"]["source_digest"]}, "preconditions": {"config_revision": body["revision"]}})
    assert status == 200 and response["preview"]["effects"]["probe_started"] is False, response
    assert len(mock.auth_frames) == probes


async def test_source_change_during_apply_prevents_publication(installed, served_setup, mock, monkeypatch):
    body, index = import_source(installed, mock)
    _, found = await served_setup("POST", ROUTE, body)
    request = {"action": "setup.host", "target": {"host": "bat"}, "params": {"import_profile_id": "profile-a",
        "profile_id": "default", "source_digest": found["source"]["source_digest"]}, "preconditions": {"config_revision": body["revision"]}}
    _, preview = await served_setup("POST", "/api/v1/managed/setup/preview", request)
    request["preconditions"]["review_digest"] = preview["preview"]["review_digest"]
    original = setup._probe_host
    async def changed(*args):
        result = await original(*args)
        index.write_text(index.read_text() + "\n")
        return result
    monkeypatch.setattr(setup, "_probe_host", changed)
    status, operation = await served_setup("POST", "/api/v1/operations", request, key="final-source-check")
    assert status == 202, operation
    await settle_operations(installed[0].ops)
    result = installed[0].ops.get(operation["operation"]["operation_id"])
    assert result["status"] == "failed" and result["error_code"] == "SETUP_REVIEW_CHANGED", result
    assert setup._configuration(installed[0])[3] == body["revision"]
