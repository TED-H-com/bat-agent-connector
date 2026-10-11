"""One fixed historical projection for management-view previews, SVG and print."""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json

from . import pm_brief_exports as brief
from . import pm_snapshot_views, pm_snapshots
from .operations import OperationError

TEXT = {
    'en': {
        'roadmap': 'Scope and milestones', 'kanban': 'Workflow Kanban', 'sprint': 'Sprint and discussions',
        'governance': 'Risks, changes and decisions', 'historical': 'Historical snapshot · fixed original sources',
        'unknown': 'Unknown / not assessed', 'empty': 'No saved records in this selected view; this does not establish completion.',
        'partial': 'Partial or restricted source coverage. Counts apply only to the visible saved selection.',
        'boundary': 'Read-only discussion output. Content approval, verification and acceptance are separate. No work or publication is authorized.',
        'workspace': 'Workspace', 'as_of': 'Assembled at', 'scope': 'Original scope', 'quality': 'Accepted requirements / original scope',
        'milestones': 'Milestone register', 'baseline': 'Original baseline', 'scheduled': 'Selected baseline date',
        'forecast': 'Forecast date', 'actual': 'Actual finish', 'depends': 'Depends on', 'requirements': 'Requirements',
        'evidence': 'Observed evidence', 'rationale': 'Reason', 'sources': 'Original references', 'coverage': 'Source coverage',
        'backlog': 'Backlog', 'in_progress': 'In progress', 'waiting': 'Waiting', 'review': 'Review / verify',
        'accepted': 'Content approved · DoD separate', 'archived': 'Archived', 'state': 'Saved state', 'owner': 'Responsible actor',
        'age': 'Work age in seconds at capture', 'attempts': 'Observed accepted sends', 'assessment': 'Quality assessment',
        'execution': 'Original execution observations', 'goal': 'Sprint Goal', 'period': 'Fixed period', 'capacity': 'Capacity assumption',
        'dod': 'Original Definition of Done', 'selection': 'Selected work and Developer How', 'results': 'Original closure results',
        'meetings': 'Discussions', 'output': 'Prepared output', 'discussion': 'Recorded discussion', 'followups': 'Follow-up results',
        'risk': 'Risk', 'assumption': 'Assumption', 'issue': 'Issue', 'dependency': 'Dependency', 'drift': 'Reported deviation',
        'change': 'Change proposal', 'description': 'Finding', 'trigger': 'Trigger', 'response': 'Response', 'review_at': 'Review time',
        'decision': 'Direction decision', 'adoption': 'Plan adoption', 'alternatives': 'Considered alternatives', 'page': 'Page',
    },
    'zh-TW': {
        'roadmap': '範圍與里程碑', 'kanban': '工作流看板', 'sprint': 'Sprint 與討論', 'governance': '風險變更與決策',
        'historical': '歷史快照 · 固定原始來源', 'unknown': '未知／尚未評估', 'empty': '此選定視圖沒有已保存紀錄；不代表已完成。',
        'partial': '來源涵蓋不完整或部分已無權讀取；數值只涵蓋可讀的已保存選取。',
        'boundary': '唯讀討論輸出。內容批准、驗證與接受條件分開；不授權工作或發布。',
        'workspace': '工作區', 'as_of': '彙整時間', 'scope': '原始範圍', 'quality': '已接受需求／原始範圍',
        'milestones': '里程碑清單', 'baseline': '原始基準', 'scheduled': '選定基準日期', 'forecast': '預測日期',
        'actual': '實際完成日期', 'depends': '相依里程碑', 'requirements': '需求', 'evidence': '已觀察證據',
        'rationale': '理由', 'sources': '原始來源', 'coverage': '來源涵蓋', 'backlog': '待安排', 'in_progress': '進行中',
        'waiting': '等待中', 'review': 'Review／驗證', 'accepted': '內容已批准 · DoD 另行評估', 'archived': '已封存',
        'state': '已保存狀態', 'owner': '責任人', 'age': '快照當時工作經過秒數', 'attempts': '已觀察的接受指令次數',
        'assessment': '品質判定', 'execution': '原始執行觀察', 'goal': 'Sprint Goal', 'period': '固定期間',
        'capacity': '容量假設', 'dod': '原始完成定義', 'selection': '選入工作與實作者安排', 'results': '原始結束結果',
        'meetings': '討論紀錄', 'output': '準備內容', 'discussion': '已記錄討論', 'followups': '後續追蹤結果',
        'risk': '風險', 'assumption': '假設', 'issue': '問題', 'dependency': '相依性', 'drift': '已報告偏差',
        'change': '變更提案', 'description': '事項', 'trigger': '觸發條件', 'response': '因應措施', 'review_at': '檢視時間',
        'decision': '方向決定', 'adoption': '計畫採用', 'alternatives': '已考慮替代方案', 'page': '頁',
    },
}


def _bad(message, code='INVALID_PARAMS', status=422):
    return OperationError(code, message, status)


def _value(value, text):
    if value is None or value == [] or value == {}:
        return text['unknown']
    if isinstance(value, str):
        return brief._literal(value)
    return brief._literal(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))


def _item(ident, title, values, text):
    return {'id': ident, 'title': brief._literal(title),
            'fields': [{'key': key, 'label': text[key], 'value': _value(value, text)} for key, value in values]}


def _roadmap(records, text):
    selected = {row['kind']: row['data'] for row in records}
    base = selected.get('baseline', {}).get('document', {})
    forecast = {item['milestone_id']: item for item in selected.get('forecast', {}).get('document', {}).get('milestones', [])}
    observed = {item['milestone_id']: item for item in selected.get('observation', {}).get('document', {}).get('milestones', [])}
    rows = []
    for item in base.get('milestones', []):
        ident = item['milestone_id']
        actual = observed.get(ident, {}).get('actual', {})
        rows.append(_item(ident, item['title'], [
            ('baseline', item['original_baseline_date']), ('scheduled', item['baseline_date']),
            ('forecast', forecast.get(ident, {}).get('forecast_date')), ('actual', actual.get('actual_finish_date')),
            ('depends', item['depends_on']), ('requirements', item['requirement_ids']),
            ('scope', item['scope_revision']), ('evidence', actual.get('facts')),
            ('rationale', base.get('rationale')), ('coverage', observed.get(ident, {}).get('coverage'))], text))
    return [{'key': 'milestones', 'title': text['milestones'], 'items': rows}]


def _kanban(records, text):
    groups = []
    for column in ('backlog', 'in_progress', 'waiting', 'review', 'accepted', 'archived'):
        rows = []
        for entry in records:
            card = entry['data']
            if card['column'] != column:
                continue
            rows.append(_item(entry['id'], card['work']['title'], [
                ('state', card['work']['state']), ('owner', card['assignment']['actor']),
                ('age', card['flow']['age_seconds']), ('attempts', card['execution']['accepted_attempts']),
                ('requirements', card['binding']['requirement_ids']), ('assessment', card['quality']),
                ('execution', card['execution']), ('coverage', {
                    'history': card['flow']['history'], 'capture_matches_binding': card['capture_matches_binding'],
                    'capture_matches_work_version': card['capture_matches_work_version']})], text))
        groups.append({'key': column, 'title': text[column], 'items': rows})
    return groups


def _sprint(records, text):
    rows, meetings = [], []
    for entry in records:
        value = entry['data']
        if entry['kind'] == 'sprint':
            definition = value['sprint']['definition']
            rows.append(_item(entry['id'], definition['title'], [
                ('state', value['sprint']['status']), ('goal', value['goal']['text']),
                ('period', definition['starts_at'] + ' — ' + definition['ends_at']), ('owner', definition['owner_actor']),
                ('capacity', definition['capacity_note']), ('dod', value['artifacts']['dod']['document']['criteria']),
                ('selection', value['selected_items']), ('results', value['result']), ('coverage', value['coverage'])], text))
        else:
            meetings.append(_item(entry['id'], value['meeting']['title'], [
                ('state', value['meeting']['status']), ('goal', value['context']['document']['sprint_goal']['text']),
                ('output', value['draft']['document'] if value['draft'] else None),
                ('discussion', {'completion': value['completion'], 'notes': value['notes']}),
                ('followups', value['follow_up_events']), ('coverage', value['coverage'])], text))
    return [{'key': 'sprint', 'title': text['sprint'], 'items': rows},
            {'key': 'meetings', 'title': text['meetings'], 'items': meetings}]


def _governance(records, text):
    groups = []
    for kind in ('risk', 'assumption', 'issue', 'dependency', 'drift', 'change'):
        rows = []
        for entry in records:
            value = entry['data']
            if value['record']['kind'] != kind:
                continue
            doc = value['revision']['document']
            rows.append(_item(entry['id'], doc['title'], [
                ('state', doc['status']), ('description', doc['description']), ('owner', doc['owner_actor']),
                ('requirements', doc['affected_requirement_ids']), ('trigger', doc['trigger']), ('response', doc['response']),
                ('review_at', doc['review_at']), ('alternatives', doc['alternatives']), ('rationale', doc['rationale']),
                ('decision', doc['decision']), ('adoption', doc['adoption']),
                ('assessment', {'basis': doc['assessment_basis'], 'verification': doc['verification_status'], 'scope_applied': doc['scope_applied']})], text))
        groups.append({'key': kind, 'title': text[kind], 'items': rows})
    return groups


def project(snapshot, view, locale):
    if view not in pm_snapshot_views.VIEWS or locale not in TEXT:
        raise _bad('Select roadmap, kanban, sprint or governance and locale en or zh-TW')
    extension = snapshot.get('management_views')
    if extension is None:
        raise _bad('This historical snapshot did not capture management views', 'PM_SNAPSHOT_VIEW_UNAVAILABLE', 409)
    if snapshot['template_version'] != 1 or extension['version'] != 1:
        raise _bad('This fixed view has no compatible renderer', 'PM_EXPORT_TEMPLATE_UNAVAILABLE', 409)
    text, state = TEXT[locale], extension['views'][view]
    groups = {'roadmap': _roadmap, 'kanban': _kanban, 'sprint': _sprint, 'governance': _governance}[view](state['records'], text)
    sources = [{'kind': row['kind'], 'id': row['id'], 'access_refs': row['access_refs']} for row in state['records']]
    quality = snapshot['brief']['delivery'].get('quality')
    value = {'version': 1, 'workspace_id': snapshot['workspace_id'], 'snapshot_id': snapshot['snapshot_id'],
        'snapshot_digest': snapshot['snapshot_digest'], 'view_digest': snapshot['view_digest'],
        'template_version': 1, 'view_template_version': 1, 'view': view, 'locale': locale,
        'title': text[view], 'snapshot_title': brief._literal(snapshot['title']), 'workspace_name': brief._literal(snapshot['workspace_name']),
        'as_of': snapshot['as_of'], 'journal_cursor': snapshot['journal_cursor'], 'scope_revision': snapshot['baseline']['scope_revision'],
        'quality': quality, 'groups': groups, 'sources': sources,
        'counts': {'visible_records': len(state['records']), 'scope': 'visible_saved_selection',
                   **({'wip': pm_snapshot_views.pm_kanban._wip([r['data'] for r in state['records']])} if view == 'kanban' else {})},
        'coverage': {'snapshot': snapshot['coverage'], 'visibility': snapshot['visibility'], 'view': state['coverage'],
                     'content_truncated': False, 'projection': view, 'other_management_views': 'not_rendered',
                     'referenced_sources': 'fixed_authorized_view', 'unreferenced_evidence': 'not_expanded'},
        'execution_authorized': False, 'review_completed': False, 'published': False}
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) > brief.MAX_RESPONSE_BYTES:
        raise _bad('The complete fixed view exceeds the UTF-8 bound', 'PM_EXPORT_TOO_LARGE', 413)
    return value


def get_view(ops, principal, workspace_id, snapshot_id, *, view='roadmap', locale='en'):
    if not isinstance(view, str) or not isinstance(locale, str):
        raise _bad('Use an exact registered view and language')
    return project(pm_snapshots.get_snapshot(ops, principal, workspace_id, snapshot_id)['snapshot'], view, locale)


def _blocks(value):
    text = TEXT[value['locale']]
    timestamp = dt.datetime.fromtimestamp(value['as_of'], dt.timezone.utc).isoformat()
    blocks = [('title', value['title'] + ' · ' + value['snapshot_title']), ('note', text['historical']),
              ('body', text['workspace'] + ': ' + value['workspace_name']), ('body', text['as_of'] + ': ' + timestamp),
              ('body', text['scope'] + ': ' + _value(value['scope_revision'], text)), ('note', text['boundary'])]
    if any(value['coverage']['view'].get(key) for key in ('truncated', 'byte_limited', 'visibility_filtered')) or value['coverage']['visibility']['filtered']:
        blocks.append(('warning', text['partial']))
    if value['quality']:
        q = value['quality']
        blocks.append(('body', f"{text['quality']}: {q['qualified_requirements']} / {q['total_requirements']}"))
    for group in value['groups']:
        blocks.append(('heading', group['title']))
        if not group['items']:
            blocks.append(('note', text['empty']))
        for item in group['items']:
            blocks.append(('subheading', item['title']))
            blocks.append(('source', item['id']))
            for field in item['fields']:
                blocks.append(('body', field['label'] + ': ' + field['value']))
    blocks.extend([('heading', text['sources']), ('source', _value({key: value[key] for key in
        ('snapshot_id', 'snapshot_digest', 'view_digest', 'journal_cursor', 'scope_revision', 'sources')}, text)),
        ('heading', text['coverage']), ('source', _value(value['coverage'], text))])
    return blocks


def _svg(blocks, value):
    # Kanban uses two compact reading columns; long columns continue onto the
    # next panel. Other registers keep full-width rows for dates and rationale.
    columns = 2 if value['view'] == 'kanban' else 1
    width = (brief.PAGE_WIDTH - 80 - (columns - 1) * 24) // columns
    pages, page, column, y = [], [], 0, 76
    for kind, content in blocks:
        size = 20 if kind == 'title' else 16 if kind in {'heading', 'subheading'} else 14 if kind in {'source', 'note'} else 15
        line_height = size + 8
        y += 12 if kind in {'title', 'heading', 'subheading'} else 4
        for line in brief._wrap(content, width // size):
            if y + line_height > brief.PAGE_HEIGHT - 52:
                column += 1
                if column == columns:
                    pages.append(page)
                    if len(pages) >= brief.MAX_SVG_PAGES:
                        raise _bad('The complete SVG exceeds its panel bound', 'PM_EXPORT_TOO_LARGE', 413)
                    page, column = [], 0
                y = 76
            page.append((40 + column * (width + 24), y, kind, size, line))
            y += line_height
    pages.append(page)
    height = len(pages) * (brief.PAGE_HEIGHT + brief.PAGE_GAP) - brief.PAGE_GAP
    def esc(text):
        return html.escape(text, quote=True)
    nodes = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{brief.PAGE_WIDTH}" height="{height}" viewBox="0 0 {brief.PAGE_WIDTH} {height}" role="img" xml:lang="{value["locale"]}">',
             f'<title>{esc(value["title"])}</title>', f'<desc>{esc(TEXT[value["locale"]]["historical"])}</desc>']
    for index, lines in enumerate(pages):
        nodes.append(f'<g id="page-{index + 1}" transform="translate(0 {index * (brief.PAGE_HEIGHT + brief.PAGE_GAP)})">')
        nodes.append(f'<rect width="{brief.PAGE_WIDTH}" height="{brief.PAGE_HEIGHT}" fill="#fff" stroke="#dfe3ea"/>')
        nodes.append('<g font-family="system-ui, Segoe UI, Noto Sans TC, sans-serif" fill="#1d2330">')
        nodes.append(f'<text x="40" y="36" font-size="14" fill="#5d6676">{esc(value["title"] + " · " + value["snapshot_id"])}</text>')
        for x, offset, kind, size, line in lines:
            color = '#5d6676' if kind in {'note', 'source'} else '#1d2330'
            weight = 600 if kind in {'title', 'heading', 'subheading', 'warning'} else 400
            nodes.append(f'<text x="{x}" y="{offset}" font-size="{size}" fill="{color}" font-weight="{weight}" xml:space="preserve">{esc(line)}</text>')
        nodes.append(f'<text x="40" y="{brief.PAGE_HEIGHT - 24}" font-size="14" fill="#5d6676">{TEXT[value["locale"]]["page"]} {index + 1} / {len(pages)}</text>')
        nodes.append('</g></g>')
    return '\n'.join([*nodes, '</svg>']) + '\n', len(pages)


def render_view(ops, principal, workspace_id, snapshot_id, *, view='roadmap', format='svg', locale='en'):
    if not isinstance(format, str) or format not in ('svg', 'html'):
        raise _bad('Select SVG or printable HTML')
    value = get_view(ops, principal, workspace_id, snapshot_id, view=view, locale=locale)
    blocks = _blocks(value)
    content, pages = _svg(blocks, value) if format == 'svg' else (brief._html(blocks, locale), None)
    raw = content.encode('utf-8')
    result = {key: value[key] for key in ('version', 'workspace_id', 'snapshot_id', 'snapshot_digest', 'view_digest',
        'template_version', 'view_template_version', 'view', 'locale', 'coverage', 'execution_authorized', 'review_completed', 'published')}
    result.update(renderer_version=1, format=format, media_type=brief.FORMATS[format], content=content,
        byte_length=len(raw), output_digest=hashlib.sha256(raw).hexdigest(), page_count=pages)
    if len(raw) > brief.MAX_CONTENT_BYTES or len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')) > brief.MAX_RESPONSE_BYTES:
        raise _bad('The complete fixed view export exceeds its UTF-8 bound', 'PM_EXPORT_TOO_LARGE', 413)
    return result
