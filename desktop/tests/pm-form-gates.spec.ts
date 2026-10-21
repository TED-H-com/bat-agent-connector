import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {STRINGS} from '../src/i18n.js';
import {demoFixture} from './demo/index.ts';

for (const native of [false, true]) for (const locale of ['en', 'zh-TW'] as const) {
  test(`PM source state explains paused forms without repeated notices (${native ? 'IPC' : 'HTTP'} ${locale})`, async ({page}) => {
    const fixture = demoFixture({locale});
    let held = false, release = () => {};
    const pending = new Promise<void>(resolve => {release = resolve;});
    const posts: string[] = [];
    const dispatch = async (input: any) => {
      if (input.method !== 'GET') posts.push(input.path);
      if (input.path === '/pm/workspaces') {
        held = true;
        await pending;
        return {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'PM read unavailable'}}};
      }
      return fixture.dispatch(input);
    };
    await page.setViewportSize({width: native ? 390 : 1440, height: 844});
    await page.addInitScript(language => Object.defineProperty(navigator, 'language', {value: language}), locale);
    await mountConversation(page, native, dispatch);
    await page.goto(`/dashboard/?workbench=1#/pm/${fixture.ids.workspaceId}/board`);
    const region = page.locator('.pm-main');
    try {
      await expect.poll(() => held).toBe(true);
      await expect(region.locator('[data-pm-action]').first()).toBeAttached();
      // Inspect mounted notices even in collapsed disclosures: none may flash
      // while the initial authority reads are still pending.
      await expect(region.locator('[data-pm-client-gate]:not([hidden])')).toHaveCount(0);
      await expect(page.locator('#live')).toHaveAttribute('title', new RegExp(STRINGS[locale].sync_waiting));
    } finally {release();}
    await expect(region.locator('[data-error-summary]')).toHaveCount(1);
    await expect(region.locator('[data-error-summary]')).toHaveText(locale === 'en'
      ? 'Could not confirm the current version' : '目前無法確認最新版本');
    await expect(region.locator('[data-api-error]')).toHaveCount(1);
    await expect(region.locator('[data-pm-client-gate]:not([hidden])')).toHaveCount(0);
    expect(await region.locator('[data-pm-client-gate]').allTextContents()).toEqual(expect.arrayContaining(['']));
    expect((await region.locator('[data-pm-client-gate]').allTextContents()).every(text => !text)).toBe(true);
    expect(posts).toEqual([]);
  });
}
