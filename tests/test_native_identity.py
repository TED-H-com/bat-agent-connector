"""Identity admission delegates ownership to the real controller context."""
from dataclasses import replace

import pytest

from bat_agent_connector import controller_authority as authority
from bat_agent_connector import native_identity, native_process
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.errors import ResourceReadOnly
from tests.test_native_journal import created, journal, launcher, native, prepare, snapshot  # noqa: F401


def check(journal, context, observed, **kwargs):  # noqa: F811
    return native_identity.require_session(journal.db, context.key,
        expected_incarnation=kwargs.get("incarnation", "c" * 32),
        process=kwargs.get("process", observed.process))


def test_gate_is_read_only_and_checks_each_call(journal):  # noqa: F811
    intent, observed, context, row = created(journal)
    before, count = snapshot(journal.db), journal.db.total_changes
    with authority.use(intent.controller_authority, journal):
        assert check(journal, context, observed) == row
        with pytest.raises(ResourceReadOnly, match="CONTROLLER_INCARNATION_CHANGED"):
            check(journal, context, observed, incarnation="d" * 32)
    foreign = authority.binding(journal.db, Principal("foreign-admin", frozenset(), admin=True))
    with authority.use(foreign, journal), pytest.raises(ResourceReadOnly, match="FOREIGN_CONTROLLER"):
        check(journal, context, observed)
    with authority.use(None), pytest.raises(ResourceReadOnly, match="CONTROLLER_AUTHORITY_REQUIRED"):
        check(journal, context, observed)
    assert journal.db.total_changes == count and snapshot(journal.db) == before


def test_session_existence_and_first_input_fence_are_not_creation(journal):  # noqa: F811
    intent, observed = launcher(journal, mode="process_init")
    intent = replace(intent, context=replace(intent.context, key=replace(intent.context.key, native_session_id=observed.native_session_id)))
    prepare(journal, intent, create=False)
    native.record_turn(journal, intent.context, 0, "turn_sent", intent.nonce, {"input_digest": intent.input_digest})
    native.record_process(journal, intent.context, 2, "process_exited", intent.nonce, {"exit_code": 0})
    native.record_event(journal, intent.context, 0, {"status": "idle", "grant": "external-history"})
    admin = authority.binding(journal.db, Principal("fixture-admin", frozenset(), admin=True))
    for caller in (intent.controller_authority, admin):
        with authority.use(caller, journal), pytest.raises(native.NativeEvidenceError, match="NATIVE_CREATION_UNPROVEN"):
            check(journal, intent.context, observed)


@pytest.mark.parametrize("change", [{"adapter_instance_id": "other"}, {"incarnation": "f" * 32}])
def test_gate_rejects_another_adapter_or_session_incarnation(journal, change):  # noqa: F811
    intent, observed, context, _ = created(journal)
    with authority.use(intent.controller_authority, journal), pytest.raises(native.NativeEvidenceError, match="NATIVE_IDENTITY_MISMATCH"):
        check(journal, replace(context, key=replace(context.key, **change)), observed)


@pytest.mark.parametrize("process", [{"pid": 424242}, None, native.ProcessIdentity(424242, 26, "4" * 64)])
def test_gate_requires_full_matching_process_identity(journal, process):  # noqa: F811
    intent, observed, context, _ = created(journal)
    with authority.use(intent.controller_authority, journal), pytest.raises(native.NativeEvidenceError):
        check(journal, context, observed, process=process)


async def test_changed_membership_is_refused_by_shared_gate(journal):  # noqa: F811
    from bat_agent_connector import pm_workspace
    from bat_agent_connector.operations import OperationService
    from tests.test_pm_workspace import OTHER, OWNER, create, member

    ops = OperationService(journal)
    pm_workspace.install(ops)
    workspace = await create(ops)
    bound = authority.binding(journal.db, OWNER, pm_workspace_id=workspace["workspace_id"])
    _, observed, context, _ = created(journal, binding=bound)
    with authority.use(bound, journal):
        check(journal, context, observed)
        workspace = await member(ops, workspace, OTHER.actor, "owner")
        await member(ops, workspace, OWNER.actor, None)
        with pytest.raises(ResourceReadOnly, match="CONTROLLER_MEMBERSHIP_CHANGED"):
            check(journal, context, observed)


def test_process_type_accepts_the_shared_helper_shape():
    import os

    identity = native.ProcessIdentity(**native_process._process_identity(os.getpid()))
    assert native.process_identity(identity)["pid"] == os.getpid()
