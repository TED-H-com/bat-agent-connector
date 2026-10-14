import {openPmSection, expectPmWithdrawn} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {pmFixture,workspaceId} from './pm-fixture.ts';
import {fixture as followUpFixture,snapshotId,intakeId,threadId as followUpThreadId} from './pm-follow-up-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {structuredCapability,BAT,NATIVE} from './pm-structured-analysis-fixture.ts';

const assessmentId=(n:number)=>'pmfa_'+n.toString(16).padStart(32,'0');
function assessment(n:number){
  const runId='pmr_'+n.toString(16).padStart(32,'0'),contextDigest='c'.repeat(64);
  return {sequence:n,assessment_id:assessmentId(n),workspace_id:workspaceId,thread_id:followUpThreadId,
    operation_id:'op_'+(100+n).toString(16).padStart(32,'0'),run_id:runId,intake_id:intakeId(3),intake_digest:'3'.repeat(64),context_digest:contextDigest,
    raw_reply:'A fixed-source PM assessment. Scope and execution remain unchanged.',raw_reply_digest:'d'.repeat(64),document_digest:'e'.repeat(64),
    document:{version:1,summary:{text:'The reported recovery repair still needs acceptance evidence.',source_refs:['knowledge.scope']},
      findings:[],tradeoffs:[],questions:[],recommendations:[]},validation:{status:'format_valid',errors:[],conclusions_verified:false},
    provenance:{run_id:runId,context_digest:contextDigest,intake_id:intakeId(3),intake_digest:'3'.repeat(64),actual_model:'fixture-model',
      source_references:{'knowledge.scope':{pointer:'/knowledge/scope',digest:'f'.repeat(64)}}},
    created_at:1791705700+n,created_by:'pm:'+workspaceId,assessment_basis:'unadopted_agent_analysis',conclusions_verified:false,
    accepted_plan_changed:false,execution_authorized:false};
}
function fixture({analysisEnabled=false}={}){
  const base=pmFixture(),followUp=followUpFixture(),prefix=`/pm/workspaces/${workspaceId}`;
  followUp.state.actor=base.state.actor;
  base.state.start=analysisEnabled;
  const analysis={enabled:analysisEnabled,rows:[assessment(1)],reads:[] as string[],writes:[] as any[],ops:new Map<string,any>(),
    lost:false,pending:false,polls:0,eventType:'pm_follow_up',structured_analysis:null as any};
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),p=url.pathname;
    if(p==='/bootstrap'||p==='/capabilities'){
      const result=await base.dispatch(input),caps=p==='/bootstrap'?result.data.capabilities:result.data;
      caps.features.pm_workspaces.follow_up_prepare=true;caps.actions.push(...followUp.caps().actions);
      caps.features.pm_workspaces.follow_up_analysis=analysis.enabled;
      if(analysis.enabled)caps.actions.push({action:'pm.follow_up.analyze',allowed:true});return result;
    }
    if(p==='/events'){
      const result=await base.dispatch(input);for(const event of result.data.events){event.resource_type=analysis.eventType;
        event.kind=analysis.eventType==='pm_follow_up_assessment'?'pm.follow_up.analyze':'pm.follow_up.prepare';}return result;
    }
    if(input.method==='POST'&&input.body?.action==='pm.follow_up.analyze'){
      analysis.writes.push(structuredClone(input));let operation=analysis.ops.get(input.idempotency_key);
      if(!operation){
        if(input.body.preconditions.expected_version!==followUp.state.threadVersion||input.body.preconditions.expected_intake_digest!=='3'.repeat(64))
          return {status:409,data:{error:{code:'VERSION_CONFLICT',message:'The fixed discussion version changed',admission_refused:true}}};
        const row=assessment(analysis.rows.length+1);analysis.rows.push(row);
        operation={...structuredClone(input.body),operation_id:row.operation_id,actor:base.state.actor,idempotency_key:input.idempotency_key,
          status:analysis.pending?'running':'succeeded',result:{run_id:row.run_id,workspace_id:workspaceId,thread_id:followUpThreadId,
            state:'completed',context_digest:row.context_digest,adapter:'codex-native-linux',execution_authorized:false,
            outcome:{assessment_id:row.assessment_id,intake_id:row.intake_id,validation_status:'format_valid',conclusions_verified:false,
              assessment_adopted:false,assistant_imported:false}}};analysis.ops.set(input.idempotency_key,operation);
      }
      if(analysis.lost){analysis.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Original assessment reply lost'}}};}
      return {status:200,data:{operation:structuredClone(operation)}};
    }
    const operation=[...analysis.ops.values()].find(row=>p===`/operations/${row.operation_id}`);
    if(operation){analysis.polls++;operation.status='succeeded';return {status:200,data:{operation:structuredClone(operation)}};}
    if(p===prefix+'/capabilities'){
      const result=await base.dispatch(input);result.data.follow_up_analysis={authorized:true,eligible_for_preflight:true};if(analysis.structured_analysis)result.data.structured_analysis=analysis.structured_analysis;return result;
    }
    if(p===prefix+'/threads/'+followUpThreadId&&analysis.enabled){
      const result=await followUp.dispatch(input);if(result.status===200){result.data.thread.mode='planning';result.data.capabilities={write:true};}return result;
    }
    if(p.startsWith(prefix+'/follow-up-assessments')){
      analysis.reads.push(input.path);
      if(p===prefix+'/follow-up-assessments'){
        const after=Number(url.searchParams.get('after')||0),limit=Number(url.searchParams.get('limit')||20);
        const records=analysis.rows.filter(row=>row.intake_id===url.searchParams.get('intake_id')&&row.sequence>after),page=records.slice(0,limit);
        return {status:200,data:{version:1,workspace_id:workspaceId,
          assessments:page.map(({document,raw_reply,provenance,...row})=>row),next_cursor:records.length>limit?page.at(-1)!.sequence:null,
          coverage:{visibility_filtered:false}}};
      }
      const row=analysis.rows.find(row=>p.endsWith('/'+row.assessment_id));
      return row?{status:200,data:{version:1,workspace_id:workspaceId,assessment:structuredClone(row)}}
        :{status:404,data:{error:{code:'PM_FOLLOW_UP_ANALYSIS_NOT_FOUND',message:'Assessment not found'}}};
    }
    if(p.startsWith(prefix+'/follow-ups')||p.startsWith(prefix+'/snapshots')||p===prefix+'/plans/head'
      ||p.startsWith(prefix+'/threads')||p.startsWith(prefix+'/governance')||p.startsWith(prefix+'/meetings')
      ||p.startsWith('/operations/')||input.method==='POST'&&input.body?.action==='pm.follow_up.prepare')
      return followUp.dispatch({...input,key:input.idempotency_key});
    return base.dispatch(input);
  };
  return{base,followUp,analysis,dispatch};
}
const route=`/dashboard/#/pm/${workspaceId}`,section=(page:any)=>page.locator('[data-pm-follow-up]'),editor=(page:any)=>page.locator('[data-pm-fu-editor]');
async function open(page:any){
  if(!await editor(page).evaluate((node:HTMLDetailsElement)=>node.open))await editor(page).locator(':scope > summary').click();
  await expect(page.getByLabel('Evidence snapshot',{exact:true})).toBeEnabled();
}
async function select(page:any){await open(page);await page.getByLabel('Evidence snapshot',{exact:true}).selectOption(snapshotId);await expect(editor(page)).toContainText('Selections stay fixed.');}
for(const native of [false,true])test.describe(native?'native workspace Follow-up':'web workspace Follow-up',()=>{
  test('fixed management knowledge and later answers retain their distinct provenance',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');
    await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(3);
    await expect(page.locator('[data-pm-fu-agenda] ol > li')).toHaveCount(1);
    await expect(page.locator('[data-pm-fu-domain=finance]')).toContainText('The first paid tier price is undecided.');
    await expect(page.locator('[data-pm-fu-domain=finance]')).toContainText('The accepted plan remains unchanged');
    await expect(section(page)).toContainText('No AI Follow-up, scope change or work execution occurred');
    expect(f.followUp.state.writes).toHaveLength(0);await expect(page.getByRole('tab')).toHaveCount(0);
    f.followUp.state.rows.at(-1).document.agenda[0].text='A sourced agenda update from another client';f.base.state.cursor++;
    await expect(section(page)).toContainText('A sourced agenda update from another client',{timeout:10000});
    expect(f.followUp.state.writes).toHaveLength(0);
  });
  test('explicit preparation recovers the original request after a missing reply and reload',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');await select(page);f.followUp.state.lost=true;
    await page.getByRole('button',{name:'Save discussion preparation',exact:true}).click();await expect(editor(page)).toContainText('The original reply was lost');
    const original=structuredClone(f.followUp.state.writes[0]);await page.reload();await openPmSection(page, 'issues' , 'ui_follow_up');
    if(!await editor(page).evaluate((node:HTMLDetailsElement)=>node.open))await editor(page).locator(':scope > summary').click();
    await page.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(section(page)).toContainText('Preparation 4');
    expect(f.followUp.state.ops.size).toBe(1);expect(f.followUp.state.writes.at(-1).body).toEqual(original.body);
    expect(f.followUp.state.writes.at(-1).key).toBe(original.key);expect(original.body.preconditions).toEqual({expected_version:3,expected_plan_version:2});
    expect(original.body.params.snapshot_id).toBe(snapshotId);expect(f.base.state.writes).toHaveLength(0);
  });
  test('failed parent read hides knowledge and preserves unsent fixed selections',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');await select(page);
    f.base.state.failRead=true;f.base.state.cursor++;
    await expect(section(page)).toHaveJSProperty('hidden',true,{timeout:10000});await expectPmWithdrawn(page,'issues','[data-pm-follow-up]','ui_follow_up');
    await expect(page.locator('.shell-workspace-block')).not.toContainText('Library refresh');
    f.base.state.failRead=false;f.base.state.cursor++;
    await expect(section(page)).toBeVisible({timeout:10000});await expect(page.getByLabel('Evidence snapshot',{exact:true})).toHaveValue(snapshotId);
    expect(f.followUp.state.writes).toHaveLength(0);
  });
  test('analysis feature flag keeps existing preparation available without mounting or reading assessments',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');
    await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(3);
    await expect(page.locator('[data-pm-follow-up-assessments]')).toHaveCount(0);
    await expect(page.locator('[data-pm-follow-up-analyze]')).toHaveCount(0);
    expect(f.analysis.reads).toEqual([]);expect(f.analysis.writes).toEqual([]);
  });
  test('home submits fixed analysis through the shared transport and polls only its original operation',async({page})=>{
    const f=fixture({analysisEnabled:true});f.analysis.pending=true;
    await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');
    await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(3);
    await page.locator('[data-pm-follow-up-analyze] > summary').click();
    await page.getByLabel('What should PM assess?',{exact:true}).fill('Compare the original recovery commitment with the fixed evidence.');
    await page.getByRole('button',{name:'Analyze this preparation',exact:true}).click();
    const result=page.locator('[data-pm-assessment-record]');await expect(result).toHaveAttribute('data-pm-assessment-record',assessmentId(2));
    await expect(result).toContainText('conclusions have not been verified');
    await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(3);await expect(page.getByRole('tab')).toHaveCount(0);
    expect(f.analysis.writes).toHaveLength(1);expect(f.analysis.polls).toBe(1);
    expect(f.analysis.writes[0].body).toEqual({action:'pm.follow_up.analyze',target:{workspace_id:workspaceId,thread_id:followUpThreadId},
      params:{intake_id:intakeId(3),purpose:'Compare the original recovery commitment with the fixed evidence.'},
      preconditions:{expected_version:5,expected_intake_digest:'3'.repeat(64)}});
    expect(f.analysis.writes[0].idempotency_key).toMatch(/^[0-9a-f-]{36}$/);
    expect(f.followUp.state.writes).toHaveLength(0);expect(f.base.state.writes).toHaveLength(0);
    expect(f.followUp.state.threadVersion).toBe(5);expect(f.followUp.state.plan).toBe(2);
  });
  test('assessment event refreshes the selected result on the workspace home without dispatching work',async({page})=>{
    const f=fixture({analysisEnabled:true});await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');
    const assessments=page.locator('[data-pm-follow-up-assessments]');
    await assessments.getByRole('button',{name:/^Assessment 1 ·/}).click();
    await expect(assessments).toContainText('The reported recovery repair still needs acceptance evidence.');
    const reads=f.analysis.reads.length;
    const later=assessment(2);later.document.summary.text='Another fixed assessment was saved; no scope decision was adopted.';
    f.analysis.rows.push(later);
    f.analysis.eventType='pm_follow_up_assessment';f.base.state.cursor++;
    await expect(assessments.getByRole('button',{name:/^Assessment 2 ·/})).toBeVisible({timeout:10000});
    await expect(page.locator('[data-pm-assessment-record]')).toHaveAttribute('data-pm-assessment-record',assessmentId(1));
    expect(f.analysis.reads.length).toBeGreaterThan(reads);
    expect(f.analysis.reads.at(-1)).toBe(`/pm/workspaces/${workspaceId}/follow-up-assessments/${assessmentId(1)}`);
    await assessments.getByRole('button',{name:/^Assessment 2 ·/}).click();
    await expect(assessments).toContainText('Another fixed assessment was saved; no scope decision was adopted.');
    await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(3);
    expect(f.analysis.writes).toHaveLength(0);expect(f.followUp.state.writes).toHaveLength(0);expect(f.base.state.writes).toHaveLength(0);
  });
  test('home reload keeps a lost analysis admission on the original key and thread CAS',async({page})=>{
    const f=fixture({analysisEnabled:true});f.analysis.lost=true;await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');
    await page.locator('[data-pm-follow-up-analyze] > summary').click();
    await page.getByLabel('What should PM assess?',{exact:true}).fill('Keep this exact analysis across reconnect.');
    await page.getByRole('button',{name:'Analyze this preparation',exact:true}).click();
    await expect(page.locator('[data-pm-follow-up-analyze]')).toContainText('Original assessment reply lost');
    const original=structuredClone(f.analysis.writes[0]);f.followUp.state.threadVersion=6;await page.reload();await openPmSection(page, 'issues' , 'ui_follow_up');
    await page.locator('[data-pm-follow-up-analyze] > summary').click();
    await page.locator('[data-pm-follow-up-analyze]').getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(page.locator('[data-pm-assessment-record]')).toHaveAttribute('data-pm-assessment-record',assessmentId(2));
    expect(f.analysis.ops.size).toBe(1);expect(f.analysis.writes).toHaveLength(2);
    expect(f.analysis.writes.at(-1).body).toEqual(original.body);
    expect(f.analysis.writes.at(-1).idempotency_key).toBe(original.idempotency_key);
    expect(original.body.preconditions.expected_version).toBe(5);
    expect(f.followUp.state.writes).toHaveLength(0);expect(f.base.state.writes).toHaveLength(0);
  });
});
for(const locale of ['en-US','zh-TW'])for(const width of [390,768,1440])test(`workspace Follow-up layout ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=fixture();
  await mountConversation(page,false,f.dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(3);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const out=process.env.PM_FOLLOW_UP_WORKSPACE_SCREENSHOTS;
  if(out){await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/${locale}-${width}.png`,fullPage:true});}
  await context.close();
});

for(const native of [false,true])for(const locale of ['en','zh-TW'])test(`structured Follow-up ${native?'Tauri':'Web'} ${locale} keeps original BAT admission across reload`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage();try{
  const f=fixture({analysisEnabled:true});f.analysis.structured_analysis=structuredCapability();f.analysis.lost=true;
  await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');
  const box=page.locator('[data-pm-follow-up-analyze]');await expect(box.locator(':scope > summary')).toHaveText(locale==='zh-TW'?'請 PM 評估':'Ask PM');await box.locator(':scope > summary').click();
  const form=box.locator('form'),choice=form.locator('[data-pm-structured-analysis] select');
  await choice.selectOption(BAT);await form.locator('textarea').fill('A fixed BAT Follow-up');await form.locator('button[type=submit]').click();
  await expect(form).toContainText('Original assessment reply lost');const original=structuredClone(f.analysis.writes[0]);
  f.analysis.structured_analysis=null;await page.reload();await openPmSection(page, 'issues' , 'ui_follow_up');await box.locator(':scope > summary').click();
  await expect(choice).toHaveValue(BAT);await expect(choice).toBeDisabled();await form.locator('button[type=submit]').click();
  await expect.poll(()=>f.analysis.writes.length).toBe(2);expect(f.analysis.writes[1]).toEqual(original);expect(original.body.params.adapter).toBe(BAT);expect(f.analysis.ops.size).toBe(1);
  }finally{await context.close();}
});

for(const native of [false,true])for(const locale of ['en','zh-TW'])for(const adapter of [BAT,NATIVE])test(`unbound Follow-up ${native?'Tauri':'Web'} ${locale} restores original ${adapter} target and key`,async({browser,baseURL},info)=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage();try{
  const f=fixture({analysisEnabled:true});f.followUp.state.rows.at(-1).document.thread_ref=null;f.analysis.structured_analysis=structuredCapability({native:true});f.analysis.lost=true;
  await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');
  const box=page.locator('[data-pm-follow-up-analyze]'),target=box.locator('select').first(),form=box.locator('form'),runtime=form.locator('[data-pm-structured-analysis] select');
  await expect(box.locator(':scope > summary')).toHaveText(locale==='zh-TW'?'請 PM 評估':'Ask PM');
  await box.locator(':scope > summary').click();await target.selectOption(followUpThreadId);await runtime.selectOption(adapter);
  await form.locator('textarea').fill('Retain my selected discussion and runtime.');await form.locator('button[type=submit]').click();
  await expect(form).toContainText('Original assessment reply lost');const original=structuredClone(f.analysis.writes[0]);
  await page.reload();await openPmSection(page, 'issues' , 'ui_follow_up');await box.locator(':scope > summary').click();await expect(target).toHaveValue(followUpThreadId);await expect(target).toBeDisabled();
  await expect(runtime).toHaveValue(adapter);await expect(runtime).toBeDisabled();await expect(form.locator('textarea')).toHaveValue(original.body.params.purpose);
  if(!native&&adapter===BAT)for(const width of [390,768,1440]){await page.setViewportSize({width,height:900});await box.scrollIntoViewIfNeeded();await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:info.outputPath(`target-recovery-${locale}-${width}.png`)});}
  await form.locator('button[type=submit]').click();await expect.poll(()=>f.analysis.writes.length).toBe(2);expect(f.analysis.writes[1]).toEqual(original);expect(f.analysis.ops.size).toBe(1);
  }finally{await context.close();}
});

test('pre-fix unbound native pending draft recovers exact target after a server capability upgrade',async({page})=>{
  const f=fixture({analysisEnabled:true});f.followUp.state.rows.at(-1).document.thread_ref=null;f.analysis.lost=true;await mountConversation(page,false,f.dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');
  const box=page.locator('[data-pm-follow-up-analyze]'),target=box.locator('select').first(),form=box.locator('form');
  await box.locator(':scope > summary').click();await target.selectOption(followUpThreadId);await form.locator('textarea').fill('Legacy original native request');await form.locator('button[type=submit]').click();
  await expect(form).toContainText('Original assessment reply lost');const original=structuredClone(f.analysis.writes[0]);expect(original.body.params.adapter).toBeUndefined();
  await page.evaluate(()=>{for(const key of Object.keys(localStorage))if(key.includes('.follow-up-target.'))localStorage.removeItem(key);});
  f.analysis.structured_analysis=structuredCapability();await page.reload();await openPmSection(page, 'issues' , 'ui_follow_up');await box.locator(':scope > summary').click();
  await expect(target).toHaveValue(followUpThreadId);await expect(target).toBeDisabled();await expect(form.locator('[data-pm-structured-analysis] select')).toHaveValue(NATIVE);
  await form.locator('button[type=submit]').click();await expect.poll(()=>f.analysis.writes.length).toBe(2);expect(f.analysis.writes[1]).toEqual(original);
});

test('pre-fix unique unsent draft restores selected discussion without creating an intent',async({page})=>{
  const f=fixture({analysisEnabled:true});f.followUp.state.rows.at(-1).document.thread_ref=null;f.analysis.structured_analysis=structuredCapability();await mountConversation(page,false,f.dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');
  const box=page.locator('[data-pm-follow-up-analyze]'),target=box.locator('select').first(),form=box.locator('form');
  await box.locator(':scope > summary').click();await target.selectOption(followUpThreadId);await form.locator('[data-pm-structured-analysis] select').selectOption(BAT);await form.locator('textarea').fill('An older unsent draft');
  await page.evaluate(()=>{for(const key of Object.keys(localStorage))if(key.includes('.follow-up-target.'))localStorage.removeItem(key);});
  await page.reload();await openPmSection(page, 'issues' , 'ui_follow_up');await box.locator(':scope > summary').click();await expect(target).toHaveValue(followUpThreadId);await expect(target).toBeEnabled();await expect(form.locator('textarea')).toHaveValue('An older unsent draft');
  await expect(form.locator('[data-pm-structured-analysis] select')).toHaveValue(BAT);expect(f.analysis.writes).toEqual([]);
});

test('withdrawn original target retains its unknown intent and never substitutes a catalog discussion',async({page})=>{
  const f=fixture({analysisEnabled:true});f.followUp.state.rows.at(-1).document.thread_ref=null;f.analysis.structured_analysis=structuredCapability();f.analysis.lost=true;
  let withdrawn=false;const other='pmt_'+'9'.repeat(32),reads:string[]=[];
  const dispatch=async(input:any)=>{reads.push(input.path);
    if(withdrawn&&input.path===`/pm/workspaces/${workspaceId}/threads?limit=20`)return {status:200,data:{workspace_id:workspaceId,threads:[{workspace_id:workspaceId,thread_id:other,title:'Another discussion',mode:'idea',version:1}],next_cursor:null}};
    return f.dispatch(input);
  };
  await mountConversation(page,false,dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');const box=page.locator('[data-pm-follow-up-analyze]'),form=box.locator('form');
  await box.locator(':scope > summary').click();await box.locator('select').first().selectOption(followUpThreadId);await form.locator('[data-pm-structured-analysis] select').selectOption(BAT);await form.locator('textarea').fill('Original private discussion');await form.locator('button[type=submit]').click();
  await expect(form).toContainText('Original assessment reply lost');const original=structuredClone(f.analysis.writes[0]);
  withdrawn=true;f.followUp.state.failSource='/threads/'+followUpThreadId;await page.reload();await openPmSection(page, 'issues' , 'ui_follow_up');await box.locator(':scope > summary').click();
  await expect(page.locator('[data-pm-follow-up]')).toContainText('Your token lacks this scope.');await expect(form).toHaveCount(0);
  expect(f.analysis.writes).toHaveLength(1);expect(reads.some(path=>path.endsWith('/threads/'+other))).toBe(false);
  expect(await page.evaluate(key=>Object.values(localStorage).some(value=>value.includes(key)),original.idempotency_key)).toBe(true);
  withdrawn=false;f.followUp.state.failSource=null;await page.reload();await openPmSection(page, 'issues' , 'ui_follow_up');await box.locator(':scope > summary').click();await expect(box.locator('select').first()).toHaveValue(followUpThreadId);await expect(box.locator('select').first()).toBeDisabled();
  await form.locator('button[type=submit]').click();await expect.poll(()=>f.analysis.writes.length).toBe(2);expect(f.analysis.writes[1]).toEqual(original);
});

test('multiple older original targets block a new analysis instead of selecting a newest target',async({page})=>{
  const f=fixture({analysisEnabled:true});f.followUp.state.rows.at(-1).document.thread_ref=null;f.analysis.lost=true;await mountConversation(page,false,f.dispatch);await page.goto(route);await openPmSection(page, 'issues' , 'ui_follow_up');
  const box=page.locator('[data-pm-follow-up-analyze]'),form=box.locator('form');await box.locator(':scope > summary').click();await box.locator('select').first().selectOption(followUpThreadId);await form.locator('textarea').fill('First original request');await form.locator('button[type=submit]').click();
  await expect(form).toContainText('Original assessment reply lost');await page.evaluate(({wid,other})=>{
    const key=Object.keys(localStorage).find(key=>key.includes('.follow-up.')&&key.includes('.pm.follow_up.analyze.'))!,value=JSON.parse(localStorage.getItem(key)!);
    value.intent.key='another-original-key';value.intent.request.target={workspace_id:wid,thread_id:other};
    const prefix=key.slice(0,key.indexOf('.pm.follow_up.analyze.')+'.pm.follow_up.analyze.'.length);localStorage.setItem(prefix+JSON.stringify(value.intent.request.target),JSON.stringify(value));
  },{wid:workspaceId,other:'pmt_'+'9'.repeat(32)});
  await page.reload();await openPmSection(page, 'issues' , 'ui_follow_up');await box.locator(':scope > summary').click();await expect(box).toContainText('More than one original discussion request');await expect(form).toHaveCount(0);expect(f.analysis.writes).toHaveLength(1);
});
