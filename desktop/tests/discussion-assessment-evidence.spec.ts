import {test, expect} from '@playwright/test';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';

const reviewId='pmqr_'+'a'.repeat(32), operationId='op_'+'b'.repeat(32), digest='c'.repeat(64), workId='wi_'+'d'.repeat(20);
function fixture(locale='en') {
  const f=demoFixture({locale,discussions:true}), wid=f.ids.workspaceId, tid=f.ids.threadIdea, root=`/pm/workspaces/${wid}`;
  const ref={kind:'quality_assessment',id:operationId,review_id:reviewId,document_digest:digest,available:true,position:0,label:'Login retry'};
  f.discussion.details.get(tid).thread.context=[ref];
  const criterion={kind:'acceptance',requirement_id:'login',input_id:'pmi_'+'e'.repeat(32)};
  const summary={workspace_id:wid,review_id:reviewId,semantics:'immutable_original_assessment',current_applicability:'not_evaluated',
    context:{native_result:{candidate_commit:'f'.repeat(40)},reviewer_actor:'reviewer',criteria:[{criterion,text:'Retry after a failed login succeeds'}]},
    assessment:{operation_id:operationId,document_digest:digest,version:2,actor:'reviewer',document:{acceptance:'unassessed',dod:'not_defined',
      rationale:'Keep the missing proof visible.',criteria:[{criterion,status:'unassessed',rationale:'No fixed revision test receipt was supplied.',verification:null}]}}};
  const state={reads:0,writes:[] as any[],withdrawn:false,omit:false,mismatch:false,hold:null as null|Promise<void>};
  const dispatch=async(input:any)=>{
    if(input.method!=='GET')state.writes.push(input);
    const path=new URL(input.path,'http://fixture').pathname;
    if(path===`${root}/threads/${tid}/context`){
      state.reads++;const held=state.hold;const value=structuredClone({workspace_id:wid,thread_id:tid,bound_context:[state.withdrawn?{kind:ref.kind,position:0,available:false}:{...ref,...(state.omit?{}:{summary})}],coverage:{omitted_context_positions:state.omit?[0]:[]}});
      if(state.mismatch&&(value.bound_context[0] as any).summary)(value.bound_context[0] as any).summary.assessment.document_digest='0'.repeat(64);
      if(held)await held;return {status:200,data:value};
    }
    if(path===`${root}/quality-reviews/${reviewId}`)return state.withdrawn?{status:404,data:{error:{code:'NOT_FOUND',message:'Unavailable'}}}:{status:200,data:{workspace_id:wid,review:{review_id:reviewId,work_item_id:workId,history:[{kind:'assessment',operation_id:operationId,document_digest:digest}]}}};
    if(path===`${root}/kanban/${workId}`)return {status:200,data:{workspace_id:wid,card:{work_item_id:workId,work:{work_item_id:workId,title:'Login retry'},binding:{workspace_id:wid,work_item_id:workId}}}};
    return f.dispatch(input);
  };
  return {f,state,dispatch,route:`/dashboard/?workbench=1#/pm/${wid}/discussions/${tid}`};
}

for(const native of [false,true])for(const [locale,width] of [['en',1440],['zh-TW',390],['zh-TW',768]] as const){
  test(`reads exact assessment beside discussion ${native} ${locale} ${width}`,async({browser,baseURL},info)=>{
    const context=await browser.newContext({baseURL,locale,viewport:{width,height:width===390?844:900}}),page=await context.newPage(),f=fixture(locale);
    await mountConversation(page,native,f.dispatch);await page.goto(f.route);
    const composer=page.locator('[data-discussion-composer]');await composer.fill('Keep my unsent question');
    expect(f.state.reads).toBe(0);
    await page.locator('.pd-context [data-discussion-context="quality_assessment"]').click();
    await page.screenshot({path:info.outputPath('context-before.png'),animations:'disabled'});
    const evidence=page.locator('[data-discussion-assessment]');
    await expect(evidence).toContainText('Retry after a failed login succeeds');
    await evidence.getByText('Retry after a failed login succeeds',{exact:false}).click();
    await expect(evidence).toContainText('No fixed revision test receipt was supplied.');
    await expect(evidence).toContainText(locale==='en'?'Current acceptance has not been checked here.':'此處未重新判定目前是否符合驗收。');
    await expect(composer).toHaveValue('Keep my unsent question');
    expect(f.state.reads).toBe(1);expect(f.state.writes).toEqual([]);
    await page.screenshot({path:info.outputPath('context-after.png'),animations:'disabled'});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await context.close();
  });
}

for(const mode of ['withdrawn','omitted','mismatched'] as const)test(`does not substitute an unavailable assessment ${mode}`,async({page})=>{
  const f=fixture();if(mode==='omitted')f.state.omit=true;if(mode==='mismatched')f.state.mismatch=true;
  await mountConversation(page,false,f.dispatch);await page.goto(f.route);
  if(mode==='withdrawn')f.state.withdrawn=true;
  await page.locator('.pd-context [data-discussion-context="quality_assessment"]').click();
  const evidence=page.locator('[data-discussion-assessment]');
  await expect(evidence).toContainText(mode==='omitted'?'The complete assessment was not included':'The original assessment is unavailable');
  await expect(evidence).not.toContainText('Retry after a failed login succeeds');expect(f.state.writes).toEqual([]);
});

test('a late original context cannot restore a withdrawn assessment',async({page})=>{
  const f=fixture();let release!:()=>void;f.state.hold=new Promise<void>(resolve=>release=resolve);
  await mountConversation(page,false,f.dispatch);await page.goto(f.route);
  await page.locator('.pd-context [data-discussion-context="quality_assessment"]').click();
  await expect.poll(()=>f.state.reads).toBe(1);
  f.state.withdrawn=true;f.state.hold=null;await page.locator('[data-pm-refresh]').click();
  const evidence=page.locator('[data-discussion-assessment]');
  await expect(evidence).toContainText('The original assessment is unavailable');
  const completed=page.waitForResponse(response=>response.url().includes('/context?max_chars='));release();await completed;
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>resolve())));
  await expect(evidence).not.toContainText('Retry after a failed login succeeds');
  await expect(evidence).toContainText('The original assessment is unavailable');expect(f.state.writes).toEqual([]);
});

test('an unrelated discussion does not request assessment context',async({page})=>{
  const f=fixture();f.f.discussion.details.get(f.f.ids.threadIdea).thread.context=[];
  await mountConversation(page,false,f.dispatch);await page.goto(f.route);
  await page.locator('[data-discussion-panel-toggle]').click();
  await expect(page.locator('[data-discussion-assessment]')).toBeHidden();
  expect(f.state.reads).toBe(0);expect(f.state.writes).toEqual([]);
});
