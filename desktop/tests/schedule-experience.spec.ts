import {mkdirSync} from 'node:fs';
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
  if (view === 'schedule') await expect(page.locator('[data-pm-schedule] .schedule-meta')).toContainText(locale === 'en' ? /items|loaded/ : /共 .* 筆|已載入/);
  await expect(page.locator('.pm-main > header').getByRole('button', {name: /^(Refresh|重新整理)$/})).toBeEnabled();
  return {...fixture, state, calls, writes};
}
function item(page: Page, id: string) {return page.locator(`.schedule-chart [data-schedule-select="${id}"]`);}
async function noOverflow(page: Page) {expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);}
async function shot(page: Page, name: string) {
  if (!process.env.BATC_SCHEDULE_SCREENSHOTS) return;
  const dir = `${process.env.TMPDIR}/shots`; mkdirSync(dir, {recursive: true});
  await page.screenshot({path: `${dir}/${name}.png`});
}
for (const locale of ['en', 'zh-TW']) for (const width of [1440, 768, 390]) test(`schedule reading, inspector and keyboard ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({locale, baseURL, viewport: {width, height: width === 390 ? 844 : 900}}), page = await context.newPage();
  const errors: string[] = [], httpErrors: string[] = []; page.on('response', response => {if (response.status() >= 400) httpErrors.push(`${response.status()} ${new URL(response.url()).pathname}`);}); page.on('pageerror', error => errors.push(error.message)); page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
  const f = await start(page, locale);
  const region = page.locator('[data-pm-schedule]');
  await expect(region.locator('[data-schedule-delta]')).toHaveText(locale === 'en' ? '+3d' : '+3 天');
  await expect(region.locator('.schedule-chart [data-schedule-row^="attempt:session:"]')).toHaveCount(0);
  const open = region.locator(`[data-schedule-row="work:${f.ids.work.resetEmail}"] [data-open=true]`);
  await expect(open).toContainText(locale === 'en' ? 'Open' : '未結束');
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
  await expect(inspector.locator('a[href*="/session/"]')).toHaveCount(1);
  await expect(inspector.locator('[data-status=read_only]')).toBeVisible();
  await expect(inspector.locator('a[href*="run"]')).toHaveCount(0);
  const href = await inspector.locator('[data-schedule-discuss]').getAttribute('href');
  expect(parseDiscussRefs(href!.split('?')[1])).toEqual({refs: [{kind: 'work_item', id: f.ids.work.resetEmail}], title: locale === 'en' ? `Change: ${f.copy.work.resetEmail}` : `變更：${f.copy.work.resetEmail}`, from: 'schedule', report: ''});
  await expect(inspector.locator('a[href*="/requirements?select="]')).toHaveAttribute('href', `#/pm/${f.ids.workspaceId}/requirements?select=${f.ids.work.resetEmail}`);
  await noOverflow(page); await shot(page, `schedule-inspector-${locale}-${width}`);
  await page.keyboard.press('Escape'); await expect(inspector).toHaveCount(0); await expect(work).toBeFocused();
  await region.getByRole('radio', {name: locale === 'en' ? 'Day' : '日', exact: true}).click();
  await expect(region.locator('.schedule-weekend').first()).toBeAttached();
  await region.getByRole('button', {name: locale === 'en' ? 'Today' : '今天', exact: true}).click();
  if (width >= 800) expect(await region.locator('.schedule-viewport').evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
  else expect(await region.locator('.schedule-lane').nth(1).evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
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
  await region.getByRole('treeitem', {name: 'First requirement line', exact: true}).click();
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
  expect(f.state.overviewReads).toBeGreaterThan(overviewReads);
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
  expect(f.state.overviewReads).toBeGreaterThan(overviewReads);
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
  await expect(bar).toHaveAttribute('title', 'Actual · 2026-10-08T10:00:00Z → End time not recorded');
  await item(page, rowId).click();
  const inspector = page.locator('[data-schedule-inspector]');
  await inspector.locator('[data-schedule-section="details"] > summary').click();
  await expect(inspector.locator('[data-schedule-section="details"]')).toContainText('End time not recorded');
  await expect(inspector.locator('[data-schedule-section="details"]')).not.toContainText('→ Open');
  await page.setViewportSize({width: 390, height: 844});
  await expect(page.locator(`[data-schedule-row="${rowId}"] .schedule-date-range`)).toHaveText('Actual 2026-10-08 → End time not recorded');
  const forecastBar = page.locator('[data-schedule-track="work:forecast_start_only"] .schedule-bar');
  await expect(forecastBar).toHaveAttribute('title', 'Forecast · 2026-10-21');
  await expect(forecastBar).not.toContainText('End time not recorded');
  await expect(page.locator(`[data-schedule-row^="attempt:operation:${f.ids.work.resetEmail}:"] .schedule-date-range`)).toHaveText('Actual 2026-10-06');
  await expect(page.locator(`[data-schedule-row="work:${f.ids.work.resetEmail}"] .schedule-date-range`)).toHaveText('Actual 2026-10-05 → Open');
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
  await expect(workItem).toHaveAccessibleDescription(/In progress.*Actual 2026-10-05 → Open/);
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
  await expect(page.locator('[data-schedule-track="milestone:recovery_review"] [data-schedule-bar="baseline"]')).toHaveAttribute('title', 'Baseline · 2026-10-16');
  await item(page, 'milestone:recovery_review').click();
  const inspector = page.locator('[data-schedule-inspector]');
  const statusSection = inspector.locator('[data-schedule-section="status"]');
  await expect(statusSection).toContainText('Baseline: 2026-10-16');
  await expect(statusSection).toContainText('Forecast: 2026-10-19');
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
    for (const label of zooms) {
      await page.getByRole('radio', {name: label, exact: true}).click();
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
  await expect(page.locator('[data-pm-schedule] .schedule-meta')).toContainText('27 loaded');
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

  const metaSummary = region.locator('.schedule-meta summary');
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
