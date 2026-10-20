"""Delivery B configuration and allocated data step 3 (plan §09/§17/§28)."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from bat_agent_connector import api_auth, deployment_store
from bat_agent_connector.config import parse_config
from bat_agent_connector.errors import ConfigError
from bat_agent_connector.task_journal import LATEST_DATA_STEP, Journal
from tests.legacy_deployment_fixture import legacy_deployment
from tests.test_journal_compatibility import snapshot as journal_snapshot


def recipe_config():
    return {"github": {"token_ref": "env:FAKE_GH_TOKEN", "repos": [{"repository": "o/r"}]},
            "deploy": {"recipes": [{"name": "prod", "repository": "o/r", "environment": "production",
                "workflow": "deploy.yml", "deploy_job": "deploy", "mode": "workflow_dispatch", "ref": "main",
                "inputs": {"source_sha": "source_sha", "operation_id": "operation_id"},
                "verification": {"kind": "http_json", "url": "https://deployment.example/version",
                                 "version_required": True, "health_required": True},
                "rollback": {"supported": True, "identity": "source_sha", "not_undone": ["database migrations"]}}]}}


@pytest.mark.parametrize("missing", ["source_sha", "operation_id"])
def test_issue32_dispatch_requires_source_sha_and_operation_id_inputs(missing):
    data = recipe_config()
    data["deploy"]["recipes"][0]["inputs"].pop(missing)
    with pytest.raises(ConfigError, match="source_sha and operation_id"):
        parse_config(data)


@pytest.mark.parametrize("url", ["http://deployment.example/version", "https://user:secret@deployment.example/v",
                                "https://deployment.example:bad/v", "file:///tmp/version"])
def test_deploy_verifier_rejects_non_https_non_loopback_and_embedded_credentials(url):
    data = recipe_config()
    data["deploy"]["recipes"][0]["verification"]["url"] = url
    with pytest.raises(ConfigError):
        parse_config(data)


@pytest.mark.parametrize("change", [{"version_required": False, "health_required": False},
                                  {"token_ref": "inline-secret"}, {"max_bytes": 0}, {"timeout_s": 1},
                                  {"headers": {"Authorization": "inline-secret"}}])
def test_deploy_verification_settings_are_bounded_and_require_runtime_evidence(change):
    data = recipe_config()
    data["deploy"]["recipes"][0]["verification"].update(change)
    with pytest.raises(ConfigError):
        parse_config(data)


def test_deploy_missing_verification_is_readable_but_not_configured():
    data = recipe_config()
    data["deploy"]["recipes"][0].pop("verification")
    assert parse_config(data).github.recipes["prod"].verification is None


@pytest.mark.parametrize("change", [{"mode": "on_merge"}, {"rollback": {"supported": True}},
                                  {"rollback": {"supported": True, "identity": "artifact", "not_undone": []}},
                                  {"ordering": {"cancel_in_progress": True}}])
def test_rollback_and_ordering_configuration_refuses_unsupported_routes(change):
    data = recipe_config()
    data["deploy"]["recipes"][0].update(change)
    with pytest.raises(ConfigError):
        parse_config(data)


def test_environment_aliases_must_share_provider_ordering():
    data = recipe_config()
    alias = copy.deepcopy(data["deploy"]["recipes"][0])
    alias["name"] = "alias"
    data["deploy"]["recipes"].append(alias)
    assert len(parse_config(data).github.recipes) == 2
    alias["ref"] = "other"
    alias["workflow"] = "release.yml"
    assert len(parse_config(data).github.recipes) == 2
    alias["ordering"] = {"concurrency_group": "different-group"}
    with pytest.raises(ConfigError, match="same environment"):
        parse_config(data)


def version2_database(path):
    # Empty post-observation journal: step 2 belongs to #35 and is not implemented by delivery.
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=2")


def legacy_operations(journal):
    actor = api_auth.Principal("deployment-test", frozenset({"deploy"}))
    success = legacy_deployment(journal, actor, action="deployment.start", target={"recipe": "prod"},
                         params={"source_sha": "a" * 40}, idempotency_key="old-success")
    journal.db.execute("UPDATE operations SET status='succeeded',result=? WHERE operation_id=?",
                       (json.dumps({"deployed": True, "source_sha": "a" * 40, "repository": "o/r",
                                    "environment": "production", "workflow": "deploy.yml", "run_id": 12}),
                        success["operation_id"]))
    pending = legacy_deployment(journal, actor, action="delivery.merge_and_deploy", target={"recipe": "prod", "repository": "o/r"},
                         idempotency_key="old-pending")
    journal.db.execute("UPDATE operations SET status='waiting_external',external_refs=? WHERE operation_id=?",
                       (json.dumps({"deploy_run_id": 13, "merged_sha": "b" * 40}), pending["operation_id"]))
    journal.db.execute("INSERT INTO operation_steps(operation_id,seq,name,status,request,started_at) "
                       "VALUES(?,1,'deploy.dispatch','succeeded','{}',1)", (pending["operation_id"],))
    return success, pending


def test_delivery_v2_migration_keeps_history_unverified_and_reopens(tmp_path):
    """§09/§28: step 3 preserves operation/task/event authority and never invents runtime evidence."""
    path = tmp_path / "legacy.db"
    j = Journal(path)
    old = legacy_operations(j)
    unchanged = {t: [tuple(r) for r in j.db.execute(f"SELECT * FROM {t}")]  # noqa: S608
                 for t in ("tasks", "operations", "events", "api_events")}
    j.db.execute("PRAGMA user_version=2")
    j.close()
    j = Journal(path)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == 3
    saved = deployment_store.deployment(j.db, operation_id=old[0]["operation_id"])
    assert saved["state"] == "unverified" and saved["generation"] is None and saved["evidence"] is None
    pending = deployment_store.deployment(j.db, operation_id=old[1]["operation_id"])
    assert pending["run_id"] == 13 and not pending["provider_terminal"]
    assert pending["identity"]["source_sha"] == "b" * 40
    assert not j.db.execute("SELECT 1 FROM deployment_environments WHERE current_deployment_id IS NOT NULL").fetchone()
    for table, original in unchanged.items():
        rows = [tuple(r) for r in j.db.execute(f"SELECT * FROM {table}")]  # noqa: S608 - fixed tables
        assert rows[:len(original)] == original
        if table != "api_events":
            assert rows == original
    snapshots = [e for e in j.api_events()["events"] if e["kind"] == "deployment.backfilled"]
    assert len(snapshots) == 2
    assert all(e["context"]["backfilled"] and e["context"]["occurred_at"] is None for e in snapshots)
    events_before = j.api_head()
    before = [tuple(r) for r in j.db.execute("SELECT * FROM deployments")]
    j.close()
    j = Journal(path)
    assert [tuple(r) for r in j.db.execute("SELECT * FROM deployments")] == before
    assert j.api_head() == events_before
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == 3
    j.close()


def test_delivery_data_step3_crash_rolls_back_whole(tmp_path, monkeypatch):
    path = tmp_path / "crash.db"
    j = Journal(path)
    legacy_operations(j)
    j.db.execute("PRAGMA user_version=2")
    original = deployment_store._legacy_deployment
    def crash(journal, row):
        original(journal, row)
        raise RuntimeError("simulated process failure")
    monkeypatch.setattr(deployment_store, "_legacy_deployment", crash)
    with pytest.raises(RuntimeError, match="process failure"):
        deployment_store.backfill(j)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert j.db.execute("SELECT COUNT(*) FROM deployments").fetchone()[0] == 0
    assert j.db.execute("SELECT COUNT(*) FROM deployment_environments").fetchone()[0] == 0
    monkeypatch.setattr(deployment_store, "_legacy_deployment", original)
    deployment_store.backfill(j)
    assert j.db.execute("SELECT COUNT(*) FROM deployments").fetchone()[0] == 2
    j.close()


@pytest.mark.parametrize("version", [LATEST_DATA_STEP, 8])
def test_delivery_fresh_post_step2_journal_ends_at3_and_ddl_preserves_current_version(tmp_path, version):
    path = tmp_path / "fresh.db"
    version2_database(path)
    j = Journal(path)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == 3
    j.db.execute(f"PRAGMA user_version={version}")
    j.db.execute("DROP TABLE deployments")
    j.close()
    if version > LATEST_DATA_STEP:
        before = journal_snapshot(path)
        with pytest.raises(ValueError, match="JOURNAL_NEWER_DATA_STEP"):
            Journal(path)
        assert journal_snapshot(path) == before
        return
    j = Journal(path)
    assert j.db.execute("PRAGMA user_version").fetchone()[0] == 3
    assert j.db.execute("SELECT COUNT(*) FROM deployments").fetchone()[0] == 0
    j.close()


@pytest.mark.parametrize("interval", [59, 86401, -1, True, "300", float("nan"), float("inf")])
def test_deployment_reconcile_cadence_has_a_minimum_and_finite_bound(interval):
    data = recipe_config()
    data["github"]["deployment_reconcile_interval_s"] = interval
    with pytest.raises(ConfigError, match="deployment_reconcile_interval_s"):
        parse_config(data)


def test_deployment_reconcile_cadence_default_and_override():
    data = recipe_config()
    assert parse_config(data).github.deployment_reconcile_interval_s == 300
    data["github"]["deployment_reconcile_interval_s"] = 60
    assert parse_config(data).github.deployment_reconcile_interval_s == 60
