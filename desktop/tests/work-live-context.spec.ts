import {test, expect} from '@playwright/test';
import {followUpFixture, openWork, WS, WI, ATTEMPT} from './work-next-turn-fixture.ts';
import {workStartFixture, workspaceId, workId} from './work-start-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {mkdirSync} from 'node:fs';

const context = new URLSearchParams({workspace: WS, work: WI, attempt: ATTEMPT});
const liveRoute = `#/session/demo/search-results?from=board&${context}`;

for (const native of [false, true]) {
test.describe(native ? 'native IPC' : 'Web HTTP', () => {

test('explicit live context survives a different last workspace and reload', async ({page}) => {
  const f = followUpFixture('en', {native});
  await openWork(page, f);
  await page.evaluate(() => {
    for (let n = 0; n < localStorage.length; n++) {
      const key = localStorage.key(n)!;
      if (key.includes('workspace') && localStorage.getItem(key)?.startsWith('ws_')) localStorage.setItem(key, 'ws_' + 'd'.repeat(32));
    }
  });
  await page.evaluate(hash => {location.hash = hash;}, liveRoute);
  await expect(page.locator('[data-follow-up-input]')).toBeVisible();
  await page.reload();
  await page.locator('[data-follow-up-input]').fill('Continue this exact work');
  await page.getByRole('button', {name: 'Send next', exact: true}).click();
  expect(f.follow.posts.at(-1).body.target).toEqual({workspace_id: WS, work_item_id: WI});
  expect(f.follow.posts.at(-1).body.preconditions).toEqual({expected_attempt_id: ATTEMPT});
  await page.getByRole('button', {name: 'Results', exact: true}).click();
  await expect(page.locator('[data-work-result]')).toBeVisible();
  expect(f.follow.errors).toEqual([]);
});

test('a legacy linked work never borrows action authority from last workspace', async ({page}) => {
  const f = followUpFixture('en', {native});
  await openWork(page, f);
  await page.evaluate(id => {location.hash = '#/item/' + id;}, WI);
  await expect(page.locator('[data-session-view]')).toBeVisible();
  await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
  await expect(page.locator('#workspace-message-input')).toBeHidden();
  expect(f.follow.posts).toEqual([]);
});

for (const query of [
  `workspace=${WS}&work=${WI}`,
  `${context}&attempt=${ATTEMPT}`,
  `workspace=elsewhere&work=${WI}&attempt=${ATTEMPT}`,
]) {
  test(`invalid context remains a session read: ${query}`, async ({page}) => {
    const f = followUpFixture('en', {native});
    await openWork(page, f);
    await page.evaluate(hash => {location.hash = hash;}, '#/session/demo/search-results?' + query);
    await expect(page.locator('[data-session-view]')).toBeVisible();
    await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
    await expect(page.locator('#workspace-message-input')).toBeHidden();
    expect(f.follow.posts).toEqual([]);
  });
}

for (const mismatch of ['workspace_id', 'work_item_id', 'session']) {
  test(`the public Attempt must match the route ${mismatch}`, async ({page}) => {
    const f = followUpFixture('en', {native});
    await openWork(page, f);
    f.follow.attempt = {...f.follow.attempt, [mismatch]: mismatch === 'session' ? {host: 'demo', session_id: 'other'} : 'different'};
    await page.evaluate(hash => {location.hash = hash;}, liveRoute);
    await expect(page.locator('#main')).toContainText('This work is no longer in the current project response. Refresh the work tree.');
    await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
    await expect(page.locator('[data-work-result]')).toHaveCount(0);
    expect(f.follow.posts).toEqual([]);
  });
}

test('a newer Attempt never replaces the selected Result or send precondition', async ({page}) => {
  const f = followUpFixture('en', {native});
  await openWork(page, f);
  await page.locator('[data-follow-up-input]').fill('Stay on this attempt');
  f.follow.latest = 'att_' + 'c'.repeat(32);
  const readCount = f.follow.reads.length;
  f.pushEvent('operation');
  await expect.poll(() => f.follow.reads.length).toBeGreaterThan(readCount);
  // The fixture emits nothing else before this explicit send.
  await expect.poll(() => f.follow.reads.some(path => /^GET \/events\?.*after=1(?:&|$)/.test(path))).toBe(true);
  await page.getByRole('button', {name: 'Send next', exact: true}).click();
  expect(f.follow.posts.at(-1).body.preconditions).toEqual({expected_attempt_id: ATTEMPT});
  expect(f.follow.reads.filter(path => path.includes('/work-attempts/')).every(path => path.includes(ATTEMPT))).toBe(true);
});

test('a withdrawn Attempt removes its conversation, composer and fixed Result', async ({page}) => {
  const f = followUpFixture('en', {native});
  await openWork(page, f);
  await expect(page.locator('[data-follow-up-input]')).toBeVisible();
  f.follow.attemptStatus = 404;
  f.pushEvent('work_item');
  await expect(page.locator('[data-session-withdrawn]')).toBeVisible();
  await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
  await expect(page.locator('[data-work-result]')).toBeHidden();
  expect(f.follow.posts).toEqual([]);
});

test('a late context denial cannot add an error to the next project view', async ({page}) => {
  const f = followUpFixture('en', {native});
  await openWork(page, f);
  if (native) await page.evaluate(attempt => {
    const bag = window as any;
    const invoke = bag.__TAURI_INTERNALS__.invoke;
    bag.contextDenialReceived = false;
    bag.__TAURI_INTERNALS__.invoke = async (command: string, args: any) => {
      const response = await invoke(command, args);
      if (command === 'connector_request' && args.input.path.endsWith(`/work-attempts/${attempt}`) && response.status === 404)
        bag.contextDenialReceived = true;
      return response;
    };
  }, ATTEMPT);
  let release = () => {};
  f.follow.holdAttempt = new Promise<void>(resolve => {release = resolve;});
  f.follow.attemptStarted = false;
  f.follow.attemptStatus = 404;
  await page.evaluate(hash => {location.hash = hash;}, liveRoute);
  await expect.poll(() => f.follow.attemptStarted).toBe(true);
  await page.evaluate(workspace => {location.hash = `#/pm/${workspace}/discussions`;}, WS);
  await expect(page.locator('[data-workspace-id]')).toHaveAttribute('data-workspace-id', WS);
  const response = native
    ? page.waitForFunction(() => (window as any).contextDenialReceived)
    : page.waitForResponse(row => row.url().endsWith(`/work-attempts/${ATTEMPT}`) && row.status() === 404);
  release();
  await response;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.locator('[data-workspace-id]')).toHaveAttribute('data-workspace-id', WS);
  await expect(page.locator('#main')).not.toContainText('NOT_FOUND');
  await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
  expect(f.follow.errors).toEqual([]);
});

test('Board Open live output reaches the exact Work, next input and fixed Result', async ({page}) => {
  const start = workStartFixture();
  const f = followUpFixture('en', {native, workspaceId, workItemId: workId(1)});
  start.state.scopes.push('operate');
  start.state.actions.push({action: 'session.send', allowed: true});
  start.state.attempts = [{...f.follow.attempt, stage: 'started', status: 'succeeded', start_operation_id: 'op_' + '1'.repeat(32)}];
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path === '/capabilities' || path === '/bootstrap') {
      const response = await start.dispatch(input);
      const caps = path === '/bootstrap' ? response.data.capabilities : response.data;
      caps.features.pm_workspaces.work_follow_up = {version: 1, available: true};
      caps.hosts.push({host: 'demo', writes: true});
      return response;
    }
    if (path.startsWith('/work-items/')) {
      const response = await f.dispatch(input);
      const source = await start.dispatch(input);
      response.data.work_item = {...response.data.work_item, ...source.data.work_item};
      return response;
    }
    if (path.includes('/work-attempts/') || path.endsWith('/inputs') || path.startsWith('/sessions')
      || path.startsWith('/projects') || input.body?.action === 'session.send') return f.dispatch(input);
    return start.dispatch(input);
  };
  await mountConversation(page, native, dispatch);
  await page.goto(`/dashboard/?workbench=1#/pm/${workspaceId}/board`);
  await page.locator(`[data-pm-kanban-work="${workId(1)}"] [data-board-open]`).click();
  const link = page.locator('[data-work-start-session]');
  await expect(link).toHaveAttribute('href', `#/session/demo/search-results?from=board&workspace=${workspaceId}&work=${workId(1)}&attempt=${ATTEMPT}`);
  await link.click();
  await page.locator('[data-follow-up-input]').fill('Add the retry case');
  await page.getByRole('button', {name: 'Send next', exact: true}).click();
  expect(f.follow.posts.at(-1).body.target).toEqual({workspace_id: workspaceId, work_item_id: workId(1)});
  expect(f.follow.posts.at(-1).body.preconditions).toEqual({expected_attempt_id: ATTEMPT});
  await page.getByRole('button', {name: 'Results', exact: true}).click();
  await expect(page.locator('[data-work-result]')).toBeVisible();
});

test('the same Work and fixed Result remain readable at 1440, 768 and 390', async ({page}) => {
  const f = followUpFixture('zh-TW', {native, title: '改善文件搜尋在沒有結果時的提示，保留原本輸入的關鍵字與焦點，並補上失敗後重新送出的測試'});
  const dir = `test-results/work-live-context/${native ? 'native' : 'web'}`;
  mkdirSync(dir, {recursive: true});
  await openWork(page, f, 'zh-TW');
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({width, height: width === 390 ? 844 : 900});
    await page.locator('#main').evaluate(node => {node.scrollTop = 0;});
    await page.screenshot({path: `${dir}/work-${width}.png`, animations: 'disabled'});
    await page.locator('[data-follow-up-input]').scrollIntoViewIfNeeded();
    await expect(page.locator('[data-follow-up-input]')).toBeVisible();
    await page.screenshot({path: `${dir}/next-${width}.png`, animations: 'disabled'});
    await page.getByRole('button', {name: '成果', exact: true}).click();
    await expect(page.locator('[data-work-result]')).toBeVisible();
    await page.screenshot({path: `${dir}/result-${width}.png`, animations: 'disabled'});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.keyboard.press('Escape');
  }
  expect(f.follow.errors).toEqual([]);
});

for (const options of [
  {provenance: 'manual', api_access: 'read_only'},
  {provenance: 'unknown', api_access: 'read_only'},
  {result: 'not_controller'},
  {result: 'codex'},
]) {
  test(`read-only source remains without a sender: ${JSON.stringify(options)}`, async ({page}) => {
    const f = followUpFixture('en', {native, ...options});
    await openWork(page, f);
    await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
    await expect(page.locator('#workspace-message-input')).toBeHidden();
    expect(f.follow.posts).toEqual([]);
  });
}

test('queued receipt survives reload while an unfinished next draft stays with the Work', async ({page}) => {
  const f = followUpFixture('en', {native});
  await openWork(page, f);
  const input = page.locator('[data-follow-up-input]');
  await input.fill('Add retry coverage');
  await page.getByRole('button', {name: 'Send next', exact: true}).click();
  await expect(page.locator('[data-follow-up-receipt]')).toHaveCount(1);
  await input.fill('Keep this next draft');
  await page.reload();
  await expect(input).toHaveValue('Keep this next draft');
  await expect(page.locator('[data-follow-up-receipt]')).toContainText('Add retry coverage');
  expect(f.follow.posts).toHaveLength(1);
  expect(f.follow.posts[0].body).toMatchObject({
    target: {workspace_id: WS, work_item_id: WI},
    params: {text: 'Add retry coverage', schedule: 'after_current'},
    preconditions: {expected_attempt_id: ATTEMPT},
  });
});

});
}
