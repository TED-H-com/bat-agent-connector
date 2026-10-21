from __future__ import annotations

import shutil
import subprocess
from contextlib import contextmanager

import pytest

from bat_agent_connector import api_auth, controller_authority, registry
from bat_agent_connector.config import parse_config
from bat_agent_connector.fleet import Fleet
from bat_agent_connector.task_journal import Journal
from tests.mockbat import TOKEN, MockBat


@pytest.fixture(scope="session")
def clean_wheel(tmp_path_factory):
    # The publication seed runs a fixed baseline product against this tests
    # package. Keep newer-product build imports inside the requested fixture.
    from tests.package_build import build_clean_wheel

    return build_clean_wheel(tmp_path_factory)


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("BATC_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("BATC_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BATC_TEST_TOKEN", TOKEN)
    monkeypatch.delenv("BATC_DEVICE_ID", raising=False)
    # Autouse setup precedes other per-test fixtures, so this teardown runs
    # after their servers, journals and worktrees have been closed. Retention
    # alone only removes passing tmp_paths; failed tests must release large state too.
    try:
        yield tmp_path
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.fixture
async def mock():
    m = MockBat()
    await m.start()
    yield m
    await m.stop()


def make_config(
    mock: MockBat, *, writes=False, orchestrate=False, tabs=False, fingerprint=None, safety=None, **host_extra
):
    host = {
        "url": mock.url,
        "fingerprint": fingerprint or mock.fingerprint,
        "token_ref": "env:BATC_TEST_TOKEN",
        "writes": writes,
        "orchestrate": orchestrate,
        "orchestrate_register_tabs": tabs,
        "orchestrate_max_sessions": 2,
        **host_extra,
    }
    return parse_config({"hosts": {"h1": host}, "safety": safety or {"write_min_interval_s": 60}})


@contextmanager
def controller_scope(journal, principal=None):
    """Explicit authenticated test caller; never infer identity from Fleet.actor."""
    principal = principal or api_auth.Principal("test-operator", frozenset(api_auth.SCOPES))
    value = controller_authority.binding(journal.db, principal)
    with controller_authority.use(value, journal):
        yield value


@pytest.fixture
def controller_context(tmp_path):
    """Opt-in authority backed by a real durable server identity, without legacy adoption."""
    # Journal must create its own private directory: pytest's existing Windows
    # temporary directory need not have the current-user owner/private DACL.
    journal = Journal(tmp_path / "test-controller" / "journal.db")
    try:
        with controller_scope(journal) as value:
            yield journal, value
    finally:
        journal.close()


@pytest.fixture
def fleet_factory(mock, controller_context):
    fleets = []

    def make(**kw):
        read_only = kw.pop("read_only", False)
        f = Fleet(make_config(mock, **kw), read_only=read_only, idle_timeout=0, actor="test")
        fleets.append(f)
        return f

    yield make


def adopt(session_id: str, *, host: str = "h1", cwd: str = "/srv/demo", authority=None,
          journal=None, **fields) -> None:
    """Record a mock session as one the connector created (registry creation record, start acknowledged).

    Pair it with ``managed_roots`` covering ``cwd`` (or a connector worktree ``cwd``) so the policy lets
    write tools drive it; without that the session stays a legacy boundary. The caller must
    activate controller_scope/controller_context or supply authority (and journal for PM membership).
    This helper only creates new proven fixture records; it never stamps raw legacy rows."""
    if authority is not None:
        with controller_authority.use(authority, journal):
            return adopt(session_id, host=host, cwd=cwd, **fields)
    registry.reserve(host, {"session_id": session_id, "cwd": cwd, "origin_cwd": fields.pop("origin_cwd", cwd),
                            **fields}, 32)
    registry.update(host, session_id, status="active")


@pytest.fixture(scope="session")
def checkpoint_repo_seed(tmp_path_factory, request):
    """Build the common two-commit history once; copies keep independent Git state."""
    repo = tmp_path_factory.mktemp("checkpoint-seed")
    request.addfinalizer(lambda: shutil.rmtree(repo, ignore_errors=True))

    return build_checkpoint_repo(repo)


def build_checkpoint_repo(repo):
    """Also support standalone desktop fixtures that run outside pytest."""
    repo.mkdir(parents=True, exist_ok=True)

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "--template=", "-q", "-b", "main")
    git("config", "user.email", "dev@example.invalid")
    git("config", "user.name", "dev")
    git("remote", "add", "origin", "https://github.example/o/r.git")
    for i in (1, 2):
        (repo / "notes.txt").write_text(f"v{i}\n")
        git("add", "notes.txt")
        git("commit", "-q", "-m", f"step {i}")
    # Consolidate six loose objects before copying this repository many times.
    git("repack", "-ad")
    return repo
