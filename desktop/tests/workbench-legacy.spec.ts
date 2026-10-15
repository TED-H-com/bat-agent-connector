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
  const inspectorBack = page.locator('.ui-inspector-back, button[aria-label="Back"], button:has-text("Back")').first();
  if (await inspectorBack.isVisible().catch(() => false)) {
    await inspectorBack.evaluate((el: HTMLElement) => el.click()).catch(() => {});
    await page.waitForTimeout(50);
  }
  const dialog = page.locator('dialog[open]');
  if (await dialog.isVisible().catch(() => false)) {
    await page.keyboard.press('Escape');
    await page.waitForTimeout(50);
  }
}

async function ensureDrawerOpen(page: Page, width: number) {
  if (width < 1000) {
    await closeAnyOpenDialog(page);
    const toggle = page.locator('#workspace-menu');
    if (await toggle.isVisible().catch(() => false)) {
      const expanded = await toggle.getAttribute('aria-expanded');
      if (expanded !== 'true') {
        await toggle.click();
        await page.waitForTimeout(100);
      }
    }
  }
}

async function ensureDrawerClosed(page: Page, width: number) {
  if (width < 1000) {
    const isModal = await page.locator('aside#workspace-sidebar[aria-modal="true"]').isVisible().catch(() => false);
    if (isModal) {
      await page.keyboard.press('Escape');
      await page.waitForTimeout(50);
    }
  }
}

// --------------------------------------------------------------------------
// UX-A05: Legacy deep links (preview mode, EN and zh-TW, 1440 and 390)
// --------------------------------------------------------------------------

const legacyRoutes = [
  {
    id: 'session',
    url: `/dashboard/?workbench=1${workspaceRoute}`,
    dailyEntry: 'work',
    targets: ['Improve empty search results', '改善搜尋的空結果提示', 'Search results', '改善文件搜尋', '搜尋', 'search-results', workspaceOperation],
    otherEntry: 'today',
  },
  {
    id: 'operation',
    url: `/dashboard/?workbench=1#/op/${workspaceOperation}`,
    dailyEntry: '', // unmapped
    targets: [workspaceOperation, 'repository.continue'],
    otherEntry: 'today',
  },
  {
    id: 'overview',
    url: `/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/overview`,
    dailyEntry: 'projects',
    targets: ['Library refresh', 'Overview', '專案總覽', fixtureWorkspaceId, 'Recovery progress'],
    otherEntry: 'work',
  },
  {
    id: 'discussions',
    url: `/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/discussions`,
    dailyEntry: 'projects',
    targets: ['A clearer first visit', fixtureWorkspaceId, 'Discussions', '討論'],
    otherEntry: 'work',
  },
  {
    id: 'thread',
    url: `/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/discussions/${fixtureThreadId}`,
    dailyEntry: 'projects',
    targets: ['A clearer first visit', fixtureThreadId],
    otherEntry: 'work',
  },
  {
    id: 'requirements',
    url: `/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/requirements`,
    dailyEntry: 'projects',
    targets: ['Requirements', '需求', 'membership.login', 'Members can sign in', '會員可以登入'],
    otherEntry: 'work',
  },
  {
    id: 'board',
    url: `/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/board`,
    dailyEntry: 'work',
    targets: ['Member login', '會員登入', 'Board', '工作看板', fixtureWorkspaceId],
    otherEntry: 'projects',
  },
  {
    id: 'delivery',
    url: `/dashboard/?workbench=1#/delivery`,
    dailyEntry: '', // unmapped
    targets: ['Delivery', '成果與 GitHub'],
    otherEntry: 'today',
  },
  {
    id: 'artifacts',
    url: `/dashboard/?workbench=1#/artifact-review`,
    dailyEntry: '', // unmapped
    targets: ['Artifacts and review', '附件成果與審閱', 'Artifacts', '附件成果'],
    otherEntry: 'today',
  },
];

for (const lang of ['en', 'zh-TW']) {
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
        await page.waitForTimeout(150);

        // 1. Right entry:
        if (route.dailyEntry) {
          const entry = page.locator(`.wc-daily a[data-daily="${route.dailyEntry}"]`);
          await expect(entry, `Daily entry "${route.dailyEntry}" should be aria-current=page`).toHaveAttribute('aria-current', 'page');
        } else {
          // If the mapping gives no entry for a route, assert that the route still renders
          const allDaily = page.locator('.wc-daily a');
          const count = await allDaily.count();
          for (let i = 0; i < count; i++) {
            await expect(allDaily.nth(i)).toHaveAttribute('aria-current', 'false');
          }
          await expect(page.locator('#main')).toBeVisible();
        }

        async function checkTargetVisible(targets: string[]) {
          for (const target of targets) {
            const locMain = page.locator('#main').getByText(target, {exact: false});
            const countMain = await locMain.count();
            for (let i = 0; i < countMain; i++) {
              if (await locMain.nth(i).isVisible().catch(() => false)) return true;
            }
            const locPage = page.getByText(target, {exact: false});
            const countPage = await locPage.count();
            for (let i = 0; i < countPage; i++) {
              if (await locPage.nth(i).isVisible().catch(() => false)) return true;
            }
          }
          return false;
        }

        // 2. Same object: target object shown with its title or ID
        await expect(async () => {
          const foundTarget = await checkTargetVisible(route.targets);
          expect(foundTarget, `Route ${route.id} should show target object in [${route.targets.join(', ')}]`).toBe(true);
        }).toPass({timeout: 4000});

        // 3. Nothing dispatched: no non-GET requests
        expect(f.nonGetDispatches.length, `No non-GET requests dispatched on route ${route.id}`).toBe(startNonGetCount);

        // 4. Back works:
        // Scroll the page partway
        const beforeScroll = await page.evaluate(() => {
          const conv = document.querySelector('.conversation-scroll');
          if (conv && conv.scrollHeight > conv.clientHeight) {
            const max = conv.scrollHeight - conv.clientHeight;
            // Scroll safely above the 48px atBottom threshold
            const target = Math.max(10, Math.min(100, max - 60));
            conv.scrollTop = target;
            return {type: 'conv', val: conv.scrollTop, max};
          }
          const trans = document.querySelector('.pd-transcript');
          if (trans && trans.scrollHeight > trans.clientHeight) {
            const max = trans.scrollHeight - trans.clientHeight;
            trans.scrollTop = Math.min(50, Math.floor(max / 2));
            return {type: 'trans', val: trans.scrollTop, max};
          }
          const list = document.querySelector('.pm-workspace-list');
          if (list && list.scrollHeight > list.clientHeight) {
            const max = list.scrollHeight - list.clientHeight;
            list.scrollTop = Math.min(50, Math.floor(max / 2));
            return {type: 'list', val: list.scrollTop, max};
          }
          const doc = document.scrollingElement || document.documentElement;
          const max = doc.scrollHeight - doc.clientHeight;
          if (max > 0) {
            window.scrollTo(0, Math.min(50, Math.floor(max / 2)));
            return {type: 'doc', val: window.scrollY, max};
          }
          return {type: 'none', val: 0, max: 0};
        });

        // Open a different entry
        await ensureDrawerOpen(page, width);
        const otherEntry = page.locator(`.wc-daily a[data-daily="${route.otherEntry}"]`);
        await otherEntry.click();
        await ensureDrawerClosed(page, width);
        await page.waitForTimeout(150);

        // Press Back
        await page.goBack();
        await page.waitForTimeout(250);

        // Returns to the same route and object
        await expect(async () => {
          const returnedTarget = await checkTargetVisible(route.targets);
          expect(returnedTarget, `After goBack, route ${route.id} should return to target object`).toBe(true);
        }).toPass({timeout: 4000});

        // Scroll position is within 8px of where it was
        const afterScroll = await page.evaluate(type => {
          if (type === 'conv') {
            const el = document.querySelector('.conversation-scroll');
            return el ? el.scrollTop : 0;
          }
          if (type === 'trans') {
            const el = document.querySelector('.pd-transcript');
            return el ? el.scrollTop : 0;
          }
          if (type === 'list') {
            const el = document.querySelector('.pm-workspace-list');
            return el ? el.scrollTop : 0;
          }
          return window.scrollY;
        }, beforeScroll.type);

        const delta = Math.abs(afterScroll - beforeScroll.val);
        expect(delta, `Scroll delta on ${route.id} (${beforeScroll.type}) should be within 8px (was ${delta}px)`).toBeLessThanOrEqual(8);
      }
    });
  }
}

// --------------------------------------------------------------------------
// UX-A07: The feature map (EN and zh-TW at 1440, EN at 390)
// --------------------------------------------------------------------------

const featureMapMatrix = [
  {lang: 'en', width: 1440},
  {lang: 'zh-TW', width: 1440},
  {lang: 'en', width: 390},
];

for (const {lang, width} of featureMapMatrix) {
  test(`UX-A07: feature map classic to preview ${lang} ${width}`, async ({page}) => {
    await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), lang);
    await page.setViewportSize({width, height: width < 600 ? 844 : 900});

    const f = createLegacyFixture(lang);
    await mountAttention(page, false, {...f.ws.fixture, caps: f.combinedCaps}, f.dispatch);

    // 1. Collect destinations programmatically from classic mode DOM (no ?workbench=1)
    await page.goto(`/dashboard/#/pm/${fixtureWorkspaceId}/overview`);
    await page.waitForTimeout(200);

    // Open mobile drawer if on mobile viewport to reveal navigation
    await ensureDrawerOpen(page, width);

    // Classic navigation items
    const navItems = await page.evaluate(() => {
      const items: Array<{id: string, label: string, href: string, source: string}> = [];
      const pending = document.querySelector('a[data-shell-pending]');
      if (pending) items.push({id: 'pending', label: pending.textContent?.trim() || 'Pending', href: pending.getAttribute('href') || '', source: 'classic-nav'});
      const settings = document.querySelector('a[data-shell-settings]');
      if (settings) items.push({id: 'settings', label: settings.textContent?.trim() || 'Settings', href: settings.getAttribute('href') || '', source: 'classic-nav'});
      for (const a of Array.from(document.querySelectorAll('.workspace-nav-footer > a, .workspace-tree a, .workspace-session-tree a, .shell-all-projects, [data-project-create]'))) {
        const href = a.getAttribute('href');
        const label = a.textContent?.trim() || '';
        if (href && !items.some(i => i.href === href)) {
          items.push({id: href.replace(/^#\//, '').replace(/\//g, '-'), label, href, source: 'classic-nav'});
        }
      }
      return items;
    });

    // Tools menu items
    const toolsButton = page.locator('[data-shell-tools]');
    await toolsButton.click();
    await page.waitForTimeout(100);
    const toolsItems = await page.evaluate(() => {
      const btn = document.querySelector('[data-shell-tools]');
      const panelId = btn?.getAttribute('aria-controls');
      const panel = panelId ? document.getElementById(panelId) : null;
      const items: Array<{id: string, label: string, href: string, source: string}> = [];
      if (panel) {
        for (const a of Array.from(panel.querySelectorAll('[role="menuitem"]'))) {
          const href = a.getAttribute('href') || '';
          const key = a.getAttribute('data-menu-key') || '';
          if (key === 'workbench') continue; // Skip preview layout switch
          items.push({
            id: key || href.replace(/^#\//, '').replace(/\//g, '-'),
            label: a.textContent?.trim() || '',
            href,
            source: 'tools-menu'
          });
        }
      }
      return items;
    });
    await toolsButton.click(); // Close tools menu
    await ensureDrawerClosed(page, width);

    // Project management selector options / buttons
    const pmViews = await page.evaluate(wsId => {
      const items: Array<{id: string, label: string, href: string, source: string}> = [];
      for (const btn of Array.from(document.querySelectorAll('.pm-project-views button[data-pm-view]'))) {
        const view = btn.getAttribute('data-pm-view') || '';
        items.push({
          id: `pm-${view}`,
          label: btn.textContent?.trim() || '',
          href: `#/pm/${wsId}/${view}`,
          source: 'pm-views'
        });
      }
      return items;
    }, fixtureWorkspaceId);

    const allDestinations = [...navItems, ...toolsItems, ...pmViews];
    expect(allDestinations.length, 'Should programmatically gather destinations from classic mode').toBeGreaterThan(5);

    // 2. Check each destination in preview mode (?workbench=1)
    await page.goto(`/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/overview`);
    await page.waitForTimeout(200);

    for (const dest of allDestinations) {
      let reached = false;
      let method = '';

      // Close any modal dialog from previous steps (e.g. New Workspace dialog)
      await closeAnyOpenDialog(page);

      // Check if reachable via daily entry
      await ensureDrawerOpen(page, width);
      const dailyEntry = page.locator(`.wc-daily a[href="${dest.href}"]`).first();
      if (await dailyEntry.count() > 0 && await dailyEntry.isVisible().catch(() => false)) {
        await dailyEntry.evaluate((el: HTMLElement) => el.click());
        await ensureDrawerClosed(page, width);
        reached = true;
        method = 'daily';
      }

      // Check if reachable via management selector
      if (!reached && dest.source === 'pm-views') {
        await ensureDrawerClosed(page, width);
        const view = dest.id.replace(/^pm-/, '');
        const select = page.locator('select[data-workbench-management]');
        if (await select.count() > 0 && await select.isVisible().catch(() => false)) {
          const option = select.locator(`option[value="${view}"]`);
          if (await option.count() > 0) {
            await select.selectOption(view);
            reached = true;
            method = 'management-selector';
          }
        }
      }

      // Check if reachable via Tools menu
      if (!reached) {
        await ensureDrawerOpen(page, width);
        const tools = page.locator('[data-shell-tools]');
        if (await tools.isVisible().catch(() => false)) {
          await tools.evaluate((el: HTMLElement) => el.click());
          await page.waitForTimeout(100);
          const menuItem = page.locator(`.ui-menu-panel [role="menuitem"][href="${dest.href}"]`).first();
          if (await menuItem.count() > 0 && await menuItem.isVisible().catch(() => false)) {
            await menuItem.evaluate((el: HTMLElement) => el.click());
            await ensureDrawerClosed(page, width);
            reached = true;
            method = 'tools-menu';
          } else {
            await page.keyboard.press('Escape'); // Close menu
            await page.waitForTimeout(50);
          }
        }
        await ensureDrawerClosed(page, width);
      }

      // Check if reachable via legacy direct link
      if (!reached && dest.href) {
        await page.goto(`/dashboard/?workbench=1${dest.href}`);
        reached = true;
        method = 'legacy-link';
      }

      // Fail clearly with destination name if missing
      expect(reached, `Destination "${dest.label}" (${dest.href}) must be reachable in preview mode`).toBe(true);

      // Opening it must show that view's heading
      const heading = page.locator('#main h1, .ui-page-header h1, .ui-page-heading h1').first();
      await expect(heading, `Opening destination "${dest.label}" (${dest.href}) must show view heading`).toBeVisible();
      const headingText = await heading.textContent();
      expect(headingText?.trim().length, `Destination "${dest.label}" heading text must not be empty`).toBeGreaterThan(0);
    }
  });
}
