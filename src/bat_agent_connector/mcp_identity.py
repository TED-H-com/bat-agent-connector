"""Read-only MCP process identity, separate from an authorized central observation."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from mcp.server.mcpserver import MCPServer

from .build_identity import build_identity
from .updater import connector_update_status

_STARTED_AT = time.time()


def register(server: MCPServer, capabilities: Callable[[], Awaitable[dict]]) -> None:
    @server.resource("batc://identity", mime_type="application/json")
    async def identity() -> dict:
        """This process's loaded build, proven next launch, and separate central observation."""
        loaded = build_identity()
        update = connector_update_status(loaded)
        installed = update.get("installed") if update["state"] == "reported" else None
        difference = ("same" if installed["commit"] == loaded["commit"] else "differs") if installed else "unknown"
        next_launch = {"source": "connector_update", "state": update["state"], "installed": installed,
                       "observed_at": time.time(), "difference": difference, "restart_notice": None}
        if difference == "differs":
            next_launch["restart_notice"] = {"en": f"Restart to load {installed['version']}",
                                             "zh-TW": f"重新啟動以載入 {installed['version']}"}
        central = {"source": "central_capabilities", "build": None,
                   "status": "unavailable", "reason": "CENTRAL_IDENTITY_UNAVAILABLE"}
        try:
            caps = await capabilities()
            build = caps.get("build")
            if isinstance(build, dict) and {"version", "commit", "source"} <= build.keys():
                central.update(build={key: build[key] for key in ("version", "commit", "source")},
                               status="available", reason=None)
            else:
                central.update(status="unknown", reason="CENTRAL_BUILD_UNKNOWN")
        except Exception:
            # A refused/unavailable central must not hide local identity or leak
            # arbitrary transport errors, credentials or private endpoint details.
            central.update(status="unavailable", reason="CENTRAL_IDENTITY_UNAVAILABLE")
        central["observed_at"] = time.time()
        return {"mcp": {"source": "mcp_process", "build": loaded, "started_at": _STARTED_AT},
                "central": central, "next_launch": next_launch}
