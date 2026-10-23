import {operationStatusLabel} from './global-labels.js';
import {timeLabel, timeStamp} from './ui/stamps.js';
import {displayFact, healthLabel} from './health-display.js';
import {healthReadError} from './health-fact.js';
import {claimObservationRenewal} from './observation-renewal.js';
import {patch} from './ui/patch.js';
// BAT v3.2.15 renderer/src/utils/connection-url.ts grammar. Trust comes from
// BAT's own copied fingerprint, never a network probe or a loopback assumption.
export function normalizeSetupFingerprint(value) {
  const normalized = value.trim().toUpperCase().replace(/^SHA256:/, '').replace(/[: ]/g, '');
  return /^[0-9A-F]{64}$/.test(normalized) ? normalized : null;
}
const explicitPort = value => value.replace(/^wss?:\/\//i, '').split(/[/?#]/, 1)[0].match(/:(\d+)$/)?.[1];
export function parseBatConnectionUrl(value) {
  try {
    const raw = value.trim();
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) && !/^wss?:\/\//i.test(raw)) return null;
    const url = new URL(/^wss?:\/\//i.test(raw) ? raw : `wss://${raw}`);
    const token = url.searchParams.get('token'), fingerprint = normalizeSetupFingerprint(url.searchParams.get('fp') || '');
    // URL.port drops protocol-default ports (443/80); keep an explicit BAT port.
    const port = Number(explicitPort(raw) || 9876);
    if (!['wss:', 'ws:'].includes(url.protocol) || !url.hostname || url.username || url.password ||
        !token || !fingerprint || !Number.isInteger(port) || port < 1 || port > 65535) return null;
    return {endpoint: `wss://${url.hostname}:${port}`, fingerprint, token,
      loopback: ['127.0.0.1', '[::1]', 'localhost'].includes(url.hostname.toLowerCase())};
  } catch {return null;}
}
export function validSetupEndpoint(value) {
  try {
    const url = new URL(value);
    return value.length <= 512 && !/\s/.test(value) && /^wss:\/\//i.test(value) &&
      url.protocol === 'wss:' && !!url.hostname && Number(explicitPort(value)) > 0 && !url.username && !url.password &&
      !value.includes('?') && !value.includes('#') && /^wss:\/\/[^/]+\/?$/i.test(value);
  } catch {return false;}
}
export const validSetupHostId = value => /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(value);
const connectionFacets = ['transport', 'service', 'trust_sign_in', 'connection'];
const connectionReasons = new Set(['never_observed', 'host_unreachable', 'host_not_refreshed', 'DISCOVERY_SCOPE_CHANGED',
  'transport', 'tls_trust', 'auth_or_protocol', 'credential_missing', 'service_refused', 'service_timeout', 'unknown', 'authenticated_socket']);
// Adapt the versioned producer facts; all wording and expiry use shared health.
// Legacy booleans and the response's arrival time cannot establish currentness.
export function setupConnectionFacts(host, {now = Date.now(), error = null} = {}) {
  const envelope = host?.connection_observation;
  return Object.fromEntries(connectionFacets.map(name => {
    const raw = envelope?.version === 1 ? envelope.facets?.[name] : null;
    const bound = raw?.source && typeof host.name === 'string' && raw.source.host === host.name && raw.source.profile_id === host.profile_id &&
      /^[0-9a-f]{64}$/.test(raw.source.binding_version) && (raw.source.kind === 'hosts_observed' || name === 'transport' && raw.source.kind === 'fleet_client') &&
      raw.source.ref === `${raw.source.kind}:${host.name}` && (raw.reason === null || connectionReasons.has(raw.reason));
    const value = bound && ['ready', 'unavailable', 'missing', 'unknown'].includes(raw.value) ? raw.value : 'unknown';
    const reason = bound && connectionReasons.has(raw.reason) ? raw.reason : null;
    const deadline = bound && typeof raw.valid_until === 'string' ? Date.parse(raw.valid_until) : NaN;
    const observed = bound && typeof raw.observed_at === 'number' && Number.isFinite(raw.observed_at) ? raw.observed_at * 1000 : null;
    return [name, displayFact({value, key: `wpf_${value}`, reason, source: bound ? raw.source : null,
      observed_at: observed, valid_until: Number.isFinite(deadline) ? deadline : null,
      stale: ['host_unreachable', 'host_not_refreshed'].includes(reason), stale_reason: reason,
      // No implicit 60-second validity for undeclared observations.
      ...(Number.isFinite(deadline) ? {} : {observed_at: null}), error: healthReadError(reason, error)}, {now})];
  }));
}
// A successful receipt records past verification, not current reachability.
export function setupStatusLine({busy = false, operation = null, hosts = [], readReady = false, now = Date.now()}) {
  const ready = host => {const fact = setupConnectionFacts(host, {now}).connection; return fact.value === 'ready' && fact.freshness === 'current';};
  if (busy) return {key: 'setup_checking'};
  if (operation?.status === 'succeeded' && operation.action === 'setup.host') {
    const host = hosts.find(row => row.name === operation.target?.host);
    if (readReady && host && ready(host)) return {key: 'setup_ready'};
    // updated_at is the succeeded receipt's completion time, not the inventory time.
    return {key: 'setup_verified_saved', verifiedAt: operation.updated_at};
  }
  if (readReady && hosts.length && hosts.every(ready)) return {key: 'setup_ready'};
  if (operation) return {key: ['succeeded', 'failed', 'cancelled'].includes(operation.status) ? 'setup_failed_operation' : 'setup_saving'};
  return {key: !readReady ? 'setup_loading' : hosts.length ? '' : 'setup_waiting'};
}
// Keep native HTML constraints, but use the chosen UI language for their messages.
export function setupFormValidity(form, t) {
  const invalid = Array.from(form.elements).find(node => node.willValidate && !node.validity.valid);
  if (!invalid) return form.reportValidity();
  if (!invalid.validity.customError) {
    invalid.setCustomValidity(t(invalid.validity.valueMissing ? 'setup_required' : 'error_code_INVALID_PARAMS'));
    invalid.setAttribute('aria-invalid', 'true');
    invalid.addEventListener('input', () => {
      invalid.setCustomValidity(''); invalid.setAttribute('aria-invalid', 'false');
    }, {once: true});
  }
  for (let parent = invalid.parentElement; parent; parent = parent.parentElement)
    if (parent.tagName === 'DETAILS') parent.open = true;
  invalid.reportValidity();
  return false;
}
// Guided configuration of the owned central. Secrets stay in input memory until staged.
// Durable operations retain their exact request/key; reopening never starts a new intent.
export function managedSetupPanel({h, t, api, guard, namespace, errorBox, refreshButton, onConfigured, caps}) {
  const key = `batc.managed.setup.${namespace}`;
  let saved = {}, snapshot = null, busy = false, disposed = false, operation = null, readSerial = 0, readReady = false, hostDisclosureReady = false;
  const stagedSecrets = new Map(); // Principal/kind-bound references expire centrally after one hour.
  let review = null, reviewing = false, reviewSerial = 0, reviewChanged = false;
  let discovery = null, discovering = false, discoverySerial = 0, secretTimer;
  const renewedObservations = new Map();
  let observationRead = false;
  try { saved = JSON.parse(localStorage.getItem(key) || "{}"); } catch { /* fresh draft */ }
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) saved = {};
  // Old clients stored these codes as refusals without retaining admission proof.
  // They must recover the original key before any new request is permitted.
  if (saved.intent?.refused) {saved.intent.problemCode = saved.intent.refused; delete saved.intent.refused;}
  if (typeof saved.url === 'string' && /[?@#]/.test(saved.url)) delete saved.url;
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
  // Paste feedback is page-only and contains no URL query or credential.
  const pasteRead = h('span', {id: 'setup-connection_url-read', class: 'muted', hidden: true,
    role: 'status', 'data-setup-paste-read': ''});
  function clearPasteRead() {pasteRead.textContent = ''; pasteRead.hidden = true;}
  const fields = new Map(), errors = new Map(), hints = new Map(), composing = new Set();
  function fieldHint(name, text) {
    const help = hints.get(name);
    help.textContent = text; help.hidden = !text;
  }
  function fieldError(name, key) {
    const node = fields.get(name), error = errors.get(name);
    if (!node || !error) return;
    const text = key ? t(key) : '';
    node.setCustomValidity(text); node.setAttribute('aria-invalid', text ? 'true' : 'false');
    error.textContent = text; error.hidden = !text;
  }
  function focusField(name) {
    const node = fields.get(name);
    for (let parent = node?.parentElement; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
    node?.focus();
  }
  const input = (name, label, options = {}) => {
    const secret = options.type === "password";
    const {multiline, hint, ...attributes} = options;
    const id = `setup-${name}`;
    const node = h(multiline ? "textarea" : "input", {id, 'aria-label': t(label), 'data-setup-field': name, autocomplete: "off", maxlength: secret ? 4096 : 1024, ...attributes});
    const error = h('span', {id: `${id}-error`, class: 'error', hidden: true, 'data-setup-error': name});
    const help = h('span', {id: `${id}-hint`, class: 'muted', hidden: !hint}, hint ? t(hint) : '');
    node.setAttribute('aria-describedby', `${id}-hint ${id}-error${name === 'connection_url' ? ' setup-connection_url-read' : ''}`);
    errors.set(name, error); hints.set(name, help);
    if (!secret) node.value = typeof saved[name] === "string" ? saved[name] : "";
    node.addEventListener("input", () => {
      if (!alive() || saved.intent || busy) return;
      if (!name.startsWith('verification_') && !['repository', 'repository_host', 'workspace_id', 'remote_url', 'github_secret'].includes(name)) invalidateReview();
      if (['url', 'fingerprint', 'connection_url', 'bat_secret'].includes(name)) invalidateDiscovery();
      if (name === 'bat_secret') stagedSecrets.delete('host');
      if (['url', 'fingerprint', 'connection_url'].includes(name)) clearPasteRead();
      if (name.startsWith("verification_")) seedVerificationBase();
      if (!secret) {
        // An accidentally pasted credential URL must never enter a draft.
        saved[name] = name === 'url' && /[?@#]/.test(node.value) ? '' : node.value;
        if (["url", "fingerprint", "profile_id"].includes(name)) {saved.import_profile_id = ""; profile.value = "";}
        try {persist();} catch (error) {message.replaceChildren(errorBox(error, {sourceRefresh:false, within:message}));}
      }
      unsaved.hidden = false;
      if (!composing.has(name)) fieldError(name, null);
      if (name === 'url') suggestLocal();
      if (secret && name !== 'connection_url' && node.value) fieldHint(name, hint ? t(hint) : '');
    });
    node.addEventListener('compositionstart', () => composing.add(name));
    node.addEventListener('compositionend', () => {
      composing.delete(name);
      if (!alive() || saved.intent || busy) return;
      if (name === 'connection_url') consumeConnectionUrl(false);
      else if (['host', 'url', 'fingerprint', 'bat_secret', 'profile_id'].includes(name)) validateHost(name);
    });
    fields.set(name, node);
    return h("label", {for: id, class: 'connection-token setup-field'}, h('span', {}, t(label)), node,
      name === 'connection_url' ? pasteRead : null, help, error);
  };
  const checkbox = (name, label) => {
    const node = h("input", {type: "checkbox"}); node.checked = saved[name] === true;
    node.addEventListener("change", () => {
      if (!alive() || saved.intent || busy) return;
      invalidateReview();
      saved[name] = node.checked;
      try {persist(); unsaved.hidden = false;} catch (error) {showError(error, {sourceRefresh:false});}
    });
    fields.set(name, node);
    return h("label", {}, node, " ", t(label));
  };
  const profile = h("select", {"aria-label": t("setup_profile")});
  const profilesError = h('span', {class: 'muted', hidden: true, 'data-setup-profiles-error': ''});
  const profileField = h('label', {class: 'connection-token setup-field', hidden: true, 'data-setup-profile': ''},
    h('span', {}, t('setup_profile')), profile, profilesError);
  profile.addEventListener("change", () => {
    if (!alive() || saved.intent) return;
    invalidateDiscovery(); stagedSecrets.delete('host');
    const selected = snapshot?.profiles?.find(row => row.id === profile.value);
    if (selected) clearPasteRead();
    saved.import_profile_id = selected?.id || "";
    if (selected) for (const name of ["url", "fingerprint", "profile_id"]) {
      fields.get(name).value = selected[name] || ""; saved[name] = fields.get(name).value;
    }
    try {persist(); unsaved.hidden = false;} catch (error) {showError(error, {sourceRefresh:false});}
  });
  const manual = h('details', {'data-setup-manual': ''}, h('summary', {}, t('setup_enter_manually')),
    profileField,
    h("div", {class: "capture-fields"},
      input("url", "setup_host_url", {type: "url", required: true, placeholder: "wss://host:9876", hint: 'setup_endpoint_hint'}),
      input("fingerprint", "setup_fingerprint", {required: true, hint: 'setup_fingerprint_hint'}),
      input("profile_id", "setup_workspace_profile", {placeholder: "default", hint: 'setup_profile_hint'}),
      input("bat_secret", "setup_bat_token", {type: "password", hint: 'setup_token_hint'})),
    h("p", {class: "muted"}, t("setup_trust_help")));
  const hostForm = h("form", {novalidate: true, "data-setup-host": "", onsubmit: event => {event.preventDefault(); run("host");}},
    h('div', {class: 'capture-fields'}, input('connection_url', 'setup_connection_url', {type: 'password', hint: 'setup_connection_url_hint'})),
    h('div', {class: 'capture-fields'}, input("host", "setup_host_name", {required: true, maxlength: 64, hint: 'setup_host_hint'})), manual,
    h('button', {type: 'button', class: 'secondary', 'data-setup-discover': '', hidden: true,
      onclick: () => findSources().catch(showError)}, t('setup_discover')),
    h('p', {class: 'note', hidden: true, 'data-setup-discovery-reason': ''}),
    h("details", {}, h("summary", {}, t("setup_managed_work")),
      checkbox("writes", "setup_allow_messages"), checkbox("orchestrate", "setup_allow_start"),
      checkbox("shared_clone_worktrees", "setup_shared_clone"), h("p", {class: "muted"}, t("setup_shared_clone_help")),
      h("div", {class: "capture-fields"}, input("managed_roots", "setup_managed_roots"), input("ssh_alias", "setup_ssh_alias")),
      h("p", {class: "muted"}, t("setup_roots_help"))),
    h('p', {class: 'note', hidden: true, 'data-setup-review-reason': ''}),
    h("button", {type: "submit", class: "primary"}, t("setup_review_connection")));
  const repoForm = h("form", {novalidate: true, "data-setup-repository": "", onsubmit: event => {event.preventDefault(); run("repository");}},
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
  const verificationForm = h("form", {novalidate: true, "data-setup-verification": "", onsubmit: event => {event.preventDefault(); run("verification");}},
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
    try {persist(); unsaved.hidden = false; update();} catch (error) {showError(error, {sourceRefresh:false});}
  }
  const retry = h("button", {class: "secondary", hidden: true, onclick: () => recover().catch(showError)}, t("setup_recover"));
  const next = h("button", {class: "secondary", hidden: true, onclick: () => {
    if (!alive() || damaged || !terminal()) return;
    const previous = saved;
    saved = {...saved, intent: null};
    if (operation?.status === "succeeded" && previous.intent.request.action === "setup.verification") saved.verification_base = null;
    try {persist(); operation = null; hostDisclosure.open = true; update(); refresh().catch(showError);}
    catch (error) {saved = previous; showError(error, {sourceRefresh:false});}
  }}, t("setup_next_change"));
  const sessions = h('a', {href: '#/sessions', class: 'ui-button primary', hidden: true, 'data-setup-sessions': ''}, t('setup_view_sessions'));
  const actions = h('div', {class: 'actions', hidden: true, 'data-setup-actions': ''}, sessions, retry, next);
  const hostDisclosure = h('details', {open: true, 'data-setup-connect': ''}, h('summary', {}, t('setup_host_title')), hostForm);
  const operationReceipt = h('div', {'data-setup-receipt': ''});
  const connectionStatus = h('div', {'data-setup-host-status': ''});
  const automaticStatus = h('p', {role: 'status', 'data-setup-progress': ''}, t('setup_loading'));
  const automaticButton = h('button', {type: 'button', class: 'primary setup-automatic', disabled: true,
    onclick: () => automatic().catch(showError)}, t('setup_review_connection'));
  const connectionChoice = h('select', {'aria-label': t('setup_connection_choice')});
  connectionChoice.addEventListener('change', () => {
    if (!alive() || busy || saved.intent) return;
    invalidateDiscovery();
    stagedSecrets.delete('host'); fields.get('bat_secret').value = '';
    saved.import_profile_id = '';
    try {persist();} catch (error) {showError(error, {sourceRefresh:false});}
  });
  const choiceField = h('label', {class: 'setup-field', hidden: true}, t('setup_choose_connection'), connectionChoice);
  const copyInput = h('input', {type: 'password', autocomplete: 'off', maxlength: 8192,
    'aria-label': t('setup_copy_label'), 'aria-describedby': 'setup-copy-help setup-copy-error'});
  const copyError = h('p', {id: 'setup-copy-error', class: 'error', role: 'alert', hidden: true});
  const copyRead = h('p', {role: 'status', hidden: true}, t('setup_link_ready'));
  let copyReady = false;
  function splitCopy() {
    if (!alive() || busy || saved.intent || !readReady || !copyInput.value) return;
    const parsed = parseBatConnectionUrl(copyInput.value);
    if (!parsed) return;
    copyInput.value = ''; // Split immediately, just like the existing advanced paste field.
    try {applyConnection(parsed); copyReady = true; copyRead.hidden = false;}
    catch (error) {showError(error, {sourceRefresh:false});}
  }
  copyInput.addEventListener('input', event => {
    copyReady = false; copyRead.hidden = true; copyError.hidden = true;
    if (!event.isComposing) splitCopy();
  });
  copyInput.addEventListener('compositionend', splitCopy);
  const copyForm = h('form', {hidden: true, 'data-setup-copy': '', onsubmit: event => {
    event.preventDefault(); splitCopy();
    copyInput.value = ''; // Invalid links are cleared too, without echoing them.
    copyError.hidden = copyReady;
    copyError.textContent = copyReady ? '' : t('setup_invalid_connection_url');
    if (!copyReady || !alive() || busy || saved.intent || !readReady) return;
    copyReady = false; copyRead.hidden = true;
    if (snapshot.discovery_contract_version === 1) void findSources().catch(showError);
    else void run('host', true);
  }}, h('p', {id: 'setup-copy-help'}, t('setup_copy_help')),
    h('label', {class: 'setup-field'}, t('setup_copy_label'), copyInput), copyError, copyRead,
    h('button', {type: 'submit', class: 'primary'}, t('setup_continue')));
  const simple = h('div', {'data-setup-simple': ''}, h('p', {}, t('setup_simple_help')),
    choiceField, automaticButton, copyForm);
  const advanced = h('details', {'data-setup-advanced': ''}, h('summary', {}, t('setup_advanced')),
    hostDisclosure,
    h("details", {}, h("summary", {}, t("setup_repository_title")), repoForm),
    h("details", {}, h("summary", {}, t("setup_verification_title")), verificationForm));
  const applyReview = h('button', {type: 'button', class: 'primary', onclick: () => applyConnectionReview()}, t('setup_apply_connection'));
  const reviewBox = h('section', {hidden: true, 'data-setup-review': '', 'aria-label': t('setup_review_connection')});
  const discoveredProfile = h('select', {'aria-label': t('setup_discovered_profile'), 'data-setup-discovered-profile': ''});
  discoveredProfile.addEventListener('change', () => {
    if (!alive() || busy || saved.intent || discovering) return;
    invalidateReview();
    void findSources(discoveredProfile.value).catch(showError);
  });
  const discoveredWorkspaces = h('div', {'data-setup-discovered-workspaces': ''});
  const discoveryBox = h('section', {hidden: true, 'data-setup-discovery': ''},
    h('label', {class: 'setup-field'}, t('setup_discovered_profile'), discoveredProfile),
    h('p', {class: 'muted'}, t('setup_discovery_read')), discoveredWorkspaces);
  const box = h("section", {class: "panel ui-section-card managed-setup", "data-managed-setup": ""},
    h('header', {}, h("h2", {}, t("setup_title"), " ", unsaved)),
    simple, automaticStatus, discoveryBox, reviewBox, connectionStatus, actions, message, receipt,
    advanced,
    h('details', {'data-setup-details': ''}, h('summary', {}, t('global_details')), summary,
      operationReceipt, h('p', {}, t('setup_resume_help'))));
  function defaultHost() {
    if (fields.get('host').value || saved.intent) return;
    const names = new Set((snapshot?.hosts || []).map(row => row.name));
    let base = 'bat';
    try {if (['127.0.0.1', '[::1]', 'localhost'].includes(new URL(fields.get('url').value).hostname.toLowerCase())) base = 'local';} catch { /* manual validation owns malformed endpoints */ }
    let name = base, suffix = 2;
    while (names.has(name)) name = `${base}-${suffix++}`;
    fields.get('host').value = saved.host = name;
  }
  function applyConnection(parsed) {
    invalidateDiscovery();
    fields.get('connection_url').value = '';
    for (const [name, value] of Object.entries({url: parsed.endpoint, fingerprint: parsed.fingerprint, bat_secret: parsed.token})) {
      fields.get(name).value = value; fieldError(name, null);
      if (name !== 'bat_secret') saved[name] = value;
    }
    defaultHost();
    stagedSecrets.delete('host'); saved.import_profile_id = ''; profile.value = ''; manual.open = false;
    fieldHint('bat_secret', t('setup_token_hint'));
    pasteRead.textContent = t('setup_connection_url_read', {host: parsed.endpoint.slice('wss://'.length),
      fingerprint: `${parsed.fingerprint.slice(0, 4)}…${parsed.fingerprint.slice(-4)}`});
    pasteRead.hidden = false;
    persist(); unsaved.hidden = false;
  }
  async function automatic() {
    if (!alive() || busy || !readReady || !snapshot || snapshot.busy || saved.intent || !allowed('host')) return;
    // Only reuse an existing explicitly configured, credential-bearing profile.
    // Multiple profiles require a person's choice; never guess a server or trust pin.
    const profiles = (snapshot.profiles || []).filter(row => row.token_available);
    const selected = profiles.find(row => row.id === connectionChoice.value) || (profiles.length === 1 ? profiles[0] : null);
    if (!fields.get('bat_secret').value && !usableSecret('host') && !saved.import_profile_id) {
      if (!selected) {
        if (profiles.length > 1) {choiceField.hidden = false; connectionChoice.focus();}
        else {copyForm.hidden = false; automaticButton.hidden = true; copyInput.focus();}
        return;
      }
      saved.import_profile_id = selected.id; profile.value = selected.id;
      for (const name of ['url', 'fingerprint', 'profile_id']) fields.get(name).value = saved[name] = selected[name] || '';
    }
    defaultHost(); persist();
    if (snapshot.discovery_contract_version === 1 && !discovery?.profile_id) await findSources();
    else await run('host', true);
  }
  function suggestLocal() {
    if (!snapshot || snapshot.hosts?.length !== 0 || fields.get('host').value) return;
    try {
      if (!['127.0.0.1', '[::1]', 'localhost'].includes(new URL(fields.get('url').value).hostname.toLowerCase())) return;
      fields.get('host').value = saved.host = 'local'; persist();
    } catch { /* incomplete endpoint */ }
  }
  function consumeConnectionUrl(reportError = true) {
    if (!alive() || busy || saved.intent || composing.has('connection_url')) return;
    const node = fields.get('connection_url');
    if (!node.value) {fieldError('connection_url', null); return;}
    clearPasteRead();
    const parsed = parseBatConnectionUrl(node.value);
    if (!parsed) {if (reportError) fieldError('connection_url', 'setup_invalid_connection_url'); return;}
    node.value = ''; fieldError('connection_url', null);
    try {applyConnection(parsed);} catch (error) {showError(error, {sourceRefresh:false});}
  }
  fields.get('connection_url').addEventListener('input', () => consumeConnectionUrl(false));
  for (const event of ['change', 'blur']) fields.get('connection_url').addEventListener(event, () => consumeConnectionUrl());
  fields.get('connection_url').addEventListener('paste', event => {
    if (!alive() || busy || saved.intent || event.isComposing || composing.has('connection_url')) return;
    const value = event.clipboardData?.getData('text');
    if (value === undefined) return;
    event.preventDefault(); clearPasteRead(); fields.get('connection_url').value = value; consumeConnectionUrl();
  });
  const usableSecret = kind => {
    const secret = stagedSecrets.get(kind);
    if (secret && secret.expiresAt > Date.now()) return secret.ref;
    stagedSecrets.delete(kind);
    if (secret && kind === 'host') invalidateDiscovery();
    return null;
  };
  function invalidateDiscovery() {
    discoverySerial++; discovery = null;
    clearTimeout(secretTimer);
    invalidateReview();
    if (typeof discoveryBox !== 'undefined') {discoveryBox.hidden = true; update();}
  }
  const hasFreshCredential = () => !!fields.get('bat_secret').value || (stagedSecrets.get('host')?.expiresAt || 0) > Date.now();
  const configuredHost = () => snapshot?.hosts?.find(row => row.name === fields.get('host').value.trim());
  const profileId = () => fields.get('profile_id').value.trim() || configuredHost()?.profile_id || 'default';
  function reuseExistingConnection() {
    const host = configuredHost();
    return !!host?.token_available && !saved.import_profile_id && !hasFreshCredential() &&
      host.url?.replace(/\/$/, '') === fields.get('url').value.trim().replace(/\/$/, '') &&
      normalizeSetupFingerprint(host.fingerprint || '') === normalizeSetupFingerprint(fields.get('fingerprint').value) &&
      (host.profile_id || 'default') === profileId();
  }
  const needsDiscovery = () => snapshot?.discovery_contract_version === 1 && !reuseExistingConnection() && !discovery?.profile_id;
  const discoveryReason = () => configuredHost() && !hasFreshCredential() ? 'setup_profile_token_required' : 'setup_discovery_required';
  async function findSources(selected) {
    if (!alive() || busy || reviewing || discovering || saved.intent || !readReady || snapshot?.busy || !allowed('host') || snapshot.discovery_contract_version !== 1) return;
    defaultHost();
    if (configuredHost() && !hasFreshCredential()) {
      showError(Error(t('setup_profile_token_required')), {sourceRefresh: false}); return;
    }
    if (!validateHost(undefined, true)) return;
    const serial = ++discoverySerial;
    invalidateReview(); discovering = true; update(); message.replaceChildren();
    try {
      const tokenField = fields.get('bat_secret');
      let ref = usableSecret('host');
      if (tokenField.value) {
        const result = await api('POST', '/managed/setup/secrets', {kind: 'bat', value: tokenField.value}); guard();
        if (serial !== discoverySerial) return;
        ref = result.secret_ref; tokenField.value = '';
        stagedSecrets.set('host', {ref, expiresAt: Date.now() + 3600 * 1000});
        fieldHint('bat_secret', t('setup_secret_staged'));
      }
      const body = {revision: snapshot.revision, ...(saved.import_profile_id && !ref
        ? {import_profile_id: saved.import_profile_id} : {url: fields.get('url').value.trim(), fingerprint: fields.get('fingerprint').value.trim(), secret_ref: ref}),
        ...(selected ? {profile_id: selected} : {})};
      const result = await api('POST', '/managed/setup/discover', body); guard();
      if (serial !== discoverySerial || body.revision !== snapshot?.revision) return;
      const row = (value, keys) => object(value) && Object.keys(value).length === keys.length &&
        keys.every(key => typeof value[key] === 'string' && value[key].length > 0 && value[key].length <= 256 &&
          value[key] === value[key].trim() && !/[\x00-\x1f]/.test(value[key]));
      if (!result.source || !/^[a-f0-9]{64}$/.test(result.source.source_digest) || !Array.isArray(result.profiles) ||
          result.profiles.length > 200 || !Array.isArray(result.workspaces) || result.workspaces.length > 200 ||
          !result.profiles.every(value => row(value, ['id', 'name'])) ||
          !result.workspaces.every(value => row(value, ['workspace_id', 'name'])) ||
          result.profile_id !== null && !result.profiles.some(row => row.id === result.profile_id)) throw Error(t('setup_receipt_mismatch'));
      discovery = result;
      discoveredProfile.replaceChildren(h('option', {value: ''}, t('setup_choose_profile')),
        ...result.profiles.map(row => h('option', {value: row.id}, `${row.name} · ${row.id}`)));
      discoveredProfile.value = result.profile_id || '';
      fields.get('profile_id').value = saved.profile_id = result.profile_id || '';
      discoveredWorkspaces.replaceChildren(...result.workspaces.map(row => h('button', {type: 'button', class: 'secondary', onclick: () => {
        if (!alive() || busy || saved.intent) return;
        fields.get('workspace_id').value = saved.workspace_id = row.workspace_id; persist(); unsaved.hidden = false;
      }}, `${row.name} · ${row.workspace_id}`)));
      discoveryBox.hidden = false; persist();
      clearTimeout(secretTimer);
      const secret = stagedSecrets.get('host');
      if (secret) secretTimer = setTimeout(() => {
        if (!alive()) return;
        stagedSecrets.delete('host'); invalidateDiscovery(); fieldHint('bat_secret', t('setup_paste_token_again'));
      }, Math.max(0, secret.expiresAt - Date.now()));
    } catch (error) {
      if (serial !== discoverySerial) return;
      invalidateDiscovery();
      if (error.code === 'INVALID_SECRET') {stagedSecrets.delete('host'); fieldHint('bat_secret', t('setup_paste_token_again'));}
      showError(error, {sourceRefresh: false});
    } finally {discovering = false; if (alive()) update();}
  }
  function validateHost(only, defaults = false) {
    const value = name => fields.get(name).value.trim(), faults = {};
    if (fields.get('connection_url').value) faults.connection_url = 'setup_invalid_connection_url';
    if (!value('host')) faults.host = 'setup_required';
    else if (!validSetupHostId(value('host'))) faults.host = 'error_code_INVALID_TARGET';
    if (!value('url')) faults.url = 'setup_required';
    else if (!validSetupEndpoint(value('url'))) faults.url = 'error_code_INVALID_HOST';
    if (!value('fingerprint')) faults.fingerprint = 'setup_required';
    else if (!normalizeSetupFingerprint(value('fingerprint')) || value('fingerprint').length > 200) faults.fingerprint = 'setup_invalid_fingerprint';
    if (value('profile_id').length > 256 || /[\x00-\x1f]/.test(value('profile_id'))) faults.profile_id = 'error_code_INVALID_PARAMS';
    const token = fields.get('bat_secret').value;
    if (token && (token.length < 8 || token.length > 4096 || /[^\x21-\x7e]/.test(token))) faults.bat_secret = 'error_code_INVALID_SECRET';
    else if (!token && !usableSecret('host') && !saved.import_profile_id &&
        !snapshot?.hosts?.some(host => host.name === value('host') && host.token_available)) faults.bat_secret = 'error_code_CREDENTIAL_REQUIRED';
    if (!defaults && fields.get('orchestrate').checked && !value('managed_roots')) faults.managed_roots = 'error_code_MANAGED_ROOT_REQUIRED';
    const names = ['connection_url', 'host', 'url', 'fingerprint', 'profile_id', 'bat_secret', 'managed_roots'];
    for (const name of only ? [only] : names) fieldError(name, faults[name]);
    if (!only && Object.keys(faults).length) focusField(names.find(name => faults[name]));
    return !Object.keys(faults).length;
  }
  function showError(error, options) {if (alive()) {
    const codeKey = `error_code_${error?.code}`;
    const summary = error?.code && t(codeKey) !== codeKey ? t(codeKey)
      : ['setup_verification_invalid', 'setup_receipt_mismatch', 'setup_discovery_required', 'setup_profile_token_required',
        'setup_scope_required', 'setup_busy', 'setup_loading', 'setup_uncertain', 'setup_review_unsupported'].some(key => t(key) === error?.message)
        ? error.message : t('error_generic');
    message.replaceChildren(errorBox(error, {summary, ...options, within:message})); update();
  }}
  function update() {
    if (!alive()) return;
    const locked = busy || !snapshot || !readReady || !!saved.intent || snapshot.busy;
    for (const [kind, form] of [["host", hostForm], ["repository", repoForm], ["verification", verificationForm]]) {
      for (const field of form.querySelectorAll("input,textarea,select,button")) field.disabled = !!locked || !allowed(kind);
      let scope = form.querySelector("[data-setup-scope]");
      if (!scope) {scope = h("p", {class: "note", "data-setup-scope": ""}, t("setup_scope_required")); form.prepend(scope);}
      scope.hidden = !!allowed(kind);
    }
    const supported = snapshot?.preview_contract_version === 1;
    const discoverySupported = snapshot?.discovery_contract_version === 1;
    fields.get('profile_id').closest('label').hidden = discoverySupported;
    const find = hostForm.querySelector('[data-setup-discover]'); find.hidden = !discoverySupported;
    find.disabled ||= discovering || reviewing || !!configuredHost() && !hasFreshCredential();
    const tokenReason = hostForm.querySelector('[data-setup-discovery-reason]');
    tokenReason.hidden = !discoverySupported || !configuredHost() || hasFreshCredential();
    tokenReason.textContent = t('setup_profile_token_required');
    const reviewReason = hostForm.querySelector('[data-setup-review-reason]');
    reviewReason.hidden = !needsDiscovery(); reviewReason.textContent = t(discoveryReason());
    hostForm.querySelector('button[type=submit]').disabled ||= !supported || reviewing || discovering || needsDiscovery();
    automaticButton.textContent = t(discoverySupported && !discovery?.profile_id ? 'setup_discover' : 'setup_review_connection');
    automaticButton.disabled = !!locked || !allowed('host') || !supported || reviewing || discovering;
    discoveredProfile.disabled = !!locked || discovering;
    for (const button of discoveredWorkspaces.querySelectorAll('button')) button.disabled = !!locked || discovering;
    automaticButton.hidden = !!review;
    for (const field of copyForm.querySelectorAll('input,button')) field.disabled = !!locked || !allowed('host');
    copyForm.querySelector('button').disabled ||= !supported || reviewing || discovering;
    copyForm.querySelector('button').textContent = t(discoverySupported ? 'setup_discover' : 'setup_continue');
    connectionChoice.disabled = !!locked || !allowed('host');
    simple.hidden = !!snapshot?.hosts?.length;
    connectionStatus.hidden = !readReady;
    const status = setupStatusLine({busy, operation, hosts: snapshot?.hosts || [], readReady});
    const reviewStatus = !saved.intent && (!supported && snapshot ? 'setup_review_unsupported'
      : reviewing ? 'setup_reviewing_connection' : reviewChanged ? 'setup_review_changed' : null);
    automaticStatus.replaceChildren(reviewStatus ? t(reviewStatus) : busy && saved.intent?.request.action === 'setup.host' ? t('setup_applying_connection') : status.key === 'setup_verified_saved'
      ? timeLabel(t, status.key, {host: operation.target.host, time: timeStamp(status.verifiedAt)})
      : status.key ? t(status.key) : '');
    automaticStatus.hidden = !automaticStatus.textContent;
    const base = saved.verification_base;
    const stale = base && base.revision !== snapshot?.revision;
    verificationNotice.hidden = !stale;
    verificationNotice.textContent = t("setup_verification_changed");
    refreshButton.disabled = busy; retry.hidden = !saved.intent || terminal() || damaged; retry.disabled = busy;
    next.hidden = !saved.intent || !terminal(); next.disabled = busy || damaged;
    if (damaged) retry.disabled = true;
    receipt.replaceChildren();
    operationReceipt.replaceChildren();
    if (operation && snapshot) operationReceipt.append(h("a", {href: `#/op/${operation.operation_id}`, 'data-setup-operation': ''},
      t('setup_operation_receipt', {status: operation.status === 'succeeded' ? t('setup_operation_done') : operationStatusLabel(operation.status)})));
    else if (saved.intent) receipt.append(h("p", {class: "note"}, t(saved.intent.problemCode ? 'setup_retained_request' : "setup_uncertain")));
    sessions.hidden = !snapshot?.hosts?.length;
    actions.hidden = sessions.hidden && retry.hidden && next.hidden;
    hostForm.querySelector('button[type=submit]').className = sessions.hidden ? 'primary' : 'secondary';
    reviewBox.hidden = !review;
    applyReview.disabled = !!locked || !review || !supported;
  }
  function invalidateReview() {
    reviewSerial++;
    reviewChanged ||= !!review || reviewing;
    review = null;
    if (typeof reviewBox !== 'undefined') {reviewBox.hidden = true; update();}
  }
  function showReview(preview, request) {
    if (preview?.contract_version !== 1 || preview.action !== 'setup.host' || !equal(preview.target, request.target) ||
        preview.config_revision !== request.preconditions.config_revision || !/^[a-f0-9]{64}$/.test(preview.review_digest) ||
        !/^[a-f0-9]{64}$/.test(preview.after_revision) || !Array.isArray(preview.changes) || !preview.source ||
        !['bat_profile', 'staged_secret', 'existing_connection'].includes(preview.source.kind) ||
        typeof preview.source.reference !== 'string' || typeof preview.source.available !== 'boolean' ||
        !preview.effects || preview.effects.configuration_written !== false || preview.effects.operation_created !== false || preview.effects.probe_started !== false)
      throw Error(t('setup_receipt_mismatch'));
    const labels = {url: 'setup_host_url', fingerprint: 'setup_fingerprint', profile_id: 'setup_workspace_profile',
      git: 'setup_review_git', writes: 'setup_allow_messages', orchestrate: 'setup_allow_start',
      shared_clone_worktrees: 'setup_shared_clone', managed_roots: 'setup_managed_roots', ssh_alias: 'setup_ssh_alias'};
    if (preview.changes.length > Object.keys(labels).length || preview.changes.some(row => !labels[row.field]))
      throw Error(t('setup_receipt_mismatch'));
    const value = item => item == null ? t('setup_review_unset') : typeof item === 'boolean'
      ? t(item ? 'setup_review_yes' : 'setup_review_no') : Array.isArray(item) ? item.join('; ') || t('setup_review_unset') : String(item);
    review = {request: structuredClone(request), digest: preview.review_digest}; reviewChanged = false;
    reviewBox.replaceChildren(h('h3', {}, t('setup_review_connection')),
      request.params.profile_id ? h('p', {}, `${t('setup_workspace_profile')}: ${request.params.profile_id}`) : null,
      h('p', {}, t(`setup_review_source_${preview.source.kind}`, {reference: preview.source.reference})),
      h('p', {}, t(preview.source.available ? 'setup_review_source_available' : 'setup_review_source_unavailable')),
      preview.changes.length ? h('table', {}, h('thead', {}, h('tr', {}, ...['field', 'before', 'after'].map(key => h('th', {}, t(`setup_review_${key}`))))),
        h('tbody', {}, ...preview.changes.map(row => h('tr', {}, h('th', {}, t(labels[row.field])), h('td', {}, value(row.before)), h('td', {}, value(row.after))))))
        : h('p', {}, t('setup_review_no_changes')),
      h('p', {}, t('setup_review_effects')),
      preview.effects.credential_staged ? h('p', {class: 'note'}, t('setup_review_staged')) : null,
      h('div', {class: 'actions'}, applyReview,
        h('button', {type: 'button', class: 'secondary', onclick: () => {invalidateReview(); update(); focusField('host');}}, t('setup_review_back')),
        h('button', {type: 'button', class: 'secondary', onclick: () => {invalidateReview(); reviewChanged = false; update();}}, t('setup_review_cancel'))));
    update(); applyReview.focus();
  }
  async function applyConnectionReview() {
    if (!alive() || busy || reviewing || !review || saved.intent || !readReady || snapshot.busy || !allowed('host')) return;
    if (review.request.preconditions.config_revision !== snapshot.revision) {invalidateReview(); update(); return;}
    busy = true; update(); message.replaceChildren();
    let sourceRefresh = false;
    try {
      saved.intent = {key: crypto.randomUUID(), request: {...structuredClone(review.request),
        preconditions: {...review.request.preconditions, review_digest: review.digest}}};
      try {persist();} catch (error) {delete saved.intent; throw error;}
      review = null;
      await sendOriginal(); sourceRefresh = true; await refresh();
    } catch (error) {handleSubmissionError(error, 'host', {sourceRefresh});}
    finally {busy = false; if (alive()) update();}
  }
  function accept(candidate) {
    const intent = saved.intent;
    if (!candidate || !/^op_[0-9a-f]{32}$/.test(candidate.operation_id) || candidate.actor !== caps()?.actor || candidate.action !== intent.request.action ||
        !equal(candidate.target, intent.request.target) || !equal(candidate.params, intent.request.params) ||
        !equal(candidate.preconditions, intent.request.preconditions) ||
        candidate.idempotency_key !== intent.key ||
        intent.operation_id && candidate.operation_id !== intent.operation_id) throw Error(t("setup_receipt_mismatch"));
    operation = candidate; intent.operation_id = candidate.operation_id; persist();
    if (candidate.status === "succeeded") {
      unsaved.hidden = true;
      if (candidate.action === 'setup.host') hostDisclosure.open = false;
      Promise.resolve(onConfigured?.()).catch(showError);
    }
    update();
  }
  async function sendOriginal() {
    const intent = saved.intent;
    const result = intent.operation_id ? await api("GET", `/operations/${encodeURIComponent(intent.operation_id)}`)
      : await api("POST", "/operations?wait=3", intent.request, intent.key);
    guard(); accept(result.operation);
  }
  async function recover() {
    if (!alive() || busy || !saved.intent || damaged) return;
    busy = true; update();
    let sourceRefresh = false;
    try {await sendOriginal(); sourceRefresh = true; await refresh();}
    catch (error) {handleSubmissionError(error, saved.intent?.request.action.split('.')[1], {sourceRefresh});}
    finally {busy = false; if (alive()) update();}
  }
  async function run(kind, defaults = false) {
    const form = kind === "host" ? hostForm : kind === "repository" ? repoForm : verificationForm;
    const blocked = !allowed(kind) ? 'setup_scope_required' : saved.intent ? 'setup_uncertain'
      : busy || reviewing || composing.size || snapshot?.busy ? 'setup_busy' : !snapshot || !readReady ? 'setup_loading'
      : kind === 'host' && snapshot.preview_contract_version !== 1 ? 'setup_review_unsupported'
      : kind === 'host' && needsDiscovery() ? discoveryReason() : null;
    if (!alive() || blocked) {showError(Error(t(blocked || 'setup_loading')), {sourceRefresh:false}); return;}
    if (kind === 'host' && !validateHost(undefined, defaults) || !setupFormValidity(form, t)) return; // Fields show the refusal.
    const serial = ++reviewSerial;
    if (kind === 'host') {reviewing = true; review = null; reviewChanged = false;} else busy = true;
    update(); message.replaceChildren();
    let sourceRefresh = false;
    try {
      if (kind === "verification" && saved.verification_base &&
          (!validVerification(saved.verification_base) || !/^[0-9a-f]{64}$/.test(saved.verification_base.revision)))
        throw Error(t("setup_verification_invalid"));
      persist(); // refuse to submit an intent that cannot survive reload
      const value = name => fields.get(name).value.trim();
      const checked = name => !defaults && fields.get(name).checked;
      const tokenField = kind === "verification" ? null : fields.get(kind === "host" ? "bat_secret" : "github_secret");
      let secretRef = usableSecret(kind);
      if (tokenField?.value) {
        const staged = await api("POST", "/managed/setup/secrets", {kind: kind === "host" ? "bat" : "github", value: tokenField.value});
        guard();
        if (kind === 'host' && serial !== reviewSerial) return;
        secretRef = staged.secret_ref; tokenField.value = "";
        stagedSecrets.set(kind, {ref: secretRef, expiresAt: Date.now() + 3600 * 1000});
        fieldHint(kind === 'host' ? 'bat_secret' : 'github_secret', t('setup_secret_staged'));
      }
      const imported = kind === "host" && !secretRef && saved.import_profile_id;
      const params = kind === "host" ? {...(imported ? {import_profile_id: imported} :
        {url: value("url"), fingerprint: value("fingerprint"), profile_id: profileId()}),
        writes: checked("writes"), orchestrate: checked("orchestrate"), shared_clone_worktrees: checked("shared_clone_worktrees"),
        managed_roots: defaults ? [] : value("managed_roots").split(/\r?\n|;/).map(item => item.trim()).filter(Boolean),
        ...(!defaults && value("ssh_alias") ? {ssh_alias: value("ssh_alias")} : {})}
        : kind === "repository" ? {host: value("repository_host"), workspace_id: value("workspace_id"), remote_url: value("remote_url"),
          allow_integrate: checked("allow_integrate"), allow_merge: checked("allow_merge"), allow_pr_update: checked("allow_pr_update")}
        : {commands: {...(saved.verification_base || snapshot.verification).commands,
            [value("verification_project")]: [value("verification_executable"), ...fields.get("verification_arguments").value.split(/\r?\n/).filter(arg => arg !== "")]},
          timeout_s: Number(value("verification_timeout"))};
      if (kind === "verification" && !validVerification(params)) throw Error(t("setup_verification_invalid"));
      if (secretRef) params.secret_ref = secretRef;
      if (kind === 'host' && snapshot.discovery_contract_version === 1 && !reuseExistingConnection()) {
        params.profile_id = discovery.profile_id;
        params.source_digest = discovery.source.source_digest;
      }
      const request = {action: `setup.${kind}`,
        target: kind === "host" ? {host: value("host")} : kind === "repository" ? {repository: value("repository")} : {}, params,
        preconditions: {config_revision: kind === "verification" ? saved.verification_base?.revision || snapshot.revision : snapshot.revision}};
      if (kind === 'host') {
        const response = await api('POST', '/managed/setup/preview', request);
        guard();
        if (serial !== reviewSerial || disposed || request.preconditions.config_revision !== snapshot?.revision) return;
        showReview(response.preview, request); return;
      }
      saved.intent = {key: crypto.randomUUID(), request};
      persist(); automaticStatus.textContent = t('setup_saving'); await sendOriginal(); sourceRefresh = true; await refresh();
    } catch (error) {
      if (kind === 'host' && serial !== reviewSerial) return;
      handleSubmissionError(error, kind, {sourceRefresh});
    }
    finally {busy = false; reviewing = false; if (alive()) update();}
  }
  function handleSubmissionError(error, kind, options = {sourceRefresh:false}) {
    if (!alive()) return;
    if (kind === 'host' && ['INVALID_SECRET', 'SETUP_REVIEW_CHANGED'].includes(error.code)) invalidateDiscovery();
    let field;
    if (saved.intent && !saved.intent.operation_id && error.admissionRefused === true) {
      const previous = saved.intent;
      delete saved.intent;
      try {persist();} catch (storageError) {saved.intent = previous; showError(storageError, {sourceRefresh:false}); return;}
      const hostFields = {INVALID_TARGET: 'host', INVALID_HOST: 'url', HOST_IDENTITY_CHANGED: 'host',
        INVALID_CONFIGURATION: 'fingerprint', CREDENTIAL_REQUIRED: 'bat_secret', INVALID_SECRET: 'bat_secret',
        MANAGED_ROOT_REQUIRED: 'managed_roots', SSH_NOT_VERIFIED: 'ssh_alias', PROFILE_UNAVAILABLE: 'profile_id', PROFILE_NOT_FOUND: 'profile_id'};
      field = kind === 'host' ? hostFields[error.code] : kind === 'repository'
        ? {INVALID_TARGET: 'repository', CREDENTIAL_REQUIRED: 'github_secret', INVALID_SECRET: 'github_secret', UNKNOWN_HOST: 'repository_host', WORKSPACE_NOT_FOUND: 'workspace_id'}[error.code] : null;
      if (error.code === 'INVALID_SECRET') stagedSecrets.delete(kind);
      if (!usableSecret(kind) && kind !== 'verification') fieldHint(kind === 'host' ? 'bat_secret' : 'github_secret', t('setup_paste_token_again'));
    } else if (saved.intent && !saved.intent.operation_id && ['CONFIGURATION_CHANGED', 'SETUP_BUSY', 'PRECONDITION_REQUIRED'].includes(error.code)) {
      saved.intent.problemCode = error.code; persist();
    }
    showError(error, {...options, ...(field ? {summary: t('setup_refused')} : {})});
    if (field) {
      fieldError(field, `error_code_${error.code}`);
      // Submission's finally unlocks the field before focus is restored.
      queueMicrotask(() => {if (alive()) {update(); focusField(field);}});
    }
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
  const observationKey = (row, fact) => JSON.stringify([row.connection_observation?.version,
    fact.source?.host, fact.source?.profile_id, fact.source?.binding_version, fact.source?.kind, fact.valid_until]);
  function paintObservations() {
    if (!alive() || document.hidden || !snapshot) return;
    for (const row of snapshot.hosts || []) {
      const facts = setupConnectionFacts(row);
      for (const name of connectionFacets) {
        const fact = facts[name], attempt = renewedObservations.get(observationKey(row, fact));
        const shown = setupConnectionFacts(row, {error: attempt?.pending ? null : attempt?.error})[name];
        const node = [...connectionStatus.querySelectorAll('[data-setup-facet]')].find(node => node.dataset.host === row.name && node.dataset.setupFacet === name);
        if (node) {
          node.dataset.freshness = shown.freshness;
          node.querySelector('[data-setup-fact-value]').textContent = healthLabel(shown, t);
        }
      }
    }
    // The receipt time is always the operation's completion time.
    const status = setupStatusLine({busy, operation, hosts: snapshot.hosts || [], readReady});
    if (!review && !reviewing) automaticStatus.replaceChildren(status.key === 'setup_verified_saved'
      ? timeLabel(t, status.key, {host: operation.target.host, time: timeStamp(status.verifiedAt)}) : status.key ? t(status.key) : '');
  }
  function renderObservations() {
    patch(connectionStatus, h('div', {'data-setup-host-status': '', hidden: connectionStatus.hidden}, ...(snapshot.hosts || []).map(row => {
      const facts = setupConnectionFacts(row);
      return h('section', {'data-setup-host-state': '', 'data-host': row.name}, h('h3', {}, row.name),
        ...connectionFacets.map(name => {
          const fact = facts[name], raw = row.connection_observation?.version === 1 ? row.connection_observation.facets?.[name] : null;
          return h('div', {'data-setup-facet': name, 'data-host': row.name},
            h('p', {}, h('strong', {}, t(`setup_observation_${name}`)), ' · ', h('span', {'data-setup-fact-value': ''})),
            h('p', {'data-setup-fact-reason': '', class: 'muted'}, t(`setup_observation_reason_${fact.reason || (fact.value === 'ready' ? 'observed' : 'unknown')}`)),
            fact.source && typeof raw?.observed_at === 'number' ? h('p', {class: 'muted'}, timeLabel(t, 'setup_connection_checked', {time: timeStamp(raw.observed_at)})) : null,
            fact.source ? h('details', {}, h('summary', {}, t('setup_observation_source')),
              h('p', {}, t(`setup_observation_${fact.source.kind}`), ' · ', fact.source.profile_id),
              h('code', {}, fact.source.ref), h('p', {}, h('code', {}, fact.source.binding_version))) : null);
        }));
    })));
    paintObservations();
  }
  async function renewObservations() {
    paintObservations();
    if (!alive() || document.hidden || observationRead || !snapshot) return;
    const attempts = [], now = Date.now();
    for (const row of snapshot.hosts || []) for (const fact of Object.values(setupConnectionFacts(row, {now}))) {
      const at = fact.valid_until, key = observationKey(row, fact);
      const attempt = claimObservationRenewal(renewedObservations, key, at, now);
      if (!attempt) continue;
      attempt.pending = true; attempts.push(attempt);
    }
    if (!attempts.length) return;
    // One setup read covers all configured hosts. Spend every due deadline
    // before awaiting it, including aliases with the same producer deadline.
    observationRead = true;
    try {await refresh({observationOnly: true});}
    catch (error) {for (const attempt of attempts) attempt.error = {code: error.code || error.status || 'READ_FAILED'};}
    finally {for (const attempt of attempts) attempt.pending = false; observationRead = false; paintObservations();}
  }
  document.addEventListener('health-display-tick', renewObservations);
  document.addEventListener('visibilitychange', renewObservations);
  async function refresh({observationOnly = false} = {}) {
    const serial = ++readSerial;
    let result;
    try {result = await api("GET", "/managed/setup"); guard();}
    catch (error) {
      if (observationOnly) throw error;
      if (alive() && serial === readSerial) {
        invalidateReview();
        readReady = false;
        if ([403, 404].includes(error.status)) {
          snapshot = null; summary.replaceChildren(); connectionStatus.replaceChildren(); verificationCommands.replaceChildren(); profile.replaceChildren();
          profileField.hidden = true; profilesError.hidden = true;
        }
        update();
      }
      throw error;
    }
    if (disposed || serial !== readSerial) return;
    if (observationOnly) {
      // Patch only readout data. Mounted fields, drafts, review and receipt keep
      // their nodes and state; configuration changes still use normal reload.
      snapshot = {...snapshot, hosts: result.hosts || []};
      renderObservations();
      return;
    }
    if (!validVerification(result.verification)) {
      if (allowed("verification")) {
        readReady = false; update();
        throw Error(t("setup_verification_invalid"));
      }
      result.verification = {commands: {}, timeout_s: 600};
    }
    if (snapshot && (snapshot.revision !== result.revision || !equal(snapshot.profiles, result.profiles) || result.preview_contract_version !== 1 || snapshot.discovery_contract_version !== result.discovery_contract_version)) invalidateDiscovery();
    snapshot = result; readReady = true;
    if (!hostDisclosureReady) {hostDisclosure.open = !result.hosts?.length; hostDisclosureReady = true;}
    suggestLocal();
    if (!saved.verification_timeout) fields.get("verification_timeout").value = String(result.verification.timeout_s);
    verificationCommands.replaceChildren(...Object.entries(result.verification.commands).map(([project, argv]) =>
      h("details", {}, h("summary", {}, project), h("pre", {class: "pre"}, argv.join("\n")),
        h("button", {type: "button", class: "mini", onclick: () => fillVerification(project)}, t("setup_verification_edit", {project})))));
    const hosts = result.hosts?.length || 0, repositories = result.repositories?.length || 0;
    renderObservations();
    summary.replaceChildren(h("p", {}, t("setup_counts", {hosts, repositories,
      hostLabel: t(hosts === 1 ? 'setup_count_host_one' : 'setup_count_host_many'),
      repositoryLabel: t(repositories === 1 ? 'setup_count_repository_one' : 'setup_count_repository_many')})),
      ...(result.hosts || []).map(row => h("p", {}, h("strong", {}, row.name), " · ",
        row.url)),
      ...[result.busy ? h("p", {class: "note warn"}, t("setup_busy")) : null].filter(Boolean),
      );
    profile.replaceChildren(h("option", {value: ""}, t("setup_manual_profile")),
      ...(result.profiles || []).map(row => h("option", {value: row.id}, row.name || row.id)));
    profile.value = saved.import_profile_id || "";
    const choice = connectionChoice.value;
    const usableProfiles = (result.profiles || []).filter(row => row.token_available);
    connectionChoice.replaceChildren(h('option', {value: ''}, t('setup_choose_placeholder')),
      ...usableProfiles.map(row => h('option', {value: row.id}, row.name || row.id)));
    connectionChoice.value = usableProfiles.some(row => row.id === choice) ? choice : '';
    choiceField.hidden = usableProfiles.length <= 1;
    profileField.hidden = !(result.profiles?.length || result.profiles_error);
    profile.hidden = profileField.hidden;
    profilesError.hidden = !result.profiles_error;
    profilesError.textContent = result.profiles_error ? t('setup_profiles_unavailable') : '';
    if (!saved.intent && saved.host && !fields.get('bat_secret').value && !usableSecret('host') && !saved.import_profile_id &&
        !result.hosts?.some(host => host.name === saved.host && host.token_available)) fieldHint('bat_secret', t('setup_paste_token_again'));
    update();
  }
  return {box, reload: () => refresh().catch(showError),
    async load() {try {await refresh(); if (saved.intent?.operation_id) await recover();} catch (error) {showError(error);}},
    dispose() {copyInput.value = ""; disposed = true; document.removeEventListener('health-display-tick', renewObservations);
      document.removeEventListener('visibilitychange', renewObservations); clearTimeout(secretTimer); stagedSecrets.clear(); for (const name of ['connection_url', "bat_secret", "github_secret"]) fields.get(name).value = "";}};
}
