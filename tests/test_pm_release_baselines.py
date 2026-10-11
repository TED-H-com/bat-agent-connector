"""Fixed publication evidence and local selection; no live provider or model calls."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_lifecycle, work_items
from bat_agent_connector import pm_release_baselines as baselines
from bat_agent_connector import pm_release_sources as sources
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.config import GitHubConfig, GitHubRepo
from bat_agent_connector.github import GitHubAmbiguous
from bat_agent_connector.operations import OpContext, OperationError
from tests.test_pm_evidence import CONTRIBUTOR, OWNER, READER, act, bind_project, member, project, workspace
from tests.test_pm_evidence import ops as evidence_ops
from tests.test_pm_lifecycle import decide

base_ops = evidence_ops
SHA = "a" * 40
TAG_SHA = "b" * 40
NEXT = "c" * 40


class Provider:
    def __init__(self, cfg):
        self.cfg = cfg
        self.calls = []
        self.hook = None
        self.release = {"id": 41, "tag_name": "v1.4.0", "draft": False, "prerelease": False,
                        "published_at": "2026-01-01T00:00:00Z", "target_commitish": "main"}
        self.ref = {"ref": "refs/tags/v1.4.0", "object": {"type": "tag", "sha": TAG_SHA}}
        self.tag = {"sha": TAG_SHA, "object": {"type": "commit", "sha": SHA}}
        self.branch = {"ref": "refs/heads/support/1.x", "object": {"type": "commit", "sha": NEXT}}
        self.repo = {"id": 4242, "full_name": "example/product"}
        self.compare = {"base_commit": {"sha": SHA}, "merge_base_commit": {"sha": SHA}, "status": "ahead"}

    async def call(self, method, path, body=None, query=None):
        assert method == "GET" and body is None and query is None
        self.calls.append(path)
        if self.hook:
            await self.hook(path)
        prefix = "/repos/example/product"
        responses = {prefix: self.repo, prefix + "/releases/41": self.release,
                     prefix + "/git/ref/tags/v1.4.0": self.ref, prefix + "/git/tags/" + TAG_SHA: self.tag,
                     prefix + "/git/commits/" + SHA: {"sha": SHA},
                     prefix + "/git/ref/heads/support%2F1.x": self.branch,
                     prefix + "/compare/" + SHA + "..." + NEXT: self.compare}
        return (200, copy.deepcopy(responses[path])) if path in responses else (404, {})


@pytest.fixture
def ops(base_ops):
    cfg = GitHubConfig(repos={"example/product": GitHubRepo("example/product")})
    base_ops.context.update(github_config=cfg, github=Provider(cfg))
    pm_lifecycle.install(base_ops)
    baselines.install(base_ops)
    return base_ops


async def setup(ops):
    ws = await workspace(ops)
    await member(ops, ws, READER.actor, "reader")
    await member(ops, ws, CONTRIBUTOR.actor, "contributor")
    pid = await project(ops)
    binding = await bind_project(ops, ws["workspace_id"], pid)
    return {"workspace_id": ws["workspace_id"], "repository": "example/product"}, binding["binding_id"], pid


def request(ops, target, binding_id, *, key=None, branch=None):
    head = baselines._head(ops.db, target["workspace_id"])
    return {"action": baselines.ACTION, "target": target,
            "params": {"project_binding_id": binding_id, "source": {"kind": "github_release", "release_id": 41},
                       "channel": "stable", "maintenance_branch": branch,
                       "known_limitations": ["Offline recovery has not been accepted on real hardware"],
                       "rationale": "Maintain the already published 1.4 release"},
            "preconditions": {"expected_baseline_version": head["version"], "expected_previous_baseline_id": head["baseline_id"],
                              "expected_repository_id": 4242, "expected_commit_sha": SHA},
            "idempotency_key": key or uuid.uuid4().hex}


async def execute(ops, body, *, who=OWNER, success=True):
    op = ops.create(who, **body)[0]
    await ops._execute(op["operation_id"])
    op = ops.get(op["operation_id"])
    if success:
        assert op["status"] == "succeeded", op
    return op


async def remove_repo(ops, pid):
    p = work_items._get_project(ops.db, pid)
    await act(ops, "project.update", {"project_id": pid}, {"repositories": ["other/product"]}, {"expected_version": p["version"]})


async def test_fixed_release_not_target_commitish_or_package_or_quality_or_maintain(ops):
    target, binding, _ = await setup(ops)
    before = ops.db.execute("PRAGMA user_version").fetchone()[0]
    baselines.install(ops)
    assert ops.db.execute("PRAGMA user_version").fetchone()[0] == before
    done = await execute(ops, request(ops, target, binding, branch="support/1.x"))
    current = baselines.current_baseline(ops, READER, target["workspace_id"])
    doc = current["baseline"]["document"]
    assert doc["commit_sha"] == SHA and doc["release_version"] == {"kind": "tag", "value": "v1.4.0"}
    assert doc["proof"]["source"]["tag_chain"] == [{"type": "tag", "sha": TAG_SHA}, {"type": "commit", "sha": SHA}]
    assert doc["proof"]["source"]["target_commitish_used"] is False
    assert doc["proof"]["maintenance_branch"]["head_sha"] == NEXT
    assert doc["delivery_status"] == {"github_release": "published", "package_registry": "not_assessed", "deployment": "not_assessed"}
    assert doc["quality"]["status"] == "unassessed" and doc["quality"]["functional_acceptance_verified"] is False
    assert doc["quality"]["known_limitations"][0]["status"] == "owner_claim"
    assert doc["maintenance_authorized"] is current["maintenance_available"] is False
    assert pm_lifecycle.get_lifecycle(ops, OWNER, target["workspace_id"])["lifecycle"]["stage"] == "idea"
    assert ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert done["target"] == target
    assert baselines.operation_visible(ops.db, READER, done)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ops.db.execute("UPDATE pm_release_baselines SET document='{}'")


async def test_plain_lightweight_tag_and_unconfigured_branch_are_valid(ops):
    target, binding, _ = await setup(ops)
    ops.context["github"].ref["object"] = {"type": "commit", "sha": SHA}
    done = await execute(ops, request(ops, target, binding))
    doc = baselines.get_baseline(ops, OWNER, target["workspace_id"], done["result"]["baseline_id"])["baseline"]["document"]
    assert doc["proof"]["maintenance_branch"] == {"status": "unconfigured"}
    assert doc["proof"]["source"]["tag_chain"] == [{"type": "commit", "sha": SHA}]


@pytest.mark.parametrize("who", [CONTRIBUTOR, READER, Principal("admin", frozenset(), admin=True)])
async def test_direction_decision_requires_current_real_owner(ops, who):
    target, binding, _ = await setup(ops)
    with pytest.raises(OperationError):
        await execute(ops, request(ops, target, binding), who=who)
    assert ops.context["github"].calls == []


@pytest.mark.parametrize("variant", ["draft", "unpublished", "prerelease", "wrong_release", "wrong_repo", "wrong_commit", "tag_cycle", "wrong_branch"])
async def test_unproven_or_mismatched_sources_never_select_baseline(ops, variant):
    target, binding, _ = await setup(ops)
    gh = ops.context["github"]
    if variant == "draft":
        gh.release["draft"] = True
    elif variant == "unpublished":
        gh.release["published_at"] = None
    elif variant == "prerelease":
        gh.release["prerelease"] = True
    elif variant == "wrong_release":
        gh.release["id"] = 42
    elif variant == "wrong_repo":
        gh.repo["id"] = 777
    elif variant == "wrong_commit":
        gh.tag["object"]["sha"] = NEXT
    elif variant == "tag_cycle":
        gh.tag["object"] = {"type": "tag", "sha": TAG_SHA}
    else:
        gh.compare["merge_base_commit"]["sha"] = NEXT
    done = await execute(ops, request(ops, target, binding, branch="support/1.x"), success=False)
    assert done["status"] == "failed", done
    assert baselines.current_baseline(ops, OWNER, target["workspace_id"])["baseline"] is None


async def test_prerelease_is_preserved_and_cannot_be_promoted_by_selected_channel(ops):
    target, binding, _ = await setup(ops)
    ops.context["github"].release["prerelease"] = True
    body = request(ops, target, binding)
    body["params"]["channel"] = "prerelease"
    await execute(ops, body)
    doc = baselines.current_baseline(ops, OWNER, target["workspace_id"])["baseline"]["document"]
    assert doc["channel"] == "prerelease" and doc["proof"]["source"]["prerelease"] is True


@pytest.mark.parametrize("moving", ["tag", "branch", "release"])
async def test_source_moves_during_observation_are_detected(ops, moving):
    target, binding, _ = await setup(ops)
    gh = ops.context["github"]

    async def change(path):
        if moving == "tag" and path.endswith("tags/v1.4.0") and gh.calls.count(path) == 2:
            gh.ref["object"]["sha"] = NEXT
        if moving == "branch" and path.endswith("support%2F1.x") and gh.calls.count(path) == 2:
            gh.branch["object"]["sha"] = SHA
        if moving == "release" and path.endswith("releases/41") and gh.calls.count(path) == 2:
            gh.release["tag_name"] = "v2.0.0"
    gh.hook = change
    done = await execute(ops, request(ops, target, binding, branch="support/1.x"), success=False)
    assert done["error_code"] in {"PM_BASELINE_TAG_CHANGED", "PM_BASELINE_BRANCH_CHANGED"}, done


async def test_workspace_strings_do_not_grant_unbound_or_unconfigured_repository_authority(ops):
    target, binding, pid = await setup(ops)
    other = await workspace(ops)
    body = request(ops, {**target, "workspace_id": other["workspace_id"]}, binding)
    with pytest.raises(OperationError, match="PM_BASELINE_PROJECT_UNAVAILABLE"):
        ops.create(OWNER, **body)
    ops.context["github_config"].repos.clear()
    with pytest.raises(OperationError, match="PM_BASELINE_REPOSITORY_UNAVAILABLE"):
        ops.create(OWNER, **request(ops, target, binding))
    assert ops.context["github"].calls == []


async def test_source_withdrawn_during_get_blocks_next_get_and_record(ops):
    target, binding, pid = await setup(ops)
    gh = ops.context["github"]

    async def withdraw(path):
        if path.endswith("releases/41"):
            await remove_repo(ops, pid)
    gh.hook = withdraw
    done = await execute(ops, request(ops, target, binding), success=False)
    assert done["status"] == "failed" and len(gh.calls) == 2
    assert not baselines.operation_visible(ops.db, OWNER, done)


async def test_recorded_baseline_op_and_history_hide_original_withdrawn_source(ops):
    target, binding, pid = await setup(ops)
    done = await execute(ops, request(ops, target, binding))
    await remove_repo(ops, pid)
    assert not baselines.operation_visible(ops.db, OWNER, done)
    current = baselines.current_baseline(ops, OWNER, target["workspace_id"])
    assert current["baseline"] is None and current["head"] == {"baseline_id": None, "version": 1}
    assert current["visibility_filtered"] is True
    with pytest.raises(OperationError):
        baselines.get_baseline(ops, OWNER, target["workspace_id"], done["result"]["baseline_id"])
    page = baselines.list_baselines(ops, OWNER, target["workspace_id"])
    assert page["baselines"] == [] and page["visibility_filtered"] is True


async def test_selection_cas_and_lost_reply_original_receipt_never_reread_provider(ops):
    target, binding, _ = await setup(ops)
    first, second = request(ops, target, binding), request(ops, target, binding)
    op1, op2 = ops.create(OWNER, **first)[0], ops.create(OWNER, **second)[0]
    await ops._execute(op1["operation_id"])
    await ops._execute(op2["operation_id"])
    assert ops.get(op2["operation_id"])["error_code"] == "PM_BASELINE_VERSION_CONFLICT"
    saved = ops.get(op1["operation_id"])
    count = len(ops.context["github"].calls)
    replay, existing = ops.create(Principal(OWNER.actor, frozenset({"manage"})), **first)
    assert existing and replay["operation_id"] == op1["operation_id"]
    # The worker can recover the exact committed result even after stop and loss
    # of authority, while external read surfaces independently deny the source.
    await decide(ops, target["workspace_id"])
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (target["workspace_id"], OWNER.actor))
    assert await baselines._run(OpContext(ops, saved)) == saved["result"]
    assert not baselines.operation_visible(ops.db, OWNER, saved)
    assert len(ops.context["github"].calls) == count
    assert ops.db.execute("SELECT COUNT(*) FROM pm_release_baselines").fetchone()[0] == 1


async def test_stale_epoch_cannot_finish_new_baseline_after_pause_and_reopen(ops):
    target, binding, _ = await setup(ops)
    gh = ops.context["github"]

    async def pause(path):
        if path == "/repos/example/product" and gh.calls.count(path) == 2:
            await decide(ops, target["workspace_id"])
            await decide(ops, target["workspace_id"], "reopen", params={"stage": "planning"})
    gh.hook = pause
    done = await execute(ops, request(ops, target, binding), success=False)
    assert done["status"] == "failed", done
    assert done["error_code"] == "PM_LIFECYCLE_EPOCH_CHANGED"
    assert ops.db.execute("SELECT COUNT(*) FROM pm_release_baselines").fetchone()[0] == 0


async def test_interrupted_get_reuses_original_intent_only_with_current_authority(ops):
    target, binding, _ = await setup(ops)
    gh = ops.context["github"]

    async def fail_once(path):
        if path.endswith("releases/41"):
            gh.hook = None
            raise GitHubAmbiguous("synthetic read reply lost")
    gh.hook = fail_once
    done = await execute(ops, request(ops, target, binding), success=False)
    assert done["status"] == "uncertain", done
    op = ops.get(done["operation_id"])
    result = await baselines._run(OpContext(ops, op))
    assert result["version"] == 1 and ops.db.execute("SELECT COUNT(*) FROM pm_release_baselines").fetchone()[0] == 1


@pytest.mark.parametrize("field,value", [("channel", []), ("source", {"kind": []}), ("known_limitations", "none"),
    ("maintenance_branch", "../main"), ("maintenance_branch", "main.lock"), ("maintenance_branch", "refs/heads/main\n")])
async def test_malformed_requests_fail_before_provider(ops, field, value):
    target, binding, _ = await setup(ops)
    body = request(ops, target, binding)
    body["params"][field] = value
    with pytest.raises(OperationError):
        ops.create(OWNER, **body)
    assert ops.context["github"].calls == []


async def test_whole_utf8_record_paging_progresses_without_skipping_and_rejects_oversized_detail(ops, monkeypatch):
    target, binding, _ = await setup(ops)
    for _ in range(3):
        body = request(ops, target, binding)
        body["params"]["known_limitations"] = ["發布驗收仍未知" * 250]
        await execute(ops, body)
    wid = target["workspace_id"]
    all_rows = baselines.list_baselines(ops, OWNER, wid)["baselines"]
    one_page = {"workspace_id": wid, "baselines": [all_rows[0]], "next_cursor": all_rows[0]["sequence"], "visibility_filtered": False}
    monkeypatch.setattr(baselines, "MAX_READ_BYTES", baselines._size(one_page) + 100)
    page1 = baselines.list_baselines(ops, OWNER, wid)
    assert len(page1["baselines"]) == 1 and page1["next_cursor"] == all_rows[0]["sequence"]
    page2 = baselines.list_baselines(ops, OWNER, wid, after=page1["next_cursor"])
    assert page2["baselines"][0]["baseline_id"] == all_rows[1]["baseline_id"]
    assert len(json.dumps(page1, ensure_ascii=False).encode()) <= baselines.MAX_READ_BYTES
    monkeypatch.setattr(baselines, "MAX_READ_BYTES", 200)
    with pytest.raises(OperationError) as error:
        baselines.get_baseline(ops, OWNER, wid, all_rows[0]["baseline_id"])
    assert error.value.status == 413
    with pytest.raises(OperationError) as error:
        baselines.list_baselines(ops, OWNER, wid)
    assert error.value.status == 413
    with pytest.raises(OperationError):
        baselines.list_baselines(ops, OWNER, wid, after=2**80)


async def test_config_changes_mid_observation_fail_closed_without_exposing_config(ops):
    target, binding, _ = await setup(ops)
    gh = ops.context["github"]

    async def change(path):
        if path.endswith("releases/41"):
            ops.context["github_config"] = GitHubConfig(api_url="https://other.example/api", repos={"example/product": GitHubRepo("example/product")})
    gh.hook = change
    done = await execute(ops, request(ops, target, binding), success=False)
    assert done["error_code"] == "PM_BASELINE_REPOSITORY_UNAVAILABLE"
    assert len(gh.calls) == 2


def test_configuration_requires_matching_provider_not_only_repo_name():
    cfg = GitHubConfig(repos={"example/product": GitHubRepo("example/product")})
    other = GitHubConfig(api_url="https://different.example/api")
    ops = SimpleNamespace(context={"github_config": cfg, "github": Provider(other)})
    with pytest.raises(OperationError, match="PM_BASELINE_REPOSITORY_UNAVAILABLE"):
        sources.configured(ops, "example/product")
