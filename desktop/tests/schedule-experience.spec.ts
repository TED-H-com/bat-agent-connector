import {test, expect, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {demoProject, DEMO_NOW} from './demo/project.ts';
import {schedulePage} from './demo/schedule.ts';
import {fixture as roadmapFixture, path as roadmapPath} from './pm-roadmap-fixture.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';

async function start(page: Page, locale = 'en', options: {native?: boolean; withoutBrief?: boolean; pageSize?: number; initialView?: string; mutate?: (data: any) => void} = {}) {
  const fixture = demoFixture({locale}), project = demoProject(locale);
  const calls: string[] = [], writes: any[] = [], state = {deny: false, tooLarge: false, workspaceDeny: false, defer: false, pending: null as null | (() => void), cursor: 0, eventType: 'work_item', completed: 0, overviewReads: 0};
  await page.clock.setFixedTime(DEMO_NOW);
  await mountConversation(page, options.native || false, async input => {
    if (input.method !== 'GET') writes.push(input);
    const url = new URL(input.path, 'http://fixture');
    if (url.pathname.endsWith('/overview')) state.overviewReads++;
    if (url.pathname.endsWith('/schedule')) {
      calls.push(input.path);
      const data = schedulePage(project, url, options.pageSize); options.mutate?.(data);
      const denied = state.deny, tooLarge = state.tooLarge;
      if (state.defer) {state.defer = false; await new Promise<void>(resolve => {state.pending = resolve;});}
      // Snapshot the response before a later read revokes the source.
      state.completed++;
      if (tooLarge) return {status: 413, data: {error: {code: 'PM_SCHEDULE_TOO_LARGE', message: 'Schedule exceeds limit'}}};
      return denied ? {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Unavailable'}}} : {status: 200, data};
    }
    if (url.pathname === `/pm/workspaces/${fixture.ids.workspaceId}` && state.workspaceDeny) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Unavailable'}}};
    if (url.pathname === '/events' && state.cursor) return {status: 200, data: {events: Number(url.searchParams.get('after')) < state.cursor
      ? [{seq: state.cursor, resource_type: state.eventType, resource_id: state.eventType === 'pm_workspace' ? fixture.ids.workspaceId : fixture.ids.work.email, kind: `${state.eventType}.updated`}] : [],
      next_cursor: state.cursor, head_cursor: state.cursor, has_more: false, sync: {checkpoint: {cursor: state.cursor, token: `demo-${state.cursor}`}}}};
    const result = await fixture.dispatch(input);
    if (options.withoutBrief && ['/bootstrap', '/capabilities'].includes(url.pathname)) {
      const caps = url.pathname === '/bootstrap' ? result.data.capabilities : result.data;
      delete caps.features.pm_workspaces.brief_snapshots;
    }
    return result;
  });
  const view = options.initialView || 'schedule';
  await page.goto(`/dashboard/#/pm/${fixture.ids.workspaceId}/${view}`);
  if (view === 'schedule') await expect(page.locator('.ui-page-header .schedule-meta')).toContainText(locale === 'en' ? /items|loaded/ : /共 .* 筆|已載入/);
  await expect(page.locator('.pm-main > header').getByRole('button', {name: /^(Refresh|重新整理)$/})).toBeEnabled();
  return {...fixture, state, calls, writes};
}
function item(page: Page, id: string) {return page.locator(`.schedule-chart [data-schedule-select="${id}"]`);}
for (const locale of ['en', 'zh-TW']) test(`schedule distinguishes source closure from delivery ${locale}`, async ({page}) => {
  let sourceId = '';
  await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
  const f = await start(page, locale, {mutate(data) {
    const row = data.unscheduled.find(row => row.kind === 'work');
    sourceId = row.id;
    row.status = 'unknown'; row.actual = null;
    row.refs.source_lane = 'github_closed';
    row.refs.github_source = {source: 'github', repository: 'example/board', repository_id: 1,
      number: 1, node_id: 'PR_1', url: 'https://github.com/example/board/pull/1', kind: 'pull_request',
      state: 'closed', state_reason: null, draft: false, availability: 'current'};
  }});
  await page.locator('.schedule-unscheduled summary').click();
  await page.locator(`[data-schedule-select="${sourceId}"]`).click();
  await expect(page.locator('[data-schedule-inspector] [data-schedule-section=status]')).toContainText(
    locale === 'en' ? 'Closed (not completed)' : '已關閉（未完成）');
  expect(f.writes).toEqual([]);
});
async function noOverflow(page: Page) {expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);}
async function shot(page: Page, name: string) {
  const testInfo = test.info();
  if (!process.env.BATC_SCHEDULE_SCREENSHOTS) return;
  await page.screenshot({path: testInfo.outputPath(`${name}.png`)});
}
for (const locale of ['en', 'zh-TW']) for (const width of [1440, 768, 390]) test(`schedule reading, inspector and keyboard ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({locale, baseURL, viewport: {width, height: width === 390 ? 844 : 900}}), page = await context.newPage();
  const errors: string[] = [], httpErrors: string[] = []; page.on('response', response => {if (response.status() >= 400) httpErrors.push(`${response.status()} ${new URL(response.url()).pathname}`);}); page.on('pageerror', error => errors.push(error.message)); page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
  const f = await start(page, locale);
  const region = page.locator('[data-pm-schedule]');
  await expect(region.locator('[data-schedule-delta]')).toHaveText(locale === 'en' ? '+3d' : '+3 天');
  await expect(region.locator('.schedule-chart [data-schedule-row^="attempt:session:"]')).toHaveCount(0);
  const open = region.locator(`[data-schedule-row="work:${f.ids.work.resetEmail}"] [data-open=true]`);
  const openWord = locale === 'en' ? 'Open' : '未結束';
  if (width >= 800) await expect(open).toContainText(openWord);
  else {
    await expect(open).toHaveCount(1);
    await expect(open.locator('.schedule-bar-label')).toHaveCount(0);
    await expect(region.locator(`[data-schedule-row="work:${f.ids.work.resetEmail}"] .schedule-date-range`)).toContainText(openWord);
  }
  await expect(region.locator('.schedule-bar.blocked')).toHaveCount(0);
  await noOverflow(page); await shot(page, `schedule-${locale}-${width}`);
  const milestone = item(page, 'milestone:design_review'); await milestone.focus(); await page.keyboard.press('ArrowLeft');
  await expect(milestone).toHaveAttribute('aria-expanded', 'false'); await page.keyboard.press('ArrowRight');
  await expect(milestone).toHaveAttribute('aria-expanded', 'true'); await page.keyboard.press('ArrowDown');
  await expect(page.locator('.schedule-tree-cell:focus')).toHaveAttribute('aria-level', '2');
  const work = item(page, `work:${f.ids.work.resetEmail}`); await work.focus(); await page.keyboard.press('Enter');
  const inspector = page.locator('[data-schedule-inspector]'); await expect(inspector).toBeVisible();
  expect(await inspector.locator('[data-schedule-section]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-schedule-section')))).toEqual(['requirement','status','result','blocker','delivery','details']);
  await expect(inspector).toContainText(f.copy.requirements.find(value => value.id === f.ids.requirements.resetEmail)!.text);
  await expect(inspector.locator('[data-schedule-attempt]')).toHaveCount(2);
  const attempts = (await inspector.locator('[data-schedule-attempt]').allInnerTexts()).join('\n');
  expect(attempts).toContain(locale === 'en' ? 'Start session' : '開始工作階段');
  expect(attempts).toContain('Codex');
  const agent = inspector.locator('.schedule-agent').filter({has: page.getByRole('img', {name: 'Codex', exact: true})});
  await expect(agent).toContainText('Codex');
  await expect(agent).not.toContainText('codex');
  expect(attempts).not.toContain('session.start');
  await expect(region.locator(`[data-schedule-row^="attempt:operation:"] .schedule-title`)).toHaveText(locale === 'en' ? 'Start session' : '開始工作階段');
  await expect(inspector.locator('a[href*="/session/"]')).toHaveCount(1);
  await expect(inspector.locator('[data-status=read_only]')).toBeVisible();
  await expect(inspector.locator('a[href*="run"]')).toHaveCount(0);
  const href = await inspector.locator('[data-schedule-discuss]').getAttribute('href');
  expect(parseDiscussRefs(href!.split('?')[1])).toEqual({refs: [{kind: 'work_item', id: f.ids.work.resetEmail}], title: locale === 'en' ? `Change: ${f.copy.work.resetEmail}` : `變更：${f.copy.work.resetEmail}`, from: 'schedule', report: ''});
  await expect(inspector.locator('a[href*="/requirements?select="]')).toHaveAttribute('href', `#/pm/${f.ids.workspaceId}/requirements?select=${f.ids.work.resetEmail}`);
  await noOverflow(page); await shot(page, `schedule-inspector-${locale}-${width}`);
  await page.keyboard.press('Escape'); await expect(inspector).toHaveCount(0); await expect(work).toBeFocused();
  if (width >= 800) {
    await region.getByRole('radio', {name: locale === 'en' ? 'Day' : '日', exact: true}).click();
    await expect(region.locator('.schedule-weekend').first()).toBeAttached();
    await region.getByRole('button', {name: locale === 'en' ? 'Today' : '今天', exact: true}).click();
    expect(await region.locator('.schedule-viewport').evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
  } else {
    await expect(region.getByRole('radio', {name: locale === 'en' ? 'Day' : '日', exact: true})).toHaveCount(0);
    await expect(region.getByRole('radio', {name: locale === 'en' ? 'Week' : '週', exact: true})).toHaveAttribute('aria-checked', 'true');
    await expect(region.locator('[data-schedule-axis-caption]')).toBeVisible();
  }
  await region.locator('.schedule-unscheduled summary').click();
  await expect(region.locator('.schedule-unscheduled')).toContainText(f.copy.manualTitle);
  expect(await region.innerText()).not.toMatch(/\b(?:undefined|null|NaN)\b/);
  await noOverflow(page); expect(f.writes).toEqual([]); expect(httpErrors).toEqual(['404 /api/v1/browser-session']); expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
  if (width === 1440 && locale === 'en') {await page.emulateMedia({colorScheme: 'dark'}); await shot(page, 'schedule-dark-en-1440');}
  await context.close();
});

test('zoom and view switches preserve selection and the scroll date', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900}); const f = await start(page);
  const region = page.locator('[data-pm-schedule]'), viewport = region.locator('.schedule-viewport');
  await item(page, `work:${f.ids.work.resetEmail}`).click();
  await viewport.evaluate(node => {node.scrollLeft = 240; node.dispatchEvent(new Event('scroll'));});
  await region.getByRole('radio', {name: 'Day', exact: true}).click();
  await expect(item(page, `work:${f.ids.work.resetEmail}`)).toHaveAttribute('aria-selected', 'true');
  expect(await viewport.evaluate(node => node.scrollLeft)).toBeCloseTo(480, 0);
  await page.evaluate(id => {location.hash = `#/pm/${id}/overview`;}, f.ids.workspaceId);
  await expect(region).toBeHidden(); await expect(page.locator('[data-schedule-inspector]')).toHaveCount(0);
  await page.evaluate(id => {location.hash = `#/pm/${id}/schedule`;}, f.ids.workspaceId);
  await expect(page.locator('[data-schedule-inspector]')).toBeVisible();
  await expect(region.getByRole('radio', {name: 'Day', exact: true})).toHaveAttribute('aria-checked', 'true');
  expect(await viewport.evaluate(node => node.scrollLeft)).toBeCloseTo(480, 0);
  await page.locator('.pm-main > header').getByRole('button', {name: /^(Refresh|重新整理)$/}).click();
  await expect(page.locator('.pm-main > header').getByRole('button', {name: /^(Refresh|重新整理)$/})).toBeEnabled();
  await expect(page.locator('[data-schedule-inspector]')).toContainText(f.copy.work.resetEmail);
  expect(await viewport.evaluate(node => node.scrollLeft)).toBeCloseTo(480, 0);
});

test('all short pages load sequentially; any partial source stays labelled', async ({page}) => {
  const f = await start(page, 'en', {pageSize: 4});
  expect(f.calls).toHaveLength(7);
  for (const call of f.calls.slice(1)) expect(new URL(call, 'http://fixture').searchParams.get('after')).toMatch(/^[A-Za-z0-9_-]+$/);
  await expect(page.locator('.ui-page-header .schedule-meta [data-status=partial]')).toBeVisible();
  await expect(page.locator('.ui-page-header .schedule-meta')).toContainText('27 loaded');
  await expect(page.locator('[data-schedule-row="milestone:launch"]')).toContainText('Launch');
  await expect(page.locator('[data-schedule-track="milestone:design_review"] [data-schedule-bar=baseline]')).toBeAttached();
});

test('unobserved sessions and inconsistent dates stay honest; full multiline text is readable', async ({page}) => {
  const f = await start(page, 'en', {mutate: data => {
    const session = data.unscheduled.find(row => row.id.includes('manual-reset-notes')); session.refs = {session: session.refs.session}; session.title = session.refs.session.session_id;
    data.unknown.push({field: 'attempt.session', reason: 'session_not_observed'}, {field: 'work.actual', reason: 'inconsistent_dates'}, {field: 'future', reason: 'future_internal_code'});
    const work = data.rows.find(row => row.kind === 'work'); work.actual.end = '2025-01-01T00:00:00Z';
    const req = data.unscheduled.find(row => row.refs?.requirement_id === 'membership.email_sign_in'); req.title = 'First requirement line\nFull second line <script>safe text</script>';
  }});
  const region = page.locator('[data-pm-schedule]'); await region.locator('.schedule-unscheduled summary').click();
  const unobserved = region.locator('.schedule-unscheduled li').filter({hasText: f.ids.sessions.manual});
  await expect(unobserved).toContainText('Not observed yet');
  await region.getByRole('treeitem', {name: 'First requirement line Full second line <script>safe text</script>', exact: true}).click();
  await expect(page.locator('[data-schedule-inspector]')).toContainText('Full second line <script>safe text</script>');
  await page.locator('[data-schedule-inspector] > .ui-inspector-body > div > details > summary').click();
  await expect(page.locator('[data-schedule-inspector]')).toContainText('End precedes start; end omitted');
  await expect(page.locator('[data-schedule-inspector]')).toContainText('Not available');
  await expect(page.locator('script:not([src])')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(region.locator('.schedule-bar.actual.open').first()).toContainText('Open');
});

for (const native of [false, true]) test(`withdrawal clears rows, counts and inspector ${native ? 'IPC' : 'Web'}`, async ({page}) => {
  const f = await start(page, 'en', {native}); await item(page, `work:${f.ids.work.resetEmail}`).click();
  f.state.deny = true; await page.locator('.pm-main > header').getByRole('button', {name: /^(Refresh|重新整理)$/}).click();
  await expect(page.locator('[data-schedule-inspector]')).toHaveCount(0);
  await expect(page.locator('[data-pm-schedule] [data-schedule-row]')).toHaveCount(0);
  await expect(page.locator('.ui-page-header .schedule-meta')).toBeEmpty();
  await expect(page.locator('[data-pm-schedule] .schedule-unscheduled')).toBeEmpty();
  await expect(page.locator('[data-pm-schedule] [role=alert]')).toContainText('Schedule unavailable');
  expect(f.writes).toEqual([]);
});

test('late schedule response cannot revive data after workspace withdrawal', async ({page}) => {
  const f = await start(page); await item(page, `work:${f.ids.work.resetEmail}`).click();
  const overviewReads = f.state.overviewReads;
  f.state.defer = true; f.state.cursor++;
  await expect.poll(() => Boolean(f.state.pending)).toBe(true);
  expect(f.state.overviewReads, 'Hidden Overview does not refresh during a Schedule event').toBe(overviewReads);
  await expect(page.locator('.pm-main > header').getByRole('button', {name: 'Refresh', exact: true})).toBeEnabled();
  f.state.workspaceDeny = true;
  await page.locator('.pm-main > header').getByRole('button', {name: /^(Refresh|重新整理)$/}).click();
  await expect(page.locator('[data-schedule-inspector]')).toHaveCount(0);
  const completed = f.state.completed; f.state.pending!();
  await expect.poll(() => f.state.completed).toBe(completed + 1);
  await expect.poll(() => page.locator('[data-pm-schedule] [data-schedule-row]').count()).toBe(0);
  await expect(page.locator('.ui-page-header .schedule-meta')).toBeEmpty();
  expect(f.writes).toEqual([]);
});


test('a late success cannot restore an endpoint withdrawn by a newer read', async ({page}) => {
  const f = await start(page); await item(page, `work:${f.ids.work.resetEmail}`).click();
  const overviewReads = f.state.overviewReads;
  f.state.defer = true; f.state.cursor++;
  await expect.poll(() => Boolean(f.state.pending)).toBe(true);
  expect(f.state.overviewReads, 'Hidden Overview does not refresh during a Schedule event').toBe(overviewReads);
  await expect(page.locator('.pm-main > header').getByRole('button', {name: 'Refresh', exact: true})).toBeEnabled();
  f.state.deny = true; await page.locator('.pm-main > header').getByRole('button', {name: /^(Refresh|重新整理)$/}).click();
  await expect(page.locator('[data-pm-schedule] [role=alert]')).toContainText('Schedule unavailable');
  const completed = f.state.completed; f.state.pending!();
  await expect.poll(() => f.state.completed).toBe(completed + 1);
  await expect(page.locator('[data-schedule-inspector]')).toHaveCount(0);
  await expect(page.locator('[data-pm-schedule] [data-schedule-row]')).toHaveCount(0);
  await expect(page.locator('.ui-page-header .schedule-meta')).toBeEmpty();
  await expect(page.locator('[data-pm-schedule] .schedule-unscheduled')).toBeEmpty(); expect(f.writes).toEqual([]);
});

for (const native of [false, true]) test(`roadmap drawer retains drafts and original-key recovery ${native ? 'IPC' : 'Web'}`, async ({page}) => {
  const f = roadmapFixture();
  await mountConversation(page, native, async input => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path.endsWith('/schedule')) return {status: 200, data: {workspace_id: path.split('/')[3], version: 1, as_of: '2026-10-13T12:00:00Z',
      rows: [], unscheduled: [], baseline: null, forecast: null, range: null, partial: false, truncated: false, next_cursor: null, unknown: [], coverage: {}}};
    const result = await f.dispatch(input);
    if (['/bootstrap','/capabilities'].includes(path)) (path === '/bootstrap' ? result.data.capabilities : result.data).features.pm_schedule = {version: 1};
    return result;
  });
  await page.goto(roadmapPath + '/schedule');
  const open = async () => {await page.locator('[data-schedule-tools]').click(); await page.getByRole('menuitem', {name: 'Roadmap', exact: true}).click();};
  await open(); await page.getByText('Update a forecast', {exact: true}).click();
  const form = page.locator('[data-pm-action="pm.roadmap.forecast.set"]');
  await form.getByLabel('Milestone', {exact: true}).selectOption('first_release');
  await form.getByLabel('Reason for this change', {exact: true}).fill('Keep the draft and the exact operation');
  await page.keyboard.press('Escape'); await open();
  await expect(form.getByLabel('Reason for this change', {exact: true})).toHaveValue('Keep the draft and the exact operation');
  f.state.lost = true; await form.getByRole('button', {name: 'Save forecast', exact: true}).click();
  await expect(form).toContainText('Reply unavailable');
  await page.reload(); await open(); await page.getByText('Update a forecast', {exact: true}).click();
  await form.getByRole('button', {name: 'Check original request', exact: true}).click();
  await expect.poll(() => f.state.writes.length).toBe(2);
  expect(f.state.writes).toHaveLength(2); expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
  expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key); expect(f.base.state.writes).toHaveLength(0);
});

test('statuses stay distinct and only an explicit blocked status adds hatching', async ({page}) => {
  const project = demoProject();
  const f = await start(page, 'en', {mutate: data => {
    for (const [key, status] of Object.entries({email: 'accepted', rate: 'merged', resetEmail: 'deployed', resetToken: 'blocked'})) {
      data.rows.find(row => row.id === `work:${project.ids.work[key]}`).status = status;
    }
    data.rows.find(row => row.id === `work:${project.ids.work.resetToken}`).parent_id = null;
  }});
  for (const [key, status] of Object.entries({email: 'accepted', rate: 'merged', resetEmail: 'deployed', resetToken: 'blocked', audit: 'waiting'})) {
    await expect(item(page, `work:${f.ids.work[key]}`).locator(`[data-status=${status}]`)).toHaveCount(1);
  }
  await expect(page.getByRole('treeitem', {name: 'Not under a milestone', exact: true})).toBeAttached();
  await expect(page.locator('.schedule-bar.blocked')).toHaveCount(1);
  await expect(page.locator(`[data-schedule-track="work:${f.ids.work.resetToken}"] .schedule-bar.blocked`)).toHaveCount(1);
  await item(page, `work:${f.ids.work.rate}`).click();
  await expect(page.locator('[data-schedule-section=delivery] [data-status=merged]')).toBeVisible();
  await item(page, `work:${f.ids.work.resetEmail}`).click(); await page.keyboard.press('Escape');
  await expect(item(page, `work:${f.ids.work.resetEmail}`)).toBeFocused(); expect(f.writes).toEqual([]);
});

test('ten-page limit has a visible Partial state and a reason in Details', async ({page}) => {
  const f = await start(page, 'en', {pageSize: 1}); expect(f.calls).toHaveLength(10);
  const meta = page.locator('.ui-page-header .schedule-meta');
  await expect(meta).toContainText('10 loaded'); await expect(meta.locator('[data-status=partial]')).toBeVisible();
  await meta.locator('summary').click(); await expect(meta).toContainText('Refresh page limit reached'); expect(f.writes).toEqual([]);
});

// Capabilities can advertise the projection without mounting a Brief/Overview view.
test('schedule events still refresh without a mounted Overview', async ({page}) => {
  const f = await start(page, 'en', {withoutBrief: true});
  await expect(page.locator('[data-pm-overview]')).toHaveCount(0);
  const reads = f.calls.length;
  f.state.cursor++;
  await expect.poll(() => f.calls.length).toBeGreaterThan(reads);
  await expect(page.locator('.ui-page-header .schedule-meta')).toContainText('27 items');
  expect(f.state.overviewReads).toBe(0); expect(f.writes).toEqual([]);
});

test('terminal span without end time reads End time not recorded and mobile date ranges format cleanly', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  const project = demoProject();
  const rowId = `attempt:operation:${project.ids.work.email}:op_unrecorded_end`;
  const f = await start(page, 'en', {mutate: data => {
    data.rows.push({
      id: rowId, parent_id: `work:${project.ids.work.email}`, kind: 'attempt', title: 'session.start (unrecorded end)',
      status: 'done', blocked: null, waiting: null, depends_on: [], refs: {operation_id: 'op_unrecorded_end', agent: {cli: 'codex'}},
      baseline: null, forecast: null, actual: {start: '2026-10-08T10:00:00Z', end: null, open: false},
    });
    data.unknown.push({field: `${rowId}.actual.end`, reason: 'terminal_time_unrecorded'});
    data.rows.push({
      id: 'work:forecast_start_only', parent_id: null, kind: 'work', title: 'Forecast start only',
      status: 'not_started', blocked: null, waiting: null, depends_on: [], refs: {work_item_id: 'forecast_start_only'},
      baseline: null, forecast: {start: '2026-10-21', end: null}, actual: null,
    });
  }});
  const bar = page.locator(`[data-schedule-track="${rowId}"] .schedule-bar`);
  await expect(bar).not.toHaveClass(/\bopen\b/);
  await expect(bar).not.toHaveAttribute('data-open', 'true');
  await expect(bar).toHaveText('End time not recorded');
  await expect(bar).toHaveAttribute('title', 'Actual · Oct 8 → End time not recorded');
  await item(page, rowId).click();
  const inspector = page.locator('[data-schedule-inspector]');
  await inspector.locator('[data-schedule-section="details"] > summary').click();
  await expect(inspector.locator('[data-schedule-section="details"]')).toContainText('End time not recorded');
  await expect(inspector.locator('[data-schedule-section="details"]')).not.toContainText('→ Open');
  await page.setViewportSize({width: 390, height: 844});
  await expect(page.locator(`[data-schedule-row="${rowId}"] .schedule-date-range`)).toHaveText('Actual Oct 8 → End time not recorded');
  const forecastBar = page.locator('[data-schedule-track="work:forecast_start_only"] .schedule-bar');
  await expect(forecastBar).toHaveAttribute('title', 'Forecast · Oct 21');
  await expect(forecastBar).not.toContainText('End time not recorded');
  await expect(page.locator(`[data-schedule-row^="attempt:operation:${f.ids.work.resetEmail}:"] .schedule-date-range`)).toHaveText('Actual Oct 6');
  await expect(page.locator(`[data-schedule-row="work:${f.ids.work.resetEmail}"] .schedule-date-range`)).toHaveText('Actual Oct 5 → Open');
});

test('Unscheduled excludes rows already in the tree, omits requirement status pills, and hides when zero', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await start(page, 'en');
  const region = page.locator('[data-pm-schedule]');
  await expect(region.locator('.schedule-unscheduled summary')).toHaveText('Unscheduled (6)');
  await region.locator('.schedule-unscheduled summary').click();
  const overlap = await region.evaluate(root => {
    const treeIds = new Set([...root.querySelectorAll('.schedule-chart [data-schedule-select]')].map(el => (el as HTMLElement).dataset.scheduleSelect));
    const unschedIds = [...root.querySelectorAll('.schedule-unscheduled [data-schedule-select]')].map(el => (el as HTMLElement).dataset.scheduleSelect);
    const reqPills = [...root.querySelectorAll('.schedule-unscheduled li')].filter(li => li.querySelector('[data-schedule-select^="requirement:"]')).flatMap(li => [...li.querySelectorAll('[data-status]')]);
    const emptyMetaSpans = [...root.querySelectorAll('.schedule-meta > span')].filter(s => !s.textContent?.trim());
    return {duplicates: unschedIds.filter(id => treeIds.has(id)), reqPillsCount: reqPills.length, emptyMetaSpansCount: emptyMetaSpans.length};
  });
  expect(overlap).toEqual({duplicates: [], reqPillsCount: 0, emptyMetaSpansCount: 0});
});

test('empty Unscheduled renders no disclosure and meta has no empty span', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await start(page, 'en', {mutate: data => { data.unscheduled = []; }});
  const region = page.locator('[data-pm-schedule]');
  await expect(region.locator('.schedule-unscheduled details')).toHaveCount(0);
  expect(await region.locator('.schedule-meta > span').evaluateAll(nodes => nodes.filter(n => !n.textContent?.trim()).length)).toBe(0);
});

test('tree keeps a single tab stop, aria-selected only on treeitems, accessible row descriptions, and no lane tab stops', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  const f = await start(page, 'en');
  const region = page.locator('[data-pm-schedule]');
  const countZeroTabindex = () => region.locator('[role="treeitem"][tabindex="0"]').count();
  expect(await countZeroTabindex()).toBe(1);

  const workItem = item(page, `work:${f.ids.work.resetEmail}`);
  await expect(workItem).toHaveAccessibleName(f.copy.work.resetEmail);
  await expect(workItem).toHaveAccessibleDescription(/In progress.*Actual Oct 5 → Open/);
  await workItem.click();
  expect(await countZeroTabindex()).toBe(1);
  await expect(workItem).toHaveAttribute('tabindex', '0');

  await region.locator('.schedule-unscheduled summary').click();
  const unscheduledBtn = region.locator('.schedule-unscheduled [data-schedule-select]').first();
  await unscheduledBtn.click();
  await region.getByRole('radio', {name: 'Day', exact: true}).click();
  expect(await countZeroTabindex()).toBe(1);
  expect(await region.locator('[aria-selected]:not([role="treeitem"])').count()).toBe(0);

  await item(page, `requirement:${f.ids.planV3}:${f.ids.requirements.resetEmail}`).click();
  await item(page, 'milestone:recovery_review').locator('button').click();
  expect(await countZeroTabindex()).toBe(1);

  expect(await region.locator('.schedule-lane[tabindex], .schedule-lane[role="region"]').count()).toBe(0);
  await expect(region.locator('.schedule-axis .schedule-today')).toHaveAttribute('role', 'img');
});

test('roadmap drawer stays the only open inspector when a live refresh runs with a selected schedule row', async ({page}) => {
  const f = await start(page, 'en');
  await item(page, `work:${f.ids.work.resetEmail}`).click();
  await expect(page.locator('[data-schedule-inspector]')).toBeVisible();
  await page.locator('[data-schedule-tools]').click();
  await page.getByRole('menuitem', {name: 'Roadmap', exact: true}).click();
  await expect(page.getByText('Update a forecast')).toBeVisible();
  await expect(page.locator('[data-schedule-inspector]')).toHaveCount(0);
  const reads = f.calls.length;
  f.state.eventType = 'pm_workspace';
  f.state.cursor++;
  await expect.poll(() => f.calls.length).toBeGreaterThan(reads);
  await expect(page.getByText('Update a forecast')).toBeVisible();
  await expect(page.locator('[data-schedule-inspector]')).toHaveCount(0);
  await expect(page.locator('.ui-inspector')).toHaveCount(1);
});

test('open work item bars include Waiting and Blocked labels and delta/bar labels meet 4.5:1 contrast in light and dark mode', async ({page}) => {
  const project = demoProject();
  const f = await start(page, 'en', {mutate: data => {
    const emailRow = data.rows.find((r: any) => r.id === `work:${project.ids.work.resetEmail}`);
    if (emailRow) emailRow.status = 'blocked';
  }});
  await expect(page.locator(`[data-schedule-track="work:${f.ids.work.audit}"] .schedule-bar-label`)).toHaveText('Waiting · Open');
  await expect(page.locator(`[data-schedule-track="work:${f.ids.work.resetEmail}"] .schedule-bar-label`)).toHaveText('Blocked · Open');

  for (const colorScheme of ['light', 'dark'] as const) {
    await page.emulateMedia({colorScheme});
    const ratios = await page.evaluate(() => {
      const parse = (c: string) => (c.match(/[\d.]+/g) || [0, 0, 0]).slice(0, 3).map(Number);
      const lum = ([r, g, b]: number[]) => {
        const f = (v: number) => {const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;};
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
      };
      const cr = (a: number[], b: number[]) => {const l1 = lum(a), l2 = lum(b); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);};
      const chartBg = parse(getComputedStyle(document.querySelector('.schedule-chart')!).backgroundColor);
      const deltaCs = getComputedStyle(document.querySelector('.schedule-delta')!);
      const labelCs = getComputedStyle(document.querySelector('.schedule-bar-label')!);
      return {
        deltaOnChart: cr(parse(deltaCs.color), chartBg),
        deltaOnOwnBg: cr(parse(deltaCs.color), parse(deltaCs.backgroundColor)),
        labelOnChart: cr(parse(labelCs.color), chartBg),
        labelOnOwnBg: cr(parse(labelCs.color), parse(labelCs.backgroundColor)),
      };
    });
    expect(ratios.deltaOnChart).toBeGreaterThanOrEqual(4.5);
    expect(ratios.deltaOnOwnBg).toBeGreaterThanOrEqual(4.5);
    expect(ratios.labelOnChart).toBeGreaterThanOrEqual(4.5);
    expect(ratios.labelOnOwnBg).toBeGreaterThanOrEqual(4.5);
  }
});

test('inspector shows visible schedule dates and milestone delta, links milestones to a valid requirement, and resolves issue row requirements and repo#number', async ({page}) => {
  const project = demoProject();
  const f = await start(page, 'en', {mutate: data => {
    data.unscheduled.push({
      id: `issue:${project.ids.issueMapping}`, parent_id: `work:${project.ids.work.email}`, kind: 'issue',
      title: 'Issue #42: Email sign-in error state', status: 'in_progress', blocked: null, waiting: null, depends_on: [],
      refs: {
        mapping_id: project.ids.issueMapping, work_item_id: project.ids.work.email,
        requirement_ids: [project.ids.requirements.email],
        issue: {provider: 'github', repository: project.ids.repository, number: 42},
      },
    });
  }});
  await expect(page.locator('[data-schedule-track="milestone:recovery_review"] [data-schedule-bar="baseline"]')).toHaveAttribute('title', 'Baseline · Oct 16');
  await item(page, 'milestone:recovery_review').click();
  const inspector = page.locator('[data-schedule-inspector]');
  const statusSection = inspector.locator('[data-schedule-section="status"]');
  await expect(statusSection).toContainText('Baseline: Oct 16');
  await expect(statusSection).toContainText('Forecast: Oct 19');
  await expect(statusSection).toContainText('+3d');
  await expect(statusSection).not.toContainText('2026-10-16 → 2026-10-16');

  await inspector.locator('a[href*="/requirements"]').click();
  await expect(page.locator('[data-pm-rtm]')).toBeVisible();
  await expect(page.locator('[data-pm-rtm] [aria-selected="true"]')).toHaveCount(1);

  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/schedule`);
  const region = page.locator('[data-pm-schedule]');
  await region.locator('.schedule-unscheduled summary').click();
  await region.locator(`.schedule-unscheduled [data-schedule-select="issue:${project.ids.issueMapping}"]`).click();
  await expect(inspector.locator('[data-schedule-section="requirement"]')).toContainText(f.copy.requirements[0].text);
  await inspector.locator('[data-schedule-section="details"] > summary').click();
  await expect(inspector.locator('[data-schedule-section="details"]')).toContainText(`${project.ids.repository}#42`);
});

for (const locale of ['en', 'zh-TW']) test(`attempt action names and interval tooltips are localized ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({locale, baseURL}), page = await context.newPage();
  const f = await start(page, locale);
  const attempt = page.locator('.schedule-chart [data-schedule-select^="attempt:operation:"]');
  const name = locale === 'en' ? 'Start session' : '開始工作階段';
  await expect(attempt).toHaveAccessibleName(name);
  await expect(attempt.locator('.schedule-title')).toHaveText(name);
  await expect(attempt.locator('[title]')).toHaveCount(0);
  await attempt.focus();
  await expect(page.getByRole('tooltip')).toHaveText(name);
  await expect(page.getByRole('tooltip')).toBeVisible();
  const bar = page.locator('[data-schedule-track^="attempt:operation:"] .schedule-bar');
  const interval = locale === 'en' ? 'Actual · Oct 6' : '實際 · 10月6日';
  await expect(bar).toHaveAttribute('title', interval);
  await expect(bar).toHaveAccessibleName(interval);
  const tooltips = await page.locator('.schedule-bar').evaluateAll(nodes => nodes.map(node => node.getAttribute('title')));
  expect(tooltips.join('\n')).not.toMatch(/\d{4}-\d{2}-\d{2}|session\.start/);
  await attempt.click();
  const inspector = page.locator('[data-schedule-inspector]');
  await expect(inspector.getByRole('heading', {level: 2})).toHaveText(name);
  await inspector.locator('[data-schedule-section="details"] > summary').click();
  await expect(inspector.locator('[data-schedule-section="details"] code').filter({hasText: /^session\.start$/})).toBeVisible();
  await page.keyboard.press('Escape');
  await item(page, `work:${f.ids.work.resetEmail}`).click();
  await expect(inspector.locator('[data-schedule-attempt^="attempt:operation:"] strong')).toHaveText(name);
  expect(f.writes).toEqual([]);
  await context.close();
});

test('viewport has no nested vertical scroll cap, Day ticks and Today marker never collide in EN or zh-TW, and mobile rows share one horizontal origin', async ({browser, baseURL}) => {
  for (const locale of ['en', 'zh-TW'] as const) {
    const context = await browser.newContext({locale, baseURL, viewport: {width: 1440, height: 900}}), page = await context.newPage();
    await start(page, locale);
    const vpStyle = await page.locator('.schedule-viewport').evaluate(el => ({
      maxHeight: getComputedStyle(el).maxHeight,
      overflowY: getComputedStyle(el).overflowY,
    }));
    expect(vpStyle.maxHeight).toBe('none');

    const zooms = locale === 'en' ? ['Day', 'Week', 'Month'] : ['日', '週', '月'];
    for (const colorScheme of ['light', 'dark'] as const) for (const label of zooms) {
      await page.emulateMedia({colorScheme});
      await page.getByRole('radio', {name: label, exact: true}).click();
      await page.getByRole('button', {name: locale === 'en' ? 'Today' : '今天', exact: true}).click();
      const check = await page.evaluate(() => {
        const axis = document.querySelector('.schedule-axis') as HTMLElement;
        const axisRect = axis.getBoundingClientRect();
        const todayRect = axis.querySelector('.schedule-today')!.getBoundingClientRect();
        const ticks = [...axis.querySelectorAll('.schedule-tick')].map(el => {
          const r = el.getBoundingClientRect();
          const range = document.createRange();
          range.selectNodeContents(el);
          const tr = range.getBoundingClientRect();
          return {boxLeft: r.left - axisRect.left, boxRight: r.right - axisRect.left, textRight: tr.right - axisRect.left, textBottom: tr.bottom, textLeft: tr.left, textRightAbs: tr.right};
        });
        let overlaps = 0, todayCutsText = false;
        for (let i = 0; i < ticks.length - 1; i++) {
          if (ticks[i + 1].boxLeft - ticks[i].boxRight < 0 || ticks[i + 1].boxLeft + 8 - ticks[i].textRight < 2) overlaps++;
        }
        for (const tk of ticks) {
          if (todayRect.left >= tk.textLeft - 1 && todayRect.left <= tk.textRightAbs + 1 && todayRect.top < tk.textBottom) todayCutsText = true;
        }
        const scrollY = window.scrollY;
        for (const label of document.querySelectorAll('.schedule-bar-label, .schedule-delta')) {
          const line = label.closest('.schedule-track')!.querySelector('.schedule-today') as HTMLElement;
          const range = document.createRange(); range.selectNodeContents(label);
          const textRect = range.getBoundingClientRect(), lineRect = line.getBoundingClientRect();
          const style = getComputedStyle(label), color = style.backgroundColor;
          const opaque = color.startsWith('rgb(') || (color.startsWith('rgba(') && Number(color.match(/[\d.]+/g)?.[3]) === 1);
          // Both label types must cover the line, even if this fixture's delta is later than Today.
          if (!opaque || Number(style.zIndex) <= Number(getComputedStyle(line).zIndex)) todayCutsText = true;
          if (lineRect.left < textRect.left || lineRect.left > textRect.right) continue;
          // Hit-test the actual paint order at the intersection, including bar ancestors.
          window.scrollBy(0, textRect.top - innerHeight / 2);
          const visibleText = range.getBoundingClientRect(), visibleLine = line.getBoundingClientRect();
          const previous = line.style.pointerEvents; line.style.pointerEvents = 'auto';
          const layers = document.elementsFromPoint(visibleLine.left + .25, (visibleText.top + visibleText.bottom) / 2);
          line.style.pointerEvents = previous;
          if (layers.indexOf(label) < 0 || layers.indexOf(line) < 0 || layers.indexOf(label) > layers.indexOf(line)) todayCutsText = true;
        }
        window.scrollTo(0, scrollY);
        return {overlaps, todayCutsText};
      });
      expect(check).toEqual({overlaps: 0, todayCutsText: false});
    }

    await page.setViewportSize({width: 390, height: 844});
    const mobileAlign = async () => page.evaluate(() => {
      const lanes = [...document.querySelectorAll('.schedule-row[data-has-dates="true"] .schedule-lane')] as HTMLElement[];
      const scrolls = new Set(lanes.map(l => Math.round(l.scrollLeft)));
      const todayX = new Set(lanes.map(l => Math.round(l.querySelector('.schedule-today')!.getBoundingClientRect().left)));
      return {laneCount: lanes.length, uniqueScrolls: scrolls.size, uniqueTodayX: todayX.size};
    });
    expect(await mobileAlign()).toEqual({laneCount: 14, uniqueScrolls: 1, uniqueTodayX: 1});
    await page.getByRole('button', {name: locale === 'en' ? 'Today' : '今天', exact: true}).click();
    expect(await mobileAlign()).toEqual({laneCount: 14, uniqueScrolls: 1, uniqueTodayX: 1});
    await page.locator('.schedule-row[data-has-dates="true"] .schedule-lane').first().evaluate(node => {
      node.scrollLeft = 180;
      node.dispatchEvent(new Event('scroll'));
    });
    expect(await mobileAlign()).toEqual({laneCount: 14, uniqueScrolls: 1, uniqueTodayX: 1});
    await context.close();
  }
});

test('sticky titles cover horizontally scrolled bar labels in both locales and themes', async ({browser, baseURL}) => {
  for (const locale of ['en', 'zh-TW']) {
    const context = await browser.newContext({locale, baseURL, viewport: {width: 1440, height: 900}}), page = await context.newPage();
    const f = await start(page, locale);
    for (const colorScheme of ['light', 'dark'] as const) for (const zoom of locale === 'en' ? ['Day', 'Week'] : ['日', '週']) {
      // Week fits most of the desktop chart; use a narrower column so scrolling
      // can bring an open-bar label underneath the sticky title cell.
      await page.setViewportSize({width: ['Day', '日'].includes(zoom) ? 1440 : 1000, height: 900});
      await page.emulateMedia({colorScheme});
      await page.getByRole('radio', {name: zoom, exact: true}).click();
      expect(await page.evaluate(() => {
        const viewport = document.querySelector('.schedule-viewport') as HTMLElement;
        const label = document.querySelector('.schedule-bar-label')!;
        const cell = label.closest('.schedule-row')!.querySelector('.schedule-tree-cell')!;
        const previous = viewport.scrollLeft, title = cell.getBoundingClientRect();
        viewport.scrollLeft += label.getBoundingClientRect().left - (title.left + title.width / 2);
        const rect = label.getBoundingClientRect();
        const coversLabel = cell.contains(document.elementFromPoint(rect.left + 4, (rect.top + rect.bottom) / 2));
        viewport.scrollLeft = previous;
        return coversLabel;
      })).toBe(true);
    }
    expect(f.writes).toEqual([]);
    await context.close();
  }
});

for (const colorScheme of ['light', 'dark'] as const) test(`Month grid fills every lane without stretching its date scale ${colorScheme}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, colorScheme, viewport: {width: 1440, height: 900}}), page = await context.newPage();
  const f = await start(page);
  await page.getByRole('radio', {name: 'Month', exact: true}).click();
  const geometry = () => page.evaluate(() => ({
    axis: document.querySelector('.schedule-axis')!.getBoundingClientRect().right,
    chart: document.querySelector('.schedule-chart')!.getBoundingClientRect().right,
    lanes: [...document.querySelectorAll('.schedule-lane')].map(lane => ({
      lane: lane.getBoundingClientRect().right, track: lane.querySelector('.schedule-track')!.getBoundingClientRect().right,
    })),
    positions: [...document.querySelectorAll('.schedule-bar, .schedule-delta, .schedule-today')].map(node => (node as HTMLElement).style.left),
    scaleWidth: (document.querySelector('.schedule-chart') as HTMLElement).style.getPropertyValue('--schedule-width'),
  }));
  const desktop = await geometry();
  expect(Math.abs(desktop.axis - desktop.chart)).toBeLessThan(1);
  expect(desktop.lanes.length).toBeGreaterThan(10);
  for (const lane of desktop.lanes) {
    expect(Math.abs(lane.track - desktop.chart)).toBeLessThan(1);
    expect(Math.abs(lane.track - lane.lane)).toBeLessThan(1);
  }
  await page.setViewportSize({width: 390, height: 844});
  await expect(page.locator('[data-pm-schedule]')).toHaveAttribute('data-schedule-layout', 'phone');
  const mobile = await geometry();
  const dated = mobile.lanes.filter(lane => lane.lane > 0 && lane.track > 0);
  expect(dated.length).toBeGreaterThan(5);
  for (const lane of dated) expect(Math.abs(lane.track - lane.lane)).toBeLessThan(2);
  await expect(page.locator('[data-schedule-axis-caption]')).toBeVisible();
  await noOverflow(page);
  expect(f.writes).toEqual([]);
  await context.close();
});

test('Propose change uses Change: {firstLine} and omits milestone ref when milestone has no baseline in baseline revision', async ({page}) => {
  const project = demoProject();
  const f = await start(page, 'en', {mutate: data => {
    const req = data.unscheduled.find((r: any) => r.refs?.requirement_id === project.ids.requirements.resetEmail);
    if (req) req.title = 'First line of requirement\nSecond line with details';
    const launch = data.rows.find((r: any) => r.id === 'milestone:launch');
    if (launch) launch.baseline = null;
  }});
  await item(page, `requirement:${f.ids.planV3}:${f.ids.requirements.resetEmail}`).click();
  const inspector = page.locator('[data-schedule-inspector]');
  const reqHref = await inspector.locator('[data-schedule-discuss]').getAttribute('href');
  expect(parseDiscussRefs(reqHref!.split('?')[1])).toEqual({
    refs: [{kind: 'requirement', id: f.ids.requirements.resetEmail, revision_id: f.ids.planV3}],
    title: 'Change: First line of requirement',
    from: 'schedule',
    report: '',
  });

  await item(page, 'milestone:launch').click();
  const launchHref = await inspector.locator('[data-schedule-discuss]').getAttribute('href');
  expect(parseDiscussRefs(launchHref!.split('?')[1])).toEqual({
    refs: [],
    title: 'Change: Launch',
    from: 'schedule',
    report: '',
  });
});

test('hidden Schedule does not fetch until active, live updates keep focus in Unscheduled and Details, and 413 renders without Retry', async ({page}) => {
  let version = 1;
  const f = await start(page, 'en', {initialView: 'overview', mutate: data => {
    data.unknown.push({field: `rev_${version}`, reason: 'session_not_observed'});
  }});
  await expect(page.locator('[data-pm-overview]')).toBeVisible();
  expect(f.calls).toHaveLength(0);
  f.state.cursor++;
  await page.waitForTimeout(300);
  expect(f.calls).toHaveLength(0);

  await page.evaluate(id => {location.hash = `#/pm/${id}/schedule`;}, f.ids.workspaceId);
  await expect(page.locator('.ui-page-header .schedule-meta')).toContainText('27 items');
  expect(f.calls).toHaveLength(1);

  const region = page.locator('[data-pm-schedule]');
  await region.locator('.schedule-unscheduled summary').click();
  const unscheduledBtn = region.locator('.schedule-unscheduled [data-schedule-select]').first();
  await unscheduledBtn.focus();
  await expect(unscheduledBtn).toBeFocused();
  version++;
  f.state.cursor++;
  await expect.poll(() => f.calls.length).toBe(2);
  await expect(unscheduledBtn).toBeFocused();

  const metaSummary = page.locator('.ui-page-header .schedule-meta summary');
  await metaSummary.focus();
  await expect(metaSummary).toBeFocused();
  version++;
  f.state.cursor++;
  await expect.poll(() => f.calls.length).toBe(3);
  await expect(metaSummary).toBeFocused();

  f.state.tooLarge = true;
  await page.locator('.pm-main > header').getByRole('button', {name: 'Refresh', exact: true}).click();
  const alert = region.locator('[role="alert"]');
  await expect(alert).toContainText('Schedule exceeds the size limit.');
  await expect(alert.locator('button')).toHaveCount(0);
});

test('UTC calendar days render identical ticks, bars, and meta in Pacific/Honolulu and Asia/Taipei', async ({browser, baseURL}) => {
  const snapshots: Record<string, any> = {};
  for (const timezoneId of ['Pacific/Honolulu', 'Asia/Taipei']) {
    const context = await browser.newContext({locale: 'en', baseURL, viewport: {width: 1440, height: 900}, timezoneId}), page = await context.newPage();
    await start(page, 'en');
    snapshots[timezoneId] = await page.evaluate(() => ({
      offset: new Date().getTimezoneOffset(),
      meta: document.querySelector('.schedule-meta')?.textContent,
      ticks: [...document.querySelectorAll('.schedule-tick')].map(el => ({text: el.textContent, left: (el as HTMLElement).style.left})),
      bars: [...document.querySelectorAll('.schedule-bar')].map(el => ({
        track: (el.closest('[data-schedule-track]') as HTMLElement)?.dataset.scheduleTrack,
        kind: (el as HTMLElement).dataset.scheduleBar,
        left: (el as HTMLElement).style.left,
        width: (el as HTMLElement).style.width,
        title: el.getAttribute('title'),
      })),
    }));
    await context.close();
  }
  expect(snapshots['Pacific/Honolulu'].offset).toBe(600);
  expect(snapshots['Asia/Taipei'].offset).toBe(-480);
  expect(snapshots['Pacific/Honolulu'].meta).toBe(snapshots['Asia/Taipei'].meta);
  expect(snapshots['Pacific/Honolulu'].ticks).toEqual(snapshots['Asia/Taipei'].ticks);
  expect(snapshots['Pacific/Honolulu'].bars).toEqual(snapshots['Asia/Taipei'].bars);
});

function rowOverflows(page: Page) {
  return page.evaluate(() => {
    const bad: {id: string; cls: string; left: number; right: number; rowLeft: number; rowRight: number}[] = [];
    for (const row of document.querySelectorAll('.schedule-row[data-schedule-row]')) {
      const host = row.getBoundingClientRect();
      if (host.width < 2) continue;
      for (const el of row.querySelectorAll('.schedule-bar, .schedule-bar-label, .schedule-delta, .schedule-tick')) {
        const box = el.getBoundingClientRect();
        if (box.width < 0.5 || box.height < 0.5) continue;
        if (box.left < host.left - 0.5 || box.right > host.right + 0.5 || box.top < host.top - 0.5 || box.bottom > host.bottom + 0.5) {
          bad.push({id: (row as HTMLElement).dataset.scheduleRow || '', cls: (el as HTMLElement).className, left: box.left, right: box.right, rowLeft: host.left, rowRight: host.right});
        }
      }
    }
    return bad;
  });
}

for (const locale of ['en', 'zh-TW'] as const) test(`phone schedule fits bars inside each row and shows the shared range ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({locale, baseURL, viewport: {width: 390, height: 844}}), page = await context.newPage();
  await start(page, locale);
  const region = page.locator('[data-pm-schedule]');
  await expect(region).toHaveAttribute('data-schedule-layout', 'phone');
  await expect(region.getByRole('radio', {name: locale === 'en' ? 'Day' : '日', exact: true})).toHaveCount(0);
  const caption = region.locator('[data-schedule-axis-caption]');
  await expect(caption).toBeVisible();
  await expect(caption).toHaveText(locale === 'en' ? /\S+ – \S+/ : /\S+至\S+/);
  await expect(region.locator('.schedule-row[data-has-dates="true"] .schedule-today').first()).toBeVisible();
  await expect(region.locator('.schedule-bar-label')).toHaveCount(0);
  expect(await rowOverflows(page)).toEqual([]);
  const bounds = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('.schedule-row[data-has-dates="true"]')] as HTMLElement[];
    const lanes = rows.map(row => row.querySelector('.schedule-lane')!.getBoundingClientRect().width);
    const tracks = rows.map(row => row.querySelector('.schedule-track')!.getBoundingClientRect().width);
    const today = rows.map(row => Math.round(row.querySelector('.schedule-today')!.getBoundingClientRect().left));
    return {lanes, tracks, todaySpread: Math.max(...today) - Math.min(...today)};
  });
  expect(bounds.lanes.length).toBeGreaterThan(5);
  for (let i = 0; i < bounds.lanes.length; i++) expect(Math.abs(bounds.tracks[i] - bounds.lanes[i])).toBeLessThan(2);
  expect(bounds.todaySpread).toBeLessThan(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await context.close();
});

for (const width of [1000, 1440]) for (const locale of ['en', 'zh-TW'] as const) test(`axis labels stay clear of each other and the sticky column ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({locale, baseURL, viewport: {width, height: 900}}), page = await context.newPage();
  await start(page, locale);
  const region = page.locator('[data-pm-schedule]');
  const zooms = locale === 'en' ? [['Week', 'week'], ['Day', 'day'], ['Month', 'month']] : [['週', 'week'], ['日', 'day'], ['月', 'month']];
  for (const [label, zoom] of zooms) {
    await region.getByRole('radio', {name: label, exact: true}).click();
    const axis = await page.evaluate(() => {
      const row = document.querySelector('.schedule-head')!;
      const sticky = row.querySelector('.schedule-tree-cell')!.getBoundingClientRect();
      const ticks = [...row.querySelectorAll('.schedule-tick')].map(el => {
        const box = el.getBoundingClientRect();
        return {iso: (el as HTMLElement).dataset.scheduleTick || '', left: box.left, right: box.right, width: box.width};
      });
      return {stickyRight: sticky.right, ticks};
    });
    expect(axis.ticks.length).toBeGreaterThan(1);
    expect(axis.ticks[0].left).toBeGreaterThanOrEqual(axis.stickyRight - 0.5);
    expect(axis.ticks[1].left - axis.ticks[0].left).toBeGreaterThanOrEqual(axis.ticks[0].width - 0.5);
    for (let i = 1; i < axis.ticks.length; i++) expect(axis.ticks[i].left).toBeGreaterThanOrEqual(axis.ticks[i - 1].right - 0.5);
    if (zoom === 'week') {
      expect(new Date(`${axis.ticks[0].iso}T00:00:00Z`).getUTCDay()).toBe(1);
      expect(axis.ticks[0].iso).not.toBe('2026-09-25');
      expect(axis.ticks[0].iso).toBe('2026-09-28');
    }
  }
  await context.close();
});

for (const width of [1000, 1440]) for (const locale of ['en', 'zh-TW'] as const) test(`a scrolled redraw keeps the first axis labels ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({locale, baseURL, viewport: {width, height: 900}}), page = await context.newPage();
  const project = demoProject(locale);
  let stamp = 0;
  const f = await start(page, locale, {mutate(data) {
    if (!stamp) return;
    const row = data.rows.find(row => row.id === `work:${project.ids.work.email}`);
    if (row) row.title = `Redraw ${stamp}`;
  }});
  const region = page.locator('[data-pm-schedule]'), viewport = region.locator('.schedule-viewport');
  const names = locale === 'en' ? {week: 'Week', day: 'Day', month: 'Month'} : {week: '週', day: '日', month: '月'};
  const readAxis = () => page.evaluate(() => {
    const row = document.querySelector('.schedule-head')!;
    const sticky = row.querySelector('.schedule-tree-cell')!.getBoundingClientRect();
    const ticks = [...row.querySelectorAll('.schedule-tick')].map(el => {
      const box = el.getBoundingClientRect();
      return {iso: (el as HTMLElement).dataset.scheduleTick || '', left: box.left, right: box.right, width: box.width};
    });
    return {stickyRight: sticky.right, ticks};
  });
  const scrollToEnd = () => viewport.evaluate(node => {
    node.scrollLeft = node.scrollWidth;
    node.dispatchEvent(new Event('scroll'));
    return node.scrollLeft;
  });
  const scrollToStart = () => viewport.evaluate(node => {node.scrollLeft = 0; node.dispatchEvent(new Event('scroll'));});
  async function expectLabels(expected: string[]) {
    await scrollToStart();
    const axis = await readAxis();
    expect(axis.ticks.map(tick => tick.iso)).toEqual(expected);
    expect(axis.ticks[0].left).toBeGreaterThanOrEqual(axis.stickyRight - 0.5);
    for (let i = 1; i < axis.ticks.length; i++) expect(axis.ticks[i].left).toBeGreaterThanOrEqual(axis.ticks[i - 1].right - 0.5);
  }
  for (const zoom of ['week', 'day'] as const) {
    await region.getByRole('radio', {name: names[zoom], exact: true}).click();
    await scrollToStart();
    const initial = await readAxis();
    expect(initial.ticks.length).toBeGreaterThan(1);
    if (zoom === 'week') expect(initial.ticks[0].iso).toBe('2026-09-28');
    else expect(initial.ticks[0].iso).toBe('2026-09-25');
    const expected = initial.ticks.map(tick => tick.iso);
    expect(await scrollToEnd()).toBeGreaterThan(40);
    stamp += 1;
    const reads = f.calls.length;
    f.state.cursor++;
    await expect.poll(() => f.calls.length).toBeGreaterThan(reads);
    await expect(region.locator('.schedule-title', {hasText: `Redraw ${stamp}`})).toBeAttached();
    await expectLabels(expected);
    const other = zoom === 'week' ? 'day' : 'week';
    expect(await scrollToEnd()).toBeGreaterThan(40);
    await region.getByRole('radio', {name: names[other], exact: true}).click();
    expect(await scrollToEnd()).toBeGreaterThan(40);
    await region.getByRole('radio', {name: names[zoom], exact: true}).click();
    await expectLabels(expected);
  }
  expect(f.writes).toEqual([]);
  await context.close();
});

test('a Day choice is kept while the phone layout shows Week', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await start(page);
  const region = page.locator('[data-pm-schedule]'), viewport = region.locator('.schedule-viewport');
  await region.getByRole('radio', {name: 'Day', exact: true}).click();
  await viewport.evaluate(node => {node.scrollLeft = 240; node.dispatchEvent(new Event('scroll'));});
  await expect(region.locator('.schedule-weekend').first()).toBeAttached();
  await page.setViewportSize({width: 390, height: 844});
  await expect(region).toHaveAttribute('data-schedule-layout', 'phone');
  await expect(region.getByRole('radio', {name: 'Day', exact: true})).toHaveCount(0);
  await expect(region.getByRole('radio', {name: 'Week', exact: true})).toHaveAttribute('aria-checked', 'true');
  await expect(region.locator('[data-schedule-axis-caption]')).toBeVisible();
  await expect(region.locator('.schedule-weekend')).toHaveCount(0);
  expect(await rowOverflows(page)).toEqual([]);
  await page.setViewportSize({width: 1440, height: 900});
  await expect(region).toHaveAttribute('data-schedule-layout', 'board');
  await expect(region.getByRole('radio', {name: 'Day', exact: true})).toHaveAttribute('aria-checked', 'true');
  await expect(region.locator('.schedule-weekend').first()).toBeAttached();
  expect(await viewport.evaluate(node => node.scrollLeft)).toBeCloseTo(240, 0);
});
