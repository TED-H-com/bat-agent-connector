import {emptyState, iconButton} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
// A catalog selection is an original source, never managed-session authority.
export function workspaceAhpObserver({h,t,api,alive,caps,namespace,workspaceId,workspace,form,errorBox}) {
  const base=`/pm/workspaces/${encodeURIComponent(workspaceId)}`,action='pm.ahp.observe';
  const choices=new Map(),editors=new Map(),selectionStorage=`batc.pm.${namespace}.ahp-adapter.${workspaceId}`;
  let epoch=0,detailEpoch=0,ready=false,selected='',cursor=null,historyAfter=0,historyBusy=false,disposed=false;
  try{selected=localStorage.getItem(selectionStorage)||'';}catch{ /* preference only; durable requests enforce their own storage gate */ }
  const status=h('div',{role:'status'}),adapter=h('select',{'aria-label':t('ahp_adapter')}),body=h('div'),history=h('div'),detail=h('div');
  const refreshButton=iconButton({icon:'refresh-cw', label:t('ahp_refresh'), ...{type:'button',class:'secondary',onclick:()=>void refresh()}});
  const more=h('button',{type:'button',class:'secondary',hidden:true,onclick:()=>void historyPage(cursor)},t('ahp_more'));
  const element=h('details',{class:'panel pm-ahp','data-pm-ahp':''},h('summary',{},t('ahp_title')),
    h('p',{class:'muted'},t('ahp_intro')),h('div',{class:'capture-fields'},h('label',{},t('ahp_adapter'),adapter),refreshButton),status,body,
    h('details',{'data-ahp-history':''},h('summary',{},t('ahp_history')),history,more),detail);
  const valid=ticket=>{try{alive();return !disposed&&ticket===epoch;}catch{return false;}};
  const check=value=>{if(value?.workspace_id!==workspaceId)throw Error(t('ahp_invalid'));return value;};
  const stamp=value=>timeStamp(value);
  const line=(key,value)=>h('p',{},h('strong',{},`${t(key)}: `),value);
  const active=item=>ready&&selected===item.key&&choices.has(item.key)&&Boolean(workspace()?.membership);
  const request=async(...args)=>{const ticket=epoch;try{return await api(...args);}catch(error){if(valid(ticket)&&[401,403,404].includes(error.status))failure(error);throw error;}};
  function row(value,withDocument=false){
    if(value?.workspace_id!==workspaceId||!/^pmah_[0-9a-f]{32}$/.test(value.observation_id)||!/^op_[0-9a-f]{32}$/.test(value.operation_id)
      ||!/^([0-9a-f]{64})$/.test(value.document_digest)||typeof value.adapter_id!=='string'||!['observed','incomplete'].includes(value.coverage_status))throw Error(t('ahp_invalid'));
    if(withDocument){const doc=value.document,e=doc?.evidence;
      if(doc?.schema!=='ahp_observation_v1'||doc.observation_id!==value.observation_id||doc.workspace_id!==workspaceId||doc.adapter_id!==value.adapter_id
        ||!/^([0-9a-f]{64})$/.test(doc.binding_digest)||!choices.has(`${doc.adapter_id}.${doc.binding_digest}`)||!Array.isArray(e?.catalog)||e.catalog.length>200||typeof e.catalog_complete!=='boolean'
        ||!Array.isArray(e.snapshots)||!Array.isArray(e.events)||!Array.isArray(e.gaps)||!Array.isArray(e.missing)||e.status!==value.coverage_status
        ||new Set(e.catalog.map(s=>s.resource)).size!==e.catalog.length||e.catalog.some(s=>typeof s.title!=='string'||typeof s.provider!=='string'
          ||typeof s.resource!=='string'||!s.resource.length||s.resource.length>2048||/[\x00-\x1f\x7f]/.test(s.resource)
          ||s.source_kind!==undefined&&s.source_kind!=='session'||typeof s.source_identity!=='string'))throw Error(t('ahp_invalid'));
    }return value;
  }
  function renderDetail(value){
    const r=row(value,true),d=r.document,e=d.evidence;
    const snapshots=h('details',{},h('summary',{},t('ahp_snapshot_details')));
    for(const snapshot of e.snapshots){
      const state=snapshot.state||{},kind=snapshot.source_kind??state.source_kind??(state.kind==='root'?'root':'unknown');
      if(kind==='root')continue;
      snapshots.append(h('details',{},h('summary',{},state.title||snapshot.resource),h('p',{class:'mono'},snapshot.resource),
        line('ahp_from_sequence',snapshot.from_seq),line('ahp_later_events',t(snapshot.later_event_observed===true?'ahp_yes':snapshot.later_event_observed===false?'ahp_no':'ahp_unknown')),
        ...(kind==='session'?[line('ahp_provider',state.provider),line('ahp_native_lifecycle',state.lifecycle)]:kind==='changeset'
          ?[line('ahp_file_count',state.files?.length??0)]:[h('p',{class:'muted'},t('ahp_unknown_source_kind'))])));
    }
    const meta=h('details',{},h('summary',{},t('ahp_original')),h('p',{class:'mono'},r.observation_id),h('p',{class:'mono'},r.document_digest),
      h('a',{href:`#/op/${r.operation_id}`},t('pm_operation')),h('pre',{},JSON.stringify(d,null,2)));
    detail.replaceChildren(h('section',{'data-ahp-detail':'',class:'pm-ahp-facts'},h('h3',{},t('ahp_saved_detail')),line('ahp_adapter',r.adapter_id),line('ahp_captured',stamp(d.finished_at)),
      line('ahp_protocol',e.negotiated_version||e.protocol_version||'—'),
      ...(e.decoder_profile?[line('ahp_decoder_profile',e.decoder_profile)]:[]),
      line('ahp_coverage',t(e.status==='observed'?'ahp_observed':'ahp_incomplete')),line('ahp_catalog_coverage',t(e.catalog_complete?'ahp_catalog_complete':'ahp_catalog_partial')),
      ...(e.reason?[line('ahp_reason',e.reason)]:[]),line('ahp_snapshots',e.snapshots.length),line('ahp_events',e.events.length),line('ahp_gaps',e.gaps.length),
      h('p',{class:'muted'},t('ahp_evidence_limits')),...(snapshots.children.length>1?[snapshots]:[]),meta));
  }
  async function showDetail(id){const ticket=epoch,serial=++detailEpoch;detail.replaceChildren();try{
    const value=check(await request('GET',`${base}/ahp-observations/${encodeURIComponent(id)}`));if(!valid(ticket)||serial!==detailEpoch)return;renderDetail(value.observation);
  }catch(error){if(valid(ticket)&&serial===detailEpoch)failure(error);}}
  function renderHistory(value){
    check(value);if(!Array.isArray(value.observations)||typeof value.visibility_filtered!=='boolean'||value.next_cursor!==null&&(!Number.isSafeInteger(value.next_cursor)||value.next_cursor<=historyAfter))throw Error(t('ahp_invalid'));
    const rows=value.observations.map(r=>row(r));cursor=value.next_cursor;
    history.replaceChildren(...rows.map(r=>h('div',{class:'pm-ahp-history-row'},h('button',{type:'button',class:'secondary',onclick:()=>void showDetail(r.observation_id)},
      r.adapter_id, ' · ', stamp(r.created_at), ' · ', t(r.coverage_status==='observed'?'ahp_observed':'ahp_incomplete')),h('small',{class:'mono'},r.observation_id))));
    if(!rows.length)history.append(emptyState({title:t(value.visibility_filtered?'ahp_hidden':'ahp_empty')}));
    else if(value.visibility_filtered)history.append(h('p',{class:'muted'},t('ahp_hidden')));
    more.hidden=cursor===null;more.disabled=false;
  }
  async function historyPage(after=0){if(historyBusy||!ready)return;const ticket=epoch;historyBusy=true;more.disabled=true;detailEpoch++;detail.replaceChildren();try{
    const value=await request('GET',`${base}/ahp-observations?after=${after}&limit=20`);if(!valid(ticket))return;historyAfter=after;renderHistory(value);
  }catch(error){if(valid(ticket))failure(error);}finally{if(valid(ticket)){historyBusy=false;more.disabled=false;}}}
  function editorFor(option){
    const key=option.key;if(editors.has(key))return editors.get(key);
    const item={key,option,catalog:null,page:0,source:h('input',{type:'hidden'}),sessions:h('input',{type:'hidden',value:'[]'}),
      changesets:h('select',{'aria-label':t('ahp_changesets')},h('option',{value:'false'},t('ahp_sessions_only')),h('option',{value:'true'},t('ahp_include_changesets'))),
      catalogBox:h('div',{'data-ahp-catalog':''}),forms:[]};
    item.sessions.dataset.pmDefault='[]';item.changesets.dataset.pmDefault='false';
    const guard=()=>{alive();if(!active(item))throw Object.assign(Error(),{code:'VIEW_CHANGED'});};
    const canRead=()=>active(item)&&caps()?.scopes?.includes('observe');
    const fields={catalog_ref:item.source,session_uris:item.sessions,include_changesets:item.changesets};
    const selectedUris=()=>{try{const values=JSON.parse(item.sessions.value||'[]');if(!Array.isArray(values)||values.length>8||new Set(values).size!==values.length||values.some(v=>typeof v!=='string'))throw Error();return values;}catch{throw Error(t('ahp_invalid'));}};
    const selectionValid=()=>{try{const list=selectedUris();return Boolean(item.catalog&&list.length&&list.every(uri=>item.catalog.document.evidence.catalog.some(s=>s.resource===uri)));}catch{return false;}};
    const updateChecks=()=>{const list=selectedUris();for(const box of item.catalogBox.querySelectorAll('input[type=checkbox]')){box.checked=list.includes(box.value);box.disabled=item.observe.locked()||!canRead()||list.length>=8&&!box.checked;}};
    function renderCatalog(){
      item.catalogBox.replaceChildren();if(!item.catalog){item.catalogBox.append(h('p',{class:'muted'},t('ahp_need_catalog')));return;}
      const e=item.catalog.document.evidence,list=selectedUris(),start=item.page*20;
      item.catalogBox.append(h('p',{class:'muted'},t('ahp_catalog_coverage'), ': ', t(e.catalog_complete?'ahp_catalog_complete':'ahp_catalog_partial'), ' · ', stamp(item.catalog.document.finished_at)),
        h('p',{},t('ahp_selection_help')));
      if(list.some(uri=>!e.catalog.some(s=>s.resource===uri)))item.catalogBox.append(h('p',{class:'muted'},t('ahp_selection_unavailable')));
      for(const s of e.catalog.slice(start,start+20)){
        const box=h('input',{type:'checkbox',value:s.resource,'aria-label':`${t('ahp_select')} ${s.title}`});
        box.addEventListener('change',()=>{if(item.observe.locked()||!canRead())return;const before=selectedUris(),values=box.checked?[...before,s.resource]:before.filter(uri=>uri!==s.resource);
          if(values.length<=8)item.observe.set('session_uris',JSON.stringify(values));item.observe.update();updateChecks();});
        item.catalogBox.append(h('label',{class:'pm-ahp-session'},box,h('span',{},h('strong',{},s.title||s.resource),h('small',{},s.provider),h('small',{class:'mono'},s.resource))));
      }
      if(!e.catalog.length)item.catalogBox.append(h('p',{class:'muted'},t('ahp_no_sessions')));
      if(e.catalog.length>20)item.catalogBox.append(h('div',{class:'actions'},h('button',{type:'button',class:'secondary',disabled:item.page===0,onclick:()=>{item.page--;renderCatalog();}},t('pagination_prev')),
        h('span',{},`${start+1}–${Math.min(start+20,e.catalog.length)} / ${e.catalog.length}`),h('button',{type:'button',class:'secondary',disabled:start+20>=e.catalog.length,onclick:()=>{item.page++;renderCatalog();}},t('pagination_next'))));
      updateChecks();
    }
    item.apply=r=>{row(r,true);if(r.adapter_id!==option.adapter_id||r.document.binding_digest!==option.binding_digest)throw Error(t('ahp_invalid'));
      item.catalog=r;item.page=0;renderCatalog();item.observe.update();};
    item.observe=form({namespace:`${namespace}.ahp.${key}.selection`,action,target:{workspace_id:workspaceId,adapter_id:option.adapter_id},fields,api:request,guard,requiredScope:'observe',
      params:()=>{if(!selectionValid())throw Error(t('ahp_selection_unavailable'));return {session_uris:selectedUris(),include_changesets:item.changesets.value==='true'};},preconditions:()=>({}),
      permitted:()=>canRead()&&selectionValid(),recoverPermitted:canRead,label:'ahp_capture_selected',onStateChange:()=>{if(item.observe)updateChecks();},
      extra:h('div',{},item.catalogBox,h('label',{},t('ahp_changesets'),item.changesets),h('p',{class:'muted'},t('ahp_changesets_help'))),
      onSaved:async result=>{check(result);await refresh();await showDetail(result.observation_id);}});
    item.catalogForm=form({namespace:`${namespace}.ahp.${key}.catalog`,action,target:{workspace_id:workspaceId,adapter_id:option.adapter_id},fields:{},api:request,guard,requiredScope:'observe',
      params:()=>({session_uris:[],include_changesets:false}),preconditions:()=>({}),permitted:()=>canRead()&&!item.observe.locked(),recoverPermitted:canRead,label:'ahp_capture_catalog',
      extra:h('p',{class:'muted'},t('ahp_catalog_help')),onSaved:async result=>{check(result);if(!item.observe.locked()){
        item.observe.set('catalog_ref',JSON.stringify({observation_id:result.observation_id,document_digest:result.document_digest}));item.observe.set('session_uris','[]');}
        await refresh();await showDetail(result.observation_id);}});
    for(const f of [item.catalogForm,item.observe])f.box.querySelector('button[type=submit]').className='secondary';
    item.forms=[item.catalogForm,item.observe];item.element=h('div',{},h('details',{},h('summary',{},t('ahp_capabilities')),
      h('p',{},`${t('ahp_protocol')}: ${option.protocol_versions?.join(', ')||option.protocol_version||'—'}`),h('p',{class:'muted'},t('ahp_capability_limits')),h('pre',{},JSON.stringify(option,null,2))),item.catalogForm.box,item.observe.box);
    editors.set(key,item);return item;
  }
  async function selectAdapter(key,current=null){
    const ticket=++epoch;ready=true;historyBusy=false;more.disabled=false;selected=key;body.replaceChildren();detailEpoch++;detail.replaceChildren();status.replaceChildren();
    for(const item of editors.values()){
      item.catalog=null;item.catalogBox.replaceChildren();
      // Rehydrate IDs collected while this source was withdrawn or another
      // adapter was selected. The old collector may only enrich its original key.
      for(const f of item.forms)f.dispose();editors.delete(item.key);
    }
    if(!key||!choices.has(key)){body.append(h('p',{class:'muted'},t('ahp_no_adapters')));return;}
    const item=editorFor(choices.get(key));body.append(item.element);
    try{
      let r=null;const saved=item.source.value;
      if(saved){const ref=JSON.parse(saved);if(!/^pmah_[0-9a-f]{32}$/.test(ref.observation_id)||!/^([0-9a-f]{64})$/.test(ref.document_digest))throw Error(t('ahp_invalid'));
        const value=check(await api('GET',`${base}/ahp-observations/${ref.observation_id}`));if(!valid(ticket))return;r=row(value.observation,true);
        if(r.document_digest!==ref.document_digest)throw Error(t('ahp_invalid'));
      }else if(current?.adapter_id===item.option.adapter_id&&current.document?.binding_digest===item.option.binding_digest){
        r=current;item.observe.set('catalog_ref',JSON.stringify({observation_id:r.observation_id,document_digest:r.document_digest}));
      }
      if(r)item.apply(r);else item.catalogBox.append(h('p',{class:'muted'},t('ahp_need_catalog')));
      for(const f of item.forms)f.update();return valid(ticket);
    }catch(error){if(valid(ticket)){item.catalog=null;item.catalogBox.replaceChildren(h('p',{class:'muted'},t('ahp_selection_unavailable')));status.replaceChildren(errorBox(error));for(const f of item.forms)f.update();}}
  }
  adapter.addEventListener('change',()=>{try{alive();localStorage.setItem(selectionStorage,adapter.value);}catch{ /* preference only */ }void selectAdapter(adapter.value);});
  function withdraw(){epoch++;detailEpoch++;ready=false;historyBusy=false;choices.clear();body.replaceChildren();history.replaceChildren();detail.replaceChildren();status.replaceChildren();adapter.replaceChildren();adapter.disabled=true;more.hidden=true;
    for(const item of editors.values()){item.catalog=null;item.catalogBox.replaceChildren();for(const f of item.forms)f.update();}}
  function failure(error){withdraw();status.append(h('p',{class:'muted'},t('ahp_unavailable')),errorBox(error));}
  async function refresh(){const prior=selected;withdraw();const ticket=epoch;refreshButton.disabled=true;try{
    const [a,c,l]=await Promise.all(['ahp-adapters','ahp-observations/current','ahp-observations?after=0&limit=20'].map(path=>request('GET',`${base}/${path}`)));
    if(!valid(ticket))return;check(a);check(c);check(l);
    if(!Array.isArray(a.adapters)||typeof c.visibility_filtered!=='boolean'||c.visibility_filtered&&c.observation!==null)throw Error(t('ahp_invalid'));
    for(const option of a.adapters){if(typeof option.adapter_id!=='string'||!/^([0-9a-f]{64})$/.test(option.binding_digest)||option.contract!=='ahp_observer_v1')throw Error(t('ahp_invalid'));
      const key=`${option.adapter_id}.${option.binding_digest}`;choices.set(key,{...option,key});adapter.append(h('option',{value:key},option.adapter_id));}
    if(c.observation)row(c.observation,true);historyAfter=0;renderHistory(l);ready=true;adapter.disabled=!choices.size;
    const key=choices.has(prior)?prior:choices.keys().next().value||'';adapter.value=key;
    const unchanged=await selectAdapter(key,c.observation);if(!unchanged||!ready)return;
    if(c.visibility_filtered)status.append(h('p',{class:'muted'},t('ahp_hidden')));
    if(c.observation)renderDetail(c.observation);
  }catch(error){if(valid(ticket))failure(error);}finally{refreshButton.disabled=false;}}
  return {element,refresh,withdraw,dispose:()=>{disposed=true;withdraw();}};
}
