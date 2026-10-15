import {test, expect} from '@playwright/test';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';
import {mount, project, workItem, projectId} from './observed-resource-fixture.ts';
import {orchestrationFixture} from './orchestration-fixture.ts';

for (const locale of ['en', 'zh-TW'] as const) for (const width of [390, 768, 1440]) {
  test(`shared page headers keep icon actions beside titles ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: 900}}), page = await context.newPage();
    const f = demoFixture({locale, discussions: true}), writes: string[] = [];
    await mountConversation(page, false, async input => {if (input.method !== 'GET') writes.push(input.path); return f.dispatch(input);});
    for (const route of ['settings', 'sessions', ...['overview', 'requirements', 'issues', 'discussions', 'agents'].map(view => `pm/${f.ids.workspaceId}/${view}`)]) {
      await page.goto('/dashboard/#/' + route);
      const header = page.locator('#main .ui-page-header').first(); await expect(header.locator('h1')).toBeVisible();
      await expect(header.locator('h1')).toHaveAttribute('title', await header.locator('h1').textContent() || '');
      const iconsOnly = await header.evaluate(node => !node.querySelector('.ui-header-actions > :not(.ui-icon-button, .ui-menu, [hidden])'));
      if (iconsOnly) for (const action of await header.locator('.ui-header-actions > .ui-icon-button:visible, .ui-header-actions > .ui-menu > button:visible').all()) {
        const title = await header.locator('h1').boundingBox(), box = await action.boundingBox();
        expect(box!.y).toBeLessThan(title!.y + title!.height);
        expect(box!.x).toBeGreaterThanOrEqual(title!.x + title!.width);
        if (width < 800) expect(box!.height).toBeGreaterThanOrEqual(44);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    }
    expect(writes).toEqual([]); await context.close();
  });
}

for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 1440]) {
  test(`project rows keep titles above facts and touch actions ${locale} ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 900});
    const state = {gone: false, seq: 0, events: [], manage: true, posts: [],
      project: project({counts: {total: 12, done: 3, doing: 5, awaiting_approval: 2}}),
      workItems: [workItem({steps: [{step_id: 'one', title: 'Review', done: false}]})]};
    await mount(page, locale, state);
    for (const route of ['projects', 'project/' + projectId]) {
      await page.goto('/dashboard/#/' + route); const row = page.locator('#main .record-row').first();
      await expect(row).toBeVisible();
      const title = await row.locator('.grow').boundingBox(), meta = row.locator('.record-row-meta');
      const actions = row.locator('.tree-actions'), actionBox = await actions.boundingBox();
      if (width === 390) {
        const metaBox = await meta.boundingBox();
        expect(metaBox!.y).toBeGreaterThanOrEqual(title!.y + title!.height);
        expect(actionBox!.y).toBeGreaterThanOrEqual(metaBox!.y);
        const rowBox = await row.boundingBox();
        expect(Math.abs(actionBox!.x + actionBox!.width - rowBox!.x - rowBox!.width)).toBeLessThanOrEqual(1);
        for (const button of await actions.getByRole('button').all()) expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
        if (route === 'projects') await expect(meta.locator('.record-row-summary')).toContainText('3/12');
        else await expect(meta.locator('.record-row-status')).toBeVisible();
      } else expect(actionBox!.y).toBeLessThan(title!.y + title!.height);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    }
    const card = page.locator('[data-project-work-list]');
    expect(await card.locator('h2').evaluate(node => getComputedStyle(node).marginTop)).toBe('0px');
    expect(state.posts).toEqual([]);
    state.project.counts = {partial: true} as any;
    await page.goto('/dashboard/#/projects');
    await expect(page.locator('.record-row-summary')).toHaveText('');
    await expect(page.locator('#main')).not.toContainText(/undefined|NaN/);
  });
  test(`orchestration checkboxes align with the first label line ${locale} ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 900});
    await page.addInitScript(value => Object.defineProperty(navigator, 'language', {value}), locale);
    const state = await orchestrationFixture(page, false);
    for (const tab of ['relay', 'planner', 'items', 'failover']) {
      await page.goto('/dashboard/#/orchestrate/' + tab); await expect(page.locator('.orch-check').first()).toBeVisible();
      for (const label of await page.locator('.orch-check').all()) {
        const offset = await label.evaluate(node => {
          const input = node.querySelector('input')!.getBoundingClientRect();
          const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT); let text: Node | null;
          while ((text = walker.nextNode())) if (text.textContent?.trim()) {
            const range = document.createRange(); range.selectNodeContents(text);
            return input.y + input.height / 2 - (range.getClientRects()[0].y + range.getClientRects()[0].height / 2);
          }
          return Infinity;
        });
        expect(Math.abs(offset)).toBeLessThanOrEqual(4);
        if (width === 390) expect((await label.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      }
    }
    expect(state.posts).toEqual([]);
  });
}
