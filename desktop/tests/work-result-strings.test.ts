import assert from 'node:assert/strict';
import {test} from 'node:test';
import {workResultStrings} from '../src/pm-work-result-strings.js';
import {STRINGS} from '../src/i18n.js';

test('every wres_ string exists in both en and zh-TW and is non-empty', () => {
  const zhKeys = Object.keys(workResultStrings['zh-TW']);
  const enKeys = Object.keys(workResultStrings.en);

  assert.deepEqual(new Set(zhKeys), new Set(enKeys), 'en and zh-TW must have identical keys');

  for (const key of zhKeys) {
    assert.ok(key.startsWith('wres_') || key.startsWith('error_code_'), `Key ${key} must have wres_ or error_code_ prefix`);
    const zhVal = workResultStrings['zh-TW'][key];
    const enVal = workResultStrings.en[key];
    assert.ok(typeof zhVal === 'string' && zhVal.trim().length > 0, `zh-TW value for ${key} must be non-empty`);
    assert.ok(typeof enVal === 'string' && enVal.trim().length > 0, `en value for ${key} must be non-empty`);
    assert.ok(STRINGS['zh-TW'][key] === zhVal, `STRINGS['zh-TW'] must contain ${key}`);
    assert.ok(STRINGS.en[key] === enVal, `STRINGS.en must contain ${key}`);
  }
});

test('no existing i18n.js key was redefined by workResultStrings', async () => {
  const {workbenchStrings} = await import('../src/workbench-strings.js');
  const {diagnosticsStrings} = await import('../src/diagnostics-strings.js');
  const {overviewStrings} = await import('../src/pm-overview-strings.js');
  const {scheduleStrings} = await import('../src/pm-schedule-strings.js');
  const {workViewStrings} = await import('../src/pm-work-view-strings.js');
  const {uiStrings} = await import('../src/ui/ui-strings.js');
  const {globalStrings} = await import('../src/global-strings.js');
  const {pmStrings} = await import('../src/pm-strings.js');
  const {workStartStrings} = await import('../src/pm-work-start-strings.js');

  const otherModules = [
    workbenchStrings,
    diagnosticsStrings,
    overviewStrings,
    scheduleStrings,
    workViewStrings,
    uiStrings,
    globalStrings,
    pmStrings,
    workStartStrings,
  ];

  const workResultKeys = new Set(Object.keys(workResultStrings['zh-TW']));

  for (const mod of otherModules) {
    for (const locale of ['zh-TW', 'en'] as const) {
      if (!mod[locale]) continue;
      for (const key of Object.keys(mod[locale])) {
        assert.ok(!workResultKeys.has(key), `Key collision: ${key} was already defined in another string module`);
      }
    }
  }
});
