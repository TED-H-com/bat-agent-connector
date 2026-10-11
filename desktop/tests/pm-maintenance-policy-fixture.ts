import {releaseFixture,releasePath,workspaceId,repositoryName} from './pm-release-baselines-fixture.ts';
export {workspaceId};
export const policyProfile='github_open_prs_and_actions_v1';
export const policyRoute=`/dashboard/#/pm/${workspaceId}`;
const stamp=Date.parse('2026-10-11T12:00:00Z')/1000;
export function policyFixture(){
  const release=releaseFixture();release.addBaseline();
  const base=release.base;
  const state={enabled:true,profiles:true,adoption:true,hidden:false,fail:null as null|number,rejectWrite:false,lost:false,emptyHistoryPage:false,
    mismatch:false,deferCurrent:null as null|Promise<void>,deferHistory:null as null|Promise<void>,deferWrite:null as null|Promise<void>,
    lifecycle:{stage:'development',status:'active',revision:0},policies:[] as any[],reads:[] as string[],writes:[] as any[],ops:new Map<string,any>(),
    operationStatus:'succeeded',receiptReads:0};
  const documentPolicy=()=>({enabled:false,after_release_policy:'manual',sources:['issues','pull_requests'],allowed_work_kinds:[],allowed_paths:['.'],
    delivery:{maximum:'report',environment:null},verification:{required_ci:true,review_required:true,acceptance_evidence:['Verify the affected release in an owned workspace']},
    schedule:{timezone:'UTC',check_interval_seconds:3600,review_weekday:0,review_local_time:'09:00'},
    observation:{max_age_seconds:1800,stable_for_seconds:300,github_profile:policyProfile},
    limits:{max_parallel:1,run_seconds:1800,period_seconds:86400,period_runs:4,cost_microusd:null,retries:0}});
  function addPolicy(policy:any=documentPolicy(),rationale='Keep the supported release observable'){
    const revision=state.policies.length+1,policy_id='pmmp_'+String(revision).padStart(32,'0'),baseline_id=release.state.baselines.at(-1)!.baseline_id;
    const row={workspace_id:workspaceId,policy_id,revision,sequence:revision,baseline_id,document_digest:'e'.repeat(64),actor:base.state.actor,created_at:stamp,
      operation_id:'op_'+String(revision+100).padStart(32,'0'),document:{workspace_id:workspaceId,policy_id,revision,policy,rationale,
        lifecycle_revision:state.lifecycle.revision,automatic_execution_started:false,scope:{repository:repositoryName,channel:'stable',
          github_scope:policy.observation.github_profile||'repository'}}};
    state.policies.push(row);return row;
  }
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),path=url.pathname;
    if(path==='/capabilities'||path==='/bootstrap'){
      const response=await release.dispatch(input),caps=path==='/bootstrap'?response.data.capabilities:response.data;
      if(state.enabled)caps.features.pm_workspaces.maintenance_policy={version:1,policy_adoption:state.adoption,scheduler_available:false,repair_dispatch_available:false};
      if(state.profiles)caps.features.pm_github_observation={version:1,profiles:[policyProfile],read_only:true,automatic_maintenance:false};
      caps.actions.push({action:'pm.maintenance_policy.adopt',allowed:base.state.manage});return response;
    }
    if(path===releasePath+'/lifecycle')return {status:200,data:{workspace_id:workspaceId,lifecycle:structuredClone(state.lifecycle)}};
    if(path.startsWith(releasePath+'/maintenance-policies')){
      state.reads.push(input.path);
      if(state.fail)return {status:state.fail,data:{error:{code:'FORBIDDEN',message:'Policy source unavailable'}}};
      if(path.endsWith('/current')){
        const data=structuredClone({workspace_id:state.mismatch?'ws_'+'f'.repeat(32):workspaceId,
          head:{policy_id:state.hidden?null:state.policies.at(-1)?.policy_id||null,revision:state.policies.length},
          policy:state.hidden?null:state.policies.at(-1)||null,visibility_filtered:state.hidden});
        if(state.deferCurrent)await state.deferCurrent;return {status:200,data};
      }
      const first=state.emptyHistoryPage&&url.searchParams.get('after')==='0';
      const data=structuredClone({workspace_id:workspaceId,policies:first?[]:state.policies,next_cursor:first?10:null,visibility_filtered:first});
      if(state.deferHistory)await state.deferHistory;return {status:200,data};
    }
    if(path==='/operations'&&input.method==='POST'&&input.body.action==='pm.maintenance_policy.adopt'){
      state.writes.push(structuredClone(input));
      if(state.rejectWrite)return {status:403,data:{error:{code:'FORBIDDEN',message:'Policy source withdrawn',admission_refused:true}}};
      let op=state.ops.get(input.idempotency_key);
      if(!op){
        const p=input.body.preconditions;
        if(p.expected_policy_revision!==state.policies.length||p.expected_baseline_id!==release.state.baselines.at(-1)?.baseline_id||p.expected_lifecycle_revision!==state.lifecycle.revision)
          return {status:409,data:{error:{code:'VERSION_CONFLICT',message:'Decision versions changed',admission_refused:true}}};
        const row=addPolicy(input.body.params.policy,input.body.params.rationale);
        op={...input.body,actor:base.state.actor,idempotency_key:input.idempotency_key,operation_id:row.operation_id,status:state.operationStatus,
          result:{workspace_id:workspaceId,policy_id:row.policy_id,revision:row.revision,automatic_execution_started:false}};state.ops.set(input.idempotency_key,op);
      }
      if(state.deferWrite)await state.deferWrite;
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status:200,data:{operation:op}};
    }
    if(path.startsWith('/operations/')&&state.ops.size){
      const op=[...state.ops.values()].find(op=>path.endsWith(op.operation_id));
      if(op){state.receiptReads++;return {status:200,data:{operation:op}};}
    }
    return release.dispatch(input);
  };
  return {state,release,base,dispatch,addPolicy,documentPolicy};
}
