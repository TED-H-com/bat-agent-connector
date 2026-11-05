import {timeLabel} from './ui/stamps.js';
// Saved text has its own authorization/read lifetime and never feeds live controls.
export function sessionArchivePanel({h, t, api, guard, host, sessionId, when}) {
  let serial = 0, disposed = false, selected = null, next = null, offset = 0, timer = null;
  let pages = [0], pageIndex = 0, painted = null;
  const status = h('div', {role:'status'}), body = h('div', {'data-archive-messages':''}), summary = h('div');
  const versions = h('select', {'aria-label':t('archive_snapshot'), hidden:true,
    onchange:()=>void read(0, versions.value)});
  const newer = h('button', {hidden:true, onclick:()=>void read(pages[pageIndex - 1], selected, 'newer')}, t('archive_newer'));
  const latest = h('button', {hidden:true, onclick:()=>void read(0, selected)}, t('archive_latest'));
  const older = h('button', {hidden:true, onclick:()=>void read(next, selected, 'older')}, t('archive_older'));
  const load = h('button', {onclick:()=>void read()}, t('archive_read'));
  const controls = [load, older, newer, latest, versions];
  const box = h('details', {class:'workspace-evidence', 'data-session-archive':''}, h('summary', {}, t('archive_title')),
    h('p', {class:'ui-meta'}, t('archive_help')), h('div', {class:'actions'}, load, versions), status, summary, body,
    h('div', {class:'actions'}, newer, latest, older));
  const busy = value => {for (const control of controls) control.disabled = value; box.setAttribute('aria-busy', String(value));};
  function clear() {
    serial++; clearTimeout(timer); timer = null; selected = null; next = null; offset = 0;
    pages = [0]; pageIndex = 0; painted = null;
    body.replaceChildren(); summary.replaceChildren(); status.replaceChildren(); versions.replaceChildren();
    versions.hidden = older.hidden = newer.hidden = latest.hidden = true; busy(false);
  }
  const valid = ticket => {
    if (disposed || box.hidden || ticket !== serial) return false;
    try {guard(); return true;} catch {clear(); return false;}
  };
  function poll() {
    clearTimeout(timer);
    if (box.open && selected && !disposed) timer = setTimeout(()=>void read(offset, selected, 'refresh'), 30000);
  }
  async function read(page = 0, snapshot = null, direction = 'reset') {
    if (page == null || disposed) return;
    clearTimeout(timer); timer = null;
    const ticket = ++serial; busy(true);
    try {
      guard();
      const data = await api('GET', `/sessions/${encodeURIComponent(host)}/${encodeURIComponent(sessionId)}/messages?source=retained&last_n=30&max_chars=60000&max_message_chars=16000&offset=${page}${snapshot ? '&snapshot_id='+encodeURIComponent(snapshot) : ''}`);
      if (!valid(ticket)) return;
      if (data.host !== host || data.session_id !== sessionId || data.source !== 'retained' || !Array.isArray(data.messages)
          || !/^[a-f0-9]{64}$/.test(data.snapshot_id) || (snapshot && data.snapshot_id !== snapshot)
          || !Number.isFinite(data.saved_at)
          || !(data.next_offset == null || Number.isSafeInteger(data.next_offset) && data.next_offset > page))
        throw Error('Saved source changed');
      if (direction === 'older') {pages = pages.slice(0, pageIndex + 1); pages.push(page); pageIndex++;}
      else if (direction === 'newer') pageIndex--;
      else if (direction !== 'refresh') {pages = [page]; pageIndex = 0;}
      selected = data.snapshot_id; offset = page; next = data.next_offset;
      const sourceStatus = data.source_status || data.status;
      status.replaceChildren(h('p', {}, timeLabel(t, 'archive_asof', {time:when(data.saved_at * 1000)})),
        h('p', {class:'ui-meta'}, t('archive_partial')),
        h('p', {class:'ui-meta'}, t('archive_counts', {omitted:data.coverage?.omitted_messages || 0, clipped:data.coverage?.clipped_messages || 0})),
        ...(['source_unavailable','storage_limit','source_changed'].includes(sourceStatus) ? [h('p', {}, t('archive_'+sourceStatus))] : []),
        ...(['archive_budget','archive_unconfirmed','backfill_pending'].includes(data.coverage?.reason) ? [h('p', {}, t('archive_'+data.coverage.reason))] : []));
      // Reauthorization of an immutable page must not replace selected text or move its reader.
      const pageKey = `${selected}:${page}`;
      if (painted !== pageKey) {
        summary.replaceChildren(h('h3', {}, t('archive_brief')), ...(data.brief?.items || []).map(item=>h('div', {},
          h('strong', {}, t('archive_'+item.kind)), h('p', {class:'exh-text'}, item.text))));
        body.replaceChildren(...data.messages.map(message=>h('article', {class:'exh-entry', 'data-archive-message':message.id},
          h('strong', {}, t(message.role === 'user' ? 'archive_user' : 'archive_assistant')),
          h('p', {class:'exh-text'}, message.text), message.clipped ? h('small', {}, t('archive_clipped')) : null)));
        painted = pageKey;
      }
      const choices = [...(data.snapshots || [])];
      // A pinned older snapshot remains selectable after it leaves the latest-50 menu.
      if (!choices.some(s=>s.snapshot_id === selected)) choices.push({snapshot_id:selected, saved_at:data.saved_at});
      const options = choices.map(s=>{
        const stamp = when(s.saved_at * 1000);
        return h('option', {value:s.snapshot_id}, stamp instanceof Node ? stamp.textContent : stamp);
      });
      if (versions.options.length !== options.length || options.some((option, index)=>!option.isEqualNode(versions.options[index])))
        versions.replaceChildren(...options);
      versions.value = selected; versions.hidden = versions.options.length < 2;
      older.hidden = next == null; newer.hidden = pageIndex === 0; latest.hidden = page === 0;
    } catch (error) {
      if (!valid(ticket)) return;
      clear();
      status.textContent = t(error.code === 'ARCHIVE_NOT_FOUND' ? 'archive_empty' : 'archive_failure');
    } finally {
      if (!disposed && ticket === serial) {busy(false); poll();}
    }
  }
  box.addEventListener('toggle', () => {if (box.open) poll(); else clear();});
  return {box, clear, dispose(){disposed = true; clear();}};
}
