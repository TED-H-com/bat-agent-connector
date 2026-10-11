import {expect,type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {handoffFixture,workspaceId,packageId} from './pm-package-handoff-fixture.ts';
import {unavailableBriefTransport} from './pm-source-transport-fixture.ts';
export {workspaceId,packageId};
export const panel=(page:Page)=>page.locator('[data-pm-spec-materialization]'),handoff=(page:Page)=>page.locator('[data-pm-package-handoff]');
export const materialize=(page:Page)=>panel(page).locator('[data-pm-action="pm.spec.materialize"]');
export const startForm=(page:Page)=>handoff(page).locator('[data-pm-action="repository.continue"]');
export function specFixture(){
  const base=handoffFixture(),state:any={enabled:true,rows:[],writes:[],reads:[],ops:new Map(),lost:false,deny:false,partial:false,badCursor:false,filtered:false,gate:null,wrong:false};
  function save(pkg=base.row){
    const n=state.rows.length+1,suffix=n.toString(16).padStart(32,'0'),mid='pmsp_'+suffix,oid='op_'+suffix,hash='a'.repeat(64),change='pm-'+pkg.package_id.slice(5);
    const document={schema:'batc.pm.spec-materialization/v1',workspace_id:workspaceId,change_id:change,root_package_id:pkg.package_id,
      package_ref:{workspace_id:workspaceId,package_id:pkg.package_id,document_digest:pkg.document_digest},package:structuredClone(pkg.document),
      tasks:pkg.document.tasks.map((task:any,i:number)=>({task_id:'pmt_'+String(i+1).padStart(32,'0'),key:task.key,state:'active',history:[{package_id:pkg.package_id,document_digest:pkg.document_digest,state:'active',task}]})),
      source_chain:[{package_id:pkg.package_id,document_digest:pkg.document_digest,operation_id:pkg.operation_id}],verification:'unassessed',delivery:'not_observed',layout:'connector_owned_revision_adapter',openspec_cli_compatibility:'not_claimed'};
    const files=Object.fromEntries(['package.json','proposal.md','requirements.md','design.md','tasks.md','manifest.json'].map(name=>[`openspec/changes/${change}/revisions/${pkg.package_id}/${name}`,'e'.repeat(64)]));
    files[`docs/pm/specs/${change}/revisions/${pkg.package_id}.json`]='f'.repeat(64);
    const row={sequence:n,workspace_id:workspaceId,materialization_id:mid,package_id:pkg.package_id,document_digest:hash,document,
      artifact_ref:{artifact_id:'art_'+suffix,revision:1,digest:hash,safe_name:'fixed-spec.json'},
      git_receipt:{status:'verified',workspace_id:workspaceId,revision_id:mid,package_id:pkg.package_id,operation_id:oid,artifact_kind:'pm-spec',change_id:change,
        commit_sha:'9'.repeat(40),tree_sha:'8'.repeat(40),document_digest:'7'.repeat(64),file_digests:files,binding_id:'6'.repeat(64)},operation_id:oid,actor:base.fixture.base.state.actor,created_at:1791720000};
    state.rows.push(row);return row;
  }
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),pathname=url.pathname;
    if(pathname==='/spec-ui-config')return {status:200,data:{stage:base.fixture.state.stage}};
    if(pathname==='/capabilities'){
      const reply=await base.dispatch(input);if(state.enabled)reply.data.features.pm_workspaces.spec_materialization={version:1,action:'pm.spec.materialize',immutable:true,owned_git_chain:true};
      reply.data.features.pm_workspaces.package_handoff.source_materialization='bounded_full_prompt_or_fixed_spec_artifact';reply.data.actions.push({action:'pm.spec.materialize',allowed:base.fixture.base.state.manage});return reply;
    }
    if(pathname.startsWith(`/pm/workspaces/${workspaceId}/spec-materializations`)){
      state.reads.push(input.path);if(state.gate)await state.gate;
      if(state.deny)return {status:404,data:{error:{code:'NOT_FOUND',message:'Original specification source unavailable'}}};
      const root=pathname.endsWith('/spec-materializations'),after=Number(url.searchParams.get('after')),pkg=url.searchParams.get('package_id');
      if(root)return {status:200,data:{version:1,workspace_id:workspaceId,materializations:state.partial&&after===0?[]:state.rows.filter((row:any)=>row.package_id===pkg).map(({document,...row}:any)=>structuredClone(row)),
        next_after:state.badCursor?after:state.partial&&after===0?10:null,source_visibility_filtered:state.filtered||state.partial&&after===0}};
      const row=structuredClone(state.rows.find((row:any)=>pathname.endsWith(row.materialization_id)));
      if(!row)return {status:404,data:{error:{code:'NOT_FOUND',message:'Specification unavailable'}}};if(state.wrong)row.git_receipt.revision_id='pmsp_'+'0'.repeat(32);
      return {status:200,data:{version:1,workspace_id:workspaceId,materialization:row}};
    }
    if(pathname.startsWith('/operations/')&&input.method==='GET'){
      const op=[...state.ops.values()].find((op:any)=>pathname.endsWith(op.operation_id));if(op)return {status:200,data:{operation:op}};
    }
    if(pathname==='/operations'&&input.method==='POST'&&input.body.action==='pm.spec.materialize'){
      state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
      if(!op){const pkg=base.fixture.state.packages.find((row:any)=>row.package_id===input.body.target.package_id),row=save(pkg);
        op={...input.body,actor:base.fixture.base.state.actor,idempotency_key:input.idempotency_key,operation_id:row.operation_id,status:'succeeded',
          result:{status:'materialized',execution_authorized:false,...Object.fromEntries(['workspace_id','materialization_id','package_id','document_digest','artifact_ref','git_receipt','operation_id'].map(key=>[key,row[key]]))}};state.ops.set(input.idempotency_key,op);}
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status:200,data:{operation:op}};
    }
    return base.dispatch(input);
  };
  return {base,state,save,dispatch};
}
export async function mountSpec(page:Page,f:ReturnType<typeof specFixture>,locale='en'){
  await page.exposeFunction('specFixtureRequest',f.dispatch);
  await page.route('**/__spec_src/*.js',async route=>{
    const file=path.basename(new URL(route.request().url()).pathname);let source=await readFile(new URL('../src/'+file,import.meta.url),'utf8');
    if(file==='pm-workspace.js')source=source.replace('function durableForm(', 'export function durableForm(');
    await route.fulfill({contentType:'text/javascript',body:source});
  });await unavailableBriefTransport(page,'__spec_src');
  await page.route('**/__spec_style.css',async route=>route.fulfill({contentType:'text/css',body:await readFile(new URL('../src/pm-spec-materialization.css',import.meta.url),'utf8')}));
  await page.route('**/__spec_harness',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/dashboard/app.css"><link rel="stylesheet" href="/__spec_style.css"><main class="pm-main panel" id="mount"></main>'}));
  await page.goto('/__spec_harness');await bootSpec(page,locale);
}
export async function bootSpec(page:Page,locale='en'){
  await page.evaluate(async({locale,workspaceId,packageId})=>{
    const {workspaceSpecMaterialization}=await import('/__spec_src/pm-spec-materialization.js'),{workspacePackageHandoff}=await import('/__spec_src/pm-package-handoff.js'),{durableForm}=await import('/__spec_src/pm-workspace.js');
    const {pmSpecMaterializationStrings}=await import('/__spec_src/pm-spec-materialization-strings.js'),{pmPackageHandoffStrings}=await import('/__spec_src/pm-package-handoff-strings.js'),{pmStrings}=await import('/__spec_src/pm-strings.js');
    const strings={...pmStrings[locale],...pmPackageHandoffStrings[locale],...pmSpecMaterializationStrings[locale]},t=(key:string)=>strings[key]||key;
    const h=(tag:string,attrs:any={},...children:any[])=>{const el=document.createElement(tag);for(const[key,value]of Object.entries(attrs||{})){
      if(value===null||value===undefined||value===false)continue;if(key.startsWith('on'))el.addEventListener(key.slice(2),value as any);else el.setAttribute(key,value===true?'':String(value));
    }for(const child of children.flat()){if(child===null||child===undefined||child===false)continue;el.append(child instanceof Node?child:document.createTextNode(String(child)));}return el;};
    const win=window as any,api=async(method:string,path:string,body:any,key:any)=>{const result=await win.specFixtureRequest({method,path,body,idempotency_key:key});
      if(result.status>=400)throw Object.assign(Error(result.data.error.message),{code:result.data.error.code,status:result.status});return result.data;};
    const errorBox=(error:any)=>h('p',{class:'error'},error.message),input=(label:string,{multiline=false,max=4000,required=true,value=''}:any={})=>{
      const field=h(multiline?'textarea':'input',{'aria-label':t(label),maxlength:max,required}) as HTMLInputElement;field.value=value;return field;
    },labelled=(key:string,field:any)=>h('label',{},t(key),field),alive=()=>{};
    let caps=await api('GET','/capabilities'),workspace=(await api('GET','/pm/workspaces/'+workspaceId)).workspace,config=await api('GET','/spec-ui-config');
    const form=(options:any)=>durableForm({h,t,api,caps:()=>caps,guard:alive,namespace:'spec-ui.'+caps.actor,errorBox,...options,
      permitted:()=>options.permitted()&&config.stage==='development'});
    const args={h,t,api,alive,workspaceId,workspace:()=>workspace,caps:()=>caps,permitted:()=>['owner','contributor'].includes(workspace.membership?.role),form,input,labelled,errorBox,namespace:'spec-ui.'+caps.actor};
    let material:any,handoff:any;const clear=()=>{material?.withdraw();handoff?.withdraw();};
    material=workspaceSpecMaterialization({...args,onSourceFailure:clear});handoff=workspacePackageHandoff({...args,onSourceFailure:clear});document.getElementById('mount')!.append(material.element,handoff.element);
    win.specSelect=async(id=packageId)=>{const row=(await api('GET',`/pm/workspaces/${workspaceId}/work-packages/${id}`)).package;handoff.setPackage(row);await material.setPackage(row);};
    win.specRefresh=async()=>{caps=await api('GET','/capabilities');workspace=(await api('GET','/pm/workspaces/'+workspaceId)).workspace;config=await api('GET','/spec-ui-config');await material.refresh();await handoff.refresh();};win.specWithdraw=clear;
    await win.specSelect();for(const root of document.querySelectorAll('[data-pm-spec-materialization]>details,[data-pm-package-handoff]>details'))(root as HTMLDetailsElement).open=true;
  },{locale,workspaceId,packageId});
  await expect(panel(page)).toBeVisible();
}
