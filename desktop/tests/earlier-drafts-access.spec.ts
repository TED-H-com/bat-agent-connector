import {test, expect} from '@playwright/test';
import {setup} from './managed-storage-fixture';

for (const locale of ['en-US', 'zh-TW']) test(`access changes withdraw cached recovery until verification (${locale})`, async ({browser}) => {
  const context = await browser.newContext({locale}), page = await context.newPage();
  await setup(page, {legacy: true, legacyCursors: [1, 2]});
  await page.evaluate(() => {location.hash = '#/settings?section=about&drafts=earlier';});
  const slot = page.locator('[data-earlier-drafts-slot]');
  await expect(slot).toHaveAttribute('data-scan-state', 'complete');
  await expect(slot.locator('[data-earlier-draft]')).toHaveCount(4);
  await page.evaluate(() => {
    const bridge = (window as any).__TAURI_INTERNALS__, invoke = bridge.invoke;
    let reset = true, verify = false;
    bridge.invoke = async (command: string, args: any) => {
      if (command === 'connector_request' && args.input.path.startsWith('/events') && reset) {
        reset = false; verify = true;
        return {status: 409, data: {error: {code: 'EVENT_CURSOR_RESET', reason: 'access_changed', resnapshot: true, preserve_drafts: true}}};
      }
      if (command === 'connector_request' && args.input.path === '/bootstrap' && verify) {
        verify = false; (window as any).__verificationWaiting = true;
        await new Promise(resolve => {(window as any).__verifyAgain = resolve;});
      }
      return invoke(command, args);
    };
  });
  await expect.poll(() => page.evaluate(() => (window as any).__verificationWaiting)).toBe(true);
  await page.evaluate(() => {location.hash = '#/settings?section=about';});
  await expect(slot).toHaveAttribute('data-scan-state', 'waiting');
  await expect(page.locator('[data-earlier-drafts], [data-earlier-drafts-notice]')).toHaveCount(0);
  await page.evaluate(() => (window as any).__verifyAgain());
  await expect(slot).toHaveAttribute('data-scan-state', 'complete');
  await slot.locator('summary').click();
  await expect(slot.locator('[data-earlier-draft]')).toHaveCount(4);
  expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
  await context.close();
});
