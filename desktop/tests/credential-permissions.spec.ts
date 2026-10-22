import {test, expect} from '@playwright/test';
import {STRINGS} from '../src/i18n.js';
import {browserAuthService} from './browser-auth-service';

test.describe.configure({mode: 'parallel'});

for (const locale of ['en-US', 'zh-TW']) {
  const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
  test(`an action permission refusal keeps a remembered reader connected ${locale}`, async ({browser}) => {
    const central = await browserAuthService(), context = await browser.newContext({locale});
    try {
      const {operation_id} = await central.control({action: 'observe_operation'});
      const page = await context.newPage();
      await page.goto(central.origin + '/dashboard/');
      await page.locator('input[type=password]').fill(central.token);
      await page.locator('input[type=checkbox]').check();
      await page.getByRole('button', {name: copy.connect, exact: true}).click();
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      await page.goto(central.origin + '/dashboard/#/op/' + operation_id);
      const denied = page.waitForResponse(response => response.url().endsWith(`/operations/${operation_id}/cancel`));
      await page.getByRole('button', {name: copy.cancel, exact: true}).click();
      expect((await denied).status()).toBe(403);
      await expect(page.getByText(copy.error_code_FORBIDDEN, {exact: true})).toBeVisible();
      await expect.soft(page.locator('#live')).toHaveText(copy.ui_connected);
      expect.soft(await page.evaluate(() => [localStorage, sessionStorage].flatMap(store => Object.keys(store))
        .filter(key => key.startsWith('batc.credential-refused.')))).toEqual([]);
      const poll = await page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/events', {timeout: 5000});
      expect(poll.status()).toBe(200);
      expect(poll.request().headers().authorization === `Bearer ${central.token}`).toBe(true);
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
    } finally {await context.close(); await central.close();}
  });
}
