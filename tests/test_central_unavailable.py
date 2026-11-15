"""What a client of the local central service reports when its request was not sent."""
from __future__ import annotations

import asyncio
import errno
import json
import socket
import threading
import urllib.error

import pytest

from bat_agent_connector import cli
from bat_agent_connector.config import parse_config, state_dir
from bat_agent_connector.errors import CentralUnavailable
from bat_agent_connector.mcp_server import build_server
from bat_agent_connector.task_daemon import AdminTokenMissing, CentralNotListening, request
from tests.conftest import make_config

TOKEN = "batc_" + "x" * 43
# The two sentences are written out here: the getting-started guides quote how they begin.
MISSING = ("the local admin token was not found at {path}. `batc serve` writes it into its state directory "
           "when it first starts: start the service, or run this as the service's user with the same "
           "BATC_STATE_DIR")
REFUSED = ("no service listens at {url} (connection refused), so nothing was sent. Start `batc serve`, or "
           "set BATC_TASK_URL to the address it listens on")


@pytest.fixture(autouse=True)
def no_inherited_client_settings(monkeypatch):
    for name in ("BATC_TASK_URL", "BATC_TASK_CAPABILITY", "BATC_TASK_ADMIN_TOKEN_FILE", "BATC_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def unused_address():
    """A loopback RPC address that refuses connections: its port is bound and never listens."""
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        yield f"http://127.0.0.1:{bound.getsockname()[1]}/rpc"


def write_admin_token():
    path = state_dir() / "task-admin.token"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("a" * 43 + "\n")
    return path


@pytest.mark.parametrize("named", [False, True])
def test_a_request_without_a_credential_says_where_the_admin_token_is_expected(
        named, unused_address, monkeypatch, tmp_path):
    path = state_dir() / "task-admin.token"
    if named:
        write_admin_token()  # the named file is the one that is read
        path = tmp_path / "elsewhere" / "admin.token"
        monkeypatch.setenv("BATC_TASK_ADMIN_TOKEN_FILE", str(path))
    with pytest.raises(AdminTokenMissing) as caught:
        request("api_token_list", _url=unused_address)
    error = caught.value
    assert str(error) == MISSING.format(path=path)
    assert isinstance(error, FileNotFoundError) and isinstance(error, CentralUnavailable)
    assert (error.errno, error.filename) == (errno.ENOENT, str(path))


@pytest.mark.parametrize("credential", ["argument", "capability", "admin file"])
def test_a_refused_connection_says_that_no_service_listens(credential, unused_address, monkeypatch):
    given = {}
    if credential == "argument":
        given["_auth_token"] = TOKEN
    elif credential == "capability":
        monkeypatch.setenv("BATC_TASK_CAPABILITY", "c" * 43)
    else:
        write_admin_token()
    with pytest.raises(CentralNotListening) as caught:
        request("op_list", _url=unused_address, **given)
    error = caught.value
    assert str(error) == REFUSED.format(url=unused_address)
    assert isinstance(error, urllib.error.URLError) and isinstance(error, CentralUnavailable)
    assert isinstance(error.reason, ConnectionRefusedError)


@pytest.mark.parametrize("behaviour, expected, timeout", [("closes", ConnectionError, 30.0),
                                                           ("stays silent", TimeoutError, 0.5)])
def test_a_request_that_was_sent_keeps_its_own_error(behaviour, expected, timeout):
    """Once the service has the request, its outcome is not known: neither sentence applies."""
    held = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        def serve():
            connection, _ = listener.accept()
            received = b""
            while not received.endswith(b"}}"):  # the whole request: its JSON body ends it
                chunk = connection.recv(65536)
                if not chunk:
                    break
                received += chunk
            if behaviour == "closes":
                connection.close()
            else:
                held.append(connection)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            with pytest.raises(expected) as caught:
                request("op_list", _auth_token=TOKEN, timeout=timeout,
                        _url=f"http://127.0.0.1:{listener.getsockname()[1]}/rpc")
        finally:
            thread.join(5)
            for connection in held:
                connection.close()
    assert not isinstance(caught.value, CentralUnavailable)


def test_batc_says_in_one_line_why_the_request_was_not_sent(unused_address, monkeypatch, capsys):
    monkeypatch.setenv("BATC_TASK_URL", unused_address)
    issue = ["api-token", "issue", "--actor", "agent-1", "--scope", "observe"]
    assert cli.main(issue) == 1
    captured = capsys.readouterr()
    path = state_dir() / "task-admin.token"
    assert (captured.out, captured.err) == ("", "error: " + MISSING.format(path=path) + "\n")
    write_admin_token()
    for argv in (issue, ["api-token", "list"], ["api-token", "revoke", "--actor", "agent-1"], ["op"],
                 ["task-events"]):
        assert cli.main(argv) == 1, argv
        captured = capsys.readouterr()
        expected = "error: " + REFUSED.format(url=unused_address) + "\n"
        assert (captured.out, captured.err) == ("", expected), argv


async def test_a_command_that_records_an_operation_keeps_its_earlier_answer(
        mock, unused_address, monkeypatch, capsys):
    config = make_config(mock, writes=True, orchestrate=True)
    monkeypatch.setattr(cli, "load_config", lambda _path: config)
    monkeypatch.setenv("BATC_TASK_URL", unused_address)
    monkeypatch.setenv("BATC_API_TOKEN", TOKEN)
    assert await asyncio.to_thread(cli.main, ["start", "h1", "ws1", "--confirm"]) == 1
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == (
        "", "error: central start request failed; its outcome may be unknown. "
            "Read the saved operation or retry with the same explicit key\n")
    assert not mock.invokes


async def test_an_agent_is_told_that_no_service_listens(unused_address, monkeypatch):
    monkeypatch.setenv("BATC_API_TOKEN", TOKEN)
    monkeypatch.setenv("BATC_TASK_URL", unused_address)
    server, fleet = build_server(parse_config({}), principal_only=True)
    try:
        try:
            result = await server.call_tool("capabilities_get", {})
            answer = json.dumps(result.model_dump() if hasattr(result, "model_dump") else result, default=str)
        except Exception as error:  # the MCP library raises the tool's error or returns it as a result
            answer = str(error)
    finally:
        await fleet.close()
    assert "CentralNotListening: " + REFUSED.format(url=unused_address) in answer
