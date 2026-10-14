// Compact central record creation. Expanded fields never start an agent or select a filesystem path.
import {button} from './ui/components.js';
const equal = (a, b) => a === b || (a && b && typeof a === 'object' && typeof b === 'object'
  && Array.isArray(a) === Array.isArray(b) && Object.keys(a).length === Object.keys(b).length
  && Object.keys(a).every(key => Object.hasOwn(b, key) && equal(a[key], b[key])));
const opId = value => typeof value === 'string' && /^op_[0-9a-f]{32}$/.test(value);
const terminal = value => ['succeeded', 'failed', 'cancelled'].includes(value?.status);
// These create-action admission checks run before inserting an operation. Generic
// HTTP/auth errors and errors from a replay never prove an earlier request absent.
const admissionRefusals = {INVALID_PARAMS: 422, INVALID_TARGET: 422, NAME_TAKEN: 409,
  PROJECT_NOT_FOUND: 404, WORK_ITEM_NOT_FOUND: 404, PROJECT_ARCHIVED: 409,
  WORK_ITEM_ARCHIVED: 409, WRONG_PROJECT: 409};
export function createRecordForm({h, t, api, caps, guard, namespace, kind, projectId = null, showScope = true, onCreated, errorBox, opStatus}) {
  const project = kind === 'project', action = project ? 'project.create' : 'work_item.create';
  const target = project ? {} : {project_id: projectId}, key = `batc.create.${namespace}.${kind}.${projectId || 'root'}`;
  const primary = project ? 'name' : 'title', fields = new Map(), choices = new Map();
  const names = project ? ['name', 'repository', 'description', 'task_project', 'parent_id', 'derived_from']
    : ['title', 'goal', 'request', 'acceptance', 'steps', 'state', 'parent_id', 'derived_from'];
  let saved = {version: 1, values: {}}, busy = false, allowed = false, operation = null, damaged = false;
  try {
    const raw = localStorage.getItem(key);
    if (raw) {
      const value = JSON.parse(raw);
      if (value?.version !== 1 || !value.values || names.some(name => value.values[name] !== undefined && typeof value.values[name] !== 'string')) throw Error();
      saved = value;
      if (saved.intent && (saved.intent.request?.action !== action || !equal(saved.intent.request.target, target)
        || typeof saved.intent.key !== 'string' || !saved.intent.key || saved.intent.operation_id && !opId(saved.intent.operation_id))) throw Error();
    }
  } catch {damaged = true;}
  const status = h('div', {role: 'status'}), receipt = h('div'), scope = h('p', {class: 'muted', hidden: project});
  const unsaved = h('span', {class: 'muted', hidden: true}, t('create_unsaved'));
  const persist = () => {guard(); localStorage.setItem(key, JSON.stringify(saved));};
  const capture = () => Object.fromEntries([...fields].map(([name, input]) => [name, input.value]));
  const changed = () => {
    if (busy || saved.intent || damaged) return;
    saved.values = capture(); unsaved.hidden = false;
    try {persist();} catch (error) {status.replaceChildren(errorBox(error));}
  };
  const field = (name, label, multiline = false, max = 20000) => {
    const input = h(multiline ? 'textarea' : 'input', {maxlength: max, required: name === primary, 'aria-label': t(label)});
    input.value = saved.values[name] || ''; fields.set(name, input); input.addEventListener('input', changed);
    return h('label', {}, t(label), input);
  };
  const select = (name, label, rows) => {
    const input = h('select', {'aria-label': t(label)}); fields.set(name, input); input.addEventListener('change', changed);
    choices.set(name, input);
    for (const [value, text] of rows) input.append(h('option', {value}, text));
    const current = saved.values[name] || '';
    if (current && ![...input.options].some(option => option.value === current)) input.append(h('option', {value: current}, `${current} · ${t('create_source_missing')}`));
    input.value = current;
    return h('label', {}, t(label), input);
  };
  const compact = h('div', {class: 'capture-fields'}, field(primary, project ? 'new_project_name' : 'new_item_title', false, project ? 80 : 120));
  if (project) compact.append(select('repository', 'project_repository_optional', [['', t('project_repository_later')],
    ...[...new Set((caps()?.features?.repository_sync || []).map(row => row.repository))].sort().map(value => [value, value])]));
  const advanced = h('details', {'data-create-advanced': ''}, h('summary', {}, t('create_details')),
    h('div', {class: 'capture-fields'},
      ...(project ? [field('description', 'description', true), field('task_project', 'task_project', false, 120)]
        : [field('goal', 'goal', true), field('request', 'request', true), field('acceptance', 'acceptance', true),
          field('steps', 'create_steps', true), select('state', 'state', [['', t('wi_state_todo')], ['doing', t('wi_state_doing')], ['waiting', t('wi_state_waiting')]])]),
      select('parent_id', 'parent_id', [['', t('create_no_parent')]]),
      select('derived_from', 'derived_from', [['', t('create_no_source')]])),
    h('p', {class: 'muted'}, t(project ? 'create_project_help' : 'create_item_help')));
  const params = () => {
    const value = capture(), result = {[primary]: value[primary].trim()};
    for (const name of names) {
      if (name === primary || !value[name]) continue;
      if (name === 'repository') result.repositories = [value[name]];
      else if (name === 'steps') result.steps = value.steps.split(/\r?\n/).map(text => text.trim()).filter(Boolean).map(text => ({text, done: false}));
      else result[name] = value[name];
    }
    return result;
  };
  const permitted = () => caps()?.scopes?.includes('manage') && caps()?.actions?.find(row => row.action === action)?.allowed !== false;
  const accept = async value => {
    guard(); const intent = saved.intent;
    if (!opId(value?.operation_id) || value.action !== action || value.actor !== caps()?.actor
      || value.idempotency_key !== intent.key || !equal(value.target, target)
      || !equal(value.params, intent.request.params) || !equal(value.preconditions, intent.request.preconditions)
      || intent.operation_id && intent.operation_id !== value.operation_id) throw Error(t('create_invalid_receipt'));
    saved.intent.operation_id = value.operation_id; persist(); operation = value;
    receipt.replaceChildren(opStatus(value), ' ', h('a', {href: `#/op/${value.operation_id}`}, t('create_operation')));
    if (value.status === 'succeeded') {
      // The committed receipt remains in the form; only this frozen submission's draft is cleared.
      saved = {version: 1, values: {}}; persist();
      for (const input of fields.values()) input.value = '';
      unsaved.hidden = true; advanced.open = false;
      await onCreated(value);
    }
  };
  const run = async () => {
    if (busy || damaged || !allowed || !permitted()) return;
    if (!saved.intent && !form.reportValidity()) return;
    busy = true; update(); status.replaceChildren();
    let firstAttempt = false;
    try {
      guard();
      if (!saved.intent) {
        const next = {version: 1, values: capture(), intent: {key: crypto.randomUUID(), request: {action, target, params: params(), preconditions: {}}}};
        localStorage.setItem(key, JSON.stringify(next)); saved = next; firstAttempt = true;
      }
      const intent = saved.intent;
      const result = intent.operation_id ? await api('GET', `/operations/${intent.operation_id}`)
        : await api('POST', '/operations?wait=3', intent.request, intent.key);
      await accept(result.operation);
    } catch (error) {
      try {guard(); if (firstAttempt && saved.intent && !saved.intent.operation_id
          && Object.hasOwn(admissionRefusals, error.code) && admissionRefusals[error.code] === error.status) {
          saved.intent.initialAdmissionRefused = true; persist();
        }
        status.replaceChildren(errorBox(error));} catch { /* retired or storage unavailable */ }
    } finally {busy = false; try {guard(); update();} catch { /* retired */ }}
  };
  const create = button({variant: 'primary', type: 'submit', label: t(project ? 'add_project' : 'add_item')});
  const review = button({variant: 'secondary', hidden: true, label: t('create_edit_request'), onClick: () => {
    guard(); if (busy || !(terminal(operation) || saved.intent?.initialAdmissionRefused)) return;
    try {
      const next = {version: 1, values: saved.values}; localStorage.setItem(key, JSON.stringify(next)); saved = next; operation = null;
      receipt.replaceChildren(); status.replaceChildren(); update();
    } catch (error) {status.replaceChildren(errorBox(error));}
  }});
  const form = h('form', {class: 'panel', 'data-create-record': kind, onsubmit: event => {event.preventDefault(); run();}},
    showScope ? scope : null, compact, advanced, h('div', {class: 'actions'}, create, review, unsaved), status, receipt);
  function update() {
    for (const input of fields.values()) input.disabled = !allowed || !permitted() || busy || !!saved.intent || damaged;
    create.disabled = !allowed || !permitted() || busy || damaged || !!saved.intent?.initialAdmissionRefused || !!saved.intent && terminal(operation);
    create.textContent = t(saved.intent ? 'create_check_original' : project ? 'add_project' : 'add_item');
    review.hidden = !(saved.intent?.initialAdmissionRefused || saved.intent && terminal(operation) && operation.status !== 'succeeded');
    review.disabled = busy || !allowed || !permitted();
  }
  if (damaged) status.replaceChildren(h('p', {class: 'error'}, t('create_invalid_draft')));
  update();
  return {box: form, pending: () => Boolean(saved.intent), setContext({rows, name = '', active = true}) {
    guard(); allowed = active; scope.textContent = t('create_project_scope', {name, id: projectId});
    const flattened = [];
    const walk = list => {for (const row of list || []) {if (!row.archived) flattened.push(row); walk(row.children);}};
    walk(rows);
    for (const field of ['parent_id', 'derived_from']) {
      const input = choices.get(field), current = input.value, id = project ? 'project_id' : 'work_item_id';
      const options = flattened.map(row => [row[id], row.name || row.title]);
      input.replaceChildren(h('option', {value: ''}, t(field === 'parent_id' ? 'create_no_parent' : 'create_no_source')),
        ...options.map(([value, text]) => h('option', {value}, text)));
      if (current && !options.some(([value]) => value === current)) input.append(h('option', {value: current}, `${current} · ${t('create_source_missing')}`));
      input.value = current;
    }
    update();
  }};
}
