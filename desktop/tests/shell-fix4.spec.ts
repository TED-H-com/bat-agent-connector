import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';
import {openThreadTab} from './pm-navigation.ts';

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 1000, 390]) test(`Discussions keeps its panel column and one scroller ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: 900}}), page = await context.newPage();
  const f = demoFixture({locale, discussions: true});
  await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  const header = page.locator('.pm-main > .ui-page-header'), before = await header.boundingBox();
  await openThreadTab(page, 'plan');
  const panel = page.locator('.pd-panel-overlay'), body = panel.locator(':scope > .ui-inspector-body');
  await expect(panel).toHaveAttribute('role', width >= 1200 ? 'complementary' : 'dialog');
  if (width >= 1200) {
    const layout = await page.locator('.pm-thread-layout').boundingBox();
    const thread = await page.locator('.pm-discussion').boundingBox(), dock = await panel.boundingBox();
    const home = await page.locator('.pd-panel-home').boundingBox();
    expect(home!.width).toBe(300);
    expect(dock!.width).toBe(home!.width - 1); // The column owns a 1px border.
    expect(thread!.width).toBeCloseTo(layout!.width - 300, 0);
    expect(thread!.x + thread!.width).toBeLessThanOrEqual(dock!.x + 1);
    const after = await header.boundingBox();
    expect(after!.width).toBeCloseTo(before!.width, 0);
    await expect(panel).toHaveCSS('position', 'static');
    await expect(panel).toHaveCSS('overflow-y', 'visible');
    await expect(body).toHaveCSS('overflow-y', 'visible');
  } else {
    await expect(panel).toHaveAttribute('aria-modal', 'true');
    await expect(body).toHaveCSS('overflow-y', 'auto');
  }
  await page.locator('[data-pm-section=pm_brief_title] > details > summary').click();
  const sections = page.locator('[data-pm-brief-section]');
  await expect(sections).toHaveCount(5);
  const capture = async (suffix = '') => {
    if (!process.env.BATC_FIX4_SHOTS) return;
    await mkdir(process.env.BATC_FIX4_SHOTS, {recursive: true});
    await page.screenshot({path: `${process.env.BATC_FIX4_SHOTS}/fix4-discussions-panel${suffix}-${locale}-${width}.png`, animations: 'disabled'});
  };
  await capture();
  await sections.last().scrollIntoViewIfNeeded();
  await expect(sections.last().getByRole('heading')).toBeInViewport({ratio: 1});
  if (width >= 1200) {
    expect(await page.locator('.pd-panel-home').evaluate(node => node.scrollTop)).toBeGreaterThan(0);
    expect(await body.evaluate(node => node.scrollTop)).toBe(0);
    await expect(page.locator('[data-discussion-composer]')).toBeInViewport({ratio: 1});
  } else {
    expect(await body.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await capture('-scrolled');
  await page.keyboard.press('Escape');
  await expect(panel).toHaveCount(0);
  await expect(page.locator('[data-discussion-panel-toggle]')).toBeFocused();
  expect(f.discussion.writes).toEqual([]);
  await context.close();
});
