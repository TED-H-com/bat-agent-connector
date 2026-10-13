import {disclosure, emptyState, iconButton, inspector, pageHeader, segmented, skeleton, statusPill} from './ui/components.js';
import {reasonText} from './global-labels.js';
import {inboxRow} from './global-screens.js';
// Each list has its own cursor and loaded-page count. No client-invented total or
// completion state, and no read receipts are written by visiting this view.
export async function attentionView({main, h, t, api, guard, caps, storageKey, route, onEvents, debounceRefresh,
  errorBox, sessionRow, workItemRow, opRow, onPending = () => {}}) {
  const unread = caps?.features?.work_item_reads?.version === 1;
  const saved = sessionStorage.getItem(storageKey) || sessionStorage.getItem('batc.tab');
  const tab = saved === 'confirm' || saved === 'active' ? 'active' : saved === 'unread' && unread ? 'unread' : 'needs';
  const tabs = segmented({
    label: t('nav_home'), value: tab,
    options: ['needs', 'active', ...(unread ? ['unread'] : [])].map(value => ({value, label: t('attention_tab_' + value)})),
    onChange: value => {guard(); sessionStorage.setItem(storageKey, value); route();},
  });
  tabs.classList.add('global-inbox-tabs');
  const empty = emptyState({
    title: t('empty_needs_you'),
    action: h('div', {class: 'actions'},
      h('a', {class: 'ui-button secondary ui-sm', href: '#/projects'}, t('sessions_projects')),
      caps?.scopes?.includes('start') && caps?.actions?.some(a => a.action === 'session.start' && a.allowed === true)
        ? h('a', {class: 'ui-button primary ui-sm', href: '#/start'}, t('start_title_page')) : null),
  });
  empty.hidden = true;
  const notes = disclosure({
    label: t('global_details'),
    key: storageKey + '.notes',
    body: h('div', {}, ...['replies', 'completion', 'problems', 'hosts', 'active', 'unread'].map(key =>
      h('p', {}, h('strong', {}, t('attention_' + key)), ' ', t('attention_' + key + '_note'))),
      h('p', {}, t('empty_next_work'))),
  });
  let sheet = null;
  const open = (key, title, nodes) => {
    if (!sheet?.isConnected || sheet.dataset.inboxKey !== key) {
      sheet?.dispose();
      const node = inspector({title, onClose: () => {if (sheet === node) sheet = null;}});
      node.dataset.inboxKey = key;
      sheet = node;
      main.append(node);
    }
    sheet.body.replaceChildren(...[].concat(nodes).flat().filter(node => node != null && node !== false));
  };
  const sections = [];
  const reconcile = () => {
    if (!sheet) return;
    const alive = sections.some(section => section.list.querySelector(`[data-inbox-key="${CSS.escape(sheet.dataset.inboxKey)}"]`));
    if (!alive) sheet.dispose();
  };
  const add = (key, path, field, row, options = {}) => {
    const list = h('div', {class: 'global-inbox-list'});
    const status = h('div', {role: 'status'});
    const badge = h('span', {class: 'global-group-count'});
    const count = h('span', {class: 'muted', 'data-shown': ''});
    const more = h('button', {class: 'secondary', hidden: true}, t('load_more'));
    const viewAll = key === 'problems' ? h('a', {class: 'global-view-all', href: '#/operations/attention'}, t('global_view_all')) : null;
    const quiet = key === 'active' ? h('a', {href: '#/operations/active'}, t('operations_view_active')) : null;
    const section = h('section', {class: 'global-group', 'aria-label': t('attention_' + key), 'data-inbox-group': key},
      h('div', {class: 'global-group-head'}, h('h2', {}, t('attention_' + key)), badge, viewAll),
      status, list, h('div', {class: 'global-group-foot'}, count, more, quiet));
    const state = {key, path, field, row, ...options, section, list, status, badge, count, more, pages: 1, size: null, busy: false};
    more.onclick = () => refresh(state, state.pages + 1).catch(() => {});
    sections.push(state);
    return section;
  };
  const hostRow = (x, present) => inboxRow({
    h, key: x.host, title: x.host, href: `#/host/${encodeURIComponent(x.host)}`,
    meta: x.error || reasonText(x.stale_reason),
    pill: statusPill('stale'),
    action: h('a', {class: 'ui-button secondary ui-sm', href: `#/host/${encodeURIComponent(x.host)}`}, t('global_review')),
    detailsLabel: t('global_details_for', {title: x.host}),
    onOpen: () => present(x.host, x.host, [
      h('p', {}, reasonText(x.stale_reason) || t('reason_unavailable')),
      disclosure({label: t('global_details'), body: h('dl', {class: 'kv'},
        h('dt', {}, t('global_code')), h('dd', {}, h('code', {}, x.stale_reason || x.error || '—')))}),
    ]),
  });
  const approvals = caps?.actions?.some(action => action.action === 'session.approve_pending' && action.allowed === true)
    ? h('section', {class: 'global-group', 'aria-label': t('global_approvals')},
      h('div', {class: 'global-group-head'}, h('h2', {}, t('global_approvals'))),
      h('div', {class: 'global-inbox-list'}, inboxRow({
        h, key: 'approvals', title: t('bulk_title'), href: '#/approvals',
        action: h('a', {class: 'ui-button secondary ui-sm', href: '#/approvals'}, t('global_review')),
      }))) : null;
  main.append(...[pageHeader({
    title: t('nav_home'),
    actions: [iconButton({icon: 'refresh-cw', label: t('global_refresh'), onClick: () => refresh().catch(() => {})})],
  }), tabs, empty, ...(tab === 'needs' ? [
    approvals,
    add('replies', '/sessions?attention=true&limit=50', 'sessions', sessionRow),
    add('completion', '/work-items?pending=true&limit=50', 'work_items', workItemRow),
    add('problems', '/operations?status=needs_attention,uncertain&limit=50', 'operations', opRow, {cursor: 'next_before', param: 'before'}),
    add('hosts', '/hosts', 'hosts', hostRow, {filter: x => x.stale}),
  ] : tab === 'active' ? [
    add('active', '/operations?status=accepted,running,waiting_checks,waiting_external&limit=50', 'operations', opRow,
      {cursor: 'next_before', param: 'before'}),
  ] : [add('unread', '/work-items?unread=true&limit=50', 'work_items', workItemRow)]), notes].filter(node => node != null && node !== false));
  let serial = Promise.resolve();
  const load = async (section, wanted) => {
    guard(); section.busy = true; section.more.disabled = true;
    if (section.size === null) section.status.replaceChildren(skeleton(3));
    const focusKey = section.list.contains(document.activeElement)
      ? document.activeElement.closest('[data-inbox-key]')?.dataset.inboxKey : null;
    const focusLabel = document.activeElement?.getAttribute('aria-label') || document.activeElement?.textContent || '';
    try {
      let cursor = null, pages = 0;
      const items = new Map(), seen = new Set();
      do {
        const path = section.path + (cursor == null ? '' : `&${section.param || 'cursor'}=${encodeURIComponent(cursor)}`);
        const data = await api('GET', path); guard();
        if (!Array.isArray(data[section.field])) throw new Error(t('attention_invalid'));
        for (const row of data[section.field]) {
          if (!section.filter || section.filter(row)) items.set(row.work_item_id || row.operation_id ||
            (row.session_id ? JSON.stringify([row.host, row.session_id]) : row.host), row);
        }
        cursor = data[section.cursor || 'next_cursor'] ?? null; pages++;
        if (cursor !== null && seen.has(cursor)) throw new Error(t('attention_invalid'));
        seen.add(cursor);
      } while (cursor !== null && pages < wanted);
      section.pages = pages; section.size = items.size; section.partial = cursor !== null;
      section.list.replaceChildren(...(items.size ? [...items.values()].map(item => section.row(item, open)) : []));
      section.badge.hidden = Boolean(section.partial);
      section.badge.textContent = section.partial ? '' : String(items.size);
      section.count.hidden = !section.partial;
      section.count.textContent = section.partial ? t('global_shown', {count: items.size}) : '';
      section.status.replaceChildren();
      section.more.hidden = cursor === null;
      section.section.hidden = items.size === 0;
      section.section.dataset.fresh = 'true';
      if (focusKey) {
        const row = section.list.querySelector(`[data-inbox-key="${CSS.escape(focusKey)}"]`);
        const next = [...(row?.querySelectorAll('a, button') || [])].find(node =>
          (node.getAttribute('aria-label') || node.textContent) === focusLabel);
        (next || row)?.focus({preventScroll: true});
      }
      reconcile();
      if (tab === 'needs') onPending(sections.every(item => item.section.dataset.fresh === 'true')
        ? {count: sections.reduce((sum, item) => sum + item.size, 0), partial: sections.some(item => item.partial)} : {});
    } catch (error) {
      guard(); section.section.dataset.fresh = 'false'; onPending({});
      if ([403, 404].includes(error.status)) {
        section.list.replaceChildren(); section.count.replaceChildren(); section.badge.textContent = '';
        section.size = null; section.more.hidden = true; section.section.hidden = false;
        reconcile();
      }
      section.status.replaceChildren(errorBox(error), h('p', {class: 'muted'}, t('attention_not_updated')));
      throw error;
    } finally {
      section.busy = false; section.more.disabled = false;
    }
  };
  const refresh = (only, wanted) => {
    const work = serial.catch(() => {}).then(async () => {
      guard();
      const results = await Promise.allSettled((only ? [only] : sections).map(item => load(item, only ? wanted : item.pages)));
      guard();
      const settled = sections.every(item => item.size === 0 && item.section.dataset.fresh === 'true');
      empty.hidden = tab !== 'needs' || Boolean(approvals) || !settled;
      const failed = results.find(item => item.status === 'rejected');
      if (failed) throw failed.reason;
    });
    serial = work;
    return work;
  };
  await refresh().catch(() => {});
  // Initial route loading may finish after the user has navigated away. Let the
  // router retire that view; do not turn a superseded load into a login failure.
  try {guard();} catch {return;}
  return onEvents(debounceRefresh(() => refresh(), 500));
}
