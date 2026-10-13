import {disclosure} from "./ui/components.js";
// This form records requested policy, never an inferred live permission mode.
const record = value => value && typeof value === "object" && !Array.isArray(value);
const modeValue = value => ["default", "allow_all"].includes(value);
const operationId = value => typeof value === "string" && /^op_[0-9a-f]{32}$/.test(value);
const terminal = operation => ["succeeded", "failed", "cancelled"].includes(operation?.status);
// These admission gates run after key replay and before INSERT. Generic auth/4xx and
// transport failures can precede replay of an accepted request, so are never reset proof.
const admissionRefusals = new Set(["TASK_PAUSED", "CONTROL_VERSION_CONFLICT", "PERMISSIONS_HOST_POLICY", "CONFINEMENT_RAISE_REFUSED"]);
function restore(value, target) {
  const saved = {mode: modeValue(value?.mode) ? value.mode : "default"};
  if (!record(value) || !value.intent) return saved;
  const intent = value.intent, request = intent.request;
  const valid = request?.action === "session.permissions" && request.target?.host === target.host &&
    request.target?.session_id === target.session_id && modeValue(request.params?.mode) &&
    record(request.preconditions) && Object.keys(request.preconditions).length === 0 &&
    typeof intent.key === "string" && intent.key.length > 0 && intent.key.length <= 200;
  // Reconstruct known fields only. Damaged accepted requests remain read-back-only.
  saved.intent = {key: valid ? intent.key : null, request: valid ? {action: "session.permissions",
    target: {...target}, params: {mode: request.params.mode}, preconditions: {}} : null,
    operation_id: operationId(intent.operation_id) ? intent.operation_id : null};
  if (valid && !saved.intent.operation_id && admissionRefusals.has(intent.refused)) saved.intent.refused = intent.refused;
  if (valid) saved.mode = request.params.mode;
  return saved;
}
export function permissionsPanel({h, t, api, caps, guard, errorBox, opStatus, storageKey, target, session, ready}) {
  let raw;
  try {raw = JSON.parse(localStorage.getItem(storageKey));} catch { /* memory only */ }
  let saved = restore(raw, target), operation = null, busy = false, refreshing = null, submission = null, readFailed = false;
  const mode = h("select", {"aria-label": t("permissions_mode")},
    ...["default", "allow_all"].map(value => h("option", {value}, t("permissions_" + value))));
  mode.value = saved.mode;
  const message = h("div", {role: "status"}), result = h("div", {"data-permission-result": ""});
  const explanation = h("p", {class: "muted"}), restriction = h("p", {class: "muted"});
  const persist = () => {guard(); try {localStorage.setItem(storageKey, JSON.stringify(saved));} catch { /* memory only */ }};
  const current = () => {try {guard(); return true;} catch {return false;}};
  const writable = () => ready() && session()?.api_access === "managed" &&
    session()?.provenance === "connector_managed" && (caps()?.scopes || []).includes("operate") &&
    caps()?.hosts?.some(host => host.host === target.host && host.writes === true) &&
    caps()?.actions?.some(action => action.action === "session.permissions" && action.allowed === true);
  const accept = candidate => {
    guard();
    if (!saved.intent || !operationId(candidate?.operation_id) || candidate.action !== "session.permissions" ||
        candidate.target?.host !== target.host || candidate.target?.session_id !== target.session_id ||
        !modeValue(candidate.params?.mode) || (saved.intent.request && candidate.params.mode !== saved.intent.request.params.mode) ||
        (saved.intent.key && candidate.idempotency_key !== saved.intent.key) ||
        (saved.intent.operation_id && candidate.operation_id !== saved.intent.operation_id))
      throw new Error(t("permissions_invalid_result"));
    operation = candidate; saved.intent.operation_id = candidate.operation_id;
    saved.mode = candidate.params.mode; mode.value = saved.mode; readFailed = false; persist();
    message.replaceChildren(); update();
  };
  const apply = h("button", {class: "secondary", onclick: async () => {
    if (!current() || busy || readFailed || !writable() || saved.intent?.operation_id ||
        (saved.intent && (!saved.intent.request || saved.intent.refused))) return;
    busy = true;
    if (!saved.intent) {
      // Session observations currently expose no authoritative control_version. Central admission
      // binds the current task incarnation; unrelated journal/resource versions must not be sent.
      saved.intent = {key: crypto.randomUUID(), request: {action: "session.permissions", target: {...target},
        params: {mode: saved.mode}, preconditions: {}}, operation_id: null};
      persist();
    }
    const intent = saved.intent; update();
    submission = (async () => {
      try {
        const response = await api("POST", "/operations?wait=3", intent.request, intent.key); guard();
        if (saved.intent === intent) accept(response.operation);
      } catch (error) {
        if (current()) {
          if (error.status >= 400 && error.status < 500 && admissionRefusals.has(error.code)) {
            intent.refused = error.code; persist();
          }
          message.replaceChildren(errorBox(error));
        }
      } finally {busy = false; if (current()) update();}
    })();
    try {await submission;} finally {submission = null;}
  }}, t("permissions_apply"));
  const check = h("button", {class: "secondary", onclick: () => refresh(true).catch(showError)}, t("permissions_check"));
  const another = h("button", {class: "secondary", onclick: () => {
    if (!current() || busy || refreshing || readFailed || !(terminal(operation) || saved.intent?.refused) || !writable()) return;
    saved = {mode: "default"}; mode.value = saved.mode; operation = null; persist(); message.replaceChildren(); update();
  }}, t("permissions_new"));
  const box = disclosure({label: t("permissions_title"), body: h("div", {},
    h("p", {class: "muted"}, t("permissions_help")),
    h("label", {class: "permission-mode"}, t("permissions_mode"), mode), explanation,
    h("div", {class: "actions"}, apply, check, another), restriction, result, message)});
  box.classList.add("permissions");
  box.dataset.permissions = "";
  mode.addEventListener("change", () => {
    if (!current() || saved.intent) {mode.value = saved.mode; return;}
    saved.mode = modeValue(mode.value) ? mode.value : "default"; persist(); update();
  });
  function update() {
    const managed = session()?.api_access === "managed" && session()?.provenance === "connector_managed";
    box.hidden = !managed;
    mode.disabled = busy || Boolean(saved.intent);
    apply.hidden = Boolean(saved.intent?.operation_id || saved.intent?.refused);
    apply.disabled = busy || readFailed || !writable() || Boolean(saved.intent && !saved.intent.request);
    apply.textContent = t(saved.intent ? "permissions_retry" : "permissions_apply");
    check.hidden = !saved.intent?.operation_id; check.disabled = busy || Boolean(refreshing);
    another.hidden = !(terminal(operation) || saved.intent?.refused); another.disabled = busy || Boolean(refreshing) || readFailed || !writable();
    explanation.textContent = t(saved.mode === "allow_all" ? "permissions_allow_help" : "permissions_default_help");
    restriction.textContent = writable() ? "" : t("permissions_unavailable");
    result.replaceChildren();
    if (saved.intent) {
      result.append(h("p", {}, operation ? opStatus(operation) : t(saved.intent.refused ? "permissions_refused" : "permissions_unknown"), " ",
        saved.intent.operation_id ? h("a", {href: `#/op/${saved.intent.operation_id}`}, t("permissions_details")) : null,
        operation?.status_reason ? ` · ${operation.status_reason}` : ""));
      result.append(h("p", {class: "muted"}, t("permissions_fixed")));
      if (operation?.status === "succeeded") result.append(h("p", {class: "muted"}, t("permissions_accepted"),
        operation.result?.agent_kind === "codex" ? " " + t("permissions_next_turn") : ""));
      if (!saved.intent.request && !saved.intent.operation_id) result.append(h("p", {class: "error"}, t("permissions_damaged")));
    }
  }
  function showError(error) {if (current()) {readFailed = true; message.replaceChildren(errorBox(error)); update();}}
  async function refresh(fresh = false) {
    // An operation event may precede the first POST reply. Wait for its identity,
    // then read again before acknowledging the event rather than keeping an older reply snapshot.
    if (submission) {await submission; guard();}
    if (refreshing) {await refreshing; if (fresh) return refresh(true); return;}
    if (!saved.intent?.operation_id) return;
    const intent = saved.intent;
    refreshing = (async () => {
      const response = await api("GET", `/operations/${intent.operation_id}`); guard();
      if (saved.intent === intent) accept(response.operation);
    })();
    update();
    try {await refreshing;} catch (error) {showError(error); throw error;}
    finally {refreshing = null; if (current()) update();}
  }
  update();
  return {box, update, refresh};
}
