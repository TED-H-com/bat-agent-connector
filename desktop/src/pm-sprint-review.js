import {button, disclosure, sectionCard} from './ui/components.js';
import {timeStamp, timeText} from './ui/stamps.js';
import {qualityFact} from './pm-quality-projections.js';
import {discussHref} from './pm-discuss-link.js';
import {emptyReviewBasis, reviewPrompts, reviewPromptKeys, reviewReferenceKey, reviewRequirementLabel, reviewStep} from './pm-sprint-review-state.js';

// All prose here is ephemeral server content. Only the explicit typed selection
// is passed to the existing durable meeting form.
export function reviewProjection({h, t, detail, governance = [], onEdit, onComplete=null, onNote=null, canPlan=false}) {
  const draft = detail.draft?.document, fixed = draft?.guided_review, context = detail.context.document, next = reviewStep(detail,governance);
  const text = value => h('p', {class: 'pm-note-text'}, value);
  const exact = value => disclosure({label: t('pm_meeting_details'), body: h('pre', {class: 'pm-brief-proof'}, JSON.stringify(value, null, 2))});
  const section = (number, body) => sectionCard({body: h('section', {'data-pm-review-step': number},
    h('h3', {}, `${number}. ${t('pm_review_step_' + number)}`), body)});
  const edit = () => onEdit ? button({variant: 'secondary', label: t('pm_meeting_edit_draft'), onClick: onEdit}) : '';
  const promptKeys = reviewPromptKeys(detail,governance);
  const prompt = key => onEdit&&promptKeys.includes(key) ? text(t(key)) : '';
  const result = h('div', {'data-pm-guided-review': '', 'data-pm-review-next': next},
    h('p',{class:'muted'},t('pm_review_available_step',{step:t('pm_review_step_'+next)})),
    section(1, h('div', {}, h('h4',{},t('pm_meeting_goal')),text(context.sprint_goal.text), h('h4', {}, t('pm_meeting_dod')),
      ...(context.artifacts.dod?.document.criteria || []).map(row => text(row.text)), text(t('pm_meeting_source_frozen')),h('p',{},t('pm_meeting_snapshot'),' · ',timeStamp(context.snapshot_ref.as_of)),
      exact({snapshot: context.snapshot_ref, context_revision_id: detail.context.context_revision_id, coverage:detail.snapshot.coverage, frozen_at: fixed?.frozen_at}))),
    section(2, h('div', {}, prompt('pm_review_outcomes_prompt'), ...(draft?[text(draft.output.goal_review),...(draft.output.outcomes||[]).map(row=>text(row.observation))]:[]), ...(fixed?.results || []).map(row => h('article', {class: 'pm-fact'},
      text(t('pm_review_reported') + ': ' + t('pm_review_'+(row.reported||'none'))),
      qualityFact({h,t,fact:{...row,reference:row.reference.assessment_ref}}), text(t('pm_review_verification') + ': ' + t('pm_review_' + row.verification)),
      exact({reference:row.reference,original_binding:row.binding_id,plan:row.plan_ref,requirements:row.requirements,assessment:row.assessment}))),
      ...(fixed?.requirements || []).map(row => h('p', {}, reviewRequirementLabel(detail.snapshot,row,t('pm_meeting_unknown')), ' · ', t('pq_' + row.acceptance),exact({requirement_id:row.requirement_id,plan_revision_id:row.plan_revision_id}))), edit())),
    section(3, h('div', {}, prompt('pm_review_feedback_prompt'), ...(fixed?.feedback || []).map(row => h('article', {class: 'pm-fact'},
      text(row.content), row.superseded_at_selection ? text(t('pm_review_historical')) : '', exact(row.reference))), ...(draft?.output.environment_changes||[]).map(value=>text(t('pm_meeting_environment_changes')+': '+value)),
      onNote?button({variant:'secondary',label:t('pm_meeting_save_note'),onClick:onNote}):'',edit())),
    section(4, h('div', {}, prompt('pm_review_direction_prompt'), ...(draft?.decisions || []).map(row => h('article', {class: 'pm-fact'},
      text(row.text), text(row.rationale), text(t('pm_meeting_assigned', {actor: row.owner_actor||t('pm_meeting_unknown')})), exact({id:row.id,follow_up_ids:row.follow_up_ids}))), ...(draft?.output.next_steps||[]).map(text),
      ...(draft?.follow_ups||[]).map(row=>h('article',{class:'pm-fact'},text(row.text),text(t('pm_meeting_assigned',{actor:row.owner_actor})),text(row.expected_effect),text(row.verification))),
      disclosure({label:t('pm_meeting_unresolved_questions'),body:h('ul',{},...(draft?.unresolved_questions||[]).map(value=>h('li',{},value)))}),edit())),
    section(5, h('div', {}, text(detail.completion?.document.discussion_summary || t('pm_meeting_complete_help')),
      !detail.completion&&onComplete?button({variant:'primary',label:t('pm_meeting_complete'),onClick:onComplete}):'',
      ...(detail.completion?.document.governance_decisions || []).map(row => h('p', {}, t('pm_meeting_recorded_by', {actor:row.recorded_by}), ' · ',t('pm_gov_'+row.disposition),' · ',
        t('pm_meeting_assigned', {actor:row.owner_actor}))), exact(detail.completion ? {completion_id:detail.completion.completion_id,document_digest:detail.completion.document_digest} : {}))),
    section(6, h('div', {}, text(t('pm_review_draft_only')), canPlan&&detail.completion?.document.governance_decisions?.length ? h('a', {class:'ui-button secondary', href:discussHref({workspaceId:detail.workspace_id,
      refs:[{kind:'meeting',id:detail.meeting.meeting_id}]})}, t('pm_review_planning')) : text(t(!canPlan?'pm_panel_read_only':detail.completion?'pm_review_no_decision':'pm_review_complete_first')))));
  if (fixed?.gaps.length) {
    const prompts = reviewPrompts(fixed.gaps);
    result.prepend(sectionCard({body:h('div', {'data-pm-review-gaps':''}, h('h3', {}, t('pm_meeting_unresolved_questions')),
      disclosure({label:t('pm_review_more_gaps',{count:prompts.shown.length+prompts.remaining}),body:h('ul',{},...fixed.gaps.map(row=>h('li',{},
        t('pm_review_gap_'+row.reason),exact({issue_id:row.issue_id,source_id:row.source_id}))))}))}));
  }
  return result;
}

export function reviewSources({h,t,api,alive,workspaceId,detail,field,getEditor,unavailable,valid}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  const element = h('div', {'data-pm-review-sources':''}), controls = [], displays = [];
  let serial = 0, disposed = false;
  const current = ticket => !disposed && ticket === serial && valid();
  const basis = () => JSON.parse(field.value || JSON.stringify(emptyReviewBasis()));
  const can = () => valid() && getEditor() && !getEditor().locked();
  const change = fn => {if (!can()) return; const value=basis();fn(value);field.value=JSON.stringify(value);getEditor().changed();sync();};
  const fold = (key,body) => disclosure({label:t(key),body});
  const exact = ref => fold('pm_meeting_details',h('pre',{class:'pm-brief-proof'},JSON.stringify(ref,null,2)));
  const fail = error => {if(!disposed){dispose();unavailable(error);}};
  const check = value => {if(value?.workspace_id !== workspaceId)throw Error(t('pm_scope_mismatch'));return value;};
  async function read(path) {const data=await api('GET',endpoint+path);alive();return data;}
  function choice(holder, label, ref, key, identity, limit=20) {
    const box=h('input',{type:'checkbox'});
    const selected=()=>basis()[key].some(row=>identity(row)===identity(ref));
    box.addEventListener('change',()=>change(value=>{
      const rows=value[key].filter(row=>identity(row)!==identity(ref));
      if(box.checked){if(rows.length>=limit)throw Error(t('pm_review_selection_limit'));rows.push(ref);}value[key]=rows;
    }));
    controls.push(()=>{box.checked=selected();box.disabled=!can()||!box.checked&&basis()[key].length>=limit;});
    holder.append(h('label',{class:'pm-fact-heading'},box,h('span',{class:'pm-note-text'},label)),exact(ref));
  }
  const sourceSection=(label)=>{const holder=h('div');displays.push(holder);element.append(fold(label,holder));return holder;};
  const results=sourceSection('pm_review_results'),feedback=sourceSection('pm_meeting_feedback'),issues=sourceSection('pm_meeting_unresolved_questions');
  for(const [holder,key]of [[results,'results'],[feedback,'feedback_items'],[issues,'governance_revision_ids']]){
    const clear=button({variant:'secondary',label:t('pm_review_clear_sources'),onClick:()=>change(value=>{value[key]=[];if(key==='governance_revision_ids')value.follow_up_ref=null;})});
    controls.push(()=>{clear.disabled=!can();});holder.append(clear);
  }
  const notes=(detail.notes||[]).filter(row=>row.context_revision_id===detail.context.context_revision_id);
  for(const row of notes)choice(feedback,row.content,{id:row.note_id,source_ref:{kind:'meeting_note',note_id:row.note_id}},'feedback_items',row=>reviewReferenceKey(row.source_ref));
  // A source must be explicitly opened. Neither selection nor opening dispatches work.
  const work=h('select',{'aria-label':t('pm_review_work')},h('option',{value:''},t('pm_review_choose')),
    ...detail.context.document.selected_items.map(row=>h('option',{value:row.work_item_id},row.title||detail.snapshot.work?.find(item=>item.work_item_id===row.work_item_id)?.title||t('pm_meeting_unknown'))));
  results.append(h('label',{},t('pm_review_work'),work));
  const resultRows=h('div');results.append(resultRows);
  work.addEventListener('change',async()=>{
    const ticket=++serial,id=work.value;resultRows.replaceChildren();if(!id)return;
    try{
      const page=check(await read(`/work/${encodeURIComponent(id)}/attempts?limit=20`));if(!current(ticket)||work.value!==id)return;
      if(page.next_cursor!=null)resultRows.append(h('p',{},t('pm_review_partial_results')));
      for(const attempt of page.attempts||[]){
        const data=check(await read(`/work-attempts/${encodeURIComponent(attempt.attempt_id)}/result`));if(!current(ticket)||work.value!==id)return;
        if(data.attempt_id!==attempt.attempt_id)throw Error(t('pm_scope_mismatch'));
        if(!data.sources?.result_id)continue;
        const ref={attempt_id:data.attempt_id,result_id:data.sources.result_id,digest:data.sources.digest,assessment_ref:null};
        choice(resultRows,data.conversation?.latest_assistant_summary?.text||t('pm_review_result'),ref,'results',row=>row.result_id);
        resultRows.append(h('p',{},t('pm_review_reported'),': ',t('pm_review_'+(data.execution?.state_facets?.self_reported||'none'))),
          h('p',{},t('pq_acceptance'),': ',t('pq_unassessed')),exact({verification:data.verification,requirements:data.requirements}));
        const seen=new Set(), assessment=h('select',{'aria-label':t('pm_review_assessment')},h('option',{value:'null'},t('pq_unassessed')));
        for(const item of data.requirements?.items||[])for(const row of item.assessments||[]){
          const value=reviewReferenceKey(row.reference);if(seen.has(value))continue;seen.add(value);
          assessment.append(h('option',{value},`${row.assessed_by} · ${timeText(row.assessed_at)} · ${t(row.applicable===true?'wres_req_applicable':row.applicable===false?'wres_req_no_longer_applies':'pq_fixed')}`));
          resultRows.append(h('ul',{},...(row.original?.requirements||[]).map(requirement=>h('li',{},data.requirements.items.find(item=>item.requirement_id===requirement.requirement_id)?.text||t('pm_meeting_unknown'),' · ',t('pq_'+requirement.status)))),exact({reference:row.reference,proof:row.original}));
        }
        const syncAssessment=()=>{assessment.disabled=!can()||!basis().results.some(row=>row.result_id===ref.result_id);assessment.value=reviewReferenceKey(basis().results.find(row=>row.result_id===ref.result_id)?.assessment_ref||null);};
        assessment.addEventListener('change',()=>change(value=>{const row=value.results.find(row=>row.result_id===ref.result_id);if(row)row.assessment_ref=JSON.parse(assessment.value);}));
        controls.push(syncAssessment);resultRows.append(h('label',{},t('pm_review_assessment'),assessment));
      }sync();
    }catch(error){if(current(ticket))fail(error);}
  });
  // Additional feedback is chosen from an existing discussion's exact InputRecords.
  const thread=h('select',{'aria-label':t('pm_review_thread')},h('option',{value:''},t('pm_review_choose')));
  const listThreads=button({variant:'secondary',label:t('pm_review_list_discussions'),onClick:async()=>{
    const ticket=++serial;threadRows.replaceChildren();thread.replaceChildren(h('option',{value:''},t('pm_review_choose')));
    try{const page=check(await read('/threads?limit=20'));if(!current(ticket))return;
      for(const row of page.threads||[])thread.append(h('option',{value:row.thread_id},row.title));
      if(page.next_cursor!=null)threadRows.append(h('p',{},t('pm_meeting_partial')));
    }catch(error){if(current(ticket))fail(error);}
  }});
  const threadRows=h('div');feedback.append(listThreads,h('label',{},t('pm_review_thread'),thread),button({variant:'secondary',label:t('pm_review_read_feedback'),onClick:async()=>{
    const id=thread.value,ticket=++serial;threadRows.replaceChildren();
    if(!/^pmt_[0-9a-f]{32}$/.test(id)){threadRows.append(h('p',{},t('pm_scope_mismatch')));return;}
    try{const data=await read(`/threads/${id}`);if(!current(ticket)||thread.value!==id)return;check(data.thread);
      if(data.thread?.thread_id!==id)throw Error(t('pm_scope_mismatch'));
      if(data.coverage?.inputs_truncated||data.coverage?.history_truncated)threadRows.append(h('p',{},t('pm_meeting_partial')));
      for(const row of [...(data.inputs||[]),...(data.input_history||[])])choice(threadRows,row.value,{id:row.input_id,source_ref:{kind:'discussion_input',thread_id:id,input_id:row.input_id,operation_id:row.operation_id}},'feedback_items',row=>reviewReferenceKey(row.source_ref));sync();
    }catch(error){if(current(ticket))fail(error);}
  }}),threadRows);
  const governanceRows=h('div');issues.append(governanceRows);
  issues.append(button({variant:'secondary',label:t('pm_review_read_issues'),onClick:async()=>{
    const ticket=++serial;
    try{const page=check(await read('/governance?after=0&limit=20'));if(!current(ticket))return;
      const rows=h('div');governanceRows.replaceChildren(rows);if(page.next_cursor!=null)rows.append(h('p',{},t('pm_meeting_partial')));
      for(const row of page.records||[])choice(rows,row.revision.document.title,row.revision.revision_id,'governance_revision_ids',id=>id);sync();
    }catch(error){if(current(ticket))fail(error);}
  }}));
  const intake=h('select',{'aria-label':t('pm_review_intake')},h('option',{value:''},t('pm_review_choose'))),intakeRows=h('div');
  issues.append(button({variant:'secondary',label:t('pm_review_list_intakes'),onClick:async()=>{
    const ticket=++serial;intakeRows.replaceChildren();intake.replaceChildren(h('option',{value:''},t('pm_review_choose')));
    try{const page=check(await read('/follow-ups?after=0&limit=20'));if(!current(ticket))return;
      for(const row of page.intakes||[])intake.append(h('option',{value:row.intake_id},`${t('pm_review_intake')} · ${new Date(row.created_at*1000).toISOString()}`));
      if(page.next_cursor!=null)intakeRows.append(h('p',{},t('pm_meeting_partial')));
    }catch(error){if(current(ticket))fail(error);}
  }}));
  issues.append(h('label',{},t('pm_review_intake'),intake),button({variant:'secondary',label:t('pm_review_read_follow_up'),onClick:async()=>{
    const id=intake.value,ticket=++serial;intakeRows.replaceChildren();
    if(!/^pmfu_[0-9a-f]{32}$/.test(id)){intakeRows.append(h('p',{},t('pm_scope_mismatch')));return;}
    try{const data=check(await read(`/follow-ups/${id}`));if(!current(ticket)||intake.value!==id)return;
      if(data.intake?.intake_id!==id)throw Error(t('pm_scope_mismatch'));
      for(const row of data.intake.document.issues){const box=h('input',{type:'checkbox'});
        box.addEventListener('change',()=>change(value=>{
          const ref=value.follow_up_ref,ids=ref?.intake_id===id?ref.issue_ids.filter(key=>key!==row.issue_id):[];
          if(box.checked&&ids.length<20)ids.push(row.issue_id);value.follow_up_ref=ids.length?{intake_id:id,document_digest:data.intake.document_digest,issue_ids:ids}:null;
        }));
        controls.push(()=>{box.checked=basis().follow_up_ref?.intake_id===id&&basis().follow_up_ref.issue_ids.includes(row.issue_id);box.disabled=!can()||!box.checked&&basis().follow_up_ref?.issue_ids.length>=20;});
        intakeRows.append(h('label',{class:'pm-fact-heading'},box,h('span',{class:'pm-note-text'},row.question||row.text||row.title||detail.snapshot.work?.find(item=>item.work_item_id===row.work_item_id)?.title||t('pm_meeting_unknown'))),exact({intake_id:id,issue_id:row.issue_id}));
      }sync();
    }catch(error){if(current(ticket))fail(error);}
  }}),intakeRows);
  thread.addEventListener('change',()=>{++serial;threadRows.replaceChildren();});
  intake.addEventListener('change',()=>{++serial;intakeRows.replaceChildren();});
  function sync(){controls.forEach(update=>update());}
  function dispose(){disposed=true;++serial;displays.forEach(node=>node.replaceChildren());controls.length=0;}
  return {element,sync,dispose};
}

// The original source projection travels with the InputRecord and pmd_ draft.
export function reviewDecisionProof({h,t,source,expected}) {
  if(!source)return '';
  if(!expected||Object.entries(expected).some(([key,value])=>source.reference?.[key]!==value)||source.governance?.decision_id!==expected.decision_id)throw Error(t('pm_scope_mismatch'));
  return disclosure({label:t('pm_review_decision_source'),body:h('div',{'data-pm-review-decision-source':''},
    h('p',{class:'pm-note-text'},source.decision.text),h('p',{class:'pm-note-text'},source.decision.rationale),
    h('p',{},t('pm_meeting_recorded_by',{actor:source.governance.recorded_by}),' · ',t('pm_gov_'+source.governance.disposition)),
    h('p',{},t('pm_meeting_assigned',{actor:source.governance.owner_actor})),
    ...(source.follow_ups||[]).map(row=>h('p',{class:'pm-note-text'},row.text,' · ',row.verification)),
    disclosure({label:t('pm_meeting_details'),body:h('pre',{class:'pm-brief-proof'},JSON.stringify(source.reference,null,2))}))});
}
