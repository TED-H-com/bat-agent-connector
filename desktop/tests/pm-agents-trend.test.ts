import assert from 'node:assert/strict';
import test from 'node:test';
import {resources} from './demo/resources.ts';
import {demoProject} from './demo/project.ts';
import {isBucketPayload, niceCeiling, periodWindow, stackDailyBuckets} from '../src/pm-agents-trend.js';

const now = new Date('2026-10-13T09:01:00.000Z');

function day(date: string, total: number | null, complete: boolean) {
  return {date, complete, tokens: {input: total, cache_read: null, cache_write: null, output: null, reasoning: null, total}};
}

function buckets(series: object[], tz = 'UTC') {
  return {granularity: 'day', tz, series};
}

test('buckets are absent until the read asks for them', () => {
  assert.equal(isBucketPayload(undefined), false);
  assert.equal(isBucketPayload({granularity: 'day', tz: 'UTC'}), false);
  assert.equal(stackDailyBuckets(undefined, now), null);
});

test('a CLI stack sums totals only and leaves a null addend as a gap', () => {
  const stacked = stackDailyBuckets(buckets([
    {source_id: 'a', cli: 'codex', days: [day('2026-10-12', 100, true), day('2026-10-13', 40, false)]},
    {source_id: 'b', cli: 'codex', days: [day('2026-10-12', 25, true), day('2026-10-13', null, false)]},
    {source_id: 'c', cli: 'claude', days: [day('2026-10-12', 0, true), day('2026-10-13', 10, false)]},
  ]), now);
  assert.ok(stacked);
  assert.deepEqual(stacked.clis, ['codex', 'claude']);
  assert.deepEqual(stacked.days[0], {
    date: '2026-10-12',
    parts: [{cli: 'codex', total: 125}, {cli: 'claude', total: 0}],
    partial: false,
    mark: '',
  });
  assert.equal(stacked.days[1].parts[0].total, null);
  assert.equal(stacked.days[1].parts[1].total, 10);
  assert.equal(stacked.days[1].partial, true);
  assert.equal(stacked.days[1].mark, 'in_progress');
});

test('a finished incomplete day settles and a covered zero stays zero', () => {
  const stacked = stackDailyBuckets(buckets([
    {cli: 'codex', days: [day('2026-10-11', 0, true), day('2026-10-12', 8, false)]},
  ], 'Asia/Taipei'), now);
  assert.equal(stacked?.tz, 'Asia/Taipei');
  assert.equal(stacked?.days[0].parts[0].total, 0);
  assert.equal(stacked?.days[0].mark, '');
  assert.equal(stacked?.days[0].partial, false);
  assert.equal(stacked?.days[1].mark, 'settling');
});

test('input and output are not added back into the total', () => {
  const stacked = stackDailyBuckets(buckets([
    {cli: 'codex', days: [{date: '2026-10-13', complete: true, tokens: {input: 100, output: 50, total: 150}}]},
  ]), now);
  assert.equal(stacked?.days[0].parts[0].total, 150);
});

test('a period starts at local midnight of today minus N plus one', () => {
  const frozen = new Date('2026-10-13T09:00:00.000Z');
  assert.equal(periodWindow(frozen, 7, 'Asia/Taipei').since, '2026-10-06T16:00:00.000Z');
  assert.equal(periodWindow(frozen, 1, 'Asia/Taipei').since, '2026-10-12T16:00:00.000Z');
  assert.equal(periodWindow(frozen, 30, 'Asia/Taipei').since, '2026-09-13T16:00:00.000Z');
  assert.equal(periodWindow(frozen, 7, 'Asia/Taipei').until, frozen.toISOString());
});

test('a clipped open day is a partial day and only today is in progress', () => {
  const frozen = new Date('2026-10-13T09:00:00.000Z');
  const bounds = periodWindow(frozen, 7, 'Asia/Taipei');
  const stacked = stackDailyBuckets(buckets([
    {cli: 'codex', days: [day('2026-10-06', 10, false), day('2026-10-07', 10, true), day('2026-10-13', 10, false)]},
  ], 'Asia/Taipei'), frozen, bounds);
  assert.equal(stacked?.days[0].mark, 'partial_day');
  assert.equal(stacked?.days[1].mark, '');
  assert.equal(stacked?.days[2].mark, 'in_progress');
});

test('the y ceiling is one nice step above every magnitude', () => {
  assert.equal(niceCeiling(600), 1000);
  assert.equal(niceCeiling(1000), 2000);
  assert.equal(niceCeiling(2000), 2500);
  assert.equal(niceCeiling(2500), 5000);
  assert.equal(niceCeiling(1234567), 2e6);
  assert.equal(niceCeiling(25345678), 5e7);
});

test('demo totals count absolute observations with and without zoned buckets', () => {
  const project = demoProject('en');
  const window = periodWindow(new Date(project.now), 7, 'Asia/Taipei');
  const query = new URLSearchParams(window);
  const totalRows = resources(project, new URL(`http://fixture/resources?${query}`)).rows;
  for (const zone of ['Asia/Taipei', 'UTC', 'America/New_York']) {
    query.set('buckets', 'day'); query.set('tz', zone);
    const read = resources(project, new URL(`http://fixture/resources?${query}`));
    assert.deepEqual(read.rows, totalRows);
    const series = (read.buckets as {series: {source_id: string, days: {tokens: {total: number | null}}[]}[]}).series;
    for (const row of read.rows as {source_id: string, metrics: {total: {value: number}} | null}[]) {
      if (!row.metrics) continue;
      const days = series.find(item => item.source_id === row.source_id)!.days;
      assert.equal(days.reduce((sum, day) => sum + (day.tokens.total ?? 0), 0), row.metrics.total.value);
    }
  }
});

test('demo window advancement keeps the actual first and last sample times', () => {
  const project = demoProject('en');
  const query = new URLSearchParams(periodWindow(new Date(project.now), 7, 'Asia/Taipei'));
  const before = resources(project, new URL(`http://fixture/resources?${query}`)).rows;
  query.set('until', new Date(project.now + 3 * 60000).toISOString());
  const after = resources(project, new URL(`http://fixture/resources?${query}`)).rows;
  assert.deepEqual(after, before);
  for (const row of after as {metrics: object | null, first_at: string, last_at: string}[]) {
    if (!row.metrics) continue;
    assert.equal(row.first_at, '2026-10-07T01:00:00.000Z');
    assert.equal(row.last_at, '2026-10-13T01:00:00.000Z');
  }
});
