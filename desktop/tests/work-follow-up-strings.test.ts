import assert from 'node:assert/strict';
import {test} from 'node:test';
import {workFollowUpStrings} from '../src/pm-work-follow-up-strings.js';
import {STRINGS} from '../src/i18n.js';

// Import lazily so the failing-first run still checks the existing string table.
const decisionModule = '../src/pm-work-follow-up-decision.js';
const ready = {reading: false, admissionReady: true, resultSettled: true,
  capabilitiesLoaded: true, managed: true, connected: true, sendOffer: {action: 'session.send'}};

test('one follow-up decision preserves every server reason and its next step', async () => {
  const {workFollowUpDecision} = await import(decisionModule);
  for (const reason of ['codex_uncorrelated_turn_boundary', 'not_controller', 'not_writer_member', 'missing_scope', 'future_reason_example']) {
    // A blocked result takes precedence over a cached offer or session capability.
    const result = workFollowUpDecision({...ready, blockedReason: reason});
    assert.equal(result.allowed, false);
    assert.equal(result.source, 'server');
    assert.equal(result.reason, reason);
    assert.equal(result.showCode, reason === 'future_reason_example');
    assert.equal(result.pillKey === 'loop_next_scope_readonly', reason === 'missing_scope');
    for (const lang of ['en', 'zh-TW']) {
      for (const key of [result.messageKey, result.nextStepKey, result.pillKey]) {
        assert.ok(workFollowUpStrings[lang][key]?.trim(), `${lang}: ${key}`);
      }
    }
  }
});

test('client gates never acquire a fabricated server reason', async () => {
  const {workFollowUpDecision} = await import(decisionModule);
  for (const [facts, reason] of [
    [{connected: false}, 'connection'], [{reading: true}, 'admission_pending'],
    [{admissionReady: false}, 'admission_unavailable'], [{resultSettled: false}, 'admission_pending'],
    [{stopped: true}, 'stopped'], [{disposed: true}, 'stopped'], [{suspended: true}, 'suspended'],
    [{capabilitiesLoaded: false}, 'capabilities_pending'], [{managed: false}, 'unmanaged'],
    [{sendOffer: null}, 'no_send_offer'],
  ]) {
    const result = workFollowUpDecision({...ready, ...facts});
    assert.equal(result.allowed, false);
    assert.equal(result.source, 'client');
    assert.equal(result.reason, reason);
    assert.equal(result.showCode, false);
  }
  assert.equal(workFollowUpDecision(ready).allowed, true);
});

test('new decision copy and next steps exist in both locales', () => {
  for (const lang of ['en', 'zh-TW']) {
    for (const key of ['loop_next_codex_step', 'loop_next_controller_step', 'loop_next_member_blocked',
      'loop_next_member_step', 'loop_next_scope_blocked', 'loop_next_scope_step', 'loop_next_blocked_unknown',
      'loop_next_refresh_step', 'loop_next_no_offer', 'loop_next_scope_readonly', 'loop_next_member_readonly', 'loop_next_blocked']) {
      assert.ok(workFollowUpStrings[lang][key]?.trim(), `${lang}: ${key}`);
    }
  }
});

test('an admission reread pauses new sends without losing original receipt recovery', async () => {
  const {workFollowUpDecision} = await import(decisionModule);
  const checking = workFollowUpDecision({...ready, reading: true, admissionReady: false});
  assert.equal(checking.allowed, false);
  assert.equal(checking.canReadReceipt, true);
  for (const gate of [{stopped: true}, {suspended: true}, {disposed: true}, {managed: false}, {capabilitiesLoaded: false}]) {
    assert.equal(workFollowUpDecision({...ready, ...gate}).canReadReceipt, false);
  }
});

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
