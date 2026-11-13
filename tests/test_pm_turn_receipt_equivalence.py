import json
import sqlite3

from bat_agent_connector import pm_attempt_results as ar
from bat_agent_connector import pm_work_follow_up as f
from tests import test_pm_attempt_results as results
from tests.operation_helpers import settle_operations
from tests.test_pm_work_start import STARTER_OWNER
from tests.test_pm_workspace_wip import clone_row
from tests.test_pm_workspace_wip_lineage import closed_attempt


def test_old_and_new_row_shapes(tmp_path):
    db = sqlite3.connect(tmp_path / "predicate.sqlite")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE session_results(sequence INTEGER PRIMARY KEY,workspace_id TEXT,attempt_id TEXT,source_position TEXT,document TEXT)"
    )
    positions = [
        None,
        "",
        "invalid",
        "[]",
        "{}",
        ar.format_source_position("wanted", "answer"),
        ar.format_source_position("other", "answer"),
    ]
    docs = [None, "bad", "[]", "null", "1"] + [
        json.dumps(x)
        for x in [
            {},
            {"turn_marker": "wanted"},
            {"boundary": "turn_done"},
            {"turn_marker": "wanted", "boundary": "turn_done"},
            {"turn_marker": "other", "boundary": "agent_error"},
            {"turn_marker": "wanted", "boundary": "process_ended"},
            {"turn_marker": "wanted", "turn_progress": {"boundary": "agent_error"}},
            {"turn_marker": "wanted", "boundary": "unknown", "turn_progress": {"boundary": "turn_done"}},
            {"turn_marker": "other", "boundary": ["turn_done"]},
            {"turn_marker": "other", "boundary": {"bad": True}},
            {"turn_marker": ["wanted"], "boundary": "turn_done"},
            {"turn_marker": "wanted", "turn_progress": []},
            {"turn_marker": "wanted", "boundary": False, "turn_progress": {"boundary": "turn_done"}},
        ]
    ]
    mismatches = []
    tested = 0

    def outcome(fn):
        try:
            return ("value", fn(db, "ws", "att", "wanted"))
        except Exception as e:
            return ("exception", type(e).__name__)

    try:
        for pos in positions:
            for doc in docs:
                db.execute("DELETE FROM session_results")
                db.execute("INSERT INTO session_results VALUES(1,?,?,?,?)", ("ws", "att", pos, doc))
                before = outcome(legacy_has_turn_boundary_receipt)
                after = outcome(f._has_turn_boundary_receipt)
                tested += 1
                if before != after:
                    mismatches.append({"source_position": pos, "document": doc, "old": before, "new": after})
    finally:
        db.close()
    print("PREDICATE SHAPES", tested, "mismatches", len(mismatches), mismatches[:2])
    assert not mismatches


# Frozen pre-extraction predicate from cf0b6ad4, the compatibility oracle.
def legacy_has_turn_boundary_receipt(
    db,
    workspace_id: str,
    attempt_id: str,
    turn_marker: str | None,
) -> bool:
    if not turn_marker or not f._has_table(db, "session_results"):
        return False
    from bat_agent_connector import pm_attempt_results

    rows = db.execute(
        """SELECT source_position, document FROM session_results
           WHERE workspace_id=? AND attempt_id=?
           ORDER BY sequence DESC""",
        (workspace_id, attempt_id),
    ).fetchall()
    for row in rows:
        parsed_pos = pm_attempt_results.parse_source_position(row["source_position"] or "")
        matched_marker = parsed_pos.get("turn_marker") == turn_marker
        try:
            doc = json.loads(row["document"] or "{}")
        except (TypeError, ValueError):
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        if not matched_marker and doc.get("turn_marker") == turn_marker:
            matched_marker = True
        if not matched_marker:
            continue
        turn_prog = doc.get("turn_progress") if isinstance(doc.get("turn_progress"), dict) else {}
        boundary = doc.get("boundary") or turn_prog.get("boundary")
        if boundary in {"turn_done", "agent_error"}:
            return True
    return False



world = results.world
pm = results.pm
ready = results.ready
start_ready = results.start_ready


async def test_malformed_unrelated_receipt_preserves_followup(start_ready, mock):
    w = start_ready
    ops = w["d"].ops
    att, rec = await closed_attempt(w, mock)
    row = dict(
        ops.db.execute("SELECT * FROM session_results WHERE result_id=?", (rec["result_id"],)).fetchone()
    )
    clone_row(
        ops.db,
        "session_results",
        row,
        sequence=None,
        result_id="res_bad_boundary",
        source_position=ar.format_source_position("unrelated-marker", "bad"),
        document=json.dumps({"turn_marker": "unrelated-marker", "boundary": ["turn_done"]}),
    )
    assert legacy_has_turn_boundary_receipt(ops.db, w["workspace_id"], att["attempt_id"], att["turn_marker"])
    # The exact real Work-input admission previously succeeds with this unrelated row.
    op, _ = ops.create(
        STARTER_OWNER,
        action="session.send",
        target=w["target"],
        params={"text": "Synthetic next Work input", "schedule": "now"},
        preconditions={},
        idempotency_key="bad-row-admission",
    )
    await settle_operations(ops)
    assert ops.get(op["operation_id"])["status"] == "succeeded"
