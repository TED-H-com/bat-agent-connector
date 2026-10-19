import {test} from 'node:test';
import assert from 'node:assert/strict';
import {managedRecoveryKey} from '../src/managed-recovery.js';
import {STRINGS} from '../src/i18n.js';

for (const locale of ['en', 'zh-TW'] as const) test(`${locale}: local recovery uses localized summaries with codes only in details`, () => {
  for (const [code, key] of [
    ['MANAGED_RECOVERING', 'managed_service_recovering'],
    ['MANAGED_RUNTIME_START_TIMEOUT', 'managed_service_retry'],
    ['MANAGED_RECOVERY_IDENTITY_CHANGED', 'managed_service_identity'],
  ]) {
    assert.equal(managedRecoveryKey(code), key);
    const text = STRINGS[locale][key as keyof typeof STRINGS[typeof locale]];
    assert.equal(typeof text, 'string');
    assert.ok(!text.includes(code));
    assert.equal(/[\u4e00-\u9fff]/.test(text), locale === 'zh-TW');
    const help = STRINGS[locale][(key + '_help') as keyof typeof STRINGS[typeof locale]];
    assert.ok(help && !help.includes(code));
    assert.equal(/[\u4e00-\u9fff]/.test(help), locale === 'zh-TW');
  }
  assert.equal(managedRecoveryKey(null), null);
});
