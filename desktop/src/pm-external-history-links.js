import {button} from './ui/components.js';
import {historyTextSelection, historySelectionKey, historyReferenceKey, matchesHistoryContent, navigateHistoryText} from './pm-history-selection.js';

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
  let selectionSerial = 0, previewSerial = 0, previewBusy = false, workSelecting = false, currentWork = null;
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

  const previewSlot = h('div', {'data-history-preview':'', role:'status'});
  const selectionStatus = h('p', {role:'status', class:'ui-meta'});
  const whole = button({variant:'secondary', size:'sm', label:t('exh_use_entry'), onclick:()=>void changeSelection({kind:'entry'})});
  const selectionControls = [whole];
  const textChoices = h('details', {'data-history-text-picker':''}, h('summary', {}, t('exh_choose_text')),
    h('p', {class:'ui-meta'}, t('exh_selection_help')));
  for (const [index, part] of (entry.parts || []).entries()) {
    if (part.kind !== 'text' || !part.text) continue;
    const input = h('textarea', {readonly:true, rows:5, class:'exh-text', 'aria-label':t('exh_text_part', {number:index + 1}), 'data-history-text-part':index});
    input.value = part.text;
    input.addEventListener('keydown', event=>navigateHistoryText(input, event));
    const use = button({variant:'secondary', size:'sm', label:t('exh_preview_selection'), onclick:()=>{
      if (previewBusy || editor?.locked() || !canLink()) return;
      const selection = historyTextSelection(part.text, index, input.selectionStart, input.selectionEnd);
      if (!selection) {selectionStatus.textContent = t('exh_select_text_first'); input.focus(); return;}
      void changeSelection(selection);
    }});
    selectionControls.push(input, use);
    textChoices.append(h('div', {class:'exh-text-choice'}, h('label', {}, t('exh_text_part', {number:index + 1}), input), use));
  }
  textChoices.hidden = selectionControls.length === 1;
  function updateSelectionControls() {
    const locked = Boolean(editor?.locked()), blocked = locked || previewBusy || workSelecting || !canLink();
    for (const control of selectionControls) control.disabled = blocked;
    whole.hidden = preview?.ref.selection.kind !== 'text';
    workSelect.disabled = locked || previewBusy;
    workRefresh.disabled = !selected || workSelect.disabled;
  }
  function drawPreview() {
    previewSlot.replaceChildren(h('h4', {}, t('exh_preview')), h('p', {class:'ui-meta'}, t('exh_link_help')),
      h('p', {class:'ui-meta'}, t(preview.ref.selection.kind === 'entry' ? 'exh_entire_entry' : 'exh_selected_text')),
      ...preview.content.parts.map(part=>part.kind === 'text' ? h('p', {class:'exh-text'}, part.text)
        : h('p', {class:'ui-meta'}, t(part.kind === 'tool_request' ? 'exh_tool_request' : 'exh_tool_result', {name:part.name || t('exh_tool_unknown')}))),
      ...[preview.content.clipped ? h('p', {class:'ui-meta'}, t('exh_clipped')) : null,
        preview.content.omitted_parts ? h('p', {class:'ui-meta'}, t('exh_omitted', {count:preview.content.omitted_parts})) : null].filter(Boolean));
  }
  async function readPreview(selection) {
    if (!historySelectionKey(selection)) throw mismatch();
    const data = await api('POST', `${endpoint}/history-excerpt-preview`, {
      source_id:sourceId, entry_id:entry.entry_id, expected_content_digest:entry.content_digest, selection});
    if (!matches(data.ref) || !historyReferenceKey(data.ref) || historySelectionKey(data.ref.selection) !== historySelectionKey(selection)
      || data.history_complete !== false || data.execution_status !== 'unknown' || !matchesHistoryContent(data.content, entry, selection)) throw mismatch();
    return data;
  }
  async function changeSelection(selection, restored = null) {
    if (!current(generation) || !canLink() || (!restored && (previewBusy || workSelecting || editor?.locked()))) return;
    const ticket = generation, serial = ++previewSerial, owner = editor;
    const focused = selectionControls.includes(document.activeElement) ? document.activeElement : null;
    previewBusy = true; preview = null; selectionStatus.textContent = '';
    previewSlot.replaceChildren(h('p', {}, t('pm_kanban_detail_loading')));
    editor?.update(); updateSelectionControls();
    try {
      const data = await readPreview(selection);
      if (!current(ticket) || serial !== previewSerial || owner !== editor) return;
      if (restored && historyReferenceKey(data.ref) !== historyReferenceKey(restored)) throw mismatch();
      preview = data; drawPreview();
      if (restored && editor) {
        // A succeeded automatic receipt may clear fields while this preview is
        // pending. Restore the verified display value even during onSaved;
        // this never changes the form's immutable original request.
        editor.fields.fixed_ref.value = JSON.stringify(data.ref);
        editor.changed();
      } else editor?.set('fixed_ref', JSON.stringify(data.ref));
    } catch (error) {if (serial === previewSerial && owner === editor) fail(error, ticket);}
    finally {
      if (current(ticket) && serial === previewSerial && owner === editor) {
        previewBusy = false; editor?.update(); updateSelectionControls();
        if (focused?.isConnected && !focused.disabled && !focused.hidden && document.activeElement === document.body) focused.focus({preventScroll:true});
      }
    }
  }

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
    if (editor?.locked() || previewBusy || !current(generation)) return;
    const ticket = generation, selection = ++selectionSerial;
    editor?.dispose(); forms.delete(editor); editor = null; currentWork = null; workRefresh.disabled = true; formSlot.replaceChildren(); workStatus.replaceChildren(); selected = id;
    workSelecting = true; updateSelectionControls();
    try {
      if (id && !workId(id)) throw mismatch();
      if (id) localStorage.setItem(selectionKey, id); else localStorage.removeItem(selectionKey);
      if (!id) return;
      const data = await api('GET', `${endpoint}/kanban/${encodeURIComponent(id)}`);
      if (!current(ticket) || selection !== selectionSerial) return;
      if (data.workspace_id !== workspaceId) throw mismatch();
      currentWork = checkCard(data.card, id); works.set(id, currentWork); choices();
      const fixed = currentWork;
      const reference = h('input', {type:'hidden', value:JSON.stringify(preview.ref)});
      editor = form({namespace:draftNamespace, action:'pm.history.link', target:{workspace_id:workspaceId, work_item_id:id},
        fields:{fixed_ref:reference}, params:values=>({fixed_ref:JSON.parse(values.fixed_ref)}),
        preconditions:()=>({expected_work_version:fixed.work.version, expected_binding_id:fixed.binding.binding_id}),
        permitted:()=>canLink() && !previewBusy && Boolean(preview) && reference.value === JSON.stringify(preview.ref) && currentWork === fixed, recoverPermitted:canLink,
        label:'exh_link', savedText:'exh_link_saved',
        onStateChange:updateSelectionControls,
        onSaved:async()=>{if (preview) reference.value = JSON.stringify(preview.ref); await readLinks(true);},
        extra:h('p', {class:'ui-meta'}, t('exh_link_target', {name:fixed.work.title, version:fixed.work.version}))});
      forms.add(editor); formSlot.append(editor.box); editor.update();
      let restored;
      try {
        // Original operations predate range metadata in some local drafts.
        // Their immutable request, not a newly chosen preview, owns recovery.
        restored = editor.intentRequest()?.params.fixed_ref || JSON.parse(editor.values().fixed_ref);
        if (!matches(restored) || !historyReferenceKey(restored)) throw mismatch();
        if (editor.locked()) reference.value = JSON.stringify(restored);
      } catch (error) {fail(error, ticket); return;}
      if (historyReferenceKey(restored) !== historyReferenceKey(preview.ref)) await changeSelection(restored.selection, restored);
      // Field order in a stored server reference is immaterial. New requests
      // still send that reference unchanged, with no client-produced digest.
      if (preview && !editor.locked()) editor.set('fixed_ref', JSON.stringify(preview.ref));
      editor?.update();
    } catch (error) {
      if (current(ticket) && selection === selectionSerial) workStatus.replaceChildren(errorBox(error, {within:workStatus}));
    } finally {
      if (current(ticket) && selection === selectionSerial) {workSelecting = false; updateSelectionControls();}
    }
  }
  async function prepare() {
    if (choosing || !canLink()) return;
    choosing = true; choose.disabled = true;
    const ticket = generation;
    try {
      const data = await readPreview({kind:'entry'});
      if (!current(ticket)) return;
      preview = data;
      drawPreview(); updateSelectionControls();
      draft.replaceChildren(h('div', {}, textChoices, selectionStatus, whole, previewSlot,
        h('label', {}, t('exh_work'), workSelect), workStatus, h('div', {class:'exh-actions'}, workMore, workRefresh), formSlot));
      choose.hidden = true;
      selected = localStorage.getItem(selectionKey) || '';
      await readWorks(true);
      if (current(ticket) && selected) await selectWork(selected);
    } catch (error) {fail(error, ticket);}
    finally {if (current(ticket)) choose.disabled = false;}
  }
  element.addEventListener('toggle', ()=>{if (element.open && !loaded) void readLinks(true);});
  return {element, dispose(){disposed = true; generation++; selectionSerial++; previewSerial++; disposeForms(); preview = currentWork = null; works.clear(); links.clear(); element.replaceChildren();}};
}
