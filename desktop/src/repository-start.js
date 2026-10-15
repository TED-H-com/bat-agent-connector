// One explicitly published head and one new managed session. Central owns every effect.
import {composerShortcut} from './composer-shortcut.js';
import {modelChoice} from './model-preferences.js';
const object = v => v && typeof v === 'object' && !Array.isArray(v);
const equal = (a, b) => a === b || (Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => equal(v, b[i]))) ||
  (object(a) && object(b) && Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(k => equal(a[k], b[k])));
const text = (v, max) => typeof v === 'string' && v.trim().length > 0 && v.length <= max && !/[\x00-\x1f\x7f]/.test(v);
const oid = v => typeof v === 'string' && /^op_[0-9a-f]{32}$/.test(v);
const sha = v => typeof v === 'string' && /^[0-9a-f]{40}$/.test(v);
const digest = v => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const terminal = op => ['succeeded', 'failed', 'cancelled'].includes(op?.status);
const target = v => object(v) && Object.keys(v).length === 3 && ['repository', 'host', 'workspace_id'].every(k => text(v[k], 256));
const ref = v => typeof v === 'string' && /^refs\/heads\/(?!-)(?!.*\.\.)(?!.*\/\/)(?!.*@\{)[A-Za-z0-9._/-]{1,200}$/.test(v) &&
  !/[./]$/.test(v) && v.slice(11).split('/').every(p => !p.startsWith('.') && !p.endsWith('.lock'));
// Display a familiar branch name, but freeze only a fully qualified branch ref in the request.
const branchRef = value => value.startsWith('refs/') ? value : 'refs/heads/' + value;
const preconditions = v => object(v) && Object.keys(v).length === 2 && Number.isSafeInteger(v.repository_id) && v.repository_id > 0 && digest(v.binding_digest);
const projectId = v => typeof v === 'string' && /^prj_[0-9a-f]{20}$/.test(v);
const workItemId = v => typeof v === 'string' && /^wi_[0-9a-f]{20}$/.test(v);
const artifactRefs = v => Array.isArray(v) && v.every(r => object(r) && Object.keys(r).length === 3 &&
  /^art_[0-9a-f]{32}$/.test(r.artifact_id) && Number.isSafeInteger(r.revision) && r.revision > 0 && digest(r.digest));
const requestPreconditions = r => {
  if (!object(r.params) || !object(r.preconditions)) return false;
  const fixed = {...r.preconditions};
  if ('project_id' in r.params) {
    if (!projectId(r.params.project_id) || !Number.isSafeInteger(fixed.expected_project_version) || fixed.expected_project_version < 1) return false;
    delete fixed.expected_project_version;
  }
  if ('work_item_id' in r.params) {
    if (!projectId(r.params.project_id) || !workItemId(r.params.work_item_id) || !digest(fixed.expected_work_item_fingerprint)) return false;
    delete fixed.expected_work_item_fingerprint;
  }
  return preconditions(fixed);
};
const validRequest = r => r?.action === 'repository.continue' && target(r.target) && requestPreconditions(r) && object(r.params) &&
  Object.keys(r.params).every(k => ['agent', 'prompt', 'title', 'model', 'artifacts', 'project_id', 'work_item_id', 'source_ref', 'source_sha'].includes(k)) &&
  ref(r.params.source_ref) && sha(r.params.source_sha) && ['claude', 'codex'].includes(r.params.agent) &&
  typeof r.params.prompt === 'string' && r.params.prompt.trim() && r.params.prompt.length <= 12000 &&
  ['title', 'model'].every(k => !(k in r.params) || text(r.params[k], 256)) &&
  (!('artifacts' in r.params) || artifactRefs(r.params.artifacts));
const validPreview = (p, input) => object(p) && equal(p.target, input.target) && p.source_ref === input.source_ref &&
  sha(p.source_sha) && p.exact_ref_head_only === true && preconditions(p.preconditions) &&
  p.repository_id === p.preconditions.repository_id && p.binding_digest === p.preconditions.binding_digest &&
  object(p.workspace) && p.workspace.workspace_id === input.target.workspace_id && text(p.workspace.folder, 4096) &&
  (p.workspace.name == null || typeof p.workspace.name === 'string');
// Both are checked after existing-key replay, before the operation is inserted.
const noAdmission = new Set(['REPOSITORY_NOT_BOUND', 'REPOSITORY_HOST_UNAVAILABLE']);
export function repositoryStartPanel({h, t, api, caps, guard, ready, errorBox, opStatus, storageKey, project = null, repairSeed = null, attachmentFactory, submitPreference}) {
  if (repairSeed && (repairSeed.project_id !== project || !workItemId(repairSeed.work_item_id) ||
      !digest(repairSeed.expected_work_item_fingerprint) || typeof repairSeed.prompt !== 'string' || !repairSeed.prompt.trim())) throw new Error(t('repair_unavailable'));
  let raw; try {raw = JSON.parse(localStorage.getItem(storageKey));} catch { /* new draft */ }
  let saved = {target: target(raw?.target) ? raw.target : null, source_ref: typeof raw?.source_ref === 'string' ? raw.source_ref : '',
    agent: ['claude', 'codex'].includes(raw?.agent) ? raw.agent : 'claude', prompt: typeof raw?.prompt === 'string' ? raw.prompt : '',
    model: typeof raw?.model === 'string' ? raw.model : '', title: typeof raw?.title === 'string' ? raw.title : ''};
  if (raw?.intent) {
    const valid = validRequest(raw.intent.request) && text(raw.intent.key, 200) && (!project || raw.intent.request.params.project_id === project) &&
      (repairSeed ? raw.intent.request.params.work_item_id === repairSeed.work_item_id : !raw.intent.request.params.work_item_id);
    saved.intent = {request: valid ? raw.intent.request : null, key: valid ? raw.intent.key : null,
      operation_id: oid(raw.intent.operation_id) ? raw.intent.operation_id : null,
      refused: !raw.intent.operation_id && noAdmission.has(raw.intent.refused) ? raw.intent.refused : null};
    if (valid) Object.assign(saved, {target: raw.intent.request.target, title: '', model: ''}, raw.intent.request.params);
  }
  if (repairSeed && !saved.intent) saved.prompt = repairSeed.prompt;
  let preview = null, operation = null, busy = false, reading = false, sequence = 0, readFailed = false, submission = null, refreshing = null;
  let projectDoc = null, projectFailed = Boolean(project), previewProjectVersion = null, autoSelect = !raw, projectQueue = Promise.resolve();
  const current = () => {try {guard(); return true;} catch {return false;}};
  const persist = () => {guard(); localStorage.setItem(storageKey, JSON.stringify(saved));};
  const observe = () => caps()?.scopes?.includes('observe');
  const allowed = () => observe() && caps()?.scopes?.includes('start') && (!repairSeed || caps()?.scopes?.includes('manage')) && caps()?.actions?.some(a => a.action === 'repository.continue' && a.allowed === true);
  const expanded = () => caps()?.features?.project_dispatch?.version === 1;
  const projectReady = () => !project || expanded() && projectDoc && !projectDoc.archived && !projectFailed;
  const bindings = () => (caps()?.features?.repository_sync || []).filter(b => b.exact_ref_head_only === true &&
    target({repository: b.repository, host: b.host, workspace_id: b.workspace_id}) &&
    (!project || projectDoc?.repositories?.some(r => r.toLowerCase() === b.repository.toLowerCase())));
  const bound = () => bindings().some(b => equal({repository: b.repository, host: b.host, workspace_id: b.workspace_id}, saved.target));
  const hostAllowed = () => caps()?.hosts?.some(h => h.host === saved.target?.host && h.writes === true && h.orchestrate === true);
  const status = h('div', {role: 'status'}), facts = h('div', {'data-published-preview': ''}), outcome = h('div', {'data-published-result': ''});
  const projectStatus = h('div', {role: 'status', 'data-dispatch-project': ''});
  const binding = h('select', {'aria-label': t('pub_binding')}), sourceRef = h('input', {'aria-label': t('pub_ref'), maxlength: 211, placeholder: 'main'});
  const branchHelp = h('p', {class: 'muted', 'aria-live': 'polite'});
  const agent = h('select', {'aria-label': t('start_agent')}, h('option', {value: 'claude'}, 'Claude'), h('option', {value: 'codex'}, 'Codex'));
  const prompt = h('textarea', {'aria-label': t('pub_prompt'), maxlength: 12000, rows: 5}), title = h('input', {'aria-label': t('start_title'), maxlength: 256});
  const model = h('input', {'aria-label': t('start_model'), maxlength: 256, placeholder: t('start_model_default')});
  const inputs = {source_ref: sourceRef, agent, prompt, title, model};
  const models = modelChoice({h, t, api, caps, guard, host: () => saved.target?.host, agent, model, submit: submitPreference, storageKey});
  let modelHost = null;
  prompt.value = saved.prompt;
  prompt.readOnly = Boolean(repairSeed);
  const attachments = expanded() && !repairSeed && attachmentFactory ? attachmentFactory(prompt, () => {if (current()) update();}) : null;
  if (attachments && !saved.intent) saved.prompt = prompt.value;
  const attachmentBox = attachments ? h('fieldset', {class: 'dispatch-attachments'}, attachments.box) : null;
  const key = b => JSON.stringify(b);
  function fill() {
    binding.replaceChildren(h('option', {value: ''}, t('pub_choose')), ...bindings().map(b => {
      const value = {repository: b.repository, host: b.host, workspace_id: b.workspace_id};
      return h('option', {value: key(value)}, `${b.repository} · ${b.host} · ${b.workspace_id}`);
    }));
    if (saved.target && ![...binding.options].some(o => o.value === key(saved.target))) binding.append(h('option', {value: key(saved.target)}, Object.values(saved.target).join(' · ')));
    binding.value = saved.target ? key(saved.target) : '';
    for (const [k, el] of Object.entries(inputs)) el.value = saved[k];
  }
  const showError = e => {if (current()) status.replaceChildren(errorBox(e, {within:status}));};
  const selected = () => ({target: saved.target, source_ref: branchRef(saved.source_ref)});
  const effectivePrompt = () => saved.prompt.trim() ? saved.prompt : (attachments?.refs().length ? t('dispatch_inspect_images') : saved.prompt);
  const request = () => ({action: 'repository.continue', target: saved.target,
    params: {source_ref: preview?.source_ref, source_sha: preview?.source_sha, agent: saved.agent, prompt: effectivePrompt(),
      ...(saved.title ? {title: saved.title} : {}), ...(saved.model && expanded() ? {model: saved.model} : {}),
      ...(attachments?.refs().length ? {artifacts: attachments.refs()} : {}), ...(project ? {project_id: project} : {}),
      ...(repairSeed ? {work_item_id: repairSeed.work_item_id} : {})},
    preconditions: preview ? {...preview.preconditions, ...(project ? {expected_project_version: previewProjectVersion} : {}),
      ...(repairSeed ? {expected_work_item_fingerprint: repairSeed.expected_work_item_fingerprint} : {})} : null});
  const inspect = h('button', {class: 'secondary', onclick: async () => {
    if (!current() || saved.intent || reading || !observe() || !ready() || !projectReady() || !bound() || !ref(selected().source_ref)) return;
    const expected = ++sequence, input = selected(), projectVersion = projectDoc?.version; preview = null; reading = true; update(); status.replaceChildren();
    try {
      const doc = await api('POST', '/repository-previews', {...input.target, source_ref: input.source_ref}); guard();
      if (expected !== sequence || saved.intent || !equal(input, selected())) return;
      if (!validPreview(doc.preview, input)) throw new Error(t('pub_invalid_preview'));
      preview = doc.preview; previewProjectVersion = projectVersion;
    } catch (e) {if (expected === sequence) showError(e);}
    finally {if (expected === sequence && current()) {reading = false; update();}}
  }}, t('pub_preview'));
  function accept(candidate) {
    guard(); const intent = saved.intent;
    if (!intent?.request || !intent.key || !oid(candidate?.operation_id) || candidate.actor !== caps()?.actor || candidate.idempotency_key !== intent.key ||
        !equal({action: candidate.action, target: candidate.target, params: candidate.params, preconditions: candidate.preconditions}, intent.request) ||
        intent.operation_id && candidate.operation_id !== intent.operation_id) throw new Error(t('pub_invalid_result'));
    const result = candidate.result, refs = candidate.external_refs || {};
    for (const [name, value] of Object.entries({host: intent.request.target.host, repository: intent.request.target.repository,
      repository_id: intent.request.preconditions.repository_id, source_ref: intent.request.params.source_ref,
      source_sha: intent.request.params.source_sha, repository_binding: intent.request.preconditions.binding_digest,
      ...(intent.request.params.project_id ? {project_id: intent.request.params.project_id} : {}),
      ...(intent.request.params.work_item_id ? {work_item_id: intent.request.params.work_item_id} : {})})) {
      if (name in refs && refs[name] !== value) throw new Error(t('pub_invalid_result'));
    }
    if (result != null && (!object(result) || result.host !== intent.request.target.host || result.workspace_id !== intent.request.target.workspace_id ||
        result.repository !== intent.request.target.repository || result.source_ref !== intent.request.params.source_ref || result.source_sha !== intent.request.params.source_sha ||
        result.repository_id !== intent.request.preconditions.repository_id || result.binding_digest !== intent.request.preconditions.binding_digest ||
        !text(result.session_id, 256) || result.session_id !== candidate.external_refs?.session_id)) throw new Error(t('pub_invalid_result'));
    if (candidate.status === 'succeeded' && (!result || result.message_id !== `batc-${candidate.operation_id}`)) throw new Error(t('pub_invalid_result'));
    operation = candidate; intent.operation_id = candidate.operation_id; readFailed = false; persist(); status.replaceChildren(); update();
  }
  const apply = h('button', {class: 'primary', onclick: async () => {
    if (!current() || busy || readFailed || !ready() || !allowed() || saved.intent?.operation_id || saved.intent?.refused) return;
    if (!saved.intent) {
      if (!projectReady() || !preview || !validPreview(preview, selected()) || !bound() || !hostAllowed() ||
          attachments && !attachments.ready() || !validRequest(request())) return;
      saved.intent = {request: structuredClone(request()), key: crypto.randomUUID(), operation_id: null};
    }
    if (!saved.intent.request || !saved.intent.key) return;
    try {persist();} catch (e) {showError(e); update(); return;}
    busy = true; update(); const intent = saved.intent;
    submission = (async () => {
      try {const doc = await api('POST', '/operations?wait=3', intent.request, intent.key); guard(); accept(doc.operation);}
      catch (e) {if (current()) {if (e.status >= 400 && e.status < 500 && noAdmission.has(e.code)) {intent.refused = e.code; try {persist();} catch { /* fixed request remains */ }} showError(e);}}
      finally {busy = false; if (current()) update();}
    })();
    try {await submission;} finally {submission = null;}
  }}, t('pub_apply'));
  const check = h('button', {class: 'secondary', onclick: () => refresh(true).catch(() => {})}, t('permissions_check'));
  const another = h('button', {class: 'secondary', onclick: () => {
    if (repairSeed || !current() || busy || refreshing || readFailed || attachments && !attachments.ready() || !(terminal(operation) || saved.intent?.refused)) return;
    const previous = saved; saved = {target: saved.target, source_ref: saved.source_ref, agent: saved.agent, prompt: '', title: '', model: saved.model};
    try {persist();} catch (e) {saved = previous; showError(e); return;}
    operation = preview = null; status.replaceChildren(); attachments?.reset(); fill(); update();
  }}, t('pub_new'));
  function change(field, value) {
    if (!current() || saved.intent || busy || repairSeed && field === 'prompt') {fill(); return;}
    saved[field] = value;
    if (field === 'target' || field === 'source_ref') {preview = null; sequence++; reading = false;}
    try {persist();} catch (e) {showError(e);} update();
  }
  binding.addEventListener('change', () => change('target', binding.value ? JSON.parse(binding.value) : null));
  for (const [field, el] of Object.entries(inputs)) el.addEventListener(field === 'agent' ? 'change' : 'input', () => change(field, el.value));
  const label = (name, el) => h('label', {}, t(name), el);
  const advanced = h('details', {class: 'dispatch-advanced', open: Boolean(saved.model || saved.title)},
    h('summary', {}, t('dispatch_advanced')), h('div', {class: 'capture-fields'}, label('start_title', title), label('start_model', model)));
  const shortcut = composerShortcut({h, t, input: prompt, button: apply, storageKey: `${storageKey}.shortcut`, guard});
  if (project) advanced.append(shortcut.box,
    h('p', {class: 'muted'}, t('pub_head_only')), h('p', {class: 'muted'}, t('pub_isolation')));
  const box = h('section', {class: 'session-start published-start', 'data-published-start': ''},
    project ? projectStatus : null,
    h('div', {class: 'panel dispatch-destination'}, h('div', {class: 'capture-fields'}, label('pub_binding', binding), label('pub_ref', sourceRef)), branchHelp, !project ? h('p', {class: 'muted'}, t('pub_head_only')) : null,
      h('div', {class: 'actions'}, inspect), facts),
    h('div', {class: 'panel dispatch-instructions'}, label('pub_prompt', prompt),
      h('div', {class: 'capture-fields'}, label('start_agent', agent), !project ? label('start_title', title) : null,
      !project && expanded() ? label('start_model', model) : null), models.box,
      !project ? shortcut.box : null, attachmentBox, project ? advanced : h('p', {class: 'muted'}, t('pub_isolation'))),
    h('div', {class: 'actions'}, apply, check, another), outcome, status);
  function update() {
    const fixed = Boolean(saved.intent);
    if (attachmentBox) {attachmentBox.disabled = fixed || busy; attachmentBox.hidden = fixed;}
    binding.disabled = fixed || busy;
    for (const el of Object.values(inputs)) el.disabled = fixed || busy;
    models.update();
    if (modelHost !== saved.target?.host) {modelHost = saved.target?.host; models.refresh();}
    const validBranch = ref(selected().source_ref);
    branchHelp.hidden = fixed || Boolean(project && !(saved.source_ref && !validBranch));
    branchHelp.textContent = t(saved.source_ref && !validBranch ? 'pub_branch_invalid' : 'pub_branch_help');
    sourceRef.setAttribute('aria-invalid', String(Boolean(saved.source_ref && !validBranch)));
    inspect.hidden = fixed; inspect.disabled = reading || !observe() || !ready() || !projectReady() || !bound() || !validBranch;
    inspect.textContent = t(reading ? 'pub_loading' : 'pub_preview');
    apply.hidden = Boolean(saved.intent?.operation_id || saved.intent?.refused); apply.textContent = t(fixed ? 'permissions_retry' : 'pub_apply');
    apply.disabled = busy || readFailed || !ready() || !allowed() || (fixed ? !saved.intent.request || !saved.intent.key :
      reading || !projectReady() || !preview || !validPreview(preview, selected()) || !bound() || !hostAllowed() ||
      attachments && !attachments.ready() || !validRequest(request()));
    check.hidden = !saved.intent?.operation_id; check.disabled = busy || Boolean(refreshing);
    another.hidden = Boolean(repairSeed) || !(terminal(operation) || saved.intent?.refused); another.disabled = busy || Boolean(refreshing) || readFailed || Boolean(attachments && !attachments.ready());
    facts.replaceChildren(); const original = saved.intent?.request;
    if (preview || original) {
      const r = original || request();
      const row = (label, value) => h('div', {}, h('strong', {}, t(label), ': '), h('span', {class: 'pre'}, value));
      facts.append(h('p', {class: 'muted'}, t('pub_reviewed')), row('capture_repository', r.target.repository), row('host', r.target.host),
        row('sessions_workspace_id', r.target.workspace_id), ...(preview ? [row('start_workspace', `${preview.workspace.name || ''} · ${preview.workspace.folder}`)] : []),
        row('pub_ref', r.params.source_ref), row('pub_sha', r.params.source_sha));
    }
    outcome.replaceChildren();
    if (!allowed()) outcome.append(h('p', {class: 'muted'}, t('pub_unavailable')));
    else if (!bindings().length && !fixed) outcome.append(h('p', {class: 'muted'}, t(project ? 'dispatch_no_bindings' : 'pub_no_bindings')));
    if (!fixed) return;
    outcome.append(h('p', {}, operation ? opStatus(operation) : t(saved.intent.refused ? 'start_refused' : 'start_unknown'), ' ',
      saved.intent.operation_id ? h('a', {href: `#/op/${saved.intent.operation_id}`}, t('permissions_details')) : null,
      operation?.status_reason ? ` · ${operation.status_reason}` : ''), h('p', {class: 'muted'}, t('pub_fixed')));
    if (!original || !saved.intent.key) outcome.append(h('p', {class: 'error'}, t('permissions_damaged')));
    if (original?.params.artifacts?.length) outcome.append(h('p', {class: 'muted'}, t('dispatch_fixed_inputs')),
      h('ul', {class: 'dispatch-fixed-inputs'}, ...original.params.artifacts.map(r => h('li', {}, `${r.artifact_id} · r${r.revision} · ${r.digest}`))));
    const complete = operation?.status === 'succeeded' && operation.result?.message_id === `batc-${operation.operation_id}`;
    const proof = complete ? operation.result : operation?.steps?.some(s => s.name === 'session.start' && s.status === 'succeeded') &&
      text(operation.external_refs?.session_id, 256) ? {host: saved.target.host, session_id: operation.external_refs.session_id} : null;
    if (proof) outcome.append(h('p', {}, t('start_started'), ' ', h('a', {href: `#/session/${encodeURIComponent(proof.host)}/${encodeURIComponent(proof.session_id)}`}, proof.session_id)),
      h('p', {class: 'muted'}, t(complete ? 'start_prompt_accepted' : 'start_prompt_unknown')));
  }
  async function refresh(fresh = false) {
    if (submission) {await submission; guard();}
    if (refreshing) {await refreshing; if (fresh) return refresh(true); return;}
    if (!saved.intent?.operation_id) {update(); return;}
    refreshing = (async () => {const doc = await api('GET', `/operations/${saved.intent.operation_id}`); guard(); accept(doc.operation);})(); update();
    try {await refreshing;} catch (e) {if (current()) {readFailed = true; showError(e); update();} throw e;}
    finally {refreshing = null; if (current()) update();}
  }
  async function readProject() {
    if (!project) return;
    try {
      const {project: p} = await api('GET', `/projects/${encodeURIComponent(project)}`); guard();
      if (p?.project_id !== project || !Number.isSafeInteger(p.version) || p.version < 1 || !Array.isArray(p.repositories) ||
          p.repositories.some(r => typeof r !== 'string')) throw new Error(t('dispatch_project_unavailable'));
      if (projectDoc && (p.version !== projectDoc.version || !equal(p.repositories, projectDoc.repositories) || p.archived !== projectDoc.archived)) {
        preview = null; previewProjectVersion = null; sequence++; reading = false;
      }
      projectDoc = p; projectFailed = false;
      if (autoSelect && !saved.intent && !saved.target && bindings().length === 1) {
        const b = bindings()[0]; saved.target = {repository: b.repository, host: b.host, workspace_id: b.workspace_id}; persist();
      }
      autoSelect = false;
      // Rebuild only the destination choices; focused instruction/advanced drafts remain mounted.
      const values = Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value])); fill();
      for (const [k, value] of Object.entries(values)) inputs[k].value = value;
      projectStatus.replaceChildren(h('p', {}, h('a', {href: `#/project/${project}`}, p.name)),
        p.archived || !expanded() ? h('p', {class: 'muted'}, t(p.archived ? 'dispatch_archived' : 'dispatch_unsupported')) : '');
      update();
    } catch (e) {if (current()) {projectFailed = true; projectStatus.replaceChildren(errorBox(e, {within:projectStatus})); update();} throw e;}
  }
  function loadProject() {
    projectQueue = projectQueue.catch(() => {}).then(() => {guard(); return readProject();});
    return projectQueue;
  }
  fill(); update();
  const refreshAll = async () => {
    const results = await Promise.allSettled([loadProject(), refresh(true)]);
    const failed = results.find(r => r.status === 'rejected'); if (failed) throw failed.reason;
  };
  return {box, update, refresh: project ? refreshAll : refresh, init: project ? refreshAll : refresh};
}
