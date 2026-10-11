import {policyFixture,policyRoute,workspaceId,policyProfile} from './pm-maintenance-policy-fixture.ts';
export {policyRoute,workspaceId};
export function checkFixture(){
  const parent=policyFixture(),settings=parent.documentPolicy();settings.enabled=true;parent.addPolicy(settings);
  const base=`/pm/workspaces/${workspaceId}`,now=Date.now()/1000;
  const state={available:true,reads:[] as string[],writes:[] as any[],ops:new Map<string,any>(),runs:[] as any[],hidden:false,fail:false,lost:false,mismatch:false,
    defer:null as null|Promise<void>,emptyPage:false,receiptReads:0,operationStatus:'succeeded',blocker:null as null|string,coalesced:0};
  function add(outcome:string|null='complete'){
    const sequence=state.runs.length+1,id=String(sequence+200).padStart(32,'0'),policy=parent.state.policies.at(-1)!,baseline=parent.release.state.baselines.at(-1)!;
    const row={sequence,run_id:'pmck_'+id,operation_id:'op_'+id,workspace_id:workspaceId,policy_id:policy.policy_id,policy_version:policy.revision,
      baseline_id:baseline.baseline_id,lifecycle_revision:parent.state.lifecycle.revision,execution_epoch:0,actor:parent.base.state.actor,
      created_at:now-30,deadline_at:now+90,finished_at:outcome?now-1:null,document_digest:outcome?'d'.repeat(64):null,
      operation_status:outcome?'succeeded':'waiting_external',capacity_reserved:!outcome,document:null as any};
    if(outcome)row.document={workspace_id:workspaceId,run_id:row.run_id,policy_id:row.policy_id,policy_version:row.policy_version,baseline_id:row.baseline_id,
      lifecycle_revision:row.lifecycle_revision,execution_epoch:0,outcome,reason:outcome==='incomplete'?'SOURCE_COVERAGE_INCOMPLETE':outcome==='stopped'?'PM_MAINTENANCE_DEADLINE':null,
      started_at:row.created_at,finished_at:row.finished_at,deadline_at:row.deadline_at,automatic_maintenance_authorized:false,repair_started:false,
      children:[{operation_id:'op_'+'b'.repeat(32),action:'pm.github.observe',status:'succeeded',result:{coverage_status:outcome==='incomplete'?'incomplete':'complete'}},
        {operation_id:'op_'+'c'.repeat(32),action:'pm.workload.capture',status:'succeeded',result:{classification:'unknown'}}]};
    state.runs.push(row);return row;
  }
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),path=url.pathname;
    if(path==='/capabilities'||path==='/bootstrap'){
      const result=await parent.dispatch(input),caps=path==='/bootstrap'?result.data.capabilities:result.data;
      if(state.available)caps.features.pm_workspaces.maintenance_checks={version:1,server_schedule:true};
      caps.actions.push({action:'pm.maintenance.check',allowed:parent.base.state.manage});return result;
    }
    if(path.startsWith(base+'/maintenance-checks')||path===base+'/maintenance-schedule'){
      state.reads.push(input.path);if(state.fail)return {status:403,data:{error:{code:'FORBIDDEN',message:'Check source withdrawn'}}};
      const common={workspace_id:state.mismatch?'ws_'+'f'.repeat(32):workspaceId,visibility_filtered:state.hidden},policy=parent.state.policies.at(-1)!,latest=state.runs.at(-1);
      let data;
      if(path.endsWith('/maintenance-schedule'))data={...common,schedule:state.hidden?null:{schedule_id:'pmms_'+'f'.repeat(32),policy_id:policy.policy_id,policy_version:policy.revision,
        next_due:now+1800,overdue:false,blocked_reason:state.blocker,last_success:state.runs.find(row=>row.document?.outcome==='complete')?.finished_at||null,
        missed_windows:'coalesce_latest',automatic_repairs:false,latest_window:latest?{operation_id:latest.operation_id,window:5,scheduled_at:now-30,
          admitted_at:now-25,coalesced_windows:state.coalesced,operation_status:latest.operation_status}:null}};
      else if(path.endsWith('/current'))data={...common,run:state.hidden?null:latest||null};
      else{const first=state.emptyPage&&url.searchParams.get('after')==='0';data={...common,runs:first||state.hidden?[]:state.runs,next_cursor:first?10:null,visibility_filtered:first||state.hidden};}
      data=structuredClone(data);if(state.defer)await state.defer;return {status:200,data};
    }
    if(path==='/operations'&&input.body?.action==='pm.maintenance.check'){
      state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
      if(!op){
        const p=input.body.preconditions,current=parent.state.policies.at(-1)!;
        if(p.expected_policy_version!==current.revision||p.expected_baseline_id!==parent.release.state.baselines.at(-1)?.baseline_id||p.expected_lifecycle_revision!==parent.state.lifecycle.revision)
          return {status:409,data:{error:{code:'VERSION_CONFLICT',message:'Check versions changed',admission_refused:true}}};
        const row=add(state.operationStatus==='succeeded'?'complete':null);op={...input.body,operation_id:row.operation_id,actor:parent.base.state.actor,
          idempotency_key:input.idempotency_key,status:state.operationStatus,result:{workspace_id:workspaceId,run_id:row.run_id}};state.ops.set(input.idempotency_key,op);
      }
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Check reply unavailable'}}};}return {status:200,data:{operation:op}};
    }
    if(path.startsWith('/operations/')){const op=[...state.ops.values()].find(op=>path.endsWith(op.operation_id));if(op){state.receiptReads++;return {status:200,data:{operation:op}};}}
    return parent.dispatch(input);
  };
  return {parent,state,add,dispatch,profile:policyProfile};
}
