import {mkdir} from 'node:fs/promises';
import {test, expect, type Page, type Locator} from '@playwright/test';
import {reviewFixture} from './requirements-review-fixture.ts';

async function shot(page: Page, state: string, locale: string, width: number) {
  const directory = process.env.BATC_OVERVIEW_READER_SHOTS;
  if (!directory) return;
  await mkdir(directory, {recursive: true});
  await page.screenshot({path: `${directory}/fix5-overview-${state}-${locale}-${width}.png`, animations: 'disabled', fullPage: width >= 1000});
}

async function readableGrids(content: Locator) {
  const width = (await content.boundingBox())!.width;
  for (const [selector, columns] of [
    ['.pm-overview-charter-grid', width > 1100 ? 3 : width > 540 ? 2 : 1],
    ['.pm-overview-columns', width > 1100 ? 2 : 1],
    ['.pm-overview-side', width > 1100 || width <= 540 ? 1 : 2],
    ['.pm-overview-status', width > 800 ? 4 : width > 540 ? 2 : 1],
  ] as const) {
    expect(await content.locator(selector).evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ').length), selector).toBe(columns);
  }
  const cards = content.locator('.pm-overview-charter-grid > section');
  await expect(cards).toHaveCount(6);
  for (const card of await cards.all()) {
    const bounds = (await card.boundingBox())!;
    expect(bounds.width).toBeGreaterThan(240);
    expect(await card.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
  }
  const range = content.locator('[data-overview-milestone="recovery_review"] [data-overview-date-range]');
  // Both formatted calendar dates and the delay are readable on one line here.
  await expect(range.locator('time')).toHaveCount(2);
  const lines = await range.locator(':scope > span').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().y));
  expect(lines[0]).toBe(lines[1]);
}

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 1280, 1000, 390]) {
  test(`Overview cards follow the available column beside Full plan ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage(), f = reviewFixture(locale);
    try {
      await f.mount(page, 'overview');
      const content = page.locator('[data-overview-content]');
      await expect(content.locator('[data-charter-section="definition"]')).toContainText(f.copy.goal);
      await readableGrids(content);
      if (width >= 1000) {
        await page.evaluate(() => scrollTo(0, 0));
        await shot(page, 'closed', locale, width);
      }
      await page.locator('[data-charter-plan]').click();
      const reader = page.locator('[data-overview-plan]');
      await expect(reader).toHaveAttribute('role', width >= 1200 ? 'complementary' : 'dialog');
      await expect(reader).toContainText(f.copy.planTitle);
      await readableGrids(content);
      if (width >= 1200) {
        const main = (await content.boundingBox())!, panel = (await reader.boundingBox())!;
        expect(main.x + main.width).toBeLessThan(panel.x);
        expect(panel.x + panel.width).toBe(width);
        expect(await content.locator('.pm-overview-charter-grid').evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ').length)).toBe(2);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.evaluate(() => scrollTo(0, 0));
      await shot(page, 'open', locale, width);
      await page.keyboard.press('Escape');
      await expect(reader).toHaveCount(0);
      await expect(page.locator('[data-charter-plan]')).toBeFocused();
      await readableGrids(content);
      expect(f.writes).toEqual([]);
    } finally {await context.close();}
  });

  test(`a narrow milestone date range breaks only before the arrow ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage(), f = reviewFixture(locale);
    try {
      await f.mount(page, 'overview');
      const range = page.locator('[data-overview-milestone="recovery_review"] [data-overview-date-range]');
      await expect(range.locator('time')).toHaveText(locale === 'en' ? ['Oct 16', 'Oct 19'] : ['10月16日', '10月19日']);
      await expect(range).toContainText(locale === 'en' ? '+3d' : '+3 天');
      // Exercise a card too narrow for the entire range without changing the viewport.
      await range.evaluate(node => {
        const groupWidth = Math.max(...[...node.children].map(child => child.getBoundingClientRect().width));
        node.style.maxWidth = `${Math.ceil(groupWidth) + 4}px`;
      });
      const lines = await range.locator(':scope > span').evaluateAll(nodes => nodes.map(node => {
        const text = document.createRange(); text.selectNodeContents(node);
        return {y: node.getBoundingClientRect().y, lines: [...text.getClientRects()].map(rect => rect.y)};
      }));
      expect(lines[1].y).toBeGreaterThan(lines[0].y);
      for (const line of lines) expect(new Set(line.lines).size).toBe(1);
      expect(await range.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
      expect(f.writes).toEqual([]);
    } finally {await context.close();}
  });
}
