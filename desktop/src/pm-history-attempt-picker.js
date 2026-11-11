import {button} from './ui/components.js';
import {timeText} from './ui/stamps.js';

const attemptId = value => /^att_[0-9a-f]{32}$/.test(value || '');
// This picker reads existing attempts. Its only durable value is an opaque ID;
// the original operation request, when present, owns recovery independently.
export function historyAttemptPicker({h, t, api, alive, workspaceId, workId, supported, blocked, onChange, errorBox}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  const field = h('input', {type:'hidden', value:''});
  let disposed = false, serial = 0, busy = false, loaded = false, pages = 1, after = null;
  let selected = '', verified = null, rows = [];
  const status = h('div', {role:'status'}), detail = h('div', {'data-history-attempt-detail':''});
  const select = h('select', {'aria-label':t('exh_attempt'), onchange:()=>{
    if (busy || blocked()) return;
    selected = select.value; field.value = selected; verified = null;
    onChange(); void readSelected();
  }});
  const refresh = button({variant:'ghost', size:'sm', label:t('exh_refresh_attempts'), onclick:()=>void readPages(pages)});
  const more = button({variant:'secondary', size:'sm', label:t('exh_more_attempts'), onclick:()=>void readPages(pages + 1)});
  const element = h('details', {'data-history-attempt-picker':'', hidden:!supported()},
    h('summary', {}, t('exh_choose_attempt')), h('p', {class:'ui-meta'}, t('exh_attempt_help')),
    h('label', {}, t('exh_attempt'), select), status, detail, h('div', {class:'exh-actions'}, refresh, more));
  const current = ticket => {try {alive();} catch {return false;} return !disposed && ticket === serial;};
  const mismatch = () => Error(t('pm_scope_mismatch'));
  function check(row, id = row?.attempt_id) {
    if (!attemptId(id) || row?.attempt_id !== id || row.workspace_id !== workspaceId || row.work_item_id !== workId
      || !Number.isSafeInteger(row.sequence) || row.sequence < 1 || typeof row.stage !== 'string' || !row.stage
      || !Number.isFinite(row.created_at) || row.created_at < 0 || !Number.isFinite(new Date(row.created_at * 1000).getTime())) throw mismatch();
    return row;
  }
  function label(row) {
    const key = `wst_status_${row.stage}`, stage = t(key) === key ? t('exh_attempt_stage_unknown') : t(key);
    return t('exh_attempt_option', {number:row.sequence, time:timeText(row.created_at), stage});
  }
  function choices() {
    select.replaceChildren(h('option', {value:''}, t('exh_work_only')),
      ...rows.map(row=>h('option', {value:row.attempt_id}, label(row.attempt_id === verified?.attempt_id ? verified : row))));
    if (selected && !rows.some(row=>row.attempt_id === selected)) select.append(h('option', {value:selected}, verified ? label(verified) : t('exh_attempt_unavailable')));
    select.value = selected;
  }
  function update() {
    const disabled = busy || blocked();
    select.disabled = disabled; refresh.disabled = disabled || !supported(); more.disabled = refresh.disabled;
    more.hidden = after === null || pages >= 25;
  }
  function clear() {verified = null; rows = []; detail.replaceChildren(); choices();}
  async function selectedDetail() {
    if (!selected) return null;
    if (!attemptId(selected) || !supported()) throw mismatch();
    const data = await api('GET', `${endpoint}/work-attempts/${encodeURIComponent(selected)}`);
    if (data.workspace_id !== workspaceId) throw mismatch();
    return check(data.attempt, selected);
  }
  function display(row) {
    verified = row; choices();
    detail.replaceChildren(...(row ? [h('p', {}, label(row)), h('p', {class:'mono'}, row.attempt_id)] : []));
  }
  function failed(error) {
    clear(); after = null;
    status.replaceChildren(errorBox(error, {within:status}));
  }
  async function readSelected() {
    const ticket = ++serial;
    busy = true; detail.replaceChildren(); status.replaceChildren(); update(); onChange();
    try {
      const row = await selectedDetail();
      if (current(ticket)) display(row);
    } catch (error) {if (current(ticket)) failed(error);}
    finally {if (current(ticket)) {busy = false; update(); onChange();}}
  }
  async function readPages(count) {
    if (busy || disposed || !supported()) return;
    const ticket = ++serial;
    busy = true; clear(); status.replaceChildren(h('p', {}, t('pm_kanban_detail_loading'))); update(); onChange();
    try {
      const found = new Map(); let cursor = 0, next = null, filtered = false, read = 0, lastSequence = 0;
      // Reread earlier pages before appending, so revoked rows do not linger.
      do {
        const data = await api('GET', `${endpoint}/work/${encodeURIComponent(workId)}/attempts?after=${cursor}&limit=20`);
        if (!current(ticket)) return;
        if (data.workspace_id !== workspaceId || data.work_item_id !== workId || !Array.isArray(data.attempts)
          || data.attempts.length > 20 || typeof data.visibility_filtered !== 'boolean') throw mismatch();
        next = data.next_cursor;
        if (next !== null && (!Number.isSafeInteger(next) || next <= cursor)) throw mismatch();
        for (const row of data.attempts) {
          check(row);
          if (row.sequence <= Math.max(lastSequence, cursor) || next !== null && row.sequence > next || found.has(row.attempt_id)) throw mismatch();
          found.set(row.attempt_id, row); lastSequence = row.sequence;
        }
        filtered ||= data.visibility_filtered; cursor = next; read++;
      } while (next !== null && read < Math.min(count, 25));
      const row = await selectedDetail();
      if (!current(ticket)) return;
      rows = [...found.values()]; pages = read; after = next; loaded = true;
      display(row); status.replaceChildren();
      if (!rows.length) status.append(h('p', {class:'ui-meta'}, t('exh_no_attempts')));
      if (filtered) status.append(h('p', {class:'ui-meta'}, t('wst_visibility_filtered')));
      if (next !== null && pages >= 25) status.append(h('p', {class:'ui-meta'}, t('exh_attempt_limit')));
    } catch (error) {if (current(ticket)) failed(error);}
    finally {if (current(ticket)) {busy = false; update(); onChange();}}
  }
  element.addEventListener('toggle', ()=>{if (element.open && !loaded && !busy) void readPages(1);});
  choices(); update();
  return {element, field, update,
    ready:()=>!busy && field.value === selected && (!selected || Boolean(verified && verified.attempt_id === selected && supported())),
    retain:()=>{field.value = selected;},
    restore:async id=>{
      selected = id; field.value = id; choices();
      if (!id) return;
      element.hidden = false; element.open = true;
      if (supported()) await readPages(1); else await readSelected();
    },
    dispose:()=>{disposed = true; serial++; clear(); field.value = ''; element.replaceChildren();},
  };
}
