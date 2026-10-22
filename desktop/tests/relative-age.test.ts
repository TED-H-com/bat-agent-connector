import {test} from 'node:test';
import assert from 'node:assert/strict';
import {relativeAge, relativeAgeStrings} from '../src/relative-age.js';
import {displayFact, healthAge, healthLabel} from '../src/health-fact.js';
import {STRINGS} from '../src/i18n.js';

for (const locale of ['en', 'zh-TW']) test(`relative ages share minute/hour/day boundaries ${locale}`, () => {
  const t = (key: string, vars = {}) => Object.entries(vars).reduce((text, [key, value]) => text.replaceAll(`{${key}}`, String(value)), STRINGS[locale][key]);
  const zh = locale === 'zh-TW';
  assert.equal(healthAge, relativeAge);
  for (const [seconds, en, chinese] of [[0, 'just now', '剛剛'], [59, 'just now', '剛剛'],
    [60, '1 min ago', '1 分鐘前'], [3599, '59 min ago', '59 分鐘前'],
    [3600, '1 hr ago', '1 小時前'], [86399, '23 hr ago', '23 小時前'],
    [86400, '1 d ago', '1 天前'], [172800, '2 d ago', '2 天前']] as const) {
    assert.equal(relativeAge(seconds, t), zh ? chinese : en);
  }
  for (const age of [null, undefined, NaN, Infinity]) assert.equal(relativeAge(age, t), relativeAgeStrings[locale].age_unknown);
  assert.equal(relativeAge(-60, t), zh ? '剛剛' : 'just now');
  assert.equal(relativeAge(86400, t, {direction: 'future'}), zh ? '1 天後' : 'in 1 d');
  assert.equal(relativeAge(86400, t, {direction: 'duration'}), zh ? '1 天' : '1 d');
  const fact = displayFact({key: 'health_idle', observed_at: 0, valid_until: 86400001}, {now: 86400000});
  assert.equal(fact.freshness, 'current'); // Formatting a day never changes validity.
  assert.equal(healthLabel(fact, t), zh ? '閒置' : 'Idle');
  const stale = displayFact({...fact, valid_until: 86400000}, {now: 86400000});
  assert.equal(stale.freshness, 'stale');
  assert.equal(healthLabel(stale, t), zh ? '閒置 · 1 天前 · 檢查中' : 'Idle · 1 d ago · Checking');
  assert.equal(healthLabel(stale, t, {checking: false}), zh ? '閒置 · 1 天前' : 'Idle · 1 d ago');
});
