import {h} from './ui/dom.js';
import {t} from './i18n.js';
import {button, segmented, statusPill, inspector, disclosure, emptyState, skeleton, inlineError} from './ui/components.js';
import {viewState} from './ui/view-state.js';
import {reasonLabel} from './ui/ui-strings.js';
import {DAY, ZOOM, firstLine, dayNumber, spans, milestoneDelta, readSchedule, scheduleTree} from './pm-schedule-model.js';
import {render as renderInspector, rowStatus} from './pm-schedule-inspector.js';

export function workspaceSchedule({api, alive, namespace, workspaceId, canRead}) {
  const preference = viewState(namespace, workspaceId, 'schedule');
  let zoom = Object.hasOwn(ZOOM, preference.zoom) ? preference.zoom : 'week';
  const collapsed = new Set(preference.collapsed || []);
  let selected = preference.selected || null, active = false, serial = 0, data = null, panel = null, panelSignature = '', signature = '';
  let domain, scale, visible = [], focusId = null;
  const viewport = h('div', {class: 'schedule-viewport', tabindex: 0, role: 'region', 'aria-label': t('sched_timeline')});
  const chart = h('div', {class: 'schedule-chart'}), meta = h('div', {class: 'schedule-meta', 'aria-live': 'polite'});
  const unscheduled = h('div', {class: 'schedule-unscheduled'}), error = h('div');
  const main = h('div', {class: 'schedule-main'}, meta, error, viewport, unscheduled);
  viewport.append(chart);
  const legend = h('div', {class: 'schedule-legend', 'aria-label': t('sched_legend')},
    ...['baseline', 'forecast', 'actual', 'milestone'].map(kind => h('span', {},
      h('i', {class: `schedule-swatch ${kind}`, 'aria-hidden': 'true'}, kind === 'milestone' ? '◆' : null), t('sched_' + kind))));
  const zoomControl = segmented({label: t('sched_zoom'), options: Object.keys(ZOOM).map(value => ({value, label: t('sched_' + value)})), value: zoom,
    onChange: value => {saveScroll(); zoom = value; preference.zoom = zoom; draw();}});
  const toolbar = h('div', {class: 'schedule-toolbar'}, zoomControl,
    button({label: t('sched_today'), variant: 'ghost', onClick: today}), legend);
  const layout = h('div', {class: 'schedule-layout'}, main);
  const element = h('section', {'data-pm-schedule': '', class: 'pm-schedule'}, toolbar, layout);
  viewport.addEventListener('scroll', saveScroll, {passive: true});
  function remember() {Object.assign(preference, {selected, zoom, collapsed: [...collapsed].slice(0, 1000)});}
  function saveScroll() {
    if (!domain || !viewport.clientWidth) return;
    preference.anchor = domain.start + viewport.scrollLeft / scale;
    preference.top = viewport.scrollTop;
  }
  function restoreScroll() {
    viewport.scrollLeft = Math.max(0, ((preference.anchor ?? domain.start) - domain.start) * scale);
    viewport.scrollTop = preference.top || 0;
  }
  function today() {
    if (!domain) return;
    const x = (dayNumber(new Date().toISOString()) - domain.start) * scale;
    viewport.scrollLeft = Math.max(0, x - Math.max(0, viewport.clientWidth - 328) / 2);
    for (const mini of chart.querySelectorAll('.schedule-lane')) mini.scrollLeft = Math.max(0, x - mini.clientWidth / 2);
    saveScroll();
  }
  function closePanel(clear = true) {
    const old = selected; if (!clear) selected = null;
    panel?.close(); panel = null; panelSignature = '';
    if (!clear) {selected = old; remember();}
  }
  function showPanel() {
    const row = data?.rows.find(row => row.id === selected);
    if (!row || !active) {closePanel(!row); return;}
    const next = JSON.stringify([row, data.rows.filter(value => value.parent_id === row.id || value.kind === 'requirement'), data.unknown, data.baseline, data.forecast]);
    if (panel && next === panelSignature) return;
    if (!panel) {
      panel = inspector({title: firstLine(row), onClose: () => {
        const previous = selected; panel = null; panelSignature = ''; selected = null; remember(); markSelection();
        if (active && previous) element.querySelector(`[data-schedule-select="${CSS.escape(previous)}"]`)?.focus({preventScroll: true});
      }}); panel.dataset.scheduleInspector = ''; layout.append(panel);
    }
    panel.querySelector('h2').textContent = firstLine(row);
    const opened = [...panel.body.querySelectorAll('details')].map(node => node.open), scroll = panel.scrollTop;
    const focus = panel.contains(document.activeElement) ? document.activeElement?.getAttribute('data-schedule-discuss') : null;
    panel.body.replaceChildren(renderInspector(row, {workspaceId, data})); panelSignature = next;
    [...panel.body.querySelectorAll('details')].forEach((node, i) => {if (opened[i] !== undefined) node.open = opened[i];});
    panel.scrollTop = scroll;
    if (focus !== null) panel.querySelector('[data-schedule-discuss]')?.focus({preventScroll: true});
  }
  function markSelection() {
    for (const node of element.querySelectorAll('[data-schedule-select]')) {
      const current = node.dataset.scheduleSelect === selected;
      node.setAttribute('aria-selected', String(current));
      node.closest('.schedule-row')?.classList.toggle('selected', current);
    }
  }
  function select(id, opener) {
    opener?.focus({preventScroll: true});
    if (panel && id !== selected) closePanel();
    selected = id; focusId = id; remember(); markSelection(); showPanel();
  }
  const label = row => row.kind === 'group' ? t('sched_unassigned') : firstLine(row);
  function toggle(id) {collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id); remember(); draw();}
  function focusRow(id) {
    focusId = id;
    for (const node of chart.querySelectorAll('[role=treeitem]')) node.tabIndex = node.dataset.scheduleSelect === id ? 0 : -1;
    chart.querySelector(`[data-schedule-select="${CSS.escape(id)}"]`)?.focus({preventScroll: true});
  }
  function keydown(event, item, index) {
    if (event.target.closest('button')) return;
    const {row, expandable, parent} = item;
    const keys = ['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'Enter', ' '];
    if (!keys.includes(event.key)) return; event.preventDefault();
    if (event.key === 'Enter' || event.key === ' ') {if (row.kind === 'group') toggle(row.id); else select(row.id, event.currentTarget); return;}
    if (event.key === 'ArrowRight') {if (expandable && collapsed.has(row.id)) {toggle(row.id); focusRow(row.id);} else if (expandable) focusRow(visible[index + 1]?.row.id); return;}
    if (event.key === 'ArrowLeft') {if (expandable && !collapsed.has(row.id)) {toggle(row.id); focusRow(row.id);} else if (parent) focusRow(parent); return;}
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? visible.length - 1 : Math.max(0, Math.min(visible.length - 1, index + (event.key === 'ArrowUp' ? -1 : 1)));
    focusRow(visible[next]?.row.id);
  }
  function track(row, axis = false) {
    const node = h('div', {class: 'schedule-track' + (axis ? ' schedule-axis' : ''), 'data-schedule-track': row?.id});
    node.style.width = `${(domain.end - domain.start + 1) * scale}px`;
    const todayDay = dayNumber(new Date().toISOString());
    if (zoom === 'day') for (let date = domain.start; date <= domain.end; date++) {
      if ([0, 6].includes(new Date(date * DAY).getUTCDay())) {
        const weekend = h('span', {class: 'schedule-weekend', 'aria-hidden': 'true'}); weekend.style.left = `${(date - domain.start) * scale}px`; weekend.style.width = `${scale}px`; node.append(weekend);
      }
    }
    if (axis) {
      const step = zoom === 'day' ? 1 : zoom === 'week' ? 7 : 30;
      const ticks = Math.max(step, Math.ceil((domain.end - domain.start) / 300));
      for (let date = domain.start; date <= domain.end; date += ticks) {
        const text = new Intl.DateTimeFormat(navigator.language, {month: 'short', day: zoom === 'month' ? undefined : 'numeric', timeZone: 'UTC'}).format(new Date(date * DAY));
        const tick = h('span', {class: 'schedule-tick'}, text); tick.style.left = `${(date - domain.start) * scale}px`; node.append(tick);
      }
    } else if (row) {
      const rowSpans = spans(row);
      for (const span of rowSpans) {
        const start = span.start ?? span.end, end = span.end ?? start;
        const marker = row.kind === 'milestone';
        const bar = h('span', {class: `schedule-bar ${span.kind}${marker ? ' milestone' : ''}${span.open ? ' open' : ''}${row.status === 'blocked' ? ' blocked' : ''}`,
          'data-schedule-bar': span.kind, 'data-open': span.open ? 'true' : null,
          title: `${t('sched_' + span.kind)} · ${row[span.kind].start || '—'} → ${span.open ? t('sched_open') : row[span.kind].end || '—'}`}, marker ? '◆' : null);
        bar.style.left = `${(start - domain.start) * scale}px`;
        if (!marker) bar.style.width = `${span.open ? 44 : Math.max(8, (end - start + 1) * scale)}px`;
        if (span.open) bar.append(h('span', {class: 'schedule-bar-label'}, t('sched_open')));
        else if (['waiting', 'blocked'].includes(row.status)) bar.append(h('span', {class: 'schedule-bar-label'}, t('ui_status_' + row.status)));
        node.append(bar);
      }
      const delta = milestoneDelta(row);
      if (row.kind === 'milestone' && delta !== null && delta !== 0) {
        const value = h('span', {class: 'schedule-delta', 'data-schedule-delta': ''}, t('sched_delta', {n: delta > 0 ? '+' + delta : delta}));
        value.style.left = `${(dayNumber(row.forecast.start) - domain.start) * scale + 20}px`; node.append(value);
      }
    }
    const line = h('span', {class: 'schedule-today', 'aria-label': axis ? t('sched_today') : null, 'aria-hidden': axis ? null : 'true'});
    line.style.left = `${(todayDay - domain.start) * scale}px`; node.append(line); return node;
  }
  function draw() {
    if (!data) return;
    const focused = chart.contains(document.activeElement) ? document.activeElement.closest('[data-schedule-select]')?.dataset.scheduleSelect : null;
    const now = dayNumber(new Date().toISOString());
    domain = {start: Math.min(data.range?.start ?? now, now) - 3, end: Math.max(data.range?.end ?? now, now) + 5};
    scale = Math.min(ZOOM[zoom], 120000 / (domain.end - domain.start + 1));
    chart.style.setProperty('--schedule-width', `${(domain.end - domain.start + 1) * scale}px`);
    visible = scheduleTree(data.rows, collapsed);
    const axis = h('div', {class: 'schedule-row schedule-head'}, h('div', {class: 'schedule-tree-cell'}, t('sched_item')), h('div', {class: 'schedule-lane'}, track(null, true)));
    const tree = h('div', {role: 'tree', 'aria-label': t('sched_tree')});
    visible.forEach((item, index) => {
      const {row, depth, expandable} = item;
      const expand = expandable ? button({icon: 'chevron-right', variant: 'ghost', label: null, 'aria-label': t(collapsed.has(row.id) ? 'ui_expand' : 'ui_collapse', {title: label(row)}),
        tabindex: -1, 'aria-expanded': String(!collapsed.has(row.id)), onClick: event => {event.stopPropagation(); toggle(row.id); focusRow(row.id);}}) : h('span', {class: 'schedule-indent'});
      const dates = ['baseline', 'forecast', 'actual'].filter(key => row[key]).map(key => `${t('sched_' + key)} ${row[key].start?.slice(0, 10) || '—'}${row[key].end && row[key].end !== row[key].start ? ' → ' + row[key].end.slice(0, 10) : ''}`);
      const cell = h('div', {class: 'schedule-tree-cell', role: 'treeitem', 'aria-level': depth, 'aria-expanded': expandable ? String(!collapsed.has(row.id)) : null,
        'aria-label': label(row), tabindex: (focusId || selected || visible[0]?.row.id) === row.id ? 0 : -1,
        'data-schedule-select': row.id, onclick: event => row.kind === 'group' ? toggle(row.id) : select(row.id, event.currentTarget), onkeydown: event => keydown(event, item, index)},
        expand, h('span', {class: 'schedule-tree-copy'}, h('span', {class: 'schedule-title', title: row.title}, label(row)), h('span', {class: 'schedule-date-range'}, dates.join(' · ') || t('sched_no_dates'))),
        row.kind === 'work' || row.kind === 'attempt' ? rowStatus(row, data) : null);
      cell.style.paddingLeft = `${8 + (depth - 1) * 16}px`;
      const lane = h('div', {class: 'schedule-lane', tabindex: 0, role: 'region', 'aria-label': `${t('sched_timeline')} · ${label(row)}`}, track(row));
      lane.addEventListener('scroll', () => {if (lane.clientWidth) {preference.mini ||= {}; preference.mini[row.id] = domain.start + lane.scrollLeft / scale;}});
      const line = h('div', {class: 'schedule-row', 'data-schedule-row': row.id}, cell, lane); tree.append(line);
    });
    chart.replaceChildren(axis, tree);
    if (!visible.length) chart.replaceChildren(emptyState({title: t('sched_empty')}));
    restoreScroll(); markSelection();
    for (const lane of chart.querySelectorAll('.schedule-lane')) {
      const id = lane.firstElementChild?.dataset.scheduleTrack;
      if (preference.mini?.[id] !== undefined) lane.scrollLeft = (preference.mini[id] - domain.start) * scale;
    }
    if (focused) focusRow(focused);
  }
  function renderData() {
    const next = JSON.stringify([data.rows, data.range, data.partial, data.reasons, data.unknown]);
    if (next !== signature) {
      saveScroll(); signature = next;
      const details = disclosure({label: t('sched_details'), key: `${namespace}:${workspaceId}:schedule:details`, body: h('div', {},
        h('p', {}, t('sched_as_of', {date: data.as_of})),
        ...[...data.reasons.map(reason => ({reason})), ...data.unknown].map(note => h('p', {}, reasonLabel(note.reason), h('code', {}, note.reason))))});
      meta.replaceChildren(h('span', {}, t('sched_loaded', {n: data.rows.length})), data.partial ? statusPill('partial') : h('span'), details);
      const list = h('ul', {}, ...data.unscheduled.map(row => h('li', {},
        button({variant: 'ghost', label: firstLine(row), 'data-schedule-select': row.id, title: row.title, onClick: event => select(row.id, event.currentTarget)}), rowStatus(row, data))));
      unscheduled.replaceChildren(disclosure({label: t('sched_unscheduled', {n: data.unscheduled.length}), key: `${namespace}:${workspaceId}:schedule:unscheduled`, body: list}));
      draw();
    }
    if (selected && !data.rows.some(row => row.id === selected)) {closePanel(); selected = null; remember();}
    showPanel();
  }
  function withdraw() {
    serial++; closePanel(); selected = null; remember(); data = null; signature = ''; domain = null;
    chart.replaceChildren(); meta.replaceChildren(); unscheduled.replaceChildren(); error.replaceChildren();
  }
  async function refresh() {
    if (!canRead()) {withdraw(); return;}
    const ticket = ++serial;
    if (!data) chart.replaceChildren(skeleton(6));
    const check = () => {alive(); if (ticket !== serial || !canRead()) throw Error('PM_SCHEDULE_RETIRED');};
    try {
      const next = await readSchedule(api, workspaceId, check); check();
      // Retain existing sibling order during live updates. New rows append within their parent.
      if (data) {const order = new Map(data.rows.map((row, index) => [row.id, index])); next.rows.sort((a, b) => (order.get(a.id) ?? Infinity) - (order.get(b.id) ?? Infinity));}
      data = next; error.replaceChildren(); renderData();
    } catch (failure) {
      try {alive(); if (ticket !== serial) return;} catch {return;}
      withdraw(); error.append(inlineError({error: t(failure.message === 'PM_SCHEDULE_CHANGED' ? 'sched_changed' : 'sched_error'), retry: refresh}));
    }
  }
  return {element, refresh, withdraw, setActive(value) {
    active = value;
    if (!value) {saveScroll(); closePanel(false);} else if (data) {restoreScroll(); showPanel();}
  }, dispose() {saveScroll(); remember(); active = false; serial++; closePanel(false); data = null;}};
}
