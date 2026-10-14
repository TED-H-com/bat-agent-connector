import {expect, test} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {governanceFixture} from './pm-governance-fixture.ts';
import {meetingFixture} from './pm-meetings-fixture.ts';
import {workspaceId, pmFixture} from './pm-fixture.ts';
import {openPmSection} from './pm-navigation.ts';

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) test(`unselected register governance shows only its selection hint ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: 1000}}), page = await context.newPage();
  try {
    const demo = demoFixture({locale}), writes: any[] = [];
    await mountConversation(page, false, input => {if (input.method === 'POST') writes.push(input); return demo.dispatch(input);});
    await page.goto(`/dashboard/#/pm/${demo.ids.workspaceId}/issues`); await openPmSection(page, 'issues', 'ui_governance');
    const panel = page.locator('[data-pm-governance]'), hint = panel.locator('[data-pm-governance-prompt]');
    const sentence = locale === 'en' ? 'Select an item above.' : '從上方清單選取一筆事項。';
    await expect(hint).toBeVisible(); await expect.poll(() => panel.innerText()).toBe(sentence);
    await expect(panel.locator('.pm-heading')).toBeHidden(); await expect(panel.locator('[data-pm-action]')).toBeHidden();
    await expect(panel.locator('[data-pm-governance-row], [data-pm-governance-selected], [data-pm-governance-detail]')).toHaveCount(0);
    const height = await hint.evaluate(node => ({hint: node.getBoundingClientRect().height, body: node.parentElement!.getBoundingClientRect().height}));
    expect(height.body - height.hint).toBeLessThanOrEqual(1); expect(writes).toEqual([]);
  } finally {await context.close();}
});

for (const locale of ['en', 'zh-TW']) for (const kind of ['governance', 'meeting']) test(`register opens the selected ${kind} form and focuses it, ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale}); const page = await context.newPage();
  try {
    const base = pmFixture(), demo = demoFixture({locale}), governance = governanceFixture(), meetings = meetingFixture();
    governance.state.actor = base.state.actor; meetings.state.actor = base.state.actor;
    const selected = governance.state.records[1]; selected.revision.document.owner_actor = base.state.actor;
    const selectedMeeting = meetings.addMeeting('review', 'Second review'); selectedMeeting.meeting.status = 'completed';
    selectedMeeting.meeting.completion_id = 'pmmc_' + 'f'.repeat(32);
    selectedMeeting.completion = {meeting_id: selectedMeeting.meeting.meeting_id, completion_id: selectedMeeting.meeting.completion_id, actor: base.state.actor,
      document: {discussion_summary: 'Review outcomes', decisions: [], follow_ups: [{id: 'second-result', text: 'Verify the second result', owner_actor: base.state.actor}]}};
    const writes:any[] = [];
    const dispatch = async (input:any) => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (input.method === 'POST') {writes.push(input); throw Error('No form handoff may write');}
      if (path.endsWith('/register') || path.includes('/register/')) {
        const result = await demo.dispatch({...input,path:(path.includes('/register/') ? `/pm/workspaces/${demo.ids.workspaceId}/register/governance.pmgov_00000000000000000000000000000001` : input.path.replace(workspaceId,demo.ids.workspaceId))}), data:any = result.data;data.workspace_id=workspaceId;
        const row = {row_id: kind + '.selected', type: kind === 'governance' ? 'risk' : 'action', title: kind === 'governance' ? selected.revision.document.title : 'Second result', status: 'open', owner: base.state.actor,
          source: {kind, id: kind === 'governance' ? selected.record.record_id : selectedMeeting.meeting.meeting_id}, related: {requirement_ids: [], work_item_ids: [], prs: []}, unknowns: {},
          available_actions: [{action: kind === 'governance' ? 'pm.governance.revise' : 'pm.meeting.follow_up.record',
            target: kind === 'governance' ? {workspace_id: workspaceId, record_id: selected.record.record_id} : {workspace_id: workspaceId, meeting_id: selectedMeeting.meeting.meeting_id}, required: kind === 'meeting' ? {follow_up_id: 'second-result'} : {}}]};
        if (path.endsWith('/register')) {data.rows = [row]; data.next_cursor = null;}
        else {data.row = row; data.timeline = [];}
        return result;
      }
      if (path.includes('/governance/') || path.endsWith('/governance') || path.includes('/snapshots') || path.endsWith('/plans/accepted')) return governance.dispatch(input);
      if (path.includes('/meetings') || path.endsWith('/sprints')) return meetings.dispatch(input);
      const result = await base.dispatch(input), data:any = result.data;
      if (path === '/bootstrap' || path === '/capabilities') {
        const caps = path === '/bootstrap' ? data.capabilities : data;
        caps.features.pm_register={version:1};caps.features.pm_workspaces.governance=true;caps.features.pm_workspaces.meetings=true;caps.scopes.push('manage'); caps.actions.push(...governance.config().caps.actions, ...meetings.config().caps.actions);
      }
      if (data.workspace) {data.workspace.membership = {actor: base.state.actor, role: 'owner'}; data.workspace.members = [{actor: base.state.actor, role: 'owner'}];}
      return result;
    };
    await mountConversation(page, false, dispatch); await page.goto(`/dashboard/#/pm/${workspaceId}/issues`);
    if (kind === 'governance') {
      await openPmSection(page, 'issues', 'ui_governance');
      const panel = page.locator('[data-pm-governance]'), launch = panel.locator('[data-pm-governance-record]');
      const record = panel.locator('[data-pm-action="pm.governance.record"]'), title = record.getByLabel(locale === 'en' ? 'Finding title' : '事項標題', {exact: true});
      await expect(panel.locator('[data-pm-governance-prompt]')).toBeVisible(); await expect(launch).toBeVisible();
      await launch.click(); await expect(record).toBeVisible(); await expect(record.locator('input:not([type=hidden]),select,textarea').first()).toBeFocused();
      await expect(panel.locator('[data-pm-governance-prompt]')).toBeHidden(); await title.fill('Preserve the register creation draft');
      await record.getByRole('button', {name: locale === 'en' ? 'Cancel' : '取消', exact: true}).click();
      await expect(record).toBeHidden(); await expect(panel.locator('[data-pm-governance-prompt]')).toBeVisible();
      await page.reload(); await openPmSection(page, 'issues', 'ui_governance');
      await expect(record).toBeVisible(); await expect(title).toHaveValue('Preserve the register creation draft');
      await expect(launch).toHaveAttribute('aria-expanded', 'true'); await expect(panel.locator('[data-pm-governance-prompt]')).toBeHidden();
      expect(writes).toEqual([]);
    }
    await page.locator('[data-reg-table] tbody tr').first().click();
    const inspector = page.locator('.ui-inspector[data-reg-row]');
    await inspector.getByRole('button', {name: kind === 'governance' ? (locale === 'en' ? 'Revise record' : '修訂紀錄') : (locale === 'en' ? 'Record follow-up' : '記錄後續'), exact: true}).click();
    const form = page.locator(`[data-pm-action="${kind === 'governance' ? 'pm.governance.revise' : 'pm.meeting.follow_up.record'}"]`);
    await expect(form).toBeVisible();
    const first = form.locator('input:not([type=hidden]), select, textarea').first(); await expect(first).toBeFocused();
    if (kind === 'governance') {
      await expect(first).toHaveValue(selected.revision.document.title);
      await expect(page.locator('[data-pm-governance-row], [data-pm-governance-detail]')).toHaveCount(0);
      await expect(page.locator('[data-pm-governance-selected]')).toHaveAttribute('data-pm-governance-selected', selected.record.record_id);
    } else {await expect(first).toHaveValue('second-result'); await expect(page.locator('[data-pm-meeting-selected]')).toHaveAttribute('data-pm-meeting-selected', selectedMeeting.meeting.meeting_id);}
    expect(writes).toEqual([]);
  } finally {await context.close();}
});
