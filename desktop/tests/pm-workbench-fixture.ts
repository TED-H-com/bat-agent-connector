import {pmFixture, workspaceId} from './pm-fixture.ts';
import {fixture as kanbanFixture, workId} from './pm-kanban-fixture.ts';
export {workspaceId, workId};
export const route = `/dashboard/#/pm/${workspaceId}`;
export const revisionId = 'pmrev_' + '4'.repeat(32);
export function workbenchFixture() {
  const base = pmFixture(), board = kanbanFixture(); base.state.plans = true;
  const statement = (value:string, id:string, status='user_confirmed') => ({input_id:id, value, status, source_ref:{kind:'statement',ref:'Planning discussion'}});
  const document = {title:'Membership release plan', markdown:'# Membership release plan\n\nA member can regain access without contacting support.',
    sections:Object.fromEntries(Object.entries({governance:'Members can reliably regain account access.', scope:'Login and password recovery; billing is outside this release.',
      schedule:'Recovery review is planned for November 1.', resources:'Use the adopted engineering policy.', finance:'Budget remains to be confirmed.',
      stakeholders:'Product owner reviews scope changes.', risk:'Recovery messages may arrive late.', quality:'Prove the original user can recover the account.'})
      .map(([key,value])=>[key,{statements:[statement(value,'input-'+key,key==='finance'?'unresolved':'user_confirmed')]}])),
    requirements:[{id:'membership.login',text:'Members can sign in',acceptance:[{source_input_id:'login-criterion',text:'Reject an incorrect password.'}]},
      {id:'membership.recovery',text:'Members can recover access',acceptance:[{source_input_id:'recovery-criterion',text:'A recovery token can be used only once.'}]}]};
  const plan:any = {workspace_id:workspaceId,revision_id:revisionId,document_digest:'4'.repeat(64),sequence:1,document,
    adoption:{adopted_by:'product-owner',created_at:1791705600,rationale:'Keep a small, verifiable release.',commit_sha:'a'.repeat(40),scope_revision:'pmscope_'+'5'.repeat(32)}};
  base.state.revisions=[plan];base.state.planHead={workspace_id:workspaceId,version:2,latest_revision_id:revisionId,accepted_revision_id:revisionId};
  board.state.cards[0].binding.requirement_ids=['membership.login','membership.recovery'];
  board.state.cards[1].binding.requirement_ids=['membership.recovery'];
  board.state.cards[2].binding.plan_revision_id='pmrev_'+'9'.repeat(32);
  const snapshot = (sequence:number) => ({snapshot_id:'pmsnap_'+String(sequence).repeat(32),workspace_id:workspaceId,sequence,
    title:sequence===1?'Scope agreed':'Recovery progress',as_of:1791705600+sequence*86400,
    baseline:{status:'accepted',document_revision:revisionId,document:structuredClone(document),adoption:plan.adoption},
    evidence:[],coverage:{cost:'unknown'},visibility:{restricted:false},snapshot_digest:'a'.repeat(64),view_digest:'b'.repeat(64),
    brief:{delivery:{items:sequence===1?[]:[{kind:'pr_merge',text:'Login PR merge recorded; recovery still needs verification.',source:{kind:'evidence',evidence_id:'pme_'+'1'.repeat(32)}}]},
      blockers:{items:[]},decisions:{items:[{kind:'recorded_open_input',text:'Confirm the recovery support budget.',status:'unresolved',source:{kind:'plan_input',input_id:'input-finance'}}]}}});
  const mapping = (n:number, id=workId(1), binding='pmwb_'+'1'.repeat(32)) => {
    const mappingId='pmim_'+String(n).repeat(32),rid='pmir_'+String(n).repeat(32);
    return {workspace_id:workspaceId,mapping_id:mappingId,revision_id:rid,sequence:n,document_digest:'d'.repeat(64),work_item_id:id,
      document:{workspace_id:workspaceId,mapping_id:mappingId,revision_id:rid,work_item_id:id,state:'tracking',execution_authorized:false,counts_as_done:false,
        relation:{work_item_id:id,binding_id:binding,repository_id:7,requirement_ids:['membership.login','membership.recovery']},
        selected:{public_title:n===1?'Account recovery delivery':'Unassigned export request'},issue:{repository:'example/library',number:40+n}}};
  };
  const receipts = [1,2,3].map(n=>({operation_id:'op_'+String(n).repeat(32),action:n===3?'delivery.merge_and_deploy':'github.pr.merge',status:'succeeded',
    requirement_acceptance:'not_evaluated_by_delivery',target:{provider_origin:'https://api.github.com',repository:'example/library',repository_id:7,pull_number:n===1?10:11},
    merge:{merged:true,verified:true,merged_sha:String(n).repeat(40)},...(n===3?{deployment:{environment:'preview',verified:true,is_current:false,source_sha:'3'.repeat(40)}}:{})}));
  const state = {snapshots:[snapshot(1),snapshot(2)],issues:[mapping(1),mapping(2,workId(9),'pmwb_'+'9'.repeat(32))],receipts,
    denySnapshot:false,denyWork:false,denyIssues:false,lost:false,writes:[] as any[],ops:new Map<string,any>(),heldDetail:null as null|Promise<void>,
    issuePageSize:20,deliveryPageSize:20,reads:[] as string[]};
  async function dispatch(input:any):Promise<any> {
    const url=new URL(input.path,'http://fixture'),path=url.pathname,endpoint=`/pm/workspaces/${workspaceId}`;
    if(input.method==='GET')state.reads.push(input.path);
    if(path==='/bootstrap'||path==='/capabilities'){
      const result=await base.dispatch(input),caps=path==='/bootstrap'?result.data.capabilities:result.data;
      Object.assign(caps.features.pm_workspaces,{brief_snapshots:true,kanban:true,issue_mapping:true,engineering_delivery:{contract:'pm-engineering-delivery/v1'}});
      caps.actions.push({action:'pm.snapshot.create',allowed:base.state.manage});return result;
    }
    if(path===endpoint+'/plans/head')return {status:200,data:{head:structuredClone(base.state.planHead)}};
    if(path===endpoint+'/snapshots')return {status:200,data:{workspace_id:workspaceId,snapshots:state.snapshots.map(({baseline,brief,...row})=>row),next_cursor:null}};
    if(path.startsWith(endpoint+'/snapshots/')){
      if(state.denySnapshot)return {status:403,data:{error:{code:'FORBIDDEN',message:'Summary sources withdrawn'}}};
      return {status:200,data:{workspace_id:workspaceId,snapshot:structuredClone(path.endsWith('/latest')?state.snapshots.at(-1)||null:state.snapshots.find(row=>path.endsWith(row.snapshot_id))||null)}};
    }
    if(path.startsWith(endpoint+'/kanban')){
      if(state.denyWork)return {status:403,data:{error:{code:'FORBIDDEN',message:'Work sources withdrawn'}}};
      const data=await board.dispatch(input);if(path!==endpoint+'/kanban'&&state.heldDetail)await state.heldDetail;return data;
    }
    if(path===endpoint+'/issue-mappings'){
      if(state.denyIssues)return {status:403,data:{error:{code:'FORBIDDEN',message:'Issue sources withdrawn'}}};
      const after=Number(url.searchParams.get('after')||0),rows=state.issues.filter(row=>row.sequence>after).slice(0,state.issuePageSize);
      return {status:200,data:{version:1,workspace_id:workspaceId,mappings:structuredClone(rows),next_cursor:state.issues.some(row=>row.sequence>(rows.at(-1)?.sequence||after))?rows.at(-1)?.sequence:null,coverage:{}}};
    }
    if(path.endsWith('/deliveries')){
      const after=Number(url.searchParams.get('after')||0),rows=state.receipts.slice(after,after+state.deliveryPageSize),next=after+rows.length;
      return {status:200,data:{workspace_id:workspaceId,work_item_id:path.split('/').at(-2),contract:'pm-engineering-delivery/v1',original_packages:[],
        deliveries:structuredClone(rows),next_after:next<state.receipts.length?next:null,requirement_acceptance:'not_evaluated_by_delivery',source_visibility_filtered:false}};
    }
    if(path==='/operations'&&input.method==='POST'&&input.body.action==='pm.snapshot.create'){
      state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
      if(!op){const row=snapshot(state.snapshots.length+1);state.snapshots.push(row);op={...input.body,actor:base.state.actor,idempotency_key:input.idempotency_key,
        operation_id:'op_'+'c'.repeat(32),status:'succeeded',result:{snapshot_id:row.snapshot_id}};state.ops.set(input.idempotency_key,op);}
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}return {status:200,data:{operation:op}};
    }
    if(path.startsWith('/operations/')&&input.method==='GET'){
      const op=[...state.ops.values()].find(row=>path.endsWith(row.operation_id));if(op)return {status:200,data:{operation:op}};
    }
    return base.dispatch(input);
  }
  return {base,board,state,plan,dispatch,snapshot};
}
