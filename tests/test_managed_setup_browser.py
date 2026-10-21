"""Empty managed setup to an existing, read-only BAT session through the shared UI.

Run with BATC_WORK_LOOP_BROWSER=1 after installing desktop's Playwright Chromium.
No PM Work is seeded: the source remains the manual session supplied by MockBat.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from bat_agent_connector import __version__, api_auth, dashboard_sync
from bat_agent_connector.browser_sessions import BrowserSessions
from bat_agent_connector.channels import GUARDED_CHANNELS, ORCHESTRATE_CHANNELS, WRITE_CHANNELS
from bat_agent_connector.config import tomllib
from tests.test_api_v1 import http, token
from tests.test_api_v1_bootstrap_golden import GOLDEN, normalize_bootstrap
from tests.test_managed_setup import installed

_ = installed
ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.environ.get("BATC_WORK_LOOP_BROWSER") != "1",
    reason="Opt-in real managed Connector + Chromium; run by the desktop work-loop job")


@pytest.fixture
async def managed_browser(installed, tmp_path):
    daemon, fixture_principal, root = installed
    personal_token = token(daemon, fixture_principal.actor, *api_auth.SCOPES)
    principal = api_auth.authenticate(daemon.journal.db, personal_token, daemon._admin_token)
    identity = dashboard_sync.identity(daemon.journal, principal)
    daemon.managed_installation.update(installation_id="fixture-installation", runtime_version=__version__)
    assert all(daemon.managed_installation[key] == value for key, value in identity.items())
    daemon.api.browser_sessions = BrowserSessions(daemon, root, "fixture-installation")
    service = asyncio.create_task(daemon.serve(port=0))
    try:
        async def listening():
            while not daemon._endpoint:
                if service.done():
                    await service
                await asyncio.sleep(0.01)
        await asyncio.wait_for(listening(), 10)
        port = urlsplit(daemon._endpoint).port

        async def run(mode, locale, **facts):
            handoff = daemon.api.browser_sessions.create_handoff(principal)
            browser_temp = tmp_path / "browser"
            browser_temp.mkdir(exist_ok=True)
            shots = ROOT / "desktop/test-results/managed-setup-reading" / locale / mode.removeprefix("--") / facts.get("entry", "default")
            shots.mkdir(parents=True, exist_ok=True)
            with (shots / "browser.log").open("w") as errors:
                child = await asyncio.create_subprocess_exec("node", "tests/managed-setup-browser-integration.mjs",
                    mode, cwd=ROOT / "desktop", stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=errors,
                    env={**os.environ, "TMPDIR": str(browser_temp), "TEMP": str(browser_temp), "TMP": str(browser_temp)})
                try:
                    child.stdin.write((json.dumps({"origin": daemon._endpoint.removesuffix("/rpc"), "locale": locale,
                        "handoff": handoff["handoff_file"], "identity": identity, "shots": str(shots), **facts}) + "\n").encode())
                    await child.stdin.drain()
                    stdout, _ = await asyncio.wait_for(child.communicate(), 80)
                    assert child.returncode == 0, (shots / "browser.log").read_text()
                finally:
                    if child.returncode is None:
                        child.terminate()
                        await child.wait()
            report = json.loads(stdout)
            (shots / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
            return report

        yield {"daemon": daemon, "principal": principal, "token": personal_token, "identity": identity,
            "root": root, "port": port, "run": run}
    finally:
        service.cancel()
        with suppress(asyncio.CancelledError):
            await service


@pytest.mark.parametrize("locale", ["en", "zh-TW"])
async def test_empty_setup_opens_existing_read_only_session(managed_browser, mock, locale):
    daemon = managed_browser["daemon"]
    root, port = managed_browser["root"], managed_browser["port"]
    personal_token, identity = managed_browser["token"], managed_browser["identity"]
    assert not daemon.fleet.config.hosts
    assert not tomllib.loads((root / "config/hosts.toml").read_text())["hosts"]
    original_sessions = json.dumps(mock.states, sort_keys=True)
    # Same empty installed producer, using the golden's explicit read scopes.
    golden_token = token(daemon, "fixture-operator", "observe", "operate")
    status, bootstrap = await http(port, "GET", "/api/v1/bootstrap", tok=golden_token)
    assert status == 200
    normalized = normalize_bootstrap(bootstrap)
    # The plain golden daemon does not register the installed setup actions.
    # Check those additions, then compare every original bootstrap field.
    actions = normalized["capabilities"]["actions"]
    assert {row["action"] for row in actions if row["action"].startswith("setup.")} == {
        "setup.host", "setup.repository", "setup.verification"}
    normalized["capabilities"]["actions"] = [row for row in actions if not row["action"].startswith("setup.")]
    assert normalized == json.loads(GOLDEN.read_text())
    status, before = await http(port, "GET", "/api/v1/bootstrap", tok=personal_token)
    assert status == 200 and before["capabilities"]["identity"] == identity
    assert before["snapshot"]["hosts"]["hosts"] == []
    assert before["snapshot"]["sessions"]["sessions"] == []
    assert before["snapshot"]["operations"]["operations"] == []
    report = await managed_browser["run"]("--read-existing", locale,
        connection_url=f"{mock.url}?token={mock.token}&fp={mock.fingerprint}",
        title=mock.ws_doc["terminals"][0]["title"], session_id="sess-claude-0001")
    assert report["identity"] == identity
    assert report["actions"] == ["setup.host"]
    rows = daemon.journal.db.execute("SELECT operation_id,action,status,target,result FROM operations").fetchall()
    assert [(row["action"], row["status"]) for row in rows] == [("setup.host", "succeeded")]
    assert report["operation_id"] == rows[0]["operation_id"]
    configured_host = json.loads(rows[0]["target"])["host"]
    status, after = await http(port, "GET", "/api/v1/bootstrap", tok=personal_token)
    assert status == 200 and after["capabilities"]["identity"] == identity
    status, state = await http(port, "GET", "/api/v1/managed/setup", tok=personal_token)
    assert status == 200 and len(state["hosts"]) == 1
    host = state["hosts"][0]
    assert host["name"] == configured_host
    assert not host["writes"] and not host["orchestrate"] and host["managed_roots"] == []
    assert not host["git_status"]["available"] and not host["ssh_alias"] and state["repositories"] == []
    assert state["verification"]["commands"] == {}
    status, source = await http(port, "GET", f"/api/v1/sessions/{configured_host}/sess-claude-0001", tok=personal_token)
    assert status == 200
    assert source["session"]["title"] == mock.ws_doc["terminals"][0]["title"]
    assert source["session"]["provenance"] == "manual" and source["session"]["api_access"] == "read_only"
    assert source.get("work_items", []) == [] and not source.get("started_from")
    assert json.dumps(mock.states, sort_keys=True) == original_sessions
    assert not [frame for frame in mock.invokes
        if frame["channel"] in WRITE_CHANNELS | ORCHESTRATE_CHANNELS | GUARDED_CHANNELS]


@pytest.mark.parametrize("locale", ["en", "zh-TW"])
@pytest.mark.parametrize("entry", ["startup", "route"])
async def test_loading_view_renews_its_first_read_after_return(managed_browser, locale, entry):
    daemon = managed_browser["daemon"]
    report = await managed_browser["run"]("--resume-initial", locale, entry=entry)
    assert report["identity"] == managed_browser["identity"]
    assert report["operation_posts"] == 0
    assert report["source_reads"] == 4
    assert report["draft_key"] is None  # The app creates an intent key only on explicit Apply.
    assert daemon.journal.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
