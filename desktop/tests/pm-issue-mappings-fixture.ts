import {pmFixture,workspaceId} from './pm-fixture.ts';
export const packageId='pmwp_'+'6'.repeat(32),workId='wi_'+'7'.repeat(32),mappingId='pmim_'+'8'.repeat(32),route=`/dashboard/#/pm/${workspaceId}`;
export function issueFixture(){
  const base=pmFixture(),packagePath=`/pm/workspaces/${workspaceId}/work-packages`,mappingPath=`/pm/workspaces/${workspaceId}/issue-mappings`;
  const document={package_id:packageId,workspace_id:workspaceId,work_item_id:workId,title:'Restore member access',execution_authorized:false,quality_assessment:'unassessed',
    context:{plan_ref:{revision_id:'pmrev_'+'9'.repeat(32),scope_revision:'pmscope_'+'a'.repeat(32)},work_ref:{binding_id:'pmwb_'+'b'.repeat(32),work_version:1}},
    requirements:[{id:'member.recovery',text:'Private requirement must never be copied automatically.',source_status:'user_confirmed',acceptance:[]}],
    design:{status:'described',text:'Private architecture detail.',source_ids:[]},rationale:'Private planning rationale.',
    tasks:[{key:'token',description:'Build token storage.',requirement_ids:['member.recovery'],depends_on:[]},{key:'form',description:'Build recovery form.',requirement_ids:['member.recovery'],depends_on:['token']}],verification:[],
    delivery:{repository:'example/library',base_ref:'refs/heads/main',goal:'pull_request',...Object.fromEntries(['migration','rollout','rollback','observation'].map(key=>[key,{status:'unresolved',text:'Unknown'}]))},
    dod:null,unresolved:['Budget is not decided.'],code_source:{repository:'example/library',repository_id:7,source_sha:'c'.repeat(40)},sources:{documents:[]}};
  const initial={sequence:1,created_at:1791720000,actor:'reader',package_id:packageId,workspace_id:workspaceId,work_item_id:workId,previous_package_id:null,document_digest:'d'.repeat(64),operation_id:'op_'+'e'.repeat(32),document};
  const state={packages:[initial] as any[],revisions:[] as any[],writes:[] as any[],reads:[] as any[],ops:new Map<string,any>(),lost:false,stage:'development',
    hidden:false,denied:false,packageDenied:false,partial:false,filtered:false,gate:null as Promise<void>|null,writeGate:null as Promise<void>|null};
  const gated=['pm.issue.publish','pm.issue.cancel'],variants={'pm.issue.cancel':{absent_params:['public_reason']}};
  const current=()=>state.revisions.at(-1);
  function revision(selected:any,pkg=state.packages[0],cancel=false,oid='op_'+String(state.revisions.length+1).padStart(32,'0')){
    const rid='pmir_'+String(state.revisions.length+1).padStart(32,'0'),old=current();
    const row={sequence:state.revisions.length+1,revision_id:rid,mapping_id:mappingId,workspace_id:workspaceId,work_item_id:workId,previous_revision_id:old?.revision_id||null,
      created_at:1791720000+state.revisions.length,actor:base.state.actor,document_digest:String(state.revisions.length+1).repeat(64).slice(0,64),operation_id:oid,
      document:{revision_id:rid,mapping_id:mappingId,workspace_id:workspaceId,work_item_id:workId,operation_id:oid,
        relation:{work_item_id:workId,binding_id:'pmwb_'+'b'.repeat(32),requirement_ids:['member.recovery'],repository_id:7,repository:'example/library'},
        package_ref:{package_id:pkg.package_id,document_digest:pkg.document_digest},selected:structuredClone(selected),
        issue:{number:41,id:73,repository:'example/library'},kind:cancel?'cancel':old?'revision':'create',state:cancel?'cancelled':'tracking',
        execution_authorized:false,counts_as_done:false,external_issue_state:'not_changed',external_content_policy:'create_or_append_only'}};
    state.revisions.push(row);return row;
  }
  function addPackage(){const row=structuredClone(state.packages[0]);row.sequence=state.packages.length+1;row.previous_package_id=state.packages.at(-1).package_id;
    row.package_id='pmwp_'+String(row.sequence).padStart(32,'0');row.document.package_id=row.package_id;row.document_digest='f'.repeat(64);state.packages.push(row);return row;}
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),path=url.pathname;
    if(path==='/capabilities'||path==='/bootstrap'){
      const reply=await base.dispatch(input),caps=path==='/bootstrap'?reply.data.capabilities:reply.data;
      Object.assign(caps.features.pm_workspaces,{issue_mapping:true,work_package_prepare:true,lifecycle:{version:1,gated_actions:gated,record_actions:[],record_variants:variants}});
      caps.actions.push(...gated.map(action=>({action,allowed:base.state.manage})));return reply;
    }
    if(path==='/events'){const reply=await base.dispatch(input);for(const event of reply.data.events)event.resource_type='pm_issue_mapping';return reply;}
    if(path===`/pm/workspaces/${workspaceId}/lifecycle`)return {status:200,data:{workspace_id:workspaceId,lifecycle:{stage:state.stage,status:state.stage==='development'?'active':'paused',revision:0,execution_epoch:0,transition_id:null,coverage:'recorded'},transition:null,
      blocked_actions:state.stage==='development'?[]:gated,record_actions:[],record_variants:variants,available_stages:['idea','planning','development'],maintenance:{available:false,reason:'release_baseline_and_policy_required'},inflight_summary:{visible_unresolved:0,visible_retained:0,visibility_filtered:false}}};
    if(path===`/pm/workspaces/${workspaceId}/lifecycle/inflight`)return {status:200,data:{workspace_id:workspaceId,transition_id:null,inflight:[],next_cursor:null,visibility_filtered:false}};
    if(path.startsWith(packagePath)){
      state.reads.push(structuredClone(input));if(state.packageDenied)return {status:404,data:{error:{code:'PM_PACKAGE_SOURCE_UNAVAILABLE',message:'Package source unavailable'}}};
      if(path===packagePath)return {status:200,data:{version:1,workspace_id:workspaceId,packages:structuredClone(state.packages),next_cursor:null,coverage:{visibility_filtered:false}}};
      const row=state.packages.find(row=>path.split('/').includes(row.package_id));
      if(path.endsWith('/readiness'))return {status:200,data:{version:1,workspace_id:workspaceId,package_id:row.package_id,document_digest:row.document_digest,status:'ready',ready_for:'engineering_handoff_review',execution_available:false,execution_authorized:false,dispatch_readiness:'not_implemented',checks:[]}};
      return {status:200,data:{version:1,workspace_id:workspaceId,package:structuredClone(row)}};
    }
    if(path.startsWith(mappingPath)){
      state.reads.push(structuredClone(input));
      if(state.denied)return {status:403,data:{error:{code:'FORBIDDEN',message:'Mapping source withdrawn'}}};
      const after=Number(url.searchParams.get('after'));
      const data=path===mappingPath?{version:1,workspace_id:workspaceId,mappings:state.hidden||state.partial&&after===0?[]:current()?[structuredClone(current())]:[],next_cursor:state.partial&&after===0?1:null,coverage:{visibility_filtered:state.hidden||state.filtered,byte_limited:state.partial,has_more:state.partial&&after===0}}
        :{version:1,workspace_id:workspaceId,mapping_id:mappingId,current:structuredClone(current()),revisions:structuredClone(state.revisions),execution_authorized:false};
      if(state.gate)await state.gate;
      return {status:200,data};
    }
    if(path.startsWith('/operations/')&&input.method==='GET'){
      const op=[...state.ops.values()].find(op=>path.endsWith(op.operation_id));
      if(op)return state.denied?{status:403,data:{error:{code:'FORBIDDEN',message:'Original operation source withdrawn'}}}:{status:200,data:{operation:structuredClone(op)}};
    }
    if(path==='/operations'&&input.method==='POST'&&gated.includes(input.body.action)){
      state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
      if(!op){
        const {action,params,preconditions}=input.body;
        if(preconditions.expected_mapping_revision!==(current()?.revision_id||null))return {status:409,data:{error:{code:'PM_ISSUE_VERSION_CONFLICT',message:'Mapping changed',admission_refused:true}}};
        if(state.stage!=='development'&&(action==='pm.issue.publish'||Object.hasOwn(params,'public_reason')))return {status:409,data:{error:{code:'PM_LIFECYCLE_STOPPED',message:'New investment stopped',admission_refused:true}}};
        const selected=action==='pm.issue.publish'?{public_title:params.public_title,public_body:params.public_body}:params;
        const oid='op_'+String(state.ops.size+1).padStart(32,'0'),row=revision(selected,action==='pm.issue.publish'?state.packages.find(row=>row.package_id===params.package_id):state.packages[0],action==='pm.issue.cancel',oid);
        op={...input.body,actor:base.state.actor,idempotency_key:input.idempotency_key,operation_id:oid,status:'succeeded',result:{workspace_id:workspaceId,mapping_id:mappingId,revision_id:row.revision_id,work_item_id:workId,state:row.document.state,execution_authorized:false}};
        state.ops.set(input.idempotency_key,op);
      }
      if(state.writeGate)await state.writeGate;
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status:200,data:{operation:structuredClone(op)}};
    }
    return base.dispatch(input);
  };
  return {base,state,dispatch,revision,addPackage,current};
}
