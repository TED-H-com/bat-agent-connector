import {locale} from './i18n.js';
import {pmDurableForm} from './pm-workspace.js';
import {button, emptyState, statusPill} from './ui/components.js';

const WORKSPACE_ID = /^ws_[0-9a-f]{32}$/;
const WORK_ITEM_ID = /^wi_[0-9a-f]{20}$/;
const ATTEMPT_ID = /^att_[0-9a-f]{32}$/;
const OPERATION_ID = /^op_[0-9a-f]{32}$/;
const REKEY = new Set(['PM_WORK_ATTEMPT_CHANGED', 'INVALID_PRECONDITIONS']);
const QUEUED_RECEIPT = new Set(['queued', 'waiting_external']);

export function workFollowUpAvailable(caps) {
  const feature = caps?.features?.pm_workspaces?.work_follow_up;
  return feature?.version === 1 && feature?.available === true;
}

const retired = error => ['VIEW_CHANGED', 'CONNECTION_CHANGED', 'PM_DISPOSED'].includes(error?.code);

function exactTarget(target, workspaceId, workItemId) {
  if (!target || typeof target !== 'object' || Array.isArray(target)) return false;
  const keys = Object.keys(target);
  return keys.length === 2 && target.workspace_id === workspaceId && target.work_item_id === workItemId;
}

// Server copy wins. A missing locale string stays in this locale; it does not fall back to English.
function receiptText(receipt, part, t) {
  const value = receipt?.labels?.[part]?.[locale];
  return typeof value === 'string' && value.trim() ? value : t('loop_next_unknown');
}

function actionLabel(action, t) {
  const value = locale === 'zh-TW' ? action?.label_zh_TW : action?.label;
  return typeof value === 'string' && value.trim() ? value : '';
}

export function mountWorkFollowUp({h, t, api, caps, guard, namespace, workspaceId, workItemId, selectedAttemptId = '', errorBox, canSend = () => true, managed = () => false, onField = () => {}}) {
  if (!workFollowUpAvailable(caps()) || !WORKSPACE_ID.test(workspaceId || '') || !WORK_ITEM_ID.test(workItemId || '')) return null;
  const titleId = `work-next-title-${workItemId}`;
  const hintId = `work-next-hint-${workItemId}`;
  const heading = h('h2', {id: titleId}, t('loop_next_label'));
  const pill = h('span', {'data-follow-up-pill': ''});
  const list = h('div', {class: 'work-next-list', 'data-follow-up-list': ''});
  const formHost = h('div', {'data-follow-up-form': ''});
  const note = h('div', {'data-follow-up-note': ''});
  const status = h('div', {role: 'status', 'data-follow-up-status': ''});
  const live = h('div', {role: 'status', class: 'work-next-live', 'data-follow-up-live': ''});
  const box = h('section', {class: 'ui-section-card work-next', 'data-work-section': 'next-turn'},
    h('header', {class: 'work-next-head'}, heading, pill), list, note, formHost, status, live);

  let epoch = 0, serial = 0, stopped = false, suspended = false, disposed = false;
  let attemptId = ATTEMPT_ID.test(selectedAttemptId) ? selectedAttemptId : '', sendOffer = null, blockedReason = '', resultSettled = false;
  let reading = false, admissionReady = false;
  let pageItems = [], nextCursor = null, hasMore = false, filtered = false, listSettled = false;
  const pinned = new Map(), announced = new Set(), cancelDenied = new Set();
  let composer = null, composing = false, composeEnded = -Infinity, focusAfter = null;
  let sawList = false;

  const seen = () => epoch;
  function alive(ticket, mark) {
    try {guard();} catch {return false;}
    return mark === epoch && ticket === serial && !stopped && !suspended && !disposed;
  }
  const inputsPath = after => `/pm/workspaces/${encodeURIComponent(workspaceId)}/work/${encodeURIComponent(workItemId)}/inputs?${new URLSearchParams({
    limit: '50',
    ...(after ? {after: String(after)} : {}),
    ...(attemptId ? {attempt_id: attemptId} : {}),
  })}`;
  function validReceipt(row) {
    return row && typeof row === 'object' && OPERATION_ID.test(row.operation_id || '')
      && row.workspace_id === workspaceId && row.work_item_id === workItemId;
  }
  function mergedItems() {
    const map = new Map();
    for (const row of pageItems) if (validReceipt(row)) map.set(row.operation_id, row);
    for (const [id, row] of pinned) if (validReceipt(row) && !map.has(id)) map.set(id, row);
    return [...map.values()].sort((a, b) => {
      const as = Number.isInteger(a.sequence) ? a.sequence : Number.MAX_SAFE_INTEGER;
      const bs = Number.isInteger(b.sequence) ? b.sequence : Number.MAX_SAFE_INTEGER;
      return as - bs || String(a.operation_id).localeCompare(String(b.operation_id));
    });
  }
  function rememberAttempt(value) {
    if (!selectedAttemptId && typeof value === 'string' && ATTEMPT_ID.test(value)) attemptId = value;
  }
  function sentence(code) {
    const key = `loop_next_err_${code || ''}`;
    const text = t(key);
    return text !== key ? text : t('loop_next_err_unknown');
  }
  function showError(error, host) {
    const summary = error?.code ? sentence(error.code) : t('loop_next_err_unknown');
    host.replaceChildren(errorBox(error, {summary, within: host}));
  }

  function clearComposer() {
    composer?.dispose();
    composer = null;
    formHost.replaceChildren();
    try {onField(null, null);} catch { /* the host reports its own binding */ }
  }
  function composerAllowed() {
    return !reading && admissionReady && Boolean(sendOffer) && resultSettled && managed() && !suspended
      && caps()?.scopes?.includes('operate')
      && caps()?.actions?.some(row => row.action === 'session.send' && row.allowed === true);
  }
  function dropUnconfirmedPins() {
    const visible = new Set(pageItems.map(row => row.operation_id));
    for (const id of [...pinned.keys()]) if (!visible.has(id)) pinned.delete(id);
  }
  function actorMatches(receipt) {
    const actor = caps()?.actor;
    return typeof actor === 'string' && actor !== '' && actor === receipt?.actor;
  }
  function offerSend(result) {
    if (!result || result.contract !== 'pm-work-attempt-result/v1') return null;
    if (result.workspace_id !== workspaceId || result.attempt_id !== attemptId) return null;
    if (result.identity?.work_item_id !== workItemId) return null;
    const rows = Array.isArray(result.next_allowed_actions) ? result.next_allowed_actions : [];
    return rows.find(row => row?.kind === 'action' && row.action === 'session.send' && row.allowed !== false
      && exactTarget(row.target, workspaceId, workItemId)
      && Array.isArray(row.allowed_schedules) && row.allowed_schedules.includes('after_current')) || null;
  }
  function applyResult(result) {
    if (!result || result.contract !== 'pm-work-attempt-result/v1') return false;
    if (result.workspace_id !== workspaceId || result.identity?.work_item_id !== workItemId) return false;
    if (attemptId && result.attempt_id !== attemptId) return false;
    resultSettled = true;
    sendOffer = offerSend(result);
    const blocked = Array.isArray(result.blocked_actions) ? result.blocked_actions.find(row => row?.action === 'session.send') : null;
    blockedReason = sendOffer ? '' : blocked?.reason || '';
    return true;
  }
  function syncComposer() {
    if (stopped || disposed) return;
    const allowed = composerAllowed();
    const showReadOnly = !reading && !allowed && resultSettled && admissionReady
      && (blockedReason === 'missing_scope' || blockedReason === 'not_writer_member' || !caps()?.scopes?.includes('operate'));
    pill.replaceChildren(...(showReadOnly ? [statusPill('read_only')] : []));
    const reason = blockedReason === 'codex_uncorrelated_turn_boundary' ? t('loop_next_codex')
      : blockedReason === 'not_controller' ? t('loop_next_not_controller') : '';
    note.replaceChildren(...(reason ? [h('p', {class: 'work-next-note'}, reason)] : []));
    if (!allowed) {
      if (reading && composer) {composer.update(); paint(); return;}
      if (composer) clearComposer();
      paint();
      return;
    }
    if (composer) {composer.update(); paint(); return;}
    const field = h('textarea', {id: `work-next-input-${workItemId}`, 'data-follow-up-input': '', rows: '4'});
    field.setAttribute('aria-labelledby', titleId);
    field.setAttribute('aria-describedby', hintId);
    field.addEventListener('compositionstart', () => {composing = true;});
    field.addEventListener('compositionend', () => {composing = false; composeEnded = performance.now();});
    field.addEventListener('input', () => composer?.update());
    const hint = h('p', {class: 'ui-meta', id: hintId}, t('loop_next_hint'));
    composer = pmDurableForm({
      h, t, api, caps, guard, namespace,
      action: 'session.send',
      target: {workspace_id: workspaceId, work_item_id: workItemId},
      fields: {text: field},
      params: values => ({text: values.text.trim(), schedule: 'after_current'}),
      // Preconditions are captured only when a new request is authorized.
      preconditions: () => attemptId ? {expected_attempt_id: attemptId} : {},
      permitted: () => managed() && !stopped && !suspended,
      canSend: () => !reading && admissionReady && field.value.trim().length > 0
        && field.value.trim().length <= 20000 && canSend(),
      requiredScope: 'operate',
      label: 'loop_next_send',
      releaseAfterAcknowledgement: true,
      acknowledgeStatus: acknowledge,
      onAcknowledged: acceptOperation,
      onSaved: async () => {},
      onError: handleFormError,
      errorBox,
      extra: h('div', {class: 'pm-fields work-next-field'}, field, hint),
    });
    composer.box.addEventListener('submit', event => {
      if (composing || performance.now() - composeEnded < 50) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }, true);
    formHost.append(composer.box);
    if (field.value) field.selectionStart = field.selectionEnd = field.value.length;
    try {onField(field, composer.box.querySelector('button[type=submit]'));} catch { /* the host reports its own binding */ }
    paint();
  }
  function correlatedReceipt(op) {
    const receipt = op?.input_receipt;
    if (!validReceipt(receipt) || receipt.operation_id !== op?.operation_id) return null;
    return receipt;
  }
  function acknowledge(op, builder, translate) {
    const receipt = correlatedReceipt(op);
    if (receipt) {
      const title = receiptText(receipt, 'state', translate);
      if (QUEUED_RECEIPT.has(receipt.state)) {
        return builder('span', {'data-pm-acknowledged': '', 'data-follow-up-ack': 'queued'},
          translate('loop_next_queued'), title ? ' · ' : '', title || null);
      }
      const kind = receipt.state === 'accepted' ? 'accepted' : (receipt.state || 'unknown');
      return builder('span', {'data-pm-acknowledged': '', 'data-follow-up-ack': kind}, title);
    }
    const accepted = op?.status === 'accepted' || op?.status === 'succeeded';
    const id = OPERATION_ID.test(op?.operation_id || '') ? op.operation_id : '';
    return builder('span', {'data-pm-acknowledged': '', 'data-follow-up-ack': accepted ? 'accepted' : 'unknown'},
      translate(accepted ? 'loop_next_accepted' : 'loop_next_unknown'),
      id ? ' ' : null,
      id ? builder('a', {href: `#/op/${encodeURIComponent(id)}`}, id) : null);
  }
  function acceptOperation(op) {
    const receipt = op?.input_receipt;
    if (!validReceipt(receipt) || receipt.operation_id !== op?.operation_id) return;
    pinned.set(receipt.operation_id, receipt);
    paint();
    void refresh();
  }
  function handleFormError(error) {
    if (!error?.code || error.code === 'NETWORK_ERROR' || error.code === 'TIMEOUT' || error.code === 'CENTRAL_OFFLINE') return false;
    const formStatus = composer?.box.querySelector('[role=status]');
    if (formStatus) showError(error, formStatus);
    // A newer attempt can be read. It does not replace the original key or request.
    if (REKEY.has(error.code)) void refresh();
    return true;
  }
  function fillFrom(action, focusField) {
    if (!composer || composer.locked()) return false;
    const text = typeof action?.params?.text === 'string' ? action.params.text : '';
    composer.set('text', text);
    if (focusField) composer.fields.text.focus();
    return true;
  }
  async function runEndpoint(action, receipt) {
    const mark = epoch;
    const id = action?.operation_id;
    if (!OPERATION_ID.test(id || '') || !actorMatches(receipt) || !['cancel', 'resume'].includes(action?.endpoint)) return;
    const endpoint = action.endpoint === 'cancel' ? 'cancel' : 'resume';
    try {
      guard();
      await api('POST', `/operations/${encodeURIComponent(id)}/${endpoint}`, {});
      if (mark !== epoch || stopped || disposed) return;
      if (action.endpoint === 'cancel' && id === receipt.operation_id) focusAfter = {id: receipt.operation_id, kind: 'edit'};
      await refresh();
    } catch (error) {
      if (mark !== epoch || stopped || disposed || retired(error)) return;
      if (error?.code === 'PM_WORK_CANCEL_NOT_ALLOWED_AFTER_SEND') {
        cancelDenied.add(receipt.operation_id);
        showError(error, status);
        paint();
        return;
      }
      showError(error, status);
    }
  }
  function mutationsAllowed() {
    if (reading || !admissionReady || !sendOffer) return false;
    if (['not_controller', 'codex_uncorrelated_turn_boundary', 'not_writer_member', 'missing_scope'].includes(blockedReason)) return false;
    return managed() && !stopped && !suspended
      && caps()?.scopes?.includes('operate')
      && caps()?.actions?.some(row => row.action === 'session.send' && row.allowed === true);
  }
  function rowActions(receipt) {
    if (!mutationsAllowed()) return [];
    if (!actorMatches(receipt)) {
      const present = Array.isArray(receipt.next_allowed_actions) && receipt.next_allowed_actions.length > 0;
      return present ? [h('p', {class: 'ui-meta'}, t('loop_next_not_actor'))] : [];
    }
    const actions = Array.isArray(receipt.next_allowed_actions) ? receipt.next_allowed_actions : [];
    const nodes = [];
    for (const action of actions) {
      if (action?.kind === 'operation_endpoint' && action.endpoint === 'cancel' && action.operation_id === receipt.operation_id
        && receipt.cancellable === true && !cancelDenied.has(receipt.operation_id)) {
        const label = actionLabel(action, t);
        if (!label) continue;
        nodes.push(button({variant: 'secondary', label, 'data-follow-up-action': 'cancel', onClick: () => runEndpoint(action, receipt)}));
      } else if (action?.kind === 'operation_endpoint' && action.endpoint === 'resume' && action.operation_id === receipt.operation_id
        && receipt.state === 'unknown') {
        const label = actionLabel(action, t);
        if (!label) continue;
        nodes.push(button({variant: 'secondary', label, 'data-follow-up-action': 'resume', onClick: () => runEndpoint(action, receipt)}));
      } else if (action?.kind === 'action' && action.action === 'session.send' && exactTarget(action.target, workspaceId, workItemId)
        && (receipt.state === 'failed' || receipt.state === 'cancelled')) {
        const label = actionLabel(action, t);
        if (!label) continue;
        const kind = receipt.state === 'cancelled' ? 'edit' : 'retry';
        nodes.push(button({variant: 'secondary', label, 'data-follow-up-action': kind, onClick: () => {fillFrom(action, kind === 'retry');}}));
      }
    }
    return nodes;
  }
  function paint() {
    if (stopped || disposed) return;
    const active = document.activeElement;
    const typing = composer && (active === composer.fields.text || composer.fields.text.contains?.(active));
    const focusedReceipt = !typing ? active?.closest?.('[data-follow-up-receipt]')?.getAttribute('data-follow-up-receipt') : '';
    const focusedAction = !typing ? active?.getAttribute?.('data-follow-up-action') : '';
    const items = mergedItems();
    const fresh = items.filter(row => !announced.has(row.operation_id));
    if (sawList) {
      for (const row of fresh) announced.add(row.operation_id);
      if (fresh.length) live.textContent = fresh.map(row => receiptText(row, 'state', t)).filter(Boolean).join(' ');
    }
    const rows = items.map(receipt => h('article', {class: 'work-next-row', 'data-follow-up-receipt': receipt.operation_id, 'data-follow-up-state': receipt.state || ''},
      h('a', {href: `#/op/${encodeURIComponent(receipt.operation_id)}`}, receiptText(receipt, 'state', t)),
      h('p', {}, receiptText(receipt, 'detail', t)),
      typeof receipt.text_excerpt === 'string' && receipt.text_excerpt ? h('p', {}, receipt.text_excerpt) : null,
      receipt.text_truncated === true ? h('p', {class: 'ui-meta'}, t('loop_next_excerpt')) : null,
      receipt.status_reason === 'waiting_permission_or_question_gate' ? h('p', {}, t('loop_next_waiting')) : null,
      typeof receipt.blocked_by_operation_id === 'string' && OPERATION_ID.test(receipt.blocked_by_operation_id)
        ? h('a', {href: `#/op/${encodeURIComponent(receipt.blocked_by_operation_id)}`}, t('loop_next_blocked_link')) : null,
      ...rowActions(receipt)));
    const tail = [];
    if (filtered) tail.push(h('p', {class: 'work-next-note', 'data-follow-up-hidden': ''}, t('loop_next_hidden')));
    if (hasMore) tail.push(button({variant: 'ghost', label: t('loop_next_more'), 'data-follow-up-more': '', onClick: () => loadMore()}));
    if (!items.length && listSettled && !hasMore) tail.push(emptyState({title: t('loop_next_empty'), heading: 'h3'}));
    list.replaceChildren(...rows, ...tail);
    if (focusAfter) {
      const pending = focusAfter;
      const node = list.querySelector(`[data-follow-up-receipt="${CSS.escape(pending.id)}"] [data-follow-up-action="${pending.kind}"]`);
      if (node) {focusAfter = null; node.focus();}
    } else if (focusedReceipt && !typing) {
      const selector = focusedAction
        ? `[data-follow-up-receipt="${CSS.escape(focusedReceipt)}"] [data-follow-up-action="${CSS.escape(focusedAction)}"]`
        : `[data-follow-up-receipt="${CSS.escape(focusedReceipt)}"] a`;
      list.querySelector(selector)?.focus();
    }
  }
  function takeList(data, append) {
    if (!data || data.workspace_id !== workspaceId || data.work_item_id !== workItemId || !Array.isArray(data.items)
      || selectedAttemptId && data.attempt_id !== selectedAttemptId) return false;
    const rows = data.items.filter(validReceipt);
    pageItems = append ? [...pageItems, ...rows] : rows;
    nextCursor = typeof data.next_cursor === 'string' && data.next_cursor ? data.next_cursor : Number.isInteger(data.next_cursor) ? data.next_cursor : null;
    hasMore = data.has_more === true && nextCursor != null && nextCursor !== '';
    filtered = data.visibility_filtered === true;
    rememberAttempt(data.latest_attempt_id);
    listSettled = true;
    dropUnconfirmedPins();
    return true;
  }
  function denyAdmission(message) {
    reading = false;
    admissionReady = false;
    sendOffer = null;
    dropUnconfirmedPins();
    if (message) status.replaceChildren(h('p', {}, message));
    syncComposer();
  }
  async function readResult(ticket, mark) {
    if (!alive(ticket, mark)) return;
    if (!attemptId) {
      resultSettled = true; sendOffer = null; blockedReason = '';
      reading = false; admissionReady = true; syncComposer(); return;
    }
    let data;
    try {
      data = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/work-attempts/${encodeURIComponent(attemptId)}/result`);
    } catch (error) {
      if (!alive(ticket, mark) || retired(error)) return;
      if ([403, 404, 410].includes(error?.status)) {withdrawSource(); return;}
      throw error;
    }
    if (!alive(ticket, mark)) return;
    if (!applyResult(data)) {denyAdmission(t('loop_next_err_unknown')); return;}
    reading = false;
    admissionReady = true;
    syncComposer();
  }
  async function refresh() {
    if (stopped || suspended || disposed) return;
    const mark = epoch;
    const ticket = ++serial;
    reading = true;
    admissionReady = false;
    sendOffer = null;
    syncComposer();
    const queried = attemptId;
    try {
      guard();
      const data = await api('GET', inputsPath(''));
      if (!alive(ticket, mark)) return;
      if (!takeList(data, false)) {denyAdmission(t('loop_next_err_unknown')); return;}
      paint();
      sawList = true;
      for (const row of mergedItems()) announced.add(row.operation_id);
      if (!queried && attemptId) {
        const filteredPage = await api('GET', inputsPath(''));
        if (!alive(ticket, mark)) return;
        if (!takeList(filteredPage, false)) {denyAdmission(t('loop_next_err_unknown')); return;}
        paint();
      }
      await readResult(ticket, mark);
    } catch (error) {
      if (!alive(ticket, mark) || retired(error)) return;
      if ([403, 404, 410].includes(error?.status)) {withdrawSource(); return;}
      denyAdmission();
      showError(error, status);
    }
  }
  async function loadMore() {
    if (!hasMore || stopped || suspended || disposed || nextCursor == null) return;
    const mark = epoch;
    const ticket = ++serial;
    const after = nextCursor;
    try {
      const data = await api('GET', inputsPath(after));
      if (!alive(ticket, mark)) return;
      if (!takeList(data, true)) {status.replaceChildren(h('p', {}, t('loop_next_err_unknown'))); return;}
      paint();
    } catch (error) {
      if (!alive(ticket, mark) || retired(error)) return;
      if ([403, 404, 410].includes(error?.status)) {withdrawSource(); return;}
      showError(error, status);
    }
  }
  function withdrawSource() {
    epoch += 1; serial += 1; stopped = true;
    reading = false; admissionReady = false;
    pageItems = []; pinned.clear(); announced.clear();
    sendOffer = null; resultSettled = false; hasMore = false; filtered = false;
    clearComposer();
    list.replaceChildren(); note.replaceChildren(); pill.replaceChildren(); status.replaceChildren();
    live.textContent = t('loop_next_withdrawn');
    box.replaceChildren(h('p', {role: 'status', 'data-follow-up-withdrawn': ''}, t('loop_next_withdrawn')));
  }
  function suspend() {
    epoch += 1; serial += 1; suspended = true;
    reading = false; admissionReady = false; sendOffer = null;
    if (stopped) return;
    pageItems = []; pinned.clear();
    list.replaceChildren(); note.replaceChildren(); status.replaceChildren();
    clearComposer();
    box.hidden = true;
  }
  function restore() {
    if (stopped || disposed) return;
    suspended = false;
    box.hidden = false;
    void refresh();
  }
  function sync() {
    if (stopped || disposed) return;
    syncComposer();
  }
  function dispose() {
    disposed = true; epoch += 1; serial += 1;
    clearComposer();
  }
  void refresh();
  return {box, refresh, suspend, restore, sync, withdraw: withdrawSource, dispose};
}
