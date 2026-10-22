import {sourceModules} from './source-modules.ts';
import type {Page} from '@playwright/test';

export const workspaceId='ws_'+'a'.repeat(32), commit='b'.repeat(40), sourcePath='openspec/specs/membership/spec.md';
export function fixture(){
  const state={eligible:true, denied:false, bad:false, commit, reads:[] as string[], checks:0, writes:[] as any[], delay:null as null|Promise<void>};
  const document=(path:string,role='main_spec')=>({path,role,location:role==='tasks'?'archive':'main',
    commit_sha:state.commit,file_id:'pmsfile_'+'c'.repeat(64),source_id:'pmssrc_'+'d'.repeat(64),blob_sha:'e'.repeat(40),sha256:'f'.repeat(64),
    status:'read',content:role==='tasks'?'- [x] Form implemented\n- [ ] Delivery not verified':'# Membership\n<script>window.fixtureUnsafe=true</script>\nPassword recovery remains an unadopted proposal.',
    observations:{status:'lexical_observation_only',task_reports:role==='tasks'?[{line:1,text:'Form implemented',reported_checked:true,in_fenced_block:false},
      {line:2,text:'Delivery not verified',reported_checked:false,in_fenced_block:false}]:[]}});
  const dispatch=async(input:any)=>{
    if(input.method!=='GET'){state.writes.push(input);throw Error('Specification reading cannot write');}
    const url=new URL(input.path,'http://fixture');
    if(url.pathname.endsWith('/spec-sources/capabilities')){
      state.checks++;if(state.denied)return {status:403,data:{error:{code:'FORBIDDEN',message:'Current source access was revoked.'}}};
      return {status:200,data:{version:1,adapter:'openspec-fixed-git-v1',workspace_id:workspaceId,eligible:state.eligible,
        reason:state.eligible?'git_read_required':'PM_SPEC_BINDING_UNAVAILABLE',repository:'example/membership',commit_sha:state.commit,git_verified:false,execution_authorized:false}};
    }
    if(!url.pathname.endsWith('/spec-sources'))throw Error('Unexpected specification endpoint: '+input.path);
    state.reads.push(input.path);
    // Capture the original request identity before awaiting the response.
    const fixedCommit=state.commit, documents=[document(sourcePath),document('openspec/changes/archive/2026-01-01-membership/tasks.md','tasks'),
      {...document('openspec/changes/recovery/design.md','design'),status:'unavailable',content:null,reason:'document_too_large'}];
    if(state.delay)await state.delay;
    if(state.denied)return {status:404,data:{error:{code:'NOT_FOUND',message:'Current source access was revoked.'}}};
    const selected=url.searchParams.get('paths')?.split(',');
    return {status:200,data:{version:1,adapter:'openspec-fixed-git-v1',workspace_id:state.bad?'ws_'+'f'.repeat(32):workspaceId,
      repository:'example/membership',commit_sha:fixedCommit,tree_sha:'1'.repeat(40),document_digest:'2'.repeat(64),execution_authorized:false,
      coverage:{content_complete:!!selected,ignored_paths:1},documents:documents.filter(doc=>!selected||selected.includes(doc.path))}};
  };return {state,dispatch};
}

export async function mount(page:Page,f:ReturnType<typeof fixture>,native=false){
  if(native){
    await page.exposeFunction('specFixtureRequest',f.dispatch);
    await page.addInitScript(()=>Object.assign(window,{isTauri:true,__TAURI_INTERNALS__:{invoke:async(command:string,args:any)=>{
      if(command!=='connector_request')throw Error('Unexpected native command');return (window as any).specFixtureRequest(args.input);
    }}}));
  }
  await page.route('**/api/v1/**',async route=>{
    if(native)throw Error('Native reads must use IPC');
    const request=route.request(),url=new URL(request.url());
    const result=await f.dispatch({method:request.method(),path:url.pathname.slice(7)+url.search});
    await route.fulfill({status:result.status,json:result.data});
  });
  await sourceModules(page, '__spec_src');
  await page.route('**/__spec_harness',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/dashboard/app.css"><div class="pm-shell"><aside><h2>PM workspaces</h2><p>Membership</p></aside><main class="pm-main"><h1>Membership workspace</h1><p>Plan and specification evidence</p><div id="mount"></div></main></div>'}));
  await page.goto('/__spec_harness');
  await page.evaluate(async workspaceId=>{
    const {specSources}=await import('/__spec_src/pm-spec-sources.js'),{connectorRequest}=await import('/__spec_src/transport.js');
    const h=(tag:string,attrs:any={},...children:any[])=>{
      const node=document.createElement(tag);for(const [key,value]of Object.entries(attrs)){
        if(value===false||value===null||value===undefined)continue;
        if(key.startsWith('on'))node.addEventListener(key.slice(2),value as any);else node.setAttribute(key,value===true?'':String(value));
      }for(const value of children.flat())if(value!==null&&value!==undefined&&value!==false)node.append(value instanceof Node?value:document.createTextNode(String(value)));return node;
    };
    const api=async(method:string,path:string)=>{
      const response=await connectorRequest(method,path,undefined,undefined,'fixture-token');
      if(response.status>=400)throw Object.assign(Error(response.data.error.message),{code:response.data.error.code});return response.data;
    };
    const win=window as any;win.specAlive=true;
    const ui=specSources({h,t:(key:string)=>key,api,alive:()=>{if(!win.specAlive)throw Error('Workspace changed');},workspaceId,
      errorBox:(error:any)=>h('p',{class:'error'},error.message)});
    document.getElementById('mount')!.append(ui.element);win.refreshSpec=ui.refresh;await ui.refresh();
  },workspaceId);
}
