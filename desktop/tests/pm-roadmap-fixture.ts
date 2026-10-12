import {pmFixture,workspaceId} from './pm-fixture.ts';
export const rid='pmrev_'+'a'.repeat(32),bid='pmrb_'+'b'.repeat(32),sid='pmsnap_'+'c'.repeat(32),path=`/dashboard/#/pm/${workspaceId}`;
export const qualityRef={review_id:'pmqr_'+'4'.repeat(32),operation_id:'op_'+'5'.repeat(32),version:2,document_digest:'6'.repeat(64),candidate_commit:'7'.repeat(40)};
export function qualityObservation(completion='qualified',complete=true){
  const accepted=completion==='qualified'||!complete,state=accepted?'accepted':'rejected',qualified=completion==='qualified';
  const context={profile:'fixed_milestone_assessments_v1',plan_revision_id:rid,plan_document_digest:'8'.repeat(64),scope_revision:3,requirement_ids:['login']};
  return {revision_id:'pmro_'+'9'.repeat(32),document_digest:'a'.repeat(64),view_digest:'b'.repeat(64),document:{baseline_revision_id:bid,snapshot_id:sid,
    milestones:[{milestone_id:'first_release',quality_context:context,coverage:{inaccessible_sources:false,scope_assessment:'unassessed'},
      actual:{completion,dod:qualified?'accepted':complete?'rejected':'unassessed',actual_finish_date:null,facts:[],quality:{profile:context.profile,plan_revision_id:rid,plan_document_digest:context.plan_document_digest,scope_revision:3,
        requirements:[{requirement_id:'login',acceptance:state,dod:state,qualified,contributions:[{work_item_id:'wi_'+'1'.repeat(20),acceptance:state,dod:state}]}],
        total_requirements:1,qualified_requirements:qualified?1:0,qualified_deliverables:accepted?1:0,contribution_coverage_complete:complete,
        basis:'explicit_fixed_assessments_all_selected_work_contributions',current_applicability:'not_evaluated',current_environment:'not_assessed',
        assessments:[{evidence_id:'pmev_'+'2'.repeat(32),reference:qualityRef,work_item_id:'wi_'+'1'.repeat(20),candidate_commit:qualityRef.candidate_commit,dod_revision_id:'pmdod_'+'3'.repeat(32)}]}}}]}};
}
export function fixture(existing=true){
  const base=pmFixture(),milestone={milestone_id:'first_release',title:'First useful release',plan_revision_id:rid,requirement_ids:['login'],work_binding_ids:[],baseline_date:'2026-11-01',depends_on:[]};
  const state={lost:false,writes:[] as any[],ops:new Map<string,any>(),version:existing?1:0,baseline:existing?{revision_id:bid,document:{title:'Next release',rationale:'One usable outcome',milestones:[milestone]}}:null as any,forecast:null as any,observation:null as any,bindings:[] as any[]};
  const dispatch=async(input:any)=>{
    const endpoint=`/pm/workspaces/${workspaceId}`,route=new URL(input.path,'http://fixture').pathname;
    if(route==='/bootstrap'||route==='/capabilities'){
      const response=await base.dispatch(input),caps=route==='/bootstrap'?response.data.capabilities:response.data;caps.features.pm_workspaces.roadmap=true;
      caps.actions.push(...['pm.roadmap.baseline.set','pm.roadmap.forecast.set','pm.roadmap.observe'].map(action=>({action,allowed:base.state.manage})));return response;
    }
    if(route===endpoint+'/plans/accepted')return {status:200,data:{head:{workspace_id:workspaceId,version:2,accepted_revision_id:rid},plan:{workspace_id:workspaceId,revision_id:rid,document:{title:'Accepted release',requirements:[{id:'login',text:'Members can sign in'}]}}}};
    if(route===endpoint+'/work')return {status:200,data:{workspace_id:workspaceId,bindings:state.bindings,next_cursor:null}};
    if(route==='/work-items')return {status:200,data:{work_items:[{work_item_id:'wi_'+'1'.repeat(20),title:'Login implementation'}]}};
    if(route===endpoint+'/snapshots')return {status:200,data:{workspace_id:workspaceId,snapshots:[{snapshot_id:sid,title:'Saved review'}],next_cursor:null}};
    if(route===endpoint+'/roadmap')return {status:200,data:{workspace_id:workspaceId,head:{workspace_id:workspaceId,version:state.version,baseline_revision_id:state.baseline?.revision_id||null},baseline:structuredClone(state.baseline),forecast:structuredClone(state.forecast),observation:structuredClone(state.observation)}};
    if(route===endpoint+'/roadmap/revisions')return {status:200,data:{workspace_id:workspaceId,revisions:state.observation?[{revision_id:state.observation.revision_id,kind:'observation',created_at:1791705700}]:[],next_cursor:null}};
    if(state.observation&&route===endpoint+'/roadmap/revisions/'+state.observation.revision_id)return {status:200,data:{workspace_id:workspaceId,revision:structuredClone(state.observation)}};
    if(input.method==='GET'&&route.startsWith('/operations/'))return {status:200,data:{operation:[...state.ops.values()].find(op=>route.endsWith(op.operation_id))}};
    if(input.method==='POST'&&route==='/operations'&&input.body.action.startsWith('pm.roadmap.')){
      state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
      if(!op){
        if(input.body.action==='pm.roadmap.baseline.set')state.baseline={revision_id:bid,document:structuredClone(input.body.params)};
        if(input.body.action==='pm.roadmap.forecast.set')state.forecast={document:{milestones:structuredClone(input.body.params.updates)}};
        if(input.body.action==='pm.roadmap.observe')state.observation={document:{milestones:[{milestone_id:'first_release',actual:{completion:'unassessed',dod:'unassessed',actual_finish_date:null,facts:[]}}]}};
        state.version++;op={...input.body,actor:base.state.actor,idempotency_key:input.idempotency_key,operation_id:'op_'+'d'.repeat(32),status:'succeeded',result:{revision_id:bid}};state.ops.set(input.idempotency_key,op);
      }
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}return {status:200,data:{operation:op}};
    }return base.dispatch(input);
  };return{base,state,dispatch};
}
