import {planDiffText} from './pm-plan-diff-strings.js';

// Pure reads of fixed plan/binding IDs. This component never changes work or plans.
export function planComparison({h, t, api, alive, workspaceId, errorBox}) {
  const text = (key, values) => planDiffText(t, key, values), endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  const plans = new Map(), bindings = new Map(), selected = new Set();
  let request = 0, busy = false, cursor = null, planAfter = null, bindingAfter = null, bindingLoaded = false, bindingBusy = false;
  const from = h('select', {'aria-label': text('pm_diff_from')}), to = h('select', {'aria-label': text('pm_diff_to')});
  const status = h('div', {role: 'status'}), result = h('div', {'data-pm-diff-result': ''});
  const bindingRows = h('div'), selection = h('p', {class: 'muted'}), bindingStatus = h('div', {role: 'status'});
  const label = (key, control) => h('label', {}, text(key), control);
  const button = h('button', {type: 'button', class: 'secondary', disabled: true, onclick: () => void read()}, text('pm_diff_compare'));
  const morePlans = h('button', {type: 'button', class: 'secondary', hidden: true, onclick: () => void loadPlans()}, text('pm_diff_more_versions'));
  const loadBindings = h('button', {type: 'button', class: 'secondary', onclick: () => void refreshBindings(bindingAfter === null)}, text('pm_diff_load_bindings'));
  const workPicker = h('details', {'data-pm-diff-bindings': ''}, h('summary', {}, text('pm_diff_bindings')),
    h('p', {class: 'muted'}, text('pm_diff_binding_help')), bindingStatus, bindingRows, selection, loadBindings);
  const box = h('section', {class: 'pm-packages pm-plan-comparison', 'data-pm-plan-diff': ''}, h('h3', {}, text('pm_diff_title')),
    h('p', {class: 'muted'}, text('pm_diff_intro')), h('div', {class: 'capture-fields'}, label('pm_diff_from', from), label('pm_diff_to', to)),
    morePlans, workPicker, h('div', {class: 'actions'}, button), status, result);
  const valid = page => {if (page?.workspace_id !== workspaceId) throw Error(text('pm_diff_invalid')); return page;};
  const raw = value => value === null || value === undefined ? text('pm_diff_unknown') : typeof value === 'object' ? JSON.stringify(value) : String(value);
  const detail = (heading, ...children) => h('details', {}, h('summary', {}, heading), ...children);
  function controls() {
    button.disabled = busy || !from.value || !to.value;
    button.textContent = text(busy ? 'pm_diff_reading' : cursor === null ? 'pm_diff_compare' : 'pm_diff_recheck');
    selection.textContent = text('pm_diff_binding_count', {count: selected.size});
    bindingRows.querySelectorAll('input').forEach(input => {input.disabled = selected.size >= 100 && !input.checked;});
  }
  function changed() {request++; busy = false; cursor = null; result.replaceChildren(); status.replaceChildren(); controls();}
  from.addEventListener('change', changed); to.addEventListener('change', changed);
  workPicker.addEventListener('toggle', () => {if (workPicker.open && !bindingLoaded) void refreshBindings();});
  function options() {
    for (const control of [from, to]) {
      const old = control.value;
      control.replaceChildren(h('option', {value: ''}, text('pm_diff_choose')),
        ...[...plans.values()].sort((a, b) => a.sequence - b.sequence).map(plan =>
          h('option', {value: plan.revision_id}, t('pm_revision_number', {number: plan.sequence}), ' · ', plan.revision_id)));
      if (plans.has(old)) control.value = old;
    }
    morePlans.hidden = planAfter === null;
    if (!plans.size) status.replaceChildren(h('p', {class: 'muted'}, text('pm_diff_missing_versions')));
    controls();
  }
  function update(page, workPage) {
    valid(page);
    for (const plan of page.revisions || []) plans.set(plan.revision_id, plan);
    planAfter = page.next_cursor === null || page.next_cursor === undefined ? null
      : Math.max(page.next_cursor, ...[...plans.values()].map(plan => plan.sequence)); options();
    if (workPage) showBindings(valid(workPage));
  }
  async function loadPlans() {
    if (morePlans.disabled || planAfter === null) return;
    morePlans.disabled = true;
    try {const page = await api('GET', `${endpoint}/plans?after=${encodeURIComponent(planAfter)}&limit=100`); alive(); if (box.isConnected) update(page);}
    catch (error) {if (box.isConnected) status.replaceChildren(errorBox(error));}
    finally {morePlans.disabled = false;}
  }
  function showBindings(page) {
    bindingLoaded = true; bindingAfter = page.next_cursor ?? null;
    for (const value of page.bindings || []) bindings.set(value.binding_id, value);
    bindingRows.replaceChildren(...[...bindings.values()].map(value => {
      const id = `pm-diff-${workspaceId}-${value.binding_id}`;
      const input = h('input', {id, type: 'checkbox', checked: selected.has(value.binding_id), 'data-pm-diff-binding': value.binding_id,
        onchange: () => {if (input.checked) selected.add(value.binding_id); else selected.delete(value.binding_id); changed();}});
      return h('div', {class: 'pm-fact'}, h('div', {class: 'pm-fact-heading'}, input, h('label', {for: id, class: 'pm-source'}, value.work_item_id)),
        h('p', {class: 'muted pm-source'}, text('pm_diff_bound'), ': ', value.plan_revision_id || text('pm_diff_no_plan')),
        h('small', {class: 'muted pm-source'}, value.binding_id));
    }));
    if (!bindings.size) bindingRows.append(h('p', {class: 'muted'}, text('pm_diff_no_bindings')));
    bindingStatus.replaceChildren(...(bindingAfter !== null ? [h('p', {class: 'muted'}, text('pm_diff_partial'))] : []));
    loadBindings.hidden = false; loadBindings.textContent = text(bindingAfter === null ? 'pm_diff_refresh_bindings' : 'pm_diff_more_bindings'); controls();
  }
  async function refreshBindings(reset = true) {
    if (bindingBusy) return;
    bindingBusy = true; loadBindings.disabled = true;
    try {
      const after = reset ? null : bindingAfter;
      const page = valid(await api('GET', `${endpoint}/work?limit=100${after === null ? '' : '&after=' + encodeURIComponent(after)}`));
      alive(); if (box.isConnected) showBindings(page);
    } catch (error) {if (box.isConnected) {bindingStatus.replaceChildren(errorBox(error)); loadBindings.hidden = false;}}
    finally {bindingBusy = false; loadBindings.disabled = false;}
  }
  function source(input) {
    return h('div', {class: 'pm-fact pm-source'}, h('span', {class: 'chip'}, t('pm_status_' + input.status)),
      h('p', {class: 'pm-note-text'}, input.value), h('p', {class: 'muted'}, text('pm_diff_selected_source'), ': ', input.input_id),
      h('p', {class: 'muted'}, text('pm_diff_source'), ': ', raw(input.source_ref)),
      h('p', {class: 'muted'}, input.confirmed_by ? text('pm_diff_confirmed_by') + ': ' + input.confirmed_by : text('pm_diff_not_confirmed')));
  }
  function requirement(item) {
    return h('div', {class: 'pm-source'}, h('p', {class: 'pm-note-text'}, item.text),
      h('p', {class: 'muted'}, t('pm_status_' + item.source_status), ' · ', text('pm_diff_selected_source'), ': ', item.source_input_id),
      h('p', {class: 'muted'}, text('pm_diff_priority'), ': ', raw(item.priority)),
      h('p', {class: 'muted'}, text('pm_diff_dependencies'), ': ', raw(item.depends_on)),
      h('h4', {}, text('pm_diff_acceptance')), ...(item.acceptance?.length ? item.acceptance.map(acceptance =>
        h('p', {}, acceptance.text, ' · ', t('pm_status_' + acceptance.source_status), h('small', {class: 'muted pm-source'}, ' · ' + acceptance.source_input_id)))
        : [h('p', {class: 'muted'}, text('pm_diff_no_acceptance'))]));
  }
  function render(value) {
    const changes = value.changes, req = changes.requirements, reqRows = [];
    for (const kind of ['added', 'removed', 'changed']) for (const item of req[kind] || []) {
      reqRows.push(detail(text('pm_diff_' + kind) + ' · ' + item.id,
        ...(kind === 'changed' ? [h('h4', {}, text('pm_diff_before')), requirement(item.before), h('h4', {}, text('pm_diff_after')), requirement(item.after)] : [requirement(item)])));
    }
    const sources = changes.sources, sourceRows = [];
    for (const kind of ['added', 'removed', 'changed', 'unchanged']) for (const item of sources[kind] || []) {
      sourceRows.push(detail((kind === 'unchanged' ? text('pm_diff_domain_same') : text('pm_diff_' + kind)) + ' · ' + item.input_id,
        ...(kind === 'changed' ? [h('h4', {}, text('pm_diff_before')), source(item.before), h('h4', {}, text('pm_diff_after')), source(item.after)] : [source(item)])));
    }
    const domains = detail(text('pm_diff_domains'));
    for (const [name, value] of Object.entries(changes.domains)) {
      const row = detail(t('pm_slot_' + name) + ' · ' + text(value.changed ? 'pm_diff_changed' : 'pm_diff_domain_same'));
      for (const side of ['before', 'after']) row.append(h('h4', {}, text('pm_diff_' + side)),
        ...(value[side].statements?.length ? value[side].statements.map(source) : [h('p', {class: 'muted'}, text('pm_diff_unknown'))]));
      domains.append(row);
    }
    const prose = detail(text('pm_diff_prose'));
    if (changes.title.changed) prose.append(h('p', {}, text('pm_diff_old_title'), ': ', changes.title.before), h('p', {}, text('pm_diff_new_title'), ': ', changes.title.after));
    if (changes.markdown.changed || changes.title.changed) prose.append(h('p', {class: 'muted'}, text('pm_diff_prose_unknown')));
    else prose.append(h('p', {class: 'muted'}, text('pm_diff_no_prose')));
    if (changes.markdown.replacement) prose.append(h('h4', {}, text('pm_diff_removed')), h('div', {class: 'pm-plan-document'}, changes.markdown.replacement.removed),
      h('h4', {}, text('pm_diff_added')), h('div', {class: 'pm-plan-document'}, changes.markdown.replacement.added));
    const work = h('div', {}, h('h4', {}, text('pm_diff_impact')));
    if (!value.impact_candidates.length) work.append(h('p', {class: 'muted'}, text('pm_diff_no_impact_selection')));
    for (const candidate of value.impact_candidates) work.append(detail(candidate.binding.work_item_id + ' · ' + text(candidate.assessment === 'review_candidate' ? 'pm_diff_candidate' : 'pm_diff_no_identified'),
      h('p', {class: 'muted'}, text('pm_diff_work_unknown')),
      h('p', {}, text('pm_diff_work_version', {version: candidate.binding.source_work_version})),
      h('p', {class: 'pm-source'}, text('pm_diff_bound'), ': ', candidate.binding.plan_revision_id || text('pm_diff_no_plan')),
      h('p', {class: 'pm-source'}, candidate.binding.binding_id),
      ...candidate.reasons.map(reason => h('p', {}, text('pm_diff_reason_' + reason))),
      h('p', {class: 'pm-source'}, text('pm_diff_requirements'), ': ', raw(candidate.binding.requirement_ids)),
      h('a', {href: `#/item/${encodeURIComponent(candidate.binding.work_item_id)}`}, text('pm_diff_open_work'))));
    const fixed = detail(text('pm_diff_evidence'), h('p', {class: 'pm-source'}, text('pm_diff_from'), ': ', value.from_revision.revision_id),
      h('p', {class: 'pm-source'}, text('pm_diff_to'), ': ', value.to_revision.revision_id),
      h('p', {class: 'pm-source'}, text('pm_diff_cursor'), ': ', value.context.journal_cursor),
      ...[['pm_diff_from_digest', value.from_revision.document_digest], ['pm_diff_to_digest', value.to_revision.document_digest],
        ['pm_diff_from_scope_digest', value.from_revision.structured_scope_digest], ['pm_diff_to_scope_digest', value.to_revision.structured_scope_digest],
        ['pm_diff_comparison_digest', value.comparison_digest], ['pm_diff_context_digest', value.context_digest]]
        .map(([key, digest]) => h('p', {class: 'pm-source'}, text(key), ': ', raw(digest))));
    result.replaceChildren(h('p', {class: 'muted'}, text('pm_diff_no_scope_decision')),
      h('p', {class: 'muted'}, text(value.scope?.structured_digest_changed === true ? 'pm_diff_scope_changed'
        : value.scope?.structured_digest_changed === false ? 'pm_diff_scope_same' : 'pm_diff_scope_unknown')),
      h('h4', {}, text('pm_diff_requirements')),
      ...(reqRows.length ? reqRows : [h('p', {class: 'muted'}, text('pm_diff_no_requirements'))]),
      ...(req.order_changed ? [h('p', {class: 'muted'}, text('pm_diff_order'))] : []), domains, prose,
      detail(text('pm_diff_sources'), ...sourceRows, h('p', {class: 'pm-source'}, text('pm_diff_unclassified'), ': ', raw(changes.unclassified_sources))), work, fixed);
  }
  async function read() {
    if (busy || !from.value || !to.value) return;
    busy = true; const ticket = ++request, left = from.value, right = to.value, ids = [...selected].sort(), fixedCursor = cursor;
    controls(); result.replaceChildren(); status.replaceChildren(h('p', {class: 'muted'}, text('pm_diff_loading')));
    const query = new URLSearchParams({from_revision_id: left, to_revision_id: right});
    if (ids.length) query.set('binding_ids', ids.join(','));
    if (fixedCursor !== null) query.set('journal_cursor', String(fixedCursor));
    try {
      const data = await api('GET', `${endpoint}/plan-comparison?${query}`); alive();
      if (!box.isConnected || ticket !== request) return;
      const value = valid(data).comparison;
      if (value?.from_revision?.revision_id !== left || value?.to_revision?.revision_id !== right
          || JSON.stringify(value.context?.binding_ids) !== JSON.stringify(ids)
          || !Array.isArray(value.impact_candidates) || value.impact_candidates.length !== ids.length
          || value.impact_candidates.some(candidate => !ids.includes(candidate.binding?.binding_id)
            || candidate.binding?.workspace_id !== workspaceId)
          || new Set(value.impact_candidates.map(candidate => candidate.binding.binding_id)).size !== ids.length
          || !Number.isSafeInteger(value.context?.journal_cursor) || value.context.journal_cursor < 0
          || fixedCursor !== null && value.context.journal_cursor !== fixedCursor) throw Error(text('pm_diff_invalid'));
      render(value); cursor = value.context.journal_cursor;
      status.replaceChildren(h('p', {class: 'muted'}, text('pm_diff_fixed')));
    } catch (error) {if (box.isConnected && ticket === request) {result.replaceChildren(); status.replaceChildren(errorBox(error), h('p', {class: 'muted'}, text('pm_diff_failure')));}}
    finally {if (ticket === request) {busy = false; controls();}}
  }
  options();
  return {box, update, refreshBindings, select(left, right) {
    from.value = plans.has(left) ? left : '';
    to.value = plans.has(right) && right !== left ? right : [...plans.keys()].find(id => id !== left) || '';
    changed(); to.focus();
  }};
}
