import {test, expect} from '@playwright/test';
import {mount, project, projectId, wt, manualWt, workItem, taskId, type ResourceFixtureState} from './observed-resource-fixture.ts';
import {taskFixture, taskId as controlledTask} from './task-controls-fixture.ts';

const fresh = (extra: Partial<ResourceFixtureState> = {}): ResourceFixtureState => ({gone: false, seq: 0, events: [], posts: [], ...extra});
const widths = [1440, 1000, 390];
for (const locale of ['en-US', 'zh-TW']) {
  const zh = locale === 'zh-TW';
  for (const failure of ['403', '404', '503', 'network', 'invalid', 'missing']) {
    test(`fix3 1: unavailable ${failure} has one coherent explanation ${locale}`, async ({page}) => {
      const state = fresh({listStatus: Number(failure) || 0, listNetwork: failure === 'network', listMissing: failure === 'missing',
        project: failure === 'invalid' ? project({children: null}) : project()});
      await mount(page, locale, state);
      for (const width of widths) {
        await page.setViewportSize({width, height: 900}); await page.goto('/dashboard/#/projects'); await page.reload();
        const unavailable = page.locator('[data-project-unavailable="list"]');
        await expect(unavailable.getByRole('heading')).toHaveText(zh ? '無法取得專案' : 'Projects unavailable');
        await expect(page.locator('#main > .note')).toBeHidden();
        await expect(page.locator('#main > details')).toHaveCount(0);
        expect(await page.locator('#main').innerText()).not.toContain(zh ? '這個帳號可檢視專案' : 'This account can view projects');
        const code = failure === '403' ? 'FORBIDDEN' : failure === '404' ? 'NOT_FOUND' : failure === '503' ? 'UNAVAILABLE' : '';
        const details = unavailable.locator('details');
        if (code) {
          await expect(details.locator('summary')).toHaveText(zh ? '詳細資訊' : 'Details');
          expect(await details.evaluate((node: HTMLDetailsElement) => node.open)).toBe(false);
          expect(await unavailable.innerText()).not.toContain(code);
          await details.locator('summary').click(); await expect(details.locator('.mono')).toHaveText(code);
          await details.locator('summary').click();
        } else await expect(details).toHaveCount(0);
        await expect(unavailable.getByRole('button', {name: zh ? '連線設定' : 'Connection settings', exact: true})).toHaveCount(failure === '403' ? 1 : 0);
        await expect(unavailable.getByRole('button', {name: zh ? '重試' : 'Retry', exact: true})).toHaveCount(['503', 'network'].includes(failure) ? 1 : 0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      }
      expect(state.posts).toEqual([]);
    });
  }

  test(`fix3 1: forbidden catalog opens Connection rather than a remembered settings section ${locale}`, async ({page}) => {
    const state = fresh({listStatus: 403}); await mount(page, locale, state);
    for (const width of widths) {
      await page.setViewportSize({width, height: 900}); await page.goto('/dashboard/#/settings');
      await page.locator('[data-settings-nav="about"]').click();
      await expect(page.locator('[data-settings-panel="about"]')).toBeVisible();
      await page.goto('/dashboard/#/projects');
      await page.getByRole('button', {name: zh ? '連線設定' : 'Connection settings', exact: true}).click();
      await expect(page).toHaveURL(/#\/settings$/);
      await expect(page.locator('[data-settings-panel="connection"]')).toBeVisible();
      await expect(page.locator('[data-settings-nav="connection"]')).toHaveAttribute('aria-selected', 'true');
      await expect(page.locator('[data-settings-panel="connection"]').getByRole('button', {name: zh ? '中斷' : 'Disconnect', exact: true})).toBeVisible();
      await expect(page.locator('[data-settings-panel="about"]')).toBeHidden();
    }
    expect(state.posts).toEqual([]);
  });

  test(`fix3 1: withdrawn reader project removes the view-only notice as well ${locale}`, async ({page}) => {
    const state = fresh(); await mount(page, locale, state);
    for (const status of [403, 404]) for (const width of widths) {
      state.projectStatus = 0; await page.setViewportSize({width, height: 900});
      await page.goto('/dashboard/#/project/' + projectId); await page.reload();
      await expect(page.locator('#main > .note')).toBeVisible();
      state.projectStatus = status; state.events.push({seq: ++state.seq, resource_type: 'project', resource_id: projectId, kind: 'project.updated'});
      const unavailable = page.locator('[data-project-unavailable="project"]');
      await expect(unavailable.getByRole('heading')).toHaveText(zh ? '無法開啟專案' : 'Project unavailable');
      await expect(page.locator('#main > .note')).toBeHidden();
      await expect(unavailable.getByRole('link', {name: zh ? '返回專案' : 'Back to Projects', exact: true})).toBeVisible();
      await expect(unavailable.getByRole('button', {name: zh ? '重試' : 'Retry', exact: true})).toHaveCount(0);
      await expect(page.locator('[data-project-work], [data-project-count], [data-work-item-inspector]')).toHaveCount(0);
    }
    expect(state.posts).toEqual([]);
  });

  test(`fix3 2: move and pin controls use icons and explain disabled moves ${locale}`, async ({page}) => {
    const state = fresh({manage: true, workItems: [workItem(), workItem({work_item_id: 'wi_' + 'b'.repeat(20), title: 'Second item'})]});
    await mount(page, locale, state);
    for (const width of widths) {
      await page.setViewportSize({width, height: 900}); await page.goto('/dashboard/#/project/' + projectId); await page.reload();
      const more = page.locator('[data-project-items] .row.tree').first().getByRole('button', {name: zh ? '更多' : 'More', exact: true});
      await more.click(); const drawer = page.locator('[data-work-item-inspector]').first();
      const up = drawer.getByRole('button', {name: zh ? '上移' : 'Move up', exact: true});
      const down = drawer.getByRole('button', {name: zh ? '下移' : 'Move down', exact: true});
      const pin = drawer.getByRole('button', {name: zh ? '固定在上方' : 'Pin to top', exact: true});
      await expect(up).toBeDisabled(); await expect(down).toBeEnabled();
      await expect(up).toHaveAttribute('title', zh ? '已在這一組的最上方。' : 'Already first in this group.');
      await expect(down).toHaveAttribute('title', zh ? '下移' : 'Move down');
      for (const control of [up, down, pin]) {
        await expect(control).toHaveClass(/ui-button ghost ui-icon-button/);
        await expect(control.locator('svg')).toHaveCount(1); expect(await control.innerText()).toBe('');
        if (width === 390) {const box = await control.boundingBox(); expect(box!.width).toBeGreaterThanOrEqual(44); expect(box!.height).toBeGreaterThanOrEqual(44);}
      }
      expect(await up.evaluate((node: HTMLElement) => Number(getComputedStyle(node).opacity))).toBeLessThan(1);
      await expect(pin).toHaveAttribute('aria-pressed', 'false');
      await down.focus(); await page.keyboard.press('Tab'); await expect(pin).toBeFocused();
      await page.keyboard.press('Escape'); await expect(more).toBeFocused();
    }
    state.workItems![1].pinned = true; await page.reload();
    await page.locator('[data-project-items] .row.tree').first().getByRole('button', {name: zh ? '更多' : 'More', exact: true}).click();
    await expect(page.locator('[data-work-item-inspector]').first().getByRole('button', {name: zh ? '下移' : 'Move down', exact: true}))
      .toHaveAttribute('title', zh ? '置頂與未置頂項目分開排序。' : 'Pinned and unpinned items stay in separate groups.');
    expect(state.posts).toEqual([]);
  });

  test(`fix3 3: ownership kind, known agent and paused dispatch are separate facts ${locale}`, async ({page}) => {
    const state = fresh({task: {task_id: taskId, title: 'Review result', state: 'running', paused: true, lead_agent: 'claude',
      events: [{kind: 'paused', created_at: 1, body: {reason: zh ? '先審閱成果。' : 'Review the result first.'}}]}});
    await mount(page, locale, state);
    for (const width of widths) {
      await page.setViewportSize({width, height: 900}); await page.goto('/dashboard/#/task/' + taskId);
      const facts = page.locator('[data-observed-resource] .or-fact');
      await expect(facts.filter({has: page.getByText(zh ? '歸屬類型' : 'Ownership', {exact: true})})).toHaveText(zh ? '歸屬類型受管任務' : 'OwnershipManaged task');
      await expect(facts.filter({has: page.getByText(zh ? '負責人' : 'Owner', {exact: true})})).toHaveText(zh ? '負責人Claude' : 'OwnerClaude');
      await expect(page.locator('[data-task-dispatch]')).toHaveText(zh ? '已暫停' : 'Paused');
      const dispatch = facts.filter({has: page.getByText(zh ? '派工' : 'Dispatch', {exact: true})});
      await expect(dispatch).toContainText(zh ? '先審閱成果。' : 'Review the result first.');
      expect((await dispatch.innerText()).match(zh ? /派工/g : /Dispatch/g)).toHaveLength(1);
      await page.goto('/dashboard/#/worktree/' + manualWt);
      await expect(page.locator('.or-fact').first()).toHaveText(zh ? '歸屬類型人工（唯讀）' : 'OwnershipManual (read-only)');
      await expect(page.locator('#main').getByRole('button')).toHaveCount(0);
    }
    expect(state.posts).toEqual([]);
  });

  test(`fix3 3: execution controls share the facts card padding ${locale}`, async ({page}) => {
    await page.addInitScript(value => Object.defineProperty(navigator, 'language', {value}), locale);
    const state = await taskFixture(page);
    for (const width of widths) {
      await page.setViewportSize({width, height: 900}); await page.goto('/dashboard/#/task/' + controlledTask);
      const controls = page.locator('[data-task-controls]'), facts = page.locator('[data-observed-resource] .ui-section-card');
      await expect(controls.getByRole('heading')).toHaveText(zh ? '執行控制' : 'Execution controls');
      await expect(controls.getByRole('button', {name: zh ? '暫停派工' : 'Pause dispatch', exact: true})).toBeVisible();
      const padding = async (node: any) => node.evaluate((el: HTMLElement) => ({top: getComputedStyle(el).paddingTop, left: getComputedStyle(el).paddingLeft}));
      expect(await padding(controls)).toEqual(await padding(facts));
      const card = await controls.boundingBox(), heading = await controls.getByRole('heading').boundingBox();
      expect(heading!.y - card!.y).toBe(17);
      expect(await page.locator('[data-observed-resource] .or-fact').first().innerText()).not.toMatch(zh ? /負責人/ : /Owner\b/);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    expect(state.posts).toEqual([]);
  });
}
