import {test, expect, type Page} from '@playwright/test';
import {workspaceFixture} from './workspace-fixture.ts';
import {workbenchFixture, workspaceId as fixtureWorkspaceId, revisionId} from './pm-workbench-fixture.ts';
import {mountAttention} from './attention-fixture.ts';

function createScrollFixture(locale = 'en', {withLifecycle = false} = {}) {
  const ws = workspaceFixture(locale);
  const pm = workbenchFixture();

  const combinedCaps = () => {
    const c = ws.caps();
    c.features.pm_overview = {version: 1};
    c.features.pm_workspaces = {
      version: 1,
      executable_modes: ['idea', 'planning'],
      context_packages: true,
      brief_snapshots: true,
      kanban: true,
      issue_mapping: true,
      engineering_delivery: {contract: 'pm-engineering-delivery/v1'},
      work_evidence: true,
      plan_drafting: true,
      plan_adoption: true,
      ...(withLifecycle ? {lifecycle: {version: 1, gated_actions: [], record_actions: []}} : {}),
    };
    c.actions.push(
      {action: 'pm.workspace.create', allowed: true},
      {action: 'pm.snapshot.create', allowed: true},
      {action: 'pm.thread.create', allowed: true},
      {action: 'pm.message.append', allowed: true},
    );
    return c;
  };

  // Ensure requirements view has plenty of items to be tall (>1500px) at both 1440 and 390
  for (let i = 3; i <= 35; i++) {
    pm.plan.document.requirements.push({
      id: `membership.req_${i}`,
      text: `Additional requirement item ${i} ensuring scroll depth verification`,
      acceptance: [{source_input_id: `crit-${i}`, text: `Criteria ${i} detail.`}]
    });
  }

  // Ensure board view has plenty of cards to be tall (>1500px) at both 1440 and 390
  for (let i = 4; i <= 35; i++) {
    const wid = 'wi_' + String(i).padStart(20, '0');
    pm.board.state.cards.push({
      work_item_id: wid,
      binding: {
        workspace_id: fixtureWorkspaceId,
        work_item_id: wid,
        sequence: i,
        binding_id: 'pmwb_' + String(i).padStart(32, '0'),
        project_id: 'prj_' + '2'.repeat(20),
        plan_revision_id: revisionId,
        requirement_ids: ['membership.login'],
        work_version: 1
      },
      work: {
        work_item_id: wid,
        title: `Card ${i} scroll height verification`,
        version: 1,
        project_id: 'prj_' + '2'.repeat(20),
        state: 'doing'
      },
      column: 'in_progress',
      completion: {approved: false, pending: false},
      flow: {wip: true, age_seconds: 3600, waiting_seconds: null, history: 'available_central_events', reopened: 0},
      quality: {dod: 'unassessed'},
      execution: {accepted_attempts: 0, tasks: [], operations: [], sessions: [], attempts: [], coverage: {network_checked: false, unavailable_sources: false, truncated: false}},
      latest_capture: null,
      capture_matches_binding: true,
      capture_matches_work_version: true
    });
  }

  let heldPromise: Promise<void> | null = null;
  let releaseHeld: (() => void) | null = null;
  let readStarted = false;
  let holdPathPrefix: string | null = null;
  let threadsCallCount = 0;

  const holdNext = (pathPrefix: string) => {
    holdPathPrefix = pathPrefix;
    readStarted = false;
    heldPromise = new Promise<void>(resolve => {
      releaseHeld = resolve;
    });
  };

  const release = () => {
    if (releaseHeld) {
      releaseHeld();
      releaseHeld = null;
      heldPromise = null;
      holdPathPrefix = null;
    }
  };

  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture');
    const path = url.pathname;

    if (input.method !== 'GET') {
      if (path.startsWith('/pm') || input.body?.action?.startsWith('pm.')) {
        return pm.dispatch(input);
      }
      return ws.dispatch(input);
    }

    if (holdPathPrefix && path.startsWith(holdPathPrefix) && heldPromise) {
      readStarted = true;
      await heldPromise;
    }

    if (path.endsWith('/threads') && path.includes('/pm/workspaces/')) {
      threadsCallCount++;
    }

    if (path === '/capabilities') return {status: 200, data: combinedCaps()};
    if (path === '/bootstrap') {
      return {
        status: 200,
        data: {
          capabilities: combinedCaps(),
          sync: {version: 1, server_id: 'scroll-fixture', principal_id: ws.fixture.data.principal, checkpoint: {cursor: 0, token: 'w-0'}}
        }
      };
    }

    if (path === `/pm/workspaces/${fixtureWorkspaceId}/lifecycle`) {
      return {
        status: 200,
        data: {
          workspace_id: fixtureWorkspaceId,
          lifecycle: {
            stage: 'planning',
            status: 'active',
            revision: 1,
            execution_epoch: 1,
            transition_id: null,
            coverage: 'recorded'
          },
          transition: null,
          transition_visibility_filtered: false,
          blocked_actions: [],
          record_actions: [],
          available_stages: ['idea', 'planning', 'development'],
          maintenance: {available: false, reason: 'release_baseline_and_policy_required'},
          inflight_summary: {visible_unresolved: 0, visible_retained: 0, visibility_filtered: false}
        }
      };
    }

    if (path.endsWith('/projects') && path.includes('/pm/workspaces/')) {
      return {status: 200, data: {workspace_id: fixtureWorkspaceId, bindings: [], next_cursor: null}};
    }
    if (path.endsWith('/work') && path.includes('/pm/workspaces/')) {
      return {status: 200, data: {workspace_id: fixtureWorkspaceId, bindings: [], next_cursor: null}};
    }
    if (path === `/pm/workspaces/${fixtureWorkspaceId}/overview`) {
      return {
        status: 200,
        data: {
          workspace_id: fixtureWorkspaceId,
          version: 1,
          attention: [],
          milestones: [],
          recent_deliveries: [],
        }
      };
    }

    if (path.startsWith('/pm/')) {
      return pm.dispatch(input);
    }

    return ws.dispatch(input);
  };

  return {
    ws,
    pm,
    dispatch,
    combinedCaps,
    holdNext,
    release,
    isReadStarted: () => readStarted,
    threadsCount: () => threadsCallCount,
  };
}

async function leaveByAnotherProjectView(page: Page, view: string) {
  const mgmt = page.locator('select[data-workbench-management]');
  await expect(mgmt).toBeVisible();
  await mgmt.selectOption(view);
}

async function leaveByProjectsEntry(page: Page, width: number) {
  if (width < 1000) {
    const toggle = page.locator('#workspace-menu');
    await expect(toggle).toBeVisible();
    await toggle.click();
    const link = page.locator('.shell-work-header .shell-all-projects').first();
    await expect(link).toBeVisible();
    await link.click();
  } else {
    const switcher = page.locator('button.shell-workspace-switch').first();
    await expect(switcher).toBeVisible();
    await switcher.click();
    const allProjects = page.locator('.ui-menu a[href="#/projects"]').first();
    await expect(allProjects).toBeVisible();
    await allProjects.click();
  }
  await expect(page.locator('[data-project-list]')).toBeVisible();
}

const MATRIX_CASES: Array<{
  view: 'board' | 'requirements';
  leaveBy: 'view' | 'projects';
  lang: 'en' | 'zh-TW';
  width: 1440 | 390;
}> = [
  // Board: leave by another project view
  {view: 'board', leaveBy: 'view', lang: 'en', width: 1440},
  {view: 'board', leaveBy: 'view', lang: 'en', width: 390},
  {view: 'board', leaveBy: 'view', lang: 'zh-TW', width: 1440},
  {view: 'board', leaveBy: 'view', lang: 'zh-TW', width: 390},
  // Board: leave by Projects entry
  {view: 'board', leaveBy: 'projects', lang: 'en', width: 1440},
  {view: 'board', leaveBy: 'projects', lang: 'en', width: 390},
  {view: 'board', leaveBy: 'projects', lang: 'zh-TW', width: 1440},
  {view: 'board', leaveBy: 'projects', lang: 'zh-TW', width: 390},
  // Requirements: leave by another project view
  {view: 'requirements', leaveBy: 'view', lang: 'en', width: 1440},
  {view: 'requirements', leaveBy: 'view', lang: 'en', width: 390},
  {view: 'requirements', leaveBy: 'view', lang: 'zh-TW', width: 1440},
  {view: 'requirements', leaveBy: 'view', lang: 'zh-TW', width: 390},
  // Requirements: leave by Projects entry
  {view: 'requirements', leaveBy: 'projects', lang: 'en', width: 1440},
  {view: 'requirements', leaveBy: 'projects', lang: 'en', width: 390},
  {view: 'requirements', leaveBy: 'projects', lang: 'zh-TW', width: 1440},
  {view: 'requirements', leaveBy: 'projects', lang: 'zh-TW', width: 390},
];

const WORKSPACE_LAST_CASES: Array<{
  view: 'board' | 'requirements';
  lang: 'en' | 'zh-TW';
  width: 1440 | 390;
}> = [
  {view: 'board', lang: 'en', width: 1440},
  {view: 'board', lang: 'en', width: 390},
  {view: 'board', lang: 'zh-TW', width: 1440},
  {view: 'board', lang: 'zh-TW', width: 390},
  {view: 'requirements', lang: 'en', width: 1440},
  {view: 'requirements', lang: 'en', width: 390},
  {view: 'requirements', lang: 'zh-TW', width: 1440},
  {view: 'requirements', lang: 'zh-TW', width: 390},
];

const PRODUCTION_HEADER_CASES: Array<{
  view: 'board' | 'requirements';
  lang: 'en' | 'zh-TW';
}> = [
  {view: 'board', lang: 'en'},
  {view: 'board', lang: 'zh-TW'},
  {view: 'requirements', lang: 'en'},
  {view: 'requirements', lang: 'zh-TW'},
];

test.describe('PM Scroll Leave & Restore', () => {
  for (const tc of MATRIX_CASES) {
    const leaveLabel = tc.leaveBy === 'view' ? 'another project view' : 'Projects entry';
    test(`${tc.view}: scroll during read and leave by ${leaveLabel} (${tc.lang} ${tc.width})`, async ({page}) => {
      const height = tc.width === 1440 ? 900 : 844;
      await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), tc.lang);
      await page.setViewportSize({width: tc.width, height});

      const f = createScrollFixture(tc.lang);
      await mountAttention(page, false, {...f.ws.fixture, caps: f.combinedCaps}, f.dispatch);

      // 1. View is settled
      await page.goto(`/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/${tc.view}`);
      const expectedTitle = tc.view === 'board'
        ? (tc.lang === 'en' ? 'Board' : '工作看板')
        : (tc.lang === 'en' ? 'Requirements' : '需求');
      await expect(page.locator('#main h1').first()).toHaveText(expectedTitle);
      if (tc.view === 'board') {
        await expect(page.locator('[data-pm-kanban] .pm-kanban-card').first()).toBeVisible();
      } else {
        await expect(page.locator('[data-rtm-requirement]').first()).toBeVisible();
      }

      const scrollMax = await page.evaluate(() => {
        const doc = document.scrollingElement || document.documentElement;
        return Math.max(0, doc.scrollHeight - window.innerHeight);
      });
      expect(scrollMax).toBeGreaterThanOrEqual(250);

      // 2. Refresh read starts and is held
      const holdPath = tc.view === 'board'
        ? `/pm/workspaces/${fixtureWorkspaceId}/kanban`
        : `/pm/workspaces/${fixtureWorkspaceId}/plans/accepted`;
      f.holdNext(holdPath);
      await page.locator('button[data-pm-refresh]').click();
      await expect.poll(() => f.isReadStarted()).toBe(true);

      // 3. Scroll to 200
      await page.evaluate(() => window.scrollTo(0, 200));
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(200);

      // 4. Leave
      if (tc.leaveBy === 'view') {
        await leaveByAnotherProjectView(page, 'overview');
        await expect(page.locator('[data-pm-overview]')).toBeVisible();
      } else {
        await leaveByProjectsEntry(page, tc.width);
        await page.evaluate(() => window.scrollTo(0, 0));
        await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
      }

      // 5. Release held read
      f.release();

      // 6. Press Back
      await page.goBack();
      await expect(page.locator('#main h1').first()).toHaveText(expectedTitle);
      await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();
      await expect(page.locator('#main [data-pm-source-pending]:visible')).toHaveCount(0);

      // 7. Expect position within 8 px of 200
      await expect.poll(async () => Math.abs(await page.evaluate(() => window.scrollY) - 200)).toBeLessThanOrEqual(8);
    });
  }

  // Controls: scroll after read has settled still restores as today
  test('control: board scroll after read has settled restores (en 1440)', async ({page}) => {
    const lang = 'en', width = 1440;
    await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), lang);
    await page.setViewportSize({width, height: 900});

    const f = createScrollFixture(lang);
    await mountAttention(page, false, {...f.ws.fixture, caps: f.combinedCaps}, f.dispatch);

    await page.goto(`/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/board`);
    await expect(page.locator('#main h1').first()).toHaveText('Board');
    await expect(page.locator('[data-pm-kanban] .pm-kanban-card').first()).toBeVisible();
    await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();

    await page.evaluate(() => window.scrollTo(0, 200));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(200);
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));

    await leaveByAnotherProjectView(page, 'overview');
    await expect(page.locator('[data-pm-overview]')).toBeVisible();

    await page.goBack();
    await expect(page.locator('#main h1').first()).toHaveText('Board');
    await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();
    await expect(page.locator('#main [data-pm-source-pending]:visible')).toHaveCount(0);

    await expect.poll(async () => Math.abs(await page.evaluate(() => window.scrollY) - 200)).toBeLessThanOrEqual(8);
  });

  test('control: requirements scroll after read has settled restores (zh-TW 390)', async ({page}) => {
    const lang = 'zh-TW', width = 390;
    await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), lang);
    await page.setViewportSize({width, height: 844});

    const f = createScrollFixture(lang);
    await mountAttention(page, false, {...f.ws.fixture, caps: f.combinedCaps}, f.dispatch);

    await page.goto(`/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/requirements`);
    await expect(page.locator('#main h1').first()).toHaveText('需求');
    await expect(page.locator('[data-rtm-requirement]').first()).toBeVisible();
    await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();
    await expect(page.locator('[data-pm-work-meta]')).toHaveText(/loaded|已載入/);

    await page.evaluate(() => window.scrollTo(0, 200));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(200);
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));

    await leaveByAnotherProjectView(page, 'overview');
    await expect(page.locator('[data-pm-overview]')).toBeVisible();

    await page.goBack();
    await expect(page.locator('#main h1').first()).toHaveText('需求');
    await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();
    await expect(page.locator('#main [data-pm-source-pending]:visible')).toHaveCount(0);

    await expect.poll(async () => Math.abs(await page.evaluate(() => window.scrollY) - 200)).toBeLessThanOrEqual(8);
  });

  // Workspace finishes last: threads held on Back until view items render, then released
  for (const tc of WORKSPACE_LAST_CASES) {
    test(`workspace finishes last: ${tc.view} scroll restore after leave by Projects entry (${tc.lang} ${tc.width})`, async ({page}) => {
      const height = tc.width === 1440 ? 900 : 844;
      await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), tc.lang);
      await page.setViewportSize({width: tc.width, height});

      const f = createScrollFixture(tc.lang);
      await mountAttention(page, false, {...f.ws.fixture, caps: f.combinedCaps}, f.dispatch);

      await page.goto(`/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/${tc.view}`);
      const expectedTitle = tc.view === 'board'
        ? (tc.lang === 'en' ? 'Board' : '工作看板')
        : (tc.lang === 'en' ? 'Requirements' : '需求');
      await expect(page.locator('#main h1').first()).toHaveText(expectedTitle);
      const itemSelector = tc.view === 'board' ? '[data-pm-kanban] .pm-kanban-card' : '[data-rtm-requirement]';
      await expect(page.locator(itemSelector).first()).toBeVisible();

      const holdPath = tc.view === 'board'
        ? `/pm/workspaces/${fixtureWorkspaceId}/kanban`
        : `/pm/workspaces/${fixtureWorkspaceId}/plans/accepted`;
      f.holdNext(holdPath);
      await page.locator('button[data-pm-refresh]').click();
      await expect.poll(() => f.isReadStarted()).toBe(true);

      await page.evaluate(() => window.scrollTo(0, 200));
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(200);

      await leaveByProjectsEntry(page, tc.width);
      await page.evaluate(() => window.scrollTo(0, 0));
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);

      f.release();

      // On Back, hold /pm/workspaces/{id}/threads until the view's items render, then release.
      f.holdNext(`/pm/workspaces/${fixtureWorkspaceId}/threads`);
      await page.goBack();
      await expect.poll(() => f.isReadStarted()).toBe(true);
      await expect.poll(() => page.locator(itemSelector).count()).toBeGreaterThan(0);
      f.release();

      await expect(page.locator('#main h1').first()).toHaveText(expectedTitle);
      await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();
      await expect(page.locator('#main [data-pm-source-pending]:visible')).toHaveCount(0);

      await expect.poll(async () => Math.abs(await page.evaluate(() => window.scrollY) - 200)).toBeLessThanOrEqual(8);
    });
  }

  // View finishes last: view's own read held on Back until workspace reads finish, then released
  for (const tc of WORKSPACE_LAST_CASES) {
    test(`view finishes last: ${tc.view} scroll restore after leave by Projects entry (${tc.lang} ${tc.width})`, async ({page}) => {
      const height = tc.width === 1440 ? 900 : 844;
      await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), tc.lang);
      await page.setViewportSize({width: tc.width, height});

      const f = createScrollFixture(tc.lang);
      await mountAttention(page, false, {...f.ws.fixture, caps: f.combinedCaps}, f.dispatch);

      await page.goto(`/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/${tc.view}`);
      const expectedTitle = tc.view === 'board'
        ? (tc.lang === 'en' ? 'Board' : '工作看板')
        : (tc.lang === 'en' ? 'Requirements' : '需求');
      await expect(page.locator('#main h1').first()).toHaveText(expectedTitle);
      const itemSelector = tc.view === 'board' ? '[data-pm-kanban] .pm-kanban-card' : '[data-rtm-requirement]';
      await expect(page.locator(itemSelector).first()).toBeVisible();

      const holdPath = tc.view === 'board'
        ? `/pm/workspaces/${fixtureWorkspaceId}/kanban`
        : `/pm/workspaces/${fixtureWorkspaceId}/plans/accepted`;
      f.holdNext(holdPath);
      await page.locator('button[data-pm-refresh]').click();
      await expect.poll(() => f.isReadStarted()).toBe(true);

      await page.evaluate(() => window.scrollTo(0, 200));
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(200);

      await leaveByProjectsEntry(page, tc.width);
      await page.evaluate(() => window.scrollTo(0, 0));
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);

      f.release();

      // On Back, hold the view's own read until workspace reads have completed, then release.
      const threadsBefore = f.threadsCount();
      f.holdNext(holdPath);
      await page.goBack();
      await expect.poll(() => f.isReadStarted()).toBe(true);
      await expect.poll(() => f.threadsCount()).toBeGreaterThan(threadsBefore);
      f.release();

      await expect(page.locator('#main h1').first()).toHaveText(expectedTitle);
      await expect.poll(() => page.locator(itemSelector).count()).toBeGreaterThan(0);
      await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();
      await expect(page.locator('#main [data-pm-source-pending]:visible')).toHaveCount(0);

      await expect.poll(async () => Math.abs(await page.evaluate(() => window.scrollY) - 200)).toBeLessThanOrEqual(8);
    });
  }

  // Production-like header: lifecycle stage enabled, phase pill shows
  for (const tc of PRODUCTION_HEADER_CASES) {
    test(`production-like header: ${tc.view} scroll restore with lifecycle phase pill (${tc.lang} 390)`, async ({page}) => {
      await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), tc.lang);
      await page.setViewportSize({width: 390, height: 844});

      const f = createScrollFixture(tc.lang, {withLifecycle: true});
      await mountAttention(page, false, {...f.ws.fixture, caps: f.combinedCaps}, f.dispatch);

      await page.goto(`/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/${tc.view}`);
      const expectedTitle = tc.view === 'board'
        ? (tc.lang === 'en' ? 'Board' : '工作看板')
        : (tc.lang === 'en' ? 'Requirements' : '需求');
      await expect(page.locator('#main h1').first()).toHaveText(expectedTitle);
      const itemSelector = tc.view === 'board' ? '[data-pm-kanban] .pm-kanban-card' : '[data-rtm-requirement]';
      await expect(page.locator(itemSelector).first()).toBeVisible();

      const expectedPhase = tc.lang === 'en' ? 'Planning' : '規劃';
      await expect(page.locator('.ui-page-header .ui-pill').first()).toHaveText(expectedPhase);

      const holdPath = tc.view === 'board'
        ? `/pm/workspaces/${fixtureWorkspaceId}/kanban`
        : `/pm/workspaces/${fixtureWorkspaceId}/plans/accepted`;
      f.holdNext(holdPath);
      await page.locator('button[data-pm-refresh]').click();
      await expect.poll(() => f.isReadStarted()).toBe(true);

      await page.evaluate(() => window.scrollTo(0, 200));
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(200);

      await leaveByProjectsEntry(page, 390);
      await page.evaluate(() => window.scrollTo(0, 0));
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);

      f.release();

      // On Back, hold the lifecycle read so it finishes last.
      f.holdNext(`/pm/workspaces/${fixtureWorkspaceId}/lifecycle`);
      await page.goBack();
      await expect.poll(() => f.isReadStarted()).toBe(true);
      await expect.poll(() => page.locator(itemSelector).count()).toBeGreaterThan(0);
      f.release();

      await expect(page.locator('#main h1').first()).toHaveText(expectedTitle);
      await expect(page.locator('.ui-page-header .ui-pill').first()).toHaveText(expectedPhase);
      await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();
      await expect(page.locator('#main [data-pm-source-pending]:visible')).toHaveCount(0);

      await expect.poll(async () => Math.abs(await page.evaluate(() => window.scrollY) - 200)).toBeLessThanOrEqual(8);
    });
  }
});
