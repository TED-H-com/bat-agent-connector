import {axisDateText, timeText, timeStamp, timeLabel} from './ui/stamps.js';
import {h} from './ui/dom.js';
import {t} from './i18n.js';
import {button, segmented, statusPill, inspector, disclosure, emptyState, skeleton, inlineError} from './ui/components.js';
import {viewState} from './ui/view-state.js';
import {reasonLabel} from './ui/ui-strings.js';
import {DAY, ZOOM, axisTickDays, firstLine, dayNumber, spans, milestoneDelta, readSchedule, scheduleTree, scheduleRef} from './pm-schedule-model.js';
import {render as renderInspector, rowStatus} from './pm-schedule-inspector.js';
import {scheduleTitle, scheduleFirstLine} from './pm-schedule-labels.js';

export function workspaceSchedule({api, alive, namespace, workspaceId, canRead}) {
  const preference = viewState(namespace, workspaceId, 'schedule');
  let zoom = Object.hasOwn(ZOOM, preference.zoom) ? preference.zoom : 'week';
  const collapsed = new Set(preference.collapsed || []);
  let selected = preference.selected || null, active = false, serial = 0, data = null, panel = null, panelSignature = '', signature = '';
  let domain, scale, visible = [], focusId = null, syncingLanes = false, renderZoom = zoom;
  const narrowMedia = matchMedia('(max-width: 799px)');
  const phone = () => narrowMedia.matches;
  // Day cannot be read in a phone-width row. Keep the saved choice for wider screens.
  const shownZoom = () => phone() && zoom === 'day' ? 'week' : zoom;
  let zoomControl = null, lastWidth = -1, lastNarrow = phone(), fitting = false;
  const viewport = h('div', {class: 'schedule-viewport', tabindex: 0, role: 'region', 'aria-label': t('sched_timeline')});
  const chart = h('div', {class: 'schedule-chart'}), meta = h('div', {class: 'schedule-meta', 'aria-live': 'polite'});
  const caption = h('p', {class: 'schedule-axis-caption', 'data-schedule-axis-caption': ''});
  const unscheduled = h('div', {class: 'schedule-unscheduled'}), error = h('div');
  const labelTip = h('div', {class: 'schedule-label-tip', role: 'tooltip', id: `${workspaceId}-schedule-label`, hidden: true});
  let tipCell = null;
  function hideLabel() {labelTip.hidden = true; tipCell = null;}
  function showLabel(cell, text) {
    if (tipCell === cell) return;
    tipCell = cell; labelTip.textContent = text; labelTip.hidden = false;
    positionLabel(cell);
  }
  function positionLabel(cell) {
    const box = cell.getBoundingClientRect();
    labelTip.style.left = `${Math.max(8, Math.min(box.left, innerWidth - labelTip.offsetWidth - 8))}px`;
    labelTip.style.top = `${Math.max(8, Math.min(box.bottom, innerHeight - labelTip.offsetHeight - 8))}px`;
  }
  function treeDate(value) {
    const stamp = timeStamp(value, {dateOnly: true});
    if (stamp instanceof HTMLElement) {
      stamp.setAttribute('aria-description', stamp.title);
      stamp.removeAttribute('title');
    }
    return stamp;
  }
  const main = h('div', {class: 'schedule-main'}, error, viewport, unscheduled);
  viewport.append(caption, chart);
  const legend = h('div', {class: 'schedule-legend', 'aria-label': t('sched_legend')},
    ...['baseline', 'forecast', 'actual', 'milestone'].map(kind => h('span', {},
      h('i', {class: `schedule-swatch ${kind}`, 'aria-hidden': 'true'}, kind === 'milestone' ? '◆' : null), t('sched_' + kind))));
  const toolbar = h('div', {class: 'schedule-toolbar'},
    button({label: t('sched_today'), variant: 'ghost', onClick: today}), legend);
  function mountZoom() {
    const options = Object.keys(ZOOM).filter(value => !(phone() && value === 'day')).map(value => ({value, label: t('sched_' + value)}));
    const next = segmented({label: t('sched_zoom'), options, value: shownZoom(), onChange: value => {
      saveScroll(); zoom = value; preference.zoom = zoom; draw();
    }});
    if (zoomControl) zoomControl.replaceWith(next);
    else toolbar.prepend(next);
    zoomControl = next;
  }
  mountZoom();
  const layout = h('div', {class: 'schedule-layout'}, main);
  const element = h('section', {'data-pm-schedule': '', class: 'pm-schedule'}, toolbar, layout, labelTip);
  viewport.addEventListener('scroll', () => {
    if (tipCell?.contains(document.activeElement)) positionLabel(tipCell);
    else hideLabel();
  }, {passive: true});
  viewport.addEventListener('scroll', saveScroll, {passive: true});
  function fitViewport() {
    if (!data || fitting) return;
    const width = viewport.clientWidth, narrow = phone();
    if (width === lastWidth && narrow === lastNarrow) return;
    const crossed = narrow !== lastNarrow;
    lastWidth = width; lastNarrow = narrow;
    fitting = true;
    try {if (crossed) mountZoom(); draw();}
    finally {fitting = false;}
  }
  const onNarrow = () => fitViewport();
  narrowMedia.addEventListener('change', onNarrow);
  const observer = new ResizeObserver(() => fitViewport());
  observer.observe(viewport);
  function remember() {Object.assign(preference, {selected, zoom, collapsed: [...collapsed].slice(0, 1000)});}
  function saveScroll() {
    if (!domain || !viewport.clientWidth) return;
    if (matchMedia('(min-width: 800px)').matches) preference.anchor = domain.start + viewport.scrollLeft / scale;
    preference.top = viewport.scrollTop;
    const top = viewport.getBoundingClientRect().top + (matchMedia('(min-width: 800px)').matches ? 40 : 0);
    const row = [...chart.querySelectorAll('[data-schedule-row]')].find(node => node.getBoundingClientRect().bottom > top);
    if (row) preference.vertical = {id: row.dataset.scheduleRow, offset: row.getBoundingClientRect().top - top};
  }
  function restoreScroll() {
    const left = Math.max(0, ((preference.anchor ?? domain.start) - domain.start) * scale);
    viewport.scrollLeft = left;
    syncingLanes = true;
    for (const mini of chart.querySelectorAll('.schedule-lane')) mini.scrollLeft = left;
    syncingLanes = false;
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
    if (phone()) {
      const lane = chart.querySelector('[data-has-dates="true"] .schedule-lane') || chart.querySelector('.schedule-lane');
      if (lane && lane.scrollWidth > lane.clientWidth + 1) {
        const miniLeft = Math.max(0, x - lane.clientWidth / 2);
        syncingLanes = true;
        for (const mini of chart.querySelectorAll('.schedule-lane')) mini.scrollLeft = miniLeft;
        syncingLanes = false;
        preference.anchor = domain.start + miniLeft / scale;
      }
      return;
    }
    viewport.scrollLeft = Math.max(0, x - Math.max(0, viewport.clientWidth - 328) / 2);
    const firstLane = chart.querySelector('[data-has-dates="true"] .schedule-lane') || chart.querySelector('.schedule-lane');
    const miniLeft = firstLane ? Math.max(0, x - firstLane.clientWidth / 2) : 0;
    syncingLanes = true;
    for (const mini of chart.querySelectorAll('.schedule-lane')) mini.scrollLeft = miniLeft;
    syncingLanes = false;
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
      panel = inspector({title: scheduleFirstLine(row), onClose: () => {
        const previous = selected; panel = null; panelSignature = ''; selected = null; remember(); markSelection();
        if (active && previous) element.querySelector(`[data-schedule-select="${CSS.escape(previous)}"]`)?.focus({preventScroll: true});
      }}); panel.dataset.scheduleInspector = ''; layout.append(panel);
    }
    panel.querySelector('h2').textContent = scheduleFirstLine(row);
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
  const label = row => row.kind === 'group' ? t('sched_unassigned') : scheduleFirstLine(row);
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
  function placeSpan(bar, start, end, marker, open) {
    const days = domain.end - domain.start + 1;
    if (phone()) {
      const origin = start - domain.start;
      bar.style.left = `${origin / days * 100}%`;
      if (!marker) {
        const span = open ? Math.min(1.75, Math.max(0.6, days - origin)) : Math.max(end - start + 1, 0.35);
        bar.style.width = `${Math.max(0, Math.min(days - origin, span)) / days * 100}%`;
      }
      return;
    }
    bar.style.left = `${(start - domain.start) * scale}px`;
    if (!marker) bar.style.width = `${open ? 44 : Math.max(8, (end - start + 1) * scale)}px`;
  }
  function track(row, axis = false) {
    const node = h('div', {class: 'schedule-track' + (axis ? ' schedule-axis' : ''), 'data-schedule-track': row?.id});
    const days = domain.end - domain.start + 1;
    node.style.width = phone() ? '100%' : `${days * scale}px`;
    const todayDay = dayNumber(new Date().toISOString());
    if (renderZoom === 'day' && !phone()) {
      const weekend = h('span', {class: 'schedule-weekend', 'aria-hidden': 'true'});
      weekend.style.backgroundImage = `repeating-linear-gradient(90deg, transparent 0, transparent ${5 * scale}px, var(--surface-2) ${5 * scale}px, var(--surface-2) ${7 * scale}px)`;
      weekend.style.backgroundPositionX = `${-((new Date(domain.start * DAY).getUTCDay() + 6) % 7) * scale}px`;
      node.append(weekend);
    }
    if (axis && !phone()) {
      for (const date of axisTickDays({zoom: renderZoom, start: domain.start, end: domain.end, scale})) {
        const iso = new Date(date * DAY).toISOString().slice(0, 10);
        const tick = h('span', {class: 'schedule-tick', 'data-schedule-tick': iso}, axisDateText(iso, {unit: renderZoom}));
        tick.style.left = `${(date - domain.start) * scale}px`;
        node.append(tick);
      }
    } else if (row) {
      const rowSpans = spans(row);
      for (const span of rowSpans) {
        const start = span.start ?? span.end, end = span.end ?? start;
        const marker = row.kind === 'milestone';
        const interval = row[span.kind], startText = timeText(interval.start?.slice(0, 10), {dateOnly: true});
        const endText = span.open ? t('sched_open') : span.unknownEnd ? reasonLabel(t, 'terminal_time_unrecorded')
          : span.end !== null ? timeText(interval.end?.slice(0, 10), {dateOnly: true}) : null;
        const rangeText = endText && span.end !== span.start ? `${startText || '—'} → ${endText}` : (startText || endText || '—');
        const barTitle = `${t('sched_' + span.kind)} · ${rangeText}`;
        const bar = h('span', {class: `schedule-bar ${span.kind}${marker ? ' milestone' : ''}${span.open ? ' open' : ''}${row.status === 'blocked' ? ' blocked' : ''}`,
          role: 'img', 'aria-label': barTitle, 'data-schedule-bar': span.kind, 'data-open': span.open ? 'true' : null,
          title: barTitle}, marker ? '◆' : null);
        placeSpan(bar, start, end, marker, span.open);
        if (!phone()) {
          const labels = [];
          if (['waiting', 'blocked'].includes(row.status)) labels.push(t('ui_status_' + row.status));
          if (span.open) labels.push(t('sched_open'));
          else if (span.unknownEnd) labels.push(reasonLabel(t, 'terminal_time_unrecorded'));
          if (labels.length) bar.append(h('span', {class: 'schedule-bar-label'}, labels.join(' · ')));
        }
        node.append(bar);
      }
      const delta = milestoneDelta(row);
      if (!phone() && row.kind === 'milestone' && delta !== null && delta !== 0) {
        const value = h('span', {class: 'schedule-delta', 'data-schedule-delta': ''}, t('sched_delta', {n: delta > 0 ? '+' + delta : delta}));
        value.style.left = `${(dayNumber(row.forecast.start) - domain.start) * scale + 20}px`; node.append(value);
      }
    }
    const line = h('span', {class: 'schedule-today', role: axis ? 'img' : null, 'aria-label': axis ? t('sched_today') : null, 'aria-hidden': axis ? null : 'true'});
    line.style.left = phone() ? `${(todayDay - domain.start) / days * 100}%` : `${(todayDay - domain.start) * scale}px`;
    node.append(line); return node;
  }
  function trimAxis(axisRow) {
    const trackNode = axisRow.querySelector('.schedule-axis');
    if (!trackNode) return;
    // Track coordinates, including a centering transform. Viewport scroll must not drop a label.
    const origin = trackNode.getBoundingClientRect().left;
    for (const tick of [...trackNode.querySelectorAll('.schedule-tick')]) {
      const box = tick.getBoundingClientRect();
      if (box.width > 0 && box.left - origin < -0.5) tick.remove();
    }
    const kept = [...trackNode.querySelectorAll('.schedule-tick')];
    for (let i = 0; i < kept.length - 1;) {
      const current = kept[i].getBoundingClientRect(), next = kept[i + 1].getBoundingClientRect();
      if (current.width > 0 && next.left - current.left < current.width - 0.5) {kept[i].remove(); kept.splice(i, 1);}
      else i++;
    }
  }
  function containPhone(trackNode) {
    const host = trackNode.getBoundingClientRect();
    if (host.width < 2) return;
    for (const el of trackNode.querySelectorAll('.schedule-bar, .schedule-today')) {
      let box = el.getBoundingClientRect();
      const leftOverflow = host.left - box.left;
      if (leftOverflow > 0.5) el.style.marginLeft = `${leftOverflow}px`;
      box = el.getBoundingClientRect();
      const rightOverflow = box.right - host.right;
      if (rightOverflow > 0.5) {
        if (el.classList.contains('milestone') || el.classList.contains('schedule-today')) el.style.marginLeft = `${(parseFloat(el.style.marginLeft) || 0) - rightOverflow}px`;
        else el.style.width = `${Math.max(2, box.width - rightOverflow)}px`;
      }
    }
  }
  function writeCaption() {
    const start = new Date(domain.start * DAY).toISOString().slice(0, 10);
    const end = new Date(domain.end * DAY).toISOString().slice(0, 10);
    caption.textContent = t('sched_axis_range', {start: axisDateText(start, {unit: 'day'}), end: axisDateText(end, {unit: 'day'})});
  }
  function draw() {
    if (!data) return;
    renderZoom = shownZoom();
    element.dataset.scheduleLayout = phone() ? 'phone' : 'board';
    const focused = chart.contains(document.activeElement) ? document.activeElement.closest('[data-schedule-select]')?.dataset.scheduleSelect : null;
    const now = dayNumber(new Date().toISOString());
    domain = {start: Math.min(data.range?.start ?? now, now) - 3, end: Math.max(data.range?.end ?? now, now) + 5};
    const days = domain.end - domain.start + 1;
    const fitted = viewport.clientWidth;
    scale = phone() && fitted >= 40 ? fitted / days : Math.min(ZOOM[renderZoom], 120000 / days);
    chart.style.setProperty('--schedule-width', phone() ? '100%' : `${days * scale}px`);
    writeCaption();
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
        const suffix = span.open ? [' → ', t('sched_open')] : span.unknownEnd ? [' → ', reasonLabel(t, 'terminal_time_unrecorded')] : e && e !== s ? [' → ', treeDate(e)] : [];
        return h('span', {}, t('sched_' + span.kind), ' ', treeDate(s), ...suffix);
      });
      const deltaValue = row.kind === 'milestone' ? milestoneDelta(row) : null;
      const dateChildren = dates.length ? dates.flatMap((date, index) => index ? [' · ', date] : [date]) : [t('sched_no_dates')];
      if (phone() && deltaValue) dateChildren.push(' · ', h('span', {'data-schedule-delta': ''}, t('sched_delta', {n: deltaValue > 0 ? '+' + deltaValue : deltaValue})));
      const pill = row.kind === 'work' || row.kind === 'attempt' ? rowStatus(row, data) : null;
      const descId = `sched-desc-${index}`, statusId = pill ? `sched-status-${index}` : null;
      if (pill) pill.id = statusId;
      const fullTitle = scheduleTitle(row) || label(row);
      const cell = h('div', {class: 'schedule-tree-cell', role: 'treeitem', 'aria-level': depth, 'aria-expanded': expandable ? String(!collapsed.has(row.id)) : null,
        'aria-label': fullTitle, 'aria-describedby': [statusId, descId].filter(Boolean).join(' '), tabindex: tabId === row.id ? 0 : -1,
        'data-schedule-select': row.id, onclick: event => row.kind === 'group' ? toggle(row.id) : select(row.id, event.currentTarget), onkeydown: event => keydown(event, item, index)},
        expand, h('span', {class: 'schedule-tree-copy'}, h('span', {class: 'schedule-title'}, label(row)), h('span', {id: descId, class: 'schedule-date-range'}, ...dateChildren)),
        pill);
      const hoverLabel = () => {
        const focused = document.activeElement?.closest('.schedule-tree-cell');
        if (!focused || focused === cell) showLabel(cell, fullTitle);
      };
      cell.addEventListener('mouseenter', hoverLabel);
      cell.addEventListener('mousemove', hoverLabel);
      cell.addEventListener('mouseleave', () => {if (tipCell === cell && !cell.contains(document.activeElement)) hideLabel();});
      cell.addEventListener('focusin', () => showLabel(cell, fullTitle));
      cell.addEventListener('focusout', event => {if (tipCell === cell && !cell.contains(event.relatedTarget)) hideLabel();});
      cell.addEventListener('keydown', event => {if (event.key === 'Escape') hideLabel();});
      cell.style.paddingLeft = `${8 + (depth - 1) * 16}px`;
      const lane = h('div', {class: 'schedule-lane'}, track(row));
      lane.addEventListener('scroll', () => {
        if (syncingLanes || !lane.clientWidth) return;
        syncingLanes = true;
        preference.anchor = domain.start + lane.scrollLeft / scale;
        for (const other of chart.querySelectorAll('.schedule-lane')) if (other !== lane) other.scrollLeft = lane.scrollLeft;
        syncingLanes = false;
      }, {passive: true});
      const line = h('div', {class: 'schedule-row', 'data-schedule-row': row.id, 'data-has-dates': String(spans(row).length > 0)}, cell, lane); tree.append(line);
    });
    chart.replaceChildren(axis, tree);
    // Measure label, indentation and state pill together. Reserve a readable
    // timeline and share one column width across the header and every row.
    if (!phone()) {
      const needed = Math.max(328, ...[...tree.querySelectorAll('.schedule-tree-cell')].map(cell => {
        const title = cell.querySelector('.schedule-title');
        return title.scrollWidth + cell.clientWidth - title.clientWidth + 2;
      }));
      chart.style.setProperty('--schedule-tree', `${Math.min(needed, Math.max(328, viewport.clientWidth - 480))}px`);
    }
    if (tipCell && !tipCell.isConnected) hideLabel();
    if (!visible.length) chart.replaceChildren(emptyState({title: t('sched_empty')}));
    else if (phone()) for (const trackNode of chart.querySelectorAll('.schedule-track')) containPhone(trackNode);
    else trimAxis(axis);
    restoreScroll(); markSelection();
    if (focused) focusRow(focused);
    lastWidth = viewport.clientWidth; lastNarrow = phone();
  }
  let stale = false, pendingRefresh = null;
  function renderData() {
    const next = JSON.stringify([data.rows, data.range, data.partial, data.reasons, data.unknown]);
    if (next !== signature) {
      saveScroll(); signature = next;
      const activeEl = document.activeElement;
      const focusMetaSummary = meta.contains(activeEl) && activeEl?.tagName === 'SUMMARY';
      const focusUnscheduledSummary = unscheduled.contains(activeEl) && activeEl?.tagName === 'SUMMARY';
      const focusUnscheduledId = unscheduled.contains(activeEl) ? activeEl?.closest('[data-schedule-select]')?.dataset.scheduleSelect : null;
      const details = disclosure({label: t('sched_details'), key: `${namespace}:${workspaceId}:schedule:details`, body: h('div', {},
        h('p', {}, timeLabel(t, 'sched_as_of', {date: timeStamp(data.as_of)})),
        ...[...data.reasons.map(reason => ({reason})), ...data.unknown].map(note => h('p', {}, reasonLabel(t, note.reason), h('code', {}, note.reason))))});
      const upcoming = data.rows.filter(row => row.kind === 'milestone' && dayNumber(row.forecast?.start || row.baseline?.start) >= dayNumber(new Date().toISOString()))
        .sort((a, b) => dayNumber(a.forecast?.start || a.baseline?.start) - dayNumber(b.forecast?.start || b.baseline?.start))[0];
      const delta = upcoming ? milestoneDelta(upcoming) : null;
      meta.replaceChildren(...(upcoming ? [h('span', {class: 'schedule-answer'}, firstLine(upcoming), delta ? ' · ' + t('sched_delta', {n: delta > 0 ? '+' + delta : delta}) : '')] : []),
        h('span', {}, t(data.partial ? 'sched_loaded' : 'sched_count', {n: data.rows.length})), ...(data.partial ? [statusPill('partial')] : []), details);
      const list = h('ul', {}, ...data.unscheduled.map(row => h('li', {},
        button({variant: 'ghost', label: scheduleFirstLine(row), 'data-schedule-select': row.id, title: scheduleTitle(row), onClick: event => select(row.id, event.currentTarget)}),
        row.kind === 'work' || row.kind === 'attempt' ? rowStatus(row, data) : null)));
      unscheduled.replaceChildren(...(data.unscheduled.length ? [disclosure({label: t('sched_unscheduled', {n: data.unscheduled.length}), key: `${namespace}:${workspaceId}:schedule:unscheduled`, body: list})] : []));
      draw();
      if (focusMetaSummary) meta.querySelector('summary')?.focus({preventScroll: true});
      else if (focusUnscheduledSummary) unscheduled.querySelector('summary')?.focus({preventScroll: true});
      else if (focusUnscheduledId) unscheduled.querySelector(`[data-schedule-select="${CSS.escape(focusUnscheduledId)}"]`)?.focus({preventScroll: true});
    }
    if (selected && !data.rows.some(row => row.id === selected)) {closePanel(); selected = null; remember();}
    showPanel();
  }
  function withdraw() {
    hideLabel();
    serial++; stale = false; pendingRefresh = null; closePanel(); selected = null; remember(); data = null; signature = ''; domain = null;
    chart.replaceChildren(); caption.textContent = ''; meta.replaceChildren(); unscheduled.replaceChildren(); error.replaceChildren();
    delete element.dataset.scheduleLayout;
  }
  async function refresh(force = false) {
    if (!canRead()) {withdraw(); return;}
    if (!force && element.closest('[hidden]')) {stale = true; return;}
    stale = false;
    const ticket = ++serial;
    const run = (async () => {
      if (!data) chart.replaceChildren(skeleton(6));
      const check = () => {alive(); if (ticket !== serial || !canRead()) throw Error('PM_SCHEDULE_RETIRED');};
      try {
        const next = await readSchedule(api, workspaceId, check); check();
        // Retain existing sibling order during live updates. New rows append within their parent.
        if (data) {const order = new Map(data.rows.map((row, index) => [row.id, index])); next.rows.sort((a, b) => (order.get(a.id) ?? Infinity) - (order.get(b.id) ?? Infinity));}
        data = next; error.replaceChildren(); renderData();
      } catch (failure) {
        try {alive(); if (ticket !== serial) return;} catch {return;}
        withdraw();
        const tooLarge = failure.status === 413 || failure.code === 'PM_SCHEDULE_TOO_LARGE';
        error.append(inlineError({
          error: t(tooLarge ? 'sched_too_large' : failure.message === 'PM_SCHEDULE_CHANGED' ? 'sched_changed' : 'sched_error'),
          retry: tooLarge ? null : () => refresh(),
        }));
      }
    })();
    pendingRefresh = run;
    try {await run;} finally {if (pendingRefresh === run) pendingRefresh = null;}
  }
  async function openRef(ref) {
    if (pendingRefresh) await pendingRefresh;
    else if (!data || stale) await refresh(true);
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
  return {element, meta, refresh, withdraw, openRef, setActive(value) {
    active = value;
    if (!value) {hideLabel(); saveScroll(); closePanel(false);}
    else {
      if (data) {restoreScroll(); showPanel();}
      if (stale && !element.closest('[hidden]')) void refresh();
    }
  }, dispose() {saveScroll(); remember(); observer.disconnect(); narrowMedia.removeEventListener('change', onNarrow); active = false; stale = false; pendingRefresh = null; serial++; closePanel(false); data = null;}};
}
