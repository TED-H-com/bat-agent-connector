"""Fixed, opt-in Connector cold-start guard; never constructs TaskDaemon or Journal.

Deployed separately as the reviewed ``bat-connector-bootstrap-v1`` SSH command.
Only its protected server recipe chooses files/service. Stdin carries identity and
query/ensure, not executable arguments. This module performs no deployment.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shlex
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from bat_agent_connector import hidden_process

LIMIT = 16_384
MAX_DATABASE = 256 * 1024 * 1024
MAX_WAL = 64 * 1024 * 1024
SYSTEMCTL = "/usr/bin/systemctl"
SERVER_RECIPE = Path("/etc/bat-agent-connector/bootstrap-v1.json")
PROPERTIES = (
    "Id", "LoadState", "ActiveState", "SubState", "MainPID", "FragmentPath", "DropInPaths",
    "NeedDaemonReload", "Environment", "EnvironmentFiles", "PassEnvironment", "UnsetEnvironment",
    "ExecStart", "ExecStartPre", "ExecStartPost", "ExecCondition", "RootDirectory", "RootImage",
    "User", "DynamicUser", "Type", "Restart", "Job",
)


class Refused(Exception):
    """Fixed private-to-public code only; never include remote output or paths."""


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def pairs(items):
        out = {}
        seen = set()
        for key, value in items:
            if key.casefold() in seen:
                raise Refused("BOOTSTRAP_PROTOCOL_INVALID")
            seen.add(key.casefold())
            out[key] = value
        return out
    try:
        if len(raw) > LIMIT:
            raise ValueError
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, TypeError):
        raise Refused("BOOTSTRAP_PROTOCOL_INVALID") from None


def _fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise Refused("BOOTSTRAP_PROTOCOL_INVALID")


def _hex(value, count=64):
    return isinstance(value, str) and re.fullmatch(rf"[a-f0-9]{{{count}}}", value) is not None


def _path(value):
    # Deliberately limited literal systemd argv/environment; no quoting/specifiers.
    if (not isinstance(value, str) or len(value) > 4096
            or not re.fullmatch(r"/[A-Za-z0-9._/-]+", value)
            or any(part in {"", ".", ".."} for part in value[1:].split("/"))):
        raise Refused("BOOTSTRAP_RECIPE_UNSUPPORTED")
    return Path(value)


def _secure_path(path, uid, directory=False):
    for parent in [*reversed(path.parents), path]:
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid not in {0, uid} or info.st_mode & 0o022 and not (parent != path and info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise Refused("BOOTSTRAP_STATE_UNPROVEN")
        if parent != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise Refused("BOOTSTRAP_STATE_UNPROVEN")
    return info


def _read(path, uid, limit, expected=None):
    before = _secure_path(path, uid)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        identity = (info.st_dev, info.st_ino)
        if (not stat.S_ISREG(info.st_mode) or identity != (before.st_dev, before.st_ino)
                or expected is not None and identity != tuple(expected)
                or info.st_size > limit):
            raise Refused("BOOTSTRAP_STATE_UNPROVEN")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        if (len(raw) > limit or (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or path.lstat() != after):
            raise Refused("BOOTSTRAP_STATE_CHANGED")
        return raw, after
    finally:
        os.close(fd)


class Recipe:
    """Native server configuration. No Debug/repr exposing private fields."""
    def __init__(self, raw, path=None):
        doc = _json(raw)
        _fields(doc, ("schema_version", "service_id", "unit", "unit_path", "unit_sha256", "uid",
                      "executable", "config_path", "state_directory", "journal_path", "journal_identity",
                      "lock_identity", "port"))
        if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
                or type(doc["uid"]) is not int or doc["uid"] < 0
                or type(doc["port"]) is not int or not 1 <= doc["port"] <= 65535
                or not isinstance(doc["service_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", doc["service_id"])
                or not isinstance(doc["unit"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.service", doc["unit"])
                or not _hex(doc["unit_sha256"])):
            raise Refused("BOOTSTRAP_RECIPE_UNSUPPORTED")
        for name in ("journal_identity", "lock_identity"):
            if (not isinstance(doc[name], list) or len(doc[name]) != 2
                    or any(type(n) is not int or n < 0 for n in doc[name]) or doc[name][1] == 0):
                raise Refused("BOOTSTRAP_RECIPE_UNSUPPORTED")
        for name in ("unit_path", "executable", "config_path", "state_directory", "journal_path"):
            _path(doc[name])
        if Path(doc["journal_path"]).parent != Path(doc["state_directory"]):
            raise Refused("BOOTSTRAP_RECIPE_UNSUPPORTED")
        self.data = doc
        self.digest = _digest(raw)
        self._raw = raw
        self._path = path

    def verify_current(self):
        if self._path is not None:
            current, _ = _read(self._path, self.data["uid"], LIMIT)
            if current != self._raw:
                raise Refused("BOOTSTRAP_RECIPE_CHANGED")

    def expected_request(self):
        d = self.data
        return {"recipe_sha256": self.digest, "service_id": d["service_id"],
                "state_directory": d["state_directory"], "journal_path": d["journal_path"],
                "owner_uid": d["uid"]}

    def argv(self):
        d = self.data
        return [d["executable"], "--config", d["config_path"], "serve", "--host", "127.0.0.1",
                "--port", str(d["port"]), "--db", d["journal_path"]]


class Systemd:
    """Only fixed read/start invocations. Output stays internal and is bounded."""
    def _call(self, arguments):
        env = {key: os.environ[key] for key in ("HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS") if key in os.environ}
        env.update({"LC_ALL": "C", "PATH": "/usr/bin:/bin"})
        # communicate() would buffer arbitrarily large output. The temporary file
        # caps retained output reads; RLIMIT_FSIZE also bounds the subprocess file.
        import resource
        def bound():
            resource.setrlimit(resource.RLIMIT_FSIZE, (LIMIT, LIMIT))
        with tempfile.TemporaryFile() as output:
            try:
                result = hidden_process.run([SYSTEMCTL, "--user", "--no-pager", *arguments],  # noqa: S603
                                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                                        env=env, timeout=3, check=False, preexec_fn=bound)
            except (OSError, subprocess.TimeoutExpired):
                raise Refused("BOOTSTRAP_SERVICE_UNKNOWN") from None
            output.seek(0)
            raw = output.read(LIMIT + 1)
        if result.returncode or len(raw) > LIMIT:
            raise Refused("BOOTSTRAP_SERVICE_UNKNOWN")
        return raw

    def query(self, unit):
        raw = self._call(["show", "--all", "--property=" + ",".join(PROPERTIES), "--", unit])
        try:
            pairs = [line.split("=", 1) for line in raw.decode("utf-8").splitlines()]
            if any(len(pair) != 2 for pair in pairs) or len({p[0] for p in pairs}) != len(pairs):
                raise ValueError
            values = dict(pairs)
            # systemd v257's special array printers emit no line when empty,
            # even with --all. Only these reviewed optional arrays may be absent;
            # the pinned unit parser independently forbids their directives.
            for key in ("EnvironmentFiles", "ExecStartPre", "ExecStartPost", "ExecCondition"):
                values.setdefault(key, "")
            return values
        except (ValueError, UnicodeError):
            raise Refused("BOOTSTRAP_UNIT_UNPROVEN") from None

    def start(self, unit):
        self._call(["start", "--no-block", "--", unit])


def _unit(recipe, view):
    d = recipe.data
    _fields(view, PROPERTIES)
    raw, _ = _read(_path(d["unit_path"]), d["uid"], LIMIT)
    if _digest(raw) != d["unit_sha256"]:
        raise Refused("BOOTSTRAP_UNIT_CHANGED")
    # Only this simple direct service form is proven. Loaded environment must
    # explicitly bind the same registry/lock directory and Connector config.
    environment = {"BATC_STATE_DIR": d["state_directory"], "BATC_CONFIG": d["config_path"]}
    try:
        env_pairs = [part.split("=", 1) for part in shlex.split(view["Environment"])]
        effective_env = dict(env_pairs)
    except ValueError:
        raise Refused("BOOTSTRAP_UNIT_UNPROVEN") from None
    if (len(env_pairs) != len(environment) or effective_env != environment
            or view["Id"] != d["unit"] or view["LoadState"] != "loaded"
            or view["FragmentPath"] != d["unit_path"] or view["NeedDaemonReload"] != "no"
            or view["Type"] != "simple" or view["Restart"] != "no" or view["DynamicUser"] != "no"
            or view["User"] not in {"", str(d["uid"])}
            or any(view[k] for k in ("DropInPaths", "EnvironmentFiles", "PassEnvironment", "UnsetEnvironment",
                                    "ExecStartPre", "ExecStartPost", "ExecCondition", "RootDirectory", "RootImage"))):
        raise Refused("BOOTSTRAP_UNIT_UNPROVEN")
    prefix = "{ path=" + d["executable"] + " ; argv[]=" + " ".join(recipe.argv()) + " ; ignore_errors=no ;"
    if (not view["ExecStart"].startswith(prefix) or not view["ExecStart"].endswith(" }")
            or "{" in view["ExecStart"][1:] or "}" in view["ExecStart"][:-1]):
        raise Refused("BOOTSTRAP_UNIT_UNPROVEN")
    # Pinning alone is not semantic validation: reject directives that can
    # introduce other commands, environment sources, root/state remapping, or specifiers.
    try:
        section = None
        entries = {}
        for line in raw.decode("utf-8").splitlines():
            if not line or line.startswith(("#", ";")):
                continue
            if line in {"[Unit]", "[Service]", "[Install]"}:
                section = line
                continue
            key, value = line.split("=", 1)
            allowed = {"[Unit]": {"Description"}, "[Service]": {"Type", "ExecStart", "Environment", "Restart"},
                       "[Install]": {"WantedBy"}}.get(section, set())
            if key not in allowed or (section, key) in entries or any(c in value for c in ("%", "$", "\\")):
                raise ValueError
            entries[(section, key)] = value
        expected = {("[Service]", "Type"): "simple", ("[Service]", "Restart"): "no",
                    ("[Service]", "ExecStart"): " ".join(recipe.argv()),
                    ("[Service]", "Environment"): " ".join(f"{k}={v}" for k, v in environment.items())}
        if any(entries.get(k) != v for k, v in expected.items()):
            raise ValueError
    except (ValueError, UnicodeError):
        raise Refused("BOOTSTRAP_RECIPE_UNSUPPORTED") from None


def _database(recipe):
    """Validate a bounded DB+WAL snapshot; never create source SHM/journal files."""
    d = recipe.data
    path = _path(d["journal_path"])
    raw, identity = _read(path, d["uid"], MAX_DATABASE, d["journal_identity"])
    if not raw.startswith(b"SQLite format 3\0"):
        raise Refused("BOOTSTRAP_JOURNAL_UNPROVEN")
    wal_path = Path(str(path) + "-wal")
    try:
        wal, wal_identity = _read(wal_path, d["uid"], MAX_WAL)
    except FileNotFoundError:
        wal, wal_identity = None, None
    # A hot rollback journal may require recovery. This helper never performs it.
    try:
        Path(str(path) + "-journal").lstat()
    except FileNotFoundError:
        pass
    else:
        raise Refused("BOOTSTRAP_JOURNAL_UNPROVEN")
    deadline = time.monotonic() + 2
    with tempfile.TemporaryDirectory(prefix="bat-bootstrap-proof-") as temporary:
        copy = Path(temporary) / "snapshot.sqlite3"
        copy.write_bytes(raw)
        if wal is not None:
            Path(str(copy) + "-wal").write_bytes(wal)
        try:
            with contextlib.closing(sqlite3.connect(copy.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as db:
                db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                db.execute("PRAGMA query_only=ON")
                names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"tasks", "commands", "operations", "daemon_owner"}.issubset(names):
                    raise Refused("BOOTSTRAP_JOURNAL_UNPROVEN")
                if db.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                    raise Refused("BOOTSTRAP_JOURNAL_UNPROVEN")
        except sqlite3.Error:
            raise Refused("BOOTSTRAP_JOURNAL_UNPROVEN") from None
    if path.lstat() != identity:
        raise Refused("BOOTSTRAP_STATE_CHANGED")
    try:
        current_wal = wal_path.lstat()
    except FileNotFoundError:
        current_wal = None
    if current_wal != wal_identity:
        raise Refused("BOOTSTRAP_STATE_CHANGED")


def _lock(recipe):
    d = recipe.data
    path = _path(d["state_directory"]) / "task-daemon.lock"
    before = _secure_path(path, d["uid"])
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or [info.st_dev, info.st_ino] != d["lock_identity"]
            or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
        os.close(fd)
        raise Refused("BOOTSTRAP_OWNER_UNPROVEN")
    return fd



def _proc(path, bound):
    with open(path, "rb") as stream:
        raw = stream.read(bound + 1)
    if len(raw) > bound:
        raise Refused("BOOTSTRAP_OWNER_UNPROVEN")
    return raw


def _running_owner(recipe, view, fd, systemd):
    """Kernel flock owner + current unit PID/incarnation + actual fixed command.

    Stale task-service.json/daemon_owner rows alone never grant start authority.
    Unknown evidence returns an owner-present refusal, never a takeover.
    """
    try:
        d = recipe.data
        if view["ActiveState"] != "active" or view["SubState"] != "running" or not re.fullmatch(r"[1-9][0-9]{0,9}", view["MainPID"]):
            return False
        pid = int(view["MainPID"])
        before = _proc(f"/proc/{pid}/stat", LIMIT).rsplit(b")", 1)[1].split()[19]
        fields = _proc(f"/proc/{pid}/status", LIMIT).decode().splitlines()
        if next(line for line in fields if line.startswith("Uid:")).split()[1:] != [str(d["uid"])] * 4:
            return False
        argv = _proc(f"/proc/{pid}/cmdline", LIMIT).decode().rstrip("\0").split("\0")
        expected = recipe.argv()
        if argv != expected:
            entry, _ = _read(_path(d["executable"]), d["uid"], LIMIT)
            interpreter = entry.splitlines()[0].decode().removeprefix("#!")
            _path(interpreter)
            if not entry.startswith(b"#!/") or argv != [interpreter, *expected]:
                return False
        raw, _ = _read(_path(d["state_directory"]) / "task-service.json", d["uid"], LIMIT)
        pointer = _json(raw)
        if (not isinstance(pointer, dict) or type(pointer.get("pid")) is not int or pointer["pid"] != pid
                or pointer.get("db_path") != d["journal_path"]
                or pointer.get("lease_path") != str(_path(d["state_directory"]) / "task-daemon.lock")
                or not _hex(pointer.get("owner_id"), 32)):
            return False
        lock = os.fstat(fd)
        lock_id = f"{os.major(lock.st_dev):02x}:{os.minor(lock.st_dev):02x}:{lock.st_ino}"
        rows = [row.split() for row in _proc("/proc/locks", 1024 * 1024).decode().splitlines()]
        if not any(len(row) == 8 and row[1:6] == ["FLOCK", "ADVISORY", "WRITE", str(pid), lock_id] for row in rows):
            return False
        # The active journal may change normally. Check its original inode and
        # SQLite header without using a stale metadata row as ownership proof.
        journal = _path(d["journal_path"])
        _secure_path(journal, d["uid"])
        with open(journal, "rb") as db:
            info = os.fstat(db.fileno())
            if [info.st_dev, info.st_ino] != d["journal_identity"] or db.read(16) != b"SQLite format 3\0":
                return False
        if systemd.query(d["unit"]) != view:
            return False
        after = _proc(f"/proc/{pid}/stat", LIMIT).rsplit(b")", 1)[1].split()[19]
        return before == after
    except (OSError, ValueError, IndexError, StopIteration, Refused):
        return False


def handle(recipe, request, systemd=None):
    """The only effect is one fixed user-unit start after complete fresh proof."""
    expected = recipe.expected_request()
    _fields(request, (*expected, "protocol", "request_id", "generation", "attempt", "action"))
    if (any(request[k] != v or type(request[k]) is not type(v) for k, v in expected.items())
            or type(request["protocol"]) is not int or request["protocol"] != 1
            or not _hex(request["request_id"], 32) or not _hex(request["generation"])
            or type(request["attempt"]) is not int or not 1 <= request["attempt"] <= 4
            or not isinstance(request["action"], str) or request["action"] not in {"query", "ensure"}):
        raise Refused("BOOTSTRAP_BINDING_CHANGED")
    recipe.verify_current()
    d = recipe.data
    if os.getuid() != d["uid"] or os.geteuid() != d["uid"]:
        raise Refused("BOOTSTRAP_OWNER_UNPROVEN")
    systemd = systemd or Systemd()
    response = {**request, "state": "unknown", "owner": "unknown", "ensure_accepted": False}
    view = systemd.query(d["unit"])
    _unit(recipe, view)
    _secure_path(_path(d["state_directory"]), d["uid"], directory=True)
    fd = _lock(recipe)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Never infer ownership from stale PID/heartbeat metadata. A held
            # owner is not eligible for bootstrap; central readiness verifies it.
            if _running_owner(recipe, view, fd, systemd):
                response.update(state="running", owner="service")
            else:
                response["state"] = "owner_present"
            return response
        if view["ActiveState"] in {"active", "activating", "reloading", "deactivating"} or view["MainPID"] != "0" or view["Job"] != "":
            response["state"] = "transitioning"
            return response
        if view["ActiveState"] != "inactive" or view["SubState"] != "dead":
            raise Refused("BOOTSTRAP_SERVICE_UNKNOWN")
        _database(recipe)
        # Unit/config bytes and kernel owner exclusion are checked again before
        # releasing the lock. Never hold this lock while asking systemd to start.
        fresh = systemd.query(d["unit"])
        _unit(recipe, fresh)
        if fresh != view:
            raise Refused("BOOTSTRAP_SERVICE_CHANGED")
        recipe.verify_current()
        lock_path = _path(d["state_directory"]) / "task-daemon.lock"
        if lock_path.lstat() != os.fstat(fd):
            raise Refused("BOOTSTRAP_OWNER_UNPROVEN")
        response.update(state="stopped", owner="absent")
    finally:
        os.close(fd)
    if request["action"] == "ensure":
        recipe.verify_current()
        systemd.start(d["unit"])
        response.update(state="transitioning", owner="unknown", ensure_accepted=True)
    return response


def main():
    """Fixed recipe location; no command/service/path arguments or stdout diagnostics."""
    def expired(_signal, _frame):
        raise Refused("BOOTSTRAP_TIMEOUT")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(15)
    try:
        if len(sys.argv) != 1:
            raise Refused("BOOTSTRAP_PROTOCOL_INVALID")
        raw, _ = _read(SERVER_RECIPE, os.getuid(), LIMIT)
        recipe = Recipe(raw, SERVER_RECIPE)
        request = _json(sys.stdin.buffer.read(LIMIT + 1))
        result = handle(recipe, request)
        sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    except (Refused, OSError):
        # No arbitrary exceptions, paths, commands or service/environment output.
        sys.stdout.write('{"protocol":1,"error":"BOOTSTRAP_REFUSED"}\n')
        return 1
    finally:
        signal.alarm(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
