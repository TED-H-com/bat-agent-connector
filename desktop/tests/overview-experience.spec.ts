import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture, demoRoute} from './demo/index.ts';
import {demoProject} from './demo/project.ts';
import {overview} from './demo/overview.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';
import {findingName} from '../src/ui/finding-name.js';
import {openPmPanel, openPmSection} from './pm-navigation.ts';

const colorScheme = process.env.BATC_UI_TEST_SCHEME === 'dark' ? 'dark' : 'light';
test.use({colorScheme});
const root = (page: Page) => page.locator('[data-pm-overview]');
const metric = (page: Page, key: string) => root(page).locator(`[data-overview-metric="${key}"]`);
function fixture(locale = 'en') {
  const base = demoFixture({locale}), data: any = overview(demoProject(locale));
  Object.assign(data, {recent_decisions: [], recent_decisions_count: 0, recent_decisions_complete: true, recent_decisions_truncated: false});
  const state = {data, overviewCap: true, cursor: 0, events: [] as any[], planPatch: null as null | ((plan: any) => void), reportPatch: null as null | ((snapshot: any) => void), heldReport: null as Promise<void> | null, extraStatements: false, reportPageSize: 100, denied: false, held: null as Promise<void> | null, reads: [] as string[], writes: [] as unknown[], reportDenied: false};
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
    if (response.data?.plan && state.planPatch) state.planPatch(response.data.plan);
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

function assessedDelivery(f: ReturnType<typeof fixture>, seed = 'a') {
  const row = f.state.data.recent_deliveries.find((item: any) => item.state === 'accepted');
  row.assessment_ref = {review_id: 'pmqr_' + seed.repeat(32), operation_id: 'op_' + seed.repeat(32),
    version: 2, document_digest: seed.repeat(64), candidate_commit: seed.repeat(40)};
  return row;
}

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) test(`delivery preserves its original assessment source ${locale} ${native ? 'IPC' : 'HTTP'}`, async ({browser, baseURL}, testInfo) => {
  const context = await browser.newContext({baseURL, locale, colorScheme, viewport: {width: native ? 390 : 1440, height: 900}});
  const page = await context.newPage(), f = fixture(locale), row = assessedDelivery(f);
  await boot(page, f, native);
  const card = root(page).locator(`[data-overview-delivery="${row.work_item_id}"]`), source = card.locator('[data-overview-assessment]');
  await source.locator('summary').click();
  await expect(source.locator('summary')).toHaveText(locale === 'en' ? 'Original assessment' : '原始評估');
  expect(JSON.parse(await source.locator('pre').innerText())).toEqual(row.assessment_ref);
  const links = source.locator('a'), operation = links.first(), discuss = links.last();
  await expect(operation).toHaveAttribute('href', `#/op/${row.assessment_ref.operation_id}`);
  const href = await discuss.getAttribute('href');
  expect(parseDiscussRefs(href!.split('?')[1]).refs).toEqual([{kind: 'quality_assessment', id: row.assessment_ref.operation_id,
    review_id: row.assessment_ref.review_id, document_digest: row.assessment_ref.document_digest}]);
  expect(parseDiscussRefs(href!.split('?')[1]).from).toBe('overview');
  await operation.focus();
  f.state.data.milestones[0].status = 'in_progress'; f.state.cursor++;
  await expect(root(page).locator('[data-overview-milestone]').first().locator('[data-status=in_progress]')).toBeVisible();
  await expect(source).toHaveAttribute('open', ''); await expect(operation).toBeFocused();
  await expect(metric(page, 'progress')).toContainText(locale === 'en' ? '3 of 8 accepted' : '8 項中 3 項已驗收');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await card.screenshot({path: testInfo.outputPath(`original-assessment-${locale}-${native ? 'ipc' : 'http'}.png`)});
  expect(f.state.reads.some(path => path.includes('/quality-reviews/'))).toBe(false);
  expect(f.state.writes).toEqual([]); await context.close();
});

test('changed delivery assessment never inherits another original source disclosure or focus', async ({page}) => {
  const f = fixture(), row = assessedDelivery(f); await boot(page, f);
  const source = root(page).locator('[data-overview-assessment]');
  await source.locator('summary').click(); await source.locator('a').first().focus();
  const original = structuredClone(row.assessment_ref);
  assessedDelivery(f, 'b'); f.state.cursor++;
  await expect(source).toHaveAttribute('data-overview-assessment', row.assessment_ref.operation_id);
  await expect(source).not.toHaveAttribute('open', '');
  await expect(source.locator('a').first()).not.toBeFocused();
  await source.locator('summary').click();
  await expect(source).not.toContainText(original.operation_id);
  expect(JSON.parse(await source.locator('pre').innerText())).toEqual(row.assessment_ref);
  delete row.assessment_ref; await refresh(page);
  await expect(source).toHaveCount(0); // Older servers do not provide this reference.
  await expect(root(page).locator(`[data-overview-delivery="${row.work_item_id}"] [data-status=accepted]`)).toBeVisible();
  expect(f.state.writes).toEqual([]);
});

test('fixed reports and withdrawn overview cannot retain a live delivery assessment', async ({page}) => {
  const f = fixture(), row = assessedDelivery(f); await boot(page, f);
  await root(page).locator('[data-overview-assessment] summary').click();
  await report(page, f.ids.snap1);
  await expect(root(page).locator('[data-overview-assessment]')).toHaveCount(0);
  await expect(root(page)).not.toContainText(row.assessment_ref.operation_id);
  await root(page).getByRole('button', {name: 'Back to live', exact: true}).click();
  await expect(root(page).locator('[data-overview-assessment]')).toHaveCount(1);
  f.state.denied = true; await refresh(page);
  await expect(root(page).locator('[data-overview-assessment]')).toHaveCount(0);
  await expect(root(page)).not.toContainText(row.assessment_ref.operation_id);
  expect(f.state.writes).toEqual([]);
});

test('delivery assessment references render literal text and invalid identities have no actions', async ({page}) => {
  const f = fixture(), row = assessedDelivery(f);
  row.assessment_ref.review_id = '<img src=x onerror="window.injected=true">';
  await boot(page, f); const source = root(page).locator('[data-overview-assessment]');
  await source.locator('summary').click();
  expect(JSON.parse(await source.locator('pre').innerText()).review_id).toBe(row.assessment_ref.review_id);
  await expect(source.locator('img, a')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).injected)).toBeUndefined();
  expect(f.state.writes).toEqual([]);
});

function decided(f: ReturnType<typeof fixture>) {
  const row = {key: `plan_revision.${f.ids.planV3}`, title: f.state.data.plan.title, decided_at: f.state.data.as_of - 3600,
    actor: f.ids.actor, status: 'adopted', ref: {kind: 'plan_revision', id: f.ids.planV3}, operation_id: 'op_' + 'd'.repeat(32)};
  Object.assign(f.state.data, {recent_decisions: [row], recent_decisions_count: 1, recent_decisions_complete: true, recent_decisions_truncated: false});
  return row;
}

async function burst(page: Page, f: ReturnType<typeof fixture>) {
  const types = ['session', 'task', 'operation', 'pm_workspace', 'pm_governance'];
  f.state.events = Array.from({length: 10}, (_, i) => ({seq: f.state.cursor + i + 1,
    resource_type: types[i % types.length], resource_id: f.ids.workspaceId, kind: 'fixture.updated'}));
  f.state.cursor += 10;
  await expect.poll(() => f.state.reads.some(path => path.startsWith(`/events?after=${f.state.cursor}&`))).toBe(true);
}

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) test(`empty overview sources distinguish unknown from recorded absence ${locale} ${width}`, async ({browser, baseURL}, testInfo) => {
  const context = await browser.newContext({baseURL, locale, colorScheme, viewport: {width, height: width === 390 ? 844 : 900}});
  const page = await context.newPage(), f = fixture(locale);
  Object.assign(f.state.data, {milestones: [], next_milestone: null, recent_deliveries: [], recent_deliveries_complete: false});
  f.state.data.coverage.sources.roadmap.complete = f.state.data.coverage.complete = false;
  f.state.data.coverage.sources.work.complete = false;
  f.state.data.unknown = [{field: 'milestones', reason: 'source_unavailable'}, {field: 'work', reason: 'source_incomplete'}];
  await boot(page, f); await page.evaluate(() => document.fonts.ready);
  if (process.env.BATC_OVERVIEW_SHOTS) {
    await page.screenshot({path: testInfo.outputPath(`${process.env.BATC_OVERVIEW_SHOTS}-${locale}-${width}-unknown.png`), fullPage: true, animations: 'disabled'});
  }
  const milestones = root(page).getByRole('heading', {name: locale === 'en' ? 'Milestones' : '里程碑', exact: true}).locator('..').locator('..');
  const deliveries = root(page).locator('.pm-overview-side > section').last();
  const empty = locale === 'en' ? 'None recorded' : '尚無紀錄';
  // Assert the card's coverage status separately from optional source record times.
  for (const [card, reason] of [[milestones, locale === 'en' ? 'Source unavailable' : '無法取得來源'],
    [deliveries, locale === 'en' ? 'Source is incomplete' : '來源不完整']] as const) {
    await expect(card.locator('[data-status=unknown]:not([data-readable-sources] *)')).toBeVisible();
    await expect(card).toContainText(reason); await expect(card).not.toContainText(empty);
  }
  // Coverage alone can change while the arrays remain empty.
  f.state.data.coverage.sources.roadmap.complete = true; f.state.data.recent_deliveries_complete = true;
  f.state.data.unknown = [{field: 'next_milestone', reason: 'no_roadmap_baseline'}];
  await refresh(page);
  for (const card of [milestones, deliveries]) {await expect(card).toContainText(empty); await expect(card.locator('[data-status=unknown]:not([data-readable-sources] *)')).toHaveCount(0);}
  // A complete scan can still omit delivery rows at the output-reference limit.
  f.state.data.coverage.sources.attention_references = {complete: false, next_cursor: null, status: 'source_limit'};
  f.state.data.unknown = [{field: 'attention_references', reason: 'source_limit'}];
  await refresh(page); await expect(deliveries.locator('[data-status=unknown]:not([data-readable-sources] *)')).toBeVisible(); await expect(deliveries).not.toContainText(empty);
  await expect(deliveries.getByRole('heading')).toHaveText(locale === 'en' ? 'Recent deliveries (loaded)' : '近期交付（已載入）');
  delete f.state.data.coverage.sources.roadmap;
  await refresh(page); await expect(milestones.locator('[data-status=unknown]:not([data-readable-sources] *)')).toBeVisible(); await expect(milestones).not.toContainText(empty);
  f.state.data.plan = null; f.state.data.coverage.sources.plan.complete = false;
  f.state.data.unknown = [{field: 'plan', reason: 'source_unavailable'}];
  await refresh(page);
  for (const key of ['definition', 'scope', 'resources', 'risks']) {
    const card = root(page).locator('[data-charter-empty]');
    await expect(card).toContainText(locale === 'en' ? {definition:'Purpose & success',scope:'Scope & deliverables',resources:'Resources & budget',risks:'Risks & dependencies'}[key]! : {definition:'目的與成功條件',scope:'範圍與交付',resources:'資源與預算',risks:'風險與相依'}[key]!);
    await expect(card.locator('[data-status=unknown]:not([data-readable-sources] *)')).toBeVisible();
    await expect(card).toContainText(locale === 'en' ? 'Source unavailable' : '無法取得來源'); await expect(card).not.toContainText(empty);
  }
  expect(f.state.writes).toEqual([]); await context.close();
});

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) for (const reading of ['focused', 'hovered', 'selected', 'visible']) test(`changed event burst holds the ${reading} attention row ${locale} ${width}`, async ({browser, baseURL}, testInfo) => {
  const context = await browser.newContext({baseURL, locale, colorScheme, viewport: {width, height: width === 390 ? 844 : 900}});
  const page = await context.newPage(), f = fixture(locale);
  // Enough authorized rows to keep the passive anchor away from either scroll limit
  // when Central reverses the order; interactive cases still read the second row.
  const risk = f.state.data.attention.at(-1);
  f.state.data.attention.push(...Array.from({length: 4}, (_, i) => ({...risk, key: `risk:governance:extra-${i}`,
    ref: {kind: 'governance', id: 'pmgov_' + (100 + i).toString(16).padStart(32, '0')}})));
  f.state.data.attention_counts.risk += 4;
  await boot(page, f); await page.evaluate(() => document.fonts.ready);
  const key = f.state.data.attention[reading === 'visible' ? 4 : 1].key, row = root(page).locator(`[data-overview-row="${key}"]`), link = row.locator('[data-overview-open]');
  await row.evaluate((node, reading) => {
    document.activeElement instanceof HTMLElement && document.activeElement.blur();
    window.scrollBy(0, node.getBoundingClientRect().top - (reading === 'visible' ? -10 : 200));
    getSelection()?.removeAllRanges();
  }, reading);
  await page.mouse.move(0, 0);
  if (reading === 'focused') await link.focus();
  if (reading === 'hovered') await link.hover();
  if (reading === 'visible') {
    expect(await root(page).locator('[data-overview-row]').evaluateAll(nodes => nodes.find(node => node.getBoundingClientRect().bottom > 0 && node.getBoundingClientRect().top < innerHeight)?.getAttribute('data-overview-row'))).toBe(key);
    const rect = (await row.boundingBox())!;
    expect(rect.y).toBeLessThan(0); expect(rect.y + rect.height).toBeGreaterThan(0);
  }
  // Selection alone is interaction; focus and hover cases remain independent.
  const selected = reading === 'selected' ? await row.locator('strong').evaluate(node => {
    const selection = getSelection()!, range = document.createRange(); range.selectNodeContents(node);
    selection.removeAllRanges(); selection.addRange(range); return selection.toString();
  }) : '';
  if (reading === 'selected') {
    expect(await root(page).locator('[data-overview-row]').evaluateAll(nodes => nodes.find(node => node.getBoundingClientRect().bottom > 0 && node.getBoundingClientRect().top < innerHeight)?.getAttribute('data-overview-row'))).not.toBe(key);
    await expect(root(page).locator('[data-overview-row]').first()).toBeInViewport({ratio: 1});
    expect(await root(page).locator('[data-overview-attention]').evaluate(node => node.contains(document.activeElement))).toBe(false);
    await expect(root(page).locator('[data-overview-row]:hover')).toHaveCount(0);
  }
  const top = (await row.boundingBox())!.y, originalOrder = f.state.data.attention.map((item: any) => item.key);
  const scenario = reading === 'selected' ? 'selected' : 'passive';
  const shots = ['visible', 'selected'].includes(reading) && process.env.BATC_OVERVIEW_SHOTS ? testInfo.outputPath('screenshots') : null;
  if (shots) {await page.screenshot({path: testInfo.outputPath(`${locale}-${width}-${scenario}-before.png`), animations: 'disabled'});}
  f.state.data.attention[0].title = (locale === 'en' ? 'A revised dependency blocker needs a longer maintenance assessment and remediation description. ' : '相依套件阻礙已更新，需要更詳細的維護評估與修正說明。').repeat(5);
  f.state.data.attention.reverse(); f.state.data.progress.accepted = 2; f.state.data.progress.in_review = 2;
  await burst(page, f);
  await expect(metric(page, 'progress')).toContainText(locale === 'en' ? '2 of 8 accepted' : '8 項中 2 項已驗收');
  expect(Math.abs((await row.boundingBox())!.y - top)).toBeLessThanOrEqual(1);
  expect(await root(page).locator('[data-overview-row]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-overview-row'))))
    .toEqual(reading === 'visible' ? f.state.data.attention.map((item: any) => item.key) : originalOrder);
  if (reading === 'focused') await expect(link).toBeFocused();
  expect(await page.evaluate(() => getSelection()?.toString())).toBe(selected);
  if (shots) await page.screenshot({path: testInfo.outputPath(`${locale}-${width}-${scenario}-after.png`), animations: 'disabled'});
  expect(f.state.writes).toEqual([]); await context.close();
});

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) test(`a new top-severity blocker pushes rows down with the list's top in view ${locale} ${width}`, async ({browser, baseURL}, testInfo) => {
  const context = await browser.newContext({baseURL, locale, colorScheme, viewport: {width, height: width === 390 ? 844 : 900}});
  const page = await context.newPage(), f = fixture(locale); await boot(page, f); await page.evaluate(() => document.fonts.ready);
  const anchor = root(page).locator('[data-overview-row]').first();
  await anchor.evaluate(node => {
    document.activeElement instanceof HTMLElement && document.activeElement.blur(); getSelection()?.removeAllRanges();
    window.scrollBy(0, node.getBoundingClientRect().top - 200);
  });
  await page.mouse.move(0, 0);
  const heading = root(page).locator('[data-overview-attention]').locator('..').getByRole('heading');
  await expect(heading).toBeInViewport({ratio: 1});
  const top = (await anchor.boundingBox())!.y, anchorKey = await anchor.getAttribute('data-overview-row'), scroll = await page.evaluate(() => scrollY);
  const shots = process.env.BATC_OVERVIEW_SHOTS ? testInfo.outputPath('screenshots') : null;
  if (shots) {await page.screenshot({path: testInfo.outputPath(`${locale}-${width}-top-before.png`), animations: 'disabled'});}
  const blocker = {...f.state.data.attention[0], key: 'blocker:governance:new-critical', severity: 'critical', since: f.state.data.as_of - 10 * 86400,
    ref: {kind: 'governance', id: 'pmgov_' + 'c'.repeat(32)}, title: locale === 'en' ? 'New critical dependency blocker' : '新增的重大相依套件阻礙'};
  f.state.data.attention.unshift(blocker); f.state.data.attention_counts.blocker++;
  await burst(page, f);
  const first = root(page).locator('[data-overview-row]').first();
  await expect(first).toHaveAttribute('data-overview-row', blocker.key);
  expect(await root(page).locator('[data-overview-row]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-overview-row'))))
    .toEqual(f.state.data.attention.map((item: any) => item.key));
  const inserted = (await first.boundingBox())!, previous = (await root(page).locator(`[data-overview-row="${anchorKey}"]`).boundingBox())!;
  expect(Math.abs(inserted.y - top)).toBeLessThanOrEqual(1);
  expect(Math.abs(previous.y - top - inserted.height)).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => scrollY)).toBe(scroll);
  await expect(heading).toBeInViewport({ratio: 1});
  if (shots) await page.screenshot({path: testInfo.outputPath(`${locale}-${width}-top-after.png`), animations: 'disabled'});
  expect(f.state.writes).toEqual([]); await context.close();
});

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) test(`returning to Overview restores Central's attention order ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, colorScheme, viewport: {width, height: width === 390 ? 844 : 900}});
  const page = await context.newPage(), f = fixture(locale); await boot(page, f);
  const originalOrder = f.state.data.attention.map((item: any) => item.key), row = root(page).locator('[data-overview-row]').nth(1);
  await row.locator('[data-overview-open]').focus();
  f.state.data.attention.reverse(); f.state.data.progress.accepted = 2; f.state.data.progress.in_review = 2;
  await burst(page, f); await expect(metric(page, 'progress')).toContainText(locale === 'en' ? '2 of 8 accepted' : '8 項中 2 項已驗收');
  expect(await root(page).locator('[data-overview-row]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-overview-row')))).toEqual(originalOrder);
  await row.locator('strong').evaluate(node => {
    const selection = getSelection()!, range = document.createRange(); range.selectNodeContents(node); selection.removeAllRanges(); selection.addRange(range);
  });
  await openPmSection(page, 'requirements'); await openPmSection(page, 'overview');
  await expect.poll(() => root(page).locator('[data-overview-row]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-overview-row'))))
    .toEqual(f.state.data.attention.map((item: any) => item.key));
  expect(f.state.writes).toEqual([]); await context.close();
});

for (const width of [1440, 390]) test(`changed event burst compensates the attention scroll container ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900}); const f = fixture(); await boot(page, f); await page.evaluate(() => document.fonts.ready);
  await page.locator('.pm-main').evaluate(node => {const scroller = node as HTMLElement; scroller.style.height = '600px'; scroller.style.overflowY = 'auto';});
  const key = f.state.data.attention[1].key, row = root(page).locator(`[data-overview-row="${key}"]`), link = row.locator('[data-overview-open]');
  await link.focus();
  const top = (await row.boundingBox())!.y, windowScroll = await page.evaluate(() => scrollY);
  f.state.data.attention[0].title = 'A longer assessment of this recorded dependency blocker. '.repeat(6);
  f.state.data.attention.reverse(); await burst(page, f);
  expect(Math.abs((await row.boundingBox())!.y - top)).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => scrollY)).toBe(windowScroll); await expect(link).toBeFocused(); expect(f.state.writes).toEqual([]);
});

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) test(`selection anchors attention even with the list's top in view ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, colorScheme, viewport: {width, height: width === 390 ? 844 : 900}});
  const page = await context.newPage(), f = fixture(locale); await boot(page, f); await page.evaluate(() => document.fonts.ready);
  const key = f.state.data.attention[0].key, row = root(page).locator(`[data-overview-row="${key}"]`), originalOrder = f.state.data.attention.map((item: any) => item.key);
  await row.evaluate(node => {
    document.activeElement instanceof HTMLElement && document.activeElement.blur(); window.scrollBy(0, node.getBoundingClientRect().top - 200);
  });
  await page.mouse.move(0, 0);
  await expect(root(page).locator('[data-overview-attention]').locator('..').getByRole('heading')).toBeInViewport({ratio: 1});
  const selected = await row.locator('strong').evaluate(node => {
    const selection = getSelection()!, range = document.createRange(); range.selectNodeContents(node); selection.removeAllRanges(); selection.addRange(range); return selection.toString();
  });
  const top = (await row.boundingBox())!.y;
  f.state.data.plan.goal = f.state.data.plan.goal.repeat(4); f.state.data.attention.reverse();
  f.state.data.progress.accepted = 2; f.state.data.progress.in_review = 2;
  await burst(page, f); await expect(metric(page, 'progress')).toContainText(locale === 'en' ? '2 of 8 accepted' : '8 項中 2 項已驗收');
  expect(Math.abs((await row.boundingBox())!.y - top)).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => getSelection()?.toString())).toBe(selected);
  expect(await root(page).locator('[data-overview-row]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-overview-row')))).toEqual(originalOrder);
  expect(f.state.writes).toEqual([]); await context.close();
});

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) test(`Progress Details keeps focus and its open state through a count burst ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, colorScheme, viewport: {width, height: width === 390 ? 844 : 900}});
  const page = await context.newPage(), f = fixture(locale); await boot(page, f);
  const details = metric(page, 'progress').locator('details'), summary = details.locator('summary');
  await summary.click(); await expect(details).toHaveAttribute('open', ''); await summary.focus();
  f.state.data.progress.accepted = 2; f.state.data.progress.in_review = 2; await burst(page, f);
  await expect(summary).toBeFocused(); await expect(details).toHaveAttribute('open', '');
  await expect(details.locator('li').first()).toHaveText(locale === 'en' ? 'Accepted 2' : '已驗收 2');
  expect(f.state.writes).toEqual([]); await context.close();
});

for (const width of [1440, 390]) test(`withdrawing the attention anchor removes its row and count ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900}); const f = fixture(), errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message)); await boot(page, f);
  const anchor = f.state.data.attention[0], row = root(page).locator(`[data-overview-row="${anchor.key}"]`);
  await row.locator('[data-overview-open]').focus();
  f.state.data.attention = f.state.data.attention.filter((item: any) => item.key !== anchor.key); f.state.data.attention_counts.blocker = 0;
  await burst(page, f); await expect(row).toHaveCount(0);
  await expect(metric(page, 'blocker').locator('.ui-metric-value')).toHaveText('0');
  await expect(metric(page, 'blocker')).not.toContainText(anchor.title);
  expect(errors).toEqual([]); expect(f.state.writes).toEqual([]);
});

for (const locale of ['en', 'zh-TW']) test(`charter empty states require readable complete sources ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, colorScheme}), page = await context.newPage(), f = fixture(locale);
  const clearStatements = (plan: any) => {for (const section of Object.values(plan.document.sections) as any[]) section.statements = [];};
  f.state.planPatch = clearStatements;
  f.state.data.attention = f.state.data.attention.filter((row: any) => row.kind !== 'decision'); f.state.data.attention_counts.decision = 0;
  await boot(page, f); const cards = root(page).locator('[data-charter-empty]'), empty = locale === 'en' ? 'None recorded' : '尚無紀錄';
  await expect(cards).toHaveCount(1);
  await expect(cards.locator('details')).toHaveCount(0);
  // Empty domains share one line without empty source disclosures.
  for (const card of await cards.all()) {await expect(card).toContainText(empty); await expect(card.locator('[data-status=unknown]:not([data-readable-sources] *)')).toHaveCount(0);}
  f.state.data.attention_counts.decision = null;
  f.state.data.unknown = [{field: 'attention_counts.decision', reason: 'source_unavailable'}];
  await refresh(page); const decisions = metric(page, 'decision');
  await expect(decisions.locator('.ui-metric-value [data-status=unknown]')).toBeVisible(); await expect(decisions.locator('.ui-metric-value')).not.toContainText(empty);
  const decision = overview(demoProject(locale)).attention.find(row => row.kind === 'decision')!;
  f.state.data.attention.push(decision); await refresh(page);
  await expect(decisions).toContainText(decision.title); await expect(decisions.locator('[data-status=unknown]:not([data-readable-sources] *)')).toBeVisible();
  await expect(decisions.locator('.ui-metric-value')).not.toContainText(empty);
  delete f.state.data.coverage.sources.plan;
  await refresh(page);
  for (const card of (await cards.all()).slice(0, 5)) {await expect(card.locator('[data-status=unknown]:not([data-readable-sources] *)')).toBeVisible(); await expect(card).not.toContainText(empty);}
  let restricted = true, filtered = false;
  f.state.reportPatch = snapshot => {
    clearStatements(snapshot.baseline); for (const section of Object.values(snapshot.brief) as any[]) section.items = [];
    snapshot.visibility.restricted = restricted; snapshot.visibility.filtered = filtered;
  };
  await report(page, f.ids.snap1);
  await expect(cards).toHaveCount(1);
  for (const card of await cards.all()) {
    await expect(card.locator('[data-status=unknown]:not([data-readable-sources] *)')).toBeVisible(); await expect(card).not.toContainText(empty);
    await expect(card).toContainText(locale === 'en' ? 'Restricted sources' : '來源受限');
  }
  restricted = false; filtered = true; await refresh(page);
  for (const card of await cards.all()) {
    await expect(card.locator('[data-status=unknown]:not([data-readable-sources] *)')).toBeVisible(); await expect(card).not.toContainText(empty);
    await expect(card).toContainText(locale === 'en' ? 'Some sources hidden' : '部分來源已隱藏');
  }
  filtered = false; await refresh(page);
  for (const card of await cards.all()) {await expect(card).toContainText(empty); await expect(card.locator('[data-status=unknown]:not([data-readable-sources] *)')).toHaveCount(0);}
  expect(f.state.writes).toEqual([]); await context.close();
});

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) {
  test(`progress track measures accepted requirements ${locale} ${native ? 'native' : 'web'}`, async ({browser, baseURL}, testInfo) => {
    const context = await browser.newContext({baseURL, locale, colorScheme,
      viewport: {width: locale === 'en' ? 1440 : 390, height: 900}});
    const page = await context.newPage(), f = fixture(locale); await boot(page, f, native);
    expect(await page.evaluate(() => matchMedia('(prefers-color-scheme: dark)').matches)).toBe(colorScheme === 'dark');
    const bar = metric(page, 'progress').locator('.ui-metric-bar');
    const segments = () => bar.evaluate(node => [...node.children].map(child => ({
      color: getComputedStyle(child).backgroundColor,
      share: child.getBoundingClientRect().width / node.getBoundingClientRect().width,
    })));
    const mixed = await segments();
    for (const [index, share] of [3 / 8, 5 / 8].entries()) expect(mixed[index].share).toBeCloseTo(share, 2);
    expect(mixed[1].color).toBe('rgba(0, 0, 0, 0)');
    expect(mixed[0].color).not.toBe(mixed[1].color);
    expect(mixed.slice(0, 1).every(row => row.color !== 'rgba(0, 0, 0, 0)')).toBe(true);
    f.state.data.progress = {requirements: 2, accepted: 0, in_review: 0, in_progress: 0, blocked: 0, not_started: 2, complete: true};
    await refresh(page); await expect(metric(page, 'progress')).toContainText(locale === 'en' ? '0 of 2 accepted' : '2 項中 0 項已驗收');
    const empty = await segments();
    expect(empty.slice(0, 1).every(row => row.share === 0)).toBe(true);
    expect(empty[1]).toEqual({share: 1, color: 'rgba(0, 0, 0, 0)'});
    expect(await bar.evaluate(node => getComputedStyle(node).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
    await page.screenshot({path: testInfo.outputPath(`${locale}-${native ? 'native' : 'web'}-${colorScheme}-empty.png`), fullPage: true, animations: 'disabled'});
    f.state.data.progress = {...f.state.data.progress, requirements: 4, in_progress: 1, blocked: 1};
    await refresh(page); await expect(metric(page, 'progress')).toContainText(locale === 'en' ? '0 of 4 accepted' : '4 項中 0 項已驗收');
    const started = await segments();
    expect(started[0].share).toBe(0); expect(started[1].share).toBe(1);
    expect(started[1].color).toBe(mixed[1].color);
    expect(f.state.writes).toEqual([]); await context.close();
  });

  test(`live ages distinguish waiting from decided without replacing rows ${locale} ${native ? 'native' : 'web'}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, colorScheme});
    const page = await context.newPage(), f = fixture(locale), decision = decided(f), now = f.state.data.as_of;
    f.state.data.attention[0].since = decision.decided_at = now;
    await boot(page, f, native);
    const waiting = root(page).locator('[data-overview-row]').first(), completed = root(page).locator(`[data-overview-decided="${decision.key}"]`);
    await waiting.evaluate(node => {(window as any).clockAttention = node;});
    await completed.evaluate(node => {(window as any).clockDecision = node;});
    for (const [seconds, enWait, zhWait, enPast, zhPast] of [
      [0, '<1 min', '不到 1 分鐘', 'just now', '剛剛'], [59, '<1 min', '不到 1 分鐘', 'just now', '剛剛'],
      [60, '1 min', '1 分鐘', '1 min ago', '1 分鐘前'], [300, '5 min', '5 分鐘', '5 min ago', '5 分鐘前'],
      [10800, '3 hr', '3 小時', '3 hr ago', '3 小時前'], [172800, '2 d', '2 天', '2 d ago', '2 天前'],
    ] as const) {
      f.state.data.as_of = now + seconds; await refresh(page);
      await expect(waiting.locator('[data-overview-age]')).toHaveText(locale === 'en' ? enWait : zhWait);
      await expect(completed.locator('[data-overview-decided-meta]')).toHaveText(`${locale === 'en' ? enPast : zhPast} · ${decision.actor}`);
      expect(await waiting.evaluate(node => node === (window as any).clockAttention)).toBe(true);
      expect(await completed.evaluate(node => node === (window as any).clockDecision)).toBe(true);
      await expect(root(page)).not.toContainText(/\b0m\b|0 分鐘/);
    }
    decision.decided_at = null as any; f.state.data.attention[0].since = null;
    await refresh(page); await expect(completed.locator('[data-overview-decided-meta]')).toHaveText(decision.actor);
    await expect(waiting.locator('[data-overview-age]')).toHaveCount(0);
    expect(f.state.writes).toEqual([]); await context.close();
  });

  test(`live goal omits repeated provenance while saved and unknown states remain ${locale} ${native ? 'native' : 'web'}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, colorScheme});
    const page = await context.newPage(), f = fixture(locale); await boot(page, f, native);
    await expect(metric(page, 'decision').locator(':scope > .ui-meta')).toHaveText(locale === 'en' ? 'Open decisions' : '待決事項');
    await expect(metric(page, 'goal')).toContainText(f.copy.goal);
    await expect(metric(page, 'goal')).not.toContainText(f.state.data.plan.title);
    await expect(metric(page, 'goal')).not.toContainText(locale === 'en' ? 'adopted' : '採用');
    await expect(page.locator('[data-overview-meta]')).toContainText(f.state.data.plan.title);
    await expect(page.locator('[data-overview-meta]')).toContainText(locale === 'en' ? 'adopted' : '採用');
    f.state.data.plan.goal = null; f.state.data.unknown = [{field: 'plan.goal', reason: 'unknown'}];
    await refresh(page); await expect(metric(page, 'goal').locator('.ui-metric-value')).toHaveText('—');
    await expect(metric(page, 'goal').locator('[data-status=unknown]')).toHaveCount(1);
    f.state.data.unknown = [{field: 'plan.goal', reason: 'goal_unconfirmed'}];
    await refresh(page); await expect(metric(page, 'goal').locator('[data-status=unconfirmed]')).toHaveCount(1);
    await expect(metric(page, 'goal').locator('[data-status=unknown]')).toHaveCount(0);
    f.state.data.unknown = [{field: 'plan.goal', reason: 'no_explicit_goal'}];
    await refresh(page); await expect(metric(page, 'goal')).toContainText(locale === 'en' ? 'Not set' : '未設定');
    let fixedTitle = '';
    f.state.reportPatch = snapshot => {fixedTitle = snapshot.baseline.document.title; expect(snapshot.baseline.adoption.created_at).toBeGreaterThan(0);};
    await report(page, f.ids.snap1); await expect(metric(page, 'goal')).toContainText(fixedTitle);
    await expect(metric(page, 'goal')).toContainText(locale === 'en' ? 'adopted' : '採用');
    expect(f.state.writes).toEqual([]); await context.close();
  });
}

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) test(`completed decision opens its register inspector ${locale} ${native ? 'native' : 'web'}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, colorScheme, viewport: {width: locale === 'en' ? 1440 : 390, height: 900}});
  const page = await context.newPage(), f = fixture(locale), row = decided(f);
  await boot(page, f, native);
  await expect(metric(page, 'decision').locator('.ui-metric-value')).toHaveText('2');
  const recent = metric(page, 'decision').locator('[data-overview-recent-decisions]');
  await expect(recent).toContainText(locale === 'en' ? 'Recently decided' : '近期決策');
  await expect(recent).toContainText(row.title); await expect(recent).toContainText(f.ids.actor);
  await expect(recent).toContainText(locale === 'en' ? '1 hr ago' : '1 小時前');
  await recent.getByRole('button').click();
  await expect(page.locator(`[data-reg-inspector][data-reg-row="${row.key}"]`)).toBeVisible();
  expect(f.state.writes).toEqual([]); await context.close();
});

test('completed history distinguishes missing, incomplete, capped and empty sources without changing pending count', async ({page}) => {
  const f = fixture(); delete f.state.data.recent_decisions; await boot(page, f);
  const recent = metric(page, 'decision').locator('[data-overview-recent-decisions]');
  await expect(recent).toContainText('Unknown');
  const row = decided(f);
  Object.assign(f.state.data, {recent_decisions_count: null, recent_decisions_complete: false, recent_decisions_truncated: true});
  await refresh(page); await expect(recent).toContainText('Partial'); await expect(recent).toContainText('Showing 1');
  await expect(recent).toContainText(row.title); await expect(metric(page, 'decision').locator('.ui-metric-value')).toHaveText('2');
  Object.assign(f.state.data, {recent_decisions_count: 7, recent_decisions_complete: true});
  await refresh(page); await expect(recent).toContainText('Showing 1'); await expect(recent).not.toContainText('7');
  Object.assign(f.state.data, {recent_decisions: [], recent_decisions_count: 0, recent_decisions_complete: true, recent_decisions_truncated: false});
  await refresh(page); await expect(recent).toContainText('None recorded'); await expect(recent).not.toContainText('Partial');
  expect(f.state.writes).toEqual([]);
});

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 768, 390]) test(`five-second project answer ${locale} ${width}`, async ({browser, baseURL}, testInfo) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}, colorScheme, reducedMotion: 'reduce'});
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
  await expect(root(page).locator('[data-charter-section]')).toHaveCount(4);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  if (width < 800) {
    const discuss = page.locator('[data-overview-discuss]');
    const bounds = await discuss.boundingBox();
    expect(bounds!.height).toBeLessThan(60);
    // Wrapped header actions remain inside the header and reachable at their hit point.
    await page.locator('[data-pm-refresh]').focus();
    expect(await page.locator('[data-pm-refresh]').evaluate(node => {
      const box = node.getBoundingClientRect(), row = node.closest('.ui-page-header')!.getBoundingClientRect();
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
  await page.screenshot({path: testInfo.outputPath(`overview-${locale}-${width}.png`), fullPage: true});
  if (width === 1440) {await page.screenshot({path: testInfo.outputPath(`overview-${locale}-${width}-fold.png`)});}
  if (width === 1440 && locale === 'en') {
    await page.emulateMedia({colorScheme: 'dark'}); await page.screenshot({path: testInfo.outputPath(`overview-en-1440-dark.png`), fullPage: true}); await page.emulateMedia({colorScheme});
  }
  const details = root(page).locator(':scope > div > details'); await details.locator(':scope > summary').click();
  expect(await root(page).innerText()).not.toMatch(/\b(undefined|null|NaN|ov_\w+)\b/);
  await expect(details.locator('[data-overview-coverage]')).toContainText(locale === 'en' ? 'Plan · Complete' : '計畫 · 完整');
  await expect(details.locator('details')).not.toHaveAttribute('open', ''); await details.locator(':scope > summary').click();
  if (width === 1440) {await page.emulateMedia({media: 'print'}); await page.screenshot({path: testInfo.outputPath(`overview-${locale}-print-live.png`), fullPage: true}); await page.emulateMedia({media: 'screen'});}
  f.state.reportPatch = saved => {saved.visibility.restricted = true; saved.coverage.work_truncated = true;};
  await report(page, f.ids.snap2); await expect(root(page).locator('.pm-overview-saved [data-status=partial]')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const arrows = root(page).locator('.pm-overview-report-arrows button'), first = (await arrows.nth(0).boundingBox())!, second = (await arrows.nth(1).boundingBox())!;
  expect(first.y).toBe(second.y);
  await page.screenshot({path: testInfo.outputPath(`overview-${locale}-${width}-saved-partial.png`), fullPage: true});
  await root(page).getByRole('button', {name: locale === 'en' ? 'Compare' : '比較', exact: true}).click();
  await expect(page.locator('.pm-charter-comparison')).toBeVisible();
  await page.screenshot({path: testInfo.outputPath(`overview-${locale}-${width}-compare.png`)}); await page.keyboard.press('Escape');
  if (width === 1440) {await page.emulateMedia({media: 'print'}); await page.screenshot({path: testInfo.outputPath(`overview-${locale}-print-saved.png`), fullPage: true});}
  expect(errors).toEqual([]); expect(f.state.writes).toEqual([]); await context.close();
});

for (const native of [false, true]) test(`discussion links bind exact identities and saved report ${native ? 'native' : 'web'}`, async ({page}) => {
  const f = fixture(); await boot(page, f, native);
  const header = await page.locator('[data-overview-discuss]').getAttribute('href');
  expect(parseDiscussRefs(header!.split('?')[1]).refs).toEqual([{kind: 'plan_revision', id: f.ids.planV3}]);
  for (const row of f.state.data.attention) {
    const link = root(page).locator(`[data-overview-row="${row.key}"] a`), href = await link.getAttribute('href');
    const query = new URLSearchParams(href!.split('?')[1]);
    expect(parseDiscussRefs(query).refs).toEqual([row.ref]); expect(query.get('title')).toBe(findingName(row.finding, 'en', row.title)); expect(query.get('from')).toBe('overview');
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
  const f = fixture(), dispatch = f.dispatch;
  f.dispatch = async input => {
    const result = await dispatch(input);
    if (input.path.endsWith('/register/governance.' + f.state.data.attention[0].ref.id)) result.data.row.title = f.state.data.attention[0].title;
    return result;
  };
  await boot(page, f);
  const row = f.state.data.attention[0]; await root(page).locator(`[data-overview-open="${row.key}"]`).click();
  await expect(page.locator('[data-pm-view="issues"]')).toHaveAttribute('aria-current', 'page');
  const inspector = page.locator(`[data-reg-inspector][data-reg-row="governance.${row.ref.id}"]`);
  await expect(inspector.locator('h2')).toHaveText(row.title);
  await expect(inspector.locator('[data-reg-discuss]')).toBeVisible();
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
  const f = fixture(), decision = decided(f); await boot(page, f); await report(page, f.ids.snap1);
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
  await expect(root(page).locator('[data-overview-decided]')).toHaveCount(0);
  await expect(root(page)).not.toContainText(decision.actor);
  await expect(metric(page, 'progress')).not.toContainText('3 of 8 accepted'); expect(f.state.writes).toEqual([]);
});

test('background updates retain reading rows, charter expansion and plan reader', async ({page}) => {
  const f = fixture(); f.state.extraStatements = true; await boot(page, f);
  await page.evaluate(() => document.fonts.ready);
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
  await page.getByRole('button', {name: 'More versions', exact: true}).click();
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
  const card = metric(page, 'decision');
  await expect(card.locator('.ui-metric-value [data-status=unknown]')).toBeVisible(); await expect(card.locator('.ui-metric-value')).not.toContainText('None recorded');
  f.state.data.attention_counts.decision = 3; f.state.data.attention_truncated = true; await refresh(page);
  await expect(card.locator('[data-status=partial]')).toBeVisible();
  await expect(card.getByRole('link', {name: '+3 more', exact: true})).toHaveAttribute('href', `#/pm/${f.ids.workspaceId}/issues`);
  f.state.data.attention_truncated = false; await refresh(page);
  await expect(card.locator('[data-status=partial]')).toHaveCount(0);
  expect(f.state.writes).toEqual([]);
});

for (const locale of ['en', 'zh-TW']) test(`selected report has a loading state ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, colorScheme}), page = await context.newPage(), f = fixture(locale); await boot(page, f);
  await openPmPanel(page, 'ui_report'); await expect(page.locator('[data-pm-report-tools] select')).toBeEnabled();
  let release!: () => void; f.state.heldReport = new Promise<void>(resolve => {release = resolve;});
  try {
    await page.locator('[data-pm-report-tools] select').selectOption(f.ids.snap1); await page.keyboard.press('Escape');
    await expect(root(page).locator('.ui-skeleton')).toBeVisible();
    await expect(root(page).getByRole('status', {name: locale === 'en' ? 'Loading report…' : '正在載入報告…'})).toBeVisible();
    await expect(root(page)).not.toContainText(locale === 'en' ? 'Select a report in Versions.' : '請從版本選取報告。');
  } finally {release();}
  await expect(metric(page, 'goal')).toContainText(f.copy.goalV2); await context.close();
});

test('epoch dates are local while milestone calendar dates stay fixed', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en', colorScheme, timezoneId: 'Asia/Taipei'}), page = await context.newPage(), f = fixture();
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
  await expect(row.locator('[data-overview-age]')).toHaveText('5 d'); await control.focus();
  await row.evaluate(node => {(window as any).readingRow = node;});
  const y = (await row.boundingBox())!.y;
  f.state.data.as_of += 3 * 86400; f.state.cursor++;
  await expect(row.locator('[data-overview-age]')).toHaveText('8 d'); await expect(control).toBeFocused();
  expect(await row.evaluate(node => node === (window as any).readingRow)).toBe(true); expect((await row.boundingBox())!.y).toBe(y);
  f.state.data.as_of += 3 * 86400; f.state.data.attention_counts.decision++; f.state.cursor++;
  await expect(row.locator('[data-overview-age]')).toHaveText('11 d'); await expect(control).toBeFocused(); expect(f.state.writes).toEqual([]);
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
  await root(page).locator('[data-charter-section=risks]').scrollIntoViewIfNeeded();
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
