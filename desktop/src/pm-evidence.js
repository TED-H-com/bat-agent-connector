// Work stays in the Connector; this view preserves explicit PM associations and saved evidence.
const flatten = rows => (rows || []).flatMap(row => [row, ...flatten(row.children)]);
// Retain only explicit selection IDs across a same-principal access revalidation.
// The catalog, associations and evidence are always read again before displaying them.
const projectSelections = new Map();
export function workspaceEvidence({h, t, api, alive, namespace, workspaceId, workspace, permitted, form, input, labelled, errorBox}) {
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  const selectionKey = namespace ? JSON.stringify([namespace, workspaceId]) : null;
  let loaded = false, generation = 0, projects = [], bindings = [], workBindings = [], selected = projectSelections.get(selectionKey) || '', active = null;
  const rows = h('div', {class: 'pm-evidence-rows'}), status = h('div', {role: 'status'});
  const choose = h('select', {'aria-label': t('pm_evidence_project'), onchange: () => {
    alive(); selected = choose.value;
    if (selectionKey) {
      projectSelections.delete(selectionKey);
      if (selected) projectSelections.set(selectionKey, selected);
      if (projectSelections.size > 40) projectSelections.delete(projectSelections.keys().next().value);
    }
    void loadProject();
  }});
  const box = h('section', {class: 'panel pm-evidence', 'data-pm-evidence': ''}, h('h2', {}, t('pm_evidence_title')),
    h('p', {class: 'muted'}, t('pm_evidence_intro')), labelled('pm_evidence_project', choose), status, rows);
  const stateText = value => {const key = 'pm_evidence_state_' + value, translated = t(key); return translated === key ? String(value) : translated;};
  const sourceTime = value => typeof value === 'number' ? new Date(value * 1000).toLocaleString() : t('pm_evidence_time_unknown');
  const verify = data => {if (data.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch')); return data;};
  const can = decide => loaded && permitted(decide);
  const forms = new Set();
  const track = value => {forms.add(value); return value;};
  function updateForms() {
    // The action handler uses the same read gate. A still-enabled button would
    // silently discard a click during an event refresh; keep the draft intact.
    for (const value of forms) {
      if (!value.box.isConnected) forms.delete(value);
      else value.update();
    }
  }
  function bindingForm(action, target, preconditions, label, extraParams = {}, alreadyBound = false) {
    const rationale = input('pm_evidence_rationale', {max: 4000});
    let result;
    result = track(form({action, target: {workspace_id: workspaceId, ...target}, fields: {rationale},
      params: values => ({rationale: values.rationale, ...extraParams}), preconditions,
      permitted: () => can(true) && !alreadyBound, recoverPermitted: () => can(true), label, onSaved: () => refresh(),
      onStateChange: () => {if (result) result.box.hidden = alreadyBound && !result.locked();},
      extra: labelled('pm_evidence_rationale', rationale)}));
    result.update(); return result;
  }
  async function loadEvidence(item, holder) {
    const workId = item.work_item_id;
    const last = await api('GET', `${endpoint}/work/${workId}/capture`);
    alive(); verify(last); if (!holder.isConnected) return;
    holder.replaceChildren();
    if (!last.capture) {holder.append(h('p', {class: 'muted'}, t('pm_evidence_empty'))); return;}
    if (last.capture.work_item_id !== workId) throw Error(t('pm_scope_mismatch'));
    const capture = last.capture, coverage = capture.coverage;
    holder.append(h('p', {}, t('pm_evidence_saved_at', {time: sourceTime(capture.captured_at)})),
      h('p', {class: 'muted'}, t('pm_evidence_coverage', {count: coverage.links_examined,
        missing: coverage.unavailable_sources + coverage.unsupported_sources})),
      h('p', {class: 'muted'}, t('pm_evidence_quality_unknown')));
    if (coverage.truncated || coverage.inaccessible_sources) holder.append(h('p', {class: 'muted'}, t('pm_partial')));
    const facts = h('div'); holder.append(facts);
    function render(page) {
      for (const evidence of page.evidence || []) {
        const body = h('div'), row = h('details', {'data-pm-fact': evidence.evidence_id},
          h('summary', {}, t('pm_evidence_kind_' + evidence.source_kind), ' · ', sourceTime(evidence.source_observed_at)), body);
        let fetched = false, busy = false;
        row.addEventListener('toggle', async () => {
          if (!row.open || fetched || busy) return;
          busy = true;
          try {
            const detail = await api('GET', `${endpoint}/evidence/${evidence.evidence_id}`); alive(); if (!row.isConnected) return;
            if (detail.evidence.workspace_id !== workspaceId || detail.evidence.evidence_id !== evidence.evidence_id) throw Error(t('pm_scope_mismatch'));
            const fact = detail.evidence.document.facts;
            const visible = ['state', 'status', 'head_sha', 'merged_sha', 'version', 'deployment', 'verification'];
            const brief = visible.filter(key => fact[key] !== null && fact[key] !== undefined);
            body.replaceChildren(h('dl', {class: 'pm-evidence-fields'}, ...brief.flatMap(key => [h('dt', {}, t('pm_evidence_field_' + key)),
              h('dd', {}, ['state', 'status'].includes(key) ? stateText(fact[key]) : String(fact[key]))])),
              h('details', {class: 'pm-evidence-original'}, h('summary', {}, t('pm_evidence_original')),
                h('dl', {class: 'pm-evidence-fields'}, ...Object.entries(fact).flatMap(([key, value]) =>
                  [h('dt', {}, key), h('dd', {}, value === null ? t('pm_evidence_unknown') : typeof value === 'object' ? JSON.stringify(value) : String(value))]))),
              h('small', {class: 'muted'}, t('pm_evidence_saved_at', {time: sourceTime(detail.evidence.captured_at)})));
            fetched = true;
          } catch (error) {if (row.isConnected) body.replaceChildren(errorBox(error));} finally {busy = false;}
        }); facts.append(row);
      }
    }
    render({evidence: capture.evidence || []});
  }
  function workRow(item) {
    const binding = workBindings.find(row => row.work_item_id === item.work_item_id);
    const detail = h('div'), row = h('details', {'data-pm-work': item.work_item_id},
      h('summary', {}, item.title, h('small', {class: 'muted'}, ' · ' + t(binding ? 'pm_evidence_associated' : 'pm_evidence_unassociated'))), detail);
    let opened = false;
    row.addEventListener('toggle', async () => {
      if (!row.open || opened) return; opened = true;
      detail.append(h('a', {href: `#/work/${item.work_item_id}`}, t('pm_evidence_open_work')));
      if (can(true)) {
        const editor = h('details', {}, h('summary', {}, t(binding ? 'pm_evidence_reassociate' : 'pm_evidence_associate_work')));
        detail.append(editor);
        try {
          const accepted = await api('GET', `${endpoint}/plans/accepted`); alive(); if (!row.isConnected) return;
          if (accepted.head.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
          const plan = accepted.plan, rationale = input('pm_evidence_rationale', {max: 4000});
          const planId = h('select', {'aria-label': t('pm_evidence_plan_choice')}, h('option', {value: ''}, t('pm_evidence_no_plan_choice')));
          if (plan) planId.append(h('option', {value: plan.revision_id}, plan.document.title));
          if (binding?.plan_revision_id && binding.plan_revision_id !== plan?.revision_id)
            planId.append(h('option', {value: binding.plan_revision_id}, t('pm_evidence_keep_plan')));
          planId.value = binding?.plan_revision_id || '';
          const selectedIds = h('input', {type: 'hidden', value: JSON.stringify(binding?.requirement_ids || [])});
          const choices = h('div', {class: 'pm-evidence-requirements'});
          let bind;
          const showRequirements = () => {
            const ids = JSON.parse(selectedIds.value || '[]');
            choices.replaceChildren();
            if (planId.value === plan?.revision_id) for (const requirement of plan.document.requirements || []) {
              const checkbox = h('input', {type: 'checkbox', checked: ids.includes(requirement.id), disabled: bind?.locked() || !can(true)});
              checkbox.addEventListener('change', () => {
                if (bind.locked()) return;
                const current = new Set(JSON.parse(selectedIds.value || '[]'));
                if (checkbox.checked) current.add(requirement.id); else current.delete(requirement.id);
                selectedIds.value = JSON.stringify([...current]); bind.changed();
              }); choices.append(h('label', {}, checkbox, requirement.text));
            }
          };
          bind = track(form({action: 'pm.work.bind', target: {workspace_id: workspaceId, work_item_id: item.work_item_id},
            fields: {rationale, plan_revision_id: planId, requirement_ids: selectedIds},
            params: values => ({rationale: values.rationale, plan_revision_id: values.plan_revision_id || null,
              requirement_ids: values.plan_revision_id ? JSON.parse(values.requirement_ids || '[]') : []}),
            preconditions: () => ({expected_work_version: item.version, expected_binding_id: binding?.binding_id || null}),
            permitted: () => can(true), label: 'pm_evidence_associate_work', onSaved: () => refresh(),
            onStateChange: showRequirements,
            extra: h('div', {}, labelled('pm_evidence_rationale', rationale), labelled('pm_evidence_plan_choice', planId), selectedIds, choices)}));
          planId.addEventListener('change', () => {if (bind.locked()) return; selectedIds.value = '[]'; bind.changed(); showRequirements();});
          editor.append(bind.box); showRequirements();
        } catch (error) {if (row.isConnected) editor.append(errorBox(error));}
      }
      if (!binding) return;
      detail.append(h('p', {class: 'muted'}, t(binding.plan_revision_id ? 'pm_evidence_fixed_plan' : 'pm_evidence_no_plan')));
      if (binding.plan_revision_id) detail.append(h('code', {}, binding.plan_revision_id),
        h('p', {}, (binding.requirement_ids || []).join(', ')));
      const holder = h('div');
      const capture = track(form({action: 'pm.evidence.capture', target: {workspace_id: workspaceId, work_item_id: item.work_item_id},
        fields: {}, params: () => ({}), preconditions: () => ({expected_binding_id: binding.binding_id}),
        permitted: () => can(false), label: 'pm_evidence_capture', onSaved: () => loadEvidence(item, holder)}));
      detail.append(capture.box, holder);
      try {await loadEvidence(item, holder);} catch (error) {if (holder.isConnected) holder.replaceChildren(errorBox(error));}
    }); return row;
  }
  async function loadProject() {
    const ticket = ++generation; loaded = false; updateForms(); status.replaceChildren();
    if (!selected) {rows.replaceChildren(); active = null; return;}
    try {
      const data = await api('GET', `/projects/${encodeURIComponent(selected)}`); alive(); if (ticket !== generation) return;
      if (data.project?.project_id !== selected) throw Error(t('pm_scope_mismatch'));
      const signature = JSON.stringify([data, bindings, workBindings]);
      loaded = true; if (active?.signature === signature) {updateForms(); return;}
      active = {signature}; rows.replaceChildren();
      const bound = bindings.some(row => row.project_id === selected);
      if (can(true)) rows.append(bindingForm('pm.project.bind', {project_id: selected},
        () => ({expected_workspace_version: workspace().version, expected_project_version: data.project.version}), 'pm_evidence_associate_project', {}, bound).box);
      if (!bound) {
        rows.append(h('p', {class: 'muted'}, t('pm_evidence_bind_help')));
      } else {
        rows.append(...flatten(data.work_items).map(workRow));
        if (!data.work_items?.length) rows.append(h('p', {class: 'muted'}, t('pm_evidence_no_work')));
      }
    } catch (error) {if (ticket === generation) status.replaceChildren(errorBox(error));}
  }
  async function allBindings(suffix) {
    let cursor = 0, records = [];
    for (let page = 0; page < 20; page++) {
      const data = verify(await api('GET', `${endpoint}/${suffix}?after=${cursor}&limit=100`)); alive();
      records.push(...data.bindings); if (data.next_cursor === null) return records;
      if (data.next_cursor <= cursor) throw Error(t('pm_stale')); cursor = data.next_cursor;
    }
    throw Error(t('pm_partial'));
  }
  async function refresh() {
    const ticket = ++generation; loaded = false; updateForms();
    try {
      const [catalog, associated, work] = await Promise.all([api('GET', '/projects'), allBindings('projects'), allBindings('work')]); alive();
      if (ticket !== generation) return;
      projects = flatten(catalog.projects); bindings = associated; workBindings = work;
      choose.replaceChildren(h('option', {value: ''}, t('pm_evidence_choose')),
        ...projects.map(project => h('option', {value: project.project_id}, project.name)));
      choose.value = projects.some(project => project.project_id === selected) ? selected : '';
      selected = choose.value;
      await loadProject();
    } catch (error) {if (ticket === generation) status.replaceChildren(errorBox(error));}
  }
  return {box, refresh};
}
