import {button} from './ui/components.js';
// Repair rules retain the exact reviewed handoff; editing a preview never replaces a saved rule.
export function maintenanceRepairPolicyControls({h,t,input,labelled,available,ciAvailable,reviewedHandoff,baseline,editable,changed}) {
  const hidden=()=>h('input',{type:'hidden'}),fields={repair_rules:hidden(),repair_rules_set:hidden(),repair_key:input('pmr_rule_key',{required:false,max:48}),
    repair_kind:h('select',{'aria-label':t('pmr_work_kind')},...['dependency_fix','bug_fix','documentation'].map(value=>h('option',{value},t('pmp_'+value)))),
    repair_labels:input('pmr_rule_labels',{multiline:true,required:false,max:2020}),repair_paths:input('pmr_rule_paths',{multiline:true,required:false,max:51300}),
    repair_command:input('pmr_rule_command',{required:false,max:4000}),repair_ci:h('select',{'aria-label':t('pmr_ci_profile')},
      h('option',{value:''},t('pmr_ci_unchanged')),h('option',{value:'github_required_status_checks_v1'},t('pmr_ci_explicit')))};
  let visible=false,busy=false,epoch=0;const rows=h('div'),status=h('p',{role:'status',class:'muted'});
  const read=()=>{const value=fields.repair_rules.value?JSON.parse(fields.repair_rules.value):[];if(!Array.isArray(value)||value.length>16)throw Error(t('pmr_invalid'));return value;};
  const selected=()=>{try{return reviewedHandoff();}catch{return null;}};
  const lines=value=>value.split('\n').map(v=>v.trim()).filter(Boolean);
  const add=button({variant:'secondary',label:t('pmr_add_rule'),onClick:()=>void addRule()});
  const element=h('details',{'data-policy-group':'pmr_rules'},h('summary',{},t('pmr_rules')),h('p',{class:'muted'},t('pmr_rules_help')),rows,
    h('div',{class:'pm-fields'},labelled('pmr_rule_key',fields.repair_key),labelled('pmr_work_kind',fields.repair_kind)),
    labelled('pmr_rule_labels',fields.repair_labels),labelled('pmr_rule_paths',fields.repair_paths),labelled('pmr_rule_command',fields.repair_command),
    h('p',{class:'muted'},t('pmr_command_help')),add,status,labelled('pmr_ci_profile',fields.repair_ci),h('p',{class:'muted'},t('pmr_ci_help')));
  async function addRule(){
    if(!visible||!editable()||busy||!available())return;
    const source=selected(),release=baseline(),original=JSON.stringify(source),ticket=++epoch;
    try{
      const rules=read(),key=fields.repair_key.value,labels=lines(fields.repair_labels.value),paths=lines(fields.repair_paths.value),kind=fields.repair_kind.value,command=fields.repair_command.value;
      if(!source?.handoff||!release||source.handoff.params.pm_package_ref?.workspace_id!==release.workspace_id||source.handoff.params.source_sha!==release.document.commit_sha
        ||source.handoff.params.source_ref!==release.document.proof.maintenance_branch.ref||source.handoff.preconditions.repository_id!==release.document.repository_id||rules.length>=16||! /^[a-z][a-z0-9_]{0,47}$/.test(key)||rules.some(r=>r.key===key||r.handoff.params.work_item_id===source.handoff.params.work_item_id)
        ||!labels.length||labels.length>20||new Set(labels).size!==labels.length||labels.some(v=>v.length>100||/[\x00-\x1f]/.test(v))
        ||!paths.length||paths.length>100||new Set(paths).size!==paths.length||paths.some(v=>v.length>512||v!=='.'&&(/[\\:*?\[\]\x00-\x1f]/.test(v)||v.replace(/\/$/,'').split('/').some(p=>['','.','..'].includes(p))))
        ||!command.trim()||/[\x00-\x1f]/.test(command))throw Error(t('pmr_rule_invalid'));
      busy=true;update();
      const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(command))),digest=[...new Uint8Array(bytes)].map(n=>n.toString(16).padStart(2,'0')).join('');
      if(ticket!==epoch||!visible||!editable()||JSON.stringify(selected())!==original||baseline()?.baseline_id!==release.baseline_id)throw Error(t('pmr_review_changed'));
      rules.push({key,work_kind:kind,labels_all:labels,paths,release:{baseline_id:release.baseline_id,baseline_digest:release.document_digest,
        commit_sha:release.document.commit_sha,release_version:structuredClone(release.document.release_version)},handoff:structuredClone(source.handoff),
        verification:{kind:'fixed_revision_verification',profile:'observed_runner_v1',command_digest:digest}});
      fields.repair_rules.value=JSON.stringify(rules);fields.repair_rules_set.value='1';status.textContent=t('pmr_rule_added');changed();
    }catch(error){if(ticket===epoch&&visible)status.textContent=error.message;}finally{busy=false;update();}
  }
  function update(show=visible){visible=show;rows.replaceChildren();element.hidden=!available()&&!fields.repair_rules_set.value&&!fields.repair_ci.value;
    add.disabled=!visible||!editable()||busy||!available()||!selected();fields.repair_ci.disabled=!visible||!editable()||!ciAvailable();
    if(!visible){epoch++;status.textContent='';return;}
    for(const rule of read())rows.append(h('article',{class:'pm-repair-rule'},h('h4',{},rule.key),h('p',{},t('pmp_'+rule.work_kind),' · ',rule.labels_all.join(' · ')),
      h('p',{class:'pm-source'},rule.paths.join(' · ')),h('details',{},h('summary',{},t('pmr_rule_original')),
        h('p',{class:'pm-source'},rule.handoff.params.pm_package_ref.package_id),h('p',{class:'pm-source'},rule.release.baseline_id),h('p',{class:'pm-source'},rule.verification.command_digest)),
      button({variant:'secondary',label:t('pmr_remove_rule'),disabled:!editable()||null,onClick:()=>{if(!editable())return;fields.repair_rules.value=JSON.stringify(read().filter(r=>r.key!==rule.key));fields.repair_rules_set.value='1';changed();update();}})));
  }
  function initialize(policy){return {repair_rules:JSON.stringify(policy.repair_rules||[]),repair_rules_set:Object.hasOwn(policy,'repair_rules')?'1':'',repair_ci:policy.observation?.required_ci_profile||''};}
  function build(values){const result={};if(values.repair_rules_set){
    const rules=JSON.parse(values.repair_rules||'[]'),release=baseline(),paths=lines(values.paths),kinds=JSON.parse(values.works),labels=lines(values.finding_labels);
    if(!Array.isArray(rules)||rules.length>16||rules.some(rule=>!kinds.includes(rule.work_kind)||values.finding_issues!=='true'||!labels.every(label=>rule.labels_all.includes(label))
      ||rule.paths.some(path=>!paths.some(root=>root==='.'||root===path||root.endsWith('/')&&path.startsWith(root)))
      ||!release||rule.release.baseline_id!==release.baseline_id||rule.release.baseline_digest!==release.document_digest||rule.release.commit_sha!==release.document.commit_sha))throw Error(t('pmr_rule_invalid'));
    result.repair_rules=rules;
  }return result;}
  async function checkSources({api,endpoint,workspaceId,valid}){
    const signature=fields.repair_rules.value,rules=read(),seen=new Set();
    for(const rule of rules){
      const ref=rule.handoff?.params?.pm_package_ref,release=rule.release;
      if(ref?.workspace_id!==workspaceId||!/^pmwp_[0-9a-f]{32}$/.test(ref.package_id)||! /^[0-9a-f]{64}$/.test(ref.document_digest)
        ||!/^pmrb_[0-9a-f]{32}$/.test(release?.baseline_id)||! /^[0-9a-f]{64}$/.test(release.baseline_digest))throw Error(t('pmr_invalid'));
      const reads=[['work-packages',ref.package_id,'package',ref.document_digest],['release-baselines',release.baseline_id,'baseline',release.baseline_digest]];
      const spec=rule.handoff.params.pm_spec_ref;if(spec){if(spec.workspace_id!==workspaceId||!/^pmsp_[0-9a-f]{32}$/.test(spec.materialization_id)||! /^[0-9a-f]{64}$/.test(spec.document_digest))throw Error(t('pmr_invalid'));
        reads.push(['spec-materializations',spec.materialization_id,'materialization',spec.document_digest]);}
      for(const [path,id,key,digest]of reads){const cache=path+id+digest;if(seen.has(cache))continue;seen.add(cache);
        const value=await api('GET',`${endpoint}/${path}/${encodeURIComponent(id)}`);if(!valid())return;
        const row=value[key];if(value.workspace_id!==workspaceId||row?.workspace_id!==workspaceId||row[key==='package'?'package_id':key==='baseline'?'baseline_id':'materialization_id']!==id||row.document_digest!==digest)throw Error(t('pmr_invalid'));
      }
    }
    if(signature!==fields.repair_rules.value)throw Error(t('pmr_review_changed'));
  }
  return {element,fields,initialize,build,update,checkSources};
}

export function maintenanceRepairPolicyView({h,t,policy}){
  if(!policy.repair_rules?.length&&!policy.observation?.required_ci_profile)return null;
  return h('section',{},h('h4',{},t('pmr_rules')),...(policy.repair_rules||[]).map(rule=>h('p',{class:'pm-source'},rule.key,' · ',t('pmp_'+rule.work_kind),' · ',rule.labels_all.join(' · '))),
    policy.observation?.required_ci_profile?h('p',{},t('pmr_ci_explicit')):null,h('p',{class:'muted'},t('pmr_rules_help')));
}
