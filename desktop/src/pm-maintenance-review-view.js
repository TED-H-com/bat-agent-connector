import {briefExport} from './pm-brief-export.js';
import {maintenanceFindingsView} from './pm-maintenance-findings.js';

// Render only the supplied original Snapshot. Discussion stays in separate records.
export function maintenanceReviewView({h,t,api,alive,workspaceId,caps,readable,errorBox,onUnavailable,detail,candidateInput}) {
  const {review,snapshot,completion,events}=detail,extension=snapshot?.maintenance,report=extension?.report;
  if(review?.workspace_id!==workspaceId||snapshot?.workspace_id!==workspaceId||extension?.review_id!==review.review_id
    ||review.snapshot_id!==snapshot.snapshot_id||!/^pmrevw_[0-9a-f]{32}$/.test(review.review_id)
    ||extension.schema_version!==1||report?.schema_version!==1||report.meeting_completed!==false
    ||report.scope_adopted!==false||report.automatic_maintenance_authorized!==false||!Array.isArray(events))throw Error(t('pmrv_invalid'));
  const text=value=>h('p',{class:'pm-note-text'},String(value));
  const line=(key,value)=>h('p',{},h('strong',{},`${t(key)}: `),String(value));
  const stamp=value=>Number.isFinite(value)?new Date(value*1000).toLocaleString():t('pmrv_unknown');
  const fold=(key,...nodes)=>h('details',{},h('summary',{},t(key)),...nodes);
  const label=(prefix,value)=>t(`${prefix}_${value}`);
  const list=values=>values.length?h('ul',{},...values.map(value=>h('li',{class:'pm-note-text'},String(value)))):text(t('pmrv_none'));
  const op=id=>h('a',{href:`#/op/${encodeURIComponent(id)}`},t('pm_operation'));
  const sourceById=new Map(report.sources.map(row=>[row.source_id,row]));
  const sourceProof=ids=>h('div',{},...ids.map(id=>{
    const source=sourceById.get(id);if(!source)throw Error(t('pmrv_invalid'));
    return h('div',{class:'pm-review-proof'},line(`pmrv_ref_${source.kind}`,source.id),line('pmrv_source_digest',source.digest));
  }));
  const release=report.release,coverage=report.coverage,changes=report.changes;
  const reasonGroups={
    source_limit:['source_row_limit','source_reference_limit'],source_access:['source_not_visible','linked_task_unavailable','sprint_source_unavailable','linked_operation_unavailable'],
    source_age:['source_time_unproven','source_expired'],pending_work:['task_waiting','task_committed','work_waiting','work_committed','coding_active','session_waiting'],
    verification:['task_failed_unresolved','task_not_verified','work_done_claim_unverified','started_attempt_result_unverified','session_end_unproven'],
    uncertain:['command_waiting_or_unknown','operation_unknown','operation_committed','deployment_waiting_or_unknown'],sprint:['sprint_commitment'],
    adapter:['bat_scope_unconfigured','adapter_scope_changed','adapter_disconnected','adapter_not_observed','adapter_coverage_incomplete','linked_session_not_covered','session_workspace_unproven','session_profile_unproven','session_state_unknown'],
    github_gap:['github_repository_coverage_unavailable','linked_pr_current_state_unproven','github_observation_missing','github_observation_context_changed','github_observation_incomplete'],
    github_prs:['github_open_pull_requests'],github_actions:['github_nonterminal_actions']};
  const overview=h('section',{'data-review-report':''},h('h3',{},review.title),
    text(t(review.status==='completed'?'pmrv_completed':'pmrv_prepared')),
    line('pmrv_period',`${stamp(report.period.start)} — ${stamp(report.period.end)}`),text(t('pmrv_period_help')),
    text(t('pmrv_fixed_help')),text(t('pmrv_samples')),
    line('pmrv_release',`${release.repository} · ${release.channel} · ${release.release_version.value}`),
    line('pmp_revision',report.policy.version));
  if(report.previous_context?.status==='omitted_source_limit')overview.append(text(t('pmrv_continuity_gap')));
  if(report.schedule)overview.append(fold('pmrv_schedule',line('pmc_due',stamp(report.schedule.scheduled_at)),
    line('pmrv_missed_weeks',report.schedule.coalesced_windows),text(t('pmrv_missed_help'))));
  const sources=h('section',{},h('h4',{},t('pmrv_coverage')));
  for(const kind of ['github','workload','checks',...(coverage.repairs?['repairs']:[]),'issues','dependencies','research','quality','model_analysis']){
    const value=coverage[kind],state=typeof value==='string'?value:value.status;
    sources.append(line(`pmrv_source_${kind}`,label('pmrv_coverage',state)));
    if(Number.isSafeInteger(value.samples))sources.append(h('p',{class:'muted pm-review-count'},`${t('pmrv_sample_count')}: ${value.samples}`));
  }
  const compared=h('section',{},h('h4',{},t('pmrv_changes')),text(label('pmrv_changes',changes.status)));
  if(changes.scope_changed)compared.append(text(t('pmrv_scope_changed')));
  for(const key of ['added','changed','no_longer_observed'])compared.append(fold(`pmrv_${key}`,list(changes[key])));
  if(report.finding_changes){
    const findings=fold('pmrv_finding_changes');
    for(const kind of ['issues','dependabot_pull_requests','dependabot_alerts']){
      const change=report.finding_changes[kind];
      if(!change)throw Error(t('pmrv_invalid'));
      const entry=h('section',{},h('h5',{},t('pmf_'+kind)),text(label('pmrv_changes',change.status)));
      for(const key of ['added','changed','no_longer_observed'])entry.append(fold(`pmrv_${key}`,list(change[key])));
      findings.append(entry);
    }
    compared.append(findings);
  }
  overview.append(h('div',{class:'pm-review-columns'},sources,compared));
  const blockers=h('div');
  for(const item of report.current_blockers){
    if(typeof item==='string')blockers.append(text(item));
    else blockers.append(text(t('pmo_reason_'+(Object.keys(reasonGroups).find(key=>reasonGroups[key].includes(item.code))||'unknown'))));
  }
  if(!report.current_blockers.length)blockers.append(text(t('pmrv_none')));
  overview.append(fold('pmrv_blockers',text(t('pmrv_blocker_help')),blockers),
    line('pmrv_finished_checks',report.effort.completed_checks),line('pmrv_pending_checks',report.effort.pending_checks),text(t('pmrv_effort_unknown')),
    fold('pmrv_proof',line('pmrv_snapshot',snapshot.snapshot_id),line('pmrv_release',release.baseline_id),text(release.commit_sha),
      line('pmrv_as_of',stamp(report.as_of)),text(snapshot.snapshot_digest),sourceProof(report.sources.map(row=>row.source_id)),op(review.operation_id)));
  const samples=h('div');
  for(const row of report.observations||[]){
    const sample=h('article',{class:'pm-fact'},line('pmrv_sample_at',stamp(row.observed_at)),
      line('pmrv_open_prs',row.observed_open_pulls),line('pmrv_active_actions',row.observed_nonterminal_actions),
      fold('pmrv_proof',sourceProof([row.source_id])));
    if(row.maintenance_sources){
      const original=sourceById.get(row.source_id);
      if(original?.kind!=='github'||original.id!==row.observation_id||original.digest!==row.document_digest)throw Error(t('pmrv_invalid'));
      sample.append(maintenanceFindingsView({h,t,workspaceId,data:{workspace_id:workspaceId,
        observation_ref:{observation_id:row.observation_id,document_digest:row.document_digest},
        sources:row.maintenance_sources,visibility_filtered:false,automatic_repair_authorized:false}}));
    }
    samples.append(sample);
  }
  for(const row of report.workloads||[])samples.append(h('article',{class:'pm-fact'},line('pmrv_sample_at',stamp(row.observed_at)),
    line('pmrv_workload_classification',label('pmrv_workload',row.classification)),fold('pmrv_proof',sourceProof([row.source_id]))));
  for(const row of report.checks||[])samples.append(h('article',{class:'pm-fact'},line('pmrv_check_outcome',label('pmrv_check',row.outcome)),
    line('pmrv_recorded',stamp(row.started_at)),fold('pmrv_proof',sourceProof([row.source_id]))));
  for(const row of report.repairs||[]){
    if(typeof row.capacity_held!=='boolean'||row.observation_basis!=='saved_repair_point'||row.later_changes_included!==false)throw Error(t('pmrv_invalid'));
    const result=['published_for_review','merged','deployed'].includes(row.outcome)?label('pmrv_repair',row.outcome):t('pmrv_repair_pending');
    samples.append(h('article',{class:'pm-fact','data-review-repair':row.repair_id},
      line('pmrv_repair_outcome',result),text(t('pmrv_repair_limit')),
      text(t(row.capacity_held?'pmrv_repair_capacity_held':'pmrv_repair_capacity_released')),
      text(t(row.runtime_completion?'pmrv_repair_runtime_completed':'pmrv_repair_runtime_unproven')),
      line('pmrv_recorded',stamp(row.started_at)),
      fold('pmrv_proof',sourceProof([row.source_id]),op(row.operation_id),...(row.children||[]).map(child=>op(child.operation_id)))));
  }
  if(!samples.children.length)samples.append(text(t('pmrv_none')));
  overview.append(fold('pmrv_source_samples',samples),fold('pmrv_next_focus_report',list((report.next_observation_focus||[]).map(value=>
    ['review_source_coverage','verify_reported_results'].includes(value)?label('pmrv_focus',value):value))));
  const output=briefExport({h,t,api,alive,workspaceId,caps,snapshot:()=>snapshot,readable,onUnavailable,errorBox});
  overview.append(output.element);
  const discussion=h('section',{'data-review-discussion':''},h('h4',{},t('pmrv_discussion')));
  const eventView=(event,expectedReviewId=review.review_id)=>{
    if(event.workspace_id!==workspaceId||event.review_id!==expectedReviewId||typeof event.document!=='object')throw Error(t('pmrv_invalid'));
    const d=event.document,box=h('article',{class:'pm-fact','data-review-event':event.event_id},
      h('h5',{},label('pmrv_event',event.kind.replaceAll('.','_'))),line('pmrv_recorded',`${event.actor} · ${stamp(event.created_at)}`));
    if(event.kind==='note')box.append(text(d.content));
    if(event.kind==='candidate.record'){
      box.append(h('h5',{},d.title),text(t('pmrv_candidate_help')));
      for(const key of ['problem','goal_relation','expected_effect'])box.append(line(`pmrv_${key}`,d[key]));
      box.append(fold('pmrv_options',list(d.options)),fold('pmrv_hypotheses',list(d.hypotheses)));
      for(const source of d.public_sources||[]){
        let url;try{url=new URL(source.url);if(url.protocol!=='https:'||url.username||url.password)throw Error();}catch{throw Error(t('pmrv_invalid'));}
        box.append(h('a',{href:url.href,target:'_blank',rel:'noopener noreferrer'},source.title),line('pmrv_public_date',stamp(source.checked_at)),text(source.claim),text(t('pmrv_attributed_source')));
      }
      if(candidateInput)box.append(candidateInput(event));
    }
    if(event.kind==='complete'){
      box.append(text(d.discussion_summary),line('pmrv_direction',label('pmrv_direction',d.direction)),text(d.direction_rationale),text(t('pmrv_completion_help')));
      if(d.follow_ups.length)box.append(h('h5',{},t('pmrv_followups')),...d.follow_ups.map(item=>h('article',{class:'pm-fact'},text(item.text),
        line('pmrv_owner',item.owner_actor),line('pmrv_expected_effect',item.expected_effect),line('pmrv_verification',item.verification),text(t('pmrv_unassessed')))));
      box.append(h('h5',{},t('pmrv_next_focus')),list(d.next_observation_focus));
    }
    if(event.kind==='follow_up.record')box.append(line('pmrv_followup_status',label('pmrv_followup',d.status)),text(d.result),text(t('pmrv_unassessed')));
    box.append(fold('pmrv_proof',text(d.rationale),sourceProof(d.source_refs||[]),op(event.operation_id)));
    return box;
  };
  if(report.previous_review){
    const previous=report.previous_review,history=fold('pmrv_previous_context',
      text(t(previous.status==='completed'?'pmrv_completed':'pmrv_prepared')),
      line('pmrv_snapshot',previous.snapshot_ref.snapshot_id));
    if(previous.history_truncated)history.append(text(t('pmrv_previous_truncated')));
    if(previous.completion)history.append(eventView(previous.completion,previous.review_id));
    for(const event of [...previous.notes,...previous.candidates,...previous.follow_up_results])history.append(eventView(event,previous.review_id));
    overview.append(history);
  }
  if(completion)discussion.append(eventView(completion));
  else discussion.append(text(t('pmrv_not_completed')));
  for(const event of events)if(event.event_id!==completion?.event_id)discussion.append(eventView(event));
  const element=h('div',{},overview,discussion);
  return {element,eventView,discussion,update:output.update};
}
