import {button, inlineError} from './ui/components.js';
import {workHistoryPanel} from './pm-work-history.js';

// A Work record has no workspace identity. Navigation context is only a hint;
// central must prove the current workspace and exact Work binding before reading.
export function workItemHistory({h, t, api, alive, caps, workId, workspaceHint = null, visibilityRoot = null}) {
  let disposed = false, ready = false, serial = 0, record = null, selected = workspaceHint || '';
  let reader = null, card = null;
  const supported = () => caps()?.features?.external_history?.relationships?.version === 1
    && caps()?.features?.external_history?.relationships?.refs?.includes('external_history_ref/v1')
    && caps()?.features?.pm_workspaces?.kanban && caps()?.scopes?.includes('observe');
  const status = h('div', {role:'status'}), body = h('div');
  const select = h('select', {'data-history-workspace':'', 'aria-label':t('exh_workspace'), onchange:()=>{
    selected = select.value; reset(); if (ready) void load();
  }});
  const retry = button({variant:'secondary', size:'sm', label:t('wv_retry'), onclick:()=>void load()});
  const disclosure = h('details', {'data-work-item-history-reader':''}, h('summary', {}, t('exh_read_links')),
    h('p', {class:'ui-meta'}, t('exh_workspace_help')), h('label', {}, t('exh_workspace'), select), status, body);
  const element = h('section', {class:'panel', 'data-work-item-history':'', hidden:true}, h('h3', {}, t('exh_title')), disclosure);
  const current = ticket => {
    alive();
    if (disposed || !ready || !disclosure.open || !supported() || ticket !== serial
      || visibilityRoot && (visibilityRoot.hidden || !visibilityRoot.isConnected)) throw Error(t('exh_unavailable'));
  };
  function reset() {serial++; reader?.dispose(); reader = null; card = null; body.replaceChildren(); status.replaceChildren();}
  async function proof(workspaceId, ticket, expected = null) {
    current(ticket);
    const data = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}`); current(ticket);
    const workspace = data.workspace;
    if (workspace?.workspace_id !== workspaceId || typeof workspace.server_id !== 'string' || !workspace.server_id
      || !['owner','contributor','reader'].includes(workspace.membership?.role)
      || expected && workspace.server_id !== expected.serverId) throw Error(t('pm_scope_mismatch'));
    const result = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/kanban/${encodeURIComponent(workId)}`); current(ticket);
    const value = result.card, projectId = record?.work_item.project_id;
    if (result.workspace_id !== workspaceId || value?.work_item_id !== workId || value.work?.work_item_id !== workId
      || value.work.project_id !== projectId || value.binding?.workspace_id !== workspaceId || value.binding.work_item_id !== workId
      || value.binding.project_id !== projectId || !/^pmwb_[0-9a-f]{32}$/.test(value.binding.binding_id)
      || expected && value.binding.binding_id !== expected.bindingId) throw Error(t('pm_scope_mismatch'));
    return {workspace, card:value};
  }
  async function load() {
    reset(); const ticket = serial;
    try {
      current(ticket); select.disabled = true;
      status.textContent = t('pm_kanban_detail_loading');
      const data = await api('GET', '/pm/workspaces'); current(ticket);
      if (!Array.isArray(data.workspaces) || data.workspaces.some(row=>!/^ws_[0-9a-f]{32}$/.test(row?.workspace_id) || typeof row.name !== 'string')
        || new Set(data.workspaces.map(row=>row.workspace_id)).size !== data.workspaces.length) throw Error(t('pm_scope_mismatch'));
      select.replaceChildren(h('option', {value:''}, t('exh_choose_workspace')),
        ...data.workspaces.map(row=>h('option', {value:row.workspace_id}, row.name)));
      select.value = data.workspaces.some(row=>row.workspace_id === selected) ? selected : '';
      if (!selected) {status.textContent = t('exh_choose_workspace'); return;}
      if (!select.value) throw Error(t('exh_unavailable'));
      let context = await proof(selected, ticket); current(ticket);
      const workspaceId = selected, expected = {serverId:context.workspace.server_id, bindingId:context.card.binding.binding_id};
      card = context.card;
      reader = workHistoryPanel({h, t, api, alive, caps, workspaceId, workspace:()=>context.workspace, embedded:true,
        validate:async()=>{
          // Use the current lifecycle ticket: parent safety refreshes suspend this
          // reader, then reauthorize it without losing the selected excerpt.
          const next = await proof(workspaceId, serial, expected);
          context = next;
        }});
      status.replaceChildren(); body.replaceChildren(reader.element); reader.setWork(card);
    } catch {
      try {current(ticket);} catch {return;}
      body.replaceChildren(); status.replaceChildren(inlineError({error:{message:t('exh_unavailable')}}), retry);
    } finally {if (ticket === serial) select.disabled = false;}
  }
  disclosure.addEventListener('toggle', ()=>{if (disclosure.open && ready) void load(); else reset();});
  // Linked-session Work lives in the existing Results inspector. Closing,
  // resizing or withdrawing that container must also retire its reader.
  const visibility = visibilityRoot ? new MutationObserver(records=>{
    if (!visibilityRoot.isConnected || visibilityRoot.hidden || records.some(row=>row.target === visibilityRoot && row.oldValue === null)) {
      disclosure.open = false; reset();
    }
  }) : null;
  if (visibility) {
    visibility.observe(visibilityRoot, {attributes:true, attributeFilter:['hidden'], attributeOldValue:true});
    const main = visibilityRoot.closest('main');
    if (main) visibility.observe(main, {childList:true});
  }
  function suspend() {serial++; ready = false; reader?.suspend(); status.replaceChildren();}
  function withdraw() {suspend(); reset(); record = null; element.hidden = true;}
  function setWork(value) {
    if (disposed) return;
    if (!/^wi_[0-9a-f]{20}$/.test(workId) || value?.work_item?.work_item_id !== workId || !/^prj_[0-9a-f]{20}$/.test(value.work_item.project_id)
      || value.project?.project_id !== value.work_item.project_id || !supported()) {withdraw(); return;}
    record = value; ready = true; element.hidden = false;
    if (disclosure.open) {if (reader) reader.setWork(card); else void load();}
  }
  return {element, setWork, suspend, withdraw,
    refresh() {if (ready && disclosure.open) {const value = record; suspend(); setWork(value);}},
    dispose() {visibility?.disconnect(); withdraw(); disposed = true;},
  };
}
