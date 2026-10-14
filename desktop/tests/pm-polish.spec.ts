import {test, expect, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {reviewFixture} from './requirements-review-fixture.ts';

async function shot(page: Page, name: string) {
  const folder = process.env.BATC_POLISH_SHOTS;
  if (!folder) return;
  await mkdir(folder, {recursive: true});
  await page.screenshot({path: `${folder}/${name}.png`, fullPage: true});
}
async function fits(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}
for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) {
  test(`Open decisions action has row spacing ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
    try {
      const page = await context.newPage(), f = reviewFixture(locale); await f.mount(page, 'overview');
      const row = page.locator('[data-overview-open-decision]'); await expect(row).toBeVisible();
      const link = row.locator('.pm-overview-link'), action = row.locator('.ui-button');
      const a = (await link.boundingBox())!, b = (await action.boundingBox())!;
      expect(Math.max(b.x - a.x - a.width, b.y - a.y - a.height)).toBeGreaterThanOrEqual(3);
      await fits(page); await shot(page, `overview-${locale}-${width}`); expect(f.writes).toEqual([]);
    } finally {await context.close();}
  });
  test(`Schedule narrow zoom keeps dates and tracks readable ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
    try {
      const page = await context.newPage(), f = reviewFixture(locale); await f.mount(page, 'schedule');
      const region = page.locator('[data-pm-schedule]'); await expect(region.locator('.schedule-chart')).toBeVisible();
      if (width === 390) {
        await expect(region.getByRole('radio', {name: locale === 'en' ? 'Day' : '日', exact: true})).toHaveCount(0);
        await expect(region.locator('[data-schedule-axis-caption]')).toBeVisible();
        for (const track of await region.locator('.schedule-track').all()) expect(await track.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
      }
      await fits(page); await shot(page, `schedule-week-${locale}-${width}`);
      if (width === 1440) {
        await region.getByRole('radio', {name: locale === 'en' ? 'Day' : '日', exact: true}).click();
        await shot(page, `schedule-day-${locale}-${width}`);
        await page.setViewportSize({width: 390, height: 844});
        await expect(region.getByRole('radio', {name: locale === 'en' ? 'Week' : '週', exact: true})).toHaveAttribute('aria-checked', 'true');
        await fits(page); await shot(page, `schedule-saved-day-${locale}-390`);
      }
      expect(f.writes).toEqual([]);
    } finally {await context.close();}
  });
}
for (const locale of ['en', 'zh-TW']) for (const width of [1440, 1000, 390]) test(`Issues toolbar and whole type labels fit beside inspector ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
  try {
    const page = await context.newPage(), f = reviewFixture(locale); await f.mount(page, 'issues');
    const panel = page.locator('[data-pm-register]'); await expect(panel.locator('[data-row-key]')).toHaveCount(3);
    await shot(page, `issues-${locale}-${width}`);
    await panel.locator('[data-row-key]').first().click(); await expect(page.locator('[data-reg-inspector]')).toBeVisible();
    await shot(page, `issues-inspector-${locale}-${width}`);
    await fits(page);
    if (width >= 1000) {
      const toolbar = panel.locator('.reg-toolbar'); expect(await toolbar.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
      for (const type of await panel.locator('td[data-column=type] .reg-type').all()) expect(await type.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
      await expect(panel.locator('td[data-column=related]').first()).toBeHidden();
    }
    await page.keyboard.press('Escape'); await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
    expect(f.writes).toEqual([]);
  } finally {await context.close();}
});
for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) test(`Untitled issue mapping has one calm name ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
  try {
    const page = await context.newPage(), f = reviewFixture(locale);
    await f.mount(page, 'issues'); const panel = page.locator('[data-pm-register]');
    await panel.getByRole('button', {name: locale === 'en' ? 'Load more' : '載入更多', exact: true}).click();
    const row = panel.locator(`[data-row-key="issue_mapping.${f.ids.issueMapping}"]`);
    await expect(row).toBeVisible(); await shot(page, `mapping-${locale}-${width}`);
    await expect(row).toContainText(locale === 'en' ? 'Untitled issue' : '未命名議題');
    await row.click(); const inspector = page.locator('[data-reg-inspector]');
    await expect(inspector.getByRole('heading', {level: 2})).toHaveText(locale === 'en' ? 'Untitled issue' : '未命名議題');
    await inspector.locator('details').last().locator('summary').click(); await expect(inspector).toContainText(f.ids.issueMapping);
    await fits(page); await shot(page, `mapping-inspector-${locale}-${width}`);
    expect(f.writes).toEqual([]);
  } finally {await context.close();}
});
