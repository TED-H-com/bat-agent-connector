"""Preserve the independent review's exact bounds and read-only stress probes."""

import json
import time

import pytest

from bat_agent_connector import pm_roadmap_report_layout as l
from bat_agent_connector import pm_roadmap_report_sources as s
from bat_agent_connector import pm_roadmap_reports as r
from bat_agent_connector import pm_source_lineage
from bat_agent_connector.operations import OperationError
from tests.test_pm_evidence import capture
from tests.test_pm_roadmap import mapped, revision
from tests.test_pm_roadmap_report_sources import READER
from tests.test_pm_roadmap_report_sources import ops as ops_fixture
from tests.test_pm_roadmap_reports import fixture_projection, reconstruct
from tests.test_pm_snapshots import snapshot

ops = ops_fixture


def exact_text_bytes(n, tail="漢🫨"):
    return "x" * (n - len(tail.encode())) + tail


def test_exact_authorization_budgets():
    c = s._Closure(None, None, None)
    for _ in range(2000):
        c.charge()
    assert c.records == 2000
    with pytest.raises(OperationError, match="PM_REPORT_SOURCE_UNAVAILABLE") as e:
        c.charge()
    assert e.value.status == 404
    for tail in ("漢", "🫨", "漢🫨"):
        c = s._Closure(None, None, None)
        value = exact_text_bytes(16_000_000 - 2, tail)
        c.charge(value)
        assert c.bytes == 16_000_000
        with pytest.raises(OperationError, match="PM_REPORT_SOURCE_UNAVAILABLE"):
            c.charge("")
        c = s._Closure(None, None, None)
        with pytest.raises(OperationError, match="PM_REPORT_SOURCE_UNAVAILABLE"):
            c.charge(value + "x")
    print("auth records 2000 allowed / 2001 concealed; actual bytes 16000000 allowed / 16000001 concealed")


def test_exact_source_bound():
    for tail in ("漢", "🫨", "漢🫨"):
        value = exact_text_bytes(2_000_000 - 2, tail)
        assert s.bounded(value) == value
        with pytest.raises(OperationError, match="PM_EXPORT_TOO_LARGE") as e:
            s.bounded(value + "x")
        assert e.value.status == 413
    print("canonical source bytes 2000000 allowed / 2000001 is 413; CJK and non-BMP edges")


def test_exact_content_response_bounds(monkeypatch):
    fixed = fixture_projection()
    for tail in ("漢", "🫨", "漢🫨"):
        # Exercise real formatting/envelope; keep the normal constants unchanged.
        # Markdown one-source literal wrapper adds exactly 17 UTF-8 bytes.
        payload = [""]
        monkeypatch.setattr(r, "blocks", lambda *_: [("body", payload[0])])
        overhead = r.render_fixed(fixed)["byte_length"]
        payload[0] = exact_text_bytes(1_500_000 - overhead, tail)
        out = r.render_fixed(fixed)
        assert out["byte_length"] == 1_500_000
        payload[0] += "x"
        with pytest.raises(OperationError, match="PM_EXPORT_TOO_LARGE"):
            r.render_fixed(fixed)
        # A backslash doubles inside JSON; solve exact full serialized size using ASCII pad.
        payload[0] = "\\" * 600_000 + tail
        base = r.render_fixed(fixed)
        size = len(json.dumps(base, ensure_ascii=False, allow_nan=False).encode())
        pad = 2_000_000 - size
        payload[0] += "a" * pad
        with monkeypatch.context() as calibration:
            calibration.setattr(r, "MAX_RESPONSE_BYTES", 2_100_000)
            candidate = r.render_fixed(fixed)
            delta = 2_000_000 - len(json.dumps(candidate, ensure_ascii=False, allow_nan=False).encode())
        if delta < 0:
            payload[0] = payload[0][:delta]
        else:
            payload[0] += "a" * delta
        out = r.render_fixed(fixed)
        assert len(json.dumps(out, ensure_ascii=False, allow_nan=False).encode()) == 2_000_000
        assert out["byte_length"] <= 1_500_000
        payload[0] += "x"
        with pytest.raises(OperationError, match="PM_EXPORT_TOO_LARGE"):
            r.render_fixed(fixed)
    print("actual 1500000/1500001 content and 2000000/2000001 JSON boundaries, constants unchanged")


def test_cjk_nonbmp_page_edge():
    # body at 58 units per line: 29 wide chars; first panel 49 lines, then 50.
    for char in ("漢", "🫨"):
        value = char * (29 * (49 + 50 * 31))
        blocks = [("body", value)]
        content, pages = l.svg(blocks, "en", "fixed", "Page")
        assert pages == 32
        reconstruct(content, blocks)
        with pytest.raises(OperationError, match="PM_EXPORT_TOO_LARGE"):
            l.svg([("body", value + char)], "en", "fixed", "Page")
    print("CJK and four-byte char: 32 exact panels complete; one character more is 413")


async def test_near_budget_cost_and_read_only(ops):
    # Actual actions establish every unique source. Repeated saved references are synthetic load.
    pm_source_lineage.schema(ops.journal)
    target, plan, work, *_ = await mapped(ops)
    await capture(ops, work)
    frozen = await snapshot(ops, target["workspace_id"])
    obs = await revision(ops, target["workspace_id"], "observation", snapshot_id=frozen["snapshot_id"])
    wid = target["workspace_id"]
    rid = obs["revision_id"]
    base = s._Closure(ops, READER, wid)
    closure = base.collect(rid)
    saved = closure[3]["document"]
    doc = closure[0]["document"]
    assert saved["evidence"]
    saved["evidence"] += [saved["evidence"][0]] * (2000 - base.records)
    ops.db.execute("DROP TRIGGER pm_document_snapshots_update_immutable")
    ops.db.execute("DROP TRIGGER pm_roadmap_revisions_update_immutable")

    def persist():
        digest = s.digest(saved)
        doc["snapshot_digest"] = digest
        ops.db.execute(
            "UPDATE pm_document_snapshots SET document=?,snapshot_digest=? WHERE snapshot_id=?",
            (s.canonical(saved), digest, frozen["snapshot_id"]),
        )
        ops.db.execute(
            "UPDATE pm_roadmap_revisions SET document=?,document_digest=? WHERE revision_id=?",
            (s.canonical(doc), s.digest(doc), rid),
        )

    persist()
    counts = [0]
    ops.db.set_progress_handler(lambda: counts.__setitem__(0, counts[0] + 1000) or 0, 1000)
    changes = ops.db.total_changes
    begin = time.monotonic()
    c = s._Closure(ops, READER, wid)
    try:
        closure = c.collect(rid)
        assert c.records == 2000
        print(
            "2000-record collect seconds",
            round(time.monotonic() - begin, 4),
            "VM instructions approx",
            counts[0],
            "charged bytes",
            c.bytes,
        )
        begin = time.monotonic()
        try:
            s.load_fixed(ops, READER, wid, rid)
            status = 200
        except OperationError as e:
            assert e.code == "PM_EXPORT_TOO_LARGE"
            status = e.status
        print(
            "2000-record entire load seconds",
            round(time.monotonic() - begin, 4),
            "status",
            status,
            "VM cumulative",
            counts[0],
            "db write delta",
            ops.db.total_changes - changes,
        )
        assert ops.db.total_changes == changes
    finally:
        ops.db.set_progress_handler(None, 0)
    saved["evidence"].append(saved["evidence"][0])
    persist()
    with pytest.raises(OperationError, match="PM_REPORT_SOURCE_UNAVAILABLE"):
        s.load_fixed(ops, READER, wid, rid)
    # Use a valid observation document with a large ignored member to measure byte traversal limit.
    saved["evidence"] = saved["evidence"][:1]
    doc["load_padding"] = "x" * 7_800_000
    persist()
    begin = time.monotonic()
    changes = ops.db.total_changes
    c = s._Closure(ops, READER, wid)
    c.collect(rid)
    assert 15_500_000 < c.bytes <= 16_000_000
    with pytest.raises(OperationError, match="PM_EXPORT_TOO_LARGE"):
        s.load_fixed(ops, READER, wid, rid)
    print(
        "near-byte-budget collect+load seconds",
        round(time.monotonic() - begin, 4),
        "charged bytes",
        c.bytes,
        "writes",
        ops.db.total_changes - changes,
    )
    assert ops.db.total_changes == changes
