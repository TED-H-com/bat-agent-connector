"""Fixed PM revision Git facts, distinct from adopted-plan journal pointers."""
from __future__ import annotations

import hashlib
import json
import re

from . import pm_worktree, repository_sync
from .errors import ResourceReadOnly
from .operations import RERUN, Cancelled, OperationError, StepFailed, Uncertain

_REVISION = re.compile(r"pmrev_[0-9a-f]{32}")


def binding_id(plan):
    return repository_sync.digest({"server_id": plan["server_id"], "workspace_id": plan["pm_workspace_id"],
                                   "bootstrap_id": plan["operation_id"], "binding_digest": plan["binding_digest"]})


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
    return await ctx.step("pm.revision.commit",
        lambda: pm_worktree._host_call(ctx, plan, "pm-revision", check=check, revision=fixed),
        request={key: value for key, value in fixed.items() if key != "files"}, reconcile=reconcile)
