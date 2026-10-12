"""Literal maintenance sections from the same authorized DocumentSnapshot."""
from __future__ import annotations

import datetime as dt
import json

_TEXT = {
    'en': {
        'title': 'Maintenance Review', 'period': 'Reporting period (start inclusive, end exclusive)',
        'boundary': 'Saved observations prepare discussion. This fixed report contains no later meeting decisions or adopted improvements.',
        'sampling': 'Observations are point samples. No observed change does not prove an event-free week; a missing item is not a verified resolution.',
        'release': 'Original release baseline', 'policy': 'Original maintenance policy',
        'coverage': 'Source coverage', 'github': 'GitHub PRs and Actions', 'workload': 'Central workload',
        'checks': 'Maintenance checks', 'issues': 'Issue contents', 'dependencies': 'Dependency contents',
        'repairs': 'Maintenance repair outcomes', 'repair_capacity': 'Original execution capacity remains reserved',
        'repair_released': 'Original execution capacity released', 'repair_unknown': 'Delivery outcome not yet proven',
        'published_for_review': 'Published for review', 'merged': 'Merged', 'deployed': 'Deployed',
        'repair_limit': 'These are saved original receipts. Publication, native execution completion and a verified release are separate facts.',
        'research': 'Research', 'quality': 'Quality assessment', 'model_analysis': 'Model analysis',
        'changes': 'Compared observations', 'added': 'Newly observed', 'changed': 'Changed observations',
        'no_longer_observed': 'No longer observed · resolution unassessed', 'none': 'None in this fixed selection',
        'scope_changed': 'The original policy scopes differ. This comparison cannot establish resolution across them.',
        'blockers': 'Saved workload blockers', 'blocker_limit': 'These reasons describe the selected saved workload point; they are not a fresh or complete diagnosis.',
        'effort': 'Recorded checks and effort', 'completed_checks': 'Finished checks', 'pending_checks': 'Pending checks',
        'effort_limit': 'Check counts do not measure engineering work. Engineering effort and provider charges are unknown.',
        'focus': 'Next observation focus', 'review_source_coverage': 'Review the source coverage gaps.',
        'verify_reported_results': 'Verify reported results against their original evidence.',
        'schedule': 'The next actual execution time is not determined by this historical report.',
        'sources': 'Fixed maintenance facts and source references',
        'observed': 'Observed at saved sample times', 'incomplete': 'Incomplete', 'unchecked': 'Not checked',
        'stale': 'Saved observation has expired', 'disabled': 'Monitoring disabled', 'not_configured': 'Not configured',
        'not_observed': 'Not observed', 'not_performed': 'Not performed', 'unassessed': 'Unassessed',
        'not_requested': 'Not requested', 'comparison_unavailable': 'Comparison unavailable',
        'observed_changes': 'Changes in selected observations', 'observed_changes_partial': 'Changes in a partial comparison',
        'no_observed_changes': 'No change between selected observations', 'data_gaps': 'Source coverage gaps', 'unknown': 'Unknown',
        'continuity_gap': 'Earlier comparison context exceeded the report limit. It remains in its original history; this report cannot compare it.',
        'missed_weeks': 'Coalesced missed weeks', 'missed_help': 'Only the latest due period is covered. Earlier missed weeks were not checked.',
    },
    'zh-TW': {
        'title': '維護 Review', 'period': '報告期間（包含起點、不包含終點）',
        'boundary': '這份固定報告以已保存觀察準備討論，不包含後來的會議決策或已採用改善。',
        'sampling': '觀察只涵蓋各個取樣時間。沒有觀察到變化不代表整週無事件；項目不再出現也不代表已查證解決。',
        'release': '原發布基準', 'policy': '原維護政策',
        'coverage': '來源涵蓋', 'github': 'GitHub PR 與 Actions', 'workload': '中央工作量',
        'checks': '維護檢查', 'issues': 'Issue 內容', 'dependencies': '依賴內容',
        'repairs': '維護修復成果', 'repair_capacity': '原執行工作仍占用容量',
        'repair_released': '原執行容量已釋放', 'repair_unknown': '交付結果尚未證實',
        'published_for_review': '已發布供審查', 'merged': '已合併', 'deployed': '已部署',
        'repair_limit': '這些是已保存的原始回執。發布、原生執行完成與已查證發布版本分別記錄。',
        'research': '研究', 'quality': '品質判斷', 'model_analysis': '模型分析',
        'changes': '觀察比較', 'added': '新觀察到', 'changed': '觀察有變化',
        'no_longer_observed': '未再觀察到 · 尚未判定已解決', 'none': '本次固定選取沒有項目',
        'scope_changed': '前後原政策範圍不同，不能據此認定問題已解決。',
        'blockers': '已保存的工作量阻止原因', 'blocker_limit': '這些原因只描述所選的歷史工作量觀察，並非新的或完整的診斷。',
        'effort': '已記錄檢查與投入', 'completed_checks': '已結束檢查', 'pending_checks': '尚未結束檢查',
        'effort_limit': '檢查次數不代表工程工作量；工程投入與 provider 費用仍未知。',
        'focus': '下一次觀察重點', 'review_source_coverage': '檢視來源涵蓋缺口。',
        'verify_reported_results': '按原始證據核對已回報成果。',
        'schedule': '這份歷史報告不決定下一次實際執行時間。',
        'sources': '固定維護事實與來源引用',
        'observed': '已在保存的取樣時間觀察', 'incomplete': '不完整', 'unchecked': '尚未檢查',
        'stale': '已保存觀察已過期', 'disabled': '監測已停用', 'not_configured': '尚未配置',
        'not_observed': '尚未觀察', 'not_performed': '尚未進行', 'unassessed': '尚未評估',
        'not_requested': '尚未要求', 'comparison_unavailable': '無法比較',
        'observed_changes': '所選觀察有變化', 'observed_changes_partial': '部分比較有變化',
        'no_observed_changes': '所選觀察之間沒有變化', 'data_gaps': '來源涵蓋有缺口', 'unknown': '未知',
        'continuity_gap': '先前比較脈絡超過報告上限，仍保留在原歷史紀錄；這份報告無法進行該比較。',
        'missed_weeks': '已合併的漏跑週數', 'missed_help': '只涵蓋最近到期期間，更早漏跑週期未經檢查。',
    },
}


def blocks(extension, locale):
    """No reads or inference; the caller applies escaping and output byte/page bounds."""
    text, report = _TEXT[locale], extension['report']
    def label(value):
        return text.get(value, str(value))

    def stamp(value):
        try:
            return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat(timespec='seconds')
        except (OverflowError, OSError, ValueError):
            return text['unknown']

    release, policy = report['release'], report['policy']
    result = [('heading', text['title']), ('note', text['boundary']),
        ('body', text['period'] + ': ' + stamp(report['period']['start']) + ' — ' + stamp(report['period']['end'])),
        ('body', label(report['report_status'])), ('note', text['sampling']),
        ('body', text['release'] + ': ' + release['repository'] + ' · ' + release['channel'] + ' · ' + release['release_version']['value']),
        ('note', release['baseline_id'] + ' · ' + release['commit_sha']),
        ('body', text['policy'] + ': ' + str(policy['version']) + ' · ' + policy['policy_id']),
        ('subheading', text['coverage'])]
    if report.get('previous_context', {}).get('status') == 'omitted_source_limit':
        result.append(('warning', text['continuity_gap']))
    if report.get('schedule'):
        result.extend([('body', text['missed_weeks'] + ': ' + str(report['schedule']['coalesced_windows'])), ('note', text['missed_help'])])
    for kind in ('github', 'workload', 'checks', 'issues', 'dependencies', 'research', 'quality', 'model_analysis'):
        coverage = report['coverage'][kind]
        state = coverage['status'] if isinstance(coverage, dict) else coverage
        result.append(('body', text[kind] + ': ' + label(state)))
    changes = report['changes']
    result.extend([('subheading', text['changes']), ('body', label(changes['status']))])
    if changes['scope_changed']:
        result.append(('warning', text['scope_changed']))
    for kind in ('added', 'changed', 'no_longer_observed'):
        result.append(('body', text[kind] + ': ' + (' · '.join(changes[kind]) or text['none'])))
    result.extend([('subheading', text['blockers']), ('note', text['blocker_limit'])])
    if not report['current_blockers']:
        result.append(('body', text['none']))
    else:
        result.append(('source', json.dumps(report['current_blockers'], ensure_ascii=False, sort_keys=True, indent=2)))
    result.append(('subheading', text['effort']))
    for key in ('completed_checks', 'pending_checks'):
        result.append(('body', text[key] + ': ' + str(report['effort'][key])))
    result.append(('note', text['effort_limit']))
    if 'repairs' in report:
        result.extend([('subheading', text['repairs']), ('note', text['repair_limit'])])
        if not report['repairs']:
            result.append(('body', text['none']))
        for repair in report['repairs']:
            result.extend([('body', repair['repair_id'] + ': ' + label(repair['outcome'] or 'repair_unknown')),
                ('body', text['repair_capacity' if repair['capacity_held'] else 'repair_released']),
                ('source', json.dumps(repair, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2))])
    result.append(('subheading', text['focus']))
    result.extend(('body', label(value)) for value in report['next_observation_focus'])
    result.extend([('note', text['schedule']), ('subheading', text['sources']),
        ('source', json.dumps(extension, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2))])
    return result
