"""Capabilities from real updater transitions; only release/install boundaries are fake."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver import MCPServer

from bat_agent_connector import api_auth, api_v1, mcp_identity, update_release, updater
from bat_agent_connector import build_identity as identity_module
from bat_agent_connector.config import Config
from bat_agent_connector.task_daemon import TaskDaemon
from bat_agent_connector.update_release import UpdateError
from scripts import install_connector as bootstrap
from tests.test_connector_installer import transport
from tests.test_connector_updates import (  # noqa: F401
    deployment,
    make_deployment,
    make_package,
    publication,
    release,
    update_tree,
)

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux updater producer")
GOLDEN = Path(__file__).parent / "fixtures" / "connector-update-facts.json"
AT = "2026-10-22T12:00:00.123456+00:00"


@pytest.fixture
def process(deployment, monkeypatch):  # noqa: F811
    u, receipt, _ = deployment
    monkeypatch.setenv("HOME", str(u.home))
    monkeypatch.setattr(updater, "timestamp", lambda: AT)
    build = {"version": "0.2.7", "commit": "a" * 40, "source": "vcs"}
    monkeypatch.setattr(api_v1, "build_identity", lambda: dict(build))
    monkeypatch.setattr(identity_module, "build_identity", lambda: dict(build))
    monkeypatch.setattr(updater, "_PROCESS_INSTALLATION", None)
    package = receipt.parent / "tools/bat-agent-connector/lib/python3.13/site-packages/bat_agent_connector"
    monkeypatch.setattr(updater, "__file__", str(package / "updater.py"))
    return u, receipt, build


async def read_capabilities(tmp_path):
    daemon = TaskDaemon(Config(hosts={}), tmp_path / "journal.db")
    try:
        status, caps = await api_v1.ApiV1(daemon).capabilities(
            api_auth.Principal("fixture-observer", frozenset({"observe"})))
        assert status == 200
        return {key: caps[key] for key in ("build", "connector_update")}
    finally:
        await daemon.fleet.close()
        await daemon.inventory.close()
        daemon.journal.close()


@pytest.mark.parametrize("scenario", [
    "not_enrolled", "same", "failed", "unreadable", "incompatible", "commit_mismatch", "outside_root", "unsupported",
])
async def test_capabilities_update_states_are_read_only(process, tmp_path, monkeypatch, scenario):
    u, receipt, build = process
    if scenario != "not_enrolled":
        u.enroll(receipt, "preview")
        if scenario == "failed":
            u.find_release = lambda channel, observation=None: release("b" * 40, "0.2.8")
            def fail(*args, **kwargs):
                raise UpdateError("fixture_install_failed")
            u.install = fail
            with pytest.raises(UpdateError):
                u.apply()
        elif scenario == "unreadable":
            (u.root / "state.json").chmod(0o644)
        elif scenario == "incompatible":
            monkeypatch.setattr(updater, "EPOCH", updater.EPOCH + 1)
        elif scenario == "commit_mismatch":
            build["commit"] = "c" * 40
        elif scenario == "outside_root":
            monkeypatch.setattr(updater, "__file__", str(tmp_path / "checkout/updater.py"))
        elif scenario == "unsupported":
            monkeypatch.setattr(updater, "sys", SimpleNamespace(platform="darwin"))
        monkeypatch.setattr(updater, "_PROCESS_INSTALLATION", updater._capture_process_installation())
        (u.root / "lease").unlink()
    before = update_tree(u.root)
    def forbidden(*args, **kwargs):
        pytest.fail("capabilities must not lock, reconcile, write, inspect packages or contact a release feed")
    for method in ("locked", "reconcile", "save", "outcome", "failure", "verify"):
        monkeypatch.setattr(updater.Updater, method, forbidden)
    original_init = updater.Updater.__init__
    def read_only_init(self):
        original_init(self, find_release=forbidden, install=forbidden, inspect=forbidden, command=forbidden)
    monkeypatch.setattr(updater.Updater, "__init__", read_only_init)
    reads = []
    read = updater.files.read_private
    def counted(path, **kwargs):
        reads.append((Path(path), kwargs.get("max_bytes", 1024 * 1024)))
        return read(path, **kwargs)
    monkeypatch.setattr(updater.files, "read_private", counted)
    result = await read_capabilities(tmp_path)
    block = result["connector_update"]
    expected = {"same": "reported", "failed": "reported", "incompatible": "unreadable",
                "commit_mismatch": "not_managed", "outside_root": "not_managed"}.get(scenario, scenario)
    assert block["state"] == expected
    assert block["activation"] == "next_launch"
    if expected == "reported":
        assert block["installed"] == {"version": "0.2.7", "commit": "a" * 40}
        assert block["channel"] == "preview" and block["enabled"] is True
        assert block["pending"] is False
        assert block["last"] == ({"status": "failed", "code": "fixture_install_failed", "at": AT}
                                 if scenario == "failed" else {"status": "enrolled_for_next_launch", "at": AT})
    elif expected == "unreadable":
        assert block["code"] == ("update_state_incompatible" if scenario == "incompatible" else "update_state_unreadable")
    assert update_tree(u.root) == before
    state_reads = [entry for entry in reads if entry[0] == u.root / "state.json"]
    assert len(state_reads) == (0 if scenario == "unsupported" else 1)
    assert all(size <= 1024 * 1024 for _, size in state_reads)
    assert [entry for entry in reads if entry[0].is_relative_to(u.root)] == state_reads
    for private in (str(u.home), "receipt", "proof", "previous", "https://", "root"):
        assert private not in json.dumps(block)
    golden = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if os.environ.get("BATC_UPDATE_CONNECTOR_GOLDEN") == "1":
        golden[scenario] = result
        GOLDEN.write_text(json.dumps(golden, indent=2) + "\n")
    assert result == golden[scenario]


async def test_running_process_reports_changed_installation_without_stale_state(process, tmp_path, monkeypatch):
    u, receipt, _ = process
    u.enroll(receipt, "preview")
    monkeypatch.setattr(updater, "_PROCESS_INSTALLATION", updater._capture_process_installation())
    first = await read_capabilities(tmp_path)
    assert first["connector_update"]["installed"]["commit"] == "a" * 40
    u.find_release = lambda channel, observation=None: release("b" * 40, "0.2.8")
    u.install = lambda root, candidate, **_: make_package(root, candidate)
    u.apply()
    before = update_tree(u.root)
    second = await read_capabilities(tmp_path)
    assert update_tree(u.root) == before
    assert second["build"] == first["build"]
    assert second["connector_update"]["installed"] == {"version": "0.2.8", "commit": "b" * 40}
    assert second["connector_update"]["last"] == {"status": "installed_for_next_launch", "at": AT}
    u.rollback()
    third = await read_capabilities(tmp_path)
    assert third["connector_update"]["installed"]["commit"] == "a" * 40
    assert third["connector_update"]["enabled"] is False
    # A changed last result/policy must also be re-read, even without a commit change.
    u.policy(True, "stable")
    assert (await read_capabilities(tmp_path))["connector_update"]["channel"] == "stable"
    generated = {"same": first, "differs": second, "rollback": third}
    if os.environ.get("BATC_UPDATE_CONNECTOR_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps({**json.loads(GOLDEN.read_text()), **generated}, indent=2) + "\n")
    golden = json.loads(GOLDEN.read_text())
    assert generated == {key: golden[key] for key in generated}


async def test_rollback_can_leave_an_older_installation_waiting_for_next_launch(process, tmp_path, monkeypatch):
    u, receipt, build = process
    u.enroll(receipt, "preview")
    u.find_release = lambda channel, observation=None: release("b" * 40, "0.2.8")
    u.install = lambda root, candidate, **_: make_package(root, candidate)
    u.apply()
    # A newly launched process belongs to the current installation, then stays
    # loaded while a subsequent rollback changes the next-launch pointer.
    build.update(version="0.2.8", commit="b" * 40)
    package = u.root / "versions" / ("b" * 40) / "tools/bat-agent-connector/lib/python3.13/site-packages/bat_agent_connector"
    monkeypatch.setattr(updater, "__file__", str(package / "updater.py"))
    monkeypatch.setattr(updater, "_PROCESS_INSTALLATION", updater._capture_process_installation())
    u.rollback()
    before = update_tree(u.root)
    result = await read_capabilities(tmp_path)
    assert result["connector_update"]["installed"] == {"version": "0.2.7", "commit": "a" * 40}
    assert result["build"]["commit"] == "b" * 40
    assert update_tree(u.root) == before
    golden = json.loads(GOLDEN.read_text())
    if os.environ.get("BATC_UPDATE_CONNECTOR_GOLDEN") == "1":
        golden["rollback_differs"] = result
        GOLDEN.write_text(json.dumps(golden, indent=2) + "\n")
    assert result == golden["rollback_differs"]


async def test_launch_evidence_does_not_attach_an_old_process_to_a_new_current(process, tmp_path, monkeypatch):
    u, receipt, _ = process
    u.enroll(receipt, "preview")
    u.find_release = lambda channel, observation=None: release("b" * 40, "0.2.8")
    u.install = lambda root, candidate, **_: make_package(root, candidate)
    u.apply()
    # First discovery after the switch cannot claim the old process was launched
    # from current. A matching retained version alone is insufficient.
    monkeypatch.setattr(updater, "_PROCESS_INSTALLATION", updater._capture_process_installation())
    assert (await read_capabilities(tmp_path))["connector_update"]["state"] == "not_managed"


def test_injected_finder_records_publication_clock_without_changing_install_candidate(process):
    u, receipt, _ = process
    u.enroll(receipt, "preview")
    candidate = release("b" * 40, "0.2.8")
    published_at = "2026-10-17T00:00:00Z"

    def find_release(channel, observation=None):
        assert channel == "preview"
        if observation is not None:
            observation["published_at"] = published_at
        return candidate

    u.find_release = find_release
    result = u.check()

    assert result["available"] is candidate
    assert "published_at" not in candidate
    assert result["release_check"]["completed_at"] == AT
    assert result["release_check"]["candidate"] == {**candidate, "published_at": published_at}
    assert u.read()["release_check"] == result["release_check"]


async def test_completed_release_check_retains_three_independent_builds_and_clocks(process, tmp_path, monkeypatch):
    u, receipt, build = process
    u.enroll(receipt, "preview")
    monkeypatch.setattr(updater, "_PROCESS_INSTALLATION", updater._capture_process_installation())
    # Real manifest validation, followed by the existing check and pointer switch.
    item, raw = publication(release("b" * 40, "0.2.8"))
    def download(url, **kwargs):
        return json.dumps([item]).encode() if url == update_release.API else raw
    monkeypatch.setattr(update_release, "fetch", download)
    u.find_release = updater.discover
    u.install = lambda root, candidate, **_: make_package(root, candidate)
    u.apply()
    completed = "2026-10-22T13:00:00.123456+00:00"
    monkeypatch.setattr(updater, "timestamp", lambda: completed)
    item, raw = publication(release("c" * 40, "0.2.9"))
    u.check()
    before = update_tree(u.root)
    caps = await read_capabilities(tmp_path)
    check = caps["connector_update"]["release_check"]
    assert check == {"completed_at": completed, "candidate": {
        "version": "0.2.9", "commit": "c" * 40, "published_at": "2026-10-17T00:00:00Z"}}
    assert caps["build"] == build
    assert caps["connector_update"]["installed_at"] == AT
    assert caps["connector_update"]["installed"] == {"version": "0.2.8", "commit": "b" * 40}
    monkeypatch.setattr(updater, "timestamp", lambda: "2026-10-22T14:00:00+00:00")
    assert (await read_capabilities(tmp_path)) == caps
    assert update_tree(u.root) == before
    def failed(channel, observation=None):
        raise UpdateError("release_network_unavailable")
    u.find_release = failed
    with pytest.raises(UpdateError):
        u.check()
    assert (await read_capabilities(tmp_path))["connector_update"]["release_check"] == check
    u.policy(True, "stable")
    assert (await read_capabilities(tmp_path))["connector_update"]["release_check"] is None
    u.find_release = lambda channel, observation=None: None
    u.check()
    assert (await read_capabilities(tmp_path))["connector_update"]["release_check"]["candidate"] is None
    golden = json.loads(GOLDEN.read_text())
    if os.environ.get("BATC_UPDATE_CONNECTOR_GOLDEN") == "1":
        golden["released"] = caps
        GOLDEN.write_text(json.dumps(golden, indent=2) + "\n")
    assert caps == golden["released"]


@pytest.mark.parametrize("scenario", ["same", "differs", "foreign", "unreadable", "not_enrolled", "not_managed"])
async def test_identified_mcp_compares_only_its_own_next_launch_installation(process, monkeypatch, scenario):
    u, receipt, build = process
    monkeypatch.setattr(mcp_identity, "build_identity", lambda: dict(build))
    if scenario != "not_enrolled":
        u.enroll(receipt, "preview")
        monkeypatch.setattr(updater, "_PROCESS_INSTALLATION", updater._capture_process_installation())
        if scenario == "differs":
            u.find_release = lambda channel, observation=None: release("b" * 40, "0.2.8")
            u.install = lambda root, candidate, **_: make_package(root, candidate)
            u.apply()
        elif scenario == "foreign":
            other, other_receipt, _ = make_deployment(u.home.parent / "other-installation")
            other.enroll(other_receipt, "preview")
            monkeypatch.setenv("HOME", str(other.home))
        elif scenario == "not_managed":
            monkeypatch.setattr(updater, "_PROCESS_INSTALLATION", None)
        elif scenario == "unreadable":
            (u.root / "current").unlink()
    before = update_tree(u.root)
    async def refused():
        raise PermissionError("private central endpoint")
    server = MCPServer("identified-process-fixture")
    mcp_identity.register(server, refused)
    contents = list(await server.read_resource("batc://identity"))
    result = json.loads(contents[0].content)
    assert result["mcp"]["build"] == build
    assert result["central"]["status"] == "unavailable"
    next_launch = result["next_launch"]
    expected = scenario if scenario in {"same", "differs"} else "unknown"
    assert next_launch["difference"] == expected
    if scenario == "foreign":
        assert next_launch["state"] == "not_managed"
    assert next_launch["restart_notice"] == ({"en": "Restart to load 0.2.8", "zh-TW": "重新啟動以載入 0.2.8"}
                                               if scenario == "differs" else None)
    assert next_launch["installed"] == ({"version": "0.2.8" if scenario == "differs" else "0.2.7",
                                           "commit": ("b" if scenario == "differs" else "a") * 40}
                                          if expected != "unknown" else None)
    assert update_tree(u.root) == before
    assert "private central" not in contents[0].content


async def test_older_private_state_does_not_invent_release_or_installation_clocks(process, tmp_path, monkeypatch):
    u, receipt, _ = process
    u.enroll(receipt, "preview")
    monkeypatch.setattr(updater, "_PROCESS_INSTALLATION", updater._capture_process_installation())
    state = u.read()
    state.pop("installed_at")
    assert "release_check" not in state
    u.save(state)
    before = update_tree(u.root)
    result = (await read_capabilities(tmp_path))["connector_update"]
    assert result["state"] == "reported"
    assert result["release_check"] is None and result["installed_at"] is None
    assert update_tree(u.root) == before


@pytest.mark.parametrize("published_at", [
    pytest.param("not-a-time", id="malformed"),
    pytest.param("2026-10-17T00:00:00", id="naive"),
    pytest.param("2026-10-17T00:00:00.00000000000000000000000000000000Z", id="too-long"),
    pytest.param(123, id="number"),
    pytest.param(["2026-10-17T00:00:00Z"], id="array"),
    pytest.param({"time": "2026-10-17T00:00:00Z"}, id="object"),
])
async def test_publication_display_clock_never_changes_release_acceptance_or_installation(
        process, tmp_path, monkeypatch, published_at):
    u, receipt, build = process
    u.enroll(receipt, "preview")
    monkeypatch.setattr(updater, "_PROCESS_INSTALLATION", updater._capture_process_installation())
    item, raw, download = transport(candidate=release("c" * 40, "0.2.9"))
    item["published_at"] = published_at
    accepted = bootstrap.resolve(item["tag_name"], "preview", download)
    assert update_release.manifest(item, raw) == accepted
    calls = []
    def release_download(url, **kwargs):
        calls.append(url)
        return json.dumps([item]).encode() if url == update_release.API else download(url)
    monkeypatch.setattr(update_release, "fetch", release_download)
    u.find_release = updater.discover
    u.install = lambda root, candidate, **_: make_package(root, candidate)
    checked = u.check()
    assert checked["available"] == accepted
    assert checked["installed"]["commit"] == build["commit"]
    assert u.read()["release_check"]["completed_at"] == AT
    assert u.read()["release_check"]["candidate"].get("published_at") is None
    installed = u.apply()
    assert installed["installed"] == accepted
    assert installed["installation_matches_state"] and not installed["pending"]
    assert installed["last"]["status"] == "installed_for_next_launch"
    assert u.read()["versions"][accepted["commit"]]["release"] == accepted
    # Two explicit checks, each using the existing list/manifest reads; no clock poll.
    assert len(calls) == 4
    before = update_tree(u.root)
    caps = await read_capabilities(tmp_path)
    assert update_tree(u.root) == before
    assert caps["build"] == build
    assert caps["connector_update"]["installed"] == {"version": "0.2.9", "commit": "c" * 40}
    assert caps["connector_update"]["state"] == "reported"
    assert caps["connector_update"]["release_check"] is None
    golden = json.loads(GOLDEN.read_text())
    if os.environ.get("BATC_UPDATE_CONNECTOR_GOLDEN") == "1":
        golden["publication_unknown"] = caps
        GOLDEN.write_text(json.dumps(golden, indent=2) + "\n")
    assert caps == golden["publication_unknown"]


@pytest.mark.parametrize("case", ["draft", "missing", "empty", "null", "false", "empty_array", "empty_object"])
def test_shared_publication_acceptance_still_refuses_unpublished_releases(case):
    item, raw, download = transport()
    if case == "draft":
        item["draft"] = True
    elif case == "missing":
        item.pop("published_at")
    else:
        item["published_at"] = {"empty": "", "null": None, "false": False,
                                "empty_array": [], "empty_object": {}}[case]
    with pytest.raises(bootstrap.InstallError, match="release_unpublished"):
        bootstrap.resolve(item["tag_name"], "preview", download)
    with pytest.raises(UpdateError, match="release_unpublished"):
        update_release.manifest(item, raw)


async def test_prior_release_record_shape_is_preserved_by_read_only_observation(process, tmp_path, monkeypatch):
    u, receipt, _ = process
    u.enroll(receipt, "preview")
    monkeypatch.setattr(updater, "_PROCESS_INSTALLATION", updater._capture_process_installation())
    item, raw, download = transport(candidate=release("b" * 40, "0.2.8"))
    monkeypatch.setattr(update_release, "fetch", lambda url, **kwargs: json.dumps([item]).encode()
                        if url == update_release.API else download(url))
    u.find_release = updater.discover
    u.install = lambda root, candidate, **_: make_package(root, candidate)
    u.apply()
    state = u.read()
    # Prior builds also retained the publication field in installation records.
    # Derive that legacy shape from this real completed check, without a new envelope.
    state["versions"][state["current"]]["release"] = state["release_check"]["candidate"]
    u.save(state)
    before = update_tree(u.root)
    caps = await read_capabilities(tmp_path)
    assert caps["connector_update"]["installed"] == {"version": "0.2.8", "commit": "b" * 40}
    assert caps["connector_update"]["release_check"] == {"completed_at": AT, "candidate": {
        "version": "0.2.8", "commit": "b" * 40, "published_at": item["published_at"]}}
    assert u.read() == state
    assert update_tree(u.root) == before
