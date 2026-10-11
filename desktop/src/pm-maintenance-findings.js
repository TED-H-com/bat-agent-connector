const kinds=['issues','dependabot_pull_requests','dependabot_alerts'];
const states=['disabled','missing','complete','incomplete','stale','context_changed','stopped'];
export function findingPolicyView({h,t,config}){
  if(!config)return null;
  const line=(key,value)=>h('p',{class:'pm-note-text'},h('strong',{},t(key)+': '),String(value));
  const deps=config.dependencies;
  return h('section',{'data-finding-policy':''},h('h4',{},t('pmf_configure')),
    line('pmf_issues_enable',t(config.issues?'pmf_on':'pmf_off')),
    config.issues?line('pmf_labels',config.issues.labels_all.length?config.issues.labels_all.join(' · '):t('pmf_all_issues')):null,
    line('pmf_deps_enable',t(deps?'pmf_on':'pmf_off')),
    ...(deps?[line('pmf_manifests',deps.manifests.join(' · ')),line('pmf_branch',deps.base_branch),line('pmf_bot',deps.dependabot_actor_id),
      line('pmf_alerts_enable',t(deps.alerts?'pmf_on':'pmf_off')),line('pmf_pulls_enable',t(deps.pull_requests?'pmf_on':'pmf_off'))]:[]));
}
export function findingPolicyControls({h,t,input,labelled,available}){
  const toggle=key=>h('select',{'aria-label':t(key)},h('option',{value:'false'},t('pmf_off')),h('option',{value:'true'},t('pmf_on')));
  const fields={finding_configured:h('input',{type:'hidden'}),finding_issues:toggle('pmf_issues_enable'),
    finding_labels:input('pmf_labels',{multiline:true,required:false,max:2020}),finding_dependencies:toggle('pmf_deps_enable'),
    finding_manifests:input('pmf_manifests',{multiline:true,required:false,max:51300}),finding_branch:input('pmf_branch',{required:false,max:256}),
    finding_bot:input('pmf_bot',{required:false,max:16}),finding_alerts:toggle('pmf_alerts_enable'),finding_pulls:toggle('pmf_pulls_enable')};
  const labels={finding_issues:'pmf_issues_enable',finding_labels:'pmf_labels',finding_dependencies:'pmf_deps_enable',
    finding_manifests:'pmf_manifests',finding_branch:'pmf_branch',finding_bot:'pmf_bot',finding_alerts:'pmf_alerts_enable',finding_pulls:'pmf_pulls_enable'};
  const element=h('details',{'data-policy-group':'pmf_sources',hidden:!available()},h('summary',{},t('pmf_configure')),
    h('p',{class:'muted'},t('pmf_configure_help')),...Object.entries(labels).map(([key,label])=>labelled(label,fields[key])),h('p',{class:'muted'},t('pmf_permission')));
  function initialize(observation){const c=observation.maintenance_sources,d=c?.dependencies;return {
    finding_configured:c?'1':'',finding_issues:String(c?.issues!=null),finding_labels:c?.issues?.labels_all?.join('\n')||'',
    finding_dependencies:String(d!=null),finding_manifests:d?.manifests?.join('\n')||'',finding_branch:d?.base_branch||'',
    finding_bot:d?.dependabot_actor_id||'',finding_alerts:String(d?.alerts===true),finding_pulls:String(d?.pull_requests===true)};}
  function build(v,sources,profile,baseline){
    const issues=v.finding_issues==='true',deps=v.finding_dependencies==='true';
    if(!v.finding_configured&&!issues&&!deps)return {};
    const invalid=()=>{throw Error(t('pmf_config_invalid'));};
    if(!available()||profile!=='github_open_prs_and_actions_v1')invalid();
    const lines=(value,max,length)=>{const rows=value.split('\n').map(s=>s.trim()).filter(Boolean);
      if(rows.length>max||new Set(rows).size!==rows.length||rows.some(s=>s.length>length||/[\x00-\x1f]/.test(s)))invalid();return rows;};
    const labels=issues?lines(v.finding_labels,20,100):[];
    if(issues&&!sources.includes('issues')||deps&&!sources.includes('dependencies'))invalid();
    let dependencies=null;
    if(deps){const manifests=lines(v.finding_manifests,100,512),bot=Number(v.finding_bot),branch=v.finding_branch;
      if(!manifests.length||manifests.some(path=>path.includes('\\')||path.split('/').some(part=>['','.','..'].includes(part)))
        ||!/^\d+$/.test(v.finding_bot)||!Number.isSafeInteger(bot)||bot<1
        ||baseline?.document?.proof?.maintenance_branch?.status!=='verified_at_observation'
        ||baseline.document.proof.maintenance_branch.ref!=='refs/heads/'+branch
        ||v.finding_alerts!=='true'&&v.finding_pulls!=='true')invalid();
      dependencies={manifests,base_branch:branch,dependabot_actor_id:bot,alerts:v.finding_alerts==='true',pull_requests:v.finding_pulls==='true'};
    }
    return {maintenance_sources:{issues:issues?{labels_all:labels}:null,dependencies}};
  }
  return {element,fields,initialize,build};
}

export function maintenanceFindingsView({h,t,workspaceId,data}){
  const invalid=()=>{throw Error(t('pmf_invalid'));};
  if(data?.workspace_id!==workspaceId||data.automatic_repair_authorized!==false||typeof data.visibility_filtered!=='boolean'
    ||!data.sources||Object.keys(data.sources).some(k=>!kinds.includes(k)))invalid();
  const ref=data.observation_ref;
  if(ref&&(!/^pmgh_[0-9a-f]{32}$/.test(ref.observation_id)||! /^[0-9a-f]{64}$/.test(ref.document_digest)))invalid();
  const rows=[];
  for(const kind of kinds){const source=data.sources[kind];
    if(!source||!states.includes(source.status)||!Array.isArray(source.findings)||source.findings.length>2000
      ||data.visibility_filtered&&source.findings.length)invalid();
    const items=source.findings.map(row=>{
      const native=row.native;
      if(!ref||!/^pmfn_[0-9a-f]{32}$/.test(row.finding_id)||native?.kind!==kind||!Number.isSafeInteger(native.number)||native.number<1
        ||!Number.isSafeInteger(native.repository_id)||native.repository_id<1||! /^[0-9a-f]{64}$/.test(native.provider_digest))invalid();
      const facts=row.facts||{},detail=h('details',{},h('summary',{},`${t('pmf_'+kind)} #${native.number}`),
        h('p',{class:'muted'},t('pmf_applicability')),h('p',{class:'pm-source'},row.finding_id));
      for(const key of ['package','ecosystem','manifest_path','severity','vulnerable_version_range','first_patched_version'])
        if(facts[key]!=null)detail.append(h('p',{},h('strong',{},t('pmf_'+key)+': '),String(facts[key])));
      if(row.existing_pull_request)detail.append(h('p',{},t('pmf_existing_pr')+' #'+row.existing_pull_request.number));
      for(const work of row.existing_work||[])detail.append(h('p',{class:'pm-source'},t('pmf_existing_work')+': '+work.work_item_id));
      return detail;
    });
    rows.push(h('section',{},h('h5',{},t('pmf_'+kind)),h('p',{},t('pmf_state_'+source.status)),
      source.status==='complete'&&!items.length?h('p',{class:'muted'},t('pmf_empty')):null,...items));
  }
  return h('section',{'data-maintenance-findings':''},h('h4',{},t('pmf_title')),h('p',{class:'muted'},t('pmf_boundary')),
    ...(data.visibility_filtered?[h('p',{class:'muted'},t('pmf_hidden'))]:[]),...rows,
    ref?h('details',{},h('summary',{},t('pmf_original')),h('p',{class:'pm-source'},ref.observation_id),h('p',{class:'pm-source'},ref.document_digest)):null);
}
