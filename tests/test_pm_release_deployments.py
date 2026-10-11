"""Baseline selection from the actual deployment engine's mock-provider receipts."""
from __future__ import annotations

from dataclasses import replace

import pytest

from bat_agent_connector import deployment_store, pm_evidence, pm_lifecycle, pm_workspace, work_items
from bat_agent_connector import pm_release_baselines as baselines
from bat_agent_connector.operations import OperationError
from tests import test_deployments as deployments
from tests.test_pm_evidence import OWNER, act, bind_project, workspace
from tests.test_pm_release_baselines import execute, request

gh = deployments.gh
make_daemon = deployments.make_daemon


async def setup(d, gh):
    ops = d.ops
    pm_workspace.install(ops)
    pm_evidence.install(ops)
    pm_lifecycle.install(ops)
    baselines.install(ops)
    for action in work_items.ACTIONS:
        if action.name not in ops.actions:
            ops.register(action)
    ws = await workspace(ops)
    pid = (await act(ops, "project.create", params={"name": "Published product", "repositories": ["o/r"]}))["project_id"]
    binding = await bind_project(ops, ws["workspace_id"], pid)
    deployments.source_on_main(gh)
    return {"workspace_id": ws["workspace_id"], "repository": "o/r"}, binding["binding_id"]


def selection(ops, target, binding, done):
    body = request(ops, target, binding)
    body["params"].update(source={"kind": "deployment", "deployment_id": done["result"]["deployment_id"]}, channel="production")
    body["preconditions"]["expected_commit_sha"] = deployments.MERGED
    return body


async def test_real_deploy_record_proves_original_commit_not_newer_main_or_package_version(make_daemon, gh):
    d = make_daemon()
    target, binding = await setup(d, gh)
    done = await deployments.deployed(d, gh)
    # Main and the workflow source may advance independently of the fixed product
    # identity, so do not read their checks as this released version's quality.
    gh.branches["main"] = "c" * 40
    selected = await execute(d.ops, selection(d.ops, target, binding, done))
    doc = baselines.get_baseline(d.ops, OWNER, target["workspace_id"], selected["result"]["baseline_id"])["baseline"]["document"]
    assert doc["release_version"] == {"kind": "commit", "value": deployments.MERGED}
    assert doc["commit_sha"] == deployments.MERGED
    assert doc["channel"] == "production"
    assert doc["delivery_status"] == {"github_release": "not_assessed", "package_registry": "not_assessed", "deployment": "verified"}
    assert doc["quality"]["status"] == "unassessed"
    proof = doc["proof"]["source"]
    assert proof["operation_id"] == done["operation_id"]
    assert proof["version_checked"] is proof["health_checked"] is True
    assert proof["current_environment_status"] == "not_rechecked"
    assert doc["maintenance_authorized"] is False


@pytest.mark.parametrize("field,value", [("expected_commit_sha", "c" * 40), ("expected_repository_id", 9898)])
async def test_deployment_record_cannot_bind_another_repository_or_version(make_daemon, gh, field, value):
    d = make_daemon()
    target, binding = await setup(d, gh)
    done = await deployments.deployed(d, gh)
    body = selection(d.ops, target, binding, done)
    body["preconditions"][field] = value
    with pytest.raises(OperationError, match="PM_BASELINE_SOURCE_UNPROVEN"):
        d.ops.create(OWNER, **body)
    assert baselines.current_baseline(d.ops, OWNER, target["workspace_id"])["baseline"] is None


async def test_deployment_environment_is_the_only_channel_and_original_provider_must_match(make_daemon, gh):
    d = make_daemon()
    target, binding = await setup(d, gh)
    done = await deployments.deployed(d, gh)
    body = selection(d.ops, target, binding, done)
    body["params"]["channel"] = "stable"
    with pytest.raises(OperationError, match="PM_BASELINE_SOURCE_UNPROVEN"):
        d.ops.create(OWNER, **body)
    body["params"]["channel"] = "production"
    # The client and current config move together; original recorded provider does not.
    d.ops.context["github_config"].api_url = "https://different.example/api"
    with pytest.raises(OperationError, match="PM_BASELINE_REPOSITORY_CHANGED"):
        d.ops.create(OWNER, **body)


async def test_health_only_actual_success_is_not_fixed_version_publication_proof(make_daemon, gh):
    d = make_daemon()
    recipe = d.ops.context["github_config"].recipes["prod"]
    d.ops.context["github_config"].recipes["prod"] = replace(recipe, verification=replace(recipe.verification, version_required=False))
    target, binding = await setup(d, gh)
    d.ops.context["deployment_verifier"].response = {"repository_id": 4242, "environment": "production", "healthy": True}
    done = await deployments.deployed(d, gh)
    assert done["result"]["evidence"]["runtime"]["version_checked"] is False
    with pytest.raises(OperationError, match="PM_BASELINE_SOURCE_UNPROVEN"):
        d.ops.create(OWNER, **selection(d.ops, target, binding, done))


async def test_older_verified_deployment_remains_historical_evidence_after_newer_deploy(make_daemon, gh):
    d = make_daemon()
    target, binding = await setup(d, gh)
    first = await deployments.deployed(d, gh)
    second = await deployments.deployed(d, gh, key="second")
    assert first["result"]["deployment_id"] != second["result"]["deployment_id"]
    selected = await execute(d.ops, selection(d.ops, target, binding, first))
    doc = baselines.get_baseline(d.ops, OWNER, target["workspace_id"], selected["result"]["baseline_id"])["baseline"]["document"]
    assert doc["proof"]["source"]["deployment_id"] == first["result"]["deployment_id"]
    assert doc["proof"]["source"]["current_environment_status"] == "not_rechecked"


async def test_unverified_provider_terminal_document_is_not_a_release_receipt(make_daemon, gh):
    d = make_daemon()
    target, binding = await setup(d, gh)
    done = await deployments.deployed(d, gh)
    # A mere provider completion, even with the right hash, does not replace the
    # engine's committed verification fact. Simulate missing migration evidence.
    deployment_store.update(d.journal, done["result"]["deployment_id"], facts={"verified": False})
    with pytest.raises(OperationError, match="PM_BASELINE_SOURCE_UNPROVEN"):
        d.ops.create(OWNER, **selection(d.ops, target, binding, done))
