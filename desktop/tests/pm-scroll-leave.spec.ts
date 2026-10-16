import {test, expect, type Page} from '@playwright/test';
import {workspaceFixture} from './workspace-fixture.ts';
import {workbenchFixture, workspaceId as fixtureWorkspaceId, revisionId} from './pm-workbench-fixture.ts';
import {mountAttention} from './attention-fixture.ts';

function createScrollFixture(locale = 'en') {
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
  };
}

async function ensureDrawerOpen(page: Page, width: number) {
  if (width < 1000) {
    const toggle = page.locator('#workspace-menu');
    const expanded = await toggle.getAttribute('aria-expanded');
    if (expanded !== 'true') {
      await toggle.click();
      await expect(page.locator('aside#workspace-sidebar')).toBeVisible();
    }
  }
}

async function ensureDrawerClosed(page: Page, width: number) {
  if (width < 1000) {
    const modalSidebar = page.locator('aside#workspace-sidebar[aria-modal="true"]');
    const isModal = await modalSidebar.isVisible();
    if (isModal) {
      await page.keyboard.press('Escape');
      await expect(modalSidebar).toBeHidden();
    }
  }
}

async function leaveByAnotherProjectView(page: Page, view: string) {
  const mgmt = page.locator('select[data-workbench-management]');
  if (await mgmt.isVisible()) {
    await mgmt.selectOption(view);
  } else {
    await page.locator(`.pm-project-views button[data-pm-view="${view}"]`).first().click();
  }
}

async function leaveByProjectsEntry(page: Page, width: number) {
  if (width < 1000) {
    await ensureDrawerOpen(page, width);
    const link = page.locator('.shell-work-header .shell-all-projects').first();
    await expect(link).toBeVisible();
    await link.click();
    await ensureDrawerClosed(page, width);
  } else {
    const switcher = page.locator('button.shell-workspace-switch').first();
    if (await switcher.isVisible()) {
      await switcher.click();
      const allProjects = page.locator('.ui-menu a[href="#/projects"]').first();
      await expect(allProjects).toBeVisible();
      await allProjects.click();
    } else {
      const link = page.locator('a[href="#/projects"]').first();
      await expect(link).toBeVisible();
      await link.click();
    }
  }
  await expect(page.locator('[data-project-list]')).toBeVisible();
}

test.describe('PM Scroll Leave & Restore', () => {
  test('board: scroll during read and leave by another project view (en 1440)', async ({page}) => {
    const lang = 'en', width = 1440;
    await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), lang);
    await page.setViewportSize({width, height: 900});

    const f = createScrollFixture(lang);
    await mountAttention(page, false, {...f.ws.fixture, caps: f.combinedCaps}, f.dispatch);

    // 1. View is settled
    await page.goto(`/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/board`);
    await expect(page.locator('#main h1').first()).toHaveText('Board');
    await expect(page.locator('[data-pm-kanban] .pm-kanban-card').first()).toBeVisible();

    const scrollMax = await page.evaluate(() => {
      const doc = document.scrollingElement || document.documentElement;
      return Math.max(0, doc.scrollHeight - window.innerHeight);
    });
    expect(scrollMax).toBeGreaterThanOrEqual(250);

    // 2. Refresh read starts and is held
    f.holdNext(`/pm/workspaces/${fixtureWorkspaceId}/kanban`);
    await page.locator('button[data-pm-refresh]').click();
    await expect.poll(() => f.isReadStarted()).toBe(true);

    // 3. Scroll to 200
    await page.evaluate(() => window.scrollTo(0, 200));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(200);

    // 4. Leave by another project view
    await leaveByAnotherProjectView(page, 'overview');
    await expect(page.locator('[data-pm-overview]')).toBeVisible();

    // 5. Release held read
    f.release();

    // 6. Press Back
    await page.goBack();
    await expect(page.locator('#main h1').first()).toHaveText('Board');

    // 7. Expect position within 8 px of 200
    await expect.poll(() => page.evaluate(() => window.scrollY), {
      message: 'Board scroll position should restore within 8px of 200 after view switch',
      timeout: 5000
    }).toBeCloseTo(200, -1);
    const finalY = await page.evaluate(() => window.scrollY);
    expect(Math.abs(finalY - 200)).toBeLessThanOrEqual(8);
  });

  test('board: scroll during read and leave by Projects entry (zh-TW 390)', async ({page}) => {
    const lang = 'zh-TW', width = 390;
    await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), lang);
    await page.setViewportSize({width, height: 844});

    const f = createScrollFixture(lang);
    await mountAttention(page, false, {...f.ws.fixture, caps: f.combinedCaps}, f.dispatch);

    // 1. View is settled
    await page.goto(`/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/board`);
    await expect(page.locator('#main h1').first()).toHaveText('工作看板');
    await expect(page.locator('[data-pm-kanban] .pm-kanban-card').first()).toBeVisible();

    const scrollMax = await page.evaluate(() => {
      const doc = document.scrollingElement || document.documentElement;
      return Math.max(0, doc.scrollHeight - window.innerHeight);
    });
    expect(scrollMax).toBeGreaterThanOrEqual(250);

    // 2. Refresh read starts and is held
    f.holdNext(`/pm/workspaces/${fixtureWorkspaceId}/kanban`);
    await page.locator('button[data-pm-refresh]').click();
    await expect.poll(() => f.isReadStarted()).toBe(true);

    // 3. Scroll to 200
    await page.evaluate(() => window.scrollTo(0, 200));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(200);

    // 4. Leave by Projects entry
    await leaveByProjectsEntry(page, width);
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);

    // 5. Release held read
    f.release();

    // 6. Press Back
    await page.goBack();
    await expect(page.locator('#main h1').first()).toHaveText('工作看板');

    // 7. Expect position within 8 px of 200
    await expect.poll(() => page.evaluate(() => window.scrollY), {
      message: 'Board scroll position should restore within 8px of 200 after leaving route',
      timeout: 5000
    }).toBeCloseTo(200, -1);
    const finalY = await page.evaluate(() => window.scrollY);
    expect(Math.abs(finalY - 200)).toBeLessThanOrEqual(8);
  });

  test('requirements: scroll during read and leave by another project view (en 390)', async ({page}) => {
    const lang = 'en', width = 390;
    await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), lang);
    await page.setViewportSize({width, height: 844});

    const f = createScrollFixture(lang);
    await mountAttention(page, false, {...f.ws.fixture, caps: f.combinedCaps}, f.dispatch);

    // 1. View is settled
    await page.goto(`/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/requirements`);
    await expect(page.locator('#main h1').first()).toHaveText('Requirements');
    await expect(page.locator('[data-rtm-requirement]').first()).toBeVisible();

    const scrollMax = await page.evaluate(() => {
      const doc = document.scrollingElement || document.documentElement;
      return Math.max(0, doc.scrollHeight - window.innerHeight);
    });
    expect(scrollMax).toBeGreaterThanOrEqual(250);

    // 2. Refresh read starts and is held
    f.holdNext(`/pm/workspaces/${fixtureWorkspaceId}/plans/accepted`);
    await page.locator('button[data-pm-refresh]').click();
    await expect.poll(() => f.isReadStarted()).toBe(true);

    // 3. Scroll to 200
    await page.evaluate(() => window.scrollTo(0, 200));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(200);

    // 4. Leave by another project view (overview)
    await leaveByAnotherProjectView(page, 'overview');
    await expect(page.locator('[data-pm-overview]')).toBeVisible();

    // 5. Release held read
    f.release();

    // 6. Press Back
    await page.goBack();
    await expect(page.locator('#main h1').first()).toHaveText('Requirements');

    // 7. Expect position within 8 px of 200
    await expect.poll(() => page.evaluate(() => window.scrollY), {
      message: 'Requirements scroll position should restore within 8px of 200 after view switch',
      timeout: 5000
    }).toBeCloseTo(200, -1);
    const finalY = await page.evaluate(() => window.scrollY);
    expect(Math.abs(finalY - 200)).toBeLessThanOrEqual(8);
  });

  test('requirements: scroll during read and leave by Projects entry (zh-TW 1440)', async ({page}) => {
    const lang = 'zh-TW', width = 1440;
    await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), lang);
    await page.setViewportSize({width, height: 900});

    const f = createScrollFixture(lang);
    await mountAttention(page, false, {...f.ws.fixture, caps: f.combinedCaps}, f.dispatch);

    // 1. View is settled
    await page.goto(`/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/requirements`);
    await expect(page.locator('#main h1').first()).toHaveText('需求');
    await expect(page.locator('[data-rtm-requirement]').first()).toBeVisible();

    const scrollMax = await page.evaluate(() => {
      const doc = document.scrollingElement || document.documentElement;
      return Math.max(0, doc.scrollHeight - window.innerHeight);
    });
    expect(scrollMax).toBeGreaterThanOrEqual(250);

    // 2. Refresh read starts and is held
    f.holdNext(`/pm/workspaces/${fixtureWorkspaceId}/plans/accepted`);
    await page.locator('button[data-pm-refresh]').click();
    await expect.poll(() => f.isReadStarted()).toBe(true);

    // 3. Scroll to 200
    await page.evaluate(() => window.scrollTo(0, 200));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(200);

    // 4. Leave by Projects entry
    await leaveByProjectsEntry(page, width);
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);

    // 5. Release held read
    f.release();

    // 6. Press Back
    await page.goBack();
    await expect(page.locator('#main h1').first()).toHaveText('需求');

    // 7. Expect position within 8 px of 200
    await expect.poll(() => page.evaluate(() => window.scrollY), {
      message: 'Requirements scroll position should restore within 8px of 200 after leaving route',
      timeout: 5000
    }).toBeCloseTo(200, -1);
    const finalY = await page.evaluate(() => window.scrollY);
    expect(Math.abs(finalY - 200)).toBeLessThanOrEqual(8);
  });

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

    await expect.poll(() => page.evaluate(() => window.scrollY), {
      message: 'Board settled scroll should restore within 8px of 200',
      timeout: 5000
    }).toBeCloseTo(200, -1);
    const finalY = await page.evaluate(() => window.scrollY);
    expect(Math.abs(finalY - 200)).toBeLessThanOrEqual(8);
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

    await expect.poll(() => page.evaluate(() => window.scrollY), {
      message: 'Requirements settled scroll should restore within 8px of 200',
      timeout: 5000
    }).toBeCloseTo(200, -1);
    const finalY = await page.evaluate(() => window.scrollY);
    expect(Math.abs(finalY - 200)).toBeLessThanOrEqual(8);
  });
});
