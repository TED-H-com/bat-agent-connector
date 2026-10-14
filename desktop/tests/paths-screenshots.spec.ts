import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {pathsFixture} from './paths-fixture.ts';
import {go} from './session-fixture.ts';
for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) {
  test(`paths screenshots ${locale} ${width}`, async ({browser}) => {
    test.skip(!process.env.FR4_SHOTS, 'Explicit before/after capture');
    const s = await pathsFixture(browser, {locale, width, height: width === 390 ? 844 : 900});
    const {page, ids} = s;
    const out = process.env.FR4_SHOTS!;
    mkdirSync(out, {recursive: true});
    const shot = async (name: string) => {await page.screenshot({path: `${out}/${name}-${locale}-${width}.png`, fullPage: true});};
    await page.goto(`/dashboard/#/session/${ids.host}/${ids.sessions.manual}`);
    await expect(page.locator('[data-session-readonly]')).toBeVisible();
    await shot('manual');
    s.state.checkpoints = false;
    await page.reload();
    await expect(page.locator('[data-session-readonly]')).toBeVisible();
    await shot('checkpoint-unavailable');
    await go(page, `#/start?host=${encodeURIComponent(ids.host)}`);
    await expect(page.locator('[data-session-start]')).toBeVisible();
    await shot('start');
    await go(page, '#/settings?section=agents');
    await expect(page.locator('[data-settings-panel=connection]')).toBeAttached();
    if (await page.locator('[data-settings-nav=agents]').count()) await expect(page.locator('[data-settings-panel=agents]')).toBeVisible();
    await shot('agents');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await s.context.close();
  });
}
