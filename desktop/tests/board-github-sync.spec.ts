import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) test(`GitHub empty board import and repeat sync ${locale} ${native ? 'IPC' : 'HTTP'}`, async ({page}) => {
  await page.setViewportSize({width: native ? 390 : 1280, height: 900});
  const fixture = demoFixture({locale, managedSession: true}), {ids} = fixture;
  const errors: string[] = [], writes: any[] = [], operations = new Map<string, any>();
  page.on('pageerror', error => errors.push(error.message));
  let imported = false, revision = 0;
  const setting = () => ({project_id: ids.projectId, name: 'Example board', repository: 'example/board', workspace_id: ids.workspaceId,
    revision, enabled: true, status: imported ? 'synced' : 'not_synced', last_sync: imported ? 1700000000 : null, authenticated: false});
  const dispatch = async (input: any) => {
    if (input.method === 'POST') {
      writes.push(input);
      expect(input.body.action).toBe('pm.github.import'); expect(input.idempotency_key).toBeTruthy();
      expect(input.body.target).toEqual({workspace_id: ids.workspaceId});
      expect(input.body.preconditions).toEqual({expected_revision: revision});
      expect(input.body.params.repository).toBe('example/board');
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
      result.data.cards = imported ? result.data.cards.slice(0, 1) : [];
      if (imported) {result.data.cards[0].work.title = 'Imported GitHub issue'; result.data.cards[0].work.external = {repository: 'example/board', number: 1, kind: 'issue', url: 'https://github.com/example/board/issues/1'};}
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
  await expect(panel.getByRole('checkbox')).toBeChecked();
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(0);
  const shots = `${process.env.BATC_WORK_VIEW_SCREENSHOTS || process.env.TMPDIR || '/tmp'}/board-github`; await mkdir(shots, {recursive: true});
  await page.screenshot({path: `${shots}/empty-${locale}-${native ? 'ipc' : 'http'}.png`, fullPage: true});
  await panel.getByRole('button', {name: locale === 'en' ? 'Import from GitHub' : '從 GitHub 匯入', exact: true}).click();
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(1);
  await expect(page.locator('[data-github-card-link]')).toHaveAttribute('href', 'https://github.com/example/board/issues/1');
  await expect(panel.locator('[data-github-sync-status]')).toContainText(locale === 'en' ? 'Up to date' : '已與 GitHub 同步');
  await panel.getByRole('button', {name: locale === 'en' ? 'Sync now' : '立即同步', exact: true}).click();
  await expect.poll(() => writes.length).toBe(2);
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(1);
  expect(writes[0].idempotency_key).not.toBe(writes[1].idempotency_key);
  expect(errors).toEqual([]);
  await page.screenshot({path: `${shots}/imported-${locale}-${native ? 'ipc' : 'http'}.png`, fullPage: true});
});

for (const locale of ['en', 'zh-TW']) for (const state of ['no_repo', 'empty', 'rate_limited', 'unreachable']) test(`GitHub empty board explains ${state} ${locale}`, async ({page}) => {
  const fixture = demoFixture({locale, managedSession: true}), {ids} = fixture;
  const text: Record<string, string[]> = {
    no_repo: ['Link a GitHub repository', '連結 GitHub 儲存庫'], empty: ['no open issues', '沒有開啟中的議題'],
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
