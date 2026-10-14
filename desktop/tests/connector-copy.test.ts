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
      ? 'The existing service owner was confirmed. This service evidence does not prove Connector login or API compatibility'
      : '已確認既有服務的執行身分；這不代表 Connector 登入或 API 相容性已通過驗證');
    assert.equal(copy.pm_saved, locale === 'en' ? 'Saved in Connector' : '已儲存到 Connector');
  });
}
