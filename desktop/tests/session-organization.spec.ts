import {expectErrorDetails} from './api-error-assertions.ts';
import {test, expect, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';

const caps = {actor: 'organizer', scopes: ['observe'], api_version: 1, contract_version: '2026-10-08', hosts: [], actions: [], features: {}};
const cp = (cursor: number) => ({cursor, token: `session-page-${cursor}`});
function fixture() {
  const data = {cursor: 0, principal: 'one', fail: false, pages: [] as string[], writes: [] as any[],
    rows: Array.from({length: 32}, (_, i) => ({host: i < 24 ? 'build-east' : 'build-west', session_id: `session-${String(i).padStart(3, '0')}`,
      title: ['Review parser changes', 'Prepare release notes', 'Investigate flaky test', 'Check migration history'][i % 4] + ` · ${i + 1}`,
      workspace: i < 16 ? 'Connector' : i < 24 ? 'Dashboard' : 'Connector', workspace_id: i < 16 ? 'workspace-connector' : i < 24 ? 'workspace-dashboard' : 'workspace-connector',
      agent_kind: i % 3 ? 'claude' : 'codex', worktree_branch: i % 4 ? 'fix/parser' : 'main', loaded: i !== 5, streaming: i === 5 || i === 6 ? null : i % 4 === 0,
      provenance: i % 3 === 0 ? 'manual' : 'connector_managed', api_access: i % 3 === 0 ? 'read_only' : 'managed',
      pending: i === 1 ? {kind: 'ask_user'} : null, fields_stale: i === 1 || i === 4, stale: i === 2, stale_reason: i === 2 ? 'host_unreachable' : null,
      state: {lifecycle: 'unknown', enumeration: i === 3 ? 'gone' : 'present'}, observed_at: '2026-10-08T15:30:00Z', last_activity_at: '2026-10-08T15:25:00Z'}))};
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    if (input.method !== 'GET') {data.writes.push(input); throw new Error('Read-only organization must not write');}
    if (path === '/sessions') {
      data.pages.push(input.path);
      if (data.fail) return {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Inventory unavailable'}}};
      const filtered = data.rows.filter(row => (!url.searchParams.get('host') || row.host === url.searchParams.get('host')) &&
        (!url.searchParams.get('access') || row.api_access === url.searchParams.get('access')));
      const offset = url.searchParams.get('cursor') ? 24 : 0;
      return {status: 200, data: {sessions: filtered.slice(offset, offset + 24), next_cursor: filtered.length > offset + 24 ? 'second' : null}};
    }
    return {status: 200, data: path === '/capabilities' ? caps : path === '/bootstrap' ? {capabilities: caps,
      sync: {version: 1, server_id: 'central-one', principal_id: data.principal, checkpoint: cp(0)}} : path === '/events' ? {
      events: Number(url.searchParams.get('after')) < data.cursor ? [{seq: data.cursor, resource_type: 'session', resource_id: 'build-east/session-000', kind: 'session.updated'}] : [],
      next_cursor: data.cursor, head_cursor: data.cursor, has_more: false, sync: {checkpoint: cp(data.cursor)}}
      : {hosts: [{host: 'build-east'}, {host: 'build-west'}], sessions: [], operations: [], work_items: []}};
  };
  return {data, dispatch};
}
async function mount(page: Page, native: boolean, dispatch: (input: any) => Promise<any>) {
  if (native) {
    await page.exposeFunction('fixtureConnector', dispatch);
    await page.addInitScript(caps => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === 'native_status') return {endpoint: 'https://central.example/', credential_available: true};
      if (command === 'connector_connect') return caps;
      if (command === 'connector_disconnect') return null;
      if (command === 'connector_request') return (window as any).fixtureConnector(args.input);
      throw new Error(`Unexpected native command ${command}`);
    }}}), caps);
  } else await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
  await page.route('**/api/v1/**', async route => {
    if (native) throw new Error('Native must use fixed IPC');
    const request = route.request(), url = new URL(request.url());
    const result = await dispatch({method: request.method(), path: url.pathname.slice(7) + url.search});
    await route.fulfill({status: result.status, json: result.data});
  });
}
const saved = (page: Page) => page.evaluate(() => JSON.parse(Object.entries(localStorage).find(([key]) => key.startsWith('batc.sync.'))?.[1] || 'null'));
for (const native of [false, true]) {
  const name = native ? 'native' : 'browser';
  test(`loaded workspace scope, search and pagination preserve truthful ownership (${name})`, async ({page}) => {
    const {data, dispatch} = fixture(); const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
    await mount(page, native, dispatch); await page.goto('/dashboard/#/sessions');
    await expect(page.getByText('Showing 24 · 24 loaded', {exact: true})).toBeVisible();
    await expect(page.locator('[data-resource-id]')).toHaveCount(24);
    await expect(page.locator('.session-group-heading code')).toHaveCount(0);
    await expect(page.locator('[data-resource-id="build-east/session-000"]')).toContainText('Manual · API read-only');
    await expect(page.locator('[data-resource-id="build-east/session-000"] .session-entry-heading .chip')).toHaveText('Running');
    await expect(page.locator('[data-resource-id="build-east/session-001"] .session-entry-heading .chip')).toHaveText('Waiting for you');
    await expect(page.locator('[data-resource-id="build-east/session-003"]')).toContainText('Not in latest scan');
    await expect(page.locator('[data-resource-id="build-east/session-003"] .session-entry-heading .chip')).toHaveText('Out of sync');
    await expect(page.locator('.session-inventory')).not.toContainText('Ended');
    await expect(page.locator('[data-resource-id="build-east/session-004"] .chip')).toHaveText('Out of sync');
    await expect(page.locator('[data-resource-id="build-east/session-005"] .session-entry-heading .chip')).toHaveCount(0);
    await expect(page.locator('[data-resource-id="build-east/session-006"] .session-entry-heading .chip')).toHaveCount(0);
    await page.locator('[data-resource-id="build-east/session-005"] .session-row-details > summary').click();
    await expect(page.locator('[data-resource-id="build-east/session-005"] .session-row-details')).toContainText('Not open in BAT');
    await page.locator('[data-resource-id="build-east/session-006"] .session-row-details > summary').click();
    await expect(page.locator('[data-resource-id="build-east/session-006"] .session-row-details')).toContainText('BAT did not report activity');
    await expect(page.locator('[data-resource-id="build-east/session-001"]')).toContainText('Activity and pending requests have not been rechecked.');
    const west = page.locator('.session-workspaces').getByRole('link', {name: 'build-west', exact: true});
    await expect(west).toHaveCount(0);
    await page.getByRole('searchbox', {name: 'Search loaded sessions'}).fill('build-west');
    await expect(page.getByText('Showing 0 · 24 loaded', {exact: true})).toBeVisible();
    await expect(page.getByText('More pages are available. Load more, or adjust your search and filters.')).toBeVisible();
    await page.locator('#main').getByRole('button', {name: 'Load more', exact: true}).click();
    await expect(page.getByText('Showing 8 of 32 sessions', {exact: true})).toBeVisible();
    await expect(west).toHaveAttribute('href', '#/host/build-west');
    await page.locator('#main').getByRole('searchbox').fill('');
    await page.locator('.session-workspaces button').filter({hasText: 'Dashboard'}).click();
    await expect(page.getByText('Showing 8 of 32 sessions', {exact: true})).toBeVisible();
    await expect(page.locator('[data-resource-id]')).toHaveCount(8);
    await page.reload();
    await expect(page.getByText('Showing 8 · 24 loaded', {exact: true})).toBeVisible();
    await expect(page.locator('.session-workspaces button[aria-pressed="true"]')).toContainText('Dashboard');
    await expect(page.getByRole('link', {name: 'Projects and work items'})).toHaveAttribute('href', '#/projects');
    expect(data.pages.every(path => path.includes('order=id') && path.includes('include_gone=true'))).toBe(true);
    expect(data.writes).toEqual([]); expect(errors).toEqual([]);
  });
  test(`event refresh retains pages, row anchor and expanded evidence; failed read cannot acknowledge (${name})`, async ({page}) => {
    const {data, dispatch} = fixture(); await mount(page, native, dispatch); await page.goto('/dashboard/#/sessions');
    await page.locator('#main').getByRole('button', {name: 'Load more', exact: true}).click();
    const row = page.locator('[data-resource-id="build-east/session-009"]');
    await row.locator('summary').first().click();
    await expect(row.getByText('session-009', {exact: true})).toBeVisible();
    await row.evaluate(el => window.scrollTo(0, el.getBoundingClientRect().top + scrollY - 115));
    const top = await row.evaluate(el => el.getBoundingClientRect().top);
    data.rows[0].title += ' with a much longer title '.repeat(12); data.cursor = 1;
    await expect.poll(() => saved(page)).toEqual(cp(1));
    await expect(page.getByText('Showing 32 of 32 sessions', {exact: true})).toHaveCount(1);
    await expect(row.locator('details').first()).toHaveAttribute('open', '');
    expect(Math.abs(await row.evaluate(el => el.getBoundingClientRect().top) - top)).toBeLessThan(3);
    data.fail = true; data.cursor = 2;
    await expectErrorDetails(page.locator('#main'), 'Inventory unavailable', 'UNAVAILABLE');
    expect(await saved(page)).toEqual(cp(1));
    data.fail = false;
    await expect.poll(() => saved(page), {timeout: 10000}).toEqual(cp(2));
    expect(data.writes).toEqual([]);
  });
  test(`filter change during an event read waits for the replacement view (${name})`, async ({page}) => {
    const {data, dispatch} = fixture();
    let hold = false, held = false, release!: () => void;
    const gate = new Promise<void>(resolve => {release = resolve;});
    await mount(page, native, async input => {
      // Hold the inventory view read, independently of the persistent sidebar read.
      if (hold && input.path.startsWith('/sessions?limit=') && !input.path.includes('host=')) {
        const result = await dispatch(input); hold = false; held = true; await gate; return result;
      }
      return dispatch(input);
    });
    await page.goto('/dashboard/#/sessions');
    await expect(page.locator('[data-resource-id]')).toHaveCount(24);
    hold = true; data.cursor = 1;
    await expect.poll(() => held).toBe(true);
    await page.getByRole('combobox', {name: 'Host', exact: true}).selectOption('build-west');
    data.fail = true; release();
    await expectErrorDetails(page.locator('#main'), 'Inventory unavailable', 'UNAVAILABLE');
    expect(await saved(page)).not.toEqual(cp(1));
    data.fail = false;
    await expect.poll(() => saved(page), {timeout: 10000}).toEqual(cp(1));
    await expect(page.locator('[data-resource-id]')).toHaveCount(8);
    expect(await page.locator('[data-resource-id]').evaluateAll(rows => rows.every(row => row.getAttribute('data-resource-id')?.startsWith('build-west/')))).toBe(true);
    expect(data.writes).toEqual([]);
  });
  test(`scope preferences cannot leak into another central principal (${name})`, async ({page}) => {
    const {data, dispatch} = fixture(); await mount(page, native, dispatch); await page.goto('/dashboard/#/sessions');
    await page.locator('#main').getByRole('searchbox').fill('parser');
    await page.locator('.session-workspaces button').filter({hasText: 'Dashboard'}).click();
    data.principal = 'two'; await page.reload();
    await expect(page.locator('#main').getByRole('searchbox')).toHaveValue('');
    await expect(page.locator('.session-workspaces button[aria-pressed="true"]')).toContainText('All loaded sessions');
    data.principal = 'one'; await page.reload();
    await expect(page.locator('#main').getByRole('searchbox')).toHaveValue('parser');
    await expect(page.locator('.session-workspaces button[aria-pressed="true"]')).toContainText('Dashboard');
    expect(data.writes).toEqual([]);
  });
}
for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) {
  test(`session organization fits ${locale} ${width}`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}}), page = await context.newPage();
    const {data, dispatch} = fixture(); await mount(page, false, dispatch); await page.goto('/dashboard/#/sessions');
    await expect(page.locator('[data-resource-id]')).toHaveCount(24);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await mkdir('/tmp/bac-sessions-after', {recursive: true});
    await page.screenshot({path: `/tmp/bac-sessions-after/sessions-${locale}-${width}.png`});
    if (width < 901) await page.locator('.session-scope > summary').click();
    await page.locator('.session-workspaces button').filter({hasText: 'Dashboard'}).click();
    await expect(page.locator('[data-resource-id]')).toHaveCount(8);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(data.writes).toEqual([]); await context.close();
  });
}

for (const width of [390, 768, 1440]) test(`long workspace identities and expanded evidence fit ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  const {data, dispatch} = fixture();
  const row = {...data.rows[0], title: '<img src=x onerror=alert(1)> ' + 'LongTitleWithoutSpaces'.repeat(9),
    workspace: 'Workspace/' + '工作區名稱'.repeat(25), workspace_id: 'workspace-' + 'abcdef'.repeat(35),
    session_id: 'session-' + '123456789'.repeat(20), worktree_branch: 'branch/' + 'deep-name'.repeat(25), fields_stale: true};
  data.rows = [row];
  await mount(page, false, dispatch); await page.goto('/dashboard/#/sessions');
  await expect(page.locator('[data-resource-id]')).toHaveCount(1);
  if (width < 901) await page.locator('.session-scope > summary').click();
  await page.locator('.session-row-details > summary').click();
  await page.locator('.observation-evidence > summary').click();
  await expect(page.getByText(row.session_id, {exact: true})).toBeVisible();
  await expect(page.locator('.session-inventory img')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await mkdir('/tmp/bac-sessions-after', {recursive: true});
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({path: `/tmp/bac-sessions-after/expanded-long-${width}.png`, fullPage: true});
});

test('same-host workspace labels disambiguate by ID while unique names stay clean', async ({page}) => {
  const {data, dispatch} = fixture();
  data.rows = [{...data.rows[0], workspace: 'Shared', workspace_id: 'workspace-one'},
    {...data.rows[1], workspace: 'Shared', workspace_id: 'workspace-two'},
    {...data.rows[2], workspace: '', workspace_id: 'workspace-unnamed'}];
  await mount(page, false, dispatch); await page.goto('/dashboard/#/sessions');
  await expect(page.locator('.session-group-heading code')).toHaveText(['workspace-one', 'workspace-two']);
  await expect(page.getByRole('heading', {name: 'workspace-unnamed', exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Shared · workspace-two 1 session', exact: true}).click();
  await expect(page.locator('[data-resource-id]')).toHaveCount(1);
  await expect(page.locator('[data-resource-id]')).toHaveAttribute('data-resource-id', 'build-east/session-001');
});
