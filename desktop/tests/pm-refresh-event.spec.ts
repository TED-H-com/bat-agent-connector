import {test, expect, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';

async function countCompletedBodies(page: Page, path: string): Promise<number> {
  return await page.evaluate(targetPath => {
    const urls = ((window as any).__bodyCompletedUrls || []) as string[];
    return urls.filter(u => u.split('?')[0].endsWith(targetPath)).length;
  }, path);
}

async function syncHeldBodyCompletion(page: Page, holdPath: string, beforeCount: number): Promise<void> {
  await page.waitForFunction(
    ({targetPath, target}) => {
      const urls = ((window as any).__bodyCompletedUrls || []) as string[];
      return urls.filter(u => u.split('?')[0].endsWith(targetPath)).length === target;
    },
    {targetPath: holdPath, target: beforeCount + 1}
  );
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
}

interface TestFixtureOptions {
  locale: 'en' | 'zh-TW';
  moreThreads?: boolean;
}

function createRefreshFixture({locale, moreThreads = false}: TestFixtureOptions) {
  const f = demoFixture({locale, managedSession: true});
  const {ids} = f;
  const workspaceId = ids.workspaceId;
  let eventCursor = 0;
  let holdNextWorkspaces = false;
  let heldWorkspaces = false;
  let releaseWorkspaces = () => {};
  let waitingWorkspaces: Promise<void> = Promise.resolve();
  let workspacesReadCount = 0;

  const holdWorkspaces = () => {
    heldWorkspaces = false;
    holdNextWorkspaces = true;
    waitingWorkspaces = new Promise<void>(resolve => {
      releaseWorkspaces = resolve;
    });
  };

  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture');
    const path = url.pathname;
    if (input.method === 'GET' && path === '/pm/workspaces') {
      workspacesReadCount++;
      if (holdNextWorkspaces) {
        holdNextWorkspaces = false;
        heldWorkspaces = true;
        await waitingWorkspaces;
      }
      return f.dispatch(input);
    }

    if (path === '/events' && eventCursor && Number(url.searchParams.get('after') || 0) < eventCursor) {
      return {
        status: 200,
        data: {
          events: [
            {
              seq: eventCursor,
              resource_type: 'pm_workspace',
              resource_id: workspaceId,
              kind: 'pm.workspace.updated',
            },
          ],
          next_cursor: eventCursor,
          head_cursor: eventCursor,
          has_more: false,
          sync: {checkpoint: {cursor: eventCursor, token: `demo-${eventCursor}`}},
        },
      };
    }

    if (moreThreads && path.startsWith(`/pm/workspaces/${workspaceId}/threads`)) {
      const response = await f.dispatch(input);
      const after = url.searchParams.get('after');
      if (!after) {
        return {
          ...response,
          data: {
            ...response.data,
            next_cursor: 'cursor-more-threads',
          },
        };
      }
      return response;
    }

    return f.dispatch(input);
  };

  return {
    ids,
    workspaceId,
    dispatch,
    holdWorkspaces,
    releaseWorkspaces: () => releaseWorkspaces(),
    isHeldWorkspaces: () => heldWorkspaces,
    getWorkspacesReadCount: () => workspacesReadCount,
    emitEvent: () => {
      eventCursor++;
    },
  };
}

async function setupPage(page: Page, locale: string, width: number, height: number) {
  await page.setViewportSize({width, height});
  await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), locale);
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    (window as any).__bodyCompletedUrls = [];
    window.fetch = async function(...args) {
      const response = await originalFetch.apply(this, args);
      const url = typeof args[0] === 'string' ? args[0] : (args[0] && (args[0] as any).url) || '';
      const originalJson = response.json.bind(response);
      response.json = async function() {
        const data = await originalJson();
        (window as any).__bodyCompletedUrls.push(url);
        return data;
      };
      return response;
    };
  });
}

test('refresh stays enabled when a PM event arrives during a board refresh (en 1440)', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en', viewport: {width: 1440, height: 900}});
  try {
    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));

    await setupPage(page, 'en', 1440, 900);
    const fixture = createRefreshFixture({locale: 'en'});
    await mountConversation(page, false, fixture.dispatch);

    // 1. Open Board at en 1440 and wait until the first load has settled (Refresh enabled).
    await page.goto(`/dashboard/#/pm/${fixture.workspaceId}/board`);
    const refresh = page.locator('[data-pm-refresh]');
    await expect(refresh).toBeEnabled();
    await expect(page.locator('#main h1')).toHaveText('Board');
    await expect(page.locator('[data-pm-kanban]')).toBeVisible();

    // 2. Hold the next GET /pm/workspaces. Press Refresh. Refresh is disabled.
    fixture.holdWorkspaces();
    await refresh.click();
    await expect(refresh).toBeDisabled();
    await expect.poll(fixture.isHeldWorkspaces).toBe(true);

    // 3. Emit one pm_* event for this workspace. Wait until authority read completes.
    const beforeAuthority = await countCompletedBodies(page, `/pm/workspaces/${fixture.workspaceId}`);
    fixture.emitEvent();
    await syncHeldBodyCompletion(page, `/pm/workspaces/${fixture.workspaceId}`, beforeAuthority);

    // 4. Release the held read, then sync on its completed body plus one frame.
    const beforeWorkspaces = await countCompletedBodies(page, '/pm/workspaces');
    fixture.releaseWorkspaces();
    await syncHeldBodyCompletion(page, '/pm/workspaces', beforeWorkspaces);

    // Assertions:
    // - Refresh is enabled;
    // - the workspace header and the board are shown, with no error in the status area;
    // - pressing Refresh again makes exactly one new GET /pm/workspaces, and Refresh is enabled once it settles.
    await expect(refresh).toBeEnabled();
    await expect(page.locator('#main h1')).toHaveText('Board');
    await expect(page.locator('[data-pm-kanban]')).toBeVisible();
    await expect(page.locator('.pm-status .error, .pm-status [data-api-error]')).toHaveCount(0);

    const countBefore = fixture.getWorkspacesReadCount();
    await refresh.click();
    await expect(refresh).toBeEnabled();
    expect(fixture.getWorkspacesReadCount() - countBefore).toBe(1);

    expect(errors).toEqual([]);
  } finally {
    await context.close();
  }
});

test('refresh stays enabled when a PM event arrives during more threads on discussions (en 1440)', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'en', viewport: {width: 1440, height: 900}});
  try {
    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));

    await setupPage(page, 'en', 1440, 900);
    const fixture = createRefreshFixture({locale: 'en', moreThreads: true});
    await mountConversation(page, false, fixture.dispatch);

    // 1. Open Discussions at en 1440 and wait until the first load has settled (Refresh enabled).
    await page.goto(`/dashboard/#/pm/${fixture.workspaceId}/discussions`);
    const refresh = page.locator('[data-pm-refresh]');
    await expect(refresh).toBeEnabled();
    await expect(page.locator('#main h1')).toHaveText('Discussions');
    const moreThreads = page.locator('[data-discussion-more]');
    await expect(moreThreads).toBeVisible();

    // 2. Hold the next GET /pm/workspaces. Press More threads. Refresh is disabled.
    fixture.holdWorkspaces();
    await moreThreads.click();
    await expect(refresh).toBeDisabled();
    await expect.poll(fixture.isHeldWorkspaces).toBe(true);

    // 3. Emit one pm_* event for this workspace. Wait until authority read completes.
    const beforeAuthority = await countCompletedBodies(page, `/pm/workspaces/${fixture.workspaceId}`);
    fixture.emitEvent();
    await syncHeldBodyCompletion(page, `/pm/workspaces/${fixture.workspaceId}`, beforeAuthority);

    // 4. Release the held read, then sync on its completed body plus one frame.
    const beforeWorkspaces = await countCompletedBodies(page, '/pm/workspaces');
    fixture.releaseWorkspaces();
    await syncHeldBodyCompletion(page, '/pm/workspaces', beforeWorkspaces);

    // Assertions:
    // - Refresh is enabled;
    // - the workspace header and discussions are shown, with no error in the status area;
    // - pressing Refresh again makes exactly one new GET /pm/workspaces, and Refresh is enabled once it settles.
    await expect(refresh).toBeEnabled();
    await expect(page.locator('#main h1')).toHaveText('Discussions');
    await expect(page.locator('[data-discussions]')).toBeVisible();
    await expect(page.locator('.pm-status .error, .pm-status [data-api-error]')).toHaveCount(0);

    const countBefore = fixture.getWorkspacesReadCount();
    await refresh.click();
    await expect(refresh).toBeEnabled();
    expect(fixture.getWorkspacesReadCount() - countBefore).toBe(1);

    expect(errors).toEqual([]);
  } finally {
    await context.close();
  }
});

test('refresh stays enabled when a PM event arrives during a refresh (zh-TW 390)', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'zh-TW', viewport: {width: 390, height: 844}});
  try {
    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));

    await setupPage(page, 'zh-TW', 390, 844);
    const fixture = createRefreshFixture({locale: 'zh-TW'});
    await mountConversation(page, false, fixture.dispatch);

    // 1. Open Board at zh-TW 390 and wait until the first load has settled (Refresh enabled).
    await page.goto(`/dashboard/#/pm/${fixture.workspaceId}/board`);
    const refresh = page.locator('[data-pm-refresh]');
    await expect(refresh).toBeEnabled();
    await expect(page.locator('#main h1')).toHaveText('工作看板');
    await expect(page.locator('[data-pm-kanban]')).toBeVisible();

    // 2. Hold the next GET /pm/workspaces. Press Refresh. Refresh is disabled.
    fixture.holdWorkspaces();
    await refresh.click();
    await expect(refresh).toBeDisabled();
    await expect.poll(fixture.isHeldWorkspaces).toBe(true);

    // 3. Emit one pm_* event for this workspace. Wait until authority read completes.
    const beforeAuthority = await countCompletedBodies(page, `/pm/workspaces/${fixture.workspaceId}`);
    fixture.emitEvent();
    await syncHeldBodyCompletion(page, `/pm/workspaces/${fixture.workspaceId}`, beforeAuthority);

    // 4. Release the held read, then sync on its completed body plus one frame.
    const beforeWorkspaces = await countCompletedBodies(page, '/pm/workspaces');
    fixture.releaseWorkspaces();
    await syncHeldBodyCompletion(page, '/pm/workspaces', beforeWorkspaces);

    // Assertions:
    // - Refresh is enabled;
    // - the workspace header and the board are shown, with no error in the status area;
    // - pressing Refresh again makes exactly one new GET /pm/workspaces, and Refresh is enabled once it settles.
    await expect(refresh).toBeEnabled();
    await expect(page.locator('#main h1')).toHaveText('工作看板');
    await expect(page.locator('[data-pm-kanban]')).toBeVisible();
    await expect(page.locator('.pm-status .error, .pm-status [data-api-error]')).toHaveCount(0);

    const countBefore = fixture.getWorkspacesReadCount();
    await refresh.click();
    await expect(refresh).toBeEnabled();
    expect(fixture.getWorkspacesReadCount() - countBefore).toBe(1);

    expect(errors).toEqual([]);
  } finally {
    await context.close();
  }
});
