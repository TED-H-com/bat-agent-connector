import {test,expect} from '@playwright/test';
import {guidedReviewFixture,seedGuidedReview,workspaceId} from './pm-sprint-review-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {openPmSection} from './pm-navigation.ts';
const route=`/dashboard/#/pm/${workspaceId}`;
for(const native of [false,true])test(`Review draft keyboard handoff keeps authored editor and selection ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=guidedReviewFixture();seedGuidedReview(f,3);await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'discussions','ui_meetings');
  const button=page.locator('[data-pm-review-step="2"] button'),form=page.locator('[data-pm-action="pm.meeting.draft"]'),editor=form.getByLabel('Goal impact',{exact:true});
  await button.focus();await page.keyboard.press('Enter');await expect(editor).toBeFocused();
  await editor.fill('Authored observation with selection');
  await editor.evaluate((node:HTMLTextAreaElement)=>{node.setSelectionRange(3,11);(window as any).__reviewEditor=node;});
  await button.focus();await page.keyboard.press('Enter');await expect(editor).toBeFocused();
  expect(await editor.evaluate((node:HTMLTextAreaElement)=>({same:node===(window as any).__reviewEditor,text:node.value,start:node.selectionStart,end:node.selectionEnd}))).toEqual({same:true,text:'Authored observation with selection',start:3,end:11});
});
for(const native of [false,true])test(`Review governance keyboard handoff focuses enabled authored title ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=guidedReviewFixture();seedGuidedReview(f,4);f.governance.state.records=[];f.governance.state.history={};
  await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'discussions','ui_meetings');
  const button=page.locator('[data-pm-review-step="4"]').getByRole('button',{name:'Record finding',exact:true});
  await button.focus();await page.keyboard.press('Enter');
  const form=page.locator('[data-pm-action="pm.governance.record"]');await expect(form).toBeVisible();
  await expect(form.getByLabel('Finding title',{exact:true})).toBeFocused();await expect(form.getByLabel('Finding title',{exact:true})).toBeEnabled();
});
