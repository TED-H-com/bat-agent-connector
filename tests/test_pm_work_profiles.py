"""Tests for PM work profiles and readiness (v1.3 E01)."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_lifecycle, pm_work_profiles, pm_workspace
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.config import GitHubConfig, GitHubRepo, RepositorySyncConfig
from bat_agent_connector.operations import OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.operation_helpers import settle_operations

WS = "ws_" + "a" * 32
OWNER = Principal("alice", frozenset({"observe", "manage"}))
OTHER = Principal("bob", frozenset({"observe", "manage"}))
_UNSET = object()


def _fleet():
    repo = GitHubRepo(
        "acme/widget",
        sync=RepositorySyncConfig("https://github.com/acme/widget.git", (("h1", "bat-ws"),)),
    )
    return SimpleNamespace(config=SimpleNamespace(github=GitHubConfig(repos={"acme/widget": repo})))


def _document(**overrides):
    document = {
        "version": 1,
        "binding": {"repository": "Acme/Widget", "host": "h1", "workspace_id": "bat-ws"},
        "agent": "claude",
        "delivery_policy_ref": {"goal": "pull_request"},
    }
    document.update(overrides)
    return document


@pytest.fixture
def ops(tmp_path):
    journal = Journal(tmp_path / "tasks.sqlite3")
    service = OperationService(journal)
    pm_workspace.install(service)
    pm_work_profiles.install(service)
    service.context["fleet"] = _fleet()
    now = time.time()
    server = pm_workspace.server_id(service.db)
    with journal.tx():
        service.db.execute(
            "INSERT INTO pm_workspaces VALUES(?,?,?,?,1,?,?,?,?)",
            (WS, server, "Profiles", '["other/repo"]', OWNER.actor, now, now, "op_ws_profile"),
        )
        for who, role in ((OWNER, "owner"), (OTHER, "owner")):
            service.db.execute(
                "INSERT INTO pm_workspace_members VALUES(?,?,?,?,?,?)",
                (WS, who.actor, role, pm_workspace.controller_for(service.db, who), now, now),
            )
        pm_lifecycle.initialize(service.db, WS, now)
    yield service
    journal.close()


def _save(ops, document=None, *, who=OWNER, key=None, workspace_id=WS, profile_id=_UNSET,
          expected_revision=_UNSET, withdrawn=_UNSET, make_default=_UNSET, expected_default_sequence=_UNSET):
    params = {"document": _document() if document is None else document}
    if profile_id is not _UNSET:
        params["profile_id"] = profile_id
    if expected_revision is not _UNSET:
        params["expected_revision"] = expected_revision
    if withdrawn is not _UNSET:
        params["withdrawn"] = withdrawn
    if make_default is not _UNSET:
        params["make_default"] = make_default
    if expected_default_sequence is not _UNSET:
        params["expected_default_sequence"] = expected_default_sequence
    return ops.create(
        who, action=pm_work_profiles.ACTION, target={"workspace_id": workspace_id}, params=params,
        preconditions={}, idempotency_key=key or uuid.uuid4().hex,
    )


async def _finish(ops, operation):
    await settle_operations(ops)
    result = ops.get(operation["operation_id"])
    assert result["status"] == "succeeded", result
    return result


def _counts(ops):
    profiles = ops.db.execute("SELECT COUNT(*) FROM work_profiles").fetchone()[0]
    defaults = ops.db.execute("SELECT COUNT(*) FROM work_profile_defaults").fetchone()[0]
    return profiles, defaults


def _revisions(ops, profile_id):
    return ops.db.execute(
        "SELECT COUNT(*) FROM work_profiles WHERE profile_id=?", (profile_id,)
    ).fetchone()[0]


def test_schema_is_idempotent_and_allocates_no_user_version(tmp_path):
    journal = Journal(tmp_path / "tasks.sqlite3")
    before = journal.db.execute("PRAGMA user_version").fetchone()[0]
    pm_work_profiles.schema(journal)
    pm_work_profiles.schema(journal)
    assert journal.db.execute("PRAGMA user_version").fetchone()[0] == before
    names = {
        row[0]
        for row in journal.db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")
    }
    assert {"work_profiles", "work_profile_defaults", "operations_continue_succeeded"} <= names
    index_sql = journal.db.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'operations_continue_succeeded'"
    ).fetchone()[0]
    assert "WHERE action = 'repository.continue' AND status = 'succeeded'" in index_sql

    now = time.time()
    profile_id = "wpf_" + "a" * 32
    workspace_id = "ws_" + "b" * 32
    with journal.tx():
        journal.db.execute(
            """INSERT INTO work_profiles(
                profile_id, workspace_id, revision, withdrawn, document, digest, operation_id, created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (profile_id, workspace_id, 1, 0, "{}", "d" * 64, "op_" + "c" * 32, now),
        )
        journal.db.execute(
            """INSERT INTO work_profile_defaults(workspace_id, profile_id, operation_id, created_at)
               VALUES(?,?,?,?)""",
            (workspace_id, profile_id, "op_" + "e" * 32, now),
        )
    pm_work_profiles.schema(journal)
    with pytest.raises(sqlite3.IntegrityError, match="work profile history is immutable"):
        journal.db.execute("UPDATE work_profiles SET withdrawn = 1 WHERE profile_id = ?", (profile_id,))
    with pytest.raises(sqlite3.IntegrityError, match="work profile history is immutable"):
        journal.db.execute("DELETE FROM work_profiles WHERE profile_id = ?", (profile_id,))
    with pytest.raises(sqlite3.IntegrityError, match="work profile default history is immutable"):
        journal.db.execute(
            "UPDATE work_profile_defaults SET profile_id = NULL WHERE workspace_id = ?", (workspace_id,)
        )
    with pytest.raises(sqlite3.IntegrityError, match="work profile default history is immutable"):
        journal.db.execute("DELETE FROM work_profile_defaults WHERE workspace_id = ?", (workspace_id,))


async def test_save_profile_appends_revision_and_conflict_keeps_original(ops):
    created, inserted = _save(ops, key="create")
    assert inserted is True
    first = await _finish(ops, created)
    assert first["result"]["revision"] == 1
    assert first["result"]["profile_id"].startswith("wpf_") and len(first["result"]["profile_id"]) == 36
    assert _counts(ops) == (1, 0)

    explicit_null, _ = _save(ops, _document(model=None), key="null-model")
    null_model = await _finish(ops, explicit_null)
    assert null_model["result"]["digest"] == first["result"]["digest"]
    assert null_model["result"]["profile_id"] != first["result"]["profile_id"]

    profile_id = first["result"]["profile_id"]
    updated, _ = _save(ops, profile_id=profile_id, expected_revision=1, key="update")
    second = await _finish(ops, updated)
    assert second["result"]["revision"] == 2
    assert _revisions(ops, profile_id) == 2

    before = _counts(ops)
    with pytest.raises(OperationError) as stale:
        _save(ops, profile_id=profile_id, expected_revision=1, key="stale")
    assert stale.value.code == "PM_WORK_PROFILE_REVISION_CONFLICT" and stale.value.status == 409
    assert _counts(ops) == before

    replay, inserted = _save(ops, profile_id=profile_id, expected_revision=1, key="update")
    assert inserted is False and replay["operation_id"] == updated["operation_id"]
    assert _revisions(ops, profile_id) == 2
    again = await ops.actions[pm_work_profiles.ACTION].run(OpContext(ops, ops.get(updated["operation_id"])))
    assert again["revision"] == 2 and _revisions(ops, profile_id) == 2

    with pytest.raises(OperationError) as conflict:
        _save(ops, _document(model="other"), profile_id=profile_id, expected_revision=1, key="update")
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT" and conflict.value.status == 409
    assert _revisions(ops, profile_id) == 2

    left, _ = _save(ops, profile_id=profile_id, expected_revision=2, key="race-a")
    right, _ = _save(ops, _document(agent="codex"), profile_id=profile_id, expected_revision=2, key="race-b")
    await settle_operations(ops)
    outcomes = [ops.get(item["operation_id"]) for item in (left, right)]
    assert sorted(item["status"] for item in outcomes) == ["failed", "succeeded"]
    failed = [item for item in outcomes if item["status"] == "failed"]
    assert [item["error_code"] for item in failed] == ["PM_WORK_PROFILE_REVISION_CONFLICT"]
    assert _revisions(ops, profile_id) == 3
    assert ops.db.execute(
        "SELECT 1 FROM work_profiles WHERE operation_id=?", (failed[0]["operation_id"],)
    ).fetchone() is None


async def test_withdraw_removes_list_get_and_readiness_together(ops):
    first = (await _finish(ops, _save(ops, key="first")[0]))["result"]
    second = (await _finish(ops, _save(ops, _document(agent="codex"), key="second")[0]))["result"]
    listed = pm_work_profiles.list_profiles(ops.db, OWNER, WS)
    assert listed["count"] == 2
    assert {item["profile_id"] for item in listed["profiles"]} == {first["profile_id"], second["profile_id"]}
    assert all(item["repository"] == "Acme/Widget" and item["workspace_id"] == "bat-ws" for item in listed["profiles"])

    withdrawn, _ = _save(ops, profile_id=first["profile_id"], expected_revision=1, withdrawn=True, key="withdraw")
    result = await _finish(ops, withdrawn)
    assert result["result"]["withdrawn"] is True
    listed = pm_work_profiles.list_profiles(ops.db, OWNER, WS)
    assert listed["count"] == 1
    assert first["profile_id"] not in {item["profile_id"] for item in listed["profiles"]}
    assert second["profile_id"] in {item["profile_id"] for item in listed["profiles"]}
    for _ in range(2):
        with pytest.raises(OperationError) as missing:
            pm_work_profiles.get_profile(ops.db, OWNER, WS, first["profile_id"])
        assert missing.value.code == "PM_WORK_PROFILE_WITHDRAWN" and missing.value.status == 404
        assert first["digest"] not in str(missing.value)
    with pytest.raises(OperationError) as readiness_error:
        pm_work_profiles.readiness(ops, OWNER, WS, first["profile_id"])
    assert readiness_error.value.code == "PM_WORK_PROFILE_WITHDRAWN" and readiness_error.value.status == 404
    assert first["digest"] not in str(readiness_error.value)
    stored = ops.get(withdrawn["operation_id"])
    assert stored["status"] == "succeeded"
    row = ops.db.execute(
        "SELECT withdrawn FROM work_profiles WHERE operation_id=?", (withdrawn["operation_id"],)
    ).fetchone()
    assert row["withdrawn"] == 1
    assert _counts(ops)[1] == 0


async def test_save_refuses_forbidden_key_oversize_and_budget(ops):
    forbidden = _document()
    forbidden["binding"] = {**forbidden["binding"], "token": "sekret-value"}
    with pytest.raises(OperationError) as refused:
        _save(ops, forbidden, key="forbidden")
    assert refused.value.code == "INVALID_PARAMS" and refused.value.status == 422
    assert "sekret-value" not in str(refused.value)

    with pytest.raises(OperationError) as oversized:
        _save(ops, _document(model="m" * 20000), key="oversize")
    assert oversized.value.code == "PM_WORK_PROFILE_TOO_LARGE" and oversized.value.status == 413

    with pytest.raises(OperationError) as budget:
        _save(ops, _document(budget_policy_ref={"cap": 1}), key="budget")
    assert budget.value.code == "PM_WORK_PROFILE_BUDGET_UNSUPPORTED" and budget.value.status == 422
    assert _counts(ops) == (0, 0)
    assert ops.db.execute(
        "SELECT COUNT(*) FROM operations WHERE action=?", (pm_work_profiles.ACTION,)
    ).fetchone()[0] == 0

    saved = await _finish(ops, _save(ops, _document(model="token-haiku"), key="token-word")[0])
    assert saved["result"]["revision"] == 1
    assert _counts(ops) == (1, 0)


async def test_save_checks_binding_not_workspace_repository_list(ops):
    repositories = ops.db.execute(
        "SELECT repositories FROM pm_workspaces WHERE workspace_id=?", (WS,)
    ).fetchone()[0]
    assert "acme/widget" not in repositories.lower()
    saved = await _finish(ops, _save(ops, key="bound")[0])
    stored = json.loads(ops.db.execute(
        "SELECT document FROM work_profiles WHERE profile_id=?", (saved["result"]["profile_id"],)
    ).fetchone()[0])
    assert stored["binding"]["repository"] == "Acme/Widget"
    before = _counts(ops)
    missing = _document()
    missing["binding"] = {**missing["binding"], "host": "other-host"}
    with pytest.raises(OperationError) as unbound:
        _save(ops, missing, key="unbound")
    assert unbound.value.code == "REPOSITORY_NOT_BOUND" and unbound.value.status == 403
    assert unbound.value.message == "no explicit repository/host/workspace binding"
    assert _counts(ops) == before


async def test_default_change_is_one_transaction(ops):
    primary = (await _finish(ops, _save(ops, key="p")[0]))["result"]
    other = (await _finish(ops, _save(ops, _document(agent="codex"), key="q")[0]))["result"]
    untouched = (await _finish(ops, _save(ops, _document(model="kept"), key="r")[0]))["result"]
    assert _counts(ops) == (3, 0)

    ignored = await _finish(ops, _save(
        ops, profile_id=primary["profile_id"], expected_revision=1, make_default=False,
        expected_default_sequence="stale-ignored", key="ignore-seq",
    )[0])
    assert ignored["result"]["revision"] == 2 and "default_sequence" not in ignored["result"]
    assert _counts(ops) == (4, 0)

    before = _counts(ops)
    with pytest.raises(OperationError) as missing_sequence:
        _save(ops, profile_id=primary["profile_id"], expected_revision=2, make_default=True, key="missing-seq")
    assert missing_sequence.value.code == "INVALID_PARAMS"
    with pytest.raises(OperationError) as both:
        _save(
            ops, profile_id=primary["profile_id"], expected_revision=2, withdrawn=True, make_default=True,
            expected_default_sequence=None, key="both",
        )
    assert both.value.code == "INVALID_PARAMS"
    assert _counts(ops) == before

    left, _ = _save(
        ops, profile_id=primary["profile_id"], expected_revision=2, make_default=True,
        expected_default_sequence=None, key="race-p", who=OWNER,
    )
    right, _ = _save(
        ops, _document(agent="codex"), profile_id=other["profile_id"], expected_revision=1,
        make_default=True, expected_default_sequence=None, key="race-q", who=OTHER,
    )
    await settle_operations(ops)
    outcomes = [ops.get(item["operation_id"]) for item in (left, right)]
    succeeded = [item for item in outcomes if item["status"] == "succeeded"]
    failed = [item for item in outcomes if item["status"] == "failed"]
    assert len(succeeded) == 1 and len(failed) == 1
    assert failed[0]["error_code"] == "PM_WORK_PROFILE_DEFAULT_CHANGED"
    winner = succeeded[0]["result"]
    assert winner["default_sequence"] == 1
    assert _revisions(ops, winner["profile_id"]) == (3 if winner["profile_id"] == primary["profile_id"] else 2)
    loser_id = other["profile_id"] if winner["profile_id"] == primary["profile_id"] else primary["profile_id"]
    assert _revisions(ops, loser_id) == (1 if loser_id == other["profile_id"] else 2)
    assert _revisions(ops, untouched["profile_id"]) == 1
    assert ops.db.execute(
        "SELECT 1 FROM work_profiles WHERE operation_id=?", (failed[0]["operation_id"],)
    ).fetchone() is None
    assert ops.db.execute(
        "SELECT 1 FROM work_profile_defaults WHERE operation_id=?", (failed[0]["operation_id"],)
    ).fetchone() is None
    assert ops.db.execute("SELECT profile_id FROM work_profile_defaults").fetchone()["profile_id"] == winner["profile_id"]
    listed = pm_work_profiles.list_profiles(ops.db, OWNER, WS)
    assert listed["profiles"][0]["profile_id"] == winner["profile_id"] and listed["profiles"][0]["is_default"] is True
    assert listed["count"] == 3

    document = _document() if winner["profile_id"] == primary["profile_id"] else _document(agent="codex")
    cleared = await _finish(ops, _save(
        ops, document, profile_id=winner["profile_id"], expected_revision=winner["revision"],
        make_default=False, expected_default_sequence=winner["default_sequence"], key="clear",
    )[0])
    assert cleared["result"]["default_sequence"] == 2
    assert ops.db.execute(
        "SELECT profile_id FROM work_profile_defaults WHERE sequence=2"
    ).fetchone()["profile_id"] is None
    assert _revisions(ops, loser_id) == (1 if loser_id == other["profile_id"] else 2)
    assert _revisions(ops, untouched["profile_id"]) == 1
