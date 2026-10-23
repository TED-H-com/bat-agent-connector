import assert from 'node:assert/strict';
import test from 'node:test';
import {axisDate, compactDate, compactTime, fullTime} from '../src/ui/time.js';

const now = Date.parse('2026-10-13T09:00:00.000Z');
const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

for (const locale of ['en', 'zh-TW']) test(`axis labels match their scale and keep calendar dates ${locale}`, () => {
  for (const value of ['2026-09-25', '2026-10-01', '2026-11-01']) {
    const month = Number(value.slice(5, 7));
    assert.equal(axisDate(value, {locale, unit: 'month'}), locale === 'en' ? months[month - 1] : `${month}月`);
    for (const unit of ['day', 'week']) assert.equal(axisDate(value, {locale, unit, now}), compactDate(value, {locale, now}));
  }
  for (const value of [null, '', 'not-a-date', '2026-02-30']) assert.equal(axisDate(value, {locale, unit: 'month'}), '');
});

function absolute(date: Date, locale: string) {
  const zh = locale.toLowerCase().startsWith('zh');
  let hour = date.getHours();
  const minute = String(date.getMinutes()).padStart(2, '0');
  const period = hour < 12 ? (zh ? '上午' : 'AM') : (zh ? '下午' : 'PM');
  let h12 = hour % 12;
  if (h12 === 0) h12 = 12;
  return zh ? `${date.getMonth() + 1}月${date.getDate()}日 ${period}${h12}:${minute}`
    : `${months[date.getMonth()]} ${date.getDate()}, ${h12}:${minute} ${period}`;
}

test('compact time stays relative for a day and drops seconds after that', () => {
  assert.equal(compactTime(new Date(now - 20_000).toISOString(), {locale: 'en-US', now}), 'just now');
  assert.equal(compactTime(new Date(now - 5 * 60_000).toISOString(), {locale: 'en-US', now}), '5 min ago');
  assert.equal(compactTime(new Date(now - 2 * 60 * 60_000).toISOString(), {locale: 'en-US', now}), '2 hr ago');
  assert.equal(compactTime(new Date(now - 2 * 60 * 60_000).toISOString(), {locale: 'zh-TW', now}), '2 小時前');
  assert.equal(compactTime(new Date(now - 12 * 60_000).toISOString(), {locale: 'zh-TW', now}), '12 分鐘前');
  const older = new Date(now - 25 * 60 * 60_000);
  assert.equal(compactTime(older.toISOString(), {locale: 'en-US', now}), absolute(older, 'en-US'));
  assert.equal(compactTime(older.toISOString(), {locale: 'zh-TW', now}), absolute(older, 'zh-TW'));
  assert.equal(compactTime(new Date(now - 24 * 60 * 60_000).toISOString(), {locale: 'en-US', now}), absolute(new Date(now - 24 * 60 * 60_000), 'en-US'));
  assert.doesNotMatch(compactTime(older.toISOString(), {locale: 'en-US', now}), /\d:\d{2}:\d{2}/);
  assert.equal(compactTime('', {locale: 'en-US', now}), '');
  assert.equal(compactTime('not-a-time', {locale: 'en-US', now}), '');
});

for (const locale of ['en', 'zh-TW']) test(`minute/hour/day and future boundaries ${locale}`, () => {
  const zh = locale === 'zh-TW';
  for (const [distance, en, chinese] of [[59999, 'just now', '剛剛'], [60000, '1 min ago', '1 分鐘前'], [3599999, '59 min ago', '59 分鐘前'], [3600000, '1 hr ago', '1 小時前'], [86399999, '23 hr ago', '23 小時前']] as const)
    assert.equal(compactTime(now - distance, {locale, now}), zh ? chinese : en);
  for (const [distance, en, chinese] of [[60000, 'in 1 min', '1 分鐘後'], [300000, 'in 5 min', '5 分鐘後'], [3600000, 'in 1 hr', '1 小時後'], [86399999, 'in 23 hr', '23 小時後']] as const)
    assert.equal(compactTime(now + distance, {locale, now}), zh ? chinese : en);
  for (const direction of [-1, 1]) assert.equal(compactTime(now + direction * 86400000, {locale, now}), absolute(new Date(now + direction * 86400000), locale));
});

for (const locale of ['en', 'zh-TW']) test(`calendar dates and different years ${locale}`, () => {
  assert.equal(compactDate('2026-10-14', {locale, now}), locale === 'en' ? 'Oct 14' : '10月14日');
  assert.equal(compactDate('2025-10-14', {locale, now}), locale === 'en' ? 'Oct 14, 2025' : '2025年10月14日');
  assert.match(compactTime('2025-10-14T15:14:00Z', {locale, now}), /2025/);
  assert.doesNotMatch(compactTime('2026-10-06T15:14:00Z', {locale, now}), /2026/);
  for (const value of [undefined, null, '', 'not-a-time', new Date(NaN), '2026-02-30']) {
    assert.equal(compactDate(value, {locale, now}), '');
    assert.equal(compactTime(value, {locale, now}), '');
  }
  assert.match(fullTime('2026-10-14T15:14:00Z', {locale}), /2026/);
  assert.ok(fullTime('2026-10-14T15:14:00Z', {locale}).length > 20);
});
