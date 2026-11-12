// Opt-in composition: no default behavior of the shared fixture producers changes.
import {meetingFixture, workspaceId, snapshotId} from './pm-meetings-fixture.ts';
import {fixture as sprintFixture} from './pm-sprint-fixture.ts';
import {governanceFixture} from './pm-governance-fixture.ts';
import {pmFixture, threadId} from './pm-fixture.ts';
export {workspaceId,threadId};
export function guidedReviewFixture() {
  const meeting=meetingFixture('review'),sprint=sprintFixture(),governance=governanceFixture(),base=pmFixture();
  base.state.inputs=base.state.inputs.map((row:any,index:number)=>({...row,operation_id:'op_'+(index+50).toString(16).padStart(32,'0')}));
  const state:any={actor:'owner',role:'owner',enabled:true,requests:[],withdraw:false,lost:false,defer:null,providerCalls:0};
  const detail=meeting.state.meetings[0],context=detail.context.document;
  Object.assign(sprint.state.sprints[0],meeting.state.sprints[0],{definition:{...sprint.state.sprints[0].definition,...meeting.state.sprints[0].definition},selected:[meeting.binding]});
  Object.assign(detail.snapshot,structuredClone(governance.state.snapshots[0]),{snapshot_id:snapshotId});
  context.snapshot_ref.snapshot_digest=detail.snapshot.snapshot_digest;
  governance.state.snapshots=[detail.snapshot];governance.state.records=[];governance.state.history={};
  const note={meeting_id:detail.meeting.meeting_id,context_revision_id:detail.context.context_revision_id,note_id:'pmmnote_'+'9'.repeat(32),sequence:1,content:'SOURCE_FEEDBACK: 使用者需要更清楚的確認步驟。',source_refs:[],actor:'stakeholder'};
  detail.notes.push(note);
  const resultRef={attempt_id:'att_'+'a'.repeat(32),result_id:'res_'+'b'.repeat(32),digest:'sha256:'+'c'.repeat(64),assessment_ref:null};
  const result={workspace_id:workspaceId,attempt_id:resultRef.attempt_id,sources:{result_id:resultRef.result_id,digest:resultRef.digest},
    conversation:{latest_assistant_summary:{text:'SOURCE_RESULT: Reset behavior reported complete.'}},requirements:{items:[]}};
  const response=(data:any,status=200)=>({data:structuredClone(data),status});
  const denied=()=>response({error:{code:'PM_MEETING_SOURCE_UNAVAILABLE',message:'Original source withdrawn'}},404);
  async function dispatch(input:any):Promise<any>{
    state.requests.push(structuredClone(input));const path=new URL(input.path,'http://fixture').pathname,prefix='/pm/workspaces/'+workspaceId,action=input.body?.action;
    if(state.withdrawOnSubmit&&input.method==='POST'){state.withdraw=true;return denied();}
    for(const fixture of [meeting,sprint,governance,base])Object.assign(fixture.state,{actor:state.actor,role:state.role});
    if(/provider|model|run.prepare|session.send/.test(path+' '+(action||'')))state.providerCalls++;
    if(path==='/bootstrap'||path==='/capabilities'){
      const result=await base.dispatch(input),caps=path==='/bootstrap'?result.data.capabilities:result.data;
      Object.assign(caps.features.pm_workspaces,{sprints:true,meetings:true,governance:true,...(state.enabled?{guided_sprint_review:{version:1}}:{})});
      caps.actions.push(...meeting.config().caps.actions,...sprint.config().caps.actions,...governance.config().caps.actions);return result;
    }
    if(path===prefix){const value=await base.dispatch(input);value.data.workspace={...value.data.workspace,...meeting.config().workspace};return value;}
    if(input.method==='GET'&&state.withdraw&&(path.includes('/meetings')||path.includes('/governance')||path.includes('/threads/')||path.includes('/work-attempts/')))return denied();
    if(path===prefix+'/work/'+meeting.binding.work_item_id+'/attempts')return response({workspace_id:workspaceId,attempts:[{attempt_id:resultRef.attempt_id,work_item_id:meeting.binding.work_item_id}],next_cursor:null});
    if(path===prefix+'/work-attempts/'+resultRef.attempt_id+'/result'){if(state.resultDeferred)await state.resultDeferred;return response(result);}
    if(path===prefix+'/work/'+'wi_'+'6'.repeat(20)+'/attempts')return response({workspace_id:workspaceId,attempts:[{attempt_id:'att_'+'6'.repeat(32)}],next_cursor:null});
    if(path===prefix+'/work-attempts/'+'att_'+'6'.repeat(32)+'/result')return response({...result,attempt_id:'att_'+'6'.repeat(32),sources:{...result.sources,result_id:'res_'+'6'.repeat(32)},conversation:{latest_assistant_summary:{text:'SOURCE_RESULT_B'}}});
    if(path===prefix+'/threads')return base.dispatch(input);
    if(path.startsWith('/operations/')&&input.method==='GET')return response({operation:[...meeting.state.ops.values(),...governance.state.ops.values(),...base.state.ops.values()].find((op:any)=>path.endsWith(op.operation_id))});
    if(path.includes('/meetings')||path===prefix+'/sprints'||path===prefix+'/snapshots'||action?.startsWith('pm.meeting.')){
      if(state.defer&&input.method==='GET'&&path.includes('/meetings/'))await state.defer;
      const known=meeting.state.ops.has(input.idempotency_key);if(state.lost&&action){meeting.state.lost=true;state.lost=false;}
      const value=await meeting.dispatch(input);
      if(action&&!known){const current=meeting.state.meetings.find((row:any)=>row.meeting.meeting_id===input.body.target.meeting_id)||detail;
        if(action==='pm.meeting.draft'&&input.body.params.review_basis){
          const doc=current.draft.document,basis=structuredClone(input.body.params.review_basis);
          doc.guided_review={version:1,basis,context_revision_id:current.context.context_revision_id,context_digest:'d'.repeat(64),frozen_at:1791720000,
            results:basis.results.map((ref:any)=>({reference:ref,reported:'claimed_done',acceptance:'unassessed',dod:'unassessed',verification:'unassessed',qualified:false,
              binding_id:meeting.binding.binding_id,plan_ref:{revision_id:meeting.binding.plan_revision_id},requirements:['account.recovery'],assessment:null})),
            feedback:basis.feedback_items.map((ref:any)=>({id:ref.id,reference:ref.source_ref,content:current.notes.find((row:any)=>row.note_id===ref.source_ref.note_id)?.content||'SOURCE_FEEDBACK: Historical input',superseded_at_selection:ref.source_ref.kind==='discussion_input'})),
            requirements:[{requirement_id:'account.recovery',acceptance:'unassessed'}],gaps:[{issue_id:'pmsrg_'+'e'.repeat(32),source_id:meeting.binding.binding_id,reason:'acceptance_missing'}]};
          doc.output.feedback=doc.guided_review.feedback.map((row:any)=>row.content);current.draft.document_digest='f'.repeat(64);
        }
        if(action==='pm.meeting.complete'){
          current.completion.document.guided_review={draft_revision_id:current.draft.revision_id,draft_digest:current.draft.document_digest};
          current.completion.document.governance_decisions=(input.body.params.governance_decisions||[]).map((ref:any)=>({...ref,recorded_by:'owner',owner_actor:'developer',disposition:'adopted_direction'}));
          current.completion.document_digest='a'.repeat(64);
        }
      }return value;
    }
    if(path.includes('/governance')||path.startsWith(prefix+'/snapshots/')||path===prefix+'/plans/accepted'||action?.startsWith('pm.governance.')){
      const value=await governance.dispatch(input);
      if(action==='pm.governance.record'&&input.body.params.review_proposal_ref){
        const ref=input.body.params.review_proposal_ref,row=governance.state.records.find((row:any)=>row.record.record_id===value.data.operation.result.record_id);
        row.record.context.review_proposal_ref=ref;row.record.context.snapshot_ref=context.snapshot_ref;
        row.revision.document.description=detail.draft.document.decisions.find((row:any)=>row.id===ref.decision_id).text;
      }return value;
    }
    if(path.startsWith(prefix+'/sprint')||path===prefix+'/work'||path.startsWith('/projects/'))return sprint.dispatch(input);
    if(action==='pm.thread.create')Object.assign(base.state.thread,{context:input.body.params.context||[]});
    const reply=await base.dispatch(input);
    if(action==='pm.input.record'&&input.body.params.source_ref.kind==='sprint_review_decision'){const item=base.state.inputs.at(-1),ref=item.source_ref;item.source_decision={reference:ref,decision:detail.completion.document.decisions.find((row:any)=>row.id===ref.decision_id),governance:detail.completion.document.governance_decisions.find((row:any)=>row.decision_id===ref.decision_id),follow_ups:detail.completion.document.follow_ups};}
    return reply;
  }
  return {state,meeting,sprint,governance,base,detail,note,resultRef,dispatch};
}

export function seedGuidedReview(f:ReturnType<typeof guidedReviewFixture>,stage=4){
  const historical={kind:'discussion_input',thread_id:threadId,input_id:'pmi_'+'7'.repeat(32),operation_id:'op_'+'8'.repeat(32)};
  const d=f.detail,decision={id:'retain',text:'SOURCE_GOVERNANCE: Retain direction',rationale:'Review the evidence',owner_actor:'developer',follow_up_ids:['verify']};
  d.draft={meeting_id:d.meeting.meeting_id,revision_id:'pmmrev_'+'a'.repeat(32),document_digest:'b'.repeat(64),actor:'owner',document:{kind:'review',
    output:{goal_review:'SOURCE_GOAL_OBSERVATION',outcomes:[],feedback:stage>=3?[f.note.content]:[],environment_changes:[],next_steps:stage>=4?['SOURCE_NEXT_DIRECTION']:[]},
    decisions:stage>=4?[decision]:[],follow_ups:stage>=4?[{id:'verify',text:'SOURCE_FOLLOW_UP',owner_actor:'developer',expected_effect:'SOURCE_EFFECT',verification:'SOURCE_VERIFICATION',source_refs:[]}]:[],
    unresolved_questions:['SOURCE_UNRESOLVED'],source_refs:[],guided_review:{version:1,context_revision_id:d.context.context_revision_id,context_digest:'c'.repeat(64),frozen_at:1791720000,
      basis:{version:1,results:[f.resultRef],feedback_items:stage>=3?[{id:'historical',source_ref:historical}]:[],governance_revision_ids:[],follow_up_ref:null},
      results:[{reference:f.resultRef,reported:'claimed_done',acceptance:'unassessed',dod:'unassessed',verification:'unassessed',qualified:false,binding_id:f.meeting.binding.binding_id,requirements:['account.recovery']}],
      feedback:stage>=3?[{id:'historical',reference:historical,content:f.note.content,superseded_at_selection:true}]:[],requirements:[],gaps:[]}}};
  d.meeting.draft_revision_id=d.draft.revision_id;d.meeting.version=2;
  if(stage>=4){const row=f.governance.add('change',{description:decision.text,title:'SOURCE_GOVERNANCE_TITLE',owner_actor:'developer'});
    row.record.context.review_proposal_ref={meeting_id:d.meeting.meeting_id,draft_revision_id:d.draft.revision_id,draft_digest:d.draft.document_digest,decision_id:decision.id};
    row.record.context.snapshot_ref=d.context.document.snapshot_ref;
    if(stage>=5){row.revision.action='pm.governance.decide';row.revision.document.decision={recorded_by:'owner',disposition:'adopted_direction',rationale:'Observe'};
      d.meeting.status='completed';d.meeting.completion_id='pmmc_'+'d'.repeat(32);
      d.completion={meeting_id:d.meeting.meeting_id,completion_id:d.meeting.completion_id,document_digest:'e'.repeat(64),actor:'owner',document:{
        discussion_summary:'SOURCE_COMPLETION',guided_review:{draft_revision_id:d.draft.revision_id,draft_digest:d.draft.document_digest},decisions:[decision],follow_ups:d.draft.document.follow_ups,
        governance_decisions:[{decision_id:decision.id,revision_id:row.revision.revision_id,document_digest:row.revision.document_digest,recorded_by:'owner',owner_actor:'developer',disposition:'adopted_direction'}]}};
    }
  }
  return d;
}
