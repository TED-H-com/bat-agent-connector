import {test, expect} from '@playwright/test';
import {setup, changeIdentity} from './managed-storage-fixture';
import {holdRecoveryReset, pressRetainedRecoveryControls} from './earlier-drafts-reset-fixture.ts';

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

for (const locale of ['en-US', 'zh-TW']) for (const [reason, changed] of [
  ['principal_changed', 'principal'], ['server_changed', 'server'],
  ['identity_unavailable', 'unavailable'], ['checkpoint_invalid', 'unavailable'],
] as const) test(`identity reset ${reason} withdraws recovery even when bootstrap fails (${locale})`, async ({browser}) => {
  const context = await browser.newContext({locale, permissions: ['clipboard-read', 'clipboard-write']}), page = await context.newPage();
  await setup(page, {source: 'external'});
  await page.locator('textarea').first().fill('Earlier instructions one');
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('batc.sync.http')).length)).toBe(1);
  await changeIdentity(page, {endpoint: 'http://127.0.0.1:41002/'}); await page.reload();
  await page.locator('textarea').first().fill('Earlier instructions two');
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('batc.sync.http')).length)).toBe(2);
  await changeIdentity(page, {endpoint: 'http://127.0.0.1:41003/', source: 'managed'}); await page.reload();
  await page.evaluate(() => {location.hash = '#/settings?section=about&drafts=earlier';});
  await expect(page.locator('[data-earlier-drafts-slot]')).toHaveAttribute('data-scan-state', 'complete');
  await expect(page.locator('[data-earlier-draft]')).toHaveCount(2);
  await page.evaluate(() => navigator.clipboard.writeText('Before verification'));
  await holdRecoveryReset(page, reason, changed);
  await expect.poll(() => page.evaluate(() => (window as any).__recoveryReset.verificationWaiting)).toBe(true);
  await pressRetainedRecoveryControls(page);
  expect.soft(await page.evaluate(() => navigator.clipboard.readText())).toBe('Before verification');
  expect.soft(await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.earlier-drafts.')).sort()))
    .toEqual(await page.evaluate(() => (window as any).__recoveryReset.markers));
  await expect.soft(page.locator('[data-earlier-drafts], [data-earlier-drafts-notice]')).toHaveCount(0);
  await page.evaluate(() => (window as any).__recoveryReset.releaseVerification());
  await expect.poll(() => page.evaluate(() => (window as any).__recoveryReset.bootstrapReturned)).toBe(true);
  // A route read after the failed bootstrap must not revive stale recovery.
  await page.evaluate(() => {location.hash = '#/settings?section=about';});
  await expect.soft(page.locator('[data-earlier-drafts-slot]')).toHaveAttribute('data-scan-state', 'waiting');
  await pressRetainedRecoveryControls(page);
  expect.soft(await page.evaluate(() => navigator.clipboard.readText())).toBe('Before verification');
  expect.soft(await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.earlier-drafts.')).sort()))
    .toEqual(await page.evaluate(() => (window as any).__recoveryReset.markers));
  await expect.soft(page.locator('[data-earlier-drafts], [data-earlier-drafts-notice]')).toHaveCount(0);
  expect.soft(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
  await context.close();
});
