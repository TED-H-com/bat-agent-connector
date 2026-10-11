"""PM carrier boundaries with real disk Git and the mock BAT transport."""
from __future__ import annotations

import base64
import copy
import json
import os
import shlex
import zlib
from pathlib import Path

import pytest

from bat_agent_connector import controller_authority, pm_runs, pm_workspace, pm_worktree, repository_sync
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import ActionDef, AmbiguousOutcome, OperationError
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import LocalRunner, bat_writes, git, snapshot
from tests.test_repository_sync import REQUEST
from tests.test_repository_sync import world as repository_world

world = repository_world

OWNER = Principal("pm-owner", frozenset({"observe", "manage"}))


class Runner(LocalRunner):
    def __init__(self):
        super().__init__()
        self.before, self.after, self.phases = {}, {}, []

    async def run(self, host, script, timeout_s=None):
        request = json.loads(zlib.decompress(base64.b64decode(shlex.split(script)[-1])))
        phase = request["phase"]
        self.phases.append(phase)
        if phase in self.before:
            self.before.pop(phase)(request)
        result = await super().run(host, script, timeout_s)
        if phase in self.after:
            self.after.pop(phase)(request)
        return result


@pytest.fixture
async def pm(world):
    w = world
    ops = w["d"].ops
    created, _ = ops.create(OWNER, action="pm.workspace.create", params={"name": "Project", "repositories": ["o/r"]},
                            idempotency_key="pm-workspace")
    await settle_operations(ops)
    wid = ops.get(created["operation_id"])["result"]["workspace"]["workspace_id"]
    pm_runs.schema(ops.journal)
    thread_op, _ = ops.create(OWNER, action="pm.thread.create", target={"workspace_id": wid},
                              params={"title": "Idea"}, preconditions={"expected_version": 1}, idempotency_key="thread")
    await settle_operations(ops)
    thread_id = ops.get(thread_op["operation_id"])["result"]["thread_id"]
    authority = {**pm_runs.execution_identity(ops.db, wid), "server_id": pm_workspace.server_id(ops.db),
                 "scopes": ["observe", "manage", "start"], "admin": False, "pm_workspace_id": wid}
    runner = Runner()
    ops.context["git_runner"] = runner
    pv = await repository_sync.source_preview(ops, REQUEST)
    selection = {**pv["target"], **{key: pv[key] for key in ("source_ref", "source_sha", "repository_id", "binding_digest")}}
    w.update(workspace_id=wid, authority=authority, runner=runner, selection=selection, plan=None, current=True)

    async def check():
        run = w["active_run"]
        pm_runs.require_writer(ops.journal, run["run_id"], run["generation"])
        if not w["current"]:
            raise OperationError("PM_WRITER_CHANGED", "The original writer changed", 409)

    async def run(ctx):
        execution = {**authority, "pm_operation_id": ctx.operation_id,
                     "pm_requester": pm_workspace.principal_binding(OWNER)}
        record = pm_runs.create(ops, OWNER, operation_id=ctx.operation_id, workspace_id=wid, thread_id=thread_id,
                               expected_version=1, adapter="manual", purpose="Compare options", execution_authority=execution)
        w["active_run"] = record
        with controller_authority.use(execution, ops.journal):
            plan = (ops.get(ctx.operation_id).get("external_refs") or {}).get("pm_worktree_plan") or w["plan"]
            if plan is None:
                plan = await pm_worktree.select(ops, wid, execution, w["selection"], operation_id=ctx.operation_id)
            # Original plan is durable before the first host effect.
            pm_runs.bind_worktree(ops.journal, record["run_id"], record["generation"], plan)
            ctx.set_refs(pm_worktree_plan=plan)
            w["plan"] = plan
            carrier = await pm_worktree.ensure(ctx, plan, check=check)
            result = {"carrier": carrier}
            if ctx.params.get("snapshot", True):
                result["snapshot"] = await pm_worktree.write_snapshot(ctx, plan,
                    ctx.params.get("run_id", record["run_id"]), "context",
                    ctx.params.get("payload", {"thread_id": thread_id, "known": "Evidence", "assumptions": ["Candidate"]}),
                    check=check)
                if "revision" in ctx.params:
                    result["revision"] = await pm_worktree.commit_revision(ctx, plan, ctx.params["revision"],
                        expected_parent=ctx.params.get("expected_parent", plan["source_sha"]), check=check)
                pm_runs.complete_preparation(ops.journal, record["run_id"], record["generation"],
                                             worktree_binding=plan, snapshot=result["snapshot"])
            else:
                pm_runs.fail(ops.journal, record["run_id"], record["generation"], "Fixture only inspects bootstrap", before_send=True)
            return result

    ops.register(ActionDef("pm.fixture.prepare", "manage", "PM helper fixture", run))
    w["check"] = check
    yield w


async def submit(w, *, key=None, **params):
    ops = w["d"].ops
    op, _ = ops.create(OWNER, action="pm.fixture.prepare", target={"workspace_id": w["workspace_id"]},
                       params=params, idempotency_key=key or "pm-" + str(len(ops.list()["operations"])))
    await settle_operations(ops)
    return ops.get(op["operation_id"])


async def resume(w, op):
    ops = w["d"].ops
    ops.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
    await settle_operations(ops)
    return ops.get(op["operation_id"])


async def test_fixed_pm_carrier_and_draft_leave_human_source_untouched(pm, mock):
    before = snapshot(pm["human"])
    done = await submit(pm)
    assert done["status"] == "succeeded", (done["error_code"], done["status_reason"], done["steps"])
    plan = pm["plan"]
    draft = done["result"]["snapshot"]
    content = json.loads(Path(plan["worktree_path"], draft["path"]).read_text())
    assert git(plan["worktree_path"], "rev-parse", "HEAD") == pm["sha"]
    assert content["payload"]["assumptions"] == ["Candidate"]
    assert content["status"] == "draft" and content["source_sha"] == pm["sha"]
    assert content["controller_id"] != pm_workspace.controller_for(pm["d"].ops.db, OWNER)
    assert content["workspace_id"] == pm["workspace_id"]
    assert snapshot(pm["human"]) == before and not bat_writes(mock)
    assert all(method == "GET" for method, _, _ in pm["gh"].requests)
    count = len(pm["runner"].phases)
    replay = await submit(pm, key=done["idempotency_key"])
    assert replay["operation_id"] == done["operation_id"] and len(pm["runner"].phases) == count


async def test_next_run_reuses_original_worktree_when_published_head_moves(pm):
    first = await submit(pm)
    assert first["status"] == "succeeded", first
    original = copy.deepcopy(pm["plan"])
    pm["state"]["sha"] = "f" * 40
    second = await submit(pm)
    assert second["status"] == "succeeded", second
    assert second["result"]["carrier"] == first["result"]["carrier"]
    assert pm["plan"] == original and pm["runner"].phases.count("fetch") == 1
    assert len(list(Path(original["worktree_path"]).glob("docs/pm/drafts/*/context.json"))) == 2


@pytest.mark.parametrize("phase", ["fetch", "prepare", "pm-snapshot"])
async def test_lost_reply_reads_original_receipt_without_resending(pm, phase):
    def lose(_):
        pm["state"]["sha"] = "b" * 40
        raise AmbiguousOutcome("lost reply")
    pm["runner"].after[phase] = lose
    first = await submit(pm)
    assert first["status"] == "uncertain", first
    pm["plan"] = None  # recovery comes from the original operation's journal refs
    recovered = await resume(pm, first)
    assert recovered["status"] == "succeeded", recovered
    assert pm["runner"].phases.count(phase) == 1


@pytest.mark.parametrize("change,code", [("repository", "REPOSITORY_NOT_BOUND"), ("sha", "REPOSITORY_REF_CHANGED"),
                                          ("repository_id", "REPOSITORY_BINDING_CHANGED"), ("digest", "REPOSITORY_BINDING_CHANGED")])
async def test_workspace_strings_never_grant_repository_or_path_authority(pm, change, code):
    if change == "repository":
        pm["selection"]["repository"] = str(pm["human"])
    elif change == "sha":
        pm["selection"]["source_sha"] = "a" * 40
    elif change == "repository_id":
        pm["selection"]["repository_id"] += 1
    else:
        pm["selection"]["binding_digest"] = "0" * 64
    done = await submit(pm)
    assert done["status"] == "failed" and done["error_code"] == code, done
    assert not pm["runner"].phases and not list(pm["managed"].iterdir())


@pytest.mark.parametrize("kind", ["directory", "symlink"])
async def test_preexisting_clone_is_not_adopted(pm, kind):
    def plant(request):
        dest = Path(request["clone_path"])
        if kind == "directory":
            dest.mkdir()
            (dest / "keep.txt").write_text("not ours")
        else:
            dest.symlink_to(pm["human"], target_is_directory=True)
    before = snapshot(pm["human"])
    pm["runner"].before["fetch"] = plant
    done = await submit(pm)
    assert done["status"] == "needs_attention", done
    assert snapshot(pm["human"]) == before
    dest = Path(pm["plan"]["clone_path"])
    if kind == "directory":
        assert (dest / "keep.txt").read_text() == "not ours" and not (dest / ".git").exists()
    else:
        assert dest.is_symlink()


@pytest.mark.parametrize("change", ["member", "writer", "workspace", "config"])
async def test_final_snapshot_guard_rejects_changes_before_file_effect(pm, mock, change):
    def drift(_):
        if change == "member":
            pm["d"].ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (pm["workspace_id"], OWNER.actor))
        elif change == "writer":
            pm["current"] = False
        elif change == "workspace":
            mock.ws_doc["workspaces"][0]["folderPath"] += "-changed"
        else:
            pm["d"].fleet.config.hosts["h1"].default_permission_mode = "different"
    pm["runner"].before["pm-snapshot"] = drift
    done = await submit(pm)
    assert done["status"] in {"failed", "needs_attention"}, done
    root = Path(pm["plan"]["worktree_path"])
    assert not list(root.glob("docs/pm/drafts/*/context.json"))
    assert not Path(pm["plan"]["clone_path"], ".git", "batc-pm-drafts.json").exists()


@pytest.mark.parametrize("change", ["tracked", "untracked", "snapshot", "manifest", "hardlink"])
async def test_foreign_or_tampered_files_block_further_writes(pm, change):
    first = await submit(pm)
    assert first["status"] == "succeeded", first
    root = Path(pm["plan"]["worktree_path"])
    saved = root / first["result"]["snapshot"]["path"]
    if change == "tracked":
        (root / "hello.txt").write_text("unexpected edit")
    elif change == "untracked":
        (root / "foreign.txt").write_text("unknown")
    elif change == "snapshot":
        saved.write_text("changed")
    elif change == "manifest":
        Path(pm["plan"]["clone_path"], ".git", "batc-pm-drafts.json").write_text("{}")
    else:
        os.link(saved, pm["managed"] / "outside-link.json")
    second = await submit(pm)
    assert second["status"] == "needs_attention", second
    assert len(list(root.glob("docs/pm/drafts/*/context.json"))) == 1


async def test_snapshot_parent_symlink_is_not_followed(pm):
    first = await submit(pm, snapshot=False)
    assert first["status"] == "succeeded", first
    root = Path(pm["plan"]["worktree_path"])
    (root / "docs").symlink_to(pm["human"], target_is_directory=True)
    before = snapshot(pm["human"])
    second = await submit(pm)
    assert second["status"] == "needs_attention", second
    assert snapshot(pm["human"]) == before and not (pm["human"] / "pm").exists()


async def test_same_run_snapshot_cannot_change_payload(pm):
    run_id = "pmr_" + "3" * 32
    first = await submit(pm, run_id=run_id, payload={"known": "original"})
    assert first["status"] == "succeeded", first
    path = Path(pm["plan"]["worktree_path"], first["result"]["snapshot"]["path"])
    before = path.read_bytes()
    second = await submit(pm, run_id=run_id, payload={"known": "changed"})
    assert second["status"] == "needs_attention" and second["error_code"] == "PM_SNAPSHOT_CONFLICT", second
    assert path.read_bytes() == before


async def test_existing_plan_and_openspec_stay_in_original_locations(pm):
    # Republish a fixture revision with ordinary project docs before selecting it.
    human = pm["human"]
    (human / "docs" / "pm").mkdir(parents=True)
    (human / "docs" / "pm" / "plan.md").write_text("Accepted project plan\n")
    (human / "openspec" / "specs" / "account").mkdir(parents=True)
    (human / "openspec" / "specs" / "account" / "spec.md").write_text("Existing spec\n")
    git(human, "add", "docs", "openspec")
    git(human, "commit", "-qm", "project docs")
    sha = git(human, "rev-parse", "HEAD")
    git(human, "push", str(pm["remote"]), "main")
    pm["state"]["sha"] = sha
    pv = await repository_sync.source_preview(pm["d"].ops, REQUEST)
    pm["selection"].update({key: pv[key] for key in ("source_sha", "repository_id", "binding_digest")})
    done = await submit(pm)
    assert done["status"] == "succeeded", (done["error_code"], done["status_reason"], done["steps"])
    root = Path(pm["plan"]["worktree_path"])
    assert (root / "docs/pm/plan.md").read_text() == "Accepted project plan\n"
    assert (root / "openspec/specs/account/spec.md").read_text() == "Existing spec\n"
    assert not (root / "docs/pm/specs").exists()
    assert not git(root, "diff", "HEAD")


@pytest.mark.parametrize("run_id,kind,payload", [("../escape", "context", {}), ("pmr_" + "1" * 32, "plan", {}),
    ("pmr_" + "1" * 32, "context", {"bad": float("nan")}), ("pmr_" + "1" * 32, "context", {"too_large": "x" * 512_000})])
async def test_invalid_snapshot_never_reaches_host(pm, run_id, kind, payload):
    first = await submit(pm, snapshot=False)
    assert first["status"] == "succeeded", first
    with pytest.raises(OperationError):
        pm_worktree.snapshot_document(pm["plan"], run_id, kind, payload, operation_id=first["operation_id"])


async def test_original_internal_controller_is_required(pm):
    first = await submit(pm, snapshot=False)
    assert first["status"] == "succeeded", first
    from bat_agent_connector.operations import OpContext
    ctx = OpContext(pm["d"].ops, first)
    with controller_authority.use(controller_authority.binding(pm["d"].ops.db, OWNER), pm["d"].journal):
        with pytest.raises(Exception, match="another controller"):
            await pm_worktree.write_snapshot(ctx, pm["plan"], "pmr_" + "f" * 32, "reply", {"reply": "text"}, check=pm["check"])
    assert not list(Path(pm["plan"]["worktree_path"]).glob("docs/pm/drafts/*/reply.json"))


async def test_capabilities_distinguish_configured_files_from_runtime(pm):
    rows = pm_worktree.capabilities(pm["d"].ops)
    assert rows == [{"repository": "o/r", "host": "h1", "workspace_id": "ws-1", "available": True,
                     "fixed_revision": True, "runtime_writes": False, "service_draft_snapshots": True}]
    pm["d"].ops.context["git_runner"] = None
    assert pm_worktree.capabilities(pm["d"].ops)[0]["available"] is False


@pytest.mark.parametrize("obstacle", ["tracked_draft", "tracked_symlink"])
async def test_source_revision_cannot_supply_a_writable_snapshot_destination(pm, obstacle):
    human = pm["human"]
    run_id = "pmr_" + "8" * 32
    if obstacle == "tracked_draft":
        path = human / "docs" / "pm" / "drafts" / run_id
        path.mkdir(parents=True)
        (path / "context.json").write_text("Existing repository content\n")
    else:
        (human / "docs").symlink_to(pm["managed"], target_is_directory=True)
    git(human, "add", "docs")
    git(human, "commit", "-qm", "existing paths")
    git(human, "push", str(pm["remote"]), "main")
    pm["state"]["sha"] = git(human, "rev-parse", "HEAD")
    pv = await repository_sync.source_preview(pm["d"].ops, REQUEST)
    pm["selection"].update({key: pv[key] for key in ("source_sha", "repository_id", "binding_digest")})
    before = snapshot(human)
    done = await submit(pm, run_id=run_id)
    assert done["status"] == "needs_attention", done
    assert snapshot(human) == before and not (pm["managed"] / "pm").exists()
    assert not Path(pm["plan"]["clone_path"], ".git", "batc-pm-drafts.json").exists()
    if obstacle == "tracked_draft":
        assert Path(pm["plan"]["worktree_path"], "docs/pm/drafts", run_id, "context.json").read_text() == "Existing repository content\n"
