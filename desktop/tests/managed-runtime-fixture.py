"""Exercise the packaged central without Python/uv available to its child processes.

Only temporary local state and an unconfigured BAT inventory are used. No live host
or provider is contacted. Secrets remain in memory and are omitted from receipts.
"""

from __future__ import annotations

import concurrent.futures
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from managed_runtime_assertions import assert_stopped_reply, service_files, service_stays_stopped


def remove_stopped_fixture(directory: Path, *, timeout: float = 30) -> None:
    # The owned stop receipt already proves the daemon released its service lease.
    # A frozen one-file bootstrap can briefly retain inherited handles afterward;
    # Windows refuses removal until those handles close. Never kill a recorded PID
    # or hide a persistent sharing violation (or any unrelated filesystem error).
    until = time.monotonic() + timeout
    while True:
        try:
            shutil.rmtree(directory)
            return
        except OSError as error:
            remaining = until - time.monotonic()
            if getattr(error, "winerror", None) not in {32, 33} or remaining <= 0:
                raise
            time.sleep(min(0.1, remaining))


def main() -> None:
    resources = Path(__file__).resolve().parents[1] / "managed-runtime"
    manifest = json.loads((resources / "manifest.json").read_text())
    payload = (resources / "runtime.gz").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == manifest["payload_sha256"]
    binary = gzip.decompress(payload)
    assert len(binary) == manifest["size"]
    assert hashlib.sha256(binary).hexdigest() == manifest["sha256"]
    temp_root = Path(os.environ.get("RUNNER_TEMP", Path.home() / "agent-work/tmp/managed-runtime"))
    temp_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="packaged-central-", dir=temp_root))
    runtime = directory / manifest["executable"]
    runtime.write_bytes(binary)
    runtime.chmod(0o700)
    data = directory / "owned-installation"
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "BATC_DESKTOP_TOKEN"):
        env.pop(key, None)
    env["PATH"] = ""
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    # PyInstaller one-file extraction is also explicitly disk-backed.
    env["TMPDIR"] = env["TEMP"] = env["TMP"] = str(directory)

    def command(action: str, *, stopped: dict | None = None) -> dict:
        result = subprocess.run(  # noqa: S603 - hash-verified bundled fixture executable
            [str(runtime), action, "--data-dir", str(data)], env=env,
            stdin=subprocess.DEVNULL, capture_output=True, timeout=90,
        )
        if stopped is not None:
            assert result.returncode != 0, "Held service must refuse automatic start"
            document = json.loads(result.stdout)
            assert_stopped_reply(result.returncode, document, stopped)
            return document
        if result.returncode:
            try:
                reply = json.loads(result.stdout)
                details = {key: reply.get(key) for key in ("error", "message", "errno", "winerror")}
                location = reply.get("location", {})
                details["location"] = {
                    "phase": location.get("phase") if location.get("phase") in {
                        "root", "launcher_lock", "prepare", "manifest", "service_lease", "spawn",
                        "endpoint", "verify", "ensure", "serve", "stop", "unknown"} else "unknown",
                    "storage": location.get("storage") if location.get("storage") in {
                        "directory", "directory_walk", "open", "read", "write", "lock", "none"} else "unknown",
                } if isinstance(location, dict) else {"phase": "unknown", "storage": "unknown"}
                if isinstance(location, dict) and location.get("object") in {"launcher_lease", "service_lease"}:
                    details["location"]["object"] = location["object"]
            except (ValueError, TypeError):
                details = {"error": "INVALID_RUNTIME_REPLY"}
            raise AssertionError(f"Packaged runtime {action} failed ({result.returncode}): {details}")
        assert len(result.stdout) < 16384, "Bounded native reply required"
        document = json.loads(result.stdout)
        assert document["protocol"] == 1
        return document

    def identity(reply: dict) -> tuple:
        return tuple(reply[key] for key in ("actor", "server_id", "principal_id", "runtime_version"))

    def bootstrap(reply: dict) -> dict:
        request = urllib.request.Request(  # noqa: S310 - owned fixture loopback endpoint
            reply["endpoint"].rstrip("/") + "/api/v1/bootstrap",
            headers={"Authorization": "Bearer " + reply["token"]},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=15) as response:
            return json.load(response)

    stopped = False
    try:
        # Simultaneous clients must reconcile one installation and one listening owner.
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
            replies = list(workers.map(lambda _: command("ensure"), range(3)))
        original = replies[0]
        assert all(identity(reply) == identity(original) for reply in replies)
        assert all(reply["endpoint"] == original["endpoint"] for reply in replies)
        assert original["runtime_version"] == manifest["runtime_version"]
        state = bootstrap(original)
        assert state["sync"]["server_id"] == original["server_id"]
        assert state["sync"]["principal_id"] == original["principal_id"]
        browser = command("browser")
        handoff = Path(browser["handoff_file"])
        assert handoff.parent.resolve() == (data / "browser-handoffs").resolve()
        assert handoff.suffix == ".html" and not handoff.is_symlink()
        assert original["token"] not in handoff.read_text(), "Bearer must not enter browser handoff"
        stop_reply = command("stop")
        assert stop_reply["stopped"] is True
        stopped = True
        with service_stays_stopped(data):
            command("ensure", stopped=stop_reply["lifecycle"])
            command("browser", stopped=stop_reply["lifecycle"])
            assert command("status")["lifecycle"]["state"] == "stopped"
        stopped = False  # A partial resume must still be stopped in finally.
        restarted = command("resume")
        assert not (data / "state/lifecycle.json").exists()
        resumed_owner = service_files(data)
        assert identity(command("ensure")) == identity(restarted)
        assert service_files(data) == resumed_owner
        assert identity(restarted) == identity(original), "Restart must preserve journal identities"
        assert bootstrap(restarted)["sync"]["server_id"] == original["server_id"]
        assert command("stop")["stopped"] is True
        stopped = True
        receipt = {
            "status": "passed", "evidence_level": "packaged-runtime-fixture", "live_accepted": False,
            "platform": manifest["platform"], "architecture": manifest["architecture"],
            "runtime_sha256": manifest["sha256"], "no_python_on_path": True,
            "concurrent_clients": 3, "stable_identity_after_restart": True,
            "authenticated_bootstrap": True, "browser_handoff_contains_no_bearer": True,
            "owned_service_stopped": True, "stop_hold_blocks_ensure_and_browser": True, "explicit_resume": True,
            "fixture_directory_removed": True,
        }
    finally:
        if not stopped and (data / "installation.json").exists():
            # Refuse silent cleanup when a service remains alive.
            assert command("stop")["stopped"] is True
        remove_stopped_fixture(directory)
    # Passing evidence includes teardown, not just the service-level assertions.
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
