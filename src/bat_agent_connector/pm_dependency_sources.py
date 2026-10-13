"""Verify dependency bytes through the existing PM source binding and final guard."""
from __future__ import annotations

import base64
import hashlib
import json
import shlex
import zlib
from importlib import resources

from . import pm_spec_sources as carrier
from . import repository_sync
from .operations import OperationError
from .pm_dependency_paths import MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, MAX_WIRE_BYTES, allowed_path


def invalid():
    return OperationError("PM_DEPENDENCIES_INVALID_SOURCE", "Dependency source identity is invalid / 相依來源身分無法驗證", 409)


def _host_source():
    files = resources.files(__package__)
    names = ("repository_sync_host.py", "pm_spec_source_format.py", "pm_spec_sources_host.py", "pm_dependency_paths.py")
    source = []
    for name in names:
        part = (files / name).read_text().split('\nif __name__ == "__main__":', 1)[0]
        source.append(part.replace("from __future__ import annotations\n", ""))
    return "\n".join(source) + "\n" + (files / "pm_dependencies_host.py").read_text()


def _file(row):
    if not isinstance(row, dict) or not allowed_path(row.get("path")):
        raise invalid()
    blob, size, reason = row.get("blob_sha"), row.get("size"), row.get("reason")
    if blob is not None and (not isinstance(blob, str) or not repository_sync.SHA.fullmatch(blob)):
        raise invalid()
    if size is not None and (type(size) is not int or not 0 <= size < 10**12):
        raise invalid()
    result = {"path": row["path"], "blob_sha": blob, "size": size, "sha256": None, "status": "read", "reason": None}
    if reason is not None:
        statuses = {"missing_at_commit": "missing", "document_too_large": "too_large",
                    "selection_byte_budget": "too_large", "symlink_or_nonregular_blob": "unparsable"}
        if reason not in statuses or reason == "missing_at_commit" and (blob is not None or size is not None):
            raise invalid()
        return {**result, "status": statuses[reason], "reason": reason}, None
    if (size is None or size > MAX_FILE_BYTES or blob is None or row.get("kind") != "blob"
            or row.get("mode") not in {"100644", "100755"} or not isinstance(row.get("content"), str)
            or len(row["content"]) > MAX_FILE_BYTES * 2):
        raise invalid()
    try:
        raw = base64.b64decode(row["content"], validate=True)
    except (ValueError, UnicodeError):
        raise invalid() from None
    if (len(raw) != size or hashlib.sha256(raw).hexdigest() != row.get("sha256")
            or hashlib.sha1(b"blob " + str(size).encode() + b"\0" + raw).hexdigest() != blob):  # noqa: S324 - Git identity
        raise invalid()
    result["sha256"] = row["sha256"]
    try:
        content = raw.decode("utf-8")
        if "\0" in content:
            raise ValueError
    except (UnicodeError, ValueError):
        return {**result, "status": "unparsable", "reason": "non_utf8_or_binary"}, None
    return result, content


async def collect(ops, principal, wid, binding):
    await carrier._guard(ops, principal, wid, binding)
    request = {**binding, "managed_root": binding["config"]["managed_root"], "commit_sha": binding["source_sha"],
               "phase": "read.dependencies"}
    payload = base64.b64encode(zlib.compress(json.dumps(request).encode())).decode()
    try:
        raw = await ops.context["git_runner"].run(binding["target"]["host"],
            "python3 -c " + shlex.quote(_host_source()) + " " + shlex.quote(payload), timeout_s=60)
    except OperationError:
        raise
    except Exception:
        raise OperationError("PM_DEPENDENCIES_SOURCE_UNAVAILABLE", "Pinned source unavailable / 無法讀取固定版本來源", 409) from None
    await carrier._guard(ops, principal, wid, binding)
    if not isinstance(raw, str) or len(raw.encode()) > MAX_WIRE_BYTES:
        raise invalid()
    try:
        envelope = json.loads(raw)
    except (ValueError, RecursionError):
        raise invalid() from None
    if not isinstance(envelope, dict) or "error" in envelope:
        raise invalid()
    value = envelope.get("result")
    if (not isinstance(value, dict) or any(value.get(k) != v for k, v in {
            "workspace_id": wid, "repository_id": binding["repository_id"], "binding_digest": binding["binding_digest"],
            "commit_sha": binding["source_sha"]}.items()) or not isinstance(value.get("tree_sha"), str)
            or not repository_sync.SHA.fullmatch(value["tree_sha"]) or not isinstance(value.get("files"), list)
            or len(value["files"]) > MAX_FILES or not isinstance(value.get("catalog_reasons"), list)
            or any(r not in {"tree_limit", "entry_limit", "depth_limit", "file_limit"} for r in value["catalog_reasons"])):
        raise invalid()
    rows, contents = [], {}
    for raw_row in value["files"]:
        row, content = _file(raw_row)
        if row["path"] in contents:
            raise invalid()
        rows.append(row)
        contents[row["path"]] = content
    if sum(r["size"] or 0 for r in rows if r["sha256"]) > MAX_TOTAL_BYTES:
        raise invalid()
    return {"files": rows, "contents": contents, "tree_sha": value["tree_sha"], "catalog_reasons": value["catalog_reasons"]}
