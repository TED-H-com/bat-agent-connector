import {emptyState, iconButton} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
import {maintenanceFindingsView} from './pm-maintenance-findings.js';
// Saved observations are evidence at a point in time, never permission to repair.
export function maintenanceObservations({h,t,api,alive,workspaceId,workspace,caps,permitted,form,errorBox}) {
  const base=`/pm/workspaces/${encodeURIComponent(workspaceId)}`,profile='github_open_prs_and_actions_v1';
  let epoch=0,ready=false,policy=null,baseline=null,lifecycle=null;
  const editors=[],status=h('div',{role:'status'}),facts=h('div',{class:'pm-observation-facts'}),context=h('p',{class:'muted'});
  const canWrite=()=>permitted(false)&&caps()?.scopes?.includes('manage');
  const canRecover=()=>Boolean(workspace()?.membership)&&caps()?.scopes?.includes('manage');
  const githubAvailable=()=>caps()?.features?.pm_github_observation?.profiles?.includes(profile);
  const current=()=>ready&&policy&&baseline&&lifecycle;
  const check=value=>{if(value?.workspace_id!==workspaceId)throw Error(t('pmo_invalid'));return value;};
  const valid=ticket=>{try{alive();return ticket===epoch;}catch{return false;}};
  const line=(label,value)=>h('p',{},h('strong',{},`${t(label)}: `),value);
  const stamp=value=>timeStamp(value, {empty:t('pmo_unknown_time')});
  const reasonGroups={
    source_limit:['source_row_limit','source_reference_limit'],source_access:['source_not_visible','linked_task_unavailable','sprint_source_unavailable','linked_operation_unavailable'],
    source_age:['source_time_unproven','source_expired'],pending_work:['task_waiting','task_committed','work_waiting','work_committed','coding_active','session_waiting'],
    verification:['task_failed_unresolved','task_not_verified','work_done_claim_unverified','started_attempt_result_unverified','session_end_unproven'],
    uncertain:['command_waiting_or_unknown','operation_unknown','operation_committed','deployment_waiting_or_unknown'],sprint:['sprint_commitment'],
    adapter:['bat_scope_unconfigured','adapter_scope_changed','adapter_disconnected','adapter_not_observed','adapter_coverage_incomplete','linked_session_not_covered','session_workspace_unproven','session_profile_unproven','session_state_unknown'],
    github_gap:['github_repository_coverage_unavailable','linked_pr_current_state_unproven','github_observation_missing','github_observation_context_changed','github_observation_incomplete'],
    github_prs:['github_open_pull_requests'],github_actions:['github_nonterminal_actions']};
  const reasonText=reason=>t('pmo_reason_'+(Object.keys(reasonGroups).find(key=>reasonGroups[key].includes(reason.code))||'unknown'));
  const text=value=>typeof value==='string'&&value.length>0&&value.length<=4096;
  const object=value=>value&&typeof value==='object'&&!Array.isArray(value);
  function sourceReference(source){
    if(source==null)return '';
    if(!object(source)||!text(source.kind))throw Error(t('pmo_invalid'));
    const names={work:'work',task:'task',operation:'operation',command:'command',session:'session',sprint:'sprint',bat_workspace:'bat'};
    const row=h('p',{class:'pm-source'},t('pmo_recorded_source'),': ',names[source.kind]?t('pmo_ref_'+names[source.kind]):h('code',{},source.kind));
    for(const field of ['id','host','workspace_id','profile_id','project_id'])if(source[field]!=null){
      if(!text(source[field]))throw Error(t('pmo_invalid'));
      row.append(' · ',h('code',{},source[field]));
    }
    return row;
  }
  function exclusion(row){
    if(!object(row)||!text(row.reason))throw Error(t('pmo_invalid'));
    const reasons=['checkpoint_is_not_activity','failed_attempt_explicitly_deferred','unselected_backlog',
      'pm_owned_session','verified_ended_session_retained','original_maintenance_attempt_completed'];
    const item=h('li',{},t('pmo_exclusion_'+(reasons.includes(row.reason)?row.reason:'unknown')),sourceReference(row));
    if(!reasons.includes(row.reason))item.append(h('code',{class:'pm-source'},row.reason));
    if(row.runtime_observation_id!=null){
      if(!/^op_[0-9a-f]{32}$/.test(row.runtime_observation_id))throw Error(t('pmo_invalid'));
      item.append(h('a',{href:`#/op/${encodeURIComponent(row.runtime_observation_id)}`},t('pmo_runtime_receipt')),
        ' · ',h('code',{class:'pm-source'},row.runtime_observation_id));
    }
    return item;
  }
  const editorsBox=h('div',{class:'pm-observation-actions'});
  function update(){for(const editor of editors)editor.update();}
  const request=async(...args)=>{const ticket=epoch;try{return await api(...args);}catch(error){
    if(valid(ticket)&&[401,403,404].includes(error.status))failure(error);throw error;
  }};
  for(const [action,label,description] of [['pm.github.observe','pmo_observe','pmo_observe_help'],['pm.workload.capture','pmo_capture','pmo_capture_help']]){
    if(action==='pm.github.observe'&&!githubAvailable())continue;
    const editor=form({action,target:{workspace_id:workspaceId},fields:{},api:request,
      params:()=>({policy_id:policy.policy_id}),preconditions:()=>({expected_policy_version:policy.revision,
        expected_baseline_id:baseline.baseline_id,expected_lifecycle_revision:lifecycle.revision}),
      permitted:()=>Boolean(current()&&canWrite()&&(action!=='pm.github.observe'||policy.document.scope.github_scope===profile)),
      recoverPermitted:canRecover,label,extra:h('p',{class:'muted'},t(description)),onSaved:()=>refresh()});
    // These are optional evidence actions beneath the policy's adoption decision.
    editor.box.querySelector('button[type=submit]').className='secondary';editors.push(editor);editorsBox.append(editor.box);
  }
  const element=h('div',{'data-pm-maintenance-observations':''},h('header',{class:'pm-heading'},h('h3',{},t('pmo_title')),
    iconButton({icon:'refresh-cw', label:t('pmo_refresh'), ...{type:'button',class:'secondary',onclick:()=>void refresh()}})),status,
    h('p',{class:'muted'},t('pmo_intro')),facts,context,editorsBox);
  function record(value,kind){
    const key=kind==='github'?'observation':'snapshot',row=check(value)[key];
    if(typeof value.visibility_filtered!=='boolean'||value.visibility_filtered&&row!==null)throw Error(t('pmo_invalid'));
    const box=h('section',{'data-observation-kind':kind},h('h4',{},t(kind==='github'?'pmo_github':'pmo_workload')));
    if(!row){box.append(emptyState({title:t(value.visibility_filtered?'pmo_hidden':'pmo_empty')}));return box;}
    const doc=row.document,id=kind==='github'?'observation_id':'snapshot_id',prefix=kind==='github'?'pmgh_':'pmwl_';
    if(row.workspace_id!==workspaceId||doc?.workspace_id!==workspaceId||doc[id]!==row[id]||!new RegExp(`^${prefix}[0-9a-f]{32}$`).test(row[id])
      ||!/^op_[0-9a-f]{32}$/.test(row.operation_id)||!Number.isFinite(doc.as_of)||!Number.isFinite(doc.expires_at))throw Error(t('pmo_invalid'));
    box.append(line('pmo_as_of',stamp(doc.as_of)),line('pmo_expiry',stamp(doc.expires_at)));
    if(Date.now()/1000>=doc.expires_at||Date.now()/1000<doc.as_of)box.append(h('p',{class:'muted'},t('pmo_expired')));
    if(current()&&(row.policy_id!==policy.policy_id||row.baseline_id!==baseline.baseline_id))box.append(h('p',{class:'muted'},t('pmo_old_context')));
    if(kind==='github'){
      const evidence=doc.evidence;
      if(doc.profile!==profile||!['complete','incomplete'].includes(evidence?.status)
        ||![evidence.observed_open_pulls,evidence.observed_nonterminal_actions].every(n=>Number.isSafeInteger(n)&&n>=0))throw Error(t('pmo_invalid'));
      box.append(line('pmo_coverage',t(`pmo_${evidence.status}`)),line('pmo_open_prs',evidence.observed_open_pulls),
        line('pmo_actions',evidence.observed_nonterminal_actions),h('p',{class:'muted'},t('pmo_profile_scope')));
      if(evidence.status==='incomplete')box.append(h('p',{},t('pmo_partial_counts')));
    }else{
      if(!['development','unknown','idle_candidate'].includes(doc.classification)||!Array.isArray(doc.source_coverage)
        ||!Array.isArray(doc.blocking_reasons)||typeof doc.stability?.satisfied!=='boolean'
        ||doc.exclusions!=null&&!Array.isArray(doc.exclusions))throw Error(t('pmo_invalid'));
      box.append(line('pmo_classification',t(`pmo_${doc.classification}`)),line('pmo_stability',t(doc.stability.satisfied?'pmo_stable':'pmo_not_stable')));
      const coverage=h('ul');
      for(const source of doc.source_coverage){
        const label=source.kind==='bat_workspace'?'pmo_source_bat':['central_project','github','bat'].includes(source.kind)?`pmo_source_${source.kind}`:'pmo_source_other';
        coverage.append(h('li',{},`${t(label)}: ${t(source.status==='complete'?'pmo_complete':'pmo_incomplete')}`));
      }
      box.append(coverage,line('pmo_blockers',doc.blocking_reasons.length),h('p',{class:'muted'},t('pmo_capture_limits')));
      if(doc.blocking_reasons.length)box.append(h('details',{},h('summary',{},t('pmo_blocker_details')),
        h('ul',{},...doc.blocking_reasons.map(reason=>{
          if(!object(reason)||!text(reason.code))throw Error(t('pmo_invalid'));
          return h('li',{},reasonText(reason),h('code',{class:'pm-source'},' · '+reason.code),sourceReference(reason.source));
        }))));
      if(doc.exclusions?.length)box.append(h('details',{'data-workload-exclusions':''},h('summary',{},t('pmo_exclusions')+` (${doc.exclusions.length})`),
        h('p',{class:'muted'},t('pmo_exclusion_boundary')),h('ul',{},...doc.exclusions.map(exclusion))));
    }
    box.append(h('details',{},h('summary',{},t('pmo_proof')),h('p',{class:'mono'},row[id]),h('p',{class:'mono'},row.document_digest),
      h('a',{href:`#/op/${encodeURIComponent(row.operation_id)}`},t('pm_operation'))));return box;
  }
  function withdraw(){epoch++;ready=false;policy=null;baseline=null;lifecycle=null;facts.replaceChildren();context.replaceChildren();status.replaceChildren();update();}
  function failure(error){withdraw();status.append(errorBox(error,{summary:t('pmo_unavailable'), within:status}));}
  async function refresh(){
    withdraw();const ticket=epoch;
    try{
      const paths=['maintenance-policies/current','release-baselines/current','lifecycle','workload-snapshots/current',...(githubAvailable()?['github-observations/current']:[])];
      const values=await Promise.all(paths.map(path=>api('GET',`${base}/${path}`)));if(!valid(ticket))return;
      const [p,b,l,w,g]=values.map(check);
      if(typeof p.visibility_filtered!=='boolean'||typeof b.visibility_filtered!=='boolean'||!Number.isSafeInteger(l.lifecycle?.revision)
        ||!Number.isSafeInteger(p.head?.revision)||!Number.isSafeInteger(b.head?.version)
        ||p.policy&&(p.policy.workspace_id!==workspaceId||p.policy.document?.workspace_id!==workspaceId||p.policy.policy_id!==p.head.policy_id||p.policy.revision!==p.head.revision)
        ||b.baseline&&(b.baseline.workspace_id!==workspaceId||b.baseline.baseline_id!==b.head.baseline_id||b.baseline.version!==b.head.version)
        ||p.visibility_filtered&&p.policy!==null||b.visibility_filtered&&b.baseline!==null)throw Error(t('pmo_invalid'));
      policy=p.visibility_filtered?null:p.policy;baseline=b.visibility_filtered?null:b.baseline;lifecycle=l.lifecycle;ready=true;
      // Build all rows before rendering; a malformed or withdrawn source clears the whole projection.
      const rows=[...(g?[record(g,'github')]:[]),record(w,'workload')];
      if(policy&&caps()?.features?.pm_github_observation?.maintenance_findings?.version===1){
        const data=await api('GET',`${base}/maintenance-findings/current`);if(!valid(ticket))return;
        rows.push(maintenanceFindingsView({h,t,workspaceId,data}));
      }
      facts.replaceChildren(...rows);
      context.textContent=current()?`${t('pmo_request_context')}: ${t('pmp_revision')} ${policy.revision} · ${baseline.document.release_version.value}`:t('pmo_context_missing');update();
    }catch(error){if(valid(ticket))failure(error);}
  }
  return {element,refresh,withdraw};
}
