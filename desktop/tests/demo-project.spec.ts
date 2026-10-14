import {UNCERTAIN_OPERATION} from './demo/global.ts';
import {openPmPanel, openThreadTab} from './pm-navigation.ts';
import {mkdirSync} from 'node:fs';
import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture, demoRoute} from './demo/index.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';

const cases = [
  {locale: 'en' as const, width: 1440},
  {locale: 'en' as const, width: 390},
  {locale: 'zh-TW' as const, width: 1440},
  {locale: 'zh-TW' as const, width: 390},
];

type Demo = ReturnType<typeof demoFixture>;

function shotDir() {
  return process.env.BATC_DEMO_SCREENSHOT_DIR || `${process.env.TMPDIR || '/tmp'}/demo-fixture-shots`;
}

async function shot(page: Page, name: string) {
  if (process.env.BATC_DEMO_SCREENSHOTS !== '1') return;
  mkdirSync(shotDir(), {recursive: true});
  const fullPage = await page.locator('[aria-modal="true"]:visible').count() === 0;
  await page.screenshot({path: `${shotDir()}/${name}.png`, fullPage, animations: 'disabled'});
}

// The rail is still `.workspace-nav` (a drawer below 1000px). Scan it with the page.
async function visibleText(page: Page) {
  return page.locator('#main, .workspace-nav').evaluateAll(nodes => nodes.map(node => (node as HTMLElement).innerText).join('\n'));
}

async function expectNoJunk(page: Page) {
  const text = await visibleText(page);
  expect(text, text.slice(0, 800)).not.toMatch(/\b(?:undefined|null|NaN)\b/);
}

const viewTitle = {
  en: {overview: 'Overview', discussions: 'Discussions', requirements: 'Requirements', schedule: 'Schedule', dependencies: 'Dependencies', issues: 'Issues & changes', agents: 'Agents', board: 'Board'},
  'zh-TW': {overview: '專案總覽', discussions: '討論', requirements: '需求', schedule: '時程', dependencies: '技術相依', issues: '問題與變更', agents: 'Agent 資源', board: '工作看板'},
} as const;

type DemoLocale = keyof typeof viewTitle;
type ProjectView = keyof typeof viewTitle.en;

async function expectProjectName(page: Page, width: number, name: string) {
  const crumb = page.locator('.shell-crumb-workspace');
  const mobile = page.locator('.shell-mobile-name');
  const switcher = page.locator('.shell-workspace-switch');
  if (width >= 1000) {
    await expect(crumb).toBeVisible();
    await expect(crumb).toContainText(name);
    await expect(mobile).toBeHidden();
    await expect(switcher).toBeVisible();
  } else {
    await expect(mobile).toBeVisible();
    await expect(mobile).toContainText(name);
    await expect(crumb).toBeHidden();
  }
  await expect(switcher).toContainText(name);
}

async function expectView(page: Page, locale: DemoLocale, width: number, view: ProjectView, name: string) {
  await expect(page.getByRole('heading', {level: 1})).toHaveText(viewTitle[locale][view]);
  await expectProjectName(page, width, name);
  for (const [selector, visible] of [
    ['[data-overview-discuss]', view === 'overview'], ['[data-pm-report-menu]', view === 'overview'],
    ['[data-schedule-tools]', view === 'schedule'], ['[data-dep-capture]', view === 'dependencies'],
    ['[data-dependencies-meta]', view === 'dependencies'], ['[data-pm-header-meta]', view === 'issues'],
    ['[data-pm-overflow]', view === 'issues'],
    ['[data-dep-capture-reason]', view === 'dependencies'],
  ] as const) {
    if (visible) await expect(page.locator(selector)).toBeVisible();
    else await expect(page.locator(selector)).toBeHidden();
  }
  await expect(page.locator(`[data-pm-view="${view}"]`)).toHaveAttribute('aria-current', 'page');
}

// Below 1000px the project rail is a drawer. Open it from the topbar, then choose the view.
async function openView(page: Page, id: ProjectView) {
  await expect.poll(() => page.locator('[data-pm-view]').count()).toBeGreaterThan(0);
  const entry = page.locator(`[data-pm-view="${id}"]`);
  const narrow = (page.viewportSize()?.width ?? 0) < 1000;
  if (!await entry.isVisible()) {
    await page.locator('#workspace-menu').click();
    await expect(page.locator('.workspace-nav')).toHaveAttribute('aria-modal', 'true');
  }
  await entry.click();
  await expect(page.locator(`[data-pm-region="${id}"]`)).toBeVisible();
  if (narrow) await expect(page.locator('.workspace-nav')).toBeHidden();
}

async function showThreads(page: Page) {
  await openView(page, 'discussions');
  await expect(page.locator('.pm-workspace-list a.pm-thread-link').first()).toBeVisible();
}

test('unknown demo routes fail loudly and pages keep their cursors', async () => {
  const {dispatch, ids, dates} = demoFixture({locale: 'en'});
  await expect(dispatch({method: 'GET', path: '/not-a-route'})).rejects.toThrow(/Unknown demo fixture route GET \/not-a-route/);
  await expect(dispatch({method: 'POST', path: '/operations'})).rejects.toThrow(/does not perform POST \/operations/);
  const missing = await dispatch({method: 'GET', path: `/pm/workspaces/${'ws_' + 'f'.repeat(32)}`});
  expect(missing.status).toBe(404);
  const plans = await dispatch({method: 'GET', path: `/pm/workspaces/${ids.workspaceId}/plans?limit=1`});
  expect(plans.data.revisions.map((row: {sequence: number}) => row.sequence)).toEqual([2]);
  expect(plans.data.next_cursor).toBe(2);
  const roadmap = await dispatch({method: 'GET', path: `/pm/workspaces/${ids.workspaceId}/roadmap`});
  expect(roadmap.data.revisions).toBeUndefined();
  expect(roadmap.data.forecast.document.milestones.find((row: {milestone_id: string}) => row.milestone_id === 'recovery_review').forecast_date)
    .toBe(dates.recoveryForecast);
  expect(roadmap.data.baseline.document.milestones.find((row: {milestone_id: string}) => row.milestone_id === 'recovery_review').baseline_date)
    .toBe(dates.recoveryBaseline);
  const deliveries = await dispatch({method: 'GET', path: `/pm/workspaces/${ids.workspaceId}/work/${ids.work.email}/deliveries?after=0&limit=20`});
  expect(deliveries.data.deliveries).toHaveLength(2);
  expect(deliveries.data.next_after).toBeNull();
  expect(deliveries.data.deliveries[0]).not.toHaveProperty('seq');
  expect(deliveries.data.requirement_acceptance).toBe('not_evaluated_by_delivery');
  const register = await dispatch({method: 'GET', path: `/pm/workspaces/${ids.workspaceId}/register?limit=50`});
  expect(register.status).toBe(200);
  expect(register.data.rows).toHaveLength(3);
  expect(register.data.next_cursor).toBe('cmVnMQ');
  expect(register.data.coverage.complete).toBe(true);
  expect(register.data.total_by_type).toEqual({risk: 1, issue: 2, action: 1, change: 1, decision: 1});
  expect(register.data.summary.blocking_reasons).toHaveLength(1);
  const registerNext = await dispatch({method: 'GET', path: `/pm/workspaces/${ids.workspaceId}/register?after=${register.data.next_cursor}&limit=50`});
  expect(registerNext.data.rows).toHaveLength(3);
  expect(registerNext.data.next_cursor).toBeNull();
  const registerPartial = await dispatch({method: 'GET', path: `/pm/workspaces/${ids.workspaceId}/register?owner=unavailable-source&limit=50`});
  expect(registerPartial.data.coverage.complete).toBe(false);
  expect(registerPartial.data.total_by_type).toBeNull();
  expect(registerPartial.data.summary).toBeNull();
  const registerChange = await dispatch({method: 'GET', path: `/pm/workspaces/${ids.workspaceId}/register?type=change&limit=50`});
  expect(registerChange.data.summary.blocking_reasons).toEqual([]);
  expect(registerChange.data.coverage.blocked_without_reason).toBe(0);
  const registerEmpty = await dispatch({method: 'GET', path: `/pm/workspaces/${ids.workspaceId}/register?q=&limit=50`});
  expect(registerEmpty.status).toBe(400);
  const other = await dispatch({method: 'GET', path: `/pm/workspaces/${ids.workspaceId}/work/${ids.work.audit}/deliveries`});
  expect(other.data.deliveries).toEqual([]);
  expect(demoRoute('board')).toBe(demoRoute('charter'));
  expect(demoRoute('planning-thread')).toContain(ids.threadPlanning);
  expect(demoRoute('manual')).toContain('/session/demo-host/manual-reset-notes');
  expect(() => demoRoute('missing')).toThrow(/Unknown demo route missing/);
});

for (const {locale, width} of cases) {
  test(`Overview and Issues open bound discussion drafts in ${locale} at ${width}`, async ({browser, baseURL}) => {
    // Use the authorized discussion fixture for composer focus. The browsing demo below
    // keeps PM management controls gated and manual sessions read-only; Session writes are mocked.
    const fixture = demoFixture({locale, discussions: true});
    const context = await browser.newContext({locale, baseURL, viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage();
    await mountConversation(page, false, fixture.dispatch);
    await page.goto(demoRoute());
    const row = page.locator('[data-overview-row]').filter({hasText: fixture.copy.blocker});
    const discuss = row.getByRole('link', {name: locale === 'zh-TW' ? '討論' : 'Discuss', exact: true});
    const launch = parseDiscussRefs((await discuss.getAttribute('href'))!.split('?')[1]);
    expect(launch.refs).toEqual([{kind: 'governance', id: 'pmgov_' + '1'.padStart(32, '0')}]);
    await discuss.click();
    await expect(page).toHaveURL(/\/discussions\/new\?/);
    await expect(page.locator('[data-discussion-title]')).toHaveText(fixture.copy.blocker);
    await expect(page.locator('[data-discussion-context=governance]').first()).toBeVisible();
    await expect(page.locator('[data-discussion-composer]')).toBeFocused();
    expect(fixture.discussion.writes).toHaveLength(0);
    await shot(page, `discuss-launch-${locale}-${width}`);
    await page.getByRole('link', {name: locale === 'zh-TW' ? '返回專案總覽' : 'Back to Overview', exact: true}).click();
    await expectView(page, locale, width, 'overview', fixture.copy.name);
    await expect(page.locator('[data-overview-status]')).toBeVisible();
    await openView(page, 'issues');
    await expectView(page, locale, width, 'issues', fixture.copy.name);
    if (process.env.BATC_DEMO_SCREENSHOTS === '1') await expect(page.locator('[data-reg-table] [data-row-key]').first()).toBeVisible();
    await shot(page, `issues-${locale}-${width}`);
    await page.locator('[data-pm-overflow]').click();
    await expect(page.locator('[data-menu-key=governance]')).toBeVisible();
    await expect(page.locator('[data-menu-key=governance]')).toBeFocused();
    await expect(page.locator('[data-menu-key=phase]')).toBeHidden();
    await expect(page.locator('[data-menu-key=settings]')).toBeHidden();
    await page.locator('[data-menu-key=governance]').click();
    const governance = page.locator('[data-pm-section=ui_governance] > details');
    await expect(governance).toHaveAttribute('open', '');
    await governance.locator(':scope > summary').click();
    const typeSelect = page.locator('[data-pm-register] [data-choice-select]');
    if (await typeSelect.isVisible()) await typeSelect.selectOption({label:locale === 'zh-TW' ? '決定' : 'Decision'});
    else await page.locator('[data-reg-type=decision]').click();
    await page.locator('[data-row-key]').filter({hasText: fixture.copy.decisions.budget}).click();
    const issueDiscuss = page.locator('[data-reg-discuss]');
    const issueLaunch = parseDiscussRefs((await issueDiscuss.getAttribute('href'))!.split('?')[1]);
    expect(issueLaunch.refs).toEqual([{kind: 'plan_revision', id: fixture.ids.planV3}]);
    expect(issueLaunch.from).toBe('issues');
    await issueDiscuss.click();
    await expect(page).toHaveURL(/\/discussions\/new\?/);
    await expect(page.locator('[data-discussion-title]')).toHaveText(fixture.copy.decisions.budget);
    await expect(page.locator('[data-discussion-context=plan_revision]').first()).toBeVisible();
    await expect(page.locator('[data-discussion-composer]')).toBeFocused();
    await shot(page, `issues-discuss-launch-${locale}-${width}`);
    await page.getByRole('link', {name: locale === 'zh-TW' ? '返回問題與變更' : 'Back to Issues & changes', exact: true}).click();
    await expectView(page, locale, width, 'issues', fixture.copy.name);
    await expect(page.locator('[data-reg-type=decision]')).toHaveAttribute('aria-checked', 'true');
    await expectNoJunk(page);
    expect(fixture.discussion.writes).toHaveLength(0);
    await context.close();
  });

  test(`member sign-in demo renders in ${locale} at ${width}`, async ({browser, baseURL}) => {
    test.setTimeout(60_000);
    const fixture = demoFixture({locale});
    const {copy, ids, dates} = fixture;
    const accepted = locale === 'zh-TW' ? '已記錄受理' : 'Send accepted';
    const context = await browser.newContext({locale, baseURL, viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage();
    const errors: string[] = [];
    const httpErrors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
    page.on('response', response => {
      if (response.status() < 400) return;
      httpErrors.push(`${response.status()} ${new URL(response.url()).pathname}`);
    });
    await mountConversation(page, false, fixture.dispatch);
    await page.goto(demoRoute());
    await expectView(page, locale, width, 'overview', copy.name);
    const charter = page.locator('[data-pm-charter]');
    await expect(page.locator('[data-overview-status]')).toContainText(locale === 'zh-TW' ? '8 項中 3 項已驗收' : '3 of 8 accepted');
    await shot(page, `overview-live-${locale}-${width}`);
    await openPmPanel(page, 'ui_report');
    await page.locator('[data-pm-report-tools] select').selectOption(ids.snap2);
    await page.keyboard.press('Escape');
    await expect(charter).toContainText(copy.goal);
    await expect(charter).toContainText(copy.schedule);
    await expect(charter).toContainText(copy.decisions.budget);
    await expect(charter).toContainText(copy.decisions.lockout);
    await expect(charter).toContainText(copy.smtp);
    await expect(charter).toContainText(copy.blocker);
    await expect(charter).toContainText(copy.finance);
    await expect(page.locator('.pm-overview-saved')).toContainText(copy.snapshotV3);
    await charter.locator('[data-charter-plan]').click();
    for (const requirement of copy.requirements) await expect(page.locator('[data-overview-plan]')).toContainText(requirement.text);
    await page.keyboard.press('Escape');
    await expectNoJunk(page);
    await shot(page, `charter-${locale}-${width}`);

    await openView(page, 'requirements');
    await expectView(page, locale, width, 'requirements', copy.name);
    const rtm = page.locator('[data-pm-rtm]');
    for (const requirement of copy.requirements) await expect(rtm).toContainText(requirement.text);
    await expect(rtm).toContainText(locale === 'zh-TW' ? '未評估' : 'Not assessed');
    if (width < 800) await shot(page, `rtm-list-${locale}-${width}`);
    await rtm.locator(width < 800 ? `[data-rtm-requirement="${ids.requirements.email}"]` : `[data-rtm-toggle="${ids.requirements.email}"]`).click();
    await expect(rtm).toContainText(copy.work.email);
    await expect(rtm).toContainText(locale === 'en' ? 'example/membership · Issue #41' : 'example/membership · 議題 #41');
    await rtm.locator(`[data-rtm-select="${ids.work.email}"]`).click();
    await expect(rtm.locator('[data-rtm-pr]')).toHaveCount(2);
    await expect(rtm).toContainText('example/membership · PR #87');
    await expect(rtm).toContainText('example/membership · PR #91');
    await expectNoJunk(page);
    await shot(page, `rtm-${locale}-${width}`);

    await page.keyboard.press('Escape');
    await openView(page, 'board');
    await expectView(page, locale, width, 'board', copy.name);
    const board = page.locator('[data-pm-kanban]');
    await expect(board.locator('[data-pm-kanban-work]')).toHaveCount(12);
    await expect(board.locator('[data-pm-kanban-column="accepted"]')).toContainText(copy.work.email);
    await expect(board.locator('[data-pm-kanban-column="review"]')).toContainText(copy.work.rate);
    await expect(board.locator('[data-pm-kanban-column="in_progress"]')).toContainText(copy.work.resetEmail);
    await expect(board.locator('[data-pm-kanban-column="waiting"]')).toContainText(copy.work.audit);
    await expect(board.locator('[data-pm-kanban-column="backlog"]')).toContainText(copy.work.a11y);
    if (width < 800) await shot(page, `board-lanes-${locale}-${width}`);
    const email = board.locator(`[data-pm-kanban-work="${ids.work.email}"]`);
    await email.locator('[data-board-open]').click();
    const workInspector = page.locator('[data-work-inspector]');
    await expect(workInspector.locator('[data-work-attempt]')).toHaveCount(3);
    await expect.poll(async () => (await workInspector.innerText()).split(accepted).length - 1).toBe(2);
    await expectNoJunk(page);
    await shot(page, `board-${locale}-${width}`);

    await page.keyboard.press('Escape');
    await openView(page, 'agents');
    await expectView(page, locale, width, 'agents', copy.name);
    const agents = page.locator('[data-pm-resources]');
    await expect(agents.locator('[data-resource-row="codex-implementer"]')).toBeVisible();
    await expect(agents.locator('[data-resource-row="claude-reviewer"]')).toBeVisible();
    const unavailable = agents.locator('[data-resource-row="security-notes"]');
    await expect(unavailable).toContainText(locale === 'zh-TW' ? '無法取得' : 'Unavailable');
    await expect(unavailable).not.toContainText('100%');
    await expect(unavailable).not.toContainText('Codex');
    await expect(agents.locator('[data-resource-chart]')).toBeVisible();
    await expect(agents.locator('[data-resource-quota]')).toContainText('40%');
    await expectNoJunk(page);
    await shot(page, `agents-${locale}-${width}`);

    await openView(page, 'schedule');
    await expectView(page, locale, width, 'schedule', copy.name);
    if (process.env.BATC_DEMO_SCREENSHOTS === '1') await expect(page.locator('.schedule-chart [data-schedule-row]').first()).toBeVisible();
    await shot(page, `schedule-${locale}-${width}`);
    await page.locator('[data-schedule-tools]').click();
    await page.getByRole('menuitem', {name: locale === 'zh-TW' ? '路線圖' : 'Roadmap', exact: true}).click();
    const recovery = page.locator('[data-pm-milestone="recovery_review"]');
    await expect(recovery).toContainText(locale === 'zh-TW' ? '復原審查' : 'Recovery review');
    await expect(recovery.locator('td').nth(0).locator('time')).toHaveAttribute('datetime', dates.recoveryBaseline);
    await expect(recovery.locator('td').nth(1).locator('time')).toHaveAttribute('datetime', dates.recoveryForecast);
    await expect(page.locator('[data-pm-milestone="design_review"]')).toContainText(locale === 'zh-TW' ? '設計審查' : 'Design review');
    await expect(page.locator('[data-pm-milestone="launch"]')).toContainText(locale === 'zh-TW' ? '上線' : 'Launch');
    await expect(page.locator('[data-pm-milestone="launch"] td').first().locator('time')).toHaveAttribute('datetime', dates.launchDate);
    await expectNoJunk(page);
    await shot(page, `roadmap-${locale}-${width}`);
    await page.keyboard.press('Escape');

    await openView(page, 'dependencies');
    await expectView(page, locale, width, 'dependencies', copy.name);
    const dependencies = page.locator('[data-dep-view]');
    await expect(dependencies.locator('[data-dep-relation=test]')).toContainText('2');
    await expect(page.locator('[data-dep-capture]')).toBeDisabled();
    await expect(page.locator('.ui-page-header [data-status=partial]')).toBeVisible();
    const inventory = dependencies.locator(width === 390 ? '[data-dep-list]' : '[data-dep-matrix]');
    await expect(inventory).toContainText('pytest');
    await expect(inventory).toContainText('membership-api');
    await expectNoJunk(page);
    await shot(page, `dependencies-${locale}-${width}`);
    await dependencies.getByRole('radio', {name: locale === 'zh-TW' ? '關係圖' : 'Graph', exact: true}).click();
    await expect(dependencies.locator('[data-dep-graph] svg')).toBeVisible();
    await openView(page, 'overview');
    await expectView(page, locale, width, 'overview', copy.name);
    await expect(page.locator('[data-pm-overview]')).toHaveAttribute('data-overview-mode', 'saved');
    await expect(page.locator('.pm-overview-saved')).toContainText(copy.snapshotV3);
    await openView(page, 'dependencies');
    await expectView(page, locale, width, 'dependencies', copy.name);
    await expect(dependencies.getByRole('radio', {name: locale === 'zh-TW' ? '關係圖' : 'Graph', exact: true})).toHaveAttribute('aria-checked', 'true');

    await showThreads(page);
    await expectView(page, locale, width, 'discussions', copy.name);
    const threads = page.locator('.pm-workspace-list a.pm-thread-link');
    await expect(threads.filter({hasText: copy.threads.idea})).toBeVisible();
    await expect(threads.filter({hasText: copy.threads.delivery})).toBeVisible();
    await threads.filter({hasText: copy.threads.planning}).click();
    await expectView(page, locale, width, 'discussions', copy.name);
    await expect(page.locator('.pm-notes')).toContainText(copy.planningNote);
    await openThreadTab(page,'plan');
    await expect(page.locator('.pm-draft')).toContainText(copy.draftRationale);
    await expect(page.locator('.pm-draft')).toContainText(locale === 'zh-TW' ? '財務' : 'Finance');
    const proposals = page.locator('[data-pm-plan-proposals]');
    await page.locator('[data-proposal-history] > summary').click();
    await page.getByLabel(locale === 'zh-TW' ? '已保存提案' : 'Saved proposal').selectOption(ids.proposalId);
    if (width < 1200) await page.keyboard.press('Escape');
    await expect(proposals).toContainText(copy.proposalSummary);
    await expect(proposals.getByLabel(locale === 'zh-TW' ? '計畫名稱' : 'Plan title')).toHaveValue(copy.proposalTitle);
    await expectNoJunk(page);
    await shot(page, `planning-${locale}-${width}`);

    await page.goto(demoRoute('manual'));
    await expect(page.locator('h1')).toContainText(copy.manualTitle);
    await expectProjectName(page, width, copy.name);
    await expect(page.locator('[data-session-readonly]')).toContainText(locale === 'zh-TW' ? '這是有人在 BAT 開啟的工作階段' : 'Started in BAT by a person');
    await expect(page.locator('#main')).toContainText(copy.manualNote);
    await expect(page.locator('#workspace-message-input')).toBeHidden();
    if (width < 1200) await shot(page, `manual-transcript-${locale}-${width}`);
    if (width < 1200) await page.getByRole('button', {name: locale === 'zh-TW' ? '成果' : 'Results', exact: true}).click();
    await page.locator('[data-session-details] > summary').click();
    await expect(page.locator('[data-session-details]')).toContainText(locale === 'zh-TW' ? 'API 唯讀' : 'API read-only');
    await expect(page.locator('[data-session-details]')).toContainText(locale === 'zh-TW' ? '人工建立（BAT）' : 'created in BAT');
    await expectNoJunk(page);
    await shot(page, `manual-${locale}-${width}`);
    // Web boot probes a managed browser session. This demo is a bearer-token client, so the
    // central answer is 404. Chromium still logs that handled 404 as a console error.
    expect(httpErrors).toEqual(['404 /api/v1/browser-session']);
    expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
    await context.close();
  });
}


test('authorized discussion demo retains session actions and original-key mock recovery', async () => {
  const fixture = demoFixture({discussions: true}), {ids} = fixture;
  const capabilities = (await fixture.dispatch({method: 'GET', path: '/capabilities'})).data;
  for (const action of ['session.send', 'session.answer', 'session.interrupt', 'pm.thread.create', 'pm.conversation.adopt'])
    expect(capabilities.actions).toContainEqual({action, allowed: true});
  expect(capabilities.scopes).toEqual(expect.arrayContaining(['observe', 'operate', 'manage', 'start']));
  const global = await fixture.dispatch({method: 'GET', path: `/operations/${UNCERTAIN_OPERATION}`});
  expect(global.data.operation).toMatchObject({operation_id: UNCERTAIN_OPERATION, action: 'checkpoint.continue', status: 'uncertain'});
  const input = {method: 'POST', path: '/operations', idempotency_key: 'fixture-session-original', body: {
    action: 'session.send', target: {host: ids.host, session_id: ids.sessions.implement}, params: {text: 'Retain this original mock send.'},
  }};
  const first = await fixture.dispatch(input), recovered = await fixture.dispatch(input);
  expect(first.status).toBe(200); expect(recovered.data.operation).toEqual(first.data.operation);
  const messages = (await fixture.dispatch({method: 'GET', path: `/sessions/${ids.host}/${ids.sessions.implement}/messages`})).data.messages;
  expect(messages.filter((row: any) => row.text === input.body.params.text)).toHaveLength(1);
  const manual = await fixture.dispatch({...input, idempotency_key: 'fixture-manual-refusal', body: {...input.body, target: {host: ids.host, session_id: ids.sessions.manual}}});
  expect(manual.status).toBe(403); expect(fixture.discussion.writes).toHaveLength(0);
});
