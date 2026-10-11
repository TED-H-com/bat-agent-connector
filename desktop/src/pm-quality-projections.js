// Preserve selected original identities in durable forms and frozen projections.
export function qualityFact({h,t,fact,current=false}) {
  if (!fact) return '';
  const label = value => t('pq_' + (['accepted','rejected','unassessed','not_defined'].includes(value) ? value : 'unassessed'));
  const reference = fact.reference || fact.selected_assessment?.reference;
  return h('div', {'data-pm-quality-fact': '', class: 'pm-note-text'},
    h('p', {}, h('strong', {}, t(fact.qualified ? 'pq_qualified' : 'pq_not_qualified'))),
    h('p', {}, t('pq_acceptance'), ': ', label(fact.acceptance || fact.acceptance_criteria), ' · ', t('pq_dod'), ': ', label(fact.dod)),
    !current ? h('p', {class: 'muted'}, t('pq_fixed')) : '',
    fact.assessed_by ? h('p', {class: 'muted'}, t('pq_recorded_by', {actor: fact.assessed_by})) : '',
    reference ? h('details', {}, h('summary', {}, t('pq_original')),
      h('code', {class: 'pm-source'}, reference.candidate_commit), h('p', {}, h('code', {class: 'pm-source'}, reference.review_id)),
      h('p', {}, h('code', {class: 'pm-source'}, reference.operation_id)), h('p', {}, h('code', {class: 'pm-source'}, reference.document_digest))) : '');
}

export function fixedQualityPicker({h,t,api,alive,workspaceId,workItemId,readSelection,field,multiple=false,constraints={},errorBox}) {
  let editor = null, pending = false;
  const status = h('div', {role: 'status'}), chosen = h('p', {class: 'muted'});
  const read = () => {const value = JSON.parse(field.value || (multiple ? '{}' : 'null')); return multiple ? value[workItemId] : value;};
  function write(value) {
    if (!editor || editor.locked()) return;
    const values = multiple ? JSON.parse(field.value || '{}') : null;
    if (multiple) {if (value) values[workItemId] = value; else delete values[workItemId];}
    field.value = JSON.stringify(multiple ? values : value); editor.changed(); update();
  }
  const use = h('button', {type: 'button', class: 'secondary', onclick: async () => {
    if (!editor || editor.locked() || pending) return;
    const selected = readSelection(workItemId);
    if (!selected) {status.replaceChildren(h('p', {}, t('pq_choose_help'))); return;}
    pending = true; update(); status.replaceChildren();
    try {
      const data = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/quality-reviews/${encodeURIComponent(selected.review_id)}`); alive();
      if (!element.isConnected || editor.locked()) return;
      const row = data.review, context = row?.context, assessment = row?.assessment;
      if (data.workspace_id !== workspaceId || row?.review_id !== selected.review_id || row.qualified !== true
          || context?.work_ref?.work_item_id !== workItemId || context?.native_result?.candidate_commit !== selected.candidate_commit
          || ['operation_id','version','document_digest'].some(key => assessment?.[key] !== selected[key])
          || constraints.binding_id && context.work_ref.binding_id !== constraints.binding_id
          || constraints.dod_revision_id && context.dod_ref?.revision_id !== constraints.dod_revision_id) throw Error(t('pq_changed'));
      write(selected);
    } catch (error) {if (element.isConnected) status.replaceChildren(errorBox(error));}
    finally {pending = false; update();}
  }}, t('pq_include'));
  const clear = h('button', {type: 'button', class: 'secondary', onclick: () => write(null)}, t('pq_clear'));
  const element = h('div', {'data-pm-quality-picker': workItemId}, chosen, h('div', {class: 'actions'}, use, clear), status);
  function update() {
    const reference = read();
    chosen.replaceChildren(reference ? h('code', {class: 'pm-source'}, reference.candidate_commit) : t('pq_none'));
    use.disabled = !editor || editor.locked() || pending;
    clear.disabled = !editor || editor.locked() || pending || !reference;
  }
  update();
  return {element,update,attach: value => {editor=value;update();}};
}
