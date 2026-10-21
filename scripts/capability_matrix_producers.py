"""Offline observations using the bootstrap-golden producer path.

The caller puts the candidate's src first on sys.path and supplies an empty disk
directory. No installed configuration, host, provider or external service is used.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


async def server_facts(directory):
    from bat_agent_connector import __version__, api_auth
    from bat_agent_connector.api_v1 import ApiV1
    from bat_agent_connector.config import Config
    from bat_agent_connector.task_daemon import TaskDaemon

    result = {}
    for managed in (False, True):
        daemon = TaskDaemon(Config(hosts={}), directory / f"{managed}.sqlite3")
        if managed:
            daemon.managed_installation = {"installation_id": "fixture-installation", "runtime_version": __version__}
        principal = api_auth.Principal("fixture-operator", frozenset({"observe", "operate"}))
        try:
            status, bootstrap = await ApiV1(daemon).bootstrap(principal)
            caps_status, caps = await ApiV1(daemon).capabilities(principal)
            if status != 200 or caps_status != 200 or bootstrap["capabilities"] != caps:
                raise ValueError("BOOTSTRAP_CAPABILITIES_DIFFER")
            if not managed:
                # The browser probe's HTTP adapter returns the complete real
                # response, not a hand-written subset. Ephemeral IDs/build digest
                # stay in this temporary file and never enter the golden.
                (directory / "browser-capabilities.json").write_text(json.dumps(caps), encoding="utf-8")
            result["managed" if managed else "plain"] = {
                "bootstrap_capabilities_equal": True,
                "principal": {"actor": caps["actor"], "scopes": caps["scopes"]},
                "features": caps["features"],
                "artifacts": caps["artifacts"],
                "managed_installation_present": "managed_installation" in caps,
                "desktop_identity_present": "desktop_identity" in caps,
            }
        finally:
            await daemon.fleet.close()
            await daemon.inventory.close()
            daemon.journal.close()
    return result


if __name__ == "__main__":
    print(json.dumps(asyncio.run(server_facts(Path(sys.argv[1]))), sort_keys=True))
