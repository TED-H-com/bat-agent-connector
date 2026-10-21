"""Real same-version VCS installs: loaded launcher and owner identities stay separate."""
from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from scripts.validate_connector_update import copy_fixture_source
from tests.fixture_vcs_install import install_fixture_candidate

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "fixtures" / "managed-runtime-identity.json"
CONTROLLER = """
import json, sys
from bat_agent_connector import managed_runtime as runtime
children = []
original = runtime._spawn
def spawn(root):
    child = original(root)
    children.append(child)
    return child
runtime._spawn = spawn
try:
    launch = runtime.ensure(sys.argv[1], timeout=15)
    print(json.dumps({"launch": launch, "pid": children[0].pid}), flush=True)
    input()
finally:
    for child in children:
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=10)
        except Exception:
            child.kill()
            child.wait(timeout=10)
"""


def run(args, *, env, cwd=None):
    result = subprocess.run(args, env=env, cwd=cwd, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture(scope="module")
def packages(tmp_path_factory):
    scratch = tmp_path_factory.mktemp("managed-vcs-packages")
    source = scratch / "source"
    source.mkdir()
    copy_fixture_source(ROOT, source)
    # The cache supplies dependencies only; golden identity comes from the fixture packages.
    env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "UV_CACHE_DIR")
           if key in os.environ}
    env.update(UV_NO_CONFIG="1", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_DATE="2026-01-01T00:00:00+00:00", GIT_COMMITTER_DATE="2026-01-01T00:00:00+00:00")
    env.setdefault("UV_CACHE_DIR", str(scratch / "cache"))
    git = shutil.which("git")
    assert git
    run([git, "init", "--quiet", str(source)], env=env)
    installed = {}
    for candidate in ("a", "b"):
        if candidate == "b":
            path = source / "src/bat_agent_connector/__init__.py"
            path.write_text(path.read_text() + "\n# Fixture candidate B, with the same package version.\n")
        run([git, "-C", str(source), "add", "."], env=env)
        run([git, "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", "commit", "--quiet", "-m", candidate], env=env)
        sha = run([git, "-C", str(source), "rev-parse", "HEAD"], env=env)
        interpreter = install_fixture_candidate(source, sha, scratch / ("environment-" + candidate),
                                                env=env, lock_root=ROOT)
        identity = json.loads(run([str(interpreter), "-I", "-c",
            "import json;from bat_agent_connector.build_identity import build_identity;print(json.dumps(build_identity()))"],
            env=env, cwd=scratch))
        assert identity["commit"] == sha and identity["source"] == "vcs"
        metadata = Path(json.loads(run([str(interpreter), "-I", "-c",
            "import json;from importlib.metadata import distribution;"
            "d=distribution('bat-agent-connector');"
            "print(json.dumps(str(next(d.locate_file(p) for p in d.files if str(p).endswith('.dist-info/direct_url.json')))))"],
            env=env, cwd=scratch)))
        direct = json.loads(metadata.read_text())
        assert direct["vcs_info"]["commit_id"] == sha
        build_info = Path(json.loads(run([str(interpreter), "-I", "-c",
            "import json;from pathlib import Path;from bat_agent_connector import build_identity;"
            "print(json.dumps(str(Path(build_identity.__file__).with_name('build-info.json'))))"],
            env=env, cwd=scratch)))
        assert json.loads(build_info.read_text())["commit"] == sha
        installed[candidate] = {"python": str(interpreter), "commit": sha, "metadata": metadata,
                                "build_info": build_info, "identity": identity}
    assert installed["a"]["commit"] != installed["b"]["commit"]
    assert installed["a"]["identity"]["version"] == installed["b"]["identity"]["version"]
    empty_path = scratch / "empty-path"
    empty_path.mkdir()
    runtime_env = {**env, "PATH": str(empty_path), "CODEX_HOME": str(scratch / "codex"),
                   "XDG_CONFIG_HOME": str(scratch / "config"), "XDG_DATA_HOME": str(scratch / "data")}
    yield scratch, installed, runtime_env
    shutil.rmtree(scratch)


@contextmanager
def owner(packages, name):
    scratch, installed, env = packages
    root = scratch / name
    process = subprocess.Popen([installed["a"]["python"], "-I", "-u", "-c", CONTROLLER,
        str(root)], env=env, cwd=scratch,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=30), "Fixture launcher did not report its child"
        line = process.stdout.readline()
        assert line, process.stderr.read()
        first = json.loads(line)
        yield root, first
    finally:
        if process.poll() is None:
            process.communicate(input="stop\n", timeout=30)
        assert process.returncode == 0
        assert not (root / "state/task-service.json").exists() or not _owner_alive(root, installed["a"], env)
        shutil.rmtree(root, ignore_errors=True)


def _owner_alive(root, package, env):
    return json.loads(run([package["python"], "-I", "-c",
        "import json,sys;from pathlib import Path;from bat_agent_connector.managed_runtime import _running;"
        "print(json.dumps(_running(Path(sys.argv[1]))))",
        str(root)], env=env, cwd=root.parent))


def ensure(package, root, env, *, older=False):
    script = """
import json, sys
from bat_agent_connector import managed_runtime as runtime
if sys.argv[2] == 'older':
    request = runtime._request
    def old_response(endpoint, path, **kwargs):
        value = request(endpoint, path, **kwargs)
        if path == '/api/v1/capabilities':
            value.pop('build', None)
        return value
    runtime._request = old_response
print(json.dumps(runtime.ensure(sys.argv[1], timeout=15)))
"""
    return json.loads(run([package["python"], "-I", "-c", script, str(root), "older" if older else "new"],
                          env=env, cwd=root.parent))


@contextmanager
def missing_provenance(package):
    """An install without either VCS metadata or embedded build attribution."""
    originals = {path: path.read_bytes() for path in (package["metadata"], package["build_info"])}
    for path in originals:
        assert path.stat().st_nlink == 1, "Fixture installs must use UV_LINK_MODE=copy"

    def restore():
        for path, content in originals.items():
            # Restoring twice must also create fresh inodes, never truncate an
            # installed file that could have been linked to a package cache.
            path.unlink(missing_ok=True)
            path.write_bytes(content)

    try:
        for path in originals:
            path.unlink()
        yield restore
    finally:
        restore()


@pytest.fixture(scope="module")
def observed(packages):
    scratch, installed, env = packages
    observations = {}
    with owner(packages, "installation") as (root, first):
        before = json.loads((root / "state/task-service.json").read_text())
        assert before["pid"] == first["pid"]
        same = ensure(installed["a"], root, env)
        different = ensure(installed["b"], root, env)
        after = json.loads((root / "state/task-service.json").read_text())
        assert after["pid"] == before["pid"]
        for field in ("endpoint", "actor", "server_id", "principal_id", "token", "runtime_version"):
            assert same[field] == different[field] == first["launch"][field]
        observations.update(same=same, differs=different)
        # An unattributed launcher cannot identify its own installed build.
        with missing_provenance(installed["b"]):
            observations["unknown_bundled"] = ensure(installed["b"], root, env)
        observations["older_service"] = ensure(installed["b"], root, env, older=True)
    # A service started without attribution keeps that unknown loaded snapshot,
    # even when an installation repair restores provenance while it is running.
    with missing_provenance(installed["a"]) as restore:
        with owner(packages, "unknown-installation") as (root, _first):
            restore()
            observations["unknown_running"] = ensure(installed["b"], root, env)
    return observations


def test_same_version_real_packages_report_distinct_loaded_identities(packages, observed):
    _scratch, installed, _env = packages
    different = observed["differs"]
    assert different["bundled_runtime"]["commit"] == installed["b"]["commit"]
    assert different["running_runtime"]["commit"] == installed["a"]["commit"]
    assert different["runtime_difference"] == "differs"
    assert observed["same"]["runtime_difference"] == "same"
    for name in ("unknown_bundled", "unknown_running", "older_service"):
        assert observed[name]["runtime_difference"] == "unknown"
    assert observed["unknown_bundled"]["bundled_runtime"]["commit"] is None
    assert observed["unknown_running"]["running_runtime"]["commit"] is None
    assert observed["older_service"]["running_runtime"]["commit"] is None
    for reply in observed.values():
        assert reply["bundled_runtime"]["source"] == "this_process"
        assert reply["running_runtime"]["source"] == "capabilities"
        assert 0 < reply["bundled_runtime"]["observed_at"] <= reply["running_runtime"]["observed_at"] <= time.time()


def test_managed_runtime_identity_matches_producer_golden(packages, observed):
    _scratch, installed, _env = packages
    normalized = json.loads(json.dumps(observed))
    for reply in normalized.values():
        reply.update(endpoint="http://127.0.0.1:45123", actor="fixture-operator", server_id="fixture-server",
                     principal_id="fixture-principal", token="fixture-token")
        for field, when in (("bundled_runtime", 1790000000.0), ("running_runtime", 1790000001.0)):
            fact = reply[field]
            if fact["commit"] is not None:
                fact["commit"] = next(name * 40 for name, package in installed.items() if package["commit"] == fact["commit"])
            fact["observed_at"] = when
    serialized = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if os.environ.get("BATC_UPDATE_MANAGED_RUNTIME_GOLDEN") == "1":
        GOLDEN.write_text(serialized)
    assert serialized == GOLDEN.read_text()
