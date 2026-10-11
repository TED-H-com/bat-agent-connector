"""Literal, bounded Project Brief exports from one authorized historical snapshot."""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import math
import re
import unicodedata

from . import pm_snapshots
from .operations import OperationError

RENDERER_VERSION = 2
MAX_CONTENT_BYTES = 1_500_000
MAX_RESPONSE_BYTES = 2_000_000
MAX_SVG_PAGES = 32
PAGE_WIDTH, PAGE_HEIGHT, PAGE_GAP = 960, 1280, 24
FORMATS = {"markdown": "text/markdown; charset=utf-8", "html": "text/html; charset=utf-8",
           "svg": "image/svg+xml; charset=utf-8"}
SECTIONS = ("goals", "delivery", "blockers", "deviations", "decisions")

_TEXT = {
    "en": {
        "brief": "Project Brief", "historical": "Historical snapshot · fixed source selection",
        "workspace": "Workspace", "as_of": "Snapshot assembled at", "cursor": "Central journal cursor",
        "baseline": "Baseline at snapshot time", "accepted": "Accepted plan", "unavailable": "No accepted plan",
        "candidate": "Candidate document · not the current commitment",
        "boundary": "This report prepares discussion. It does not complete a Review, authorize work, verify quality, or publish anything.",
        "freshness": "Assembly time is not a fresh check of every source. Each saved observation retains its own time.",
        "filtered": "Some original evidence is no longer readable. It is omitted from this authorized view; the view digest differs from the original snapshot digest.",
        "partial": "Source coverage is partial. Omitted or unlinked activity is not assessed and is not a complete-project denominator.",
        "goals": "Goals", "delivery": "Delivery", "blockers": "Blockers", "deviations": "Deviations", "decisions": "Decisions",
        "goals_note": "Original input statuses remain distinct: adoption does not turn an assumption or repository claim into a confirmed fact.",
        "delivery_note": "Work approval is not Definition of Done. A test applies to its recorded commit; a merge is not deployment. Historical deployment proof does not establish current environment health. No completion ratio is assessed.",
        "blockers_note": "These are selected saved attention states and missing captures, not a complete diagnosis or an inferred cause.",
        "deviations_note": "Baseline/result drift has not been assessed. An association with an older plan is not itself a drift finding.",
        "decisions_note": "Only this snapshot's accepted decision and fixed open inputs are shown. Later discussions and priorities were not added.",
        "empty": "No items in this fixed projection; absence is not proof of completion or resolution.",
        "status": "Recorded status", "source": "Source", "sources": "Source references and recorded facts",
        "identity": "Fixed identity and coverage", "coverage": "Saved source coverage", "selection": "Selected work and capture versions",
        "details": "Recorded details · quality remains unassessed", "unknown": "Unknown", "page": "Page",
        "verification": "Recorded verification {status} for commit {commit}",
        "merge": "Merge verified at commit {commit}", "deployment": "Recorded deployment {status}; current environment not assessed",
        "integration": "Integration {status}; PR state remains unknown", "claim": "Work state recorded as {status}; quality unassessed",
        "attention": "Recorded {kind} state: {status}", "missing_capture": "No saved evidence capture is selected for this work",
        "quality": "Original acceptance: {acceptance}; Definition of Done: {dod}",
        "quality_count": "Qualified original requirements: {qualified} / {total}",
        "quality_fixed": "These attributed assessments were explicitly selected at capture time. Later conclusions and current applicability are not substituted.",
        "work_state": "Work state recorded as {status}",
    },
    "zh-TW": {
        "brief": "專案摘要", "historical": "歷史快照 · 固定來源選取",
        "workspace": "工作區", "as_of": "快照彙整時間", "cursor": "中央紀錄游標",
        "baseline": "快照當時的基準", "accepted": "已採用計畫", "unavailable": "尚無已採用計畫",
        "candidate": "候選文件 · 並非目前承諾",
        "boundary": "這份報告用於準備討論；不代表 Review 已完成、工作已授權、品質已驗證，也不會發布任何內容。",
        "freshness": "彙整時間不代表已重新查證每個來源；各筆已保存的觀察仍保留原本時間。",
        "filtered": "部分原始證據目前已無法讀取，因此未包含於這份可讀視圖；視圖摘要值與原始快照摘要值分開保存。",
        "partial": "來源涵蓋不完整；省略或未關聯的活動未經評估，也不能當作整個專案的統計分母。",
        "goals": "目標", "delivery": "交付", "blockers": "阻塞", "deviations": "偏差", "decisions": "決策",
        "goals_note": "資訊保留原始身分；採用計畫不會把假設或 Repo 聲明變成已確認事實。",
        "delivery_note": "工作內容獲准不等於符合 DoD；測試只適用於記錄的 commit，合併不等於部署。歷史部署證據不代表目前環境健康，完成比例尚未評估。",
        "blockers_note": "這裡只呈現選定的已保存狀態與缺少的擷取紀錄，不是完整診斷，也不推論根因。",
        "deviations_note": "尚未評估基準與成果之間的偏差；關聯到較舊計畫，本身不代表發生偏差。",
        "decisions_note": "僅呈現這份快照的採用決策與固定未決資訊，不加入後來的討論或優先序。",
        "empty": "這份固定投影沒有項目；缺少項目不代表已完成或已解決。",
        "status": "記錄狀態", "source": "來源", "sources": "來源引用與已記錄事實",
        "identity": "固定身分與涵蓋範圍", "coverage": "已保存的來源涵蓋", "selection": "所選工作與擷取版本",
        "details": "已記錄細節 · 品質仍未評估", "unknown": "未知", "page": "頁",
        "verification": "已記錄驗證 {status}，適用 commit {commit}",
        "merge": "已查證合併至 commit {commit}", "deployment": "已記錄部署 {status}；目前環境尚未評估",
        "integration": "整合狀態 {status}；PR 狀態仍未知", "claim": "工作狀態記錄為 {status}；品質尚未評估",
        "attention": "已記錄 {kind} 狀態：{status}", "missing_capture": "這份工作尚未選取已保存的證據擷取紀錄",
        "quality": "原始驗收條件：{acceptance}；完成定義：{dod}",
        "quality_count": "符合驗收與完成定義的原始需求：{qualified} / {total}",
        "quality_fixed": "這些具責任歸屬的評估由整理證據時明確選取；不會替換成後來的結論，也不代表已重查目前適用性。",
        "work_state": "工作狀態記錄為 {status}",
    },
}
_STATUSES = {
    "en": {"user_confirmed": "User confirmed", "repo_evidence": "Attributed repository claim",
           "agent_assumption": "Agent assumption", "unresolved": "Unresolved", "conflicting": "Conflicting",
           "not_applicable": "Recorded not applicable", "unassessed": "Unassessed", "unknown": "Unknown",
           "quality_unassessed": "Quality unassessed", "partial_source_states": "Partial saved source states",
           "fixed_quality_assessments": "Explicit original quality assessments", "qualified": "Qualified", "not_qualified": "Not qualified",
           "accepted_sources": "Accepted sources", "recorded_sources": "Recorded sources", "unavailable": "Unavailable"},
    "zh-TW": {"user_confirmed": "使用者已確認", "repo_evidence": "有來源歸屬的 Repo 聲明",
              "agent_assumption": "Agent 假設", "unresolved": "尚未決定", "conflicting": "存在衝突",
              "not_applicable": "已記錄不適用", "unassessed": "尚未評估", "unknown": "未知",
              "quality_unassessed": "品質尚未評估", "partial_source_states": "部分已保存來源狀態",
              "fixed_quality_assessments": "明確選取的原始品質評估", "qualified": "符合品質條件", "not_qualified": "尚未符合品質條件",
              "accepted_sources": "已採用的來源", "recorded_sources": "已記錄的來源", "unavailable": "無可用資料",
              "passed": "通過", "failed": "失敗", "merged": "已合併", "verified": "已查證",
              "unverified": "未查證", "done": "回報完成", "accepted": "內容獲准", "waiting": "等待中",
              "blocked": "阻塞", "needs_attention": "需要處理", "needs_ted": "需要處理", "uncertain": "結果不明",
              "adopted": "已採用"},
}


def _bad(code, message, status=422):
    return OperationError(code, message, status)


def _literal(value):
    """Keep controls visible instead of dropping them or emitting invalid XML."""
    text = str(value)
    return "".join(f"\\u{ord(char):04x}" if (
        ord(char) < 32 and char not in "\n\r\t" or 0x7F <= ord(char) <= 0x9F
        or 0xD800 <= ord(char) <= 0xDFFF or ord(char) in {0xFFFE, 0xFFFF}
        or unicodedata.bidirectional(char) in {"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"}
    ) else char for char in text)


def _json(value):
    return _literal(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2))


def _stamp(value, text):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return text["unknown"]
    try:
        return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", " UTC")
    except (OverflowError, OSError, ValueError):
        return text["unknown"]


def _status(value, locale):
    label = _STATUSES[locale].get(value)
    return f"{label} ({value})" if label else str(value)


def _source(snapshot, source):
    kind = source["kind"]
    result = {"reference": source}
    if kind == "evidence":
        evidence = next(v for v in snapshot["evidence"] if v["evidence_id"] == source["evidence_id"])
        result.update({key: evidence[key] for key in ("source_kind", "source_id", "fact_digest", "source_observed_at",
                                                    "captured_at", "journal_cursor", "document")})
    elif kind == "plan_input":
        value = next(v for v in snapshot["baseline"]["document"]["selected_inputs"] if v["input_id"] == source["input_id"])
        result["provenance"] = {key: value.get(key) for key in ("status", "confirmed_by", "observed_at", "applicable_revision", "evidence_verification")}
    elif kind == "plan_adoption":
        value = snapshot["baseline"]["adoption"]
        result["adoption"] = {key: value.get(key) for key in ("adopted_by", "created_at", "scope_revision", "commit_sha", "tree_sha")}
    elif kind == "work_binding":
        value = next(v for v in snapshot["work"] if v["binding"]["binding_id"] == source["binding_id"])
        result.update(binding=value["binding"], capture=value["capture"], coverage=value["coverage"])
    else:
        raise _bad("PM_EXPORT_TEMPLATE_UNAVAILABLE", "Unknown source kind in this Brief template", 409)
    return result


def _item_text(item, source, text, locale, *, quality=False):
    kind, details = item["kind"], item.get("details", {})
    value = _status(item["status"], locale)
    if kind == "observed_verification":
        return text["verification"].format(status=value, commit=details["candidate_commit"])
    if kind == "pr_merge":
        return text["merge"].format(commit=details["merged_sha"])
    if kind == 'quality_assessment':
        labels = {'accepted': '符合', 'rejected': '不符合', 'unassessed': '尚未評估', 'not_defined': '尚未定義'} if locale == 'zh-TW' else {}
        return text['quality'].format(**{key: labels.get(details[key], details[key]) for key in ('acceptance', 'dod')})
    if kind == 'work_claim' and quality:
        return text['work_state'].format(status=value)
    if kind in {"deployment", "integration_delivery", "work_claim"}:
        return text[{"deployment": "deployment", "integration_delivery": "integration", "work_claim": "claim"}[kind]].format(status=value)
    if kind == "recorded_attention_state":
        return text["attention"].format(kind=source["source_kind"], status=value)
    return text["missing_capture"] if kind == "missing_capture" else item["text"]


def _blocks(snapshot, locale):
    text = _TEXT[locale]
    baseline = snapshot["baseline"]
    blocks = [("title", text["brief"] + " · " + snapshot["title"]), ("note", text["historical"]),
              ("body", f"{text['workspace']}: {snapshot['workspace_name']}"),
              ("body", f"{text['as_of']}: {_stamp(snapshot['as_of'], text)}"),
              ("body", f"{text['baseline']}: {text['accepted'] if baseline['document'] else text['unavailable']}"),
              ("note", text["boundary"]), ("note", text["freshness"])]
    coverage = snapshot["coverage"]
    if snapshot["visibility"]["filtered"]:
        blocks.append(("warning", text["filtered"]))
    if any(coverage.get(key) for key in ("projects_truncated", "work_truncated", "evidence_truncated", "bytes_truncated")):
        blocks.append(("warning", text["partial"]))
    sources, identifiers = [], {}
    for section in SECTIONS:
        view = snapshot["brief"][section]
        blocks.extend([("heading", text[section]), ("note", _status(view["status"], locale)), ("note", text[section + "_note"])])
        if view.get('quality'):
            summary = view['quality']
            blocks.extend([('body', text['quality_count'].format(qualified=summary['qualified_requirements'], total=summary['total_requirements'])),
                ('note', text['quality_fixed']), ('source', _json(summary))])
        if not view["items"]:
            blocks.append(("body", text["empty"]))
        for item in view["items"]:
            source_key = _json(item["source"])
            if source_key not in identifiers:
                identifiers[source_key] = f"S{len(sources) + 1:03}"
                sources.append((identifiers[source_key], _source(snapshot, item["source"])))
            identifier = identifiers[source_key]
            source = next(value for ref, value in sources if ref == identifier)
            blocks.extend([("body", _item_text(item, source, text, locale, quality=bool(view.get('quality')))),
                           ("note", f"{text['status']}: {_status(item['status'], locale)} · {text['source']}: {identifier}")])
    if snapshot.get("maintenance"):
        from .pm_maintenance_review_exports import blocks as maintenance_blocks
        blocks.extend(maintenance_blocks(snapshot["maintenance"], locale))
    blocks.append(("heading", text["identity"]))
    identity = {key: snapshot[key] for key in ("snapshot_id", "workspace_id", "server_id", "workspace_version", "sequence", "as_of",
                "journal_cursor", "template_version", "snapshot_digest", "view_digest", "created_by", "operation_id")}
    identity["baseline"] = {key: baseline.get(key) for key in ("status", "plan_head_version", "document_revision", "scope_revision", "document_digest")}
    identity.update(renderer_version=_renderer_version(snapshot), visibility=snapshot["visibility"])
    blocks.append(("source", _json(identity)))
    if snapshot["candidate_document"]:
        blocks.extend([("subheading", text["candidate"]), ("source", _json(snapshot["candidate_document"]))])
    blocks.extend([("subheading", text["coverage"]), ("source", _json({"snapshot": coverage,
        "brief": {key: snapshot["brief"][key]["coverage"] for key in SECTIONS},
        "visible_saved_selection_counts": {"projects": len(snapshot["projects"]), "work": len(snapshot["work"]), "evidence": len(snapshot["evidence"])},
        "counts_are_complete_project_totals": False}))])
    selections = [{"work_item_id": value["work_item_id"], "metadata_version": value["metadata_version"],
        "binding_id": value["binding"]["binding_id"], "plan_revision_id": value["binding"]["plan_revision_id"],
        "capture": value["capture"], "evidence_ids": value["evidence_ids"], "coverage": value["coverage"]} for value in snapshot["work"]]
    blocks.extend([("subheading", text["selection"]), ("source", _json(selections)), ("heading", text["sources"])])
    for identifier, source in sources:
        blocks.extend([("subheading", identifier), ("source", _json(source))])
    return [(kind, _literal(value)) for kind, value in blocks]


def _markdown(blocks):
    result = []
    for kind, value in blocks:
        # Source text is literal CommonMark code, including raw HTML, Markdown
        # delimiters and URLs. Dynamic fences cannot be closed by that text.
        fence = "`" * max(3, max((len(v) for v in re.findall(r"`+", value)), default=0) + 1)
        if kind in {"title", "heading", "subheading"} and "\n" not in value and "\r" not in value:
            result.append({"title": "# ", "heading": "## ", "subheading": "### "}[kind] + fence + " " + value + " " + fence)
        else:
            result.append(fence + "text\n" + value + "\n" + fence)
    return "\n\n".join(result) + "\n"


_CSS = """
:root{color-scheme:light;--bg:#f6f7f9;--panel:#fff;--text:#1d2330;--muted:#5d6676;--line:#dfe3ea}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,'Segoe UI','Noto Sans TC',sans-serif}
main{max-width:960px;margin:24px auto;padding:28px;background:var(--panel);border:1px solid var(--line);border-radius:10px}
h1,h2,h3{overflow-wrap:anywhere}h1{font-size:20px}h2{font-size:16px;border-top:1px solid var(--line);padding-top:16px;margin-top:24px}h3{font-size:15px}
pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0}.note,.source{font-size:14px;color:var(--muted)}
.warning{border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:8px 0;font-weight:600}
@media(max-width:640px){main{margin:0;padding:16px;border:0;border-radius:0}}
@media print{body{background:white}main{max-width:none;margin:0;padding:0;border:0}h1,h2,h3{break-after:avoid}pre{orphans:3;widows:3}}
@page{size:A4;margin:16mm}
""".strip()


def _html(blocks, locale):
    nodes = []
    for kind, value in blocks:
        tag = {"title": "h1", "heading": "h2", "subheading": "h3"}.get(kind, "pre")
        nodes.append(f'<{tag} class="{kind}">{html.escape(value, quote=True)}</{tag}>')
    return (f'<!doctype html>\n<html lang="{locale}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            f'<title>{html.escape(blocks[0][1], quote=True)}</title><style>{_CSS}</style></head>'
            '<body><main>' + "\n".join(nodes) + '</main></body></html>\n')


def _wrap(value, columns):
    """Conservative wrapping with word boundaries and intact common graphemes."""
    result = []
    for line in value.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4).split("\n"):
        clusters = []
        for char in line:
            extender = (unicodedata.category(char) in {"Mn", "Mc", "Me"}
                        or char == "\u200d" or 0x1F3FB <= ord(char) <= 0x1F3FF)
            regional_pair = (0x1F1E6 <= ord(char) <= 0x1F1FF and clusters
                             and len(clusters[-1]) == 1 and 0x1F1E6 <= ord(clusters[-1]) <= 0x1F1FF)
            if clusters and (extender or clusters[-1].endswith("\u200d") or regional_pair):
                clusters[-1] += char
            else:
                clusters.append(char)
        current, width = [], 0
        for cluster in clusters:
            units = 2 if any(ord(char) > 0xFFFF for char in cluster) else 1
            if width + units > columns and current:
                split = max((i + 1 for i, (char, _) in enumerate(current) if char.isspace()), default=0)
                # Avoid tiny lines when the only space precedes a long ID.
                split = split if split >= len(current) // 2 else len(current)
                result.append("".join(char for char, _ in current[:split]))
                current = current[split:]
                width = sum(size for _, size in current)
            current.append((cluster, units))
            width += units
        result.append("".join(char for char, _ in current))
    return result


def _svg(blocks, locale, snapshot_id):
    pages, page, y = [], [], 76
    for kind, value in blocks:
        size = 20 if kind == "title" else 16 if kind == "heading" else 14 if kind in {"source", "note"} else 15
        height = size + 8
        y += 12 if kind in {"title", "heading", "subheading"} else 4
        for line in _wrap(value, (PAGE_WIDTH - 80) // size):
            if y + height > PAGE_HEIGHT - 52:
                pages.append(page)
                if len(pages) >= MAX_SVG_PAGES:
                    raise _bad("PM_EXPORT_TOO_LARGE", "A complete SVG Brief exceeds the page bound; select a smaller snapshot", 413)
                page, y = [], 76
            page.append((y, kind, size, line))
            y += height
    pages.append(page)
    height = len(pages) * (PAGE_HEIGHT + PAGE_GAP) - PAGE_GAP
    def escaped(value):
        return html.escape(value, quote=True)
    nodes = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{PAGE_WIDTH}" height="{height}" viewBox="0 0 {PAGE_WIDTH} {height}" role="img" xml:lang="{locale}">',
             f'<title>{escaped(blocks[0][1])}</title>', f'<desc>{escaped(_TEXT[locale]["boundary"])}</desc>']
    for index, lines in enumerate(pages):
        nodes.append(f'<g id="page-{index + 1}" transform="translate(0 {index * (PAGE_HEIGHT + PAGE_GAP)})">')
        nodes.append(f'<rect width="{PAGE_WIDTH}" height="{PAGE_HEIGHT}" fill="#fff" stroke="#dfe3ea"/>')
        nodes.append('<g font-family="system-ui, Segoe UI, Noto Sans TC, sans-serif" fill="#1d2330">')
        nodes.append(f'<text x="40" y="36" font-size="14" fill="#5d6676">{escaped(_TEXT[locale]["brief"] + " · " + snapshot_id)}</text>')
        for offset, kind, size, line in lines:
            color = '#5d6676' if kind in {"note", "source"} else '#1d2330'
            weight = 600 if kind in {"title", "heading", "subheading", "warning"} else 400
            nodes.append(f'<text x="40" y="{offset}" font-size="{size}" fill="{color}" font-weight="{weight}" xml:space="preserve">{escaped(line)}</text>')
        nodes.append(f'<text x="40" y="{PAGE_HEIGHT - 24}" font-size="14" fill="#5d6676">{escaped(_TEXT[locale]["page"])} {index + 1} / {len(pages)}</text>')
        nodes.append('</g></g>')
    return "\n".join([*nodes, '</svg>']) + "\n", len(pages)


def _renderer_version(snapshot):
    return 3 if snapshot['brief']['delivery'].get('quality') else RENDERER_VERSION if snapshot.get('maintenance') else 1


def render_brief(ops, principal, workspace_id, snapshot_id, *, format="markdown", locale="en"):
    """Read an exact authorized snapshot and return a complete literal export.

    No schema installation, durable effect, file write, provider call, latest
    state lookup, model invocation, or publication is performed here.
    """
    if not isinstance(format, str) or format not in FORMATS or not isinstance(locale, str) or locale not in _TEXT:
        raise _bad("INVALID_PARAMS", "Choose markdown, html or svg and locale en or zh-TW")
    snapshot = pm_snapshots.get_snapshot(ops, principal, workspace_id, snapshot_id)["snapshot"]
    if snapshot["template_version"] != 1:
        raise _bad("PM_EXPORT_TEMPLATE_UNAVAILABLE", "This snapshot template has no compatible Brief renderer", 409)
    blocks = _blocks(snapshot, locale)
    pages = None
    if format == "svg":
        content, pages = _svg(blocks, locale, snapshot_id)
    else:
        content = _markdown(blocks) if format == "markdown" else _html(blocks, locale)
    raw = content.encode("utf-8")
    result = {"version": 1, "workspace_id": workspace_id, "snapshot_id": snapshot_id,
        "snapshot_digest": snapshot["snapshot_digest"], "view_digest": snapshot["view_digest"],
        "template_version": snapshot["template_version"], "renderer_version": _renderer_version(snapshot),
        "locale": locale, "format": format, "media_type": FORMATS[format], "content": content,
        "byte_length": len(raw), "output_digest": hashlib.sha256(raw).hexdigest(), "page_count": pages,
        "coverage": {"snapshot": snapshot["coverage"], "visibility": snapshot["visibility"],
                     "projection": "project_brief_with_maintenance_review" if snapshot.get("maintenance") else "project_brief_only", "other_management_views": "not_rendered",
                     "referenced_sources": "fixed_authorized_view", "unreferenced_evidence": "not_expanded",
                     "content_truncated": False}, "execution_authorized": False, "review_completed": False,
        "published": False}
    if len(raw) > MAX_CONTENT_BYTES or len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise _bad("PM_EXPORT_TOO_LARGE", "The complete Brief exceeds the UTF-8 output bound; select a smaller snapshot", 413)
    return result
