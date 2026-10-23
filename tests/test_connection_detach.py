"""Real HTTP/journal flows; v2 codecs synthesize receipts, not a native participant.

No reference participant exists yet. The existing Fleet producer creates each
binding, while the mock BAT supplies the real Inventory observation shape.
"""
import asyncio
import copy
import json
from dataclasses import replace

import pytest

from bat_agent_connector import connection_dependencies as dependencies
from bat_agent_connector import connection_detach, setup_connections
from bat_agent_connector import connection_handoff as handoff
from bat_agent_connector.operations import OperationError
from tests import test_connection_reference_contract as codec
from tests import test_setup_connections as connections
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token
from tests.test_work_items import act

installed = connections.installed
served_setup = connections.served_setup
source = connections.source
ACTION = codec.ACTION


@pytest.fixture
async def connected(installed, served_setup, source, mock):
    d, principal, root = installed
    params = connections.producer.host_params(d, principal, mock)
    selected = {"kind": "staged_secret", **{k: params[k] for k in ("secret_ref", "url", "fingerprint")}}
    descriptor = setup_connections.source_descriptor(d.ops, principal, selected)
    source = {**source, "source": selected, "source_reference": descriptor["reference"], "service_ref": "fixture",
        "server_configuration": {"target": {"host": "fixture"}, "params": params,
            "preconditions": {"config_revision": connections.setup._configuration(d)[3]}}}
    _, ident = await connections.accept(served_setup, connections.intent(source, action="setup.connection.apply"))
    await settle_operations(d.ops)
    native = await connections.read_handoff(served_setup, ident)
    assert (await served_setup("POST", connections.RECEIPT, connections.participant_receipt(native)))[0] == 200
    await settle_operations(d.ops)
    assert d.ops.get(ident)["status"] == "succeeded"
    await d.inventory.refresh_host("fixture")
    row = setup_connections.list_connections(d.ops, principal, "desktop-a")["connections"][0]
    assert dependencies.read(d.ops, principal, "desktop-a", row["connection_id"])["server_complete"]
    files = {p: p.read_bytes() for p in root.rglob("*") if p.is_file() and "state" not in p.parts}
    invokes, auth = copy.deepcopy(mock.invokes), copy.deepcopy(mock.auth_frames)
    yield row
    assert mock.invokes == invokes and mock.auth_frames == auth
    assert all(p.read_bytes() == data for p, data in files.items())


def intent(row, outcome="applied"):
    plan = codec.plan()
    if outcome == "unchanged":
        plan["artifacts"][0]["after"] = plan["artifacts"][0]["before"].copy()
    return {"action": ACTION, "target": {"installation_ref": row["installation_ref"], "connection_id": row["connection_id"]},
        "params": {"native_plan": plan}, "preconditions": {"expected_revision": row["revision"],
            "dependency_digest": "0" * 64, "review_digest": "0" * 64}}


async def reviewed(request, body):
    status, result = await request("POST", connections.PREVIEW, body)
    assert status == 200, result
    preview = result["preview"]
    assert preview["dependencies"]["server_complete"] and not preview["dependencies"]["complete"]
    return {**body, "preconditions": {key: preview[key] for key in body["preconditions"]}}


async def submitted(d, request, body):
    status, response = await request("POST", "/api/v1/operations", body, key="detach")
    assert status == 202, response
    ident = response["operation"]["operation_id"]
    await settle_operations(d.ops)
    assert d.ops.get(ident)["status"] == "waiting_external"
    assert d.ops.db.execute("SELECT state FROM connection_bindings").fetchone()[0] == "active"
    return ident


async def claimed(request, ident, mode="claim", **kwargs):
    body = {**connections.handoff_request(ident, mode=mode), "contract_version": 2}
    status, response = await request("POST", connections.HANDOFF, body, **kwargs)
    assert status == 200, response
    return response["native_handoff"]


def receipt(native, outcome="applied"):
    phase, code = {"refused": ("preflight", "CONNECTION_DETACH_WOULD_STOP_TUNNEL"),
        "uncertain": ("publishing", "LOCAL_THIRD_BYTES"), "abandoned": ("abandonment", "LOCAL_PLAN_ABANDONED")}.get(outcome, ("readback", None))
    body = {**codec.receipt(native, outcome, phase, code), "challenge": native["challenge"]}
    handoff.validate_receipt_v2(body, action=ACTION, kind=codec.KIND, plan=native["native_plan"])
    return body


@pytest.fixture
async def pending(installed, served_setup, connected, request):
    d, principal, _ = installed
    outcome = getattr(request, "param", "applied")
    body = await reviewed(served_setup, intent(connected, outcome))
    ident = await submitted(d, served_setup, body)
    return body, ident, outcome


@pytest.mark.parametrize("pending", ["applied", "unchanged", "refused", "uncertain", "abandoned"], indirect=True)
async def test_outcomes_keep_identity_and_wait_for_evidence(installed, served_setup, connected, pending):
    d, principal, _ = installed
    before = dict(d.ops.db.execute("SELECT * FROM connection_bindings").fetchone())
    body, ident, outcome = pending
    native = await claimed(served_setup, ident)
    expected = handoff.build_claims_v2(d.ops.get(ident), d.ops.get(ident)["external_refs"]["admission_binding"],
        kind=codec.KIND, issued_at=native["expires_at"] - handoff.TTL_S, epoch=native["epoch"])
    assert {k: native[k] for k in expected} == expected
    assert native["challenge"] == handoff._challenge_v2(d.ops, expected)
    sent = receipt(native, outcome)
    status, response = await served_setup("POST", connections.RECEIPT, sent)
    assert status == 200, response
    await settle_operations(d.ops)
    if outcome == "uncertain":
        from bat_agent_connector.operations import UNCERTAIN_RETRY_S
        for _ in UNCERTAIN_RETRY_S:
            d.ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (ident,))
            await settle_operations(d.ops)
    operation = d.ops.get(ident)
    after = dict(d.ops.db.execute("SELECT * FROM connection_bindings").fetchone())
    success = outcome in {"applied", "unchanged"}
    assert operation["status"] == ("succeeded" if success else "needs_attention" if outcome == "uncertain" else "failed")
    if success:
        assert after == {**before, "state": "detached", "revision": before["revision"] + 1,
            "receipt_refs": json.dumps([*json.loads(before["receipt_refs"]), response["receipt"]["receipt_ref"]])}
        assert response["receipt"]["receipt_ref"] == "receipt_" + handoff.digest({k: v for k, v in sent.items() if k != "challenge"})
        status, refused = await served_setup("POST", connections.PREVIEW, intent({**connected, "revision": 2}))
        assert status == 409 and refused["error"]["code"] == "CONNECTION_ALREADY_DETACHED"
    else:
        assert after == before
        if outcome != "uncertain":
            assert operation["error_code"] == sent["code"]
    status, replay = await served_setup("POST", connections.RECEIPT, sent)
    assert status == 200 and replay["replayed"]
    assert dict(d.ops.db.execute("SELECT * FROM connection_bindings").fetchone()) == after


@pytest.mark.parametrize("field,value", [("target", {"stop": True}), ("params", {"server_configuration": {}}),
    ("params", {"path": "/ignored"}), ("preconditions", {"skip": True}), ("target", None), ("params", None)])
async def test_exact_request(installed, served_setup, connected, field, value):
    body = intent(connected)
    if value is None:
        body[field].pop("connection_id" if field == "target" else "native_plan")
    else:
        body[field].update(value)
    status, _ = await served_setup("POST", connections.PREVIEW, body)
    assert status in {409, 422}


async def test_owner_and_scope_and_codec_capability(installed, served_setup, connected):
    d, principal, _ = installed
    with pytest.raises(OperationError, match="Connection not found"):
        connection_detach.owned(d.ops, replace(principal, actor="stranger"), intent(connected)["target"])
    for actor, scopes, code in (("stranger", principal.scopes, "NOT_FOUND"), (principal.actor, {"manage"}, "FORBIDDEN")):
        status, response = await served_setup("POST", connections.PREVIEW, intent(connected), credential=token(d, actor, *scopes))
        assert status == (404 if code == "NOT_FOUND" else 403) and response["error"]["code"] == code
    body = intent(connected)
    body["target"]["installation_ref"] = "desktop-b"
    assert (await served_setup("POST", connections.PREVIEW, body))[0] == 404
    d.managed_installation = None  # Reference effects do not require personal managed setup.
    await reviewed(served_setup, intent(connected))
    status, bootstrap = await served_setup("GET", "/api/v1/bootstrap")
    caps = bootstrap["capabilities"]
    assert status == 200 and caps["connection_contract_version"] == 1
    assert caps["connection_effect_kinds"] == [{"kind": codec.KIND, "contract_version": 2, "action": ACTION}]
    assert next(a for a in caps["actions"] if a["action"] == ACTION)["scope"] == "observe"


@pytest.mark.parametrize("fault", ["revision", "digest", "digest_value", "coverage", "blocking"])
async def test_admission_rechecks_dependencies(installed, served_setup, connected, monkeypatch, fault):
    d, principal, _ = installed
    body = await reviewed(served_setup, intent(connected))
    expected = "SETUP_REVIEW_CHANGED"
    if fault == "revision":
        body["preconditions"]["expected_revision"] -= 1
    elif fault == "digest":
        pid = (await act(d, principal, "project.create", params={"name": "History"}))["result"]["project_id"]
        wid = (await act(d, principal, "work_item.create", {"project_id": pid}, {"title": "History"}))["result"]["work_item_id"]
        await act(d, principal, "work_item.link", {"work_item_id": wid}, {"kind": "operation", "ref": connected["operation_id"]})
    elif fault == "digest_value":
        body["preconditions"]["dependency_digest"] = "0" * 64
    elif fault == "coverage":
        monkeypatch.setattr(d.inventory, "settings", replace(d.inventory.settings, stale_after_s=0))
        expected = "CONNECTION_DEPENDENCIES_UNPROVEN"
    else:
        monkeypatch.setattr(d.ops, "kick", lambda: None)
        d.ops.create(principal, action="session.start", target={"host": "fixture", "workspace": "ws-1"},
            params={"prompt": "pending"}, idempotency_key="pending")
        expected = "CONNECTION_DEPENDENCY_ACTIVE"
    if fault in {"coverage", "blocking"}:
        status, response = await served_setup("POST", connections.PREVIEW, body)
        assert status == 409 and response["error"]["code"] == expected
    status, response = await served_setup("POST", "/api/v1/operations", body, key="changed")
    assert status == 409 and response["error"]["code"] == expected, response
    assert not d.ops.db.execute("SELECT 1 FROM operations WHERE action=?", (ACTION,)).fetchone()


@pytest.mark.parametrize("first", [ACTION, "connection.reference.apply"])
async def test_busy_in_both_directions(installed, served_setup, connected, monkeypatch, first):
    d, _, _ = installed
    detach = await reviewed(served_setup, intent(connected))
    apply = connections.intent({}, connection=connected["connection_id"])
    apply.update(params={"label": "Changed"}, preconditions={"expected_revision": 1})
    apply = await connections.review(served_setup, apply)
    monkeypatch.setattr(d.ops, "kick", lambda: None)
    a, b = (detach, apply) if first == ACTION else (apply, detach)
    assert (await served_setup("POST", "/api/v1/operations", a, key="first"))[0] == 202
    status, response = await served_setup("POST", "/api/v1/operations", b, key="second")
    assert status == 409 and response["error"]["code"] == "CONNECTION_OPERATION_RUNNING", response


async def test_persisted_action_selects_plan_codec(served_setup, connected, source):
    for action, plan in ((ACTION, connections.native_manifest()), ("setup.connection.apply", codec.plan())):
        body = intent(connected) if action == ACTION else connections.intent(source, action=action)
        body["params"]["native_plan"] = plan
        status, response = await served_setup("POST", connections.PREVIEW, body)
        assert (status, response["error"]["code"]) == ((409, "CONNECTION_CONTRACT_UNSUPPORTED") if action == ACTION else (422, "INVALID_NATIVE_PLAN"))
    body = intent(connected)
    body["params"]["native_plan"]["kind"] = "reference_select"
    assert (await served_setup("POST", connections.PREVIEW, body))[0] == 409


async def test_cross_version_claim_receipt_and_challenge(installed, served_setup, connected, pending):
    d, _, _ = installed
    _, ident, _ = pending
    assert (await served_setup("POST", connections.HANDOFF, connections.handoff_request(ident)))[0] == 409
    native = await claimed(served_setup, ident)
    old = d.ops.get(connected["operation_id"])
    old_native = await connections.read_handoff(served_setup, old["operation_id"])
    old_receipt = connections.participant_receipt(old_native)
    old_receipt["operation_id"] = ident
    assert (await served_setup("POST", connections.RECEIPT, old_receipt))[0] == 409
    body = receipt(native)
    body["operation_id"] = old["operation_id"]
    assert (await served_setup("POST", connections.RECEIPT, body))[0] == 409
    body = receipt(native)
    claims = {k: v for k, v in native.items() if k not in {"challenge", "receipt"}}
    body["challenge"] = handoff._challenge(d.ops, claims)
    assert (await served_setup("POST", connections.RECEIPT, body))[0] == 409
    assert d.ops.get(ident)["status"] == "waiting_external"


async def test_restart_claim_receipt_readback(installed, served_setup, connected, pending):
    from bat_agent_connector.config import parse_config
    from bat_agent_connector.task_daemon import TaskDaemon
    d, principal, root = installed
    _, ident, _ = pending
    native = await claimed(served_setup, ident)
    bearer = token(d, principal.actor, *principal.scopes)
    await d.ops.drain()
    await d.fleet.close()
    await d.inventory.close()
    d.journal.close()
    path = root / "config/hosts.toml"
    restored = TaskDaemon(parse_config(connections.setup.tomllib.loads(path.read_text()), path), root / "state/tasks.sqlite3")
    server = await asyncio.start_server(restored._handle, "127.0.0.1", 0)
    async def request(method, route, body=None):
        return await http(server.sockets[0].getsockname()[1], method, route, tok=bearer, body=body)
    try:
        assert await claimed(request, ident, "readback") == native
        assert (await request("POST", connections.RECEIPT, receipt(native)))[0] == 200
        await settle_operations(restored.ops)
        assert restored.ops.get(ident)["status"] == "succeeded"
        readback = await claimed(request, ident, "readback")
        assert readback["effect_id"] == native["effect_id"] and readback["receipt"]["outcome"] == "applied"
    finally:
        server.close()
        await server.wait_closed()
        await restored.ops.drain()
        await restored.fleet.close()
        await restored.inventory.close()
        restored.journal.close()


async def test_busy_gate_rechecks_after_initial_admission(installed, served_setup, connected, monkeypatch):
    d, principal, _ = installed
    body = await reviewed(served_setup, intent(connected))
    apply = connections.intent({}, connection=connected["connection_id"])
    apply.update(params={"label": "Changed"}, preconditions={"expected_revision": 1})
    apply = await connections.review(served_setup, apply)
    original = d.ops.actions[ACTION].admit
    def interleave(*args):
        binding = original(*args)
        d.ops.create(principal, **apply, idempotency_key="interleaved")
        return binding
    monkeypatch.setitem(d.ops.actions, ACTION, replace(d.ops.actions[ACTION], admit=interleave))
    monkeypatch.setattr(d.ops, "kick", lambda: None)
    status, response = await served_setup("POST", "/api/v1/operations", body, key="raced")
    assert status == 409 and response["error"]["code"] == "CONNECTION_OPERATION_RUNNING"
    assert not d.ops.db.execute("SELECT 1 FROM operations WHERE action=?", (ACTION,)).fetchone()


async def test_blocker_after_first_dependency_page(installed, served_setup, connected, monkeypatch):
    d, principal, _ = installed
    original = dependencies.read
    monkeypatch.setattr(dependencies, "read", lambda *a, **kw: original(*a, **kw, limit=1))
    monkeypatch.setattr(d.ops, "kick", lambda: None)
    d.ops.create(principal, action="session.start", target={"host": "fixture", "workspace": "ws-1"},
        params={"prompt": "pending"}, idempotency_key="pending")
    page = original(d.ops, principal, "desktop-a", connected["connection_id"], limit=1)
    assert page["next_offset"] and not any(r["disposition"] == "blocking" for r in page["impacts"])
    status, response = await served_setup("POST", connections.PREVIEW, intent(connected))
    assert status == 409 and response["error"]["code"] == "CONNECTION_DEPENDENCY_ACTIVE"


async def test_preview_is_read_only_and_preserves_native_unknowns(installed, served_setup, connected):
    d, principal, _ = installed
    before = d.ops.db.total_changes
    status, response = await served_setup("POST", connections.PREVIEW, intent(connected))
    assert status == 200 and d.ops.db.total_changes == before
    preview = response["preview"]
    assert preview["dependencies"] == dependencies.read(d.ops, principal, "desktop-a", connected["connection_id"])
    for domain in ("native_selection", "native_resources"):
        assert preview["dependencies"]["domains"][domain] == {
            "covered": False, "complete": False, "reason": "NATIVE_REVIEW_REQUIRED"}
