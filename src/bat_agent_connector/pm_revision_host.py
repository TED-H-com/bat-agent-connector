"""Deterministic, scoped PM revision Git transaction on the original owned carrier.

Appended after the published repository and PM draft helpers by the existing SSH
runner. The Git fact is not an adopted-plan decision. No source checkout or remote
push is involved, and only this clone's dedicated worktree/index is changed.
"""
import base64
import contextlib
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
    from .pm_worktree_host import pm_carrier as _base_carrier
    from .pm_worktree_host import (
        pm_directory,
        pm_execute,
        pm_manifest,
        pm_parent,
        pm_read,
        pm_write,
    )
    from .repository_sync_host import LOCK_FDS, directory, git, identity, pinned, real
else:
    _base_carrier = globals()["pm_carrier"]

_REVISIONS = "batc-pm-revisions.json"
_REV_PATH = re.compile(r"(?:docs/pm/revisions/pmrev_[0-9a-f]{32}/plan\.(?:json|md)|"
    r"openspec/changes/pm-[0-9a-f]{32}/revisions/pmwp_[0-9a-f]{32}/(?:package\.json|proposal\.md|requirements\.md|design\.md|tasks\.md|manifest\.json)|"
    r"docs/pm/specs/pm-[0-9a-f]{32}/revisions/pmwp_[0-9a-f]{32}\.json)")


def rev_paths(data):
    if data.get("artifact_kind") == "pm-spec":
        if (not re.fullmatch(r"pmsp_[0-9a-f]{32}", data["revision_id"])
                or not re.fullmatch(r"pm-[0-9a-f]{32}", data["change_id"])
                or not re.fullmatch(r"pmwp_[0-9a-f]{32}", data["package_id"])):
            raise ValueError("PM_REVISION_INVALID")
        prefix = "openspec/changes/" + data["change_id"] + "/revisions/" + data["package_id"] + "/"
        return {prefix + name for name in ("package.json", "proposal.md", "requirements.md", "design.md", "tasks.md", "manifest.json")} | {
            "docs/pm/specs/" + data["change_id"] + "/revisions/" + data["package_id"] + ".json"}
    if "artifact_kind" in data or not re.fullmatch(r"pmrev_[0-9a-f]{32}", data["revision_id"]):
        raise ValueError("PM_REVISION_INVALID")
    return {"docs/pm/revisions/" + data["revision_id"] + "/plan." + ext for ext in ("md", "json")}



def rev_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def rev_binding(req):
    return hashlib.sha256(rev_json({"server_id": req["server_id"], "workspace_id": req["pm_workspace_id"],
        "bootstrap_id": req["operation_id"], "binding_digest": req["binding_digest"]})).hexdigest()


def rev_git(path, *args, data=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_LFS_SKIP_SMUDGE="1")
    result = subprocess.run(["git", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",  # noqa: S603,S607
        "-c", "core.fsmonitor=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
        "-c", "protocol.ext.allow=never", "-c", "fetch.recurseSubmodules=false", "-C", path, *args],
        input=data, capture_output=True, env=env, timeout=1800, pass_fds=tuple(LOCK_FDS))
    if result.returncode:
        raise ValueError("PM_REVISION_GIT_FAILED")
    return result.stdout


def rev_load(req):
    with directory(req["clone_path"] + "/.git") as fd:
        raw = pm_read(fd, _REVISIONS, missing=True, maximum=4_000_000)
    if raw is None:
        return []
    try:
        value = json.loads(raw)
        if value["schema"] != "batc.pm.revisions.v1" or value["binding_id"] != rev_binding(req):
            raise ValueError("PM_REVISION_MANIFEST_CHANGED")
        rows = value["records"]
        if not isinstance(rows, list):
            raise ValueError("PM_REVISION_MANIFEST_CHANGED")
        parent, operations, revisions = req["source_sha"], set(), set()
        for index, row in enumerate(rows):
            receipt = row["receipt"]
            if (row["parent"] != parent or row["state"] not in {"prepared", "complete"}
                    or row["state"] == "prepared" and index != len(rows) - 1
                    or receipt["binding_id"] != rev_binding(req) or receipt["workspace_id"] != req["pm_workspace_id"]
                    or not re.fullmatch(r"op_[0-9a-f]{32}", receipt["operation_id"])
                    or receipt["operation_id"] in operations or receipt["revision_id"] in revisions
                    or any(not re.fullmatch(r"[0-9a-f]{40}", receipt[key]) for key in ("commit_sha", "tree_sha"))
                    or not re.fullmatch(r"[0-9a-f]{64}", row["index_before"])
                    or row["state"] == "complete" and not re.fullmatch(r"[0-9a-f]{64}", row["index_after"])):
                raise ValueError("PM_REVISION_MANIFEST_CHANGED")
            expected_paths = rev_paths(receipt)
            if set(receipt["file_digests"]) != expected_paths:
                raise ValueError("PM_REVISION_MANIFEST_CHANGED")
            operations.add(receipt["operation_id"])
            revisions.add(receipt["revision_id"])
            parent = receipt["commit_sha"]
        return rows
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("PM_REVISION_MANIFEST_CHANGED") from None


def rev_save(req, rows):
    raw = rev_json({"schema": "batc.pm.revisions.v1", "binding_id": rev_binding(req), "records": rows}) + b"\n"
    if len(raw) > 4_000_000:
        raise ValueError("PM_REVISION_MANIFEST_LIMIT")
    with directory(req["clone_path"] + "/.git") as fd:
        pm_read(fd, _REVISIONS, missing=True, maximum=4_000_000)
        pm_write(fd, _REVISIONS, raw, replace=True)


def rev_object(kind, data):
    return hashlib.sha1(kind.encode() + b" " + str(len(data)).encode() + b"\0" + data).hexdigest()  # noqa: S324 - Git SHA-1 object identity


def rev_commit_bytes(record):
    receipt = record["receipt"]
    author = b"PM Workspace <pm@example.invalid> " + str(record["timestamp"]).encode() + b" +0000"
    return (b"tree " + receipt["tree_sha"].encode() + b"\nparent " + record["parent"].encode()
            + b"\nauthor " + author + b"\ncommitter " + author + b"\n\nPM revision "
            + receipt["revision_id"].encode() + b"\n\nBATC-Operation: " + receipt["operation_id"].encode() + b"\n")


def rev_ref(record):
    return "refs/batc/pm-revisions/" + record["receipt"]["operation_id"][3:]


def rev_verify(req, record):
    receipt = record["receipt"]
    clone = req["clone_path"]
    if git(clone, "rev-parse", "--verify", rev_ref(record), optional=True) != receipt["commit_sha"]:
        raise ValueError("PM_REVISION_REF_CHANGED")
    if rev_git(clone, "cat-file", "commit", receipt["commit_sha"]) != rev_commit_bytes(record):
        raise ValueError("PM_REVISION_COMMIT_CHANGED")
    paths = rev_git(clone, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", record["parent"], receipt["commit_sha"])
    if set(paths.decode().strip("\0").split("\0")) != set(receipt["file_digests"]):
        raise ValueError("PM_REVISION_SCOPE_CHANGED")
    for path, digest in receipt["file_digests"].items():
        if hashlib.sha256(rev_git(clone, "cat-file", "blob", receipt["commit_sha"] + ":" + path)).hexdigest() != digest:
            raise ValueError("PM_REVISION_FILES_CHANGED")
    if hashlib.sha256(rev_json(receipt["file_digests"])).hexdigest() != receipt["document_digest"]:
        raise ValueError("PM_REVISION_FILES_CHANGED")


def rev_index(req):
    path = git(req["worktree_path"], "rev-parse", "--path-format=absolute", "--git-path", "index")
    common = req["clone_path"] + "/.git/worktrees/"
    if not path.startswith(common) or os.path.realpath(path) != path:
        raise ValueError("PM_REVISION_INDEX_CHANGED")
    with directory(os.path.dirname(path)) as fd:
        value = pm_read(fd, "index", maximum=64_000_000)
    return hashlib.sha256(value).hexdigest()


def rev_head(req, *, pending=False):
    rows = rev_load(req)
    head = req["source_sha"]
    for row in rows:
        if row["state"] == "prepared":
            if not pending:
                raise ValueError("PM_REVISION_PENDING")
            break
        rev_verify(req, row)
        head = row["receipt"]["commit_sha"]
    return head, rows


def pm_carrier(req, *, pending=None):
    head, rows = rev_head(req, pending=True)
    if rows and rows[-1]["state"] == "prepared":
        record = rows[-1]
        if record["receipt"]["operation_id"] != req.get("active_operation_id"):
            raise ValueError("PM_REVISION_PENDING")
        current = rev_pending_guard(req, record,
            {path: {"sha256": digest} for path, digest in record["receipt"]["file_digests"].items()})
        return {"clone_path": req["clone_path"], "worktree_path": req["worktree_path"], "branch": req["branch"], "head": current}
    observed = _base_carrier({**req, "source_sha": head}, pending=pending)
    if observed and rows and rev_index(req) != rows[-1]["index_after"]:
        raise ValueError("PM_REVISION_INDEX_CHANGED")
    return observed


@contextlib.contextmanager
def rev_parent(path, relative, *, create=False):
    if not _REV_PATH.fullmatch(relative):
        raise ValueError("PM_REVISION_PATH_INVALID")
    with ExitStack() as stack:
        fd = stack.enter_context(directory(path))
        for component in relative.split("/")[:-1]:
            fd = pm_directory(fd, component, create=create)
            stack.callback(os.close, fd)
        yield fd


def rev_request(req):
    data = req["revision"]
    try:
        if (data["workspace_id"] != req["pm_workspace_id"] or data["binding_id"] != rev_binding(req)
                or not re.fullmatch(r"op_[0-9a-f]{32}", data["operation_id"])
                or not re.fullmatch(r"[0-9a-f]{40}", data["expected_parent"])
                or type(data["timestamp"]) is not int or not 0 < data["timestamp"] < 32_503_680_000
                or set(data["files"]) != rev_paths(data)):
            raise ValueError("PM_REVISION_INVALID")
        files = {path: content.encode() for path, content in data["files"].items()}
        if (sum(map(len, files.values())) > 512_000
                or {path: hashlib.sha256(content).hexdigest() for path, content in files.items()} != data["file_digests"]
                or hashlib.sha256(rev_json(data["file_digests"])).hexdigest() != data["document_digest"]):
            raise ValueError("PM_REVISION_INVALID")
        return data, files
    except (KeyError, TypeError, AttributeError, UnicodeEncodeError):
        raise ValueError("PM_REVISION_INVALID") from None


def rev_tree(req, parent, files):
    """Read only existing ancestor trees; construct Git objects without an index."""
    objects = []
    trie = {}
    for path, data in files.items():
        node = trie
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part.encode(), {})
        node[parts[-1].encode()] = data
    def tree(sha, additions):
        entries = {}
        if sha:
            raw = rev_git(req["clone_path"], "cat-file", "tree", sha)
            offset = 0
            while offset < len(raw):
                zero = raw.index(b"\0", offset)
                mode, name = raw[offset:zero].split(b" ", 1)
                entries[name] = (mode, raw[zero + 1:zero + 21].hex())
                offset = zero + 21
        for name, item in additions.items():
            previous = entries.get(name)
            if isinstance(item, dict):
                if previous and previous[0] != b"40000":
                    raise ValueError("PM_REVISION_PATH_INVALID")
                entries[name] = (b"40000", tree(previous[1] if previous else None, item))
            else:
                if previous:
                    raise ValueError("PM_REVISION_PATH_EXISTS")
                sha = rev_object("blob", item)
                objects.append(("blob", sha, item))
                entries[name] = (b"100644", sha)
        data = b"".join(mode + b" " + name + b"\0" + bytes.fromhex(sha) for name, (mode, sha) in
                        sorted(entries.items(), key=lambda pair: pair[0] + (b"/" if pair[1][0] == b"40000" else b"")))
        sha = rev_object("tree", data)
        objects.append(("tree", sha, data))
        return sha
    parent_tree = git(req["clone_path"], "rev-parse", parent + "^{tree}")
    return tree(parent_tree, trie), objects


def rev_pending_guard(req, record, files):
    """Prove that an interrupted original transaction can still be finished."""
    path, receipt = req["worktree_path"], record["receipt"]
    real(path)
    if git(path, "symbolic-ref", "--short", "HEAD") != req["branch"]:
        raise ValueError("PM_WORKTREE_CHANGED")
    current = git(path, "rev-parse", "HEAD")
    if current not in {record["parent"], receipt["commit_sha"]}:
        raise ValueError("PM_REVISION_PARENT_CHANGED")
    if git(req["clone_path"], "rev-parse", "--verify", rev_ref(record), optional=True) not in {None, receipt["commit_sha"]}:
        raise ValueError("PM_REVISION_REF_CHANGED")
    # Reject index flags that would hide worktree changes from normal Git diff.
    flags = rev_git(path, "ls-files", "-v", "-z").split(b"\0")
    if any(line and (line[:1].islower() or line[:1] == b"S") for line in flags):
        raise ValueError("PM_REVISION_INDEX_CHANGED")
    index = rev_index(req)
    if index != record["index_before"]:
        if current != receipt["commit_sha"] or rev_git(path, "diff-index", "--cached", "--raw", current, "--"):
            raise ValueError("PM_REVISION_INDEX_CHANGED")
    for args in (("diff-index", "--name-only", "-z", record["parent"], "--"), ("diff-files", "--name-only", "-z", "--")):
        changed = set(filter(None, rev_git(path, *args).decode().split("\0")))
        if changed - set(files):
            raise ValueError("PM_WORKTREE_CHANGED")
    drafts = pm_manifest(req)
    for relative, original in drafts.items():
        with pm_parent(path, relative) as fd:
            saved = pm_read(fd, relative.rsplit("/", 1)[1])
        if len(saved) != original["size"] or hashlib.sha256(saved).hexdigest() != original["sha256"]:
            raise ValueError("PM_SNAPSHOT_CHANGED")
    unknown = set(filter(None, rev_git(path, "ls-files", "--others", "-z").decode().split("\0")))
    if unknown - set(drafts) - set(files):
        raise ValueError("PM_WORKTREE_UNREGISTERED_FILES")
    for relative, data in files.items():
        try:
            with rev_parent(path, relative) as fd:
                existing = pm_read(fd, relative.rsplit("/", 1)[1], missing=True)
        except FileNotFoundError:
            existing = None
        expected_digest = data["sha256"] if isinstance(data, dict) else hashlib.sha256(data).hexdigest()
        if existing is not None and hashlib.sha256(existing).hexdigest() != expected_digest:
            raise ValueError("PM_REVISION_FILES_CHANGED")
    return current


def rev_transaction(req, *, write):
    data, files = rev_request(req)
    head, rows = rev_head(req, pending=True)
    existing = next((row for row in rows if row["receipt"]["operation_id"] == data["operation_id"]), None)
    digest = hashlib.sha256(rev_json(data)).hexdigest()
    if existing:
        if existing["request_digest"] != digest:
            raise ValueError("PM_REVISION_INTENT_CHANGED")
        if existing["state"] == "complete":
            rev_verify(req, existing)
            pm_carrier(req)
            return existing["receipt"] if write else {"receipt": existing["receipt"]}
    else:
        if rows and rows[-1]["state"] != "complete":
            raise ValueError("PM_REVISION_PENDING")
        if head != data["expected_parent"]:
            raise ValueError("PM_REVISION_PARENT_CHANGED")
        pm_carrier(req)
    tree, objects = rev_tree(req, data["expected_parent"], files)
    receipt = {key: data[key] for key in ("workspace_id", "revision_id", "operation_id", "document_digest", "file_digests", "binding_id")}
    if data.get("artifact_kind") == "pm-spec":
        receipt.update({key: data[key] for key in ("artifact_kind", "change_id", "package_id")})
    receipt.update(tree_sha=tree, status="verified")
    proposed = {"receipt": receipt, "parent": data["expected_parent"], "timestamp": data["timestamp"],
                "state": "prepared", "request_digest": digest, "index_before": rev_index(req)}
    commit = rev_commit_bytes(proposed)
    receipt["commit_sha"] = rev_object("commit", commit)
    objects.append(("commit", receipt["commit_sha"], commit))
    if existing:
        if existing["receipt"] != receipt:
            raise ValueError("PM_REVISION_INTENT_CHANGED")
        proposed = existing
    rev_pending_guard(req, proposed, files)
    if not write:
        # No branch/index effect can be replayed without these exact old/own
        # resource facts; the mutation path checks them again under its gate.
        return {"receipt": None, "resume_safe": True, "effect_started": existing is not None}
    print(json.dumps({"locked": True}), flush=True)
    if json.loads(sys.stdin.readline() or "{}").get("proceed") is not True:
        raise ValueError("REPOSITORY_GATE_REQUIRED")
    identity(req)
    rev_pending_guard(req, proposed, files)
    if not existing:
        rows.append(proposed)
        rev_save(req, rows)
    for kind, sha, content in objects:
        actual = rev_git(req["clone_path"], "hash-object", "-w", "-t", kind, "--stdin", data=content).decode().strip()
        if actual != sha:
            raise ValueError("PM_REVISION_OBJECT_CHANGED")
    for relative, content in files.items():
        with rev_parent(req["worktree_path"], relative, create=True) as fd:
            existing_bytes = pm_read(fd, relative.rsplit("/", 1)[1], missing=True)
            if existing_bytes is None:
                pm_write(fd, relative.rsplit("/", 1)[1], content)
            elif existing_bytes != content:
                raise ValueError("PM_REVISION_FILES_CHANGED")
    if git(req["clone_path"], "rev-parse", "--verify", rev_ref(proposed), optional=True) is None:
        git(req["clone_path"], "update-ref", rev_ref(proposed), receipt["commit_sha"], "")
    current = rev_pending_guard(req, proposed, files)
    if current == proposed["parent"]:
        git(req["clone_path"], "update-ref", "refs/heads/" + req["branch"], receipt["commit_sha"], proposed["parent"])
    # Only this proven PM-owned index is synchronized; objects were built without
    # using it, so no staged draft or unrelated path can enter the commit.
    rev_pending_guard(req, proposed, files)
    git(req["worktree_path"], "read-tree", "--reset", receipt["commit_sha"])
    git(req["worktree_path"], "update-index", "--refresh")
    proposed["index_after"] = rev_index(req)
    proposed["state"] = "complete"
    rev_verify(req, proposed)
    rev_save(req, rows)
    pm_carrier(req)
    return receipt


def rev_execute(req):
    if req["phase"] not in {"pm-revision", "read.pm-revision"}:
        return pm_execute(req)
    root, clone = req["managed_root"], req["clone_path"]
    real(root)
    real(clone)
    if clone != root + "/batc-published-" + req["operation_id"][3:]:
        raise ValueError("REPOSITORY_DESTINATION_UNPROVEN")
    with directory(root) as root_fd, directory(clone) as clone_fd:
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        fcntl.flock(clone_fd, fcntl.LOCK_EX)
        LOCK_FDS.extend((root_fd, clone_fd))
        try:
            if os.stat(root) != os.fstat(root_fd) or os.stat(clone) != os.fstat(clone_fd):
                raise ValueError("REPOSITORY_DESTINATION_UNPROVEN")
            identity(req)
            if not pinned(req):
                raise ValueError("REPOSITORY_PIN_MISMATCH")
            return rev_transaction(req, write=req["phase"] == "pm-revision")
        finally:
            LOCK_FDS.remove(clone_fd)
            LOCK_FDS.remove(root_fd)


if __name__ == "__main__":
    try:
        request = json.loads(zlib.decompress(base64.b64decode(sys.argv[1])))
        print(json.dumps({"result": rev_execute(request)}), flush=True)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        exc = sys.exc_info()[1]
        code = str(exc) if str(exc).startswith(("REPOSITORY_", "PM_")) else "PM_HOST_UNCERTAIN"
        print(json.dumps({"error": code}), flush=True)
