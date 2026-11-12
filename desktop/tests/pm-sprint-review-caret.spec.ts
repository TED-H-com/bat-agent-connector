import {test,expect} from '@playwright/test';
import {guidedReviewFixture,seedGuidedReview,workspaceId} from './pm-sprint-review-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {openPmSection} from './pm-navigation.ts';
for(const native of [false,true])test(`same unsent Review proposal restores authored field and range ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=guidedReviewFixture();seedGuidedReview(f,4);f.governance.state.records=[];f.governance.state.history={};
  await mountConversation(page,native,f.dispatch);await page.goto(`/dashboard/#/pm/${workspaceId}`);await openPmSection(page,'discussions','ui_meetings');
  const button=page.locator('[data-pm-review-step="4"]').getByRole('button',{name:'Record finding',exact:true});
  await button.press('Enter');const form=page.locator('[data-pm-action="pm.governance.record"]');
  for(const name of ['Finding title','Reason for this record']){
    const field=form.getByLabel(name,{exact:true});await field.fill('USER_AUTHORED_FINDING 核對');await field.evaluate((n:HTMLInputElement)=>n.setSelectionRange(2,8,'backward'));
    await openPmSection(page,'discussions','ui_meetings');await button.press('Enter');await expect(field).toBeFocused();
    expect(await field.evaluate((n:HTMLInputElement)=>({text:n.value,start:n.selectionStart,end:n.selectionEnd,direction:n.selectionDirection}))).toEqual({text:'USER_AUTHORED_FINDING 核對',start:2,end:8,direction:'backward'});
  }
  expect(f.state.requests.filter((r:any)=>r.method!=='GET')).toEqual([]);expect(f.state.providerCalls).toBe(0);
});
