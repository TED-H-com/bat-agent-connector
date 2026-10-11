"""One authorized snapshot, three literal report formats, no new PM facts."""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import pytest

from bat_agent_connector import pm_brief_exports as exports
from bat_agent_connector import pm_discussions, pm_snapshots, work_items
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.test_pm_discussions import OUTSIDER, OWNER, READER, record, workspace
from tests.test_pm_evidence import act, bind_work, capture, link, prepared, task
from tests.test_pm_plans import adopt, adoption, draft, planning
from tests.test_pm_snapshots import accepted, snapshot, work_for
from tests.test_pm_snapshots import ops as ops_fixture

ops = ops_fixture


class Document(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.tags, self.attributes, self.text = [], [], []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)

    def handle_data(self, data):
        self.text.append(data)


def visible(result):
    if result["format"] == "html":
        return "\n".join(Document(result["content"]).text)
    if result["format"] == "svg":
        return "\n".join(ET.fromstring(result["content"]).itertext())
    return result["content"]


def render(ops, saved, **kwargs):
    return exports.render_brief(ops, READER, saved["workspace_id"], saved["snapshot_id"], **kwargs)


@pytest.mark.parametrize("format", ["markdown", "html", "svg"])
@pytest.mark.parametrize("locale", ["en", "zh-TW"])
async def test_report_keeps_fixed_identity_statuses_and_unassessed_quality(ops, format, locale):
    target, revision, *_ = await accepted(ops)
    work = await work_for(ops, target)
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    ops.journal.record_observed_verification(source["task_id"], {
        "candidate_commit": "1" * 40, "tree_hash": "2" * 40, "command": "pytest",
        "exit_code": 0, "log_ref": "saved-check", "output_sha256": "3" * 64, "source": "observed_runner"})
    await capture(ops, work)
    saved = await snapshot(ops, target["workspace_id"])
    changes = ops.db.total_changes
    result = render(ops, saved, format=format, locale=locale)
    text = visible(result)
    for value in (saved["snapshot_id"], revision["revision_id"], "user_confirmed", "quality_unassessed", "completion_ratio", "not_performed"):
        assert value in "".join(text.split())
    assert "".join(text.split()).count("1" * 40) >= 1
    assert result["snapshot_digest"] == saved["snapshot_digest"]
    assert result["view_digest"] == saved["view_digest"]
    assert result["byte_length"] == len(result["content"].encode("utf-8"))
    assert result["output_digest"] == hashlib.sha256(result["content"].encode("utf-8")).hexdigest()
    assert result["coverage"]["projection"] == "project_brief_only"
    assert result["coverage"]["other_management_views"] == "not_rendered"
    assert not result["execution_authorized"] and not result["review_completed"] and not result["published"]
    assert ops.db.total_changes == changes
    assert render(ops, saved, format=format, locale=locale) == result
    if locale == "zh-TW":
        assert all(word in text for word in ("目標", "交付", "阻塞", "偏差", "決策", "尚未評估"))
    else:
        assert all(word in text for word in ("Goals", "Delivery", "Blockers", "Deviations", "Decisions"))


async def test_new_plan_capture_and_discussion_cannot_enter_old_export(ops):
    target, first, *_ = await accepted(ops)
    work = await work_for(ops, target)
    await bind_work(ops, work, plan_revision_id=first["revision_id"])
    await capture(ops, work)
    saved = await snapshot(ops, target["workspace_id"])
    before = {format: render(ops, saved, format=format) for format in exports.FORMATS}
    next_plan = await draft(ops, target, title="A later scope direction")
    adopt(adoption(ops, target, next_plan["revision_id"], previous=first["revision_id"]))
    await bind_work(ops, work, plan_revision_id=next_plan["revision_id"])
    current = work_items._get_item(ops.db, work["work_item_id"])
    await act(ops, "work_item.update", {"work_item_id": work["work_item_id"]}, {"state": "done"},
              {"expected_version": current["version"]})
    await capture(ops, work)
    await record(ops, target, slot="risk", status="unresolved", value="A later discussion question")
    await snapshot(ops, target["workspace_id"])
    for format in exports.FORMATS:
        assert render(ops, saved, format=format) == before[format]
        assert next_plan["revision_id"] not in before[format]["content"]
    assert pm_snapshots.get_snapshot(ops, OWNER, target["workspace_id"], saved["snapshot_id"])["snapshot"] == saved


@pytest.mark.parametrize("format", ["markdown", "html", "svg"])
async def test_source_revocation_removes_fact_derived_item_and_source_link(ops, format):
    work = await prepared(ops)
    source = task(ops)
    await link(ops, work, "task", source["task_id"])
    ops.db.execute("UPDATE tasks SET state='needs_ted' WHERE task_id=?", (source["task_id"],))
    await capture(ops, work)
    saved = await snapshot(ops, work["workspace_id"])
    before = render(ops, saved, format=format)
    assert source["task_id"] in before["content"]
    ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (source["task_id"],))
    after = render(ops, saved, format=format)
    assert source["task_id"] not in after["content"] and "needs_ted" not in after["content"]
    assert after["snapshot_digest"] == before["snapshot_digest"]
    assert after["view_digest"] != before["view_digest"] and after["output_digest"] != before["output_digest"]
    assert after["coverage"]["visibility"]["filtered"]
    assert "Some original evidence is no longer readable" in visible(after)


@pytest.mark.parametrize("format", ["markdown", "html", "svg"])
async def test_observe_membership_and_exact_workspace_are_required_for_every_export(ops, format):
    work = await prepared(ops)
    saved = await snapshot(ops, work["workspace_id"])
    for principal in (OUTSIDER, Principal(READER.actor, frozenset({"manage"}))):
        with pytest.raises(OperationError):
            exports.render_brief(ops, principal, work["workspace_id"], saved["snapshot_id"], format=format)
    other = await workspace(ops)
    with pytest.raises(OperationError, match="PM_SNAPSHOT_NOT_FOUND"):
        exports.render_brief(ops, OWNER, other["workspace_id"], saved["snapshot_id"], format=format)
    ops.db.execute("DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?", (work["workspace_id"], READER.actor))
    with pytest.raises(OperationError, match="NOT_FOUND"):
        render(ops, saved, format=format)


async def test_candidate_and_missing_capture_are_visible_without_becoming_commitment(ops):
    target, *_ = await planning(ops)
    candidate = await draft(ops, target)
    await work_for(ops, target)
    saved = await snapshot(ops, target["workspace_id"])
    result = render(ops, saved, format="html")
    text = visible(result)
    assert "No accepted plan" in text and "No saved evidence capture is selected" in text
    assert "not the current commitment" in text and candidate["revision_id"] in text
    assert '"is_current_commitment": false' in text
    assert '"completion_ratio": null' in text and '"cost": "unknown"' in text
    assert ops.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert ops.db.execute("SELECT COUNT(*) FROM pm_evidence_captures").fetchone()[0] == 0


async def malicious_snapshot(ops):
    target, *_ = await planning(ops)
    payload = '<script>alert("literal")</script>\n```\n<img src="https://example.invalid/x">\n[click](javascript:alert(1))\nhttps://example.invalid/'
    claim = await record(ops, target, slot="scope", status="repo_evidence", value=payload,
        source_ref={"kind": "repository", "ref": "https://example.invalid/repo", "path": "<svg onload=alert(1)>", "revision": "claimed-revision"})
    current = pm_discussions.get_thread(ops, OWNER, **target)
    await act(ops, "pm.thread.plan.revise", target,
        {"selected_input_ids": [*[i["input_id"] for i in current["draft"]["selected_inputs"]], claim["input_id"]], "rationale": "Retain literal repository claim"},
        {"expected_version": current["thread"]["version"], "expected_draft_id": current["draft"]["draft_id"]})
    proposal = await draft(ops, target)
    adopt(adoption(ops, target, proposal["revision_id"]))
    return await snapshot(ops, target["workspace_id"], title='<Brief & "quoted">'), payload


async def test_html_and_svg_emit_only_passive_nodes_with_literal_source_content(ops):
    saved, payload = await malicious_snapshot(ops)
    document = Document(render(ops, saved, format="html")["content"])
    assert set(document.tags) <= {"html", "head", "meta", "title", "style", "body", "main", "h1", "h2", "h3", "pre"}
    assert not any(key.lower().startswith("on") or key in {"href", "src"} for key, _ in document.attributes)
    assert payload in "\n".join(document.text)
    assert "repo_evidence" in "\n".join(document.text) and "unverified_claim" in "\n".join(document.text)
    assert any(key == "content" and "default-src 'none'" in value for key, value in document.attributes)
    svg = ET.fromstring(render(ops, saved, format="svg")["content"])
    assert {node.tag.split("}")[-1] for node in svg.iter()} <= {"svg", "title", "desc", "g", "rect", "text"}
    assert not any(key.endswith("href") or key.lower().startswith("on") for node in svg.iter() for key in node.attrib)
    assert "".join(payload.split()) in "".join("".join(svg.itertext()).split())


async def test_markdown_source_cannot_close_its_literal_fence_or_create_links(ops):
    saved, payload = await malicious_snapshot(ops)
    content = render(ops, saved)["content"]
    assert payload in content
    fence, outside = None, []
    for line in content.splitlines():
        if fence is not None:
            if line == fence:
                fence = None
        elif re.fullmatch(r"`{3,}text", line):
            fence = line[:-4]
        else:
            outside.append(line)
    assert fence is None
    assert not any("javascript:" in line or "https://" in line or "<script>" in line for line in outside)
    assert all(not line or re.fullmatch(r"#{1,3} (`{3,}) .+ \1", line) for line in outside)


async def test_partial_source_selection_remains_explicit_and_never_looks_like_full_completion(ops, monkeypatch):
    work = await prepared(ops)
    await capture(ops, work)
    monkeypatch.setattr(pm_snapshots, "MAX_EVIDENCE", 0)
    saved = await snapshot(ops, work["workspace_id"])
    assert saved["coverage"]["evidence_truncated"]
    for format in exports.FORMATS:
        result = render(ops, saved, format=format)
        text = visible(result)
        assert "Source coverage is partial" in text
        assert result["coverage"]["snapshot"]["evidence_truncated"]
        assert not result["coverage"]["content_truncated"]
        assert '"counts_are_complete_project_totals": false' in text


async def test_output_limits_measure_complete_utf8_content_and_response_without_truncation(ops, monkeypatch):
    work = await prepared(ops)
    saved = await snapshot(ops, work["workspace_id"], title="跨語言摘要" * 20)
    result = render(ops, saved, format="html", locale="zh-TW")
    actual = len(result["content"].encode("utf-8"))
    assert actual > len(result["content"])
    monkeypatch.setattr(exports, "MAX_CONTENT_BYTES", actual)
    assert render(ops, saved, format="html", locale="zh-TW") == result
    monkeypatch.setattr(exports, "MAX_CONTENT_BYTES", actual - 1)
    with pytest.raises(OperationError, match="PM_EXPORT_TOO_LARGE") as error:
        render(ops, saved, format="html", locale="zh-TW")
    assert error.value.status == 413
    monkeypatch.setattr(exports, "MAX_CONTENT_BYTES", actual)
    wire_size = len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    monkeypatch.setattr(exports, "MAX_RESPONSE_BYTES", wire_size)
    assert render(ops, saved, format="html", locale="zh-TW") == result
    monkeypatch.setattr(exports, "MAX_RESPONSE_BYTES", wire_size - 1)
    with pytest.raises(OperationError, match="PM_EXPORT_TOO_LARGE"):
        render(ops, saved, format="html", locale="zh-TW")


def test_svg_paginates_long_unicode_and_unbroken_identifiers_without_losing_text(monkeypatch):
    original = ("長文字與身分識別" * 80) + ("abc123" * 180) + " 👩‍💻 保留結尾"
    blocks = [("title", "長文件"), ("body", original)]
    content, pages = exports._svg(blocks, "zh-TW", "pmsnap_" + "1" * 32)
    assert pages > 1
    root = ET.fromstring(content)
    lines = [node.text or "" for node in root.iter() if node.tag.endswith("}text") and "{http://www.w3.org/XML/1998/namespace}space" in node.attrib]
    assert "".join(lines) == "長文件" + original
    assert "…" not in content and "href=" not in content
    monkeypatch.setattr(exports, "MAX_SVG_PAGES", pages - 1)
    with pytest.raises(OperationError, match="PM_EXPORT_TOO_LARGE"):
        exports._svg(blocks, "zh-TW", "pmsnap_" + "1" * 32)


def test_illegal_xml_controls_and_bidi_overrides_remain_visible_not_active():
    value = "literal\x00\x1f\ud800\ufffe\u202eEND"
    blocks = [("title", "Controls"), ("body", exports._literal(value))]
    content, _ = exports._svg(blocks, "en", "pmsnap_" + "1" * 32)
    text = "".join(ET.fromstring(content).itertext())
    assert "literal\\u0000\\u001f\\ud800\\ufffe\\u202eEND" in text
    assert "\u202e" not in text


def test_readable_wrap_preserves_word_boundaries_and_joined_unicode():
    sentence = "A sentence should wrap at a word boundary"
    lines = exports._wrap(sentence, 20)
    assert "".join(lines) == sentence
    assert all(line.endswith(" ") for line in lines[:-1])
    unicode = "abcdefghijklm👩‍💻🇹🇼 é結尾"
    lines = exports._wrap(unicode, 15)
    assert "".join(lines) == unicode
    assert any("👩‍💻" in line for line in lines) and any("🇹🇼" in line for line in lines)
    assert any("é" in line for line in lines)


@pytest.mark.parametrize("params", [{"format": "pdf"}, {"format": {}}, {"locale": "zh"}, {"locale": ["en"]}])
async def test_unsupported_format_or_locale_is_not_a_silent_fallback(ops, params):
    work = await prepared(ops)
    saved = await snapshot(ops, work["workspace_id"])
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        render(ops, saved, **params)
