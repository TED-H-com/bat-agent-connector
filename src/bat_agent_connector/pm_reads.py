"""Identical read contracts for the Dashboard, RPC and MCP PM entry points."""
from __future__ import annotations

import re

from . import pm_discussions, pm_external_history_links, pm_profiles, pm_workspace
from .operations import OperationError

FIELDS = {
    **pm_external_history_links.READS,
    "pm_dependencies_get": ("workspace_id",),
    "pm_dependencies_revisions_list": ("workspace_id", "after", "limit"),
    "pm_dependencies_impact_get": ("workspace_id", "dependency"),
    "pm_overview_get": ("workspace_id",),
    "pm_overview_assessments_list": ("workspace_id", "before", "limit"),
    "pm_overview_assessment_get": ("workspace_id", "assessment_id"),
    "pm_history_sources_list": ("workspace_id",),
    "pm_history_get": ("workspace_id", "source_id", "after", "limit"),
    "pm_resources_get": ("workspace_id", "since", "until", "buckets", "tz"),
    "pm_schedule_get": ("workspace_id", "after", "limit"),
    "pm_register_list": ("workspace_id", "type", "status", "owner", "q", "after", "limit"),
    "pm_register_get": ("workspace_id", "row_id"),
    "pm_ahp_adapters_list": ("workspace_id",),
    "pm_ahp_observations_list": ("workspace_id", "after", "limit"),
    "pm_ahp_observation_current_get": ("workspace_id",),
    "pm_ahp_observation_get": ("workspace_id", "observation_id"),
    "pm_pr_publication_get": ("workspace_id", "publication_operation_id"),
    "pm_maintenance_repairs_list": ("workspace_id", "after", "limit"),
    "pm_maintenance_repair_current_get": ("workspace_id",),
    "pm_maintenance_repair_get": ("workspace_id", "repair_id"),
    "pm_quality_reviews_list": ("workspace_id", "after", "limit"),
    "pm_quality_review_get": ("workspace_id", "review_id"),
    "pm_work_quality_get": ("workspace_id", "work_item_id", "after", "limit"),
    "pm_work_deliveries_list": ("workspace_id", "work_item_id", "after", "limit"),
    "pm_work_batches_list": ("workspace_id", "after", "limit"),
    "pm_work_batch_get": ("workspace_id", "batch_id"),
    "pm_maintenance_findings_current_get": ("workspace_id",),
    "pm_spec_materializations_list": ("workspace_id", "package_id", "after", "limit"),
    "pm_spec_materialization_get": ("workspace_id", "materialization_id"),
    "pm_maintenance_reviews_list": ("workspace_id", "after", "limit"),
    "pm_maintenance_review_current_get": ("workspace_id", "history_limit"),
    "pm_maintenance_review_get": ("workspace_id", "review_id", "history_limit"),
    "pm_maintenance_review_events_list": ("workspace_id", "review_id", "after", "limit"),
    "pm_maintenance_checks_list": ("workspace_id", "after", "limit"),
    "pm_maintenance_check_current_get": ("workspace_id",),
    "pm_maintenance_check_get": ("workspace_id", "run_id"),
    "pm_maintenance_schedule_get": ("workspace_id",),
    "pm_github_observations_list": ("workspace_id", "after", "limit"),
    "pm_github_observation_current_get": ("workspace_id",),
    "pm_github_observation_get": ("workspace_id", "observation_id"),
    "pm_workload_snapshots_list": ("workspace_id", "after", "limit"),
    "pm_workload_snapshot_current_get": ("workspace_id",),
    "pm_workload_snapshot_get": ("workspace_id", "snapshot_id"),
    "pm_lifecycle_get": ("workspace_id",),
    "pm_lifecycle_transitions_list": ("workspace_id", "after", "limit"),
    "pm_lifecycle_transition_get": ("workspace_id", "transition_id"),
    "pm_lifecycle_inflight_list": ("workspace_id", "transition_id", "after", "limit"),
    "pm_issue_mappings_list": ("workspace_id", "after", "limit"),
    "pm_issue_mapping_get": ("workspace_id", "mapping_id"),

    "pm_maintenance_policies_list": ("workspace_id", "after", "limit"),
    "pm_maintenance_policy_current_get": ("workspace_id",),
    "pm_maintenance_policy_get": ("workspace_id", "policy_id"),
    "pm_release_baselines_list": ("workspace_id", "after", "limit"),
    "pm_release_baseline_current_get": ("workspace_id",),
    "pm_release_baseline_get": ("workspace_id", "baseline_id"),
    "pm_work_packages_list": ("workspace_id", "work_item_id", "after", "limit"),
    "pm_work_package_get": ("workspace_id", "package_id"),
    "pm_work_package_readiness_get": ("workspace_id", "package_id", "quality_refs"),
    "pm_work_attempts_list": ("workspace_id", "work_item_id", "after", "limit"),
    "pm_work_inputs_list": ("workspace_id", "work_item_id", "attempt_id", "after", "limit"),
    "pm_work_attempt_get": ("workspace_id", "attempt_id"),
    "pm_conversation_proposals_list": ("workspace_id", "thread_id", "after", "limit"),
    "pm_conversation_proposal_get": ("workspace_id", "proposal_id"),
    "pm_follow_up_assessments_list": ("workspace_id", "intake_id", "after", "limit"),
    "pm_follow_up_assessment_get": ("workspace_id", "assessment_id"),
    "pm_follow_up_list": ("workspace_id", "after", "limit"),
    "pm_follow_up_get": ("workspace_id", "intake_id"),
    "pm_follow_up_latest_get": ("workspace_id",),
    "pm_governance_list": ("workspace_id", "kind", "after", "limit"),
    "pm_governance_get": ("workspace_id", "record_id"),
    "pm_governance_history_list": ("workspace_id", "record_id", "after", "limit"),
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
    "pm_context_bundle_preview": ("workspace_id", "thread_id", "max_chars", "kind"),
    "pm_profiles_get": (),
    "pm_runs_list": ("workspace_id", "thread_id", "limit", "after"),
    "pm_run_get": ("workspace_id", "run_id"),
    "pm_capabilities_get": ("workspace_id",),
    "pm_work_profiles_list": ("workspace_id", "limit", "cursor"),
    "pm_work_profile_get": ("workspace_id", "profile_id"),
    "pm_work_profile_readiness_get": ("workspace_id", "profile_id"),
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
    "pm_brief_export_get": ("workspace_id", "snapshot_id", "format", "locale"),
}


def read(ops, principal, method, params):
    if not principal.allows("observe"):
        raise OperationError("FORBIDDEN", "PM reads need observe", 403)
    if method not in FIELDS or set(params) - set(FIELDS[method]):
        raise OperationError("INVALID_PARAMS", "Unknown PM read parameters", 422)
    if method in pm_external_history_links.READS:
        return pm_external_history_links.read(ops, principal, method, params)
    workspace_id, thread_id = params.get("workspace_id"), params.get("thread_id")
    if method in {"pm_overview_assessments_list", "pm_overview_assessment_get"}:
        from . import pm_overview_assessments as assessments
        if method == "pm_overview_assessment_get":
            return assessments.get_assessment(ops, principal, workspace_id, params.get("assessment_id"))
        return assessments.list_assessments(ops, principal, workspace_id, before=params.get("before"), limit=params.get("limit", 10))
    if method == "pm_overview_get":
        from .pm_overview import read as overview
        return overview(ops, principal, workspace_id)
    if method in {"pm_dependencies_get", "pm_dependencies_revisions_list", "pm_dependencies_impact_get"}:
        from . import pm_dependency_reads as dependencies
        if method == "pm_dependencies_get":
            return dependencies.get(ops, principal, workspace_id)
        if method == "pm_dependencies_impact_get":
            return dependencies.impact(ops, principal, workspace_id, dependency=params.get("dependency"))
        return dependencies.revisions(ops, principal, workspace_id, after=params.get("after", 0), limit=params.get("limit", 20))
    if method in {"pm_history_sources_list", "pm_history_get"}:
        from . import session_history
        if method == "pm_history_sources_list":
            return session_history.list_sources(ops, principal, workspace_id)
        return session_history.read(ops, principal, workspace_id, params.get("source_id"),
            after=params.get("after", 0), limit=params.get("limit", 50))
    if method == "pm_resources_get":
        from .pm_resources import read as resources
        return resources(ops, principal, workspace_id, since=params.get("since"), until=params.get("until"),
                         tz=params.get("tz"), buckets=params.get("buckets"))
    if method == "pm_schedule_get":
        from .pm_schedule import read as schedule
        return schedule(ops, principal, workspace_id, after=params.get("after"), limit=params.get("limit"))
    if method == "pm_register_list":
        from .pm_register import list_register
        return list_register(ops, principal, workspace_id, row_type=params.get("type"), status=params.get("status"),
                             owner=params.get("owner"), q=params.get("q"), after=params.get("after"), limit=params.get("limit", 50))
    if method == "pm_register_get":
        from .pm_register import get_register
        return get_register(ops, principal, workspace_id, params.get("row_id"))
    if method == "pm_context_bundle_preview":
        if params.get("kind") == "overview_assessment":
            from .pm_overview_assessment_context import preview as overview_preview
            return overview_preview(ops, principal, workspace_id, thread_id, max_chars=params.get("max_chars", 32000))
        if params.get("kind") is not None:
            raise OperationError("INVALID_PARAMS", "Unknown context bundle kind", 422)
        from .pm_context_bundles import preview
        return preview(ops, principal, workspace_id, thread_id, max_chars=params.get("max_chars", 32000))
    if method in {"pm_ahp_adapters_list", "pm_ahp_observations_list", "pm_ahp_observation_current_get", "pm_ahp_observation_get"}:
        from . import pm_ahp_observations as ahp
        if method == "pm_ahp_adapters_list":
            return ahp.adapters(ops, principal, workspace_id)
        if method == "pm_ahp_observation_get":
            return ahp.get_observation(ops, principal, workspace_id, params.get("observation_id"))
        if method == "pm_ahp_observation_current_get":
            return ahp.current_observation(ops, principal, workspace_id)
        return ahp.list_observations(ops, principal, workspace_id, after=params.get("after", 0), limit=params.get("limit", 20))
    if method == 'pm_pr_publication_get':
        from . import pr_publication
        pm_workspace.require_member(ops.db, principal, workspace_id)
        oid = params.get('publication_operation_id')
        if not isinstance(oid, str) or not re.fullmatch('op_[0-9a-f]{32}', oid):
            raise OperationError('INVALID_PARAMS', 'Select an original publication operation ID', 422)
        op = pr_publication._operation(ops.db, oid)
        if op['target'].get('workspace_id') != workspace_id:
            raise OperationError('NOT_FOUND', 'Publication not found', 404)
        return {'workspace_id': workspace_id, **pr_publication.get_publication(ops, principal, oid)}
    if method in {"pm_maintenance_repairs_list", "pm_maintenance_repair_current_get", "pm_maintenance_repair_get"}:
        from . import pm_maintenance_repairs as repairs
        if method == "pm_maintenance_repair_get":
            return repairs.get_repair(ops, principal, workspace_id, params.get("repair_id"))
        if method == "pm_maintenance_repair_current_get":
            return repairs.current_repair(ops, principal, workspace_id)
        return repairs.list_repairs(ops, principal, workspace_id, after=params.get("after", 0), limit=params.get("limit", 20))
    if method in {"pm_quality_reviews_list", "pm_quality_review_get", "pm_work_quality_get"}:
        from . import pm_quality_reviews as quality
        if method == "pm_quality_review_get":
            return quality.get_review(ops, principal, workspace_id, params.get("review_id"))
        if method == "pm_work_quality_get":
            return quality.for_work(ops, principal, workspace_id, params.get("work_item_id"),
                after=params.get("after", 0), limit=params.get("limit", 50))
        return quality.list_reviews(ops, principal, workspace_id, after=params.get("after", 0), limit=params.get("limit", 50))
    if method == "pm_work_deliveries_list":
        from .pm_delivery_lineage import for_work
        return for_work(ops, principal, workspace_id, params.get("work_item_id"),
                        after=params.get("after", 0), limit=params.get("limit", 50))
    if method == "pm_maintenance_findings_current_get":
        from . import pm_maintenance_findings
        return pm_maintenance_findings.current_findings(ops, principal, workspace_id)
    if method in {"pm_work_batches_list", "pm_work_batch_get"}:
        from . import pm_work_batches
        if method == "pm_work_batch_get":
            return pm_work_batches.get_batch(ops, principal, workspace_id, params.get("batch_id"))
        return pm_work_batches.list_batches(ops, principal, workspace_id,
                                           after=params.get("after", 0), limit=params.get("limit", 20))
    if method in {"pm_spec_materializations_list", "pm_spec_materialization_get"}:
        from . import pm_spec_materialization as specs
        if method == "pm_spec_materialization_get":
            return specs.get_materialization(ops, principal, workspace_id, params.get("materialization_id"))
        return specs.list_materializations(ops, principal, workspace_id, package_id=params.get("package_id"),
                                          after=params.get("after", 0), limit=params.get("limit", 20))
    if method in {"pm_maintenance_reviews_list", "pm_maintenance_review_current_get", "pm_maintenance_review_get", "pm_maintenance_review_events_list"}:
        from . import pm_maintenance_review as review
        if method == "pm_maintenance_review_current_get":
            return review.current_review(ops, principal, workspace_id, history_limit=params.get("history_limit", 20))
        if method == "pm_maintenance_review_get":
            return review.get_review(ops, principal, workspace_id, params.get("review_id"), history_limit=params.get("history_limit", 20))
        page = {"after": params.get("after", 0), "limit": params.get("limit", 20)}
        if method == "pm_maintenance_review_events_list":
            return review.list_events(ops, principal, workspace_id, params.get("review_id"), **page)
        return review.list_reviews(ops, principal, workspace_id, **page)
    if method in {"pm_lifecycle_get", "pm_lifecycle_transitions_list", "pm_lifecycle_transition_get", "pm_lifecycle_inflight_list"}:
        from . import pm_lifecycle
        if method == "pm_lifecycle_get":
            return pm_lifecycle.get_lifecycle(ops, principal, workspace_id)
        if method == "pm_lifecycle_transition_get":
            return pm_lifecycle.get_transition(ops, principal, workspace_id, params.get("transition_id"))
        page = {"after": params.get("after", 0), "limit": params.get("limit", 20)}
        if method == "pm_lifecycle_transitions_list":
            return pm_lifecycle.list_transitions(ops, principal, workspace_id, **page)
        return pm_lifecycle.list_inflight(ops, principal, workspace_id, transition_id=params.get("transition_id"), **page)
    if method in {"pm_issue_mappings_list", "pm_issue_mapping_get"}:
        from . import pm_issue_mappings
        if method == "pm_issue_mapping_get":
            return pm_issue_mappings.get_mapping(ops, principal, workspace_id, params.get("mapping_id"))
        return pm_issue_mappings.list_mappings(ops, principal, workspace_id,
                                               after=params.get("after", 0), limit=params.get("limit", 50))

    if method in {"pm_maintenance_checks_list", "pm_maintenance_check_current_get", "pm_maintenance_check_get", "pm_maintenance_schedule_get"}:
        from . import pm_maintenance_checks as checks
        if method == "pm_maintenance_check_current_get":
            return checks.current_run(ops, principal, workspace_id)
        if method == "pm_maintenance_check_get":
            return checks.get_run(ops, principal, workspace_id, params.get("run_id"))
        if method == "pm_maintenance_schedule_get":
            return checks.schedule_status(ops, principal, workspace_id)
        return checks.list_runs(ops, principal, workspace_id, after=params.get("after", 0), limit=params.get("limit", 20))
    if method in {"pm_github_observations_list", "pm_github_observation_current_get", "pm_github_observation_get"}:
        from . import pm_github_observation
        if method == "pm_github_observation_current_get":
            return pm_github_observation.current_observation(ops, principal, workspace_id)
        if method == "pm_github_observation_get":
            return pm_github_observation.get_observation(ops, principal, workspace_id, params.get("observation_id"))
        return pm_github_observation.list_observations(ops, principal, workspace_id,
            after=params.get("after", 0), limit=params.get("limit", 20))
    if method in {"pm_workload_snapshots_list", "pm_workload_snapshot_current_get", "pm_workload_snapshot_get"}:
        from . import pm_workload
        if method == "pm_workload_snapshot_current_get":
            return pm_workload.current_snapshot(ops, principal, workspace_id)
        if method == "pm_workload_snapshot_get":
            return pm_workload.get_snapshot(ops, principal, workspace_id, params.get("snapshot_id"))
        return pm_workload.list_snapshots(ops, principal, workspace_id,
            after=params.get("after", 0), limit=params.get("limit", 20))
    if method in {"pm_maintenance_policies_list", "pm_maintenance_policy_current_get", "pm_maintenance_policy_get"}:
        from . import pm_maintenance_policy
        if method == "pm_maintenance_policy_current_get":
            return pm_maintenance_policy.current_policy(ops, principal, workspace_id)
        if method == "pm_maintenance_policy_get":
            return pm_maintenance_policy.get_policy(ops, principal, workspace_id, params.get("policy_id"))
        return pm_maintenance_policy.list_policies(ops, principal, workspace_id,
            after=params.get("after", 0), limit=params.get("limit", 20))
    if method in {"pm_release_baselines_list", "pm_release_baseline_current_get", "pm_release_baseline_get"}:
        from . import pm_release_baselines
        if method == "pm_release_baseline_current_get":
            return pm_release_baselines.current_baseline(ops, principal, workspace_id)
        if method == "pm_release_baseline_get":
            return pm_release_baselines.get_baseline(ops, principal, workspace_id, params.get("baseline_id"))
        return pm_release_baselines.list_baselines(ops, principal, workspace_id,
            after=params.get("after", 0), limit=params.get("limit", 20))
    if method == "pm_brief_export_get":
        from .pm_brief_exports import render_brief
        return render_brief(ops, principal, workspace_id, params.get("snapshot_id"),
                            format=params.get("format", "markdown"), locale=params.get("locale", "en"))
    if method in {"pm_work_packages_list", "pm_work_package_get", "pm_work_package_readiness_get"}:
        from . import pm_work_packages
        if method == "pm_work_package_get":
            return pm_work_packages.get_package(ops, principal, workspace_id, params.get("package_id"))
        if method == "pm_work_package_readiness_get":
            return pm_work_packages.readiness(ops, principal, workspace_id, params.get("package_id"), quality_refs=params.get('quality_refs'))
        return pm_work_packages.list_packages(ops, principal, workspace_id, work_item_id=params.get("work_item_id"),
                                              after=params.get("after", 0), limit=params.get("limit", 50))
    if method in {"pm_work_attempts_list", "pm_work_attempt_get"}:
        from . import pm_work_start
        if method == "pm_work_attempt_get":
            return pm_work_start.get_attempt(ops, principal, workspace_id, params.get("attempt_id"))
        return pm_work_start.list_attempts(ops, principal, workspace_id, params.get("work_item_id"),
                                           after=params.get("after", 0), limit=params.get("limit", 50))
    if method == "pm_work_inputs_list":
        from . import pm_work_follow_up
        return pm_work_follow_up.list_inputs(
            ops, principal, workspace_id, params.get("work_item_id"),
            attempt_id=params.get("attempt_id"),
            after=params.get("after", 0), limit=params.get("limit", 50))
    if method in {"pm_conversation_proposals_list", "pm_conversation_proposal_get"}:
        from . import pm_conversation_proposals
        if method == "pm_conversation_proposal_get":
            return pm_conversation_proposals.get_proposal(ops, principal, workspace_id, params.get("proposal_id"))
        return pm_conversation_proposals.list_proposals(ops, principal, workspace_id, thread_id=params.get("thread_id"),
            after=params.get("after", 0), limit=params.get("limit", 50))
    if method in {"pm_follow_up_assessments_list", "pm_follow_up_assessment_get"}:
        from . import pm_follow_up_analysis
        if method == "pm_follow_up_assessment_get":
            return pm_follow_up_analysis.get_assessment(ops, principal, workspace_id, params.get("assessment_id"))
        return pm_follow_up_analysis.list_assessments(ops, principal, workspace_id, intake_id=params.get("intake_id"),
            after=params.get("after", 0), limit=params.get("limit", 50))
    if method in {"pm_follow_up_list", "pm_follow_up_get", "pm_follow_up_latest_get"}:
        from . import pm_follow_up
        if method == "pm_follow_up_get":
            return pm_follow_up.get_intake(ops, principal, workspace_id, params.get("intake_id"))
        if method == "pm_follow_up_latest_get":
            return pm_follow_up.latest_intake(ops, principal, workspace_id)
        return pm_follow_up.list_intakes(ops, principal, workspace_id, after=params.get("after", 0), limit=params.get("limit", 50))
    if method in {"pm_governance_list", "pm_governance_get", "pm_governance_history_list"}:
        from . import pm_governance
        if method == "pm_governance_get":
            return pm_governance.get_record(ops, principal, workspace_id, params.get("record_id"))
        page = {"after": params.get("after", 0), "limit": params.get("limit", 50)}
        if method == "pm_governance_list":
            return pm_governance.list_records(ops, principal, workspace_id, kind=params.get("kind"), **page)
        return pm_governance.list_history(ops, principal, workspace_id, params.get("record_id"), **page)
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
        return {"version": 1, "head": pm_plans.public_head(ops, principal, workspace_id)}
    if method == "pm_runs_list":
        from . import pm_runs
        return pm_runs.list_runs(ops, principal, workspace_id, thread_id=thread_id,
                                limit=params.get("limit", 50), after=params.get("after"))
    if method == "pm_run_get":
        from . import pm_runs
        return {"run": pm_runs.read(ops, principal, workspace_id, params.get("run_id"))}
    if method in {"pm_work_profiles_list", "pm_work_profile_get", "pm_work_profile_readiness_get"}:
        from . import pm_work_profiles
        if method == "pm_work_profiles_list":
            query = {key: params[key] for key in ("limit", "cursor") if key in params}
            return pm_work_profiles.list_profiles(ops.db, principal, workspace_id, query)
        if method == "pm_work_profile_get":
            return pm_work_profiles.get_profile(ops.db, principal, workspace_id, params.get("profile_id"))
        return pm_work_profiles.readiness(ops, principal, workspace_id, params.get("profile_id"))
    if method == "pm_capabilities_get":
        from . import (
            pm_analysis_adapters,
            pm_attempt_result_reads,
            pm_bat_operations,
            pm_native_capabilities,
            pm_overview_assessments,
            pm_runs,
            pm_runtime,
            pm_work_follow_up,
            pm_work_profiles,
            pm_worktree,
        )
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
        bat = pm_bat_operations.capabilities(ops, principal, workspace)
        from . import pm_workspace_adapter
        choice = pm_workspace_adapter.read(ops, principal, workspace_id) if pm_workspace_adapter.ACTION in ops.actions else None
        follow_up_registered = "pm.follow_up.analyze" in ops.actions
        follow_up = {**native, "action_registered": follow_up_registered,
            "authorized": (follow_up_registered and (principal.admin or workspace["membership"]["role"] in {"owner", "contributor"})
                and all(principal.allows(scope) for scope in ("observe", "manage", "start"))),
            "authority": "context_only", "output_destination": "follow_up_assessment",
            "conclusions_verified": False, "execution_authorized": False}
        return {"workspace_id": workspace_id, "context_packages": True,
                "plan_drafting": "pm.plan.draft" in ops.actions,
                "plan_adoption": "pm.plan.adopt" in ops.actions,
                "plan_adoption_available": adoption_reason is None, "plan_adoption_reason": adoption_reason,
                "worktree_selection": ({**{key: binding["target"][key] for key in ("repository", "host", "workspace_id")},
                                        **{key: binding[key] for key in ("source_ref", "source_sha", "repository_id", "binding_digest")}}
                                       if binding else None),
                "runtime": pm_runtime.capability_report(),
                "native_analysis": native,
                "bat_analysis": bat,
                "structured_analysis": pm_analysis_adapters.capabilities(ops, principal, workspace, native=native, bat=bat),
                "workspace_adapter": choice,
                "effective_pm_route": pm_workspace_adapter.effective_route(choice, native, bat,
                    [scope for scope in pm_workspace_adapter.SCOPES if principal.allows(scope)]),
                "follow_up_analysis": follow_up,
                "overview_assessment": pm_overview_assessments.capabilities(ops, principal, workspace, native=native, bat=bat),
                "conversation_proposals": {"version": 1, "analysis": native, "adoption_available": adoption_reason is None,
                    "adoption_scopes": ["observe", "manage", "start"], "adoption_role": "owner"},
                "work_attempt_results": pm_attempt_result_reads.capabilities(ops),
                "work_profiles": pm_work_profiles.capabilities(ops),
                "work_follow_up": pm_work_follow_up.capabilities(ops),
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
