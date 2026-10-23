"""Bundled central launcher. Stdout is a private native IPC pipe, never a log.

The native client owns packaging/login startup; this process owns the ordinary
Python central. A durable manifest and kernel lease recover one installation.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__, api_auth, dashboard_sync, detached_process, platform_files
from .build_identity import _commit, build_identity
from .platform_files import (
    atomic_write,
    ensure_private_directory,
    lock,
    open_private_file,
    read_private,
    unlock,
)
from .task_journal import LATEST_DATA_STEP, JournalNewerDataStep

PROTOCOL = 1
MANIFEST = "installation.json"


def _json(path):
    return json.loads(read_private(path))


def _write(path, value):
    atomic_write(path, (json.dumps(value, sort_keys=True) + "\n").encode())


@contextlib.contextmanager
def _locked(path, *, blocking=True):
    fd = open_private_file(path, os.O_RDWR | os.O_CREAT)
    try:
        lock(fd, blocking=blocking)
        try:
            yield
        finally:
            unlock(fd)
    finally:
        os.close(fd)


def _root(raw):
    path = Path(raw).expanduser().absolute()
    # Never follow an existing alias and then bless its target as this installation.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("managed data location must not contain symbolic links")
    ensure_private_directory(path)
    return path


def _manifest(root):
    doc = _json(root / MANIFEST)
    if (doc.get("protocol") != PROTOCOL or doc.get("data_dir") != str(root)
            or not re.fullmatch(r"[0-9a-f]{32}", doc.get("installation_id", ""))
            or doc.get("actor") != "desktop-" + doc["installation_id"]
            or doc.get("state") not in {"initializing", "ready"}):
        raise ValueError("installation manifest is invalid; preserve its data for recovery")
    if doc["state"] == "ready" and (not doc.get("server_id") or not doc.get("principal_id")):
        raise ValueError("installation identity is incomplete")
    return doc


def _prepare(root):
    path = root / MANIFEST
    if path.exists():
        return _manifest(root)
    # A directory with existing service data but no manifest is never a new installation.
    if any((root / name).exists() for name in ("state", "config", "identity.token", "installation.key")):
        raise ValueError("unclaimed installation data exists; automatic replacement is refused")
    ident = secrets.token_hex(16)
    doc = {"protocol": PROTOCOL, "installation_id": ident, "actor": "desktop-" + ident,
           "data_dir": str(root), "runtime_version": __version__, "state": "initializing"}
    _write(path, doc)
    return doc


def _endpoint(root):
    pointer = _json(root / "state" / "task-service.json")
    if pointer.get("db_path") != str(root / "state" / "tasks.sqlite3"):
        raise ValueError("service pointer belongs to another database")
    value = pointer.get("endpoint", "")
    if not value:
        raise ValueError("owned service is not listening yet")
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.path != "/rpc" or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("invalid owned service endpoint")
    return value.removesuffix("/rpc")


def _request(endpoint, path, *, token=None, body=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(endpoint + path,  # noqa: S310 - fixed loopback owned endpoint
        data=json.dumps(body).encode() if body is not None else None, headers=headers)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            raise ValueError("managed service redirects are refused")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=2) as response:  # noqa: S310 - no proxies or redirects
        raw = response.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("managed service response exceeds its bound")
    return json.loads(raw)


def _proof(key, challenge, endpoint, server_id):
    data = json.dumps([PROTOCOL, challenge, endpoint, server_id], separators=(",", ":")).encode()
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def _runtime_identity(build, source):
    build = build if isinstance(build, dict) else {}
    version = build.get("version")
    return {"version": version if isinstance(version, str) and 0 < len(version) <= 128 else None,
            "commit": _commit(build.get("commit")),
            "build_source": build.get("source") if build.get("source") in ("package", "vcs", "bundled") else None,
            "source": source, "observed_at": time.time()}


def _runtime_difference(bundled, running):
    if any(fact[key] is None for fact in (bundled, running) for key in ("version", "commit")):
        return "unknown"
    return "same" if (bundled["version"], bundled["commit"]) == (running["version"], running["commit"]) else "differs"


def _verify(root, doc):
    bundled = _runtime_identity(build_identity(), "this_process")
    endpoint = _endpoint(root)
    challenge = secrets.token_hex(32)
    result = _request(endpoint, "/api/v1/managed/identity?challenge=" + challenge)
    expected = _proof(read_private(root / "installation.key"), challenge, endpoint, doc.get("server_id"))
    if not hmac.compare_digest(str(result.get("proof", "")), expected):
        raise ValueError("owned service identity could not be verified")
    token = read_private(root / "identity.token").decode().strip()
    caps = _request(endpoint, "/api/v1/capabilities", token=token)
    if (caps.get("actor") != doc["actor"] or caps.get("desktop_identity") !=
            {"server_id": doc["server_id"], "principal_id": doc["principal_id"]}):
        raise ValueError("owned service personal identity changed; automatic replacement is refused")
    # Capabilities already comes from this proven owner and contains the build
    # identity it loaded at startup. Old services without provenance remain unknown.
    running = _runtime_identity(caps.get("build"), "capabilities")
    return {"protocol": PROTOCOL, "endpoint": endpoint, "actor": doc["actor"],
            "server_id": doc["server_id"], "principal_id": doc["principal_id"],
            "runtime_version": result.get("runtime_version", doc["runtime_version"]), "token": token,
            "bundled_runtime": bundled, "running_runtime": running,
            "runtime_difference": _runtime_difference(bundled, running)}


def _service_command(root):
    prefix = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "bat_agent_connector.managed_runtime"]
    return [*prefix, "serve", "--data-dir", str(root)]


def _spawn(root):
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
               "close_fds": True, "cwd": str(root)}
    if getattr(sys, "frozen", False):
        options["env"] = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    if os.name == "nt":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    with os.fdopen(open_private_file(root / "service.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC), "wb") as log:
        options["stderr"] = log
        return detached_process.spawn(_service_command(root), windows=os.name == "nt", **options)


def _check_upgrade(previous):
    if previous == __version__:
        return
    # Published runtime packages use three-part release versions. Never silently
    # downgrade a newer journal or guess ordering for an unknown version format.
    versions = []
    for value in (previous, __version__):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}", value):
            raise ValueError("runtime version needs explicit installation recovery")
        versions.append(tuple(map(int, value.split("."))))
    if versions[0] >= versions[1]:
        raise ValueError("a newer runtime owns this installation; reopen its matching application")


def _running(root):
    try:
        with _locked(root / "state" / "task-daemon.lock", blocking=False):
            return False
    except BlockingIOError:
        return True


class LifecycleRefusal(ValueError):
    def __init__(self, code, message, lifecycle=None):
        super().__init__(message)
        self.code = code
        self.lifecycle = lifecycle


def lifecycle(raw):
    """Private, read-only projection. Only launcher-lock holders reconcile a hold."""
    path = Path(raw) / "state" / "lifecycle.json"
    try:
        doc = json.loads(read_private(path, max_bytes=4096))
    except FileNotFoundError:
        # A dangling alias is still evidence of an untrusted hold.
        if path.is_symlink():
            raise LifecycleRefusal("SERVICE_LIFECYCLE_UNPROVEN", "service stop hold cannot be verified") from None
        return {"state": "running", "requested_at": None, "stopped_at": None}
    except (OSError, ValueError):
        raise LifecycleRefusal("SERVICE_LIFECYCLE_UNPROVEN", "service stop hold cannot be verified") from None
    if (not isinstance(doc, dict) or type(doc.get("version")) is not int or doc["version"] != 1
            or doc.get("state") not in ("stop_pending", "stopped")
            or not _timestamp(doc.get("requested_at"))
            or (doc["state"] == "stopped" and not _timestamp(doc.get("stopped_at")))
            or (doc["state"] == "stop_pending" and doc.get("stopped_at") is not None)):
        raise LifecycleRefusal("SERVICE_LIFECYCLE_UNPROVEN", "service stop hold cannot be verified")
    return {key: doc.get(key) for key in ("state", "requested_at", "stopped_at")}


def _timestamp(value):
    return type(value) in (int, float) and 0 < value < float("inf")


def _save_lifecycle(root, state):
    _write(root / "state" / "lifecycle.json", {"version": 1, **state})
    return state


def _clear_hold(root):
    # Validate again before mutation. The private parent and leaf use the same
    # platform protections as the other launcher-owned state.
    lifecycle(root)
    (root / "state" / "lifecycle.json").unlink()
    platform_files.sync_directory(root / "state")


def _settle_hold(root, state):
    # Caller holds launcher.lock. Reading status never calls this mutation.
    if state["state"] == "stop_pending" and not _running(root):
        return _save_lifecycle(root, {**state, "state": "stopped", "stopped_at": time.time()})
    return state


def _stop_owned(root, launch, until, *, lifecycle_stop=False):
    # Called with launcher.lock held. Only the proven Python owner decides whether
    # work is quiescent; no PID/port from disk is ever used to terminate a process.
    try:
        result = _request(launch["endpoint"], "/api/v1/managed/stop", token=launch["token"], body={})
    except urllib.error.HTTPError as error:
        if lifecycle_stop and 400 <= error.code < 500:
            if error.code != 409:
                raise LifecycleRefusal("SERVICE_STOP_REFUSED", "owned service refused shutdown") from None
            try:
                reason = json.loads(error.read(4096))["error"]["message"]
            except (ValueError, KeyError, TypeError):
                reason = None
            reasons = {"finish or reconcile current operations before stopping",
                       "finish or pause service shutdown until current tasks settle", "current work is still running"}
            raise LifecycleRefusal("SERVICE_BUSY", reason if isinstance(reason, str) and reason in reasons else
                                   "owned service is busy; finish or reconcile current work before stopping") from None
        if error.code == 409:
            raise ValueError("owned service is busy; finish or reconcile current work before upgrading") from None
        raise
    if result != {"protocol": PROTOCOL, "stopped": True}:
        raise ValueError("owned service did not acknowledge shutdown")
    while _running(root):
        if time.monotonic() >= until:
            raise ValueError("owned service is still stopping")
        time.sleep(0.1)
    return result


def stop(raw, *, timeout=30):
    root = _root(raw)
    with _locked(root / "launcher.lock"):
        doc = _manifest(root)
        state = lifecycle(root)
        if state["state"] != "running":
            state = _settle_hold(root, state)
            if state["state"] == "stopped":
                return {"protocol": PROTOCOL, "stopped": True, "lifecycle": state}
        running = _running(root)
        launch = _verify(root, doc) if running else None
        if state["state"] == "running":
            state = _save_lifecycle(root, {"state": "stop_pending", "requested_at": time.time(), "stopped_at": None})
        if launch is not None:
            try:
                _stop_owned(root, launch, time.monotonic() + timeout, lifecycle_stop=True)
            except LifecycleRefusal as error:
                _clear_hold(root)  # every 4xx is an authoritative refusal before any effect
                error.lifecycle = lifecycle(root)
                raise
            except (OSError, ValueError):
                # Keep uncertainty durable. Only a later explicit stop may send again.
                return {"protocol": PROTOCOL, "stopped": False, "lifecycle": state}
        state = _settle_hold(root, state)
        return {"protocol": PROTOCOL, "stopped": state["state"] == "stopped", "lifecycle": state}


def resume(raw, *, timeout=45):
    root = _root(raw)
    with _locked(root / "launcher.lock"):
        state = lifecycle(root)
        if state["state"] != "running":
            if _running(root):
                raise LifecycleRefusal("SERVICE_STILL_STOPPING", "owned service is still stopping", state)
            _clear_hold(root)
        return _ensure_locked(root, timeout=timeout)


def _journal_startup_failure(root):
    """Forward only the child's fixed, numeric journal refusal, never arbitrary logs."""
    try:
        failure = json.loads(read_private(root / "service.log", max_bytes=16384))
    except (OSError, ValueError):
        return None
    if (isinstance(failure, dict) and failure.get("protocol") == PROTOCOL
            and failure.get("error") == JournalNewerDataStep.code
            and type(failure.get("recorded_data_step")) is int
            and 0 <= failure["recorded_data_step"] <= 2147483647
            and failure.get("supported_data_step") == LATEST_DATA_STEP):
        return JournalNewerDataStep(failure["recorded_data_step"])
    return None


def ensure(raw, *, timeout=45):
    root = _root(raw)
    with _locked(root / "launcher.lock"):
        return _ensure_locked(root, timeout=timeout)


def _ensure_locked(root, *, timeout):
    state = lifecycle(root)
    if state["state"] != "running":
        state = _settle_hold(root, state)
        raise LifecycleRefusal("SERVICE_STOPPED", "background service stays stopped until Start", state)
    doc = _prepare(root)
    ensure_private_directory(root / "state")
    # A locked service lease is evidence of an owner, not proof of its endpoint.
    running = _running(root)
    if not running:
        _check_upgrade(doc.get("runtime_version"))
    child = None if running else _spawn(root)
    until = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < until:
        launch = None
        try:
            doc = _manifest(root)
            if doc["state"] == "ready":
                launch = _verify(root, doc)
        except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
            last_error = type(error).__name__
        if launch is not None:
            if launch["runtime_version"] == __version__:
                return launch
            _check_upgrade(launch["runtime_version"])
            _stop_owned(root, launch, until)
            # Interrupted upgrades retain the same ready manifest and journal.
            # A later ensure can recover this installation if spawning fails.
            child = _spawn(root)
        if child and child.poll() is not None:
            if failure := _journal_startup_failure(root):
                raise failure
            raise ValueError("managed service initialization failed; existing data was preserved")
        time.sleep(0.1)
    # Never kill a PID read from a pointer or create a second database after a failure.
    raise ValueError("managed service unavailable; retry this installation" +
                     (f" ({last_error})" if last_error else ""))


async def serve(raw):
    root = _root(raw)
    state = lifecycle(root)
    if state["state"] != "running":
        raise LifecycleRefusal("SERVICE_STOPPED", "background service stays stopped until Start", state)
    doc = _manifest(root)
    ensure_private_directory(root / "config")
    ensure_private_directory(root / "state")
    os.environ["BATC_STATE_DIR"] = str(root / "state")
    os.environ["BATC_CONFIG_DIR"] = str(root / "config")
    os.environ["BATC_CONFIG"] = str(root / "config" / "hosts.toml")
    # A packaged private installation must not accidentally adopt operator env recipes.
    for key in ("BATC_TASK_SETTINGS", "BATC_TASK_ADMIN_TOKEN_FILE", "BATC_TASK_CAPABILITY", "BATC_TASK_URL",
                "BATC_PM_PROVIDER_CONFIG", "BATC_DEVICE_ID"):
        os.environ.pop(key, None)
    from .config import load_config
    from .task_daemon import TaskDaemon
    config_path = root / "config" / "hosts.toml"
    if not config_path.exists():
        if doc["state"] != "initializing":
            raise ValueError("installation configuration is missing; automatic replacement is refused")
        atomic_write(config_path, b'[client]\nlabel = "Better Agent Dashboard"\n[hosts]\n')
    daemon = TaskDaemon(load_config(config_path), root / "state" / "tasks.sqlite3")
    daemon.acquire_owner()
    try:
        token_path = root / "identity.token"
        if not token_path.exists():
            if doc["state"] != "initializing":
                raise ValueError("personal credential is missing; automatic replacement is refused")
            with daemon.journal.tx():
                api_auth.revoke(daemon.journal.db, doc["actor"])
                token = api_auth.issue(daemon.journal.db, doc["actor"], api_auth.SCOPES, label="Managed desktop")
            atomic_write(token_path, token.encode())
        token = read_private(token_path).decode().strip()
        principal = api_auth.authenticate(daemon.journal.db, token, daemon._admin_token)
        if principal is None or principal.actor != doc["actor"]:
            raise ValueError("personal credential expired or revoked; automatic identity replacement is refused")
        identity = dashboard_sync.identity(daemon.journal, principal)
        if doc["state"] == "ready" and any(doc[k] != identity[k] for k in identity):
            raise ValueError("installation journal identity changed")
        key_path = root / "installation.key"
        if not key_path.exists():
            if doc["state"] != "initializing":
                raise ValueError("installation proof key is missing")
            atomic_write(key_path, secrets.token_bytes(32))
        key = read_private(key_path)
        doc.update(identity, state="ready", runtime_version=__version__)
        _write(root / MANIFEST, doc)
        daemon.managed_installation = {**doc, "data_dir": root}
        from . import managed_setup
        managed_setup.install(daemon)
        from .browser_sessions import BrowserSessions
        browser_sessions = BrowserSessions(daemon, root, doc["installation_id"])
        daemon.api.browser_sessions = browser_sessions

        def prove(challenge, host):
            endpoint = browser_sessions.origin
            if not re.fullmatch(r"[0-9a-f]{64}", challenge) or host != urlsplit(endpoint).netloc:
                raise ValueError("invalid managed identity challenge")
            return {"protocol": PROTOCOL, "runtime_version": __version__,
                    "proof": _proof(key, challenge, endpoint, doc["server_id"])}
        daemon.managed_proof = prove
        stopping = asyncio.Event()
        daemon.managed_stop = stopping
        # Port zero is published through the owned pointer and verified before any bearer is sent.
        service_task = asyncio.create_task(daemon.serve(port=0))
        stop_task = asyncio.create_task(stopping.wait())
        try:
            await asyncio.wait((service_task, stop_task), return_when=asyncio.FIRST_COMPLETED)
            if service_task.done():
                await service_task
        finally:
            service_task.cancel()
            stop_task.cancel()
            await asyncio.gather(service_task, stop_task, return_exceptions=True)
    finally:
        if daemon._lease_fd is not None:
            daemon.journal.close()
            daemon.release_owner()


def _failure_location(error):
    # A package may fail before a service.log exists. Retain enough bounded
    # location evidence to diagnose that failure without publishing filenames,
    # source lines, locals or arbitrary exception/provider text on native IPC.
    phases = {function.__code__: name for name, function in (
        ("root", _root), ("launcher_lock", _locked.__wrapped__),
        ("prepare", _prepare), ("manifest", _manifest), ("service_lease", _running),
        ("spawn", _spawn), ("endpoint", _endpoint), ("verify", _verify),
        ("ensure", ensure), ("serve", serve), ("stop", stop),
    )}
    storage = {function.__code__: name for name, function in (
        ("directory", platform_files.ensure_private_directory),
        ("open", platform_files.open_private_file), ("read", platform_files.read_private),
        ("write", platform_files.atomic_write), ("lock", platform_files.lock),
    )}
    result = {"phase": "unknown", "storage": "none"}
    frame = error.__traceback__
    while frame is not None:
        code = frame.tb_frame.f_code
        if code in phases:
            # The shared lock helper also opens the service lease. Preserve the
            # more specific caller instead of misreporting it as launcher.lock.
            phase = phases[code]
            if phase != "launcher_lock" or result["phase"] != "service_lease":
                result["phase"] = phase
        if code in storage:
            result["storage"] = storage[code]
        if not platform_files.WINDOWS and code is platform_files._posix_dir.__code__:
            result["storage"] = "directory_walk"
        frame = frame.tb_next
    if result["storage"] == "open" and isinstance(error, OSError):
        # Only fixed public leaf names become labels; arbitrary error filenames,
        # including private parent components, never leave the native process.
        leaf = getattr(error, "filename", None)
        label = ({"launcher.lock": "launcher_lease", "task-daemon.lock": "service_lease"}.get(leaf)
                 if isinstance(leaf, str) else None)
        if label:
            result["object"] = label
    return result


def main():
    parser = argparse.ArgumentParser(description="Managed desktop central runtime")
    parser.add_argument("command", choices=("ensure", "serve", "browser", "stop", "resume", "status"))
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    try:
        if args.command == "serve":
            asyncio.run(serve(args.data_dir))
            return
        if args.command == "stop":
            print(json.dumps(stop(args.data_dir)), flush=True)
            return
        if args.command == "status":
            print(json.dumps({"protocol": PROTOCOL, "lifecycle": lifecycle(args.data_dir)}), flush=True)
            return
        result = resume(args.data_dir) if args.command == "resume" else ensure(args.data_dir)
        if args.command == "browser":
            result = _request(result["endpoint"], "/api/v1/managed/browser-file", token=result["token"], body={})
        print(json.dumps(result), flush=True)
    except Exception as error:  # no secrets, paths or arbitrary provider output on the native protocol
        failure = {"protocol": PROTOCOL, "error": "MANAGED_RUNTIME_UNAVAILABLE",
                   "message": type(error).__name__ + ": managed installation needs recovery",
                   "errno": getattr(error, "errno", None), "winerror": getattr(error, "winerror", None),
                   "location": _failure_location(error)}
        if isinstance(error, LifecycleRefusal):
            failure.update(error=error.code, message=str(error))
            if error.lifecycle is not None:
                failure["lifecycle"] = error.lifecycle
        if isinstance(error, JournalNewerDataStep):
            failure.update(error=error.code, message=str(error), recorded_data_step=error.recorded,
                           supported_data_step=LATEST_DATA_STEP)
        print(json.dumps(failure), file=sys.stderr if args.command == "serve" else sys.stdout, flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
