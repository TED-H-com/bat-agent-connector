import {expect,test} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture,demoRoute} from './demo/index.ts';
import {openPmSection} from './pm-navigation.ts';
for(const locale of ['en','zh-TW'])test(`demo assessment accepts its fixed plan and discussion sources, ${locale}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage();try{
    const f=demoFixture({locale}); await mountConversation(page,false,f.dispatch);await page.goto(demoRoute());await openPmSection(page,'issues','ui_follow_up');
    const panel=page.locator('[data-pm-follow-up]');await expect(panel).not.toContainText(locale==='en'?'does not match its fixed source':'評估與固定來源不符');
    await panel.getByRole('button',{name:new RegExp(locale==='en'?'^Assessment 1':'^評估 1')}).click();
    await expect(panel.locator('[data-pm-assessment-record]')).toBeVisible();await expect(panel.locator('.ui-inline-error')).toHaveCount(0);
    await expect(panel.locator('[data-pm-assessment-record] > p.muted').first()).toContainText(locale==='en'?'Reader':'讀者');
    await expect(panel).toContainText(locale==='en'?'It does not adopt scope changes or dispatch work.':'不會採用範圍變更或派工');
    await expect(panel).not.toContainText(locale==='en'?'No AI Follow-up':'尚未執行自動後續追蹤');
    expect(await panel.innerText()).not.toMatch(/\b(?:null|undefined|ui_details|pm_fu_)\b/);
    const governance=await f.dispatch({path:`/pm/workspaces/${f.ids.workspaceId}/governance`});const record=governance.data.records[0].record;
    const history=await f.dispatch({path:`/pm/workspaces/${f.ids.workspaceId}/governance/${record.record_id}/history`});expect(history.status).toBe(200);expect(history.data.context).toEqual(record.context);expect(history.data.revisions).toHaveLength(1);
  }finally{await context.close();}
});
