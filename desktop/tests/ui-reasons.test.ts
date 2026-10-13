import assert from 'node:assert/strict';
import {test} from 'node:test';
import {reasonLabel, reasonLabels, uiStrings} from '../src/ui/ui-strings.js';

for (const locale of ['en', 'zh-TW'] as const) {
  test(`projection reasons use the supplied app translator: ${locale}`, () => {
    const t = (key: string) => uiStrings[locale][key];
    const index = locale === 'en' ? 0 : 1;
    assert.ok(Object.isFrozen(reasonLabels));
    for (const [code, labels] of Object.entries(reasonLabels)) {
      assert.ok(labels[index], `${code} has a ${locale} label`);
      assert.equal(reasonLabel(t, code), labels[index]);
    }
    // New server codes (including inherited object names) never become body copy.
    for (const code of ['future_private_reason', 'toString', '__proto__', '', null, undefined]) {
      assert.equal(reasonLabel(t, code), locale === 'en' ? 'Not available' : '無法取得');
    }
    assert.equal(reasonLabel(key => `app:${key}`, 'no_adopted_plan'), 'app:ui_reason_no_adopted_plan');
  });
}
