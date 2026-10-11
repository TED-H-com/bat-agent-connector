"""Private PM carrier extension, appended to repository_sync_host by the runner.

Git setup uses the shared identity/locking helpers. Only fixed service-generated
JSON draft paths may be created. A manifest outside the worktree records their
original digests; an arbitrary pre-existing file is never adopted or overwritten.
"""
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import zlib
from contextlib import ExitStack

if __name__ != "__main__":
    from .repository_sync_host import LOCK_FDS, directory, execute, git, identity, pinned, real

_MANIFEST = "batc-pm-drafts.json"
_DRAFT = re.compile(r"docs/pm/drafts/pmr_[0-9a-f]{32}/(?:context|reply)\.json")


def pm_json(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def pm_directory(parent_fd, name, *, create=False):
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
    try:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        raise
    except OSError:
        raise ValueError("PM_SNAPSHOT_PATH_UNSAFE") from None
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("PM_SNAPSHOT_PATH_UNSAFE")
    return fd


@contextlib.contextmanager
def pm_parent(path, relative, *, create=False):
    if not _DRAFT.fullmatch(relative):
        raise ValueError("PM_SNAPSHOT_PATH_UNSAFE")
    with ExitStack() as stack:
        root = stack.enter_context(directory(path))
        fd = root
        for component in relative.split("/")[:-1]:
            child = pm_directory(fd, component, create=create)
            stack.callback(os.close, child)
            fd = child
        yield fd


def pm_read(fd, name, *, missing=False, maximum=512_000):
    try:
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        if missing:
            return None
        raise ValueError("PM_SNAPSHOT_MISSING") from None
    try:
        before = os.fstat(source)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            raise ValueError("PM_SNAPSHOT_PATH_UNSAFE")
        chunks = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(source, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(source)
        content = b"".join(chunks)
        if len(content) > maximum or (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("PM_SNAPSHOT_CHANGED")
        return content
    finally:
        os.close(source)


def pm_manifest(req):
    with directory(req["clone_path"] + "/.git") as fd:
        data = pm_read(fd, _MANIFEST, missing=True, maximum=4_000_000)
    if data is None:
        return {}
    try:
        value = json.loads(data)
        if (value.get("schema") != "batc.pm.drafts.v1" or value.get("workspace_id") != req["pm_workspace_id"]
                or value.get("binding_digest") != req["binding_digest"] or not isinstance(value.get("files"), dict)):
            raise ValueError("PM_SNAPSHOT_MANIFEST_CHANGED")
        for path, record in value["files"].items():
            if (not _DRAFT.fullmatch(path) or not isinstance(record, dict) or set(record) != {"sha256", "size", "operation_id"}
                    or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
                    or type(record["size"]) is not int or not 0 < record["size"] <= 512_000
                    or not re.fullmatch(r"op_[0-9a-f]{32}", record["operation_id"])):
                raise ValueError("PM_SNAPSHOT_MANIFEST_CHANGED")
        return value["files"]
    except (TypeError, KeyError, json.JSONDecodeError):
        raise ValueError("PM_SNAPSHOT_MANIFEST_CHANGED") from None


def pm_write(fd, name, content, *, replace=False):
    # Fixed pending name is only touched when its original bytes match exactly.
    pending = ".batc-pm-" + hashlib.sha256(content).hexdigest() + ".pending"
    try:
        dest = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    except FileExistsError:
        if pm_read(fd, pending, maximum=4_000_000) != content:
            raise ValueError("PM_SNAPSHOT_PENDING_CHANGED") from None
    else:
        try:
            with os.fdopen(dest, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(dest)
        finally:
            os.close(dest)
    if replace:
        os.replace(pending, name, src_dir_fd=fd, dst_dir_fd=fd)
    else:
        # Exclusive final creation avoids replacing even a same-name foreign file.
        # If interrupted after linking, read-back remains conservative until the
        # original pending link is reconciled; it never rewrites the target.
        os.link(pending, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.unlink(pending, dir_fd=fd)
    os.fsync(fd)


def pm_save_manifest(req, files):
    content = pm_json({"schema": "batc.pm.drafts.v1", "workspace_id": req["pm_workspace_id"],
                       "binding_digest": req["binding_digest"], "files": files})
    with directory(req["clone_path"] + "/.git") as fd:
        # Validate old inode before replacing this service-owned metadata file.
        pm_read(fd, _MANIFEST, missing=True, maximum=4_000_000)
        pm_write(fd, _MANIFEST, content, replace=True)


def pm_snapshot_bytes(req):
    snapshot = req["snapshot"]
    try:
        relative = snapshot["path"]
        content = base64.b64decode(snapshot["content"], validate=True)
        doc = json.loads(content)
        if (not _DRAFT.fullmatch(relative) or relative != "docs/pm/drafts/" + snapshot["run_id"] + "/" + snapshot["kind"] + ".json"
                or len(content) != snapshot["size"] or len(content) > 512_000
                or hashlib.sha256(content).hexdigest() != snapshot["sha256"]
                or doc.get("schema") != "batc.pm.draft.v1" or doc.get("status") != "draft"
                or any(doc.get(key) != snapshot[key] for key in ("run_id", "kind", "operation_id", "payload_digest"))
                or doc.get("workspace_id") != req["pm_workspace_id"] or doc.get("server_id") != req["server_id"]
                or doc.get("controller_id") != req["execution_authority"]["controller_id"]
                or any(doc.get(key) != req[key] for key in ("repository_id", "source_sha", "binding_digest"))
                or hashlib.sha256(json.dumps(doc["payload"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    != snapshot["payload_digest"]):
            raise ValueError("PM_SNAPSHOT_INVALID")
        return content
    except (ValueError, TypeError, KeyError):
        raise ValueError("PM_SNAPSHOT_INVALID") from None


def pm_carrier(req, *, pending=None):
    path, clone = req["worktree_path"], req["clone_path"]
    if not os.path.lexists(path):
        return None
    real(path)
    with directory(path) as fd:
        pm_read(fd, ".git", maximum=4096)
    if (git(path, "rev-parse", "--path-format=absolute", "--git-common-dir") != clone + "/.git"
            or git(path, "symbolic-ref", "--short", "HEAD") != req["branch"]
            or git(path, "rev-parse", "HEAD") != req["source_sha"]
            or git(path, "diff-index", "--raw", "HEAD", "--")):
        raise ValueError("PM_WORKTREE_CHANGED")
    files = pm_manifest(req)
    untracked = set(filter(None, git(path, "ls-files", "--others", "-z").split("\0")))
    if untracked - set(files):
        raise ValueError("PM_WORKTREE_UNREGISTERED_FILES")
    for relative, record in files.items():
        try:
            with pm_parent(path, relative) as fd:
                data = pm_read(fd, relative.rsplit("/", 1)[1], missing=relative == pending)
        except FileNotFoundError:
            if relative == pending:
                continue
            raise ValueError("PM_SNAPSHOT_MISSING") from None
        if data is None and relative == pending:
            continue
        if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("PM_SNAPSHOT_CHANGED")
    return {"clone_path": clone, "worktree_path": path, "branch": req["branch"], "head": req["source_sha"]}


def pm_snapshot(req, *, write):
    content = pm_snapshot_bytes(req)
    item = req["snapshot"]
    relative = item["path"]
    files = pm_manifest(req)
    record = {key: item[key] for key in ("sha256", "size", "operation_id")}
    if relative in files and files[relative] != record:
        raise ValueError("PM_SNAPSHOT_CONFLICT")
    # A tracked path belongs to the fixed source revision, even if bytes match.
    if git(req["worktree_path"], "ls-files", "--", relative):
        raise ValueError("PM_SNAPSHOT_TRACKED_PATH")
    try:
        with pm_parent(req["worktree_path"], relative) as fd:
            existing = pm_read(fd, relative.rsplit("/", 1)[1], missing=True)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if relative not in files or existing != content:
            raise ValueError("PM_SNAPSHOT_CONFLICT")
    elif write:
        # Journal step already contains this immutable digest before the host
        # intent is recorded. Interrupted partial writes retain that original key.
        with pm_parent(req["worktree_path"], relative, create=True) as fd:
            files[relative] = record
            pm_save_manifest(req, files)
            pm_write(fd, relative.rsplit("/", 1)[1], content)
    else:
        return {"snapshot": None}
    result = {key: item[key] for key in ("path", "kind", "run_id", "operation_id", "sha256", "size", "payload_digest")}
    result.update(worktree_path=req["worktree_path"], status="draft")
    return result if write else {"snapshot": result}


def pm_base_execute(req):
    before = list(LOCK_FDS)
    try:
        return execute(req)
    finally:
        LOCK_FDS[:] = before


def pm_execute(req):
    phase = req["phase"]
    if phase in {"fetch", "prepare", "read.fetch"}:
        return pm_base_execute(req)
    if phase not in {"read.pm-carrier", "read.pm-snapshot", "pm-snapshot"}:
        raise ValueError("PM_HOST_PHASE_INVALID")
    root, clone = req["managed_root"], req["clone_path"]
    # Reuse the original helper's strict operation-derived path validation.
    state = pm_base_execute({**req, "phase": "read.fetch"})
    if not state.get("exists"):
        return {"exists": False, "carrier": None, "snapshot": None}
    with directory(root) as root_fd, directory(clone) as clone_fd:
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        fcntl.flock(clone_fd, fcntl.LOCK_EX)
        LOCK_FDS.extend((root_fd, clone_fd))
        try:
            real(root)
            real(clone)
            if os.stat(root) != os.fstat(root_fd) or os.stat(clone) != os.fstat(clone_fd):
                raise ValueError("REPOSITORY_DESTINATION_UNPROVEN")
            identity(req)
            if not pinned(req):
                raise ValueError("REPOSITORY_PIN_MISMATCH")
            pending = req["snapshot"]["path"] if "snapshot" in req else None
            carrier_result = pm_carrier(req, pending=pending)
            if phase == "read.pm-carrier":
                return {"exists": True, "carrier": carrier_result}
            if carrier_result is None:
                raise ValueError("PM_WORKTREE_MISSING")
            if phase == "pm-snapshot":
                print(json.dumps({"locked": True}), flush=True)
                if json.loads(sys.stdin.readline() or "{}").get("proceed") is not True:
                    raise ValueError("REPOSITORY_GATE_REQUIRED")
                # Final local checks after the central writer/member check.
                identity(req)
                pm_carrier(req, pending=pending)
            return pm_snapshot(req, write=phase == "pm-snapshot")
        finally:
            LOCK_FDS.remove(clone_fd)
            LOCK_FDS.remove(root_fd)


if __name__ == "__main__":
    try:
        request = json.loads(zlib.decompress(base64.b64decode(sys.argv[1])))
        print(json.dumps({"result": pm_execute(request)}), flush=True)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        exc = sys.exc_info()[1]
        code = str(exc) if str(exc).startswith(("REPOSITORY_", "PM_")) else "PM_HOST_UNCERTAIN"
        print(json.dumps({"error": code}), flush=True)
