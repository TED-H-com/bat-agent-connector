// One reviewed central intent; proposal text never becomes authority by itself.
import {discussionFeedback} from './pm-discussions.js';
import {iconButton, statusPill} from './ui/components.js';
import {reasonLabel, reasonLabels} from './ui/ui-strings.js';
import {icon} from './ui/icons.js';
import {structuredAnalysis} from './pm-structured-analysis.js';
// Proposal freshness has its own safe fallback; generic evidence stays Not available.
export const proposalReasonLabel = (t, reason) => Object.hasOwn(reasonLabels, reason) ? reasonLabel(t, reason) : t('pd_stale');
const PROPOSE='pm.conversation.propose',ADOPT='pm.conversation.adopt';
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b),copy=value=>structuredClone(value);
const statuses=['user_confirmed','agent_assumption','unresolved','conflicting','not_applicable'];
const defaults={proposal:'user_confirmed',assumption:'agent_assumption',unknown:'unresolved',conflict:'conflicting',excluded:'not_applicable'};
const record=value=>value&&typeof value==='object'&&!Array.isArray(value);
const key=(value,max)=>typeof value==='string'&&value.length<=max&&/^[a-z][a-z0-9_.-]*$/.test(value);
const text=(value,max)=>typeof value==='string'&&!!value.trim()&&[...value].length<=max&&!value.includes('\0');
const refs=(values,allowed,min=1)=>Array.isArray(values)&&values.length>=min&&values.length<=20&&new Set(values).size===values.length&&values.every(value=>allowed.has(value));
function cyclic(requirements){
  const graph=new Map(requirements.map(row=>[row.id,row.depends_on])),active=new Set(),done=new Set();
  const visit=id=>{if(active.has(id))return true;if(done.has(id))return false;active.add(id);if((graph.get(id)||[]).some(visit))return true;active.delete(id);done.add(id);return false;};
  return [...graph.keys()].some(visit);
}
function validRequirements(values,included){
  if(!Array.isArray(values)||values.length>20||values.some(row=>!record(row)||!key(row.id,80)))return false;
  const ids=new Set(values.map(row=>row.id));return ids.size===values.length&&values.every(row=>included.has(row.source_key)
    &&refs(row.acceptance_keys,included)&&['must','should','could','unresolved'].includes(row.priority)&&refs(row.depends_on,ids,0))&&!cyclic(values);
}
export function planProposals({h,t,api,alive,form,input,labelled,caps,workspaceId,threadId,thread,permitted,refresh:refreshParent,errorBox,conversation=false,backLink=()=>null,onFollowUp=()=>{},onAnswer=()=>{},onRevision=()=>{}}) {
  const base=`/pm/workspaces/${encodeURIComponent(workspaceId)}`,endpoint=base+'/conversation-proposals',target={workspace_id:workspaceId,thread_id:threadId};
  let head=null,capability=null,rows=[],cursor=null,active=null,ticket=0,loading=false,ready=false,prepare,adopt,preferredId=null;
  let candidateFields=[],requirementFields=[],rendered='',visible=false,conflict=false,composerNode=null,ask=null,connection=null,working=null,clock=null,adoptedId=null,reviewShell=false,adapterInitialized=false;
  const enabled=()=>caps()?.features?.pm_workspaces?.conversation_proposals?.version===1;
  const writing=()=>visible&&enabled()&&permitted(false)&&thread()?.capabilities?.write===true&&caps()?.scopes?.includes('manage');
  const deciding=()=>writing()&&permitted(true)&&thread()?.capabilities?.decide===true&&caps()?.scopes?.includes('start')
    &&caps()?.actions?.some(row=>row.action===ADOPT&&row.allowed===true)&&capability?.plan_adoption_available!==false;
  const fresh=()=>!conflict&&active?.adoptable===true;
  const adopted=()=>active?.adoptable_reason==='adopted';
  const original=()=>{try{return JSON.parse(adopt?.values().review||'null');}catch{return null;}};
  const note=key=>h('p',{class:'muted'},t(key));
  const acceptedDocument=p=>p.context.accepted_plan?.document||p.context.accepted_plan;
  const removed=(p,requirements)=>(acceptedDocument(p)?.requirements||[]).filter(row=>!requirements.some(item=>item.id===row.id)).map(row=>row.id);
  const fail=()=>Error(t('pcp_mismatch'));
  const select=(label,options,attrs={})=>h('select',{'aria-label':t(label),...attrs},...options.map(([value,text])=>h('option',{value},text)));
  const hidden=()=>h('input',{type:'hidden',value:''});
  const picker=select('pcp_saved',[['',t('pcp_choose')]]),notice=h('div',{role:'status'}),detail=h('div'),content=h('div');
  const purpose=input('pcp_purpose',{multiline:true,max:4000}),model=input('pcp_model',{max:128,required:false});
  const runtime=structuredAnalysis({h,t,labelled,action:PROPOSE,capability:()=>capability,readable:()=>visible,
    authorized:()=>writing()&&caps()?.scopes?.includes('start')&&(!adopt?.locked()||adopt?.replaceable?.()),
    legacyEligible:()=>capability?.native_analysis?.authorized!==false&&capability?.native_analysis?.eligible_for_preflight===true});
  prepare=form({action:PROPOSE,target,fields:{purpose,model,adapter:runtime.field},params:values=>({purpose:values.purpose,...(values.model.trim()?{model:values.model.trim()}:{}),...runtime.params(values)}),
    preconditions:()=>({expected_version:thread().thread.version,expected_plan_version:head.version,expected_accepted_revision_id:head.accepted_revision_id}),
    permitted:()=>writing()&&head&&caps()?.scopes?.includes('start')&&runtime.eligible()&&(!adopt?.locked()||adopt?.replaceable?.()),
    recoverPermitted:()=>writing()&&caps()?.scopes?.includes('start'),label:'pcp_prepare',
    onSaved:async result=>{const id=result?.outcome?.proposal_id||result?.proposal_id;preferredId=id;if(conversation&&refreshParent)await refreshParent();else{await refreshParent?.();await refresh(id);}},
    onStateChange:()=>{if(ready){adopt?.update();updateControls();}},
    extra:h('div',{class:'pm-fields'},labelled('pcp_purpose',purpose),runtime.box,h('details',{},h('summary',{},t('pcp_model')),labelled('pcp_model',model)),note('pcp_prepare_help'))});
  const review=hidden(),rationale=input('pcp_rationale',{multiline:true,max:4000});
  adopt=form({action:ADOPT,target,fields:{review,rationale},params:values=>{
      const saved=JSON.parse(values.review);if(!active||saved.proposal_id!==active.proposal_id||!validReview(saved.review))throw Error(t('pcp_missing'));
      return {proposal_id:saved.proposal_id,review:saved.review,rationale:values.rationale};},
    preconditions:()=>{const saved=original();if(!saved?.preconditions)throw fail();return saved.preconditions;},
    permitted:()=>active?.proposal_id!==adoptedId&&deciding()&&fresh()&&active?.validation.status==='format_valid'&&validReview(original()?.review)&&!prepare.locked(),
    recoverPermitted:()=>writing()&&permitted(true)&&caps()?.scopes?.includes('start'),label:'pcp_adopt',onError:error=>{if(!conversation||error.code!=='VERSION_CONFLICT')return false;conflict=true;updateControls();return true;},onSaved:async()=>{adoptedId=active?.proposal_id;if(conversation&&refreshParent)await refreshParent();else{await refreshParent?.();await refresh();}if(conversation){detail.replaceChildren(h('p',{'data-proposal-adopted':'',role:'status'},t('pd_adopted'),' · ',backLink()));}},
    onStateChange:()=>{if(ready)updateControls();},extra:h('div',{},review,content,labelled('pcp_rationale',rationale),note('pcp_review_help'))});
  const more=h('button',{type:'button',class:'secondary',hidden:true,onclick:()=>void loadMore()},t('pcp_more'));
  const reload=conversation?iconButton({icon:'refresh-cw',label:t('pcp_refresh'),onClick:()=>void refresh()}):h('button',{type:'button',class:'secondary',onclick:()=>void refresh()},t('pcp_refresh'));
  const prepareBox=h('details',{open:true,'data-proposal-prepare':''},h('summary',{},t('pcp_prepare')),prepare.box);
  const box=h('section',{class:'pm-plans','data-pm-plan-proposals':''},h('h3',{},t(conversation?'pd_proposed':'pcp_title')),conversation?'':note('pcp_help'),prepareBox,
    h('div',{class:'pm-fields'},labelled('pcp_saved',picker),h('div',{class:'actions'},reload,more)),notice,detail);
  const tools=h('div', {'data-discussion-proposal-tools':''});
  if(conversation){
    prepareBox.open=false;
    picker.closest('.pm-fields').remove();
    const history=h('details',{'data-proposal-history':''},h('summary',{},t('pd_history')),picker.parentElement);
    tools.append(h('header',{class:'pd-section-header'},h('h3',{},t('pcp_title')),reload),prepareBox,history,more);
  }
  picker.addEventListener('change',()=>{if(adopt.locked()){picker.value=active?.proposal_id||'';return;}void choose(picker.value);});
  function checked(value){
    if(value?.workspace_id!==workspaceId||!record(value.proposal))throw fail();const p=value.proposal,c=p.context;
    if(p.thread_id!==threadId||p.workspace_id!==undefined&&p.workspace_id!==workspaceId||typeof p.proposal_id!=='string'||!/^op_[0-9a-f]{32}$/.test(p.operation_id)
      ||!record(c)||!Number.isInteger(c.thread_version)||!record(c.plan_head)
      ||!Number.isInteger(c.plan_head.version)||!Object.hasOwn(c.plan_head,'accepted_revision_id')||!record(c.source_references)
      ||typeof p.raw_reply!=='string'||!['format_valid','needs_review'].includes(p.validation?.status))throw fail();
    for(const ref of Object.values(c.source_references))if(!record(ref)||typeof ref.quote!=='string'||!ref.quote||typeof ref.id!=='string'||!/^[0-9a-f]{64}$/.test(ref.digest))throw fail();
    if(p.validation.status==='needs_review'){if(p.document!==null||p.document_digest!==null)throw fail();}
    else {
      const d=p.document;if(!/^[0-9a-f]{64}$/.test(p.document_digest)||d?.version!==1||!text(d.title,160)||!text(d.summary,8000)||!Array.isArray(d.candidates)||!d.candidates.length||d.candidates.length>20
        ||!Array.isArray(d.requirements)||d.requirements.length>20||!Array.isArray(d.questions)||d.questions.length>3)throw fail();
      const keys=new Set();for(const row of d.candidates){if(!record(row)||!key(row.key,60)||keys.has(row.key)||!text(row.text,4000)
        ||!Object.hasOwn(defaults,row.kind)||!Array.isArray(row.source_refs)||!row.source_refs.length||row.source_refs.some(ref=>!Object.hasOwn(c.source_references,ref)))throw fail();keys.add(row.key);}
      if(!validRequirements(d.requirements,new Set(d.candidates.filter(row=>row.kind!=='excluded').map(row=>row.key))))throw fail();
      if(!Array.isArray(d.removed_requirement_ids)||!same([...d.removed_requirement_ids].sort(),removed(p,d.requirements).sort()))throw fail();
      for(const question of d.questions)if(typeof question.question!=='string'||typeof question.why_now!=='string'||!Array.isArray(question.source_refs)||question.source_refs.some(ref=>!Object.hasOwn(c.source_references,ref)))throw fail();
    }return p;
  }
  function defaultsFor(p){return {title:p.document.title,rows:p.document.candidates.map(row=>({key:row.key,text:row.text,status:defaults[row.kind],include:row.kind!=='excluded'})),requirements:copy(p.document.requirements),removed_requirement_ids:copy(p.document.removed_requirement_ids)};}
  function reviewIssue(value){
    if(!active||!record(value)||!text(value.title,160)||!Array.isArray(value.rows)||value.rows.some(row=>!record(row))||!Array.isArray(value.requirements)||value.requirements.length>20||value.requirements.some(row=>!record(row)))return 'pcp_missing';
    const keys=active.document?.candidates?.map(row=>row.key)||[];
    if(value.rows.length!==keys.length||new Set(value.rows.map(row=>row.key)).size!==keys.length||value.rows.some(row=>!keys.includes(row.key)
      ||!key(row.key,60)||!text(row.text,4000)||!statuses.includes(row.status)||typeof row.include!=='boolean'))return 'pcp_missing';
    const included=new Set(value.rows.filter(row=>row.include).map(row=>row.key));if(!included.size)return 'pcp_empty_selection';
    if(value.requirements.some(row=>Array.isArray(row.acceptance_keys)&&!row.acceptance_keys.length))return 'pcp_acceptance_missing';
    if(value.requirements.every(row=>record(row)&&Array.isArray(row.depends_on))&&cyclic(value.requirements))return 'pcp_cycle';
    if(!validRequirements(value.requirements,included)||!Array.isArray(value.removed_requirement_ids)
      ||!same([...value.removed_requirement_ids].sort(),removed(active,value.requirements).sort()))return 'pcp_missing';
    return null;
  }
  function validReview(value){return reviewIssue(value)===null;}
  function sourceQuotes(refs){return h('details',{class:'pm-source'},h('summary',{},t('pcp_sources')),...refs.map(key=>{
    const row=active.context.source_references[key];return h('div',{},h('blockquote',{class:'pm-note-text'},row.quote),h('small',{},row.kind,' · ',row.id));}));}
  function updateControls(){
    if(!ready)return;const locked=adopt.locked()||!deciding();picker.disabled=loading||adopt.locked();reload.disabled=loading;more.disabled=loading;
    runtime.update(prepare);
    if(composerNode){
      ask.disabled=!prepare.canSubmit();
      ask.textContent=t(prepare.locked()?'pm_check_original':'pd_ask');
      connection.hidden=runtime.eligible();ask.hidden=!runtime.eligible()&&!prepare.locked();
      discussionFeedback({t,request:prepare,node:working,eligible:runtime.eligible()});
    }
    for(const element of content.querySelectorAll('input,textarea,select,button'))element.disabled=locked;
    const reviewButton=adopt.box.querySelector('[data-proposal-review]');if(reviewButton)reviewButton.disabled=!fresh()&&!adopt.locked();
    if(active&&active.validation.status==='format_valid'){
      const rawReason=content.querySelector('[data-proposal-adoptable-reason]');if(rawReason)rawReason.textContent=active.adoptable_reason||'';
      const reasons=content.querySelector('[data-proposal-reason]');if(reasons){const reason=capability?.plan_adoption_available===false?'pm_plan_worktree_required':!deciding()?'pcp_readonly':!fresh()?(conversation?'pd_stale':'pcp_stale'):reviewIssue(original()?.review);reasons.textContent=reason?t(reason):'';if(reason==='pd_stale'&&!conflict){const label=proposalReasonLabel(t,active.adoptable_reason);if(label!==t('pd_stale'))reasons.textContent+=' · '+label;}reasons.hidden=!reason;const retry=content.querySelector('[data-proposal-again]');if(retry){retry.hidden=fresh()||adopted();retry.disabled=!prepare.canSubmit();}}
    }
  }
  function saveEditor(title){
    if(adopt.locked()||!active)return;
    const value={title:title.value,rows:candidateFields.map(row=>({key:row.key,text:row.text.value,status:row.status.value,include:row.include.checked})),
      requirements:requirementFields.map(row=>({id:row.id.value,source_key:row.source.value,acceptance_keys:[...row.acceptance.selectedOptions].map(option=>option.value),priority:row.priority.value,depends_on:[...row.dependencies.selectedOptions].map(option=>option.value)}))};
    value.removed_requirement_ids=removed(active,value.requirements);
    const saved=original();adopt.set('review',JSON.stringify({...saved,review:value}));showRemoved(value.removed_requirement_ids);adopt.update();
  }
  function showRemoved(ids){const box=content.querySelector('[data-proposal-removed]');if(!box)return;box.hidden=!ids.length;
    box.replaceChildren(h('h4',{},t('pcp_removed')),note('pcp_removed_help'),h('ul',{},...ids.map(id=>h('li',{},h('strong',{},id),' · ',acceptedDocument(active).requirements.find(row=>row.id===id).text))));}
  function renderReview(){
    const p=active;if(!p)return;const sig=p.proposal_id+':'+p.document_digest+':'+(p.adoptable_reason==='adopted'?p.adopted_revision_id:'');
    if(rendered===sig){adopt.update();return;}rendered=sig;candidateFields=[];requirementFields=[];detail.replaceChildren();content.replaceChildren();
    detail.append(h('h4',{},p.document?.title||t('pcp_review')));
    if(conversation&&adoptedId===p.proposal_id){detail.replaceChildren(h('p',{'data-proposal-adopted':'',role:'status'},t('pd_adopted'),' · ',backLink()));return;}
    if(conversation&&adopted()){
      const confirmation=h('div',{'data-proposal-adopted':''},statusPill('adopted'));
      if(typeof p.adopted_revision_id==='string'&&/^pmrev_[0-9a-f]{32}$/.test(p.adopted_revision_id))confirmation.append(' · ',h('a',{'data-proposal-adopted-revision':'',href:`#/pm/${encodeURIComponent(workspaceId)}/discussions/${encodeURIComponent(threadId)}?revision=${encodeURIComponent(p.adopted_revision_id)}`,onclick:event=>{event.preventDefault();onRevision(p.adopted_revision_id);}},t('pd_accepted_revision')));
      detail.append(h('p',{},p.document?.summary||''),confirmation);
      if(adopt.locked()){
        // Central adoption does not erase an unresolved original operation.
        // Keep only its existing recovery controls in Details, not a new review.
        adopt.box.firstElementChild.hidden=true;
        adopt.box.append(adopt.box.querySelector('button[type=submit]').closest('.actions'));
        detail.append(h('details',{},h('summary',{},t('pd_details')),adopt.box));
      }
      return;
    }
    if(!conversation&&p.provenance?.adapter)detail.append(h('p',{class:'muted'},t('pm_native_origin'),' ',p.provenance.adapter));
    if(p.validation.status!=='format_valid'){detail.append(note('pcp_invalid'),h('ul',{},...(p.validation.errors||[]).map(error=>h('li',{},String(error)))),h('details',{},h('summary',{},t('pcp_raw')),h('pre',{class:'pm-note-text'},p.raw_reply)));return;}
    let saved=original();if(saved?.proposal_id!==p.proposal_id){
      if(adopt.locked())throw fail();const drafts={...(record(saved?.drafts)?saved.drafts:{})};
      if(saved?.proposal_id)drafts[saved.proposal_id]={review:saved.review,preconditions:saved.preconditions,rationale:adopt.values().rationale,answers:saved.answers};
      const retained=drafts[p.proposal_id];saved={proposal_id:p.proposal_id,review:retained?.review||defaultsFor(p),answers:retained?.answers||{},drafts,preconditions:retained?.preconditions||{expected_version:p.context.thread_version,
        expected_plan_version:p.context.plan_head.version,expected_accepted_revision_id:p.context.plan_head.accepted_revision_id,expected_proposal_digest:p.document_digest}};
      adopt.set('review',JSON.stringify(saved));adopt.set('rationale',retained?.rationale||'');
    }
    if(!same(saved.preconditions,{expected_version:p.context.thread_version,expected_plan_version:p.context.plan_head.version,expected_accepted_revision_id:p.context.plan_head.accepted_revision_id,expected_proposal_digest:p.document_digest}))throw fail();
    if(!prepare.locked())prepareBox.open=false;
    const edited=saved.review,title=input('pcp_name',{max:160,value:edited.title});
    const accepted=acceptedDocument(p);
    if(conversation)detail.append(h('p',{class:'pm-note-text'},p.document.summary));
    const before=h('details',{},h('summary',{},t('pcp_before')),
      accepted?h('div',{},h('strong',{},accepted.title||''),h('p',{class:'pm-note-text'},accepted.markdown||''),h('ul',{},...(accepted.requirements||[]).map(row=>h('li',{},row.id,' · ',row.text))),h('small',{class:'pm-source'},t('pcp_baseline'),' · ',p.context.plan_head.accepted_revision_id)):note('pcp_first'));
    const technical=h('details',{'data-proposal-details':''},h('summary',{},t('pd_details')),labelled('pcp_name',title),h('code',{},p.proposal_id),h('code',{'data-proposal-adoptable-reason':''},p.adoptable_reason||''),before);
    if(!conversation)content.append(h('p',{class:'pm-note-text'},p.document.summary),before,labelled('pcp_name',title));
    content.append(h('h4',{},t('pcp_candidates')));
    title.addEventListener('input',()=>saveEditor(title));
    const requirementBox=h('div'),add=h('button',{type:'button',class:'secondary',onclick:()=>{
      if(adopt.locked()||requirementFields.length>=20)return;let index=1;while(requirementFields.some(row=>row.id.value==='requirement_'+index))index++;
      addRequirement({id:'requirement_'+index,source_key:'',acceptance_keys:[],priority:'unresolved',depends_on:[]});saveEditor(title);}},t('pcp_add_requirement'));
    const choices=(element,options)=>{const selected=[...element.selectedOptions].map(row=>row.value);element.replaceChildren(...options.map(([value,label])=>h('option',{value},label)));
      for(const option of element.options)option.selected=selected.includes(option.value);};
    function rebuildChoices(){const sources=[['',t('pcp_none')],...candidateFields.filter(row=>row.include.checked).map(row=>[row.key,row.text.value||row.key])];
      for(const row of requirementFields){const keep=(element,options)=>{const missing=[...element.selectedOptions].filter(option=>!options.some(([value])=>value===option.value)).map(option=>[option.value,option.textContent]);choices(element,[...options,...missing]);};
        keep(row.source,sources);keep(row.acceptance,sources.slice(1));keep(row.dependencies,requirementFields.filter(value=>value!==row).map(value=>[value.id.value,value.id.value]));}
    }
    const candidateGroups=new Map();
    for(const candidate of p.document.candidates){const row=edited.rows.find(value=>value.key===candidate.key);if(!row)throw fail();
      const include=h('input',{type:'checkbox','aria-label':t('pcp_include')}),text=input('pcp_text',{multiline:true,max:4000,value:row.text}),status=select('pcp_status',statuses.map(value=>[value,t('pm_status_'+value)]));include.checked=row.include;status.value=row.status;
      candidateFields.push({key:row.key,include,text,status});
      const wording=h('span',{},row.text),summary=h('summary',{},h('span',{class:'chip'},t('pcp_'+candidate.kind)),' ',wording);
      const item=h('details',{'data-proposal-candidate':row.key},summary,h('div',{class:'pm-fields'},h('label',{},include,t('pcp_include')),labelled('pcp_status',status),labelled('pcp_text',text)),sourceQuotes(candidate.source_refs));
      for(const field of [include,text,status])field.addEventListener('input',()=>{wording.textContent=text.value;rebuildChoices();saveEditor(title);});
      if(conversation){
        let group=candidateGroups.get(candidate.kind);if(!group){group=h('section',{class:'pd-candidate-group'},h('h4',{},t('pcp_'+candidate.kind)));candidateGroups.set(candidate.kind,group);content.append(group);}
        item.querySelector('label').remove();summary.replaceChildren(icon('chevron-right'),wording);
        group.append(h('div',{class:'pd-candidate'},h('label',{'aria-label':t('pcp_include')},include),item));
      }else content.append(item);
    }
    function addRequirement(value){const id=input('pcp_requirement_id',{max:80,value:value.id}),source=select('pcp_requirement_source',[[value.source_key,value.source_key]]),acceptance=select('pcp_acceptance',value.acceptance_keys.map(key=>[key,key]),{multiple:true,size:3}),
        priority=select('pcp_priority',['must','should','could','unresolved'].map(value=>[value,t('pcp_'+value)])),dependencies=select('pcp_dependencies',value.depends_on.map(key=>[key,key]),{multiple:true,size:2});
      priority.value=value.priority;for(const option of [...acceptance.options,...dependencies.options])option.selected=true;
      const row={id,source,acceptance,priority,dependencies};requirementFields.push(row);
      const remove=h('button',{type:'button',class:'secondary',onclick:()=>{if(adopt.locked())return;requirementFields=requirementFields.filter(item=>item!==row);item.remove();rebuildChoices();saveEditor(title);}},t('pcp_remove_requirement'));
      const item=h('details',{'data-proposal-requirement':''},h('summary',{},value.id||t('pcp_requirements'),' · ',candidateFields.find(row=>row.key===value.source_key)?.text.value||t('pcp_none')),h('div',{class:'pm-fields'},labelled('pcp_requirement_id',id),labelled('pcp_requirement_source',source),labelled('pcp_priority',priority),labelled('pcp_acceptance',acceptance),labelled('pcp_dependencies',dependencies)),remove);
      for(const field of [id,source,acceptance,priority,dependencies])field.addEventListener('input',()=>{if(field===id)rebuildChoices();saveEditor(title);});requirementBox.append(item);rebuildChoices();
    }
    for(const row of edited.requirements)addRequirement(row);
    content.append(conversation?h('details',{},h('summary',{},t('pcp_requirements')),requirementBox,add):h('div',{},h('h4',{},t('pcp_requirements')),requirementBox,add),h('div',{'data-proposal-removed':''}));showRemoved(edited.removed_requirement_ids);
    if(!conversation&&p.document.questions.length)content.append(h('details',{},h('summary',{},t('pcp_questions')),...p.document.questions.map(question=>h('div',{},h('p',{},question.question),h('p',{},t('pcp_why'),': ',question.why_now),sourceQuotes(question.source_refs)))));
    if(conversation&&p.document.questions.length){
      const answers=hidden();answers.value=JSON.stringify(saved.answers||{});
      const questions=h('section',{class:'pd-questions'},h('h4',{},t('pcp_questions')));
      p.document.questions.slice(0,3).forEach((question,index)=>{const answer=input('pd_answer',{required:false,max:2000,value:saved.answers?.[index]||''});
        answer.addEventListener('input',()=>{const draft=original();adopt.set('review',JSON.stringify({...draft,answers:{...draft.answers,[index]:answer.value}}));});
        questions.append(h('label',{},question.question,answer));});
      questions.append(h('button',{type:'button',class:'secondary',onclick:()=>{const values=original()?.answers||{};onAnswer(p.document.questions.map((row,index)=>values[index]?row.question+'\n'+values[index]:null).filter(Boolean).join('\n\n'));}},t('pd_answer_send')));content.append(questions);
    }
    content.prepend(h('button',{type:'button',class:'secondary','data-proposal-again':'',hidden:true,onclick:()=>{ask?.click();}},t('pd_again')));
    content.prepend(h('p',{class:'muted','data-proposal-reason':''}));
    const raw=h('details',{},h('summary',{},t('pcp_raw')),h('pre',{class:'pm-note-text'},p.raw_reply));
    if(conversation){technical.append(raw);content.append(technical);}else content.append(raw);
    adopt.box.firstElementChild.hidden=false;
    if(reviewShell) adopt.box.querySelector('[data-proposal-owner-review]').append(adopt.box.querySelector('button[type=submit]').closest('.actions'));
    detail.append(adopt.box);
    if(conversation&&!reviewShell){
      reviewShell=true;
      const reviewBody=adopt.box.firstElementChild;
      const extra=[...reviewBody.children].filter(node=>node!==review&&node!==content);
      const reviewMore=h('details',{'data-proposal-owner-review':'',open:adopt.locked()},h('summary',{},t('pd_review')),...extra);
      const actions=adopt.box.querySelector(':scope > .actions');reviewMore.append(actions);reviewBody.append(reviewMore);
      const reviewButton=h('button',{type:'button',class:'primary','data-proposal-review':'',onclick:()=>{reviewMore.open=true;rationale.focus();}},t('pd_review'));
      const choices=h('div',{class:'actions pd-review-actions'},reviewButton,h('button',{type:'button',class:'secondary',onclick:onFollowUp},t('pd_follow_up')));
      reviewBody.insertBefore(choices,content);
    }
    adopt.update();
  }
  async function choose(id){const own=++ticket;active=null;rendered='';detail.replaceChildren();content.replaceChildren();candidateFields=[];requirementFields=[];adopt.update();if(!id)return;
    try{const result=checked(await api('GET',endpoint+'/'+encodeURIComponent(id)));alive();if(own!==ticket)return;if(result.proposal_id!==id)throw fail();if(active?.proposal_id!==result.proposal_id)conflict=false;active=result;renderReview();}
    catch(error){if(own===ticket){withdraw();notice.replaceChildren(errorBox(error));}}
  }
  function page(value,append=false){if(value?.workspace_id!==workspaceId||!Array.isArray(value.proposals)||value.proposals.some(row=>row.thread_id!==threadId||typeof row.proposal_id!=='string'))throw fail();
    const next=value.next_cursor;if(next!==null&&next!==undefined&&(!Number.isInteger(next)||next<1||append&&next<=cursor))throw fail();
    rows=append?[...rows,...value.proposals]:value.proposals;cursor=next??null;const selected=picker.value;picker.replaceChildren(h('option',{value:''},t('pcp_choose')),...rows.map(row=>h('option',{value:row.proposal_id},[row.title??t('pd_proposed'),row.adoptable_reason==='adopted'?t('ui_status_adopted'):row.validation?.status!=='format_valid'?proposalReasonLabel(t,'invalid_proposal'):t(row.adoptable===false?'pd_stale':'pcp_proposal')].join(' · '))));picker.value=selected;more.hidden=cursor===null;
    notice.replaceChildren(...(!rows.length?[note('pcp_empty')]:[]),...(cursor!==null?[note('pcp_partial')]:[]));
  }
  async function refresh(preferred){if(!enabled()){withdraw();box.hidden=true;return;}if(loading)return;loading=true;const own=++ticket;updateControls();
    try{const [list,plans]=await Promise.all([api('GET',`${endpoint}?thread_id=${encodeURIComponent(threadId)}&after=0&limit=20`),api('GET',base+'/plans?limit=1')]);alive();if(own!==ticket)return;
      if(plans.workspace_id!==workspaceId||!record(plans.head))throw fail();head=plans.head;
      visible=true;box.hidden=false;page(list);
      const id=preferred||preferredId||original()?.proposal_id||active?.proposal_id||picker.value||(conversation?rows.at(-1)?.proposal_id:null);if(id&&!rows.some(row=>row.proposal_id===id))picker.append(h('option',{value:id},t('pd_proposed')));picker.value=id||'';preferredId=null;
      // Re-read original sources even if the document digest is unchanged.
      if(id){const result=checked(await api('GET',endpoint+'/'+encodeURIComponent(id)));alive();if(own!==ticket)return;if(result.proposal_id!==id)throw fail();if(active?.proposal_id!==result.proposal_id)conflict=false;active=result;renderReview();}
    }catch(error){if(own===ticket){withdraw();notice.replaceChildren(errorBox(error));}}
    finally{if(own===ticket||!visible){loading=false;prepare.update();adopt.update();updateControls();}}
  }
  async function loadMore(){if(cursor===null||loading)return;loading=true;const own=ticket;updateControls();try{const result=await api('GET',`${endpoint}?thread_id=${encodeURIComponent(threadId)}&after=${cursor}&limit=20`);alive();if(own!==ticket)return;page(result,true);}catch(error){if(own===ticket){withdraw();notice.replaceChildren(errorBox(error));}}finally{loading=false;updateControls();}}
  function withdraw(){ticket++;visible=false;active=head=null;rows=[];cursor=null;rendered='';candidateFields=[];requirementFields=[];picker.replaceChildren(h('option',{value:''},t('pcp_choose')));detail.replaceChildren();content.replaceChildren();notice.replaceChildren();more.hidden=true;prepare?.update();adopt?.update();}
  function composer(textarea){
    ask=h('button',{type:'button',class:'secondary','data-discussion-ask':'',onclick:()=>{
      if(adopt.replaceable())adopt.edit();
      if(!prepare.locked())prepare.set('purpose',textarea.value.trim()||thread()?.messages?.at(-1)?.content||thread()?.thread?.title||t('pd_ask'));
      void prepare.submit();
    }},t('pd_ask'));
    connection=h('p',{class:'muted'},h('span',{},t('pd_not_connected')),' · ',h('a',{href:'#/settings'},t('pd_setup')));
    working=h('p',{'data-discussion-working':'',role:'status',class:'muted'});
    composerNode=h('div',{class:'pd-ask'},ask,connection,working);
    clock=setInterval(()=>{if(composerNode.isConnected&&prepare.checking()&&['running','accepted','queued','pending'].includes(prepare.operation()?.status))updateControls();},1000);
    updateControls();return composerNode;
  }
  ready=true;updateControls();
  return {box,tools,composer,refresh,withdraw,recheck:()=>prepare.recheck(),update(value={}){
    if(value.capabilities){
      capability=value.capabilities;
      if(conversation&&!adapterInitialized){
        adapterInitialized=true;
        // Only initialize an unsaved choice. Recovery and explicit drafts retain their adapter.
        if(!prepare.locked()&&!prepare.hasDraft?.()){
          const adapters=capability.structured_analysis?.actions?.[PROPOSE]?.adapters||[];
          const available=adapters.find(row=>['codex-native-linux','bat-pm-linux-v1'].includes(row.adapter)&&row.action_registered===true&&row.authorized===true&&row.eligible_for_preflight===true);
          if(available&&available.adapter!==runtime.field.value)prepare.set('adapter',available.adapter);
        }
      }
    }
    if(value.head)head=value.head;if(value.page){visible=true;page(value.page);}prepare.update();adopt.update();
  },dispose(){clearInterval(clock);withdraw();prepare.dispose();adopt.dispose();}};
}
