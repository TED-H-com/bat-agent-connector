import {test, expect} from '@playwright/test';
import {mount, projectId, wt, taskId, workItem, type ResourceFixtureState} from './observed-resource-fixture.ts';
import {taskFixture, taskId as controlledTask} from './task-controls-fixture.ts';

const fresh = (extra: Partial<ResourceFixtureState> = {}): ResourceFixtureState => ({gone: false, seq: 0, events: [], posts: [], ...extra});
const widths = [1440, 1000, 390];
for (const locale of ['en-US', 'zh-TW']) {
  const zh = locale === 'zh-TW';
  const reason = (status: number, collection = false) => status === 403 ? (zh ? '這個權杖沒有檢視權限。' : 'This credential does not have access.')
    : status === 404 ? collection ? (zh ? '找不到專案清單。' : 'The project list could not be found.')
      : (zh ? '找不到要求的專案。' : 'The requested project could not be found.')
    : zh ? '目前無法連線至 Connector。' : 'Connector is unavailable.';
  for (const status of [403, 404, 503, 0, -1]) test(`fix2 1: unavailable project list explains ${status} and offers only useful recovery ${locale}`, async ({page}) => {
    const state = fresh({manage: true, listStatus: status > 0 ? status : 0, listMissing: status === 0, listNetwork: status === -1});
    await mount(page, locale, state);
    for (const width of widths) {
      await page.setViewportSize({width, height: 900}); await page.goto('/dashboard/#/projects'); await page.reload();
      const unavailable = page.locator('[data-project-unavailable="list"]');
      await expect(unavailable.getByRole('heading')).toHaveText(zh ? '無法取得專案' : 'Projects unavailable');
      await expect(unavailable.locator('p')).toHaveText(reason(status, true));
      expect(await unavailable.innerText()).not.toMatch(/403|404|503|token|FORBIDDEN|NOT_FOUND|undefined|null/);
      await expect(page.locator('#main .row.tree, #main .drawer, #main [data-project-count]')).toHaveCount(0);
      await expect(page.locator('[data-create-record="project"]')).toBeHidden();
      await expect(page.locator('#main .error')).toHaveCount(0);
      const retry = unavailable.getByRole('button', {name: zh ? '重試' : 'Retry', exact: true});
      if (status === 503 || status === -1) await expect(retry).toBeVisible();
      else await expect(retry).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    if (status === 503 || status === -1) {
      state.listStatus = 0; state.listNetwork = false;
      await page.getByRole('button', {name: zh ? '重試' : 'Retry', exact: true}).click();
      await expect(page.locator('#main').getByRole('link', {name: 'Login', exact: true})).toBeVisible();
      await expect(page.locator('[data-project-unavailable]')).toHaveCount(0);
      await expect(page.getByRole('textbox', {name: zh ? '新專案名稱' : 'New project name', exact: true})).toBeEnabled();
    }
    expect(state.posts).toEqual([]);
  });

  for (const status of [403, 404]) test(`fix2 1: withdrawn project explains ${status} and returns to Projects ${locale}`, async ({page}) => {
    const state = fresh({manage: true, workItems: [workItem()]}); await mount(page, locale, state);
    for (const width of widths) {
      state.projectStatus = 0;
      await page.setViewportSize({width, height: 900}); await page.goto('/dashboard/#/project/' + projectId); await page.reload();
      await expect(page.locator('[data-project-work]')).toHaveCount(2);
      await page.locator('[data-project-summary]').getByRole('button', {name: zh ? '更多' : 'More', exact: true}).click();
      await page.getByRole('textbox', {name: zh ? '名稱' : 'Name', exact: true}).fill('Unsent project');
      state.projectStatus = status; state.events.push({seq: ++state.seq, resource_type: 'project', resource_id: projectId, kind: 'project.updated'});
      const unavailable = page.locator('[data-project-unavailable="project"]');
      await expect(unavailable.getByRole('heading')).toHaveText(zh ? '無法開啟專案' : 'Project unavailable');
      await expect(unavailable.locator('p')).toHaveText(reason(status));
      await expect(page.locator('[data-project-work], [data-project-count], [data-project-editor], [data-work-item-inspector]')).toHaveCount(0);
      await expect(page.locator('[data-project-item-card]')).toBeHidden();
      await expect(unavailable.getByRole('button')).toHaveCount(0);
      const back = unavailable.getByRole('link', {name: zh ? '返回專案' : 'Back to Projects', exact: true});
      await expect(back).toBeVisible(); await back.click();
      await expect(page).toHaveURL(/#\/projects$/);
      await expect(page.locator('#main').getByRole('link', {name: 'Login', exact: true})).toBeVisible();
    }
    expect(state.posts).toEqual([]);
  });

  test(`fix2 1: a late successful project list cannot undo withdrawal ${locale}`, async ({page}) => {
    const state = fresh({manage: true}); await mount(page, locale, state); await page.goto('/dashboard/#/projects');
    await expect(page.locator('#main .row.tree')).toHaveCount(1);
    let release: (() => void) | undefined;
    state.holdList = archived => archived ? new Promise<void>(resolve => {release = resolve;}) : Promise.resolve();
    const filter = page.locator('#main').getByRole('checkbox', {name: zh ? '顯示已封存' : 'Show archived'});
    await filter.check(); await expect.poll(() => Boolean(release)).toBe(true);
    state.listStatus = 403; await filter.uncheck();
    await expect(page.locator('[data-project-unavailable="list"]')).toContainText(reason(403));
    const late = page.waitForResponse(response => response.url().endsWith('/projects?include_archived=true') && response.status() === 200);
    release!(); await late;
    await expect(page.locator('#main .row.tree, #main .drawer, #main [data-project-count]')).toHaveCount(0);
    await expect(page.locator('[data-project-unavailable="list"]')).toContainText(reason(403));
    await expect(page.locator('[data-create-record="project"]')).toBeHidden();
    expect(state.posts).toEqual([]);
  });

  test(`fix2 2: readers see paused status and reason without execution controls ${locale}`, async ({page}) => {
    await page.addInitScript(value => Object.defineProperty(navigator, 'language', {value}), locale);
    const pauseReason = zh ? '先完成成果審閱。' : 'Finish the result review first.';
    const state = await taskFixture(page, false, {scopes: ['observe'], paused: true,
      taskEvents: [{kind: 'paused', created_at: 1791600000, body: JSON.stringify({reason: pauseReason})}]});
    for (const width of widths) {
      await page.setViewportSize({width, height: 900}); await page.goto('/dashboard/#/task/' + controlledTask); await page.reload();
      await expect(page.locator('[data-observed-resource] [data-task-dispatch]')).toHaveText(zh ? '已暫停派工' : 'Dispatch paused');
      await expect(page.getByText(pauseReason, {exact: true})).toBeVisible();
      await expect(page.locator('[data-task-controls]')).toBeHidden();
      await expect(page.getByRole('heading', {name: zh ? '執行控制' : 'Execution controls', exact: true})).toHaveCount(0);
      await expect(page.getByRole('button', {name: zh ? '恢復派工' : 'Resume dispatch', exact: true})).toHaveCount(0);
      expect(await page.locator('#main').innerText()).not.toContain(zh ? '這個帳號可以檢視，但不能暫停派工。' : 'This account can view this work but cannot pause dispatch.');
    }
    expect(state.posts).toEqual([]);
  });

  test(`fix2 3: project overflow and reorder use accessible ghost icons ${locale}`, async ({page}) => {
    const state = fresh({manage: true, workItems: [workItem(), workItem({work_item_id: 'wi_' + 'b'.repeat(20), title: 'Review the copy'})]});
    await mount(page, locale, state);
    for (const width of widths) {
      await page.setViewportSize({width, height: 900}); await page.goto('/dashboard/#/project/' + projectId);
      await expect(page.locator('[data-project-summary] h1')).toHaveText('Login');
      const more = page.locator('[data-project-summary]').getByRole('button', {name: zh ? '更多' : 'More', exact: true});
      const row = page.locator('[data-project-items] .row.tree').first();
      const itemMore = row.getByRole('button', {name: zh ? '更多' : 'More', exact: true});
      const grip = row.getByRole('button', {name: zh ? '拖曳排序：Check the login copy' : 'Drag to reorder: Check the login copy', exact: true});
      for (const control of [more, itemMore, grip]) {
        await expect(control).toHaveClass(/ui-button ghost ui-icon-button/);
        expect(await control.evaluate((button: HTMLElement) => getComputedStyle(button).borderTopWidth)).toBe('1px');
        expect(await control.evaluate((button: HTMLElement) => getComputedStyle(button).borderTopColor)).toBe('rgba(0, 0, 0, 0)');
        await expect(control.locator('svg')).toHaveCount(1);
        expect(await control.innerText()).toBe('');
        if (width === 390) {const bounds = await control.boundingBox(); expect(bounds!.width).toBeGreaterThanOrEqual(44); expect(bounds!.height).toBeGreaterThanOrEqual(44);}
      }
      await expect(grip).toHaveAttribute('title', zh ? '只在同層移動；也可使用上移／下移按鈕。' : 'Move within these siblings, or use the Move up / Move down buttons.');
      await itemMore.click();
      await expect(itemMore).toHaveAttribute('aria-expanded', 'true');
      await expect(page.locator('[data-work-item-inspector]').first().getByRole('button', {name: zh ? '上移' : 'Move up', exact: true})).toBeVisible();
      await expect(page.locator('[data-work-item-inspector]').first().getByRole('button', {name: zh ? '下移' : 'Move down', exact: true})).toBeEnabled();
      await page.locator('[data-work-item-inspector]').first().getByRole('textbox').first().focus();
      await page.keyboard.press('Escape');
      await more.click(); await page.getByRole('textbox', {name: zh ? '名稱' : 'Name', exact: true}).focus();
      await page.keyboard.press('Escape'); await expect(more).toBeFocused();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    expect(state.posts).toEqual([]);
  });

  for (const kind of ['worktree', 'task']) test(`fix2 4: ${kind} history and links share a plain section ${locale}`, async ({page}) => {
    const state = fresh(); await mount(page, locale, state);
    for (const width of widths) {
      await page.setViewportSize({width, height: 900}); await page.goto('/dashboard/#/' + kind + '/' + (kind === 'task' ? taskId : wt));
      const section = page.locator('[data-resource-disclosures]');
      await expect(page.locator('[data-observed-resource] h1')).toBeVisible();
      await expect(section.locator('.panel, .ui-section-card')).toHaveCount(0);
      const history = section.locator('[data-observation="history"]'), relations = section.locator('[data-observation="relations"]');
      await expect(history.locator(':scope > summary')).toHaveText(zh ? '資源歷程' : 'Resource history');
      await expect(relations.locator(':scope > summary')).toHaveText(zh ? '執行與工作階段關係' : 'Execution and session relations');
      for (const disclosure of [history, relations]) {
        expect(await disclosure.evaluate((details: HTMLDetailsElement) => details.open)).toBe(false);
        expect(await disclosure.evaluate((details: HTMLElement) => getComputedStyle(details).borderBottomWidth)).toBe('1px');
        await disclosure.locator(':scope > summary').click();
        await expect(disclosure.getByRole('heading', {name: zh ? '這個範圍沒有已記錄的證據。' : 'No recorded evidence in this range.', exact: true})).toBeVisible();
        await expect(disclosure.getByRole('textbox', {name: disclosure === history ? (zh ? '事件類型（選填）' : 'Event kind (optional)')
          : (zh ? '執行識別碼（選填）' : 'Execution ID (optional)'), exact: true})).toBeVisible();
        if (zh) expect(await disclosure.innerText()).not.toMatch(/journal|Execution|\bkind\b/);
        await disclosure.locator(':scope > summary').click();
        expect(await disclosure.evaluate((details: HTMLDetailsElement) => details.open)).toBe(false);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    expect(state.posts).toEqual([]);
  });
}
