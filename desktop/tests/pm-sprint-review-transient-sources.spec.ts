import {test,expect} from '@playwright/test';
import {guidedReviewFixture,seedGuidedReview,workspaceId,threadId} from './pm-sprint-review-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
const prefix=`/pm/workspaces/${workspaceId}`,route=`/dashboard/#/pm/${workspaceId}/discussions`,note='AUTHORED_NOTE',old='SOURCE_PREVIOUS_INPUT',fresh='SOURCE_UNVALIDATED_INPUT';
function fixture(){
  const f=guidedReviewFixture();seedGuidedReview(f,5);f.base.state.thread.context=[];f.base.state.messages[0].content=note;
  const d=f.detail,ref={kind:'sprint_review_decision',meeting_id:d.meeting.meeting_id,completion_id:d.completion.completion_id,completion_digest:d.completion.document_digest,decision_id:'retain'};
  f.base.state.inputs=[{input_id:'pmi_'+'e'.repeat(32),slot:'scope',value:old,status:'unresolved',source_ref:ref,recorded_by:'owner',source_decision:{reference:ref,decision:d.completion.document.decisions[0],governance:d.completion.document.governance_decisions[0],follow_ups:d.completion.document.follow_ups}}];return f;
}
for(const native of [false,true])for(const trigger of ['event','main','earlier'])for(const failure of ['503','network','timeout','401'])test(`required source transient ${failure} retains ${trigger} editor ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=fixture();let fail=false,denied=0,cursor=0;
  const target=prefix+'/meetings/'+f.detail.meeting.meeting_id;
  await mountConversation(page,native,async request=>{
    const url=new URL(request.path,'http://fixture');
    if(url.pathname==='/events'&&cursor)return{status:200,data:{events:Number(url.searchParams.get('after'))<cursor?[{seq:cursor,resource_type:'work_item',resource_id:'wi_'+'a'.repeat(20),kind:'work_item.updated'}]:[],next_cursor:cursor,head_cursor:cursor,has_more:false,sync:{checkpoint:{cursor,token:'transient-'+cursor}}}};
    if(fail&&url.pathname===target){denied++;if(native&&['network','timeout'].includes(failure))throw Error(failure==='timeout'?'Transport timeout':'Transport disconnected');return{status:Number(failure)||503,data:{error:{code:failure==='401'?'UNAUTHORIZED':'UNAVAILABLE',message:'Required source temporarily unavailable'}}};}
    const reply=await f.dispatch(request);
    if(url.pathname===prefix+'/threads/'+threadId){reply.data.next_message_cursor='older';if(fail)reply.data.inputs[0].value=fresh;}
    return reply;
  });
  if(!native&&['network','timeout'].includes(failure))await page.route('**/api/v1/**',async request=>{
    if(fail&&new URL(request.request().url()).pathname.slice(7)===target){denied++;await request.abort(failure==='timeout'?'timedout':'failed');}else await request.fallback();
  });
  await page.goto(route+'/'+threadId);await expect(page.locator('body')).toContainText(old);
  const composer=page.locator('[data-discussion-composer]');await composer.fill('UNSENT 草稿');await composer.evaluate((node:HTMLTextAreaElement)=>{node.setSelectionRange(2,5);(window as any).originalComposer=node;});
  fail=true;if(trigger==='main')await page.locator('[data-pm-refresh]').click();else if(trigger==='earlier')await page.getByRole('button',{name:'Load earlier notes',exact:true}).click();else cursor++;
  await expect.poll(()=>denied).toBeGreaterThan(0);await expect(page.locator('.pm-main [data-api-error]')).not.toHaveCount(0);
  await expect(composer).toBeVisible();await expect(composer).toHaveValue('UNSENT 草稿');
  expect(await composer.evaluate((node:any)=>({same:node===(window as any).originalComposer,start:node.selectionStart,end:node.selectionEnd}))).toEqual({same:true,start:2,end:5});
  const text=await page.locator('body').textContent();expect(text).toContain(note);expect(text).toContain(old);expect(text).not.toContain(fresh);
  expect(f.state.requests.filter((r:any)=>r.method!=='GET')).toEqual([]);expect(f.state.providerCalls).toBe(0);
});

// Positive controls: initial transient reads never publish ungated content, and
// ordinary list failures retain authored row metadata (the reviewer's list cells).
for(const native of [false,true])for(const failure of ['503','network','401'])test(`initial required source ${failure} never paints derived payload ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=fixture();let denied=0;const target=prefix+'/meetings/'+f.detail.meeting.meeting_id;
  await mountConversation(page,native,async request=>{
    if(new URL(request.path,'http://fixture').pathname===target){denied++;if(failure==='network'&&native)throw Error('Transport disconnected');return{status:Number(failure)||503,data:{error:{code:'UNAVAILABLE',message:'Required source temporarily unavailable'}}};}
    return f.dispatch(request);
  });
  if(!native&&failure==='network')await page.route('**/api/v1/**',async request=>{if(new URL(request.request().url()).pathname.slice(7)===target){denied++;await request.abort('failed');}else await request.fallback();});
  await page.goto(route+'/'+threadId);await expect.poll(()=>denied).toBeGreaterThan(0);await expect(page.locator('.pm-main [data-api-error]')).not.toHaveCount(0);
  expect(await page.locator('body').textContent()).not.toContain(old);await expect(page.locator('[data-discussion-composer]')).toHaveCount(0);
});
for(const native of [false,true])for(const failure of ['503','network'])test(`ordinary list transient ${failure} retains authored row ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=fixture();let fail=false,denied=0,cursor=0;
  await mountConversation(page,native,async request=>{
    const url=new URL(request.path,'http://fixture');
    if(url.pathname==='/events'&&cursor)return{status:200,data:{events:Number(url.searchParams.get('after'))<cursor?[{seq:cursor,resource_type:'work_item',resource_id:'wi_'+'a'.repeat(20),kind:'work_item.updated'}]:[],next_cursor:cursor,head_cursor:cursor,has_more:false,sync:{checkpoint:{cursor,token:'list-'+cursor}}}};
    if(fail&&url.pathname===prefix+'/threads'){denied++;if(failure==='network'&&native)throw Error('Transport disconnected');return{status:503,data:{error:{code:'UNAVAILABLE',message:'List temporarily unavailable'}}};}return f.dispatch(request);
  });
  if(!native&&failure==='network')await page.route('**/api/v1/**',async request=>{if(fail&&new URL(request.request().url()).pathname.slice(7)===prefix+'/threads'){denied++;await request.abort('failed');}else await request.fallback();});
  await page.goto(route);await expect(page.locator('.pm-thread-link')).toContainText(f.base.state.thread.title);fail=true;cursor++;
  await expect.poll(()=>denied).toBeGreaterThan(0);await expect(page.locator('.pm-main [data-api-error]')).not.toHaveCount(0);await expect(page.locator('.pm-thread-link')).toContainText(f.base.state.thread.title);
});
for(const native of [false,true])for(const refusal of ['403','404','forbidden','source-code','filtered','scope','integrity'])test(`required source refusal ${refusal} still clears derived content ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=fixture();let fail=false,denied=0;const target=prefix+'/meetings/'+f.detail.meeting.meeting_id;
  await mountConversation(page,native,async request=>{
    const path=new URL(request.path,'http://fixture').pathname;
    if(fail&&path===target){
      denied++;
      if(['filtered','scope','integrity'].includes(refusal)){const reply=await f.dispatch(request);if(refusal==='filtered')reply.data.visibility={filtered:true};else if(refusal==='scope')reply.data.workspace_id='ws_'+'f'.repeat(32);else reply.data.completion.document.decisions={};return reply;}
      return{status:refusal==='source-code'?503:Number(refusal)||409,data:{error:{code:refusal==='forbidden'?'FORBIDDEN':refusal==='source-code'?'PM_MEETING_SOURCE_UNAVAILABLE':'REFUSED',message:'Required source refused'}}};
    }return f.dispatch(request);
  });
  await page.goto(route+'/'+threadId);await expect(page.locator('body')).toContainText(old);fail=true;await page.locator('[data-pm-refresh]').click();
  await expect.poll(()=>denied).toBeGreaterThan(0);await expect(page.locator('body')).not.toContainText(old);await expect(page.locator('[data-discussion-composer]')).toHaveCount(0);
});
for(const native of [false,true])for(const kind of ['quality_assessment','repair','issue_mapping'])test(`nested required ${kind} transient retains discussion ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=fixture(),work='wi_'+'f'.repeat(20),id='pmref_'+'a'.repeat(32);let fail=false,denied=0;
  const ref={kind,id,review_id:'pmqr_'+'f'.repeat(32),document_digest:'e'.repeat(64)};
  f.base.state.thread.context=[ref];
  await mountConversation(page,native,async request=>{
    const path=new URL(request.path,'http://fixture').pathname;
    if(path===prefix+'/kanban/'+work){
      if(fail){denied++;return{status:503,data:{error:{code:'UNAVAILABLE',message:'Nested source temporarily unavailable'}}};}
      return{status:200,data:{workspace_id:workspaceId,card:{work_item_id:work,work:{work_item_id:work,title:'NESTED_READABLE_LABEL'},binding:{workspace_id:workspaceId,work_item_id:work}}}};
    }
    if(path===prefix+'/quality-reviews/'+ref.review_id)return{status:200,data:{workspace_id:workspaceId,review:{workspace_id:workspaceId,review_id:ref.review_id,work_item_id:work,history:[{kind:'assessment',operation_id:id,document_digest:ref.document_digest}]}}};
    if(path===prefix+'/maintenance-repairs/'+id)return{status:200,data:{workspace_id:workspaceId,repair:{repair_id:id,work_item_id:work}}};
    if(path===prefix+'/issue-mappings/'+id)return{status:200,data:{workspace_id:workspaceId,current:{mapping_id:id,work_item_id:work,document:{public_title:'NESTED_READABLE_LABEL'}}}};
    return f.dispatch(request);
  });
  await page.goto(route+'/'+threadId);await expect(page.locator('body')).toContainText(old);
  const composer=page.locator('[data-discussion-composer]');await composer.fill('UNSENT nested draft');await composer.evaluate(node=>(window as any).nestedComposer=node);
  fail=true;await page.locator('[data-pm-refresh]').click();await expect.poll(()=>denied).toBeGreaterThan(0);await expect(page.locator('.pm-main [data-api-error]')).not.toHaveCount(0);
  await expect(composer).toBeVisible();await expect(composer).toHaveValue('UNSENT nested draft');expect(await composer.evaluate(node=>node===(window as any).nestedComposer)).toBe(true);
  await expect(page.locator('body')).toContainText(old);await expect(page.locator('body')).toContainText('NESTED_READABLE_LABEL');
});
