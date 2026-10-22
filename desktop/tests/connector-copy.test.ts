import assert from 'node:assert/strict';
import {test} from 'node:test';
import {STRINGS} from '../src/i18n.js';

for (const locale of ['en', 'zh-TW'] as const) {
  test(`the shared catalogue names Connector in ${locale}`, () => {
    for (const [key, text] of Object.entries(STRINGS[locale])) {
      assert.doesNotMatch(text, /\bcentrals?\b|\bcentrally\b|中央/i, key);
      if (locale === 'zh-TW') assert.doesNotMatch(text, /\bsessions?\b/i, key);
    }
  });

  test(`local startup receipts remain distinct from Connector acceptance in ${locale}`, () => {
    const copy = STRINGS[locale];
    assert.equal(copy.bootstrap_separate, locale === 'en'
      ? 'Connector connection and identity checks continue independently. A local receipt does not mean Connector accepted the operation.'
      : 'Connector 連線與身分驗證各自持續進行。本機回執不代表 Connector 已接受操作。');
    assert.equal(copy.bootstrap_service_running, locale === 'en'
      ? 'The existing service owner was confirmed. This service evidence does not prove Connector login or API compatibility.'
      : '已確認既有服務的執行身分；這不代表 Connector 登入或 API 相容性已通過驗證。');
    assert.equal(copy.pm_saved, locale === 'en' ? 'Saved in Connector.' : '已儲存到 Connector。');
  });

  test(`Connector copy uses the named setup disclosure and schedule in ${locale}`, () => {
    const copy = STRINGS[locale];
    assert.equal(copy.managed_join_existing, locale === 'en' ? 'Join another Connector' : '加入既有的 Connector');
    assert.equal(copy.managed_recovery_help, locale === 'en'
      ? 'Existing data and background work are retained. If an older version still has work, let it finish, then quit and reopen the desktop app. The startup error appears below. You can also expand Advanced to join another Connector.'
      : '既有資料與背景工作會保留。若舊版本仍有工作，先讓它完成，再結束並重新開啟桌面程式。此次啟動錯誤列在下方，也可以展開「進階設定」，加入其他 Connector。');
    assert.equal(copy.pmc_schedule, locale === 'en' ? 'Connector schedule' : 'Connector 排程');
    if (locale === 'en') assert.match(copy.projects_help, /^Projects and work items are Connector's own records:/);
  });
}

for (const locale of ['en', 'zh-TW'] as const) {
  test(`permission guidance reuses readable Connection labels in ${locale}`, () => {
    const copy = STRINGS[locale];
    for (const [key, scopes] of Object.entries({capture_scope_observe: ['observe'], capture_scope_manage: ['observe', 'manage'],
      agents_scope: ['observe', 'manage', 'start'], agents_token: ['observe', 'operate', 'start'], ar_approve_scope: ['approve', 'observe'],
      task_control_unavailable: ['operate'], cleanup_scope: ['cleanup'], dep_needs_scope: ['deploy'], needs_integrate_scope: ['integrate'], needs_start_scope: ['start']})) {
      for (const scope of scopes) assert.ok(copy[key].includes(copy['connection_scope_' + scope]), key + ' / ' + scope);
      assert.doesNotMatch(copy[key], /\b(?:observe|manage|start|operate|approve)\b(?! access)/, key);
    }
  });
}
test('Traditional Chinese uses one agent and worktree vocabulary', () => {
  for (const [key, value] of Object.entries(STRINGS['zh-TW'])) {
    assert.doesNotMatch(value.replace(/\{\w+\}/g, ''), /\b(?:agents?|worktrees?)\b/i, key);
    if (key !== 'needs_start_scope_key') assert.doesNotMatch(value.replace(/\{\w+\}/g, ''), /\b(?:observe|start|manage|admin)\b|\b(?:operate|approve|cleanup|deploy|integrate)\b\s*權限/, key);
  }
});

test('English labels keep their English agent and worktree vocabulary', () => {
  for (const value of Object.values(STRINGS.en)) assert.doesNotMatch(value, /代理|工作樹/);
});
