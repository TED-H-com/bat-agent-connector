import {button} from './ui/components.js';
import {historyRecords, historyRecord, historyDraftNamespace} from './pm-history-records.js';

// Known IDs are a local recovery index, never proof of current central authority.
export function historyCleanup({h, t, alive, workspaceId, workspace, caps, namespace, form, errorBox}) {
  const records = historyRecords(localStorage, namespace, workspaceId), forms = new Set();
  let active = false, disposed = false, count = 20, limited = false;
  const status = h('div', {role:'status'}), rows = h('div');
  const refresh = button({variant:'ghost', size:'sm', label:t('exh_cleanup_refresh'), onclick:draw});
  const more = button({variant:'secondary', size:'sm', label:t('exh_cleanup_more'), onclick:()=>{count += 20; draw();}});
  const element = h('details', {'data-history-cleanup':'', hidden:true}, h('summary', {}, t('exh_cleanup')),
    h('p', {class:'ui-meta'}, t('exh_cleanup_help')), status, rows, h('div', {class:'exh-actions'}, refresh, more));
  function supported(visible = true) {
    try {alive();} catch {return false;}
    return (active || !visible) && !disposed && workspace()?.workspace_id === workspaceId && ['owner','contributor','reader'].includes(workspace()?.membership?.role)
      && caps()?.features?.external_history?.relationships?.version === 1
      && caps()?.features?.external_history?.relationships?.actions?.includes('pm.history.unlink')
      && caps()?.scopes?.includes('manage') && caps()?.scopes?.includes('observe')
      && caps()?.actions?.some(row=>row.action === 'pm.history.unlink' && row.allowed === true);
  }
  const allowed = (row, visible = true) => supported(visible) && (workspace()?.membership?.role === 'owner' || row.created_by === caps()?.actor);
  const target = row => ({workspace_id:workspaceId,work_item_id:row.work_item_id,link_id:row.link_id});
  function legacyPending() {
    const prefix = `batc.pm.${namespace}.history.`, found = [];
    for (const key of Object.keys(localStorage)) {
      if (!key.startsWith(prefix) || !key.endsWith('.withdrawals')) continue;
      const identity = JSON.parse(key.slice(prefix.length, -'.withdrawals'.length));
      if (!Array.isArray(identity) || identity.length !== 2) throw Error(t('exh_cleanup_invalid'));
      const pending = JSON.parse(localStorage.getItem(key));
      if (!Array.isArray(pending) || pending.length > 500) throw Error(t('exh_cleanup_invalid'));
      for (const value of pending) {
        const row = historyRecord({...value,workspace_id:workspaceId,source_id:identity[0],entry_id:identity[1]});
        const saved = JSON.parse(localStorage.getItem(`batc.pm.${historyDraftNamespace(namespace,row)}.pm.history.unlink.${JSON.stringify(target(row))}`) || 'null');
        // Old indexes did not include workspace identity. Only an exact original
        // target in this workspace admits their recovery; never create from them.
        if (saved?.intent?.request?.action === 'pm.history.unlink'
          && JSON.stringify(saved.intent.request.target) === JSON.stringify(target(row))) found.push(row);
      }
    }
    return found;
  }
  function clearForms() {for (const value of forms) value.dispose(); forms.clear(); rows.replaceChildren(); refresh.disabled = more.disabled = false;}
  function remove(row) {
    records.remove(row.link_id);
    const key = `batc.pm.${historyDraftNamespace(namespace,row)}.withdrawals`;
    const pending = JSON.parse(localStorage.getItem(key) || '[]').filter(value=>value.link_id !== row.link_id);
    if (pending.length) localStorage.setItem(key,JSON.stringify(pending)); else localStorage.removeItem(key);
  }
  function draw() {
    if (!supported() || !element.open || [...forms].some(value=>value.checking())) return;
    clearForms(); status.replaceChildren(); more.hidden = true;
    try {
      const known = records.read(), byId = new Map(known.map(row=>[row.link_id,row]));
      for (const row of legacyPending()) if (!byId.has(row.link_id)) byId.set(row.link_id,row);
      const visible = [...byId.values()].filter(row=>allowed(row));
      for (const row of visible.slice(0,count)) {
        const isKnown = known.some(value=>value.link_id === row.link_id);
        const editor = form({namespace:historyDraftNamespace(namespace,row), action:'pm.history.unlink', target:target(row), fields:{}, params:()=>({}),
          preconditions:()=>({expected_link_version:row.version}), permitted:()=>isKnown && allowed(row), recoverPermitted:()=>allowed(row), label:'exh_unlink',
          onBeforeSaved:result=>{
            const version = editor.intentRequest()?.preconditions.expected_link_version;
            if (result?.link_id !== row.link_id || result.state !== 'withdrawn' || result.version !== version + 1) throw Error(t('pm_scope_mismatch'));
            remove(row);
          },
          onSaved:()=>{alive(); editor.box.hidden = true; rowElement.prepend(h('p', {role:'status'}, t('exh_cleanup_done')));},
          onStateChange:()=>{refresh.disabled = more.disabled = [...forms].some(value=>value.checking());}});
        const rowElement = h('div', {class:'exh-relation','data-history-cleanup-link':row.link_id},
          h('p', {class:'mono'}, row.link_id), h('a', {class:'mono',href:`#/item/${encodeURIComponent(row.work_item_id)}`},row.work_item_id),
          h('p', {class:'ui-meta'},t('exh_cleanup_version',{version:row.version})), editor.box);
        forms.add(editor); rows.append(rowElement); editor.update();
      }
      more.hidden = visible.length <= count;
      if (!visible.length) status.append(h('p', {class:'ui-meta'},t('exh_cleanup_empty')));
      if (limited || known.length >= 500) status.append(h('p', {class:'ui-meta'},t('exh_cleanup_limit')));
    } catch (error) {clearForms(); status.replaceChildren(errorBox(error,{summary:t('exh_cleanup_invalid'),within:status}));}
  }
  element.addEventListener('toggle',()=>{if (element.open) draw(); else {clearForms(); status.replaceChildren(); count = 20;}});
  return {element,
    remember(link) {
      if (!allowed(link,false)) return;
      try {if (!records.remember(link)) limited = true;} catch {limited = true;}
    },
    remove(linkId) {const row = records.read().find(value=>value.link_id === linkId); if (row) remove(row);},
    setActive(value) {
      active = value; element.hidden = !supported();
      if (element.hidden) {clearForms(); status.replaceChildren(); element.open = false;}
      else if (element.open) {for (const editor of forms) editor.update();}
    },
    withdraw() {active = false; clearForms(); status.replaceChildren(); element.open = false; element.hidden = true;},
    dispose() {disposed = true; this.withdraw();},
  };
}
