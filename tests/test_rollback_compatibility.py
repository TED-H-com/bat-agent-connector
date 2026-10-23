"""Rollback preview observes real durable records; it never activates a package."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from bat_agent_connector import api_auth, updater
from bat_agent_connector import platform_files as files
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import ActionDef, AmbiguousOutcome, OperationService
from bat_agent_connector.task_journal import LATEST_DATA_STEP, Journal
from bat_agent_connector.update_install import digest_files, package_files
from tests import test_connector_installer as installer_tests
from tests.operation_helpers import settle_operations, stop_workers
from tests.test_connector_updates import make_package, release

installation = installer_tests.installation

pytestmark = pytest.mark.skipif(not os.sys.platform.startswith("linux"), reason="Linux Connector updater")


@pytest.fixture(autouse=True)
def disk_snapshots(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))


def tree_snapshot(root):
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        value = os.readlink(path) if path.is_symlink() else (path.read_bytes() if path.is_file() else None)
        result[str(path.relative_to(root))] = (info.st_mode, info.st_mtime_ns, value)
    return result


def declare_previous(service, state, **parameters):
    # Non-release cases explicitly parameterize the real declaration producer;
    # no test constructs its own declaration shape.
    from bat_agent_connector.rollback_compatibility import declaration
    record = state["versions"][state["previous"]]
    package = Path(record["proof"]["package"])
    files.atomic_write(package / "compatibility.json", json.dumps(declaration(**parameters)).encode())
    record["proof"]["files"] = digest_files(package_files(package))
    service.save(state)
    return package / "compatibility.json"


def preview_unchanged(service, journal):
    before = tree_snapshot(service.home)
    try:
        return service.rollback_preview(journal=journal.path)
    finally:
        assert tree_snapshot(service.home) == before


@pytest.fixture
def deployment(tmp_path):
    home = tmp_path / "preview-home"
    previous, current = release("a" * 40, "0.2.12"), release("b" * 40, "0.2.13")
    service = updater.Updater(home)
    records = {}
    for candidate in (previous, current):
        root = home / "releases" / candidate["commit"]
        proof = make_package(root, candidate)
        records[candidate["commit"]] = {"root": str(root), "release": candidate, "proof": proof}
    # The state shape is emitted by Updater.enroll/apply; save and proof hashing
    # are the real producers. No previous package is imported or executed.
    state = {"format": 1, "epoch": 1, "channel": "preview", "enabled": True,
             "current": current["commit"], "previous": previous["commit"], "versions": records}
    service.save(state)
    (service.root / "current").symlink_to(records[current["commit"]]["root"])
    for path, target in service.links().items():
        files.ensure_private_directory(path.parent)
        path.symlink_to(target)
    return service, state


@pytest.fixture
async def produced(deployment):
    service, state = deployment
    path = service.home / "journal" / "tasks.sqlite3"
    journal = Journal(path)
    sent = []

    async def effect(ctx):
        async def receipt():
            return {"external_ref": "fixture-original-receipt"}

        acknowledged = await ctx.step("receipt", receipt, request={"key": ctx.op["idem_key"]})
        if ctx.params.get("uncertain"):
            async def lost():
                sent.append(ctx.op["idem_key"])
                raise AmbiguousOutcome("fixture reply lost after send")
            return await ctx.step("send", lost, request={"key": ctx.op["idem_key"]})
        return acknowledged

    ops = OperationService(journal, actions=[ActionDef("fixture.effect", "operate", "Fixture effect", effect)])
    principal = Principal("fixture-operator", frozenset({"observe", "operate"}))
    try:
        done, _ = ops.create(principal, action="fixture.effect", idempotency_key="completed-original-key")
        await settle_operations(ops)
        assert ops.get(done["operation_id"])["status"] == "succeeded"
        assert journal.db.execute("PRAGMA user_version").fetchone()[0] == LATEST_DATA_STEP
        yield service, state, journal, ops, principal, sent
    finally:
        await stop_workers(ops)
        journal.close()


async def test_old_build_without_declaration_is_unknown_and_preview_changes_nothing(produced, monkeypatch):
    service, _, journal, ops, principal, sent = produced
    pending, _ = ops.create(principal, action="fixture.effect", params={"uncertain": True},
                            idempotency_key="uncertain-original-key")
    await settle_operations(ops)
    original = ops.get(pending["operation_id"])
    assert original["status"] == "uncertain"
    receipt = journal.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='receipt'",
                                 (pending["operation_id"],)).fetchone()[0]
    assert json.loads(receipt) == {"external_ref": "fixture-original-receipt"}
    before = tree_snapshot(service.home)

    def forbidden(*args, **kwargs):
        pytest.fail("Preview attempted to run, install, send or change managed state")

    for name in ("install", "inspect", "command", "find_release", "save", "reconcile", "rollback", "locked"):
        monkeypatch.setattr(service, name, forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(os, "kill", forbidden)
    try:
        preview = service.rollback_preview(journal=journal.path)
        assert preview["verdict"] == "unknown"
        assert preview["facts"]["journal"]["reason"] == "PREVIOUS_BUILD_SUPPORT_UNDECLARED"
        assert preview["facts"]["in_flight"]["value"]["counts"] == [
            {"kind": "fixture.effect", "status": "uncertain", "count": 1}]
        assert ops.get(pending["operation_id"]) == original
        assert sent == ["uncertain-original-key"]
    finally:
        assert tree_snapshot(service.home) == before


@pytest.mark.parametrize("state_override", ["BATC_STATE_DIR", "XDG_STATE_HOME"])
def test_preview_default_observes_the_daemon_default_and_reports_its_source(
        deployment, tmp_path, monkeypatch, state_override):
    from bat_agent_connector.config import Config
    from bat_agent_connector.task_daemon import TaskDaemon

    service, state = deployment
    declare_previous(service, state)
    monkeypatch.delenv("BATC_STATE_DIR", raising=False)
    monkeypatch.setenv(state_override, str(tmp_path / "configured-state"))
    directory = tmp_path / "configured-state"
    if state_override == "XDG_STATE_HOME":
        directory /= "bat-agent-connector"
    # Real daemon construction selects its path before opening any service or BAT.
    daemon = TaskDaemon(Config(hosts={}))
    assert daemon._db_path == directory / "tasks.sqlite3"
    with closing(Journal(daemon._db_path)) as journal:
        before = tree_snapshot(directory), tree_snapshot(service.home)
        preview = service.rollback_preview()
        assert preview["journal"] == {"path": str(journal.path.absolute()), "source": "default"}
        assert preview["facts"]["journal"]["value"]["recorded_data_step"] == LATEST_DATA_STEP
        assert (tree_snapshot(directory), tree_snapshot(service.home)) == before


def test_cli_exposes_an_explicit_read_only_preview(deployment, tmp_path, monkeypatch, capsys):
    service, _ = deployment
    path = tmp_path / "journal.db"
    Journal(path).close()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(updater, "Updater", lambda: service)
    before = tree_snapshot(service.home)
    assert updater.main(["rollback", "--preview", "--journal", path.name]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["verdict"] == "unknown"
    assert preview["journal"] == {"path": str(path), "source": "explicit"}
    assert preview["facts"]["journal"]["value"]["recorded_data_step"] == LATEST_DATA_STEP
    assert tree_snapshot(service.home) == before


@pytest.mark.parametrize("extra_steps", [0, 1])
async def test_declared_current_step_and_fence_with_no_in_flight_work_is_compatible(produced, extra_steps):
    service, state, journal, *_ = produced
    declare_previous(service, state, journal_max=LATEST_DATA_STEP + extra_steps)
    preview = preview_unchanged(service, journal)
    assert preview["verdict"] == "compatible"
    assert preview["facts"]["journal"]["value"] == {
        "recorded_data_step": LATEST_DATA_STEP, "declared_max": LATEST_DATA_STEP + extra_steps}
    assert preview["facts"]["in_flight"]["value"]["counts"] == []
    assert preview["facts"]["in_flight"]["reason"] == "NO_IN_FLIGHT_WORK"
    for fact in preview["facts"].values():
        assert fact["compatibility"] == "compatible"
        assert fact["reason"] and fact["observed_at"]
        assert fact["build"]["commit"] == state["previous"]


async def test_nothing_in_flight_needs_no_previous_support_declaration(produced):
    service, _, journal, *_ = produced
    preview = preview_unchanged(service, journal)
    assert preview["verdict"] == "unknown"
    assert preview["facts"]["in_flight"]["compatibility"] == "compatible"
    assert preview["facts"]["in_flight"]["reason"] == "NO_IN_FLIGHT_WORK"


@pytest.mark.parametrize("parameters,fact,reason", [
    ({"journal_max": LATEST_DATA_STEP - 1}, "journal", "JOURNAL_DATA_STEP_UNSUPPORTED"),
    ({"newer_journal_fence": False}, "newer_journal_fence", "NEWER_JOURNAL_FENCE_ABSENT"),
    ({"enrollment_formats": ()}, "enrollment", "ENROLLMENT_FORMAT_UNSUPPORTED"),
    ({"enrollment_epochs": ()}, "enrollment", "ENROLLMENT_FORMAT_UNSUPPORTED"),
])
async def test_known_incompatibility_wins_over_other_unknowns(produced, parameters, fact, reason):
    service, state, journal, *_ = produced
    declare_previous(service, state, **parameters)
    api_auth.issue(journal.db, "fixture-reader", ["observe"])
    preview = preview_unchanged(service, journal)
    assert preview["verdict"] == "incompatible"
    assert preview["facts"][fact]["reason"] == reason
    assert preview["facts"]["credentials"]["compatibility"] == "unknown"


@pytest.mark.parametrize("change", ["malformed", "deeply_nested", "unreadable", "edited_after_proof"])
async def test_unusable_declaration_cannot_prove_compatibility(produced, monkeypatch, change):
    service, state, journal, *_ = produced
    path = declare_previous(service, state)
    if change != "unreadable":
        files.atomic_write(path, b"[" * 20000 + b"0" + b"]" * 20000 if change == "deeply_nested" else b"not JSON")
        if change in {"malformed", "deeply_nested"}:
            record = state["versions"][state["previous"]]
            record["proof"]["files"] = digest_files(package_files(path.parent))
            service.save(state)
    else:
        from bat_agent_connector import rollback_compatibility
        def denied(candidate):
            raise PermissionError("fixture denial")
        monkeypatch.setattr(rollback_compatibility, "_declaration_bytes", denied)
    preview = preview_unchanged(service, journal)
    assert preview["verdict"] == "unknown"
    assert preview["facts"]["journal"]["compatibility"] == "unknown"


async def test_in_flight_requires_that_builds_explicit_operation_support(produced):
    service, state, journal, ops, principal, sent = produced
    declare_previous(service, state)
    pending, _ = ops.create(principal, action="fixture.effect", params={"uncertain": True},
                            idempotency_key="uncertain-original-key")
    await settle_operations(ops)
    original = ops.get(pending["operation_id"])
    preview = preview_unchanged(service, journal)
    assert preview["verdict"] == "unknown"
    fact = preview["facts"]["in_flight"]
    assert fact["reason"] == "IN_FLIGHT_SUPPORT_UNDECLARED"
    assert fact["value"]["counts"] == [{"kind": "fixture.effect", "status": "uncertain", "count": 1}]
    assert {s["status"] for s in fact["value"]["steps"]} == {"succeeded", "uncertain"}
    assert ops.get(pending["operation_id"]) == original
    assert sent == ["uncertain-original-key"]


async def test_present_credentials_have_no_format_version_and_remain_unknown(produced):
    service, state, journal, *_ = produced
    declare_previous(service, state)
    token = api_auth.issue(journal.db, "fixture-reader", ["observe"])
    preview = preview_unchanged(service, journal)
    assert preview["verdict"] == "unknown"
    fact = preview["facts"]["credentials"]
    assert fact["reason"] == "CREDENTIAL_FORMAT_UNVERSIONED"
    assert fact["value"]["format"] is None
    assert fact["value"]["records"]["api_principals"] == 1
    assert token not in json.dumps(preview)


async def test_real_unversioned_deployment_receipt_remains_unknown(produced, installation):
    service, state, journal, *_ = produced
    declare_previous(service, state)
    *_, finalize = installation
    receipt = Path(finalize()["receipt"]).read_bytes()
    current = Path(state["versions"][state["current"]]["root"])
    files.atomic_write(current / "rollout-receipt.json", receipt)
    preview = preview_unchanged(service, journal)
    assert preview["verdict"] == "unknown"
    assert preview["facts"]["enrollment"]["reason"] == "ENROLLMENT_RECEIPT_UNVERSIONED"


def test_real_enrollment_with_current_credentials_and_receipt_stays_unknown(installation):
    home, installed_root, _, first, inspect, finalize = installation
    receipt = Path(finalize()["receipt"])
    proofs = {installed_root: inspect(installed_root, first, home=home)}

    def install(root, candidate, *, home):
        proofs[root] = make_package(root, candidate)
        return proofs[root]

    # Real enrollment/update/state/link/receipt producers; only the package
    # installer/inspection boundary uses the existing synthetic package fixture.
    service = updater.Updater(home, find_release=lambda channel, observation=None: first, install=install,
                              inspect=lambda root, candidate, **kwargs: proofs[root])
    enrolled = service.enroll(receipt, "preview")
    assert enrolled["installation_matches_state"] and not enrolled["pending"]
    second = release("b" * 40, "0.2.8")
    service.find_release = lambda channel, observation=None: second
    assert service.apply()["installed"] == second
    state = service.read()
    assert state["previous"] == first["commit"]
    declare_previous(service, state)

    with closing(Journal(home / "journal" / "tasks.sqlite3")) as journal:
        token = api_auth.issue(journal.db, "enrolled-reader", ["observe"])
        preview = preview_unchanged(service, journal)
        facts = preview["facts"]
        assert facts["journal"]["compatibility"] == "compatible"
        assert facts["newer_journal_fence"]["compatibility"] == "compatible"
        assert facts["in_flight"]["reason"] == "NO_IN_FLIGHT_WORK"
        assert preview["verdict"] == "unknown"
        assert facts["credentials"]["reason"] == "CREDENTIAL_FORMAT_UNVERSIONED"
        assert facts["enrollment"]["reason"] == "ENROLLMENT_RECEIPT_UNVERSIONED"
        assert facts["credentials"]["value"]["records"]["api_principals"] == 1
        assert facts["enrollment"]["value"]["unversioned_deployment_receipts"] == 1
        assert token not in json.dumps(preview)


def test_shipped_declaration_matches_current_producer():
    from bat_agent_connector import rollback_compatibility
    path = Path(rollback_compatibility.__file__).with_name("compatibility.json")
    assert json.loads(path.read_text()) == rollback_compatibility.declaration()


def test_declaration_has_support_facts_without_a_duplicate_product_version():
    from bat_agent_connector import rollback_compatibility
    from bat_agent_connector.update_release import EPOCH

    path = Path(rollback_compatibility.__file__).with_name("compatibility.json")
    for doc in (json.loads(path.read_text()), rollback_compatibility.declaration()):
        assert "version" not in doc
        assert doc["format"] == 1
        assert doc["journal_max"] == LATEST_DATA_STEP
        assert doc["newer_journal_fence"] is True
        assert doc["enrollment_epochs"] == [EPOCH]
        assert doc["enrollment_formats"] == [1]


async def test_missing_journal_is_unknown_and_never_created(deployment):
    service, state = deployment
    declare_previous(service, state)
    journal = service.home / "absent" / "tasks.sqlite3"
    before = tree_snapshot(service.home)
    preview = service.rollback_preview(journal=journal)
    assert preview["verdict"] == "unknown"
    assert preview["facts"]["journal"]["reason"] == "JOURNAL_UNREADABLE"
    assert preview["journal"] == {"path": str(journal), "source": "explicit"}
    assert tree_snapshot(service.home) == before


async def test_journal_is_opened_with_sqlite_mode_ro(produced, monkeypatch):
    service, state, journal, *_ = produced
    declare_previous(service, state)
    connect = sqlite3.connect
    seen = []
    def checked(database, *args, **kwargs):
        assert "mode=ro" in str(database) and kwargs.get("uri") is True
        seen.append(database)
        return connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", checked)
    preview_unchanged(service, journal)
    assert seen


@pytest.mark.parametrize("parameters", [
    {"journal_max": True}, {"newer_journal_fence": "true"},
    {"enrollment_formats": (True,)}, {"operation_support": ({"kind": "fixture.effect"},)},
])
async def test_structurally_invalid_producer_parameters_never_become_safe(produced, parameters):
    service, state, journal, *_ = produced
    declare_previous(service, state, **parameters)
    preview = preview_unchanged(service, journal)
    assert preview["verdict"] == "unknown"
    assert preview["facts"]["journal"]["reason"] == "PREVIOUS_DECLARATION_UNREADABLE_OR_INVALID"


async def test_explicit_future_operation_declaration_is_checked_for_each_step(produced):
    service, state, journal, ops, principal, sent = produced
    pending, _ = ops.create(principal, action="fixture.effect", params={"uncertain": True},
                            idempotency_key="uncertain-original-key")
    await settle_operations(ops)
    # Explicit hypothetical support; no published build is claimed to provide it.
    parameters = [{"kind": "fixture.effect", "statuses": ["uncertain"], "steps": [
        {"name": "receipt", "statuses": ["succeeded"]}, {"name": "send", "statuses": ["uncertain"]}]}]
    declare_previous(service, state, operation_support=parameters)
    original = ops.get(pending["operation_id"])
    preview = preview_unchanged(service, journal)
    assert preview["verdict"] == "compatible"
    assert preview["facts"]["in_flight"]["reason"] == "IN_FLIGHT_SUPPORT_DECLARED"
    parameters[0]["steps"].pop()
    declare_previous(service, state, operation_support=parameters)
    assert preview_unchanged(service, journal)["verdict"] == "unknown"
    assert ops.get(pending["operation_id"]) == original
    assert sent == ["uncertain-original-key"]


async def test_a_real_commit_during_snapshot_is_unknown_and_temporary_copy_is_removed(produced, monkeypatch, tmp_path):
    from bat_agent_connector import rollback_compatibility
    service, state, journal, *_ = produced
    declare_previous(service, state)
    signatures = rollback_compatibility._signatures
    reads = 0
    def changed(paths):
        nonlocal reads
        reads += 1
        if reads == 2:
            api_auth.issue(journal.db, "concurrent-writer", ["observe"])
        return signatures(paths)
    monkeypatch.setattr(rollback_compatibility, "_signatures", changed)
    preview = service.rollback_preview(journal=journal.path)
    assert preview["verdict"] == "unknown"
    assert preview["facts"]["journal"]["reason"] == "JOURNAL_CHANGED_DURING_PREVIEW"
    assert not list(tmp_path.glob("batc-rollback-preview-*"))


def test_plain_rollback_still_activates_after_an_unknown_preview(deployment, tmp_path):
    service, state = deployment
    journal = Journal(tmp_path / "journal.db")
    journal.close()
    assert service.rollback_preview(journal=journal.path)["verdict"] == "unknown"
    # Existing synthetic package fixture substitutes inspection, as updater tests do.
    service.inspect = lambda root, release, **kwargs: state["versions"][release["commit"]]["proof"]
    result = service.rollback()
    assert result["installed"]["commit"] == state["previous"]
    assert not result["enabled"]


def test_journal_argument_cannot_silently_invoke_plain_rollback(deployment, monkeypatch):
    service, _ = deployment
    monkeypatch.setattr(updater, "Updater", lambda: service)
    before = tree_snapshot(service.home)
    with pytest.raises(SystemExit) as refused:
        updater.main(["rollback", "--journal", "/fixture/journal.db"])
    assert refused.value.code == 2
    assert tree_snapshot(service.home) == before
