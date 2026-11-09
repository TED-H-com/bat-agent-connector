import {button, disclosure, inlineError} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';

const digest = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const keys = (value, names) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join(',') === [...names].sort().join(',');
const refFields = ['schema','server_id','workspace_id','source_id','entry_id','source_digest','content_digest','selection','excerpt_digest'];

let readerSequence = 0;

// The parent owns Work selection and visibility. This reader only resolves explicit
// relationships through central; no transcript or selection is persisted locally.
export function workHistoryPanel({h, t, api, alive, workspaceId, workspace, caps, embedded = false, validate = null}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  let card = null, identity = '', disposed = false, generation = 0, detailGeneration = 0;
  let pages = 1, next = null, selected = null, busy = false, timer = null, ready = false;
  let links = new Map();
  const excerptId = `work-history-${workspaceId}-${++readerSequence}`;
  const status = h('div', {role:'status'}), rows = h('div'), excerpt = h('div', {id:excerptId, 'data-work-history-excerpt':''});
  const refresh = button({variant:'ghost', size:'sm', label:t('exh_refresh_links'), onclick:()=>void read()});
  const more = button({variant:'secondary', size:'sm', label:t('exh_more_links'), onclick:()=>{pages++; void read();}});
  more.hidden = true;
  const reader = h(embedded ? 'div' : 'details', {'data-work-history-reader':''}, embedded ? null : h('summary', {}, t('exh_read_links')),
    h('p', {class:'ui-meta'}, t('exh_work_help')), status, rows,
    h('div', {class:'exh-actions'}, refresh, more), excerpt);
  const element = h('section', {'data-work-section':'external-history', 'data-work-history':'', hidden:true},
    embedded ? null : h('h3', {}, t('exh_title')), reader);
  const isOpen = () => embedded || reader.open;
  const supported = () => caps()?.features?.external_history?.relationships?.version === 1
    && caps()?.features?.external_history?.relationships?.refs?.includes('external_history_ref/v1')
    && caps()?.scopes?.includes('observe') && workspace()?.workspace_id === workspaceId
    && ['owner','contributor','reader'].includes(workspace()?.membership?.role) && typeof workspace()?.server_id === 'string';
  const current = ticket => {try {alive();} catch {return false;} return !disposed && ready && card && isOpen() && supported() && ticket === generation;};
  const mismatch = () => Error(t('pm_scope_mismatch'));
  function reference(ref) {
    if (!keys(ref, refFields) || ref.schema !== 'external_history_ref/v1' || ref.server_id !== workspace()?.server_id
      || ref.workspace_id !== workspaceId || typeof ref.source_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(ref.source_id)
      || !/^shi_[0-9a-f]{32}$/.test(ref.entry_id) || !['source_digest','content_digest','excerpt_digest'].every(key=>digest(ref[key]))) throw mismatch();
    const selection = ref.selection;
    if (selection?.kind === 'entry') {if (!keys(selection, ['kind'])) throw mismatch();}
    else if (!keys(selection, ['kind','part_index','start_codepoint','end_codepoint']) || selection.kind !== 'text'
      || ![selection.part_index,selection.start_codepoint,selection.end_codepoint].every(Number.isSafeInteger)
      || selection.part_index < 0 || selection.part_index >= 128 || selection.start_codepoint < 0
      || selection.start_codepoint >= selection.end_codepoint || selection.end_codepoint > 16000) throw mismatch();
    return JSON.stringify([...refFields.filter(key=>key !== 'selection').map(key=>ref[key]),
      selection.kind, selection.part_index, selection.start_codepoint, selection.end_codepoint]);
  }
  function relation(link) {
    if (!link || link.workspace_id !== workspaceId || link.work_item_id !== card?.work_item_id
      || !/^hxl_[0-9a-f]{32}$/.test(link.link_id) || !/^hxe_[0-9a-f]{64}$/.test(link.excerpt_id)
      || link.attempt_id !== null && !/^att_[0-9a-f]{32}$/.test(link.attempt_id)
      || link.purpose !== 'reference' || link.state !== 'active' || !Number.isSafeInteger(link.version) || link.version < 1) throw mismatch();
    return JSON.stringify([link.link_id,link.work_item_id,link.attempt_id,link.excerpt_id,link.version,reference(link.fixed_ref)]);
  }
  function clear() {
    generation++; detailGeneration++; clearTimeout(timer); timer = null; busy = false;
    links.clear(); next = null; rows.replaceChildren(); excerpt.replaceChildren(); status.replaceChildren();
    more.hidden = true; refresh.disabled = more.disabled = false; reader.removeAttribute('aria-busy');
  }
  function fail(error, ticket) {
    if (!current(ticket)) return;
    clear(); selected = null; pages = 1;
    status.replaceChildren(inlineError({error:{message:t('exh_unavailable')}}));
  }
  function content(data, link) {
    if (data?.excerpt_id !== link.excerpt_id || reference(data.ref) !== reference(link.fixed_ref)
      || data.history_complete !== false || data.execution_status !== 'unknown') throw mismatch();
    const value = data.content;
    if (!value || !['user','assistant','tool'].includes(value.role) || typeof value.clipped !== 'boolean'
      || !Number.isSafeInteger(value.omitted_parts) || value.omitted_parts < 0 || !Array.isArray(value.parts) || value.parts.length > 128) throw mismatch();
    let length = 0;
    for (const part of value.parts) {
      if (part?.kind === 'text' && typeof part.text === 'string') length += [...part.text].length;
      else if (!['tool_request','tool_result'].includes(part?.kind) || part.name !== null && typeof part.name !== 'string') throw mismatch();
    }
    if (length > 16000) throw mismatch();
    return value;
  }
  async function open(linkId, ticket = generation) {
    if (!current(ticket) || !links.has(linkId)) return;
    const own = ++detailGeneration, link = links.get(linkId), signature = relation(link);
    selected = linkId; excerpt.replaceChildren(h('p', {role:'status'}, t('pm_kanban_detail_loading')));
    for (const row of rows.querySelectorAll('[data-history-open]')) row.setAttribute('aria-expanded', String(row.dataset.historyOpen === selected));
    const valid = () => current(ticket) && own === detailGeneration;
    try {
      if (validate) {await validate(); if (!valid()) return;}
      const detail = await api('GET', `${endpoint}/external-links/${encodeURIComponent(linkId)}`);
      if (!valid()) return;
      if (relation(detail.link) !== signature) throw mismatch();
      const data = await api('GET', `${endpoint}/history-excerpts/${encodeURIComponent(link.excerpt_id)}`);
      if (!valid()) return;
      const value = content(data, link);
      // Excerpt reads authorize the source. Recheck the Work/Attempt relationship
      // after that read as well, before publishing any plaintext.
      if (validate) {await validate(); if (!valid()) return;}
      const final = await api('GET', `${endpoint}/external-links/${encodeURIComponent(linkId)}`);
      if (!valid()) return;
      if (relation(final.link) !== signature) throw mismatch();
      excerpt.replaceChildren(h('article', {class:'exh-entry'},
        h('h4', {}, link.fixed_ref.source_id, ' · ', t(link.fixed_ref.selection.kind === 'entry' ? 'exh_entire_entry' : 'exh_selected_text')),
        h('p', {class:'ui-meta'}, t('exh_scope')), h('p', {class:'ui-meta'}, t('exh_execution_unknown')),
        h('strong', {}, t(`exh_${value.role}`)),
        ...value.parts.map(part=>part.kind === 'text' ? h('p', {class:'exh-text'}, part.text)
          : h('p', {class:'ui-meta'}, t(part.kind === 'tool_request' ? 'exh_tool_request' : 'exh_tool_result', {name:part.name || t('exh_tool_unknown')}))),
        value.clipped ? h('p', {class:'ui-meta'}, t('exh_clipped')) : null,
        value.omitted_parts ? h('p', {class:'ui-meta'}, t('exh_omitted', {count:value.omitted_parts})) : null,
        disclosure({label:t('exh_coverage'), body:h('div', {class:'exh-reference'},
          h('p', {}, t('exh_fixed_reference')), h('pre', {}, JSON.stringify({link_id:link.link_id, attempt_id:link.attempt_id, ref:data.ref}, null, 2)))})));
    } catch (error) {if (valid()) fail(error, ticket);}
  }
  async function read() {
    if (disposed || !ready || !card || !isOpen()) return;
    const restore = selected, focus = rows.contains(document.activeElement) ? document.activeElement?.dataset.historyOpen : null;
    clear();
    const ticket = generation;
    if (!current(ticket)) {element.hidden = true; return;}
    busy = true; refresh.disabled = more.disabled = true; reader.setAttribute('aria-busy', 'true');
    status.replaceChildren(h('p', {}, t('pm_kanban_detail_loading')));
    try {
      if (validate) {await validate(); if (!current(ticket)) return;}
      const found = new Map(); let after = 0;
      for (let index = 0; index < pages; index++) {
        const data = await api('GET', `${endpoint}/work/${encodeURIComponent(card.work_item_id)}/external-links?after=${after}&limit=20`);
        if (!current(ticket)) return;
        if (!Array.isArray(data.links) || data.links.length > 20
          || data.next_after !== null && (!Number.isSafeInteger(data.next_after) || data.next_after <= after)) throw mismatch();
        for (const link of data.links) {relation(link); if (found.has(link.link_id)) throw mismatch(); found.set(link.link_id, link);}
        next = data.next_after;
        if (next === null) break;
        after = next;
      }
      links = found;
      status.replaceChildren(...(!links.size ? [h('p', {class:'ui-meta'}, t(next !== null ? 'exh_more_hidden_links' : 'exh_work_no_links'))] : []),
        ...(next !== null && pages >= 25 ? [h('p', {class:'ui-meta'}, t('exh_link_limit'))] : []));
      rows.replaceChildren(...[...links.values()].map(link=>h('div', {class:'exh-relation'},
        button({variant:'ghost', size:'sm', label:t('exh_read_excerpt', {name:link.fixed_ref.source_id}), 'data-history-open':link.link_id,
          'aria-expanded':'false', 'aria-controls':excerptId, onclick:()=>void open(link.link_id)}),
        h('span', {class:'ui-meta'}, t(link.fixed_ref.selection.kind === 'entry' ? 'exh_entire_entry' : 'exh_selected_text'),
          ' · ', timeStamp(link.created_at)),
        link.attempt_id ? h('span', {class:'ui-meta'}, t('exh_attempt_relation')) : null)));
      more.hidden = next === null || pages >= 25;
      if (focus) [...rows.querySelectorAll('[data-history-open]')].find(row=>row.dataset.historyOpen === focus)?.focus({preventScroll:true});
      if (restore && links.has(restore)) await open(restore, ticket);
      else selected = null;
    } catch (error) {fail(error, ticket);}
    finally {
      if (current(ticket)) {
        busy = false; refresh.disabled = more.disabled = false; reader.removeAttribute('aria-busy');
        timer = setTimeout(()=>void read(), 60000);
      }
    }
  }
  if (!embedded) reader.addEventListener('toggle', ()=>{
    if (!reader.open) {clear(); selected = null; pages = 1;}
    else if (card && !busy) void read();
  });
  function withdraw() {clear(); ready = false; card = null; identity = ''; selected = null; pages = 1; reader.open = false; element.hidden = true;}
  return {element, withdraw,
    suspend() {
      clear(); ready = false;
      if (isOpen()) {status.replaceChildren(h('p', {}, t('pm_kanban_detail_loading'))); refresh.disabled = true;}
    },
    setWork(value) {
      const key = JSON.stringify([value.work_item_id, value.binding?.binding_id, value.binding?.project_id]);
      if (identity !== key) withdraw();
      card = value; identity = key; ready = true; element.hidden = !supported();
      if (element.hidden) {clear(); return;}
      if (isOpen()) void read();
    },
    dispose() {withdraw(); disposed = true;},
  };
}
