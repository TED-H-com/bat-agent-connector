import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {fixture,mount,intakeId,workspaceId,threadId} from './pm-follow-up-fixture.ts';

const assessmentId=(n:number)=>'pmfa_'+n.toString(16).padStart(32,'0');
function assessment(n:number,intake=3) {
  const refs=['knowledge.scope'];
  return {sequence:n,assessment_id:assessmentId(n),workspace_id:workspaceId,thread_id:threadId,operation_id:'op_'+'a'.repeat(32),
    run_id:'pmr_'+'b'.repeat(32),intake_id:intakeId(intake),intake_digest:String(intake).repeat(64),context_digest:'c'.repeat(64),
    raw_reply:'  <script>window.agentMarkupRan=true</script>\nOriginal analysis <b>remains literal</b>  ',raw_reply_digest:'d'.repeat(64),document_digest:'e'.repeat(64),
    document:{version:1,summary:{text:'Recovery is reported complete, but acceptance evidence remains missing.',source_refs:refs},
      findings:[{kind:'deviation',text:'A successful login does not verify password recovery.',impact:'Members could remain locked out.',source_refs:refs}],
      tradeoffs:[{decision:'How should recovery be validated?',options:[{label:'Verify before release',consequence:'Release waits for a bounded recovery check.'},{label:'Keep exploring',consequence:'The release decision remains unresolved.'}],source_refs:refs}],
      questions:[{question:'Which recovery evidence is required for this release?',why_now:'The release has no agreed recovery verification.',options:[{label:'Exercise the full reset flow',consequence:'Checks the member outcome.'},{label:'Retain as undecided',consequence:'No new execution is authorized.'}],source_refs:refs}],
      recommendations:[{text:'Review the fixed repair result against the accepted recovery behavior.',proposed_owner:'project-owner',source_refs:refs}]},
    validation:{status:'format_valid',errors:[],conclusions_verified:false},
    provenance:{intake_id:intakeId(intake),intake_digest:String(intake).repeat(64),run_id:'pmr_'+'b'.repeat(32),context_digest:'c'.repeat(64),
      source_references:{'knowledge.scope':{pointer:'/knowledge/scope',digest:'f'.repeat(64)}},actual_model:'fixture-model'},
    created_at:1791705700+n,created_by:'project-owner',assessment_basis:'unadopted_agent_analysis',conclusions_verified:false,accepted_plan_changed:false,execution_authorized:false};
}
function assessedFixture() {
  const f=fixture(),baseCaps=f.caps,baseDispatch=f.dispatch;
  Object.assign(f.state,{assessments:[assessment(1),assessment(2),assessment(3)],hiddenAssessments:new Set(),failAssessments:null,assessmentReads:[],failAssessmentDetail:null,holdAssessment:false,heldAssessments:[],
    runtimeEligible:true,start:true,analysisPending:false,analysisPolls:0,loseAnalysisReply:false});
  f.caps=()=>({...baseCaps(),scopes:[...baseCaps().scopes,...(f.state.start?['start']:[])],features:{pm_workspaces:{follow_up_analysis:true}},
    actions:[...baseCaps().actions,{action:'pm.follow_up.analyze',allowed:f.state.manage&&f.state.start}]});
  f.dispatch=async input=>{
    const url=new URL(input.path,'http://fixture'),base=`/pm/workspaces/${workspaceId}/follow-up-assessments`;
    if(input.method==='POST'&&input.body?.action==='pm.follow_up.analyze'){
      f.state.writes.push(structuredClone(input));let operation=f.state.ops.get(input.key);
      if(!operation){
        const n=f.state.assessments.length+1,value=assessment(n,Number.parseInt(input.body.params.intake_id.slice(5),16));f.state.assessments.push(value);
        operation={...structuredClone(input.body),operation_id:'op_'+'e'.repeat(32),actor:f.state.actor,idempotency_key:input.key,status:f.state.analysisPending?'running':'succeeded',
          result:{run_id:value.run_id,outcome:{assessment_id:value.assessment_id,intake_id:value.intake_id}}};f.state.ops.set(input.key,operation);
      }
      if(f.state.loseAnalysisReply){f.state.loseAnalysisReply=false;return {status:503,data:{error:{code:'UNAVAILABLE',message:'Original analysis reply lost'}}};}
      return {status:200,data:{operation}};
    }
    if(url.pathname==='/operations/op_'+'e'.repeat(32)){
      f.state.analysisPolls++;const operation=[...f.state.ops.values()].find((row:any)=>row.action==='pm.follow_up.analyze');operation.status='succeeded';
      return {status:200,data:{operation}};
    }
    if(url.pathname===`/pm/workspaces/${workspaceId}/capabilities`)return {status:200,data:{workspace_id:workspaceId,follow_up_analysis:{authorized:f.state.manage&&f.state.start,eligible_for_preflight:f.state.runtimeEligible,reason:'PM_NATIVE_EXECUTABLE_UNAVAILABLE'}}};
    if(url.pathname===`/pm/workspaces/${workspaceId}/threads/${threadId}`){
      const response=await baseDispatch(input);if(response.status===200){response.data.thread.mode='planning';response.data.capabilities={write:f.state.manage&&['owner','contributor'].includes(f.state.role)};}return response;
    }
    if(!url.pathname.startsWith(base))return baseDispatch(input);
    f.state.assessmentReads.push(input.path);
    const error=(status:number,code=status===403?'FORBIDDEN':'PM_FOLLOW_UP_ANALYSIS_UNAVAILABLE')=>({status,data:{error:{code,message:'Assessment source unavailable'}}});
    if(f.state.failAssessments)return error(f.state.failAssessments);
    let data;
    if(url.pathname===base){
      const after=Number(url.searchParams.get('after')||0),limit=Number(url.searchParams.get('limit')||20);
      const candidates=f.state.assessments.filter((row:any)=>row.intake_id===url.searchParams.get('intake_id')&&row.sequence>after);
      const page=candidates.slice(0,limit),records=page.filter((row:any)=>!f.state.hiddenAssessments.has(row.assessment_id));
      data={version:1,workspace_id:workspaceId,assessments:records.map(({document,raw_reply,provenance,...row}:any)=>row),
        next_cursor:candidates.length>limit?page.at(-1).sequence:null,coverage:{visibility_filtered:page.length!==records.length}};
    }else{
      if(f.state.failAssessmentDetail)return error(f.state.failAssessmentDetail);
      const id=url.pathname.split('/').at(-1),row=f.state.assessments.find((row:any)=>row.assessment_id===id);
      if(!row)return error(404,'PM_FOLLOW_UP_ANALYSIS_NOT_FOUND');
      if(f.state.hiddenAssessments.has(id))return error(404,'PM_FOLLOW_UP_SOURCE_UNAVAILABLE');
      data={version:1,workspace_id:workspaceId,assessment:row};
    }
    const response={status:200,data:structuredClone(data)};
    if(f.state.holdAssessment&&url.pathname!==base){f.state.holdAssessment=false;return new Promise(resolve=>f.state.heldAssessments.push(()=>resolve(response)));}
    return response;
  };
  return f;
}
const section=(page:any)=>page.locator('[data-pm-follow-up-assessments]');
const select=(page:any,n=1)=>section(page).getByRole('button',{name:new RegExp(`^Assessment ${n} ·`)}).click();
const refresh=(page:any)=>page.evaluate(()=>(window as any).refreshFollowUp());
async function openAnalysis(page:any){await page.locator('[data-pm-follow-up-analyze] > summary').click();await expect(page.getByLabel('What should PM assess?',{exact:true})).toBeVisible();}

test('assessment is read in the selected preparation with quoted sources and explicit proposal semantics',async({page})=>{
  const f=assessedFixture();await mount(page,f);await select(page);
  await expect(section(page)).toContainText('Format and source references checked');
  await expect(section(page)).toContainText('conclusions have not been verified');
  await expect(section(page)).toContainText('Suggested owner — not assigned');
  await section(page).getByText('Read the quoted preparation sources',{exact:true}).first().click();
  await expect(section(page)).toContainText('Members can recover access to their account.');
  await section(page).getByText('Original agent reply',{exact:true}).click();
  await expect(section(page).locator('pre').last()).toHaveText(f.state.assessments[0].raw_reply,{useInnerText:false});
  expect(await page.evaluate(()=>(window as any).agentMarkupRan)).toBeUndefined();
  expect(f.state.writes).toHaveLength(0);await expect(page.getByRole('tab')).toHaveCount(0);
});

test('assessment lists only fixed preparation results and explicitly loads an older result',async({page})=>{
  const f=assessedFixture();f.state.assessments.push(assessment(4,1));await mount(page,f);
  expect(f.state.assessmentReads).toEqual([`/pm/workspaces/${workspaceId}/follow-up-assessments?intake_id=${intakeId(3)}&after=0&limit=2`]);
  await expect(section(page)).not.toContainText('Recovery is reported complete');await select(page,2);
  await expect(section(page).locator('[data-pm-assessment-record]')).toHaveAttribute('data-pm-assessment-record',assessmentId(2));
  await page.getByText('Preparation history',{exact:true}).click();await page.getByLabel('Saved preparation',{exact:true}).selectOption(intakeId(1));
  await expect(section(page)).not.toContainText('Recovery is reported complete');
  await expect(section(page).getByRole('button',{name:/^Assessment 4 ·/})).toBeVisible();
  expect(f.state.writes).toHaveLength(0);
});

test('invalid structured response retains raw reply without rendering a professional assessment',async({page})=>{
  const f=assessedFixture();f.state.assessments[0].validation={status:'needs_review',errors:['questions[3]: limit exceeded'],conclusions_verified:false};
  f.state.assessments[0].document=null;await mount(page,f);await select(page);
  await expect(section(page)).toContainText('Response needs review');await expect(section(page)).toContainText('questions[3]: limit exceeded');
  await expect(section(page).getByRole('heading',{name:'Findings and impact'})).toHaveCount(0);
  await section(page).getByText('Original agent reply',{exact:true}).click();await expect(section(page)).toContainText('Original analysis');
});

for(const error of [403,404,503])test(`assessment refresh ${error} removes quoted facts and stale metadata`,async({page})=>{
  const f=assessedFixture();await mount(page,f);await select(page);await expect(section(page)).toContainText('Members could remain locked out');
  f.state.failAssessments=error;await refresh(page);await expect(page.locator('[data-pm-follow-up]')).toContainText('Assessment source unavailable');
  await expect(page.locator('[data-pm-follow-up]')).not.toContainText('Members could remain locked out');
  await expect(section(page).getByRole('button',{name:/^Assessment /})).toHaveCount(0);
  expect(f.state.writes).toHaveLength(0);
});

test('an inaccessible selected assessment is not silently replaced by another result',async({page})=>{
  const f=assessedFixture();await mount(page,f);await select(page);f.state.hiddenAssessments.add(assessmentId(1));await refresh(page);
  await expect(page.locator('[data-pm-follow-up]')).toContainText('Assessment source unavailable');await expect(section(page)).toHaveCount(0);
  expect(f.state.assessmentReads.at(-1)).toBe(`/pm/workspaces/${workspaceId}/follow-up-assessments/${assessmentId(1)}`);
});

test('hidden metadata pages retain their cursor and permit forward scanning',async({page})=>{
  const f=assessedFixture();[1,2].forEach(n=>f.state.hiddenAssessments.add(assessmentId(n)));await mount(page,f);
  await expect(section(page)).toContainText('more history is available');await section(page).getByRole('button',{name:'Load more assessments'}).click();
  await select(page,3);await expect(section(page)).toContainText('Recovery is reported complete');
});

test('late analysis cannot restore a result after parent source withdrawal',async({page})=>{
  const f=assessedFixture();await mount(page,f);f.state.holdAssessment=true;await select(page);
  await expect.poll(()=>f.state.heldAssessments.length).toBe(1);f.state.hiddenIntakes.add(intakeId(3));await refresh(page);
  await f.state.heldAssessments[0]();await expect(section(page)).toHaveCount(0);
  await expect(page.locator('[data-pm-follow-up]')).not.toContainText('Members could remain locked out');
});

for(const mutate of ['digest','reference','claims_verified'])test(`assessment ${mutate} mismatch fails closed`,async({page})=>{
  const f=assessedFixture();await mount(page,f);
  if(mutate==='digest')f.state.assessments[0].intake_digest='0'.repeat(64);
  if(mutate==='reference')f.state.assessments[0].document.summary.source_refs=['invented'];
  if(mutate==='claims_verified')f.state.assessments[0].conclusions_verified=true;
  await select(page);await expect(section(page)).toContainText('does not match its fixed source');
  await expect(section(page).locator('[data-pm-assessment-record]')).toHaveCount(0);
});

test('one analysis action fixes intake, digest and thread version then reads its assessment',async({page})=>{
  const f=assessedFixture();await mount(page,f);await openAnalysis(page);
  await page.getByLabel('What should PM assess?',{exact:true}).fill('Explain the recovery evidence gap and realistic choices.');
  await page.getByRole('button',{name:'Analyze this preparation',exact:true}).click();
  await expect(section(page).locator('[data-pm-assessment-record]')).toHaveAttribute('data-pm-assessment-record',assessmentId(4));
  expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].body).toEqual({action:'pm.follow_up.analyze',target:{workspace_id:workspaceId,thread_id:threadId},
    params:{intake_id:intakeId(3),purpose:'Explain the recovery evidence gap and realistic choices.'},preconditions:{expected_version:5,expected_intake_digest:'3'.repeat(64)}});
  await expect(page.getByLabel('PM discussion',{exact:true})).toBeDisabled();
});

test('acknowledged native analysis polls only its original operation and opens the saved assessment',async({page})=>{
  const f=assessedFixture();f.state.analysisPending=true;await mount(page,f);await openAnalysis(page);
  await page.getByLabel('What should PM assess?',{exact:true}).fill('Assess the fixed source.');await page.getByRole('button',{name:'Analyze this preparation',exact:true}).click();
  await expect(section(page).locator('[data-pm-assessment-record]')).toHaveAttribute('data-pm-assessment-record',assessmentId(4));
  expect(f.state.analysisPolls).toBe(1);expect(f.state.writes).toHaveLength(1);
});

test('lost admission stays on the original preparation and key across refresh',async({page})=>{
  const f=assessedFixture();f.state.loseAnalysisReply=true;await mount(page,f);await openAnalysis(page);
  await page.getByLabel('What should PM assess?',{exact:true}).fill('Keep this fixed analysis.');await page.getByRole('button',{name:'Analyze this preparation',exact:true}).click();
  await expect(page.locator('[data-pm-follow-up-analyze]')).toContainText('Original analysis reply lost');
  const original=structuredClone(f.state.writes[0]);f.state.threadVersion=6;await refresh(page);
  await page.locator('[data-pm-follow-up-analyze]').getByRole('button',{name:'Check original request',exact:true}).click();
  await expect(section(page).locator('[data-pm-assessment-record]')).toHaveAttribute('data-pm-assessment-record',assessmentId(4));
  expect(f.state.writes.at(-1)).toEqual(original);expect(f.state.ops.size).toBe(1);
});

test('unsent analysis preserves purpose and requires an explicit thread-version rebase',async({page})=>{
  const f=assessedFixture();await mount(page,f);await openAnalysis(page);await page.getByLabel('What should PM assess?',{exact:true}).fill('Review the saved scope.');
  f.state.threadVersion=6;await refresh(page);await expect(page.getByLabel('What should PM assess?',{exact:true})).toHaveValue('Review the saved scope.');
  await expect(page.getByRole('button',{name:'Analyze this preparation',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Use the current discussion version',exact:true}).click();await page.getByRole('button',{name:'Analyze this preparation',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.preconditions.expected_version).toBe(6);
});

for(const reason of ['reader','start','runtime'])test(`analysis respects ${reason} capability without hiding saved results`,async({page})=>{
  const f=assessedFixture();if(reason==='reader'){f.state.manage=false;f.state.role='reader';}if(reason==='start')f.state.start=false;if(reason==='runtime')f.state.runtimeEligible=false;
  await mount(page,f);await select(page);await openAnalysis(page);await expect(page.getByRole('button',{name:'Analyze this preparation',exact:true})).toBeDisabled();
  await expect(section(page)).toContainText('Recovery is reported complete');expect(f.state.writes).toHaveLength(0);
});

test('analysis submission disappears when the preparation source is withdrawn',async({page})=>{
  const f=assessedFixture();await mount(page,f);await openAnalysis(page);await page.getByLabel('What should PM assess?',{exact:true}).fill('Keep my analysis draft.');
  f.state.hiddenIntakes.add(intakeId(3));await refresh(page);await expect(page.locator('[data-pm-follow-up-analyze]')).toHaveCount(0);
  expect(f.state.writes).toHaveLength(0);expect(await page.evaluate(()=>Object.values(localStorage).some(value=>value.includes('Keep my analysis draft.')))).toBe(true);
});

test('unbound preparation selects a PM discussion using real string cursors',async({page})=>{
  const f=assessedFixture();f.state.rows.at(-1).document.thread_ref=null;
  const second='pmt_'+'d'.repeat(32),original=f.dispatch,paths:string[]=[];
  f.dispatch=async input=>{
    const url=new URL(input.path,'http://fixture');
    if(url.pathname===`/pm/workspaces/${workspaceId}/threads`){
      paths.push(input.path);const after=url.searchParams.get('after');
      if(after!==null&&after!==threadId)return {status:400,data:{error:{code:'INVALID_CURSOR',message:'Thread cursor must be a pmt identifier'}}};
      return {status:200,data:{workspace_id:workspaceId,threads:[{workspace_id:workspaceId,thread_id:after?second:threadId,title:after?'Second discussion':'First discussion',mode:'planning',version:5}],next_cursor:after?null:threadId}};
    }
    if(url.pathname===`/pm/workspaces/${workspaceId}/threads/${second}`)return {status:200,data:{workspace_id:workspaceId,thread:{workspace_id:workspaceId,thread_id:second,version:8,mode:'planning'},capabilities:{write:true}}};
    return original(input);
  };
  await mount(page,f);await page.locator('[data-pm-follow-up-analyze] > summary').click();
  await expect(page.getByLabel('PM discussion',{exact:true})).toBeEnabled();await page.getByRole('button',{name:'Load more discussions',exact:true}).click();
  await page.getByLabel('PM discussion',{exact:true}).selectOption(second);await page.getByLabel('What should PM assess?',{exact:true}).fill('Assess this source with the selected PM discussion.');
  await page.getByRole('button',{name:'Analyze this preparation',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(paths).toEqual([`/pm/workspaces/${workspaceId}/threads?limit=20`,`/pm/workspaces/${workspaceId}/threads?after=${threadId}&limit=20`]);
  expect(f.state.writes[0].body.target.thread_id).toBe(second);expect(f.state.writes[0].body.preconditions.expected_version).toBe(8);
});

test('assessment source loss clears its parent facts and unsent analysis while preserving the draft',async({page})=>{
  const f=assessedFixture();await mount(page,f);await openAnalysis(page);await page.getByLabel('What should PM assess?',{exact:true}).fill('Preserve this private-context draft.');
  f.state.hiddenAssessments.add(assessmentId(1));await select(page);
  await expect(page.locator('[data-pm-follow-up]')).toContainText('Assessment source unavailable');
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);await expect(page.locator('[data-pm-follow-up-analyze]')).toHaveCount(0);
  expect(await page.evaluate(()=>Object.values(localStorage).some(value=>value.includes('Preserve this private-context draft.')))).toBe(true);
  expect(f.state.writes).toHaveLength(0);
});

test('request-side authority failure clears parent knowledge before a native request can start',async({page})=>{
  const f=assessedFixture(),original=f.dispatch;f.dispatch=input=>input.path.endsWith('/capabilities')
    ?Promise.resolve({status:403,data:{error:{code:'FORBIDDEN',message:'Current workspace authority withdrawn'}}}):original(input);
  await mount(page,f);await page.locator('[data-pm-follow-up-analyze] > summary').click();
  await expect(page.locator('[data-pm-follow-up]')).toContainText('Current workspace authority withdrawn');
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);await expect(page.locator('[data-pm-follow-up-analyze]')).toHaveCount(0);
  expect(f.state.writes).toHaveLength(0);
});

test('a late rejected analysis from an old preparation cannot clear the current readable preparation',async({page})=>{
  const f=assessedFixture(),original=f.dispatch;let release:any;
  f.dispatch=input=>input.method==='POST'&&input.body?.action==='pm.follow_up.analyze'
    ?new Promise(resolve=>{release=()=>resolve({status:403,data:{error:{code:'FORBIDDEN',message:'Old request authority withdrawn'}}});}):original(input);
  await mount(page,f);await openAnalysis(page);await page.getByLabel('What should PM assess?',{exact:true}).fill('Read preparation three.');
  await page.getByRole('button',{name:'Analyze this preparation',exact:true}).click();await expect.poll(()=>Boolean(release)).toBe(true);
  await page.getByText('Preparation history',{exact:true}).click();await page.getByLabel('Saved preparation',{exact:true}).selectOption(intakeId(1));
  await expect(page.locator('[data-pm-follow-up]').getByText('Preparation 1',{exact:true})).toBeVisible();
  await release();await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(8);
  await expect(page.locator('[data-pm-follow-up]')).not.toContainText('Old request authority withdrawn');
  await expect(page.getByLabel('What should PM assess?',{exact:true})).toHaveValue('');
});

for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`assessment shares the compact workspace layout ${locale} ${width}`,async({page},info)=>{
  const f=assessedFixture();await page.setViewportSize({width,height:900});await mount(page,f,{locale});
  await section(page).getByRole('button',{name:new RegExp(`^${locale==='en'?'Assessment':'評估'} 1 ·`)}).click();
  await expect(section(page).locator('[data-pm-assessment-record]')).toBeVisible();
  const out=process.env.BATC_PM_ASSESSMENT_SCREENSHOTS||info.outputPath('screenshots');await mkdir(out,{recursive:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:`${out}/assessment-${locale}-${width}.png`,fullPage:true});
  if(width===1440){await page.evaluate(()=>document.documentElement.dataset.theme='dark');await page.screenshot({path:`${out}/assessment-${locale}-${width}-dark.png`,fullPage:true});}
  await expect(section(page)).not.toContainText('pm_fa_');
});
