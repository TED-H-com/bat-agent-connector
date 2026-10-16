import {test, expect, type Page} from '@playwright/test';
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

  let heldPromise: Promise<void> | null = null;
  let releaseHeld: (() => void) | null = null;
  let readStarted = false;
  let holdPath: string | null = null;
  let heldSerial = 0;
  let lastHeldToken: string | null = null;
  const unexpected: string[] = [];

  const holdNext = (path: string) => {
    holdPath = path;
    readStarted = false;
    lastHeldToken = null;
    heldPromise = new Promise(resolve => { releaseHeld = resolve; });
  };
  const release = () => {
    releaseHeld?.();
    releaseHeld = null;
    heldPromise = null;
    holdPath = null;
  };

  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture');
    const path = url.pathname;
    if (input.method !== 'GET') return ws.dispatch(input);

    // One token identifies this response after its body is parsed. A later read of
    // the same path must not be counted as the read this fixture held.
    let heldToken: string | null = null;
    if (holdPath && path === holdPath && heldPromise) {
      readStarted = true;
      holdPath = null;
      heldToken = String(++heldSerial);
      lastHeldToken = heldToken;
      await heldPromise;
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
    isReadStarted: () => readStarted,
    heldToken: () => lastHeldToken,
    boardPath: (id: string) => `/pm/workspaces/${id}/kanban`,
    selectedTitle: () => titles.b[1],
  };
}

async function installHeldBodyProbe(page: Page) {
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    (window as any).__heldBodyTokens = [];
    window.fetch = async function(...args) {
      const response = await originalFetch.apply(this, args as [RequestInfo, RequestInit?]);
      const held = response.headers.get('x-batc-held-read');
      const originalJson = response.json.bind(response);
      response.json = async function() {
        const data = await originalJson();
        if (held) (window as any).__heldBodyTokens.push(held);
        return data;
      };
      return response;
    };
  });
}

async function syncHeldBody(page: Page, token: string) {
  await page.waitForFunction(expected => ((window as any).__heldBodyTokens || []).includes(expected), token);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
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
