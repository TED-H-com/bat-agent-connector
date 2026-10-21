import {disclosure} from "./ui/components.js";
// Only Connector metadata changes. Labels never grant control over a BAT session.
const object = v => v && typeof v === 'object' && !Array.isArray(v);
const version = v => Number.isSafeInteger(v) && v >= 0;
const opId = v => typeof v === 'string' && /^op_[0-9a-f]{32}$/.test(v);
const equal = (a,b) => a === b || (Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v,i)=>equal(v,b[i]))) ||
  (object(a) && object(b) && Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(k=>equal(a[k],b[k])));
export function validLabels(v) {
  return Array.isArray(v) && v.length <= 8 && new Set(v).size === v.length && v.every(x=>typeof x === 'string' &&
    x === x.trim() && [...x].length >= 1 && [...x].length <= 40 && !/[\p{C}\u2028\u2029]/u.test(x));
}
const metadata = v => object(v) && version(v.version) && validLabels(v.labels);
function markControl(el, blocked, flight, own = false) {
  if (!el.hidden && flight && (own || !blocked)) {el.disabled = false; el.setAttribute('aria-disabled', 'true');}
  else {el.disabled = Boolean(blocked || flight); el.removeAttribute('aria-disabled');}
}
function markRegion(region, controls) {
  if (controls.some(el => !el.hidden && el.getAttribute('aria-disabled') === 'true')) region.setAttribute('aria-busy', 'true');
  else region.removeAttribute('aria-busy');
}
function controlShown(el) {
  return Boolean(el?.isConnected && !el.disabled && !el.closest('[hidden]'));
}
function moveFocus(from, candidate, root, region) {
  if (controlShown(candidate)) {candidate.focus(); return;}
  const controls = [...root.querySelectorAll('button, a[href], input, select, textarea')];
  const start = Math.max(controls.indexOf(from), 0);
  const next = [...controls.slice(start + 1), ...controls.slice(0, start)].find(el => controlShown(el) && el.getAttribute('aria-disabled') !== 'true');
  if (next) {next.focus(); return;}
  region.tabIndex = -1; region.focus();
}
function settleFocus(before, buttons, candidate, root, region) {
  if (!root?.isConnected || !buttons.includes(before)) return;
  const active = document.activeElement;
  if (active !== before && active !== document.body && active !== document.documentElement) return;
  if (!before.disabled && !before.closest('[hidden]')) return;
  moveFocus(before, candidate, root, region);
}
export function sessionLabelsPanel({h,t,api,caps,guard,target,storageKey,errorBox,opStatus}) {
  const path=`/sessions/${encodeURIComponent(target.host)}/${encodeURIComponent(target.session_id)}`;
  const valid = request => request?.action === 'session.labels.set' && equal(request.target,target) &&
    object(request.params) && Object.keys(request.params).length === 1 && validLabels(request.params.labels) &&
    object(request.preconditions) && Object.keys(request.preconditions).length === 1 && version(request.preconditions.expected_version);
  let saved={}, current=null, operation=null, busy=false, submission=null, refreshing=null, readable=false, damaged=false;
  try {
    const raw=JSON.parse(localStorage.getItem(storageKey));
    if(raw && typeof raw.text === 'string' && raw.text.length <= 1000 && version(raw.base_version)) saved={text:raw.text,base_version:raw.base_version};
    if(raw?.intent){
      const proven=valid(raw.intent.request) && typeof raw.intent.key === 'string' && raw.intent.key.length>0 && raw.intent.key.length<=200;
      saved.intent={request:proven?raw.intent.request:null,key:proven?raw.intent.key:null,
        operation_id:opId(raw.intent.operation_id)?raw.intent.operation_id:null};
      if(proven){saved.text=raw.intent.request.params.labels.join('\n');saved.base_version=raw.intent.request.preconditions.expected_version;}
      if(proven && !saved.intent.operation_id && raw.intent.refused==='METADATA_VERSION_CONFLICT') saved.intent.refused=raw.intent.refused;
    } else if(raw && !Object.hasOwn(saved,'text')) damaged=true;
  } catch {damaged=true;}
  const live=()=>{try{guard();return true;}catch{return false;}};
  const persist=()=>{guard();localStorage.setItem(storageKey,JSON.stringify(saved));};
  const permitted=()=>caps()?.scopes?.includes('manage') && caps()?.actions?.some(a=>a.action==='session.labels.set' && a.allowed===true);
  const terminal=()=>['succeeded','failed','cancelled'].includes(operation?.status);
  const input=h('textarea',{'aria-label':t('labels_input'),rows:3,maxlength:1000});
  input.value=saved.text||'';
  const tags=h('div',{class:'actions session-label-tags'}), message=h('div',{role:'status'}), result=h('div'), restriction=h('p',{class:'muted'});
  const error=(e, options = {})=>{if(live()){message.replaceChildren(errorBox(e, {...options, within:message}));update();}};
  const parsed=()=>input.value.trim()===''?[]:input.value.split('\n').map(v=>v.trim());
  const accept=candidate=>{
    guard();const intent=saved.intent;
    if(!intent || !opId(candidate?.operation_id) || candidate.action!=='session.labels.set' || !equal(candidate.target,target) ||
      candidate.actor!==caps()?.actor || intent.operation_id && candidate.operation_id!==intent.operation_id ||
      intent.key && candidate.idempotency_key!==intent.key || intent.request && !equal({action:candidate.action,target:candidate.target,
        params:candidate.params,preconditions:candidate.preconditions},intent.request)) throw Error(t('labels_wrong_receipt'));
    if(candidate.status==='succeeded' && (!equal({host:candidate.result?.host,session_id:candidate.result?.session_id},target) ||
      !metadata(candidate.result?.connector_metadata) || intent.request && (!equal(candidate.result.connector_metadata.labels,intent.request.params.labels) ||
      candidate.result.connector_metadata.version!==intent.request.preconditions.expected_version+1))) throw Error(t('labels_wrong_receipt'));
    operation=candidate;intent.operation_id=candidate.operation_id;persist();update();
  };
  const apply=h('button',{class:'secondary',onclick:async()=>{
    if(!live() || busy || refreshing || !readable || !permitted() || damaged || saved.intent?.operation_id ||
      saved.intent && (!saved.intent.request || saved.intent.refused))return;
    const values=parsed();if(!validLabels(values)){error(Error(t('labels_limits')), {sourceRefresh:false});return;}
    const previous=saved;
    if(!saved.intent)saved={text:input.value,base_version:saved.base_version??current.version,intent:{key:crypto.randomUUID(),operation_id:null,
      request:{action:'session.labels.set',target:{...target},params:{labels:values},preconditions:{expected_version:saved.base_version??current.version}}}};
    try{persist();}catch(e){saved=previous;error(e, {sourceRefresh:false});return;}
    busy=true;update();const intent=saved.intent;
    submission=(async()=>{
      try{const data=await api('POST','/operations?wait=3',intent.request,intent.key);guard();accept(data.operation);message.replaceChildren();}
      catch(e){if(live()){
        // This action-specific code is raised after key replay, before admission INSERT.
        if(e.code==='METADATA_VERSION_CONFLICT' && e.status===409){intent.refused=e.code;try{persist();}catch{/* keep intent in memory */}}
        error(e, {sourceRefresh:false});
      }} finally{busy=false;if(live())update();}
    })();
    try{await submission;}finally{submission=null;}
    if(live())try{await refresh(true);}catch(e){error(e);}
  }},t('labels_save'));
  const check=h('button',{class:'secondary',onclick:()=>{
    if(!live()||busy||refreshing)return;
    refresh(true).catch(error);
  }},t('permissions_check'));
  const renew=h('button',{class:'secondary',onclick:()=>{
    if(!live() || busy || refreshing || !readable || !permitted() || saved.intent && !terminal() && !saved.intent.refused)return;
    const previous=saved;
    const text=operation?.status==='succeeded'?current.labels.join('\n'):(saved.text??current.labels.join('\n'));
    saved={text,base_version:current.version};try{persist();}catch(e){saved=previous;error(e, {sourceRefresh:false});return;}
    damaged=false;operation=null;input.value=text;message.replaceChildren();update();
  }},t('labels_review_current'));
  input.oninput=()=>{
    if(!live() || saved.intent || !readable){input.value=saved.text||'';return;}
    saved={text:input.value,base_version:saved.base_version??current.version};try{persist();}catch(e){error(e, {sourceRefresh:false});}update();
  };
  const editorMount=h('div',{class:'session-label-editor'});
  const reason=h('p',{class:'muted session-label-reason'});
  const foldLabel=h('span');
  const fold=disclosure({label:foldLabel,body:h('div',{},reason,editorMount)});
  const stateLine=h('p',{class:'muted session-label-state'});
  let mounted=false;
  const mountEditor=()=>{
    if(mounted)return;
    editorMount.append(h('p',{class:'muted'},t('labels_help')),h('label',{},t('labels_input'),input),
      h('p',{class:'muted'},t('labels_limits')),h('div',{class:'actions'},apply,check,renew),restriction,result,message);
    mounted=true;
  };
  fold.addEventListener('toggle',()=>{if(fold.open)update();});
  const box=h('section',{class:'session-labels','data-session-labels':''},stateLine,tags,fold);
  function update(){
    if(!live())return;
    const before=document.activeElement;
    const fixed=Boolean(saved.intent);
    const canSave=readable && permitted() && !damaged;
    const showEdit=canSave || fixed || damaged || (readable && Object.hasOwn(saved,'text'));
    const chips=current?.labels.length?current.labels.map(v=>h('span',{class:'chip'},v)):[];
    tags.replaceChildren(...chips);
    tags.hidden=!chips.length;
    stateLine.hidden=Boolean(chips.length);
    stateLine.textContent=t(readable?'labels_empty':'labels_unavailable');
    foldLabel.textContent=t(showEdit?'labels_edit':'session_label_details');
    const why=!readable?t('labels_unreadable'):!permitted()?t('labels_scope'):'';
    reason.hidden=showEdit||!why;
    reason.textContent=why;
    fold.hidden=!showEdit && !why;
    editorMount.hidden=!showEdit;
    if(showEdit && fold.open)mountEditor();
    input.disabled=fixed||busy||!readable||!permitted()||damaged;
    const flight=busy||Boolean(refreshing);
    apply.hidden=Boolean(saved.intent?.operation_id||saved.intent?.refused);
    apply.textContent=t(fixed?'permissions_retry':'labels_save');
    markControl(apply,!readable||!permitted()||damaged||Boolean(fixed&&!saved.intent.request)||!validLabels(parsed()),flight,busy);
    check.textContent=t(fixed?'permissions_check':'labels_refresh');
    markControl(check,false,flight,false);
    renew.hidden=fixed?!terminal()&&!saved.intent.refused:!damaged && !(current && saved.base_version!==undefined && saved.base_version!==current.version);
    markControl(renew,!readable||!permitted(),flight,false);
    markRegion(message,[apply,check,renew]);
    const candidate=before===apply?saved.intent?.operation_id?check:saved.intent?.refused?renew:null:before===renew?input:null;
    settleFocus(before,[apply,check,renew],candidate,box,message);
    restriction.textContent=!permitted()?t('labels_scope'):!readable?t('labels_unreadable'):saved.base_version!==undefined&&saved.base_version!==current.version?t('labels_changed'):'';
    result.replaceChildren();
    if(fixed){result.append(h('p',{},operation?opStatus(operation):t(saved.intent.refused?'labels_refused':'permissions_unknown'),' ',
      saved.intent.operation_id?h('a',{href:`#/op/${saved.intent.operation_id}`},t('permissions_details')):null),
      h('p',{class:'muted'},t('labels_fixed',{version:saved.intent.request?.preconditions.expected_version??'?'})));
      if(operation?.status==='succeeded')result.append(h('p',{},t('labels_saved')));
      if(!saved.intent.request&&!saved.intent.operation_id)result.append(h('p',{class:'error'},t('permissions_damaged')));
    }
    if(damaged)result.append(h('p',{class:'error'},t('labels_damaged')));
  }
  async function refresh(fresh=false){
    guard();
    if(!caps()?.actions?.some(a=>a.action==='session.labels.set')){readable=false;update();return;}
    if(submission){await submission;guard();}
    if(refreshing){await refreshing;if(fresh)return refresh(true);return;}
    refreshing=(async()=>{
      if(saved.intent?.operation_id){const data=await api('GET',`/operations/${saved.intent.operation_id}`);guard();accept(data.operation);}
      const data=await api('GET',path);guard();
      const matched=data.session?.host===target.host && data.session?.session_id===target.session_id ||
        !data.session && data.cleanup?.some(item=>item.kind==='session' && item.host===target.host && item.session_id===target.session_id);
      if(!matched || !metadata(data.connector_metadata))throw Error(t('labels_unreadable'));
      current=data.connector_metadata;readable=true;
      if(!Object.hasOwn(saved,'text')&&!saved.intent&&!damaged)input.value=current.labels.join('\n');
    })();update();
    try{await refreshing;}catch(e){
      // A failed read disables the field. If it had focus, keep the keyboard on the status.
      const typing=document.activeElement===input&&!input.disabled;
      readable=false;error(e);
      if(typing&&input.disabled&&live()){message.tabIndex=-1;message.focus();}
      throw e;
    }finally{refreshing=null;if(live())update();}
  }
  update();return {box,refresh,update};
}
