import {button, disclosure, emptyState, inspector, sectionCard, skeleton} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';

// Clients only read the shared collector. No CLI controls, prompts or automatic PM context.
export function workspaceExternalHistory({h, t, api, alive, workspaceId, errorBox}) {
  const root = `/pm/workspaces/${encodeURIComponent(workspaceId)}/history`;
  let active = false, disposed = false, serial = 0, detailSerial = 0, timer = null, loading = null;
  let rows = [], signature = '', selected = null, panel = null, count = 0, last = 0, next = null, detailBusy = false;
  let messages = null, detailStatus = null, more = null, newEntries = null;
  const status = h('div', {role: 'status'}), list = h('div', {class: 'exh-list'});
  const element = sectionCard({title: t('exh_title'), body: h('div', {}, h('p', {class: 'ui-meta'}, t('exh_help')), status, list)});
  element.dataset.externalHistory = '';
  const sourceName = row => `${row.adapter?.startsWith('codex-') ? 'Codex' : row.adapter?.startsWith('claude-') ? 'Claude' : t('exh_source')} · ${row.source_id}`;
  const state = row => t(row.stale ? 'exh_stale' : ({current:'exh_ready', catching_up:'exh_catching', entry_limit:'exh_limit', pending:'exh_pending'}[row.status] || 'exh_unavailable'));
  const valid = ticket => {try {alive();} catch {return false;} return !disposed && active && ticket === serial;};
  function close() {
    detailSerial++; selected = null; count = 0; last = 0; next = null; detailBusy = false;
    const old = panel; panel = null; old?.dispose();
    messages = detailStatus = more = newEntries = null;
  }
  function draw() {
    const changed = JSON.stringify(rows.map(row => [row.source_id, row.adapter, row.status, row.stale, row.readable, row.entries]));
    if (signature === changed) return;
    signature = changed;
    const focused = list.contains(document.activeElement) ? document.activeElement?.dataset.historySource : null;
    list.replaceChildren(...(rows.length ? rows.map(row => {
      const control = button({variant:'ghost', size:'sm', label:t('exh_open', {name:sourceName(row)}), onclick:()=>void open(row.source_id)});
      control.dataset.historySource = row.source_id;
      return h('div', {class:'exh-source'}, control, h('span', {class:'ui-meta'}, state(row), row.readable ? ` · ${t('exh_entries', {count:row.entries})}` : ''));
    }) : [emptyState({title:t('exh_empty'), text:t('exh_empty_help'), heading:'h3'})]));
    if (focused) [...list.querySelectorAll('[data-history-source]')].find(node=>node.dataset.historySource === focused)?.focus({preventScroll:true});
  }
  function controls(row) {
    if (!panel || !more || !newEntries) return;
    more.hidden = next == null || count >= 500;
    more.disabled = detailBusy;
    newEntries.hidden = !count || next != null || !row?.readable || !(row.entries > count) || count >= 500;
    newEntries.disabled = detailBusy;
  }
  function coverage(row) {
    return disclosure({label:t('exh_coverage'), body:h('div', {class:'ui-meta'},
      h('p', {}, t('exh_scope')), h('p', {}, row.collected_at ? t('exh_asof', {time:timeStamp(row.collected_at)}) : t('exh_unknown_time')),
      h('p', {}, t('exh_coverage_counts', {records:row.coverage?.omitted_records || 0, parts:row.coverage?.omitted_parts || 0, clipped:row.coverage?.clipped_records || 0})),
      h('p', {}, t('exh_cli'), ': ', row.cli_version || '—'), h('code', {}, row.adapter))});
  }
  function entry(value) {
    return h('article', {class:'exh-entry', 'data-history-entry':value.entry_id},
      h('header', {}, h('strong', {}, t(['user','assistant','tool'].includes(value.role) ? `exh_${value.role}` : 'exh_tool')),
        h('span', {class:'ui-meta'}, value.observed_at ? timeStamp(value.observed_at) : '—')),
      ...(value.parts || []).map(part => part.kind === 'text' ? h('p', {class:'exh-text'}, part.text)
        : h('p', {class:'ui-meta'}, t(part.kind === 'tool_request' ? 'exh_tool_request' : 'exh_tool_result', {name:part.name || t('exh_tool_unknown')}))),
      value.clipped ? h('p', {class:'ui-meta'}, t('exh_clipped')) : null,
      value.omitted_parts ? h('p', {class:'ui-meta'}, t('exh_omitted', {count:value.omitted_parts})) : null);
  }
  async function page({initial = false} = {}) {
    if (!selected || !panel || detailBusy || count >= 500) return;
    const id = selected, ticket = ++detailSerial, current = serial;
    detailBusy = true; controls(rows.find(row=>row.source_id === id));
    try {
      const data = await api('GET', `${root}/${encodeURIComponent(id)}?after=${initial ? 0 : last}&limit=${Math.min(50, 500-count)}`);
      if (!valid(current) || ticket !== detailSerial || selected !== id || !panel) return;
      if (data.workspace_id !== workspaceId || data.source?.source_id !== id || !Array.isArray(data.entries)) throw Error(t('pm_scope_mismatch'));
      detailStatus.replaceChildren();
      if (!data.source.readable) {
        messages.replaceChildren(); count = last = 0; next = null;
        detailStatus.replaceChildren(h('p', {}, t('exh_unavailable'))); return;
      }
      if (initial) {messages.replaceChildren(); panel.body.prepend(coverage(data.source));}
      for (const value of data.entries || []) {
        if (Number.isSafeInteger(value.sequence) && value.sequence > last) {messages.append(entry(value)); last = value.sequence; count++;}
      }
      next = data.next_after;
      if (!count) detailStatus.replaceChildren(h('p', {}, t('exh_no_entries')));
      if (count >= 500) detailStatus.replaceChildren(h('p', {}, t('exh_page_limit')));
      const index = rows.findIndex(row=>row.source_id === id);
      if (index >= 0) rows[index] = data.source;
    } catch (error) {
      if (!valid(current) || ticket !== detailSerial || selected !== id || !panel) return;
      // Every failed source proof hides old plaintext, including a lost grant.
      messages.replaceChildren(); count = last = 0; next = null;
      detailStatus.replaceChildren(errorBox(error), button({variant:'secondary', size:'sm', label:t('exh_retry'), onclick:()=>void open(id)}));
    } finally {
      if (ticket === detailSerial) {detailBusy = false; controls(rows.find(row=>row.source_id === id));}
    }
  }
  async function open(id) {
    close(); selected = id;
    const row = rows.find(row=>row.source_id === id); if (!row) return;
    panel = inspector({title:sourceName(row), dock:true, overlay:true, historyNavigation:true,
      returnFocus:()=>[...list.querySelectorAll('[data-history-source]')].find(node=>node.dataset.historySource === id),
      onClose:()=>{detailSerial++; selected = null; count = last = 0; next = null; detailBusy = false; panel?.body.replaceChildren(); messages = detailStatus = more = newEntries = null;}, canReopen:()=>active && selected === id && rows.some(row=>row.source_id === id)});
    panel.dataset.historyDetail = '';
    detailStatus = h('div', {role:'status'}, skeleton(3)); messages = h('div', {class:'exh-messages'});
    more = button({variant:'secondary', size:'sm', label:t('exh_more'), onclick:()=>void page()}); more.hidden = true;
    newEntries = button({variant:'secondary', size:'sm', label:t('exh_new_read'), onclick:()=>void page()}); newEntries.hidden = true;
    panel.body.append(h('p', {class:'ui-meta'}, t('exh_scope')), detailStatus, messages, h('div', {class:'exh-actions'}, more, newEntries));
    element.append(panel); const opened = panel; queueMicrotask(()=>{if (panel === opened) opened.open();}); await page({initial:true});
  }
  function withdraw() {serial++; loading = null; clearTimeout(timer); rows = []; signature = ''; close(); list.replaceChildren(); status.replaceChildren();}
  async function refresh() {
    if (!active || disposed) return;
    if (loading) return loading;
    const ticket = serial;
    const request = (async()=>{
      try {
        const data = await api('GET', root);
        if (!valid(ticket)) return;
        if (data.workspace_id !== workspaceId || !Array.isArray(data.rows)) throw Error(t('pm_scope_mismatch'));
        rows = data.rows; status.replaceChildren(); draw();
        if (selected) {
          const row = rows.find(item=>item.source_id === selected);
          if (!row || !row.readable) {close(); status.replaceChildren(h('p', {}, t('exh_unavailable')));}
          else controls(row);
        }
      } catch (error) {
        if (!valid(ticket)) return;
        withdraw(); status.replaceChildren(errorBox(error));
      } finally {
        if (loading === request) loading = null;
        clearTimeout(timer);
        if (active && !disposed) timer = setTimeout(()=>void refresh(), 60000);
      }
    })();
    loading = request; return request;
  }
  function setActive(value) {
    if (active === value) return value ? refresh() : undefined;
    active = value;
    if (!value) {serial++; loading = null; clearTimeout(timer); close(); return;}
    return refresh();
  }
  return {element, refresh, setActive, withdraw, dispose(){disposed = true; active = false; withdraw();}};
}
