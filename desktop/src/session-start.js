import {skeleton} from './ui/components.js';
// One fixed standalone start. Reload and events only read accepted operations.
import {composerShortcut} from './composer-shortcut.js';
import {modelChoice} from './model-preferences.js';
const object = v => v && typeof v === "object" && !Array.isArray(v);
const operationId = v => typeof v === "string" && /^op_[0-9a-f]{32}$/.test(v);
const terminal = op => ["succeeded", "failed", "cancelled"].includes(op?.status);
const equal = (a, b) => a === b || (object(a) && object(b) && Object.keys(a).length === Object.keys(b).length &&
  Object.keys(a).every(k => equal(a[k], b[k])));
const text = (v, max) => typeof v === "string" && v.trim().length > 0 && v.length <= max;
const fields = ["host", "workspace", "agent", "model", "title", "prompt"];
// This action's host tier check runs after existing-key replay and before INSERT.
// Scope/auth, generic validation and key conflicts are not no-admission proof.
const admissionRefusals = new Set(["TIER_DISABLED", "START_WORKTREE_REQUIRED"]);
function validRequest(r) {
  return r?.action === "session.start" && object(r.target) && Object.keys(r.target).length === 2 &&
    text(r.target.host, 256) && text(r.target.workspace, 256) && object(r.params) &&
    Object.keys(r.params).every(k => ["agent", "model", "title", "prompt", "use_worktree"].includes(k)) &&
    ["claude", "codex"].includes(r.params.agent) && r.params.use_worktree === true &&
    ["model", "title", "prompt"].every(k => !(k in r.params) || text(r.params[k], k === "prompt" ? 20000 : 256)) &&
    object(r.preconditions) && Object.keys(r.preconditions).length === 0;
}
export function sessionStartPanel({h, t, api, caps, guard, ready, errorBox, opStatus, storageKey, submitPreference, initialHost}) {
  let raw;
  try {raw = JSON.parse(localStorage.getItem(storageKey));} catch { /* no saved draft */ }
  let saved = Object.fromEntries(fields.map(k => [k, typeof raw?.[k] === "string" ? raw[k] : k === "agent" ? "claude" : ""]));
  if (!["claude", "codex"].includes(saved.agent)) saved.agent = "claude";
  if (raw?.intent) {
    const valid = validRequest(raw.intent.request) && text(raw.intent.key, 200);
    saved.intent = {request: valid ? raw.intent.request : null, key: valid ? raw.intent.key : null,
      operation_id: operationId(raw.intent.operation_id) ? raw.intent.operation_id : null,
      refused: !raw.intent.operation_id && admissionRefusals.has(raw.intent.refused) ? raw.intent.refused : null};
    if (valid) Object.assign(saved, raw.intent.request.target, {model: "", title: "", prompt: ""}, raw.intent.request.params);
  }
  // A route hint can change an editable draft, never its original frozen operation.
  if (!saved.intent && initialHost && caps()?.hosts?.some(x => x.host === initialHost) && saved.host !== initialHost) {
    saved.host = initialHost; saved.workspace = "";
  }
  let operation = null, busy = false, submission = null, refreshing = null, readFailed = false;
  let workspaces = [], discovery = 0, discovering = false, discoveredHost = "";
  const current = () => {try {guard(); return true;} catch {return false;}};
  const persist = () => {guard(); localStorage.setItem(storageKey, JSON.stringify(saved));};
  const observe = () => caps()?.scopes?.includes("observe");
  const allowed = () => observe() && caps()?.scopes?.includes("start") &&
    caps()?.actions?.some(a => a.action === "session.start" && a.allowed === true);
  const hostAllowed = () => caps()?.hosts?.some(x => x.host === saved.host && x.writes === true && x.orchestrate === true);
  const status = h("div", {role: "status"}), result = h("div", {"data-start-result": ""}), discoveryStatus = h("p", {class: "muted", role: "status"});
  const host = h("select", {"aria-label": t("host")}, h("option", {value: ""}, t("start_choose_host")),
    ...(caps()?.hosts || []).map(x => h("option", {value: x.host}, x.host)));
  if (saved.host && ![...host.options].some(o => o.value === saved.host)) host.append(h("option", {value: saved.host}, saved.host));
  const workspace = h("select", {"aria-label": t("start_workspace")});
  const agent = h("select", {"aria-label": t("start_agent")}, h("option", {value: "claude"}, "Claude"), h("option", {value: "codex"}, "Codex"));
  const model = h("input", {"aria-label": t("start_model"), maxlength: 256, placeholder: t("start_model_default")});
  const title = h("input", {"aria-label": t("start_title"), maxlength: 256});
  const prompt = h("textarea", {"aria-label": t("start_prompt"), maxlength: 20000, rows: 6});
  const inputs = {host, workspace, agent, model, title, prompt};
  const models = modelChoice({h, t, api, caps, guard, host: () => saved.host, agent, model, submit: submitPreference, storageKey});
  const fill = () => {for (const [k, el] of Object.entries(inputs)) el.value = saved[k];};
  const showError = e => {if (current()) status.replaceChildren(errorBox(e));};
  function renderWorkspaces() {
    workspace.replaceChildren(h("option", {value: ""}, t("start_choose_workspace")), ...workspaces.map(w =>
      h("option", {value: w.workspace_id}, `${w.name || w.workspace_id} · ${w.workspace_id}`)));
    if (saved.workspace && ![...workspace.options].some(o => o.value === saved.workspace))
      workspace.append(h("option", {value: saved.workspace}, saved.workspace));
    workspace.value = saved.workspace;
  }
  async function discover() {
    if (!current() || saved.intent || !observe() || !saved.host) return;
    models.refresh();
    const expected = ++discovery, selectedHost = saved.host;
    discovering = true; discoveredHost = ""; workspaces = []; renderWorkspaces(); update();
    discoveryStatus.replaceChildren(skeleton(2));
    try {
      const doc = await api("GET", `/workspaces?host=${encodeURIComponent(selectedHost)}&limit=200`); guard();
      if (expected !== discovery || saved.host !== selectedHost || saved.intent) return;
      if (!Array.isArray(doc.workspaces) || doc.workspaces.length > 200 || typeof doc.has_more !== "boolean" ||
          !object(doc.errors) || Object.keys(doc.errors).length || doc.workspaces.some(w => !object(w) ||
            w.host !== selectedHost || !text(w.workspace_id, 256) || typeof w.folder !== "string") ||
          new Set(doc.workspaces.map(w => w.workspace_id)).size !== doc.workspaces.length)
        throw new Error(t("start_discovery_failed"));
      workspaces = doc.workspaces; discoveredHost = selectedHost; renderWorkspaces();
      discoveryStatus.textContent = t(doc.has_more ? "start_truncated" : !workspaces.length ? "start_no_workspaces" : "start_workspace_help");
    } catch (e) {if (current() && expected === discovery) {discoveryStatus.textContent = t("start_discovery_failed"); showError(e);}}
    finally {if (expected === discovery && current()) {discovering = false; update();}}
  }
  const reload = h("button", {class: "secondary", onclick: discover}, t("start_reload_workspaces"));
  const accept = candidate => {
    guard(); const intent = saved.intent;
    if (!intent || !intent.request || !intent.key || !operationId(candidate?.operation_id) || candidate.actor !== caps()?.actor ||
        candidate.idempotency_key !== intent.key || !equal({action: candidate.action, target: candidate.target,
          params: candidate.params, preconditions: candidate.preconditions}, intent.request) ||
        (intent.operation_id && intent.operation_id !== candidate.operation_id)) throw new Error(t("start_invalid_result"));
    for (const proof of [candidate.result, candidate.external_refs?.start_result]) {
      if (proof?.started === true && (proof.host !== intent.request.target.host || !text(proof.session_id, 256) ||
          proof.session_id !== candidate.external_refs?.session_id)) throw new Error(t("start_invalid_result"));
    }
    operation = candidate; intent.operation_id = candidate.operation_id; readFailed = false; persist(); status.replaceChildren(); update();
  };
  function request() {
    const params = {agent: saved.agent, use_worktree: true};
    for (const key of ["model", "title", "prompt"]) if (saved[key] !== "") params[key] = saved[key];
    return {action: "session.start", target: {host: saved.host, workspace: saved.workspace}, params, preconditions: {}};
  }
  const apply = h("button", {class: "primary", onclick: async () => {
    if (!current() || busy || readFailed || !ready() || !allowed() || saved.intent?.operation_id || saved.intent?.refused) return;
    if (!saved.intent) {
      if (!hostAllowed() || discoveredHost !== saved.host || !workspaces.some(w => w.workspace_id === saved.workspace) || !validRequest(request())) return;
      saved.intent = {request: request(), key: crypto.randomUUID(), operation_id: null};
    }
    if (!saved.intent.request || !saved.intent.key) return;
    try {persist();} catch (e) {showError(e); update(); return;} // never submit without the durable original envelope/key
    busy = true; update(); const intent = saved.intent;
    submission = (async () => {
      try {const data = await api("POST", "/operations?wait=3", intent.request, intent.key); guard(); accept(data.operation);}
      catch (e) {if (current()) {if (e.status >= 400 && e.status < 500 && admissionRefusals.has(e.code)) {
        intent.refused = e.code; try {persist();} catch { /* original intent remains frozen */ }
      } showError(e);}}
      finally {busy = false; if (current()) update();}
    })();
    try {await submission;} finally {submission = null;}
  }}, t("start_apply"));
  const check = h("button", {class: "secondary", onclick: () => refresh(true).catch(() => {})}, t("permissions_check"));
  const another = h("button", {class: "secondary", onclick: async () => {
    if (!current() || busy || refreshing || readFailed || !(terminal(operation) || saved.intent?.refused)) return;
    const previous = saved;
    saved = {host: saved.host, workspace: "", agent: "claude", model: "", title: "", prompt: ""};
    try {persist();} catch (e) {saved = previous; showError(e); return;}
    operation = null; status.replaceChildren(); fill(); renderWorkspaces(); update(); await discover();
  }}, t("start_new"));
  for (const [key, el] of Object.entries(inputs)) el.addEventListener(key === "host" || key === "workspace" || key === "agent" ? "change" : "input", () => {
    if (!current() || saved.intent || busy) {fill(); return;}
    saved[key] = el.value;
    if (key === "host") {saved.workspace = ""; discoveredHost = ""; workspaces = []; discovery++; renderWorkspaces();}
    try {persist();} catch (e) {showError(e);} update(); if (key === "host") discover();
  });
  const label = (name, control) => h("label", {}, t(name), control);
  const shortcut = composerShortcut({h, t, input: prompt, button: apply, storageKey: `${storageKey}.shortcut`, guard});
  const box = h("section", {class: "session-start", "data-session-start": ""},
    h("div", {class: "panel"}, h("div", {class: "capture-fields"}, label("host", host), label("start_workspace", workspace)),
      h("div", {class: "actions"}, reload), discoveryStatus, h("p", {class: "muted"}, t("start_isolation"))),
    h("div", {class: "panel"}, h("div", {class: "capture-fields"}, label("start_agent", agent), label("start_model", model), label("start_title", title)),
      models.box, label("start_prompt", prompt), shortcut.box, h("p", {class: "muted"}, t("start_prompt_help"))),
    h("div", {class: "actions"}, apply, check, another), result, status);
  function update() {
    const fixed = Boolean(saved.intent);
    for (const el of Object.values(inputs)) el.disabled = busy || fixed;
    models.update();
    workspace.disabled ||= discovering || !saved.host;
    reload.hidden = fixed; reload.disabled = discovering || !observe() || !saved.host;
    apply.hidden = Boolean(saved.intent?.operation_id || saved.intent?.refused);
    apply.textContent = t(fixed ? "permissions_retry" : "start_apply");
    apply.disabled = busy || readFailed || !ready() || !allowed() || (fixed ? !saved.intent.request || !saved.intent.key :
      !hostAllowed() || discoveredHost !== saved.host || !workspaces.some(w => w.workspace_id === saved.workspace) || !validRequest(request()));
    check.hidden = !saved.intent?.operation_id; check.disabled = busy || Boolean(refreshing);
    another.hidden = !(terminal(operation) || saved.intent?.refused); another.disabled = busy || Boolean(refreshing) || readFailed;
    result.replaceChildren();
    if (!allowed()) result.append(h("p", {class: "muted"}, t("start_unavailable")));
    if (!fixed) return;
    result.append(h("p", {}, operation ? opStatus(operation) : t(saved.intent.refused ? "start_refused" : "start_unknown"), " ",
      saved.intent.operation_id ? h("a", {href: `#/op/${saved.intent.operation_id}`}, t("permissions_details")) : null,
      operation?.status_reason ? ` · ${operation.status_reason}` : ""));
    result.append(h("p", {class: "muted"}, t("start_fixed")));
    if (!saved.intent.request || !saved.intent.key) result.append(h("p", {class: "error"}, t("permissions_damaged")));
    const proof = operation?.result?.started === true ? operation.result : operation?.external_refs?.start_result;
    if (proof?.started === true) {
      result.append(h("p", {}, t("start_started"), " ", h("a", {href: `#/session/${encodeURIComponent(proof.host)}/${encodeURIComponent(proof.session_id)}`}, proof.session_id)));
      const sent = operation?.result?.prompt_sent;
      result.append(h("p", {class: "muted"}, t(!saved.intent.request?.params.prompt ? "start_without_prompt" : sent === true ? "start_prompt_accepted" : "start_prompt_unknown")));
    }
  }
  async function refresh(fresh = false) {
    if (submission) {await submission; guard();}
    if (refreshing) {await refreshing; if (fresh) return refresh(true); return;}
    if (!saved.intent?.operation_id) {update(); return;}
    refreshing = (async () => {const doc = await api("GET", `/operations/${saved.intent.operation_id}`); guard(); accept(doc.operation);})();
    update();
    try {await refreshing;} catch (e) {if (current()) {readFailed = true; showError(e); update();} throw e;}
    finally {refreshing = null; if (current()) update();}
  }
  renderWorkspaces(); fill(); update();
  return {box, update, refresh, init: async () => {if (saved.intent) await refresh(); else await discover();}};
}
