import {expectErrorDetails,expectNoNestedErrorDetails} from './api-error-assertions.ts';
import {openPmSection, openPmPanel} from './pm-navigation.ts';
import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture, pmRoute, workspaceId} from './pm-fixture.ts';
import {expectShellEmpty, shellShot} from './shell-empty-state.ts';
const pid='prj_'+'1'.repeat(20), wid='wi_'+'2'.repeat(20), rid='pmrev_'+'3'.repeat(32), eid='pme_'+'4'.repeat(32);
function fixture() {
  const base=pmFixture(), state={...base.state, accessVersion:0, bootstraps:0, catalogReads:0, catalogWait:null as Promise<void>|null, recoveryReads:0, recoveryWait:null as Promise<void>|null, associated:false, tracked:false, lost:false, planDenied:false, acceptedPlan:rid, planReads:[] as string[], boundPlan:null as string|null, boundRequirements:[] as string[], sources:[] as any[], writes:[] as any[], ops:new Map<string,any>(), capture:null as any,evidenceReadFails:false};
  const project={project_id:pid,name:'Reader experience',version:2};
  const binding=()=>({workspace_id:workspaceId,work_item_id:wid,binding_id:'pmwb_'+'5'.repeat(32),project_id:pid,plan_revision_id:state.boundPlan||null,requirement_ids:state.boundRequirements||[]});
  let workBinding:any=null;
  const summary={evidence_id:eid,workspace_id:workspaceId,source_kind:'work_claim',source_observed_at:1791720000,captured_at:1791720001,assessment:{state:'done'}};
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),path=url.pathname;
    if(path==='/events'){
      if(!url.searchParams.get('checkpoint')?.startsWith(`evidence-access-${state.accessVersion}-`))return {status:409,data:{error:{code:'EVENT_CURSOR_RESET',reason:'access_changed',preserve_drafts:true}}};
      const result=await base.dispatch(input);result.data.sync.checkpoint.token=`evidence-access-${state.accessVersion}-${result.data.next_cursor}`;return result;
    }
    if(path==='/capabilities'||path==='/bootstrap'){
      const result=await base.dispatch(input),caps=path==='/bootstrap'?result.data.capabilities:result.data;
      caps.features.pm_workspaces.work_evidence=true;
      caps.actions.push(...['pm.project.bind','pm.work.bind','pm.evidence.capture'].map(action=>({action,allowed:base.state.manage})));
      if(path==='/bootstrap'){state.bootstraps++;result.data.sync.checkpoint.token=`evidence-access-${state.accessVersion}-0`;}
      return result;
    }
    if(path==='/projects'){state.catalogReads++;await state.catalogWait;return {status:200,data:{projects:[project]}};}
    if(path==='/projects/'+pid)return {status:200,data:{project,work_items:[{work_item_id:wid,title:'Clear first action',version:3,children:[]}]}};
    const endpoint='/pm/workspaces/'+workspaceId;
    if(path===endpoint+'/projects')return {status:200,data:{workspace_id:workspaceId,bindings:state.associated?[{project_id:pid,sequence:1}]:[],next_cursor:null}};
    if(path===endpoint+'/work')return {status:200,data:{workspace_id:workspaceId,bindings:state.tracked?[workBinding||binding()]:[],next_cursor:null}};
    if(path.startsWith(endpoint+'/plans/')){
      state.planReads.push(path);
      const id=path.endsWith('/accepted')?state.acceptedPlan:path.split('/').at(-1)!;
      return state.planDenied?{status:403,data:{error:{code:'FORBIDDEN',message:'Plan withdrawn'}}}:{status:200,data:{head:{workspace_id:workspaceId},plan:{workspace_id:workspaceId,revision_id:id,
        document:{title:id===rid?'First useful action':'Previous supported release',requirements:[{id:'first-action',text:id===rid?'Visitors find the first useful action':'Members can use the supported release'}]}}}};
    }
    if(path===endpoint+'/work/'+wid+'/capture')return {status:200,data:{workspace_id:workspaceId,capture:state.capture}};
    if(path===endpoint+'/evidence/'+eid&&state.evidenceReadFails)return {status:503,data:{error:{code:'BAT_ERROR',message:'Fixture evidence read failed'}}};
    if(path===endpoint+'/evidence/'+eid)return {status:200,data:{evidence:{...summary,document:{facts:{state:'done',authority:'recorded_work_claim_and_approval',description:'<script>literal evidence</script>'}}}}};
    if(input.method==='GET'&&path.startsWith('/operations/'))return {status:200,data:{operation:[...state.ops.values()].find(op=>path.endsWith(op.operation_id))}};
    if(input.method==='POST'&&path==='/operations'&&['pm.project.bind','pm.work.bind','pm.evidence.capture'].includes(input.body.action)){
      if(state.ops.has(input.idempotency_key)){state.recoveryReads++;await state.recoveryWait;}
      state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
      if(!op){const {action,params}=input.body;
        if(action==='pm.project.bind')state.associated=true;
        if(action==='pm.work.bind'){state.tracked=true;workBinding={...binding(),plan_revision_id:params.plan_revision_id,requirement_ids:params.requirement_ids||[]};}
        if(action==='pm.evidence.capture')state.capture={workspace_id:workspaceId,work_item_id:wid,captured_at:1791720001,binding_id:binding().binding_id,
          coverage:{links_examined:2,unavailable_sources:1,unsupported_sources:0,truncated:false},evidence_ids:[eid],evidence:[summary],dod:'unassessed'};
        op={...input.body,actor:base.state.actor,idempotency_key:input.idempotency_key,operation_id:'op_'+String(state.ops.size+1).padStart(32,'0'),status:'succeeded',result:{execution_authorized:false}};
        state.ops.set(input.idempotency_key,op);
      }
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status:200,data:{operation:op}};
    }
    return base.dispatch(input);
  };
  return {state,base,dispatch};
}
const action=(page:any,name:string)=>page.locator(`[data-pm-action="${name}"]`);
const selectProject=async(page:any)=>{await page.getByLabel('Connector project',{exact:true}).selectOption(pid);};
const openWork=async(page:any)=>{await page.locator('[data-pm-work] > summary').click();};
for(const native of [false,true])for(const locale of ['en','zh-TW'])for(const width of [1440,390])
test(`an unreadable bound plan has one message and one Details ${native?'native':'web'} ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage();
  const f=fixture();f.state.associated=true;f.state.tracked=true;f.state.boundPlan=rid;
  f.state.boundRequirements=['membership.email_sign_in'];f.state.planDenied=true;
  await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);
  await openPmSection(page,'requirements','ui_evidence');
  await page.getByLabel(locale==='en'?'Connector project':'Connector 專案',{exact:true}).selectOption(pid);await openWork(page);
  const work=page.locator('[data-pm-work]'),facts=work.locator('.ui-disclosure');
  const summary=locale==='en'?'Could not read this plan.':'無法讀取這份計畫。';
  await expect(work).toContainText('Clear first action');
  await expect(work.locator('[data-pm-bound-plan] .ui-pill')).toHaveText(locale==='en'?'Unknown':'未知');
  await expect(facts).toHaveCount(1);
  await expect(facts).toHaveJSProperty('open',false);
  await expect(work.locator('[data-error-summary]')).toHaveCount(1);
  await expect(work.locator('[data-error-summary]')).toHaveText(summary);
  await expect(facts.locator('details')).toHaveCount(0);
  await expect.poll(()=>work.innerText()).not.toContain(rid);
  await expect.poll(()=>work.innerText()).not.toContain('membership.email_sign_in');
  const capture=async(state:string)=>{
    if(!process.env.BATC_FIX4_SHOTS)return;
    await mkdir(process.env.BATC_FIX4_SHOTS,{recursive:true});
    await page.screenshot({path:`${process.env.BATC_FIX4_SHOTS}/fix4-evidence-${state}-${native?'native':'web'}-${locale}-${width}.png`,animations:'disabled'});
  };
  await capture('collapsed');await facts.locator(':scope > summary').click();
  await expect(facts.locator('[data-error-summary]')).toBeVisible();
  await expect(facts.locator('[data-error-details] code')).toHaveText('FORBIDDEN');
  await expect(facts.locator('[data-error-details] p')).toHaveText('Plan withdrawn');
  await expect(facts).toContainText(rid);await expect(facts).toContainText('membership.email_sign_in');
  await capture('expanded');
  expect(f.state.writes).toHaveLength(0);expect(f.base.state.writes).toHaveLength(0);
  await context.close();
});
for(const locale of ['en','zh-TW'])for(const width of [1440,390])for(const state of ['no-projects','no-work','no-evidence','unavailable'])
test(`Settings ${state} has a short title and muted explanation ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({locale,baseURL,viewport:{width,height:900}}),page=await context.newPage();
  const f=fixture();f.state.associated=true;f.state.tracked=true;let withdrawn=false;
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path==='/projects'&&withdrawn)return {status:403,data:{error:{code:'FORBIDDEN',message:'Project catalog withdrawn'}}};
    const result=await f.dispatch(input);
    if(path==='/projects'&&state==='no-projects')result.data.projects=[];
    if(path==='/projects/'+pid&&state==='no-work')result.data.work_items=[];
    return result;
  };
  const copy=locale==='en'?{project:'Connector project',
    'no-projects':['No projects yet','No Connector projects are available.'],
    'no-work':['No work yet','This project has no active work items.'],
    'no-evidence':['No evidence yet','No evidence snapshot collected yet.'],
    unavailable:['Projects unavailable','Your token lacks this permission.']}
    :{project:'Connector 專案','no-projects':['尚無專案','目前沒有可用的 Connector 專案。'],
      'no-work':['尚無工作','此專案目前沒有工作項目。'],'no-evidence':['尚無證據','尚未整理證據快照。'],
      unavailable:['無法取得專案','你的權杖沒有這個權限。']};
  await mountConversation(page,false,dispatch);await page.goto(pmRoute);await openPmPanel(page,'ui_workspace_settings');
  if(state!=='no-projects')await page.getByLabel(copy.project,{exact:true}).selectOption(pid);
  if(state==='no-evidence'||state==='unavailable')await openWork(page);
  if(state==='unavailable'){
    await expect(page.locator('[data-pm-work]')).toContainText('Clear first action');
    withdrawn=true;f.base.state.cursor++;
    await expect(page.locator('[data-pm-work]')).toHaveCount(0);
    await expect(page.getByLabel(copy.project,{exact:true})).toHaveValue('');
    await expect(page.getByLabel(copy.project,{exact:true}).locator('option')).toHaveCount(1);
  }
  const [title,reason]=copy[state as keyof typeof copy] as string[];
  const empty=page.locator('[data-pm-evidence] .ui-empty').filter({has:page.getByRole('heading',{name:title,exact:true})});
  await expectShellEmpty(empty,title,reason);await empty.scrollIntoViewIfNeeded();
  await shellShot(page,'settings-'+state,locale,width);
  expect(f.state.writes).toHaveLength(0);expect(f.base.state.writes).toHaveLength(0);await context.close();
});
for(const locale of ['en','zh-TW'])for(const source of ['catalog','work'])test(`Settings missing ${source} data stays unavailable rather than empty ${locale}`,async({browser,baseURL})=>{
  const context=await browser.newContext({locale,baseURL}),page=await context.newPage(),f=fixture();f.state.associated=true;
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname,result=await f.dispatch(input);
    if(source==='catalog'&&path==='/projects')delete result.data.projects;
    if(source==='work'&&path==='/projects/'+pid)delete result.data.work_items;
    return result;
  };
  await mountConversation(page,false,dispatch);await page.goto(pmRoute);await openPmPanel(page,'ui_workspace_settings');
  if(source==='work')await page.getByLabel(locale==='en'?'Connector project':'Connector 專案',{exact:true}).selectOption(pid);
  const panel=page.locator('[data-pm-evidence]'),title=locale==='en'?'Projects unavailable':'無法取得專案';
  await expectShellEmpty(panel.locator('.ui-empty'),title,locale==='en'?'Could not read Connector data.':'無法讀取 Connector 專案紀錄。');
  await expect(panel.getByRole('heading',{name:locale==='en'?'No projects yet':'尚無專案',exact:true})).toHaveCount(0);
  await expect(panel.getByRole('heading',{name:locale==='en'?'No work yet':'尚無工作',exact:true})).toHaveCount(0);
  expect(f.state.writes).toHaveLength(0);await context.close();
});
for(const native of [false,true])test.describe(native?'native evidence':'web evidence',()=>{
  test('refresh visibly disables association until its fixed source reads finish, without losing the draft',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmSection(page, 'requirements' , 'ui_evidence');await selectProject(page);
    const bind=action(page,'pm.project.bind'),button=bind.getByRole('button',{name:'Associate project',exact:true});
    const rationale=bind.getByLabel('Why associate this work?',{exact:true});await rationale.fill('Keep this exact association through source revalidation');
    let release!:()=>void;f.state.catalogWait=new Promise<void>(resolve=>{release=resolve;});const before=f.state.catalogReads;
    try {
      await page.getByRole('button',{name:'Refresh',exact:true}).click();
      await expect.poll(()=>f.state.catalogReads).toBeGreaterThan(before);
      await expect(button).toBeDisabled();await expect(rationale).toBeDisabled();
      expect(f.state.writes).toHaveLength(0);
    }finally{f.state.catalogWait=null;release();}
    await expect(button).toBeEnabled();await expect(rationale).toHaveValue('Keep this exact association through source revalidation');
    await button.click();await expect(page.locator('[data-pm-work]')).toHaveCount(1);
    expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].body.params.rationale).toBe('Keep this exact association through source revalidation');
    expect(f.state.writes[0].body.preconditions.expected_project_version).toBe(2);
  });
  test('access revalidation preserves a still-readable project selection and unsent association',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmSection(page, 'requirements' , 'ui_evidence');await selectProject(page);
    const bind=action(page,'pm.project.bind'),rationale=bind.getByLabel('Why associate this work?',{exact:true});
    await rationale.fill('Keep the same project and unsent reason');const before=f.state.bootstraps;f.state.accessVersion++;
    await expect.poll(()=>f.state.bootstraps,{timeout:10000}).toBeGreaterThan(before);
    await expect(page.getByLabel('Connector project',{exact:true})).toHaveValue(pid);
    await expect(rationale).toBeVisible();await expect(rationale).toHaveValue('Keep the same project and unsent reason');
    expect(f.state.writes).toHaveLength(0);
  });
  test('explicit project and work association records selected plan requirements without dispatch',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmSection(page, 'requirements' , 'ui_evidence');await selectProject(page);
    await action(page,'pm.project.bind').getByLabel('Why associate this work?',{exact:true}).fill('Manage reader experience here');
    await action(page,'pm.project.bind').getByRole('button',{name:'Associate project',exact:true}).click();
    await openWork(page);await page.getByText('Track this work',{exact:true}).first().click();
    const bind=action(page,'pm.work.bind');await bind.getByLabel('Why associate this work?',{exact:true}).fill('Track first visit results');
    await bind.getByLabel('Plan version for this work',{exact:true}).selectOption(rid);
    await bind.getByLabel('Visitors find the first useful action',{exact:true}).check();
    await bind.getByRole('button',{name:'Track this work',exact:true}).click();await openWork(page);
    expect(f.state.writes.map(row=>row.body.action)).toEqual(['pm.project.bind','pm.work.bind']);
    expect(f.state.writes[1].body.params).toEqual({rationale:'Track first visit results',plan_revision_id:rid,requirement_ids:['first-action']});
    expect(f.state.writes[1].body.preconditions).toEqual({expected_work_version:3,expected_binding_id:null});
    const work=page.locator('[data-pm-work]');
    await expect(work).toContainText('This work references a fixed adopted plan revision.');
    await expect(work).toContainText('First useful action');
    await expect(work).toContainText('Visitors find the first useful action');
    await expect.poll(async()=>work.innerText()).not.toContain(rid);
    const facts=work.locator('.ui-disclosure');
    await expect(facts).toHaveJSProperty('open',false);
    await expect(facts).toContainText(rid);await expect(facts).toContainText('first-action');
  });
  test('an unreadable plan keeps the work row and leaves the revision id in Details',async({page})=>{
    const f=fixture();f.state.associated=true;f.state.tracked=true;f.state.boundPlan=rid;
    f.state.boundRequirements=['membership.email_sign_in'];f.state.planDenied=true;
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmSection(page,'requirements','ui_evidence');await selectProject(page);await openWork(page);
    const work=page.locator('[data-pm-work]');
    await expect(work).toContainText('Clear first action');
    await expect(work.getByText('Unknown',{exact:true}).first()).toBeVisible();
    await expect.poll(async()=>work.innerText()).not.toContain(rid);
    await expect.poll(async()=>work.innerText()).not.toContain('membership.email_sign_in');
    const facts=work.locator('.ui-disclosure');
    await expect(facts).toHaveCount(1);
    await expect(facts).toContainText('Could not read this plan.');
    await expect(facts).toContainText(rid);await expect(facts).toContainText('membership.email_sign_in');
    await expect(facts.locator('[data-error-summary]')).toHaveText('Could not read this plan.');
    await expect(facts.locator('[data-error-details]')).toContainText('FORBIDDEN');
    await expect(facts.locator('[data-error-details]')).toContainText('Plan withdrawn');
    expect(f.state.writes).toHaveLength(0);
  });
  test('an older bound plan supplies its own readable labels without borrowing the current plan',async({page})=>{
    const f=fixture();f.state.associated=true;f.state.tracked=true;f.state.boundPlan='pmrev_'+'6'.repeat(32);
    f.state.boundRequirements=['first-action'];f.base.state.role='reader';f.base.state.manage=false;
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmSection(page,'requirements','ui_evidence');await selectProject(page);await openWork(page);
    const labels=page.locator('[data-pm-bound-plan]');
    await expect(labels).toContainText('Previous supported release');await expect(labels).toContainText('Members can use the supported release');
    await expect(labels).not.toContainText('First useful action');
    expect(f.state.planReads).toContain('/pm/workspaces/'+workspaceId+'/plans/'+f.state.boundPlan);
    await expect(action(page,'pm.work.bind')).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
  });
  test('refresh withdraws the bound plan labels when that source becomes unreadable',async({page})=>{
    const f=fixture();f.state.associated=true;f.state.tracked=true;f.state.boundPlan=rid;f.state.boundRequirements=['first-action'];
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmSection(page,'requirements','ui_evidence');await selectProject(page);await openWork(page);
    const labels=page.locator('[data-pm-bound-plan]');await expect(labels).toContainText('First useful action');
    f.state.planDenied=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect(labels.getByText('Unknown',{exact:true})).toBeVisible();await expect(labels).not.toContainText('First useful action');
    await expect(labels).not.toContainText('Visitors find the first useful action');expect(f.state.writes).toHaveLength(0);
  });
  test('a late bound plan response cannot restore labels after withdrawal',async({page})=>{
    const f=fixture();f.state.associated=true;f.state.tracked=true;f.state.boundPlan='pmrev_'+'6'.repeat(32);f.state.boundRequirements=['first-action'];
    let release!:()=>void,waiting=false,replied=false;
    const held=new Promise<void>(resolve=>{release=resolve;});
    const dispatch=async(input:any)=>{
      const result=await f.dispatch(input),path=new URL(input.path,'http://fixture').pathname;
      if(path.endsWith('/plans/'+f.state.boundPlan)&&!waiting){waiting=true;await held;replied=true;}
      return result;
    };
    await mountConversation(page,native,dispatch);await page.goto(pmRoute);await openPmSection(page,'requirements','ui_evidence');await selectProject(page);await openWork(page);
    const labels=page.locator('[data-pm-bound-plan]');await expect.poll(()=>waiting).toBe(true);
    f.state.planDenied=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();
    const facts=page.locator('[data-pm-work] .ui-disclosure');
    await expect(facts).toHaveCount(1);
    await expect(facts).toContainText('Could not read this plan.');
    await expect(facts.locator('[data-error-details]')).toContainText('FORBIDDEN');
    await expect(facts.locator('[data-error-details]')).toContainText('Plan withdrawn');
    release();await expect.poll(()=>replied).toBe(true);
    await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
    await expect(labels.getByText('Unknown',{exact:true})).toBeVisible();
    await expect(labels).not.toContainText('Previous supported release');expect(f.state.writes).toHaveLength(0);
  });
  test('lost project association response can recover even after association becomes visible',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmSection(page, 'requirements' , 'ui_evidence');await selectProject(page);
    const bind=action(page,'pm.project.bind');await bind.getByLabel('Why associate this work?',{exact:true}).fill('Keep original association');f.state.lost=true;
    await bind.getByRole('button',{name:'Associate project',exact:true}).click();await expect(bind).toContainText('Reply unavailable');
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect(bind.getByRole('button',{name:'Check original request',exact:true})).toBeEnabled();
    await expect(bind.getByLabel('Why associate this work?',{exact:true})).toHaveValue('Keep original association');
    expect(f.state.writes).toHaveLength(1);
    await page.reload();await openPmSection(page, 'requirements', 'ui_evidence');await selectProject(page);
    const recover=bind.getByRole('button',{name:'Check original request',exact:true});
    let release!:()=>void;f.state.recoveryWait=new Promise<void>(resolve=>{release=resolve;});
    try {
      await recover.click();await expect.poll(()=>f.state.recoveryReads).toBe(1);
      // Clicking submits asynchronously; the original receipt may still be in transit.
      await expect(recover).toBeDisabled();expect(f.state.writes).toHaveLength(1);expect(f.state.ops.size).toBe(1);
    }finally{f.state.recoveryWait=null;release();}
    await expect.poll(()=>f.state.writes.length).toBe(2);
    expect(f.state.ops.size).toBe(1);expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
    expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
    await expect(bind).toBeHidden();
  });
  test('saved claim and coverage do not become verified delivery, and missing reply keeps original capture',async({page})=>{
    const f=fixture();f.state.associated=true;f.state.tracked=true;
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmSection(page, 'requirements' , 'ui_evidence');await selectProject(page);await openWork(page);
    const capture=action(page,'pm.evidence.capture');f.state.lost=true;
    await capture.getByRole('button',{name:'Collect saved evidence',exact:true}).click();await expect(capture).toContainText('Reply unavailable');
    await page.reload();await openPmSection(page, 'requirements' , 'ui_evidence');await selectProject(page);await openWork(page);
    await capture.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(page.locator('[data-pm-evidence]')).toContainText('Definition of Done has not been assessed.');
    await expect(page.locator('[data-pm-evidence]')).toContainText('1 unavailable or unsupported sources');
    await page.locator('[data-pm-fact] > summary').click();
    await page.locator('.pm-evidence-original > summary').click();
    await expect(page.locator('[data-pm-fact]')).toContainText('<script>literal evidence</script>');
    await expect(page.locator('[data-pm-evidence] script')).toHaveCount(0);
    expect(f.state.ops.size).toBe(1);expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
  });
  test('readers can inspect associations but cannot collect new evidence',async({page})=>{
    const f=fixture();f.state.associated=true;f.state.tracked=true;f.base.state.role='reader';f.base.state.manage=false;
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmSection(page, 'requirements' , 'ui_evidence');await selectProject(page);await openWork(page);
    await expect(action(page,'pm.work.bind')).toHaveCount(0);
    await expect(action(page,'pm.evidence.capture').getByRole('button')).toBeDisabled();
    expect(f.state.writes).toHaveLength(0);
  });
});
for(const locale of ['en','zh-TW'])for(const width of [390,1440])test(`evidence layout ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({locale,baseURL,viewport:{width,height:1000}}),page=await context.newPage();
  const f=fixture();f.state.associated=true;f.state.tracked=true;
  await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openPmSection(page, 'requirements' , 'ui_evidence');
  await page.locator('[data-pm-evidence] select').first().selectOption(pid);await openWork(page);
  await action(page,'pm.evidence.capture').getByRole('button',{name:locale==='en'?'Collect saved evidence':'整理已保存的證據',exact:true}).click();
  await page.locator('[data-pm-fact] > summary').click();
  await expect(page.locator('[data-pm-evidence] h2')).toHaveText(locale==='en'?'Work and delivery evidence':'工作與交付證據');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const out=process.env.BATC_PM_EVIDENCE_SCREENSHOTS;if(out){await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/evidence-${locale}-${width}.png`,fullPage:true});}
  await context.close();
});

for(const locale of ['en','zh-TW'])test(`evidence error reuses its open Details in ${locale}`,async({browser,baseURL})=>{
  const context=await browser.newContext({locale,baseURL}),page=await context.newPage();
  const f=fixture();f.state.associated=true;f.state.tracked=true;f.state.evidenceReadFails=true;
  await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openPmSection(page,'requirements','ui_evidence');
  await page.locator('[data-pm-evidence] select').first().selectOption(pid);await openWork(page);
  await action(page,'pm.evidence.capture').getByRole('button',{name:locale==='en'?'Collect saved evidence':'整理已保存的證據',exact:true}).click();
  const fact=page.locator('[data-pm-fact]');await fact.locator(':scope > summary').click();
  await expectErrorDetails(fact,'Fixture evidence read failed','BAT_ERROR');
  const error=fact.locator('[data-api-error]');await expectNoNestedErrorDetails(error);await expect(error.locator('details')).toHaveCount(0);
  await expect(fact.locator('summary')).toHaveCount(1);await expect(error.locator('[data-error-summary]')).toHaveCount(1);
  expect(f.state.writes.map(row=>row.body.action)).toEqual(['pm.evidence.capture']);
  await context.close();
});
