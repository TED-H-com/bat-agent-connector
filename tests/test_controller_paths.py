"""Indirect resource actions retain original controller and incarnation evidence."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from bat_agent_connector import api_auth, bulk_approval, cleanup, controller_authority, registry
from bat_agent_connector import orchestration_operations as relay
from bat_agent_connector.errors import ResourceReadOnly
from tests import test_api_v1 as api
from tests.conftest import adopt, controller_scope
from tests.operation_helpers import settle_operations
from tests.test_lifecycle import _add_wt_claude

daemon = api.daemon
OWNER = api_auth.Principal("operator-dashboard", frozenset(api_auth.SCOPES))
OTHER = api_auth.Principal("other-controller", OWNER.scopes)
ADMIN = api_auth.Principal(api_auth.ADMIN_ACTOR, frozenset(), admin=True)
SID = "wt-claude-0007"


def replace_nonce(sid):
    path = registry.registry_path()
    data = json.loads(path.read_text())
    rows = data if isinstance(data, list) else data["sessions"]
    next(row for row in rows if row["session_id"] == sid)["controller_incarnation"] = "f" * 32
    path.write_text(json.dumps(data))


def resource(sid, *, kind="session", path="/srv/demo"):
    row = registry.get("h1", sid)
    item = {"kind": kind, "host": "h1", "registry": row, "path": path,
            "creation_evidence": {"intent": f"{sid}@{row['created_at']}"}}
    item["controller_source"] = cleanup._controller_source(item, [row])
    return item


@pytest.mark.parametrize("caller", [OTHER, ADMIN])
async def test_foreign_failover_never_starts_successor(daemon, mock, caller):
    with controller_scope(daemon.journal, OWNER):
        _add_wt_claude(mock, SID)
    op, _ = daemon.ops.create(caller, action="session.failover", target={"host": "h1", "session_id": SID},
                              idempotency_key="foreign-failover")
    await settle_operations(daemon.ops)
    result = daemon.ops.get(op["operation_id"])
    assert result["status"] == "failed", result
    assert result["error_code"] == "FOREIGN_CONTROLLER", result
    assert not api.write_frames(mock)
    assert len(registry.list_entries("h1")) == 1


@pytest.mark.parametrize("caller", [OTHER, ADMIN])
async def test_bulk_preview_never_selects_other_controllers_prompt(daemon, mock, caller):
    sid = "sess-codex-0002"
    with controller_scope(daemon.journal, OWNER):
        adopt(sid, agent_preset="codex-agent")
    mock.states[sid]["pendingPermission"] = {"toolUseId": "private-permission", "toolName": "Bash"}
    token = daemon._admin_token if caller.admin else api.token(daemon, caller.actor, "observe", "operate")
    principal = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token)
    doc = await bulk_approval.preview(daemon.ops, principal, {"host": "h1"})
    item = next(item for item in doc["items"] if item["session_id"] == sid)
    assert item["eligible"] is False and item["code"] == "FOREIGN_CONTROLLER"
    assert "prompt" not in item
    assert not api.write_frames(mock)


@pytest.mark.parametrize("kind", ["session", "worktree", "local_branch"])
def test_cleanup_foreign_ended_shared_consumer_remains_protected(daemon, kind):
    with controller_scope(daemon.journal, OWNER):
        adopt("owner")
        item = resource("owner", kind=kind)
    with controller_scope(daemon.journal, OTHER):
        adopt("ended-neighbor", cwd="/srv/demo/nested")
        registry.update("h1", "ended-neighbor", status="ended")
    with controller_scope(daemon.journal, OWNER):
        with pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
            cleanup._require_resource_controller(daemon.ops, item)


def test_cleanup_orphaned_git_carrier_uses_original_operation_not_path(daemon):
    original = {"external_refs": {"admission_binding": {
        "controller_authority": controller_authority.binding(daemon.journal.db, OWNER)}}}
    ops = SimpleNamespace(_row=lambda op_id: original, journal=daemon.journal)
    item = {"kind": "temporary", "host": "h1", "path": "/srv/managed/temp",
            "controller_source": {"kind": "operation", "operation_id": "op_original"}}
    with controller_scope(daemon.journal, OWNER):
        cleanup._require_resource_controller(ops, item)
    for caller in (OTHER, ADMIN):
        with controller_scope(daemon.journal, caller), pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
            cleanup._require_resource_controller(ops, item)
    item["controller_source"] = None
    with controller_scope(daemon.journal, OWNER), pytest.raises(ResourceReadOnly, match="CONTROLLER_AUTHORITY_REQUIRED"):
        cleanup._require_resource_controller(ops, item)


@pytest.mark.parametrize("check", ["relay", "cleanup"])
def test_saved_identity_cannot_use_same_id_replacement_or_fill_missing_old_nonce(daemon, check):
    with controller_scope(daemon.journal, OWNER):
        adopt("owner")
        saved = relay.record("h1", "owner")
        item = resource("owner")
        call = (lambda: relay.require_record("h1", "owner", saved)) if check == "relay" else (
            lambda: cleanup._require_resource_controller(daemon.ops, item))
        call()
        replace_nonce("owner")
        with pytest.raises(ResourceReadOnly, match="CONTROLLER_INCARNATION_CHANGED"):
            call()
        if check == "relay":
            saved.pop("controller_incarnation")
        else:
            item["controller_source"].pop("controller_incarnation")
        with pytest.raises(ResourceReadOnly, match="CONTROLLER_OWNER_UNPROVEN"):
            call()


def test_task_binding_ignores_controller_keys_but_keeps_task_identity():
    task = {"task_id": "task-original", "control_version": 4, "session_id": SID, "role": "lead"}
    full = {**copy.deepcopy(task), "controller_authority": {"actor": "owner"},
            "session_controller": {"host": "h1", "session_id": SID, "controller_incarnation": "a" * 32}}
    assert relay.task_binding(full) == task
    assert relay.task_binding({"controller_authority": {}, "session_controller": {}}) is None
    full["control_version"] += 1
    assert relay.task_binding(full) != task
