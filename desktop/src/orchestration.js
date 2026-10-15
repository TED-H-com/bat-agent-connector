import {skeleton} from './ui/components.js';
import {operationStatusLabel} from './global-labels.js';
// Explicit orchestration intents. Accepted recovery only reads the original operation.
const object = v => v && typeof v === 'object' && !Array.isArray(v);
const text = (v, max = 256) => typeof v === 'string' && v.trim().length > 0 && v.length <= max;
const opId = v => typeof v === 'string' && /^op_[0-9a-f]{32}$/.test(v);
const equal = (a, b) => a === b || (Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => equal(v, b[i]))) ||
  (object(a) && object(b) && Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(k => equal(a[k], b[k])));
const terminal = op => ['succeeded', 'failed', 'cancelled'].includes(op?.status);
export const orchestrationActions = {relay: 'session.relay', planner: 'fanout.plan', items: 'fanout.start', failover: 'session.failover'};
const scopes = mode => mode === 'relay' ? ['observe', 'operate'] : mode === 'failover' ? ['observe', 'start', 'operate'] : ['observe', 'start'];
const sessionMode = mode => ['relay', 'failover'].includes(mode);
const managed = row => row?.api_access === 'managed' && row.provenance === 'connector_managed' &&
  row.stale === false && row.scope_status === 'current' && ['claude', 'codex'].includes(row.agent_kind);
const failoverSource = row => managed(row) && row.agent_kind === 'claude' && row.streaming === false &&
  !row.task_id && Array.isArray(row.relations) && row.relations.length === 0;
const admissionRefusals = new Set(['TIER_DISABLED', 'START_WORKTREE_REQUIRED', 'FANOUT_CAP']);
const withinBodyLimit = r => new TextEncoder().encode(JSON.stringify(r)).length <= 200000; // central HTTP bound, including CJK/JSON encoding
function validRequest(r, mode) {
  if (!object(r) || r.action !== orchestrationActions[mode] || !object(r.target) || !object(r.params) ||
      !object(r.preconditions) || Object.keys(r.preconditions).length || Object.keys(r.target).length !== 2 ||
      !text(r.target.host) || !text(r.target[sessionMode(mode) ? 'session_id' : 'workspace']) || !withinBodyLimit(r)) return false;
  const p = r.params, keys = Object.keys(p);
  if (mode === 'relay') return keys.length === 3 && text(p.message, 12000) && typeof p.queue === 'boolean' && p.start_if_missing === false;
  if (mode === 'planner') return keys.length === 2 && text(p.message, 12000) && Number.isInteger(p.max_items) && p.max_items >= 1 && p.max_items <= 16;
  if (mode === 'failover') return keys.every(k => ['tail_messages', 'instructions'].includes(k)) && p.tail_messages === 12 &&
    (!('instructions' in p) || text(p.instructions, 4000));
  return keys.length === 2 && ['claude', 'codex'].includes(p.agent) && Array.isArray(p.plan) && p.plan.length >= 1 && p.plan.length <= 16 &&
    p.plan.every((v, i) => object(v) && Object.keys(v).length === 3 && v.index === i + 1 && text(v.title, 200) && text(v.prompt, 19000));
}
export function orchestrationPanel({h, t, api, caps, guard, ready, errorBox, opStatus, storageKey, mode, context = {}}) {
  if (!Object.hasOwn(orchestrationActions, mode)) throw new Error('Unknown orchestration form');
  let raw;
  try {raw = JSON.parse(localStorage.getItem(storageKey));} catch { /* empty draft */ }
  let saved = {host: typeof raw?.host === 'string' ? raw.host : context.host || '',
    selection: typeof raw?.selection === 'string' ? raw.selection : context.session_id || '',
    message: typeof raw?.message === 'string' ? raw.message : '', queue: raw?.queue === true,
    agent: raw?.agent === 'claude' ? 'claude' : 'codex', max_items: Number.isInteger(raw?.max_items) ? raw.max_items : 3,
    items: Array.isArray(raw?.items) && raw.items.length >= 1 && raw.items.length <= 16 ? raw.items.map(v => ({title: typeof v?.title === 'string' ? v.title : '', prompt: typeof v?.prompt === 'string' ? v.prompt : ''})) : [{title: '', prompt: ''}]};
  if (raw?.intent) {
    const valid = validRequest(raw.intent.request, mode) && text(raw.intent.key, 200);
    saved.intent = {request: valid ? raw.intent.request : null, key: valid ? raw.intent.key : null,
      operation_id: opId(raw.intent.operation_id) ? raw.intent.operation_id : null,
      refused: !raw.intent.operation_id && admissionRefusals.has(raw.intent.refused) ? raw.intent.refused : null};
    if (valid) {
      const r = raw.intent.request;
      Object.assign(saved, {host: r.target.host, selection: r.target.session_id || r.target.workspace,
        message: r.params.message || r.params.instructions || '', queue: r.params.queue === true,
        max_items: r.params.max_items || 3, agent: r.params.agent || 'codex', items: r.params.plan || saved.items});
    }
  }
  let operation = null, busy = false, submission = null, refreshing = null, readFailed = false, rows = [], selected = null;
  let discovering = false, discovery = 0, discoveredHost = '', reviewed = false;
  const current = () => {try {guard(); return true;} catch {return false;}};
  const persist = () => {guard(); localStorage.setItem(storageKey, JSON.stringify(saved));};
  const allowed = () => scopes(mode).every(s => caps()?.scopes?.includes(s)) && caps()?.actions?.some(a => a.action === orchestrationActions[mode] && a.allowed === true);
  const hostAllowed = () => caps()?.hosts?.some(row => row.host === saved.host && row.writes === true && (mode === 'relay' || row.orchestrate === true));
  const proofOf = op => op?.result || op?.external_refs?.[mode === 'relay' ? 'relay_result' : mode === 'failover' ? 'failover_result' : 'fanout_result'] || {};
  // Planner success flattens one start result (started=true); the aggregate list remains in refs.
  const childrenOf = op => {
    const rows = op?.external_refs?.fanout_result?.started ?? proofOf(op).started;
    return Array.isArray(rows) ? rows : [];
  };
  const mayReplace = () => {
    if (!terminal(operation)) return false;
    const proof = proofOf(operation), statuses = [proof.child_operation_status, ...childrenOf(operation).map(row => row.operation_status)].filter(Boolean);
    return !(operation.steps || []).some(step => ['started', 'uncertain'].includes(step.status)) &&
      statuses.every(status => ['succeeded', 'failed', 'cancelled'].includes(status));
  };
  const selectedAllowed = () => discoveredHost === saved.host && (sessionMode(mode) ? selected?.host === saved.host && selected.session_id === saved.selection &&
    (mode === 'failover' ? failoverSource(selected) : managed(selected)) : rows.some(row => row.workspace_id === saved.selection));
  const status = h('div', {role: 'status'}), result = h('div', {'data-orchestration-result': ''}), discoveryStatus = h('p', {class: 'muted', role: 'status'}), identity = h('p', {class: 'muted'});
  const host = h('select', {'aria-label': t('host')}, h('option', {value: ''}, t('start_choose_host')),
    ...(caps()?.hosts || []).map(row => h('option', {value: row.host}, row.host)));
  if (saved.host && ![...host.options].some(o => o.value === saved.host)) host.append(h('option', {value: saved.host}, saved.host));
  const selection = h('select', {'aria-label': t(sessionMode(mode) ? 'orch_session' : 'start_workspace')});
  const message = h('textarea', {'aria-label': t(mode === 'failover' ? 'orch_instructions' : 'orch_message'), rows: 5, maxlength: mode === 'failover' ? 4000 : 12000});
  const queue = h('input', {type: 'checkbox'}), agent = h('select', {'aria-label': t('start_agent')}, h('option', {value: 'codex'}, 'Codex'), h('option', {value: 'claude'}, 'Claude'));
  const maximum = h('input', {type: 'number', min: 1, max: 16, 'aria-label': t('orch_max_items')});
  const review = h('input', {type: 'checkbox'}), items = h('div', {'data-orchestration-items': ''});
  const label = (key, input) => h('label', {}, t(key), input);
  const inputs = {host, selection, message, queue, agent, maximum};
  function fill() {host.value = saved.host; selection.value = saved.selection; message.value = saved.message; queue.checked = saved.queue; agent.value = saved.agent; maximum.value = saved.max_items;}
  const showError = error => {if (current()) status.replaceChildren(errorBox(error, {within:status}));};
  function renderSelection() {
    const id = row => sessionMode(mode) ? row.session_id : row.workspace_id;
    selection.replaceChildren(h('option', {value: ''}, t(sessionMode(mode) ? 'orch_choose_session' : 'start_choose_workspace')),
      ...rows.map(row => h('option', {value: id(row)}, (row.title || row.name) && (row.title || row.name) !== id(row) ? `${row.title || row.name} · ${id(row)}` : id(row))));
    if (saved.selection && ![...selection.options].some(o => o.value === saved.selection)) selection.append(h('option', {value: saved.selection}, saved.selection));
    selection.value = saved.selection;
  }
  async function readSelected(serial = discovery) {
    selected = null;
    if (!sessionMode(mode) || !saved.selection || saved.intent) return;
    const fixed = {host: saved.host, session_id: saved.selection};
    const doc = await api('GET', `/sessions/${encodeURIComponent(fixed.host)}/${encodeURIComponent(fixed.session_id)}`); guard();
    if (serial !== discovery || saved.intent || saved.host !== fixed.host || saved.selection !== fixed.session_id) return;
    if (doc?.session?.host !== fixed.host || doc.session.session_id !== fixed.session_id || !Array.isArray(doc.relations_summary)) throw new Error(t('orch_discovery_failed'));
    selected = {...doc.session, relations: doc.relations_summary};
  }
  async function discover() {
    if (!current() || saved.intent || !caps()?.scopes?.includes('observe') || !saved.host) return;
    const mine = ++discovery, expectedHost = saved.host;
    discovering = true; selected = null; discoveredHost = ''; rows = []; renderSelection(); update();
    discoveryStatus.replaceChildren(skeleton(2));
    try {
      const path = sessionMode(mode) ? `/sessions?host=${encodeURIComponent(expectedHost)}&access=managed&provenance=connector_managed&limit=200` : `/workspaces?host=${encodeURIComponent(expectedHost)}&limit=200`;
      const doc = await api('GET', path); guard();
      if (mine !== discovery || expectedHost !== saved.host || saved.intent) return;
      const list = doc[sessionMode(mode) ? 'sessions' : 'workspaces'], id = sessionMode(mode) ? 'session_id' : 'workspace_id';
      if (!Array.isArray(list) || list.length > 200 || list.some(row => row?.host !== expectedHost || !text(row?.[id])) ||
          new Set(list.map(row => row[id])).size !== list.length || (!sessionMode(mode) && (!object(doc.errors) || Object.keys(doc.errors).length || typeof doc.has_more !== 'boolean')) ||
          (sessionMode(mode) && doc.next_cursor !== null && !text(doc.next_cursor, 8192))) throw new Error(t('orch_discovery_failed'));
      rows = sessionMode(mode) ? list.filter(row => mode === 'failover' ? failoverSource(row) : managed(row)) : list;
      discoveredHost = expectedHost; renderSelection(); await readSelected(mine); guard();
      if (mine === discovery) readFailed = false;
      if (mine === discovery) discoveryStatus.textContent = t(doc.next_cursor || doc.has_more ? 'orch_truncated' : rows.length ? 'orch_selection_help' : 'orch_empty');
    } catch (error) {if (current() && mine === discovery) {discoveredHost = ''; selected = null; discoveryStatus.textContent = t('orch_discovery_failed'); showError(error);} throw error;}
    finally {if (current() && mine === discovery) {discovering = false; update();}}
  }
  const reload = h('button', {class: 'secondary', onclick: () => discover().catch(() => {})}, t('orch_reload'));
  const changed = () => {reviewed = false; review.checked = false; try {persist();} catch (e) {showError(e);} update();};
  for (const [key, input] of Object.entries(inputs)) input.addEventListener(['host', 'selection', 'queue', 'agent'].includes(key) ? 'change' : 'input', () => {
    if (!current() || busy || saved.intent) {fill(); return;}
    if (key === 'maximum') saved.max_items = Number(input.value);
    else saved[key] = key === 'queue' ? input.checked : input.value;
    if (key === 'host') {saved.selection = ''; selected = null; discovering = false; rows = []; discoveredHost = ''; ++discovery; renderSelection();}
    changed();
    if (key === 'host') discover().catch(() => {});
    if (key === 'selection') {
      const mine = ++discovery; discovering = true; selected = null; update();
      readSelected(mine).catch(error => {if (current() && mine === discovery) showError(error);}).finally(() => {if (current() && mine === discovery) {discovering = false; update();}});
    }
  });
  function renderItems() {
    items.replaceChildren(...saved.items.map((item, index) => {
      const title = h('input', {'aria-label': t('orch_item_title', {n: index + 1}), maxlength: 200}),
        prompt = h('textarea', {'aria-label': t('orch_item_prompt', {n: index + 1}), rows: 4, maxlength: 19000});
      title.value = item.title; prompt.value = item.prompt;
      for (const [key, el] of [['title', title], ['prompt', prompt]]) el.addEventListener('input', () => {
        if (!current() || saved.intent || busy) {el.value = item[key]; return;} item[key] = el.value; changed();
      });
      const remove = h('button', {class: 'secondary', onclick: () => {
        if (!current() || saved.intent || busy || saved.items.length < 2) return; saved.items.splice(index, 1); renderItems(); changed();
      }}, t('orch_remove_item', {n: index + 1}));
      return h('section', {class: 'orch-item'}, h('h2', {}, t('orch_item', {n: index + 1})), label('orch_title_label', title),
        label('orch_prompt_label', prompt), h('div', {class: 'actions'}, remove));
    }));
  }
  const add = h('button', {class: 'secondary', onclick: () => {
    if (!current() || saved.intent || busy || saved.items.length >= 16) return; saved.items.push({title: '', prompt: ''}); renderItems(); changed();
  }}, t('orch_add_item'));
  review.addEventListener('change', () => {reviewed = review.checked; update();});
  function request() {
    const target = {host: saved.host, [sessionMode(mode) ? 'session_id' : 'workspace']: saved.selection};
    const params = mode === 'relay' ? {message: saved.message, queue: saved.queue, start_if_missing: false} : mode === 'planner' ? {message: saved.message, max_items: saved.max_items} : mode === 'items' ?
      {agent: saved.agent, plan: saved.items.map((item, i) => ({index: i + 1, title: item.title, prompt: item.prompt}))} :
      {tail_messages: 12, ...(saved.message !== '' ? {instructions: saved.message} : {})};
    return {action: orchestrationActions[mode], target, params, preconditions: {}};
  }
  function accept(candidate) {
    guard(); const intent = saved.intent;
    if (!intent?.request || !intent.key || !opId(candidate?.operation_id) || candidate.actor !== caps()?.actor || candidate.idempotency_key !== intent.key ||
        !equal({action: candidate.action, target: candidate.target, params: candidate.params, preconditions: candidate.preconditions}, intent.request) ||
        (intent.operation_id && intent.operation_id !== candidate.operation_id)) throw new Error(t('orch_invalid_result'));
    const proof = proofOf(candidate);
    if (!object(proof) || (proof.host !== undefined && proof.host !== intent.request.target.host) ||
        (mode === 'relay' && proof.session_id !== undefined && proof.session_id !== intent.request.target.session_id) ||
        (mode === 'failover' && proof.old_session_id !== undefined && proof.old_session_id !== intent.request.target.session_id) ||
        (proof.started !== undefined && !(mode === 'planner' && proof.started === true) &&
          (!Array.isArray(proof.started) || proof.started.length > 16 || proof.started.some(row => !object(row)))) ||
        !Array.isArray(candidate.steps)) throw new Error(t('orch_invalid_result'));
    operation = candidate; intent.operation_id = candidate.operation_id; readFailed = false; persist(); status.replaceChildren(); update();
  }
  const apply = h('button', {class: 'primary', onclick: async () => {
    if (!current() || busy || readFailed || !ready() || !allowed() || saved.intent?.operation_id || saved.intent?.refused) return;
    if (!saved.intent) {
      if (discovering || !hostAllowed() || !selectedAllowed() || !validRequest(request(), mode) || !reviewed) return;
      saved.intent = {request: request(), key: crypto.randomUUID(), operation_id: null};
    }
    if (!saved.intent.request || !saved.intent.key) return;
    try {persist();} catch (e) {showError(e); update(); return;}
    busy = true; update(); const intent = saved.intent;
    submission = (async () => {
      try {const doc = await api('POST', '/operations?wait=3', intent.request, intent.key); guard(); accept(doc.operation);}
      catch (error) {if (current()) {
        if (error.status >= 400 && error.status < 500 && admissionRefusals.has(error.code)) {intent.refused = error.code; try {persist();} catch { /* keep original */ }}
        showError(error);
      }} finally {busy = false; if (current()) update();}
    })();
    try {await submission;} finally {submission = null;}
  }}, t('orch_apply_' + mode));
  const check = h('button', {class: 'secondary', onclick: () => refresh(true).catch(() => {})}, t('permissions_check'));
  const another = h('button', {class: 'secondary', onclick: async () => {
    if (!current() || busy || refreshing || readFailed || !(mayReplace() || saved.intent?.refused)) return;
    const previous = saved;
    saved = {...saved, message: '', queue: false, items: [{title: '', prompt: ''}]}; delete saved.intent;
    try {persist();} catch (error) {saved = previous; showError(error); return;}
    operation = null; reviewed = false; review.checked = false; status.replaceChildren(); fill(); renderItems(); update(); await discover().catch(() => {});
  }}, t('orch_new'));
  const editor = mode === 'items' ? h('div', {class: 'panel'}, label('start_agent', agent), items, h('div', {class: 'actions'}, add)) :
    h('div', {class: 'panel'}, label(mode === 'failover' ? 'orch_instructions' : 'orch_message', message),
      ...(mode === 'relay' ? [h('label', {class: 'orch-check'}, queue, ' ', t('queue_behind'))] : []),
      ...(mode === 'planner' ? [label('orch_max_items', maximum)] : []));
  const reviewLine = h('label', {class: 'orch-check'}, review, ' ', t('orch_review_' + mode));
  const box = h('section', {class: 'orchestration', 'data-orchestration': mode},
    h('p', {class: 'muted'}, t('orch_help_' + mode)),
    h('div', {class: 'panel'}, h('div', {class: 'capture-fields'}, label('host', host), label(sessionMode(mode) ? 'orch_session' : 'start_workspace', selection)),
      h('div', {class: 'actions'}, reload), discoveryStatus, identity), editor,
    h('p', {class: 'muted'}, t('orch_preparation')), reviewLine, h('div', {class: 'actions'}, apply, check, another), result, status);
  function renderResult() {
    result.replaceChildren();
    if (!saved.intent && !withinBodyLimit(request())) result.append(h('p', {class: 'error'}, t('orch_body_limit')));
    if (!allowed()) result.append(h('p', {class: 'muted'}, t('orch_unavailable', {scopes: scopes(mode).join(' + ')})));
    if (!saved.intent) return;
    const intent = saved.intent;
    result.append(h('p', {}, operation ? operation.status === 'waiting_external' ? t('orch_waiting_children') : opStatus(operation) : t(intent.refused ? 'start_refused' : 'start_unknown'), ' ',
      ...(intent.operation_id ? [h('a', {href: `#/op/${intent.operation_id}`}, t('permissions_details'))] : []), operation?.status_reason ? ` · ${operation.status_reason}` : ''));
    result.append(h('p', {class: 'muted'}, t('orch_fixed')));
    if (!intent.request || !intent.key) result.append(h('p', {class: 'error'}, t('permissions_damaged')));
    if (!operation) return;
    const proof = proofOf(operation);
    if (operation.status !== 'succeeded') result.append(h('p', {class: 'note'}, t('orch_partial')));
    const links = new Map();
    if (opId(proof.child_operation_id)) links.set(proof.child_operation_id, t('orch_child'));
    for (const row of childrenOf(operation)) if (opId(row.operation_id)) links.set(row.operation_id, `${row.task}. ${row.title} · ${operationStatusLabel(row.operation_status)}`);
    // Child admission receipts exist before the aggregate result has caught up.
    for (const step of operation.steps || []) if (opId(step.response?.operation_id)) links.set(step.response.operation_id, links.get(step.response.operation_id) || step.name);
    if (links.size) result.append(h('h2', {}, t('orch_receipts')), h('ul', {}, ...[...links].map(([id, label]) => h('li', {}, h('a', {href: `#/op/${id}`}, label)))));
    if (mode === 'items') result.append(h('p', {class: 'muted'}, t('orch_item_progress', {count: childrenOf(operation).filter(row => row.prompt_sent === true).length, total: intent.request?.params.plan.length || 0})));
    if (mode === 'relay' && proof.sent === true) result.append(h('p', {}, t('orch_relay_accepted')));
    if (mode === 'planner' && proof.prompt_sent === true && text(proof.session_id)) result.append(h('p', {}, t('orch_planner_started'), ' ',
      h('a', {href: `#/session/${encodeURIComponent(intent.request.target.host)}/${encodeURIComponent(proof.session_id)}`}, proof.session_id)));
    if (mode === 'failover') {
      const sid = proof.new_session_id || proof.pending_successor;
      if (text(sid)) result.append(h('p', {}, t(proof.prompt_sent === true ? 'orch_handoff_accepted' : 'orch_successor_unconfirmed'), ' ',
        h('a', {href: `#/session/${encodeURIComponent(intent.request.target.host)}/${encodeURIComponent(sid)}`}, sid)));
    }
    if (operation.steps?.length) result.append(h('details', {'data-orchestration-steps': ''}, h('summary', {}, t('orch_steps')), h('ul', {}, ...operation.steps.map(step => h('li', {}, `${step.name} · ${operationStatusLabel(step.status)}`)))));
  }
  function update() {
    const fixed = Boolean(saved.intent);
    for (const el of Object.values(inputs)) el.disabled = busy || fixed;
    selection.disabled ||= discovering || !saved.host;
    for (const el of items.querySelectorAll('input,textarea,button')) el.disabled = busy || fixed || (el.tagName === 'BUTTON' && saved.items.length === 1);
    add.disabled = busy || fixed || saved.items.length >= 16; add.hidden = fixed;
    reload.hidden = fixed; reload.disabled = discovering || !saved.host || !caps()?.scopes?.includes('observe');
    review.disabled = fixed || busy; reviewLine.hidden = fixed;
    apply.hidden = Boolean(saved.intent?.operation_id || saved.intent?.refused); apply.textContent = t(fixed ? 'permissions_retry' : 'orch_apply_' + mode);
    apply.disabled = busy || readFailed || !ready() || !allowed() || (fixed ? !saved.intent.request || !saved.intent.key :
      discovering || !reviewed || !hostAllowed() || !selectedAllowed() || !validRequest(request(), mode));
    check.hidden = !saved.intent?.operation_id; check.disabled = busy || Boolean(refreshing);
    another.hidden = !(mayReplace() || saved.intent?.refused); another.disabled = busy || Boolean(refreshing) || readFailed;
    identity.replaceChildren(); identity.hidden = !saved.selection;
    if (saved.selection) identity.append(t(sessionMode(mode) ? 'sessions_id' : 'sessions_workspace_id'), ': ', h('code', {}, saved.selection));
    renderResult();
  }
  async function refresh(fresh = false) {
    if (submission) {await submission; guard();}
    if (refreshing) {await refreshing; if (fresh) return refresh(true); return;}
    refreshing = (async () => {
      if (saved.intent?.operation_id) {const doc = await api('GET', `/operations/${saved.intent.operation_id}`); guard(); accept(doc.operation);}
      else if (!saved.intent) await discover();
      else update();
    })();
    update();
    try {await refreshing;} catch (error) {if (current()) {readFailed = true; showError(error); update();} throw error;}
    finally {refreshing = null; if (current()) update();}
  }
  renderSelection(); renderItems(); fill(); update();
  return {box, update, refresh, init: refresh};
}
