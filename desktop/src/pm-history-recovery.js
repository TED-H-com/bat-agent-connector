import {historyRecords, historyRecord, historyDraftNamespace} from './pm-history-records.js';

const action = 'pm.history.unlink', separator = `.${action}.`, limit = 500;
const object = value => value !== null && typeof value === 'object'
  && [Object.prototype,null].includes(Object.getPrototypeOf(value));
const exact = (value, keys) => object(value) && Object.keys(value).sort().join(',') === [...keys].sort().join(',');
const same = (a, b) => a === b || (object(a) && object(b) && Object.keys(a).length === Object.keys(b).length
  && Object.keys(a).every(key=>Object.hasOwn(b,key) && same(a[key],b[key])));
const invalid = () => Object.assign(Error('HISTORY_RECOVERY_INVALID'), {code:'HISTORY_RECOVERY_INVALID'});
const changed = () => Object.assign(Error('HISTORY_RECOVERY_CHANGED'), {code:'HISTORY_RECOVERY_CHANGED'});
const full = () => Object.assign(Error('HISTORY_RECOVERY_FULL'), {code:'HISTORY_RECOVERY_FULL'});
const id = (value, pattern) => typeof value === 'string' && pattern.test(value);
const version = value => Number.isSafeInteger(value) && value > 0;
const sameIntent = (a,b) => a && b && a.key === b.key && same(a.request,b.request)
  && (!a.operation_id || !b.operation_id || a.operation_id === b.operation_id);
function targetOf(row, workspaceId) {
  const target = {workspace_id:workspaceId,work_item_id:row?.work_item_id,link_id:row?.link_id};
  if (row?.workspace_id !== undefined && row.workspace_id !== workspaceId
    || !id(workspaceId,/^ws_[0-9a-f]{32}$/) || !id(target.work_item_id,/^wi_[0-9a-f]{20}$/)
    || !id(target.link_id,/^hxl_[0-9a-f]{32}$/)) throw invalid();
  return target;
}
function envelope(saved, target) {
  if (!exact(saved, saved?.intent === undefined ? ['version','values'] : ['version','values','intent'])
    || saved.version !== 1 || !exact(saved.values,[])) throw invalid();
  if (saved.intent === undefined) return;
  const intent = saved.intent, request = intent?.request;
  if (!object(intent) || Object.keys(intent).some(key=>!['key','request','operation_id','initialAdmissionRefused'].includes(key))
    || typeof intent.key !== 'string' || !intent.key.trim() || intent.key.trim().length > 200
    || Object.hasOwn(intent,'operation_id') && !id(intent.operation_id,/^op_[0-9a-f]{32}$/)
    || Object.hasOwn(intent,'initialAdmissionRefused') && typeof intent.initialAdmissionRefused !== 'boolean'
    || !exact(request,['action','target','params','preconditions']) || request.action !== action
    || !exact(request.target,['workspace_id','work_item_id','link_id']) || !same(request.target,target)
    || !exact(request.params,[]) || !exact(request.preconditions,['expected_link_version'])
    || !version(request.preconditions.expected_link_version)) throw invalid();
}

// Read-only resolution. Discovery is never written into the seven-field index.
// The only new durable record is the ordinary form envelope, before its POST.
export function historyRecovery(storage, namespace, workspaceId) {
  if (typeof namespace !== 'string' || !namespace) throw invalid();
  targetOf({work_item_id:'wi_'+'0'.repeat(20),link_id:'hxl_'+'0'.repeat(32)},workspaceId);
  const records = historyRecords(storage,namespace,workspaceId);
  const cleanupNamespace = `${namespace}.history-cleanup.${workspaceId}`;
  const cleanupPrefix = `batc.pm.${cleanupNamespace}.`, legacyPrefix = `batc.pm.${namespace}.history.`;
  function descriptor(row, draftNamespace, target = targetOf(row,workspaceId), intent = null) {
    return {row,namespace:draftNamespace,target,intent,known:false,sourceIndependent:draftNamespace === cleanupNamespace,
      storageKey:`batc.pm.${draftNamespace}${separator}${JSON.stringify(target)}`};
  }
  function read() {
    const byId = new Map(records.read().map(row=>[row.link_id,{...descriptor(row,historyDraftNamespace(namespace,row)),known:true}]));
    const seen = new Set(), keys = new Set();
    const count = storage.length;
    if (!Number.isSafeInteger(count) || count < 0) throw invalid();
    for (let i = 0; i < count; i++) {
      const key = storage.key(i);
      if (typeof key !== 'string') throw invalid();
      keys.add(key);
    }
    let pendingCount = 0;
    for (const key of keys) {
      const independent = key.startsWith(cleanupPrefix);
      if (!independent && !key.startsWith(legacyPrefix)) continue;
      const legacyName = !independent && key.slice(legacyPrefix.length).match(/^(\["[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}","shi_[0-9a-f]{32}"\])(.*)$/);
      if (legacyName && !legacyName[2].startsWith(separator)) continue;
      const split = key.lastIndexOf(separator);
      if (split < 0) {if (independent) throw invalid(); else continue;}
      const target = JSON.parse(key.slice(split + separator.length));
      if (!id(target?.workspace_id,/^ws_[0-9a-f]{32}$/)) throw invalid();
      if (target.workspace_id !== workspaceId) {if (independent) throw invalid(); else continue;}
      if (!exact(target,['workspace_id','work_item_id','link_id']) || !same(target,targetOf(target,workspaceId))
        || JSON.stringify(target) !== key.slice(split + separator.length)) throw invalid();
      const draftNamespace = key.slice('batc.pm.'.length,split);
      let source = {};
      if (independent) {if (draftNamespace !== cleanupNamespace) throw invalid();}
      else {
        const encoded = key.slice(legacyPrefix.length,split), identity = JSON.parse(encoded);
        if (!Array.isArray(identity) || identity.length !== 2 || JSON.stringify(identity) !== encoded
          || !id(identity[0],/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/) || !id(identity[1],/^shi_[0-9a-f]{32}$/)) throw invalid();
        source = {source_id:identity[0],entry_id:identity[1]};
      }
      const raw = storage.getItem(key);
      if (raw === null) continue;
      const saved = JSON.parse(raw); envelope(saved,target);
      if (!saved.intent) continue;
      if (seen.has(target.link_id) || independent && ++pendingCount > limit) throw invalid();
      seen.add(target.link_id);
      const known = byId.get(target.link_id);
      if (known && known.row.work_item_id !== target.work_item_id) throw invalid();
      const row = {...known?.row,...source,...target,version:saved.intent.request.preconditions.expected_link_version};
      byId.set(target.link_id,{...descriptor(row,draftNamespace,target,saved.intent),known:Boolean(known?.known)});
    }
    return [...byId.values()];
  }
  function resolve(value, {reviewVersion = false} = {}) {
    const target = targetOf(value,workspaceId);
    if (!version(value?.version)) throw invalid();
    const legacy = value.source_id !== undefined || value.entry_id !== undefined || value.fixed_ref !== undefined;
    if (legacy && (!id(value.source_id ?? value.fixed_ref?.source_id,/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/)
      || !id(value.entry_id ?? value.fixed_ref?.entry_id,/^shi_[0-9a-f]{32}$/))) throw invalid();
    if (!legacy && !exact(value,Object.hasOwn(value,'workspace_id') ? ['workspace_id','work_item_id','link_id','version'] : ['work_item_id','link_id','version'])) throw invalid();
    const current = read().find(item=>item.row.link_id === target.link_id);
    if (current) {
      if (current.row.work_item_id !== target.work_item_id) throw invalid();
      return reviewVersion && !current.intent ? {...current,row:{...current.row,version:value.version}} : current;
    }
    if (legacy) {
      const row = historyRecord({...value,workspace_id:workspaceId});
      return descriptor(row,historyDraftNamespace(namespace,row));
    }
    return descriptor({...target,version:value.version},cleanupNamespace);
  }
  function inspect(selected, owned, capacity = true) {
    const rows = read(), current = rows.find(item=>item.row.link_id === selected.row.link_id);
    if (current && current.row.work_item_id !== selected.row.work_item_id) throw invalid();
    if (current?.intent && !sameIntent(current.intent,owned)) throw owned ? invalid() : changed();
    if (current && current.storageKey !== selected.storageKey && (!owned || current.intent)) throw changed();
    if (capacity && selected.sourceIndependent && !current?.intent
      && rows.filter(item=>item.sourceIndependent && item.intent).length >= limit) throw full();
    return current;
  }
  return {read,resolve,admit:inspect,
    beforePersist(selected, saved, storageKey, owned = saved.intent) {
      if (storageKey !== selected.storageKey) throw invalid();
      envelope(saved,selected.target);
      inspect(selected,owned,Boolean(saved.intent));
      if (saved.intent && saved.intent.request.preconditions.expected_link_version !== selected.row.version) throw invalid();
    },
  };
}

// One form owner per row. If an intent appeared since rendering, remount that
// exact original instead of allocating another key. Remounting never sends POST.
export function historyWithdrawalForm({h,t,form,recovery,row,permitted,recoverPermitted = permitted,
  beforeNew = () => {}, onResolve = () => {}, onError = () => false, onSaved = () => {}, errorBox, reviewVersion = false, ...options}) {
  const box = h('div'), notice = h('div', {role:'status'});
  let editor, selected, owned, disposed = false, queued = false, completed = false, needsRead = false, lastError = null;
  function report(error) {
    if (lastError === error.code) return;
    lastError = error.code;
    const key = error.code === 'HISTORY_RECOVERY_FULL' ? 'full' : 'invalid';
    notice.replaceChildren(errorBox(Error(t(selected?.sourceIndependent ? `exh_cleanup_recovery_${key}` : key === 'full' ? 'exh_cleanup_limit' : 'exh_cleanup_invalid')), {within:notice,sourceRefresh:false}));
  }
  function remount() {
    if (queued || disposed) return;
    queued = true;
    queueMicrotask(()=>{queued = false; if (!disposed) {try {mount();} catch (error) {report(error);}}});
  }
  function ready(recovering) {
    if (disposed || completed || !(recovering ? recoverPermitted() : permitted())) return false;
    try {
      const stored = recovery.admit(selected,owned);
      // A failed removeItem must keep the original owner; remounting its known
      // receipt here would restart automatic GET recovery on every failed clear.
      if (owned && !stored?.intent && editor && !editor.intentRequest()) owned = null;
      return Boolean(owned) || !needsRead;
    } catch (error) {
      if (error.code === 'HISTORY_RECOVERY_CHANGED' && !owned) remount(); else report(error);
      return false;
    }
  }
  function mount() {
    const next = recovery.resolve(row,{reviewVersion});
    editor?.dispose(); selected = next; owned = next.intent; needsRead = Boolean(owned?.initialAdmissionRefused);
    notice.replaceChildren(); lastError = null; onResolve(next);
    editor = form({...options,namespace:next.namespace,action,target:next.target,fields:{},params:()=>({}),
      pruneEmpty:next.sourceIndependent,
      preconditions:()=>{recovery.admit(selected,owned); beforeNew(); return {expected_link_version:selected.row.version};},
      permitted:()=>ready(false),recoverPermitted:()=>ready(true),
      beforePersist:(saved,key)=>{recovery.beforePersist(selected,saved,key,owned); if (saved.intent) owned = structuredClone(saved.intent);},
      onError:error=>{
        if (['PM_HISTORY_LINK_CHANGED','VERSION_CONFLICT'].includes(error.code)) needsRead = true;
        if (typeof error.code === 'string' && error.code.startsWith('HISTORY_RECOVERY_')) {report(error); return true;}
        return onError(error);
      },
      onSaved:async result=>{completed = true; return onSaved(result);}});
    box.replaceChildren(editor.box,notice);
  }
  mount();
  return {box,update:()=>editor.update(),checking:()=>editor.checking(),intentRequest:()=>editor.intentRequest(),
    locked:()=>editor.locked(),operation:()=>editor.operation(),
    dispose:()=>{disposed = true; editor.dispose();}};
}
