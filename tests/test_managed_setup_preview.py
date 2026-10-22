"""Reviewed setup through the real HTTP boundary, candidate and durable journal."""
from __future__ import annotations

import asyncio
import copy
import json
import time

import pytest

from bat_agent_connector import managed_setup as setup
from bat_agent_connector import platform_files
from bat_agent_connector.operations import UNCERTAIN_RETRY_S
from tests import test_managed_setup as producer
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token

installed = producer.installed
ROUTE = "/api/v1/managed/setup/preview"


@pytest.fixture
async def served_setup(installed):
    daemon, principal, root = installed
    bearer = token(daemon, principal.actor, *principal.scopes)
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    async def request(method, path, body=None, *, credential=bearer, key=None):
        return await http(server.sockets[0].getsockname()[1], method, path, tok=credential,
                          body=body, headers={"Idempotency-Key": key} if key else None)
    yield request
    server.close()
    await server.wait_closed()


def request_body(installed, mock):
    daemon, principal, _ = installed
    return {"action": "setup.host", "target": {"host": "host-a"},
            "params": producer.host_params(daemon, principal, mock),
            "preconditions": {"config_revision": setup.state(daemon, principal)["revision"]}}


async def reviewed(request, body):
    status, response = await request("POST", ROUTE, body)
    assert status == 200, response
    return {**copy.deepcopy(body), "preconditions": {**body["preconditions"],
            "review_digest": response["preview"]["review_digest"]}}, response["preview"]


async def test_preview_http_is_read_only_and_cancel_has_no_effect(installed, served_setup, mock, monkeypatch):
    daemon, principal, root = installed
    body = request_body(installed, mock)  # Explicit staging is a separate, already completed write.
    unrelated = root / "config" / "other.toml"
    unrelated.write_text("keep = true\n")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file() and "state" not in p.parts}
    async def forbidden(*args):
        pytest.fail("preview must not probe")
    monkeypatch.setattr(setup, "_probe_host", forbidden)
    monkeypatch.setattr(setup, "_probe_ssh", forbidden)
    status, state = await served_setup("GET", "/api/v1/managed/setup")
    assert status == 200 and state["preview_contract_version"] == 1
    apply, preview = await reviewed(served_setup, body)
    assert preview["contract_version"] == 1 and preview["action"] == "setup.host"
    assert preview["target"] == body["target"] and preview["config_revision"] == body["preconditions"]["config_revision"]
    assert preview["source"] == {"kind": "staged_secret", "reference": body["params"]["secret_ref"], "available": True}
    assert {change["field"] for change in preview["changes"]} >= {"url", "fingerprint", "profile_id", "writes"}
    assert apply["preconditions"]["review_digest"] == preview["review_digest"]
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()
    assert not daemon.journal.db.execute("SELECT 1 FROM operation_steps").fetchone()
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before
    assert not mock.auth_frames and not mock.invokes
    encoded = json.dumps(preview)
    assert mock.token not in encoded and str(root) not in encoded and "file:" not in encoded
    assert setup.state(daemon, principal)["hosts"] == []  # Cancel is simply no Apply.


@pytest.mark.parametrize("fault", ["scope", "actor", "principal", "unmanaged", "extra", "action", "target", "params", "preconditions", "secret", "path"])
async def test_preview_rejects_authority_and_malformed_requests(installed, served_setup, mock, fault):
    daemon, principal, _ = installed
    body = request_body(installed, mock)
    credential = token(daemon, principal.actor, *principal.scopes)
    if fault == "scope":
        credential = token(daemon, principal.actor, "observe")
    elif fault == "actor":
        credential = token(daemon, "other-person", "manage")
    elif fault == "principal":
        daemon.managed_installation["principal_id"] = "other-principal"
    elif fault == "unmanaged":
        daemon.managed_installation = None
    elif fault == "extra":
        body["raw_toml"] = "unexpected"
    elif fault == "action":
        body["action"] = "setup.repository"
    elif fault in {"target", "params", "preconditions"}:
        body[fault] = []
    else:
        body["params"]["token" if fault == "secret" else "config_path"] = "unaccepted"
    status, response = await served_setup("POST", ROUTE, body, credential=credential)
    assert status in {403, 422}, response
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()
    assert not mock.auth_frames


@pytest.mark.parametrize("fault", ["configuration", "params", "digest", "source"])
async def test_changed_review_is_refused_before_journal_or_probe(installed, served_setup, mock, fault):
    daemon, _, root = installed
    body = request_body(installed, mock)
    if fault == "source":
        directory = root / "profiles"
        directory.mkdir(mode=0o700)
        from urllib.parse import urlsplit
        endpoint = urlsplit(mock.url)
        index = {"profiles": [{"id": "profile-a", "type": "remote", "remoteHost": endpoint.hostname,
                 "remotePort": endpoint.port, "remoteFingerprint": mock.fingerprint, "remoteProfileId": "default"}]}
        (directory / "index.json").write_text(json.dumps(index))
        platform_files.atomic_write(directory / "remote-tokens.enc.json", json.dumps({"enc": False, "data": {"tokens": {"profile-a": mock.token}}}).encode())
        path = root / "config/hosts.toml"
        document = setup.tomllib.loads(path.read_text())
        document["bat"] = {"profiles_dir": str(directory)}
        platform_files.atomic_write(path, setup._encode(document))
        body["params"] = {"import_profile_id": "profile-a"}
        body["preconditions"]["config_revision"] = setup._configuration(daemon)[3]
    apply, _ = await reviewed(served_setup, body)
    if fault == "configuration":
        with (root / "config/hosts.toml").open("ab") as output:
            output.write(b"# concurrent edit\n")
    elif fault == "params":
        apply["params"]["shared_clone_worktrees"] = True
    elif fault == "digest":
        apply["preconditions"]["review_digest"] = "0" * 64
    else:
        index["profiles"][0]["remotePort"] += 1
        (directory / "index.json").write_text(json.dumps(index))
    status, response = await served_setup("POST", "/api/v1/operations", apply, key="changed-review")
    assert status == 409 and response["error"]["code"] in {"CONFIGURATION_CHANGED", "SETUP_REVIEW_CHANGED"}, response
    assert response["error"]["admission_refused"] is True
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()
    assert not mock.auth_frames


async def test_reviewed_apply_reply_and_final_receipt_loss_keep_one_effect(installed, served_setup, mock, monkeypatch):
    daemon, _, root = installed
    apply, preview = await reviewed(served_setup, request_body(installed, mock))
    status, first = await served_setup("POST", "/api/v1/operations", apply, key="original-review")
    assert status == 202, first
    operation_id = first["operation"]["operation_id"]
    original = daemon.ops._step_done
    def lose_receipt(ident, name, response, **kwargs):
        raise OSError("synthetic receipt loss after publication")
    monkeypatch.setattr(daemon.ops, "_step_done", lose_receipt)
    await settle_operations(daemon.ops)
    assert daemon.ops.get(operation_id)["status"] == "uncertain"
    before = (root / "config/hosts.toml").stat().st_mtime_ns
    probes = len(mock.auth_frames)
    status, replay = await served_setup("POST", "/api/v1/operations", apply, key="original-review")
    assert status in {200, 202} and replay["operation"]["operation_id"] == operation_id
    monkeypatch.setattr(daemon.ops, "_step_done", original)
    daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (operation_id,))
    await settle_operations(daemon.ops)
    status, result = await served_setup("GET", "/api/v1/operations/" + operation_id)
    assert status == 200 and result["operation"]["status"] == "succeeded"
    assert result["operation"]["external_refs"]["admission_binding"]["review_digest"] == preview["review_digest"]
    assert len(mock.auth_frames) == probes and (root / "config/hosts.toml").stat().st_mtime_ns == before
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1


async def test_third_value_readback_keeps_original_uncertain_and_busy(installed, served_setup, mock, monkeypatch):
    daemon, principal, root = installed
    apply, _ = await reviewed(served_setup, request_body(installed, mock))
    _, created = await served_setup("POST", "/api/v1/operations", apply, key="uncertain-review")
    ident = created["operation"]["operation_id"]
    original = daemon.ops._step_done
    def lose_receipt(*args, **kwargs):
        raise OSError("synthetic receipt loss")
    monkeypatch.setattr(daemon.ops, "_step_done", lose_receipt)
    await settle_operations(daemon.ops)
    monkeypatch.setattr(daemon.ops, "_step_done", original)
    with (root / "config/hosts.toml").open("ab") as output:
        output.write(b"# third value\n")
    probes = len(mock.auth_frames)
    for attempt in range(1, len(UNCERTAIN_RETRY_S) + 1):
        daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (ident,))
        await settle_operations(daemon.ops)
        op = daemon.ops.get(ident)
        if attempt < len(UNCERTAIN_RETRY_S):
            assert op["status"] == "uncertain" and op["next_run_at"] > time.time()
        else:
            assert op["status"] == "needs_attention" and op["error_code"] == "UNCERTAIN_UNRESOLVED"
        body = {**apply, "preconditions": {"config_revision": setup.state(daemon, principal)["revision"]}}
        status, response = await served_setup("POST", ROUTE, body)
        assert status == 409 and response["error"]["code"] == "SETUP_BUSY"
    assert len(mock.auth_frames) == probes


async def test_reviewed_noop_preserves_live_client_bytes_and_ids(installed, served_setup, mock):
    daemon, _, root = installed
    body = request_body(installed, mock)
    apply, _ = await reviewed(served_setup, body)
    await served_setup("POST", "/api/v1/operations", apply, key="initial-connection")
    await settle_operations(daemon.ops)
    client = daemon.fleet.client("host-a")
    await client.ping()
    path = root / "config/hosts.toml"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    identity = daemon.journal.db.execute("SELECT * FROM api_principals").fetchall()
    body["preconditions"]["config_revision"] = setup._configuration(daemon)[3]
    apply, preview = await reviewed(served_setup, body)
    assert preview["changes"] == []
    await served_setup("POST", "/api/v1/operations", apply, key="same-connection")
    await settle_operations(daemon.ops)
    probes = len(mock.auth_frames)
    await served_setup("POST", "/api/v1/operations", apply, key="same-connection")
    await settle_operations(daemon.ops)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert daemon.fleet.client("host-a") is client and client.connected
    assert len(mock.auth_frames) == probes
    assert daemon.journal.db.execute("SELECT * FROM api_principals").fetchall() == identity
    assert setup.tomllib.loads(path.read_text())["operator_extension"]["keep"] == ["one", "two"]


async def test_preview_uses_cookie_csrf_and_native_bearer_boundaries(installed, mock):
    from bat_agent_connector.browser_sessions import BrowserSessions
    from tests.test_browser_sessions import enter
    from tests.test_browser_sessions import http as browser_http

    daemon, principal, root = installed
    bearer = token(daemon, principal.actor, *principal.scopes)
    # Authenticate the actual parent token before creating its browser handoff.
    from bat_agent_connector.api_auth import authenticate
    personal = authenticate(daemon.journal.db, bearer, daemon._admin_token)
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    daemon._endpoint = f"http://127.0.0.1:{port}/rpc"
    daemon.api.browser_sessions = BrowserSessions(daemon, root, "example-installation")
    try:
        _, cookie, _ = await enter((daemon, personal, bearer, port))
        origin = f"http://127.0.0.1:{port}"
        headers = {"Cookie": cookie, "Sec-Fetch-Site": "same-origin", "Referer": origin + "/dashboard/"}
        _, _, session = await browser_http(port, "GET", "/api/v1/browser-session", headers)
        body = json.dumps(request_body(installed, mock)).encode()
        assert (await browser_http(port, "POST", ROUTE, {**headers, "Origin": origin}, body))[0] == 403
        headers.update({"Origin": origin, "X-Batc-CSRF": session["csrf"]})
        status, _, response = await browser_http(port, "POST", ROUTE, headers, body)
        assert status == 200 and response["preview"]["contract_version"] == 1
        assert (await browser_http(port, "POST", ROUTE, {"Authorization": "Bearer " + cookie.split("=", 1)[1]}, body))[0] == 401
        status, _, native = await browser_http(port, "POST", ROUTE, {"Authorization": "Bearer " + bearer}, body)
        assert status == 200 and native == response
        assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()
        assert not mock.auth_frames
    finally:
        server.close()
        await server.wait_closed()
