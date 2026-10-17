import {h} from './ui/dom.js';
import {button, disclosure, statusPill} from './ui/components.js';

export const ADOPT_START = 'work.adopt_and_start';
const wi = value => typeof value === 'string' && /^wi_[0-9a-f]{20}$/.test(value);
const opId = value => typeof value === 'string' && /^op_[0-9a-f]{32}$/.test(value);
const record = value => value && typeof value === 'object' && !Array.isArray(value);
const same = (a,b) => JSON.stringify(a) === JSON.stringify(b);
const copy = value => structuredClone(value);
const workKeys = ['expected_work_item_fingerprint','expected_work_version','expected_project_version','expected_binding_id','expected_previous_package_id','expected_latest_attempt_id','repository_id','binding_digest','expected_source_sha'];
const planKeys = ['expected_version','expected_plan_version','expected_accepted_revision_id','expected_proposal_digest'];
const exact = (value, keys) => record(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value,key));
export const landingPrefix = (namespace,workspaceId) => `batc.pm.${namespace}.adopt-start-landing.${workspaceId}.`;
export const landingNavigationKey = (namespace,workspaceId,id) => `batc.pm.${namespace}.adopt-start-live.${workspaceId}.${id}`;

// A read result can only complete the exact reviewed selection. Its ready rows
// supply all execution values; the client never invents a scope or destination.
export function checkedAdoptPreview(value, body, target, baseline) {
  if (!record(value) || value.workspace_id !== target.workspace_id || value.thread_id !== target.thread_id
      || value.proposal_id !== body.proposal_id || typeof value.adoptable !== 'boolean'
      || !Array.isArray(value.works) || value.works.length !== body.works.length
      || !exact(value.preconditions, [...planKeys,'works']) || !Array.isArray(value.preconditions.works)
      || value.preconditions.works.length !== body.works.length
      || planKeys.some(key => value.preconditions[key] !== baseline[key])) throw Error('loop_preview_mismatch');
  const works = [], conditions = [];
  value.works.forEach((row,index) => {
    const selected = body.works[index], pre = value.preconditions.works[index];
    if (!record(row) || row.work_item_id !== selected.work_item_id || !same(row.requirement_ids,selected.requirement_ids)
        || typeof row.readiness?.ready !== 'boolean') throw Error('loop_preview_mismatch');
    if (!row.readiness.ready) return;
    if (!exact(pre,['work_item_id',...workKeys]) || pre.work_item_id !== row.work_item_id
        || !['claude','codex'].includes(row.agent) || !['pull_request','reviewable_changes'].includes(row.delivery)
        || typeof row.instruction !== 'string' || !row.instruction.trim() || row.instruction.length > 4000
        || selected.instruction !== undefined && row.instruction !== selected.instruction
        || row.model != null && (typeof row.model !== 'string' || !row.model.trim() || row.model.length > 128)
        || !exact(row.code_target,['repository','host','workspace_id','source_ref'])
        || Object.values(row.code_target).some(value => typeof value !== 'string' || !value.trim())
        || !/^[0-9a-f]{64}$/.test(pre.expected_work_item_fingerprint)
        || !Number.isSafeInteger(pre.expected_work_version) || pre.expected_work_version < 0
        || !Number.isSafeInteger(pre.expected_project_version) || pre.expected_project_version < 0
        || !Number.isSafeInteger(pre.repository_id) || pre.repository_id <= 0
        || !/^[0-9a-f]{64}$/.test(pre.binding_digest) || !/^[0-9a-f]{40}$/.test(pre.expected_source_sha)) throw Error('loop_preview_mismatch');
    works.push({work_item_id:row.work_item_id,requirement_ids:copy(row.requirement_ids),instruction:row.instruction,
      agent:row.agent,delivery:row.delivery,code_target:copy(row.code_target),...(typeof row.model === 'string'?{model:row.model}:{})});
    conditions.push(copy(pre));
  });
  return {value:copy(value),works,preconditions:{...copy(value.preconditions),works:conditions}};
}

export function adoptStartReview({t,api,alive,form,caps,namespace,workspaceId,threadId,review,rationale,permitted,readable,reviewable=()=>true,otherLocked,errorBox,onStateChange=()=>{},onLanded}) {
  const target = {workspace_id:workspaceId,thread_id:threadId};
  const base = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  let request, disposed=false, visible=false, ticket=0, checking=false, loading=false, prepared=null, signature='', rendered='', landed=null, updating=false, followRequested=false;
  let rows=new Map(), projects=[], projectCursor=null, projectIndex=0, workCursor=null;
  const nodes=new Map(), selection=h('input',{type:'hidden',value:''});
  const catalog=h('div',{'data-adopt-work-list':''}), status=h('div',{role:'status'}), notice=h('p',{class:'muted'});
  const cap=()=>caps()?.features?.pm_workspaces?.work_adopt_and_start;
  const availability=()=>!permitted()?'loop_not_owner':cap()?.version!==1||cap()?.available!==true?'loop_adopt_start_unavailable'
    :!caps()?.actions?.some(row=>row.action===ADOPT_START&&row.allowed===true)?'loop_adopt_start_denied':null;
  const max=()=>Number.isSafeInteger(cap()?.max_selected_works)&&cap().max_selected_works>0&&cap().max_selected_works<=10?cap().max_selected_works:0;
  const capable=()=>!availability()&&max()>0&&caps()?.scopes?.includes('observe');
  const draft=()=>{try{const value=JSON.parse(request?.values().selection||'null');return record(value)&&Array.isArray(value.works)?value:{proposal_id:null,works:[],drafts:{}};}catch{return {proposal_id:null,works:[],drafts:{}};}};
  const selected=()=>draft().proposal_id===review()?.proposal_id?draft().works:[];
  const body=()=>({proposal_id:review()?.proposal_id,review:copy(review()?.review),rationale:rationale(),works:selected().map(row=>({work_item_id:row.work_item_id,requirement_ids:row.requirement_ids,...(row.instruction?.trim()?{instruction:row.instruction}:{})}))});
  const fingerprint=()=>JSON.stringify(body());
  const live=own=>{try{alive();return !disposed&&visible&&readable()&&own===ticket;}catch{return false;}};
  const invalidate=()=>{if(prepared)status.textContent=t('loop_stale_preview');ticket++;prepared=null;signature='';checking=false;for(const node of nodes.values())node.observed.replaceChildren();};
  const save=works=>{request.set('selection',JSON.stringify({...draft(),proposal_id:review().proposal_id,works}));invalidate();update();};
  const changed=()=>{const works=selected().map(row=>{const node=nodes.get(row.work_item_id);return node?{...row,instruction:node.instruction.value,requirement_ids:[...node.requirements.querySelectorAll('input:checked')].map(input=>input.value)}:row;});save(works);};
  const checkButton=button({label:t('loop_preview'),onClick:()=>void preview()});
  const more=button({label:t('loop_works_more'),hidden:true,onClick:()=>void loadMore()});
  const controls=h('div',{class:'actions'},checkButton,more);
  request=form({action:ADOPT_START,target,fields:{selection},requiredScope:'start',label:'loop_adopt_start',
    params:()=>{if(!prepared||signature!==fingerprint())throw Error(t('loop_stale_preview'));return {...body(),works:copy(prepared.works)};},
    preconditions:()=>copy(prepared.preconditions),
    permitted:()=>visible&&readable()&&reviewable()&&capable()&&!otherLocked()&&Boolean(prepared?.value.adoptable&&prepared.works.length&&signature===fingerprint()),
    recoverPermitted:()=>visible&&readable()&&capable()&&!otherLocked(),
    onSaved:()=>{},onBeforeSaved:()=>{},
    onStateChange:()=>{if(request&&!updating)update();},
    extra:h('div',{},selection,h('h4',{},t('loop_works_heading')),h('p',{class:'muted'},t('loop_adopt_start_help')),catalog,controls,status),
    errorBox:error=>errorBox(error,{summary:t('loop_preview_mismatch')})});
  const box=h('section',{class:'pm-adopt-start','data-adopt-start':''},notice,request.box);
  // Only this tab's explicit submission requests automatic navigation. Reading
  // a stored receipt in another view or tab must not redirect that reader.
  request.box.addEventListener('submit',()=>{followRequested=true;},{capture:true});
  function rememberReceipt() {
    const op=request.operation();if(!op||landed===op.operation_id||!opId(op.operation_id))return;
    // durableForm already verified actor, key, body and target. Save only IDs.
    const ids=op.params?.works?.map(row=>row.work_item_id);
    if(!Array.isArray(ids)||!ids.length||ids.some(id=>!wi(id)))return;
    landed=op.operation_id;
    try{alive();localStorage.setItem(landingPrefix(namespace,workspaceId)+op.operation_id,JSON.stringify({version:1,operation_id:op.operation_id,thread_id:threadId,work_item_ids:ids}));}
    catch{return;}
    if(followRequested){try{sessionStorage.setItem(landingNavigationKey(namespace,workspaceId,op.operation_id),ids[0]);}catch{/* the ordinary Work link remains available */}followRequested=false;}
    if(onLanded)onLanded(ids[0],op.operation_id);
    else location.hash=`#/pm/${encodeURIComponent(workspaceId)}/board?select=${encodeURIComponent(ids[0])}`;
  }
  function update() {
    if(disposed||updating)return;updating=true;
    try{
      if(prepared&&signature!==fingerprint())invalidate();
      const reason=availability();notice.textContent=reason?t(reason):'';notice.hidden=!reason;
      request.box.hidden=!capable()&&!request.locked();
      const frozen=request.locked()||otherLocked()||!capable()||!readable();
      for(const node of nodes.values()){
        node.choose.disabled=frozen;
        node.instruction.disabled=frozen;
        for(const input of node.requirements.querySelectorAll('input'))input.disabled=frozen||input.dataset.prerequisite==='true';
      }
      checkButton.disabled=frozen||!reviewable()||checking||loading||!selected().length||selected().some(row=>!row.requirement_ids.length);
      more.disabled=frozen||loading;more.hidden=!projectCursor&&projectIndex>=projects.length;
      request.update();
      const submit=request.box.querySelector('button[type=submit]');
      submit.hidden=submit.hidden||!selected().length&&!request.locked();
      const adopted=request.operation()?.result?.adopted===true;
      if(adopted){request.box.querySelector(':scope > .actions > button[type=button]').hidden=true;}
      rememberReceipt();
    }finally{updating=false;}
    onStateChange();
  }
  function render() {
    const current=review();if(!current)return;
    const required=current.review.requirements;
    if(!nodes.size)catalog.replaceChildren();
    for(const row of rows.values()){
      const chosen=selected().find(value=>value.work_item_id===row.work_item_id);
      let node=nodes.get(row.work_item_id);
      if(!node){
        const choose=h('input',{type:'checkbox','aria-label':row.title});
        const instruction=h('textarea',{'aria-label':t('loop_work_instruction'),maxlength:4000,rows:2});
        const requirements=h('div',{class:'pm-adopt-requirements'}),observed=h('div',{'data-adopt-readiness':''});
        const fields=h('div',{class:'pm-adopt-work-fields'},requirements,h('label',{},t('loop_work_instruction'),instruction),observed);
        const element=h('div',{class:'pm-adopt-work','data-adopt-work':row.work_item_id},h('label',{class:'pm-adopt-choice'},choose,h('span',{},row.title,h('small',{class:'muted'},row.project_name||''))),fields);
        node={element,choose,instruction,requirements,fields,observed,reqSignature:''};nodes.set(row.work_item_id,node);catalog.append(element);
        choose.addEventListener('change',()=>{
          if(choose.checked&&selected().length>=max()){choose.checked=false;status.textContent=t('loop_works_limit',{count:max()});return;}
          const works=selected().filter(value=>value.work_item_id!==row.work_item_id);
          if(choose.checked)works.push({work_item_id:row.work_item_id,requirement_ids:[],instruction:''});
          save(works);render();if(works.length===max())status.textContent=t('loop_works_limit',{count:max()});
        });instruction.addEventListener('input',changed);
      }
      node.choose.checked=Boolean(chosen);node.fields.hidden=!chosen;
      if(node.instruction!==document.activeElement)node.instruction.value=chosen?.instruction||'';
      const next=JSON.stringify([required,current.review.rows,chosen?.requirement_ids]);
      if(next!==node.reqSignature){node.reqSignature=next;node.requirements.replaceChildren(h('strong',{},t('loop_requirement_heading')),
        ...required.map(req=>{const input=h('input',{type:'checkbox',value:req.id,'data-prerequisite':String(req.depends_on.length>0)});input.checked=chosen?.requirement_ids.includes(req.id)||false;
          input.addEventListener('change',changed);const label=current.review.rows.find(row=>row.key===req.source_key)?.text||req.id;
          return h('div',{},h('label',{class:'pm-adopt-choice'},input,label),...(req.depends_on.length?[h('p',{class:'muted'},t('loop_requirement_prerequisite'))]:[]));}));}
    }
    if(!rows.size)catalog.replaceChildren(h('p',{class:'muted'},t('loop_works_empty')));
    update();
  }
  async function loadMore(reset=false) {
    if(loading||!visible||!capable()||request.locked())return;
    loading=true;const own=ticket;update();
    try{
      if(reset){projects=[];projectCursor=0;projectIndex=0;workCursor=null;rows.clear();nodes.clear();catalog.replaceChildren();}
      if(projectIndex>=projects.length&&projectCursor!==null){
        const result=await api('GET',`${base}/projects?after=${projectCursor}&limit=50`);
        if(!live(own))return;
        if(result.workspace_id!==workspaceId||!Array.isArray(result.bindings)||result.bindings.some(row=>row.workspace_id!==workspaceId||!/^prj_[0-9a-f]{20}$/.test(row.project_id)))throw Error(t('loop_preview_mismatch'));
        projects.push(...result.bindings.map(row=>row.project_id));projectCursor=result.next_cursor??null;
      }
      if(projectIndex<projects.length){
        const project=projects[projectIndex];
        const result=await api('GET',`/work-items?project_id=${encodeURIComponent(project)}&limit=50${workCursor?'&cursor='+encodeURIComponent(workCursor):''}`);
        if(!live(own))return;
        if(!Array.isArray(result.work_items)||result.work_items.some(row=>!wi(row.work_item_id)||row.project_id!==project||typeof row.title!=='string'||row.archived_at))throw Error(t('loop_preview_mismatch'));
        for(const row of result.work_items)rows.set(row.work_item_id,row);
        workCursor=result.next_cursor??null;if(!workCursor)projectIndex++;
      }
      render();
    }catch(error){if(live(own)){status.replaceChildren(errorBox(error,{within:status}));if([403,404].includes(error.status))withdraw();}}
    finally{loading=false;update();}
  }
  async function preview() {
    if(checkButton.disabled)return;
    invalidate();checking=true;const own=ticket, sent=body(),before=fingerprint(),baseline=copy(review().preconditions);
    status.textContent=t('loop_preview_pending');update();
    try{
      const value=await api('POST',`${base}/threads/${encodeURIComponent(threadId)}/adopt-and-start-preview`,sent);
      if(!live(own)||before!==fingerprint())return;
      prepared=checkedAdoptPreview(value,sent,target,baseline);
      const works=selected().map(row=>({...row,instruction:prepared.value.works.find(item=>item.work_item_id===row.work_item_id)?.instruction||row.instruction}));
      request.set('selection',JSON.stringify({...draft(),works}));signature=fingerprint();
      for(const row of prepared.value.works){const node=nodes.get(row.work_item_id);if(!node)continue;
        const ready=row.readiness.ready,pill=statusPill(ready?'accepted':'blocked');pill.textContent=t(ready?'loop_preview_ready':'loop_preview_blocked');
        const key='wst_error_'+row.readiness.blocking_code;
        const reason=!ready?t(key)===key?t('loop_preview_blocked'):t(key):'';
        node.observed.replaceChildren(pill,...(reason?[h('p',{},reason)]:[]),...(ready?[h('p',{class:'muted'},t('loop_work_resolved'),' · ',[row.agent,row.model].filter(Boolean).join(' · '),' · ',row.code_target.repository,' · ',row.code_target.source_ref),disclosure({label:t('pd_details'),body:h('code',{class:'mono'},JSON.stringify(row.code_target))})]:[]));
      }
      status.textContent=prepared.works.length?t('loop_preview_ready'):t('loop_preview_blocked');render();
    }catch(error){if(live(own)){prepared=null;if(error.message==='loop_preview_mismatch')error=Error(t('loop_preview_mismatch'));status.replaceChildren(errorBox(error,{within:status}));}}
    finally{if(own===ticket)checking=false;update();}
  }
  function withdraw(){invalidate();visible=false;rows.clear();nodes.clear();catalog.replaceChildren();status.replaceChildren();request.update();}
  return {box,request,locked:()=>request.locked()&&!request.replaceable(),available:()=>capable()&&selected().length>0,
    update,refresh:()=>{invalidate();update();},withdraw,dispose:()=>{disposed=true;withdraw();request.dispose();},
    show(){visible=true;const current=review();if(!current)return;const id=current.proposal_id;
      if(!request.locked()&&draft().proposal_id!==id){const old=draft(),drafts={...old.drafts};if(old.proposal_id)drafts[old.proposal_id]=old.works;request.set('selection',JSON.stringify({proposal_id:id,works:drafts[id]||[],drafts}));}
      const next=JSON.stringify([id,current.review.requirements,current.review.rows]);if(rendered!==next){rendered=next;invalidate();render();}
      update();if(capable()&&!rows.size&&!loading)void loadMore(true);
    }};
}
