import assert from 'node:assert/strict';
import {test} from 'node:test';
import {workFollowUpStrings} from '../src/pm-work-follow-up-strings.js';
import {STRINGS} from '../src/i18n.js';

test('every loop_next string exists in both en and zh-TW and is registered', () => {
  const zhKeys = Object.keys(workFollowUpStrings['zh-TW']);
  const enKeys = Object.keys(workFollowUpStrings.en);
  assert.deepEqual(new Set(zhKeys), new Set(enKeys));
  for (const key of zhKeys) {
    assert.ok(key.startsWith('loop_next_'), key);
    const zh = workFollowUpStrings['zh-TW'][key];
    const en = workFollowUpStrings.en[key];
    assert.equal(typeof zh, 'string');
    assert.equal(typeof en, 'string');
    assert.ok(zh.trim().length > 0, key);
    assert.ok(en.trim().length > 0, key);
    assert.equal(STRINGS['zh-TW'][key], zh);
    assert.equal(STRINGS.en[key], en);
    const prose = zh.replaceAll('Codex', '');
    assert.ok(!/[A-Za-z]{4,}/.test(prose) || key.startsWith('loop_next_err_'), `${key} leaves English in zh-TW: ${zh}`);
  }
});

test('work follow-up strings do not reuse another module key', async () => {
  const modules = await Promise.all([
    import('../src/workbench-strings.js'),
    import('../src/diagnostics-strings.js'),
    import('../src/pm-overview-strings.js'),
    import('../src/pm-schedule-strings.js'),
    import('../src/pm-work-view-strings.js'),
    import('../src/ui/ui-strings.js'),
    import('../src/global-strings.js'),
    import('../src/pm-strings.js'),
    import('../src/pm-work-start-strings.js'),
    import('../src/pm-work-result-strings.js'),
  ]);
  const ours = new Set(Object.keys(workFollowUpStrings.en));
  for (const mod of modules) {
    for (const table of Object.values(mod)) {
      if (!table || typeof table !== 'object') continue;
      for (const locale of ['zh-TW', 'en']) {
        const bag = table[locale];
        if (!bag) continue;
        for (const key of Object.keys(bag)) assert.ok(!ours.has(key), key);
      }
    }
  }
});
