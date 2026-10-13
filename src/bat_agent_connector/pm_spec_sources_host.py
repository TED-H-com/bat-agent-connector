"""Read pinned PM Git objects only; appended to existing repository identity helpers.

No fetch, checkout, index write, filter, OpenSpec executable or repository script.
The worktree may be dirty: it is never the source of document bytes.
"""
import base64
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import zlib
from contextlib import ExitStack

if __name__ != "__main__":
    from .pm_spec_source_format import MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, describe_path
    from .repository_sync_host import LOCK_FDS, directory, identity, pinned, real


def spec_git(repo, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_LFS_SKIP_SMUDGE="1",
               GIT_NO_LAZY_FETCH="1")
    result = subprocess.run(["git", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",  # noqa: S603,S607
        "-c", "core.fsmonitor=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
        "-c", "protocol.allow=never", "-C", repo, *args], env=env, capture_output=True,
        timeout=30, pass_fds=tuple(LOCK_FDS))
    if result.returncode:
        raise ValueError("PM_SPEC_GIT_UNAVAILABLE")
    return result.stdout


def spec_size(repo, object_id):
    value = spec_git(repo, "cat-file", "-s", object_id).strip()
    if not value.isdigit() or len(value) > 12:
        raise ValueError("PM_SPEC_GIT_UNAVAILABLE")
    return int(value)


def spec_tree(repo, object_id):
    if spec_size(repo, object_id) > 512_000:
        raise ValueError("PM_SPEC_TREE_TOO_LARGE")
    rows = []
    for raw in spec_git(repo, "ls-tree", "-z", object_id).split(b"\0"):
        if not raw:
            continue
        header, separator, name = raw.partition(b"\t")
        if not separator:
            raise ValueError("PM_SPEC_GIT_UNAVAILABLE")
        mode, kind, sha = header.decode("ascii").split(" ")
        try:
            path = name.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ValueError("PM_SPEC_PATH_ENCODING_UNSUPPORTED") from None
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or "/" in path or path in {"", ".", ".."}:
            raise ValueError("PM_SPEC_PATH_UNSAFE")
        rows.append((path, mode, kind, sha))
    return rows


def spec_objects(req):
    repo, commit = req["clone_path"], req["commit_sha"]
    if spec_git(repo, "cat-file", "-t", commit).strip() != b"commit":
        raise ValueError("PM_SPEC_COMMIT_UNAVAILABLE")
    tree = spec_git(repo, "rev-parse", "--verify", commit + "^{tree}").strip().decode("ascii")
    root = next((row for row in spec_tree(repo, tree) if row[0] == "openspec"), None)
    if root is None:
        missing = [{"path": path, "reason": "missing_at_commit"} for path in req.get("paths") or []]
        return {"tree_sha": tree, "files": missing, "ignored_paths": 0}
    if root[1:3] != ("040000", "tree"):
        raise ValueError("PM_SPEC_ROOT_UNSAFE")
    found, ignored, tree_count = [], 0, 0

    def walk(prefix, object_id, depth):
        nonlocal ignored, tree_count
        tree_count += 1
        if depth > 14 or tree_count > 500:
            raise ValueError("PM_SPEC_CATALOG_TOO_LARGE")
        for name, mode, kind, sha in spec_tree(repo, object_id):
            path = prefix + "/" + name
            if kind == "tree":
                if mode != "040000" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", name):
                    ignored += 1
                    continue
                walk(path, sha, depth + 1)
            elif describe_path(path):
                found.append({"path": path, "mode": mode, "kind": kind, "blob_sha": sha})
                if len(found) > 1000:
                    raise ValueError("PM_SPEC_CATALOG_TOO_LARGE")
            else:
                ignored += 1

    walk("openspec", root[3], 0)
    paths = req.get("paths")
    if paths is not None:
        if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES or any(describe_path(path) is None for path in paths) or len(set(paths)) != len(paths):
            raise ValueError("PM_SPEC_PATH_UNSAFE")
        found = [row for row in found if row["path"] in paths]
        found.extend({"path": path, "reason": "missing_at_commit"} for path in paths if not any(row["path"] == path for row in found))
    if len(found) > MAX_FILES:
        raise ValueError("PM_SPEC_SELECTION_REQUIRED")
    total = 0
    for row in sorted(found, key=lambda row: row["path"]):
        if "reason" in row:
            continue
        if row["kind"] != "blob" or row["mode"] not in {"100644", "100755"}:
            row["reason"] = "symlink_or_nonregular_blob"
            continue
        size = spec_size(repo, row["blob_sha"])
        row["size"] = size
        if size > MAX_FILE_BYTES:
            row["reason"] = "document_too_large"
            continue
        if total + size > MAX_TOTAL_BYTES:
            row["reason"] = "selection_byte_budget"
            continue
        raw = spec_git(repo, "cat-file", "blob", row["blob_sha"])
        if len(raw) != size or hashlib.sha1(b"blob " + str(size).encode() + b"\0" + raw).hexdigest() != row["blob_sha"]:  # noqa: S324 - Git object identity
            raise ValueError("PM_SPEC_BLOB_CHANGED")
        row.update(content=base64.b64encode(raw).decode(), sha256=hashlib.sha256(raw).hexdigest())
        total += size
    return {"tree_sha": tree, "files": sorted(found, key=lambda row: row["path"]), "ignored_paths": ignored}


def spec_execute(req, *, reader=spec_objects):
    root, clone = req["managed_root"], req["clone_path"]
    if (not re.fullmatch(r"op_[0-9a-f]{32}", req["operation_id"])
            or clone != root + "/batc-published-" + req["operation_id"][3:]
            or not re.fullmatch(r"[0-9a-f]{40}", req["commit_sha"])
            or req["commit_sha"] != req["source_sha"]):
        raise ValueError("PM_SPEC_COMMIT_UNBOUND")
    with ExitStack() as stack:
        for path in (root, clone):
            real(path)
            fd = stack.enter_context(directory(path))
            fcntl.flock(fd, fcntl.LOCK_SH)
            LOCK_FDS.append(fd)
            stack.callback(LOCK_FDS.remove, fd)
            if os.stat(path) != os.fstat(fd):
                raise ValueError("PM_SPEC_REPOSITORY_CHANGED")
        identity(req)
        if not pinned(req):
            raise ValueError("PM_SPEC_COMMIT_UNAVAILABLE")
        result = reader(req)
        identity(req)
        if not pinned(req):
            raise ValueError("PM_SPEC_COMMIT_UNAVAILABLE")
        return {**result, "commit_sha": req["commit_sha"], "binding_digest": req["binding_digest"],
                "repository_id": req["repository_id"], "workspace_id": req["pm_workspace_id"]}


if __name__ == "__main__":
    try:
        request = json.loads(zlib.decompress(base64.b64decode(sys.argv[1])))
        print(json.dumps({"result": spec_execute(request)}), flush=True)
    except (ValueError, OSError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError):
        error = str(sys.exc_info()[1])
        print(json.dumps({"error": error if error.startswith(("PM_SPEC_", "REPOSITORY_")) else "PM_SPEC_HOST_UNAVAILABLE"}), flush=True)
