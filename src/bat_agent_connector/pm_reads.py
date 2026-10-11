"""Identical read contracts for the Dashboard, RPC and MCP PM entry points."""
from __future__ import annotations

from . import pm_discussions, pm_profiles, pm_workspace
from .operations import OperationError

FIELDS = {
    "pm_workspaces_list": (),
    "pm_workspace_get": ("workspace_id",),
    "pm_threads_list": ("workspace_id", "limit", "after"),
    "pm_thread_get": ("workspace_id", "thread_id", "limit", "before_message", "before_input"),
    "pm_context_get": ("workspace_id", "thread_id", "max_chars"),
    "pm_profiles_get": (),
}


def read(ops, principal, method, params):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "PM reads need observe", 403)
    if method not in FIELDS or set(params) - set(FIELDS[method]):
        raise OperationError("INVALID_PARAMS", "Unknown PM read parameters", 422)
    workspace_id, thread_id = params.get("workspace_id"), params.get("thread_id")
    if method == "pm_workspaces_list":
        return {"workspaces": pm_workspace.list_workspaces(ops.db, principal)}
    if method == "pm_workspace_get":
        return {"workspace": pm_workspace.get_workspace(ops.db, principal, workspace_id)}
    if method == "pm_profiles_get":
        return pm_profiles.catalog()
    if method == "pm_threads_list":
        return pm_discussions.list_threads(ops, principal, workspace_id,
                                           limit=params.get("limit", 50), after=params.get("after"))
    if method == "pm_thread_get":
        return pm_discussions.get_thread(ops, principal, workspace_id, thread_id,
            limit=params.get("limit", 50), before_message=params.get("before_message"),
            before_input=params.get("before_input"))
    return pm_discussions.context(ops, principal, workspace_id, thread_id,
                                  max_chars=params.get("max_chars", 32000))
