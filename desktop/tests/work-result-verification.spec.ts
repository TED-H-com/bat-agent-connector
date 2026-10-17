import {test, expect} from '@playwright/test';
import {buildResultEnvelope} from './work-result-fixture.ts';
import {qualityFixture, mountQuality, bootQuality, panel, editor, VERIFY, CREATE, workspaceId, workId, packageId, childId, candidate} from './pm-quality-reviews-fixture.ts';

function fixture() {
  const f=qualityFixture();f.state.verifyEnabled=true;f.state.evidence=[];
  f.state.resultEnvelope=buildResultEnvelope({workspace_id:workspaceId,
    identity:{workspace_id:workspaceId,work_item_id:workId,attempt_id:'att_'+'1'.repeat(32),package_id:packageId,execution_operation_id:childId},
    fixed_commit:{commit_sha:candidate,source_kind:'turn_end_observation'},
    next_allowed_actions:[{kind:'action',action:VERIFY,target:{workspace_id:workspaceId,execution_operation_id:childId}},
      {kind:'action',action:CREATE,target:{workspace_id:workspaceId,work_item_id:workId}}]});
  return f;
}
const verifier=(page:any)=>panel(page).locator('[data-pm-execution-verification]');
const refresh=(page:any)=>page.evaluate(()=>(window as any).qualityRefresh());
async function open(page:any,zh=false){
  await page.getByRole('button',{name:zh?'審閱這份成果':'Review this result',exact:true}).click();
  await expect(editor(page).locator('select').first()).toHaveCount(1);
}

for(const [locale,width] of [['en',1440],['zh-TW',768],['zh-TW',390]] as const)
test(`verify the fixed Result without selecting an internal execution: ${locale} ${width}`,async({page},info)=>{
  const f=fixture(),zh=locale==='zh-TW';await page.setViewportSize({width,height:900});
  await mountQuality(page,f,locale,true);await open(page,zh);
  await page.screenshot({path:info.outputPath('before-open.png'),fullPage:true});
  expect(f.state.reads.filter((path:string)=>path.includes('/verification-config'))).toHaveLength(0);
  await verifier(page).locator('summary').first().click();
  await expect(editor(page,VERIFY).locator('button[type=submit]')).toBeEnabled();
  await expect(verifier(page).getByLabel(zh?'原始實作操作':'Original implementation operation',{exact:true})).toBeHidden();
  expect(f.state.writes).toHaveLength(0);
  await page.screenshot({path:info.outputPath('fixed-verifier.png'),fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await editor(page,VERIFY).locator('button[type=submit]').click();
  await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body).toEqual({action:VERIFY,target:{workspace_id:workspaceId,execution_operation_id:childId},
    params:{candidate_commit:candidate,tree_hash:f.state.config.tree_hash,command_digest:f.state.config.command_digest},
    preconditions:{handoff_contract_digest:f.state.config.handoff_contract_digest}});
  const conclusion=page.locator('[data-work-result] [data-check=verification]');
  await expect(conclusion).toHaveText(zh?'已驗證':'Verified');
  await expect(conclusion).toBeFocused();await expect(conclusion).toBeInViewport();
  await page.screenshot({path:info.outputPath('verified-conclusion.png'),fullPage:true});
  await panel(page).locator('[data-quality-create]>summary').click();
  await expect(editor(page).getByLabel(zh?'指定驗證':'Required verification',{exact:true}).first()
    .locator(`option[value="${f.state.verifyRecords[0].operation_id}"]`)).toHaveCount(1);
  expect(f.state.reviews).toHaveLength(0);
});

for(const reason of ['different candidate','no configured command','foreign controller','different execution target','missing operate'])
test(`fixed Result verifier cannot run: ${reason}`,async({page})=>{
  const f=fixture();
  if(reason==='different candidate')f.state.config.candidate_commit='b'.repeat(40);
  if(reason==='no configured command'){f.state.config.available=false;f.state.config.command_digest=null;}
  if(reason==='foreign controller')f.state.resultEnvelope.next_allowed_actions=[];
  if(reason==='different execution target')f.state.resultEnvelope.next_allowed_actions[0].target.execution_operation_id='op_'+'9'.repeat(32);
  if(reason==='missing operate')f.state.scopes=['observe','manage'];
  await mountQuality(page,f,'en',true);await open(page);await verifier(page).locator('summary').first().click();
  await expect(editor(page,VERIFY).locator('button[type=submit]')).toBeDisabled();
  if(reason==='different candidate')await expect(verifier(page)).toContainText('The current checkout differs from this fixed result');
  if(reason==='no configured command'){
    await expect(verifier(page)).toContainText('The original project has no available trusted verifier.');
    await expect(verifier(page)).not.toContainText('The current checkout differs');
  }
  expect(f.state.writes).toHaveLength(0);
});

test('withdrawing only the offered verifier action retires its form',async({page})=>{
  const f=fixture();await mountQuality(page,f,'en',true);await open(page);await verifier(page).locator('summary').first().click();
  await expect(editor(page,VERIFY).locator('button[type=submit]')).toBeEnabled();
  f.state.resultEnvelope.next_allowed_actions=f.state.resultEnvelope.next_allowed_actions.filter((row:any)=>row.action!==VERIFY);
  await refresh(page);await expect(editor(page,VERIFY).locator('button[type=submit]')).toBeDisabled();
  expect(f.state.writes).toHaveLength(0);
});

test('late verification configuration cannot restore a withdrawn Result',async({page})=>{
  const f=fixture(),dispatch=f.dispatch;let release!:()=>void;const held=new Promise<void>(resolve=>{release=resolve;});
  f.dispatch=async input=>{const result=await dispatch(input);if(input.path.endsWith('/verification-config'))await held;return result;};
  await mountQuality(page,f,'en',true);await open(page);await verifier(page).locator('summary').first().click();
  await expect.poll(()=>f.state.reads.some((path:string)=>path.includes('/verification-config'))).toBe(true);
  f.state.deny=true;await refresh(page);release();
  await expect(panel(page)).toHaveCount(0);await expect(page.locator('[data-work-result]')).toBeHidden();
  expect(f.state.writes).toHaveLength(0);
});


test('an unknown fixed Result verification retains its original request after a checkout change',async({page})=>{
  const f=fixture();await mountQuality(page,f,'en',true);await open(page);await verifier(page).locator('summary').first().click();
  f.state.lost=true;await editor(page,VERIFY).locator('button[type=submit]').click();
  await expect(editor(page,VERIFY)).toContainText('Reply unavailable');
  const original=structuredClone(f.state.writes[0]);f.state.config.candidate_commit='9'.repeat(40);
  await page.reload();await bootQuality(page,'en',true);await open(page);await verifier(page).locator('summary').first().click();
  await editor(page,VERIFY).getByRole('button',{name:'Check original request',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(2);expect(f.state.writes[1]).toEqual(original);
  expect(f.state.verifyRecords).toHaveLength(1);
});


test('a delayed verification result preserves focus moved to another draft',async({page})=>{
  const f=fixture();await mountQuality(page,f,'en',true);await open(page);await verifier(page).locator('summary').first().click();
  await expect(editor(page,VERIFY).locator('button[type=submit]')).toBeEnabled();
  let release!:()=>void;f.state.gate=new Promise<void>(resolve=>{release=resolve;});
  const reads=f.state.reads.filter((p:string)=>p.includes('/work-packages')).length;
  await editor(page,VERIFY).locator('button[type=submit]').click();
  await expect.poll(()=>f.state.reads.filter((p:string)=>p.includes('/work-packages')).length).toBeGreaterThan(reads);
  await page.evaluate(()=>{const field=document.createElement('textarea');field.setAttribute('aria-label','Next draft');document.body.append(field);field.focus();});
  const field=page.getByLabel('Next draft',{exact:true});await field.fill('Keep editing here');
  release();f.state.gate=null;await expect(editor(page,VERIFY).locator('button[type=submit]')).toBeEnabled();
  await expect(field).toBeFocused();await expect(field).toHaveValue('Keep editing here');
  expect(f.state.writes).toHaveLength(1);
});
