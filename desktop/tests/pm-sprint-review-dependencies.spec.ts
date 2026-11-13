import {test,expect} from '@playwright/test';
import {guidedReviewFixture,seedGuidedReview,workspaceId,threadId} from './pm-sprint-review-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {openPmSection} from './pm-navigation.ts';

const route=`/dashboard/#/pm/${workspaceId}`;
const privateText=['SOURCE_PLANNING_TITLE','SOURCE_PLANNING_REASON','SOURCE_GOVERNANCE','SOURCE_FEEDBACK','SOURCE_FOLLOW_UP'];
async function absent(page:any){
  const body=await page.locator('body').textContent();
  for(const text of privateText)expect(body).not.toContain(text);
  for(const [key,value] of await page.evaluate(()=>Object.entries(localStorage)))for(const text of privateText)expect(key+value).not.toContain(text);
}
function planning(){
  const f=guidedReviewFixture();seedGuidedReview(f,5);
  const mid=f.detail.meeting.meeting_id,decision=f.detail.completion.document.decisions[0];
  const ref={kind:'sprint_review_decision',meeting_id:mid,completion_id:f.detail.completion.completion_id,completion_digest:f.detail.completion.document_digest,decision_id:decision.id};
  Object.assign(f.base.state.thread,{context:[{kind:'meeting',id:mid}],mode:'planning',draft_id:'pmd_'+'6'.repeat(32)});
  const input={input_id:'pmi_'+'c'.repeat(32),slot:'scope',value:'Authored intent',status:'unresolved',source_ref:ref,recorded_by:'owner',source_decision:{reference:ref,decision,governance:f.detail.completion.document.governance_decisions[0],follow_ups:f.detail.completion.document.follow_ups}};
  f.base.state.inputs=[input];f.base.state.draft={draft_id:f.base.state.thread.draft_id,title:'SOURCE_PLANNING_TITLE',rationale:'SOURCE_PLANNING_REASON',selected_inputs:[input],candidate_inputs:[],unknowns:[],status:'draft',scope_applied:false,execution_authorized:false};
  return f;
}
for(const native of [false,true])for(const source of ['context','input','filtered','governance'] as const)test(`Review dependency withdraws readable Planning ${source} ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=planning(),mid=f.detail.meeting.meeting_id;let threadReads=0,denials=0;
  if(source==='input')f.base.state.thread.context=[];
  const dependency=source==='governance'?'/governance/'+f.governance.state.records[0].record.record_id:'/meetings/'+mid;
  if(source==='governance')f.base.state.thread.context=[{kind:'governance',id:f.governance.state.records[0].record.record_id}];
  if(source==='filtered')f.base.state.thread.context=[{kind:'meeting',available:false}];
  await mountConversation(page,native,async(req:any)=>{
    const path=new URL(req.path,'http://fixture').pathname;
    if(req.method==='GET'&&path.endsWith('/threads/'+threadId))threadReads++;
    if(req.method==='GET'&&path.endsWith(dependency)){denials++;return{status:404,data:{error:{code:'PM_MEETING_SOURCE_UNAVAILABLE',message:'Source withdrawn after thread read'}}};}
    return f.dispatch(req);
  });
  await page.goto(`${route}/discussions/${threadId}`);
  await expect.poll(()=>threadReads).toBeGreaterThan(0);
  // A rendered draft proves the old load finished; a refusal status proves the new gate finished.
  await expect(page.locator('body')).toContainText(/SOURCE_PLANNING_TITLE|Source withdrawn after thread read|Unavailable/);
  if(source!=='filtered')await expect.poll(()=>denials).toBeGreaterThan(0);
  await absent(page);
  expect(f.state.requests.filter((r:any)=>r.method!=='GET')).toEqual([]);expect(f.state.providerCalls).toBe(0);
});
for(const native of [false,true])for(const source of ['detail','snapshot'] as const)test(`Review governance ${source} denial withdraws linked panels ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=guidedReviewFixture();seedGuidedReview(f,4);let withdraw=false,denials=0;
  const deniedPath=`/pm/workspaces/${workspaceId}/`+(source==='detail'?'governance/'+f.governance.state.records[0].record.record_id:'snapshots/'+f.detail.snapshot.snapshot_id);
  await mountConversation(page,native,async(req:any)=>{
    if(withdraw&&req.method==='GET'&&new URL(req.path,'http://fixture').pathname===deniedPath){denials++;return{status:404,data:{error:{code:source==='detail'?'PM_GOVERNANCE_SOURCE_UNAVAILABLE':'PM_SNAPSHOT_SOURCE_UNAVAILABLE',message:'Targeted source refusal'}}};}
    return f.dispatch(req);
  });
  await page.goto(route);await openPmSection(page,'discussions','ui_meetings');await expect(page.locator('[data-pm-guided-review]')).toContainText('SOURCE_FEEDBACK');
  await openPmSection(page,'issues','ui_governance');await expect(page.locator('[data-pm-governance-detail]')).toContainText('SOURCE_GOVERNANCE');
  const before=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('draft')||key.includes('intent')));
  withdraw=true;await page.getByRole('button',{name:'Refresh findings',exact:true}).click();await expect.poll(()=>denials).toBeGreaterThan(0);
  await expect(page.locator('[data-pm-governance-detail]')).toHaveCount(0);
  await absent(page);
  expect(await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('draft')||key.includes('intent')))).toEqual(before);
});
for(const native of [false,true])for(const read of ['event','earlier'] as const)test(`Review dependency on ${read} read clears existing Planning ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=planning();let withdraw=false,denials=0;const mid=f.detail.meeting.meeting_id;
  await mountConversation(page,native,async(req:any)=>{
    const url=new URL(req.path,'http://fixture');
    if(withdraw&&req.method==='GET'&&url.pathname.endsWith('/meetings/'+mid)){denials++;return{status:404,data:{error:{code:'PM_MEETING_SOURCE_UNAVAILABLE',message:'Source withdrawn on reread'}}};}
    const response=await f.dispatch(req);
    if(req.method==='GET'&&url.pathname.endsWith('/threads/'+threadId))response.data.next_message_cursor=url.searchParams.has('before_message')?null:'older';
    return response;
  });
  await page.goto(`${route}/discussions/${threadId}`);
  // The prior payload is readable, including the draft proof hidden in another tab.
  await expect(page.locator('body')).toContainText('SOURCE_PLANNING_TITLE');
  f.base.state.lost=true;
  await page.locator('[data-discussion-composer]').fill('Authored note retained through withdrawal');
  await page.locator('[data-pm-action="pm.message.append"] button[type=submit]').click();
  await expect(page.locator('[data-pm-action="pm.message.append"]')).toContainText('Reply unavailable');
  const saved=await page.evaluate(()=>Object.entries(localStorage).filter(([key,value])=>key.includes('pm.message.append')&&JSON.parse(value).intent));
  expect(saved).toHaveLength(1);expect(f.base.state.writes).toHaveLength(1);
  withdraw=true;
  if(read==='event')f.base.state.cursor++;
  else await page.getByRole('button',{name:'Load earlier notes',exact:true}).click();
  await expect.poll(()=>denials).toBeGreaterThan(0);await absent(page);
  expect(await page.evaluate(()=>Object.entries(localStorage).filter(([key,value])=>key.includes('pm.message.append')&&JSON.parse(value).intent))).toEqual(saved);
  await page.reload();await expect(page.locator('body')).toContainText('Source withdrawn on reread');
  expect(f.base.state.writes).toHaveLength(1);await absent(page);
});
