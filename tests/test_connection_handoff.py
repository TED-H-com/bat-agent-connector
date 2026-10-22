"""Frozen native wire shapes; no native transport, file generator or live BAT."""
import asyncio
import copy
import json
from unittest.mock import patch

import pytest

from bat_agent_connector import connection_handoff as handoff
from bat_agent_connector.operations import OperationError
from tests import test_setup_connections as connections
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import token

installed = connections.installed
served_setup = connections.served_setup
source = connections.source


def receipt():
    native = {"contract_version": connections.CONTRACT["contract_version"], "installation_ref": "desktop-a", "operation_id": "op_" + "a" * 32,
        "step": "native_effects", "review_digest": "b" * 64, "expected_revision": 0,
        "epoch": 1, "effect_id": "effect_" + "c" * 64, "challenge": "d" * 64, "native_plan": connections.native_manifest()}
    return connections.participant_receipt(native)


def test_frozen_receipt_exact_shape_and_challenge_excluded_from_evidence():
    body = receipt()
    assert set(body) == handoff.RECEIPT_FIELDS
    evidence = handoff.validate_receipt(body)
    assert evidence == {key: value for key, value in body.items() if key != "challenge"}
    assert handoff.digest(evidence) == handoff.digest(dict(reversed(list(evidence.items()))))
    assert set(handoff.CLAIMS) == {"contract_version", "server_id", "principal_id", "installation_ref",
        "operation_id", "step", "review_digest", "expected_revision", "epoch", "effect_id", "expires_at", "native_plan"}
    assert handoff.STEP == "native_effects" and handoff.TTL_S == connections.CONTRACT["bounds"]["challenge_ttl_s"]


@pytest.mark.parametrize("change", [
    {"path": "/private/file"}, {"outcome": "success"}, {"epoch": True}, {"expected_revision": -1},
    {"challenge": "unbounded"}, {"roles": []}, {"roles": [{"role": "path", "before_digest": "a" * 64, "after_digest": "b" * 64}]},
    {"phase": "preflight"}, {"roles": receipt()["roles"] * 2},
])
def test_receipt_rejects_untyped_or_private_payloads(change):
    body = {**copy.deepcopy(receipt()), **change}
    with pytest.raises(OperationError) as error:
        handoff.validate_receipt(body)
    assert error.value.status == 422


async def test_get_is_pure_and_post_claim_is_personal(installed, served_setup, source):
    daemon, principal, _ = installed
    _, ident = await connections.waiting_connection(installed, served_setup, source)
    before = daemon.journal.db.total_changes
    status, response = await served_setup("GET", f"/api/v1/operations/{ident}?installation_ref=desktop-a")
    assert status == 200 and "native_handoff" not in response["operation"]
    assert daemon.journal.db.total_changes == before
    body = connections.handoff_request(ident, "desktop-a")
    status, response = await served_setup("POST", connections.HANDOFF, body)
    assert status == 200, response
    assert response["native_handoff"]["contract_version"] == 1
    assert daemon.ops.get(ident)["external_refs"]["native_effects_sent"] is True
    for credential in (token(daemon, principal.actor, "observe"), token(daemon, "foreign", "observe", "manage")):
        assert (await served_setup("POST", connections.HANDOFF, body, credential=credential))[0] in {403, 404}
    daemon.managed_installation["principal_id"] = "other-personal-installation"
    assert (await served_setup("POST", connections.HANDOFF, body))[0] == 403


async def test_claim_ttl_starts_on_claim_and_reissues_expired_unclaimed_epoch(installed, served_setup, source):
    daemon, _, _ = installed
    _, ident = await connections.waiting_connection(installed, served_setup, source)
    row = daemon.journal.db.execute("SELECT request FROM operation_steps WHERE name='native_effects'").fetchone()
    old = json.loads(row[0])
    claimed_at = old["expires_at"] + 60
    with patch.object(handoff, "now", return_value=claimed_at):
        status, response = await served_setup("POST", connections.HANDOFF,
            connections.handoff_request(ident, "desktop-a"))
        assert status == 200, response
        native = response["native_handoff"]
        assert native["epoch"] == old["epoch"] + 1 and native["effect_id"] == old["effect_id"]
        assert native["expires_at"] == claimed_at + handoff.TTL_S
        assert (await served_setup("POST", connections.HANDOFF, connections.handoff_request(ident, "desktop-a")))[1] == response
    with patch.object(handoff, "now", return_value=native["expires_at"] + 1):
        assert (await served_setup("POST", connections.HANDOFF, connections.handoff_request(ident, "desktop-a")))[0] == 409


async def test_resume_reissues_only_never_claimed_challenge(installed, served_setup, source):
    daemon, _, _ = installed
    _, ident = await connections.waiting_connection(installed, served_setup, source)
    old = json.loads(daemon.journal.db.execute("SELECT request FROM operation_steps WHERE name='native_effects'").fetchone()[0])
    daemon.journal.db.execute("UPDATE operations SET status='needs_attention' WHERE operation_id=?", (ident,))
    with patch.object(handoff, "now", return_value=old["expires_at"] + 1):
        assert (await served_setup("POST", f"/api/v1/operations/{ident}/resume", {}))[0] == 200
        await settle_operations(daemon.ops)
    current = json.loads(daemon.journal.db.execute("SELECT request FROM operation_steps WHERE name='native_effects'").fetchone()[0])
    assert current["epoch"] == old["epoch"] + 1 and current["effect_id"] == old["effect_id"]
    assert daemon.ops.get(ident)["external_refs"]["native_effects_sent"] is False


async def test_processed_claim_with_lost_reply_keeps_original_fence(installed, served_setup, source, monkeypatch):
    daemon, _, _ = installed
    _, ident = await connections.waiting_connection(installed, served_setup, source)
    send = daemon.api._send_json
    processed = asyncio.Event()
    captured = []
    async def lose(writer, status, payload, *args):
        if status == 200 and "native_handoff" in payload and not captured:
            captured.append(payload)
            processed.set()
            writer.close()
            return
        return await send(writer, status, payload, *args)
    monkeypatch.setattr(daemon.api, "_send_json", lose)
    body = connections.handoff_request(ident, "desktop-a")
    try:
        response = await served_setup("POST", connections.HANDOFF, body)
    except (ValueError, IndexError):
        response = None
    assert processed.is_set(), response
    assert daemon.ops.get(ident)["external_refs"]["native_effects_sent"] is True
    assert (await served_setup("POST", connections.HANDOFF, body))[1] == captured[0]
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1


async def test_handoff_key_is_outside_journal(installed, served_setup, source):
    daemon, _, _ = installed
    _, ident = await connections.waiting_connection(installed, served_setup, source)
    native = await connections.claim_handoff(served_setup, ident)
    daemon.journal.db.execute("UPDATE api_sync_metadata SET signing_key=?", ("f" * 64,))
    again = await connections.claim_handoff(served_setup, ident)
    assert again["challenge"] == native["challenge"]
    assert daemon._admin_token not in "\n".join(daemon.journal.db.iterdump())


async def test_receipt_wire_limit_precedes_json_parsing(installed, served_setup):
    status, _ = await served_setup.raw_request(connections.RECEIPT, b"not-json" + b" " * connections.CONTRACT["bounds"]["receipt_bytes"])
    assert status == 413


async def test_claim_replay_after_completion_keeps_original_challenge(installed, served_setup, source, tmp_path):
    daemon, _, _ = installed
    _, ident = await connections.waiting_connection(installed, served_setup, source)
    native = await connections.claim_handoff(served_setup, ident)
    assert (await connections.DiskParticipant(tmp_path / "participant.json").send(served_setup, native))[0][0] == 200
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "succeeded"
    status, response = await served_setup("POST", connections.HANDOFF,
        connections.handoff_request(ident, "desktop-a"))
    assert status == 200, response
    assert all(response["native_handoff"][key] == native[key] for key in native if key != "receipt")
    assert response["native_handoff"]["receipt"]["outcome"] == "applied"


async def test_claim_key_change_refuses_unverifiable_envelope_but_keeps_original_evidence(
        installed, served_setup, source, tmp_path):
    daemon, _, _ = installed
    _, ident = await connections.waiting_connection(installed, served_setup, source)
    native = await connections.claim_handoff(served_setup, ident)
    before = daemon.ops.get(ident)
    daemon._admin_token = "R" * 64
    status, response = await served_setup("POST", connections.HANDOFF, connections.handoff_request(ident, "desktop-a"))
    assert status == 409 and response["error"]["code"] == "CONNECTION_HANDOFF_KEY_CHANGED"
    assert daemon.ops.get(ident) == before  # Key loss alone makes no assertion about the original local effect.
    assert (await connections.DiskParticipant(tmp_path / "participant.json").send(served_setup, native))[0][0] == 200
    await settle_operations(daemon.ops)
    assert daemon.ops.get(ident)["status"] == "succeeded"


async def test_background_polling_never_reissues_unclaimed_challenge(installed, served_setup, source):
    daemon, _, _ = installed
    _, ident = await connections.waiting_connection(installed, served_setup, source)
    first = daemon.journal.db.execute("SELECT request FROM operation_steps WHERE operation_id=? AND name='native_effects'", (ident,)).fetchone()[0]
    for offset in range(1, 13):
        with patch.object(handoff, "now", return_value=json.loads(first)["expires_at"] + offset * 300):
            daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (ident,))
            await settle_operations(daemon.ops)
    assert daemon.journal.db.execute("SELECT count(*) FROM api_events WHERE resource_id=? AND kind='connection.handoff.issued'", (ident,)).fetchone()[0] == 0
    assert daemon.journal.db.execute("SELECT request FROM operation_steps WHERE operation_id=? AND name='native_effects'", (ident,)).fetchone()[0] == first
    assert (await served_setup("POST", connections.HANDOFF, connections.handoff_request(ident, "desktop-a")))[0] == 200


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
async def test_identical_receipt_replay_is_idempotent_in_every_terminal_state(installed, served_setup, source, tmp_path, terminal):
    from bat_agent_connector.operations import UNCERTAIN_RETRY_S
    daemon, _, _ = installed
    _, ident = await connections.waiting_connection(installed, served_setup, source)
    native = await connections.claim_handoff(served_setup, ident)
    outcome = {"succeeded": "applied", "failed": "refused", "cancelled": "uncertain"}[terminal]
    (status, response), receipt = await connections.DiskParticipant(tmp_path / "participant.json").send(served_setup, native, outcome=outcome)
    assert status == 200, response
    for _ in range(len(UNCERTAIN_RETRY_S) + 1):
        daemon.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (ident,))
        await settle_operations(daemon.ops)
    if terminal == "cancelled":
        assert (await served_setup("POST", f"/api/v1/operations/{ident}/cancel", {}))[0] == 200
    assert daemon.ops.get(ident)["status"] == terminal
    status, replay = await served_setup("POST", connections.RECEIPT, receipt)
    assert status == 200 and replay["replayed"] is True
    different = copy.deepcopy(receipt)
    different["roles"][0]["after_digest"] = "e" * 64
    different["roles"][0]["observation"] = "present"
    for other in (different, {**receipt, "challenge": "0" * 64}, {**receipt, "epoch": receipt["epoch"] + 1}):
        status, refused = await served_setup("POST", connections.RECEIPT, other)
        assert status == 409 and refused["error"]["code"] == "CONNECTION_OPERATION_TERMINAL"
    assert daemon.ops.get(ident)["status"] == terminal
