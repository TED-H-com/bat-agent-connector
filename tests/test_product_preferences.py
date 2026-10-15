from __future__ import annotations

import json
import time

import pytest

from bat_agent_connector import api_auth
from bat_agent_connector import product_preferences as preferences
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_daemon import TaskDaemon
from tests.conftest import make_config
from tests.operation_helpers import settle_operations

PERSON = api_auth.Principal("person", frozenset({"observe"}), credential_id="old")


@pytest.fixture
async def daemon(mock, tmp_path):
    daemon = TaskDaemon(make_config(mock), tmp_path / "journal" / "tasks.sqlite3")
    daemon.acquire_owner()
    preferences.install(daemon.ops)
    await daemon.inventory.refresh_host("h1")
    try:
        yield daemon
    finally:
        await daemon.artifact_store.close_reaper()
        await daemon.api.session_observation.close()
        await daemon.inventory.close()
        await daemon.fleet.close()
        daemon.journal.close()


async def test_catalogs_come_from_host_and_preserve_unknown_preferences(daemon, monkeypatch):
    calls = []
    async def invoke(channel, params):
        calls.append((channel, params))
        if channel == "agent:list-presets":
            return [{"id": "claude-code", "name": "Host Claude", "command": "secret-local-command"},
                    {"id": "bogus-engine", "name": "not a supported central engine"}]
        if channel == "agent:usage-snapshot":
            return {"claude": {"provider": "claude", "fiveHour": {"utilization": .4, "resetsAt": 1700000000000},
                               "fetchedAt": time.time() * 1000, "secret": "do-not-forward"}}
        assert channel == "claude:get-supported-models"
        assert params["sessionId"]
        return [{"value": "host:model", "displayName": "A real host model", "source": "sdk"}]
    monkeypatch.setattr(daemon.fleet.client("h1"), "invoke", invoke)
    op, _ = daemon.ops.create(PERSON, action="preferences.models.update", target={"host": "h1"}, params={
        "initial_agent": "claude", "initial_model": "retired-model", "last_agent": "claude",
        "last_model": "host:model", "hidden": ["codex:retired-model"], "order": ["claude:host:model"]},
        preconditions={"expected_revision": 0}, idempotency_key="save-pref")
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["status"] == "succeeded"
    result = await preferences.read(daemon.ops, PERSON, "h1", agent="claude")
    assert result["model_catalog"]["models"] == [{"agent": "claude", "id": "host:model",
        "label": "A real host model", "source": "sdk", "available": True}]
    assert result["model_preferences"]["initial_model"] == "retired-model"
    assert result["unknown_models"] == [{"agent": "claude", "id": "retired-model"}]
    assert result["usage"]["providers"][0]["five_hour"]["utilization"] == .4
    assert result["usage"]["providers"][0]["kind"] == "subscription"
    assert "do-not-forward" not in json.dumps(result)
    assert "secret-local-command" not in json.dumps(result)
    assert len(calls) == 3


async def test_host_failure_retains_explicit_stale_usage_without_zero_balance(daemon, monkeypatch):
    async def invoke(channel, params):
        if channel == "agent:usage-snapshot":
            return {"codex": {"provider": "codex", "sevenDay": {"utilization": .9}, "fetchedAt": time.time() * 1000}}
        return []
    monkeypatch.setattr(daemon.fleet.client("h1"), "invoke", invoke)
    first = await preferences.read(daemon.ops, PERSON, "h1")
    async def offline(*args):
        raise OSError("PRIVATE HOST ERROR")
    monkeypatch.setattr(daemon.fleet.client("h1"), "invoke", offline)
    stale = await preferences.read(daemon.ops, PERSON, "h1", refresh=True)
    assert stale["usage"]["stale"] and stale["usage"]["reason"] == "host_unavailable"
    assert stale["usage"]["providers"] == first["usage"]["providers"]
    assert "PRIVATE HOST ERROR" not in json.dumps(stale)


async def test_reconfigured_host_never_inherits_the_previous_source_cache(daemon, monkeypatch):
    async def invoke(channel, params):
        if channel == "agent:usage-snapshot":
            return {"claude": {"provider": "claude", "fiveHour": {"utilization": .4},
                               "accountEmail": "prior@example.test", "fetchedAt": time.time() * 1000}}
        return []
    client = daemon.fleet.client("h1")
    monkeypatch.setattr(client, "invoke", invoke)
    first = await preferences.read(daemon.ops, PERSON, "h1")
    assert first["usage"]["providers"][0]["account_email"] == "prior@example.test"
    assert "_source_binding" not in json.dumps(first)
    daemon.fleet.config.host("h1").profile_id = "different-profile"
    async def offline(*args):
        raise OSError("unavailable")
    monkeypatch.setattr(client, "invoke", offline)
    changed = await preferences.read(daemon.ops, PERSON, "h1")
    assert changed["usage"]["status"] == "unavailable" and changed["usage"]["providers"] == []
    assert "prior@example.test" not in json.dumps(changed)


async def test_source_change_during_observation_cannot_publish_under_new_host(daemon, monkeypatch):
    async def racing(channel, params):
        daemon.fleet.config.host("h1").profile_id = "changed-during-read"
        return [{"id": "claude-code", "name": "Old source"}]
    monkeypatch.setattr(daemon.fleet.client("h1"), "invoke", racing)
    value = await preferences._observe(daemon.ops, "h1", "agents", "", "agent:list-presets", {}, preferences._agents)
    assert value["status"] == "unavailable" and value["reason"] == "host_binding_changed"
    assert not daemon.ops.db.execute("SELECT 1 FROM product_host_observations").fetchone()


async def test_model_preferences_are_rotation_stable_but_scope_isolated(daemon):
    from bat_agent_connector import host_git

    assert isinstance(daemon.ops.context["git_runner"], host_git.HostGitRunner)
    op, _ = daemon.ops.create(PERSON, action="preferences.models.update", target={"host": "h1"},
        params={"initial_agent": "codex"}, preconditions={"expected_revision": 0}, idempotency_key="personal")
    assert "git_execution" not in op["external_refs"]["admission_binding"]
    await settle_operations(daemon.ops)
    assert daemon.ops.get(op["operation_id"])["status"] == "succeeded"
    replayed, created = daemon.ops.create(PERSON, action="preferences.models.update", target={"host": "h1"},
        params={"initial_agent": "codex"}, preconditions={"expected_revision": 0}, idempotency_key="personal")
    assert not created and replayed["operation_id"] == op["operation_id"] and replayed["status"] == "succeeded"
    rotated = api_auth.Principal("person", PERSON.scopes, credential_id="replacement")
    rotated_replay, rotated_created = daemon.ops.create(rotated, action="preferences.models.update", target={"host": "h1"},
        params={"initial_agent": "codex"}, preconditions={"expected_revision": 0}, idempotency_key="personal")
    assert not rotated_created and rotated_replay["operation_id"] == op["operation_id"] and rotated_replay["status"] == "succeeded"
    a = preferences.dashboard_sync.identity(daemon.journal, PERSON)["principal_id"]
    b = preferences.dashboard_sync.identity(daemon.journal, rotated)["principal_id"]
    assert a == b
    assert preferences.preferences(daemon.ops.db, b, "h1")["initial_agent"] == "codex"
    for other in (api_auth.Principal("other", PERSON.scopes), api_auth.Principal("person", frozenset({"observe", "manage"})),
                  api_auth.Principal("person", PERSON.scopes, admin=True)):
        identity = preferences.dashboard_sync.identity(daemon.journal, other)["principal_id"]
        assert preferences.preferences(daemon.ops.db, identity, "h1")["revision"] == 0
        with pytest.raises(OperationError, match="original effective principal"):
            preferences._authorize(daemon.ops, other, daemon.ops.get(op["operation_id"]), "read")
    with pytest.raises(OperationError, match="changed"):
        daemon.ops.create(PERSON, action="preferences.models.update", target={"host": "h1"},
            params={"initial_agent": "claude"}, preconditions={"expected_revision": 0}, idempotency_key="stale-edit")


def test_subscription_normalization_does_not_guess_missing_fields():
    now = time.time()
    result = preferences.normalize_usage({"claude": {"provider": "claude", "fetchedAt": now * 1000,
        "fiveHour": {"utilization": 99}, "sevenDay": None, "token": "never-export"}}, now)
    assert result["status"] == "unavailable"
    assert result["providers"][0]["five_hour"] is None
    assert "never-export" not in json.dumps(result)
