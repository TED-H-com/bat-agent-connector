import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {proposalFixture,mountProposal,bootProposal,selectProposal,panel,editor,PROPOSE,ADOPT,proposalId,workspaceId,threadId} from './pm-plan-proposals-fixture.ts';

test('natural discussion prepares and adopts one whole reviewed plan without field confirmations',async({page})=>{
  const f=proposalFixture();await mountProposal(page,f);await editor(page,PROPOSE).getByLabel('What should this plan update cover?').fill('Prepare paid member login and reset; no referral codes');
  await editor(page,PROPOSE).getByRole('button',{name:'Prepare plan update',exact:true}).click();await expect(editor(page)).toBeVisible();
  const row=editor(page).locator('[data-proposal-candidate="login"]');await row.locator('summary').first().click();await row.getByLabel('Reviewed wording').fill('Paid members can log in using their existing account');
  await editor(page).getByLabel('Reason for adopting this update').fill('Adopt the focused paid-member flow and retain provider uncertainty');
  await editor(page).getByRole('button',{name:'Adopt this update',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(2);
  expect(f.state.writes.map((w:any)=>w.body.action)).toEqual([PROPOSE,ADOPT]);const {params,preconditions,target}=f.state.writes[1].body;
  expect(target).toEqual({workspace_id:workspaceId,thread_id:threadId});expect(params.proposal_id).toBe(proposalId);
  expect(params.review.rows).toEqual([{key:'login',text:'Paid members can log in using their existing account',status:'user_confirmed',include:true},
    {key:'reset',text:'Password reset links expire after one use',status:'user_confirmed',include:true},{key:'provider',text:'Existing identity provider supports reset links',status:'agent_assumption',include:true},
    {key:'referral',text:'Referral codes are outside this release',status:'not_applicable',include:false}]);
  expect(params.review.requirements).toEqual(f.state.proposals[0].document.requirements);
  expect(preconditions).toEqual({expected_version:4,expected_plan_version:0,expected_accepted_revision_id:null,expected_proposal_digest:'a'.repeat(64)});
});
test('sources render as literal quotes and reviewed edits survive unchanged refresh and remount',async({page})=>{
  const f=proposalFixture();f.save();await mountProposal(page,f);await selectProposal(page);const row=editor(page).locator('[data-proposal-candidate="login"]');await row.locator('summary').first().click();
  await row.getByLabel('Reviewed wording').fill('A precise revised login requirement');await row.getByText('Original sources',{exact:true}).click();
  await expect(row.locator('blockquote')).toContainText('<img src=x onerror=alert(1)>');await expect(row.locator('img')).toHaveCount(0);
  await page.evaluate(()=>(window as any).proposalRefresh());await expect(row.getByLabel('Reviewed wording')).toHaveValue('A precise revised login requirement');
  await page.reload();await bootProposal(page);await editor(page).locator('[data-proposal-candidate="login"]>summary').click();await expect(editor(page).getByLabel('Reviewed wording').first()).toHaveValue('A precise revised login requirement');
});
test('ordinary requirement controls add criteria and keep excluded information out',async({page})=>{
  const f=proposalFixture();f.save();await mountProposal(page,f);await selectProposal(page);await editor(page).getByRole('button',{name:'Add requirement',exact:true}).click();
  const req=editor(page).locator('[data-proposal-requirement]').last();await req.locator('summary').click();await req.getByLabel('Stable requirement ID').fill('password_reset');await req.getByLabel('Requirement source').selectOption('reset');
  await req.getByLabel('Acceptance criteria').selectOption(['reset']);await req.getByLabel('Depends on requirements').selectOption(['member_login']);
  expect(await req.getByLabel('Requirement source').locator('option[value="referral"]').count()).toBe(0);
  await editor(page).getByLabel('Reason for adopting this update').fill('Select exact information and dependency');await editor(page).getByRole('button',{name:'Adopt this update',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.params.review.requirements[1]).toEqual({id:'password_reset',source_key:'reset',acceptance_keys:['reset'],priority:'unresolved',depends_on:['member_login']});
});
test('excluding a selected prerequisite blocks apply until review is consistent',async({page})=>{
  const f=proposalFixture();f.save();await mountProposal(page,f);await selectProposal(page);const row=editor(page).locator('[data-proposal-candidate="reset"]');await row.locator('summary').first().click();await row.getByLabel('Include in this plan').uncheck();
  await expect(editor(page).getByRole('button',{name:'Adopt this update',exact:true})).toBeDisabled();await expect(editor(page)).toContainText('Select included information');expect(f.state.writes).toEqual([]);
});
test('known baseline changes preserve edits and refuse a fresh adoption',async({page})=>{
  const f=proposalFixture();f.save();await mountProposal(page,f);await selectProposal(page);await editor(page).getByLabel('Plan title',{exact:true}).fill('Retained draft');f.state.head.version++;
  await page.evaluate(()=>(window as any).proposalRefresh());await expect(editor(page).getByLabel('Plan title',{exact:true})).toHaveValue('Retained draft');await expect(editor(page)).toContainText('discussion or plan has changed');await expect(editor(page).locator('button[type=submit]')).toBeDisabled();
});
test('unknown admission retries the exact original key after the discussion moves',async({page})=>{
  const f=proposalFixture();f.save();f.state.lost=true;await mountProposal(page,f);await selectProposal(page);await editor(page).getByLabel('Reason for adopting this update').fill('One original decision');
  await editor(page).locator('button[type=submit]').click();await expect(editor(page).getByRole('button',{name:'Check original request',exact:true})).toBeVisible();f.state.thread.thread.version=9;f.state.head.version=4;
  await page.evaluate(()=>(window as any).proposalRefresh());await editor(page).getByRole('button',{name:'Check original request',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(2);
  expect(f.state.writes[1]).toEqual(f.state.writes[0]);expect(f.state.ops.size).toBe(1);
});
test('source withdrawal removes summaries quotes rows and counts while preserving private request recovery',async({page})=>{
  const f=proposalFixture();f.save();await mountProposal(page,f);await selectProposal(page);await editor(page).getByLabel('Reason for adopting this update').fill('Private saved decision');f.state.deny=true;
  await page.evaluate(()=>(window as any).proposalRefresh());await expect(panel(page)).not.toContainText('Paid members can log in');await expect(panel(page)).not.toContainText('Referral codes');await expect(editor(page)).toHaveCount(0);await expect(panel(page)).toContainText('Original sources withdrawn');
  f.state.deny=false;await page.evaluate(()=>(window as any).proposalRefresh());await expect(editor(page).getByLabel('Reason for adopting this update')).toHaveValue('Private saved decision');
});
test('invalid structured output preserves raw analysis and has no adoption form',async({page})=>{
  const f=proposalFixture(),p=f.save();p.validation={status:'needs_review',errors:['Unknown source reference']};p.document=null;p.raw_reply='Unverified claim that everything is accepted';await mountProposal(page,f);
  await panel(page).getByRole('combobox',{name:'Saved proposal',exact:true}).selectOption(proposalId);await expect(panel(page)).toContainText('reply needs review');await expect(editor(page)).toHaveCount(0);await panel(page).getByText('Original analysis reply',{exact:true}).click();await expect(panel(page)).toContainText(p.raw_reply);expect(f.state.writes).toEqual([]);
});
test('an older central receives no proposal requests',async({page})=>{const f=proposalFixture();f.state.available=false;await mountProposal(page,f);expect(f.state.reads.filter((p:string)=>p.includes('conversation-proposals'))).toEqual([]);await expect(panel(page)).toBeHidden();});
test('readers inspect fixed proposals but cannot adopt or prepare',async({page})=>{const f=proposalFixture();f.save();f.state.role='reader';f.state.thread.capabilities={write:false,decide:false};await mountProposal(page,f);await selectProposal(page);await expect(editor(page).locator('button[type=submit]')).toBeDisabled();await expect(editor(page,PROPOSE).locator('button[type=submit]')).toBeDisabled();});
test('switching proposals preserves each original reviewed draft',async({page})=>{
  const f=proposalFixture(),p=f.save(),other=structuredClone(p);other.proposal_id='pmcp_'+'8'.repeat(32);other.document.title='Another fixed proposal';f.state.proposals.push(other);
  await mountProposal(page,f);await selectProposal(page);await editor(page).getByLabel('Plan title',{exact:true}).fill('Original reviewed draft');await editor(page).getByLabel('Reason for adopting this update').fill('Original rationale');
  await selectProposal(page,other.proposal_id);await editor(page).getByLabel('Plan title',{exact:true}).fill('Second reviewed draft');await selectProposal(page);
  await expect(editor(page).getByLabel('Plan title',{exact:true})).toHaveValue('Original reviewed draft');await expect(editor(page).getByLabel('Reason for adopting this update')).toHaveValue('Original rationale');
});
test('late source response cannot repopulate a withdrawn review',async({page})=>{
  const f=proposalFixture();f.save();await mountProposal(page,f);let release:any;f.state.gate=new Promise(resolve=>{release=resolve;});
  await panel(page).getByRole('combobox',{name:'Saved proposal',exact:true}).selectOption(proposalId);await page.evaluate(()=>(window as any).proposalWithdraw());release();await expect(panel(page)).not.toContainText('Paid members can log in');await expect(editor(page)).toHaveCount(0);
});
test('pagination retains a visibility-filtered page without treating it as the complete history',async({page})=>{
  const f=proposalFixture();f.save();f.state.partial=true;await mountProposal(page,f);await expect(panel(page)).toContainText('partial list');await panel(page).getByRole('button',{name:'Load more proposals',exact:true}).click();await selectProposal(page);await expect(editor(page)).toBeVisible();
});
test('removing original requirements is visible and included in the one reviewed request',async({page})=>{
  const f=proposalFixture(),p=f.save();p.context.accepted_plan={document:{title:'Original commitment',requirements:[{id:'member_login',text:'Original member login'},{id:'referral_codes',text:'Original paid referral program'}]}};
  p.document.removed_requirement_ids=['referral_codes'];await mountProposal(page,f);await selectProposal(page);const removed=editor(page).locator('[data-proposal-removed]');
  await expect(removed).toContainText('referral_codes');await expect(removed).toContainText('Original paid referral program');await editor(page).locator('[data-proposal-requirement]>summary').click();await editor(page).getByRole('button',{name:'Remove requirement',exact:true}).click();
  await expect(removed).toContainText('Original member login');await editor(page).getByLabel('Reason for adopting this update').fill('Explicitly replace the original commitments with reviewed scope');
  await editor(page).getByRole('button',{name:'Adopt this update',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params.review.removed_requirement_ids).toEqual(['member_login','referral_codes']);
});
test('a proposal that omits original commitments without declaring removal fails closed',async({page})=>{
  const f=proposalFixture(),p=f.save();p.context.accepted_plan={document:{title:'Original commitment',requirements:[{id:'original_scope',text:'Original commitment must not disappear'}]}};
  await mountProposal(page,f);await panel(page).getByRole('combobox',{name:'Saved proposal',exact:true}).selectOption(proposalId);await expect(panel(page)).toContainText('does not match its original discussion or sources');await expect(editor(page)).toHaveCount(0);
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`proposal review ${locale} ${width}`,async({page},info)=>{
  const f=proposalFixture();f.save();await page.setViewportSize({width,height:900});await mountProposal(page,f,locale);await panel(page).locator('select').filter({has:page.locator(`option[value="${proposalId}"]`)}).selectOption(proposalId);await expect(editor(page)).toBeVisible();
  expect(await page.evaluate(()=>[...document.querySelectorAll('main *')].filter(node=>node.getBoundingClientRect().right>innerWidth+1).map(node=>({tag:node.tagName,class:node.className})))).toEqual([]);
  expect(await panel(page).innerText()).not.toMatch(/pcp_[a-z_]+/);const out=process.env.BATC_PM_PROPOSAL_SCREENSHOTS||info.outputPath('screenshots');await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/proposal-${locale}-${width}.png`,fullPage:true});
});
