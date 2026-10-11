import {conversationFixture} from './conversation-fixture.ts';
export const workspaceId='ws_'+'1'.repeat(32), threadId='pmt_'+'2'.repeat(32), inputA='pmi_'+'a'.repeat(32), inputB='pmi_'+'b'.repeat(32);
export const pmPath=`/pm/workspaces/${workspaceId}/threads/${threadId}`;
export const pmRoute=`/dashboard/#/pm/${workspaceId}/${threadId}`;
const stamp='2026-10-11T12:00:00Z';
export function pmFixture() {
  const base=conversationFixture();
  const state={actor:'reader', principal:'person-1', role:'owner', manage:true, available:true, failRead:false,
    lost:false, mismatch:false, reject:null as null|{status:number,code:string}, defer:null as null|Promise<void>, cursor:0,
    workspaces:[{workspace_id:workspaceId,server_id:'pm-fixture',name:'Library refresh',repositories:['example/library'],version:1,created_by:'reader',created_at:stamp,updated_at:stamp}],
    thread:{thread_id:threadId,workspace_id:workspaceId,title:'A clearer first visit',mode:'idea',version:1,created_by:'reader',created_at:stamp,updated_at:stamp,draft_id:null as null|string},
    messages:[{message_id:'pmm_'+'3'.repeat(32),sequence:1,actor:'reader',content:'Visitors should find their first useful action without switching between pages.',created_at:stamp}],
    inputs:[{input_id:inputA,slot:'problem',value:'Keep the first useful action visible',status:'user_confirmed',source_ref:{kind:'message',message_id:'pmm_'+'3'.repeat(32)},recorded_by:'reader',confirmed_by:'reader',observed_at:stamp,evidence_verification:'central_message'},
      {input_id:inputB,slot:'alternatives',value:'Offer a guided tour as another option',status:'agent_assumption',source_ref:{kind:'statement',ref:'Idea from the discussion'},recorded_by:'reader',confirmed_by:null,observed_at:stamp,evidence_verification:'unverified_claim'}] as any[],
    draft:null as any, writes:[] as any[], ops:new Map<string,any>()};
  const actions=['pm.workspace.create','pm.thread.create','pm.message.append','pm.input.record','pm.input.confirm','pm.thread.plan'];
  const caps=()=>({actor:state.actor,scopes:state.manage?['observe','manage']:['observe'],api_version:1,contract_version:'2026-10-08',hosts:[],
    actions:actions.map(action=>({action,allowed:state.manage})),features:state.available?{pm_workspaces:{version:1,executable_modes:['idea','planning'],runner:false,plan_adoption:false,dispatch:false}}:{}});
  const workspace=(w:any)=>({...w,membership:{actor:state.actor,role:state.role,controller_id:'ctr_'+'4'.repeat(32)}});
  const detail=()=>({version:1,thread:{...state.thread},profile:{profile_id:'pm.'+state.thread.mode,version:1,mode:state.thread.mode,
    input_slots:(state.thread.mode==='idea'?['problem','stakeholders','alternatives','unknowns']:['value','scope','schedule','finance','stakeholders','resources','risk','quality']).map(key=>({key,domain:key,purpose:key}))},
    messages:structuredClone(state.messages),inputs:structuredClone(state.inputs),input_history:[],draft:structuredClone(state.draft),
    coverage:{messages_truncated:false,inputs_truncated:false,history_truncated:false},next_message_cursor:null,next_input_cursor:null,
    capabilities:{write:state.manage&&['owner','contributor'].includes(state.role),decide:state.manage&&state.role==='owner',runtime:false,adopt:false,dispatch:false}});
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'), path=url.pathname;
    if(path==='/capabilities')return {status:200,data:caps()};
    if(path==='/bootstrap')return {status:200,data:{capabilities:caps(),sync:{version:1,server_id:'pm-fixture',principal_id:state.principal,checkpoint:{cursor:0,token:'pm-0'}}}};
    if(path==='/events')return {status:200,data:{events:Number(url.searchParams.get('after'))<state.cursor?[{seq:state.cursor,resource_type:'pm_thread',resource_id:threadId,kind:'pm.message.append'}]:[],next_cursor:state.cursor,head_cursor:state.cursor,has_more:false,sync:{checkpoint:{cursor:state.cursor,token:'pm-'+state.cursor}}}};
    if(input.method==='GET'&&path.startsWith('/pm/')){
      if(state.failRead)return {status:503,data:{error:{code:'UNAVAILABLE',message:'PM read unavailable'}}};
      if(path==='/pm/workspaces')return {status:200,data:{workspaces:state.workspaces.map(workspace)}};
      if(path.endsWith('/threads'))return {status:200,data:{version:1,workspace_id:workspaceId,threads:[state.thread],next_cursor:null,runtime:{available:false,reason:'manual_discussion_only'}}};
      if(path===pmPath)return {status:200,data:detail()};
      return {status:200,data:{workspace:workspace(state.workspaces.find(w=>path.endsWith(w.workspace_id)))}};
    }
    if(input.method==='GET'&&path.startsWith('/operations/'))return {status:200,data:{operation:[...state.ops.values()].find(op=>path.endsWith(op.operation_id))}};
    if(input.method==='POST'&&path==='/operations'){
      state.writes.push(structuredClone(input));
      if(state.reject)return {status:state.reject.status,data:{error:{code:state.reject.code,message:'Rejected request',admission_refused:true}}};
      let op=state.ops.get(input.idempotency_key);
      if(!op){
        const {action,params,target}=input.body;let result:any={workspace_id:workspaceId,thread_id:threadId,version:state.thread.version+1,mode:state.thread.mode};
        if(action==='pm.workspace.create'){
          const created={...state.workspaces[0],workspace_id:'ws_'+'5'.repeat(32),name:params.name,repositories:params.repositories};state.workspaces.push(created);result={workspace:workspace(created)};
        }else if(action==='pm.thread.create'){state.thread.title=params.title;result={workspace_id:target.workspace_id,thread_id:threadId,version:1,mode:'idea'};
        }else if(action==='pm.message.append')state.messages.push({message_id:'pmm_'+String(state.messages.length+1).padStart(32,'0'),sequence:state.messages.length+1,actor:state.actor,content:params.content,created_at:stamp});
        else if(action==='pm.input.record')state.inputs.push({input_id:'pmi_'+String(state.inputs.length+1).padStart(32,'0'),...params,recorded_by:state.actor,confirmed_by:null,evidence_verification:params.source_ref.kind==='message'?'central_message':'unverified_claim'});
        else if(action==='pm.input.confirm')state.inputs=state.inputs.map(item=>item.input_id===params.input_id?{...item,status:'user_confirmed',confirmed_by:state.actor}:item);
        else if(action==='pm.thread.plan'){
          state.thread.mode='planning';state.thread.draft_id='pmd_'+'6'.repeat(32);
          state.draft={draft_id:state.thread.draft_id,status:'draft',title:state.thread.title,rationale:params.rationale,
            selected_inputs:state.inputs.filter(item=>params.selected_input_ids.includes(item.input_id)),candidate_inputs:state.inputs.filter(item=>!params.selected_input_ids.includes(item.input_id)),
            unknowns:[{slot:'finance',domain:'finance',status:'unresolved'}],execution_authorized:false,scope_revision:null};
        }else throw Error('Unexpected PM action '+action);
        state.thread.version++;
        op={...input.body,actor:state.actor,idempotency_key:input.idempotency_key,operation_id:'op_'+String(state.ops.size+1).padStart(32,'0'),status:'succeeded',result};
        state.ops.set(input.idempotency_key,op);
      }
      if(state.defer)await state.defer;
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status:200,data:{operation:state.mismatch?{...op,actor:'foreign'}:op}};
    }
    return base.dispatch(input);
  };
  return {state,dispatch,detail};
}
