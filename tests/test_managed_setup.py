"""Real mock BAT setup, private staging and journaled configuration recovery."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from bat_agent_connector import (
    api_auth,
    client,
    dashboard_sync,
    inventory,
    managed_runtime,
    platform_files,
    pm_work_profiles,
    registry,
)
from bat_agent_connector import managed_setup as setup
from bat_agent_connector.channels import GUARDED_CHANNELS, ORCHESTRATE_CHANNELS, WRITE_CHANNELS
from bat_agent_connector.config import GitHubRepo, RepositorySyncConfig, load_config, parse_config, tomllib
from bat_agent_connector.host_git import HostGitRunner
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_daemon import TaskDaemon
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token


@pytest.fixture
async def installed(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    platform_files.ensure_private_directory(root)
    installation = managed_runtime._prepare(root)
    platform_files.ensure_private_directory(root / "config")
    monkeypatch.setenv("BATC_CONFIG_DIR", str(root / "config"))
    monkeypatch.setenv("BATC_STATE_DIR", str(root / "state"))
    path = root / "config" / "hosts.toml"
    source = {"client": {"label": "Fixture"}, "hosts": {}, "safety": {"write_min_interval_s": 0},
              "operator_extension": {"keep": ["one", "two"], "nested": {"enabled": True}}}
    platform_files.atomic_write(path, setup._encode(source))
    daemon = TaskDaemon(parse_config(source, path), root / "state" / "tasks.sqlite3")
    principal = api_auth.Principal(installation["actor"], frozenset(api_auth.SCOPES))
    installation.update(dashboard_sync.identity(daemon.journal, principal), state="ready")
    managed_runtime._write(root / managed_runtime.MANIFEST, installation)
    daemon.managed_installation = {**installation, "data_dir": root}
    setup.install(daemon)
    yield daemon, principal, root
    if daemon._lease_fd is not None:  # restart tests already shut down this exact owner
        await daemon.ops.drain()
        await daemon.fleet.close()
        await daemon.inventory.close()
        daemon.journal.close()


def host_params(daemon, principal, mock, **extra):
    ref = setup.stage_secret(daemon, principal, {"kind": "bat", "value": mock.token})["secret_ref"]
    return {"url": mock.url, "fingerprint": mock.fingerprint, "profile_id": "default", "secret_ref": ref,
            "writes": True, "orchestrate": True, "managed_roots": ["/srv/owned"], **extra}


def create(installed, action, target, params, *, key="fixture-setup", revision=None):
    daemon, principal, _ = installed
    return daemon.ops.create(principal, action=action, target=target, params=params,
        preconditions={"config_revision": revision or setup.state(daemon, principal)["revision"]},
        idempotency_key=key)[0]


async def settle(installed, operation):
    daemon, _, _ = installed
    await settle_operations(daemon.ops)
    return daemon.ops.get(operation["operation_id"])


async def test_setup_real_bat_preserves_config_and_exposes_no_secrets(installed, mock, monkeypatch):
    daemon, principal, root = installed
    aliases = []
    async def ssh(alias):
        aliases.append(alias)
    monkeypatch.setattr(setup, "_probe_ssh", ssh)
    params = host_params(daemon, principal, mock, ssh_alias="fixture-bat", shared_clone_worktrees=True)
    before = setup.state(daemon, principal)["revision"]
    operation = create(installed, "setup.host", {"host": "fixture"}, params, revision=before)
    result = await settle(installed, operation)
    assert result["status"] == "succeeded", result
    assert aliases == ["fixture-bat"]
    assert result["result"]["workspaces"] == [{"workspace_id": "ws-1", "name": "demo-project"},
                                              {"workspace_id": "ws-2", "name": "other"}]
    raw = (root / "config" / "hosts.toml").read_text()
    data = tomllib.loads(raw)
    assert data["operator_extension"] == {"keep": ["one", "two"], "nested": {"enabled": True}}
    assert data["safety"]["write_min_interval_s"] == 0
    assert daemon.fleet.config.host("fixture").orchestrate
    assert daemon.fleet.config.host("fixture").shared_clone_worktrees
    assert daemon.inventory.fleet.config is daemon.fleet.config
    assert daemon.inventory.observation.config is daemon.fleet.config
    assert daemon.adapter.verifier.settings.ssh_hosts == {"fixture": "fixture-bat"}
    assert daemon.ops.context["git_runner"].available("fixture")
    assert daemon.ops.context["artifact_host"].available("fixture")
    assert mock.token not in raw and mock.token not in json.dumps(result)
    assert mock.token not in " ".join(str(tuple(row)) for row in daemon.journal.db.execute("SELECT * FROM operations"))
    assert not [frame for frame in mock.invokes if frame["channel"] in WRITE_CHANNELS | ORCHESTRATE_CHANNELS | GUARDED_CHANNELS]
    assert create(installed, "setup.host", {"host": "fixture"}, params, revision=before)["operation_id"] == operation["operation_id"]


async def test_bad_pin_or_profile_is_never_published(installed, mock):
    daemon, principal, root = installed
    original = (root / "config" / "hosts.toml").read_bytes()
    params = host_params(daemon, principal, mock, fingerprint="00" * 32)
    result = await settle(installed, create(installed, "setup.host", {"host": "fixture"}, params))
    assert result["status"] == "failed" and result["error_code"] == "SETUP_PROBE_FAILED"
    assert not mock.auth_frames  # pin failure happens before the token crosses the wire
    assert (root / "config" / "hosts.toml").read_bytes() == original
    params = host_params(daemon, principal, mock, profile_id="missing")
    result = await settle(installed, create(installed, "setup.host", {"host": "fixture"}, params, key="bad-profile"))
    assert result["status"] == "failed" and result["error_code"] == "PROFILE_NOT_FOUND"
    assert (root / "config" / "hosts.toml").read_bytes() == original


async def test_reviewed_host_permissions_enable_new_managed_start_without_touching_manual_sessions(installed, mock):
    daemon, principal, _ = installed
    params = host_params(daemon, principal, mock, shared_clone_worktrees=True)
    result = await settle(installed, create(installed, "setup.host", {"host": "fixture"}, params))
    assert result["status"] == "succeeded"
    assert not [frame for frame in mock.invokes if frame["channel"] in WRITE_CHANNELS | ORCHESTRATE_CHANNELS | GUARDED_CHANNELS]
    original_sessions = json.dumps(mock.states, sort_keys=True)
    mock.handlers["git:log"] = lambda _: [{"hash": "a" * 40}]
    operation = daemon.ops.create(principal, action="session.start", target={"host": "fixture", "workspace": "ws-1"},
        params={"prompt": "Run only this mock fixture task"}, idempotency_key="onboarded-start")[0]
    started = await settle(installed, operation)
    assert started["status"] == "succeeded", started
    session_id = started["result"]["session_id"]
    assert registry.get("fixture", session_id)["start_operation_id"] == operation["operation_id"]
    manual = {key: value for key, value in mock.states.items() if key != session_id}
    assert json.dumps(manual, sort_keys=True) == original_sessions
    assert not registry.get("fixture", "sess-claude-0001")


async def test_noop_setup_keeps_active_connection_and_original_digest(installed, mock):
    daemon, principal, _ = installed
    params = host_params(daemon, principal, mock)
    first = await settle(installed, create(installed, "setup.host", {"host": "fixture"}, params))
    client = daemon.fleet.client("fixture")
    await client.ping()
    assert client.connected
    second = await settle(installed, create(installed, "setup.host", {"host": "fixture"}, params, key="review-same-configuration"))
    assert second["status"] == "succeeded", second
    assert second["result"]["revision"] == first["result"]["revision"]
    assert daemon.fleet.client("fixture") is client and client.connected


async def test_existing_profile_connection_remains_one_source_across_read_only_entries(installed, mock, monkeypatch):
    """Real setup/Config/HTTP/observation; only BAT and SSH verification are doubles."""
    daemon, principal, root = installed
    profiles, ssh = root / "profiles", root / "ssh" / "config"
    profiles.mkdir(mode=0o700)
    ssh.parent.mkdir(mode=0o700)
    # Handwritten, verified against the readers: fleet-core's profile-index.json,
    # credential_tests.rs::profile_store and configuration SSH fixture. No real
    # BAT/SSH capture: IDs/endpoint/pin/token are synthetic; encrypted stores,
    # case differences, directory fallbacks and SSH key trust are not exercised.
    endpoint = urlsplit(mock.url)
    (profiles / "index.json").write_text(json.dumps({"profiles": [{
        "id": "profile-1", "type": "remote", "remoteHost": endpoint.hostname,
        "remotePort": endpoint.port, "remoteProfileId": "default", "remoteFingerprint": mock.fingerprint}],
        "activeProfileIds": []}))
    store = profiles / "remote-tokens.enc.json"
    platform_files.atomic_write(store, json.dumps({"enc": False,
        "data": json.dumps({"tokens": {"profile-1": mock.token}})}).encode())
    ssh_bytes = (Path(__file__).resolve().parents[1] / "desktop/fleet-core/tests/fixtures/ssh-config").read_bytes()
    ssh.write_bytes(ssh_bytes)
    probes = []

    async def restricted_ssh(alias):
        assert alias == "node-1-direct" and b"Host node-1-direct\n" in ssh_bytes
        assert ssh.read_bytes() == ssh_bytes
        probes.append(alias)

    def no_second_daemon(*args, **kwargs):
        pytest.fail("Reading or selecting an existing connection must not spawn a daemon")

    monkeypatch.setattr(setup, "_probe_ssh", restricted_ssh)
    monkeypatch.setattr(managed_runtime, "_spawn", no_second_daemon)
    path = root / "config/hosts.toml"
    document = tomllib.loads(path.read_text())
    document["bat"] = {"profiles_dir": str(profiles)}
    document["usage"] = {"interval_s": 90, "sources": []}
    document["operator_extension"]["future_usage"] = {"preserve": ["opaque", "operator-owned"]}
    platform_files.atomic_write(path, setup._encode(document))
    unrelated = root / "config/other-client.toml"
    platform_files.atomic_write(unrelated, setup._encode({"unrelated_extension": {"keep": "original"}}))

    def files(paths):
        return {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in paths}

    external = files([profiles / "index.json", store, ssh, unrelated])
    manual = json.dumps([mock.ws_doc, mock.metas, mock.states, mock.archives], sort_keys=True)
    bearer = token(daemon, principal.actor, *principal.scopes)
    db = daemon.journal.db
    principals = [tuple(row) for row in db.execute("SELECT * FROM api_principals")]
    assert len(principals) == 1
    identity = dashboard_sync.identity(daemon.journal, principal)
    lease = daemon._lease_fd
    owner = [tuple(row) for row in db.execute("SELECT * FROM daemon_owner")]
    owner_pointer = registry.registry_path().parent / "task-service.json"
    pointer_before = owner_pointer.read_bytes()
    params = {"import_profile_id": "profile-1", "ssh_alias": "node-1-direct"}
    first = await settle(installed, create(installed, "setup.host", {"host": "fixture"}, params))
    assert first["status"] == "succeeded", first
    assert first["result"]["host"] == "fixture" and probes == ["node-1-direct"]
    assert files(external) == external
    await daemon.inventory._refresh_scheduled("fixture")
    assert daemon.inventory.hosts()[0]["reachable"] is True
    source = [tuple(row) for row in db.execute("SELECT host,profile_id,binding FROM discovery_latest")]
    assert len(source) == 1 and source[0][:2] == ("fixture", "default")
    saved_config = files([path])
    old_sessions = [tuple(row) for row in db.execute("SELECT * FROM sessions_observed ORDER BY session_id")]
    assert {row[1] for row in old_sessions} == {
        terminal["id"] for terminal in mock.ws_doc["terminals"] if terminal.get("agentPreset")}

    def reloaded_reference():
        config = load_config(path)
        host = config.host("fixture")
        raw = tomllib.loads(path.read_text())
        assert len(config.hosts) == 1 and host.resolve_token() == mock.token
        assert raw["operator_extension"] == document["operator_extension"]
        assert raw["usage"] == document["usage"]
        aliases = setup._aliases(raw)
        assert aliases == daemon.adapter.verifier.settings.ssh_hosts == {"fixture": "node-1-direct"}
        return (host.url, host.fingerprint, host.profile_id, host.token_ref,
                aliases["fixture"])

    reference = (mock.url.rstrip("/") + "/", mock.fingerprint, "default", "bat-profile:profile-1", "node-1-direct")
    assert reloaded_reference() == reference
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    assert port != 18796
    responses = []

    async def get(route):
        status, response = await http(port, "GET", "/api/v1" + route, tok=bearer)
        assert status == 200, response
        responses.append(response)
        return response

    async def read_existing():
        state = await get("/managed/setup")
        assert len(state["hosts"]) == len(state["profiles"]) == 1
        host = state["hosts"][0]
        assert (host["name"], host["url"], host["fingerprint"], host["profile_id"], host["ssh_alias"]) == (
            "fixture", reference[0], reference[1], reference[2], reference[4])
        assert host["token_available"] is True
        bootstrap = await get("/bootstrap")
        assert {key: bootstrap["sync"][key] for key in identity} == identity
        hosts = (await get("/hosts"))["hosts"]
        assert hosts == bootstrap["snapshot"]["hosts"]["hosts"] and len(hosts) == 1
        assert hosts[0]["host"] == "fixture"
        scopes = (await get("/hosts?host=fixture&discovery=true"))["scopes"]
        assert [(s["host"], s["profile_id"], s["binding_version"]) for s in scopes] == source
        assert hosts[0]["discovery"] == scopes
        workspaces = await get("/workspaces?host=fixture")
        assert workspaces["errors"] == {}
        assert [{"workspace_id": w["workspace_id"], "name": w["name"]} for w in workspaces["workspaces"]] == first["result"]["workspaces"]
        assert all(w["host"] == "fixture" for w in workspaces["workspaces"])
        sessions = (await get("/sessions?host=fixture&order=id"))["sessions"]
        assert {s["session_id"] for s in sessions} == {row[1] for row in old_sessions}
        assert {s["resource_id"] for s in sessions} == {"fixture/" + row[1] for row in old_sessions}
        histories = {}
        for session in sessions:
            sid = session["session_id"]
            history = await get("/sessions/fixture/" + sid + "/history")
            assert history["events"] and history["has_more"] is False
            histories[sid] = history["events"]
        return state, histories

    try:
        before, histories = await read_existing()
        client = daemon.fleet.client("fixture")
        status, accepted = await http(port, "POST", "/api/v1/operations?wait=0", tok=bearer,
            body={"action": "setup.host", "target": {"host": "fixture"}, "params": params,
                  "preconditions": {"config_revision": before["revision"]}},
            headers={"Idempotency-Key": "select-existing-connection-again"})
        assert status == 202 and accepted["created"] is True, accepted
        second = await settle(installed, accepted["operation"])
        assert second["status"] == "succeeded", second
        assert second["operation_id"] != first["operation_id"]
        assert second["result"]["revision"] == first["result"]["revision"]
        assert (await read_existing())[1] == histories
        assert setup.state(daemon, principal)["revision"] == before["revision"]
        assert reloaded_reference() == reference
        assert daemon.fleet.client("fixture") is client and client.connected
        assert set(probes) == {"node-1-direct"}  # Read-only re-verification may repeat the same alias.
        assert files(external) == external
        assert [tuple(row) for row in db.execute("SELECT host,profile_id,binding FROM discovery_latest")] == source
        assert db.execute("SELECT COUNT(*) FROM hosts_observed").fetchone()[0] == 1
        assert [tuple(row) for row in db.execute("SELECT * FROM api_principals")] == principals
        assert db.execute("SELECT COUNT(*) FROM api_sync_metadata").fetchone()[0] == 1
        assert [tuple(row) for row in db.execute("SELECT * FROM sessions_observed ORDER BY session_id")] == old_sessions
        assert json.dumps([mock.ws_doc, mock.metas, mock.states, mock.archives], sort_keys=True) == manual
        assert not list((root / "config/setup-secrets").glob("*"))
        assert [p for p in (root / "config").rglob("*") if p.is_file() and mock.token.encode() in p.read_bytes()] == []
        assert daemon._lease_fd == lease and daemon._owns_fleet()
        assert [tuple(row) for row in db.execute("SELECT * FROM daemon_owner")] == owner
        assert owner_pointer.read_bytes() == pointer_before
        public = json.dumps(responses)
        assert all(value not in public for value in (mock.token, bearer, reference[3], str(profiles)))
        assert '"token_ref"' not in public and '"bat_profiles_dir"' not in public
        assert not [frame for frame in mock.invokes if frame["channel"] in WRITE_CHANNELS | ORCHESTRATE_CHANNELS | GUARDED_CHANNELS]
        # This same file also owns unrelated client/safety/usage/operator settings.
        assert path.read_bytes() == saved_config[path][0]
        assert path.stat().st_ino == saved_config[path][2]
        assert path.stat().st_mtime_ns == saved_config[path][1]
    finally:
        server.close()
        await server.wait_closed()


async def test_revision_conflict_rejected_before_journaling_or_network(installed, mock):
    daemon, principal, _ = installed
    params = host_params(daemon, principal, mock)
    with pytest.raises(OperationError) as error:
        create(installed, "setup.host", {"host": "fixture"}, params, revision="0" * 64)
    assert error.value.code == "CONFIGURATION_CHANGED"
    assert daemon.journal.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
    assert not mock.frames


@pytest.mark.parametrize("change", [{"token_ref": "file:/unrelated/private"}, {"secret_ref": "../outside"},
    {"writes": "yes"}, {"url": "wss://user:credential@fixture:9876"}, {"managed_roots": ["/"]},
    {"ssh_alias": "-oProxyCommand=untrusted"}, {"shared_clone_worktrees": "true"}])
async def test_setup_rejects_unsafe_or_untyped_fields(installed, mock, change):
    daemon, principal, _ = installed
    params = host_params(daemon, principal, mock, **change)
    with pytest.raises(OperationError):
        create(installed, "setup.host", {"host": "fixture"}, params)
    assert not mock.frames


async def test_secret_kind_identity_expiry_and_traversal_are_enforced(installed, mock):
    daemon, principal, root = installed
    stranger = api_auth.Principal("other-person", principal.scopes)
    with pytest.raises(OperationError):
        setup.stage_secret(daemon, stranger, {"kind": "bat", "value": mock.token})
    with pytest.raises(OperationError):
        setup.stage_secret(daemon, principal, {"kind": ["bat"], "value": mock.token})
    ref = setup.stage_secret(daemon, principal, {"kind": "github", "value": mock.token})["secret_ref"]
    params = host_params(daemon, principal, mock, secret_ref=ref)
    with pytest.raises(OperationError):
        create(installed, "setup.host", {"host": "fixture"}, params)
    good = host_params(daemon, principal, mock)
    metadata_path = root / "config" / "setup-secrets" / (good["secret_ref"] + ".json")
    metadata = json.loads(metadata_path.read_text())
    metadata["expires_at"] = time.time() - 1
    platform_files.atomic_write(metadata_path, json.dumps(metadata).encode())
    with pytest.raises(OperationError):
        create(installed, "setup.host", {"host": "fixture"}, good)
    setup.stage_secret(daemon, principal, {"kind": "bat", "value": mock.token})
    assert not metadata_path.exists()  # unreferenced expired staging is cleaned on the next explicit stage


async def test_host_identity_is_not_rebound_and_other_work_blocks_setup(installed, mock):
    daemon, principal, _ = installed
    params = host_params(daemon, principal, mock)
    assert (await settle(installed, create(installed, "setup.host", {"host": "fixture"}, params)))["status"] == "succeeded"
    with pytest.raises(OperationError) as error:
        create(installed, "setup.host", {"host": "fixture"}, {**params, "url": "wss://127.0.0.1:65431/"}, key="rebind")
    assert error.value.code == "HOST_IDENTITY_CHANGED"
    daemon.ops.create(principal, action="project.create", params={"name": "Another operation"}, idempotency_key="project")
    with pytest.raises(OperationError) as error:
        create(installed, "setup.host", {"host": "second"}, params, key="busy")
    assert error.value.code == "SETUP_BUSY"


async def test_post_publish_receipt_loss_recovers_by_digest_without_repeating_probe(installed, mock, monkeypatch):
    daemon, principal, root = installed
    params = host_params(daemon, principal, mock)
    operation = create(installed, "setup.host", {"host": "fixture"}, params)
    original = daemon.ops._step_done
    interrupted = False
    def lose_receipt(ident, name, response, **kwargs):
        nonlocal interrupted
        if name == "managed_configuration" and not interrupted:
            interrupted = True
            raise OSError("fixture receipt lost after durable configuration publication")
        return original(ident, name, response, **kwargs)
    monkeypatch.setattr(daemon.ops, "_step_done", lose_receipt)
    await daemon.ops._execute(operation["operation_id"])
    calls = len(mock.auth_frames)
    assert "fixture" in tomllib.loads((root / "config" / "hosts.toml").read_text())["hosts"]
    monkeypatch.setattr(daemon.ops, "_step_done", original)
    await daemon.ops._execute(operation["operation_id"])
    assert daemon.ops.get(operation["operation_id"])["status"] == "succeeded"
    assert len(mock.auth_frames) == calls


@pytest.mark.parametrize("change", ["configuration", "operation"])
async def test_probe_cannot_overwrite_concurrent_configuration_or_active_work(installed, mock, monkeypatch, change):
    daemon, principal, root = installed
    path = root / "config" / "hosts.toml"
    expected = path.read_bytes()
    original_probe = setup._probe_host
    async def probe(config, host):
        nonlocal expected
        result = await original_probe(config, host)
        if change == "configuration":
            document = tomllib.loads(path.read_text())
            document["operator_extension"]["added_during_verification"] = True
            expected = setup._encode(document)
            platform_files.atomic_write(path, expected)
        else:
            daemon.ops.create(principal, action="project.create", params={"name": "Concurrent work"}, idempotency_key="concurrent")
        return result
    monkeypatch.setattr(setup, "_probe_host", probe)
    operation = create(installed, "setup.host", {"host": "fixture"}, host_params(daemon, principal, mock))
    await daemon.ops._execute(operation["operation_id"])
    assert daemon.ops.get(operation["operation_id"])["error_code"] == "CONFIGURATION_CHANGED"
    assert path.read_bytes() == expected
    assert not daemon.fleet.config.hosts


async def test_accepted_secret_expiry_does_not_change_durable_original_intent(installed, mock):
    daemon, principal, root = installed
    params = host_params(daemon, principal, mock)
    operation = create(installed, "setup.host", {"host": "fixture"}, params)
    metadata_path = root / "config" / "setup-secrets" / (params["secret_ref"] + ".json")
    metadata = json.loads(metadata_path.read_text())
    metadata["expires_at"] = time.time() - 1
    platform_files.atomic_write(metadata_path, json.dumps(metadata).encode())
    setup.stage_secret(daemon, principal, {"kind": "bat", "value": mock.token})
    assert metadata_path.exists()  # pending original intent keeps its already admitted credential
    result = await settle(installed, operation)
    assert result["status"] == "succeeded", result


async def test_explicit_verification_commands_persist_without_execution_and_restore(installed, tmp_path):
    daemon, principal, root = installed
    marker = tmp_path / "must-not-run-during-save"
    params = {"commands": {"Reviewed task project": ["python3", "-c", f"open({str(marker)!r}, 'w').write('unexpected')"]},
              "timeout_s": 120}
    operation = create(installed, "setup.verification", {}, params)
    result = await settle(installed, operation)
    assert result["status"] == "succeeded", result
    assert not marker.exists()
    assert setup.state(daemon, principal)["verification"] == params
    assert daemon.adapter.verifier.settings.commands["Reviewed task project"] == tuple(params["commands"]["Reviewed task project"])
    assert daemon.adapter.verifier.settings.timeout_s == 120
    # A second service instance loading the persisted document restores commands
    # through the same installation hook, without BATC_TASK_SETTINGS or execution.
    await daemon.ops.drain()
    await daemon.fleet.close()
    await daemon.inventory.close()
    daemon.journal.close()
    restored = TaskDaemon(daemon.fleet.config, root / "state" / "tasks.sqlite3")
    restored.managed_installation = daemon.managed_installation
    try:
        setup.install(restored)
        assert restored.adapter.verifier.settings.commands == daemon.adapter.verifier.settings.commands
        assert restored.adapter.verifier.settings.timeout_s == 120
        assert not marker.exists()
        # An explicitly reviewed empty replacement removes prior mappings.
        replacement = restored, principal, root
        cleared = create(replacement, "setup.verification", {}, {"commands": {}, "timeout_s": 30}, key="clear-verification")
        assert (await settle(replacement, cleared))["status"] == "succeeded"
        assert not restored.adapter.verifier.settings.commands
    finally:
        await restored.ops.drain()
        await restored.fleet.close()
        await restored.inventory.close()
        restored.journal.close()


@pytest.mark.parametrize("params", [{"commands": {"project": "python -m pytest"}, "timeout_s": 60},
    {"commands": {"project": []}, "timeout_s": 60}, {"commands": {}, "timeout_s": True},
    {"commands": {}, "timeout_s": 3601}, {"commands": {"project": ["python\nother"]}, "timeout_s": 60}])
async def test_verification_settings_refuse_ambiguous_or_unbounded_commands(installed, params):
    with pytest.raises(OperationError):
        create(installed, "setup.verification", {}, params)


async def test_repository_binding_checks_provider_and_exact_workspace(installed, mock, monkeypatch):
    daemon, principal, root = installed
    assert (await settle(installed, create(installed, "setup.host", {"host": "fixture"}, host_params(daemon, principal, mock))))["status"] == "succeeded"
    calls = []
    async def repository(self, name):
        calls.append((self.cfg.api_url, name))
        return 200, {"full_name": "fixture/repository"}
    monkeypatch.setattr(setup.GitHubClient, "repository", repository)
    ref = setup.stage_secret(daemon, principal, {"kind": "github", "value": "synthetic-github-fixture-token"})["secret_ref"]
    params = {"host": "fixture", "workspace_id": "ws-1", "secret_ref": ref,
              "remote_url": "git@github.com:fixture/repository.git", "allow_merge": True, "allow_integrate": True}
    result = await settle(installed, create(installed, "setup.repository", {"repository": "fixture/repository"}, params, key="repo"))
    assert result["status"] == "succeeded", result
    repo = daemon.ops.context["github_config"].repos["fixture/repository"]
    assert repo.sync.bindings == (("fixture", "ws-1"),)
    assert repo.integrate.hosts == ("fixture",)
    assert calls == [("https://api.github.com", "fixture/repository")]
    saved = (root / "config" / "hosts.toml").read_bytes()
    bad = {**params, "workspace_id": "unknown-workspace"}
    with pytest.raises(OperationError) as error:
        create(installed, "setup.repository", {"repository": "fixture/repository"}, bad, key="rebind-integration")
    assert error.value.code == "REPOSITORY_BINDING_CHANGED"
    bad.pop("allow_integrate")
    result = await settle(installed, create(installed, "setup.repository", {"repository": "fixture/repository"}, bad, key="bad-workspace"))
    assert result["error_code"] == "WORKSPACE_NOT_FOUND"
    assert (root / "config" / "hosts.toml").read_bytes() == saved
    assert len(calls) == 1


async def test_profile_import_respects_encryption_and_never_returns_credentials(installed, mock, tmp_path, monkeypatch):
    daemon, principal, _ = installed
    directory = tmp_path / "bat-profiles"
    directory.mkdir()
    from urllib.parse import urlsplit
    endpoint = urlsplit(mock.url)
    (directory / "index.json").write_text(json.dumps({"profiles": [{"id": "remote-1", "name": "Fixture BAT",
        "type": "remote", "remoteHost": endpoint.hostname, "remotePort": endpoint.port,
        "remoteFingerprint": mock.fingerprint}]}))
    (directory / "remote-tokens.enc.json").write_text(json.dumps({"enc": True, "data": "opaque-encrypted"}))
    (directory / "remote-tokens.enc.json").chmod(0o600)
    monkeypatch.setattr(setup, "_profile_directory", lambda _: directory)
    state = setup.state(daemon, principal)
    assert state["profiles"][0]["token_available"] is False
    assert state["profiles"][0]["token_state"] == "encrypted"
    with pytest.raises(OperationError):
        create(installed, "setup.host", {"host": "fixture"}, {"import_profile_id": "remote-1"})
    (directory / "remote-tokens.enc.json").write_text(json.dumps({"enc": False, "data": json.dumps({"tokens": {"remote-1": mock.token}})}))
    state = setup.state(daemon, principal)
    assert state["profiles"][0]["token_available"]
    assert mock.token not in json.dumps(state)
    result = await settle(installed, create(installed, "setup.host", {"host": "fixture"}, {"import_profile_id": "remote-1"}))
    assert result["status"] == "succeeded"
    assert daemon.fleet.config.host("fixture").token_ref == "bat-profile:remote-1"

    # A damaged external store must leave setup usable for credential replacement.
    (directory / "remote-tokens.enc.json").write_text('["damaged"]')
    assert setup.state(daemon, principal)["hosts"][0]["token_available"] is False


async def test_verification_http_admission_refusal_preserves_configuration_without_operation(installed):
    daemon, principal, _ = installed
    before = setup.state(daemon, principal)
    status, response = await daemon.api.create_operation(principal=principal, query={},
        body={"action": "setup.verification", "target": {}, "params": {"commands": {"project": []}, "timeout_s": 120},
              "preconditions": {"config_revision": before["revision"]}}, headers={"idempotency-key": "invalid-verification"})
    assert status == 422 and response["error"]["admission_refused"] is True
    assert response["error"]["code"] == "INVALID_PARAMS"
    assert not daemon.ops.list(action="setup.verification")["operations"]
    assert setup.state(daemon, principal)["revision"] == before["revision"]


async def test_profile_discovery_skips_damaged_fingerprints(installed, tmp_path, monkeypatch):
    daemon, principal, _ = installed
    directory = tmp_path / "bat-profiles"
    directory.mkdir()
    profiles = [{"id": "broken", "name": "Broken", "type": "remote", "remoteHost": "127.0.0.1",
                 "remotePort": 9876, "remoteFingerprint": None},
                {"id": "valid", "name": "Valid", "type": "remote", "remoteHost": "127.0.0.1",
                 "remotePort": 9876, "remoteFingerprint": "AA" * 32}]
    (directory / "index.json").write_text(json.dumps({"profiles": profiles}))
    (directory / "remote-tokens.enc.json").write_text(json.dumps({"enc": False, "data": ["damaged"]}))
    monkeypatch.setattr(setup, "_profile_directory", lambda _: directory)
    state = setup.state(daemon, principal)
    assert [profile["id"] for profile in state["profiles"]] == ["valid"]
    assert state["profiles"][0]["token_available"] is False
    assert state["profiles"][0]["token_state"] == "unavailable"



async def test_profile_token_state_requires_private_token_store(installed, mock, tmp_path, monkeypatch):
    daemon, principal, _ = installed
    directory = tmp_path / "bat-profiles"
    directory.mkdir()
    from urllib.parse import urlsplit
    endpoint = urlsplit(mock.url)
    (directory / "index.json").write_text(json.dumps({"profiles": [{"id": "remote-1", "name": "Fixture BAT",
        "type": "remote", "remoteHost": endpoint.hostname, "remotePort": endpoint.port,
        "remoteFingerprint": mock.fingerprint}]}))
    store = directory / "remote-tokens.enc.json"
    store.write_text(json.dumps({"enc": False, "data": json.dumps({"tokens": {"remote-1": mock.token}})}))
    store.chmod(0o644)
    monkeypatch.setattr(setup, "_profile_directory", lambda _: directory)
    state = setup.state(daemon, principal)
    assert state["profiles"][0]["token_available"] is False
    assert state["profiles"][0]["token_state"] == "unavailable"
    assert mock.token not in json.dumps(state)
    store.chmod(0o600)
    assert setup.state(daemon, principal)["profiles"][0]["token_available"] is True


@pytest.mark.parametrize("git", [None, True, False, 1, [], {}, "auto", "SSH"])
async def test_setup_git_requires_strict_enum_before_journal_or_probe(installed, mock, git):
    daemon, principal, root = installed
    before = (root / "config" / "hosts.toml").read_bytes()
    with pytest.raises(OperationError) as error:
        create(installed, "setup.host", {"host": "fixture"}, host_params(daemon, principal, mock, git=git))
    assert error.value.code == "HOST_GIT_INVALID"
    assert daemon.ops.list()["operations"] == []
    assert not mock.auth_frames
    assert (root / "config" / "hosts.toml").read_bytes() == before


@pytest.mark.parametrize("confirmation", [None, False, 0, 1, "true", [], {}])
async def test_setup_local_requires_exact_confirmation(installed, mock, confirmation):
    daemon, principal, root = installed
    before = (root / "config" / "hosts.toml").read_bytes()
    params = host_params(daemon, principal, mock, git="local")
    if confirmation is not None:
        params["local_confirmation"] = confirmation
    with pytest.raises(OperationError) as error:
        create(installed, "setup.host", {"host": "fixture"}, params)
    assert error.value.code == "LOCAL_GIT_CONFIRMATION_REQUIRED"
    assert daemon.ops.list()["operations"] == []
    assert not mock.auth_frames
    assert (root / "config" / "hosts.toml").read_bytes() == before


async def test_local_setup_preserves_aliases_uses_one_composite_and_replays_original_key(installed, mock, monkeypatch):
    from bat_agent_connector.host_git import HostGitRunner
    daemon, principal, root = installed
    probes = []
    async def ssh(alias):
        probes.append(alias)
        raise AssertionError("local Git must not probe SSH")
    monkeypatch.setattr(setup, "_probe_ssh", ssh)
    startup = daemon.ops.context["git_runner"]
    assert isinstance(startup, HostGitRunner)
    assert startup is daemon.fleet.confinement_runner is daemon.inventory.fleet.confinement_runner
    params = host_params(daemon, principal, mock, git="local", local_confirmation=True, ssh_alias="fixture-bat")
    before = setup.state(daemon, principal)["revision"]
    operation = create(installed, "setup.host", {"host": "fixture"}, params, revision=before)
    binding = operation["external_refs"]["admission_binding"]
    assert binding["git_execution"]["fixture"]["transport"] == "local"
    assert binding["git_execution"]["fixture"]["server_id"] == daemon.managed_installation["server_id"]
    assert binding["local_confirmation"] is True
    result = await settle(installed, operation)
    assert result["status"] == "succeeded", result
    runner = daemon.ops.context["git_runner"]
    assert isinstance(runner, HostGitRunner)
    assert runner is daemon.fleet.confinement_runner is daemon.inventory.fleet.confinement_runner
    assert not runner.available("fixture")
    assert probes == []
    assert daemon.adapter.verifier.settings.ssh_hosts == {"fixture": "fixture-bat"}
    assert daemon.ops.context["artifact_host"].available("fixture")
    assert tomllib.loads((root / "config" / "hosts.toml").read_text())["managed_setup"]["ssh_hosts"] == {"fixture": "fixture-bat"}
    state = setup.state(daemon, principal)
    assert state["hosts"][0]["git"] == "local"
    assert state["hosts"][0]["git_status"]["reason"] == "local_git_not_enabled"
    assert state["hosts"][0]["connected"] is None  # no inventory result yet; a closed socket is not failure
    calls = len(mock.auth_frames)
    assert create(installed, "setup.host", {"host": "fixture"}, params, revision=before)["operation_id"] == operation["operation_id"]
    with pytest.raises(OperationError) as error:
        create(installed, "setup.host", {"host": "fixture"}, {**params, "git": "ssh"}, revision=before)
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert len(mock.auth_frames) == calls
    assert not [frame for frame in mock.invokes if frame["channel"] in WRITE_CHANNELS | ORCHESTRATE_CHANNELS | GUARDED_CHANNELS]


async def test_setup_local_omission_preserves_mode_and_switch_requires_new_id(installed, mock):
    daemon, principal, _ = installed
    params = host_params(daemon, principal, mock, git="local", local_confirmation=True)
    first = await settle(installed, create(installed, "setup.host", {"host": "fixture"}, params))
    assert first["status"] == "succeeded"
    omitted = {key: value for key, value in params.items() if key not in {"git", "local_confirmation"}}
    second = await settle(installed, create(installed, "setup.host", {"host": "fixture"}, omitted, key="same-local"))
    assert second["status"] == "succeeded" and second["result"]["revision"] == first["result"]["revision"]
    assert daemon.fleet.config.host("fixture").git == "local"
    with pytest.raises(OperationError) as error:
        create(installed, "setup.host", {"host": "fixture"}, {**omitted, "git": "ssh"}, key="switch")
    assert error.value.code == "HOST_IDENTITY_CHANGED"
    third = await settle(installed, create(installed, "setup.host", {"host": "new-ssh"}, omitted, key="new-host"))
    assert third["status"] == "succeeded" and daemon.fleet.config.host("new-ssh").git == "ssh"
    with pytest.raises(OperationError) as error:
        create(installed, "setup.host", {"host": "new-ssh"}, params, key="reverse-switch")
    assert error.value.code == "HOST_IDENTITY_CHANGED"


async def test_local_post_publish_receipt_loss_recovers_after_restart_with_original_key(installed, mock, monkeypatch):
    from bat_agent_connector.host_git import HostGitRunner
    daemon, principal, root = installed
    params = host_params(daemon, principal, mock, git="local", local_confirmation=True, ssh_alias="fixture-bat")
    before = setup.state(daemon, principal)["revision"]
    operation = create(installed, "setup.host", {"host": "fixture"}, params, revision=before)
    original = daemon.ops._step_done
    def lose_receipt(ident, name, response, **kwargs):
        if name == "managed_configuration":
            raise OSError("fixture lost publication receipt")
        return original(ident, name, response, **kwargs)
    monkeypatch.setattr(daemon.ops, "_step_done", lose_receipt)
    await daemon.ops._execute(operation["operation_id"])
    assert daemon.ops.get(operation["operation_id"])["status"] == "uncertain"
    assert tomllib.loads((root / "config" / "hosts.toml").read_text())["hosts"]["fixture"]["git"] == "local"
    calls = len(mock.auth_frames)
    await daemon.ops.drain()
    await daemon.fleet.close()
    await daemon.inventory.close()
    daemon.journal.close()
    path = root / "config" / "hosts.toml"
    restarted = TaskDaemon(parse_config(tomllib.loads(path.read_text()), path), root / "state" / "tasks.sqlite3")
    restarted.managed_installation = dict(daemon.managed_installation)
    setup.install(restarted)
    try:
        replay = create((restarted, principal, root), "setup.host", {"host": "fixture"}, params, revision=before)
        assert replay["operation_id"] == operation["operation_id"]
        await restarted.ops._execute(replay["operation_id"])
        result = restarted.ops.get(replay["operation_id"])
        assert result["status"] == "succeeded" and result["idempotency_key"] == "fixture-setup"
        assert len(mock.auth_frames) == calls
        runner = restarted.ops.context["git_runner"]
        assert isinstance(runner, HostGitRunner)
        assert runner is restarted.fleet.confinement_runner is restarted.inventory.fleet.confinement_runner
        assert not runner.available("fixture")
    finally:
        await restarted.ops.drain()
        await restarted.fleet.close()
        await restarted.inventory.close()
        restarted.journal.close()


@pytest.mark.parametrize("phase", ["probe", "receipt-recovery"])
async def test_setup_local_confirmation_is_bound_to_original_installation(installed, mock, monkeypatch, phase):
    daemon, principal, root = installed
    daemon.managed_installation["installation_id"] = "fixture-installation"
    params = host_params(daemon, principal, mock, git="local", local_confirmation=True)
    original_config = (root / "config" / "hosts.toml").read_bytes()
    operation = create(installed, "setup.host", {"host": "fixture"}, params)
    assert operation["external_refs"]["admission_binding"]["git_execution"]["fixture"]["installation_id"] == "fixture-installation"
    if phase == "probe":
        original = setup._probe_host
        async def probe(config, host):
            result = await original(config, host)
            daemon.managed_installation["installation_id"] = "other-installation"
            return result
        monkeypatch.setattr(setup, "_probe_host", probe)
    else:
        original = daemon.ops._step_done
        def lose_receipt(ident, name, response, **kwargs):
            if name == "managed_configuration":
                raise OSError("fixture lost publication receipt")
            return original(ident, name, response, **kwargs)
        monkeypatch.setattr(daemon.ops, "_step_done", lose_receipt)
        await daemon.ops._execute(operation["operation_id"])
        assert daemon.ops.get(operation["operation_id"])["status"] == "uncertain"
        monkeypatch.setattr(daemon.ops, "_step_done", original)
        daemon.managed_installation["installation_id"] = "other-installation"
    calls = len(mock.auth_frames)
    await daemon.ops._execute(operation["operation_id"])
    result = daemon.ops.get(operation["operation_id"])
    assert result["error_code"] == "SETUP_IDENTITY_CHANGED"
    assert result["status"] == "needs_attention"
    if phase == "probe":
        assert (root / "config" / "hosts.toml").read_bytes() == original_config
    else:
        assert len(mock.auth_frames) == calls


async def test_setup_uses_observed_connectivity_after_read_only_socket_closes(installed, mock):
    """Opening sessions succeeds even when neither pool retains an idle socket."""
    daemon, principal, _ = installed
    result = await settle(installed, create(installed, "setup.host", {"host": "fixture"},
                                           host_params(daemon, principal, mock)))
    assert result["status"] == "succeeded"
    await daemon.inventory._refresh_scheduled("fixture")
    assert daemon.inventory.hosts()[0]["reachable"] is True
    assert not daemon.fleet._clients
    assert not daemon.inventory.fleet.client("fixture").connected
    before = len(mock.auth_frames)
    host = setup.state(daemon, principal)["hosts"][0]
    assert host["connected"] is True
    assert host["connection_status"] == "connected"
    assert host["connection_checked_at"] is not None
    assert len(mock.auth_frames) == before  # Reading Setup does not launch another probe.


@pytest.fixture
async def setup_evidence(installed, mock, monkeypatch):
    """Real saved setup and inventory; only BAT's TLS/WebSocket peer is a double."""
    daemon, principal, root = installed
    profiles = root / "profiles"
    profiles.mkdir()
    (profiles / "index.json").write_text('{"profiles": []}')
    monkeypatch.setattr(setup, "_profile_directory", lambda _document: profiles)
    operation = await settle(installed, create(installed, "setup.host", {"host": "fixture"},
        host_params(daemon, principal, mock)))
    assert operation["status"] == "succeeded"
    await daemon.inventory._refresh_scheduled("fixture")
    connected = setup.state(daemon, principal)
    await mock.stop()
    await daemon.inventory._refresh_scheduled("fixture")
    unreachable = setup.state(daemon, principal)
    with daemon.journal.tx():
        daemon.journal.db.execute("UPDATE hosts_observed SET last_attempt_at=? WHERE host=?",
            (time.time() - daemon.inventory.settings.stale_after_s - 1, "fixture"))
    checking = setup.state(daemon, principal)
    # This is the same receipt in all three snapshots, not three successful setups.
    assert daemon.ops.get(operation["operation_id"]) == operation
    return {"operation": operation, "states": {"unreachable": unreachable, "checking": checking, "connected": connected}}


async def test_past_setup_success_does_not_establish_present_connectivity(setup_evidence):
    operation = setup_evidence["operation"]
    assert operation["result"]["configured"] and operation["result"]["connected"]
    assert operation["updated_at"] >= operation["created_at"]
    for condition, document in setup_evidence["states"].items():
        host = document["hosts"][0]
        assert host["name"] == operation["target"]["host"]
        assert host["connection_status"] == condition
        assert host["connected"] is (None if condition == "checking" else condition == "connected")
        assert isinstance(host["connection_checked_at"], float)
        assert (host["connection_checked_at"] < operation["updated_at"]) is (condition == "checking")
        assert document["revision"] == operation["result"]["revision"]


@pytest.mark.parametrize("condition,expected", [("never", "checking"), ("failed", "unreachable"),
    ("stale_success", "checking"), ("stale_failure", "checking"), ("changed_binding", "checking"),
    ("active_writer", "connected")])
async def test_setup_connectivity_requires_current_recent_evidence(installed, mock, condition, expected):
    daemon, principal, _ = installed
    result = await settle(installed, create(installed, "setup.host", {"host": "fixture"},
                                           host_params(daemon, principal, mock)))
    assert result["status"] == "succeeded"
    if condition != "never":
        await daemon.inventory._refresh_scheduled("fixture")
        if condition in {"failed", "stale_failure"}:
            daemon.inventory._record_failure("fixture", time.time(), ConnectionError("synthetic unreachable"))
        if condition.startswith("stale_"):
            with daemon.journal.tx():
                daemon.journal.db.execute("UPDATE hosts_observed SET last_attempt_at=? WHERE host=?",
                    (time.time() - daemon.inventory.settings.stale_after_s - 1, "fixture"))
        if condition == "changed_binding":
            with daemon.journal.tx():
                daemon.journal.db.execute("UPDATE discovery_latest SET binding=? WHERE host=?", ("old-binding", "fixture"))
        if condition == "active_writer":
            daemon.inventory._record_failure("fixture", time.time(), ConnectionError("older check failed"))
            await daemon.fleet.client("fixture").connect()
    host = setup.state(daemon, principal)["hosts"][0]
    assert host["connection_status"] == expected
    assert host["connected"] is (None if expected == "checking" else expected == "connected")
    assert "synthetic unreachable" not in json.dumps(host)


@pytest.fixture
async def connection_evidence(installed, mock, monkeypatch):
    """Producer scenarios: real TLS, authentication, protocol and read refusal."""
    daemon, principal, root = installed
    profiles = root / "profiles"
    profiles.mkdir()
    (profiles / "index.json").write_text('{"profiles": []}')
    monkeypatch.setattr(setup, "_profile_directory", lambda _: profiles)
    async def ssh(_alias):
        pass
    monkeypatch.setattr(setup, "_probe_ssh", ssh)
    operation = await settle(installed, create(installed, "setup.host", {"host": "fixture"},
        host_params(daemon, principal, mock, ssh_alias="fixture-bat")))
    assert operation["status"] == "succeeded"
    original = daemon.fleet.config.host("fixture")
    daemon.fleet.config.github.repos["acme/widget"] = GitHubRepo("acme/widget",
        sync=RepositorySyncConfig("https://github.com/acme/widget.git", (("fixture", "ws-1"),)))
    clock = {"now": 1800000600.0}
    for module in (setup, inventory, pm_work_profiles):
        monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: clock["now"],
            strftime=time.strftime, gmtime=time.gmtime, strptime=time.strptime))
    created, _ = daemon.ops.create(principal, action="pm.workspace.create", params={"name": "Connection evidence"},
        idempotency_key="connection-workspace")
    workspace = (await settle(installed, created))["result"]["workspace"]["workspace_id"]
    created, _ = daemon.ops.create(principal, action=pm_work_profiles.ACTION, target={"workspace_id": workspace},
        params={"document": {"version": 1, "binding": {"repository": "acme/widget", "host": "fixture", "workspace_id": "ws-1"},
        "agent": "claude"}}, idempotency_key="connection-work-profile")
    profile = (await settle(installed, created))["result"]["profile_id"]
    row = daemon.journal.db.execute("SELECT * FROM work_profiles WHERE profile_id=?", (profile,)).fetchone()
    cases = {}

    def capture(name):
        daemon.ops.context["git_runner"] = HostGitRunner(daemon.fleet.config, {"fixture": "fixture-bat"})
        before = (list(mock.frames), daemon.journal.db.total_changes, daemon.journal.api_head())
        state = setup.state(daemon, principal)
        work = pm_work_profiles._readiness_body(daemon.ops, principal, workspace, row)
        cases[name] = {"setup": state, "inventory": daemon.inventory.hosts(clock["now"])[0],
            "work": work["states"]["can_observe"]}
        assert before == (list(mock.frames), daemon.journal.db.total_changes, daemon.journal.api_head())

    capture("never_observed")
    await daemon.fleet.client("fixture").connect()
    capture("active_writer")
    await daemon.fleet.close()
    daemon.fleet._clients.clear()
    await daemon.inventory._refresh_scheduled("fixture")
    capture("connected")
    clock["now"] += daemon.inventory.settings.stale_after_s - 1
    capture("before_expiry")
    clock["now"] += 1
    capture("expired")
    await daemon.inventory._refresh_scheduled("fixture")
    capture("renewed")
    clock["now"] += 1
    for name in ("authentication", "protocol", "tls_trust", "missing_credential", "service_refusal", "service_timeout", "unknown", "transport"):
        daemon.fleet.config.hosts["fixture"] = original
        await daemon.inventory.fleet.close()
        daemon.inventory.fleet._clients.clear()
        if name == "tls_trust":
            daemon.fleet.config.hosts["fixture"] = replace(original, fingerprint="00" * 32)
            # Observe this configured source, with no older discovery binding.
            with daemon.journal.tx():
                daemon.journal.db.execute("DELETE FROM discovery_latest WHERE host='fixture'")
        elif name == "missing_credential":
            daemon.fleet.config.hosts["fixture"] = replace(original, token_ref="env:BATC_FIXTURE_MISSING")
            monkeypatch.delenv("BATC_FIXTURE_MISSING", raising=False)
        elif name == "authentication":
            mock.token = "different-fixture-token"
        elif name == "protocol":
            mock.protocol = "bat-remote/unsupported"
        elif name in {"service_refusal", "unknown"}:
            def read(_):
                if name == "service_refusal":
                    raise RuntimeError("fixture read refused")
                return None
            mock.handlers["workspace:load"] = read
        elif name == "service_timeout":
            async def slow_read(_):
                await asyncio.sleep(0.1)
                return mock.ws_doc
            mock.handlers["workspace:load"] = slow_read
        elif name == "transport":
            await mock.stop()
        with monkeypatch.context() as deadline:
            if name == "service_timeout":
                deadline.setattr(client, "timeout_for", lambda _: 0.01)
            await daemon.inventory._refresh_scheduled("fixture")
        capture(name)
        mock.token = original.resolve_token()
        mock.protocol = "bat-remote/v2"
        mock.handlers.clear()
        if name != "transport":
            with daemon.journal.tx():
                daemon.journal.db.execute("DELETE FROM discovery_latest WHERE host='fixture'")
        clock["now"] += 1
    daemon.fleet.config.hosts["fixture"] = replace(original, profile_id="other-profile")
    capture("changed_binding")
    bearer = token(daemon, "other-setup-reader", "observe", "manage")
    before = list(mock.frames)
    server = await asyncio.start_server(daemon._handle, "127.0.0.1", 0)
    try:
        status, response = await http(server.sockets[0].getsockname()[1], "GET", "/api/v1/managed/setup", tok=bearer)
        assert status == 403 and response["error"]["code"] == "FORBIDDEN"
        cases["connected"]["read_refusal"] = {"status": status, "body": response}
        assert mock.frames == before
    finally:
        server.close()
        await server.wait_closed()
    assert daemon.ops.get(operation["operation_id"]) == operation
    return {"operation": operation, "cases": cases}


async def test_connection_envelope_is_inventory_evidence_and_preserves_receipt(connection_evidence):
    expected = {"authentication": "auth_or_protocol", "protocol": "auth_or_protocol", "tls_trust": "tls_trust",
        "missing_credential": "credential_missing", "service_refusal": "service_refused", "service_timeout": "service_timeout",
        "unknown": "unknown", "transport": "transport"}
    for name, case in connection_evidence["cases"].items():
        host = case["setup"]["hosts"][0]
        envelope = host["connection_observation"]
        assert envelope["version"] == 1
        assert set(envelope["facets"]) == {"transport", "service", "trust_sign_in", "connection"}
        for fact in envelope["facets"].values():
            assert set(fact) == {"value", "observed_at", "valid_until", "reason", "source"}
            assert fact["source"]["host"] == "fixture"
            assert len(fact["source"]["binding_version"]) == 64
        fact = envelope["facets"]["connection"]
        work = case["work"]
        assert (fact["value"], fact["reason"], fact["observed_at"], fact["valid_until"]) == (
            work["value"], work["reason"], work.get("observed_at"), work.get("valid_until"))
        if name != "changed_binding":
            observed = case["inventory"]
            assert (fact["reason"], fact["valid_until"]) == (observed["stale_reason"], observed["valid_until"])
            assert fact["observed_at"] == (datetime.fromisoformat(observed["last_attempt_at"].replace("Z", "+00:00")).timestamp()
                if observed["last_attempt_at"] is not None else None)
            assert fact["value"] == ("unknown" if observed["reachable"] is None else "unavailable" if observed["stale"] else "ready")
        if name in expected:
            assert case["inventory"]["failure_reason"] == expected[name]
            assert expected[name] in {f["reason"] for f in envelope["facets"].values()}
        if name == "active_writer":
            assert host["connected"] is True and host["connection_checked_at"] is None
            assert fact["value"] == "unknown"
            assert envelope["facets"]["transport"]["reason"] == "authenticated_socket"
            assert envelope["facets"]["transport"]["observed_at"] == 1800000600.0
            for axis in ("service", "trust_sign_in", "connection"):
                assert envelope["facets"][axis]["value"] == "unknown"
                assert envelope["facets"][axis]["observed_at"] is None
        if name == "expired":
            assert fact["value"] == "unavailable" and fact["reason"] == "host_not_refreshed"
            assert host["connected"] is True  # The legacy window remains inclusive.
        if name == "changed_binding":
            assert fact["reason"] == "DISCOVERY_SCOPE_CHANGED"


async def test_failure_reason_upgrade_is_idempotent_and_old_rows_stay_unknown(installed):
    daemon, _, _ = installed
    from bat_agent_connector.errors import ConnectionLost
    from bat_agent_connector.inventory import Inventory
    daemon.journal.db.execute("ALTER TABLE hosts_observed DROP COLUMN failure_reason")
    daemon.journal.db.execute("INSERT INTO hosts_observed(host,reachable,error) VALUES('old',0,'AuthError: not a type')")
    version = daemon.journal.db.execute("PRAGMA user_version").fetchone()[0]
    for _ in range(2):
        Inventory(daemon.journal, daemon.fleet.config)
    assert daemon.journal.db.execute("PRAGMA user_version").fetchone()[0] == version
    assert daemon.journal.db.execute("SELECT failure_reason FROM hosts_observed WHERE host='old'").fetchone()[0] is None
    assert inventory._failure_reason(ValueError("AuthError: text must not classify")) == "unknown"
    assert inventory._failure_reason(ConnectionLost("private details")) == "transport"
