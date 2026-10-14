import {iconButton, skeleton} from './ui/components.js';
import {workspaceExecutionVerification} from './pm-execution-verification.js';
// Fixed quality conclusions remain central; this view selects original receipts.
const CREATE='pm.quality.review.create',RECORD='pm.quality.review.record',REVOKE='pm.quality.review.revoke';
const sha=/^[0-9a-f]{40}$/,digest=/^[0-9a-f]{64}$/,operation=/^op_[0-9a-f]{32}$/,reviewId=/^pmqr_[0-9a-f]{32}$/;
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b),clone=value=>structuredClone(value);
const github={kind:'fixed_revision_verification',profile:'github_reported_checks_v1'};
export function workspaceQualityReviews({h,t,api,alive,workspaceId,workspace,caps,permitted,form,input,labelled,errorBox,namespace,onSelectionChange=()=>{}}){
  const base=`/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  let workId=null,serial=0,ready=false,filtered=false,model=null,selected='',chosen=null,create=null,record=null,revoke=null,formKey='',matrixKey='',verifier=null;
  const navigation=`batc.pm.quality-navigation.${namespace}.${workspaceId}`;
  const workChoice=h('select',{'aria-label':t('pmq_work')}),status=h('div',{role:'status'}),facts=h('div'),draft=h('div'),assessment=h('div'),withdrawal=h('div'),history=h('select',{'aria-label':t('pmq_history')});
  const text=key=>h('p',{class:'muted'},t(key)),line=(key,value)=>h('p',{class:'pm-source'},h('strong',{},t(key)+': '),String(value??t('pmq_none')));
  const choice=key=>h('select',{'aria-label':t(key)}),hidden=value=>h('input',{type:'hidden',value:JSON.stringify(value),'data-pm-default':JSON.stringify(value)});
  const element=h('section',{class:'panel pm-quality-reviews','data-pm-quality-reviews':''},
    h('header',{class:'pm-heading'},h('div',{},h('h2',{},t('pmq_title')),text('pmq_intro')),
      iconButton({icon:'refresh-cw', label:t('pmq_refresh'), ...{type:'button',class:'secondary',onclick:()=>void refresh()}})),labelled('pmq_work',workChoice),status,
    h('details',{'data-quality-create':''},h('summary',{},t('pmq_create_heading')),draft),labelled('pmq_history',history),facts,assessment,withdrawal);
  const valid=ticket=>{try{alive();return ticket===serial;}catch{return false;}};
  const member=()=>workspace()?.members?.find(row=>row.actor===caps()?.actor)?.role||workspace()?.membership?.role;
  const writer=()=>ready&&permitted(false)&&['owner','contributor'].includes(member())&&caps()?.scopes?.includes('manage');
  const owner=()=>writer()&&member()==='owner';
  const current=()=>model?.reviews.find(row=>row.review_id===selected)||null;
  const supported=()=>caps()?.features?.pm_workspaces?.quality_reviews?.version===1;
  const error=()=>Error(t('pmq_invalid'));
  function emit(ref){chosen=ref?clone(ref):null;if(workId)onSelectionChange(workId,chosen?clone(chosen):null);}
  function options(field,rows){const previous=field.value;field.replaceChildren(h('option',{value:''},t('pmq_choose')),...rows.map(([id,label])=>h('option',{value:id},label)));
    if(previous&&!rows.some(([id])=>id===previous))field.append(h('option',{value:previous},t('pmq_original_missing')));field.value=previous;}
  function update(){for(const editor of [create,record,revoke])editor?.update();history.disabled=!ready;}
  function clear(){ready=false;model=null;verifier?.withdraw();facts.replaceChildren();history.replaceChildren();draft.hidden=assessment.hidden=withdrawal.hidden=true;emit(null);update();}
  function purge(){for(const editor of [create,record,revoke])editor?.dispose();create=record=revoke=null;formKey=matrixKey='';draft.replaceChildren();assessment.replaceChildren();withdrawal.replaceChildren();}
  function withdraw(){serial++;clear();purge();workChoice.replaceChildren();status.replaceChildren();}
  function failure(err){serial++;clear();purge();workChoice.replaceChildren();status.replaceChildren(text('pmq_unavailable'),errorBox(err));}
  const request=async(...args)=>{const ticket=serial;try{return await api(...args);}catch(err){if(valid(ticket)&&[401,403,404].includes(err.status))failure(err);throw err;}};
  async function pages(path,key,ticket,{cursor='next_cursor',wid=true,descending=false}={}){
    let after=descending?null:0;const rows=[],seen=new Set();
    for(let page=0;page<20;page++){
      const query=`${path.includes('?')?'&':'?'}limit=50${after===null?'':`&${descending?'before':'after'}=${after}`}`;
      const value=await request('GET',path+query);if(!valid(ticket))return null;
      if(value.visibility_filtered||value.source_visibility_filtered||value.coverage?.visibility_filtered||value.coverage?.inaccessible_sources)filtered=true;
      if(wid&&value.workspace_id!==workspaceId||!Array.isArray(value[key])||value[cursor]!==null&&(!Number.isFinite(value[cursor])||(!descending&&!Number.isSafeInteger(value[cursor]))||value[cursor]<=0||after!==null&&(descending?value[cursor]>=after:value[cursor]<=after)))throw error();
      for(const row of value[key]){const id=row.review_id||row.package_id||row.revision_id||row.observation_id||row.evidence_id||row.operation_id;if(!id||seen.has(id))throw error();seen.add(id);rows.push(row);}
      if(value[cursor]===null)return rows;after=value[cursor];
    }throw Error(t('pmq_history_bound'));
  }
  function checkReview(row){
    if(row?.workspace_id!==workspaceId||row.work_item_id!==workId||!reviewId.test(row.review_id)||!operation.test(row.operation_id)||!Number.isSafeInteger(row.version)||row.version<1
      ||!digest.test(row.context_digest)||!Array.isArray(row.context?.criteria)||!Array.isArray(row.history)||!Array.isArray(row.applicability_reasons)||typeof row.qualified!=='boolean'
      ||!sha.test(row.context?.native_result?.candidate_commit)||!operation.test(row.context?.execution_operation_id)||!row.original_outcome
      ||row.assessment&&(!operation.test(row.assessment.operation_id)||!digest.test(row.assessment.document_digest)||!Number.isSafeInteger(row.assessment.version)||!Array.isArray(row.assessment.document?.criteria))
      ||row.qualified&&!row.assessment)throw error();return row;
  }
  function packageFor(id){return model?.packages.find(row=>row.package_id===id);}
  function children(pkg){return (model?.operations||[]).filter(op=>op.action==='repository.continue'&&op.status==='succeeded'&&op.params?.work_item_id===workId
    &&same(op.external_refs?.admission_binding?.pm_handoff?.contract?.package_ref,{workspace_id:workspaceId,package_id:pkg?.package_id,document_digest:pkg?.document_digest}));}
  function criteria(pkg,dod){return [...(pkg?.document.requirements||[]).flatMap(req=>(req.acceptance||[]).map(item=>({criterion:{kind:'acceptance',requirement_id:req.id,input_id:item.source_input_id},text:item.text}))),
    ...(dod?.document.criteria||[]).map(item=>({criterion:{kind:'dod',criterion_id:item.id},text:item.text}))];}
  function dodChoices(pkg){const fixed=pkg?.document.context.dod_ref;return (model?.dods||[]).filter(row=>row.document.plan_ref?.scope_revision===pkg?.document.context.plan_ref.scope_revision&&(!fixed||row.revision_id===fixed.revision_id));}
  function policySources(){return [...model.proofs,...(model.configuration?[{key:'configured:'+model.configuration.execution_operation_id,condition:{kind:'fixed_revision_verification',profile:'observed_runner_v1',command_digest:model.configuration.command_digest},reference:{candidate_commit:model.configuration.candidate_commit||'',verification_id:t('pmq_configured')}}]:[])];}
  function policyChoices(){return [['github',t('pmq_github')],...policySources().filter(row=>row.condition.profile==='observed_runner_v1').map(row=>[row.key,`${t('pmq_runner')} · ${row.reference.candidate_commit.slice(0,12)} · #${row.reference.verification_id||row.reference.verification_operation_id}`])];}
  function policy(value){if(value==='github')return github;const row=policySources().find(row=>row.key===value&&row.condition.profile==='observed_runner_v1');if(!row)throw Error(t('pmq_select_proof'));return row.condition;}
  function buildCreate(){
    if(!create){
      const pkg=choice('pmq_package'),child=choice('pmq_execution'),candidate=input('pmq_candidate',{max:40}),dod=choice('pmq_dod'),reviewer=choice('pmq_reviewer'),rationale=input('pmq_policy_reason',{multiline:true,max:4000});
      const policies=hidden([]),baseline=hidden(null),matrix=h('div'),stale=text('pmq_stale');stale.hidden=true;
      const rebase=h('button',{type:'button',class:'secondary',hidden:true,onclick:()=>{if(create&&!create.locked()&&ready){create.set('_previous',JSON.stringify(previous()));sync();}}},t('pmq_use_version'));
      const previous=()=>model?.reviews.find(row=>row.context.package_ref.package_id===pkg.value&&row.context.native_result.candidate_commit===candidate.value
        &&(row.context.dod_ref?.revision_id||'')===dod.value&&!row.applicability_reasons.includes('review_superseded'))?.review_id||null;
      const chosenCriteria=()=>criteria(packageFor(pkg.value),model?.dods.find(row=>row.revision_id===dod.value));
      const rows=()=>{try{return JSON.parse(policies.value);}catch{return [];}};
      const permittedCreate=()=>owner()&&packageFor(pkg.value)&&children(packageFor(pkg.value)).some(row=>row.operation_id===child.value)&&sha.test(candidate.value)
        &&reviewer.value&&chosenCriteria().length>0&&same(rows().map(row=>row.criterion),chosenCriteria().map(row=>row.criterion))&&rows().every(row=>{try{return same(row.condition,policy(row.choice));}catch{return false;}})
        &&JSON.parse(baseline.value)===previous();
      const sync=()=>{if(!ready)return;const original=packageFor(pkg.value);options(pkg,model.packages.map(row=>[row.package_id,`${row.document.title} · ${row.package_id}`]));
        options(child,children(original).map(row=>[row.operation_id,`${row.actor} · ${row.operation_id}`]));
        options(dod,dodChoices(original).map(row=>[row.revision_id,`${t('pmq_dod')} · ${row.revision_id}`]));
        if(original?.document.context.dod_ref&&!dod.value&&!create.locked())create.set('dod',original.document.context.dod_ref.revision_id);
        options(reviewer,(workspace()?.members||[]).filter(row=>['owner','contributor'].includes(row.role)).map(row=>[row.actor,row.actor]));
        const signature=JSON.stringify([pkg.value,dod.value,chosenCriteria(),policySources().map(row=>row.key)]);
        if(signature!==matrixKey){matrixKey=signature;matrix.replaceChildren();const saved=rows();
          for(const criterion of chosenCriteria()){const field=choice('pmq_policy_profile'),old=saved.find(row=>same(row.criterion,criterion.criterion));options(field,policyChoices());field.value=old?.choice||'';
            field.addEventListener('change',()=>{if(create.locked())return;const values=rows().filter(row=>!same(row.criterion,criterion.criterion));if(field.value)values.push({criterion:criterion.criterion,condition:policy(field.value),choice:field.value});
              const ordered=chosenCriteria().map(item=>values.find(row=>same(row.criterion,item.criterion))||{criterion:item.criterion,condition:null,choice:''});create.set('criteria',JSON.stringify(ordered));create.update();});
            matrix.append(h('div',{class:'pm-fields'},h('p',{class:'pm-note-text'},criterion.text),labelled('pmq_policy_profile',field)));}
        }
        for(const node of matrix.querySelectorAll('select'))node.disabled=!owner()||create.locked();
        stale.hidden=rebase.hidden=!owner()||create.locked()||JSON.parse(baseline.value)===previous();create.update();};
      create=form({namespace:`${namespace}.quality.${workId}`,api:request,action:CREATE,target:{workspace_id:workspaceId,work_item_id:workId},
        fields:{package:pkg,execution:child,candidate,dod,reviewer,criteria:policies,_previous:baseline,rationale},
        params:values=>{const original=packageFor(values.package);if(!permittedCreate())throw error();return {package_ref:{workspace_id:workspaceId,package_id:original.package_id,document_digest:original.document_digest},
          execution_operation_id:values.execution,candidate_commit:values.candidate,dod_revision_id:values.dod||null,reviewer_actor:values.reviewer,
          criteria:JSON.parse(values.criteria).map(({choice,...row})=>row),rationale:values.rationale};},preconditions:()=>({expected_previous_review_id:JSON.parse(baseline.value)}),
        permitted:()=>owner()&&JSON.parse(baseline.value)===previous(),recoverPermitted:writer,label:'pmq_create',onSaved:async result=>{selected=result.review_id;remember();matrixKey='';await refresh();},
        onStateChange:()=>{if(create){for(const node of matrix.querySelectorAll('select'))node.disabled=!owner()||create.locked();if(!create.locked())create.box.querySelector('button[type=submit]').disabled=!permittedCreate();}},
        extra:h('div',{class:'pm-fields'},text('pmq_owner_help'),h('div',{class:'capture-fields'},labelled('pmq_package',pkg),labelled('pmq_execution',child)),
          labelled('pmq_candidate',candidate),text('pmq_candidate_help'),h('div',{class:'capture-fields'},labelled('pmq_dod',dod),labelled('pmq_reviewer',reviewer)),
          text('pmq_dod_help'),matrix,labelled('pmq_policy_reason',rationale),stale,rebase)});
      for(const field of [pkg,dod,candidate])field.addEventListener('change',sync);for(const field of [child,candidate,reviewer])field.addEventListener('input',()=>create.update());
      create.sync=sync;draft.replaceChildren(create.box);
    }create.sync();draft.hidden=false;
  }
  function renderReview(){
    const row=current();facts.replaceChildren();if(!row){facts.append(text('pmq_empty'));assessment.hidden=withdrawal.hidden=true;return;}
    const document=row.assessment?.document;
    const use=h('button',{type:'button',class:'secondary',disabled:!selectable(row),onclick:()=>{if(!ready||!selectable(current()))return;emit(reference(current()));renderReview();}},t('pmq_use'));
    facts.append(...[h('h3',{},t('pmq_result')),h('p',{},`${t('pmq_acceptance')}: ${t('pmq_'+row.acceptance)} · ${t('pmq_dod')}: ${t('pmq_'+row.dod)}`),
      line('pmq_candidate',row.context.native_result.candidate_commit),line('pmq_reviewer',row.context.reviewer_actor),
      row.context.self_review?text('pmq_self_review'):null,...row.applicability_reasons.map(reason=>text('pmq_reason_'+reason)),
      row.original_outcome.qualified&&!row.qualified?text('pmq_original_qualified'):null,
      h('details',{},h('summary',{},t('pmq_original_sources')),line('pmq_package',row.context.package_ref.package_id),line('pmq_digest',row.context.package_ref.document_digest),
        line('pmq_scope',row.context.plan_ref.scope_revision),line('pmq_binding',row.context.work_ref.binding_id),line('pmq_dod',row.context.dod_ref?.revision_id),
        h('a',{href:`#/op/${row.context.execution_operation_id}`},t('pmq_execution')),h('p',{class:'pm-note-text'},row.context.policy_rationale)),
      ...(row.context.criteria||[]).map(item=>{const saved=document?.criteria.find(value=>same(value.criterion,item.criterion));return h('details',{},h('summary',{},`${item.text} · ${t('pmq_'+(saved?.status||'unassessed'))}`),
        saved?h('p',{class:'pm-note-text'},saved.rationale):null,saved?.verification?line('pmq_verification',t('pmq_'+saved.verification.status)):text('pmq_no_proof'));}),
      row.revocation?h('div',{},h('h4',{},t('pmq_revoked')),h('p',{class:'pm-note-text'},row.revocation.document.reason),line('pmq_by',row.revocation.actor)):null,
      h('details',{},h('summary',{},t('pmq_history_receipts')),...row.history.map(item=>h('p',{class:'pm-source'},`${item.version} · ${item.actor} · ${t('pmq_'+item.kind)} · `,
        h('a',{href:`#/op/${item.operation_id}`},item.operation_id),h('span',{},' · '+item.document_digest)))),use,
      chosen&&same(chosen,reference(row))?text('pmq_selected'):text('pmq_selection_help')].filter(Boolean));
    buildAssessment(row);
  }
  function reference(row){const a=row.assessment;return a?{review_id:row.review_id,operation_id:a.operation_id,version:a.version,document_digest:a.document_digest,candidate_commit:row.context.native_result.candidate_commit}:null;}
  function selectable(row){return Boolean(row?.assessment&&row.applicable===true&&!row.revocation);}
  function proofChoices(row,item){return model.proofs.filter(proof=>proof.reference.candidate_commit===row.context.native_result.candidate_commit&&same(proof.condition,item.condition)
    &&(!proof.source_operation_id||proof.source_operation_id===row.context.execution_operation_id));}
  function buildAssessment(row){
    const key=row.review_id;if(formKey!==key){record?.dispose();revoke?.dispose();formKey=key;
      const version=hidden(row.version),rationale=input('pmq_assessment_reason',{multiline:true,max:4000}),fields={_version:version,rationale},matrix=h('div',{class:'pm-fields'});
      row.context.criteria.forEach((item,index)=>{const verdict=choice('pmq_verdict'),proof=choice('pmq_proof'),reason=input('pmq_criterion_reason',{multiline:true,max:2000});
        options(verdict,['unassessed','accepted','rejected'].map(value=>[value,t('pmq_'+value)]));verdict.value='unassessed';verdict.dataset.pmDefault='unassessed';
        options(proof,proofChoices(row,item).map(value=>[value.key,value.label]));fields['verdict_'+index]=verdict;fields['proof_'+index]=proof;fields['reason_'+index]=reason;
        matrix.append(h('div',{},h('h4',{},item.text),h('div',{class:'capture-fields'},labelled('pmq_verdict',verdict),labelled('pmq_proof',proof)),labelled('pmq_criterion_reason',reason)));});
      const reviewer=()=>writer()&&current()?.context.reviewer_actor===caps()?.actor;
      const active=()=>current()&&!current().revocation&&!current().applicability_reasons.includes('review_superseded');
      const rebase=h('button',{type:'button',class:'secondary',onclick:()=>{if(record&&!record.locked()&&current()){record.set('_version',JSON.stringify(current().version));record.update();}}},t('pmq_use_version'));
      record=form({namespace:`${namespace}.quality.${workId}`,api:request,action:RECORD,target:{workspace_id:workspaceId,review_id:row.review_id},fields,
        params:values=>({conclusions:row.context.criteria.map((item,index)=>{const key=values['proof_'+index],proof=key?proofChoices(current(),item).find(value=>value.key===key):null;if(key&&!proof)throw Error(t('pmq_select_proof'));
          return {criterion:item.criterion,verdict:values['verdict_'+index],rationale:values['reason_'+index],reference:proof?.reference||null};}),rationale:values.rationale}),
        preconditions:()=>({expected_version:JSON.parse(version.value)}),permitted:()=>reviewer()&&active()&&JSON.parse(version.value)===current().version,recoverPermitted:writer,
        label:'pmq_record',onSaved:()=>refresh(),onStateChange:()=>{rebase.hidden=!reviewer()||JSON.parse(version.value)===current().version;rebase.disabled=!!record?.locked();},extra:h('div',{class:'pm-fields'},text('pmq_reviewer_help'),matrix,labelled('pmq_assessment_reason',rationale),h('div',{class:'actions'},rebase))});
      const reason=input('pmq_revoke_reason',{multiline:true,max:4000}),revokeVersion=hidden(row.version);
      revoke=form({namespace:`${namespace}.quality.${workId}`,api:request,action:REVOKE,target:{workspace_id:workspaceId,review_id:row.review_id},fields:{reason,_version:revokeVersion},params:values=>({reason:values.reason}),
        preconditions:()=>({expected_version:JSON.parse(revokeVersion.value)}),permitted:()=>writer()&&(owner()||reviewer())&&active()&&JSON.parse(revokeVersion.value)===current().version,recoverPermitted:writer,
        label:'pmq_revoke',onSaved:()=>refresh(),extra:h('div',{class:'pm-fields'},labelled('pmq_revoke_reason',reason),h('button',{type:'button',class:'secondary',onclick:()=>{if(revoke&&!revoke.locked()&&current()){revoke.set('_version',JSON.stringify(current().version));revoke.update();}}},t('pmq_use_version')))});
      assessment.replaceChildren(h('details',{'data-quality-assess':'',open:row.context.reviewer_actor===caps()?.actor&&(!row.assessment||record.locked())},h('summary',{},t('pmq_assess_heading')),record.box));
      withdrawal.replaceChildren(h('details',{},h('summary',{},t('pmq_revoke_heading')),revoke.box));
    }
    row.context.criteria.forEach((item,index)=>options(record.fields['proof_'+index],proofChoices(row,item).map(value=>[value.key,value.label])));
    assessment.hidden=withdrawal.hidden=false;update();
  }
  function remember(){try{localStorage.setItem(navigation,JSON.stringify({workId,review_id:selected}));}catch{}}
  history.addEventListener('change',()=>{selected=history.value;emit(null);remember();renderReview();});
  async function refresh(){
    const id=workId,ticket=++serial,previous=chosen;clear();filtered=false;if(!supported())return;status.replaceChildren(skeleton(2));
    try{
      const catalog=await pages(`${base}/work-packages`,'packages',ticket);if(!valid(ticket))return;
      if(catalog.some(row=>row.workspace_id!==workspaceId||!/^wi_[0-9a-f]{20}$/.test(row.work_item_id)))throw error();
      options(workChoice,[...new Map(catalog.map(row=>[row.work_item_id,`${row.document.title} · ${row.work_item_id}`])).entries()]);workChoice.value=id||'';
      if(!id){status.replaceChildren(text('pmq_choose_work'));return;}
      const packages=catalog.filter(row=>row.work_item_id===id);
      const [operations,dods,reviews,evidence,deliveries,observations,verifications]=await Promise.all([pages('/operations?action=repository.continue&status=succeeded','operations',ticket,{cursor:'next_before',wid:false,descending:true}),
        pages(`${base}/sprint-artifacts/dod`,'revisions',ticket,{wid:false}),pages(`${base}/work/${id}/quality`,'reviews',ticket),
        pages(`${base}/evidence?work_item_id=${id}`,'evidence',ticket),pages(`${base}/work/${id}/deliveries`,'deliveries',ticket,{cursor:'next_after'}),pages(`${base}/github-observations`,'observations',ticket),caps()?.features?.pm_workspaces?.execution_verification?.version===1?pages('/operations?action=pm.execution.verify','operations',ticket,{cursor:'next_before',wid:false,descending:true}):Promise.resolve([])]);
      if(!valid(ticket)||workId!==id)return;
      const proofs=[];
      for(const op of verifications.filter(value=>value.action==='pm.execution.verify'&&value.target?.workspace_id===workspaceId&&operations.some(child=>child.operation_id===value.target.execution_operation_id&&child.params?.work_item_id===id))){
        const saved=await request('GET',`${base}/execution-verifications/${op.operation_id}`);if(!valid(ticket))return;const receipt=saved.verification;
        if(saved.workspace_id!==workspaceId)throw error();if(!receipt)continue;
        if(receipt.operation_id!==op.operation_id||receipt.workspace_id!==workspaceId||receipt.execution_operation_id!==op.target.execution_operation_id||!digest.test(receipt.receipt_digest)||receipt.reference?.verification_operation_id!==op.operation_id
          ||!sha.test(receipt.reference.candidate_commit)||!sha.test(receipt.reference.tree_hash)||!digest.test(op.params?.command_digest)||receipt.profile!=='observed_runner_v1')throw error();
        proofs.push({key:op.operation_id,label:`${t('pmq_runner')} · ${receipt.reference.candidate_commit.slice(0,12)} · ${op.operation_id}`,source_operation_id:op.target.execution_operation_id,
          condition:{kind:'fixed_revision_verification',profile:'observed_runner_v1',command_digest:op.params.command_digest},reference:receipt.reference});
      }
      for(const entry of evidence.filter(value=>value.source_kind==='observed_verification')){const data=await request('GET',`${base}/evidence/${entry.evidence_id}`);if(!valid(ticket))return;
        const e=data.evidence,f=e?.document?.facts;if(e?.workspace_id!==workspaceId||e.evidence_id!==entry.evidence_id||!f||!sha.test(f.candidate_commit)||!sha.test(f.tree_hash)||!digest.test(f.command_digest)||!Number.isSafeInteger(f.verification_id)||f.verification_id<1)throw error();
        proofs.push({key:entry.evidence_id,label:`${t('pmq_runner')} · #${f.verification_id} · ${f.candidate_commit.slice(0,12)}`,
          condition:{kind:'fixed_revision_verification',profile:'observed_runner_v1',command_digest:f.command_digest},reference:{verification_id:f.verification_id,candidate_commit:f.candidate_commit,tree_hash:f.tree_hash}});}
      for(const delivery of deliveries)for(const source of delivery.integration?.sources||[]){if(source.source_kind!=='execution'||source.status!=='delivered'||!sha.test(source.delivered_sha)||!operation.test(source.source_id))continue;
        for(const observation of observations){if(!digest.test(observation.document_digest))throw error();proofs.push({key:`${delivery.operation_id}:${source.seq}:${observation.observation_id}`,source_operation_id:source.source_id,
          label:`${t('pmq_github')} · ${source.delivered_sha.slice(0,12)} · ${observation.observation_id}`,condition:github,
          reference:{integration_operation_id:delivery.operation_id,source_seq:source.seq,candidate_commit:source.delivered_sha,observation_id:observation.observation_id,document_digest:observation.document_digest}});}}
      if(packages.some(row=>row.workspace_id!==workspaceId||row.work_item_id!==id)||dods.some(row=>row.workspace_id!==workspaceId))throw error();
      model={packages,operations,dods,reviews:reviews.map(checkReview),proofs};ready=true;options(history,reviews.map(row=>[row.review_id,`${row.context.native_result.candidate_commit.slice(0,12)} · ${row.context.reviewer_actor} · ${row.review_id}`]));
      if(selected&&!reviews.some(row=>row.review_id===selected))selected='';history.value=selected;buildCreate();renderReview();await verifier.setSources(operations.filter(op=>packages.some(pkg=>children(pkg).some(child=>child.operation_id===op.operation_id))));if(!valid(ticket))return;status.replaceChildren(...(filtered?[text('pmq_filtered')]:[]));
      if(previous&&selectable(current())&&same(previous,reference(current())))emit(previous);update();
    }catch(err){if(valid(ticket))failure(err);}
  }
  function setWork(id){if(id===workId)return;withdraw();
    workId=/^wi_[0-9a-f]{20}$/.test(id||'')?id:null;selected='';try{const saved=JSON.parse(localStorage.getItem(navigation));if(saved.workId===workId)selected=saved.review_id||'';}catch{}
    void refresh();}
  workChoice.addEventListener('change',()=>setWork(workChoice.value));
  verifier=workspaceExecutionVerification({h,t,api:request,alive,workspaceId,workspace,caps,form,labelled,errorBox,namespace,onSaved:()=>refresh(),onUnavailable:failure,onConfiguration:value=>{if(model){model.configuration=value?.available&&digest.test(value.command_digest)?value:null;matrixKey='';create?.sync();}}});
  element.insertBefore(verifier.element,draft.parentElement);
  return {element,setWork,refresh,withdraw,selection:()=>ready&&chosen?clone(chosen):null};
}
