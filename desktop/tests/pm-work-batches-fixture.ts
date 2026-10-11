import {expect,type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {handoffFixture,workspaceId,packageId} from './pm-package-handoff-fixture.ts';
import {unavailableBriefTransport} from './pm-source-transport-fixture.ts';
export {workspaceId,packageId};
export const START='pm.work_batch.start',RECORD='pm.work_batch.evidence.record';
export const panel=(page:Page)=>page.locator('[data-pm-work-batches]'),start=(page:Page)=>panel(page).locator(`[data-pm-action="${START}"]`),proof=(page:Page)=>panel(page).locator(`[data-pm-action="${RECORD}"]`),handoff=(page:Page)=>page.locator('[data-pm-package-handoff]');
const condition={kind:'fixed_revision_verification',profile:'github_reported_checks_v1'};
export function batchFixture(){
  const base=handoffFixture(),state:any={version:2,revision:0,enabled:true,deny:false,historyDeny:false,partial:false,badCursor:false,gate:null,rows:[],observations:[],deliveries:[],reads:[],writes:[],ops:new Map(),lost:false,deliveryPartial:false,observationPartial:false};
  const save=(params:any={entries:[],limits:{max_starts:2,max_parallel:1,max_retries:0,deadline_at:Date.now()/1000+3600}})=>{
    const suffix=String(state.rows.length+1).padStart(32,'0'),batch_id='pmbt_'+suffix;
    const row={sequence:state.rows.length+1,batch_id,operation_id:'op_'+suffix,workspace_id:workspaceId,created_at:Date.now()/1000,version:0,operation_status:'succeeded',limits:params.limits,
      entries:params.entries.map((entry:any)=>({key:entry.key,package_ref:entry.handoff.params.pm_package_ref,work_item_id:entry.handoff.params.work_item_id,depends_on:entry.depends_on,result_condition:entry.result_condition||condition,
        source_sha:entry.handoff.params.source_sha,operation_id:null,operation_status:null,state:'selected',reason:null,verification_evidence:[]})),result:{batch_id},requirement_acceptance:'not_evaluated',model_provider_cost:'unmeasured'};state.rows.push(row);return row;
  };
  const entry=(key='first',row=base.row)=>({key,handoff:{target:row.document.code_source,params:{source_sha:row.document.code_source.source_sha,work_item_id:row.work_item_id,pm_package_ref:{workspace_id:workspaceId,package_id:row.package_id,document_digest:row.document_digest}},preconditions:{}},depends_on:[],result_condition:condition});
  const evidence=()=>{
    const row=save({entries:[entry()],limits:{max_starts:2,max_parallel:1,max_retries:0,deadline_at:Date.now()/1000+3600}}),child='op_'+'c'.repeat(32),integration='op_'+'d'.repeat(32);row.entries[0].operation_id=child;row.entries[0].state='dispatched_awaiting_result';
    state.observations.push({workspace_id:workspaceId,observation_id:'pmgh_'+'e'.repeat(32),document_digest:'f'.repeat(64),created_at:Date.now()/1000,document:{}});
    state.deliveries.push({operation_id:integration,action:'integration.apply',target:{repository:'example/library'},integration:{sources:[{seq:1,source_kind:'execution',source_id:child,pinned_sha:'a'.repeat(40),status:'delivered',effective_status:'delivered',delivered_sha:'b'.repeat(40)}]}});return row;
  };
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),pathname=url.pathname;
    if(input.method==='GET')state.reads.push(input.path);
    if(pathname==='/capabilities'||pathname==='/bootstrap'){const response=await base.dispatch(input),caps=pathname==='/bootstrap'?response.data.capabilities:response.data;caps.features.pm_workspaces.work_batches=state.enabled?{version:1,actions:[START,RECORD],profiles:[condition.profile,'observed_runner_v1'],max_entries:16}:null;
      caps.actions.push({action:START,allowed:true},{action:RECORD,allowed:true});return response;}
    if(pathname===`/pm/workspaces/${workspaceId}`){const response=await base.dispatch(input);response.data.workspace.version=state.version;return response;}
    if(pathname===`/pm/workspaces/${workspaceId}/lifecycle`){const response=await base.dispatch(input);response.data.lifecycle.revision=state.revision;if(base.fixture.state.stage!=='development')response.data.blocked_actions.push(START);return response;}
    if(pathname.startsWith(`/pm/workspaces/${workspaceId}/work-batches`)){
      if(state.gate)await state.gate;if(state.historyDeny)return {status:404,data:{error:{code:'NOT_FOUND',message:'Original batch unavailable'}}};
      if(pathname.endsWith('/work-batches')){const after=Number(url.searchParams.get('after'));return {status:200,data:{workspace_id:workspaceId,batches:state.partial&&after===0?[]:structuredClone(state.rows),visibility_filtered:state.partial,next_cursor:state.badCursor?after:state.partial&&after===0?10:null}};}
      const row=state.rows.find((row:any)=>pathname.endsWith(row.batch_id));return row?{status:200,data:{workspace_id:workspaceId,batch:structuredClone(row)}}:{status:404,data:{error:{code:'NOT_FOUND',message:'Original batch unavailable'}}};
    }
    if(pathname.endsWith('/github-observations')){const partial=state.observationPartial&&Number(url.searchParams.get('after'))===0;return state.deny?{status:404,data:{error:{code:'NOT_FOUND',message:'Observation withdrawn'}}}:{status:200,data:{workspace_id:workspaceId,observations:partial?[]:structuredClone(state.observations),visibility_filtered:partial,next_cursor:partial?3:null}};}
    if(pathname.endsWith('/deliveries')){const partial=state.deliveryPartial&&Number(url.searchParams.get('after'))===0;return {status:200,data:{workspace_id:workspaceId,work_item_id:pathname.split('/').at(-2),deliveries:partial?[]:structuredClone(state.deliveries),next_after:partial?4:null,source_visibility_filtered:partial}};}
    if(pathname.startsWith('/operations/')&&input.method==='GET'){const operation=[...state.ops.values()].find((op:any)=>pathname.endsWith(op.operation_id));if(operation)return {status:200,data:{operation}};}
    if(pathname==='/operations'&&input.method==='POST'&&[START,RECORD].includes(input.body.action)){
      state.writes.push(structuredClone(input));let operation=state.ops.get(input.idempotency_key);
      if(!operation){let result;if(input.body.action===START){const row=save(input.body.params);result={batch_id:row.batch_id};}else{const row=state.rows.find((row:any)=>row.batch_id===input.body.target.batch_id);row.version++;
        for(const item of input.body.params.evidence)row.entries.find((entry:any)=>entry.key===item.entry_key).verification_evidence.push({...item,status:'passed'});result={batch_id:row.batch_id,version:row.version};}
        operation={...input.body,operation_id:'op_'+String(100+state.ops.size).padStart(32,'0'),actor:base.fixture.base.state.actor,idempotency_key:input.idempotency_key,status:'succeeded',result};state.ops.set(input.idempotency_key,operation);}
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}return {status:200,data:{operation}};
    }
    return base.dispatch(input);
  };return {base,state,save,entry,evidence,dispatch};
}
export async function mountBatch(page:Page,f:ReturnType<typeof batchFixture>,locale='en'){
  await page.exposeFunction('batchFixtureRequest',f.dispatch);
  await page.route('**/__batch_src/*.js',async route=>{const file=path.basename(new URL(route.request().url()).pathname);let source=await readFile(new URL('../src/'+file,import.meta.url),'utf8');
    if(file==='pm-workspace.js')source=source.replace('function durableForm(', 'export function durableForm(');await route.fulfill({contentType:'text/javascript',body:source});});
  await unavailableBriefTransport(page,'__batch_src');
  await page.route('**/__batch_style.css',async route=>route.fulfill({contentType:'text/css',body:await readFile(new URL('../src/pm-work-batches.css',import.meta.url),'utf8')}));
  await page.route('**/__batch_harness',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/dashboard/app.css"><link rel="stylesheet" href="/__batch_style.css"><main class="pm-main" id="mount"></main>'}));
  await page.goto('/__batch_harness');await bootBatch(page,locale);
}
export async function bootBatch(page:Page,locale='en'){
  await page.evaluate(async({locale,workspaceId,packageId})=>{
    const {workspaceWorkBatches}=await import('/__batch_src/pm-work-batches.js'),{workspacePackageHandoff}=await import('/__batch_src/pm-package-handoff.js'),{durableForm}=await import('/__batch_src/pm-workspace.js');
    const {pmWorkBatchesStrings}=await import('/__batch_src/pm-work-batches-strings.js'),{pmPackageHandoffStrings}=await import('/__batch_src/pm-package-handoff-strings.js'),{pmStrings}=await import('/__batch_src/pm-strings.js');
    const strings={...pmStrings[locale],...pmPackageHandoffStrings[locale],...pmWorkBatchesStrings[locale]},t=(key:string)=>strings[key]||key;
    const h=(tag:string,attrs:any={},...children:any[])=>{const el=document.createElement(tag);for(const[key,value]of Object.entries(attrs||{})){if(value===null||value===undefined||value===false)continue;
      if(key.startsWith('on'))el.addEventListener(key.slice(2),value as any);else el.setAttribute(key,value===true?'':String(value));}for(const child of children.flat()){if(child!==null&&child!==undefined&&child!==false)el.append(child instanceof Node?child:document.createTextNode(String(child)));}return el;};
    const win=window as any,api=async(method:string,path:string,body:any,key:any)=>{const result=await win.batchFixtureRequest({method,path,body,idempotency_key:key});
      if(result.status>=400)throw Object.assign(Error(result.data.error.message),{code:result.data.error.code,status:result.status,admissionRefused:result.data.error.admission_refused});return result.data;};
    const errorBox=(error:any)=>h('p',{class:'error'},error.message),input=(label:string,{multiline=false,max=4000,required=true,value=''}:any={})=>{
      const field=h(multiline?'textarea':'input',{'aria-label':t(label),maxlength:max,required}) as HTMLInputElement;field.value=value;return field;},labelled=(key:string,field:any)=>h('label',{},t(key),field),alive=()=>{};
    let caps=await api('GET','/capabilities'),workspace=(await api('GET','/pm/workspaces/'+workspaceId)).workspace;
    const form=(options:any)=>durableForm({h,t,api,caps:()=>caps,guard:alive,namespace:'batch-ui.'+caps.actor,errorBox,...options});
    const args={h,t,api,alive,workspaceId,workspace:()=>workspace,caps:()=>caps,permitted:()=>['owner','contributor'].includes(workspace.membership?.role),form,input,labelled,errorBox,namespace:'batch-ui.'+caps.actor};
    let batches:any,handoff:any;handoff=workspacePackageHandoff({...args,onReviewChange:()=>batches?.update()});batches=workspaceWorkBatches({...args,reviewedHandoff:()=>handoff.batchSelection()});
    document.getElementById('mount')!.append(handoff.element,batches.element);
    win.batchSelect=async(id=packageId)=>{const row=(await api('GET',`/pm/workspaces/${workspaceId}/work-packages/${id}`)).package;handoff.setPackage(row);};
    win.batchRefresh=async()=>{caps=await api('GET','/capabilities');workspace=(await api('GET','/pm/workspaces/'+workspaceId)).workspace;await handoff.refresh();await batches.refresh();};win.batchOnlyRefresh=()=>batches.refresh();
    win.batchWithdraw=()=>{handoff.withdraw();batches.withdraw();};win.batchSelection=()=>handoff.batchSelection();
    await win.batchSelect();await batches.refresh();(document.querySelector('[data-pm-package-handoff]>details') as HTMLDetailsElement).open=true;
  },{locale,workspaceId,packageId});await expect(panel(page)).toBeVisible();
}
