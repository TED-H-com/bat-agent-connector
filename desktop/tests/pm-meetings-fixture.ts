import {expect,type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {unavailableBriefTransport} from './pm-source-transport-fixture.ts';

export const workspaceId='ws_'+'1'.repeat(32),sprintId='pms_'+'2'.repeat(32),snapshotId='pmsnap_'+'3'.repeat(32);
const actions=['create','context.revise','note','draft','complete','follow_up.record'].map(key=>'pm.meeting.'+key);
export const action=(page:Page,name:string)=>page.locator(`[data-pm-action="pm.meeting.${name}"]`);
export function meetingFixture(kind='planning') {
  const binding={binding_id:'pmwb_'+'4'.repeat(32),work_item_id:'wi_'+'5'.repeat(20),plan_revision_id:'pmrev_'+'6'.repeat(32),
    title:'Verify account recovery',assignee:'owner',how:'Verify account recovery in my own worktree',requirement_ids:['account.recovery']};
  const sprint={workspace_id:workspaceId,sprint_id:sprintId,version:4,status:'active',started_at:'2026-10-11T10:00:00Z',
    definition:{title:'Reliable account recovery',owner_actor:'owner',developers:['owner','developer'],capacity_note:'One focused improvement'}};
  const snapshot={workspace_id:workspaceId,snapshot_id:snapshotId,title:'Account recovery snapshot',snapshot_digest:'b'.repeat(64),as_of:'2026-10-11T12:00:00Z',journal_cursor:40,
    evidence:[{evidence_id:'pme_'+'7'.repeat(32),summary:'Reset tests passed; production outcome not yet checked'}]};
  const state:any={actor:'owner',role:'owner',manage:true,lost:false,writes:[],reads:[],ops:new Map(),meetings:[],sprints:[sprint],snapshots:[snapshot],histories:{},failRead:false,wrongScope:false,moreNotes:false};
  const makeContext=(id:string,version=1)=>({sequence:version,meeting_id:id,context_revision_id:'pmmctx_'+String(version).repeat(32),actor:'owner',document:{
    sprint_id:sprintId,sprint_version:sprint.version,sprint_status:sprint.status,sprint_definition:structuredClone(sprint.definition),
    sprint_goal:{revision_id:'pmsg_'+'8'.repeat(32),text:'Members regain access without losing their account'},selection_revision_id:'pmssel_'+'9'.repeat(32),selected_items:[binding],
    snapshot_ref:{snapshot_id:snapshotId,snapshot_digest:snapshot.snapshot_digest,as_of:snapshot.as_of,journal_cursor:40},
    artifacts:{product_goal:{revision_id:'pmgoal_'+'1'.repeat(32),document:{text:'Reliable membership access'}},
      backlog:{revision_id:'pmbkl_'+'1'.repeat(32),document:{items:[binding]}},dod:{revision_id:'pmdod_'+'1'.repeat(32),document:{criteria:[{text:'Relevant tests and actual behavior are checked'}]}}}}});
  const addMeeting=(kind:string,title='Account recovery discussion')=>{
    const id='pmmeet_'+String(state.meetings.length+1).repeat(32),context=makeContext(id),meeting={workspace_id:workspaceId,meeting_id:id,sprint_id:sprintId,
      sequence:state.meetings.length+1,title,kind,version:1,status:'draft',context_revision_id:context.context_revision_id,draft_revision_id:null,completion_id:null};
    const detail={workspace_id:workspaceId,meeting,context,snapshot,notes:[],draft:null,completion:null,follow_up_events:[],history_next_cursors:{notes:null,follow_ups:null}};
    state.meetings.push(detail);return detail;
  };
  addMeeting(kind);
  const config=()=>({caps:{actor:state.actor,scopes:['observe',...(state.manage?['manage']:[])],actions:actions.map(action=>({action,allowed:state.manage}))},
    workspace:{workspace_id:workspaceId,version:3,membership:{actor:state.actor,role:state.role},members:[
      {actor:'owner',role:state.actor==='owner'?state.role:'owner'},{actor:'developer',role:state.actor==='developer'?state.role:'contributor'},{actor:'reader',role:'reader'}]}});
  const response=(data:any,status=200)=>({data,status});
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),pathname=url.pathname,base='/pm/workspaces/'+workspaceId;
    if(input.method==='GET'){
      state.reads.push(input.path);
      if(pathname.startsWith('/operations/'))return response({operation:[...state.ops.values()].find((row:any)=>pathname.endsWith(row.operation_id))});
      if(state.failRead)return response({error:{code:state.failRead==='revoked'?'FORBIDDEN':'UNAVAILABLE',message:state.failRead==='revoked'?'Workspace access removed':'Meeting read unavailable'}},state.failRead==='revoked'?403:503);
      if(pathname===base+'/meetings')return response({workspace_id:workspaceId,meetings:state.meetings.map((row:any)=>row.meeting),next_cursor:null});
      if(pathname===base+'/sprints')return response({sprints:state.sprints,next_cursor:null});
      if(pathname===base+'/snapshots'){
        if(state.pagedSnapshots&&!url.searchParams.get('after')?.match(/^[1-9]/))return response({workspace_id:workspaceId,snapshots:[],next_cursor:10});
        return response({workspace_id:workspaceId,snapshots:state.snapshots,next_cursor:state.badCursor?10:null});
      }
      if(pathname.includes('/history/')){
        const [,id,kind]=pathname.match(/\/meetings\/([^/]+)\/history\/(.+)/)!;
        const detail=state.meetings.find((row:any)=>row.meeting.meeting_id===id);
        return response({workspace_id:workspaceId,meeting_id:id,kind,records:state.histories[kind]||
          (kind==='notes'?[{meeting_id:id,context_revision_id:detail.context.context_revision_id,note_id:'pmmnote_'+'9'.repeat(32),sequence:2,content:'A second discussion note',source_refs:[],actor:'developer'}]:[]),next_cursor:null});
      }
      if(pathname.startsWith(base+'/meetings/')){
        const detail=structuredClone(state.meetings.find((row:any)=>pathname.endsWith(row.meeting.meeting_id)));
        if(state.wrongScope)detail.workspace_id='ws_'+'0'.repeat(32);
        if(state.moreNotes)detail.history_next_cursors.notes=1;
        return response(detail);
      }
      throw Error('Unexpected read '+input.path);
    }
    if(input.method!=='POST'||pathname!=='/operations')throw Error('Unexpected write '+input.path);
    state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
    if(!op){const {action,target,params}=input.body;let detail=state.meetings.find((row:any)=>row.meeting.meeting_id===target.meeting_id),result:any={workspace_id:workspaceId,execution_authorized:false};
      if(action==='pm.meeting.create'){detail=addMeeting(params.kind,params.title);result.meeting_id=detail.meeting.meeting_id;}
      else {const meeting=detail.meeting;meeting.version++;
        if(action==='pm.meeting.note')detail.notes.push({meeting_id:meeting.meeting_id,note_id:'pmmnote_'+String(detail.notes.length+1).repeat(32),
          sequence:detail.notes.length+1,context_revision_id:meeting.context_revision_id,content:params.content,source_refs:params.source_refs,actor:state.actor});
        if(action==='pm.meeting.draft'){
          const revision_id='pmmrev_'+String(meeting.version).repeat(32),document={...structuredClone(params),kind:meeting.kind,quality:'unassessed',source_status:'discussion_proposal'};
          if(meeting.kind==='planning')Object.assign(document.output,{what:[binding],how:[{binding_id:binding.binding_id,recorded_how:binding.how}]});
          if(meeting.kind==='review')document.output.unreviewed_binding_ids=document.output.outcomes.length?[]:[binding.binding_id];
          detail.draft={meeting_id:meeting.meeting_id,revision_id,document,actor:state.actor};meeting.draft_revision_id=revision_id;result.draft_revision_id=revision_id;
        }
        if(action==='pm.meeting.complete'){
          meeting.status='completed';meeting.completion_id='pmmc_'+'1'.repeat(32);
          detail.completion={meeting_id:meeting.meeting_id,completion_id:meeting.completion_id,actor:state.actor,document:{...params,
            follow_ups:detail.draft.document.follow_ups,decisions:detail.draft.document.decisions.map((row:any)=>({...row,disposition:params.decision_ids.includes(row.id)?'recorded_discussion_decision':'proposal'}))}};
        }
        if(action==='pm.meeting.context.revise'){
          detail.context=makeContext(meeting.meeting_id,meeting.version);detail.context.document.snapshot_ref={...detail.context.document.snapshot_ref,snapshot_id:params.snapshot_id};
          detail.snapshot=state.snapshots.find((row:any)=>row.snapshot_id===params.snapshot_id);meeting.context_revision_id=detail.context.context_revision_id;detail.draft=null;meeting.draft_revision_id=null;
        }
        if(action==='pm.meeting.follow_up.record')detail.follow_up_events.push({meeting_id:meeting.meeting_id,event_id:'pmfue_'+'1'.repeat(32),sequence:1,actor:state.actor,
          document:{...params,snapshot_ref:{snapshot_id:params.snapshot_id||snapshotId},verification_status:'unassessed'}});
        Object.assign(result,{meeting_id:meeting.meeting_id,version:meeting.version,status:meeting.status});
      }
      op={...input.body,actor:state.actor,idempotency_key:input.idempotency_key,operation_id:'op_'+String(state.ops.size+1).padStart(32,'0'),status:'succeeded',result};state.ops.set(input.idempotency_key,op);
    }
    if(state.lost){state.lost=false;return response({error:{code:'LOST_REPLY',message:'Reply unavailable'}},503);}
    if(state.mismatch)return response({operation:{...op,actor:'another-actor'}});
    return response({operation:op});
  };
  return {state,config,dispatch,binding,addMeeting};
}
export async function mount(page:Page,f:ReturnType<typeof meetingFixture>,locale='en') {
  await page.exposeFunction('meetingFixtureRequest',f.dispatch);await page.exposeFunction('meetingFixtureConfig',f.config);
  await page.route('**/__meeting_src/*.js',async route=>{
    const file=path.basename(new URL(route.request().url()).pathname);let source=await readFile(new URL('../src/'+file,import.meta.url),'utf8');
    if(file==='pm-workspace.js')source=source.replace('function durableForm(', 'export function durableForm(');
    await route.fulfill({contentType:'text/javascript',body:source});
  });
  await unavailableBriefTransport(page,'__meeting_src');
  await page.route('**/__meeting_harness',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/dashboard/app.css"><div class="pm-shell"><aside><h2>PM workspace</h2><p>Membership service</p></aside><main class="pm-main" id="mount"></main></div>'}));
  await page.goto('/__meeting_harness');await boot(page,locale);
}
export async function boot(page:Page,locale='en') {
  await page.evaluate(async locale=>{
    const {workspaceMeetings}=await import('/__meeting_src/pm-meetings.js'),{durableForm}=await import('/__meeting_src/pm-workspace.js');
    const {pmMeetingStrings}=await import('/__meeting_src/pm-meetings-strings.js'),{pmStrings}=await import('/__meeting_src/pm-strings.js');
    const {uiStrings}=await import('/__meeting_src/ui/ui-strings.js');
    const strings={...uiStrings[locale],...pmStrings[locale],...pmMeetingStrings[locale]},t=(key:string,args:any={})=>Object.entries(args).reduce((text,[name,value])=>text.replaceAll('{'+name+'}',String(value)),strings[key]||key);
    const h=(tag:string,attrs:any={},...children:any[])=>{const el=document.createElement(tag);for(const [key,value]of Object.entries(attrs||{})){
      if(value===null||value===undefined||value===false)continue;if(key.startsWith('on'))el.addEventListener(key.slice(2),value as any);else el.setAttribute(key,value===true?'':String(value));
    }for(const child of children.flat()){if(child===null||child===undefined||child===false)continue;el.append(child instanceof Node?child:document.createTextNode(String(child)));}return el;};
    const win=window as any;let config=await win.meetingFixtureConfig();
    const api=async(method:string,path:string,body:any,key:any)=>{const result=await win.meetingFixtureRequest({method,path,body,idempotency_key:key});
      if(result.status>=400)throw Object.assign(Error(result.data.error.message),{code:result.data.error.code});return result.data;};
    const errorBox=(error:any)=>h('p',{class:'error'},error.message),input=(label:string,{multiline=false,max=4000,required=true,value=''}:any={})=>{
      const field=h(multiline?'textarea':'input',{'aria-label':t(label),maxlength:max,required}) as HTMLInputElement;field.value=value;return field;
    },labelled=(key:string,field:any)=>h('label',{},t(key),field),caps=()=>config.caps,alive=()=>{};
    const form=(options:any)=>durableForm({h,t,api,caps,guard:alive,namespace:'meeting-ui.'+config.caps.actor,errorBox,...options});
    const ui=workspaceMeetings({h,t,api,alive,workspaceId:config.workspace.workspace_id,workspace:()=>config.workspace,caps,
      permitted:(decide:boolean)=>config.caps.scopes.includes('manage')&&(decide?config.workspace.membership.role==='owner':['owner','contributor'].includes(config.workspace.membership.role)),form,input,labelled,errorBox});
    document.getElementById('mount')!.append(ui.element);win.refreshMeetings=async()=>{config=await win.meetingFixtureConfig();await ui.refresh();};await ui.refresh();
  },locale);
  await expect(page.locator('[data-pm-meetings]')).toBeVisible();
}
export async function openAction(page:Page,name:string) {await action(page,name).evaluate(el=>{let parent=el.parentElement;while(parent){if(parent.tagName==='DETAILS')(parent as HTMLDetailsElement).open=true;parent=parent.parentElement;}});}
export async function reason(page:Page,name:string,text='Keep a clear record of the discussion') {await openAction(page,name);await action(page,name).getByLabel('Reason for this record',{exact:true}).fill(text);}
