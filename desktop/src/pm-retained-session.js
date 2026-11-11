import {timeLabel, timeStamp} from './ui/stamps.js';

const keys = ['version', 'server_id', 'host', 'session_id', 'binding', 'snapshot_id'];
export function retainedSessionRef(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.version !== 1
    || Object.keys(value).sort().join(',') !== [...keys].sort().join(',')
    || ['server_id', 'host', 'session_id'].some(key => typeof value[key] !== 'string' || !value[key])
    || ['binding', 'snapshot_id'].some(key => typeof value[key] !== 'string' || !/^[0-9a-f]{64}$/.test(value[key]))) return null;
  return Object.fromEntries(keys.map(key => [key, value[key]]));
}

export function retainedSessionHref(ref) {
  return `#/session/${encodeURIComponent(ref.host)}/${encodeURIComponent(ref.session_id)}?retained_snapshot=${encodeURIComponent(ref.snapshot_id)}`;
}

// Only the exact central selector enters the durable intent. Browsing never runs PM.
export function retainedSessionSelection({h, t, api, guard, caps, thread, authorized}) {
  const value = h('input', {type:'hidden', value:'', 'data-pm-default':''});
  const status = h('div', {role:'status'}), previewBox = h('div'), selectedBox = h('div');
  const sessions = h('select', {'aria-label':t('pcb_saved_choose')}), versions = h('select', {'aria-label':t('archive_snapshot')});
  const search = h('input', {type:'search', 'aria-label':t('pcb_saved_search'), placeholder:t('pcb_saved_search')});
  let request = null, rows = [], cursor = null, preview = null, verified = '', serial = 0, busy = false, dead = false;
  let checked = '', checking = null;
  const supported = () => caps()?.features?.session_archive?.pm_continuation === true;
  const selected = () => {try {return retainedSessionRef(JSON.parse(value.value));} catch {return null;}};
  const fresh = () => !dead && Boolean(thread()) && supported() && authorized() && !request?.locked();
  const current = ticket => {try {guard(); return !dead && ticket === serial && Boolean(thread());} catch {return false;}};
  const error = () => Error(t('pcb_saved_unavailable'));
  const identity = row => JSON.stringify([row.host, row.session_id]);
  const button = (label, onclick) => h('button', {type:'button', class:'secondary', onclick}, t(label));
  const browse = button('pcb_saved_browse', () => void load());
  const more = button('pcb_saved_more', () => void load(true)); more.hidden = true;
  const check = button('pcb_saved_recheck', () => {checked = ''; void recheck();});
  const choose = button('pcb_saved_include', () => {
    try {
      guard(); if (!fresh() || busy || !preview) return;
      const ref = JSON.stringify(preview.retained_session_ref);
      request.set('retained_session', ref); verified = checked = ref; update(); request.update();
    } catch { /* A retired view cannot change the original intent. */ }
  });
  const clear = button('pcb_saved_clear', () => {
    try {guard(); if (!fresh() || busy) return; request.set('retained_session', ''); verified = checked = ''; update(); request.update();}
    catch { /* A retired view cannot change the original intent. */ }
  });
  const selectors = h('div', {class:'pm-fields', hidden:true}, search, sessions, h('div', {class:'actions'}, more), versions);
  const box = h('details', {'data-pm-retained-session':''}, h('summary', {}, t('pcb_saved_title')),
    h('p', {class:'muted'}, t('pcb_saved_help')), value, selectedBox,
    h('div', {class:'actions'}, browse, check, clear), selectors, status, previewBox, choose);

  function navigation() {
    const before = sessions.value, query = search.value.trim().toLocaleLowerCase();
    sessions.replaceChildren(h('option', {value:''}, t('pcb_saved_choose')), ...rows.filter(row =>
      [row.title, row.host, row.session_id].some(text => String(text || '').toLocaleLowerCase().includes(query)))
      .map(row => h('option', {value:identity(row)}, `${row.title || row.session_id} · ${row.host}`)));
    if ([...sessions.options].some(option => option.value === before)) sessions.value = before;
    more.hidden = !cursor;
  }
  function showPreview(data) {
    previewBox.replaceChildren(h('p', {}, timeLabel(t, 'archive_asof', {time:timeStamp(data.saved_at)})),
      h('p', {class:'muted'}, t('archive_partial')),
      h('a', {href:retainedSessionHref(data.retained_session_ref)}, t('pcb_saved_open')));
    versions.replaceChildren(...(data.snapshots || []).map(row => h('option', {value:row.snapshot_id}, timeStamp(row.saved_at).textContent)));
    if (![...versions.options].some(option => option.value === data.snapshot_id))
      versions.append(h('option', {value:data.snapshot_id}, timeStamp(data.saved_at).textContent));
    versions.value = data.snapshot_id; versions.hidden = versions.options.length < 2;
  }
  async function read(host, sessionId, snapshotId = null, expected = null) {
    if (busy || !fresh()) return;
    const ticket = ++serial; busy = true; preview = null; previewBox.replaceChildren(); status.textContent = t('pcb_saved_loading'); update();
    try {
      guard();
      const data = await api('GET', `/sessions/${encodeURIComponent(host)}/${encodeURIComponent(sessionId)}/messages?source=retained&last_n=1&max_chars=2000&max_message_chars=2000${snapshotId ? '&snapshot_id='+encodeURIComponent(snapshotId) : ''}`);
      if (!current(ticket) || request.locked()) return;
      const ref = retainedSessionRef(data.retained_session_ref);
      if (!ref || data.host !== host || data.session_id !== sessionId || data.source !== 'retained' || !Number.isFinite(data.saved_at)
        || ref.host !== host || ref.session_id !== sessionId || ref.snapshot_id !== data.snapshot_id
        || snapshotId && snapshotId !== ref.snapshot_id || expected && JSON.stringify(ref) !== expected) throw error();
      preview = {...data, retained_session_ref:ref};
      if (expected) verified = expected;
      showPreview(preview); status.replaceChildren();
    } catch (failure) {
      if (!current(ticket)) return;
      preview = null; verified = ''; previewBox.replaceChildren(); versions.replaceChildren();
      status.textContent = t(failure.code === 'ARCHIVE_NOT_FOUND' ? 'archive_empty' : 'pcb_saved_unavailable');
    } finally {
      if (!dead && ticket === serial) {busy = false; update(); request?.update();}
    }
  }
  async function recheck() {
    const ref = selected();
    if (!ref || !fresh() || busy || checked === value.value) return;
    checked = value.value;
    await read(ref.host, ref.session_id, ref.snapshot_id, value.value);
  }
  async function load(append = false) {
    if (busy || !fresh()) return;
    const ticket = ++serial, after = append ? cursor : null;
    busy = true; status.textContent = t('pcb_saved_loading'); update();
    try {
      guard();
      const data = await api('GET', '/sessions?limit=50&order=id&include_gone=true' + (after ? '&cursor='+encodeURIComponent(after) : ''));
      if (!current(ticket) || request.locked()) return;
      if (!Array.isArray(data.sessions) || data.sessions.some(row => !row || typeof row.host !== 'string' || !row.host
        || typeof row.session_id !== 'string' || !row.session_id)
        || data.next_cursor != null && (typeof data.next_cursor !== 'string' || !data.next_cursor || data.next_cursor === after)) throw error();
      const seen = new Map((append ? rows : []).map(row => [identity(row), row]));
      for (const row of data.sessions) seen.set(identity(row), row);
      rows = [...seen.values()]; cursor = data.next_cursor || null; selectors.hidden = false; navigation();
      status.textContent = rows.length ? t('pcb_saved_inventory') : t('pcb_saved_empty');
    } catch {
      if (!current(ticket)) return;
      rows = []; cursor = null; navigation(); preview = null; previewBox.replaceChildren(); selectors.hidden = true;
      status.textContent = t('pcb_saved_unavailable');
    } finally {if (!dead && ticket === serial) {busy = false; update(); request?.update();}}
  }
  function update() {
    const locked = request?.locked(), ref = selected();
    box.hidden = !supported() && !value.value;
    for (const control of [browse, sessions, versions, search, more, choose, check, clear]) control.disabled = busy || !fresh();
    choose.hidden = !preview || JSON.stringify(preview.retained_session_ref) === value.value;
    check.hidden = !value.value || locked; clear.hidden = !value.value;
    selectedBox.replaceChildren();
    if (value.value) selectedBox.append(h('p', {class:'muted'}, t(locked ? 'pcb_saved_original' : verified === value.value ? 'pcb_saved_selected' : 'pcb_saved_recheck_needed')));
    if (!thread()) {
      serial++; busy = false; preview = null; verified = ''; checked = ''; rows = []; cursor = null;
      previewBox.replaceChildren(); status.replaceChildren(); sessions.replaceChildren(); versions.replaceChildren(); selectors.hidden = true;
    } else if (ref && fresh() && checked !== value.value && !busy && !checking) {
      checking = Promise.resolve().then(recheck).finally(() => {checking = null;});
    }
  }
  sessions.addEventListener('change', () => {
    const row = rows.find(item => identity(item) === sessions.value);
    if (row) void read(row.host, row.session_id);
  });
  versions.addEventListener('change', () => {if (preview) void read(preview.host, preview.session_id, versions.value);});
  search.addEventListener('input', navigation);
  search.addEventListener('keydown', event => {if (event.key === 'Enter') event.preventDefault();});
  return {box, value, bind(form) {request = form; update();}, update,
    valid:() => !value.value || Boolean(selected() && verified === value.value && supported()),
    params(values) {
      if (!values.retained_session) return {};
      const ref = retainedSessionRef(JSON.parse(values.retained_session)); if (!ref) throw error();
      return {retained_session_ref:ref};
    }, dispose() {dead = true; serial++; preview = null; previewBox.replaceChildren(); status.replaceChildren();}};
}
