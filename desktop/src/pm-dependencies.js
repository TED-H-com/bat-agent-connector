import {timeStamp, timeLabel} from './ui/stamps.js';
import {viewState} from './ui/view-state.js';
import {searchField, discussLink, button, disclosure, emptyState, inspector, segmented, skeleton, statusPill} from './ui/components.js';
import {icon} from './ui/icons.js';
import {discussHref} from './pm-discuss-link.js';
import {boundGraph, layoutGraph, limitColumns} from './pm-dependencies-layout.js';
import {dependencyNodeNames, wrapDependencyName} from './pm-dependencies-labels.js';
import {reasonLabel, reasonLabels} from './ui/ui-strings.js';
import {place} from './ui/dom.js';
import {joinLabel} from './label-text.js';
// Captured inventory only. Counts, versions and owners come from the central projection.
const RELATIONS = ['runtime', 'build', 'test', 'contract', 'reference'];
const EVIDENCE = ['verified', 'declared', 'inferred'];
const KINDS = ['runtime_library', 'api_schema', 'build_test_toolchain', 'external_service'];
const LETTERS = {runtime: 'R', build: 'B', test: 'T', contract: 'C', reference: 'Ref'};
const COLUMN_LIMIT = 12, ROW_LIMIT = 12;
const MISS = new Set(['dependency_omitted', 'capture_budget', 'dependency_not_observed']);
const FILE_STATUS = {read: 'dep_file_read', missing: 'dep_file_missing', too_large: 'dep_file_too_large', unparsable: 'dep_file_unparsable'};

const SVG = 'http://www.w3.org/2000/svg';
const NODE_W = 148, NODE_H = 36, GAP_X = 132, GAP_Y = 18, PAD = 20;

const LOCAL_REASONS = new Set(['tree_limit', 'file_limit', 'component_limit', 'unsupported_tree_entry', 'selection_budget', 'unreadable_manifest']);
const knownReason = code => LOCAL_REASONS.has(code) || Object.hasOwn(reasonLabels, code);
const reasonText = (t, code, detail = false) => {
  const label = LOCAL_REASONS.has(code) ? t('dep_reason_' + code) : reasonLabel(t, code);
  return detail && code && !knownReason(code) ? t('ui_reason_detail', {label, code}) : label;
};
const shortSha = value => typeof value === 'string' && value ? value.slice(0, 7) : '';
const capturedDate = epoch => {
  if (typeof epoch !== 'number' || !Number.isFinite(epoch)) return '';
  const date = new Date(epoch * 1000);
  if (Number.isNaN(date.valueOf())) return '';
  return timeStamp(epoch);
};
const abbreviate = value => {const text = String(value || ''); return text.length > 16 ? text.slice(0, 15) + '…' : text;};
const svg = (tag, attrs = {}, ...children) => {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat()) if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
};

export function workspaceDependencies({h, t, api, alive, namespace, workspaceId, permitted, form, errorBox, main, header}) {
  const preference = viewState(namespace, workspaceId, 'dependencies');
  let active = false, serial = 0, impactTicket = 0, impactKey = '', data = null, revisions = null, impact = null;
  let mode = preference.mode === 'graph' ? 'graph' : 'matrix';
  let relationFilter = Array.isArray(preference.relations) ? preference.relations.filter(value => RELATIONS.includes(value)) : [...RELATIONS];
  let evidenceFilter = Array.isArray(preference.evidence) ? preference.evidence.filter(value => EVIDENCE.includes(value)) : [...EVIDENCE];
  let showAll = preference.showAll === true;
  let showAllRows = preference.showAllRows === true;
  let selection = preference.selection && typeof preference.selection === 'object' ? preference.selection : null;
  let focusId = typeof preference.focus === 'string' ? preference.focus : '';
  let zoom = Number.isFinite(preference.zoom) ? Math.min(1.8, Math.max(0.6, preference.zoom)) : 1;
  let panel = null, graphRows = [];
  const levelText = level => EVIDENCE.includes(level) ? t('dep_' + level) : reasonText(t, level);
  const remember = () => Object.assign(preference, {mode, relations: [...relationFilter], evidence: [...evidenceFilter], query: search.value, showAll, showAllRows, selection, focus: focusId, zoom});
  const ready = () => Boolean(data?.source?.repository && data.source.commit_sha && data.source.binding_digest && Number.isInteger(data.version) && Number.isInteger(data.workspace_version));
  const preconditions = () => ({expected_version: data.version, expected_workspace_version: data.workspace_version, expected_binding_digest: data.source.binding_digest});
  let nodeName = () => '';
  const dependencyName = key => nodeName('d:' + key);
  const componentName = id => nodeName('c:' + id);

  function dependencyByKey() {
    const map = new Map();
    for (const kind of KINDS) for (const row of data?.dependencies?.[kind] || []) if (row?.key && !map.has(row.key)) map.set(row.key, row);
    for (const row of data?.declared || []) {
      const dep = row.dependency;
      if (dep?.key && !map.has(dep.key)) map.set(dep.key, {key: dep.key, name: dep.name, ecosystem: dep.ecosystem, kinds: dep.kind ? [dep.kind] : []});
    }
    for (const edge of data?.edges || []) if (edge?.dependency_key && !map.has(edge.dependency_key)) map.set(edge.dependency_key, {key: edge.dependency_key, name: edge.dependency_key, kinds: edge.kind ? [edge.kind] : []});
    return map;
  }
  function allEdges(source = data) {
    const seen = new Set(), rows = [];
    const add = edge => {
      if (!edge?.component_id || !edge?.dependency_key || !RELATIONS.includes(edge.relation)) return;
      const id = edge.declaration_id || edge.edge_id || [edge.component_id, edge.dependency_key, edge.relation, edge.evidence_level].join('|');
      if (seen.has(id)) return;
      seen.add(id);
      rows.push(edge);
    };
    for (const edge of source?.edges || []) add(edge);
    for (const row of source?.declared || []) add({...row, edge_id: row.declaration_id, dependency_key: row.dependency_key, kind: row.dependency?.kind, evidence_refs: row.evidence_refs || (row.source_ref ? [row.source_ref] : [])});
    return rows;
  }
  function matchesFilters(edge) {
    const query = search.value.trim().toLocaleLowerCase();
    return relationFilter.includes(edge.relation) && evidenceFilter.includes(edge.evidence_level)
      && (!query || [componentName(edge.component_id), dependencyName(edge.dependency_key), componentName(edge.target_component_id)]
        .some(name => name.toLocaleLowerCase().includes(query)));
  }
  function filteredEdges() {return allEdges().filter(matchesFilters);}
  function filteredCrossEdges() {return (data?.cross_repository_edges || []).filter(matchesFilters);}
  const externalById = id => (data?.external_components || []).find(row => row?.component_id === id) || null;
  const otherRepositoryLabel = row => {const name = typeof row?.repository === 'string' ? row.repository.trim() : ''; return name || t('dep_other_repository');};
  function peerCaption(dependencyKey, componentId = '') {
    const labels = [], seen = new Set();
    for (const link of filteredCrossEdges()) {
      if (link?.dependency_key !== dependencyKey) continue;
      if (componentId && link.component_id !== componentId && link.target_component_id !== componentId) continue;
      const row = externalById(link.target_component_id) || externalById(link.component_id);
      if (!row || seen.has(row.component_id)) continue;
      seen.add(row.component_id);
      labels.push(joinLabel([componentName(row.component_id), otherRepositoryLabel(row)]));
    }
    return labels.join(' · ');
  }
  const selectedKey = () => selection?.type === 'dependency' || selection?.type === 'edge' ? selection.dependencyKey : '';
  const selectionEdges = () => allEdges().filter(edge => selection?.type === 'component' ? edge.component_id === selection.componentId
    : selection?.type === 'edge' ? edge.component_id === selection.componentId && edge.dependency_key === selection.dependencyKey
    : edge.dependency_key === selection?.dependencyKey);
  function selectionAlive() {
    if (!selection || !data) return false;
    if (selection.type === 'dependency') return dependencyByKey().has(selection.dependencyKey);
    if (selection.type === 'component') return (data.components || []).some(row => row.component_id === selection.componentId) || (data.external_components || []).some(row => row.component_id === selection.componentId);
    return allEdges().some(edge => edge.component_id === selection.componentId && edge.dependency_key === selection.dependencyKey);
  }
  const narrow = () => matchMedia('(max-width: 800px)').matches;
  const focusKeyFor = value => value?.type === 'edge' ? mode === 'graph' ? `col:${value.dependencyKey}`
    : `${narrow() ? 'pick' : 'cell'}:${value.componentId}|${value.dependencyKey}`
    : value?.type === 'dependency' ? `col:${value.dependencyKey}` : value?.type === 'component' ? `row:${value.componentId}` : '';
  const activeFocusHost = () => mode === 'graph' ? graphHost : narrow() ? listHost : matrixScroll;
  const focusTarget = key => key ? activeFocusHost().querySelector(`[data-dep-focus="${CSS.escape(key)}"]`) : null;
  const focusSelection = () => {
    focusTarget(focusKeyFor(selection))?.focus({preventScroll: true});
  };

  const search = h('input', {type: 'search', 'aria-label': t('dep_search'), placeholder: t('dep_search')});
  search.value = typeof preference.query === 'string' ? preference.query : '';
  search.addEventListener('input', () => {remember(); render();});
  const searchWrap = searchField(search, {class:'dep-search'});
  const relationChips = RELATIONS.map(value => makeChip('relation', value));
  const evidenceChips = EVIDENCE.map(value => makeChip('evidence', value));
  function makeChip(kind, value) {
    const label = h('span');
    const node = button({variant: 'ghost', size: 'sm', label, ['data-dep-' + kind]: value, 'aria-pressed': 'true'});
    node.classList.add('dep-chip');
    node.addEventListener('click', () => {
      const current = kind === 'relation' ? relationFilter : evidenceFilter;
      const next = current.includes(value) ? current.filter(item => item !== value) : [...current, value];
      if (kind === 'relation') relationFilter = next; else evidenceFilter = next;
      remember(); render();
    });
    return {node, label, value};
  }
  const showAllButton = button({label: t('dep_all_columns', {n: 0}), 'data-dep-show-all': ''});
  showAllButton.classList.add('dep-show-all');
  showAllButton.addEventListener('click', () => {showAll = !showAll; remember(); render();});
  const showRowsButton = button({label: t('dep_show_all_rows', {n: 0}), 'data-dep-show-rows': ''});
  showRowsButton.classList.add('dep-show-rows');
  showRowsButton.addEventListener('click', () => {showAllRows = !showAllRows; remember(); render();});
  const modeSwitch = segmented({label: t('ui_dependencies'), value: mode, options: [{value: 'matrix', label: t('dep_matrix')}, {value: 'graph', label: t('dep_graph')}], onChange: value => {mode = value; remember(); render();}});
  const filterGroup = (name, chips) => h('div', {class: 'dep-filter'}, h('span', {class: 'dep-filter-label'}, t(name)), h('div', {class: 'dep-chips'}, ...chips.map(chip => chip.node)));
  const toolbar = h('div', {class: 'dep-toolbar'}, modeSwitch, h('div', {class: 'dep-filters'}, filterGroup('dep_filter_relation', relationChips), filterGroup('dep_filter_evidence', evidenceChips)), searchWrap, showAllButton, showRowsButton);
  const coverage = h('p', {class: 'dep-note', 'data-dep-coverage': '', hidden: true});
  const statusBox = h('div', {role: 'status'});
  const matrixScroll = h('div', {class: 'dep-matrix-scroll', 'data-dep-matrix': '', 'aria-label': t('dep_matrix')});
  const listHost = h('div', {class: 'dep-list', 'data-dep-list': ''});
  const legend = h('div', {class: 'dep-legend', 'data-dep-legend': '', hidden: true});
  const matrixHost = h('div', {'data-dep-matrix-host': ''}, matrixScroll, listHost);
  const graphScroll = h('div', {class: 'dep-graph-scroll', 'data-dep-graph': ''});
  const graphHint = h('p', {class: 'ui-meta', 'data-dep-scroll-hint': ''}, t('dep_scroll_hint'));
  const graphName = h('p', {class: 'ui-meta', 'data-dep-node-name': '', hidden: true});
  graphScroll.addEventListener('focusin', event => {
    const node = event.target.closest?.('[data-dep-node]');
    if (!node) return;
    graphName.textContent = node.getAttribute('aria-label'); graphName.hidden = false;
    node.scrollIntoView({block: 'nearest', inline: 'nearest'});
  });
  const zoomOut = button({size: 'sm', label: '−', 'aria-label': t('dep_zoom_out'), title: t('dep_zoom_out'), onClick: () => setZoom(zoom - 0.2)});
  const zoomIn = button({size: 'sm', label: '+', 'aria-label': t('dep_zoom_in'), title: t('dep_zoom_in'), onClick: () => setZoom(zoom + 0.2)});
  const loaded = h('span', {class: 'dep-loaded', 'data-dep-loaded': ''}, `${Math.round(zoom * 100)}%`);
  const graphBar = h('div', {class: 'dep-graph-bar'}, h('div', {class: 'dep-graph-controls'}, loaded, zoomOut, zoomIn));
  const graphHost = h('div', {'data-dep-graph-host': ''}, graphBar, graphHint, graphName, graphScroll);
  const detailBody = h('div');
  const details = disclosure({label: t('dep_details'), body: detailBody, key: JSON.stringify([namespace, workspaceId, 'dep-files'])});
  details.dataset.depDetails = '';
  matrixScroll.addEventListener('scroll', () => {preference.matrixLeft = matrixScroll.scrollLeft; preference.matrixTop = matrixScroll.scrollTop;});
  graphScroll.addEventListener('scroll', () => {preference.graphLeft = graphScroll.scrollLeft; preference.graphTop = graphScroll.scrollTop;});
  matrixScroll.addEventListener('keydown', event => moveGrid(event, matrixScroll));
  listHost.addEventListener('keydown', event => {
    const current = event.target.closest?.('[data-dep-pick]');
    if (!current || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const items = [...listHost.querySelectorAll('[data-dep-pick]')];
    const index = items.indexOf(current);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : index + (event.key === 'ArrowUp' ? -1 : 1);
    if (!items[next]) return;
    event.preventDefault();
    items.forEach((node, item) => {node.tabIndex = item === next ? 0 : -1;});
    items[next].focus();
  });
  let drag = null;
  graphScroll.addEventListener('pointerdown', event => {
    if (event.button !== 0 || event.target.closest('[data-dep-node], button, a, input, select, textarea')) return;
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY, left: graphScroll.scrollLeft, top: graphScroll.scrollTop};
  });
  graphScroll.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    graphScroll.scrollLeft = drag.left - (event.clientX - drag.x);
    graphScroll.scrollTop = drag.top - (event.clientY - drag.y);
  });
  const endDrag = event => {if (drag && event.pointerId === drag.id) drag = null;};
  graphScroll.addEventListener('pointerup', endDrag);
  graphScroll.addEventListener('pointerleave', endDrag);
  graphScroll.addEventListener('pointercancel', endDrag);
  graphScroll.addEventListener('keydown', event => {
    const node = event.target.closest?.('[data-dep-node]');
    if (!node) return;
    if (['Enter', ' '].includes(event.key)) {event.preventDefault(); const next = selectionFromNode(node); if (next) choose(next, true); return;}
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const nodes = [...graphScroll.querySelectorAll('[data-dep-node]')];
    if (event.key === 'Home' || event.key === 'End') {focusGraph(event.key === 'Home' ? nodes[0] : nodes.at(-1)); return;}
    const box = node.getBoundingClientRect();
    const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
    let best = null, score = Infinity;
    for (const other of nodes) {
      if (other === node) continue;
      const bounds = other.getBoundingClientRect();
      const dx = bounds.x + bounds.width / 2 - cx, dy = bounds.y + bounds.height / 2 - cy;
      if (event.key === 'ArrowRight' && dx <= 8 || event.key === 'ArrowLeft' && dx >= -8 || event.key === 'ArrowDown' && dy <= 8 || event.key === 'ArrowUp' && dy >= -8) continue;
      const primary = event.key === 'ArrowLeft' || event.key === 'ArrowRight' ? Math.abs(dx) : Math.abs(dy);
      const secondary = event.key === 'ArrowLeft' || event.key === 'ArrowRight' ? Math.abs(dy) : Math.abs(dx);
      const rank = primary + secondary * 4;
      if (rank < score) {score = rank; best = other;}
    }
    if (best) focusGraph(best);
  });
  graphScroll.addEventListener('click', event => {
    const node = event.target.closest?.('[data-dep-node]');
    const next = node && selectionFromNode(node);
    if (next) choose(next, true);
  });

  const captureHint = h('p', {id: 'dep-capture-reason', class: 'dep-capture-hint', 'data-dep-capture-reason': '', hidden: true});
  let captureButton = null;
  function captureHintText() {
    if (!active || !data || !captureButton?.disabled) return '';
    if (captureForm.locked() && permitted(false) && ready()) return '';
    if (!permitted(false)) return t('dep_capture_manage');
    if (!data.source?.repository || !data.source.commit_sha) return t('dep_capture_no_source');
    if (!ready()) return t('dep_capture_not_ready');
    return t('dep_capture_manage');
  }
  function syncCapture() {
    if (!captureButton) return;
    captureButton.hidden = !active || captureForm.replaceable();
    const reason = captureHintText();
    captureButton.className = captureButton.disabled ? 'ui-button secondary ui-md' : 'ui-button primary ui-md';
    captureHint.textContent = reason;
    captureHint.hidden = !active || !reason;
    if (reason) {
      captureButton.setAttribute('aria-describedby', captureHint.id);
      captureButton.title = reason;
    } else {
      captureButton.removeAttribute('aria-describedby');
      captureButton.removeAttribute('title');
    }
  }
  const captureForm = form({action: 'pm.dependencies.capture', target: {workspace_id: workspaceId}, fields: {}, label: 'dep_capture',
    params: () => ({repository: data.source.repository, commit_sha: data.source.commit_sha}), preconditions, permitted: () => permitted(false) && ready(), onSaved: () => refresh(), onStateChange: () => syncCapture()});
  captureForm.box.id = 'dep-capture-form';
  captureButton = captureForm.box.querySelector('button[type=submit]');
  captureButton.setAttribute('data-dep-capture', '');
  captureButton.setAttribute('form', captureForm.box.id);
  captureButton.hidden = true;
  syncCapture();
  const componentSelect = h('select', {'aria-label': t('dep_component')});
  const nameInput = h('input', {'aria-label': t('dep_name'), maxlength: 200, required: true});
  const ecosystemInput = h('input', {'aria-label': t('dep_ecosystem'), maxlength: 80, required: true});
  const kindSelect = h('select', {'aria-label': t('dep_kind')}, ...KINDS.map(value => h('option', {value}, t('dep_kind_' + value))));
  const versionInput = h('input', {'aria-label': t('dep_version'), maxlength: 80});
  const relationSelect = h('select', {'aria-label': t('dep_relation')});
  const evidenceSelect = h('select', {'aria-label': t('dep_level')}, ...['declared', 'inferred'].map(value => h('option', {value}, t('dep_' + value))));
  const pathInput = h('input', {'aria-label': t('dep_source_path'), maxlength: 500});
  const urlInput = h('input', {'aria-label': t('dep_source_url'), maxlength: 500});
  const noteInput = h('textarea', {'aria-label': t('dep_note'), maxlength: 1000});
  const sourceCommit = h('span', {class: 'dep-note', 'data-dep-source-commit': ''});
  const labelled = (key, node, note = '') => h('label', {}, t(key), node, note);
  function relationOptions() {
    const allowed = evidenceSelect.value === 'declared' ? ['contract', 'reference'] : RELATIONS;
    const current = relationSelect.value;
    relationSelect.replaceChildren(...allowed.map(value => h('option', {value}, t('dep_' + value))));
    relationSelect.value = allowed.includes(current) ? current : allowed[0];
  }
  evidenceSelect.addEventListener('change', relationOptions);
  relationOptions();
  const declareForm = form({action: 'pm.dependencies.declare', target: {workspace_id: workspaceId}, label: 'dep_declare',
    fields: {component: componentSelect, name: nameInput, ecosystem: ecosystemInput, kind: kindSelect, version: versionInput, evidence: evidenceSelect, relation: relationSelect, path: pathInput, url: urlInput, note: noteInput},
    permitted: () => permitted(true) && ready() && Boolean(data?.capture?.revision_id), preconditions, onSaved: () => refresh(),
    params: values => {
      const url = values.url.trim(), path = values.path.trim();
      if (!url && !path) throw Error(t('dep_source_required'));
      const note = values.note.trim();
      return {component_id: values.component, dependency: {name: values.name.trim(), ecosystem: values.ecosystem.trim(), kind: values.kind, version: values.version.trim() || null},
        relation: values.relation, evidence_level: values.evidence, source_ref: url || {path, commit_sha: data.source.commit_sha}, ...(note ? {note} : {})};
    },
    extra: h('div', {class: 'dep-fields dep-declare-grid'}, labelled('dep_component', componentSelect), labelled('dep_name', nameInput), labelled('dep_ecosystem', ecosystemInput),
      labelled('dep_kind', kindSelect), labelled('dep_version', versionInput), labelled('dep_level', evidenceSelect), labelled('dep_relation', relationSelect),
      labelled('dep_source_path', pathInput, sourceCommit), labelled('dep_source_url', urlInput), labelled('dep_note', noteInput))});
  relationOptions(); // Restored evidence determines the translated relation options.
  const retractSelect = h('select', {'aria-label': t('dep_declaration')});
  const retractForm = form({action: 'pm.dependencies.retract', target: {workspace_id: workspaceId}, label: 'dep_retract', fields: {declaration_id: retractSelect},
    permitted: () => permitted(true) && ready() && /^pmdecl_[0-9a-f]{32}$/.test(retractSelect.value), preconditions, onSaved: () => refresh(),
    params: values => ({declaration_id: values.declaration_id}), extra: h('div', {class: 'dep-fields'}, labelled('dep_declaration', retractSelect))});
  retractForm.box.querySelector('button[type=submit]').className = 'secondary';
  const record = disclosure({label: t('dep_record'), body: h('div', {}, declareForm.box, retractForm.box), key: JSON.stringify([namespace, workspaceId, 'dep-record'])});
  record.dataset.depRecord = '';
  const holder = h('div', {}, record);
  const element = h('section', {class: 'dep-view', 'data-dep-view': ''}, captureHint, toolbar, coverage, statusBox, captureForm.box, legend, matrixHost, graphHost, details, holder);

  function fillComponents() {
    nodeName = dependencyNodeNames([
      ...(data?.components || []).map(row => ({id: 'c:' + row.component_id, name: row.name || row.component_id})),
      ...(data?.external_components || []).map(row => ({id: 'c:' + row.component_id, name: row.name || row.component_id})),
      ...[...dependencyByKey()].map(([key, row]) => ({id: 'd:' + key, name: row.name || key})),
    ]);
    const value = componentSelect.value;
    componentSelect.replaceChildren(...(data?.components || []).map(row => h('option', {value: row.component_id}, componentName(row.component_id))));
    if ([...componentSelect.options].some(option => option.value === value)) componentSelect.value = value;
  }
  function fillRetract() {
    const value = retractSelect.value;
    const rows = data?.declared || [];
    retractSelect.replaceChildren(...(!data ? [] : rows.length ? rows.map(row => h('option', {value: row.declaration_id}, joinLabel([row.dependency?.name, RELATIONS.includes(row.relation) ? t('dep_' + row.relation) : '']))) : [h('option', {value: ''}, t('dep_none_recorded'))]));
    if ([...retractSelect.options].some(option => option.value === value)) retractSelect.value = value;
  }
  function moveGrid(event, root) {
    const current = event.target.closest?.('[data-row]');
    if (!current || !root.contains(current) || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const row = Number(current.getAttribute('data-row')), col = Number(current.getAttribute('data-col'));
    const cells = [...root.querySelectorAll('[data-row]')];
    const rows = cells.map(node => Number(node.getAttribute('data-row')));
    const cols = cells.map(node => Number(node.getAttribute('data-col')));
    let nextRow = row, nextCol = col;
    if (event.key === 'Home') nextCol = Math.min(...cols.filter((_, index) => rows[index] === row));
    else if (event.key === 'End') nextCol = Math.max(...cols.filter((_, index) => rows[index] === row));
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') nextCol = col + (event.key === 'ArrowLeft' ? -1 : 1);
    else nextRow = row + (event.key === 'ArrowUp' ? -1 : 1);
    const target = cells.find(node => Number(node.getAttribute('data-row')) === nextRow && Number(node.getAttribute('data-col')) === nextCol);
    if (!target) return;
    cells.forEach(node => {node.tabIndex = node === target ? 0 : -1;});
    target.focus();
  }
  function selectionFromNode(node) {
    const id = node.getAttribute('data-dep-node') || '';
    if (id.startsWith('d:')) return {type: 'dependency', dependencyKey: id.slice(2)};
    if (id.startsWith('c:')) return {type: 'component', componentId: id.slice(2)};
    return null;
  }
  function focusGraph(node) {
    if (!node) return;
    for (const item of graphScroll.querySelectorAll('[data-dep-node]')) item.tabIndex = item === node ? 0 : -1;
    node.focus();
  }
  function setZoom(next) {zoom = Math.min(1.8, Math.max(0.6, Math.round(next * 10) / 10)); loaded.textContent = `${Math.round(zoom * 100)}%`; remember(); drawGraph(filteredEdges());}
  function choose(next, open) {
    selection = next;
    if (next?.componentId) focusId = next.componentId;
    remember();
    render();
    focusSelection();
    if (open) openInspector();
  }
  function mark(edge) {
    const level = EVIDENCE.includes(edge.evidence_level) ? edge.evidence_level : 'declared';
    const version = versionLabel(edge);
    return h('span', {class: `dep-mark dep-rel-${edge.relation} evidence-${level}`, title: version || dependencyName(edge.dependency_key)}, LETTERS[edge.relation]);
  }
  function versionFieldLabel(edge) {
    const kind = edge?.kind || dependencyByKey().get(edge?.dependency_key)?.kinds?.[0] || '';
    if (edge?.relation === 'contract' || (edge?.relation !== 'reference' && kind === 'api_schema')) return t('dep_version_contract');
    if (edge?.relation === 'reference') return t('dep_version_reference');
    if (kind === 'external_service') return t('dep_version_service');
    return t('dep_range');
  }
  function versionLabel(edge) {
    const pinned = typeof edge?.resolved === 'string' ? edge.resolved : '';
    const range = edge?.version == null || edge.version === '' ? '' : String(edge.version);
    const bare = value => /^\d+(?:\.\d+)*$/.test(value);
    const rangeText = range ? (bare(range) ? `${versionFieldLabel(edge)} ${range}` : range) : '';
    const pinnedText = pinned && bare(pinned) && !range ? `${t('dep_pinned')} ${pinned}` : pinned;
    return joinLabel([pinnedText, rangeText]);
  }
  function requirementValue(edge) {
    return edge?.version == null || edge.version === '' ? '' : String(edge.version);
  }
  function edgeLabel(edge) {
    return joinLabel([dependencyName(edge.dependency_key), t('dep_' + edge.relation), versionLabel(edge), levelText(edge.evidence_level), peerCaption(edge.dependency_key, edge.component_id)]);
  }
  function paintLegend() {
    const swatch = (relation, level, text) => h('span', {class: `dep-mark dep-rel-${relation} evidence-${level}`, 'aria-hidden': 'true'}, text);
    const item = (mark, label) => h('span', {class: 'dep-legend-item'}, mark, label);
    const peers = filteredCrossEdges().length ? item(h('span', {class: 'dep-legend-external', 'data-dep-legend-external': '', 'aria-hidden': 'true'}), t('dep_other_repository')) : null;
    legend.replaceChildren(
      ...RELATIONS.map(relation => item(swatch(relation, 'verified', LETTERS[relation]), t('dep_' + relation))),
      h('span', {class: 'dep-legend-gap', 'aria-hidden': 'true'}),
      item(swatch('runtime', 'verified', '\u00a0'), t('dep_legend_verified')),
      item(swatch('runtime', 'declared', '\u00a0'), t('dep_legend_declared')),
      item(swatch('runtime', 'inferred', '\u00a0'), t('dep_legend_inferred')),
      ...(peers ? [peers] : []));
  }
  function syncChrome() {
    if (header?.meta) {header.meta.hidden = true; header.meta.textContent = '';}
    if (header?.pills) {header.pills.hidden = true; header.pills.replaceChildren();}
    syncCapture();
    if (!active || !data) return;
    const commit = shortSha(data.capture?.commit_sha);
    const date = capturedDate(data.capture?.captured_at);
    const line = commit && date ? timeLabel(t, 'dep_meta', {commit, date}) : commit ? t('dep_meta_commit', {commit}) : '';
    if (header?.meta) {header.meta.hidden = !line; header.meta.replaceChildren(line);}
    const marks = [];
    if (data.stale === true) marks.push(statusPill('stale'));
    if (data.capture?.revision_id && data.partial === true) marks.push(statusPill('partial'));
    if (header?.pills) {header.pills.replaceChildren(...marks); header.pills.hidden = !marks.length;}
  }
  function syncImpact() {
    const key = active ? selectedKey() : '';
    if (key === impactKey) return;
    impactKey = key; impact = null;
    const ticket = ++impactTicket;
    if (!key) return;
    void (async () => {
      try {
        const result = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/dependencies/impact?dependency=${encodeURIComponent(key)}`);
        alive(); if (ticket !== impactTicket) return;
        if (result?.workspace_id !== workspaceId || result?.dependency !== key) throw Error(t('pm_scope_mismatch'));
        impact = result; renderInspector();
      } catch (error) {
        try {alive(); if (ticket !== impactTicket) return;} catch {return;}
        if ([401, 403, 404].includes(error.status)) {withdraw(); statusBox.replaceChildren(readError(error, () => {void refresh();}, statusBox)); return;}
        impact = {dependency: key, error}; renderInspector();
      }
    })();
  }
  function hintRows() {
    const hints = [...(data?.verification_hints || []), ...(data?.components || []).flatMap(row => row.verification_hints || []), ...(impact?.components || []).flatMap(row => row.verification_hints || [])];
    const componentIds = new Set(), relations = new Set();
    for (const edge of selectionEdges()) {componentIds.add(edge.component_id); relations.add(edge.relation);}
    if (selection?.type === 'component') componentIds.add(selection.componentId);
    const seen = new Set(), rows = [];
    for (const hint of hints) {
      if (componentIds.size && hint?.component_id && !componentIds.has(hint.component_id)) continue;
      if (relations.size && hint?.relation && !relations.has(hint.relation)) continue;
      const label = hint?.job_id || hint?.command || hint?.path;
      const id = hint?.job_id || label;
      if (!label || seen.has(id)) continue;
      seen.add(id); rows.push(hint);
    }
    return rows;
  }
  function evidenceLine(ref) {
    if (!ref || typeof ref !== 'object') return '';
    if (typeof ref.url === 'string') return ref.url;
    const sha = shortSha(ref.commit_sha);
    const place = ref.path && sha ? `${ref.path} @ ${sha}` : ref.path || sha;
    return joinLabel([place, Number.isFinite(ref.line) ? t('dep_line_n', {n: ref.line}) : '']);
  }
  function ownerFact(row) {
    const owner = row?.maintenance_owner;
    const actor = owner && typeof owner === 'object' ? owner.actor : typeof owner === 'string' ? owner : '';
    return {actor: typeof actor === 'string' ? actor : '', reason: actor ? '' : row?.maintenance_owner_reason || ''};
  }
  function inspectorTitle() {
    if (selection?.type === 'component') return componentName(selection.componentId) || t('dep_component');
    return dependencyName(selectedKey()) || t('ui_dependencies');
  }
  function openInspector() {
    if (!selection || !data) return;
    if (!panel) {
      panel = inspector({title: inspectorTitle(), returnFocus: () => focusTarget(focusKeyFor(selection)), onClose: () => {panel = null;}});
      panel.dataset.depInspector = '';
      panel.querySelector('h2')?.classList.add('dep-inspector-title');
      (main || element).append(panel);
    }
    renderInspector();
  }
  function closeInspector() {panel?.close();}
  function partialImpact() {
    return impact?.partial === true || impact?.counts?.partial === true || impact?.reason === 'bounded_captured_graph'
      || ['bounded', 'partial'].includes(impact?.cross_repository?.status) || impact?.cross_repository?.complete === false;
  }
  function dependencyRow(edge) {
    const refs = (edge.evidence_refs || (edge.source_ref ? [edge.source_ref] : [])).map(evidenceLine).filter(Boolean);
    return h('section', {'data-dep-dependency-row': edge.dependency_key}, h('h4', {}, dependencyName(edge.dependency_key)),
      h('p', {}, joinLabel([t('dep_' + edge.relation), versionLabel(edge) || t('dep_dash'), levelText(edge.evidence_level), peerCaption(edge.dependency_key, edge.component_id)])),
      refs.length ? h('dl', {class: 'dep-facts'}, h('dt', {}, t('dep_source')), h('dd', {}, refs.join(' · '))) : '');
  }
  function renderInspector() {
    if (!panel || !selection || !data) return;
    const activeEl = document.activeElement;
    if (panel.contains(activeEl) && ['INPUT', 'TEXTAREA', 'SELECT'].includes(activeEl.tagName)) return;
    const title = panel.querySelector('h2');
    if (title) title.textContent = inspectorTitle();
    const edges = selectionEdges();
    const dep = dependencyByKey().get(selectedKey());
    const component = [...(data.components || []), ...(data.external_components || [])].find(row => row.component_id === (selection.componentId || edges[0]?.component_id));
    const owner = ownerFact(component || data);
    const facts = h('dl', {class: 'dep-facts'});
    const add = (label, value, note) => {facts.append(h('dt', {}, label), h('dd', {}, value)); if (note) facts.append(h('dd', {class: 'dep-note'}, note));};
    if (dep || selection.type !== 'component') add(t('dep_name'), dependencyName(selectedKey()) || t('dep_dash'));
    if (selection.type === 'component') {
      facts.dataset.depComponentFacts = '';
      add(t('dep_component'), componentName(selection.componentId) || t('dep_dash'));
      add(t('dep_ecosystem'), component?.ecosystem || t('dep_dash'));
      add(t('dep_manifest'), component?.manifest_path || t('dep_dash'));
      add(t('dep_version'), component?.version || t('dep_dash'));
    } else for (const edge of edges) {
      add(t('dep_component'), componentName(edge.component_id) || t('dep_dash'));
      add(t('dep_pinned'), edge.resolved || t('dep_dash'), edge.resolved == null && edge.resolution_reason ? reasonText(t, edge.resolution_reason) : '');
      const requirement = requirementValue(edge);
      add(versionFieldLabel(edge), requirement || t('dep_dash'), !requirement && edge.version_reason ? reasonText(t, edge.version_reason) : '');
      add(t('dep_level'), levelText(edge.evidence_level));
      if (edge.basis) add(t('dep_why'), reasonText(t, edge.basis));
      const refs = (edge.evidence_refs || []).map(evidenceLine).filter(Boolean);
      if (refs.length) add(t('dep_source'), refs.join(' · '));
    }
    add(t('dep_owner'), owner.actor || t('dep_dash'), owner.reason ? reasonText(t, owner.reason) : '');
    const tests = h('div', {'data-dep-tests': ''}, h('h3', {}, t('dep_tests')));
    const jobs = hintRows();
    const hintsOmitted = Number(data?.omitted_hints);
    const hintsDropped = [...(data?.projection_omitted_collections || []), ...(impact?.projection_omitted_collections || [])].includes('verification_hints');
    // omitted_hints counts observed hints left out of this capture. It is not a repository total.
    const moreOmitted = hintsDropped || (Number.isFinite(hintsOmitted) && hintsOmitted > 0);
    for (const hint of jobs) tests.append(h('p', {}, joinLabel([hint.job_id, hint.command])));
    if (!jobs.length) tests.append(h('p', {'data-dep-tests-note': moreOmitted ? 'possible' : 'none'}, moreOmitted ? t('dep_tests_none_listed') : t('dep_none_recorded')));
    else if (moreOmitted) tests.append(h('p', {'data-dep-tests-note': 'omitted'}, t('dep_tests_more_omitted')));
    const affected = h('div', {'data-dep-affected': ''}, h('h3', {}, t(selectedKey() ? 'dep_affected' : 'ui_dependencies')));
    if (!selectedKey()) {
      if (edges.length) affected.append(...edges.map(dependencyRow));
      else affected.append(h('p', {}, Number(data.omitted_edges) > 0 ? t('dep_edges_omitted') : t('dep_none_recorded')));
    } else if (!impact || impact.dependency !== selectedKey()) affected.append(skeleton(2));
    else if (impact.error) affected.append(readError(impact.error, () => {impactKey = ''; syncImpact();}, affected));
    else {
      const reasons = [];
      for (const code of [impact.reason, impact.cross_repository?.reason, ...(impact.selection_reasons || []), ...(impact.cross_repository?.selection_reasons || [])]) {
        if (typeof code === 'string' && code && !reasons.includes(code)) reasons.push(code);
      }
      if (partialImpact()) {
        affected.append(statusPill('partial'));
        const clauses = (reasons.length ? reasons : ['partial']).map(code => {const key = 'dep_clause_' + code; const text = t(key); return text === key ? reasonText(t, code) : text;});
        const detail = clauses.length === 1 ? clauses[0] : clauses.length === 2 ? t('dep_impact_pair', {first: clauses[0], second: clauses[1]}) : t('dep_impact_many', {first: clauses[0], rest: clauses.slice(1, -1).join(t('dep_impact_sep')), last: clauses.at(-1)});
        affected.append(h('p', {class: 'dep-impact-summary', 'data-dep-impact-summary': ''}, t('dep_impact_partial', {detail})));
        if (reasons.length) affected.append(disclosure({label: t('dep_impact_reasons'), body: h('div', {'data-dep-impact-reasons': ''}, ...reasons.map(code => h('p', {'data-dep-impact-reason': code}, reasonText(t, code)))), key: JSON.stringify([namespace, workspaceId, 'dep-impact', selectedKey()])}));
      } else for (const code of reasons) affected.append(h('p', {class: 'dep-note', 'data-dep-impact-reason': code}, reasonText(t, code)));
      for (const row of impact.components || []) affected.append(h('p', {}, joinLabel([row.name, Number.isFinite(row.distance) ? t('dep_distance', {n: row.distance}) : ''])));
      for (const row of impact.declared || []) affected.append(h('section', {'data-dep-impact-declaration': row.declaration_id},
        h('p', {}, joinLabel([componentName(row.component_id), row.dependency?.name, t('dep_' + row.relation), versionLabel(row) || t('dep_dash'), levelText(row.evidence_level)])),
        h('p', {class: 'dep-note'}, (row.evidence_refs || (row.source_ref ? [row.source_ref] : [])).map(evidenceLine).filter(Boolean).join(' · '))));
      const count = impact.counts?.components;
      if (impact.counts?.basis === 'returned_components' && Number.isFinite(count) && (count > 0 || !partialImpact())) affected.append(h('p', {class: 'dep-note'}, t('dep_loaded_count', {n: count})));
      if (!(impact.components || []).length && !(impact.declared || []).length && !MISS.has(impact.reason)) affected.append(h('p', {}, t('dep_no_components_returned')));
    }
    const releases = impact && Object.hasOwn(impact, 'affected_releases') ? impact.affected_releases : Object.hasOwn(data, 'affected_releases') ? data.affected_releases : null;
    const releaseBlock = Array.isArray(releases) ? h('div', {'data-dep-releases': ''}, h('h3', {}, t('dep_releases')), ...(releases.length ? releases.map(row => h('p', {}, typeof row === 'string' ? row : joinLabel([row?.name, row?.version, row?.revision_id, row?.id]) || t('dep_dash'))) : [h('p', {}, t('dep_dash'))])) : '';
    const subject = selectedKey() ? dependencyName(selectedKey()) : componentName(selection.componentId);
    const discussionTitle = joinLabel([subject, shortSha(data.capture?.commit_sha) ? t('dep_meta_commit', {commit: shortSha(data.capture.commit_sha)}) : '']);
    const discuss = selection ? discussLink({workspaceId, refs: [], title: discussionTitle, from: 'dependencies'}, {'data-dep-discuss': ''}) : '';
    panel.body.replaceChildren(facts, tests, affected, releaseBlock, h('div', {class: 'dep-actions'}, discuss));
    if (activeEl?.matches('[data-dep-discuss]')) panel.querySelector('[data-dep-discuss]')?.focus({preventScroll: true});
  }
  function drawGraph(edges) {
    const nodes = new Map();
    for (const edge of edges) {
      nodes.set('c:' + edge.component_id, {id: 'c:' + edge.component_id, name: componentName(edge.component_id) || edge.component_id});
      nodes.set('d:' + edge.dependency_key, {id: 'd:' + edge.dependency_key, name: dependencyName(edge.dependency_key) || edge.dependency_key});
    }
    const cross = [];
    for (const link of filteredCrossEdges()) {
      const source = componentName(link.component_id), target = componentName(link.target_component_id);
      if (!source || !target) continue;
      const describe = (id, name) => {
        const row = externalById(id);
        const external = Boolean(row) && !(data?.components || []).some(item => item.component_id === id);
        return {id: 'c:' + id, name, external, repository: external ? otherRepositoryLabel(row) : ''};
      };
      nodes.set('c:' + link.component_id, describe(link.component_id, source));
      nodes.set('c:' + link.target_component_id, describe(link.target_component_id, target));
      cross.push(link);
    }
    const fullLinks = [...edges.map(edge => ({from: 'd:' + edge.dependency_key, to: 'c:' + edge.component_id, relation: edge.relation, level: edge.evidence_level})),
      ...cross.map(link => ({from: 'c:' + link.target_component_id, to: 'c:' + link.component_id, relation: link.relation, level: link.evidence_level}))];
    const rows = new Set(graphRows.map(row => 'c:' + row.component_id));
    const rowLinks = fullLinks.filter(link => rows.has(link.to));
    const candidateIds = new Set(rowLinks.flatMap(link => [link.from, link.to]));
    const previous = document.activeElement?.getAttribute?.('data-dep-node');
    const bounded = boundGraph([...nodes.values()].filter(node => candidateIds.has(node.id)), rowLinks, {required: previous ? [previous] : []});
    const loadedNodes = nodes.size;
    nodes.clear(); for (const node of bounded.nodes) nodes.set(node.id, node);
    const links = bounded.links;
    if (mode === 'graph') {
      coverage.hidden = nodes.size >= loadedNodes;
      coverage.textContent = t('dep_nodes_shown', {shown: nodes.size, loaded: loadedNodes}) + ' · ' + t('dep_narrow');
    }
    const layout = layoutGraph([...nodes.values()], links);
    const placed = new Map(layout.positions.map(position => [position.id, position]));
    // Keep the complete name visible, including distinguishing suffixes. Text
    // wraps inside each node instead of collapsing different names to one prefix.
    const measure = document.createElement('canvas').getContext('2d');
    measure.font = `12px ${getComputedStyle(graphScroll).fontFamily}`;
    const lines = new Map([...nodes].map(([id, node]) => [id,
      wrapDependencyName(node.name, NODE_W - 20, text => measure.measureText(text).width)]));
    const nodeHeight = Math.max(narrow() ? 44 : NODE_H, ...[...nodes].map(([id, node]) => lines.get(id).length * 16 + 16 + (node.external ? 16 : 0)));
    const strideX = Math.max(NODE_W + (narrow() ? 16 : GAP_X), (graphScroll.clientWidth - 2 - PAD * 2 - NODE_W) / Math.max(1, layout.layers - 1));
    const width = Math.max(1, PAD * 2 + NODE_W + Math.max(0, layout.layers - 1) * strideX);
    const depth = layout.positions.reduce((max, position) => Math.max(max, position.y), 0);
    const height = Math.max(nodeHeight + PAD * 2, PAD * 2 + (layout.positions.length ? (depth + 1) * nodeHeight + depth * GAP_Y : 0));
    const selectedIds = new Set();
    if (selection?.type === 'dependency' || selection?.type === 'edge') selectedIds.add('d:' + selection.dependencyKey);
    if (selection?.type === 'component' || selection?.type === 'edge') selectedIds.add('c:' + selection.componentId);
    const selectedId = [...selectedIds][0] || '';
    const incoming = new Map([...nodes.keys()].map(id => [id, []]));
    const outgoing = new Map([...nodes.keys()].map(id => [id, []]));
    for (const link of links) {outgoing.get(link.from)?.push(link.to); incoming.get(link.to)?.push(link.from);}
    const upstream = new Set(), downstream = new Set();
    const walk = (start, direction, into) => {
      const stack = [...(direction.get(start) || [])], seen = new Set();
      while (stack.length) {
        const id = stack.pop();
        if (!id || id === start || seen.has(id)) continue;
        seen.add(id); into.add(id); stack.push(...(direction.get(id) || []));
      }
    };
    for (const id of selectedIds) if (nodes.has(id)) {walk(id, incoming, upstream); walk(id, outgoing, downstream);}
    const markOf = id => !selectedIds.size ? '' : selectedIds.has(id) ? 'selected' : upstream.has(id) || downstream.has(id) ? (upstream.has(id) ? 'upstream' : 'downstream') : 'dim';
    const picture = svg('svg', {class: 'dep-graph', role: 'group', 'aria-label': t('dep_graph_label'), viewBox: `0 0 ${width} ${height}`, width: Math.round(width * zoom), height: Math.round(height * zoom)});
    const arrivals = new Map();
    links.forEach((link, index) => {const list = arrivals.get(link.to) || []; list.push(index); arrivals.set(link.to, list);});
    links.forEach((link, index) => {
      const from = placed.get(link.from), to = placed.get(link.to);
      if (!from || !to) return;
      const group = arrivals.get(link.to) || [index];
      const bow = (group.indexOf(index) - (group.length - 1) / 2) * 8;
      const x1 = PAD + from.x * strideX + NODE_W, y1 = PAD + from.y * (nodeHeight + GAP_Y) + nodeHeight / 2;
      const x2 = PAD + to.x * strideX, y2 = PAD + to.y * (nodeHeight + GAP_Y) + nodeHeight / 2;
      const mid = (x1 + x2) / 2;
      const path = svg('path', {d: `M${x1} ${y1}C${mid} ${y1 + bow} ${mid} ${y2 + bow} ${x2} ${y2}`, 'data-dep-edge': '', 'data-dep-from': link.from, 'data-dep-to': link.to, class: `dep-edge dep-rel-${link.relation}${link.level === 'inferred' ? ' evidence-inferred' : ''}`});
      // Dim an edge when either end is outside the selected path. Keyboard focus does not select.
      if (selectedIds.size && (markOf(link.from) === 'dim' || markOf(link.to) === 'dim')) path.dataset.depMark = 'dim';
      picture.append(path);
    });
    const visualOrder = [...nodes].sort(([a], [b]) => placed.get(a).x - placed.get(b).x || placed.get(a).y - placed.get(b).y);
    for (const [id, node] of visualOrder) {
      const position = placed.get(id);
      if (!position) continue;
      const left = PAD + position.x * strideX, top = PAD + position.y * (nodeHeight + GAP_Y);
      const repo = node.external ? node.repository : '';
      const accessible = repo ? `${node.name}, ${repo}` : node.name;
      const group = svg('g', {class: node.external ? 'dep-node dep-node-external' : 'dep-node', tabindex: '-1', role: 'button', 'aria-label': accessible, 'aria-pressed': String(selectedIds.has(id)), 'data-dep-node': id, 'data-dep-external': node.external ? 'true' : null, 'data-dep-focus': id.startsWith('d:') ? `col:${id.slice(2)}` : `row:${id.slice(2)}`, 'data-dep-mark': markOf(id)});
      const wrapped = lines.get(id), nameY = top + (nodeHeight - wrapped.length * 16 - (repo ? 16 : 0)) / 2 + 12;
      const shownRepo = repo && repo.length > 22 ? repo.slice(0, 21) + '…' : repo;
      place(group, 'append', svg('title', {}, accessible), svg('rect', {x: left, y: top, width: NODE_W, height: nodeHeight, rx: 6, class: 'dep-node-shape'}),
        svg('text', {class: 'dep-node-name'}, ...wrapped.map((line, index) => svg('tspan', {x: left + 10, y: nameY + index * 16}, line))),
        repo ? svg('text', {x: left + 10, y: top + nodeHeight - 8, class: 'dep-node-repo'}, shownRepo) : null);
      picture.append(group);
    }
    const focusNode = picture.querySelector(`[data-dep-node="${CSS.escape(selectedId)}"]`) || picture.querySelector('[data-dep-node]');
    if (focusNode) focusNode.tabIndex = 0;
    graphScroll.replaceChildren(picture);
    graphScroll.scrollLeft = preference.graphLeft || 0;
    graphScroll.scrollTop = preference.graphTop || 0;
  }
  function render() {
    const previous = document.activeElement?.getAttribute?.('data-dep-focus');
    graphName.textContent = ''; graphName.hidden = true;
    element.dataset.depMode = mode;
    toolbar.hidden = !data; details.hidden = !data; holder.hidden = !data;
    if (mode === 'graph') graphBar.prepend(legend); else matrixHost.before(legend);
    matrixHost.hidden = !data || mode !== 'matrix';
    graphHost.hidden = !data || mode !== 'graph';
    const counted = Boolean(data?.capture?.revision_id) && Number.isFinite(data?.counts?.edges);
    const countedEdges = allEdges();
    for (const chip of relationChips) {
      const count = counted ? countedEdges.filter(edge => edge.relation === chip.value).length : null;
      const text = count == null ? t('dep_' + chip.value) : `${t('dep_' + chip.value)} ${count}`;
      chip.label.textContent = text; chip.node.setAttribute('aria-label', text); chip.node.setAttribute('aria-pressed', String(relationFilter.includes(chip.value)));
    }
    for (const chip of evidenceChips) {
      const count = counted ? countedEdges.filter(edge => edge.evidence_level === chip.value).length : null;
      const text = count == null ? t('dep_' + chip.value) : `${t('dep_' + chip.value)} ${count}`;
      chip.label.textContent = text; chip.node.setAttribute('aria-label', text); chip.node.setAttribute('aria-pressed', String(evidenceFilter.includes(chip.value)));
    }
    sourceCommit.textContent = data?.source?.commit_sha ? t('dep_meta_commit', {commit: shortSha(data.source.commit_sha)}) : '';
    const edges = data ? filteredEdges() : [];
    const names = new Map();
    for (const edge of edges) names.set(edge.dependency_key, dependencyName(edge.dependency_key));
    showAllButton.hidden = mode !== 'matrix' || names.size <= COLUMN_LIMIT;
    showAllButton.textContent = showAll ? t('dep_fewer_columns') : t('dep_all_columns', {n: names.size});
    const components = [...(data?.components || [])].filter(row => edges.some(edge => edge.component_id === row.component_id)
      || mode === 'graph' && filteredCrossEdges().some(edge => edge.component_id === row.component_id)).sort((a, b) => a.name.localeCompare(b.name));
    showRowsButton.hidden = components.length <= ROW_LIMIT;
    showRowsButton.textContent = showAllRows ? t('dep_show_fewer') : t('dep_show_all_rows', {n: components.length});
    const visibleRows = !showAllRows && components.length > ROW_LIMIT ? components.slice(0, ROW_LIMIT) : components;
    if (!focusId || !components.some(row => row.component_id === focusId)) focusId = components[0]?.component_id || focusId;
    graphRows = visibleRows;
    let columnDeps = [...names.keys()].map(key => ({key, ...(dependencyByKey().get(key) || {name: key})}));
    columnDeps.sort((a, b) => KINDS.findIndex(kind => (a.kinds || []).includes(kind)) - KINDS.findIndex(kind => (b.kinds || []).includes(kind)) || String(a.name).localeCompare(String(b.name)));
    if (!showAll && columnDeps.length > COLUMN_LIMIT) {
      const kept = new Set(limitColumns(visibleRows, columnDeps.map(dep => dep.key), edges, COLUMN_LIMIT));
      columnDeps = columnDeps.filter(dep => kept.has(dep.key));
    }
    const summaries = [];
    if (visibleRows.length < components.length) summaries.push(t('dep_components_shown', {shown: visibleRows.length, loaded: components.length}));
    if (!narrow() && columnDeps.length < names.size) summaries.push(t('dep_dependencies_shown', {shown: columnDeps.length, loaded: names.size}));
    coverage.hidden = !data || !summaries.length;
    coverage.textContent = [...summaries, t('dep_narrow')].join(' · ');
    legend.hidden = mode === 'matrix' && narrow() || !edges.length && !filteredCrossEdges().length;
    if (!legend.hidden) paintLegend();
    if (!data || !visibleRows.length) {
      const reason = data?.reason === 'not_captured' ? t('dep_not_captured') : data && data.capture?.status === 'complete' && data.counts?.components === 0 ? t('dep_empty') : data?.reason ? reasonText(t, data.reason) : data ? t('dep_no_match') : '';
      matrixScroll.replaceChildren(reason ? emptyState({title: reason}) : '');
      listHost.replaceChildren(reason ? emptyState({title: reason}) : '');
      if (data && mode === 'graph') drawGraph(edges); else graphScroll.replaceChildren();
    } else {
      const groups = new Map();
      for (const dep of columnDeps) {
        const kind = KINDS.find(item => (dep.kinds || []).includes(item)) || KINDS.find(item => edges.some(edge => edge.dependency_key === dep.key && edge.kind === item)) || 'runtime_library';
        if (!groups.has(kind)) groups.set(kind, []);
        groups.get(kind).push(dep);
      }
      const headKind = h('tr');
      const headName = h('tr');
      headKind.append(h('th', {scope: 'col', class: 'dep-corner'}, t('dep_component')));
      headName.append(h('th', {class: 'dep-corner'}));
      let col = 0;
      const colOf = new Map();
      for (const [kind, deps] of groups) {
        headKind.append(h('th', {scope: 'colgroup', colspan: String(deps.length)}, t('dep_kind_' + kind) === 'dep_kind_' + kind ? t('ui_reason_generic') : t('dep_kind_' + kind)));
        for (const dep of deps) {
          colOf.set(dep.key, col);
          const selectedCol = selection?.type === 'dependency' && selection.dependencyKey === dep.key;
          const peer = peerCaption(dep.key);
          const columnLabel = joinLabel([dependencyName(dep.key), peer]);
          headName.append(h('th', {scope: 'col', title: columnLabel}, h('button', {type: 'button', title: columnLabel, 'aria-label': columnLabel, 'data-dep-focus': `col:${dep.key}`, 'data-row': '-1', 'data-col': String(col), tabindex: '-1', 'aria-pressed': String(selectedCol), onclick: () => choose({type: 'dependency', dependencyKey: dep.key}, true)}, abbreviate(dependencyName(dep.key)), peer ? h('span', {class: 'dep-peer', 'data-dep-peer': ''}, peer) : null)));
          col += 1;
        }
      }
      const body = h('tbody');
      visibleRows.forEach((component, row) => {
        const selectedRow = selection?.type === 'component' && selection.componentId === component.component_id;
        const line = h('tr', {}, h('th', {scope: 'row'}, h('button', {type: 'button', class: 'dep-name', 'data-dep-focus': `row:${component.component_id}`, 'data-row': String(row), 'data-col': '-1', tabindex: '-1', 'aria-pressed': String(selectedRow), onclick: () => choose({type: 'component', componentId: component.component_id}, true)}, componentName(component.component_id))));
        for (const dep of columnDeps) {
          const cellEdges = edges.filter(edge => edge.component_id === component.component_id && edge.dependency_key === dep.key);
          const selectedCell = selection?.type === 'edge' && selection.componentId === component.component_id && selection.dependencyKey === dep.key;
          const label = joinLabel(cellEdges.map(edgeLabel));
          line.append(h('td', {}, h('button', {type: 'button', class: 'dep-cell', 'data-dep-cell': cellEdges.length ? `${component.component_id}|${dep.key}` : null, 'data-dep-focus': `cell:${component.component_id}|${dep.key}`, 'data-row': String(row), 'data-col': String(colOf.get(dep.key)), tabindex: '-1', title: label || dependencyName(dep.key), 'aria-label': label || joinLabel([componentName(component.component_id), dependencyName(dep.key)]), 'aria-pressed': String(selectedCell), onclick: cellEdges.length ? () => choose({type: 'edge', componentId: component.component_id, dependencyKey: dep.key}, true) : null}, ...cellEdges.map(mark))));
        }
        body.append(line);
      });
      const focusable = [...body.querySelectorAll('[data-row]'), ...headName.querySelectorAll('[data-row]')];
      const current = focusable.find(node => node.getAttribute('data-dep-focus') === focusKeyFor(selection)) || focusable.find(node => node.getAttribute('data-row') === '0' && node.getAttribute('data-col') === '0') || focusable[0];
      if (current) current.tabIndex = 0;
      matrixScroll.replaceChildren(h('table', {class: 'dep-matrix'}, h('thead', {}, headKind, headName), body));
      matrixScroll.scrollLeft = preference.matrixLeft || 0;
      matrixScroll.scrollTop = preference.matrixTop || 0;
      listHost.replaceChildren(...(narrow() && mode === 'matrix' ? visibleRows : []).map(component => {
        const own = edges.filter(edge => edge.component_id === component.component_id);
        return h('section', {class: 'dep-group'}, h('h2', {}, componentName(component.component_id)), ...own.map(edge => {
          const selectedPick = selection?.type === 'edge' && selection.componentId === edge.component_id && selection.dependencyKey === edge.dependency_key;
          const peer = peerCaption(edge.dependency_key, edge.component_id);
          return h('button', {type: 'button', class: 'dep-pick', 'data-dep-pick': `${edge.component_id}|${edge.dependency_key}`, 'data-dep-focus': `pick:${edge.component_id}|${edge.dependency_key}`, tabindex: selectedPick ? '0' : '-1', 'aria-pressed': String(selectedPick), 'aria-label': edgeLabel(edge), onclick: () => choose({type: 'edge', componentId: edge.component_id, dependencyKey: edge.dependency_key}, true)},
            h('span', {class: 'dep-pick-main'}, h('span', {}, dependencyName(edge.dependency_key)), peer ? h('span', {class: 'dep-peer', 'data-dep-peer': ''}, peer) : null), h('span', {class: 'dep-pick-meta'}, mark(edge), versionLabel(edge)));
        }));
      }));
      const picks = [...listHost.querySelectorAll('[data-dep-pick]')];
      if (picks.length && !picks.some(node => node.tabIndex === 0)) picks[0].tabIndex = 0;
      if (mode === 'graph') drawGraph(edges);
    }
    detailBody.replaceChildren();
    if (data) {
      if (data.capture?.revision_id && data.stale == null) detailBody.append(h('p', {'data-dep-freshness': ''}, t('dep_freshness_unknown', {reason: reasonText(t, data.stale_reason, true)})));
      const omitted = ['omitted_edges', 'omitted_hints', 'omitted_components'].reduce((sum, key) => Number.isFinite(data[key]) && data[key] > 0 ? sum + data[key] : sum, 0);
      if (omitted > 0) detailBody.append(h('p', {'data-dep-omitted': ''}, t('dep_omitted', {n: omitted})));
      for (const code of new Set([...(data.parser_reasons || []), ...(data.catalog_reasons || []), data.cross_repository?.reason, ...(data.cross_repository?.selection_reasons || [])].filter(Boolean))) detailBody.append(h('p', {'data-dep-reason': code}, knownReason(code) ? reasonText(t, code) : reasonText(t, code, true)));
      if (data.projection_omitted_collections?.length) {
        const names = data.projection_omitted_collections.map(code => t('dep_collection_' + code) === 'dep_collection_' + code ? t('ui_reason_generic') : t('dep_collection_' + code));
        detailBody.append(h('p', {}, t('dep_omitted_collections', {names: names.join(' · ')})));
      }
      const files = h('div', {class: 'dep-files'});
      for (const file of data.files || []) {
        const status = FILE_STATUS[file.status] ? t(FILE_STATUS[file.status]) : reasonText(t, file.status, true);
        const note = file.reason && file.reason !== file.status ? reasonText(t, file.reason, !knownReason(file.reason)) : '';
        files.append(h('div', {class: 'dep-file', 'data-dep-file': file.path || ''}, h('span', {}, file.path || t('dep_dash')), h('span', {}, joinLabel([status, note]))));
      }
      for (const row of data.unreadable_paths || []) {
        const path = typeof row === 'string' ? row : row?.path || row?.parent_path;
        files.append(h('div', {class: 'dep-file', 'data-dep-file': path || ''}, h('span', {}, path || t('dep_dash')), h('span', {}, joinLabel([t('dep_file_unreadable'), row?.reason ? reasonText(t, row.reason, true) : '']))));
      }
      if (files.childNodes.length) detailBody.append(files);
      if ((revisions?.revisions || []).length || revisions?.next_cursor) {
        detailBody.append(h('h2', {}, t('dep_revisions')));
        for (const row of revisions?.revisions || []) detailBody.append(h('p', {}, shortSha(row.commit_sha), ' · ', capturedDate(row.captured_at), row.status === 'partial' ? ' · ' + t('ui_status_partial') : row.status === 'complete' ? '' : ' · ' + reasonText(t, row.reason, true)));
        if (revisions?.next_cursor) detailBody.append(h('p', {}, t('dep_revisions_more')));
      }
    }
    loaded.hidden = !(data?.capture?.revision_id || (data?.edges || []).length);
    syncChrome();
    syncImpact();
    if (panel) renderInspector();
    if (previous) {
      const target = focusTarget(previous);
      if (target) {
        for (const node of activeFocusHost().querySelectorAll('[data-dep-focus]')) node.tabIndex = node === target ? 0 : -1;
        target.focus({preventScroll: true});
      }
    }
  }
  function readError(error, retry, within) {
    return h('div', {}, errorBox(error, {within}), button({size: 'sm', variant: 'ghost', label: t('ui_retry'), onClick: retry}));
  }
  async function refresh() {
    if (!active) return;
    const ticket = ++serial;
    if (!data) statusBox.replaceChildren(skeleton(3));
    try {
      const [projection, history] = await Promise.all([
        api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/dependencies`),
        api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/dependencies/revisions`)]);
      alive(); if (ticket !== serial) return;
      if (projection?.workspace_id !== workspaceId || history?.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
      data = projection; revisions = history; statusBox.replaceChildren();
      impactTicket++; impactKey = ''; // An open selection must reread the refreshed projection's impact.
      if (!selectionAlive()) {selection = null; closeInspector();}
      fillComponents(); fillRetract(); render();
      captureForm.update(); declareForm.update(); retractForm.update();
    } catch (error) {
      try {alive(); if (ticket !== serial) return;} catch {return;}
      if ([401, 403, 404].includes(error.status)) withdraw();
      else clearData();
      statusBox.replaceChildren(readError(error, () => {void refresh();}, statusBox));
    }
  }
  function clearData() {
    impactTicket++; data = null; revisions = null; impact = null; impactKey = '';
    closeInspector(); fillComponents(); fillRetract(); render();
    captureForm.update(); declareForm.update(); retractForm.update();
  }
  function withdraw() {
    serial++; impactTicket++; data = null; revisions = null; impact = null; impactKey = ''; selection = null; preference.selection = null;
    closeInspector(); statusBox.replaceChildren();
    fillComponents(); fillRetract(); render();
    captureForm.update(); declareForm.update(); retractForm.update();
  }
  function setActive(value) {
    const next = Boolean(value);
    if (next === active) {syncChrome(); return next ? refresh() : undefined;}
    active = next;
    if (!next) {serial++; impactTicket++; impactKey = ''; impact = null; closeInspector(); syncChrome(); return;}
    syncChrome(); return refresh();
  }
  const media = matchMedia('(max-width: 800px)');
  const onResize = () => render();
  media.addEventListener('change', onResize);
  render();
  return {element, captureButton, setActive, withdraw, dispose() {media.removeEventListener('change', onResize); active = false; serial++; impactTicket++; closeInspector(); syncChrome();}};
}
