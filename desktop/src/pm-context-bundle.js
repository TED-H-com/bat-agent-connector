import {skeleton} from './ui/components.js';
// A reviewed selector fixes central bytes; this client never constructs plan contents.
export function pmContextBundle({h,t,api,guard,caps,target,thread,errorBox}) {
  const value=h('input',{type:'hidden'}),summary=h('div',{'data-pm-context-selection':''});
  const status=h('div',{role:'status'}),details=h('div',{'data-pm-context-preview':''});
  let request=null,preview=null,verified=null,loading=false,generation=0;
  const supported=()=>caps()?.features?.pm_workspaces?.accepted_context_bundle?.schema==='pm_context_bundle_v1';
  const currentVersion=()=>thread()?.thread?.version;
  const selected=()=>{try{return value.value?validate(JSON.parse(value.value)):null;}catch{return null;}};
  // Only this panel's own validation failures may supply their localized summary.
  const clientErrors = new WeakSet();
  const clientError = key => {const error = Error(t(key)); clientErrors.add(error); return error;};
  function validate(row) {
    const ref=row?.context_bundle_ref,sha=/^[0-9a-f]{64}$/;
    if(row?.schema!=='pm_context_bundle_preview_v1'||row.workspace_id!==target.workspace_id||row.thread_id!==target.thread_id
      ||!Number.isSafeInteger(row.thread_version)||row.thread_version<1||ref?.schema!=='pm_context_bundle_v1'
      ||Object.keys(ref).sort().join(',')!=='adoption_operation_id,document_digest,git_receipt_digest,revision_id,schema'
      ||!/^pmrev_[0-9a-f]{32}$/.test(ref.revision_id)||!/^op_[0-9a-f]{32}$/.test(ref.adoption_operation_id)
      ||!sha.test(ref.document_digest)||!sha.test(ref.git_receipt_digest)||!Array.isArray(row.manifest)||!row.manifest.length
      ||row.manifest.some(file=>typeof file.path!=='string'||!sha.test(file.digest)||!Number.isSafeInteger(file.utf8_bytes)||file.utf8_bytes<0||typeof file.included!=='boolean')
      ||typeof row.coverage?.accepted_document_complete!=='boolean'
      ||row.coverage.accepted_document_complete!==row.manifest.every(file=>file.included)
      ||row.coverage.acceptance_coverage!==(row.coverage.accepted_document_complete?'complete_original_document':'incomplete_original_document')
      ||row.execution_authorized!==false||row.coverage.execution_authorized!==false||row.coverage.adoption_authorized!==false
      ||row.authority!=='context_only')throw clientError('pm_cb_invalid');
    return row;
  }
  const signature=row=>JSON.stringify(row?.context_bundle_ref);
  const valid=()=>!value.value||Boolean(selected()&&verified===value.value&&selected().thread_version===currentVersion()&&supported());
  const inspect=h('button',{type:'button',class:'secondary',onclick:async()=>{
    let ticket,actor;
    try {guard?.();if(loading||request?.locked()||!supported())return;
      loading=true;ticket=++generation;actor=caps()?.actor;preview=null;verified=null;status.replaceChildren(skeleton(2));details.replaceChildren();update();request?.update();
      const result=await api('GET',`/pm/workspaces/${encodeURIComponent(target.workspace_id)}/threads/${encodeURIComponent(target.thread_id)}/context-bundle`);
      guard?.();if(ticket!==generation||caps()?.actor!==actor||request?.locked())return;
      preview=validate(result);
      if(preview.thread_version!==currentVersion())throw clientError('pm_cb_stale');
      const old=selected();if(old&&signature(old)===signature(preview)&&old.thread_version===preview.thread_version&&JSON.stringify(old.manifest)===JSON.stringify(preview.manifest))verified=value.value;
      status.textContent='';renderDetails(preview);
    } catch(error){
      try{guard?.();}catch{return;}
      if(ticket!==generation||caps()?.actor!==actor||request?.locked())return;
      preview=null;verified=null;details.replaceChildren();status.replaceChildren(errorBox(error,{summary:clientErrors.has(error)?error.message:t('pm_cb_unavailable'),within:status}));
    }
    finally{loading=false;update();request?.update();}
  }},t('pm_cb_preview'));
  const choose=h('button',{type:'button',class:'secondary',hidden:true,onclick:()=>{
    try{guard?.();if(!preview||preview.thread_version!==currentVersion()||request?.locked())return;
      request.set('accepted_context',JSON.stringify(preview));verified=value.value;update();request.update();
    }catch{ /* A retired view cannot change a durable draft. */ }
  }},t('pm_cb_select'));
  const clear=h('button',{type:'button',class:'secondary',hidden:true,onclick:()=>{
    try{guard?.();if(request?.locked())return;request.set('accepted_context','');verified=null;update();request.update();}catch{ /* Retired view. */ }
  }},t('pm_cb_clear'));
  const box=h('details',{'data-pm-context-bundle':''},h('summary',{},t('pm_cb_title')),
    h('p',{class:'muted'},t('pm_cb_help')),value,summary,h('div',{class:'actions'},inspect,clear),status,details,choose);
  function renderDetails(row){
    details.replaceChildren(h('p',{},h('strong',{},t(row.coverage.accepted_document_complete?'pm_cb_complete':'pm_cb_incomplete'))),
      h('p',{class:'pm-source'},t('pm_cb_revision'),' ',row.context_bundle_ref.revision_id),
      h('ul',{},...row.manifest.map(file=>h('li',{class:'pm-source'},file.path,' · ',t(file.included?'pm_cb_included':'pm_cb_omitted'),' · ',String(file.utf8_bytes),' ',t('pm_cb_bytes')))),
      h('p',{class:'muted'},t('pm_cb_boundary')));
  }
  function update(){
    const locked=request?.locked(),row=selected();
    box.hidden=!supported()&&!value.value;
    inspect.disabled=Boolean(locked||loading||!supported()||!thread());
    choose.hidden=!preview;choose.disabled=Boolean(locked||loading||preview?.thread_version!==currentVersion());
    choose.textContent=t(preview?.coverage.accepted_document_complete?'pm_cb_select':'pm_cb_select_partial');
    clear.hidden=!value.value;clear.disabled=Boolean(locked);
    summary.replaceChildren();
    if(value.value)summary.append(h('p',{},h('strong',{},t('pm_cb_selected')),' ',row?row.context_bundle_ref.revision_id:t('pm_cb_invalid')),
      h('p',{class:'muted'},t(row?.coverage.accepted_document_complete?'pm_cb_complete':'pm_cb_incomplete')),
      h('p',{class:'muted'},t(locked?'pm_cb_original':valid()?'pm_cb_fixed':'pm_cb_recheck')));
  }
  return {box,value,valid,update,bind:form=>{request=form;update();},params:values=>{
    if(!values.accepted_context)return {};
    const row=validate(JSON.parse(values.accepted_context));return {context_bundle_ref:row.context_bundle_ref};
  }};
}
