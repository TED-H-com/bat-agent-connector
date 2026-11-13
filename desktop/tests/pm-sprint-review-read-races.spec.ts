import {test,expect} from '@playwright/test';
import {guidedReviewFixture,seedGuidedReview,workspaceId} from './pm-sprint-review-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {openPmSection} from './pm-navigation.ts';

const label='SOURCE_MEETING_LATE',other='INDEPENDENT_ROW',route=`/dashboard/#/pm/${workspaceId}/discussions`;
const eventReply=(cursor:number,request:any)=>({status:200,data:{events:Number(new URL(request.path,'http://fixture').searchParams.get('after'))<cursor?[{seq:cursor,resource_type:'work_item',resource_id:'wi_'+'a'.repeat(20),kind:'work_item.updated'}]:[],next_cursor:cursor,head_cursor:cursor,has_more:false,sync:{checkpoint:{cursor,token:'late-source-'+cursor}}}});
const refusal={status:404,data:{error:{code:'PM_GOVERNANCE_SOURCE_UNAVAILABLE',message:'Source refused during hydration'}}};
async function observe(page:any,marker:string){await page.evaluate(marker=>{
  (window as any).restoredSources=[];
  new MutationObserver(()=>{if(document.body.textContent?.includes(marker))(window as any).restoredSources.push(marker);}).observe(document.body,{subtree:true,childList:true,characterData:true});
},marker);}
async function neverRestored(page:any,done:()=>number,marker:string){
  await expect.poll(done).toBe(1);
  // Include reads spawned by the released response in the observed mutation window.
  await page.waitForLoadState('networkidle');await page.evaluate(()=>new Promise(requestAnimationFrame));
  expect(await page.evaluate(()=>(window as any).restoredSources)).toEqual([]);
  expect(await page.locator('body').textContent()).not.toContain(marker);
}

// Adapted from the independent observer and visible-list probes. No old bundle or
// evidence-directory import is needed: run these against the actual candidate.
for(const native of [false,true])for(const load of ['event-section','event-drawer','main-section'])test(`withdrawal invalidates list hydration ${load} ${native?'IPC':'HTTP'}`,async({page})=>{
  await page.setViewportSize({width:1440,height:950});
  const f=guidedReviewFixture();seedGuidedReview(f,load.endsWith('section')?5:4);f.detail.meeting.title=label;
  const mid=f.detail.meeting.meeting_id,gid=f.governance.state.records[0].record.record_id;
  f.base.state.thread.context=[{kind:'meeting',id:mid}];let cursor=0,hold=false,held=0,done=0,deny=false,denied=0,release=()=>{};
  const gate=new Promise<void>(resolve=>release=resolve);
  await mountConversation(page,native,async request=>{
    const path=new URL(request.path,'http://fixture').pathname;
    if(path==='/events'&&cursor)return eventReply(cursor,request);
    if(deny&&path.endsWith('/governance/'+gid)){denied++;return refusal;}
    const reply=await f.dispatch(request);
    if(path.endsWith('/threads'))reply.data.threads.push({...f.base.state.thread,thread_id:'pmt_'+'f'.repeat(32),title:other,context:[]});
    if(hold&&path.endsWith('/meetings/'+mid)){hold=false;held++;await gate;done++;}return reply;
  });
  await page.goto(route);await expect(page.locator('.pm-thread-link').first()).toContainText(label);
  if(load.endsWith('section')){
    await openPmSection(page,'issues','ui_governance');await expect(page.locator('body')).toContainText('SOURCE_GOVERNANCE');await openPmSection(page,'discussions');
  }else{
    await openPmSection(page,'discussions','ui_meetings');await page.locator('[data-pm-review-step="4"]').getByRole('button',{name:'Decide direction',exact:true}).click();
    await expect(page.locator('[data-pm-action="pm.governance.decide"]')).toBeVisible();
  }
  try{
    hold=true;if(load==='main-section')await page.locator('[data-pm-refresh]').click();else cursor++;
    await expect.poll(()=>held).toBe(1);deny=true;
    if(load.endsWith('section'))await openPmSection(page,'issues','ui_governance');else await page.getByRole('button',{name:'Refresh findings',exact:true}).click();
    await expect.poll(()=>denied).toBeGreaterThan(0);await expect(page.locator('body')).toContainText(refusal.data.error.message);
    expect((await page.locator('.pm-thread-link').allTextContents()).join(' ')).not.toContain(label);
    await observe(page,label);release();await neverRestored(page,()=>done,label);
    await expect(page.locator('.pm-thread-link').filter({hasText:other})).toHaveCount(1);
    expect(f.state.requests.filter((r:any)=>r.method!=='GET')).toEqual([]);expect(f.state.providerCalls).toBe(0);
  }finally{release();}
});

for(const native of [false,true])for(const panel of ['meetings','governance'])test(`withdrawal invalidates first ${panel} refresh ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=guidedReviewFixture();seedGuidedReview(f,4);let hold=false,held=0,done=0,deny=false,denied=0,release=()=>{};
  const gate=new Promise<void>(resolve=>release=resolve),mid=f.detail.meeting.meeting_id,gid=f.governance.state.records[0].record.record_id;
  const late=panel==='meetings'?'/meetings/'+mid:'/governance/'+gid,failed=panel==='meetings'?'/governance/'+gid:'/meetings/'+mid;
  const marker=panel==='meetings'?'SOURCE_FEEDBACK':'SOURCE_GOVERNANCE_TITLE';
  await mountConversation(page,native,async request=>{
    const path=new URL(request.path,'http://fixture').pathname;
    if(deny&&path.endsWith(failed)){denied++;return refusal;}
    const reply=await f.dispatch(request);if(hold&&path.endsWith(late)){hold=false;held++;await gate;done++;}return reply;
  });
  await page.goto(route);
  if(panel==='meetings'){await openPmSection(page,'issues','ui_governance');await expect(page.locator('body')).toContainText('SOURCE_GOVERNANCE');}
  else{await openPmSection(page,'discussions','ui_meetings');await expect(page.locator('body')).toContainText('SOURCE_FEEDBACK');}
  try{
    hold=true;await openPmSection(page,panel==='meetings'?'discussions':'issues',panel==='meetings'?'ui_meetings':'ui_governance');await expect.poll(()=>held).toBe(1);
    deny=true;await openPmSection(page,panel==='meetings'?'issues':'discussions',panel==='meetings'?'ui_governance':'ui_meetings');await page.getByRole('button',{name:panel==='meetings'?'Refresh findings':'Refresh meetings',exact:true}).click();await expect.poll(()=>denied).toBeGreaterThan(0);
    await expect(page.locator('body')).toContainText(refusal.data.error.message);expect(await page.locator('body').textContent()).not.toContain(marker);
    await observe(page,marker);release();await neverRestored(page,()=>done,marker);
  }finally{release();}
});
