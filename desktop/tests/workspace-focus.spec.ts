import {test, expect, type Browser, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture, pmRoute, workspaceId} from './pm-fixture.ts';
import {workbenchFixture, route as workbenchRoute} from './pm-workbench-fixture.ts';
import {policyFixture} from './pm-maintenance-policy-fixture.ts';
import {fixture as sprintFixture, actions, pid} from './pm-sprint-fixture.ts';
import {demoFixture, demoRoute} from './demo/index.ts';
import {expectShellEmpty, shellShot} from './shell-empty-state.ts';
import {openPmSection, openReportTask} from './pm-navigation.ts';

function scheduleOnly() {
  const base = pmFixture(), sprint = sprintFixture(), prefix = `/pm/workspaces/${workspaceId}`;
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path === '/bootstrap' || path === '/capabilities') {
      const result = await base.dispatch(input), caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_workspaces.sprints = true;
      caps.actions.push(...sprint.config().caps.actions);
      return result;
    }
    if (path.startsWith(prefix + '/sprint') || path === prefix + '/plans/accepted' || path === prefix + '/work' || path === '/projects/' + pid
      || path.startsWith('/operations/') || input.method === 'POST' && actions.includes(input.body?.action)) return sprint.dispatch(input);
    return base.dispatch(input);
  };
  return dispatch;
}

function agentsOnly() {
  const base = pmFixture();
  const contract = {contract: 'ahp_observer_v1', protocol_version: '1.0.0', observation: 'supported'};
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path === '/bootstrap' || path === '/capabilities') {
      const result = await base.dispatch(input), caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_workspaces.ahp_observation = contract;
      return result;
    }
    if (path.includes('/ahp-')) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'No observations'}}};
    return base.dispatch(input);
  };
  return dispatch;
}

async function openView(browser: Browser, locale: string, baseURL: string, dispatch: (input: any) => any, view: string, width = 1280) {
  const context = await browser.newContext({locale, baseURL, viewport: {width, height: 900}});
  const page = await context.newPage();
  await mountConversation(page, false, dispatch);
  await page.goto(pmRoute);
  await openPmSection(page, view);
  return {context, page, region: page.locator(`[data-pm-region=${view}]`)};
}

async function follows(region: ReturnType<Page['locator']>, heading: string, explanation: string, section: string) {
  const empty = region.locator(':scope > .ui-empty');
  const fold = region.locator(`[data-pm-section=${section}] > details`);
  await expectShellEmpty(empty, heading, explanation);
  await expect(fold).toHaveJSProperty('open', false);
  expect(await region.evaluate((node, [title, key]) => {
    const first = node.querySelector('.ui-empty');
    const next = node.querySelector(`[data-pm-section="${key}"]`);
    return Boolean(first && next && (first.compareDocumentPosition(next) & Node.DOCUMENT_POSITION_FOLLOWING) && (node.firstElementChild === first));
  }, [heading, section])).toBe(true);
}

for (const moveFocus of [false, true]) test(`report focus waits for its read and respects user navigation ${moveFocus}`, async ({page}) => {
  const base = demoFixture();
  let release!: () => void, started = false;
  const waiting = new Promise<void>(resolve => {release = resolve;});
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    const result = await base.dispatch(input);
    if (path === '/bootstrap' || path === '/capabilities') {
      const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_workspaces.brief_export = {formats: ['markdown', 'html', 'svg'], locales: ['en', 'zh-TW'], renderer_version: 1};
    }
    if (path.endsWith('/snapshots/latest')) {started = true; await waiting;}
    return result;
  };
  await mountConversation(page, false, dispatch);await page.goto(demoRoute());
  const trigger = page.locator('[data-pm-report-menu]');
  await trigger.click();await page.locator('[data-menu-key=export]').click();
  await expect.poll(() => started).toBe(true);
  if (moveFocus) await trigger.focus();
  release();
  const format = page.getByRole('combobox', {name: 'File format', exact: true});
  await expect(format).toBeVisible();
  if (moveFocus) {await expect(trigger).toBeFocused();await expect(format).not.toBeFocused();}
  else await expect(format).toBeFocused();
});

for (const task of ['versions', 'save']) test(`live Overview opens only the ${task} task after a delayed read`, async ({page}) => {
  const base = demoFixture(), writes: any[] = [];
  let release!: () => void, started = false;
  const waiting = new Promise<void>(resolve => {release = resolve;});
  const dispatch = async (input: any) => {
    if (input.method === 'POST') writes.push(input);
    const path = new URL(input.path, 'http://fixture').pathname;
    const result = await base.dispatch(input);
    if (path === '/bootstrap' || path === '/capabilities') {
      const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_workspaces.brief_export = {formats: ['markdown', 'html', 'svg'], locales: ['en', 'zh-TW'], renderer_version: 1};
      caps.scopes.push('manage'); caps.actions.push({action: 'pm.snapshot.create', allowed: true});
    }
    if (path === `/pm/workspaces/${base.ids.workspaceId}`) result.data.workspace.membership.role = 'owner';
    if (path.endsWith('/snapshots/latest')) {started = true; await waiting;}
    return result;
  };
  await mountConversation(page, false, dispatch); await page.goto(demoRoute());
  await page.locator('[data-pm-report-menu]').click(); await page.locator(`[data-menu-key=${task}]`).click();
  await expect.poll(() => started).toBe(true); release();
  const save = page.locator('[data-pm-action="pm.snapshot.create"]');
  const versions = page.getByRole('combobox', {name: 'Report version', exact: true});
  await expect(task === 'save' ? save.locator('button[type=submit]') : versions).toBeFocused();
  await expect(task === 'save' ? versions : save).toBeHidden();
  await expect(page.locator('[data-pm-brief-export]')).toBeHidden();
  await expect(page.getByRole('heading', {name: task === 'save' ? 'Save report' : 'Versions', exact: true})).toBeVisible();
  expect(writes).toHaveLength(0);
});

for (const locale of ['en', 'zh-TW']) for (const navigation of ['stay', 'move', 'close']) test(`save focus waits for initial admission and respects ${navigation} ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({locale, baseURL}), page = await context.newPage();
  const base = demoFixture(), writes: any[] = [];
  let release!: () => void, overviewStarted = false, reportRead = false;
  const waiting = new Promise<void>(resolve => {release = resolve;});
  const dispatch = async (input: any) => {
    if (input.method === 'POST') writes.push(input);
    const path = new URL(input.path, 'http://fixture').pathname, result = await base.dispatch(input);
    if (path === '/bootstrap' || path === '/capabilities') {
      const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.scopes.push('manage'); caps.actions.push({action: 'pm.snapshot.create', allowed: true});
    }
    if (path === `/pm/workspaces/${base.ids.workspaceId}`) result.data.workspace.membership.role = 'owner';
    // Keep the initial permission gate closed after the independent report read.
    if (path.endsWith('/overview')) {overviewStarted = true; await waiting;}
    if (path.endsWith('/snapshots/latest')) reportRead = true;
    return result;
  };
  await mountConversation(page, false, dispatch); await page.goto(demoRoute());
  await expect.poll(() => overviewStarted).toBe(true);
  await openReportTask(page, 'save');
  await expect.poll(() => reportRead).toBe(true);
  const submit = page.locator('[data-pm-action="pm.snapshot.create"] button[type=submit]');
  const trigger = page.locator('[data-pm-report-menu]');
  await expect(submit).toBeVisible(); await expect(submit).toBeDisabled();
  if (navigation === 'move') await trigger.focus();
  if (navigation === 'close') await page.keyboard.press('Escape');
  release(); await expect(submit).toBeEnabled();
  if (navigation === 'stay') {
    await expect(submit).toBeFocused();
    await expect(page.getByRole('combobox', {name: locale === 'en' ? 'Report version' : '報告版本', exact: true})).toBeHidden();
  } else {
    await expect(submit).not.toBeFocused();
    if (navigation === 'move') await expect(trigger).toBeFocused();
    else {await expect(page.locator('.ui-inspector')).toHaveCount(0); await expect(submit).toBeHidden();}
  }
  expect(writes).toHaveLength(0); await context.close();
});

for (const [locale, copy] of [['en', {schedule: 'Schedule unavailable', scheduleReason: 'Schedule data is not provided here.',
  agents: 'Usage unavailable', agentsReason: 'Usage data is not provided here.', dependencies: 'Dependencies unavailable',
  dependenciesReason: 'Dependency data is not provided here.', sprints: 'Sprints', observations: 'Observations', policy: 'Policy'}],
  ['zh-TW', {schedule: '無法取得時程', scheduleReason: '目前連線未提供時程資料。', agents: '無法取得用量', agentsReason: '目前連線未提供用量資料。',
    dependencies: '無法取得相依', dependenciesReason: '目前連線未提供相依資料。', sprints: '衝刺', observations: '觀察', policy: '政策'}]] as const) for (const width of [1440, 390]) {
  test(`schedule keeps its empty state above collapsed sprints ${locale} ${width}`, async ({browser, baseURL}) => {
    const session = await openView(browser, locale, baseURL!, scheduleOnly(), 'schedule', width);
    await follows(session.region, copy.schedule, copy.scheduleReason, 'ui_sprints');
    await expect(session.region.locator('[data-pm-sprints]')).toBeHidden();
    await expect(session.region.locator('[data-pm-section=ui_sprints] > details > summary')).toHaveText(copy.sprints);
    await shellShot(session.page, 'schedule-unavailable', locale, width);
    await session.context.close();
  });
  test(`agents keeps its empty state above collapsed observations ${locale} ${width}`, async ({browser, baseURL}) => {
    const session = await openView(browser, locale, baseURL!, agentsOnly(), 'agents', width);
    await follows(session.region, copy.agents, copy.agentsReason, 'ui_observations');
    await expect(session.region.locator('[data-pm-ahp]')).toBeHidden();
    await shellShot(session.page, 'agents-unavailable', locale, width);
    await session.context.close();
  });
  test(`dependencies keeps its empty state above collapsed policy and baselines ${locale} ${width}`, async ({browser, baseURL}) => {
    const session = await openView(browser, locale, baseURL!, policyFixture().dispatch, 'dependencies', width);
    await follows(session.region, copy.dependencies, copy.dependenciesReason, 'ui_policy');
    await expect(session.region.locator('[data-pm-section=ui_baselines] > details')).toHaveJSProperty('open', false);
    await expect(session.region.locator('[data-pm-maintenance-policy]')).toBeHidden();
    await expect(session.region.locator('[data-pm-release-baselines]')).toBeHidden();
    await shellShot(session.page, 'dependencies-unavailable', locale, width);
    await session.context.close();
  });
}

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) test(`empty reports explain missing versions and still permit a first save ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({locale, baseURL, viewport: {width, height: 900}}), page = await context.newPage();
  const base = workbenchFixture(); base.state.snapshots = [];
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname, result = await base.dispatch(input);
    if (path === '/bootstrap' || path === '/capabilities') {
      const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_workspaces.brief_export = {formats: ['markdown', 'html', 'svg'], locales: ['en', 'zh-TW'], renderer_version: 1};
    }
    return result;
  };
  await mountConversation(page, false, dispatch); await page.goto(workbenchRoute);
  for (const task of ['versions', 'export', 'save']) {
    await page.locator('[data-pm-report-menu]').click(); await page.locator(`[data-menu-key=${task}]`).click();
    const empty = page.locator('[data-pm-report-state]');
    if (task === 'save') {
      await expect(empty).toBeHidden();
      await expect(page.locator('[data-pm-action="pm.snapshot.create"] button[type=submit]')).toBeEnabled();
    } else await expectShellEmpty(empty, locale === 'en' ? 'Report unavailable' : '無法取得報告',
      locale === 'en' ? 'No readable saved report is selected.' : '目前未選取可讀取的已存報告。');
    await shellShot(page, 'report-'+task+'-empty', locale, width); await page.keyboard.press('Escape');
  }
  expect(base.state.writes).toHaveLength(0); await context.close();
});

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) test(`report tasks open their own focused control ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({locale, baseURL, viewport: {width, height: 900}}), page = await context.newPage();
  const copy = locale === 'en' ? {export: 'Export report', format: 'File format', versions: 'Versions', version: 'Report version', save: 'Save report'}
    : {export: '匯出報告', format: '檔案格式', versions: '版本', version: '報告版本', save: '儲存報告'};
  const base = workbenchFixture();
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    const result = await base.dispatch(input);
    if (path === '/bootstrap' || path === '/capabilities') {
      const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_workspaces.brief_export = {formats: ['markdown', 'html', 'svg'], locales: ['en', 'zh-TW'], renderer_version: 1};
    }
    return result;
  };
  await mountConversation(page, false, dispatch);
  await page.goto(workbenchRoute);
  await page.locator('[data-pm-report-menu]').click();
  await page.locator('[data-menu-key=export]').click();
  await expect(page.getByRole('heading', {name: copy.export, exact: true})).toBeVisible();
  const exported = page.locator('[data-pm-brief-export]');
  await expect(exported).toBeVisible();
  await expect(exported.locator('details, summary')).toHaveCount(0);
  await expect(exported.locator(':scope > p')).toContainText(locale === 'en' ? 'selected report' : '選定報告');
  await expect(exported.getByRole('button', {name: locale === 'en' ? 'Download report' : '下載報告', exact: true})).toBeVisible();
  await expect(page.getByRole('combobox', {name: copy.format, exact: true})).toBeFocused();
  await expect(page.locator('[data-pm-action="pm.snapshot.create"]')).toBeHidden();
  await page.keyboard.press('Escape');
  await page.locator('[data-pm-report-menu]').click();
  await page.locator('[data-menu-key=versions]').click();
  await expect(page.getByRole('heading', {name: copy.versions, exact: true})).toBeVisible();
  await expect(page.getByRole('combobox', {name: copy.version, exact: true})).toBeFocused();
  await expect(exported).toBeHidden();
  await expect(page.locator('[data-pm-action="pm.snapshot.create"]')).toBeHidden();
  await page.keyboard.press('Escape');
  await page.locator('[data-pm-report-menu]').click();
  await page.locator('[data-menu-key=save]').click();
  await expect(page.getByRole('heading', {name: copy.save, exact: true})).toBeVisible();
  await expect(page.locator('[data-pm-action="pm.snapshot.create"] button[type=submit]')).toBeFocused();
  await expect(page.getByRole('combobox', {name: copy.version, exact: true})).toBeHidden();
  await expect(exported).toBeHidden();
  expect(base.state.writes).toHaveLength(0);
  await context.close();
});

test('a pristine Development phase form offers Choose phase in zh-TW', async ({browser, baseURL}) => {
  const base = pmFixture();
  const endpoint = `/pm/workspaces/${workspaceId}/lifecycle`;
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path === '/bootstrap' || path === '/capabilities') {
      const result = await base.dispatch(input), caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_workspaces.lifecycle = {version: 1, gated_actions: [], record_actions: []};
      caps.actions.push({action: 'pm.lifecycle.set_stage', allowed: true});
      return result;
    }
    if (path === endpoint) return {status: 200, data: {workspace_id: workspaceId,
      lifecycle: {stage: 'development', status: 'active', revision: 2, execution_epoch: 1, transition_id: null, coverage: 'recorded'},
      transition: null, transition_visibility_filtered: false, blocked_actions: [], record_actions: [],
      available_stages: ['idea', 'planning', 'development'], maintenance: {available: false, reason: 'release_baseline_and_policy_required'},
      inflight_summary: {visible_unresolved: 0, visible_retained: 0, visibility_filtered: false}}};
    return base.dispatch(input);
  };
  const context = await browser.newContext({locale: 'zh-TW', baseURL});
  const page = await context.newPage();
  await mountConversation(page, false, dispatch);
  await page.goto(pmRoute);
  await page.locator('[data-pm-overflow]').click();
  await page.locator('[data-menu-key=phase]').click();
  await page.locator('[data-pm-lifecycle-controls] > summary').click();
  await page.locator('[data-pm-lifecycle-intent=set_stage] > summary').click();
  const stage = page.locator('[data-pm-action="pm.lifecycle.set_stage"]').getByLabel('管理階段');
  await expect(stage).toHaveValue('');
  await expect(stage.locator('option:checked')).toHaveText('選擇階段');
  await expect(page.getByRole('button', {name: '記錄階段', exact: true})).toBeDisabled();
  expect(base.state.writes).toHaveLength(0);
  await context.close();
});
