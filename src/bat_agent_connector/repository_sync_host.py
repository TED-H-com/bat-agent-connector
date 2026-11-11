"""Fixed published-repository Git operations. Transported by the existing SSH runner.

Only operation-derived paths and configured remote URLs reach this private script.
No source checkout is named. No hooks, filters, recursive submodules or push.
"""
from __future__ import annotations

import base64
import contextlib
import fcntl
import json
import os
import re
import stat
import subprocess
import sys

LOCK_FDS = []


def real(path):
    if not os.path.isdir(path) or os.path.realpath(path) != path:
        raise ValueError("REPOSITORY_DESTINATION_UNPROVEN")


@contextlib.contextmanager
def directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def git(repo, *args, network=False, optional=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
               SSH_ASKPASS_REQUIRE="never", GIT_LFS_SKIP_SMUDGE="1")
    if not network:
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    result = subprocess.run(["git", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",  # noqa: S603,S607
        "-c", "core.fsmonitor=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
        "-c", "protocol.ext.allow=never", "-c", "fetch.recurseSubmodules=false", "-C", repo, *args],
        capture_output=True, env=env, timeout=1800, pass_fds=tuple(LOCK_FDS))
    if result.returncode:
        if optional:
            return None
        raise ValueError("REPOSITORY_GIT_FAILED")
    return result.stdout.decode("utf-8", errors="strict").strip()


def identity(req):
    clone = req["clone_path"]
    real(clone)
    common = clone + "/.git"
    real(common)
    for parent, dirs, files in os.walk(common, followlinks=False):
        for name in dirs + files:
            item = os.lstat(os.path.join(parent, name))
            if stat.S_ISLNK(item.st_mode) or (not stat.S_ISDIR(item.st_mode) and (
                    not stat.S_ISREG(item.st_mode) or item.st_nlink != 1)):
                raise ValueError("REPOSITORY_CLONE_TAMPERED")
    if any(os.path.lexists(common + "/" + n) for n in ("objects/info/alternates", "objects/info/http-alternates",
            "info/grafts", "shallow", "commondir")) or git(clone, "for-each-ref", "refs/replace"):
        raise ValueError("REPOSITORY_CLONE_TAMPERED")
    allowed = re.compile(r"(?:core\.(?:repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode)|batc\.[a-z-]+)")
    if any(not allowed.fullmatch(k) for k in git(clone, "config", "--local", "--name-only", "--list").splitlines()):
        raise ValueError("REPOSITORY_CLONE_TAMPERED")
    for key, value in req["markers"].items():
        if git(clone, "config", "--local", "--get-all", "batc." + key, optional=True) != value:
            raise ValueError("REPOSITORY_CLONE_NOT_OURS")
    if git(clone, "rev-parse", "--path-format=absolute", "--git-common-dir") != common:
        raise ValueError("REPOSITORY_CLONE_NOT_OURS")


def pinned(req):
    clone, sha = req["clone_path"], req["source_sha"]
    ref = "refs/batc/published/" + req["operation_id"][3:]
    seen = git(clone, "rev-parse", "--verify", ref, optional=True)
    if seen is None:
        return False
    if seen != sha or git(clone, "cat-file", "-t", sha) != "commit":
        raise ValueError("REPOSITORY_PIN_MISMATCH")
    return True


def carrier(req):
    clone, path, branch = req["clone_path"], req["worktree_path"], req["branch"]
    if not os.path.lexists(path):
        return None
    real(path)
    if (git(path, "rev-parse", "--path-format=absolute", "--git-common-dir") != clone + "/.git"
            or git(path, "symbolic-ref", "--short", "HEAD") != branch):
        raise ValueError("REPOSITORY_CARRIER_CHANGED")
    head = git(path, "rev-parse", "HEAD")
    if head != req["source_sha"] or git(path, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("REPOSITORY_CARRIER_CHANGED")
    return {"clone_path": clone, "worktree_path": path, "branch": branch, "head": head}


def fetch_source(req):
    clone = req["clone_path"]
    remote = git(clone, "ls-remote", "--refs", req["remote_url"], req["source_ref"], network=True)
    matches = [r.split("\t", 1)[0] for r in remote.splitlines() if r.endswith("\t" + req["source_ref"])]
    if matches != [req["source_sha"]]:
        raise ValueError("REPOSITORY_REF_CHANGED")
    git(clone, "fetch", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules",
        req["remote_url"], req["source_sha"], network=True)
    if git(clone, "cat-file", "-t", req["source_sha"]) != "commit":
        raise ValueError("REPOSITORY_COMMIT_UNPROVEN")


def execute(req, *, observe=lambda stage, req, value: value, fetch_source=fetch_source):
    root, clone, phase = req["managed_root"], req["clone_path"], req["phase"]
    real(root)
    if (os.path.dirname(clone) != root or not re.fullmatch(r"batc-published-[0-9a-f]{32}", os.path.basename(clone))
            or not re.fullmatch(r"op_[0-9a-f]{32}", req["operation_id"])
            or clone != root + "/batc-published-" + req["operation_id"][3:]
            or req["worktree_path"] != clone + "/.bat-worktrees/batc-published-" + req["operation_id"][3:15]
            or req["branch"] != "batc/published-" + req["operation_id"][3:15]
            or not re.fullmatch(r"[0-9a-f]{40}", req["source_sha"])):
        raise ValueError("REPOSITORY_DESTINATION_UNPROVEN")
    # Lock an existing directory inode; no lock-file link or pathname replacement.
    with directory(root) as root_fd:
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        LOCK_FDS.append(root_fd)
        real(root)
        if os.stat(root) != os.fstat(root_fd):
            raise ValueError("REPOSITORY_DESTINATION_UNPROVEN")
        # Cleanup locks this same clone inode. Root lock serializes its first creation.
        exists = os.path.lexists(clone)
        observe("existing", req, exists)
        if exists:
            real(clone)
        if phase.startswith("read.") and not exists:
            return {"exists": False}
        if not exists:
            print(json.dumps({"locked": True}), flush=True)
            if json.loads(sys.stdin.readline() or "{}").get("proceed") is not True:
                raise ValueError("REPOSITORY_GATE_REQUIRED")
            os.mkdir(clone)
            git(clone, "init", "--quiet", "--template=")
            for key, value in req["markers"].items():
                git(clone, "config", "--local", "batc." + key, value)
        fd = os.open(clone, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            LOCK_FDS.append(fd)
            identity(req)
            if os.stat(clone) != os.fstat(fd):
                raise ValueError("REPOSITORY_DESTINATION_UNPROVEN")
            if phase.startswith("read."):
                return observe("result", req, {"exists": True, "pinned": pinned(req), "carrier": carrier(req) if phase == "read.carrier" else None})
            if exists:
                print(json.dumps({"locked": True}), flush=True)
                if json.loads(sys.stdin.readline() or "{}").get("proceed") is not True:
                    raise ValueError("REPOSITORY_GATE_REQUIRED")
            if phase == "fetch":
                if not pinned(req):
                    fetch_source(req)
                    git(clone, "update-ref", "refs/batc/published/" + req["operation_id"][3:], req["source_sha"], "")
                return observe("result", req, {"clone_path": clone, "source_sha": req["source_sha"], "pinned": True})
            if phase != "prepare" or not pinned(req):
                raise ValueError("REPOSITORY_PIN_MISMATCH")
            observed = carrier(req)
            if observed:
                return observe("result", req, observed)
            parent = clone + "/.bat-worktrees"
            if os.path.lexists(parent):
                real(parent)
            else:
                os.mkdir(parent)
            # A leftover branch without a registered carrier is not proof of no effect.
            if git(clone, "show-ref", "--verify", "refs/heads/" + req["branch"], optional=True):
                raise ValueError("REPOSITORY_CARRIER_UNPROVEN")
            git(clone, "worktree", "add", "--quiet", "-b", req["branch"], req["worktree_path"], req["source_sha"])
            return observe("result", req, carrier(req))
        finally:
            LOCK_FDS.remove(fd)
            os.close(fd)


if __name__ == "__main__":
    try:
        request = json.loads(base64.b64decode(sys.argv[1]))
        print(json.dumps({"result": execute(request)}), flush=True)
    except (ValueError, OSError, subprocess.SubprocessError):
        # No raw Git error, remote URL, environment or credential output.
        exc = sys.exc_info()[1]
        code = str(exc) if str(exc).startswith("REPOSITORY_") else "REPOSITORY_HOST_UNCERTAIN"
        print(json.dumps({"error": code}), flush=True)
