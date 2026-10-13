// Schedule dates are UTC days. Never substitute observation times for execution evidence.
export const DAY = 86400000;
export const ZOOM = {day: 48, week: 24, month: 8};
export function dayNumber(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}(?:T|$)/.test(value)) return null;
  const n = Date.parse(value.slice(0, 10) + 'T00:00:00Z');
  return Number.isFinite(n) ? n / DAY : null;
}
export const firstLine = row => String(row.title || '').split(/\r?\n/)[0];
export const sessionAttempt = row => row.kind === 'attempt' && row.id.startsWith('attempt:session:');
// The backend's session_not_observed note is page-wide, not attached to an id.
// With complete data a single candidate is identifiable; otherwise keep Unknown.
export function notObserved(row, data) {
  if (!sessionAttempt(row) || row.status !== 'unknown') return false;
  if ((row.notes || []).some(note => note.reason === 'session_not_observed')) return true;
  if (data.partial || !(data.unknown || []).some(note => note.reason === 'session_not_observed')) return false;
  const candidates = data.rows.filter(value => sessionAttempt(value) && value.status === 'unknown'
    && value.title === value.refs?.session?.session_id && !value.refs?.recorded_state && !value.refs?.agent && !value.refs?.run_id);
  return candidates.length === 1 && candidates[0].id === row.id;
}
export function spans(row) {
  if (sessionAttempt(row)) return [];
  return ['baseline', 'forecast', 'actual'].flatMap(kind => {
    const span = row[kind]; if (!span) return [];
    const start = dayNumber(span.start), end = dayNumber(span.end);
    if (start === null && end === null) return [];
    const cleanEnd = start !== null && end !== null && end < start ? null : end;
    return [{kind, start, end: cleanEnd, open: kind === 'actual' && span.open === true && cleanEnd === null}];
  });
}
export function chartRange(rows) {
  const days = rows.flatMap(row => spans(row).flatMap(span => [span.start, span.end])).filter(n => n !== null);
  return days.length ? {start: Math.min(...days), end: Math.max(...days)} : null;
}
export function milestoneDelta(row) {
  const baseline = dayNumber(row.baseline?.start), forecast = dayNumber(row.forecast?.start);
  return baseline === null || forecast === null ? null : forecast - baseline;
}
export function scheduleRef(row, data) {
  const refs = row.refs || {};
  if (row.kind === 'milestone') {
    const hasBaseline = row.baseline ? Boolean(row.baseline.start || row.baseline.end) : row.baseline !== null && !row.forecast;
    if (!refs.milestone_id || !data?.baseline?.revision_id || !hasBaseline || (refs.revision_id && refs.revision_id !== data.baseline.revision_id)) return null;
    return {kind: 'milestone', id: refs.milestone_id, revision_id: data.baseline.revision_id};
  }
  if (row.kind === 'requirement') return {kind: 'requirement', id: refs.requirement_id, revision_id: refs.plan_revision_id};
  if (row.kind === 'issue') return {kind: 'issue_mapping', id: row.id.slice('issue:'.length)};
  const workId = refs.work_item_id || (row.kind === 'attempt' && row.parent_id?.startsWith('work:') ? row.parent_id.slice(5) : null);
  return workId ? {kind: 'work_item', id: workId} : null;
}
// All pages are read sequentially, including short byte/lineage-limited pages.
export async function readSchedule(api, workspaceId, check) {
  const all = new Map(), notes = new Map(), reasons = new Set();
  let after, frame, partial = false;
  for (let page = 0; page < 10; page++) {
    const query = new URLSearchParams({limit: '100'}); if (after) query.set('after', after);
    const next = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/schedule?${query}`);
    check();
    if (next?.workspace_id !== workspaceId || !Array.isArray(next.rows) || !Array.isArray(next.unscheduled)) throw Error('PM_SCHEDULE_INVALID');
    // A revision switch between pages cannot form one coherent chart.
    if (frame && JSON.stringify([frame.baseline, frame.forecast]) !== JSON.stringify([next.baseline, next.forecast])) throw Error('PM_SCHEDULE_CHANGED');
    frame = next;
    for (const row of [...next.rows, ...next.unscheduled]) all.set(row.id, row);
    for (const note of next.unknown || []) notes.set(JSON.stringify(note), note);
    if (next.partial || next.truncated) {partial = true; reasons.add('schedule_page_partial');}
    for (const key of ['attempts_truncated', 'attempts_unavailable', 'work_bindings_truncated', 'issues_truncated']) if (next.coverage?.[key]) reasons.add(key);
    if (next.next_cursor === null) break;
    if (typeof next.next_cursor !== 'string' || !next.next_cursor || after === next.next_cursor) throw Error('PM_SCHEDULE_INVALID');
    after = next.next_cursor;
    if (page === 9) {partial = true; reasons.add('schedule_page_limit');}
  }
  const rows = [...all.values()], {included} = treeIds(rows);
  return {...frame, rows, unscheduled: rows.filter(row => !spans(row).length && !included.has(row.id)), range: chartRange(rows),
    unknown: [...notes.values()], partial, reasons: [...reasons]};
}
function treeIds(rows) {
  const byId = new Map(rows.map(row => [row.id, row])), included = new Set();
  for (const row of rows.filter(row => spans(row).length)) {
    let next = row; const path = new Set();
    while (next && !path.has(next.id)) {included.add(next.id); path.add(next.id); next = byId.get(next.parent_id);}
  }
  return {byId, included};
}
// Keep ancestors even when they have no dates. Session attempts never become bars.
export function scheduleTree(rows, collapsed) {
  const {byId, included} = treeIds(rows);
  const children = new Map();
  for (const row of rows.filter(row => included.has(row.id) && !sessionAttempt(row))) {
    const parent = included.has(row.parent_id) ? row.parent_id : row.kind === 'milestone' ? null : 'unassigned';
    if (!children.has(parent)) children.set(parent, []); children.get(parent).push(row);
  }
  const visible = [], visited = new Set();
  function add(row, depth, parent) {
    if (visited.has(row.id)) return; visited.add(row.id);
    const nested = children.get(row.id) || [];
    visible.push({row, depth, parent, expandable: nested.length > 0});
    if (!collapsed.has(row.id)) for (const child of nested) add(child, depth + 1, row.id);
  }
  for (const row of children.get(null) || []) add(row, 1, null);
  if (children.has('unassigned')) add({id: 'unassigned', kind: 'group', title: '', status: 'unknown'}, 1, null);
  // A partial page may contain a parent cycle or a missing ancestor. Keep its loaded dates reachable.
  for (const row of rows.filter(row => included.has(row.id))) if (!visited.has(row.id) && !byId.has(row.parent_id)) add(row, 1, null);
  return visible;
}
