import {test, expect} from '@playwright/test';

import {wt, manualWt, unknownWt, taskId, opId, projectId, workId, bareId, sessionTitle, workTitle, tree, work, mount} from './observed-resource-fixture.ts';

async function surface(page: any) {
  return page.locator('[data-observed-resource]').evaluate((root: HTMLElement) => {
    const copy = root.cloneNode(true) as HTMLElement;
    copy.querySelectorAll('[data-observed-details]').forEach(node => node.remove());
    return copy.textContent || '';
  });
}

for (const locale of ['en-US', 'zh-TW']) {
  const zh = locale === 'zh-TW';
  test(`worktree and delivery reading ${locale}`, async ({page}) => {
    const state = {gone: false, seq: 0, events: [] as any[], posts: [] as any[]};
    await mount(page, locale, state);
    for (const width of [1440, 1000, 390]) {
      await page.setViewportSize({width, height: 900});
      await page.goto('/dashboard/#/worktree/' + wt);
      await expect(page.getByRole('heading', {level: 1})).toHaveText(workTitle);
      await expect(page.locator('[data-observed-owner]')).toContainText(zh ? '受管任務' : 'Managed task');
      await expect(page.locator('[data-observed-result] [data-observed-pr="12"]')).toHaveText('PR #12');
      await expect(page.locator('[data-observed-session]').getByRole('link', {name: sessionTitle})).toBeVisible();
      await expect(page.locator('[data-observed-resource]')).not.toContainText(zh ? '活動狀態未確認' : 'Activity unknown');
      const shown = await surface(page);
      expect(shown).not.toMatch(/wt_|op_|task_/);
      expect(shown).not.toContain('{');
      expect(shown).not.toMatch(/undefined|null|NaN|\[object Object\]/);
      await expect(page.locator('[data-observed-resource] code')).toHaveCount(0);
      const visibleActions = await page.locator('[data-observed-resource] a.ui-button').evaluateAll(nodes => nodes.filter(node => !node.closest('[data-observed-details]')).length);
      expect(visibleActions).toBeLessThanOrEqual(2);
      expect(visibleActions).toBeGreaterThan(0);
      await expect(page.locator('[data-observed-details]')).not.toHaveAttribute('open', '');
      await expect(page.locator('[data-observed-raw]')).not.toHaveAttribute('open', '');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

      await page.goto('/dashboard/#/worktree/' + manualWt);
      await expect(page.locator('[data-observed-owner]')).toContainText(zh ? '人工（唯讀）' : 'Manual (read-only)');
      await expect(page.locator('[data-observed-resource] a.ui-button')).toHaveCount(0);
      await expect(page.locator('[data-observed-resource] a[href^="#/cleanup"]')).toHaveCount(0);

      await page.goto('/dashboard/#/worktree/' + unknownWt);
      await expect(page.locator('[data-observed-owner]')).toContainText(zh ? '人工（唯讀）' : 'Manual (read-only)');
      await expect(page.locator('[data-observed-resource] a[href^="#/cleanup"]')).toHaveCount(0);

      await page.goto('/dashboard/#/task/' + taskId);
      await expect(page.getByRole('heading', {level: 1})).toHaveText(workTitle);
      await expect(page.locator('[data-task-dispatch]')).toHaveCount(0);
      const taskSurface = await surface(page);
      expect(taskSurface).not.toMatch(/wt_|op_|task_/);
      expect(taskSurface).not.toContain('{');
      expect(taskSurface).not.toMatch(/undefined|null|NaN|\[object Object\]/);
      const taskActions = await page.locator('[data-observed-resource] a.ui-button').evaluateAll(nodes => nodes.filter(node => !node.closest('[data-observed-details]')).length);
      expect(taskActions).toBeLessThanOrEqual(2);
      await expect(page.locator('[data-observed-resource] a.ui-button', {hasText: zh ? '審閱成果' : 'Review result'})).toBeVisible();

      await page.goto('/dashboard/#/project/' + projectId);
      const row = page.locator(`[data-project-work="${workId}"]`);
      await expect(row.locator('a.ui-button')).toHaveCount(1);
      await expect(row.locator('a.ui-button')).toHaveText(zh ? '審閱成果' : 'Review result');
      const more = row.getByRole('button', {name: zh ? '更多操作' : 'More actions'});
      await more.focus(); await page.keyboard.press('Enter');
      await expect(row.getByRole('menuitem', {name: zh ? '工作目錄' : 'Worktree', exact: true})).toBeVisible();
      await page.keyboard.press('Escape'); await expect(more).toBeFocused();
      await page.goto('/dashboard/#/work/' + projectId + '/execution/' + bareId);
      const bare = page.locator(`[data-project-work="${bareId}"]`);
      await expect(bare.locator('a.ui-button')).toHaveCount(1);
      await expect(bare.locator('a.ui-button')).toHaveText(zh ? '審閱成果' : 'Review result');
      await expect(page.getByRole('heading', {level: 1})).toHaveText('Notes without a session');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    await page.setViewportSize({width: 1440, height: 900});
    await page.goto('/dashboard/#/worktree/' + wt);
    await page.locator('[data-observed-details] > summary').click();
    await expect(page.locator('[data-observed-raw]')).not.toHaveAttribute('open', '');
    await expect(page.locator('[data-observed-details]')).toContainText(wt);
    await expect(page.locator('[data-observed-details]')).toContainText(taskId);
    await expect(page.locator('[data-observed-details]')).toContainText(opId);
    await expect(page.locator('[data-observed-details]')).toContainText('/srv/login');
    expect(state.posts).toEqual([]);

  });
}

test('withdrawn worktree drops rows and details together', async ({page}) => {
  const state = {gone: false, seq: 0, events: [] as any[], posts: [] as any[]};
  await mount(page, 'en-US', state);
  await page.goto('/dashboard/#/worktree/' + wt);
  await expect(page.getByRole('heading', {level: 1})).toHaveText(workTitle);
  state.gone = true;
  state.events.push({seq: 1, resource_type: 'worktree', resource_id: wt, kind: 'worktree.updated'});
  await expect(page.locator('[data-observed-details]')).toHaveCount(0, {timeout: 10000});
  await expect(page.locator('[data-observed-resource]')).not.toContainText(workTitle);
  await expect(page.locator('[data-observed-resource]')).not.toContainText(wt);
  await expect(page.locator('#main .error')).toBeVisible();
  state.gone = false;
  state.events.push({seq: 2, resource_type: 'worktree', resource_id: wt, kind: 'worktree.updated'});
  await expect(page.getByRole('heading', {level: 1})).toHaveText(workTitle, {timeout: 10000});
  await expect(page.locator('[data-observed-details]')).toContainText(wt);
});

for (const locale of ['en-US', 'zh-TW']) {
  const zh = locale === 'zh-TW';
  test(`unknown ownership and missing result stay honest ${locale}`, async ({page}) => {
    const state = {gone: false, seq: 0, events: [] as any[], posts: [] as any[], intent: 'unrecognised',
      resource: tree({work: [], known_sessions: []})};
    await mount(page, locale, state);
    await page.goto('/dashboard/#/worktree/' + wt);
    await expect(page.getByRole('heading', {level: 1})).toHaveText('login-copy');
    await expect(page.locator('[data-observed-owner]')).toHaveText(zh ? '人工（唯讀）' : 'Manual (read-only)');
    await expect(page.locator('[data-observed-result] .ui-pill')).toHaveText(zh ? '未知' : 'Unknown');
    await expect(page.locator('[data-observed-resource] a[href^="#/cleanup"]')).toHaveCount(0);
    await expect(page.locator('[data-observed-session]')).toHaveCount(0);
    state.resource.branch = null;
    state.events.push({seq: 1, resource_type: 'worktree', resource_id: wt, kind: 'worktree.updated'});
    await expect(page.getByRole('heading', {level: 1})).toHaveText(zh ? '工作目錄' : 'Worktree', {timeout: 10000});
    expect(await surface(page)).not.toMatch(/undefined|null|None recorded|尚無紀錄/);
    expect(state.posts).toEqual([]);
  });

  test(`latest receipt and session activity use their own evidence ${locale}`, async ({page}) => {
    const old = work();
    old.delivered_to.push({...old.delivered_to[0], pull_number: 11, delivered_at: 1791587000});
    const newer = {...work(), id: 'op_' + '3'.repeat(32), title: 'Review accessibility',
      delivered_to: [{...old.delivered_to[0], pull_number: 13, delivered_at: 1791600000}]};
    const state = {gone: false, seq: 0, events: [] as any[], posts: [] as any[], resource: tree({work: [old, newer], known_sessions: [
      {host: 'demo', session_id: 'unknown', observation: {title: 'Unknown activity', streaming: null}},
      {host: 'demo', session_id: 'stale', observation: {title: 'Stale activity', streaming: true, stale: true}},
      {host: 'demo', session_id: 'waiting', observation: {title: 'Waiting activity', streaming: false}},
    ]})};
    await mount(page, locale, state);
    await page.goto('/dashboard/#/worktree/' + wt);
    await expect(page.getByRole('heading', {level: 1})).toHaveText(workTitle);
    await expect(page.locator('[data-observed-result] [data-observed-pr]')).toHaveText('PR #13');
    await expect(page.locator('[data-observed-result] .ui-pill')).toHaveText(zh ? '尚未驗證' : 'Not verified');
    const sessions = page.locator('[data-observed-session]');
    await expect(sessions.nth(0).locator('.ui-pill')).toHaveCount(0);
    await expect(sessions.nth(1).locator('.ui-pill')).toHaveCount(0);
    await expect(sessions.nth(2).locator('.ui-pill')).toHaveCount(0);
    const details = page.locator('[data-observed-details]');
    await details.locator(':scope > summary').click();
    await expect(details.getByRole('link', {name: 'PR #11', exact: true})).toBeVisible();
    await expect(details).toContainText('a'.repeat(40));
    await expect(page.locator('[data-observed-raw]')).not.toHaveAttribute('open', '');
    state.events.push({seq: 1, resource_type: 'worktree', resource_id: wt, kind: 'worktree.updated'});
    await expect.poll(() => page.evaluate(() => Object.entries(localStorage).some(([k, v]) => k.startsWith('batc.sync.') && JSON.parse(v).cursor === 1))).toBe(true);
    await expect(details).toHaveAttribute('open', '');
    expect(state.posts).toEqual([]);
  });

  test(`execution uses recorded delivery stage ${locale}`, async ({page}) => {
    const state = {gone: false, seq: 0, events: [] as any[], posts: [] as any[], task: {task_id: taskId,
      host: 'demo', title: 'Publish accessibility review', project: 'Login', state: 'done', paused: false,
      delivery: {stage: 'merged', merged: {ref: 'PR #12', at: 1791600000}, deployed: null}}};
    await mount(page, locale, state);
    await page.goto('/dashboard/#/task/' + taskId);
    await expect(page.getByRole('heading', {level: 1})).toHaveText(state.task.title);
    await expect(page.locator('[data-observed-resource] [data-status="merged"]')).toHaveText(zh ? '已合併' : 'Merged');
    await expect(page.locator('[data-observed-resource] [data-status="deployed"]')).toHaveCount(0);
    expect(await surface(page)).not.toContain('1791600000');
    expect(state.posts).toEqual([]);
  });
}

for (const type of ['worktree', 'task']) test(`${type} scope withdrawal clears the summary and identifiers`, async ({page}) => {
  const state = {gone: false, error: false, seq: 0, events: [] as any[], posts: [] as any[]};
  await mount(page, 'zh-TW', state);
  const id = type === 'task' ? taskId : wt;
  await page.goto(`/dashboard/#/${type}/${id}`);
  await expect(page.locator('[data-observed-details]')).toHaveCount(1);
  await page.locator('[data-observed-details] > summary').click();
  state.error = true;
  state.events.push({seq: 1, resource_type: type, resource_id: id, kind: `${type}.updated`});
  await expect(page.locator('[data-observed-details]')).toHaveCount(0, {timeout: 10000});
  await expect(page.locator('[data-observed-session], [data-observed-pr], [data-observed-raw]')).toHaveCount(0);
  await expect(page.locator('[data-observed-resource]')).toBeEmpty();
  expect(state.posts).toEqual([]);
});

test('late worktree read cannot revive a withdrawn page', async ({page}) => {
  let release: (() => void) | undefined;
  const state = {gone: false, seq: 0, events: [] as any[], posts: [] as any[], hold: undefined as undefined | (() => Promise<void>)};
  await mount(page, 'en-US', state);
  await page.goto('/dashboard/#/worktree/' + wt);
  await expect(page.getByRole('heading', {level: 1})).toHaveText(workTitle);
  state.hold = () => new Promise<void>(resolve => {release = resolve;});
  state.events.push({seq: 1, resource_type: 'worktree', resource_id: wt, kind: 'worktree.updated'});
  await expect.poll(() => Boolean(release)).toBe(true);
  await page.goto('/dashboard/#/worktree/' + manualWt);
  await expect(page.locator('[data-observed-owner]')).toContainText('Manual (read-only)');
  state.hold = undefined; state.gone = true;
  await page.goto('/dashboard/#/worktree/' + wt);
  await expect(page.locator('[data-observed-resource]')).toBeEmpty();
  const response = page.waitForResponse(r => r.url().endsWith('/worktrees/' + wt) && r.status() === 200);
  release!(); await response;
  await expect(page.locator('[data-observed-resource]')).toBeEmpty();
  await expect(page.locator('[data-observed-details], [data-observed-session], [data-observed-pr]')).toHaveCount(0);
  expect(state.posts).toEqual([]);
});
