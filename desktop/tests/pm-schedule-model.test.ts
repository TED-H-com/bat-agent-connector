import assert from 'node:assert/strict';
import {test} from 'node:test';
import {chartRange, dayNumber, readSchedule, scheduleRef, scheduleTree, spans} from '../src/pm-schedule-model.js';
import {reasonLabel} from '../src/ui/ui-strings.js';
const frame = (rows: any[], extra = {}) => ({workspace_id: 'w', rows, unscheduled: [], baseline: null, forecast: null,
  range: null, unknown: [], partial: false, truncated: false, next_cursor: null, ...extra});
test('opaque short-page cursors are passed unchanged and range includes every loaded page', async () => {
  const calls: string[] = [];
  const data = await readSchedule(async (_, path) => {calls.push(path); return calls.length === 1
    ? frame([{id: 'a', actual: {start: '2026-10-02T23:00:00Z', end: null}}], {next_cursor: 'opaque_-cursor', partial: true})
    : frame([{id: 'b', baseline: {start: '2026-11-20', end: '2026-11-20'}}]);}, 'w', () => {});
  assert.equal(new URL(calls[1], 'http://fixture').searchParams.get('after'), 'opaque_-cursor');
  assert.deepEqual(data.range, {start: dayNumber('2026-10-02'), end: dayNumber('2026-11-20')});
  assert.equal(data.partial, true); assert.equal(data.rows.length, 2);
});
test('ten page bound remains partial with a reason and no fabricated total', async () => {
  let count = 0;
  const data = await readSchedule(async () => frame([], {next_cursor: 'page_' + ++count}), 'w', () => {});
  assert.equal(count, 10); assert.equal(data.partial, true); assert.ok(data.reasons.includes('schedule_page_limit')); assert.equal('total' in data, false);
});
test('page source truncation remains partial without a cursor', async () => {
  const data = await readSchedule(async () => frame([], {truncated: true, coverage: {attempts_unavailable: true}}), 'w', () => {});
  assert.equal(data.partial, true); assert.ok(data.reasons.includes('attempts_unavailable'));
});
test('scope mismatch, revision changes and retirement cannot produce a chart', async () => {
  await assert.rejects(readSchedule(async () => frame([], {workspace_id: 'other'}), 'w', () => {}));
  let n = 0;
  await assert.rejects(readSchedule(async () => frame([], {baseline: {revision_id: ++n}, next_cursor: 'next'}), 'w', () => {}));
  await assert.rejects(readSchedule(async () => frame([]), 'w', () => {throw Error('withdrawn');}), /withdrawn/);
});
test('session inventory dates never become bars; inconsistent ends are omitted', () => {
  const session = {id: 'attempt:session:w:h/s', kind: 'attempt', actual: {start: '2026-10-01', end: '2026-10-02'}};
  assert.deepEqual(spans(session), []); assert.equal(chartRange([session]), null);
  assert.deepEqual(spans({actual: {start: '2026-10-08', end: '2026-10-02'}})[0], {kind: 'actual', start: dayNumber('2026-10-08'), end: null, open: true, recordedOpen: undefined});
});
test('tree uses explicit parents, preserves undated ancestors and never invents phase rows', () => {
  const rows = [{id: 'm', kind: 'milestone', parent_id: null}, {id: 'r', kind: 'requirement', parent_id: 'm'},
    {id: 'w', kind: 'work', parent_id: 'r', actual: {start: '2026-10-01', end: null}},
    {id: 'o', kind: 'work', parent_id: null, actual: {start: '2026-10-01', end: null}}];
  assert.deepEqual(scheduleTree(rows, new Set()).map(value => [value.row.id, value.depth]), [['m',1],['r',2],['w',3],['unassigned',1],['o',2]]);
  assert.deepEqual(scheduleTree(rows, new Set(['r'])).map(value => value.row.id), ['m','r','unassigned','o']);
});
test('discussion references contain exact backend identities only', () => {
  assert.deepEqual(scheduleRef({kind: 'milestone', refs: {milestone_id: 'review', plan_revision_id: 'ignored'}}, {baseline: {revision_id: 'baseline'}}), {kind: 'milestone', id: 'review', revision_id: 'baseline'});
  assert.deepEqual(scheduleRef({kind: 'issue', id: 'issue:mapping', refs: {}}, {}), {kind: 'issue_mapping', id: 'mapping'});
  assert.deepEqual(scheduleRef({kind: 'attempt', parent_id: 'work:work-id', refs: {}}, {}), {kind: 'work_item', id: 'work-id'});
});
test('reason vocabulary has both locales and unknown codes stay out of body copy', () => {
  assert.equal(reasonLabel('future_private_code', 'en'), 'Not available'); assert.equal(reasonLabel('future_private_code', 'zh-TW'), '無法取得');
  assert.equal(reasonLabel('inconsistent_dates', 'zh-TW'), '結束早於開始，已省略結束日期');
});
