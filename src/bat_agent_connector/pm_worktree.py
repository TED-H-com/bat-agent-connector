"""PM-owned, fixed-revision carriers and immutable draft snapshots.

The caller persists ``select``'s plan before ``ensure`` and holds the journal's
workspace writer generation throughout every effect. These helpers never grant
membership, adopt workspace repository strings, or launch a BAT session.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import uuid
import zlib
from importlib import resources

from . import checkpoints, controller_authority, native_carrier, pm_workspace, repository_sync
from .operations import AmbiguousOutcome, NeedsAttention, OperationError, StepFailed

_RUN = re.compile(r"pmr_[0-9a-f]{32}")
_OPERATION = re.compile(r"op_[0-9a-f]{32}")
MAX_SNAPSHOT_BYTES = 512_000
_SELECTION = {"repository", "host", "workspace_id", "source_ref", "source_sha", "repository_id", "binding_digest"}


def capabilities(ops) -> list[dict]:
    """Configuration availability is not runtime or sandbox verification."""
    result = []
    for target in repository_sync.capabilities(ops):
        target = {key: target[key] for key in ("repository", "host", "workspace_id")}
        try:
            repository_sync.configured(ops, target)
        except OperationError as exc:
            result.append({**target, "available": False, "reason": exc.code})
        else:
            result.append({**target, "available": True, "fixed_revision": True,
                           "runtime_writes": False, "service_draft_snapshots": True})
    return result


def validate_selection(ops, selection: dict) -> dict:
    if not isinstance(selection, dict) or set(selection) != _SELECTION:
        raise OperationError("INVALID_PARAMS", "An exact configured repository preview is required", 422)
    target = {key: selection[key] for key in ("repository", "host", "workspace_id")}
    repository_sync.configured(ops, target)
    repository_sync.check_ref(selection["source_ref"])
    if (not isinstance(selection["source_sha"], str) or not repository_sync.SHA.fullmatch(selection["source_sha"])
            or type(selection["repository_id"]) is not int or selection["repository_id"] <= 0
            or not isinstance(selection["binding_digest"], str)
            or not repository_sync.DIGEST.fullmatch(selection["binding_digest"])):
        raise OperationError("INVALID_PARAMS", "Full source SHA, repository ID and binding digest are required", 422)
    return target


async def select(ops, workspace_id: str, execution_authority: dict, selection: dict, *, operation_id: str) -> dict:
    """Freeze first binding; subsequent runs reuse the persisted plan unchanged.

    Membership/intent admission belongs to the registered PM operation. The
    selection comes from the existing repository preview, not a workspace label.
    ``operation_id`` is the actual original PM creation operation, not a client ID.
    """
    target = validate_selection(ops, selection)
    workspace = pm_workspace._workspace(ops.db, workspace_id)
    authority = controller_authority._validated(execution_authority)
    if (authority["server_id"] != workspace["server_id"] or authority.get("pm_workspace_id") != workspace_id
            or authority["actor"] != "pm:" + workspace_id
            or not isinstance(operation_id, str) or not _OPERATION.fullmatch(operation_id)):
        raise OperationError("PM_WORKTREE_AUTHORITY_INVALID", "PM execution authority and original operation are required", 403)
    preview = await repository_sync.source_preview(ops, {**target, "source_ref": selection["source_ref"]})
    if preview["source_sha"] != selection["source_sha"]:
        raise OperationError("REPOSITORY_REF_CHANGED", "The selected published revision changed", 409)
    if any(preview[key] != selection[key] for key in ("repository_id", "binding_digest")):
        raise OperationError("REPOSITORY_BINDING_CHANGED", "The selected repository binding changed", 409)
    config = repository_sync.config_snapshot(ops, target)
    bootstrap_id = "op_" + uuid.uuid5(uuid.UUID(workspace["server_id"]), "pm-worktree:" + workspace_id).hex
    suffix = bootstrap_id[3:15]
    clone = config["managed_root"] + "/batc-published-" + bootstrap_id[3:]
    plan = {**preview, "pm_workspace_id": workspace_id, "server_id": workspace["server_id"],
            "execution_authority": authority, "creation_operation_id": operation_id,
            "operation_id": bootstrap_id, "config": config, "config_digest": repository_sync.digest(config),
            "clone_path": clone, "worktree_path": clone + "/.bat-worktrees/batc-published-" + suffix,
            "branch": "batc/published-" + suffix,
            "markers": {"managed-clone": "true", "role": "pm", "repository": config["repository"],
                        "repository-id": str(preview["repository_id"]), "host": target["host"],
                        "remote-url": config["remote_url"], "operation": bootstrap_id,
                        "binding": preview["binding_digest"], "pm-workspace": workspace_id,
                        "pm-server": workspace["server_id"], "pm-controller": authority["controller_id"],
                        "pm-creation-operation": operation_id}}
    # Configuration dataclasses include tuple bindings; persist exactly the JSON
    # form so a recovered plan compares identically with the original value.
    return json.loads(json.dumps(plan))


async def _guard(ctx, plan, check):
    controller_authority.require_binding(plan["execution_authority"])
    ctx.check_cancel()
    await check()
    if repository_sync.digest(repository_sync.config_snapshot(ctx.service, plan["target"])) != plan["config_digest"]:
        raise StepFailed("REPOSITORY_BINDING_CHANGED", "The fixed repository configuration changed")
    observed = await repository_sync.workspace(ctx.service, plan["target"])
    if observed["folder"] != plan["workspace"]["folder"]:
        raise StepFailed("REPOSITORY_WORKSPACE_CHANGED", "The fixed BAT workspace changed")
    controller_authority.require_binding(plan["execution_authority"])
    ctx.check_cancel()
    await check()


def _host_source() -> str:
    # Reuse the audited Git runner's sanitized environment, markers and inode locks.
    shared = (resources.files(__package__) / "repository_sync_host.py").read_text()
    shared = shared.split('\nif __name__ == "__main__":', 1)[0]
    drafts = (resources.files(__package__) / "pm_worktree_host.py").read_text().split('\nif __name__ == "__main__":', 1)[0]
    paths = (resources.files(__package__) / "pm_revision_paths.py").read_text()
    return shared + "\n" + drafts + "\n" + paths + "\n" + (resources.files(__package__) / "pm_revision_host.py").read_text()


async def _host_call(ctx, plan, phase, *, check, snapshot=None, revision=None):
    request = {**plan, "phase": phase, "active_operation_id": ctx.operation_id, **{key: plan["config"][key] for key in ("managed_root", "remote_url")}}
    if snapshot is not None:
        request["snapshot"] = snapshot
    if revision is not None:
        request["revision"] = revision
    arg = base64.b64encode(zlib.compress(json.dumps(request).encode(), level=9)).decode()
    script = "python3 -c " + shlex.quote(_host_source()) + " " + shlex.quote(arg)
    if len(script.encode()) > 120_000:
        raise OperationError("PM_SNAPSHOT_TOO_LARGE", "The bounded snapshot exceeds the host transport limit", 422)
    async def locked_check():
        await _guard(ctx, plan, check)
        if phase == "fetch":
            await repository_sync.published(ctx.service, plan["config"]["repository"], plan["source_ref"],
                                            expected_id=plan["repository_id"], expected_sha=plan["source_sha"])
    reading = phase.startswith("read.")
    if not reading:
        await _guard(ctx, plan, check)
    token = checkpoints._LOCKED_CHECK.set(None if reading else locked_check)
    try:
        raw = await ctx.service.context["git_runner"].run(plan["target"]["host"], script,
                                                        timeout_s=plan["config"]["sync"]["fetch_timeout_s"])
        result = json.loads(raw)
    finally:
        checkpoints._LOCKED_CHECK.reset(token)
    if "error" in result:
        if result["error"] in {"REPOSITORY_HOST_UNCERTAIN", "PM_HOST_UNCERTAIN"}:
            raise AmbiguousOutcome("PM carrier host effect is unproven")
        raise NeedsAttention(result["error"], "The PM-owned carrier requires review")
    if not isinstance(result.get("result"), dict):
        raise AmbiguousOutcome("PM carrier host returned no result")
    return result["result"]


async def ensure(ctx, plan: dict, *, check) -> dict:
    """Prepare/recover the original carrier using existing operation step receipts.

    ``check`` is an async final guard for the current writer generation and
    requesting member. A lost Git response is read back; never blindly repeated.
    """
    return await native_carrier.prepare_pm(ctx, plan, lambda phase: _host_call(ctx, plan, phase, check=check))


def snapshot_document(plan: dict, run_id: str, kind: str, payload: dict, *, operation_id: str) -> dict:
    """Immutable draft only: no adopted plan, spec changes or execution authority."""
    if (not isinstance(run_id, str) or not _RUN.fullmatch(run_id) or kind not in {"context", "reply"}
            or not isinstance(payload, dict) or not _OPERATION.fullmatch(operation_id)):
        raise OperationError("INVALID_PARAMS", "A PM run and context/reply payload are required", 422)
    document = {"schema": "batc.pm.draft.v1", "status": "draft", "kind": kind, "run_id": run_id,
                "workspace_id": plan["pm_workspace_id"], "server_id": plan["server_id"],
                "operation_id": operation_id, "controller_id": plan["execution_authority"]["controller_id"],
                "repository_id": plan["repository_id"], "source_sha": plan["source_sha"],
                "binding_digest": plan["binding_digest"], "payload_digest": repository_sync.digest(payload), "payload": payload}
    try:
        content = (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    except (UnicodeEncodeError, ValueError, TypeError):
        raise OperationError("INVALID_PARAMS", "Snapshot must contain valid bounded JSON", 422) from None
    if len(content) > MAX_SNAPSHOT_BYTES:
        raise OperationError("PM_SNAPSHOT_TOO_LARGE", "Snapshot exceeds the bounded document size", 422)
    return {"path": "docs/pm/drafts/" + run_id + "/" + kind + ".json", "kind": kind,
            "run_id": run_id, "operation_id": operation_id, "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content), "payload_digest": document["payload_digest"],
            "content": base64.b64encode(content).decode()}


async def write_snapshot(ctx, plan: dict, run_id: str, kind: str, payload: dict, *, check) -> dict:
    snapshot = snapshot_document(plan, run_id, kind, payload, operation_id=ctx.operation_id)
    async def read(_=None):
        return (await _host_call(ctx, plan, "read.pm-snapshot", check=check, snapshot=snapshot)).get("snapshot")
    return await ctx.step("pm.snapshot." + kind,
        lambda: _host_call(ctx, plan, "pm-snapshot", check=check, snapshot=snapshot),
        request={key: value for key, value in snapshot.items() if key != "content"}, reconcile=read)


async def commit_revision(ctx, plan, revision_files, *, expected_parent, check):
    from .pm_revision_git import commit_revision as commit
    return await commit(ctx, plan, revision_files, expected_parent=expected_parent, check=check)
