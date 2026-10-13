import {iconButton, skeleton} from './ui/components.js';
import {timeStamp, timeText} from './ui/stamps.js';
import {workspaceExecutionVerification} from './pm-execution-verification.js';
// Every choice is an original server record. A refresh may withdraw it, never silently replace it.
const START='pm.maintenance.repair',RECORD='pm.maintenance.repair.evidence.record';
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b),op=/^op_[0-9a-f]{32}$/,sha=/^[0-9a-f]{40}$/,digest=/^[0-9a-f]{64}$/;
export function workspaceMaintenanceRepairs({h,t,api,alive,workspaceId,workspace,caps,permitted,form,labelled,errorBox,namespace}){
  const base=`/pm/workspaces/${encodeURIComponent(workspaceId)}`,key=`batc.pm.repair-selection.${namespace}.${workspaceId}`;
  let epoch=0,ready=false,policy=null,baseline=null,lifecycle=null,findings=null,repair=null,start=null,proof=null,firstStart=false,proofReady=false;
  let filtered=false;let selected='';try{selected=localStorage.getItem(key)||'';}catch{}
  const editors=new Map(),choices=new Map(),status=h('div',{role:'status'}),facts=h('div'),budget=h('div'),startView=h('div'),proofView=h('div');
  const hidden=()=>h('input',{type:'hidden'}),startFields={selection:hidden(),context:hidden()};
  const select=label=>h('select',{'aria-label':t(label)}),finding=select('pmr_finding'),rule=select('pmr_rule'),history=select('pmr_history'),evidence=select('pmr_evidence');
  finding.dataset.pmRepairFinding='';
  let pendingFinding='';
  const line=(label,value)=>h('p',{class:'pm-source'},h('strong',{},t(label)+': '),value??t('pmr_none')),text=label=>h('p',{class:'muted'},t(label));
  const state=value=>t('pmr_status_'+(['accepted','queued','pending','running','succeeded','failed','cancelled','needs_attention','not_admitted','passed','blocked'].includes(value)?value:'unknown'));
  const link=id=>h('a',{href:`#/op/${encodeURIComponent(id)}`},t('pm_operation'));
  const valid=ticket=>{try{alive();return ticket===epoch;}catch{return false;}};
  const invalid=()=>Error(t('pmr_invalid'));
  const check=value=>{if(value?.workspace_id!==workspaceId)throw invalid();return value;};
  const owner=()=>permitted(true)&&workspace()?.membership?.role==='owner'&&['observe','manage'].every(s=>caps()?.scopes?.includes(s));
  const recover=()=>Boolean(workspace()?.membership)&&caps()?.scopes?.includes('manage');
  const cap=()=>caps()?.features?.pm_workspaces?.maintenance_repairs;
  const active=()=>ready&&owner()&&policy?.actor===caps()?.actor&&policy?.document.policy.enabled&&['development','maintain'].includes(lifecycle?.stage)&&!['pausing','closing','paused','closed'].includes(lifecycle?.status);
  const pre=()=>policy&&baseline&&lifecycle?{expected_policy_version:policy.revision,expected_baseline_id:baseline.baseline_id,expected_lifecycle_revision:lifecycle.revision}:null;
  const parse=field=>{try{return JSON.parse(field.value||'null');}catch{return null;}};
  const rules=()=>policy?.document.policy.repair_rules||[];
  const issueRows=()=>findings?.sources?.issues?.status==='complete'&&!findings.visibility_filtered?findings.sources.issues.findings:[];
  const eligible=(row,item)=>row&&item&&!row.existing_pull_request&&!row.existing_work?.length&&item.labels_all.every(label=>row.facts?.labels?.includes(label));
  const startValid=()=>{const value=parse(startFields.selection);return active()&&caps()?.scopes?.includes('start')&&cap()?.version===1&&value&&same(parse(startFields.context),pre())
    &&value.policy_id===policy.policy_id&&same(value.finding_ref,{...findings?.observation_ref,finding_id:value.finding_ref.finding_id})
    &&eligible(issueRows().find(row=>row.finding_id===value.finding_ref.finding_id),rules().find(row=>row.key===value.rule_key));};
  const stale=text('pmr_stale'),rebase=h('button',{type:'button',class:'secondary',onclick:()=>{if(!ready||start.locked())return;start.set('context',JSON.stringify(pre()));update();}},t('pmr_rebase'));
  const request=async(...args)=>{const ticket=epoch;try{
    if(args[0]==='POST'&&args[2]?.action===START&&firstStart){firstStart=false;
      const [p,b,l,f]=(await Promise.all(['maintenance-policies/current','release-baselines/current','lifecycle','maintenance-findings/current'].map(path=>api('GET',`${base}/${path}`)))).map(check);
      if(!valid(ticket))throw Object.assign(Error(t('pmr_unavailable')),{admissionRefused:true});
      const expected={expected_policy_version:p.policy?.revision,expected_baseline_id:b.baseline?.baseline_id,expected_lifecycle_revision:l.lifecycle?.revision};
      if(p.visibility_filtered||b.visibility_filtered||f.visibility_filtered||!same(expected,args[2].preconditions)||p.policy?.policy_id!==args[2].params.policy_id
        ||!same(f.observation_ref,{observation_id:args[2].params.finding_ref.observation_id,document_digest:args[2].params.finding_ref.document_digest})
        ||!['development','maintain'].includes(l.lifecycle?.stage)||['pausing','closing','paused','closed'].includes(l.lifecycle?.status))throw Object.assign(Error(t('pmr_stale')),{code:'VERSION_CONFLICT',admissionRefused:true});
    }
    return await api(...args);
  }catch(error){if(valid(ticket)&&[401,403,404].includes(error.status))failure(error);throw error;}};
  function update(){
    start?.update();proof?.update();
    finding.disabled=rule.disabled=!ready||!owner()||Boolean(start?.locked());history.disabled=!ready;
    evidence.disabled=!proofReady||!owner()||Boolean(proof?.locked());
    stale.hidden=rebase.hidden=!ready||!start||start.locked()||!startFields.context.value||same(parse(startFields.context),pre());
    rebase.disabled=!active();startView.hidden=!ready&&!start?.locked();proofView.hidden=!ready&&!proof?.locked();
    if(proof&&!proof.locked())proof.box.querySelector('button[type=submit]').disabled=!proofValid();
  }
  start=form({action:START,target:{workspace_id:workspaceId},fields:startFields,api:request,
    params:values=>{if(start?.locked())firstStart=true;return JSON.parse(values.selection);},preconditions:()=>parse(startFields.context),
    permitted:startValid,recoverPermitted:recover,label:'pmr_start',extra:h('div',{},h('div',{class:'pm-fields'},labelled('pmr_finding',finding),labelled('pmr_rule',rule)),budget,stale,rebase),
    onSaved:result=>{selected=result.repair_id;try{localStorage.setItem(key,selected);}catch{}return refresh();}});
  startView.append(start.box);
  function changed(){if(!ready||start.locked())return;const row=issueRows().find(v=>v.finding_id===finding.value),item=rules().find(v=>v.key===rule.value);
    start.set('selection',row&&item?JSON.stringify({policy_id:policy.policy_id,rule_key:item.key,finding_ref:{...findings.observation_ref,finding_id:row.finding_id}}):'');
    if(!startFields.context.value)start.set('context',JSON.stringify(pre()));update();}
  finding.addEventListener('change',changed);rule.addEventListener('change',changed);
  history.addEventListener('change',()=>{selected=history.value;try{localStorage.setItem(key,selected);}catch{}void refresh();});
  evidence.addEventListener('change',()=>{if(!proof||proof.locked())return;proof.set('selection',evidence.value?JSON.stringify(choices.get(evidence.value)?.selection):'');update();});
  const verifier=workspaceExecutionVerification({h,t,api:request,alive,workspaceId,workspace,caps,form,labelled,errorBox,
    namespace:`${namespace}.repair`,onSaved:()=>refresh(),onUnavailable:error=>failure(error)});
  const element=h('section',{class:'panel pm-maintenance-repairs','data-pm-maintenance-repairs':''},h('header',{class:'pm-heading'},h('div',{},h('h2',{},t('pmr_title')),text('pmr_intro')),
    iconButton({icon:'refresh-cw', label:t('pmr_refresh'), ...{type:'button',class:'secondary',onclick:()=>void refresh()}})),status,startView,labelled('pmr_history',history),facts,verifier.element,proofView);
  function options(field,rows,value=''){field.replaceChildren(h('option',{value:''},t('pmr_choose')),...rows.map(([id,label])=>h('option',{value:id},label)));field.value=rows.some(([id])=>id===value)?value:'';}
  async function pages(path,list,cursor,ticket){const result=[];let after=0;
    for(let page=0;page<20;page++){const data=check(await api('GET',`${base}/${path}${path.includes('?')?'&':'?'}after=${after}&limit=50`));if(!valid(ticket))return null;
      const next=data[cursor];if(!Array.isArray(data[list])||next!==null&&(!Number.isSafeInteger(next)||next<=after))throw invalid();result.push(...data[list]);
      if(data.source_visibility_filtered||data.visibility_filtered)filtered=true;
      if(next===null)return result;after=next;}
    throw Error(t('pmr_limit'));
  }
  async function verifierOperations(ticket){const result=[];let before=null;
    for(let page=0;page<20;page++){const data=await api('GET',`/operations?action=pm.execution.verify&limit=50${before===null?'':'&before='+before}`);if(!valid(ticket))return null;
      const next=data.next_before;if(!Array.isArray(data.operations)||next!==null&&(!Number.isSafeInteger(next)||next<=0||before!==null&&next>=before))throw invalid();
      result.push(...data.operations);if(next===null)return result;before=next;}throw Error(t('pmr_limit'));
  }
  function validate(row){if(row?.workspace_id!==workspaceId||!/^pmrp_[0-9a-f]{32}$/.test(row.repair_id)||!op.test(row.operation_id)||!Number.isSafeInteger(row.version)
    ||!row.context?.rule||!row.context.finding_ref||!Array.isArray(row.evidence))throw invalid();return row;}
  function render(row){const c=row.context,p=row.progress||{},box=h('article',{'data-repair':row.repair_id},h('h3',{},`${t('pmr_issue')} #${c.finding.native.number}`),
      line('pmr_rule',c.rule.key),line('pmr_dispatch',state(row.child?.status||'not_admitted')),text(row.capacity_held===false?'pmr_released':'pmr_reserved'),
      line('pmr_verification',p.verification?state(p.verification.status):t('pmr_unproven')),line('pmr_quality',p.quality?.length?t('pmr_recorded'):t('pmr_unproven')),
      line('pmr_publication',row.publication_child?state(row.publication_child.status):t('pmr_unproven')),line('pmr_ci',p.required_ci_receipts?.length?t('pmr_recorded'):t('pmr_unproven')),text('pmr_not_fixed'));
    if(['published_for_review','merged','deployed'].includes(row.outcome))box.prepend(line('pmr_outcome',t('pmr_'+row.outcome)));
    const completed=row.runtime_completion,baselineUpdate=row.baseline_update;
    if(completed?.schema==='pm_repair_runtime_completion_v1'&&completed.parent_operation_id===row.operation_id
      &&completed.execution_operation_id===row.child?.operation_id&&completed.runtime_status==='original_turn_completed'){
      box.append(line('pmr_runtime',t('pmr_turn_completed')));
      if(op.test(completed.observation_operation_id))box.append(h('p',{},t('pmr_completion_receipt'),' · ',link(completed.observation_operation_id)));
    }else if(row.runtime_observer)box.append(line('pmr_runtime',t('pmr_unproven')),h('p',{},t('pmr_completion_receipt'),' · ',link(row.runtime_observer.operation_id)));
    if(row.delivery_child)box.append(line('pmr_delivery',state(row.delivery_child.status)),h('p',{},t('pmr_original_delivery'),' · ',link(row.delivery_child.operation_id)));
    if(baselineUpdate?.status==='updated'&&row.baseline_child?.result?.baseline_id===baselineUpdate.baseline_id
      &&row.baseline_child.operation_id===baselineUpdate.operation_id){
      box.append(line('pmr_baseline_update',t('pmr_baseline_updated')),h('p',{},t('pmr_baseline_receipt'),' · ',link(baselineUpdate.operation_id)));
    }else if(baselineUpdate?.status==='not_updated')box.append(line('pmr_baseline_update',t('pmr_baseline_not_updated')));
    else if(row.baseline_child)box.append(line('pmr_baseline_update',state(row.baseline_child.status)),h('p',{},t('pmr_baseline_receipt'),' · ',link(row.baseline_child.operation_id)));
    if(row.child)box.append(h('p',{},t('pmr_child'),' · ',link(row.child.operation_id)));
    if(row.publication_child){box.append(h('p',{},t('pmr_original_publication'),' · ',link(row.publication_child.operation_id)));
      const receipt=row.publication_child.result?.creation_receipt;if(receipt)box.append(line('pmr_native_pr',receipt.native_pr_id),line('pmr_candidate',receipt.candidate_sha));
      if(row.publication_child.status==='needs_attention'||row.publication_child.error_code?.includes('UNKNOWN'))box.append(text('pmr_unknown'));}
    if(p.new_investment_blocked_reason||p.blocked_reason)box.append(h('p',{class:'muted'},reason(p.new_investment_blocked_reason||p.blocked_reason)));
    const detail=h('details',{},h('summary',{},t('pmr_original')),line('pmr_work',row.work_item_id),line('pmr_policy',row.policy_id),
      line('pmr_observation',c.finding_ref.observation_id),line('pmr_digest',c.finding_ref.document_digest),line('pmr_deadline',timeStamp(row.deadline_at)),
      line('pmr_retry',row.retries_used),text('pmr_cost'),link(row.operation_id));
    for(const item of row.evidence){const proof=item.evidence;detail.append(h('p',{},t('pmr_kind_'+item.selection.kind),' · ',link(item.operation_id)));
      for(const [name,value]of Object.entries(proof.reference||item.selection.reference||{}))detail.append(h('p',{class:'pm-source'},name+': '+value));}
    box.append(detail);return box;
  }
  function reason(code){if(code==='PM_REPAIR_RUNTIME_TERMINATION_UNPROVEN')return t('pmr_runtime_unproven');if(/UNKNOWN|UNSETTLED/.test(code))return t('pmr_unknown');if(/DEADLINE/.test(code))return t('pmr_deadline_block');if(/BUDGET|CAPACITY/.test(code))return t('pmr_budget_block');
    if(/QUALITY/.test(code))return t('pmr_quality_needed');if(/VERIFICATION/.test(code))return t('pmr_verify_needed');if(/STOP|PAUS|EPOCH|CHANGED/.test(code))return t('pmr_stopped');return t('pmr_gate');}
  function proofValid(){return ready&&proofReady&&owner()&&proof&&repair&&Number(proof.fields.version.value)===repair.version&&[...choices.values()].some(choice=>same(choice.selection,parse(proof.fields.selection)));}
  async function loadProof(ticket){
    choices.clear();proofReady=false;if(!repair?.child){proofView.replaceChildren();proof=null;return;}
    const row=repair;let editor=editors.get(row.repair_id);
    if(!editor){const fields={selection:hidden(),version:hidden()},use=h('button',{type:'button',class:'secondary',onclick:()=>{if(ready&&!editor.locked()){editor.set('version',String(repair.version));update();}}},t('pmr_evidence_rebase'));
      editor=form({action:RECORD,target:{workspace_id:workspaceId,repair_id:row.repair_id},fields,api:request,params:v=>({evidence:JSON.parse(v.selection)}),preconditions:()=>({expected_repair_version:Number(fields.version.value)}),
        permitted:proofValid,recoverPermitted:recover,label:'pmr_record',extra:h('div',{},text('pmr_evidence_help'),labelled('pmr_evidence',evidence),use),onSaved:()=>refresh()});
      editors.set(row.repair_id,editor);editor.rebase=use;}
    proof=editor;proofView.replaceChildren(editor.box);if(!editor.fields.version.value&&!editor.locked())editor.set('version',String(row.version));
    if(caps()?.features?.pm_workspaces?.execution_verification?.version===1){
      const child=await api('GET',`/operations/${row.child.operation_id}`);if(!valid(ticket))return;
      if(child.operation?.operation_id!==row.child.operation_id||child.operation.action!=='repository.continue')throw invalid();
      await verifier.setSources(row.child.status==='succeeded'?[child.operation]:[]);if(!valid(ticket))return;
      const operations=await verifierOperations(ticket);if(!valid(ticket)||!operations)return;
      for(const source of operations){if(source.action!=='pm.execution.verify'||source.target?.workspace_id!==workspaceId||source.target.execution_operation_id!==row.child.operation_id)continue;
        if(!op.test(source.operation_id)||source.params?.command_digest!==row.context.rule.verification.command_digest)continue;
        const data=check(await api('GET',`${base}/execution-verifications/${source.operation_id}`));if(!valid(ticket))return;const receipt=data.verification;if(!receipt)continue;
        const ref=receipt.reference;if(receipt.workspace_id!==workspaceId||receipt.operation_id!==source.operation_id||receipt.execution_operation_id!==row.child.operation_id
          ||receipt.profile!=='observed_runner_v1'||!digest.test(receipt.receipt_digest)||ref?.verification_operation_id!==source.operation_id||!sha.test(ref.candidate_commit)||!sha.test(ref.tree_hash)
          ||ref.candidate_commit!==source.params.candidate_commit||ref.tree_hash!==source.params.tree_hash)throw invalid();
        choices.set('verification:'+source.operation_id,{selection:{kind:'verification',reference:ref},label:`${t('pmr_kind_verification')} · ${state(receipt.status)} · ${ref.candidate_commit.slice(0,12)}`});
      }
    }
    const quality=await pages(`work/${encodeURIComponent(row.work_item_id)}/quality`,'reviews','next_cursor',ticket);if(!valid(ticket)||!quality)return;
    for(const item of quality){const assessment=item.assessment;if(item.context?.execution_operation_id!==row.child.operation_id||item.qualified!==true||!assessment)continue;
      const ref={review_id:item.review_id,operation_id:assessment.operation_id,version:assessment.version,document_digest:assessment.document_digest};
      if(!/^pmqr_[0-9a-f]{32}$/.test(ref.review_id)||!op.test(ref.operation_id)||!digest.test(ref.document_digest)||!Number.isSafeInteger(ref.version))throw invalid();
      choices.set('quality:'+ref.operation_id,{selection:{kind:'quality',reference:ref},label:`${t('pmr_kind_quality')} · ${ref.review_id} · ${t('pmr_revision')} ${ref.version}`});}
    const receipt=row.publication_child?.result?.creation_receipt;
    if(receipt?.target_verified===true&&Number.isSafeInteger(receipt.native_pr_id)){
      const observations=await pages('github-observations','observations','next_cursor',ticket);if(!valid(ticket)||!observations)return;
      for(const item of observations){const ci=item.document?.evidence?.facts?.required_ci;if(ci?.profile!=='github_required_status_checks_v1'||ci.status!=='complete'
          ||!ci.pulls?.some(p=>p.native_pr_id===receipt.native_pr_id&&p.head_sha===receipt.candidate_sha))continue;
        const ref={observation_id:item.observation_id,document_digest:item.document_digest,native_pr_id:receipt.native_pr_id};
        if(!/^pmgh_[0-9a-f]{32}$/.test(ref.observation_id)||!digest.test(ref.document_digest))throw invalid();
        choices.set('required_ci:'+ref.observation_id,{selection:{kind:'required_ci',reference:ref},label:`${t('pmr_kind_required_ci')} · #${receipt.pull_number} · ${timeText(item.created_at)}`});}
    }
    if(!valid(ticket))return;if(filtered&&!status.querySelector('[data-repair-filtered]')){const notice=text('pmr_filtered');notice.dataset.repairFiltered='';status.append(notice);}proofReady=true;const saved=parse(editor.fields.selection),value=[...choices].find(([,v])=>same(v.selection,saved))?.[0]||'';
    options(evidence,[...choices].map(([id,value])=>[id,value.label]),value);editor.rebase.hidden=Number(editor.fields.version.value)===row.version||editor.locked();update();
  }
  function withdraw(){epoch++;verifier.withdraw();verifier.element.hidden=true;filtered=false;ready=false;policy=baseline=lifecycle=findings=repair=null;proofReady=false;choices.clear();for(const field of [finding,rule,history,evidence])field.replaceChildren();facts.replaceChildren();budget.replaceChildren();status.replaceChildren();
    for(const editor of [start,...editors.values()])editor?.box.querySelector('.pm-receipt')?.replaceChildren();update();}
  function failure(error){withdraw();status.append(text('pmr_unavailable'),errorBox(error));}
  async function refresh(){withdraw();const ticket=epoch;status.append(skeleton(2));
    try{const [p,b,l,f]=(await Promise.all(['maintenance-policies/current','release-baselines/current','lifecycle','maintenance-findings/current'].map(path=>api('GET',`${base}/${path}`)))).map(check);if(!valid(ticket))return;
      if(!Number.isSafeInteger(l.lifecycle?.revision)||p.policy&&(p.policy.workspace_id!==workspaceId||p.policy.document?.workspace_id!==workspaceId||p.policy.revision!==p.head?.revision)
        ||p.policy&&(p.policy.policy_id!==p.head.policy_id||p.policy.document.policy_id!==p.policy.policy_id||p.policy.document.revision!==p.policy.revision)
        ||b.baseline&&(b.baseline.workspace_id!==workspaceId||b.baseline.document?.workspace_id!==workspaceId||b.baseline.baseline_id!==b.head?.baseline_id||b.baseline.version!==b.head.version||b.baseline.document.baseline_id!==b.baseline.baseline_id||b.baseline.document.version!==b.baseline.version)||p.visibility_filtered&&p.policy||b.visibility_filtered&&b.baseline
        ||typeof f.visibility_filtered!=='boolean'||!f.sources?.issues||!Array.isArray(f.sources.issues.findings))throw invalid();
      policy=p.policy;baseline=b.baseline;lifecycle=l.lifecycle;findings=f;
      const rows=await pages('maintenance-repairs','repairs','next_after',ticket);if(!valid(ticket)||!rows)return;rows.forEach(validate);
      status.replaceChildren();if(filtered){const notice=text('pmr_filtered');notice.dataset.repairFiltered='';status.append(notice);}ready=true;const saved=parse(startFields.selection);
      options(finding,issueRows().map(row=>[row.finding_id,`${t('pmr_issue')} #${row.native.number} · ${(row.facts.labels||[]).join(' · ')}`]),pendingFinding||saved?.finding_ref?.finding_id);
      pendingFinding='';
      options(rule,rules().map(row=>[row.key,`${row.key} · ${t('pmp_'+row.work_kind)}`]),saved?.rule_key);
      options(history,rows.map(row=>[row.repair_id,`${t('pmr_issue')} #${row.context.finding.native.number} · ${row.context.rule.key} · ${state(row.operation_status)}`]),selected);
      if(selected){const data=check(await api('GET',`${base}/maintenance-repairs/${encodeURIComponent(selected)}`));if(!valid(ticket))return;repair=validate(data.repair);if(repair.repair_id!==selected)throw invalid();facts.append(render(repair));await loadProof(ticket);}else{proof=null;proofView.replaceChildren();}
      if(!valid(ticket))return;const limits=policy?.document.policy.limits;
      if(limits)budget.append(line('pmr_budget',`${limits.max_parallel} ${t('pmr_parallel')} · ${limits.period_runs} / ${limits.period_seconds}s · ${limits.run_seconds}s`),text('pmr_cost'));
      if(!owner())status.append(text('pmr_owner'));else if(!active())status.append(text('pmr_stopped'));else if(!rules().length)status.append(text('pmr_no_rules'));
      update();
    }catch(error){if(valid(ticket))failure(error);}}
  function focusFinding(ref){const id=typeof ref?.finding_id==='string'?ref.finding_id:'';if(!id)return;pendingFinding=id;if([...finding.options].some(option=>option.value===id))finding.value=id;}
  update();return {element,refresh,withdraw,focusFinding};
}
