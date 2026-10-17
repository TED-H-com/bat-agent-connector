import {test, expect} from '@playwright/test';
import {attentionFixture, mountAttention} from './attention-fixture.ts';
for (const native of [false, true]) test(`explicit result lineage, partial children and unknown consumption remain distinct (${native ? 'IPC' : 'Web'})`, async ({page}) => {
  const f = attentionFixture(), [parent, child, accepted] = f.data.items, errors: string[] = [];
  const summary = (item: any) => ({...item, links: [], result_artifacts: [], links_truncated: false, artifacts_truncated: false});
  let failed = false, sourceReads = 0;
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    if (path.endsWith('/result-sources')) {
      sourceReads++;
      if (failed) return {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Result source unavailable'}}};
      const artifact = {artifact_id: 'art_'+'a'.repeat(32), revision: 2, digest: 'd'.repeat(64), display_name: 'Child report', state: 'ready', available: true,
        source_operation_id: 'op_'+'a'.repeat(32), recorded_consumers: [{owner_kind: 'work_item', owner_id: parent.work_item_id, role: 'input'}], live_consumers: 'unknown'};
      return {status: 200, data: {version: 1, source: 'central_journal', read_at: 1791580000, item: summary(parent),
        parent: {...accepted, title: 'Ancestor'}, derived_from: null, children_next_cursor: url.searchParams.has('after') ? null : 'next-child',
        children: url.searchParams.has('after') ? [summary(accepted)] : [{...summary(child), result_artifacts: [artifact],
          links: [{kind: 'session', ref: 'demo/exact-session', target: {found: true, title: 'Child source session', host: 'demo', session_id: 'exact-session', api_access: 'read_only'},
            observation: {status: 'available', streaming: false, observed_at: 1791580000, stale: true},
            delivered_to: [{operation_id: 'op_'+'b'.repeat(32), repository: 'example/repo', pull_number: 7, delivered_sha: 'c'.repeat(40)}]}]}]}};
    }
    const result = await f.dispatch(input);
    if (path === '/capabilities') result.data.features.work_item_results = {version: 1};
    if (path === '/bootstrap') result.data.capabilities.features.work_item_results = {version: 1};
    return result;
  };
  page.on('pageerror', error => errors.push(error.message)); await mountAttention(page, native, f, dispatch);
  await page.goto('/dashboard/#/item/'+parent.work_item_id); const panel = page.locator('[data-result-sources]');
  await expect(panel).toContainText('1 loaded child work items have not been accepted'); await expect(panel).toContainText('More child work is not loaded');
  await panel.getByText('Own and child result sources', {exact: true}).click();
  await panel.locator('[data-result-child] > summary').click();
  await expect(panel.getByRole('link', {name: 'Child report', exact: true})).toHaveAttribute('href', '#/artifact-review/artifact/art_'+'a'.repeat(32)+'/2');
  await expect(panel).toContainText('live consumption remains unknown'); await expect(panel).toContainText('Last observation: unknown');
  await expect(panel.getByRole('link', {name: 'Child source session', exact: true})).toHaveAttribute('href', '#/session/demo/exact-session');
  await expect(panel).toContainText('Delivery recorded'); await expect(panel).toContainText('parent completion are separate');
  await panel.getByRole('button', {name: 'Load more child work', exact: true}).click();
  await expect(panel.locator('[data-result-child]')).toHaveCount(2); await expect(panel.getByRole('button', {name: 'Load more child work', exact: true})).toBeHidden();
  failed = true; await panel.getByRole('button', {name: 'Refresh result sources', exact: true}).click();
  await expect(panel).toContainText('retaining the last observation'); await expect(panel.getByRole('link', {name: 'Child report', exact: true})).toBeVisible();
  const beforeEvent = sourceReads; f.data.cursor = 1;
  await expect.poll(() => sourceReads).toBeGreaterThan(beforeEvent);
  expect(f.data.reads.some(path => path.startsWith('/events?after=1'))).toBe(false);
  failed = false;
  await expect.poll(() => f.data.reads.some(path => path.startsWith('/events?after=1')), {timeout: 15000}).toBe(true);
  // Original text remains verbatim behind its compact summary.
  const original = page.locator('#main details').filter({has: page.locator('summary', {hasText: 'Request'})}).last();
  await original.locator('summary').click(); await expect(original.locator('.pre')).toHaveText(parent.request);
  expect(f.data.writes).toEqual([]); expect(errors).toEqual([]);
});

function resultDocument(url: URL, parent: any, child: any, accepted: any) {
  const summary = (item: any) => ({...item, links: [], result_artifacts: [], links_truncated: false, artifacts_truncated: false});
  const after = url.searchParams.has('after');
  return {version: 1, source: 'central_journal', read_at: 1791580000, item: summary(parent),
    parent: {...accepted, title: 'Ancestor'}, derived_from: null, children_next_cursor: after ? null : 'next-child',
    children: after ? [summary(accepted)] : [summary(child)]};
}

async function openResultSources(page: import('@playwright/test').Page, options: {operationEvents?: boolean} = {}) {
  const f = attentionFixture(), [parent, child, accepted] = f.data.items;
  let sourceGets = 0, holding = false, release = () => {};
  let gate = Promise.resolve();
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    if (path.endsWith('/result-sources')) {
      sourceGets++;
      if (holding) await gate;
      return {status: 200, data: resultDocument(url, parent, child, accepted)};
    }
    if (path === '/events' && options.operationEvents) {
      const result = await f.dispatch(input);
      for (const event of result.data.events || []) event.resource_type = 'operation';
      return result;
    }
    const result = await f.dispatch(input);
    if (path === '/capabilities') result.data.features.work_item_results = {version: 1};
    if (path === '/bootstrap') result.data.capabilities.features.work_item_results = {version: 1};
    return result;
  };
  await mountAttention(page, false, f, dispatch);
  await page.goto('/dashboard/#/item/' + parent.work_item_id);
  const panel = page.locator('[data-result-sources]');
  await expect(panel).toContainText('1 loaded child work items have not been accepted');
  await panel.getByText('Own and child result sources', {exact: true}).click();
  const refresh = panel.getByRole('button', {name: 'Refresh result sources', exact: true});
  const more = panel.getByRole('button', {name: 'Load more child work', exact: true});
  const status = panel.locator('[role="status"]');
  return {panel, refresh, more, status, parent, gets: () => sourceGets, hold() {holding = true; gate = new Promise(resolve => {release = resolve;});}, release() {holding = false; release();}, bumpOperation() {f.data.cursor = 1;}};
}

async function refreshBusy(refresh: import('@playwright/test').Locator, status: import('@playwright/test').Locator) {
  return refresh.evaluate((button, statusSelector) => {
    const panel = button.closest('[data-result-sources]');
    const region = panel?.querySelector(statusSelector);
    const active = document.activeElement;
    return {focused: active === button, active: active?.getAttribute?.('aria-label') || active?.tagName || '',
      ariaDisabled: button.getAttribute('aria-disabled'), disabled: button.disabled, ariaBusy: region?.getAttribute('aria-busy') ?? null};
  }, '[role="status"]');
}

test('result sources refresh keeps focus and ignores a second press', async ({page}) => {
  const ui = await openResultSources(page);
  const before = ui.gets();
  ui.hold();
  await ui.refresh.focus();
  await page.keyboard.press('Enter');
  await expect.poll(() => ui.gets()).toBe(before + 1);
  expect(await refreshBusy(ui.refresh, ui.status)).toEqual({focused: true, active: 'Refresh result sources', ariaDisabled: 'true', disabled: false, ariaBusy: 'true'});
  await page.keyboard.press('Enter');
  await ui.refresh.evaluate(button => button.click());
  ui.release();
  await expect(ui.status).not.toHaveAttribute('aria-busy', 'true');
  expect(ui.gets()).toBe(before + 1);
  await expect(ui.refresh).toBeFocused();
});

test('an operation event keeps focus on result sources refresh', async ({page}) => {
  const ui = await openResultSources(page, {operationEvents: true});
  const before = ui.gets();
  await ui.refresh.focus();
  ui.hold();
  ui.bumpOperation();
  await expect.poll(() => ui.gets()).toBe(before + 1);
  expect(await refreshBusy(ui.refresh, ui.status)).toEqual({focused: true, active: 'Refresh result sources', ariaDisabled: 'true', disabled: false, ariaBusy: 'true'});
  ui.release();
  await expect(ui.status).not.toHaveAttribute('aria-busy', 'true');
  await expect(ui.refresh).toBeFocused();
});

test('result sources more on the last page focuses the first new child', async ({page}) => {
  const ui = await openResultSources(page);
  await expect(ui.more).toBeVisible();
  await ui.more.focus();
  await page.keyboard.press('Enter');
  const summary = ui.panel.locator('[data-result-child] summary', {hasText: 'Accepted work'});
  await expect(summary).toBeFocused();
});
