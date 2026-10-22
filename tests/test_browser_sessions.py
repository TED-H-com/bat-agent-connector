"""Exercise real HTTP handoff, cookie boundaries, principal identity and revocation."""
import asyncio
import json
import re
import time
from urllib.parse import urlencode

import pytest

from bat_agent_connector import api_auth, dashboard_sync
from bat_agent_connector.browser_sessions import BrowserSessions
from bat_agent_connector.config import Config
from bat_agent_connector.task_daemon import TaskDaemon


async def http(port, method, path, headers=None, raw=b""):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    lines = [f"{method} {path} HTTP/1.1", f"Host: 127.0.0.1:{port}", f"Content-Length: {len(raw)}"]
    lines += [f"{key}: {value}" for key, value in (headers or {}).items()]
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode() + raw)
    await writer.drain()
    try:
        response = await asyncio.wait_for(reader.read(), 10)
    finally:
        writer.close()
        await writer.wait_closed()
    head, _, body = response.partition(b"\r\n\r\n")
    rows = head.decode().split("\r\n")
    return int(rows[0].split()[1]), dict(row.split(": ", 1) for row in rows[1:]), json.loads(body) if body else None


@pytest.fixture
async def browser(tmp_path):
    d = TaskDaemon(Config(hosts={}), tmp_path / "state" / "tasks.db")
    token = api_auth.issue(d.journal.db, "desktop-test", api_auth.SCOPES)
    principal = api_auth.authenticate(d.journal.db, token, d._admin_token)
    service = await asyncio.start_server(d._handle, "127.0.0.1", 0)
    port = service.sockets[0].getsockname()[1]
    d._endpoint = f"http://127.0.0.1:{port}/rpc"
    d.managed_installation = {"installation_id": "test", "actor": principal.actor, "runtime_version": "test"}
    d.api.browser_sessions = BrowserSessions(d, tmp_path, "test")
    yield d, principal, token, port
    service.close()
    await service.wait_closed()
    await d.fleet.close()
    await d.inventory.close()
    d.journal.close()
    d.release_owner()


async def enter(browser):
    d, principal, _, port = browser
    entry = d.api.browser_sessions.create_handoff(principal)
    from pathlib import Path
    path = Path(entry["handoff_file"])
    page = path.read_text()
    ticket = re.search(r'name=ticket value="([^"]+)"', page)[1]
    assert "batc_" not in page
    headers = {"Origin": "null", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
               "Content-Type": "application/x-www-form-urlencoded"}
    status, response, _ = await http(port, "POST", "/api/v1/browser-handoff", headers, urlencode({"ticket": ticket}).encode())
    assert status == 303 and response["Location"] == "/dashboard/" and not path.exists()
    assert "HttpOnly" in response["Set-Cookie"] and "SameSite=Strict" in response["Set-Cookie"]
    return ticket, response["Set-Cookie"].split(";", 1)[0], headers


async def test_handoff_is_single_use_and_cookie_has_same_principal(browser):
    d, principal, _, port = browser
    ticket, cookie, handoff_headers = await enter(browser)
    status, _, _ = await http(port, "POST", "/api/v1/browser-handoff", handoff_headers, urlencode({"ticket": ticket}).encode())
    assert status == 401
    origin = f"http://127.0.0.1:{port}"
    headers = {"Cookie": cookie, "Sec-Fetch-Site": "same-origin", "Referer": origin + "/dashboard/"}
    status, _, session = await http(port, "GET", "/api/v1/browser-session", headers)
    assert status == 200 and session["principal_id"] == dashboard_sync.identity(d.journal, principal)["principal_id"]
    assert (await http(port, "GET", "/api/v1/capabilities", headers))[0] == 403
    headers["X-Batc-CSRF"] = session["csrf"]
    assert (await http(port, "GET", "/api/v1/capabilities", headers))[0] == 200
    assert (await http(port, "GET", "/api/v1/capabilities", {**headers, "Origin": "http://127.0.0.1:9999"}))[0] == 403
    assert (await http(port, "GET", "/api/v1/browser-session", {**headers, "Sec-Fetch-Site": "cross-site"}))[0] == 403
    headers["Origin"] = origin
    assert (await http(port, "POST", "/api/v1/browser-session/logout", headers, b"{}"))[0] == 200
    assert (await http(port, "GET", "/api/v1/capabilities", headers))[0] == 401


async def test_parent_revocation_and_expiry_block_browser_credentials(browser):
    d, _, parent, port = browser
    _, cookie, _ = await enter(browser)
    headers = {"Cookie": cookie, "Sec-Fetch-Site": "same-origin", "Referer": f"http://127.0.0.1:{port}/dashboard/"}
    d.journal.db.execute("UPDATE api_principals SET revoked_at=? WHERE token_hash=?", (time.time(), api_auth._digest(parent)))
    assert (await http(port, "GET", "/api/v1/browser-session", headers))[0] == 401


async def test_cross_origin_handoff_and_disabled_operator_central(browser):
    d, _, _, port = browser
    headers = {"Origin": "https://untrusted.example", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
               "Content-Type": "application/x-www-form-urlencoded"}
    assert (await http(port, "POST", "/api/v1/browser-handoff", headers, b"ticket=bad"))[0] == 403
    del d.api.browser_sessions
    assert (await http(port, "GET", "/api/v1/browser-session"))[0] == 404


@pytest.mark.parametrize("path,body", [
    ("/api/v1/capabilities", None),
    ("/api/v1/operations", {"action": "project.create", "params": {"name": "Example"}}),
    ("/api/v1/operations/op_" + "a" * 32 + "/cancel", {}),
    ("/api/v1/operations/op_" + "a" * 32 + "/resume", {}),
    ("/api/v1/managed/browser-file", {}),
    ("/api/v1/managed/stop", {}),
    ("/api/v1/browser-session", None),
    ("/api/v1/browser-session/logout", {}),
    ("/api/v1/artifacts/uploads/op_" + "a" * 32 + "/content", {}),
    ("/api/v1/events/stream", None),
    ("/rpc", {"method": "op_list"}),
    ("/rpc", {"method": "op_submit", "params": {
        "action": "project.create", "params": {"name": "Example"}, "idempotency_key": "example-create"}}),
])
async def test_browser_credentials_are_not_bearer_credentials(browser, path, body):
    d, _, _, port = browser
    _, cookie, _ = await enter(browser)
    token = cookie.split("=", 1)[1]
    method, raw = ("GET", b"") if body is None else ("POST", json.dumps(body).encode())
    headers = {"Content-Type": "application/json", "Idempotency-Key": "example-create"}
    unknown = await http(port, method, path, {**headers, "Authorization": "Bearer unknown"}, raw)
    actual = await http(port, method, path, {**headers, "Authorization": f"Bearer {token}"}, raw)
    assert (actual[0], actual[2]) == (unknown[0], unknown[2])
    assert d.journal.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


async def test_api_bearers_and_existing_cookie_sessions_keep_their_access(browser):
    d, _, parent, port = browser
    _, cookie, _ = await enter(browser)
    # Reopening the adapter keeps the persisted session and its original identity.
    d.api.browser_sessions = BrowserSessions(d, d.api.browser_sessions.data_dir, "test")
    for path, raw in [("/api/v1/capabilities", b""), ("/rpc", b'{"method":"op_list"}')]:
        assert (await http(port, "POST" if raw else "GET", path,
                           {"Authorization": f"Bearer {parent}"}, raw))[0] == 200
    headers = {"Cookie": cookie, "Sec-Fetch-Site": "same-origin", "Referer": f"http://127.0.0.1:{port}/dashboard/"}
    status, _, session = await http(port, "GET", "/api/v1/browser-session", headers)
    assert status == 200
    headers.update({"X-Batc-CSRF": session["csrf"], "Content-Type": "application/json", "Idempotency-Key": "cookie-create"})
    status, _, result = await http(port, "POST", "/api/v1/operations", headers,
                                  b'{"action":"project.create","params":{"name":"Example"}}')
    assert status == 202
    operation_id = result["operation"]["operation_id"]
    assert (await http(port, "POST", f"/api/v1/operations/{operation_id}/cancel", headers, b"{}"))[0] == 200


@pytest.mark.parametrize("invalidated", ["parent", "session", "credential", "disabled"])
async def test_browser_credential_class_survives_expiry_and_adapter_changes(browser, invalidated):
    from bat_agent_connector.session_observation import authorizer

    d, _, parent, port = browser
    _, cookie, _ = await enter(browser)
    token = cookie.split("=", 1)[1]
    principal = api_auth.authenticate(d.journal.db, token, d._admin_token, browser_session=True)
    check = authorizer(d, token, principal, browser_session=True)
    assert check()
    assert not authorizer(d, token, principal)()
    if invalidated == "disabled":
        del d.api.browser_sessions
    elif invalidated == "session":
        d.journal.db.execute("UPDATE browser_sessions SET expires_at=1")
    else:
        digest = api_auth._digest(parent if invalidated == "parent" else token)
        d.journal.db.execute("UPDATE api_principals SET revoked_at=1 WHERE token_hash=?", (digest,))
    assert api_auth.authenticate(d.journal.db, token, d._admin_token) is None
    if invalidated != "disabled":
        assert not check()
        headers = {"Cookie": cookie, "Sec-Fetch-Site": "same-origin", "Referer": f"http://127.0.0.1:{port}/dashboard/"}
        assert (await http(port, "GET", "/api/v1/browser-session", headers))[0] == 401
    assert (await http(port, "POST", "/rpc", {"Authorization": f"Bearer {token}"}, b'{"method":"op_list"}'))[0] == 400


async def test_cookie_event_stream_rechecks_session_and_parent(browser, monkeypatch):
    from bat_agent_connector import api_v1

    d, _, parent, port = browser
    _, cookie, _ = await enter(browser)
    headers = {"Cookie": cookie, "Sec-Fetch-Site": "same-origin", "Referer": f"http://127.0.0.1:{port}/dashboard/"}
    _, _, session = await http(port, "GET", "/api/v1/browser-session", headers)
    headers["X-Batc-CSRF"] = session["csrf"]
    monkeypatch.setattr(api_v1, "REAUTH_S", 0)
    monkeypatch.setattr(api_v1, "KEEPALIVE_S", 0)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    lines = ["GET /api/v1/events/stream HTTP/1.1", f"Host: 127.0.0.1:{port}"]
    lines += [f"{key}: {value}" for key, value in headers.items()]
    try:
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
        await writer.drain()
        assert (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)).startswith(b"HTTP/1.1 200")
        # A keepalive is emitted only after the stream reauthenticates the cookie.
        await asyncio.wait_for(reader.readuntil(b": keepalive\n\n"), 5)
        d.journal.db.execute("UPDATE api_principals SET revoked_at=1 WHERE token_hash=?", (api_auth._digest(parent),))
        await asyncio.wait_for(reader.read(), 5)
        assert reader.at_eof()
    finally:
        writer.close()
        await writer.wait_closed()
