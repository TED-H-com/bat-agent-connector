import {test, expect, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {openPmSection, openThreadBrief} from './pm-navigation.ts';
import {handoffFixture} from './pm-package-handoff-fixture.ts';
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
  const findingId = `pmfn_${'a'.repeat(32)}`;
  const rowId = `finding.${findingId}`;
  const registerRow = {
    row_id: rowId, type: 'issue', type_basis: 'neutral', title: null, status: null, owner: null,
    due: null, impact: null, priority: null, next_step: null, created_at: null, updated_at: null,
    source: {kind: 'finding', id: findingId}, related: {requirement_ids: [], work_item_ids: [], prs: []},
    available_actions: [], unknowns: {title: 'not_recorded', owner: 'not_recorded'},
  };
  const coverage = {complete: false, partial_reason: 'incomplete', open_without_known_age: null, blocked_without_reason: null};
  let mappings: any[] = [];
  return async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path.endsWith('/resources')) return {status: 200, data: structuredClone(resources)};
    if (path.endsWith('/register')) return {status: 200, data: {
      workspace_id: workspaceId, as_of: at, rows: [structuredClone(registerRow)], next_cursor: null,
      total_by_type: null, summary: null, coverage: structuredClone(coverage),
    }};
    const registerDetail = path.match(/\/register\/([^/]+)$/);
    if (registerDetail) {
      if (decodeURIComponent(registerDetail[1]) !== rowId) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Not found'}}};
      return {status: 200, data: {
        workspace_id: workspaceId, as_of: at, row: structuredClone(registerRow),
        timeline: [{step: 'finding', at, title: null, ref: structuredClone(registerRow.source), status: null}],
        timeline_coverage: {complete: true, partial_reason: null, sources: []},
        coverage: structuredClone(coverage),
      }};
    }
    const mappingDetail = path.match(/\/issue-mappings\/(pmim_[0-9a-f]{32})$/);
    if (mappingDetail) {
      const row = mappings.find(item => item.mapping_id === mappingDetail[1]);
      if (!row) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Not found'}}};
      const current = structuredClone(row);
      current.document.package_ref = {package_id: 'pkg_publication'};
      current.operation_id = `op_${'e'.repeat(32)}`;
      delete current.actor;
      return {status: 200, data: {version: 1, workspace_id: workspaceId, mapping_id: current.mapping_id,
        execution_authorized: false, current, revisions: [structuredClone(current)]}};
    }
    const result = await base.dispatch(input);
    if (path.endsWith('/issue-mappings')) mappings = result.data?.mappings || [];
    if (path === '/bootstrap' || path === '/capabilities') {
      const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_resources = {version: 1};
      caps.features.pm_register = {version: 1};
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
  await expect(page.locator('[data-rtm-unassigned-issue]')).toContainText(zh ? 'example/library · 議題 #42' : 'example/library · Issue #42');
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
  await issue.click();
  await page.locator('[data-pm-issue-mappings] summary').filter({hasText: zh ? '發布歷史' : 'Publication history'}).click();
  await expect(page.locator('[data-pm-issue-mappings]')).toContainText(zh ? '版本 1' : 'Revision 1');
  await assertNoMissingLabels(page);

  await openPmSection(page, 'issues');
  await expect(page.locator('[data-pm-register] tbody tr')).toHaveCount(1);
  await page.locator('[data-pm-register] tbody tr').click();
  await expect(page.locator('[data-reg-inspector]')).toBeVisible();
  await expect(page.locator('[data-reg-step="finding"]')).toBeVisible();
  await page.locator('[data-reg-inspector] summary').filter({hasText: zh ? '詳細資料' : 'Details'}).click();
  await assertNoMissingLabels(page);

  await page.goto(pmRoute);
  await openThreadBrief(page);
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

for (const locale of ['en-US', 'zh-TW']) test(`Requirements search and Board reject missing label fragments (${locale})`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale}); const page = await context.newPage();
  const fixture = workbenchFixture(); fixture.board.state.cards[0].column = {unexpected: 'state'};
  fixture.state.issues[0].document.selected.public_title = undefined as any;
  await mountConversation(page, false, fixture.dispatch); await page.goto(route);
  await openPmSection(page, 'requirements');
  const search = page.locator('[data-pm-rtm] input[type=search]'); await search.fill('undefined');
  await expect(page.locator('[data-rtm-requirement]')).toHaveCount(0);
  await search.fill('null'); await expect(page.locator('[data-rtm-requirement]')).toHaveCount(0);
  await search.fill(''); await assertNoMissingLabels(page);
  await openPmSection(page, 'board'); await expect(page.locator('[data-pm-kanban-column=unknown] [data-pm-kanban-work]')).toHaveCount(1);
  await assertNoMissingLabels(page); await context.close();
});

for (const locale of ['en-US', 'zh-TW']) test(`Selected work package omits missing preparation (${locale})`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale}); const page = await context.newPage();
  const fixture = handoffFixture(); fixture.row.sequence = undefined as any;
  await mountConversation(page, false, fixture.dispatch); await page.goto(route);
  await openPmSection(page, 'requirements', 'ui_packages');
  const picker = page.locator('[data-pm-work-packages]').getByLabel(/^(Package|工作包)$/);
  await picker.selectOption({label: (await picker.locator('option').allTextContents()).find(text => /Restore member access/.test(text)) || ''});
  await openPmSection(page, 'requirements', 'ui_handoff');
  await page.locator('[data-pm-package-handoff-controls] > summary').click();
  await expect(page.locator('[data-pm-package-handoff]')).toContainText('Restore member access');
  const zh = locale === 'zh-TW';
  await page.getByRole('button', {name: zh ? '核對基底' : 'Review base', exact: true}).click();
  await expect(page.getByRole('heading', {name: zh ? '已核對實作基底' : 'Reviewed implementation base'})).toBeVisible();
  const reviewed = (await page.locator('[data-pm-package-handoff]').innerText()).toLowerCase();
  expect(reviewed).not.toContain('null');
  expect(reviewed).not.toContain('undefined');
  await assertNoMissingLabels(page); expect(fixture.state.writes).toHaveLength(0); await context.close();
});
