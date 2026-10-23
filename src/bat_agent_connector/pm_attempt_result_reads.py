"""Read-only E04 work attempt result envelope, live changeset summary, and single-file commit diff.

All three reads (``pm_work_attempt_result_get``, ``pm_work_attempt_changes_get``,
``pm_work_attempt_diff_get``) perform zero database writes and emit zero ``api_events``.
``/result`` is a bounded projection that makes zero BAT IPC calls and never invokes
``SessionObservation.request`` or ``service.session_read``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from collections import OrderedDict
from pathlib import PurePosixPath
from typing import Any

from . import (
    checkpoints,
    controller_authority,
    dashboard_sync,
    delivery,
    orchestrate,
    pm_attempt_results,
    pm_delivery_lineage,
    pm_execution_verification,
    pm_lifecycle_gates,
    pm_quality_reviews,
    pm_result_requirements,
    pm_source_lineage,
    pm_work_start,
    pm_workspace,
    pr_publication,
    registry,
    repository_sync,
    verification,
    work_item_results,
)
from .api_auth import Principal
from .errors import BatError, ConnectionLost, InvokeTimeout, ResourceReadOnly
from .operations import TERMINAL, OperationError
from .pm_attempt_results import (
    BRANCH_DIFF_MAX_COMMITS,
    BRANCH_DIFF_MAX_FILES,
    CHANGESET_TOTAL_BUDGET_S,
    DECODER_VERSION,
)

SHA40 = re.compile(r"^[0-9a-f]{40}$")
CONTRACT = "pm-work-attempt-result/v1"
RESULT_GET = "pm_work_attempt_result_get"
CHANGES_GET = "pm_work_attempt_changes_get"
DIFF_GET = "pm_work_attempt_diff_get"

CHANGESET_MEMO_MAX_ENTRIES = 128
CHANGESET_MEMO_TTL_S = 300.0
WAIT_MS_MAX = 10_000
WAIT_POLL_INTERVAL_S = 0.5
WAIT_EVENT_PAGE_LIMIT = 20
WAIT_MAX_PAGES_PER_RESOURCE = 3
MAX_DIFF_BYTES = 262_144

READ_SCOPE = "observe"

VERIFY_OPS_SQL = """SELECT operation_id, params, actor, created_at, updated_at
FROM operations
WHERE action='pm.execution.verify'
  AND json_extract(target,'$.workspace_id')=?
  AND json_extract(target,'$.execution_operation_id')=?
ORDER BY created_at DESC, operation_id DESC
LIMIT 51"""

FIELDS: dict[str, tuple[str, ...]] = {
    RESULT_GET: ("workspace_id", "attempt_id", "cursor", "wait_ms"),
    CHANGES_GET: ("workspace_id", "attempt_id", "max_commits", "max_files"),
    DIFF_GET: ("workspace_id", "attempt_id", "commit", "path"),
}


def capabilities(ops=None) -> dict[str, Any]:
    return {
        "version": 1,
        "contract": CONTRACT,
        "routes": {
            "result": "/api/v1/pm/workspaces/{workspace_id}/work-attempts/{attempt_id}/result",
            "changes": "/api/v1/pm/workspaces/{workspace_id}/work-attempts/{attempt_id}/changes",
            "diff": "/api/v1/pm/workspaces/{workspace_id}/work-attempts/{attempt_id}/diff",
        },
        "reads": [RESULT_GET, CHANGES_GET, DIFF_GET],
        "mcp_tools": [RESULT_GET, CHANGES_GET, DIFF_GET],
        "adapters": {"bat": {"read_results": True}},
        "wait_ms_max": WAIT_MS_MAX,
        "reads_never_write": True,
        "requirements": {"version": 1, "binding": "original_engineering_handoff", "selection": "explicit_only"},
    }


def _bad(code: str, message: str, status: int = 422) -> OperationError:
    return OperationError(code, message, status)


def _has_table(db, name: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    clipped = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return clipped, True


def _changeset_memo(ops) -> OrderedDict[tuple[str, str, str, int, int], dict[str, Any]]:
    memo = ops.context.get("attempt_changes_memo")
    if not isinstance(memo, OrderedDict):
        memo = OrderedDict()
        ops.context["attempt_changes_memo"] = memo
    return memo


def _inflight_changes(ops) -> dict[tuple[str, int, int], asyncio.Task]:
    inflight = ops.context.get("attempt_changes_inflight")
    if not isinstance(inflight, dict):
        inflight = {}
        ops.context["attempt_changes_inflight"] = inflight
    return inflight


def _memo_get(
    ops,
    attempt_id: str,
    base_sha: str,
    live_head: str,
    max_commits: int,
    max_files: int,
) -> dict[str, Any] | None:
    memo = _changeset_memo(ops)
    key = (attempt_id, base_sha, live_head, max_commits, max_files)
    entry = memo.get(key)
    if entry is None:
        return None
    if (time.monotonic() - float(entry.get("stored_at", 0.0))) >= CHANGESET_MEMO_TTL_S:
        memo.pop(key, None)
        return None
    memo.move_to_end(key)
    return copy.deepcopy(entry["summary"])


def _memo_put(
    ops,
    attempt_id: str,
    base_sha: str,
    live_head: str,
    max_commits: int,
    max_files: int,
    summary: dict[str, Any],
) -> None:
    memo = _changeset_memo(ops)
    key = (attempt_id, base_sha, live_head, max_commits, max_files)
    memo[key] = {
        "stored_at": time.monotonic(),
        "summary": copy.deepcopy(summary),
    }
    memo.move_to_end(key)
    while len(memo) > CHANGESET_MEMO_MAX_ENTRIES:
        memo.popitem(last=False)


def _links(
    workspace_id: str,
    work_item_id: str,
    start_op_id: str,
    execution_op_id: str | None,
    sess_host: str | None,
    sess_id: str | None,
) -> dict[str, str | None]:
    return {
        "start_operation_hash": f"#/op/{start_op_id}",
        "execution_operation_hash": f"#/op/{execution_op_id}" if execution_op_id else None,
        "session_hash": f"#/session/{sess_host}/{sess_id}" if (sess_host and sess_id) else None,
        "work_item_hash": f"#/pm/{workspace_id}/board?select={work_item_id}",
    }


def _attempt_context(ops, principal: Principal, workspace_id: str, attempt_id: str) -> dict[str, Any]:
    env = pm_work_start.get_attempt(ops, principal, workspace_id, attempt_id)
    view = env["attempt"]
    work_item_id = view["work_item_id"]
    pm_source_lineage.require(ops.db, principal, "work_item", work_item_id)
    member_info = pm_workspace.require_member(ops.db, principal, workspace_id, write=False)

    start_op_id = view["start_operation_id"] or view["origin_id"]
    parent = ops.get(start_op_id, steps=False)
    saved_binding = (parent.get("external_refs") or {}).get("admission_binding") or {}
    intent = saved_binding.get("work_start") if isinstance(saved_binding.get("work_start"), dict) else {}
    handoff = view.get("execution_operation") if isinstance(view.get("execution_operation"), dict) else None
    execution_op_id = view.get("execution_operation_id")

    session = view.get("session") if isinstance(view.get("session"), dict) else None
    sess_host = session.get("host") if session else None
    sess_id = session.get("session_id") if session else None

    handoff_res = handoff.get("result") if (handoff and isinstance(handoff.get("result"), dict)) else {}
    turn_marker = handoff_res.get("message_id")
    if not isinstance(turn_marker, str) or not turn_marker.strip():
        turn_marker = None
        if handoff is not None:
            for step in handoff.get("steps") or []:
                if step.get("name") == "send" and isinstance(step.get("result"), dict):
                    mid = step["result"].get("message_id")
                    if isinstance(mid, str) and mid.strip():
                        turn_marker = mid
                        break
    latest_input_operation_id = None
    from .pm_work_follow_up import latest_sent_turn
    sent_mid, sent_op = latest_sent_turn(ops.db, attempt_id)
    if isinstance(sent_mid, str) and sent_mid.strip():
        turn_marker = sent_mid
        latest_input_operation_id = sent_op
    worktree_path = handoff_res.get("worktree_path") if isinstance(handoff_res.get("worktree_path"), str) else None
    code_source = view.get("code_source") if isinstance(view.get("code_source"), dict) else (intent.get("code_source") or {})
    base_sha = (
        code_source.get("source_sha")
        or handoff_res.get("source_sha")
        or ((handoff.get("params") or {}).get("source_sha") if handoff else None)
    )
    controller = (
        intent.get("controller")
        if isinstance(intent.get("controller"), dict)
        else controller_authority.operation_binding(parent)
    )
    return {
        "workspace_id": workspace_id,
        "attempt_id": attempt_id,
        "work_item_id": work_item_id,
        "view": view,
        "member_info": member_info,
        "start_op_id": start_op_id,
        "parent": parent,
        "intent": intent,
        "children": view.get("children") or {},
        "handoff": handoff,
        "execution_op_id": execution_op_id,
        "sess_host": sess_host,
        "sess_id": sess_id,
        "turn_marker": turn_marker,
        "latest_input_operation_id": latest_input_operation_id,
        "worktree_path": worktree_path,
        "code_source": code_source,
        "base_sha": str(base_sha) if isinstance(base_sha, str) and base_sha else None,
        "controller": controller,
    }


def _check_managed_session_registry(ops, ctx: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
    handoff = ctx["handoff"]
    sess_host = ctx["sess_host"]
    sess_id = ctx["sess_id"]
    worktree_path = ctx["worktree_path"]
    if handoff is None or sess_host is None or sess_id is None:
        return False, "session_not_started_yet", None
    if (
        handoff.get("status") != "succeeded"
        or ctx["view"].get("stage") == "start_unknown"
        or ctx["view"].get("operation_status") == "uncertain"
    ):
        return False, "session_not_managed_by_attempt", None
    handoff_res = handoff.get("result") if isinstance(handoff.get("result"), dict) else {}
    handoff_sess = handoff_res.get("session") if isinstance(handoff_res.get("session"), dict) else None
    if handoff_sess is None and handoff_res.get("host") and handoff_res.get("session_id"):
        handoff_sess = {"host": handoff_res["host"], "session_id": handoff_res["session_id"]}
    if not worktree_path or handoff_sess != {"host": sess_host, "session_id": sess_id}:
        return False, "session_not_managed_by_attempt", None
    reg = registry.get(sess_host, sess_id)
    if not isinstance(reg, dict) or reg.get("start_operation_id") != handoff["operation_id"]:
        return False, "session_not_managed_by_attempt", None
    handoff_ctrl = controller_authority.operation_binding(handoff)
    if handoff_ctrl is not None and not controller_authority.same_controller(
        reg.get("controller_authority"), handoff_ctrl
    ):
        return False, "session_not_managed_by_attempt", None
    return True, "", reg


def _observed_inventory(ops, host: str | None, session_id: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    host_row = None
    obs_row = None
    obs_body: dict[str, Any] = {}
    if host and _has_table(ops.db, "hosts_observed"):
        h = ops.db.execute(
            "SELECT host, reachable, last_success_at, error FROM hosts_observed WHERE host=?",
            (host,),
        ).fetchone()
        if h is not None:
            host_row = dict(h)
    if host and session_id and _has_table(ops.db, "sessions_observed"):
        s = ops.db.execute(
            "SELECT body, digest, sort_key, last_seen_at, missing_count, gone_at FROM sessions_observed WHERE host=? AND session_id=?",
            (host, session_id),
        ).fetchone()
        if s is not None:
            obs_row = dict(s)
            if obs_row.get("body"):
                try:
                    parsed = json.loads(obs_row["body"])
                    if isinstance(parsed, dict):
                        obs_body = parsed
                except (TypeError, ValueError):
                    obs_body = {}
    return host_row, obs_row, obs_body


def _collector_status(ops, attempt_id: str) -> dict[str, Any]:
    collector = ops.context.get("attempt_result_collector")
    if collector is not None and attempt_id in collector.last_check:
        entry = collector.last_check[attempt_id]
        return {
            "status": "checked",
            "checked_at": entry.get("checked_at"),
            "outcome": entry.get("outcome"),
        }
    return {"status": "not_checked_since_start"}


def _self_reported_facet(
    current_receipt_doc: dict[str, Any] | None,
    latest_assistant_summary: dict[str, Any] | None,
    turn_done: bool | str,
) -> str:
    if not isinstance(current_receipt_doc, dict):
        return "none"
    turn_prog = (
        current_receipt_doc.get("turn_progress")
        if isinstance(current_receipt_doc.get("turn_progress"), dict)
        else {}
    )
    boundary = current_receipt_doc.get("boundary") or turn_prog.get("boundary")
    if boundary == "agent_error":
        return "reported_blocker"
    if (
        turn_done is True
        and isinstance(latest_assistant_summary, dict)
        and bool(str(latest_assistant_summary.get("text") or "").strip())
    ):
        return "claimed_done"
    return "none"


def _verification_section(
    ops,
    principal: Principal,
    workspace_id: str,
    work_item_id: str,
    start_op_id: str,
    execution_op_id: str | None,
    sess_host: str | None,
    sess_id: str | None,
    target_commit_sha: str | None,
) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any], dict[str, Any] | None, str]:
    runs: list[dict[str, Any]] = []
    exec_verify_commit: dict[str, Any] | None = None
    sess_verify_commit: dict[str, Any] | None = None
    verification_partial = False

    if execution_op_id and _has_table(ops.db, "operations"):
        rows = ops.db.execute(
            VERIFY_OPS_SQL,
            (workspace_id, execution_op_id),
        ).fetchall()
        if len(rows) > 50:
            verification_partial = True
            rows = rows[:50]
        rows = list(reversed(rows))
        for r in rows:
            oid = r["operation_id"]
            if not pm_execution_verification.operation_visible(ops.db, principal, {"operation_id": oid}):
                verification_partial = True
                continue
            try:
                ver_env = pm_execution_verification.get_verification(ops, principal, workspace_id, oid)
            except OperationError:
                verification_partial = True
                continue
            pub = ver_env.get("verification")
            op_st = ver_env.get("operation_status")
            if isinstance(pub, dict):
                cand_sha = (pub.get("reference") or {}).get("candidate_commit")
                raw_pub_st = str(pub.get("status") or "").lower()
                if raw_pub_st in {"passed", "verified", "succeeded"}:
                    run_status = "verified"
                elif raw_pub_st in {"failed", "blocked", "error"}:
                    run_status = "failed"
                else:
                    run_status = "unknown"
                if (
                    run_status == "verified"
                    and isinstance(cand_sha, str)
                    and SHA40.fullmatch(cand_sha)
                    and isinstance(target_commit_sha, str)
                    and SHA40.fullmatch(target_commit_sha)
                    and cand_sha != target_commit_sha
                ):
                    run_status = "stale"
                rec_at = pub.get("created_at") or r["updated_at"] or r["created_at"]
                runs.append(
                    {
                        "kind": "execution_verification",
                        "verification_id": oid,
                        "operation_id": oid,
                        "status": run_status,
                        "commit_sha": cand_sha,
                        "exit_code": pub.get("exit_code"),
                        "recorded_by": r["actor"],
                        "recorded_at": rec_at,
                    }
                )
                if isinstance(cand_sha, str) and SHA40.fullmatch(cand_sha):
                    exec_verify_commit = {
                        "commit_sha": cand_sha,
                        "source_kind": "execution_verification",
                        "source_id": oid,
                        "recorded_by": r["actor"],
                        "recorded_at": rec_at,
                    }
            elif op_st in {"accepted", "queued", "running", "waiting_external", "waiting_checks"}:
                try:
                    op_params = json.loads(r["params"] or "{}")
                except (TypeError, ValueError):
                    op_params = {}
                runs.append(
                    {
                        "kind": "execution_verification",
                        "verification_id": oid,
                        "operation_id": oid,
                        "status": "running",
                        "commit_sha": op_params.get("candidate_commit") if isinstance(op_params, dict) else None,
                        "exit_code": None,
                        "recorded_by": r["actor"],
                        "recorded_at": r["updated_at"] or r["created_at"],
                    }
                )
            elif op_st == "failed":
                try:
                    op_params = json.loads(r["params"] or "{}")
                except (TypeError, ValueError):
                    op_params = {}
                runs.append(
                    {
                        "kind": "execution_verification",
                        "verification_id": oid,
                        "operation_id": oid,
                        "status": "failed",
                        "commit_sha": op_params.get("candidate_commit") if isinstance(op_params, dict) else None,
                        "exit_code": None,
                        "recorded_by": r["actor"],
                        "recorded_at": r["updated_at"] or r["created_at"],
                    }
                )

    if sess_host and sess_id:
        ver = verification.get(sess_host, sess_id)
        if isinstance(ver, dict):
            v_sha = ver.get("candidate_commit") or ver.get("commit_sha") or ver.get("head_sha")
            if isinstance(ver.get("status"), str) and ver.get("status"):
                raw_st = ver["status"].lower()
                if raw_st in {"passed", "verified", "succeeded"}:
                    v_status = "verified"
                elif raw_st in {"running", "pending", "queued"}:
                    v_status = "running"
                elif raw_st in {"failed", "blocked", "error"}:
                    v_status = "failed"
                else:
                    v_status = "unknown"
            elif type(ver.get("exit_code")) is int:
                v_status = "verified" if ver["exit_code"] == 0 else "failed"
            else:
                v_status = "unknown"
            if (
                v_status == "verified"
                and isinstance(v_sha, str)
                and SHA40.fullmatch(v_sha)
                and isinstance(target_commit_sha, str)
                and SHA40.fullmatch(target_commit_sha)
                and v_sha != target_commit_sha
            ):
                v_status = "stale"
            rec_by = ver.get("actor") or ver.get("verified_by")
            rec_at = ver.get("recorded_at") if ver.get("recorded_at") is not None else ver.get("verified_at")
            runs.append(
                {
                    "kind": "session_verification",
                    "operation_id": None,
                    "status": v_status,
                    "commit_sha": v_sha,
                    "exit_code": ver.get("exit_code"),
                    "recorded_by": rec_by,
                    "recorded_at": rec_at,
                }
            )
            if isinstance(v_sha, str) and SHA40.fullmatch(v_sha):
                sess_verify_commit = {
                    "commit_sha": v_sha,
                    "source_kind": "session_verification",
                    "source_id": f"{sess_host}/{sess_id}",
                    "recorded_by": rec_by,
                    "recorded_at": rec_at,
                }

    if not runs:
        overall_ver_status = "unverified"
    else:
        indexed_runs = list(enumerate(runs))
        indexed_runs.sort(
            key=lambda pair: (
                float(pair[1]["recorded_at"]) if isinstance(pair[1].get("recorded_at"), (int, float)) else 0.0,
                pair[0],
            )
        )
        runs = [pair[1] for pair in indexed_runs]
        overall_ver_status = runs[-1]["status"]

    qr_envelope: dict[str, Any] = {"reviews": [], "next_cursor": None, "visibility_filtered": False}
    if _has_table(ops.db, "pm_quality_reviews"):
        try:
            qr_envelope = pm_quality_reviews.for_work(ops, principal, workspace_id, work_item_id)
            if qr_envelope.get("visibility_filtered") or qr_envelope.get("source_visibility_filtered") or qr_envelope.get("next_cursor") is not None:
                verification_partial = True
        except OperationError:
            verification_partial = True
            qr_envelope = {"reviews": [], "next_cursor": None, "visibility_filtered": False}

    reviews = qr_envelope.get("reviews") if isinstance(qr_envelope.get("reviews"), list) else []
    matched_review = None
    if execution_op_id:
        for rev in reviews:
            if isinstance(rev, dict) and (rev.get("context") or {}).get("execution_operation_id") == execution_op_id:
                matched_review = rev

    if matched_review is None:
        review_decision = {
            "kind": "unassessed",
            "acceptance": "unassessed",
            "dod": "unassessed",
            "qualified": False,
        }
    else:
        r_kind = (
            "revocation"
            if matched_review.get("revocation") is not None
            else ("assessment" if matched_review.get("assessment") is not None else "unassessed")
        )
        review_decision = {
            "kind": r_kind,
            "acceptance": str(matched_review.get("acceptance") or "unassessed"),
            "dod": str(matched_review.get("dod") or "unassessed"),
            "qualified": bool(matched_review.get("qualified", False)),
        }

    if review_decision["qualified"] and review_decision["acceptance"] == "accepted":
        acceptance_facet = {"status": "passed", "semantic_acceptance": "accepted"}
    elif review_decision["acceptance"] == "rejected":
        acceptance_facet = {"status": "blocked", "semantic_acceptance": "rejected"}
    else:
        acceptance_facet = {"status": "unassessed", "semantic_acceptance": "unassessed"}

    chosen_ver_commit = exec_verify_commit or sess_verify_commit
    verification_block = {
        "status": overall_ver_status,
        "runs": runs,
        "quality_reviews": {
            "reviews": reviews,
            "current": (
                {
                    "review_id": matched_review.get("review_id"),
                    "version": matched_review.get("version"),
                    "qualified": bool(matched_review.get("qualified", False)),
                    "kind": review_decision["kind"],
                    "acceptance": review_decision["acceptance"],
                    "dod": review_decision["dod"],
                }
                if matched_review is not None
                else None
            ),
        },
    }
    ver_coverage = "partial" if verification_partial else "complete"
    return verification_block, overall_ver_status, review_decision, acceptance_facet, chosen_ver_commit, ver_coverage


def _delivery_section(
    ops,
    principal: Principal,
    workspace_id: str,
    work_item_id: str,
    execution_op_id: str | None,
    delivery_goal: str,
    repository: str | None,
    target_commit_sha: str | None,
) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any] | None, str]:
    pull_requests: list[dict[str, Any]] = []
    integrations: list[dict[str, Any]] = []
    merges: list[dict[str, Any]] = []
    deployments: list[dict[str, Any]] = []
    commit_candidates: list[dict[str, Any]] = []
    delivery_partial = False
    pr_closed_unmerged = False

    attributed_pr_keys: set[tuple[str, int]] = set()
    seen_pr_keys: set[tuple[str, int]] = set()
    attributed_merge_op_ids: set[str] = set()
    attributed_merged_shas: set[str] = set()

    # 1. PR creation receipts for this execution_operation_id
    if execution_op_id and _has_table(ops.db, "pr_creation_receipts"):
        try:
            total_pr_rows = ops.db.execute(
                "SELECT COUNT(*) FROM pr_creation_receipts WHERE repair_parent_id IS NULL AND child_operation_id=?",
                (execution_op_id,),
            ).fetchone()[0]
            pr_list = pr_publication.list_creation_receipts(ops, principal, None, execution_op_id)
            visible_entries = pr_list.get("creation_receipts") or []
            if len(visible_entries) < int(total_pr_rows or 0) or pr_list.get("next_after") is not None:
                delivery_partial = True
            for entry in visible_entries:
                rec = entry.get("receipt") if isinstance(entry, dict) else None
                if not isinstance(rec, dict):
                    continue
                pr_num = rec.get("pull_number")
                pr_repo = rec.get("repository")
                if type(pr_num) is int:
                    pr_entry = {
                        k: rec[k]
                        for k in (
                            "publication_operation_id",
                            "workspace_id",
                            "child_operation_id",
                            "repair_parent_id",
                            "provider_origin",
                            "repository",
                            "repository_id",
                            "pull_number",
                            "native_pr_id",
                            "native_pr_node_id",
                            "branch_ref",
                            "candidate_sha",
                            "base_ref",
                            "base_sha",
                            "observed_head_sha",
                            "observed_base_sha",
                            "target_verified",
                            "quality_ref",
                            "created_at",
                            "requirement_acceptance",
                        )
                        if k in rec
                    }
                    pull_requests.append(pr_entry)
                    if isinstance(pr_repo, str) and pr_repo.strip():
                        pr_key = (pr_repo.strip().lower(), pr_num)
                        attributed_pr_keys.add(pr_key)
                        seen_pr_keys.add(pr_key)
                c_sha = rec.get("candidate_sha")
                if isinstance(c_sha, str) and SHA40.fullmatch(c_sha):
                    commit_candidates.append(
                        {
                            "commit_sha": c_sha,
                            "source_kind": "integration",
                            "source_id": rec.get("publication_operation_id"),
                            "recorded_by": None,
                            "recorded_at": rec.get("created_at"),
                        }
                    )
        except OperationError:
            delivery_partial = True

    # 2. Work item delivery lineage, attributed to this attempt
    raw_items: list[dict[str, Any]] = []
    try:
        deliv = pm_delivery_lineage.for_work(ops, principal, workspace_id, work_item_id)
        if deliv.get("source_visibility_filtered") or deliv.get("next_after") is not None:
            delivery_partial = True
        raw_items = [it for it in (deliv.get("deliveries") or []) if isinstance(it, dict)]
    except OperationError as exc:
        if exc.code != "NOT_FOUND":
            delivery_partial = True

    # Pass 2a: Integration items attributed by sources row (source_kind == 'execution' and source_id == execution_op_id)
    if execution_op_id:
        for item in raw_items:
            if "integration" not in item or not isinstance(item.get("integration"), dict):
                continue
            integ = item["integration"]
            matching_src = None
            for src in integ.get("sources") or []:
                if (
                    isinstance(src, dict)
                    and src.get("source_kind") == "execution"
                    and src.get("source_id") == execution_op_id
                ):
                    matching_src = src
                    break
            if matching_src is None:
                continue
            integrations.append(
                {
                    k: copy.deepcopy(item[k])
                    for k in (
                        "operation_id",
                        "action",
                        "status",
                        "created_at",
                        "updated_at",
                        "error_code",
                        "target",
                        "integration",
                        "requirement_acceptance",
                    )
                    if k in item
                }
            )
            delivered_sha = matching_src.get("delivered_sha")
            if isinstance(delivered_sha, str) and SHA40.fullmatch(delivered_sha):
                commit_candidates.append(
                    {
                        "commit_sha": delivered_sha,
                        "source_kind": "integration",
                        "source_id": item.get("operation_id"),
                        "recorded_by": None,
                        "recorded_at": item.get("updated_at") or item.get("created_at"),
                    }
                )
            if item.get("action") != "integration.preview" or (
                isinstance(delivered_sha, str) and SHA40.fullmatch(delivered_sha)
            ):
                item_target = item.get("target") if isinstance(item.get("target"), dict) else {}
                pr_num = item_target.get("pull_number")
                pr_repo = item_target.get("repository")
                if type(pr_num) is int and isinstance(pr_repo, str) and pr_repo.strip():
                    pr_key = (pr_repo.strip().lower(), pr_num)
                    attributed_pr_keys.add(pr_key)
                    if pr_key not in seen_pr_keys:
                        seen_pr_keys.add(pr_key)
                        pull_requests.append(
                            {
                                "operation_id": item.get("operation_id"),
                                **{
                                    k: item_target[k]
                                    for k in ("provider_origin", "repository", "repository_id", "pull_number", "host")
                                    if k in item_target
                                },
                                "requirement_acceptance": item.get("requirement_acceptance"),
                            }
                        )

    # Pass 2b: Merge items attributed by target (repository, pull_number) matching an attributed PR
    for item in raw_items:
        if "merge" not in item or not isinstance(item.get("merge"), dict):
            continue
        item_target = item.get("target") if isinstance(item.get("target"), dict) else {}
        m_pr_num = item_target.get("pull_number")
        m_repo = item_target.get("repository")
        if not (type(m_pr_num) is int and isinstance(m_repo, str) and (m_repo.strip().lower(), m_pr_num) in attributed_pr_keys):
            continue
        m = item["merge"]
        m_err = item.get("error_code")
        if isinstance(item.get("operation_id"), str):
            attributed_merge_op_ids.add(item["operation_id"])
        merges.append(
            {
                "operation_id": item.get("operation_id"),
                "repository": m_repo,
                "pull_number": m_pr_num,
                "merged": bool(m.get("merged")),
                "merged_sha": m.get("merged_sha"),
                "head_sha": m.get("head_sha"),
                "status": item.get("status"),
                "error_code": m_err,
                "requirement_acceptance": item.get("requirement_acceptance"),
            }
        )
        if m_err in {"PR_CLOSED", "PR_NOT_OPEN"} and not m.get("merged"):
            pr_closed_unmerged = True
        m_sha = m.get("merged_sha")
        if isinstance(m_sha, str) and SHA40.fullmatch(m_sha):
            attributed_merged_shas.add(m_sha)
            commit_candidates.append(
                {
                    "commit_sha": m_sha,
                    "source_kind": "integration",
                    "source_id": item.get("operation_id"),
                    "recorded_by": None,
                    "recorded_at": item.get("updated_at") or item.get("created_at"),
                }
            )

    # Pass 2c: Deployment items attributed through an attributed merge
    for item in raw_items:
        if "deployment" not in item or not isinstance(item.get("deployment"), dict):
            continue
        d = item["deployment"]
        same_merge_op = (
            item.get("operation_id") in attributed_merge_op_ids
            and item.get("action") == "delivery.merge_and_deploy"
        )
        matches_merged_sha = isinstance(d.get("source_sha"), str) and d["source_sha"] in attributed_merged_shas
        if not (same_merge_op or matches_merged_sha):
            continue
        deployments.append(
            {
                **d,
                "operation_id": item.get("operation_id"),
                "requirement_acceptance": item.get("requirement_acceptance"),
            }
        )

    integration_commit: dict[str, Any] | None = None
    if commit_candidates:
        indexed_cands = list(enumerate(commit_candidates))
        indexed_cands.sort(
            key=lambda pair: (
                float(pair[1]["recorded_at"]) if isinstance(pair[1].get("recorded_at"), (int, float)) else 0.0,
                pair[0],
            )
        )
        integration_commit = indexed_cands[-1][1]

    if any(m.get("merged") for m in merges):
        merge_facet = "merged"
    elif any(
        m.get("status") in {"queued", "running", "waiting_external", "waiting_checks", "accepted", "uncertain"}
        for m in merges
    ):
        merge_facet = "merge_pending"
    elif pr_closed_unmerged:
        merge_facet = "pr_closed_unmerged"
    elif pull_requests:
        merge_facet = "pr_open"
    else:
        merge_facet = "not_started"

    if deployments:
        latest_dep = deployments[-1]
        deployment_facet = {
            "state": str(latest_dep.get("state") or "not_deployed"),
            "verified": bool(latest_dep.get("verified", False)),
            "is_current": bool(latest_dep.get("is_current", False)),
        }
    else:
        deployment_facet = {
            "state": "not_deployed",
            "verified": False,
            "is_current": False,
        }

    delivery_block = {
        "delivery_goal": delivery_goal,
        "pull_requests": pull_requests,
        "integrations": integrations,
        "merges": merges,
        "deployments": deployments,
    }
    deliv_coverage = "partial" if delivery_partial else "complete"
    return delivery_block, merge_facet, deployment_facet, integration_commit, deliv_coverage


def _is_attempt_controller(
    ops,
    principal: Principal,
    workspace_id: str,
    attempt_ctrl: dict[str, Any] | None,
) -> bool:
    if not isinstance(attempt_ctrl, dict):
        return False
    try:
        caller_ctrl = controller_authority.binding(
            ops.db,
            principal,
            pm_workspace_id=workspace_id,
        )
        return controller_authority.same_controller(caller_ctrl, attempt_ctrl)
    except (OperationError, ResourceReadOnly, KeyError, TypeError, ValueError):
        return False


def _is_writer_member(ops, principal: Principal, workspace_id: str, *, decide: bool = False) -> bool:
    try:
        pm_workspace.require_member(ops.db, principal, workspace_id, write=True, decide=decide)
        return True
    except OperationError:
        return False


def _action_scopes(ops, action_name: str, extra: frozenset[str] = frozenset()) -> list[str]:
    return sorted({READ_SCOPE, ops.actions[action_name].scope} | extra)


def _publication_pair(ctx: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    """The exact repository id, start base and branch admission will require."""
    code = ctx.get("code_source") if isinstance(ctx.get("code_source"), dict) else {}
    repository = code.get("repository")
    repository_id = code.get("repository_id")
    base_sha = ctx.get("base_sha")
    if not isinstance(repository, str) or not repository:
        return None
    if type(repository_id) is not int or repository_id <= 0:
        return None
    if not isinstance(base_sha, str) or SHA40.fullmatch(base_sha) is None:
        return None
    try:
        base_ref = repository_sync.check_ref(code.get("source_ref"))
    except OperationError:
        return None
    return {"repository_id": repository_id, "expected_base_sha": base_sha}, base_ref


def _publication_open(ops, execution_op_id: str) -> bool:
    rows = ops.db.execute(
        """SELECT status FROM operations
           WHERE action=? AND json_extract(target, '$.source_operation_id')=?""",
        (pr_publication.ACTION, execution_op_id),
    ).fetchall()
    return any(row["status"] not in TERMINAL for row in rows)


def _verified_publication_for_commit(ops, execution_op_id: str, candidate_sha: str | None) -> bool:
    # Scan the child's receipts. A 50-row page is not proof none exist, and a
    # row that fails verification is not proof a pull request exists.
    if not isinstance(candidate_sha, str) or SHA40.fullmatch(candidate_sha) is None:
        return False
    if not _has_table(ops.db, "pr_creation_receipts"):
        return False
    rows = ops.db.execute(
        "SELECT operation_id FROM pr_creation_receipts WHERE child_operation_id=?",
        (execution_op_id,),
    ).fetchall()
    for row in rows:
        try:
            document = pr_publication._raw_receipt(ops, row["operation_id"])
        except (OperationError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if (isinstance(document, dict) and document.get("target_verified") is True
                and document.get("candidate_sha") == candidate_sha):
            return True
    return False


def _publication_lifecycle_block(ops, workspace_id: str) -> str | None:
    # Same predicate as a new admission: investment against the current epoch.
    # Passing that epoch refuses only a stopped workspace, and this read does not insert.
    binding = {"pm_lineage": {"workspace_id": workspace_id}}
    target = {"workspace_id": workspace_id}
    wid = pm_lifecycle_gates.lineage(pr_publication.ACTION, target, binding)
    if not wid or not pm_lifecycle_gates.installed(ops.db):
        return None
    if pm_lifecycle_gates.kind(pr_publication.ACTION, binding) != "investment":
        return None
    current = pm_lifecycle_gates.head(ops.db, wid)
    try:
        pm_lifecycle_gates._require(current, current["execution_epoch"])
    except OperationError as exc:
        if exc.code == "PM_LIFECYCLE_STOPPED":
            return "lifecycle_stopped"
    return None


def _publication_github_block(ops) -> str | None:
    try:
        delivery._gh(ops)
    except OperationError as exc:
        if exc.code == "GITHUB_NOT_CONFIGURED":
            return "github_not_configured"
    return None


def _publication_block_reason(ops, ctx: dict[str, Any], candidate_sha: str | None) -> str | None:
    if _publication_pair(ctx) is None:
        return "publication_source_incomplete"
    execution_op_id = ctx["execution_op_id"]
    if isinstance(execution_op_id, str) and _publication_open(ops, execution_op_id):
        return "WAIT_ORIGINAL_OPERATION_RECONCILE"
    # Admission requires this head. An open publish stays reconcile above; do not
    # invent a commit or read the provider to fill one in.
    if not isinstance(candidate_sha, str) or SHA40.fullmatch(candidate_sha) is None:
        return "publication_source_incomplete"
    if isinstance(execution_op_id, str) and _verified_publication_for_commit(
        ops, execution_op_id, candidate_sha
    ):
        return "pull_request_exists"
    stopped = _publication_lifecycle_block(ops, ctx["workspace_id"])
    if stopped:
        return stopped
    return _publication_github_block(ops)


def _compute_actions(
    ops,
    principal: Principal,
    ctx: dict[str, Any],
    *,
    execution_state: str,
    delivery_goal: str,
    candidate_sha: str | None,
    has_receipt: bool,
    current_review_id: str | None = None,
    current_reviewer_actor: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    workspace_id = ctx["workspace_id"]
    work_item_id = ctx["work_item_id"]
    start_op_id = ctx["start_op_id"]
    execution_op_id = ctx["execution_op_id"]
    sess_host = ctx["sess_host"]
    sess_id = ctx["sess_id"]
    view = ctx["view"]
    stage = view["stage"]
    op_status = view["operation_status"]
    requester_actor = ctx["parent"].get("actor")

    is_writer = _is_writer_member(ops, principal, workspace_id)
    is_controller = _is_attempt_controller(ops, principal, workspace_id, ctx["controller"])

    allowed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    def consider(
        entry: dict[str, Any],
        *,
        action_name: str,
        requires_controller: bool = True,
        requires_original_actor: bool = False,
        requires_writer: bool = False,
        requires_owner: bool = False,
        requires_reviewer: bool = False,
        reconcile_blocked: bool = False,
    ) -> None:
        is_registered_action = action_name in ops.actions
        if entry.get("kind") == "action" and not is_registered_action:
            return
        req_scopes = entry.get("required_scopes") or []
        if reconcile_blocked:
            if is_registered_action:
                blocked.append({"action": action_name, "reason": "WAIT_ORIGINAL_OPERATION_RECONCILE"})
            return
        if requires_controller and not is_controller:
            if is_registered_action:
                blocked.append({"action": action_name, "reason": "not_controller"})
            return
        if requires_original_actor and requester_actor and principal.actor != requester_actor:
            if is_registered_action:
                blocked.append({"action": action_name, "reason": "not_controller"})
            return
        if requires_writer and not is_writer:
            if is_registered_action:
                blocked.append({"action": action_name, "reason": "not_writer_member"})
            return
        if not all(principal.allows(s) for s in req_scopes):
            if is_registered_action:
                blocked.append({"action": action_name, "reason": "missing_scope"})
            return
        # Quality policy belongs to the workspace owner, while conclusions
        # belong to the appointed reviewer, even when another reader is owner.
        if requires_owner and not _is_writer_member(ops, principal, workspace_id, decide=True):
            blocked.append({"action": action_name, "reason": "not_workspace_owner"})
            return
        if requires_reviewer and principal.actor != current_reviewer_actor:
            blocked.append({"action": action_name, "reason": "not_original_reviewer"})
            return
        allowed.append(entry)

    # 1. Operation lifecycle endpoints: api_v1.py:357-358 has route scope None; handlers call
    # operations._may_steer (operations.py:633-646) + pm_work_start._authorize_existing (pm_work_start.py:956-961),
    # which require operation_visible (observe scope), same_controller, workspace writer member (write=True),
    # and principal.actor == op["actor"] (satisfying op["actor"] == principal.actor at operations.py:644).
    work_start_steer_scopes = [READ_SCOPE]
    if op_status not in {"succeeded", "failed", "cancelled"}:
        consider(
            {
                "kind": "operation_endpoint",
                "endpoint": "cancel",
                "operation_id": start_op_id,
                "http_method": "POST",
                "http_path": f"/api/v1/operations/{start_op_id}/cancel",
                "required_scopes": work_start_steer_scopes,
            },
            action_name="cancel",
            requires_controller=True,
            requires_original_actor=True,
            requires_writer=True,
            reconcile_blocked=False,
        )

    if stage == "start_unknown" or op_status == "uncertain":
        if pm_work_start.ACTION in ops.actions:
            blocked.append({"action": pm_work_start.ACTION, "reason": "WAIT_ORIGINAL_OPERATION_RECONCILE"})
        # Publish stays blocked with the same reason, including before a session exists.
        if delivery_goal == "pull_request" and pr_publication.ACTION in ops.actions:
            blocked.append({"action": pr_publication.ACTION, "reason": "WAIT_ORIGINAL_OPERATION_RECONCILE"})
        return allowed, blocked, is_controller

    if op_status == "needs_attention":
        consider(
            {
                "kind": "operation_endpoint",
                "endpoint": "resume",
                "operation_id": start_op_id,
                "http_method": "POST",
                "http_path": f"/api/v1/operations/{start_op_id}/resume",
                "required_scopes": work_start_steer_scopes,
            },
            action_name="resume",
            requires_controller=True,
            requires_original_actor=True,
            requires_writer=True,
            reconcile_blocked=False,
        )

    # 2. Session & PM actions when a session exists
    if sess_host and sess_id:
        sess_target = {"host": sess_host, "session_id": sess_id}
        def offer_work_send(schedules: list[str]) -> None:
            if stage != "started" or "session.send" not in ops.actions:
                return
            agent = (ctx.get("intent") or {}).get("agent") or (view or {}).get("agent")
            if agent == "codex":
                blocked.append({"action": "session.send", "reason": "codex_uncorrelated_turn_boundary"})
                return
            consider(
                {
                    "kind": "action",
                    "action": "session.send",
                    "target": {"workspace_id": workspace_id, "work_item_id": work_item_id},
                    "required_scopes": _action_scopes(ops, "session.send"),
                    "allowed_schedules": list(schedules),
                    "default_schedule": "after_current",
                    "requires_controller": True,
                    "requires_writer": True,
                },
                action_name="session.send",
                requires_controller=True,
                requires_writer=True,
            )

        if execution_state == "waiting":
            if "session.answer" in ops.actions:
                consider(
                    {
                        "kind": "action",
                        "action": "session.answer",
                        "target": dict(sess_target),
                        "required_scopes": _action_scopes(ops, "session.answer"),
                    },
                    action_name="session.answer",
                    requires_controller=True,
                )
            offer_work_send(["after_current"])
        elif execution_state == "active":
            if "session.interrupt" in ops.actions:
                consider(
                    {
                        "kind": "action",
                        "action": "session.interrupt",
                        "target": dict(sess_target),
                        "required_scopes": _action_scopes(ops, "session.interrupt"),
                    },
                    action_name="session.interrupt",
                    requires_controller=True,
                )
            offer_work_send(["after_current"])
        elif execution_state == "turn-ended":
            offer_work_send(["after_current", "now"])
            if execution_op_id:
                if pm_execution_verification.ACTION in ops.actions:
                    consider(
                        {
                            "kind": "action",
                            "action": pm_execution_verification.ACTION,
                            "target": {
                                "workspace_id": workspace_id,
                                "execution_operation_id": execution_op_id,
                            },
                            "required_scopes": _action_scopes(
                                ops,
                                pm_execution_verification.ACTION,
                                pm_execution_verification.REQUIRED,
                            ),
                        },
                        action_name=pm_execution_verification.ACTION,
                        requires_controller=True,
                        requires_writer=True,
                    )
                if "session.record_verification" in ops.actions:
                    consider(
                        {
                            "kind": "action",
                            "action": "session.record_verification",
                            "target": dict(sess_target),
                            "required_scopes": _action_scopes(ops, "session.record_verification"),
                        },
                        action_name="session.record_verification",
                        requires_controller=True,
                    )

        if execution_state in {"turn-ended", "process-ended"}:
            if delivery_goal == "pull_request" and execution_op_id and pr_publication.ACTION in ops.actions:
                reason = _publication_block_reason(ops, ctx, candidate_sha)
                pair = None if reason else _publication_pair(ctx)
                if reason or pair is None:
                    blocked.append({
                        "action": pr_publication.ACTION,
                        "reason": reason or "publication_source_incomplete",
                    })
                else:
                    preconditions, base_ref = pair
                    repository = (ctx["code_source"] or {}).get("repository")
                    prefilled: dict[str, Any] = {"base_ref": base_ref}
                    if isinstance(candidate_sha, str) and SHA40.fullmatch(candidate_sha):
                        prefilled["candidate_sha"] = candidate_sha
                    consider(
                        {
                            "kind": "action",
                            "action": pr_publication.ACTION,
                            "target": {
                                "workspace_id": workspace_id,
                                "repository": repository,
                                "host": sess_host,
                                "source_operation_id": execution_op_id,
                            },
                            "required_scopes": _action_scopes(
                                ops,
                                pr_publication.ACTION,
                                pr_publication.REQUIRED,
                            ),
                            "required_params": [
                                "candidate_sha",
                                "base_ref",
                                "public_title",
                                "public_body",
                                "draft",
                            ],
                            "optional_params": ["quality_ref"],
                            "prefilled_params": prefilled,
                            "preconditions": preconditions,
                        },
                        action_name=pr_publication.ACTION,
                        requires_controller=True,
                        requires_writer=True,
                    )
            if has_receipt or execution_op_id:
                if "pm.quality.review.create" in ops.actions:
                    consider(
                        {
                            "kind": "action",
                            "action": "pm.quality.review.create",
                            "target": {
                                "workspace_id": workspace_id,
                                "work_item_id": work_item_id,
                            },
                            "required_scopes": _action_scopes(ops, "pm.quality.review.create"),
                        },
                        action_name="pm.quality.review.create",
                        requires_controller=False,
                        requires_writer=True,
                        requires_owner=True,
                    )
                if current_review_id is not None and "pm.quality.review.record" in ops.actions:
                    consider(
                        {
                            "kind": "action",
                            "action": "pm.quality.review.record",
                            "target": {
                                "workspace_id": workspace_id,
                                "review_id": current_review_id,
                            },
                            "required_scopes": _action_scopes(ops, "pm.quality.review.record"),
                        },
                        action_name="pm.quality.review.record",
                        requires_controller=False,
                        requires_writer=True,
                        requires_reviewer=True,
                    )

    return allowed, blocked, is_controller


def _find_related_event(
    ops,
    principal: Principal,
    after_cursor: int,
    related: list[tuple[str, str]],
    scan_cursors: dict[tuple[str, str], int],
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for rtype, rid in related:
        cur = scan_cursors.get((rtype, rid), after_cursor)
        for _ in range(WAIT_MAX_PAGES_PER_RESOURCE):
            try:
                page = dashboard_sync.event_page(
                    ops.journal,
                    principal,
                    cur,
                    WAIT_EVENT_PAGE_LIMIT,
                    resource_type=rtype,
                    resource_id=rid,
                )
            except dashboard_sync.ResetRequired as exc:
                raise _bad("DASHBOARD_RESET_REQUIRED", f"Event cursor reset required ({exc.reason})", 409) from exc
            matched_on_resource = None
            for ev in page.get("events") or []:
                if int(ev["seq"]) > after_cursor:
                    matched_on_resource = ev
                    break
            if matched_on_resource is not None:
                candidates.append(matched_on_resource)
                break
            next_cur = page.get("next_cursor")
            if next_cur is not None and int(next_cur) > cur:
                cur = int(next_cur)
                scan_cursors[(rtype, rid)] = cur
            if not page.get("has_more") or next_cur is None:
                break
    if not candidates:
        return None
    return min(candidates, key=lambda e: int(e["seq"]))


async def _wait_for_related_event(
    ops,
    principal: Principal,
    ctx: dict[str, Any],
    cursor: int | None,
    wait_ms: int,
) -> dict[str, Any]:
    related_set: set[tuple[str, str]] = {
        ("pm_work_attempt", ctx["attempt_id"]),
        ("operation", ctx["start_op_id"]),
    }
    if ctx["execution_op_id"]:
        related_set.add(("operation", ctx["execution_op_id"]))
    if ctx.get("latest_input_operation_id"):
        related_set.add(("operation", ctx["latest_input_operation_id"]))
    if ctx["sess_host"] and ctx["sess_id"]:
        related_set.add(("session", f"{ctx['sess_host']}/{ctx['sess_id']}"))
    related = sorted(related_set)

    scan_cursors: dict[tuple[str, str], int] = {}
    if cursor is not None:
        for res in related:
            scan_cursors[res] = cursor
        matched = _find_related_event(ops, principal, cursor, related, scan_cursors)
        if matched is not None:
            return {
                "requested_ms": wait_ms,
                "timed_out": False,
                "matched_seq": matched["seq"],
                "retry_after_ms": 0,
            }

    if wait_ms == 0:
        return {
            "requested_ms": 0,
            "timed_out": False,
            "retry_after_ms": 0,
        }

    after_cursor = ops.journal.api_head() if cursor is None else cursor
    if not scan_cursors:
        for res in related:
            scan_cursors[res] = after_cursor
    deadline = time.monotonic() + (wait_ms / 1000.0)
    while True:
        matched = _find_related_event(ops, principal, after_cursor, related, scan_cursors)
        if matched is not None:
            return {
                "requested_ms": wait_ms,
                "timed_out": False,
                "matched_seq": matched["seq"],
                "retry_after_ms": 0,
            }
        rem = deadline - time.monotonic()
        if rem <= 0:
            return {
                "requested_ms": wait_ms,
                "timed_out": True,
                "retry_after_ms": 1000,
            }
        await asyncio.sleep(min(WAIT_POLL_INTERVAL_S, rem))


async def _read_result(
    ops,
    principal: Principal,
    workspace_id: str,
    attempt_id: str,
    *,
    cursor: int | None = None,
    wait_ms: int = 0,
) -> dict[str, Any]:
    if cursor is not None and (type(cursor) is not int or not 0 <= cursor < 2**63):
        raise _bad("INVALID_PARAMS", "cursor must be a nonnegative integer", 422)
    if type(wait_ms) is not int or not 0 <= wait_ms <= WAIT_MS_MAX:
        raise _bad("INVALID_PARAMS", f"wait_ms must be an integer between 0 and {WAIT_MS_MAX}", 422)

    ctx = _attempt_context(ops, principal, workspace_id, attempt_id)
    wait_info = await _wait_for_related_event(ops, principal, ctx, cursor, wait_ms)
    current_head_cursor = ops.journal.api_head()
    ctx = _attempt_context(ops, principal, workspace_id, attempt_id)

    view = ctx["view"]
    work_item_id = ctx["work_item_id"]
    start_op_id = ctx["start_op_id"]
    execution_op_id = ctx["execution_op_id"]
    parent = ctx["parent"]
    intent = ctx["intent"]
    sess_host = ctx["sess_host"]
    sess_id = ctx["sess_id"]
    turn_marker = ctx["turn_marker"]
    base_sha = ctx["base_sha"]

    rec_data = pm_attempt_results.receipts(ops.db, workspace_id, attempt_id, limit=20)
    all_rows = rec_data["receipts"]
    latest_row = all_rows[0] if all_rows else None
    latest_doc = latest_row["document"] if (latest_row and isinstance(latest_row.get("document"), dict)) else {}

    current_receipt = None
    if turn_marker is not None:
        for r in all_rows:
            if r.get("turn_marker") == turn_marker:
                current_receipt = r
                break
    cur_doc = (
        current_receipt["document"]
        if (current_receipt and isinstance(current_receipt.get("document"), dict))
        else {}
    )

    host_row, obs_row, obs_body = _observed_inventory(ops, sess_host, sess_id)
    collection = _collector_status(ops, attempt_id)

    reasons: list[str] = []
    stage = view["stage"]
    op_status = view["operation_status"]
    work_start_ts = parent.get("updated_at") or view.get("created_at")
    ok_reg, reg_reason, _ = _check_managed_session_registry(ops, ctx)

    # Execution state synthesis (§4.1 & §4.2, with state_source + observed_at)
    if op_status == "failed" or stage == "start_failed":
        exec_state = "failed"
        state_source = "work_start"
        state_observed_at = work_start_ts
        state_reason = view.get("error_code") or "start_failed"
    elif op_status == "cancelled" and not (sess_host and sess_id):
        exec_state = "failed"
        state_source = "work_start"
        state_observed_at = work_start_ts
        state_reason = "start_cancelled"
    elif stage == "start_unknown" or op_status == "uncertain":
        exec_state = "unknown"
        state_source = "work_start"
        state_observed_at = work_start_ts
        state_reason = "handoff_outcome_uncertain"
        reasons.append("start_outcome_uncertain")
    elif stage in {"accepted", "preparing", "package_prepared"}:
        exec_state = "accepted"
        state_source = "work_start"
        state_observed_at = work_start_ts
        state_reason = view.get("error_code") if op_status == "needs_attention" else stage
        reasons.append("session_not_started_yet")
    elif stage == "starting" and not (sess_host and sess_id):
        exec_state = "starting"
        state_source = "work_start"
        state_observed_at = work_start_ts
        state_reason = view.get("error_code") if op_status == "needs_attention" else "handoff_starting"
        reasons.append("session_not_started_yet")
    elif not ok_reg and reg_reason == "session_not_managed_by_attempt":
        exec_state = "unknown"
        state_source = "collector_check" if collection.get("status") == "checked" else "work_start"
        state_observed_at = collection.get("checked_at") if collection.get("status") == "checked" else work_start_ts
        state_reason = "session_not_managed_by_attempt"
        reasons.append("session_not_managed_by_attempt")
    else:
        # Session exists on sess_host / sess_id
        host_unreachable = host_row is not None and int(host_row.get("reachable") or 0) == 0
        obs_seen_at = obs_row.get("last_seen_at") if obs_row is not None else None
        gone_at = obs_row.get("gone_at") if obs_row is not None else None
        missing_count = int(obs_row.get("missing_count") or 0) if obs_row is not None else 0
        fields_stale = bool(obs_body.get("fields_stale")) if obs_row is not None else False
        runtime_status = obs_body.get("runtime_status") if obs_row is not None else None
        pending_val = obs_body.get("pending") if obs_row is not None else None
        streaming_val = bool(obs_body.get("streaming")) if obs_row is not None else False
        loaded_val = obs_body.get("loaded") if obs_row is not None else None

        if host_unreachable or fields_stale or (missing_count > 0 and gone_at is None):
            r_code = "bat_unreachable" if host_unreachable else "observation_stale"
            exec_state = "unknown"
            state_source = "inventory_observation"
            state_observed_at = obs_seen_at or (host_row.get("last_success_at") if host_row else None) or work_start_ts
            state_reason = r_code
            reasons.append(r_code)
        elif gone_at is not None or runtime_status in {"exited", "stopped"}:
            exec_state = "process-ended"
            state_source = "inventory_observation"
            state_observed_at = gone_at or obs_seen_at or work_start_ts
            state_reason = "session_gone" if gone_at is not None else str(runtime_status)
        elif pending_val is not None:
            exec_state = "waiting"
            state_source = "inventory_observation"
            state_observed_at = obs_seen_at or work_start_ts
            state_reason = "pending_input"
        elif streaming_val or runtime_status in {"running", "streaming", "active"}:
            exec_state = "active"
            state_source = "inventory_observation"
            state_observed_at = obs_seen_at or work_start_ts
            state_reason = "streaming" if streaming_val else str(runtime_status)
        elif loaded_val is True and runtime_status == "starting":
            exec_state = "starting"
            state_source = "inventory_observation"
            state_observed_at = obs_seen_at or work_start_ts
            state_reason = "starting"
        elif loaded_val is False:
            exec_state = "unknown"
            state_source = "inventory_observation"
            state_observed_at = obs_seen_at or work_start_ts
            state_reason = "unloaded_history_session"
            reasons.append("unloaded_history_session")
        elif current_receipt is not None:
            if (cur_doc.get("boundary") or cur_doc.get("boundary_reason")) == "agent_error":
                exec_state = "failed"
                state_source = "result_receipt"
                state_observed_at = current_receipt["observed_at"]
                state_reason = "agent_error"
            else:
                exec_state = "turn-ended"
                state_source = "result_receipt"
                state_observed_at = current_receipt["observed_at"]
                state_reason = "turn_done"
        else:
            if collection.get("outcome") == "turn_marker_not_visible":
                exec_state = "unknown"
                state_source = "collector_check"
                state_observed_at = collection.get("checked_at")
                state_reason = "turn_marker_not_visible"
                reasons.append("turn_marker_not_visible")
            else:
                exec_state = "unknown"
                if collection.get("status") == "checked":
                    state_source = "collector_check"
                    state_observed_at = collection.get("checked_at")
                elif obs_row is not None:
                    state_source = "inventory_observation"
                    state_observed_at = obs_seen_at
                else:
                    state_source = "work_start"
                    state_observed_at = work_start_ts
                state_reason = "turn_boundary_not_collected"
                reasons.append("turn_boundary_not_collected")

    if collection.get("outcome") == "turn_marker_not_visible" and "turn_marker_not_visible" not in reasons:
        reasons.append("turn_marker_not_visible")

    attention = (
        {
            "code": view.get("error_code"),
            "message": view.get("status_reason"),
            "resumable": False,
        }
        if op_status == "needs_attention"
        else None
    )

    # Conversation section (bounded projection from current_receipt only)
    if current_receipt is not None:
        turn_prog = cur_doc.get("turn_progress") if isinstance(cur_doc.get("turn_progress"), dict) else {}
        raw_started = cur_doc.get("turn_started")
        if raw_started is None:
            raw_started = turn_prog.get("turn_started")
        turn_started: bool | str = raw_started if isinstance(raw_started, bool) else "unknown"

        raw_done = cur_doc.get("turn_done")
        if raw_done is None:
            raw_done = turn_prog.get("turn_done")
        turn_done: bool | str = raw_done if isinstance(raw_done, bool) else "unknown"

        raw_phase = cur_doc.get("turn_phase")
        if raw_phase is None:
            raw_phase = turn_prog.get("turn_phase")
        turn_phase: str = str(raw_phase) if isinstance(raw_phase, str) and raw_phase else "unknown"

        raw_attr = cur_doc.get("turn_attribution")
        if raw_attr is None:
            raw_attr = turn_prog.get("turn_attribution")
        turn_attribution: str = str(raw_attr) if isinstance(raw_attr, str) and raw_attr else "unknown"

        latest_assistant_summary = cur_doc.get("latest_assistant_summary")
        messages_preview = list(cur_doc.get("messages_preview") or [])
        tool_activity = list(cur_doc.get("tool_activity") or [])
        streaming_text_tail = cur_doc.get("streaming_text_tail") if turn_started is True else None
        transcript_meta = cur_doc.get("transcript")
    else:
        turn_started = "unknown"
        turn_done = "unknown"
        turn_phase = "unknown"
        turn_attribution = "unknown"
        latest_assistant_summary = None
        messages_preview = []
        tool_activity = []
        streaming_text_tail = None
        transcript_meta = None

    transcript_read = (
        {
            "http": f"/api/v1/sessions/{sess_host}/{sess_id}/messages?after={turn_marker}",
            "rpc": "session_read",
            "mcp": "session_read",
            "after": turn_marker,
        }
        if (sess_host and sess_id and turn_marker)
        else None
    )

    # Artifact references / attachments for the work item
    result_artifacts: list[dict[str, Any]] = []
    if work_item_id:
        try:
            wi_res = work_item_results.read(ops, principal, work_item_id)
            wi_item = wi_res.get("item") if isinstance(wi_res.get("item"), dict) else {}
            result_artifacts = list(wi_item.get("result_artifacts") or [])
        except OperationError:
            result_artifacts = []

    # Changes section from latest receipt
    if latest_row is not None:
        receipt_head = latest_row.get("head_commit")
        live_head_commit = receipt_head if (isinstance(receipt_head, str) and SHA40.fullmatch(receipt_head)) else "unknown"
        head_freshness = "cached_receipt" if live_head_commit != "unknown" else "unavailable"
        uncommitted = copy.deepcopy(
            latest_doc.get("uncommitted")
            or {
                "count": "unknown",
                "files": "unknown",
                "evidence": "unproven",
                "reason": "bat_empty_status_not_clean_proof",
            }
        )
        changes_summary = copy.deepcopy(
            latest_doc.get("changes_summary")
            or {
                "aggregation": "per_commit",
                "commits": [],
                "files": [],
                "additions": "unknown",
                "deletions": "unknown",
                "unavailable": [],
                "complete": False,
                "reason": "no_receipt_changes_summary",
                "cached_from_result_id": latest_row["result_id"],
            }
        )
        receipt_fixed_commit = copy.deepcopy(latest_doc.get("fixed_result_commit"))
    else:
        live_head_commit = "unknown"
        head_freshness = "unavailable"
        uncommitted = {
            "count": "unknown",
            "files": "unknown",
            "evidence": "unproven",
            "reason": "session_not_started_yet" if not (sess_host and sess_id) else "turn_boundary_not_collected",
        }
        changes_summary = {
            "aggregation": "per_commit",
            "commits": [],
            "files": [],
            "additions": "unknown",
            "deletions": "unknown",
            "unavailable": [],
            "complete": False,
            "reason": "session_not_started_yet" if not (sess_host and sess_id) else "turn_boundary_not_collected",
            "cached_from_result_id": None,
        }
        receipt_fixed_commit = None

    target_sha = (
        receipt_fixed_commit.get("commit_sha")
        if isinstance(receipt_fixed_commit, dict) and isinstance(receipt_fixed_commit.get("commit_sha"), str)
        else (live_head_commit if live_head_commit != "unknown" else None)
    )

    # Verification, Quality Reviews & Delivery
    raw_delivery = view.get("delivery") or intent.get("delivery")
    delivery_goal = str(raw_delivery) if isinstance(raw_delivery, str) and raw_delivery else "unknown"
    repository = (ctx["code_source"] or {}).get("repository")
    verification_block, ver_facet, review_decision, acceptance_facet, ver_commit, ver_coverage = _verification_section(
        ops,
        principal,
        workspace_id,
        work_item_id,
        start_op_id,
        execution_op_id,
        sess_host,
        sess_id,
        target_sha,
    )
    delivery_block, merge_facet, deployment_facet, integ_commit, deliv_coverage = _delivery_section(
        ops,
        principal,
        workspace_id,
        work_item_id,
        execution_op_id,
        delivery_goal,
        repository,
        target_sha,
    )

    # Priority order for fixed_result_commit (§3.2):
    # 1. integration, 2. execution_verification, 3. session_verification, 4. turn_end_observation
    uncomm_count = uncommitted.get("count", "unknown") if isinstance(uncommitted, dict) else "unknown"
    if integ_commit is not None:
        fixed_result_commit = {**integ_commit, "uncommitted": uncomm_count}
    elif ver_commit is not None:
        fixed_result_commit = {**ver_commit, "uncommitted": uncomm_count}
    else:
        fixed_result_commit = receipt_fixed_commit

    cand_sha_for_pr = (
        (receipt_fixed_commit or {}).get("commit_sha")
        if isinstance(receipt_fixed_commit, dict)
        else (fixed_result_commit.get("commit_sha") if isinstance(fixed_result_commit, dict) else None)
    )
    current_review_obj = verification_block["quality_reviews"]["current"]
    current_review_id = current_review_obj.get("review_id") if isinstance(current_review_obj, dict) else None
    current_review = next((row for row in verification_block["quality_reviews"]["reviews"]
                           if isinstance(row, dict) and row.get("review_id") == current_review_id), None)
    current_reviewer_actor = (current_review.get("context") or {}).get("reviewer_actor") if current_review else None
    next_allowed_actions, blocked_actions, is_controller = _compute_actions(
        ops,
        principal,
        ctx,
        execution_state=exec_state,
        delivery_goal=delivery_goal,
        candidate_sha=cand_sha_for_pr,
        has_receipt=latest_row is not None,
        current_review_id=current_review_id,
        current_reviewer_actor=current_reviewer_actor,
    )
    # Same proof and authorization as the public resume endpoint. An unknown
    # child returns before resume is considered, so this stays false.
    if attention is not None:
        attention["resumable"] = any(
            item.get("kind") == "operation_endpoint" and item.get("endpoint") == "resume"
            for item in next_allowed_actions
        )

    # Identity & Sources
    reg_entry = registry.get(sess_host, sess_id) if (sess_host and sess_id) else None
    reg_origin = None
    if isinstance(reg_entry, dict):
        reg_origin = {
            "controller_incarnation": reg_entry.get("controller_incarnation"),
            "created_at": reg_entry.get("created_at"),
            "start_operation_id": reg_entry.get("start_operation_id"),
        }
    elif isinstance(obs_body.get("registry_origin"), dict):
        reg_origin = obs_body["registry_origin"]

    raw_meta = latest_doc.get("meta") or latest_doc.get("session_meta")
    sess_meta = raw_meta if isinstance(raw_meta, dict) else {}
    sdk_session_id = sess_meta.get("sdk_session_id") or obs_body.get("sdk_session_id")
    agent_kind = sess_meta.get("agent_kind") or intent.get("agent") or "unknown"
    model_name = sess_meta.get("model") or obs_body.get("model") or intent.get("model") or "unknown"

    raw_ctrl = ctx["controller"]
    public_controller = (
        {
            "server_id": raw_ctrl.get("server_id"),
            "controller_id": raw_ctrl.get("controller_id"),
        }
        if isinstance(raw_ctrl, dict)
        else None
    )
    identity_block = {
        "server_id": pm_workspace.server_id(ops.db),
        "workspace_id": workspace_id,
        "project_id": view.get("project_id"),
        "work_item_id": work_item_id,
        "attempt_id": attempt_id,
        "predecessor_attempt_id": view.get("predecessor_attempt_id"),
        "start_operation_id": start_op_id,
        "execution_operation_id": execution_op_id,
        "binding_id": view.get("binding_id"),
        "package_id": view.get("package_id"),
        "scope_revision": (intent.get("plan_ref") or {}).get("scope_revision"),
        "requester_actor": parent.get("actor"),
        "controller": public_controller,
        "caller_is_controller": is_controller,
        "adapter": {
            "kind": "bat",
            "instance_id": sess_host or (ctx["code_source"] or {}).get("host"),
            "agent_kind": agent_kind,
            "model": model_name,
        },
        "native_session": {
            "host": sess_host,
            "session_id": sess_id,
            "sdk_session_id": sdk_session_id,
            "registry_origin": reg_origin,
        },
    }

    source_key = (
        latest_row["source_key"]
        if latest_row is not None
        else (f"bat:{sess_host}:{sess_id}" if (sess_host and sess_id) else None)
    )
    source_gen = (
        latest_row["source_generation"]
        if latest_row is not None
        else (
            pm_attempt_results.source_generation_from_registry(sess_host, sess_id, reg_entry)
            if (sess_host and sess_id)
            else "unknown"
        )
    )
    sources_block = {
        "result_id": latest_row["result_id"] if latest_row is not None else None,
        "collector_actor": "connector" if latest_row is not None else None,
        "source_key": source_key,
        "source_uri": f"bat://{sess_host}/sessions/{sess_id}" if (sess_host and sess_id) else None,
        "source_generation": source_gen,
        "source_position": {
            "turn_marker": turn_marker,
            "last_message_id": latest_row["last_message_id"] if latest_row is not None else None,
        },
        "decoder_version": latest_doc.get("decoder_version") or DECODER_VERSION,
        "digest": latest_row["digest"] if latest_row is not None else None,
        "session_digest": obs_row["digest"] if (obs_row and obs_row.get("digest")) else "unknown",
        "observed_at": (
            latest_row["observed_at"]
            if latest_row is not None
            else (obs_row.get("last_seen_at") if obs_row is not None else None)
        ),
        "collected_at": latest_row["collected_at"] if latest_row is not None else None,
    }

    conversation_block = {
        "turn_marker": turn_marker,
        "turn_started": turn_started,
        "turn_done": turn_done,
        "turn_phase": turn_phase,
        "turn_attribution": turn_attribution,
        "latest_assistant_summary": latest_assistant_summary,
        "messages_preview": messages_preview,
        "tool_activity": tool_activity,
        "streaming_text_tail": streaming_text_tail,
        "attachments": result_artifacts,
        "transcript": transcript_meta,
        "transcript_read": transcript_read,
    }

    pending_out = (
        obs_body.get("pending")
        if (obs_row is not None and obs_body.get("pending") is not None)
        else None
    )
    self_reported = _self_reported_facet(
        cur_doc if current_receipt is not None else None,
        latest_assistant_summary=latest_assistant_summary,
        turn_done=turn_done,
    )
    execution_block = {
        "state": exec_state,
        "state_source": state_source,
        "observed_at": state_observed_at,
        "state_reason": state_reason,
        "stage": stage,
        "operation_status": op_status,
        "attention": attention,
        "pending": pending_out,
        "exit_code": "unknown",
        "exit_code_reason": "not_exposed_by_bat",
        "state_facets": {
            "self_reported": self_reported,
            "verification": ver_facet,
            "review_decision": review_decision,
            "acceptance": acceptance_facet,
            "merge": merge_facet,
            "deployment": deployment_facet,
        },
    }

    changes_block = {
        "base_commit": base_sha,
        "live_head_commit": live_head_commit,
        "head_freshness": head_freshness,
        "fixed_result_commit": fixed_result_commit,
        "uncommitted": uncommitted,
        "summary": changes_summary,
        "result_artifacts": result_artifacts,
    }

    usage_doc = latest_doc.get("usage") if isinstance(latest_doc.get("usage"), dict) else {}
    usage_block = {
        "context_tokens": usage_doc.get("context_tokens", "unknown"),
        "context_window": usage_doc.get("context_window", "unknown"),
        "num_turns": usage_doc.get("num_turns", "unknown"),
        "input_tokens": "unknown",
        "output_tokens": "unknown",
        "total_tokens": "unknown",
        "quota": "unknown",
        "duration_seconds": "unknown",
        "duration_reason": "not_exposed_by_bat",
    }

    # Completeness reasons & coverage (§7)
    doc_cov = latest_doc.get("coverage") if isinstance(latest_doc.get("coverage"), dict) else {}
    for r_code in doc_cov.get("reasons") or []:
        if isinstance(r_code, str) and r_code not in reasons:
            reasons.append(r_code)
    if isinstance(uncommitted, dict) and uncommitted.get("count") == "unknown":
        u_reason = uncommitted.get("reason")
        if isinstance(u_reason, str) and u_reason and u_reason not in reasons:
            reasons.append(u_reason)
    if isinstance(changes_summary, dict):
        cs_reasons = changes_summary.get("reasons")
        if isinstance(cs_reasons, list) and cs_reasons:
            for r_code in cs_reasons:
                if (
                    isinstance(r_code, str)
                    and r_code
                    and r_code not in {"no_receipt_changes_summary"}
                    and r_code not in reasons
                ):
                    reasons.append(r_code)
        else:
            if changes_summary.get("unavailable") and not changes_summary.get("reason"):
                if "diff_exceeded_pipe_limit" not in reasons:
                    reasons.append("diff_exceeded_pipe_limit")
            cs_reason = changes_summary.get("reason")
            if (
                isinstance(cs_reason, str)
                and cs_reason
                and cs_reason not in {"no_receipt_changes_summary"}
                and cs_reason not in reasons
            ):
                reasons.append(cs_reason)

    has_conflict = False
    if latest_row is not None and pm_attempt_results.installed(ops.db):
        target_ver = str(latest_doc.get("decoder_version") or DECODER_VERSION)
        pos_rows = ops.db.execute(
            """SELECT digest, document FROM session_results
               WHERE workspace_id=? AND source_key=? AND source_generation=? AND source_position=? AND digest!=?""",
            (
                workspace_id,
                latest_row["source_key"],
                latest_row["source_generation"],
                latest_row["source_position_json"],
                latest_row["digest"],
            ),
        ).fetchall()
        for pr in pos_rows:
            try:
                pdoc = json.loads(pr["document"])
            except (TypeError, ValueError):
                pdoc = {}
            pver = str((pdoc.get("decoder_version") if isinstance(pdoc, dict) else None) or DECODER_VERSION)
            if pver == target_ver:
                has_conflict = True
                break
    if has_conflict and "source_digest_conflict" not in reasons:
        reasons.append("source_digest_conflict")

    doc_truncated = bool(doc_cov.get("document_truncated", False))
    if doc_truncated and "result_document_bounded" not in reasons:
        reasons.append("result_document_bounded")

    if latest_row is None and (
        (not (sess_host and sess_id) and exec_state == "failed")
        or ("session_not_managed_by_attempt" in reasons)
    ):
        comp_status = "unavailable"
    elif (
        stage == "started"
        and obs_row is not None
        and obs_body.get("loaded") is True
        and current_receipt is not None
        and bool(changes_summary.get("complete"))
        and not changes_summary.get("unavailable")
        and ver_coverage == "complete"
        and deliv_coverage == "complete"
        and not has_conflict
        and not doc_truncated
        and not reasons
    ):
        comp_status = "complete"
    else:
        comp_status = "partial"

    last_obs = (
        {
            "observed_at": latest_row["observed_at"],
            "collected_at": latest_row["collected_at"],
            "result_id": latest_row["result_id"],
            "source_position": {
                "turn_marker": latest_row["turn_marker"],
                "last_message_id": latest_row["last_message_id"],
            },
            "fixed_result_commit": receipt_fixed_commit,
        }
        if latest_row is not None
        else None
    )

    completeness_block = {
        "status": comp_status,
        "reasons": reasons,
        "coverage": {
            "conversation": "complete" if (current_receipt is not None and not doc_truncated) else ("partial" if current_receipt is not None else "unavailable"),
            "execution": "complete" if exec_state not in {"unknown"} else "partial",
            "changes": "complete" if (latest_row is not None and bool(changes_summary.get("complete")) and not changes_summary.get("unavailable")) else ("partial" if latest_row is not None else "unavailable"),
            "verification": ver_coverage,
            "delivery": deliv_coverage,
            "document_truncated": doc_truncated,
            "transcript_window": transcript_meta,
            "changes_window": {
                "commits_scanned": len(changes_summary.get("commits") or []),
                "files_included": len(changes_summary.get("files") or []),
                "unavailable_files": list(changes_summary.get("unavailable") or []),
                "complete": bool(changes_summary.get("complete", False)),
            },
        },
        "last_successful_observation": last_obs,
    }

    return {
        "contract": CONTRACT,
        "workspace_id": workspace_id,
        "attempt_id": attempt_id,
        "cursor": current_head_cursor,
        "events_read": {
            "http": f"/api/v1/events?after={current_head_cursor}&resource_type=pm_work_attempt&resource_id={attempt_id}",
            "rpc": "api_events",
            "mcp": "events_list",
            "resource_type": "pm_work_attempt",
            "resource_id": attempt_id,
        },
        "wait": wait_info,
        "collection": collection,
        "results": rec_data["results"],
        "results_has_more": rec_data["has_more"],
        "identity": identity_block,
        "sources": sources_block,
        "conversation": conversation_block,
        "execution": execution_block,
        "changes": changes_block,
        "verification": verification_block,
        "requirements": pm_result_requirements.read(ops, principal, workspace_id, execution_op_id,
            (receipt_fixed_commit or fixed_result_commit or {}).get("commit_sha"), verification_block["status"],
            verification_evidence=verification_block["runs"]),
        "delivery": delivery_block,
        "usage": usage_block,
        "completeness": completeness_block,
        "next_allowed_actions": next_allowed_actions,
        "blocked_actions": blocked_actions,
        "links": _links(workspace_id, work_item_id, start_op_id, execution_op_id, sess_host, sess_id),
    }


async def _compute_changes_live(
    ops,
    *,
    ctx: dict[str, Any],
    max_commits: int,
    max_files: int,
    latest_row: dict[str, Any] | None,
    head_cursor: int,
) -> dict[str, Any]:
    workspace_id = ctx["workspace_id"]
    attempt_id = ctx["attempt_id"]
    work_item_id = ctx["work_item_id"]
    start_op_id = ctx["start_op_id"]
    execution_op_id = ctx["execution_op_id"]
    sess_host = ctx["sess_host"]
    sess_id = ctx["sess_id"]
    worktree_path = ctx["worktree_path"]
    base_sha = ctx["base_sha"] or ""

    latest_doc = latest_row["document"] if (latest_row and isinstance(latest_row.get("document"), dict)) else {}
    receipt_fixed_commit = copy.deepcopy(latest_doc.get("fixed_result_commit"))
    reasons: list[str] = []

    client = checkpoints._read_fleet(ops).client(sess_host)
    started = time.monotonic()

    try:
        rem = max(0.001, CHANGESET_TOTAL_BUDGET_S - (time.monotonic() - started))
        log_raw = await asyncio.wait_for(
            client.invoke("git:log", {"cwd": worktree_path, "count": 1}),
            timeout=rem,
        )
        head_sha = None
        if isinstance(log_raw, list) and log_raw and isinstance(log_raw[0], dict):
            cand = str(log_raw[0].get("hash") or "")
            if SHA40.fullmatch(cand):
                head_sha = cand
        if head_sha is None:
            raise BatError("WORKTREE_UNREACHABLE", "git:log returned no valid HEAD commit")

        rem = max(0.001, CHANGESET_TOTAL_BUDGET_S - (time.monotonic() - started))
        uncommitted = await asyncio.wait_for(
            pm_attempt_results.collect_uncommitted(client, worktree_path),
            timeout=rem,
        )
    except ConnectionLost:
        reasons.append("bat_unreachable")
        return _fallback_changes_response(
            ops,
            ctx,
            latest_row=latest_row,
            reasons=reasons,
            status="partial" if latest_row is not None else "unavailable",
            head_cursor=head_cursor,
        )
    except (BatError, InvokeTimeout, asyncio.TimeoutError):
        reasons.append("worktree_unreachable")
        return _fallback_changes_response(
            ops,
            ctx,
            latest_row=latest_row,
            reasons=reasons,
            status="partial" if latest_row is not None else "unavailable",
            head_cursor=head_cursor,
        )

    # Check if live_head matches receipt or per-daemon LRU memo
    memo_hit = False
    summary = _memo_get(ops, attempt_id, base_sha, head_sha, max_commits, max_files)
    if summary is not None:
        memo_hit = True
    elif (
        latest_row is not None
        and latest_row.get("head_commit") == head_sha
        and max_commits == BRANCH_DIFF_MAX_COMMITS
        and max_files == BRANCH_DIFF_MAX_FILES
        and isinstance(latest_doc.get("changes_summary"), dict)
        and latest_doc["changes_summary"].get("reason") is None
    ):
        summary = copy.deepcopy(latest_doc["changes_summary"])
        _memo_put(ops, attempt_id, base_sha, head_sha, max_commits, max_files, summary)
        memo_hit = True
    else:
        rem = max(0.001, CHANGESET_TOTAL_BUDGET_S - (time.monotonic() - started))
        summary = await pm_attempt_results.collect_changes_summary(
            client,
            worktree_path,
            base_sha=base_sha,
            result_id=latest_row["result_id"] if latest_row is not None else None,
            max_commits=max_commits,
            max_files=max_files,
            budget_s=rem,
        )
        if summary.get("reason") is None:
            _memo_put(ops, attempt_id, base_sha, head_sha, max_commits, max_files, summary)

    if isinstance(uncommitted, dict) and uncommitted.get("count") == "unknown":
        u_reason = uncommitted.get("reason")
        if isinstance(u_reason, str) and u_reason and u_reason not in reasons:
            reasons.append(u_reason)
    s_reasons = summary.get("reasons")
    if isinstance(s_reasons, list) and s_reasons:
        for r_code in s_reasons:
            if isinstance(r_code, str) and r_code and r_code not in reasons:
                reasons.append(r_code)
    else:
        if summary.get("unavailable") and not summary.get("reason"):
            if "diff_exceeded_pipe_limit" not in reasons:
                reasons.append("diff_exceeded_pipe_limit")
        s_reason = summary.get("reason")
        if isinstance(s_reason, str) and s_reason and s_reason not in reasons:
            reasons.append(s_reason)

    comp_status = "complete" if (bool(summary.get("complete")) and not summary.get("unavailable") and not reasons) else "partial"
    return {
        "workspace_id": workspace_id,
        "attempt_id": attempt_id,
        "base_commit": base_sha or None,
        "live_head_commit": head_sha,
        "head_freshness": "live",
        "memo_hit": memo_hit,
        "fixed_result_commit": receipt_fixed_commit,
        "uncommitted": uncommitted,
        "summary": summary,
        "completeness": {
            "status": comp_status,
            "reasons": reasons,
        },
        "cursor": head_cursor,
        "links": _links(workspace_id, work_item_id, start_op_id, execution_op_id, sess_host, sess_id),
    }


def _fallback_changes_response(
    ops,
    ctx: dict[str, Any],
    *,
    latest_row: dict[str, Any] | None,
    reasons: list[str],
    status: str,
    head_cursor: int,
) -> dict[str, Any]:
    workspace_id = ctx["workspace_id"]
    attempt_id = ctx["attempt_id"]
    work_item_id = ctx["work_item_id"]
    start_op_id = ctx["start_op_id"]
    execution_op_id = ctx["execution_op_id"]
    sess_host = ctx["sess_host"]
    sess_id = ctx["sess_id"]
    base_sha = ctx["base_sha"]

    latest_doc = latest_row["document"] if (latest_row and isinstance(latest_row.get("document"), dict)) else {}
    if latest_row is not None:
        r_head = latest_row.get("head_commit")
        live_head = r_head if (isinstance(r_head, str) and SHA40.fullmatch(r_head)) else "unknown"
        freshness = "cached_receipt" if live_head != "unknown" else "unavailable"
        uncommitted = copy.deepcopy(
            latest_doc.get("uncommitted")
            or {
                "count": "unknown",
                "files": "unknown",
                "evidence": "unproven",
                "reason": reasons[0] if reasons else "worktree_unreachable",
            }
        )
        summary = copy.deepcopy(
            latest_doc.get("changes_summary")
            or {
                "aggregation": "per_commit",
                "commits": [],
                "files": [],
                "additions": "unknown",
                "deletions": "unknown",
                "unavailable": [],
                "complete": False,
                "reason": reasons[0] if reasons else "worktree_unreachable",
                "cached_from_result_id": latest_row["result_id"],
            }
        )
        fixed_commit = copy.deepcopy(latest_doc.get("fixed_result_commit"))
    else:
        live_head = "unknown"
        freshness = "unavailable"
        uncommitted = {
            "count": "unknown",
            "files": "unknown",
            "evidence": "unproven",
            "reason": reasons[0] if reasons else "session_not_managed_by_attempt",
        }
        summary = {
            "aggregation": "per_commit",
            "commits": [],
            "files": [],
            "additions": "unknown",
            "deletions": "unknown",
            "unavailable": [],
            "complete": False,
            "reason": reasons[0] if reasons else "session_not_managed_by_attempt",
            "cached_from_result_id": None,
        }
        fixed_commit = None

    return {
        "workspace_id": workspace_id,
        "attempt_id": attempt_id,
        "base_commit": base_sha,
        "live_head_commit": live_head,
        "head_freshness": freshness,
        "memo_hit": False,
        "fixed_result_commit": fixed_commit,
        "uncommitted": uncommitted,
        "summary": summary,
        "completeness": {
            "status": status,
            "reasons": reasons,
        },
        "cursor": head_cursor,
        "links": _links(workspace_id, work_item_id, start_op_id, execution_op_id, sess_host, sess_id),
    }


async def _read_changes(
    ops,
    principal: Principal,
    workspace_id: str,
    attempt_id: str,
    *,
    max_commits: int = BRANCH_DIFF_MAX_COMMITS,
    max_files: int = BRANCH_DIFF_MAX_FILES,
) -> dict[str, Any]:
    if type(max_commits) is not int or not 1 <= max_commits <= BRANCH_DIFF_MAX_COMMITS:
        raise _bad("INVALID_PARAMS", f"max_commits must be an integer between 1 and {BRANCH_DIFF_MAX_COMMITS}", 422)
    if type(max_files) is not int or not 1 <= max_files <= BRANCH_DIFF_MAX_FILES:
        raise _bad("INVALID_PARAMS", f"max_files must be an integer between 1 and {BRANCH_DIFF_MAX_FILES}", 422)

    head_cursor = ops.journal.api_head()
    ctx = _attempt_context(ops, principal, workspace_id, attempt_id)
    rec_data = pm_attempt_results.receipts(ops.db, workspace_id, attempt_id, limit=1)
    latest_row = rec_data["receipts"][0] if rec_data["receipts"] else None

    ok_reg, reg_reason, _ = _check_managed_session_registry(ops, ctx)
    if not ok_reg:
        # Make zero BAT calls when registry check fails
        return _fallback_changes_response(
            ops,
            ctx,
            latest_row=None,
            reasons=[reg_reason],
            status="unavailable",
            head_cursor=head_cursor,
        )

    inflight = _inflight_changes(ops)
    flight_key = (attempt_id, max_commits, max_files)
    task = inflight.get(flight_key)
    if task is None or task.done():
        task = asyncio.create_task(
            _compute_changes_live(
                ops,
                ctx=ctx,
                max_commits=max_commits,
                max_files=max_files,
                latest_row=latest_row,
                head_cursor=head_cursor,
            )
        )
        inflight[flight_key] = task
    try:
        return copy.deepcopy(await asyncio.shield(task))
    finally:
        if inflight.get(flight_key) is task and task.done():
            inflight.pop(flight_key, None)


async def _read_diff(
    ops,
    principal: Principal,
    workspace_id: str,
    attempt_id: str,
    *,
    commit: str | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    ctx = _attempt_context(ops, principal, workspace_id, attempt_id)
    ok_reg, _, _ = _check_managed_session_registry(ops, ctx)
    if not ok_reg:
        raise _bad("PM_DIFF_NOT_FOUND", "Session is not managed by this attempt", 404)

    if not isinstance(commit, str) or not SHA40.fullmatch(commit):
        raise _bad("PM_DIFF_NOT_FOUND", "Commit must be a 40-character hex SHA in (base_commit..head_commit]", 404)
    if (
        not isinstance(path, str)
        or not path.strip()
        or path.startswith("/")
        or ".." in PurePosixPath(path).parts
    ):
        raise _bad("PM_DIFF_NOT_FOUND", "Path must be a listed relative file path in the commit", 404)

    base_sha = ctx["base_sha"]
    if not isinstance(base_sha, str) or not SHA40.fullmatch(base_sha) or commit == base_sha:
        raise _bad("PM_DIFF_NOT_FOUND", "Commit is not in (base_commit..head_commit]", 404)

    sess_host = ctx["sess_host"]
    worktree_path = ctx["worktree_path"]
    client = checkpoints._read_fleet(ops).client(sess_host)
    started = time.monotonic()

    try:
        rem = max(0.001, CHANGESET_TOTAL_BUDGET_S - (time.monotonic() - started))
        log_raw = await asyncio.wait_for(
            client.invoke("git:log", {"cwd": worktree_path, "count": BRANCH_DIFF_MAX_COMMITS + 1}),
            timeout=rem,
        )
    except (BatError, ConnectionLost, InvokeTimeout, asyncio.TimeoutError) as exc:
        raise _bad("PM_DIFF_UNAVAILABLE", "Worktree diff could not be read from BAT", 502) from exc

    if not isinstance(log_raw, list) or not log_raw:
        raise _bad("PM_DIFF_NOT_FOUND", "Commit history is empty", 404)

    in_range_hashes: set[str] = set()
    found_base = False
    for idx, entry in enumerate(log_raw):
        if not isinstance(entry, dict):
            continue
        h = str(entry.get("hash") or "")
        if not SHA40.fullmatch(h):
            continue
        if h == base_sha:
            found_base = True
            break
        if idx < BRANCH_DIFF_MAX_COMMITS:
            in_range_hashes.add(h)

    if not found_base or commit not in in_range_hashes:
        raise _bad("PM_DIFF_NOT_FOUND", "Commit is outside the attempt's scanned (base_commit..head_commit] range", 404)

    try:
        rem = max(0.001, CHANGESET_TOTAL_BUDGET_S - (time.monotonic() - started))
        df_raw = await asyncio.wait_for(
            client.invoke("git:diff-files", {"cwd": worktree_path, "commitHash": commit}),
            timeout=rem,
        )
    except (BatError, ConnectionLost, InvokeTimeout, asyncio.TimeoutError) as exc:
        raise _bad("PM_DIFF_UNAVAILABLE", "Commit diff-files could not be read from BAT", 502) from exc

    file_status = None
    for df in df_raw or []:
        if isinstance(df, dict) and df.get("file") == path:
            file_status = str(df.get("status") or "M")
            break
    if file_status is None:
        raise _bad("PM_DIFF_NOT_FOUND", "Path is not in git:diff-files for the specified commit", 404)

    try:
        rem = max(0.001, CHANGESET_TOTAL_BUDGET_S - (time.monotonic() - started))
        raw_diff = await asyncio.wait_for(
            client.invoke("git:diff", {"cwd": worktree_path, "commitHash": commit, "filePath": path}),
            timeout=rem,
        )
    except (BatError, ConnectionLost, InvokeTimeout, asyncio.TimeoutError) as exc:
        raise _bad("PM_DIFF_UNAVAILABLE", "File diff could not be read from BAT", 502) from exc

    if not isinstance(raw_diff, str) or raw_diff == "":
        return {
            "workspace_id": workspace_id,
            "attempt_id": attempt_id,
            "commit": commit,
            "path": path,
            "status": file_status,
            "available": False,
            "reason": "diff_exceeded_pipe_limit",
            "diff": None,
            "truncated": False,
            "stats": {"additions": "unknown", "deletions": "unknown"},
        }

    st = orchestrate.diff_stats(raw_diff)
    diff_text, truncated = _truncate_utf8(raw_diff, MAX_DIFF_BYTES)
    return {
        "workspace_id": workspace_id,
        "attempt_id": attempt_id,
        "commit": commit,
        "path": path,
        "status": file_status,
        "available": True,
        "reason": None,
        "diff": diff_text,
        "truncated": truncated,
        "stats": {
            "additions": int(st.get("additions", 0)),
            "deletions": int(st.get("deletions", 0)),
        },
    }


async def read(ops, principal: Principal, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if not principal.allows(READ_SCOPE):
        raise _bad("FORBIDDEN", "Work attempt result reads require observe scope", 403)
    if method not in FIELDS or set(params) - set(FIELDS[method]):
        raise _bad("INVALID_PARAMS", "Unknown work attempt result read parameters", 422)

    workspace_id = params.get("workspace_id")
    attempt_id = params.get("attempt_id")
    if method == RESULT_GET:
        return await _read_result(
            ops,
            principal,
            workspace_id,
            attempt_id,
            cursor=params.get("cursor"),
            wait_ms=0 if params.get("wait_ms") is None else params.get("wait_ms"),
        )
    if method == CHANGES_GET:
        return await _read_changes(
            ops,
            principal,
            workspace_id,
            attempt_id,
            max_commits=BRANCH_DIFF_MAX_COMMITS if params.get("max_commits") is None else params.get("max_commits"),
            max_files=BRANCH_DIFF_MAX_FILES if params.get("max_files") is None else params.get("max_files"),
        )
    return await _read_diff(
        ops,
        principal,
        workspace_id,
        attempt_id,
        commit=params.get("commit"),
        path=params.get("path"),
    )

