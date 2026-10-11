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
