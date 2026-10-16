import {viewState} from './ui/view-state.js';
import {h} from './ui/dom.js';
import {skeleton, searchField, dataTable, statusPill, agentMark, disclosure, emptyState} from './ui/components.js';
import {icon} from './ui/icons.js';
import {firstLabel, joinLabel, labelSegment} from './label-text.js';
import {workInspector, workStatus, blockedWork, blockerText, workAgents, prPills, prGroups, readWorkSources, aggregateDelivery, deliveryCoverage, deployStatus, readEarlierPlans} from './pm-work-inspector.js';
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
  let serial = 0, detailSerial = 0, active = false, visible = false, busy = false, plan = null, cards = [], issues = [], reviews = [], milestones = [], sources = new Map(), earlierPlans = new Map();
  let pendingFocus = null, pendingNavigation = null, currentRequirement = null, currentMilestone = null, scrollGeneration = 0;
  let qualityComplete = false, workIncomplete = false, catalogueErrors = [], stale = true;
  let selected = preference.selected || null, signature = '', deliveryPages = 1, preserveSelection = false, tools = () => [];
  const pages = {work: 1, issues: 1, reviews: 1}, cursors = {}, expanded = new Set(preference.expanded || []), order = [];
  const status = h('div', {role: 'status'}), meta = h('span'), tables = h('div', {class: 'pm-requirements-tables'});
  const filter = h('input', {type: 'search', 'aria-label': t('wv_search'), placeholder: t('wv_search'), oninput: () => {preference.filter = filter.value; renderTable();}});
  filter.value = preference.filter || '';
  const group = h('select', {'aria-label': t('wv_group'), onchange: () => {preference.group = group.value; renderTable();}},
    ...['milestone', 'phase', 'none'].map(value => h('option', {value}, t('wv_' + value)))); group.value = preference.group || 'none';
  const layout = h('div', {class: 'pm-work-layout'}, tables);
  const moreButtons = Object.fromEntries(['work', 'issues', 'reviews'].map(kind => [kind, h('button', {type: 'button', class: 'ui-button secondary', hidden: true, 'aria-label': t(kind === 'work' ? 'wb_more' : kind === 'issues' ? 'wb_more_issues' : 'wb_more_reviews'),
    onclick: () => {pages[kind]++; void refresh();}}, t('wv_load_more'))]));
  const element = h('section', {'data-pm-rtm': '', class: 'pm-requirements'},
    h('div', {class: 'pm-requirements-toolbar'}, searchField(filter, {class:'pm-work-search'}), h('label', {}, t('wv_group'), group)),
    status, layout, h('div', {class: 'actions'}, ...Object.values(moreButtons)),
    disclosure({label: t('wb_sources'), body: h('div', {}, h('p', {}, t('wb_no_phase')))}));
  const detail = workInspector({t, workspaceId, from: 'requirements', mount: layout, tools: () => tools(), returnFocus: () => selectionTarget(selected) || viewHeading(), onClose: () => {
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
  const deliverySource = req => {
    const source = aggregateDelivery(linked(req).map(card => sources.get(card.work_item_id)), workIncomplete);
    if (!feature('engineering_delivery')) source.available = false;
    return source;
  };
  const milestoneFor = req => milestones.filter(row => row.plan_revision_id === plan?.revision_id && row.requirement_ids?.includes(req.id));
  const issueLabel = row => joinLabel([firstLabel(row.document.relation?.repository, row.document.issue?.repository), t('wv_issue', {number: row.document.issue.number})]);
  const acceptance = req => requirementReviews(plan, req, reviews).length ? 'accepted' : qualityComplete ? 'not_assessed' : 'unknown';
  const criteriaResults = req => {
    const qualified = requirementReviews(plan, req, reviews);
    const current = qualified.length ? qualified : reviews.filter(row => row.applicable === true && plan?.document_digest && plan?.adoption?.scope_revision
      && row.context?.plan_ref?.revision_id === plan.revision_id && row.context.plan_ref.document_digest === plan.document_digest
      && row.context.plan_ref.scope_revision === plan.adoption.scope_revision && Array.isArray(row.assessment?.document?.criteria)
      && row.assessment.document.criteria.some(result => result.criterion?.kind === 'acceptance' && result.criterion.requirement_id === req.id));
    return current.at(-1)?.assessment?.document?.criteria || [];
  };
  const acceptancePill = req => statusPill(acceptance(req));
  const deployPill = req => statusPill(deployStatus(deliverySource(req)));
  const milestoneLabel = req => {
    const label = milestoneFor(req).map(row => labelSegment(row.title)).filter(Boolean).join(' · ');
    return h('span', {class: 'pm-requirement-milestone', title: label || t('wv_no_milestone'), 'aria-label': label || t('wv_no_milestone')}, label || '—');
  };
  const selectionTarget = id => [...tables.querySelectorAll(`[data-rtm-select="${CSS.escape(id)}"]`),
    ...tables.querySelectorAll(`[data-row-key="${CSS.escape(id)}"]`)].find(node => node.getClientRects().length);
  const viewHeading = () => {const heading = element.closest('main')?.querySelector('h1'); if (heading) heading.tabIndex = -1; return heading;};
  function writeSelection(id) {
    const [path, query] = location.hash.split('?');
    if (!path.endsWith('/requirements')) return;
    const params = new URLSearchParams(query); if (id) params.set('select', id); else params.delete('select');
    history.replaceState(null, '', path + (params.size ? '?' + params : ''));
  }
  function controls() {for (const [kind, button] of Object.entries(moreButtons)) {button.disabled = busy; button.hidden = cursors[kind] == null;}}
  function closeRetaining() {detailSerial++; preserveSelection = true; detail.close(); preserveSelection = false;}
  function withdraw({forgetRoute = true} = {}) {
    pendingFocus = null; pendingNavigation = null; currentRequirement = null; currentMilestone = null; scrollGeneration++; serial++; detailSerial++; busy = false; plan = null; cards = []; issues = []; reviews = []; milestones = []; sources.clear(); earlierPlans.clear(); signature = '';
    if (forgetRoute) writeSelection(null);
    closeRetaining(); tables.replaceChildren(); meta.replaceChildren(); status.replaceChildren();
    if (forgetRoute && visible) viewHeading()?.focus({preventScroll: true});
    for (const kind of Object.keys(pages)) cursors[kind] = null; controls();
  }
  function fail(error, ticket) {
    try {alive(); if (ticket !== serial) return;} catch {return;}
    // Keep the shared event-cycle failure signal, with concise local presentation.
    errorBox(error); withdraw();
    status.replaceChildren(h('p', {class: 'error'}, t(error.code === 'SNAPSHOT_CHANGED' ? 'wb_plan_changed' : error.status === 403 ? 'forbidden_scope' : 'wv_requirements_unavailable')),
      ...(error.code ? [disclosure({label: t('wv_details'), body: h('p', {}, labelSegment(error.code))})] : []),
      h('button', {type: 'button', class: 'ui-button secondary', onclick: () => void refresh()}, t('wv_retry')));
  }
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
        count !== 0 ? h('span', {class: 'ui-meta', title: t('wv_attempts', {n: count})}, `×${count}`) : null, ...prPills(source)),
      card.binding.plan_revision_id && card.binding.plan_revision_id !== plan?.revision_id ? h('span', {class: 'ui-meta'}, t('wv_from_earlier_plan')) : null,
      ...issuesFor(card).map(row => h('span', {class: 'ui-meta'}, issueLabel(row))));
  }
  function renderTable() {
    const focused = tables.contains(document.activeElement) ? ['data-rtm-select', 'data-rtm-toggle', 'data-row-key', 'data-rtm-group']
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
        expandable: req => h('div', {class: 'pm-requirement-expanded'}, h('h2', {}, t('wv_criteria')),
          h('ul', {}, ...(req.acceptance || []).map(row => h('li', {}, labelSegment(row.text)))),
          h('button', {type: 'button', class: 'ui-button ghost pm-requirement-open', 'data-rtm-select': req.id,
            onclick: () => void select(req.id)}, t('wv_open_requirement')),
          ...linked(req).map(workRow), !linked(req).length ? emptyState({title:t('wb_no_work')}) : null),
        compactBelow: 720, compactRow: req => {
          const work = linked(req), blocked = work.find(blockedWork), source = deliverySource(req);
          const coverage = deliveryCoverage(source), prs = prGroups(source.deliveries).length;
          const workLabel = work.length === 1 ? 'wv_compact_work_one' : 'wv_compact_work';
          const prLabel = prs === 1 ? 'wv_compact_pr_one' : 'wv_compact_prs';
          return h('div', {class: 'pm-requirement-summary'}, h('span', {class: 'pm-requirement-title', title: labelSegment(req.text)}, labelSegment(req.text)),
            h('div', {class: 'pm-requirement-facts'},
              h('span', {}, t('wv_acceptance') + ': ', acceptancePill(req)), h('span', {}, t('wv_deploy') + ': ', deployPill(req)),
              h('span', {}, t(workLabel + (workIncomplete ? '_loaded' : ''), {n: work.length})),
              h('span', {}, t(coverage === 'unknown' ? 'wv_compact_prs_unknown' : prLabel + (coverage === 'partial' ? '_loaded' : ''), {n: prs})), milestoneLabel(req)),
            blocked ? h('div', {class: 'pm-requirement-blocker'}, icon('circle-alert'), blockerText(t, blocked)) : null);
        },
        columns: [
          {key: 'title', label: t('wv_requirement')},
          {key: 'milestone', secondary:true, label: t('wv_milestone'), render: milestoneLabel},
          {key: 'work', label: t('wv_work'), numeric: true, render: req => workIncomplete ? t('wv_loaded', {n: linked(req).length}) : String(linked(req).length)},
          {key: 'pr', label: t('wv_pr'), render: req => h('div', {class: 'pm-work-inline'}, ...prPills(deliverySource(req)))},
          {key: 'acceptance', label: t('wv_acceptance'), render: acceptancePill},
          {key: 'deploy', label: t('wv_deploy'), render: deployPill},
          {key: 'blocker', secondary:true, label: t('wv_next_blocker'), render: req => {const card = linked(req).find(blockedWork); return card ? blockerText(t, card) : h('span', {class: 'ui-meta'}, t('ui_status_unknown'));}},
        ]});
      for (const row of table.querySelectorAll('[data-row-key]')) {row.dataset.rtmRequirement = row.dataset.rowKey; row.querySelector('button').dataset.rtmToggle = row.dataset.rowKey;}
      return table;
    };
    tables.replaceChildren();
    for (const [label, rows] of [...groups].sort(([a], [b]) => a ? b ? 0 : -1 : b ? 1 : 0)) {
      const ids = milestoneFor(rows[0]).map(row => row.milestone_id).sort().join(',');
      if (group.value !== 'none') tables.append(h('h2', {class: 'pm-requirement-group', tabindex: -1,
        'data-rtm-group': group.value + ':' + (group.value === 'milestone' ? ids : label), 'data-rtm-milestones': ids}, label || t('wv_unassigned', {n: rows.length})));
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
    if (focused) (tables.querySelector(`[${focused[0]}="${CSS.escape(focused[1])}"]`) || viewHeading())?.focus({preventScroll: true});
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
    if (!active || !visible) {stale = true; return;}
    stale = false;
    const ticket = ++serial; detailSerial++; busy = true; controls();
    try {
      const accepted = await api('GET', `${endpoint}/plans/accepted`); alive(); if (ticket !== serial) return;
      if (accepted.head?.workspace_id !== workspaceId || accepted.plan && (accepted.plan.workspace_id !== workspaceId || accepted.plan.revision_id !== accepted.head.accepted_revision_id)) throw Error(t('pm_scope_mismatch'));
      const work = await catalogue('work', ticket); alive(); if (ticket !== serial) return;
      work.rows.forEach(row => cardCheck(row));
      if (new Set(work.rows.map(row => row.work_item_id)).size !== work.rows.length) throw Error(t('wv_source_mismatch'));
      const settled = await Promise.allSettled([
        feature('issue_mapping') ? catalogue('issues', ticket) : null,
        feature('quality_reviews') ? catalogue('reviews', ticket) : null,
        feature('roadmap') ? api('GET', `${endpoint}/roadmap`).then(scope) : null,
      ]);
      alive(); if (ticket !== serial) return;
      catalogueErrors = settled.flatMap((row, i) => row.status === 'rejected' ? [{kind: ['issues', 'reviews', 'roadmap'][i], code: row.reason?.code}] : []);
      let [mappings, quality, roadmap] = settled.map(row => row.status === 'fulfilled' ? row.value : null);
      if (mappings?.rows.some(row => row.workspace_id !== workspaceId || row.document?.workspace_id !== workspaceId || row.document.work_item_id !== row.work_item_id
        || row.document.relation?.work_item_id !== row.work_item_id || !row.document.issue?.repository || !Number.isSafeInteger(row.document.issue.number))) {
        mappings = null; catalogueErrors.push({kind: 'issues'});
      }
      if (quality?.rows.some(row => row.workspace_id !== workspaceId)) {quality = null; catalogueErrors.push({kind: 'reviews'});}
      qualityComplete = Boolean(quality && !quality.filtered && quality.next == null);
      const read = await readWorkSources({api, workspaceId, cards: work.rows, caps: caps(), t, valid: () => {alive(); return ticket === serial;}});
      const end = await api('GET', `${endpoint}/plans/head`); alive(); if (ticket !== serial) return;
      if (end.head?.workspace_id !== workspaceId || end.head.accepted_revision_id !== accepted.head.accepted_revision_id) throw Error(t('wb_plan_changed'));
      earlierPlans = await readEarlierPlans({api, workspaceId, cards: work.rows, currentId: accepted.plan?.revision_id, valid: () => {alive(); return ticket === serial;}});
      alive(); if (ticket !== serial) return;
      plan = accepted.plan; cards = work.rows; issues = mappings?.rows || []; reviews = quality?.rows || []; sources = read;
      workIncomplete = work.filtered || work.next != null;
      milestones = roadmap?.baseline?.document?.milestones || [];
      for (const [kind, data] of Object.entries({work, issues: mappings, reviews: quality})) cursors[kind] = data?.next ?? null;
      // Concurrent reads fill the source Map in completion order, not content order.
      const next = JSON.stringify([plan, cards.map(card => [card.work_item_id, card.binding, card.work.title, card.column, card.execution?.attempts?.length, blockedWork(card)]), issues, reviews, qualityComplete, workIncomplete, cursors, milestones, [...sources].sort(([left], [right]) => left.localeCompare(right)).map(([id, source]) => [id, source.deliveries, source.available, source.filtered, source.next, source.errors, workAgents(source)])]);
      if (next !== signature) {signature = next; renderTable();}
      const requirements = plan?.document?.requirements || [], partial = !qualityComplete || catalogueErrors.length > 0 || [work, mappings, quality].some(row => row?.filtered || row?.next != null) || [...sources.values()].some(row => deliveryCoverage(row) !== 'complete');
      meta.textContent = !plan ? t('ui_status_unknown') : partial ? t('wv_loaded', {n: requirements.length}) : joinLabel([
        t('wv_requirements_count', {n: requirements.length}), feature('quality_reviews') ? t('wv_accepted_count', {n: requirements.filter(req => requirementReviews(plan, req, reviews).length).length}) : null,
        requirements.some(req => linked(req).some(blockedWork)) ? t(requirements.filter(req => linked(req).some(blockedWork)).length === 1 ? 'wv_blocked_one' : 'wv_blocked_count', {n: requirements.filter(req => linked(req).some(blockedWork)).length}) : null]);
      status.replaceChildren(...(partial ? [h('span', {class: 'ui-meta'}, t('wb_partial'))] : []),
        ...catalogueErrors.map(row => h('span', {}, t(row.kind === 'reviews' ? 'wv_acceptance_unknown' : 'wv_source_unavailable'))),
        ...(catalogueErrors.length ? [h('button', {type: 'button', class: 'ui-button secondary', onclick: () => void refresh()}, t('wv_retry'))] : []));
      const routeSelection = new URLSearchParams(location.hash.split('?')[1]).get('select');
      if (!routeSelection) currentMilestone = null;
      if (visible && routeSelection && routeSelection !== selected && routeSelection !== currentRequirement
        && (routeSelection !== currentMilestone || !milestones.some(row => 'milestone:' + row.milestone_id === currentMilestone))) await select(routeSelection, true);
      else if (visible && selected) await select(selected, false, true);
      busy = false;
      if (pendingFocus) await applyPending();
      if (pendingNavigation) {const ref = pendingNavigation; pendingNavigation = null; await openRef(ref);}
    } catch (error) {fail(error, ticket);} finally {if (ticket === serial) {busy = false; controls();}}
  }
  async function select(id, scroll = false, retain = false) {
    if (id.startsWith('milestone:')) {
      const milestone = milestones.find(row => row.milestone_id === id.slice(10));
      selected = null; preference.selected = null; currentRequirement = null; currentMilestone = null; closeRetaining();
      if (!milestone) {markSelection(); status.textContent = t('wv_not_in_plan'); return;}
      currentMilestone = id;
      group.value = 'milestone'; preference.group = 'milestone'; filter.value = ''; preference.filter = ''; renderTable();
      const heading = [...tables.querySelectorAll('.pm-requirement-group')].find(node => node.dataset.rtmMilestones.split(',').includes(milestone.milestone_id));
      if (heading) {heading.tabIndex = -1; heading.focus(); heading.scrollIntoView({block: 'nearest'});}
      return;
    }
    currentMilestone = null;
    const card = cards.find(row => row.work_item_id === id), requirement = plan?.document?.requirements?.find(row => row.id === id);
    if (!card && !requirement) {const withdrawn = selected === id; selected = null; preference.selected = null; currentRequirement = null; closeRetaining(); markSelection(); status.textContent = t('wv_not_in_plan'); if (withdrawn) {writeSelection(null); viewHeading()?.focus();} return;}
    if (selected !== id) deliveryPages = 1;
    selected = id; preference.selected = id; writeSelection(id);
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
    if (!retain) {detail.show({title: firstLabel(card?.work.title, requirement?.text)}); detail.element.body.replaceChildren(skeleton(2, {label:t('wb_detail_loading')}));}
    if (requirement) {
      let register = null;
      if (caps()?.features?.pm_register?.version === 1) {
        try {
          const data = scope(await api('GET', `${endpoint}/register?limit=100`)); alive(); if (ticket !== detailSerial || generation !== serial) return;
          if (!Array.isArray(data.rows)) throw Error(t('wv_source_mismatch'));
          register = {rows: data.rows.filter(row => ['decision', 'change', 'action', 'issue', 'risk'].includes(row.type) && row.related?.requirement_ids?.includes(id)), complete: data.coverage?.complete === true && data.next_cursor == null};
        } catch {register = null;}
      }
      alive(); if (ticket !== detailSerial || generation !== serial) return;
      detail.show({title: requirement.text, plan, requirement, acceptance: acceptance(requirement), reviews: requirementReviews(plan, requirement, reviews), criteriaResults: criteriaResults(requirement), register,
        work: linked(requirement), workIncomplete, source: deliverySource(requirement), milestones: milestoneFor(requirement), onWork: id => void select(id), onRetry: () => void refresh()}); return;
    }
    try {
      const data = scope(await api('GET', `${endpoint}/kanban/${id}`)); alive(); if (ticket !== detailSerial || generation !== serial) return;
      cardCheck(data.card, id); if (data.card.binding.binding_id !== card.binding.binding_id) throw Error(t('wb_plan_changed'));
      const read = await readWorkSources({api, workspaceId, cards: [data.card], caps: caps(), t, deliveryPages, valid: () => {alive(); return ticket === detailSerial && generation === serial;}});
      alive(); if (ticket !== detailSerial || generation !== serial) return;
      const changed = JSON.stringify(sources.get(id)) !== JSON.stringify(read.get(id));
      sources.set(id, read.get(id)); if (changed) renderTable();
      detail.show({title: data.card.work.title, card: data.card, source: read.get(id), plan: data.card.binding.plan_revision_id === plan?.revision_id ? plan : earlierPlans.get(data.card.binding.plan_revision_id), earlierPlan: data.card.binding.plan_revision_id !== plan?.revision_id, issues: issuesFor(data.card),
        milestones: milestones.filter(row => row.plan_revision_id === data.card.binding.plan_revision_id && row.requirement_ids?.some(id => data.card.binding.requirement_ids?.includes(id))),
        requirements: (data.card.binding.plan_revision_id === plan?.revision_id ? plan : earlierPlans.get(data.card.binding.plan_revision_id))?.document?.requirements?.filter(req => data.card.binding.requirement_ids?.includes(req.id)) || [],
        onMore: () => {deliveryPages++; void select(id, false, true);}});
    } catch (error) {if (ticket === detailSerial && generation === serial) {
      if ([403, 404].includes(error.status)) {cards = cards.filter(row => row.work_item_id !== id); sources.delete(id); workIncomplete = true; renderTable(); meta.textContent = plan ? t('wv_loaded', {n: plan.document.requirements.length}) : t('ui_status_unknown');}
      detail.show({title: card.work.title, error, onRetry: () => void refresh()});
    }}
  }
  const onScroll = () => {
    if (visible && !busy && !element.closest('[hidden]') && window.scrollY > 0) preference.scroll = window.scrollY;
  };
  window.addEventListener('scroll', onScroll, {passive: true});
  function setActive(value) {
    if (visible === value) {if (value && signature) {const id = new URLSearchParams(location.hash.split('?')[1]).get('select'); if (!id) currentMilestone = null; if (id && id !== currentMilestone && (id !== selected || !detail.element)) void select(id, id !== selected);} return;}
    visible = value;
    if (!value) {active = false; stale = true; serial++; detailSerial++; if (!element.closest('[hidden]')) preference.scroll = window.scrollY; closeRetaining();}
    else {
      const generation = ++scrollGeneration; const read = activate(); if (!stale && selected && cards.length) void select(selected, false, true);
      let restoreY = 0;
      requestAnimationFrame(() => {
        if (generation === scrollGeneration) {
          window.scrollTo(0, preference.scroll || 0);
          restoreY = window.scrollY;
        }
      });
      if (read?.then) {
        void read.then(() => {
          if (generation === scrollGeneration && window.scrollY === restoreY) {
            window.scrollTo(0, preference.scroll || 0);
          }
        });
      }
      return read;
    }
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
    if (ref.kind !== 'requirement' || ref.revision_id !== plan?.revision_id || !plan.document?.requirements?.some(row => row.id === ref.id)) {selected = null; currentRequirement = null; closeRetaining(); markSelection(); status.textContent = t('wv_not_in_plan'); return;}
    closeRetaining(); selected = null; preference.selected = null; currentRequirement = ref.id; currentMilestone = null; writeSelection(ref.id);
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
  return {element, meta, refresh, withdraw, openRef, focusWork, focusRequirement, labelFor, setActive, hideInspector: closeRetaining, activate, setTools: value => {tools = value;}, dispose: () => {window.removeEventListener('scroll', onScroll); Object.assign(preference, {selected, expanded: [...expanded], filter: filter.value}); withdraw({forgetRoute: false});}};
}
