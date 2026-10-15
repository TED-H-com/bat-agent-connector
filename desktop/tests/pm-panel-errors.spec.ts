import {test, expect, type Page} from '@playwright/test';
import {readFile, mkdir} from 'node:fs/promises';
import {handoffFixture} from './pm-package-handoff-fixture.ts';
import {specFixture} from './pm-spec-materialization-fixture.ts';
import {meetingFixture, mount as mountMeetings} from './pm-meetings-fixture.ts';
import {unavailableBriefTransport} from './pm-source-transport-fixture.ts';
import {workbenchFixture} from './pm-workbench-fixture.ts';
import {reviewFixture as maintenanceFixture} from './pm-maintenance-review-fixture.ts';
import {pmFixture, pmPath, pmRoute} from './pm-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';

const panels = [
  ['maintenance-review', 'workspaceMaintenanceReview', 'pmrv_unavailable'],
  ['maintenance-policy', 'workspaceMaintenancePolicy', 'pmp_unavailable'],
  ['maintenance-repairs', 'workspaceMaintenanceRepairs', 'pmr_unavailable'],
  ['release-baselines', 'workspaceReleaseBaselines', 'prb_unavailable'],
  ['maintenance-checks', 'maintenanceChecks', 'pmc_unavailable'],
  ['maintenance-observations', 'maintenanceObservations', 'pmo_unavailable'],
  ['work-packages', 'workspaceWorkPackages', 'wp_unavailable'],
  ['work-deliveries', 'workspaceWorkDeliveries', 'wd_unavailable'],
  ['work-batches', 'workspaceWorkBatches', 'pmb_unavailable'],
  ['spec-materialization', 'workspaceSpecMaterialization', 'psm_unavailable'],
  ['package-handoff', 'workspacePackageHandoff', 'ph_unavailable'],
  ['issue-mappings', 'workspaceIssueMappings', 'pmi_unavailable'],
  ['quality-reviews', 'workspaceQualityReviews', 'pmq_unavailable'],
  ['plan-diff', 'planComparison', 'pm_diff_failure'],
  ['ahp-observer', 'workspaceAhpObserver', 'ahp_unavailable'],
  ['dependencies', 'workspaceDependencies', 'error_generic'],
  ['overview', 'workspaceOverview', 'error_generic'],
  ['overview-saved', 'workspaceOverview', 'error_generic'],
  ['meetings', 'workspaceMeetings', 'error_generic'],
  ['lifecycle', 'workspaceLifecycle', 'pm_lifecycle_unknown'],
  ['context-bundle', 'pmContextBundle', 'pm_cb_unavailable'],
  ['export-brief', 'projectBrief', 'error_generic'],
  ['export-charter', 'workspaceCharter', 'error_generic'],
  ['export-maintenance', 'workspaceMaintenanceReview', 'pmrv_unavailable'],
];
const code = 'PM_PANEL_READ_FAILED', serverMessage = 'Original English source diagnostics <b>stay text</b>';
const failures = panels.map(([name, factory, key]) => ({name, factory, key, cause: 'unavailable', code, message: serverMessage, detached: false}));
failures.push(
  {name: 'package-handoff', factory: 'workspacePackageHandoff', key: 'ph_invalid', cause: 'spec-mismatch', code, message: 'PM_SPEC_RESPONSE_MISMATCH', detached: false},
  {name: 'package-handoff', factory: 'workspacePackageHandoff', key: 'ph_invalid', cause: 'quality-mismatch', code: 'PM_REQUIREMENT_QUALITY_INVALID', message: serverMessage, detached: false},
  {name: 'spec-materialization', factory: 'workspaceSpecMaterialization', key: 'psm_invalid', cause: 'spec-mismatch', code, message: 'PM_SPEC_RESPONSE_MISMATCH', detached: false},
  {name: 'work-batches', factory: 'workspaceWorkBatches', key: 'pmb_invalid', cause: 'quality-mismatch', code: 'PM_REQUIREMENT_QUALITY_INVALID', message: serverMessage, detached: false},
  ...failures.filter(row => row.name.startsWith('overview')).map(row => ({...row, cause: 'detached-status', detached: true})),
);

// Use the production errorBox, including refresh-cycle recording, without
// booting the application shell. Existing panel suites exercise durable recovery.
async function mountFailure(page: Page, name: string, factory: string, status: number, failure: {code: string, message: string, detached: boolean}) {
  const f = specFixture(), bootstrap = await f.dispatch({method: 'GET', path: '/bootstrap'});
  const caps = bootstrap.data.capabilities, row = handoffFixture().row;
  const workspace = (await f.dispatch({method: 'GET', path: `/pm/workspaces/${row.workspace_id}`})).data.workspace;
  Object.assign(caps.features.pm_workspaces, {quality_reviews: {version: 1}, maintenance_checks: {version: 1}, maintenance_review: {version: 1},
    lifecycle: {version: 1, gated_actions: []}, accepted_context_bundle: {schema: 'pm_context_bundle_v1'},
    work_batches: {version: 1, actions: ['pm.work_batch.start'], profiles: ['github_reported_checks_v1']}});
  caps.features.pm_ahp_observer = {version: 1};
  caps.features.pm_workspaces.brief_export = {formats: ['markdown', 'html', 'svg'], locales: ['en', 'zh-TW']};
  const maintenance = maintenanceFixture(), snapshot = workbenchFixture().snapshot(1);
  let reads = 0, writes = 0;
  await page.exposeFunction('c4Request', async (method: string, path: string) => {
    if (method === 'GET') reads++; else writes++;
    if (name.startsWith('export-') && !path.includes('/brief-export')) {
      if (name === 'export-maintenance') return maintenance.dispatch({method, path});
      if (path.endsWith('/plans/head')) return {status: 200, data: {head: {workspace_id: row.workspace_id, version: 1}}};
      if (new URL(path, 'http://fixture').pathname.endsWith('/snapshots')) return {status: 200, data: {workspace_id: row.workspace_id, snapshots: [snapshot], next_cursor: null}};
      return {status: 200, data: {workspace_id: row.workspace_id, snapshot}};
    }
    return {status, data: {error: {code: failure.code, message: failure.message}}};
  });
  await page.route('**/__c4-error.js', async route => {
    const app = await readFile(new URL('../src/app.js', import.meta.url), 'utf8');
    const start = app.indexOf('function errorBox('), end = app.indexOf('async function submit(', start);
    expect(start).toBeGreaterThan(0); expect(end).toBeGreaterThan(start);
    await route.fulfill({contentType: 'text/javascript', body: `
      import {h} from '/src/ui/dom.js';
      import {t} from '/src/i18n.js';
      import {disclosure} from '/src/ui/components.js';
      import {errorPresentation} from '/src/error-presentation.js';
      export const state = {refreshCycle: {}};
      export ${app.slice(start, end)}`});
  });
  await page.route('**/__c4-form.js', async route => {
    const source = await readFile(new URL('../src/pm-workspace.js', import.meta.url), 'utf8');
    await route.fulfill({contentType: 'text/javascript', body: source.replace('function durableForm(', 'export function durableForm(').replaceAll("from './", "from '/src/")});
  });
  await unavailableBriefTransport(page, 'src');
  await page.goto('/ui-components.html');
  await page.evaluate(async ({name, factory, caps, workspace, row, detached}) => {
    const {h} = await import('/src/ui/dom.js'), {t} = await import('/src/i18n.js');
    const {errorBox, state} = await import('/__c4-error.js'), {durableForm} = await import('/__c4-form.js');
    const source = {'overview-saved': 'overview', 'export-brief': 'brief', 'export-charter': 'charter', 'export-maintenance': 'maintenance-review'}[name] || name;
    const module = await import(`/src/pm-${source}.js`), win = window as any;
    const {planDiffText} = await import('/src/pm-plan-diff-strings.js');
    const api = async (method: string, path: string) => {
      const result = await win.c4Request(method, path);
      if (result.status >= 400) throw Object.assign(Error(result.data.error.message), {code: result.data.error.code, status: result.status});
      return result.data;
    };
    const input = (key: string, {multiline = false, max = 4000, required = true, value = ''}: any = {}) =>
      h(multiline ? 'textarea' : 'input', {'aria-label': t(key), maxlength: max, required, value});
    const labelled = (key: string, field: any) => h('label', {}, t(key), field);
    const alive = () => {}, getCaps = () => caps;
    const form = (options: any) => durableForm({h, t, api, caps: getCaps, guard: alive, namespace: 'c4', errorBox, ...options});
    const main = document.getElementById('fixture')!;
    if (name === 'overview-saved') {
      const {viewState} = await import('/src/ui/view-state.js');
      viewState('c4', row.workspace_id, 'overview').mode = 'saved';
    }
    const ui = module[factory]({h, t, api, alive, namespace: 'c4', workspaceId: row.workspace_id, workspace: () => workspace,
      caps: getCaps, permitted: () => true, canRead: () => true, form, input, labelled, errorBox, main, onDiscuss: () => {}, onChange: () => {},
      guard: alive, target: {workspace_id: row.workspace_id, thread_id: 'pmt_' + '1'.repeat(32)}, thread: () => ({thread: {version: 1}})});
    const root = ui.element || ui.box; main.append(root); root.hidden = false;
    if (root instanceof HTMLDetailsElement) root.open = true;
    win.c4Ui = ui; win.c4State = state; win.c4Text = (key: string) => planDiffText(t, key);
    if (name === 'context-bundle') root.querySelector('button').click();
    else if (name === 'plan-diff') {
      const first = 'pmrev_' + '1'.repeat(32), second = 'pmrev_' + '2'.repeat(32);
      ui.update({workspace_id: row.workspace_id, revisions: [{revision_id: first, sequence: 1}, {revision_id: second, sequence: 2}], next_cursor: null});
      ui.select(first, second); root.querySelector('.actions button').click();
    } else if (name === 'package-handoff') {
      ui.setPackage(row); root.querySelector('[data-pm-package-handoff-controls]').open = true;
      [...root.querySelectorAll('button')].find((button: any) => button.textContent === t('ph_review')).click();
    } else if (name === 'spec-materialization') {root.querySelector(':scope > details').open = true; await ui.setPackage(row);}
    else if (name === 'work-deliveries') ui.setWork('wi_' + '7'.repeat(20));
    else if (name === 'dependencies') await ui.setActive(true);
    else if (name.startsWith('overview')) {
      main.append(ui.reportTools); win.c4ReportStatus = ui.reportTools.querySelector('[data-report-status]');
      if (detached) win.c4ReportStatus.remove();
      await ui.prepareReports();
    }
    else await ui.refresh();
    if (name.startsWith('export-')) root.querySelector('[data-pm-brief-export] button').click();
  }, {name, factory, caps, workspace, row, detached: failure.detached});
  return {reads: () => reads, writes: () => writes};
}

for (const locale of ['en', 'zh-TW']) test.describe(locale, () => {
  test.use({locale: locale === 'en' ? 'en-US' : 'zh-TW', colorScheme: 'light', reducedMotion: 'reduce'});
  for (const width of [1440, 390]) test(`version conflict has one complete summary and keeps its original draft ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: width === 390 ? 844 : 900});
    const f = pmFixture(); f.state.reject = {status: 409, code: 'VERSION_CONFLICT'};
    await mountConversation(page, false, f.dispatch); await page.goto(pmRoute);
    const form = page.locator('[data-pm-action="pm.message.append"]'), field = form.locator('textarea');
    await field.fill('Keep this original draft');
    await form.getByRole('button', {name: locale === 'en' ? 'Send' : '傳送', exact: true}).click();
    const sentence = locale === 'en'
      ? 'Could not confirm the current version. Your draft is preserved. Refresh and review before another change.'
      : '目前無法確認最新版本。草稿仍保留；請重新整理並檢視後，再進行變更。';
    await expect(form.locator('[data-api-error]')).toHaveCount(1);
    await expect(form.locator('[data-error-summary]')).toHaveText(sentence);
    await expect(form.getByText(sentence, {exact: true})).toHaveCount(1);
    await expect(form.locator('[data-error-details]')).toBeHidden();
    expect(await form.innerText()).not.toContain('Rejected request');
    expect(await form.innerText()).not.toContain('VERSION_CONFLICT');
    const folder = process.env.BATC_FIX1_SHOTS;
    if (folder) {
      await mkdir(folder, {recursive: true}); await form.locator('[data-api-error]').scrollIntoViewIfNeeded();
      await page.screenshot({animations: 'disabled', path: `${folder}/version-conflict-${locale}-${width}-closed.png`});
    }
    await form.locator('[data-api-error] summary').click();
    await expect(form.locator('[data-error-details] code')).toHaveText('VERSION_CONFLICT');
    await expect(form.locator('[data-error-details]')).toContainText('Rejected request');
    if (folder) await page.screenshot({animations: 'disabled', path: `${folder}/version-conflict-${locale}-${width}-open.png`});
    await expect(field).toHaveValue('Keep this original draft');
    expect(f.state.writes).toHaveLength(1); expect(f.state.ops.size).toBe(0);
    const [stored] = await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.includes('.pm.message.append.')).map(([, value]) => JSON.parse(value)));
    expect(stored.values.content).toBe('Keep this original draft');
    expect(stored.intent.key).toBe(f.state.writes[0].idempotency_key);
    expect(stored.intent.request).toEqual(f.state.writes[0].body);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
  for (const width of [1440, 390]) for (const mode of ['refresh', 'earlier']) test(`workspace ${mode} one localized error ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: width === 390 ? 844 : 900});
    const f = pmFixture(); let denied = false, failedReads = 0;
    const calls: {method: string, path: string, body: any}[] = [];
    await mountConversation(page, false, async input => {
      calls.push(input);
      const url = new URL(input.path, 'http://fixture');
      if (url.pathname === pmPath) {
        if (denied) {failedReads++; return {status: 503, data: {error: {code, message: serverMessage}}};}
        return {status: 200, data: {...f.detail(), next_message_cursor: 50}};
      }
      return f.dispatch(input);
    });
    await page.goto(pmRoute);
    const composer = page.locator('[data-pm-action="pm.message.append"] textarea'); await composer.fill('Preserved local draft');
    denied = true;
    if (mode === 'earlier') await page.getByRole('button', {name: locale === 'en' ? 'Load earlier notes' : '載入較早筆記', exact: true}).click();
    else await page.locator('.pm-main > header').getByRole('button', {name: locale === 'en' ? 'Refresh' : '重新整理', exact: true}).click();
    const root = page.locator('#main'), summary = root.locator('[data-error-summary]');
    await expect(summary).toHaveCount(1);
    await expect(summary).toHaveText(locale === 'en' ? 'Could not confirm the current version. Your draft is preserved. Refresh and review before another change.' : '目前無法確認最新版本。草稿仍保留；請重新整理並檢視後，再進行變更。');
    await expect(root.locator('[data-api-error]')).toHaveCount(1);
    await expect(root.locator('[data-error-details]')).toBeHidden(); await expect(root).not.toContainText(serverMessage, {useInnerText: true});
    const shots = process.env.BATC_C4_SHOTS;
    if (shots) {await mkdir(shots, {recursive: true}); await page.screenshot({path: `${shots}/workspace-${mode}-${locale}-${width}-closed.png`});}
    await root.locator('[data-api-error] summary').click();
    await expect(root.locator('[data-error-details]')).toContainText(serverMessage);
    await expect(root.locator('[data-error-details] code')).toHaveText(code);
    await expect(root.locator('[data-error-details] b')).toHaveCount(0);
    if (shots) await page.screenshot({path: `${shots}/workspace-${mode}-${locale}-${width}-open.png`});
    await expect(composer).toHaveValue('Preserved local draft');
    const before = failedReads; f.state.cursor++;
    await expect.poll(() => failedReads).toBeGreaterThan(before);
    expect(await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.sync.'))
      .every(([, value]) => JSON.parse(value).cursor === 0))).toBe(true);
    expect(calls.filter(input => input.method !== 'GET')).toEqual([]);
    expect(f.state.writes).toEqual([]);
  });
  for (const width of [1440, 390]) for (const failure of failures) {
    const {name, factory, key, cause, code, message} = failure;
    test(`${name} ${cause} one failure message ${width}`, async ({page}) => {
      await page.setViewportSize({width, height: width === 390 ? 844 : 900});
      const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
      const f = await mountFailure(page, name, factory, name === 'ahp-observer' ? 503 : 403, failure);
      const root = page.locator('#fixture'), summary = root.locator('[data-error-summary]');
      await expect(summary).toHaveCount(1); await expect(summary).toBeVisible();
      const sentence = await page.evaluate(key => (window as any).c4Text(key), key);
      expect(sentence).not.toBe(key);
      await expect(summary).toHaveText(sentence);
      await expect(root.getByText(sentence, {exact: true})).toHaveCount(1);
      await expect(root.locator('.error:visible, .ui-inline-error:visible')).toHaveCount(1);
      await expect(root.locator('[data-api-error]')).toHaveCount(1);
      if (name.startsWith('export-')) await expect(root.locator('[data-pm-brief-export]')).toBeHidden();
      if (name.startsWith('overview')) {
        await expect(root.locator('[data-pm-overview] [data-error-summary]')).toHaveCount(name === 'overview-saved' ? 1 : 0);
        await expect(root.locator('[data-pm-report-tools] [data-error-summary]')).toHaveCount(name === 'overview-saved' ? 0 : 1);
        expect(await page.evaluate(() => document.querySelector('[data-report-status]') === (window as any).c4ReportStatus)).toBe(true);
      }
      await expect(root.locator('form button[type=submit]:visible:enabled')).toHaveCount(0);
      await expect(root.locator('[data-error-details]')).toBeHidden();
      expect(await root.innerText()).not.toContain(message);
      expect(await root.innerText()).not.toContain(code);
      expect(await page.evaluate(() => (window as any).c4State.refreshCycle.error.code)).toBe(code);
      const shots = process.env.BATC_C4_SHOTS;
      if (shots) {await mkdir(shots, {recursive: true}); await page.screenshot({path: `${shots}/c4-r2-${name}-${cause}-${locale}-${width}-closed.png`});}
      await root.locator('[data-api-error] summary').click();
      await expect(root.locator('[data-error-details] code')).toHaveText(code);
      await expect(root.locator('[data-error-details]')).toContainText(message);
      await expect(root.locator('[data-error-details] b')).toHaveCount(0);
      if (shots) await page.screenshot({path: `${shots}/c4-r2-${name}-${cause}-${locale}-${width}-open.png`});
      if (name.startsWith('overview')) {
        await page.evaluate(() => (window as any).c4Ui.withdraw());
        await expect(summary).toHaveCount(0);
        await page.evaluate(() => (window as any).c4Ui.prepareReports());
        await expect(summary).toHaveCount(1); await expect(summary).toHaveText(sentence);
        expect(await page.evaluate(() => document.querySelector('[data-report-status]') === (window as any).c4ReportStatus)).toBe(true);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(f.reads()).toBeGreaterThan(0); expect(f.writes()).toBe(0); expect(errors).toEqual([]);
    });
  }
  for (const role of ['owner', 'reader']) for (const width of [1440, 768, 390]) test(`meetings ${role} empty uses one card and first-row refresh ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: width === 390 ? 844 : 900});
    const f = meetingFixture(); f.state.meetings = []; f.state.sprints = []; f.state.role = role;
    if (role === 'reader') {f.state.actor = 'reader'; f.state.manage = false;}
    await mountMeetings(page, f, locale);
    const panel = page.locator('[data-pm-meetings]'), header = panel.locator('.pm-heading');
    await expect(panel.getByRole('heading', {name: locale === 'en' ? 'Sprint discussions' : '衝刺討論', exact: true})).toBeVisible();
    const empty = role === 'owner' ? locale === 'en' ? 'Plan a Sprint before creating a meeting.' : '請先規劃衝刺，再建立會議。'
      : locale === 'en' ? 'No meeting has been recorded.' : '尚未建立會議紀錄。';
    await expect(panel.getByText(empty, {exact: true})).toHaveCount(1);
    await expect(panel.locator('.pm-note-text:visible')).toHaveText(empty);
    await expect(panel.locator('p:visible')).toHaveCount(role === 'reader' ? 2 : 1);
    await expect(panel.locator('[data-pm-panel-readonly]')).toHaveCount(1);
    if (role === 'reader') await expect(panel.locator('[data-pm-panel-readonly]')).toBeVisible();
    else await expect(panel.locator('[data-pm-panel-readonly]')).toBeHidden();
    await expect(panel.locator('.ui-empty, .ui-section-card')).toHaveCount(0);
    await expect(panel.locator('[data-pm-meeting-create]')).toBeHidden();
    const delta = await header.evaluate(node => {
      const title = node.querySelector('h3')!.getBoundingClientRect(), refresh = node.querySelector('.ui-icon-button')!.getBoundingClientRect();
      return Math.abs(title.top + title.height / 2 - refresh.top - refresh.height / 2);
    });
    expect(delta).toBeLessThanOrEqual(4);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const shots = process.env.BATC_C4_SHOTS;
    if (shots) {await mkdir(shots, {recursive: true}); await page.screenshot({path: `${shots}/c4-r2-meetings-empty-${role}-${locale}-${width}.png`});}
    expect(f.state.writes).toEqual([]);
  });
});
