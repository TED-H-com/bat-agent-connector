"""Real authentication replies shared with the Node-only browser job."""
import json
import os
from pathlib import Path

from bat_agent_connector import api_auth
from tests.test_browser_sessions import browser as browser, http

GOLDEN = Path(__file__).parent / "fixtures" / "browser-credential-responses.json"


async def test_browser_credential_responses_match_real_handlers(browser):
    daemon, _, token, port = browser
    del daemon.api.browser_sessions
    headers = {"Authorization": f"Bearer {token}"}

    async def reply(method, path, authenticated=True):
        status, _, data = await http(port, method, "/api/v1" + path,
            headers if authenticated else {}, b"{}" if method == "POST" else b"")
        return {"status": status, "data": data}

    other = api_auth.issue(daemon.journal.db, "other-operator", api_auth.SCOPES)
    principal = api_auth.authenticate(daemon.journal.db, other, daemon._admin_token)
    operation, _ = daemon.ops.create(principal, action="project.create", params={"name": "Example"},
        idempotency_key="example-project")
    operation_path = "/operations/" + operation["operation_id"]
    daemon.journal.db.execute("UPDATE api_principals SET scopes=? WHERE token_hash=?",
        (json.dumps(["observe"]), api_auth._digest(token)))
    operation_reply = await reply("GET", operation_path)
    assert operation_reply["status"] == 200
    normalized = json.loads(json.dumps(operation_reply).replace(operation["operation_id"], "op_" + "a" * 32))
    authority = normalized["data"]["operation"]["external_refs"]["admission_binding"]["controller_authority"]
    authority["controller_id"] = "ctr_" + "b" * 32
    authority["server_id"] = "fixture-server"
    for key in ("created_at", "updated_at"):
        normalized["data"]["operation"][key] = 0
    result = {"operation": normalized,
        "action_forbidden": await reply("POST", operation_path + "/cancel"),
        "browser_session_unavailable": await reply("GET", "/browser-session", False)}
    assert result["action_forbidden"]["status"] == 403
    for status in (403, 401):
        daemon.journal.db.execute("UPDATE api_principals SET revoked_at=?,scopes=? WHERE token_hash=?",
            (1 if status == 401 else None, json.dumps(["manage"]), api_auth._digest(token)))
        responses = [await reply("GET", path) for path in ("/capabilities", "/bootstrap", "/events?after=0")]
        assert all(response == responses[0] for response in responses)
        assert responses[0]["status"] == status
        result[str(status)] = responses[0]
    if os.environ.get("BATC_UPDATE_CREDENTIAL_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(result, indent=2) + "\n")
    assert result == json.loads(GOLDEN.read_text())
