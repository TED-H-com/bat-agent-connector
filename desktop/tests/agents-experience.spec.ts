import {revealPmView} from './pm-navigation.ts';
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

async function openAgents(page: Page, _width: number) {
  const entry = await revealPmView(page, 'agents');
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
  await expect(codexDetail).toContainText('Unknown');
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
  expect(parseDiscussRefs(new URL(href || '', 'http://x').hash.split('?')[1] || '')).toEqual({refs: [], title: 'Codex · Implementer', from: 'agents', report: ''});
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
  await expect(pausedMeta).toContainText('2 min ago');
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
  await page.locator('[data-resource-filter]').selectOption({label: 'Role · Reviewer'});
  await expect(page.locator('[data-resource-row]')).toHaveCount(1);
  await expect(page.locator('[data-resource-metrics]')).toContainText('1,552');
  await expect(page.locator('[data-resource-chart]')).toBeVisible();
  await expect(page.locator('[data-resource-quota]')).toContainText('40%');
  const before = fixture.reads.length;
  await page.getByRole('radio', {name: '30d', exact: true}).click();
  await expect.poll(() => fixture.reads.length).toBeGreaterThan(before);
  await page.locator('[data-pm-view="overview"]').click();
  await (await revealPmView(page, 'agents')).click();
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
  await expect(page.getByRole('heading', {level: 1})).toHaveText('代理資源');
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
    await expect(page.locator('[data-resource-detail]')).toContainText('Codex · Implementer');
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
  await expect(page.locator('[data-resource-detail] h2')).toHaveText('Claude · Reviewer');
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
  await expect(detail.locator('h2')).toHaveText('Codex · Implementer');
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

for (const locale of ['en', 'zh-TW'] as const) test(`x labels and the in-progress marker stay inside the plot without collisions ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width: 1440, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const fixture = tracked(locale);
  const periodName = (period: string) => locale === 'en' ? period : period.replace('d', ' 天');
  await boot(page, fixture.dispatch);
  await openAgents(page, 1440);
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({width, height: width === 390 ? 844 : 900});
    for (const period of ['7d', '30d', '90d']) {
      const before = fixture.reads.length;
      await page.getByRole('radio', {name: periodName(period), exact: true}).click();
      await expect.poll(() => fixture.reads.length).toBeGreaterThan(before);
      await expect(page.locator('[data-resource-chart] g.day')).toHaveCount(Number(period.slice(0, -1)));
      await expect.poll(async () => page.locator('.pm-agents-xlabels span', {hasText: /\d/}).count()).toBeGreaterThan(0);
      await expect.poll(() => page.locator('.pm-agents-xlabels').evaluate(box => [...box.querySelectorAll('span')].filter(node => node.textContent && !node.hidden).flatMap(node => {
        const label = node.getBoundingClientRect();
        const plot = box.getBoundingClientRect();
        return label.width > 0 && (label.left < plot.left - 1 || label.right > plot.right + 1) ? [node.textContent] : [];
      })), {message: `${period} at ${width}`}).toEqual([]);
      await expect.poll(() => page.locator('.pm-agents-xlabels').evaluate(box => {
        const labels = [...box.querySelectorAll('span')].filter(node => node.textContent && !node.hidden)
          .map(node => ({text: node.textContent, box: node.getBoundingClientRect()})).filter(label => label.box.width > 0);
        return labels.slice(1).flatMap((label, index) => labels[index].box.right + 4 > label.box.left
          ? [`${labels[index].text} / ${label.text}`] : []);
      }), {message: `${locale} ${period} at ${width}: adjacent dates need a readable gap`}).toEqual([]);
    }
  }
  await page.setViewportSize({width: 390, height: 844});
  await expect(page.locator('.pm-agents-list')).toBeVisible();
  const markerReads = fixture.reads.length;
  await page.getByRole('radio', {name: periodName('7d'), exact: true}).click();
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

async function goView(page: Page, view: string) {
  const entry = await revealPmView(page, view);
  await entry.click();
}

async function consistentUsage(page: Page, codex = 1006, claude = 546, range = /Oct 7.*Oct 13|10月7日.*10月13日/) {
  await expect(page.locator('[data-resource-meta]')).toContainText(range);
  await expect(page.locator('[data-resource-metrics] .ui-metric-value').first()).toContainText((codex + claude).toLocaleString());
  const total = (cli: string) => page.locator(`[data-resource-chart] tbody [data-cli="${cli}"]`).evaluateAll(nodes =>
    nodes.reduce((sum, node) => sum + (node.textContent === '—' ? 0 : Number(node.textContent?.replaceAll(',', ''))), 0));
  await expect.poll(() => total('codex')).toBe(codex);
  await expect.poll(() => total('claude')).toBe(claude);
  await expect(page.locator('[data-resource-row="codex-implementer"]')).toContainText(codex.toLocaleString());
  await expect(page.locator('[data-resource-row="claude-reviewer"]')).toContainText(claude.toLocaleString());
  await expect(page.locator('tr[data-date="2026-10-09"] [data-cli="codex"]')).toHaveText('0');
  await expect(page.locator('tr[data-date="2026-10-10"] [data-cli="codex"]')).toHaveText('—');
}

for (const locale of ['en', 'zh-TW'] as const) test(`returning and new usage keep one read, human names and unknown facts ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width: 1440, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage(), fixture = tracked(locale);
  let extra = 0, release = () => {}, hold: Promise<void> | null = null;
  const dispatch = async (input: {path: string, method?: string}) => {
    const result = await fixture.dispatch(input) as {data?: {window: {since: string, until: string}, rows?: {source_id: string, metrics?: {total: {value: number, observed: number}}}[], buckets?: {series: {source_id: string, days: {tokens: {total: number | null}}[]}[]}}};
    if (input.path.includes('/resources') && result.data) result.data.window.since = result.data.window.since.replace('Z', '+00:00');
    if (input.path.includes('/resources') && extra && result.data) {
      const row = result.data.rows?.find(row => row.source_id === 'codex-implementer');
      if (row?.metrics) {row.metrics.total.value += extra; row.metrics.total.observed += extra;}
      const last = result.data.buckets?.series.find(series => series.source_id === 'codex-implementer')?.days.at(-1);
      if (last && last.tokens.total != null) last.tokens.total += extra;
      if (result.data.buckets && hold) await hold;
    }
    return result;
  };
  await boot(page, dispatch);
  await openAgents(page, 1440);
  const name = locale === 'en' ? 'Codex · Implementer' : 'Codex · 實作';
  const role = locale === 'en' ? 'Implementer' : '實作';
  const detail = page.locator('[data-resource-detail]');
  for (const width of [1440, 1000, 390]) {
    if (width !== 1440) {
      await goView(page, 'overview');
      await page.setViewportSize({width, height: width === 390 ? 844 : 900});
      const before = fixture.reads.length;
      await goView(page, 'agents');
      await expect.poll(() => fixture.reads.length).toBe(before + 1);
      expect(fixture.reads.at(-1)).not.toContain('buckets=');
    }
    await consistentUsage(page);
    const row = page.locator('[data-resource-row="codex-implementer"]');
    await expect(row).toContainText(name);
    await expect(row).not.toContainText('codex-implementer');
    await expect(page.locator('[data-resource-filter] option').filter({hasText: new RegExp(`^(?:Role|角色) · ${role}$`)})).toHaveText(locale === 'en' ? `Role · ${role}` : `角色 · ${role}`);
    await row.click();
    await expect(detail.locator('h2')).toHaveText(name);
    for (const label of locale === 'en' ? ['Reported model', 'Cache write', 'Messages'] : ['回報模型', '快取寫入', '訊息數']) {
      await expect(detail.locator('dt', {hasText: new RegExp(`^${label}$`)}).locator('+ dd [data-status="unknown"]')).toHaveText(locale === 'en' ? 'Unknown' : '未知');
    }
    const sourceId = detail.locator('details code.mono', {hasText: 'codex-implementer'});
    const detailsToggle = detail.locator('details summary');
    if (await detail.locator('details').getAttribute('open') != null) await detailsToggle.click();
    await expect(detail.locator('details')).not.toHaveAttribute('open');
    await expect(sourceId).toBeHidden();
    await detail.getByText(locale === 'en' ? 'Details' : '詳細資料', {exact: true}).click();
    await expect(sourceId).toBeVisible();
    await detail.getByText(locale === 'en' ? 'Details' : '詳細資料', {exact: true}).click();
    await expect(detail.locator('details')).not.toHaveAttribute('open');
    await page.waitForTimeout(50);
    if (locale === 'zh-TW') expect(await page.locator('[data-pm-resources]').innerText()).not.toMatch(/Tokens|CLI|implementer|reviewer|investigator/);
    await page.keyboard.press('Escape');
  }
  // Hold the follow-up read: neither the fresh total nor its inspector may paint
  // until the new daily values arrive. The chart remains mounted throughout.
  await page.locator('[data-resource-row="codex-implementer"]').click();
  await page.locator('[data-resource-chart]').evaluate(node => {node.setAttribute('data-consistent-pair', 'retained');});
  extra = 80; hold = new Promise<void>(resolve => {release = resolve;});
  const before = fixture.reads.length;
  await page.clock.fastForward(61000);
  await expect.poll(() => fixture.reads.length).toBe(before + 2);
  expect(fixture.reads.at(-1)).toContain('buckets=day');
  await consistentUsage(page);
  await expect(detail).toContainText('1,006');
  await expect(page.locator('[data-resource-chart]')).toHaveAttribute('data-consistent-pair', 'retained');
  release(); hold = null;
  await consistentUsage(page, 1086);
  await expect(detail).toContainText('1,086');
  await page.locator('[data-resource-discuss]').click();
  await expect(page.locator('[data-discussion-title]')).toHaveText(name);
  await expect(page.locator('.pd-thread-meta .ui-pill').filter({hasText: locale === 'en' ? /^Draft$/ : /^草稿$/})).toHaveText(locale === 'en' ? 'Draft' : '草稿');
  await context.close();
});

for (const status of [403, 404]) test(`a ${status} withdraws the consistent pair and rejects a late bucket response`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1440, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage(), fixture = tracked('en');
  let extra = 0, deny = false, holding = false, release = () => {};
  const dispatch = async (input: {path: string, method?: string}) => {
    const result = await fixture.dispatch(input) as {data?: {rows?: {metrics?: {total?: {value: number}}}[]}};
    if (input.path.includes('/resources')) {
      if (deny) return {status, data: {error: {code: status === 403 ? 'FORBIDDEN' : 'NOT_FOUND', message: 'Source withdrawn'}}};
      const total = result.data?.rows?.[0].metrics?.total;
      if (total) total.value += extra;
      if (extra && input.path.includes('buckets=day')) {holding = true; await new Promise<void>(resolve => {release = resolve;});}
    }
    return result;
  };
  await boot(page, dispatch); await openAgents(page, 1440);
  await page.locator('[data-resource-row="codex-implementer"]').click();
  extra = 10; await page.clock.fastForward(61000);
  await expect.poll(() => holding).toBe(true);
  deny = true; await goView(page, 'overview'); await goView(page, 'agents');
  const withdrawn = async () => {
    await expect(page.locator('[data-resource-row], [data-resource-metrics], [data-resource-chart], [data-resource-quota]')).toHaveCount(0);
    await expect(page.locator('[data-resource-detail]')).toBeHidden();
    await expect(page.locator('[data-resource-meta]')).toBeHidden();
  };
  await withdrawn(); release(); await page.waitForTimeout(200); await withdrawn();
  await context.close();
});

test('withdrawn source facts disappear while replacement buckets are still pending', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1440, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage(), fixture = tracked('en');
  let drop = false, holding = false, release = () => {};
  const dispatch = async (input: {path: string, method?: string}) => {
    const result = await fixture.dispatch(input) as {data?: {rows: {source_id: string}[], buckets?: {series: {source_id: string}[]}, quota_pools: {sources: string[]}[]}};
    if (drop && input.path.includes('/resources') && result.data) {
      result.data.rows = result.data.rows.filter(row => row.source_id !== 'codex-implementer');
      result.data.quota_pools = result.data.quota_pools.filter(pool => !pool.sources.includes('codex-implementer'));
      if (result.data.buckets) {
        result.data.buckets.series = result.data.buckets.series.filter(series => series.source_id !== 'codex-implementer');
        holding = true; await new Promise<void>(resolve => {release = resolve;});
      }
    }
    return result;
  };
  await boot(page, dispatch); await openAgents(page, 1440);
  await page.locator('[data-resource-row="codex-implementer"]').click();
  drop = true; await page.clock.fastForward(61000); await expect.poll(() => holding).toBe(true);
  const withdrawn = async () => {
    await expect(page.locator('[data-resource-row]')).toHaveCount(2);
    await expect(page.locator('[data-resource-row="codex-implementer"]')).toHaveCount(0);
    await expect(page.locator('[data-resource-metrics]')).toContainText('1 / 2');
    await expect(page.locator('[data-resource-metrics] .ui-metric-value').first()).toContainText('546');
    await expect(page.locator('.bar.cli-codex, .tick.cli-codex, [data-resource-chart] [data-cli="codex"]')).toHaveCount(0);
    await expect(page.locator('[data-resource-detail]')).toBeHidden();
    await expect(page.locator('[data-resource-quota]')).not.toContainText('40%');
  };
  await withdrawn(); release(); await page.waitForTimeout(200); await withdrawn();
  await context.close();
});

test('two rejected zones stop buckets until the view is reopened', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en-US', viewport: {width: 1440, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage(), fixture = tracked('en');
  const dispatch = async (input: {path: string, method?: string}) => {
    const result = await fixture.dispatch(input);
    if (input.path.includes('/resources') && input.path.includes('buckets=day')) return {status: 422, data: {error: {code: 'INVALID_PARAMS', message: 'invalid_timezone'}}};
    return result;
  };
  await boot(page, dispatch); await openAgents(page, 1440);
  expect(fixture.reads).toHaveLength(3);
  await expect(page.locator('[data-resource-meta] [data-tz]')).toHaveText('UTC');
  await expect(page.locator('[data-resource-chart]')).toHaveCount(0);
  await page.clock.fastForward(61000); await expect.poll(() => fixture.reads.length).toBe(4);
  expect(fixture.reads.at(-1)).not.toMatch(/buckets=|tz=/);
  await goView(page, 'overview'); await goView(page, 'agents');
  await expect.poll(() => fixture.reads.length).toBe(7);
  await expect(page.locator('[data-resource-chart]')).toHaveCount(0);
  await context.close();
});

type CacheRead = {
  as_of: string, window: {until: string},
  rows: {source_id: string, metrics: Record<string, {value: number | null, observed: number | null, covered: number}> | null,
    observations: number | null, calls: number | null, first_at: string | null, last_at: string | null}[],
  buckets?: {series: {source_id: string, days: {date: string, complete: boolean, tokens: Record<string, number | null>}[]}[]},
};

for (const locale of ['en', 'zh-TW'] as const) test(`idle and partial sources retain their trend until sample facts change ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width: 1440, height: 900}, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce'});
  const page = await context.newPage(), fixture = tracked(locale);
  let bucketReads = 0, idleSample = false, missingSample = false;
  let release = () => {}, hold: Promise<void> | null = null;
  const dispatch = async (input: {path: string, method?: string}) => {
    const url = new URL(input.path, 'http://fixture');
    const bucketRequest = url.pathname.endsWith('/resources') && url.searchParams.get('buckets') === 'day';
    if (bucketRequest) bucketReads++;
    const result = await fixture.dispatch(input) as {data?: CacheRead};
    if (!url.pathname.endsWith('/resources') || !result.data) return result;
    const read = result.data;
    read.as_of = read.window.until;
    const partial = read.rows.find(row => row.source_id === 'codex-implementer')!;
    const idle = read.rows.find(row => row.source_id === 'claude-reviewer')!;
    for (const key of Object.keys(partial.metrics!)) partial.metrics![key] = {value: null, observed: null, covered: 0};
    partial.metrics!.total = {value: null, observed: 120, covered: 2};
    partial.metrics!.input = {value: null, observed: 120, covered: 2};
    partial.metrics!.output = {value: null, observed: 0, covered: 2};
    partial.observations = missingSample ? 4 : 3;
    partial.first_at = '2026-10-08T01:00:00.000Z';
    partial.last_at = '2026-10-13T08:59:00.000Z';
    for (const key of Object.keys(idle.metrics!)) idle.metrics![key] = {value: null, observed: null, covered: 0};
    idle.observations = idleSample ? 1 : 0; idle.calls = idleSample ? 1 : 0;
    idle.first_at = idle.last_at = idleSample ? '2026-10-13T09:03:04.000Z' : null;
    if (idleSample) {
      idle.metrics!.total = idle.metrics!.input = {value: 42, observed: 42, covered: 1};
      idle.metrics!.output = {value: 0, observed: 0, covered: 1};
    }
    // Reordering rows and a newer collection time do not change the sample facts.
    if (fixture.reads.length % 2 === 0) read.rows.reverse();
    for (const series of read.buckets?.series || []) for (const day of series.days) {
      day.complete = day.date < '2026-10-12';
      const isPartial = series.source_id === partial.source_id;
      let total: number | null = day.complete ? 0 : null;
      if (isPartial && day.date === '2026-10-08') total = missingSample ? null : 60;
      if (!isPartial && idleSample && day.date === '2026-10-13') total = 42;
      day.tokens = {input: total, cache_read: null, cache_write: null, output: total == null ? null : 0, reasoning: null, total};
    }
    if (bucketRequest && hold) await hold;
    return result;
  };
  await boot(page, dispatch); await openAgents(page, 1440);
  const chart = page.locator('[data-resource-chart]');
  const row = page.locator('[data-resource-row="claude-reviewer"]');
  const metric = page.locator('[data-resource-metrics] .ui-metric-value').first();
  const dayValue = (date: string, cli: string) => chart.locator(`tr[data-date="${date}"] [data-cli="${cli}"]`);
  const idleFacts = async (sample: boolean) => {
    await expect(page.locator('[data-resource-meta]')).toContainText(/Oct 7.*Oct 13|10月7日.*10月13日/);
    await expect(page.locator('[data-resource-meta] [data-tz]')).toHaveText('Asia/Taipei');
    await expect(metric).toHaveText(new RegExp(`^${sample ? '162' : '120'}\\s*(Partial|部分)$`));
    await expect(page.locator('[data-resource-metrics] [data-status="partial"]')).toBeVisible();
    await expect(row.locator('[data-column="tokens"]')).toHaveText(sample ? '42' : '—');
    await expect(page.locator('[data-resource-row="codex-implementer"] [data-column="tokens"]')).toHaveText(/120\s*(Partial|部分)/);
    await expect(page.locator('[data-resource-row="codex-implementer"] [data-status="partial"]')).toBeVisible();
    for (const date of ['2026-10-07', '2026-10-08', '2026-10-09', '2026-10-10', '2026-10-11']) await expect(dayValue(date, 'claude')).toHaveText('0');
    await expect(dayValue('2026-10-12', 'claude')).toHaveText('—');
    await expect(dayValue('2026-10-13', 'claude')).toHaveText(sample ? '42' : '—');
    await expect(dayValue('2026-10-08', 'codex')).toHaveText('60');
    await expect(dayValue('2026-10-13', 'codex')).toHaveText('—');
  };
  await idleFacts(false); expect(bucketReads).toBe(1);
  await row.click();
  const inspectorTotal = page.locator('[data-resource-detail] dt', {hasText: locale === 'en' ? /^Total$/ : /^總計$/}).locator('+ dd');
  await expect(inspectorTotal).toHaveText('—');
  await chart.evaluate(node => node.setAttribute('data-consistent-pair', 'retained'));
  for (let tick = 0; tick < 3; tick++) {
    const before = fixture.reads.length;
    await page.clock.fastForward(61000);
    await expect.poll(() => fixture.reads.length).toBe(before + 1);
    await idleFacts(false);
    expect(bucketReads).toBe(1);
    await expect(chart).toHaveAttribute('data-consistent-pair', 'retained');
    await expect(inspectorTotal).toHaveText('—');
  }
  idleSample = true; hold = new Promise<void>(resolve => {release = resolve;});
  const before = fixture.reads.length;
  await page.clock.fastForward(61000);
  await expect.poll(() => fixture.reads.length).toBe(before + 2);
  expect(bucketReads).toBe(2);
  await idleFacts(false); await expect(inspectorTotal).toHaveText('—');
  await expect(chart).toHaveAttribute('data-consistent-pair', 'retained');
  release(); hold = null;
  await idleFacts(true); await expect(inspectorTotal).toHaveText('42');
  // A late sample without a total makes a formerly known day unknown, while
  // the observed subtotal and last sample time remain unchanged.
  missingSample = true; hold = new Promise<void>(resolve => {release = resolve;});
  const beforePartial = fixture.reads.length;
  await page.clock.fastForward(61000);
  await expect.poll(() => fixture.reads.length).toBe(beforePartial + 2);
  expect(bucketReads).toBe(3);
  await idleFacts(true);
  release(); hold = null;
  await expect(dayValue('2026-10-08', 'codex')).toHaveText('—');
  await expect(metric).toHaveText(/^162\s*(Partial|部分)$/); await expect(inspectorTotal).toHaveText('42');
  await expect(page.locator('[data-resource-row="codex-implementer"] [data-column="tokens"]')).toHaveText(/120\s*(Partial|部分)/);
  await context.close();
});
