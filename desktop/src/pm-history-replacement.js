import {button} from './ui/components.js';
import {historyReferenceKey} from './pm-history-selection.js';

export function historyReplacement({h, t, api, alive, workspaceId, matches, allowed, choices, blocked, onChange, errorBox}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  const field = h('input', {type:'hidden',value:''});
  let selected = null, verified = null, busy = false, disposed = false, serial = 0;
  const status = h('div', {role:'status'}), detail = h('div', {'data-history-replacement-detail':''});
  const select = h('select', {'aria-label':t('exh_replacement'),onchange:()=>{
    if (blocked() || busy) return;
    const row = choices().find(value=>value.link.link_id === select.value)?.link;
    selected = row ? {link_id:row.link_id,version:row.version} : null;
    field.value = selected ? JSON.stringify(selected) : ''; verified = null; onChange(); void read();
  }});
  const refresh = button({variant:'ghost',size:'sm',label:t('exh_replacement_refresh'),onclick:()=>void read(true)});
  const element = h('details', {'data-history-replacement':''},h('summary',{},t('exh_replace_choose')),
    h('p',{class:'ui-meta'},t('exh_replace_help')),h('label',{},t('exh_replacement'),select),status,detail,refresh);
  const current = ticket => {try {alive();} catch {return false;} return !disposed && ticket === serial;};
  const mismatch = () => Error(t('pm_scope_mismatch'));
  function drawChoices() {
    const rows = choices().filter(row=>allowed(row.link));
    select.replaceChildren(h('option',{value:''},t('exh_create_relationship')),
      ...rows.map(({link,title})=>h('option',{value:link.link_id},`${title} · ${t(link.fixed_ref.selection.kind === 'entry' ? 'exh_entire_entry' : 'exh_selected_text')} · ${link.link_id}`)));
    if (selected && !rows.some(row=>row.link.link_id === selected.link_id)) select.append(h('option',{value:selected.link_id},selected.link_id));
    select.value = selected?.link_id || '';
  }
  function update() {
    select.disabled = busy || blocked(); refresh.disabled = select.disabled || !selected;
  }
  async function read(advance = false) {
    if (advance && (blocked() || busy)) return;
    const ticket = ++serial, expected = selected;
    busy = true; verified = null; detail.replaceChildren(); status.replaceChildren(); update(); onChange();
    try {
      if (!expected) return;
      const data = await api('GET',`${endpoint}/external-links/${encodeURIComponent(expected.link_id)}`);
      if (!current(ticket)) return;
      const link = data.link;
      if (link?.link_id !== expected.link_id || link.workspace_id !== workspaceId || !/^wi_[0-9a-f]{20}$/.test(link.work_item_id)
        || link.state !== 'active' || link.purpose !== 'reference' || !Number.isSafeInteger(link.version) || link.version < 1
        || !matches(link.fixed_ref) || !historyReferenceKey(link.fixed_ref) || !allowed(link)
        || !advance && link.version !== expected.version) throw mismatch();
      const work = await api('GET',`/work-items/${encodeURIComponent(link.work_item_id)}`);
      if (!current(ticket)) return;
      if (work.work_item?.work_item_id !== link.work_item_id || typeof work.work_item.title !== 'string') throw mismatch();
      selected = {link_id:link.link_id,version:link.version}; field.value = JSON.stringify(selected); verified = link;
      detail.replaceChildren(h('p',{},t('exh_replace_target',{name:work.work_item.title,version:link.version})),h('p',{class:'mono'},link.link_id));
    } catch (error) {if (current(ticket)) {verified = null; detail.replaceChildren(); status.replaceChildren(errorBox(error,{within:status}));}}
    finally {if (current(ticket)) {busy = false; update(); onChange();}}
  }
  drawChoices(); update();
  return {element,field,update,
    refreshChoices:drawChoices,
    ready:()=>!busy && (!selected ? field.value === '' : Boolean(verified && allowed(verified) && field.value === JSON.stringify(selected))),
    restore:async value=>{
      if (value !== '' && (typeof value !== 'object' || !value || Object.keys(value).sort().join(',') !== 'link_id,version'
        || !/^hxl_[0-9a-f]{32}$/.test(value.link_id) || !Number.isSafeInteger(value.version) || value.version < 1)) throw mismatch();
      selected = value || null; field.value = selected ? JSON.stringify(selected) : ''; drawChoices();
      if (selected) {element.open = true; await read();}
    },
    saved() {serial++; busy = false; selected = verified = null; field.value = ''; detail.replaceChildren(); status.replaceChildren(); drawChoices(); update();},
    dispose() {disposed = true; serial++; selected = verified = null; field.value = ''; element.replaceChildren();},
  };
}
