import {viewState} from './ui/view-state.js';
import {h} from './ui/dom.js';
import {dataTable, statusPill, agentMark, disclosure, emptyState} from './ui/components.js';
import {icon} from './ui/icons.js';
import {firstLabel, joinLabel, labelSegment} from './label-text.js';
import {workInspector, workStatus, blockedWork, blockerText, workAgents, prPills, prGroups, readWorkSources} from './pm-work-inspector.js';
// Join only central identities, including the exact adopted plan and acceptance inputs.
export function requirementReviews(plan, requirement, reviews) {
  const ids = (requirement.acceptance || []).map(row => row.source_input_id);
  if (!ids.length || ids.some(id => !id) || !plan.document_digest || !plan.adoption?.scope_revision) return [];
  return reviews.filter(row => row.qualified === true && row.applicable === true && row.assessment?.document?.qualified === true
    && row.context?.plan_ref?.revision_id === plan.revision_id
    && row.context.plan_ref.document_digest === plan.document_digest
    && row.context.plan_ref.scope_revision === plan.adoption?.scope_revision
    && ids.every(id => row.assessment.document.criteria?.some(result => result.criterion?.kind === 'acceptance'
      && result.criterion.requirement_id === requirement.id && result.criterion.input_id === id && result.status === 'accepted')));
}

export function workspaceTraceability({t, api, alive, namespace, workspaceId, caps, errorBox}) {
  const preference = viewState(namespace, workspaceId, 'requirements'), endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  let serial = 0, detailSerial = 0, active = false, visible = false, busy = false, plan = null, cards = [], issues = [], reviews = [], milestones = [], sources = new Map();
  let pendingFocus = null, pendingNavigation = null, currentRequirement = null, scrollGeneration = 0;
  let selected = preference.selected || null, signature = '', deliveryPages = 1, preserveSelection = false, tools = () => [];
  const pages = {work: 1, issues: 1, reviews: 1}, cursors = {}, expanded = new Set(preference.expanded || []), order = [];
  const status = h('div', {role: 'status'}), meta = h('span'), tables = h('div', {class: 'pm-requirements-tables'});
  const filter = h('input', {type: 'search', 'aria-label': t('wv_search'), placeholder: t('wv_search'), oninput: () => {preference.filter = filter.value; renderTable();}});
  filter.value = preference.filter || '';
  const group = h('select', {'aria-label': t('wv_group'), onchange: () => {preference.group = group.value; renderTable();}},
    ...['milestone', 'phase', 'none'].map(value => h('option', {value}, t('wv_' + value)))); group.value = preference.group || 'none';
  const layout = h('div', {class: 'pm-work-layout'}, tables);
  const moreButtons = Object.fromEntries(['work', 'issues', 'reviews'].map(kind => [kind, h('button', {type: 'button', class: 'ui-button secondary', hidden: true,
    onclick: () => {pages[kind]++; void refresh();}}, t(kind === 'work' ? 'wb_more' : kind === 'issues' ? 'wb_more_issues' : 'wb_more_reviews'))]));
  const element = h('section', {'data-pm-rtm': '', class: 'pm-requirements'},
    h('div', {class: 'pm-requirements-toolbar'}, h('label', {class: 'pm-work-search'}, icon('search'), filter), h('label', {}, t('wv_group'), group)),
    status, layout, h('div', {class: 'actions'}, ...Object.values(moreButtons)),
    disclosure({label: t('wb_sources'), body: h('div', {}, h('p', {}, t('wb_acceptance_separate')), h('p', {}, t('wb_no_phase')))}));
  const detail = workInspector({t, workspaceId, from: 'requirements', mount: layout, tools: () => tools(), onClose: () => {
    detailSerial++; if (!preserveSelection) {const id = selected; selected = null; preference.selected = null; markSelection();
      if (id) selectionTarget(id)?.focus({preventScroll: true});
      const [path, query] = location.hash.split('?'), params = new URLSearchParams(query); params.delete('select');
      history.replaceState(null, '', path + (params.size ? '?' + params : ''));}
  }});
  const feature = name => caps()?.features?.pm_workspaces?.[name];
  const scope = data => {if (data?.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch')); return data;};
  const cardCheck = (row, id = row?.work_item_id) => {
    if (!/^wi_[0-9a-f]{20}$/.test(id || '') || row?.work_item_id !== id || row.work?.work_item_id !== id
      || row.binding?.workspace_id !== workspaceId || row.binding.work_item_id !== id
      || !/^pmwb_[0-9a-f]{32}$/.test(row.binding.binding_id)) throw Error(t('pm_scope_mismatch'));
    if (row.latest_capture && (row.latest_capture.workspace_id !== workspaceId || row.latest_capture.work_item_id !== id)) throw Error(t('pm_scope_mismatch'));
    return row;
  };
  const issuesFor = card => issues.filter(row => row.work_item_id === card.work_item_id && row.document.relation?.binding_id === card.binding.binding_id);
  const linked = req => cards.filter(card => card.binding.plan_revision_id === plan?.revision_id && card.binding.requirement_ids?.includes(req.id));
  const deliverySource = req => ({deliveries: [...new Map(linked(req).flatMap(card => sources.get(card.work_item_id)?.deliveries || []).map(row => [row.operation_id, row])).values()]});
  const milestoneFor = req => milestones.filter(row => row.plan_revision_id === plan?.revision_id && row.requirement_ids?.includes(req.id));
  const issueLabel = row => joinLabel([firstLabel(row.document.relation?.repository, row.document.issue?.repository), t('wv_issue', {number: row.document.issue.number})]);
  const acceptancePill = req => statusPill(requirementReviews(plan, req, reviews).length ? 'accepted' : 'not_assessed');
  const deployPill = req => {
    const groups = prGroups(deliverySource(req).deliveries), deployed = groups.filter(group => group.rows.some(row => row.deployment?.verified === true && row.deployment.is_current === true)).length;
    return statusPill(deployed && deployed === groups.length ? 'deployed' : deployed ? 'partial' : 'unknown');
  };
  const milestoneLabel = req => {
    const label = milestoneFor(req).map(row => labelSegment(row.title)).filter(Boolean).join(' · ');
    return h('span', {class: 'pm-requirement-milestone', title: label || t('wv_no_milestone'), 'aria-label': label || t('wv_no_milestone')}, label || '—');
  };
  const selectionTarget = id => [...tables.querySelectorAll(`[data-rtm-select="${CSS.escape(id)}"], [data-row-key="${CSS.escape(id)}"]`)].find(node => node.getClientRects().length);
  function controls() {for (const [kind, button] of Object.entries(moreButtons)) {button.disabled = busy; button.hidden = cursors[kind] == null;}}
  function closeRetaining() {detailSerial++; preserveSelection = true; detail.close(); preserveSelection = false;}
  function withdraw() {
    pendingFocus = null; pendingNavigation = null; currentRequirement = null; scrollGeneration++; serial++; detailSerial++; busy = false; plan = null; cards = []; issues = []; reviews = []; milestones = []; sources.clear(); signature = '';
    closeRetaining(); tables.replaceChildren(); meta.replaceChildren(); status.replaceChildren();
    for (const kind of Object.keys(pages)) cursors[kind] = null; controls();
  }
  function fail(error, ticket) {try {alive(); if (ticket !== serial) return;} catch {return;} withdraw(); status.replaceChildren(errorBox(error));}
  function markSelection() {
    for (const row of tables.querySelectorAll('[data-row-key]')) {
      row.setAttribute('aria-selected', String(row.dataset.rowKey === selected));
      if (row.dataset.rowKey === currentRequirement) row.setAttribute('aria-current', 'true'); else row.removeAttribute('aria-current');
    }
    for (const node of tables.querySelectorAll('[data-rtm-select]')) node.setAttribute('aria-pressed', String(node.dataset.rtmSelect === selected));
  }
  function workRow(card) {
    const source = sources.get(card.work_item_id);
    const attempts = card.execution?.attempts?.length, limited = card.execution?.coverage?.unavailable_sources || card.execution?.coverage?.truncated;
    const count = limited && !attempts ? '—' : attempts ?? '—';
    return h('div', {'data-rtm-work': card.work_item_id, class: 'pm-requirement-work'},
      h('button', {type: 'button', class: 'ui-button ghost pm-rtm-work', 'data-rtm-select': card.work_item_id,
        'aria-pressed': String(selected === card.work_item_id), onclick: () => void select(card.work_item_id)}, firstLabel(card.work.title, t('ui_work_item'))),
      h('div', {class: 'pm-work-inline'}, statusPill(workStatus(card)), ...workAgents(source).map(agentMark),
        h('span', {class: 'ui-meta', title: t('wv_attempts', {n: count})}, `×${count}`), ...prPills(source)),
      ...issuesFor(card).map(row => h('span', {class: 'ui-meta'}, issueLabel(row))));
  }
  function renderTable() {
    const focused = tables.contains(document.activeElement) ? ['data-rtm-select', 'data-rtm-toggle', 'data-row-key']
      .map(attribute => [attribute, document.activeElement?.getAttribute(attribute)]).find(([, value]) => value) : null;
    const scrolls = [...tables.querySelectorAll('.ui-data-table')].map(node => node.scrollLeft);
    const query = filter.value.trim().toLocaleLowerCase(), requirements = plan?.document?.requirements || [];
    const matches = (req, work) => !query || joinLabel([req?.text, req?.id, ...work.flatMap(card => [card.work.title,
      ...issuesFor(card).flatMap(row => [row.document.selected?.public_title, row.document.issue?.repository,
        Number.isSafeInteger(row.document.issue?.number) ? `#${row.document.issue.number}` : ''])])], ' ').toLocaleLowerCase().includes(query);
    const displayed = requirements.filter(req => matches(req, linked(req)));
    for (const req of displayed) if (!order.includes(req.id)) order.push(req.id);
    displayed.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
    const groups = new Map();
    for (const req of displayed) {
      const key = group.value === 'milestone' ? milestoneFor(req).map(row => row.title).join(' · ') : group.value === 'phase' ? t('wv_phase_unknown') : '';
      if (!groups.has(key)) groups.set(key, []); groups.get(key).push(req);
    }
    const tableFor = requirements => {
      const table = dataTable({rows: requirements.map(req => ({...req, title: req.text})), rowKey: 'id', selected, expanded,
        onToggle: (id, open) => {if (open) expanded.add(id); else expanded.delete(id); preference.expanded = [...expanded];},
        onSelect: (id, row) => {tables.querySelector(`[data-row-key="${CSS.escape(id)}"]`)?.focus(); void select(id);},
        expandable: req => h('div', {class: 'pm-requirement-expanded'}, h('h3', {}, t('wv_criteria')),
          h('ul', {}, ...(req.acceptance || []).map(row => h('li', {}, labelSegment(row.text)))),
          h('button', {type: 'button', class: 'ui-button ghost pm-requirement-open', 'data-rtm-select': req.id,
            onclick: () => void select(req.id)}, t('wv_open_requirement')),
          ...linked(req).map(workRow), !linked(req).length ? h('p', {class: 'ui-meta'}, t('wb_no_work')) : null),
        compactRow: req => {
          const work = linked(req), blocked = work.find(blockedWork), source = deliverySource(req);
          const partialPRs = work.some(card => sources.get(card.work_item_id)?.filtered || sources.get(card.work_item_id)?.next != null);
          return h('div', {class: 'pm-requirement-summary'}, h('span', {class: 'pm-requirement-title', title: labelSegment(req.text)}, labelSegment(req.text)),
            h('div', {class: 'pm-requirement-facts'},
              h('span', {'aria-label': t('wv_acceptance')}, acceptancePill(req)), h('span', {'aria-label': t('wv_deploy')}, deployPill(req)),
              h('span', {}, t(cursors.work != null ? 'wv_compact_work_loaded' : 'wv_compact_work', {n: work.length})),
              h('span', {}, t(!feature('engineering_delivery') ? 'wv_compact_prs_unknown' : partialPRs ? 'wv_compact_prs_loaded' : 'wv_compact_prs', {n: prGroups(source.deliveries).length})), milestoneLabel(req)),
            blocked ? h('div', {class: 'pm-requirement-blocker'}, icon('circle-alert'), blockerText(t, blocked)) : null);
        },
        columns: [
          {key: 'title', label: t('wv_requirement')},
          {key: 'milestone', label: t('wv_milestone'), render: milestoneLabel},
          {key: 'work', label: t('wv_work'), numeric: true, render: req => cursors.work != null ? t('wv_loaded', {n: linked(req).length}) : String(linked(req).length)},
          {key: 'pr', label: t('wv_pr'), render: req => h('div', {class: 'pm-work-inline'}, ...prPills(deliverySource(req)))},
          {key: 'acceptance', label: t('wv_acceptance'), render: acceptancePill},
          {key: 'deploy', label: t('wv_deploy'), render: deployPill},
          {key: 'blocker', label: t('wv_next_blocker'), render: req => {const card = linked(req).find(blockedWork); return card ? blockerText(t, card) : h('span', {class: 'ui-meta'}, t('ui_status_unknown'));}},
        ]});
      for (const row of table.querySelectorAll('[data-row-key]')) {row.dataset.rtmRequirement = row.dataset.rowKey; row.querySelector('button').dataset.rtmToggle = row.dataset.rowKey;}
      return table;
    };
    tables.replaceChildren();
    for (const [label, rows] of [...groups].sort(([a], [b]) => a ? b ? 0 : -1 : b ? 1 : 0)) {
      if (group.value !== 'none') tables.append(h('h2', {class: 'pm-requirement-group'}, label || t('wv_unassigned', {n: rows.length})));
      tables.append(tableFor(rows));
    }
    const unassigned = cards.filter(card => !requirements.some(req => card.binding.plan_revision_id === plan?.revision_id && card.binding.requirement_ids?.includes(req.id)))
      .filter(card => matches(null, [card]));
    const orphanIssues = issues.filter(row => !cards.some(card => row.work_item_id === card.work_item_id && row.document.relation?.binding_id === card.binding.binding_id))
      .filter(row => !query || joinLabel([row.document.selected?.public_title, row.document.issue?.repository, row.document.issue?.number], ' ').toLocaleLowerCase().includes(query));
    if (unassigned.length || orphanIssues.length) {
      const fold = disclosure({label: t('wv_unassigned', {n: unassigned.length + orphanIssues.length}), open: preference.unassigned !== false,
        body: h('div', {}, ...unassigned.map(workRow), ...orphanIssues.map(row => h('p', {'data-rtm-unassigned-issue': row.mapping_id},
          firstLabel(row.document.selected?.public_title, t('wb_work')), ' · ', issueLabel(row))))});
      fold.dataset.rtmUnassigned = ''; fold.addEventListener('toggle', () => {preference.unassigned = fold.open;}); tables.append(fold);
    }
    if (!displayed.length && !unassigned.length && !orphanIssues.length) tables.append(emptyState({title: t(query ? 'wb_no_match' : 'wb_no_requirements')}));
    [...tables.querySelectorAll('.ui-data-table')].forEach((node, i) => {node.scrollLeft = scrolls[i] || preference.tableScroll || 0; node.addEventListener('scroll', () => {preference.tableScroll = node.scrollLeft;});});
    markSelection();
    if (focused) tables.querySelector(`[${focused[0]}="${CSS.escape(focused[1])}"]`)?.focus({preventScroll: true});
  }
  async function catalogue(kind, ticket) {
    const config = {work: ['kanban', 'cards'], issues: ['issue-mappings', 'mappings'], reviews: ['quality-reviews', 'reviews']}[kind];
    let after = 0, next = null, filtered = false; const rows = [];
    for (let index = 0; index < pages[kind]; index++) {
      const data = scope(await api('GET', `${endpoint}/${config[0]}?after=${after}&limit=20`)); alive(); if (ticket !== serial) return null;
      if (!Array.isArray(data[config[1]]) || data.next_cursor != null && (!Number.isSafeInteger(data.next_cursor) || data.next_cursor <= after)) throw Error(t('pm_scope_mismatch'));
      rows.push(...data[config[1]]); next = data.next_cursor ?? null;
      filtered ||= Boolean(data.visibility_filtered || data.coverage?.visibility_filtered || data.coverage?.byte_limited);
      if (next === null) break; after = next;
    }
    return {rows, next, filtered};
  }
  async function refresh() {
    if (!active) return;
    const ticket = ++serial; detailSerial++; busy = true; controls();
    try {
      const accepted = await api('GET', `${endpoint}/plans/accepted`); alive(); if (ticket !== serial) return;
      if (accepted.head?.workspace_id !== workspaceId || accepted.plan && (accepted.plan.workspace_id !== workspaceId || accepted.plan.revision_id !== accepted.head.accepted_revision_id)) throw Error(t('pm_scope_mismatch'));
      const [work, mappings, quality, roadmap] = await Promise.all([catalogue('work', ticket), feature('issue_mapping') ? catalogue('issues', ticket) : null,
        feature('quality_reviews') ? catalogue('reviews', ticket) : null, feature('roadmap') ? api('GET', `${endpoint}/roadmap`) : null]);
      alive(); if (ticket !== serial) return;
      work.rows.forEach(row => cardCheck(row));
      if (new Set(work.rows.map(row => row.work_item_id)).size !== work.rows.length) throw Error(t('pm_scope_mismatch'));
      for (const row of mappings?.rows || []) if (row.workspace_id !== workspaceId || row.document?.workspace_id !== workspaceId || row.document.work_item_id !== row.work_item_id
        || row.document.relation?.work_item_id !== row.work_item_id || !row.document.issue?.repository || !Number.isSafeInteger(row.document.issue.number)) throw Error(t('pm_scope_mismatch'));
      for (const row of quality?.rows || []) if (row.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
      if (roadmap) scope(roadmap);
      const read = await readWorkSources({api, workspaceId, cards: work.rows, caps: caps(), t, valid: () => {alive(); return ticket === serial;}});
      const end = await api('GET', `${endpoint}/plans/head`); alive(); if (ticket !== serial) return;
      if (end.head?.workspace_id !== workspaceId || end.head.accepted_revision_id !== accepted.head.accepted_revision_id) throw Error(t('wb_plan_changed'));
      plan = accepted.plan; cards = work.rows; issues = mappings?.rows || []; reviews = quality?.rows || []; sources = read;
      milestones = roadmap?.baseline?.document?.milestones || [];
      for (const [kind, data] of Object.entries({work, issues: mappings, reviews: quality})) cursors[kind] = data?.next ?? null;
      const next = JSON.stringify([plan, cards.map(card => [card.work_item_id, card.binding, card.work.title, card.column, card.execution?.attempts?.length, blockedWork(card)]), issues, reviews, milestones, [...sources].map(([id, source]) => [id, source.deliveries, workAgents(source)])]);
      if (next !== signature) {signature = next; renderTable();}
      const requirements = plan?.document?.requirements || [], partial = [work, mappings, quality].some(row => row?.filtered || row?.next != null) || [...sources.values()].some(row => row.filtered || row.next != null);
      meta.textContent = !plan ? t('ui_status_unknown') : partial ? t('wv_loaded', {n: requirements.length}) : joinLabel([
        t('wv_requirements_count', {n: requirements.length}), feature('quality_reviews') ? t('wv_accepted_count', {n: requirements.filter(req => requirementReviews(plan, req, reviews).length).length}) : null,
        requirements.some(req => linked(req).some(blockedWork)) ? t(requirements.filter(req => linked(req).some(blockedWork)).length === 1 ? 'wv_blocked_one' : 'wv_blocked_count', {n: requirements.filter(req => linked(req).some(blockedWork)).length}) : null]);
      status.replaceChildren(...(partial ? [h('span', {class: 'ui-meta'}, t('wb_partial'))] : []));
      const routeSelection = new URLSearchParams(location.hash.split('?')[1]).get('select');
      if (visible && routeSelection && routeSelection !== selected) await select(routeSelection, true);
      else if (visible && selected) await select(selected, false, true);
      busy = false;
      if (pendingFocus) await applyPending();
      if (pendingNavigation) {const ref = pendingNavigation; pendingNavigation = null; await openRef(ref);}
    } catch (error) {fail(error, ticket);} finally {if (ticket === serial) {busy = false; controls();}}
  }
  async function select(id, scroll = false, retain = false) {
    const card = cards.find(row => row.work_item_id === id), requirement = plan?.document?.requirements?.find(row => row.id === id);
    if (!card && !requirement) {closeRetaining(); return;}
    if (selected !== id) deliveryPages = 1;
    selected = id; preference.selected = id;
    if (scroll) {
      scrollGeneration++;
      // An explicit destination must be visible even when the previous search hid it.
      filter.value = ''; preference.filter = '';
      if (card) for (const req of plan?.document?.requirements || []) if (linked(req).includes(card)) expanded.add(req.id);
      renderTable();
    }
    const opener = selectionTarget(id);
    if (scroll) {opener?.scrollIntoView({block: 'center'}); opener?.focus({preventScroll: true});}
    markSelection();
    const ticket = ++detailSerial, generation = serial;
    if (!retain) {detail.show({title: firstLabel(card?.work.title, requirement?.text)}); detail.element.body.replaceChildren(h('p', {role: 'status'}, t('wb_detail_loading')));}
    if (requirement) {const blocked = linked(requirement).find(blockedWork); detail.show({title: requirement.text, plan, requirement, milestones: milestoneFor(requirement), blocker: blocked ? blockerText(t, blocked) : null, accepted: requirementReviews(plan, requirement, reviews).length > 0}); return;}
    try {
      const data = scope(await api('GET', `${endpoint}/kanban/${id}`)); alive(); if (ticket !== detailSerial || generation !== serial) return;
      cardCheck(data.card, id); if (data.card.binding.binding_id !== card.binding.binding_id) throw Error(t('wb_plan_changed'));
      const read = await readWorkSources({api, workspaceId, cards: [data.card], caps: caps(), t, deliveryPages, valid: () => {alive(); return ticket === detailSerial && generation === serial;}});
      alive(); if (ticket !== detailSerial || generation !== serial) return;
      const changed = JSON.stringify(sources.get(id)) !== JSON.stringify(read.get(id));
      sources.set(id, read.get(id)); if (changed) renderTable();
      detail.show({title: data.card.work.title, card: data.card, source: read.get(id), plan: data.card.binding.plan_revision_id === plan?.revision_id ? plan : null, issues: issuesFor(data.card),
        milestones: milestones.filter(row => row.plan_revision_id === data.card.binding.plan_revision_id && row.requirement_ids?.some(id => data.card.binding.requirement_ids?.includes(id))),
        requirements: plan?.document?.requirements?.filter(req => data.card.binding.plan_revision_id === plan.revision_id && data.card.binding.requirement_ids?.includes(req.id)) || [],
        onMore: () => {deliveryPages++; void select(id, false, true);}});
    } catch (error) {if (ticket === detailSerial) fail(error, generation);}
  }
  function setActive(value) {
    if (visible === value) {if (value) {const id = new URLSearchParams(location.hash.split('?')[1]).get('select'); if (id && id !== selected) void select(id, true);} return;}
    visible = value;
    if (!value) {preference.scroll = window.scrollY; closeRetaining();}
    else {const generation = ++scrollGeneration; void activate(); if (selected && cards.length) void select(selected, false, true); requestAnimationFrame(() => {if (generation === scrollGeneration) window.scrollTo(0, preference.scroll || 0);});}
  }
  async function activate() {if (!active) {active = true; await refresh();}}
  async function openRef(ref) {
    scrollGeneration++;
    if (!active || !plan || busy) {pendingNavigation = ref; if (!active) await activate(); return;}
    if (ref.kind === 'work_item') {
      await select(ref.id, true);
      const target = matchMedia('(max-width: 1199px)').matches ? detail.element?.querySelector(':scope > header h2') : selectionTarget(ref.id);
      if (target) {target.tabIndex = target.matches('h2') ? -1 : target.tabIndex; target.focus({preventScroll: true}); target.scrollIntoView({block: 'nearest'});}
      return;
    }
    if (ref.kind !== 'requirement' || ref.revision_id !== plan?.revision_id || !plan.document?.requirements?.some(row => row.id === ref.id)) return;
    closeRetaining(); selected = null; preference.selected = null; currentRequirement = ref.id;
    expanded.add(ref.id); preference.expanded = [...expanded]; filter.value = ''; preference.filter = ''; renderTable();
    const row = tables.querySelector(`[data-rtm-requirement="${CSS.escape(ref.id)}"]`);
    const target = matchMedia('(max-width: 799px)').matches ? row : row?.querySelector('[data-rtm-toggle]');
    target?.focus({preventScroll: true}); row?.scrollIntoView({block: 'nearest'});
  }
  function focusWork(id) {pendingFocus = {kind: 'work_item', id}; if (!active) return activate(); if (!busy) void applyPending();}
  function focusRequirement(id) {pendingFocus = {kind: 'requirement', id}; if (!active) return activate(); if (!busy) void applyPending();}
  async function applyPending() {
    const pending = pendingFocus;
    if (!pending || !plan) return;
    pendingFocus = null; await openRef({...pending, revision_id: plan.revision_id});
  }
  function labelFor(kind, id) {
    const value = kind === 'requirement' ? plan?.document?.requirements?.find(row => row.id === id)?.text
      : kind === 'work_item' ? cards.find(row => row.work_item_id === id)?.work?.title : '';
    return typeof value === 'string' ? value : '';
  }
  return {element, meta, refresh, withdraw, openRef, focusWork, focusRequirement, labelFor, setActive, hideInspector: closeRetaining, activate, setTools: value => {tools = value;}, dispose: () => {Object.assign(preference, {selected, expanded: [...expanded], filter: filter.value}); withdraw();}};
}
