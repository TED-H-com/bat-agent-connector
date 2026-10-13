import {disclosure, pageHeader} from './ui/components.js';
import {artifactContent} from "./artifact-content.js";
// Managed capture and exact-revision review are separate central operations.
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const ordered = value => record(value) ? Object.fromEntries(Object.keys(value).sort().map(k => [k, ordered(value[k])])) : Array.isArray(value) ? value.map(ordered) : value;
const stable = value => JSON.stringify(ordered(value));
const equal = (a, b) => stable(a ?? null) === stable(b ?? null);
const digest = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const oid = value => typeof value === 'string' && /^op_[0-9a-f]{32}$/.test(value);
const aid = value => typeof value === 'string' && /^art_[0-9a-f]{32}$/.test(value);
const tid = value => typeof value === 'string' && /^[0-9a-f-]{8,64}$/.test(value);
const commandId = value => typeof value === 'string' && value.length > 0 && value.length <= 256 && !/[\x00-\x1f\x7f-\x9f]/.test(value);
const terminal = op => ['succeeded', 'failed', 'cancelled'].includes(op?.status);
const sha40 = value => typeof value === 'string' && /^[0-9a-f]{40}$/.test(value);
const executions = ['checkpoint.continue', 'integration.handoff', 'session.send', 'session.start', 'repository.continue'];
export const managedCaptureExecution = op => oid(op?.operation_id) && op.status === 'succeeded' && executions.includes(op.action) &&
  (op.action !== 'session.start' || op.result?.started === true && op.result.prompt_sent === true && op.result.message_id === `batc-${op.operation_id}`) &&
  (op.action !== 'repository.continue' || op.result?.message_id === `batc-${op.operation_id}` && sha40(op.result.source_sha));
const safePath = path => typeof path === 'string' && path && new TextEncoder().encode(path).length <= 4096 &&
  !/[\\\x00-\x1f\x7f-\x9f]/.test(path) && path.split('/').every(p => p && p !== '.' && p !== '..' && p.toLowerCase() !== '.git');
const selectorValid = s => record(s) && (equal(Object.keys(s).sort(), ['execution_operation_id']) && oid(s.execution_operation_id) ||
  equal(Object.keys(s).sort(), ['command_id', 'task_id']) && tid(s.task_id) && commandId(s.command_id));
const validRef = ref => aid(ref?.artifact_id) && Number.isSafeInteger(ref.revision) && ref.revision > 0 && ref.revision <= 999999999 && digest(ref.digest);
const refOf = value => ({artifact_id: value.artifact_id, revision: value.revision, digest: value.digest});
const sourceFromOperation = op => op.action === 'session.send' ? (op.external_refs?.resolved_target || op.target) : op.result;
function validPreview(doc, input) {
  return record(doc) && /^acpv_[0-9a-f]{32}$/.test(doc.preview_id) && typeof doc.preview_token === 'string' &&
    doc.preview_token.length > 0 && doc.preview_token.length <= 24576 && digest(doc.fingerprint) && doc.snapshot === false &&
    Number.isFinite(doc.expires_at) && doc.source?.provenance === 'connector_managed' &&
    doc.source.host === input.host && doc.source.session_id === input.session_id &&
    equal(doc.source.selector, input.selector) && doc.relative_path === input.relative_path &&
    typeof doc.source.root === 'string' && typeof doc.source.repository_root === 'string' && record(doc.source.lineage) &&
    digest(doc.evidence?.digest) && /^[0-9a-f]{40}$/.test(doc.evidence.head_sha) &&
    Number.isSafeInteger(doc.evidence.size_bytes) && doc.evidence.size_bytes >= 0;
}
function validArtifact(row, expected) {
  const proof = row?.source;
  return validRef(row) && row.state === 'ready' && record(proof) && proof.kind === 'managed_capture' &&
    oid(proof.operation_id) && row.operation_id === proof.operation_id && digest(proof.fingerprint) &&
    proof.source?.provenance === 'connector_managed' && record(proof.source.lineage) && selectorValid(proof.source.selector) &&
    safePath(proof.relative_path) && proof.evidence?.digest === row.digest && proof.evidence.size_bytes === row.size_bytes &&
    /^[0-9a-f]{40}$/.test(proof.evidence.head_sha) && (!expected || equal(refOf(row), expected));
}
function restore(raw) {
  const saved = {relative_path: '', selector: null, reviewText: ''};
  if (!record(raw)) return saved;
  if (typeof raw.relative_path === 'string') saved.relative_path = raw.relative_path;
  if (selectorValid(raw.selector)) saved.selector = raw.selector;
  if (typeof raw.reviewText === 'string') saved.reviewText = raw.reviewText;
  if (record(raw.preview)) saved.preview = raw.preview;
  for (const kind of ['capture', 'accept']) if (raw[kind]) {
    const intent = raw[kind];
    const usable = record(intent.request) && typeof intent.key === 'string' && intent.key.length > 0 && intent.key.length <= 200 &&
      typeof intent.actor === 'string' && record(intent.request.target) && record(intent.request.params) && record(intent.request.preconditions) &&
      intent.request.action === (kind === 'capture' ? 'artifact.capture.managed' : 'artifact.accept');
    saved[kind] = {request: usable ? intent.request : null, key: usable ? intent.key : null, actor: intent.actor,
      operation_id: oid(intent.operation_id) ? intent.operation_id : null, expected: intent.expected,
      refused: usable && ['PREVIEW_EXPIRED', 'PREVIEW_TOKEN_INVALID', 'PREVIEW_MISMATCH', 'INVALID_PARAMS', 'ARTIFACT_REVISION_MISMATCH', 'ARTIFACT_LINEAGE_UNPROVEN'].includes(intent.refused) ? intent.refused : null};
  }
  return saved;
}

export async function mountArtifactReview({main, h, t, api, caps, guard, onEvents, errorBox, opStatus, storageKey, context, readBrowser}) {
  const container = h('div', {class: 'artifact-review'});
  main.append(container);
  let raw; try {raw = JSON.parse(localStorage.getItem(storageKey));} catch { /* keep a new draft */ }
  let saved = restore(raw), source = null, artifact = null, busy = false, readFailed = false, disposed = false;
  let refreshing = null, submission = null, revision = 0, catalogCursor = null, catalogReading = null, catalogPages = 0;
  let catalogRows = new Map();
  const operations = {capture: null, accept: null}, candidates = new Map(), pages = new Map();
  const notice = h('div', {role: 'status'}), sourceBox = h('div'), evidence = h('div'), outcome = h('div');
  const catalog = h('div'), reviewFacts = h('div'), catalogNotice = h('div');
  const path = h('input', {'aria-label': t('capture_path'), value: saved.relative_path, placeholder: 'results/report.md'});
  const choice = h('select', {'aria-label': t('ar_execution')}, h('option', {value: ''}, t('ar_choose_execution')));
  const customType = h('select', {'aria-label': t('ar_evidence_kind')}, h('option', {value: 'operation'}, t('ar_operation')), h('option', {value: 'command'}, t('ar_command')));
  const customTask = h('input', {'aria-label': t('ar_task_id'), maxlength: 64});
  const customId = h('input', {'aria-label': t('ar_evidence_id'), maxlength: 256});
  const custom = h('div', {class: 'capture-fields', hidden: true}, h('label', {}, t('ar_evidence_kind'), customType),
    h('label', {}, t('ar_task_id'), customTask), h('label', {}, t('ar_evidence_id'), customId));
  const receipt = h('textarea', {'aria-label': t('ar_receipt'), maxlength: 2000, value: saved.reviewText});
  receipt.value = saved.reviewText;
  const reviewed = h('input', {type: 'checkbox', onchange: () => update()});
  const currentView = () => {try {guard(); return !disposed;} catch {return false;}};
  const persist = (required = false) => {guard(); try {localStorage.setItem(storageKey, JSON.stringify(saved)); return true;}
    catch {if (required) throw new Error(t('ar_storage')); return false;} };
  const showError = error => {if (currentView()) notice.replaceChildren(errorBox(error));};
  const supported = () => caps()?.artifacts?.capture?.managed_single_file === true;
  const scope = name => caps()?.scopes?.includes(name);
  const allowed = name => caps()?.actions?.some(a => a.action === name && a.allowed === true);
  const mayCapture = () => supported() && scope('observe') && scope('manage') && allowed('artifact.capture.managed');
  const mayAccept = () => scope('observe') && scope('approve') && allowed('artifact.accept');
  const active = kind => saved[kind] && !saved[kind].refused && (!terminal(operations[kind]) || kind === 'capture' && operations.capture?.status === 'succeeded' && !artifact);
  const frozen = () => Boolean(saved.capture || saved.accept);
  const input = () => ({host: source?.host, session_id: source?.session_id, relative_path: path.value, selector: saved.selector});
  const facts = rows => h('dl', {class: 'kv'}, ...rows.flatMap(([label, value]) => [h('dt', {}, label), h('dd', {}, h('code', {}, value ?? ''))]));
  const proofFacts = proof => facts([[t('capture_source'), `${proof.source.host} / ${proof.source.session_id}`],
    [t('capture_path'), proof.relative_path], [t('capture_root'), proof.source.root], ['HEAD', proof.evidence.head_sha],
    [t('capture_bytes'), String(proof.evidence.size_bytes)], ['SHA-256', proof.evidence.digest]]);
  function renderChoice() {
    choice.replaceChildren(h('option', {value: ''}, t('ar_choose_execution')),
      ...[...candidates].map(([key, entry]) => h('option', {value: key}, entry.label)),
      h('option', {value: 'custom'}, t('ar_exact_evidence')));
    if (saved.selector) {
      const key = stable(saved.selector);
      if (frozen() && !candidates.has(key)) choice.append(h('option', {value: key},
        saved.selector.execution_operation_id ? `${t('ar_operation')} · ${saved.selector.execution_operation_id}`
          : `${t('ar_command')} · ${saved.selector.task_id} · ${saved.selector.command_id}`));
      if (candidates.has(key) || frozen()) choice.value = key;
      else {choice.value = 'custom'; customType.value = saved.selector.task_id ? 'command' : 'operation';
        customTask.value = saved.selector.task_id || context.task_id || ''; customId.value = saved.selector.command_id || saved.selector.execution_operation_id;}
    }
    custom.hidden = choice.value !== 'custom'; customTask.disabled = customType.value !== 'command' || frozen() || busy;
  }
  function changed() {
    if (!currentView() || frozen()) return;
    revision++; saved.relative_path = path.value; saved.preview = null; reviewed.checked = false;
    saved.selector = choice.value === 'custom' ? (customType.value === 'command' ? {task_id: customTask.value.trim(), command_id: customId.value.trim()} : {execution_operation_id: customId.value.trim()})
      : candidates.get(choice.value)?.selector || null;
    custom.hidden = choice.value !== 'custom'; persist(); render();
  }
  for (const field of [path, choice, customType, customTask, customId]) field.addEventListener('input', changed);
  receipt.addEventListener('input', () => {if (!currentView() || saved.accept) return; saved.reviewText = receipt.value; persist(); update();});

  async function captureArtifact(op) {
    const intent = saved.capture, ref = op.result;
    if (!validRef(ref) || ref.digest !== intent.expected?.digest) throw new Error(t('ar_invalid_result'));
    const {artifact: row} = await api('GET', `/artifacts/${ref.artifact_id}/revisions/${ref.revision}`); guard();
    const proof = row?.source;
    if (!validArtifact(row, refOf(ref)) || proof.operation_id !== op.operation_id || proof.fingerprint !== intent.request.preconditions.expected_fingerprint ||
        !equal(proof.source, intent.expected.source) || !equal(proof.evidence, intent.expected.evidence) || proof.relative_path !== intent.expected.relative_path)
      throw new Error(t('ar_invalid_result'));
    artifact = row;
    await loadCatalog();
  }
  async function adopt(kind, op) {
    guard(); const intent = saved[kind];
    if (!intent?.request || !oid(op?.operation_id) || op.actor !== intent.actor || op.actor !== caps().actor ||
        op.idempotency_key !== intent.key || op.action !== intent.request.action || !equal(op.target, intent.request.target) ||
        !equal(op.params, intent.request.params) || !equal(op.preconditions, intent.request.preconditions) ||
        intent.operation_id && intent.operation_id !== op.operation_id) throw new Error(t('ar_invalid_result'));
    intent.operation_id = op.operation_id; persist();
    if (kind === 'capture' && op.status === 'succeeded') await captureArtifact(op);
    if (kind === 'accept' && op.status === 'succeeded') {
      const result = op.result, request = intent.request;
      if (!result || result.operation_id !== op.operation_id || result.actor !== intent.actor || result.meaning !== 'artifact_revision_review' ||
          !equal(refOf(result), {...request.target, digest: request.params.digest}) || result.source_fingerprint !== request.params.source_fingerprint ||
          result.receipt !== request.params.receipt || result.capture_operation_id !== intent.expected?.capture_operation_id ||
          result.source_commit !== intent.expected?.source_commit || !equal(result.lineage, intent.expected?.lineage)) throw new Error(t('ar_invalid_result'));
    }
    operations[kind] = op; readFailed = false; render();
  }
  async function submit(kind) {
    if (busy || refreshing || readFailed || !saved[kind]?.request) return;
    guard(); busy = true; update();
    let finish; submission = new Promise(resolve => {finish = resolve;});
    try {
      persist(true); const intent = saved[kind];
      const result = intent.operation_id ? await api('GET', `/operations/${intent.operation_id}`)
        : await api('POST', '/operations?wait=3', intent.request, intent.key);
      guard(); await adopt(kind, result.operation); notice.replaceChildren();
    } catch (error) {
      if (!currentView()) return;
      const safe = kind === 'capture' ? ['PREVIEW_EXPIRED', 'PREVIEW_TOKEN_INVALID', 'PREVIEW_MISMATCH', 'INVALID_PARAMS']
        : ['INVALID_PARAMS', 'ARTIFACT_REVISION_MISMATCH', 'ARTIFACT_LINEAGE_UNPROVEN'];
      if (!saved[kind].operation_id && error.status >= 400 && error.status < 500 && safe.includes(error.code)) saved[kind].refused = error.code;
      persist(); showError(error);
      if (kind === 'capture' && error.status === 403) notice.append(h('p', {class: 'muted'}, t('ar_original_credential')));
    } finally {busy = false; finish(); submission = null; if (currentView()) render();}
  }
  const preview = h('button', {class: 'secondary', onclick: async () => {
    if (busy || frozen() || !source || !scope('observe') || !supported() || !safePath(path.value) || !selectorValid(saved.selector)) return;
    guard(); const ticket = ++revision, fixed = structuredClone(input()); busy = true; saved.preview = null; reviewed.checked = false; update();
    try {
      const {selector, ...fields} = fixed;
      const {preview: doc} = await api('POST', '/artifact-managed-capture-previews', {...fields, ...selector}); guard();
      if (ticket !== revision) return;
      if (!validPreview(doc, fixed)) throw new Error(t('capture_invalid_preview'));
      saved.preview = doc; readFailed = false; persist(); notice.replaceChildren();
    } catch (error) {if (ticket === revision) showError(error);}
    finally {busy = false; if (currentView()) render();}
  }}, t('capture_preview'));
  const capture = h('button', {class: 'primary', onclick: () => {
    if (busy || refreshing || readFailed || !mayCapture() || saved.capture?.operation_id || saved.capture?.refused) return;
    guard(); if (!saved.capture) {
      const doc = saved.preview;
      if (!doc || !reviewed.checked || doc.expires_at * 1000 <= Date.now() || !validPreview(doc, input())) return;
      saved.capture = {key: crypto.randomUUID(), actor: caps().actor,
        request: {action: 'artifact.capture.managed', target: {preview_id: doc.preview_id}, params: {preview_token: doc.preview_token}, preconditions: {expected_fingerprint: doc.fingerprint}},
        expected: {digest: doc.evidence.digest, evidence: doc.evidence, source: doc.source, relative_path: doc.relative_path}};
    }
    submit('capture');
  }}, t('capture_save'));
  const accept = h('button', {class: 'primary', onclick: () => {
    if (busy || refreshing || readFailed || !artifact || !mayAccept() || saved.accept?.operation_id || saved.accept?.refused || !receipt.value.trim() || [...receipt.value].length > 2000) return;
    guard(); if (!saved.accept) saved.accept = {key: crypto.randomUUID(), actor: caps().actor,
      request: {action: 'artifact.accept', target: {artifact_id: artifact.artifact_id, revision: artifact.revision},
        params: {digest: artifact.digest, source_fingerprint: artifact.source.fingerprint, receipt: receipt.value}, preconditions: {}},
      expected: {capture_operation_id: artifact.source.operation_id, source_commit: artifact.source.evidence.head_sha, lineage: artifact.source.source.lineage}};
    submit('accept');
  }}, t('ar_accept'));
  const check = h('button', {class: 'secondary', onclick: () => refresh(true).catch(showError)}, t('ar_check'));
  const reload = h('button', {class: 'secondary', onclick: async () => {
    if (busy || refreshing || frozen()) return;
    busy = true; update();
    try {await loadSource(); guard(); readFailed = false; notice.replaceChildren();}
    catch (error) {showError(error);}
    finally {busy = false; if (currentView()) render();}
  }}, t('ar_refresh_sources'));
  const reset = h('button', {class: 'secondary', onclick: async () => {
    guard(); if (busy || refreshing || readFailed || active('capture') || active('accept')) return;
    saved = {relative_path: path.value, selector: null, reviewText: ''}; artifact = source = null;
    candidates.clear(); pages.clear();
    operations.capture = operations.accept = null; reviewed.checked = false; receipt.value = ''; revision++;
    persist(); notice.replaceChildren(); renderChoice(); busy = true; render();
    try {await loadSource();}
    catch (error) {showError(error);}
    finally {busy = false; if (currentView()) render();}
  }}, t('capture_new'));
  const newReview = h('button', {class: 'secondary', onclick: () => {
    guard(); if (busy || refreshing || readFailed || active('accept')) return;
    saved.accept = null; operations.accept = null; persist(); render();
  }}, t('ar_new_review'));
  const moreExecutions = h('button', {class: 'secondary', onclick: () => loadExecutions(true).catch(showError)}, t('ar_more_executions'));
  const content = artifactContent({h, t, api, guard, getArtifact: () => artifact, canRead: () => scope('observe') && !readFailed, validArtifact, readBrowser});
  const reviewPanel = h('section', {class: 'panel', 'data-artifact-accept': ''}, h('h2', {}, t('ar_review_title')), reviewFacts,
    content.box, h('p', {class: 'muted'}, t('ar_accept_help')), h('label', {}, t('ar_receipt'), receipt),
    h('p', {class: 'muted'}, t('ar_approve_scope')), h('div', {class: 'actions'}, accept, newReview));
  const capturePanel = h('section', {class: 'panel', 'data-managed-capture': ''}, h('h2', {}, t('ar_capture_title')), sourceBox,
    h('div', {class: 'capture-fields'}, h('label', {}, t('ar_execution'), choice), h('label', {}, t('capture_path'), path)), custom,
    h('div', {class: 'actions'}, reload, moreExecutions, preview), evidence,
    h('label', {class: 'capture-choice'}, reviewed, t('capture_review')), h('div', {class: 'actions'}, capture, reset));
  const moreArtifacts = h('button', {class: 'secondary', hidden: true, onclick: () => loadCatalog(true).catch(e => catalogNotice.replaceChildren(errorBox(e)))}, t('more'));
  const catalogPanel = h('section', {class: 'panel'}, h('h2', {}, t('ar_catalog')), catalog, catalogNotice, h('div', {class: 'actions'}, moreArtifacts));
  container.append(pageHeader({title: t('ar_title')}), disclosure({label: t('global_details'), body: h('p', {}, t('ar_help'))}), capturePanel, reviewPanel,
    h('div', {class: 'actions'}, check), outcome, notice, catalogPanel);

  function update() {
    content.update();
    const fixed = busy || frozen();
    for (const field of [path, choice, customType, customId]) field.disabled = fixed;
    customTask.disabled = fixed || customType.value !== 'command';
    preview.disabled = busy || frozen() || !source || !scope('observe') || !supported() || !safePath(path.value) || !selectorValid(saved.selector);
    reviewed.disabled = busy || Boolean(saved.capture) || !saved.preview || saved.preview.expires_at * 1000 <= Date.now();
    reviewed.closest('label').hidden = Boolean(saved.capture) || !saved.preview;
    capture.hidden = Boolean(saved.capture?.operation_id || saved.capture?.refused);
    capture.textContent = saved.capture ? t('capture_check') : t('capture_save');
    capture.disabled = busy || Boolean(refreshing) || readFailed || !mayCapture() || !source || Boolean(saved.capture && !saved.capture.request) ||
      (!saved.capture && (!saved.preview || !reviewed.checked || saved.preview.expires_at * 1000 <= Date.now()));
    receipt.disabled = busy || Boolean(saved.accept) || !artifact;
    accept.hidden = Boolean(saved.accept?.operation_id || saved.accept?.refused);
    accept.textContent = saved.accept ? t('ar_check_accept') : t('ar_accept');
    accept.disabled = busy || Boolean(refreshing) || readFailed || !artifact || !mayAccept() || !receipt.value.trim() || [...receipt.value].length > 2000 || Boolean(saved.accept && !saved.accept.request);
    check.hidden = !saved.capture?.operation_id && !saved.accept?.operation_id && context.kind !== 'artifact';
    check.disabled = busy || Boolean(refreshing);
    reset.hidden = !saved.capture; reset.disabled = busy || Boolean(refreshing) || readFailed || Boolean(active('capture') || active('accept'));
    newReview.hidden = !saved.accept; newReview.disabled = busy || Boolean(refreshing) || readFailed || Boolean(active('accept'));
    moreExecutions.disabled = busy || frozen();
    reload.disabled = busy || frozen();
    moreExecutions.hidden = ![...pages.values()].some(value => value !== null);
  }
  function render() {
    if (!currentView()) return;
    capturePanel.hidden = !['session', 'task', 'operation'].includes(context.kind);
    evidence.replaceChildren();
    if (!mayCapture()) evidence.append(h('p', {class: 'muted'}, t('capture_scope_manage')));
    if (saved.preview && validPreview(saved.preview, input())) {
      const description = [proofFacts(saved.preview), h('p', {class: 'muted'}, saved.capture ? t('capture_fixed')
        : saved.preview.expires_at * 1000 <= Date.now() ? t('capture_expired') : t('capture_single_file'))];
      evidence.append(...(artifact ? [h('details', {}, h('summary', {}, t('ar_original_preview')), ...description)] : description));
    }
    reviewPanel.hidden = !artifact && context.kind !== 'artifact' && !saved.accept;
    reviewFacts.replaceChildren();
    if (artifact) reviewFacts.append(facts([[t('ar_revision'), `${artifact.artifact_id} · r${artifact.revision}`]]),
      proofFacts(artifact.source), h('details', {}, h('summary', {}, t('ar_lineage')), h('pre', {class: 'pre'}, JSON.stringify(artifact.source.source.lineage, null, 2))));
    outcome.replaceChildren();
    for (const kind of ['capture', 'accept']) if (saved[kind]) {
      const intent = saved[kind], op = operations[kind];
      outcome.append(h('p', {}, t(kind === 'capture' ? 'ar_capture_title' : 'ar_review_title'), ': ', op ? opStatus(op) : t('capture_unknown'), ' ',
        intent.operation_id ? h('a', {href: `#/op/${intent.operation_id}`}, intent.operation_id) : null));
      if (intent.refused) outcome.append(h('p', {class: 'muted'}, intent.refused));
    }
    if (artifact) outcome.append(h('p', {'data-artifact-ready': ''}, t('capture_saved'), ' ', `${artifact.artifact_id} · r${artifact.revision}`));
    if (operations.accept?.status === 'succeeded') outcome.append(h('p', {'data-artifact-accepted': ''}, t('ar_recorded')));
    update();
  }
  async function loadExecutions(more = false) {
    if (!source || frozen()) return;
    const results = await Promise.all(executions.map(async action => {
      if (more && pages.get(action) === null) return;
      const before = more ? pages.get(action) : null;
      return {action, data: await api('GET', `/operations?status=succeeded&action=${action}&limit=50${before ? `&before=${before}` : ''}`)};
    })); guard();
    if (frozen()) return;
    for (const result of results.filter(Boolean)) {
      pages.set(result.action, result.data.next_before ?? null);
      for (const op of result.data.operations || []) {
        const bound = sourceFromOperation(op);
        if (managedCaptureExecution(op) && bound?.host === source.host && bound.session_id === source.session_id) {
          const selector = {execution_operation_id: op.operation_id}; candidates.set(stable(selector), {selector, label: `${op.action} · ${op.operation_id}`});
        }
      }
    }
    renderChoice(); update();
  }
  async function loadSource() {
    let task, data, bound;
    if (context.kind === 'task') {
      task = (await api('GET', `/tasks/${encodeURIComponent(context.task_id)}`)).task;
      if (task?.task_id !== context.task_id) throw new Error(t('ar_source_unavailable'));
      bound = {host: task.host, session_id: task.session_id}; customTask.value = task.task_id;
    } else if (context.kind === 'operation') {
      const op = (await api('GET', `/operations/${context.operation_id}`)).operation;
      if (op?.operation_id !== context.operation_id || !managedCaptureExecution(op)) throw new Error(t('ar_source_unavailable'));
      bound = sourceFromOperation(op);
      if (!saved.selector) saved.selector = {execution_operation_id: context.operation_id};
    } else if (context.kind === 'session') bound = context;
    else return;
    guard();
    if (!bound?.host || !bound?.session_id) throw new Error(t('ar_source_unavailable'));
    data = await api('GET', `/sessions/${encodeURIComponent(bound.host)}/${encodeURIComponent(bound.session_id)}`); guard();
    const row = data.session;
    // Capture reads a proven source; session-control availability is caller-specific.
    // The central preview checks current lineage, folder and exact file evidence.
    if (row?.host !== bound.host || row.session_id !== bound.session_id || row.provenance !== 'connector_managed') throw new Error(t('ar_source_unavailable'));
    source = {host: row.host, session_id: row.session_id};
    sourceBox.replaceChildren(facts([[t('capture_source'), `${row.host} / ${row.session_id}`]]));
    if (!saved.capture) {
      const taskIds = task ? [task.task_id] : [...new Set((data.relations_summary || []).filter(r => r.status !== 'closed').map(r => r.execution_id))].filter(tid);
      const tasks = task ? [task] : await Promise.all(taskIds.slice(0, 20).map(async id => (await api('GET', `/tasks/${id}`)).task)); guard();
      for (const current of tasks) if (current?.host === source.host && current.session_id === source.session_id) {
        for (const cmd of current.commands || []) if (cmd.task_id === current.task_id && cmd.session_id === source.session_id && cmd.kind === 'send' && ['accepted', 'settled'].includes(cmd.status) && commandId(cmd.command_id)) {
          const selector = {task_id: current.task_id, command_id: cmd.command_id};
          candidates.set(stable(selector), {selector, label: `${t('ar_command')} · ${current.task_id} · ${cmd.command_id}`});
        }
      }
      await loadExecutions();
    }
    renderChoice(); render();
  }
  async function loadExactArtifact() {
    const expected = context.kind === 'artifact' ? {artifact_id: context.artifact_id, revision: context.revision} : null;
    if (!expected) return;
    if (!aid(expected.artifact_id) || !Number.isSafeInteger(expected.revision) || expected.revision < 1 || expected.revision > 999999999) throw new Error(t('ar_invalid_result'));
    const {artifact: row} = await api('GET', `/artifacts/${expected.artifact_id}/revisions/${expected.revision}`); guard();
    if (!validArtifact(row) || row.artifact_id !== expected.artifact_id || row.revision !== expected.revision) throw new Error(t('ar_invalid_result'));
    artifact = row; render();
  }
  async function loadCatalog(more = false) {
    if (catalogReading) {await catalogReading; guard(); return loadCatalog(more);}
    if (more && !catalogCursor) return;
    catalogReading = (async () => {
      let cursor = more ? catalogCursor : null, readPages = 0;
      const rows = more ? new Map(catalogRows) : new Map(), seen = new Set();
      // Refresh every loaded page from its first cursor; retain the old display until all settle.
      for (let index = 0; index < (more ? 1 : Math.max(1, catalogPages)); index++) {
        if (seen.has(cursor)) throw new Error(t('ar_invalid_result'));
        seen.add(cursor);
        const data = await api('GET', `/artifacts?limit=30${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`); guard();
        if (!Array.isArray(data.artifacts) || data.next_cursor != null && (typeof data.next_cursor !== 'string' || !data.next_cursor))
          throw new Error(t('ar_invalid_result'));
        for (const row of data.artifacts.map(a => a.revision).filter(row => validArtifact(row))) rows.set(`${row.artifact_id}:${row.revision}`, row);
        cursor = data.next_cursor ?? null; readPages++;
        if (!cursor) break;
      }
      guard(); catalogRows = rows; catalogCursor = cursor; catalogPages = (more ? catalogPages : 0) + readPages;
      catalog.replaceChildren(...[...rows.values()].map(row => h('div', {class: 'row'}, h('a', {class: 'title', href: `#/artifact-review/artifact/${row.artifact_id}/${row.revision}`},
        `${row.display_name || row.artifact_id} · r${row.revision}`), h('code', {}, row.digest))));
      moreArtifacts.hidden = !catalogCursor; catalogNotice.replaceChildren();
      if (!rows.size) catalog.append(h('p', {class: 'muted'}, t('ar_no_artifacts')));
    })(); moreArtifacts.disabled = true;
    try {await catalogReading;}
    catch (error) {if (currentView()) catalogNotice.replaceChildren(errorBox(error)); throw error;}
    finally {catalogReading = null; moreArtifacts.disabled = false;}
  }
  async function refresh(fresh = false) {
    if (submission) {await submission; guard();}
    if (refreshing) {await refreshing; if (fresh) return refresh(true); return;}
    refreshing = (async () => {
      const reads = [];
      if (saved.capture?.operation_id) reads.push(api('GET', `/operations/${saved.capture.operation_id}`).then(data => adopt('capture', data.operation)));
      else if (context.kind === 'artifact') reads.push(loadExactArtifact());
      if (saved.accept?.operation_id) reads.push(api('GET', `/operations/${saved.accept.operation_id}`).then(data => adopt('accept', data.operation)));
      const outcomes = await Promise.allSettled(reads);
      for (const result of outcomes) if (result.status === 'rejected') throw result.reason;
      guard(); readFailed = false; render();
    })(); update();
    try {await refreshing;} catch (error) {if (currentView()) {readFailed = true; showError(error);} throw error;}
    finally {refreshing = null; if (currentView()) update();}
  }
  const off = onEvents(async event => {
    if (!currentView()) return;
    const reads = [];
    if (event.resource_id === saved.capture?.operation_id || event.resource_id === saved.accept?.operation_id ||
        event.resource_id === artifact?.artifact_id || context.kind === 'artifact' && event.resource_id === context.artifact_id) reads.push(refresh(true));
    if (event.resource_type === 'artifact') reads.push(loadCatalog());
    const settled = await Promise.allSettled(reads);
    for (const result of settled) if (result.status === 'rejected') throw result.reason;
  });
  if (saved.capture?.expected?.source) source = {host: saved.capture.expected.source.host, session_id: saved.capture.expected.source.session_id};
  renderChoice();
  render();
  const initial = [];
  if (!saved.capture) initial.push(loadSource());
  if (saved.capture?.operation_id || saved.accept?.operation_id || context.kind === 'artifact') initial.push(refresh());
  initial.push(loadCatalog().catch(error => {if (currentView()) catalogNotice.replaceChildren(errorBox(error));}));
  const results = await Promise.allSettled(initial);
  for (const result of results) if (result.status === 'rejected') {readFailed = true; showError(result.reason);}
  // An accepted capture may outlive its source. Restore display evidence without a live source read.
  if (!source && saved.capture?.expected?.source) {source = {host: saved.capture.expected.source.host, session_id: saved.capture.expected.source.session_id}; renderChoice();}
  render();
  const timer = setInterval(() => {
    if (!currentView()) return;
    update();
    if ((saved.capture?.operation_id && active('capture')) || (saved.accept?.operation_id && active('accept'))) refresh().catch(showError);
  }, 1000);
  return () => {disposed = true; clearInterval(timer); content.dispose(); off();};
}
