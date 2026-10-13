import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture, demoRoute} from './demo/index.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';

const FROZEN = new Date('2026-10-13T09:00:00.000Z');

function tracked(locale: 'en' | 'zh-TW', rejectZone = false) {
  const demo = demoFixture({locale});
  const reads: string[] = [];
  let rejects = rejectZone ? 1 : 0;
  const dispatch = async (input: {path: string, method?: string}) => {
    const url = new URL(input.path, 'http://fixture');
    if (url.pathname.endsWith('/resources')) {
      reads.push(input.path);
      if (rejects > 0 && url.searchParams.get('buckets') === 'day' && url.searchParams.get('tz') !== 'UTC') {
        rejects -= 1;
        return {status: 422, data: {error: {code: 'INVALID_PARAMS', message: 'invalid_timezone'}}};
      }
    }
    return demo.dispatch(input);
  };
  return {reads, dispatch, copy: demo.copy};
}

async function boot(page: Page, dispatch: (input: {path: string, method?: string}) => Promise<unknown>) {
  await page.clock.install({time: FROZEN});
  await mountConversation(page, false, dispatch);
  await page.goto(demoRoute());
  await expect.poll(() => page.locator('[data-pm-view]').count()).toBeGreaterThan(0);
}

async function openAgents(page: Page, width: number) {
  const entry = page.locator('[data-pm-view="agents"]');
  if (width < 1000 && !await entry.isVisible()) await page.locator('#workspace-menu').click();
  await entry.click();
  await expect(page.locator('[data-resource-row]')).toHaveCount(3);
}

test('agents period, trend and unavailable source stay sourced', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1440, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const fixture = tracked('en');
  await boot(page, fixture.dispatch);
  await openAgents(page, 1440);
  const chart = page.locator('[data-resource-chart]');
  await expect(chart).toBeVisible();
  await expect(chart.locator('[data-tz]')).toHaveText('Asia/Taipei');
  expect(fixture.reads[0]).toContain('buckets=day');
  expect(new URL(fixture.reads[0], 'http://fixture').searchParams.get('tz')).toBe('Asia/Taipei');
  await expect(page.locator('[data-resource-metrics]')).toContainText('2 / 3');
  await expect(page.locator('[data-resource-metrics] [data-status="partial"]')).toBeVisible();
  await expect(page.locator('[data-resource-meta]')).toContainText('Observed');
  const today = chart.locator('g[data-date="2026-10-13"]');
  await expect(today).toHaveAttribute('data-incomplete', 'in_progress');
  await expect(today).toHaveAttribute('aria-label', /In progress/);
  await expect(chart.locator('g[data-date="2026-10-12"]')).toHaveAttribute('data-incomplete', 'settling');
  const gap = chart.locator('g[data-date="2026-10-10"]');
  await expect(gap).toHaveAttribute('data-partial', 'true');
  await expect(gap.locator('.bar.cli-codex')).toHaveCount(0);
  await expect(gap.locator('.tick.cli-codex')).toHaveCount(0);
  await expect(gap.locator('.bar.cli-claude')).toHaveCount(1);
  await expect(chart.locator('g[data-date="2026-10-09"] .tick.cli-codex')).toHaveCount(1);
  await gap.hover();
  await expect(chart.locator('[data-tip-cli="codex"]')).toHaveText('Codex —');
  await chart.getByText('Exact values', {exact: true}).click();
  await expect(chart.locator('tr[data-date="2026-10-10"] [data-cli="codex"]')).toHaveText('—');
  await expect(chart.locator('tr[data-date="2026-10-09"] [data-cli="codex"]')).toHaveText('0');
  const rows = page.locator('[data-resource-row]');
  await rows.nth(0).focus();
  await page.keyboard.press('ArrowDown');
  await expect(rows.nth(1)).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('[data-resource-detail]')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(rows.nth(1)).toBeFocused();
  const unavailable = page.locator('[data-resource-row="security-notes"]');
  await expect(unavailable).toContainText('Source unavailable');
  expect(await unavailable.innerText()).not.toMatch(/Codex|\d/);
  await unavailable.click();
  const detail = page.locator('[data-resource-detail]');
  await expect(detail).toContainText('codex-rollout-v1');
  await expect(detail).not.toContainText('420');
  await page.locator('[data-resource-row="codex-implementer"]').click();
  const href = await page.locator('[data-resource-discuss]').getAttribute('href');
  expect(parseDiscussRefs(new URL(href || '', 'http://x').hash.split('?')[1] || '')).toEqual({refs: [], title: 'codex-implementer', from: 'agents', report: ''});
  const before = fixture.reads.length;
  await page.getByRole('radio', {name: '30d', exact: true}).click();
  await expect.poll(() => fixture.reads.length).toBe(before + 1);
  const period = new URL(fixture.reads.at(-1) || '', 'http://fixture');
  expect(period.searchParams.get('tz')).toBe('Asia/Taipei');
  expect(period.searchParams.get('buckets')).toBe('day');
  expect(Date.parse(period.searchParams.get('until') || '') - Date.parse(period.searchParams.get('since') || '')).toBe(30 * 86400000);
  await expect(chart).toBeVisible();
  expect(await page.locator('[data-pm-resources]').innerText()).not.toMatch(/\b(?:undefined|null|NaN)\b/);
  expect(errors).toEqual([]);
  await context.close();
});

test('a rejected browser zone is requested once more as UTC', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1280, height: 800}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const fixture = tracked('en', true);
  await boot(page, fixture.dispatch);
  await openAgents(page, 1280);
  await expect(page.locator('[data-tz]')).toHaveText('UTC');
  expect(fixture.reads).toHaveLength(2);
  expect(new URL(fixture.reads[1], 'http://fixture').searchParams.get('tz')).toBe('UTC');
  await page.waitForTimeout(300);
  expect(fixture.reads).toHaveLength(2);
  await context.close();
});

test('pause freezes the screen and a live refresh reuses the trend', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1280, height: 800}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const fixture = tracked('en');
  await boot(page, fixture.dispatch);
  await openAgents(page, 1280);
  const meta = await page.locator('[data-resource-meta]').innerText();
  await page.getByRole('button', {name: 'Pause updates', exact: true}).click();
  const paused = fixture.reads.length;
  await page.clock.fastForward(120000);
  expect(fixture.reads).toHaveLength(paused);
  await expect(page.locator('[data-resource-meta]')).toHaveText(meta);
  await page.getByRole('button', {name: 'Resume updates', exact: true}).click();
  await expect.poll(() => fixture.reads.length).toBe(paused + 1);
  expect(fixture.reads.at(-1)).not.toContain('buckets=');
  await expect(page.locator('[data-resource-chart]')).toBeVisible();
  await page.locator('[data-pm-view="overview"]').click();
  await page.clock.fastForward(120000);
  expect(fixture.reads).toHaveLength(paused + 1);
  await context.close();
});

test('filter, period and selection survive leaving the view', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1280, height: 800}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const fixture = tracked('en');
  await boot(page, fixture.dispatch);
  await openAgents(page, 1280);
  await page.getByRole('radio', {name: '30d', exact: true}).click();
  await page.getByRole('button', {name: 'All sources', exact: true}).click();
  await page.getByRole('menuitem', {name: 'Role · reviewer', exact: true}).click();
  await expect(page.locator('[data-resource-row]')).toHaveCount(1);
  await page.locator('[data-pm-view="overview"]').click();
  await page.locator('[data-pm-view="agents"]').click();
  await expect(page.getByRole('radio', {name: '30d', exact: true})).toHaveAttribute('aria-checked', 'true');
  await expect(page.locator('[data-resource-filter]')).toHaveText('Role · reviewer');
  await expect(page.locator('[data-resource-row]')).toHaveCount(1);
  await context.close();
});

test('agents copy is Traditional Chinese', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'zh-TW', viewport: {width: 1280, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const fixture = tracked('zh-TW');
  await boot(page, fixture.dispatch);
  await openAgents(page, 1280);
  const agents = page.locator('[data-pm-resources]');
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Agent 資源');
  await expect(agents.locator('[data-resource-meta]')).toContainText('觀測');
  await expect(agents).toContainText('用量');
  await expect(agents).toContainText('進行中');
  await expect(agents).toContainText('待穩定');
  await expect(agents).toContainText('待確認');
  await expect(agents.locator('[data-resource-row="security-notes"]')).toContainText('來源無法讀取');
  await expect(agents.locator('[data-resource-row="security-notes"]')).not.toContainText('Codex');
  expect(await agents.innerText()).not.toMatch(/\b(?:undefined|null|NaN|res_\w+)\b/);
  await context.close();
});

for (const locale of ['en-US', 'zh-TW'] as const) for (const width of [390, 768, 1440]) {
  test(`agents layout ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
    const httpErrors: string[] = [];
    page.on('response', response => {
      if (response.status() < 400) return;
      httpErrors.push(`${response.status()} ${new URL(response.url()).pathname}`);
    });
    const fixture = tracked(locale === 'zh-TW' ? 'zh-TW' : 'en');
    await boot(page, fixture.dispatch);
    await openAgents(page, width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.locator('[data-resource-select]').first().click();
    await expect(page.locator('[data-resource-detail]')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.locator('[data-pm-resources]').innerText()).not.toMatch(/\b(?:undefined|null|NaN)\b/);
    // Web boot probes a managed browser session. The demo answers 404, and Chromium logs it.
    expect(httpErrors).toEqual(['404 /api/v1/browser-session']);
    expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
    await context.close();
  });
}
