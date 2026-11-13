"""Operation history cursors pass unchanged through each real client transport."""
import asyncio
import json
from urllib.parse import urlencode

import pytest

from bat_agent_connector import api_auth, cli, operations
from bat_agent_connector.mcp_server import build_server
from tests import test_api_v1 as api

# Use only the mock BAT and temporary disk journal; no operation is dispatched.
daemon = api.daemon
served = api.served


@pytest.fixture
async def client(served, monkeypatch, capsys, request):
    d, port = served
    tok = api.token(d, "cursor-reader", "observe", "manage")
    principal = api_auth.authenticate(d.journal.db, tok, d._admin_token)
    monkeypatch.setenv("BATC_TASK_URL", f"http://127.0.0.1:{port}/rpc")
    monkeypatch.setenv("BATC_API_TOKEN", tok)
    server, fleet = build_server(d.fleet.config, principal_only=True, read_only=True)

    async def read(**params):
        if request.param == "rpc":
            status, body = await api.http(port, "POST", "/rpc", tok=tok,
                                          body={"method": "op_list", "params": params})
            return status, body.get("result"), body
        if request.param == "http":
            status, body = await api.http(port, "GET", "/api/v1/operations?" + urlencode(params), tok=tok)
            return status, body if status == 200 else None, body
        if request.param == "cli":
            argv = ["op"]
            for key, value in params.items():
                argv += ["--" + key.replace("_", "-"), str(value)]
            code = await asyncio.to_thread(cli.main, argv)
            output = capsys.readouterr()
            return code, json.loads(output.out) if code == 0 else None, output.err
        try:
            result = await server.call_tool("operations_list", params)
        except Exception as error:  # MCP versions can raise ToolError instead of returning it.
            return 1, None, str(error)
        # MCP's real tool call returns content plus the structured return value.
        if isinstance(result, tuple):
            return 0, result[1], result
        if getattr(result, "isError", False):
            return 1, None, str(result)
        content = getattr(result, "content", result)
        return 0, json.loads(content[0].text), result

    try:
        yield d, principal, read, request.param
    finally:
        await fleet.close()


@pytest.mark.parametrize("client", ["mcp", "rpc", "cli"], indirect=True)
async def test_equal_timestamp_pages_return_each_operation_once(client):
    d, principal, read, transport = client
    ids = [d.ops.create(principal, action="project.create", params={"name": f"Cursor {n}"},
                       idempotency_key=f"cursor-{n}")[0]["operation_id"] for n in range(3)]
    d.journal.db.execute("UPDATE operations SET created_at=42.5")
    status, first, _ = await read(limit=2)
    assert status == (200 if transport == "rpc" else 0)
    assert first["next_before"] == 42.5
    assert first["next_before_id"] == sorted(ids, reverse=True)[1]
    status, second, _ = await read(limit=2, before=first["next_before"], before_id=first["next_before_id"])
    assert status == (200 if transport == "rpc" else 0)
    seen = [row["operation_id"] for page in (first, second) for row in page["operations"]]
    assert seen == sorted(ids, reverse=True)
    assert second["next_before"] is None and second["next_before_id"] is None


@pytest.mark.parametrize("client", ["mcp", "rpc", "cli", "http"], indirect=True)
@pytest.mark.parametrize("params", [{"before_id": "op_" + "a" * 32}, {"before": "not-a-number"}])
async def test_invalid_cursor_uses_central_validator_and_existing_error_format(client, monkeypatch, params):
    _, _, read, transport = client
    validator = operations.operation_cursor
    with pytest.raises(operations.OperationError) as expected:
        validator(**params)
    assert expected.value.status == 422
    calls = []

    def observe_validator(before=None, before_id=None):
        calls.append((before, before_id))
        return validator(before, before_id)

    monkeypatch.setattr(operations, "operation_cursor", observe_validator)
    status, page, error = await read(**params)
    assert len(calls) == 1  # No adapter may reject or discard the cursor first.
    assert page is None
    assert status == {"http": 422, "rpc": 400, "mcp": 1, "cli": 1}[transport]
    if transport == "rpc":
        assert error == {"error": expected.value.code, "message": expected.value.message}
    elif transport == "http":
        assert error["error"]["code"] == expected.value.code
        assert error["error"]["message"] == expected.value.message
    else:
        assert expected.value.code in str(error)
        assert expected.value.message in str(error)
