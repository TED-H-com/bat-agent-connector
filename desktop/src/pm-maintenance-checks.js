import {timeStamp} from './ui/stamps.js';
// The central owns cadence and execution. This view reads facts and submits one fixed intent.
export function maintenanceChecks({h,t,api,alive,workspaceId,workspace,caps,permitted,form,errorBox}) {
  const base=`/pm/workspaces/${encodeURIComponent(workspaceId)}`,profile='github_open_prs_and_actions_v1';
  let epoch=0,ready=false,policy=null,baseline=null,lifecycle=null,schedule=null,historyLoaded=false,cursor=null,filtered=false;
  const status=h('div',{role:'status'}),facts=h('div',{class:'pm-check-facts'}),context=h('p',{class:'muted'}),history=h('div');
  const check=value=>{if(value?.workspace_id!==workspaceId)throw Error(t('pmc_invalid'));return value;};
  const valid=ticket=>{try{alive();return ticket===epoch;}catch{return false;}};
  const owner=()=>permitted(true)&&workspace()?.membership?.role==='owner'&&['observe','manage'].every(scope=>caps()?.scopes?.includes(scope));
  const recover=()=>Boolean(workspace()?.membership)&&caps()?.scopes?.includes('manage');
  const current=()=>ready&&policy&&baseline&&lifecycle;
  const stage=()=>['development','maintain'].includes(lifecycle?.stage);
  const canCheck=()=>Boolean(current()&&owner()&&stage()&&policy.document.policy.enabled===true&&policy.document.scope.github_scope===profile&&schedule&&!schedule.blocked_reason);
  const stamp=value=>timeStamp(value, {empty:t('pmc_unknown_time')});
  const line=(label,value)=>h('p',{},h('strong',{},`${t(label)}: `),value);
  const opLink=id=>h('a',{href:`#/op/${encodeURIComponent(id)}`},t('pm_operation'));
  const isId=(value,prefix)=>typeof value==='string'&&new RegExp(`^${prefix}_[0-9a-f]{32}$`).test(value);
  const blocker=code=>t('pmc_block_'+({PM_MAINTENANCE_AUTHORITY_UNAVAILABLE:'authority',PM_MAINTENANCE_CONTROLLER_CHANGED:'authority',FORBIDDEN:'authority',
    PM_MAINTENANCE_STAGE_STOPPED:'stage',PM_LIFECYCLE_STOPPED:'stage',PM_LIFECYCLE_EPOCH_CHANGED:'changed',PM_MAINTENANCE_DISABLED:'disabled',
    PM_MAINTENANCE_SOURCES_UNAVAILABLE:'sources',PM_MAINTENANCE_BUDGET_EXHAUSTED:'budget',PM_MAINTENANCE_DEADLINE:'deadline',
    PM_MAINTENANCE_PARENT_STOPPED:'stopped',PARENT_STOPPED:'stopped',SOURCE_COVERAGE_INCOMPLETE:'coverage',PM_MAINTENANCE_CONTEXT_CHANGED:'changed',
    PM_MAINTENANCE_CHILD_UNSETTLED:'unsettled'}[code]||'unknown'));
  const request=async(...args)=>{const ticket=epoch;try{return await api(...args);}catch(error){
    if(valid(ticket)&&[401,403,404].includes(error.status))failure(error);throw error;
  }};
  const editor=form({action:'pm.maintenance.check',target:{workspace_id:workspaceId},fields:{},api:request,
    params:()=>({policy_id:policy.policy_id}),preconditions:()=>({expected_policy_version:policy.revision,
      expected_baseline_id:baseline.baseline_id,expected_lifecycle_revision:lifecycle.revision}),
    permitted:canCheck,recoverPermitted:recover,label:'pmc_check',extra:context,onSaved:()=>refresh()});
  editor.box.querySelector('button[type=submit]').className='secondary';
  const historyDetails=h('details',{'data-check-history':''},h('summary',{},t('pmc_history')),history);
  historyDetails.addEventListener('toggle',()=>{if(historyDetails.open&&!historyLoaded&&ready)void readHistory(false);});
  const element=h('div',{'data-pm-maintenance-checks':''},h('header',{class:'pm-heading'},h('h3',{},t('pmc_title')),
    h('button',{type:'button',class:'secondary',onclick:()=>void refresh()},t('pmc_refresh'))),status,
    h('p',{class:'muted'},t('pmc_intro')),facts,editor.box,historyDetails);
  function run(row){
    if(row?.workspace_id!==workspaceId||!isId(row.run_id,'pmck')||!isId(row.operation_id,'op')||!isId(row.policy_id,'pmmp')
      ||!isId(row.baseline_id,'pmrb')||!Number.isSafeInteger(row.policy_version)||!Number.isSafeInteger(row.lifecycle_revision)||!Number.isSafeInteger(row.execution_epoch)||typeof row.capacity_reserved!=='boolean'
      ||!Number.isFinite(row.created_at)||!Number.isFinite(row.deadline_at)||typeof row.operation_status!=='string')throw Error(t('pmc_invalid'));
    const box=h('article',{'data-check-run':row.run_id}),doc=row.document,proof=h('details',{},h('summary',{},t('pmc_proof')));
    if(doc){
      if(doc.workspace_id!==workspaceId||doc.run_id!==row.run_id||doc.policy_id!==row.policy_id||doc.policy_version!==row.policy_version
        ||doc.baseline_id!==row.baseline_id||doc.lifecycle_revision!==row.lifecycle_revision||doc.execution_epoch!==row.execution_epoch
        ||!['complete','incomplete','stopped'].includes(doc.outcome)||!Array.isArray(doc.children)||doc.children.length>2
        ||!Number.isFinite(doc.finished_at)||row.capacity_reserved||doc.automatic_maintenance_authorized!==false||doc.repair_started!==false)throw Error(t('pmc_invalid'));
      box.append(h('p',{class:'pm-check-outcome'},t(`pmc_${doc.outcome}`)),line('pmc_finished',stamp(doc.finished_at)));
      if(doc.reason)box.append(h('p',{class:'muted'},blocker(doc.reason)));
      const children=h('ul',{class:'pm-check-children'});
      for(const child of doc.children){
        if(!isId(child.operation_id,'op')||!['pm.github.observe','pm.workload.capture'].includes(child.action)||!['succeeded','failed','cancelled'].includes(child.status))throw Error(t('pmc_invalid'));
        children.append(h('li',{},t(child.action==='pm.github.observe'?'pmc_github':'pmc_workload'),' · ',t(`pmc_status_${['succeeded','failed','cancelled'].includes(child.status)?child.status:'unknown'}`),' · ',opLink(child.operation_id)));
      }
      box.append(children);proof.append(h('p',{class:'muted'},t('pmc_cost')));
    }else box.append(h('p',{class:'pm-check-outcome'},t('pmc_pending')),line('pmc_deadline',stamp(row.deadline_at)));
    box.append(h('p',{class:'muted'},t(row.capacity_reserved?'pmc_reserved':'pmc_released')));
    if(current()&&(row.policy_id!==policy.policy_id||row.baseline_id!==baseline.baseline_id||row.lifecycle_revision!==lifecycle.revision))box.append(h('p',{class:'muted'},t('pmc_old')));
    proof.append(line('pmc_started',stamp(row.created_at)),line('pmp_revision',row.policy_version),line('pmp_lifecycle',row.lifecycle_revision),
      h('p',{class:'mono'},row.run_id),h('p',{class:'mono'},row.baseline_id),...(row.document_digest?[h('p',{class:'mono'},row.document_digest)]:[]),opLink(row.operation_id));
    box.append(proof);return box;
  }
  function scheduleView(value){
    check(value);const item=value.schedule;
    if(typeof value.visibility_filtered!=='boolean'||value.visibility_filtered&&item!==null)throw Error(t('pmc_invalid'));
    const box=h('section',{'data-check-schedule':''},h('h4',{},t('pmc_schedule')));
    if(!item){box.append(h('p',{class:'muted'},t(value.visibility_filtered?'pmc_hidden':'pmc_no_schedule')));return box;}
    if(!isId(item.schedule_id,'pmms')||!isId(item.policy_id,'pmmp')||!Number.isSafeInteger(item.policy_version)||!Number.isFinite(item.next_due)
      ||typeof item.overdue!=='boolean'||item.blocked_reason!==null&&typeof item.blocked_reason!=='string'||item.automatic_repairs!==false
      ||item.missed_windows!=='coalesce_latest'||!policy||policy&&(policy.policy_id!==item.policy_id||policy.revision!==item.policy_version))throw Error(t('pmc_invalid'));
    schedule=item;
    box.append(line('pmc_next',stamp(item.next_due)),h('p',{class:'muted'},item.blocked_reason?blocker(item.blocked_reason):t(item.overdue?'pmc_overdue':'pmc_scheduled')),
      line('pmc_last_success',item.last_success===null?t('pmc_no_success'):stamp(item.last_success)));
    if(item.latest_window){
      const window=item.latest_window;
      if(!isId(window.operation_id,'op')||!Number.isSafeInteger(window.coalesced_windows)||window.coalesced_windows<0)throw Error(t('pmc_invalid'));
      if(window.coalesced_windows)box.append(line('pmc_skipped',window.coalesced_windows),h('p',{class:'muted'},t('pmc_coalesced')));
      box.append(h('details',{},h('summary',{},t('pmc_window')),line('pmc_due',stamp(window.scheduled_at)),line('pmc_admitted',stamp(window.admitted_at)),opLink(window.operation_id)));
    }
    return box;
  }
  function update(){
    context.textContent=!ready?'':!owner()?t('pmc_owner'):!current()?t('pmc_context_missing'):!stage()?t('pmc_block_stage'):
      policy.document.policy.enabled!==true?t('pmc_block_disabled'):policy.document.scope.github_scope!==profile?t('pmc_block_sources'):
      schedule?.blocked_reason?blocker(schedule.blocked_reason):`${t('pmc_context')}: ${t('pmp_revision')} ${policy.revision} · ${baseline.document.release_version.value}`;
    context.hidden=Boolean(schedule?.blocked_reason&&context.textContent===blocker(schedule.blocked_reason));editor.update();
  }
  function withdraw(){epoch++;ready=false;policy=null;baseline=null;lifecycle=null;schedule=null;historyLoaded=false;cursor=null;filtered=false;facts.replaceChildren();history.replaceChildren();status.replaceChildren();context.replaceChildren();update();}
  function failure(error){withdraw();status.append(h('p',{class:'muted'},t('pmc_unavailable')),errorBox(error));}
  async function readHistory(append){
    const ticket=epoch,after=append?cursor:0;
    try{
      const data=check(await api('GET',`${base}/maintenance-checks?after=${after}&limit=20`));if(!valid(ticket))return;
      if(!Array.isArray(data.runs)||typeof data.visibility_filtered!=='boolean'||data.next_cursor!==null&&(!Number.isSafeInteger(data.next_cursor)||data.next_cursor<=after))throw Error(t('pmc_invalid'));
      const rows=data.runs.map(run);if(!append){history.replaceChildren();filtered=false;}else history.querySelector('[data-check-more]')?.remove();
      historyLoaded=true;cursor=data.next_cursor;filtered ||= data.visibility_filtered;history.append(...rows);history.querySelector('[data-check-coverage]')?.remove();
      history.append(h('p',{'data-check-coverage':'',class:'muted'},t(filtered?'pmc_filtered':'pmc_partial')));
      if(cursor!==null)history.append(h('button',{'data-check-more':'',type:'button',class:'secondary',onclick:event=>{event.currentTarget.disabled=true;void readHistory(true);}},t('pmc_more')));
    }catch(error){if(valid(ticket))failure(error);}
  }
  async function refresh(){
    withdraw();const ticket=epoch;
    try{
      const [p,b,l,s,r]=(await Promise.all(['maintenance-policies/current','release-baselines/current','lifecycle','maintenance-schedule','maintenance-checks/current']
        .map(path=>api('GET',`${base}/${path}`)))).map(check);if(!valid(ticket))return;
      if(typeof p.visibility_filtered!=='boolean'||typeof b.visibility_filtered!=='boolean'||!Number.isSafeInteger(l.lifecycle?.revision)
        ||!Number.isSafeInteger(p.head?.revision)||!Number.isSafeInteger(b.head?.version)
        ||p.policy&&(p.policy.workspace_id!==workspaceId||p.policy.document?.workspace_id!==workspaceId||p.policy.document.policy_id!==p.policy.policy_id||p.policy.document.revision!==p.policy.revision||p.policy.policy_id!==p.head.policy_id||p.policy.revision!==p.head.revision)
        ||b.baseline&&(b.baseline.workspace_id!==workspaceId||b.baseline.document?.workspace_id!==workspaceId||b.baseline.document.baseline_id!==b.baseline.baseline_id||b.baseline.document.version!==b.baseline.version||b.baseline.baseline_id!==b.head.baseline_id||b.baseline.version!==b.head.version)
        ||p.visibility_filtered&&p.policy!==null||b.visibility_filtered&&b.baseline!==null
        ||typeof r.visibility_filtered!=='boolean'||r.visibility_filtered&&r.run!==null)throw Error(t('pmc_invalid'));
      policy=p.visibility_filtered?null:p.policy;baseline=b.visibility_filtered?null:b.baseline;lifecycle=l.lifecycle;ready=true;
      const next=scheduleView(s),latest=h('section',{'data-check-latest':''},h('h4',{},t('pmc_latest')),
        r.run?run(r.run):h('p',{class:'muted'},t(r.visibility_filtered?'pmc_hidden':'pmc_no_run')));
      facts.replaceChildren(next,latest);update();if(historyDetails.open)await readHistory(false);
    }catch(error){if(valid(ticket))failure(error);}
  }
  return {element,refresh,withdraw};
}
