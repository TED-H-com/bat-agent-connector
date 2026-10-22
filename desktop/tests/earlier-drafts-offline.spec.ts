import {test, expect} from '@playwright/test';
import {setup, changeIdentity} from './managed-storage-fixture';

for (const locale of ['en-US', 'zh-TW']) test(`verified local text stays usable while the Connector is offline (${locale})`, async ({browser}) => {
  const context = await browser.newContext({locale, permissions: ['clipboard-read', 'clipboard-write']}), page = await context.newPage();
  await setup(page, {source: 'external'});
  await page.locator('textarea').first().fill('Offline draft one');
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('batc.sync.http')).length)).toBe(1);
  await changeIdentity(page, {endpoint: 'http://127.0.0.1:41002/'}); await page.reload();
  await page.locator('textarea').first().fill('Offline draft two');
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('batc.sync.http')).length)).toBe(2);
  await changeIdentity(page, {endpoint: 'http://127.0.0.1:41003/', source: 'managed'}); await page.reload();
  await page.evaluate(() => {location.hash = '#/settings?section=about&drafts=earlier';});
  await expect(page.locator('[data-earlier-drafts-slot]')).toHaveAttribute('data-scan-state', 'complete');
  await expect(page.locator('[data-earlier-draft]')).toHaveCount(2);
  await page.evaluate(() => {
    const bridge = (window as any).__TAURI_INTERNALS__, invoke = bridge.invoke;
    bridge.invoke = async (command: string, args: any) => command === 'connector_request' && args.input.path.startsWith('/events')
      ? {status: 503, data: {error: {code: 'SERVICE_UNAVAILABLE', message: 'Fixture service offline'}}} : invoke(command, args);
  });
  await expect(page.locator('#live')).not.toHaveAttribute('data-connection', 'connected');
  await page.evaluate(() => navigator.clipboard.writeText('Before copy'));
  const row = page.locator('[data-earlier-draft]').first(), text = await row.locator('textarea').inputValue();
  await row.getByRole('button', {name: locale === 'zh-TW' ? '複製文字' : 'Copy text', exact: true}).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(text);
  await row.getByRole('button', {name: locale === 'zh-TW' ? '隱藏' : 'Hide', exact: true}).click();
  await expect(page.locator('[data-earlier-draft]')).toHaveCount(1);
  expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
  await context.close();
});
