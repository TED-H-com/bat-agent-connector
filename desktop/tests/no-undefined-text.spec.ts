import {test, expect, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {openPmSection} from './pm-navigation.ts';
import {pmRoute} from './pm-fixture.ts';
import {workbenchFixture, route, workspaceId} from './pm-workbench-fixture.ts';

// `[` is not a word character, so a leading \b would miss "[object Object]".
const forbidden = /(?:^|[^\p{L}\p{N}_])(?:undefined|null|NaN|\[object Object\])(?![\p{L}\p{N}_])/u;

function withResources(base: ReturnType<typeof workbenchFixture>) {
  const at = new Date().toISOString();
  const resources = {workspace_id: workspaceId, as_of: at, interval_s: 60, rows: [{
    source_id: 'Implementation', adapter: 'codex-rollout-v1', role: 'implementer', status: 'current',
    metrics: {total: {value: 1000, observed: 1000, covered: 3}}, observations: 3, calls: 2,
    checked_at: at, successful_at: at, first_at: at, last_at: at,
    configurations: [{actual_model: 'example-codex', requested_model: 'example-codex', effort: 'high', cli_version: '0.120.0'}],
  }], quota_pools: []};
  return async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path.endsWith('/resources')) return {status: 200, data: structuredClone(resources)};
    const result = await base.dispatch(input);
    if (path === '/bootstrap' || path === '/capabilities') {
      const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_resources = {version: 1};
    }
    return result;
  };
}

async function assertNoMissingLabels(page: Page) {
  const text = await page.locator('body').innerText();
  const hit = text.match(forbidden);
  const around = hit && hit.index != null ? text.slice(Math.max(0, hit.index - 80), hit.index + 80) : '';
  expect(around).toBe('');
}

for (const locale of ['en-US', 'zh-TW']) test(`PM views hide missing values (${locale})`, async ({browser, baseURL}) => {
  const zh = locale === 'zh-TW';
  const context = await browser.newContext({baseURL, locale, viewport: {width: 1440, height: 900}, colorScheme: 'light', reducedMotion: 'reduce'});
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const fixture = workbenchFixture();
  await mountConversation(page, false, withResources(fixture));
  await page.goto(route);
  await expect(page.locator('[data-charter-section]')).toHaveCount(6);
  await assertNoMissingLabels(page);

  await page.locator('[data-pm-view="requirements"]').click();
  await expect(page.locator('[data-rtm-requirement]')).toHaveCount(2);
  await expect(page.locator('[data-rtm-unassigned-issue]')).toContainText('example/library · Issue #42');
  await assertNoMissingLabels(page);

  await page.locator('[data-pm-view="board"]').click();
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(3);
  await assertNoMissingLabels(page);

  await page.locator('[data-pm-view="agents"]').click();
  await expect(page.locator('[data-resource-row]')).toHaveCount(1);
  await assertNoMissingLabels(page);

  await openPmSection(page, 'requirements', 'ui_publication');
  const tracking = zh ? '持續追蹤' : 'Tracking';
  const issue = page.locator('[data-pm-issue-mappings]').getByRole('button', {name: `example/library · #41 · ${tracking}`, exact: true});
  await expect(issue).toBeVisible();
  await expect(page.locator('[data-pm-issue-mappings]').getByRole('button', {name: `example/library · #42 · ${tracking}`, exact: true})).toBeVisible();
  await assertNoMissingLabels(page);

  await page.goto(pmRoute);
  await expect(page.locator('[data-pm-brief-section]')).toHaveCount(5);
  const unknown = zh ? '未知' : 'Unknown';
  const unavailable = zh ? '資訊尚未取得' : 'Information unavailable';
  await expect(page.locator('[data-pm-brief-section="delivery"]')).toContainText(unknown);
  await expect(page.locator('[data-pm-brief-section="delivery"]')).toContainText('Login PR merge recorded');
  await expect(page.locator('[data-pm-brief-section="goals"]')).toContainText(unavailable);
  await expect(page.locator('[data-pm-brief-section="decisions"]')).toContainText('Confirm the recovery support budget.');
  await assertNoMissingLabels(page);
  expect(errors).toEqual([]);
  await context.close();
});
