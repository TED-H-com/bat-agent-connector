import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdirSync} from 'node:fs';
import {expect, test} from '@playwright/test';
import {demoProject} from './demo/project.ts';
import {schedulePage} from './demo/schedule.ts';

test.beforeAll(async () => {
  await promisify(execFile)('npm', ['run', 'build:demo'], {cwd: new URL('..', import.meta.url)});
});

for (const lang of ['en', 'zh-TW']) for (const width of [1440, 768, 390]) {
  test(`public demo uses the product views without a backend ${lang} ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 960});
    const errors: string[] = [], unexpected: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (!new URL(request.url()).pathname.startsWith('/demo/')) unexpected.push(request.method() + ' ' + request.url());
    });
    // Deliberately do not install route mocks or a Tauri bridge: this tests the
    // standalone, deployable bundle and its no-network boundary.
    await page.goto(`/demo/?lang=${lang}`);
    await expect(page.locator('[data-pm-overview]')).toBeVisible();
    await expect(page.locator('#demo-bar')).toContainText(lang === 'en' ? 'Sample data' : '合成資料');
    const views = ['requirements', 'board', 'schedule', 'dependencies', 'issues', 'agents', 'discussions', 'overview'];
    for (const view of views) {
      const entry = page.locator(`[data-pm-view="${view}"]`);
      if (!await entry.isVisible()) await page.locator('#workspace-menu').click();
      await entry.click();
      await expect(page.locator(`[data-pm-region="${view}"]`)).toBeVisible();
      await expect(page.locator('#main [data-api-error]:visible')).toHaveCount(0);
      await expect(page.locator('#main')).not.toContainText(/placeholder|undefined|NaN/);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      if (view === 'requirements') {
        const ids = demoProject(lang).ids;
        const requirements = page.locator('[data-pm-rtm]');
        await requirements.locator(width < 800 ? `[data-rtm-requirement="${ids.requirements.email}"]` : `[data-rtm-toggle="${ids.requirements.email}"]`).click();
        await requirements.locator(`[data-rtm-select="${ids.work.email}"]`).click();
        await expect(requirements.locator('[data-rtm-pr]')).toHaveCount(2);
        await expect(requirements).toContainText('PR #87');
        await page.keyboard.press('Escape');
      }
      if (view === 'board') {
        const board = page.locator('[data-pm-kanban]');
        await expect(board.locator('[data-pm-kanban-work]')).toHaveCount(12);
        const search = board.getByRole('searchbox');
        await search.fill('this-work-does-not-exist');
        await expect(board.locator('[data-pm-kanban-work]:visible')).toHaveCount(0);
        await search.clear();
        await expect(board.locator('[data-pm-kanban-work]:visible')).toHaveCount(12);
      }
    }
    if (process.env.BATC_PUBLIC_DEMO_SHOTS) {
      mkdirSync(process.env.BATC_PUBLIC_DEMO_SHOTS, {recursive: true});
      await page.screenshot({path: `${process.env.BATC_PUBLIC_DEMO_SHOTS}/demo-${lang}-${width}.png`, fullPage: true});
    }
    await expect(page.locator('#demo-bar a[href*="prototype"]')).toHaveAttribute('href', '../prototype/v1.3/?lang=' + lang);
    expect(errors).toEqual([]);
    expect(unexpected).toEqual([]);
  });
}

test('direct view links, language and reset preserve the public-demo boundary', async ({page}) => {
  await page.goto('/demo/?lang=zh-TW&view=requirements');
  await expect(page.locator('[data-pm-region="requirements"]')).toBeVisible();
  await expect(page.getByRole('heading', {level: 1})).toHaveText('需求');
  await page.locator('#demo-bar').getByRole('link', {name: 'English', exact: true}).click();
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Requirements');
  await page.evaluate(() => localStorage.setItem('unrelated-preference', 'keep'));
  await page.locator('#demo-bar').getByRole('button', {name: 'Reset tour'}).click();
  await expect(page.locator('[data-pm-overview]')).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('unrelated-preference'))).toBe('keep');
});

test('sample schedule cursors work in browsers and retain distinct pages', () => {
  const project = demoProject('en');
  const first = schedulePage(project, new URL('https://example.invalid/schedule?limit=2'));
  expect(first.next_cursor).toBeTruthy();
  const next = schedulePage(project, new URL('https://example.invalid/schedule?limit=2&after=' + first.next_cursor));
  expect([...next.rows, ...next.unscheduled].map(row => row.id)).not.toEqual([...first.rows, ...first.unscheduled].map(row => row.id));
});

for (const lang of ['en', 'zh-TW']) for (const width of [1440, 768, 390]) test(`workbench preview retains all project views and Idea reading ${lang} ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  const project = demoProject(lang);
  const errors: string[] = [], writes: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {if (request.method() !== 'GET') writes.push(request.url());});
  await page.goto(`/demo/?lang=${lang}&workbench=1`);
  await expect(page.locator('[data-pm-overview]')).toBeVisible();
  const selector = page.locator('[data-workbench-management]');
  await expect(selector).toBeVisible();
  for (const view of ['requirements', 'board', 'schedule', 'dependencies', 'issues', 'agents', 'discussions', 'overview']) {
    await selector.selectOption(view);
    await expect(page.locator(`[data-pm-region="${view}"]`)).toBeVisible();
    await expect(page.locator('#main [data-api-error]:visible')).toHaveCount(0);
  }
  await page.goto(`/demo/?lang=${lang}&workbench=1#/pm/${project.ids.workspaceId}/discussions/${project.ids.threadIdea}`);
  await expect(page.locator('.pd-thread-header')).toBeVisible();
  await expect(page.locator('.pd-panel-home')).toBeHidden();
  expect(errors).toEqual([]); expect(writes).toEqual([]);
});

test('global pages and manual session expose records without write controls', async ({page}) => {
  await page.goto('/demo/?lang=en');
  await expect(page.locator('[data-pm-overview]')).toBeVisible();
  for (const route of ['projects', 'home', 'sessions', 'settings', 'session/demo-host/manual-reset-notes']) {
    await page.goto('/demo/?lang=en#/' + route);
    await expect(page.locator('#main h1')).toBeVisible();
    await expect(page.locator('#main [data-api-error]:visible')).toHaveCount(0);
  }
  await expect(page.locator('#main')).toContainText('Reset notes');
  await expect(page.locator('#main')).toContainText('read-only');
  await expect(page.locator('#main form button[type=submit]:enabled')).toHaveCount(0);
});


test('Settings language switch stays inside the localized public tour', async ({page}) => {
  await page.goto('/demo/?lang=en#/settings');
  await page.locator('[data-language-switch]').getByRole('button', {name: '繁體中文', exact: true}).click();
  await expect(page).toHaveURL(/lang=zh-TW#\/settings$/);
  await expect(page.locator('#demo-bar')).toContainText('合成資料');
  await expect(page.locator('[data-language-switch]').getByRole('button', {name: '繁體中文', exact: true})).toHaveAttribute('aria-pressed', 'true');
  await page.locator('[data-language-switch]').getByRole('button', {name: 'English', exact: true}).click();
  await expect(page).toHaveURL(/lang=en#\/settings$/);
  await expect(page.locator('#demo-bar')).toContainText('Sample data');
});
