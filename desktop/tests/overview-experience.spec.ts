import {mkdirSync} from 'node:fs';
import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture, demoRoute} from './demo/index.ts';
import {demoProject} from './demo/project.ts';
import {overview} from './demo/overview.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';
import {openPmPanel, openPmSection} from './pm-navigation.ts';

const root = (page: Page) => page.locator('[data-pm-overview]');
const metric = (page: Page, key: string) => root(page).locator(`[data-overview-metric="${key}"]`);
function fixture(locale = 'en') {
  const base = demoFixture({locale}), data: any = overview(demoProject(locale));
  const state = {data, cursor: 0, extraStatements: false, reportPageSize: 100, denied: false, held: null as Promise<void> | null, reads: [] as string[], writes: [] as unknown[], reportDenied: false};
  return {...base, state, dispatch: async (input: any) => {
    state.reads.push(input.path); if (input.method !== 'GET') state.writes.push(input);
    const url = new URL(input.path, 'http://fixture');
    if (url.pathname === '/events') return {status: 200, data: {
      events: Number(url.searchParams.get('after')) < state.cursor ? [{seq: state.cursor, resource_type: 'pm_evidence', resource_id: base.ids.workspaceId, kind: 'pm.evidence.updated'}] : [],
      next_cursor: state.cursor, head_cursor: state.cursor, has_more: false, sync: {checkpoint: {cursor: state.cursor, token: `demo-${state.cursor}`}},
    }};
    if (input.path.endsWith('/overview')) {
      const result = structuredClone(state.data), held = state.held, denied = state.denied; state.held = null;
      if (held) await held;
      return denied ? {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Source withdrawn'}}} : {status: 200, data: result};
    }
    if (state.reportDenied && /\/snapshots\//.test(input.path)) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Report withdrawn'}}};
    if (url.pathname.endsWith('/snapshots')) url.searchParams.set('limit', String(state.reportPageSize));
    const response = await base.dispatch({...input, path: url.pathname + url.search});
    if (state.extraStatements && url.pathname.endsWith('/plans/' + base.ids.planV3)) {
      const rows = response.data.plan.document.sections.governance.statements;
      rows.push({...rows[0], value: 'A fourth explicit purpose statement', input_id: 'extra-purpose'});
    }
    return response;
  }};
}
async function boot(page: Page, f: ReturnType<typeof fixture>, native = false) {
  await mountConversation(page, native, f.dispatch); await page.goto(demoRoute());
  await expect(metric(page, 'progress')).toContainText('3');
  await expect(page.getByRole('button', {name: /^Refresh$|^重新整理$/})).toBeEnabled();
}
async function refresh(page: Page) {const control = page.locator('.pm-main > .ui-page-header').getByRole('button', {name: /Refresh|重新整理/}); await control.focus(); await control.press('Enter');}
async function report(page: Page, id: string) {
  await openPmPanel(page, 'ui_report'); await page.locator('[data-pm-report-tools] select').selectOption(id); await page.keyboard.press('Escape');
  await expect(root(page)).toHaveAttribute('data-overview-mode', 'saved');
  await expect(page.locator('[data-overview-discuss]')).toHaveAttribute('href', new RegExp(id));
}

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 768, 390]) test(`five-second project answer ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}, colorScheme: 'light', reducedMotion: 'reduce'});
  const page = await context.newPage(), f = fixture(locale), errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => {if (m.type() === 'error' && !m.text().startsWith('Failed to load resource:')) errors.push(m.text());});
  await boot(page, f);
  await expect(root(page)).toHaveAttribute('data-overview-mode', 'live');
  await expect(metric(page, 'goal')).toContainText(f.copy.goal);
  await expect(metric(page, 'progress')).toContainText(locale === 'en' ? '3 of 8 accepted' : '8 項中 3 項已驗收');
  await expect(metric(page, 'blocker').locator('.ui-metric-value')).toHaveText('1');
  await expect(metric(page, 'decision').locator('.ui-metric-value')).toHaveText('2');
  await expect(metric(page, 'progress')).toContainText(locale === 'en' ? '+3d' : '+3 天');
  await expect(page.locator('[data-overview-meta]')).not.toContainText('v3');
  await expect(root(page).locator('[data-charter-section]')).toHaveCount(6);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await root(page).innerText()).not.toMatch(/\b(undefined|null|NaN|ov_\w+)\b/);
  if (width === 1440) {
    const strip = await root(page).locator('[data-overview-status]').boundingBox(); expect(strip!.y + strip!.height).toBeLessThan(430);
    for (const key of ['goal', 'progress', 'blocker', 'decision']) await expect(metric(page, key)).toBeInViewport({ratio: 1});
    await expect(root(page).locator('[data-overview-attention]')).toBeInViewport({ratio: 1});
  }
  const dir = `${process.env.TMPDIR}/shots`; mkdirSync(dir, {recursive: true});
  await page.screenshot({path: `${dir}/overview-${locale}-${width}.png`, fullPage: true});
  if (width === 1440) {await page.screenshot({path: `${dir}/overview-${locale}-${width}-fold.png`});}
  if (width === 1440 && locale === 'en') {
    await page.emulateMedia({colorScheme: 'dark'}); await page.screenshot({path: `${dir}/overview-en-1440-dark.png`, fullPage: true});
  }
  expect(errors).toEqual([]); expect(f.state.writes).toEqual([]); await context.close();
});

for (const native of [false, true]) test(`discussion links bind exact identities and saved report ${native ? 'native' : 'web'}`, async ({page}) => {
  const f = fixture(); await boot(page, f, native);
  const header = await page.locator('[data-overview-discuss]').getAttribute('href');
  expect(parseDiscussRefs(header!.split('?')[1]).refs).toEqual([{kind: 'plan_revision', id: f.ids.planV3}]);
  for (const row of f.state.data.attention) {
    const link = root(page).locator(`[data-overview-row="${row.key}"] a`), href = await link.getAttribute('href');
    const query = new URLSearchParams(href!.split('?')[1]);
    expect(parseDiscussRefs(query).refs).toEqual([row.ref]); expect(query.get('title')).toBe(row.title); expect(query.get('from')).toBe('overview');
  }
  await report(page, f.ids.snap1);
  const query = new URLSearchParams((await page.locator('[data-overview-discuss]').getAttribute('href'))!.split('?')[1]);
  expect(parseDiscussRefs(query).refs).toEqual([{kind: 'plan_revision', id: f.ids.planV2}, {kind: 'snapshot', id: f.ids.snap1}]);
  expect(query.get('report')).toBe(f.ids.snap1);
  await expect(root(page).locator('[data-overview-attention]')).toHaveCount(0);
  await expect(root(page)).not.toContainText('3 of 8 accepted');
  await expect(root(page).locator('[data-overview-delivery]')).toHaveCount(0);
  await expect(metric(page, 'progress')).toContainText('4 requirements');
  await root(page).getByRole('button', {name: 'Back to live', exact: true}).click();
  await expect(metric(page, 'progress')).toContainText('3 of 8 accepted'); expect(f.state.writes).toEqual([]);
});

test('attention opens exact governance, requirement and work selection; Full plan returns focus', async ({page}) => {
  const f = fixture(); await boot(page, f);
  const row = f.state.data.attention[0]; await root(page).locator(`[data-overview-open="${row.key}"]`).click();
  await expect(page.locator('[data-pm-view="issues"]')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator(`[data-pm-governance-row="${row.ref.id}"] button`)).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator(`[data-pm-governance-detail="${row.ref.id}"]`)).toContainText(row.title);
  await openPmSection(page, 'overview');
  f.state.data.attention = [{...row, key: 'quality-revoked', kind: 'deviation', title: f.copy.requirements[0].text, ref: {kind: 'requirement', id: f.ids.requirements.email, revision_id: f.ids.planV3}}];
  await refresh(page); await root(page).locator('[data-overview-open="quality-revoked"]').click();
  await expect(page.locator('[data-pm-view="requirements"]')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator(`[data-rtm-toggle="${f.ids.requirements.email}"]`)).toHaveAttribute('aria-expanded', 'true');
  await openPmSection(page, 'overview');
  await root(page).locator(`[data-overview-delivery="${f.ids.work.email}"] button`).click();
  await expect(page.locator('[data-rtm-detail]')).toBeVisible(); await expect(page.locator('[data-rtm-detail]')).toContainText(f.copy.work.email);
  await openPmSection(page, 'overview');
  const plan = root(page).locator('[data-charter-plan]'); await plan.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('[data-overview-plan]')).toContainText(f.copy.requirements[0].text);
  await page.keyboard.press('Escape'); await expect(plan).toBeFocused(); expect(f.state.writes).toEqual([]);
});

test('unknown and partial remain distinct; no raw reasons or invented ages', async ({page}) => {
  const f = fixture(); await boot(page, f);
  f.state.data.progress = null; f.state.data.plan.goal = null;
  f.state.data.attention_counts = {blocker: null, decision: null, deviation: null, risk: 0};
  f.state.data.unknown = [{field: 'progress', reason: 'no_adopted_plan'}, {field: 'plan.goal', reason: 'goal_unconfirmed'}, {field: 'other', reason: 'future_reason'}, {field: 'sources', reason: 'source_limit'}];
  f.state.data.attention[0].since = null; f.state.data.coverage.complete = false;
  f.state.data.coverage.sources.lifecycle_inflight = {complete: false, next_cursor: null, omitted: 2};
  f.state.data.recent_deliveries_complete = false; f.state.data.recent_deliveries[0].state = 'rolled_back';
  await refresh(page);
  await expect(metric(page, 'progress')).toContainText('—'); await expect(metric(page, 'progress')).toContainText('Unknown');
  await expect(metric(page, 'progress')).toContainText('No adopted plan'); await expect(metric(page, 'goal')).toContainText('Unconfirmed');
  for (const key of ['blocker', 'decision']) await expect(metric(page, key).locator('.ui-metric-value')).toHaveText('—Unknown');
  await expect(root(page).locator('[data-overview-row]').first().locator('[data-overview-age]')).toHaveCount(0);
  await expect(root(page).getByRole('heading', {name: 'Recent deliveries (loaded)', exact: true})).toBeVisible();
  await expect(root(page).locator('[data-status="rolled_back"]')).toHaveClass(/tone-warn/);
  expect(await root(page).innerText()).not.toContain('future_reason');
  await root(page).locator(':scope > div > details > summary').click();
  await expect(root(page)).toContainText('2 hidden', {useInnerText: true}); await expect(root(page)).toContainText('Not available', {useInnerText: true});
  f.state.data.progress = {...overview(demoProject()).progress, accepted: null, in_review: null, in_progress: null, blocked: null, not_started: null, complete: false};
  f.state.data.attention = []; await refresh(page);
  await expect(metric(page, 'progress')).toContainText('8 requirements'); await expect(metric(page, 'progress')).toContainText('Partial');
  await expect(root(page)).toContainText('Nothing loaded needs you · sources incomplete');
  await expect(metric(page, 'progress').locator('.ui-metric-bar')).toHaveCount(0);
});

test('source withdrawal clears values and plan inspector; saved reports reauthorize', async ({page}) => {
  const f = fixture(); await boot(page, f); await root(page).locator('[data-charter-plan]').click();
  f.state.denied = true; await refresh(page);
  await expect(root(page).locator('[data-overview-row]')).toHaveCount(0);
  await expect(page.locator('[data-overview-plan]')).toHaveCount(0);
  await expect(metric(page, 'progress')).not.toContainText('3 of 8 accepted'); await expect(root(page)).not.toContainText(f.copy.goal);
  f.state.denied = false; await refresh(page); await expect(metric(page, 'progress')).toContainText('3 of 8 accepted');
  await report(page, f.ids.snap2); f.state.reportDenied = true; await refresh(page);
  await expect(root(page)).not.toContainText(f.copy.goal); await expect(root(page).locator('[data-charter-section]')).toHaveCount(0);
});


test('late live response cannot restore withdrawn data', async ({page}) => {
  const f = fixture(); await boot(page, f); await report(page, f.ids.snap1);
  let release!: () => void;
  f.state.held = new Promise<void>(resolve => {release = resolve;});
  const before = f.state.reads.filter(path => path.endsWith('/overview')).length;
  await root(page).getByRole('button', {name: 'Back to live', exact: true}).click();
  await expect.poll(() => f.state.reads.filter(path => path.endsWith('/overview')).length).toBe(before + 1);
  f.state.denied = true;
  try {
    await refresh(page); await expect(metric(page, 'progress')).toContainText('—Unknown');
    await expect(root(page)).not.toContainText(f.copy.goal);
  } finally {release();}
  await expect(page.getByRole('button', {name: 'Refresh', exact: true})).toBeEnabled();
  await expect(root(page).locator('[data-overview-row]')).toHaveCount(0);
  await expect(metric(page, 'progress')).not.toContainText('3 of 8 accepted'); expect(f.state.writes).toEqual([]);
});

test('background updates retain reading rows, charter expansion and plan reader', async ({page}) => {
  const f = fixture(); f.state.extraStatements = true; await boot(page, f);
  const more = root(page).locator('[data-charter-section="definition"] button');
  await more.click(); await expect(more).toHaveAttribute('aria-expanded', 'true');
  const row = root(page).locator('[data-overview-open]').nth(1); await row.focus();
  await row.evaluate(node => node.setAttribute('data-reading-marker', 'retained'));
  const y = (await row.boundingBox())!.y;
  f.state.data.progress.accepted = 2; f.state.data.progress.in_review = 2;
  f.state.data.attention.reverse(); f.state.cursor++;
  await expect(metric(page, 'progress')).toContainText('2 of 8 accepted');
  await expect(page.locator('[data-reading-marker=retained]')).toBeFocused();
  expect((await page.locator('[data-reading-marker=retained]').boundingBox())!.y).toBe(y);
  await expect(more).toHaveAttribute('aria-expanded', 'true');
  await root(page).locator('[data-charter-plan]').click();
  const reader = page.locator('[data-overview-plan]'); await expect(reader).toBeVisible();
  const readCount = f.state.reads.filter(path => path.endsWith('/overview')).length;
  f.state.data.attention_counts.decision = 3; f.state.cursor++;
  await expect.poll(() => f.state.reads.filter(path => path.endsWith('/overview')).length).toBeGreaterThan(readCount);
  await expect(reader).toBeVisible(); await page.keyboard.press('Escape');
  await openPmSection(page, 'requirements'); await openPmSection(page, 'overview');
  await expect(more).toHaveAttribute('aria-expanded', 'true'); expect(f.state.writes).toEqual([]);
});

test('milestone deviation opens its exact context in Issues; withdrawal closes it', async ({page}) => {
  const f = fixture(); await boot(page, f);
  const deviation = f.state.data.attention.find((row: any) => row.kind === 'deviation');
  await root(page).locator(`[data-overview-open="${deviation.key}"]`).click();
  await expect(page.locator('[data-pm-view=issues]')).toHaveAttribute('aria-current', 'page');
  const detail = page.locator('[data-overview-related]'); await expect(detail).toContainText(deviation.title);
  const query = new URLSearchParams((await detail.getByRole('link', {name: 'Discuss', exact: true}).getAttribute('href'))!.split('?')[1]);
  expect(parseDiscussRefs(query).refs).toEqual([deviation.ref]);
  f.state.data.attention = []; f.state.cursor++;
  await expect(detail).toHaveCount(0); expect(f.state.writes).toEqual([]);
});


test('report pages survive refresh and comparisons reauthorize both snapshots', async ({page}) => {
  const f = fixture(); f.state.reportPageSize = 1; await boot(page, f);
  await report(page, f.ids.snap1);
  await openPmPanel(page, 'ui_report');
  await page.getByRole('button', {name: 'Load more summary versions', exact: true}).click();
  await page.keyboard.press('Escape');
  await expect(root(page).getByRole('button', {name: 'Next report', exact: true})).toBeEnabled();
  const before = f.state.reads.filter(path => path.includes('/snapshots?')).length; f.state.cursor++;
  await expect.poll(() => f.state.reads.filter(path => path.includes('/snapshots?')).length).toBeGreaterThan(before);
  await expect(root(page).getByRole('button', {name: 'Next report', exact: true})).toBeEnabled();
  await root(page).getByRole('button', {name: 'Next report', exact: true}).click();
  await expect(root(page).locator('.pm-overview-saved')).toContainText('Viewing report #2');
  const readStart = f.state.reads.length;
  await root(page).getByRole('button', {name: 'Compare', exact: true}).click();
  await expect(page.locator('.pm-charter-comparison')).toBeVisible();
  expect(f.state.reads.slice(readStart).some(path => path.endsWith('/snapshots/' + f.ids.snap1))).toBe(true);
  expect(f.state.reads.slice(readStart).some(path => path.endsWith('/snapshots/' + f.ids.snap2))).toBe(true);
  await page.keyboard.press('Escape'); expect(f.state.writes).toEqual([]);
});
