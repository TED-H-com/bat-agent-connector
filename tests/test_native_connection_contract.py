"""Generator commitments through real HTTP and a durable disk journal."""

import copy
import itertools
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bat_agent_connector import connection_handoff as handoff
from tests import test_setup_connections as connections
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import token

installed = connections.installed
served_setup = connections.served_setup
source = connections.source
CONTRACT = json.loads(Path("src/bat_agent_connector/connection_contract.json").read_text())
GOLDEN = json.loads(Path("tests/fixtures/native-plan-manifest.json").read_text())


def manifest(kind="create"):
    return copy.deepcopy(GOLDEN[kind])


def claim_body(ident, mode="claim", **changes):
    return {
        "contract_version": CONTRACT["contract_version"],
        "installation_ref": "desktop-a",
        "operation_id": ident,
        "mode": mode,
        **changes,
    }


def native_intent(source, kind="create"):
    body = connections.intent(source, action="setup.connection.apply")
    body["params"]["native_plan"] = manifest(kind)
    return body


async def pending(installed, request, source, *, kind="create", key="native"):
    body, ident = await connections.accept(request, native_intent(source, kind), key=key)
    await settle_operations(installed[0].ops)
    status, response = await request("POST", connections.HANDOFF, claim_body(ident))
    assert status == 200, response
    return body, ident, response["native_handoff"]


def wire_receipt(native, outcome="applied", phase=None, code=None):
    plan = native.get("native_plan", manifest())
    choices = CONTRACT["matrix"][outcome]
    phase = phase or next(iter(choices))
    code = choices[phase][0] if code is None and outcome not in ("applied", "unchanged") else code
    body = {
        key: native[key]
        for key in (
            "contract_version",
            "installation_ref",
            "operation_id",
            "step",
            "review_digest",
            "expected_revision",
            "epoch",
            "effect_id",
            "challenge",
        )
    }
    roles = copy.deepcopy(plan["artifacts"])
    for index, role in enumerate(roles):
        if outcome == "refused" or outcome in ("uncertain", "abandoned") and index > 0:
            role["after_digest"] = role["before_digest"]
        role["observation"] = "present" if role["after_digest"] is not None else "absent"
    return {
        **body,
        "native_review_digest": plan["review_digest"],
        "outcome": outcome,
        "phase": phase,
        "code": code,
        "roles": roles,
    }


@pytest.mark.parametrize("configuration", [False, True])
@pytest.mark.parametrize("route", [connections.PREVIEW, "/api/v1/operations"])
async def test_personal_apply_requires_desktop_review_before_any_configuration(
    installed, served_setup, source, route, configuration
):
    daemon, _, root = installed
    before = (root / "config/hosts.toml").read_bytes()
    body = connections.intent(source, action="setup.connection.apply")
    body["params"].pop("native_plan", None)
    if configuration:
        body["params"]["server_configuration"] = {
            "target": {"host": "fixture"},
            "params": {"import_profile_id": "profile-a"},
            "preconditions": {"config_revision": connections.setup._configuration(daemon)[3]},
        }
    status, response = await served_setup("POST", route, body, key="browser")
    assert status == 409 and response["error"]["code"] == "NATIVE_REVIEW_REQUIRED", response
    assert "desktop" in response["error"]["message"] and "桌面" in response["error"]["message"]
    assert (root / "config/hosts.toml").read_bytes() == before
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()


async def test_native_manifest_is_echoed_and_bound_to_outer_review(installed, served_setup, source):
    daemon, _, _ = installed
    body = native_intent(source)
    status, preview = await served_setup("POST", connections.PREVIEW, body)
    assert status == 200, preview
    assert preview["preview"]["native_plan"] == body["params"]["native_plan"]
    reviewed = await connections.review(served_setup, body)
    mutated = copy.deepcopy(reviewed)
    mutated["params"]["native_plan"]["review_digest"] = "f" * 64
    status, refusal = await served_setup("POST", "/api/v1/operations", mutated, key="changed")
    assert status == 409 and refusal["error"]["code"] == "SETUP_REVIEW_CHANGED"
    assert not daemon.journal.db.execute("SELECT 1 FROM operations").fetchone()
    status, result = await served_setup("POST", "/api/v1/operations", reviewed, key="native")
    assert status == 202
    operation = result["operation"]
    assert (
        operation["params"]["native_plan"]
        == operation["external_refs"]["admission_binding"]["native_plan"]
        == manifest()
    )
    await settle_operations(daemon.ops)
    _, claimed = await served_setup("POST", connections.HANDOFF, claim_body(operation["operation_id"]))
    assert claimed["native_handoff"]["native_plan"] == manifest()
    assert claimed["native_handoff"]["review_digest"] != manifest()["review_digest"]


@pytest.mark.parametrize(
    "fault", ["extra", "missing", "duplicate", "order", "digest", "bool_version", "generator", "size"]
)
async def test_malformed_or_unsupported_native_manifest_is_refused(installed, served_setup, source, fault):
    body = native_intent(source)
    plan = body["params"]["native_plan"]
    expected, status_code = "INVALID_NATIVE_PLAN", 422
    if fault == "extra":
        plan["path"] = "/private/file"
    elif fault == "missing":
        plan["artifacts"].pop()
    elif fault == "duplicate":
        plan["artifacts"][1] = copy.deepcopy(plan["artifacts"][0])
    elif fault == "order":
        plan["artifacts"].reverse()
    elif fault == "digest":
        plan["review_digest"] = "invalid"
    elif fault == "bool_version":
        plan["generator_version"] = True
    elif fault == "generator":
        plan["generator_version"] = max(CONTRACT["generator_versions"]) + 1
        expected, status_code = "CONNECTION_GENERATOR_UNSUPPORTED", 409
    else:
        plan["review_digest"] = "f" * (CONTRACT["bounds"]["native_plan_bytes"] + 1)
    status, response = await served_setup("POST", connections.PREVIEW, body)
    assert status == status_code and response["error"]["code"] == expected, response
    assert not installed[0].journal.db.execute("SELECT 1 FROM operations").fetchone()


async def test_reference_action_rejects_native_plan(installed, served_setup, source):
    body = connections.intent(source)
    body["params"]["native_plan"] = manifest()
    status, response = await served_setup("POST", connections.PREVIEW, body)
    assert status == 422 and response["error"]["code"] == "INVALID_NATIVE_PLAN"


@pytest.mark.parametrize("outcome,phase", list(itertools.product(CONTRACT["outcomes"], CONTRACT["phases"])))
async def test_receipt_outcome_phase_code_matrix(installed, served_setup, source, outcome, phase):
    daemon, _, _ = installed
    for index, code in enumerate([None, *CONTRACT["codes"]]):
        _, ident, native = await pending(
            installed,
            served_setup,
            source,
            kind="unchanged" if outcome == "unchanged" else "create",
            key=f"matrix-{index}",
        )
        body = wire_receipt(native, outcome)
        body.update(phase=phase, code=code)
        allowed = code in CONTRACT["matrix"][outcome].get(phase, [])
        status, response = await served_setup("POST", connections.RECEIPT, body)
        assert status == (200 if allowed else 422), response
        if allowed:
            await settle_operations(daemon.ops)
            operation = daemon.ops.get(ident)
            expected = (
                "succeeded"
                if outcome in ("applied", "unchanged")
                else "waiting_external"
                if outcome == "uncertain"
                else "failed"
            )
            if outcome == "uncertain":
                assert operation["status"] in ("uncertain", "needs_attention"), operation
            else:
                assert operation["status"] == expected, operation
            if outcome in ("refused", "abandoned"):
                assert operation["error_code"] == code
                assert operation["status_reason"] == f"[{code}] {CONTRACT['codes'][code]['reason']}"
                assert not daemon.journal.db.execute(
                    "SELECT 1 FROM connection_bindings WHERE operation_id=?", (ident,)
                ).fetchone()
            assert operation["external_refs"]["receipt_refs"] == [response["receipt"]["receipt_ref"]]
            assert "challenge" not in json.dumps(response["receipt"])
        # Each create is independent; no native effect is retried to explore validation.


@pytest.mark.parametrize("route", [connections.HANDOFF, connections.RECEIPT])
@pytest.mark.parametrize("version", [None, True, 0, CONTRACT["contract_version"] + 1])
async def test_missing_or_unsupported_wire_version_has_typed_refusal(
    installed, served_setup, source, route, version
):
    _, ident = await connections.waiting_connection(installed, served_setup, source)
    body = claim_body(ident) if route == connections.HANDOFF else {"contract_version": version}
    if version is None:
        body.pop("contract_version", None)
    else:
        body["contract_version"] = version
    status, response = await served_setup("POST", route, body)
    assert status == 409 and response["error"]["code"] == "CONNECTION_CONTRACT_UNSUPPORTED", response


async def test_expired_lost_claim_reply_recovers_only_original_readback(
    installed, served_setup, source, tmp_path
):
    daemon, _, _ = installed
    body, ident = await connections.accept(served_setup, native_intent(source))
    await settle_operations(daemon.ops)
    assert (await served_setup("POST", connections.HANDOFF, claim_body(ident, "readback")))[0] == 409
    send = daemon.api._send_json
    processed = []

    async def lost(writer, status, payload, *args):
        if status == 200 and "native_handoff" in payload and not processed:
            processed.append(payload)
            writer.close()
            return
        return await send(writer, status, payload, *args)

    daemon.api._send_json = lost
    try:
        try:
            await served_setup("POST", connections.HANDOFF, claim_body(ident))
        except (ValueError, IndexError):
            pass
    finally:
        daemon.api._send_json = send
    assert processed
    stored = daemon.journal.db.execute(
        "SELECT request FROM operation_steps WHERE operation_id=? AND name='native_effects'", (ident,)
    ).fetchone()[0]
    old = json.loads(stored)
    for at in (old["expires_at"] - 1, old["expires_at"] + 1):
        with patch.object(handoff, "now", return_value=at):
            changes = daemon.journal.db.total_changes
            status, response = await served_setup("POST", connections.HANDOFF, claim_body(ident, "readback"))
            assert status == 200 and response["read_only"] is True, response
            native = response["native_handoff"]
            assert native["epoch"] == old["epoch"] and native["expires_at"] == old["expires_at"]
            assert daemon.journal.db.total_changes == changes
            assert (await served_setup("POST", connections.HANDOFF, claim_body(ident)))[0] == (
                200 if at < old["expires_at"] else 409
            )
    participant = connections.DiskParticipant(tmp_path / "effect.json")
    # The lost claim reply never authorized this participant to begin an effect.
    # Expired readback can recover identity to deliver durable no-write evidence.
    with patch.object(handoff, "now", return_value=native["expires_at"] + 1):
        (status, _), receipt = await participant.send(
            served_setup, native, outcome="refused", code="LOCAL_EFFECT_EXPIRED"
        )
    assert status == 200
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "failed"
    assert daemon.ops.get(ident)["error_code"] == "LOCAL_EFFECT_EXPIRED"
    changes = daemon.journal.db.total_changes
    status, response = await served_setup("POST", connections.HANDOFF, claim_body(ident, "readback"))
    assert status == 200 and response["read_only"] is True
    assert daemon.journal.db.total_changes == changes
    assert participant.writes == 0


@pytest.mark.parametrize("role", CONTRACT["roles"])
@pytest.mark.parametrize("observation", CONTRACT["observations"])
async def test_partial_observations_preserve_each_artifact(
    installed, served_setup, source, role, observation
):
    daemon, _, _ = installed
    _, ident, native = await pending(installed, served_setup, source)
    body = wire_receipt(native, "uncertain")
    entry = next(item for item in body["roles"] if item["role"] == role)
    entry.update(observation=observation, after_digest="f" * 64 if observation == "present" else None)
    status, response = await served_setup("POST", connections.RECEIPT, body)
    assert status == 200, response
    await settle_operations(daemon.ops)
    assert not daemon.journal.db.execute("SELECT 1 FROM connection_bindings").fetchone()
    assert response["receipt"]["roles"] == body["roles"]
    definitive = wire_receipt(native, "abandoned")
    assert (await served_setup("POST", connections.RECEIPT, definitive))[0] == 200
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["error_code"] == "LOCAL_PLAN_ABANDONED"
    row = daemon.journal.db.execute(
        "SELECT response FROM operation_steps WHERE operation_id=? AND name='native_effects'", (ident,)
    ).fetchone()
    assert len(handoff.receipts(row)) == 2
    assert daemon.journal.db.execute(
        "SELECT 1 FROM api_events WHERE resource_id=? AND kind='connection.native.abandoned'", (ident,)
    ).fetchone()
    assert (await served_setup("POST", connections.RECEIPT, definitive))[1]["replayed"] is True


@pytest.mark.parametrize("field", ["native_review_digest", "before_digest", "after_digest"])
async def test_changed_native_commitment_is_refused_before_projection(installed, served_setup, source, field):
    _, ident, native = await pending(installed, served_setup, source)
    body = wire_receipt(native)
    if field == "native_review_digest":
        body[field] = "f" * 64
    else:
        body["roles"][0][field] = "f" * 64
    status, response = await served_setup("POST", connections.RECEIPT, body)
    assert status == 409 and response["error"]["code"] == "SETUP_REVIEW_CHANGED", response
    assert not installed[0].journal.db.execute("SELECT 1 FROM connection_bindings").fetchone()


@pytest.mark.parametrize("mode", CONTRACT["modes"])
async def test_handoff_modes_keep_personal_principal_installation_and_scope(
    installed, served_setup, source, mode
):
    daemon, principal, _ = installed
    _, ident, _ = await pending(installed, served_setup, source)
    for credential in (
        token(daemon, principal.actor, "observe"),
        token(daemon, "foreign", "observe", "manage"),
    ):
        assert (
            await served_setup("POST", connections.HANDOFF, claim_body(ident, mode), credential=credential)
        )[0] in (403, 404)
    assert (
        await served_setup("POST", connections.HANDOFF, claim_body(ident, mode, installation_ref="other"))
    )[0] == 403


@pytest.mark.parametrize(
    "fault",
    ["missing", "duplicate", "order", "unknown", "extra", "observation", "present_null", "absent_digest"],
)
async def test_receipt_requires_exact_artifacts_and_truthful_observations(
    installed, served_setup, source, fault
):
    _, _, native = await pending(installed, served_setup, source)
    body = wire_receipt(native, "uncertain")
    if fault == "missing":
        body["roles"].pop()
    elif fault == "duplicate":
        body["roles"][1] = copy.deepcopy(body["roles"][0])
    elif fault == "order":
        body["roles"].reverse()
    elif fault == "unknown":
        body["roles"][0]["role"] = "unsupported"
    elif fault == "extra":
        body["roles"][0]["path"] = "/private/file"
    elif fault == "observation":
        body["roles"][0]["observation"] = "unknown"
    elif fault == "present_null":
        body["roles"][0].update(observation="present", after_digest=None)
    else:
        body["roles"][0].update(observation="absent", after_digest="a" * 64)
    status, response = await served_setup("POST", connections.RECEIPT, body)
    assert status == 422 and response["error"]["code"] == "INVALID_CONNECTION_RECEIPT", response


async def test_no_write_refusal_can_observe_external_changes(installed, served_setup, source):
    daemon, _, _ = installed
    _, ident, native = await pending(installed, served_setup, source)
    body = wire_receipt(native, "refused")
    for role in body["roles"]:
        role.update(after_digest="f" * 64, observation="present")
    status, response = await served_setup("POST", connections.RECEIPT, body)
    assert status == 200, response
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["error_code"] == body["code"]
    assert not daemon.journal.db.execute("SELECT 1 FROM connection_bindings").fetchone()


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
async def test_readback_after_terminal_and_expiry_never_renews(installed, served_setup, source, terminal):
    from bat_agent_connector.operations import UNCERTAIN_RETRY_S

    daemon, _, _ = installed
    _, ident, native = await pending(installed, served_setup, source)
    outcome = {"succeeded": "applied", "failed": "abandoned", "cancelled": "uncertain"}[terminal]
    receipt = wire_receipt(native, outcome)
    assert (await served_setup("POST", connections.RECEIPT, receipt))[0] == 200
    for _ in range(len(UNCERTAIN_RETRY_S) + 1):
        daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (ident,))
        await settle_operations(daemon.ops)
    if terminal == "cancelled":
        _, response = await served_setup("POST", f"/api/v1/operations/{ident}/cancel", {})
        assert "unknown" in response["operation"]["status_reason"].lower()
        assert "unresolved" in response["operation"]["status_reason"].lower()
    assert daemon.ops.get(ident)["status"] == terminal
    if terminal == "cancelled":
        assert not daemon.journal.db.execute(
            "SELECT 1 FROM api_events WHERE resource_id=? AND kind='connection.native.abandoned'", (ident,)
        ).fetchone()
    with patch.object(handoff, "now", return_value=native["expires_at"] + 3600):
        before = daemon.ops.get(ident)
        changes = daemon.journal.db.total_changes
        status, response = await served_setup("POST", connections.HANDOFF, claim_body(ident, "readback"))
        assert status == 200 and response["read_only"] is True, response
        assert {key: response["native_handoff"][key] for key in handoff.CLAIMS} == {
            key: native[key] for key in handoff.CLAIMS
        }
        assert response["native_handoff"]["challenge"] == native["challenge"]
        assert daemon.journal.db.total_changes == changes and daemon.ops.get(ident) == before
        assert (await served_setup("POST", connections.RECEIPT, receipt))[1]["replayed"] is True
        other = copy.deepcopy(receipt)
        other["code"] = next(code for code in CONTRACT["codes"] if code != receipt["code"])
        # Use a syntactically valid different receipt so terminal policy owns the refusal.
        other = (
            wire_receipt(native, "abandoned")
            if terminal == "cancelled"
            else wire_receipt(native, "refused", code=CONTRACT["matrix"]["refused"]["preflight"][0])
        )
        status, result = await served_setup("POST", connections.RECEIPT, other)
        assert status == 409 and result["error"]["code"] == "CONNECTION_OPERATION_TERMINAL"


async def test_readback_after_admin_key_loss_returns_no_envelope(installed, served_setup, source):
    daemon, _, _ = installed
    _, ident, native = await pending(installed, served_setup, source)
    daemon._admin_token = "synthetic-replacement-admin-key"
    with patch.object(handoff, "now", return_value=native["expires_at"] + 1):
        before = daemon.ops.get(ident)
        status, response = await served_setup("POST", connections.HANDOFF, claim_body(ident, "readback"))
    assert status == 409 and response["error"]["code"] == "CONNECTION_HANDOFF_KEY_CHANGED"
    assert "native_handoff" not in response and daemon.ops.get(ident) == before


@pytest.mark.parametrize("route", [connections.HANDOFF, connections.RECEIPT])
async def test_wire_bounds_precede_json_decoding(installed, served_setup, route):
    bound = CONTRACT["bounds"]["handoff_bytes" if route == connections.HANDOFF else "receipt_bytes"]
    status, _ = await served_setup.raw_request(route, b"not-json" + b" " * bound)
    assert status == 413


def test_public_vocabulary_is_generated_from_the_shipped_contract():
    from scripts.generate_connection_contract import BEGIN, END, ROOT, render

    document = (ROOT / "docs/design/fleet-configuration.md").read_text()
    assert document[document.index(BEGIN) : document.index(END) + len(END)] == render()
    for plan in GOLDEN.values():
        assert [artifact["role"] for artifact in plan["artifacts"]] == CONTRACT["roles"]
        assert plan["generator_version"] in CONTRACT["generator_versions"]


@pytest.mark.parametrize("kind,outcome", [("create", "unchanged"), ("unchanged", "applied")])
async def test_success_outcome_must_match_original_plan_change(
    installed, served_setup, source, kind, outcome
):
    _, _, native = await pending(installed, served_setup, source, kind=kind)
    status, response = await served_setup("POST", connections.RECEIPT, wire_receipt(native, outcome))
    assert status == 409 and response["error"]["code"] == "SETUP_REVIEW_CHANGED"


async def test_native_abandonment_keeps_partial_and_server_configuration_evidence(
    installed, served_setup, source, mock, tmp_path
):
    from bat_agent_connector import setup_connections

    daemon, principal, root = installed
    params = connections.producer.host_params(daemon, principal, mock)
    selected = {"kind": "staged_secret", **{key: params[key] for key in ("secret_ref", "url", "fingerprint")}}
    descriptor = setup_connections.source_descriptor(daemon.ops, principal, selected)
    source = {
        **source,
        "source": selected,
        "source_reference": descriptor["reference"],
        "service_ref": "fixture",
        "server_configuration": {
            "target": {"host": "fixture"},
            "params": params,
            "preconditions": {"config_revision": connections.setup._configuration(daemon)[3]},
        },
    }
    body, ident, native = await pending(installed, served_setup, source)
    configured = (root / "config/hosts.toml").read_bytes()
    participant = connections.DiskParticipant(tmp_path / "partial.json")
    (status, first), partial = await participant.send(served_setup, native, outcome="uncertain")
    assert status == 200 and participant.writes == 1
    await settle_operations(daemon.ops)
    definitive = connections.participant_receipt(native, "abandoned")
    outbox = tmp_path / "abandonment.json"
    outbox.write_text(json.dumps(definitive))
    assert outbox.stat().st_size < CONTRACT["bounds"]["receipt_bytes"]
    status, last = await served_setup("POST", connections.RECEIPT, json.loads(outbox.read_text()))
    assert status == 200
    await settle_operations(daemon.ops)
    operation = daemon.ops.get(ident)
    assert (operation["status"], operation["error_code"]) == ("failed", "LOCAL_PLAN_ABANDONED")
    assert operation["external_refs"]["receipt_refs"] == [
        first["receipt"]["receipt_ref"],
        last["receipt"]["receipt_ref"],
    ]
    assert (
        next(step for step in operation["steps"] if step["name"] == "managed_configuration")["status"]
        == "succeeded"
    )
    assert (root / "config/hosts.toml").read_bytes() == configured
    assert not daemon.journal.db.execute("SELECT 1 FROM connection_bindings").fetchone()
    assert (await served_setup("POST", connections.RECEIPT, definitive))[1]["replayed"] is True
    assert (await served_setup("POST", "/api/v1/operations", body, key="native"))[1]["operation"][
        "operation_id"
    ] == ident
    assert participant.writes == 1 and json.loads(participant.path.read_text()) == partial
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
    assert (
        daemon.journal.db.execute(
            "SELECT count(*) FROM api_events WHERE resource_id=? AND kind='connection.native.abandoned'",
            (ident,),
        ).fetchone()[0]
        == 1
    )


async def test_handoff_accepts_the_full_installation_reference_character_bound(
    installed, served_setup, source
):
    body = native_intent(source)
    installation = "界" * CONTRACT["bounds"]["installation_ref_chars"]
    body["target"]["installation_ref"] = installation
    _, ident = await connections.accept(served_setup, body)
    await settle_operations(installed[0].ops)
    status, response = await served_setup(
        "POST", connections.HANDOFF, claim_body(ident, installation_ref=installation)
    )
    assert status == 200, response
    assert response["native_handoff"]["installation_ref"] == installation


@pytest.mark.parametrize(
    "route", [connections.PREVIEW, "/api/v1/operations", connections.HANDOFF, connections.RECEIPT]
)
@pytest.mark.parametrize("fault", ["duplicate_field", "invalid_unicode"])
async def test_native_wire_rejects_ambiguous_json_fields(installed, served_setup, source, route, fault):
    if route in (connections.HANDOFF, connections.RECEIPT):
        _, ident, native = await pending(installed, served_setup, source)
        body = claim_body(ident) if route == connections.HANDOFF else wire_receipt(native)
        field = "mode" if route == connections.HANDOFF else "epoch"
        expected = (
            "INVALID_CONNECTION_HANDOFF" if route == connections.HANDOFF else "INVALID_CONNECTION_RECEIPT"
        )
        if fault == "invalid_unicode":
            body["installation_ref"] = "\ud800"
    else:
        body = native_intent(source)
        if route == "/api/v1/operations":
            body = await connections.review(served_setup, body)
            body["idempotency_key"] = "ambiguous-native-json"
        field = "generator_version"
        expected = "INVALID_NATIVE_PLAN"
        if fault == "invalid_unicode":
            body["params"]["native_plan"]["review_digest"] = "\ud800"
    raw = json.dumps(body)
    if fault == "duplicate_field":
        value = (
            body["mode"]
            if field == "mode"
            else body["epoch"]
            if field == "epoch"
            else body["params"]["native_plan"]["generator_version"]
        )
        pair = json.dumps(field) + ": " + json.dumps(value)
        assert pair in raw
        raw = raw.replace(pair, pair + ", " + pair, 1)
    before = installed[0].journal.db.total_changes
    status, response = await served_setup.raw_request(route, raw.encode())
    document = json.loads(response.split(b"\r\n\r\n", 1)[1])
    assert status == 422 and document["error"]["code"] == expected, document
    assert installed[0].journal.db.total_changes == before
