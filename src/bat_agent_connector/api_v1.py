"""/api/v1: the Dashboard's HTTP API, served by the task daemon on its existing loopback listener.

Every route needs a bearer token (the admin token or an API token from ``batc api-token issue``); the actor is taken
from the token. Reads come from the persisted inventory, the operation records and the event log; every change goes
through ``OperationService.create`` like MCP and CLI. Browsers are kept honest with a loopback Host check (DNS
rebinding), an Origin allowlist and bearer-only auth (no cookies, so no CSRF). Design: docs/design/api-v1.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import time
from importlib import resources
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import (
    __version__,
    api_auth,
    artifact_capture,
    artifact_managed,
    artifacts,
    checkpoints,
    cleanup,
    confinement,
    dashboard_sync,
    deployment,
    integration,
    pm_operation_loop,
    pr_delivery,
    repository_sync,
    resource_policy,
    service,
    session_start_operations,
    work_items,
)
from .build_identity import build_identity
from .connection_contract import BOUNDS as CONNECTION_BOUNDS
from .connection_contract import VERSION as CONNECTION_VERSION
from .connection_contract import decode_native_json
from .errors import BatError, ResourceReadOnly
from .operations import STATES, OperationError
from .updater import connector_update_status

API_VERSION = 1
CONTRACT_VERSION = "2026-10-08"  # delivery A/B contract changes share this UTC date; keep an ISO date
MAX_BODY = 200_000
MAX_STREAMS = 16
MAX_STREAMS_PER_ACTOR = 8  # several Dashboard tabs per person; reloads briefly overlap
REAUTH_S = 5.0  # an open event stream re-checks its token this often (revoked or expired tokens stop it)
STREAM_MAX_S = 1800.0
KEEPALIVE_S = 15.0
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
# The Dashboard is four static files; nothing else under /dashboard/ is served.
DASHBOARD_FILES = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
                   "i18n.js": "text/javascript; charset=utf-8", "app.css": "text/css; charset=utf-8"}
_FRONTEND_PLACEHOLDER = b"__BATC_FRONTEND_DIGEST_PLACEHOLDER______________________________"


def _dashboard_snapshot() -> tuple[dict[str, bytes], str | None]:
    """Identify stored bytes, including the placeholder, never a process-cached build.

    The app.js response uses this same read, so its substituted identity cannot
    describe an earlier read of that file during an in-place upgrade.
    """
    directory = resources.files(__package__) / "dashboard"
    try:
        files = {name: (directory / name).read_bytes() for name in sorted(DASHBOARD_FILES)}
    except OSError:
        return {}, None
    if files["app.js"].count(_FRONTEND_PLACEHOLDER) != 1:
        return files, None
    digest = hashlib.sha256(b"batc.frontend.files.v1\0")
    for name, body in files.items():
        digest.update(f"{len(name.encode())}:{name}\0{len(body)}\0".encode())
        digest.update(body)
    return files, digest.hexdigest()


DASHBOARD_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
                 "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_REASONS = {200: "OK", 202: "Accepted", 204: "No Content", 302: "Found", 400: "Bad Request", 401: "Unauthorized",
            403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 409: "Conflict", 413: "Payload Too Large",
            422: "Unprocessable Entity", 429: "Too Many Requests", 500: "Internal Server Error",
            502: "Bad Gateway", 503: "Service Unavailable"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def _host_is_loopback(value: str) -> bool:
    host = value.strip()
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.lower() in _LOOPBACK_HOSTS or re.fullmatch(r"127(\.\d{1,3}){3}", host) is not None


def _origin_ok(origin: str, allowed: tuple[str, ...]) -> bool:
    if origin in allowed:
        return True
    parts = urlsplit(origin)
    return parts.scheme in {"http", "https"} and _host_is_loopback(parts.netloc)


def is_dashboard_path(path: str) -> bool:
    return path in {"/", "/dashboard"} or path.startswith("/dashboard/")


def parse_wait(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        wait = float(value)
    except (TypeError, ValueError):
        raise ApiError(422, "INVALID_REQUEST", "wait must be a number of seconds") from None
    if not 0 <= wait <= 60:
        raise ApiError(422, "INVALID_REQUEST", "wait must be 0-60 seconds")
    return wait


class ApiV1:
    def __init__(self, daemon, *, allowed_origins: tuple[str, ...] = ()) -> None:
        self.daemon = daemon
        cleanup.install(daemon.ops, daemon._admin_token)
        artifact_capture.install(daemon.ops, daemon._admin_token)
        artifact_managed.install(daemon.ops)
        session_start_operations.install(daemon.ops)
        repository_sync.install(daemon.ops)
        from . import orchestration_operations
        orchestration_operations.install(daemon.ops)
        from . import fanout_operations
        fanout_operations.install(daemon.ops)
        from . import verification_operations
        verification_operations.install(daemon.ops)
        from . import failover_operations
        failover_operations.install(daemon.ops)
        from . import worktree_merge_operations
        worktree_merge_operations.install(daemon.ops)
        from . import setup_connections
        setup_connections.install(daemon.ops)

        from . import bulk_approval
        bulk_approval.install(daemon.ops, daemon._admin_token)
        from . import managed_repairs, product_preferences, project_skills
        managed_repairs.install(daemon.ops)
        product_preferences.install(daemon.ops)
        project_skills.install(daemon.ops)
        from . import (
            pm_ahp_observations,
            pm_conversation_proposals,
            pm_discussions,
            pm_evidence,
            pm_execution_verification,
            pm_follow_up,
            pm_follow_up_analysis,
            pm_github_observation,
            pm_governance,
            pm_issue_mappings,
            pm_lifecycle,
            pm_maintenance_checks,
            pm_maintenance_policy,
            pm_maintenance_repairs,
            pm_maintenance_review,
            pm_meetings,
            pm_native_operations,
            pm_plan_operations,
            pm_quality_reviews,
            pm_release_baselines,
            pm_roadmap,
            pm_run_operations,
            pm_snapshots,
            pm_spec_materialization,
            pm_sprints,
            pm_work_batches,
            pm_work_packages,
            pm_workload,
            pm_workspace,
        )
        pm_workspace.install(daemon.ops)
        pm_ahp_observations.install(daemon.ops)
        pm_discussions.install(daemon.ops)
        pm_run_operations.install(daemon.ops)
        pm_native_operations.install(daemon.ops)
        from . import pm_bat_operations
        pm_bat_operations.install(daemon.ops)
        pm_plan_operations.install(daemon.ops)
        from . import pm_workspace_adapter
        pm_workspace_adapter.install(daemon.ops)
        pm_evidence.install(daemon.ops)
        from . import board_github
        board_github.install(daemon.ops)
        pm_snapshots.install(daemon.ops)
        pm_roadmap.install(daemon.ops)
        pm_sprints.install(daemon.ops)
        pm_meetings.install(daemon.ops)
        pm_governance.install(daemon.ops)
        pm_follow_up.install(daemon.ops)
        pm_follow_up_analysis.install(daemon.ops)
        pm_conversation_proposals.install(daemon.ops)
        from . import pm_continuation
        pm_continuation.install(daemon.ops)
        from . import pm_overview_assessments
        pm_overview_assessments.install(daemon.ops)
        pm_work_packages.install(daemon.ops)
        pm_spec_materialization.install(daemon.ops)
        pm_work_batches.install(daemon.ops)
        pm_lifecycle.install(daemon.ops)
        pm_issue_mappings.install(daemon.ops)

        pm_release_baselines.install(daemon.ops)
        pm_maintenance_policy.install(daemon.ops)
        pm_workload.install(daemon.ops)
        pm_github_observation.install(daemon.ops)
        from . import pm_dependencies
        pm_dependencies.install(daemon.ops)
        pm_maintenance_checks.install(daemon.ops)
        pm_maintenance_review.install(daemon.ops)
        pm_maintenance_repairs.install(daemon.ops)
        pm_quality_reviews.install(daemon.ops)
        pm_execution_verification.install(daemon.ops)
        from . import pr_publication
        pr_publication.install(daemon.ops)
        from . import pm_attempt_results, pm_work_adopt_and_start, pm_work_profiles, pm_work_start
        pm_work_profiles.install(daemon.ops)
        pm_work_start.install(daemon.ops)
        pm_work_adopt_and_start.install(daemon.ops)
        pm_attempt_results.install(daemon.ops)
        from . import pm_work_input_idea
        pm_work_input_idea.install(daemon.ops)
        from .session_observation import SessionObservation
        self.session_observation = SessionObservation(daemon)
        self.allowed_origins = allowed_origins
        self._streams = 0
        self._streams_by_actor: dict[str, int] = {}
        self.routes = [
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/overview", self.pm_overview, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/resources", self.pm_resources, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/history", self.pm_history_sources, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/history/(?P<source_id>[A-Za-z0-9][A-Za-z0-9_.:-]{0,127})", self.pm_history, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/schedule", self.pm_schedule, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/register", self.pm_register, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/register/(?P<row_id>[^/]+)", self.pm_register_row, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/dependencies", self.pm_dependencies, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/dependencies/revisions", self.pm_dependency_revisions, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/dependencies/impact", self.pm_dependency_impact, "observe"),
            ("GET", r"/api/v1/managed/setup", self.managed_setup_state, "manage"),
            ("POST", r"/api/v1/managed/setup/discover", self.managed_setup_discover, "manage"),
            ("POST", r"/api/v1/managed/setup/preview", self.managed_setup_preview, "manage"),
            ("POST", r"/api/v1/managed/setup/secrets", self.managed_setup_secret, "manage"),
            ("POST", r"/api/v1/approval-previews", self.approval_preview, "observe"),
            ("POST", r"/api/v1/repository-previews", self.repository_preview, "observe"),
            ("POST", r"/api/v1/cleanup-previews", self.cleanup_preview, "observe"),
            ("GET", r"/api/v1/cleanup-retained", self.cleanup_retained, "observe"),
            ("GET", r"/api/v1/cleanup-tombstones", self.cleanup_tombstones, "observe"),
            ("GET", r"/api/v1/cleanup-tombstones/(?P<rid>(?:cr|wt)_[0-9a-f]{32})", self.cleanup_tombstone, "observe"),
            ("GET", r"/api/v1/version", self.version, None),
            ("GET", r"/api/v1/capabilities", self.capabilities, "observe"),
            ("GET", r"/api/v1/workspaces", self.workspaces, "observe"),
            ("GET", r"/api/v1/pm/workspaces", self.pm_workspaces, "observe"),
            ("GET", r"/api/v1/pm/installation-capabilities", self.pm_installation_capabilities, "observe"),
            ("GET", r"/api/v1/pm/profiles", self.pm_profiles, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})", self.pm_workspace, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/lifecycle", self.pm_lifecycle, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/lifecycle/transitions", self.pm_lifecycle_transitions, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/lifecycle/transitions/(?P<transition_id>pmlt_[0-9a-f]{32})", self.pm_lifecycle_transition, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/lifecycle/inflight", self.pm_lifecycle_inflight, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/threads", self.pm_threads, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/runs", self.pm_runs, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/runs/(?P<run_id>pmr_[0-9a-f]{32})", self.pm_run, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/capabilities", self.pm_capabilities, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/spec-sources", self.pm_spec_sources, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/spec-sources/capabilities", self.pm_spec_source_capabilities, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/plans", self.pm_plans, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/projects", self.pm_projects, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/plan-comparison", self.pm_plan_comparison, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/kanban", self.pm_kanban, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/kanban/(?P<work_item_id>wi_[0-9a-f]{20})", self.pm_kanban_card, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/governance", self.pm_governance_records, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/issue-mappings", self.pm_issue_mappings, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/issue-mappings/(?P<mapping_id>pmim_[0-9a-f]{32})", self.pm_issue_mapping, "observe"),


            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/release-baseline-sources", self.pm_release_baseline_sources, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/release-baseline-sources/github-releases", self.pm_release_baseline_releases, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/release-baseline-sources/deployments", self.pm_release_baseline_deployments, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/release-baseline-preview", self.pm_release_baseline_preview, "observe"),
            ("POST", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/threads/(?P<thread_id>pmt_[0-9a-f]{32})/adopt-and-start-preview", self.pm_work_adopt_and_start_preview, "observe"),
            ("POST", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work/(?P<work_item_id>wi_[0-9a-f]{20})/start-preview", self.pm_work_start_preview, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work/(?P<work_item_id>wi_[0-9a-f]{20})/attempts", self.pm_work_attempts, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work/(?P<work_item_id>wi_[0-9a-f]{20})/inputs", self.pm_work_inputs, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work-attempts/(?P<attempt_id>att_[0-9a-f]{32})", self.pm_work_attempt, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work-attempts/(?P<attempt_id>att_[0-9a-f]{32})/result", self.pm_work_attempt_result, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work-attempts/(?P<attempt_id>att_[0-9a-f]{32})/changes", self.pm_work_attempt_changes, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work-attempts/(?P<attempt_id>att_[0-9a-f]{32})/diff", self.pm_work_attempt_diff, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work/(?P<work_item_id>wi_[0-9a-f]{20})/deliveries", self.pm_work_deliveries, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-reviews", self.pm_maintenance_reviews, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-reviews/current", self.pm_maintenance_review_current, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-reviews/(?P<review_id>pmrevw_[0-9a-f]{32})", self.pm_maintenance_review, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-reviews/(?P<review_id>pmrevw_[0-9a-f]{32})/events", self.pm_maintenance_review_events, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-checks", self.pm_maintenance_checks, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-checks/current", self.pm_maintenance_check_current, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-checks/(?P<run_id>pmck_[0-9a-f]{32})", self.pm_maintenance_check, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-schedule", self.pm_maintenance_schedule, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/ahp-adapters", self.pm_ahp_adapters, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/ahp-observations", self.pm_ahp_observations, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/ahp-observations/current", self.pm_ahp_observation_current, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/ahp-observations/(?P<observation_id>pmah_[0-9a-f]{32})", self.pm_ahp_observation, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/github-observations", self.pm_github_observations, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/github-observations/current", self.pm_github_observation_current, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/github-observations/(?P<observation_id>pmgh_[0-9a-f]{32})", self.pm_github_observation, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/workload-snapshots", self.pm_workload_snapshots, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/workload-snapshots/current", self.pm_workload_snapshot_current, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/workload-snapshots/(?P<snapshot_id>pmwl_[0-9a-f]{32})", self.pm_workload_snapshot, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-policies", self.pm_maintenance_policies, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-policies/current", self.pm_maintenance_policy_current, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-policies/(?P<policy_id>pmmp_[0-9a-f]{32})", self.pm_maintenance_policy, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/release-baselines", self.pm_release_baselines, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/release-baselines/current", self.pm_release_baseline_current, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/release-baselines/(?P<baseline_id>pmrb_[0-9a-f]{32})", self.pm_release_baseline, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-repairs", self.pm_maintenance_repairs, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-repairs/current", self.pm_maintenance_repair_current, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-repairs/(?P<repair_id>pmrp_[0-9a-f]{32})", self.pm_maintenance_repair, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/executions/(?P<execution_operation_id>op_[0-9a-f]{32})/verification-config", self.pm_execution_verification_configuration, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/execution-verifications/(?P<verification_operation_id>op_[0-9a-f]{32})", self.pm_execution_verification_get, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/pr-publications/(?P<publication_operation_id>op_[0-9a-f]{32})", self.pm_pr_publication, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/quality-reviews", self.pm_quality_reviews, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/quality-reviews/(?P<review_id>pmqr_[0-9a-f]{32})", self.pm_quality_review, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work/(?P<work_item_id>wi_[0-9a-f]{20})/quality", self.pm_work_quality, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work-batches", self.pm_work_batches, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work-batches/(?P<batch_id>pmbt_[0-9a-f]{32})", self.pm_work_batch, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/maintenance-findings/current", self.pm_maintenance_findings_current, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/spec-materializations", self.pm_spec_materializations, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/spec-materializations/(?P<materialization_id>pmsp_[0-9a-f]{32})", self.pm_spec_materialization, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work-packages", self.pm_work_packages, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work-packages/(?P<package_id>pmwp_[0-9a-f]{32})", self.pm_work_package, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work-packages/(?P<package_id>pmwp_[0-9a-f]{32})/readiness", self.pm_work_package_readiness, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work-profiles", self.pm_work_profiles, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work-profiles/(?P<profile_id>wpf_[0-9a-f]{32})", self.pm_work_profile, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work-profiles/(?P<profile_id>wpf_[0-9a-f]{32})/readiness", self.pm_work_profile_readiness, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/conversation-proposals", self.pm_conversation_proposals, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/conversation-proposals/(?P<proposal_id>pmcp_[0-9a-f]{32})", self.pm_conversation_proposal, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/overview-assessments", self.pm_overview_assessments, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/overview-assessments/(?P<assessment_id>pmoa_[0-9a-f]{32})", self.pm_overview_assessment, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/follow-up-assessments", self.pm_follow_up_assessments, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/snapshots/(?P<snapshot_id>pmsnap_[0-9a-f]{32})/brief-export", self.pm_brief_export, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/follow-up-assessments/(?P<assessment_id>pmfa_[0-9a-f]{32})", self.pm_follow_up_assessment, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/follow-ups", self.pm_follow_ups, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/follow-ups/latest", self.pm_follow_up_latest, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/follow-ups/(?P<intake_id>pmfu_[0-9a-f]{32})", self.pm_follow_up, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/governance/(?P<record_id>pmgov_[0-9a-f]{32})", self.pm_governance_record, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/governance/(?P<record_id>pmgov_[0-9a-f]{32})/history", self.pm_governance_history, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/meetings", self.pm_meetings, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/meetings/(?P<meeting_id>pmmeet_[0-9a-f]{32})", self.pm_meeting, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/meetings/(?P<meeting_id>pmmeet_[0-9a-f]{32})/history/(?P<kind>contexts|notes|drafts|follow_ups)", self.pm_meeting_history, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/sprint-artifacts", self.pm_sprint_artifacts, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/sprint-artifacts/(?P<kind>product_goal|dod|backlog)", self.pm_sprint_artifact_revisions, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/sprint-artifacts/(?P<kind>product_goal|dod|backlog)/(?P<revision_id>(?:pmgoal|pmdod|pmbkl)_[0-9a-f]{32})", self.pm_sprint_artifact, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/sprints", self.pm_sprints, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/sprints/(?P<sprint_id>pms_[0-9a-f]{32})", self.pm_sprint, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/sprints/(?P<sprint_id>pms_[0-9a-f]{32})/history/(?P<kind>goals|selections|events)", self.pm_sprint_history, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/roadmap", self.pm_roadmap, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/roadmap/revisions", self.pm_roadmap_revisions, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/roadmap/revisions/(?P<revision_id>pmr[bfo]_[0-9a-f]{32})", self.pm_roadmap_revision, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/snapshots", self.pm_snapshots, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/snapshots/latest", self.pm_snapshot_latest, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/snapshots/(?P<snapshot_id>pmsnap_[0-9a-f]{32})", self.pm_snapshot, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work", self.pm_work, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/evidence", self.pm_evidence, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/evidence/(?P<evidence_id>pme_[0-9a-f]{32})", self.pm_evidence_detail, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/captures/(?P<capture_id>pmcap_[0-9a-f]{32})", self.pm_capture, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/work/(?P<work_item_id>wi_[0-9a-f]{20})/capture", self.pm_capture_latest, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/plans/head", self.pm_plan_head, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/plans/accepted", self.pm_plan_accepted, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/plans/(?P<revision_id>pmrev_[0-9a-f]{32})", self.pm_plan, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/threads/(?P<thread_id>pmt_[0-9a-f]{32})", self.pm_thread, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/threads/(?P<thread_id>pmt_[0-9a-f]{32})/context", self.pm_context, "observe"),
            ("GET", r"/api/v1/pm/workspaces/(?P<workspace_id>ws_[0-9a-f]{32})/threads/(?P<thread_id>pmt_[0-9a-f]{32})/context-bundle", self.pm_context_bundle, "observe"),
            ("POST", r"/api/v1/artifacts", self.create_artifact, "manage"),
            ("POST", r"/api/v1/artifact-capture-previews", self.artifact_capture_preview, "observe"),
            ("POST", r"/api/v1/artifact-managed-capture-previews", self.artifact_managed_capture_preview, "observe"),
            ("GET", r"/api/v1/artifacts", self.artifacts, "observe"),
            ("GET", r"/api/v1/artifacts/(?P<aid>art_[0-9a-f]{32})/revisions/(?P<revision>[1-9][0-9]*)", self.artifact, "observe"),
            ("GET", r"/api/v1/bootstrap", self.bootstrap, "observe"),
            ("GET", r"/api/v1/hosts", self.hosts, "observe"),
            ("GET", r"/api/v1/hosts/(?P<host>[^/]+)/preferences", self.host_preferences, "observe"),
            ("GET", r"/api/v1/sessions", self.sessions, "observe"),
            ("GET", r"/api/v1/sessions/(?P<host>[^/]+)/(?P<sid>[^/]+)", self.session, "observe"),
            ("GET", r"/api/v1/sessions/(?P<host>[^/]+)/(?P<sid>[^/]+)/messages", self.messages, "observe"),
            ("GET", r"/api/v1/sessions/(?P<host>[^/]+)/(?P<sid>[^/]+)/wait", self.session_wait, "observe"),
            ("GET", r"/api/v1/sessions/(?P<host>[^/]+)/(?P<sid>[^/]+)/history", self.session_history, "observe"),
            ("GET", r"/api/v1/sessions/(?P<host>[^/]+)/(?P<sid>[^/]+)/relations", self.session_relations, "observe"),
            ("GET", r"/api/v1/sessions/(?P<host>[^/]+)/(?P<sid>[^/]+)/instructions", self.session_instructions, "observe"),
            ("GET", r"/api/v1/hosts/(?P<host>[^/]+)/discovery", self.discovery, "observe"),
            ("GET", r"/api/v1/worktrees/(?P<wid>wt_[0-9a-f]{32})", self.worktree, "observe"),
            ("GET", r"/api/v1/worktrees/(?P<wid>wt_[0-9a-f]{32})/history", self.worktree_history, "observe"),
            ("GET", r"/api/v1/worktrees/(?P<wid>wt_[0-9a-f]{32})/relations", self.worktree_relations, "observe"),
            ("GET", r"/api/v1/tasks/(?P<task>[0-9a-f-]{8,64})/history", self.task_history, "observe"),
            ("GET", r"/api/v1/tasks/(?P<task>[0-9a-f-]{8,64})/sessions", self.task_sessions, "observe"),
            ("GET", r"/api/v1/policy", self.policy, "observe"),
            ("GET", r"/api/v1/connections", self.connections, "observe"),
            ("POST", r"/api/v1/connections/preview", self.connections_preview, None),
            ("POST", r"/api/v1/connections/receipt", self.connections_receipt, "manage"),
            ("POST", r"/api/v1/connections/handoff", self.connections_handoff, "manage"),
            ("GET", r"/api/v1/operations", self.operations, "observe"),
            ("POST", r"/api/v1/operations", self.create_operation, None),
            ("GET", r"/api/v1/operations/(?P<op>op_[0-9a-f]{32})", self.operation, "observe"),
            ("POST", r"/api/v1/operations/(?P<op>op_[0-9a-f]{32})/cancel", self.cancel_operation, None),
            ("POST", r"/api/v1/operations/(?P<op>op_[0-9a-f]{32})/resume", self.resume_operation, None),
            ("GET", r"/api/v1/events", self.events, "observe"),
            ("GET", r"/api/v1/tasks/(?P<task>[0-9a-f-]{8,64})", self.task, "observe"),
            ("GET", r"/api/v1/checkpoints", self.checkpoints, "observe"),
            ("GET", r"/api/v1/sessions/(?P<host>[^/]+)/(?P<sid>[^/]+)/checkpoint-preview", self.checkpoint_preview,
             "observe"),
            ("GET", r"/api/v1/checkpoints/(?P<cp>cp_[0-9a-f]{32})", self.checkpoint, "observe"),
            ("GET", r"/api/v1/repositories/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pulls",
             self.pulls_list, "observe"),
            ("GET", r"/api/v1/repositories/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pulls/(?P<number>\d{1,9})",
             self.pull_preview, "observe"),
            ("GET", r"/api/v1/delivery/previews/(?P<pv>mpv_[0-9a-f]{32})", self.merge_preview, "observe"),
            ("GET", r"/api/v1/deployments/preview", self.deployment_preview, "observe"),
            ("GET", r"/api/v1/deployments", self.deployments, "observe"),
            ("GET", r"/api/v1/deployments/(?P<dep>dep_[0-9a-f]{32})", self.deployment, "observe"),
            ("GET", r"/api/v1/deployment-environments", self.deployment_environment, "observe"),
            ("GET", r"/api/v1/deployment-environments/history", self.deployment_environment_history, "observe"),
            ("GET", r"/api/v1/integrations/candidates", self.integration_candidates, "observe"),
            ("GET", r"/api/v1/integrations/previews/(?P<pv>ipv_[0-9a-f]{32})", self.integration_preview, "observe"),
            ("GET", r"/api/v1/integrations", self.integrations, "observe"),
            ("GET", r"/api/v1/integrations/(?P<op>op_[0-9a-f]{32})", self.integration, "observe"),
            ("GET", r"/api/v1/projects", self.projects, "observe"),
            ("GET", r"/api/v1/projects/(?P<prj>prj_[0-9a-f]{20})", self.project, "observe"),
            ("GET", r"/api/v1/projects/(?P<prj>prj_[0-9a-f]{20})/skills", self.project_skills, "observe"),
            ("GET", r"/api/v1/projects/(?P<prj>prj_[0-9a-f]{20})/repair-evidence", self.repair_evidence, "observe"),
            ("GET", r"/api/v1/work-items/(?P<wid>wi_[0-9a-f]{20})/repair", self.work_item_repair, "observe"),
            ("GET", r"/api/v1/work-items/(?P<wid>wi_[0-9a-f]{20})/result-sources", self.work_item_results, "observe"),
            ("GET", r"/api/v1/work-items", self.work_items, "observe"),
            ("GET", r"/api/v1/work-items/(?P<wi>wi_[0-9a-f]{20})", self.work_item, "observe"),
        ]

    # ------------------------------------------------------------------ plumbing
    def _cors(self, headers: dict[str, str]) -> str:
        """CORS headers only for an origin listed in [api] allowed_origins (the Dashboard itself is same-origin)."""
        origin = headers.get("origin")
        if not origin or origin not in self.allowed_origins:
            return ""
        return f"Access-Control-Allow-Origin: {origin}\r\nVary: Origin\r\n"

    async def handle(self, method: str, target: str, headers: dict[str, str], reader, writer) -> None:
        cors = ""
        try:
            if not _host_is_loopback(headers.get("host", "")):
                raise ApiError(400, "BAD_HOST", "Host must be a loopback address")
            parts = urlsplit(target)
            path, query = parts.path.rstrip("/") or "/", parse_qs(parts.query)
            browser = getattr(self, "browser_sessions", None)
            if browser and path == "/api/v1/browser-handoff" and method == "POST":
                await browser.consume(headers, reader, writer)
                return
            if browser and path == "/api/v1/managed/identity" and method == "GET":
                payload = self.daemon.managed_proof(self._q(query, "challenge", ""), headers.get("host"))
                await self._send_json(writer, 200, payload)
                return
            origin = headers.get("origin")
            if origin and not _origin_ok(origin, self.allowed_origins):
                raise ApiError(403, "BAD_ORIGIN", "origin is not allowed")
            cors = self._cors(headers)
            if method == "OPTIONS":  # preflight: no token is sent, so nothing is decided here but CORS
                if not cors:
                    raise ApiError(403, "BAD_ORIGIN", "cross-origin requests need [api] allowed_origins")
                writer.write((
                    "HTTP/1.1 204 No Content\r\n" + cors
                    + "Access-Control-Allow-Methods: GET, POST\r\n"
                    "Access-Control-Allow-Headers: Authorization, Content-Type, Idempotency-Key, Last-Event-ID\r\n"
                    "Access-Control-Max-Age: 600\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").encode())
                with contextlib.suppress(Exception):
                    await writer.drain()
                return
            token = self._bearer(headers)
            browser_session = False
            if not token and browser:
                token = browser.credential(method, headers,
                    bootstrap=method == "GET" and path == "/api/v1/browser-session")
                browser_session = bool(token)
            content = re.fullmatch(r"/api/v1/artifacts/uploads/(?P<op>op_[0-9a-f]{32})/content", path)
            download = re.fullmatch(r"/api/v1/artifacts/(?P<aid>art_[0-9a-f]{32})/revisions/(?P<revision>[1-9][0-9]*)/content", path)
            if content or download:
                principal = api_auth.authenticate(self.daemon.journal.db, token, self.daemon._admin_token,
                                                  browser_session=browser_session)
                if principal is None:
                    raise ApiError(401, "UNAUTHORIZED", "a valid bearer token is required")
                if content:
                    if method != "POST":
                        raise ApiError(405, "METHOD_NOT_ALLOWED", "upload content needs POST")
                    if headers.get("transfer-encoding") or headers.get("content-type") != "application/octet-stream":
                        raise ApiError(422, "INVALID_REQUEST", "use Content-Length and application/octet-stream; chunked transfer is refused")
                    length = headers.get("content-length", "")
                    if not length.isdigit():
                        raise ApiError(400, "BAD_LENGTH", "declared Content-Length is required")
                    op = await self.daemon.artifact_store.receive(principal, content["op"], reader, int(length))
                    await self._send_json(writer, 202, {"operation": op}, cors)
                else:
                    if method != "GET":
                        raise ApiError(405, "METHOD_NOT_ALLOWED", "download content needs GET")
                    if not principal.allows("observe"):
                        raise ApiError(403, "FORBIDDEN", "download needs observe scope")
                    from .pm_source_lineage import require
                    require(self.daemon.journal.db, principal, "artifact", download["aid"])
                    row = artifacts.get(self.daemon.journal.db, download["aid"], int(download["revision"]))
                    data = self.daemon.artifact_store.read_content(row["artifact_id"], row["revision"])
                    head = (f"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: {len(data)}\r\n"
                            f"Content-Disposition: attachment; filename*=UTF-8''{quote(row['display_name'], safe='')}\r\n"
                            f"X-Content-Type-Options: nosniff\r\nCache-Control: no-store\r\n{cors}Connection: close\r\n\r\n")
                    writer.write(head.encode() + data)
                    await writer.drain()
                return
            body = await self._read_body(method, headers, reader,
                max_body=CONNECTION_BOUNDS["receipt_bytes"] if path == "/api/v1/connections/receipt" else (
                    CONNECTION_BOUNDS["handoff_bytes"] if path == "/api/v1/connections/handoff" else MAX_BODY), path=path)
            if (method == "POST" and path != "/api/v1/managed/stop"
                    and getattr(self.daemon, "managed_stopping", False)):
                raise ApiError(503, "SERVICE_STOPPING", "owned service is stopping; reopen Dashboard to reconnect")
            principal = api_auth.authenticate(self.daemon.journal.db, token, self.daemon._admin_token,
                                              browser_session=browser_session)
            if browser and path in {"/api/v1/browser-session", "/api/v1/browser-session/logout",
                                    "/api/v1/managed/browser-file", "/api/v1/managed/stop"}:
                if principal is None:
                    raise ApiError(401, "UNAUTHORIZED", "open Dashboard from the desktop menu")
                if path == "/api/v1/browser-session" and method == "GET":
                    payload = browser.describe(token)
                elif path == "/api/v1/browser-session/logout" and method == "POST":
                    browser.describe(token)  # only a browser credential can be signed out here
                    browser.revoke(token)
                    payload = {"signed_out": True}
                elif path == "/api/v1/managed/browser-file" and method == "POST" and self._bearer(headers):
                    if principal.actor != self.daemon.managed_installation["actor"]:
                        raise ApiError(403, "FORBIDDEN", "browser entry belongs to the installation owner")
                    payload = browser.create_handoff(principal)
                elif path == "/api/v1/managed/stop" and method == "POST" and self._bearer(headers):
                    if principal.actor != self.daemon.managed_installation["actor"]:
                        raise ApiError(403, "FORBIDDEN", "service belongs to the installation owner")
                    if getattr(self.daemon, "managed_stopping", False):
                        # Same authenticated owner, same accepted effect. Do not
                        # re-run busy admission or signal shutdown a second time.
                        await self._send_json(writer, 200, {"protocol": 1, "stopped": True})
                        return
                    db = self.daemon.journal.db
                    if db.execute("SELECT 1 FROM operations WHERE status NOT IN ('succeeded','failed','cancelled') LIMIT 1").fetchone():
                        raise ApiError(409, "SERVICE_BUSY", "finish or reconcile current operations before stopping")
                    if db.execute("SELECT 1 FROM tasks WHERE state NOT IN ('done','failed') LIMIT 1").fetchone():
                        raise ApiError(409, "SERVICE_BUSY", "finish or pause service shutdown until current tasks settle")
                    if self.daemon._active_ticks or self.daemon.ops._active:
                        raise ApiError(409, "SERVICE_BUSY", "current work is still running")
                    payload = {"protocol": 1, "stopped": True}
                    self.daemon.managed_stopping = True
                    # Reply is written before the ordinary service shutdown cancels background loops.
                    asyncio.get_running_loop().call_later(0.1, self.daemon.managed_stop.set)
                else:
                    raise ApiError(405, "METHOD_NOT_ALLOWED", "method not allowed")
                await self._send_json(writer, 200, payload)
                return
            if principal is None and method == "POST" and path == "/api/v1/operations":
                principal = self.daemon.capability_principal(
                    token, body.get("action"), body.get("target") or {},
                    headers.get("idempotency-key") or body.get("idempotency_key"))
            if path == "/api/v1/events/stream" and method == "GET":
                if principal is None:
                    raise ApiError(401, "UNAUTHORIZED", "a valid bearer token is required")
                if not principal.allows("observe"):
                    raise ApiError(403, "FORBIDDEN", "this route needs the 'observe' scope")
                await self._stream(query, headers, reader, writer, token=token, principal=principal, cors=cors,
                                   browser_session=browser_session)
                return
            fn, kwargs, scope = self._route(method, path)
            if fn != self.version:
                if principal is None:
                    raise ApiError(401, "UNAUTHORIZED", "a valid bearer token is required")
                if scope and not principal.allows(scope):
                    raise ApiError(403, "FORBIDDEN", f"this route needs the {scope!r} scope")
            if fn in {self.messages, self.session_wait}:
                from .session_observation import authorizer
                query = parse_qs(parts.query, keep_blank_values=True)
                kwargs.update(reader=reader, writer=writer,
                              check_authorization=authorizer(self.daemon, token, principal, browser_session=browser_session))
            if fn in {self.connections, self.operation, self.project, self.repair_evidence, self.work_item_repair, self.session_instructions,
                      self.host_preferences, self.project_skills, self.work_item_results,
                      self.pm_governance_records, self.pm_governance_record, self.pm_governance_history,
                      self.pm_follow_ups, self.pm_follow_up, self.pm_follow_up_latest,
                      self.pm_follow_up_assessments, self.pm_follow_up_assessment,
                      self.pm_overview_assessments, self.pm_overview_assessment,
                      self.pm_conversation_proposals, self.pm_conversation_proposal,
                      self.pm_brief_export, self.pm_work_deliveries, self.pm_context_bundle,
                      self.pm_lifecycle, self.pm_lifecycle_transitions, self.pm_lifecycle_transition, self.pm_lifecycle_inflight,
                      self.pm_issue_mappings, self.pm_issue_mapping,
                      self.pm_execution_verification_configuration, self.pm_execution_verification_get,
                      self.pm_maintenance_repairs, self.pm_maintenance_repair_current, self.pm_maintenance_repair, self.pm_quality_reviews, self.pm_quality_review, self.pm_work_quality, self.pm_pr_publication,
                      self.pm_work_batches, self.pm_work_batch, self.pm_maintenance_findings_current,
                      self.pm_spec_materializations, self.pm_spec_materialization,
                      self.pm_work_packages, self.pm_work_package, self.pm_work_package_readiness,
                      self.pm_work_profiles, self.pm_work_profile, self.pm_work_profile_readiness,
                      self.pm_maintenance_reviews, self.pm_maintenance_review_current, self.pm_maintenance_review, self.pm_maintenance_review_events,
                      self.pm_maintenance_checks, self.pm_maintenance_check_current, self.pm_maintenance_check, self.pm_maintenance_schedule,
                      self.pm_ahp_adapters, self.pm_ahp_observations, self.pm_ahp_observation_current, self.pm_ahp_observation,
                      self.pm_overview, self.pm_dependencies, self.pm_dependency_revisions, self.pm_dependency_impact,
                      self.pm_history_sources, self.pm_history, self.pm_resources, self.pm_register, self.pm_register_row, self.pm_schedule, self.pm_github_observations, self.pm_github_observation_current, self.pm_github_observation,
                      self.pm_workload_snapshots, self.pm_workload_snapshot_current, self.pm_workload_snapshot,
                      self.pm_maintenance_policies, self.pm_maintenance_policy_current, self.pm_maintenance_policy,
                      self.pm_release_baselines, self.pm_release_baseline_current, self.pm_release_baseline,
                      self.pm_release_baseline_sources, self.pm_release_baseline_releases,
                      self.pm_release_baseline_deployments, self.pm_release_baseline_preview,
                      self.pm_work_adopt_and_start_preview, self.pm_work_start_preview, self.pm_work_attempts, self.pm_work_attempt, self.pm_work_inputs,
                      self.pm_work_attempt_result, self.pm_work_attempt_changes, self.pm_work_attempt_diff,
                      self.pm_spec_sources, self.pm_spec_source_capabilities}:
                # Empty or repeated selectors cannot silently become defaults.
                query = parse_qs(parts.query, keep_blank_values=True)
            from . import controller_authority
            authority = controller_authority.binding(self.daemon.journal.db, principal) if principal else None
            with controller_authority.use(authority, self.daemon.journal):
                from .pm_source_lineage import http_selection, read_request, read_response
                # Commands retain their durable admission/replay classification.
                # Read selectors and final responses always use current sources.
                if method == "GET":
                    read_request(self.daemon.journal.db, principal, http_selection(fn.__name__, kwargs, query, body))
                status, payload = await fn(principal=principal, query=query, body=body, headers=headers, **kwargs)
                payload = read_response(self.daemon.journal.db, principal, payload)
        except dashboard_sync.ResetRequired as e:
            status, payload = 409, e.document()
        except ApiError as e:
            status, payload = e.status, {"error": {"code": e.code, "message": e.message}}
        except OperationError as e:
            status, payload = e.status, {"error": {"code": e.code, "message": e.message}}
        except ResourceReadOnly as e:
            status, payload = 403, {"error": {"code": e.code, "message": str(e)}}
        except (ValueError, TypeError) as e:
            status, payload = 422, {"error": {"code": "INVALID_REQUEST", "message": str(e)[:300]}}
        except KeyError:
            status, payload = 404, {"error": {"code": "NOT_FOUND", "message": "not found"}}
        except BatError as e:
            status, payload = 502, {"error": {"code": "BAT_ERROR", "message": service._err(e)[:300]}}
        except Exception as e:  # noqa: BLE001 - never echo internals
            status, payload = 500, {"error": {"code": "INTERNAL", "message": type(e).__name__}}
        await self._send_json(writer, status, payload, cors)

    async def dashboard(self, method: str, target: str, headers: dict[str, str], writer) -> None:
        """Static Dashboard files. They hold no data and need no token; the API calls they make do."""
        path = urlsplit(target).path
        extra = ""
        body = b""
        if not _host_is_loopback(headers.get("host", "")):
            status, ctype, body = 400, "text/plain; charset=utf-8", b"Host must be a loopback address\n"
        elif method not in {"GET", "HEAD"}:
            status, ctype, extra = 405, "text/plain; charset=utf-8", "Allow: GET, HEAD\r\n"
        elif path in {"/", "/dashboard"}:
            status, ctype, extra = 302, "text/plain; charset=utf-8", "Location: /dashboard/\r\n"
        else:
            name = path[len("/dashboard/"):] or "index.html"
            if name in DASHBOARD_FILES:
                status, ctype = 200, DASHBOARD_FILES[name]
                if name == "app.js":
                    files, digest = _dashboard_snapshot()
                    body = files[name] if files else (resources.files(__package__) / "dashboard" / name).read_bytes()
                    if digest is not None:
                        body = body.replace(_FRONTEND_PLACEHOLDER, digest.encode("ascii"), 1)
                else:
                    body = (resources.files(__package__) / "dashboard" / name).read_bytes()
            else:
                status, ctype, body = 404, "text/plain; charset=utf-8", b"not found\n"
        referrer_policy = "same-origin" if getattr(self, "browser_sessions", None) else "no-referrer"
        head = (f"HTTP/1.1 {status} {_REASONS[status]}\r\n"
                f"Content-Type: {ctype}\r\nContent-Length: {len(body)}\r\n{extra}"
                f"Cache-Control: no-cache\r\nContent-Security-Policy: {DASHBOARD_CSP}\r\n"
                f"X-Frame-Options: DENY\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: {referrer_policy}\r\n"
                "Cross-Origin-Opener-Policy: same-origin\r\nConnection: close\r\n\r\n")
        writer.write(head.encode() + (b"" if method == "HEAD" else body))
        with contextlib.suppress(Exception):
            await writer.drain()

    @staticmethod
    def _bearer(headers: dict[str, str]) -> str:
        auth = headers.get("authorization", "")
        return auth[7:].strip() if auth.startswith("Bearer ") else ""

    @staticmethod
    async def _read_body(method: str, headers: dict[str, str], reader, *, max_body=MAX_BODY, path=None) -> dict:
        if method != "POST":
            return {}
        raw_length = headers.get("content-length", "0").strip()
        if not raw_length.isdigit():
            raise ApiError(400, "BAD_LENGTH", "invalid Content-Length")
        length = int(raw_length)
        if length > max_body:
            raise ApiError(413, "TOO_LARGE", "request body is too large")
        if length == 0:
            return {}
        try:
            raw = await asyncio.wait_for(reader.readexactly(length), 10)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            raise ApiError(400, "BAD_LENGTH", "the body is shorter than its Content-Length") from None
        try:
            body = json.loads(raw)
        except ValueError:
            raise ApiError(400, "BAD_JSON", "request body must be JSON") from None
        if not isinstance(body, dict):
            raise ApiError(422, "INVALID_REQUEST", "request body must be a JSON object")
        code = {"/api/v1/connections/handoff": "INVALID_CONNECTION_HANDOFF",
            "/api/v1/connections/receipt": "INVALID_CONNECTION_RECEIPT",
            "/api/v1/connections/preview": "INVALID_PARAMS"}.get(path)
        if path in {"/api/v1/operations", "/api/v1/connections/preview"} and body.get("action") == "setup.connection.apply":
            code = "INVALID_NATIVE_PLAN"
        return decode_native_json(raw, code=code) if code else body

    def _route(self, method: str, path: str):
        allowed = False
        for m, pattern, fn, scope in self.routes:
            match = re.fullmatch(pattern, path)
            if match:
                if m == method:
                    return fn, {k: unquote(v) for k, v in match.groupdict().items()}, scope
                allowed = True
        if allowed:
            raise ApiError(405, "METHOD_NOT_ALLOWED", "method not allowed")
        raise ApiError(404, "NOT_FOUND", "no such route")

    @staticmethod
    async def _send_json(writer, status: int, payload: dict, cors: str = "") -> None:
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode()
        head = (f"HTTP/1.1 {status} {_REASONS.get(status, 'Error')}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(raw)}\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n"
                f"{cors}Connection: close\r\n\r\n")
        writer.write(head.encode() + raw)
        with contextlib.suppress(Exception):
            await writer.drain()

    @staticmethod
    def _q(query: dict, name: str, default=None):
        values = query.get(name)
        return values[-1] if values else default

    @staticmethod
    def _exact_query(query, allowed):
        if set(query) - set(allowed) or any(
            len(values) != 1 or not values[0] or len(values[0]) > 2048
            or any(ord(char) < 32 or ord(char) == 127 for char in values[0])
            for values in query.values()
        ):
            raise ApiError(422, "INVALID_REQUEST", "use one nonempty value for each supported query field")

    def _int(self, query: dict, name: str, default: int) -> int:
        try:
            return int(self._q(query, name, default))
        except (TypeError, ValueError):
            raise ApiError(422, "INVALID_REQUEST", f"{name} must be an integer") from None

    def _bool(self, query: dict, name: str):
        value = self._q(query, name)
        if value is None:
            return None
        if value.lower() in {"1", "true", "yes"}:
            return True
        if value.lower() in {"0", "false", "no"}:
            return False
        raise ApiError(422, "INVALID_REQUEST", f"{name} must be true or false")

    # ------------------------------------------------------------------ routes
    async def version(self, **_):
        return 200, {"connector": __version__, "api_version": API_VERSION, "contract_version": CONTRACT_VERSION}

    async def workspaces(self, principal, query, **_):
        request = {"host": self._q(query, "host")}
        limit = self._q(query, "limit")
        if limit is not None:
            try:
                request["limit"] = int(limit)
            except ValueError:
                raise OperationError("INVALID_PARAMS", "limit must be 1-200", 422) from None
        return 200, await session_start_operations.workspaces(self.daemon.ops, principal, request)

    def _pm_read(self, principal, method, query, **ids):
        from .pm_reads import FIELDS, read
        self._exact_query(query, set(FIELDS[method]) - set(ids))
        params = {key: values[0] for key, values in query.items()}
        if method == 'pm_work_package_readiness_get' and 'quality_refs' in params:
            encoded = params['quality_refs']
            if len(encoded.encode('utf-8')) > 16_384:
                raise OperationError('INVALID_PARAMS', 'Original quality selectors exceed the read bound', 422)
            try:
                params['quality_refs'] = json.loads(encoded)
            except (ValueError, RecursionError):
                raise OperationError('INVALID_PARAMS', 'Original quality selectors must be a JSON object', 422) from None
            if not isinstance(params['quality_refs'], dict):
                raise OperationError('INVALID_PARAMS', 'Original quality selectors must be a JSON object', 422)
        integer_fields = {"limit", "max_chars", "before_message", "before_input", "journal_cursor", "history_limit"}
        if method == "pm_work_profiles_list":
            integer_fields.add("cursor")
        if method in {"pm_plans_list", "pm_projects_list", "pm_work_list", "pm_evidence_list", "pm_snapshots_list", "pm_roadmap_revisions_list", "pm_sprint_artifact_revisions_list", "pm_sprints_list", "pm_sprint_history_list", "pm_kanban_get", "pm_meetings_list", "pm_meeting_history_list", "pm_governance_list", "pm_governance_history_list", "pm_follow_up_list", "pm_follow_up_assessments_list", "pm_work_packages_list", "pm_issue_mappings_list", "pm_release_baselines_list", "pm_maintenance_policies_list", "pm_workload_snapshots_list"}:
            integer_fields.add("after")
        wide_cursor = method in {"pm_history_get", "pm_dependencies_revisions_list", "pm_conversation_proposals_list", "pm_maintenance_repairs_list", "pm_quality_reviews_list", "pm_work_quality_get", "pm_work_deliveries_list", "pm_work_batches_list", "pm_work_attempts_list", "pm_work_inputs_list", "pm_spec_materializations_list", "pm_lifecycle_transitions_list", "pm_lifecycle_inflight_list",
                                 "pm_workload_snapshots_list", "pm_ahp_observations_list", "pm_github_observations_list", "pm_maintenance_checks_list", "pm_maintenance_reviews_list", "pm_maintenance_review_events_list"}
        if wide_cursor:
            integer_fields.add("after")
        for key in integer_fields & params.keys():
            value = params[key]
            pattern = r"[0-9]{1,19}" if wide_cursor and key == "after" else r"[0-9]{1,9}"
            if not re.fullmatch(pattern, value):
                raise OperationError("INVALID_PARAMS", f"{key} must be a positive integer", 422)
            params[key] = int(value)
        if method == "pm_plan_compare" and "binding_ids" in params:
            params["binding_ids"] = params["binding_ids"].split(",") if params["binding_ids"] else []
        return 200, read(self.daemon.ops, principal, method, {**params, **ids})

    async def pm_workspaces(self, principal, query, **_):
        return self._pm_read(principal, "pm_workspaces_list", query)

    async def _pm_spec_read(self, principal, method, query, workspace_id):
        from .pm_spec_reads import FIELDS, read
        self._exact_query(query, set(FIELDS[method]) - {"workspace_id"})
        params = {key: values[0] for key, values in query.items()}
        if "paths" in params:
            params["paths"] = params["paths"].split(",")
        return 200, await read(self.daemon.ops, principal, method, {**params, "workspace_id": workspace_id})

    async def pm_spec_sources(self, principal, query, workspace_id, **_):
        return await self._pm_spec_read(principal, "pm_spec_sources_get", query, workspace_id)

    async def pm_spec_source_capabilities(self, principal, query, workspace_id, **_):
        return await self._pm_spec_read(principal, "pm_spec_source_capabilities_get", query, workspace_id)

    async def pm_workspace(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_workspace_get", query, workspace_id=workspace_id)

    async def pm_profiles(self, principal, query, **_):
        return self._pm_read(principal, "pm_profiles_get", query)

    async def pm_threads(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_threads_list", query, workspace_id=workspace_id)

    async def pm_thread(self, principal, query, workspace_id, thread_id, **_):
        return self._pm_read(principal, "pm_thread_get", query, workspace_id=workspace_id, thread_id=thread_id)

    async def pm_context(self, principal, query, workspace_id, thread_id, **_):
        return self._pm_read(principal, "pm_context_get", query, workspace_id=workspace_id, thread_id=thread_id)

    async def pm_context_bundle(self, principal, query, workspace_id, thread_id, **_):
        return self._pm_read(principal, "pm_context_bundle_preview", query, workspace_id=workspace_id, thread_id=thread_id)

    async def pm_runs(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_runs_list", query, workspace_id=workspace_id)

    async def pm_run(self, principal, query, workspace_id, run_id, **_):
        return self._pm_read(principal, "pm_run_get", query, workspace_id=workspace_id, run_id=run_id)

    async def pm_installation_capabilities(self, principal, query, **_):
        from . import pm_native_capabilities
        self._exact_query(query, set())
        return 200, {"native_analysis": pm_native_capabilities.installation(self.daemon.ops),
                     "workspace_required": True}

    async def pm_capabilities(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_capabilities_get", query, workspace_id=workspace_id)

    async def pm_plans(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_plans_list", query, workspace_id=workspace_id)

    async def pm_plan(self, principal, query, workspace_id, revision_id, **_):
        return self._pm_read(principal, "pm_plan_get", query, workspace_id=workspace_id, revision_id=revision_id)

    async def pm_plan_accepted(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_plan_accepted_get", query, workspace_id=workspace_id)

    async def pm_plan_head(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_plan_head_get", query, workspace_id=workspace_id)

    async def pm_projects(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_projects_list", query, workspace_id=workspace_id)

    async def pm_work(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_work_list", query, workspace_id=workspace_id)

    async def pm_evidence(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_evidence_list", query, workspace_id=workspace_id)

    async def pm_evidence_detail(self, principal, query, workspace_id, evidence_id, **_):
        return self._pm_read(principal, "pm_evidence_get", query, workspace_id=workspace_id, evidence_id=evidence_id)

    async def pm_capture(self, principal, query, workspace_id, capture_id, **_):
        return self._pm_read(principal, "pm_capture_get", query, workspace_id=workspace_id, capture_id=capture_id)

    async def pm_capture_latest(self, principal, query, workspace_id, work_item_id, **_):
        return self._pm_read(principal, "pm_capture_latest_get", query, workspace_id=workspace_id, work_item_id=work_item_id)

    async def pm_plan_comparison(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_plan_compare", query, workspace_id=workspace_id)

    async def pm_kanban(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_kanban_get", query, workspace_id=workspace_id)

    async def pm_kanban_card(self, principal, query, workspace_id, work_item_id, **_):
        return self._pm_read(principal, "pm_kanban_card_get", query, workspace_id=workspace_id, work_item_id=work_item_id)

    async def pm_governance_records(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_governance_list", query, workspace_id=workspace_id)

    async def pm_lifecycle(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_lifecycle_get", query, workspace_id=workspace_id)

    async def pm_lifecycle_transitions(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_lifecycle_transitions_list", query, workspace_id=workspace_id)

    async def pm_lifecycle_transition(self, principal, query, workspace_id, transition_id, **_):
        return self._pm_read(principal, "pm_lifecycle_transition_get", query, workspace_id=workspace_id, transition_id=transition_id)

    async def pm_lifecycle_inflight(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_lifecycle_inflight_list", query, workspace_id=workspace_id)
    async def pm_issue_mappings(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_issue_mappings_list", query, workspace_id=workspace_id)

    async def pm_issue_mapping(self, principal, query, workspace_id, mapping_id, **_):
        return self._pm_read(principal, "pm_issue_mapping_get", query, workspace_id=workspace_id, mapping_id=mapping_id)

    async def _pm_release_source_read(self, principal, method, query, workspace_id):
        from .pm_release_previews import FIELDS, read
        self._exact_query(query, FIELDS[method] - {"workspace_id"})
        params = {key: values[0] for key, values in query.items()}
        for key in {"after", "limit", "page", "release_id"} & params.keys():
            if not re.fullmatch(r"[0-9]{1,19}" if key == "release_id" else r"[0-9]{1,9}", params[key]):
                raise OperationError("INVALID_PARAMS", f"{key} must be a bounded integer", 422)
            params[key] = int(params[key])
        return 200, await read(self.daemon.ops, principal, method, {**params, "workspace_id": workspace_id})

    async def pm_release_baseline_sources(self, principal, query, workspace_id, **_):
        return await self._pm_release_source_read(principal, "pm_release_baseline_sources_list", query, workspace_id)

    async def pm_release_baseline_releases(self, principal, query, workspace_id, **_):
        return await self._pm_release_source_read(principal, "pm_release_baseline_releases_list", query, workspace_id)

    async def pm_release_baseline_deployments(self, principal, query, workspace_id, **_):
        return await self._pm_release_source_read(principal, "pm_release_baseline_deployments_list", query, workspace_id)

    async def pm_release_baseline_preview(self, principal, query, workspace_id, **_):
        return await self._pm_release_source_read(principal, "pm_release_baseline_preview", query, workspace_id)

    async def pm_work_adopt_and_start_preview(self, principal, query, body, workspace_id, thread_id, **_):
        from . import pm_work_adopt_and_start
        self._exact_query(query, set())
        return 200, await pm_work_adopt_and_start.preview(self.daemon.ops, principal, workspace_id, thread_id, body)

    async def pm_work_start_preview(self, principal, query, body, workspace_id, work_item_id, **_):
        from . import pm_work_start
        self._exact_query(query, set())
        return 200, await pm_work_start.preview(self.daemon.ops, principal, workspace_id, work_item_id, body)

    async def pm_work_attempts(self, principal, query, workspace_id, work_item_id, **_):
        return self._pm_read(principal, "pm_work_attempts_list", query, workspace_id=workspace_id, work_item_id=work_item_id)

    async def pm_work_inputs(self, principal, query, workspace_id, work_item_id, **_):
        return self._pm_read(principal, "pm_work_inputs_list", query, workspace_id=workspace_id, work_item_id=work_item_id)

    async def pm_work_attempt(self, principal, query, workspace_id, attempt_id, **_):
        return self._pm_read(principal, "pm_work_attempt_get", query, workspace_id=workspace_id, attempt_id=attempt_id)

    async def _pm_attempt_result_read(self, principal, method, query, workspace_id, attempt_id):
        from .pm_attempt_result_reads import FIELDS, read
        self._exact_query(query, set(FIELDS[method]) - {"workspace_id", "attempt_id"})
        params = {key: values[0] for key, values in query.items()}
        for key in {"cursor", "wait_ms", "max_commits", "max_files"} & params.keys():
            pattern = r"[0-9]{1,19}" if key == "cursor" else r"[0-9]{1,9}"
            if not re.fullmatch(pattern, params[key]):
                raise OperationError("INVALID_PARAMS", f"{key} must be a bounded integer", 422)
            params[key] = int(params[key])
        return 200, await read(self.daemon.ops, principal, method,
                               {**params, "workspace_id": workspace_id, "attempt_id": attempt_id})

    async def pm_work_attempt_result(self, principal, query, workspace_id, attempt_id, **_):
        return await self._pm_attempt_result_read(principal, "pm_work_attempt_result_get", query, workspace_id, attempt_id)

    async def pm_work_attempt_changes(self, principal, query, workspace_id, attempt_id, **_):
        return await self._pm_attempt_result_read(principal, "pm_work_attempt_changes_get", query, workspace_id, attempt_id)

    async def pm_work_attempt_diff(self, principal, query, workspace_id, attempt_id, **_):
        return await self._pm_attempt_result_read(principal, "pm_work_attempt_diff_get", query, workspace_id, attempt_id)

    async def pm_work_deliveries(self, principal, query, workspace_id, work_item_id, **_):
        return self._pm_read(principal, "pm_work_deliveries_list", query, workspace_id=workspace_id, work_item_id=work_item_id)

    async def pm_maintenance_repairs(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_maintenance_repairs_list", query, workspace_id=workspace_id)

    async def pm_maintenance_repair_current(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_maintenance_repair_current_get", query, workspace_id=workspace_id)

    async def pm_maintenance_repair(self, principal, query, workspace_id, repair_id, **_):
        return self._pm_read(principal, "pm_maintenance_repair_get", query, workspace_id=workspace_id, repair_id=repair_id)

    async def pm_execution_verification_configuration(self, principal, query, workspace_id, execution_operation_id, **_):
        from .pm_execution_reads import read
        self._exact_query(query, set())
        return 200, await read(self.daemon.ops, principal, 'pm_execution_verification_configuration',
            {'workspace_id': workspace_id, 'execution_operation_id': execution_operation_id})

    async def pm_execution_verification_get(self, principal, query, workspace_id, verification_operation_id, **_):
        from .pm_execution_reads import read
        self._exact_query(query, set())
        return 200, await read(self.daemon.ops, principal, 'pm_execution_verification_get',
            {'workspace_id': workspace_id, 'verification_operation_id': verification_operation_id})

    async def pm_pr_publication(self, principal, query, workspace_id, publication_operation_id, **_):
        return self._pm_read(principal, 'pm_pr_publication_get', query, workspace_id=workspace_id, publication_operation_id=publication_operation_id)

    async def pm_quality_reviews(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_quality_reviews_list", query, workspace_id=workspace_id)

    async def pm_quality_review(self, principal, query, workspace_id, review_id, **_):
        return self._pm_read(principal, "pm_quality_review_get", query, workspace_id=workspace_id, review_id=review_id)

    async def pm_work_quality(self, principal, query, workspace_id, work_item_id, **_):
        return self._pm_read(principal, "pm_work_quality_get", query, workspace_id=workspace_id, work_item_id=work_item_id)

    async def pm_work_batches(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_work_batches_list", query, workspace_id=workspace_id)

    async def pm_work_batch(self, principal, query, workspace_id, batch_id, **_):
        return self._pm_read(principal, "pm_work_batch_get", query, workspace_id=workspace_id, batch_id=batch_id)

    async def pm_maintenance_findings_current(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_maintenance_findings_current_get", query, workspace_id=workspace_id)

    async def pm_spec_materializations(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_spec_materializations_list", query, workspace_id=workspace_id)

    async def pm_spec_materialization(self, principal, query, workspace_id, materialization_id, **_):
        return self._pm_read(principal, "pm_spec_materialization_get", query, workspace_id=workspace_id, materialization_id=materialization_id)

    async def pm_maintenance_reviews(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_maintenance_reviews_list", query, workspace_id=workspace_id)

    async def pm_maintenance_review_current(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_maintenance_review_current_get", query, workspace_id=workspace_id)

    async def pm_maintenance_review(self, principal, query, workspace_id, review_id, **_):
        return self._pm_read(principal, "pm_maintenance_review_get", query, workspace_id=workspace_id, review_id=review_id)

    async def pm_maintenance_review_events(self, principal, query, workspace_id, review_id, **_):
        return self._pm_read(principal, "pm_maintenance_review_events_list", query, workspace_id=workspace_id, review_id=review_id)

    async def pm_maintenance_checks(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_maintenance_checks_list", query, workspace_id=workspace_id)

    async def pm_maintenance_check_current(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_maintenance_check_current_get", query, workspace_id=workspace_id)

    async def pm_maintenance_check(self, principal, query, workspace_id, run_id, **_):
        return self._pm_read(principal, "pm_maintenance_check_get", query, workspace_id=workspace_id, run_id=run_id)

    async def pm_maintenance_schedule(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_maintenance_schedule_get", query, workspace_id=workspace_id)

    async def pm_ahp_adapters(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_ahp_adapters_list", query, workspace_id=workspace_id)

    async def pm_overview(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_overview_get", query, workspace_id=workspace_id)

    async def pm_dependencies(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_dependencies_get", query, workspace_id=workspace_id)

    async def pm_dependency_revisions(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_dependencies_revisions_list", query, workspace_id=workspace_id)

    async def pm_dependency_impact(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_dependencies_impact_get", query, workspace_id=workspace_id)

    async def pm_history_sources(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_history_sources_list", query, workspace_id=workspace_id)

    async def pm_history(self, principal, query, workspace_id, source_id, **_):
        return self._pm_read(principal, "pm_history_get", query, workspace_id=workspace_id, source_id=source_id)

    async def pm_resources(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_resources_get", query, workspace_id=workspace_id)

    async def pm_schedule(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_schedule_get", query, workspace_id=workspace_id)

    async def pm_register(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_register_list", query, workspace_id=workspace_id)

    async def pm_register_row(self, principal, query, workspace_id, row_id, **_):
        return self._pm_read(principal, "pm_register_get", query, workspace_id=workspace_id, row_id=row_id)

    async def pm_ahp_observations(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_ahp_observations_list", query, workspace_id=workspace_id)

    async def pm_ahp_observation_current(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_ahp_observation_current_get", query, workspace_id=workspace_id)

    async def pm_ahp_observation(self, principal, query, workspace_id, observation_id, **_):
        return self._pm_read(principal, "pm_ahp_observation_get", query, workspace_id=workspace_id, observation_id=observation_id)

    async def pm_github_observations(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_github_observations_list", query, workspace_id=workspace_id)

    async def pm_github_observation_current(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_github_observation_current_get", query, workspace_id=workspace_id)

    async def pm_github_observation(self, principal, query, workspace_id, observation_id, **_):
        return self._pm_read(principal, "pm_github_observation_get", query, workspace_id=workspace_id, observation_id=observation_id)

    async def pm_workload_snapshots(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_workload_snapshots_list", query, workspace_id=workspace_id)

    async def pm_workload_snapshot_current(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_workload_snapshot_current_get", query, workspace_id=workspace_id)

    async def pm_workload_snapshot(self, principal, query, workspace_id, snapshot_id, **_):
        return self._pm_read(principal, "pm_workload_snapshot_get", query, workspace_id=workspace_id, snapshot_id=snapshot_id)

    async def pm_maintenance_policies(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_maintenance_policies_list", query, workspace_id=workspace_id)

    async def pm_maintenance_policy_current(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_maintenance_policy_current_get", query, workspace_id=workspace_id)

    async def pm_maintenance_policy(self, principal, query, workspace_id, policy_id, **_):
        return self._pm_read(principal, "pm_maintenance_policy_get", query, workspace_id=workspace_id, policy_id=policy_id)

    async def pm_release_baselines(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_release_baselines_list", query, workspace_id=workspace_id)

    async def pm_release_baseline_current(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_release_baseline_current_get", query, workspace_id=workspace_id)

    async def pm_release_baseline(self, principal, query, workspace_id, baseline_id, **_):
        return self._pm_read(principal, "pm_release_baseline_get", query, workspace_id=workspace_id, baseline_id=baseline_id)

    async def pm_work_packages(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_work_packages_list", query, workspace_id=workspace_id)

    async def pm_work_package(self, principal, query, workspace_id, package_id, **_):
        return self._pm_read(principal, "pm_work_package_get", query, workspace_id=workspace_id, package_id=package_id)

    async def pm_work_package_readiness(self, principal, query, workspace_id, package_id, **_):
        return self._pm_read(principal, "pm_work_package_readiness_get", query, workspace_id=workspace_id, package_id=package_id)

    async def pm_work_profiles(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_work_profiles_list", query, workspace_id=workspace_id)

    async def pm_work_profile(self, principal, query, workspace_id, profile_id, **_):
        return self._pm_read(principal, "pm_work_profile_get", query, workspace_id=workspace_id, profile_id=profile_id)

    async def pm_work_profile_readiness(self, principal, query, workspace_id, profile_id, **_):
        return self._pm_read(principal, "pm_work_profile_readiness_get", query, workspace_id=workspace_id, profile_id=profile_id)

    async def pm_conversation_proposals(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_conversation_proposals_list", query, workspace_id=workspace_id)

    async def pm_conversation_proposal(self, principal, query, workspace_id, proposal_id, **_):
        return self._pm_read(principal, "pm_conversation_proposal_get", query, workspace_id=workspace_id, proposal_id=proposal_id)

    async def pm_overview_assessments(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_overview_assessments_list", query, workspace_id=workspace_id)

    async def pm_overview_assessment(self, principal, query, workspace_id, assessment_id, **_):
        return self._pm_read(principal, "pm_overview_assessment_get", query, workspace_id=workspace_id, assessment_id=assessment_id)

    async def pm_follow_up_assessments(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_follow_up_assessments_list", query, workspace_id=workspace_id)

    async def pm_follow_up_assessment(self, principal, query, workspace_id, assessment_id, **_):
        return self._pm_read(principal, "pm_follow_up_assessment_get", query, workspace_id=workspace_id, assessment_id=assessment_id)

    async def pm_follow_ups(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_follow_up_list", query, workspace_id=workspace_id)

    async def pm_follow_up(self, principal, query, workspace_id, intake_id, **_):
        return self._pm_read(principal, "pm_follow_up_get", query, workspace_id=workspace_id, intake_id=intake_id)

    async def pm_follow_up_latest(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_follow_up_latest_get", query, workspace_id=workspace_id)

    async def pm_governance_record(self, principal, query, workspace_id, record_id, **_):
        return self._pm_read(principal, "pm_governance_get", query, workspace_id=workspace_id, record_id=record_id)

    async def pm_governance_history(self, principal, query, workspace_id, record_id, **_):
        return self._pm_read(principal, "pm_governance_history_list", query, workspace_id=workspace_id, record_id=record_id)

    async def pm_meetings(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_meetings_list", query, workspace_id=workspace_id)

    async def pm_meeting(self, principal, query, workspace_id, meeting_id, **_):
        return self._pm_read(principal, "pm_meeting_get", query, workspace_id=workspace_id, meeting_id=meeting_id)

    async def pm_meeting_history(self, principal, query, workspace_id, meeting_id, kind, **_):
        return self._pm_read(principal, "pm_meeting_history_list", query, workspace_id=workspace_id, meeting_id=meeting_id, kind=kind)

    async def pm_sprint_artifacts(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_sprint_artifacts_get", query, workspace_id=workspace_id)

    async def pm_sprint_artifact_revisions(self, principal, query, workspace_id, kind, **_):
        return self._pm_read(principal, "pm_sprint_artifact_revisions_list", query, workspace_id=workspace_id, kind=kind)

    async def pm_sprint_artifact(self, principal, query, workspace_id, kind, revision_id, **_):
        return self._pm_read(principal, "pm_sprint_artifact_get", query, workspace_id=workspace_id, kind=kind, revision_id=revision_id)

    async def pm_sprints(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_sprints_list", query, workspace_id=workspace_id)

    async def pm_sprint(self, principal, query, workspace_id, sprint_id, **_):
        return self._pm_read(principal, "pm_sprint_get", query, workspace_id=workspace_id, sprint_id=sprint_id)

    async def pm_sprint_history(self, principal, query, workspace_id, sprint_id, kind, **_):
        return self._pm_read(principal, "pm_sprint_history_list", query, workspace_id=workspace_id, sprint_id=sprint_id, kind=kind)

    async def pm_roadmap(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_roadmap_get", query, workspace_id=workspace_id)

    async def pm_roadmap_revisions(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_roadmap_revisions_list", query, workspace_id=workspace_id)

    async def pm_roadmap_revision(self, principal, query, workspace_id, revision_id, **_):
        return self._pm_read(principal, "pm_roadmap_revision_get", query, workspace_id=workspace_id, revision_id=revision_id)

    async def pm_snapshots(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_snapshots_list", query, workspace_id=workspace_id)

    async def pm_snapshot(self, principal, query, workspace_id, snapshot_id, **_):
        return self._pm_read(principal, "pm_snapshot_get", query, workspace_id=workspace_id, snapshot_id=snapshot_id)

    async def pm_snapshot_latest(self, principal, query, workspace_id, **_):
        return self._pm_read(principal, "pm_snapshot_latest_get", query, workspace_id=workspace_id)

    async def pm_brief_export(self, principal, query, workspace_id, snapshot_id, **_):
        return self._pm_read(principal, "pm_brief_export_get", query, workspace_id=workspace_id, snapshot_id=snapshot_id)

    async def capabilities(self, principal, **_):
        from . import (
            pm_ahp_observations,
            pm_dependencies,
            pm_github_observation,
            pm_lifecycle,
            pm_overview,
            pm_register,
            pm_resources,
            pm_schedule,
            session_history,
        )
        fleet = self.daemon.fleet
        from . import (
            pm_conversation_proposals,
            pm_execution_runtime,
            pm_execution_verification,
            pm_maintenance_checks,
            pm_maintenance_policy,
            pm_maintenance_repair_runtime,
            pm_maintenance_repairs,
            pm_maintenance_review,
            pm_quality_reviews,
            pm_release_baselines,
            pm_spec_materialization,
            pm_work_batches,
            pm_workload,
            worktree_merge_operations,
        )
        gh_cfg = self.daemon.ops.context["github_config"]
        from . import (
            pm_attempt_result_reads,
            pm_work_adopt_and_start,
            pm_work_follow_up,
            pm_work_profiles,
            pm_work_start,
            pr_publication,
        )
        actions = [{"action": a.name, "scope": a.scope, "summary": a.summary, "allowed": principal.allows(a.scope)
                               and a.name not in {pm_execution_runtime.ACTION, pm_maintenance_repair_runtime.ACTION}
                               and (a.name != "delivery.merge_and_deploy" or principal.allows("deploy"))
                               and (a.name != "session.failover" or principal.allows("operate"))
                               and (a.name != pm_execution_verification.ACTION or all(principal.allows(s) for s in pm_execution_verification.REQUIRED))
                               and (a.name != pr_publication.ACTION or all(principal.allows(s) for s in pr_publication.REQUIRED))
                               and (a.name not in {pm_work_start.ACTION, pm_work_adopt_and_start.ACTION} or all(principal.allows(s) for s in pm_work_start.REQUIRED_SCOPES))
                               and (a.name != "pm.maintenance.repair" or principal.allows("observe") and principal.allows("manage"))
                               and (a.name not in {"pm.maintenance.repair.evidence.record", *pm_quality_reviews.ACTIONS} or principal.allows("observe"))
                               and (a.name not in {"pm.spec.materialize", "pm.work_batch.start", "pm.work_batch.evidence.record"}
                                    or principal.allows("observe") and principal.allows("start"))
                               and (a.name not in {"artifact.capture", "artifact.capture.managed", "session.approve_pending"} or principal.allows("observe"))}
                   for a in self.daemon.ops.actions.values()]
        hosts = [{"host": h, "observe": True, "writes": fleet.writes_enabled(h),
                  "profile_id": fleet.config.host(h).profile_id,
                  "orchestrate": fleet.orchestrate_enabled(h),
                  "managed_roots": list(fleet.config.host(h).managed_roots),
                  "shared_clone_worktrees": fleet.config.host(h).shared_clone_worktrees,
                  "confinement": confinement.host_capability(fleet, h)}
                 for h in fleet.config.hosts]
        managed = getattr(self.daemon, "managed_installation", None)
        managed_fields = {"managed_installation": {"installation_id": managed["installation_id"],
                            "runtime_version": managed["runtime_version"], "background": True},
                          "desktop_identity": dashboard_sync.identity(self.daemon.journal, principal)} if managed else {}
        build = build_identity()
        return 200, {**managed_fields, "actor": principal.actor, "scopes": sorted(principal.scopes), "api_version": API_VERSION,
                     "contract_version": CONTRACT_VERSION, "connector": __version__, "build": build,
                     "connector_update": connector_update_status(build), "hosts": hosts,
                     "frontend": {"digest": _dashboard_snapshot()[1]},
                     "artifacts": {"limits": self.daemon.artifact_store.settings.limits(),
                                   "capture": {"manual_single_file": True, "managed_single_file": True, "snapshot": False,
                                               "managed_selectors": ["execution_operation_id", "task_command"],
                                               "preview_ttl_s": artifact_capture.TTL_S},
                                   "hosts": [{"host": h, "configured": self.daemon.ops.context["artifact_host"].available(h),
                                              "readiness": self.daemon.ops.context["artifact_host"].readiness.get(h)} for h in fleet.config.hosts]},
                     "identity": dashboard_sync.identity(self.daemon.journal, principal),
                     "actions": actions, "operation_statuses": list(STATES), "connection_contract_version": CONNECTION_VERSION,
                     "cleanup": {"preview_ttl_s": cleanup.TTL_S, "max_items": cleanup.MAX_ITEMS, "restore": False,
                                 "scopes": ["cleanup", "cleanup_discard"], "retention": vars(fleet.config.cleanup),
                                 "read_deadline_s": cleanup.READ_DEADLINE_S, "serialize_previews": True,
                                 "preview": principal.allows("observe"), "apply": principal.allows("cleanup"),
                                 "discard": principal.allows("cleanup_discard"),
                                 "hosts": [{"host": name, "available": self._can_continue(name),
                                     "reasons": ([] if self._can_continue(name) else ["HOST_CLEANUP_UNAVAILABLE"])}
                                     for name in fleet.config.hosts]},
                      "features": {"dashboard_sync": {"version": 1, "bootstrap": "/api/v1/bootstrap", "checkpoint_replay": True},
                                  "controller_isolation": {"version": 1, "identity": "server_actor",
                                      "unproven_legacy": "read_only", "admin_takeover": False},
                                  "pm_overview": pm_overview.capabilities(),
                                  "pm_resources": pm_resources.capabilities(),
                                  "external_history": session_history.capabilities(),
                                  "pm_schedule": pm_schedule.capabilities(),
                                  "pm_register": pm_register.capabilities(),
                                  "pm_dependencies": pm_dependencies.capabilities(),
                                  "pm_github_observation": pm_github_observation.capabilities(),
                                  "pm_workspaces": {"version": 1, "ahp_observation": pm_ahp_observations.capabilities(), "executable_modes": ["idea", "planning"],
                                                    "runner": False, "context_packages": True,
                                                    "accepted_context_bundle": {"schema": "pm_context_bundle_v1", "context_version": 2,
                                                        "max_utf8_bytes": 262144, "explicit_selection": True, "complete_files_only": True},
                                                    "installation_eligibility": True,
                                                    "native_analysis": "pm.run.analyze" in self.daemon.ops.actions,
                                                    "bat_analysis": "pm.run.bat.analyze" in self.daemon.ops.actions,
                                                    "workspace_adapter": {"version": 1, "action": "pm.workspace.adapter.select"},
                                                    "runtime_reason": "external_tool_policy_unverified",
                                                    "plan_drafting": "pm.plan.draft" in self.daemon.ops.actions,
                                                    "plan_adoption": "pm.plan.adopt" in self.daemon.ops.actions,
                                                    "work_evidence": "pm.evidence.capture" in self.daemon.ops.actions,
                                                    "brief_snapshots": "pm.snapshot.create" in self.daemon.ops.actions,
                                                    "roadmap": "pm.roadmap.baseline.set" in self.daemon.ops.actions,
                                                    "lifecycle": pm_lifecycle.capabilities(self.daemon.ops),
                                                    "sprints": "pm.sprint.create" in self.daemon.ops.actions,
                                                    "governance": "pm.governance.record" in self.daemon.ops.actions,
                                                    "release_baselines": pm_release_baselines.capabilities(),
                                                    "maintenance_policy": pm_maintenance_policy.capabilities(self.daemon.ops),
                                                    "maintenance_checks": pm_maintenance_checks.capabilities(self.daemon.ops),
                                                    "maintenance_review": pm_maintenance_review.capabilities(),
                                                    "maintenance_repairs": pm_maintenance_repairs.capabilities(self.daemon.ops),
                                                    "execution_verification": pm_execution_verification.capabilities(),
                                                    "quality_reviews": pm_quality_reviews.capabilities(),
                                                    "pr_publication": pr_publication.capabilities(),
                                                    "engineering_delivery": {"contract": "pm-engineering-delivery/v1", "read_only": True},
                                                    "spec_materialization": pm_spec_materialization.capabilities(),
                                                    "work_batches": pm_work_batches.capabilities(self.daemon.ops),
                                                    "work_start": pm_work_start.capabilities(self.daemon.ops),
                                                    "work_adopt_and_start": pm_work_adopt_and_start.capabilities(self.daemon.ops),
                                                    "work_profiles": pm_work_profiles.capabilities(self.daemon.ops),
                                                    "work_attempt_results": pm_attempt_result_reads.capabilities(self.daemon.ops),
                                                    "operation_loop": {"version": 1},
                                                    "work_follow_up": pm_work_follow_up.capabilities(self.daemon.ops),
                                                    "work_input_idea": {"version": 1, "available": "pm.work_input.defer" in self.daemon.ops.actions},
                                                    "workload": pm_workload.capabilities(),
                                                    "work_package_prepare": "pm.work_package.create" in self.daemon.ops.actions,
                                                    "issue_mapping": all(action in self.daemon.ops.actions for action in ("pm.issue.publish", "pm.issue.cancel")),
                                                    "package_handoff": {"action": "repository.continue", "contract": "pm-engineering-handoff/v1",
                                                        "available": "repository.continue" in self.daemon.ops.actions,
                                                        "code_basis_required": True, "source_materialization": "bounded_full_prompt_or_fixed_spec_artifact",
                                                        "materialization_action": "pm.spec.materialize",
                                                        "requirement_acceptance_profiles": ["accepted_requirements_v1"]},
                                                    "follow_up_analysis": "pm.follow_up.analyze" in self.daemon.ops.actions,
                                                    "overview_assessment": "pm.overview.assess" in self.daemon.ops.actions,
                                                    "conversation_proposals": pm_conversation_proposals.capabilities(),
                                                    "brief_export": {"formats": ["markdown", "html", "svg"], "locales": ["en", "zh-TW"], "renderer_version": 4},
                                                    "follow_up_prepare": "pm.follow_up.prepare" in self.daemon.ops.actions,
                                                    "kanban": True, "spec_sources": True, "meetings": "pm.meeting.create" in self.daemon.ops.actions, "dispatch": False},
                                  "project_dispatch": {"version": 1, "artifacts": True, "model": True},
                                  "execution_delivery": {"version": 1, "source_kinds": ["execution", "task_command"]},
                                  "work_item_reads": {"version": 1},
                                  "session_reading": {"version": 1, "counts": "observed_history"},
                                  "host_preferences": {"version": 1, "model_catalog": True, "usage": True, "personal_models": True},
                                  "project_skills": {"version": 1, "pinned_selection": True, "application": False},
                                  "work_item_results": {"version": 1},
                                  "project_navigation": {"version": 1},
                                  "managed_repairs": {"version": 1, "fixed_evidence": True, "auto_launch": False},
                                  "session_instructions": {"version": 1, "live_queue": False, "per_message_cancel": False},
                                  "repository_sync": repository_sync.capabilities(self.daemon.ops),
                                  "worktree_merge": worktree_merge_operations.capabilities(self.daemon.ops),
                                  "cleanup": True, "cleanup_task": True, "inventory": True, "session_history": True, "session_observation": {"read": True, "wait": True, "max_wait_s": 1800}, "resource_relations": True, "discovery_scope": True,
                                  "worktree_history": {"known_bindings_only": True}, "history": {"source": "journal", "legacy_transitions": "may_be_incomplete", "optional_adapters": ["delivery_part_a", "delivery_part_b"],
                                      "observed_event_kinds": [r[0] for r in self.daemon.journal.db.execute("SELECT DISTINCT kind FROM api_events ORDER BY kind")]}, "events_stream": True, "operations": True, "work_items": True,
                                  "board_github_sync": {"version": 1, "interval_s": 300},
                                  "github": self.daemon.ops.context.get("github") is not None,
                                  "deploy": self.daemon.ops.context.get("github") is not None and any(r.verification for r in gh_cfg.recipes.values()),
                                  "deployment_history": True, "environment_generation": True,
                                  "runtime_check": True, "rollback_readiness": True,
                                  "metadata_update": self.daemon.ops.context.get("github") is not None,
                                  "merge_scope_preview": self.daemon.ops.context.get("github") is not None,
                                  "checkpoints": [h for h in fleet.config.hosts if self._can_continue(h)],
                                  "integration": [{"repository": r.repository,
                                                   "hosts": integration.usable_hosts(self.daemon.ops, r)}
                                                  for r in gh_cfg.repos.values() if r.integrate]},
                     "repositories": [{"repository": r.repository, "allow_merge": r.allow_merge, "allow_pr_update": r.allow_pr_update,
                                       "merge_methods": list(r.merge_methods)} for r in gh_cfg.repos.values()],
                     "deploy_recipes": [{"name": r.name, "repository": r.repository, "environment": r.environment,
                                         "mode": r.mode, "readiness": deployment.ready(r),
                                         "environment_generation": deployment.environment_status(self.daemon.ops, r.name)["desired_generation"],
                                         "runtime_check": {"version_required": r.verification.version_required,
                                                           "health_required": r.verification.health_required} if r.verification else None,
                                         "rollback": {"supported": r.rollback.supported, "identity": r.rollback.identity,
                                                      "not_undone": list(r.rollback.not_undone)}} for r in gh_cfg.recipes.values()]}

    async def repository_preview(self, principal, body, **_):
        return 200, {"preview": await repository_sync.preview(self.daemon.ops, principal, body)}

    async def managed_setup_state(self, principal, query, **_):
        from . import managed_setup
        if query:
            raise ApiError(422, "INVALID_REQUEST", "setup accepts no query")
        return 200, managed_setup.state(self.daemon, principal)

    async def managed_setup_discover(self, principal, query, body, **_):
        from .setup_source_discovery import discover
        if query:
            raise ApiError(422, "INVALID_REQUEST", "setup discovery accepts no query")
        try:
            return 200, await discover(self.daemon, principal, body)
        except OperationError as error:
            return error.status, {"error": {"code": error.code, "message": error.message, "admission_refused": True}}

    async def managed_setup_preview(self, principal, query, body, **_):
        from . import managed_setup
        if query:
            raise ApiError(422, "INVALID_REQUEST", "setup preview accepts no query")
        return 200, managed_setup.preview(self.daemon, principal, body)

    async def managed_setup_secret(self, principal, query, body, **_):
        from . import managed_setup
        if query:
            raise ApiError(422, "INVALID_REQUEST", "secret staging accepts no query")
        return 200, managed_setup.stage_secret(self.daemon, principal, body)

    async def work_item_results(self, principal, wid, query, **_):
        from . import work_item_results
        self._exact_query(query, {"after", "limit"})
        limit = self._q(query, "limit", "50")
        if not re.fullmatch(r"[0-9]{1,3}", limit):
            raise ApiError(422, "INVALID_REQUEST", "limit must be 1-100")
        return 200, work_item_results.read(self.daemon.ops, principal, wid,
            limit=int(limit), after=self._q(query, "after"))

    async def approval_preview(self, principal, body, **_):
        from .bulk_approval import preview
        return 200, await preview(self.daemon.ops, principal, body)

    async def bootstrap(self, principal, **_):
        from .session_reading import decorate
        # Cursor first. The following existing read models are live pages, not an atomic snapshot.
        sync = dashboard_sync.checkpoint(self.daemon.journal, principal)
        _, capabilities = await self.capabilities(principal)
        db = self.daemon.journal.db
        return 200, {"sync": sync, "capabilities": capabilities,
                     "snapshot": {"hosts": self.daemon.inventory.hosts_document(),
                         "sessions": decorate(self.daemon.journal, principal,
                             self.daemon.inventory.list_sessions(order="id", include_gone=True, limit=50,
                                                                principal=principal)),
                         "projects": work_items.projects_list(db, include_archived=True),
                         "work_items": work_items.work_items_list(db, include_archived=True, limit=50,
                             principal_id=sync["principal_id"]),
                         "operations": self.daemon.ops.list(limit=50, principal=principal)},
                     "pagination": {"atomic": False, "session_order": "id"}}

    def _can_continue(self, host: str) -> bool:
        runner = self.daemon.ops.context.get("git_runner")
        fleet = self.daemon.fleet
        return bool(runner and runner.available(host) and fleet.writes_enabled(host)
                    and fleet.orchestrate_enabled(host) and fleet.config.host(host).managed_roots)

    async def hosts(self, query, **_):
        return 200, self.daemon.inventory.hosts_document(host=self._q(query, "host"), discovery=bool(self._bool(query, "discovery")), after=self._int(query, "after", 0), limit=self._int(query, "limit", 20))

    async def host_preferences(self, principal, host, query, **_):
        from . import product_preferences
        self._exact_query(query, {"agent", "session_id", "refresh"})
        if self._q(query, "refresh", "false") not in {"0", "1", "true", "false"}:
            raise ApiError(422, "INVALID_REQUEST", "refresh must be true or false")
        return 200, await product_preferences.read(self.daemon.ops, principal, host,
            agent=self._q(query, "agent"), session_id=self._q(query, "session_id"),
            refresh=bool(self._bool(query, "refresh")))

    async def project_skills(self, principal, prj, query, **_):
        from . import project_skills
        self._exact_query(query, {"host", "workspace_id", "refresh"})
        if self._q(query, "refresh", "false") not in {"0", "1", "true", "false"}:
            raise ApiError(422, "INVALID_REQUEST", "refresh must be true or false")
        return 200, await project_skills.read(self.daemon.ops, principal, prj,
            self._q(query, "host"), self._q(query, "workspace_id"), refresh=bool(self._bool(query, "refresh")))

    async def repair_evidence(self, principal, prj, query, **_):
        from . import managed_repairs
        self._exact_query(query, {"kind", "host", "profile_id", "operation_id"})
        return 200, managed_repairs.read(self.daemon.ops, principal, prj,
                                         {key: values[0] for key, values in query.items()})

    async def work_item_repair(self, principal, wid, query, **_):
        from . import managed_repairs
        self._exact_query(query, set())
        return 200, managed_repairs.read_work_item(self.daemon.ops, principal, wid)

    async def session_instructions(self, principal, host, sid, query, **_):
        from . import session_instructions
        self._exact_query(query, {"limit", "cursor"})
        limit = self._q(query, "limit", "30")
        if not re.fullmatch(r"[0-9]{1,3}", limit):
            raise ApiError(422, "INVALID_REQUEST", "limit must be 1-100")
        return 200, session_instructions.read(self.daemon.ops, principal, host, sid,
                                             limit=int(limit), cursor=self._q(query, "cursor"))

    async def sessions(self, query, principal, **_):
        from .session_reading import decorate
        return 200, decorate(self.daemon.journal, principal, self.daemon.inventory.list_sessions(
            principal=principal,
            host=self._q(query, "host"), provenance=self._q(query, "provenance"),
            api_access=self._q(query, "access"), attention=self._bool(query, "attention"),
            include_gone=bool(self._bool(query, "include_gone")), order=self._q(query, "order", "activity"),
            cursor=self._q(query, "cursor"), limit=self._int(query, "limit", 50),
            profile_id=self._q(query, "profile_id"), project_id=query.get("project_id"), work_item_id=self._q(query, "work_item_id"),
            execution_id=self._q(query, "execution_id"), provider=self._q(query, "provider"), has_tab=self._bool(query, "has_tab"),
            loaded=self._bool(query, "loaded"), streaming=self._bool(query, "streaming"), lifecycle=self._q(query, "lifecycle"),
            stale=self._bool(query, "stale"), relation_scope=self._q(query, "relation_scope", "history")))

    def _known_host(self, host: str) -> None:
        if host not in self.daemon.fleet.config.hosts:
            raise ApiError(404, "UNKNOWN_HOST", f"unknown host {host!r}")

    async def session(self, query, host, sid, principal, **_):
        from .session_reading import decorate
        live = self._bool(query, "live")
        if live:
            self._known_host(host)
        try:
            out = self.daemon.inventory.session_document(host, sid)
        except OperationError as exc:
            if not live or exc.code != "NOT_FOUND":
                raise
            out = {"session": None, "cleanup": []}
        if live:
            from . import controller_authority
            with controller_authority.use(controller_authority.binding(self.daemon.journal.db, principal), self.daemon.journal):
                out["policy"] = await resource_policy.session_policy(self.daemon.fleet, host, sid)
            if out["session"]:
                out["session"].update(confinement.session_fields(
                    host, sid, await service._meta(self.daemon.fleet.client(host), sid),
                    account=confinement.account_status(self.daemon.fleet, host)))
        return 200, decorate(self.daemon.journal, principal, out)

    def _history(self, query, resource_type, resource_id):
        return self.daemon.inventory.observation.history(resource_type, resource_id,
            cursor=self._q(query, "cursor"), limit=self._int(query, "limit", 50), order=self._q(query, "order", "desc"),
            kind=query.get("kind"), since=float(self._q(query, "since")) if self._q(query, "since") is not None else None,
            until=float(self._q(query, "until")) if self._q(query, "until") is not None else None)

    def _relations(self, query, resource_type, resource_id):
        include = self._bool(query, "include_closed")
        return self.daemon.inventory.observation.relations(resource_type, resource_id, cursor=self._q(query, "cursor"),
            limit=self._int(query, "limit", 50), execution_id=self._q(query, "execution_id"), include_closed=True if include is None else include)

    async def session_history(self, query, host, sid, **_):
        return 200, self._history(query, "session", f"{host}/{sid}")

    async def session_relations(self, query, host, sid, **_):
        return 200, self._relations(query, "session", f"{host}/{sid}")

    async def task_history(self, query, task, **_):
        return 200, self._history(query, "execution", task)

    async def task_sessions(self, query, task, **_):
        return 200, self._relations(query, "execution", task)

    async def worktree(self, wid, **_):
        from . import execution_sources
        resource = self.daemon.inventory.observation.resource("worktree", wid)
        facts = execution_sources.for_worktree(self.daemon.ops, resource)
        facts["work"] = [integration.candidate_evidence(self.daemon.ops, item) for item in facts["work"]]
        return 200, {"worktree": {**resource, **facts}, "cleanup_receipts": cleanup.lookup(self.daemon.journal.db, wid)}

    async def worktree_history(self, query, wid, **_):
        return 200, self._history(query, "worktree", wid)

    async def worktree_relations(self, query, wid, **_):
        return 200, self._relations(query, "worktree", wid)

    async def discovery(self, query, host, **_):
        return 200, self.daemon.inventory.discovery(host, after=self._int(query, "after", 0), limit=self._int(query, "limit", 20))

    async def messages(self, query, host, sid, principal, reader=None, writer=None, check_authorization=None, **_):
        from .session_observation import query_params
        return 200, await self.session_observation.request(principal, "session_read",
            query_params("session_read", host, sid, query), reader=reader, writer=writer,
            check_authorization=check_authorization)

    async def session_wait(self, query, host, sid, principal, reader=None, writer=None, check_authorization=None, **_):
        from .session_observation import query_params
        return 200, await self.session_observation.request(principal, "session_wait",
            query_params("session_wait", host, sid, query), reader=reader, writer=writer,
            check_authorization=check_authorization)

    async def policy(self, query, principal, **_):
        host = self._q(query, "host")
        if host:
            self._known_host(host)
        names = [host] if host else list(self.daemon.fleet.config.hosts)
        from . import controller_authority
        with controller_authority.use(controller_authority.binding(self.daemon.journal.db, principal), self.daemon.journal):
            return 200, {"hosts": [await resource_policy.session_policy(self.daemon.fleet, h) for h in names]}

    async def connections(self, query, principal, **_):
        from . import setup_connections
        self._exact_query(query, {"installation_ref", "after", "limit"})
        return 200, setup_connections.list_connections(self.daemon.ops, principal,
            self._q(query, "installation_ref"), after=self._q(query, "after"), limit=self._int(query, "limit", 50))

    async def connections_preview(self, query, principal, body, **_):
        from . import setup_connections
        self._exact_query(query, set())
        return 200, setup_connections.preview(self.daemon.ops, principal, body)

    async def connections_receipt(self, query, principal, body, **_):
        from . import connection_handoff
        self._exact_query(query, set())
        return 200, connection_handoff.receive(self.daemon.ops, principal, body)

    async def connections_handoff(self, query, principal, body, **_):
        from . import connection_handoff
        self._exact_query(query, set())
        return 200, connection_handoff.claim(self.daemon.ops, principal, body)

    async def operations(self, query, principal, **_):
        statuses = [s for s in (self._q(query, "status") or "").split(",") if s]
        before = self._q(query, "before")
        return 200, self.daemon.ops.list(statuses=statuses or None, actor=self._q(query, "actor"),
                                         action=self._q(query, "action"),
                                         before=float(before) if before else None, before_id=self._q(query, "before_id"),
                                         limit=self._int(query, "limit", 50), principal=principal)

    async def create_operation(self, principal, query, body, headers, **_):
        key = headers.get("idempotency-key") or body.get("idempotency_key")
        wait = parse_wait(self._q(query, "wait"))  # before anything is stored: a 422 must mean nothing happened
        try:
            op, created = self.daemon.ops.create(
                principal, action=body.get("action"), target=body.get("target"), params=body.get("params"),
                preconditions=body.get("preconditions"), idempotency_key=key, entry="http")
        except OperationError as error:
            action = body.get("action") if isinstance(body, dict) else None
            target = body.get("target") if isinstance(body, dict) else None
            is_work_send = (
                action == "session.send"
                and isinstance(target, dict)
                and ("workspace_id" in target or "work_item_id" in target)
            )
            if (not str(action or "").startswith("pm.")
                    and action not in {
                        "setup.host", "setup.repository", "setup.verification", "setup.connection.apply",
                        "connection.reference.apply", "work.start", "work.adopt_and_start",
                        "github.pr.publish",
                    } and not is_work_send):
                raise
            # A source-hidden replay is still the original admitted operation.
            # Only proven absence in this actor/key namespace permits a fresh UI key.
            existing = self.daemon.ops.has_idempotency_record(principal, action=body.get("action"),
                target=body.get("target"), idempotency_key=key)
            return error.status, {"error": {"code": error.code, "message": error.message,
                                           **({} if existing else {"admission_refused": True})}}
        if wait > 0:
            await self.daemon.ops.wait(op["operation_id"], wait)
            # Waiting is not a read grant: membership or original source access
            # may have changed. Outside the admission catch: never mark this unsent.
            op = self.daemon.ops.get(op["operation_id"], principal=principal)
        from . import pm_work_follow_up
        op = pm_work_follow_up.enrich_operation(self.daemon.ops.db, op)
        op = await pm_operation_loop.attach(self.daemon.ops, principal, op)
        return (202 if created else 200), {"operation": op, "created": created}

    async def operation(self, op, principal, query=None, **_):
        if query and "installation_ref" in query:
            self._exact_query(query, {"installation_ref"})
        from . import pm_work_follow_up
        operation = await pm_operation_loop.attach(
            self.daemon.ops, principal, pm_work_follow_up.enrich_operation(
                self.daemon.ops.db, self.daemon.ops.get(op, principal=principal)))
        return 200, {"operation": operation,
                     "work_items": work_items.work_items_for(self.daemon.journal.db, "operation", op),
                     "cleanup_receipts": cleanup.receipts(self.daemon.ops, op)}

    async def cancel_operation(self, principal, op, **_):
        from . import pm_work_follow_up
        result = self.daemon.ops.cancel(principal, op)
        await self.daemon.artifact_store.reap_best_effort(op)
        result = pm_work_follow_up.enrich_operation(self.daemon.ops.db, result)
        return 200, {"operation": await pm_operation_loop.attach(self.daemon.ops, principal, result)}

    async def create_artifact(self, principal, query, body, headers, **_):
        envelope = {"action": "artifact.upload", "target": body.get("target", {}),
                    "params": body.get("params", {}), "preconditions": body.get("preconditions", {}),
                    "idempotency_key": body.get("idempotency_key")}
        return await self.create_operation(principal, query, envelope, headers)

    async def artifact_managed_capture_preview(self, principal, body, **_):
        return 200, {"preview": await artifact_managed.preview(self.daemon.ops, principal, body)}

    async def artifact_capture_preview(self, principal, body, **_):
        return 200, {"preview": await artifact_capture.preview(self.daemon.ops, principal, body)}

    async def artifacts(self, query, principal, **_):
        return 200, artifacts.list_artifacts(self.daemon.journal.db, limit=self._int(query, "limit", 50), cursor=self._q(query, "cursor"), principal=principal)

    async def artifact(self, aid, revision, principal, **_):
        return 200, {"artifact": artifacts.get(self.daemon.journal.db, aid, int(revision), principal=principal)}

    async def resume_operation(self, principal, op, **_):
        from . import pm_work_follow_up
        resumed = self.daemon.ops.resume(principal, op)
        resumed = pm_work_follow_up.enrich_operation(self.daemon.ops.db, resumed)
        return 200, {"operation": await pm_operation_loop.attach(self.daemon.ops, principal, resumed)}

    async def events(self, query, principal, **_):
        return 200, dashboard_sync.event_page(
            self.daemon.journal, principal, self._int(query, "after", 0), self._int(query, "limit", 100),
            token=self._q(query, "checkpoint"),
            resource_type=self._q(query, "resource_type"), resource_id=self._q(query, "resource_id"), kind=self._q(query, "kind"),
            related_resource_type=self._q(query, "related_resource_type"), related_resource_id=self._q(query, "related_resource_id"))

    async def pulls_list(self, owner, repo, query, **_):
        from . import pr_list
        if set(query) - {"state", "page"}:
            raise ApiError(422, "INVALID_REQUEST", "unknown PR list query")
        return 200, await pr_list.list_pulls(self.daemon.ops, f"{owner}/{repo}", self._q(query, "state", "open"), self._int(query, "page", 1))

    async def pull_preview(self, owner, repo, number, query, **_):
        return 200, {"pull_request": await integration.pr_card(
            self.daemon.ops, f"{owner}/{repo}", int(number), self._q(query, "method"),
            from_event=self._q(query, "from_event") == "true")}

    async def merge_preview(self, pv, **_):
        return 200, {"preview": pr_delivery.get_preview(self.daemon.journal.db, pv)}

    async def deployment_preview(self, query, **_):
        return 200, {"preview": await deployment.preview(self.daemon.ops, self._q(query, "recipe"))}

    async def deployments(self, query, **_):
        return 200, deployment.history(self.daemon.ops, self._q(query, "recipe"),
                                       cursor=self._q(query, "cursor"), limit=self._int(query, "limit", 50))

    async def deployment(self, dep, **_):
        return 200, {"deployment": deployment.status(self.daemon.ops, dep)}

    async def deployment_environment(self, query, **_):
        return 200, {"environment": deployment.environment_status(self.daemon.ops, self._q(query, "recipe"))}

    async def deployment_environment_history(self, query, **_):
        return 200, deployment.environment_history(self.daemon.ops, self._q(query, "recipe"),
                                                   cursor=self._q(query, "cursor"), limit=self._int(query, "limit", 50))

    async def integration_candidates(self, query, **_):
        host = self._q(query, "host")
        self._known_host(host or "")
        return 200, integration.candidates(self.daemon.ops, host, self._int(query, "limit", 50),
                                           source_kind=self._q(query, "source_kind"), source_id=self._q(query, "source_id"))

    async def integration_preview(self, pv, **_):
        return 200, {"preview": integration.preview_document(self.daemon.journal.db, pv)}

    async def integrations(self, query, **_):
        number = self._int(query, "pull_number", 0)
        if not self._q(query, "repository") or number <= 0:
            raise ApiError(422, "INVALID_REQUEST", "repository and pull_number are required")
        return 200, integration.integrations_list(self.daemon.ops, self._q(query, "repository"), number,
                                                  self._int(query, "limit", 20))

    async def integration(self, op, principal, **_):
        self.daemon.ops.get(op, steps=False, principal=principal)
        out = integration.integration_get(self.daemon.ops, op)
        out["cleanup"] = cleanup.lookup(self.daemon.journal.db, op)
        return 200, out

    async def checkpoints(self, query, **_):
        return 200, checkpoints.list_checkpoints(self.daemon.journal.db, host=self._q(query, "host"),
                                                 session_id=self._q(query, "session_id"),
                                                 limit=self._int(query, "limit", 50))

    async def checkpoint(self, query, cp, **_):
        record = checkpoints.get(self.daemon.journal.db, cp)
        out = {"checkpoint": record, "cleanup": cleanup.lookup(self.daemon.journal.db, cp)}
        if self._bool(query, "live"):  # has the source moved on since? (the checkpoint itself never changes)
            out["source"] = await checkpoints.source_head(self.daemon.ops, record)
        return 200, out

    async def checkpoint_preview(self, host, sid, **_):
        self._known_host(host)
        return 200, {"preview": await checkpoints.preview(self.daemon.ops, host, sid)}

    async def projects(self, query, **_):
        return 200, work_items.projects_list(self.daemon.journal.db,
                                             include_archived=bool(self._bool(query, "include_archived")))

    async def project(self, query, prj, principal, **_):
        from .session_reading import decorate
        if 'projection' in query:
            self._exact_query(query, {'projection', 'include_archived'})
        return 200, decorate(self.daemon.journal, principal, work_items.project_get(self.daemon.journal.db, prj,
            include_archived=bool(self._bool(query, "include_archived")), ops=self.daemon.ops, principal=principal,
            projection=self._q(query, 'projection', 'full')))

    async def work_items(self, query, principal, **_):
        return 200, work_items.work_items_list(
            self.daemon.journal.db, project_id=self._q(query, "project_id"), state=self._q(query, "state"),
            pending=self._bool(query, "pending"), include_archived=bool(self._bool(query, "include_archived")),
            limit=self._int(query, "limit", 50), cursor=self._q(query, "cursor"), unread=self._bool(query, "unread"),
            principal_id=dashboard_sync.identity(self.daemon.journal, principal)["principal_id"])

    async def work_item(self, wi, principal, **_):
        out = work_items.work_item_get(self.daemon.journal.db, wi,
            principal_id=dashboard_sync.identity(self.daemon.journal, principal)["principal_id"])
        out["cleanup"] = cleanup.lookup(self.daemon.journal.db, wi)
        return 200, out

    async def cleanup_preview(self, principal, body, **_):
        if set(body) - {"target", "choices"}:
            raise ApiError(422, "INVALID_PARAMS", "preview accepts target and choices")
        return 200, {"preview": await cleanup.preview(self.daemon.ops, principal, body.get("target"), body.get("choices"))}

    async def cleanup_retained(self, query, **_):
        return 200, await cleanup.retained(self.daemon.ops, **{k: self._q(query, k) for k in
            ("host", "resource_id", "query", "cursor")}, limit=self._int(query, "limit", 50))

    async def cleanup_tombstones(self, query, **_):
        return 200, cleanup.tombstones(self.daemon.ops, **{k: self._q(query, k) for k in
            ("host", "kind", "query", "original_id", "work_item_id", "cursor")}, limit=self._int(query, "limit", 50))

    async def cleanup_tombstone(self, rid, **_):
        row = self.daemon.journal.db.execute("SELECT document FROM resource_tombstones WHERE resource_id=?", (rid,)).fetchone()
        if not row:
            raise ApiError(404, "NOT_FOUND", "tombstone not found")
        doc = json.loads(row[0])
        return 200, {"tombstone": doc, "receipts": cleanup.receipts(self.daemon.ops, doc["operation_id"])}

    async def task(self, task, **_):
        return 200, {"task": await self.daemon.call("work_status", {"task_id": task}), "execution_id": task,
                     "relations_summary": self.daemon.inventory.observation.relations("execution", task, limit=200)}

    # ------------------------------------------------------------------ SSE
    async def _stream(self, query: dict, headers: dict[str, str], reader, writer, *, token: str,
                      principal, cors: str = "", browser_session: bool = False) -> None:
        actor = principal.actor
        if self._streams >= MAX_STREAMS:
            raise ApiError(429, "TOO_MANY_STREAMS", "too many event streams are open")
        if self._streams_by_actor.get(actor, 0) >= MAX_STREAMS_PER_ACTOR:
            raise ApiError(429, "TOO_MANY_STREAMS", f"{actor} already has {MAX_STREAMS_PER_ACTOR} event streams open")
        try:
            after = int(headers.get("last-event-id") or self._q(query, "after", 0) or 0)
        except ValueError:
            raise ApiError(422, "INVALID_REQUEST", "after must be an integer") from None
        checkpoint_token = self._q(query, "checkpoint")
        # Validate every event filter, including direct resource filters this legacy stream ignores.
        dashboard_sync.require_unfiltered(checkpoint_token, **{
            name: self._q(query, name) for name in
            ("kind", "resource_type", "resource_id", "related_resource_type", "related_resource_id")})
        dashboard_sync.event_page(self.daemon.journal, principal, after, 0, token=checkpoint_token, kind=self._q(query, "kind"),
            related_resource_type=self._q(query, "related_resource_type"), related_resource_id=self._q(query, "related_resource_id"))  # validate before streaming headers
        self._streams += 1
        self._streams_by_actor[actor] = self._streams_by_actor.get(actor, 0) + 1
        hangup = asyncio.ensure_future(reader.read(1))  # a client never sends after the request: EOF = gone
        try:
            writer.write(("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\n"
                          f"X-Content-Type-Options: nosniff\r\n{cors}Connection: close\r\n\r\n").encode())
            await asyncio.wait_for(writer.drain(), 10)
            started = last_write = last_auth = time.monotonic()
            while time.monotonic() - started < STREAM_MAX_S:
                if time.monotonic() - last_auth >= REAUTH_S:
                    current = api_auth.authenticate(self.daemon.journal.db, token, self.daemon._admin_token,
                                                    browser_session=browser_session)
                    if current is None or not current.allows("observe"):
                        return  # revoked or expired: stop sending; the client sees the stream end
                    if current != principal:
                        raise dashboard_sync.ResetRequired("principal_changed")
                    last_auth = time.monotonic()
                page = dashboard_sync.event_page(self.daemon.journal, principal, after, 100, token=checkpoint_token, kind=self._q(query, "kind"),
                    related_resource_type=self._q(query, "related_resource_type"), related_resource_id=self._q(query, "related_resource_id"))
                previous = after
                for ev in page["events"]:
                    data = json.dumps(ev, ensure_ascii=False, default=str)
                    writer.write(f"id: {ev['seq']}\nevent: {ev['kind']}\ndata: {data}\n\n".encode())
                    after = ev["seq"]
                after = page["next_cursor"]
                if checkpoint_token is not None and (page["events"] or after != previous):
                    checkpoint_token = page["sync"]["checkpoint"]["token"]
                    data = json.dumps(page["sync"], ensure_ascii=False)
                    writer.write(f"event: sync.checkpoint\ndata: {data}\n\n".encode())
                if page["events"] or after != previous:
                    await asyncio.wait_for(writer.drain(), 10)
                    last_write = time.monotonic()
                    if page["has_more"]:
                        continue
                elif time.monotonic() - last_write > KEEPALIVE_S:
                    writer.write(b": keepalive\n\n")
                    await asyncio.wait_for(writer.drain(), 10)
                    last_write = time.monotonic()
                if writer.is_closing() or hangup.done():
                    return
                await asyncio.wait({hangup}, timeout=0.5)
        except dashboard_sync.ResetRequired as e:
            data = json.dumps(e.document())
            with contextlib.suppress(ConnectionError, asyncio.TimeoutError):
                writer.write(f"event: sync.reset\ndata: {data}\n\n".encode())
                await asyncio.wait_for(writer.drain(), 10)
        except (ConnectionError, asyncio.TimeoutError):
            return
        finally:
            hangup.cancel()
            self._streams -= 1
            self._streams_by_actor[actor] -= 1
            if not self._streams_by_actor[actor]:
                del self._streams_by_actor[actor]
