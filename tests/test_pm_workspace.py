"""Workspace namespace, membership and durable local effects; no live runtimes."""
from __future__ import annotations

import json
import uuid

import pytest

from bat_agent_connector import api_auth, dashboard_sync
from bat_agent_connector import pm_workspace as pm
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.operation_helpers import settle_operations

OWNER = api_auth.Principal("workspace-owner", frozenset({"observe", "manage"}))
OTHER = api_auth.Principal("other-owner", frozenset({"observe", "manage"}))
READER = api_auth.Principal("workspace-reader", frozenset({"observe", "manage"}))
ADMIN = api_auth.Principal("local-admin", frozenset(), admin=True)


@pytest.fixture
def ops(tmp_path):
    journal = Journal(tmp_path / "journal.db")
    service = OperationService(journal)
    pm.install(service)
    yield service
    journal.close()


def submit(ops, action, *, who=OWNER, target=None, params=None, pre=None, key=None):
    return ops.create(who, action=action, target=target or {}, params=params or {},
                      preconditions=pre or {}, idempotency_key=key or uuid.uuid4().hex)


async def finish(ops, op):
    await settle_operations(ops)
    result = ops.get(op["operation_id"])
    assert result["status"] == "succeeded", result
    return result["result"]["workspace"]


async def create(ops, *, who=OWNER, name="Example", key=None):
    op, _ = submit(ops, "pm.workspace.create", who=who,
                   params={"name": name, "repositories": ["example/project"]}, key=key)
    return await finish(ops, op)


def member_op(ops, workspace, actor, role, *, who=OWNER, key=None):
    return submit(ops, "pm.workspace.member.set", who=who,
                  target={"workspace_id": workspace["workspace_id"]}, params={"actor": actor, "role": role},
                  pre={"expected_version": workspace["version"]}, key=key)


async def member(ops, workspace, actor, role, *, who=OWNER):
    op, _ = member_op(ops, workspace, actor, role, who=who)
    return await finish(ops, op)


async def test_independent_workspace_identity_and_shared_clients_survive_reopen(ops):
    first = await create(ops)
    second = await create(ops)
    assert first["workspace_id"] != second["workspace_id"]
    assert first["repositories"] == second["repositories"]
    assert first["server_id"] == dashboard_sync.identity(ops.journal, OWNER)["server_id"]
    assert first["membership"]["controller_id"] == second["membership"]["controller_id"]
    client_two = api_auth.Principal(OWNER.actor, OWNER.scopes, credential_id="another-credential")
    assert pm.list_workspaces(ops.db, client_two) == pm.list_workspaces(ops.db, OWNER)
    other_journal = Journal(ops.journal.path)
    try:
        pm.schema(other_journal)
        assert pm.get_workspace(other_journal.db, client_two, first["workspace_id"]) == pm.get_workspace(ops.db, OWNER, first["workspace_id"])
        assert pm.controller_for(other_journal.db, client_two) == first["membership"]["controller_id"]
        assert pm.controller_for(other_journal.db, OTHER) != first["membership"]["controller_id"]
        before = other_journal.db.total_changes
        pm.list_workspaces(other_journal.db, ADMIN)
        pm.controller_for(other_journal.db, ADMIN)
        assert other_journal.db.total_changes == before  # reads do not provision identities
    finally:
        other_journal.close()


def test_additive_schema_does_not_assign_legacy_data_or_consume_data_version(tmp_path):
    journal = Journal(tmp_path / "legacy.db")
    try:
        journal.db.execute("""INSERT INTO projects(project_id,name,repositories,created_by,operation_id,created_at,updated_at)
            VALUES('prj_legacy','Legacy','["example/project"]','legacy','op_legacy',1,1)""")
        journal.api_event("legacy", "original", "legacy.saved", {"kept": True}, actor="legacy")
        before = {table: [tuple(row) for row in journal.db.execute(f"SELECT * FROM {table}")]  # noqa: S608 - fixed test tables
                  for table in ("projects", "api_events", "api_sync_metadata")}
        version = journal.db.execute("PRAGMA user_version").fetchone()[0]
        pm.schema(journal)
        pm.schema(journal)
        assert pm.list_workspaces(journal.db, ADMIN) == []
        assert journal.db.execute("PRAGMA user_version").fetchone()[0] == version
        for table, rows in before.items():
            assert [tuple(row) for row in journal.db.execute(f"SELECT * FROM {table}")] == rows  # noqa: S608
    finally:
        journal.close()


async def test_membership_separates_workspaces_roles_and_private_member_list(ops):
    workspace = await create(ops)
    other = await create(ops, who=OTHER)
    assert [w["workspace_id"] for w in pm.list_workspaces(ops.db, OTHER)] == [other["workspace_id"]]
    with pytest.raises(OperationError) as denied:
        pm.get_workspace(ops.db, OTHER, workspace["workspace_id"])
    assert denied.value.status == 404
    workspace = await member(ops, workspace, READER.actor, "reader")
    assert "members" not in pm.get_workspace(ops.db, READER, workspace["workspace_id"])
    assert len(pm.get_workspace(ops.db, OWNER, workspace["workspace_id"])["members"]) == 2
    assert pm.require_member(ops.db, READER, workspace["workspace_id"])["membership"]["role"] == "reader"
    with pytest.raises(OperationError, match="FORBIDDEN"):
        pm.require_member(ops.db, READER, workspace["workspace_id"], write=True)
    with pytest.raises(OperationError, match="FORBIDDEN"):
        member_op(ops, workspace, READER.actor, "owner", who=READER)
    workspace = await member(ops, workspace, READER.actor, "contributor")
    pm.require_member(ops.db, READER, workspace["workspace_id"], write=True)
    with pytest.raises(OperationError, match="FORBIDDEN"):
        pm.require_member(ops.db, READER, workspace["workspace_id"], decide=True)
    await member(ops, workspace, OTHER.actor, "owner", who=ADMIN)


async def test_last_owner_and_self_removal_are_durable(ops):
    workspace = await create(ops)
    for role in (None, "reader", "contributor"):
        with pytest.raises(OperationError, match="LAST_OWNER"):
            member_op(ops, workspace, OWNER.actor, role, who=ADMIN)
    workspace = await member(ops, workspace, OTHER.actor, "owner")
    workspace = await member(ops, workspace, OWNER.actor, None)
    assert workspace["membership"]["role"] is None
    with pytest.raises(OperationError, match="NOT_FOUND"):
        pm.get_workspace(ops.db, OWNER, workspace["workspace_id"])
    assert pm.get_workspace(ops.db, OTHER, workspace["workspace_id"])["members"][0]["actor"] == OTHER.actor


async def test_concurrent_membership_intents_recheck_version_inside_effect(ops):
    workspace = await create(ops)
    first, _ = member_op(ops, workspace, OTHER.actor, "owner")
    second, _ = member_op(ops, workspace, READER.actor, "reader")
    await settle_operations(ops)
    results = [ops.get(op["operation_id"]) for op in (first, second)]
    assert sorted(op["status"] for op in results) == ["failed", "succeeded"]
    assert [op["error_code"] for op in results if op["status"] == "failed"] == ["VERSION_CONFLICT"]
    assert pm.get_workspace(ops.db, OWNER, workspace["workspace_id"])["version"] == 2


async def test_actor_scoped_replay_survives_stale_precondition_without_duplicate_members(ops):
    workspace = await create(ops, key="same-create")
    create_op, created = submit(ops, "pm.workspace.create", key="same-create",
                                params={"name": "Example", "repositories": ["example/project"]})
    assert not created and create_op["result"]["workspace"]["workspace_id"] == workspace["workspace_id"]
    op, _ = member_op(ops, workspace, READER.actor, "reader", key="member-key")
    await finish(ops, op)
    replay, created = member_op(ops, workspace, READER.actor, "reader", key="member-key")
    assert not created and replay["operation_id"] == op["operation_id"]
    assert pm.get_workspace(ops.db, OWNER, workspace["workspace_id"])["version"] == 2
    with pytest.raises(OperationError, match="IDEMPOTENCY_CONFLICT"):
        member_op(ops, workspace, READER.actor, "owner", key="member-key")
    other_workspace = await create(ops, who=OTHER, key="same-create")
    assert other_workspace["workspace_id"] != workspace["workspace_id"]


async def test_revoke_membership_blocks_pending_mutation_replay_and_visibility(ops):
    workspace = await create(ops)
    workspace = await member(ops, workspace, OTHER.actor, "owner")
    pending, _ = member_op(ops, workspace, READER.actor, "reader", who=OTHER, key="pending")
    removal, _ = member_op(ops, workspace, OTHER.actor, None)
    await ops.actions[removal["action"]].run(OpContext(ops, removal))  # commit removal before pending runner
    await settle_operations(ops)
    assert ops.get(pending["operation_id"])["error_code"] == "NOT_FOUND"
    assert not pm.operation_visible(ops.db, OTHER, pending)
    with pytest.raises(OperationError, match="FORBIDDEN"):
        member_op(ops, workspace, READER.actor, "reader", who=OTHER, key="pending")
    assert ops.db.execute("SELECT 1 FROM pm_workspace_members WHERE actor=?", (READER.actor,)).fetchone() is None


async def test_crash_after_local_commit_recovers_original_receipt(ops):
    op, _ = submit(ops, "pm.workspace.create", params={"name": "Crash recovery"})
    original = await ops.actions[op["action"]].run(OpContext(ops, op))
    count = ops.db.execute("SELECT COUNT(*) FROM api_events").fetchone()[0]
    journal = Journal(ops.journal.path)
    try:
        recovered = OperationService(journal)
        pm.install(recovered)
        await settle_operations(recovered)
        assert recovered.get(op["operation_id"])["result"] == original
        assert journal.db.execute("SELECT COUNT(*) FROM pm_workspaces").fetchone()[0] == 1
        assert journal.db.execute("SELECT COUNT(*) FROM api_events WHERE kind='pm.workspace.created'").fetchone()[0] == 1
        assert count > 0
    finally:
        journal.close()


@pytest.mark.parametrize("extra", [{"actor": "local-admin"}, {"controller_id": "claimed"}, {"admin": True}, {"workspace_id": "claimed"}])
def test_claimed_authority_in_request_is_refused(ops, extra):
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        submit(ops, "pm.workspace.create", params={"name": "Example", **extra})
    assert pm.list_workspaces(ops.db, ADMIN) == []


async def test_authenticated_binding_omits_credentials_and_scope_is_required(ops):
    who = api_auth.Principal(OWNER.actor, OWNER.scopes, credential_id="private-credential-identifier")
    op, _ = submit(ops, "pm.workspace.create", who=who, params={"name": "Example"})
    assert "private-credential" not in json.dumps(op)
    assert pm.admitted_principal(OpContext(ops, op)) == OWNER
    with pytest.raises(OperationError, match="FORBIDDEN"):
        submit(ops, "pm.workspace.create", who=api_auth.Principal("viewer", frozenset({"observe"})), params={"name": "Denied"})
    forged = {**op, "actor": "different-actor"}
    with pytest.raises(OperationError, match="AUTHORITY_UNAVAILABLE"):
        pm.admitted_principal(OpContext(ops, forged))
    assert pm.operation_visible(ops.db, OWNER, op)
    assert not pm.operation_visible(ops.db, OTHER, op)
    await finish(ops, op)
    assert pm.operation_visible(ops.db, ADMIN, ops.get(op["operation_id"]))


async def test_accepted_credential_authority_and_current_membership_are_distinct(ops):
    token = api_auth.issue(ops.db, OWNER.actor, ["manage", "observe"])
    principal = api_auth.authenticate(ops.db, token, "unused-admin-token")
    op, _ = submit(ops, "pm.workspace.create", who=principal, params={"name": "Accepted intent"})
    api_auth.revoke(ops.db, OWNER.actor)
    assert api_auth.authenticate(ops.db, token, "unused-admin-token") is None
    workspace = await finish(ops, op)  # accepted local intent does not need the old token again
    assert workspace["created_by"] == OWNER.actor
    with pytest.raises(OperationError, match="NOT_FOUND"):
        pm.require_member(ops.db, api_auth.Principal("local-admin", OWNER.scopes), workspace["workspace_id"], decide=True)


async def test_failure_before_receipt_rolls_back_workspace_membership_and_events(ops, monkeypatch):
    op, _ = submit(ops, "pm.workspace.create", params={"name": "Atomic create"})
    emit = ops.journal.api_event

    def interrupted(*args, **kwargs):
        emit(*args, **kwargs)
        raise OSError("simulated journal interruption")

    with monkeypatch.context() as patch:
        patch.setattr(ops.journal, "api_event", interrupted)
        with pytest.raises(OSError):
            await ops.actions[op["action"]].run(OpContext(ops, op))
    assert pm.list_workspaces(ops.db, ADMIN) == []
    assert ops.db.execute("SELECT COUNT(*) FROM pm_workspace_members").fetchone()[0] == 0
    assert ops.db.execute("SELECT COUNT(*) FROM pm_workspace_applied").fetchone()[0] == 0
    assert ops.db.execute("SELECT COUNT(*) FROM api_events WHERE kind='pm.workspace.created'").fetchone()[0] == 0
    await finish(ops, op)


async def test_bad_membership_inputs_do_not_change_workspace(ops):
    workspace = await create(ops)
    for actor, role in (("other\nowner", "owner"), ("other", "admin"), ("other", {})):
        with pytest.raises(OperationError, match="INVALID_PARAMS"):
            member_op(ops, workspace, actor, role)
    for version in (True, None, 0, "1"):
        with pytest.raises(OperationError, match="PRECONDITION_REQUIRED"):
            submit(ops, "pm.workspace.member.set", target={"workspace_id": workspace["workspace_id"]},
                   params={"actor": OTHER.actor, "role": "owner"}, pre={"expected_version": version})
    assert pm.get_workspace(ops.db, OWNER, workspace["workspace_id"])["version"] == 1
