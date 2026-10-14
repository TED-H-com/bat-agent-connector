import {reviewFixture} from './requirements-review-fixture.ts';

export const recordedStatuses = [
  {status: 'adopted', group: 'done', en: 'Adopted', zh: '已採用'},
  {status: 'rejected', group: 'closed', en: 'Rejected', zh: '已拒絕'},
  {status: 'reported_done', group: 'done', en: 'Reported done', zh: '回報完成'},
  {status: 'accepted', group: 'in_progress', en: 'Accepted for processing', zh: '已受理'},
  {status: 'reported_resolved', group: 'done', en: 'Reported resolved', zh: '回報已解決'},
  {status: 'observed_open', group: 'open', en: 'Observed', zh: '已觀察'},
  {status: 'format_valid', group: 'in_review', en: 'Format checked', zh: '格式已核對'},
];

export function registerStatusFixture(locale = 'en') {
  const f = reviewFixture(locale);
  const records = recordedStatuses.map((item, index) => ({
    row_id: `plan_revision.exact_${item.status}`, type: 'decision',
    title: `${locale === 'en' ? 'Plan revision' : '計畫修訂'} ${index + 1}`,
    source: {kind: 'plan_revision', id: `exact_${item.status}`}, status: item.status,
    owner: null, related: {}, available_actions: [],
  }));
  f.overrides.push((url, result) => {
    if (url.pathname.endsWith('/register')) {
      result.data = {...result.data, rows: records, next_cursor: null, summary: null,
        total_by_type: {decision: records.length}, coverage: {complete: true}};
    } else if (url.pathname.includes('/register/plan_revision.exact_')) {
      const row = records.find(item => url.pathname.endsWith(item.row_id));
      result = {status: 200, data: {workspace_id: f.ids.workspaceId, row,
        timeline: [{step: 'decision', status: row!.status}], timeline_coverage: {complete: true}}};
    }
    return result;
  });
  return f;
}
