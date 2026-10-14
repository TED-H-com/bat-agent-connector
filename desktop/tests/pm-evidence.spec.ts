import {openPmSection} from './pm-navigation.ts';
import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture, pmRoute, workspaceId} from './pm-fixture.ts';
const pid='prj_'+'1'.repeat(20), wid='wi_'+'2'.repeat(20), rid='pmrev_'+'3'.repeat(32), eid='pme_'+'4'.repeat(32);
function fixture() {
  const base=pmFixture(), state={...base.state, accessVersion:0, bootstraps:0, catalogReads:0, catalogWait:null as Promise<void>|null, associated:false, tracked:false, lost:false, sources:[] as any[], writes:[] as any[], ops:new Map<string,any>(), capture:null as any};
  const project={project_id:pid,name:'Reader experience',version:2};
  const binding=()=>({workspace_id:workspaceId,work_item_id:wid,binding_id:'pmwb_'+'5'.repeat(32),project_id:pid,plan_revision_id:null,requirement_ids:[]});
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
    if(path===endpoint+'/plans/accepted')return {status:200,data:{head:{workspace_id:workspaceId},plan:{revision_id:rid,document:{title:'First useful action',requirements:[{id:'first-action',text:'Visitors find the first useful action'}]}}}};
    if(path===endpoint+'/work/'+wid+'/capture')return {status:200,data:{workspace_id:workspaceId,capture:state.capture}};
    if(path===endpoint+'/evidence/'+eid)return {status:200,data:{evidence:{...summary,document:{facts:{state:'done',authority:'recorded_work_claim_and_approval',description:'<script>literal evidence</script>'}}}}};
    if(input.method==='GET'&&path.startsWith('/operations/'))return {status:200,data:{operation:[...state.ops.values()].find(op=>path.endsWith(op.operation_id))}};
    if(input.method==='POST'&&path==='/operations'&&['pm.project.bind','pm.work.bind','pm.evidence.capture'].includes(input.body.action)){
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
    await expect(page.locator('[data-pm-work]')).toContainText('This work references a fixed adopted plan revision.');
  });
  test('lost project association response can recover even after association becomes visible',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmSection(page, 'requirements' , 'ui_evidence');await selectProject(page);
    const bind=action(page,'pm.project.bind');await bind.getByLabel('Why associate this work?',{exact:true}).fill('Keep original association');f.state.lost=true;
    await bind.getByRole('button',{name:'Associate project',exact:true}).click();await expect(bind).toContainText('Reply unavailable');
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect(bind.getByRole('button',{name:'Check original request',exact:true})).toBeEnabled();
    await expect(bind.getByLabel('Why associate this work?',{exact:true})).toHaveValue('Keep original association');
    expect(f.state.writes).toHaveLength(1);
    await page.reload();await openPmSection(page, 'requirements' , 'ui_evidence');await selectProject(page);await bind.getByRole('button',{name:'Check original request',exact:true}).click();
    expect(f.state.ops.size).toBe(1);expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
    expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
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
