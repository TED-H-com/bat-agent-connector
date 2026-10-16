"""Tests for PM work profiles and readiness (v1.3 E01)."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_lifecycle, pm_work_profiles, pm_work_start, pm_workspace
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.config import GitHubConfig, GitHubRepo, RepositorySyncConfig
from bat_agent_connector.operations import ActionDef, OpContext, OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.operation_helpers import settle_operations
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_work_start import pm as _pm_fixture
from tests.test_pm_work_start import ready as _ready_fixture
from tests.test_pm_work_start import start_ready as _start_ready_fixture
from tests.test_pm_work_start import world as _world_fixture

world = _world_fixture
pm = _pm_fixture
ready = _ready_fixture
start_ready = _start_ready_fixture

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


async def test_save_checks_binding_not_workspace_repository_list(ops, start_ready):
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

    started = start_ready
    sops = started["d"].ops
    sops.context["fleet"].config.github.repos["other/repo"] = GitHubRepo(
        "other/repo",
        sync=RepositorySyncConfig("https://example.invalid/other.git", (("h1", "ws-other"),)),
    )
    listed = sops.db.execute(
        "SELECT repositories FROM pm_workspaces WHERE workspace_id=?", (started["workspace_id"],)
    ).fetchone()[0]
    assert "other/repo" not in listed.lower()
    profile = await _save_start(started, _start_doc(
        binding={"repository": "Other/Repo", "host": "h1", "workspace_id": "ws-other"},
    ))
    project_repos = sops.db.execute(
        """SELECT repositories FROM projects WHERE project_id=(
               SELECT project_id FROM work_items WHERE work_item_id=?)""",
        (started["target"]["work_item_id"],),
    ).fetchone()[0]
    assert "other/repo" not in project_repos.lower()
    plain = await _view(started, {})
    children = _child_count(sops)
    starts = _start_count(sops)
    with pytest.raises(OperationError) as project_gate:
        _open_start(
            started, plain, profile,
            code_target={"repository": "Other/Repo", "host": "h1", "workspace_id": "ws-other"},
        )
    assert project_gate.value.code == "PROJECT_REPOSITORY_CHANGED"
    assert project_gate.value.status == 409
    assert _child_count(sops) == children
    assert _start_count(sops) == starts


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


async def _idle(_ctx):
    return {}


def _note(ops, name):
    if name not in ops.actions:
        ops.actions[name] = ActionDef(name, "start", "test double", _idle)


def _configure(ops, *, reason=None, available=True, token=True, orchestrate=True, roots=("/srv/repos",),
               reachable=_UNSET, register_continue=True, register_send=False):
    fleet = _fleet()
    host = SimpleNamespace(writes=True, orchestrate=orchestrate, managed_roots=roots)
    host.token_available = lambda: bool(token)
    fleet.config.hosts = {"h1": host}
    fleet.read_only = False

    def orchestrate_enabled(name):
        item = fleet.config.hosts[name]
        return (not fleet.read_only) and item.writes and item.orchestrate

    fleet.orchestrate_enabled = orchestrate_enabled
    invokes = {"n": 0}

    def client(_name):
        invokes["n"] += 1
        raise AssertionError("readiness called BAT")

    fleet.client = client
    ops.context["fleet"] = fleet
    ops.context["git_runner"] = SimpleNamespace(
        status=lambda _host: {"available": available, "reason": reason, "details": {}},
    )
    if register_continue:
        _note(ops, "repository.continue")
    if register_send:
        _note(ops, "session.send")
    if reachable is not _UNSET:
        ops.db.execute(
            """INSERT INTO hosts_observed(host, reachable, last_attempt_at, last_success_at, consecutive_failures)
               VALUES(?,?,?,?,?)""",
            ("h1", 1 if reachable else 0, time.time(), time.time() if reachable else None, 0),
        )
    return invokes


def _usage_table(ops):
    ops.db.execute(
        """CREATE TABLE IF NOT EXISTS product_host_observations (
            host TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL, document TEXT NOT NULL,
            observed_at REAL NOT NULL, PRIMARY KEY(host,kind,scope))"""
    )


def _usage(ops, providers, *, kind="usage", scope=""):
    ops.db.execute(
        """INSERT INTO product_host_observations(host, kind, scope, document, observed_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(host, kind, scope) DO UPDATE SET document=excluded.document""",
        ("h1", kind, scope, json.dumps({"providers": providers, "_source_binding": "hidden-binding"}), time.time()),
    )


def _continue_row(ops, *, created_at, session_id, provenance, api_access, gone_at=None, agent=_UNSET,
                  body_agent="claude", body_workspace="bat-ws"):
    operation_id = "op_" + uuid.uuid4().hex
    target = {"repository": "Acme/Widget", "host": "h1", "workspace_id": "bat-ws"}
    params = {} if agent is _UNSET else {"agent": agent}
    ops.db.execute(
        """INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,target,
           params,preconditions,status,result,external_refs,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (operation_id, "alice", "test", operation_id, "hash", "repository.continue", json.dumps(target),
         json.dumps(params), "{}", "succeeded", json.dumps({"host": "h1", "session_id": session_id}),
         "{}", created_at, created_at),
    )
    ops.db.execute(
        """INSERT INTO sessions_observed(
            host,session_id,body,digest,provenance,api_access,first_seen_at,last_seen_at,gone_at
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        ("h1", session_id, json.dumps({
            "agent_kind": body_agent, "workspace_id": body_workspace, "account_email": "hidden@example.com",
        }), "d" * 64, provenance, api_access, created_at, created_at, gone_at),
    )
    return operation_id


async def _profile(ops):
    saved = await _finish(ops, _save(ops)[0])
    return saved["result"]["profile_id"]


async def test_configured_host_never_verified_is_unknown_not_ready(ops):
    _configure(ops)
    profile_id = await _profile(ops)
    assert ops.db.execute("SELECT COUNT(*) FROM hosts_observed").fetchone()[0] == 0
    assert ops.db.execute("SELECT COUNT(*) FROM discovery_latest").fetchone()[0] == 0
    body = pm_work_profiles.readiness(ops, OWNER, WS, profile_id)
    for name in ("discovered", "signed_in_or_available", "can_start_own"):
        assert body["states"][name]["value"] != "ready"
    assert body["states"]["can_start_own"]["reason"] == "configured_unverified"
    assert body["coding_route_ready"] is False
    assert body["calls_bat"] is False
    assert body["contract"] == "work-profile-readiness/v1"
    assert body["resume"] == {"kind": "client_draft", "stored_on_server": False}
    assert body["configured"]["binding"] == {"repository": "Acme/Widget", "host": "h1", "workspace_id": "bat-ws"}
    assert body["workspace_id"] == WS
    from bat_agent_connector.pm_reads import read
    listed = read(ops, OWNER, "pm_work_profiles_list", {"workspace_id": WS})
    assert listed["count"] == 1 and listed["profiles"][0]["profile_id"] == profile_id
    got = read(ops, OWNER, "pm_work_profile_get", {"workspace_id": WS, "profile_id": profile_id})
    assert got["profile_id"] == profile_id and got["repository"] == "Acme/Widget"
    again = read(ops, OWNER, "pm_work_profile_readiness_get", {"workspace_id": WS, "profile_id": profile_id})
    assert again["states"] == body["states"] and again["calls_bat"] is False
    with pytest.raises(OperationError) as unexpected:
        read(ops, OWNER, "pm_work_profiles_list", {"workspace_id": WS, "draft": "back"})
    assert unexpected.value.code == "INVALID_PARAMS"


async def test_offline_host_and_unsigned_agent_stay_separate(ops):
    invokes = _configure(ops, reachable=False, register_send=True)
    profile_id = await _profile(ops)
    _usage_table(ops)
    _usage(ops, [
        {"provider": "claude", "reason": "unauthorized", "account_email": "secret@example.com"},
        {"provider": "codex", "reason": None, "account_email": "other@example.com"},
    ])
    _usage(ops, [{"provider": "claude", "reason": "unauthorized", "account_email": "model@example.com"}],
           kind="models", scope="claude:session")
    body = pm_work_profiles.readiness(ops, OWNER, WS, profile_id)
    assert body["states"]["can_observe"]["value"] == "unavailable"
    assert body["states"]["can_observe"]["reason"] == "host_unreachable"
    assert body["states"]["can_observe"]["value"] != "missing"
    assert body["states"]["can_start_own"]["value"] == "unavailable"
    assert body["states"]["can_start_own"]["reason"] == "host_unreachable"
    assert body["states"]["signed_in_or_available"]["value"] == "unavailable"
    assert body["states"]["signed_in_or_available"]["reason"] == "unauthorized"
    dumped = json.dumps(body)
    assert "secret@example.com" not in dumped and "other@example.com" not in dumped and "model@example.com" not in dumped
    assert "account_email" not in dumped and "_source_binding" not in dumped
    assert invokes["n"] == 0

    _usage(ops, [
        {"provider": "claude", "reason": "network", "account_email": "secret@example.com"},
        {"provider": "codex", "reason": "unauthorized", "account_email": "other@example.com"},
    ])
    body = pm_work_profiles.readiness(ops, OWNER, WS, profile_id)
    assert body["states"]["signed_in_or_available"]["value"] == "unknown"
    assert body["states"]["signed_in_or_available"]["reason"] == "auth_validity_unverified"
    assert body["states"]["can_observe"]["reason"] == "host_unreachable"
    dumped = json.dumps(body)
    assert "secret@example.com" not in dumped and "account_email" not in dumped
    assert invokes["n"] == 0


async def test_local_git_unavailable_has_no_ssh_fallback(ops):
    from bat_agent_connector.host_git import LOCAL_GIT_REASONS

    state = {"reason": "local_git_not_enabled"}
    invokes = _configure(ops, token=False, register_continue=True, register_send=True)
    ops.context["git_runner"] = SimpleNamespace(
        status=lambda _host: {"available": False, "reason": state["reason"], "details": {}},
    )
    profile_id = await _profile(ops)
    body = pm_work_profiles.readiness(ops, OWNER, WS, profile_id)
    assert state["reason"] in LOCAL_GIT_REASONS
    assert body["states"]["can_observe"]["reason"] == state["reason"]
    assert body["states"]["can_start_own"]["reason"] == state["reason"]
    assert body["smallest_gap"]["reason"] == state["reason"]
    assert body["smallest_gap"]["fix"] is None and body["smallest_gap"]["fallback"] is None
    assert "ssh" not in json.dumps(body["smallest_gap"])
    assert invokes["n"] == 0

    state["reason"] = "local_git_platform_unsupported"
    body = pm_work_profiles.readiness(ops, OWNER, WS, profile_id)
    assert body["states"]["can_observe"]["reason"] == "local_git_platform_unsupported"
    assert body["states"]["can_start_own"]["reason"] == "local_git_platform_unsupported"
    assert body["smallest_gap"]["reason"] == "local_git_platform_unsupported"
    assert body["smallest_gap"]["fix"] is None and body["smallest_gap"]["fallback"] is None


async def test_readiness_does_not_call_bat_or_write(ops):
    invokes = _configure(ops, reachable=True, register_send=True)
    profile_id = await _profile(ops)
    assert "action = 'repository.continue'" in pm_work_profiles._CONTINUE_SQL
    assert "status = 'succeeded'" in pm_work_profiles._CONTINUE_SQL
    plan = ops.db.execute(
        "EXPLAIN QUERY PLAN " + pm_work_profiles._CONTINUE_SQL, ("h1", "bat-ws")
    ).fetchall()
    detail = " ".join(row[3] for row in plan)
    assert "operations_continue_succeeded" in detail
    assert "USE TEMP B-TREE" not in detail
    before = ops.db.total_changes
    body = pm_work_profiles.readiness(ops, OWNER, WS, profile_id)
    assert ops.db.total_changes == before
    assert invokes["n"] == 0
    assert body["calls_bat"] is False and body["coding_route_ready"] is False


async def test_pm_eligibility_does_not_mark_coding_route_ready(ops, monkeypatch):
    from bat_agent_connector import pm_bat_operations, pm_native_capabilities

    analyst = Principal("carol", frozenset({"observe", "manage", "start"}))
    now = time.time()
    ops.db.execute(
        "INSERT INTO pm_workspace_members VALUES(?,?,?,?,?,?)",
        (WS, analyst.actor, "owner", pm_workspace.controller_for(ops.db, analyst), now, now),
    )
    _configure(ops, register_send=True)
    profile_id = await _profile(ops)

    def native(_service, _principal, _workspace, *, eligible):
        return {"eligible_for_preflight": eligible, "action_registered": True, "authorized": True}

    def bat(_service, _principal, _workspace):
        return {"eligible_for_preflight": False, "action_registered": True, "authorized": False}

    monkeypatch.setattr(pm_native_capabilities, "read", lambda *args: native(*args, eligible=True))
    monkeypatch.setattr(pm_bat_operations, "capabilities", bat)
    ready = pm_work_profiles.readiness(ops, analyst, WS, profile_id)
    assert ready["pm_analysis"]["pm_ready"] is True
    assert ready["coding_route_ready"] is False
    assert ready["states"]["can_start_own"]["value"] == "unknown"

    monkeypatch.setattr(pm_native_capabilities, "read", lambda *args: native(*args, eligible=False))
    unknown = pm_work_profiles.readiness(ops, analyst, WS, profile_id)
    assert unknown["states"]["can_start_own"]["value"] == "unknown"
    assert unknown["pm_analysis"]["pm_ready"] is False
    assert unknown["coding_route_ready"] is False


async def test_manual_session_does_not_satisfy_continue(ops):
    invokes = _configure(ops, register_send=True)
    profile_id = await _profile(ops)
    _continue_row(
        ops, created_at=10, session_id="codex-owned", provenance="connector_managed", api_access="managed",
        agent="codex", body_agent="claude",
    )
    body = pm_work_profiles.readiness(ops, OWNER, WS, profile_id)
    assert body["states"]["last_verified_execution"]["value"] != "ready"
    assert body["states"]["can_continue_own"]["verified"] is None

    _continue_row(
        ops, created_at=20, session_id="manual-one", provenance="manual", api_access="read_only", agent="claude",
    )
    body = pm_work_profiles.readiness(ops, OWNER, WS, profile_id)
    assert body["states"]["last_verified_execution"]["value"] != "ready"
    assert (body["states"]["can_continue_own"]["verified"] or {}).get("value") != "ready"

    _continue_row(
        ops, created_at=30, session_id="unknown-one", provenance="unknown", api_access="managed", agent="claude",
    )
    body = pm_work_profiles.readiness(ops, OWNER, WS, profile_id)
    assert body["states"]["last_verified_execution"]["value"] != "ready"
    assert (body["states"]["can_continue_own"]["verified"] or {}).get("value") != "ready"

    managed = _continue_row(
        ops, created_at=40, session_id="owned", provenance="connector_managed", api_access="managed",
    )
    body = pm_work_profiles.readiness(ops, OWNER, WS, profile_id)
    assert body["states"]["can_continue_own"]["verified"]["value"] == "ready"
    assert body["states"]["last_verified_execution"]["value"] == "ready"
    assert body["states"]["last_verified_execution"]["evidence_ref"]["operation_id"] == managed
    assert "hidden@example.com" not in json.dumps(body)
    assert invokes["n"] == 0

    ops.db.execute("UPDATE sessions_observed SET gone_at=? WHERE session_id=?", (50, "owned"))
    body = pm_work_profiles.readiness(ops, OWNER, WS, profile_id)
    assert body["states"]["last_verified_execution"]["value"] == "ready"
    assert body["states"]["can_continue_own"]["verified"] is None
    assert body["coding_route_ready"] is False
    assert invokes["n"] == 0


_CODE = {"repository": "o/r", "host": "h1", "workspace_id": "ws-1"}
_PLAIN_PRECONDITIONS = (
    "expected_work_item_fingerprint",
    "expected_work_version",
    "expected_binding_id",
    "expected_scope_revision",
    "expected_previous_package_id",
    "expected_project_version",
    "repository_id",
    "binding_digest",
    "expected_source_sha",
    "expected_latest_attempt_id",
)
_PREVIEW_KEYS = {
    "workspace_id", "work_item_id", "code_target", "candidates", "source",
    "binding", "package_plan", "readiness", "preconditions", "observed_at",
}
_PREVIEW_MESSAGE = "start preview accepts code_target, delivery, requirement_ids and instruction"


def _start_doc(**overrides):
    document = {
        "version": 1,
        "binding": dict(_CODE),
        "agent": "claude",
        "delivery_policy_ref": {"goal": "pull_request"},
    }
    document.update(overrides)
    return document


async def _save_start(w, document=None, *, who=None, profile_id=_UNSET, expected_revision=_UNSET,
                      withdrawn=_UNSET, make_default=_UNSET, expected_default_sequence=_UNSET):
    ops = w["d"].ops
    pm_work_profiles.install(ops)
    params = {"document": _start_doc() if document is None else document}
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
    operation, _created = ops.create(
        who or STARTER_OWNER, action=pm_work_profiles.ACTION, target={"workspace_id": w["workspace_id"]},
        params=params, preconditions={}, idempotency_key=uuid.uuid4().hex,
    )
    await settle_operations(ops)
    result = ops.get(operation["operation_id"])
    assert result["status"] == "succeeded", result
    return result["result"]


def _child_count(ops):
    present = ops.db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_work_start_children'"
    ).fetchone()
    if present is None:
        return 0
    return ops.db.execute("SELECT COUNT(*) FROM pm_work_start_children").fetchone()[0]


def _start_count(ops):
    return ops.db.execute("SELECT COUNT(*) FROM operations WHERE action='work.start'").fetchone()[0]


def _body(profile, **changes):
    body = {
        "profile_id": profile["profile_id"],
        "profile_revision": profile["revision"],
        "code_target": dict(_CODE),
        "delivery": "pull_request",
    }
    body.update(changes)
    return body


async def _view(w, body, who=None):
    return await pm_work_start.preview(
        w["d"].ops, who or STARTER_OWNER, w["workspace_id"], w["target"]["work_item_id"], body,
    )


def _open_start(w, preview, profile, *, who=None, agent="claude", delivery="pull_request", model=_UNSET,
                code_target=None, revision=None, digest=None, include_profile=True):
    params = {
        "instruction": "Implement credential validation and tests.",
        "agent": agent,
        "delivery": delivery,
        "code_target": dict(preview["code_target"]),
    }
    if code_target:
        params["code_target"].update(code_target)
    if model is not _UNSET:
        params["model"] = model
    pre = {key: preview["preconditions"][key] for key in _PLAIN_PRECONDITIONS}
    if include_profile:
        params["profile_id"] = profile["profile_id"]
        pre["expected_profile_revision"] = profile["revision"] if revision is None else revision
        pre["expected_profile_digest"] = profile["digest"] if digest is None else digest
    return w["d"].ops.create(
        who or STARTER_OWNER, action="work.start",
        target={"workspace_id": w["workspace_id"], "work_item_id": w["target"]["work_item_id"]},
        params=params, preconditions=pre, idempotency_key=uuid.uuid4().hex,
    )


def _profile_rows(ops, profile_id):
    return ops.db.execute(
        "SELECT COUNT(*) FROM work_profiles WHERE profile_id=?", (profile_id,)
    ).fetchone()[0]


async def test_work_start_pins_profile_revision_and_refuses_when_it_changes(start_ready):
    w = start_ready
    ops = w["d"].ops
    saved = await _save_start(w)
    viewed = await _view(w, _body(saved, instruction="Pin this revision."))
    assert viewed["profile"] == {
        "profile_id": saved["profile_id"],
        "revision": saved["revision"],
        "digest": saved["digest"],
        "delivery_goal": "pull_request",
    }
    assert viewed["preconditions"]["expected_profile_revision"] == saved["revision"]
    assert viewed["preconditions"]["expected_profile_digest"] == saved["digest"]

    outsider = Principal("outsider-pin", frozenset({"observe", "manage", "start"}))
    with pytest.raises(OperationError) as hidden:
        await _view(w, _body(saved), outsider)
    assert hidden.value.code == "NOT_FOUND" and hidden.value.message == "Workspace not found"

    children, starts = _child_count(ops), _start_count(ops)
    with pytest.raises(OperationError) as digest_mismatch:
        _open_start(w, viewed, saved, digest="ab" * 32)
    assert digest_mismatch.value.code == "PM_WORK_PROFILE_CHANGED"
    assert digest_mismatch.value.revision == saved["revision"]
    assert saved["digest"] not in digest_mismatch.value.message
    assert _child_count(ops) == children and _start_count(ops) == starts

    updated = await _save_start(
        w, _start_doc(model="next"), profile_id=saved["profile_id"], expected_revision=saved["revision"],
    )
    assert updated["revision"] == saved["revision"] + 1
    with pytest.raises(OperationError) as stale_preview:
        await _view(w, _body(saved))
    assert stale_preview.value.code == "PM_WORK_PROFILE_CHANGED"
    assert stale_preview.value.status == 409
    assert stale_preview.value.revision == updated["revision"]
    assert updated["digest"] not in stale_preview.value.message

    with pytest.raises(OperationError) as stale_start:
        _open_start(w, viewed, saved)
    assert stale_start.value.code == "PM_WORK_PROFILE_CHANGED"
    assert stale_start.value.revision == updated["revision"]
    assert _child_count(ops) == children and _start_count(ops) == starts

    other = await _save_start(w, _start_doc(agent="codex"))
    withdrawn = await _save_start(
        w, _start_doc(agent="codex"), profile_id=other["profile_id"], expected_revision=other["revision"],
        withdrawn=True,
    )
    with pytest.raises(OperationError) as preview_withdrawn:
        await _view(w, _body(withdrawn))
    assert preview_withdrawn.value.code == "PM_WORK_PROFILE_WITHDRAWN"
    assert preview_withdrawn.value.status == 404
    with pytest.raises(OperationError) as admit_withdrawn:
        _open_start(w, viewed, withdrawn)
    assert admit_withdrawn.value.code == "PM_WORK_PROFILE_WITHDRAWN"
    assert admit_withdrawn.value.status == 409
    with pytest.raises(OperationError) as older:
        _open_start(w, viewed, withdrawn, revision=other["revision"], digest=other["digest"])
    assert older.value.code == "PM_WORK_PROFILE_CHANGED"
    assert older.value.revision == withdrawn["revision"]
    assert _child_count(ops) == children and _start_count(ops) == starts

    current = await _view(w, _body(updated))
    operation, created = _open_start(w, current, updated, model="next")
    assert created is True and operation["status"] == "accepted"


async def test_default_change_keeps_pinned_start(start_ready):
    w = start_ready
    ops = w["d"].ops
    document = _start_doc()
    first = await _save_start(w, document)
    await _save_start(w, document, profile_id=first["profile_id"], expected_revision=1)
    third = await _save_start(w, document, profile_id=first["profile_id"], expected_revision=2)
    assert third["revision"] == 3 and third["digest"] == first["digest"]
    viewed = await _view(w, _body(third))
    rows = _profile_rows(ops, first["profile_id"])

    editor = Principal("profile-editor", frozenset({"observe", "manage"}))
    current = pm_workspace.get_workspace(ops.db, STARTER_OWNER, w["workspace_id"])
    member, _created = ops.create(
        STARTER_OWNER, action="pm.workspace.member.set", target={"workspace_id": w["workspace_id"]},
        params={"actor": editor.actor, "role": "contributor"},
        preconditions={"expected_version": current["version"]}, idempotency_key=uuid.uuid4().hex,
    )
    await settle_operations(ops)
    assert ops.get(member["operation_id"])["status"] == "succeeded"

    await _save_start(
        w, _start_doc(agent="codex"), who=editor, make_default=True, expected_default_sequence=None,
    )
    latest = ops.db.execute(
        "SELECT revision, digest FROM work_profiles WHERE profile_id=? ORDER BY revision DESC LIMIT 1",
        (first["profile_id"],),
    ).fetchone()
    assert latest["revision"] == 3 and latest["digest"] == first["digest"]
    assert _profile_rows(ops, first["profile_id"]) == rows
    operation, created = _open_start(w, viewed, third)
    assert created is True and operation["status"] == "accepted"
    assert operation["error_code"] != "PM_WORK_PROFILE_CHANGED"


async def test_profile_target_mismatch_reports_the_step(start_ready, mock, monkeypatch):
    calls = {"n": 0}
    real = pm_work_start.evaluate_package_plan

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(pm_work_start, "evaluate_package_plan", wrapped)
    w = start_ready
    ops = w["d"].ops
    saved = await _save_start(w, _start_doc(
        model="haiku", delivery_policy_ref={"goal": "reviewable_changes"},
    ))
    github = len(w["gh"].requests)
    bat = len(mock.invokes)
    preview_cases = (
        ({"code_target": {**_CODE, "repository": "other/repo"}, "delivery": "reviewable_changes"}, "code_target.repository"),
        ({"code_target": {**_CODE, "host": "h2"}, "delivery": "reviewable_changes"}, "code_target.host"),
        ({"code_target": {**_CODE, "workspace_id": "ws-2"}, "delivery": "reviewable_changes"}, "code_target.workspace_id"),
        ({"delivery": "pull_request"}, "delivery"),
    )
    for changes, step in preview_cases:
        with pytest.raises(OperationError) as mismatch:
            await _view(w, _body(saved, **changes))
        assert mismatch.value.code == "PM_WORK_PROFILE_TARGET_MISMATCH"
        assert mismatch.value.status == 409
        assert step in mismatch.value.message
    assert calls["n"] == 0
    assert len(w["gh"].requests) == github and len(mock.invokes) == bat

    viewed = await _view(w, _body(saved, delivery="reviewable_changes"))
    assert calls["n"] == 1
    children, starts = _child_count(ops), _start_count(ops)
    admission_cases = (
        ({"agent": "codex", "delivery": "reviewable_changes", "model": "haiku"}, "agent"),
        ({"delivery": "reviewable_changes"}, "model"),
        ({"delivery": "reviewable_changes", "model": "sonnet"}, "model"),
        ({"delivery": "pull_request", "model": "haiku"}, "delivery"),
        ({"delivery": "reviewable_changes", "model": "haiku", "code_target": {"repository": "other/repo"}}, "code_target.repository"),
        ({"delivery": "reviewable_changes", "model": "haiku", "code_target": {"host": "h2"}}, "code_target.host"),
        ({"delivery": "reviewable_changes", "model": "haiku", "code_target": {"workspace_id": "ws-2"}}, "code_target.workspace_id"),
    )
    for changes, step in admission_cases:
        with pytest.raises(OperationError) as mismatch:
            _open_start(w, viewed, saved, **changes)
        assert mismatch.value.code == "PM_WORK_PROFILE_TARGET_MISMATCH"
        assert step in mismatch.value.message
        assert calls["n"] == 1
    assert _child_count(ops) == children and _start_count(ops) == starts


async def test_preview_omitted_delivery_with_profile_goal_is_invalid_params(start_ready, mock, monkeypatch):
    calls = {"n": 0}
    real = pm_work_start.evaluate_package_plan

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(pm_work_start, "evaluate_package_plan", wrapped)
    w = start_ready
    saved = await _save_start(w, _start_doc(delivery_policy_ref={"goal": "reviewable_changes"}))
    github, bat = len(w["gh"].requests), len(mock.invokes)
    omitted = _body(saved)
    omitted.pop("delivery")
    for body in (omitted, _body(saved, delivery=None)):
        with pytest.raises(OperationError) as invalid:
            await _view(w, body)
        assert invalid.value.code == "INVALID_PARAMS" and invalid.value.status == 422
        assert invalid.value.message == "delivery must be reviewable_changes"
    assert calls["n"] == 0
    assert len(w["gh"].requests) == github and len(mock.invokes) == bat

    optional = await _save_start(w, _start_doc(delivery_policy_ref=None))
    body = _body(optional)
    body.pop("delivery")
    viewed = await _view(w, body)
    assert viewed["profile"]["delivery_goal"] is None
    assert viewed["package_plan"]["description"]["delivery"]["goal"] == "pull_request"


async def test_profile_preconditions_are_twelve_and_plain_start_stays_ten(start_ready):
    w = start_ready
    ops = w["d"].ops
    saved = await _save_start(w)
    viewed = await _view(w, _body(saved))
    assert set(viewed["preconditions"]) == set(_PLAIN_PRECONDITIONS) | {
        "expected_profile_revision", "expected_profile_digest",
    }
    assert viewed["preconditions"]["expected_profile_revision"] == saved["revision"]
    assert viewed["preconditions"]["expected_profile_digest"] == saved["digest"]

    plain = await _view(w, {
        "code_target": dict(_CODE),
        "delivery": "pull_request",
        "requirement_ids": ["member.login"],
        "instruction": "Plain start.",
    })
    assert set(plain) == _PREVIEW_KEYS
    assert set(plain["preconditions"]) == set(_PLAIN_PRECONDITIONS)
    assert "profile" not in plain

    sync = ops.context["fleet"].config.github.repos["o/r"].sync
    original = sync.bindings
    try:
        object.__setattr__(sync, "bindings", original + (("h1", "ws-2"),))
        blocked = await _view(w, {})
        assert blocked["preconditions"] is None
    finally:
        object.__setattr__(sync, "bindings", original)

    ops.context["fleet"].config.github.repos["other/repo"] = GitHubRepo(
        "other/repo", sync=RepositorySyncConfig("https://example.invalid/other.git", (("h1", "ws-other"),)),
    )
    other = await _save_start(w, _start_doc(
        binding={"repository": "other/repo", "host": "h1", "workspace_id": "ws-other"},
    ))
    blocked_profile = await _view(w, _body(other, code_target={
        "repository": "other/repo", "host": "h1", "workspace_id": "ws-other",
    }))
    assert blocked_profile["preconditions"] is None
    assert blocked_profile["package_plan"] is None
    assert "profile" not in blocked_profile


async def test_preview_rejects_non_object_and_half_profile_key(start_ready):
    w = start_ready
    ops = w["d"].ops
    for bad in (1, "profile_id", ["profile_id"]):
        with pytest.raises(OperationError) as invalid:
            await pm_work_start.preview(
                ops, STARTER_OWNER, w["workspace_id"], w["target"]["work_item_id"], bad,
            )
        assert invalid.value.code == "INVALID_PARAMS" and invalid.value.status == 422
        assert invalid.value.message == _PREVIEW_MESSAGE
    for half in ({"profile_id": "wpf_" + "ab" * 16}, {"profile_revision": 1}):
        with pytest.raises(OperationError) as invalid:
            await _view(w, half)
        assert invalid.value.code == "INVALID_PARAMS" and invalid.value.status == 422
        assert invalid.value.message == "profile_id and profile_revision are required together"
    empty = await _view(w, None)
    again = await _view(w, {})
    assert {key: value for key, value in empty.items() if key != "observed_at"} == {
        key: value for key, value in again.items() if key != "observed_at"
    }


async def test_work_start_without_profile_matches_existing_contract(start_ready):
    w = start_ready
    ops = w["d"].ops
    pm_work_profiles.install(ops)
    assert ops.db.execute("SELECT COUNT(*) FROM work_profiles").fetchone()[0] == 0
    viewed = await _view(w, {
        "code_target": dict(_CODE),
        "delivery": "pull_request",
        "requirement_ids": ["member.login"],
        "instruction": "Keep today's start.",
    })
    assert set(viewed) == _PREVIEW_KEYS
    assert "profile" not in viewed
    assert set(viewed["preconditions"]) == set(_PLAIN_PRECONDITIONS)

    children, starts = _child_count(ops), _start_count(ops)
    extra = dict(viewed["preconditions"])
    extra["expected_profile_revision"] = 1
    with pytest.raises(OperationError) as rejected:
        ops.create(
            STARTER_OWNER, action="work.start",
            target={"workspace_id": w["workspace_id"], "work_item_id": w["target"]["work_item_id"]},
            params={
                "instruction": "Implement credential validation and tests.",
                "agent": "claude",
                "delivery": "pull_request",
                "code_target": viewed["code_target"],
            },
            preconditions=extra, idempotency_key=uuid.uuid4().hex,
        )
    assert rejected.value.code == "PRECONDITION_REQUIRED"
    assert rejected.value.message == "work.start requires all ten preview preconditions"
    assert _child_count(ops) == children and _start_count(ops) == starts

    operation, created = ops.create(
        STARTER_OWNER, action="work.start",
        target={"workspace_id": w["workspace_id"], "work_item_id": w["target"]["work_item_id"]},
        params={
            "instruction": "Implement credential validation and tests.",
            "agent": "claude",
            "delivery": "pull_request",
            "code_target": viewed["code_target"],
        },
        preconditions=viewed["preconditions"], idempotency_key=uuid.uuid4().hex,
    )
    assert created is True and operation["status"] == "accepted"
    assert ops.db.execute("SELECT COUNT(*) FROM work_profiles").fetchone()[0] == 0
