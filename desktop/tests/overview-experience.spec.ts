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
  const state = {data, overviewCap: true, cursor: 0, events: [] as any[], reportPatch: null as null | ((snapshot: any) => void), heldReport: null as Promise<void> | null, extraStatements: false, reportPageSize: 100, denied: false, held: null as Promise<void> | null, reads: [] as string[], writes: [] as unknown[], reportDenied: false};
  return {...base, state, dispatch: async (input: any) => {
    state.reads.push(input.path); if (input.method !== 'GET') state.writes.push(input);
    const url = new URL(input.path, 'http://fixture');
    if (url.pathname === '/events') return {status: 200, data: {
      events: state.events.length ? state.events.filter(event => event.seq > Number(url.searchParams.get('after'))) : Number(url.searchParams.get('after')) < state.cursor ? [{seq: state.cursor, resource_type: 'pm_evidence', resource_id: base.ids.workspaceId, kind: 'pm.evidence.updated'}] : [],
      next_cursor: state.cursor, head_cursor: state.cursor, has_more: false, sync: {checkpoint: {cursor: state.cursor, token: `demo-${state.cursor}`}},
    }};
    if (input.path.endsWith('/overview')) {
      const result = structuredClone(state.data), held = state.held, denied = state.denied; state.held = null;
      if (held) await held;
      return denied ? {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Source withdrawn'}}} : {status: 200, data: result};
    }
    if (state.heldReport && /\/snapshots\//.test(input.path)) {const held = state.heldReport; state.heldReport = null; await held;}
    if (state.reportDenied && /\/snapshots\//.test(input.path)) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Report withdrawn'}}};
    if (url.pathname.endsWith('/snapshots')) url.searchParams.set('limit', String(state.reportPageSize));
    const response = await base.dispatch({...input, path: url.pathname + url.search});
    if (state.extraStatements && url.pathname.endsWith('/plans/' + base.ids.planV3)) {
      const rows = response.data.plan.document.sections.governance.statements;
      rows.push({...rows[0], value: 'A fourth explicit purpose statement', input_id: 'extra-purpose'});
    }
    if (!state.overviewCap && ['/bootstrap', '/capabilities'].includes(url.pathname)) delete (url.pathname === '/bootstrap' ? response.data.capabilities : response.data).features.pm_overview;
    if (response.data?.snapshot && state.reportPatch) state.reportPatch(response.data.snapshot);
    return response;
  }};
}
async function boot(page: Page, f: ReturnType<typeof fixture>, native = false) {
  await mountConversation(page, native, f.dispatch); await page.goto(demoRoute());
  await expect(metric(page, 'progress')).toContainText('3');
  await expect(page.getByRole('button', {name: /^Refresh$|^重新整理$/})).toBeEnabled();
}
async function refresh(page: Page) {
  const control = page.locator('.pm-main > .ui-page-header').getByRole('button', {name: /Refresh|重新整理/});
  // Overview may finish before the other mounted projections in this workspace read.
  await expect(control).toBeEnabled(); await control.focus(); await control.press('Enter');
}
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
  if (width < 800) {
    const discuss = page.locator('[data-overview-discuss]');
    const bounds = await discuss.boundingBox();
    expect(bounds!.height).toBeLessThan(60);
    // All header actions remain reachable in the row's own scroll area.
    await page.locator('[data-pm-refresh]').focus();
    expect(await page.locator('[data-pm-refresh]').evaluate(node => {
      const box = node.getBoundingClientRect(), row = node.parentElement!.getBoundingClientRect();
      const target = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
      // Scroll offsets round to pixels while translated label widths can be fractional.
      return box.left >= row.left - 1 && box.right <= row.right + 1 && node.contains(target);
    })).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  expect(await root(page).innerText()).not.toMatch(/\b(undefined|null|NaN|ov_\w+)\b/);
  if (width === 1440) {
    const strip = await root(page).locator('[data-overview-status]').boundingBox(); expect(strip!.y + strip!.height).toBeLessThan(430);
    for (const key of ['goal', 'progress', 'blocker', 'decision']) await expect(metric(page, key)).toBeInViewport({ratio: 1});
    await expect(root(page).locator('[data-overview-attention]')).toBeInViewport({ratio: 1});
  }
  const dir = `${process.env.TMPDIR}/shots2`; mkdirSync(dir, {recursive: true});
  await page.screenshot({path: `${dir}/overview-${locale}-${width}.png`, fullPage: true});
  if (width === 1440) {await page.screenshot({path: `${dir}/overview-${locale}-${width}-fold.png`});}
  if (width === 1440 && locale === 'en') {
    await page.emulateMedia({colorScheme: 'dark'}); await page.screenshot({path: `${dir}/overview-en-1440-dark.png`, fullPage: true}); await page.emulateMedia({colorScheme: 'light'});
  }
  const details = root(page).locator(':scope > div > details'); await details.locator(':scope > summary').click();
  expect(await root(page).innerText()).not.toMatch(/\b(undefined|null|NaN|ov_\w+)\b/);
  await expect(details.locator('[data-overview-coverage]')).toContainText(locale === 'en' ? 'Plan · Complete' : '計畫 · 完整');
  await expect(details.locator('details')).not.toHaveAttribute('open', ''); await details.locator(':scope > summary').click();
  if (width === 1440) {await page.emulateMedia({media: 'print'}); await page.screenshot({path: `${dir}/overview-${locale}-print-live.png`, fullPage: true}); await page.emulateMedia({media: 'screen'});}
  f.state.reportPatch = saved => {saved.visibility.restricted = true; saved.coverage.work_truncated = true;};
  await report(page, f.ids.snap2); await expect(root(page).locator('.pm-overview-saved [data-status=partial]')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const arrows = root(page).locator('.pm-overview-report-arrows button'), first = (await arrows.nth(0).boundingBox())!, second = (await arrows.nth(1).boundingBox())!;
  expect(first.y).toBe(second.y);
  await page.screenshot({path: `${dir}/overview-${locale}-${width}-saved-partial.png`, fullPage: true});
  await root(page).getByRole('button', {name: locale === 'en' ? 'Compare' : '比較', exact: true}).click();
  await expect(page.locator('.pm-charter-comparison')).toBeVisible();
  await page.screenshot({path: `${dir}/overview-${locale}-${width}-compare.png`}); await page.keyboard.press('Escape');
  if (width === 1440) {await page.emulateMedia({media: 'print'}); await page.screenshot({path: `${dir}/overview-${locale}-print-saved.png`, fullPage: true});}
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
  await page.getByRole('button', {name: 'Load more report versions', exact: true}).click();
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


test('event bursts coalesce projection reads without rebuilding publication', async ({page}) => {
  const f = fixture(); await boot(page, f); await openPmSection(page, 'requirements', 'ui_publication');
  const mappings = page.locator('[data-pm-issue-mappings]'); await expect(mappings).toBeVisible();
  await mappings.evaluate(node => { (window as any).removedMappings = 0;
    new MutationObserver(changes => {for (const change of changes) (window as any).removedMappings += change.removedNodes.length;}).observe(node, {childList: true, subtree: true}); });
  for (const kind of ['session', 'task', 'command', 'operation', 'work_item', 'deployment', 'integration']) {
    const start = f.state.reads.length;
    f.state.events = Array.from({length: 20}, (_, i) => ({seq: f.state.cursor + i + 1, resource_type: kind, resource_id: 'fixture-event', kind: kind + '.updated'}));
    f.state.cursor += 20;
    await expect.poll(() => f.state.reads.slice(start).some(path => path.startsWith(`/events?after=${f.state.cursor}&`))).toBe(true);
    const reads = f.state.reads.slice(start);
    expect(reads.filter(path => path.endsWith('/overview')).length).toBeLessThanOrEqual(['session', 'task', 'command'].includes(kind) ? 0 : 2);
    expect(reads.filter(path => path === '/pm/workspaces')).toHaveLength(0);
    expect(await page.evaluate(() => (window as any).removedMappings)).toBe(0);
  }
  expect(f.state.writes).toEqual([]);
});

test('an event starts a newer withdrawal read while an earlier projection is held', async ({page}) => {
  const f = fixture(); await boot(page, f);
  let release!: () => void;
  f.state.held = new Promise<void>(resolve => {release = resolve;});
  const before = f.state.reads.filter(path => path.endsWith('/overview')).length;
  await refresh(page);
  await expect.poll(() => f.state.reads.filter(path => path.endsWith('/overview')).length).toBe(before + 1);
  f.state.denied = true;
  f.state.events = [{seq: ++f.state.cursor, resource_type: 'operation', resource_id: 'fixture-operation', kind: 'operation.updated'}];
  try {
    await expect(metric(page, 'progress')).toContainText('—Unknown');
    expect(f.state.reads.filter(path => path.endsWith('/overview')).length).toBeGreaterThanOrEqual(before + 2);
    // An authorization failure withdraws data but must not advance the event ACK.
    expect(f.state.reads.some(path => path.startsWith(`/events?after=${f.state.cursor}&`))).toBe(false);
  } finally {release();}
  await expect(page.getByRole('button', {name: 'Refresh', exact: true})).toBeEnabled();
  await expect(root(page)).not.toContainText(f.copy.goal); expect(f.state.writes).toEqual([]);
});


test('event ACK waits for the shared projection read to finish', async ({page}) => {
  const f = fixture(); await boot(page, f);
  let release!: () => void;
  f.state.held = new Promise<void>(resolve => {release = resolve;});
  const before = f.state.reads.filter(path => path.endsWith('/overview')).length;
  f.state.events = Array.from({length: 20}, (_, i) => ({seq: i + 1, resource_type: 'operation', resource_id: 'fixture-operation', kind: 'operation.updated'}));
  f.state.cursor = 20;
  try {
    await expect.poll(() => f.state.reads.filter(path => path.endsWith('/overview')).length).toBe(before + 1);
    expect(f.state.reads.some(path => path.startsWith('/events?after=20&'))).toBe(false);
  } finally {release();}
  await expect.poll(() => f.state.reads.some(path => path.startsWith('/events?after=20&'))).toBe(true);
  expect(f.state.reads.filter(path => path.endsWith('/overview')).length).toBe(before + 1); expect(f.state.writes).toEqual([]);
});


for (const restriction of ['restricted', 'filtered', 'work_truncated']) test(`saved report discloses ${restriction} and incomplete history`, async ({page}) => {
  const f = fixture(); f.state.reportPageSize = 1;
  f.state.reportPatch = saved => {if (restriction.endsWith('_truncated')) saved.coverage[restriction] = true; else saved.visibility[restriction] = true;};
  await boot(page, f); await report(page, f.ids.snap1);
  await expect(root(page).locator('.pm-overview-saved [data-status=partial]')).toBeVisible();
  await expect(root(page).locator('[data-overview-status] [data-status=partial]')).toBeVisible();
  await expect(root(page).locator('[data-overview-history-partial]')).toHaveText('More reports available');
  await expect(metric(page, 'goal')).toContainText(f.copy.goalV2);
  await root(page).locator(':scope > div > details > summary').click();
  await expect(root(page)).toContainText(restriction === 'restricted' ? 'Restricted sources' : restriction === 'filtered' ? 'Some sources hidden' : 'Partial', {useInnerText: true});
  await expect(root(page)).not.toContainText('3 of 8 accepted'); expect(f.state.writes).toEqual([]);
});

test('decisions card distinguishes unknown, missing and truncated items', async ({page}) => {
  const f = fixture(); f.state.data.attention = f.state.data.attention.filter((row: any) => row.kind !== 'decision');
  f.state.data.attention_counts.decision = null; await boot(page, f);
  const card = root(page).locator('[data-charter-section=decisions]');
  await expect(card.locator('[data-status=unknown]')).toBeVisible(); await expect(card).not.toContainText('None recorded');
  f.state.data.attention_counts.decision = 3; f.state.data.attention_truncated = true; await refresh(page);
  await expect(card.locator('[data-status=partial]')).toBeVisible();
  await expect(card.getByRole('link', {name: '+3 more', exact: true})).toHaveAttribute('href', `#/pm/${f.ids.workspaceId}/issues`);
  expect(f.state.writes).toEqual([]);
});

for (const locale of ['en', 'zh-TW']) test(`selected report has a loading state ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale}), page = await context.newPage(), f = fixture(locale); await boot(page, f);
  await openPmPanel(page, 'ui_report'); await expect(page.locator('[data-pm-report-tools] select')).toBeEnabled();
  let release!: () => void; f.state.heldReport = new Promise<void>(resolve => {release = resolve;});
  try {
    await page.locator('[data-pm-report-tools] select').selectOption(f.ids.snap1); await page.keyboard.press('Escape');
    await expect(root(page).locator('.ui-skeleton')).toBeVisible();
    await expect(root(page).getByRole('status').filter({hasText: locale === 'en' ? 'Loading report…' : '正在載入報告…'})).toBeVisible();
    await expect(root(page)).not.toContainText(locale === 'en' ? 'Select a report in Versions.' : '請從版本選取報告。');
  } finally {release();}
  await expect(metric(page, 'goal')).toContainText(f.copy.goalV2); await context.close();
});

test('epoch dates are local while milestone calendar dates stay fixed', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en', timezoneId: 'Asia/Taipei'}), page = await context.newPage(), f = fixture();
  f.state.data.plan.adopted_at = Date.parse('2026-10-11T20:00:00Z') / 1000;
  f.state.reportPatch = saved => {saved.as_of = Date.parse('2026-10-12T20:00:00Z') / 1000;};
  await boot(page, f); await expect(page.locator('[data-overview-meta]')).toContainText('adopted Oct 12');
  await expect(root(page).locator('[data-overview-milestone]').first()).toContainText('Sep 28');
  await report(page, f.ids.snap2); await expect(page.locator('[data-overview-meta] time')).toHaveAttribute('title', /October 13, 2026/); await context.close();
});

test('saved goal reasons match confirmed and unconfirmed plan inputs', async ({page}) => {
  const f = fixture(); f.state.reportPatch = saved => {saved.baseline.document.selected_inputs = [];};
  await boot(page, f); await report(page, f.ids.snap1); await expect(metric(page, 'goal')).toContainText('Not set');
  f.state.reportPatch = saved => {saved.baseline.document.selected_inputs[0].status = 'unresolved';};
  await refresh(page); await expect(metric(page, 'goal').locator('[data-status=unconfirmed]')).toBeVisible();
  expect(f.state.writes).toEqual([]);
});


for (const mode of ['live', 'saved', 'fallback']) test(`print includes the fixed or live charter ${mode}`, async ({page}) => {
  const f = fixture(); f.state.overviewCap = mode !== 'fallback';
  await mountConversation(page, false, f.dispatch); await page.goto(demoRoute());
  await expect(page.getByRole('button', {name: 'Refresh', exact: true})).toBeEnabled();
  if (mode === 'saved') await report(page, f.ids.snap2);
  await page.emulateMedia({media: 'print'});
  await expect(page.locator('[data-pm-region=overview]')).toBeVisible();
  if (mode !== 'fallback') {await expect(root(page).locator('[data-overview-status]')).toBeVisible(); await expect(root(page).locator('.pm-overview-charter-grid')).toBeVisible();}
  else await expect(page.locator('.pm-charter-report')).toBeVisible();
  await expect(page.locator('[data-charter-section=definition]')).toContainText(f.copy.goal);
  await expect(page.locator('[data-charter-section=scope]')).toBeVisible(); expect(f.state.writes).toEqual([]);
});

for (const target of ['attention', 'blocker', 'milestone', 'delivery']) test(`refresh retains the ${target} link identity and focus`, async ({page}) => {
  const f = fixture(); await boot(page, f);
  const link = target === 'attention' ? root(page).locator('[data-overview-row]').nth(1).locator('a')
    : target === 'blocker' ? metric(page, 'blocker').locator('button')
    : root(page).locator(target === 'milestone' ? '[data-overview-milestone] button' : '[data-overview-delivery] button').first();
  const id = await link.getAttribute('data-overview-link'); await link.focus();
  await link.evaluate(node => {(window as any).readingLink = node;});
  f.state.data.attention_counts.blocker++;
  f.state.data.milestones[0].status = 'in_progress'; f.state.data.recent_deliveries[0].state = 'rolled_back'; f.state.cursor++;
  await expect(metric(page, 'blocker').locator('.ui-metric-value')).toHaveText('2');
  await expect(root(page).locator(`[data-overview-link="${id}"]`)).toBeFocused();
  if (target === 'attention') expect(await page.evaluate(() => document.activeElement === (window as any).readingLink)).toBe(true);
  expect(f.state.writes).toEqual([]);
});

test('cached attention ages advance without moving the reading row', async ({page}) => {
  const f = fixture(); await boot(page, f);
  const row = root(page).locator('[data-overview-row]').first(), control = row.locator('button');
  await expect(row.locator('[data-overview-age]')).toHaveText('5d'); await control.focus();
  await row.evaluate(node => {(window as any).readingRow = node;});
  const y = (await row.boundingBox())!.y;
  f.state.data.as_of += 3 * 86400; f.state.cursor++;
  await expect(row.locator('[data-overview-age]')).toHaveText('8d'); await expect(control).toBeFocused();
  expect(await row.evaluate(node => node === (window as any).readingRow)).toBe(true); expect((await row.boundingBox())!.y).toBe(y);
  f.state.data.as_of += 3 * 86400; f.state.data.attention_counts.decision++; f.state.cursor++;
  await expect(row.locator('[data-overview-age]')).toHaveText('11d'); await expect(control).toBeFocused(); expect(f.state.writes).toEqual([]);
});

test('390px navigation selects visible requirement and work after an earlier visit', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844}); const f = fixture();
  f.state.data.attention[0].ref = {kind: 'requirement', id: f.ids.requirements.unlock, revision_id: f.ids.planV3};
  await boot(page, f); await openPmSection(page, 'requirements'); await expect(page.locator('[data-rtm-requirement]').first()).toBeVisible();
  await openPmSection(page, 'overview'); await root(page).locator('[data-overview-open]').first().click();
  const row = page.locator(`[data-rtm-requirement="${f.ids.requirements.unlock}"]`);
  await expect(row).toHaveAttribute('aria-current', 'true'); await expect(row).toBeInViewport({ratio: 1});
  const bounds = (await row.boundingBox())!; expect(bounds.y).toBeGreaterThanOrEqual(0); expect(bounds.y + bounds.height).toBeLessThanOrEqual(844);
  expect(await row.evaluate(node => getComputedStyle(node).boxShadow)).not.toBe('none');
  await expect(row).toBeFocused();
  await openPmSection(page, 'overview'); await root(page).locator('[data-overview-delivery] button').first().click();
  await expect(page.locator('[data-rtm-detail] > header h2')).toBeFocused(); await expect(page.locator('[data-rtm-detail] > header h2')).toBeInViewport({ratio: 1});
  // The shared mobile sheet overlays the shell. Its focused title must be
  // unobscured, rather than positioned below the shell's old inline top bar.
  const detail = page.locator('[data-rtm-detail]');
  await expect(detail).toHaveAttribute('aria-modal', 'true');
  expect(await detail.locator(':scope > header h2').evaluate(node => {
    const box = node.getBoundingClientRect();
    return document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.closest('[data-rtm-detail]') === node.closest('[data-rtm-detail]');
  })).toBe(true);
  expect(f.state.writes).toEqual([]);
});

for (const view of ['requirements', 'dependencies']) test(`Overview restores scroll after ${view}`, async ({page}) => {
  await page.setViewportSize({width: 390, height: 844}); const f = fixture(); await boot(page, f);
  await root(page).locator('[data-charter-section=decisions]').scrollIntoViewIfNeeded();
  const before = await page.evaluate(() => scrollY); expect(before).toBeGreaterThan(500);
  await openPmSection(page, view); await openPmSection(page, 'overview');
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(before); expect(f.state.writes).toEqual([]);
});

for (const width of [390, 1440]) test(`related inspector Escape returns focus to Issues heading ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900}); const f = fixture(); await boot(page, f);
  const row = f.state.data.attention.find((item: any) => item.kind === 'deviation');
  await root(page).locator(`[data-overview-open="${row.key}"]`).click(); await expect(page.locator('[data-overview-related]')).toBeVisible();
  await page.keyboard.press('Escape'); await expect(page.locator('[data-overview-related]')).toHaveCount(0);
  await expect(page.locator('.pm-main > .ui-page-header h1')).toBeFocused(); expect(f.state.writes).toEqual([]);
});


test('requirement, finding, unbound and strip discussion links keep exact context', async ({page}) => {
  const f = fixture(), original = f.state.data.attention[0];
  const refs = [{kind: 'requirement', id: f.ids.requirements.email, revision_id: f.ids.planV3},
    {kind: 'finding', id: 'pmfn_' + '1'.repeat(32), observation_id: 'pmgh_' + '2'.repeat(32), document_digest: 'a'.repeat(64)}, null];
  f.state.data.attention = [...refs.map((ref, i) => ({...original, key: `ref-${i}`, title: `Context ${i}`, ref})), ...f.state.data.attention.slice(1)];
  await boot(page, f);
  for (let i = 0; i < refs.length; i++) {
    const query = new URLSearchParams((await root(page).locator(`[data-overview-row="ref-${i}"] a`).getAttribute('href'))!.split('?')[1]);
    expect(parseDiscussRefs(query).refs).toEqual(refs[i] ? [refs[i]] : []); expect(query.get('title')).toBe(`Context ${i}`); expect(query.get('from')).toBe('overview');
  }
  const decision = f.state.data.attention.find((row: any) => row.kind === 'decision');
  const query = new URLSearchParams((await metric(page, 'decision').getByRole('link', {name: 'Discuss', exact: true}).getAttribute('href'))!.split('?')[1]);
  expect(parseDiscussRefs(query).refs).toEqual([decision.ref]); expect(query.get('title')).toBe(decision.title); expect(query.get('from')).toBe('overview');
  f.state.data.plan = null; f.state.data.progress = null; await refresh(page);
  await expect(page.locator('[data-overview-discuss]')).toHaveAttribute('href', `#/pm/${f.ids.workspaceId}/discussions/new?from=overview`);
  expect(f.state.writes).toEqual([]);
});


for (const width of [390, 1440]) test(`sidebar milestone opens the matching Schedule inspector ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900}); const f = fixture(); await boot(page, f);
  const milestone = f.state.data.milestones.at(-1);
  await root(page).locator(`[data-overview-milestone="${milestone.milestone_id}"] button`).click();
  await expect(page.locator('[data-pm-view=schedule]')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('[data-schedule-inspector]')).toContainText(milestone.title);
  const selected = page.locator('[data-schedule-select][aria-selected=true]'); await expect(selected).toHaveAttribute('aria-label', milestone.title);
  const id = await selected.getAttribute('data-schedule-select'); await page.keyboard.press('Escape');
  const row = page.locator(`[data-schedule-select="${id}"]`); await expect(row).toBeFocused(); await expect(row).toBeInViewport(); expect(f.state.writes).toEqual([]);
});
