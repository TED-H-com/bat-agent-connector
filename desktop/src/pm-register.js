import {viewState} from './ui/view-state.js';
import {dataTable, disclosure, emptyState, inlineError, inspector, segmented, skeleton, statusPill} from './ui/components.js';
import {statusVocabulary, reasonLabel} from './ui/ui-strings.js';
import {icon} from './ui/icons.js';
import {discussHref, isContextRef} from './pm-discuss-link.js';
import {joinLabel, labelSegment} from './label-text.js';

// Read-only projection. Row ids and cursors are opaque: pass them through unchanged.
// Age bars are informational. The register API has no age filter.
const TYPES = ['risk', 'issue', 'action', 'change', 'decision'];
const ROW_ID = /^(?:governance|finding|repair|follow_up|proposal|issue_mapping|meeting|plan_revision)\.[A-Za-z0-9._-]{1,180}$/;
const STATUSES = ['open', 'monitoring', 'observed_open', 'blocked', 'tracking', 'needs_review', 'proposed', 'in_progress', 'draft', 'reported_resolved', 'reported_done', 'adopted', 'rejected', 'cancelled', 'succeeded', 'failed'];
const STEPS = ['finding', 'action', 'work', 'evidence', 'review', 'proposal', 'decision', 'revision'];
const TYPE_ICON = {risk: 'circle-alert', issue: 'inbox', action: 'check', change: 'refresh-cw', decision: 'list-checks'};
const ACTION_KEYS = {
  'pm.maintenance.repair': 'reg_action_repair',
  'pm.maintenance.repair.evidence.record': 'reg_action_evidence',
  'pm.governance.revise': 'reg_action_revise',
  'pm.governance.decide': 'reg_action_decide',
  'pm.governance.adoption.link': 'reg_action_link',
  'pm.issue.publish': 'reg_action_publish',
  'pm.issue.cancel': 'reg_action_cancel',
  'pm.meeting.follow_up.record': 'reg_action_follow',
  'pm.maintenance_review.follow_up.record': 'reg_action_follow',
};
const AGE_KEYS = {'0-7d': 'reg_age_0_7', '8-30d': 'reg_age_8_30', '31-90d': 'reg_age_31_90', '90d+': 'reg_age_90'};
const TONES = {open: 'info', monitoring: 'info', observed_open: 'info', tracking: 'info', failed: 'bad', proposed: 'review', needs_review: 'review', succeeded: 'ok'};
const REPO = /^[\w.-]+\/[\w.-]+$/;

const finite = value => typeof value === 'number' && Number.isFinite(value);
const known = (t, key) => {const text = t(key); return text === key ? '' : text;};
const when = value => {const text = labelSegment(value); if (!text) return ''; const date = new Date(text); return Number.isNaN(date.valueOf()) ? '' : date.toLocaleString();};
const day = value => {const text = labelSegment(value); if (!text) return ''; const date = new Date(text); return Number.isNaN(date.valueOf()) ? text : date.toLocaleDateString();};

export function workspaceRegister({h, t, api, alive, namespace, workspaceId, errorBox, onMeta = () => {}, onOpenWork = () => {}, onOpenRequirement = () => {}, onAction = () => {}}) {
  const preference = viewState(namespace, workspaceId, 'issues');
  let serial = 0, detailSerial = 0, active = false, busy = false, rows = [], nextCursor = null, coverage = null, totals = null, summary = null;
  let loaded = false, unavailable = false, failed = null, selected = preference.selected || null;
  let typeValue = TYPES.includes(preference.type) ? preference.type : '';
  let debounce = null, sheet = null, retainSheet = false, opening = false;
  const statusLine = h('div', {class: 'reg-status', role: 'status'});
  const summaryHost = h('div', {class: 'reg-summary', 'data-reg-summary': ''});
  const trends = h('details', {class: 'ui-disclosure reg-trends', 'data-reg-trends': ''},
    h('summary', {}, icon('chevron-right'), t('reg_trends')), summaryHost);
  const tableHost = h('div', {'data-reg-table': ''});
  const footer = h('div', {class: 'reg-footer', 'data-reg-footer': '', hidden: true});
  const statusSelect = h('select', {'data-reg-status': '', 'aria-label': t('reg_filter_status'), onchange: () => reloadNow()},
    h('option', {value: ''}, t('reg_any_status')),
    ...STATUSES.map(status => h('option', {value: status}, statusLabel(status))));
  const ownerListId = `reg-owners-${workspaceId.replace(/[^\w-]/g, '')}`;
  const ownerList = h('datalist', {id: ownerListId});
  const ownerInput = h('input', {type: 'text', class: 'reg-owner', role: 'combobox', 'aria-autocomplete': 'list', 'data-reg-owner': '',
    list: ownerListId, 'aria-label': t('reg_filter_owner'), placeholder: t('reg_filter_owner'), autocomplete: 'off', oninput: queueReload});
  const searchInput = h('input', {type: 'search', 'data-reg-search': '', 'aria-label': t('reg_filter_search'), placeholder: t('reg_filter_search'), oninput: queueReload});
  statusSelect.value = STATUSES.includes(preference.status) ? preference.status : '';
  ownerInput.value = preference.owner || '';
  searchInput.value = preference.q || '';
  const typeControl = segmented({label: t('reg_col_type'), value: typeValue, onChange: value => {typeValue = value || ''; reloadNow();},
    options: [{value: '', label: t('reg_all')}, ...TYPES.map(type => ({value: type, label: t('reg_type_' + type)}))]});
  [...typeControl.querySelectorAll('button')].forEach((node, index) => node.setAttribute('data-reg-type', index ? TYPES[index - 1] : ''));
  const filters = h('div', {class: 'reg-toolbar'}, typeControl,
    h('div', {class: 'reg-toolbar-filters'}, statusSelect, ownerInput, ownerList,
      h('div', {class: 'reg-search'}, icon('search', {size: 16}), searchInput)));
  const mainCol = h('div', {class: 'reg-main'}, filters, trends, statusLine, tableHost, footer);
  const layout = h('div', {class: 'reg-layout'}, mainCol);
  const element = h('section', {'data-pm-register': ''}, layout);
  const wideQuery = matchMedia('(min-width: 801px)');
  function onWide() {trends.open = wideQuery.matches; paintTable();}
  wideQuery.addEventListener('change', onWide);
  trends.open = wideQuery.matches;
  function statusLabel(status) {
    if (Object.hasOwn(statusVocabulary, status)) return t('ui_status_' + status);
    return known(t, 'reg_status_' + status) || t('ui_reason_generic');
  }
  function statusNode(status) {
    if (!labelSegment(status)) return statusPill('unknown');
    if (Object.hasOwn(statusVocabulary, status)) return statusPill(status);
    const label = known(t, 'reg_status_' + status);
    return h('span', {class: `ui-pill tone-${TONES[status] || 'neutral'}`}, label || t('ui_reason_generic'));
  }
  function kindLabel(kind) {return known(t, 'reg_kind_' + kind) || t('ui_reason_generic');}
  function stepLabel(step) {return known(t, 'reg_step_' + step) || t('ui_reason_generic');}
  function dash(value) {return labelSegment(value) || '—';}
  function readFilters() {
    preference.type = typeValue;
    preference.status = statusSelect.value;
    preference.owner = ownerInput.value;
    preference.q = searchInput.value;
  }
  function resetPage() {
    rows = []; nextCursor = null; summary = null; totals = null; coverage = null; loaded = false; failed = null; unavailable = false;
    selected = null; preference.inspector = false; closeSheet(false); render();
  }
  function queueReload() {readFilters(); clearTimeout(debounce); debounce = setTimeout(() => {debounce = null; resetPage(); void refresh();}, 200);}
  function reloadNow() {readFilters(); clearTimeout(debounce); debounce = null; resetPage(); void refresh();}
  function publishMeta() {
    if (!active || unavailable || failed || !loaded) {onMeta(null); return;}
    if (coverage?.complete === true && totals && typeof totals === 'object') {
      const line = joinLabel(TYPES.map(type => finite(totals[type]) ? `${t('reg_type_' + type)} ${totals[type]}` : ''));
      onMeta(line || (rows.length ? t('reg_shown', {n: rows.length}) : t('reg_none_shown')));
      return;
    }
    onMeta(rows.length ? t('reg_shown', {n: rows.length}) : t('reg_none_shown'));
  }
  function shownLine() {
    const complete = coverage?.complete === true;
    const sum = complete && totals ? TYPES.reduce((total, type) => total + (finite(totals[type]) ? totals[type] : 0), 0) : null;
    const show = Boolean(nextCursor) || !complete || (sum != null && sum !== rows.length);
    return show ? (rows.length ? t('reg_shown', {n: rows.length}) : t('reg_none_shown')) : '';
  }
  function bar(label, value, max, attrs) {
    const track = h('div', {class: 'reg-bar-track', 'aria-hidden': 'true'}, h('span', {class: 'reg-bar-fill'}));
    const fill = track.firstChild;
    fill.style.width = max > 0 && finite(value) ? `${Math.max(0, Math.min(100, Math.round(value / max * 100)))}%` : '0%';
    return h('div', {class: 'reg-bar', role: 'img', 'aria-label': t('reg_bar_value', {label, value}), ...attrs},
      h('span', {class: 'reg-bar-label'}, label), track, h('span', {class: 'reg-bar-value'}, String(value)));
  }
  function paintSummary() {
    summaryHost.replaceChildren();
    trends.hidden = summary == null;
    if (summary == null) return;
    const ages = Array.isArray(summary.open_by_age) ? summary.open_by_age : [];
    const maxAge = Math.max(0, ...ages.map(row => finite(row?.count) ? row.count : 0));
    const ageSection = h('section', {}, h('h3', {}, t('reg_open_age')), h('div', {class: 'reg-age-bars'},
      ...ages.filter(row => AGE_KEYS[row?.bucket] && finite(row.count)).map(row => bar(t(AGE_KEYS[row.bucket]), row.count, maxAge, {'data-reg-age': row.bucket}))));
    const unknownAge = finite(summary.open_without_known_age) ? summary.open_without_known_age : coverage?.open_without_known_age;
    if (finite(unknownAge)) ageSection.append(h('p', {class: 'reg-note', 'data-reg-unknown-age': ''}, t('reg_without_age', {n: unknownAge})));
    const weeks = (Array.isArray(summary.weekly) ? summary.weekly : []).filter(week => labelSegment(week?.week_start) && finite(week.opened) && finite(week.resolved));
    const maxWeek = Math.max(0, ...weeks.flatMap(row => [row.opened, row.resolved]));
    const chart = h('div', {class: 'reg-chart'});
    for (const week of weeks) {
      const opened = h('span', {class: 'reg-week-open'});
      const resolved = h('span', {class: 'reg-week-resolved'});
      opened.style.height = maxWeek > 0 ? `${Math.round(week.opened / maxWeek * 32)}px` : '0px';
      resolved.style.height = maxWeek > 0 ? `${Math.round(week.resolved / maxWeek * 32)}px` : '0px';
      chart.append(h('div', {class: 'reg-week', role: 'img', 'aria-label': t('reg_week_value', {week: week.week_start, opened: week.opened, resolved: week.resolved})},
        h('div', {class: 'reg-week-bars', 'aria-hidden': 'true'}, opened, resolved)));
    }
    const weekSection = h('section', {}, h('h3', {}, t('reg_weekly')),
      h('div', {class: 'reg-legend'}, h('span', {}, h('span', {class: 'reg-swatch', 'aria-hidden': 'true'}), t('reg_opened')),
        h('span', {}, h('span', {class: 'reg-swatch resolved', 'aria-hidden': 'true'}), t('reg_resolved'))),
      chart);
    if (weeks.length) weekSection.append(h('div', {class: 'reg-scale'}, h('span', {}, String(weeks[0].week_start).slice(5)), h('span', {}, String(weeks[weeks.length - 1].week_start).slice(5))));
    const reasons = Array.isArray(summary.blocking_reasons) ? summary.blocking_reasons : null;
    const reasonSection = h('section', {'data-reg-reasons': ''}, h('h3', {}, t('reg_blocking')));
    if (reasons && !reasons.length) reasonSection.append(h('p', {class: 'reg-note', 'data-reg-no-reasons': ''}, t('reg_no_reasons')));
    else if (reasons) {
      const recorded = reasons.filter(row => labelSegment(row?.reason) && finite(row.count));
      const maxReason = Math.max(0, ...recorded.map(row => row.count));
      reasonSection.append(h('div', {class: 'reg-reasons'}, ...recorded.slice(0, 3).map(row => {
        const track = h('span', {class: 'reg-bar-track', 'aria-hidden': 'true'}, h('span', {class: 'reg-bar-fill'}));
        track.firstChild.style.width = maxReason > 0 ? `${Math.max(0, Math.min(100, Math.round(row.count / maxReason * 100)))}%` : '0%';
        return h('div', {class: 'reg-reason', role: 'img', 'aria-label': t('reg_reason_count', {reason: row.reason, count: row.count})},
          h('span', {class: 'reg-reason-name'}, String(row.reason)), track, h('span', {class: 'reg-reason-count'}, String(row.count)));
      })));
      if (recorded.length > 3) reasonSection.append(h('p', {class: 'reg-note', 'data-reg-more-reasons': ''}, t('reg_more', {n: recorded.length - 3})));
    }
    const blocked = finite(summary.blocked_without_reason) ? summary.blocked_without_reason : finite(coverage?.blocked_without_reason) ? coverage.blocked_without_reason : null;
    if (blocked > 0) reasonSection.append(h('p', {class: 'reg-note', 'data-reg-blocked': ''}, t('reg_no_reason_recorded', {n: blocked})));
    summaryHost.append(ageSection, weekSection, reasonSection);
  }
  function relatedItems(row) {
    const related = row.related || {};
    const items = [];
    for (const id of related.requirement_ids || []) if (labelSegment(id)) items.push(h('button', {type: 'button', class: 'reg-chip', 'data-reg-requirement': id,
      title: id, 'aria-label': `${t('reg_requirement')} ${id}`, onclick: () => onOpenRequirement(id)}, t('reg_requirement')));
    for (const id of related.work_item_ids || []) if (labelSegment(id)) items.push(h('button', {type: 'button', class: 'reg-chip', 'data-reg-work': id,
      title: id, 'aria-label': `${t('reg_work')} ${id}`, onclick: () => onOpenWork(id)}, t('reg_work')));
    for (const pr of related.prs || []) {
      if (!finite(pr?.number)) continue;
      const label = t('reg_pr', {n: pr.number});
      if (typeof pr.repository === 'string' && REPO.test(pr.repository)) items.push(h('a', {class: 'reg-chip', 'data-reg-pr': '', href: `https://github.com/${pr.repository}/pull/${pr.number}`,
        target: '_blank', rel: 'noopener noreferrer'}, label));
      else items.push(h('span', {class: 'reg-chip'}, label));
    }
    return items;
  }
  function relatedChips(row, limit) {
    const items = relatedItems(row);
    if (!items.length) return '—';
    const shown = limit ? items.slice(0, limit) : items;
    const extra = limit ? items.length - shown.length : 0;
    return h('div', {class: 'reg-chips'}, ...shown, extra > 0 ? h('span', {class: 'reg-chip-more'}, t('reg_more', {n: extra})) : null);
  }
  function paintOwners() {
    const owners = [...new Set(rows.map(row => labelSegment(row.owner)).filter(Boolean))];
    ownerList.replaceChildren(...owners.map(owner => h('option', {value: owner})));
  }
  function listMeta(row) {
    const parts = [h('span', {}, t('reg_type_' + row.type)), statusNode(row.status)];
    if (labelSegment(row.priority)) parts.push(h('span', {}, row.priority));
    const who = labelSegment(row.owner) ? row.owner : day(row.due);
    if (who) parts.push(h('span', {}, who));
    return h('div', {class: 'reg-list-meta'}, ...parts.flatMap((part, index) => index ? [h('span', {class: 'reg-sep', 'aria-hidden': 'true'}, '·'), part] : [part]));
  }
  function paintList(focusKey) {
    const controls = [];
    const list = h('div', {class: 'reg-list', role: 'list'});
    rows.forEach((row, index) => {
      const active = row.row_id === selected;
      const item = h('button', {type: 'button', class: 'reg-list-row', role: 'listitem', 'data-row-key': row.row_id,
        'aria-selected': String(active), tabindex: active || selected == null && index === 0 ? 0 : -1, onclick: () => openRow(row)},
        h('span', {class: 'reg-list-title'}, dash(row.title)), listMeta(row),
        labelSegment(row.next_step) ? h('span', {class: 'reg-list-next'}, row.next_step) : null);
      item.addEventListener('keydown', event => {
        if (event.target !== item) return;
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? controls.length - 1
          : Math.max(0, Math.min(controls.length - 1, index + (event.key === 'ArrowUp' ? -1 : 1)));
        controls.forEach((node, n) => {node.tabIndex = n === next ? 0 : -1;});
        controls[next]?.focus();
      });
      controls.push(item);
      list.append(item);
    });
    tableHost.replaceChildren(list);
    if (focusKey) tableHost.querySelector(`[data-row-key="${CSS.escape(focusKey)}"]`)?.focus({preventScroll: true});
  }
  function paintTable() {
    const focusKey = tableHost.contains(document.activeElement) ? document.activeElement.closest('[data-row-key]')?.getAttribute('data-row-key') : null;
    const scroll = tableHost.querySelector('.ui-data-table')?.scrollTop || 0;
    if (failed) {tableHost.replaceChildren(); return;}
    if (unavailable) {tableHost.replaceChildren(emptyState({icon: 'circle-alert', title: t('reg_unavailable')})); return;}
    if (!loaded) {tableHost.replaceChildren(skeleton(4)); return;}
    if (!rows.length) {tableHost.replaceChildren(emptyState({icon: 'search', title: t('reg_empty')})); return;}
    if (!wideQuery.matches) {paintList(focusKey); return;}
    const columns = [
      {key: 'type', label: t('reg_col_type'), render: row => h('span', {class: 'reg-type'}, icon(TYPE_ICON[row.type] || 'circle-alert'), h('span', {}, t('reg_type_' + row.type)))},
      {key: 'title', label: t('reg_col_title'), render: row => h('span', {class: 'reg-clamp'}, dash(row.title))},
      {key: 'status', label: t('reg_col_status'), render: row => statusNode(row.status)},
      {key: 'priority', label: t('reg_col_priority'), render: row => dash(row.priority)},
      {key: 'impact', label: t('reg_col_impact'), render: row => dash(row.impact)},
      {key: 'owner', label: t('reg_col_owner'), render: row => dash(row.owner)},
      {key: 'due', label: t('reg_col_due'), render: row => day(row.due) || '—'},
      {key: 'next', label: t('reg_col_next'), render: row => h('span', {class: 'reg-clamp'}, dash(row.next_step))},
      {key: 'related', label: t('reg_col_related'), render: row => relatedChips(row, 2)},
    ];
    tableHost.replaceChildren(dataTable({columns, rows, rowKey: 'row_id', selected, onSelect: (_key, row) => openRow(row)}));
    if (focusKey) tableHost.querySelector(`[data-row-key="${CSS.escape(focusKey)}"]`)?.focus({preventScroll: true});
    const scroller = tableHost.querySelector('.ui-data-table');
    if (scroller) scroller.scrollTop = scroll;
  }
  function paintFooter() {
    footer.replaceChildren();
    const text = !failed && !unavailable && loaded ? shownLine() : '';
    footer.hidden = !text && !nextCursor;
    if (text) footer.append(h('p', {'data-reg-count': ''}, text));
    if (nextCursor && !failed && !unavailable) footer.append(h('button', {type: 'button', class: 'ui-button secondary ui-sm', 'data-reg-more': '', disabled: busy || null,
      onclick: () => void loadList(true)}, t('reg_load_more')));
  }
  function render() {paintSummary(); paintOwners(); paintTable(); paintFooter(); publishMeta();}
  function closeSheet(retain) {
    retainSheet = retain;
    sheet?.close();
    retainSheet = false;
  }
  function linkedRow(ref) {
    if (!ref || typeof ref !== 'object') return null;
    return rows.find(row => row.source?.kind === ref.kind && row.source?.id === ref.id) || null;
  }
  function refLink(ref, label, rowId) {
    if (ref?.kind === 'work_item' && labelSegment(ref.id)) return h('button', {type: 'button', class: 'reg-step-link', 'data-reg-work': ref.id,
      title: ref.id, 'aria-label': `${t('reg_work')} ${ref.id}`, onclick: () => onOpenWork(ref.id)}, label || t('reg_work'));
    if (ref?.kind === 'requirement' && labelSegment(ref.id)) return h('button', {type: 'button', class: 'reg-step-link', 'data-reg-requirement': ref.id,
      title: ref.id, 'aria-label': `${t('reg_requirement')} ${ref.id}`, onclick: () => onOpenRequirement(ref.id)}, label || t('reg_requirement'));
    if (ref?.kind === 'proposal' && isContextRef(ref)) return h('a', {class: 'reg-step-link', href: discussHref({workspaceId, refs: [ref], title: label || t('reg_discussion'), from: 'issues'})}, label || t('reg_kind_proposal'));
    const match = linkedRow(ref);
    if (match && match.row_id !== rowId) return h('button', {type: 'button', class: 'reg-step-link', onclick: () => openRow(match)}, label || kindLabel(ref.kind));
    return label ? h('span', {}, label) : null;
  }
  function detailNodes(record) {
    const row = record.row;
    const unknownCodes = [];
    const pairs = [
      [t('reg_col_type'), t('reg_type_' + row.type)],
      [t('reg_col_source'), kindLabel(row.source?.kind)],
      [t('reg_col_impact'), dash(row.impact)],
      [t('reg_col_priority'), dash(row.priority)],
      [t('reg_col_owner'), dash(row.owner)],
      [t('reg_col_due'), day(row.due) || '—'],
      [t('reg_col_status'), statusNode(row.status)],
      [t('reg_col_next'), dash(row.next_step)],
      [t('reg_col_related'), relatedChips(row)],
    ];
    const facts = h('dl', {class: 'reg-facts'}, ...pairs.flatMap(([label, value]) => [h('dt', {}, label), h('dd', {}, value)]));
    const ordered = (Array.isArray(record.timeline) ? record.timeline : []).map((step, index) => ({step, index}))
      .sort((a, b) => (STEPS.indexOf(a.step?.step) < 0 ? STEPS.length : STEPS.indexOf(a.step.step)) - (STEPS.indexOf(b.step?.step) < 0 ? STEPS.length : STEPS.indexOf(b.step.step)) || a.index - b.index);
    const timeline = h('ol', {class: 'reg-timeline', 'data-reg-timeline': ''}, ...ordered.map(({step}) => {
      if (!known(t, 'reg_step_' + step?.step)) unknownCodes.push(step?.step);
      const meta = joinLabel([step?.actor, when(step?.at)]);
      const title = labelSegment(step?.title);
      const link = refLink(step?.ref, title, row.row_id);
      return h('li', {class: 'reg-step', 'data-reg-step': step?.step || 'unknown'},
        h('h4', {}, stepLabel(step?.step)),
        meta ? h('p', {class: 'reg-note'}, meta) : null,
        h('div', {class: 'reg-step-line'}, link || (title ? h('span', {}, title) : null), labelSegment(step?.status) ? statusNode(step.status) : null));
    }));
    const partial = record.timeline_coverage && record.timeline_coverage.complete === false;
    if (partial && record.timeline_coverage.partial_reason) unknownCodes.push(record.timeline_coverage.partial_reason);
    const head = h('div', {class: 'reg-timeline-head'}, h('h3', {}, t('reg_timeline')), partial ? statusPill('partial') : null);
    const source = isContextRef(row.source) ? row.source : null;
    const title = labelSegment(row.title) || t('reg_discussion');
    const offered = (Array.isArray(row.available_actions) ? row.available_actions : []).filter(action => labelSegment(action?.action));
    const labels = offered.map(action => t(ACTION_KEYS[action.action] || 'reg_action_open'));
    const stepText = labelSegment(row.next_step);
    const primary = stepText && labels.filter(label => label === stepText).length === 1 ? stepText : '';
    const links = h('div', {class: 'reg-actions'},
      h('a', {class: 'ui-button secondary ui-sm', 'data-reg-discuss': '', href: discussHref({workspaceId, refs: source ? [source] : [], title, from: 'issues'})}, t('reg_discuss')),
      ...offered.map((action, index) => {
        const key = ACTION_KEYS[action.action];
        if (!key) unknownCodes.push(action.action);
        return h('button', {type: 'button', class: `ui-button ${labels[index] === primary ? 'primary' : 'secondary'} ui-sm`, 'data-reg-action': action.action,
          onclick: () => onAction(action)}, labels[index]);
      }),
      h('a', {class: 'ui-button secondary ui-sm', 'data-reg-propose': '', href: discussHref({workspaceId, refs: source ? [source] : [], title: t('reg_change_title', {title}), from: 'issues'})}, t('reg_propose')));
    const fieldKey = {title: 'reg_col_title', status: 'reg_col_status', owner: 'reg_col_owner', due: 'reg_col_due', impact: 'reg_col_impact', priority: 'reg_col_priority', next_step: 'reg_col_next', created_at: 'reg_created', updated_at: 'reg_updated'};
    const detailBits = [];
    if (row.type_basis === 'neutral') detailBits.push(h('p', {}, t('reg_neutral')));
    for (const [name, code] of Object.entries(row.unknowns || {})) {
      if (!labelSegment(code)) continue;
      const recognised = Boolean(known(t, 'ui_reason_' + code));
      const label = fieldKey[name] ? t(fieldKey[name]) : t('ui_reason_generic');
      detailBits.push(h('p', {}, `${label}: ${reasonLabel(t, code)}`));
      if (!recognised) detailBits.push(h('p', {class: 'reg-note'}, String(code)));
    }
    for (const code of unknownCodes) if (labelSegment(code)) detailBits.push(h('p', {class: 'reg-note'}, String(code)));
    const extra = detailBits.length ? disclosure({label: t('reg_details'), key: `reg-details:${workspaceId}:${row.row_id}`,
      body: h('div', {class: 'reg-detail-body'}, ...detailBits)}) : null;
    return [h('div', {class: 'reg-sheet-main'}, facts, head, timeline, extra), links];
  }
  function paintInspector(record) {
    const row = record.row;
    const title = labelSegment(row.title) || t('reg_type_' + row.type);
    const focused = sheet?.contains(document.activeElement);
    if (focused && sheet?.getAttribute('data-reg-row') === row.row_id) return;
    if (!sheet || sheet.getAttribute('data-reg-row') !== row.row_id) {
      closeSheet(true);
      opening = true;
      sheet = inspector({title, onClose: () => {sheet = null; if (!retainSheet && !opening) preference.inspector = false;}});
      opening = false;
      sheet.setAttribute('data-reg-inspector', '');
      sheet.setAttribute('data-reg-row', row.row_id);
      layout.append(sheet);
    } else sheet.querySelector('h2').textContent = title;
    sheet.body.replaceChildren(...detailNodes(record).filter(Boolean));
  }
  function openRow(row) {
    selected = row.row_id;
    preference.selected = selected;
    preference.inspector = true;
    render();
    void loadDetail(row.row_id);
  }
  function checkList(data) {
    if (data?.workspace_id !== workspaceId || !Array.isArray(data.rows) || (data.next_cursor != null && typeof data.next_cursor !== 'string')) throw Error(t('pm_scope_mismatch'));
    const complete = data.coverage?.complete === true;
    if (!complete && (data.total_by_type != null || data.summary != null)) throw Error(t('pm_scope_mismatch'));
    if (complete && data.total_by_type != null) {
      if (typeof data.total_by_type !== 'object') throw Error(t('pm_scope_mismatch'));
      for (const type of TYPES) if (data.total_by_type[type] != null && !finite(data.total_by_type[type])) throw Error(t('pm_scope_mismatch'));
    }
    for (const row of data.rows) {
      if (!ROW_ID.test(row?.row_id || '') || !TYPES.includes(row.type) || !row.source || typeof row.source.kind !== 'string' || !labelSegment(row.source.id)) throw Error(t('pm_scope_mismatch'));
    }
    return data;
  }
  function checkDetail(data, rowId) {
    if (data?.workspace_id !== workspaceId || data.row?.row_id !== rowId || !Array.isArray(data.timeline)) throw Error(t('pm_scope_mismatch'));
    return data;
  }
  function query(append) {
    const params = new URLSearchParams();
    if (typeValue) params.set('type', typeValue);
    if (statusSelect.value) params.set('status', statusSelect.value);
    if (ownerInput.value.trim()) params.set('owner', ownerInput.value.trim());
    if (searchInput.value.trim()) params.set('q', searchInput.value.trim());
    if (append && nextCursor) params.set('after', nextCursor);
    params.set('limit', '50');
    return params;
  }
  async function loadList(append) {
    if (!active) return;
    const ticket = ++serial;
    busy = true;
    paintFooter();
    try {
      const data = checkList(await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/register?${query(append)}`));
      alive();
      if (ticket !== serial) return;
      unavailable = false; failed = null; loaded = true;
      if (!append) rows = [];
      const seen = new Set(rows.map(row => row.row_id));
      for (const row of data.rows) if (!seen.has(row.row_id)) {seen.add(row.row_id); rows.push(row);}
      nextCursor = data.next_cursor || null;
      coverage = data.coverage || {};
      totals = coverage.complete === true ? data.total_by_type : null;
      summary = coverage.complete === true ? data.summary : null;
      if (selected && !rows.some(row => row.row_id === selected)) {selected = null; preference.inspector = false; closeSheet(false);}
      statusLine.replaceChildren();
      render();
      if (preference.inspector && selected) void loadDetail(selected);
    } catch (error) {
      try {alive();} catch {return;}
      if (ticket !== serial) return;
      if ([403, 404].includes(error.status)) withdraw();
      else fail(error);
    } finally {if (ticket === serial) {busy = false; paintFooter();}}
  }
  async function loadDetail(rowId) {
    const ticket = ++detailSerial;
    const listTicket = serial;
    try {
      const data = checkDetail(await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/register/${encodeURIComponent(rowId)}`), rowId);
      alive();
      if (ticket !== detailSerial || listTicket !== serial || !preference.inspector || selected !== rowId) return;
      paintInspector(data);
    } catch (error) {
      try {alive();} catch {return;}
      if (ticket !== detailSerial || listTicket !== serial) return;
      if ([403, 404].includes(error.status)) {
        rows = rows.filter(row => row.row_id !== rowId);
        if (selected === rowId) {selected = null; preference.inspector = false; closeSheet(false);}
        render();
        void refresh();
        return;
      }
      statusLine.replaceChildren(inlineError({error, retry: () => void loadDetail(rowId)}));
    }
  }
  function fail(error) {
    serial++;
    busy = false;
    rows = []; nextCursor = null; coverage = null; totals = null; summary = null;
    loaded = true; unavailable = false; failed = error; selected = null; preference.inspector = false;
    closeSheet(false);
    onMeta(null);
    statusLine.replaceChildren(inlineError({error, retry: () => void refresh()}));
    render();
  }
  function withdraw() {
    serial++; detailSerial++; busy = false;
    rows = []; nextCursor = null; coverage = null; totals = null; summary = null;
    loaded = true; unavailable = true; failed = null; selected = null; preference.inspector = false;
    closeSheet(false);
    statusLine.replaceChildren();
    onMeta(null);
    render();
  }
  async function refresh() {
    if (!active) return;
    await loadList(false);
  }
  function rememberScroll() {
    preference.scrollY = window.scrollY;
    preference.tableScroll = tableHost.querySelector('.ui-data-table')?.scrollTop || 0;
  }
  function restoreScroll() {
    const y = finite(preference.scrollY) ? preference.scrollY : 0;
    const tableY = finite(preference.tableScroll) ? preference.tableScroll : 0;
    requestAnimationFrame(() => {
      window.scrollTo(0, y);
      const scroller = tableHost.querySelector('.ui-data-table');
      if (scroller) scroller.scrollTop = tableY;
    });
  }
  function setActive(value) {
    const next = Boolean(value);
    if (active === next) {if (active) publishMeta(); else onMeta(null); return;}
    active = next;
    if (!active) {onMeta(null); closeSheet(true); return;}
    void refresh();
  }
  function dispose() {
    active = false; clearTimeout(debounce); serial++; detailSerial++;
    wideQuery.removeEventListener('change', onWide);
    readFilters(); rememberScroll(); closeSheet(false);
  }
  render();
  return {element, refresh, withdraw, rememberScroll, restoreScroll, setActive, dispose};
}
