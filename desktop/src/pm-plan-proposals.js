// One reviewed central intent; proposal text never becomes authority by itself.
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
export function planProposals({h,t,api,alive,form,input,labelled,caps,workspaceId,threadId,thread,permitted,refresh:refreshParent,errorBox}) {
  const base=`/pm/workspaces/${encodeURIComponent(workspaceId)}`,endpoint=base+'/conversation-proposals',target={workspace_id:workspaceId,thread_id:threadId};
  let head=null,capability=null,rows=[],cursor=null,active=null,ticket=0,loading=false,ready=false,prepare,adopt;
  let candidateFields=[],requirementFields=[],rendered='',visible=false;
  const enabled=()=>caps()?.features?.pm_workspaces?.conversation_proposals?.version===1;
  const writing=()=>visible&&enabled()&&permitted(false)&&thread()?.capabilities?.write===true&&caps()?.scopes?.includes('manage');
  const deciding=()=>writing()&&permitted(true)&&thread()?.capabilities?.decide===true&&caps()?.scopes?.includes('start')
    &&caps()?.actions?.some(row=>row.action===ADOPT&&row.allowed===true)&&capability?.plan_adoption_available!==false;
  const fresh=()=>active&&head&&thread()?.thread?.version===active.context.thread_version
    &&head.version===active.context.plan_head.version&&head.accepted_revision_id===active.context.plan_head.accepted_revision_id;
  const original=()=>{try{return JSON.parse(adopt?.values().review||'null');}catch{return null;}};
  const note=key=>h('p',{class:'muted'},t(key));
  const acceptedDocument=p=>p.context.accepted_plan?.document||p.context.accepted_plan;
  const removed=(p,requirements)=>(acceptedDocument(p)?.requirements||[]).filter(row=>!requirements.some(item=>item.id===row.id)).map(row=>row.id);
  const fail=()=>Error(t('pcp_mismatch'));
  const select=(label,options,attrs={})=>h('select',{'aria-label':t(label),...attrs},...options.map(([value,text])=>h('option',{value},text)));
  const hidden=()=>h('input',{type:'hidden',value:''});
  const picker=select('pcp_saved',[['',t('pcp_choose')]]),notice=h('div',{role:'status'}),detail=h('div'),content=h('div');
  const purpose=input('pcp_purpose',{multiline:true,max:4000}),model=input('pcp_model',{max:128,required:false}),prepareReason=h('p',{class:'muted','data-proposal-availability':''});
  prepare=form({action:PROPOSE,target,fields:{purpose,model},params:values=>({purpose:values.purpose,...(values.model.trim()?{model:values.model.trim()}:{})}),
    preconditions:()=>({expected_version:thread().thread.version,expected_plan_version:head.version,expected_accepted_revision_id:head.accepted_revision_id}),
    permitted:()=>writing()&&head&&caps()?.scopes?.includes('start')&&capability?.native_analysis?.authorized!==false&&capability?.native_analysis?.eligible_for_preflight===true&&!adopt?.locked(),
    recoverPermitted:()=>writing()&&caps()?.scopes?.includes('start'),label:'pcp_prepare',
    onSaved:async result=>{const id=result?.outcome?.proposal_id||result?.proposal_id;if(id)picker.value=id;await refreshParent?.();await refresh(id);},
    onStateChange:()=>{if(ready)updateControls();},
    extra:h('div',{class:'pm-fields'},labelled('pcp_purpose',purpose),h('details',{},h('summary',{},t('pcp_model')),labelled('pcp_model',model)),note('pcp_prepare_help'),prepareReason)});
  const review=hidden(),rationale=input('pcp_rationale',{multiline:true,max:4000});
  adopt=form({action:ADOPT,target,fields:{review,rationale},params:values=>{
      const saved=JSON.parse(values.review);if(!active||saved.proposal_id!==active.proposal_id||!validReview(saved.review))throw Error(t('pcp_missing'));
      return {proposal_id:saved.proposal_id,review:saved.review,rationale:values.rationale};},
    preconditions:()=>{const saved=original();if(!saved?.preconditions)throw fail();return saved.preconditions;},
    permitted:()=>deciding()&&fresh()&&active?.validation.status==='format_valid'&&validReview(original()?.review)&&!prepare.locked(),
    recoverPermitted:()=>writing()&&permitted(true)&&caps()?.scopes?.includes('start'),label:'pcp_adopt',onSaved:async()=>{await refreshParent?.();await refresh();},
    onStateChange:()=>{if(ready)updateControls();},extra:h('div',{},review,content,labelled('pcp_rationale',rationale),note('pcp_review_help'))});
  const more=h('button',{type:'button',class:'secondary',hidden:true,onclick:()=>void loadMore()},t('pcp_more'));
  const reload=h('button',{type:'button',class:'secondary',onclick:()=>void refresh()},t('pcp_refresh'));
  const prepareBox=h('details',{open:true,'data-proposal-prepare':''},h('summary',{},t('pcp_prepare')),prepare.box);
  const box=h('section',{class:'pm-plans','data-pm-plan-proposals':''},h('h3',{},t('pcp_title')),note('pcp_help'),prepareBox,
    h('div',{class:'pm-fields'},labelled('pcp_saved',picker),h('div',{class:'actions'},reload,more)),notice,detail);
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
    const unavailable=capability?.native_analysis?.authorized===false||capability?.native_analysis?.eligible_for_preflight!==true||!caps()?.scopes?.includes('start');
    prepareReason.hidden=!unavailable;prepareReason.textContent=unavailable?t('pcp_runtime'):'';
    for(const element of content.querySelectorAll('input,textarea,select,button'))element.disabled=locked;
    if(active&&active.validation.status==='format_valid'){
      const reasons=content.querySelector('[data-proposal-reason]');if(reasons){const reason=capability?.plan_adoption_available===false?'pm_plan_worktree_required':!deciding()?'pcp_readonly':!fresh()?'pcp_stale':reviewIssue(original()?.review);reasons.textContent=reason?t(reason):'';reasons.hidden=!reason;}
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
    const p=active;if(!p)return;const sig=p.proposal_id+':'+p.document_digest;
    if(rendered===sig){adopt.update();return;}rendered=sig;candidateFields=[];requirementFields=[];detail.replaceChildren();content.replaceChildren();
    detail.append(h('h4',{},t('pcp_review')));
    if(p.validation.status!=='format_valid'){detail.append(note('pcp_invalid'),h('ul',{},...(p.validation.errors||[]).map(error=>h('li',{},String(error)))),h('details',{},h('summary',{},t('pcp_raw')),h('pre',{class:'pm-note-text'},p.raw_reply)));return;}
    let saved=original();if(saved?.proposal_id!==p.proposal_id){
      if(adopt.locked())throw fail();const drafts={...(record(saved?.drafts)?saved.drafts:{})};
      if(saved?.proposal_id)drafts[saved.proposal_id]={review:saved.review,preconditions:saved.preconditions,rationale:adopt.values().rationale};
      const retained=drafts[p.proposal_id];saved={proposal_id:p.proposal_id,review:retained?.review||defaultsFor(p),drafts,preconditions:retained?.preconditions||{expected_version:p.context.thread_version,
        expected_plan_version:p.context.plan_head.version,expected_accepted_revision_id:p.context.plan_head.accepted_revision_id,expected_proposal_digest:p.document_digest}};
      adopt.set('review',JSON.stringify(saved));adopt.set('rationale',retained?.rationale||'');
    }
    if(!same(saved.preconditions,{expected_version:p.context.thread_version,expected_plan_version:p.context.plan_head.version,expected_accepted_revision_id:p.context.plan_head.accepted_revision_id,expected_proposal_digest:p.document_digest}))throw fail();
    if(!prepare.locked())prepareBox.open=false;
    const edited=saved.review,title=input('pcp_name',{max:160,value:edited.title});
    const accepted=acceptedDocument(p);
    content.append(h('p',{class:'pm-note-text'},p.document.summary),h('details',{},h('summary',{},t('pcp_before')),
      accepted?h('div',{},h('strong',{},accepted.title||''),h('p',{class:'pm-note-text'},accepted.markdown||''),h('ul',{},...(accepted.requirements||[]).map(row=>h('li',{},row.id,' · ',row.text))),h('small',{class:'pm-source'},t('pcp_baseline'),' · ',p.context.plan_head.accepted_revision_id)):note('pcp_first')),
      labelled('pcp_name',title),h('h4',{},t('pcp_candidates')));
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
    for(const candidate of p.document.candidates){const row=edited.rows.find(value=>value.key===candidate.key);if(!row)throw fail();
      const include=h('input',{type:'checkbox','aria-label':t('pcp_include')}),text=input('pcp_text',{multiline:true,max:4000,value:row.text}),status=select('pcp_status',statuses.map(value=>[value,t('pm_status_'+value)]));include.checked=row.include;status.value=row.status;
      candidateFields.push({key:row.key,include,text,status});
      const wording=h('span',{},row.text),summary=h('summary',{},h('span',{class:'chip'},t('pcp_'+candidate.kind)),' ',wording);
      const item=h('details',{'data-proposal-candidate':row.key},summary,h('div',{class:'pm-fields'},h('label',{},include,t('pcp_include')),labelled('pcp_status',status),labelled('pcp_text',text)),sourceQuotes(candidate.source_refs));
      for(const field of [include,text,status])field.addEventListener('input',()=>{wording.textContent=text.value;rebuildChoices();saveEditor(title);});content.append(item);
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
    content.append(h('h4',{},t('pcp_requirements')),requirementBox,add,h('div',{'data-proposal-removed':''}));showRemoved(edited.removed_requirement_ids);
    if(p.document.questions.length)content.append(h('details',{},h('summary',{},t('pcp_questions')),...p.document.questions.map(question=>h('div',{},h('p',{},question.question),h('p',{},t('pcp_why'),': ',question.why_now),sourceQuotes(question.source_refs)))));
    content.append(h('p',{class:'muted','data-proposal-reason':''}),h('details',{},h('summary',{},t('pcp_raw')),h('pre',{class:'pm-note-text'},p.raw_reply)));
    detail.append(adopt.box);adopt.update();
  }
  async function choose(id){const own=++ticket;active=null;rendered='';detail.replaceChildren();content.replaceChildren();candidateFields=[];requirementFields=[];adopt.update();if(!id)return;
    try{const result=checked(await api('GET',endpoint+'/'+encodeURIComponent(id)));alive();if(own!==ticket)return;if(result.proposal_id!==id)throw fail();active=result;renderReview();}
    catch(error){if(own===ticket){withdraw();notice.replaceChildren(errorBox(error));}}
  }
  function page(value,append=false){if(value?.workspace_id!==workspaceId||!Array.isArray(value.proposals)||value.proposals.some(row=>row.thread_id!==threadId||typeof row.proposal_id!=='string'))throw fail();
    const next=value.next_cursor;if(next!==null&&next!==undefined&&(!Number.isInteger(next)||next<1||append&&next<=cursor))throw fail();
    rows=append?[...rows,...value.proposals]:value.proposals;cursor=next??null;const selected=picker.value;picker.replaceChildren(h('option',{value:''},t('pcp_choose')),...rows.map(row=>h('option',{value:row.proposal_id},row.title||row.proposal_id)));picker.value=selected;more.hidden=cursor===null;
    notice.replaceChildren(...(!rows.length?[note('pcp_empty')]:[]),...(cursor!==null?[note('pcp_partial')]:[]));
  }
  async function refresh(preferred){if(!enabled()){withdraw();box.hidden=true;return;}if(loading)return;loading=true;const own=++ticket;updateControls();
    try{const [list,plans]=await Promise.all([api('GET',`${endpoint}?thread_id=${encodeURIComponent(threadId)}&after=0&limit=20`),api('GET',base+'/plans?limit=1')]);alive();if(own!==ticket)return;
      if(plans.workspace_id!==workspaceId||!record(plans.head))throw fail();head=plans.head;visible=true;box.hidden=false;page(list);
      const id=preferred||original()?.proposal_id||active?.proposal_id||picker.value;if(id&&!rows.some(row=>row.proposal_id===id))picker.append(h('option',{value:id},id));picker.value=id||'';
      // Re-read original sources even if the document digest is unchanged.
      if(id){const result=checked(await api('GET',endpoint+'/'+encodeURIComponent(id)));alive();if(own!==ticket)return;if(result.proposal_id!==id)throw fail();active=result;renderReview();}
    }catch(error){if(own===ticket){withdraw();notice.replaceChildren(errorBox(error));}}
    finally{if(own===ticket||!visible){loading=false;prepare.update();adopt.update();updateControls();}}
  }
  async function loadMore(){if(cursor===null||loading)return;loading=true;const own=ticket;updateControls();try{const result=await api('GET',`${endpoint}?thread_id=${encodeURIComponent(threadId)}&after=${cursor}&limit=20`);alive();if(own!==ticket)return;page(result,true);}catch(error){if(own===ticket){withdraw();notice.replaceChildren(errorBox(error));}}finally{loading=false;updateControls();}}
  function withdraw(){ticket++;visible=false;active=head=null;rows=[];cursor=null;rendered='';candidateFields=[];requirementFields=[];picker.replaceChildren(h('option',{value:''},t('pcp_choose')));detail.replaceChildren();content.replaceChildren();notice.replaceChildren();more.hidden=true;prepare?.update();adopt?.update();}
  ready=true;updateControls();
  return {box,refresh,withdraw,update(value={}){if(value.capabilities)capability=value.capabilities;if(value.head)head=value.head;if(value.page){visible=true;page(value.page);}prepare.update();adopt.update();},dispose(){withdraw();prepare.dispose();adopt.dispose();}};
}
