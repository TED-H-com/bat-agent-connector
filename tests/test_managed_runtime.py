"""Real isolated service ownership, credential recovery and safe failure boundaries."""
import concurrent.futures
import json
import os
import socket
import sqlite3
import sys
import time

import pytest

from bat_agent_connector import managed_runtime as runtime


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = tmp_path / "installed"
    children = []
    original = runtime._spawn

    def spawn(path):
        child = original(path)
        children.append(child)
        return child

    monkeypatch.setattr(runtime, "_spawn", spawn)
    yield root, children
    for child in children:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except Exception:
                child.kill()
                child.wait(timeout=10)


def test_installed_runtime_reuses_identity_and_recovers_after_restart(installation):
    root, children = installation
    first = runtime.ensure(root, timeout=15)
    second = runtime.ensure(root, timeout=5)
    assert first == second and len(children) == 1
    assert first["token"].startswith("batc_")
    assert first["actor"].startswith("desktop-")
    # Service continues independently of launcher/client; its ordinary API is authoritative.
    bootstrap = runtime._request(first["endpoint"], "/api/v1/bootstrap", token=first["token"])
    assert bootstrap["sync"]["server_id"] == first["server_id"]
    assert bootstrap["sync"]["principal_id"] == first["principal_id"]
    stopped = runtime._request(first["endpoint"], "/api/v1/managed/stop", token=first["token"], body={})
    assert stopped == {"protocol": 1, "stopped": True}
    children[0].wait(timeout=10)
    third = runtime.ensure(root, timeout=15)
    assert {k: third[k] for k in ("actor", "token", "server_id", "principal_id")} == {
        k: first[k] for k in ("actor", "token", "server_id", "principal_id")}


def test_invalid_manifest_and_existing_unclaimed_data_never_spawn(installation):
    root, children = installation
    root.mkdir(mode=0o700)
    (root / "state").mkdir()
    with pytest.raises(ValueError, match="unclaimed"):
        runtime.ensure(root)
    assert not children and not (root / runtime.MANIFEST).exists()
    (root / "state").rmdir()
    runtime._write(root / runtime.MANIFEST, {"protocol": 1, "data_dir": str(root)})
    with pytest.raises(ValueError, match="manifest"):
        runtime.ensure(root)
    assert not children


def test_revoked_identity_never_gets_replaced(installation):
    root, children = installation
    first = runtime.ensure(root, timeout=15)
    runtime._request(first["endpoint"], "/api/v1/managed/stop", token=first["token"], body={})
    children[0].wait(timeout=10)
    import sqlite3
    with sqlite3.connect(root / "state" / "tasks.sqlite3") as db:
        db.execute("UPDATE api_principals SET revoked_at=?", (time.time(),))
    with pytest.raises(ValueError, match="initialization failed"):
        runtime.ensure(root, timeout=10)
    assert (root / "identity.token").read_text() == first["token"]
    assert json.loads((root / runtime.MANIFEST).read_text())["server_id"] == first["server_id"]


def test_occupied_default_port_is_not_adopted(installation):
    root, _ = installation
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        first = runtime.ensure(root, timeout=15)
        assert first["endpoint"] != f"http://127.0.0.1:{listener.getsockname()[1]}"


@pytest.mark.skipif(os.name == "nt", reason="native reparse coverage lives in Windows platform fixtures")
def test_symlinked_installation_is_not_adopted(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "alias"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        runtime.ensure(link)
    assert not list(target.iterdir())


@pytest.fixture
def versioned_installation(installation, monkeypatch):
    # A real child daemon, with the version a different installed package would
    # contain. No version override exists in the product's runtime protocol.
    def command(root):
        source = ("import asyncio, sys; from bat_agent_connector import managed_runtime as r; "
                  f"r.__version__ = {runtime.__version__!r}; asyncio.run(r.serve(sys.argv[1]))")
        return [sys.executable, "-c", source, str(root)]
    monkeypatch.setattr(runtime, "_service_command", command)
    monkeypatch.setattr(runtime, "__version__", "1.0.0")
    return installation


def test_new_package_upgrades_owned_idle_service_preserving_identity_and_history(versioned_installation, monkeypatch):
    root, children = versioned_installation
    original = runtime.ensure(root, timeout=15)
    # History already in the sole journal must survive the replacement process.
    with sqlite3.connect(root / "state" / "tasks.sqlite3") as db:
        db.execute("INSERT INTO api_events(resource_type,resource_id,kind,body,created_at) VALUES(?,?,?,?,?)",
                   ("fixture", "saved-event", "retained", "{}", time.time()))
        sequence = db.execute("SELECT seq FROM api_events WHERE resource_id='saved-event'").fetchone()[0]
    monkeypatch.setattr(runtime, "__version__", "1.1.0")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
        replies = list(workers.map(lambda _: runtime.ensure(root, timeout=15), range(3)))
    upgraded = replies[0]
    assert all(reply == upgraded for reply in replies)
    assert upgraded["runtime_version"] == "1.1.0"
    assert len(children) == 2
    children[0].wait(timeout=10)
    assert {k: upgraded[k] for k in ("actor", "token", "server_id", "principal_id")} == {
        k: original[k] for k in ("actor", "token", "server_id", "principal_id")}
    with sqlite3.connect(root / "state" / "tasks.sqlite3") as db:
        assert db.execute("SELECT seq FROM api_events WHERE resource_id='saved-event'").fetchone()[0] == sequence
    assert runtime.ensure(root, timeout=5) == upgraded and len(children) == 2


def test_busy_upgrade_preserves_original_owner_and_uncertain_operation(versioned_installation, monkeypatch):
    root, children = versioned_installation
    original = runtime.ensure(root, timeout=15)
    # A saved uncertain effect cannot be retried or discarded to make upgrading
    # convenient. Keep it out of the scheduler's due window in this fixture.
    with sqlite3.connect(root / "state" / "tasks.sqlite3") as db:
        db.execute("""INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,action,
                   target,params,preconditions,status,next_run_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   ("uncertain-fixture", original["actor"], "fixture", "original-key", "fixed-hash",
                    "session.send", "{}", "{}", "{}", "uncertain", time.time() + 3600, time.time(), time.time()))
    monkeypatch.setattr(runtime, "__version__", "1.1.0")
    with pytest.raises(ValueError, match="busy"):
        runtime.ensure(root, timeout=5)
    assert len(children) == 1 and children[0].poll() is None
    assert runtime._verify(root, runtime._manifest(root)) == original
    with sqlite3.connect(root / "state" / "tasks.sqlite3") as db:
        assert db.execute("SELECT status,idem_key FROM operations WHERE operation_id='uncertain-fixture'").fetchone() == (
            "uncertain", "original-key")


def test_older_package_never_downgrades_a_running_service(versioned_installation, monkeypatch):
    root, children = versioned_installation
    original = runtime.ensure(root, timeout=15)
    monkeypatch.setattr(runtime, "__version__", "0.9.0")
    with pytest.raises(ValueError, match="newer"):
        runtime.ensure(root, timeout=5)
    assert len(children) == 1 and children[0].poll() is None
    assert runtime._verify(root, runtime._manifest(root)) == original


def test_older_package_never_downgrades_a_stopped_installation(versioned_installation, monkeypatch):
    root, children = versioned_installation
    runtime.ensure(root, timeout=15)
    assert runtime.stop(root)["stopped"] is True
    children[0].wait(timeout=10)
    manifest = (root / runtime.MANIFEST).read_bytes()
    monkeypatch.setattr(runtime, "__version__", "0.9.0")
    with pytest.raises(ValueError, match="newer"):
        runtime.ensure(root, timeout=5)
    assert len(children) == 1 and (root / runtime.MANIFEST).read_bytes() == manifest


def test_interrupted_upgrade_recovers_original_installation(versioned_installation, monkeypatch):
    root, children = versioned_installation
    original = runtime.ensure(root, timeout=15)
    monkeypatch.setattr(runtime, "__version__", "1.1.0")
    spawn = runtime._spawn
    def interrupted(_root):
        raise OSError("fixture failed to launch replacement")
    monkeypatch.setattr(runtime, "_spawn", interrupted)
    with pytest.raises(OSError, match="replacement"):
        runtime.ensure(root, timeout=15)
    children[0].wait(timeout=10)
    assert runtime._manifest(root)["state"] == "ready"
    assert runtime._manifest(root)["runtime_version"] == "1.0.0"
    monkeypatch.setattr(runtime, "_spawn", spawn)
    recovered = runtime.ensure(root, timeout=15)
    assert recovered["runtime_version"] == "1.1.0" and len(children) == 2
    assert {k: recovered[k] for k in ("actor", "token", "server_id", "principal_id")} == {
        k: original[k] for k in ("actor", "token", "server_id", "principal_id")}


def test_ensure_restarts_dead_service_with_new_endpoint_and_original_identity(installation):
    root, children = installation
    first = runtime.ensure(root, timeout=15)
    # Terminate only our fixture child, never a PID from the endpoint pointer.
    children[0].terminate()
    children[0].wait(timeout=10)
    # Occupy the old port so the OS cannot coincidentally reuse it for port=0.
    from urllib.parse import urlsplit
    with socket.socket() as occupied:
        occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied.bind(("127.0.0.1", urlsplit(first["endpoint"]).port))
        occupied.listen()
        recovered = runtime.ensure(root, timeout=15)
    assert len(children) == 2
    assert recovered["endpoint"] != first["endpoint"]
    for key in ("server_id", "principal_id", "actor", "token", "runtime_version"):
        assert recovered[key] == first[key]
    assert runtime._request(recovered["endpoint"], "/api/v1/bootstrap", token=recovered["token"])["sync"]["server_id"] == first["server_id"]
