// Opt-in exact-read responses. Existing fixture defaults are deliberately untouched.
import {qualityFixture, workspaceId as qualityWorkspace, workId as qualityWork, packageId, childId, candidate, RECORD} from './pm-quality-reviews-fixture.ts';
import {buildResultEnvelope} from './work-result-fixture.ts';
export function assessmentRoundtrip(base:any, workspaceId:string, workId:string) {
  const q=qualityFixture(), row=q.save(true), attemptId='att_'+'1'.repeat(32);
  const map=(value:any,reverse=false):any=>{
    if(Array.isArray(value))return value.map(item=>map(item,reverse));
    if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,map(item,reverse)]));
    if(typeof value==='string')return reverse?value.replaceAll(workspaceId,qualityWorkspace).replaceAll(workId,qualityWork):value.replaceAll(qualityWorkspace,workspaceId).replaceAll(qualityWork,workId);
    return value;
  };
  const reference=()=>map({review_id:row.review_id,...Object.fromEntries(['operation_id','version','document_digest'].map(key=>[key,row.assessment[key]])),candidate_commit:candidate});
  const saved=new Map<string,any>();
  const snapshot=()=>{row.assessment.document.requirements=[{requirement_id:'recovery',status:row.assessment.document.acceptance}];const ref=reference();saved.set(ref.operation_id,map({workspace_id:qualityWorkspace,review_id:row.review_id,context_digest:row.context_digest,
    context:row.context,assessment:row.assessment,semantics:'immutable_original_assessment',current_applicability:'not_evaluated'}));return ref;};
  const ref=snapshot();
  const attempt={attempt_id:attemptId,workspace_id:workspaceId,work_item_id:workId,package_id:packageId,execution_operation_id:childId,
    start_operation_id:'op_'+'1'.repeat(32),stage:'started',status:'succeeded',session:{host:'fixture-host',session_id:'session-123'}};
  const envelope=buildResultEnvelope({workspace_id:workspaceId,attempt_id:attemptId,
    identity:{workspace_id:workspaceId,work_item_id:workId,attempt_id:attemptId,package_id:packageId,execution_operation_id:childId},
    sources:{result_id:'res_'+'1'.repeat(32),digest:'sha256:'+'d'.repeat(64)},fixed_commit:{commit_sha:candidate,source_kind:'turn_end_observation'},
    requirements:{relation:'explicit',package_ref:map(q.package_ref),candidate_commit:candidate,verification:'verified',coverage:{reviews_partial:false,packages_partial:false},
      items:[{requirement_id:'recovery',text:'Recover original ownership',state:'assessments_recorded',assessments:[{reference:ref,original:structuredClone(row.assessment.document),
        conclusion:'accepted',assessed_by:'reviewer',applicable:true,qualified:true,applicability_reasons:[]}]}],dependent_work:[]},
    next_allowed_actions:[{kind:'action',action:RECORD,target:{workspace_id:workspaceId,review_id:row.review_id}}]});
  const state={enabled:true,denied:false,hold:null as Promise<void>|null,requests:[] as any[],envelope,attempt,saved};
  const dispatch=async(input:any)=>{
    state.requests.push(structuredClone(input));const path=new URL(input.path,'http://fixture').pathname;
    if(['/bootstrap','/capabilities'].includes(path)){
      const response=await base.dispatch(input),caps=path==='/bootstrap'?response.data.capabilities:response.data;
      const quality=(await q.dispatch({method:'GET',path:'/capabilities'})).data;
      caps.actor=quality.actor;caps.scopes=quality.scopes;
      caps.actions=[...caps.actions.filter((value:any)=>!quality.actions.some((q:any)=>q.action===value.action)),...quality.actions];
      caps.features.pm_workspaces.quality_reviews={...quality.features.pm_workspaces.quality_reviews,exact_assessment_read:state.enabled};
      return response;
    }
    if(path.includes('/assessments/')){
      const value=structuredClone(saved.get(path.split('/').at(-1)!)),denied=state.denied,hold=state.hold;
      if(hold)await hold;
      const params=new URL(input.path,'http://fixture').searchParams;
      return !denied&&value&&String(value.assessment.version)===params.get('version')&&value.assessment.document_digest===params.get('document_digest')
        ?{status:200,data:value}:{status:404,data:{error:{code:'NOT_FOUND',message:'Original assessment unavailable'}}};
    }
    if(path.endsWith('/work-attempts/'+attemptId))return {status:200,data:{workspace_id:workspaceId,attempt:structuredClone(state.attempt)}};
    if(path.endsWith('/work/'+workId+'/attempts'))return {status:200,data:{workspace_id:workspaceId,work_item_id:workId,attempts:[structuredClone(state.attempt)],next_cursor:null}};
    if(path.endsWith('/work-attempts/'+attemptId+'/result'))return {status:200,data:structuredClone(state.envelope)};
    if(path==='/work-items/'+workId)return {status:200,data:{work_item:{work_item_id:workId,title:'Selected original work'}}};
    if(path==='/pm/workspaces/'+workspaceId||path==='/operations'&&input.body?.action===RECORD
      ||path.startsWith('/operations/')||path==='/operations'&&new URL(input.path,'http://fixture').searchParams.has('action')
      ||/\/(work-packages|quality|sprint-artifacts\/dod|evidence(?:\/[^/]+)?|deliveries|github-observations)$/.test(path)) {
      const response=map(await q.dispatch(map(input,true)));
      if(input.method==='POST'&&input.body?.action===RECORD){
        // Match the production operation receipt identity, including immutable history.
        const op=response.data?.operation||map(q.state.ops.get(input.idempotency_key));
        if(op){row.assessment.operation_id=op.operation_id;row.history.at(-1).operation_id=op.operation_id;
          Object.assign(op.result,{workspace_id:workspaceId,kind:'assessment',document_digest:row.assessment.document_digest});
          const stored=q.state.ops.get(input.idempotency_key);if(stored)stored.result=map(op.result,true);snapshot();}
      }
      return response;
    }
    return base.dispatch(input);
  };
  return {state,q,row,ref,dispatch,snapshot,newer:()=>{q.assess(row);return snapshot();}};
}
