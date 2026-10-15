import {expect,type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {unavailableBriefTransport} from './pm-source-transport-fixture.ts';
export const workspaceId='ws_'+'1'.repeat(32),threadId='pmt_'+'2'.repeat(32),proposalId='pmcp_'+'3'.repeat(32);
export const PROPOSE='pm.conversation.propose',ADOPT='pm.conversation.adopt';
export function proposalFixture(){
  const state:any={actor:'owner',role:'owner',available:true,eligible:true,deny:false,lost:false,conflict:false,reads:[],writes:[],ops:new Map(),gate:null,partial:false,
    head:{workspace_id:workspaceId,version:0,latest_revision_id:null,accepted_revision_id:null},thread:{thread:{workspace_id:workspaceId,thread_id:threadId,version:4,mode:'idea'},capabilities:{write:true,decide:true}},proposals:[]};
  const caps=()=>({actor:state.actor,scopes:state.role==='reader'?['observe']:['observe','manage','start'],actions:[PROPOSE,ADOPT].map(action=>({action,allowed:state.role!=='reader'})),features:{pm_workspaces:{version:1,conversation_proposals:state.available?{version:1}:null}}});
  const capability=()=>({native_analysis:{eligible_for_preflight:state.eligible},...(state.structured_analysis?{structured_analysis:state.structured_analysis}:{})});
  const save=()=>{const document={version:1,title:'Paid member access',summary:'Add paid member login and password reset. Keep referral codes outside this release.',
    candidates:[{key:'login',domain:'scope',text:'Paid members can log in',kind:'proposal',source_refs:['message.owner']},
      {key:'reset',domain:'quality',text:'Password reset links expire after one use',kind:'proposal',source_refs:['message.owner']},
      {key:'provider',domain:'risk',text:'Existing identity provider supports reset links',kind:'assumption',source_refs:['message.owner']},
      {key:'referral',domain:'scope',text:'Referral codes are outside this release',kind:'excluded',source_refs:['message.owner']}],
    requirements:[{id:'member_login',source_key:'login',acceptance_keys:['reset'],priority:'must',depends_on:[]}],removed_requirement_ids:[],
    questions:[{question:'Which identity provider is already in use?',why_now:'Its reset behavior affects implementation.',source_refs:['message.owner']}]};
    const proposal={proposal_id:proposalId,thread_id:threadId,workspace_id:workspaceId,operation_id:'op_'+'3'.repeat(32),run_id:'pmr_'+'5'.repeat(32),document_digest:'a'.repeat(64),
      adoptable:true,adoptable_reason:null,adopted_revision_id:null,raw_reply:JSON.stringify(document),validation:{status:'format_valid',errors:[]},document,context:{thread_version:state.thread.thread.version,plan_head:structuredClone(state.head),accepted_plan:null,
        source_references:{'message.owner':{kind:'message',id:'pmm_'+'6'.repeat(32),quote:'付費會員要能登入與忘記密碼；這輪不要推薦碼。 <img src=x onerror=alert(1)>',digest:'b'.repeat(64)}},coverage:{complete:true}}};
    state.proposals=[proposal];return proposal;};
  const dispatch=async(input:any)=>{const url=new URL(input.path,'http://fixture'),pathname=url.pathname;
    if(input.method==='GET')state.reads.push(input.path);
    if(pathname==='/fixture-state')return {status:200,data:{caps:caps(),capability:capability(),thread:state.thread,head:state.head}};
    if(state.deny)return {status:404,data:{error:{code:'NOT_FOUND',message:'Original sources withdrawn'}}};
    if(input.method==='GET'){
      for(const p of state.proposals)if(p.context.plan_head.version!==state.head.version){p.adoptable=false;p.adoptable_reason='plan_head_changed';}
      if(state.gate)await state.gate;
      if(pathname.endsWith('/plans'))return {status:200,data:{workspace_id:workspaceId,head:structuredClone(state.head),revisions:[]}};
      if(pathname.endsWith('/conversation-proposals')){const partial=state.partial&&!url.searchParams.get('after')?.match(/[1-9]/);return {status:200,data:{version:1,workspace_id:workspaceId,proposals:partial?[]:state.proposals.map((p:any)=>({proposal_id:p.proposal_id,thread_id:p.thread_id,operation_id:p.operation_id,run_id:p.run_id,document_digest:p.document_digest,validation:p.validation,title:p.document?.title??null,adopted_revision_id:p.adopted_revision_id??null,adoptable:p.adoptable,adoptable_reason:p.adoptable_reason})),next_cursor:partial?4:null,coverage:{visibility_filtered:partial}}};}
      if(pathname.includes('/conversation-proposals/'))return {status:200,data:{version:1,workspace_id:workspaceId,proposal:structuredClone(state.proposals.find((p:any)=>pathname.endsWith(p.proposal_id)))}};
      if(pathname.startsWith('/operations/')){const op=[...state.ops.values()].find((op:any)=>pathname.endsWith(op.operation_id));return {status:200,data:{operation:structuredClone(op)}};}
    }
    if(input.method==='POST'&&pathname==='/operations'){
      state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
      if(!op){if(state.conflict)return {status:409,data:{error:{code:'VERSION_CONFLICT',message:'Plan version changed',admission_refused:true}}};
        const result=input.body.action===PROPOSE?{run_id:'pmr_'+'5'.repeat(32),outcome:{proposal_id:save().proposal_id,validation_status:'format_valid'}}:{revision_id:'pmrev_'+'7'.repeat(32),accepted:true};
        op={...structuredClone(input.body),actor:state.actor,idempotency_key:input.idempotency_key,operation_id:'op_'+String(10+state.ops.size).padStart(32,'0'),status:'succeeded',result};state.ops.set(input.idempotency_key,op);}
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Original reply unavailable'}}};}
      return {status:200,data:{operation:structuredClone(op)}};
    }throw Error('Unhandled proposal fixture request '+input.path);
  };return {state,save,caps,capability,dispatch};
}
export const panel=(page:Page)=>page.locator('[data-pm-plan-proposals]');
export const editor=(page:Page,action=ADOPT)=>panel(page).locator(`[data-pm-action="${action}"]`);
export async function mountProposal(page:Page,f:ReturnType<typeof proposalFixture>,locale='en'){
  page.on('pageerror',error=>{throw error;});await page.exposeFunction('proposalFixtureRequest',f.dispatch);
  await page.route('**/__proposal_src/*.js',async route=>{const file=path.basename(new URL(route.request().url()).pathname);let source=await readFile(new URL('../src/'+file,import.meta.url),'utf8');
    if(file==='pm-workspace.js')source=source.replace('function durableForm(', 'export function durableForm(');await route.fulfill({contentType:'text/javascript',body:source});});
  await unavailableBriefTransport(page,'__proposal_src');
  await page.route('**/__proposal_harness',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/dashboard/app.css"><div class="pm-shell"><aside aria-label="Workspace"><strong>Member access</strong><p>Idea discussion</p></aside><main class="pm-main" id="mount"></main></div>'}));
  await page.goto('/__proposal_harness');await bootProposal(page,locale);
}
export async function bootProposal(page:Page,locale='en'){
  await page.evaluate(async({locale,workspaceId,threadId})=>{
    const {planProposals}=await import('/__proposal_src/pm-plan-proposals.js'),{durableForm}=await import('/__proposal_src/pm-workspace.js');
    const {pmPlanProposalStrings}=await import('/__proposal_src/pm-plan-proposal-strings.js'),{pmStrings}=await import('/__proposal_src/pm-strings.js');
    const strings={...pmStrings[locale],...pmPlanProposalStrings[locale]},t=(key:string)=>strings[key]||key;
    const h=(tag:string,attrs:any={},...children:any[])=>{const el=document.createElement(tag);for(const[key,value]of Object.entries(attrs||{})){if(value===null||value===undefined||value===false)continue;
      if(key.startsWith('on'))el.addEventListener(key.slice(2),value as any);else el.setAttribute(key,value===true?'':String(value));}for(const child of children.flat()){if(child!==null&&child!==undefined&&child!==false)el.append(child instanceof Node?child:document.createTextNode(String(child)));}return el;};
    const win=window as any,api=async(method:string,path:string,body:any,key:any)=>{const result=await win.proposalFixtureRequest({method,path,body,idempotency_key:key});if(result.status>=400)throw Object.assign(Error(result.data.error.message),{code:result.data.error.code,status:result.status,admissionRefused:result.data.error.admission_refused});return result.data;};
    const errorBox=(error:any)=>h('p',{class:'error'},error.message),input=(label:string,{multiline=false,max=4000,required=true,value=''}:any={})=>{
      const field=h(multiline?'textarea':'input',{'aria-label':t(label),maxlength:max,required}) as HTMLInputElement;field.value=value;return field;},labelled=(key:string,field:any)=>h('label',{},t(key),field),alive=()=>{};
    let current=await api('GET','/fixture-state');
    const form=(options:any)=>durableForm({h,t,api,caps:()=>current.caps,guard:alive,namespace:'proposal-ui.'+current.caps.actor,errorBox,...options});
    const proposal=planProposals({h,t,api,alive,workspaceId,threadId,caps:()=>current.caps,thread:()=>current.thread,
      permitted:(decide:boolean)=>current.caps.actor==='owner'&&(decide?current.thread.capabilities.decide:current.thread.capabilities.write),form,input,labelled,errorBox,refresh:async()=>{current=await api('GET','/fixture-state');proposal.update({head:current.head,capabilities:current.capability});}});
    document.getElementById('mount')!.replaceChildren(proposal.box);proposal.update({head:current.head,capabilities:current.capability});
    win.proposalRefresh=async()=>{current=await api('GET','/fixture-state');proposal.update({head:current.head,capabilities:current.capability});await proposal.refresh();};
    win.proposalWithdraw=()=>proposal.withdraw();win.proposalDispose=()=>proposal.dispose();await proposal.refresh();
  },{locale,workspaceId,threadId});
}
export async function selectProposal(page:Page,id=proposalId){await panel(page).getByRole('combobox',{name:'Saved proposal',exact:true}).selectOption(id);await expect(editor(page)).toBeVisible();}
