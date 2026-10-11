"""Fixed PM revision Git facts, distinct from adopted-plan journal pointers."""
from __future__ import annotations

import hashlib
import json
import re

from . import pm_worktree, repository_sync
from .errors import ResourceReadOnly
from .operations import RERUN, Cancelled, NeedsAttention, OpContext, OperationError, StepFailed, Uncertain

_REVISION = re.compile(r"pmrev_[0-9a-f]{32}")


def binding_id(plan):
    return repository_sync.digest({"server_id": plan["server_id"], "workspace_id": plan["pm_workspace_id"],
                                   "bootstrap_id": plan["operation_id"], "binding_digest": plan["binding_digest"]})


def step_receipt(value):
    """Operation reconciliation metadata is not part of the exact Git fact."""
    return {key: item for key, item in value.items() if key != "reconciled"}


def request(ctx, plan, revision, expected_parent):
    if (not isinstance(revision, dict) or set(revision) != {"workspace_id", "revision_id", "files", "file_digests", "document_digest"}
            or revision["workspace_id"] != plan["pm_workspace_id"]
            or not isinstance(revision["revision_id"], str) or not _REVISION.fullmatch(revision["revision_id"])
            or not isinstance(expected_parent, str) or not repository_sync.SHA.fullmatch(expected_parent)):
        raise OperationError("PM_REVISION_INVALID", "Fixed revision files and parent commit are required", 422)
    prefix = "docs/pm/revisions/" + revision["revision_id"] + "/"
    if not isinstance(revision["files"], dict) or set(revision["files"]) != {prefix + "plan.json", prefix + "plan.md"}:
        raise OperationError("PM_REVISION_INVALID", "Only the revision's fixed plan files can be committed", 422)
    try:
        encoded = {path: value.encode("utf-8") for path, value in revision["files"].items()}
    except (AttributeError, UnicodeEncodeError):
        raise OperationError("PM_REVISION_INVALID", "Revision files must contain UTF-8 text", 422) from None
    digests = {path: hashlib.sha256(value).hexdigest() for path, value in encoded.items()}
    digest = hashlib.sha256(json.dumps(digests, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if (sum(map(len, encoded.values())) > pm_worktree.MAX_SNAPSHOT_BYTES or digests != revision["file_digests"]
            or digest != revision["document_digest"]):
        raise OperationError("PM_REVISION_INVALID", "Revision bytes do not match their bounded document digest", 422)
    return {**revision, "operation_id": ctx.operation_id, "expected_parent": expected_parent,
            "timestamp": int(ctx.op["created_at"]), "binding_id": binding_id(plan)}


async def commit_revision(ctx, plan, revision_files, *, expected_parent, check):
    fixed = request(ctx, plan, revision_files, expected_parent)
    async def reconcile(_):
        value = await pm_worktree._host_call(ctx, plan, "read.pm-revision", check=check, revision=fixed)
        if value.get("receipt"):
            return value["receipt"]
        if not value.get("resume_safe"):
            return None
        if value.get("effect_started") is False:
            return RERUN  # Original host intent is absent: no effect to preserve.
        if value.get("effect_started") is not True:
            return None
        # A partial original transaction is not a zero-effect retry. Continue
        # only its exact deterministic intent under the original writer and
        # current gates, leaving the original step unresolved if stopped.
        try:
            return await pm_worktree._host_call(ctx, plan, "pm-revision", check=check, revision=fixed)
        except (Cancelled, OperationError, StepFailed, ResourceReadOnly):
            raise Uncertain("pm.revision.commit", "The original partial Git transaction still needs reconciliation") from None
    result = await ctx.step("pm.revision.commit",
        lambda: pm_worktree._host_call(ctx, plan, "pm-revision", check=check, revision=fixed),
        request={key: value for key, value in fixed.items() if key != "files"}, reconcile=reconcile)
    return step_receipt(result)


async def verify_revision(ctx, plan, revision_files, *, source_operation_id, check):
    """Read an original journal-backed commit for a new explicit adoption."""
    # This operation has no Git mutation. Cancelling its read can release its
    # writer without changing or concealing the original creation receipt.
    ctx.check_cancel()
    db = ctx.service.db
    saved = db.execute("SELECT receipt FROM pm_plan_git_receipts WHERE operation_id=?", (source_operation_id,)).fetchone()
    source = json.loads(saved[0]) if saved else None
    step = db.execute("SELECT request,status FROM operation_steps WHERE operation_id=? AND name='pm.revision.commit'",
                      (source_operation_id,)).fetchone()
    if (not source or "source_operation_id" in source or not step or step["status"] != "succeeded"
            or source["workspace_id"] != plan["pm_workspace_id"] or source["revision_id"] != revision_files["revision_id"]
            or source["binding_id"] != binding_id(plan)):
        raise OperationError("PM_GIT_SOURCE_INVALID", "An original verified Git creation operation is required", 409)
    original_request = json.loads(step["request"])
    original = OpContext(ctx.service, ctx.service.get(source_operation_id, steps=False))
    fixed = request(original, plan, revision_files, original_request["expected_parent"])
    if {key: value for key, value in fixed.items() if key != "files"} != original_request:
        raise OperationError("PM_GIT_SOURCE_INVALID", "Original Git intent does not match the fixed revision", 409)

    async def read(_=None):
        observed = await pm_worktree._host_call(ctx, plan, "read.pm-revision", check=check, revision=fixed)
        if observed.get("receipt") != source:
            raise NeedsAttention("PM_GIT_SOURCE_UNPROVEN", "The original commit and current owned Git chain could not be verified")
        return {**source, "operation_id": ctx.operation_id, "source_operation_id": source_operation_id}

    async def initial():
        await pm_worktree._guard(ctx, plan, check)
        return await read()

    result = await ctx.step("pm.revision.verify", initial,
        request={"source_operation_id": source_operation_id, "commit_sha": source["commit_sha"],
                 "document_digest": source["document_digest"], "binding_id": source["binding_id"]}, reconcile=read)
    return step_receipt(result)
