import {test, expect, type Page} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {DEMO_NOW} from './demo/project.ts';

// The same producer-shaped demo responses used by the reviewed gallery, mounted
// in the real Dashboard bundle. No copied view or screenshot-site dependency.
async function start(page: Page, locale: string, view: string) {
  await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
  await page.clock.setFixedTime(DEMO_NOW);
  const f = demoFixture({locale, discussions: true});
  await mountConversation(page, false, async input => {
    const result = await f.dispatch(input);
    // OperationService._decode exposes updated_at (epoch seconds), not an
    // operation-level finished_at. The discussion fixture omits this optional field.
    if (result.data?.operation) result.data.operation.updated_at = DEMO_NOW / 1000 - 120;
    return result;
  });
  await page.goto(view === 'session' ? `/dashboard/#/session/${f.ids.host}/${f.ids.sessions.manual}`
    : `/dashboard/#/pm/${f.ids.workspaceId}/${view}${view === 'discussions' ? '/' + f.ids.threadIdea : ''}`);
  if (view === 'session') await expect(page.locator('.conversation .msg')).not.toHaveCount(0);
  else await expect(page.locator(`[data-pm-region="${view}"]`)).toBeVisible();
  return f;
}
async function shot(page: Page, defect: string, locale: string, width: number) {
  const root = process.env.BATC_GALLERY_SHOTS;
  if (!root) return;
  mkdirSync(root, {recursive: true});
  await page.screenshot({path: `${root}/${defect}-${locale}-${width}.png`, fullPage: true});
}
for (const locale of ['en', 'zh-TW']) for (const width of [390, 768, 1440]) {
  const zh = locale === 'zh-TW';
  test.describe(`${locale} ${width}`, () => {
    test.use({viewport: {width, height: 960}});
    test('gallery relative age rolls over in the session view', async ({page}) => {
      await start(page, locale, 'session');
      await shot(page, 'relative-age', locale, width);
      await expect(page.locator('#installation-health .health-last-value')).toContainText(zh ? '1 天前' : '1 d ago');
    });
    test('gallery compact health announces checking once after the facets', async ({page}) => {
      await start(page, locale, 'session');
      await shot(page, 'compact-health', locale, width);
      const strip = page.locator('#installation-health');
      const text = await strip.innerText(), checking = zh ? '檢查中' : 'Checking';
      expect(text.split(checking).length - 1).toBe(1);
      expect(text.trim().endsWith(checking)).toBe(true);
      expect(await strip.ariaSnapshot()).toContain(checking);
      expect((await strip.ariaSnapshot()).split(checking).length - 1).toBe(1);
      await expect(strip.locator('.health-facets')).toContainText(zh ? '資料時效：已過期' : 'Freshness: Stale');
      await expect(strip.locator('.health-facets')).toContainText(zh ? '輸出活動：未知' : 'Output activity: Unknown');
    });
    test('gallery schedule full labels work with hover and keyboard', async ({page}) => {
      await start(page, locale, 'schedule');
      await expect(page.locator('.schedule-title').first()).toBeVisible();
      await shot(page, 'schedule', locale, width);
      const cells = page.locator('.schedule-chart [role=treeitem]');
      for (const cell of await cells.all()) {
        const title = cell.locator('.schedule-title');
        const full = await title.getAttribute('title');
        await cell.focus();
        await expect(page.getByRole('tooltip')).toHaveText(full!);
        await expect(page.getByRole('tooltip')).toBeVisible();
        await expect(cell).toHaveAccessibleName(await title.innerText());
        await cell.blur(); await title.hover();
        await expect(page.getByRole('tooltip')).toHaveText(full!);
      }
      if (width === 1440) expect(await page.locator('.schedule-title').evaluateAll(nodes =>
        nodes.filter(node => node.scrollWidth > node.clientWidth + 1).map(node => node.textContent))).toEqual([]);
      const geometry = await page.locator('[data-schedule-row]').evaluateAll(rows => rows.map(row => {
        const cell = row.querySelector('.schedule-tree-cell')!.getBoundingClientRect();
        const lane = row.querySelector('.schedule-track')!.getBoundingClientRect();
        return {cell: {top: cell.top, bottom: cell.bottom}, lane: {top: lane.top, bottom: lane.bottom}};
      }));
      for (const {cell, lane} of geometry) {
        if (width >= 800) {expect(lane.top).toBeCloseTo(cell.top, 0); expect(lane.bottom).toBeCloseTo(cell.bottom, 0);}
        else expect(lane.top).toBeGreaterThanOrEqual(cell.bottom - 1);
      }
    });
    test('gallery graph nodes are distinct and all reachable', async ({page}) => {
      await start(page, locale, 'dependencies');
      await page.getByRole('radio', {name: zh ? '關係圖' : 'Graph', exact: true}).click();
      const graph = page.locator('[data-dep-graph]'), nodes = graph.locator('[data-dep-node]');
      await expect(nodes.first()).toBeVisible();
      await shot(page, 'dependencies', locale, width);
      const names = await nodes.locator('.dep-node-name').allTextContents();
      expect(new Set(names).size).toBe(names.length);
      for (const node of await nodes.all()) {
        const name = await node.getAttribute('aria-label');
        await node.focus();
        await expect(page.locator('[data-dep-node-name]')).toHaveText(name!);
        const bounds = await node.boundingBox(), frame = await graph.boundingBox();
        expect(bounds!.x).toBeGreaterThanOrEqual(frame!.x);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(frame!.x + frame!.width + 1);
      }
      if (await graph.evaluate(node => node.scrollWidth > node.clientWidth + 1))
        await expect(page.locator('[data-dep-scroll-hint]')).toBeVisible();
      const before = await graph.locator('svg').getAttribute('width');
      await page.getByRole('button', {name: zh ? '放大' : 'Zoom in', exact: true}).click();
      expect(await graph.locator('svg').getAttribute('width')).not.toBe(before);
      await page.getByRole('button', {name: zh ? '縮小' : 'Zoom out', exact: true}).click();
      await expect(graph.locator('svg')).toHaveAttribute('width', before!);
    });
    test('gallery last PM run is distinct from current availability', async ({page}) => {
      await start(page, locale, 'discussions');
      await page.locator('[data-discussion-ask]').click();
      const feedback = page.locator('[data-discussion-working]');
      await expect(feedback).toContainText(zh ? '完成' : 'finished');
      await page.locator('.pd-composer').scrollIntoViewIfNeeded();
      await shot(page, 'discussions', locale, width);
      await expect(page.locator('[data-pm-proposal-availability]')).toContainText(zh ? 'PM 可用性未知' : 'PM availability is unknown');
      await expect(feedback).toContainText(zh ? '此討論上次的 PM 執行：' : "This discussion’s last PM run:");
      await expect(feedback).toContainText(zh ? '狀態更新於' : 'status updated');
      await expect(feedback.locator('time')).toHaveAttribute('datetime', new Date(DEMO_NOW - 120_000).toISOString());
    });
  });
}
