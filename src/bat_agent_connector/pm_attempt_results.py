"""Append-only session_results store, canonical turn digest, and AttemptResultCollector (v1.3 E04 Phase B1).

All result receipts are written at turn boundaries by the server-side AttemptResultCollector
under actor ``connector``. Read endpoints never write to SQLite or emit api_events.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
import uuid
from typing import Any

from . import lifecycle, orchestrate, pm_source_lineage, registry
from .errors import BatError, ConnectionLost, InvokeTimeout
from .redact import redact_secrets
from .service import DIGEST_TEXT_MAX_CHARS, TURN_SLICE_MAX_ITEMS  # noqa: F401
from .summarize import clip, is_tool, iso_local, summarize_message, ts_to_ms
from .triage import _msg_text, _role

ACTOR = "connector"
DECODER_VERSION = "bat_ipc_session_v1"
RESULT_DOC_SCHEMA = "session-result-doc/v1"
MAX_RESULT_DOCUMENT_BYTES = 262_144

TURN_ITEM_IDS_MAX = 200
SUMMARY_MAX_CHARS = 4_000
MESSAGES_PREVIEW_MAX_ITEMS = 20
MESSAGES_PREVIEW_MAX_CHARS = 1_000
TOOL_ACTIVITY_MAX_ITEMS = 50
TOOL_ACTIVITY_MAX_CHARS = 500
BRANCH_DIFF_MAX_COMMITS = 30
BRANCH_DIFF_MAX_FILES = 80
CHANGESET_TOTAL_BUDGET_S = 25.0

BOUNDARIES = frozenset({"turn_done", "process_ended", "agent_error"})


def _json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def installed(db) -> bool:
    return pm_source_lineage._table(db, "session_results")


def schema(journal) -> None:
    """Idempotent additive DDL from E04 §2.3; does not allocate user_version or a data step."""
    with journal.tx():
        for ddl in (
            """CREATE TABLE IF NOT EXISTS session_results (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL,
    attempt_id TEXT,
    source_key TEXT NOT NULL,
    source_generation TEXT NOT NULL,
    source_position TEXT NOT NULL,
    digest TEXT NOT NULL,
    document TEXT NOT NULL,
    observed_at REAL NOT NULL,
    collected_at REAL NOT NULL,
    operation_id TEXT NOT NULL,
    UNIQUE(workspace_id, source_key, source_generation, source_position, digest)
)""",
            """CREATE INDEX IF NOT EXISTS session_results_by_attempt
    ON session_results(workspace_id, attempt_id, sequence DESC)""",
            """CREATE INDEX IF NOT EXISTS session_results_by_position
    ON session_results(workspace_id, source_key, source_generation, source_position, sequence ASC)""",
            """CREATE TRIGGER IF NOT EXISTS session_results_no_update
BEFORE UPDATE ON session_results BEGIN
    SELECT RAISE(ABORT, 'session_results is append-only');
END""",
            """CREATE TRIGGER IF NOT EXISTS session_results_no_delete
BEFORE DELETE ON session_results BEGIN
    SELECT RAISE(ABORT, 'session_results is append-only');
END""",
        ):
            journal.db.execute(ddl)


def install(ops) -> None:
    schema(ops.journal)


def source_generation_from_registry(host: str, session_id: str, entry: dict | None = None) -> str:
    """Return ``<controller_incarnation>:<created_at>`` from registry, or ``unknown`` when missing."""
    reg = entry if entry is not None else registry.get(host, session_id)
    if not isinstance(reg, dict):
        return "unknown"
    inc = reg.get("controller_incarnation")
    created_at = reg.get("created_at")
    if inc is not None and created_at is not None and str(inc) and str(created_at):
        return f"{inc}:{created_at}"
    return "unknown"


def format_source_position(turn_marker: str | None, last_message_id: str | None) -> str:
    return _json(
        {
            "last_message_id": str(last_message_id or ""),
            "turn_marker": str(turn_marker or ""),
        }
    )


def parse_source_position(raw: str | dict) -> dict[str, str]:
    if isinstance(raw, dict):
        return {
            "last_message_id": str(raw.get("last_message_id") or ""),
            "turn_marker": str(raw.get("turn_marker") or ""),
        }
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    return {
        "last_message_id": str(parsed.get("last_message_id") or ""),
        "turn_marker": str(parsed.get("turn_marker") or ""),
    }


def summarize_turn_messages(messages: list[dict]) -> list[dict]:
    """Summarize raw BAT messages with ``include_tools=True, max_chars=DIGEST_TEXT_MAX_CHARS``."""
    return [
        s
        for m in messages
        if isinstance(m, dict)
        and (s := summarize_message(m, include_tools=True, max_chars=DIGEST_TEXT_MAX_CHARS)) is not None
    ]


def canonical_turn_item(item: dict) -> dict[str, str]:
    """Keep only ``id, role, tool, text`` from a summarized turn item."""
    return {
        "id": str(item.get("id") or ""),
        "role": str(item.get("role") or ""),
        "tool": str(item.get("tool") or ""),
        "text": str(item.get("text") or ""),
    }


def canonical_digest_input(
    *,
    source_key: str,
    source_generation: str,
    turn_marker: str | None,
    last_message_id: str | None,
    turn_items: list[dict],
    decoder_version: str = DECODER_VERSION,
) -> dict[str, Any]:
    return {
        "decoder_version": str(decoder_version),
        "last_message_id": str(last_message_id or ""),
        "source_generation": str(source_generation),
        "source_key": str(source_key),
        "turn_items": [canonical_turn_item(item) for item in turn_items],
        "turn_marker": str(turn_marker or ""),
    }


def canonical_digest_bytes(
    *,
    source_key: str,
    source_generation: str,
    turn_marker: str | None,
    last_message_id: str | None,
    turn_items: list[dict],
    decoder_version: str = DECODER_VERSION,
) -> bytes:
    payload = canonical_digest_input(
        source_key=source_key,
        source_generation=source_generation,
        turn_marker=turn_marker,
        last_message_id=last_message_id,
        turn_items=turn_items,
        decoder_version=decoder_version,
    )
    return _json(payload).encode("utf-8")


def compute_digest(
    *,
    source_key: str,
    source_generation: str,
    turn_marker: str | None,
    last_message_id: str | None,
    turn_items: list[dict],
    decoder_version: str = DECODER_VERSION,
) -> str:
    raw = canonical_digest_bytes(
        source_key=source_key,
        source_generation=source_generation,
        turn_marker=turn_marker,
        last_message_id=last_message_id,
        turn_items=turn_items,
        decoder_version=decoder_version,
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _latest_assistant_summary(
    slice_items: list[dict],
    *,
    max_chars: int = SUMMARY_MAX_CHARS,
) -> tuple[dict[str, Any] | None, bool]:
    final_text = lifecycle._final_output(slice_items)
    if not final_text:
        return None, False
    last_msg: dict | None = None
    for m in reversed(slice_items):
        if _msg_text(m) and _role(m) not in ("user", "system"):
            last_msg = m
            break
    redacted = redact_secrets(final_text)
    clipped = clip(redacted, max_chars)
    was_clipped = len(redacted) > max_chars
    summary: dict[str, Any] = {
        "text": clipped,
        "message_id": last_msg.get("id") if last_msg else None,
        "claim_status": "self_reported",
    }
    if last_msg and last_msg.get("ts"):
        summary["ts"] = iso_local(ts_to_ms(last_msg.get("ts")))
    return summary, was_clipped


def _usage_from_meta(meta: dict | None) -> dict[str, Any]:
    m = meta if isinstance(meta, dict) else {}

    def _int_or_unknown(val: Any) -> int | str:
        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
            return val
        return "unknown"

    return {
        "context_tokens": _int_or_unknown(m.get("context_tokens")),
        "context_window": _int_or_unknown(m.get("context_window")),
        "num_turns": _int_or_unknown(m.get("num_turns")),
        "input_tokens": "unknown",
        "output_tokens": "unknown",
        "total_tokens": "unknown",
        "quota": "unknown",
        "duration_seconds": "unknown",
        "duration_reason": "not_exposed_by_bat",
    }


def _dedup_reasons(reasons: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for r in reasons:
        if isinstance(r, str) and r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _mark_document_bounded(doc: dict[str, Any]) -> None:
    cov = doc.setdefault("coverage", {})
    cov["document_truncated"] = True
    reasons = list(cov.get("reasons") or [])
    if "result_document_bounded" not in reasons:
        reasons.append("result_document_bounded")
    cov["reasons"] = _dedup_reasons(reasons)


def _bound_document(doc: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Ensure the serialized UTF-8 document is at or under MAX_RESULT_DOCUMENT_BYTES (262,144)."""
    work = copy.deepcopy(doc)
    encoded = _json(work)
    if len(encoded.encode("utf-8")) <= MAX_RESULT_DOCUMENT_BYTES:
        return work, encoded

    _mark_document_bounded(work)

    # 1. Progressively shrink messages_preview and tool_activity counts and text caps.
    for msg_limit, msg_chars, tool_limit, tool_chars in (
        (10, 500, 25, 250),
        (5, 250, 10, 120),
        (2, 120, 5, 80),
        (0, 0, 0, 0),
    ):
        msgs = list(work.get("messages_preview") or [])
        if msg_limit == 0:
            work["messages_preview"] = []
        else:
            shrunk_msgs = []
            for item in msgs[-msg_limit:]:
                if isinstance(item, dict):
                    shrunk_msgs.append({**item, "text": clip(str(item.get("text") or ""), msg_chars)})
            work["messages_preview"] = shrunk_msgs

        tools = list(work.get("tool_activity") or [])
        if tool_limit == 0:
            work["tool_activity"] = []
        else:
            shrunk_tools = []
            for item in tools[-tool_limit:]:
                if isinstance(item, dict):
                    txt = clip(str(item.get("summary") or item.get("text") or ""), tool_chars)
                    shrunk_tools.append({**item, "summary": txt, "text": txt})
            work["tool_activity"] = shrunk_tools

        encoded = _json(work)
        if len(encoded.encode("utf-8")) <= MAX_RESULT_DOCUMENT_BYTES:
            return work, encoded

    # 2. Shrink turn_item_ids and latest_assistant_summary.
    for id_limit, sum_chars in ((50, 1_000), (10, 250), (0, 120)):
        ids = list(work.get("turn_item_ids") or [])
        work["turn_item_ids"] = ids[-id_limit:] if id_limit else []
        summary = work.get("latest_assistant_summary")
        if isinstance(summary, dict) and isinstance(summary.get("text"), str):
            summary["text"] = clip(summary["text"], sum_chars)
        encoded = _json(work)
        if len(encoded.encode("utf-8")) <= MAX_RESULT_DOCUMENT_BYTES:
            return work, encoded

    # 3. Shrink large uncommitted file lists or changes_summary file lists if needed.
    head_obs = work.get("head_observation")
    if isinstance(head_obs, dict) and isinstance(head_obs.get("uncommitted"), dict):
        unc = head_obs["uncommitted"]
        if isinstance(unc.get("files"), list) and len(unc["files"]) > 20:
            unc["files"] = unc["files"][:20]
    if isinstance(work.get("uncommitted"), dict) and isinstance(work["uncommitted"].get("files"), list):
        if len(work["uncommitted"]["files"]) > 20:
            work["uncommitted"]["files"] = work["uncommitted"]["files"][:20]
    encoded = _json(work)
    if len(encoded.encode("utf-8")) <= MAX_RESULT_DOCUMENT_BYTES:
        return work, encoded

    changes = work.get("changes_summary")
    if isinstance(changes, dict):
        for file_keep in (20, 5, 0):
            if isinstance(changes.get("files"), list) and len(changes["files"]) > file_keep:
                changes["files"] = changes["files"][:file_keep]
                changes["complete"] = False
            if isinstance(changes.get("unavailable"), list) and len(changes["unavailable"]) > file_keep:
                changes["unavailable"] = changes["unavailable"][:file_keep]
            encoded = _json(work)
            if len(encoded.encode("utf-8")) <= MAX_RESULT_DOCUMENT_BYTES:
                return work, encoded

    return work, encoded


def build_result_document(
    *,
    result_id: str,
    turn_marker: str | None,
    last_message_id: str | None,
    slice_items: list[dict],
    slice_complete: bool = True,
    slice_reason: str | None = None,
    turn_started: bool = True,
    turn_done: bool = True,
    turn_phase: str = "terminal",
    turn_attribution: str = "correlated",
    boundary: str = "turn_done",
    meta: dict | None = None,
    agent_kind: str | None = None,
    head_observation: dict[str, Any] | None = None,
    changes_summary: dict[str, Any] | None = None,
    transcript_window: dict[str, Any] | None = None,
    extra_reasons: list[str] | None = None,
    decoder_version: str = DECODER_VERSION,
) -> dict[str, Any]:
    if boundary not in BOUNDARIES:
        raise ValueError(f"boundary must be one of {sorted(BOUNDARIES)}")

    truncated = False
    reasons: list[str] = []
    if not slice_complete:
        truncated = True
        if slice_reason:
            reasons.append(slice_reason)

    all_ids = [str(item.get("id") or "") for item in slice_items if item.get("id")]
    if len(all_ids) > TURN_ITEM_IDS_MAX:
        turn_item_ids = all_ids[-TURN_ITEM_IDS_MAX:]
        truncated = True
    else:
        turn_item_ids = all_ids

    latest_summary, summary_clipped = _latest_assistant_summary(slice_items, max_chars=SUMMARY_MAX_CHARS)
    if summary_clipped:
        truncated = True

    non_tool_items = [item for item in slice_items if not is_tool(item) and item.get("role") != "tool"]
    if len(non_tool_items) > MESSAGES_PREVIEW_MAX_ITEMS:
        non_tool_slice = non_tool_items[-MESSAGES_PREVIEW_MAX_ITEMS:]
        truncated = True
    else:
        non_tool_slice = non_tool_items

    messages_preview: list[dict[str, Any]] = []
    for item in non_tool_slice:
        raw_txt = str(item.get("text") or "")
        clipped_txt = clip(raw_txt, MESSAGES_PREVIEW_MAX_CHARS)
        if len(raw_txt) > MESSAGES_PREVIEW_MAX_CHARS:
            truncated = True
        messages_preview.append(
            {
                "id": item.get("id"),
                "ts": item.get("ts"),
                "role": item.get("role"),
                "text": clipped_txt,
            }
        )

    tool_items = [item for item in slice_items if is_tool(item) or item.get("role") == "tool"]
    if len(tool_items) > TOOL_ACTIVITY_MAX_ITEMS:
        tool_slice = tool_items[-TOOL_ACTIVITY_MAX_ITEMS:]
        truncated = True
    else:
        tool_slice = tool_items

    tool_activity: list[dict[str, Any]] = []
    for item in tool_slice:
        raw_sum = str(item.get("summary") or item.get("text") or "")
        clipped_sum = clip(raw_sum, TOOL_ACTIVITY_MAX_CHARS)
        if len(raw_sum) > TOOL_ACTIVITY_MAX_CHARS:
            truncated = True
        entry: dict[str, Any] = {
            "id": item.get("id"),
            "ts": item.get("ts"),
            "role": "tool",
            "tool": item.get("tool"),
            "summary": clipped_sum,
            "text": clipped_sum,
        }
        for opt_key in ("status", "completed_at", "duration_ms", "denied", "deferred"):
            if opt_key in item:
                entry[opt_key] = item[opt_key]
        tool_activity.append(entry)

    if truncated:
        reasons.append("result_document_bounded")

    default_uncommitted = {
        "count": "unknown",
        "files": "unknown",
        "evidence": "unproven",
        "reason": "bat_empty_status_not_clean_proof",
    }
    resolved_head_obs = copy.deepcopy(head_observation) if isinstance(head_observation, dict) else {
        "base_commit": "unknown",
        "head_commit": "unknown",
        "observed_at": None,
        "uncommitted": default_uncommitted,
        "fixed_result_commit": None,
    }
    uncommitted_obj = resolved_head_obs.get("uncommitted")
    if not isinstance(uncommitted_obj, dict):
        uncommitted_obj = default_uncommitted
        resolved_head_obs["uncommitted"] = uncommitted_obj
    if uncommitted_obj.get("reason"):
        reasons.append(str(uncommitted_obj["reason"]))

    fixed_commit = resolved_head_obs.get("fixed_result_commit")
    if isinstance(fixed_commit, dict):
        fixed_commit = {**fixed_commit, "source_id": result_id}
        resolved_head_obs["fixed_result_commit"] = fixed_commit

    resolved_changes = copy.deepcopy(changes_summary) if isinstance(changes_summary, dict) else {
        "aggregation": "per_commit",
        "base_commit": resolved_head_obs.get("base_commit", "unknown"),
        "head_commit": resolved_head_obs.get("head_commit", "unknown"),
        "commits": [],
        "commit_count": 0,
        "files": [],
        "file_count": 0,
        "additions": 0,
        "deletions": 0,
        "unavailable": [],
        "complete": True,
        "reason": None,
        "reasons": [],
        "cached_from_result_id": result_id,
    }
    resolved_changes["cached_from_result_id"] = result_id
    for ch_reason in resolved_changes.get("reasons") or []:
        reasons.append(str(ch_reason))
    if resolved_changes.get("reason"):
        reasons.append(str(resolved_changes["reason"]))
    for extra in extra_reasons or []:
        reasons.append(str(extra))

    deduped_reasons = _dedup_reasons(reasons)
    m = meta if isinstance(meta, dict) else {}
    doc: dict[str, Any] = {
        "schema_version": RESULT_DOC_SCHEMA,
        "decoder_version": str(decoder_version),
        "result_id": result_id,
        "turn_marker": str(turn_marker or ""),
        "last_message_id": str(last_message_id or ""),
        "turn_item_ids": turn_item_ids,
        "latest_assistant_summary": latest_summary,
        "messages_preview": messages_preview,
        "tool_activity": tool_activity,
        "turn_done": bool(turn_done),
        "turn_phase": str(turn_phase),
        "turn_attribution": str(turn_attribution),
        "boundary": boundary,
        "turn_progress": {
            "turn_started": bool(turn_started),
            "turn_done": bool(turn_done),
            "turn_phase": str(turn_phase),
            "turn_attribution": str(turn_attribution),
            "boundary": boundary,
        },
        "usage": _usage_from_meta(m),
        "meta": {
            "model": m.get("model"),
            "agent_kind": agent_kind,
            "num_turns": m.get("num_turns"),
            "context_tokens": m.get("context_tokens"),
            "context_window": m.get("context_window"),
            "last_data_at": m.get("last_data_at"),
        },
        "head_observation": resolved_head_obs,
        "fixed_result_commit": fixed_commit,
        "uncommitted": uncommitted_obj,
        "changes_summary": resolved_changes,
        "transcript": transcript_window
        or {
            "offset": 0,
            "next_offset": None,
            "live_items": len(slice_items),
            "archived_total": None,
            "truncated_by_size": False,
        },
        "coverage": {
            "document_truncated": truncated,
            "reasons": deduped_reasons,
            "turn_slice_complete": bool(slice_complete),
            "changes_complete": bool(resolved_changes.get("complete", False)),
        },
    }
    bounded_doc, _ = _bound_document(doc)
    return bounded_doc


def _resolve_attempt_event_fields(
    db,
    *,
    workspace_id: str,
    attempt_id: str | None,
    operation_id: str,
    work_item_id: str | None = None,
    start_operation_id: str | None = None,
    package_id: str | None = None,
    package_reused: bool | None = None,
    session: dict[str, str] | None = None,
) -> dict[str, Any]:
    resolved_start_op = start_operation_id
    resolved_wi = work_item_id
    resolved_pkg = package_id
    resolved_reused = package_reused
    resolved_session = session

    if attempt_id and pm_source_lineage._table(db, "work_attempts"):
        att_row = db.execute(
            "SELECT work_item_id, start_operation_id FROM work_attempts WHERE workspace_id=? AND attempt_id=?",
            (workspace_id, attempt_id),
        ).fetchone()
        if att_row is not None:
            resolved_wi = resolved_wi or att_row["work_item_id"]
            resolved_start_op = resolved_start_op or att_row["start_operation_id"]

    if not resolved_start_op and pm_source_lineage._table(db, "pm_work_start_children"):
        child_row = db.execute(
            "SELECT parent_id FROM pm_work_start_children WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if child_row is not None:
            resolved_start_op = child_row["parent_id"]

    if resolved_start_op:
        op_row = db.execute(
            "SELECT target, result, external_refs FROM operations WHERE operation_id=?",
            (resolved_start_op,),
        ).fetchone()
        if op_row is not None:
            try:
                target = json.loads(op_row["target"] or "{}")
            except (TypeError, ValueError):
                target = {}
            try:
                result = json.loads(op_row["result"] or "{}")
            except (TypeError, ValueError):
                result = {}
            try:
                refs = json.loads(op_row["external_refs"] or "{}")
            except (TypeError, ValueError):
                refs = {}
            state = refs.get("work_start_state") if isinstance(refs.get("work_start_state"), dict) else {}
            intent = (
                (refs.get("admission_binding") or {}).get("work_start")
                if isinstance(refs.get("admission_binding"), dict)
                else {}
            )
            if not isinstance(intent, dict):
                intent = {}
            pkg_plan = intent.get("package_plan") if isinstance(intent.get("package_plan"), dict) else {}
            resolved_wi = resolved_wi or result.get("work_item_id") or state.get("work_item_id") or target.get("work_item_id")
            resolved_pkg = (
                resolved_pkg
                or result.get("package_id")
                or state.get("package_id")
                or refs.get("package_id")
                or pkg_plan.get("package_id")
            )
            if resolved_reused is None:
                if "package_reused" in result:
                    resolved_reused = bool(result["package_reused"])
                elif "package_reused" in state:
                    resolved_reused = bool(state["package_reused"])
                else:
                    resolved_reused = bool(pkg_plan.get("package_reused"))
            if resolved_session is None:
                cand_sess = result.get("session") or state.get("session")
                if isinstance(cand_sess, dict) and cand_sess.get("host") and cand_sess.get("session_id"):
                    resolved_session = {"host": str(cand_sess["host"]), "session_id": str(cand_sess["session_id"])}

    return {
        "workspace_id": workspace_id,
        "work_item_id": resolved_wi,
        "attempt_id": attempt_id,
        "operation_id": resolved_start_op or operation_id,
        "package_id": resolved_pkg,
        "package_reused": bool(resolved_reused),
        "execution_operation_id": operation_id,
        "session": resolved_session,
    }


def _decode_row(row) -> dict[str, Any]:
    pos = parse_source_position(row["source_position"])
    try:
        doc = json.loads(row["document"])
    except (TypeError, ValueError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    head_obs = doc.get("head_observation") if isinstance(doc.get("head_observation"), dict) else {}
    fixed = doc.get("fixed_result_commit") if isinstance(doc.get("fixed_result_commit"), dict) else {}
    head_commit = fixed.get("commit_sha") or head_obs.get("head_commit") or "unknown"
    return {
        "sequence": int(row["sequence"]),
        "result_id": row["result_id"],
        "workspace_id": row["workspace_id"],
        "attempt_id": row["attempt_id"],
        "source_key": row["source_key"],
        "source_generation": row["source_generation"],
        "source_position": pos,
        "source_position_json": row["source_position"],
        "turn_marker": pos.get("turn_marker"),
        "last_message_id": pos.get("last_message_id"),
        "head_commit": head_commit,
        "digest": row["digest"],
        "document": doc,
        "observed_at": float(row["observed_at"]),
        "collected_at": float(row["collected_at"]),
        "operation_id": row["operation_id"],
    }


def record_receipt(
    journal_or_ops,
    *,
    workspace_id: str,
    attempt_id: str | None,
    source_key: str,
    source_generation: str,
    source_position: str | dict,
    digest: str,
    document: dict[str, Any],
    observed_at: float,
    operation_id: str,
    collected_at: float | None = None,
    result_id: str | None = None,
    work_item_id: str | None = None,
    start_operation_id: str | None = None,
    package_id: str | None = None,
    package_reused: bool | None = None,
    session: dict[str, str] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Insert a turn-boundary receipt in one transaction and emit connector events."""
    journal = getattr(journal_or_ops, "journal", journal_or_ops)
    db = journal.db
    if not installed(db):
        schema(journal)

    pos_dict = parse_source_position(source_position)
    pos_json = format_source_position(pos_dict["turn_marker"], pos_dict["last_message_id"])

    rid = result_id or str(document.get("result_id") or "") or f"res_{uuid.uuid4().hex}"
    doc_in = copy.deepcopy(document)
    doc_in["result_id"] = rid
    doc_in.setdefault("schema_version", RESULT_DOC_SCHEMA)
    doc_in.setdefault("decoder_version", DECODER_VERSION)
    if isinstance(doc_in.get("fixed_result_commit"), dict):
        doc_in["fixed_result_commit"]["source_id"] = rid
    if isinstance(doc_in.get("head_observation"), dict) and isinstance(
        doc_in["head_observation"].get("fixed_result_commit"), dict
    ):
        doc_in["head_observation"]["fixed_result_commit"]["source_id"] = rid
    if isinstance(doc_in.get("changes_summary"), dict):
        doc_in["changes_summary"]["cached_from_result_id"] = rid

    bounded_doc, doc_json = _bound_document(doc_in)
    decoder_version = str(bounded_doc.get("decoder_version") or DECODER_VERSION)

    with journal.tx():
        now = time.time() if collected_at is None else float(collected_at)
        cur = db.execute(
            """INSERT OR IGNORE INTO session_results (
                result_id, workspace_id, attempt_id, source_key, source_generation,
                source_position, digest, document, observed_at, collected_at, operation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rid,
                workspace_id,
                attempt_id,
                source_key,
                source_generation,
                pos_json,
                digest,
                doc_json,
                float(observed_at),
                now,
                operation_id,
            ),
        )
        inserted = cur.rowcount > 0
        if not inserted:
            existing = db.execute(
                """SELECT * FROM session_results
                   WHERE workspace_id=? AND source_key=? AND source_generation=? AND source_position=? AND digest=?""",
                (workspace_id, source_key, source_generation, pos_json, digest),
            ).fetchone()
            return _decode_row(existing), False

        other_rows = db.execute(
            """SELECT result_id, digest, document FROM session_results
               WHERE workspace_id=? AND source_key=? AND source_generation=? AND source_position=? AND digest!=?
               ORDER BY sequence ASC""",
            (workspace_id, source_key, source_generation, pos_json, digest),
        ).fetchall()
        conflicting_digests: list[str] = []
        for other in other_rows:
            try:
                other_doc = json.loads(other["document"])
            except (TypeError, ValueError):
                other_doc = {}
            other_ver = (
                other_doc.get("decoder_version", DECODER_VERSION)
                if isinstance(other_doc, dict)
                else DECODER_VERSION
            )
            if other_ver == decoder_version and other["digest"] not in conflicting_digests:
                conflicting_digests.append(other["digest"])

        if attempt_id:
            base_body = _resolve_attempt_event_fields(
                db,
                workspace_id=workspace_id,
                attempt_id=attempt_id,
                operation_id=operation_id,
                work_item_id=work_item_id,
                start_operation_id=start_operation_id,
                package_id=package_id,
                package_reused=package_reused,
                session=session,
            )
            head_obs = bounded_doc.get("head_observation") if isinstance(bounded_doc.get("head_observation"), dict) else {}
            event_body = {
                **base_body,
                "result_id": rid,
                "source_key": source_key,
                "source_generation": source_generation,
                "source_position": pos_dict,
                "digest": digest,
                "decoder_version": decoder_version,
                "boundary": bounded_doc.get("boundary"),
                "head_commit": head_obs.get("head_commit"),
            }
            journal.api_event(
                "pm_work_attempt",
                attempt_id,
                "work.attempt.result_recorded",
                event_body,
                actor=ACTOR,
            )
            if conflicting_digests:
                journal.api_event(
                    "pm_work_attempt",
                    attempt_id,
                    "work.attempt.result_conflict",
                    {
                        **event_body,
                        "conflicting_digests": conflicting_digests,
                    },
                    actor=ACTOR,
                )

        stored = db.execute("SELECT * FROM session_results WHERE result_id=?", (rid,)).fetchone()
        return _decode_row(stored), True


def receipts(db, workspace_id: str, attempt_id: str, limit: int = 20) -> dict[str, Any]:
    """Return receipts for ``(workspace_id, attempt_id)`` newest first, with ``has_more``."""
    if not installed(db):
        return {"receipts": [], "results": [], "has_more": False}
    lim = max(1, min(200, int(limit)))
    rows = db.execute(
        """SELECT * FROM session_results
           WHERE workspace_id=? AND attempt_id=?
           ORDER BY sequence DESC LIMIT ?""",
        (workspace_id, attempt_id, lim + 1),
    ).fetchall()
    has_more = len(rows) > lim
    decoded = [_decode_row(r) for r in rows[:lim]]
    summary_list = [
        {
            "result_id": item["result_id"],
            "turn_marker": item["turn_marker"],
            "last_message_id": item["last_message_id"],
            "head_commit": item["head_commit"],
            "observed_at": item["observed_at"],
            "collected_at": item["collected_at"],
            "digest": item["digest"],
            "sequence": item["sequence"],
        }
        for item in decoded
    ]
    return {
        "receipts": decoded,
        "results": summary_list,
        "has_more": has_more,
    }


# --------------------------------------------------------------------------- Section 3: Boundary head & changes summary
def _unknown_uncommitted() -> dict[str, Any]:
    return {
        "count": "unknown",
        "files": "unknown",
        "evidence": "unproven",
        "reason": "bat_empty_status_not_clean_proof",
    }


async def collect_uncommitted(client: Any, worktree_path: str | None) -> dict[str, Any]:
    """Read uncommitted worktree status from ``worktree_path`` only (never ``meta.cwd``).

    BAT ``git:status`` returns ``[]`` both when clean and when git fails, so an empty
    list or error is never treated as proof of a clean working tree.
    """
    if not isinstance(worktree_path, str) or not worktree_path.strip():
        return _unknown_uncommitted()
    wt = worktree_path.strip()
    try:
        st = await client.invoke("git:status", {"cwd": wt})
    except (BatError, ConnectionLost, InvokeTimeout, asyncio.TimeoutError):
        return _unknown_uncommitted()
    if not isinstance(st, list) or not st:
        return _unknown_uncommitted()
    files: list[dict[str, Any]] = []
    for entry in st:
        if isinstance(entry, dict):
            item = dict(entry)
            path_val = str(item.get("file") or item.get("path") or "").strip()
            if path_val:
                item.setdefault("file", path_val)
                item.setdefault("path", path_val)
            item.setdefault("diff_available", False)
            item.setdefault("unavailable_reason", "uncommitted_diff_not_supported_by_bat")
            files.append(item)
        elif isinstance(entry, str) and entry.strip():
            path_val = entry.strip()
            files.append(
                {
                    "file": path_val,
                    "path": path_val,
                    "status": "?",
                    "diff_available": False,
                    "unavailable_reason": "uncommitted_diff_not_supported_by_bat",
                }
            )
    if not files:
        return _unknown_uncommitted()
    return {
        "count": len(files),
        "files": files,
        "evidence": "bat_git_status_nonempty",
        "reason": None,
    }


async def collect_head_observation(
    client: Any,
    worktree_path: str | None,
    *,
    base_sha: str | None,
    result_id: str | None = None,
    observed_at: float | None = None,
) -> dict[str, Any]:
    """Observe the worktree HEAD (`git:log count 1`) and uncommitted changes at a turn boundary."""
    uncommitted = await collect_uncommitted(client, worktree_path)
    head_sha: str | None = None
    if isinstance(worktree_path, str) and worktree_path.strip():
        try:
            log = await client.invoke("git:log", {"cwd": worktree_path.strip(), "count": 1})
            if isinstance(log, list) and log and isinstance(log[0], dict):
                h = log[0].get("hash")
                if isinstance(h, str) and h.strip():
                    head_sha = h.strip()
        except (BatError, ConnectionLost, InvokeTimeout, asyncio.TimeoutError):
            head_sha = None

    fixed_result_commit: dict[str, Any] | None = None
    if head_sha:
        fixed_result_commit = {
            "commit_sha": head_sha,
            "source_kind": "turn_end_observation",
            "source_id": result_id,
            "recorded_by": ACTOR,
            "uncommitted": uncommitted["count"],
        }
        if observed_at is not None:
            fixed_result_commit["recorded_at"] = float(observed_at)

    return {
        "base_commit": str(base_sha or "unknown"),
        "head_commit": head_sha or "unknown",
        "observed_at": float(observed_at) if observed_at is not None else None,
        "uncommitted": uncommitted,
        "fixed_result_commit": fixed_result_commit,
        "worktree_unreachable": head_sha is None,
    }


def _incomplete_changes(
    base_sha: str | None,
    head_sha: str | None,
    reason: str,
    *,
    result_id: str | None = None,
    commits: list[str] | None = None,
    files: list[dict[str, Any]] | None = None,
    unavailable: list[str] | None = None,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    deduped = _dedup_reasons(reasons or ([reason] if reason else []))
    c_list = list(commits or [])
    f_list = list(files or [])
    return {
        "aggregation": "per_commit",
        "base_commit": str(base_sha or "unknown"),
        "head_commit": str(head_sha or "unknown"),
        "commits": c_list,
        "commit_count": len(c_list),
        "files": f_list,
        "file_count": len(f_list),
        "additions": "unknown",
        "deletions": "unknown",
        "unavailable": list(unavailable or []),
        "complete": False,
        "reason": deduped[0] if deduped else reason,
        "reasons": deduped,
        "cached_from_result_id": result_id,
    }


async def collect_changes_summary(
    client: Any,
    worktree_path: str | None,
    *,
    base_sha: str | None,
    result_id: str | None = None,
    budget_s: float = CHANGESET_TOTAL_BUDGET_S,
) -> dict[str, Any]:
    """Walk ``(base_sha..head]`` oldest-first on ``worktree_path`` and compute per-commit file stats.

    Modelled on ``lifecycle._branch_diff`` without changing that function (whose base is the main checkout).
    Stores no raw diff text; when a file's diff is empty or raises ``BatError``, lists the path in
    ``unavailable`` with ``additions: "unknown", deletions: "unknown"`` (never ``0``).
    """
    if not isinstance(worktree_path, str) or not worktree_path.strip() or not isinstance(base_sha, str) or not base_sha.strip():
        return _incomplete_changes(base_sha, "unknown", "worktree_unreachable", result_id=result_id)

    wt = worktree_path.strip()
    frozen_base = base_sha.strip()
    started = time.monotonic()

    def remaining() -> float:
        return float(budget_s) - (time.monotonic() - started)

    rem = remaining()
    if rem <= 0:
        return _incomplete_changes(frozen_base, "unknown", "changeset_time_budget_exceeded", result_id=result_id)
    try:
        wl = await asyncio.wait_for(
            client.invoke("git:log", {"cwd": wt, "count": BRANCH_DIFF_MAX_COMMITS + 1}),
            timeout=rem,
        )
    except (asyncio.TimeoutError, InvokeTimeout):
        return _incomplete_changes(frozen_base, "unknown", "changeset_time_budget_exceeded", result_id=result_id)
    except (BatError, ConnectionLost):
        return _incomplete_changes(frozen_base, "unknown", "worktree_unreachable", result_id=result_id)

    if not isinstance(wl, list) or not wl:
        return _incomplete_changes(frozen_base, "unknown", "worktree_unreachable", result_id=result_id)

    head_sha = str((wl[0] or {}).get("hash") if isinstance(wl[0], dict) else "").strip() or "unknown"
    ahead_hashes: list[str] = []
    found_base = False
    for entry in wl:
        if not isinstance(entry, dict):
            continue
        h = str(entry.get("hash") or "").strip()
        if not h:
            continue
        if h == frozen_base:
            found_base = True
            break
        ahead_hashes.append(h)

    if not found_base:
        reason = (
            "commit_limit_exceeded"
            if len(ahead_hashes) > BRANCH_DIFF_MAX_COMMITS
            else "base_commit_not_ancestor"
        )
        return _incomplete_changes(frozen_base, head_sha, reason, result_id=result_id)

    chronological = list(reversed(ahead_hashes))
    if not chronological:
        return {
            "aggregation": "per_commit",
            "base_commit": frozen_base,
            "head_commit": head_sha,
            "commits": [],
            "commit_count": 0,
            "files": [],
            "file_count": 0,
            "additions": 0,
            "deletions": 0,
            "unavailable": [],
            "complete": True,
            "reason": None,
            "reasons": [],
            "cached_from_result_id": result_id,
        }

    commit_files_list: list[tuple[str, list[dict[str, str]]]] = []
    for h in chronological:
        rem = remaining()
        if rem <= 0:
            return _incomplete_changes(
                frozen_base,
                head_sha,
                "changeset_time_budget_exceeded",
                result_id=result_id,
                commits=chronological,
            )
        try:
            raw_files = await asyncio.wait_for(
                client.invoke("git:diff-files", {"cwd": wt, "commitHash": h}),
                timeout=rem,
            )
        except (asyncio.TimeoutError, InvokeTimeout):
            return _incomplete_changes(
                frozen_base,
                head_sha,
                "changeset_time_budget_exceeded",
                result_id=result_id,
                commits=chronological,
            )
        except (BatError, ConnectionLost):
            return _incomplete_changes(
                frozen_base,
                head_sha,
                "worktree_unreachable",
                result_id=result_id,
                commits=chronological,
            )

        parsed_files: list[dict[str, str]] = []
        for fe in raw_files or []:
            if not isinstance(fe, dict):
                continue
            path = str(fe.get("file") or fe.get("path") or "").strip()
            if not path:
                continue
            status = str(fe.get("status") or "M")
            parsed_files.append({"path": path, "status": status})
        commit_files_list.append((h, parsed_files))

    per_file: dict[str, dict[str, Any]] = {}
    unavailable_paths: list[str] = []
    reasons: list[str] = []
    diff_calls = 0
    budget_exceeded = False
    file_limit_exceeded = False

    def _mark_unavailable(p: str) -> None:
        per_file[p]["unavailable"] = True
        if p not in unavailable_paths:
            unavailable_paths.append(p)

    for h, parsed_files in commit_files_list:
        for fe in parsed_files:
            path = fe["path"]
            status = fe["status"]
            entry = per_file.setdefault(
                path,
                {
                    "path": path,
                    "status": status,
                    "additions": 0,
                    "deletions": 0,
                    "commits_touched": [],
                    "unavailable": False,
                },
            )
            entry["status"] = status
            if h not in entry["commits_touched"]:
                entry["commits_touched"].append(h)

            if budget_exceeded or remaining() <= 0:
                budget_exceeded = True
                _mark_unavailable(path)
                if "changeset_time_budget_exceeded" not in reasons:
                    reasons.append("changeset_time_budget_exceeded")
                continue

            diff_calls += 1
            if diff_calls > BRANCH_DIFF_MAX_FILES:
                file_limit_exceeded = True
                _mark_unavailable(path)
                continue

            rem = remaining()
            try:
                d = await asyncio.wait_for(
                    client.invoke("git:diff", {"cwd": wt, "commitHash": h, "filePath": path}),
                    timeout=rem,
                )
            except (asyncio.TimeoutError, InvokeTimeout):
                budget_exceeded = True
                _mark_unavailable(path)
                if "changeset_time_budget_exceeded" not in reasons:
                    reasons.append("changeset_time_budget_exceeded")
                continue
            except (BatError, ConnectionLost):
                d = ""

            if not isinstance(d, str) or not d.strip():
                _mark_unavailable(path)
                if "diff_exceeded_pipe_limit" not in reasons:
                    reasons.append("diff_exceeded_pipe_limit")
                continue

            st = orchestrate.diff_stats(d)
            if not entry["unavailable"]:
                entry["additions"] += int(st["additions"])
                entry["deletions"] += int(st["deletions"])

    files_out: list[dict[str, Any]] = []
    for path, entry in per_file.items():
        if entry["unavailable"]:
            files_out.append(
                {
                    "path": path,
                    "status": entry["status"],
                    "additions": "unknown",
                    "deletions": "unknown",
                    "commits_touched": list(entry["commits_touched"]),
                }
            )
        else:
            files_out.append(
                {
                    "path": path,
                    "status": entry["status"],
                    "additions": entry["additions"],
                    "deletions": entry["deletions"],
                    "commits_touched": list(entry["commits_touched"]),
                }
            )

    deduped_reasons = _dedup_reasons(reasons)
    complete = (
        not unavailable_paths
        and not budget_exceeded
        and not file_limit_exceeded
        and not deduped_reasons
    )
    if complete:
        total_add: int | str = sum(int(f["additions"]) for f in files_out)
        total_del: int | str = sum(int(f["deletions"]) for f in files_out)
    else:
        total_add = "unknown"
        total_del = "unknown"

    return {
        "aggregation": "per_commit",
        "base_commit": frozen_base,
        "head_commit": head_sha,
        "commits": chronological,
        "commit_count": len(chronological),
        "files": files_out,
        "file_count": len(files_out),
        "additions": total_add,
        "deletions": total_del,
        "unavailable": unavailable_paths,
        "complete": complete,
        "reason": deduped_reasons[0] if deduped_reasons else None,
        "reasons": deduped_reasons,
        "cached_from_result_id": result_id,
    }

