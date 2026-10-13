import assert from 'node:assert/strict';
import test from 'node:test';
import {compactTime} from '../src/ui/time.js';

const now = Date.parse('2026-10-13T09:00:00.000Z');
const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

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
  assert.equal(compactTime(new Date(now - 5 * 60_000).toISOString(), {locale: 'en-US', now}), '5m ago');
  assert.equal(compactTime(new Date(now - 2 * 60 * 60_000).toISOString(), {locale: 'en-US', now}), '2h ago');
  assert.equal(compactTime(new Date(now - 2 * 60 * 60_000).toISOString(), {locale: 'zh-TW', now}), '2 小時前');
  assert.equal(compactTime(new Date(now - 12 * 60_000).toISOString(), {locale: 'zh-TW', now}), '12 分鐘前');
  const older = new Date(now - 25 * 60 * 60_000);
  assert.equal(compactTime(older.toISOString(), {locale: 'en-US', now}), absolute(older, 'en-US'));
  assert.equal(compactTime(older.toISOString(), {locale: 'zh-TW', now}), absolute(older, 'zh-TW'));
  assert.equal(compactTime(new Date(now - 24 * 60 * 60_000).toISOString(), {locale: 'en-US', now}), absolute(new Date(now - 24 * 60 * 60_000), 'en-US'));
  assert.doesNotMatch(compactTime(older.toISOString(), {locale: 'en-US', now}), /\d:\d{2}:\d{2}/);
  assert.equal(compactTime('', {locale: 'en-US', now}), '');
  assert.equal(compactTime('not-a-time', {locale: 'en-US', now}), 'not-a-time');
});
