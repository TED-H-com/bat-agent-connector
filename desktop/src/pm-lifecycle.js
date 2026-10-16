import {nativeRunState} from './pm-native-analysis.js';
import {retiredView} from './pm-retired.js';
import {actionLabel, operationStatusLabel} from './global-labels.js';
import {emptyState, skeleton} from './ui/components.js';
import {phaseVocabulary} from './ui/ui-strings.js';
// Management intent and execution admissions stay on the central journal.
export function workspaceLifecycle({h,t,api,alive,workspaceId,caps,permitted,form,input,labelled,errorBox,onChange}) {
  const endpoint=`/pm/workspaces/${encodeURIComponent(workspaceId)}/lifecycle`,actor=caps()?.actor;
  const feature=caps()?.features?.pm_workspaces?.lifecycle;
  let current=null,ready=false,serial=0,historyLoaded=false,historyRequest=null,rows=[],cursor=null,historyCursor=null,workFiltered=false;
  let retentionLocked=()=>true;
  const editors=[],status=h('div',{role:'status'}),summary=h('div'),work=h('div'),historyBody=h('div');
  const controls=h('details',{'data-pm-lifecycle-controls':''},h('summary',{},t('pm_lifecycle_manage')));
  const history=h('details',{'data-pm-lifecycle-history':''},h('summary',{},t('pm_lifecycle_history')),historyBody);
  const element=h('section',{class:'pm-lifecycle','data-pm-lifecycle':''},h('h2',{},t('pm_lifecycle_title')),status,summary,controls,work,history);
  const gate=()=>ready&&current&&caps()?.actor===actor&&permitted(true);
  const hidden=()=>h('input',{type:'hidden'});
  const stageName=value=>t('pm_lifecycle_'+(value||'unclassified'));
  const canAction=(action,readParams)=>{
    // Only this selected workspace's explicit PM package lineage uses its lifecycle.
    // The server independently binds and validates original lineage at admission.
    if(action==='repository.continue'&&typeof readParams==='function'){
      try{const params=readParams();if(Object.hasOwn(params,'pm_package_ref')){
        return params.pm_package_ref?.workspace_id===workspaceId&&ready&&Boolean(current)
          &&!['paused','closed'].includes(current.lifecycle.stage)&&!['pausing','closing'].includes(current.lifecycle.status);
      }}catch{return false;}
    }
    if(!Array.isArray(feature.gated_actions))return false;
    if(!feature.gated_actions.includes(action))return true;
    if(!ready||!current)return false;
    if(!current.blocked_actions.includes(action))return true;
    const rule=current.record_variants?.[action];
    if(!Array.isArray(rule?.absent_params)||!rule.absent_params.length||typeof readParams!=='function')return false;
    try{const params=readParams();return params&&rule.absent_params.every(key=>typeof key==='string'&&!Object.hasOwn(params,key));}catch{return false;}
  };
  const notify=()=>{for(const editor of editors)editor.update();onChange();for(const field of retentionChoices.querySelectorAll('input'))field.disabled=retentionLocked();};
  const checkScope=value=>{if(value?.workspace_id!==workspaceId)throw Error(t('pm_scope_mismatch'));return value;};
  const validCursor=(next,previous)=>next===null||Number.isSafeInteger(next)&&next>previous;
  const chosen=()=>{try{const ids=JSON.parse(retained.value||'[]');return Array.isArray(ids)&&ids.every(id=>typeof id==='string')?ids:[];}catch{return [];}};
  function makeEditor(kind,{stage=false,extra=null,fields={},params=()=>({}),eligible=()=>true}={}){
    const reason=input('pm_lifecycle_reason',{max:4000,multiline:true}),version=hidden();
    const stages=['idea','planning','development','maintain'];
    const choice=stage?h('select',{'aria-label':t('pm_lifecycle_stage'),required:true},h('option',{value:''},t('pm_lifecycle_choose')),...stages.map(value=>h('option',{value},stageName(value)))):null;
    if(choice)choice.dataset.pmDefault='';
    const maintenance=stage?hidden():null;
    const values={reason,version,...(choice?{stage:choice,maintenance}:{}),...fields};
    const selectedMaintenance=()=>{try{return JSON.parse(maintenance?.value||'null');}catch{return null;}};
    const maintenanceReady=()=>!choice||choice.value!=='maintain'||current?.maintenance?.available===true
      &&JSON.stringify(selectedMaintenance())===JSON.stringify(current.maintenance.reference);
    const captureMaintenance=()=>{if(maintenance)editor.set('maintenance',choice.value==='maintain'&&current?.maintenance?.available?JSON.stringify(current.maintenance.reference):'');};
    let editor;
    editor=form({action:`pm.lifecycle.${kind}`,target:{workspace_id:workspaceId},fields:values,
      params:data=>({reason:data.reason,...(choice?{stage:data.stage}:{}),...(choice&&data.stage==='maintain'?{maintenance_ref:JSON.parse(data.maintenance)}:{}),...params(data)}),
      preconditions:()=>({expected_lifecycle_revision:Number(version.value)}),
      permitted:()=>Boolean(gate()&&eligible()&&maintenanceReady()&&version.value!==''&&Number(version.value)===current.lifecycle.revision&&(!choice||stages.includes(choice.value))),
      recoverPermitted:()=>permitted(true),label:kind==='retain_inflight'?'pm_lifecycle_retain':'pm_lifecycle_'+kind,
      onStateChange:()=>{editablePhase();if(kind==='retain_inflight')for(const check of retentionChoices.querySelectorAll('input'))check.disabled=retentionLocked();},
      onSaved:()=>refresh(),extra:h('div',{},choice?labelled('pm_lifecycle_stage',choice):'',extra,
        labelled('pm_lifecycle_reason',reason),version,maintenance,...Object.values(fields))});
    const rebase=h('button',{type:'button',class:'secondary',onclick:()=>{
      if(!gate()||editor.locked())return;editor.set('version',String(current.lifecycle.revision));
      captureMaintenance();
      if(kind==='retain_inflight')editor.set('transition',current.lifecycle.transition_id||'');editor.update();
    }},t('pm_lifecycle_review_version'));
    if(choice)choice.addEventListener('change',()=>{if(!editor.locked()){captureMaintenance();editor.update();}});
    const warning=h('p',{class:'muted',hidden:true},t('pm_lifecycle_stale'));
    editor.box.append(warning,rebase);
    function editablePhase(){
      // An unanswered phase blocks submission. It must not prevent choosing one.
      // Keep the durable form's authority and recovery locks on every state update.
      if(!choice||!editor||editor.locked()||stages.includes(choice.value)||!gate()||!eligible()
        ||version.value===''||Number(version.value)!==current.lifecycle.revision
        ||!caps()?.scopes?.includes('manage')||!caps()?.actions?.some(row=>row.action===`pm.lifecycle.${kind}`&&row.allowed===true))return;
      choice.disabled=false;reason.disabled=false;
    }
    const originalUpdate=editor.update;
    editor.update=()=>{
      const empty=!reason.value&&(!choice||choice.value===choice.dataset.pmDefault)&&(!fields.operations||!chosen().length);
      if(current&&!editor.locked()&&(version.value===''||empty))editor.set('version',String(current.lifecycle.revision));
      if(kind==='retain_inflight'&&current&&!editor.locked()&&(!fields.transition.value||empty))editor.set('transition',current.lifecycle.transition_id||'');
      originalUpdate();
      const stale=Boolean(current&&version.value!==''&&(Number(version.value)!==current.lifecycle.revision||!maintenanceReady()));
      warning.hidden=!stale;rebase.hidden=!stale;rebase.disabled=!gate()||editor.locked();
      if(choice)for(const option of choice.options)option.disabled=Boolean(option.value&&current&&!current.available_stages.includes(option.value));
    };
    editors.push(editor);return editor;
  }
  const stage=makeEditor('set_stage',{stage:true,extra:h('p',{class:'muted'},t('pm_lifecycle_no_dispatch')),
    eligible:()=>!['paused','closed'].includes(current.lifecycle.stage)});
  const pause=makeEditor('pause',{eligible:()=>!['paused','closed'].includes(current.lifecycle.stage)});
  const close=makeEditor('close',{extra:h('p',{class:'muted'},t('pm_lifecycle_close_help')),eligible:()=>current.lifecycle.stage!=='closed'});
  const reopen=makeEditor('reopen',{stage:true,extra:h('p',{class:'muted'},t('pm_lifecycle_no_dispatch')),
    eligible:()=>['paused','closed'].includes(current.lifecycle.stage)});
  for(const [kind,editor] of [['set_stage',stage],['pause',pause],['close',close],['reopen',reopen]])controls.append(
    h('details',{'data-pm-lifecycle-intent':kind},h('summary',{},t('pm_lifecycle_'+kind)),editor.box));
  const maintenanceNote=h('p',{class:'muted'});controls.append(maintenanceNote);
  const retained=hidden(),transition=hidden(),retentionChoices=h('div');
  const retain=makeEditor('retain_inflight',{fields:{operations:retained,transition},
    params:values=>({transition_id:values.transition,operation_ids:JSON.parse(values.operations||'[]')}),
    eligible:()=>Boolean(current.lifecycle.transition_id&&transition.value===current.lifecycle.transition_id&&chosen().length
      &&chosen().every(id=>rows.some(row=>row.operation_id===id&&row.disposition==='unresolved'))),
    extra:h('div',{},h('p',{class:'muted'},t('pm_lifecycle_retain_help')),retentionChoices)});
  retentionLocked=()=>!gate()||retain.locked()||Number(retain.fields.version.value)!==current.lifecycle.revision||transition.value!==current.lifecycle.transition_id;
  const retention=h('details',{'data-pm-lifecycle-retain':''},h('summary',{},t('pm_lifecycle_retain')),retain.box);element.insertBefore(retention,history);
  function renderRetention(){
    retentionChoices.replaceChildren();const selected=chosen();
    const available=rows.filter(row=>row.disposition==='unresolved');
    for(const id of new Set([...available.map(row=>row.operation_id),...selected])){
      const row=available.find(row=>row.operation_id===id),check=h('input',{type:'checkbox',checked:selected.includes(id),'aria-label':t('pm_lifecycle_select_operation')+' '+id});
      check.disabled=retentionLocked();check.addEventListener('change',()=>{
        if(retentionLocked()){check.checked=chosen().includes(id);return;}
        retain.set('operations',JSON.stringify(check.checked?[...new Set([...chosen(),id])]:chosen().filter(value=>value!==id)));retain.update();
      });
      retentionChoices.append(h('label',{},check,' ',row?`${actionLabel(row.action)} · ${operationStatusLabel(row.operation_status)}`:t('pm_lifecycle_retained_missing'),h('code',{},id)));
    }
    retention.hidden=!current?.lifecycle.transition_id&&!retain.locked()&&!selected.length;
  }
  function renderWork(){
    work.replaceChildren(h('h3',{},t('pm_lifecycle_inflight')));
    if(!rows.length)work.append(emptyState({title:t('pm_lifecycle_inflight_empty'),text:h('span',{class:'muted'},t('pm_lifecycle_inflight_empty_help'))}));
    if(workFiltered)work.append(h('p',{class:'muted'},t('pm_lifecycle_filtered')));
    for(const row of rows){
      const details=h('details',{'data-pm-lifecycle-operation':row.operation_id},h('summary',{},actionLabel(row.action),' · ',t('pm_lifecycle_'+row.disposition)),
        h('p',{},row.actor,' · ',operationStatusLabel(row.operation_status)),h('a',{href:`#/op/${row.operation_id}`},t('pm_operation')));
      for(const step of row.steps||[])details.append(h('p',{class:'muted'},`${step.name} · ${operationStatusLabel(step.status)}`));
      if(row.writer)details.append(h('p',{class:'muted'},`${t('pm_lifecycle_writer')}: ${nativeRunState(t,row.writer)}`));
      if(row.retention)details.append(h('p',{},row.retention.reason));
      if(row.retention_visibility_filtered)details.append(h('p',{class:'muted'},t('pm_lifecycle_filtered')));
      work.append(details);
    }
    if(cursor!==null)work.append(h('button',{type:'button',class:'secondary',onclick:()=>void moreWork()},t('pm_lifecycle_more')));
    renderRetention();
  }
  function verifyRows(page,after){
    checkScope(page);if(page.transition_id!==current.lifecycle.transition_id||!Array.isArray(page.inflight)||!validCursor(page.next_cursor,after)
      ||page.inflight.some(row=>!/^op_[0-9a-f]{32}$/.test(row.operation_id)||!Number.isSafeInteger(row.sequence)))throw Error(t('pm_scope_mismatch'));
    return page;
  }
  async function moreWork(){
    if(cursor===null||!current)return;const ticket=serial,after=cursor;
    const button=work.querySelector('button');if(button)button.disabled=true;
    try{
      const page=await api('GET',`${endpoint}/inflight?transition_id=${current.lifecycle.transition_id}&after=${after}&limit=20`);alive();if(ticket!==serial)return;
      verifyRows(page,after);const ids=new Set(rows.map(row=>row.operation_id));if(page.inflight.some(row=>ids.has(row.operation_id)))throw Error(t('pm_scope_mismatch'));
      rows.push(...page.inflight);cursor=page.next_cursor;workFiltered||=Boolean(page.visibility_filtered);renderWork();notify();
    }catch(error){if(ticket===serial)unavailable(error);}
  }
  function render(){
    const state=current.lifecycle,stopped=['paused','closed'].includes(state.stage);
    if(state.stage===null){
      const empty=emptyState({title:stageName(null),text:h('span',{class:'muted'},t('pm_lifecycle_legacy_help'))});
      empty.querySelector('h2').classList.add('pm-lifecycle-stage');summary.replaceChildren(empty);
    }else summary.replaceChildren(h('p',{class:'pm-lifecycle-stage'},h('strong',{},stageName(['pausing','closing'].includes(state.status)?state.status:state.stage))),
      h('p',{class:'muted'},t(stopped?'pm_lifecycle_stopped_help':'pm_lifecycle_active_help')));
    maintenanceNote.textContent=t(state.stage==='maintain'?'pm_lifecycle_maintenance_active':current.maintenance?.available?'pm_lifecycle_maintenance_ready':'pm_lifecycle_maintenance_unavailable');
    if(current.transition?.reason)summary.append(h('p',{},current.transition.reason));
    if(!permitted(true))summary.append(h('p',{class:'muted'},t('pm_lifecycle_reader')));
    if(current.inflight_summary.visibility_filtered||current.transition_visibility_filtered)summary.append(h('p',{class:'muted'},t('pm_lifecycle_filtered')));
    renderWork();notify();renderRetention();
  }
  function withdraw(){++serial;ready=false;current=null;rows=[];cursor=null;historyCursor=null;historyLoaded=false;historyRequest=null;workFiltered=false;
    maintenanceNote.textContent='';summary.replaceChildren();work.replaceChildren();retentionChoices.replaceChildren();historyBody.replaceChildren();status.replaceChildren();notify();}
  function unavailable(error){withdraw();status.replaceChildren(errorBox(error,{summary:t('pm_lifecycle_unknown'),within:status}));}
  async function refresh({event=false}={}){
    // An unrelated background read does not withdraw the last observed policy.
    // Disabling a pressed submit button here makes the browser drop its click.
    // Explicit refreshes and policy changes still close admission before reading.
    if(!event||!ready||!current){withdraw();status.replaceChildren(skeleton(2));}
    else ++serial;
    const ticket=serial;
    try{
      const value=checkScope(await api('GET',endpoint));alive();if(ticket!==serial)return;
      if(!value.lifecycle||!Number.isSafeInteger(value.lifecycle.revision)||value.lifecycle.revision<0
        ||![null,'idea','planning','development','maintain','paused','closed'].includes(value.lifecycle.stage)
        ||!['active','pausing','paused','closing','closed','unclassified'].includes(value.lifecycle.status)
        ||!Array.isArray(value.blocked_actions)||!Array.isArray(value.available_stages)
        ||!value.inflight_summary)throw Error(t('pm_scope_mismatch'));
      current=value;
      if(value.lifecycle.transition_id){
        const page=await api('GET',`${endpoint}/inflight?transition_id=${value.lifecycle.transition_id}&after=0&limit=20`);alive();if(ticket!==serial)return;
        verifyRows(page,0);rows=page.inflight;cursor=page.next_cursor;workFiltered=Boolean(page.visibility_filtered);
      }
      ready=true;status.replaceChildren();render();if(history.open)await loadHistory();
    }catch(error){if(retiredView(error))return;if(ticket===serial)unavailable(error);}
  }
  async function loadHistory(after=0){
    if(!current||historyRequest===serial)return;const ticket=serial;historyRequest=ticket;
    try{
      const page=checkScope(await api('GET',`${endpoint}/transitions?after=${after}&limit=20`));alive();if(ticket!==serial)return;
      if(!Array.isArray(page.transitions)||!validCursor(page.next_cursor,after))throw Error(t('pm_scope_mismatch'));
      if(!after)historyBody.replaceChildren();else historyBody.querySelector('button')?.remove();
      for(const row of page.transitions){
        if(!/^pmlt_[0-9a-f]{32}$/.test(row.transition_id))throw Error(t('pm_scope_mismatch'));
        const detail=h('details',{},h('summary',{},row.reason||t('pm_lifecycle_trace')),h('p',{class:'muted'},row.actor||''));
        if(Object.hasOwn(phaseVocabulary,row.to_stage))detail.append(h('p',{'data-pm-lifecycle-history-phase':''},
          Object.hasOwn(phaseVocabulary,row.from_stage)?stageName(row.from_stage)+' → ':'',stageName(row.to_stage)));
        let done=false;detail.addEventListener('toggle',async()=>{
          if(!detail.open||done)return;done=true;
          try{const original=checkScope(await api('GET',`${endpoint}/transitions/${row.transition_id}`));alive();if(ticket!==serial||!detail.isConnected)return;
            if(original.transition?.transition_id!==row.transition_id)throw Error(t('pm_scope_mismatch'));
            detail.append(h('pre',{class:'pm-brief-proof'},JSON.stringify(original.transition,null,2)));
          }catch(error){if(ticket===serial)unavailable(error);}
        });historyBody.append(detail);
      }
      historyLoaded=true;historyCursor=page.next_cursor;
      if(historyCursor!==null)historyBody.append(h('button',{type:'button',class:'secondary',onclick:()=>void loadHistory(historyCursor)},t('pm_lifecycle_more_history')));
      if(page.visibility_filtered)historyBody.append(h('p',{class:'muted'},t('pm_lifecycle_filtered')));
    }catch(error){if(ticket===serial)unavailable(error);}
    finally{if(historyRequest===ticket)historyRequest=null;}
  }
  history.addEventListener('toggle',()=>{if(history.open&&!historyLoaded)void loadHistory();});
  return {element,refresh,withdraw,newActionAllowed:canAction,phase:()=>current?.lifecycle?.stage};
}
