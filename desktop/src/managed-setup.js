import {iconButton} from './ui/components.js';
// Guided configuration of the owned central. Secrets stay in input memory until staged.
// Durable operations retain their exact request/key; reopening never starts a new intent.
export function managedSetupPanel({h, t, api, guard, namespace, errorBox, opStatus, onConfigured, caps}) {
  const key = `batc.managed.setup.${namespace}`;
  let saved = {}, snapshot = null, busy = false, disposed = false, operation = null;
  try { saved = JSON.parse(localStorage.getItem(key) || "{}"); } catch { /* fresh draft */ }
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) saved = {};
  const equal = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])]));
    return value;
  }
  const object = value => value && typeof value === "object" && !Array.isArray(value);
  const validCommands = commands => object(commands) && Object.keys(commands).length <= 200 &&
    Object.entries(commands).every(([project, argv]) => project.length > 0 && project.length <= 256 &&
      !/[\x00-\x1f]/.test(project) && Array.isArray(argv) && argv.length > 0 && argv.length <= 64 &&
      argv.every(arg => typeof arg === "string" && arg.length > 0 && arg.length <= 4096 && !/[\x00-\x1f]/.test(arg)) &&
      !argv[0].startsWith("-") && argv.reduce((size, arg) => size + arg.length, 0) <= 16000);
  const validVerification = value => object(value) && validCommands(value.commands) && Number.isInteger(value.timeout_s) && value.timeout_s >= 1 && value.timeout_s <= 3600;
  const validIntent = intent => intent && typeof intent.key === "string" && intent.key.length > 0 && intent.key.length <= 200 &&
    ["setup.host", "setup.repository", "setup.verification"].includes(intent.request?.action) &&
    /^[0-9a-f]{64}$/.test(intent.request?.preconditions?.config_revision) && object(intent.request?.params) &&
    object(intent.request?.target) && (intent.request.action === "setup.verification"
      ? Object.keys(intent.request.target).length === 0 && validVerification(intent.request.params)
      : Object.keys(intent.request.target).length === 1 && typeof intent.request.target[intent.request.action === "setup.host" ? "host" : "repository"] === "string") &&
    (!intent.operation_id || /^op_[0-9a-f]{32}$/.test(intent.operation_id));
  const allowed = kind => caps()?.scopes?.includes("manage") && caps()?.actions?.some(action => action.action === `setup.${kind}` && action.allowed === true);
  const damaged = !!saved.intent && !validIntent(saved.intent);
  const alive = () => {try {guard(); return !disposed;} catch {return false;}};
  const persist = () => {guard(); localStorage.setItem(key, JSON.stringify(saved));};
  const terminal = () => ["succeeded", "failed", "cancelled"].includes(operation?.status);
  const message = h("div", {role: "status"}), summary = h("div"), receipt = h("div");
  const unsaved = h("span", {class: "chip", hidden: true}, t("setup_unsaved"));
  const fields = new Map();
  const input = (name, label, options = {}) => {
    const secret = options.type === "password";
    const {multiline, ...attributes} = options;
    const node = h(multiline ? "textarea" : "input", {autocomplete: "off", maxlength: secret ? 4096 : 1024, ...attributes});
    if (!secret) node.value = typeof saved[name] === "string" ? saved[name] : "";
    node.addEventListener("input", () => {
      if (!alive() || saved.intent || busy) return;
      if (name.startsWith("verification_")) seedVerificationBase();
      if (!secret) {
        saved[name] = node.value;
        if (["url", "fingerprint", "profile_id"].includes(name)) {saved.import_profile_id = ""; profile.value = "";}
        try {persist();} catch (error) {message.replaceChildren(errorBox(error));}
      }
      unsaved.hidden = false;
    });
    fields.set(name, node);
    return h("label", {}, t(label), node);
  };
  const checkbox = (name, label) => {
    const node = h("input", {type: "checkbox"}); node.checked = saved[name] === true;
    node.addEventListener("change", () => {
      if (!alive() || saved.intent || busy) return;
      saved[name] = node.checked;
      try {persist(); unsaved.hidden = false;} catch (error) {showError(error);}
    });
    fields.set(name, node);
    return h("label", {}, node, " ", t(label));
  };
  const profile = h("select", {"aria-label": t("setup_profile")});
  profile.addEventListener("change", () => {
    if (!alive() || saved.intent) return;
    const selected = snapshot?.profiles?.find(row => row.id === profile.value);
    saved.import_profile_id = selected?.id || "";
    if (selected) for (const name of ["url", "fingerprint", "profile_id"]) {
      fields.get(name).value = selected[name] || ""; saved[name] = fields.get(name).value;
    }
    try {persist(); unsaved.hidden = false;} catch (error) {showError(error);}
  });
  const hostForm = h("form", {"data-setup-host": "", onsubmit: event => {event.preventDefault(); run("host");}},
    h("h3", {}, t("setup_host_title")), h("p", {class: "muted"}, t("setup_host_help")),
    h("label", {}, t("setup_profile"), profile),
    h("div", {class: "capture-fields"}, input("host", "setup_host_name", {required: true, maxlength: 64}),
      input("url", "setup_host_url", {type: "url", required: true, placeholder: "wss://host:9876/"}),
      input("fingerprint", "setup_fingerprint", {required: true}),
      input("profile_id", "setup_workspace_profile", {placeholder: "default"}),
      input("bat_secret", "setup_bat_token", {type: "password"})),
    h("p", {class: "muted"}, t("setup_trust_help")),
    h("details", {}, h("summary", {}, t("setup_managed_work")),
      checkbox("writes", "setup_allow_messages"), checkbox("orchestrate", "setup_allow_start"),
      checkbox("shared_clone_worktrees", "setup_shared_clone"), h("p", {class: "muted"}, t("setup_shared_clone_help")),
      h("div", {class: "capture-fields"}, input("managed_roots", "setup_managed_roots"), input("ssh_alias", "setup_ssh_alias")),
      h("p", {class: "muted"}, t("setup_roots_help"))),
    h("button", {type: "submit", class: "primary"}, t("setup_save_host")));
  const repoForm = h("form", {"data-setup-repository": "", onsubmit: event => {event.preventDefault(); run("repository");}},
    h("h3", {}, t("setup_repository_title")), h("p", {class: "muted"}, t("setup_repository_help")),
    h("div", {class: "capture-fields"}, input("repository", "setup_repository", {required: true, placeholder: "owner/repository"}),
      input("repository_host", "setup_host_name", {required: true}), input("workspace_id", "setup_workspace_id", {required: true}),
      input("remote_url", "setup_remote_url", {required: true, placeholder: "git@github.com:owner/repository.git"}),
      input("github_secret", "setup_github_token", {type: "password"})),
    h("div", {class: "actions"}, checkbox("allow_integrate", "setup_allow_integrate"),
      checkbox("allow_merge", "setup_allow_merge"), checkbox("allow_pr_update", "setup_allow_pr_update")),
    h("button", {type: "button", class: "secondary", onclick: () => workspaces().catch(showError)}, t("setup_find_workspaces")),
    h("div", {"data-setup-workspaces": ""}),
    h("button", {type: "submit", class: "primary"}, t("setup_save_repository")));
  const verificationCommands = h("div", {"data-setup-verification-commands": ""});
  const verificationNotice = h("p", {class: "note warn", hidden: true});
  const reloadVerification = h("button", {class: "secondary", type: "button", onclick: () => {
    if (!alive() || busy || saved.intent || !snapshot) return;
    saved.verification_base = null;
    fillVerification(fields.get("verification_project").value.trim());
  }}, t("setup_verification_reload"));
  const verificationForm = h("form", {"data-setup-verification": "", onsubmit: event => {event.preventDefault(); run("verification");}},
    h("h3", {}, t("setup_verification_title")), h("p", {class: "muted"}, t("setup_verification_help")),
    verificationNotice, verificationCommands,
    h("div", {class: "capture-fields"}, input("verification_project", "setup_verification_project", {required: true, maxlength: 256}),
      input("verification_executable", "setup_verification_executable", {required: true, maxlength: 4096}),
      input("verification_timeout", "setup_verification_timeout", {type: "number", min: 1, max: 3600, step: 1, required: true})),
    input("verification_arguments", "setup_verification_arguments", {multiline: true, rows: 4, maxlength: 16000}),
    h("p", {class: "muted"}, t("setup_verification_arguments_help")),
    h("div", {class: "actions"}, h("button", {type: "submit", class: "primary"}, t("setup_save_verification")), reloadVerification));
  function seedVerificationBase() {
    if (!saved.verification_base && snapshot && validVerification(snapshot.verification))
      saved.verification_base = {revision: snapshot.revision, ...structuredClone(snapshot.verification)};
  }
  function fillVerification(project) {
    if (!snapshot || saved.intent || busy) return;
    const settings = snapshot.verification, argv = settings.commands[project] || [];
    for (const [name, value] of Object.entries({verification_project: project, verification_executable: argv[0] || "",
      verification_arguments: argv.slice(1).join("\n"), verification_timeout: String(settings.timeout_s)})) {
      fields.get(name).value = value; saved[name] = value;
    }
    saved.verification_base = null; seedVerificationBase();
    try {persist(); unsaved.hidden = false; update();} catch (error) {showError(error);}
  }
  const refreshButton = iconButton({icon:'refresh-cw', label:t('setup_refresh'), ...{class: "secondary", onclick: () => refresh().catch(showError)}});
  const retry = h("button", {class: "secondary", hidden: true, onclick: () => recover().catch(showError)}, t("setup_recover"));
  const next = h("button", {class: "secondary", hidden: true, onclick: () => {
    if (!alive() || damaged || !terminal() && !saved.intent?.refused) return;
    const previous = saved;
    saved = {...saved, intent: null};
    if (operation?.status === "succeeded" && previous.intent.request.action === "setup.verification") saved.verification_base = null;
    try {persist(); operation = null; update(); refresh().catch(showError);}
    catch (error) {saved = previous; showError(error);}
  }}, t("setup_next_change"));
  const box = h("section", {class: "panel managed-setup", "data-managed-setup": ""},
    h("h2", {}, t("setup_title"), " ", unsaved), h("p", {class: "muted"}, t("setup_resume_help")), summary,
    h("div", {class: "actions"}, refreshButton, retry, next), message, receipt,
    h("details", {open: true}, h("summary", {}, t("setup_host_title")), hostForm),
    h("details", {}, h("summary", {}, t("setup_repository_title")), repoForm),
    h("details", {}, h("summary", {}, t("setup_verification_title")), verificationForm),
    h("a", {href: "#/projects"}, t("setup_open_projects")));
  function showError(error) {if (alive()) {message.replaceChildren(errorBox(error)); update();}}
  function update() {
    if (!alive()) return;
    const locked = busy || !snapshot || !!saved.intent || snapshot.busy;
    for (const [kind, form] of [["host", hostForm], ["repository", repoForm], ["verification", verificationForm]]) {
      for (const field of form.querySelectorAll("input,textarea,select,button")) field.disabled = !!locked || !allowed(kind);
      let scope = form.querySelector("[data-setup-scope]");
      if (!scope) {scope = h("p", {class: "note", "data-setup-scope": ""}, t("setup_scope_required")); form.prepend(scope);}
      scope.hidden = !!allowed(kind);
    }
    const base = saved.verification_base;
    const stale = base && base.revision !== snapshot?.revision;
    verificationNotice.hidden = !stale;
    verificationNotice.textContent = t("setup_verification_changed");
    refreshButton.disabled = busy; retry.hidden = !saved.intent || terminal(); retry.disabled = busy;
    next.hidden = !saved.intent || !terminal() && !saved.intent.refused; next.disabled = busy || damaged;
    if (damaged) retry.disabled = true;
    receipt.replaceChildren();
    if (operation) receipt.append(opStatus(operation), " ", h("a", {href: `#/op/${operation.operation_id}`}, t("setup_operation")));
    else if (saved.intent) receipt.append(h("p", {class: "note"}, t("setup_uncertain")));
  }
  function accept(candidate) {
    const intent = saved.intent;
    if (!candidate || !/^op_[0-9a-f]{32}$/.test(candidate.operation_id) || candidate.actor !== caps()?.actor || candidate.action !== intent.request.action ||
        !equal(candidate.target, intent.request.target) || !equal(candidate.params, intent.request.params) ||
        !equal(candidate.preconditions, intent.request.preconditions) ||
        candidate.idempotency_key !== intent.key ||
        intent.operation_id && candidate.operation_id !== intent.operation_id) throw Error(t("setup_receipt_mismatch"));
    operation = candidate; intent.operation_id = candidate.operation_id; persist();
    if (candidate.status === "succeeded") {unsaved.hidden = true; Promise.resolve(onConfigured?.()).catch(showError);}
    update();
  }
  async function sendOriginal() {
    const intent = saved.intent;
    const result = intent.operation_id ? await api("GET", `/operations/${encodeURIComponent(intent.operation_id)}`)
      : await api("POST", "/operations?wait=3", intent.request, intent.key);
    guard(); accept(result.operation);
  }
  async function recover() {
    if (!alive() || busy || !saved.intent || damaged || saved.intent.refused) return;
    busy = true; update();
    try {await sendOriginal(); await refresh();}
    finally {busy = false; if (alive()) update();}
  }
  async function run(kind) {
    const form = kind === "host" ? hostForm : kind === "repository" ? repoForm : verificationForm;
    if (!alive() || busy || !snapshot || snapshot.busy || saved.intent || !allowed(kind) || !form.reportValidity()) return;
    busy = true; update(); message.replaceChildren();
    try {
      if (kind === "verification" && saved.verification_base &&
          (!validVerification(saved.verification_base) || !/^[0-9a-f]{64}$/.test(saved.verification_base.revision)))
        throw Error(t("setup_verification_invalid"));
      persist(); // refuse to submit an intent that cannot survive reload
      const value = name => fields.get(name).value.trim();
      const checked = name => fields.get(name).checked;
      const tokenField = kind === "verification" ? null : fields.get(kind === "host" ? "bat_secret" : "github_secret");
      let secretRef;
      if (tokenField?.value) {
        const staged = await api("POST", "/managed/setup/secrets", {kind: kind === "host" ? "bat" : "github", value: tokenField.value});
        guard(); secretRef = staged.secret_ref; tokenField.value = "";
      }
      const imported = kind === "host" && !secretRef && saved.import_profile_id;
      const params = kind === "host" ? {...(imported ? {import_profile_id: imported} :
        {url: value("url"), fingerprint: value("fingerprint"), profile_id: value("profile_id") || "default"}),
        writes: checked("writes"), orchestrate: checked("orchestrate"), shared_clone_worktrees: checked("shared_clone_worktrees"),
        managed_roots: value("managed_roots").split(/\r?\n|;/).map(item => item.trim()).filter(Boolean),
        ...(value("ssh_alias") ? {ssh_alias: value("ssh_alias")} : {})}
        : kind === "repository" ? {host: value("repository_host"), workspace_id: value("workspace_id"), remote_url: value("remote_url"),
          allow_integrate: checked("allow_integrate"), allow_merge: checked("allow_merge"), allow_pr_update: checked("allow_pr_update")}
        : {commands: {...(saved.verification_base || snapshot.verification).commands,
            [value("verification_project")]: [value("verification_executable"), ...fields.get("verification_arguments").value.split(/\r?\n/).filter(arg => arg !== "")]},
          timeout_s: Number(value("verification_timeout"))};
      if (kind === "verification" && !validVerification(params)) throw Error(t("setup_verification_invalid"));
      if (secretRef) params.secret_ref = secretRef;
      saved.intent = {key: crypto.randomUUID(), request: {action: `setup.${kind}`,
        target: kind === "host" ? {host: value("host")} : kind === "repository" ? {repository: value("repository")} : {}, params,
        preconditions: {config_revision: kind === "verification" ? saved.verification_base?.revision || snapshot.revision : snapshot.revision}}};
      persist(); await sendOriginal(); await refresh();
    } catch (error) {
      if (alive() && saved.intent && !saved.intent.operation_id &&
          (error.admissionRefused || ["CONFIGURATION_CHANGED", "SETUP_BUSY", "PRECONDITION_REQUIRED"].includes(error.code))) {
        saved.intent.refused = error.code; persist();
      }
      showError(error);
    }
    finally {busy = false; if (alive()) update();}
  }
  async function workspaces() {
    const host = fields.get("repository_host").value.trim();
    if (!host || busy || !alive()) return;
    const result = await api("GET", `/workspaces?host=${encodeURIComponent(host)}`); guard();
    const rows = result.workspaces || [];
    box.querySelector("[data-setup-workspaces]").replaceChildren(...rows.map(row =>
      h("button", {class: "secondary", type: "button", onclick: () => {
        if (!alive() || saved.intent) return;
        fields.get("workspace_id").value = row.workspace_id || row.id;
        saved.workspace_id = fields.get("workspace_id").value; persist(); unsaved.hidden = false;
      }}, `${row.name || row.title || row.workspace_id || row.id} · ${row.workspace_id || row.id}`)));
  }
  async function refresh() {
    const result = await api("GET", "/managed/setup"); guard();
    if (disposed) return;
    if (!validVerification(result.verification)) {
      if (allowed("verification")) throw Error(t("setup_verification_invalid"));
      result.verification = {commands: {}, timeout_s: 600};
    }
    snapshot = result;
    if (!saved.verification_timeout) fields.get("verification_timeout").value = String(result.verification.timeout_s);
    verificationCommands.replaceChildren(...Object.entries(result.verification.commands).map(([project, argv]) =>
      h("details", {}, h("summary", {}, project), h("pre", {class: "pre"}, argv.join("\n")),
        h("button", {type: "button", class: "mini", onclick: () => fillVerification(project)}, t("setup_verification_edit", {project})))));
    summary.replaceChildren(h("p", {}, t("setup_counts", {hosts: result.hosts?.length || 0, repositories: result.repositories?.length || 0})),
      ...(result.hosts || []).map(row => h("p", {}, h("strong", {}, row.name), " · ",
        t(row.connected ? "setup_connected" : "setup_unverified"), " · ", row.url)),
      ...[result.busy ? h("p", {class: "note warn"}, t("setup_busy")) : null].filter(Boolean),
      ...[result.profiles_error ? h("p", {class: "muted"}, t("setup_profiles_unavailable")) : null].filter(Boolean));
    profile.replaceChildren(h("option", {value: ""}, t("setup_manual_profile")),
      ...(result.profiles || []).map(row => h("option", {value: row.id}, row.name || row.id)));
    profile.value = saved.import_profile_id || "";
    update();
  }
  return {box, async load() {try {await refresh(); if (saved.intent?.operation_id) await recover();} catch (error) {showError(error);}},
    dispose() {disposed = true; for (const name of ["bat_secret", "github_secret"]) fields.get(name).value = "";}};
}
