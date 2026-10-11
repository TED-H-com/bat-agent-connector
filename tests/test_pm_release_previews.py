"""User-selected publication discovery, bounded pages and fixed preview handoff."""
import copy
import uuid

import pytest

from bat_agent_connector import pm_release_previews as previews
from bat_agent_connector.operations import OperationError
from tests.test_pm_release_baselines import NEXT, OWNER, READER, SHA, Provider, execute, remove_repo, setup
from tests.test_pm_release_baselines import base_ops as evidence_base
from tests.test_pm_release_baselines import ops as baseline_ops
from tests.test_pm_release_deployments import deployments
from tests.test_pm_release_deployments import setup as deployment_setup

base_ops, ops = evidence_base, baseline_ops
gh, make_daemon = deployments.gh, deployments.make_daemon


class ChoiceProvider(Provider):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.releases = [self.release]
        self.list_hook = None

    async def call(self, method, path, body=None, query=None):
        if path.endswith("/releases"):
            assert method == "GET" and body is None
            assert set(query) == {"page", "per_page"} and 1 <= query["per_page"] <= 20
            self.calls.append(path)
            if self.list_hook:
                self.list_hook()
            start = (query["page"] - 1) * query["per_page"]
            return 200, {"items": copy.deepcopy(self.releases[start:start + query["per_page"]])}
        return await super().call(method, path, body, query)


async def test_local_choices_are_quiet_then_explicit_preview_supplies_native_ids_and_fixed_cas(ops):
    target, binding, _ = await setup(ops)
    gh = ops.context["github"] = ChoiceProvider(ops.context["github_config"])
    wid = target["workspace_id"]
    before = ops.db.total_changes
    local = previews.list_sources(ops, READER, wid)
    assert local["bindings"][0]["project_binding_id"] == binding
    assert local["bindings"][0]["repositories"] == ["example/product"]
    assert gh.calls == []
    remote = await previews.list_releases(ops, READER, wid, project_binding_id=binding, repository=target["repository"])
    assert remote["releases"] == [{"release_id": 41, "tag": "v1.4.0", "channel": "stable", "published_at": "2026-01-01T00:00:00Z"}]
    preview = await previews.preview(ops, READER, wid, project_binding_id=binding, repository=target["repository"],
                                     kind="github_release", release_id=remote["releases"][0]["release_id"])
    fixed = preview["registration"]
    assert fixed["preconditions"] == {"expected_repository_id": 4242, "expected_commit_sha": SHA,
        "expected_previous_baseline_id": None, "expected_baseline_version": 0}
    assert fixed["params"]["channel"] == "stable" and fixed["params"]["maintenance_branch"] is None
    assert preview["requires_input"] == ["rationale"] and preview["maintenance_authorized"] is False
    assert preview["proof"]["selection"] == {"repository_id": 4242, "commit_sha": SHA, "channel": "stable"}
    assert ops.db.total_changes == before
    # UI adds only the actual owner's direction; it never types a SHA or native ID.
    fixed["params"]["rationale"] = "Select the published 1.4 release for future maintenance planning"
    fixed["idempotency_key"] = uuid.uuid4().hex
    done = await execute(ops, fixed)
    assert done["result"]["version"] == 1 and done["result"]["maintenance_authorized"] is False


async def test_filtered_release_page_advances_and_prerelease_remains_distinct(ops):
    target, binding, _ = await setup(ops)
    gh = ops.context["github"] = ChoiceProvider(ops.context["github_config"])
    gh.releases = [{**gh.release, "draft": True}, {**gh.release, "prerelease": True}]
    args = {"project_binding_id": binding, "repository": target["repository"], "limit": 1}
    first = await previews.list_releases(ops, OWNER, target["workspace_id"], **args)
    assert first["releases"] == [] and first["next_page"] == 2
    second = await previews.list_releases(ops, OWNER, target["workspace_id"], page=first["next_page"], **args)
    assert second["releases"][0]["channel"] == "prerelease" and second["next_page"] == 3
    last = await previews.list_releases(ops, OWNER, target["workspace_id"], page=3, **args)
    assert last["releases"] == [] and last["next_page"] is None
    gh.release["prerelease"] = True
    preview = await previews.preview(ops, OWNER, target["workspace_id"], project_binding_id=binding,
        repository=target["repository"], kind="github_release", release_id=41)
    assert preview["registration"]["params"]["channel"] == "prerelease"


async def test_preview_cannot_authorize_changed_tag_or_hide_newer_baseline(ops):
    target, binding, _ = await setup(ops)
    gh = ops.context["github"]
    value = await previews.preview(ops, OWNER, target["workspace_id"], project_binding_id=binding,
        repository=target["repository"], kind="github_release", release_id=41)
    fixed = value["registration"]
    fixed["params"]["rationale"] = "Explicit choice"
    fixed["idempotency_key"] = uuid.uuid4().hex
    gh.tag["object"]["sha"] = NEXT
    done = await execute(ops, fixed, success=False)
    assert done["error_code"] == "PM_BASELINE_TAG_CHANGED"
    assert ops.db.execute("SELECT COUNT(*) FROM pm_release_baselines").fetchone()[0] == 0


async def test_choices_and_preview_recheck_original_project_during_external_reads(ops):
    target, binding, pid = await setup(ops)
    gh = ops.context["github"] = ChoiceProvider(ops.context["github_config"])
    gh.list_hook = lambda: ops.db.execute("UPDATE projects SET repositories='[]' WHERE project_id=?", (pid,))
    with pytest.raises(OperationError) as error:
        await previews.list_releases(ops, OWNER, target["workspace_id"], project_binding_id=binding, repository=target["repository"])
    assert error.value.status == 404
    assert len(gh.calls) == 2
    with pytest.raises(OperationError):
        await previews.preview(ops, OWNER, target["workspace_id"], project_binding_id=binding,
            repository=target["repository"], kind="github_release", release_id=41)
    assert len(gh.calls) == 2


@pytest.mark.parametrize("method,extra", [
    ("pm_release_baseline_sources_list", {"after": True}),
    ("pm_release_baseline_sources_list", {"after": 2**80}),
    ("pm_release_baseline_releases_list", {"page": 0}),
    ("pm_release_baseline_releases_list", {"page": 10001}),
    ("pm_release_baseline_releases_list", {"limit": 21}),
    ("pm_release_baseline_preview", {"kind": "package_registry"}),
    ("pm_release_baseline_preview", {"kind": "github_release", "release_id": True}),
    ("pm_release_baseline_preview", {"kind": "github_release", "release_id": 41, "maintenance_branch": " ../main"}),
    ("pm_release_baseline_preview", {"kind": "github_release", "release_id": 41, "expected_commit_sha": SHA}),
])
async def test_choice_parameters_are_bounded_exact_and_cannot_supply_identity(ops, method, extra):
    target, binding, _ = await setup(ops)
    args = {"workspace_id": target["workspace_id"]}
    if method != "pm_release_baseline_sources_list":
        args.update(project_binding_id=binding, repository=target["repository"])
    with pytest.raises(OperationError):
        await previews.read(ops, OWNER, method, {**args, **extra})
    assert ops.context["github"].calls == []


async def test_deployment_choices_use_original_local_receipts_then_preview_exact_runtime_source(make_daemon, gh):
    d = make_daemon()
    target, binding = await deployment_setup(d, gh)
    done = await deployments.deployed(d, gh)
    wid = target["workspace_id"]
    chosen = previews.list_deployments(d.ops, OWNER, wid, project_binding_id=binding, repository=target["repository"])
    assert len(chosen["deployments"]) == 1
    selected = chosen["deployments"][0]
    assert selected["operation_id"] == done["operation_id"]
    assert selected["version"] == {"kind": "commit", "value": deployments.MERGED}
    value = await previews.preview(d.ops, OWNER, wid, project_binding_id=binding, repository=target["repository"],
        kind="deployment", deployment_id=selected["deployment_id"])
    assert value["registration"]["preconditions"]["expected_commit_sha"] == deployments.MERGED
    assert value["registration"]["params"]["channel"] == "production"
    assert value["proof"]["source"]["version_checked"] is True


async def test_local_catalog_source_withdrawal_removes_repository_choices(ops):
    target, _, pid = await setup(ops)
    assert previews.list_sources(ops, OWNER, target["workspace_id"])["bindings"]
    await remove_repo(ops, pid)
    assert previews.list_sources(ops, OWNER, target["workspace_id"])["bindings"] == []
    assert ops.context["github"].calls == []
