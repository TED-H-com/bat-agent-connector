import {h} from './ui/dom.js';
import {t} from './i18n.js';
import {button, segmented, statusPill, inspector, disclosure, emptyState, skeleton, inlineError} from './ui/components.js';
import {viewState} from './ui/view-state.js';
import {reasonLabel} from './ui/ui-strings.js';
import {DAY, ZOOM, firstLine, dayNumber, spans, milestoneDelta, readSchedule, scheduleTree, scheduleRef} from './pm-schedule-model.js';
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
    const top = viewport.getBoundingClientRect().top + (matchMedia('(min-width: 800px)').matches ? 40 : 0);
    const row = [...chart.querySelectorAll('[data-schedule-row]')].find(node => node.getBoundingClientRect().bottom > top);
    if (row) preference.vertical = {id: row.dataset.scheduleRow, offset: row.getBoundingClientRect().top - top};
  }
  function restoreScroll() {
    viewport.scrollLeft = Math.max(0, ((preference.anchor ?? domain.start) - domain.start) * scale);
    viewport.scrollTop = preference.top || 0;
    if (preference.vertical) {
      const row = chart.querySelector(`[data-schedule-row="${CSS.escape(preference.vertical.id)}"]`);
      const top = viewport.getBoundingClientRect().top + (matchMedia('(min-width: 800px)').matches ? 40 : 0);
      if (row) viewport.scrollTop += row.getBoundingClientRect().top - top - preference.vertical.offset;
    }
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
    const next = JSON.stringify([row, data.rows.filter(value => value.parent_id === row.id || value.kind === 'requirement'), data.rows.find(value => value.id === row.parent_id), data.unknown, data.baseline, data.forecast]);
    if (panel && next === panelSignature) return;
    if (!panel) {
      panel = inspector({title: firstLine(row), onClose: () => {
        const previous = selected; panel = null; panelSignature = ''; selected = null; remember(); markSelection();
        if (active && previous) element.querySelector(`[data-schedule-select="${CSS.escape(previous)}"]`)?.focus({preventScroll: true});
      }}); panel.dataset.scheduleInspector = ''; layout.append(panel);
    }
    panel.querySelector('h2').textContent = firstLine(row);
    const opened = [...panel.body.querySelectorAll('details')].map(node => node.open), scroll = panel.scrollTop;
    const focus = [...panel.body.querySelectorAll('a, summary')].indexOf(document.activeElement);
    panel.body.replaceChildren(renderInspector(row, {workspaceId, data})); panelSignature = next;
    [...panel.body.querySelectorAll('details')].forEach((node, i) => {if (opened[i] !== undefined) node.open = opened[i];});
    panel.scrollTop = scroll;
    if (focus >= 0) panel.body.querySelectorAll('a, summary')[focus]?.focus({preventScroll: true});
  }
  const activeTreeId = () => [focusId, selected].find(id => visible.some(v => v.row.id === id)) || visible[0]?.row.id || null;
  function markSelection() {
    const tabId = activeTreeId();
    for (const node of element.querySelectorAll('[data-schedule-select]')) {
      const current = node.dataset.scheduleSelect === selected;
      if (node.getAttribute('role') === 'treeitem') {
        node.setAttribute('aria-selected', String(current));
        node.tabIndex = node.dataset.scheduleSelect === tabId ? 0 : -1;
      } else {
        node.removeAttribute('aria-selected');
        node.setAttribute('aria-pressed', String(current));
      }
      node.closest('.schedule-row')?.classList.toggle('selected', current);
    }
  }
  function select(id, opener) {
    if (panel && id !== selected) closePanel();
    opener?.focus({preventScroll: true});
    selected = id;
    if (visible.some(v => v.row.id === id)) focusId = id;
    remember(); markSelection(); showPanel();
  }
  const label = row => row.kind === 'group' ? t('sched_unassigned') : firstLine(row);
  function toggle(id) {collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id); remember(); draw();}
  function focusRow(id) {
    if (!id || !visible.some(v => v.row.id === id)) return;
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
    if (zoom === 'day') {
      const weekend = h('span', {class: 'schedule-weekend', 'aria-hidden': 'true'});
      weekend.style.backgroundImage = `repeating-linear-gradient(90deg, transparent 0, transparent ${5 * scale}px, var(--surface-2) ${5 * scale}px, var(--surface-2) ${7 * scale}px)`;
      weekend.style.backgroundPositionX = `${-((new Date(domain.start * DAY).getUTCDay() + 6) % 7) * scale}px`;
      node.append(weekend);
    }
    if (axis) {
      const length = domain.end - domain.start + 1;
      const step = zoom === 'day' ? Math.max(1, Math.ceil(length / 300)) : zoom === 'week' ? Math.max(1, Math.ceil(length / 2100)) * 7 : Math.max(1, Math.ceil(length / 9000));
      for (let date = domain.start, count = 0; date <= domain.end && count < 300; count++) {
        const instant = new Date(date * DAY);
        const text = new Intl.DateTimeFormat(navigator.language, {month: 'short', day: zoom === 'month' ? undefined : 'numeric', timeZone: 'UTC'}).format(instant);
        const tick = h('span', {class: 'schedule-tick'}, text); tick.style.left = `${(date - domain.start) * scale}px`; node.append(tick);
        if (zoom === 'month') {instant.setUTCMonth(instant.getUTCMonth() + step, 1); date = instant.getTime() / DAY;}
        else if (zoom === 'week' && count === 0) date += ((8 - instant.getUTCDay()) % 7 || 7);
        else date += step;
      }
    } else if (row) {
      const rowSpans = spans(row);
      for (const span of rowSpans) {
        const start = span.start ?? span.end, end = span.end ?? start;
        const marker = row.kind === 'milestone';
        const endText = span.open ? t('sched_open') : span.end === null ? reasonLabel(t, 'terminal_time_unrecorded') : row[span.kind].end || '—';
        const barTitle = `${t('sched_' + span.kind)} · ${row[span.kind].start || '—'} → ${endText}`;
        const bar = h('span', {class: `schedule-bar ${span.kind}${marker ? ' milestone' : ''}${span.open ? ' open' : ''}${row.status === 'blocked' ? ' blocked' : ''}`,
          role: 'img', 'aria-label': barTitle, 'data-schedule-bar': span.kind, 'data-open': span.open ? 'true' : null,
          title: barTitle}, marker ? '◆' : null);
        bar.style.left = `${(start - domain.start) * scale}px`;
        if (!marker) bar.style.width = `${span.open ? 44 : Math.max(8, (end - start + 1) * scale)}px`;
        const labels = [];
        if (['waiting', 'blocked'].includes(row.status)) labels.push(t('ui_status_' + row.status));
        if (span.open) labels.push(t('sched_open'));
        else if (span.end === null) labels.push(reasonLabel(t, 'terminal_time_unrecorded'));
        if (labels.length) bar.append(h('span', {class: 'schedule-bar-label'}, labels.join(' · ')));
        node.append(bar);
      }
      const delta = milestoneDelta(row);
      if (row.kind === 'milestone' && delta !== null && delta !== 0) {
        const value = h('span', {class: 'schedule-delta', 'data-schedule-delta': ''}, t('sched_delta', {n: delta > 0 ? '+' + delta : delta}));
        value.style.left = `${(dayNumber(row.forecast.start) - domain.start) * scale + 20}px`; node.append(value);
      }
    }
    const line = h('span', {class: 'schedule-today', role: axis ? 'img' : null, 'aria-label': axis ? t('sched_today') : null, 'aria-hidden': axis ? null : 'true'});
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
    const tabId = activeTreeId();
    visible.forEach((item, index) => {
      const {row, depth, expandable} = item;
      const expand = expandable ? button({icon: 'chevron-right', variant: 'ghost', label: null, 'aria-label': t(collapsed.has(row.id) ? 'ui_expand' : 'ui_collapse', {title: label(row)}),
        tabindex: -1, 'aria-expanded': String(!collapsed.has(row.id)), onClick: event => {event.stopPropagation(); toggle(row.id); focusRow(row.id);}}) : h('span', {class: 'schedule-indent'});
      const dates = spans(row).map(span => {
        const s = row[span.kind].start?.slice(0, 10) || '—';
        const e = span.end !== null ? row[span.kind].end?.slice(0, 10) : null;
        const suffix = span.open ? ' → ' + t('sched_open') : span.end === null ? ' → ' + reasonLabel(t, 'terminal_time_unrecorded') : e && e !== s ? ' → ' + e : '';
        return `${t('sched_' + span.kind)} ${s}${suffix}`;
      });
      const pill = row.kind === 'work' || row.kind === 'attempt' ? rowStatus(row, data) : null;
      const descId = `sched-desc-${index}`, statusId = pill ? `sched-status-${index}` : null;
      if (pill) pill.id = statusId;
      const cell = h('div', {class: 'schedule-tree-cell', role: 'treeitem', 'aria-level': depth, 'aria-expanded': expandable ? String(!collapsed.has(row.id)) : null,
        'aria-label': label(row), 'aria-describedby': [statusId, descId].filter(Boolean).join(' '), tabindex: tabId === row.id ? 0 : -1,
        'data-schedule-select': row.id, onclick: event => row.kind === 'group' ? toggle(row.id) : select(row.id, event.currentTarget), onkeydown: event => keydown(event, item, index)},
        expand, h('span', {class: 'schedule-tree-copy'}, h('span', {class: 'schedule-title', title: row.title}, label(row)), h('span', {id: descId, class: 'schedule-date-range'}, dates.join(' · ') || t('sched_no_dates'))),
        pill);
      cell.style.paddingLeft = `${8 + (depth - 1) * 16}px`;
      const lane = h('div', {class: 'schedule-lane'}, track(row));
      lane.addEventListener('scroll', () => {if (lane.clientWidth) {preference.mini ||= {}; preference.mini[row.id] = domain.start + lane.scrollLeft / scale;}});
      const line = h('div', {class: 'schedule-row', 'data-schedule-row': row.id, 'data-has-dates': String(spans(row).length > 0)}, cell, lane); tree.append(line);
    });
    chart.replaceChildren(axis, tree);
    if (!visible.length) chart.replaceChildren(emptyState({title: t('sched_empty')}));
    restoreScroll(); markSelection();
    for (const lane of chart.querySelectorAll('.schedule-lane')) {
      const id = lane.firstElementChild?.dataset.scheduleTrack;
      if (preference.mini?.[id] !== undefined) lane.scrollLeft = (preference.mini[id] - domain.start) * scale;
      else if (matchMedia('(max-width: 799px)').matches) {
        const row = data.rows.find(row => row.id === id), dates = row ? spans(row).map(span => span.start ?? span.end) : [];
        if (dates.length) lane.scrollLeft = Math.max(0, (Math.min(...dates) - domain.start) * scale - 32);
      }
    }
    if (focused) focusRow(focused);
  }
  function renderData() {
    const next = JSON.stringify([data.rows, data.range, data.partial, data.reasons, data.unknown]);
    if (next !== signature) {
      saveScroll(); signature = next;
      const details = disclosure({label: t('sched_details'), key: `${namespace}:${workspaceId}:schedule:details`, body: h('div', {},
        h('p', {}, t('sched_as_of', {date: data.as_of})),
        ...[...data.reasons.map(reason => ({reason})), ...data.unknown].map(note => h('p', {}, reasonLabel(t, note.reason), h('code', {}, note.reason))))});
      const upcoming = data.rows.filter(row => row.kind === 'milestone' && dayNumber(row.forecast?.start || row.baseline?.start) >= dayNumber(new Date().toISOString()))
        .sort((a, b) => dayNumber(a.forecast?.start || a.baseline?.start) - dayNumber(b.forecast?.start || b.baseline?.start))[0];
      const delta = upcoming ? milestoneDelta(upcoming) : null;
      meta.replaceChildren(...(upcoming ? [h('span', {class: 'schedule-answer'}, firstLine(upcoming), delta ? ' · ' + t('sched_delta', {n: delta > 0 ? '+' + delta : delta}) : '')] : []),
        h('span', {}, t('sched_loaded', {n: data.rows.length})), ...(data.partial ? [statusPill('partial')] : []), details);
      const list = h('ul', {}, ...data.unscheduled.map(row => h('li', {},
        button({variant: 'ghost', label: firstLine(row), 'data-schedule-select': row.id, title: row.title, onClick: event => select(row.id, event.currentTarget)}),
        row.kind === 'work' || row.kind === 'attempt' ? rowStatus(row, data) : null)));
      unscheduled.replaceChildren(...(data.unscheduled.length ? [disclosure({label: t('sched_unscheduled', {n: data.unscheduled.length}), key: `${namespace}:${workspaceId}:schedule:unscheduled`, body: list})] : []));
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
  async function openRef(ref) {
    if (!data) await refresh();
    if (!active || !data) return;
    const row = data.rows.find(row => {const identity = scheduleRef(row, data);
      return identity?.kind === ref.kind && identity.id === ref.id && identity.revision_id === ref.revision_id;});
    if (!row) return;
    let parent = row.parent_id;
    const visited = new Set();
    while (parent && !visited.has(parent)) {visited.add(parent); collapsed.delete(parent); parent = data.rows.find(row => row.id === parent)?.parent_id;}
    draw(); focusRow(row.id);
    const opener = element.querySelector(`[data-schedule-select="${CSS.escape(row.id)}"]`);
    opener?.scrollIntoView({block: 'nearest'}); select(row.id, opener);
  }
  return {element, refresh, withdraw, openRef, setActive(value) {
    active = value;
    if (!value) {saveScroll(); closePanel(false);} else if (data) {restoreScroll(); showPanel();}
  }, dispose() {saveScroll(); remember(); active = false; serial++; closePanel(false); data = null;}};
}
