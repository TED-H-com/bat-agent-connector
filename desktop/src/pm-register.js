import {timeStamp} from './ui/stamps.js';
import {viewState} from './ui/view-state.js';
import {searchField, discussLink, dataTable, disclosure, emptyState, inlineError, inspector, segmented, skeleton, statusPill} from './ui/components.js';
import {statusVocabulary, reasonLabel} from './ui/ui-strings.js';
import {icon} from './ui/icons.js';
import {discussHref, isContextRef} from './pm-discuss-link.js';
import {joinLabel, labelSegment} from './label-text.js';
import {locale} from './i18n.js';
import {acceptedFinding, findingName, findingSeverity} from './ui/finding-name.js';

// Read-only projection. Row ids and cursors are opaque: pass them through unchanged.
// Age bars are informational. The register API has no age filter.
const TYPES = ['risk', 'issue', 'action', 'change', 'decision'];
const ROW_ID = /^(?:governance|finding|repair|follow_up|proposal|issue_mapping|meeting|plan_revision)\.[A-Za-z0-9._-]{1,180}$/;
const STATUSES = ['open', 'monitoring', 'observed_open', 'blocked', 'tracking', 'needs_review', 'proposed', 'in_progress', 'draft', 'reported_resolved', 'reported_done', 'adopted', 'rejected', 'cancelled', 'succeeded', 'failed', 'running', 'uncertain', 'needs_attention', 'waiting_checks', 'waiting_external', 'accepted', 'format_valid', 'recorded_discussion_decision', 'continue_maintenance', 'bounded_repair_proposal', 'adopted_direction'];
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
const TONES = {open: 'info', monitoring: 'info', observed_open: 'info', tracking: 'info', failed: 'bad', proposed: 'review', needs_review: 'review', succeeded: 'ok', running: 'info', uncertain: 'review', needs_attention: 'review', waiting_checks: 'info', waiting_external: 'info', accepted: 'info', format_valid: 'review', recorded_discussion_decision: 'info', continue_maintenance: 'info', bounded_repair_proposal: 'review', adopted_direction: 'ok'};
const REPO = /^[\w.-]+\/[\w.-]+$/;
const DEPTH_PAGES = 20;

const finite = value => typeof value === 'number' && Number.isFinite(value);
const known = (t, key) => {const text = t(key); return text === key ? '' : text;};
const when = value => timeStamp(value, {empty:''});
const day = value => timeStamp(value, {dateOnly:true, empty:''});

export function workspaceRegister({h, t, api, alive, namespace, workspaceId, errorBox, onMeta = () => {}, onOpenWork = () => {}, onOpenRequirement = () => {}, onAction = () => {}, nameFor = () => ''}) {
  const preference = viewState(namespace, workspaceId, 'issues');
  let serial = 0, detailSerial = 0, active = false, busy = false, rows = [], nextCursor = null, coverage = null, totals = null, summary = null;
  let trail = null, trailAgain = false, trailQuery = '';
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
    list: ownerListId, 'aria-label': t('reg_filter_owner'), placeholder: t('reg_filter_owner'), autocomplete: 'off', maxlength: '200', oninput: queueReload});
  const searchInput = h('input', {type: 'search', 'data-reg-search': '', 'aria-label': t('reg_filter_search'), placeholder: t('reg_filter_search'), maxlength: '200', oninput: queueReload});
  statusSelect.value = STATUSES.includes(preference.status) ? preference.status : '';
  ownerInput.value = preference.owner || '';
  searchInput.value = preference.q || '';
  const typeControl = segmented({label: t('reg_col_type'), value: typeValue, onChange: value => {typeValue = value || ''; reloadNow();},
    options: [{value: '', label: t('reg_all')}, ...TYPES.map(type => ({value: type, label: t('reg_type_' + type)}))]});
  [...typeControl.querySelectorAll('button')].forEach((node, index) => node.setAttribute('data-reg-type', index ? TYPES[index - 1] : ''));
  const filters = h('div', {class: 'reg-toolbar'}, typeControl,
    h('div', {class: 'reg-toolbar-filters'}, statusSelect, ownerInput, ownerList,
      searchField(searchInput, {class:'reg-search'})));
  const mainCol = h('div', {class: 'reg-main'}, filters, trends, statusLine, tableHost, footer);
  const layout = h('div', {class: 'reg-layout'}, mainCol);
  const element = h('section', {'data-pm-register': ''}, layout);
  const wideQuery = matchMedia('(min-width: 801px)');
  function onWide() {trends.open = wideQuery.matches; paintTable();}
  wideQuery.addEventListener('change', onWide);
  trends.open = wideQuery.matches;
  function vocabularyStatus(status) {
    // Operation "accepted" means accepted for processing, not the vocabulary's inspected result.
    return status !== 'accepted' && Object.hasOwn(statusVocabulary, status);
  }
  function statusKnown(status) {return vocabularyStatus(status) || Boolean(known(t, 'reg_status_' + status));}
  function statusLabel(status) {
    if (vocabularyStatus(status)) return t('ui_status_' + status);
    return known(t, 'reg_status_' + status) || t('ui_reason_generic');
  }
  function statusNode(status) {
    if (!labelSegment(status)) return statusPill('unknown');
    if (vocabularyStatus(status)) return statusPill(status);
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
    if (typeValue && coverage?.complete === true) {
      const count = totals && finite(totals[typeValue]) ? totals[typeValue] : rows.length;
      onMeta(t('reg_matching', {n: count}));
      return;
    }
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
    if (weeks.length) weekSection.append(h('div', {class: 'reg-scale'}, h('span', {}, day(weeks[0].week_start)), h('span', {}, day(weeks[weeks.length - 1].week_start))));
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
  function displayTitle(row) {
    const title = labelSegment(row?.title) || labelSegment(row?.statement);
    if (title) return title;
    const finding = row?.source?.kind === 'finding' || String(row?.row_id || '').startsWith('finding.');
    if (!finding) return [t('reg_type_' + row.type), labelSegment(nameFor(row?.source?.kind, row?.source?.id)) || labelSegment(row?.source?.label) || t('ui_reason_not_recorded')].filter(Boolean).join(' · ');
    const qualifier = labelSegment(row.impact) || labelSegment(row.priority);
    const kind = t('reg_kind_finding');
    const fallback = qualifier ? `${kind} · ${qualifier}` : kind;
    return findingName(row?.finding, locale, fallback);
  }
  function findingFactPairs(identity) {
    if (!identity) return [];
    const pairs = [];
    const severity = findingSeverity(identity.severity, locale);
    if (severity) pairs.push([t('fn_severity'), h('span', {class: `ui-pill tone-${severity.tone}`, 'data-finding-severity': severity.severity}, severity.label)]);
    if (identity.package) pairs.push([t('fn_package'), identity.package]);
    if (identity.ecosystem) pairs.push([t('fn_ecosystem'), identity.ecosystem]);
    if (identity.labels?.length) pairs.push([t('fn_labels'), h('div', {class: 'reg-chips', 'data-finding-labels': ''}, ...identity.labels.map(label => h('span', {class: 'reg-chip', 'data-finding-label': ''}, label)))]);
    if (identity.manifests?.length) pairs.push([t('fn_manifests'), h('div', {'data-finding-manifests': ''}, ...identity.manifests.map(path => h('div', {}, path)))]);
    return pairs;
  }
  function findingContext(action) {
    const ref = action?.required?.finding_ref;
    if (!ref || typeof ref !== 'object') return null;
    const id = labelSegment(ref.finding_id) || labelSegment(ref.id);
    if (!id) return null;
    const built = {kind: 'finding', id, observation_id: ref.observation_id, document_digest: ref.document_digest};
    return isContextRef(built) ? built : null;
  }
  function contextRefs(row) {
    const refs = [];
    if (isContextRef(row?.source)) refs.push(row.source);
    for (const action of Array.isArray(row?.available_actions) ? row.available_actions : []) {
      const finding = findingContext(action);
      if (finding && !refs.some(item => item.kind === finding.kind && item.id === finding.id && item.observation_id === finding.observation_id && item.document_digest === finding.document_digest)) refs.push(finding);
    }
    return refs.slice(0, 8);
  }
  function namedChip(kind, id, fallback, attrs, onclick) {
    const name = labelSegment(nameFor(kind, id));
    const text = name || fallback;
    return h('button', {type: 'button', class: 'reg-chip', title: name || id, 'aria-label': name || `${fallback} ${id}`, onclick, ...attrs},
      h('span', {class: 'reg-chip-label'}, text));
  }
  function relatedItems(row) {
    const related = row.related || {};
    const items = [];
    for (const id of related.requirement_ids || []) if (labelSegment(id)) items.push(namedChip('requirement', id, t('reg_requirement'), {'data-reg-requirement': id}, () => onOpenRequirement(id)));
    for (const id of related.work_item_ids || []) if (labelSegment(id)) items.push(namedChip('work_item', id, t('reg_work'), {'data-reg-work': id}, () => onOpenWork(id)));
    for (const pr of related.prs || []) {
      if (!finite(pr?.number)) continue;
      const label = t('reg_pr', {n: pr.number});
      if (typeof pr.repository === 'string' && REPO.test(pr.repository)) items.push(h('a', {class: 'reg-chip', 'data-reg-pr': '', href: `https://github.com/${pr.repository}/pull/${pr.number}`,
        target: '_blank', rel: 'noopener noreferrer'}, h('span', {class: 'reg-chip-label'}, label)));
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
    const parts = [h('span', {class: 'reg-list-type'}, t('reg_type_' + row.type)), statusNode(row.status)];
    if (labelSegment(row.priority)) parts.push(h('span', {class: 'reg-list-priority'}, row.priority));
    const who = labelSegment(row.owner) ? row.owner : day(row.due);
    if (who) parts.push(h('span', {class: 'reg-list-who'}, who));
    return h('div', {class: 'reg-list-meta'}, ...parts);
  }
  function paintList(focusKey) {
    const controls = [];
    const list = h('div', {class: 'reg-list', role: 'list', tabindex: '-1'});
    rows.forEach((row, index) => {
      const current = row.row_id === selected;
      const button = h('button', {type: 'button', class: 'reg-list-row', 'data-row-key': row.row_id,
        'aria-current': current ? 'true' : null, 'aria-selected': current ? 'true' : null,
        tabindex: current || selected == null && index === 0 ? 0 : -1, onclick: () => openRow(row)},
        h('span', {class: 'reg-list-title'}, dash(displayTitle(row))), listMeta(row),
        labelSegment(row.next_step) ? h('span', {class: 'reg-list-next'}, row.next_step) : null);
      button.addEventListener('keydown', event => {
        if (event.target !== button) return;
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? controls.length - 1
          : Math.max(0, Math.min(controls.length - 1, index + (event.key === 'ArrowUp' ? -1 : 1)));
        controls.forEach((node, n) => {node.tabIndex = n === next ? 0 : -1;});
        controls[next]?.focus();
      });
      controls.push(button);
      list.append(h('div', {role: 'listitem'}, button));
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
      {key: 'title', label: t('reg_col_title'), render: row => h('span', {class: 'reg-clamp'}, dash(displayTitle(row)))},
      {key: 'status', label: t('reg_col_status'), render: row => statusNode(row.status)},
      {key: 'priority', label: t('reg_col_priority'), render: row => dash(row.priority)},
      {key: 'impact', secondary:true, label: t('reg_col_impact'), render: row => dash(row.impact)},
      {key: 'owner', secondary:true, label: t('reg_col_owner'), render: row => h('span', {class: 'reg-owner-cell'}, dash(row.owner))},
      {key: 'due', secondary:true, label: t('reg_col_due'), render: row => day(row.due) || '—'},
      {key: 'next', secondary:true, label: t('reg_col_next'), render: row => h('span', {class: 'reg-clamp'}, dash(row.next_step))},
      {key: 'related', secondary:true, label: t('reg_col_related'), render: row => relatedChips(row, 2)},
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
    const identity = acceptedFinding(row.finding);
    pairs.push(...findingFactPairs(identity));
    const facts = h('dl', {class: 'reg-facts'}, ...pairs.flatMap(([label, value]) => [h('dt', {}, label), h('dd', {}, value)]));
    if (labelSegment(row.status) && !statusKnown(row.status)) unknownCodes.push(row.status);
    const timeline = h('ol', {class: 'reg-timeline', 'data-reg-timeline': ''}, ...(Array.isArray(record.timeline) ? record.timeline : []).map(step => {
      if (labelSegment(step?.step) && !known(t, 'reg_step_' + step.step)) unknownCodes.push(step.step);
      if (labelSegment(step?.status) && !statusKnown(step.status)) unknownCodes.push(step.status);
      const meta = h('span', {}, step?.actor || '', step?.actor && step?.at ? ' · ' : '', when(step?.at));
      const title = labelSegment(step?.title);
      const link = refLink(step?.ref, title, row.row_id);
      return h('li', {class: 'reg-step', 'data-reg-step': step?.step || 'unknown'},
        h('h4', {}, stepLabel(step?.step)),
        meta ? h('p', {class: 'reg-note'}, meta) : null,
        h('div', {class: 'reg-step-line'}, link || (title ? h('span', {}, title) : null), labelSegment(step?.status) ? statusNode(step.status) : null));
    }));
    const partial = record.timeline_coverage && record.timeline_coverage.complete === false;
    const head = h('div', {class: 'reg-timeline-head'}, h('h3', {}, t('reg_timeline')), partial ? statusPill('partial') : null);
    const refs = contextRefs(row);
    const title = displayTitle(row) || t('reg_discussion');
    const offered = (Array.isArray(row.available_actions) ? row.available_actions : []).filter(action => labelSegment(action?.action));
    const labels = offered.map(action => t(ACTION_KEYS[action.action] || 'reg_action_open'));
    const stepText = labelSegment(row.next_step);
    const primary = stepText && labels.filter(label => label === stepText).length === 1 ? stepText : '';
    const links = h('div', {class: 'reg-actions'},
      discussLink({workspaceId, refs, title, from:'issues'}, {'data-reg-discuss':''}),
      ...offered.map((action, index) => {
        const key = ACTION_KEYS[action.action];
        if (!key) unknownCodes.push(action.action);
        return h('button', {type: 'button', class: `ui-button ${labels[index] === primary ? 'primary' : 'secondary'} ui-sm`, 'data-reg-action': action.action,
          onclick: () => onAction(action)}, labels[index]);
      }),
      h('a', {class: 'ui-button secondary ui-sm', 'data-reg-propose': '', href: discussHref({workspaceId, refs, title: t('reg_change_title', {title}), from: 'issues'})}, t('reg_propose')));
    const fieldKey = {title: 'reg_col_title', status: 'reg_col_status', owner: 'reg_col_owner', due: 'reg_col_due', impact: 'reg_col_impact', priority: 'reg_col_priority', next_step: 'reg_col_next', created_at: 'reg_created', updated_at: 'reg_updated', pull_requests: 'reg_pull_requests'};
    const detailBits = [];
    if (row.type_basis === 'neutral') detailBits.push(h('p', {}, t('reg_neutral')));
    if (row.status === 'format_valid' || (Array.isArray(record.timeline) && record.timeline.some(step => step?.status === 'format_valid'))) detailBits.push(h('p', {}, t('reg_format_checked_help')));
    if (partial && labelSegment(record.timeline_coverage.partial_reason)) {
      const code = String(record.timeline_coverage.partial_reason);
      const recognised = Boolean(known(t, 'ui_reason_' + code));
      detailBits.push(h('p', {}, recognised ? reasonLabel(t, code) : t('ui_reason_generic')));
      if (!recognised) detailBits.push(h('p', {class: 'reg-note'}, code));
    }
    const namedFromFinding = Boolean(findingName(row.finding, locale, ''));
    for (const [name, code] of Object.entries(row.unknowns || {})) {
      if (!labelSegment(code) || String(name).startsWith('finding.')) continue;
      if (namedFromFinding && name === 'title') continue;
      const recognised = Boolean(known(t, 'ui_reason_' + code));
      const label = fieldKey[name] ? t(fieldKey[name]) : t('ui_reason_generic');
      detailBits.push(h('p', {}, t('reg_pair', {label, value: reasonLabel(t, code)})));
      if (!recognised) detailBits.push(h('p', {class: 'reg-note'}, String(code)));
    }
    if (identity?.ghsa_id) detailBits.push(h('p', {'data-finding-ghsa': ''}, t('fn_ghsa'), locale === 'zh-TW' ? '：' : ': ', h('code', {}, identity.ghsa_id)));
    const seenCodes = new Set();
    for (const code of unknownCodes) if (labelSegment(code) && !seenCodes.has(String(code))) {seenCodes.add(String(code)); detailBits.push(h('p', {class: 'reg-note'}, String(code)));}
    const extra = detailBits.length ? disclosure({label: t('reg_details'), key: `reg-details:${workspaceId}:${row.row_id}`,
      body: h('div', {class: 'reg-detail-body'}, ...detailBits)}) : null;
    return [h('div', {class: 'reg-sheet-main'}, facts, head, timeline, extra), links];
  }
  function focusSelectedRow() {
    const row = selected ? tableHost.querySelector(`[data-row-key="${CSS.escape(selected)}"]`) : null;
    const fallback = tableHost.querySelector('.ui-data-table') || tableHost.querySelector('.reg-list') || tableHost;
    (row || fallback)?.focus();
  }
  function prepareInspector(row) {
    const title = displayTitle(row) || t('reg_type_' + row.type);
    if (!sheet || sheet.getAttribute('data-reg-row') !== row.row_id) {
      closeSheet(true);
      opening = true;
      sheet = inspector({title, onClose: () => {
        sheet = null;
        if (!retainSheet && !opening) preference.inspector = false;
        // close() focuses its captured opener after onClose. A rebuilt row is disconnected, so focus again on the next turn.
        queueMicrotask(() => {if (!opening && !sheet && active) focusSelectedRow();});
      }});
      opening = false;
      sheet.setAttribute('data-reg-inspector', '');
      sheet.setAttribute('data-reg-row', row.row_id);
      sheet.setAttribute('data-reg-loading', '');
      sheet.body.append(skeleton(3));
      layout.append(sheet);
    } else sheet.querySelector('h2').textContent = title;
  }
  function paintInspector(record) {
    if (!active) return;
    prepareInspector(record.row);
    sheet.removeAttribute('data-reg-loading');
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
  function pageQuery(cursor) {
    const params = new URLSearchParams();
    if (typeValue) params.set('type', typeValue);
    if (statusSelect.value) params.set('status', statusSelect.value);
    if (ownerInput.value.trim()) params.set('owner', ownerInput.value.trim());
    if (searchInput.value.trim()) params.set('q', searchInput.value.trim());
    if (cursor) params.set('after', cursor);
    params.set('limit', '50');
    return params;
  }
  async function readPage(cursor, ticket) {
    const data = checkList(await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/register?${pageQuery(cursor)}`));
    alive();
    if (!active || ticket !== serial) return null;
    return data;
  }
  async function loadList(append) {
    if (!active || (append && !nextCursor)) return;
    const ticket = ++serial;
    busy = true;
    paintFooter();
    const prior = rows.slice();
    const depth = prior.length;
    const kept = selected ? prior.find(row => row.row_id === selected) || null : null;
    const beforeSig = kept ? JSON.stringify(kept) : '';
    try {
      const merged = append ? prior.slice() : [];
      const seen = new Set(merged.map(row => row.row_id));
      let cursor = append ? nextCursor : null;
      let meta = null;
      let lastCursor = null;
      const target = append ? depth + 1 : Math.max(depth, 1);
      for (let page = 0; page < (append ? 1 : DEPTH_PAGES) && active && ticket === serial; page++) {
        const data = await readPage(cursor, ticket);
        if (!data) return;
        if (!meta) meta = data;
        for (const row of data.rows) if (!seen.has(row.row_id)) {seen.add(row.row_id); merged.push(row);}
        lastCursor = data.next_cursor || null;
        cursor = lastCursor;
        if (append || !cursor || merged.length >= target) break;
      }
      if (!active || ticket !== serial || !meta) return;
      let stale = false;
      if (!append && kept && !seen.has(kept.row_id)) {merged.push(kept); seen.add(kept.row_id); stale = true;}
      if (selected && !seen.has(selected)) {selected = null; preference.selected = null; preference.inspector = false; closeSheet(false);}
      const current = selected ? merged.find(row => row.row_id === selected) : null;
      const changed = Boolean(beforeSig) && JSON.stringify(current) !== beforeSig;
      rows = merged;
      nextCursor = lastCursor;
      coverage = meta.coverage || {};
      totals = coverage.complete === true ? meta.total_by_type : null;
      summary = coverage.complete === true ? meta.summary : null;
      unavailable = false; failed = null; loaded = true;
      statusLine.replaceChildren();
      render();
      // Below 1200 the inspector is a modal sheet. A refresh or a return keeps the selection
      // and leaves that sheet closed. The side panel (1200 and above) still restores.
      const showSheet = Boolean(sheet) || matchMedia('(min-width: 1200px)').matches;
      if (preference.inspector && selected && (changed || stale || (showSheet && (!sheet || sheet.hasAttribute('data-reg-loading'))))) void loadDetail(selected, showSheet);
    } catch (error) {
      try {alive();} catch {return;}
      if (!active || ticket !== serial) return;
      if ([403, 404].includes(error.status)) withdraw();
      else fail(error);
    } finally {if (ticket === serial) {busy = false; paintFooter();}}
  }
  async function loadDetail(rowId, paint = true) {
    if (!active) return;
    const ticket = ++detailSerial;
    const listTicket = serial;
    try {
      const data = checkDetail(await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/register/${encodeURIComponent(rowId)}`), rowId);
      alive();
      if (!active || ticket !== detailSerial || listTicket !== serial || !preference.inspector || selected !== rowId) return;
      // A closed modal stays closed. The read still drops the row when the detail is gone.
      if (!paint) return;
      paintInspector(data);
    } catch (error) {
      try {alive();} catch {return;}
      if (!active || ticket !== detailSerial || listTicket !== serial) return;
      if ([403, 404].includes(error.status)) {
        rows = rows.filter(row => row.row_id !== rowId);
        totals = null; summary = null;
        if (selected === rowId) {selected = null; preference.selected = null; preference.inspector = false; closeSheet(false);}
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
  function refresh() {
    if (!active) return Promise.resolve();
    const query = String(pageQuery());
    // Same filters share one trailing read. A new filter invalidates the read in flight.
    if (trail && trailQuery === query) {trailAgain = true; return trail;}
    const run = (async () => {
      try {
        do {trailAgain = false; trailQuery = String(pageQuery()); await loadList(false);}
        while (trailAgain && active && String(pageQuery()) === trailQuery);
      } finally {if (trail === run) {trail = null; trailQuery = '';}}
    })();
    trail = run;
    trailQuery = query;
    return run;
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
    if (!active) {serial++; detailSerial++; busy = false; onMeta(null); closeSheet(true); return;}
    // Reserve the returning side panel before exposing the table. Opening it only
    // after the read hides secondary chips that may already be under the pointer.
    const selectedRow = rows.find(row => row.row_id === selected);
    if (preference.inspector && selectedRow && matchMedia('(min-width: 1200px)').matches) prepareInspector(selectedRow);
    return refresh().then(() => {
      if (!active || sheet || matchMedia('(min-width: 1200px)').matches) return;
      const current = document.activeElement;
      // A related-item inspector or its destination heading may own focus while
      // this first read settles. Do not replace that visible reading position.
      if (current && current !== document.body && current.getClientRects().length && !current.closest('[hidden], [inert]')) return;
      focusSelectedRow();
    });
  }
  function dispose() {
    active = false; clearTimeout(debounce); serial++; detailSerial++;
    wideQuery.removeEventListener('change', onWide);
    readFilters(); rememberScroll(); closeSheet(false);
  }
  async function openRef(ref) {
    if (!isContextRef(ref)) return;
    const rowId = `${ref.kind}.${ref.id}`;
    if (!ROW_ID.test(rowId) || !active) return;
    // Finish the destination's initial list read before selecting. Its normal
    // serial/withdrawal guards still govern the subsequent exact record read.
    await refresh();
    try {alive();} catch {return;}
    if (!active || unavailable || failed) return;
    selected = rowId; preference.selected = rowId; preference.inspector = true;
    render();
    await loadDetail(rowId);
  }
  render();
  return {element, refresh, withdraw, rememberScroll, restoreScroll, setActive, dispose, closeSheet, openRef};
}
