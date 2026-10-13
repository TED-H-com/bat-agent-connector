import {mkdirSync} from 'node:fs';
import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture, demoRoute} from './demo/index.ts';

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
  await page.screenshot({path: `${shotDir()}/${name}.png`, fullPage: true});
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
  en: {overview: 'Overview', discussions: 'Discussions', requirements: 'Requirements', schedule: 'Schedule', agents: 'Agents', board: 'Board'},
  'zh-TW': {overview: '專案總覽', discussions: '討論', requirements: '需求', schedule: '時程', agents: 'Agent 資源', board: '工作看板'},
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
  const other = await dispatch({method: 'GET', path: `/pm/workspaces/${ids.workspaceId}/work/${ids.work.audit}/deliveries`});
  expect(other.data.deliveries).toEqual([]);
  expect(demoRoute('board')).toBe(demoRoute('charter'));
  expect(demoRoute('planning-thread')).toContain(ids.threadPlanning);
  expect(demoRoute('manual')).toContain('/session/demo-host/manual-reset-notes');
  expect(() => demoRoute('missing')).toThrow(/Unknown demo route missing/);
});

for (const {locale, width} of cases) {
  test(`member sign-in demo renders in ${locale} at ${width}`, async ({browser, baseURL}) => {
    test.setTimeout(60_000);
    const fixture = demoFixture({locale});
    const {copy, ids, dates} = fixture;
    const accepted = locale === 'zh-TW' ? '已記錄受理' : 'Acceptance recorded';
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
    await expect(charter).toContainText(copy.goal);
    await expect(charter).toContainText(copy.schedule);
    await expect(charter).toContainText(copy.decisions.budget);
    await expect(charter).toContainText(copy.decisions.lockout);
    await expect(charter).toContainText(copy.smtp);
    await expect(charter).toContainText(copy.blocker);
    await expect(charter).toContainText(copy.finance);
    await expect(charter).toContainText(copy.snapshotV3);
    await charter.locator('[data-charter-plan] > summary').click();
    for (const requirement of copy.requirements) await expect(charter).toContainText(requirement.text);
    await expectNoJunk(page);
    await shot(page, `charter-${locale}-${width}`);

    await openView(page, 'requirements');
    await expectView(page, locale, width, 'requirements', copy.name);
    const rtm = page.locator('[data-pm-rtm]');
    for (const requirement of copy.requirements) await expect(rtm).toContainText(requirement.text);
    await expect(rtm).toContainText(locale === 'zh-TW' ? '尚未評估' : 'Not assessed');
    await rtm.locator(`[data-rtm-toggle="${ids.requirements.email}"]`).click();
    await expect(rtm).toContainText(copy.work.email);
    await expect(rtm).toContainText('example/membership · Issue #41');
    await rtm.locator(`[data-rtm-select="${ids.work.email}"]`).click();
    await expect(rtm.locator('[data-rtm-pr]')).toHaveCount(2);
    await expect(rtm).toContainText('example/membership · PR #87');
    await expect(rtm).toContainText('example/membership · PR #91');
    await expectNoJunk(page);
    await shot(page, `rtm-${locale}-${width}`);

    await openView(page, 'board');
    await expectView(page, locale, width, 'board', copy.name);
    const board = page.locator('[data-pm-kanban]');
    await expect(board.locator('[data-pm-kanban-work]')).toHaveCount(12);
    await expect(board.locator('[data-pm-kanban-column="accepted"]')).toContainText(copy.work.email);
    await expect(board.locator('[data-pm-kanban-column="review"]')).toContainText(copy.work.rate);
    await expect(board.locator('[data-pm-kanban-column="in_progress"]')).toContainText(copy.work.resetEmail);
    await expect(board.locator('[data-pm-kanban-column="waiting"]')).toContainText(copy.work.audit);
    await expect(board.locator('[data-pm-kanban-column="backlog"]')).toContainText(copy.work.a11y);
    const email = board.locator(`[data-pm-kanban-work="${ids.work.email}"]`);
    await email.locator('summary').click();
    await expect.poll(async () => (await email.innerText()).split(accepted).length - 1).toBe(2);
    await expectNoJunk(page);
    await shot(page, `board-${locale}-${width}`);

    await openView(page, 'agents');
    await expectView(page, locale, width, 'agents', copy.name);
    const agents = page.locator('[data-pm-resources]');
    await expect(agents.locator('[data-resource-row="codex-implementer"]')).toBeVisible();
    await expect(agents.locator('[data-resource-row="claude-reviewer"]')).toBeVisible();
    const unavailable = agents.locator('[data-resource-row="security-notes"]');
    await expect(unavailable).toContainText(locale === 'zh-TW' ? '來源無法讀取' : 'Source unavailable');
    await expect(unavailable).not.toContainText('100%');
    await expect(agents.locator('[data-resource-quota]')).toContainText('60%');
    await expectNoJunk(page);
    await shot(page, `agents-${locale}-${width}`);

    await openView(page, 'schedule');
    await expectView(page, locale, width, 'schedule', copy.name);
    const recovery = page.locator('[data-pm-milestone="recovery_review"]');
    await expect(recovery).toContainText(locale === 'zh-TW' ? '復原審查' : 'Recovery review');
    await expect(recovery).toContainText(dates.recoveryBaseline);
    await expect(recovery).toContainText(dates.recoveryForecast);
    await expect(page.locator('[data-pm-milestone="design_review"]')).toContainText(locale === 'zh-TW' ? '設計審查' : 'Design review');
    await expect(page.locator('[data-pm-milestone="launch"]')).toContainText(locale === 'zh-TW' ? '上線' : 'Launch');
    await expect(page.locator('[data-pm-milestone="launch"]')).toContainText(dates.launchDate);
    await expectNoJunk(page);
    await shot(page, `roadmap-${locale}-${width}`);

    await showThreads(page);
    await expectView(page, locale, width, 'discussions', copy.name);
    const threads = page.locator('.pm-workspace-list a.pm-thread-link');
    await expect(threads.filter({hasText: copy.threads.idea})).toBeVisible();
    await expect(threads.filter({hasText: copy.threads.delivery})).toBeVisible();
    await threads.filter({hasText: copy.threads.planning}).click();
    await expectView(page, locale, width, 'discussions', copy.name);
    await expect(page.locator('.pm-notes')).toContainText(copy.planningNote);
    await page.locator('[data-pm-information] > summary').click();
    await expect(page.locator('[data-pm-information]')).toContainText(copy.draftRationale);
    await expect(page.locator('[data-pm-information]')).toContainText(locale === 'zh-TW' ? '財務' : 'Finance');
    const proposals = page.locator('[data-pm-plan-proposals]');
    await proposals.getByLabel(locale === 'zh-TW' ? '已保存提案' : 'Saved proposal').selectOption(ids.proposalId);
    await expect(proposals).toContainText(copy.proposalSummary);
    await expect(proposals.getByLabel(locale === 'zh-TW' ? '計畫名稱' : 'Plan title')).toHaveValue(copy.proposalTitle);
    await expectNoJunk(page);
    await shot(page, `planning-${locale}-${width}`);

    await page.goto(demoRoute('manual'));
    await expect(page.locator('h1')).toContainText(copy.manualTitle);
    await expectProjectName(page, width, copy.name);
    await expect(page.locator('#main')).toContainText(locale === 'zh-TW' ? '這個工作階段由人在 BAT 建立' : 'A person created this session in BAT');
    await expect(page.locator('#main')).toContainText(copy.manualNote);
    await expect(page.locator('#workspace-message-input')).toBeHidden();
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
