import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {STRINGS} from '../src/i18n.js';

for (const native of [false, true]) for (const locale of ['en', 'zh-TW']) for (const width of [390, 1440]) {
  test(`everyday navigation, keyboard access and complete management ${native ? 'native' : 'web'} ${locale} ${width}`, async ({browser, baseURL}, testInfo) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: 900}});
    const page = await context.newPage(), f = demoFixture({locale}), writes: string[] = [];
    await mountConversation(page, native, async input => {
      if (input.method !== 'GET') writes.push(input.path);
      return f.dispatch(input);
    });
    await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/overview`);
    const rail = page.locator('.shell-project-nav'), group = rail.locator('[data-project-management]');
    await expect(group).toBeAttached();
    if (width < 1000) await page.locator('#workspace-menu').click();
    await expect(group).not.toHaveAttribute('open', '');
    expect(await rail.locator('[data-pm-view]:visible').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-pm-view'))))
      .toEqual(['overview', 'discussions', 'board', 'issues']);
    await expect(rail).toContainText(locale === 'en' ? 'Goals, progress and next steps' : '看目標、進度與下一步');
    await expect(rail.locator('[data-pm-view=requirements]')).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path: testInfo.outputPath('everyday-navigation.png')});
    const summary = group.locator('summary');
    await summary.focus(); await page.keyboard.press('Enter');
    await expect(rail.locator('[data-pm-view]:visible')).toHaveCount(8);
    for (const button of await rail.locator('[data-pm-view]').all()) {
      const view = await button.getAttribute('data-pm-view');
      const badge = button.locator('.shell-count');
      const count = await badge.count() ? ` ${await badge.getAttribute('aria-label')}` : '';
      await expect(button).toHaveAccessibleName(STRINGS[locale]['ui_' + view] + count);
      await expect(button).toHaveAccessibleDescription(STRINGS[locale]['ui_nav_' + view]);
    }
    await page.keyboard.press('Tab');
    await expect(rail.locator('[data-pm-view=requirements]')).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('[data-pm-region=requirements]')).toBeVisible();
    if (width < 1000) {
      await expect(page.locator('.workspace-nav')).toBeHidden();
      await page.locator('#workspace-menu').click();
    }
    await expect(group).toHaveAttribute('open', '');
    await summary.click();
    await expect(rail.locator('[data-pm-view=requirements]')).toBeHidden();
    // Folding the navigation cannot hide or discard the current page.
    await expect(page.locator('[data-pm-region=requirements]')).toBeVisible();
    await rail.locator('[data-pm-view=overview]').click();
    await expect(page.locator('[data-pm-region=overview]')).toBeVisible();
    if (width < 1000) await page.locator('#workspace-menu').click();
    await expect(group).not.toHaveAttribute('open', '');
    expect(writes).toEqual([]);
    await context.close();
  });
}

for (const native of [false, true]) test(`bookmarked management views remain visible after reload and Back ${native ? 'native' : 'web'}`, async ({page}) => {
  const f = demoFixture();
  await mountConversation(page, native, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/schedule`);
  const group = page.locator('[data-project-management]');
  await expect(group).toHaveAttribute('open', '');
  await expect(group.locator('[data-pm-view=schedule]')).toHaveAttribute('aria-current', 'page');
  await page.reload();
  await expect(group.locator('[data-pm-view=schedule]')).toBeVisible();
  await page.locator('[data-pm-view=overview]').click();
  await expect(page.locator('[data-pm-region=overview]')).toBeVisible();
  await group.locator('summary').click();
  await page.goBack();
  await expect(group).toHaveAttribute('open', '');
  await expect(group.locator('[data-pm-view=schedule]')).toBeVisible();
  await expect(page.locator('[data-pm-region=schedule]')).toBeVisible();
});

test('background workspace updates preserve a folded group and keyboard focus', async ({page}) => {
  const f = demoFixture();
  let cursor = 0, name = 'Initial project name';
  await mountConversation(page, false, async input => {
    const url = new URL(input.path, 'https://fixture.example');
    if (url.pathname === '/events') return {status: 200, data: {
      events: Number(url.searchParams.get('after')) < cursor ? [{seq: cursor,
        resource_type: 'pm_workspace', resource_id: f.ids.workspaceId, kind: 'pm_workspace.updated'}] : [],
      next_cursor: cursor, head_cursor: cursor, has_more: false, sync: {checkpoint: {cursor, token: `navigation-${cursor}`}},
    }};
    const result = await f.dispatch(input);
    if (result.data.workspace?.workspace_id === f.ids.workspaceId) result.data.workspace.name = name;
    return result;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/requirements`);
  const group = page.locator('[data-project-management]'), summary = group.locator('summary');
  await expect(group).toHaveAttribute('open', '');
  await summary.click(); await summary.focus();
  name = 'Updated project name'; cursor++;
  await expect(page.locator('.shell-workspace-name').first()).toHaveText(name);
  await expect(group).not.toHaveAttribute('open', '');
  await expect(summary).toBeFocused();
  await expect(page.locator('[data-pm-region=requirements]')).toBeVisible();
});
