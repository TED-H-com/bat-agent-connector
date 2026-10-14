import {specSourceText} from './pm-spec-sources-strings.js';

// Parent mounts only behind features.pm_workspaces.spec_sources. Refresh never reads Git.
export function specSources({h, t, api, alive, workspaceId, errorBox}) {
  const text = (key, values) => specSourceText(t, key, values), endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}/spec-sources`;
  let caps = null, refreshId = 0, readId = 0, checking = false, reading = false;
  const facts = h('div'), status = h('div', {role: 'status'}), result = h('div', {'data-pm-spec-result': ''});
  const paths = h('textarea', {rows: 3, maxlength: 80099, 'aria-label': text('spec_paths')});
  const readButton = h('button', {type: 'button', class: 'secondary', disabled: true, onclick: () => void read()}, text('spec_read'));
  const checkButton = h('button', {type: 'button', class: 'secondary', onclick: () => void refresh()}, text('spec_refresh'));
  const controlsBox = h('details', {hidden: true, 'data-pm-spec-selection': ''}, h('summary', {}, text('spec_paths')), h('label', {}, text('spec_paths'), paths),
    h('p', {class: 'muted'}, text('spec_paths_help')));
  const element = h('section', {class: 'pm-packages', 'data-pm-spec-sources': ''}, h('h3', {}, text('spec_title')),
    h('p', {class: 'muted'}, text('spec_intro')), facts, h('p', {class: 'muted'}, text('spec_limit')),
    controlsBox, h('div', {class: 'actions'}, readButton, checkButton), status, result);
  const detail = (title, ...children) => h('details', {}, h('summary', {}, title), ...children);
  const fact = (label, value) => h('p', {class: 'pm-source'}, text(label), ': ', value ?? text('spec_unknown'));
  const identity = value => value?.eligible === true ? `${value.repository}@${value.commit_sha}` : null;
  function current() {alive(); return element.isConnected;}
  function controls() {
    controlsBox.hidden = !caps?.eligible;
    readButton.hidden = !caps?.eligible;
    readButton.disabled = checking || reading || !caps?.eligible;
    readButton.textContent = text(reading ? 'spec_reading' : 'spec_read');
    checkButton.disabled = checking;
    result.hidden = checking;
  }
  function clear() {readId++; result.replaceChildren();}
  paths.addEventListener('input', () => {clear(); status.replaceChildren(); controls();});
  function render(data) {
    const rows = [h('p', {class: 'muted'}, text('spec_scope')), h('p', {class: 'muted'}, text('spec_schema'))];
    if (!data.coverage.content_complete) rows.push(h('p', {}, text('spec_partial')));
    if (data.coverage.ignored_paths) rows.push(h('p', {class: 'muted'}, text('spec_ignored', {count: data.coverage.ignored_paths})));
    if (!data.documents.length) rows.push(h('p', {class: 'muted'}, text('spec_empty')));
    for (const doc of data.documents) {
      const row = detail(doc.path + ' · ' + text('spec_role_' + doc.role));
      if (doc.location === 'archive') row.append(h('p', {}, text('spec_archive')));
      row.append(detail(text('spec_source_details'), fact('spec_commit', doc.commit_sha), fact('spec_file_id', doc.file_id),
        fact('spec_source_id', doc.source_id), fact('spec_blob', doc.blob_sha), fact('spec_hash', doc.sha256)));
      if (doc.status !== 'read') row.append(fact('spec_reason', text('spec_reason_' + doc.reason)));
      else {
        if (doc.schema_declaration) row.append(fact('spec_schema_name', doc.schema_declaration.declared_name));
        row.append(detail(text('spec_original'), h('div', {class: 'pm-plan-document'}, doc.content)));
        if (doc.observations?.status === 'observation_limit') row.append(h('p', {class: 'muted'}, text('spec_observation_limit')));
        if (doc.role === 'tasks') {
          const tasks = detail(text('spec_tasks'), h('p', {class: 'muted'}, text('spec_task_note')));
          for (const task of doc.observations?.task_reports || []) tasks.append(h('p', {class: 'pm-note-text'},
            text('spec_line', {line: task.line}), ' · ', text(task.reported_checked ? 'spec_checked' : 'spec_unchecked'),
            task.in_fenced_block ? ' · ' + text('spec_example') : '', '\n', task.text));
          row.append(tasks);
        }
      }
      rows.push(row);
    }
    rows.push(detail(text('spec_source_details'), fact('spec_tree', data.tree_sha), fact('spec_digest', data.document_digest)));
    result.replaceChildren(...rows);
  }
  async function refresh() {
    const ticket = ++refreshId;
    checking = true; controls(); status.replaceChildren(h('p', {class: 'muted'}, text('spec_checking')));
    try {
      const value = await api('GET', `${endpoint}/capabilities`);
      if (!current() || ticket !== refreshId) return;
      if (value?.workspace_id !== workspaceId || value?.adapter !== 'openspec-fixed-git-v1' || value?.execution_authorized !== false ||
          typeof value.eligible !== 'boolean' || value.eligible && (typeof value.repository !== 'string' || !/^[0-9a-f]{40}$/.test(value.commit_sha))) throw Error(text('spec_invalid'));
      if (identity(caps) !== identity(value) || !value.eligible) clear();
      caps = value; facts.replaceChildren(...(caps.eligible ? [fact('spec_repo', caps.repository), fact('spec_commit', caps.commit_sha)] : []));
      status.replaceChildren(h('p', {class: 'muted'}, text(caps.eligible ? 'spec_ready' : caps.reason === 'PM_SPEC_BINDING_UNAVAILABLE' ? 'spec_no_binding' : 'spec_unavailable')));
    } catch (error) {
      if (ticket !== refreshId || !element.isConnected) return;
      clear(); caps = null; facts.replaceChildren(); status.replaceChildren(errorBox(error, {within:status}));
    } finally {if (ticket === refreshId) {checking = false; controls();}}
  }
  async function read() {
    if (readButton.disabled || !caps?.eligible) return;
    const chosen = paths.value.split(/\r?\n/).map(path => path.trim()).filter(Boolean);
    if (chosen.length > 100 || new Set(chosen).size !== chosen.length || chosen.some(path => path.length > 800)) {
      clear(); status.replaceChildren(h('p', {class: 'error'}, text('spec_bad_paths'))); return;
    }
    const selected = identity(caps), ticket = ++readId, query = new URLSearchParams({repository: caps.repository, commit_sha: caps.commit_sha});
    if (chosen.length) query.set('paths', chosen.join(','));
    if (`${endpoint}?${query}`.length > 4096) {
      clear(); status.replaceChildren(h('p', {class: 'error'}, text('spec_long_selection'))); return;
    }
    result.replaceChildren(); reading = true; controls(); status.replaceChildren();
    try {
      const value = await api('GET', `${endpoint}?${query}`);
      if (!current() || ticket !== readId || selected !== identity(caps)) return;
      if (value?.workspace_id !== workspaceId || value.repository !== caps.repository || value.commit_sha !== caps.commit_sha ||
          value.adapter !== 'openspec-fixed-git-v1' || value.execution_authorized !== false || !Array.isArray(value.documents) ||
          !value.coverage || value.documents.some(doc => doc.commit_sha !== caps.commit_sha || typeof doc.path !== 'string')) throw Error(text('spec_invalid'));
      render(value);
    } catch (error) {
      if (ticket !== readId || !element.isConnected) return;
      clear(); caps = null; facts.replaceChildren(); status.replaceChildren(errorBox(error, {within:status}));
    } finally {reading = false; controls();}
  }
  return {element, refresh};
}
