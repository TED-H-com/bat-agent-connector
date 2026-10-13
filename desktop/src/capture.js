import {timeStamp} from './ui/stamps.js';
// A reviewed remote read, never a local file picker or a directory browser.
const record = value => value && typeof value === "object" && !Array.isArray(value);
const digest = value => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const operationId = value => typeof value === "string" && /^op_[0-9a-f]{32}$/.test(value);
const previewId = value => typeof value === "string" && /^acpv_[0-9a-f]{32}$/.test(value);
const previewToken = value => typeof value === "string" && value.length > 0 && value.length <= 24576;
const validPreview = doc => record(doc) && record(doc.source) && record(doc.evidence) &&
  [doc.relative_path, doc.source.host, doc.source.session_id, doc.source.root, doc.source.repository_root, doc.evidence.head_sha]
    .every(value => typeof value === "string" && value.length > 0) &&
  doc.source.provenance === "manual" && doc.snapshot === false && Number.isFinite(doc.expires_at) &&
  previewId(doc.preview_id) && previewToken(doc.preview_token) && digest(doc.fingerprint) && digest(doc.evidence.digest) &&
  Number.isSafeInteger(doc.evidence.size_bytes) && doc.evidence.size_bytes >= 0;
function restore(value, source) {
  const saved = {input: {...source, relative_path: ""}};
  if (!record(value)) return saved;
  if (record(value.input)) for (const key of ["host", "session_id", "relative_path"])
    if (typeof value.input[key] === "string") saved.input[key] = value.input[key];
  if (validPreview(value.preview)) saved.preview = value.preview;
  const intent = value.intent, request = intent?.request;
  const usable = request?.action === "artifact.capture" && previewId(request.target?.preview_id) &&
    previewToken(request.params?.preview_token) && digest(request.preconditions?.expected_fingerprint) &&
    typeof intent.key === "string" && intent.key.length > 0 && intent.key.length <= 200;
  if (usable || operationId(intent?.operation_id)) {
    // Damaged storage must not mint a replacement intent for an already accepted operation.
    saved.intent = {key: usable ? intent.key : null, request: usable ? request : null,
      operation_id: operationId(intent.operation_id) ? intent.operation_id : null,
      reviewed_digest: digest(intent.reviewed_digest) ? intent.reviewed_digest : saved.preview?.evidence.digest};
  }
  return saved;
}
export function capturePanel({h, t, api, caps, guard, onEvents, errorBox, storageKey, source = {}, onAttach}) {
  let saved;
  try { saved = JSON.parse(localStorage.getItem(storageKey)); } catch { /* retain in memory */ }
  saved = restore(saved, source);
  let busy = false, revision = 0, disposed = false, refreshing = null;
  const host = h("select", {"aria-label": t("capture_host")}, h("option", {value: ""}, t("capture_host")),
    ...(caps()?.hosts || []).map(item => h("option", {value: item.host}, item.host)));
  const session = h("input", {"aria-label": t("capture_session"), placeholder: "sess-…", maxlength: 256});
  const path = h("input", {"aria-label": t("capture_path"), placeholder: "notes/input.txt"});
  host.value = saved.input.host || ""; session.value = saved.input.session_id || ""; path.value = saved.input.relative_path || "";
  const notice = h("div", {role: "status"}), evidence = h("div", {"data-capture-evidence": ""});
  const reviewed = h("input", {type: "checkbox", onchange: () => update()});
  const review = h("label", {class: "capture-choice"}, reviewed, " ", t("capture_review"));
  const persist = () => { guard(); try {localStorage.setItem(storageKey, JSON.stringify(saved));} catch { /* memory only */ } };
  const current = () => ({host: host.value, session_id: session.value.trim(), relative_path: path.value});
  const scopes = () => caps()?.scopes || [];
  const supported = () => caps()?.artifacts?.capture?.manual_single_file === true;
  const mayPreview = () => supported() && scopes().includes("observe");
  const mayCapture = () => mayPreview() && scopes().includes("manage") &&
    caps()?.actions?.some(item => item.action === "artifact.capture" && item.allowed === true);
  const expired = () => !saved.preview || saved.preview.expires_at * 1000 <= Date.now();
  const settled = () => ["succeeded", "failed", "cancelled"].includes(saved.operation?.status);
  const active = () => Boolean(saved.intent && (!settled() || (saved.operation.status === "succeeded" && !saved.result)) && !saved.refused);
  const currentView = () => {try {guard(); return !disposed;} catch {return false;}};
  const validInput = input => input.host && input.session_id.length >= 6 && input.session_id.length <= 256 &&
    !/[\x00-\x1f\x7f-\x9f/\\]/.test(input.session_id) && input.relative_path &&
    new TextEncoder().encode(input.relative_path).length <= 4096 && !/[\\\x00-\x1f\x7f-\x9f]/.test(input.relative_path) &&
    input.relative_path.split("/").every(part => part && part !== "." && part !== ".." && part.toLowerCase() !== ".git");
  const showError = error => {if (currentView()) notice.replaceChildren(errorBox(error));};
  const preview = h("button", {class: "secondary", onclick: async () => {
    const input = current(); guard();
    if (busy || saved.intent || !mayPreview() || !validInput(input)) return;
    const ticket = ++revision; busy = true; saved.preview = null; reviewed.checked = false; persist(); render();
    try {
      // This is a helpful read-model gate; central preview repeats current source/policy checks.
      const row = (await api("GET", `/sessions/${encodeURIComponent(input.host)}/${encodeURIComponent(input.session_id)}`)).session;
      guard(); if (ticket !== revision) return;
      if (row?.provenance !== "manual" || row.host !== input.host || row.session_id !== input.session_id)
        throw new Error(t("capture_manual_only"));
      const response = await api("POST", "/artifact-capture-previews", input); guard();
      if (ticket !== revision) return;
      const doc = response.preview;
      if (!validPreview(doc) || doc.source.host !== input.host || doc.source.session_id !== input.session_id ||
          doc.relative_path !== input.relative_path)
        throw new Error(t("capture_invalid_preview"));
      saved.preview = doc; persist(); notice.replaceChildren();
    } catch (error) {if (ticket === revision) showError(error);}
    finally {busy = false; render();}
  }}, t("capture_preview"));
  const accept = async operation => {
    guard();
    const intent = saved.intent;
    if (!intent || !operationId(operation?.operation_id) || operation.action !== "artifact.capture" ||
        (intent.operation_id && intent.operation_id !== operation.operation_id) ||
        (intent.request && operation.target?.preview_id !== intent.request.target.preview_id))
      throw new Error(t("capture_invalid_result"));
    saved.operation = operation; saved.intent.operation_id = operation.operation_id; persist();
    if (operation.status === "succeeded") {
      const ref = operation.result;
      if (!/^art_[0-9a-f]{32}$/.test(ref?.artifact_id) || !Number.isSafeInteger(ref.revision) || ref.revision < 1 ||
          !digest(ref.digest) || ref.digest !== intent.reviewed_digest) throw new Error(t("capture_invalid_result"));
      const {artifact} = await api("GET", `/artifacts/${ref.artifact_id}/revisions/${ref.revision}`); guard();
      if (saved.intent !== intent) return;
      if (artifact.artifact_id !== ref.artifact_id || artifact.revision !== ref.revision ||
          artifact.state !== "ready" || artifact.digest !== ref.digest || artifact.source?.kind !== "manual_capture" ||
          artifact.source.operation_id !== operation.operation_id) throw new Error(t("capture_invalid_result"));
      saved.result = {artifact_id: ref.artifact_id, revision: ref.revision, digest: ref.digest}; persist();
    }
    render();
  };
  const apply = h("button", {class: "secondary", onclick: async () => {
    guard();
    if (busy || !mayCapture() || (settled() && (saved.operation.status !== "succeeded" || saved.result)) || saved.refused) return;
    if (!saved.intent) {
      if (expired() || !reviewed.checked) return;
      const doc = saved.preview;
      saved.intent = {key: crypto.randomUUID(), reviewed_digest: doc.evidence.digest,
        request: {action: "artifact.capture", target: {preview_id: doc.preview_id},
        params: {preview_token: doc.preview_token}, preconditions: {expected_fingerprint: doc.fingerprint}}};
      persist();
    }
    busy = true; update();
    try {
      const intent = saved.intent;
      const result = intent.operation_id ? await api("GET", `/operations/${intent.operation_id}`)
        : await api("POST", "/operations?wait=3", intent.request, intent.key);
      guard(); await accept(result.operation); notice.replaceChildren();
    } catch (error) {
      if (!currentView()) return;
      // These admission failures prove that this key did not accept an operation.
      if (!saved.intent.operation_id && ["PREVIEW_EXPIRED", "PREVIEW_TOKEN_INVALID", "PREVIEW_MISMATCH", "INVALID_PARAMS"].includes(error.code))
        saved.refused = true;
      persist(); showError(error);
    } finally {busy = false; render();}
  }}, t("capture_save"));
  const reset = h("button", {class: "secondary", onclick: () => {
    guard(); if (busy || refreshing || active()) return;
    saved = {input: current()}; reviewed.checked = false; revision++; persist(); notice.replaceChildren(); render();
  }}, t("capture_new"));
  const attach = onAttach ? h("button", {class: "secondary", onclick: () => {
    guard(); if (saved.result) {onAttach({...saved.result}, (saved.preview?.relative_path || saved.input.relative_path).split("/").at(-1)); notice.textContent = t("capture_attached");}
  }}, t("capture_attach")) : null;
  const box = h("details", {class: "capture", "data-capture": "", hidden: !supported()}, h("summary", {}, t("capture_title")),
    h("p", {class: "muted"}, t("capture_help")),
    h("div", {class: "capture-fields"},
      h("label", {}, t("capture_host"), host), h("label", {}, t("capture_session"), session), h("label", {}, t("capture_path"), path)),
    h("div", {class: "actions"}, preview), evidence, review, h("div", {class: "actions"}, apply, attach, reset), notice);
  function update() {
    const frozen = busy || Boolean(saved.intent);
    for (const field of [host, session, path]) field.disabled = frozen;
    preview.disabled = busy || Boolean(saved.intent) || !mayPreview() || !validInput(current());
    review.hidden = !saved.preview || Boolean(saved.intent); reviewed.disabled = expired();
    apply.hidden = settled() && (saved.operation.status !== "succeeded" || saved.result); apply.textContent = saved.intent ? t("capture_check") : t("capture_save");
    apply.disabled = busy || !mayCapture() || Boolean(saved.refused) || (!saved.intent && (expired() || !reviewed.checked));
    reset.hidden = !saved.intent; reset.disabled = busy || Boolean(refreshing) || active();
    if (attach) {attach.hidden = !saved.result; attach.disabled = busy;}
    const expiry = evidence.querySelector("[data-capture-expiry]");
    if (expiry) expiry.textContent = saved.intent ? t("capture_fixed") : expired() ? t("capture_expired") : t("capture_single_file");
  }
  function render() {
    if (disposed) return;
    evidence.replaceChildren();
    if (!mayPreview()) evidence.append(h("p", {class: "muted"}, t("capture_scope_observe")));
    else if (!mayCapture()) evidence.append(h("p", {class: "muted"}, t("capture_scope_manage")));
    const doc = saved.preview;
    if (doc) {
      const facts = [[t("capture_name"), doc.relative_path.split("/").at(-1)], [t("capture_bytes"), String(doc.evidence.size_bytes)],
        ["SHA-256", doc.evidence.digest], [t("capture_source"), `${doc.source.host} / ${doc.source.session_id}`],
        [t("capture_path"), doc.relative_path], [t("capture_root"), doc.source.root], [t("capture_repository"), doc.source.repository_root],
        ["HEAD", doc.evidence.head_sha], [t("capture_expiry"), timeStamp(doc.expires_at)]];
      evidence.append(h("dl", {class: "kv"}, ...facts.flatMap(([label, value]) => [h("dt", {}, label), h("dd", {}, h("code", {}, value))])),
        h("p", {class: "muted", "data-capture-expiry": ""}));
    }
    if (saved.intent) evidence.append(h("p", {}, saved.operation ? `${t("op_" + saved.operation.status)} ` : t("capture_unknown"),
      saved.intent.operation_id ? h("a", {href: `#/op/${saved.intent.operation_id}`}, saved.intent.operation_id) : null,
      saved.operation?.status_reason ? ` · ${saved.operation.status_reason}` : ""));
    if (saved.result) evidence.append(h("p", {class: "pre"}, t("capture_saved"), " ", `${saved.result.artifact_id} · r${saved.result.revision} · ${saved.result.digest}`));
    update();
  }
  for (const field of [host, session, path]) field.addEventListener("input", () => {
    guard(); if (saved.intent) return;
    revision++; saved.input = current(); saved.preview = null; reviewed.checked = false; persist(); render();
  });
  const refresh = async (fresh = false) => {
    if (!saved.intent?.operation_id) return;
    if (refreshing) {
      await refreshing;
      if (!fresh) return;
      guard();
      return refresh(true);
    }
    const id = saved.intent.operation_id;
    refreshing = (async () => {
      while (busy) {await new Promise(resolve => setTimeout(resolve, 25)); guard();}
      const {operation} = await api("GET", `/operations/${id}`); guard();
      if (saved.intent?.operation_id === id) await accept(operation);
    })();
    try {await refreshing;} finally {refreshing = null;}
  };
  const off = onEvents(event => {
    if (!box.isConnected) {off(); return;}
    if (!currentView()) {off(); return;}
    if (event.resource_id === saved.intent?.operation_id || event.resource_id === saved.result?.artifact_id) return refresh(true);
  });
  const timer = setInterval(() => {
    if (!box.isConnected) {disposed = true; clearInterval(timer); off(); return;}
    try {guard();} catch {disposed = true; clearInterval(timer); off(); return;}
    update();
    if (saved.intent?.operation_id && (active() || (saved.operation?.status === "succeeded" && !saved.result))) refresh().catch(showError);
  }, 1000);
  queueMicrotask(() => {if (box.isConnected && saved.intent?.operation_id) refresh().catch(showError);});
  render();
  return box;
}
