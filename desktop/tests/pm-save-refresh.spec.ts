import {mkdirSync} from 'node:fs';
import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture, pmRoute, workspaceId, threadId} from './pm-fixture.ts';
import {openThreadTab, openPmPanel} from './pm-navigation.ts';

for (const native of [false, true]) for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) {
  test(`a background lifecycle read does not lose a plan save (${native ? 'IPC' : 'Web'} ${locale} ${width})`, async ({browser, baseURL}) => {
    const zh = locale === 'zh-TW', context = await browser.newContext({baseURL, locale,
      viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage(), f = pmFixture(), errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    f.state.plans = true; f.state.thread.mode = 'planning';
    f.state.thread.title = zh ? '首次使用計畫' : 'First visit plan';
    f.state.workspaces[0].name = zh ? '文件工作區' : 'Documentation workspace';
    f.state.messages[0].content = zh ? '保留討論來源，讓第一個操作清楚可見。' : 'Keep the sources and make the first action visible.';
    const title = zh ? '更新首次使用計畫' : 'Updated first visit plan';
    const saveLabel = zh ? '儲存計畫版本' : 'Save plan revision';
    let holdNext = false, held = false, denied = false, paused = false, policyCursor = 0, release = () => {};
    let waiting: Promise<void>;
    const hold = () => {held = false; holdNext = true; waiting = new Promise<void>(resolve => {release = resolve;});};
    const dispatch = async (input: any) => {
      const url = new URL(input.path, 'http://fixture'), path = url.pathname;
      if (path === '/capabilities' || path === '/bootstrap') {
        const response = await f.dispatch(input), caps = path === '/bootstrap' ? response.data.capabilities : response.data;
        caps.features.pm_workspaces.lifecycle = {version: 1, gated_actions: ['pm.plan.draft'], record_actions: []};
        return response;
      }
      // The policy change must close admission even if an earlier thread event
      // coalesces the page into one background read.
      if (path === '/events' && policyCursor && Number(url.searchParams.get('after')) < policyCursor) {
        return {status: 200, data: {events: [
          {seq: policyCursor - 1, resource_type: 'pm_thread', resource_id: threadId, kind: 'pm.message.append'},
          {seq: policyCursor, resource_type: 'pm_lifecycle', resource_id: workspaceId, kind: 'pm.lifecycle.pause'}
        ], next_cursor: f.state.cursor, head_cursor: f.state.cursor, has_more: false,
        sync: {checkpoint: {cursor: f.state.cursor, token: `pm-${f.state.cursor}`}}}};
      }
      if (path === `/pm/workspaces/${workspaceId}/lifecycle`) {
        if (holdNext) {holdNext = false; held = true; await waiting;}
        if (denied) return {status: 403, data: {error: {code: 'FORBIDDEN',
          message: zh ? '工作區來源存取權限已變更' : 'Workspace source access changed'}}};
        return {status: 200, data: {workspace_id: workspaceId,
          lifecycle: {stage: paused ? 'paused' : 'planning', status: paused ? 'paused' : 'active',
            revision: paused ? 1 : 0, execution_epoch: paused ? 1 : 0, transition_id: null},
          blocked_actions: paused ? ['pm.plan.draft'] : [], record_actions: [], available_stages: ['idea', 'planning'],
          maintenance: {available: false, reason: 'release_baseline_and_policy_required'},
          inflight_summary: {visible_unresolved: 0, visible_retained: 0, visibility_filtered: false}}};
      }
      return f.dispatch(input);
    };
    const screenshot = async (state: string) => {
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      if (native) return;
      const shots = `${process.env.TMPDIR || '/tmp'}/shots`; mkdirSync(shots, {recursive: true});
      await page.screenshot({path: `${shots}/fix4-${state}-${zh ? 'zh' : 'en'}-${width}.png`, animations: 'disabled'});
    };
    try {
      await mountConversation(page, native, dispatch); await page.goto(pmRoute); await openThreadTab(page, 'plan');
      const editor = page.locator('[data-pm-action="pm.plan.draft"]');
      await page.locator('[data-pm-revision-editor] > summary').click();
      await editor.getByLabel(zh ? '計畫名稱' : 'Plan title', {exact: true}).fill(f.state.thread.title);
      await editor.getByLabel(zh ? '計畫內容（Markdown）' : 'Plan document (Markdown)', {exact: true})
        .fill(zh ? '# 首次使用計畫\n\n保留來源。' : '# First visit plan\n\nKeep the sources.');
      await editor.getByRole('button', {name: saveLabel, exact: true}).click();
      await expect(editor.locator('[data-pm-saved]')).toHaveText(zh ? '已儲存到中央。' : 'Saved centrally.');
      await expect(page.locator('[data-pm-revision]')).toHaveCount(1);
      await editor.getByLabel(zh ? '計畫名稱' : 'Plan title', {exact: true}).fill(title);
      await editor.getByLabel(zh ? '計畫內容（Markdown）' : 'Plan document (Markdown)', {exact: true})
        .fill(zh ? '# 更新首次使用計畫\n\n保留原始版本並納入新資訊。' : '# Updated first visit plan\n\nKeep the original and include new information.');
      const save = editor.getByRole('button', {name: saveLabel, exact: true});
      await expect(save).toBeEnabled(); await save.scrollIntoViewIfNeeded();
      const bounds = await save.boundingBox(); expect(bounds).not.toBeNull();
      await page.mouse.move(bounds!.x + bounds!.width / 2, bounds!.y + bounds!.height / 2);
      await page.mouse.down();
      try {
        hold(); f.state.cursor++;
        await expect.poll(() => held).toBe(true);
        await expect(save).toBeEnabled();
        await screenshot('background-save-pending');
        await page.mouse.up();
      } finally {release(); await page.mouse.up();}
      await expect(page.locator('[data-pm-revision]')).toHaveCount(2);
      await expect(editor.locator('[data-pm-saved]')).toHaveText(zh ? '已儲存到中央。' : 'Saved centrally.');
      await expect(page.locator('[data-pm-revision-open]')).toHaveText(zh ? ['版本 1', '版本 2'] : ['Revision 1', 'Revision 2']);
      await page.locator('[data-pm-revision-open]').last().click();
      await expect(page.locator('[data-pm-revision]').last()).toContainText(title);
      expect(f.state.writes.map(write => write.body.action)).toEqual(['pm.plan.draft', 'pm.plan.draft']);
      expect(f.state.writes[1].body.params.previous_revision_id).toBe(f.state.revisions[0].revision_id);
      await screenshot('two-plan-revisions');
      if (width === 390) await page.keyboard.press('Escape');
      await openPmPanel(page, 'ui_change_phase');
      // The phone drawer makes the plan panel inert; retain the form's submit
      // hook to check admission while the lifecycle state is visible.
      const submit = editor.locator('button[type=submit]');
      hold(); paused = true; policyCursor = f.state.cursor + 2; f.state.cursor = policyCursor;
      try {
        await expect.poll(() => held).toBe(true);
        await expect(submit).toBeDisabled();
        await expect(page.locator('[data-pm-lifecycle] .pm-lifecycle-stage')).toHaveCount(0);
        await screenshot('policy-change-pending');
      } finally {release();}
      await expect(page.locator('[data-pm-lifecycle] .pm-lifecycle-stage')).toHaveText(zh ? '已暫停' : 'Paused');
      await expect(page.locator('[data-pm-lifecycle]')).toContainText(zh
        ? '已停止受理新的受管理工作。既有結果仍可查回，其他控制者的工作階段保持原狀。'
        : 'New managed work is stopped. Existing results remain readable; other controllers’ sessions are preserved.');
      await expect(submit).toBeDisabled();
      expect(f.state.writes).toHaveLength(2);
      await screenshot('policy-paused');
      denied = true; f.state.cursor++;
      await expect(page.locator('[data-pm-lifecycle]')).toContainText(zh ? '目前帳號沒有這項操作的權限。' : 'Your account does not have the required scope.');
      await expect(page.locator('[data-pm-lifecycle]')).toContainText(zh
        ? '目前無法讀取工作區狀態，重新取得資料後才能調整投入。'
        : 'Activity controls are unavailable until the workspace state can be read again.');
      await expect(page.locator('[data-pm-lifecycle] .pm-lifecycle-stage')).toHaveCount(0);
      await expect(submit).toBeDisabled();
      expect(f.state.writes).toHaveLength(2);
      await screenshot('lifecycle-withdrawn');
      expect(errors).toEqual([]);
    } finally {release(); await context.close();}
  });
}
