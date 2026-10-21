from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from copy import deepcopy
from pathlib import Path

import pytest

from bat_agent_connector import __version__, mcp_server, task_scoped_mcp
from bat_agent_connector.config import Config, parse_config, tomllib
from bat_agent_connector.errors import ConfigError, TokenUnavailable
from bat_agent_connector.importer import read_bat_profiles, render_hosts_toml
from bat_agent_connector.mcp_server import (
    OPERATION_TOOLS,
    ORCHESTRATE_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    build_server,
)
from bat_agent_connector.update_install import uv_git_env
from scripts.validate_connector_update import copy_fixture_source, identity_line
from tests.conftest import make_config
from tests.fixture_vcs_install import install_fixture_candidate


@pytest.fixture(scope="module")
def in_place_packages(tmp_path_factory):
    """Two real VCS packages, one install path; no live BAT or model."""
    scratch = tmp_path_factory.mktemp("mcp-in-place")
    root = Path(__file__).resolve().parents[1]
    source = scratch / "source"
    source.mkdir()
    copy_fixture_source(root, source)
    home = scratch / "home"
    home.mkdir()
    temporary = scratch / "temporary"
    temporary.mkdir()
    env = uv_git_env({"HOME": str(home), "PATH": os.environ["PATH"], "LANG": "C.UTF-8", "TMPDIR": str(temporary),
                      "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
                      "UV_NO_CONFIG": "true", "UV_PYTHON_DOWNLOADS": "never",
                      "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(scratch / "cache"))})
    git = shutil.which("git")
    assert git, "Real package fixture requires Git"

    def run(args, *, cwd=None):
        result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, f"{args!r}\n{result.stdout}\n{result.stderr}"
        return result.stdout.strip()

    run([git, "init", "--quiet", str(source)])

    def commit():
        run([git, "-C", str(source), "add", "."])
        run([git, "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "core.hooksPath=" + os.devnull, "commit", "--quiet", "-m", "identity fixture"])
        return run([git, "-C", str(source), "rev-parse", "HEAD"])

    first_sha = commit()
    environment = scratch / "environment"

    def install(sha):
        return install_fixture_candidate(source, sha, environment, env=env, lock_root=root)

    def provenance():
        script = (
            "import json;from pathlib import Path;from importlib import metadata;"
            "from bat_agent_connector import build_identity as identity;"
            "print(json.dumps({'build':identity.build_identity(),"
            "'embedded':json.loads(Path(identity.__file__).with_name('build-info.json').read_text())['commit'],"
            "'direct_url':json.loads(metadata.distribution('bat-agent-connector').read_text('direct_url.json'))"
            "['vcs_info']['commit_id']}))"
        )
        observed = json.loads(run([str(python), "-I", "-B", "-c", script]))
        print("REAL IN_PLACE PROVENANCE " + json.dumps(observed, sort_keys=True), flush=True)
        return observed

    def capabilities(name):
        script = (
            "import asyncio,json;from bat_agent_connector.config import Config;"
            "from bat_agent_connector.task_daemon import TaskDaemon;"
            "from bat_agent_connector.api_auth import Principal;"
            "from bat_agent_connector.api_v1 import ApiV1\n"
            "async def read():\n"
            f" d=TaskDaemon(Config(hosts={{}}),{str(scratch / (name + '.sqlite3'))!r})\n"
            " try: print(json.dumps((await ApiV1(d).capabilities(Principal('identity-reader',frozenset({'observe'}))))[1]))\n"
            " finally:\n"
            "  await d.fleet.close();await d.inventory.close();d.journal.close()\n"
            "asyncio.run(read())"
        )
        return json.loads(run([str(python), "-I", "-B", "-c", script]))

    python = install(first_sha)
    first_provenance = provenance()
    first_caps = capabilities("first")
    script = ("import json;from bat_agent_connector.build_identity import build_identity;"
              "print(json.dumps(build_identity()));input();print(json.dumps(build_identity()))")
    # A second reader imports before upgrade but makes its first identity call afterwards.
    late_script = ("import json;from bat_agent_connector.build_identity import build_identity;"
                   "print(json.dumps({'ready':True}));input();print(json.dumps(build_identity()))")
    readers = []
    try:
        for body in (script, late_script):
            readers.append(subprocess.Popen([str(python), "-I", "-B", "-u", "-c", body], env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        before = identity_line(readers[0])
        assert identity_line(readers[1]) == {"ready": True}
        parts = list(map(int, __version__.split(".")))
        parts[2] += 1
        next_version = ".".join(map(str, parts))
        for relative, old, new in (
            ("pyproject.toml", f'version = "{__version__}"', f'version = "{next_version}"'),
            ("uv.lock", f'name = "bat-agent-connector"\nversion = "{__version__}"',
             f'name = "bat-agent-connector"\nversion = "{next_version}"'),
            ("src/bat_agent_connector/__init__.py", f'__version__ = "{__version__}"', f'__version__ = "{next_version}"'),
            ("skills/bat-agent-connector/SKILL.md", f'version: {__version__}\n', f'version: {next_version}\n'),
        ):
            path = source / relative
            text = path.read_text()
            assert text.count(old) == 1
            path.write_text(text.replace(old, new, 1))
        second_sha = commit()
        install(second_sha)
        second_provenance = provenance()
        second_caps = capabilities("second")
        observed = []
        for reader in readers:
            reader.stdin.write("continue\n")
            reader.stdin.flush()
            observed.append(identity_line(reader))
            assert reader.wait(timeout=10) == 0
    finally:
        for reader in readers:
            if reader.poll() is None:
                reader.terminate()
                try:
                    reader.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    reader.kill()
                    reader.wait()
            for stream in (reader.stdin, reader.stdout, reader.stderr):
                stream.close()
    return {"before": before, "after": observed[0], "late": observed[1], "first": first_caps,
            "second": second_caps, "first_sha": first_sha, "second_sha": second_sha,
            "first_provenance": first_provenance, "second_provenance": second_provenance}


def test_in_place_upgrade_never_mixes_loaded_version_and_installed_commit(in_place_packages):
    data = in_place_packages
    for name in ("first", "second"):
        proof = data[name + "_provenance"]
        assert proof["build"] == data[name]["build"]
        assert proof["build"]["source"] == "vcs"
        assert proof["build"]["commit"] == proof["embedded"] == proof["direct_url"] == data[name + "_sha"]
    assert data["before"] == data["first"]["build"]
    assert data["before"]["commit"] == data["first_sha"]
    assert data["second"]["build"]["commit"] == data["second_sha"]
    assert data["second"]["build"]["version"] != data["before"]["version"]
    identities = {key: data[key] for key in ("before", "after", "late")}
    identities["installed"] = data["second"]["build"]
    assert data["after"] == data["before"], json.dumps(identities)
    assert data["late"] == data["before"], json.dumps(identities)


@pytest.mark.parametrize("entry", ["bat", "bat-task"])
async def test_both_initializations_report_loaded_product_version(entry):
    if entry == "bat":
        server, fleet = build_server(Config(hosts={}), read_only=True)
    else:
        server, fleet = task_scoped_mcp.build("fixture-task"), None
    try:
        options = server._lowlevel_server.create_initialization_options()
        assert options.server_name == entry
        assert options.server_version and options.server_version == __version__
    finally:
        if fleet:
            await fleet.close()


@pytest.mark.parametrize("entry", ["bat", "bat-task"])
async def test_shared_identity_resource_keeps_loaded_and_central_builds_separate(entry, in_place_packages, monkeypatch):
    caps = in_place_packages["first"]
    monkeypatch.setenv("BATC_API_TOKEN", "fixture-api-token")

    def request(method, **kwargs):
        assert method == "api_capabilities"
        assert kwargs["_auth_token"] == "fixture-api-token"
        return deepcopy(caps)

    monkeypatch.setattr(mcp_server, "task_request", request)
    monkeypatch.setattr(task_scoped_mcp, "request", request)
    if entry == "bat":
        server, fleet = build_server(Config(hosts={}), read_only=True, principal_only=True)
    else:
        server, fleet = task_scoped_mcp.build("fixture-task"), None
    try:
        resources = await server.list_resources()
        assert any(str(resource.uri) == "batc://identity" for resource in resources)

        async def read():
            contents = list(await server.read_resource("batc://identity"))
            assert len(contents) == 1
            return json.loads(contents[0].content)

        first = await read()
        assert first["mcp"]["build"]["version"] == __version__
        assert first["mcp"]["source"] == "mcp_process"
        assert 0 < first["mcp"]["started_at"] <= time.time()
        assert first["central"]["build"] == caps["build"]
        assert first["central"]["source"] == "central_capabilities"
        assert 0 < first["central"]["observed_at"] <= time.time()
        caps = in_place_packages["second"]
        second = await read()
        assert second["mcp"] == first["mcp"]
        assert second["central"]["build"] == caps["build"]
        assert second["central"]["build"] != first["central"]["build"]
        assert second["central"]["observed_at"] >= first["central"]["observed_at"]
    finally:
        if fleet:
            await fleet.close()


@pytest.mark.parametrize("entry", ["bat", "bat-task"])
async def test_identity_remains_readable_when_central_is_not_authorized(entry, monkeypatch):
    monkeypatch.delenv("BATC_API_TOKEN", raising=False)
    monkeypatch.setenv("BATC_TASK_CAPABILITY", "fixture-task-capability")
    calls = []

    def refused(method, **kwargs):
        calls.append((method, kwargs))
        raise ValueError("task API authorization failed: private credential detail")

    monkeypatch.setattr(mcp_server, "task_request", refused)
    monkeypatch.setattr(task_scoped_mcp, "request", refused)
    if entry == "bat":
        server, fleet = build_server(Config(hosts={}), read_only=True, principal_only=True)
    else:
        server, fleet = task_scoped_mcp.build("fixture-task"), None
    try:
        contents = list(await server.read_resource("batc://identity"))
        identity = json.loads(contents[0].content)
        assert identity["mcp"]["build"]["version"] == __version__
        assert identity["central"]["build"] is None
        assert identity["central"]["status"] == "unavailable"
        assert identity["central"]["reason"] == "CENTRAL_IDENTITY_UNAVAILABLE"
        assert "private credential detail" not in contents[0].content
        assert not calls if entry == "bat" else len(calls) == 1
    finally:
        if fleet:
            await fleet.close()


async def _names(cfg, **kw):
    server, _ = build_server(cfg, **kw)
    return {t.name for t in await server.list_tools()}


async def test_tool_registration_tiers(mock):
    # Accepted start keys remain recoverable after a local host tier is disabled.
    # Current central authorization still decides whether a new key can start work.
    central = {"worktree_merge", "session_record_verification", "session_failover", "session_start", "session_relay", "fanout_plan_session", "fanout_from_plan", "work_continue_from_repository"}
    assert await _names(make_config(mock)) == set(READ_TOOLS + OPERATION_TOOLS) | central
    assert await _names(make_config(mock, writes=True)) == set(READ_TOOLS + OPERATION_TOOLS + WRITE_TOOLS) | central
    assert await _names(make_config(mock, writes=True, orchestrate=True)) == set(
        READ_TOOLS + OPERATION_TOOLS + WRITE_TOOLS + ORCHESTRATE_TOOLS
    ) | central
    assert await _names(make_config(mock, writes=True, orchestrate=True), read_only=True) == set(READ_TOOLS)


async def test_operation_tools_need_confirm_and_the_callers_own_token(mock, monkeypatch):
    server, fleet = build_server(make_config(mock))
    monkeypatch.delenv("BATC_API_TOKEN", raising=False)
    for args, needle in (({"operation_id": "op_" + "0" * 32}, "confirm=true"),
                         ({"operation_id": "op_" + "0" * 32, "confirm": True}, "BATC_API_TOKEN")):
        try:
            res = await server.call_tool("operation_cancel", args)
            text = json.dumps(res.model_dump() if hasattr(res, "model_dump") else res, default=str)
        except Exception as e:  # noqa: BLE001 - the server may raise the tool's refusal instead
            text = str(e)
        assert needle in text
    await fleet.close()


async def test_mcp_call_read_tool(mock):
    server, fleet = build_server(make_config(mock))
    res = await server.call_tool("sessions_list", {"host": "h1"})
    text = json.dumps(res.model_dump() if hasattr(res, "model_dump") else res, default=str)
    assert "sess-claude-0001" in text
    await fleet.close()


def test_config_rejects_inline_token_and_bad_tiers():
    base = {"url": "wss://127.0.0.1:1/", "fingerprint": "AA" * 32, "token_ref": "env:X"}
    with pytest.raises(ConfigError):
        parse_config({"hosts": {"a": {**base, "token": "abc"}}})
    with pytest.raises(ConfigError):
        parse_config({"hosts": {"a": {**base, "orchestrate": True}}})
    with pytest.raises(ConfigError):
        parse_config({"hosts": {"a": {**base, "url": "ws://x"}}})
    cfg = parse_config({"hosts": {"a": base}})
    assert "abc" not in repr(cfg.host("a"))


def test_importer_never_copies_tokens(tmp_path):
    pdir = tmp_path / "profiles"
    pdir.mkdir()
    (pdir / "index.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {"id": "default", "type": "local"},
                    {
                        "id": "box-a",
                        "name": "Box A",
                        "type": "remote",
                        "remoteHost": "127.0.0.9",
                        "remotePort": 9009,
                        "remoteFingerprint": "AB:" * 31 + "AB",
                        "remoteProfileId": "default",
                    },
                ]
            }
        )
    )
    (pdir / "remote-tokens.enc.json").write_text(
        json.dumps({"enc": False, "data": json.dumps({"tokens": {"box-a": "SUPERSECRETTOKEN"}})})
    )
    (pdir / "remote-tokens.enc.json").chmod(0o600)
    text = render_hosts_toml(read_bat_profiles(pdir), profiles_dir=str(pdir), rename={"box-a": "a1"})
    assert "SUPERSECRETTOKEN" not in text and 'token_ref = "bat-profile:box-a"' in text
    cfg = parse_config(tomllib.loads(text))
    h = cfg.host("a1")
    assert h.writes is False and h.url == "wss://127.0.0.9:9009/"
    assert h.resolve_token() == "SUPERSECRETTOKEN"


@pytest.mark.parametrize("document", [[], None, {"enc": False, "data": ["damaged"]},
    {"enc": False, "data": '["damaged"]'}, {"enc": False, "data": {"tokens": ["damaged"]}}])
def test_damaged_bat_profile_tokens_report_unavailable(tmp_path, document):
    (tmp_path / "remote-tokens.enc.json").write_text(json.dumps(document))
    (tmp_path / "remote-tokens.enc.json").chmod(0o600)
    config = parse_config({"hosts": {"fixture": {"url": "wss://127.0.0.1:1/", "fingerprint": "AA" * 32,
        "token_ref": "bat-profile:fixture", "bat_profiles_dir": str(tmp_path)}}})
    host = config.host("fixture")
    assert host.token_available() is False
    with pytest.raises(TokenUnavailable):
        host.resolve_token()


@pytest.mark.parametrize("value", [None, True, False, 0, 1, [], {}, "", "SSH", "Local", "auto"])
def test_git_transport_is_a_strict_enum_with_fixed_config_code(value):
    base = {"url": "wss://127.0.0.1:9876/", "fingerprint": "aa" * 32, "token_ref": "env:FIXTURE_TOKEN"}
    with pytest.raises(ConfigError) as error:
        parse_config({"hosts": {"fixture": {**base, "git": value}}})
    assert error.value.code == "HOST_GIT_INVALID"


@pytest.mark.parametrize("url", ["wss://127.0.0.1:9876/", "wss://localhost:19876/", "wss://[::1]:9876/", "wss://fixture.example.invalid:9876/"])
def test_loopback_and_tunnel_never_select_local_git(url):
    base = {"url": url, "fingerprint": "aa" * 32, "token_ref": "env:FIXTURE_TOKEN"}
    for fields in ({}, {"git": "ssh"}):
        assert parse_config({"hosts": {"fixture": {**base, **fields}}}).host("fixture").git == "ssh"
    assert parse_config({"hosts": {"fixture": {**base, "git": "local"}}}).host("fixture").git == "local"
