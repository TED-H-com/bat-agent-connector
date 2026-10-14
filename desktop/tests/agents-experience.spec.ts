import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture, demoRoute} from './demo/index.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';
import {periodWindow} from '../src/pm-agents-trend.js';

const FROZEN = new Date('2026-10-13T09:00:00.000Z');

function tracked(locale: 'en' | 'zh-TW', rejectZone = false, buckets = true) {
  const demo = demoFixture({locale, buckets});
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

async function boot(page: Page, dispatch: (input: {path: string, method?: string}) => Promise<unknown>, time: Date = FROZEN) {
  await page.clock.install({time});
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
  await expect(page.locator('[data-resource-meta] [data-tz]')).toHaveText('Asia/Taipei');
  await expect(page.locator('[data-resource-meta]')).toContainText('updated');
  await expect(chart.locator('svg text')).toHaveCount(0);
  await expect(chart.locator('.pm-agents-xlabels')).toContainText('Oct 7');
  await expect(chart.locator('.pm-agents-xlabels')).not.toContainText('Oct 6');
  await expect(chart.locator('.pm-agents-yscale')).toContainText('0');
  await expect(chart.locator('.pm-agents-yscale')).toContainText('1K');
  await expect(chart.locator('.pm-agents-plot-main > svg')).toHaveAttribute('preserveAspectRatio', 'xMinYMin meet');
  expect(fixture.reads[0]).toContain('buckets=day');
  expect(new URL(fixture.reads[0], 'http://fixture').searchParams.get('tz')).toBe('Asia/Taipei');
  await expect(page.locator('[data-resource-metrics]')).toContainText('2 / 3');
  await expect(page.locator('[data-resource-metrics]')).toContainText('1 unavailable');
  await expect(page.locator('[data-resource-metrics]')).toContainText('1,552');
  await expect(page.locator('[data-resource-metrics] [data-status="partial"]')).toBeVisible();
  await expect(page.locator('[data-pm-resources]').getByRole('button', {name: 'Refresh', exact: true})).toHaveCount(0);
  await expect(page.locator('[data-pm-resources]').getByRole('button', {name: 'Update records', exact: true})).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'Refresh', exact: true})).toHaveCount(1);
  const today = chart.locator('g[data-date="2026-10-13"]');
  await expect(today).toHaveAttribute('data-incomplete', 'in_progress');
  await expect(today).toHaveAttribute('aria-label', /In progress/);
  await expect(chart.locator('g[data-date="2026-10-12"]')).toHaveAttribute('data-incomplete', 'settling');
  await expect(chart.locator('g[data-date="2026-10-07"]')).not.toHaveAttribute('data-incomplete', /.+/);
  const gap = chart.locator('g[data-date="2026-10-10"]');
  await expect(gap).toHaveAttribute('data-partial', 'true');
  await expect(gap.locator('.bar.cli-codex')).toHaveCount(0);
  await expect(gap.locator('.tick.cli-codex')).toHaveCount(0);
  await expect(gap.locator('.gap')).toHaveCount(1);
  await expect(gap.locator('.bar.cli-claude')).toHaveCount(1);
  await expect(today.locator('.bar.open')).toHaveCount(2);
  await expect(today.locator('.marker')).toHaveCount(1);
  await expect(chart.locator('g[data-date="2026-10-09"] .tick.cli-codex')).toHaveCount(1);
  await expect(chart.locator('.pm-agents-legend')).toContainText('No data');
  await expect(chart.locator('.pm-agents-legend')).toContainText('In progress');
  await expect(chart.locator('.pm-agents-legend')).toContainText('Settling');
  const markerGap = await today.evaluate(group => {
    const marker = group.querySelector('.marker')?.getBoundingClientRect();
    const bars = [...group.querySelectorAll('.bar')].map(node => node.getBoundingClientRect());
    const barTop = Math.min(...bars.map(box => box.top));
    return marker && bars.length ? barTop - marker.bottom : -1;
  });
  expect(markerGap).toBeGreaterThanOrEqual(-1);
  expect(markerGap).toBeLessThan(12);
  await gap.hover();
  await expect(chart.locator('[data-tip-cli="codex"]')).toHaveText('Codex · No data');
  await chart.getByText('Exact values', {exact: true}).click();
  await expect(chart.locator('th').first()).toHaveText('Date');
  await expect(chart.locator('tr[data-date="2026-10-10"] td').first()).toHaveText('Oct 10');
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
  await expect(unavailable).toContainText('Unavailable');
  expect(await unavailable.innerText()).not.toMatch(/Codex|\d/);
  await unavailable.click();
  const detail = page.locator('[data-resource-detail]');
  await expect(detail).toContainText('codex-rollout-v1');
  await expect(detail).not.toContainText('420');
  const codex = page.locator('[data-resource-row="codex-implementer"]');
  await expect(codex).toContainText('1,006');
  expect(await codex.innerText()).not.toContain('1,200');
  await codex.click();
  const codexDetail = page.locator('[data-resource-detail]');
  await expect(codexDetail).toContainText('Cache read');
  await expect(codexDetail).toContainText('704');
  await expect(codexDetail).toContainText('Not reported');
  await expect(codexDetail).toContainText('Medium');
  await expect(codexDetail).toContainText('Model');
  await expect(codexDetail).toContainText('Tokens');
  const clippedPills = await page.locator('[data-resource-row] .ui-pill').evaluateAll(nodes => nodes.flatMap(node => {
    const pill = node.getBoundingClientRect();
    const port = node.closest('.ui-data-table, .pm-agents-list')?.getBoundingClientRect();
    const cut = node.scrollWidth > node.clientWidth + 1 || (port && pill.width > 0 && (pill.left < port.left - 1 || pill.right > port.right + 1));
    return cut ? [node.textContent] : [];
  }));
  expect(clippedPills).toEqual([]);
  const quota = page.locator('[data-resource-quota]');
  await expect(quota).toContainText('Codex');
  await expect(quota).toContainText('5-hour window');
  await expect(quota).toContainText('7-day window');
  await expect(quota).toContainText('Used 40%');
  await expect(quota).toContainText('Used 12%');
  expect(await quota.innerText()).not.toContain('pool_');
  const href = await page.locator('[data-resource-discuss]').getAttribute('href');
  expect(parseDiscussRefs(new URL(href || '', 'http://x').hash.split('?')[1] || '')).toEqual({refs: [], title: 'codex-implementer', from: 'agents', report: ''});
  const before = fixture.reads.length;
  await page.getByRole('radio', {name: '30d', exact: true}).click();
  await expect.poll(() => fixture.reads.length).toBe(before + 1);
  const period = new URL(fixture.reads.at(-1) || '', 'http://fixture');
  expect(period.searchParams.get('tz')).toBe('Asia/Taipei');
  expect(period.searchParams.get('buckets')).toBe('day');
  expect(period.searchParams.get('since')).toBe(periodWindow(new Date(period.searchParams.get('until') || ''), 30, 'Asia/Taipei').since);
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
  await page.getByRole('button', {name: 'Pause updates', exact: true}).click();
  const pausedMeta = page.locator('[data-resource-meta]');
  await expect(pausedMeta).toHaveText('Paused · data from just now');
  await expect(page.getByRole('button', {name: 'Resume updates', exact: true})).not.toHaveAttribute('aria-pressed');
  const paused = fixture.reads.length;
  const frozen = await pausedMeta.locator('time').getAttribute('datetime');
  await page.clock.fastForward(120000);
  expect(fixture.reads).toHaveLength(paused);
  await expect(pausedMeta.locator('time')).toHaveAttribute('datetime', frozen!);
  await expect(pausedMeta).toContainText('2m ago');
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
  await page.locator('[data-resource-filter]').selectOption({label: 'Role · reviewer'});
  await expect(page.locator('[data-resource-row]')).toHaveCount(1);
  await expect(page.locator('[data-resource-metrics]')).toContainText('1,552');
  await expect(page.locator('[data-resource-chart]')).toBeVisible();
  await expect(page.locator('[data-resource-quota]')).toContainText('40%');
  const before = fixture.reads.length;
  await page.getByRole('radio', {name: '30d', exact: true}).click();
  await expect.poll(() => fixture.reads.length).toBeGreaterThan(before);
  await page.locator('[data-pm-view="overview"]').click();
  await page.locator('[data-pm-view="agents"]').click();
  await expect(page.getByRole('radio', {name: '30d', exact: true})).toHaveAttribute('aria-checked', 'true');
  await expect(page.locator('[data-resource-filter]')).toHaveValue('role\u0000reviewer');
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
  await expect(page.locator('[data-resource-meta]')).toContainText('更新');
  await expect(page.locator('[data-resource-meta] [data-tz]')).toHaveText('Asia/Taipei');
  await expect(agents).toContainText('無資料');
  await expect(agents).toContainText('5 小時區間');
  await expect(agents).toContainText('7 天區間');
  await page.getByRole('button', {name: '暫停更新', exact: true}).click();
  await expect(page.getByRole('button', {name: '繼續更新', exact: true})).toBeVisible();
  await expect(page.locator('[data-resource-meta]')).toContainText('已暫停');
  await expect(page.locator('[data-resource-meta]')).toContainText('剛剛');
  await expect(agents).toContainText('用量');
  await expect(agents).toContainText('進行中');
  await expect(agents).toContainText('待穩定');
  await expect(agents).toContainText('待確認');
  await expect(agents.locator('[data-resource-row="security-notes"]')).toContainText('無法取得');
  await expect(page.locator('[data-resource-metrics]')).toContainText('1 個無法取得');
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
    const agents = page.locator('[data-pm-resources]');
    if (width < 800) {
      await expect(agents.locator('.pm-agents-list [data-resource-row]')).toHaveCount(3);
      await expect(agents.locator('.ui-data-table')).toHaveCount(0);
    } else await expect(agents.locator('.ui-data-table [data-resource-row]')).toHaveCount(3);
    if (width <= 640) expect((await page.locator('[data-resource-metrics]').boundingBox())?.height || 0).toBeLessThan(160);
    if (width === 390) expect((await page.locator('.pm-agents-legend').boundingBox())?.height || 0).toBeLessThan(28);
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

for (const width of [390, 768]) {
  test(`every source row is tabbable at ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
    const page = await context.newPage();
    const fixture = tracked('en');
    await boot(page, fixture.dispatch);
    await openAgents(page, width);
    const rows = page.locator('.pm-agents-list [data-resource-row]');
    await expect(rows).toHaveCount(3);
    expect(await rows.evaluateAll(nodes => nodes.map(node => node.tabIndex))).toEqual([0, 0, 0]);
    await rows.nth(0).focus();
    await page.keyboard.press('Tab');
    await expect(rows.nth(1)).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(rows.nth(2)).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('[data-resource-detail]')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(rows.nth(2)).toBeFocused();
    await rows.nth(0).focus();
    await page.keyboard.press(' ');
    await expect(page.locator('[data-resource-detail]')).toContainText('codex-implementer');
    await context.close();
  });
}

test('focus stays on the selected row, the disclosures and the period control', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1440, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const fixture = tracked('en');
  let seen = 0;
  const dispatch = async (input: {path: string, method?: string}) => {
    const result = await fixture.dispatch(input) as {data?: {rows?: {observations?: number}[]}};
    if (String(input.path).includes('/resources')) {
      seen += 1;
      if (seen > 1) for (const row of result.data?.rows || []) if (typeof row.observations === 'number') row.observations += seen;
    }
    return result;
  };
  await boot(page, dispatch);
  await openAgents(page, 1440);
  await page.locator('[data-resource-row="codex-implementer"]').click();
  await page.locator('[data-resource-row="claude-reviewer"]').click();
  await expect(page.locator('[data-resource-detail] h2')).toHaveText('claude-reviewer');
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-resource-row="claude-reviewer"]')).toBeFocused();
  await page.locator('[data-resource-row="codex-implementer"]').click();
  const detail = page.locator('[data-resource-detail]');
  await detail.getByText('Details', {exact: true}).click();
  await expect(detail.locator('details')).toHaveAttribute('open', '');
  const before = fixture.reads.length;
  await page.clock.fastForward(61000);
  await expect.poll(() => fixture.reads.length).toBeGreaterThan(before);
  await expect(detail.locator('summary')).toBeFocused();
  await expect(detail.locator('details')).toHaveAttribute('open', '');
  await page.locator('[data-resource-chart]').getByText('Exact values', {exact: true}).focus();
  const again = fixture.reads.length;
  await page.clock.fastForward(61000);
  await expect.poll(() => fixture.reads.length).toBeGreaterThan(again);
  await expect(page.locator('[data-resource-chart] summary')).toBeFocused();
  await page.getByRole('radio', {name: '7d', exact: true}).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('radio', {name: '30d', exact: true})).toBeFocused();
  await expect(detail).toBeVisible();
  await expect.poll(() => fixture.reads.at(-1) || '').toContain('buckets=day');
  await expect(page.getByRole('radio', {name: '30d', exact: true})).toBeFocused();
  await expect(detail.locator('h2')).toHaveText('codex-implementer');
  await context.close();
});

test('the drawer scrim survives a tick at 1000px', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1000, height: 800}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const fixture = tracked('en');
  let seen = 0;
  const dispatch = async (input: {path: string, method?: string}) => {
    const result = await fixture.dispatch(input) as {data?: {rows?: {observations?: number}[]}};
    if (String(input.path).includes('/resources')) {
      seen += 1;
      if (seen > 1) for (const row of result.data?.rows || []) if (typeof row.observations === 'number') row.observations += 10;
    }
    return result;
  };
  await boot(page, dispatch);
  await openAgents(page, 1000);
  await page.locator('[data-resource-select]').first().click();
  await expect(page.locator('.ui-inspector-scrim')).toHaveCount(1);
  await expect(page.locator('[data-resource-detail]')).toContainText('4');
  const before = fixture.reads.length;
  await page.clock.fastForward(61000);
  await expect.poll(() => fixture.reads.length).toBeGreaterThan(before);
  await expect(page.locator('[data-resource-detail]')).toContainText('14');
  await expect(page.locator('.ui-inspector-scrim')).toHaveCount(1);
  await expect(page.locator('[data-resource-detail]')).toBeVisible();
  await context.close();
});

test('x labels and the in-progress marker stay inside the plot', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1440, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const fixture = tracked('en');
  await boot(page, fixture.dispatch);
  await openAgents(page, 1440);
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({width, height: width === 390 ? 844 : 900});
    for (const period of ['7d', '30d', '90d']) {
      const before = fixture.reads.length;
      await page.getByRole('radio', {name: period, exact: true}).click();
      await expect.poll(() => fixture.reads.length).toBeGreaterThan(before);
      await expect(page.locator('[data-resource-chart] g.day')).toHaveCount(Number(period.slice(0, -1)));
      await expect.poll(async () => page.locator('.pm-agents-xlabels span', {hasText: /\d/}).count()).toBeGreaterThan(0);
      await expect.poll(() => page.locator('.pm-agents-xlabels').evaluate(box => [...box.querySelectorAll('span')].filter(node => node.textContent && !node.hidden).flatMap(node => {
        const label = node.getBoundingClientRect();
        const plot = box.getBoundingClientRect();
        return label.width > 0 && (label.left < plot.left - 1 || label.right > plot.right + 1) ? [node.textContent] : [];
      })), {message: `${period} at ${width}`}).toEqual([]);
    }
  }
  await page.setViewportSize({width: 390, height: 844});
  await expect(page.locator('.pm-agents-list')).toBeVisible();
  const markerReads = fixture.reads.length;
  await page.getByRole('radio', {name: '7d', exact: true}).click();
  await expect.poll(() => fixture.reads.length).toBeGreaterThan(markerReads);
  await expect(page.locator('g.day')).toHaveCount(7);
  await expect.poll(async () => page.locator('[data-resource-chart] g[data-incomplete="in_progress"] .marker').evaluate(node => {
    const mark = node.getBoundingClientRect();
    const plot = node.ownerSVGElement?.getBoundingClientRect();
    return Boolean(node.isConnected && plot && mark.width > 0 && mark.left >= plot.left - 1 && mark.right <= plot.right + 1 && mark.top >= plot.top - 1 && mark.bottom <= plot.bottom + 1);
  })).toBe(true);
  await context.close();
});

test('a tick withdraws a source from the rows and the chart together', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1440, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const fixture = tracked('en');
  let drop = false;
  const dispatch = async (input: {path: string, method?: string}) => {
    const result = await fixture.dispatch(input) as {data?: {rows?: {source_id?: string, status?: string, metrics?: unknown, observations?: unknown, configurations?: unknown[]}[], buckets?: {series?: {source_id?: string}[]}}};
    if (drop && String(input.path).includes('/resources') && result.data) {
      const row = result.data.rows?.find(item => item.source_id === 'codex-implementer');
      if (row) {row.status = 'source_unavailable'; row.metrics = null; row.observations = null; row.configurations = [];}
      if (result.data.buckets?.series) result.data.buckets.series = result.data.buckets.series.filter(item => item.source_id !== 'codex-implementer');
    }
    return result;
  };
  await boot(page, dispatch);
  await openAgents(page, 1440);
  await expect(page.locator('.bar.cli-codex').first()).toBeVisible();
  drop = true;
  const before = fixture.reads.length;
  await page.clock.fastForward(61000);
  await expect.poll(() => fixture.reads.filter(path => path.includes('buckets=day')).length).toBeGreaterThan(1);
  expect(fixture.reads.length).toBeGreaterThanOrEqual(before + 2);
  await expect(page.locator('.bar.cli-codex')).toHaveCount(0);
  await expect(page.locator('.tick.cli-codex')).toHaveCount(0);
  await expect(page.locator('[data-resource-row="codex-implementer"]')).toContainText('Unavailable');
  await expect(page.locator('.bar.cli-claude').first()).toBeVisible();
  await expect(page.locator('[data-resource-metrics]')).toContainText('546');
  await context.close();
});

test('a tick does not replace a slow period change', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1280, height: 800}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const fixture = tracked('en');
  let hold = false;
  let release = () => {};
  const dispatch = async (input: {path: string, method?: string}) => {
    const result = await fixture.dispatch(input);
    if (hold && String(input.path).includes('/resources')) {
      hold = false;
      await new Promise<void>(resolve => {release = resolve;});
    }
    return result;
  };
  await boot(page, dispatch);
  await openAgents(page, 1280);
  await expect(page.locator('g.day')).toHaveCount(7);
  hold = true;
  const before = fixture.reads.length;
  await page.clock.fastForward(61000);
  await expect.poll(() => fixture.reads.length).toBe(before + 1);
  await page.getByRole('radio', {name: '30d', exact: true}).click();
  await expect.poll(() => fixture.reads.length).toBe(before + 2);
  await expect.poll(() => page.locator('g.day').count()).toBe(30);
  release();
  await page.waitForTimeout(300);
  await expect(page.locator('g.day')).toHaveCount(30);
  await expect(page.getByRole('radio', {name: '30d', exact: true})).toHaveAttribute('aria-checked', 'true');
  expect(fixture.reads.at(-1)).toContain('buckets=day');
  await context.close();
});

test('the open view asks for a new trend after local midnight', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1440, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const fixture = tracked('en');
  await boot(page, fixture.dispatch, new Date('2026-10-13T15:50:00.000Z'));
  await openAgents(page, 1440);
  await expect(page.locator('g[data-date="2026-10-13"]')).toHaveAttribute('data-incomplete', 'in_progress');
  await expect(page.locator('[data-resource-meta]')).toContainText('Oct 13');
  await page.clock.fastForward(12 * 60 * 1000);
  await expect.poll(() => fixture.reads.some(path => path.includes('buckets=day') && path.includes('2026-10-13T16%3A'))).toBe(true);
  await expect(page.locator('g[data-date="2026-10-14"]')).toHaveAttribute('data-incomplete', 'in_progress');
  await expect(page.locator('[data-resource-meta]')).toContainText('Oct 14');
  await context.close();
});

test('version 1 without the buckets capability draws no chart', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1280, height: 800}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const fixture = tracked('en', false, false);
  await boot(page, fixture.dispatch);
  await openAgents(page, 1280);
  await expect(page.locator('[data-resource-chart]')).toHaveCount(0);
  await expect(page.locator('[data-resource-row]')).toHaveCount(3);
  await expect(page.locator('[data-resource-metrics]')).toContainText('Partial');
  await expect(page.locator('[data-resource-metrics]')).toContainText('2 / 3');
  await expect(page.locator('[data-resource-metrics]')).toContainText('1 unavailable');
  await expect(page.locator('[data-resource-quota]')).toContainText('40%');
  expect(fixture.reads[0]).not.toContain('buckets=');
  expect(fixture.reads[0]).not.toContain('tz=');
  const before = fixture.reads.length;
  await page.getByRole('radio', {name: '30d', exact: true}).click();
  await expect.poll(() => fixture.reads.length).toBe(before + 1);
  expect(fixture.reads.at(-1)).not.toContain('buckets=');
  expect(fixture.reads.at(-1)).not.toContain('tz=');
  await expect(page.locator('[data-resource-chart]')).toHaveCount(0);
  await context.close();
});

for (const scheme of ['light', 'dark'] as const) {
  test(`usage series contrast in ${scheme}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1440, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce', colorScheme: scheme});
    const page = await context.newPage();
    const fixture = tracked('en');
    await boot(page, fixture.dispatch);
    await openAgents(page, 1440);
    const measured = await page.evaluate(() => {
      const parts = (color: string) => color.match(/[\d.]+/g)?.slice(0, 3).map(Number) || [];
      const channel = (value: number) => {const s = value / 255; return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;};
      const luminance = (color: string) => {const [r, g, b] = parts(color).map(channel); return 0.2126 * r + 0.7152 * g + 0.0722 * b;};
      const ratio = (a: string, b: string) => {const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05);};
      const codex = getComputedStyle(document.querySelector('.bar.cli-codex')!).fill;
      const claude = getComputedStyle(document.querySelector('.bar.cli-claude')!).fill;
      const surface = getComputedStyle(document.querySelector('.seg-gap')!).fill;
      return {codex, claude, surface, codexRatio: ratio(codex, surface), claudeRatio: ratio(claude, surface), between: ratio(codex, claude), openOpacity: getComputedStyle(document.querySelector('.bar.open')!).opacity};
    });
    console.log(`contrast ${scheme} ${JSON.stringify(measured)}`);
    expect(measured.openOpacity).toBe('1');
    expect(measured.codexRatio, JSON.stringify(measured)).toBeGreaterThanOrEqual(3);
    expect(measured.claudeRatio, JSON.stringify(measured)).toBeGreaterThanOrEqual(3);
    expect(measured.between, JSON.stringify(measured)).toBeGreaterThanOrEqual(2);
    await context.close();
  });
}
