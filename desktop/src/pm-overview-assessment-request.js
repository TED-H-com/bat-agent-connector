import {structuredAnalysis} from './pm-structured-analysis.js';
import {timeLabel, timeStamp} from './ui/stamps.js';
import {retiredView, viewLeft} from './pm-retired.js';

const ACTION = 'pm.overview.assess';
const validThread = id => typeof id === 'string' && /^pmt_[0-9a-f]{32}$/.test(id);
// This pointer exists only between the original create receipt and assessment.
// Fresh carrier selection always comes from central assessment history.
export function overviewAssessmentRequest(options) {
  const {h,t,api,alive,caps,workspaceId,namespace,form,input,labelled,permitted,errorBox,readLatest,onSaved} = options;
  const root = `/pm/workspaces/${encodeURIComponent(workspaceId)}`, ns = `${namespace}.overview-assessment.${workspaceId}`;
  const actor = caps()?.actor, pointerKey = `batc.pm.${ns}.carrier`;
  let serial = 0, request = null, capability = null, thread = null, preparing = false;
  const status = h('div',{role:'status'}), body = h('div'), element = h('div',{'data-overview-assessment-request':''},status,body);
  const authorized = () => permitted(false) && caps()?.actor === actor && caps()?.scopes?.includes('start');
  const current = ticket => {alive(); return ticket === serial && caps()?.actor === actor;};
  const hidden = value => h('input',{type:'hidden',value:String(value)});
  const mismatch = () => Error(t('poa_mismatch'));
  function pending() {
    const prefix = `batc.pm.${ns}.`, found = [];
    let count = 0;
    try {
      for(let index=0;index<localStorage.length;index++) {
        const key = localStorage.key(index);
        if(!key?.startsWith(prefix) || key === pointerKey) continue;
        if(++count > 64) throw Error();
        const raw = localStorage.getItem(key); if(!raw || raw.length > 65536) throw Error();
        const value = JSON.parse(raw);
        if(value?.version !== 1 || !value.values || typeof value.values !== 'object' || Array.isArray(value.values)
          || Object.values(value.values).some(item => typeof item !== 'string')) throw Error();
        if(!value.intent) continue;
        const original = value.intent.request, target = original?.target;
        if(![ACTION,'pm.thread.create'].includes(original?.action) || target?.workspace_id !== workspaceId
          || key !== prefix + original.action + '.' + JSON.stringify(target)
          || typeof value.intent.key !== 'string' || !value.intent.key
          || (original.action === ACTION ? !validThread(target.thread_id) || Object.keys(target).length !== 2 : Object.keys(target).length !== 1)) throw Error();
        found.push({action:original.action,thread_id:target.thread_id,values:value.values});
      }
      if(found.length > 1) throw Error();
      return found[0];
    } catch {throw Error(t('poa_pending_damaged'));}
  }
  function clear() {serial++; preparing=false; request?.dispose(); request=null; capability=null;thread=null;body.replaceChildren();status.replaceChildren();}
  function fail(error,ticket) {
    if(retiredView(error) || viewLeft(alive) || !current(ticket)) return;
    request?.dispose();request=null;thread=null;body.replaceChildren();status.replaceChildren(errorBox(error,{within:status}));
  }
  function runtime() {
    return structuredAnalysis({h,t,labelled,action:ACTION,capability:()=>capability,authorized,
      legacyEligible:()=>false});
  }
  async function carrierPointer() {
    const raw=localStorage.getItem(pointerKey);if(raw===null)return null;
    let pointer;
    try {pointer=JSON.parse(raw);if(pointer.version!==1 || pointer.actor!==actor || pointer.workspace_id!==workspaceId
      || !validThread(pointer.thread_id) || !/^op_[0-9a-f]{32}$/.test(pointer.operation_id))throw Error();}
    catch {throw Error(t('poa_pending_damaged'));}
    const value=await api('GET',`/operations/${pointer.operation_id}`), op=value.operation;
    if(op?.operation_id!==pointer.operation_id || op.actor!==actor || op.action!=='pm.thread.create'
      || op.target?.workspace_id!==workspaceId || op.status!=='succeeded' || op.result?.thread_id!==pointer.thread_id)throw mismatch();
    return pointer.thread_id;
  }
  async function createCarrier(ticket) {
    const value=await api('GET',root);if(!current(ticket))return;
    const workspace=value.workspace;
    if(workspace?.workspace_id!==workspaceId || !Number.isSafeInteger(workspace.version))throw mismatch();
    const title=hidden(t('poa_title')),version=hidden(workspace.version),refs=hidden(JSON.stringify(options.refs()));
    request=form({namespace:ns,action:'pm.thread.create',target:{workspace_id:workspaceId},
      fields:{title,_version:version,_refs:refs},params:values=>({title:values.title,context:JSON.parse(values._refs)}),
      preconditions:()=>({expected_version:Number(version.value)}),permitted:()=>authorized()&&capability?.overview_assessment?.eligible_for_preflight===true,
      recoverPermitted:authorized,label:'poa_create',
      extra:h('p',{class:'ui-meta'},t('poa_create_help')),
      onBeforeSaved:result=>{
        if(!validThread(result?.thread_id))throw mismatch();
        const op=request.operation();
        localStorage.setItem(pointerKey,JSON.stringify({version:1,actor,workspace_id:workspaceId,thread_id:result.thread_id,operation_id:op.operation_id}));
      },
      onSaved:()=>{queueMicrotask(()=>void prepare(true));},
    });
    body.replaceChildren(request.box);request.update();
  }
  async function assessment(ticket,id,preserved) {
    const page=await api('GET',`${root}/threads/${encodeURIComponent(id)}`);if(!current(ticket))return;
    if(page.thread?.thread_id!==id || page.thread.workspace_id!==workspaceId || !Number.isSafeInteger(page.thread.version)
      || !['idea','planning'].includes(page.thread.mode))throw mismatch();
    thread=page;
    // An uncertain intent is recovered as saved; even its preview must not be replaced.
    let preview=null;
    if(!preserved){preview=await api('GET',`${root}/threads/${encodeURIComponent(id)}/context-bundle?kind=overview_assessment`);if(!current(ticket))return;
      if(preview.workspace_id!==workspaceId || preview.thread_id!==id || preview.schema!=='pm_overview_bundle_v1'
        || preview.selector?.schema!==preview.schema || !/^[0-9a-f]{64}$/.test(preview.selector?.projection_digest)
        || preview.context?.thread_version!==page.thread.version || preview.calls_provider!==false)throw mismatch();}
    const adapter=runtime(),model=input('pm_native_model',{required:false,max:128});model.placeholder=t('pm_native_default_model');
    const version=hidden(page.thread.version),selector=hidden(JSON.stringify(preview?.selector||{}));
    const limits=preview?.limits||capability.overview_assessment.limits;
    const canAssess=()=>authorized()&&thread?.capabilities?.write===true&&adapter.eligible()&&limits?.allowed===true;
    const extra=h('div',{class:'pm-fields'},adapter.box,
      h('details',{},h('summary',{},t('pm_fa_model_optional')),labelled('pm_native_model',model)),
      h('p',{class:'ui-meta'},t('poa_cost')),
      limits?.allowed===false&&Number.isFinite(limits.retry_at)?h('p',{class:'ui-meta'},timeLabel(t,'poa_rate',{date:timeStamp(limits.retry_at)})):null);
    request=form({namespace:ns,action:ACTION,target:{workspace_id:workspaceId,thread_id:id},
      fields:{adapter:adapter.field,model,_version:version,_selector:selector},
      params:values=>({adapter:values.adapter,context_bundle_ref:JSON.parse(values._selector),...(values.model.trim()?{model:values.model.trim()}:{})}),
      preconditions:()=>({expected_version:Number(version.value)}),permitted:canAssess,recoverPermitted:authorized,
      label:'poa_assess',extra,onStateChange:()=>adapter.update(request),
      onBeforeSaved:()=>localStorage.removeItem(pointerKey),
      onSaved:async()=>{await onSaved();},
    });
    body.replaceChildren(request.box);request.update();
  }
  async function prepare(replace=false) {
    if(preparing || request?.locked()&&!replace){request?.recheck();return;}
    const ticket=++serial;preparing=true;request?.dispose();request=null;
    body.replaceChildren();status.replaceChildren(h('p',{class:'ui-meta'},t('poa_loading')));
    try {
      capability=await api('GET',`${root}/capabilities`);if(!current(ticket))return;
      if(capability.workspace_id!==workspaceId || capability.overview_assessment?.version!==1)throw mismatch();
      const saved=pending();
      if(saved?.action==='pm.thread.create')await createCarrier(ticket);
      else {
        let id=saved?.thread_id;
        if(!id)id=await carrierPointer();if(!current(ticket))return;
        if(!id){const row=await readLatest();if(!current(ticket))return;id=row?.thread_id;}
        if(id)await assessment(ticket,id,saved);
        else await createCarrier(ticket);
      }
      if(current(ticket))status.replaceChildren();
    }catch(error){fail(error,ticket);}finally{if(ticket===serial)preparing=false;}
  }
  return {element,prepare,clear,locked:()=>Boolean(request?.locked()),recheck:()=>request?.recheck()};
}
