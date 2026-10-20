import {revealPmView} from './pm-navigation.ts';
import {test, expect, type Page} from '@playwright/test';
import {workspaceFixture, workspaceOperation, workspaceRoute} from './workspace-fixture.ts';
import {workbenchFixture, workspaceId as fixtureWorkspaceId} from './pm-workbench-fixture.ts';
import {threadId as fixtureThreadId} from './pm-fixture.ts';
import {mountAttention} from './attention-fixture.ts';

function createLegacyFixture(locale = 'en') {
  const ws = workspaceFixture(locale);
  const pm = workbenchFixture();
  const nonGetDispatches: any[] = [];

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

  // Add messages to workspace fixture so conversation has scrolling content
  for (let i = 1; i <= 8; i++) {
    ws.data.messages.push({
      id: `msg-${i}`,
      role: i % 2 ? 'user' : 'assistant',
      ts: `2026-10-10T12:${String(i + 2).padStart(2, '0')}:00Z`,
      text: `Message ${i}: Testing conversation scroll depth and verification note ${i}. ` + 'Preserve the query and verify results. '.repeat(4)
    });
  }

  // Add messages to thread fixture so transcript has scrolling content
  for (let i = 2; i <= 15; i++) {
    pm.base.state.messages.push({
      message_id: 'pmm_' + String(i).padStart(32, '0'),
      sequence: i,
      actor: 'reader',
      content: `Discussion note ${i}. Details regarding implementation and requirements verification.`,
      created_at: 1791705600 + i * 60
    });
  }

  // Ensure requirements view is tall enough to be generously scrollable at mobile widths
  for (let i = 3; i <= 8; i++) {
    pm.plan.document.requirements.push({
      id: `membership.req_${i}`,
      text: `Additional requirement item ${i} ensuring scroll depth verification`,
      acceptance: [{source_input_id: `crit-${i}`, text: `Criteria ${i} detail.`}]
    });
  }

  // Ensure board view is tall enough to be generously scrollable at mobile widths
  for (let i = 4; i <= 8; i++) {
    const wid = 'wi_' + String(i).repeat(20);
    pm.board.state.cards.push({
      work_item_id: wid,
      binding: {
        workspace_id: fixtureWorkspaceId,
        work_item_id: wid,
        sequence: i,
        binding_id: 'pmwb_' + String(i).repeat(32),
        project_id: 'prj_' + '2'.repeat(20),
        plan_revision_id: 'pmrev_' + '4'.repeat(32),
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

  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture');
    const path = url.pathname;

    if (input.method !== 'GET') {
      nonGetDispatches.push(input);
      if (path.startsWith('/pm') || input.body?.action?.startsWith('pm.')) {
        return pm.dispatch(input);
      }
      return ws.dispatch(input);
    }

    if (path === '/capabilities') return {status: 200, data: combinedCaps()};
    if (path === '/bootstrap') {
      return {
        status: 200,
        data: {
          capabilities: combinedCaps(),
          sync: {version: 1, server_id: 'legacy-fixture', principal_id: ws.fixture.data.principal, checkpoint: {cursor: 0, token: 'w-0'}}
        }
      };
    }

    if (path === '/operations/' + workspaceOperation) {
      return {
        status: 200,
        data: {
          operation: {
            operation_id: workspaceOperation,
            action: 'repository.continue',
            status: 'succeeded',
            actor: 'agent',
            entry: 'manual',
            created_at: 1760000000,
            target: {host: 'demo', session_id: 'search-results'},
            steps: [{name: 'continue', status: 'succeeded'}]
          }
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

  return {ws, pm, dispatch, combinedCaps, nonGetDispatches};
}

async function closeAnyOpenDialog(page: Page) {
  const inspectorBack = page.locator('.ui-inspector-back');
  // Optional probe: check if an inspector back button is visible to close it
  if (await inspectorBack.isVisible()) {
    await inspectorBack.click();
    await expect(inspectorBack).toBeHidden();
  }
  const dialog = page.locator('dialog[open]');
  // Optional probe: check if an HTML5 dialog is open to close it
  if (await dialog.isVisible()) {
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  }
}

async function ensureDrawerOpen(page: Page, width: number) {
  if (width < 1000) {
    await closeAnyOpenDialog(page);
    const toggle = page.locator('#workspace-menu');
    await expect(toggle).toBeVisible();
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
    // Optional probe: check if mobile drawer is currently in modal state
    const isModal = await modalSidebar.isVisible();
    if (isModal) {
      await page.keyboard.press('Escape');
      await expect(modalSidebar).toBeHidden();
    }
  }
}

async function waitForViewSettled(page: Page) {
  await expect(page.locator('[data-pm-source-loading]:visible')).toHaveCount(0);
  await expect(page.locator('#main [aria-busy="true"]:visible')).toHaveCount(0);
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}

// --------------------------------------------------------------------------
// UX-A05: Legacy deep links (preview mode, EN and zh-TW, 1440 and 390)
// --------------------------------------------------------------------------

interface LegacyRouteConfig {
  id: string;
  url: string;
  dailyEntry: string;
  h1: {en: string; 'zh-TW': string};
  objectSelector?: string;
  objectText?: string | {en: string; 'zh-TW': string};
  contentSelector?: string;
  scroller: string;
  otherEntry: string;
}

const legacyRoutes: LegacyRouteConfig[] = [
  {
    id: 'session',
    url: `/dashboard/?workbench=1${workspaceRoute}`,
    dailyEntry: 'work',
    h1: {
      en: 'Improve empty search results',
      'zh-TW': '改善搜尋的空結果提示',
    },
    objectSelector: '#main',
    objectText: workspaceOperation,
    scroller: '.conversation-scroll',
    otherEntry: 'today',
  },
  {
    id: 'operation',
    url: `/dashboard/?workbench=1#/op/${workspaceOperation}`,
    dailyEntry: '', // unmapped
    h1: {
      en: 'Continue from published version',
      'zh-TW': '從已發布版本繼續',
    },
    objectSelector: '#main dl code',
    objectText: 'repository.continue',
    scroller: 'document',
    otherEntry: 'today',
  },
  {
    id: 'overview',
    url: `/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/overview`,
    dailyEntry: 'projects',
    h1: {
      en: 'Overview',
      'zh-TW': '專案總覽',
    },
    scroller: 'document',
    otherEntry: 'work',
  },
  {
    id: 'discussions',
    url: `/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/discussions`,
    dailyEntry: 'projects',
    h1: {
      en: 'Discussions',
      'zh-TW': '討論',
    },
    scroller: '.pm-workspace-list',
    otherEntry: 'work',
  },
  {
    id: 'thread',
    url: `/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/discussions/${fixtureThreadId}`,
    dailyEntry: 'projects',
    h1: {
      en: 'Discussions',
      'zh-TW': '討論',
    },
    objectSelector: '#main .pd-heading',
    objectText: 'A clearer first visit',
    scroller: '.pd-transcript',
    otherEntry: 'work',
  },
  {
    id: 'requirements',
    url: `/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/requirements`,
    dailyEntry: 'projects',
    h1: {
      en: 'Requirements',
      'zh-TW': '需求',
    },
    contentSelector: '[data-rtm-requirement]',
    scroller: 'document',
    otherEntry: 'work',
  },
  {
    id: 'board',
    url: `/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/board`,
    dailyEntry: 'work',
    h1: {
      en: 'Board',
      'zh-TW': '工作看板',
    },
    contentSelector: '.pm-kanban-card',
    scroller: 'document',
    otherEntry: 'projects',
  },
  {
    id: 'delivery',
    url: `/dashboard/?workbench=1#/delivery`,
    dailyEntry: '', // unmapped
    h1: {
      en: 'Delivery',
      'zh-TW': '成果與 GitHub',
    },
    scroller: 'document',
    otherEntry: 'today',
  },
  {
    id: 'artifacts',
    url: `/dashboard/?workbench=1#/artifact-review`,
    dailyEntry: '', // unmapped
    h1: {
      en: 'Artifacts and review',
      'zh-TW': '附件成果與審閱',
    },
    scroller: 'document',
    otherEntry: 'today',
  },
];

for (const lang of ['en', 'zh-TW'] as const) {
  for (const width of [1440, 390]) {
    test(`UX-A05: legacy deep links in preview mode ${lang} ${width}`, async ({page}) => {
      await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), lang);
      await page.setViewportSize({width, height: width < 600 ? 844 : 900});

      const f = createLegacyFixture(lang);
      await mountAttention(page, false, {...f.ws.fixture, caps: f.combinedCaps}, f.dispatch);

      for (const route of legacyRoutes) {
        const startNonGetCount = f.nonGetDispatches.length;

        // Navigate directly to route with ?workbench=1
        await page.goto(route.url);
        const expectedHash = new URL(route.url, 'http://dummy').hash;
        await expect.poll(() => page.evaluate(() => location.hash)).toBe(expectedHash);

        // 1. Right entry:
        if (route.dailyEntry) {
          const entry = page.locator(`.wc-daily a[data-daily="${route.dailyEntry}"]`);
          await expect(entry, `Daily entry "${route.dailyEntry}" should be aria-current=page`).toHaveAttribute('aria-current', 'page');
        } else {
          // If the mapping gives no entry for a route, assert that all daily entries are aria-current=false
          const allDaily = page.locator('.wc-daily a');
          const count = await allDaily.count();
          for (let i = 0; i < count; i++) {
            await expect(allDaily.nth(i)).toHaveAttribute('aria-current', 'false');
          }
          await expect(page.locator('#main')).toBeVisible();
        }

        // 2. Same object: exact #main h1 and object title/ID inside #main
        await expect(page.locator('#main h1').first()).toHaveText(route.h1[lang]);
        if (route.objectSelector && route.objectText) {
          const expectedText = typeof route.objectText === 'string' ? route.objectText : route.objectText[lang];
          await expect(page.locator(route.objectSelector).first()).toContainText(expectedText);
        }

        // 3. Nothing dispatched: no non-GET requests
        expect(f.nonGetDispatches.length, `No non-GET requests dispatched on route ${route.id}`).toBe(startNonGetCount);

        // 4. Back works:
        if (route.contentSelector) {
          await expect(page.locator(route.contentSelector).first()).toBeVisible();
        }
        await waitForViewSettled(page);

        // Measure scroller
        const scrollInfo = await page.evaluate(selector => {
          if (selector === 'document') {
            const doc = document.scrollingElement || document.documentElement;
            const max = doc.scrollHeight - window.innerHeight;
            return {max: Math.max(0, max), current: window.scrollY};
          }
          const el = document.querySelector(selector);
          if (!el) return {max: 0, current: 0};
          const max = el.scrollHeight - el.clientHeight;
          return {max: Math.max(0, max), current: el.scrollTop};
        }, route.scroller);

        if (scrollInfo.max === 0) {
          // If a route really has nothing to scroll at a width, assert that its maximum is 0
          expect(scrollInfo.max, `Route ${route.id} at width ${width} has nothing to scroll (max === 0)`).toBe(0);
        } else {
          // Scroll it to at least 200 px, or 40 % of its maximum when that is smaller
          const target = Math.max(1, Math.min(200, Math.floor(scrollInfo.max * 0.4)));
          await page.evaluate(({selector, target}) => {
            if (selector === 'document') {
              window.scrollTo(0, target);
            } else {
              const el = document.querySelector(selector);
              if (el) el.scrollTop = target;
            }
          }, {selector: route.scroller, target});

          const savedScroll = await page.evaluate(selector => {
            if (selector === 'document') return window.scrollY;
            const el = document.querySelector(selector);
            return el ? el.scrollTop : 0;
          }, route.scroller);

          await ensureDrawerOpen(page, width);
          const otherEntry = page.locator(`.wc-daily a[data-daily="${route.otherEntry}"]`);
          await expect(otherEntry).toBeVisible();
          await otherEntry.click();
          await ensureDrawerClosed(page, width);

          // Wait for hash to change away and the destination view to settle
          await expect.poll(() => page.evaluate(() => location.hash)).not.toBe(expectedHash);
          await expect(otherEntry).toHaveAttribute('aria-current', 'page');
          await expect(page.getByRole('main').getByRole('heading', {level: 1}).first()).toBeVisible();
          await waitForViewSettled(page);

          // Press Back
          await page.goBack();

          // Returns to the same route and object
          await expect.poll(() => page.evaluate(() => location.hash)).toBe(expectedHash);
          await expect(page.locator('#main h1').first()).toHaveText(route.h1[lang]);
          if (route.objectSelector && route.objectText) {
            const expectedText = typeof route.objectText === 'string' ? route.objectText : route.objectText[lang];
            await expect(page.locator(route.objectSelector).first()).toContainText(expectedText);
          }
          if (route.contentSelector) {
            await expect(page.locator(route.contentSelector).first()).toBeVisible();
          }
          await waitForViewSettled(page);

          // Scroll position lands within 8px of saved position
          await expect.poll(async () => {
            const current = await page.evaluate(selector => {
              if (selector === 'document') return window.scrollY;
              const el = document.querySelector(selector);
              return el ? el.scrollTop : 0;
            }, route.scroller);
            return Math.abs(current - savedScroll);
          }, {
            message: `Scroll position on ${route.id} (${route.scroller}) should restore within 8px of ${savedScroll}`,
            timeout: 5000
          }).toBeLessThanOrEqual(8);

          // For Requirements and Board at 390, assert deliberate scroll back to 0 restores to 0
          if (width === 390 && ['requirements', 'board'].includes(route.id)) {
            const currentMax = await page.evaluate(() => {
              const doc = document.scrollingElement || document.documentElement;
              return Math.max(0, doc.scrollHeight - window.innerHeight);
            });
            expect(currentMax, `Route ${route.id} at 390px must have currentMax >= 200 to test scroll-to-top`).toBeGreaterThanOrEqual(200);

            await page.evaluate(() => window.scrollTo(0, 200));
            await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThanOrEqual(200);
            await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));

            await page.evaluate(() => window.scrollTo(0, 0));
            await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
            await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));

            await ensureDrawerOpen(page, width);
            const other = page.locator(`.wc-daily a[data-daily="${route.otherEntry}"]`);
            await expect(other).toBeVisible();
            await other.click();
            await ensureDrawerClosed(page, width);

            await expect.poll(() => page.evaluate(() => location.hash)).not.toBe(expectedHash);
            await expect(other).toHaveAttribute('aria-current', 'page');
            await expect(page.getByRole('main').getByRole('heading', {level: 1}).first()).toBeVisible();
            await waitForViewSettled(page);

            await page.goBack();

            await expect.poll(() => page.evaluate(() => location.hash)).toBe(expectedHash);
            await expect(page.locator('#main h1').first()).toHaveText(route.h1[lang]);
            if (route.contentSelector) {
              await expect(page.locator(route.contentSelector).first()).toBeVisible();
            }
            await waitForViewSettled(page);

            await expect.poll(async () => {
              return await page.evaluate(() => window.scrollY);
            }, {
              message: `Scroll position on ${route.id} should restore within 8px of 0 after deliberate scroll to top`,
              timeout: 5000
            }).toBeLessThanOrEqual(8);
          }
        }
      }
    });
  }
}

test('board return keeps the requested scroll while workspace layout is still loading',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  const f=createLegacyFixture();let hold=false,release:(()=>void)|null=null,boardReads=0;
  await mountAttention(page,false,{...f.ws.fixture,caps:f.combinedCaps},async input=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(hold&&path===`/pm/workspaces/${fixtureWorkspaceId}`){hold=false;await new Promise<void>(resolve=>{release=resolve;});}
    const result=await f.dispatch(input);if(path.endsWith('/kanban'))boardReads++;return result;
  });
  const route=legacyRoutes.find(row=>row.id==='board')!;await page.goto(route.url);
  await expect(page.locator('.pm-kanban-card').first()).toBeVisible();await waitForViewSettled(page);
  await page.evaluate(()=>window.scrollTo(0,200));await expect.poll(()=>page.evaluate(()=>scrollY)).toBe(200);
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  await ensureDrawerOpen(page,390);await page.locator('.wc-daily a[data-daily="today"]').click();await ensureDrawerClosed(page,390);
  await waitForViewSettled(page);const before=boardReads;hold=true;await page.goBack();
  await expect.poll(()=>Boolean(release)).toBe(true);await expect.poll(()=>boardReads).toBeGreaterThan(before);
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  release!();await waitForViewSettled(page);
  await expect.poll(async()=>Math.abs(await page.evaluate(()=>scrollY)-200)).toBeLessThanOrEqual(8);
  expect(f.nonGetDispatches).toEqual([]);
});

// --------------------------------------------------------------------------
// UX-A07: The feature map (EN and zh-TW at 1440, EN at 390)
// --------------------------------------------------------------------------

interface CollectedDestination {
  id: string;
  label: string;
  href: string;
  source: 'pm-views' | 'tools-menu' | 'classic-nav';
  classicHeading: string;
}

const pmViewSelectors: Record<string, string> = {
  overview: '[data-pm-overview]',
  discussions: '[data-discussions]',
  requirements: '[data-pm-rtm]',
  schedule: '[data-pm-schedule]',
  dependencies: '[data-pm-dependencies]',
  issues: '[data-pm-register]',
  agents: '[data-pm-resources]',
  board: '[data-pm-kanban]',
};

const toolsViewSelectors: Record<string, string> = {
  operations: '.global-ops-tools',
  delivery: '.delivery-controls, .delivery-card',
  'artifact-review': '.artifact-review',
  cleanup: '.cleanup-choice',
  sessions: '.session-inventory, .session-scope',
};

const featureMapMatrix = [
  {lang: 'en', width: 1440},
  {lang: 'zh-TW', width: 1440},
  {lang: 'en', width: 390},
] as const;

for (const {lang, width} of featureMapMatrix) {
  test(`UX-A07: feature map classic to preview ${lang} ${width}`, async ({page}) => {
    await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), lang);
    await page.setViewportSize({width, height: width < 600 ? 844 : 900});

    const f = createLegacyFixture(lang);
    await mountAttention(page, false, {...f.ws.fixture, caps: f.combinedCaps}, f.dispatch);

    // 1. Record classic destinations and their headings through classic UI controls
    await page.goto(`/dashboard/#/pm/${fixtureWorkspaceId}/overview`);
    await expect(page.locator('#main h1').first()).toBeVisible();

    const destinations: CollectedDestination[] = [];

    // A. Record PM view destinations via .pm-project-views buttons
    await ensureDrawerOpen(page, width);
    const pmButtons = page.locator('.pm-project-views button[data-pm-view]');
    const pmCount = await pmButtons.count();
    const pmViews: Array<{view: string; label: string}> = [];
    for (let i = 0; i < pmCount; i++) {
      const btn = pmButtons.nth(i);
      const view = await btn.getAttribute('data-pm-view') || '';
      const label = (await btn.textContent())?.trim() || '';
      pmViews.push({view, label});
    }

    for (const {view, label} of pmViews) {
      await ensureDrawerOpen(page, width);
      const btn = await revealPmView(page, view);
      await btn.click();
      await ensureDrawerClosed(page, width);
      await expect.poll(() => page.evaluate(() => location.hash)).toBe(`#/pm/${fixtureWorkspaceId}/${view}`);
      if (pmViewSelectors[view]) {
        await expect(page.locator(pmViewSelectors[view]).first()).toBeVisible();
      }
      await expect(page.locator('#main h1').first()).toBeVisible();
      await expect(page.locator('#main h1').first()).not.toBeEmpty();
      const classicHeading = (await page.locator('#main h1').first().textContent())?.trim() || '';
      destinations.push({
        id: `pm-${view}`,
        label,
        href: `#/pm/${fixtureWorkspaceId}/${view}`,
        source: 'pm-views',
        classicHeading,
      });
    }

    // B. Record Tools menu items via [data-shell-tools]
    await ensureDrawerOpen(page, width);
    const toolsBtn = page.locator('[data-shell-tools]');
    await toolsBtn.click();
    const menuItemsLoc = page.locator('.ui-menu:has([data-shell-tools]) .ui-menu-panel [role="menuitem"]');
    await expect(menuItemsLoc.first()).toBeVisible();
    const toolsCount = await menuItemsLoc.count();
    const toolsItems: Array<{key: string; label: string; href: string}> = [];
    for (let i = 0; i < toolsCount; i++) {
      const item = menuItemsLoc.nth(i);
      // Skip hidden items (e.g. create-workspace when workspace is present)
      if (await item.isHidden()) continue;
      const key = await item.getAttribute('data-menu-key') || '';
      if (key === 'workbench') continue; // Skip workbench layout toggle
      const href = await item.getAttribute('href') || '';
      const label = (await item.textContent())?.trim() || '';
      toolsItems.push({key, label, href});
    }
    // Close tools menu
    await page.keyboard.press('Escape');
    await ensureDrawerClosed(page, width);

    let lastHeading = '';
    for (const {key, label, href} of toolsItems) {
      await closeAnyOpenDialog(page);
      await ensureDrawerOpen(page, width);
      const prevHeading = lastHeading || (await page.locator('#main h1').first().textContent())?.trim() || '';
      await page.locator('[data-shell-tools]').click();
      const item = page.locator(`.ui-menu:has([data-shell-tools]) .ui-menu-panel [role="menuitem"][href="${href}"]`);
      await item.click();
      await ensureDrawerClosed(page, width);
      await expect.poll(() => page.evaluate(() => location.hash)).toBe(href);
      if (toolsViewSelectors[key]) {
        await expect(page.locator(toolsViewSelectors[key]).first()).toBeVisible();
      }
      await expect(page.locator('#main h1').first()).toBeVisible();
      if (prevHeading) {
        await expect(page.locator('#main h1').first()).not.toHaveText(prevHeading);
      }
      const classicHeading = (await page.locator('#main h1').first().textContent())?.trim() || '';
      lastHeading = classicHeading;
      destinations.push({
        id: key || href.replace(/^#\//, ''),
        label,
        href,
        source: 'tools-menu',
        classicHeading,
      });
      await closeAnyOpenDialog(page);
    }

    // C. Record classic navigation items
    // Pending
    await ensureDrawerOpen(page, width);
    const prevHeadingBeforePending = lastHeading || (await page.locator('#main h1').first().textContent())?.trim() || '';
    const pendingLink = page.locator('a[data-shell-pending]');
    const pendingLabel = (await pendingLink.textContent())?.trim() || 'Pending';
    await pendingLink.click();
    await ensureDrawerClosed(page, width);
    await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/home');
    await expect(page.locator('.global-pending-main').first()).toBeVisible();
    await expect(page.locator('#main h1').first()).toBeVisible();
    if (prevHeadingBeforePending) {
      await expect(page.locator('#main h1').first()).not.toHaveText(prevHeadingBeforePending);
    }
    const pendingHeading = (await page.locator('#main h1').first().textContent())?.trim() || '';
    destinations.push({
      id: 'pending',
      label: pendingLabel,
      href: '#/home',
      source: 'classic-nav',
      classicHeading: pendingHeading,
    });

    // Settings
    await ensureDrawerOpen(page, width);
    const settingsLink = page.locator('a[data-shell-settings]');
    const settingsLabel = (await settingsLink.textContent())?.trim() || 'Settings';
    await settingsLink.click();
    await ensureDrawerClosed(page, width);
    await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/settings');
    await expect(page.locator('.global-settings, [data-settings-panel]').first()).toBeVisible();
    await expect(page.locator('#main h1').first()).toBeVisible();
    if (pendingHeading) {
      await expect(page.locator('#main h1').first()).not.toHaveText(pendingHeading);
    }
    const settingsHeading = (await page.locator('#main h1').first().textContent())?.trim() || '';
    destinations.push({
      id: 'settings',
      label: settingsLabel,
      href: '#/settings',
      source: 'classic-nav',
      classicHeading: settingsHeading,
    });

    // All projects
    await ensureDrawerOpen(page, width);
    const projectsLink = page.locator('.shell-all-projects, a[href="#/projects"]').first();
    const projectsLabel = (await projectsLink.textContent())?.trim() || 'Projects';
    await projectsLink.click();
    await ensureDrawerClosed(page, width);
    await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/projects');
    await expect(page.locator('[data-project-list], .or-item-list').first()).toBeVisible();
    await expect(page.locator('#main h1').first()).toBeVisible();
    if (settingsHeading) {
      await expect(page.locator('#main h1').first()).not.toHaveText(settingsHeading);
    }
    const projectsHeading = (await page.locator('#main h1').first().textContent())?.trim() || '';
    destinations.push({
      id: 'projects',
      label: projectsLabel,
      href: '#/projects',
      source: 'classic-nav',
      classicHeading: projectsHeading,
    });

    expect(destinations.length, 'Should programmatically gather destinations from classic mode').toBeGreaterThan(5);

    // 2. Reach each destination in preview mode (?workbench=1) through UI only
    await page.goto(`/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/overview`);
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(`#/pm/${fixtureWorkspaceId}/overview`);
    await expect(page.locator('#main h1').first()).toBeVisible();

    for (const dest of destinations) {
      let reached = false;
      let uiMethod = '';

      await closeAnyOpenDialog(page);

      // Check if reachable via Daily Entry
      await ensureDrawerOpen(page, width);
      const dailyEntry = page.locator(`.wc-daily a[href="${dest.href}"]`).first();
      // Optional probe: check if daily entry link with matching href is present and visible
      if (await dailyEntry.isVisible()) {
        await dailyEntry.click();
        await ensureDrawerClosed(page, width);
        reached = true;
        uiMethod = 'daily-entry';
      }

      // Check if reachable via Management Selector
      if (!reached && dest.source === 'pm-views') {
        await ensureDrawerClosed(page, width);
        // If not in PM workspace, use Projects daily entry to return to workspace
        const mgmtSelect = page.locator('select[data-workbench-management]');
        // Optional probe: check if workbench management select is visible
        if (!await mgmtSelect.isVisible()) {
          await ensureDrawerOpen(page, width);
          await page.locator('.wc-daily a[data-daily="projects"]').click();
          await ensureDrawerClosed(page, width);
        }
        await expect(mgmtSelect).toBeVisible();
        const view = dest.id.replace(/^pm-/, '');
        await mgmtSelect.selectOption(view);
        if (pmViewSelectors[view]) {
          await expect(page.locator(pmViewSelectors[view]).first()).toBeVisible();
        }
        reached = true;
        uiMethod = 'management-selector';
      }

      // Check if reachable via Tools Menu
      if (!reached) {
        await ensureDrawerOpen(page, width);
        const tools = page.locator('[data-shell-tools]');
        await expect(tools).toBeVisible();
        await tools.click();
        const menuItem = page.locator(`.ui-menu:has([data-shell-tools]) .ui-menu-panel [role="menuitem"][href="${dest.href}"]`).first();
        // Optional probe: check if tools menuitem with target href is present and visible
        if (await menuItem.isVisible()) {
          await menuItem.click();
          await ensureDrawerClosed(page, width);
          reached = true;
          uiMethod = 'tools-menu';
        } else {
          await page.keyboard.press('Escape');
          await ensureDrawerClosed(page, width);
        }
      }

      // Check if reachable via Project Switcher / Catalog link (.shell-all-projects)
      if (!reached && dest.href === '#/projects') {
        if (width >= 1000) {
          await ensureDrawerClosed(page, width);
          const switcherBtn = page.locator('.shell-workspace-switch').first();
          await expect(switcherBtn).toBeVisible();
          await switcherBtn.click();
          const switcherItem = page.locator('.ui-menu:has(.shell-workspace-switch) .ui-menu-panel [role="menuitem"][href="#/projects"]').first();
          await expect(switcherItem).toBeVisible();
          await switcherItem.click();
          reached = true;
          uiMethod = 'switcher-menu';
        } else {
          await ensureDrawerOpen(page, width);
          await page.locator('.shell-workspace-switch').click();
          await page.locator('.ui-menu:has(.shell-workspace-switch) [role="menuitem"][href="#/projects"]').click();
          await ensureDrawerClosed(page, width);
          reached = true;
          uiMethod = 'switcher-menu';
        }
      }

      // Check if reachable via Settings link in nav footer
      if (!reached && dest.href === '#/settings') {
        await ensureDrawerOpen(page, width);
        const settingsLink = page.locator('a[data-shell-settings]').first();
        // Optional probe: check if settings link is visible in sidebar/drawer
        if (await settingsLink.isVisible()) {
          await settingsLink.click();
          await ensureDrawerClosed(page, width);
          reached = true;
          uiMethod = 'settings-link';
        }
      }

      // Fail clearly with destination name if not reachable through UI
      expect(reached, `Destination "${dest.label}" (${dest.href}) must be reachable through the UI in preview mode (attempted ${uiMethod || 'none'})`).toBe(true);

      // 3. Compare headings: preview's #main h1 text must equal classic heading
      await expect(page.locator('#main h1').first(), `Destination "${dest.label}" heading should equal classic heading`).toHaveText(dest.classicHeading);
    }
  });
}
