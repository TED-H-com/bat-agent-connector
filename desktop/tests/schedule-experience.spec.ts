import {mkdirSync} from 'node:fs';
import {test, expect, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {demoProject, DEMO_NOW} from './demo/project.ts';
import {schedulePage} from './demo/schedule.ts';
import {fixture as roadmapFixture, path as roadmapPath} from './pm-roadmap-fixture.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';

async function start(page: Page, locale = 'en', options: {native?: boolean; pageSize?: number; mutate?: (data: any) => void} = {}) {
  const fixture = demoFixture({locale}), project = demoProject(locale);
  const calls: string[] = [], writes: any[] = [], state = {deny: false, workspaceDeny: false, defer: false, pending: null as null | (() => void), cursor: 0, completed: 0};
  await page.clock.setFixedTime(DEMO_NOW);
  await mountConversation(page, options.native || false, async input => {
    if (input.method !== 'GET') writes.push(input);
    const url = new URL(input.path, 'http://fixture');
    if (url.pathname.endsWith('/schedule')) {
      calls.push(input.path);
      const data = schedulePage(project, url, options.pageSize); options.mutate?.(data);
      const denied = state.deny;
      if (state.defer) {state.defer = false; await new Promise<void>(resolve => {state.pending = resolve;});}
      // Snapshot the response before a later read revokes the source.
      state.completed++;
      return denied ? {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Unavailable'}}} : {status: 200, data};
    }
    if (url.pathname === `/pm/workspaces/${fixture.ids.workspaceId}` && state.workspaceDeny) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Unavailable'}}};
    if (url.pathname === '/events' && state.cursor) return {status: 200, data: {events: Number(url.searchParams.get('after')) < state.cursor
      ? [{seq: state.cursor, resource_type: 'work_item', resource_id: fixture.ids.work.email, kind: 'work_item.updated'}] : [],
      next_cursor: state.cursor, head_cursor: state.cursor, has_more: false, sync: {checkpoint: {cursor: state.cursor, token: `demo-${state.cursor}`}}}};
    return fixture.dispatch(input);
  });
  await page.goto(`/dashboard/#/pm/${fixture.ids.workspaceId}/schedule`);
  await expect(page.locator('[data-pm-schedule] .schedule-meta')).toContainText(locale === 'en' ? 'loaded' : '已載入');
  await expect(page.locator('.pm-main > header button[aria-label]').first()).toBeEnabled();
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
  expect(parseDiscussRefs(href!.split('?')[1])).toEqual({refs: [{kind: 'work_item', id: f.ids.work.resetEmail}], title: f.copy.work.resetEmail, from: 'schedule', report: ''});
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
  await page.locator('.pm-main > header button[aria-label]').first().click();
  await expect(page.locator('.pm-main > header button[aria-label]').first()).toBeEnabled();
  await expect(page.locator('[data-schedule-inspector]')).toContainText(f.copy.work.resetEmail);
  expect(await viewport.evaluate(node => node.scrollLeft)).toBeCloseTo(480, 0);
});

test('all short pages load sequentially; any partial source stays labelled', async ({page}) => {
  const f = await start(page, 'en', {pageSize: 4});
  expect(f.calls).toHaveLength(7);
  for (const call of f.calls.slice(1)) expect(new URL(call, 'http://fixture').searchParams.get('after')).toMatch(/^[A-Za-z0-9_-]+$/);
  await expect(page.locator('[data-pm-schedule] .schedule-meta [data-status=partial]')).toBeVisible();
  await expect(page.locator('[data-pm-schedule] .schedule-meta')).toContainText('27 loaded');
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
  f.state.deny = true; await page.locator('.pm-main > header button[aria-label]').first().click();
  await expect(page.locator('[data-schedule-inspector]')).toHaveCount(0);
  await expect(page.locator('[data-pm-schedule] [data-schedule-row]')).toHaveCount(0);
  await expect(page.locator('[data-pm-schedule] .schedule-meta')).toBeEmpty();
  await expect(page.locator('[data-pm-schedule] .schedule-unscheduled')).toBeEmpty();
  await expect(page.locator('[data-pm-schedule] [role=alert]')).toContainText('Schedule unavailable');
  expect(f.writes).toEqual([]);
});

test('late schedule response cannot revive data after workspace withdrawal', async ({page}) => {
  const f = await start(page); await item(page, `work:${f.ids.work.resetEmail}`).click();
  f.state.defer = true; f.state.cursor++;
  await expect.poll(() => Boolean(f.state.pending)).toBe(true);
  f.state.workspaceDeny = true;
  await page.locator('.pm-main > header button[aria-label]').first().click();
  await expect(page.locator('[data-schedule-inspector]')).toHaveCount(0);
  const completed = f.state.completed; f.state.pending!();
  await expect.poll(() => f.state.completed).toBe(completed + 1);
  await expect.poll(() => page.locator('[data-pm-schedule] [data-schedule-row]').count()).toBe(0);
  await expect(page.locator('[data-pm-schedule] .schedule-meta')).toBeEmpty();
  expect(f.writes).toEqual([]);
});


test('a late success cannot restore an endpoint withdrawn by a newer read', async ({page}) => {
  const f = await start(page); await item(page, `work:${f.ids.work.resetEmail}`).click();
  f.state.defer = true; f.state.cursor++;
  await expect.poll(() => Boolean(f.state.pending)).toBe(true);
  f.state.deny = true; await page.locator('.pm-main > header button[aria-label]').first().click();
  await expect(page.locator('[data-pm-schedule] [role=alert]')).toContainText('Schedule unavailable');
  const completed = f.state.completed; f.state.pending!();
  await expect.poll(() => f.state.completed).toBe(completed + 1);
  await expect(page.locator('[data-schedule-inspector]')).toHaveCount(0);
  await expect(page.locator('[data-pm-schedule] [data-schedule-row]')).toHaveCount(0);
  await expect(page.locator('[data-pm-schedule] .schedule-meta')).toBeEmpty();
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
  const meta = page.locator('[data-pm-schedule] .schedule-meta');
  await expect(meta).toContainText('10 loaded'); await expect(meta.locator('[data-status=partial]')).toBeVisible();
  await meta.locator('summary').click(); await expect(meta).toContainText('Refresh page limit reached'); expect(f.writes).toEqual([]);
});
