import {acceptanceProfile,requirementCondition,readRequirementReviews,matchesRequirementReview,referenceKey,checkRequirementMap} from './pm-requirement-quality.js';
import {checkSpecMaterialization,fixedSpecSelection} from './pm-spec-materialization.js';

const START='pm.work_batch.start',RECORD='pm.work_batch.evidence.record';
const profile={kind:'fixed_revision_verification',profile:'github_reported_checks_v1'};
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b),clone=value=>structuredClone(value);
const sha=/^[0-9a-f]{40}$/,digest=/^[0-9a-f]{64}$/,op=/^op_[0-9a-f]{32}$/;

export function workspaceWorkBatches({h,t,api,alive,workspaceId,workspace,caps,permitted,form,input,labelled,errorBox,namespace,reviewedHandoff=()=>null}){
  const base=`/pm/workspaces/${encodeURIComponent(workspaceId)}`,navigation=`batc.pm.batch-selection.${namespace}.${workspaceId}`;
  let selected='';try{selected=localStorage.getItem(navigation)||'';}catch{}
  let serial=0,ready=false,context=null,batch=null,start=null,proof=null,sourceReady=false,sourceTicket=0,entrySignature='',firstSubmission=false;
  let qualityReviews=[],receipts=[],observations=[],proofReady=false,proofBaseline=null,proofChoices=null,evidenceFiltered=false,proofRebase=null;
  const labels=new Map(),packages=new Map();
  const facts=h('div'),status=h('div',{role:'status'}),entriesView=h('div'),startView=h('div'),proofView=h('div'),history=h('select',{'aria-label':t('pmb_history')});
  const text=key=>h('p',{class:'muted'},t(key)),line=(key,value)=>h('p',{class:'pm-source'},h('strong',{},t(key)+': '),String(value));
  const hidden=value=>{const field=h('input',{type:'hidden',value:JSON.stringify(value)});field.dataset.pmDefault=field.value;return field;};
  const evidenceNotice=text('pmb_filtered');evidenceNotice.hidden=true;
  const entries=hidden([]),baseline=hidden(null),reason=input('pmb_rationale',{multiline:true,max:4000});
  const number=(label,min,max,value)=>{const field=input(label,{value:String(value)});field.type='number';field.min=String(min);field.max=String(max);field.step='1';field.dataset.pmDefault=String(value);return field;};
  const starts=number('pmb_starts',1,16,1),parallel=number('pmb_parallel',1,8,1),retries=number('pmb_retries',0,3,0),deadline=input('pmb_deadline');deadline.type='datetime-local';
  const add=h('button',{type:'button',class:'secondary',onclick:()=>void addReviewed()},t('pmb_add'));
  const stale=text('pmb_stale');stale.hidden=true;
  const rebase=h('button',{type:'button',class:'secondary',hidden:true,onclick:()=>{if(ready&&!start.locked()){start.set('_baseline',JSON.stringify(currentPre()));update();}}},t('pmb_use_versions'));
  const element=h('section',{class:'panel pm-work-batches','data-pm-work-batches':''},
    h('header',{class:'pm-heading'},h('div',{},h('h2',{},t('pmb_title')),text('pmb_intro')),
      h('button',{type:'button',class:'secondary',onclick:()=>void refresh()},t('pmb_refresh'))),status,
    h('details',{'data-batch-draft':'',open:true},h('summary',{},t('pmb_draft')),text('pmb_selection_help'),add,stale,rebase,startView),
    labelled('pmb_history',history),facts,proofView);
  const cap=()=>caps()?.features?.pm_workspaces?.work_batches;
  const supported=()=>cap()?.version===1&&cap()?.actions?.includes(START)&&cap()?.profiles?.includes(profile.profile);
  const valid=ticket=>{try{alive();return ticket===serial;}catch{return false;}};
  const writer=()=>ready&&permitted(false)&&['owner','contributor'].includes(workspace()?.membership?.role)&&caps()?.scopes?.includes('manage');
  const starter=()=>writer()&&caps()?.scopes?.includes('start')&&!context?.lifecycle.blocked_actions.includes(START);
  const currentPre=()=>({expected_workspace_version:context.workspace.version,expected_lifecycle_revision:context.lifecycle.lifecycle.revision});
  const draft=()=>{try{const rows=JSON.parse(entries.value||'[]');return Array.isArray(rows)?rows:[];}catch{return [];}};
  const draftStale=()=>ready&&!same(JSON.parse(baseline.value),currentPre());
  const condition=entry=>entry.result_condition||profile;
  const error=()=>Error(t('pmb_invalid'));
  const choose=(key,rows)=>h('select',{'aria-label':t(key)},h('option',{value:''},t('pmb_choose')),...rows.map(([value,label])=>h('option',{value},label)));
  function validateDraft(){
    try{const rows=draft();if(!sourceReady||!rows.length||rows.length>16||new Set(rows.map(row=>row.handoff.params.work_item_id)).size!==rows.length)return false;
      const visiting=new Set(),seen=new Set();function visit(key){if(visiting.has(key))throw error();if(seen.has(key))return;const row=rows.find(row=>row.key===key);if(!row)throw error();
        visiting.add(key);for(const edge of row.depends_on){if(!same(edge.condition,condition(rows.find(other=>other.key===edge.entry_key)||{})))throw error();visit(edge.entry_key);}visiting.delete(key);seen.add(key);}
      rows.forEach(row=>visit(row.key));return true;
    }catch{return false;}
  }
  function update(){
    if(start){start.update();if(!start.locked()&&!validateDraft())start.box.querySelector('button[type=submit]').disabled=true;}
    if(proof){proof.update();if(!proof.locked()&&!validProof())proof.box.querySelector('button[type=submit]').disabled=true;}
    if(proofRebase)proofRebase.hidden=!ready||!batch||!proof||proof.locked()||Number(proof.fields._version.value)===batch.version;
    add.disabled=!starter()||Boolean(start?.locked())||draft().length>=16||!reviewedHandoff();
    stale.hidden=rebase.hidden=!ready||!start||start.locked()||!draftStale();history.disabled=!ready;
    for(const node of entriesView.querySelectorAll('button,input,select'))node.disabled=!writer()||Boolean(start?.locked())||!sourceReady;
  }
  function clearSources(){sourceTicket++;labels.clear();packages.clear();qualityReviews=[];sourceReady=false;proofReady=false;evidenceFiltered=false;evidenceNotice.hidden=true;receipts=[];observations=[];entriesView.replaceChildren();entrySignature='';facts.replaceChildren();history.replaceChildren();startView.hidden=true;proofView.hidden=true;
    if(proof){proofChoices ||= Object.fromEntries(['entry','receipt','observation','assessment'].map(name=>[name,proof.fields[name].value]));for(const name of ['entry','receipt','observation','assessment'])proof.fields[name].replaceChildren();}}
  function withdraw(){serial++;ready=false;context=null;batch=null;clearSources();status.replaceChildren();update();}
  function failure(err){withdraw();status.replaceChildren(text('pmb_unavailable'),errorBox(err.code==='PM_REQUIREMENT_QUALITY_INVALID'?error():err));}
  const request=async(...args)=>{const ticket=serial;try{return await api(...args);}catch(err){if(valid(ticket)&&[401,403,404].includes(err.status))failure(err);throw err;}};
  async function readContext(){
    const [w,l]=await Promise.all([api('GET',base),api('GET',`${base}/lifecycle`)]);
    if(w?.workspace?.workspace_id!==workspaceId||!Number.isSafeInteger(w.workspace.version)||w.workspace.version<1
      ||l?.workspace_id!==workspaceId||!Number.isSafeInteger(l.lifecycle?.revision)||l.lifecycle.revision<0||!Array.isArray(l.blocked_actions))throw error();
    return {workspace:w.workspace,lifecycle:l};
  }
  async function pages(path,key,cursor='next_cursor',filter='visibility_filtered',extraCheck=()=>{}){
    const rows=[],seen=new Set();let after=0,filtered=false;const ticket=serial;
    for(let page=0;page<20;page++){
      const data=await api('GET',`${path}?after=${after}&limit=50`);if(!valid(ticket))return null;
      if(data?.workspace_id!==workspaceId||!Array.isArray(data[key])||typeof data[filter]!=='boolean'
        ||data[cursor]!==null&&(!Number.isSafeInteger(data[cursor])||data[cursor]<=after))throw error();extraCheck(data);
      for(const row of data[key]){const id=row.batch_id||row.observation_id||row.operation_id||row.review_id;if(!id||seen.has(id))throw error();seen.add(id);rows.push(row);}
      filtered ||= data[filter];if(data[cursor]===null)return {rows,filtered};after=data[cursor];
    }throw Error(t('pmb_history_bound'));
  }
  async function checkDraft(rows,ticket=serial){
    let quality=null;
    for(const row of rows){const ref=row.handoff.params.pm_package_ref;
      const data=await api('GET',`${base}/work-packages/${encodeURIComponent(ref.package_id)}`);if(!valid(ticket))return false;
      const pkg=data?.package,code=pkg?.document?.code_source,p=row.handoff.params;
      if(data?.workspace_id!==workspaceId||pkg?.workspace_id!==workspaceId||pkg.package_id!==ref.package_id||pkg.document_digest!==ref.document_digest||ref.workspace_id!==workspaceId
        ||pkg.work_item_id!==p.work_item_id||code?.source_sha!==p.source_sha||code.source_ref!==p.source_ref
        ||['repository','host','workspace_id'].some(key=>code[key]!==row.handoff.target[key]))throw error();
      packages.set(ref.package_id,pkg);
      if(p.pm_requirement_quality_refs){quality ||= await readRequirementReviews(api,workspaceId,()=>valid(ticket));if(!quality||!valid(ticket))return false;checkRequirementMap(pkg,p.pm_requirement_quality_refs,quality.rows);}
      if(condition(row).profile===acceptanceProfile&&!same(requirementCondition(pkg,condition(row).requirements.map(item=>item.requirement_id)),condition(row)))throw error();
      if(p.pm_spec_ref){const value=await api('GET',`${base}/spec-materializations/${encodeURIComponent(p.pm_spec_ref.materialization_id)}`);if(!valid(ticket))return false;
        if(value?.workspace_id!==workspaceId)throw error();const spec=checkSpecMaterialization(value.materialization,workspaceId,pkg,{full:true});
        if(!same(fixedSpecSelection(spec,workspaceId),{pm_spec_ref:p.pm_spec_ref,artifacts:p.artifacts}))throw error();}
    }return true;
  }
  function renderEntries(keepOpen=false){
    if(!ready||!sourceReady)return;const rows=draft(),signature=JSON.stringify(rows);if(signature===entrySignature)return;entrySignature=signature;const expanded=new Map((keepOpen?[...entriesView.querySelectorAll('[data-batch-entry]')]:[]).map(node=>[node.dataset.batchEntry,[...node.querySelectorAll('details')].map(item=>item.open)]));entriesView.replaceChildren();
    if(!rows.length){entriesView.append(text('pmb_empty'));return;}
    for(const row of rows){
      const deps=h('div',{class:'pm-batch-dependencies'});
      for(const other of rows.filter(other=>other.key!==row.key)){
        const node=h('input',{type:'checkbox','aria-label':`${t('pmb_wait_for')} ${other.title}`});node.checked=row.depends_on.some(edge=>edge.entry_key===other.key);
        node.addEventListener('input',()=>{if(start.locked()||!writer())return;const values=draft(),current=values.find(item=>item.key===row.key);
          current.depends_on=current.depends_on.filter(edge=>edge.entry_key!==other.key);if(node.checked)current.depends_on.push({entry_key:other.key,condition:clone(condition(other))});
          start.set('entries',JSON.stringify(values));entrySignature=JSON.stringify(values);
          const notice=entriesView.querySelector(`[data-batch-entry="${row.key}"] [data-condition-changed]`);if(notice)notice.hidden=!current.depends_on.some(edge=>!same(edge.condition,condition(values.find(item=>item.key===edge.entry_key)||{})));update();});deps.append(h('label',{},node,h('span',{},other.title)));
      }
      for(const missing of row.depends_on.filter(edge=>!rows.some(item=>item.key===edge.entry_key))){deps.append(text('pmb_missing_dependency'),h('button',{type:'button',class:'secondary',onclick:()=>{
        if(start.locked()||!writer())return;const values=draft();values.find(item=>item.key===row.key).depends_on=values.find(item=>item.key===row.key).depends_on.filter(edge=>edge.entry_key!==missing.entry_key);
        start.set('entries',JSON.stringify(values));renderEntries();update();}},t('pmb_remove_dependency')));}
      const result=choose('pmb_profile_label',[[profile.profile,t('pmb_github_profile')],...(cap().profiles.includes(acceptanceProfile)?[[acceptanceProfile,t('pmb_acceptance_profile')]]:[])]);result.value=condition(row).profile;
      result.addEventListener('change',()=>{if(start.locked()||!writer())return;try{const values=draft();values.find(item=>item.key===row.key).result_condition=result.value===acceptanceProfile?requirementCondition(packages.get(row.handoff.params.pm_package_ref.package_id)):clone(profile);start.set('entries',JSON.stringify(values));renderEntries(true);update();}catch(err){failure(err);}});
      const changed=text('pmb_changed_dependency');changed.dataset.conditionChanged='';changed.hidden=!row.depends_on.some(edge=>!same(edge.condition,condition(rows.find(other=>other.key===edge.entry_key)||{})));
      const details=h('details',{},h('summary',{},t('pmb_fixed_handoff')),line('pmb_entry_key',row.key),line('pmb_base',row.handoff.params.source_sha),
        line('pmb_package',row.handoff.params.pm_package_ref.package_id),h('p',{class:'pm-note-text'},row.handoff.params.prompt),h('p',{class:'pm-note-text'},row.handoff.params.pm_code_basis.rationale),
        row.handoff.params.pm_spec_ref?line('pmb_attachment',row.handoff.params.pm_spec_ref.materialization_id):null);
      entriesView.append(h('article',{class:'pm-batch-entry','data-batch-entry':row.key},h('h4',{},row.title),labelled('pmb_profile_label',result),condition(row).profile===acceptanceProfile?text('pmb_acceptance_help'):text('pmb_profile'),details,changed,
        h('details',{},h('summary',{},t('pmb_dependencies')),text('pmb_dependency_help'),deps),h('button',{type:'button',class:'secondary',onclick:()=>{
          if(start.locked()||!writer())return;start.set('entries',JSON.stringify(draft().filter(item=>item.key!==row.key)));renderEntries();update();}},t('pmb_remove'))));
      const article=entriesView.lastElementChild;[...article.querySelectorAll('details')].forEach((node,index)=>{node.open=expanded.get(row.key)?.[index]||false;});
    }
  }
  async function addReviewed(){
    const selected=reviewedHandoff();if(!selected||add.disabled)return;
    const rows=draft();if(rows.some(row=>row.handoff.params.work_item_id===selected.handoff.params.work_item_id)){status.replaceChildren(text('pmb_duplicate'));return;}
    const next=[...rows,{key:'entry_'+crypto.randomUUID().replaceAll('-',''),title:selected.title,handoff:clone(selected.handoff),depends_on:[],result_condition:clone(profile)}];
    const ticket=serial,read=++sourceTicket;sourceReady=false;update();
    try{if(!await checkDraft(next,ticket)||!valid(ticket)||read!==sourceTicket)return;start.set('entries',JSON.stringify(next));sourceReady=true;renderEntries();status.replaceChildren();update();}
    catch(err){if(valid(ticket))failure(err);}
  }
  const guardedStart=async(...args)=>{
    if(args[0]==='POST'&&args[2]?.action===START&&firstSubmission){firstSubmission=false;const ticket=serial;
      try{const latest=await readContext();if(!valid(ticket))throw error();
        const pre={expected_workspace_version:latest.workspace.version,expected_lifecycle_revision:latest.lifecycle.lifecycle.revision};
        if(!same(pre,args[2].preconditions)||latest.lifecycle.blocked_actions.includes(START)){context=latest;throw Object.assign(Error(t('pmb_stale')),{code:'VERSION_CONFLICT'});}
        if(!await checkDraft(args[2].params.entries,ticket)||!valid(ticket))throw error();
      }catch(err){if(valid(ticket)&&[401,403,404].includes(err.status))failure(err);throw Object.assign(err,{admissionRefused:true});}
    }return request(...args);
  };
  start=form({namespace:`${namespace}.work-batch`,api:guardedStart,action:START,target:{workspace_id:workspaceId},
    fields:{entries,_baseline:baseline,max_starts:starts,max_parallel:parallel,max_retries:retries,deadline,rationale:reason},
    params:values=>{if(!validateDraft())throw Error(t('pmb_selection_required'));const deadline_at=new Date(values.deadline).getTime()/1000;
      if(![values.max_starts,values.max_parallel,values.max_retries].every(value=>/^\d+$/.test(value))||Number(values.max_parallel)>Number(values.max_starts))throw Error(t('pmb_limits_invalid'));
      if(!Number.isFinite(deadline_at)||deadline_at<=Date.now()/1000||deadline_at>Date.now()/1000+86400)throw Error(t('pmb_deadline_invalid'));
      return {entries:JSON.parse(values.entries).map(({title,...entry})=>entry),limits:{max_starts:Number(values.max_starts),max_parallel:Number(values.max_parallel),max_retries:Number(values.max_retries),deadline_at},rationale:values.rationale};},
    preconditions:()=>JSON.parse(baseline.value),permitted:()=>starter()&&!draftStale(),recoverPermitted:writer,label:'pmb_start',
    onStateChange:()=>{if(start){if(!start.locked()&&!validateDraft())start.box.querySelector('button[type=submit]').disabled=true;stale.hidden=rebase.hidden=!ready||start.locked()||!draftStale();}},
    onSaved:async result=>{if(result?.batch_id){selected=result.batch_id;remember();}entrySignature='';await refresh();},
    extra:h('div',{class:'pm-fields'},entriesView,h('div',{class:'pm-batch-limits'},labelled('pmb_starts',starts),labelled('pmb_parallel',parallel),labelled('pmb_retries',retries),labelled('pmb_deadline',deadline)),
      text('pmb_budget_help'),labelled('pmb_rationale',reason),text('pmb_boundary'))});
  start.box.addEventListener('submit',()=>{firstSubmission=!start.locked();},true);start.box.addEventListener('input',update);start.box.addEventListener('change',update);startView.append(start.box);
  function remember(){try{localStorage.setItem(navigation,selected);}catch{}}
  history.addEventListener('change',()=>{selected=history.value;remember();void refresh();});
  function checkBatch(value){
    if(value?.workspace_id!==workspaceId||!/^pmbt_[0-9a-f]{32}$/.test(value.batch_id)||!op.test(value.operation_id)||!Number.isSafeInteger(value.version)||value.version<0
      ||!Array.isArray(value.entries)||value.entries.length>16||!['not_evaluated','see_explicit_original_evidence'].includes(value.requirement_acceptance)||value.model_provider_cost!=='unmeasured')throw error();
    for(const entry of value.entries)if(!/^[a-z][a-z0-9_]{0,47}$/.test(entry.key)||!sha.test(entry.source_sha)||!Array.isArray(entry.depends_on)||!Array.isArray(entry.verification_evidence)
      ||entry.operation_id&&!op.test(entry.operation_id))throw error();return value;
  }
  function proofFacts(proof){
    const ref=proof.reference||{};return h('details',{},h('summary',{},t(proof.status==='passed'?(proof.condition?.profile===acceptanceProfile?'pmb_accepted':'pmb_verified'):'pmb_unproven')),
      proof.reason?h('p',{class:'muted'},proof.reason):null,
      ...Object.entries({review_id:'pmb_assessment',operation_id:'pmb_assessment_operation',version:'pmb_assessment_version',observation_id:'pmb_observation',document_digest:'pmb_observation_digest',candidate_commit:'pmb_result_commit',source_seq:'pmb_source_seq',verification_id:'pmb_verification_id',tree_hash:'pmb_result_tree'}).filter(([key])=>ref[key]!==undefined).map(([key,label])=>line(label,ref[key])),
      ref.integration_operation_id&&op.test(ref.integration_operation_id)?h('a',{href:`#/op/${ref.integration_operation_id}`},t('pmb_delivery')):null,
      proof.condition?.command_digest?line('pmb_command_digest',proof.condition.command_digest):null);
  }
  function renderBatch(){
    if(!batch){facts.replaceChildren(text('pmb_no_history'));return;}
    const limits=batch.limits,children=batch.entries.map(entry=>h('article',{class:'pm-batch-entry'},h('h4',{},labels.get(entry.key)||entry.key),
      line('pmb_state',t('pmb_state_'+entry.state)===('pmb_state_'+entry.state)?entry.state:t('pmb_state_'+entry.state)),entry.reason?h('p',{class:'muted'},t('pmb_blocked_reason'),' · ',entry.reason):null,
      line('pmb_profile_label',t(condition(entry).profile===acceptanceProfile?'pmb_acceptance_profile':condition(entry).profile===profile.profile?'pmb_github_profile':'pmb_observed_profile')),
      entry.operation_id?h('a',{href:`#/op/${entry.operation_id}`},t('pmb_child_operation')):text('pmb_no_child'),
      h('details',{},h('summary',{},t('pmb_fixed_sources')),line('pmb_entry_key',entry.key),line('pmb_package',entry.package_ref.package_id),line('pmb_base',entry.source_sha),
        ...entry.depends_on.map(edge=>h('p',{},t('pmb_wait_for'),' ',edge.entry_key))),
      ...entry.verification_evidence.map(proofFacts)));
    facts.replaceChildren(h('h3',{},t('pmb_saved')),line('pmb_operation_status',batch.operation_status),
      h('p',{},`${t('pmb_starts')}: ${limits.max_starts} · ${t('pmb_parallel')}: ${limits.max_parallel} · ${t('pmb_retries')}: ${limits.max_retries}`),
      line('pmb_deadline',new Date(limits.deadline_at*1000).toLocaleString()),text('pmb_budget_help'),text('pmb_boundary'),
      h('a',{href:`#/op/${batch.operation_id}`},t('pmb_parent_operation')),...children);
  }
  const receiptKey=row=>`${row.operation_id}:${row.seq}`;
  function reviewChoices(entry){return qualityReviews.filter(row=>entry&&condition(entry).profile===acceptanceProfile&&same(row.review.context.package_ref,entry.package_ref)&&matchesRequirementReview(row.review,row.package,condition(entry).requirements.map(item=>item.requirement_id),entry.operation_id));}
  function validProof(){
    if(!proofReady||!proof||!batch||proofBaseline?.batch_id!==batch.batch_id||proofBaseline.version!==batch.version)return false;
    const entry=batch.entries.find(entry=>entry.key===proof.fields.entry.value);if(!entry)return false;
    if(condition(entry).profile===acceptanceProfile)return reviewChoices(entry).some(row=>row.key===proof.fields.assessment.value);
    return condition(entry).profile===profile.profile&&receipts.some(row=>receiptKey(row)===proof.fields.receipt.value&&row.entry_key===entry.key)&&observations.some(row=>row.observation_id===proof.fields.observation.value);
  }
  function syncProofOptions(){
    if(!proof||!proofReady)return;
    const choices={entry:batch.entries.filter(entry=>entry.operation_id&&[profile.profile,acceptanceProfile].includes(condition(entry).profile)).map(entry=>[entry.key,labels.get(entry.key)||entry.key]),
      receipt:receipts.filter(row=>row.entry_key===(proofChoices?.entry??proof.fields.entry.value)).map(row=>[receiptKey(row),`${row.repository} · ${row.candidate_commit.slice(0,12)} · ${row.operation_id}`]),
      assessment:reviewChoices(batch.entries.find(entry=>entry.key===(proofChoices?.entry??proof.fields.entry.value))).map(row=>[row.key,`${row.package.document.title} · ${row.review.context.native_result.candidate_commit.slice(0,12)} · ${t('pmb_assessment_version')} ${row.review.assessment.version} · ${row.review.context.reviewer_actor}`]),
      observation:observations.map(row=>[row.observation_id,`${new Date(row.created_at*1000).toLocaleString()} · ${row.document?.scope?.repository||row.observation_id} · ${t(row.document?.evidence?.status==='complete'?'pmb_observation_complete':'pmb_observation_incomplete')}`])};
    for(const [key,rows]of Object.entries(choices)){const node=proof.fields[key],value=proofChoices?.[key]??node.value;node.replaceChildren(h('option',{value:''},t('pmb_choose')),...rows.map(([id,label])=>h('option',{value:id},label)));
      if(value&&!rows.some(([id])=>id===value))node.append(h('option',{value},t('pmb_original_missing')));node.value=value;}proofChoices=null;
    const semantic=condition(batch.entries.find(entry=>entry.key===proof.fields.entry.value)||{}).profile===acceptanceProfile;
    for(const name of ['receipt','observation'])proof.fields[name].parentElement.hidden=semantic;proof.fields.assessment.parentElement.hidden=!semantic;
  }
  async function bindProof(ticket){
    if(!batch)return;
    const entries=batch.entries.filter(entry=>entry.operation_id&&[profile.profile,acceptanceProfile].includes(condition(entry).profile));
    if(!entries.length){proof?.dispose?.();proofView.replaceChildren(text('pmb_no_evidence_entry'));proof=null;proofChoices=null;return;}
    const semanticEntries=entries.filter(entry=>condition(entry).profile===acceptanceProfile);
    if(semanticEntries.length){const catalog=await readRequirementReviews(api,workspaceId,()=>valid(ticket));if(!catalog||!valid(ticket))return;qualityReviews=catalog.rows;evidenceFiltered ||= catalog.filtered;}
    const githubEntries=entries.filter(entry=>condition(entry).profile===profile.profile);
    const data=githubEntries.length?await pages(`${base}/github-observations`,'observations'):{rows:[],filtered:false};if(!valid(ticket)||!data)return;
    observations=data.rows;evidenceFiltered ||= data.filtered;for(const row of observations)if(!/^pmgh_[0-9a-f]{32}$/.test(row.observation_id)||!digest.test(row.document_digest)||row.workspace_id!==workspaceId)throw error();
    const deliveries=[];for(const entry of githubEntries){const data=await pages(`${base}/work/${encodeURIComponent(entry.work_item_id)}/deliveries`,'deliveries','next_after','source_visibility_filtered',data=>{if(data.work_item_id!==entry.work_item_id)throw error();});if(!valid(ticket)||!data)return;
      evidenceFiltered ||= data.filtered;for(const delivery of data.rows)if(delivery.action==='integration.apply'&&op.test(delivery.operation_id))for(const source of delivery.integration?.sources||[])
        if(source.source_kind==='execution'&&source.source_id===entry.operation_id&&source.status==='delivered'&&sha.test(source.delivered_sha)&&Number.isSafeInteger(source.seq)&&source.seq>=0)
          deliveries.push({entry_key:entry.key,operation_id:delivery.operation_id,seq:source.seq,candidate_commit:source.delivered_sha,repository:delivery.target?.repository||''});
    }receipts=deliveries;proofReady=true;evidenceNotice.hidden=!evidenceFiltered;
    if(proofBaseline?.batch_id!==batch.batch_id||!proof){
      proof?.dispose?.();proofChoices=null;proofBaseline={batch_id:batch.batch_id,version:batch.version};
      const entry=choose('pmb_evidence_entry',[]),receipt=choose('pmb_delivery',[]),observation=choose('pmb_observation',[]),assessment=choose('pmb_assessment',[]),rationale=input('pmb_rationale',{multiline:true,max:4000}),version=hidden(batch.version);
      const rebase=h('button',{type:'button',class:'secondary',hidden:true,onclick:()=>{if(writer()&&!proof.locked()){proofBaseline.version=batch.version;proof.set('_version',JSON.stringify(batch.version));update();}}},t('pmb_use_evidence_version'));proofRebase=rebase;
      proof=form({namespace:`${namespace}.work-batch-evidence`,api:request,action:RECORD,target:{workspace_id:workspaceId,batch_id:batch.batch_id},fields:{entry,receipt,observation,assessment,rationale,_version:version},
        params:values=>{if(!validProof())throw Error(t('pmb_selection_required'));const selected=batch.entries.find(row=>row.key===values.entry);
          if(condition(selected).profile===acceptanceProfile){const chosen=reviewChoices(selected).find(row=>row.key===values.assessment);return {evidence:[{entry_key:values.entry,condition:clone(condition(selected)),reference:JSON.parse(referenceKey(chosen.review))}],rationale:values.rationale};}
          const source=receipts.find(row=>receiptKey(row)===values.receipt&&row.entry_key===values.entry),observed=observations.find(row=>row.observation_id===values.observation);
          return {evidence:[{entry_key:values.entry,condition:clone(condition(batch.entries.find(row=>row.key===values.entry))),reference:{integration_operation_id:source.operation_id,source_seq:source.seq,candidate_commit:source.candidate_commit,
            observation_id:observed.observation_id,document_digest:observed.document_digest}}],rationale:values.rationale};},
        preconditions:()=>({expected_version:JSON.parse(version.value)}),permitted:()=>writer()&&proofReady&&JSON.parse(version.value)===batch.version,recoverPermitted:writer,label:'pmb_record',
        onStateChange:()=>{if(proof){if(!proof.locked()&&!validProof())proof.box.querySelector('button[type=submit]').disabled=true;if(proofRebase)proofRebase.hidden=!ready||!batch||proof.locked()||Number(proof.fields._version.value)===batch.version;}},onSaved:()=>refresh(),
        extra:h('div',{class:'pm-fields'},text('pmb_evidence_help'),evidenceNotice,labelled('pmb_evidence_entry',entry),labelled('pmb_delivery',receipt),labelled('pmb_observation',observation),labelled('pmb_assessment',assessment),labelled('pmb_rationale',rationale),h('div',{class:'actions'},rebase))});
      proofBaseline.version=JSON.parse(version.value);entry.addEventListener('change',()=>{syncProofOptions();update();});proof.box.addEventListener('input',update);proof.box.addEventListener('change',update);
    }
    syncProofOptions();proofView.replaceChildren(h('details',{open:true},h('summary',{},t('pmb_evidence')),proof.box));
  }
  async function refresh(){
    const ticket=++serial;ready=false;batch=null;clearSources();status.replaceChildren(text('pmb_reading'));update();
    try{if(!supported())throw Error(t('pmb_not_supported'));
      const [latest,historyData]=await Promise.all([readContext(),pages(`${base}/work-batches`,'batches')]);if(!valid(ticket)||!historyData)return;
      context=latest;for(const row of historyData.rows)checkBatch(row);
      if(!selected&&historyData.rows.length&&!historyData.filtered){selected=historyData.rows.at(-1).batch_id;remember();}
      if(selected){const data=await api('GET',`${base}/work-batches/${encodeURIComponent(selected)}`);if(!valid(ticket))return;
        if(data.workspace_id!==workspaceId||data.batch?.batch_id!==selected)throw error();batch=checkBatch(data.batch);
        for(const entry of batch.entries){const data=await api('GET',`${base}/work-packages/${encodeURIComponent(entry.package_ref.package_id)}`);if(!valid(ticket))return;const pkg=data.package;
          if(data.workspace_id!==workspaceId||pkg?.workspace_id!==workspaceId||pkg.package_id!==entry.package_ref.package_id||pkg.document_digest!==entry.package_ref.document_digest||pkg.work_item_id!==entry.work_item_id)throw error();
          labels.set(entry.key,pkg.document.title);}
      }
      if(!await checkDraft(draft(),ticket)||!valid(ticket))return;sourceReady=true;
      if(baseline.value==='null'&&!start.locked())start.set('_baseline',JSON.stringify(currentPre()));
      if(batch)await bindProof(ticket);if(!valid(ticket))return;
      history.replaceChildren(h('option',{value:''},t('pmb_choose')),...historyData.rows.map(row=>h('option',{value:row.batch_id},`${t('pmb_batch')} ${row.sequence} · ${row.operation_status}`)));
      if(selected&&!historyData.rows.some(row=>row.batch_id===selected))history.append(h('option',{value:selected},t('pmb_selected_original')));history.value=selected;
      ready=true;startView.hidden=false;proofView.hidden=!batch;renderEntries();renderBatch();status.replaceChildren();if(historyData.filtered)status.append(text('pmb_filtered'));update();
    }catch(err){if(valid(ticket))failure(err);}
  }
  update();return {element,refresh,withdraw,update};
}
