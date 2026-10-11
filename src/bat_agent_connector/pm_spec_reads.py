"""Async fixed-specification reads shared by HTTP, RPC and MCP."""
from __future__ import annotations

from . import pm_spec_sources
from .operations import OperationError

FIELDS = {
    "pm_spec_sources_get": ("workspace_id", "repository", "commit_sha", "paths"),
    "pm_spec_source_capabilities_get": ("workspace_id",),
}


async def read(ops, principal, method, params):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "Specification reads need observe", 403)
    if method not in FIELDS or set(params) - set(FIELDS[method]):
        raise OperationError("INVALID_PARAMS", "Unknown specification read parameters", 422)
    if method == "pm_spec_source_capabilities_get":
        return pm_spec_sources.capabilities(ops, principal, params.get("workspace_id"))
    if not all(isinstance(params.get(key), str) and params[key] for key in ("repository", "commit_sha")):
        raise OperationError("INVALID_PARAMS", "Specify the exact repository and full source commit", 422)
    return await pm_spec_sources.read_sources(ops, principal, params.get("workspace_id"),
        repository=params["repository"], commit_sha=params["commit_sha"], paths=params.get("paths"))
