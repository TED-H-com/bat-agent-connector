import {iconButton} from './ui/components.js';
import {timeStamp, timeLabel} from './ui/stamps.js';
import {errorPresentation} from './error-presentation.js';
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const refs = value => Array.isArray(value) && value.length <= 20 && value.every(ref => ref && typeof ref.skill_id === 'string' && digest(ref.digest)) && new Set(value.map(ref => ref.skill_id)).size === value.length;
const record = value => value && refs(value.selected) && Number.isInteger(value.revision) && value.revision >= 0 && digest(value.digest);
const key = (host, workspace) => JSON.stringify([host, workspace]);
const same = (a, b) => a.skill_id === b.skill_id && a.digest === b.digest;
export function projectSkillsPanel({h, t, api, caps, guard, submit, projectId, storageKey, onEvents, errorBox}) {
  let saved = {host: '', workspace: '', drafts: {}};
  try {const raw = JSON.parse(localStorage.getItem(storageKey)); if (raw && typeof raw.host === 'string' && typeof raw.workspace === 'string' && raw.drafts && typeof raw.drafts === 'object' && !Array.isArray(raw.drafts)) saved = raw;} catch { /* empty draft */ }
  let doc = null, draft = null, busy = false, discovery = 0, serial = 0, discoveredHost = '', discoveryReady = false;
  const host = h('select', {'aria-label': t('host')}, h('option', {value: ''}, t('start_choose_host')),
    ...(caps()?.hosts || []).map(value => h('option', {value: value.host}, value.host)));
  if (![...host.options].some(option => option.value === saved.host)) saved.host = '';
  host.value = saved.host;
  const workspace = h('select', {'aria-label': t('start_workspace')}, h('option', {value: ''}, t('start_choose_workspace')));
  const status = h('p', {class: 'muted', role: 'status'}), source = h('div'), list = h('div');
  const persist = () => {guard(); localStorage.setItem(storageKey, JSON.stringify(saved));};
  const coherent = () => doc && doc.host === saved.host && doc.workspace_id === saved.workspace;
  const refresh = iconButton({icon:'refresh-cw', label:t('skills_refresh'), ...{class: 'secondary', type: 'button', onclick: () => discover(true)}});
  const discard = h('button', {class: 'secondary', type: 'button', onclick: () => {
    if (busy || draft?.intent || !coherent()) return;
    try {guard(); const next = {...saved, drafts: {...saved.drafts}}; delete next.drafts[key(saved.host, saved.workspace)];
      localStorage.setItem(storageKey, JSON.stringify(next)); saved = next; draft = null; load(true);
    } catch (error) {status.textContent = errorPresentation(error, t).summary;}
  }}, t('skills_reload'));
  const save = h('button', {class: 'primary', type: 'button', onclick: async () => {
    if (busy || !draft || !coherent()) return;
    if (!draft.intent && (doc.catalog.stale || doc.catalog.status !== 'available')) return;
    busy = true;
    try {
      guard(); draft.intent ||= {selected: structuredClone(draft.selected), revision: draft.revision, digest: draft.digest}; persist(); update();
      const intent = draft.intent;
      const op = await submit('project.skills.update', {project_id: projectId},
        {host: saved.host, workspace_id: saved.workspace, selected: intent.selected},
        {expected_revision: intent.revision, expected_catalog_digest: intent.digest}, storageKey);
      guard();
      if (op.status === 'succeeded') {
        const next = {...saved, drafts: {...saved.drafts}}; delete next.drafts[key(saved.host, saved.workspace)];
        localStorage.setItem(storageKey, JSON.stringify(next)); saved = next; draft = null;
        status.textContent = t('skills_saved'); await load();
      } else {
        if (['failed', 'cancelled'].includes(op.status)) {draft.intent = null; persist();}
        status.textContent = op.status_reason || t('models_pending');
      }
    } catch (error) {
      try {guard(); if (error.status === 409 && ['VERSION_CONFLICT', 'SKILL_CATALOG_CHANGED', 'SKILL_SOURCE_CHANGED'].includes(error.code)) {draft.intent = null; persist();}
        status.textContent = errorPresentation(error, t).summary;} catch { /* retired */ }
    } finally {busy = false; update();}
  }}, t('skills_save'));
  const box = h('details', {class: 'panel project-skills', 'data-project-skills': ''}, h('summary', {}, t('skills_title')),
    h('p', {class: 'note'}, t('skills_not_applied')), h('div', {class: 'capture-fields'},
      h('label', {}, t('host'), host), h('label', {}, t('start_workspace'), workspace)),
    h('div', {class: 'actions'}, refresh), status, source, list, h('div', {class: 'actions'}, save, discard));
  function update() {
    const locked = busy || Boolean(draft?.intent), valid = coherent();
    host.disabled = locked; workspace.disabled = locked || !discoveryReady; refresh.disabled = busy || !saved.host;
    save.textContent = t(draft?.intent ? 'permissions_retry' : 'skills_save');
    save.disabled = busy || !valid || !draft || (!draft.intent && (doc.catalog.status !== 'available' || doc.catalog.stale)) ||
      !caps()?.actions?.some(action => action.action === 'project.skills.update' && action.allowed);
    discard.disabled = locked || !valid || !draft;
    for (const control of list.querySelectorAll('input,button')) control.disabled = locked || !valid || control.dataset.unavailable === 'true';
  }
  function render() {
    if (!coherent() || !draft) {list.replaceChildren(); update(); return;}
    const catalog = doc.catalog;
    source.replaceChildren(h('p', {class: 'muted'}, timeLabel(t, 'models_source', {source: `${catalog.source} · ${doc.host} / ${doc.workspace_id}`, time: timeStamp(catalog.observed_at)})),
      h('p', {class: 'muted'}, t('skills_compatibility')));
    if (catalog.status !== 'available' || catalog.stale || !catalog.complete) source.append(h('p', {class: 'note warn'}, t('skills_catalog_state', {state: catalog.reason || (catalog.stale ? 'stale' : !catalog.complete ? 'partial' : catalog.status)})));
    if (doc.selection.selected.length && (doc.selection.host !== doc.host || doc.selection.workspace_id !== doc.workspace_id)) source.append(h('p', {class: 'note warn'}, t('skills_other_binding', {host: doc.selection.host, workspace: doc.selection.workspace_id})));
    const change = fn => {
      if (busy || draft.intent || !coherent()) return;
      fn(); try {persist(); status.textContent = t('skills_unsaved'); render();} catch (error) {status.textContent = errorPresentation(error, t).summary;}
    };
    const unresolved = draft.selected.filter(ref => !catalog.skills.some(skill => same(ref, skill) && skill.available));
    list.replaceChildren(...unresolved.map(ref => h('div', {class: 'row'}, h('div', {class: 'grow'},
      h('strong', {}, t('skills_unresolved')), h('p', {}, ref.skill_id), h('code', {}, ref.digest)),
      h('button', {class: 'mini', type: 'button', onclick: () => change(() => {draft.selected = draft.selected.filter(value => !same(value, ref));})}, t('remove')))),
      ...catalog.skills.map(skill => {
        const selected = draft.selected.some(ref => same(ref, skill));
        const input = h('input', {type: 'checkbox', checked: selected, 'data-unavailable': String(!skill.available), onchange: () => change(() => {
          if (input.checked && draft.selected.length >= 20 && !draft.selected.some(ref => ref.skill_id === skill.skill_id)) return;
          draft.selected = draft.selected.filter(ref => ref.skill_id !== skill.skill_id);
          if (input.checked) draft.selected.push({skill_id: skill.skill_id, digest: skill.digest});
        })});
        return h('section', {class: 'panel'}, h('label', {}, input, ' ', skill.name || skill.skill_id),
          h('p', {class: 'muted'}, `${skill.scope} · ${skill.agent} · ${skill.relative_path}`),
          skill.description ? h('p', {}, skill.description) : null,
          h('details', {}, h('summary', {}, t('skills_version')), h('code', {}, skill.digest || '—'), h('p', {}, `${skill.files} files · ${skill.size_bytes} bytes`)),
          !skill.available ? h('p', {class: 'note warn'}, skill.reason || t('obs_unknown')) : null);
      }));
    update();
  }
  async function load(force = false) {
    if (!saved.host || !saved.workspace || discoveredHost !== saved.host || !discoveryReady) return;
    const selectedHost = saved.host, selectedWorkspace = saved.workspace, request = ++serial; update();
    try {
      const next = await api('GET', `/projects/${encodeURIComponent(projectId)}/skills?host=${encodeURIComponent(selectedHost)}&workspace_id=${encodeURIComponent(selectedWorkspace)}${force ? '&refresh=1' : ''}`);
      guard(); if (request !== serial || selectedHost !== saved.host || selectedWorkspace !== saved.workspace) return;
      if (next.version !== 1 || next.project_id !== projectId || next.host !== selectedHost || next.workspace_id !== selectedWorkspace ||
          !Array.isArray(next.catalog?.skills) || !refs(next.selection?.selected) || !Number.isInteger(next.selection.revision)) throw Error(t('skills_invalid'));
      doc = next; const draftKey = key(selectedHost, selectedWorkspace), stored = saved.drafts[draftKey];
      draft = record(stored) ? stored : {selected: next.selection.host === selectedHost && next.selection.workspace_id === selectedWorkspace ? structuredClone(next.selection.selected) : [], revision: next.selection.revision, digest: next.catalog.catalog_digest, intent: null};
      if (draft.intent && !record(draft.intent)) draft.intent = null;
      saved.drafts[draftKey] = draft;
      if (draft.revision !== next.selection.revision || draft.digest !== next.catalog.catalog_digest) status.textContent = t('skills_conflict');
      render();
    } catch (error) {try {guard(); if (request === serial) {errorBox?.(error); status.textContent = `${t('models_not_refreshed')} ${errorPresentation(error, t).summary}`;}} catch { /* retired */ }}
  }
  async function discover(force = false) {
    if (!saved.host) return;
    const request = ++discovery, selectedHost = saved.host;
    discoveryReady = false; update();
    try {
      const next = await api('GET', `/workspaces?host=${encodeURIComponent(selectedHost)}&limit=200`); guard();
      if (request !== discovery || selectedHost !== saved.host) return;
      if (!Array.isArray(next.workspaces) || !next.errors || Object.keys(next.errors).length || next.workspaces.some(value => value.host !== selectedHost || typeof value.workspace_id !== 'string')) throw Error(t('start_discovery_failed'));
      workspace.replaceChildren(h('option', {value: ''}, t('start_choose_workspace')), ...next.workspaces.map(value => h('option', {value: value.workspace_id}, `${value.name || value.workspace_id} · ${value.workspace_id}`)));
      if (saved.workspace && !next.workspaces.some(value => value.workspace_id === saved.workspace)) workspace.append(h('option', {value: saved.workspace}, `${saved.workspace} · ${t('skills_workspace_missing')}`));
      workspace.value = saved.workspace; discoveredHost = selectedHost; discoveryReady = true;
      if (next.has_more) status.textContent = t('start_truncated');
      update(); await load(force);
    } catch (error) {try {guard(); if (request === discovery) status.textContent = errorPresentation(error, t).summary;} catch { /* retired */ }}
  }
  host.addEventListener('change', () => {if (busy || draft?.intent) return; saved.host = host.value; saved.workspace = ''; doc = draft = null; serial++; source.replaceChildren(); list.replaceChildren(); workspace.replaceChildren(); try {persist();} catch {} discover();});
  workspace.addEventListener('change', () => {if (busy || draft?.intent) return; saved.workspace = workspace.value; doc = draft = null; serial++; try {persist();} catch {} load();});
  box.addEventListener('toggle', () => {if (box.open && !discoveryReady) discover();});
  const off = onEvents(event => box.open && event.resource_type === 'project' && event.resource_id === projectId ? load() : undefined);
  update(); return {box, dispose() {serial++; discovery++; off();}};
}
