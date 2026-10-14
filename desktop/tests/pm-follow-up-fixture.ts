import {expect,type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {unavailableBriefTransport} from './pm-source-transport-fixture.ts';

export const workspaceId='ws_'+'1'.repeat(32), snapshotId='pmsnap_'+'2'.repeat(32), threadId='pmt_'+'3'.repeat(32);
export const planId='pmrev_'+'4'.repeat(32), governanceId='pmgrev_'+'5'.repeat(32), recordId='pmgov_'+'6'.repeat(32);
export const meetingId='pmmeet_'+'7'.repeat(32), completionId='pmmc_'+'8'.repeat(32), resultId='pmfue_'+'9'.repeat(32);
export const intakeId=(n:number)=>'pmfu_'+n.toString(16).padStart(32,'0');
export const at=1791705600;
const domains=['governance','scope','schedule','finance','stakeholders','resources','risk','quality'];
export function intake(n:number) {
  const known={input_id:'pmi_'+'a'.repeat(32),status:'user_confirmed',value:'Members can recover access to their account.'};
  const unknown={input_id:'pmi_'+'b'.repeat(32),status:'unresolved',value:'The first paid tier price is undecided.'};
  const answer={input_id:'pmi_'+'c'.repeat(32),status:'user_confirmed',value:'The owner explicitly confirmed the pricing decision.'};
  const issue={issue_id:'pmfui_'+'d'.repeat(32),kind:'meeting_follow_up',text:'Review the recovery verification',status:'reported_done',
    source_refs:[{kind:'meeting_follow_up',completion_id:completionId,follow_up_id:'recovery',result_event_id:resultId}],
    details:{result:'The implementer reports the repair is complete.'},fingerprint:'a'.repeat(64),ordering_basis:'reported_done_verification_unassessed'};
  const knowledge:any=Object.fromEntries(domains.map(domain=>[domain,{baseline_inputs:[],supplemental_inputs:[],answer_links:[],coverage:'unresolved'}]));
  knowledge.scope={baseline_inputs:[known],supplemental_inputs:[],answer_links:[],coverage:'attributed_inputs'};
  knowledge.finance={baseline_inputs:[unknown],supplemental_inputs:[answer],answer_links:[{source_input_id:unknown.input_id,answer_input_id:answer.input_id,accepted_plan_changed:false}],coverage:'attributed_inputs'};
  knowledge.risk={baseline_inputs:[{input_id:'pmi_'+'e'.repeat(32),status:'agent_assumption',value:'Recovery email delivery needs further evidence.'}],supplemental_inputs:[],answer_links:[],coverage:'attributed_inputs'};
  return {sequence:n,intake_id:intakeId(n),workspace_id:workspaceId,version:n,created_by:'project-owner',created_at:at+n,
    operation_id:'op_'+n.toString(16).padStart(32,'0'),document_digest:String(n).repeat(64),document:{as_of:at+n,journal_cursor:100+n,
      snapshot_ref:{snapshot_id:snapshotId,snapshot_digest:'a'.repeat(64),as_of:at-300,journal_cursor:50},
      plan_ref:{document_revision:planId,scope_revision:'pmscope_'+planId,document_digest:'b'.repeat(64),plan_head_version:2},
      thread_ref:{thread_id:threadId,version:5},knowledge,issues:[issue],agenda:[{issue_id:issue.issue_id,text:issue.text,ordering_basis:issue.ordering_basis,source_refs:issue.source_refs}],
      previous_intake_id:n>1?intakeId(n-1):null,changes:{added:[],changed:[],unchanged:[issue.issue_id],no_longer_selected:['pmfui_'+'f'.repeat(32)]},
      selected_governance_refs:[{record_id:recordId,revision_id:governanceId,document:{title:'Recovery reliability assumption'}}],
      selected_meeting_follow_up_refs:[{completion_id:completionId,follow_up_id:'recovery',result_event_id:resultId,follow_up:{text:'Review the repair'},
        original_snapshot_ref:{snapshot_id:snapshotId},result:{document:{snapshot_ref:{snapshot_id:'pmsnap_'+'0'.repeat(32)}}}}],
      coverage:{thread:{truncated:false},cost:'unknown',quality:'unassessed'},execution_authorized:false,runtime_invoked:false}};
}
export function fixture() {
  const state:any={rows:[intake(1),intake(2),intake(3)],head:3,plan:2,threadVersion:5,actor:'project-owner',role:'owner',manage:true,
    reads:[],writes:[],ops:new Map(),failRead:null,failMore:null,failSource:null,wrongScope:false,wrongDetail:false,nonAdvancing:false,
    lost:false,holdPath:null,held:[],empty:false,moreResults:true,hiddenIntakes:new Set(),failWorkspace:null,failIntake:null,wrongWorkspaceActor:false};
  const caps=()=>({actor:state.actor,role:state.role,scopes:state.manage?['observe','manage']:['observe'],actions:[{action:'pm.follow_up.prepare',allowed:state.manage}]});
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),p=url.pathname,base=`/pm/workspaces/${workspaceId}`,after=Number(url.searchParams.get('after')||0),limit=Number(url.searchParams.get('limit')||20);
    const error=(status:number,message:string,code=status===403?'FORBIDDEN':'UNAVAILABLE')=>({status,data:{error:{code,message}}});
    if(input.method==='POST'){
      state.writes.push(structuredClone(input));let op=state.ops.get(input.key);
      if(!op){
        state.head++;const value=intake(state.head);state.rows.push(value);
        op={...structuredClone(input.body),operation_id:'op_'+'f'.repeat(32),actor:state.actor,idempotency_key:input.key,status:'succeeded',
          result:{workspace_id:workspaceId,intake_id:value.intake_id,version:state.head}};state.ops.set(input.key,op);
      }
      if(state.lost){state.lost=false;return error(503,'The original reply was lost');}
      return {status:200,data:{operation:op}};
    }
    if(p.startsWith('/operations/'))return {status:200,data:{operation:[...state.ops.values()].find((v:any)=>v.operation_id===p.split('/').at(-1))}};
    state.reads.push(input.path);
    if(state.failRead)return error(state.failRead,'Workspace sources unavailable');
    if(state.failMore&&p.endsWith('/follow-ups')&&after>0)return error(state.failMore,'History read unavailable');
    if(state.failSource&&p.includes(state.failSource))return error(403,'Selected source access withdrawn');
    if(p===base&&state.failWorkspace)return error(state.failWorkspace,'Current workspace membership unavailable');
    if(p.startsWith(base+'/follow-ups/')){
      if(state.failIntake)return error(state.failIntake,'Preparation read failed');
      const id=p.endsWith('/latest')?state.rows.at(-1)?.intake_id:p.split('/').at(-1);
      if(state.hiddenIntakes.has(id))return error(404,'Original preparation source withdrawn','PM_FOLLOW_UP_SOURCE_UNAVAILABLE');
    }
    let data:any;
    const rows=state.empty?[]:state.rows;
    if(p===base+'/follow-ups'){
      const selected=rows.filter((row:any)=>row.sequence>after).slice(0,limit),more=rows.some((row:any)=>row.sequence>(selected.at(-1)?.sequence??after));
      data={version:1,workspace_id:workspaceId,head_version:state.head,intakes:selected.filter((row:any)=>!state.hiddenIntakes.has(row.intake_id)).map(({document,...metadata}:any)=>metadata),
        next_cursor:more?state.nonAdvancing?after:selected.at(-1).sequence:null,coverage:{visibility_filtered:selected.some((row:any)=>state.hiddenIntakes.has(row.intake_id))}};
    }else if(p===base+'/follow-ups/latest')data={version:1,workspace_id:workspaceId,head_version:state.head,intake:rows.at(-1)||null};
    else if(p.startsWith(base+'/follow-ups/'))data={version:1,workspace_id:workspaceId,head_version:state.head,
      intake:rows.find((row:any)=>row.intake_id===p.split('/').at(-1))};
    else if(p===base+'/plans/head')data={version:1,head:{workspace_id:workspaceId,version:state.plan}};
    else if(p===base)data={version:1,workspace:{workspace_id:workspaceId,membership:{actor:state.wrongWorkspaceActor?'someone-else':state.actor,role:state.workspaceRole||state.role}}};
    else if(p===base+'/snapshots')data={workspace_id:workspaceId,snapshots:[{workspace_id:workspaceId,snapshot_id:snapshotId,title:'Adopted membership evidence',sequence:1}],next_cursor:null};
    else if(p===base+'/snapshots/'+snapshotId)data={workspace_id:workspaceId,snapshot:{workspace_id:workspaceId,snapshot_id:snapshotId,baseline:{document:{title:'Accepted plan'}},visibility:{filtered:false}}};
    else if(p===base+'/threads')data={workspace_id:workspaceId,threads:[{workspace_id:workspaceId,thread_id:threadId,title:'Pricing discussion',mode:'planning',version:state.threadVersion}],next_cursor:null};
    else if(p===base+'/threads/'+threadId)data={workspace_id:workspaceId,thread:{workspace_id:workspaceId,thread_id:threadId,version:state.threadVersion}};
    else if(p===base+'/governance')data={workspace_id:workspaceId,records:[{record:{workspace_id:workspaceId,record_id:recordId},revision:{revision_id:governanceId,version:2,document:{title:'Recovery reliability assumption'}}}],next_cursor:null};
    else if(p===base+'/meetings')data={workspace_id:workspaceId,meetings:[{workspace_id:workspaceId,meeting_id:meetingId,title:'Recovery retrospective',completion_id:completionId}],next_cursor:null};
    else if(p===base+'/meetings/'+meetingId)data={workspace_id:workspaceId,meeting:{workspace_id:workspaceId,meeting_id:meetingId},
      completion:{completion_id:completionId,meeting_id:meetingId,document:{follow_ups:[{id:'recovery',text:'Review the recovery result'}]}},
      follow_up_events:[],history_next_cursors:{follow_ups:state.moreResults?0:null}};
    else if(p===base+'/meetings/'+meetingId+'/history/follow_ups')data={workspace_id:workspaceId,meeting_id:meetingId,kind:'follow_ups',records:[{
      event_id:resultId,meeting_id:meetingId,follow_up_id:'recovery',document:{status:'reported_done',result:'First repair report'}}],next_cursor:null};
    else throw Error('Unexpected route '+input.path);
    data=structuredClone(data);if(state.wrongScope&&data.workspace_id)data.workspace_id='ws_'+'9'.repeat(32);
    if(state.wrongDetail&&data.intake)data.intake.workspace_id='ws_'+'9'.repeat(32);
    const result={status:200,data};if(state.holdPath&&p.includes(state.holdPath)){state.holdPath=null;return new Promise(resolve=>state.held.push(()=>resolve(result)));}return result;
  };
  return {state,dispatch,caps};
}
const clients=new WeakMap<Page,ReturnType<typeof fixture>>();
export async function mount(page:Page,f:ReturnType<typeof fixture>,{locale='en',pageSize=2}={}) {
  const first=!clients.has(page);clients.set(page,f);
  if(first){await page.exposeFunction('followUpFixtureRequest',(input:any)=>clients.get(page)!.dispatch(input));
    await page.exposeFunction('followUpFixtureCaps',()=>clients.get(page)!.caps());}
  await page.route('**/__follow_up_src/*',async route=>{
    const file=path.basename(new URL(route.request().url()).pathname);
    let body=await readFile(new URL('../src/'+file,import.meta.url),'utf8');
    // Exercise the production durable form without changing its public exports.
    if(file==='pm-workspace.js')body=body.replace('function durableForm(','export function durableForm(');
    await route.fulfill({contentType:file.endsWith('.css')?'text/css':'text/javascript',body});
  });
  await unavailableBriefTransport(page,'__follow_up_src');
  await page.route('**/__follow_up_harness',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/dashboard/app.css"><link rel="stylesheet" href="/__follow_up_src/pm-follow-up.css"><div class="pm-shell"><aside><h2>PM workspace</h2><p>Membership service</p></aside><main class="pm-main" id="mount"><section class="panel"><h1>Membership service</h1><p>Goal · A returning member can access their account.</p></section></main></div>'}));
  await page.goto('/__follow_up_harness');
  await page.evaluate(async({locale,workspaceId,pageSize})=>{
    const {workspaceFollowUp}=await import('/__follow_up_src/pm-follow-up.js');
    const {pmFollowUpStrings}=await import('/__follow_up_src/pm-follow-up-strings.js');
    const {pmStrings}=await import('/__follow_up_src/pm-strings.js');
    const {durableForm}=await import('/__follow_up_src/pm-workspace.js');
    const win=window as any;let caps=await win.followUpFixtureCaps();
    const {uiStrings}=await import('/__follow_up_src/ui/ui-strings.js');
    const strings={...uiStrings[locale],...pmStrings[locale],...pmFollowUpStrings[locale]},t=(key:string,args:any={})=>Object.entries(args).reduce((text,[key,value])=>text.replaceAll('{'+key+'}',String(value)),strings[key]||key);
    const h=(tag:string,attrs:any={},...children:any[])=>{const node=document.createElement(tag);for(const[key,value]of Object.entries(attrs)){
      if(value==null||value===false)continue;if(key.startsWith('on'))node.addEventListener(key.slice(2),value as any);else node.setAttribute(key,value===true?'':String(value));}
      for(const child of children.flat())if(child!=null&&child!==false)node.append(child instanceof Node?child:String(child));return node;};
    const api=async(method:string,path:string,body?:any,key?:string)=>{const result=await win.followUpFixtureRequest({method,path,body,key});
      if(result.status>=400)throw Object.assign(Error(result.data.error.message),{code:result.data.error.code,status:result.status});return result.data;};
    const input=(key:string,{multiline=false,required=true,value='',max=4000}={})=>{const node=h(multiline?'textarea':'input',{'aria-label':t(key),required,maxlength:max});(node as HTMLInputElement).value=value;return node;};
    const errorBox=(error:any)=>h('p',{class:'error'},error.message),labelled=(key:string,node:any)=>h('label',{},t(key),node);
    const form=(options:any)=>durableForm({h,t,api,caps:()=>caps,guard:()=>{},namespace:'follow-up-fixture.'+caps.actor,errorBox,...options});
    const ui=workspaceFollowUp({h,t,api,alive:()=>{},workspaceId,pageSize,namespace:'follow-up-fixture.'+caps.actor,form,input,labelled,errorBox,caps:()=>caps,
      permitted:()=>['owner','contributor'].includes(caps.role)&&caps.scopes.includes('manage')});
    document.getElementById('mount')!.append(ui.element);
    win.refreshFollowUp=async()=>{caps=await win.followUpFixtureCaps();await ui.refresh();};
    await ui.refresh();
  },{locale,workspaceId,pageSize});
  await expect(page.locator('[data-pm-follow-up]')).toBeVisible();
}
