"""W02: authenticated ownership is independent of provenance and host/path names."""
from __future__ import annotations

import copy
import json

import pytest

from bat_agent_connector import controller_authority as authority
from bat_agent_connector import platform_files, registry, resource_policy
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.errors import ResourceReadOnly
from bat_agent_connector.task_journal import Journal


@pytest.fixture
def identities(tmp_path):
    journal = Journal(tmp_path / "authority.db")
    first = authority.binding(journal.db, Principal("operator-a", frozenset({"operate", "start"})))
    second = authority.binding(journal.db, Principal("operator-b", frozenset({"operate", "start"}), admin=True))
    yield journal, first, second
    journal.close()


def test_binding_reuses_durable_server_and_actor_identity(identities, tmp_path):
    journal, first, second = identities
    again = authority.binding(journal.db, Principal("operator-a", frozenset({"observe"}), admin=True))
    assert authority.same_controller(first, again)
    assert not authority.same_controller(first, second)
    journal.close()
    reopened = Journal(tmp_path / "authority.db")
    try:
        assert first == authority.binding(reopened.db, Principal("operator-a", frozenset({"operate", "start"})))
    finally:
        reopened.close()


def test_context_is_copied_restored_and_cannot_inherit_authority(identities):
    journal, first, second = identities
    with authority.use(first, journal):
        first_copy = authority.current()
        first_copy["scopes"].append("admin")
        assert authority.current() == first
        with authority.use(second, journal):
            with pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
                authority.require_binding(first)
        assert authority.current() == first
        with authority.use(None):
            assert registry.list_entries() == []
            with pytest.raises(ResourceReadOnly, match="CONTROLLER_AUTHORITY_REQUIRED"):
                registry.reserve("h1", {"session_id": "unbound"}, 8)
        assert authority.current() == first
    assert authority.current() is None


def test_creation_grant_requires_reserved_incarnation_at_final_frame(identities):
    journal, first, second = identities
    with authority.use(first, journal):
        grant = resource_policy._create_grant("h1", "new-session", None, None)
        with pytest.raises(ResourceReadOnly, match="CONTROLLER_OWNER_UNPROVEN"):
            resource_policy.check_grant(grant, "h1", "claude:start-session", {"sessionId": "new-session"})
        registry.reserve("h1", {"session_id": "new-session"}, 8)
        row = registry.get("h1", "new-session")
        assert row["controller_authority"] == first
        assert row["controller_incarnation"] == grant.controller_incarnation
        resource_policy.check_grant(grant, "h1", "claude:start-session", {"sessionId": "new-session"})
    with authority.use(second, journal):
        with pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
            resource_policy.check_grant(grant, "h1", "claude:start-session", {"sessionId": "new-session"})
    row["controller_incarnation"] = "0" * 32
    platform_files.atomic_write(registry.registry_path(), json.dumps({"sessions": [row]}).encode())
    with authority.use(first, journal):
        with pytest.raises(ResourceReadOnly, match="CONTROLLER_INCARNATION_CHANGED"):
            resource_policy.check_grant(grant, "h1", "claude:start-session", {"sessionId": "new-session"})


@pytest.mark.parametrize("link", ["replaces", "failover_of", "shares_worktree_with", "lead_session_id", "cwd"])
def test_foreign_carriers_cannot_be_reserved_even_by_admin(identities, link):
    journal, first, second = identities
    with authority.use(first, journal):
        registry.reserve("h1", {"session_id": "original", "cwd": "/srv/managed/one"}, 8)
        registry.update("h1", "original", status="ended")
    before = registry.registry_path().read_bytes()
    entry = {"session_id": "replacement"}
    kwargs = {}
    if link == "replaces":
        kwargs[link] = "original"
    else:
        entry[link] = "/srv/managed/one" if link == "cwd" else "original"
    with authority.use(second, journal), pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
        registry.reserve("h1", entry, 8, **kwargs)
    assert registry.registry_path().read_bytes() == before


def test_no_legacy_adoption_or_ownership_rebinding(identities):
    journal, first, second = identities
    legacy = {"host": "h1", "session_id": "old", "cwd": "/srv/managed/one", "status": "active"}
    platform_files.atomic_write(registry.registry_path(), json.dumps({"sessions": [legacy]}).encode())
    before = registry.registry_path().read_bytes()
    with authority.use(first, journal):
        for fn in (lambda: registry.reserve("h1", {"session_id": "old"}, 8),
                   lambda: registry.ensure_existing("h1", legacy)):
            with pytest.raises(ResourceReadOnly):
                fn()
        assert registry.registry_path().read_bytes() == before
        registry.reserve("h1", {"session_id": "new"}, 8)
        row = copy.deepcopy(registry.get("h1", "new"))
        with pytest.raises(ResourceReadOnly, match="CONTROLLER_IMMUTABLE"):
            registry.update("h1", "new", controller_authority=second)
        assert registry.get("h1", "new") == row


def test_server_identity_cannot_be_transplanted(identities, tmp_path):
    _journal, first, _second = identities
    other = Journal(tmp_path / "other-server.db")
    try:
        with pytest.raises(ResourceReadOnly, match="CONTROLLER_SERVER_CHANGED"), authority.use(first, other):
            pytest.fail("foreign server scope was entered")
        with authority.use(authority.binding(other.db, Principal(first["actor"], frozenset(first["scopes"]))), other):
            with pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
                authority.require_binding(first)
    finally:
        other.close()


def test_recovery_restores_only_original_durable_evidence(identities):
    journal, first, second = identities
    with authority.use(first, journal):
        registry.reserve("h1", {"session_id": "original", "cwd": "/srv/managed/one"}, 8)
        original = registry.get("h1", "original")
        platform_files.atomic_write(registry.registry_path(), b'{"sessions": []}')
        missing_nonce = {k: v for k, v in original.items() if k != "controller_incarnation"}
        with pytest.raises(ResourceReadOnly, match="CONTROLLER_OWNER_UNPROVEN"):
            registry.ensure_existing("h1", missing_nonce)
        assert registry.list_entries() == []
    with authority.use(second, journal), pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
        registry.ensure_existing("h1", original)
    with authority.use(first, journal):
        registry.ensure_existing("h1", original)
        recovered = registry.get("h1", "original")
        assert recovered["controller_authority"] == original["controller_authority"]
        assert recovered["controller_incarnation"] == original["controller_incarnation"]
        assert recovered["status"] == "active"


def test_warm_reuse_rechecks_controller_before_task_transfer(identities):
    journal, first, second = identities
    entry = {"session_id": "warm", "task_id": "before", "role": "lead", "workspace_id": "bat-workspace",
             "cwd": "/srv/managed/work", "worktree_path": "/srv/managed/work", "branch": "bat/task"}
    with authority.use(first, journal):
        registry.reserve("h1", entry, 8)
        registry.update("h1", "warm", status="active")
    before = registry.registry_path().read_bytes()
    params = {"previous_task_id": "before", "task_id": "after", "workspace_id": "bat-workspace",
              "cwd": entry["cwd"], "branch": entry["branch"]}
    with authority.use(second, journal), pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
        registry.claim_warm("h1", "warm", **params)
    assert registry.registry_path().read_bytes() == before
    with authority.use(first, journal):
        registry.claim_warm("h1", "warm", **params)
    assert registry.get("h1", "warm")["task_id"] == "after"
    assert registry.get("h1", "warm")["controller_authority"] == first


async def test_pm_scope_checks_current_membership_without_workspace_adoption(tmp_path):
    from bat_agent_connector import pm_workspace
    from bat_agent_connector.operations import OperationService
    from tests.test_pm_workspace import OTHER, OWNER, create, member

    journal = Journal(tmp_path / "pm.db")
    ops = OperationService(journal)
    pm_workspace.install(ops)
    try:
        workspace = await create(ops)
        different = await create(ops)
        bound = authority.binding(journal.db, OWNER, pm_workspace_id=workspace["workspace_id"])
        unscoped = authority.binding(journal.db, OWNER)
        with authority.use(bound, journal):
            registry.reserve("h1", {"session_id": "pm-session"}, 8)
        row = registry.get("h1", "pm-session")
        with authority.use(unscoped, journal):
            assert authority.require_entry(row) == row
        with authority.use(unscoped), pytest.raises(ResourceReadOnly, match="CONTROLLER_WORKSPACE_UNAVAILABLE"):
            authority.require_entry(row)
        other_scope = authority.binding(journal.db, OWNER, pm_workspace_id=different["workspace_id"])
        with authority.use(other_scope, journal), pytest.raises(ResourceReadOnly, match="CONTROLLER_WORKSPACE_CHANGED"):
            authority.require_entry(row)
        workspace = await member(ops, workspace, OTHER.actor, "owner")
        await member(ops, workspace, OWNER.actor, None)
        with authority.use(bound, journal), pytest.raises(ResourceReadOnly, match="CONTROLLER_MEMBERSHIP_CHANGED"):
            authority.require_entry(row)
        # Revocation neither relabels the original resource nor grants another owner its controller.
        with authority.use(authority.binding(journal.db, OTHER), journal), pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
            authority.require_entry(row)
        assert registry.get("h1", "pm-session")["controller_authority"] == bound
    finally:
        journal.close()


async def test_policy_preserves_provenance_but_uses_current_caller(fleet_factory, mock, controller_context):
    from tests.test_resource_policy import add_managed_wt, all_tiers

    journal, owner = controller_context
    sid = add_managed_wt(mock)
    fleet = all_tiers(fleet_factory)
    other = authority.binding(journal.db, Principal("other-controller", frozenset({"operate"}), admin=True))
    try:
        owned = await resource_policy.session_policy(fleet, "h1", sid)
        assert owned["actions"]["session.send"]["allowed"]
        with authority.use(other, journal):
            foreign = await resource_policy.session_policy(fleet, "h1", sid)
        assert foreign["provenance"] == owned["provenance"]
        assert foreign["api_access"] == "read_only"
        assert foreign["read_only_code"] == "FOREIGN_CONTROLLER"
        assert foreign["actions"]["session.send"]["code"] == "FOREIGN_CONTROLLER"
        with authority.use(None):
            unknown_caller = await resource_policy.session_policy(fleet, "h1", sid)
        assert unknown_caller["provenance"] == owned["provenance"]
        assert unknown_caller["api_access"] == "read_only"
        assert unknown_caller["read_only_code"] == "CONTROLLER_AUTHORITY_REQUIRED"
        assert unknown_caller["actions"]["session.send"]["code"] == "CONTROLLER_AUTHORITY_REQUIRED"
        assert registry.get("h1", sid)["controller_authority"] == owner
    finally:
        await fleet.close()


async def test_direct_service_helper_cannot_send_without_bound_controller(fleet_factory, mock):
    from bat_agent_connector import service
    from tests.test_resource_policy import add_managed_wt, all_tiers, write_frames

    sid = add_managed_wt(mock)
    fleet = all_tiers(fleet_factory)
    try:
        with authority.use(None), pytest.raises(ResourceReadOnly, match="CONTROLLER_AUTHORITY_REQUIRED"):
            await service.session_send(fleet, "h1", sid, "fixture", confirm=True)
        assert write_frames(mock) == []
    finally:
        await fleet.close()


def test_corrupt_actor_controller_pair_is_not_an_admitted_binding(identities):
    journal, first, second = identities
    corrupt = {**first, "actor": second["actor"]}
    with pytest.raises(ResourceReadOnly, match="CONTROLLER_AUTHORITY_INVALID"), authority.use(corrupt, journal):
        pytest.fail("corrupt identity was accepted")
    assert not authority.same_controller(first, corrupt)


def test_foreign_caller_cannot_preclaim_an_unsent_reservation(identities):
    journal, first, second = identities
    with authority.use(first, journal):
        registry.reserve("h1", {"session_id": "unsent", "start_sent": False}, 8)
    before = registry.registry_path().read_bytes()
    with authority.use(second, journal), pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
        registry.claim_unsent("h1", "unsent")
    with authority.use(None), pytest.raises(ResourceReadOnly, match="CONTROLLER_AUTHORITY_REQUIRED"):
        registry.claim_unsent("h1", "unsent")
    assert registry.registry_path().read_bytes() == before


@pytest.mark.parametrize("status,sent", [("starting", True), ("uncertain", True), ("failed", None), ("active", True)])
def test_sent_or_settled_legacy_readback_needs_no_new_start_claim(status, sent):
    row = {"host": "h1", "session_id": "legacy", "status": status, "start_sent": sent}
    platform_files.atomic_write(registry.registry_path(), json.dumps({"sessions": [row]}).encode())
    before = registry.registry_path().read_bytes()
    with authority.use(None):
        assert registry.claim_unsent("h1", "legacy") == "not_unsent"
    assert registry.registry_path().read_bytes() == before
