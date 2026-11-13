"""One fixed scope and roadmap report, six portable literal outputs."""
from __future__ import annotations

import hashlib
import json

from . import pm_brief_exports as brief
from . import pm_roadmap_report_layout as layout
from . import pm_roadmap_report_sources as sources
from .operations import OperationError

MAX_CONTENT_BYTES = 1_500_000
MAX_RESPONSE_BYTES = 2_000_000
RENDERER_VERSION = 1
FORMATS = brief.FORMATS
_TEXT = {
    'en': {**brief._TEXT['en'], 'title': 'Scope and roadmap report', 'schedule': 'Selected scheduling baseline',
        'requirement': 'Requirement', 'milestone': 'Milestone', 'original_date': 'Original baseline date',
        'baseline_date': 'Selected baseline date', 'forecast_date': 'Forecast date', 'actual_date': 'Actual finish date',
        'dependencies': 'Dependencies', 'historical_plan': 'Historically adopted scope',
        'actual_note': 'Actual finish dates are unknown: no actual-date record exists yet. Commit, merge, review and acceptance times are not actual finish dates.',
        'scope_note': 'Requirement IDs belong to their exact plan revision. Shared work and repeated milestones are not additional deliverables. Dependencies do not predict dates or a critical path.',
        'forecast_note': 'Forecast dates are those selected by this observation, not the current forecast. Quality describes original selected assessments; current applicability is not evaluated.',
        'assessment': 'Original quality and coverage', 'requirements': 'Accepted requirements', 'inputs': 'Original scope inputs',
        'selection_note': 'A snapshot ID alone does not identify this report: the observation fixes its baseline and forecast too.'},
    'zh-TW': {**brief._TEXT['zh-TW'], 'title': '範圍與路線圖報告', 'schedule': '所選排程基準',
        'requirement': '需求', 'milestone': '里程碑', 'original_date': '原始基準日期',
        'baseline_date': '所選基準日期', 'forecast_date': '預測日期', 'actual_date': '實際完成日期',
        'dependencies': '依賴', 'historical_plan': '歷史已採用範圍',
        'actual_note': '實際完成日期皆為未知：目前尚無實際日期紀錄。Commit、合併、審查與驗收時間不是實際完成日期。',
        'scope_note': '需求 ID 屬於其確切計畫版本；共用工作與重複里程碑不會增加交付物數量。依賴不會推算日期或關鍵路徑。',
        'forecast_note': '預測日期是這筆觀察選定的版本，並非目前預測。品質呈現原始選定評估，目前適用性尚未評估。',
        'assessment': '原始品質與涵蓋範圍', 'requirements': '已採用需求', 'inputs': '原始範圍輸入',
        'selection_note': '快照 ID 本身無法識別這份報告：觀察紀錄也固定了其基準與預測。'},
}


def capabilities():
    return {'version': 1, 'read': 'pm_roadmap_report_get', 'read_only': True, 'required_scopes': ['observe'],
        'template_version': 1, 'renderer_versions': [1], 'formats': list(FORMATS), 'locales': list(_TEXT),
        'source': 'roadmap_observation', 'withdrawal': 'whole_report', 'max_content_bytes': MAX_CONTENT_BYTES,
        'max_response_bytes': MAX_RESPONSE_BYTES, 'max_svg_pages': layout.MAX_SVG_PAGES,
        'unicode_version': layout.ucd.UNICODE_VERSION, 'materialization': False, 'native_save': False}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2)


def blocks(fixed, locale):
    """All formats consume these same complete, ordered normalized literals."""
    text = _TEXT[locale]
    result = [('title', text['title']), ('note', text['historical']), ('note', text['selection_note']),
              ('note', text['boundary']), ('note', text['freshness']), ('note', text['actual_note']),
              ('note', text['scope_note']), ('note', text['forecast_note']), ('heading', text['identity'])]
    fields = ('workspace_id', 'observation_revision_id', 'snapshot_id', 'snapshot_digest', 'snapshot_view_digest',
              'baseline_revision_id', 'forecast_revision_id', 'baseline_digest', 'forecast_digest', 'observation_digest', 'source_set_digest', 'view_digest')
    result.append(('source', _json({key: fixed[key] for key in fields})))
    snapshot, baseline = fixed['snapshot'], fixed['baseline']
    result += [('body', text['workspace'] + ': ' + snapshot['workspace_name']),
        ('body', text['as_of'] + ': ' + brief._stamp(snapshot['as_of'], text)),
        ('body', text['cursor'] + ': ' + str(snapshot['journal_cursor'])), ('heading', text['baseline'])]
    accepted = snapshot['baseline']
    result.append(('body', text['accepted'] if accepted['document'] else text['unavailable']))
    result.append(('source', _json({k: v for k, v in accepted.items() if k != 'document'})))
    if accepted['document']:
        _scope_blocks(result, accepted['document'], text)
    if snapshot.get('candidate_document'):
        result += [('subheading', text['candidate']), ('source', _json(snapshot['candidate_document']))]
    for plan in fixed['historical_plans']:
        result += [('heading', text['historical_plan'] + ': ' + plan['revision_id'])]
        _scope_blocks(result, plan['document'], text)
    result += [('heading', text['schedule']), ('body', baseline['document']['title']),
               ('body', baseline['document']['rationale']), ('source', baseline['revision_id'])]
    forecasts = {m['milestone_id']: m['forecast_date'] for m in (fixed['forecast'] or {}).get('document', {}).get('milestones', [])}
    observed = {m['milestone_id']: m for m in fixed['observation']['document']['milestones']}
    for row in baseline['document']['milestones']:
        result.append(('subheading', text['milestone'] + ': ' + row['milestone_id'] + ' · ' + row['title']))
        for key, value in [('original_date', row['original_baseline_date']), ('baseline_date', row['baseline_date']),
                           ('forecast_date', forecasts.get(row['milestone_id'])), ('actual_date', None)]:
            result.append(('body', text[key] + ': ' + (value or text['unknown'])))
        result += [('body', text['dependencies'] + ': ' + _json(row['depends_on'])), ('source', _json(row)),
                   ('body', text['assessment']), ('source', _json(observed.get(row['milestone_id'], {})))]
    result += [('source', _json({'changes': baseline['document']['changes'], 'forecast': fixed['forecast']})),
               ('heading', text['assessment']), ('source', _json(fixed['quality']) if fixed['quality'] else text['unknown']),
               ('note', text['quality_fixed']), ('source', _json(fixed['coverage'])),
               ('heading', text['sources']), ('source', _json(fixed['source_manifest']))]
    evidence_ids = {eid for m in observed.values() for eid in m['evidence_ids']}
    for entry in snapshot['evidence']:
        if entry['evidence_id'] in evidence_ids:
            result.append(('source', _json(entry)))
    result.append(('source', _json({'work': snapshot['work'], 'coverage': snapshot['coverage']})))
    return [(kind, layout.normalize(value)) for kind, value in result]


def _scope_blocks(result, document, text):
    result += [('body', document['title']), ('heading', text['requirements'])]
    for requirement in document['requirements']:
        result += [('subheading', text['requirement'] + ': ' + requirement['id']), ('body', _json(requirement))]
    result += [('subheading', text['inputs']), ('source', _json(document['selected_inputs'])),
               ('source', _json(document['sections']))]


def render_fixed(fixed, *, format='markdown', locale='en', renderer_version=1):
    """Pure renderer; only call with the fully authorized immutable projection."""
    if renderer_version != 1:
        raise OperationError('PM_EXPORT_TEMPLATE_UNAVAILABLE', 'The report renderer is unsupported', 409)
    content_blocks = blocks(fixed, locale)
    pages = None
    if format == 'svg':
        content, pages = layout.svg(content_blocks, locale, fixed['observation_revision_id'], _TEXT[locale]['page'])
    else:
        content = brief._markdown(content_blocks) if format == 'markdown' else brief._html(content_blocks, locale)
    raw = content.encode('utf-8')
    identity = ('workspace_id', 'observation_revision_id', 'snapshot_id', 'snapshot_digest', 'snapshot_view_digest',
        'baseline_revision_id', 'forecast_revision_id', 'baseline_digest', 'forecast_digest', 'observation_digest', 'source_set_digest', 'view_digest', 'template_version', 'coverage')
    result = {'version': 1, **{key: fixed[key] for key in identity}, 'renderer_version': renderer_version,
        'format': format, 'locale': locale, 'media_type': FORMATS[format], 'content': content,
        'byte_length': len(raw), 'output_digest': hashlib.sha256(raw).hexdigest(), 'page_count': pages,
        'execution_authorized': False, 'review_completed': False, 'published': False}
    if len(raw) > MAX_CONTENT_BYTES or len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > MAX_RESPONSE_BYTES:
        raise OperationError('PM_EXPORT_TOO_LARGE', 'The complete report exceeds its UTF-8 output bound', 413)
    return result


def get_report(ops, principal, workspace_id, observation_revision_id, *, format='markdown', locale='en', renderer_version=1):
    if (not isinstance(format, str) or format not in FORMATS or not isinstance(locale, str) or locale not in _TEXT
            or type(renderer_version) is not int or not 1 <= renderer_version <= 999_999_999):
        raise OperationError('INVALID_PARAMS', 'Select markdown, html or svg; en or zh-TW; and an integer renderer version', 422)
    fixed = sources.load_fixed(ops, principal, workspace_id, observation_revision_id)
    return render_fixed(fixed, format=format, locale=locale, renderer_version=renderer_version)
