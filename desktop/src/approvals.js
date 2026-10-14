import {emptyState} from './ui/components.js';
// A fixed, reviewed selection. Runtime events refresh receipts, never select new prompts.
const object = value => value && typeof value === "object" && !Array.isArray(value);
const opId = value => typeof value === "string" && /^op_[0-9a-f]{32}$/.test(value);
const mode = value => value === null || value === "default" || value === "allow_all";
const terminal = op => ["succeeded", "failed", "cancelled"].includes(op?.status);
const equal = (a, b) => {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => equal(v, b[i]));
  return object(a) && object(b) && Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(k => equal(a[k], b[k]));
};
// OperationService replays an accepted key before these static admission checks.
// These exact errors prove this selection was not accepted; auth/conflict errors do not.
const refusedBeforeAdmission = new Set(["BULK_PREVIEW_INVALID", "BULK_PREVIEW_EXPIRED", "BULK_PREVIEW_MISMATCH", "BULK_MODE_REFUSED",
  "CONTROL_VERSION_CONFLICT", "BULK_BINDING_CHANGED"]);
function validPreview(p) {
  return object(p) && typeof p.host === "string" && p.host.length > 0 &&
    (p.workspace === null || typeof p.workspace === "string") && typeof p.preview_token === "string" &&
    p.preview_token.startsWith("bap1.") && p.preview_token.length <= 262144 && /^[0-9a-f]{64}$/.test(p.fingerprint) &&
    Number.isFinite(p.expires_at) && equal(p.answer, {permission: "allow", dont_ask_again: true}) &&
    Array.isArray(p.items) && p.items.length <= 50 && new Set(p.items.map(i => i?.item_id)).size === p.items.length &&
    p.items.every(i => object(i) && /^bapi_[0-9a-f]{24}$/.test(i.item_id) && i.host === p.host &&
      typeof i.session_id === "string" && typeof i.eligible === "boolean" && (!i.eligible ||
        (object(i.prompt) && typeof i.prompt.toolUseId === "string" && Array.isArray(i.allowed_modes) &&
          i.allowed_modes.includes(null) && i.allowed_modes.every(mode))));
}
function validRequest(r) {
  return r?.action === "session.approve_pending" && typeof r.target?.host === "string" &&
    Object.keys(r.target).length === 1 && typeof r.params?.preview_token === "string" &&
    Object.keys(r.params).length === 2 && Array.isArray(r.params.selection) && r.params.selection.length > 0 &&
    r.params.selection.length <= 50 && r.params.selection.every(s => object(s) && Object.keys(s).length === 2 &&
      /^bapi_[0-9a-f]{24}$/.test(s.item_id) && mode(s.mode)) &&
    new Set(r.params.selection.map(s => s.item_id)).size === r.params.selection.length &&
    object(r.preconditions) && Object.keys(r.preconditions).length === 1 && /^[0-9a-f]{64}$/.test(r.preconditions.expected_fingerprint);
}
export function approvalsPanel({h, t, api, caps, guard, ready, errorBox, opStatus, storageKey}) {
  let raw;
  try {raw = JSON.parse(localStorage.getItem(storageKey));} catch { /* memory only */ }
  let saved = {host: typeof raw?.host === "string" ? raw.host : "", workspace: typeof raw?.workspace === "string" ? raw.workspace : ""};
  if (validPreview(raw?.preview) && raw.preview.host === saved.host && (raw.preview.workspace || "") === saved.workspace) saved.preview = raw.preview;
  if (raw?.intent) saved.intent = {
    request: validRequest(raw.intent.request) ? raw.intent.request : null,
    key: typeof raw.intent.key === "string" && raw.intent.key.length > 0 && raw.intent.key.length <= 200 ? raw.intent.key : null,
    operation_id: opId(raw.intent.operation_id) ? raw.intent.operation_id : null,
    refused: refusedBeforeAdmission.has(raw.intent.refused) && !raw.intent.operation_id ? raw.intent.refused : null,
  };
  let operation = null, busy = false, submission = null, refreshing = null, readFailed = false;
  const selected = new Map(), controls = [];
  if (saved.preview && !saved.intent && Array.isArray(raw?.selection)) {
    for (const s of raw.selection) {
      if (object(s) && saved.preview.items.some(i => i.eligible && i.item_id === s.item_id && i.allowed_modes.includes(s.mode)))
        selected.set(s.item_id, s.mode);
    }
  }
  const current = () => {try {guard(); return true;} catch {return false;}};
  const persist = (required = false) => {guard(); saved.selection = [...selected].map(([item_id, mode]) => ({item_id, mode}));
    try {localStorage.setItem(storageKey, JSON.stringify(saved));} catch (error) {if (required) throw error;}};
  const observable = () => ready() && caps()?.scopes?.includes("observe");
  const writable = () => observable() && caps()?.scopes?.includes("operate") &&
    caps()?.actions?.some(a => a.action === "session.approve_pending" && a.allowed === true) &&
    caps()?.hosts?.some(host => host.host === (saved.intent?.request?.target.host || saved.host) && host.writes === true);
  const host = h("select", {"aria-label": t("host")}, h("option", {value: ""}, t("bulk_choose_host")),
    ...(caps()?.hosts || []).map(x => h("option", {value: x.host}, x.host)));
  host.value = saved.host;
  const workspace = h("input", {"aria-label": t("bulk_workspace"), placeholder: t("bulk_workspace"), value: saved.workspace});
  const rows = h("div", {"data-approval-items": ""}), result = h("div", {"data-approval-result": ""}), status = h("div", {role: "status"});
  const summary = h("p", {class: "muted"});
  const previewButton = h("button", {class: "secondary", onclick: async () => {
    if (!current() || busy || saved.intent || !observable() || !host.value) return;
    saved.host = host.value; saved.workspace = workspace.value.trim(); workspace.value = saved.workspace;
    delete saved.preview; selected.clear(); busy = true; persist(); renderPreview(); update();
    try {
      const p = await api("POST", "/approval-previews", {host: saved.host, ...(saved.workspace ? {workspace: saved.workspace} : {})}); guard();
      if (!validPreview(p) || p.host !== saved.host || (p.workspace || "") !== saved.workspace) throw new Error(t("bulk_invalid_preview"));
      saved.preview = p; persist(); status.replaceChildren(); renderPreview();
    } catch (e) {if (current()) status.replaceChildren(errorBox(e));}
    finally {busy = false; if (current()) update();}
  }}, t("bulk_preview"));
  const accept = candidate => {
    guard(); const intent = saved.intent;
    if (!intent || !opId(candidate?.operation_id) || !intent.request || !intent.key ||
        candidate.actor !== caps()?.actor || candidate.idempotency_key !== intent.key ||
        !equal({action: candidate.action, target: candidate.target, params: candidate.params, preconditions: candidate.preconditions}, intent.request) ||
        (intent.operation_id && candidate.operation_id !== intent.operation_id)) throw new Error(t("bulk_invalid_result"));
    operation = candidate; intent.operation_id = candidate.operation_id; readFailed = false; persist(); status.replaceChildren(); update();
  };
  const apply = h("button", {class: "primary", onclick: async () => {
    if (!current() || busy || readFailed || !writable() || saved.intent?.operation_id || saved.intent?.refused) return;
    if (!saved.intent) {
      if (!saved.preview || saved.preview.expires_at * 1000 <= Date.now() || !selected.size) {update(); return;}
      const selection = saved.preview.items.filter(i => selected.has(i.item_id)).map(i => ({item_id: i.item_id, mode: selected.get(i.item_id)}));
      saved.intent = {key: crypto.randomUUID(), operation_id: null, request: {action: "session.approve_pending", target: {host: saved.preview.host},
        params: {preview_token: saved.preview.preview_token, selection}, preconditions: {expected_fingerprint: saved.preview.fingerprint}}};
    }
    if (!saved.intent.request || !saved.intent.key) return;
    try {persist(true);} catch (error) {status.replaceChildren(errorBox(error)); update(); return;}
    busy = true; const intent = saved.intent; update();
    submission = (async () => {
      try {const data = await api("POST", "/operations?wait=3", intent.request, intent.key); guard(); accept(data.operation);}
      catch (e) {
        if (current()) {
          if (e.status >= 400 && e.status < 500 && refusedBeforeAdmission.has(e.code)) {intent.refused = e.code; persist();}
          status.replaceChildren(errorBox(e));
        }
      } finally {busy = false; if (current()) update();}
    })();
    try {await submission;} finally {submission = null;}
  }}, t("bulk_apply"));
  const check = h("button", {class: "secondary", onclick: () => refresh(true).catch(() => {})}, t("bulk_check"));
  const another = h("button", {class: "secondary", onclick: () => {
    if (!current() || busy || refreshing || readFailed || !(terminal(operation) || saved.intent?.refused)) return;
    delete saved.intent; delete saved.preview; selected.clear(); operation = null; persist(); status.replaceChildren(); renderPreview(); update();
  }}, t("bulk_new"));
  const box = h("section", {"data-approvals": ""},
    h("div", {class: "panel"}, h("div", {class: "filters"}, host, workspace, previewButton), h("p", {class: "muted"}, t("bulk_scope"))),
    rows, h("div", {class: "panel"}, h("p", {class: "note"}, t("bulk_effect")), summary,
      h("div", {class: "actions"}, apply, check, another), result, status));
  host.onchange = workspace.oninput = () => {
    if (!current() || busy || saved.intent) return;
    saved.host = host.value; saved.workspace = workspace.value; delete saved.preview; selected.clear(); persist(); renderPreview(); update();
  };
  function renderPreview() {
    controls.length = 0; rows.replaceChildren();
    if (!saved.preview) return;
    if (saved.preview.truncated) rows.append(h("p", {class: "note"}, t("bulk_truncated")));
    if (!saved.preview.items.length) rows.append(emptyState({title:t('bulk_empty')}));
    for (const item of saved.preview.items) {
      const choice = h("input", {type: "checkbox", "aria-label": t("bulk_select", {id: item.session_id})});
      const requestMode = saved.intent?.request?.params.selection.find(s => s.item_id === item.item_id);
      choice.checked = Boolean(requestMode) || selected.has(item.item_id);
      const select = h("select", {"aria-label": t("bulk_mode", {id: item.session_id})},
        ...(item.allowed_modes || [null]).map(value => h("option", {value: value || ""}, t(value === null ? "bulk_no_mode" : "permissions_" + value))));
      select.value = requestMode?.mode || selected.get(item.item_id) || "";
      choice.onchange = () => {if (!current() || saved.intent || busy) return;
        if (choice.checked) selected.set(item.item_id, select.value || null); else selected.delete(item.item_id); persist(); update();};
      select.onchange = () => {if (!current() || saved.intent || busy) return;
        if (selected.has(item.item_id)) selected.set(item.item_id, select.value || null); persist(); update();};
      const warning = h("p", {class: "muted"});
      controls.push({item, choice, select, warning});
      rows.append(h("article", {class: "panel bulk-item", "data-approval-item": item.item_id},
        h("div", {class: "row"}, choice, h("div", {class: "grow"},
          h("a", {class: "title", href: `#/session/${encodeURIComponent(item.host)}/${encodeURIComponent(item.session_id)}`}, item.session_id),
          h("div", {class: "muted"}, [item.host, item.agent_kind].filter(Boolean).join(" · ")))),
        item.eligible ? [h("p", {class: "title"}, item.prompt.toolName || t("bulk_answer")),
          h("pre", {class: "pre bulk-prompt"}, typeof item.prompt.input === "string" ? item.prompt.input
            : Object.keys(item.prompt.input || {}).length === 1 && typeof item.prompt.input?.command === "string"
              ? item.prompt.input.command : JSON.stringify(item.prompt.input ?? item.prompt, null, 2)),
          h("details", {class: "bulk-evidence"}, h("summary", {}, t("bulk_full_prompt")),
            h("pre", {class: "pre bulk-prompt"}, JSON.stringify(item.prompt, null, 2))), select, warning]
          : h("p", {class: "muted"}, t("bulk_blocked"), " · ", item.code || t("obs_unknown"))));
    }
  }
  function update() {
    const fixed = Boolean(saved.intent), expired = saved.preview && saved.preview.expires_at * 1000 <= Date.now();
    host.disabled = workspace.disabled = busy || fixed;
    previewButton.disabled = busy || fixed || !observable() || !host.value;
    for (const c of controls) {
      c.choice.disabled = busy || fixed || !c.item.eligible || !writable() || expired;
      c.select.disabled = c.choice.disabled || !c.choice.checked;
      c.warning.textContent = c.select.value === "allow_all" ? t("permissions_allow_help") : "";
    }
    apply.hidden = Boolean(saved.intent?.operation_id || saved.intent?.refused);
    apply.textContent = t(fixed ? "permissions_retry" : "bulk_apply");
    apply.disabled = busy || readFailed || !writable() || (fixed ? !saved.intent.request || !saved.intent.key : !selected.size || expired);
    check.hidden = !saved.intent?.operation_id; check.disabled = busy || Boolean(refreshing);
    another.hidden = !(terminal(operation) || saved.intent?.refused); another.disabled = busy || Boolean(refreshing) || readFailed;
    summary.textContent = fixed ? t(saved.intent.refused ? "bulk_refused" : "bulk_fixed") : expired ? t("bulk_expired")
      : t("bulk_selected", {count: selected.size});
    result.replaceChildren();
    if (!fixed) return;
    result.append(h("p", {}, operation ? opStatus(operation) : t("permissions_unknown"), " ",
      saved.intent.operation_id ? h("a", {href: `#/op/${saved.intent.operation_id}`}, t("permissions_details")) : null));
    if (!saved.intent.request || !saved.intent.key) result.append(h("p", {class: "error"}, t("permissions_damaged")));
    if (!operation) return;
    // Parent succeeded means processing ended. Only individual receipts prove each approval/mode.
    const items = operation.result?.items || operation.external_refs?.bulk_items || [];
    result.append(h("p", {class: "muted"}, t(operation.result?.all_succeeded === true ? "bulk_all_proven" : "bulk_partial")));
    for (const item of items) {
      result.append(h("div", {class: "row bulk-receipt"}, h("div", {class: "grow"}, item.session_id,
        h("div", {class: "muted"}, t(item.complete === true ? "bulk_item_complete" : "bulk_item_incomplete"))),
        ...["answer", "permissions"].filter(phase => item[phase]).map(phase => h("span", {}, t("bulk_" + phase), ": ",
          opId(item[phase].operation_id) ? h("a", {href: `#/op/${item[phase].operation_id}`}, item[phase].status || t("obs_unknown"))
            : item[phase].status || t("obs_unknown"), item[phase].code ? ` · ${item[phase].code}` : ""))));
    }
  }
  async function refresh(fresh = false) {
    if (submission) {await submission; guard();}
    if (refreshing) {await refreshing; if (fresh) return refresh(true); return;}
    if (!saved.intent?.operation_id) {update(); return;}
    refreshing = (async () => {const data = await api("GET", `/operations/${saved.intent.operation_id}`); guard(); accept(data.operation);})();
    update();
    try {await refreshing;} catch (e) {if (current()) {readFailed = true; status.replaceChildren(errorBox(e)); update();} throw e;}
    finally {refreshing = null; if (current()) update();}
  }
  renderPreview(); update();
  return {box, update, refresh};
}
