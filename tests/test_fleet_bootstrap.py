"""Synthetic user-unit/state fixtures only. Never invoke installed systemd or SSH."""
import fcntl
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from bat_agent_connector import fleet_bootstrap as b


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode()


class Service:
    def __init__(self, recipe):
        self.recipe = recipe
        d = recipe.data
        self.view = dict.fromkeys(b.PROPERTIES, "")
        self.view.update(Id=d["unit"], LoadState="loaded", ActiveState="inactive", SubState="dead", MainPID="0",
                         FragmentPath=d["unit_path"], NeedDaemonReload="no", Type="simple", Restart="no", DynamicUser="no",
                         Environment=f'BATC_STATE_DIR={d["state_directory"]} BATC_CONFIG={d["config_path"]}',
                         ExecStart="{ path=" + d["executable"] + " ; argv[]=" + " ".join(recipe.argv()) + " ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }")
        self.queries = 0
        self.starts = []
        self.change = None

    def query(self, unit):
        assert unit == self.recipe.data["unit"]
        self.queries += 1
        if self.change and self.queries == 2:
            self.change(self.view)
        return dict(self.view)

    def start(self, unit):
        # The real daemon needs this exact owner lock; the guard must release it
        # before asking systemd to start. No new lock or journal can substitute.
        with open(Path(self.recipe.data["state_directory"]) / "task-daemon.lock", "rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.starts.append(unit)


@pytest.fixture
def fixture(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    database = state / "tasks.sqlite3"
    with sqlite3.connect(database) as db:
        for table in ("tasks", "commands", "operations", "daemon_owner"):
            db.execute(f"CREATE TABLE {table}(id INTEGER)")
    lock = state / "task-daemon.lock"
    lock.touch(mode=0o600)
    executable = tmp_path / "batc"
    executable.write_bytes(b"#!/usr/bin/python3\n# synthetic never executed\n")
    config = tmp_path / "hosts.toml"
    config.write_bytes(b"# synthetic\n")
    unit = tmp_path / "synthetic-connector.service"
    doc = {"schema_version": 1, "service_id": "synthetic-connector", "unit": unit.name, "unit_path": str(unit),
           "unit_sha256": "a" * 64, "uid": os.getuid(), "executable": str(executable), "config_path": str(config),
           "state_directory": str(state), "journal_path": str(database),
           "journal_identity": [database.stat().st_dev, database.stat().st_ino],
           "lock_identity": [lock.stat().st_dev, lock.stat().st_ino], "port": 18796}
    recipe = b.Recipe(encoded(doc))
    text = "[Unit]\nDescription=Synthetic fixture\n[Service]\nType=simple\nRestart=no\nExecStart=" + " ".join(recipe.argv()) + f"\nEnvironment=BATC_STATE_DIR={state} BATC_CONFIG={config}\n"
    unit.write_text(text)
    for protected in (unit, executable, config, database):
        protected.chmod(0o600)
    doc["unit_sha256"] = hashlib.sha256(unit.read_bytes()).hexdigest()
    recipe = b.Recipe(encoded(doc))
    request = {**recipe.expected_request(), "protocol": 1, "request_id": "1" * 32, "generation": "2" * 64,
               "attempt": 1, "action": "query"}
    return recipe, request, Service(recipe)


def test_query_proves_existing_state_without_modifying_source_or_requesting_start(fixture):
    recipe, request, service = fixture
    state = Path(recipe.data["state_directory"])
    before = {p.name: p.read_bytes() for p in state.iterdir()}
    result = b.handle(recipe, request, service)
    assert result == {**request, "state": "stopped", "owner": "absent", "ensure_accepted": False}
    assert not service.starts
    assert {p.name: p.read_bytes() for p in state.iterdir()} == before


def test_ensure_rechecks_unit_then_releases_original_owner_lock_before_one_start(fixture):
    recipe, request, service = fixture
    request["action"] = "ensure"
    result = b.handle(recipe, request, service)
    assert result["ensure_accepted"] is True
    assert result["state"] == "transitioning"
    assert service.queries == 2
    assert service.starts == [recipe.data["unit"]]


@pytest.mark.parametrize("target", ["ancestor", "state", "journal", "lock", "unit"])
@pytest.mark.parametrize("writable", [0o020, 0o002])
def test_unsafe_path_permissions_refuse_before_service_start(fixture, target, writable):
    recipe, request, service = fixture
    state = Path(recipe.data["state_directory"])
    path = {"ancestor": state.parent, "state": state, "journal": Path(recipe.data["journal_path"]),
            "lock": state / "task-daemon.lock", "unit": Path(recipe.data["unit_path"])}[target]
    # Establish the safe control first, so an unrelated unsafe host ancestor cannot
    # make a refusal test pass without reaching the intended fixture boundary.
    assert b.handle(recipe, request, service)["state"] == "stopped"
    request["action"] = "ensure"
    original = path.stat().st_mode & 0o777
    try:
        path.chmod(original | writable)
        with pytest.raises(b.Refused, match="BOOTSTRAP_STATE_UNPROVEN"):
            b.handle(recipe, request, service)
        assert not service.starts
    finally:
        path.chmod(original)


@pytest.mark.parametrize("field,value", [("action", {}), ("action", "restart"), ("attempt", True), ("attempt", 6),
                                         ("protocol", True), ("request_id", "other"), ("generation", []),
                                         ("owner_uid", -1), ("service_id", "other"), ("journal_path", "/other"),
                                         ("recipe_sha256", "f" * 64)])
def test_request_cannot_select_commands_or_change_any_identity(fixture, field, value):
    recipe, request, service = fixture
    request[field] = value
    with pytest.raises(b.Refused):
        b.handle(recipe, request, service)
    assert service.queries == 0
    assert not service.starts


@pytest.mark.parametrize("field,value", [("DropInPaths", "/unreviewed.conf"), ("NeedDaemonReload", "yes"),
                                         ("EnvironmentFiles", "/secret"), ("PassEnvironment", "BATC_STATE_DIR"),
                                         ("UnsetEnvironment", "BATC_STATE_DIR"), ("Environment", "BATC_STATE_DIR=/other"),
                                         ("ExecStart", "{ path=/other ; argv[]=/other ; ignore_errors=no ; }"),
                                         ("RootDirectory", "/other"), ("ExecStartPre", "/other"),
                                         ("LoadState", "not-found"), ("Type", "forking"), ("Restart", "always")])
def test_unknown_effective_unit_or_environment_refuses_without_start(fixture, field, value):
    recipe, request, service = fixture
    request["action"] = "ensure"
    service.view[field] = value
    with pytest.raises(b.Refused):
        b.handle(recipe, request, service)
    assert not service.starts


@pytest.mark.parametrize("target", ["journal", "lock", "unit"])
def test_missing_or_replaced_preexisting_identity_never_creates_state(fixture, target):
    recipe, request, service = fixture
    request["action"] = "ensure"
    path = {"journal": Path(recipe.data["journal_path"]), "lock": Path(recipe.data["state_directory"]) / "task-daemon.lock",
            "unit": Path(recipe.data["unit_path"])}[target]
    path.unlink()
    with pytest.raises((OSError, b.Refused)):
        b.handle(recipe, request, service)
    assert not path.exists()
    assert not service.starts


def test_uninitialized_existing_sqlite_file_is_not_a_connector_journal(fixture):
    recipe, request, service = fixture
    request["action"] = "ensure"
    with sqlite3.connect(recipe.data["journal_path"]) as db:
        db.execute("DROP TABLE operations")
    with pytest.raises(b.Refused, match="BOOTSTRAP_JOURNAL_UNPROVEN"):
        b.handle(recipe, request, service)
    assert not service.starts


def test_nonempty_wal_is_included_without_creating_shm_next_to_source(fixture):
    recipe, request, service = fixture
    request["action"] = "ensure"
    path = Path(recipe.data["journal_path"])
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("DROP TABLE operations")
    connection.commit()
    raw = path.read_bytes()
    wal = Path(str(path) + "-wal").read_bytes()
    connection.close()
    # Restore the exact WAL-only change with no source SHM: immutable=1 would
    # incorrectly see operations still present and authorize ensure.
    path.write_bytes(raw)
    wal_path = Path(str(path) + "-wal")
    wal_path.write_bytes(wal)
    wal_path.chmod(0o600)
    assert not Path(str(path) + "-shm").exists()
    with pytest.raises(b.Refused, match="BOOTSTRAP_JOURNAL_UNPROVEN"):
        b.handle(recipe, request, service)
    assert path.read_bytes() == raw and wal_path.read_bytes() == wal
    assert not Path(str(path) + "-shm").exists()
    assert not service.starts


def test_held_owner_never_uses_stale_pointer_or_heartbeat_as_start_authority(fixture):
    recipe, request, service = fixture
    request["action"] = "ensure"
    state = Path(recipe.data["state_directory"])
    (state / "task-service.json").write_text(json.dumps({"pid": 1, "owner_id": "f" * 32, "heartbeat_at": 0}))
    with (state / "task-daemon.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = b.handle(recipe, request, service)
    assert result["state"] == "owner_present"
    assert not service.starts


@pytest.mark.parametrize("state", ["active", "activating", "deactivating", "reloading"])
def test_existing_service_transition_is_query_only_even_without_owner_lock(fixture, state):
    recipe, request, service = fixture
    request["action"] = "ensure"
    service.view["ActiveState"] = state
    result = b.handle(recipe, request, service)
    assert result["state"] == "transitioning"
    assert not service.starts


def test_changed_second_unit_observation_refuses(fixture):
    recipe, request, service = fixture
    request["action"] = "ensure"
    service.change = lambda view: view.update(ActiveState="activating")
    with pytest.raises(b.Refused, match="BOOTSTRAP_SERVICE_CHANGED"):
        b.handle(recipe, request, service)
    assert not service.starts


def test_recipe_pin_does_not_allow_extra_unit_effects(fixture):
    recipe, request, service = fixture
    path = Path(recipe.data["unit_path"])
    path.write_bytes(path.read_bytes() + b"ExecStartPost=/bin/false\n")
    doc = dict(recipe.data)
    doc["unit_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    recipe = b.Recipe(encoded(doc))
    request.update(recipe.expected_request())
    service.recipe = recipe
    with pytest.raises(b.Refused, match="BOOTSTRAP_RECIPE_UNSUPPORTED"):
        b.handle(recipe, request, service)
    assert not service.starts


def test_duplicate_protocol_fields_and_wrong_scalar_types_are_refused():
    for raw in [b'{"action":"query","ACTION":"ensure"}', b'{"x":NaN}', b'[]', b'"string"']:
        with pytest.raises(b.Refused):
            b.Recipe(raw)


def test_protected_recipe_changed_between_query_and_ensure_refuses(fixture, tmp_path):
    recipe, request, service = fixture
    path = tmp_path / "bootstrap-v1.json"
    path.write_bytes(recipe._raw)
    path.chmod(0o600)
    recipe = b.Recipe(path.read_bytes(), path)
    service.recipe = recipe
    request["action"] = "ensure"
    service.change = lambda _: path.write_bytes(b"{}")
    with pytest.raises(b.Refused, match="BOOTSTRAP_RECIPE_CHANGED"):
        b.handle(recipe, request, service)
    assert not service.starts


def test_systemctl_boundary_uses_fixed_user_unit_argv_and_no_central_tokens(monkeypatch, fixture):
    recipe, _, service = fixture
    calls = []
    monkeypatch.setenv("BATC_API_TOKEN", "synthetic-never-forwarded")
    monkeypatch.setenv("BATC_DESKTOP_TOKEN", "synthetic-never-forwarded")
    monkeypatch.setenv("HTTPS_PROXY", "synthetic-never-forwarded")

    def run(argv, **kwargs):
        assert argv[:3] == ["/usr/bin/systemctl", "--user", "--no-pager"]
        assert argv[-2:] == ["--", recipe.data["unit"]]
        assert not any("TOKEN" in key or "PROXY" in key for key in kwargs["env"])
        assert kwargs["timeout"] == 3 and kwargs["check"] is False
        calls.append(argv)
        if "show" in argv:
            kwargs["stdout"].write("".join(f"{k}={v}\n" for k, v in service.view.items()
                                         if k not in {"EnvironmentFiles", "ExecStartPre", "ExecStartPost", "ExecCondition"}).encode())
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(b.subprocess, "run", run)
    adapter = b.Systemd()
    assert adapter.query(recipe.data["unit"]) == service.view
    adapter.start(recipe.data["unit"])
    assert calls[0][3:5] == ["show", "--all"]
    assert calls[1][3:5] == ["start", "--no-block"]


@pytest.mark.parametrize("body", ["import sys; sys.stdout.write('x'*20000)", "import time; time.sleep(8)"])
def test_systemctl_output_and_time_are_bounded_with_only_owned_executable_double(tmp_path, monkeypatch, body):
    executable = tmp_path / "systemctl-double"
    executable.write_text("#!/usr/bin/python3\n" + body + "\n")
    executable.chmod(0o700)
    monkeypatch.setattr(b, "SYSTEMCTL", str(executable))
    with pytest.raises(b.Refused, match="BOOTSTRAP_SERVICE_UNKNOWN"):
        b.Systemd().query("synthetic-connector.service")


def test_current_service_pid_kernel_owner_and_exact_command_prove_running(fixture, monkeypatch):
    recipe, request, service = fixture
    request["action"] = "ensure"
    d = recipe.data
    state = Path(d["state_directory"])
    pointer = state / "task-service.json"
    pointer.write_text(json.dumps({"pid": 4242, "owner_id": "3" * 32, "db_path": d["journal_path"],
                                   "lease_path": str(state / "task-daemon.lock")}))
    pointer.chmod(0o600)
    service.view.update(ActiveState="active", SubState="running", MainPID="4242")
    lock_info = (state / "task-daemon.lock").stat()
    stamp = "7"

    def proc(path, bound):
        if path == "/proc/4242/stat":
            return b"4242 (synthetic) " + b" ".join([b"S"] + [b"0"] * 18 + [stamp.encode()])
        if path == "/proc/4242/status":
            return ("Uid:\t" + "\t".join([str(os.getuid())] * 4) + "\n").encode()
        if path == "/proc/4242/cmdline":
            return ("\0".join(recipe.argv()) + "\0").encode()
        if path == "/proc/locks":
            return f"1: FLOCK ADVISORY WRITE 4242 {os.major(lock_info.st_dev):02x}:{os.minor(lock_info.st_dev):02x}:{lock_info.st_ino} 0 EOF\n".encode()
        raise FileNotFoundError

    monkeypatch.setattr(b, "_proc", proc)
    with (state / "task-daemon.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = b.handle(recipe, request, service)
        assert result["state"] == "running" and result["owner"] == "service"
        service.view["MainPID"] = "4243"
        result = b.handle(recipe, request, service)
        assert result["state"] == "owner_present"
    assert not service.starts


def test_real_owned_child_flock_and_proc_identity_with_synthetic_unit_only(fixture):
    import select
    import subprocess
    recipe, request, service = fixture
    d = recipe.data
    state = Path(d["state_directory"])
    executable = Path(d["executable"])
    executable.write_text('''#!/usr/bin/python3
import fcntl, pathlib, sys
state = pathlib.Path(sys.argv[sys.argv.index("--db") + 1]).parent
with (state / "task-daemon.lock").open("rb") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    print("locked", flush=True)
    sys.stdin.buffer.read()
''')
    executable.chmod(0o700)
    child = subprocess.Popen(recipe.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        assert select.select([child.stdout], [], [], 5)[0], "owned fixture did not enter its lock"
        assert child.stdout.readline() == b"locked\n"
        pointer = state / "task-service.json"
        pointer.write_text(json.dumps({"pid": child.pid, "owner_id": "4" * 32, "db_path": d["journal_path"],
                                       "lease_path": str(state / "task-daemon.lock")}))
        pointer.chmod(0o600)
        service.view.update(ActiveState="active", SubState="running", MainPID=str(child.pid))
        request["action"] = "ensure"
        result = b.handle(recipe, request, service)
        assert result["state"] == "running" and result["owner"] == "service"
        assert not service.starts
    finally:
        child.stdin.close()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
