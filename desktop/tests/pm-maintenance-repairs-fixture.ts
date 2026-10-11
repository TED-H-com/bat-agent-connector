import {expect,type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {policyFixture,workspaceId} from './pm-maintenance-policy-fixture.ts';
import {unavailableBriefTransport} from './pm-source-transport-fixture.ts';
export {workspaceId};
export const START='pm.maintenance.repair',RECORD='pm.maintenance.repair.evidence.record';
export const panel=(page:Page)=>page.locator('[data-pm-maintenance-repairs]'),start=(page:Page)=>panel(page).locator(`[data-pm-action="${START}"]`),proof=(page:Page)=>panel(page).locator(`[data-pm-action="${RECORD}"]`),policy=(page:Page)=>page.locator('[data-pm-maintenance-policy]');
const id=(prefix:string,n:string)=>prefix+'_'+n.repeat(32),wid=workspaceId;
export function repairFixture(){
  const base=policyFixture();base.base.state.start=true;const release=base.release.state.baselines[0],work='wi_'+'4'.repeat(20),child=id('op','c');
  release.document.proof.maintenance_branch={status:'verified_at_observation',ref:'refs/heads/maint/1.4',head_sha:release.document.commit_sha};
  const handoff={target:{repository:'example/library',host:'mock-host',workspace_id:'ws-1'},params:{work_item_id:work,source_sha:release.document.commit_sha,source_ref:release.document.proof.maintenance_branch.ref,
    pm_package_ref:{workspace_id:wid,package_id:id('pmwp','d'),document_digest:'d'.repeat(64)},agent:'codex',prompt:'Implement the reviewed package',pm_code_basis:{mode:'implement_fixed_spec_from_selected_base',rationale:'Original fixed implementation'}},preconditions:{repository_id:7,expected_project_version:3}};
  const rule={key:'docs_fix',work_kind:'documentation',labels_all:['maintenance'],paths:['docs/'],release:{baseline_id:release.baseline_id,baseline_digest:release.document_digest,commit_sha:release.document.commit_sha,release_version:release.document.release_version},handoff,verification:{kind:'fixed_revision_verification',profile:'observed_runner_v1',command_digest:'a'.repeat(64)}};
  const p:any=base.documentPolicy();p.enabled=true;p.allowed_work_kinds=['documentation'];p.allowed_paths=['docs/'];p.delivery.maximum='pull_request';p.repair_rules=[rule];p.observation.maintenance_sources={issues:{labels_all:['maintenance']},dependencies:null};p.observation.required_ci_profile='github_required_status_checks_v1';base.addPolicy(p);
  const finding={finding_id:id('pmfn','f'),native:{kind:'issues',number:24,repository_id:7,provider_digest:'a'.repeat(64)},facts:{labels:['maintenance']},existing_pull_request:null,existing_work:[]};
  const state:any={deny:false,packageDeny:false,partial:false,badCursor:false,gate:null,lost:false,rows:[],quality:[],observations:[],verifier:false,verifications:[],config:{workspace_id:wid,execution_operation_id:child,profile:'observed_runner_v1',available:true,reason:null,command_digest:'a'.repeat(64),handoff_contract_digest:'c'.repeat(64),candidate_commit:'b'.repeat(40),tree_hash:'d'.repeat(40),candidate_reason:null},reads:[],writes:[],ops:new Map(),handoff:{title:'Original docs package',handoff},
    findings:{workspace_id:wid,automatic_repair_authorized:false,visibility_filtered:false,observation_ref:{observation_id:id('pmgh','e'),document_digest:'e'.repeat(64)},sources:{issues:{status:'complete',findings:[finding]},dependabot_pull_requests:{status:'disabled',findings:[]},dependabot_alerts:{status:'disabled',findings:[]}}}};
  function save(){const n=String(state.rows.length+1),row={workspace_id:wid,sequence:state.rows.length+1,repair_id:id('pmrp',n),operation_id:id('op',n),version:0,policy_id:base.state.policies.at(-1)!.policy_id,work_item_id:work,
    context:{rule:structuredClone(rule),finding:structuredClone(finding),finding_ref:{...state.findings.observation_ref,finding_id:finding.finding_id}},created_at:Date.now()/1000,deadline_at:Date.now()/1000+1800,finished_at:null,operation_status:'running',capacity_held:true,repair_fixed:false,release_status:'not_inferred',cost_coverage:'model_provider_unmeasured',retries_used:0,
    child:{operation_id:child,action:'repository.continue',status:'succeeded',result:{candidate_commit:'b'.repeat(40)},error_code:null},publication_child:null,evidence:[],progress:{dispatch:'succeeded',verification:null,quality:[],publication:'not_admitted',acceptance:[],delivery_receipts:[],blocked_reason:'PM_REPAIR_VERIFICATION_REQUIRED',new_investment_blocked_reason:null}};state.rows.push(row);return row;}
  function evidence(){const row=save();state.quality.push({workspace_id:wid,review_id:id('pmqr','9'),qualified:true,context:{execution_operation_id:child,native_result:{candidate_commit:'b'.repeat(40)}},assessment:{operation_id:id('op','8'),version:2,document_digest:'8'.repeat(64)}});return row;}
  const dispatch=async(input:any)=>{const url=new URL(input.path,'http://fixture'),pathname=url.pathname;
    if(input.method==='GET')state.reads.push(input.path);
    if(pathname==='/capabilities'||pathname==='/bootstrap'){const response=await base.dispatch(input),caps=pathname==='/bootstrap'?response.data.capabilities:response.data;
      caps.features.pm_workspaces.maintenance_repairs={version:1,actions:[START,RECORD]};caps.features.pm_github_observation.maintenance_findings={version:1};caps.features.pm_github_observation.required_ci={version:1,profiles:['github_required_status_checks_v1']};
      if(state.verifier){caps.features.pm_workspaces.execution_verification={version:1};caps.scopes.push('operate');caps.actions.push({action:'pm.execution.verify',allowed:true});}
      caps.actions.push({action:START,allowed:true},{action:RECORD,allowed:true});return response;}
    if(/\/release-baselines\/pmrb_/.test(pathname)){const row=base.release.state.baselines.find((r:any)=>pathname.endsWith(r.baseline_id));return {status:200,data:{workspace_id:wid,baseline:structuredClone(row)}};}
    if(pathname.includes('/work-packages/'))return state.packageDeny?{status:404,data:{error:{code:'NOT_FOUND',message:'Original rule package withdrawn'}}}:{status:200,data:{workspace_id:wid,package:{workspace_id:wid,package_id:handoff.params.pm_package_ref.package_id,document_digest:handoff.params.pm_package_ref.document_digest}}};
    if(pathname.endsWith('/maintenance-findings/current'))return state.deny?{status:404,data:{error:{code:'NOT_FOUND',message:'Original source withdrawn'}}}:{status:200,data:structuredClone(state.findings)};
    if(pathname.startsWith(`/pm/workspaces/${wid}/maintenance-repairs`)){
      if(state.gate)await state.gate;if(state.deny)return {status:404,data:{error:{code:'NOT_FOUND',message:'Original repair withdrawn'}}};
      if(pathname.endsWith('/maintenance-repairs')){const after=Number(url.searchParams.get('after'));return {status:200,data:{workspace_id:wid,repairs:state.partial&&after===0?[]:structuredClone(state.rows),next_after:state.badCursor?after:state.partial&&after===0?10:null,source_visibility_filtered:state.partial}};}
      const row=pathname.endsWith('/current')?state.rows.at(-1):state.rows.find((r:any)=>pathname.endsWith(r.repair_id));return row?{status:200,data:{workspace_id:wid,repair:structuredClone(row)}}:{status:404,data:{error:{code:'NOT_FOUND',message:'Original repair missing'}}};}
    if(pathname.endsWith('/verification-config'))return {status:200,data:structuredClone(state.config)};
    if(pathname.includes('/execution-verifications/')){const operation=state.verifications.find((op:any)=>pathname.endsWith(op.operation_id));return {status:200,data:{workspace_id:wid,operation_status:'succeeded',verification:operation?.result||null}};}
    if(pathname==='/operations'&&input.method==='GET')return {status:200,data:{operations:structuredClone(state.verifications),next_before:null}};
    if(pathname==='/operations/'+child&&input.method==='GET')return {status:200,data:{operation:{operation_id:child,actor:base.base.state.actor,action:'repository.continue',status:'succeeded'}}};
    if(pathname.endsWith('/quality'))return {status:200,data:{workspace_id:wid,reviews:structuredClone(state.quality),next_cursor:null,visibility_filtered:false}};
    if(pathname.endsWith('/github-observations'))return {status:200,data:{workspace_id:wid,observations:structuredClone(state.observations),next_cursor:null,visibility_filtered:false}};
    if(pathname.startsWith('/operations/')&&input.method==='GET'){const operation=[...state.ops.values()].find((op:any)=>pathname.endsWith(op.operation_id));if(operation)return {status:200,data:{operation}};}
    if(pathname==='/operations'&&input.method==='POST'&&[START,RECORD,'pm.execution.verify'].includes(input.body.action)){
      state.writes.push(structuredClone(input));let operation=state.ops.get(input.idempotency_key);
      if(!operation&&input.body.action==='pm.execution.verify'){
        const oid=id('op','6'),result={workspace_id:wid,operation_id:oid,execution_operation_id:child,profile:'observed_runner_v1',status:'passed',exit_code:0,receipt_digest:'6'.repeat(64),created_at:Date.now()/1000,
          reference:{verification_operation_id:oid,candidate_commit:input.body.params.candidate_commit,tree_hash:input.body.params.tree_hash}};
        operation={...input.body,operation_id:oid,actor:base.base.state.actor,idempotency_key:input.idempotency_key,status:'succeeded',result};state.ops.set(input.idempotency_key,operation);state.verifications.push(operation);
      }
      if(!operation){let row;if(input.body.action===START)row=save();else{row=state.rows.find((r:any)=>r.repair_id===input.body.target.repair_id);row.version++;row.evidence.push({selection:input.body.params.evidence,evidence:input.body.params.evidence,operation_id:id('op','7')});}
        operation={...input.body,operation_id:id('op',String(5+state.ops.size)),actor:base.base.state.actor,idempotency_key:input.idempotency_key,status:'succeeded',result:{repair_id:row.repair_id,version:row.version}};state.ops.set(input.idempotency_key,operation);}
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}return {status:200,data:{operation}};}
    return base.dispatch(input);
  };return {base,state,rule,handoff,save,evidence,dispatch};
}
export async function mountRepairs(page:Page,f:ReturnType<typeof repairFixture>,locale='en'){
  await page.exposeFunction('repairFixtureRequest',f.dispatch);await page.exposeFunction('repairHandoff',()=>f.state.handoff);
  await page.route('**/__repair_src/*.js',async route=>{const file=path.basename(new URL(route.request().url()).pathname);let source=await readFile(new URL('../src/'+file,import.meta.url),'utf8');
    if(file==='pm-workspace.js')source=source.replace('function durableForm(', 'export function durableForm(');await route.fulfill({contentType:'text/javascript',body:source});});
  await unavailableBriefTransport(page,'__repair_src');
  await page.route('**/__repair_style.css',async route=>route.fulfill({contentType:'text/css',body:await readFile(new URL('../src/pm-maintenance-repairs.css',import.meta.url),'utf8')}));
  await page.route('**/__repair_harness',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/dashboard/app.css"><link rel="stylesheet" href="/__repair_style.css"><main class="pm-main" id="mount"></main>'}));
  await page.goto('/__repair_harness');await bootRepairs(page,locale);
}
export async function bootRepairs(page:Page,locale='en'){
  await page.evaluate(async({locale,workspaceId})=>{
    const {workspaceMaintenanceRepairs}=await import('/__repair_src/pm-maintenance-repairs.js'),{workspaceMaintenancePolicy}=await import('/__repair_src/pm-maintenance-policy.js'),{durableForm}=await import('/__repair_src/pm-workspace.js');
    const modules=await Promise.all(['pm-strings','pm-maintenance-repairs-strings','pm-maintenance-policy-strings','pm-maintenance-findings-strings','pm-quality-reviews-strings'].map(name=>import('/__repair_src/'+name+'.js')));
    const strings=Object.assign({},...modules.map(module=>(Object.values(module)[0] as any)[locale])),t=(key:string)=>strings[key]||key;
    const h=(tag:string,attrs:any={},...children:any[])=>{const el=document.createElement(tag);for(const[key,value]of Object.entries(attrs||{})){if(value===null||value===undefined||value===false)continue;
      if(key.startsWith('on'))el.addEventListener(key.slice(2),value as any);else el.setAttribute(key,value===true?'':String(value));}for(const child of children.flat()){if(child!==null&&child!==undefined&&child!==false)el.append(child instanceof Node?child:document.createTextNode(String(child)));}return el;};
    const win=window as any,api=async(method:string,path:string,body:any,key:any)=>{const result=await win.repairFixtureRequest({method,path,body,idempotency_key:key});
      if(result.status>=400)throw Object.assign(Error(result.data.error.message),{code:result.data.error.code,status:result.status,admissionRefused:result.data.error.admission_refused});return result.data;};
    const errorBox=(error:any)=>h('p',{class:'error'},error.message),input=(label:string,{multiline=false,max=4000,required=true,value=''}:any={})=>{
      const field=h(multiline?'textarea':'input',{'aria-label':t(label),maxlength:max,required}) as HTMLInputElement;field.value=value;return field;},labelled=(key:string,field:any)=>h('label',{},t(key),field),alive=()=>{};
    let caps=await api('GET','/capabilities'),workspace=(await api('GET','/pm/workspaces/'+workspaceId)).workspace,reviewed=await win.repairHandoff();
    const form=(options:any)=>durableForm({h,t,api,caps:()=>caps,guard:alive,namespace:'repair-ui.'+caps.actor,errorBox,...options});
    const args={h,t,api,alive,workspaceId,workspace:()=>workspace,caps:()=>caps,permitted:()=>['owner','contributor'].includes(workspace.membership?.role),form,input,labelled,errorBox,namespace:'repair-ui.'+caps.actor};
    const repairs=workspaceMaintenanceRepairs(args),policy=workspaceMaintenancePolicy({...args,reviewedHandoff:()=>reviewed});document.getElementById('mount')!.append(policy.element,repairs.element);
    win.repairRefresh=async()=>{caps=await api('GET','/capabilities');workspace=(await api('GET','/pm/workspaces/'+workspaceId)).workspace;await repairs.refresh();};win.policyRefresh=()=>policy.refresh();
    win.repairWithdraw=()=>{repairs.withdraw();policy.withdraw();};win.handoffRefresh=async()=>{reviewed=await win.repairHandoff();policy.update();};
    await policy.refresh();await repairs.refresh();
  },{locale,workspaceId});await expect(panel(page)).toBeVisible();
}
