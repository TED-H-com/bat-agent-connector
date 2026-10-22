"""Read-only MCP process identity, separate from an authorized central observation."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from mcp.server.mcpserver import MCPServer

from .build_identity import build_identity

_STARTED_AT = time.time()


def register(server: MCPServer, capabilities: Callable[[], Awaitable[dict]]) -> None:
    @server.resource("batc://identity", mime_type="application/json")
    async def identity() -> dict:
        """Loaded MCP build and a separate central capabilities observation; no installation or restart claim."""
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
        return {"mcp": {"source": "mcp_process", "build": build_identity(), "started_at": _STARTED_AT},
                "central": central}
