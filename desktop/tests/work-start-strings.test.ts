import assert from 'node:assert/strict';
import {test} from 'node:test';
import {workStartStrings} from '../src/pm-work-start-strings.js';
import {STRINGS} from '../src/i18n.js';

const SHARED_ERROR_CODES = [
  'WORK_ITEM_CHANGED',
  'PROJECT_CHANGED',
  'PM_PACKAGE_SCOPE_CHANGED',
  'PM_PACKAGE_VERSION_CONFLICT',
  'PM_WORK_BINDING_CHANGED',
  'PROJECT_REPOSITORY_CHANGED',
  'REPOSITORY_REF_CHANGED',
  'PM_ADOPTED_PLAN_REQUIRED',
  'PM_LIFECYCLE_STOPPED',
  'PM_HANDOFF_TOO_LARGE',
];

const START_ONLY_ERROR_CODES = [
  'PM_WORK_ATTEMPT_CHANGED',
  'PM_WORK_START_IN_PROGRESS',
  'PM_WORK_START_OUTCOME_UNPROVEN',
  'PM_WORK_START_NOT_READY',
  'PM_WORK_START_PREREQUISITES',
  'PM_WORK_CONTROLLER_CONFLICT',
  'PM_WORK_BIND_FAILED',
  'PM_WORK_PACKAGE_FAILED',
  'PM_HANDOFF_FAILED',
];

test('every work.start string exists in both en and zh-TW and is non-empty', () => {
  const zhKeys = Object.keys(workStartStrings['zh-TW']);
  const enKeys = Object.keys(workStartStrings.en);

  assert.deepEqual(new Set(zhKeys), new Set(enKeys), 'en and zh-TW must have identical keys');

  for (const key of zhKeys) {
    const zhVal = workStartStrings['zh-TW'][key];
    const enVal = workStartStrings.en[key];
    assert.ok(typeof zhVal === 'string' && zhVal.trim().length > 0, `zh-TW value for ${key} must be non-empty`);
    assert.ok(typeof enVal === 'string' && enVal.trim().length > 0, `en value for ${key} must be non-empty`);
  }
});

test('workStartStrings defines no error_code_ key for shared codes, and defines wst_error_ in both locales', () => {
  assert.equal(SHARED_ERROR_CODES.length, 10);

  for (const code of SHARED_ERROR_CODES) {
    assert.equal(workStartStrings.en[`error_code_${code}`], undefined, `shared code ${code} must not define error_code_ in en`);
    assert.equal(workStartStrings['zh-TW'][`error_code_${code}`], undefined, `shared code ${code} must not define error_code_ in zh-TW`);

    const wstKey = `wst_error_${code}`;
    assert.ok(typeof workStartStrings.en[wstKey] === 'string' && workStartStrings.en[wstKey].length > 0, `en missing ${wstKey}`);
    assert.ok(typeof workStartStrings['zh-TW'][wstKey] === 'string' && workStartStrings['zh-TW'][wstKey].length > 0, `zh-TW missing ${wstKey}`);
  }
});

test('all 9 start-only error codes exist in both locales and in STRINGS', () => {
  assert.equal(START_ONLY_ERROR_CODES.length, 9);

  for (const code of START_ONLY_ERROR_CODES) {
    const key = `error_code_${code}`;
    assert.ok(workStartStrings['zh-TW'][key], `workStartStrings['zh-TW'] missing ${key}`);
    assert.ok(workStartStrings.en[key], `workStartStrings.en missing ${key}`);
    assert.ok(STRINGS['zh-TW'][key], `STRINGS['zh-TW'] missing ${key}`);
    assert.ok(STRINGS.en[key], `STRINGS.en missing ${key}`);
  }
});

test('no existing i18n.js key was redefined by workStartStrings', async () => {
  // Import all other string files to verify no collisions
  const {workbenchStrings} = await import('../src/workbench-strings.js');
  const {diagnosticsStrings} = await import('../src/diagnostics-strings.js');
  const {overviewStrings} = await import('../src/pm-overview-strings.js');
  const {scheduleStrings} = await import('../src/pm-schedule-strings.js');
  const {workViewStrings} = await import('../src/pm-work-view-strings.js');
  const {uiStrings} = await import('../src/ui/ui-strings.js');
  const {globalStrings} = await import('../src/global-strings.js');
  const {pmStrings} = await import('../src/pm-strings.js');

  const otherModules = [
    workbenchStrings,
    diagnosticsStrings,
    overviewStrings,
    scheduleStrings,
    workViewStrings,
    uiStrings,
    globalStrings,
    pmStrings,
  ];

  const workStartKeys = new Set(Object.keys(workStartStrings['zh-TW']));

  for (const mod of otherModules) {
    for (const locale of ['zh-TW', 'en'] as const) {
      if (!mod[locale]) continue;
      for (const key of Object.keys(mod[locale])) {
        assert.ok(!workStartKeys.has(key), `Key collision: ${key} was already defined in another string module`);
      }
    }
  }
});
