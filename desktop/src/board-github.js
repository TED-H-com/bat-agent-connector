import {h} from './ui/dom.js';
import {locale} from './i18n.js';
import {boardGitHubStrings} from './board-github-strings.js';
export const boardGitHubText = (key, fallback = key) => (boardGitHubStrings[locale] || boardGitHubStrings.en)[key] || fallback;

export function boardGitHubLink(external) {
  if (!external || typeof external.repository !== 'string' || !/^[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/.test(external.repository)
      || !Number.isSafeInteger(external.number) || external.number < 1 || !['issue', 'pull_request'].includes(external.kind)) return null;
  const url = `https://github.com/${external.repository}/${external.kind === 'pull_request' ? 'pull' : 'issues'}/${external.number}`;
  if (external.url !== url) return null;
  const marker = external.availability === 'missing' ? 'bgh_missing_card' : external.draft ? 'bgh_draft' : external.state === 'closed' && (external.kind === 'pull_request' || external.state_reason !== 'completed') ? 'bgh_closed' : external.state === 'closed' && external.state_reason === 'completed' ? 'bgh_completed' : external.state === 'merged' ? 'bgh_done' : null;
  return {url, label: `${boardGitHubText(external.kind === 'pull_request' ? 'bgh_pr' : 'bgh_issue')} #${external.number}${marker ? ' · ' + boardGitHubText(marker) : ''}`, repository: external.repository};
}

// All writes use the shared durable form: original key, receipt and draft recovery.
export function boardGitHubPanel({t, api, alive, caps, form, permitted, workspaceId = null, projectId = null, onSaved}) {
  const bt = (key, values) => boardGitHubText(key, t(key, values));
  let current = null, projects = [], forms = [], loaded = false, serial = 0, preferredProject = null;
  let repository = h('input', {type: 'text', maxlength: 200, placeholder: 'owner/repo', required: true, 'aria-label': bt('bgh_repo')});
  let project = h('select', {'aria-label': bt('bgh_project')});
  let enabled = h('input', {type: 'hidden', value: 'true'});
  let toggle = h('input', {type: 'checkbox', checked: true});
  let includeClosed = h('input', {type: 'hidden', value: 'false'});
  let history = h('input', {type: 'checkbox'});
  const status = h('p', {role: 'status', class: 'muted', 'data-github-sync-status': ''});
  const controls = h('div'), description = h('p', {class: 'muted'}), stamp = h('p', {class: 'ui-meta'});
  const element = h('section', {class: 'panel', 'data-board-github': '', hidden: true},
    h('h3', {}, bt('bgh_title')), status, controls, description, stamp,
    h('details', {}, h('summary', {}, bt('bgh_title')), h('p', {}, bt('bgh_help'))));
  const selected = () => projectId || project.value || null;
  const allowed = () => loaded && permitted() && !forms.some(f => f.locked());
  const updateStatus = () => {
    const repo = current?.repository;
    status.textContent = bt(!repo ? 'bgh_no_repo' : 'bgh_' + (current?.status || 'not_synced'));
    description.textContent = current?.authenticated === false ? bt('bgh_public') : '';
    stamp.textContent = current?.last_sync ? `${bt('bgh_last')} · ${new Date(current.last_sync * 1000).toLocaleString()}` : '';
  };
  function build() {
    const repoValue = repository.value, projectValue = project.value;
    for (const f of forms) f.dispose(); forms = [];
    repository = repository.cloneNode(true); repository.disabled = false; repository.value = repoValue;
    project = project.cloneNode(true); project.disabled = false; project.value = projectValue;
    enabled = enabled.cloneNode(true); enabled.disabled = false;
    toggle = toggle.cloneNode(true); toggle.disabled = false;
    includeClosed = includeClosed.cloneNode(true); includeClosed.disabled = false;
    history = history.cloneNode(true); history.disabled = false;
    current = projects.find(p => p.project_id === selected()) || null;
    repository.value = current?.repository || repository.value || '';
    toggle.checked = current?.revision ? current.enabled : true; enabled.value = String(toggle.checked);
    repository.dataset.pmDefault = repository.value; enabled.dataset.pmDefault = enabled.value;
    history.checked = Boolean(current?.include_closed); includeClosed.value = String(history.checked);
    includeClosed.dataset.pmDefault = includeClosed.value;
    const fields = {repository, enabled, include_closed: includeClosed, ...(workspaceId ? {project_id: project} : {})};
    let importForm, submittedValues = null;
    const refreshSaved = async result => {preferredProject = result.project_id || selected(); await refresh(null, true); await onSaved();};
    const saved = async result => {
      if (['unreachable', 'rate_limited', 'auth_required', 'invalid_source', 'apply_failed', 'sync_failed', 'too_large', 'identity_conflict'].includes(result.status)) {
        for (const [name, input] of Object.entries(fields)) input.value = submittedValues?.[name] ?? input.value;
        history.checked = includeClosed.value === 'true'; toggle.checked = enabled.value === 'true';
        status.textContent = bt('bgh_' + result.status);
        // The read has a terminal receipt, but its choices remain an unsent
        // draft. Persist after the shared form has released its busy state.
        setTimeout(() => {if (element.isConnected && forms.includes(importForm)) importForm.changed();}, 0);
        return;
      }
      await refreshSaved(result);
    };
    importForm = form({t: bt, action: workspaceId ? 'pm.github.import' : 'project.github.import',
      target: workspaceId ? {workspace_id: workspaceId} : {project_id: projectId}, fields,
      params: values => ({repository: values.repository.trim(), enabled: values.enabled === 'true', include_closed: values.include_closed === 'true', ...(workspaceId ? {project_id: values.project_id || null} : {})}),
      preconditions: () => ({expected_revision: current?.revision || 0}), permitted: () => loaded && permitted() && !forms.some(f => f !== importForm && f.locked()),
      recoverPermitted: permitted, label: current?.last_sync ? 'bgh_sync' : 'bgh_import',
      extra: h('div', {class: 'capture-fields'}, workspaceId ? h('label', {}, bt('bgh_project'), project) : null,
        h('label', {}, bt('bgh_repo'), repository), h('label', {class: 'capture-choice'}, toggle, bt('bgh_keep')),
        h('label', {class: 'capture-choice'}, history, bt('bgh_history')), enabled, includeClosed),
      onBeforeSaved: (_result, values) => {submittedValues = {...values};},
      onSaved: saved, savedText: 'bgh_checked'});
    toggle.checked = enabled.value === 'true'; history.checked = includeClosed.value === 'true';
    current = projects.find(p => p.project_id === selected()) || null;
    forms.push(importForm);
    const save = h('div');
    if (current?.project_id) {
      const pid = current.project_id;
      const configForm = form({t: bt, action: 'project.github.configure', target: {project_id: pid}, fields: {},
        params: () => ({repository: repository.value.trim(), enabled: toggle.checked, include_closed: history.checked}),
        preconditions: () => ({expected_revision: current.revision}), permitted: () => loaded && permitted() && !importForm.locked(),
        recoverPermitted: permitted, label: 'bgh_save', extra: h('span'), savedText: 'bgh_saved',
        onSaved: refreshSaved});
      configForm.box.querySelector('button[type=submit]').classList.replace('primary', 'secondary');
      forms.push(configForm); save.append(configForm.box);
    }
    toggle.addEventListener('change', () => {enabled.value = String(toggle.checked); forms[0]?.changed();});
    history.addEventListener('change', () => {includeClosed.value = String(history.checked); forms[0]?.changed();});
    project.addEventListener('change', () => {if (allowed()) {repository.value = ''; build();}});
    controls.replaceChildren(importForm.box, save); updateStatus();
    for (const f of forms) f.update();
  }
  async function refresh(data = null, force = false) {
    if (caps()?.features?.board_github_sync?.version !== 1) return;
    const ticket = ++serial; element.hidden = false;
    try {
      data ||= await api('GET', workspaceId ? `/pm/workspaces/${encodeURIComponent(workspaceId)}/projects?limit=100` : `/projects/${projectId}`);
      alive(); if (ticket !== serial) return;
      const settings = data.github_sync;
      if (!settings) throw Error();
      projects = workspaceId ? settings.projects : [{...settings, name: data.project?.name}];
      if (!Array.isArray(projects)) throw Error();
      loaded = true;
      if (force || !forms.some(f => f.locked() || f.hasDraft())) {
        if (workspaceId) {
          const previous = preferredProject || project.value; preferredProject = null;
          project.replaceChildren(...projects.map(p => h('option', {value: p.project_id}, p.name)), h('option', {value: ''}, bt('bgh_new')));
          project.value = projects.some(p => p.project_id === previous) ? previous : projects[0]?.project_id || '';
          project.dataset.pmDefault = project.value;
          if (!repository.value) repository.value = settings.suggested_repository || '';
        }
        build();
      } else {
        current = projects.find(p => p.project_id === selected()) || null; updateStatus();
        for (const f of forms) f.update();
      }
    } catch {try {alive(); if (ticket === serial) {loaded = false; status.textContent = bt('bgh_read_failed'); for (const f of forms) f.update();}} catch { /* retired */ }}
  }
  return {element, refresh, message: () => status.textContent, dispose: () => {serial++; for (const f of forms) f.dispose();}};
}
