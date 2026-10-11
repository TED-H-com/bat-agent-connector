import {briefExport} from './pm-brief-export.js';

// Render only the supplied original Snapshot. Discussion stays in separate records.
export function maintenanceReviewView({h,t,api,alive,workspaceId,caps,readable,errorBox,onUnavailable,detail}) {
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
  for(const kind of ['github','workload','checks','issues','dependencies','research','quality','model_analysis']){
    const value=coverage[kind],state=typeof value==='string'?value:value.status;
    sources.append(line(`pmrv_source_${kind}`,label('pmrv_coverage',state)));
  }
  const compared=h('section',{},h('h4',{},t('pmrv_changes')),text(label('pmrv_changes',changes.status)));
  if(changes.scope_changed)compared.append(text(t('pmrv_scope_changed')));
  for(const key of ['added','changed','no_longer_observed'])compared.append(fold(`pmrv_${key}`,list(changes[key])));
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
      line('pmrv_as_of',stamp(report.as_of)),text(snapshot.snapshot_digest),op(review.operation_id)));
  const output=briefExport({h,t,api,alive,workspaceId,caps,snapshot:()=>snapshot,readable,onUnavailable,errorBox});
  overview.append(output.element);
  const discussion=h('section',{'data-review-discussion':''},h('h4',{},t('pmrv_discussion')));
  const eventView=event=>{
    if(event.workspace_id!==workspaceId||event.review_id!==review.review_id||typeof event.document!=='object')throw Error(t('pmrv_invalid'));
    const d=event.document,box=h('article',{class:'pm-fact','data-review-event':event.event_id},
      h('h5',{},label('pmrv_event',event.kind.replaceAll('.','_'))),line('pmrv_recorded',`${event.actor} · ${stamp(event.created_at)}`));
    if(event.kind==='note')box.append(text(d.content));
    if(event.kind==='candidate.record'){
      box.append(h('h5',{},d.title),text(t('pmrv_candidate_help')));
      for(const key of ['problem','goal_relation','expected_effect'])box.append(line(`pmrv_${key}`,d[key]));
      box.append(fold('pmrv_options',list(d.options)),fold('pmrv_hypotheses',list(d.hypotheses)));
      for(const source of d.public_sources||[]){
        let url;try{url=new URL(source.url);if(url.protocol!=='https:'||url.username||url.password)throw Error();}catch{throw Error(t('pmrv_invalid'));}
        box.append(h('a',{href:url.href,target:'_blank',rel:'noopener noreferrer'},source.title),text(t('pmrv_attributed_source')));
      }
    }
    if(event.kind==='complete'){
      box.append(text(d.discussion_summary),line('pmrv_direction',label('pmrv_direction',d.direction)),text(d.direction_rationale),text(t('pmrv_completion_help')));
      if(d.follow_ups.length)box.append(h('h5',{},t('pmrv_followups')),...d.follow_ups.map(item=>h('article',{class:'pm-fact'},text(item.text),
        line('pmrv_owner',item.owner_actor),line('pmrv_expected_effect',item.expected_effect),line('pmrv_verification',item.verification),text(t('pmrv_unassessed')))));
      box.append(h('h5',{},t('pmrv_next_focus')),list(d.next_observation_focus));
    }
    if(event.kind==='follow_up.record')box.append(line('pmrv_followup_status',label('pmrv_followup',d.status)),text(d.result),text(t('pmrv_unassessed')));
    box.append(fold('pmrv_proof',text(d.rationale),op(event.operation_id)));
    return box;
  };
  if(completion)discussion.append(eventView(completion));
  else discussion.append(text(t('pmrv_not_completed')));
  for(const event of events)if(event.event_id!==completion?.event_id)discussion.append(eventView(event));
  const element=h('div',{},overview,discussion);
  return {element,eventView,discussion,update:output.update};
}
