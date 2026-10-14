import {test, expect} from '@playwright/test';
import * as governance from './pm-governance-fixture';
import * as meetings from './pm-meetings-fixture';
import * as followUp from './pm-follow-up-fixture';

for (const locale of ['en', 'zh-TW']) {
  const zh = locale === 'zh-TW';
  const cancel = zh ? '取消' : 'Cancel';
  const reason = zh ? '唯讀：需要操作權限' : 'Read only: needs operate permission';
  for (const panel of ['governance', 'meetings', 'follow-up']) {
    const action = panel === 'governance' ? 'pm.governance.record' : panel === 'meetings' ? 'pm.meeting.create' : 'pm.follow_up.prepare';
    const hook = panel === 'governance' ? 'data-pm-governance-record' : panel === 'meetings' ? 'data-pm-meeting-create' : 'data-pm-follow-up-prepare';
    const label = panel === 'governance' ? (zh ? '記錄新事項' : 'Record finding') : panel === 'meetings' ? (zh ? '新增衝刺討論' : 'New discussion') : (zh ? '準備一次討論' : 'Prepare a discussion');
    const mount = async (page: any, f: any) => panel === 'governance' ? governance.mount(page, f, locale) : panel === 'meetings' ? meetings.mount(page, f, locale) : followUp.mount(page, f, {locale});
    const fixture = () => panel === 'governance' ? governance.governanceFixture() : panel === 'meetings' ? meetings.meetingFixture() : followUp.fixture();
    test(`${panel} single entry, focus and draft restoration ${locale}`, async ({page}) => {
      const f = fixture(); await mount(page, f);
      const launch = page.locator(`[${hook}]`), editor = page.locator(`[data-pm-action="${action}"]`);
      await expect(page.getByText(label, {exact: true})).toHaveCount(1);
      await expect(launch).toHaveAttribute('aria-expanded', 'false'); await expect(editor).toBeHidden();
      await launch.click(); await expect(launch).toHaveAttribute('aria-expanded', 'true'); await expect(editor).toBeVisible();
      const first = editor.locator('input:not([type=hidden]),select,textarea').first(); await expect(first).toBeFocused();
      const controls = await launch.getAttribute('aria-controls'); expect(controls).toBeTruthy();
      await expect(page.locator(`[id="${controls}"]`)).toBeVisible();
      await expect(page.getByText(label, {exact: true})).toHaveCount(1);
      if (panel === 'follow-up') await first.selectOption(followUp.snapshotId);
      else await editor.locator('input[aria-label]').first().fill('Preserved creation draft');
      await editor.getByRole('button', {name: cancel, exact: true}).click();
      await expect(editor).toBeHidden(); await expect(launch).toBeFocused(); await expect(launch).toHaveAttribute('aria-expanded', 'false');
      expect(f.state.writes).toHaveLength(0);
      await page.reload();
      if (panel === 'governance') await governance.boot(page, locale);
      else if (panel === 'meetings') await meetings.boot(page, locale);
      else await followUp.mount(page, f, {locale});
      await expect(editor).toBeVisible(); await expect(launch).toHaveAttribute('aria-expanded', 'true');
      if (panel === 'follow-up') await expect(first).toHaveValue(followUp.snapshotId);
      else await expect(editor.locator('input[aria-label]').first()).toHaveValue('Preserved creation draft');
      expect(f.state.writes).toHaveLength(0);
    });
    for (const width of [1440, 390]) test(`${panel} read-only header keeps its reason and refresh aligned ${locale} ${width}`, async ({page}) => {
      await page.setViewportSize({width, height: 1000});
      const f = fixture(); f.state.role = 'reader'; f.state.manage = false; await mount(page, f);
      await expect(page.locator(`[${hook}]`)).toBeHidden();
      const header = page.locator('.pm-heading'), explanation = header.locator('[data-pm-panel-readonly]'), refresh = header.locator('.ui-icon-button');
      await expect(header).toHaveCount(1); await expect(explanation).toBeVisible(); await expect(explanation).toHaveText(reason);
      await expect(refresh).toBeVisible();
      const positions = await header.evaluate(node => {
        const reason = node.querySelector('[data-pm-panel-readonly]')!.getBoundingClientRect(), refresh = node.querySelector('.ui-icon-button')!.getBoundingClientRect();
        return {reasonLeft: reason.left, reasonRight: reason.right, refreshLeft: refresh.left, refreshRight: refresh.right, headerRight: node.getBoundingClientRect().right,
          centreDifference: Math.abs(reason.top + reason.height / 2 - refresh.top - refresh.height / 2)};
      });
      expect(positions.reasonLeft).toBeLessThan(positions.refreshLeft); expect(positions.reasonRight).toBeLessThanOrEqual(positions.refreshLeft);
      expect(positions.centreDifference).toBeLessThanOrEqual(4); expect(positions.headerRight - positions.refreshRight).toBeLessThanOrEqual(1);
      await expect(page.locator('.pm-heading button.primary:visible')).toHaveCount(0);
      await expect(page.locator('[data-pm-action]:visible button:enabled')).toHaveCount(0);
      if (panel === 'follow-up') {
        await expect(page.locator(`[data-pm-action="${action}"]`)).toBeHidden();
        await expect(page.locator(`[data-pm-action="${action}"] button[type=submit]`)).toBeDisabled();
        await expect(page.locator('[data-pm-follow-up-analyze]')).toBeHidden();
      } else await expect(page.locator('[data-pm-action]')).toHaveCount(0);
      expect(f.state.writes).toHaveLength(0);
    });
  }
  test(`read-only governance retains reading without write entries ${locale}`, async ({page}) => {
    const f = governance.governanceFixture(); f.state.manage = false; f.state.role = 'reader'; await governance.mount(page, f, locale);
    await page.locator(`[data-pm-governance-row="${f.state.records[1].record.record_id}"]`).getByRole('button').click();
    await expect(page.locator('[data-pm-governance-detail]')).toHaveAttribute('data-pm-governance-detail', f.state.records[1].record.record_id);
    await expect(page.getByText(reason, {exact: true})).toHaveCount(1); await expect(page.getByText(reason, {exact: true})).toBeVisible();
    for (const action of ['revise','decide','adoption.link']) await expect(governance.action(page, action)).toHaveCount(0);
    await expect(page.getByText(zh ? '修訂這筆事項' : 'Revise finding', {exact: true})).toHaveCount(0);
    await expect(page.getByRole('button', {name: zh ? '回報解決' : 'Resolve', exact: true})).toHaveCount(0);
    await page.locator('[data-pm-governance-detail] > details > summary').click();
    await page.getByText(zh ? '歷史' : 'History', {exact: true}).click();
    await expect(page.getByText(zh ? '#1 · 擁有者 · 記錄事項' : '#1 · Owner · Finding recorded', {exact: true})).toBeVisible();
    expect(f.state.writes).toEqual([]);
  });
  test(`owner governance entries and drafts survive permission changes ${locale}`, async ({page}) => {
    const f = governance.governanceFixture(); await governance.mount(page, f, locale);
    await page.locator(`[data-pm-governance-row="${f.state.records[1].record.record_id}"]`).getByRole('button').click();
    await expect(page.getByText(zh ? '修訂這筆事項' : 'Revise finding', {exact: true})).toBeVisible();
    await expect(page.getByRole('button', {name: zh ? '回報解決' : 'Resolve', exact: true})).toBeVisible();
    for (const action of ['revise','decide','adoption.link']) {await expect(governance.action(page, action)).toHaveCount(1); await governance.openAction(page, action);}
    const title = governance.action(page, 'revise').getByLabel(zh ? '事項標題' : 'Finding title', {exact: true});
    const decision = governance.action(page, 'decide').getByLabel(zh ? '本次記錄的理由' : 'Reason for this record', {exact: true});
    const adoption = governance.action(page, 'adoption.link').getByLabel(zh ? '本次記錄的理由' : 'Reason for this record', {exact: true});
    await title.fill('Keep the revision draft'); await decision.fill('Keep the direction draft'); await adoption.fill('Keep the adoption draft');
    const stored = () => page.evaluate(() => Object.fromEntries(Object.entries(localStorage).filter(([key]) => key.includes('pm.governance.'))));
    const drafts = await stored();
    f.state.role = 'contributor'; await page.evaluate(() => (window as any).refreshGovernance());
    await expect(governance.action(page, 'revise')).toHaveCount(1);
    await expect(governance.action(page, 'decide')).toHaveCount(0); await expect(governance.action(page, 'adoption.link')).toHaveCount(0);
    expect(await stored()).toEqual(drafts);
    f.state.role = 'reader'; f.state.manage = false; await page.evaluate(() => (window as any).refreshGovernance());
    await expect(page.locator('[data-pm-action]')).toHaveCount(0); expect(await stored()).toEqual(drafts); expect(f.state.writes).toEqual([]);
    await page.reload(); await governance.boot(page, locale); await expect(page.locator('[data-pm-action]')).toHaveCount(0); expect(await stored()).toEqual(drafts);
    f.state.role = 'owner'; f.state.manage = true; await page.evaluate(() => (window as any).refreshGovernance());
    for (const action of ['revise','decide','adoption.link']) await governance.openAction(page, action);
    await expect(title).toHaveValue('Keep the revision draft'); await expect(decision).toHaveValue('Keep the direction draft'); await expect(adoption).toHaveValue('Keep the adoption draft');
    await expect(governance.action(page, 'adoption.link').locator('button[type=submit]')).toBeDisabled();
    await expect(page.getByRole('button', {name: zh ? '回報解決' : 'Resolve', exact: true})).toBeVisible();
    expect(await stored()).toEqual(drafts); expect(f.state.writes).toEqual([]);
  });
  test(`meeting drafts return after a permission downgrade and reload ${locale}`, async ({page}) => {
    const f = meetings.meetingFixture(); await meetings.mount(page, f, locale);
    await meetings.openAction(page, 'note'); await meetings.openAction(page, 'draft');
    const note = meetings.action(page, 'note').locator('textarea').first(), draft = meetings.action(page, 'draft').locator('textarea').first();
    await note.fill('Keep the discussion note'); await draft.fill('Keep the planning draft');
    const stored = () => page.evaluate(() => Object.fromEntries(Object.entries(localStorage).filter(([key]) => key.includes('pm.meeting.'))));
    const drafts = await stored(); f.state.manage = false; f.state.role = 'reader';
    await page.evaluate(() => (window as any).refreshMeetings()); await expect(page.locator('[data-pm-action]')).toHaveCount(0);
    await expect(page.getByText(reason, {exact: true})).toHaveCount(1); expect(await stored()).toEqual(drafts); expect(f.state.writes).toEqual([]);
    await page.reload(); await meetings.boot(page, locale); await expect(page.locator('[data-pm-action]')).toHaveCount(0); expect(await stored()).toEqual(drafts);
    f.state.manage = true; f.state.role = 'owner'; await page.evaluate(() => (window as any).refreshMeetings());
    await meetings.openAction(page, 'note'); await meetings.openAction(page, 'draft');
    await expect(note).toHaveValue('Keep the discussion note'); await expect(draft).toHaveValue('Keep the planning draft');
    await expect(meetings.action(page, 'complete')).toHaveCount(1); await expect(meetings.action(page, 'complete').locator('button[type=submit]')).toBeDisabled();
    expect(await stored()).toEqual(drafts); expect(f.state.writes).toEqual([]);
  });
  test(`governance toggle retains its label after selection ${locale}`, async ({page}) => {
    const f = governance.governanceFixture(); await governance.mount(page, f, locale);
    const row = page.locator(`[data-pm-governance-row="${f.state.records[1].record.record_id}"]`), toggle = row.getByRole('button');
    const label = zh ? '查看事項' : 'View finding';
    await expect(toggle).toHaveText(label); await expect(toggle).toHaveAttribute('aria-pressed', 'false');
    await toggle.click(); await expect(toggle).toHaveAttribute('aria-pressed', 'true'); await expect(toggle).toHaveText(label);
    await expect(row).toHaveClass(/is-selected/); expect(f.state.writes).toHaveLength(0);
  });
  test(`governance punctuation and actors ${locale}`, async ({page}) => {
    const f = governance.governanceFixture(); f.state.records[0].revision.document.owner_actor = 'reader';
    await governance.mount(page, f, locale);
    await expect(page.locator(`[data-pm-governance-row="${f.state.records[0].record.record_id}"]`)).toContainText(zh ? '讀者' : 'Reader');
    await page.locator('[data-pm-governance-detail] > details > summary').click();
    await expect(page.getByText((zh ? '觸發條件：' : 'Trigger: ') + 'Before release', {exact: true})).toBeVisible();
    await expect(page.getByText(zh ? '檢視時間：未安排' : 'Review time: Not scheduled', {exact: true})).toBeVisible();
    await page.getByText(zh ? '歷史' : 'History', {exact: true}).click();
    await expect(page.getByText(zh ? '#1 · 擁有者 · 記錄事項' : '#1 · Owner · Finding recorded', {exact: true})).toBeVisible();
    expect(f.state.writes).toHaveLength(0);
  });
  test(`meeting title picker, spacing and actors ${locale}`, async ({page}) => {
    const f = meetings.meetingFixture(); const detail = f.state.meetings[0];
    detail.notes.push({meeting_id:detail.meeting.meeting_id, sequence:1, actor:'developer', content:'Observed discussion', source_refs:[]});
    detail.meeting.draft_revision_id = 'pmmd_' + 'a'.repeat(32);
    detail.draft = {meeting_id:detail.meeting.meeting_id, revision_id:detail.meeting.draft_revision_id, document:{kind:'planning',
      output:{why:'Shared goal', delivery_approach:'Review the fixed work', capacity_discussion:'Availability discussed', dependency_notes:[]},
      decisions:[], follow_ups:[{id:'verify', text:'Review the result', owner_actor:'reader', expected_effect:'Evidence improves', verification:'Observe the result', source_refs:[]}],
      unresolved_questions:[], source_refs:[]}};
    await meetings.mount(page, f, locale);
    const picker = page.getByLabel(zh ? '會議' : 'Meeting', {exact: true});
    await expect(picker.locator('option')).toHaveText(['Account recovery discussion']);
    const pickerBottom = await picker.evaluate(node => node.getBoundingClientRect().bottom);
    const pillsTop = await page.locator('[data-pm-meeting-selected] .pm-fact-heading').evaluate(node => node.getBoundingClientRect().top);
    expect(pillsTop - pickerBottom).toBeGreaterThanOrEqual(12);
    await expect(page.locator('[data-pm-meeting-create]')).toHaveText(zh ? '新增衝刺討論' : 'New discussion');
    await page.getByText(zh ? '已儲存的討論草稿' : 'Saved discussion draft', {exact:true}).click();
    await expect(page.getByText(zh ? '負責人：讀者' : 'Responsible: Reader', {exact:true})).toBeVisible();
    await expect(page.getByText((zh ? '如何核對結果：' : 'How the result will be checked: ') + 'Observe the result', {exact:true})).toBeVisible();
    await page.getByText(zh ? '詳細資訊' : 'Details', {exact: true}).first().click();
    await page.getByText(zh ? '討論與來源歷史' : 'History', {exact: true}).click();
    await page.getByText(zh ? '討論紀錄' : 'Discussion notes', {exact: true}).click();
    await expect(page.getByText(zh ? '#1 · 開發者' : '#1 · Developer', {exact: true})).toBeVisible();
    expect(f.state.writes).toHaveLength(0);
  });
  test(`follow-up named gaps, punctuation and actors ${locale}`, async ({page}) => {
    const f = followUp.fixture(); f.state.rows.forEach((row: any) => {row.created_by = 'owner';});
    await followUp.mount(page, f, {locale});
    await expect(page.locator('[data-pm-fu-missing]')).toHaveText(zh ? '尚無基準輸入：治理、時程、利害關係人、資源、品質' : 'No baseline input: Governance, Schedule, Stakeholders, Resources, Quality');
    await expect(page.locator('.pm-fu-meta')).toContainText(zh ? '準備時間：' : 'Prepared at: ');
    await expect(page.locator('.pm-fu-meta')).toContainText(zh ? '證據快照時間：' : 'Evidence snapshot at: ');
    await expect(page.locator('[data-pm-fu-domain=finance]')).toContainText(zh ? '後續討論資訊：' : 'Later discussion input: ');
    await page.getByText(zh ? '討論準備歷史' : 'Preparation history', {exact: true}).click();
    await expect(page.getByLabel(zh ? '已保存的討論準備' : 'Saved preparation', {exact: true}).locator('option').last()).toHaveText(zh ? '#2 · 擁有者' : '#2 · Owner');
    expect(f.state.writes).toHaveLength(0);
  });
}
