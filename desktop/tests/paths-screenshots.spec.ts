import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {pathsFixture} from './paths-fixture.ts';
import {go, consoleNoise} from './session-fixture.ts';
import {workPathsStrings} from '../src/work-paths-strings.js';
for (const locale of ['en', 'zh-TW'] as const) for (const width of (process.env.FR4_WIDTHS || '1440,390').split(',').map(Number)) {
  test(`paths screenshots ${locale} ${width}`, async ({browser}) => {
    const fix1 = process.env.FR4_FIX1_SHOTS;
    test.skip(!process.env.FR4_SHOTS && !fix1, 'Explicit before/after capture');
    const s = await pathsFixture(browser, {locale, width, height: width === 390 ? 844 : 900});
    const {page, ids} = s;
    const out = (fix1 || process.env.FR4_SHOTS)!;
    mkdirSync(out, {recursive: true});
    const shot = async (name: string) => {await page.screenshot({path: `${out}/${name}-${locale}-${width}.png`, fullPage: true});};
    if (fix1) {
      await page.goto('/dashboard/#/settings?section=agents');
      await expect(page.locator('[data-agents-heading]')).toBeFocused();
      await expect(page.locator('[data-pm-eligibility]')).toHaveText(workPathsStrings[locale].agents_ready);
      await shot('agents');
      let holdNext = false;
      let releaseCheck!: () => void;
      const heldCheck = new Promise<void>(resolve => { releaseCheck = resolve; });
      s.overrides.unshift(async (_input, url) => {
        if (url.pathname !== '/pm/installation-capabilities' || !holdNext) return;
        await heldCheck;
        return undefined;
      });
      holdNext = true;
      await page.locator('[data-pm-eligibility-check]').click();
      await expect(page.locator('[data-pm-eligibility-check]')).toHaveAttribute('aria-disabled', 'true');
      await shot('agents-checking');
      releaseCheck();
      await expect(page.locator('[data-pm-eligibility]')).toHaveText(workPathsStrings[locale].agents_ready);
      await go(page, '#/sessions');
      await expect(page.locator('.session-entry').first()).toBeVisible();
      await expect(page.locator('[data-session-paths]')).toBeHidden();
      await shot('sessions-populated');
      s.overrides.unshift((_input, url) => url.pathname === '/sessions' ? {status: 200, data: {sessions: [], next_cursor: null}} : undefined);
      await page.reload();
      await expect(page.locator('[data-session-paths]')).toBeVisible();
      await expect(page.locator('.session-entry')).toHaveCount(0);
      await shot('sessions-empty');
    } else {
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
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    expect(s.writes).toHaveLength(0);
    expect(s.errors.filter(error => !consoleNoise(error))).toEqual([]);
    await s.context.close();
  });
}
