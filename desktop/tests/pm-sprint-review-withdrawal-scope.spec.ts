import {test,expect} from '@playwright/test';
import {guidedReviewFixture,seedGuidedReview,workspaceId,threadId} from './pm-sprint-review-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {openPmSection,openThreadTab} from './pm-navigation.ts';
const route=`/dashboard/#/pm/${workspaceId}/discussions`,prefix=`/pm/workspaces/${workspaceId}`;
const otherId='pmt_'+'f'.repeat(32),ownNote='INDEPENDENT_AUTHORED_NOTE',otherTitle='UNRELATED_THREAD_TITLE';
function setup(derived=false,location='inputs'){
  const f=guidedReviewFixture();Object.assign(f.base.state.thread,{title:'ORDINARY_AUTHORED_TITLE',context:[]});f.base.state.messages[0].content=ownNote;
  if(derived){
    seedGuidedReview(f,5);const d=f.detail,decision=d.completion.document.decisions[0];
    const ref={kind:'sprint_review_decision',meeting_id:d.meeting.meeting_id,completion_id:d.completion.completion_id,completion_digest:d.completion.document_digest,decision_id:decision.id};
    const item={input_id:'pmi_'+'e'.repeat(32),slot:'scope',value:'SOURCE_DERIVED_INPUT',status:'unresolved',source_ref:ref,recorded_by:'owner',source_decision:{reference:ref,decision,governance:d.completion.document.governance_decisions[0],follow_ups:d.completion.document.follow_ups}};
    if(location==='inputs')f.base.state.inputs.push(item);
    if(location==='history')f.state.feedbackHistory=[item];
    if(location==='selected'||location==='candidate')f.base.state.draft={draft_id:'pmd_'+'6'.repeat(32),title:'SOURCE_DERIVED_PLAN',rationale:'SOURCE_DERIVED_RATIONALE',selected_inputs:location==='selected'?[item]:[],candidate_inputs:location==='candidate'?[item]:[],unknowns:[],status:'draft'};
  }
  return f;
}
const refused=(status=404)=>({status,data:{error:{code:status===503?'UNAVAILABLE':'PM_MEETING_SOURCE_UNAVAILABLE',message:'Scoped fixture refusal'}}});
async function expand(form:any){await form.evaluate((node:Element)=>{for(let p=node.parentElement;p;p=p.parentElement)if(p instanceof HTMLDetailsElement)p.open=true;});}
function transport(f:any,intercept:(req:any)=>any=()=>null){return async(req:any)=>{
  const override=await intercept(req);if(override)return override;
  const response=await f.dispatch(req);
  if(req.method==='GET'&&new URL(req.path,'http://fixture').pathname===prefix+'/threads')response.data.threads.push({...f.base.state.thread,thread_id:otherId,title:otherTitle,context:[]});
  return response;
};}
for(const native of [false,true])for(const surface of ['thread','list'])test(`ordinary citation keeps ${surface} with capability absent and v1 ${native?'IPC':'HTTP'}`,async({browser,baseURL})=>{
  for(const enabled of [false,true]){
    const context=await browser.newContext({baseURL}),page=await context.newPage(),f=setup();f.state.enabled=enabled;f.detail.meeting.kind='planning';
    Object.assign(f.base.state.thread,{context:[{kind:'meeting',id:f.detail.meeting.meeting_id}]});let denied=0;
    await mountConversation(page,native,transport(f,req=>{if(req.method==='GET'&&req.path.endsWith('/meetings/'+f.detail.meeting.meeting_id)){denied++;return refused();}}));
    await page.goto(route+(surface==='thread'?'/'+threadId:''));await expect.poll(()=>denied).toBeGreaterThan(0);await expect(page.locator('body')).toContainText(/Unavailable|unavailable/);
    await expect.soft(page.locator('body'),`capability ${enabled}`).toContainText(surface==='thread'?ownNote:otherTitle);
    if(surface==='list')await expect.soft(page.locator('body')).toContainText('ORDINARY_AUTHORED_TITLE');
    expect(f.state.requests.filter((r:any)=>r.method!=='GET')).toEqual([]);await context.close();
  }
});
for(const native of [false,true])for(const location of ['inputs','history','selected','candidate'])test(`required ${location} lineage withdraws only its thread ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=setup(true,location);let denied=0;
  await mountConversation(page,native,transport(f,req=>{if(req.method==='GET'&&req.path.endsWith('/meetings/'+f.detail.meeting.meeting_id)){denied++;return refused();}}));
  await page.goto(route+'/'+threadId);await expect.poll(()=>denied).toBeGreaterThan(0);await expect(page.locator('body')).toContainText('Scoped fixture refusal');
  await expect(page.locator('body')).not.toContainText('SOURCE_DERIVED');await expect(page.locator('body')).not.toContainText('SOURCE_GOVERNANCE');
  await expect(page.locator('.pm-thread-link').filter({hasText:otherTitle})).toBeVisible();
});
for(const native of [false,true])for(const trigger of ['main','earlier','context','list'])test(`ordinary ${trigger} read error retains baseline content ${native?'IPC':'HTTP'}`,async({browser,baseURL})=>{
  for(const enabled of [false,true]){
    const context=await browser.newContext({baseURL}),page=await context.newPage(),f=setup();f.state.enabled=enabled;let fail=false,denied=0,cursor=0;
    await mountConversation(page,native,transport(f,async req=>{
      const url=new URL(req.path,'http://fixture');
      if(url.pathname==='/events'&&cursor)return{status:200,data:{events:Number(url.searchParams.get('after'))<cursor?[{seq:cursor,resource_type:'work_item',resource_id:'wi_'+'a'.repeat(20),kind:'work_item.updated'}]:[],next_cursor:cursor,head_cursor:cursor,has_more:false,sync:{checkpoint:{cursor,token:'scope-'+cursor}}}};
      if(fail&&req.method==='GET'&&url.pathname===(trigger==='list'?prefix+'/threads':prefix+'/threads/'+threadId)){denied++;return refused(503);}
      if(req.method==='GET'&&url.pathname===prefix+'/threads/'+threadId){const res=await f.dispatch(req);res.data.next_message_cursor='older';return res;}
    }));
    await page.goto(route+(trigger==='list'?'':'/'+threadId));await expect(page.locator('body')).toContainText(trigger==='list'?otherTitle:ownNote);fail=true;
    if(trigger==='main')await page.locator('[data-pm-refresh]').click();else if(trigger==='earlier')await page.getByRole('button',{name:'Load earlier notes',exact:true}).click();else cursor++;
    await expect.poll(()=>denied).toBeGreaterThan(0);await expect(page.locator('body')).toContainText('Scoped fixture refusal');
    expect.soft(await page.locator('body').textContent(),`capability ${enabled}`).toContain(trigger==='list'?otherTitle:ownNote);
    await context.close();
  }
});
for(const native of [false,true])for(const action of ['input','plan','meeting','governance'])test(`ordinary ${action} action refusal keeps readable content ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=setup();let denied=0;
  if(action==='meeting')f.detail.meeting.kind='planning';
  await mountConversation(page,native,transport(f,req=>{if(req.method==='POST'){denied++;return refused();}}));
  await page.goto(['meeting','governance'].includes(action)?route:route+'/'+threadId);
  await expect(page.locator('.pm-thread-link').filter({hasText:otherTitle})).toBeVisible();
  let form:any;
  if(action==='input'){
    await openThreadTab(page,'inputs');form=page.locator('[data-pm-action="pm.input.record"]');await expand(form);
    await form.getByLabel('Statement or question',{exact:true}).fill('Authored input');await form.getByLabel('Topic',{exact:true}).selectOption('problem');
  }else if(action==='plan'){
    await openThreadTab(page,'inputs');await page.locator('[data-pm-information] input[type=checkbox]').first().check();await openThreadTab(page,'plan');
    form=page.locator('[data-pm-action="pm.thread.plan"]');await expand(form);await form.getByLabel('Why this direction',{exact:true}).fill('Authored rationale');
  }else{
    await openPmSection(page,action==='meeting'?'discussions':'issues',action==='meeting'?'ui_meetings':'ui_governance');
    if(action==='meeting'){
      form=page.locator('[data-pm-action="pm.meeting.note"]');await expand(form);await form.getByLabel('Discussion note',{exact:true}).fill('Authored note');await form.getByLabel('Reason for this record',{exact:true}).fill('Authored reason');
    }else{
      await page.getByRole('button',{name:'Record finding',exact:true}).click();form=page.locator('[data-pm-action="pm.governance.record"]');
      await form.getByLabel('Finding title',{exact:true}).fill('Authored finding');await form.getByLabel('Explicit root cause key',{exact:true}).fill('authored-key');await form.getByLabel('Reason for this record',{exact:true}).fill('Authored reason');
      await form.getByLabel('What was observed and why it matters',{exact:true}).fill('Authored observation');await form.getByRole('checkbox').first().check();
    }
  }
  await form.locator('button[type=submit]').click();await expect.poll(()=>denied).toBe(1);await expect(page.locator('body')).toContainText('Scoped fixture refusal');
  if(!['meeting','governance'].includes(action))expect(await page.locator('body').textContent()).toContain(ownNote);
  await expect(form).toBeVisible();
  const unrelated=page.locator('.pm-thread-link').filter({hasText:otherTitle});
  if(action==='governance')await expect(unrelated).toHaveCount(1);else await expect(unrelated).toBeVisible();
});
for(const native of [false,true])for(const trigger of ['main','earlier','context'])test(`required dependency refusal on ${trigger} keeps unrelated row ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=setup(true);let fail=false,denied=0,cursor=0;
  await mountConversation(page,native,transport(f,async req=>{
    const url=new URL(req.path,'http://fixture');
    if(url.pathname==='/events'&&cursor)return{status:200,data:{events:Number(url.searchParams.get('after'))<cursor?[{seq:cursor,resource_type:'work_item',resource_id:'wi_'+'a'.repeat(20),kind:'work_item.updated'}]:[],next_cursor:cursor,head_cursor:cursor,has_more:false,sync:{checkpoint:{cursor,token:'required-'+cursor}}}};
    if(fail&&req.method==='GET'&&url.pathname.endsWith('/meetings/'+f.detail.meeting.meeting_id)){denied++;return refused();}
    if(req.method==='GET'&&url.pathname===prefix+'/threads/'+threadId){const res=await f.dispatch(req);res.data.next_message_cursor='older';return res;}
  }));
  await page.goto(route+'/'+threadId);await expect(page.locator('body')).toContainText('SOURCE_DERIVED_INPUT');fail=true;
  if(trigger==='main')await page.locator('[data-pm-refresh]').click();else if(trigger==='earlier')await page.getByRole('button',{name:'Load earlier notes',exact:true}).click();else cursor++;
  await expect.poll(()=>denied).toBeGreaterThan(0);await expect(page.locator('body')).toContainText('Scoped fixture refusal');
  await expect(page.locator('body')).not.toContainText('SOURCE_DERIVED');await expect(page.locator('body')).not.toContainText('SOURCE_GOVERNANCE');
  await expect(page.locator('.pm-thread-link').filter({hasText:otherTitle})).toBeVisible();
});
for(const native of [false,true])for(const action of ['input','plan','meeting','governance'])test(`typed ${action} action refusal withdraws only related content ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=setup(!['meeting','governance'].includes(action));let denied=0;
  f.base.state.thread.context=[{kind:'meeting',id:f.detail.meeting.meeting_id}];
  if(action==='meeting'||action==='governance')seedGuidedReview(f,4);
  if(action==='governance'){f.governance.state.records=[];f.governance.state.history={};}
  await mountConversation(page,native,transport(f,req=>{if(req.method==='POST'){denied++;return refused();}}));
  await page.goto(['meeting','governance'].includes(action)?route:route+'/'+threadId);
  let form:any;
  if(action==='input'){
    await openThreadTab(page,'inputs');form=page.locator('[data-pm-action="pm.input.record"]');await expand(form);
    await form.getByLabel('Statement or question',{exact:true}).fill('Authored intent');await form.getByLabel('Topic',{exact:true}).selectOption('problem');
    const select=form.getByLabel('Source type or saved note',{exact:true});const value=await select.locator('option').evaluateAll(nodes=>nodes.map(n=>(n as HTMLOptionElement).value).find(v=>v.startsWith('review:')));await select.selectOption(value!);
  }else if(action==='plan'){
    await openThreadTab(page,'inputs');await page.locator('[data-pm-information] article').filter({hasText:'SOURCE_DERIVED_INPUT'}).getByRole('checkbox').check();await openThreadTab(page,'plan');
    form=page.locator('[data-pm-action="pm.thread.plan"]');await expand(form);await form.getByLabel('Why this direction',{exact:true}).fill('Authored rationale');
  }else{
    await openPmSection(page,'discussions','ui_meetings');
    if(action==='meeting'){
      form=page.locator('[data-pm-action="pm.meeting.note"]');await expand(form);await form.getByLabel('Discussion note',{exact:true}).fill('Authored note');await form.getByLabel('Reason for this record',{exact:true}).fill('Authored reason');
    }else{
      await page.locator('[data-pm-review-step="4"]').getByRole('button',{name:'Record finding',exact:true}).click();form=page.locator('[data-pm-action="pm.governance.record"]');
      await form.getByLabel('Finding title',{exact:true}).fill('Authored finding');await form.getByLabel('Explicit root cause key',{exact:true}).fill('authored-key');await form.getByLabel('Reason for this record',{exact:true}).fill('Authored reason');
      await form.getByRole('checkbox').first().check();
    }
  }
  await form.locator('button[type=submit]').click();await expect.poll(()=>denied).toBe(1);await expect(page.locator('body')).toContainText('Scoped fixture refusal');
  await expect(page.locator('body')).not.toContainText('SOURCE_DERIVED');await expect(page.locator('body')).not.toContainText('SOURCE_FEEDBACK');await expect(page.locator('body')).not.toContainText('SOURCE_GOVERNANCE');
  await expect(page.locator('.pm-thread-link').filter({hasText:otherTitle})).toBeVisible();
});

for(const native of [false,true])test(`first typed input refusal clears source projection and keeps authored thread ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=setup();seedGuidedReview(f,5);f.base.state.thread.context=[{kind:'meeting',id:f.detail.meeting.meeting_id}];let denied=0,attempt:any;
  await mountConversation(page,native,transport(f,req=>{if(req.method==='POST'){denied++;attempt=req;return refused();}}));
  await page.goto(route+'/'+threadId);await openThreadTab(page,'inputs');
  const form=page.locator('[data-pm-action="pm.input.record"]');await expand(form);
  await form.getByLabel('Statement or question',{exact:true}).fill('Authored intent');await form.getByLabel('Topic',{exact:true}).selectOption('problem');
  const select=form.getByLabel('Source type or saved note',{exact:true});
  const value=await select.locator('option').evaluateAll(nodes=>nodes.map(n=>(n as HTMLOptionElement).value).find(v=>v.startsWith('review:')));await select.selectOption(value!);
  await form.locator('button[type=submit]').click();await expect.poll(()=>denied).toBe(1);await expect(page.locator('body')).toContainText('Scoped fixture refusal');
  await expect(page.locator('body')).not.toContainText('SOURCE_GOVERNANCE');await expect(page.locator('body')).toContainText(ownNote);
  await expect(page.locator('.pm-thread-link').filter({hasText:otherTitle})).toBeVisible();
  expect(await select.inputValue()).toBe(value);
  const saved=await page.evaluate(()=>Object.values(localStorage).map(raw=>{try{return JSON.parse(raw);}catch{return null;}}).find(row=>row?.intent?.request?.action==='pm.input.record'));
  expect(saved.intent.request).toEqual(attempt.body);expect(saved.intent.key).toBe(attempt.idempotency_key);
});

for(const native of [false,true])for(const module of ['meeting','governance'])test(`transient ${module} source error does not revoke another panel ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=setup();seedGuidedReview(f,4);let fail=false,denied=0;
  const path=prefix+(module==='meeting'?'/meetings/'+f.detail.meeting.meeting_id:'/governance/'+f.governance.state.records[0].record.record_id);
  await mountConversation(page,native,transport(f,req=>{if(fail&&req.method==='GET'&&new URL(req.path,'http://fixture').pathname===path){denied++;return refused(503);}}));
  await page.goto(route);await openPmSection(page,'discussions','ui_meetings');await expect(page.locator('body')).toContainText('SOURCE_FEEDBACK');
  await openPmSection(page,'issues','ui_governance');await expect(page.locator('body')).toContainText('SOURCE_GOVERNANCE');
  if(module==='meeting')await openPmSection(page,'discussions','ui_meetings');
  fail=true;await page.getByRole('button',{name:module==='meeting'?'Refresh meetings':'Refresh findings',exact:true}).click();await expect.poll(()=>denied).toBeGreaterThan(0);
  await expect(page.locator('body')).toContainText('Scoped fixture refusal');
  expect(await page.locator('body').textContent()).toContain(module==='meeting'?'SOURCE_GOVERNANCE':'SOURCE_FEEDBACK');
  const unrelated=page.locator('.pm-thread-link').filter({hasText:otherTitle});
  if(module==='governance')await expect(unrelated).toHaveCount(1);else await expect(unrelated).toBeVisible();
});
