"""W36: current-authority reads of fixed OpenSpec artifacts from an existing PM clone.

No new journal, filesystem permission, writer claim, runtime, CLI, fetch or mutation.
The first carrier supports only its already pinned original code source commit.
"""
from __future__ import annotations

import base64
import hashlib
import json
import shlex
import zlib
from importlib import resources

from . import pm_discussions, pm_runs, repository_sync
from .operations import OperationError
from .pm_spec_source_format import (
    ADAPTER_VERSION,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    MAX_WIRE_BYTES,
    UPSTREAM_REVISION,
    describe_path,
    markdown_observations,
    schema_declaration,
)


def _bad(code, message, status=409):
    return OperationError(code, message, status)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _binding(ops, principal, workspace_id):
    workspace = pm_discussions._read(ops, principal, workspace_id)
    binding = pm_runs.workspace_worktree(ops.db, workspace_id)
    if binding is None:
        raise _bad("PM_SPEC_BINDING_UNAVAILABLE", "Prepare an explicitly bound PM source carrier before reading Git artifacts")
    if binding.get("pm_workspace_id") != workspace_id or binding.get("server_id") != workspace["server_id"]:
        raise _bad("PM_SPEC_BINDING_CHANGED", "The recorded PM source identity does not match this workspace")
    current = repository_sync.config_snapshot(ops, binding["target"])
    if repository_sync.digest(current) != binding["config_digest"]:
        raise _bad("REPOSITORY_BINDING_CHANGED", "The configured repository binding changed")
    return binding


def capabilities(ops, principal, workspace_id):
    """Cheap eligibility only; does not start a process, inspect an installed CLI or read Git."""
    pm_discussions._read(ops, principal, workspace_id)
    result = {"adapter": ADAPTER_VERSION, "version": 1, "workspace_id": workspace_id,
              "source": "existing_pm_pinned_git", "source_commit_only": True, "read_only": True,
              "git_verified": False, "cli_required": False, "cli_version": None,
              "cli_json_supported": False, "custom_schema_execution": False, "execution_authorized": False}
    try:
        binding = _binding(ops, principal, workspace_id)
    except OperationError as error:
        return {**result, "eligible": False, "reason": error.code}
    return {**result, "eligible": True, "repository": binding["target"]["repository"],
            "commit_sha": binding["source_sha"], "reason": "git_read_required"}


def _host_source():
    files = resources.files(__package__)
    shared = (files / "repository_sync_host.py").read_text().split('\nif __name__ == "__main__":', 1)[0]
    # These audited modules contain only fixed Git argv and bounded format readers.
    format_source = (files / "pm_spec_source_format.py").read_text().replace("from __future__ import annotations\n", "")
    return shared + "\n" + format_source + "\n" + (files / "pm_spec_sources_host.py").read_text()


async def _guard(ops, principal, workspace_id, binding):
    if _binding(ops, principal, workspace_id) != binding:
        raise _bad("PM_SPEC_BINDING_CHANGED", "Read the original PM repository binding again")
    observed = await repository_sync.workspace(ops, binding["target"])
    pm_discussions._read(ops, principal, workspace_id)
    if observed["folder"] != binding["workspace"]["folder"]:
        raise _bad("REPOSITORY_WORKSPACE_CHANGED", "The exact bound BAT workspace changed")
    if _binding(ops, principal, workspace_id) != binding:
        raise _bad("PM_SPEC_BINDING_CHANGED", "The PM repository binding changed during the read")


def _document(binding, commit_sha, row):
    descriptor = describe_path(row.get("path"))
    if descriptor is None:
        raise _bad("PM_SPEC_READ_INVALID", "The Git reader returned an unsupported artifact path")
    identity = {"workspace_id": binding["pm_workspace_id"], "server_id": binding["server_id"],
                "repository": binding["target"]["repository"], "repository_id": binding["repository_id"],
                "binding_digest": binding["binding_digest"], "path": row["path"]}
    blob = row.get("blob_sha")
    if blob is not None and (not isinstance(blob, str) or not repository_sync.SHA.fullmatch(blob)):
        raise _bad("PM_SPEC_READ_INVALID", "The Git reader returned an invalid object identity")
    result = {**descriptor, "file_id": "pmsfile_" + _digest(identity),
              "source_id": "pmssrc_" + _digest({**identity, "commit_sha": commit_sha, "blob_sha": blob}),
              "path": row["path"], "commit_sha": commit_sha, "blob_sha": blob, "sha256": None,
              "size": row.get("size"), "status": "unavailable", "content": None,
              "source_status": "repository_document", "accepted_scope": "unknown", "validation": "not_run",
              "implementation_verified": False, "merged": "unknown", "deployed": "unknown", "counts_as_done": False}
    if "reason" in row:
        if row["reason"] not in {"missing_at_commit", "symlink_or_nonregular_blob", "document_too_large", "selection_byte_budget"}:
            raise _bad("PM_SPEC_READ_INVALID", "The Git reader returned an invalid unavailable reason")
        return {**result, "reason": row["reason"]}
    if (type(row.get("size")) is not int or not 0 <= row["size"] <= MAX_FILE_BYTES
            or not isinstance(row.get("content"), str) or len(row["content"]) > MAX_FILE_BYTES * 2
            or row.get("kind") != "blob" or row.get("mode") not in {"100644", "100755"}):
        raise _bad("PM_SPEC_READ_INVALID", "The Git reader returned invalid artifact bytes")
    try:
        raw = base64.b64decode(row["content"], validate=True)
    except ValueError:
        raise _bad("PM_SPEC_READ_INVALID", "The Git reader returned malformed artifact bytes") from None
    if (len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != row.get("sha256")
            or hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != blob):  # noqa: S324 - Git object identity
        raise _bad("PM_SPEC_READ_INVALID", "The Git reader returned inconsistent artifact digests")
    result["sha256"] = row["sha256"]
    try:
        content = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return {**result, "reason": "non_utf8_document"}
    if "\0" in content:
        return {**result, "reason": "binary_document"}
    result.update(status="read", content=content, reason=None)
    if descriptor["role"].endswith("metadata"):
        result["schema_declaration"] = schema_declaration(content)
    else:
        result["observations"] = markdown_observations(content, tasks=descriptor["role"] == "tasks")
        for report in result["observations"]["task_reports"]:
            report["locator"] = {"source_id": result["source_id"], "line": report["line"], "identity_scope": "fixed_artifact_revision"}
    return result


async def read_sources(ops, principal, workspace_id, *, repository, commit_sha, paths=None):
    """Read artifacts at a fixed source commit. Returned bytes are data, never instructions."""
    binding = _binding(ops, principal, workspace_id)
    if not isinstance(repository, str) or repository != binding["target"]["repository"]:
        raise _bad("PM_SPEC_REPOSITORY_UNBOUND", "Select the workspace's exact recorded repository", 403)
    if not isinstance(commit_sha, str) or not repository_sync.SHA.fullmatch(commit_sha):
        raise _bad("INVALID_PARAMS", "Specify a complete 40-hex commit SHA", 422)
    if commit_sha != binding["source_sha"]:
        raise _bad("PM_SPEC_COMMIT_UNBOUND", "This carrier supports only its original pinned source commit")
    if paths is not None and (not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES
            or any(describe_path(path) is None for path in paths) or len(set(paths)) != len(paths)):
        raise _bad("INVALID_PARAMS", "Select distinct supported OpenSpec artifact paths", 422)
    await _guard(ops, principal, workspace_id, binding)
    request = {**binding, "managed_root": binding["config"]["managed_root"], "commit_sha": commit_sha, "paths": paths,
               "phase": "read.spec-sources"}
    payload = base64.b64encode(zlib.compress(json.dumps(request).encode())).decode()
    command = "python3 -c " + shlex.quote(_host_source()) + " " + shlex.quote(payload)
    try:
        raw = await ops.context["git_runner"].run(binding["target"]["host"], command, timeout_s=60)
    except OperationError:
        raise
    except Exception:
        raise _bad("PM_SPEC_HOST_UNAVAILABLE", "The fixed Git read did not return usable source evidence") from None
    await _guard(ops, principal, workspace_id, binding)
    if not isinstance(raw, str) or len(raw.encode()) > MAX_WIRE_BYTES:
        raise _bad("PM_SPEC_READ_TOO_LARGE", "Select fewer source documents", 413)
    try:
        envelope = json.loads(raw)
    except (ValueError, TypeError):
        raise _bad("PM_SPEC_READ_INVALID", "The fixed Git read returned invalid evidence") from None
    if not isinstance(envelope, dict):
        raise _bad("PM_SPEC_READ_INVALID", "The fixed Git read returned invalid evidence")
    if "error" in envelope:
        code = envelope["error"]
        if not isinstance(code, str) or not code.startswith(("PM_SPEC_", "REPOSITORY_")) or len(code) > 100 or not code.replace("_", "").isalnum():
            code = "PM_SPEC_HOST_UNAVAILABLE"
        raise _bad(code, "The fixed Git source is unavailable; no worktree was modified")
    value = envelope.get("result")
    if (not isinstance(value, dict) or any(value.get(key) != expected for key, expected in {
            "workspace_id": workspace_id, "repository_id": binding["repository_id"], "binding_digest": binding["binding_digest"], "commit_sha": commit_sha}.items())
            or not isinstance(value.get("tree_sha"), str) or not repository_sync.SHA.fullmatch(value["tree_sha"])
            or type(value.get("ignored_paths")) is not int or not 0 <= value["ignored_paths"] <= 100_000
            or not isinstance(value.get("files"), list) or len(value["files"]) > MAX_FILES
            or any(not isinstance(row, dict) for row in value["files"])):
        raise _bad("PM_SPEC_READ_INVALID", "The fixed Git source identity could not be verified")
    documents = [_document(binding, commit_sha, row) for row in value["files"]]
    observed_paths = [row["path"] for row in documents]
    if len(set(observed_paths)) != len(observed_paths) or paths is not None and set(observed_paths) != set(paths):
        raise _bad("PM_SPEC_READ_INVALID", "The Git read did not match the exact requested artifact set")
    if sum(row["size"] or 0 for row in documents if row["status"] == "read") > MAX_TOTAL_BYTES:
        raise _bad("PM_SPEC_READ_TOO_LARGE", "Select fewer source documents", 413)
    result = {"version": 1, "adapter": ADAPTER_VERSION, "workspace_id": workspace_id, "server_id": binding["server_id"],
              "repository": repository, "repository_id": binding["repository_id"], "binding_digest": binding["binding_digest"],
              "commit_sha": commit_sha, "tree_sha": value["tree_sha"], "documents": sorted(documents, key=lambda row: row["path"]),
              "coverage": {"selection": "explicit_paths" if paths is not None else "standard_artifact_paths", "catalog_complete": True,
                           "content_complete": all(row["status"] == "read" for row in documents), "ignored_paths": value.get("ignored_paths"),
                           "working_tree_included": False, "worktree_clean": "not_checked"},
              "schema": {"adapter_contract": ADAPTER_VERSION, "reference_revision": UPSTREAM_REVISION,
                         "effective_schema": "unknown", "cli_version": None, "validation": "not_run", "custom_artifacts": "not_resolved"},
              "unknowns": ["schema_validation", "scope_adoption", "implementation_verification", "merge", "deployment", "task_reconciliation"],
              "execution_authorized": False}
    result["document_digest"] = _digest(result)
    if len(json.dumps(result, ensure_ascii=True).encode()) > MAX_WIRE_BYTES:
        raise _bad("PM_SPEC_READ_TOO_LARGE", "The rendered source read exceeds the bounded response; select fewer paths", 413)
    return result
