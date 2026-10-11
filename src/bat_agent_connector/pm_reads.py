"""Identical read contracts for the Dashboard, RPC and MCP PM entry points."""
from __future__ import annotations

from . import pm_discussions, pm_profiles, pm_workspace
from .operations import OperationError

FIELDS = {
    "pm_kanban_get": ("workspace_id", "project_id", "after", "limit"),
    "pm_kanban_card_get": ("workspace_id", "work_item_id"),
    "pm_meetings_list": ("workspace_id", "sprint_id", "after", "limit"),
    "pm_meeting_get": ("workspace_id", "meeting_id", "history_limit"),
    "pm_meeting_history_list": ("workspace_id", "meeting_id", "kind", "after", "limit"),
    "pm_sprint_artifacts_get": ('workspace_id',),
    "pm_sprint_artifact_revisions_list": ('workspace_id', 'kind', 'after', 'limit'),
    "pm_sprint_artifact_get": ('workspace_id', 'kind', 'revision_id'),
    "pm_sprints_list": ('workspace_id', 'after', 'limit'),
    "pm_sprint_get": ('workspace_id', 'sprint_id', 'history_limit'),
    "pm_sprint_history_list": ('workspace_id', 'sprint_id', 'kind', 'after', 'limit'),
    "pm_plan_compare": ("workspace_id", "from_revision_id", "to_revision_id", "binding_ids", "journal_cursor"),
    "pm_roadmap_get": ("workspace_id",),
    "pm_roadmap_revisions_list": ("workspace_id", "kind", "after", "limit"),
    "pm_roadmap_revision_get": ("workspace_id", "revision_id"),
    "pm_workspaces_list": (),
    "pm_workspace_get": ("workspace_id",),
    "pm_threads_list": ("workspace_id", "limit", "after"),
    "pm_thread_get": ("workspace_id", "thread_id", "limit", "before_message", "before_input"),
    "pm_context_get": ("workspace_id", "thread_id", "max_chars"),
    "pm_profiles_get": (),
    "pm_runs_list": ("workspace_id", "thread_id", "limit", "after"),
    "pm_run_get": ("workspace_id", "run_id"),
    "pm_capabilities_get": ("workspace_id",),
    "pm_plans_list": ("workspace_id", "after", "limit"),
    "pm_plan_get": ("workspace_id", "revision_id"),
    "pm_plan_accepted_get": ("workspace_id",),
    "pm_plan_head_get": ("workspace_id",),
    "pm_projects_list": ("workspace_id", "after", "limit"),
    "pm_work_list": ("workspace_id", "project_id", "after", "limit"),
    "pm_evidence_list": ("workspace_id", "work_item_id", "after", "limit"),
    "pm_evidence_get": ("workspace_id", "evidence_id"),
    "pm_capture_get": ("workspace_id", "capture_id"),
    "pm_capture_latest_get": ("workspace_id", "work_item_id"),
    "pm_snapshots_list": ("workspace_id", "after", "limit"),
    "pm_snapshot_get": ("workspace_id", "snapshot_id"),
    "pm_snapshot_latest_get": ("workspace_id",),
}


def read(ops, principal, method, params):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "PM reads need observe", 403)
    if method not in FIELDS or set(params) - set(FIELDS[method]):
        raise OperationError("INVALID_PARAMS", "Unknown PM read parameters", 422)
    workspace_id, thread_id = params.get("workspace_id"), params.get("thread_id")
    if method in {"pm_kanban_get", "pm_kanban_card_get"}:
        from . import pm_kanban
        if method == "pm_kanban_card_get":
            return pm_kanban.get_card(ops, principal, workspace_id, params.get("work_item_id"))
        return pm_kanban.get_board(ops, principal, workspace_id, project_id=params.get("project_id"),
                                   after=params.get("after", 0), limit=params.get("limit", 50))
    if method in {"pm_meetings_list", "pm_meeting_get", "pm_meeting_history_list"}:
        from . import pm_meetings
        if method == "pm_meeting_get":
            return pm_meetings.get_meeting(ops, principal, workspace_id, params.get("meeting_id"),
                                           history_limit=params.get("history_limit", 50))
        page = {"after": params.get("after", 0), "limit": params.get("limit", 50)}
        if method == "pm_meetings_list":
            return pm_meetings.list_meetings(ops, principal, workspace_id, sprint_id=params.get("sprint_id"), **page)
        return pm_meetings.list_history(ops, principal, workspace_id, params.get("meeting_id"), params.get("kind"), **page)
    if method in {"pm_sprint_artifacts_get", "pm_sprint_artifact_revisions_list", "pm_sprint_artifact_get",
                   "pm_sprints_list", "pm_sprint_get", "pm_sprint_history_list"}:
        from . import pm_sprints
        if method == "pm_sprint_artifacts_get":
            return pm_sprints.get_artifacts(ops, principal, workspace_id)
        if method == "pm_sprint_artifact_get":
            return pm_sprints.get_artifact(ops, principal, workspace_id, params.get("kind"), params.get("revision_id"))
        if method == "pm_sprint_get":
            return pm_sprints.get_sprint(ops, principal, workspace_id, params.get("sprint_id"), history_limit=params.get("history_limit", 50))
        page = {"after": params.get("after", 0), "limit": params.get("limit", 50)}
        if method == "pm_sprints_list":
            return pm_sprints.list_sprints(ops, principal, workspace_id, **page)
        if method == "pm_sprint_history_list":
            return pm_sprints.list_history(ops, principal, workspace_id, params.get("sprint_id"), params.get("kind"), **page)
        return pm_sprints.list_revisions(ops, principal, workspace_id, params.get("kind"), **page)
    if method == "pm_plan_compare":
        from . import pm_plan_diff
        if not all(isinstance(params.get(key), str) and params[key] for key in ("from_revision_id", "to_revision_id")):
            raise OperationError("INVALID_PARAMS", "Both fixed revision IDs are required", 422)
        return pm_plan_diff.compare(ops, principal, workspace_id, params.get("from_revision_id"), params.get("to_revision_id"),
                                    binding_ids=params.get("binding_ids", []), journal_cursor=params.get("journal_cursor"))
    if method in {"pm_roadmap_get", "pm_roadmap_revisions_list", "pm_roadmap_revision_get"}:
        from . import pm_roadmap
        if method == "pm_roadmap_get":
            return pm_roadmap.get_roadmap(ops, principal, workspace_id)
        if method == "pm_roadmap_revision_get":
            return pm_roadmap.get_revision(ops, principal, workspace_id, params.get("revision_id"))
        return pm_roadmap.list_revisions(ops, principal, workspace_id, kind=params.get("kind"),
                                        after=params.get("after", 0), limit=params.get("limit", 50))
    if method in {"pm_snapshots_list", "pm_snapshot_get", "pm_snapshot_latest_get"}:
        from . import pm_snapshots
        if method == "pm_snapshots_list":
            return pm_snapshots.list_snapshots(ops, principal, workspace_id,
                                               after=params.get("after", 0), limit=params.get("limit", 50))
        if method == "pm_snapshot_get":
            return pm_snapshots.get_snapshot(ops, principal, workspace_id, params.get("snapshot_id"))
        return pm_snapshots.latest_snapshot(ops, principal, workspace_id)
    if method in {"pm_projects_list", "pm_work_list", "pm_evidence_list", "pm_evidence_get",
                  "pm_capture_get", "pm_capture_latest_get"}:
        from . import pm_evidence
        page = {"after": params.get("after", 0), "limit": params.get("limit", 50)}
        if method == "pm_projects_list":
            return pm_evidence.list_projects(ops, principal, workspace_id, **page)
        if method == "pm_work_list":
            return pm_evidence.list_work(ops, principal, workspace_id, project_id=params.get("project_id"), **page)
        if method == "pm_evidence_list":
            return pm_evidence.list_evidence(ops, principal, workspace_id, work_item_id=params.get("work_item_id"), **page)
        if method == "pm_evidence_get":
            return pm_evidence.get_evidence(ops, principal, workspace_id, params.get("evidence_id"))
        if method == "pm_capture_get":
            return pm_evidence.get_capture(ops, principal, workspace_id, params.get("capture_id"))
        return pm_evidence.latest_capture(ops, principal, workspace_id, params.get("work_item_id"))
    if method in {"pm_plans_list", "pm_plan_get", "pm_plan_accepted_get", "pm_plan_head_get"}:
        from . import pm_plans
        pm_workspace.require_member(ops.db, principal, workspace_id)
        if method == "pm_plans_list":
            return pm_plans.list_plans(ops, principal, workspace_id, after=params.get("after", 0), limit=params.get("limit", 50))
        if method == "pm_plan_get":
            return pm_plans.get_plan(ops, principal, workspace_id, params.get("revision_id"))
        if method == "pm_plan_accepted_get":
            return pm_plans.accepted_plan(ops, principal, workspace_id)
        return {"version": 1, "head": pm_plans.head(ops.db, workspace_id)}
    if method == "pm_runs_list":
        from . import pm_runs
        return pm_runs.list_runs(ops, principal, workspace_id, thread_id=thread_id,
                                limit=params.get("limit", 50), after=params.get("after"))
    if method == "pm_run_get":
        from . import pm_runs
        return {"run": pm_runs.read(ops, principal, workspace_id, params.get("run_id"))}
    if method == "pm_capabilities_get":
        from . import pm_native_capabilities, pm_runs, pm_runtime, pm_worktree
        workspace = pm_workspace.require_member(ops.db, principal, workspace_id)
        repositories = {repository.lower() for repository in workspace["repositories"]}
        binding = pm_runs.workspace_worktree(ops.db, workspace_id)
        if "pm.plan.adopt" not in ops.actions:
            adoption_reason = "action_unavailable"
        elif not principal.admin and workspace["membership"]["role"] != "owner":
            adoption_reason = "owner_required"
        elif any(not principal.allows(scope) for scope in ("observe", "manage", "start")):
            adoption_reason = "scope_required"
        elif binding is None:
            adoption_reason = "pm_worktree_required"
        else:
            adoption_reason = None
        native = pm_native_capabilities.read(ops, principal, workspace)
        return {"workspace_id": workspace_id, "context_packages": True,
                "plan_drafting": "pm.plan.draft" in ops.actions,
                "plan_adoption": "pm.plan.adopt" in ops.actions,
                "plan_adoption_available": adoption_reason is None, "plan_adoption_reason": adoption_reason,
                "worktree_selection": ({**{key: binding["target"][key] for key in ("repository", "host", "workspace_id")},
                                        **{key: binding[key] for key in ("source_ref", "source_sha", "repository_id", "binding_digest")}}
                                       if binding else None),
                "runtime": pm_runtime.capability_report(),
                "native_analysis": native,
                "worktrees": [entry for entry in pm_worktree.capabilities(ops)
                              if entry["repository"].lower() in repositories]}
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
