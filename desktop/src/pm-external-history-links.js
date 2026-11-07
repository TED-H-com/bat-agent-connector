import {button} from './ui/components.js';

const workId = value => /^wi_[0-9a-f]{20}$/.test(value || '');
const digest = value => /^[0-9a-f]{64}$/.test(value || '');
// Relationships are records in the Connector. Only selection/recovery metadata
// and the shared form's original intent persist locally; preview text never does.
export function externalHistoryLinks({h, t, api, alive, workspaceId, workspace, caps, namespace, permitted, form, errorBox, sourceId, entry, onUnavailable}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  const identity = JSON.stringify([sourceId, entry.entry_id]);
  const draftNamespace = `${namespace}.history.${identity}`;
  const selectionKey = `batc.pm.${draftNamespace}.work-selection`;
  const withdrawalKey = `batc.pm.${draftNamespace}.withdrawals`;
  let disposed = false, generation = 0, loaded = false, busy = false, after = null;
  let preview = null, selected = '', editor = null, choosing = false, workAfter = null, workBusy = false;
  let selectionSerial = 0, currentWork = null;
  const forms = new Set(), links = new Map(), works = new Map(), recoveryForms = new Set();
  const status = h('div', {role:'status'}), rows = h('div', {class:'exh-relations'}), draft = h('div');
  const element = h('details', {'data-history-links':entry.entry_id}, h('summary', {}, t('exh_related')), status, rows, draft);
  const current = ticket => {try {alive();} catch {return false;} return !disposed && ticket === generation;};
  const supported = action => caps()?.features?.external_history?.relationships?.version === 1
    && caps()?.features?.external_history?.relationships?.actions?.includes(action)
    && caps()?.scopes?.includes('observe') && caps()?.scopes?.includes('manage')
    && caps()?.actions?.some(row => row.action === action && row.allowed === true);
  const canLink = () => current(generation) && supported('pm.history.link') && permitted(false)
    && ['owner', 'contributor'].includes(workspace()?.membership?.role);
  const canUnlink = link => current(generation) && supported('pm.history.unlink') && Boolean(workspace()?.membership)
    && (workspace()?.membership?.role === 'owner' || link.created_by === caps()?.actor);
  const mismatch = () => Error(t('pm_scope_mismatch'));
  const matches = ref => ref?.schema === 'external_history_ref/v1' && ref.server_id === workspace()?.server_id
    && ref.workspace_id === workspaceId && ref.source_id === sourceId && ref.entry_id === entry.entry_id
    && ref.content_digest === entry.content_digest && digest(ref.source_digest) && digest(ref.excerpt_digest);
  function fail(error, ticket) {
    if (!current(ticket)) return;
    // A refused source proof must hide the original entry as well as its preview.
    onUnavailable(error);
  }
  function disposeForms() {for (const value of forms) value.dispose(); forms.clear(); recoveryForms.clear(); editor = null;}
  function checkPage(data, cursor, key) {
    const next = data[key];
    if (next !== null && (!Number.isSafeInteger(next) || next <= cursor)) throw mismatch();
    return next;
  }
  function pendingWithdrawals() {
    const saved = JSON.parse(localStorage.getItem(withdrawalKey) || '[]');
    if (!Array.isArray(saved) || saved.some(link=>!/^hxl_[0-9a-f]{32}$/.test(link.link_id) || !workId(link.work_item_id)
      || !Number.isSafeInteger(link.version) || link.version < 1 || typeof link.created_by !== 'string')) throw mismatch();
    return saved;
  }
  function rememberWithdrawal(link, remove = false) {
    const saved = pendingWithdrawals().filter(row=>row.link_id !== link.link_id);
    if (!remove) saved.push({link_id:link.link_id, work_item_id:link.work_item_id, version:link.version, created_by:link.created_by});
    if (saved.length) localStorage.setItem(withdrawalKey, JSON.stringify(saved)); else localStorage.removeItem(withdrawalKey);
  }
  function withdrawal(link, active = true) {
    const target = {workspace_id:workspaceId, work_item_id:link.work_item_id, link_id:link.link_id};
    if (!active) {
      // An active-only server list may no longer contain a successfully withdrawn
      // link whose reply was lost. Recover its exact stored intent, never invent
      // a fresh request from this small index of opaque IDs.
      const saved = JSON.parse(localStorage.getItem(`batc.pm.${draftNamespace}.pm.history.unlink.${JSON.stringify(target)}`) || 'null');
      if (!saved?.intent) {rememberWithdrawal(link, true); return null;}
    }
    const remove = form({namespace:draftNamespace, action:'pm.history.unlink', target, fields:{}, params:()=>({}),
      preconditions:()=>{rememberWithdrawal(link); return {expected_link_version:link.version};},
      permitted:()=>active && canUnlink(link), recoverPermitted:()=>canUnlink(link), label:'exh_unlink',
      onSaved:()=>{rememberWithdrawal(link, true); return readLinks(true);}});
    forms.add(remove); if (!active) recoveryForms.add(remove); return remove.box;
  }
  const refresh = button({variant:'ghost', size:'sm', label:t('exh_refresh_links'), onclick:()=>void readLinks(true)});
  const more = button({variant:'secondary', size:'sm', label:t('exh_more_links'), onclick:()=>void readLinks(false)});
  more.hidden = true;
  const choose = button({variant:'secondary', size:'sm', label:t('exh_choose_work'), onclick:()=>void prepare()});
  choose.hidden = !canLink() || caps()?.features?.pm_workspaces?.kanban !== true;
  element.append(h('div', {class:'exh-actions'}, refresh, more, choose));

  async function readLinks(reset) {
    if (busy || disposed) return;
    const ticket = generation, cursor = reset ? 0 : after;
    busy = true; refresh.disabled = more.disabled = true; status.replaceChildren();
    try {
      const data = await api('GET', `${endpoint}/history/${encodeURIComponent(sourceId)}/associations?entry_id=${encodeURIComponent(entry.entry_id)}&after=${cursor}&limit=20`);
      if (!current(ticket)) return;
      if (!Array.isArray(data.links) || data.links.length > 20) throw mismatch();
      const next = checkPage(data, cursor, 'next_after');
      const visible = await Promise.all(data.links.map(async link => {
        if (link.workspace_id !== workspaceId || !workId(link.work_item_id) || !/^hxl_[0-9a-f]{32}$/.test(link.link_id)
          || link.state !== 'active' || !Number.isSafeInteger(link.version) || link.version < 1 || !matches(link.fixed_ref)) throw mismatch();
        const detail = await api('GET', `/work-items/${encodeURIComponent(link.work_item_id)}`);
        if (detail.work_item?.work_item_id !== link.work_item_id || typeof detail.work_item.title !== 'string') throw mismatch();
        return {link, title:detail.work_item.title};
      }));
      if (!current(ticket)) return;
      if (reset) {
        // Refreshing source metadata never replaces an unresolved original intent.
        for (const value of forms) if (value !== editor) {value.dispose(); forms.delete(value);}
        rows.replaceChildren(); links.clear();
      }
      for (const {link, title} of visible) {
        if (links.has(link.link_id)) continue;
        links.set(link.link_id, link);
        const row = h('div', {class:'exh-relation', 'data-history-link':link.link_id},
          h('a', {href:`#/item/${encodeURIComponent(link.work_item_id)}`}, title),
          h('span', {class:'ui-meta'}, t(link.fixed_ref.selection?.kind === 'entry' ? 'exh_entire_entry' : 'exh_selected_text')));
        if (link.attempt_id) row.append(h('span', {class:'ui-meta'}, t('exh_attempt_relation')));
        if (canUnlink(link)) row.append(withdrawal(link));
        rows.append(row);
      }
      // Rebuild recovery controls from IDs only. No titles or source text are
      // retained after a withdrawal or a reader/access boundary.
      for (const value of recoveryForms) {value.dispose(); forms.delete(value);}
      recoveryForms.clear();
      rows.querySelectorAll('[data-history-withdrawal]').forEach(node=>node.remove());
      for (const link of pendingWithdrawals()) if (!links.has(link.link_id) && canUnlink(link)) {
        const recovery = withdrawal(link, false);
        if (recovery) rows.append(h('div', {class:'exh-relation', 'data-history-withdrawal':link.link_id}, h('p', {}, t('exh_withdrawal_pending')), recovery));
      }
      loaded = true; after = next; more.hidden = next === null;
      if (!links.size) status.append(h('p', {class:'ui-meta'}, t(next === null ? 'exh_no_links' : 'exh_more_hidden_links')));
    } catch (error) {fail(error, ticket);}
    finally {if (current(ticket)) {busy = false; refresh.disabled = more.disabled = false;}}
  }
  const workSelect = h('select', {'aria-label':t('exh_work'), onchange:()=>void selectWork(workSelect.value)});
  const workStatus = h('div', {role:'status'}), formSlot = h('div');
  const workMore = button({variant:'secondary', size:'sm', label:t('exh_more_work'), onclick:()=>void readWorks(false)});
  const workRefresh = button({variant:'ghost', size:'sm', label:t('exh_refresh_work'), onclick:()=>void selectWork(selected)});
  workRefresh.disabled = true;
  function checkCard(card, id = card?.work_item_id) {
    if (!workId(id) || card?.work_item_id !== id || card.work?.work_item_id !== id || typeof card.work.title !== 'string'
      || card.binding?.workspace_id !== workspaceId || card.binding?.work_item_id !== id
      || card.work.project_id !== card.binding.project_id || !/^pmwb_[0-9a-f]{32}$/.test(card.binding.binding_id)
      || !Number.isSafeInteger(card.work.version) || card.work.version < 1) throw mismatch();
    return card;
  }
  function choices() {
    workSelect.replaceChildren(h('option', {value:''}, t('exh_select_work')),
      ...[...works.values()].map(card=>h('option', {value:card.work_item_id}, card.work.title)));
    workSelect.value = selected;
  }
  async function readWorks(reset) {
    if (workBusy || disposed) return;
    workBusy = true; workMore.disabled = true;
    const ticket = generation, cursor = reset ? 0 : workAfter;
    try {
      const data = await api('GET', `${endpoint}/kanban?after=${cursor}&limit=20`);
      if (!current(ticket)) return;
      if (data.workspace_id !== workspaceId || !Array.isArray(data.cards) || data.cards.length > 20) throw mismatch();
      const next = checkPage(data, cursor, 'next_cursor');
      for (const card of data.cards) checkCard(card);
      if (reset) works.clear();
      for (const card of data.cards) works.set(card.work_item_id, card);
      workAfter = next; workMore.hidden = next === null; choices();
      if (!works.size) workStatus.replaceChildren(h('p', {class:'ui-meta'}, t('exh_no_work')));
    } catch (error) {
      if (current(ticket)) {currentWork = null; editor?.update(); workStatus.replaceChildren(errorBox(error, {within:workStatus}));}
    } finally {if (current(ticket)) {workBusy = false; workMore.disabled = false;}}
  }
  async function selectWork(id) {
    if (editor?.locked() || !current(generation)) return;
    const ticket = generation, selection = ++selectionSerial;
    editor?.dispose(); forms.delete(editor); editor = null; currentWork = null; workRefresh.disabled = true; formSlot.replaceChildren(); workStatus.replaceChildren(); selected = id;
    try {
      if (id && !workId(id)) throw mismatch();
      if (id) localStorage.setItem(selectionKey, id); else localStorage.removeItem(selectionKey);
      if (!id) return;
      const data = await api('GET', `${endpoint}/kanban/${encodeURIComponent(id)}`);
      if (!current(ticket) || selection !== selectionSerial) return;
      if (data.workspace_id !== workspaceId) throw mismatch();
      currentWork = checkCard(data.card, id); works.set(id, currentWork); choices();
      const fixed = currentWork;
      editor = form({namespace:draftNamespace, action:'pm.history.link', target:{workspace_id:workspaceId, work_item_id:id},
        fields:{}, params:()=>({fixed_ref:preview.ref}),
        preconditions:()=>({expected_work_version:fixed.work.version, expected_binding_id:fixed.binding.binding_id}),
        permitted:()=>canLink() && Boolean(preview) && currentWork === fixed, recoverPermitted:canLink,
        label:'exh_link', savedText:'exh_link_saved',
        onStateChange:()=>{workSelect.disabled = Boolean(editor?.locked()); workRefresh.disabled = !selected || workSelect.disabled;},
        onSaved:async()=>{await readLinks(true);},
        extra:h('p', {class:'ui-meta'}, t('exh_link_target', {name:fixed.work.title, version:fixed.work.version}))});
      forms.add(editor); formSlot.append(editor.box); editor.update();
    } catch (error) {
      if (current(ticket) && selection === selectionSerial) workStatus.replaceChildren(errorBox(error, {within:workStatus}));
    } finally {
      if (current(ticket) && selection === selectionSerial) workRefresh.disabled = !selected || Boolean(editor?.locked());
    }
  }
  async function prepare() {
    if (choosing || !canLink()) return;
    choosing = true; choose.disabled = true;
    const ticket = generation;
    try {
      const data = await api('POST', `${endpoint}/history-excerpt-preview`, {
        source_id:sourceId, entry_id:entry.entry_id, expected_content_digest:entry.content_digest, selection:{kind:'entry'}});
      if (!current(ticket)) return;
      if (!matches(data.ref) || data.ref.selection?.kind !== 'entry' || Object.keys(data.ref.selection).length !== 1
        || data.history_complete !== false || data.execution_status !== 'unknown' || !Array.isArray(data.content?.parts)) throw mismatch();
      preview = data;
      draft.replaceChildren(h('div', {}, h('h4', {}, t('exh_preview')), h('p', {class:'ui-meta'}, t('exh_link_help')),
        ...data.content.parts.map(part=>part.kind === 'text' ? h('p', {class:'exh-text'}, part.text)
          : h('p', {class:'ui-meta'}, t(part.kind === 'tool_request' ? 'exh_tool_request' : 'exh_tool_result', {name:part.name || t('exh_tool_unknown')}))),
        data.content.clipped ? h('p', {class:'ui-meta'}, t('exh_clipped')) : null,
        data.content.omitted_parts ? h('p', {class:'ui-meta'}, t('exh_omitted', {count:data.content.omitted_parts})) : null,
        h('label', {}, t('exh_work'), workSelect), workStatus, h('div', {class:'exh-actions'}, workMore, workRefresh), formSlot));
      choose.hidden = true;
      selected = localStorage.getItem(selectionKey) || '';
      await readWorks(true);
      if (current(ticket) && selected) await selectWork(selected);
    } catch (error) {fail(error, ticket);}
    finally {if (current(ticket)) choose.disabled = false;}
  }
  element.addEventListener('toggle', ()=>{if (element.open && !loaded) void readLinks(true);});
  return {element, dispose(){disposed = true; generation++; selectionSerial++; disposeForms(); preview = currentWork = null; works.clear(); links.clear(); element.replaceChildren();}};
}
