import {button, disclosure, iconButton, inlineError, statusPill} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
import {maintenanceRepairPolicyControls,maintenanceRepairPolicyView} from './pm-maintenance-repair-policy.js';
import {findingPolicyControls,findingPolicyView} from './pm-maintenance-findings.js';
import {maintenanceChecks} from './pm-maintenance-checks.js';
import {maintenanceObservations} from './pm-maintenance-observations.js';
// One durable decision; selecting sources never runs monitoring or repair work.
export function workspaceMaintenancePolicy({h,t,api,alive,workspaceId,workspace,caps,permitted,form,input,labelled,errorBox,reviewedHandoff=()=>null}) {
  const endpoint=`/pm/workspaces/${encodeURIComponent(workspaceId)}`,action='pm.maintenance_policy.adopt';
  const sourceKinds=['issues','pull_requests','dependencies','documentation','research'],workKinds=['dependency_fix','bug_fix','documentation'];
  let repairSourcesReady=false;let epoch=0,ready=false,current=null,baseline=null,lifecycle=null,editor=null,syncing=false,historyLoaded=false,historyCursor=null,historyFiltered=false;
  const view=h('div',{'data-maintenance-current':''}),status=h('div',{role:'status'}),source=h('div',{'data-maintenance-source':''}),history=h('div');
  const check=value=>{if(value?.workspace_id!==workspaceId)throw Error(t('pmp_invalid'));return value;};
  const valid=ticket=>{try{alive();return ticket===epoch;}catch{return false;}};
  const owner=()=>permitted(true)&&workspace()?.membership?.role==='owner'&&caps()?.scopes?.includes('manage');
  const recover=()=>Boolean(workspace()?.membership)&&caps()?.scopes?.includes('manage');
  const githubProfile='github_open_prs_and_actions_v1';
  const profileAvailable=()=>caps()?.features?.pm_github_observation?.profiles?.includes(githubProfile)===true;
  const allowed=()=>owner()&&caps()?.features?.pm_workspaces?.maintenance_policy?.policy_adoption===true;
  const stopped=()=>!lifecycle||['paused','closed'].includes(lifecycle.stage)||['pausing','closing','paused','closed'].includes(lifecycle.status);
  const stamp=value=>timeStamp(value);
  const hidden=()=>h('input',{type:'hidden'});
  const select=(label,options)=>h('select',{'aria-label':t(label)},...options.map(([value,key])=>h('option',{value},t(key))));
  const bool=label=>select(label,[['true','pmp_yes'],['false','pmp_no']]);
  const numeric=(label,low,high)=>{const field=input(label,{required:false,max:16});field.type='number';field.min=low;field.max=high;field.step=1;return field;};
  const fields={original_policy:hidden(),initialized:hidden(),context:hidden(),enabled:select('pmp_enabled',[['false','pmp_enabled_no'],['true','pmp_enabled_yes']]),
    after:select('pmp_after',['manual','maintain_when_idle','continue_development'].map(key=>[key,`pmp_${key}`])),sources:hidden(),works:hidden(),profile:select('pmp_github_profile',[['','pmp_profile_choose'],...(profileAvailable()?[[githubProfile,'pmp_profile_github']]:[])]),
    paths:input('pmp_paths',{multiline:true,required:false,max:51300}),delivery:select('pmp_delivery',['report','pull_request','merge','deploy'].map(key=>[key,`pmp_${key}`])),
    environment:input('pmp_environment',{required:false,max:256}),ci:bool('pmp_ci'),review:bool('pmp_review'),
    acceptance:input('pmp_acceptance',{multiline:true,required:false,max:80040}),timezone:input('pmp_timezone',{required:false,max:128}),
    interval:numeric('pmp_interval',60,2678400),weekday:select('pmp_weekday',Array.from({length:7},(_,day)=>[String(day),`pmp_day_${day}`])),time:input('pmp_time',{required:false,max:5}),
    age:numeric('pmp_max_age',1,86400),stable:numeric('pmp_stable',0,604800),parallel:numeric('pmp_parallel',1,16),run:numeric('pmp_run_seconds',1,86400),
    period:numeric('pmp_period_seconds',60,2678400),runs:numeric('pmp_period_runs',1,1000),cost:input('pmp_cost',{required:false,max:20}),retries:numeric('pmp_retries',0,10),
    rationale:input('pmp_rationale',{multiline:true,required:false,max:4000})};
  const findingControls=findingPolicyControls({h,t,input,labelled,available:()=>caps()?.features?.pm_github_observation?.maintenance_findings?.version===1});
  Object.assign(fields,findingControls.fields);
  const repairControls=maintenanceRepairPolicyControls({h,t,input,labelled,reviewedHandoff,baseline:()=>baseline?.baseline,
    available:()=>caps()?.features?.pm_workspaces?.maintenance_repairs?.version===1,
    ciAvailable:()=>caps()?.features?.pm_github_observation?.required_ci?.profiles?.includes('github_required_status_checks_v1')===true,
    editable:()=>ready&&allowed()&&!editor?.locked(),changed:()=>editor?.changed()});
  Object.assign(fields,repairControls.fields);
  const selected=(field)=>{try{const values=JSON.parse(field.value);return Array.isArray(values)?values:[];}catch{return [];}};
  const checks=[];
  function choices(field,values,label){
    return h('fieldset',{class:'pm-policy-choices'},h('legend',{},t(label)),...values.map(value=>{
      const check=h('input',{type:'checkbox','aria-label':`${t(label)}: ${t(`pmp_${value}`)}`,onchange:()=>{
        if(!editor||editor.locked()||!allowed())return;
        field.value=JSON.stringify(checks.filter(item=>item.field===field&&item.check.checked).map(item=>item.value));editor.changed();
      }});checks.push({field,value,check});return h('label',{},check,t(`pmp_${value}`));
    }));
  }
  const profileLabel=labelled('pmp_github_profile',fields.profile);
  const profileHelp=h('p',{class:'muted'},t(profileAvailable()?'pmp_profile_help':'pmp_profile_unavailable'));
  const envLabel=labelled('pmp_environment',fields.environment);
  const group=(name,...nodes)=>disclosure({element:h('details',{'data-policy-group':name},h('summary',{},t(name)),...nodes)});
  const grid=(...names)=>h('div',{class:'pm-fields'},...names.map(([key,label])=>labelled(label,fields[key])));
  const stale=h('p',{class:'muted',hidden:true},t('pmp_stale'));
  const useVersions=button({variant:'secondary',label:t('pmp_use_versions'),onClick:()=>{
    if(!ready||!allowed()||stopped()||editor.locked()||!context())return;
    if(!fields.original_policy.value)Object.entries(repairControls.initialize(current.policy?.document?.policy||{})).forEach(([key,value])=>editor.set(key,value));
    editor.set('original_policy',JSON.stringify(current.policy?.document?.policy||{}));editor.set('context',JSON.stringify(context()));editor.update();
  }});
  useVersions.hidden=true;
  const eligibility=h('p',{class:'muted'});
  const extra=h('div',{},source,stale,useVersions,grid(['enabled','pmp_enabled'],['after','pmp_after']),
    group('pmp_scope',choices(fields.sources,sourceKinds,'pmp_sources'),h('p',{class:'muted'},t('pmp_sources_help')),
      choices(fields.works,workKinds,'pmp_work'),h('p',{class:'muted'},t('pmp_work_help')),labelled('pmp_paths',fields.paths),h('p',{class:'muted'},t('pmp_paths_help')),
      grid(['delivery','pmp_delivery']),envLabel,h('p',{class:'muted'},t('pmp_delivery_help'))),
    group('pmp_verification',grid(['ci','pmp_ci'],['review','pmp_review']),labelled('pmp_acceptance',fields.acceptance),h('p',{class:'muted'},t('pmp_acceptance_help'))),
    findingControls.element,repairControls.element,
    group('pmp_schedule',profileLabel,profileHelp,grid(['timezone','pmp_timezone'],['interval','pmp_interval'],['weekday','pmp_weekday'],['time','pmp_time'],['age','pmp_max_age'],['stable','pmp_stable'])),
    group('pmp_limits',grid(['parallel','pmp_parallel'],['run','pmp_run_seconds'],['period','pmp_period_seconds'],['runs','pmp_period_runs'],['cost','pmp_cost'],['retries','pmp_retries'])),
    labelled('pmp_rationale',fields.rationale),eligibility);
  function context(){
    return ready&&baseline?.baseline&&!baseline.visibility_filtered&&current&&!current.visibility_filtered&&lifecycle
      ? {expected_policy_revision:current.head.revision,expected_previous_policy_id:current.head.policy_id,
        expected_baseline_id:baseline.baseline.baseline_id,expected_lifecycle_revision:lifecycle.revision}:null;
  }
  const matches=()=>context()&&fields.context.value===JSON.stringify(context());
  const editorApi=async(...args)=>{const ticket=epoch;try{return await api(...args);}catch(error){if(valid(ticket)&&[401,403,404].includes(error.status))failure(error);throw error;}};
  editor=form({action,target:{workspace_id:workspaceId},fields,api:editorApi,
    params:values=>({policy:policy(values),rationale:required(values.rationale,4000)}),preconditions:()=>JSON.parse(fields.context.value),
    permitted:()=>Boolean(allowed()&&ready&&!stopped()&&matches()),recoverPermitted:recover,label:'pmp_save',extra,
    onSaved:()=>refresh(),onStateChange:()=>{if(editor&&!syncing)editor.update();}});
  editor.box.noValidate=true;
  const baseUpdate=editor.update;
  editor.update=()=>{if(syncing)return;syncing=true;try{
    if(ready&&allowed()&&!editor.locked()&&!fields.initialized.value&&context())initialize();
    // Upgrade old drafts only when their original decision versions still match.
    if(ready&&matches()&&!editor.locked()&&!fields.original_policy.value){
      fields.original_policy.value=JSON.stringify(current.policy?.document?.policy||{});
      Object.entries(repairControls.initialize(current.policy?.document?.policy||{})).forEach(([key,value])=>fields[key].value=value);
      editor.changed();
    }
    baseUpdate();
    const editing=ready&&allowed()&&!editor.locked();
    for(const [key,field]of Object.entries(fields))if(!['context','initialized'].includes(key))field.disabled=!editing;
    for(const item of checks){item.check.checked=selected(item.field).includes(item.value);item.check.disabled=!editing;}
    profileLabel.hidden=!profileAvailable();fields.profile.disabled=!editing||!profileAvailable();
    envLabel.hidden=fields.delivery.value!=='deploy';fields.environment.disabled=!editing||envLabel.hidden;
    repairControls.update(ready&&repairSourcesReady);
    stale.hidden=editor.locked()||!ready||!fields.context.value||Boolean(matches());
    useVersions.hidden=stale.hidden||!context();useVersions.disabled=!editing||stopped();
    editorDetails.hidden=!owner()&&!(editor.locked()&&recover());permission.hidden=owner();
    eligibility.textContent=!allowed()?t('pmp_owner'):!ready?'':stopped()?t('pmp_stopped'):!baseline?.baseline?t(baseline?.visibility_filtered?'pmp_baseline_hidden':'pmp_no_baseline'):current?.visibility_filtered?t('pmp_hidden'):'';
  }finally{syncing=false;}};
  fields.delivery.addEventListener('change',()=>editor.update());
  const editorDetails=disclosure({element:h('details',{'data-maintenance-editor':''},h('summary',{},t('pmp_edit')),editor.box)});
  const historyDetails=disclosure({element:h('details',{},h('summary',{},t('pmp_history')),history)});
  historyDetails.addEventListener('toggle',()=>{if(historyDetails.open&&!historyLoaded&&ready)void readHistory(false);});
  const permission=h('p',{class:'muted'},t('pmp_owner'));
  const maintenanceRuns=caps()?.features?.pm_workspaces?.maintenance_checks?.version===1
    ?maintenanceChecks({h,t,api,alive,workspaceId,workspace,caps,permitted,form,errorBox}):null;
  const observations=caps()?.features?.pm_workspaces?.workload?.version===1
    ?maintenanceObservations({h,t,api,alive,workspaceId,workspace,caps,permitted,form,errorBox}):null;
  const headingState=h('div',{class:'pm-answer'},h('h3',{},t('pmp_title')));
  const runtimeText=()=>t(caps()?.features?.pm_workspaces?.maintenance_repairs?.version===1?'pmr_policy_runtime':maintenanceRuns?'pmc_runtime_available':'pmp_runtime');
  function paintPolicyHeading(tone){headingState.replaceChildren();
    if(tone)headingState.append(h('h3',{},t('pmp_current')),statusPill(tone));
    else if(ready&&current)headingState.append(h('h3',{},t(current.visibility_filtered||current.head?.revision?'pmp_hidden':'pmp_empty')));
    else headingState.append(h('h3',{},t('pmp_title')));}
  const element=h('section',{class:'pm-brief pm-maintenance-policy','data-pm-maintenance-policy':''},h('header',{class:'pm-heading'},
    headingState,iconButton({icon:'refresh-cw', label:t('pmp_refresh'), ...{type:'button',onclick:()=>void refresh()}})),
    status,view,historyDetails,editorDetails,...(maintenanceRuns?[maintenanceRuns.element]:[]),...(observations?[observations.element]:[]));
  function controls(){editorDetails.hidden=!owner()&&!(editor.locked()&&recover());permission.hidden=owner();editor.update();}
  function initialize(){
    const p=current.policy?.document?.policy||{enabled:false,after_release_policy:'manual',sources:['issues','pull_requests'],allowed_work_kinds:[],allowed_paths:['.'],
      delivery:{maximum:'report',environment:null},verification:{required_ci:true,review_required:true,acceptance_evidence:[]},
      schedule:{timezone:'UTC',check_interval_seconds:3600,review_weekday:0,review_local_time:'09:00'},observation:{max_age_seconds:1800,stable_for_seconds:300},
      limits:{max_parallel:1,run_seconds:1800,period_seconds:86400,period_runs:4,cost_microusd:null,retries:0}};
    const values={original_policy:JSON.stringify(p),initialized:'1',context:JSON.stringify(context()),enabled:String(p.enabled),after:p.after_release_policy,sources:JSON.stringify(p.sources),works:JSON.stringify(p.allowed_work_kinds),paths:p.allowed_paths.join('\n'),
      delivery:p.delivery.maximum,environment:p.delivery.environment||'',ci:String(p.verification.required_ci),review:String(p.verification.review_required),acceptance:p.verification.acceptance_evidence.join('\n'),
      profile:p.observation.github_profile||'',timezone:p.schedule.timezone,interval:p.schedule.check_interval_seconds,weekday:p.schedule.review_weekday,time:p.schedule.review_local_time,age:p.observation.max_age_seconds,stable:p.observation.stable_for_seconds,
      parallel:p.limits.max_parallel,run:p.limits.run_seconds,period:p.limits.period_seconds,runs:p.limits.period_runs,cost:p.limits.cost_microusd===null?'':String(p.limits.cost_microusd/1000000),retries:p.limits.retries,rationale:''};
    Object.assign(values,findingControls.initialize(p.observation),repairControls.initialize(p));
    for(const [key,value]of Object.entries(values))fields[key].value=String(value);editor.changed();
  }
  function required(value,max){if(typeof value!=='string'||!value.trim()||[...value].length>max||value.includes('\0'))throw Error(t('pmp_validation'));return value;}
  function policy(v){
    const integer=(value,low,high)=>{const n=Number(value);if(!/^\d+$/.test(value)||!Number.isSafeInteger(n)||n<low||n>high)throw Error(t('pmp_validation'));return n;};
    const list=(value,max,chars)=>{const rows=value.split('\n').map(line=>line.trim()).filter(Boolean);if(!rows.length||rows.length>max)throw Error(t('pmp_validation'));rows.forEach(line=>required(line,chars));return rows;};
    const selection=(value,options,empty=false)=>{const rows=JSON.parse(value);if(!Array.isArray(rows)||!empty&&!rows.length||rows.length!==new Set(rows).size||rows.some(x=>!options.includes(x)))throw Error(t('pmp_validation'));return rows;};
    const paths=list(v.paths,100,512);
    if(paths.length!==new Set(paths).size||paths.some(path=>path!=='.'&&(/[\\:*?\[\]\x00-\x1f]/.test(path)||path.replace(/\/$/,'').split('/').some(part=>['','.','..'].includes(part)))))throw Error(t('pmp_validation'));
    if(!['true','false'].includes(v.enabled)||!['true','false'].includes(v.ci)||!['true','false'].includes(v.review)||!['manual','maintain_when_idle','continue_development'].includes(v.after)||!['report','pull_request','merge','deploy'].includes(v.delivery))throw Error(t('pmp_validation'));
    try{new Intl.DateTimeFormat('en',{timeZone:required(v.timezone,128)});}catch{throw Error(t('pmp_validation'));}
    if(!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(v.time))throw Error(t('pmp_validation'));
    let cost=null;if(v.cost){if(!/^\d+(\.\d{1,6})?$/.test(v.cost))throw Error(t('pmp_validation'));const [whole,fraction='']=v.cost.split('.');cost=Number(BigInt(whole)*1000000n+BigInt(fraction.padEnd(6,'0')));if(!Number.isSafeInteger(cost)||cost<1||cost>10**12)throw Error(t('pmp_validation'));}
    if(profileAvailable()&&v.profile!==githubProfile||v.profile&&!profileAvailable())throw Error(t('pmp_profile_required'));
    const works=selection(v.works,workKinds,true);
    if(works.length&&(v.delivery==='report'||baseline?.baseline?.document?.proof?.maintenance_branch?.status!=='verified_at_observation'))throw Error(t('pmp_repair_invalid'));
    const original=JSON.parse(v.original_policy||'{}');
    const finding=findingControls.build(v,selection(v.sources,sourceKinds),v.profile,baseline?.baseline);
    // Preserve optional provider fields that this editor does not understand.
    const merge=(old,value)=>value&&typeof value==='object'&&!Array.isArray(value)
      ?Object.fromEntries(Object.entries({...old,...value}).map(([key,item])=>[key,Object.hasOwn(value,key)?merge(old?.[key],item):item])):value;
    const findingPolicy=merge(original.observation?.maintenance_sources?{maintenance_sources:original.observation.maintenance_sources}:{},finding);
    return {...original,...repairControls.build(v),enabled:v.enabled==='true',after_release_policy:v.after,sources:selection(v.sources,sourceKinds),allowed_work_kinds:works,allowed_paths:paths,
      delivery:{...original.delivery,maximum:v.delivery,environment:v.delivery==='deploy'?required(v.environment,256):null},
      observation:{...original.observation,...(v.repair_ci?{required_ci_profile:v.repair_ci}:{}),max_age_seconds:integer(v.age,1,86400),stable_for_seconds:integer(v.stable,0,604800),...(v.profile?{github_profile:v.profile}:{}),...findingPolicy},
      schedule:{...original.schedule,timezone:v.timezone,check_interval_seconds:integer(v.interval,60,2678400),review_weekday:integer(v.weekday,0,6),review_local_time:v.time},
      limits:{...original.limits,max_parallel:integer(v.parallel,1,16),run_seconds:integer(v.run,1,86400),period_seconds:integer(v.period,60,2678400),period_runs:integer(v.runs,1,1000),cost_microusd:cost,retries:integer(v.retries,0,10)},
      verification:{...original.verification,required_ci:v.ci==='true',review_required:v.review==='true',acceptance_evidence:list(v.acceptance,40,2000)}};
  }
  function fixed(row){
    if(row?.workspace_id!==workspaceId||row.document?.workspace_id!==workspaceId||row.document.policy_id!==row.policy_id||row.document.revision!==row.revision||!/^pmmp_[0-9a-f]{32}$/.test(row.policy_id)||!Number.isSafeInteger(row.revision)||row.document.automatic_execution_started!==false)throw Error(t('pmp_invalid'));
    return row;
  }
  const line=(label,value)=>h('p',{},h('strong',{},`${t(label)}: `),value??t('pmp_none'));
  function record(row,isCurrent=false){
    fixed(row);const doc=row.document,p=doc.policy,s=doc.scope,details=h('details',{},h('summary',{},t('pmp_note')));
    const names=values=>values.length?values.map(value=>t(`pmp_${value}`)).join(' · '):t('pmp_none');
    details.append(line('pmp_after',t(`pmp_${p.after_release_policy}`)),line('pmp_sources',names(p.sources)),h('p',{class:'muted'},t('pmp_sources_help')),
      line('pmp_work',names(p.allowed_work_kinds)),line('pmp_paths',p.allowed_paths.join(' · ')),line('pmp_delivery',t(`pmp_${p.delivery.maximum}`)),
      ...(p.delivery.environment?[line('pmp_environment',p.delivery.environment)]:[]),line('pmp_ci',t(p.verification.required_ci?'pmp_yes':'pmp_no')),line('pmp_review',t(p.verification.review_required?'pmp_yes':'pmp_no')),
      h('p',{},h('strong',{},t('pmp_acceptance'))),h('ul',{},...p.verification.acceptance_evidence.map(text=>h('li',{},text))),
      line('pmp_github_profile',t(s.github_scope===githubProfile?'pmp_profile_github':'pmp_profile_legacy')),h('p',{class:'muted'},t(s.github_scope===githubProfile?'pmp_profile_help':'pmp_profile_legacy_help')),
      line('pmp_timezone',p.schedule.timezone),line('pmp_interval',p.schedule.check_interval_seconds),line('pmp_weekday',t(`pmp_day_${p.schedule.review_weekday}`)),line('pmp_time',p.schedule.review_local_time),
      line('pmp_max_age',p.observation.max_age_seconds),line('pmp_stable',p.observation.stable_for_seconds),
      ...[['parallel','max_parallel'],['run_seconds','run_seconds'],['period_seconds','period_seconds'],['period_runs','period_runs'],['retries','retries']].map(([label,key])=>line(`pmp_${label}`,p.limits[key])),
      line('pmp_cost',p.limits.cost_microusd===null?t('pmp_cost_none'):String(p.limits.cost_microusd/1000000)),line('pmp_reason',doc.rationale),line('pmp_author',row.actor),line('pmp_recorded',stamp(row.created_at)),
      line('pmp_original_baseline',row.baseline_id),line('pmp_lifecycle',doc.lifecycle_revision),line('pmp_revision',row.revision),
      h('p',{class:'mono'},row.document_digest),h('a',{href:`#/op/${encodeURIComponent(row.operation_id)}`},t('pm_operation')));
    const repairPolicy=maintenanceRepairPolicyView({h,t,policy:p});if(repairPolicy)details.append(repairPolicy);
    const findingPolicy=findingPolicyView({h,t,config:p.observation.maintenance_sources});if(findingPolicy)details.append(findingPolicy);
    details.append(h('p',{},t(p.enabled?'pmp_permitted':'pmp_disabled'),' · ',t(`pmp_${p.delivery.maximum}`)),
      h('p',{class:'muted'},p.allowed_work_kinds.length?names(p.allowed_work_kinds):t('pmp_observation')));
    if(isCurrent){
      details.append(h('p',{class:'muted'},t('pmp_intro')),h('p',{class:'muted'},runtimeText()),permission);
      const branch=baseline?.baseline?.document?.proof?.maintenance_branch;
      if(baseline?.baseline)details.append(h('p',{class:'muted'},branch?.status==='verified_at_observation'?`${t('pmp_branch')}: ${branch.ref}`:t('pmp_no_branch')));
    }
    disclosure({element:details});
    const tone=!p.enabled?'stopped':p.allowed_work_kinds.length?'adopted':'read_only';
    if(isCurrent)paintPolicyHeading(tone);
    return h('article',{'data-maintenance-policy':row.policy_id},
      isCurrent?null:h('div',{class:'pm-answer'},h('h3',{},`${t('pmp_revision')} ${row.revision}`),statusPill(tone)),
      h('p',{},h('strong',{},`${t('pmp_revision')} ${row.revision}`),' · ',s.repository,' · ',s.channel),
      line('pmp_github_profile',t(s.github_scope===githubProfile?'pmp_profile_github':'pmp_profile_legacy')),details);
  }
  function withdraw(){repairSourcesReady=false;maintenanceRuns?.withdraw();observations?.withdraw();epoch++;ready=false;current=null;baseline=null;lifecycle=null;historyLoaded=false;historyCursor=null;historyFiltered=false;view.replaceChildren();source.replaceChildren();history.replaceChildren();paintPolicyHeading(null);controls();}
  function failure(error){withdraw();status.replaceChildren(inlineError({error:t('pmp_unavailable')}),errorBox(error));}
  async function readHistory(append){
    const ticket=epoch,after=append?historyCursor:0;
    try{
      const data=check(await api('GET',`${endpoint}/maintenance-policies?after=${after}&limit=20`));if(!valid(ticket))return;
      if(!Array.isArray(data.policies)||data.next_cursor!==null&&(!Number.isSafeInteger(data.next_cursor)||data.next_cursor<=after))throw Error(t('pmp_invalid'));
      const rows=data.policies.map(row=>record(row));if(!append){history.replaceChildren();historyFiltered=false;}else history.querySelector('[data-policy-more]')?.remove();
      historyLoaded=true;historyCursor=data.next_cursor;historyFiltered ||= data.visibility_filtered;history.append(...rows);history.querySelector('[data-policy-coverage]')?.remove();
      history.append(h('p',{'data-policy-coverage':'',class:'muted'},t(historyFiltered?'pmp_filtered':'pmp_partial')));
      if(historyCursor!==null)history.append(button({'data-policy-more':'',variant:'secondary',label:t('pmp_more'),onClick:event=>{event.currentTarget.disabled=true;void readHistory(true);}}));
    }catch(error){if(valid(ticket))failure(error);}
  }
  async function refresh(){
    withdraw();status.replaceChildren();const ticket=epoch;
    try{
      const responses=await Promise.all([api('GET',`${endpoint}/maintenance-policies/current`),api('GET',`${endpoint}/release-baselines/current`),api('GET',`${endpoint}/lifecycle`)]);
      if(!valid(ticket))return;
      const [p,b,l]=responses.map(check);
      if(!Number.isSafeInteger(p.head?.revision)||p.head.revision<0||!Number.isSafeInteger(l.lifecycle?.revision)||l.lifecycle.revision<0||!Number.isSafeInteger(b.head?.version)
        ||typeof p.visibility_filtered!=='boolean'||typeof b.visibility_filtered!=='boolean'
        ||p.visibility_filtered&&(p.policy!==null||p.head.policy_id!==null)||b.visibility_filtered&&(b.baseline!==null||b.head.baseline_id!==null)
        ||p.policy&&(p.policy.policy_id!==p.head.policy_id||p.policy.revision!==p.head.revision)
        ||!p.policy&&!p.visibility_filtered&&(p.head.policy_id!==null||p.head.revision!==0)
        ||!b.baseline&&!b.visibility_filtered&&(b.head.baseline_id!==null||b.head.version!==0)
        ||b.baseline&&(b.baseline.document?.workspace_id!==workspaceId||b.baseline.document.baseline_id!==b.baseline.baseline_id||b.baseline.document.version!==b.baseline.version||b.baseline.workspace_id!==workspaceId||b.baseline.baseline_id!==b.head.baseline_id||b.baseline.version!==b.head.version))throw Error(t('pmp_invalid'));
      if(p.policy)fixed(p.policy);current=p;baseline=b;lifecycle=l.lifecycle;ready=true;
      if(p.policy)view.replaceChildren(record(p.policy,true));
      else{const details=disclosure({element:h('details',{},h('summary',{},t('pmp_note')),h('p',{class:'muted'},t('pmp_intro')),h('p',{class:'muted'},runtimeText()),permission)});view.replaceChildren(details);paintPolicyHeading(null);}
      if(b.baseline){const doc=b.baseline.document;
        source.append(h('p',{},t('pmp_baseline'),': ',doc.release_version.value,' · ',doc.repository,' · ',doc.channel),
          h('p',{class:'muted'},doc.proof.maintenance_branch.status==='verified_at_observation'?`${t('pmp_branch')}: ${doc.proof.maintenance_branch.ref}`:t('pmp_no_branch')));
      }
      controls();if(!p.visibility_filtered&&!b.visibility_filtered){await repairControls.checkSources({api,endpoint,workspaceId,valid:()=>valid(ticket)});if(!valid(ticket))return;repairSourcesReady=true;controls();}
      if(historyDetails.open)await readHistory(false);if(valid(ticket))await maintenanceRuns?.refresh();if(valid(ticket))await observations?.refresh();
    }catch(error){if(valid(ticket))failure(error);}
  }
  controls();return {element,refresh,withdraw,update:()=>editor.update()};
}
