import {readFileSync} from 'node:fs';
import {expect, test} from '@playwright/test';
import {openSession} from './session-fixture.ts';

// These operation records are generated and checked by the real Python producer tests.
const operations = {
  staging: JSON.parse(readFileSync(
    new URL('../../tests/fixtures/checkpoint-refusal-staging.json', import.meta.url), 'utf8')),
  destination: JSON.parse(readFileSync(
    new URL('../../tests/fixtures/checkpoint-refusal-destination.json', import.meta.url), 'utf8')),
};
const copy = {
  en: {
    staging: 'An earlier attempt may have left an incomplete copy. It has been kept.',
    destination: 'The destination appeared while the copy was being prepared. Both paths have been kept.',
    why: 'The Connector only removes folders it can prove it created.',
    next: 'Use Continue from checkpoint again. The new attempt uses a new temporary folder. After checking the old copy, remove it yourself if you no longer need it.',
    destinationNext: 'Check the destination, then use Continue from checkpoint again. The new attempt uses a new temporary folder. After checking the old copy, remove it yourself if you no longer need it.',
  },
  'zh-TW': {
    staging: '先前的嘗試可能留下未完成的副本，目前已保留。',
    destination: '準備副本時，目的地已出現；兩個路徑都已保留。',
    why: 'Connector 只會移除能證明由它建立的資料夾。',
    next: '請再次使用「從檢查點繼續」。新的嘗試會使用新的暫存資料夾。檢查舊副本後，若已不需要，請自行移除。',
    destinationNext: '請先檢查目的地，再使用「從檢查點繼續」。新的嘗試會使用新的暫存資料夾。檢查舊副本後，若已不需要，請自行移除。',
  },
};

for (const locale of ['en', 'zh-TW'] as const) for (const kind of ['staging', 'destination'] as const) {
  test(`retained checkpoint copy explains ${kind} refusal in ${locale}`, async ({browser}) => {
    const operation = operations[kind];
    const s = await openSession(browser, {locale, width: locale === 'zh-TW' ? 390 : 1440,
      overrides: [(_input, url) => url.pathname === `/operations/${operation.operation_id}`
        ? {status: 200, data: {operation}} : undefined]});
    try {
      await s.page.goto(`/dashboard/#/op/${operation.operation_id}`);
      const main = s.page.locator('main');
      await expect(main).toContainText(copy[locale][kind]);
      await expect(main).toContainText(copy[locale].why);
      await expect(main).toContainText(kind === 'destination' ? copy[locale].destinationNext : copy[locale].next);
      await expect(main.locator('[data-checkpoint-retained] code').first())
        .toHaveText('/srv/managed/app-11111111.batc-tmp-cccccccccccc');
      if (kind === 'destination') await expect(main.locator('[data-checkpoint-retained] code').nth(1))
        .toHaveText('/srv/managed/app-11111111');
      await expect(main).not.toContainText('GIT_FAILED');
      await expect(main).not.toContainText('creation is unproven');
      if (locale === 'zh-TW') await expect(main).not.toContainText(operation.status_reason);
      expect(s.writes).toEqual([]);
    } finally {
      await s.context.close();
    }
  });
}
