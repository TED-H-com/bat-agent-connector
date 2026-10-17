import {retiredView, viewLeft} from './pm-retired.js';
// A PM worktree uses the existing repository preview and exact published SHA.
export function pmWorktreeChoice({h, t, api, alive, input, labelled, capability, scopes, editable, errorBox}) {
  let form = null, reading = false, sequence = 0;
  const mode = h('select', {'aria-label': t('pm_context_location')});
  const source = input('pub_ref', {max: 211, required: false, value: 'main'});
  const selection = h('input', {type: 'hidden', value: ''});
  const proof = h('div', {role: 'status'});
  const preview = h('button', {type: 'button', class: 'secondary', onclick: async () => {
    if (!form || form.locked() || reading || !mode.value || mode.value === 'owned') return;
    const ticket = ++sequence, target = JSON.parse(mode.value);
    const branch = source.value.startsWith('refs/') ? source.value : 'refs/heads/' + source.value;
    reading = true; update(); proof.replaceChildren();
    try {
      const data = await api('POST', '/repository-previews', {...target, source_ref: branch}); alive();
      if (ticket !== sequence || form.locked()) return;
      const value = data.preview;
      if (!value || Object.keys(target).some(key => value.target?.[key] !== target[key]) || value.source_ref !== branch
          || !/^[0-9a-f]{40,64}$/.test(value.source_sha) || !/^[0-9a-f]{64}$/.test(value.binding_digest)
          || !Number.isSafeInteger(value.repository_id) || value.repository_id < 1) throw Error(t('pm_scope_mismatch'));
      form.set('repository_selection', JSON.stringify({...target, ...Object.fromEntries(
        ['source_ref', 'source_sha', 'repository_id', 'binding_digest'].map(key => [key, value[key]]))}));
      renderProof();
    } catch (error) {if (retiredView(error) || viewLeft(alive)) return; if (ticket === sequence) proof.replaceChildren(errorBox(error, {within:proof}));}
    finally {if (ticket === sequence) {reading = false; update();}}
  }}, t('pm_preview_worktree'));
  const controls = h('div', {class: 'pm-fields'}, labelled('pub_ref', source), preview);
  const box = h('details', {'data-pm-worktree-choice': ''}, h('summary', {}, t('pm_context_location')),
    labelled('pm_context_location', mode), controls, selection, proof, h('p', {class: 'muted'}, t('pm_context_location_help')));
  function renderProof() {
    if (!selection.value) {proof.replaceChildren(); return;}
    try {const saved = JSON.parse(selection.value); proof.replaceChildren(h('p', {}, saved.repository, ' · ', h('code', {}, saved.source_sha)));}
    catch {proof.replaceChildren(h('p', {class: 'muted'}, t('pm_preview_required')));}
  }
  function update() {
    if (!form) return;
    controls.hidden = !mode.value || mode.value === 'owned';
    const blocked = form.locked() || reading || !scopes().includes('start') || !editable();
    mode.disabled = blocked; source.disabled = blocked; preview.disabled = blocked || !mode.value || mode.value === 'owned';
  }
  function changed() {
    if (form?.locked()) return;
    sequence++; reading = false;
    form?.set('repository_selection', mode.value === 'owned' ? JSON.stringify(capability()?.worktree_selection || null) : '');
    renderProof(); update();
  }
  mode.addEventListener('change', changed); source.addEventListener('input', changed);
  return {box, fields: {repository_mode: mode, repository_ref: source, repository_selection: selection},
    params(values) {
      if (!values.repository_mode) return {};
      if (!values.repository_selection) throw Error(t('pm_preview_required'));
      const fixed = JSON.parse(values.repository_selection);
      if (!fixed) throw Error(t('pm_preview_required'));
      return {repository_binding: fixed};
    },
    attach(value) {form = value; this.update();},
    update() {
      const selected = mode.value;
      if (!form?.locked()) {
        const options = [h('option', {value: ''}, t('pm_context_journal'))];
        if (capability()?.worktree_selection) options.push(h('option', {value: 'owned'}, t('pm_context_owned')));
        else for (const row of capability()?.worktrees || []) if (row.available) {
          const target = Object.fromEntries(['repository', 'host', 'workspace_id'].map(key => [key, row[key]]));
          options.push(h('option', {value: JSON.stringify(target)}, `${row.repository} · ${row.host} · ${row.workspace_id}`));
        }
        if (selected && !options.some(option => option.value === selected)) options.push(h('option', {value: selected}, t('pm_saved_worktree_choice')));
        mode.replaceChildren(...options); mode.value = selected;
      }
      update(); renderProof();
    }};
}
