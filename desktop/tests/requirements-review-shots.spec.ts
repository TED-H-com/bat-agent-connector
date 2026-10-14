import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {reviewFixture} from './requirements-review-fixture.ts';
import {openPmSection} from './pm-navigation.ts';

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 1280, 1000, 768, 390]) {
  for (const theme of width === 1440 ? ['light', 'dark'] : ['light']) test(`Requirements and Board review states ${locale} ${width} ${theme}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, colorScheme: theme as 'light' | 'dark', reducedMotion: 'reduce', viewport: {width, height: 900}});
    const page = await context.newPage(), f = reviewFixture(locale); let mode = 'default';
    f.overrides.push((url, r) => {
      if (mode === 'partial' && url.pathname.endsWith('/deliveries') && url.pathname.includes(f.ids.work.email)) {
        r.data.deliveries = r.data.deliveries.filter((row: any) => row.target.pull_number === 91); r.data.next_after = 2;
      }
      if (mode === 'unknown' && url.pathname.endsWith('/quality-reviews')) {r.data.reviews = []; r.data.visibility_filtered = true;}
      if (mode === 'empty' && url.pathname.endsWith('/kanban')) {r.data.cards = []; r.data.next_cursor = null; r.data.wip = {count: 0, unknown: 0, cards: 0, scope: 'returned_page'};}
      return r;
    });
    const out = process.env.BATC_REQUIREMENTS_SHOTS;
    const shot = async (name: string) => {
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await page.locator('#main').innerText()).not.toMatch(/\b(?:undefined|null|NaN|wv_\w+)\b/);
      if (out) {await mkdir(out, {recursive: true}); await page.screenshot({path: `${out}/${name}-${locale}-${width}-${theme}.png`, fullPage: false, animations: 'disabled'});}
    };
    await f.mount(page, 'requirements'); await expect(page.locator('[data-rtm-requirement]')).toHaveCount(8);
    await expect(page.locator('[data-pm-view=requirements]')).toHaveAttribute('aria-current', 'page');
    await shot('rtm');
    await page.evaluate(hash => {location.hash = hash;}, `#/pm/${f.ids.workspaceId}/requirements?select=${f.ids.work.email}`);
    await expect(page.locator('[data-work-section=delivery] [data-rtm-pr]')).toHaveCount(2); await shot('rtm-work-inspector');
    await page.keyboard.press('Escape');
    await page.evaluate(hash => {location.hash = hash;}, `#/pm/${f.ids.workspaceId}/requirements?select=${f.ids.requirements.email}`);
    await expect(page.locator('[data-work-section=criteria]')).toBeVisible(); await shot('requirement-inspector');
    mode = 'partial'; f.emit();
    await expect(page.locator('[data-work-section=delivery] [data-rtm-pr]')).toHaveCount(1);
    await expect(page.locator('[data-work-section=delivery] > [data-status=partial]')).toBeVisible(); await shot('partial-delivery');
    mode = 'unknown'; f.emit();
    await expect(page.locator('[data-work-section=criteria] > [data-status=unknown]')).toBeVisible(); await shot('unknown-acceptance');
    await page.keyboard.press('Escape'); await openPmSection(page, 'board');
    await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(12);
    await page.locator(`[data-board-open="${f.ids.work.audit}"]`).click();
    await expect(page.locator('[data-work-section=status]')).toBeVisible(); await shot('board-inspector');
    await page.keyboard.press('Escape'); mode = 'empty'; f.emit();
    await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(0); await expect(page.locator('[data-pm-kanban-column]:visible')).toHaveCount(0); await shot('empty-board');
    expect(f.writes).toEqual([]); await context.close();
  });
}
