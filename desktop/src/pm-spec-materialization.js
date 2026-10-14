import {iconButton} from './ui/components.js';
import {joinLabel} from './label-text.js';
const id=/^pmsp_[0-9a-f]{32}$/,digest=/^[0-9a-f]{64}$/,sha=/^[0-9a-f]{40}$/;
const artifactRef=row=>Object.fromEntries(['artifact_id','revision','digest'].map(key=>[key,row.artifact_ref[key]]));

// Both the spec panel and implementation handoff require the same original receipt.
export function checkSpecMaterialization(value,workspaceId,selected,{full=false,original=null}={}) {
  const fail=()=>{throw Error('PM_SPEC_RESPONSE_MISMATCH');},row=value;
  if(!row||row.workspace_id!==workspaceId||row.package_id!==selected.package_id||!id.test(row.materialization_id)
    ||!digest.test(row.document_digest)||!/^op_[0-9a-f]{32}$/.test(row.operation_id)
    ||!/^art_[0-9a-f]{32}$/.test(row.artifact_ref?.artifact_id)||!Number.isSafeInteger(row.artifact_ref.revision)||row.artifact_ref.revision<1
    ||row.artifact_ref.digest!==row.document_digest)fail();
  const receipt=row.git_receipt;
  if(receipt?.status!=='verified'||receipt.workspace_id!==workspaceId||receipt.revision_id!==row.materialization_id
    ||receipt.package_id!==row.package_id||receipt.operation_id!==row.operation_id||receipt.artifact_kind!=='pm-spec'
    ||!sha.test(receipt.commit_sha)||!sha.test(receipt.tree_sha)||!digest.test(receipt.document_digest)
    ||!receipt.file_digests||typeof receipt.file_digests!=='object'||!Object.keys(receipt.file_digests).length
    ||Object.values(receipt.file_digests).some(value=>!digest.test(value)))fail();
  if(original&&(JSON.stringify(fixedSpecSelection(row,workspaceId))!==JSON.stringify(fixedSpecSelection(original,workspaceId))
    ||row.operation_id!==original.operation_id||receipt.commit_sha!==original.git_receipt.commit_sha
    ||receipt.document_digest!==original.git_receipt.document_digest))fail();
  if(full){
    const doc=row.document,ref=doc?.package_ref;
    if(doc?.schema!=='batc.pm.spec-materialization/v1'||doc.workspace_id!==workspaceId
      ||ref?.workspace_id!==workspaceId||ref.package_id!==selected.package_id||ref.document_digest!==selected.document_digest
      ||doc.package?.package_id!==selected.package_id||doc.package.workspace_id!==workspaceId
      ||doc.verification!=='unassessed'||doc.delivery!=='not_observed'||doc.layout!=='connector_owned_revision_adapter'
      ||doc.openspec_cli_compatibility!=='not_claimed'||doc.change_id!==receipt.change_id
      ||!Array.isArray(doc.tasks)||!Array.isArray(doc.source_chain))fail();
    const seen=new Set();for(const task of doc.tasks){
      if(!/^pmt_[0-9a-f]{32}$/.test(task.task_id)||typeof task.key!=='string'||seen.has(task.task_id)
        ||!['active','removed'].includes(task.state)||!Array.isArray(task.history))fail();seen.add(task.task_id);
    }
  }
  return row;
}
export async function readSpecMaterializations(api,workspaceId,selected,valid=()=>true){
  let after=0,filtered=false;const rows=[],seen=new Set(),path=`/pm/workspaces/${encodeURIComponent(workspaceId)}/spec-materializations`;
  for(let page=0;page<20;page++){
    const data=await api('GET',`${path}?package_id=${encodeURIComponent(selected.package_id)}&after=${after}&limit=50`);if(!valid())return null;
    if(data?.version!==1||data.workspace_id!==workspaceId||!Array.isArray(data.materializations)||typeof data.source_visibility_filtered!=='boolean'
      ||data.next_after!==null&&(!Number.isSafeInteger(data.next_after)||data.next_after<=after))throw Error('PM_SPEC_RESPONSE_MISMATCH');
    for(const row of data.materializations){checkSpecMaterialization(row,workspaceId,selected);if(seen.has(row.materialization_id))throw Error('PM_SPEC_RESPONSE_MISMATCH');seen.add(row.materialization_id);rows.push(row);}
    filtered ||= data.source_visibility_filtered;if(data.next_after===null)return {rows,filtered};after=data.next_after;
  }
  throw Error('PM_SPEC_HISTORY_BOUND');
}
export function fixedSpecSelection(row,workspaceId){
  return {pm_spec_ref:{workspace_id:workspaceId,materialization_id:row.materialization_id,document_digest:row.document_digest},artifacts:[artifactRef(row)]};
}

export function workspaceSpecMaterialization({h,t,api,alive,workspaceId,workspace,caps,permitted,form,errorBox,namespace,onSourceFailure=()=>{}}){
  const endpoint=`/pm/workspaces/${encodeURIComponent(workspaceId)}`,action='pm.spec.materialize';
  let selected=null,serial=0,ready=false,record=null,eligible=false,editor=null;
  const editors=new Map(),facts=h('div'),status=h('div',{role:'status'}),editorView=h('div'),summary=h('p',{class:'muted'});
  const refreshButton=iconButton({icon:'refresh-cw', label:t('psm_refresh'), ...{type:'button',class:'secondary',onclick:()=>void refresh()}});
  const element=h('section',{class:'pm-packages','data-pm-spec-materialization':''},h('details',{},h('summary',{},t('psm_title')),
    h('p',{class:'muted'},t('psm_intro')),summary,refreshButton,status,facts,editorView));
  const cap=()=>caps()?.features?.pm_workspaces?.spec_materialization;
  const supported=()=>cap()?.version===1&&cap().action===action&&cap().immutable===true&&cap().owned_git_chain===true;
  const valid=ticket=>{try{alive();return serial===ticket;}catch{return false;}};
  const writer=()=>supported()&&permitted(false)&&['owner','contributor'].includes(workspace()?.membership?.role)
    &&['observe','manage','start'].every(scope=>caps()?.scopes?.includes(scope));
  const text=key=>h('p',{class:'muted'},t(key));
  const line=(key,value)=>h('p',{class:'pm-source'},h('strong',{},t(key)+': '),String(value));
  const detail=(key,...nodes)=>h('details',{},h('summary',{},t(key)),...nodes);
  const op=value=>h('a',{href:`#/op/${encodeURIComponent(value)}`},t('pm_operation'));
  function update(){
    refreshButton.disabled=!selected;summary.textContent=selected?joinLabel([selected.document.title, joinLabel([t('ph_preparation'), selected.sequence], ' ')]):t('psm_select');
    for(const value of editors.values())value.update();
  }
  function clear(){ready=false;eligible=false;record=null;facts.replaceChildren();status.replaceChildren();editorView.hidden=true;update();}
  function withdraw(){serial++;selected=null;clear();editorView.replaceChildren();editor=null;}
  function fail(error){clear();status.replaceChildren(errorBox(error,{summary:t(error.message?.startsWith('PM_SPEC_')?'psm_invalid':'psm_unavailable')}));
    if([401,403,404].includes(error.status)){selected=null;editorView.replaceChildren();editor=null;onSourceFailure();update();}}
  function bind(row){
    if(!editors.has(row.package_id)){
      const guardedApi=async(...args)=>{const ticket=serial;try{return await api(...args);}catch(error){if(valid(ticket)&&[401,403,404].includes(error.status))fail(error);throw error;}};
      const value=form({namespace:`${namespace}.spec-materialization.${row.package_id}`,api:guardedApi,action,target:{workspace_id:workspaceId,package_id:row.package_id},fields:{},params:()=>({}),
        preconditions:()=>({expected_package_digest:row.document_digest,expected_scope_revision:row.document.context.plan_ref.scope_revision}),
        permitted:()=>ready&&eligible&&!record&&selected?.package_id===row.package_id&&writer(),
        recoverPermitted:()=>ready&&selected?.package_id===row.package_id&&writer(),label:'psm_create',extra:text('psm_boundary'),
        onSaved:async result=>{
          if(result?.status!=='materialized'||result.workspace_id!==workspaceId||result.package_id!==row.package_id||result.execution_authorized!==false
            ||!id.test(result.materialization_id))throw Error(t('psm_invalid'));
          await refresh();
        }});editors.set(row.package_id,value);
    }
    editor=editors.get(row.package_id);editorView.replaceChildren(editor.box);update();
  }
  function render(row){
    const doc=row.document,receipt=row.git_receipt,ref=row.artifact_ref;
    const tasks=doc.tasks.map(task=>h('details',{},h('summary',{},task.key),line('psm_task_key',task.key),line('psm_task_id',task.task_id),
      text(task.state==='active'?'psm_active':'psm_removed'),...task.history.map(history=>h('div',{class:'pm-fact'},
        line('wp_package_id',history.package_id),text(history.state==='active'?'psm_active':'psm_removed'),
        history.task?h('p',{class:'pm-note-text'},history.task.description):null))));
    facts.replaceChildren(h('h4',{},t('psm_saved')),text('psm_unassessed'),line('psm_commit',receipt.commit_sha),
      h('p',{},h('a',{href:`#/artifact-review/artifact/${encodeURIComponent(ref.artifact_id)}/${ref.revision}`},t('psm_open_artifact'))),
      detail('psm_tasks',text('psm_task_help'),...tasks),detail('psm_proof',line('psm_identity',row.materialization_id),line('wp_package_id',row.package_id),
        line('psm_artifact',ref.artifact_id),line('psm_revision',ref.revision),line('psm_artifact_digest',row.document_digest),line('psm_files_digest',receipt.document_digest),
        ...Object.entries(receipt.file_digests).map(([path,hash])=>h('p',{class:'pm-source'},path,' · ',hash)),op(row.operation_id)));
  }
  async function refresh(){
    if(!selected)return;const row=selected,ticket=++serial;clear();status.replaceChildren(text('psm_reading'));
    try{
      if(!supported())throw Error(t('psm_not_supported'));
      const [pkg,readiness,history]=await Promise.all([api('GET',`${endpoint}/work-packages/${row.package_id}`),api('GET',`${endpoint}/work-packages/${row.package_id}/readiness`),
        readSpecMaterializations(api,workspaceId,row,()=>valid(ticket))]);if(!valid(ticket))return;
      if(pkg?.version!==1||pkg.workspace_id!==workspaceId||pkg.package?.package_id!==row.package_id||pkg.package.document_digest!==row.document_digest
        ||pkg.package.document.context.plan_ref.scope_revision!==row.document.context.plan_ref.scope_revision
        ||readiness?.version!==1||readiness.workspace_id!==workspaceId||readiness.package_id!==row.package_id||readiness.document_digest!==row.document_digest
        ||readiness.execution_authorized!==false||!Array.isArray(readiness.checks))throw Error(t('psm_invalid'));
      eligible=!history.filtered&&readiness.checks.every(check=>check.status==='passed'||check.check==='spec_code_alignment');
      if(history.rows.length>1)throw Error(t('psm_invalid'));
      if(history.rows.length){const data=await api('GET',`${endpoint}/spec-materializations/${history.rows[0].materialization_id}`);if(!valid(ticket))return;
        if(data?.version!==1||data.workspace_id!==workspaceId||data.materialization?.materialization_id!==history.rows[0].materialization_id
          ||data.materialization.document_digest!==history.rows[0].document_digest)throw Error(t('psm_invalid'));
        record=checkSpecMaterialization(data.materialization,workspaceId,row,{full:true,original:history.rows[0]});render(record);
      }
      ready=true;editorView.hidden=false;status.replaceChildren();
      if(history.filtered)status.append(text('psm_filtered'));else if(!record)status.append(text(eligible?'psm_empty':'psm_blocked'));
      if(!writer())status.append(text('psm_permissions'));bind(row);update();
    }catch(error){if(valid(ticket))fail(error);}
  }
  async function setPackage(row){
    withdraw();if(!row)return;
    try{
      if(row.workspace_id!==workspaceId||!/^pmwp_[0-9a-f]{32}$/.test(row.package_id)||!digest.test(row.document_digest)
        ||row.document?.workspace_id!==workspaceId||row.document.package_id!==row.package_id
        ||!/^pmscope_[0-9a-f]{32}$/.test(row.document.context?.plan_ref?.scope_revision))throw Error(t('psm_invalid'));
      selected=row;bind(row);await refresh();
    }catch(error){fail(error);}
  }
  update();return {element,setPackage,refresh,withdraw};
}
