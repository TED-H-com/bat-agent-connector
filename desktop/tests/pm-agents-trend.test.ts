import assert from 'node:assert/strict';
import test from 'node:test';
import {isBucketPayload, stackDailyBuckets} from '../src/pm-agents-trend.js';

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
