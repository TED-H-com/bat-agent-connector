import {test, expect, type Page} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {DEMO_NOW} from './demo/project.ts';
import {hexId} from './demo/project.ts';
import {dependencyFixtureIds as depIds} from './demo/dependencies.ts';
test.describe.configure({mode: 'parallel'});

// The same producer-shaped demo responses used by the reviewed gallery, mounted
// in the real Dashboard bundle. No copied view or screenshot-site dependency.
async function start(page: Page, locale: string, view: string, graphNames?: string[], projectDependencies?: (doc: any) => void) {
  await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
  await page.clock.setFixedTime(DEMO_NOW);
  const f = demoFixture({locale, discussions: true});
  await mountConversation(page, false, async input => {
    const result = await f.dispatch(input);
    if (graphNames && input.path.endsWith('/dependencies'))
      result.data.components.forEach((row: any, index: number) => {row.name = graphNames[index];});
    if (projectDependencies && input.path.endsWith('/dependencies')) projectDependencies(result.data);
    // OperationService._decode exposes updated_at (epoch seconds), not an
    // operation-level finished_at. The discussion fixture omits this optional field.
    if (result.data?.operation) {
      result.data.operation.created_at = DEMO_NOW / 1000 - 180;
      result.data.operation.updated_at = DEMO_NOW / 1000 - 120;
    }
    return result;
  });
  await page.goto(view === 'session' ? `/dashboard/#/session/${f.ids.host}/${f.ids.sessions.manual}`
    : `/dashboard/#/pm/${f.ids.workspaceId}/${view}${view === 'discussions' ? '/' + f.ids.threadIdea : ''}`);
  if (view === 'session') await expect(page.locator('.conversation .msg')).not.toHaveCount(0);
  else if (view === 'discussions') await expect(page.locator('[data-discussion-composer]')).toBeVisible();
  else await expect(page.locator(`[data-pm-region="${view}"]`)).toBeVisible();
  return f;
}
for (const locale of ['en', 'zh-TW']) for (const duplicate of [false, true]) {
  test(`graph keeps long producer names distinct ${locale} duplicate=${duplicate}`, async ({page}) => {
    await page.setViewportSize({width: 390, height: 960});
    const prefix = locale === 'en' ? 'membership-service-with-a-long-common-prefix-' : '會員服務共用的完整長名稱前綴';
    const names = [prefix + 'api', prefix + (duplicate ? 'api' : 'web')];
    await start(page, locale, 'dependencies', names);
    await page.getByRole('radio', {name: locale === 'en' ? 'Graph' : '關係圖', exact: true}).click();
    const nodes = page.locator('[data-dep-node^="c:"]:not([data-dep-external=true])');
    await expect(nodes).toHaveCount(2);
    const labels = await nodes.locator('.dep-node-name').allTextContents();
    expect(new Set(labels).size).toBe(2);
    for (let index = 0; index < 2; index++) {
      expect(labels.some(label => label.startsWith(names[index]))).toBe(true);
      const node = nodes.nth(index); await node.focus();
      await expect(page.locator('[data-dep-node-name]')).toHaveText((await node.getAttribute('aria-label'))!);
      expect(await node.locator('tspan').count()).toBeGreaterThan(1);
    }
    names[0] = prefix + 'updated-api'; names[1] = prefix + 'updated-web';
    await page.locator('.pm-main > header').getByRole('button', {name: locale === 'en' ? 'Refresh' : '重新整理', exact: true}).click();
    await expect(nodes.first()).toHaveAttribute('aria-label', /updated-/);
    await expect(page.locator('[data-dep-node-name]')).toBeHidden();
  });
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
        const full = await cell.getAttribute('aria-label');
        await cell.focus();
        await expect(page.getByRole('tooltip')).toHaveText(full!);
        await expect(page.getByRole('tooltip')).toBeVisible();
        if (full?.includes(zh ? '管理員' : 'administrator')) await shot(page, 'schedule-focus', locale, width);
        await expect(cell).toHaveAccessibleName(await title.innerText());
        await cell.blur(); await title.hover();
        await expect(page.getByRole('tooltip')).toHaveText(full!);
      }
      if (width === 1440) expect(await page.locator('.schedule-title').evaluateAll(nodes =>
        nodes.filter(node => node.scrollWidth > node.clientWidth + 1).map(node => node.textContent))).toEqual([]);
      const geometry = await page.locator('[data-schedule-row]').evaluateAll(rows => rows.map(row => {
        const cell = row.querySelector('.schedule-tree-cell')!.getBoundingClientRect();
        const lane = row.querySelector('.schedule-track')!.getBoundingClientRect();
        return {hasDates: row.getAttribute('data-has-dates') === 'true', cell: {top: cell.top, bottom: cell.bottom}, lane: {top: lane.top, bottom: lane.bottom}};
      }));
      for (const {cell, lane, hasDates} of geometry) {
        if (width >= 800) {expect(lane.top).toBeCloseTo(cell.top, 0); expect(lane.bottom).toBeCloseTo(cell.bottom, 0);}
        else if (hasDates) expect(lane.top).toBeGreaterThanOrEqual(cell.bottom - 1);
        else expect(lane.bottom - lane.top).toBe(0);
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
      if (await graph.evaluate(node => node.scrollWidth > node.clientWidth + 1))
        await expect(page.locator('[data-dep-scroll-hint]')).toBeVisible();
      for (const node of await nodes.all()) {
        const name = await node.getAttribute('aria-label');
        await node.focus();
        await expect(page.locator('[data-dep-node-name]')).toHaveText(name!);
        const bounds = await node.boundingBox(), frame = await graph.boundingBox();
        expect(bounds!.x).toBeGreaterThanOrEqual(frame!.x);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(frame!.x + frame!.width + 1);
      }
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
      await expect(feedback).toContainText(zh ? '此討論上次的 PM 執行：' : "This discussion’s last PM run:");
      await expect(page.locator('.pd-composer [data-pm-availability=unknown]')).toContainText(zh ? 'PM 可用性未知' : 'PM availability is unknown');
      await expect(feedback).toContainText(zh ? '狀態更新於' : 'status updated');
      await expect(feedback.locator('time')).toHaveAttribute('datetime', new Date(DEMO_NOW - 120_000).toISOString());
    });
  });
}

for (const locale of ['en', 'zh-TW']) for (const width of [390, 768, 1440]) {
  const zh = locale === 'zh-TW';
  test.describe(`gallery r2 ${locale} ${width}`, () => {
    test.use({viewport: {width, height: 960}});
    test('schedule exposes only one full tooltip for hover and focus', async ({page}) => {
      await start(page, locale, 'schedule');
      const cell = page.locator('.schedule-tree-cell').filter({hasText: zh ? '管理員' : 'administrator'}).first();
      const full = await cell.getAttribute('aria-label');
      for (const activate of [() => cell.hover(), () => cell.focus()]) {
        await activate();
        await expect(page.getByRole('tooltip')).toHaveCount(1);
        await expect(page.getByRole('tooltip')).toBeVisible();
        await expect(page.getByRole('tooltip')).toHaveText(full!);
        await shot(page, 'schedule-tooltip', locale, width);
        await expect(page.locator('.schedule-tree-cell[title], .schedule-tree-cell [title]')).toHaveCount(0);
      }
      await page.locator('.schedule-viewport').dispatchEvent('scroll');
      await expect(page.getByRole('tooltip')).toHaveText(full!);
      await expect(page.getByRole('tooltip')).toBeVisible();
    });
    test('schedule pointer movement does not measure an already shown tooltip again', async ({page}) => {
      await start(page, locale, 'schedule');
      const cell = page.locator('.schedule-tree-cell').filter({hasText: zh ? '管理員' : 'administrator'}).first();
      await cell.focus(); await cell.hover();
      expect(await cell.evaluate(cell => {
        const original = cell.getBoundingClientRect.bind(cell);
        let reads = 0;
        cell.getBoundingClientRect = () => {reads++; return original();};
        for (let i = 0; i < 10; i++) cell.dispatchEvent(new MouseEvent('mousemove', {bubbles: true}));
        cell.getBoundingClientRect = original;
        return reads;
      })).toBe(0);
    });
    test('graph duplicate ordinals are stable within their name group in every view', async ({page}) => {
      let added = false;
      // Reuse real component/link rows and producer ID shapes. The added
      // unrelated component sorts before both same-name local components.
      await start(page, locale, 'dependencies', ['api', 'api'], doc => {
        if (added) {
          const componentId = hexId('pmcomp_', 0, 32);
          doc.components.push({...doc.components[0], component_id: componentId, name: 'unrelated', manifest_path: 'tools/pyproject.toml', verification_hints: []});
          doc.edges.push({...doc.edges[0], edge_id: hexId('pmde_', 99, 32), component_id: componentId});
          doc.counts.components++; doc.counts.edges++;
        }
      });
      const graphRadio = page.getByRole('radio', {name: zh ? '關係圖' : 'Graph', exact: true});
      const matrixRadio = page.getByRole('radio', {name: zh ? '矩陣' : 'Matrix', exact: true});
      for (const refresh of [false, true]) {
        if (refresh) {
          added = true;
          await page.locator('.pm-main > header').getByRole('button', {name: zh ? '重新整理' : 'Refresh', exact: true}).click();
        }
        await graphRadio.click();
        const nodes = page.locator('[data-dep-node]');
        await expect(nodes).toHaveCount(refresh ? 12 : 11);
        await shot(page, 'dependencies-duplicates', locale, width);
        for (const [index, id] of [depIds.api, depIds.web].entries()) {
          const node = nodes.locator(`:scope[data-dep-node="c:${id}"]`);
          const name = `api [${index + 1}]`;
          await expect(node.locator('.dep-node-name')).toHaveText(name);
          await expect(node.locator('title')).toHaveText(name);
          await expect(node).toHaveAccessibleName(name);
          await node.focus();
          await expect(page.locator('[data-dep-node-name]')).toHaveText(name);
        }
        await matrixRadio.click();
        for (const name of ['api [1]', 'api [2]']) {
          if (width <= 800) await expect(page.locator('.dep-group > h2').getByText(name, {exact: true})).toBeVisible();
          else await expect(page.locator('.dep-matrix .dep-name').getByText(name, {exact: true})).toBeVisible();
        }
      }
    });
    test('graph wraps fitting English segments at separators and retains the full name', async ({page}) => {
      const name = 'membership-service_gateway/worker.module adapter';
      await start(page, locale, 'dependencies', [name, '會員服務完整名稱仍然可以逐字換行']);
      await page.getByRole('radio', {name: zh ? '關係圖' : 'Graph', exact: true}).click();
      const node = page.locator(`[data-dep-node="c:${depIds.api}"]`);
      await expect(node).toBeVisible();
      await node.focus();
      await shot(page, 'dependencies-wrapping', locale, width);
      const lines = await node.locator('.dep-node-name tspan').allTextContents();
      expect(lines.join('')).toBe(name);
      for (const segment of name.split(/[\s\-_/\.]/)) expect(lines.some(line => line.includes(segment))).toBe(true);
      await expect(node).toHaveAccessibleName(name);
      await expect(node.locator('title')).toHaveText(name);
      await expect(page.locator('[data-dep-node-name]')).toHaveText(name);
    });
  });
}
