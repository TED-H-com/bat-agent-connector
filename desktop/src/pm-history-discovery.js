import {button} from './ui/components.js';

const linkId = value => typeof value === 'string' && /^hxl_[0-9a-f]{32}$/.test(value);
const workId = value => typeof value === 'string' && /^wi_[0-9a-f]{20}$/.test(value);
const exact = (value, keys) => value && !Array.isArray(value) && typeof value === 'object'
  && Object.keys(value).sort().join(',') === [...keys].sort().join(',');
const invalid = () => Error('HISTORY_CLEANUP_PAGE_INVALID');

// Cursor IDs need not occur among visible rows, nor be lexically increasing.
export function cleanupPage(data, work, ids = new Set(), cursors = new Set()) {
  if (!exact(data,['links','next_after']) || !workId(work) || !Array.isArray(data.links) || data.links.length > 20
    || data.next_after !== null && (!linkId(data.next_after) || cursors.has(data.next_after))) throw invalid();
  const seen = new Set(ids), links = [];
  for (const row of data.links) {
    if (!exact(row,['link_id','work_item_id','version']) || !linkId(row.link_id) || row.work_item_id !== work
      || !Number.isSafeInteger(row.version) || row.version < 1 || seen.has(row.link_id)) throw invalid();
    seen.add(row.link_id);
    links.push({link_id:row.link_id,work_item_id:row.work_item_id,version:row.version});
  }
  return {links,next_after:data.next_after};
}

export function historyDiscovery({h,t,api,alive,workspaceId,enabled,errorBox,onChange}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  let disposed = false, opened = false, epoch = 0, serial = 0, selected = '', busy = false, workBusy = false;
  let after = null, workAfter = null, pages = 0, loaded = false, review = false;
  const rows = new Map(), works = new Map(), ids = new Set(), cursors = new Set();
  const status = h('div',{role:'status'}), workStatus = h('div',{role:'status'});
  const select = h('select',{'aria-label':t('exh_work'),onchange:()=>{
    selected = works.has(select.value) ? select.value : '';
    clearPages(); status.replaceChildren(); sync(); onChange();
  }});
  const read = button({variant:'secondary',size:'sm',label:t('exh_cleanup_read_ids'),onclick:()=>void readPage(true,loaded)});
  const more = button({variant:'secondary',size:'sm',label:t('exh_more_links'),onclick:()=>void readPage(false,review)});
  const workMore = button({variant:'ghost',size:'sm',label:t('exh_more_work'),onclick:()=>void readWorks(false)});
  const retryWorks = button({variant:'ghost',size:'sm',label:t('exh_choose_work'),onclick:()=>void readWorks(true)});
  retryWorks.hidden = true;
  const chooser = h('div',{'data-history-cleanup-chooser':'',hidden:true},
    h('p',{class:'ui-meta'},t('exh_cleanup_discover_help')),h('label',{},t('exh_work'),select),workStatus,
    h('div',{class:'exh-actions'},workMore,retryWorks,read,more),status);
  const discover = button({variant:'secondary',size:'sm',label:t('exh_cleanup_discover'),onclick:()=>{
    if (!available() || opened) return;
    opened = true; chooser.hidden = false; discover.setAttribute('aria-expanded','true');
    void readWorks(true); select.focus();
  }});
  discover.setAttribute('aria-expanded','false');
  const element = h('div',{'data-history-discovery':'',hidden:true},h('div',{class:'exh-actions'},discover),chooser);
  function available() {try {alive();} catch {return false;} return !disposed && enabled();}
  const current = ticket => available() && opened && ticket === epoch;
  function clearPages() {serial++; busy = false; after = null; pages = 0; loaded = review = false; rows.clear(); ids.clear(); cursors.clear();}
  function sync() {
    const focused = document.activeElement;
    read.textContent = t(loaded ? 'exh_cleanup_read_again' : 'exh_cleanup_read_ids');
    read.disabled = !available() || !selected;
    read.setAttribute('aria-disabled',String(busy));
    more.hidden = !loaded || after === null || pages >= 25; more.setAttribute('aria-disabled',String(busy));
    workMore.hidden = workAfter === null;
    for (const control of [workMore,retryWorks]) control.setAttribute('aria-disabled',String(workBusy));
    status.setAttribute('aria-busy',String(busy));
    if (opened && available()) {
      if (focused === more && more.hidden) read.focus({preventScroll:true});
      if (focused === workMore && workMore.hidden) select.focus({preventScroll:true});
    }
  }
  function choices() {
    select.replaceChildren(h('option',{value:''},t('exh_select_work')),
      ...[...works.values()].map(card=>h('option',{value:card.work_item_id},card.work.title)));
    select.value = selected;
  }
  async function readWorks(reset) {
    if (!available() || !opened || workBusy) return;
    const ticket = epoch, cursor = reset ? 0 : workAfter;
    if (!reset && cursor === null) return;
    workBusy = true;
    if (document.activeElement === retryWorks) select.focus({preventScroll:true});
    retryWorks.hidden = true; sync(); workStatus.replaceChildren();
    try {
      const data = await api('GET',`${endpoint}/kanban?after=${cursor}&limit=20`);
      if (!current(ticket)) return;
      if (data.workspace_id !== workspaceId || !Array.isArray(data.cards) || data.cards.length > 20
        || data.next_cursor !== null && (!Number.isSafeInteger(data.next_cursor) || data.next_cursor <= cursor)) throw invalid();
      for (const card of data.cards) if (!workId(card?.work_item_id) || card.work?.work_item_id !== card.work_item_id
        || typeof card.work.title !== 'string' || card.binding?.workspace_id !== workspaceId
        || card.binding.work_item_id !== card.work_item_id || card.work.project_id !== card.binding.project_id
        || !/^pmwb_[0-9a-f]{32}$/.test(card.binding.binding_id)) throw invalid();
      if (reset) works.clear();
      for (const card of data.cards) works.set(card.work_item_id,card);
      workAfter = data.next_cursor;
      if (selected && !works.has(selected)) {selected = ''; clearPages(); onChange();}
      choices();
      if (!works.size) workStatus.append(h('p',{class:'ui-meta'},t('exh_no_work')));
    } catch (error) {
      if (current(ticket)) {
        works.clear(); selected = ''; workAfter = null; clearPages(); choices(); onChange();
        retryWorks.hidden = false;
        workStatus.replaceChildren(errorBox(error,{summary:t('exh_cleanup_read_error'),within:workStatus,sourceRefresh:false}));
      }
    } finally {if (current(ticket)) {workBusy = false; sync();}}
  }
  async function readPage(reset, reviewVersion) {
    if (!available() || !opened || !selected || busy || !reset && (after === null || pages >= 25)) return;
    if (reset) {clearPages(); review = reviewVersion; onChange();}
    const ticket = epoch, request = ++serial, work = selected, cursor = reset ? null : after;
    busy = true; status.replaceChildren(); sync();
    try {
      const data = await api('GET',`${endpoint}/work/${encodeURIComponent(work)}/external-links/cleanup?limit=20${cursor ? `&after=${encodeURIComponent(cursor)}` : ''}`);
      if (!current(ticket) || request !== serial || selected !== work) return;
      const page = cleanupPage(data,work,ids,cursors);
      for (const row of page.links) {rows.set(row.link_id,row); ids.add(row.link_id);}
      after = page.next_after; if (after !== null) cursors.add(after);
      pages++; loaded = true;
      status.replaceChildren(h('p',{class:'ui-meta'},t(rows.size ? 'exh_cleanup_discovered' : after === null ? 'exh_cleanup_none' : 'exh_cleanup_empty_page')));
      if (pages >= 25 && after !== null) status.append(h('p',{class:'ui-meta'},t('exh_cleanup_read_limit')));
      onChange('page');
    } catch (error) {
      if (current(ticket) && request === serial) {
        clearPages(); onChange();
        status.replaceChildren(errorBox(error,{summary:t('exh_cleanup_read_error'),within:status,sourceRefresh:false}));
      }
    } finally {if (current(ticket) && (request === serial || !busy)) {busy = false; sync();}}
  }
  function reset() {
    epoch++; opened = workBusy = false; selected = ''; workAfter = null; works.clear(); clearPages(); choices();
    chooser.hidden = true; discover.setAttribute('aria-expanded','false'); status.replaceChildren(); workStatus.replaceChildren(); sync(); onChange();
  }
  return {element,rows,reviewVersion:()=>review,
    has:id=>available() && opened && rows.has(id),
    invalidate() {clearPages(); sync(); onChange();},
    forget(id) {rows.delete(id); serial++; busy = false; sync();},
    focus:()=>{(loaded ? read : discover).focus({preventScroll:true});},
    update() {element.hidden = !available(); if (element.hidden) reset(); else sync();},
    reset,dispose() {disposed = true; reset();},
  };
}
