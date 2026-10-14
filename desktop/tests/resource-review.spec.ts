import {test, expect} from '@playwright/test';
import {mount, project, projectId, wt, workItem, type ResourceFixtureState} from './observed-resource-fixture.ts';
import {taskFixture, taskId} from './task-controls-fixture.ts';

const fresh = (extra: Partial<ResourceFixtureState> = {}): ResourceFixtureState => ({gone: false, seq: 0, events: [], posts: [], ...extra});
const route = '/dashboard/#/project/' + projectId;
const summary = (page: any) => page.locator('[data-project-summary]');
const items = (page: any) => page.locator('[data-project-item-card]');
const signal = (state: ResourceFixtureState) => state.events.push({seq: ++state.seq, resource_type: 'project', resource_id: projectId, kind: 'project.updated'});

for (const locale of ['en-US', 'zh-TW']) {
  const zh = locale === 'zh-TW';
  test(`1: untitled sessions keep their own title and host ${locale}`, async ({page}) => {
    const state = fresh(); await mount(page, locale, state);
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 900});
      await page.goto('/dashboard/#/worktree/' + wt);
      const row = page.locator('[data-observed-session]').nth(1);
      await expect(row.getByRole('link')).toHaveText(zh ? '未命名工作階段' : 'Untitled session');
      await expect(row.locator('[data-observed-session-host]')).toHaveText('demo');
      await expect(row.locator('.ui-pill')).toHaveCount(0);
    }
    expect(state.posts).toEqual([]);
  });

  test(`3: dispatch appears only for a recorded pause ${locale}`, async ({page}) => {
    await page.addInitScript(value => Object.defineProperty(navigator, 'language', {value}), locale);
    const state = await taskFixture(page);
    await page.goto('/dashboard/#/task/' + taskId);
    const resource = page.locator('[data-observed-resource]');
    await expect(resource.getByText(zh ? '派工' : 'Dispatch', {exact: true})).toHaveCount(0);
    await expect(resource.locator('[data-task-dispatch]')).toHaveCount(0);
    const reason = zh ? '先完成成果審閱。' : 'Finish the result review first.';
    state.paused = true; state.version++;
    state.taskEvents = [{kind: 'paused', created_at: 1791600000, body: JSON.stringify({reason})}];
    state.events.push({seq: 1, resource_type: 'task', resource_id: taskId, kind: 'task.updated'});
    await expect(resource.locator('[data-task-dispatch]')).toHaveText(zh ? '已暫停' : 'Paused');
    await expect(resource.getByText(reason, {exact: true})).toBeVisible();
    state.paused = false; state.version++;
    state.events.push({seq: 2, resource_type: 'task', resource_id: taskId, kind: 'task.updated'});
    await expect(resource.locator('[data-task-dispatch]')).toHaveCount(0);
    await expect(resource.getByText(reason, {exact: true})).toBeHidden();
    expect(state.posts).toEqual([]);
  });

  test(`4: accounts without control permission have no pause entry point ${locale}`, async ({page}) => {
    await page.addInitScript(value => Object.defineProperty(navigator, 'language', {value}), locale);
    const state = await taskFixture(page, false, {scopes: ['observe']});
    const panel = page.locator('[data-task-controls]');
    for (const allowed of [true, false]) {
      state.scopes = allowed ? ['observe'] : ['observe', 'operate']; state.allowed = allowed;
      await page.goto('/dashboard/#/task/' + taskId); await page.reload();
      if (allowed) await expect(panel).toBeHidden();
      else await expect(panel).toContainText(zh ? '這個帳號可以檢視，但不能暫停派工。' : 'This account can view this work but cannot pause dispatch.');
      await expect(panel.getByRole('button', {name: zh ? '暫停派工' : 'Pause dispatch', exact: true})).toBeHidden();
      await expect(panel.getByRole('checkbox')).toBeHidden();
      expect(await panel.innerText()).not.toMatch(/\boperate\b|\bscope\b|權限/);
    }
    state.scopes = ['observe', 'operate']; state.allowed = true; await page.reload();
    await expect(panel.getByRole('button', {name: zh ? '暫停派工' : 'Pause dispatch', exact: true})).toBeVisible();
    expect(state.posts).toEqual([]);
  });

  test(`5b–g: read-only project has an honest header and one work-item list ${locale}`, async ({page}) => {
    const state = fresh({project: project({counts: {total: 0}})}); await mount(page, locale, state);
    for (const width of [1440, 1000, 390]) {
      await page.setViewportSize({width, height: 900}); await page.goto(route);
      await expect(summary(page).getByRole('heading', {name: 'Login', exact: true})).toBeVisible();
      await expect(summary(page).locator('[data-project-count]')).toHaveCount(0);
      await expect(page.locator('#main > .note')).toHaveText(zh
        ? '這個帳號可檢視專案；如需編輯，請到「連線」更換憑證。'
        : 'This account can view projects; change the credential under Connection to edit.');
      expect(await page.locator('#main').innerText()).not.toContain(projectId);
      await expect(page.locator('[data-create-record="work_item"]')).toBeHidden();
      await expect(page.getByRole('button', {name: zh ? '新增' : 'Add', exact: true})).toBeHidden();
      await expect(items(page).getByRole('heading', {name: zh ? '還沒有工作項目。' : 'No work items yet.', exact: true})).toBeVisible();
      const filter = items(page).getByRole('checkbox', {name: zh ? '顯示已封存' : 'Show archived'});
      await expect(filter).toBeVisible();
      expect(await filter.evaluate((box: HTMLInputElement) => {
        const a = box.getBoundingClientRect(), b = box.parentElement!.querySelector('span')!.getBoundingClientRect();
        return b.left >= a.right && Math.abs(a.y + a.height / 2 - b.y - b.height / 2) < 2;
      })).toBe(true);
      if (width === 390) await page.locator('#workspace-menu').click();
      const rail = page.locator('.workspace-nav');
      await expect(rail.getByRole('link', {name: 'Login', exact: true})).toBeVisible();
      await expect(rail.locator('[data-project-create]')).toBeHidden();
      await expect(rail).not.toContainText(zh ? '還沒有專案。' : 'No projects yet.');
      if (width === 390) await page.keyboard.press('Escape');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    for (const counts of [{done: null, total: 0}, {done: 0, total: null}, {done: 0, total: 0, partial: true}]) {
      state.project.counts = counts; await page.reload();
      await expect(summary(page).getByRole('heading', {name: 'Login', exact: true})).toBeVisible();
      await expect(summary(page).locator('[data-project-count]')).toHaveCount(0);
    }
    state.project.counts = {done: 0, total: 0}; await page.reload();
    await expect(summary(page).locator('[data-project-count="completion"]')).toHaveText(zh ? '完成 0/0' : '0/0 done');
    const archivedTitle = zh ? '舊版文案檢查' : 'Archived copy check';
    state.archived = [workItem({title: archivedTitle, archived: true})];
    await items(page).getByRole('checkbox', {name: zh ? '顯示已封存' : 'Show archived'}).check();
    await expect(items(page).getByRole('link', {name: archivedTitle, exact: true})).toBeVisible();
    await expect(items(page).getByRole('heading', {name: zh ? '還沒有工作項目。' : 'No work items yet.', exact: true})).toBeHidden();
    await expect(items(page).getByRole('button', {name: zh ? '復原' : 'Restore', exact: true})).toBeHidden();
    expect(state.posts).toEqual([]);
  });

  test(`5c–d: write entry points require their existing grant ${locale}`, async ({page}) => {
    const state = fresh({manage: true}); await mount(page, locale, state); await page.goto(route);
    await expect(page.getByRole('textbox', {name: zh ? '新工作項目' : 'New work item', exact: true})).toBeVisible();
    await expect(page.locator('[data-project-create]')).toBeVisible();
    expect(await page.locator('#main').innerText()).not.toContain(projectId);
    state.denyCreation = true; await page.reload();
    await expect(page.locator('[data-create-record="work_item"]')).toBeHidden();
    await expect(page.locator('[data-project-create]')).toBeHidden();
    expect(state.posts).toEqual([]);
  });

  for (const status of [403, 404]) test(`5a: ${status} removes the project while editing and retains drafts ${locale}`, async ({page}) => {
    const state = fresh({manage: true, workItems: [workItem()], project: project({counts: {done: 0, total: 1}})});
    await mount(page, locale, state); await page.goto(route);
    await expect(page.locator('[data-project-work]')).toHaveCount(2);
    await expect(summary(page).locator('[data-project-count="completion"]')).toHaveText(zh ? '完成 0/1' : '0/1 done');
    await page.getByRole('textbox', {name: zh ? '新工作項目' : 'New work item', exact: true}).fill('Unsent work');
    await items(page).locator('.row.tree').getByRole('button', {name: zh ? '更多' : 'More', exact: true}).click();
    await page.locator('[data-work-item-inspector]').getByRole('textbox').first().fill('Unsent child');
    await summary(page).getByRole('button', {name: zh ? '更多' : 'More', exact: true}).click();
    const editor = page.locator('[data-project-editor]');
    await expect(editor.getByRole('textbox', {name: zh ? '儲存庫' : 'Repositories', exact: true})).toBeVisible();
    if (zh) await expect(editor).not.toContainText('Repositories');
    await editor.getByRole('textbox', {name: zh ? '名稱' : 'Name', exact: true}).fill('Unsent project name');
    state.projectStatus = status; signal(state);
    await expect(page.locator('[data-project-work]')).toHaveCount(0, {timeout: 10000});
    await expect(page.locator('[data-project-count], [data-project-editor], [data-work-item-inspector]')).toHaveCount(0);
    await expect(items(page)).toBeHidden();
    await expect(summary(page).getByRole('heading', {name: zh ? '無法開啟專案' : 'Project unavailable', exact: true})).toBeVisible();
    expect(await page.locator('#main').innerText()).not.toContain('Check the login copy');
    state.projectStatus = 0; signal(state);
    await expect(editor.getByRole('textbox', {name: zh ? '名稱' : 'Name', exact: true})).toHaveValue('Unsent project name', {timeout: 10000});
    await expect(page.getByRole('textbox', {name: zh ? '新工作項目' : 'New work item', exact: true})).toHaveValue('Unsent work');
    await expect(page.locator('[data-work-item-inspector]').getByRole('textbox').first()).toHaveValue('Unsent child');
    await expect(page.locator('[data-project-work]')).toHaveCount(2);
    expect(state.posts).toEqual([]);
  });

  test(`5a: an older successful project read cannot revive a withdrawn source ${locale}`, async ({page}) => {
    let release: (() => void) | undefined;
    const state = fresh({project: project()}); await mount(page, locale, state); await page.goto(route);
    await expect(page.locator('[data-project-work]')).toHaveCount(2);
    state.holdProject = archived => archived ? new Promise<void>(resolve => {release = resolve;}) : Promise.resolve();
    const filter = items(page).getByRole('checkbox', {name: zh ? '顯示已封存' : 'Show archived'});
    await filter.check(); await expect.poll(() => Boolean(release)).toBe(true);
    state.projectStatus = 403; await filter.uncheck();
    await expect(page.locator('[data-project-work]')).toHaveCount(0);
    await expect(items(page)).toBeHidden();
    const late = page.waitForResponse(r => r.url().includes('/projects/' + projectId + '?include_archived=true') && r.status() === 200);
    release!(); await late;
    await expect(page.locator('[data-project-work], [data-project-count], [data-project-editor]')).toHaveCount(0);
    await expect(items(page)).toBeHidden(); expect(state.posts).toEqual([]);
  });

  test(`5g: unavailable project lists never claim zero projects ${locale}`, async ({page}) => {
    const state = fresh(); await mount(page, locale, state);
    for (const status of [403, 404, 503, 0]) {
      state.listStatus = status; state.listMissing = status === 0;
      await page.goto(route); await page.reload();
      await expect(summary(page).getByRole('heading', {name: 'Login', exact: true})).toBeVisible();
      const rail = page.locator('.workspace-nav');
      await expect(rail.locator('[data-work-read-error] > details > summary')).toHaveText(zh ? '無法取得工作資料' : 'Work unavailable');
      await expect(rail).not.toContainText(zh ? '還沒有專案。' : 'No projects yet.');
      await rail.getByRole('searchbox').fill('Login');
      await expect(rail).not.toContainText(zh ? '沒有符合的工作。' : 'No matching work.');
      await expect(rail).not.toContainText(zh ? '還沒有專案。' : 'No projects yet.');
    }
    await page.locator('.workspace-session-tree > summary').click();
    await expect(page.locator('.workspace-session-tree .workspace-tree > p')).toHaveText(zh ? '0 個工作階段' : '0 sessions');
    await expect(page.locator('.workspace-nav')).not.toContainText(zh ? '還沒有專案。' : 'No projects yet.');
    expect(state.posts).toEqual([]);
  });
}
