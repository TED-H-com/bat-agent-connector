"""Producer readiness responses shared with the profile picker's browser proof."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from bat_agent_connector import platform_files, pm_work_profiles, product_preferences
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.host_git import HostGitRunner
from tests import test_pm_work_profiles as profiles
from tests.operation_helpers import settle_operations
from tests.test_api_v1 import http, token, write_frames
from tests.test_repository_sync import start

world = profiles.world
GOLDEN = Path(__file__).parent / "fixtures" / "work-profile-readiness.json"


async def test_work_profile_readiness_matches_producer_golden(world, mock, tmp_path):
    daemon, port = world["d"], world["port"]
    ops = daemon.ops
    owner = Principal("profile-owner", frozenset({"observe", "manage"}))
    auth = token(daemon, owner.actor, *owner.scopes)
    directory = tmp_path / "bat-profiles"
    platform_files.ensure_private_directory(directory)
    store = directory / "remote-tokens.enc.json"
    credentials = json.dumps({"enc": False, "data": {"tokens": {"fixture": mock.token}}})
    platform_files.atomic_write(store, credentials.encode())
    host = daemon.fleet.config.hosts["h1"]
    host.token_ref, host.bat_profiles_dir = "bat-profile:fixture", str(directory)
    runner = HostGitRunner(daemon.fleet.config, {"h1": "fixture-bat"})
    ops.context["git_runner"] = runner
    operation, _ = ops.create(owner, action="pm.workspace.create", params={"name": "Profiles"},
        idempotency_key="readiness-workspace")
    await settle_operations(ops)
    workspace = ops.get(operation["operation_id"])
    assert workspace["status"] == "succeeded", workspace
    wid = workspace["result"]["workspace"]["workspace_id"]
    document = {"version": 1, "binding": {"repository": "o/r", "host": "h1", "workspace_id": "ws-1"},
        "agent": "claude", "delivery_policy_ref": {"goal": "pull_request"}}
    operation, _ = ops.create(owner, action=pm_work_profiles.ACTION, target={"workspace_id": wid},
        params={"document": document}, idempotency_key="readiness-profile")
    await settle_operations(ops)
    saved = ops.get(operation["operation_id"])
    assert saved["status"] == "succeeded", saved
    ident = saved["result"]["profile_id"]
    base = f"/api/v1/pm/workspaces/{wid}/work-profiles"
    status, profile = await http(port, "GET", base + "/" + ident, tok=auth)
    assert status == 200
    cases = {}

    async def capture(name):
        before = (len(mock.invokes), len(write_frames(mock)), ops.db.total_changes)
        status, body = await http(port, "GET", base + "/" + ident + "/readiness", tok=auth)
        assert status == 200, body
        assert body["calls_bat"] is False
        assert before == (len(mock.invokes), len(write_frames(mock)), ops.db.total_changes)
        cases[name] = body
        return body

    never = await capture("never_observed")
    assert never["states"]["can_observe"]["reason"] == "never_observed"
    await daemon.inventory._refresh_scheduled("h1")
    assert daemon.inventory.hosts()[0]["reachable"] is True
    with daemon.journal.tx():
        ops.db.execute("UPDATE hosts_observed SET last_success_at=? WHERE host=?",
            (time.time() - daemon.inventory.settings.stale_after_s - 1, "h1"))
    stale = await capture("host_not_refreshed")
    assert stale["states"]["can_observe"]["reason"] == "host_not_refreshed"
    await daemon.inventory._refresh_scheduled("h1")
    platform_files.atomic_write(store, b'{"enc":false,"data":{"tokens":{}}}')
    missing = await capture("credential_required")
    assert missing["states"]["signed_in_or_available"]["reason"] == "CREDENTIAL_REQUIRED"
    platform_files.atomic_write(store, credentials.encode())
    mock.handlers["agent:usage-snapshot"] = lambda _: {"claude": {"provider": "claude",
        "fetchedAt": time.time() * 1000, "stale": {"reason": "unauthorized"}}}
    await product_preferences._observe(ops, "h1", "usage", "", "agent:usage-snapshot", {},
        lambda raw: product_preferences.normalize_usage(raw, time.time()), refresh=True)
    unauthorized = await capture("unauthorized")
    assert unauthorized["states"]["signed_in_or_available"]["reason"] == "unauthorized"
    ops.db.execute("DELETE FROM product_host_observations WHERE host=? AND kind=?", ("h1", "usage"))
    action = ops.actions.pop("repository.continue")
    try:
        unavailable = await capture("action_unavailable")
        assert unavailable["states"]["can_observe"]["reason"] == "action_unavailable"
    finally:
        ops.actions["repository.continue"] = action
    # Execute the existing repository harness against disk Git and MockBat, then
    # observe the genuinely owned session. No invented successful operation row.
    ops.context["git_runner"] = world["runner"]
    execution = await start(world)
    assert execution["status"] == "succeeded", execution
    ops.context["git_runner"] = runner
    await daemon.inventory._refresh_scheduled("h1")
    owned = await capture("owned_execution")
    for name in ("can_continue_own", "last_verified_execution"):
        assert owned["states"][name]["verified"]["evidence_ref"]["operation_id"] == execution["operation_id"]
    await mock.stop()
    await daemon.inventory._refresh_scheduled("h1")
    failed = await capture("host_unreachable")
    assert failed["states"]["can_observe"]["value"] == "unavailable"
    assert failed["states"]["can_observe"]["verified"]["value"] == "ready"

    # Keep full route documents. Redact only random IDs and wall-clock times;
    # profile digest and all classifications remain the producer's values.
    bundle = json.loads(json.dumps({"profile": profile, "cases": cases})
        .replace(wid, "ws_" + "1" * 32).replace(ident, "wpf_" + "a" * 32)
        .replace(saved["operation_id"], "op_" + "b" * 32)
        .replace(execution["operation_id"], "op_" + "c" * 32))

    def normalize(value, observed=1790000540.0):
        if isinstance(value, list):
            return [normalize(item, observed) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        stamp = 1790000500.0 if (value.get("evidence_ref") or {}).get("kind") == "operation" else observed
        for key, item in value.items():
            if key == "scan_id" and item is not None:
                result[key] = "scan_fixture"
            elif key in {"observed_at", "last_success_at", "finished_at", "created_at", "updated_at"} and item is not None:
                epoch = stamp if key == "observed_at" else observed
                result[key] = datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(item, str) else epoch
            else:
                result[key] = normalize(item, observed)
        return result

    bundle["profile"] = normalize(bundle["profile"])
    for name, body in bundle["cases"].items():
        bundle["cases"][name] = normalize(body, 1790000000.0 if name == "host_not_refreshed" else 1790000540.0)
        bundle["cases"][name]["observed_at"] = 1790000600.0
    raw = json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"
    assert mock.token not in raw and str(tmp_path) not in raw
    if os.environ.get("BATC_UPDATE_PROFILE_GOLDEN") == "1":
        GOLDEN.write_text(raw, encoding="utf-8")
    assert bundle == json.loads(GOLDEN.read_text(encoding="utf-8"))
