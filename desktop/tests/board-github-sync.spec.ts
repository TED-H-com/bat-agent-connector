import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {readFileSync} from 'node:fs';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';
import {workStartFixture, workspaceId, workId} from './work-start-fixture.ts';

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) test(`GitHub empty board import and repeat sync ${locale} ${native ? 'IPC' : 'HTTP'}`, async ({page}, testInfo) => {
  await page.setViewportSize({width: native ? 390 : 1280, height: 900});
  const fixture = demoFixture({locale, managedSession: true}), {ids} = fixture;
  const errors: string[] = [], writes: any[] = [], operations = new Map<string, any>();
  page.on('pageerror', error => errors.push(error.message));
  let imported = false, revision = 0;
  const setting = () => ({project_id: ids.projectId, name: 'Example board', repository: 'example/board', workspace_id: ids.workspaceId,
    revision, enabled: true, include_closed: false, last_error: null, status: imported ? 'synced' : 'not_synced', last_sync: imported ? 1700000000 : null, authenticated: false});
  const dispatch = async (input: any) => {
    if (input.method === 'POST') {
      writes.push(input);
      expect(input.body.action).toBe('pm.github.import'); expect(input.idempotency_key).toBeTruthy();
      expect(input.body.target).toEqual({workspace_id: ids.workspaceId});
      expect(input.body.preconditions).toEqual({expected_revision: revision});
      expect(input.body.params.repository).toBe('example/board'); expect(input.body.params.include_closed).toBe(false);
      imported = true; revision++;
      const id = 'op_' + revision.toString(16).padStart(32, '0');
      const operation = {...input.body, operation_id: id, actor: ids.actor, idempotency_key: input.idempotency_key, status: 'succeeded',
        result: {project_id: ids.projectId, github_sync: setting(), created: revision === 1 ? 1 : 0, adopted: 0, updated: 0}};
      operations.set(id, operation);
      return {status: 202, data: {operation}};
    }
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path.startsWith('/operations/')) return {status: 200, data: {operation: operations.get(path.split('/').at(-1)!)}};
    if (path.endsWith('/projects') && path.startsWith('/pm/')) return {status: 200, data: {version: 1, workspace_id: ids.workspaceId, bindings: [], next_cursor: null,
      github_sync: {projects: [setting()], suggested_repository: 'example/board'}}};
    const result: any = await fixture.dispatch(input);
    const caps = path === '/capabilities' ? result.data : path === '/bootstrap' ? result.data.capabilities : null;
    if (caps) {
      caps.features.board_github_sync = {version: 1}; caps.scopes.push('manage');
      caps.actions.push(...['pm.github.import', 'project.github.configure'].map(action => ({action, allowed: true, scope: 'manage'})));
    }
    if (path === `/pm/workspaces/${ids.workspaceId}`) result.data.workspace.membership.role = 'owner';
    if (path.endsWith('/kanban')) {
      result.data.cards = imported ? [result.data.cards.find((card: any) => card.column === 'backlog')] : [];
      if (imported) {result.data.cards[0].binding.requirement_ids = []; result.data.cards[0].work.title = 'Imported GitHub issue'; result.data.cards[0].work.external = {repository: 'example/board', number: 1, kind: 'issue', url: 'https://github.com/example/board/issues/1'};}
      result.data.next_cursor = null; result.data.wip = {count: 0, unknown: 0, cards: result.data.cards.length, scope: 'returned_page'};
    }
    return result;
  };
  await page.addInitScript(value => Object.defineProperty(navigator, 'language', {value}), locale);
  await mountConversation(page, native, dispatch);
  await page.goto(`/dashboard/#/pm/${ids.workspaceId}/board`);
  const panel = page.locator('[data-board-github]');
  await expect(panel).toBeVisible();
  await expect(panel.locator('[data-github-sync-status]')).toContainText(locale === 'en' ? 'has not been imported' : '尚未匯入');
  await expect(panel.getByRole('checkbox', {name: locale === 'en' ? 'Keep in sync' : '持續同步'})).toBeChecked();
  await expect(panel.getByRole('checkbox', {name: locale === 'en' ? 'Include closed history' : '包含已關閉的歷史'})).not.toBeChecked();
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(0);
  const shots = process.env.BATC_WORK_VIEW_SCREENSHOTS || testInfo.outputPath('board-github');
  await mkdir(shots, {recursive: true});
  await page.screenshot({path: `${shots}/empty-${locale}-${native ? 'ipc' : 'http'}.png`, fullPage: true});
  await panel.getByRole('button', {name: locale === 'en' ? 'Import from GitHub' : '從 GitHub 匯入', exact: true}).click();
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(1);
  await expect(page.locator('[data-pm-kanban-column="backlog"] [data-pm-kanban-work]')).toHaveCount(1);
  await expect(page.locator('[data-github-card-link]')).toHaveAttribute('href', 'https://github.com/example/board/issues/1');
  await expect(panel.locator('[data-github-sync-status]')).toContainText(locale === 'en' ? 'Up to date' : '已與 GitHub 同步');
  await panel.getByRole('button', {name: locale === 'en' ? 'Sync now' : '立即同步', exact: true}).click();
  await expect.poll(() => writes.length).toBe(2);
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(1);
  expect(writes[0].idempotency_key).not.toBe(writes[1].idempotency_key);
  expect(errors).toEqual([]);
  await page.screenshot({path: `${shots}/imported-${locale}-${native ? 'ipc' : 'http'}.png`, fullPage: true});
});

for (const locale of ['en', 'zh-TW']) for (const state of ['no_repo', 'empty', 'rate_limited', 'unreachable', 'missing', 'invalid_source', 'apply_failed', 'sync_failed', 'identity_conflict']) test(`GitHub empty board explains ${state} ${locale}`, async ({page}) => {
  const fixture = demoFixture({locale, managedSession: true}), {ids} = fixture;
  const text: Record<string, string[]> = {
    no_repo: ['Link a GitHub repository', '連結 GitHub 儲存庫'], empty: ['no open issues', '沒有開啟中的議題'],
    missing: ['missing from GitHub', 'GitHub 來源已消失'], invalid_source: ['invalid source data', '來源資料無效'], apply_failed: ['could not be applied', '無法套用'], sync_failed: ['could not run', '無法執行'], identity_conflict: ['repository identity', '儲存庫身分'],
    rate_limited: ['limiting requests', '限制請求次數'], unreachable: ['could not be reached', '無法連線'],
  };
  await page.addInitScript(value => Object.defineProperty(navigator, 'language', {value}), locale);
  await mountConversation(page, false, async (input: any) => {
    expect(input.method).toBe('GET');
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path.endsWith('/projects') && path.startsWith('/pm/')) return {status: 200, data: {
      github_sync: {projects: [{project_id: ids.projectId, name: 'Example board', repository: state === 'no_repo' ? null : 'example/board',
        revision: 0, enabled: true, status: state === 'no_repo' ? 'not_synced' : state, last_sync: null}], suggested_repository: null}}};
    const result: any = await fixture.dispatch(input);
    const caps = path === '/capabilities' ? result.data : path === '/bootstrap' ? result.data.capabilities : null;
    if (caps) {caps.features.board_github_sync = {version: 1}; caps.scopes.push('manage');
      caps.actions.push(...['pm.github.import', 'project.github.configure'].map(action => ({action, allowed: true, scope: 'manage'})));}
    if (path === `/pm/workspaces/${ids.workspaceId}`) result.data.workspace.membership.role = 'owner';
    if (path.endsWith('/kanban')) {result.data.cards = []; result.data.next_cursor = null; result.data.wip = {count: 0, unknown: 0, cards: 0, scope: 'returned_page'};}
    return result;
  });
  await page.goto(`/dashboard/#/pm/${ids.workspaceId}/board`);
  const panel = page.locator('[data-board-github]');
  await expect(panel.locator('[data-github-sync-status]')).toContainText(text[state][locale === 'en' ? 0 : 1]);
  await expect(panel.getByRole('button', {name: locale === 'en' ? 'Import from GitHub' : '從 GitHub 匯入', exact: true})).toBeVisible();
});


for (const locale of ['en', 'zh-TW']) for (const [column, en, zh] of [
  ['github_closed', 'Closed (not completed)', '已關閉（未完成）'],
  ['github_completed', 'Completed · GitHub', '完成議題 · GitHub'],
  ['github_done', 'Done · GitHub', '完成 · GitHub'],
]) test(`GitHub source lane ${column} ${locale}`, async ({page}) => {
  const fixture = demoFixture({locale, managedSession: true}), {ids} = fixture;
  await page.addInitScript(value => Object.defineProperty(navigator, 'language', {value}), locale);
  await mountConversation(page, false, async (input: any) => {
    expect(input.method).toBe('GET');
    const result: any = await fixture.dispatch(input);
    if (new URL(input.path, 'http://fixture').pathname.endsWith('/kanban')) {
      const card = result.data.cards.find((row: any) => row.column === 'backlog');
      card.column = column;
      card.work.external = {repository: 'example/board', repository_id: 123, number: 1, node_id: 'I_node_1',
        key: '123#1:I_node_1', kind: column === 'github_done' ? 'pull_request' : 'issue',
        url: `https://github.com/example/board/${column === 'github_done' ? 'pull' : 'issues'}/1`, source: 'github',
        state: column === 'github_done' ? 'merged' : 'closed', state_reason: column === 'github_completed' ? 'completed' : column === 'github_done' ? null : 'not_planned',
        board_lane: column, applied_lane: 'todo', availability: 'current', manual: false, draft: false};
      result.data.cards = [card]; result.data.next_cursor = null;
    }
    return result;
  });
  await page.goto(`/dashboard/#/pm/${ids.workspaceId}/board`);
  await expect(page.locator(`[data-pm-kanban-column="${column}"]`)).toContainText(locale === 'en' ? en : zh);
  await expect(page.locator('[data-github-card-link]')).toHaveCount(1);
});


for (const locale of ['en', 'zh-TW']) for (const missing of [false, true]) test(`GitHub draft and missing source are visible ${locale} ${missing}`, async ({page}) => {
  const fixture = demoFixture({locale, managedSession: true}), {ids} = fixture;
  await page.addInitScript(value => Object.defineProperty(navigator, 'language', {value}), locale);
  await mountConversation(page, false, async (input: any) => {
    expect(input.method).toBe('GET');
    const result: any = await fixture.dispatch(input);
    if (new URL(input.path, 'http://fixture').pathname.endsWith('/kanban')) {
      const card = result.data.cards.find((row: any) => row.column === 'in_progress');
      card.work.external = {repository: 'example/board', repository_id: 123, number: 1,
        node_id: 'I_node_1', key: '123#1:I_node_1', kind: 'pull_request',
        url: 'https://github.com/example/board/pull/1', source: 'github', state: 'open', state_reason: null,
        applied_lane: 'doing', board_lane: 'doing', manual: false, draft: true, availability: missing ? 'missing' : 'current'};
      result.data.cards = [card]; result.data.next_cursor = null;
    }
    return result;
  });
  await page.goto(`/dashboard/#/pm/${ids.workspaceId}/board`);
  const links = page.locator('[data-github-card-link]');
  await expect(links).toHaveCount(1);
  await expect(links).toContainText(missing ? locale === 'en' ? 'Source missing' : '來源已消失' : locale === 'en' ? 'Draft' : '草稿');
});

for (const locale of ['en', 'zh-TW']) test(`Failed first import preserves GitHub choices ${locale}`, async ({page}) => {
  const fixture = demoFixture({locale, managedSession: true}), {ids} = fixture;
  let operation: any = null;
  await page.addInitScript(value => Object.defineProperty(navigator, 'language', {value}), locale);
  await mountConversation(page, false, async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (input.method === 'POST') {
      expect(input.body.params.include_closed).toBe(true); expect(input.body.params.enabled).toBe(false);
      operation = {...input.body, operation_id: 'op_' + 'd'.repeat(32), actor: ids.actor, idempotency_key: input.idempotency_key,
        status: 'succeeded', result: {project_id: null, status: 'unreachable', github_sync: {revision: 0, repository: null, workspace_id: null}}};
      return {status: 202, data: {operation}};
    }
    if (path.startsWith('/operations/')) return {status: 200, data: {operation}};
    if (path.endsWith('/projects') && path.startsWith('/pm/')) return {status: 200, data: {
      version: 1, workspace_id: ids.workspaceId, bindings: [], next_cursor: null,
      github_sync: {projects: [], suggested_repository: 'example/board'}}};
    const result: any = await fixture.dispatch(input);
    const caps = path === '/capabilities' ? result.data : path === '/bootstrap' ? result.data.capabilities : null;
    if (caps) {caps.features.board_github_sync = {version: 1}; caps.scopes.push('manage');
      caps.actions.push({action: 'pm.github.import', allowed: true, scope: 'manage'});}
    if (path === `/pm/workspaces/${ids.workspaceId}`) result.data.workspace.membership.role = 'owner';
    return result;
  });
  await page.goto(`/dashboard/#/pm/${ids.workspaceId}/board`);
  const panel = page.locator('[data-board-github]');
  const history = panel.getByRole('checkbox', {name: locale === 'en' ? 'Include closed history' : '包含已關閉的歷史'});
  const keep = panel.getByRole('checkbox', {name: locale === 'en' ? 'Keep in sync' : '持續同步'});
  await history.check(); await keep.uncheck();
  await panel.getByRole('button', {name: locale === 'en' ? 'Import from GitHub' : '從 GitHub 匯入', exact: true}).click();
  await expect(panel.locator('[data-github-sync-status]')).toContainText(locale === 'en' ? 'could not be reached' : '無法連線');
  await expect(history).toBeChecked(); await expect(keep).not.toBeChecked();
  await page.reload();
  await expect(history).toBeChecked(); await expect(keep).not.toBeChecked();
});

for (const locale of ['en', 'zh-TW']) test(`Managed Board and failed import drafts survive relaunch ${locale}`, async ({page}) => {
  // Use the real bootstrap producer's verified identity envelope. The native
  // bridge is a substitute; Board forms and storage run from the built bundle.
  const golden = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));
  const fixture = workStartFixture(), writes: any[] = [];
  let operation: any = null;
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (input.method === 'POST') {
      writes.push(input);
      expect(input.body.action).toBe('pm.github.import');
      expect(input.body.params).toMatchObject({repository: 'example/board', include_closed: true, enabled: false});
      operation = {...input.body, operation_id: 'op_' + 'd'.repeat(32), actor: fixture.base.state.actor,
        idempotency_key: input.idempotency_key, status: 'succeeded', result: {project_id: null, status: 'unreachable',
          github_sync: {revision: 0, repository: null, workspace_id: null}}};
      return {status: 202, data: {operation}};
    }
    if (path.startsWith('/operations/')) return {status: 200, data: {operation}};
    if (path === `/pm/workspaces/${workspaceId}/projects`) return {status: 200, data: {
      version: 1, workspace_id: workspaceId, bindings: [], next_cursor: null,
      github_sync: {projects: [], suggested_repository: 'example/board'}}};
    const result: any = await fixture.dispatch(input);
    const caps = path === '/capabilities' ? result.data : path === '/bootstrap' ? result.data.capabilities : null;
    if (caps) {
      caps.desktop_identity = golden.capabilities.desktop_identity;
      caps.features.board_github_sync = {version: 1};
      caps.actions.push({action: 'pm.github.import', allowed: true, scope: 'manage'});
    }
    if (path === '/bootstrap') Object.assign(result.data.sync, golden.capabilities.desktop_identity);
    return result;
  };
  await page.exposeFunction('managedBoardRequest', dispatch);
  await page.addInitScript(locale => {
    Object.defineProperty(navigator, 'language', {value: locale});
    Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === 'native_status') return {endpoint: sessionStorage.getItem('fixture.board.endpoint') || 'http://127.0.0.1:41001/',
        configuration_source: 'managed', credential_available: true};
      if (command === 'connector_connect') return (await (window as any).managedBoardRequest({method: 'GET', path: '/capabilities'})).data;
      if (command === 'connector_request') return (window as any).managedBoardRequest(args.input);
      if (command === 'connector_disconnect') return null;
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      throw Error(`Unexpected native command ${command}`);
    }}});
  }, locale);
  await page.goto(`/dashboard/#/pm/${workspaceId}/board`);
  const openWork = () => page.locator(`[data-pm-kanban-work="${workId(1)}"] [data-board-open]`).click();
  await openWork();
  const instruction = page.locator('[data-work-start-instruction]');
  await instruction.fill('Keep this Board draft without starting work.');
  const panel = page.locator('[data-board-github]');
  const history = panel.getByRole('checkbox', {name: locale === 'en' ? 'Include closed history' : '包含已關閉的歷史'});
  const keep = panel.getByRole('checkbox', {name: locale === 'en' ? 'Keep in sync' : '持續同步'});
  await history.check(); await keep.uncheck();
  await panel.getByRole('button', {name: locale === 'en' ? 'Import from GitHub' : '從 GitHub 匯入', exact: true}).click();
  await expect(panel.locator('[data-github-sync-status]')).toContainText(locale === 'en' ? 'could not be reached' : '無法連線');
  const namespace = ['managed', 'v1', golden.sync.server_id, golden.sync.principal_id, fixture.base.state.actor].map(encodeURIComponent).join(':');
  await expect.poll(() => page.evaluate(namespace => {
    const drafts = Object.entries(localStorage).filter(([key]) => key.startsWith(`batc.pm.${namespace}.`));
    return drafts.some(([key, value]) => key.includes('.work.start.') && value.includes('Keep this Board draft'))
      && drafts.some(([key, value]) => key.includes('.pm.github.import.') && JSON.parse(value).values.include_closed === 'true');
  }, namespace)).toBe(true);
  await page.evaluate(() => sessionStorage.setItem('fixture.board.endpoint', 'http://127.0.0.1:41002/'));
  await page.reload();
  await expect(history).toBeChecked(); await expect(keep).not.toBeChecked();
  await openWork();
  await expect(instruction).toHaveValue('Keep this Board draft without starting work.');
  expect(writes).toHaveLength(1);
  expect(fixture.state.writes).toEqual([]);
});
