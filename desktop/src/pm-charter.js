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

export function workspaceCharter({h, t, api, alive, namespace, workspaceId, permitted, form, input, labelled, errorBox, onDiscuss, caps, canRead, onChange = () => {}, onControls = () => {}}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  const preference = viewState(namespace, workspaceId, 'overview');
  let serial = 0, head = null, snapshot = null, selectedId = preference.snapshotId || null, latestId = null, history = [], cursor = null, busy = false;
  const status = h('div', {role: 'status'}), report = h('div', {class: 'pm-charter-report'}), comparison = h('div');
  const title = input('pm_brief_snapshot_title', {max: 160, required: false});
  const create = form({action: 'pm.snapshot.create', target: {workspace_id: workspaceId}, fields: {title},
    params: values => values.title.trim() ? {title: values.title.trim()} : {},
    preconditions: () => ({expected_plan_version: head.version}), permitted: () => Boolean(head) && !busy && permitted(false),
    recoverPermitted: () => permitted(false), label: 'pm_brief_create', onSaved: result => refresh(result.snapshot_id),
    extra: h('details', {}, h('summary', {}, t('pm_brief_name_optional')), labelled('pm_brief_snapshot_title', title))});
  const previous = h('button', {type: 'button', class: 'secondary', 'aria-label': t('wb_previous'), onclick: () => move(-1)}, '←');
  const next = h('button', {type: 'button', class: 'secondary', 'aria-label': t('wb_next'), onclick: () => move(1)}, '→');
  const picker = h('select', {'aria-label': t('wb_history'), onchange: () => void select(picker.value)});
  const compare = h('button', {'data-report-compare': '', type: 'button', class: 'secondary', onclick: () => void comparePrevious()}, t('wb_compare'));
  const more = h('button', {type: 'button', class: 'secondary', onclick: () => void loadMore()}, t('wb_history_more'));
  const print = h('button', {'data-report-print': '', type: 'button', class: 'secondary', onclick: () => window.print()}, t('wb_print'));
  const box = h('section', {class: 'pm-charter', 'data-pm-charter': ''},
    h('header', {class: 'pm-heading'}, h('h2', {}, t('wb_charter')),
      h('button', {type: 'button', class: 'primary', onclick: () => onDiscuss(snapshot)}, t('wb_discuss'))),
    h('div', {class: 'pm-workbench-toolbar'}, previous, picker, next, compare, print), status, report, comparison,
    h('div', {class: 'pm-charter-save'}, create.box, more));
  const exports = caps()?.features?.pm_workspaces?.brief_export ? briefExport({h, t, api, alive, workspaceId, caps,
    snapshot: () => snapshot, readable: canRead, onUnavailable: error => {withdraw(); status.replaceChildren(errorBox(error)); onChange(null, 'error', error);}, errorBox}) : null;
  if (exports) box.append(exports.element);
  const stamp = value => Number.isFinite(value) ? new Date(value * 1000).toLocaleString() : t('wb_unknown');
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
    previous.disabled = busy || index <= 0; next.disabled = busy || index < 0 || index >= history.length - 1;
    compare.disabled = previous.disabled; picker.disabled = busy || !history.length; print.disabled = busy || !snapshot;
    more.hidden = cursor === null; more.disabled = busy; create.update(); exports?.update();
    onControls({previous: previous.disabled, next: next.disabled, compare: compare.disabled});
  }
  function historyOptions() {
    picker.replaceChildren(...history.map(row => h('option', {value: row.snapshot_id}, `${t('wb_snapshot', {number: row.sequence})} · ${stamp(row.as_of)}`)));
    if (snapshot && !history.some(row => row.snapshot_id === selectedId)) picker.append(h('option', {value: selectedId}, t('wb_snapshot', {number: snapshot.sequence})));
    picker.value = selectedId || ''; controls();
  }
  function renderItems(rows, empty = 'wb_unknown') {
    if (!rows.length) return h('p', {class: 'muted'}, t(empty));
    return h('div', {}, h('ul', {class: 'pm-charter-items'}, ...rows.map(row => h('li', {}, h('span', {}, labelSegment(row.text)),
      ['agent_assumption', 'unresolved', 'conflicting', 'repo_evidence'].includes(row.status)
        ? h('span', {class: 'chip'}, t('pm_status_' + row.status)) : ''))),
      h('details', {class: 'pm-charter-source'}, h('summary', {}, t('wb_sources')),
        h('pre', {class: 'pm-brief-proof'}, JSON.stringify(rows, null, 2))));
  }
  function render() {
    onChange(snapshot, 'updated');
    report.replaceChildren(); comparison.replaceChildren();
    if (!snapshot) {report.append(h('p', {}, t('wb_no_snapshot'))); historyOptions(); return;}
    const document = snapshot.baseline?.document, groups = charterSections(snapshot);
    report.append(h('div', {class: 'pm-charter-meta'}, h('strong', {}, snapshot.title),
      h('span', {}, t('wb_snapshot', {number: snapshot.sequence})),
      h('span', {class: 'chip'}, t(snapshot.snapshot_id === latestId ? 'wb_saved' : 'wb_historical')),
      h('span', {}, t('pm_brief_as_of', {time: stamp(snapshot.as_of)}))),
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
  async function refresh(id = null) {
    if (id) onChange(null, 'selected');
    const chosen = id || selectedId, ticket = ++serial; busy = true; head = null; controls();
    const previous = JSON.stringify([snapshot, latestId, cursor]); comparison.replaceChildren();
    try {
      const [data, page, current] = await Promise.all([api('GET', `${endpoint}/snapshots/${chosen || 'latest'}`),
        api('GET', `${endpoint}/snapshots?limit=100`), api('GET', `${endpoint}/plans/head`)]);
      alive(); if (ticket !== serial) return;
      if (current.head?.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
      snapshot = checkedSnapshot(data, chosen); head = current.head; acceptHistory(page, 0);
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
      acceptHistory(page, after, true); if (cursor === null && history.length) latestId = history.at(-1).snapshot_id; historyOptions();
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
      const changes = sections.filter(key => JSON.stringify(left[key]) !== JSON.stringify(right[key]));
      comparison.append(h('h3', {}, t('wb_comparison')), changes.length ? h('div', {class: 'pm-charter-comparison'},
        ...changes.map(key => h('section', {}, h('h4', {}, t('wb_' + key)),
          h('div', {class: 'pm-charter-grid'}, h('div', {}, h('strong', {}, t('wb_before')), renderItems(left[key])),
            h('div', {}, h('strong', {}, t('wb_after')), renderItems(right[key])))))) : h('p', {}, t('wb_unchanged')));
    } catch (error) {failed(error, ticket);} finally {if (ticket === serial) {busy = false; controls();}}
  }
  controls(); return {box, refresh, withdraw, select, comparison, previous, next, compare, picker, print,
    current: () => snapshot, controls: () => ({busy, previous: previous.disabled, next: next.disabled}),
    comparePrevious, history: () => history};
}
