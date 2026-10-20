"""Four task-local MCP tools passed to Goose via ACP session/new."""

from __future__ import annotations

import asyncio
import os
import sys

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from . import __version__, mcp_identity
from .task_daemon import request


def build(task_id: str) -> MCPServer:
    mcp = MCPServer(name="bat-task", version=__version__,
                    instructions="Only operate on this one task and its BAT session.")

    async def capabilities():
        return await asyncio.to_thread(request, "api_capabilities", _auth_token=os.environ.get("BATC_API_TOKEN") or None,
                                       entry="mcp")

    mcp_identity.register(mcp, capabilities)
    ro = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    wr = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

    async def task_read(marker: str | None = None) -> dict:
        """Read this task's BAT session output and turn correlation."""
        return await asyncio.to_thread(request, "task_read", task_id=task_id, marker=marker)

    async def task_send(text: str, step_id: str, idempotency_key: str | None = None,
                        control_version: int | None = None) -> dict:
        """Send one instruction to this task's BAT session. Reuse step_id on retries."""
        return await asyncio.to_thread(request, "task_send", timeout=40, entry="mcp", task_id=task_id, text=text, step_id=step_id,
                                       **({"idempotency_key": idempotency_key} if idempotency_key is not None else {}),
                                       **({"control_version": control_version} if control_version is not None else {}))

    async def task_run_verification(idempotency_key: str | None = None,
                                    control_version: int | None = None) -> dict:
        """Run the administrator-configured verifier; no caller-supplied exit code or command."""
        return await asyncio.to_thread(request, "task_run_verification", timeout=40, entry="mcp", task_id=task_id,
                                       **({"idempotency_key": idempotency_key} if idempotency_key is not None else {}),
                                       **({"control_version": control_version} if control_version is not None else {}))

    async def task_request_ted(reason: str, idempotency_key: str | None = None,
                               control_version: int | None = None) -> dict:
        """Stop automatic dispatch and ask the user for a concrete decision."""
        return await asyncio.to_thread(request, "task_request_ted", timeout=40, entry="mcp", task_id=task_id, reason=reason,
                                       **({"idempotency_key": idempotency_key} if idempotency_key is not None else {}),
                                       **({"control_version": control_version} if control_version is not None else {}))

    for fn, ann in ((task_read, ro), (task_send, wr), (task_run_verification, wr), (task_request_ted, wr)):
        mcp.add_tool(fn, name=fn.__name__, annotations=ann)
    return mcp


def main(argv: list[str] | None = None):
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        raise SystemExit("usage: bat-task-scoped-mcp TASK_ID")
    build(args[0]).run("stdio")


if __name__ == "__main__":
    main()
