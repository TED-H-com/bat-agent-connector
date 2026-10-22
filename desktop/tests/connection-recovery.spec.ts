import {test, expect} from '@playwright/test';
import {STRINGS} from '../src/i18n.js';
import {conversationFixture, mountConversation} from './conversation-fixture';

for (const locale of ['en-US', 'zh-TW']) {
  const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
  for (const second of ['connect', 'retry']) test(`an older Connect cannot clear a newer ${second} ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage(), fixture = conversationFixture();
    const releases: (() => void)[] = [];
    await mountConversation(page, false, async input => {
      if (input.path === '/capabilities') await new Promise<void>(resolve => releases.push(resolve));
      return fixture.dispatch(input);
    });
    await page.addInitScript(() => sessionStorage.clear());
    try {
      await page.goto('/dashboard/');
      await expect(page.getByRole('button', {name: copy.connect, exact: true})).toBeVisible();
      await page.evaluate(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
      await page.evaluate(() => {location.hash = '#/settings';});
      await expect(page.getByRole('button', {name: copy.connection_retry_remembered, exact: true})).toBeVisible();
      await page.locator('input[type=password]').fill('fixture-token');
      await page.getByRole('button', {name: copy.connect, exact: true}).click();
      await expect.poll(() => releases.length).toBe(1);
      await page.getByRole('button', {name: second === 'connect' ? copy.connect : copy.connection_retry_remembered, exact: true}).click();
      await expect.poll(() => releases.length).toBe(2);
      const old = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/capabilities');
      releases[0](); await old;
      // Finish processing the older response before the newer connection verifies.
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      releases[1]();
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      await expect(page.getByRole('alert')).toHaveCount(0);
    } finally {releases.forEach(release => release()); await context.close();}
  });

  test(`a local credential refusal retains the original submission key after Retry ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage(), fixture = conversationFixture();
    const writes: any[] = [];
    await mountConversation(page, false, async input => {
      if (input.method === 'POST') {
        writes.push(input);
        return {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Operation reply unavailable'}}};
      }
      return fixture.dispatch(input);
    });
    try {
      await page.clock.install();
      await page.goto('/dashboard/#/session/demo/session-1');
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      const draft = page.locator('textarea').first(), send = page.getByRole('button', {name: copy.send, exact: true});
      await draft.fill('Keep the exact original request');
      await send.click();
      await expect.poll(() => writes.length).toBe(1);
      await expect(send).toBeEnabled();
      const storedKeys = () => page.evaluate(() => Object.fromEntries(Object.entries(localStorage).filter(([key]) => key.startsWith('batc.key.'))));
      const original = await storedKeys();
      expect(Object.keys(original)).not.toHaveLength(0);
      await page.evaluate(async () => {
        const {browserCredentialRefusals} = await import('/src/credential-refusals.js');
        browserCredentialRefusals.refuse('fixture-token', 401, 'POST', '/artifacts/uploads/op_example/content');
      });
      await send.click();
      await expect(page.locator('#live')).toHaveText(copy.ui_connection_refused);
      expect(writes).toHaveLength(1);
      expect(await storedKeys()).toEqual(original);
      await expect(draft).toHaveValue('Keep the exact original request');
      await page.evaluate(() => {location.hash = '#/settings';});
      await page.getByRole('button', {name: copy.connection_retry_remembered, exact: true}).click();
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      await page.evaluate(() => {location.hash = '#/session/demo/session-1';});
      await expect(draft).toHaveValue('Keep the exact original request');
      await send.click();
      await expect.poll(() => writes.length).toBe(2);
      expect(writes[1]).toEqual(writes[0]);
    } finally {await context.close();}
  });
}
