import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {buildResultEnvelope} from './work-result-fixture.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';
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
for(const locale of ['en','zh-TW'])test(`a saved Result assessment opens PM with its exact receipt: ${locale}`,async({page})=>{
  const f=fixture(),row=f.save(true),zh=locale==='zh-TW';
  f.state.resultEnvelope.verification.quality_reviews.current={review_id:row.review_id,version:row.version};
  await mountQuality(page,f,locale,true);await open(page,zh);
  const link=panel(page).getByRole('link',{name:zh?'與 PM 討論這次評估':'Discuss this assessment with PM',exact:true});
  await expect(link).toBeVisible();
  const original=(await link.getAttribute('href'))!;
  const parsed=parseDiscussRefs(original.split('?')[1]);
  expect(parsed.refs).toEqual([{kind:'quality_assessment',id:row.assessment.operation_id,review_id:row.review_id,document_digest:row.assessment.document_digest},{kind:'work_item',id:workId}]);
  f.assess(row);f.state.resultEnvelope.verification.quality_reviews.current.version=row.version;await refresh(page);
  await expect(link).not.toHaveAttribute('href',original);
  expect(parseDiscussRefs(original.split('?')[1]).refs).toEqual(parsed.refs);
  expect(f.state.writes).toHaveLength(0);
});

for(const reason of ['older service','unsaved conclusion'])test(`${reason} has no assessment discussion link`,async({page})=>{
  const f=fixture();f.save(reason==='older service');if(reason==='older service')f.state.discussionContexts=[];
  await mountQuality(page,f,'en',true);await open(page);
  await expect(panel(page).getByRole('link',{name:'Discuss this assessment with PM',exact:true})).toHaveCount(0);
  expect(f.state.writes).toHaveLength(0);
});
async function open(page:any, zh=false) {
  await page.getByRole('button', {name: zh ? '審閱這份成果' : 'Review this result', exact:true}).click();
  await expect(panel(page)).toBeVisible();
  await expect(editor(page).locator('select').first()).toHaveCount(1);
}

async function fillUnassessed(page:any, zh=false) {
  const record=editor(page,RECORD);
  for(const field of await record.getByLabel(zh?'條件判斷理由':'Criterion rationale',{exact:true}).all())
    await field.fill('Independent verification is still missing for this original result.');
  await record.getByLabel(zh?'這次評估的理由':'Reason for this assessment',{exact:true})
    .fill('Keep the original requirement unassessed.');
}

for(const [locale,width] of [['en',1440],['zh-TW',390]] as const)
test(`saving a Result review keeps its recorded conclusion in view: ${locale} ${width}`,async({page})=>{
  await page.setViewportSize({width,height:844});
  const f=fixture(),row=f.save(),zh=locale==='zh-TW';
  f.state.resultEnvelope.next_allowed_actions=[{kind:'action',action:RECORD,target:{workspace_id:workspaceId,review_id:row.review_id}}];
  await mountQuality(page,f,locale,true);await open(page,zh);await fillUnassessed(page,zh);
  await editor(page,RECORD).locator('button[type=submit]').click();
  await expect.poll(()=>row.assessment?.document.acceptance).toBe('unassessed');
  const conclusion=panel(page).getByRole('heading',{level:3});
  await expect(conclusion).toBeFocused();
  await expect(conclusion).toBeInViewport();
  expect(f.state.writes).toHaveLength(1);
});

test('a delayed review save does not move focus away from another field',async({page})=>{
  const f=fixture(),row=f.save();
  f.state.resultEnvelope.next_allowed_actions=[{kind:'action',action:RECORD,target:{workspace_id:workspaceId,review_id:row.review_id}}];
  await mountQuality(page,f,'en',true);await open(page);await fillUnassessed(page);
  let release!:()=>void;f.state.gate=new Promise<void>(resolve=>{release=resolve;});
  const before=f.state.reads.filter((path:string)=>path.includes('/work-packages')).length;
  await editor(page,RECORD).locator('button[type=submit]').click();
  await expect.poll(()=>f.state.reads.filter((path:string)=>path.includes('/work-packages')).length).toBeGreaterThan(before);
  await page.evaluate(()=>{const field=document.createElement('textarea');field.setAttribute('aria-label','Other draft');document.body.append(field);field.focus();});
  const other=page.getByLabel('Other draft',{exact:true});await other.fill('Do not interrupt this draft');
  release();f.state.gate=null;
  await expect(editor(page,RECORD).locator('[data-pm-saved]')).toBeVisible();
  await expect(other).toBeFocused();await expect(other).toHaveValue('Do not interrupt this draft');
  expect(f.state.writes).toHaveLength(1);
});

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

import {assessmentRoundtrip} from './assessment-roundtrip-fixture.ts';
import {workResultFixture,workspaceId as resultWorkspace,workId as resultWork,route as resultRoute} from './work-result-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
async function pinned(page:any,f:any,zh=false,native=false){
  await mountConversation(page,native,f.dispatch);await page.goto(resultRoute);
  if((page.viewportSize()?.width||1440)<768)await page.locator('#workspace-menu').click();
  await page.locator('[data-pm-view="board"]').click();
  await page.locator(`[data-pm-kanban-work="${resultWork(1)}"] [data-board-open]`).click();
  await page.locator('[data-result-requirement="recovery"] summary').filter({hasText:zh?'原結論':'Original conclusion'}).first().click();
  await page.locator('[data-assessment-open]').click();
  const reader=page.locator('[data-original-assessment]:visible');
  await reader.getByRole('button',{name:zh?'審閱這份成果':'Review this result',exact:true}).click();
  await expect(reader.locator('[data-pm-quality-reviews]')).toBeVisible();
  return reader;
}
for(const [locale,width] of [['en',1440],['zh-TW',390]] as const)for(const native of [false,true])test(`pinned assessment adopts the newer version explicitly and keeps its receipt ${locale} ${native?'IPC':'HTTP'}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage();
  const base=workResultFixture(),f=assessmentRoundtrip(base,resultWorkspace,resultWork(1)),zh=locale==='zh-TW';f.newer();
  try{
    const reader=await pinned(page,f,zh,native),form=reader.locator(`[data-pm-action="${RECORD}"]`);
    await expect(page.locator('[data-work-quality]')).toHaveCount(1);
    await reader.locator('[data-quality-assess]>summary').click();
    await expect(form.locator('button[type=submit]')).toBeDisabled();
    expect(f.state.requests.filter(input=>input.method!=='GET')).toEqual([]);
    await form.getByRole('button',{name:zh?'使用目前審查版本':'Use current review version',exact:true}).click();
    for(const field of await form.getByLabel(zh?'條件判斷理由':'Criterion rationale',{exact:true}).all())await field.fill('Keep the original pin while recording this deliberate review.');
    await form.getByLabel(zh?'這次評估的理由':'Reason for this assessment',{exact:true}).fill('Original requirements remain unassessed.');
    await form.locator('button[type=submit]').click();
    await expect(reader.getByRole('button',{name:zh?'檢視已記錄的評估':'View recorded assessment',exact:true})).toBeVisible();
    expect(f.state.requests.filter(input=>input.method==='POST')).toHaveLength(1);
    expect(new URLSearchParams((await page.evaluate(()=>location.hash)).split('?')[1]).get('qa_version')).toBe('2');
    await reader.getByRole('button',{name:zh?'檢視已記錄的評估':'View recorded assessment',exact:true}).click();
    await expect.poll(async()=>new URLSearchParams((await page.evaluate(()=>location.hash)).split('?')[1]).get('qa_version')).toBe('4');
    await reader.getByRole('button',{name:zh?'返回來源':'Return to source',exact:true}).click();
    await expect(page.locator('[data-assessment-open]').first()).toBeFocused();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  }finally{await context.close();}
});

for(const native of [false,true])test(`unknown assessment reply and reload keep the original key without resubmitting ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=assessmentRoundtrip(workResultFixture(),resultWorkspace,resultWork(1));
  let reader=await pinned(page,f,false,native),form=reader.locator(`[data-pm-action="${RECORD}"]`);
  await reader.locator('[data-quality-assess]>summary').click();
  for(const field of await form.getByLabel('Criterion rationale',{exact:true}).all())await field.fill('Review once only.');
  await form.getByLabel('Reason for this assessment',{exact:true}).fill('Keep uncertain admission tied to its original key.');
  f.q.state.lost=true;await form.locator('button[type=submit]').click();
  await expect.poll(()=>f.state.requests.filter(input=>input.method==='POST').length).toBe(1);
  const key=f.state.requests.find(input=>input.method==='POST').idempotency_key;
  await page.reload();
  reader=page.locator('[data-original-assessment]:visible');await expect(reader).toContainText('Original assessment');
  await reader.getByRole('button',{name:'Review this result',exact:true}).click();
  form=reader.locator(`[data-pm-action="${RECORD}"]`);
  await expect(form).toBeVisible();
  expect(f.state.requests.filter(input=>input.method==='POST')).toHaveLength(1);
  const intents=await page.evaluate(()=>Object.values(localStorage).map(raw=>{try{return JSON.parse(raw);}catch{return null;}}).filter(value=>value?.intent));
  expect(intents.some((value:any)=>value.intent.key===key)).toBe(true);
  await expect(reader.getByRole('button',{name:'View recorded assessment',exact:true})).toHaveCount(0);
});

import {installBodyCompletedCounter,pendingBodyIds,syncBodyIds} from './page-sync.ts';
test('a held pinned reread during save cannot replace the newer refresh or resubmit',async({page})=>{
  const base=workResultFixture(),f=assessmentRoundtrip(base,resultWorkspace,resultWork(1));
  await installBodyCompletedCounter(page);const reader=await pinned(page,f),form=reader.locator(`[data-pm-action="${RECORD}"]`);
  await reader.locator('[data-quality-assess]>summary').click();
  for(const field of await form.getByLabel('Criterion rationale',{exact:true}).all())await field.fill('Keep one intentional review.');
  await form.getByLabel('Reason for this assessment',{exact:true}).fill('Persist the receipt while refreshing the original pin.');
  const path='/assessments/'+f.ref.operation_id;
  let release!:()=>void;f.state.hold=new Promise<void>(resolve=>{release=resolve;});
  try{
    await form.locator('button[type=submit]').click();
    await expect.poll(()=>pendingBodyIds(page,path)).toHaveLength(2);
    const held=await pendingBodyIds(page,path);
    const key=f.state.requests.find(input=>input.method==='POST').idempotency_key;
    f.state.hold=null;base.emitResultRecorded('att_'+'1'.repeat(32));
    await expect(reader).toContainText('Original conclusion: accepted');
    expect(await pendingBodyIds(page,path)).toEqual(held);
    release();await syncBodyIds(page,held);
    await expect(reader.getByRole('button',{name:'View recorded assessment',exact:true})).toBeVisible();
    expect(new URLSearchParams((await page.evaluate(()=>location.hash)).split('?')[1]).get('qa_version')).toBe('2');
    const posts=f.state.requests.filter(input=>input.method==='POST');expect(posts).toHaveLength(1);expect(posts[0].idempotency_key).toBe(key);
    await reader.getByRole('button',{name:'View recorded assessment',exact:true}).click();
    await expect.poll(async()=>new URLSearchParams((await page.evaluate(()=>location.hash)).split('?')[1]).get('qa_version')).toBe('3');
  }finally{release();}
});
