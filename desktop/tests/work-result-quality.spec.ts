import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {buildResultEnvelope} from './work-result-fixture.ts';
import {qualityFixture, mountQuality, panel, editor, CREATE, RECORD, workspaceId, workId, packageId, childId, candidate} from './pm-quality-reviews-fixture.ts';

function fixture() {
  const f = qualityFixture();
  f.state.resultEnvelope = buildResultEnvelope({workspace_id: workspaceId,
    identity: {workspace_id: workspaceId, work_item_id: workId, attempt_id: 'att_' + '1'.repeat(32), package_id: packageId, execution_operation_id: childId},
    fixed_commit: {commit_sha: candidate, source_kind: 'turn_end_observation'},
    next_allowed_actions: [{kind: 'action', action: CREATE, target: {workspace_id: workspaceId, work_item_id: workId}}]});
  return f;
}
const refresh = (page:any) => page.evaluate(() => (window as any).qualityRefresh());
async function open(page:any, zh=false) {
  await page.getByRole('button', {name: zh ? '審閱這份成果' : 'Review this result', exact:true}).click();
  await expect(panel(page)).toBeVisible();
  await expect(editor(page).locator('select').first()).toHaveCount(1);
}

for (const [locale, width] of [['en',1440],['en',768],['zh-TW',390]] as const) test(`Result review keeps the original candidate and submits the existing action: ${locale} ${width}`,async({page})=>{
  await page.setViewportSize({width,height:900});
  const f=fixture(), zh=locale==='zh-TW';
  await mountQuality(page,f,locale,true);
  expect(f.state.reads.some((p:string)=>p.includes('/quality'))).toBe(false);
  expect(f.state.writes).toHaveLength(0);
  await open(page,zh);
  await panel(page).locator('[data-quality-create]>summary').click();
  for(const [label,value] of [[zh?'原始工程套件':'Original package',packageId],[zh?'原始實作操作':'Original implementation operation',childId],[zh?'候選提交':'Candidate commit',candidate]]){
    await expect(editor(page).getByLabel(label,{exact:true})).toHaveValue(value);
    await expect(editor(page).getByLabel(label,{exact:true})).toBeDisabled();
  }
  await editor(page).getByLabel(zh?'負責審查者':'Responsible reviewer',{exact:true}).selectOption(f.state.actor);
  for(const field of await editor(page).getByLabel(zh?'指定驗證':'Required verification',{exact:true}).all())await field.selectOption(f.state.evidence[0].evidence_id);
  await editor(page).getByLabel(zh?'審查政策理由':'Reason for this review policy',{exact:true}).fill('Review the selected fixed Result against its original criteria.');
  await expect(editor(page).getByLabel(zh?'候選提交':'Candidate commit',{exact:true})).toBeDisabled();
  await expect(editor(page).locator('button[type=submit]')).toBeEnabled();
  await mkdir('test-results/result-quality',{recursive:true});
  await page.screenshot({path:`test-results/result-quality/${locale}-${width}.png`,fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await editor(page).locator('button[type=submit]').click();
  await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.action).toBe(CREATE);
  expect(f.state.writes[0].body.params).toMatchObject({package_ref:f.package_ref,execution_operation_id:childId,candidate_commit:candidate});
  expect(f.state.writes[0].body.params.criteria).toHaveLength(2);
  await expect(editor(page,RECORD).locator('button[type=submit]')).toBeEnabled();
});

test('appointed reviewer sees only this execution and commit; withdrawn actions disable writes',async({page})=>{
  const f=fixture(),current=f.save(),other=f.save(false,{candidate_commit:'b'.repeat(40)});
  f.state.resultEnvelope.next_allowed_actions=[{kind:'action',action:RECORD,target:{workspace_id:workspaceId,review_id:current.review_id}}];
  await mountQuality(page,f,'en',true);await open(page);
  await expect(panel(page).getByLabel('Saved review',{exact:true})).toHaveValue(current.review_id);
  await expect(panel(page).getByLabel('Saved review',{exact:true}).locator(`option[value="${other.review_id}"]`)).toHaveCount(0);
  await expect(editor(page,RECORD).locator('button[type=submit]')).toBeEnabled();
  await expect(panel(page).getByRole('button',{name:'Use this assessment',exact:true})).toHaveCount(0);
  f.state.resultEnvelope.next_allowed_actions=[];
  await refresh(page);
  await expect(editor(page,RECORD).locator('button[type=submit]')).toBeDisabled();
  expect(f.state.writes).toHaveLength(0);
});

test('a late review read cannot restore sources after Result withdrawal',async({page})=>{
  const f=fixture();f.save(true);let release!:()=>void;
  f.state.gate=new Promise<void>(resolve=>{release=resolve;});
  await mountQuality(page,f,'en',true);await page.getByRole('button',{name:'Review this result',exact:true}).click();
  await expect.poll(()=>f.state.reads.some((path:string)=>path.includes('/work-packages'))).toBe(true);
  f.state.deny=true;await refresh(page);release();f.state.gate=null;
  await expect(panel(page)).toHaveCount(0);
  await expect(page.locator('[data-work-result]')).toBeHidden();
  expect(f.state.writes).toHaveLength(0);
});

test('a foreign action target never grants review creation',async({page})=>{
  const f=fixture();f.state.resultEnvelope.next_allowed_actions[0].target.work_item_id='wi_'+'9'.repeat(20);
  await mountQuality(page,f,'en',true);await open(page);
  await panel(page).locator('[data-quality-create]>summary').click();
  await expect(editor(page).locator('button[type=submit]')).toBeDisabled();
  expect(f.state.writes).toHaveLength(0);
});

test('different fixed commits preserve separate review drafts without retargeting',async({page})=>{
  const f=fixture();await mountQuality(page,f,'en',true);await open(page);
  await panel(page).locator('[data-quality-create]>summary').click();
  await editor(page).getByLabel('Reason for this review policy',{exact:true}).fill('Original result rationale');
  f.state.resultEnvelope.changes.fixed_result_commit.commit_sha='b'.repeat(40);
  await refresh(page);await expect(panel(page)).toHaveCount(0);await open(page);
  await panel(page).locator('[data-quality-create]>summary').click();
  await expect(editor(page).getByLabel('Reason for this review policy',{exact:true})).toHaveValue('');
  await expect(editor(page).getByLabel('Candidate commit',{exact:true})).toHaveValue('b'.repeat(40));
  f.state.resultEnvelope.changes.fixed_result_commit.commit_sha=candidate;
  await refresh(page);await open(page);await panel(page).locator('[data-quality-create]>summary').click();
  await expect(editor(page).getByLabel('Reason for this review policy',{exact:true})).toHaveValue('Original result rationale');
  expect(f.state.writes).toHaveLength(0);
});

test('an unrelated Result update preserves the review draft focus and selection',async({page})=>{
  const f=fixture();await mountQuality(page,f,'en',true);await open(page);
  await panel(page).locator('[data-quality-create]>summary').click();
  const rationale=editor(page).getByLabel('Reason for this review policy',{exact:true});
  await rationale.fill('Still reviewing this fixed result');
  await rationale.evaluate((node:HTMLTextAreaElement)=>node.setSelectionRange(6,15));
  f.state.resultEnvelope.usage.context_tokens++;
  await refresh(page);
  await expect(rationale).toBeFocused();
  await expect(rationale).toHaveValue('Still reviewing this fixed result');
  expect(await rationale.evaluate((node:HTMLTextAreaElement)=>[node.selectionStart,node.selectionEnd])).toEqual([6,15]);
  expect(f.state.writes).toHaveLength(0);
});

test('source withdrawal removes the open review and no later result recreates it',async({page})=>{
  const f=fixture();f.save(true);await mountQuality(page,f,'en',true);await open(page);
  await expect(panel(page)).toContainText('Private recovery criterion');
  f.state.deny=true;await refresh(page);
  await expect(panel(page)).toHaveCount(0);
  await expect(page.locator('[data-work-result]')).toBeHidden();
  expect(f.state.writes).toHaveLength(0);
});

test('an unavailable original execution cannot be replaced by another work operation',async({page})=>{
  const f=fixture();f.state.operations[0].operation_id='op_'+'8'.repeat(32);
  await mountQuality(page,f,'en',true);await page.getByRole('button',{name:'Review this result',exact:true}).click();
  await expect(panel(page)).toContainText('Original quality sources are unavailable');
  await expect(editor(page)).toHaveCount(0);
  expect(f.state.writes).toHaveLength(0);
});
