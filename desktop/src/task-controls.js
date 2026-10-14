// Coordinator controls bind the observed task version. A retry never substitutes a newer one.
import {observedText} from './observed-resource-strings.js';
import {button, sectionCard} from './ui/components.js';
const object = value => value && typeof value === "object" && !Array.isArray(value);
const opId = value => typeof value === "string" && /^op_[0-9a-f]{32}$/.test(value);
const version = value => Number.isSafeInteger(value) && value >= 0;
const equal = (a, b) => a === b || (object(a) && object(b) && Object.keys(a).length === Object.keys(b).length &&
  Object.keys(a).every(k => equal(a[k], b[k])));
const terminal = op => ["succeeded", "failed", "cancelled"].includes(op?.status);
function valid(request, id) {
  return ["task.pause", "task.resume"].includes(request?.action) && equal(request.target, {task_id: id}) &&
    object(request.params) && (request.action === "task.pause" ? typeof request.params.abort_current === "boolean" &&
      Object.keys(request.params).length === 1 : Object.keys(request.params).length === 0) &&
    object(request.preconditions) && Object.keys(request.preconditions).length === 1 && version(request.preconditions.control_version);
}
export function taskControlsPanel({h, t, api, caps, guard, ready, task, taskId, storageKey, errorBox, opStatus, onSettled}) {
  let raw;
  try {raw = JSON.parse(localStorage.getItem(storageKey));} catch { /* no saved draft */ }
  let saved = {abort: raw?.abort === true}, operation = null, submission = null, refreshing = null, busy = false, readFailed = false;
  if (raw?.intent) {
    const proven = valid(raw.intent.request, taskId) && typeof raw.intent.key === "string" && raw.intent.key.length > 0 && raw.intent.key.length <= 200;
    saved.intent = {request: proven ? raw.intent.request : null, key: proven ? raw.intent.key : null,
      operation_id: opId(raw.intent.operation_id) ? raw.intent.operation_id : null};
    // This code is emitted after existing-key replay and before task admission INSERT.
    if (proven && !saved.intent.operation_id && raw.intent.refused === "CONTROL_VERSION_CONFLICT") saved.intent.refused = raw.intent.refused;
    if (proven) saved.abort = raw.intent.request.params.abort_current === true;
  }
  const live = () => {try {guard(); return true;} catch {return false;}};
  const persist = () => {guard(); localStorage.setItem(storageKey, JSON.stringify(saved));};
  const observed = () => task()?.task_id === taskId && version(task()?.control_version) && [true, false, 0, 1].includes(task()?.paused);
  const action = () => saved.intent?.request?.action || (task()?.paused ? "task.resume" : "task.pause");
  const permitted = () => (caps()?.scopes || []).includes("operate") && caps()?.actions?.some(a => a.action === action() && a.allowed === true);
  const writable = () => ready() && observed() && permitted() && (saved.intent || !["done", "failed"].includes(task()?.state));
  const abort = h("input", {type: "checkbox", checked: saved.abort});
  const message = h("div", {role: "status"}), result = h("div", {"data-task-result": ""}), restriction = h("p", {class: "muted"});
  const showError = error => {if (live()) {message.replaceChildren(errorBox(error)); update();}};
  const accept = candidate => {
    guard(); const intent = saved.intent;
    if (!intent || !opId(candidate?.operation_id) || !["task.pause", "task.resume"].includes(candidate.action) ||
        candidate.target?.task_id !== taskId || candidate.actor !== caps()?.actor ||
        intent.operation_id && candidate.operation_id !== intent.operation_id ||
        intent.key && candidate.idempotency_key !== intent.key ||
        intent.request && !equal({action: candidate.action, target: candidate.target, params: candidate.params,
          preconditions: candidate.preconditions}, intent.request)) throw new Error(t("task_control_invalid"));
    operation = candidate; intent.operation_id = candidate.operation_id; persist(); readFailed = false; message.replaceChildren(); update();
  };
  const apply = button({variant: "secondary", label: t("task_pause"), onClick: async () => {
    if (!live() || busy || refreshing || readFailed || !writable() || saved.intent?.operation_id ||
        saved.intent && (!saved.intent.request || saved.intent.refused)) return;
    const previous = saved;
    if (!saved.intent) saved = {...saved, intent: {request: {action: action(), target: {task_id: taskId},
      params: action() === "task.pause" ? {abort_current: saved.abort} : {},
      preconditions: {control_version: task().control_version}}, key: crypto.randomUUID(), operation_id: null}};
    try {persist();} catch (e) {saved = previous; showError(e); return;}
    busy = true; update(); const intent = saved.intent;
    submission = (async () => {
      try {const response = await api("POST", "/operations?wait=3", intent.request, intent.key); guard(); accept(response.operation);}
      catch (e) {if (live()) {
        if (e.code === "CONTROL_VERSION_CONFLICT" && e.status === 409) {intent.refused = e.code; try {persist();} catch { /* retained in memory */ }}
        showError(e);
      }} finally {busy = false; if (live()) update();}
    })();
    try {await submission;} finally {submission = null;}
    if (live()) try {await onSettled?.();} catch (e) {showError(e);}
  }});
  const check = button({variant: "secondary", label: t("permissions_check"), onClick: () => (onSettled ? onSettled() : refresh(true)).catch(showError)});
  const another = button({variant: "secondary", label: t("task_control_new"), onClick: () => {
    if (!live() || busy || refreshing || readFailed || !writable() || !(terminal(operation) || saved.intent?.refused)) return;
    const previous = saved; saved = {abort: false};
    try {persist();} catch (e) {saved = previous; showError(e); return;}
    operation = null; abort.checked = false; message.replaceChildren(); update();
  }});
  const abortLabel = h("label", {class: "capture-choice muted"}, abort, " ", t("task_abort"));
  abort.onchange = () => {
    if (!live() || saved.intent || busy) {abort.checked = saved.abort; return;}
    saved.abort = abort.checked; try {persist();} catch (e) {showError(e);} update();
  };
  const help = h("p", {class: "muted"}, t("task_control_help"));
  const box = sectionCard({title: t("task_controls"), body: h("div", {}, help, abortLabel,
    h("div", {class: "actions"}, apply, check, another), restriction, result, message)});
  box.dataset.taskControls = "";
  function update() {
    const fixed = Boolean(saved.intent);
    const canOperate = permitted();
    box.hidden = !(caps()?.scopes || []).includes("operate");
    help.hidden = !canOperate;
    abortLabel.hidden = !canOperate || action() !== "task.pause"; abort.disabled = fixed || busy;
    apply.hidden = !canOperate || Boolean(saved.intent?.operation_id || saved.intent?.refused);
    apply.disabled = busy || Boolean(refreshing) || readFailed || !writable() || Boolean(fixed && !saved.intent.request);
    apply.textContent = t(fixed ? "permissions_retry" : action() === "task.pause" ? "task_pause" : "task_resume");
    check.hidden = !saved.intent?.operation_id; check.disabled = busy || Boolean(refreshing);
    another.hidden = !canOperate || !(terminal(operation) || saved.intent?.refused); another.disabled = busy || Boolean(refreshing) || readFailed || !writable();
    restriction.textContent = writable() ? "" : observedText(t, !canOperate ? "or_readonly_task"
      : ["done", "failed"].includes(task()?.state) ? "or_work_ended" : "or_controls_unavailable");
    result.replaceChildren();
    if (fixed) {
      result.append(h("p", {}, operation ? opStatus(operation) : t(saved.intent.refused ? "task_control_refused" : "permissions_unknown"), " ",
        ...(saved.intent.operation_id ? [h("a", {href: `#/op/${saved.intent.operation_id}`}, t("permissions_details"))] : [])),
      h("p", {class: "muted"}, t("task_control_fixed", {version: saved.intent.request?.preconditions.control_version ?? "?"})));
      if (operation?.status_reason) result.append(h("p", {}, operation.status_reason));
      if (operation?.status === "succeeded") result.append(h("p", {class: "muted"}, t("task_control_recorded")));
      if (!saved.intent.request && !saved.intent.operation_id) result.append(h("p", {class: "error"}, t("permissions_damaged")));
    }
  }
  async function refresh(fresh = false) {
    if (submission) {await submission; guard();}
    if (refreshing) {await refreshing; if (fresh) return refresh(true); return;}
    if (!saved.intent?.operation_id) return;
    refreshing = (async () => {const data = await api("GET", `/operations/${saved.intent.operation_id}`); guard(); accept(data.operation);})();
    update();
    try {await refreshing;} catch (e) {readFailed = true; showError(e); throw e;}
    finally {refreshing = null; if (live()) update();}
  }
  update(); return {box, update, refresh};
}
