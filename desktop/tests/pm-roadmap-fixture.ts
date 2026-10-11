import {pmFixture,workspaceId} from './pm-fixture.ts';
export const rid='pmrev_'+'a'.repeat(32),bid='pmrb_'+'b'.repeat(32),sid='pmsnap_'+'c'.repeat(32),path=`/dashboard/#/pm/${workspaceId}`;
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
