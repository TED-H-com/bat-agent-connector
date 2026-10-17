import {test, expect, type Page} from '@playwright/test';
import {STRINGS} from '../src/i18n.js';
import {workspaceFixture} from './workspace-fixture.ts';
import {mountAttention} from './attention-fixture.ts';

const workspaceA = 'ws_' + 'a'.repeat(32);
const workspaceB = 'ws_' + 'b'.repeat(32);
const workA = ['wi_' + 'a'.repeat(20), 'wi_' + 'c'.repeat(20)] as const;
const workB = ['wi_' + 'b'.repeat(20), 'wi_' + 'd'.repeat(20)] as const;
const selectedWork = workB[1];

function card(workspaceId: string, id: string, title: string, sequence: number) {
  return {
    work_item_id: id,
    binding: {
      workspace_id: workspaceId,
      work_item_id: id,
      sequence,
      binding_id: 'pmwb_' + String(sequence).repeat(32),
      project_id: 'prj_' + '2'.repeat(20),
      requirement_ids: [],
      work_version: 1,
    },
    work: {work_item_id: id, title, version: 1, project_id: 'prj_' + '2'.repeat(20), state: 'doing'},
    column: sequence === 1 ? 'in_progress' : 'review',
    completion: {approved: false, pending: false},
    flow: {wip: true, age_seconds: 3600, waiting_seconds: null, history: 'available_central_events', reopened: 0},
    quality: {dod: 'unassessed'},
    execution: {accepted_attempts: 0, tasks: [], operations: [], sessions: [], attempts: [], coverage: {network_checked: false, unavailable_sources: false, truncated: false}},
    latest_capture: null,
    capture_matches_binding: true,
    capture_matches_work_version: true,
  };
}

function createFixture(locale: 'en' | 'zh-TW') {
  const zh = locale === 'zh-TW';
  const ws = workspaceFixture(locale);
  const titles = zh
    ? {a: ['整理目錄', '核對來源'], b: ['會員登入', '密碼復原']}
    : {a: ['Sort the catalogue', 'Check the sources'], b: ['Member login', 'Password recovery']};
  const names = zh ? {a: '目錄整理', b: '會員服務'} : {a: 'Catalogue', b: 'Membership'};
  const workspaces = [
    {id: workspaceA, name: names.a, works: workA, titles: titles.a},
    {id: workspaceB, name: names.b, works: workB, titles: titles.b},
  ];
  const workspace = (row: typeof workspaces[number]) => ({
    workspace_id: row.id, server_id: 'retired-select', name: row.name, repositories: ['example/library'],
    version: 1, created_by: 'reader', created_at: 1791705600, updated_at: 1791705600,
    membership: {actor: 'reader', role: 'owner', controller_id: 'ctr_' + '4'.repeat(32)},
  });
  const cardsFor = (row: typeof workspaces[number]) => row.works.map((id, index) => card(row.id, id, row.titles[index], index + 1));
  const caps = () => {
    const value = ws.caps();
    value.features.pm_workspaces = {version: 1, kanban: true};
    return value;
  };

  type HeldFailure = 403 | 503 | 'abort';
  type HeldRead = {
    promise: Promise<void>;
    release: () => void;
    started: boolean;
    token: string | null;
    failure: HeldFailure | null;
  };
  const armed = new Map<string, HeldRead>();
  const inflight = new Map<string, HeldRead>();
  let heldSerial = 0;
  let watchedPath: string | null = null;
  const unexpected: string[] = [];

  const holdNext = (path: string, failure: HeldFailure | null = null) => {
    let releaseHold = () => {};
    const promise = new Promise<void>(resolve => { releaseHold = resolve; });
    armed.set(path, {promise, release: releaseHold, started: false, token: null, failure});
    watchedPath = path;
  };
  const release = (path?: string) => {
    const keys = path ? [path] : [...new Set([...armed.keys(), ...inflight.keys()])];
    for (const key of keys) {
      armed.get(key)?.release();
      inflight.get(key)?.release();
      armed.delete(key);
    }
  };
  const lookup = (path?: string) => {
    const key = path || watchedPath;
    if (!key) return null;
    return inflight.get(key) || armed.get(key) || null;
  };

  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture');
    const path = url.pathname;
    if (input.method !== 'GET') return ws.dispatch(input);

    // One token identifies this response after its body is parsed. A later read of
    // the same path must not be counted as the read this fixture held.
    let heldToken: string | null = null;
    const hold = armed.get(path);
    if (hold) {
      armed.delete(path);
      inflight.set(path, hold);
      hold.started = true;
      heldToken = String(++heldSerial);
      hold.token = heldToken;
      await hold.promise;
      inflight.delete(path);
      if (hold.failure === 'abort') return {abort: true};
      if (hold.failure) return {
        status: hold.failure,
        data: {error: {code: hold.failure === 403 ? 'FORBIDDEN' : 'UNAVAILABLE', message: 'Fixture refused the held read'}},
        headers: {'x-batc-held-read': heldToken},
      };
    }
    const finish = (result: any) => {
      if (!heldToken || !result || typeof result !== 'object') return result;
      return {...result, headers: {...(result.headers || {}), 'x-batc-held-read': heldToken}};
    };

    if (path === '/capabilities') return finish({status: 200, data: caps()});
    if (path === '/bootstrap') {
      const result = await ws.dispatch(input);
      result.data.capabilities = caps();
      return finish(result);
    }

    const owned = path.startsWith('/pm/workspaces/') ? workspaces.find(row => path === `/pm/workspaces/${row.id}` || path.startsWith(`/pm/workspaces/${row.id}/`)) : null;
    if (path === '/pm/workspaces') return finish({status: 200, data: {workspaces: workspaces.map(workspace)}});
    if (owned && path === `/pm/workspaces/${owned.id}`) return finish({status: 200, data: {workspace: workspace(owned)}});
    if (owned && path === `/pm/workspaces/${owned.id}/threads`) {
      return finish({status: 200, data: {version: 1, workspace_id: owned.id, threads: [], next_cursor: null, runtime: {available: false, reason: 'manual_discussion_only'}}});
    }
    if (owned && path === `/pm/workspaces/${owned.id}/projects`) {
      return finish({status: 200, data: {workspace_id: owned.id, bindings: [], next_cursor: null}});
    }
    if (owned && path === `/pm/workspaces/${owned.id}/kanban`) {
      const after = Number(url.searchParams.get('after') || 0);
      const cards = cardsFor(owned).filter(row => row.binding.sequence > after);
      return finish({status: 200, data: {
        version: 1, workspace_id: owned.id, project_id: url.searchParams.get('project_id'), observed_at: 1791705600,
        journal_cursor: 1, cards, next_cursor: null,
        wip: {scope: 'returned_page', count: cards.filter(row => row.flow.wip).length, unknown: 0, cards: cards.length, archived_excluded: 0},
        coverage: {}, capabilities: {read_only: true},
      }});
    }
    if (owned && path.startsWith(`/pm/workspaces/${owned.id}/kanban/`)) {
      const found = cardsFor(owned).find(row => path.endsWith(row.work_item_id));
      if (!found) return finish({status: 404, data: {error: {code: 'NOT_FOUND', message: 'Work unavailable'}}});
      return finish({status: 200, data: {version: 1, workspace_id: owned.id, observed_at: 1791705600, journal_cursor: 1, card: found}});
    }
    if (path.startsWith('/pm/')) {
      unexpected.push(input.method + ' ' + input.path);
      return finish({status: 404, data: {error: {code: 'NOT_FOUND', message: 'Fixture has no ' + path}}});
    }
    return finish(await ws.dispatch(input));
  };

  return {
    ws, caps, dispatch, holdNext, release, unexpected,
    isReadStarted: (path?: string) => Boolean(lookup(path)?.started),
    heldToken: (path?: string) => lookup(path)?.token ?? null,
    boardPath: (id: string) => `/pm/workspaces/${id}/kanban`,
    selectedTitle: () => titles.b[1],
  };
}

async function installHeldBodyProbe(page: Page) {
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    (window as any).__heldBodyTokens = [];
    (window as any).__heldFetchRejections = [];
    window.fetch = async function(...args) {
      const request = args[0];
      const url = typeof request === 'string' ? request : request instanceof URL ? request.href : request.url;
      try {
        const response = await originalFetch.apply(this, args as [RequestInfo, RequestInit?]);
        const held = response.headers.get('x-batc-held-read');
        const originalJson = response.json.bind(response);
        response.json = async function() {
          const data = await originalJson();
          if (held) (window as any).__heldBodyTokens.push(held);
          return data;
        };
        return response;
      } catch (error) {
        (window as any).__heldFetchRejections.push(String(url));
        throw error;
      }
    };
  });
}

async function syncHeldBody(page: Page, token: string) {
  await page.waitForFunction(expected => ((window as any).__heldBodyTokens || []).includes(expected), token);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
}

async function checkpoint(page: Page) {
  return page.evaluate(() => {
    const entry = Object.entries(localStorage).find(([key]) => key.startsWith('batc.sync.'));
    return entry ? JSON.parse(entry[1]) as {cursor: number, token: string} : null;
  });
}

function boardHash(workspaceId: string, workId?: string) {
  return `#/pm/${workspaceId}/board${workId ? `?select=${workId}` : ''}`;
}

// The project switcher follows a hash link. This does the same, to a Board that already has a selection.
async function openBoard(page: Page, workspaceId: string, workId?: string) {
  await page.evaluate(hash => { location.hash = hash; }, boardHash(workspaceId, workId));
}

async function expectSelected(page: Page, boardTitle: string, title: string, unavailable: string) {
  await expect(page.locator('#main h1').first()).toHaveText(boardTitle);
  await expect(page.locator('[data-work-inspector] h2')).toHaveText(title);
  await expect(page.locator('[data-work-inspector] [data-work-section=status]')).toBeVisible();
  await expect.poll(() => page.evaluate(() => location.hash)).toBe(boardHash(workspaceB, selectedWork));
  await expect(page.locator('#main')).not.toContainText(unavailable);
  await expect(page.locator('.pm-board-error, [data-api-error]')).toHaveCount(0);
}

test.describe('retired Board read keeps the other project selection', () => {
  for (const locale of ['en', 'zh-TW'] as const) for (const via of ['hash', 'back'] as const) {
    test(`${via === 'hash' ? 'project hash change' : 'Back'} (${locale})`, async ({page}) => {
      const boardTitle = locale === 'zh-TW' ? '工作看板' : 'Board';
      const unavailable = locale === 'zh-TW' ? '無法取得工作紀錄' : 'Work records unavailable';
      await page.addInitScript(language => Object.defineProperty(navigator, 'language', {value: language}), locale);
      await installHeldBodyProbe(page);
      await page.setViewportSize({width: 1440, height: 900});
      const fixture = createFixture(locale);
      await mountAttention(page, false, {...fixture.ws.fixture, caps: fixture.caps}, fixture.dispatch);

      if (via === 'back') {
        await page.goto(`/dashboard/?workbench=1${boardHash(workspaceB, selectedWork)}`);
        await expect(page.locator('#main h1').first()).toHaveText(boardTitle);
        await expect(page.locator('[data-work-inspector] h2')).toHaveText(fixture.selectedTitle());
        await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();
        fixture.holdNext(fixture.boardPath(workspaceA));
        await openBoard(page, workspaceA);
      } else {
        fixture.holdNext(fixture.boardPath(workspaceA));
        await page.goto(`/dashboard/?workbench=1${boardHash(workspaceA)}`);
      }

      // The discussion control is created after the workspace reads and in the same
      // turn that starts waiting on the held Board list. Seeing it means that read
      // is the one the mount is still waiting for.
      await expect.poll(() => fixture.isReadStarted()).toBe(true);
      await expect(page.locator('[data-discussion-new]')).toBeAttached();
      const held = fixture.heldToken();
      expect(held).toBeTruthy();

      if (via === 'back') await page.goBack();
      else await openBoard(page, workspaceB, selectedWork);

      await expect(page.locator('#main h1').first()).toHaveText(boardTitle);
      await expect(page.locator('[data-pm-kanban] .pm-kanban-card')).toHaveCount(2);
      await expect(page.locator('[data-work-inspector] h2')).toHaveText(fixture.selectedTitle());
      await expect(page.locator('[data-work-inspector] [data-work-section=status]')).toBeVisible();
      await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();
      await expect.poll(() => page.evaluate(() => location.hash)).toBe(boardHash(workspaceB, selectedWork));

      fixture.release();
      await syncHeldBody(page, held!);
      await expectSelected(page, boardTitle, fixture.selectedTitle(), unavailable);
      expect(fixture.unexpected).toEqual([]);
    });
  }
});

test.describe('retired Board HTTP failure leaves the destination batch usable', () => {
  for (const locale of ['en', 'zh-TW'] as const) for (const failure of [403, 503, 'abort'] as const) {
  test(`an old ${failure} cannot clear the selection after returning to its project (${locale})`, async ({page}) => {
    await page.addInitScript(language => Object.defineProperty(navigator, 'language', {value: language}), locale);
    await installHeldBodyProbe(page);
    const fixture = createFixture(locale);
    await mountAttention(page, false, {...fixture.ws.fixture, caps: fixture.caps}, fixture.dispatch);
    await page.goto(`/dashboard/?workbench=1${boardHash(workspaceB, selectedWork)}`);
    await expect(page.locator('[data-work-inspector] h2')).toHaveText(fixture.selectedTitle());

    fixture.holdNext(fixture.boardPath(workspaceA), failure);
    await openBoard(page, workspaceA, workA[0]);
    await expect.poll(() => fixture.isReadStarted(fixture.boardPath(workspaceA))).toBe(true);
    const oldToken = fixture.heldToken(fixture.boardPath(workspaceA));
    expect(oldToken).toBeTruthy();
    await openBoard(page, workspaceB, selectedWork);
    await expect(page.locator('[data-work-inspector] h2')).toHaveText(fixture.selectedTitle());
    await openBoard(page, workspaceA, workA[1]);
    const selectedTitle = locale === 'zh-TW' ? '核對來源' : 'Check the sources';
    await expect(page.locator('[data-work-inspector] h2')).toHaveText(selectedTitle);
    await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();

    fixture.release(fixture.boardPath(workspaceA));
    if (failure === 'abort') {
      await expect.poll(() => page.evaluate(path => (window as any).__heldFetchRejections.some((url: string) => new URL(url, location.href).pathname.endsWith(path)), fixture.boardPath(workspaceA))).toBe(true);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
    } else await syncHeldBody(page, oldToken!);
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(boardHash(workspaceA, workA[1]));
    await expect(page.locator('[data-work-inspector] h2')).toHaveText(selectedTitle);
    await expect(page.locator('.pm-board-error, [data-api-error]')).toHaveCount(0);
    expect(fixture.unexpected).toEqual([]);
  });
  }

  for (const failure of [403, 503, 'abort'] as const) {
    test(`the current Board still displays ${failure}`, async ({page}) => {
      const fixture = createFixture('en');
      await mountAttention(page, false, {...fixture.ws.fixture, caps: fixture.caps}, fixture.dispatch);
      await page.goto(`/dashboard/?workbench=1${boardHash(workspaceB, selectedWork)}`);
      await expect(page.locator('[data-work-inspector] h2')).toHaveText(fixture.selectedTitle());
      await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();
      fixture.holdNext(fixture.boardPath(workspaceB), failure);
      await page.locator('button[data-pm-refresh]').click();
      await expect.poll(() => fixture.isReadStarted(fixture.boardPath(workspaceB))).toBe(true);
      fixture.release(fixture.boardPath(workspaceB));
      await expect(page.locator('.pm-board-error')).toBeVisible();
      await expect(page.locator('.pm-board-error')).toContainText(
        failure === 403 ? STRINGS.en.forbidden_scope : STRINGS.en.wv_board_unavailable,
      );
      expect(fixture.unexpected).toEqual([]);
    });
  }

  test('a left Board HTTP 503 does not pause the destination event batch (en)', async ({page}) => {
    const copy = STRINGS.en;
    await page.addInitScript(language => Object.defineProperty(navigator, 'language', {value: language}), 'en');
    await installHeldBodyProbe(page);
    await page.setViewportSize({width: 1440, height: 900});
    const fixture = createFixture('en');
    await mountAttention(page, false, {...fixture.ws.fixture, caps: fixture.caps}, fixture.dispatch);

    // The event loop starts only after the first route settles. Land on the
    // destination first so its later batch is real, then hold the Board being left.
    await page.goto(`/dashboard/?workbench=1${boardHash(workspaceB, selectedWork)}`);
    await expect(page.locator('[data-work-inspector] h2')).toHaveText(fixture.selectedTitle());
    await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();
    await expect.poll(() => checkpoint(page)).toEqual({cursor: 0, token: 'w-0'});
    await expect(page.locator('#live')).toHaveAttribute('title', copy.desktop_polling);

    fixture.holdNext(fixture.boardPath(workspaceA), 503);
    await openBoard(page, workspaceA);
    await expect.poll(() => fixture.isReadStarted(fixture.boardPath(workspaceA))).toBe(true);
    await expect(page.locator('[data-discussion-new]')).toBeAttached();
    const failedToken = fixture.heldToken(fixture.boardPath(workspaceA));
    expect(failedToken).toBeTruthy();

    await openBoard(page, workspaceB, selectedWork);
    await expect(page.locator('#main h1').first()).toHaveText(copy.ui_board);
    await expect(page.locator('[data-pm-kanban] .pm-kanban-card')).toHaveCount(2);
    await expect(page.locator('[data-work-inspector] h2')).toHaveText(fixture.selectedTitle());
    await expect(page.locator('button[data-pm-refresh]')).toBeEnabled();
    await expect(page.locator('#live')).toHaveAttribute('title', copy.desktop_polling);

    fixture.holdNext(fixture.boardPath(workspaceB));
    fixture.ws.data.events.push({seq: 1, kind: 'work_item.updated', resource_type: 'work_item', resource_id: selectedWork});
    await expect.poll(() => fixture.isReadStarted(fixture.boardPath(workspaceB))).toBe(true);
    const batchToken = fixture.heldToken(fixture.boardPath(workspaceB));
    expect(batchToken).toBeTruthy();

    fixture.release(fixture.boardPath(workspaceA));
    await syncHeldBody(page, failedToken!);
    fixture.release(fixture.boardPath(workspaceB));
    await syncHeldBody(page, batchToken!);

    await expect.poll(async () => ({
      checkpoint: await checkpoint(page),
      title: await page.locator('#live').getAttribute('title'),
      refreshEnabled: await page.locator('button[data-pm-refresh]').isEnabled(),
    })).toEqual({checkpoint: {cursor: 1, token: 'w-1'}, title: copy.desktop_polling, refreshEnabled: true});
    await expectSelected(page, copy.ui_board, fixture.selectedTitle(), copy.wv_board_unavailable);
    expect(fixture.unexpected).toEqual([]);
  });
});
