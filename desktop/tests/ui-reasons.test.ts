import assert from 'node:assert/strict';
import {test} from 'node:test';
import {reasonLabel, reasonLabels, uiStrings} from '../src/ui/ui-strings.js';
import {proposalReasonLabel} from '../src/pm-plan-proposals.js';
import {discussionStrings} from '../src/pm-discussions-strings.js';

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
  test(`proposal reasons keep central wording and their own fallback: ${locale}`, () => {
    const strings = {...uiStrings[locale], ...discussionStrings[locale]};
    const t = (key: string) => strings[key];
    const index = locale === 'en' ? 0 : 1;
    for (const [code, labels] of Object.entries({
      invalid_proposal: ['Invalid proposal', '提案無效'],
      content_changed: ['Discussion changed', '討論內容已變更'],
      plan_head_changed: ['Plan changed', '計畫已變更'],
      superseded: ['Replaced by a newer update', '已有較新的更新'],
      withdrawn: ['Source unavailable', '來源無法取得'],
    })) {
      assert.equal(reasonLabel(t, code), labels[index]);
      assert.equal(proposalReasonLabel(t, code), labels[index]);
    }
    for (const code of ['future_private_reason', 'toString', '__proto__', '', null, undefined]) {
      assert.equal(proposalReasonLabel(t, code), locale === 'en' ? 'Out of date' : '已過期');
      assert.equal(reasonLabel(t, code), locale === 'en' ? 'Not available' : '無法取得');
    }
  });
}
