// Exact linked manual sources only. A read receipt never grants session control.
export function kanbanObservation({h,t,api,alive,workspaceId,workId,caps,permitted,form,errorBox,onSourceFailure}) {
  const action='pm.session.quiescence.observe',sources=new Map();
  let ready=false,generation=0,editor=null;
  const select=h('select',{'aria-label':t('pm_kanban_manual_source'),required:true}),context=h('p',{class:'pm-kanban-note'}),result=h('div');
  const key=row=>JSON.stringify([row.link_id,row.expected_link_operation_id,row.expected_project_binding_id,row.expected_session_identity_digest]);
  const selected=()=>sources.get(select.value);
  const reader=()=>ready&&caps()?.scopes?.includes('observe');
  const writer=()=>reader()&&permitted(false);
  const source=()=>{const row=selected();if(!row?.available)throw Error(t('pm_kanban_manual_unavailable'));return row;};
  const reason=row=>t('pm_kanban_'+({manual_native_identity_unavailable:'manual_identity',manual_multiple_inputs_unsupported:'manual_multiple',manual_native_turn_active:'manual_active'}[row?.reason]||'manual_unavailable'));
  function describe(){select.disabled=Boolean(editor?.locked())||!writer()||!caps()?.actions?.some(row=>row.action===action&&row.allowed);
    context.replaceChildren(t(!permitted(false)||!caps()?.scopes?.includes('observe')
    ||!caps()?.actions?.some(row=>row.action===action&&row.allowed)?'pm_kanban_manual_authority':!ready||!selected()?'pm_kanban_manual_unavailable':selected().available?'pm_kanban_manual_ready':'pm_kanban_manual_unavailable'));if(selected()?.reason)context.append(' ',reason(selected()));}
  editor=form({action,target:{workspace_id:workspaceId,work_item_id:workId},fields:{source:select},requiredScope:'observe',
    params:()=>({link_id:source().link_id}),preconditions:()=>{const row=source();return {
      expected_link_operation_id:row.expected_link_operation_id,expected_project_binding_id:row.expected_project_binding_id,
      expected_session_identity_digest:row.expected_session_identity_digest};},
    permitted:()=>writer()&&selected()?.available===true,recoverPermitted:reader,label:'pm_kanban_manual_observe',
    extra:h('div',{},h('p',{class:'pm-kanban-note'},t('pm_kanban_manual_help')),h('label',{},t('pm_kanban_manual_source'),select),context),
    onStateChange:describe,onSaved:receipt=>{
      if(receipt?.schema!=='pm_manual_single_input_quiescence_v1'||receipt.workspace_id!==workspaceId||receipt.work_item_id!==workId
        ||receipt.runtime_status!=='current_single_input_quiescent'||receipt.process_death_proven!==false
        ||receipt.ownership!=='manual_read_only'||receipt.work_acceptance!=='not_evaluated'
        ||!/^op_[0-9a-f]{32}$/.test(receipt.observation_operation_id||'')||!/^[0-9a-f]{64}$/.test(receipt.document_digest||'')
        ||!Number.isFinite(receipt.recorded_at))throw Error(t('pm_kanban_scope_error'));
      const recorded=new Date(receipt.recorded_at*1000).toISOString();
      result.replaceChildren(h('p',{class:'pm-kanban-note'},t('pm_kanban_manual_recorded')),h('p',{class:'pm-kanban-note'},t('pm_kanban_runtime_limits')),
        h('p',{},t('pm_kanban_source_time'),': ',h('time',{datetime:recorded},recorded)),h('code',{class:'pm-source'},receipt.document_digest));
    },api:async(...args)=>{const ticket=generation;try{const data=await api(...args);alive();
      if(ticket!==generation)throw Object.assign(Error(),{code:'PM_DISPOSED'});return data;
    }catch(error){alive();if(ticket===generation&&[401,403,404].includes(error.status))onSourceFailure(error);throw error;}},errorBox});
  editor.box.querySelector('button[type=submit]').className='secondary';
  select.addEventListener('change',()=>{result.replaceChildren();editor.update();});
  const box=h('div',{'data-pm-manual-observation':workId},h('h4',{},t('pm_kanban_manual_title')),editor.box,result);
  function update(rows){
    ++generation;
    if(!Array.isArray(rows)||rows.length>64)throw Error(t('pm_kanban_scope_error'));
    const value=select.value;sources.clear();select.replaceChildren(h('option',{value:''},t('pm_kanban_manual_choose')));
    for(const row of rows){
      if(!Number.isSafeInteger(row.link_id)||row.link_id<=0||!/^op_[0-9a-f]{32}$/.test(row.expected_link_operation_id||'')
        ||!/^pmpb_[0-9a-f]{32}$/.test(row.expected_project_binding_id||'')||!/^[0-9a-f]{64}$/.test(row.expected_session_identity_digest||'')
        ||!['host','session_id'].every(field=>typeof row[field]==='string'&&row[field].length>0&&row[field].length<=4096)
        ||typeof row.available!=='boolean'||row.reason!=null&&typeof row.reason!=='string'||row.available&&row.reason!=null
        ||sources.has(key(row)))throw Error(t('pm_kanban_scope_error'));
      sources.set(key(row),row);select.append(h('option',{value:key(row)},`${row.host} / ${row.session_id}${row.available?'':` · ${reason(row)}`}`));
    }
    // Keep the exact earlier native identity. A changed epoch is a new explicit selection.
    if(value&&!sources.has(value))select.append(h('option',{value},t('pm_kanban_manual_original_missing')));
    select.value=value;ready=true;editor.update();
  }
  function withdraw(){++generation;ready=false;sources.clear();const value=select.value;
    select.replaceChildren(h('option',{value},t('pm_kanban_manual_original_missing')));result.replaceChildren();
    editor.box.querySelector('.pm-receipt')?.replaceChildren();editor.update();}
  return {box,update,withdraw};
}
