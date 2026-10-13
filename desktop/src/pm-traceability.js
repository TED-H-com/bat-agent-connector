import {viewState} from './ui/view-state.js';
import {firstLabel, joinLabel} from './label-text.js';
import {qualityFact} from './pm-quality-projections.js';

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

export function workspaceTraceability({h, t, api, alive, namespace, workspaceId, caps, errorBox}) {
  const preference = viewState(namespace, workspaceId, 'requirements');
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  let serial = 0, detailSerial = 0, active = false, busy = false, plan = null, cards = [], issues = [], reviews = [], milestones = [];
  let selected = preference.selected || null, detailCard = null, deliveryRows = [], deliveryCursor = null, detailBusy = false, signature = '', renderedDetail = '', deliveryPages = 1;
  const pages = {work: 1, issues: 1, reviews: 1}, cursors = {}, expanded = new Set(preference.expanded || []), order = [];
  const status = h('div', {role: 'status'}), count = h('p', {class: 'muted'}), tableBody = h('tbody');
  const filter = h('input', {type: 'search', 'aria-label': t('wb_filter'), placeholder: t('wb_filter'), oninput: () => renderTable()});
  filter.value = preference.filter || '';
  const detail = h('aside', {class: 'pm-rtm-detail', 'data-rtm-detail': '', hidden: true});
  const refreshButton = h('button', {type: 'button', class: 'secondary', onclick: () => void refresh()}, t('wb_apply_update'));
  const moreButtons = Object.fromEntries(['work', 'issues', 'reviews'].map(kind => [kind, h('button', {type: 'button', class: 'secondary', hidden: true,
    onclick: () => {pages[kind]++; void refresh();}}, t(kind === 'work' ? 'wb_more' : kind === 'issues' ? 'wb_more_issues' : 'wb_more_reviews'))]));
  const table = h('div', {class: 'pm-rtm-table', tabindex: '0', role: 'region', 'aria-label': t('wb_rtm')}, h('table', {},
    h('thead', {}, h('tr', {}, ...['wb_requirements', 'wb_milestone', 'wb_work', 'wb_acceptance'].map(key => h('th', {scope: 'col'}, t(key))))), tableBody));
  const element = h('section', {'data-pm-rtm': ''}, h('header', {class: 'pm-heading'}, h('h2', {}, t('wb_rtm')), refreshButton),
    h('div', {class: 'pm-workbench-toolbar'}, filter, count), status, h('div', {class: 'pm-rtm-layout'}, table, detail),
    h('div', {class: 'actions'}, ...Object.values(moreButtons)), h('details', {}, h('summary', {}, t('wb_sources')),
      h('p', {}, t('wb_acceptance_separate')), h('p', {}, t('wb_no_phase'))));
  const scope = data => {if (data?.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch')); return data;};
  const label = (prefix, value) => {if (!value) return t('wb_unknown'); const key = prefix + value; return t(key) === key ? String(value).replaceAll('_', ' ') : t(key);};
  const code = value => h('code', {class: 'pm-source'}, value || t('wb_unknown'));
  const time = value => Number.isFinite(value) ? new Date(value * 1000).toLocaleString() : t('wb_unknown');
  const feature = name => caps()?.features?.pm_workspaces?.[name];
  const cardCheck = (row, id = row?.work_item_id) => {
    if (!/^wi_[0-9a-f]{20}$/.test(id || '') || row?.work_item_id !== id || row.work?.work_item_id !== id
      || row.binding?.workspace_id !== workspaceId || row.binding.work_item_id !== id
      || !/^pmwb_[0-9a-f]{32}$/.test(row.binding.binding_id)) throw Error(t('pm_scope_mismatch'));
    return row;
  };
  const issuesFor = card => issues.filter(row => row.work_item_id === card.work_item_id
    && row.document.relation?.binding_id === card.binding.binding_id);
  const detailSignature = () => JSON.stringify([detailCard && [detailCard.work.title, detailCard.column,
    detailCard.binding, detailCard.quality, detailCard.execution, detailCard.latest_capture, issuesFor(detailCard)],
    plan?.revision_id, plan?.adoption, deliveryRows, deliveryCursor]);
  function controls() {refreshButton.disabled = busy; for (const [kind, button] of Object.entries(moreButtons)) {
    button.disabled = busy; button.hidden = cursors[kind] == null;
  }}
  function withdraw() {
    serial++; detailSerial++; busy = false; plan = null; cards = []; issues = []; reviews = []; milestones = []; signature = '';
    detailCard = null; deliveryRows = []; deliveryCursor = null; detailBusy = false; renderedDetail = '';
    tableBody.replaceChildren(); detail.replaceChildren(); detail.hidden = true; count.replaceChildren(); status.replaceChildren();
    for (const kind of Object.keys(pages)) cursors[kind] = null; controls();
  }
  function fail(error, ticket) {try {alive(); if (ticket !== serial) return;} catch {return;}
    withdraw(); status.replaceChildren(errorBox(error));}
  function workButton(card) {return h('button', {type: 'button', class: 'secondary pm-rtm-work', 'data-rtm-select': card.work_item_id,
    'aria-pressed': String(selected === card.work_item_id), onclick: () => void select(card.work_item_id, true)}, card.work.title);}
  function renderTable() {
    const focused = tableBody.contains(document.activeElement) ? ['data-rtm-select', 'data-rtm-toggle']
      .map(attribute => [attribute, document.activeElement?.getAttribute(attribute)]).find(([, value]) => value) : null;
    tableBody.replaceChildren();
    const query = filter.value.trim().toLocaleLowerCase(), requirements = plan?.document?.requirements || [];
    const linked = req => cards.filter(card => card.binding.plan_revision_id === plan.revision_id && card.binding.requirement_ids?.includes(req.id));
    const matches = (req, work) => !query || [req?.text, req?.id, ...work.flatMap(card => [card.work.title,
      ...issuesFor(card).map(row => `${row.document.issue.repository} #${row.document.issue.number}`)])].join(' ').toLocaleLowerCase().includes(query);
    const displayed = requirements.filter(req => matches(req, linked(req)));
    for (const req of displayed) if (!order.includes(req.id)) order.push(req.id);
    displayed.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
    for (const req of displayed) {
      const work = linked(req), open = expanded.has(req.id), qualified = requirementReviews(plan, req, reviews);
      const toggle = h('button', {type: 'button', class: 'secondary pm-rtm-toggle', 'aria-expanded': String(open), 'data-rtm-toggle': req.id,
        'aria-label': t(open ? 'wb_collapse' : 'wb_open', {name: req.text}), onclick: () => {
          if (expanded.has(req.id)) expanded.delete(req.id); else expanded.add(req.id); renderTable();
        }}, open ? '−' : '+');
      const milestone = milestones.filter(row => row.plan_revision_id === plan.revision_id && row.requirement_ids?.includes(req.id));
      tableBody.append(h('tr', {'data-rtm-requirement': req.id}, h('th', {scope: 'row'}, toggle, h('span', {}, req.text), code(req.id)),
        h('td', {}, milestone.length ? milestone.map(row => row.title).join(' · ') : t('wb_no_milestone')),
        h('td', {}, String(work.length)), h('td', {}, t(qualified.length ? 'pq_qualified' : 'wb_not_assessed'))));
      if (open) {
        tableBody.append(h('tr', {class: 'pm-rtm-criteria'}, h('td', {colspan: 4},
          h('ul', {}, ...(req.acceptance || []).map(row => h('li', {}, row.text))),
          ...qualified.map(row => h('details', {}, h('summary', {}, t('pq_original')), code(row.review_id),
            h('p', {}, t('wb_approved_by', {actor: row.assessment.actor})), code(row.context.native_result?.candidate_commit))))));
        if (!work.length) tableBody.append(h('tr', {}, h('td', {colspan: 4, class: 'muted'}, t('wb_no_work'))));
        for (const card of work) tableBody.append(workRow(card));
      }
    }
    const unassigned = cards.filter(card => !requirements.some(req => card.binding.plan_revision_id === plan?.revision_id && card.binding.requirement_ids?.includes(req.id)))
      .filter(card => matches(null, [card]));
    const orphanIssues = issues.filter(row => !cards.some(card => row.work_item_id === card.work_item_id && row.document.relation?.binding_id === card.binding.binding_id))
      .filter(row => !query || `${row.document.selected?.public_title} ${row.document.issue?.repository} #${row.document.issue?.number}`.toLocaleLowerCase().includes(query));
    if (unassigned.length || orphanIssues.length) tableBody.append(h('tr', {class: 'pm-rtm-group'}, h('th', {scope: 'rowgroup', colspan: 4}, t('wb_unassigned'))),
      ...unassigned.map(workRow), ...orphanIssues.map(row => h('tr', {'data-rtm-unassigned-issue': row.mapping_id},
        h('td', {colspan: 4}, row.document.selected?.public_title || t('wb_work'), ' · ', issueLabel(row)))));
    if (!displayed.length && !unassigned.length && !orphanIssues.length) tableBody.append(h('tr', {}, h('td', {colspan: 4}, t(query ? 'wb_no_match' : 'wb_no_requirements'))));
    if (focused) tableBody.querySelector(`[${focused[0]}="${CSS.escape(focused[1])}"]`)?.focus({preventScroll: true});
  }
  const issueLabel = row => joinLabel([
    firstLabel(row.document.relation?.repository, row.document.issue?.repository),
    t('wb_issue', {number: row.document.issue.number})]);
  function workRow(card) {return h('tr', {'data-rtm-work': card.work_item_id}, h('td', {colspan: 2}, workButton(card),
    h('span', {class: 'muted'}, label('pm_kanban_state_', card.column))),
    h('td', {}, ...issuesFor(card).map(row => h('div', {}, issueLabel(row))), !issuesFor(card).length ? '—' : ''),
    h('td', {}, h('span', {class: 'muted'}, t('wb_details'))));}
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
    // Keep the reading position while reauthorizing; a failed read withdraws it all.
    const ticket = ++serial; detailSerial++; detailBusy = true;
    busy = true; controls();
    try {
      const accepted = await api('GET', `${endpoint}/plans/accepted`); alive(); if (ticket !== serial) return;
      if (accepted.head?.workspace_id !== workspaceId || accepted.plan && (accepted.plan.workspace_id !== workspaceId
        || accepted.plan.revision_id !== accepted.head.accepted_revision_id)) throw Error(t('pm_scope_mismatch'));
      const [work, mappings, quality, roadmap] = await Promise.all([catalogue('work', ticket),
        feature('issue_mapping') ? catalogue('issues', ticket) : null, feature('quality_reviews') ? catalogue('reviews', ticket) : null,
        feature('roadmap') ? api('GET', `${endpoint}/roadmap`) : null]);
      alive(); if (ticket !== serial) return;
      const end = await api('GET', `${endpoint}/plans/head`); alive(); if (ticket !== serial) return;
      if (end.head?.workspace_id !== workspaceId || end.head.accepted_revision_id !== accepted.head.accepted_revision_id) throw Error(t('wb_plan_changed'));
      work.rows.forEach(row => cardCheck(row));
      if (new Set(work.rows.map(row => row.work_item_id)).size !== work.rows.length) throw Error(t('pm_scope_mismatch'));
      for (const row of mappings?.rows || []) if (row.workspace_id !== workspaceId || row.document?.workspace_id !== workspaceId
        || row.document.work_item_id !== row.work_item_id || row.document.relation?.work_item_id !== row.work_item_id
        || !row.document.issue?.repository || !Number.isSafeInteger(row.document.issue.number)) throw Error(t('pm_scope_mismatch'));
      for (const row of quality?.rows || []) if (row.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
      if (roadmap) scope(roadmap);
      plan = accepted.plan; cards = work.rows; issues = mappings?.rows || []; reviews = quality?.rows || [];
      milestones = roadmap?.baseline?.document?.milestones || [];
      for (const [kind, data] of Object.entries({work, issues: mappings, reviews: quality})) cursors[kind] = data?.next ?? null;
      // Volatile flow ages are not table content and must not replace reading rows.
      const next = JSON.stringify([plan, cards.map(card => [card.work_item_id, card.binding, card.work.title, card.column]), issues, reviews, milestones]);
      if (next !== signature) {signature = next; renderTable();}
      count.textContent = t('wb_catalogue', {requirements: plan?.document?.requirements?.length || 0, work: cards.length});
      status.replaceChildren(...([work, mappings, quality].some(row => row?.filtered || row?.next != null) ? [h('p', {class: 'muted'}, t('wb_partial'))] : []));
      if (selected && cards.some(card => card.work_item_id === selected)) await select(selected, false, true);
      else {detailCard = null; deliveryRows = []; detail.replaceChildren(); detail.hidden = true; detailBusy = false; renderedDetail = '';}
      applyPending();
    } catch (error) {fail(error, ticket);} finally {if (ticket === serial) {busy = false; controls();}}
  }
  function deliveryCheck(data, id, after) {
    scope(data);
    if (data.work_item_id !== id || data.contract !== 'pm-engineering-delivery/v1' || !Array.isArray(data.deliveries)
      || data.requirement_acceptance !== 'not_evaluated_by_delivery'
      || data.next_after != null && (!Number.isSafeInteger(data.next_after) || data.next_after <= after)) throw Error(t('pm_scope_mismatch'));
    return data;
  }
  function renderDetail() {
    const card = detailCard; if (!card) return;
    const close = h('button', {type: 'button', class: 'secondary', onclick: () => {
      const id = selected;
      detailSerial++; selected = null; detailCard = null; deliveryRows = []; renderedDetail = ''; detail.replaceChildren(); detail.hidden = true; renderTable();
      tableBody.querySelector(`[data-rtm-select="${CSS.escape(id)}"]`)?.focus({preventScroll: true});
    }}, t('wb_close'));
    detail.replaceChildren(h('header', {class: 'pm-heading'}, h('h3', {tabindex: '-1'}, card.work.title), close),
      h('a', {href: `#/work/${encodeURIComponent(card.work_item_id)}`}, t('wb_open_work')),
      h('p', {}, label('pm_kanban_state_', card.column)), h('h4', {}, t('wb_version')), code(card.binding.plan_revision_id),
      card.binding.plan_revision_id === plan?.revision_id && plan.adoption ? h('details', {}, h('summary', {}, t('wb_approval')),
        h('p', {}, t('wb_approved_by', {actor: plan.adoption.adopted_by})), h('p', {}, time(plan.adoption.created_at)),
        h('p', {}, plan.adoption.rationale), code(plan.adoption.commit_sha)) : '',
      qualityFact({h, t, fact: card.quality?.selected_assessment ? card.quality : null, current: true}),
      h('h4', {}, t('wb_attempts')), ...['sessions', 'attempts', 'operations'].flatMap(kind => (card.execution?.[kind] || []).map(row =>
        h('p', {}, code(row.resource_id || row.attempt_id || row.operation_id), label('pm_kanban_state_', row.runtime_status || row.status), ' · ', time(row.observed_at)))),
      h('h4', {}, t('wb_delivery')));
    // PR grouping uses the original provider + numeric repository + pull number.
    const groups = new Map();
    for (const row of deliveryRows) {
      const target = row.target || {}, key = target.provider_origin && Number.isSafeInteger(target.repository_id) && Number.isSafeInteger(target.pull_number)
        ? JSON.stringify([target.provider_origin, target.repository_id, target.pull_number]) : row.operation_id;
      if (!groups.has(key)) groups.set(key, []); groups.get(key).push(row);
    }
    for (const rows of groups.values()) {
      const target = rows[0].target || {};
      detail.append(h('section', {class: 'pm-rtm-delivery', 'data-rtm-pr': target.pull_number || ''},
        h('h4', {}, target.pull_number ? `${target.repository} · PR #${target.pull_number}` : t('wb_delivery')),
        ...rows.map(row => h('div', {}, h('a', {href: `#/op/${encodeURIComponent(row.operation_id)}`}, label('wd_action_', row.action)),
          h('span', {class: 'muted'}, ' · ', label('wd_status_', row.status)),
          row.merge ? h('p', {}, t(row.merge.merged === true ? 'wb_merged' : 'wb_merge_unknown'), code(row.merge.merged_sha)) : '',
          row.integration ? h('p', {}, code(row.integration.new_head)) : '',
          row.deployment ? h('p', {}, row.deployment.environment, ' · ', t(row.deployment.verified === true ? 'wb_deployed' : 'wb_deployment_unknown'),
            ' · ', t(row.deployment.is_current === true ? 'wb_current_environment' : row.deployment.is_current === false ? 'wb_historical_deployment' : 'wb_unknown'),
            code(row.deployment.source_sha)) : ''))));
    }
    if (!deliveryRows.length) detail.append(h('p', {class: 'muted'}, t('wb_no_delivery')));
    if (deliveryCursor !== null) detail.append(h('button', {type: 'button', class: 'secondary', disabled: detailBusy, onclick: () => void moreDeliveries()}, t('wb_receipts_more')));
    detail.append(h('details', {}, h('summary', {}, t('wb_sources')), h('pre', {class: 'pm-brief-proof'}, JSON.stringify({binding: card.binding,
      coverage: card.execution?.coverage, capture: card.latest_capture, issues: issuesFor(card), deliveries: deliveryRows}, null, 2))));
    renderedDetail = detailSignature();
  }
  async function select(id, focus = false, retain = false) {
    const card = cards.find(row => row.work_item_id === id); if (!card) return;
    if (selected !== id) deliveryPages = 1;
    const previous = retain ? renderedDetail : null;
    const ticket = ++detailSerial, generation = serial; selected = id; detailBusy = true;
    if (!retain) {
      detailCard = null; deliveryRows = []; deliveryCursor = null; renderedDetail = '';
      detail.hidden = false; detail.replaceChildren(h('p', {role: 'status'}, t('wb_detail_loading'))); renderTable();
    }
    try {
      const [data, deliveries] = await Promise.all([api('GET', `${endpoint}/kanban/${id}`), feature('engineering_delivery')
        ? readDeliveries(id, ticket, generation) : null]);
      alive(); if (ticket !== detailSerial || generation !== serial) return;
      scope(data); cardCheck(data.card, id);
      if (data.card.binding.binding_id !== card.binding.binding_id) throw Error(t('wb_plan_changed'));
      detailCard = data.card;
      deliveryRows = deliveries?.deliveries || []; deliveryCursor = deliveries?.next_after ?? null;
      detailBusy = false; detail.hidden = false;
      if (previous !== detailSignature()) renderDetail();
      detail.querySelector('[data-rtm-filtered]')?.remove();
      if (deliveries?.source_visibility_filtered) detail.append(h('p', {class: 'muted', 'data-rtm-filtered': ''}, t('wb_partial')));
      if (focus) detail.querySelector('h3')?.focus({preventScroll: true});
    } catch (error) {if (ticket === detailSerial) fail(error, generation);}
  }
  async function readDeliveries(id, ticket, generation) {
    let after = 0, next = null, filtered = false; const rows = new Map();
    // Re-read prior pages as well: an earlier receipt may have lost source access.
    for (let page = 0; page < deliveryPages; page++) {
      const data = deliveryCheck(await api('GET', `${endpoint}/work/${id}/deliveries?after=${after}&limit=20`), id, after);
      alive(); if (ticket !== detailSerial || generation !== serial) return null;
      for (const row of data.deliveries) rows.set(row.operation_id, row);
      filtered ||= Boolean(data.source_visibility_filtered); next = data.next_after ?? null;
      if (next === null) break; after = next;
    }
    return {deliveries: [...rows.values()], next_after: next, source_visibility_filtered: filtered};
  }
  async function moreDeliveries() {
    if (detailBusy || deliveryCursor === null || !selected) return;
    deliveryPages++; await select(selected);
  }
  let pendingFocus = null;
  async function activate() {if (!active) {active = true; await refresh();}}
  function focusWork(id) {pendingFocus = {kind: 'work', id}; if (!active) return activate(); if (!busy) applyPending();}
  function focusRequirement(id) {pendingFocus = {kind: 'requirement', id}; if (!active) return activate(); if (!busy) applyPending();}
  function applyPending() {
    const pending = pendingFocus;
    if (!pending || !plan) return;
    if (pending.kind === 'work') {
      const card = cards.find(row => row.work_item_id === pending.id);
      if (!card) {pendingFocus = null; return;}
      pendingFocus = null;
      for (const id of card.binding?.requirement_ids || []) expanded.add(id);
      renderTable();
      tableBody.querySelector(`[data-rtm-work="${CSS.escape(pending.id)}"]`)?.scrollIntoView({block: 'nearest'});
      void select(pending.id, true);
      return;
    }
    const requirements = plan.document?.requirements || [];
    if (!requirements.some(row => row.id === pending.id)) {pendingFocus = null; return;}
    pendingFocus = null;
    expanded.add(pending.id);
    renderTable();
    tableBody.querySelector(`[data-rtm-requirement="${CSS.escape(pending.id)}"]`)?.scrollIntoView({block: 'nearest'});
  }
  controls(); return {element, refresh, withdraw, focusWork, focusRequirement, dispose: () => {Object.assign(preference, {selected, expanded: [...expanded], filter: filter.value}); withdraw();}, activate};
}
