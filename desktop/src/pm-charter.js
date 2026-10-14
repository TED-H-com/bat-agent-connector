import {discussHref} from './pm-discuss-link.js';
import {timeStamp, timeText, timeLabel} from './ui/stamps.js';
import {emptyState, discussLink, disclosure, iconButton, button} from './ui/components.js';
import {planDocument} from './pm-plans.js';
import {briefExport} from './pm-brief-export.js';
import {viewState} from './ui/view-state.js';
import {labelSegment} from './label-text.js';

const sections = ['definition', 'scope', 'schedule', 'resources', 'risks', 'decisions'];
// A report is a projection of one central DocumentSnapshot, never today's plan or quota.
export function charterSections(snapshot) {
  const document = snapshot?.baseline?.document;
  const statements = (...domains) => domains.flatMap(domain => (document?.sections?.[domain]?.statements || [])
    .map(row => ({text: row.value, status: row.status, source: row.source_ref, input_id: row.input_id})));
  const items = key => (snapshot?.brief?.[key]?.items || []).map(row => ({text: row.text, status: row.status, source: row.source, details: row.details}));
  return {
    definition: statements('governance', 'stakeholders', 'quality'),
    scope: statements('scope'), schedule: [...statements('schedule'), ...items('delivery')],
    resources: statements('resources', 'finance'), risks: [...statements('risk'), ...items('blockers')],
    decisions: items('decisions').filter(row => row.source?.kind !== 'plan_adoption'),
  };
}

// Match original statement identities across reports, rather than their array positions.
// Revision/source metadata can change without changing a statement's visible content.
export function statementChanges(before, after) {
  const identity = row => row.input_id || row.source?.input_id || row.source?.evidence_id || row.source?.work_item_id;
  const equal = (a, b) => a.text === b.text && a.status === b.status;
  const remaining = [...after], unchanged = [], pairs = [];
  for (const row of before) {
    const id = identity(row);
    let index = id ? remaining.findIndex(other => identity(other) === id) : -1;
    if (index < 0) index = remaining.findIndex(other => equal(row, other));
    if (index < 0) pairs.push({before: row, after: null});
    else {
      const [other] = remaining.splice(index, 1);
      if (equal(row, other)) unchanged.push(other);
      else pairs.push({before: row, after: other});
    }
  }
  pairs.push(...remaining.map(row => ({before: null, after: row})));
  return {pairs, unchanged};
}

export function workspaceCharter({h, t, api, alive, namespace, workspaceId, permitted, form, input, labelled, errorBox, onDiscuss, caps, canRead, onChange = () => {}, onControls = () => {}}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  const preference = viewState(namespace, workspaceId, 'overview');
  let serial = 0, head = null, snapshot = null, selectedId = preference.snapshotId || null, latestId = null, history = [], cursor = null, busy = false, historyPages = 1;
  const status = h('div', {role: 'status', class: 'pm-report-status', 'data-report-status': ''}), report = h('div', {class: 'pm-charter-report'}), comparison = h('div');
  const title = input('pm_brief_snapshot_title', {max: 160, required: false});
  const create = form({action: 'pm.snapshot.create', target: {workspace_id: workspaceId}, fields: {title},
    params: values => values.title.trim() ? {title: values.title.trim()} : {},
    preconditions: () => ({expected_plan_version: head.version}), permitted: () => Boolean(head) && !busy && permitted(false),
    recoverPermitted: () => permitted(false), label: 'pm_brief_create', onSaved: result => refresh(result.snapshot_id),
    extra: disclosure({label: t('pm_brief_name_optional'), body: labelled('pm_brief_snapshot_title', title)})});
  // Keep the durable form's tracked controls and recovery listeners intact.
  for (const node of create.box.querySelectorAll('button')) node.classList.add('ui-button', 'ui-md');
  const previous = iconButton({icon: 'chevron-left', label: t('wb_previous'), onClick: () => move(-1)});
  const next = iconButton({icon: 'chevron-right', label: t('wb_next'), onClick: () => move(1)});
  const picker = h('select', {'aria-label': t('wb_history'), onchange: () => void select(picker.value)});
  const compare = button({'data-report-compare': '', variant: 'secondary', label: t('wb_compare'), onClick: () => void comparePrevious()});
  const more = button({variant: 'ghost', label: t('wb_history_more'), onClick: () => void loadMore()});
  const print = button({'data-report-print': '', variant: 'ghost', label: t('wb_print'), onClick: () => window.print()});
  const primary = discussLink({workspaceId, from:'overview'}, {'data-charter-discuss':''});
  const box = h('section', {class: 'pm-charter', 'data-pm-charter': ''},
    h('header', {class: 'pm-heading'}, h('h2', {}, t('wb_charter')),
      primary),
    h('div', {class: 'pm-workbench-toolbar'}, previous, picker, next, compare, print, status), report, comparison,
    h('div', {class: 'pm-charter-save'}, create.box, more));
  const exports = caps()?.features?.pm_workspaces?.brief_export ? briefExport({h, t, api, alive, workspaceId, caps,
    snapshot: () => snapshot, readable: canRead, onUnavailable: error => {withdraw(); status.replaceChildren(errorBox(error)); onChange(null, 'error', error);}, errorBox}) : null;
  if (exports) box.append(exports.element);
  const stamp = value => timeStamp(value, {empty:t('wb_unknown')});
  const check = data => {if (data?.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch')); return data;};
  function checkedSnapshot(data, id) {
    const value = check(data).snapshot;
    if (id && !value) throw Error(t('pm_scope_mismatch'));
    if (value && (value.workspace_id !== workspaceId || !/^pmsnap_[0-9a-f]{32}$/.test(value.snapshot_id)
      || !Number.isSafeInteger(value.sequence) || id && value.snapshot_id !== id)) throw Error(t('pm_scope_mismatch'));
    return value;
  }
  function controls() {
    const index = history.findIndex(row => row.snapshot_id === selectedId);
    const prior = history[index - 1];
    if (prior) compare.setAttribute('aria-description', t('wb_compare_previous', {report: t('wb_snapshot', {number: prior.sequence})}));
    else compare.removeAttribute('aria-description');
    previous.disabled = busy || index <= 0; next.disabled = busy || index < 0 || index >= history.length - 1;
    compare.disabled = previous.disabled; picker.disabled = busy || !history.length; print.disabled = busy || !snapshot;
    more.hidden = cursor === null; more.disabled = busy; create.update(); exports?.update();
    onControls({previous: previous.disabled, next: next.disabled, compare: compare.disabled, historyPartial: cursor !== null});
  }
  function historyOptions() {
    picker.replaceChildren(...history.map(row => h('option', {value: row.snapshot_id}, `${t('wb_snapshot', {number: row.sequence})} · ${timeText(row.as_of)}`)));
    if (snapshot && !history.some(row => row.snapshot_id === selectedId)) picker.append(h('option', {value: selectedId}, t('wb_snapshot', {number: snapshot.sequence})));
    picker.value = selectedId || ''; controls();
    const discuss = primary;
    if (discuss) discuss.href = discussHref({workspaceId, from:'overview', report:snapshot?.snapshot_id, refs:snapshot ? [{kind:'snapshot', id:snapshot.snapshot_id}] : []});
  }
  function renderItems(rows, empty = 'wb_unknown') {
    if (!rows.length) return emptyState({title:t(empty)});
    const sources = disclosure({label: t('wb_sources'), body: h('pre', {class: 'pm-brief-proof'}, JSON.stringify(rows, null, 2))}); sources.classList.add('pm-charter-source');
    return h('div', {}, h('ul', {class: 'pm-charter-items'}, ...rows.map(row => h('li', {}, h('span', {}, labelSegment(row.text)),
      ['agent_assumption', 'unresolved', 'conflicting', 'repo_evidence'].includes(row.status)
        ? h('span', {class: 'chip'}, t('pm_status_' + row.status)) : ''))),
      sources);
  }
  function render() {
    onChange(snapshot, 'updated');
    report.replaceChildren(); comparison.replaceChildren();
    if (!snapshot) {
      status.replaceChildren(emptyState({title: t('wb_no_reports')}));
      report.append(h('p', {}, t('wb_no_snapshot'))); historyOptions(); return;
    }
    const document = snapshot.baseline?.document, groups = charterSections(snapshot);
    report.append(h('div', {class: 'pm-charter-meta'}, h('strong', {}, snapshot.title),
      h('span', {}, t('wb_snapshot', {number: snapshot.sequence})),
      h('span', {class: 'chip'}, t(snapshot.snapshot_id === latestId ? 'wb_saved' : 'wb_historical')),
      h('span', {}, timeLabel(t, 'pm_brief_as_of', {time: stamp(snapshot.as_of)}))),
    h('p', {class: 'pm-charter-plan-ref'}, t('wb_plan'), ': ', document?.title || t('wb_no_plan')));
    const grid = h('div', {class: 'pm-charter-grid'});
    for (const key of sections) grid.append(h('section', {'data-charter-section': key}, h('h3', {}, t('wb_' + key)),
      renderItems(groups[key], key === 'decisions' ? 'wb_no_decisions' : 'wb_unknown'),
      key === 'resources' ? h('p', {class: 'muted'}, t('wb_quota')) : ''));
    report.append(grid);
    if (document) report.append(h('details', {'data-charter-plan': ''}, h('summary', {}, t('wb_full_plan')),
      planDocument(h, document.markdown), h('ul', {}, ...(document.requirements || []).map(row => h('li', {}, row.text,
        h('ul', {}, ...(row.acceptance || []).map(item => h('li', {}, item.text)))))),
      h('code', {class: 'pm-source'}, snapshot.baseline.document_revision)));
    report.append(h('details', {class: 'pm-charter-trace'}, h('summary', {}, t('wb_sources')),
      h('pre', {class: 'pm-brief-proof'}, JSON.stringify({snapshot_id: snapshot.snapshot_id, as_of: snapshot.as_of,
        plan_revision: snapshot.baseline?.document_revision, adoption: snapshot.baseline?.adoption,
        candidate: snapshot.candidate_document, coverage: snapshot.coverage, visibility: snapshot.visibility,
        snapshot_digest: snapshot.snapshot_digest, view_digest: snapshot.view_digest}, null, 2))));
    if (snapshot.visibility?.restricted || snapshot.visibility?.filtered || Object.entries(snapshot.coverage || {}).some(([key, value]) => key.endsWith('_truncated') && value))
      status.append(h('p', {class: 'muted'}, t('wb_partial')));
    if (cursor !== null) status.append(h('p', {class: 'muted'}, t('wb_partial_history')));
    historyOptions();
  }
  function withdraw() {serial++; head = null; snapshot = null; history = []; cursor = null; busy = false;
    report.replaceChildren(); comparison.replaceChildren(); picker.replaceChildren(); status.replaceChildren(); controls(); onChange(null, 'withdrawn');}
  function failed(error, ticket) {try {alive(); if (ticket !== serial) return;} catch {return;}
    withdraw(); status.replaceChildren(errorBox(error)); onChange(null, 'error', error);}
  function acceptHistory(data, after, append = false) {
    check(data);
    if (!Array.isArray(data.snapshots) || data.next_cursor != null && (!Number.isSafeInteger(data.next_cursor) || data.next_cursor <= after)) throw Error(t('pm_scope_mismatch'));
    let last = after;
    for (const row of data.snapshots) {
      if (!/^pmsnap_[0-9a-f]{32}$/.test(row.snapshot_id) || !Number.isSafeInteger(row.sequence) || row.sequence <= last) throw Error(t('pm_scope_mismatch'));
      last = row.sequence;
    }
    if (data.next_cursor != null && data.next_cursor < last) throw Error(t('pm_scope_mismatch'));
    history = append ? [...history, ...data.snapshots] : data.snapshots; cursor = data.next_cursor ?? null;
  }
  async function readHistory(ticket) {
    const pages = []; let after = 0;
    for (let i = 0; i < historyPages; i++) {
      const page = await api('GET', `${endpoint}/snapshots?${after ? `after=${after}&` : ''}limit=100`);
      alive(); if (ticket !== serial) return null;
      check(page);
      if (page.next_cursor != null && (!Number.isSafeInteger(page.next_cursor) || page.next_cursor <= after)) throw Error(t('pm_scope_mismatch'));
      pages.push({page, after}); if (page.next_cursor == null) break; after = page.next_cursor;
    }
    return pages;
  }
  async function refresh(id = null) {
    onChange(null, 'loading');
    if (id) onChange(null, 'selected');
    const chosen = id || selectedId, ticket = ++serial; busy = true; head = null; controls();
    const previous = JSON.stringify([snapshot, latestId, cursor]); comparison.replaceChildren();
    try {
      const [data, pages, current] = await Promise.all([api('GET', `${endpoint}/snapshots/${chosen || 'latest'}`),
        readHistory(ticket), api('GET', `${endpoint}/plans/head`)]);
      alive(); if (ticket !== serial) return;
      if (current.head?.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
      snapshot = checkedSnapshot(data, chosen); head = current.head;
      pages.forEach(({page, after}, index) => acceptHistory(page, after, index > 0));
      selectedId = snapshot?.snapshot_id || null;
      preference.snapshotId = selectedId;
      if (!chosen) latestId = selectedId;
      if (cursor === null && history.length) latestId = history.at(-1).snapshot_id;
      if (!report.childElementCount || previous !== JSON.stringify([snapshot, latestId, cursor])) {status.replaceChildren(); render();}
      else {historyOptions(); onChange(snapshot, 'updated');}
    } catch (error) {failed(error, ticket);} finally {if (ticket === serial) {busy = false; controls();}}
  }
  async function select(id) {
    if (busy || !history.some(row => row.snapshot_id === id)) return;
    onChange(null, 'selected');
    const ticket = ++serial; busy = true; selectedId = id; snapshot = null; report.replaceChildren(); comparison.replaceChildren(); controls();
    try {const data = await api('GET', `${endpoint}/snapshots/${id}`); alive(); if (ticket !== serial) return;
      snapshot = checkedSnapshot(data, id); preference.snapshotId = id; status.replaceChildren(); render();
    } catch (error) {failed(error, ticket);} finally {if (ticket === serial) {busy = false; controls();}}
  }
  function move(delta) {const index = history.findIndex(row => row.snapshot_id === selectedId); if (index >= 0 && history[index + delta]) void select(history[index + delta].snapshot_id);}
  async function loadMore() {
    if (busy || cursor === null) return;
    const ticket = ++serial, after = cursor; busy = true; controls();
    try {const page = await api('GET', `${endpoint}/snapshots?after=${after}&limit=100`); alive(); if (ticket !== serial) return;
      acceptHistory(page, after, true); historyPages++; if (cursor === null && history.length) latestId = history.at(-1).snapshot_id; historyOptions();
    } catch (error) {failed(error, ticket);} finally {if (ticket === serial) {busy = false; controls();}}
  }
  async function comparePrevious() {
    const index = history.findIndex(row => row.snapshot_id === selectedId);
    if (busy || index <= 0) return;
    const id = selectedId, prior = history[index - 1].snapshot_id, ticket = ++serial; busy = true; controls(); comparison.replaceChildren();
    try {
      // Reauthorize both reports; never compare against a cached private copy.
      const [a, b] = await Promise.all([api('GET', `${endpoint}/snapshots/${prior}`), api('GET', `${endpoint}/snapshots/${id}`)]);
      alive(); if (ticket !== serial) return;
      const before = checkedSnapshot(a, prior); snapshot = checkedSnapshot(b, id); status.replaceChildren(); render();
      const left = charterSections(before), right = charterSections(snapshot);
      const differences = sections.map(key => ({key, ...statementChanges(left[key], right[key])}));
      const changes = differences.filter(row => row.pairs.length);
      const statement = row => h('p', {}, labelSegment(row.text),
        ['agent_assumption', 'unresolved', 'conflicting', 'repo_evidence'].includes(row.status)
          ? h('span', {class: 'chip'}, t('pm_status_' + row.status)) : null);
      const side = (row, label, report, change) => h('div', {'data-report-side': label}, h('strong', {}, t('wb_' + label)),
        h('span', {class: 'pm-report-identity'}, t('wb_snapshot', {number: report.sequence}), ' · ', report.title),
        change ? h('span', {'data-report-change': change}, t('wb_' + change)) : null, statement(row));
      comparison.append(h('h3', {}, t('wb_comparison')), changes.length ? h('div', {class: 'pm-charter-comparison'},
        ...changes.map(({key, pairs, unchanged}) => h('section', {'data-report-section': key}, h('h4', {}, t('wb_' + key)),
          ...pairs.map(pair => h('div', {class: 'pm-report-pair', 'data-report-pair': ''},
            pair.before ? side(pair.before, 'before', before, pair.after ? null : 'removed') : null,
            pair.after ? side(pair.after, 'after', snapshot, pair.before ? null : 'added') : null)),
          unchanged.length ? disclosure({element: h('details', {'data-report-unchanged': ''},
            h('summary', {}, t('wb_unchanged_count', {n: unchanged.length})),
            h('ul', {}, ...unchanged.map(row => h('li', {}, statement(row)))))}) : null))) : h('p', {}, t('wb_unchanged')));
      if (changes.length) comparison.append(disclosure({label: t('wv_details'), body: h('pre', {class: 'mono'},
        JSON.stringify({before: {snapshot_id: before.snapshot_id, snapshot_digest: before.snapshot_digest, view_digest: before.view_digest, sections: left},
          after: {snapshot_id: snapshot.snapshot_id, snapshot_digest: snapshot.snapshot_digest, view_digest: snapshot.view_digest, sections: right}}, null, 2))}));
    } catch (error) {failed(error, ticket);} finally {if (ticket === serial) {busy = false; controls();}}
  }
  controls(); return {box, primary, refresh, withdraw, select, comparison, previous, next, compare, picker, print,
    current: () => snapshot, controls: () => ({busy, previous: previous.disabled, next: next.disabled, compare: compare.disabled, historyPartial: cursor !== null}),
    comparePrevious, history: () => history};
}
