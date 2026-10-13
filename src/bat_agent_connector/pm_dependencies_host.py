"""Bounded dependency catalog on the existing read-only, pinned PM Git carrier."""
import base64
import hashlib
import json
import re
import subprocess
import sys
import zlib

if __name__ != "__main__":
    from .pm_dependency_paths import (
        MAX_DEPTH,
        MAX_ENTRIES,
        MAX_FILE_BYTES,
        MAX_FILES,
        MAX_TOTAL_BYTES,
        MAX_TREES,
        MAX_WIRE_BYTES,
        SKIP_DIRS,
        allowed_path,
        safe_path,
    )
    from .pm_spec_sources_host import spec_execute, spec_git, spec_size


def dependency_tree(repo, object_id, prefix, unreadable, reasons):
    def issue(reason, **fields):
        reasons.add(reason)
        if len(unreadable) < MAX_FILES:
            unreadable.append({"kind": "tree", "path": prefix.rstrip("/") or ".", "object_sha": object_id,
                               "status": "too_large" if reason == "tree_too_large" else "unparsable", "reason": reason, **fields})
        else:
            reasons.add("unreadable_limit")
    try:
        if spec_size(repo, object_id) > 512_000:
            issue("tree_too_large")
            return []
        data = spec_git(repo, "ls-tree", "-z", object_id)
    except (ValueError, OSError, subprocess.SubprocessError):
        issue("tree_unavailable")
        return []
    rows = []
    for raw in data.split(b"\0"):
        if not raw:
            continue
        header, separator, name = raw.partition(b"\t")
        if not separator:
            raise ValueError("PM_SPEC_GIT_UNAVAILABLE")
        mode, kind, sha = header.decode("ascii").split(" ")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("PM_SPEC_BLOB_CHANGED")
        try:
            path = name.decode("utf-8")
        except UnicodeDecodeError:
            # Every allowed dependency filename is ASCII. A non-UTF-8 blob name
            # cannot hide one; an unreadable directory can hide entire subtrees.
            if kind != "tree":
                continue
            issue("path_encoding_unsupported", kind="entry", path=None, parent_path=prefix.rstrip("/") or ".",
                  object_sha=sha, name_bytes_sha256=hashlib.sha256(name).hexdigest())
            continue
        if "/" in path or not safe_path(prefix + path):
            issue("path_unsafe", kind="entry", path=None, parent_path=prefix.rstrip("/") or ".",
                  object_sha=sha, name_bytes_sha256=hashlib.sha256(name).hexdigest())
            continue
        rows.append((path, mode, kind, sha))
    return rows


def dependency_objects(req):
    repo, commit = req["clone_path"], req["commit_sha"]
    if spec_git(repo, "cat-file", "-t", commit).strip() != b"commit":
        raise ValueError("PM_SPEC_COMMIT_UNAVAILABLE")
    tree = spec_git(repo, "rev-parse", "--verify", commit + "^{tree}").strip().decode("ascii")
    queue, found, reasons, entries, trees = [("", tree, 0)], {}, set(), 0, 0
    unreadable = []
    while queue:
        if trees >= MAX_TREES:
            reasons.add("tree_limit")
            break
        prefix, object_id, depth = queue.pop(0)
        trees += 1
        rows = dependency_tree(repo, object_id, prefix, unreadable, reasons)
        for name, mode, kind, sha in sorted(rows):
            entries += 1
            if entries > MAX_ENTRIES:
                reasons.add("entry_limit")
                break
            path = prefix + name
            if kind == "tree" and name not in SKIP_DIRS:
                if depth >= MAX_DEPTH:
                    reasons.add("depth_limit")
                else:
                    queue.append((path + "/", sha, depth + 1))
            elif allowed_path(path):
                if len(found) >= MAX_FILES:
                    reasons.add("file_limit")
                    continue
                found[path] = {"path": path, "mode": mode, "kind": kind, "blob_sha": sha}
        if entries > MAX_ENTRIES:
            break
    # An absent conventional lockfile is explicit evidence of missing resolution.
    for path in list(found):
        name = path.rsplit("/", 1)[-1]
        lock = {"pyproject.toml": "uv.lock", "package.json": "package-lock.json", "Cargo.toml": "Cargo.lock", "go.mod": "go.sum"}.get(name)
        if not lock:
            continue
        prefix = path[:-len(name)]
        ancestors = [prefix]
        while prefix:
            prefix = prefix.rstrip("/").rpartition("/")[0]
            prefix = prefix + "/" if prefix else ""
            ancestors.append(prefix)
        if any(p + lock in found for p in ancestors):
            continue
        missing = path[:-len(name)] + lock
        if len(found) < MAX_FILES:
            found[missing] = {"path": missing, "reason": "missing_at_commit"}
        else:
            reasons.add("file_limit")
    total = 0
    for row in sorted(found.values(), key=lambda r: r["path"]):
        if "reason" in row:
            continue
        if row["kind"] != "blob" or row["mode"] not in {"100644", "100755"}:
            row["reason"] = "symlink_or_nonregular_blob"
            continue
        try:
            size = spec_size(repo, row["blob_sha"])
        except (ValueError, OSError, subprocess.SubprocessError):
            row["reason"] = "blob_unavailable"
            continue
        row["size"] = size
        if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
            row["reason"] = "document_too_large" if size > MAX_FILE_BYTES else "selection_byte_budget"
            continue
        try:
            raw = spec_git(repo, "cat-file", "blob", row["blob_sha"])
        except (ValueError, OSError, subprocess.SubprocessError):
            row["reason"] = "blob_unavailable"
            continue
        if len(raw) != size or hashlib.sha1(b"blob " + str(size).encode() + b"\0" + raw).hexdigest() != row["blob_sha"]:  # noqa: S324 - Git identity
            raise ValueError("PM_SPEC_BLOB_CHANGED")
        row.update(content=base64.b64encode(raw).decode(), sha256=hashlib.sha256(raw).hexdigest())
        total += size
    result = {"tree_sha": tree, "files": sorted(found.values(), key=lambda r: r["path"]),
              "unreadable_paths": unreadable, "catalog_reasons": sorted(reasons)}
    # The existing SSH runner has a 1 MiB output ceiling. Reserve the envelope and
    # account for JSON escaping of paths before returning any bytes to that runner.
    for row in reversed(result["files"]):
        if len(json.dumps(result).encode()) <= MAX_WIRE_BYTES - 2048:
            break
        if "content" in row:
            row.pop("content")
            row.pop("sha256")
            row["reason"] = "selection_wire_budget"
    while result["unreadable_paths"] and len(json.dumps(result).encode()) > MAX_WIRE_BYTES - 2048:
        result["unreadable_paths"].pop()
        reasons.add("unreadable_limit")
        result["catalog_reasons"] = sorted(reasons)
    if len(json.dumps(result).encode()) > MAX_WIRE_BYTES - 2048:
        raise ValueError("PM_SPEC_CATALOG_TOO_LARGE")
    return result


if __name__ == "__main__":
    try:
        request = json.loads(zlib.decompress(base64.b64decode(sys.argv[1])))
        print(json.dumps({"result": spec_execute(request, reader=dependency_objects)}), flush=True)
    except (ValueError, OSError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError):
        error = str(sys.exc_info()[1])
        print(json.dumps({"error": error if error.startswith(("PM_SPEC_", "REPOSITORY_")) else "PM_SPEC_HOST_UNAVAILABLE"}), flush=True)
