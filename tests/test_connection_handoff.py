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
    return {"contract_version": 1, "installation_ref": "desktop-a", "operation_id": "op_" + "a" * 32,
        "step": "native_effects", "review_digest": "b" * 64, "expected_revision": 0,
        "epoch": 1, "effect_id": "effect_" + "c" * 64, "challenge": "d" * 64,
        "outcome": "applied", "code": None, "roles": [{"role": "inventory", "before_digest": "e" * 64, "after_digest": "f" * 64}]}


def test_frozen_receipt_exact_shape_and_challenge_excluded_from_evidence():
    body = receipt()
    assert set(body) == handoff.RECEIPT_FIELDS
    evidence = handoff.validate_receipt(body)
    assert evidence == {key: value for key, value in body.items() if key != "challenge"}
    assert handoff.digest(evidence) == handoff.digest(dict(reversed(list(evidence.items()))))
    assert set(handoff.CLAIMS) == {"contract_version", "server_id", "principal_id", "installation_ref",
        "operation_id", "step", "review_digest", "expected_revision", "epoch", "effect_id", "expires_at"}
    assert handoff.STEP == "native_effects" and handoff.TTL_S == 300


@pytest.mark.parametrize("change", [
    {"path": "/private/file"}, {"outcome": "success"}, {"epoch": True}, {"expected_revision": -1},
    {"challenge": "unbounded"}, {"roles": []}, {"roles": [{"role": "path", "before_digest": "a" * 64, "after_digest": "b" * 64}]},
    {"outcome": "unchanged"}, {"roles": receipt()["roles"] * 2},
])
def test_receipt_rejects_untyped_or_private_payloads(change):
    body = {**copy.deepcopy(receipt()), **change}
    with pytest.raises(OperationError) as error:
        handoff.validate_receipt(body)
    assert error.value.status == 422


async def test_r2_get_is_pure_and_post_claim_is_personal(installed, served_setup, source):
    daemon, principal, _ = installed
    _, ident = await connections._r2_waiting(installed, served_setup, source)
    before = daemon.journal.db.total_changes
    status, response = await served_setup("GET", f"/api/v1/operations/{ident}?installation_ref=desktop-a")
    assert status == 200 and "native_handoff" not in response["operation"]
    assert daemon.journal.db.total_changes == before
    body = {"operation_id": ident, "installation_ref": "desktop-a"}
    status, response = await served_setup("POST", connections.HANDOFF, body)
    assert status == 200, response
    assert response["native_handoff"]["contract_version"] == 1
    assert daemon.ops.get(ident)["external_refs"]["native_effects_sent"] is True
    for credential in (token(daemon, principal.actor, "observe"), token(daemon, "foreign", "observe", "manage")):
        assert (await served_setup("POST", connections.HANDOFF, body, credential=credential))[0] in {403, 404}
    daemon.managed_installation["principal_id"] = "other-personal-installation"
    assert (await served_setup("POST", connections.HANDOFF, body))[0] == 403


async def test_r2_claim_ttl_starts_on_claim_and_reissues_expired_unclaimed_epoch(installed, served_setup, source):
    daemon, _, _ = installed
    _, ident = await connections._r2_waiting(installed, served_setup, source)
    row = daemon.journal.db.execute("SELECT request FROM operation_steps WHERE name='native_effects'").fetchone()
    old = json.loads(row[0])
    claimed_at = old["expires_at"] + 60
    with patch.object(handoff, "now", return_value=claimed_at):
        status, response = await served_setup("POST", connections.HANDOFF,
            {"operation_id": ident, "installation_ref": "desktop-a"})
        assert status == 200, response
        native = response["native_handoff"]
        assert native["epoch"] == old["epoch"] + 1 and native["effect_id"] == old["effect_id"]
        assert native["expires_at"] == claimed_at + handoff.TTL_S
        assert (await served_setup("POST", connections.HANDOFF, {"operation_id": ident, "installation_ref": "desktop-a"}))[1] == response
    with patch.object(handoff, "now", return_value=native["expires_at"] + 1):
        assert (await served_setup("POST", connections.HANDOFF, {"operation_id": ident, "installation_ref": "desktop-a"}))[0] == 409


async def test_r2_resume_reissues_only_never_claimed_challenge(installed, served_setup, source):
    daemon, _, _ = installed
    _, ident = await connections._r2_waiting(installed, served_setup, source)
    old = json.loads(daemon.journal.db.execute("SELECT request FROM operation_steps WHERE name='native_effects'").fetchone()[0])
    daemon.journal.db.execute("UPDATE operations SET status='needs_attention' WHERE operation_id=?", (ident,))
    with patch.object(handoff, "now", return_value=old["expires_at"] + 1):
        assert (await served_setup("POST", f"/api/v1/operations/{ident}/resume", {}))[0] == 200
        await settle_operations(daemon.ops)
    current = json.loads(daemon.journal.db.execute("SELECT request FROM operation_steps WHERE name='native_effects'").fetchone()[0])
    assert current["epoch"] == old["epoch"] + 1 and current["effect_id"] == old["effect_id"]
    assert daemon.ops.get(ident)["external_refs"]["native_effects_sent"] is False


async def test_r2_processed_claim_with_lost_reply_keeps_original_fence(installed, served_setup, source, monkeypatch):
    daemon, _, _ = installed
    _, ident = await connections._r2_waiting(installed, served_setup, source)
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
    body = {"operation_id": ident, "installation_ref": "desktop-a"}
    try:
        response = await served_setup("POST", connections.HANDOFF, body)
    except (ValueError, IndexError):
        response = None
    assert processed.is_set(), response
    assert daemon.ops.get(ident)["external_refs"]["native_effects_sent"] is True
    assert (await served_setup("POST", connections.HANDOFF, body))[1] == captured[0]
    assert daemon.journal.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1


async def test_r2_handoff_key_is_outside_journal(installed, served_setup, source):
    daemon, _, _ = installed
    _, ident = await connections._r2_waiting(installed, served_setup, source)
    native = await connections._r2_dispatch(served_setup, ident)
    daemon.journal.db.execute("UPDATE api_sync_metadata SET signing_key=?", ("f" * 64,))
    again = await connections._r2_dispatch(served_setup, ident)
    assert again["challenge"] == native["challenge"]
    assert daemon._admin_token not in "\n".join(daemon.journal.db.iterdump())


async def test_r2_receipt_wire_limit_precedes_json_parsing(installed, served_setup):
    status, _ = await served_setup.raw_request(connections.RECEIPT, b"not-json" + b" " * 8192)
    assert status == 413
