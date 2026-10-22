import {managedServiceControls} from "./managed-service.js";
import {startupRoleChoice} from "./startup-role.js";
import {boardGitHubPanel} from './board-github.js';
import {clientActionGate, pollFailure} from "./central-service-status.js";
import {healthDisplay, healthLabel, healthAge, healthRevision, rememberHealthRead, withdrawHealthRead, selectHealthSession, resetHealthBootstrap, takeHealthRenewal} from './health-display.js';
import {browserCredentialRefusals, isCredentialRefusal, BROWSER_TOKEN_KEY as TOKEN_KEY} from './credential-refusals.js';
import {layoutDecision} from './workbench-mode.js';
import {workbenchViews} from './workbench-views.js';
import {settingsAgents} from './settings-agents.js';
import {actionAllowed, mayStartSession, checkpointReason, dispatchExplanation, connectionReturn} from './work-paths.js';
import {buildDiagnostics, buildLine, errorRing, sessionFacts} from './diagnostics.js';
import {diagnosticsDialog} from './diagnostics-dialog.js';
import {versionProjection} from './version-projection.js';
import {versionFacts} from './version-facts.js';
import {dashboardShell} from './shell.js';
import {lastWorkspace, lastProjectView} from './pm-workbench.js';
import {searchField, disclosure, emptyState, iconButton, pageHeader, segmented, statusPill, agentMark, inspector, menu, button, sectionCard, errorDetails} from './ui/components.js';
import {icon} from './ui/icons.js';
import {cliName} from './pm-agents-trend.js';
import {actionLabel, formatTime, operationMeta, operationStatusKey, operationStatusLabel, reasonText, workStatusKey} from './global-labels.js';
import {firstRunCard, inboxRow, settingsFrame, setupGuide} from './global-screens.js';
import {joinLabel} from './label-text.js';
import {discussHref, isContextRef} from './pm-discuss-link.js';
import {sessionHref, workSessionContext} from './session-navigation.js';
import {readWorkSessionContext} from './work-session-context.js';
import {workResultPanel} from './pm-work-result.js';
import {checkDelivery, prGroups, prPills} from './pm-work-inspector.js';
import {viewState} from './ui/view-state.js';
import {h as dom, place} from './ui/dom.js';
import {prSelector} from "./pr-selector.js";
import {mountPmWorkspace, pmAvailable, pmDurableForm} from "./pm-workspace.js";
import {mountWorkFollowUp, workFollowUpAvailable} from "./pm-work-follow-up.js";
import {workspaceNavigation} from "./workspace-nav.js";
import {managedSetupPanel, setupFormValidity} from "./managed-setup.js";
import {ApiError, errorPresentation, clientActionError, sessionErrorLine} from "./error-presentation.js";
import {managedControl, fleetAvailability} from "./transport/index.ts";
import {restoreBrowserSession, browserSessionToken, forgetBrowserSession, browserCredentialError} from "./transport/index.ts";
import {setupSidebarResizer, setupMobileSessionLayout} from "./workspace-layout.js";
import {observeViewport} from './ui/viewport.js';
import {attentionView} from "./attention.js";
import {parsePullRequest} from "./delivery-input.js";
import {sessionLabelsPanel, validLabels} from "./session-labels.js";
import {readArtifactContent} from "./transport/artifact-content.ts";
import {conversationPanel, readingSummary} from "./conversation.js";
import {composerShortcut} from "./composer-shortcut.js";
import {treeInteractions} from "./tree-interactions.js";
import {modelPreferencesPanel} from "./model-preferences.js";
import {projectSkillsPanel} from "./project-skills.js";
import {resultSourcesPanel} from "./result-sources.js";
import {repairPanel, repairWorkItemPanel} from "./repair-panel.js";
import {repairDispatchSeed, validateRepairRecord} from "./repair-intent.js";
import {instructionReceiptPanel} from "./instruction-receipts.js";
import {createRecordDisclosure} from "./create-record.js";
// BAT Dashboard: a client of /api/v1 only. Every change is an operation with an Idempotency-Key; text from
// sessions is always set with textContent (never parsed as HTML).
import { t, locale, setLocale } from "./i18n.js";
import {timeStamp, timeText, timeLabel, startTimeTicker} from "./ui/stamps.js";
import { connectorRequest, connectorUploadArtifact, nativeDiagnosticsSave, nativeDesktop, nativeFileSupport, nativeStatus, nativeConnect, nativeDisconnect, nativeEnroll, nativeReloadConfiguration, nativeSetupConfiguration, nativeForgetCredential, nativeSelectRole, openExternal } from "./transport/index.ts";
import { nativeAttachments } from "./native-files.js";
import { mountFleet } from "./fleet.js";
import { mountTailscale } from "./tailscale.js";
import { mountUpdates } from "./updates.js";
import { groupedSessions, matchesSession, runtimeStale, sessionActivity, sessionPill } from "./state/sessions.js";
import { capturePanel } from "./capture.js";
import { taskControlsPanel } from "./task-controls.js";
import { operationList } from "./operation-list.js";
import { permissionsPanel } from "./permissions.js";
import { approvalsPanel } from "./approvals.js";
import { sessionStartPanel } from "./session-start.js";
import { sessionBatPanel } from "./session-bat.js";
import { orchestrationPanel, orchestrationActions } from "./orchestration.js";
import { repositoryStartPanel } from "./repository-start.js";
import { mountArtifactReview, managedCaptureExecution } from "./artifact-review.js";
import {retiredRead, readThroughResume, retiredReadError} from "./state/retired-read.ts";
import { consumePageAsync, settleRefreshes, storageScope } from "./state/events.ts";
import {managedStorageScope, migrateManagedStorage} from './state/storage-identity.ts';
import {earlierDrafts, hideEarlierDraft, firstEarlierDraftNotice, markEarlierDraftNotice} from './state/earlier-drafts.ts';
import {earlierDraftPanel} from './earlier-drafts.js';
import {renderDeliveryWork, renderObservedResource, unavailableProject} from "./observed-resource.js";
import {observedText} from "./observed-resource-strings.js";

const isWorkbench = layoutDecision.layout === 'workbench';

const state = { token: null, caps: null, lastEvent: 0, lastEventAt: 0, lastPoll: null, listeners: new Set(), namespace: "", scopeIdentity: null, epoch: 0, online: false, pollHealth: 'initial', viewReady: false, sync: null, endpoint: "", connectionError: null, nativeBusy: false, nativeAttempt: 0, connectionNotice: null, refreshCycle: null };
const recentErrors = errorRing();
const diagnosticSessions = new Map();
let diagnosticSessionsRead = false;
let diagnosticRevision = 0, diagnosticsPreview = null;
const versionPainters = new Set();
let versionReads = {epoch:-1}, nativeVersionRead = null;
const paintVersions = () => {for(const paint of [...versionPainters])paint();};
function rememberVersionRead(key, data, at = new Date().toISOString(), epoch = state.epoch) {
  if(epoch !== state.epoch)return;
  if(versionReads.epoch !== epoch)versionReads = {epoch};
  versionReads[key] = {data,at};
  if (key === 'capabilities' && data && state.health) state.health.currentCaps = data;
  paintVersions();
}
async function readNativeVersions() {
  if(!nativeDesktop)return;
  if(nativeVersionRead?.epoch === state.epoch)return nativeVersionRead.promise;
  const epoch = state.epoch;
  const promise = Promise.allSettled([
    nativeStatus().then(value => rememberVersionRead('native',value.app_build,undefined,epoch)),
    managedControl({action:'status'}).then(value => rememberVersionRead('managed',value,undefined,epoch)),
  ]).finally(() => {if(nativeVersionRead?.epoch === epoch)nativeVersionRead=null;});
  nativeVersionRead = {epoch,promise};
  return promise;
}
function withdrawDiagnostics() {
  diagnosticRevision++; diagnosticSessions.clear(); diagnosticSessionsRead=false; diagnosticsPreview?.withdraw();
}
function diagnosticsClient(caps = versionReads.epoch === state.epoch ? versionReads.capabilities?.data : null) {
  const platform = navigator.platform || navigator.userAgent;
  const reads = versionReads.epoch === state.epoch ? versionReads : {};
  return {caps, appBuild:reads.native?.data, desktop:nativeDesktop,
    online:state.online, nativeReadAt:reads.native?.at, capabilitiesReadAt:reads.capabilities?.at,
    managedRuntime:reads.managed?.data, managedReadAt:reads.managed?.at,
    adapterRead:reads.adapter ? {document:reads.adapter.data,at:reads.adapter.at} : null,
    adapterRunRead:reads.adapterRun ? {run:reads.adapterRun.data,at:reads.adapterRun.at} : null,
    updater:reads.updater?.data, updaterReadAt:reads.updater?.at,
    os:/win/i.test(platform)?'windows':/mac/i.test(platform)?'macos':/linux/i.test(platform)?'linux':'unknown', locale, width:innerWidth};
}
async function openDiagnostics() {
  if(diagnosticsPreview)return;
  const epoch=state.epoch;
  diagnosticsPreview=diagnosticsDialog({h,t,onClose:()=>{diagnosticsPreview=null;}, load:async()=>{
    await readNativeVersions();
    let caps=null;
    const read=async(path,scope=null,offered=true,attempted=true)=>{
      const permitted=!scope || !!caps?.scopes?.includes(scope);
      if(!attempted || !offered || !permitted)return {attempted,offered,permitted,at:new Date().toISOString()};
      try {return {data:await api('GET',path),completed:true,at:new Date().toISOString()};}
      catch(error){return {error:{status:error.status,code:error.code},at:new Date().toISOString()};}
    };
    const capabilities=await read('/capabilities');caps=capabilities.data || null;
    if(epoch!==state.epoch)throw Error('Connection changed');
    if(!caps?.scopes?.includes('observe'))withdrawDiagnostics();
    const revision=diagnosticRevision;
    const [hostsRead,operationsRead,setupRead,observationRead]=await Promise.all([
      read('/hosts','observe',true,!!caps),read('/operations?limit=50','observe',true,!!caps),
      read('/managed/setup','manage',!!caps?.managed_installation,!!caps),read(null,null,false)]);
    if(epoch!==state.epoch)throw Error('Connection changed');
    const discarded=revision!==diagnosticRevision;
    const hosts=discarded?null:hostsRead.data, operations=discarded?null:operationsRead.data, setup=discarded?null:setupRead.data;
    return buildDiagnostics({...diagnosticsClient(caps), online:!!caps, exportedAt:new Date().toISOString(),
      hosts:hosts?.hosts, operations:operations?.operations, setup,
      sessions:hosts && caps?.scopes?.includes('observe') && diagnosticSessionsRead ? [...diagnosticSessions.values()] : null,
      recentErrors:recentErrors.entries(), sourceReads:{capabilities,hosts:{...hostsRead,discarded},operations:{...operationsRead,discarded},
        managed_setup:{...setupRead,discarded},observation:observationRead}});
  },save:async(content,filename)=>{
    if(epoch!==state.epoch)throw Error('Connection changed');
    if(nativeDesktop)return nativeDiagnosticsSave({content,filename,locale});
    const url=URL.createObjectURL(new Blob([content],{type:'application/json'}));
    const link=h('a',{href:url,download:filename});document.body.append(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);return true;
  }});
}
let earlierEntries = [], earlierIdentity = null, earlierScan = 0, earlierPanel = null, earlierPanelLocale = null;
let earlierScanState = nativeDesktop ? 'waiting' : 'skipped', earlierJump = false;
function clearEarlierDrafts(status = nativeDesktop ? 'waiting' : 'skipped') {
  earlierScan++; earlierEntries = []; earlierIdentity = null; earlierPanel = null; earlierJump = false; earlierScanState = status;
  document.querySelector('[data-earlier-drafts-notice]')?.remove();
  document.querySelector('[data-earlier-drafts]')?.remove();
  paintEarlierDrafts();
}
function acknowledgeEarlierDrafts() {
  if (!earlierIdentity) return;
  markEarlierDraftNotice(earlierIdentity, localStorage);
  document.querySelector('[data-earlier-drafts-notice]')?.remove();
}
function paintEarlierNotice() {
  document.querySelector('[data-earlier-drafts-notice]')?.remove();
  if (!state.token || !earlierIdentity || !earlierEntries.length || !firstEarlierDraftNotice(earlierIdentity, localStorage)) return;
  document.getElementById('main')?.prepend(h('aside', {'data-earlier-drafts-notice': '', class: 'earlier-drafts-notice', role: 'status'},
    h('span', {}, t('earlier_notice')), ' ', h('a', {href: '#/settings?section=about&drafts=earlier', onclick: () => {
      earlierJump = false; acknowledgeEarlierDrafts();
    }}, t('earlier_title'))));
}
function paintEarlierDrafts() {
  const slot = document.querySelector('[data-earlier-drafts-slot]');
  if (!slot) return;
  slot.dataset.scanState = earlierScanState;
  if (!earlierIdentity || !earlierEntries.length) {slot.replaceChildren(); return;}
  const identity = earlierIdentity, scope = managedStorageScope(identity), signature = JSON.stringify(earlierEntries);
  const current = () => earlierScanState === 'complete' && !!state.token && !state.nativeBusy && state.namespace === scope
    && earlierIdentity && managedStorageScope(earlierIdentity) === scope;
  if (!earlierPanel || earlierPanelLocale !== locale || earlierPanel.signature !== signature) {
    const expanded = earlierPanel?.open || false;
    earlierPanel = earlierDraftPanel({h, t, entries: earlierEntries, current, expanded, opened: acknowledgeEarlierDrafts,
      emptyFocus: () => {const heading = document.querySelector('[data-settings-panel="about"] h2'); if (heading) {heading.tabIndex = -1; heading.focus({preventScroll: true});}},
      hide: entry => {
        if (!current() || !hideEarlierDraft(identity, localStorage, entry)) return false;
        earlierEntries = earlierEntries.filter(row => row.id !== entry.id);
        earlierPanel.signature = JSON.stringify(earlierEntries);
        if (!earlierEntries.length) document.querySelector('[data-earlier-drafts-notice]')?.remove();
        return true;
      }});
    earlierPanel.signature = signature; earlierPanelLocale = locale;
  }
  if (earlierPanel.parentElement !== slot) slot.replaceChildren(earlierPanel);
  earlierPanel.resume();
  if (!earlierJump && new URLSearchParams(location.hash.split('?')[1] || '').get('drafts') === 'earlier') {
    earlierJump = true;
    const panel = earlierPanel;
    setTimeout(() => {if (panel === earlierPanel && panel.isConnected && current()) panel.reveal();}, 0);
  }
}
function scanEarlierDrafts(identity) {
  const scan = earlierScan; earlierScanState = 'scanning'; paintEarlierDrafts();
  setTimeout(async () => {
    try {
      if (scan !== earlierScan) return;
      const entries = await earlierDrafts(identity, localStorage);
      if (scan !== earlierScan || !state.token || state.namespace !== managedStorageScope(identity)) return;
      earlierIdentity = identity; earlierEntries = entries; earlierScanState = 'complete'; paintEarlierDrafts(); paintEarlierNotice();
    } catch {
      console.debug('draft_recovery_unreadable');
      if (scan === earlierScan) {earlierScanState = 'failed'; paintEarlierDrafts();}
    }
  }, 0);
}
function validateHealthBootstrap(caps, bootstrap) {
  const sync = bootstrap?.sync;
  if (sync?.version !== 1 || !sync.server_id || !sync.principal_id ||
    !Number.isSafeInteger(sync.checkpoint?.cursor) || sync.checkpoint.cursor < 0 || !sync.checkpoint.token ||
    bootstrap.capabilities?.actor !== caps.actor ||
    (caps.desktop_identity && (caps.desktop_identity.server_id !== sync.server_id || caps.desktop_identity.principal_id !== sync.principal_id)))
    throw new Error('Invalid central bootstrap identity');
}
async function activate(caps, endpoint = location.origin, reset = false, configurationSource = state.scopeIdentity?.configurationSource, retainView = false, verifiedBootstrap) {
  if (reset && earlierIdentity) {earlierScan++; earlierScanState = 'waiting'; paintEarlierDrafts();}
  else clearEarlierDrafts();
  withdrawDiagnostics(); recentErrors.clear();
  const previousHealthScope = {namespace: state.namespace, health: state.health};
  const retained = retainView ? {namespace: state.namespace, ready: state.viewReady} : null;
  // A same-identity resnapshot can retain an editing view and its guarded reads.
  // A different namespace still invalidates every old callback before mounting.
  if (!retained) state.epoch++;
  if (!retained) state.observations = new Map();
  // A verified health refresh does not dismiss an explicit credential-replacement outcome.
  if (!verifiedBootstrap) state.connectionError = null;
  state.online = false; state.pollHealth = 'initial'; state.viewReady = false;
  // Continuity verification retains its display; identity resets withdraw it before activation.
  state.health ||= {sessions: [], hosts: []};
  state.caps = caps; state.endpoint = endpoint; state.sync = null;
  let bootstrap;
  try { bootstrap = verifiedBootstrap || await api("GET", "/bootstrap"); }
  catch (e) { if (nativeDesktop || e.status !== 404) throw e; } // only browser clients support older contracts without bootstrap
  if (bootstrap) {
    const sync = bootstrap.sync;
    validateHealthBootstrap(caps, bootstrap);
    state.caps = bootstrap.capabilities;
    // Managed capabilities include desktop_identity; external centrals may omit
    // it. The bridge verifies bootstrap in both cases. Only the native managed
    // route treats a random loopback port as transport rather than storage identity.
    const identity = {actor: caps.actor, server_id: sync.server_id, principal_id: sync.principal_id};
    const managed = nativeDesktop && configurationSource === 'managed';
    if (!managed || earlierIdentity && managedStorageScope(earlierIdentity) !== managedStorageScope(identity)) clearEarlierDrafts(managed ? 'waiting' : 'skipped');
    if (managed) migrateManagedStorage(identity, localStorage, sessionStorage);
    state.scopeIdentity = {...identity, configurationSource};
    state.namespace = managed ? managedStorageScope(identity) : storageScope(endpoint, caps.actor, sync.server_id, sync.principal_id);
    state.sync = sync.checkpoint;
    if (!reset) {
      try {
        const saved = JSON.parse(localStorage.getItem(`batc.sync.${state.namespace}`));
        if (Number.isSafeInteger(saved?.cursor) && saved.cursor >= 0 && typeof saved.token === "string" && saved.token)
          state.sync = saved;
      } catch { /* use the bootstrap checkpoint */ }
    }
    state.lastEvent = state.sync.cursor;
    if (managed) scanEarlierDrafts(identity);
  } else {
    clearEarlierDrafts('skipped');
    state.scopeIdentity = null;
    state.namespace = storageScope(endpoint, caps.actor);
    // A legacy central has no continuity proof; always read current views after reconnect.
    state.lastEvent = 0;
  }
  if (retained) {
    if (state.namespace === retained.namespace) state.viewReady = retained.ready;
    else {state.epoch++; state.observations = new Map();}
  }
  state.online = true;
  state.health = resetHealthBootstrap(previousHealthScope.health, {
    sameScope: state.namespace === previousHealthScope.namespace, caps: state.caps});
}
function saveCursor() {
  if (state.sync) {
    try { localStorage.setItem(`batc.sync.${state.namespace}`, JSON.stringify(state.sync)); } catch { /* unavailable */ }
  }
}
function resetConnection() {
  clearEarlierDrafts();
  withdrawDiagnostics(); recentErrors.clear();
  state.epoch++; state.token = null; state.caps = null; state.lastEvent = 0; state.lastEventAt = 0;
  state.scopeIdentity = null;
  state.health = null;
  state.online = false; state.viewReady = false; state.sync = null;
  state.namespace = ""; state.endpoint = ""; state.lastPoll = null;
  state.pollHealth = 'initial'; state.connectionError = null; state.refreshCycle = null;
}
function disconnect() {
  if (state.token === browserSessionToken) forgetBrowserSession()?.catch(() => {});
  clearToken(); resetConnection();
}
async function connectBrowserToken(token) {
  resetConnection();
  browserCredentialRefusals.prepare(token);
  browserCredentialRefusals.retry(token);
  state.token = token;
  paintConnection();
  const connection = {epoch: state.epoch, namespace: state.namespace};
  const caps = await api('GET', '/capabilities');
  assertConnection(connection);
  await activate(caps);
}
function retryRememberedTokenButton() {
  const retry = h('button', {onclick: async () => {
    try {await connectBrowserToken(state.token);}
    catch (error) {if (error.code !== 'CONNECTION_CHANGED') {state.caps = null; state.connectionError = error;}}
    await route();
  }}, t('connection_retry_remembered'));
  const render = () => {retry.hidden = !state.token || state.token === browserSessionToken || !state.connectionError?.credentialRefused;};
  render();
  const paint = () => {
    if (!retry.isConnected) connectionPainters.delete(paint);
    else render();
  };
  connectionPainters.add(paint);
  return retry;
}

function loadToken() {
  if (nativeDesktop) return null;
  try { const token = sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY); browserCredentialRefusals.prepare(token); return token; } catch { return null; }
}
function saveToken(token, remember) {
  if (nativeDesktop) return;
  const previous = new Set();
  try {
    for (const store of [sessionStorage, localStorage]) {
      const old = store.getItem(TOKEN_KEY); if (old && old !== token) previous.add(old);
    }
  } catch { /* unavailable */ }
  for (const old of previous) browserCredentialRefusals.forget(old);
  browserCredentialRefusals.prepare(token);
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
    if (remember) localStorage.setItem(TOKEN_KEY, token); else localStorage.removeItem(TOKEN_KEY);
  } catch { /* storage may be unavailable; the token stays in memory */ }
}
function clearToken() {
  const forgotten = new Set();
  if (!nativeDesktop && state.token && state.token !== browserSessionToken) forgotten.add(state.token);
  try {
    for (const store of [sessionStorage, localStorage]) {
      const token = store.getItem(TOKEN_KEY); if (token) forgotten.add(token); store.removeItem(TOKEN_KEY);
    }
  } catch { /* unavailable */ }
  for (const token of forgotten) browserCredentialRefusals.forget(token);
}

// ------------------------------------------------------------------ DOM helpers
function h(tag, attrs = {}, ...children) {
  const el = dom(tag, attrs, ...children);
  if (nativeDesktop && tag === "a" && attrs.href && !attrs.href.startsWith("#")) {
    el.addEventListener("click", async event => {
      event.preventDefault();
      try { await openExternal(attrs.href); }
      catch (error) { document.getElementById("main").prepend(errorBox(error, {sourceRefresh:false, within:document.getElementById("main")})); }
    });
  }
  return el;
}
const chip = (text, cls = "") => h("span", { class: `chip ${cls}` }, text);
const when = value => timeStamp(value, {milliseconds: true, empty: ''});
const sessionWhen = value => timeText(value);
startTimeTicker();
function metaTail(text, sep) {
  const words = String(text).trim().split(/\s+/);
  const last = words.pop() || "";
  return [words.length ? words.join(" ") + " " : "", last + (sep ? "\u00a0·" : "")];
}
function metaAnchor(href, text, sep, quiet) {
  const [head, tail] = metaTail(text, sep);
  return h("a", {href, class: quiet ? "session-host" : null}, head, h("span", {class: "session-meta-tail"}, tail));
}
async function draftId(scope, request) {
  // A draft is the exact request: resending it (lost reply, double click) reuses its key, a different text does not.
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(request)));
  return `${scope}.${[...new Uint8Array(digest).slice(0, 12)].map(b => b.toString(16).padStart(2, "0")).join("")}`;
}
const TERMINAL = ["succeeded", "failed", "cancelled"];
function assertConnection(connection) {
  if (connection.epoch !== state.epoch || connection.namespace !== state.namespace)
    throw new ApiError(0, "CONNECTION_CHANGED", "Connection changed while preparing the operation");
}
async function keyFor(scope, connection) {
  // One idempotency key per draft: a retry of the same draft reuses it, so the action happens once. The server
  // keeps keys for good, so once the operation a key made has finished, the same draft is a new request.
  assertConnection(connection);
  const k = `batc.key.${connection.namespace}.${scope}`;
  let saved = null;
  try {
    const raw = localStorage.getItem(k);
    try { saved = JSON.parse(raw); } catch { saved = raw ? { key: raw } : null; } // a bare key from an older page
  } catch { saved = null; }
  if (saved?.key && saved.op) {
    try {
      const op = (await api("GET", `/operations/${saved.op}`)).operation;
      assertConnection(connection);
      if (TERMINAL.includes(op.status)) saved = null;
    } catch (e) {
      if (e.code === "CONNECTION_CHANGED") throw e;
      if (e.status === 404) saved = null;
    } // unreachable: keep the key, a retry must not act twice
  }
  assertConnection(connection);
  if (saved?.key) return saved.key;
  const key = crypto.randomUUID();
  try { localStorage.setItem(k, JSON.stringify({ key })); } catch { /* ignore */ }
  return key;
}
function rememberOp(scope, key, op, namespace) {
  try { localStorage.setItem(`batc.key.${namespace}.${scope}`, JSON.stringify({ key, op })); } catch { /* ignore */ }
}
function dropKey(scope, namespace) { try { localStorage.removeItem(`batc.key.${namespace}.${scope}`); } catch { /* ignore */ } }

// ------------------------------------------------------------------ API
async function api(method, path, body, key, collectReceipt, renewalDeadline) {
  const revision=diagnosticRevision, epoch=state.epoch, readHealth=state.health, inventoryRevision=state.health?.inventoryRevision;
  try {
    const data=await apiRequest(method,path,body,key,collectReceipt);
    if(epoch===state.epoch && method==='GET') {
      if (state.health && state.health === readHealth && state.health.inventoryRevision === inventoryRevision) {
        rememberHealthRead(state.health, path, data, {renewalDeadline});
        if (data && (Array.isArray(data.hosts) || Array.isArray(data.sessions) || data.session)) {
          state.health.readers ||= new Map();
          // Replay this existing GET exactly; do not synthesize a new route or BAT check.
          state.health.readers.set(path.replace(/\?$/, ''), api.bind(null, 'GET', path, undefined, undefined, undefined));
        }
        if (/^\/(hosts|sessions)(?:[/?]|$)/.test(path)) paintConnection(...connectionPaintFlags);
      }
      if(path==='/capabilities' || path==='/bootstrap')rememberVersionRead('capabilities',path==='/bootstrap'?data.capabilities:data,undefined,epoch);
      if(/^\/pm\/(?:installation-capabilities|workspaces\/[^/?]+\/capabilities)(?:\?|$)/.test(path))
        rememberVersionRead('adapter',{native_analysis:{required_version:data.native_analysis?.required_version}},undefined,epoch);
      if(/^\/pm\/workspaces\/[^/?]+\/runs\/[^/?]+(?:\?|$)/.test(path) && data.run)
        rememberVersionRead('adapterRun',{updated_at:data.run.updated_at,bat_runtime_provenance:{
          codex_source_revision:data.run.bat_runtime_provenance?.codex_source_revision,
          bat_source_revision:data.run.bat_runtime_provenance?.bat_source_revision}},undefined,epoch);
    }
    if(epoch===state.epoch && revision===diagnosticRevision && method==='GET' && /^\/sessions(?:[/?]|$)/.test(path)) {
      const previous = new Map(diagnosticSessions);
      if(Array.isArray(data.sessions)) {
        if(!new URLSearchParams(path.split('?')[1] || '').has('cursor'))diagnosticSessions.clear();
        diagnosticSessionsRead=true;
      } else if(data.session)diagnosticSessionsRead=true;
      for(const session of data.sessions || (data.session?[data.session]:[])) {
        const facts=sessionFacts(session), id=`${session.host}/${session.session_id}`;
        // Inventory reading summaries do not carry message-snapshot history reasons.
        const history = facts.history_reasons ?? previous.get(id)?.reading?.incomplete_reasons ?? null;
        diagnosticSessions.set(id, {loaded:facts.loaded,streaming:facts.streaming,stale:facts.stale,
          pending:facts.pending===true?{}:facts.pending===false?null:undefined,reading:{incomplete_reasons:history}});
      }
      const messages = path.match(/^\/sessions\/([^/?]+)\/([^/?]+)\/messages(?:[/?]|$)/);
      if(messages) {
        const id=messages.slice(1).map(decodeURIComponent).join('/');
        const history=sessionFacts({reading:data.reading}).history_reasons;
        diagnosticSessions.set(id, {...diagnosticSessions.get(id),reading:{incomplete_reasons:history}});
        diagnosticSessionsRead=true;
      }
    }
    return data;
  } catch(error) {
    if(epoch===state.epoch && !["VIEW_CHANGED", "FOREGROUND_READ_RETIRED"].includes(error.code)) {
      if (!nativeDesktop && state.token && state.token !== browserSessionToken && (error.credentialRefused || isCredentialRefusal(error.status, method, path))) {
        error.credentialRefused = true;
        state.connectionError = error;
        state.pollHealth = pollFailure(error, Date.now());
        updateOnline(false); paintConnection();
      }
      if(method==='GET' && (path==='/capabilities' || path==='/bootstrap'))rememberVersionRead('capabilities',null,undefined,epoch);
      recentErrors.record({at:new Date().toISOString(),method,path,status:error.status,code:error.code,admissionRefused:error.admissionRefused,operation:error.operation});
      if (state.health && state.health === readHealth) withdrawHealthRead(state.health, method, path, error);
      if([401,403,404].includes(error.status)) {
        withdrawDiagnostics();
      }
    }
    throw error;
  }
}
async function apiRequest(method, path, body, key, collectReceipt) {
  if (method === "POST") {
    const gate = clientActionGate(state, {post: true});
    if (!gate.allowed) throw clientActionError(gate.reason, t, state.token ? 0 : 401);
  }
  if (!state.token) throw new ApiError(401, "UNAUTHORIZED", t("need_token"), {localized: true});
  const epoch = state.epoch;
  // Source reads survive resume while a view is still loading, too. Readiness
  // gates writes; the shared event reader separately owns its next poll page.
  const resumable = method === 'GET';
  const request = signal => connectorRequest(method, path, body, key, state.token, signal);
  let response;
  try {
    const guard = () => {
      if (epoch !== state.epoch) throw new ApiError(0, "CONNECTION_CHANGED", "Connection changed while the request was in flight");
    };
    response = !resumable ? await request()
      : /^\/events(?:\?|$)/.test(path) ? await retiredRead(request(foregroundReads.signal), foregroundReads.signal)
      : await readThroughResume(request, () => foregroundReads.signal, guard);
  }
  catch (cause) {
    if (['FOREGROUND_READ_RETIRED', 'CONNECTION_CHANGED'].includes(cause?.code)) throw cause;
    if (cause?.credentialRefused) throw Object.assign(new ApiError(cause.status, cause.code, cause.message, {localized: cause.localized === true}), {credentialRefused: true});
    const timeout = ['AbortError', 'TimeoutError'].includes(cause?.name) || /timed?\s*out|timeout/i.test(cause?.message || String(cause));
    throw new ApiError(0, timeout ? 'TIMEOUT' : 'NETWORK_ERROR', cause?.message || String(cause));
  }
  const { status, data } = response;
  // A retired view may collect its exact original operation ID in its original
  // namespace. It must reread that ID under current authority before using results.
  if (status >= 200 && status < 300) collectReceipt?.(data);
  if (epoch !== state.epoch) throw new ApiError(0, "CONNECTION_CHANGED", "Connection changed while the request was in flight");
  if (status < 200 || status >= 300) {
    const error = new ApiError(status, data.error?.code, data.error?.message);
    error.admissionRefused = data.error?.admission_refused === true;
    error.reason = data.error?.reason;
    error.operation = data.operation ?? data.error?.operation;
    throw error;
  }
  return data;
}
function errorBox(e, {summary, within, insideDetails = false, sourceRefresh = true} = {}) {
  // Renderers display read errors locally. Also report them to the event batch so a
  // caught refresh error cannot acknowledge its checkpoint or re-enable mutations.
  // A request/receipt/validation outcome opts out; it is not a source read.
  if (sourceRefresh && state.refreshCycle) state.refreshCycle.error ||= e;
  const presentation = errorPresentation(e, t, {summary});
  const diagnostics = [presentation.code ? h('code', {}, presentation.code) : null,
    presentation.message && presentation.message !== presentation.code ? h('p', {}, presentation.message) : null].filter(Boolean);
  const body = h('div', {'data-error-details': ''}, ...diagnostics,
    button({variant:'ghost',label:t('diag_export'),onClick:openDiagnostics,'data-export-diagnostics':''}));
  return h('div', {'data-api-error': ''}, h("p", { class: "error", 'data-error-summary': '' }, presentation.summary),
    insideDetails || within?.closest?.('details') ? body : disclosure({label: t('global_details'), body}));
}
async function submit(action, target, params, preconditions, scope) {
  const connection = { epoch: state.epoch, namespace: state.namespace };
  const request = { action, target, params, preconditions };
  scope = await draftId(scope, request);
  assertConnection(connection);
  const key = await keyFor(scope, connection);
  assertConnection(connection);
  try {
    const out = await api("POST", "/operations?wait=3", request, key);
    assertConnection(connection);
    if (TERMINAL.includes(out.operation.status)) dropKey(scope, connection.namespace);
    else rememberOp(scope, key, out.operation.operation_id, connection.namespace);
    return out.operation;
  } catch (e) {
    // A local credential gate has not resolved an earlier uncertain submission.
    if (!e.credentialRefused && e.status && e.status < 500 && e.status !== 409) dropKey(scope, connection.namespace);
    throw e;
  }
}

function manualCapture(scope, source = {}, onAttach) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  return capturePanel({h, t, api, caps: () => state.caps, guard: () => assertView(connection), onEvents, errorBox,
    storageKey: `batc.capture.${connection.namespace}.${scope}`, source, onAttach});
}

// Browser bytes stay in memory; native bytes and exact transfer intents stay in the Rust spool.
function attachmentDraft(scope, text, initial = [], roles = false, onChange = () => {}) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const key = `batc.draft.${connection.namespace}.${scope}`;
  let saved;
  try { saved = JSON.parse(localStorage.getItem(key)); } catch { /* no usable draft */ }
  saved = saved && typeof saved === "object" ? saved : {text: text.value,
    attachments: initial.map(ref => ({name: ref.artifact_id, ref: {...ref}}))};
  if (!Array.isArray(saved.attachments)) saved.attachments = [];
  saved.attachments = saved.attachments.filter(a => a && typeof a === "object");
  for (const a of saved.attachments) delete a.busy;
  if (typeof saved.text === "string") text.value = saved.text;
  const files = new Map(), previews = new Map(), rows = h("div", {class: "attachment-list"});
  const ALLOWED_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
  const generateThumbnail = (file, onReady) => {
    if (!ALLOWED_IMAGE_TYPES.has(file.type) || file.size > 16 * 1024 * 1024) return;
    try {
      const reader = new FileReader();
      reader.onload = () => {
        const img = new Image();
        img.onload = () => {
          try {
            const maxDim = 96;
            let w = img.width, h = img.height;
            if (w <= 0 || h <= 0) return;
            if (w > maxDim || h > maxDim) {
              if (w > h) { h = Math.max(1, Math.round((h * maxDim) / w)); w = maxDim; }
              else { w = Math.max(1, Math.round((w * maxDim) / h)); h = maxDim; }
            }
            const canvas = document.createElement("canvas");
            canvas.width = w; canvas.height = h;
            const ctx = canvas.getContext("2d");
            if (!ctx) return;
            ctx.drawImage(img, 0, 0, w, h);
            onReady(canvas.toDataURL("image/png"));
          } catch { /* canvas error */ }
        };
        img.src = reader.result;
      };
      reader.readAsDataURL(file);
    } catch { /* reader error */ }
  };
  let pendingSelections = 0;
  const changed = () => queueMicrotask(() => {if (box.isConnected && connection.epoch === state.epoch && connection.generation === generation) onChange();});
  const status = h("p", {class: "muted", role: "status"});
  const promptHint = h("p", {class: "muted", hidden: true}, t("dispatch_attachment_prompt", {request: t("dispatch_inspect_images")}));
  const supported = Boolean(state.caps?.artifacts), nativeFiles = nativeDesktop && nativeFileSupport;
  const nativeUploadAllowed = state.caps?.actions?.some(a => a.action === "artifact.upload" && a.allowed === true);
  let native;
  const choose = nativeFiles ? h("button", {class: "secondary", disabled: !supported || !may("manage") || !nativeUploadAllowed, onclick: () => native.pick()}, t("files_choose")) : h("input", {type: "file", multiple: true, disabled: !supported || !may("manage"), "aria-label": t("choose_attachments")});
  const box = h("div", {class: "attachments", hidden: !supported}, nativeFiles ? h("div", {class: "actions"}, h("strong", {}, t("attachments")), choose) : h("label", {}, t("attachments"), choose),
    h("p", {class: "muted"}, t(nativeFiles ? "files_help" : "upload_on_choose")), rows, promptHint, status);
  const attachmentGate = requirements => clientActionGate({...state, manageAllowed: may("manage"),
    attachmentsSupported: supported, uploadsAllowed: nativeUploadAllowed, nativeDesktop}, requirements);
  const refuseAttachment = reason => {
    if (!supported) {
      box.hidden = false;
      for (const child of box.children) child.hidden = child !== status;
    }
    for (let parent = box.parentElement; parent; parent = parent.parentElement)
      if (parent.tagName === 'DETAILS') parent.open = true;
    fill(status, errorBox(clientActionError(reason, t), {sourceRefresh:false, within:status}));
  };
  const guard = (mounted = false) => {
    assertView(connection);
    if (mounted && !box.isConnected) throw new ApiError(0, "VIEW_CHANGED", "Attachment form changed during the request");
  };
  const persist = (required = false) => {
    guard(); saved.text = text.value;
    try { localStorage.setItem(key, JSON.stringify(saved)); } catch (error) { if (required === true) throw error; }
    promptHint.hidden = roles || Boolean(text.value.trim()) || !saved.attachments.some(a => a.ref);
    changed();
  };
  const removeStored = () => { guard(); try { localStorage.removeItem(key); } catch { /* ignore */ } };
  text.addEventListener("input", persist);
  const refs = () => saved.attachments.filter(a => a.ref).map(a => roles ? {...a.ref, role: a.ref.role || "input"}
    : {artifact_id: a.ref.artifact_id, revision: a.ref.revision, digest: a.ref.digest});
  const snapshot = () => JSON.stringify({text: text.value, attachments: refs(), fields: saved.fields});
  const ready = () => pendingSelections === 0 && saved.attachments.every(a => a.ref);
  const attachmentKeys = new WeakMap();
  // Set only by a Retry press, so a file's first upload does not show that button.
  const retrying = new WeakSet();
  let attachmentSerial = 0, catalogBusy = false;
  const attachmentKey = a => {
    let key = attachmentKeys.get(a);
    if (!key) attachmentKeys.set(a, key = "a" + (++attachmentSerial));
    return key;
  };
  // Upload rows and the catalog pager share this status line. Keep it busy while either is in flight.
  const syncStatusBusy = () => {
    if (catalogBusy || saved.attachments.some(item => item.busy)) status.setAttribute("aria-busy", "true");
    else status.removeAttribute("aria-busy");
  };
  const render = () => {
    changed();
    promptHint.hidden = roles || Boolean(text.value.trim()) || !saved.attachments.some(a => a.ref);
    const active = document.activeElement;
    const focusKey = active?.closest?.("[data-attachment]")?.getAttribute("data-attachment") || "";
    const focusControl = active?.getAttribute?.("data-attachment-control") || "";
    const visible = saved.attachments.filter(a => !nativeFiles || !a.native_handle || a.ref || !native?.has(a.native_handle));
    fill(rows, ...visible.map(a => h("div", {class: "row attachment-row", "data-attachment": attachmentKey(a)},
      previews.has(a) ? h("img", {class: "attachment-thumb", src: previews.get(a), alt: a.name || t("attachments")}) : null,
      h("div", {class: "grow"}, a.name, a.ref ? h("div", {class: "muted"}, `${a.ref.artifact_id} · r${a.ref.revision} · ${a.ref.digest.slice(0, 12)}`)
        : h("div", {class: "muted"}, a.error || (a.native_handle ? t("files_unavailable") : files.has(a) ? t("uploading") : t("choose_again")))),
      a.ref && roles ? h("select", {"aria-label": t("attachment_role"), "data-attachment-control": "role", onchange: e => {guard(); a.ref.role = e.target.value; persist();}},
        ...["input", "result"].map(role => h("option", {value: role, selected: (a.ref.role || "input") === role}, t(`attachment_${role}`)))) : null,
      a.ref && native ? native.actions(a.ref) : null,
      !a.ref && files.has(a) && (!a.busy || retrying.has(a)) ? h("button", {class: "secondary", "data-attachment-control": "retry", "aria-disabled": a.busy ? "true" : null, onclick: () => {
        if (a.busy) return;
        retrying.add(a);
        upload(a);
      }}, t("retry")) : null,
      h("button", {class: "secondary", "data-attachment-control": "remove", "aria-disabled": a.busy ? "true" : null, onclick: () => {
        guard();
        if (a.busy) return;
        if (a.native_handle) {saved.native_ignored ||= []; saved.native_ignored.push(a.native_handle);}
        saved.attachments = saved.attachments.filter(x => x !== a); files.delete(a); previews.delete(a); persist(); render();
      }}, t("remove")))));
    syncStatusBusy();
    if (!focusKey) return;
    const row = rows.querySelector(`[data-attachment="${CSS.escape(focusKey)}"]`);
    const same = focusControl && row?.querySelector(`[data-attachment-control="${CSS.escape(focusControl)}"]`);
    if (same) { same.focus(); return; }
    // A finished upload removes Retry. Stay on this attachment: its role, or Remove when it has none.
    if (focusControl === "retry" && row) (row.querySelector('[data-attachment-control="role"]') || row.querySelector('[data-attachment-control="remove"]'))?.focus();
  };
  if (nativeFiles) {
    if (!Array.isArray(saved.native_ignored)) saved.native_ignored = [];
    saved.native_ignored = saved.native_ignored.filter(id => typeof id === "string" && /^file_[0-9a-f]{32}$/.test(id));
    if (typeof saved.native_draft !== "string" || !/^[0-9a-f-]{36}$/.test(saved.native_draft))
      saved.native_draft = saved.attachments.find(a => typeof a.native_receipt?.draft_id === "string" && /^[0-9a-f-]{36}$/.test(a.native_receipt.draft_id))?.native_receipt.draft_id || crypto.randomUUID();
    native = nativeAttachments({h, t, guard: () => guard(true),
      canWrite: () => attachmentGate({attachment: true, manage: true, uploads: true}), draftId: saved.native_draft,
      onVisibility: render,
      onDiscard: id => {guard(true); saved.attachments = saved.attachments.filter(a => a.native_handle !== id || a.ref); persist(); render();},
      attached: id => saved.attachments.some(a => a.native_handle === id && a.ref) || saved.native_ignored?.includes(id),
      onReceipt: receipt => {
        guard(true);
        if (saved.native_ignored?.includes(receipt.transfer_id)) return;
        let a = saved.attachments.find(a => a.native_handle === receipt.transfer_id);
        if (a?.native_receipt && ["intent_key", "display_name", "size_bytes", "digest"].some(field => a.native_receipt[field] !== receipt[field])) throw new Error(t("files_receipt_mismatch"));
        if (a && JSON.stringify(a.native_receipt) === JSON.stringify(receipt)) return;
        if (!a) {a = {name: receipt.display_name, native_handle: receipt.transfer_id}; saved.attachments.push(a);}
        a.native_receipt = receipt;
        if (receipt.stage === "ready") a.ref = {...receipt.artifact, ...(roles ? {role: a.ref?.role || "input"} : {})};
        persist(); render();
      }});
    choose.after(native.drop); box.insertBefore(native.box, rows); persist();
  }
  const acceptUpload = (a, op) => {
    if (op.status !== "succeeded") return;
    a.ref = {artifact_id: op.result.artifact_id, revision: op.result.revision, digest: op.result.digest,
      ...(roles ? {role: "input"} : {})};
    delete a.operation_id; delete a.key; delete a.error; delete a.request; files.delete(a); persist(); render();
  };
  const upload = async a => {
    if (a.busy) return;
    guard(); a.busy = true; delete a.error; render();
    try {
      const file = files.get(a), limit = Math.min(state.caps.artifacts.limits.max_file_bytes,
        nativeDesktop ? 16 * 1024 * 1024 : Number.MAX_SAFE_INTEGER);
      if (file.size > limit) throw new Error(`ARTIFACT_TOO_LARGE (${limit})`);
      const bytes = await file.arrayBuffer(); guard(true);
      const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(x => x.toString(16).padStart(2, "0")).join("");
      guard(true);
      const request = {action: "artifact.upload", target: {}, params: {display_name: a.name || file.name,
        media_type: file.type || "application/octet-stream", size_bytes: file.size, expected_digest: digest}, preconditions: {}};
      if (a.request && JSON.stringify(a.request) !== JSON.stringify(request)) throw new Error(t("attachment_file_changed"));
      a.request ||= request; a.key ||= crypto.randomUUID(); persist(true);
      let op = a.operation_id ? (await api("GET", `/operations/${a.operation_id}`)).operation : null;
      guard(true);
      if (op && ["failed", "cancelled"].includes(op.status)) {op = null; a.key = crypto.randomUUID(); delete a.operation_id; persist(true);}
      if (!op) op = (await api("POST", "/operations?wait=3", a.request, a.key)).operation;
      guard(true); a.operation_id = op.operation_id; persist();
      const deadline = Date.now() + 60000;
      const readNext = async () => {
        if (Date.now() > deadline) throw new Error(t("attachment_pending"));
        await sleep(400); guard(true);
        const next = (await api("GET", `/operations/${op.operation_id}`)).operation; guard(true); return next;
      };
      while (["accepted", "running"].includes(op.status)) op = await readNext();
      if (op.status === "waiting_external") {
        const gate = attachmentGate({attachment: true});
        if (!gate.allowed) throw clientActionError(gate.reason, t);
        const epoch = state.epoch;
        const response = await connectorUploadArtifact(op.operation_id, bytes, state.token);
        guard(true);
        if (epoch !== state.epoch) throw new ApiError(0, "CONNECTION_CHANGED", "Connection changed during upload");
        if (response.status === 401) throw browserCredentialError({status: 401, code: 'UNAUTHORIZED',
          credentialRefused: !nativeDesktop && state.token !== browserSessionToken});
        if (response.status < 200 || response.status >= 300)
          throw new ApiError(response.status, response.data.error?.code, response.data.error?.message);
        do {op = await readNext();} while (["accepted", "running", "waiting_external"].includes(op.status));
      }
      if (op.status !== "succeeded") throw new Error(`${op.error_code || op.status}: ${op.status_reason || ""}`);
      acceptUpload(a, op);
    } catch (e) {
      if (connection.epoch !== state.epoch || connection.generation !== generation || !box.isConnected) return;
      a.error = e.message;
    } finally {
      delete a.busy;
      retrying.delete(a);
      if (connection.epoch === state.epoch && connection.generation === generation && box.isConnected) {persist(); render();}
    }
  };
  if (!nativeFiles) choose.onchange = () => {
    guard();
    for (const file of choose.files) {
      let a = saved.attachments.find(x => !x.ref && !files.has(x) && x.name === file.name);
      if (!a) {a = {name: file.name}; saved.attachments.push(a);}
      files.set(a, file);
      if (ALLOWED_IMAGE_TYPES.has(file.type)) {
        generateThumbnail(file, thumb => { if (box.isConnected && saved.attachments.includes(a)) {previews.set(a, thumb); render();} });
      }
      upload(a);
    }
    choose.value = ""; persist(); render();
  };
  const handleIncomingFile = file => {
    guard();
    fill(status);
    const limit = Math.min(state.caps?.artifacts?.limits?.max_file_bytes || 16 * 1024 * 1024,
      nativeDesktop ? 16 * 1024 * 1024 : Number.MAX_SAFE_INTEGER);
    if (!ALLOWED_IMAGE_TYPES.has(file.type)) {
      fill(status, errorBox(new Error(t("attachment_unsupported_image")), {sourceRefresh:false, within:status}));
      return;
    }
    if (file.size > limit) {
      fill(status, errorBox(new ApiError(413, "ARTIFACT_TOO_LARGE", `Limit: ${limit}`), {sourceRefresh:false, within:status}));
      return;
    }
    let name = file.name;
    const ext = file.type === "image/jpeg" ? ".jpg" : file.type === "image/webp" ? ".webp" : ".png";
    if (!name || name === "image.png" || name === "blob") {
      name = `pasted-image-${Date.now()}${ext}`;
    }
    name = name.replace(/[^\w.-]/g, "_").replace(/^(\.+)/, "image_");
    let uniqueName = name;
    let counter = 1;
    while (saved.attachments.some(x => x.name === uniqueName)) {
      const dot = name.lastIndexOf(".");
      uniqueName = dot > 0 ? `${name.slice(0, dot)}-${counter}${name.slice(dot)}` : `${name}-${counter}`;
      counter++;
    }
    const a = {name: uniqueName};
    saved.attachments.push(a);
    files.set(a, file);
    generateThumbnail(file, thumb => {
      if (box.isConnected && saved.attachments.includes(a)) {previews.set(a, thumb); render();}
    });
    upload(a);
    persist();
    render();
  };
  const onPaste = e => {
    if (e.isComposing) return;
    const cd = e.clipboardData;
    if (!cd) return;
    const items = cd.items ? Array.from(cd.items) : [];
    if (nativeDesktop) {
      // MIME metadata only: never materialize a WebView clipboard File or read its bytes.
      if (!items.some(item => item.kind === "file" && item.type.startsWith("image/"))) return;
      e.preventDefault();
      const gate = attachmentGate({attachment: true, supported: true, manage: true, uploads: true});
      if (!gate.allowed) {refuseAttachment(gate.reason); return;}
      if (!native) {refuseAttachment("attachments_unavailable"); return;}
      pendingSelections++; changed(); fill(status);
      native.paste().catch(error => {
        try {
          guard(true);
          if (error?.code === "CLIENT_ACTION_NATIVE_PASTE_UNSUPPORTED") refuseAttachment("native_paste_unsupported");
          else fill(status, errorBox(error?.code ? new ApiError(0, error.code, error.reason || "") : error, {sourceRefresh:false, within:status}));
        } catch { /* old view */ }
      }).finally(() => {pendingSelections--; changed();});
      return;
    }
    const cdFiles = cd.files ? Array.from(cd.files) : [];
    const imageCandidates = [];
    if (cdFiles.length > 0) {
      for (const f of cdFiles) {
        if (f.type.startsWith("image/") || /\.(png|jpe?g|webp|gif|svg)$/i.test(f.name)) {
          imageCandidates.push(f);
        }
      }
    } else if (items.length > 0) {
      for (const it of items) {
        if (it.kind === "file") {
          const f = it.getAsFile();
          if (f && (f.type.startsWith("image/") || /\.(png|jpe?g|webp|gif|svg)$/i.test(f.name) || it.type.startsWith("image/"))) {
            imageCandidates.push(f);
          }
        }
      }
    }
    if (!imageCandidates.length) return;
    e.preventDefault();
    const gate = attachmentGate({attachment: true, paste: true, supported: true, manage: true, uploads: nativeFiles});
    if (!gate.allowed) {
      refuseAttachment(gate.reason);
      return;
    }
    for (const file of imageCandidates) {
      handleIncomingFile(file);
    }
  };
  const onDragOver = e => {
    if (nativeFiles) return; // native handle/spool owns OS drag-and-drop
    if (e.dataTransfer?.types?.includes("Files")) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      box.classList.add("drag-over");
      if (text && text.classList) text.classList.add("drag-over");
    }
  };
  const onDragLeave = e => {
    if (!box.contains(e.relatedTarget) && e.target !== text) {
      box.classList.remove("drag-over");
      if (text && text.classList) text.classList.remove("drag-over");
    }
  };
  const onDrop = e => {
    box.classList.remove("drag-over");
    if (text && text.classList) text.classList.remove("drag-over");
    if (nativeFiles || !e.dataTransfer?.files?.length) return;
    e.preventDefault();
    // nativeFiles returned above; its upload permission clause was unreachable.
    const gate = attachmentGate({attachment: true, supported: true, manage: true});
    if (!gate.allowed) {
      refuseAttachment(gate.reason);
      return;
    }
    for (const file of e.dataTransfer.files) {
      handleIncomingFile(file);
    }
  };
  text.addEventListener("paste", onPaste);
  box.addEventListener("paste", onPaste);
  box.addEventListener("dragover", onDragOver);
  box.addEventListener("dragleave", onDragLeave);
  box.addEventListener("drop", onDrop);
  if (text && text.addEventListener) {
    text.addEventListener("dragover", onDragOver);
    text.addEventListener("dragleave", onDragLeave);
    text.addEventListener("drop", onDrop);
  }
  const existing = h("select", {"aria-label": t("existing_artifact")}, h("option", {value: ""}, t("existing_artifact")));
  box.append(h("div", {class: "actions"}, existing, h("button", {class: "secondary", onclick: async () => {
    if (!existing.value) return;
    const [artifactId, revision] = existing.value.split(":");
    pendingSelections++; changed();
    try {
      guard(); const {artifact} = await api("GET", `/artifacts/${artifactId}/revisions/${revision}`); guard(true);
      if (artifact.state !== "ready") throw new Error(t("attachments_not_ready"));
      if (!saved.attachments.some(a => a.ref?.artifact_id === artifactId && a.ref?.revision === Number(revision)))
        saved.attachments.push({name: artifact.display_name, ref: {artifact_id: artifactId, revision: Number(revision),
          digest: artifact.digest, ...(roles ? {role: "input"} : {})}});
      persist(); render();
    } catch (e) {if (connection.epoch === state.epoch) fill(status, errorBox(e, {within:status}));}
    finally {pendingSelections--; changed();}
  }}, t("add_attachment"))));
  let catalogCursor = null;
  const more = h("button", {class: "secondary", hidden: true, onclick: async () => {
    if (catalogBusy) return;
    catalogBusy = true;
    more.setAttribute("aria-disabled", "true");
    syncStatusBusy();
    try {await loadCatalog(catalogCursor);} catch (e) {if (box.isConnected) fill(status, errorBox(e, {within:status}));}
    finally {
      catalogBusy = false;
      if (!box.isConnected || connection.epoch !== state.epoch || connection.generation !== generation) return;
      more.removeAttribute("aria-disabled");
      syncStatusBusy();
      const active = document.activeElement;
      if ((more.hidden || more.disabled) && (active === more || active === document.body)) {status.tabIndex = -1; status.focus();}
    }
  }}, t("more"));
  box.append(more);
  if (state.caps?.artifacts?.capture?.manual_single_file) box.append(manualCapture(scope, {}, (ref, name) => {
    guard(true);
    if (!saved.attachments.some(a => a.ref?.artifact_id === ref.artifact_id && a.ref?.revision === ref.revision))
      saved.attachments.push({name, ref: {...ref, ...(roles ? {role: "input"} : {})}});
    persist(); render();
  }));
  const loadCatalog = async (cursor = "") => {
    const page = await api("GET", `/artifacts?limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`); guard(true);
    const selected = existing.value, selectedOption = existing.selectedOptions[0];
    if (!cursor) existing.replaceChildren(h("option", {value: ""}, t("existing_artifact")));
    for (const {revision: r} of page.artifacts) if (r?.state === "ready" &&
      ![...existing.options].some(o => o.value === `${r.artifact_id}:${r.revision}`))
      existing.append(h("option", {value: `${r.artifact_id}:${r.revision}`}, `${r.display_name} · r${r.revision} · ${r.artifact_id.slice(-8)}`));
    if (selected && ![...existing.options].some(o => o.value === selected)) existing.append(selectedOption);
    existing.value = selected;
    catalogCursor = page.next_cursor; more.hidden = !catalogCursor;
  };
  const settleSubmission = op => {
    guard(true);
    if (!saved.submission || !TERMINAL.includes(op.status)) return;
    const unchanged = saved.submission.snapshot === snapshot();
    delete saved.submission;
    if (op.status === "succeeded" && unchanged) {text.value = ""; saved.attachments = []; saved.fields = undefined; removeStored(); render();}
    else persist();
  };
  const perform = async (action, target, params, preconditions, requestScope) => {
    guard(true);
    const request = {action, target, params, preconditions};
    if (saved.submission?.refused && JSON.stringify(saved.submission.request) !== JSON.stringify(request)) delete saved.submission;
    if (!saved.submission) {
      saved.submission = {request: structuredClone({action, target, params, preconditions}),
        key: crypto.randomUUID(), snapshot: snapshot(), scope: requestScope}; persist();
    }
    const intent = saved.submission;
    try {
      persist(true); // freeze request/key on disk before any central submission
      const op = intent.operation_id ? (await api("GET", `/operations/${intent.operation_id}`)).operation
        : (await api("POST", "/operations?wait=3", intent.request, intent.key)).operation;
      guard(true); intent.operation_id = op.operation_id; persist(); settleSubmission(op); return op;
    } catch (error) {
      if (connection.epoch === state.epoch && connection.generation === generation && error.status >= 400 && error.status < 500 && error.code !== "IDEMPOTENCY_CONFLICT") {
        intent.refused = true; persist();
      }
      throw error;
    }
  };
  const refresh = async () => {
    guard(true);
    const pending = saved.attachments.filter(a => !a.ref && a.operation_id);
    await settleRefreshes([
      ...(native ? [native.refresh()] : []),
      ...pending.map(async a => {const {operation} = await api("GET", `/operations/${a.operation_id}`); guard(true); acceptUpload(a, operation);}),
      ...(saved.submission?.operation_id ? [(async () => {
        const {operation} = await api("GET", `/operations/${saved.submission.operation_id}`); guard(true); settleSubmission(operation);
      })()] : []),
    ]);
  };
  if (supported) {
    const unsub = onEvents(ev => {
      if (!box.isConnected || connection.epoch !== state.epoch) {unsub(); return;}
      if (ev.resource_type === "artifact") return settleRefreshes([refresh(), loadCatalog()]);
      if (ev.resource_id === saved.submission?.operation_id || saved.attachments.some(a => a.operation_id === ev.resource_id)) return refresh();
    });
    // Start after the containing form has been mounted. These reads also recover known upload receipts.
    queueMicrotask(() => settleRefreshes([loadCatalog(), refresh()]).catch(e => {
      if (box.isConnected && connection.epoch === state.epoch) fill(status, errorBox(e, {within:status}));
    }));
  }
  const bindFields = fields => {
    for (const [name, field] of Object.entries(fields)) {
      if (typeof saved.fields?.[name] === "string") field.value = saved.fields[name];
      field.addEventListener("input", () => {
        guard(); saved.fields = Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, value.value])); persist();
      });
    }
  };
  render();
  return {box, refs, ready, perform, bindFields, pending: () => Boolean(saved.submission), reset: () => {
    guard(true);
    if (!ready()) throw new Error(t("attachments_not_ready"));
    const ignored = [...(saved.native_ignored || []), ...saved.attachments.map(a => a.native_handle).filter(Boolean)];
    saved = {text: "", attachments: [], native_draft: saved.native_draft, native_ignored: ignored};
    text.value = ""; files.clear(); previews.clear(); persist(); render();
  }};
}

// ------------------------------------------------------------------ one shared bounded event reader
function onEvents(fn) { state.listeners.add(fn); return () => state.listeners.delete(fn); }
const onlineListeners = new Set();
function onOnline(fn) { onlineListeners.add(fn); return () => onlineListeners.delete(fn); }
function updateOnline(value) {
  state.online = value;
  if (!value) shell?.pending({});
  for (const fn of onlineListeners) fn();
}
// Transport health is independent of view readiness and mutation gates.
const connectionPainters = new Set();
let connectionPaintFlags = [false, false];
function paintConnection(waiting = false, paused = false) {
  connectionPaintFlags = [waiting, paused];
  const live = document.getElementById('live');
  const projection = healthDisplay(state);
  const {key, tone, connection: status, recoveryKey, freshness} = projection.connection;
  const label = healthLabel(projection.connection, t, {age: false});
  live.dataset.connection = status;
  live.dataset.freshness = freshness;
  live.className = 'live tone-' + (['stale', 'unknown'].includes(freshness) ? 'neutral' : tone);
  let labelNode = live.querySelector('.central-service-state');
  if (!labelNode) {labelNode = h('span', {class: 'central-service-state'}); live.append(labelNode);}
  if (labelNode.textContent !== label) labelNode.textContent = label;
  const scoped = t('ui_connector_status', {status: label});
  live.setAttribute('aria-label', scoped);
  const central = document.getElementById('central-service-status');
  central.title = scoped;
  central.querySelector('.central-service-scope').textContent = t('ui_connector_scope');
  let notice = document.getElementById('managed-recovery-notice');
  if (state.managedRecovery && !notice) {
    notice = h('aside', {id: 'managed-recovery-notice', class: 'panel', role: 'status'}, h('p'),
      h('details', {id: 'managed-recovery-details'}, h('summary', {}, t('connection_details')), h('code')));
    live.closest('header').after(notice);
  }
  if (notice) {
    notice.hidden = !state.managedRecovery;
    notice.querySelector('p').textContent = recoveryKey ? t(recoveryKey + '_help') : '';
    notice.querySelector('summary').textContent = t('connection_details');
    notice.querySelector('code').textContent = state.managedRecovery || '';
  }
  const helpKey = recoveryKey ? recoveryKey + '_help'
    : key === 'ui_not_connected' ? 'ui_not_connected_help'
    : key === 'ui_connecting' ? 'ui_connecting_help'
    : key === 'ui_reconnecting' ? 'offline_actions_paused'
    : key === 'ui_connection_refused' ? 'error_code_CLIENT_ACTION_REFUSED'
    : key === 'ui_service_error' ? 'error_code_CLIENT_ACTION_SERVICE_ERROR'
    : key === 'ui_invalid_response' ? 'error_code_CLIENT_ACTION_INVALID_RESPONSE'
    : paused ? 'sync_actions_paused' : waiting ? 'sync_waiting' : 'desktop_polling';
  live.title = scoped + ' — ' + t(helpKey);
  paintHealthSummary(projection);
  for (const paint of [...connectionPainters]) paint();
  paintVersions();
}
function healthSummaryContent(projection) {
  const summary = projection.summary;
  const pair = (label, value) => t('health_pair', {label: t(label), value});
  const join = values => values.reduce((left, right) => t('health_join', {left, right}));
  const last = summary?.facts.filter(row => row.activity.freshness === 'stale')
    .sort((a, b) => (b.activity.ageSeconds || 0) - (a.activity.ageSeconds || 0))[0];
  const refresh = projection.capabilities.refresh;
  return [summary ? h('span', {'data-health-key': 'selected', 'data-health-selected': '', class: 'health-selected'},
    h('span', {}, pair(summary.selected.workspaceId ? 'health_selected_workspace' : 'health_selected_work', summary.selected.label || summary.selected.host)),
    h('span', {class: 'ui-pill', 'data-health-compact': summary.compact}, t('health_' + summary.compact)),
    summary.connection ? h('span', {class: 'health-installation-connection'}, healthLabel({...summary.connection,
      key: summary.connection.value === 'connected' ? 'ui_connected' : summary.connection.value === 'not_connected' ? 'health_connection_problem' : 'health_unknown'}, t)) : null,
    h('span', {class: 'health-facets'}, join([
      pair('health_attention_label', t(summary.attention ? 'health_attention' : 'health_none')),
      pair('health_activity', t('health_' + summary.activity)),
      pair('health_freshness', t(summary.stale ? 'health_stale' : summary.unknown ? 'health_unknown' : 'health_current'))])),
    last ? h('span', {class: 'health-last-value'}, healthLabel({...last.activity,
      key: ['streaming', 'starting'].includes(last.activity.value) ? 'health_working'
        : last.activity.value === 'not_streaming' ? 'health_idle' : 'health_unknown'}, t)) : null) : null,
    projection.connection.freshness === 'stale' ? h('span', {'data-health-key': 'connection-age', 'data-health-age': '', 'aria-live': 'off'},
      healthAge(projection.connection.ageSeconds, t)) : null,
    projection.capabilities.freshness === 'invalidated' ? h('span', {'data-health-key': 'capabilities'},
      t(refresh?.state === 'loading' ? 'health_capabilities_checking' : 'health_capabilities_reload', {reason: refresh?.reason || 'IDENTITY_UNVERIFIED'})) : null,
    projection.capabilities.freshness === 'invalidated' && refresh?.state !== 'loading'
      ? h('button', {'data-health-key': 'reload', onclick: () => location.reload()}, t('health_reload')) : null,
    projection.otherAttention ? h(projection.other.href ? 'a' : 'span', {href: projection.other.href, 'data-health-key': 'other', 'data-health-other': ''},
      healthLabel({...projection.other, key: projection.otherAttention === 1 ? 'health_other_machines_one' : 'health_other_machines_many'},
        (key, vars) => t(key, {count: projection.otherAttention, ...vars}))) : null].filter(Boolean);
}
function renderHealthContent(container, projection) {
  const content = healthSummaryContent(projection), signature = content.map(node => node.outerHTML).join('');
  if (container.dataset.signature === signature) return;
  const focused = container.contains(document.activeElement) ? document.activeElement.dataset.healthKey : null;
  const existing = new Map([...container.children].map(node => [node.dataset.healthKey, node]));
  const next = content.map(node => {
    const previous = existing.get(node.dataset.healthKey);
    // Keep interactive nodes attached: age updates must never re-focus a link.
    if (previous && previous.tagName === node.tagName && ['A', 'BUTTON'].includes(node.tagName)) {
      if (previous.textContent !== node.textContent) previous.textContent = node.textContent;
      if (node.hasAttribute('href')) previous.setAttribute('href', node.getAttribute('href'));
      return previous;
    }
    return previous?.outerHTML === node.outerHTML ? previous : node;
  });
  for (const child of [...container.children]) if (!next.includes(child)) child.remove();
  next.forEach((node, i) => {if (container.children[i] !== node) container.insertBefore(node, container.children[i] || null);});
  container.dataset.signature = signature;
  if (focused && !container.contains(document.activeElement)) container.querySelector(`[data-health-key="${focused}"]`)?.focus({preventScroll: true});
}
let healthResize;
function paintHealthSummary(projection) {
  let summary = document.getElementById('installation-health');
  if (!summary) {
    summary = h('div', {id: 'installation-health', class: 'health-summary', role: 'group'});
    document.querySelector('header.top').after(summary);
    healthResize = new ResizeObserver(() => {
      const height = document.querySelector('header.top').getBoundingClientRect().height + summary.getBoundingClientRect().height;
      document.documentElement.style.setProperty('--shell-header-height', `${height}px`);
    });
    healthResize.observe(summary); healthResize.observe(document.querySelector('header.top'));
  }
  summary.setAttribute('aria-label', t('health_summary'));
  renderHealthContent(summary, projection); summary.hidden = !summary.children.length;
}
let healthRenewal = null;
async function refreshHealthCapabilities() {
  const health = state.health, epoch = state.epoch;
  if (!health || healthDisplay(state).capabilities.freshness !== 'invalidated') return;
  const revision = healthRevision(health.currentCaps);
  if (health.capabilityRefresh?.revision === revision) return;
  const refresh = health.capabilityRefresh = {revision, state: 'loading', startedAt: Date.now()};
  try {
    if (!state.scopeIdentity || state.nativeBusy) throw {code: 'IDENTITY_UNVERIFIED'};
    const bootstrap = await api('GET', '/bootstrap');
    if (epoch !== state.epoch || state.health !== health) return;
    validateHealthBootstrap(state.caps, bootstrap);
    if (bootstrap.sync.server_id !== state.scopeIdentity.server_id || bootstrap.sync.principal_id !== state.scopeIdentity.principal_id)
      throw {code: 'CONNECTION_CHANGED'};
    await activate(bootstrap.capabilities, state.endpoint, false, state.scopeIdentity.configurationSource, true, bootstrap);
  } catch (error) {
    refresh.revision = healthRevision(health.currentCaps);
    refresh.state = 'failed'; refresh.reason = error.code || 'BOOTSTRAP_VERIFICATION_FAILED';
  }
}
function tickHealth() {
  if (document.hidden) return;
  document.dispatchEvent(new Event('health-display-tick'));
  const verification = state.health?.capabilityRefresh;
  if (verification?.state === 'loading' && Date.now() - verification.startedAt >= 60_000) {
    verification.state = 'failed'; verification.reason = 'VERIFICATION_PENDING';
  }
  paintConnection(...connectionPaintFlags);
  void refreshHealthCapabilities();
  if (healthRenewal || !state.health || !state.token) return;
  const path = takeHealthRenewal(state.health);
  const read = state.health.readers?.get(path);
  if (read) healthRenewal = read(state.health.renewalDeadline).catch(() => {}).finally(() => {healthRenewal = null; paintConnection(...connectionPaintFlags);});
}
function rememberFleetHealth(snapshot) {
  // Native Fleet status belongs to its own configuration, not a Connector token.
  state.health ||= {sessions: [], hosts: []};
  state.health.fleet = snapshot;
  paintConnection(...connectionPaintFlags);
}
let managedStatusAt = 0;
async function refreshManagedRecovery() {
  if (!nativeDesktop || Date.now() - managedStatusAt < 3000) return;
  managedStatusAt = Date.now();
  const epoch = state.epoch;
  try {
    const status = await nativeStatus();
    if (epoch !== state.epoch) return;
    state.managedRecovery = status.managed_recovery || null;
    state.managedObservedAt = Date.now();
    if (status.configuration_source === 'managed' && status.endpoint) state.endpoint = status.endpoint;
    paintConnection(...connectionPaintFlags);
    if (status.configuration_source === 'managed' && Number.isSafeInteger(status.managed_rebind)
      && status.managed_rebind > (state.managedRebind || 0)) {
      state.managedRebind = status.managed_rebind;
      if (state.nativeBusy) return;
      if (!state.token) {
        if (status.connected) await nativeTransition('recover');
        return;
      }
      // A restart need not create an event. Recover navigation reads without
      // remounting an active form or inventing a server event/checkpoint.
      await Promise.allSettled([workspaceNav?.refresh(), shell?.refresh()]);
      if (epoch !== state.epoch) return;
      if (!state.viewReady || document.getElementById('main').querySelector('[data-api-error]')) {
        if (editing || typing()) idleReload = route;
        else await route();
      }
    }
  } catch { /* Existing transport health remains authoritative if native IPC fails. */ }
}
let foregroundVersion = 0, wakeEventPoll = null;
let foregroundReads = new AbortController();
function waitForEventPoll(ms) {
  return new Promise(resolve => {
    const finish = () => {clearTimeout(timer); wakeEventPoll = null; resolve();};
    const timer = setTimeout(finish, ms);
    wakeEventPoll = finish;
  });
}
function foregroundChanged() {
  // Invalidate an in-flight page as well as the sleeping poll. Only the existing
  // reader can re-enable writes, after a post-resume page and its sources settle.
  foregroundVersion++;
  // Hidden pages do not poll. Retire only on return, including bfcache restore.
  if (document.visibilityState !== 'hidden') {
    const previousReads = foregroundReads;
    foregroundReads = new AbortController();
    previousReads.abort();
  }
  if (state.token) {updateOnline(false); paintConnection(true, true);}
  wakeEventPoll?.();
}
// For checkpointed polls, dashboard_sync.check proves server/principal identity
// before these continuity failures. All other or unknown reasons withdraw the
// whole view immediately; they cannot retain facts or an editing view.
const IDENTITY_PRESERVING_RESETS = new Set(['cursor_expired', 'cursor_ahead', 'history_changed', 'checkpoint_cursor_mismatch']);
async function streamEvents() {
  for (;;) {
    await refreshManagedRecovery();
    if (!nativeDesktop && state.token && state.token !== browserSessionToken) {
      const token = state.token, epoch = state.epoch;
      const refusal = browserCredentialRefusals.read(token);
      if (token !== state.token || epoch !== state.epoch) continue;
      if (refusal) {
        state.connectionError = refusal; state.pollHealth = pollFailure(refusal, Date.now());
        updateOnline(false); paintConnection();
        await waitForEventPoll(1000); continue;
      }
    }
    if (state.nativeBusy || !state.token || !state.namespace || !state.viewReady || document.visibilityState === 'hidden') {
      paintConnection(true);
      await waitForEventPoll(1000); continue;
    }
    const epoch = state.epoch, view = generation, foreground = foregroundVersion, signal = foregroundReads.signal;
    let cycle, pollSucceeded = false;
    const previousPoll = state.lastPoll;
    try {
      const before = state.lastEvent;
      const checkpoint = state.sync ? `&checkpoint=${encodeURIComponent(state.sync.token)}` : "";
      const page = await api("GET", `/events?after=${before}&limit=100${checkpoint}`);
      if (epoch !== state.epoch || foreground !== foregroundVersion) continue;
      pollSucceeded = true; state.pollHealth = 'connected';
      if (state.connectionError?.credentialRefused) state.connectionError = null;
      state.lastPoll = {at: Date.now(), namespace: state.namespace}; paintConnection(true);
      if (view !== generation || !state.viewReady) continue;
      cycle = {error: null}; state.refreshCycle = cycle;
      // A deferred render is not a connection failure: keep version-checked form saves usable.
      // Actual event/read failures below pause mutations until a successful replay.
      const cursor = await retiredRead(consumePageAsync(page, before, ev => {
        return settleRefreshes([...state.listeners].map(fn => Promise.resolve().then(() => {
          if (signal.aborted) throw retiredReadError();
          return fn(ev);
        })))
          .catch(error => {cycle.error ||= error; throw error;});
      }), signal);
      if (epoch !== state.epoch || view !== generation || foreground !== foregroundVersion || !state.viewReady) continue;
      if (cycle.error) throw cycle.error;
      if (state.sync) {
        if (page.sync?.checkpoint?.cursor !== cursor || !page.sync?.checkpoint?.token)
          throw new Error("Invalid central event checkpoint");
        state.sync = page.sync.checkpoint;
      }
      if (cursor !== state.lastEvent) state.lastEventAt = Date.now();
      state.lastEvent = cursor;
      saveCursor();
      state.refreshCycle = null;
      updateOnline(true);
      paintConnection();
      if (!page.has_more || cursor <= before) await waitForEventPoll(1000);
    } catch (error) {
      if (epoch !== state.epoch || foreground !== foregroundVersion) continue;
      // A successful HTTP page followed by a failed view refresh still proves
      // transport health. Invalid event/checkpoint data is a poll response error.
      if (!pollSucceeded || !cycle?.error) {
        if (pollSucceeded) state.lastPoll = previousPoll;
        state.pollHealth = pollFailure(error, Date.now()); paintConnection();
      }
      if (view !== generation) continue;
      if (state.refreshCycle === cycle) state.refreshCycle = null;
      updateOnline(false);
      paintConnection(false, pollSucceeded);
      if (error.code === "EVENT_CURSOR_RESET") {
        const previous = { namespace: state.namespace, sync: state.sync };
        const accessChanged = !IDENTITY_PRESERVING_RESETS.has(error.reason);
        if (accessChanged) {
          state.health = null;
          state.lastPoll = null; state.pollHealth = 'initial';
          paintConnection(true);
          clearEarlierDrafts();
          // A changed access boundary invalidates displayed facts immediately. Drafts stay
          // in their original principal namespace, even when bootstrap is unavailable.
          generation++; state.viewReady = false;
          teardown?.(); teardown = null; freshPage();
          shell?.dispose(); shell = null; workspaceSidebar?.dispose(); workspaceSidebar = null;
          workspaceNav?.dispose(); workspaceNav = null; workspaceIdentity = "";
          document.getElementById("main").replaceChildren(h("p", {class: "muted"}, t("sync_waiting")));
        }
        try {
          await activate(state.caps, state.endpoint, true, state.scopeIdentity?.configurationSource, !accessChanged && (editing || typing()));
          // Keep open forms and their same-identity guards intact. The event
          // reader must remain ready to persist the refreshed checkpoint.
          if (!accessChanged && state.namespace === previous.namespace && (editing || typing())) idleReload = route;
          else await route();
        } catch { state.sync = previous.sync; state.viewReady = true; /* retry continuity proof */ }
      }
      await waitForEventPoll(3000);
    } finally {
      if (state.refreshCycle === cycle) state.refreshCycle = null;
    }
  }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
function debounce(fn, ms) { let id; return () => { clearTimeout(id); id = setTimeout(fn, ms); }; }
function assertView(connection) {
  assertConnection(connection);
  if (connection.generation !== generation) throw new ApiError(0, "VIEW_CHANGED", "View changed during refresh");
}
function debounceRefresh(fn, ms) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  let id, waiting = [];
  return () => new Promise((resolve, reject) => {
    waiting.push({resolve, reject, signal: foregroundReads.signal}); clearTimeout(id);
    id = setTimeout(async () => {
      const batch = waiting; waiting = [];
      const current = batch.filter(p => !p.signal.aborted);
      for (const p of batch) if (p.signal.aborted) p.reject(retiredReadError());
      if (!current.length) return;
      try { assertView(connection); await fn(); assertView(connection); current.forEach(p => p.resolve()); }
      catch (error) { current.forEach(p => p.reject(error)); }
    }, ms);
  });
}

// Connection-scoped resource records share stable IDs and explicit dependencies with event dispatch.
function rememberObservation(type, id, value, dependencies = []) {
  state.observations ||= new Map();
  state.observations.set(`${type}:${id}`, {value, dependencies: new Set(dependencies)});
}
function observationAffected(type, id, event) {
  const key = `${event.resource_type}:${event.resource_id}`;
  return key === `${type}:${id}` || state.observations?.get(`${type}:${id}`)?.dependencies.has(key);
}
function itemDependencies(data) {
  return [`project:${data.project.project_id}`, ...[...(data.path || []), ...(data.children || []), ...(data.derived || [])].map(item => `work_item:${item.work_item_id}`), ...(data.links || []).flatMap(link => {
    const related = [`${link.kind}:${link.ref}`];
    if (link.kind === "task") related.push(`execution:${link.ref}`);
    if (link.target?.session) related.push(`session:${link.target.session.host}/${link.target.session.session_id}`);
    return related;
  })];
}

// ------------------------------------------------------------------ shared pieces
function confinementLabel(s) {
  const level = s.confinement?.level || "none";
  return t(level === "os_sandbox" && s.confinement?.verification?.status !== "verified"
    ? "confinement_os_pending" : "confinement_" + level);
}
function confinementNote(host, agent) {
  const note = h("p", { class: "muted", "data-confinement-note": "" });
  const update = () => {
    const account = state.caps?.hosts?.find(x => x.host === host)?.confinement?.host_account;
    const effect = account?.start_effect;
    if (effect === "refused") {
      note.textContent = t("confinement_account_blocked", { reason: account.reason });
      if (agent.value === "codex") note.textContent += " " + t("confinement_codex_note");
    } else if (agent.value === "codex") {
      note.textContent = t("confinement_codex_note");
    } else if (effect === "verified") {
      note.textContent = t("confinement_account_note");
    } else if (effect === "recheck") {
      note.textContent = t("confinement_account_recheck");
    } else {
      note.textContent = (effect === "fallback_default" && account?.declared
        ? t("confinement_account_fallback", { reason: account.reason }) + " " : "") + t("confinement_claude_note");
    }
  };
  agent.addEventListener("change", update);
  note.setHost = value => { host = value; update(); };
  update();
  return note;
}
function confinementDetails(s) {
  const record = s.confinement;
  const current = s.current_verification;
  return h("details", {}, h("summary", {}, t("confinement_evidence")),
    h("p", { class: "muted" }, t("confined_note")),
    h("dl", { class: "kv" },
      h("dt", {}, t("confinement_creation")), h("dd", {}, confinementLabel(s)),
      h("dt", {}, t("confinement_current")), h("dd", {}, t("confinement_status_" + (current?.status || "unknown"))),
      h("dt", {}, t("confinement_options")), h("dd", {}, h("code", {}, JSON.stringify(record?.options || {}))),
      h("dt", {}, t("confinement_evidence")), h("dd", {}, h("code", {}, JSON.stringify(record?.evidence || {}))),
      h("dt", {}, t("confinement_gap")), h("dd", {}, record?.gap ? t("confinement_gap_" + record.gap) : t("none")),
      h("dt", {}, t("confinement_current")), h("dd", {}, h("code", {}, JSON.stringify(current || { status: "unknown" })))));
}

function sessionBadges(s) {
  return [
    chip(s.host),
    chip(confinementLabel(s), s.confinement?.level === "none" ? "readonly" : "info"),
    s.confinement?.level && s.confinement.level !== "none" && ["unknown", "mismatch"].includes(s.current_verification?.status)
      ? chip(t("confinement_current_" + s.current_verification.status), "stale") : null,
    s.api_access === "managed" ? chip(t("managed"), "managed") : chip(t("read_only"), "readonly"),
    s.stale ? chip(`${t("stale")} · ${t("stale_reason_" + s.stale_reason)}`, "stale") : null,
    s.pending ? chip(t("pending_" + s.pending.kind), "stale") : null,
  ];
}
function sessionQuestions(pending) {
  if (!Array.isArray(pending?.questions)) return [];
  return pending.questions.flatMap(item => {
    const text = typeof item === "string" ? item : item?.question;
    return typeof text === "string" && text.trim() ? [text.trim()] : [];
  });
}
function sessionRow(s, open) {
  const title = s.title || s.session_id;
  const href = `#/session/${encodeURIComponent(s.host)}/${encodeURIComponent(s.session_id)}`;
  const key = JSON.stringify([s.host, s.session_id]);
  const managed = s.api_access === "managed";
  const asking = managed && s.pending?.kind === "ask_user";
  const permission = managed && s.pending?.kind === "permission";
  const meta = joinLabel([s.host, cliName(s.agent_kind), s.worktree_branch,
    s.pending?.kind ? t("pending_" + s.pending.kind) : "", s.stale ? reasonText(s.stale_reason) : ""]);
  const pill = !managed ? statusPill("read_only") : statusPill(asking || permission ? "waiting" : s.stale ? "stale" : "unknown");
  const primary = () => h("a", {class: "ui-button secondary ui-sm", href}, t(asking ? "global_answer" : permission ? "global_review" : "global_open"));
  return inboxRow({
    h, key, title, href, meta, pill,
    action: primary(),
    detailsLabel: t("global_details_for", {title}),
    onOpen: () => open?.(key, title, [
      ...sessionQuestions(s.pending).map(text => h("p", {}, text)),
      primary(),
      !managed ? statusPill("read_only") : statusPill(asking || permission ? "waiting" : s.stale ? "stale" : "unknown"),
      meta ? h("p", {}, meta) : null,
      disclosure({label: t("global_details"), body: h("dl", {class: "kv"},
        h("dt", {}, t("sessions_id")), h("dd", {}, h("code", {}, s.session_id || "—")),
        h("dt", {}, t("host")), h("dd", {}, s.host || "—"),
        s.pending?.kind ? [h("dt", {}, t("global_state")), h("dd", {}, t("pending_" + s.pending.kind))] : null,
        s.stale_reason ? [h("dt", {}, t("global_code")), h("dd", {}, reasonText(s.stale_reason), " ", h("code", {}, String(s.stale_reason)))] : null)}),
    ]),
  });
}
const epoch = x => (x ? new Date(x * 1000).toISOString() : "");
function opStatus(op) {
  const started = op.status === "succeeded" && ["session.start", "repository.continue"].includes(op.action);
  return h("span", { class: `status-${op.status}` }, t(started ? "start_dispatched" : "op_" + op.status));
}
function opRow(op, open) {
  const title = actionLabel(op.action);
  const href = `#/op/${encodeURIComponent(op.operation_id)}`;
  const key = op.operation_id;
  const words = operationMeta(op);
  const meta = h('span', {}, words, op.created_at ? [words ? ' · ' : '', timeStamp(op.created_at)] : null);
  const needs = ["needs_attention", "uncertain", "failed"].includes(op.status);
  const precise = t("op_" + op.status);
  return inboxRow({
    h, key, title, href, meta,
    pill: statusPill(operationStatusKey(op.status)),
    action: h("a", {class: "ui-button secondary ui-sm", href}, t(needs ? "global_review" : "global_open")),
    detailsLabel: t("global_details_for", {title}),
    onOpen: () => open?.(key, title, [
      precise !== "op_" + op.status ? h("p", {}, precise) : null,
      statusPill(operationStatusKey(op.status)),
      meta ? h("p", {class: "muted"}, meta) : null,
      disclosure({label: t("global_details"), body: h("dl", {class: "kv"},
        h("dt", {}, t("global_action_id")), h("dd", {}, h("code", {}, op.action || "—")),
        h("dt", {}, t("global_operation_id")), h("dd", {}, h("code", {}, op.operation_id || "—")),
        h("dt", {}, t("global_col_target")), h("dd", {}, h("code", {}, JSON.stringify(op.target ?? null))),
        op.status_reason ? [h("dt", {}, t("reason")), h("dd", {}, op.status_reason)] : null,
        op.error_code ? [h("dt", {}, t("global_code")), h("dd", {}, reasonText(op.error_code), " ", h("code", {}, String(op.error_code)))] : null)}),
    ]),
  });
}

// ------------------------------------------------------------------ views
async function viewHome(main) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  return attentionView({main, h, t, api, onPending: value => shell?.pending(value), caps: state.caps, storageKey: `batc.attention.${connection.namespace}`,
    guard: () => assertView(connection), route, onEvents, debounceRefresh, errorBox, sessionRow, workItemRow, opRow});
}

async function viewSessions(main) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const storageKey = `batc.sessions.${connection.namespace}`;
  const q = new URLSearchParams(sessionStorage.getItem(storageKey) || "");
  const hostSel = h("select", {"aria-label": t("host")}, h("option", { value: "" }, t("all_hosts")));
  const accessSel = h("select", {"aria-label": t("all_access")}, h("option", { value: "" }, t("all_access")),
    h("option", { value: "managed" }, t("only_managed")), h("option", { value: "read_only" }, t("only_read_only")));
  const search = h("input", {type: "search", "aria-label": t("sessions_search"), placeholder: t("sessions_search")});
  search.value = q.get("search") || "";
  let selected = q.get("workspace") || "", cursor = null, pages = 1, serial = Promise.resolve(), revision = 0;
  const rows = new Map(), expanded = new Set();
  const list = h("div", {class: "session-inventory"}), status = h("div", {}), count = h("p", {class: "muted", "aria-live": "polite"});
  const more = h("button", {class: "secondary", hidden: true}, t("load_more"));
  const navigation = h("nav", {"aria-label": t("sessions_workspaces"), class: "session-workspaces"});
  const scopeNote = h("p", {class: "muted"}, t("sessions_scope_note"));
  const pageNote = h("span", {class: "muted"}, t("sessions_page_note"));
  const scope = h("details", {class: "panel session-scope", open: matchMedia("(min-width: 901px)").matches},
    h("summary", {}, t("sessions_workspaces")), scopeNote, navigation,
    h("a", {class: "session-project-link", href: "#/projects"}, t("sessions_projects")));
  const pathsIntro = h("p", {class: "ui-meta", "data-session-paths": "", hidden: true}, t("paths_first"));
  main.append(h("div", {class: "session-heading"}, pageHeader({title: t("sessions_title"), meta: pathsIntro}),
    mayStartSession(state.caps) ? h("a", {href: "#/start", class: "ui-button primary"}, t("paths_start")) : null,
    state.caps?.features?.repository_sync?.length ? h("a", {href: "#/published"}, t("pub_title")) : null,
    state.caps?.actions?.some(a => Object.values(orchestrationActions).includes(a.action)) ? h("a", {href: "#/orchestrate/relay"}, t("orch_title")) : null),
    disclosure({label: t("global_details"), body: h("p", {}, t("sessions_intro"))}),
    h("div", {class: "filters session-filters"}, searchField(search), hostSel, accessSel,
      state.caps?.actions?.some(a => a.action === "session.approve_pending") ? h("a", {href: "#/approvals"}, t("bulk_title")) : null), status,
    h("div", {class: "session-layout"}, scope, h("section", {"aria-label": t("sessions_title"), class: "session-results"}, count, list,
      h("div", {class: "session-pagination"}, more, pageNote))));
  try {
    const hosts = (await api("GET", "/hosts")).hosts; assertView(connection);
    for (const x of hosts) hostSel.append(h("option", {value: x.host}, x.host));
  } catch (e) { status.replaceChildren(errorBox(e, {within:status})); return; }
  hostSel.value = q.get("host") || ""; accessSel.value = q.get("access") || "";
  const save = () => {
    assertView(connection);
    const values = new URLSearchParams({host: hostSel.value, access: accessSel.value, search: search.value, workspace: selected});
    sessionStorage.setItem(storageKey, values.toString());
  };
  const row = session => {
    const id = `${session.host}/${session.session_id}`, activity = sessionActivity(session), pill = sessionPill(session);
    const origin = session.provenance === "manual" ? "sessions_manual" : session.provenance === "connector_managed"
      ? "sessions_connector" : "sessions_origin_unknown";
    const access = session.api_access === "managed" ? "managed" : session.api_access === "read_only" ? "read_only" : "sessions_access_unknown";
    const outOfSync = session.gone_at || ["gone", "missing"].includes(session.state?.enumeration);
    const evidence = () => [
      h("dl", {class: "kv"}, h("dt", {}, t("sessions_label")), h("dd", {}, session.title || session.session_id),
        h("dt", {}, t("sessions_workspace")), h("dd", {}, session.workspace || t("sessions_workspace_unknown")),
        h("dt", {}, t("sessions_workspace_id")), h("dd", {}, h("code", {}, session.workspace_id || t("obs_unknown"))),
        h("dt", {}, t("sessions_id")), h("dd", {}, h("code", {}, session.session_id)),
        h("dt", {}, t("obs_axis_activity")), h("dd", {}, t(activity.key)),
        h("dt", {}, t("activity")), h("dd", {}, observationTime(session.last_activity_at)),
        h("dt", {}, t("observed")), h("dd", {}, observationTime(session.observed_at))),
      h("p", {class: "muted"}, confinementLabel(session)), confinementDetails(session), observationState(session)];
    const details = h("details", {class: "session-row-details", open: expanded.has(id)}, h("summary", {}, t("sessions_details")));
    let mounted = false;
    const mountEvidence = () => {if (!mounted) {details.append(...evidence()); mounted = true;}};
    if (expanded.has(id)) mountEvidence();
    details.addEventListener("toggle", () => {
      if (!details.isConnected) return;
      if (details.open) {expanded.add(id); mountEvidence();} else expanded.delete(id);
    });
    return h("article", {class: "session-entry", "data-resource-id": id},
      h("div", {class: "session-entry-heading"},
        h("a", {class: "title", title: session.title || session.session_id, href: `#/session/${encodeURIComponent(session.host)}/${encodeURIComponent(session.session_id)}`}, session.title || session.session_id),
        pill ? chip(t(pill.key), pill.tone) : null),
      h("div", {class: "session-entry-meta"},
        h("span", {class: session.api_access === "managed" ? "" : "session-readonly"}, session.provenance === "connector_managed" && session.api_access === "managed" ? t(access) : `${t(origin)} · ${t(access)}`),
        h("span", {}, [cliName(session.agent_kind), session.worktree_branch].filter(Boolean).join(" · "))),
      runtimeStale(session) ? h("p", {class: "session-stale muted"}, t("sessions_stale"), " · ",
        session.stale_reason === "gone" ? t("sessions_not_seen") : session.stale_reason ? t("stale_reason_" + session.stale_reason) : t("sessions_runtime_stale"))
        : outOfSync ? h("p", {class: "session-stale muted"}, t("sessions_not_seen")) : null,
      validLabels(session.connector_metadata?.labels) && session.connector_metadata.labels.length ? h("div", {class: "actions session-entry-meta", "aria-label": t("labels_title")},
        ...session.connector_metadata.labels.slice(0, 2).map(v => chip(v)),
        session.connector_metadata.labels.length > 2 ? h("span", {class: "muted", title: session.connector_metadata.labels.join(" · ")}, `+${session.connector_metadata.labels.length - 2}`) : null) : null,
      details);
  };
  const render = () => {
    assertView(connection);
    const groups = groupedSessions([...rows.values()]);
    const choose = key => {selected = key; save(); render();};
    const workspaceName = group => group.name || group.id || t("sessions_workspace_unknown");
    const navButton = (label, key, n) => h("button", {class: "session-scope-button", "aria-pressed": String(selected === key),
      "data-workspace-key": key, title: label, onclick: () => choose(key)}, h("span", {}, label), h("span", {class: "muted"}, t(cursor ? "sessions_loaded_count" : n === 1 ? "sessions_exact_count_one" : "sessions_exact_count", {count: n})));
    const links = [navButton(t(cursor ? "sessions_all_loaded" : "sessions_all"), "", rows.size)];
    const labels = new Map();
    const labelKey = group => JSON.stringify([group.host, workspaceName(group)]);
    for (const group of groups) labels.set(labelKey(group), (labels.get(labelKey(group)) || 0) + 1);
    let lastHost;
    for (const group of groups) {
      if (group.host !== lastHost) {
        links.push(h("a", {class: "session-host-link", href: `#/host/${encodeURIComponent(group.host)}`, title: t("obs_discovery")}, group.host));
        lastHost = group.host;
      }
      const sameName = labels.get(labelKey(group)) > 1;
      links.push(navButton(workspaceName(group) + (sameName && group.id ? ` · ${group.id}` : ""), group.key, group.sessions.length));
    }
    // Keep an unavailable selection explicit; a refresh must not silently broaden its scope.
    if (selected && !groups.some(group => group.key === selected)) links.push(navButton(t("sessions_scope_missing"), selected, 0));
    const focusedKey = navigation.contains(document.activeElement) ? document.activeElement.dataset.workspaceKey : undefined;
    navigation.replaceChildren(...links);
    if (focusedKey !== undefined) [...navigation.querySelectorAll("button")].find(button => button.dataset.workspaceKey === focusedKey)?.focus({preventScroll: true});
    let visible = 0;
    const sections = groups.filter(group => !selected || selected === group.key).flatMap(group => {
      const sessions = group.sessions.filter(session => matchesSession(session, search.value));
      if (!sessions.length) return [];
      visible += sessions.length;
      return [h("section", {class: "panel session-group"},
        h("header", {class: "session-group-heading"}, h("h2", {title: workspaceName(group)}, workspaceName(group)),
          h("span", {class: "muted"}, group.host),
          group.name && group.id && labels.get(labelKey(group)) > 1 ? h("code", {class: "muted"}, group.id) : null,
          group.name && !group.id ? h("span", {class: "muted"}, t("sessions_workspace_unverified")) : null), ...sessions.map(row))];
    });
    const filtered = Boolean(search.value.trim() || hostSel.value || accessSel.value || selected);
    const empty = rows.size === 0 && !cursor;
    list.replaceChildren(...(sections.length ? sections : [emptyState({title:t(empty && !filtered ? "sessions_empty" : "sessions_no_matches"),
      text: empty && !filtered ? null : t(cursor ? "sessions_more_hint" : "sessions_empty_hint")})]));
    count.hidden = empty; pageNote.hidden = empty;
    pathsIntro.hidden = !empty || filtered;
    scopeNote.textContent = t(cursor ? "sessions_scope_note" : "sessions_scope_exact");
    count.textContent = t(cursor ? "sessions_showing" : "sessions_showing_exact", {shown: visible, loaded: rows.size});
  };
  const read = async (mode, expectedRevision) => {
    if (expectedRevision !== revision) return;
    if (mode === "reset") pathsIntro.hidden = true;
    const p = new URLSearchParams({limit: "50", order: "id", include_gone: "true"});
    if (hostSel.value) p.set("host", hostSel.value);
    if (accessSel.value) p.set("access", accessSel.value);
    save(); more.disabled = true;
    try {
      assertView(connection);
      const before = scrollY, anchor = [...list.querySelectorAll("[data-resource-id]")].find(node => node.getBoundingClientRect().bottom > 110);
      const anchorID = anchor?.dataset.resourceId, offset = anchor?.getBoundingClientRect().top;
      const observed = new Map(mode === "more" ? rows : []);
      let next = mode === "more" ? cursor : null;
      const total = mode === "refresh" ? pages : 1;
      let readPages = 0;
      for (let page = 0; page < total; page++) {
        const query = new URLSearchParams(p); if (next) query.set("cursor", next);
        const result = await api("GET", `/sessions?${query}`); assertView(connection);
        if (expectedRevision !== revision) return;
        for (const session of result.sessions) observed.set(`${session.host}/${session.session_id}`, session);
        readPages++;
        if (result.next_cursor && result.next_cursor === next) throw new Error("Inventory cursor did not advance");
        next = result.next_cursor; if (!next) break;
      }
      rows.clear();
      for (const [id, session] of observed) {rows.set(id, session); rememberObservation("session", id, {session}, [`host:${session.host}`]);}
      cursor = next; more.hidden = !cursor;
      pathsIntro.hidden = rows.size !== 0 || Boolean(cursor);
      pages = mode === "more" ? pages + readPages : readPages;
      const focused = document.activeElement;
      const focusedID = list.contains(focused) ? focused.closest("[data-resource-id]")?.dataset.resourceId : null;
      const focusedPart = focused?.tagName === "SUMMARY" ? "summary" : focused?.classList.contains("title") ? "a.title" : null;
      render(); status.replaceChildren();
      if (focusedID && focusedPart && document.activeElement === document.body) {
        [...list.querySelectorAll("[data-resource-id]")].find(node => node.dataset.resourceId === focusedID)
          ?.querySelector(focusedPart)?.focus({preventScroll: true});
      }
      const current = [...list.querySelectorAll("[data-resource-id]")].find(node => node.dataset.resourceId === anchorID);
      if (mode === "refresh" && current && Math.abs(scrollY - before) < 1) scrollBy(0, current.getBoundingClientRect().top - offset);
    } catch (e) { pathsIntro.hidden = true; status.replaceChildren(errorBox(e, {within:status})); }
    finally {more.disabled = false;}
  };
  const load = async mode => {
    const expected = revision;
    serial = serial.then(() => read(mode, expected));
    // An event cannot acknowledge an obsolete read while a newer filter read is queued.
    let pending;
    do {pending = serial; await pending;} while (pending !== serial);
  };
  hostSel.onchange = accessSel.onchange = () => {revision++; selected = ""; save(); return load("reset");};
  search.oninput = () => {save(); render();};
  more.onclick = () => load("more");
  await load("reset");
  const reload = debounceRefresh(() => load("refresh"), 500);
  return onEvents(ev => {if (["session", "host", "execution", "task"].includes(ev.resource_type)) return reload();});
}

const observationTime = value => value === null || value === undefined || value === "" ? t("obs_unknown")
  : when(typeof value === "number" ? new Date(value * 1000).toISOString() : value);
function observationLink(type, id) {
  if (!id) return null;
  let href;
  if (type === "session") {
    const [host, ...sid] = id.split("/");
    if (host && sid.length) href = `#/session/${encodeURIComponent(host)}/${encodeURIComponent(sid.join("/"))}`;
  } else if (type === "execution" || type === "task") href = `#/task/${encodeURIComponent(id)}`;
  else if (type === "worktree") href = `#/worktree/${encodeURIComponent(id)}`;
  else if (type === "operation") href = `#/op/${encodeURIComponent(id)}`;
  return href ? h("a", {href}, id) : h("code", {}, id);
}
function observationState(row) {
  const axes = ["connection", "loading", "tab", "activity", "lifecycle", "enumeration", "freshness"];
  return h("details", {class: "observation-evidence"}, h("summary", {}, t("obs_state_evidence")),
    h("p", {class: "muted"}, t("obs_lifecycle_note")),
    h("dl", {class: "kv"}, ...axes.flatMap(axis => {
      const evidence = row.state?.evidence?.[axis];
      const raw = row.state?.[axis] || "unknown";
      const label = axis === "activity" && raw === "unknown"
        ? t(sessionActivity(row).key)
        : t("obs_value_" + raw);
      return [h("dt", {}, t("obs_axis_" + axis)), h("dd", {}, label,
        evidence?.stale ? [" · ", chip(t("stale"), "stale")] : null,
        h("div", {class: "muted"}, observationTime(evidence?.observed_at), " · ", evidence?.source_ref || t("obs_unknown")))];
    })));
}
function discoveryEvidence(scopes) {
  return h("div", {}, ...(scopes?.length ? scopes.map(scope => h("section", {class: "observation-evidence"},
    h("h3", {}, scope.profile_id || t("obs_unknown")),
    h("dl", {class: "kv"},
      h("dt", {}, t("obs_scan_status")), h("dd", {}, scope.status || t("obs_unknown")),
      h("dt", {}, t("obs_last_success")), h("dd", {}, observationTime(scope.last_success_at)),
      h("dt", {}, t("obs_last_attempt")), h("dd", {}, observationTime(scope.finished_at)),
      h("dt", {}, t("obs_authority")), h("dd", {}, scope.authority?.kind || t("obs_unknown"), " · ",
        t(scope.authority?.verified ? "obs_verified" : "obs_unverified"))),
    scope.error_code ? h("p", {class: "error"}, scope.error_code) : null,
    h("details", {}, h("summary", {}, t("obs_scan_coverage")),
      h("pre", {class: "pre"}, JSON.stringify({coverage: scope.coverage || {}, methods: scope.methods || {}, errors: scope.errors || []}, null, 2))),
    h("h3", {}, t("obs_outside_scan")), h("ul", {}, ...(scope.outside_scan || []).map(x =>
      h("li", {}, t("obs_scope_" + x.scope), " · ", h("code", {}, x.reason)))))) : [h("p", {class: "muted"}, t("obs_no_scan"))]));
}

// Each history/relations cursor owns its server as_of. Invalidation only announces newer facts;
// it never rewrites loaded pages or mixes a new head into the old cursor's result set.
function observationPanels(type, id, path, plain = false) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const section = (mode) => {
    const relations = mode === "relations";
    const list = h("div", {}), status = h("div", {}), evidence = h("p", {class: "muted"});
    const notice = h("p", {class: "note", hidden: true}, t("obs_new_facts"));
    const kindLabel = plain ? observedText(t, "or_event_kind") : t("obs_event_kind");
    const executionLabel = plain ? observedText(t, "or_execution_filter") : t("obs_execution_filter");
    const kind = h("input", {placeholder: kindLabel, "aria-label": kindLabel});
    const execution = h("input", {placeholder: executionLabel, "aria-label": executionLabel});
    const closed = h("input", {type: "checkbox", checked: true});
    let cursor = null, asOf = null, busy = false, parameters = "", loaded = false, latestSeen = state.lastEvent;
    const seen = new Set();
    const more = h("button", {class: "secondary", hidden: true, onclick: () => load(false)}, t("load_more"));
    const refresh = h("button", {class: "secondary", onclick: () => load(true)}, t("obs_read_latest"));
    const eventRow = event => {
      const context = event.context || {};
      const occurred = Object.hasOwn(context, "occurred_at") ? context.occurred_at : event.created_at;
      const details = h("details", {}, h("summary", {}, t("obs_evidence")),
        h("pre", {class: "pre"}, JSON.stringify({body: event.body || {}, context}, null, 2)));
      const refs = [];
      for (const [field, resource] of [["execution_id", "execution"], ["session_resource_id", "session"], ["worktree_id", "worktree"], ["operation_id", "operation"]]) {
        const value = event.body?.[field] || context[field];
        if (value) refs.push(observationLink(resource, value));
      }
      return h("article", {class: "observation-record", "data-history-seq": event.seq},
        h("div", {class: "actions"}, h("strong", {}, event.kind), chip(`#${event.seq}`),
          observationLink(event.resource_type, event.resource_id)),
        h("p", {class: "muted"}, t("obs_occurred"), ": ", observationTime(occurred), " · ",
          t("obs_recorded"), ": ", observationTime(context.recorded_at ?? event.created_at), " · ", event.actor || t("obs_unknown")),
        refs.length ? h("div", {class: "actions"}, ...refs) : null, details);
    };
    const relationRow = relation => h("article", {class: "observation-record", "data-relation-id": relation.relation_id},
      h("div", {class: "actions"}, observationLink("execution", relation.execution_id),
        relation.session_resource_id ? observationLink("session", relation.session_resource_id) : chip(t("obs_pending_binding"), "warn")),
      h("p", {}, relation.role || t("obs_unknown"), " · ", relation.status || t("obs_unknown"), " · ", relation.reason || t("obs_unknown")),
      h("p", {class: "muted"}, t("obs_half_open", {start: relation.start_seq ?? "?", end: relation.end_seq ?? "∞"}), " · ",
        observationTime(relation.started_at), " → ", observationTime(relation.ended_at)),
      relation.follow_up_of_execution_id ? h("p", {}, t("obs_follow_up"), " ", observationLink("execution", relation.follow_up_of_execution_id)) : null,
      h("details", {}, h("summary", {}, t("obs_evidence")), h("pre", {class: "pre"}, JSON.stringify({relation_id: relation.relation_id,
        branch_id: relation.branch_id, parent_relation_id: relation.parent_relation_id, command_ids: relation.command_ids || [],
        worktree_ranges: relation.worktree_ranges, evidence: relation.evidence}, null, 2))));
    async function load(reset) {
      if (busy) return;
      busy = true; refresh.disabled = more.disabled = true;
      try {
        assertView(connection);
        const params = reset ? new URLSearchParams({limit: "20"}) : new URLSearchParams(parameters);
        if (reset && relations) {
          params.set("include_closed", String(closed.checked));
          if (execution.value.trim()) params.set("execution_id", execution.value.trim());
        } else if (reset) {
          params.set("order", "desc"); if (kind.value.trim()) params.set("kind", kind.value.trim());
        }
        const filters = params.toString();
        if (!reset && cursor) params.set("cursor", cursor);
        const result = await api("GET", `${path}/${relations && type === "execution" ? "sessions" : mode}?${params}`);
        assertView(connection);
        if (!Number.isSafeInteger(result.as_of) || (!reset && result.as_of !== asOf)) throw new Error("Observation cursor changed its as_of");
        const items = result[relations ? "relations" : "events"];
        if (!Array.isArray(items)) throw new Error("Invalid observation page");
        if (reset) { seen.clear(); list.replaceChildren(); parameters = filters; asOf = result.as_of; notice.hidden = true; }
        for (const item of items) {
          const key = relations ? item.relation_id : item.seq;
          if (!seen.has(key)) {seen.add(key); list.append(relations ? relationRow(item) : eventRow(item));}
        }
        if (!seen.size) list.replaceChildren(emptyState({title:t('obs_empty')}));
        cursor = result.next_cursor; more.hidden = !cursor; loaded = true; notice.hidden = latestSeen <= asOf;
        evidence.replaceChildren(...[plain ? observedText(t, "or_history_snapshot", {seq: asOf}) : t("obs_snapshot", {seq: asOf}), " ", t("obs_historical_limits"),
          !relations ? [" ", t("obs_first_recorded"), ": ", observationTime(result.coverage?.first_recorded_at)] : ""].flat());
        status.replaceChildren();
      } catch (error) { status.replaceChildren(errorBox(error, {within:status})); }
      finally {busy = false; refresh.disabled = more.disabled = false;}
    }
    const box = disclosure({label: t(relations ? "obs_relations" : "obs_history"), body: h("div", {},
      h("div", {class: "filters"}, relations ? execution : kind,
        relations ? h("label", {}, closed, " ", t("obs_include_closed")) : null, refresh), notice, evidence, status, list, more)});
    box.classList.add("observation-panel");
    if (!plain) box.classList.add("panel");
    box.dataset.observation = mode;
    box.addEventListener("toggle", () => {if (box.open && !loaded) load(true);});
    return {box, changed: event => {latestSeen = Math.max(latestSeen, event.seq); if (loaded && latestSeen > asOf) notice.hidden = false;}};
  };
  const history = section("history"), relations = section("relations");
  return {box: h(plain ? "section" : "div", plain ? {class: "or-observations", "data-resource-disclosures": ""} : {},
    history.box, relations.box), changed: event => {history.changed(event); relations.changed(event);}};
}

async function viewTask(main, id) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const head = h("div", {class: "panel", "data-observed-resource": ""}), status = h("div", {role: "status"});
  const observations = observationPanels("execution", id, `/tasks/${encodeURIComponent(id)}`, true);
  let task = null, readReady = false, active = null, retry;
  const panel = taskControlsPanel({h, t, api, caps: () => state.caps, guard: () => assertView(connection), taskId: id,
    task: () => task, ready: () => readReady && state.online && !state.nativeBusy,
    storageKey: `batc.task-control.${connection.namespace}.${id}`, errorBox, opStatus,
    onSettled: () => load()});
  main.append(head, status, panel.box, observations.box);
  async function load() {
    assertView(connection);
    if (active) {await active.catch(() => {}); assertView(connection); return load();}
    active = (async () => {
      const operation = panel.refresh(true);
      const observation = operation.catch(() => {}).then(() => api("GET", `/tasks/${encodeURIComponent(id)}`));
      await settleRefreshes([observation, operation]);
      const data = await observation;
      assertView(connection);
      if (data.task?.task_id !== id) throw new Error(t("task_control_invalid"));
      task = data.task;
      place(head, "replaceChildren", renderObservedResource({h, type: "execution", id, resource: task, caps: state.caps, namespace: state.namespace}));
      status.replaceChildren(); readReady = true; clearTimeout(retry);
    })();
    try {await active;} catch (error) {
      if (connection.generation === generation && connection.epoch === state.epoch) {
        readReady = false;
        if ([403, 404, 410].includes(error?.status)) place(head, "replaceChildren");
        status.replaceChildren(errorBox(error, {within:status}));
        clearTimeout(retry); retry = setTimeout(() => load().catch(() => {}), 3000);
      }
      throw error;
    } finally {active = null; panel.update();}
  }
  await load().catch(() => {});
  const refresh = debounceRefresh(load, 500);
  const off = onEvents(event => {observations.changed(event);
    if (["execution", "task", "operation", "session"].includes(event.resource_type)) return refresh();});
  return () => {clearTimeout(retry); off();};
}

async function viewObservedResource(main, type, id) {
  const path = type === "execution" ? `/tasks/${encodeURIComponent(id)}` : `/worktrees/${encodeURIComponent(id)}`;
  const head = h("div", {class: "panel", "data-observed-resource": ""});
  const status = h("div", {"data-observed-status": ""});
  const panels = observationPanels(type, id, path, true);
  main.append(head, status, panels.box);
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  let ticket = 0;
  const load = async () => {
    const mine = ++ticket;
    try {
      const data = await api("GET", path); assertView(connection);
      if (mine !== ticket) return;
      const resource = data[type === "execution" ? "task" : "worktree"];
      place(head, "replaceChildren", renderObservedResource({h, type, id, resource, caps: state.caps, namespace: state.namespace}));
      status.replaceChildren();
    } catch (error) {
      if (connection.generation !== generation || connection.epoch !== state.epoch || mine !== ticket) return;
      if (["VIEW_CHANGED", "CONNECTION_CHANGED"].includes(error?.code)) return;
      if ([403, 404, 410].includes(error?.status)) place(head, "replaceChildren");
      status.replaceChildren(errorBox(error, {within:status}));
    }
  };
  await load(); const reload = debounceRefresh(load, 500);
  return onEvents(event => {panels.changed(event); if ([type, "task", "operation", "session"].includes(event.resource_type)) return reload();});
}
async function viewHostDiscovery(main, host) {
  const head = h("div", {class: "panel"});
  main.append(pageHeader({title: host + " · " + t("obs_discovery")}), h("p", {class: "note"}, t("obs_discovery_note")), head);
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const profile = state.caps?.hosts?.find(item => item.host === host)?.profile_id;
  const repair = profile && state.caps?.features?.managed_repairs?.version === 1 ? repairPanel({h, t, api,
    caps: () => state.caps, guard: () => assertView(connection), namespace: connection.namespace,
    source: {kind: "discovery", host, profile_id: profile}, errorBox, opStatus}) : null;
  if (repair) main.append(repair.box);
  const load = async () => {
    try {const data = await api("GET", `/hosts/${encodeURIComponent(host)}/discovery`); assertView(connection); head.replaceChildren(discoveryEvidence(data.scopes));}
    catch (error) {head.append(errorBox(error, {within:head}));}
  };
  await load(); const reload = debounceRefresh(load, 500);
  const off = onEvents(event => {if (event.resource_type === "host" && event.resource_id === host) return reload();});
  return () => {off(); repair?.dispose();};
}

// Session read and write errors: one short localized line and its recovery; the raw code stays in Details.
function sessionErrorDetails(e, {within, insideDetails = false} = {}) {
  const code = typeof e?.code === "string" ? e.code : "";
  const detail = [code, e?.status ? `HTTP ${e.status}` : "", e?.message && e.message !== code ? e.message : ""].filter(Boolean).join(" · ");
  const body = h('div', {},
    detail ? h("p", {class: "muted"}, h("code", {}, detail)) : null,
    button({variant:'ghost',label:t('diag_export'),onClick:openDiagnostics,'data-export-diagnostics':''}));
  return insideDetails || within?.closest?.('details') ? body : disclosure({label: t("session_error_details"), body});
}
function sessionErrorBox(e, action = null, {sourceRefresh = true, ...options} = {}) {
  // Like errorBox: a caught refresh error still keeps its event batch unacknowledged.
  if (sourceRefresh && state.refreshCycle) state.refreshCycle.error ||= e;
  const code = typeof e?.code === "string" ? e.code : "";
  return h("div", {class: "session-error", role: "alert", "data-session-error": code},
    h("p", {class: "session-error-line"}, icon("circle-alert", {size: 16}), h("span", {}, options.summary || sessionErrorLine(e, t)),
      action ? h("button", {type: "button", class: "session-error-action", onclick: action.run}, action.label) : null),
    sessionErrorDetails(e, options));
}
// Pull-request links come from the receipt's provider origin, never a hard-coded host.
function sessionPullHref(target) {
  try {
    const origin = new URL(target?.provider_origin);
    if (origin.protocol !== "https:" || !/^[\w.-]+\/[\w.-]+$/.test(target.repository || "")) return null;
    const web = origin.hostname === "api.github.com" ? "https://github.com" : origin.origin;
    return `${web}/${target.repository}/pull/${target.pull_number}`;
  } catch { return null; }
}

async function viewSession(main, host, sid, context = null) {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  let fromQuery = params.get("from") || "";
  let workRecord = isWorkbench ? context?.workRecord : null;
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const path = `/sessions/${encodeURIComponent(host)}/${encodeURIComponent(sid)}`;
  const requestedWork = workSessionContext(params);
  const workContextReadable = workFollowUpAvailable(state.caps) || state.caps?.features?.pm_workspaces?.work_start?.version === 1;
  const workContext = isWorkbench && workContextReadable && requestedWork ? requestedWork : null;
  let selectedAttempt = null;
  if (workContext) {
    try {
      const selected = await readWorkSessionContext({api, guard: () => assertView(connection), ...workContext, host, sessionId: sid, missingMessage: t('workspace_work_missing')});
      workRecord = selected.record; selectedAttempt = selected.attempt;
    } catch (error) {
      try {assertView(connection);} catch {return;}
      if (!["VIEW_CHANGED", "CONNECTION_CHANGED"].includes(error.code)) main.append(errorBox(error, {within: main}));
      return;
    }
  }
  const projectViews = new Set(["overview", "discussions", "requirements", "schedule", "dependencies", "issues", "agents", "board"]);
  const storedOrigin = history.state?.batcSessionOrigin;
  const sessionWorkspace = workContext?.workspaceId || lastWorkspace(connection.namespace) || (storedOrigin?.namespace === connection.namespace
    && storedOrigin.host === host && storedOrigin.sessionId === sid ? storedOrigin.workspaceId : "");
  if (!projectViews.has(fromQuery) && projectViews.has(sessionFromMemory)) {
    fromQuery = sessionFromMemory; params.set("from", fromQuery);
    // A visible, validated return query survives reload and opening a session link in a new tab.
    try {history.replaceState(history.state, "", location.hash.split("?")[0] + "?" + params);} catch { /* unavailable */ }
  }
  if (sessionWorkspace) try {history.replaceState({...history.state, batcSessionOrigin: {
    namespace: connection.namespace, host, sessionId: sid, workspaceId: sessionWorkspace}}, "");} catch { /* unavailable */ }
  const originView = () => projectViews.has(fromQuery) ? fromQuery : "";
  const head = h("div", { class: "workspace-session-heading" });
  const titleNode = h("span");
  const markSlot = h("span", {class: "session-mark"});
  const pillSlot = h("span", {class: "session-pill"});
  const meta = h("p", {class: "ui-meta workspace-session-meta"});
  const backSlot = h("p", {class: "session-back", hidden: true});
  const metadata = h("div", {class: "workspace-metadata"});
  const result = h("section", {class: "workspace-result", "data-workspace-result": ""});
  let messageOffset = 0, nextMessageOffset = null, firstMessages = true, messageRequest = 0, messageWindow = 30, messageFailure = null;
  const readingSupported = state.caps?.features?.session_reading?.version === 1;
  const saveReading = async (action, params, preconditions = {}) => {
    const op = await submit(action, {host, session_id: sid}, params, preconditions, `${action}.${host}.${sid}`);
    assertView(connection);
    await loadMessages();
    if (op.status !== "succeeded") throw new Error(op.status_reason || t("conversation_pending"));
  };
  const scrollMemory = viewState(connection.namespace, sessionWorkspace || host, `session:${sid}`);
  const conversation = conversationPanel({h, t, when: sessionWhen, guard: () => assertView(connection), memory: scrollMemory, readingActions: readingSupported ? {
    mark: messages => saveReading("session.read", {messages}),
    remember: (position, version) => saveReading("session.position", position, {expected_version: version}),
    older: () => loadMessages(nextMessageOffset, false),
    latest: () => loadMessages(0, true),
  } : null});
  const pending = h("div", { class: "session-asking-region", "data-pending-controls": "" });
  const status = h("div", { class: "muted session-status" });
  const scope = `send.${host}.${sid}`, draftKey = `batc.draft.${connection.namespace}.${scope}`;
  const box = h("textarea", { placeholder: t("send_placeholder") });
  try { box.value = localStorage.getItem(draftKey) || ""; } catch { /* unavailable */ }
  box.oninput = () => { try { localStorage.setItem(draftKey, box.value); } catch { /* unavailable */ } };
  const queue = h("input", { type: "checkbox" });
  let row, pendingIdentity, sending = false, readReady = false, refreshInFlight = null, readError = null, queueKnown = false, workSendOwned = false;
  // Withdrawal (404/410/403 on the session read) is a settled state, not a failure to retry every second.
  let withdrawn = "", withdrawalVersion = 0, retryTimer = null, retryDelay = 1000, retryAt = 0, disposed = false, observationRequest = 0;
  const allowed = action => !withdrawn && readReady && state.online && state.viewReady && !state.nativeBusy
    && row?.api_access === "managed" && may("operate")
    && state.caps?.hosts?.find(item => item.host === host)?.writes !== false
    && state.caps?.actions?.find(item => item.action === action)?.allowed !== false;
  const identity = pend => pend ? JSON.stringify({kind: pend.kind, toolUseId: pend.toolUseId,
    toolName: pend.toolName, input_preview: pend.input_preview, questions: pend.questions}) : "";
  try {
    const savedQueue = localStorage.getItem(draftKey + ".queue");
    if (savedQueue === "true" || savedQueue === "false") {queue.checked = savedQueue === "true"; queueKnown = true;}
  } catch { /* unavailable */ }
  queue.onchange = () => {try {localStorage.setItem(draftKey + ".queue", String(queue.checked));} catch { /* unavailable */ }};
  const accessRecovery = () => ({label: t("obs_axis_connection"), run: () => {location.hash = "#/settings";}});
  const goneRecovery = () => ({label: t("ui_back"), run: () => {location.hash = originHref()?.href || "#/sessions";}});
  const sendRecovery = e => e?.code === "SESSION_BUSY" && !queue.checked
    ? {label: t("session_err_queue"), run: () => {queue.checked = true; send.click();}}
    : e?.status === 403 || e?.code === "FORBIDDEN" ? accessRecovery()
    : [404, 410].includes(e?.status) ? goneRecovery() : {label: t("ui_retry"), run: () => send.click()};
  const send = h("button", { class: "primary", disabled: true, onclick: async () => {
    if (workSendOwned || sending) return;
    if (!box.value.trim() || !allowed("session.send")) return;
    const submitted = box.value;
    sending = true;
    send.disabled = false;
    send.setAttribute("aria-disabled", "true");
    status.setAttribute("aria-busy", "true");
    try {
      assertView(connection);
      queue.onchange();
      const op = await submit("session.send", { host, session_id: sid }, { text: submitted, queue: queue.checked }, {}, scope);
      assertView(connection);
      paintSend(op);
      // The reader asked for this turn: follow it into view once it lands.
      if (op.status === "succeeded") conversation.follow();
      if (op.status === "succeeded" && box.value === submitted) {
        box.value = ""; try { localStorage.removeItem(draftKey); localStorage.removeItem(draftKey + ".queue"); } catch { /* unavailable */ }
        mobileLayout?.updateDraft();
      }
    } catch (e) { status.replaceChildren(sessionErrorBox(e, sendRecovery(e), {sourceRefresh:false, within:status})); }
    finally {
      const active = document.activeElement;
      const owned = active === send || active === document.body;
      sending = false;
      if (send.isConnected) {
        send.removeAttribute("aria-disabled");
        status.removeAttribute("aria-busy");
        send.disabled = !allowed("session.send");
        if (owned && (send.disabled || send.hidden)) {status.tabIndex = -1; status.focus();}
      }
    }
  } }, t("send"));
  const stop = button({variant: "danger", size: "sm", label: t("interrupt"), disabled: true, hidden: true, onClick: async () => {
    try {
      assertView(connection);
      const op = await submit("session.interrupt", { host, session_id: sid }, { mode: "soft" }, {}, `interrupt.${host}.${sid}`);
      assertView(connection); status.replaceChildren(opStatus(op));
    } catch (e) { status.replaceChildren(sessionErrorBox(e, e?.status === 403 ? accessRecovery() : [404, 410].includes(e?.status) ? goneRecovery() : {label: t("ui_retry"), run: () => stop.click()}, {sourceRefresh:false, within:status})); }
  }});
  const paintSend = op => {
    const id = typeof op?.operation_id === "string" ? op.operation_id : "";
    const queued = ["queued", "accepted", "pending", "running"].includes(op.status);
    const failed = op.status === "failed" || op.status === "cancelled";
    const line = h("p", {class: "muted session-send-line", title: id || null});
    if (op.status === "succeeded") {
      line.append(t("session_sent"));
      const stamp = timeStamp(op.updated_at);
      if (stamp instanceof Node) line.append(" · ", stamp);
    }
    else if (queued) line.textContent = t("session_send_queued");
    else if (failed) line.append(t("session_send_failed"), " · ", h("button", {type: "button", class: "session-retry", onclick: () => send.click()}, t("session_send_retry")));
    else line.textContent = t("op_" + op.status);
    const record = id ? disclosure({label: t("session_send_details"), body: h("p", {}, h("a", {href: `#/op/${encodeURIComponent(id)}`}, id)),
      key: `session-send:${connection.namespace}:${host}:${sid}`}) : null;
    if (record) record.classList.add("session-send-record");
    status.replaceChildren(...[line, record].filter(Boolean));
  };
  const shortcut = composerShortcut({h, t, input: box, button: send,
    storageKey: `batc.composer-shortcut.${connection.namespace}`, guard: () => assertView(connection)});
  const sendReason = h("p", {class: "muted session-send-reason", hidden: true});
  const composerRow = h("div", {class: "composer-row"}, h("label", {class: "composer-queue"}, queue, t("queue_behind")), send);
  const composer = h("div", {hidden: true, id: "workspace-message-input"}, box, composerRow);
  // Manual sessions keep their read-only hook and point to existing reviewed paths.
  const readonly = h("section", { class: "session-readonly work-paths-card", "data-session-readonly": "", hidden: true });
  let capture, permissions, batHandoff, sourcesPanel;
  const captureSlot = h("div"), permissionsSlot = h("div"), batSlot = h("div"), artifactSlot = h("div");
  const controls = h("div", { class: "panel workspace-composer", hidden: true }, pending, sendReason, composer);
  const labels = sessionLabelsPanel({h, t, api, caps: () => state.caps, guard: () => assertView(connection),
    target: {host, session_id: sid}, storageKey: `batc.labels.${connection.namespace}.${JSON.stringify([host, sid])}`, errorBox, opStatus});
  const cps = checkpointPanel(host, sid, {namespace: connection.namespace, manual: () => row?.provenance === "manual"});
  const observations = observationPanels("session", `${host}/${sid}`, path);
  const instructions = state.caps?.features?.session_instructions?.version === 1 ? instructionReceiptPanel({h, t, when, api,
    guard: () => assertView(connection), host, sessionId: sid, errorBox}) : null;
  instructions?.box.addEventListener("toggle", () => {if (instructions.box.open) instructions.refresh();});
  const deliveryPills = h("div", {class: "session-delivery-pills"});
  const sourcesEmpty = () => emptyState({title:t('session_sources_empty'), heading:'h3'});
  const sourcesBody = h("div", {"data-session-sources": ""}, sourcesEmpty());
  const details = disclosure({label: t("session_details_session"), body: metadata, key: `session-details:${connection.namespace}:${host}:${sid}`});
  details.classList.add("workspace-evidence");
  details.dataset.sessionDetails = "";
  const results = inspector({title: t("session_results"), dock: true, overlay: isWorkbench,
    historyNavigation: isWorkbench, canReopen: () => !disposed && !withdrawn && readReady, returnFocus: () => openResults});
  const openResults = isWorkbench ? button({variant: "secondary", icon: "panel-right", label: t("session_results"), onClick: () => results.open()})
    : iconButton({icon: "inbox", label: t("session_results"), onClick: () => results.open()});
  const wide = matchMedia("(min-width: 1200px)");
  const syncWide = () => {openResults.hidden = !isWorkbench && wide.matches || Boolean(withdrawn);};
  syncWide(); wide.addEventListener("change", syncWide);
  const sessionSection = (name, title, body, action = null) => h("section", {class: "session-section"},
    h("div", {class: "session-section-head"}, h("h3", {class: "session-section-title"}, icon(name, {size: 16}), title), action), body);
  const changesSection = sessionSection("folder", t("session_changes"), h("div", {class: "session-changes"}, captureSlot, artifactSlot));
  const slotVisible = slot => [...slot.children].some(child => !child.hidden);
  const syncChanges = () => {changesSection.hidden = !slotVisible(captureSlot) && !slotVisible(artifactSlot);};
  syncChanges();
  const overflow = menu({button: iconButton({icon: "more-horizontal", label: t("ui_more")}),
    items: [
      ...(isWorkbench ? [{key: "interrupt", label: t("interrupt"), content: stop}] : []),
      {key: "discuss", label: t("ui_discuss"), href: "#", icon: "message-square"},
      {key: "copy-title", label: t("bat_menu_title"), icon: "clipboard", title: t("bat_browser"), onSelect: () => batHandoff?.copyTitle()},
      {key: "copy-id", label: t("bat_menu_id"), icon: "clipboard", title: t("bat_browser"), onSelect: () => batHandoff?.copyId()},
      {key: "open-bat", label: t("bat_open"), icon: "external-link", title: t("bat_browser"), disabled: !nativeDesktop, onSelect: () => batHandoff?.open()},
      {key: "shortcut", label: t("composer_shortcut"), content: shortcut.box},
    ]});
  if (isWorkbench) {stop.setAttribute('role', 'menuitem'); stop.addEventListener('click', () => overflow.close());}
  const header = pageHeader({title: titleNode, mark: markSlot, pill: pillSlot, meta, actions: isWorkbench ? [openResults] : [stop, openResults], overflow});
  head.append(backSlot, header);
  const resultSourceAvailable = () => !disposed && !withdrawn && readReady
    && row?.provenance === 'connector_managed' && row?.api_access === 'managed';
  const resultForm = options => pmDurableForm({h, t, api, caps: () => state.caps, namespace: connection.namespace,
    ...options, guard: () => {
      assertView(connection);
      if (disposed || withdrawn) throw new ApiError(410, 'VIEW_CHANGED', 'View retired');
    },
    permitted: () => resultSourceAvailable() && options.permitted(),
    recoverPermitted: () => resultSourceAvailable() && (options.recoverPermitted || options.permitted)(),
    canSend: () => state.online && !state.nativeBusy && state.viewReady,
  });
  const workResultTools = isWorkbench && Boolean(workContext);
  const sessionTools = h('div', {}, workResultTools ? context?.itemBox : null,
    sessionSection("list-checks", t("checkpoints"), cps.box, cps.action),
    sessionSection("check", t("session_delivery"), result),
    sessionSection("inbox", t("session_sources"), sourcesBody),
    batSlot, permissionsSlot, labels.box, instructions?.box, observations.box, details);
  const supporting = h('div', {}, sessionTools);
  const toolsDisclosure = workResultTools ? disclosure({label: t('session_tools_history'),
    body: h('div'), key: `work-result-tools:${connection.namespace}:${workContext.workspaceId}:${selectedAttempt.attempt_id}`}) : null;
  if (toolsDisclosure) toolsDisclosure.dataset.workResultTools = '';
  const showSupportingTools = visible => {
    if (!toolsDisclosure) return;
    const target = visible ? toolsDisclosure : sessionTools;
    if (supporting.firstChild === target) return;
    const focused = sessionTools.contains(document.activeElement) ? document.activeElement : null;
    if (visible) {
      toolsDisclosure.append(sessionTools);
      if (focused) toolsDisclosure.open = true;
    }
    supporting.replaceChildren(target);
    focused?.focus({preventScroll: true});
  };
  const fixedResult = workContext ? workResultPanel({h, t, api, workspaceId: workContext.workspaceId,
    onVisibility: showSupportingTools,
    caps: () => state.caps, errorBox, form: resultForm, namespace: connection.namespace, alive: () => {
      assertView(connection);
      if (disposed || withdrawn) throw new ApiError(410, 'VIEW_CHANGED', 'View retired');
    }}) : null;
  results.body.append(...[fixedResult?.element, workResultTools ? null : context?.itemBox, changesSection, supporting].filter(Boolean));
  const lane = h("section", {class: "workspace-conversation"}, conversation.box, readonly, controls, status);
  // Work-target second input stays in this lane. Tab switches hide the lane; they do not rebuild it.
  // With the capability, this lane has one instruction composer. The legacy field stays for its draft
  // and is not a send path. Without the capability, that field remains the composer.
  const followWorkspace = workContext?.workspaceId || "";
  const followWorkId = workRecord?.work_item?.work_item_id || "";
  let mobileLayout = null;
  const followUp = isWorkbench && followWorkspace && /^wi_[0-9a-f]{20}$/.test(followWorkId) && workFollowUpAvailable(state.caps)
    ? mountWorkFollowUp({h, t, api, caps: () => state.caps, guard: () => assertView(connection),
      namespace: connection.namespace, workspaceId: followWorkspace, workItemId: followWorkId, selectedAttemptId: selectedAttempt.attempt_id, errorBox,
      canSend: () => state.online && !state.nativeBusy && state.viewReady,
      clientGate: () => clientActionGate(state, {post: true}),
      managed: () => !withdrawn && row?.provenance === "connector_managed" && row?.api_access === "managed",
      onField: (field, button) => {
        shortcut.rebind(field, button);
        if (!mobileLayout) return;
        if (field && button) mobileLayout.retarget({textarea: field, composer: followUp.box, composerRow: field.closest("form")?.querySelector(".actions") || null, controlsId: field.id});
        else mobileLayout.detachComposer();
      }})
    : null;
  // A linked Work with follow-up support never falls back to raw session.send
  // when its explicit selection is missing, malformed, or no longer readable.
  workSendOwned = workFollowUpAvailable(state.caps) && Boolean(workRecord || ['workspace', 'work', 'attempt'].some(key => params.has(key)));
  if (followUp) lane.append(followUp.box);
  mobileLayout = setupMobileSessionLayout({ head, composer: controls, textarea: box, t, guard: () => assertView(connection),
    infoRow: conversation.box.querySelector(".conversation-caption"), composerRow });
  if (followUp) {composer.hidden = true; shortcut.rebind(null, null); mobileLayout.detachComposer();}
  if (context?.picker) main.append(context.picker);
  const workspaceNode = h("div", {class: "workspace-session session-workspace", "data-session-view": ""}, lane, results);
  const withdrawnSlot = h("div", {class: "session-withdrawn"});
  main.append(head, workspaceNode);
  const perspectives = isWorkbench ? workbenchViews({main, head, work: workspaceNode, results, namespace: connection.namespace,
    workspace: sessionWorkspace || host, host, sessionId: sid, workId: workRecord?.work_item?.work_item_id, t}) : null;
  const originHref = () => {
    const view = originView(), workspaceId = sessionWorkspace;
    return view && workspaceId ? {href: `#/pm/${encodeURIComponent(workspaceId)}/${view}`, view} : null;
  };
  const paintOrigin = () => {
    const origin = originHref();
    backSlot.replaceChildren();
    if (!origin) {backSlot.hidden = true; return;}
    backSlot.hidden = false;
    backSlot.append(h("a", {href: origin.href, "data-session-back": origin.view}, icon("chevron-left"), t("session_back", {view: t("ui_" + origin.view)})));
  };
  const paintDiscuss = items => {
    const link = overflow.querySelector("[data-menu-key=discuss]");
    const workspaceId = sessionWorkspace;
    if (!link) return;
    if (!workspaceId) {link.hidden = true; link.setAttribute("aria-disabled", "true"); return;}
    // The selected Work has already passed the public Work/Attempt/session check.
    // Keep it first even if the session's broader related-work list is empty or long.
    const related = workContext ? [{work_item_id: workContext.workItemId}, ...(items || [])] : items || [];
    const refs = related.map(item => ({kind: "work_item", id: item.work_item_id})).filter(isContextRef);
    link.hidden = false; link.removeAttribute("aria-disabled");
    // An unknown title stays empty; the session ID is not a title.
    link.setAttribute("href", discussHref({workspaceId, refs, title: row?.title || "", from: sessionHref({host, sessionId: sid, from: originView(), ...workContext})}));
  };
  let deliveryTicket = 0, deliveryItems = [], sourceItemId = "";
  const deliveryKnown = new Map();
  const loadDelivery = async items => {
    const ticket = ++deliveryTicket;
    deliveryItems = items || [];
    const workspaceId = sessionWorkspace;
    const linked = [...new Map(deliveryItems.filter(item => isContextRef({kind: "work_item", id: item.work_item_id}))
      .map(item => [item.work_item_id, item])).values()];
    const current = () => {assertView(connection); return !disposed && ticket === deliveryTicket;};
    const sourceId = linked[0]?.work_item_id || "";
    if (sourceItemId !== sourceId) {
      sourcesPanel?.dispose(); sourcesPanel = null; sourceItemId = sourceId;
      sourcesBody.replaceChildren(sourcesEmpty());
    }
    if (!sourcesPanel && !context?.itemBox && state.caps?.features?.work_item_results?.version === 1 && sourceId) {
      sourcesPanel = resultSourcesPanel({h, t, api, guard: () => {assertView(connection); if (withdrawn || disposed) throw Error(t("session_err_gone"));}, workItemId: sourceId,
        linkTarget, onEvents, errorBox});
      sourcesBody.replaceChildren(sourcesPanel.box);
    }
    const ids = new Set(linked.map(item => item.work_item_id));
    for (const id of deliveryKnown.keys()) if (!ids.has(id)) deliveryKnown.delete(id);
    if (!workspaceId || !linked.length || !state.caps?.features?.pm_workspaces?.engineering_delivery) {
      deliveryKnown.clear(); deliveryPills.replaceChildren(); return;
    }
    const nextKnown = new Map(deliveryKnown), failed = new Set(), removed = new Set();
    // Keep the last validated rows while reading. Publish the complete batch once.
    for (const item of linked) {
      const id = item.work_item_id;
      try {
        const data = checkDelivery(await api("GET", `/pm/workspaces/${encodeURIComponent(workspaceId)}/work/${encodeURIComponent(id)}/deliveries?after=0&limit=20`),
          workspaceId, id, 0, t);
        if (!current()) return;
        nextKnown.set(id, {deliveries: data.deliveries, next: data.next_after ?? null, filtered: data.source_visibility_filtered === true});
      } catch (error) {
        if (!["VIEW_CHANGED", "CONNECTION_CHANGED"].includes(error?.code) && current()) {
          if ([403, 404, 410].includes(error?.status)) {
            nextKnown.delete(id); removed.add(id);
            if (sourceItemId === id) {sourcesPanel?.dispose(); sourcesPanel = null; sourcesBody.replaceChildren(sourcesEmpty());}
          } else failed.add(id);
        } else throw error;
      }
    }
    if (!current()) return;
    deliveryKnown.clear(); for (const [id, known] of nextKnown) deliveryKnown.set(id, known);
    const retry = () => button({variant: "ghost", size: "sm", label: t("ui_retry"), onClick: () => loadDelivery(deliveryItems).catch(() => {})});
    const receipts = linked.flatMap(item => deliveryKnown.get(item.work_item_id)?.deliveries || []);
    const groups = prGroups(receipts), pills = prPills({deliveries: receipts});
    const nodes = groups.map(({target, rows}, index) => {
      const href = sessionPullHref(target);
      const deployed = rows.some(receipt => receipt.deployment?.verified === true && receipt.deployment?.is_current === true);
      return h("div", {class: "session-delivery-row", "data-session-pr": String(target.pull_number)},
        href ? h("a", {href, target: "_blank", rel: "noopener", class: "session-delivery-link"}, pills[index]) : pills[index],
        deployed ? statusPill("deployed") : null);
    });
    // A deployment can have a fixed commit target without a PR. Preserve that evidence too.
    for (const receipt of receipts.filter(receipt => !receipt.target?.pull_number && receipt.deployment))
      nodes.push(h("div", {class: "session-delivery-row"}, statusPill(receipt.deployment.verified === true && receipt.deployment.is_current === true ? "deployed" : "unknown")));
    if (removed.size) nodes.push(h("p", {class: "session-delivery-state", "data-session-delivery-state": "removed"}, t("session_delivery_removed")));
    if (linked.some(item => !deliveryKnown.has(item.work_item_id) && !removed.has(item.work_item_id)))
      nodes.push(h("p", {class: "session-delivery-state", "data-session-delivery-state": "unknown"}, statusPill("unknown"), retry()));
    if (linked.some(item => failed.has(item.work_item_id) && deliveryKnown.has(item.work_item_id)))
      nodes.push(h("p", {class: "session-delivery-state", "data-session-delivery-state": "stale"}, t("session_delivery_stale"), " · ", retry()));
    if (linked.some(item => deliveryKnown.get(item.work_item_id)?.next != null))
      nodes.push(h("p", {class: "session-delivery-state", "data-session-delivery-state": "partial"}, t("session_delivery_more", {count: groups.length})));
    else if (linked.some(item => deliveryKnown.get(item.work_item_id)?.filtered)) nodes.push(statusPill("partial"));
    if (!nodes.length) nodes.push(h("p", {class: "muted"}, t("session_delivery_none")));
    const content = h("div", {class: "session-delivery-pills"}, ...nodes);
    if (!deliveryPills.isEqualNode(content)) deliveryPills.replaceChildren(...content.childNodes);
  };
  const renderPending = () => {
    const pend = row.api_access === "managed" ? row.pending : null;
    const current = identity(pend);
    if (current === pendingIdentity) return; // keep focused fields and answers for the same request
    pendingIdentity = current; pending.replaceChildren();
    if (!pend) return;
    const answerScope = `answer.${host}.${sid}.${pend.toolUseId || ""}`;
    const key = `batc.draft.${connection.namespace}.${answerScope}`;
    let saved;
    try { saved = JSON.parse(localStorage.getItem(key)); } catch { /* unavailable */ }
    const answer = async params => {
      let sourceRefresh = true;
      try {
        assertView(connection);
        // Inventory is observation evidence, not write authority. Recheck its latest identity here;
        // central still validates tool_use_id against the live BAT pending frame before any write.
        const sourceVersion = withdrawalVersion;
        let latest;
        try {latest = await api("GET", path);}
        catch (error) {
          assertView(connection);
          if (disposed || sourceVersion !== withdrawalVersion) return;
          if ([403, 404, 410].includes(error?.status)) {
            withdraw(error.status === 403 ? "forbidden" : "gone", error); return;
          }
          throw error;
        }
        assertView(connection);
        // A fresh view after withdrawal cannot authorize an older preflight's answer.
        if (disposed || withdrawn || sourceVersion !== withdrawalVersion) return;
        applyObservation(latest);
        sourceRefresh = false;
        if (latest.session.api_access !== "managed" || identity(latest.session.pending) !== current || !pend.toolUseId)
          throw new ApiError(409, "PENDING_CHANGED", t("pending_changed"));
        const op = await submit("session.answer", { host, session_id: sid }, { ...params, tool_use_id: pend.toolUseId }, {}, answerScope);
        assertView(connection); status.replaceChildren(opStatus(op));
        if (op.status === "succeeded") {sourceRefresh = true; await loadObservation();}
      } catch (e) {
        status.replaceChildren(sessionErrorBox(e, e?.code === "PENDING_CHANGED"
          ? {label: t("session_err_review"), run: async () => {
            await refresh().catch(() => {});
            pending.querySelector("button:not(:disabled), input")?.focus();
          }}
          : e?.status === 403 ? accessRecovery() : [404, 410].includes(e?.status) ? goneRecovery() : {label: t("ui_retry"), run: () => answer(params)}, {sourceRefresh, within:status}));
      }
    };
    const actions = h("div", { class: "actions session-asking-actions" });
    const card = h("div", { class: "session-asking", role: "group", "aria-label": t("pending_" + pend.kind) },
      h("div", { class: "title" }, statusPill("asking_you"), " ", t("pending_" + pend.kind)));
    const content = h("div", {class: "session-asking-content"});
    if (pend.kind === "permission") {
      content.append(h("p", {}, h("code", {}, pend.toolName || "")), h("p", { class: "session-asking-preview" }, pend.input_preview || ""));
      actions.append(
        h("button", { class: "primary", "data-answer-action": "", disabled: !pend.toolUseId || !allowed("session.answer"), onclick: () => answer({ permission: "allow" }) }, t("allow")),
        h("button", { class: "danger", "data-answer-action": "", disabled: !pend.toolUseId || !allowed("session.answer"), onclick: () => answer({ permission: "deny" }) }, t("deny")));
    } else if (pend.kind === "ask_user") {
      const fields = [];
      const save = () => { try { localStorage.setItem(key, JSON.stringify({identity: current, answers: fields.map(f => f.value)})); } catch { /* unavailable */ } };
      for (const [index, q] of (pend.questions || []).entries()) {
        const input = h("input", { placeholder: t("answer"), "aria-label": q.question || t("answer"),
          value: saved?.identity === current ? saved.answers?.[index] || "" : "", oninput: save });
        fields.push(input);
        const picks = (q.options || []).map(o => h("button", { class: "secondary", onclick: () => { input.value = o; save(); } }, o));
        content.append(h("div", { class: "session-question" }, h("p", {}, q.header ? h("strong", {}, `${q.header} · `) : null, q.question),
          picks.length ? h("div", { class: "actions" }, ...picks) : null, input));
      }
      actions.append(h("button", { class: "primary", "data-answer-action": "", disabled: !pend.toolUseId || !allowed("session.answer"),
        onclick: () => answer({ answers: fields.map(f => f.value) }) }, t("answer")));
    }
    // The answer row stays pinned at the bottom of the prompt while its questions scroll.
    card.append(content, actions);
    pending.append(card);
  };
  const updateControls = () => {
    permissions?.update();
    followUp?.sync();
    fixedResult?.sync();
    // In-flight send stays focusable. Permission and readiness still use disabled, once the send has finished.
    send.disabled = sending ? false : !allowed("session.send");
    if (sending) send.setAttribute("aria-disabled", "true");
    else send.removeAttribute("aria-disabled");
    stop.disabled = !allowed("session.interrupt");
    stop.hidden = withdrawn || row?.api_access !== "managed";
    let reason = "";
    delete sendReason.dataset.clientActionCode;
    if (row?.api_access === "managed" && send.disabled && !sending) {
      const gate = clientActionGate(state, {post: true});
      if (!gate.allowed) {
        const error = clientActionError(gate.reason, t);
        reason = error.message; sendReason.dataset.clientActionCode = error.code;
      }
      else if (!readReady) reason = t("session_send_waiting");
      else if (!may("operate")) reason = t("session_send_scope");
      else if (state.caps?.hosts?.find(item => item.host === host)?.writes === false) reason = t("session_send_host");
      else if (state.caps?.actions?.find(item => item.action === "session.send")?.allowed === false) reason = t("session_send_denied");
    }
    sendReason.hidden = workSendOwned || !reason; sendReason.textContent = reason;
    for (const control of pending.querySelectorAll("[data-answer-action]"))
      control.disabled = !row?.pending?.toolUseId || !allowed("session.answer");
    // The old composer panel stays only for a real question or permission. An empty shell is not a second field.
    if (workSendOwned) controls.hidden = row?.api_access !== "managed" || pending.childElementCount === 0;
  };
  let lastReading = null;
  const readingDetail = h("p", {class: "muted note", "data-session-reading-detail": "", hidden: true});
  const paintReadingDetail = readingData => {
    if (readingData !== undefined) lastReading = readingData;
    const summary = readingSummary(t, lastReading || row?.reading);
    readingDetail.textContent = summary.detail;
    readingDetail.hidden = !summary.detail;
  };
  const applyObservation = data => {
    row = data.session || (data.cleanup?.length ? {host, session_id: sid, provenance: "unknown", api_access: "read_only"} : null);
    if (!row) throw new Error(t("obs_unknown"));
    rememberObservation("session", `${host}/${sid}`, data, [`host:${host}`,
      ...(data.work_items || []).map(item => `work_item:${item.work_item_id}`),
      ...(workRecord ? [`work_item:${workRecord.work_item.work_item_id}`] : []),
      ...(data.relations_summary || []).flatMap(relation => [`execution:${relation.execution_id}`, `task:${relation.execution_id}`]),
      ...(data.started_from?.operation_id ? [`operation:${data.started_from.operation_id}`] : [])]);
    if (!queueKnown) {queue.checked = Boolean(row.streaming); queueKnown = true;}
    // Unknown stays unknown: no ID as a title and no BAT mark for an unreported agent.
    titleNode.textContent = workRecord?.work_item?.title || row.title || t("session_untitled");
    markSlot.replaceChildren(agentMark(row.agent_kind));
    const headerPill = sessionPill(row);
    const pillTone = headerPill?.tone === "stale" ? "warn" : headerPill?.tone || "neutral";
    pillSlot.replaceChildren(...(headerPill ? [
      h("span", {class: `ui-pill tone-${pillTone}`, "data-status": headerPill.status}, t(headerPill.key))
    ] : []));
    const metaBits = [];
    if (context?.project?.name) metaBits.push({kind: "link", href: `#/project/${encodeURIComponent(context.project.project_id)}`, text: context.project.name});
    else if (row.workspace) metaBits.push({kind: "text", text: row.workspace});
    metaBits.push({kind: "link", href: `#/host/${encodeURIComponent(host)}`, text: row.host || host, quiet: true});
    const observedIso = typeof row.observed_at === "number" ? new Date(row.observed_at * 1000).toISOString() : row.observed_at;
    if (row.observed_at) metaBits.push({kind: "time", text: sessionWhen(row.observed_at), dateTime: typeof observedIso === "string" ? observedIso : ""});
    meta.replaceChildren(...metaBits.flatMap((bit, index) => {
      const sep = index < metaBits.length - 1;
      const [head, tail] = metaTail(bit.text, sep);
      const node = bit.kind === "time" ? timeStamp(bit.dateTime, {attrs:{class:"session-meta-time"}})
        : bit.kind === "text" ? h("span", {}, head, sep ? h("span", {class: "session-meta-tail"}, tail) : tail)
        : metaAnchor(bit.href, bit.text, sep, bit.quiet);
      return index ? [" ", node] : [node];
    }));
    paintOrigin();
    mobileLayout?.updateInfo();
    paintReadingDetail();
    metadata.replaceChildren(h("div", {class: "actions"}, ...sessionBadges(row)),
      h("dl", { class: "kv" },
        h("dt", {}, t("host")), h("dd", {}, h("a", {href: `#/host/${encodeURIComponent(host)}`}, row.host)), h("dt", {}, t("workspace")), h("dd", {}, row.workspace || ""),
        h("dt", {}, t("sessions_id")), h("dd", {}, h("code", {}, row.session_id)),
        h("dt", {}, t("agent")), h("dd", {}, [cliName(row.agent_kind), row.model].filter(Boolean).join(" · ") || t("obs_unknown")),
        h("dt", {}, t("session_origin")), h("dd", {}, t("provenance_" + (['manual', 'connector_managed', 'unknown'].includes(row.provenance) ? row.provenance : 'unknown'))),
        h("dt", {}, t("obs_axis_activity")), h("dd", {}, t(sessionActivity(row).key)),
        h("dt", {}, t("observed")), h("dd", {}, timeStamp(row.observed_at, {empty:t("obs_unknown")}))),
      readingDetail, observationState(row), confinementDetails(row));
    if (!batHandoff) batHandoff = sessionBatPanel({h, t, guard: () => assertView(connection), session: () => row,
      storageKey: `batc.session-bat.${connection.namespace}.${JSON.stringify([host, sid])}`});
    batSlot.append(batHandoff.box); batHandoff.update();
    if (data.started_from) {
      const from = data.started_from;
      metadata.append(h("p", { class: "note" }, t("started_from", { commit: from.commit_sha.slice(0, 12) }), " ",
        h("a", { href: `#/session/${encodeURIComponent(from.source_host)}/${encodeURIComponent(from.source_session_id)}` }, t("source_session")),
        " · ", h("a", { href: `#/op/${from.operation_id}` }, from.operation_id)));
    }
    if (data.work_items?.length) metadata.append(linkedItems(data.work_items));
    if (data.discovery?.length) metadata.append(h("details", {}, h("summary", {}, t("obs_discovery")), discoveryEvidence(data.discovery)));
    const managed = row.api_access === "managed";
    // "You" only for turns sent through Connector; a person's own BAT session shows their turns as a person's.
    conversation.setUserLabel(managed && row.provenance === "connector_managed" ? t("message_you") : t("message_person"));
    artifactSlot.replaceChildren();
    if (row.provenance === "connector_managed" && state.caps?.artifacts?.capture?.managed_single_file)
      artifactSlot.append(h("p", {}, h("a", {href: `#/artifact-review/session/${encodeURIComponent(host)}/${encodeURIComponent(sid)}`}, t("ar_open"))));
    paintDiscuss(data.work_items);
    if (managed && row.provenance === "connector_managed") {
      const links = [];
      if (state.caps?.actions?.some(a => a.action === "session.relay")) links.push(h("a", {href: `#/orchestrate/relay/${encodeURIComponent(host)}/${encodeURIComponent(sid)}`}, t("orch_relay")));
      if (row.agent_kind === "claude" && !data.relations_summary?.length && state.caps?.actions?.some(a => a.action === "session.failover"))
        links.push(h("a", {href: `#/orchestrate/failover/${encodeURIComponent(host)}/${encodeURIComponent(sid)}`}, t("orch_failover")));
      if (links.length) metadata.append(h("div", {class: "actions"}, ...links));
    }
    if (managed && row.provenance === "connector_managed" && !permissions) {
      permissions = permissionsPanel({h, t, api, caps: () => state.caps, guard: () => assertView(connection),
        errorBox, opStatus, storageKey: `batc.permissions.${connection.namespace}.${JSON.stringify([host, sid])}`,
        target: {host, session_id: sid}, session: () => row,
        ready: () => readReady && state.viewReady && state.online && !state.nativeBusy});
      permissionsSlot.append(permissions.box);
    }
    const manualSource = row.provenance === "manual" && state.caps?.artifacts?.capture?.manual_single_file;
    if (manualSource && !capture) {
      capture = manualCapture(`session.${JSON.stringify([host, sid])}`, {host, session_id: sid});
      captureSlot.append(capture);
    }
    if (capture) capture.hidden = !manualSource;
    syncChanges();
    composer.hidden = workSendOwned ? true : !managed; controls.hidden = !managed; readonly.hidden = managed;
    if (row.provenance === "manual") {
      const reason = checkpointReason(state.caps, host);
      readonly.replaceChildren(h("h2", {}, t("paths_view_only")), h("p", {}, t("paths_manual")),
        h("div", {class: "actions"}, ...[
          reason ? null : button({variant: "secondary", label: t("paths_continue"), onClick: () => {results.open(); cps.open();}}),
          mayStartSession(state.caps) ? h("a", {class: "ui-button secondary", href: `#/start?host=${encodeURIComponent(host)}`}, t("paths_start")) : null,
        ].filter(Boolean)),
        ...[reason ? h("p", {class: "muted", "data-checkpoint-reason": ""}, t(reason), " ", h("a", {href: "#/settings?section=connection"}, t("paths_setup"))) : null].filter(Boolean),
        h("a", {class: "global-quiet-link", href: "#/settings?section=agents"}, t("paths_mcp")));
    } else readonly.textContent = t("session_readonly_unknown");
    perspectives?.update(data, workRecord);
    renderPending(); updateControls();
  };
  const loadObservation = async () => {
    const request = ++observationRequest;
    const current = () => {assertView(connection); return !disposed && request === observationRequest;};
    let data;
    try {
      data = await api("GET", path);
      if (workRecord && !workContext && current()) {
        const id = workRecord.work_item.work_item_id;
        const next = await api("GET", `/work-items/${encodeURIComponent(id)}`);
        if (!current()) return;
        const linked = next.links?.some(link => {
          const target = link.target?.found ? link.kind === 'session' ? link.target : link.kind === 'operation' ? link.target.session : null : null;
          return target?.host === host && target?.session_id === sid;
        });
        if (next.work_item?.work_item_id !== id || !linked) throw new ApiError(404, 'WORK_LINK_REMOVED', t('workspace_work_missing'));
        workRecord = next;
      }
      if (workContext && current()) {
        const selected = await readWorkSessionContext({api, guard: () => assertView(connection), ...workContext, host, sessionId: sid, missingMessage: t('workspace_work_missing')});
        if (!current()) return;
        workRecord = selected.record; selectedAttempt = selected.attempt;
      }
    }
    catch (error) {
      if ([403, 404, 410].includes(error?.status) && current()) {
        error.sessionWithdrawn = error.status === 403 ? "forbidden" : "gone";
        withdraw(error.sessionWithdrawn, error);
      }
      throw error;
    }
    if (!current()) return;
    applyObservation(data);
    if (fixedResult) await fixedResult.setAttempt(selectedAttempt);
    if (!current()) return;
    let work = [], repositories = [];
    if (context?.project) {
      const project = await api("GET", `/projects/${encodeURIComponent(context.project.project_id)}`); assertView(connection);
      if (!current()) return;
      work = (project.work || []).filter(item => item.host === host && item.session_id === sid);
      repositories = project.project?.repositories || [];
    } else if (row.worktree_id && state.caps?.features?.execution_delivery?.version === 1) {
      const detail = await api("GET", `/worktrees/${encodeURIComponent(row.worktree_id)}`); assertView(connection);
      if (!current()) return;
      work = (detail.worktree?.work || []).filter(item => item.host === host && item.session_id === sid);
    }
    if (!current()) return;
    const content = h("div", {},
      ...work.map(item => deliveryWork(item, repositories, true)),
      data.work_items?.length ? sessionWorkLine(data.work_items) : null,
      !work.some(item => item.worktree_id === row.worktree_id) && row.worktree_id ? h("p", {}, observationLink("worktree", row.worktree_id)) : null);
    if (!deliveryPills.isConnected) result.append(deliveryPills);
    const old = result.querySelector("[data-session-work]");
    content.dataset.sessionWork = "";
    if (!old) result.append(content);
    else if (!old.isEqualNode(content)) old.replaceWith(content);
    await loadDelivery(data.work_items);
    if (!current()) return;
    result.querySelector("[data-session-result-empty]")?.remove();
    if (!work.length && !deliveryPills.childElementCount && !data.work_items?.length)
      result.append(h('div', {'data-session-result-empty': ''}, emptyState({title:t('workspace_result_empty')})));
  };
  const loadMessages = async (offset = messageOffset, latest = false) => {
    if (offset == null) return;
    const request = ++messageRequest;
    const get = (offset, size = messageWindow) => api("GET", `${path}/messages?last_n=${size}&include_tools=true&offset=${offset}${readingSupported ? "&max_chars=60000&max_message_chars=60000" : ""}`);
    let read;
    try {
      try { read = await get(offset); }
      catch (error) {
        // Too large for the response limit: try once with a smaller window before giving up.
        if (error?.status !== 413 || messageWindow <= 10) throw error;
        assertView(connection);
        if (request !== messageRequest) return;
        read = await get(offset, 10); messageWindow = 10;
      }
      assertView(connection);
      if (request !== messageRequest) return;
      const restore = firstMessages && readingSupported ? read.reading?.position : null;
      if (restore && Number.isInteger(restore.page_offset) && restore.page_offset >= 0) {
        const savedOffset = Math.max(0, restore.page_offset - Math.floor(messageWindow / 2));
        if (savedOffset !== offset) {offset = savedOffset; read = await get(offset); assertView(connection);}
        if (request !== messageRequest) return;
      }
      messageOffset = offset; nextMessageOffset = read.next_offset; firstMessages = false;
      paintReadingDetail(readingSupported ? read.reading : null);
      messageFailure = null;
      conversation.update(read.messages, readingSupported ? {...read, restore, latest, olderWindow: offset > 0} : {next_offset: read.next_offset});
    } catch (error) {
      if (["VIEW_CHANGED", "CONNECTION_CHANGED"].includes(error?.code) || request !== messageRequest) throw error;
      messageFailure = error;
      throw error;
    }
  };
  const scheduleRetry = () => {
    if (retryTimer || withdrawn || disposed) return;
    const delay = retryDelay; retryDelay = Math.min(retryDelay * 2, 30_000);
    retryAt = Date.now() + delay;
    retryTimer = setTimeout(() => {
      retryTimer = null;
      if (!readReady && !refreshInFlight && !withdrawn && !disposed) refresh().catch(() => {});
    }, delay);
  };
  const backLink = () => {
    const origin = originHref();
    return origin ? h("a", {href: origin.href, class: "ui-button secondary"}, t("session_back", {view: t("ui_" + origin.view)}))
      : h("a", {href: "#/sessions", class: "ui-button secondary"}, t("session_back_sessions"));
  };
  const withdraw = (kind, error = null) => {
    const first = !withdrawn;
    withdrawn = kind;
    followUp?.suspend();
    fixedResult?.withdraw();
    perspectives?.withdraw();
    withdrawalVersion += 1;
    clearTimeout(retryTimer); retryTimer = null; retryDelay = 1000;
    // Late reads for the withdrawn session must not paint again.
    ++observationRequest; ++deliveryTicket; ++messageRequest; cps.clear(); deliveryKnown.clear();
    state.observations?.delete(`session:${host}/${sid}`);
    if (first && !results.hidden && (!wide.matches || isWorkbench)) results.close();
    conversation.clear(); pending.replaceChildren(); pendingIdentity = undefined; status.replaceChildren(); readError = null;
    deliveryPills.replaceChildren(); result.replaceChildren(); metadata.replaceChildren();
    sourcesPanel?.dispose(); sourcesPanel = null; sourcesBody.replaceChildren(sourcesEmpty());
    titleNode.textContent = t("session_heading"); markSlot.replaceChildren(); pillSlot.replaceChildren(); meta.replaceChildren();
    overflow.hidden = true; row = null; readReady = false; updateControls(); syncWide();
    if (first) {workspaceNode.remove(); if (context?.picker) context.picker.hidden = true; head.after(withdrawnSlot);}
    withdrawnSlot.replaceChildren(h("div", {"data-session-withdrawn": kind},
      emptyState({icon: "circle-alert", title: t(kind === "forbidden" ? "session_withdrawn_forbidden" : "session_withdrawn_gone"),
        action: h("div", {}, backLink(), sessionErrorDetails(error, {within:withdrawnSlot}))})));
  };
  const reinstate = () => {
    withdrawn = "";
    followUp?.restore();
    perspectives?.restore();
    withdrawnSlot.remove(); if (context?.picker) context.picker.hidden = false; head.after(workspaceNode);
    overflow.hidden = false; syncWide();
    void settleRefreshes([cps.load(), labels.refresh()]).catch(() => {});
  };
  let refreshForeground = foregroundVersion;
  const refresh = async (fromEvent = false) => {
    assertView(connection);
    // A resumed event must reread now, rather than inherit a failed read's timer.
    if (refreshForeground !== foregroundVersion) {
      refreshForeground = foregroundVersion;
      clearTimeout(retryTimer); retryTimer = null; retryDelay = 1000;
    }
    if (disposed) return;
    // Replayed, unacknowledged events obey the same backoff as timer retries.
    if (fromEvent && retryTimer && Date.now() < retryAt && readError) throw readError.error;
    if (refreshInFlight) {
      await refreshInFlight;
      if (fromEvent) return refresh(true);
      return;
    }
    refreshInFlight = (async () => {
      try {
        await settleRefreshes([loadObservation(), loadMessages()]);
        if (withdrawn) reinstate();
        await permissions?.refresh(fromEvent);
        readReady = true; retryDelay = 1000; clearTimeout(retryTimer); retryTimer = null;
        updateControls(); readError?.remove(); readError = null;
      } catch (error) {
        if (["VIEW_CHANGED", "CONNECTION_CHANGED"].includes(error?.code)) throw error;
        // Withdrawal is the settled, displayed state: it does not keep the event batch failing.
        if (error?.sessionWithdrawn) {withdraw(error.sessionWithdrawn, error); return;}
        readReady = false; updateControls();
        readError?.remove();
        const messagesFailed = messageFailure === error;
        if (!messagesFailed) perspectives?.fail();
        readError = sessionErrorBox(error, {label: t("ui_retry"), run: () => refresh().catch(() => {})},
          {within: messagesFailed ? conversation.box : status, summary: messagesFailed ? t(error?.status === 413 ? "session_err_too_large" : "message_window_failed") : null});
        readError.error = error;
        if (!withdrawn) {
          if (messagesFailed) {status.replaceChildren(); conversation.fail({error: readError});}
          else status.replaceChildren(readError);
        }
        scheduleRetry(); throw error;
      }
    })();
    try { await refreshInFlight; } finally { refreshInFlight = null; }
  };
  // Keep the subscription after a partial initial failure. The view already contains controls;
  // returning here would let later journal pages be acknowledged without refreshing them.
  try { await refresh(); if (!withdrawn) await settleRefreshes([cps.load(), labels.refresh()]); } catch { /* retain the failure and drafts */ }
  const reload = debounceRefresh(() => refresh(true), 500), reloadCps = debounceRefresh(cps.load, 500);
  const off = onEvents(ev => {
    observations.changed(ev);
    const own = observationAffected("session", `${host}/${sid}`, ev);
    // While withdrawn, only this session's own events may bring it back; nothing else repaints.
    if (withdrawn) return own ? reload() : Promise.resolve();
    const affected = own || ["work_item", "integration", "worktree"].includes(ev.resource_type)
      || context?.project && ["project", "operation", "task", "execution"].includes(ev.resource_type);
    const follow = !disposed && followUp && (own || ev.resource_type === "operation" || ev.resource_type === "work_item")
      ? followUp.refresh() : Promise.resolve();
    return (affected ? reload() : Promise.resolve()).then(() => {
      if (withdrawn || disposed) return;
      return settleRefreshes([
        ev.resource_type === "checkpoint" ? reloadCps() : Promise.resolve(),
        ev.resource_type === "operation" ? permissions?.refresh(true) : Promise.resolve(),
        (own || ev.resource_type === "operation") ? labels.refresh(true) : Promise.resolve(),
        instructions?.box.open && (own || ev.resource_type === "operation") ? instructions.refresh() : Promise.resolve(),
        follow
      ]);
    });
  });
  // viewReady flips after this function returns. Permissions and Work actions
  // become available only after the router finishes the complete initial read.
  const syncReady = () => {try {assertView(connection); if (!disposed) updateControls();} catch { /* the view already left */ }};
  const followSync = setTimeout(syncReady, 0);
  const offFollowOnline = onOnline(syncReady);
  return () => {disposed = true; clearTimeout(followSync); offFollowOnline(); followUp?.dispose(); fixedResult?.dispose(); perspectives?.dispose(); clearTimeout(retryTimer); off(); instructions?.dispose(); batHandoff?.dispose(); conversation.dispose(); mobileLayout?.dispose(); shortcut.dispose();
    cps.clear(); results.dispose(); overflow.dispose(); sourcesPanel?.dispose(); wide.removeEventListener("change", syncWide);};
}

// A checkpoint records this session's commit and recent conversation (read-only); continuing starts a new
// managed session at that commit in a connector clone. The person's session and folder are never written.
function checkpointPanel(host, sid, {namespace = state.namespace, manual = () => false} = {}) {
  const can = (state.caps?.features?.checkpoints || []).includes(host) && actionAllowed(state.caps, "checkpoint.create");
  const mayStart = (state.caps?.scopes || []).includes("start") && actionAllowed(state.caps, "checkpoint.continue"); // continuing starts an agent: its own grant
  const list = h("div", {});
  const status = h("div", { class: "muted", "data-checkpoint-status": "" });
  // A checkpoint never changes, so its row is built once: a reload on a new event keeps an open form and its draft.
  const rows = new Map();
  let preview = null; // what the source looks like now (read-only): the commit picker and "source moved on"
  let ticket = 0; // a withdrawn session's late list or preview never paints
  const row = cp => rows.get(cp.checkpoint_id) || rows.set(cp.checkpoint_id, buildRow(cp)).get(cp.checkpoint_id);
  const buildRow = cp => {
    const instr = h("textarea", { placeholder: t("continue_placeholder") });
    const agent = h("select", { "aria-label": t("checkpoint_agent") }, h("option", { value: "claude" }, "Claude"), h("option", { value: "codex" }, "Codex"));
    const out = h("div", { class: "muted session-checkpoint-result" });
    const draft = attachmentDraft(`continue.${cp.checkpoint_id}`, instr, cp.artifacts || []);
    draft.bindFields({agent});
    let expectedHead = null, starting = false;
    const go = h("button", { class: "primary", onclick: async () => {
      const instructions = instr.value.trim() ? instr.value : (draft.refs().length ? t("dispatch_inspect_images") : "");
      if (!instructions || starting) return;
      starting = true;
      go.disabled = false;
      go.setAttribute("aria-disabled", "true");
      out.setAttribute("aria-busy", "true");
      try {
        if (!draft.ready() && !draft.pending()) throw new Error(t("attachments_not_ready"));
        if (state.caps?.artifacts && !expectedHead && !draft.pending()) throw new Error(t("source_unavailable"));
        const op = await draft.perform("checkpoint.continue", { checkpoint_id: cp.checkpoint_id },
            { instructions, agent: agent.value, ...(state.caps?.artifacts ? {artifacts: draft.refs()} : {}) },
            state.caps?.artifacts ? { expected_source_head_sha: expectedHead } : {}, `continue.${cp.checkpoint_id}`);
        // The status and the way into the new session; the operation ID stays in Details.
        const resultHost = op.result?.host || cp.host;
        const opened = op.result?.session_id && resultHost
          ? h("a", {href: `#/session/${encodeURIComponent(resultHost)}/${encodeURIComponent(op.result.session_id)}`}, t("open_new_session")) : null;
        out.replaceChildren(h("p", {class: "session-checkpoint-op"}, statusPill(operationStatusKey(op.status)), opened ? " " : "", opened || ""),
          disclosure({label: t("checkpoint_op_details"), body: h("p", {}, h("a", { href: `#/op/${encodeURIComponent(op.operation_id)}` }, op.operation_id))}));
      } catch (e) { out.replaceChildren(sessionErrorBox(e, {label: t("ui_retry"), run: () => go.click()}, {sourceRefresh:false, within:out})); }
      finally {
        starting = false;
        if (go.isConnected) {
          go.removeAttribute("aria-disabled");
          out.removeAttribute("aria-busy");
          go.disabled = false;
        }
      }
    } }, t("start_agent_work"));
    const limits = disclosure({label: t("checkpoint_limits"), body: confinementNote(host, agent)});
    const form = h("div", { class: "session-checkpoint-form", "data-checkpoint-form": "", hidden: true }, instr, draft.box,
      h("div", { class: "actions" }, agent, go), out, limits);
    const start = h("button", { class: "secondary", type: "button", disabled: !can || !mayStart, "aria-expanded": "false",
      title: !can ? t("session_checkpoint_unavailable") : mayStart ? null : t("needs_start_scope"),
      onclick: async () => { form.hidden = !form.hidden; start.setAttribute("aria-expanded", String(!form.hidden));
        if (state.caps?.artifacts && !form.hidden && !expectedHead) { try { expectedHead = (await api("GET", `/checkpoints/${cp.checkpoint_id}?live=true`)).source.head;
          if (!expectedHead) fill(out, h("p", { class: "error" }, t("source_unavailable"))); } catch (e) { fill(out, sessionErrorBox(e, {label: t("ui_retry"), run: () => load().catch(() => {})}, {within:out})); } }
      } }, t("continue_from_checkpoint"));
    return h("div", { class: "row session-checkpoint-row" },
      h("div", { class: "session-checkpoint-head" },
        h("div", { class: "grow" },
          h("div", { class: "title" }, h("code", {}, cp.commit_sha.slice(0, 12)), " ", cp.branch || ""),
          h("div", { class: "muted" }, timeStamp(cp.captured_at), " · ", [cp.actor, t("excerpt_count", { n: cp.excerpt_messages })].filter(Boolean).join(" · ")),
          cp.dirty ? h("div", { class: "error" }, t("dirty_warning", { n: cp.dirty }))
            : cp.dirty === null ? h("div", { class: "muted" }, t("dirty_unknown")) : null,
          preview && preview.head !== cp.commit_sha ? h("div", { class: "muted" }, t("source_advanced")) : null),
        start),
      form);
  };
  const load = async (mine = ticket) => {
    try {
      const p = new URLSearchParams({ host, session_id: sid, limit: "10" });
      const page = await api("GET", `/checkpoints?${p}`);
      if (mine !== ticket) return;
      list.replaceChildren(...(page.checkpoints.length ? page.checkpoints.map(row)
        : can ? [h("p", { class: "muted" }, t("no_checkpoints"))] : []));
    } catch (e) { if (mine === ticket) list.replaceChildren(sessionErrorBox(e, {label: t("ui_retry"), run: () => load().catch(() => {})}, {within:list})); }
  };
  const pick = h("select", { "aria-label": t("commit"), hidden: true });
  const note = h("textarea", { placeholder: t("checkpoint_note_placeholder"), hidden: true });
  let previewFailed = false, recording = false;
  // Preview and the record handler both decide availability. A preview that finishes mid-record must not disable the button.
  let applyCreateAvailability = () => {};
  const loadPreview = async (mine = ticket) => {
    if (!can) return;
    try {
      const selectedCommit = pick.value;
      const current = (await api("GET", `/sessions/${encodeURIComponent(host)}/${encodeURIComponent(sid)}/checkpoint-preview`)).preview;
      if (mine !== ticket) return;
      if (!Array.isArray(current?.commits)) throw new Error("Invalid checkpoint preview");
      preview = current;
      pick.replaceChildren(...preview.commits.map(c => h("option", { value: c.hash }, `${c.hash.slice(0, 10)} · ${c.message}`)));
      if (preview.commits.some(c => c.hash === selectedCommit)) pick.value = selectedCommit;
      pick.hidden = note.hidden = false;
      const failedBefore = previewFailed;
      previewFailed = false;
      applyCreateAvailability();
      if (failedBefore) status.replaceChildren();
      if (preview.dirty) status.replaceChildren(h("span", { class: "error" }, t("dirty_warning", { n: preview.dirty })));
    } catch (error) {
      // A stale picker must never silently become a request to checkpoint HEAD.
      if (mine !== ticket) return;
      previewFailed = true;
      applyCreateAvailability();
      status.replaceChildren(sessionErrorBox(error, {label: t("ui_retry"), run: () => loadPreview().catch(() => {})}, {within:status}));
    }
  };
  const create = h("button", { class: "secondary", hidden: !can, disabled: !can, onclick: async () => {
    if (recording) return;
    recording = true;
    applyCreateAvailability();
    status.setAttribute("aria-busy", "true");
    try {
      const params = { last_n: 20, ...(preview ? { commit: pick.value } : {}), ...(note.value.trim() ? { note: note.value.trim() } : {}) };
      const op = await submit("checkpoint.create", { host, session_id: sid }, params, {}, `checkpoint.${host}.${sid}`);
      if (op.status === "succeeded") note.value = "";
      status.replaceChildren(...[opStatus(op),
        op.error_code || op.status_reason ? disclosure({label: t("checkpoint_op_details"), body: h("p", {},
          h("code", {}, [op.error_code, op.status_reason].filter(Boolean).join(" · ")))}) : null].filter(Boolean));
      await load();
    } catch (e) { status.replaceChildren(sessionErrorBox(e, {label: t("ui_retry"), run: () => load().catch(() => {})}, {sourceRefresh:false, within:status})); }
    finally {
      const active = document.activeElement;
      const owned = active === create || active === document.body;
      recording = false;
      status.removeAttribute("aria-busy");
      if (create.isConnected) {
        applyCreateAvailability();
        if (owned && (create.disabled || create.hidden)) {status.tabIndex = -1; status.focus();}
      }
    }
  } }, t("checkpoint_record"));
  applyCreateAvailability = () => {
    if (recording) {
      create.disabled = false;
      create.setAttribute("aria-disabled", "true");
      return;
    }
    create.removeAttribute("aria-disabled");
    create.disabled = !can || previewFailed;
  };
  // Recording is a quiet section action; the commit picker opens only when asked for.
  const recordForm = h("div", {class: "session-checkpoint-record", hidden: true, tabindex: -1}, h("div", {class: "actions"}, pick), note, h("div", {class: "actions"}, create));
  const action = can ? button({variant: "ghost", size: "sm", label: t("create_checkpoint"), "aria-expanded": "false", onClick: () => {
    recordForm.hidden = !recordForm.hidden; action.setAttribute("aria-expanded", String(!recordForm.hidden));
    if (!recordForm.hidden) pick.focus();
  }}) : null;
  const notes = h("div", {});
  const paintNotes = () => notes.replaceChildren(...[
    h("p", {class: "muted"}, t("checkpoint_help")),
    manual() ? h("p", {class: "muted"}, t("checkpoint_manual_hint")) : null,
    can ? null : h("p", {class: "muted"}, h("code", {}, t("checkpoint_unavailable_keys"))),
    mayStart ? null : h("p", {class: "muted"}, t("needs_start_scope")),
    mayStart ? null : h("p", {class: "muted"}, h("code", {}, t("needs_start_scope_key")))].filter(Boolean));
  paintNotes();
  const checkpointWhy = disclosure({label: t("checkpoint_notes"), key: `session-checkpoint:${namespace}:${host}:${sid}`, body: notes});
  checkpointWhy.addEventListener("toggle", () => {if (checkpointWhy.open) paintNotes();});
  const box = h("div", {class: "session-checkpoints"},
    can && mayStart ? null : h("p", {class: "muted session-checkpoint-state"}, t(checkpointReason(state.caps, host) || "paths_host_unavailable")),
    recordForm, status, list, checkpointWhy);
  const clear = () => {ticket += 1; rows.clear(); list.replaceChildren(); status.replaceChildren(); preview = null;};
  return { box, action, clear, open: async () => {
    const mine = ticket;
    recordForm.hidden = false; action?.setAttribute("aria-expanded", "true");
    recordForm.scrollIntoView({block: "nearest"});
    if (pick.hidden) await loadPreview(mine);
    if (mine === ticket) (pick.hidden ? recordForm : pick).focus();
  }, load: async () => {const mine = ticket; await loadPreview(mine); if (mine === ticket) await load(mine);} };
}

async function viewDelivery(main, sourceHost, sourceKind, sourceId, sourceRepository) {
  freshPage();
  let reload = async () => {};
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const source = sourceHost && ["execution", "task_command"].includes(sourceKind) && sourceId
    ? {host: sourceHost, kind: sourceKind, id: sourceId} : null;
  // A repository/PR selection can contain private source facts. Legacy globals
  // have no owner proof, so retain them without adopting them into any identity.
  const storageKey = `batc.delivery.${connection.namespace}`;
  const repo = h("input", { "aria-label": t("delivery_repository_input"), placeholder: "owner/name", value: source ? sourceRepository || "" : sessionStorage.getItem(storageKey + ".repo") || "" });
  const num = h("input", { "aria-label": t("delivery_pr_number"), placeholder: "123", inputmode: "numeric", size: 6, value: source ? "" : sessionStorage.getItem(storageKey + ".pr") || "" });
  const card = h("div", { class: "panel delivery-card" });
  const inputStatus = h("p", {class: "muted", role: "status"});
  const acceptLink = () => {
    const pr = parsePullRequest(repo.value);
    if (pr) {repo.value = pr.repository; num.value = pr.number;}
  };
  repo.addEventListener("change", acceptLink);
  const groups = new Map();
  for (const r of state.caps?.deploy_recipes || []) {
    const key = JSON.stringify([r.repository.toLowerCase(), r.environment]);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  const environments = [...groups.values()].map(environmentCard);
  main.append(pageHeader({title: t("nav_delivery"), actions: [iconButton({icon: "refresh-cw", label: t("global_refresh"), onClick: () => reload().catch(() => {})})]}), ...environments.map(e => e.card));
  if (source) main.append(h("p", {class: "note"}, t("delivery_source_context"), " ", h("code", {}, source.id)));
  let selectedMethod = "";
  let reviewedPreview = null;
  const selector = prSelector({
    api, h, t,
    guard: () => assertView(connection),
    repoInput: repo,
    numInput: num,
    onSelect: () => { load(); }
  });
  repo.addEventListener("change", () => selector.loadList());
  main.append(h("h2", {}, t("dep_pull_request")),
    h("form", { class: "filters delivery-controls", onsubmit: event => {event.preventDefault(); acceptLink(); load();} },
      h("label", {}, t("delivery_repository_input"), repo), h("label", {}, t("delivery_pr_number"), num),
      h("button", { type: "submit", class: "secondary" }, t("load_pr"))),
    h("details", {}, h("summary", {}, t("delivery_list_prs") || "List Pull Requests"), selector.container),
    inputStatus, card);
  setTimeout(() => { if (repo.value && !num.value) selector.loadList(); }, 100);
  if (!source && !repo.value && state.caps?.repositories?.length) repo.value = state.caps.repositories[0].repository;
  const load = async (flash = null, fromEvent = false) => {
    const opens = drawerOpens;
    // Explicit PR loads only replace this card; another environment's open panel cannot hold them.
    const holdCard = () => card.querySelector(".drawer:not([hidden])") || (fromEvent && holdRender(true, opens));
    sessionStorage.setItem(storageKey + ".repo", repo.value); sessionStorage.setItem(storageKey + ".pr", num.value);
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo.value) || !/^[1-9]\d{0,8}$/.test(num.value)) {
      inputStatus.textContent = repo.value || num.value ? t("delivery_choose_pr") : ""; return;
    }
    inputStatus.textContent = "";
    try {
      const query = new URLSearchParams();
      if (selectedMethod) query.set("method", selectedMethod);
      if (fromEvent) query.set("from_event", "true");
      const pr = (await api("GET", `/repositories/${repo.value}/pulls/${num.value}?${query}`)).pull_request;
      if (!pr?.merge_preview || !pr?.merge) throw new Error(t("delivery_pr_unavailable"));
      if (holdCard()) { idleReload = () => load(null, true); return; }
      const status = h("div", { "aria-live": "polite" });
      const target = { repository: pr.repository, pull_number: Number(pr.pull_number) };
      if (!fromEvent || !reviewedPreview) reviewedPreview = pr.merge_preview;
      const pv = reviewedPreview;
      const scopeChanged = pr.merge_preview.digest !== pv.digest;
      const pre = { expected_head_sha: pv.target.head_sha, expected_base_sha: pv.target.base_sha, preview_digest: pv.digest };
      const params = { method: pv.method, preview_id: pv.preview_id };
      const run = async (action, extra, scope) => {
        try {
          const op = await submit(action, { ...target, ...extra.target }, extra.params || params, extra.pre ?? pre, scope);
          const receipt = op.result?.merge || op.result || op.external_refs?.merge_receipt;
          fill(status, opStatus(op), " ", h("a", { href: `#/op/${op.operation_id}` }, op.operation_id),
            receipt?.base_moved ? h("p", { class: "note warn" }, t("merged_newer_base", { count: receipt.other_commits_count })) : null);
        } catch (e) { fill(status, errorBox(e, {sourceRefresh:false, within:status})); }
      };
      const blocked = pr.state !== "open" || pr.draft || pr.merged || pv.blocking.length > 0 || scopeChanged;
      const method = h("select", { "aria-label": t("merge_method"), onchange: () => { selectedMethod = method.value; load(); } },
        ...pr.merge.methods.map(m => h("option", { value: m, selected: m === pv.method }, m)));
      const buttons = [h("button", { class: "primary", "data-testid": "merge-submit",
        disabled: blocked || !pr.merge.allowed || !may("merge"),
        onclick: () => run("github.pr.merge", {}, `merge.${pv.preview_id}`) }, t("merge"))];
      for (const r of pr.recipes) {
        buttons.push(h("button", { class: "secondary", disabled: blocked || !pr.merge.allowed || !may("merge") || !may("deploy") || !state.caps?.deploy_recipes?.find(c => c.name === r.name)?.readiness?.ready,
          onclick: async () => {
            try {
              const deploymentPreview = (await api("GET", `/deployments/preview?recipe=${encodeURIComponent(r.name)}`)).preview;
              await run("delivery.merge_and_deploy", { target: { recipe: r.name }, pre: { ...pre, ...deploymentPreview.preconditions } }, `merge_deploy.${pv.preview_id}.${r.name}`);
            } catch (e) { fill(status, errorBox(e, {within:status})); }
          } },
        t("merge_and_deploy_to", { env: r.environment })));
        if (pr.merged && pr.merge_commit_sha) buttons.push(h("button", { class: "secondary", disabled: !may("deploy") || !state.caps?.deploy_recipes?.find(c => c.name === r.name)?.readiness?.ready,
          onclick: async () => {
            let sourceRefresh = true;
            try {
              const deploymentPreview = (await api("GET", `/deployments/preview?recipe=${encodeURIComponent(r.name)}`)).preview;
              const preconditions = deploymentPreview.preconditions;
              sourceRefresh = false;
              const op = await submit("deployment.start", { recipe: r.name }, { source_sha: pr.merge_commit_sha }, preconditions, `deploy.${r.name}.${pr.merge_commit_sha}`);
              fill(status, opStatus(op), " ", h("a", { href: `#/op/${op.operation_id}` }, op.operation_id));
            } catch (e) { fill(status, errorBox(e, {sourceRefresh, within:status})); }
          } }, t("deploy_to", { env: r.environment })));
      }
      const commitList = h("details", { class: "row-details" }, h("summary", {}, t("merge_commit_range", { count: pv.commits.length })),
        h("ul", {}, ...pv.commits.map(c => h("li", {}, h("code", {}, c.sha), " ", c.message))));
      const affected = pv.affected_prs.map(p => h("div", { class: "row" },
        h("div", { class: "grow" }, h("a", { href: p.html_url, target: "_blank", rel: "noopener" }, `#${p.number} ${p.title || ""}`),
          " · ", t("scope_" + p.reason), h("div", {}, h("code", {}, p.head_sha || ""))),
        chip(t(p.effect === "branch_rebase" ? "scope_stack_rebase" : p.effect === "dependency" ? "scope_dependency" : p.would_merge ? "scope_would_merge" : "scope_candidate"), p.would_merge ? "warn" : "")));
      card.querySelector("[data-integration-review]")?.closeReview?.();
      fill(card,
        h("h2", {}, h("a", { href: pr.html_url, target: "_blank", rel: "noopener" }, `#${pr.pull_number} ${pr.title || ""}`)),
        h("dl", { class: "kv" },
          h("dt", {}, t("head")), h("dd", {}, h("code", {}, `${pr.head_ref} @ ${pr.head_sha}`)),
          h("dt", {}, t("base")), h("dd", {}, h("code", {}, `${pr.base_ref} @ ${pr.base_sha}`)),
          h("dt", {}, t("mergeable")), h("dd", {}, `${pr.state}${pr.draft ? " · draft" : ""} · ${pr.mergeable_state || "?"}`),
          h("dt", {}, t("checks")), h("dd", {}, t("checks_summary", pr.checks)),
          pr.merged ? [h("dt", {}, t("merged_sha")), h("dd", {}, h("code", {}, pr.merge_commit_sha))] : null),
        metadataDrawer(pr, load),
        scopeChanged ? h("p", { class: "note warn" }, t("merge_scope_reload")) : null,
        h("h2", {}, t("merge_scope")), h("label", {}, t("merge_method"), " ", method),
        h("p", { class: "muted" }, t("merge_preview_fixed"), " ", h("code", {}, pv.preview_id)), commitList,
        affected.length ? h("div", {}, ...affected) : h("p", { class: "muted" }, t("scope_single_pr")),
        ...pv.blocking.map(b => h("p", { class: "note warn" }, h("code", {}, b.code), " · ", b.message)),
        ...pv.warnings.map(w => h("p", { class: "muted" }, w)),
        h("div", { class: "actions" }, ...buttons), status, flash instanceof Node ? flash : null,
        pr.integration?.allowed ? integrationPanel(pr, load, source) : null);
    } catch (e) { if (!holdCard()) fill(card, errorBox(e, {within:card})); }
  };
  reload = async (fromEvent = true) => { await settleRefreshes([...environments.map(e => e.load(fromEvent)), load(null, fromEvent)]); };
  await reload(false);
  return liveReload(reload, ["operation", "integration", "deployment", "deployment_environment"]);
}

function deliveryWork(item, repositories = [], compact = false) {
  return renderDeliveryWork(item, repositories, compact, {h, caps: state.caps, namespace: state.namespace});
}

function deploymentIdentity(identity, empty = "dep_no_version") {
  if (!identity?.source_sha && !identity?.artifact_id) return emptyState({title:t(empty)});
  return h("div", { class: "deployment-identity" },
    identity.source_sha ? h("code", {}, identity.source_sha) : null,
    identity.artifact_id ? h("p", {}, t("dep_artifact", { id: identity.artifact_id }),
      identity.artifact_digest ? [" · ", h("code", {}, identity.artifact_digest)] : null) : null);
}
function deploymentState(value) {
  return chip(t(`dep_state_${value || "unverified"}`), ["failed", "needs_attention", "uncertain"].includes(value) ? "bad"
    : value === "succeeded" ? "ok" : "warn");
}
function deploymentTime(value) { return value ? when(Number(value) * 1000) : t("dep_not_observed"); }
function heldDetails(title, children, onOpen) {
  let counted = false;
  const details = h("details", { class: "row-details", ontoggle: () => {
    if (details.open !== counted) {
      counted = details.open;
      if (counted) { drawerOpens += 1; if (onOpen) onOpen(); }
      setEditing(editing + (counted ? 1 : -1));
    }
  } }, h("summary", {}, title), ...children);
  details.closeHeld = () => {
    if (counted) { counted = false; setEditing(editing - 1); }
    details.open = false;
  };
  return details;
}
function deploymentReceipt(dep) {
  const body = h("div", {});
  let read = false;
  const details = heldDetails(t("dep_operation_details"), [body], async () => {
    if (read) return;
    read = true;
    try {
      const op = (await api("GET", `/operations/${dep.operation_id}`)).operation;
      const saved = op.external_refs?.deployment_id && op.external_refs.deployment_id !== dep.deployment_id
        ? (await api("GET", `/deployments/${op.external_refs.deployment_id}`)).deployment : dep;
      const values = [[t("dep_operation"), h("a", { href: `#/op/${op.operation_id}` }, op.operation_id)],
        [t("dep_status"), opStatus(op)], [t("dep_run"), saved.run_id], [t("dep_attempt"), saved.run_attempt],
        [t("dep_error_code"), op.error_code || saved.error_code || saved.reconciliation_error]];
      fill(body, h("dl", { class: "kv" }, ...values.filter(([, v]) => v !== null && v !== undefined && v !== "")
        .flatMap(([label, value]) => [h("dt", {}, label), h("dd", {}, value)])),
        op.status_reason ? h("p", {}, op.status_reason) : null,
        ...(op.steps || []).map(s => h("div", { class: "row" }, h("code", {}, s.name), h("span", {}, operationStatusLabel(s.status)))));
    } catch (e) { fill(body, errorBox(e, {within:body})); read = false; }
  });
  return details;
}
function deploymentLimits(limits) {
  return h("div", { class: "deployment-limits" }, h("span", { class: "muted" }, t("dep_not_undone")),
    limits?.length ? h("ul", {}, ...limits.map(value => h("li", {}, value))) : h("p", { class: "muted" }, t("dep_no_limits")));
}
function deploymentPreviewSummary(preview) {
  return h("p", { class: "muted", "data-testid": "deployment-preview-generation" },
    t("dep_preview_generation", { env: preview.environment, generation: preview.environment_generation }));
}
function deploymentIntent(dep, kind, reload) {
  const connection = { epoch: state.epoch, namespace: state.namespace };
  const out = h("div", { "aria-live": "polite" });
  const d = drawer();
  let busy = false, accepted = false, preview = null;
  const scope = `deployment.${kind}.${dep.deployment_id}`;
  const confirm = h("button", { class: kind === "rollback" ? "danger" : "primary", disabled: true,
    "data-testid": `deployment-${kind}-confirm`, onclick: async () => {
      if (busy || accepted || !preview) return;
      busy = true; confirm.disabled = true;
      try {
        assertConnection(connection);
        const action = kind === "rollback" ? "deployment.rollback" : "deployment.start";
        const params = kind === "rollback" ? { deployment_id: dep.deployment_id }
          : { source_sha: dep.identity.source_sha, retry_of: dep.deployment_id };
        let op = await submit(action, { recipe: dep.recipe }, params, preview.preconditions, scope);
        while (["accepted", "running"].includes(op.status) && d.box.isConnected && !d.box.hidden) {
          await sleep(1000);
          assertConnection(connection);
          if (!d.box.isConnected || d.box.hidden) break;
          op = (await api("GET", `/operations/${op.operation_id}`)).operation;
        }
        if (["DEPLOY_PREVIEW_REQUIRED", "ENVIRONMENT_CHANGED", "RECIPE_CHANGED"].includes(op.error_code)) {
          fill(out, h("p", { class: "note warn" }, t("dep_stale_preview")), deploymentReceipt({ operation_id: op.operation_id }));
          await refreshPreview();
        } else {
          accepted = true;
          fill(out, h("p", {}, opStatus(op), " · ", h("a", { href: `#/op/${op.operation_id}` }, t("dep_open_operation"))),
            deploymentReceipt({ operation_id: op.operation_id }));
        }
      } catch (e) {
        fill(out, h("p", { class: "error" }, t("dep_refused")), heldDetails(t("dep_operation_details"), [errorBox(e, {sourceRefresh:false, insideDetails:true})]));
        if (["DEPLOY_PREVIEW_REQUIRED", "ENVIRONMENT_CHANGED", "RECIPE_CHANGED"].includes(e.code)) {
          out.prepend(h("p", { class: "note warn" }, t("dep_stale_preview")));
          await refreshPreview();
        }
      } finally { busy = false; confirm.disabled = accepted || !preview?.readiness?.ready; }
    } }, t(kind === "rollback" ? "dep_confirm_rollback" : "dep_confirm_retry"));
  const previewBox = h("div", {});
  async function refreshPreview() {
    preview = null;
    confirm.disabled = true;
    try {
      assertConnection(connection);
      preview = (await api("GET", `/deployments/preview?recipe=${encodeURIComponent(dep.recipe)}`)).preview;
      fill(previewBox, deploymentPreviewSummary(preview),
        preview.readiness?.ready ? null : h("p", { class: "note warn" }, t("dep_missing_verification")));
      confirm.disabled = busy || accepted || !preview.readiness?.ready;
    } catch (e) { fill(previewBox, errorBox(e, {within:previewBox})); }
  }
  fill(d.box, h("h3", {}, t(kind === "rollback" ? "dep_rollback_review" : "dep_retry_review")),
    deploymentIdentity(dep.identity), h("p", {}, `${dep.repository} · ${dep.environment}`),
    kind === "rollback" ? deploymentLimits(dep.rollback?.not_undone) : h("p", { class: "muted" }, t("dep_retry_only")),
    previewBox, out, h("div", { class: "actions" }, confirm,
      h("button", { class: "secondary", onclick: () => {
        d.box.querySelectorAll("details[open]").forEach(el => el.closeHeld?.());
        d.close(); reload();
      } }, t("close"))));
  const button = h("button", { class: "secondary", "data-testid": `deployment-${kind}`, onclick: () => {
    if (!d.box.hidden) return;
    d.open(); refreshPreview();
  } }, t(kind === "rollback" ? "dep_rollback" : "dep_retry", { sha: (dep.identity?.source_sha || "").slice(0, 8) }));
  return { button, box: d.box };
}
function deploymentRecord(dep, env, reload, compact = false) {
  if (!dep) return h("p", { class: "muted" }, t("dep_no_version"));
  const r = (state.caps?.deploy_recipes || []).find(r => r.name === dep.recipe);
  dep = { ...dep, repository: dep.repository || r?.repository || "",
    environment: dep.environment || r?.environment || env.environment || t("dep_no_version") };
  const canDeploy = Boolean(state.caps?.features?.deploy && r?.readiness?.ready
    && (state.caps?.actions || []).some(a => a.action === "deployment.start" && a.allowed) && may("deploy"));
  const disabledReason = !may("deploy") ? "dep_needs_scope" : !r ? "dep_missing_recipe"
    : !r.readiness?.ready ? "dep_missing_verification" : "dep_disabled";
  const actions = [], drawers = [];
  if (!compact && dep.rollback_eligible && !dep.is_current) {
    const intent = deploymentIntent(dep, "rollback", reload);
    intent.button.disabled = !canDeploy || !(state.caps?.actions || []).some(a => a.action === "deployment.rollback" && a.allowed);
    actions.push(intent.button); drawers.push(intent.box);
    if (intent.button.disabled) actions.push(h("span", { class: "muted" }, t(disabledReason)));
  }
  if (!compact && dep.state === "failed") {
    if (dep.provider_terminal && !dep.legacy && dep.identity?.source_sha) {
      const intent = deploymentIntent(dep, "retry", reload);
      intent.button.disabled = !canDeploy;
      actions.push(intent.button); drawers.push(intent.box);
      if (!canDeploy) actions.push(h("span", { class: "muted" }, t(disabledReason)));
    } else actions.push(h("span", { class: "muted" }, t(dep.provider_terminal ? "dep_retry_unavailable" : "dep_provider_pending")));
  }
  let rollbackReason = dep.rollback_reason;
  let providerUrl = null;
  try {
    const url = new URL(dep.provider_url);
    if (url.protocol === "https:") providerUrl = url.href;
  } catch { /* malformed and relative provider links are not navigable */ }
  if (rollbackReason === "ROLLBACK_ARTIFACT_UNAVAILABLE" && dep.identity?.artifact_expires_at
      && Date.parse(dep.identity.artifact_expires_at) <= Date.now()) rollbackReason = "ROLLBACK_ARTIFACT_EXPIRED";
  return h("div", { class: "deployment-record", "data-deployment": dep.deployment_id },
    h("div", { class: "deployment-record-heading" }, deploymentState(dep.state), h("span", { class: "muted" }, dep.recipe)),
    deploymentIdentity(dep.identity),
    compact ? h("p", {}, h("a", { href: `#/op/${dep.operation_id}` }, t("dep_open_operation")))
      : h("p", { class: "muted" }, dep.environment, ' · ', deploymentTime(dep.created_at)),
    dep.state === "superseded" ? h("p", {}, t("dep_superseded"), " ",
      env.desired ? h("a", { href: `#/op/${env.desired.operation_id}` }, t("dep_new_desired")) : null, " · ",
      providerUrl ? h("a", { href: providerUrl, target: "_blank", rel: "noopener" }, t("dep_provider_run")) : null) : null,
    (dep.state === "needs_attention" || dep.reconciliation_error) ? h("p", { class: "note warn" }, t("dep_attention")) : null,
    !compact && rollbackReason ? h("p", { class: "muted" }, t(`dep_${rollbackReason}`)) : null,
    !compact && dep.rollback_eligible && !dep.is_current ? deploymentLimits(dep.rollback?.not_undone) : null,
    actions.length ? h("div", { class: "actions" }, ...actions) : null,
    deploymentReceipt(dep), ...drawers);
}
function environmentCard(group) {
  const card = h("section", { class: "panel delivery-card environment-card", "data-environment": group[0].environment });
  const cursors = [null];
  let page = 0, next = null, loading = false, inFlight = null;
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  async function load(fromEvent = false) {
    // A journal invalidation arriving during pagination must join that read, then read
    // again: the first request may have observed the environment before this event.
    if (inFlight) { await inFlight; if (fromEvent) return load(true); return; }
    const opens = drawerOpens;
    if (editing || (fromEvent && typing())) { idleReload = () => load(true); return; }
    loading = true;
    inFlight = (async () => {
    try {
      assertView(connection);
      const query = new URLSearchParams({ recipe: group[0].name, limit: "5" });
      if (cursors[page]) query.set("cursor", cursors[page]);
      const reads = await Promise.allSettled([
        api("GET", `/deployment-environments?recipe=${encodeURIComponent(group[0].name)}`),
        api("GET", `/deployment-environments/history?${query}`)]);
      assertView(connection);
      for (const read of reads) if (read.status === "rejected") throw read.reason;
      const [environment, history] = reads.map(read => read.value);
      if (holdRender(fromEvent, opens) || editing) { idleReload = () => load(true); return; }
      const env = environment.environment;
      next = history.next_cursor;
      const observed = env.observed || env.current?.evidence?.runtime?.observed;
      const needs = env.attention || env.desired?.state === "needs_attention" || env.desired?.reconciliation_error
        || env.current?.state === "needs_attention" || env.current?.reconciliation_error;
      const previous = h("button", { class: "secondary", disabled: page === 0, "data-testid": "history-previous",
        onclick: () => { if (loading) return; previous.disabled = more.disabled = true; page -= 1; load(); } }, t("dep_previous"));
      const more = h("button", { class: "secondary", disabled: !next, "data-testid": "history-next",
        onclick: () => { if (loading) return; previous.disabled = more.disabled = true; cursors[++page] = next; load(); } }, t("dep_next"));
      const verified = env.last_verified;
      fill(card, h("div", { class: "deployment-card-heading" }, h("h2", {}, group[0].environment),
        needs ? chip(t("dep_attention"), "warn") : null), h("p", { class: "muted" }, group[0].repository, " · ", t("dep_generation", { generation: env.desired_generation })),
        needs ? h("p", { class: "note warn" }, t(env.attention === "ENVIRONMENT_VERSION_DRIFT" ? "dep_drift" : "dep_attention_help")) : null,
        h("div", { class: "deployment-versions" },
          h("div", {}, h("h3", {}, t("dep_desired")), deploymentRecord(env.desired, env, load, true)),
          h("div", {}, h("h3", {}, t("dep_observed")), deploymentIdentity(observed),
            h("p", { class: "muted" }, timeLabel(t, "dep_observed_at", { time: deploymentTime(observed?.observed_at || env.current?.evidence?.runtime?.checked_at) })),
            env.current?.evidence?.runtime?.summary ? h("p", { class: "muted" }, runtimeSummary(env.current.evidence.runtime)) : null)),
        h("div", { class: "deployment-last-verified" }, h("h3", {}, t("dep_last_verified")), deploymentIdentity(verified?.identity),
          h("p", { class: "muted" }, timeLabel(t, "dep_verified_at", { time: deploymentTime(verified?.evidence?.runtime?.checked_at) })),
          verified?.evidence?.runtime?.summary ? h("p", { class: "muted" }, runtimeSummary(verified.evidence.runtime)) : null,
          verified ? deploymentReceipt(verified) : null),
        h("h3", {}, t("dep_history")), h("div", { "data-testid": "deployment-history" },
          ...(history.items.length ? history.items.map(dep => deploymentRecord(dep, env, load)) : [h("p", { class: "muted" }, t("dep_no_history"))])),
        !history.items.length && group.every(r => !r.rollback?.supported) ? h("p", { class: "muted" }, t("dep_ROLLBACK_UNSUPPORTED")) : null,
        h("div", { class: "actions deployment-pagination" }, previous, h("span", { class: "muted" }, t("dep_page", { page: page + 1 })), more));
    } catch (e) {
      if (["CONNECTION_CHANGED", "VIEW_CHANGED"].includes(e.code)) return;
      const failure = errorBox(e, {within:card}); // mark the whole acknowledgment batch even under an open drawer
      if (!holdRender(fromEvent, opens) && !editing) fill(card, failure);
    }
    })();
    try { await inFlight; } finally { loading = false; inFlight = null; }
  }
  return { card, load };
}
function runtimeSummary(evidence) {
  return t(evidence.version_checked ? evidence.health_checked ? "dep_version_health" : "dep_version_only" : "dep_health_only");
}

function metadataDrawer(pr, reload) {
  const title = h("input", { value: pr.title || "", "data-testid": "metadata-title" });
  const body = h("textarea", { "data-testid": "metadata-body" }, pr.body || "");
  const initialBody = body.value; // textarea normalizes CRLF; an untouched body must still be omitted
  const out = h("div", { "aria-live": "polite" });
  let d;
  const save = h("button", { class: "primary", "data-testid": "metadata-save", onclick: async () => {
    const params = {};
    if (title.value !== (pr.title || "")) params.title = title.value;
    if (body.value !== initialBody) params.body = body.value;
    if (!Object.keys(params).length) return;
    save.disabled = true;
    try {
      const op = await submit("github.pr.update", { repository: pr.repository, pull_number: pr.pull_number }, params,
        { expected_metadata_digest: pr.metadata_digest }, `pr_metadata.${pr.repository}.${pr.pull_number}.${pr.metadata_digest}`);
      const diff = op.external_refs?.metadata_difference;
      const contents = diff ? h("details", { class: "row-details", open: true }, h("summary", {}, t("metadata_diff")),
        ...[["metadata_before", diff.observed ? diff.before : { title: pr.title, body: pr.body }],
          ["metadata_intended", diff.after || diff.intended], ["metadata_observed", diff.observed || diff.before]]
          .map(([label, value]) => h("div", {}, h("strong", {}, t(label)), h("pre", { class: "pre" }, JSON.stringify(value, null, 2))))) : null;
      fill(out, opStatus(op), " ", h("a", { href: `#/op/${op.operation_id}` }, op.operation_id),
        op.error_code ? chip(op.error_code, "bad") : null, contents,
        h("p", { class: "muted" }, t("metadata_result_help")));
      idleReload = () => reload();
    } catch (e) { fill(out, errorBox(e, {sourceRefresh:false, within:out})); save.disabled = false; }
  } }, t("save"));
  const close = h("button", { class: "secondary", "data-testid": "metadata-close", onclick: () => d.close() }, t("close"));
  d = drawer(h("label", {}, t("metadata_title"), title), h("label", {}, t("description"), body),
    h("p", { class: "muted" }, t("metadata_race_limit")), h("div", { class: "actions" }, save, close), out);
  return h("div", {}, h("button", { class: "secondary", "data-testid": "metadata-edit",
    disabled: !pr.metadata_update.allowed || !may("integrate"),
    title: !may("integrate") ? t("needs_integrate_scope") : !pr.metadata_update.allowed ? t("metadata_disabled") : null,
    onclick: () => d.open() }, t("metadata_edit")), d.box);
}

// "Update PR results": put chosen results into this PR's head branch with one normal push (never forced; the
// person's folders are never touched). Sources come only from a preview that lists every commit that would enter.
function integrationPanel(pr, reloadCard, source = null) {
  const box = h("div", { class: "panel", "data-integration-review": true });
  const may = (state.caps?.scopes || []).includes("integrate");
  const hosts = pr.integration.hosts;
  box.append(h("h2", {}, t("update_pr_results")), h("p", { class: "muted" }, t("update_pr_help")));
  if (!may) { box.append(h("p", { class: "note" }, t("needs_integrate_scope"))); return box; }
  if (!hosts.length) { box.append(h("p", { class: "note" }, t("integration_no_host"))); return box; }
  if (source && (!hosts.includes(source.host) || state.caps?.features?.execution_delivery?.version !== 1)) {
    box.append(h("p", {class: "note"}, t("delivery_source_unavailable"))); return box;
  }
  const target = { host: source?.host || hosts[0], repository: pr.repository, pull_number: Number(pr.pull_number) };
  const selected = []; // [{kind, id, label}] in order
  let holdingReview = false;
  box.closeReview = () => {if (holdingReview) {holdingReview = false; setEditing(editing - 1);}};
  const holdReview = () => {
    if (selected.length && !holdingReview) {holdingReview = true; drawerOpens++; setEditing(editing + 1);}
    else if (!selected.length) box.closeReview();
  };
  const pickList = h("div", {});
  const order = h("div", {});
  const previewBox = h("div", {});
  const status = h("div", {});
  let doc = null;
  let generation = 0;
  const rerender = () => {
    holdReview();
    order.replaceChildren(...selected.map((s, i) => h("div", { class: "row" },
      h("div", { class: "grow" }, `${i + 1}. `, chip(t("kind_" + s.kind)), " ", h("code", {}, s.label)),
      h("button", { class: "secondary", disabled: i === 0, onclick: () => { selected.splice(i - 1, 0, selected.splice(i, 1)[0]); changed(); } }, "↑"),
      h("button", { class: "secondary", disabled: i === selected.length - 1, onclick: () => { selected.splice(i + 1, 0, selected.splice(i, 1)[0]); changed(); } }, "↓"),
      h("button", { class: "secondary", onclick: () => { selected.splice(i, 1); changed(); } }, "×"))));
  };
  const freshHead = async () => { // the card's head is stale after the PR moved: re-read it before previewing again
    try { pr.head_sha = (await api("GET", `/repositories/${pr.repository}/pulls/${pr.pull_number}`)).pull_request.head_sha; }
    catch { /* the preview then reports the real head */ }
  };
  const runPreview = async () => {
    const mine = ++generation;
    doc = null;
    if (!selected.length) { previewBox.replaceChildren(); return; }
    previewBox.replaceChildren(h("p", { class: "muted" }, t("previewing")));
    try {
      let op = await submit("integration.preview", target, { sources: selected.map(({ kind, id }) => ({ kind, id })) },
        { expected_head_sha: pr.head_sha }, `preview.${pr.repository}.${pr.pull_number}`);
      while (!TERMINAL.includes(op.status) && op.status !== "needs_attention" && box.isConnected && mine === generation) {
        await sleep(1000);
        op = (await api("GET", `/operations/${op.operation_id}`)).operation;
      }
      if (mine !== generation || !box.isConnected) return;
      if (op.status !== "succeeded") {
        previewBox.replaceChildren(h("p", { class: "error" }, `${op.error_code || op.status} ${op.status_reason || ""}`));
        return;
      }
      doc = op.result;
      renderPreview();
    } catch (e) { if (mine === generation) previewBox.replaceChildren(errorBox(e, {sourceRefresh:false, within:previewBox})); }
  };
  const changed = debounce(() => { rerender(); runPreview(); }, 600);
  const add = (kind, id, label) => {
    if (!selected.some(s => s.kind === kind && s.id === id)) selected.push({ kind, id, label });
    holdReview();
    changed();
  };
  const plain = w => h("li", {}, w.text);
  const renderPreview = () => {
    const expired = Date.now() / 1000 > doc.expires_at || doc.target.head_sha !== pr.head_sha;
    const conflictAt = doc.sources.find(s => s.predicted === "conflict");
    const go = h("button", { class: "primary", disabled: !doc.ready || expired, onclick: async () => {
      go.disabled = true;
      try {
        const req = { action: "integration.apply", target, params: { preview_id: doc.preview_id },
          preconditions: { expected_head_sha: doc.target.head_sha, preview_digest: doc.digest } };
        follow((await api("POST", "/operations?wait=3", req, "integrate." + doc.preview_id)).operation);
      } catch (e) { status.replaceChildren(errorBox(e, {sourceRefresh:false, within:status})); go.disabled = false; }
    } }, conflictAt ? t("start_integration_conflict", { n: conflictAt.seq }) : t("update_pr_results"));
    previewBox.replaceChildren(...[
      h("p", {}, h("code", {}, `${doc.repository} #${doc.pull_number} · ${doc.target.head_ref} @ ${doc.target.head_sha.slice(0, 12)}`),
        " → ", t("normal_push"), " · ", t("push_access_" + doc.target.push_access)),
      ...doc.sources.map(s => h("details", { class: "row-details" },
        h("summary", {}, `${s.seq}. `, chip(t("plan_" + (s.predicted || "not_predicted")), s.predicted === "conflict" ? "bad" : ""),
          " ", h("code", {}, s.label), " · ", t("n_commits", { n: s.commits_total }), " · ", t("n_files", { n: s.files_total }),
          s.conflict_files.length ? h("span", { class: "error" }, " · ", s.conflict_files.join(", ")) : null),
        h("ul", {}, ...s.commits.map(c => h("li", {}, h("code", {}, c.sha.slice(0, 10)), ` ${c.subject} — ${c.author}`,
          c.origin === "foreign" ? h("span", { class: "error" }, " · ", t("foreign_commit")) : null))),
        s.warnings.length ? h("ul", { class: "muted" }, ...s.warnings.map(plain)) : null)),
      doc.overlaps.length ? h("p", { class: "muted" }, t("overlapping_files"), " ",
        doc.overlaps.map(o => `${o.path} (${o.seqs.join(", ")}${o.also_changed_on_pr ? ", PR" : ""})`).join("; ")) : null,
      doc.blocking.length ? h("ul", { class: "error" }, ...doc.blocking.map(plain)) : null,
      doc.warnings.length ? h("ul", { class: "muted" }, ...doc.warnings.map(plain)) : null,
      h("p", { class: "muted" }, expired ? t("preview_expired") : timeLabel(t, "previewed_at", { time: when(epoch(doc.observed_at)) }),
        " ", h("a", { href: "#", onclick: ev => { ev.preventDefault(); runPreview(); } }, t("preview_again"))),
      h("div", { class: "actions" }, go)].filter(Boolean)); // replaceChildren would print a null as "null"
  };
  const follow = async op => {
    for (;;) {
      if (!box.isConnected) return;
      status.replaceChildren(integrationStatus(op, {
        resume: async () => {
          try { follow((await api("POST", `/operations/${op.operation_id}/resume`, {})).operation); }
          catch (e) { status.append(errorBox(e, {sourceRefresh:false, within:status})); }
        },
        cancel: async () => { await api("POST", `/operations/${op.operation_id}/cancel`, {}); await freshHead(); runPreview(); } }));
      if (TERMINAL.includes(op.status) || op.status === "needs_attention") break;
      await sleep(1500);
      op = (await api("GET", `/operations/${op.operation_id}`)).operation;
    }
    if (op.status === "succeeded") {box.closeReview(); reloadCard(integrationStatus(op, {}));} // the card shows the new head
    else if (["TARGET_HEAD_CHANGED", "SOURCE_CHANGED"].includes(op.error_code)) { await freshHead(); runPreview(); }
  };
  const branch = h("input", { placeholder: t("branch_on_github") });
  (async () => {
    try {
      const query = new URLSearchParams({host: target.host});
      if (source) {query.set("source_kind", source.kind); query.set("source_id", source.id);}
      const c = await api("GET", `/integrations/candidates?${query}`);
      const row = (kind, id, label, chips, eligible = true) => h("div", { class: "row", "data-delivery-source": id },
        h("div", { class: "grow" }, h("code", {}, label), " ", ...chips),
        h("button", { class: "secondary", disabled: !eligible, onclick: () => add(kind, id, label) }, t("add")));
      const delivered = x => (x.delivered_to || []).map(receipt => receipt.repository
        ? h("a", {href: `https://github.com/${receipt.repository}/pull/${receipt.pull_number}`, target: "_blank", rel: "noopener",
          title: `${receipt.repository} · ${receipt.pinned_sha || ""} → ${receipt.delivered_sha || ""}`}, t("delivered_to", {n: receipt.pull_number}))
        : chip(t("delivered_to", {n: receipt.pull_number})));
      const evidence = r => {
        const session = r.session || {streaming: r.streaming}, activity = sessionActivity(session);
        return [chip(t(activity.key), activity.tone),
          session.pending && runtimeStale(session) ? chip(t("sessions_stale"), "stale") : null,
          h("p", {class: "muted"}, t("delivery_result_unverified")),
          h("p", {class: "muted"}, session.observed_at
            ? timeLabel(t, "delivery_observed_at", {time: when(typeof session.observed_at === "number" ? epoch(session.observed_at) : session.observed_at)}) : t("delivery_unobserved")),
          r.unavailable ? h("p", {class: "note"}, t("delivery_source_unavailable")) : null,
          r.unavailable ? errorDetails(typeof r.unavailable === "object" ? r.unavailable : {code: String(r.unavailable)}) : null,
          ...delivered(r)].filter(Boolean);
      };
      const results = source ? c.selected && c.selected.kind === source.kind && c.selected.id === source.id && c.selected.host === source.host
        ? [c.selected] : [] : c.agent_results;
      pickList.replaceChildren(
        h("h3", {}, t("agent_results")),
        ...(results.length ? results.map(r => row(r.kind || "checkpoint_run", r.id, r.branch || r.id,
          evidence(r), r.eligible !== false))
          : [h("p", { class: "muted" }, t("none"))]),
        h("h3", {}, t("your_checkpoints")),
        ...(c.checkpoints.length ? c.checkpoints.map(r => row("checkpoint", r.id, `${r.branch || "?"} @ ${r.commit_sha.slice(0, 10)}`,
          [r.note ? h("span", { class: "muted" }, r.note.slice(0, 60)) : null, ...delivered(r)]))
          : [h("p", { class: "muted" }, t("none"))]),
        h("div", { class: "actions" }, branch, h("button", { class: "secondary", onclick: () => {
          if (branch.value.trim()) add("branch", branch.value.trim(), branch.value.trim());
          branch.value = "";
        } }, t("add_branch"))));
    } catch (e) { pickList.replaceChildren(errorBox(e, {within:pickList})); }
  })();
  box.append(pickList, h("h3", {}, t("selected_in_order")), order, previewBox, status);
  return box;
}

function repairControl(op) {
  const conflict = ["INTEGRATION_CONFLICT", "RESOLUTION_INCOMPLETE", "RESOLUTION_INVALID"].includes(op.error_code);
  const out = h("div", {"data-handoff-status": ""});
  let handoff = null;
  let repair = null;
  if (conflict && (state.caps?.scopes || []).includes("start")) {
    const agent = h("select", { "aria-label": t("agent") },
      h("option", { value: "claude" }, "Claude"), h("option", { value: "codex" }, "Codex"));
    let starting = false;
    const go = h("button", { class: "secondary", onclick: async () => {
      if (starting) return;
      starting = true;
      go.disabled = false;
      go.setAttribute("aria-disabled", "true");
      out.setAttribute("aria-busy", "true");
      try {
        const o = await submit("integration.handoff", { operation_id: op.operation_id }, { agent: agent.value }, {},
          `handoff.${op.operation_id}`);
        out.append(h("p", {}, opStatus(o), " ", t("handoff_started"), " ",
          h("a", { href: `#/op/${o.operation_id}` }, o.operation_id)));
      } catch (e) { out.append(errorBox(e, {sourceRefresh:false, within:out})); }
      finally {
        starting = false;
        if (go.isConnected) {
          go.removeAttribute("aria-disabled");
          out.removeAttribute("aria-busy");
          go.disabled = false;
        }
      }
    } }, t("start_agent_work"));
    const d = drawer(confinementNote(op.target?.host || op.external_refs?.host, agent),
      h("div", { class: "actions" }, agent, go));
    repair = d.box;
    handoff = h("button", { class: "secondary", onclick: () => d.toggle.click() }, t("hand_to_agent"));
  }
  if (handoff) out.append(handoff, repair);
  return out;
}

function integrationStatus(op, act) {
  const code = op.error_code;
  const text = op.status === "succeeded"
    ? t("integration_done", { old: op.result.old_head.slice(0, 7), new: op.result.new_head.slice(0, 7),
      n: op.result.added_commits ?? "?" })
    : op.status === "needs_attention" ? (t("integration_" + code) !== "integration_" + code ? t("integration_" + code) : op.status_reason)
      : op.status === "uncertain" ? t("integration_uncertain")
        : op.status === "waiting_external" ? ((op.external_refs || {}).conflict ? t("integration_waiting_resolver")
          : (op.external_refs || {}).pushed_sha ? t("integration_waiting") : op.status_reason || t("integration_running"))
          : op.status === "failed" ? `${code}: ${op.status_reason || ""}` : t("integration_running");
  const out = h("div", {});
  const handoff = repairControl(op);
  const buttons = op.status === "needs_attention" ? [
    h("button", { class: "primary", onclick: act.resume }, t("resume")), handoff,
    h("button", { class: "danger", onclick: act.cancel }, t("cancel_and_preview"))].filter(Boolean) : [];
  out.append(h("p", {}, opStatus(op), " ", text, " ", h("a", { href: `#/op/${op.operation_id}` }, op.operation_id)),
    h("div", { class: "actions" }, ...buttons));
  return out;
}

async function viewOperations(main, filter = "all") {
  const selected = ["all", "attention", "active"].includes(filter) ? filter : "all";
  const statuses = {attention: "needs_attention,uncertain", active: "accepted,running,waiting_checks,waiting_external"}[selected];
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const list = operationList({h, t, api, guard: () => assertView(connection), statuses, namespace: state.namespace});
  const memory = viewState(state.namespace || "", "", "operations:" + (statuses || "all"));
  const search = h("input", {type: "search", "aria-label": t("global_search_operations"), placeholder: t("global_search_operations"), value: memory.query || ""});
  search.addEventListener("input", () => list.filter(search.value));
  const searchWrap = searchField(search, {class:"workspace-search global-ops-search"});
  const filters = segmented({
    label: t("global_ops_filter"), value: selected,
    options: [["all", "operations_all"], ["attention", "tab_needs_you"], ["active", "tab_to_confirm"]].map(([value, label]) => ({value, label: t(label)})),
    onChange: value => {location.hash = "#/operations/" + value;},
  });
  const refresh = iconButton({icon: "refresh-cw", label: t("global_refresh"), onClick: () => list.load().catch(() => {})});
  list.onBusy(busy => { refresh.disabled = busy; });
  main.append(pageHeader({title: t("nav_operations"), actions: [refresh]}),
    h("div", {class: "global-ops-tools"}, filters, searchWrap), list.box);
  if (memory.query) list.filter(memory.query);
  await list.load().catch(() => {});
  const reload = debounceRefresh(() => list.load(), 500);
  return onEvents(ev => {if (ev.resource_type === "operation") return reload();});
}

async function viewOperation(main, id) {
  freshPage();
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  let repair;
  const panel = h("div", { class: "panel" });
  main.append(panel);
  const render = async (fromEvent = false) => {
    const opens = drawerOpens;
    try {
      const { operation: op, work_items: linked, cleanup_receipts: cleanupReceipts } = await api("GET", `/operations/${id}`);
      assertView(connection);
      if (!repair && state.caps?.features?.managed_repairs?.version === 1 &&
          ["cleanup.apply", "task.verify", "session.record_verification"].includes(op.action) && ["failed", "needs_attention"].includes(op.status)) {
        repair = repairPanel({h, t, api, caps: () => state.caps, guard: () => assertView(connection), namespace: connection.namespace,
          source: {kind: "operation", operation_id: id}, errorBox, opStatus});
        main.append(repair.box);
      }
      const refs = op.external_refs || {};
      const retainedCheckpoint = op.action === "checkpoint.continue" && op.status === "failed" &&
        ["CHECKPOINT_STAGING_RETAINED", "CHECKPOINT_DESTINATION_APPEARED"].includes(op.error_code);
      const reason = retainedCheckpoint ? t("error_code_" + op.error_code) : op.status_reason;
      const retry = refs.merged_sha && op.action === "delivery.merge_and_deploy" && op.status === "failed"
        ? h("button", { class: "primary", onclick: async () => {
          let sourceRefresh = true;
          try {
            const preview = (await api("GET", `/deployments/preview?recipe=${encodeURIComponent(op.target.recipe)}`)).preview;
            const preconditions = preview.preconditions;
            sourceRefresh = false;
            const o = await submit("deployment.start", { recipe: op.target.recipe }, { source_sha: refs.merged_sha }, preconditions,
              `deploy.${op.target.recipe}.${refs.merged_sha}`);
            location.hash = `#/op/${o.operation_id}`;
          } catch (e) { panel.append(errorBox(e, {sourceRefresh, within:panel})); }
        } }, t("retry_deploy")) : null;
      const needsConfirm = op.action === "checkpoint.continue" && ["SOURCE_MOVED", "SOURCE_UNAVAILABLE"].includes(op.error_code);
      const resume = op.status === "needs_attention" && !needsConfirm
        ? h("button", { class: "primary", title: t("resume_help"), onclick: async () => {
          try { await api("POST", `/operations/${id}/resume`, {}); render(); } catch (e) { panel.append(errorBox(e, {sourceRefresh:false, within:panel})); }
        } }, t(op.action === "worktree.merge" ? "delivery_merge_read_receipts" : "resume")) : null;
      const confirmSource = op.status === "needs_attention" && needsConfirm ? h("button", { class: "primary", onclick: async () => {
        const connection = {epoch: state.epoch, namespace: state.namespace, generation};
        let sourceRefresh = true;
        try { const seen = (await api("GET", `/checkpoints/${op.target.checkpoint_id}?live=true`)).source.head;
          assertView(connection);
          if (!seen) throw new Error(t("source_unavailable"));
          sourceRefresh = false;
          await submit("checkpoint.continue.revalidate", { operation_id: id }, { observed_source_head_sha: seen },
            { expected_input_manifest_digest: refs.input_manifest_digest }, `revalidate.${id}.${seen}`); render();
        } catch (e) { panel.append(errorBox(e, {sourceRefresh, within:panel})); }
      } }, t("confirm_source")) : null;
      const materialized = refs.materializations || op.result?.materializations || [];
      const opened = op.result?.session_id && op.result?.host
        ? h("a", { class: "secondary", href: `#/session/${encodeURIComponent(op.result.host)}/${encodeURIComponent(op.result.session_id)}` },
          t("open_new_session")) : null;
      let receipts = null;
      if (op.action === "integration.apply") {
        const rows = (await api("GET", `/integrations/${id}`)).receipts;
        receipts = [h("h2", {}, t("receipts")), ...rows.map(r => h("div", { class: "row" },
          h("div", { class: "grow" }, `${r.seq}. ${t("kind_" + r.source_kind)} `, h("code", {}, r.source_id.slice(0, 15)), " ",
            h("code", {}, `${r.pinned_sha.slice(0, 10)} → ${(r.integrated_sha || "").slice(0, 10)}`),
            r.conflict_files ? h("span", { class: "error" }, " ", r.conflict_files.join(", ")) : null),
          chip(r.method || "-"), chip(t("receipt_" + r.effective_status), r.effective_status === "delivered" ? "ok" : "")))];
      }
      const cancel = !TERMINAL.includes(op.status)
        ? h("button", { class: "danger", onclick: async () => {
          try { await api("POST", `/operations/${id}/cancel`, {}); render(); } catch (e) { panel.append(errorBox(e, {sourceRefresh:false, within:panel})); }
        } }, t("cancel"))
        : null;
      if (!panel.isConnected) return;
      if (holdRender(fromEvent, opens)) { idleReload = () => render(true); return; }
      freshPage();
      fill(panel, pageHeader({title: actionLabel(op.action), pill: statusPill(operationStatusKey(op.status))}),
        h("p", { class: "op-status" }, opStatus(op), " ", op.error_code ? chip(reasonText(op.error_code) || t("reason_unavailable"), "bad") : null),
        ...(linked?.length ? [linkedItems(linked)] : []),
        h("dl", { class: "kv" },
          h("dt", {}, t("global_action_id")), h("dd", {}, h("code", {}, op.action || "—")),
          h("dt", {}, t("actor")), h("dd", {}, `${op.actor} (${op.entry})`),
          h("dt", {}, t("created")), h("dd", {}, when(epoch(op.created_at))),
          reason ? [h("dt", {}, t("reason")), h("dd", {}, reason)] : null,
          op.error_code ? [h("dt", {}, t("global_code")), h("dd", {}, h("code", {}, op.error_code))] : null,
          h("dt", {}, t("global_col_target")), h("dd", {}, h("code", {}, JSON.stringify(op.target))),
          Object.keys(refs).length && op.action !== "worktree.merge" ? [h("dt", {}, t("global_refs")), h("dd", {}, h("code", {}, JSON.stringify(refs)))] : null,
          op.result && op.action !== "worktree.merge" ? [h("dt", {}, t("global_result")), h("dd", {}, h("code", {}, JSON.stringify(op.result)))] : null),
        retainedCheckpoint ? h("div", {class: "note warn", "data-checkpoint-retained": ""},
          h("dl", {class: "kv"},
            typeof refs.retained_staging_path === "string" ? [h("dt", {}, t("checkpoint_retained_path")),
              h("dd", {}, h("code", {}, refs.retained_staging_path))] : null,
            op.error_code === "CHECKPOINT_DESTINATION_APPEARED" && typeof refs.clone_path === "string"
              ? [h("dt", {}, t("checkpoint_destination_path")), h("dd", {}, h("code", {}, refs.clone_path))] : null),
          h("p", {}, t("checkpoint_retained_why")),
          h("p", {}, t(op.error_code === "CHECKPOINT_DESTINATION_APPEARED" ? "checkpoint_destination_next" : "checkpoint_retained_next")),
          typeof refs.source_host === "string" && refs.source_host && typeof refs.source_session_id === "string" && refs.source_session_id
            ? h("a", {href: sessionHref({host: refs.source_host, sessionId: refs.source_session_id})}, t("source_session")) : null,
          h("p", {}, t("checkpoint_retained_cleanup"))) : null,
        ...(receipts || []),
        mergeRecovery(op),
        op.action === "worktree.merge" ? h("details", {}, h("summary", {}, t("sessions_details")),
          h("pre", {class: "pre"}, JSON.stringify({refs, result: op.result}, null, 2))) : null,
        ...(materialized.length ? [h("h2", {}, t("materializations")), ...materialized.map(m => h("div", {class: "row"},
          h("div", {class: "grow"}, `${m.artifact_id} · r${m.revision}`, h("div", {class: "muted"}, m.managed_path)),
          chip(t(`material_${m.state}`), m.state === "verified" ? "ok" : "")))] : []),
        ...(cleanupReceipts?.length ? [h("h2", {}, t("cleanup_open_receipts")), ...cleanupReceipts.map(r =>
          h("details", {class: "row-details"}, h("summary", {}, r.resource_id, " · ", t("cleanup_receipt_" + r.status)),
            h("pre", {class: "pre"}, JSON.stringify(r, null, 2))))] : []),
        ...(op.action === "integration.apply" ? [repairControl(op)] : []),

        (op.result?.merge || op.result || refs.merge_receipt)?.base_moved
          ? h("p", { class: "note warn" }, t("merged_newer_base", { count: (op.result?.merge || op.result || refs.merge_receipt).other_commits_count })) : null,
        refs.write_acknowledged && refs.verification_pending ? h("p", { class: "note warn" }, t("metadata_pending")) : null,
        h("h2", {}, t("steps")),
        ...op.steps.map(s => h("div", { class: "row" }, h("div", { class: "grow" }, s.name),
          h("span", { class: `status-${s.status}` }, operationStatusLabel(s.status)), s.error ? chip(s.error.code || t("error"), "bad") : null)),
        h("div", { class: "actions" }, opened,
          ["artifact.capture.managed", "artifact.accept"].includes(op.action) && op.status === "succeeded" &&
          /^art_[0-9a-f]{32}$/.test(op.result?.artifact_id) && Number.isSafeInteger(op.result?.revision) && op.result.revision > 0
            ? h("a", {href: `#/artifact-review/artifact/${op.result.artifact_id}/${op.result.revision}`}, t("ar_review_title")) : null,
          state.caps?.artifacts?.capture?.managed_single_file && managedCaptureExecution(op)
            ? h("a", {href: `#/artifact-review/operation/${op.operation_id}`}, t("ar_open")) : null,
          confirmSource, resume, retry, cancel));
    } catch (e) { fill(panel, errorBox(e, {within:panel})); }
  };
  await render();
  const off = liveReload(render, ["operation", "integration", "cleanup"]);
  return () => {off(); repair?.dispose();};
}

function mergeRecovery(op) {
  if (op.action !== "worktree.merge") return null;
  const steps = op.steps || [], refs = op.external_refs || {};
  const reserved = steps.some(s => s.name === "merge.reserve" && s.status === "succeeded");
  const released = steps.some(s => s.name === "merge.release" && s.status === "succeeded");
  if (!reserved || released) return null;
  const frames = steps.filter(s => ["rehydrate.frame", "merge.frame"].includes(s.name));
  return h("section", {class: "panel", "data-merge-recovery": true},
    h("h2", {}, t("delivery_merge_recovery")),
    h("p", {class: "note"}, t("delivery_merge_held")),
    h("dl", {class: "kv"}, ...["source", "destination"].flatMap((label, i) => [
      h("dt", {}, t("delivery_merge_" + label)), h("dd", {}, h("code", {}, refs.carrier_paths?.[i] || t("obs_unknown")))])),
    ...frames.map(step => h("p", {}, h("code", {}, step.name), " · ",
      t(step.status === "succeeded" ? "delivery_merge_ack" : "delivery_merge_no_ack"), " · ", observationTime(step.finished_at || step.started_at))),
    h("p", {}, t("delivery_merge_safe_reads")),
    h("p", {class: "muted"}, t("delivery_merge_closeout")),
    refs.host && refs.session_id ? observationLink("session", `${refs.host}/${refs.session_id}`) : null);
}

function fleetEmpty() {
  return h("section", {class: "panel"}, emptyState({
    title: t("global_fleet_empty"),
    action: h("a", {class: "ui-button secondary ui-sm", href: setupGuide()}, t("global_setup_guide")),
  }));
}
function connectionFacts() {
  const endpoint = state.endpoint || (state.token ? location.origin : "");
  const statePill = h('span', {class: 'ui-pill'}), stateCell = h("dd", {}, statePill);
  const eventCell = h("dd", {'data-last-event': ''});
  const pollCell = h("dd", {'data-last-successful-poll': ''});
  const summaryCell = h('div', {'data-health-settings': ''});
  const dl = h("dl", {class: "kv global-connection-facts"},
    h("dt", {}, t("global_endpoint")), h("dd", {}, endpoint || "—"),
    h("dt", {}, t("global_state")), stateCell,
    h("dt", {}, t("global_last_event")), eventCell,
    h("dt", {}, t("ui_last_poll")), pollCell,
    h("dt", {}, t("connection_access")), connectionAccess());
  const render = () => {
    const projection = healthDisplay(state);
    const {tone, freshness} = projection.connection;
    statePill.className = `ui-pill tone-${freshness === 'current' ? tone : 'neutral'}`;
    statePill.textContent = healthLabel(projection.connection, t);
    renderHealthContent(summaryCell, projection);
    eventCell.replaceChildren(state.lastEventAt ? when(state.lastEventAt) : t("global_no_event"));
    eventCell.dataset.observedAt = state.lastEventAt ? new Date(state.lastEventAt).toISOString() : '';
    const pollAt = state.lastPoll?.namespace === state.namespace ? state.lastPoll.at : 0;
    pollCell.replaceChildren(pollAt ? when(pollAt) : '—');
    pollCell.dataset.observedAt = pollAt ? new Date(pollAt).toISOString() : '';
  };
  render();
  const paint = () => {
    if (!dl.isConnected) { connectionPainters.delete(paint); return; }
    render();
  };
  connectionPainters.add(paint);
  return h('div', {}, dl, summaryCell);
}
function connectionAccess() {
  const known = ['observe', 'operate', 'manage', 'start', 'approve', 'cleanup', 'cleanup_discard', 'deploy', 'integrate', 'merge'];
  const labels = [...new Set((state.caps?.scopes || []).map(scope => t(known.includes(scope) ? `connection_scope_${scope}` : 'connection_other_access')))];
  return h('dd', {'data-connection-access': ''}, joinLabel(labels));
}
function connectionIdentity() {
  return h('dl', {class: 'kv'}, h('dt', {}, t('connection_actor')), h('dd', {}, h('code', {}, state.caps?.actor || '—')),
    h('dt', {}, t('connection_scopes')), h('dd', {}, h('code', {}, (state.caps?.scopes || []).join(', '))));
}
async function fleetTailscale(root, guard) {
  try {
    const availability = await fleetAvailability(); guard();
    if (availability.configured && availability.platform_supported) return mountTailscale(root, {h, t});
  } catch { /* The Fleet panel owns its availability error. */ }
  return () => {};
}
function languageSwitch() {
  return h('div', {class: 'setup-language', role: 'group', 'aria-label': t('setup_language'), 'data-language-switch': ''},
    h('span', {}, t('setup_language')),
    ...[['en', 'setup_language_en'], ['zh-TW', 'setup_language_zh']].map(([value, label]) =>
      h('button', {type: 'button', class: 'secondary', 'aria-pressed': locale === value ? 'true' : 'false', onclick: () => {
        if (locale === value) return;
        setLocale(value); void route();
      }}, t(label))));
}
function openSettings(main, {exclusive = false, refreshButton} = {}) {
  const memory = viewState(state.namespace || "", "", "settings");
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const frame = settingsFrame({h, t, exclusive, selected: params.get("section") || memory.section || "", onSelect: id => { memory.section = id; }, sections: [
    {id: "connection", label: t("global_nav_connection")},
    {id: "desktop", label: t("global_nav_desktop"), hidden: true},
    {id: "models", label: t("global_nav_models"), hidden: true},
    {id: "fleet", label: t("global_nav_fleet"), hidden: true},
    {id: "updates", label: t("global_nav_updates"), hidden: true},
    {id: "agents", label: t("agents_heading")},
    {id: "about", label: t("global_nav_about")},
  ]});
  if (state.token && state.caps) main.append(pageHeader({
    title: t("ui_settings"),
    actions: [refreshButton || iconButton({icon: "refresh-cw", label: t("global_refresh"), onClick: () => route()})],
  }));
  frame.panel("about").append(h("section", {class: "panel"},
    h("h2", {}, t("global_nav_about")),
    h("p", {}, t("global_product")),
    h('p', {'data-build-line':''}, buildLine(versionProjection(diagnosticsClient()),t)),
    h('div', {'data-about-versions':''}),
    h('div',{class:'actions'},button({label:t('diag_export'),onClick:openDiagnostics,'data-export-diagnostics':''}),
      h("a", {href: setupGuide()}, t("global_setup_guide")))));
  main.append(languageSwitch(), frame.root);
  frame.panel('about').append(h('div', {'data-earlier-drafts-slot': ''}));
  paintEarlierDrafts();
  const identityEpoch = state.epoch;
  const paintBuild = () => {
    if(!frame.root.isConnected || identityEpoch !== state.epoch){versionPainters.delete(paintBuild);return;}
    const projection=versionProjection(diagnosticsClient());
    frame.panel('about').querySelector('[data-build-line]').textContent=buildLine(projection,t);
    frame.panel('about').querySelector('[data-about-versions]').replaceChildren(versionFacts({h,t,projection}));
  };
  versionPainters.add(paintBuild);paintBuild();
  if(state.token)api('GET','/capabilities').catch(()=>{});
  void readNativeVersions();
  const back=connectionReturn(params.get('from'));
  if(back)frame.panel('connection').append(h('a',{href:back.href,'data-connection-return':''},t(back.label)));
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const agents = settingsAgents({h, t, api, caps: () => state.caps, guard: () => assertView(connection), from: params.get("from")});
  frame.panel("agents").append(agents.root);
  void agents.load();
  if (params.get("section") === "agents") {agents.heading.scrollIntoView({block: "nearest"}); agents.heading.focus();}
  return frame;
}
function viewBrowserSettings(main) {
  const frame = openSettings(main, {exclusive: true});
  const managed = state.token === browserSessionToken;
  const facts = connectionFacts();
  frame.panel("connection").append(h("section", {class: "panel"},
    facts,
    managed ? h("h2", {}, t("ui_local_connector")) : null,
    disclosure({label: t("global_details"), body: h('div', {}, connectionIdentity(), managed ? h("p", {}, t("managed_browser_help")) : null)}),
    retryRememberedTokenButton(),
    h("button", {class: "danger", onclick: async () => {await disconnect(); route();}}, t("disconnect"))));
  frame.show("fleet");
  frame.panel("fleet").append(fleetEmpty());
  return () => {};
}
function viewBrowserSignedOut(main) {
  const input = h("input", {type: "password", autocomplete: "off", placeholder: "batc_…"});
  const remember = h("input", {type: "checkbox"});
  const info = h("div", {class: "global-connect-status"});
  const showConnectError = error => {
    info.setAttribute("role", "alert");
    info.replaceChildren(error?.credentialRefused ? h("p", {class: "error"}, t("global_token_rejected")) : errorBox(error, {within:info}));
  };
  if (state.connectionError) showConnectError(state.connectionError);
  const connect = async (token, saved = false) => {
    if (!token) return;
    try {
      await connectBrowserToken(token);
      if (!saved) saveToken(token, remember.checked);
      location.hash = "#/home";
      await route();
    } catch (error) {
      if (error.code !== 'CONNECTION_CHANGED') {
        state.token = null; state.caps = null; state.connectionError = error; showConnectError(error);
      }
    }
  };
  const form = h("form", {class: "global-connect-form", onsubmit: async event => {
    event.preventDefault();
    await connect(input.value.trim());
  }}, h("label", {class: "connection-token"}, t("token"), input),
    h("label", {class: "global-remember"}, remember, " ", t("remember")),
    h("div", {class: "global-connect-actions"},
      h("button", {class: "primary", type: "submit"}, t("connect")),
      loadToken() ? h('button', {type: 'button', onclick: () => connect(loadToken(), true)}, t('connection_retry_remembered')) : null,
      h("a", {class: "global-quiet-link", href: setupGuide()}, t("global_setup_guide"))),
    disclosure({label: t("global_details"), body: h("p", {}, t("token_help"))}), info);
  const root = firstRunCard({h, t, body: form});
  root.querySelector('.global-first-card').prepend(languageSwitch());
  main.append(root);
}
function viewSettings(main) {
  if (state.caps?.managed_installation) return viewManagedSettings(main);
  if (nativeDesktop) return viewNativeSettings(main);
  if (state.token && state.caps) return viewBrowserSettings(main);
  viewBrowserSignedOut(main);
}

function viewStartupRole(main, snapshot, mine) {
  const body = startupRoleChoice({h, t, errorBox, snapshot, select: async input => {
    const guard = () => {if (mine !== generation) throw new Error(t('desktop_role_changed'));};
    guard();
    try {
      const selected = await nativeSelectRole(input);
      guard();
      if (selected.credential_available) await nativeTransition('connect');
      else await route();
    } catch (error) {
      guard();
      const current = await nativeStatus();
      guard();
      if (current.startup_role?.role) {state.connectionError = error; await route();}
      else throw error;
    }
  }});
  const root = firstRunCard({h, t, body});
  root.querySelector('.global-first-card').prepend(languageSwitch());
  main.append(root);
}

async function viewManagedSettings(main) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const guard = () => assertView(connection);
  const status = h("div", {role: "status"});
  const refreshButton = iconButton({icon: 'refresh-cw', label: t('global_refresh'), onClick: () => setup.reload()});
  const frame = openSettings(main, {exclusive: !nativeDesktop, refreshButton});
  const local = h("section", {class: "panel", 'data-connection-card': ''}, h("h2", {}, t("ui_local_connector")),
    connectionFacts(),
    disclosure({label: t("global_details"), body: h('div', {}, connectionIdentity(),
      h("p", {}, t("managed_background_help")), !nativeDesktop ? h('p', {}, t('managed_browser_help')) : null,
      h("div", {"data-managed-locations": ""}, h('h3', {}, t('managed_locations')),
      h("p", {class: "muted"}, t("managed_locations_central")),
      h("p", {class: "muted"}, t("managed_locations_work"))))}), status);
  const setup = managedSetupPanel({h, t, api, guard, namespace: state.namespace, errorBox, refreshButton, caps: () => state.caps,
    onConfigured: async () => {const caps = await api("GET", "/capabilities"); guard(); state.caps = caps;}});
  if (!state.caps.hosts?.length) {
    setup.box.querySelector('[data-setup-advanced]').append(local);
    frame.panel('connection').append(setup.box);
  } else frame.panel('connection').append(local, setup.box);
  await setup.load();
  try {guard();} catch (error) {
    setup.dispose();
    if (["CONNECTION_CHANGED", "VIEW_CHANGED"].includes(error.code)) return;
    throw error;
  }
  const fleetRoot = frame.panel("fleet");
  frame.show("fleet");
  let disposeFleet, disposeTailscale;
  if (nativeDesktop) {
    await managedServiceControls(local, {h, t, managedControl, errorBox, guard,
      onStarted: async () => {await nativeTransition('connect');}});
    try {
      const native = await nativeStatus(); guard();
      if (native.configuration_source === "managed" && native.configuration_setup) {
        const draft = state.nativeSetupDraft ||= {endpoint: "", expected_actor: ""};
        const endpoint = h("input", {type: "url", value: draft.endpoint, required: true, maxlength: 512,
          autocomplete: "off", oninput: event => {draft.endpoint = event.target.value;}});
        const actor = h("input", {value: draft.expected_actor, required: true, maxlength: 200,
          autocomplete: "off", oninput: event => {draft.expected_actor = event.target.value;}});
        const review = h("button", {type: "submit", class: "secondary"}, t("desktop_setup_review"));
        const form = h("form", {novalidate: true, onsubmit: event => {
          event.preventDefault(); if (state.nativeBusy || !setupFormValidity(form, t)) return;
          guard(); review.disabled = true;
          return nativeTransition("configure", {endpoint: endpoint.value.trim(), expected_actor: actor.value.trim(), contract_version: "2026-10-08"});
        }}, h("p", {class: "muted"}, t("managed_join_help")),
          h("div", {class: "capture-fields"}, h("label", {}, t("desktop_endpoint"), endpoint),
            h("label", {}, t("desktop_expected_actor"), actor)), review);
        frame.panel("desktop").append(h("details", {class: "panel", "data-managed-join": ""}, h("summary", {}, t("setup_advanced")), h("h3", {}, t("managed_join_existing")), form));
        frame.show("desktop");
      }
    } catch (error) {if (connection.generation === generation) status.append(errorBox(error, {within:status}));}
    disposeFleet = await mountFleet(fleetRoot, {h, t, onStatus: rememberFleetHealth});
    disposeTailscale = await fleetTailscale(fleetRoot, guard);
  } else {
    local.append(...[retryRememberedTokenButton(), h("button", {class: "danger", onclick: () => {disconnect(); route();}}, t("disconnect"))].filter(Boolean));
    fleetRoot.append(fleetEmpty());
  }
  return () => {setup.dispose(); disposeFleet?.(); disposeTailscale?.();};
}

// Credential dialogs and protected storage stay native. A transition invalidates old async
// forms immediately, but cancellation restores the existing account and its identity-scoped drafts.
async function nativeTransition(kind, config = null) {
  if (state.nativeBusy && kind !== "disconnect") return;
  const attempt = ++state.nativeAttempt;
  const previousOnline = state.online;
  const previousToken = state.token;
  state.nativeBusy = true; state.epoch++; state.online = false;
  state.connectionError = null; state.connectionNotice = null;
  try {
    if (kind === "configure") {
      const saved = await nativeSetupConfiguration(config);
      if (attempt !== state.nativeAttempt) return;
      if (saved) {disconnect(); state.nativeSetupDraft = null; state.connectionNotice = t("desktop_setup_saved");}
      else {state.online = previousOnline; state.connectionNotice = t("desktop_setup_cancelled");}
    } else if (kind === "disconnect" || kind === "reload" || kind === "forget") {
      disconnect(); // invalidate caches/old forms before native can load another endpoint
      if (kind === "disconnect") await nativeDisconnect();
      else if (kind === "reload") await nativeReloadConfiguration();
      else await nativeForgetCredential();
      if (attempt === state.nativeAttempt) state.connectionNotice = t(kind === "forget" ? "desktop_forgotten" : kind === "reload" ? "desktop_reloaded" : "desktop_disconnected");
    } else {
      const status = await nativeStatus();
      if (attempt !== state.nativeAttempt) return;
      const caps = kind === "enroll" ? await nativeEnroll() : await nativeConnect();
      if (attempt !== state.nativeAttempt) return;
      if (!caps) {
        state.online = previousOnline;
        state.connectionNotice = t("desktop_enrollment_cancelled");
      } else {
        state.token = "native-credential";
        // Reuse storage only for this verified identity, including an explicitly
        // reconnected managed account; a different backend/principal stays isolated.
        try { await activate(caps, status.endpoint, false, status.configuration_source); }
        catch (error) { if (attempt === state.nativeAttempt) { if (kind !== "recover") await nativeDisconnect(); disconnect(); } throw error; }
        if (kind === "connect" && attempt === state.nativeAttempt) location.hash = "#/home";
      }
    }
  } catch (error) {
    if (attempt !== state.nativeAttempt) return;
    state.connectionError = error;
    // Native replacement/authentication failures retain the existing active connection.
    if (state.token === previousToken) state.online = previousOnline;
  } finally {
    if (attempt === state.nativeAttempt) { state.nativeBusy = false; await route(); }
  }
}

async function viewNativeSettings(main) {
  const mine = generation;
  const info = h("div", {"aria-live": "polite"});
  const details = h("dl", {class: "kv"});
  const technicalDetails = h("dl", {class: "kv"});
  const setup = h("div", {"data-native-setup": "", hidden: true});
  const help = h("p", {class: "muted"}, t("desktop_credential_help"));
  const platform = h("p", {class: "muted"});
  const fleetRoot = h("div");
  const updateRoot = h("div");
  let disposeUpdates;
  let firstConnection = false;
  const controls = [];
  const action = (kind, label, cls = "secondary") => {
    const button = h("button", {class: cls, disabled: true, onclick: () => {
      for (const control of controls) control.disabled = true;
      if (kind === "connect" || kind === "enroll") leave.disabled = false;
      info.textContent = t("desktop_connecting");
      return nativeTransition(kind);
    }}, t(label));
    controls.push(button); return button;
  };
  const connect = action("connect", "connect", "primary");
  const enroll = action("enroll", "desktop_add_credential");
  const reload = action("reload", "desktop_reload_configuration");
  const forget = action("forget", "desktop_forget_credential");
  const leave = action("disconnect", "disconnect", "danger");
  const actions = h("div", {class: "actions"}, connect, enroll, reload, leave);
  const saved = h("div", {}, h("p", {class: "muted"}, t("desktop_forget_help")), forget);
  const connectionDetails = h("details", {}, h("summary", {}, t("connection_details")), technicalDetails, help);
  const introduction = h("details", {}, h("summary", {}, t("delivery_setup_title")),
    h("ol", {}, h("li", {}, t("delivery_setup_central")), h("li", {}, t("delivery_setup_identity")), h("li", {}, t("delivery_setup_mode"))));
  const localDetails = h("details", {class: "panel"}, h("summary", {}, t("desktop_local")),
    h("p", {class: "muted"}, t("desktop_dashboard_only")));
  const frame = openSettings(main);
  frame.panel("connection").append(h("section", {class: "panel native-connection", "aria-label": t("desktop_connection")},
    h("h2", {}, t("desktop_connection")),
    setup, details, platform, actions, info, connectionDetails, introduction, saved));
  frame.panel("desktop").append(localDetails);
  frame.panel("fleet").append(fleetRoot);
  frame.panel("updates").append(updateRoot);
  const showInfo = () => {
    info.replaceChildren();
    technicalDetails.replaceChildren();
    if (state.caps) technicalDetails.append(connectionIdentity());
    if (state.connectionNotice) info.append(h("p", {}, state.connectionNotice));
    if (state.connectionError) info.append(errorBox(state.connectionError, {within:info}));
    if (state.nativeBusy) info.append(h("p", {}, t("desktop_connecting")));
  };
  showInfo();
  try {
    const status = await nativeStatus();
    if (mine !== generation || !main.contains(details)) return;
    // Reuse the settings read: another await before openSettings would move its
    // capability refresh into the interval before native bootstrap validation.
    const startup = status?.startup_role;
    if (startup && startup.role === null) {
      main.replaceChildren();
      return viewStartupRole(main, startup, mine);
    }
    if (status.updates === true) disposeUpdates = await mountUpdates(updateRoot, {h, t, versionInput:diagnosticsClient,
      onRead:(value,at)=>rememberVersionRead('updater',value,at),
      onVersions:paint=>{versionPainters.add(paint);return()=>versionPainters.delete(paint);}});
    if (mine !== generation || !main.contains(details)) { disposeUpdates?.(); return; }
    const row = (label, value) => { if (value) details.append(h("dt", {}, t(label)), h("dd", {}, value)); };
    const service = await managedServiceControls(frame.panel('connection'), {h, t, managedControl, errorBox,
      guard: () => {if (mine !== generation) throw new Error('VIEW_CHANGED');},
      onStarted: async () => {await nativeTransition('connect');}});
    if (mine !== generation) return;
    const serviceHeld = status.configuration_source === 'managed' && service?.lifecycle?.state !== 'running' && !!service?.lifecycle;
    if (serviceHeld) {
      details.hidden = true; actions.hidden = true; platform.hidden = true; introduction.hidden = true; saved.hidden = true;
    }
    if (status.configuration_setup === true && !serviceHeld) {
      firstConnection = true;
      setup.hidden = false;
      details.hidden = true;
      actions.hidden = true;
      platform.hidden = true;
      localDetails.hidden = true;
      const draft = state.nativeSetupDraft ||= {endpoint: "", expected_actor: ""};
      const endpoint = h("input", {type: "url", value: draft.endpoint, placeholder: "https://connector.example", required: true,
        maxlength: 512, disabled: state.nativeBusy, autocomplete: "off", oninput: event => {draft.endpoint = event.target.value;}});
      const actor = h("input", {value: draft.expected_actor, required: true, maxlength: 200,
        disabled: state.nativeBusy, autocomplete: "off", oninput: event => {draft.expected_actor = event.target.value;}});
      const review = h("button", {type: "submit", class: "primary", disabled: state.nativeBusy}, t("desktop_setup_review"));
      const form = h("form", {novalidate: true, onsubmit: event => {
        event.preventDefault();
        if (state.nativeBusy || !setupFormValidity(form, t)) return;
        review.disabled = true;
        return nativeTransition("configure", {endpoint: endpoint.value.trim(), expected_actor: actor.value.trim(), contract_version: "2026-10-08"});
      }}, h("p", {}, t("desktop_setup_help")),
        h("div", {class: "capture-fields"}, h("label", {}, t("desktop_endpoint"), endpoint), h("label", {}, t("desktop_expected_actor"), actor)),
        h("p", {class: "muted"}, t("desktop_setup_origin")), h("div", {class: "actions"}, review));
      if (status.configuration_source === "managed" && status.error) {
        setup.append(h("h3", {}, t("managed_recovery_title")), h("p", {}, t("managed_recovery_help")),
          h("details", {}, h("summary", {}, t("setup_advanced")), h("h3", {}, t("managed_join_existing")), form));
        introduction.hidden = true;
      } else {
        if (status.startup_role?.role === "client") setup.append(h("h3", {}, t("managed_join_existing")));
        setup.append(form);
      }
      // Keep configuration recovery accessible without making it compete with the first step.
      connectionDetails.append(h("div", {class: "actions"}, reload));
    }
    row("desktop_endpoint", status.endpoint || t("desktop_config_needed"));
    if (status.expected_actor) technicalDetails.append(h("dt", {}, t("desktop_expected_actor")), h("dd", {}, status.expected_actor));
    if (status.configuration_file) technicalDetails.append(h("dt", {}, t("desktop_configuration_file")), h("dd", {}, status.configuration_file));
    if (status.credential_source) row("desktop_credential_source", t("desktop_source_" + status.credential_source));
    if (state.caps) details.append(h("dt", {}, t("connection_access")), connectionAccess());
    if (status.error && (!status.configuration_setup || status.configuration_source === "managed")) info.append(errorBox(new Error(status.error), {
      summary: t(status.error.startsWith('JOURNAL_NEWER_DATA_STEP:') ? 'managed_newer_journal' : 'desktop_status_error'), within:info}));
    else if (!status.credential_available && !status.configuration_setup) info.append(h("p", {}, t("desktop_credential_missing")));
    platform.textContent = status.enrollment_supported === true ? t("desktop_enrollment_help")
      : status.enrollment_supported === false ? t("desktop_enrollment_unsupported") : "";
    connect.disabled = state.nativeBusy || !!status.error || !status.credential_available;
    enroll.hidden = status.enrollment_supported !== true;
    enroll.textContent = t(status.credential_saved ? "desktop_replace_credential" : "desktop_add_credential");
    enroll.disabled = state.nativeBusy || !status.endpoint || !status.expected_actor;
    enroll.className = status.credential_available ? "secondary" : "primary";
    if (!status.credential_available) connect.className = "secondary";
    reload.hidden = !status.configuration_reload;
    reload.disabled = state.nativeBusy || !status.configuration_reload;
    leave.disabled = !state.token && !state.nativeBusy;
    saved.hidden = !status.credential_saved;
    forget.disabled = state.nativeBusy || !status.credential_saved;
  } catch (error) { if (mine === generation) info.append(errorBox(error, {within:info})); }
  if (mine !== generation) return;
  if (disposeUpdates) frame.show("updates");
  if (firstConnection) {
    setup.prepend(h("span", {class: "brand-mark", "aria-hidden": "true"}),
      h("p", {}, t("global_first_sentence")),
      h("a", {href: setupGuide()}, t("global_setup_guide")));
    return () => disposeUpdates?.();
  }
  frame.show("desktop");
  const disposeFleet = await mountFleet(fleetRoot, {h, t, onStatus: rememberFleetHealth});
  if (mine !== generation || !main.isConnected) { disposeFleet?.(); disposeUpdates?.(); return; }
  const disposeTailscale = await fleetTailscale(fleetRoot, () => {
    if (mine !== generation || !main.isConnected) throw new Error('VIEW_CHANGED');
  });
  frame.show("fleet");
  return () => {disposeFleet?.(); disposeUpdates?.(); disposeTailscale();};
}

// ------------------------------------------------------------------ projects and work items
// The connector's own records. A change names what the page read (version, fingerprint, sibling order or pin), so a
// stale page is refused and reloads instead of overwriting someone else's edit.
const may = scope => (state.caps?.scopes || []).includes(scope);
function fill(el, ...kids) { place(el, 'replaceChildren', ...kids); }
const PROJECT_ERRORS = ["VERSION_CONFLICT", "ORDER_CHANGED", "PIN_CHANGED", "CONTENT_CHANGED", "NAME_TAKEN",
  "HAS_CHILDREN", "PARENT_ARCHIVED", "PINNED_FIRST", "STEPS_OPEN", "CYCLE", "LINK_TARGET_NOT_FOUND", "NOTHING_TO_DECIDE"];
function problem(code, message) {
  return h("p", { class: "error" }, PROJECT_ERRORS.includes(code) ? t("wi_err_" + code) : `${code || ""} ${message || ""}`);
}
// The page read is stale: re-render, keep the message (and an edit form's draft) on screen.
const STALE = ["VERSION_CONFLICT", "ORDER_CHANGED", "PIN_CHANGED", "CONTENT_CHANGED"];
let lastFailure = null;
// Run one management change; returns the finished operation, or shows why not in `out` and returns null
// (lastFailure holds the error code). `out` should sit outside what the caller re-renders afterwards.
async function change(out, action, target, params, pre, scope) {
  lastFailure = null;
  try {
    const op = await submit(action, target, params, pre, scope);
    if (op.status === "succeeded") { fill(out); return op; }
    lastFailure = op.error_code || op.status;
    if (TERMINAL.includes(op.status)) fill(out, problem(op.error_code, op.status_reason));
    else fill(out, opStatus(op), " ", h("a", { href: `#/op/${op.operation_id}` }, op.operation_id));
  } catch (e) {
    lastFailure = e.code || "ERROR";
    fill(out, e.status === 403 ? errorBox(e, {sourceRefresh:false, within:out}) : problem(e.code, e.message));
  }
  return null;
}
function manageNote() { return may("manage") ? null : h("p", { class: "note" }, observedText(t, "or_readonly_project")); }
function indent(el, depth) { el.style.setProperty("--depth", String(depth)); return el; } // CSP: no style attributes
function stateChip(c) {
  return statusPill(workStatusKey(c?.display_state));
}
function counts(c) {
  const known = value => Number.isSafeInteger(value) && value >= 0;
  const complete = c?.partial !== true && c?.complete !== false;
  return [complete && known(c?.doing) && c.doing > 0 ? h("span", {class: "ui-pill tone-info", "data-project-count": "doing"}, `${t("ui_status_in_progress")} ${c.doing}`) : null,
    complete && known(c?.awaiting_approval) && c.awaiting_approval > 0 ? h("span", {class: "ui-pill tone-review", "data-project-count": "review"}, `${t("ui_status_in_review")} ${c.awaiting_approval}`) : null,
    complete && known(c?.done) && known(c?.total) && c.done <= c.total ? h("span", {class: "ui-pill" + (c.total && c.done === c.total ? " tone-ok" : ""), "data-project-count": "completion"},
      t("wi_done_of", {done: c.done, total: c.total})) : null];
}
// Reorder and pin one entry among its siblings; pinned entries never cross unpinned ones.
function orderButtons(sibs, i, key, run) {
  const me = sibs[i];
  const swap = j => () => {
    const before = sibs.map(x => x[key]);
    const order = before.slice(); [order[i], order[j]] = [order[j], order[i]];
    run("order", before, order, Object.fromEntries(sibs.map(x => [x[key], x.version])));
  };
  const can = j => j >= 0 && j < sibs.length && sibs[j].pinned === me.pinned;
  const move = (offset, icon, label) => {
    const j = i + offset, enabled = can(j);
    const reason = j < 0 ? "or_order_first" : j >= sibs.length ? "or_order_last" : "or_order_pinned";
    return iconButton({icon, label: t(label), title: enabled ? t(label) : observedText(t, reason),
      disabled: !enabled, onClick: swap(j)});
  };
  const pin = iconButton({icon: "star", label: t(me.pinned ? "unpin" : "pin"),
    "aria-pressed": String(Boolean(me.pinned)), onClick: () => run("pin", me.pinned)});
  pin.classList.add("or-order-pin");
  return [move(-1, "chevron-up", "move_up"), move(1, "chevron-down", "move_down"), pin];
}
// A row's "…" opens a small form under it. While any is open, live reloads wait (a draft is never lost) and run
// once the last one closes.
let editing = 0, idleReload = null, drawerOpens = 0;
function setEditing(n) {
  editing = Math.max(0, n);
  if (!editing && idleReload) { const fn = idleReload; idleReload = null; fn(); }
}
function drawer(...children) {
  return drawerControls(false, children);
}
function recordDrawer(...children) {
  return drawerControls(true, children);
}
function drawerControls(useIcon, children) {
  const box = h("div", { class: "drawer", id: `drawer-${crypto.randomUUID()}`, hidden: true }, ...children);
  const attrs = {title: t("more"), "aria-label": t("more"), "aria-controls": box.id, "aria-expanded": "false"};
  const onClick = () => {
    box.hidden = !box.hidden;
    toggle.setAttribute("aria-expanded", String(!box.hidden));
    if (!box.hidden) drawerOpens += 1;
    setEditing(editing + (box.hidden ? -1 : 1));
  };
  const toggle = useIcon ? iconButton({icon: "more-horizontal", label: t("more"), ...attrs, onClick})
    : h("button", {class: "mini", ...attrs, onclick: onClick}, "…");
  const open = (focus = false) => {
    if (box.hidden) toggle.click();
    if (focus) box.querySelector("input:not(:disabled),textarea:not(:disabled),select:not(:disabled),button:not(:disabled),a")?.focus();
  };
  box.addEventListener("keydown", event => {
    if (event.key === "Escape" && !event.isComposing) {event.preventDefault(); if (!box.hidden) toggle.click(); toggle.focus();}
  });
  return { box, toggle, open, close: () => { if (!box.hidden) toggle.click(); } };
}
function freshPage() { editing = 0; idleReload = null; } // a render rebuilt every drawer closed
// Views' render(fromEvent): a live reload never rebuilds under an open drawer, and no render rebuilds under a
// drawer opened while it was fetching (the person started typing after the click that asked for it).
function typing() { // a field in the page has focus: a live reload would throw away what is being typed
  const a = document.activeElement;
  return Boolean(a?.closest?.("#main")) && (a.tagName === "TEXTAREA" || a.tagName === "SELECT"
    || (a.tagName === "INPUT" && !["checkbox", "radio", "button", "submit"].includes(a.type)));
}
function holdRender(fromEvent, opensAtStart) {
  return (editing > 0 && (fromEvent || drawerOpens !== opensAtStart)) || (fromEvent && typing());
}
document.addEventListener("focusout", () => setTimeout(() => {
  if (!editing && !typing() && idleReload) { const fn = idleReload; idleReload = null; fn(); }
}, 0));
function liveReload(fn, kinds, prepare = null, holding = () => editing || typing()) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const later = debounceRefresh(async () => {
    let preparedAt = 0;
    const prepareNow = async () => {if (prepare) {await prepare(); preparedAt = Date.now();}};
    await prepareNow();
    for (;;) {
      while (holding()) {
        assertView(connection); await sleep(100);
        // The held checkpoint also holds the next event page. Keep safety evidence current
        // while an editor stays open, so a later parent archive does not wait for that page.
        if (prepare && Date.now() - preparedAt >= 1000) await prepareNow();
      }
      assertView(connection);
      const opened = drawerOpens;
      await fn(true);
      assertView(connection);
      if (!holding() && drawerOpens === opened) return;
    }
  }, 500);
  return onEvents(ev => { if (typeof kinds === "function" ? kinds(ev) : kinds.includes(ev.resource_type)) return later(); });
}

async function viewProjects(main) {
  freshPage();
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  let request = 0;
  const out = h("div", {});
  const tree = h("div", {"data-project-list": ""});
  const archived = h("div", {});
  const create = createRecordDisclosure({h, t, api, caps: () => state.caps, guard: () => assertView(connection),
    namespace: connection.namespace, kind: "project", errorBox, opStatus,
    label: t("create_project"), hook: "data-project-new", onCreated: async op => {location.hash = `#/project/${op.result.project_id}`;}});
  const showArchived = h("input", { type: "checkbox" });
  const refresh = iconButton({icon: "refresh-cw", label: t("global_refresh"), onClick: () => render().catch(() => {})});
  const filter = h("label", {class: "capture-choice or-archive-filter"}, showArchived, h("span", {}, t("show_archived")));
  const note = manageNote();
  const help = disclosure({label: t("global_details"), body: h("p", {}, t("projects_help"))});
  create.box.hidden = true; filter.hidden = true; help.hidden = true;
  if (note) note.hidden = true;
  main.append(pageHeader({title: t("nav_projects"), actions: [refresh]}),
    note || "", create.actions, create.box, out, sectionCard({body: h("div", {class: "or-item-list"}, filter, tree, archived, help)}));
  const render = async (fromEvent = false) => {
    const opens = drawerOpens, mine = ++request;
    let received = false;
    try {
      const data = await api("GET", `/projects${showArchived.checked ? "?include_archived=true" : ""}`);
      received = true;
      assertView(connection);
      if (!tree.isConnected || mine !== request) return;
      if (!Array.isArray(data.projects)) throw new Error(observedText(t, "or_projects_unknown"));
      create.setContext({rows: data.projects});
      if (holdRender(fromEvent, opens)) { idleReload = () => render(true); return; }
      freshPage();
      create.setAvailable(may("manage") && state.caps?.actions?.find(row => row.action === "project.create")?.allowed !== false);
      filter.hidden = false; refresh.hidden = false;
      help.hidden = false; if (note) note.hidden = false;
      const rows = [];
      const walk = (sibs, depth, parent) => sibs.forEach((p, i) => {
        const msg = out; // outside the tree: it survives the re-render below
        const run = async (what, before, order, versions) => {
          if (what === "order") await change(msg, "project.order", {}, { parent_id: parent, order }, { before, expected_versions: versions }, `project.order.${parent}`);
          else await change(msg, "project.pin", { project_id: p.project_id }, { pinned: !before }, { before }, `project.pin.${p.project_id}`);
          render();
        };
        const rename = h("input", { value: p.name, maxlength: 80 });
        const sub = h("input", { placeholder: t("new_sub_project"), maxlength: 80 });
        const d = recordDrawer(
          h("div", { class: "filters" }, rename, h("button", { class: "secondary", onclick: async () => {
            const ok = await change(msg, "project.update", { project_id: p.project_id }, { name: rename.value.trim() },
              { expected_version: p.version }, `project.rename.${p.project_id}`);
            if (ok || STALE.includes(lastFailure)) render();
          } }, t("rename"))),
          h("div", { class: "filters" }, sub, h("button", { class: "secondary", onclick: async () => {
            if (!sub.value.trim()) return;
            if (await change(msg, "project.create", {}, { name: sub.value.trim(), parent_id: p.project_id }, {},
              `project.create.${p.project_id}`)) { d.close(); render(); }
          } }, t("add_project"))),
          h("div", { class: "actions" }, h("button", { class: "danger", onclick: async () => {
            const ok = await change(msg, "project.update", { project_id: p.project_id }, { archived: true },
              { expected_version: p.version }, `project.archive.${p.project_id}`);
            if (ok || STALE.includes(lastFailure)) render();
          } }, t("archive"))));
        const rowCounts = counts(p.counts).filter(Boolean);
        const compactCounts = rowCounts.map(node => node.dataset.projectCount === "completion" ? `${p.counts.done}/${p.counts.total}`
          : t(node.dataset.projectCount === "doing" ? "project_row_active" : "project_row_review",
            {count: node.dataset.projectCount === "doing" ? p.counts.doing : p.counts.awaiting_approval})).join(" · ");
        rowCounts.forEach(node => node.classList.add("record-row-count"));
        const row = indent(h("div", { class: "row tree record-row" },
          h("div", { class: "grow" }, h("a", { class: "title", href: `#/project/${p.project_id}` }, p.name),
            p.description ? h("div", { class: "muted clamp" }, p.description) : null),
          h("div", {class: "record-row-meta"}, ...rowCounts,
            h("span", {class: "record-row-summary", title: rowCounts.map(node => node.textContent).join(" · ")},
              compactCounts),
            may("manage") ? h("span", { class: "tree-actions" }, d.toggle) : null)), depth);
        if (may("manage")) d.box.prepend(h("div", {class: "actions"}, ...orderButtons(sibs, i, "project_id", run)));
        if (may("manage")) row.querySelector(".tree-actions").prepend(treeInteractions({h, t, row, scope: `${connection.namespace}.project.${parent}`,
          siblings: sibs, index: i, key: "project_id", run, open: () => d.open(true), guard: () => assertView(connection),
          editing: active => setEditing(editing + (active ? 1 : -1))}));
        rows.push(row, d.box);
        walk(p.children, depth + 1, p.project_id);
      });
      walk(data.projects, 0, "");
      fill(tree, ...(rows.length ? rows : [h("p", { class: "muted" }, t("no_projects"))]));
      fill(archived, ...(data.archived || []).map(p => h("div", { class: "row" },
        h("div", { class: "grow" }, h("span", { class: "muted" }, p.name)),
        may("manage") ? h("button", { class: "secondary", onclick: async () => {
          await change(out, "project.update", { project_id: p.project_id }, { archived: false },
            { expected_version: p.version }, `project.restore.${p.project_id}`);
          render();
        } }, t("restore")) : null)));
    } catch (error) {
      if (connection.generation !== generation || connection.epoch !== state.epoch || mine !== request || !tree.isConnected) return;
      request++; create.setContext({rows: [], active: false}); create.setAvailable(false);
      fill(out); fill(archived); filter.hidden = true; refresh.hidden = true; freshPage();
      help.hidden = true; if (note) note.hidden = true;
      errorBox(error);
      fill(tree, unavailableProject({h, error, collection: true, transportFailed: !received && error?.status === undefined,
        connection: () => {viewState(connection.namespace, "", "settings").section = "connection"; location.hash = "#/settings";},
        retry: () => render().catch(() => {})}));
    }
  };
  showArchived.onchange = () => render();
  await render();
  return liveReload(render, ["project", "work_item"]);
}

async function viewProject(main, pid) {
  freshPage();
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  let draft = null; // the project form's values after a stale save, put back into the reloaded form
  let request = 0, prepared = null, currentEditor = null;
  let workEditors = new Map();
  const workDrafts = new Map();
  const head = h("div", { class: "panel", "data-project-summary": "" });
  const items = h("div", {"data-project-items": ""});
  const work = h("section", {class: "panel", hidden: true, "data-project-work-list": true});
  const out = h("div", {});
  const archived = h("div", {});
  const showArchived = h("input", { type: "checkbox" });
  const create = createRecordDisclosure({h, t, api, caps: () => state.caps, guard: () => assertView(connection),
    namespace: connection.namespace, kind: "work_item", projectId: pid, showScope: false, errorBox, opStatus,
    label: t("new_item_title"), hook: "data-project-work-create", onCreated: async () => {await render();}});
  const filter = h("label", {class: "capture-choice or-archive-filter"}, showArchived, h("span", {}, t("show_archived")));
  const itemCard = sectionCard({title: t("work_items"), actions: [filter], body: h("div", {class: "or-item-list"}, create.actions, create.box, items, archived)});
  itemCard.dataset.projectItemCard = ""; itemCard.hidden = true; create.box.hidden = true;
  const note = manageNote(); if (note) note.hidden = true;
  const github = boardGitHubPanel({t, api, alive: () => assertView(connection), caps: () => state.caps,
    form: options => pmDurableForm({h, t, api, caps: () => state.caps, namespace: connection.namespace,
      guard: () => assertView(connection), errorBox, ...options,
      canSend: () => state.online && !state.nativeBusy && state.viewReady,
      clientGate: () => clientActionGate(state, {post: true})}),
    permitted: () => may('manage'), projectId: pid, onSaved: () => render()});
  const offGitHubOnline = onOnline(() => {try {assertView(connection); github.update();} catch { /* retired view */ }});
  main.append(note || "", out, head, github.element, work, itemCard);
  const rememberEditors = () => {
    if (currentEditor && !currentEditor.drawer.box.hidden) draft = currentEditor.values();
    for (const [id, editor] of workEditors) if (!editor.drawer.box.hidden)
      workDrafts.set(id, {child: editor.child.value, branch: editor.branch.value});
  };
  const withdraw = (error, transportFailed) => {
    rememberEditors(); workEditors.clear();
    currentEditor = null; prepared = null; request++;
    create.setContext({rows: [], active: false}); create.setAvailable(false);
    fill(work); work.hidden = true; fill(items); fill(archived); fill(out); itemCard.hidden = true;
    if (note) note.hidden = true;
    errorBox(error);
    fill(head, unavailableProject({h, error, transportFailed, retry: () => render().catch(() => {})})); freshPage();
  };
  const readProject = async () => {
    const mine = ++request;
    let received = false;
    try {
      const data = await api("GET", `/projects/${encodeURIComponent(pid)}${showArchived.checked ? "?include_archived=true" : ""}`);
      received = true;
      assertView(connection);
      if (mine !== request) return null;
      if (data.project?.project_id !== pid) throw new Error(t("workspace_work_missing"));
      return data;
    } catch (error) {
      if (connection.generation !== generation || connection.epoch !== state.epoch || mine !== request) return null;
      withdraw(error, !received && error?.status === undefined); throw error;
    }
  };
  const prepare = async () => {
    prepared = null;
    const data = await readProject();
    if (data) prepared = {data, request};
  };
  const render = async (fromEvent = false) => {
    const opens = drawerOpens;
    const data = prepared?.request === request ? prepared.data : await readProject().catch(() => null);
    prepared = null;
    if (!data || !head.isConnected) return;
    if (holdRender(fromEvent, opens)) { idleReload = () => render(true); return; }
    rememberEditors(); workEditors = new Map();
    freshPage();
    const p = data.project;
    await github.refresh(data);
    if (note) note.hidden = false;
    itemCard.hidden = false;
    const mayCreate = may("manage") && !p.archived && state.caps?.actions?.find(row => row.action === "work_item.create")?.allowed !== false;
    create.setAvailable(mayCreate);
    create.setContext({rows: data.work_items, name: p.name, active: !p.archived});
    work.hidden = !Array.isArray(data.work);
    fill(work, h("h2", {}, t("delivery_project_work")),
      ...(data.work?.length ? data.work.map(item => deliveryWork(item, p.repositories)) : [h("p", {class: "muted"}, t("delivery_no_work"))]));
    const msg = out;
    const v = draft || { name: p.name, description: p.description, repositories: p.repositories, task_project: p.task_project || "" };
    const f = {
      name: h("input", { value: v.name, maxlength: 80, "aria-label": t("name") }),
      description: h("textarea", {"aria-label": t("description")}, v.description),
      repositories: h("input", { value: v.repositories.join(", "), placeholder: "owner/name, owner/name", "aria-label": observedText(t, "or_repositories") }),
      task_project: h("input", { value: v.task_project, placeholder: t("task_project"), "aria-label": t("task_project") }),
    };
    const d = recordDrawer(h("label", {}, t("name")), f.name, h("label", {}, t("description")), f.description,
      h("label", {}, observedText(t, "or_repositories")), f.repositories, h("label", {}, t("task_project")), f.task_project,
      h("div", { class: "actions" }, h("button", { class: "primary", onclick: async () => {
        const params = { name: f.name.value.trim(), description: f.description.value,
          repositories: f.repositories.value.split(/[\s,]+/).filter(Boolean), task_project: f.task_project.value.trim() };
        const ok = await change(msg, "project.update", { project_id: pid }, params, { expected_version: p.version },
          `project.edit.${pid}`);
        if (!ok && STALE.includes(lastFailure)) draft = params;
        if (ok || draft) render();
      } }, t("save"))));
    d.box.dataset.projectEditor = "";
    fill(head,
      h("div", { class: "muted" }, h("a", { href: "#/projects" }, t("nav_projects")),
        ...data.path.flatMap(x => [" / ", h("a", { href: `#/project/${x.project_id}` }, x.name)])),
      pageHeader({title: p.name, pill: p.archived ? chip(t("archived"), "warn") : null}),
      p.description ? h("details", {}, h("summary", {}, t("description"), " · ", p.description.replace(/\s+/g, " ").slice(0, 140), p.description.length > 140 ? "…" : ""),
        h("p", {class: "pre"}, p.description)) : null,
      h("div", { class: "actions project-summary-actions" }, ...counts(p.counts), ...p.repositories.map(r => chip(r)),
        may("manage") && !p.archived && state.caps?.features?.project_dispatch?.version === 1 ? h("a", {class: "session-project-link", href: `#/dispatch/${pid}`}, t("dispatch_title")) : null,
        p.task_project ? chip(`Task Service: ${p.task_project}`) : null, may("manage") && !p.archived ? d.toggle : null),
      may("manage") && !p.archived ? d.box : null,
      data.sub_projects.length ? h("p", {}, t("sub_projects"), ": ",
        ...data.sub_projects.flatMap((x, i) => [i ? " · " : "", h("a", { href: `#/project/${x.project_id}` }, x.name)])) : null);
    currentEditor = {drawer: d, values: () => ({name: f.name.value.trim(), description: f.description.value,
      repositories: f.repositories.value.split(/[\s,]+/).filter(Boolean), task_project: f.task_project.value.trim()})};
    if (draft && may("manage") && !p.archived) { draft = null; d.open(); }
    const rows = [];
    const walk = (sibs, depth, parent) => sibs.forEach((w, i) => {
      const rowMsg = out; // outside the tree: it survives the re-render below
      const run = async (what, before, order, versions) => {
        if (what === "order") await change(rowMsg, "work_item.order", { project_id: pid }, { parent_id: parent, order }, { before, expected_versions: versions }, `wi.order.${pid}.${parent}`);
        else await change(rowMsg, "work_item.pin", { work_item_id: w.work_item_id }, { pinned: !before }, { before }, `wi.pin.${w.work_item_id}`);
        render();
      };
      const child = h("input", { placeholder: t("new_child_item"), maxlength: 120 });
      const branch = h("input", { placeholder: t("new_branch_item"), maxlength: 120 });
      const retained = workDrafts.get(w.work_item_id);
      if (retained) {child.value = retained.child; branch.value = retained.branch;}
      const create = (input, params, scope) => h("button", { class: "secondary", onclick: async () => {
        if (!input.value.trim()) return;
        if (await change(rowMsg, "work_item.create", { project_id: pid }, { title: input.value.trim(), ...params }, {}, scope)) render();
      } }, t("add_item"));
      const dr = recordDrawer(
        h("div", { class: "filters" }, child, create(child, { parent_id: w.work_item_id }, `wi.child.${w.work_item_id}`)),
        h("div", { class: "filters" }, branch, create(branch, { parent_id: parent || null, derived_from: w.work_item_id },
          `wi.branch.${w.work_item_id}`)),
        h("div", { class: "actions" }, h("button", { class: "danger", onclick: async () => {
          const ok = await change(rowMsg, "work_item.update", { work_item_id: w.work_item_id }, { archived: true },
            { expected_version: w.version }, `wi.archive.${w.work_item_id}`);
          if (ok || STALE.includes(lastFailure)) render();
        } }, t("archive_with_children"))));
      dr.box.dataset.workItemInspector = "";
      workEditors.set(w.work_item_id, {drawer: dr, child, branch});
      if (retained && may("manage") && !p.archived) {workDrafts.delete(w.work_item_id); dr.open();}
      const done = w.steps.filter(s => s.done).length;
      const rowState = () => {const pill = stateChip(w.completion); pill.classList.add("record-row-status"); return pill;};
      const row = indent(h("div", { class: "row tree record-row" }, rowState(),
        h("div", { class: "grow" }, h("a", { class: "title", href: `#/item/${w.work_item_id}` }, w.title),
          w.derived_from ? h("span", { class: "muted" }, " ⑂") : null),
        h("div", {class: "record-row-meta"}, rowState(), w.steps.length ? chip(`${done}/${w.steps.length}`) : null,
          may("manage") && !p.archived ? h("span", { class: "tree-actions" }, dr.toggle) : null)), depth);
      if (may("manage") && !p.archived) dr.box.prepend(h("div", {class: "actions"}, ...orderButtons(sibs, i, "work_item_id", run)));
      if (may("manage") && !p.archived) row.querySelector(".tree-actions").prepend(treeInteractions({h, t, row,
        scope: `${connection.namespace}.item.${pid}.${parent}`, siblings: sibs, index: i, key: "work_item_id", run,
        open: () => dr.open(true), guard: () => assertView(connection), editing: active => setEditing(editing + (active ? 1 : -1))}));
      rows.push(row, may("manage") && !p.archived ? dr.box : null);
      walk(w.children, depth + 1, w.work_item_id);
    });
    walk(data.work_items, 0, "");
    fill(items, ...(rows.length ? rows : showArchived.checked && data.archived?.length ? [] : [emptyState({title: t("no_items"), heading: "h3"})]));
    fill(archived, ...(data.archived || []).map(w => h("div", { class: "row" },
      h("div", { class: "grow" }, h("a", { class: "muted", href: `#/item/${w.work_item_id}` }, w.title)),
      may("manage") && !p.archived ? h("button", { class: "secondary", onclick: async () => {
        await change(out, "work_item.update", { work_item_id: w.work_item_id }, { archived: false },
          { expected_version: w.version }, `wi.restore.${w.work_item_id}`);
        render();
      } }, t("restore")) : null)));
  };
  showArchived.onchange = () => render();
  await render();
  const off = liveReload(render, ["project", "work_item", "operation", "task", "execution", "session", "integration"], prepare);
  return () => {off?.(); offGitHubOnline(); github.dispose();};
}

function linkTarget(l) {
  const x = l.target || {};
  if (!x.found) return h("span", { class: "muted" }, l.ref, " · ", t("link_missing"));
  if (l.kind === "session") {
    const [host, ...rest] = l.ref.split("/");
    return h("span", {}, h("a", { href: `#/session/${encodeURIComponent(host)}/${encodeURIComponent(rest.join("/"))}` }, x.title || l.ref),
      " ", chip(x.api_access === "managed" ? t("managed") : t("read_only"), x.api_access === "managed" ? "managed" : "readonly"),
      x.gone ? chip(t("stale_reason_gone"), "stale") : null);
  }
  if (l.kind === "operation") {
    return h("span", {}, h("a", { href: `#/op/${l.ref}` }, actionLabel(x.action)), " ", h("span", { class: `status-${x.status}` }, t("op_" + x.status)),
      x.session ? [" · ", h("a", { href: `#/session/${encodeURIComponent(x.session.host)}/${encodeURIComponent(x.session.session_id)}` }, t("open_new_session"))] : null);
  }
  if (l.kind === "checkpoint") {
    return h("span", {}, h("a", { href: `#/session/${encodeURIComponent(x.host)}/${encodeURIComponent(x.source_session_id)}` },
      h("code", {}, x.commit_sha.slice(0, 12))), " ", x.branch || "", " · ", x.host);
  }
  if (l.kind === "task") return h("span", {}, observationLink("execution", l.ref), " ", x.project, " · ", x.state);
  return h("a", { href: `https://github.com/${x.repository}/pull/${x.number}`, target: "_blank", rel: "noopener" },
    `${x.repository}#${x.number}`);
}

async function viewWorkItem(main, wid) {
  freshPage();
  const notice = h("div", {}); // outside the panel: a refusal stays on screen after the panel re-renders
  const panel = h("div", {});
  const reading = h("div", {});
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  let mutable = true;
  const archiveLocks = new Map();
  const blocked = h("p", {class: "note warn", hidden: true}, t("parent_archived"));
  main.append(blocked);
  const requireMutable = () => {
    assertView(connection);
    if (!mutable) throw new ApiError(409, "ITEM_ARCHIVED", t("parent_archived"));
  };
  const refreshSafety = async () => {
    try {
      const data = await api("GET", `/work-items/${wid}`); assertView(connection);
      rememberObservation("work_item", wid, data, itemDependencies(data));
      mutable = !data.work_item.archived && !data.project.archived;
      blocked.hidden = mutable;
      if (!mutable) {
        // Leave fields and the drawer toggle usable so archived parents never destroy drafts.
        for (const button of panel.querySelectorAll("button")) {
          if (button.getAttribute("aria-label") !== t("more")) {
            if (!archiveLocks.has(button)) archiveLocks.set(button, button.disabled); button.disabled = true;
          }
        }
        for (const control of panel.querySelectorAll("select,input[type=checkbox]")) {
          if (!archiveLocks.has(control)) archiveLocks.set(control, control.disabled); control.disabled = true;
        }
      } else {
        for (const [control, disabled] of archiveLocks) control.disabled = disabled;
        archiveLocks.clear();
      }
    } catch (error) { fill(notice, errorBox(error, {within:notice})); throw error; }
  };
  let draft = null; // the edit form's values after a stale save, put back into the reloaded form
  // Fields made once and moved into each render: a re-render never throws away what is being typed.
  const newStep = h("input", { placeholder: t("new_step"), maxlength: 300 });
  const kind = h("select", {}, ...["session", "checkpoint", "operation", "task", "pull_request", "github_source"].map(k =>
    h("option", { value: k }, t("link_" + k))));
  const ref = h("input", { placeholder: t("link_ref_hint") });
  kind.onchange = () => { ref.placeholder = t("link_ref_" + kind.value); };
  kind.onchange();
  const results = state.caps?.features?.work_item_results?.version === 1 ? resultSourcesPanel({h, t, api,
    guard: () => assertView(connection), workItemId: wid, linkTarget, onEvents, errorBox}) : null;
  main.append(manageNote() || "", notice, reading, results?.box || "", panel);
  let displayedItem = null, renderQueue = Promise.resolve();
  const showReading = (w, progress) => {
    const readingSupported = state.caps?.features?.work_item_reads?.version === 1 && progress;
    reading.replaceChildren();
    if (readingSupported) {
      const canRead = state.caps.actions?.some(a => a.action === "work_item.read" && a.allowed);
      const mark = h("button", {class: "secondary", disabled: !canRead || progress.read_version >= w.version, onclick: async () => {
        try {
          assertView(connection); mark.disabled = true;
          await change(notice, "work_item.read", {work_item_id: wid}, {}, {expected_version: w.version}, `wi.read.${wid}.${w.version}`);
          assertView(connection); await render(true);
        } catch (error) {
          if (!["VIEW_CHANGED", "CONNECTION_CHANGED"].includes(error.code)) fill(notice, errorBox(error, {sourceRefresh:false, within:notice}));
        } finally {if (mark.isConnected) mark.disabled = !canRead || progress.read_version >= w.version;}
      }}, t("reading_mark"));
      reading.append(h("div", {class: "panel reading-state"},
        h("div", {class: "actions"}, chip(t(progress.unread ? "reading_unread" : "reading_read")), mark),
        h("p", {class: "muted"}, t("reading_note")),
        progress.current_version > w.version ? h("p", {class: "muted"}, t("reading_newer")) : null));
    }
  };
  const render = (fromEvent = false) => {
    const work = renderQueue.catch(() => {}).then(() => renderNow(fromEvent));
    renderQueue = work; return work;
  };
  const renderNow = async (fromEvent = false) => {
    if (!panel.isConnected) return;
    const opens = drawerOpens;
    let data;
    try { data = await api("GET", `/work-items/${wid}`); }
    catch (e) { fill(panel, errorBox(e, {within:panel})); return; }
    if (!panel.isConnected) return;
    assertView(connection);
    rememberObservation("work_item", wid, data, itemDependencies(data));
    if (holdRender(fromEvent, opens)) {
      if (displayedItem) showReading(displayedItem, data.work_item.reading);
      idleReload = () => render(true); return;
    }
    freshPage();
    const w = data.work_item, c = w.completion;
    displayedItem = w; showReading(w, w.reading);
    const live = mutable = !w.archived && !data.project.archived;
    blocked.hidden = live;
    const pre = { expected_version: w.version };
    const update = (params, scope) => { requireMutable(); return change(notice, "work_item.update", { work_item_id: wid }, params, pre, scope); };
    const decide = async (action, scope) => { requireMutable(); await change(notice, action, { work_item_id: wid }, {}, { expected_fingerprint: c.fingerprint }, scope); render(); };
    // completion: an agent's "done" is a claim; a person accepts it for the content shown here
    const approve = h("button", { class: "primary", disabled: !live || !may("approve"), title: may("approve") ? null : t("needs_approve_scope"),
      onclick: () => decide("work_item.approve", `wi.approve.${wid}.${c.fingerprint}`) }, c.pending ? t("accept_done") : t("mark_done"));
    const keepGoing = h("button", { class: "secondary", disabled: !live || !may("manage"),
      onclick: () => decide("work_item.continue", `wi.continue.${wid}.${c.fingerprint}`) }, t("keep_working"));
    let banner = null;
    if (c.approved) banner = h("p", { class: "note ok" }, timeLabel(t, "approved_by", { who: c.approved_by, time: when(epoch(c.approved_at)) }));
    else if (c.pending && w.state === "done") banner = h("div", { class: "note warn" }, h("p", {}, t("claimed_done", { who: c.claimed_by || "?" })),
      h("div", { class: "actions" }, approve, keepGoing), may("approve") ? null : h("p", { class: "muted" }, t("needs_approve_scope")));
    else if (c.pending) banner = h("div", { class: "note warn" }, h("p", {}, t("steps_all_checked")), h("div", { class: "actions" }, approve, keepGoing));
    const stateSel = h("select", { disabled: !live || !may("manage") }, ...["todo", "doing", "waiting"].map(s =>
      h("option", { value: s, selected: w.state === s }, t("wi_state_" + s))));
    if (w.state === "done") stateSel.prepend(h("option", { value: "done", selected: true }, t("wi_state_" + c.display_state)));
    stateSel.onchange = async () => { await update({ state: stateSel.value }, `wi.state.${wid}`); render(); };
    // content: goal, the request verbatim, acceptance
    const v = { ...w, ...(draft || {}) };
    const f = { title: h("input", { value: v.title, maxlength: 120 }), goal: h("textarea", {}, v.goal),
      request: h("textarea", {}, v.request), acceptance: h("textarea", {}, v.acceptance) };
    const attachment = attachmentDraft(`wi.edit.${wid}`, f.request, v.attachments || [], true);
    attachment.bindFields(f);
    const d = drawer(h("label", {}, t("title")), f.title, h("label", {}, t("goal")), f.goal, h("label", {}, t("request")), f.request,
      h("label", {}, t("acceptance")), f.acceptance, attachment.box, h("div", { class: "actions" }, h("button", { class: "primary", onclick: async () => {
        const params = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, k === "title" ? el.value.trim() : el.value])
          .filter(([k, v]) => v !== w[k]));
        if (!attachment.ready()) { fill(notice, h("p", { class: "error" }, t("attachments_not_ready"))); return; }
        if (JSON.stringify(attachment.refs()) !== JSON.stringify(w.attachments || [])) params.attachments = attachment.refs();
        if (!Object.keys(params).length && !attachment.pending()) { d.close(); return; }
        let op;
        try { requireMutable(); op = await attachment.perform("work_item.update", {work_item_id: wid}, params, pre, `wi.edit.${wid}`); }
        catch (e) { fill(notice, errorBox(e, {sourceRefresh:false, within:notice})); if (STALE.includes(e.code)) {draft = params; render();} return; }
        const ok = op.status === "succeeded";
        if (!ok) fill(notice, opStatus(op), " ", h("a", { href: `#/op/${op.operation_id}` }, op.operation_id));
        if (!ok && STALE.includes(lastFailure)) draft = params;
        if (ok || draft) render();
      } }, t("save"))));
    const section = (label, text) => text ? [h("details", {class: "panel"},
      h("summary", {}, label, " · ", text.replace(/\s+/g, " ").slice(0, 140), text.length > 140 ? "…" : ""),
      h("div", {class: "pre"}, text))] : [];
    // steps: checking one is an edit like any other (an unchecked step reopens a done item)
    const setSteps = async (steps, added) => {
      if (await update({ steps }, `wi.steps.${wid}`) && added) newStep.value = "";
      render();
    };
    const stepRows = w.steps.map((s, i) => h("div", { class: "step" },
      h("label", {}, h("input", { type: "checkbox", checked: s.done, disabled: !live || !may("manage"),
        onchange: () => setSteps(w.steps.map((x, j) => (j === i ? { ...x, done: !x.done } : x))) }), " ", s.text),
      live && may("manage") ? h("button", { class: "mini", title: t("remove"), "aria-label": t("remove"),
        onclick: () => setSteps(w.steps.filter((_, j) => j !== i)) }, "×") : null));
    const addStep = h("button", { class: "secondary", disabled: !live || !may("manage"), onclick: () => {
      if (newStep.value.trim()) setSteps([...w.steps, { text: newStep.value.trim(), done: false }], true);
    } }, t("add"));
    // links: the sessions, checkpoints, operations, tasks and PRs that carried this item
    const link = async (params, scope, typed) => {
      requireMutable();
      if (await change(notice, "work_item.link", { work_item_id: wid }, params, {}, scope)) {
        if (typed) ref.value = "";
        render();
      }
    };
    const linkRows = data.links.map(l => h("div", { class: "row" }, chip(t("link_" + l.kind)),
      h("div", { class: "grow" }, linkTarget(l), l.note ? h("div", { class: "muted" }, l.note) : null,
        h("div", { class: "muted" }, l.linked_by, " · ", when(epoch(l.linked_at)))),
      l.kind === "checkpoint" && l.target?.found && live ? continueFrom(w, l.ref, notice, data.children) : null,
      live && may("manage") ? h("button", { class: "mini", title: t("remove"), "aria-label": t("remove"),
        onclick: () => link({ kind: l.kind, ref: l.ref, remove: true }, `wi.unlink.${wid}.${l.kind}.${l.ref}`) }, "×") : null));
    const brief = x => h("div", { class: "row" }, chip(t("wi_state_" + x.display_state)),
      h("a", { class: "grow", href: `#/item/${x.work_item_id}` }, x.title));
    fill(panel,
      h("div", { class: "muted" }, h("a", { href: "#/projects" }, t("nav_projects")), " / ",
        h("a", { href: `#/project/${data.project.project_id}` }, data.project.name),
        ...data.path.flatMap(x => [" / ", h("a", { href: `#/item/${x.work_item_id}` }, x.title)])),
      pageHeader({title: w.title, pill: h("span", {class: "ui-title-row"}, stateChip(c), w.archived ? chip(t("archived"), "warn") : null)}),
      data.derived_from ? h("p", { class: "muted" }, t("derived_from"), " ",
        h("a", { href: `#/item/${data.derived_from.work_item_id}` }, data.derived_from.title)) : null,
      banner,
      h("div", { class: "actions" }, h("label", {}, t("state"), " ", stateSel),
        !c.pending && !c.approved && live ? approve : null, live && may("manage") ? d.toggle : null),
      d.box,
      ...section(t("goal"), w.goal), ...section(t("request"), w.request), ...section(t("acceptance"), w.acceptance),
      h("h2", {}, t("steps_title")), h("div", { class: "panel" }, ...(stepRows.length ? stepRows : [h("p", { class: "muted" }, t("no_steps"))]),
        live && may("manage") ? h("div", { class: "filters" }, newStep, addStep) : null),
      h("div", { class: "actions" }, h("a", { href: `#/cleanup/item/${wid}` }, t("nav_cleanup"))),
      h("h2", {}, t("links")), h("div", { class: "panel" },
        ...(linkRows.length ? linkRows : [h("p", { class: "muted" }, t("no_links"))]),
        live && may("manage") ? h("div", { class: "filters" }, kind, ref, h("button", { class: "secondary", onclick: () => {
          if (ref.value.trim()) link({ kind: kind.value, ref: ref.value.trim() }, `wi.link.${wid}`, true);
        } }, t("link"))) : null),
      data.children.length ? [h("h2", {}, t("children")), h("div", { class: "panel" }, ...data.children.map(brief))] : null,
      data.derived.length ? [h("h2", {}, t("derived")), h("div", { class: "panel" }, ...data.derived.map(brief))] : null,
      h("h2", {}, t("history")), h("div", { class: "panel" }, ...data.events.map(ev => h("div", { class: "row" },
        h("div", { class: "grow" }, t("ev_" + ev.kind.replace(".", "_")), h("div", { class: "muted" }, eventDetail(ev.body))),
        h("span", { class: "muted" }, ev.actor || "", " · ", when(epoch(ev.created_at)))))));
    if (draft) { draft = null; d.open(); }
  };
  await render();
  const repair = /^op_[0-9a-f]{32}$/.test(displayedItem?.operation_id) && state.caps?.features?.managed_repairs?.version === 1 ? repairWorkItemPanel({h, t, api,
    guard: () => assertView(connection), projectId: displayedItem.project_id, workItemId: wid, originOperationId: displayedItem.operation_id, errorBox}) : null;
  if (repair) {panel.before(repair.box); await repair.refresh();}
  try {assertView(connection);} catch {repair?.dispose(); results?.dispose(); return;}
  const offRepair = repair ? onEvents(event => {
    if (event.resource_type === "operation" || observationAffected("work_item", wid, event)) return repair.refresh();
  }) : null;
  // In a linked Work, this details panel shares the page with the instruction
  // composer. Its safety read must settle without waiting for that other editor
  // to blur; renderNow still defers presentation while the person is typing.
  const off = liveReload(render, event => observationAffected("work_item", wid, event), refreshSafety,
    () => editing || (typing() && main.contains(document.activeElement)));
  return () => {off?.(); offRepair?.(); repair?.dispose(); results?.dispose();};
}

// Start agent work from a linked checkpoint, with this item's words as the instructions; the new operation is
// linked back so the item lists the run.
function continueFrom(w, checkpointId, notice, children = []) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const text = [w.title, w.goal, w.request && `${t("request")}:\n${w.request}`, w.acceptance && `${t("acceptance")}:\n${w.acceptance}`,
    w.steps.length ? `${t("steps_title")}:\n${w.steps.map(s => `- [${s.done ? "x" : " "}] ${s.text}`).join("\n")}` : ""]
    .filter(Boolean).join("\n\n");
  const instr = h("textarea", {}, text);
  const agent = h("select", { "aria-label": t("agent") }, h("option", { value: "claude" }, "Claude"), h("option", { value: "codex" }, "Codex"));
  const out = h("div", {"data-work-start-status": ""});
  const draft = attachmentDraft(`continue.${checkpointId}.${w.work_item_id}`, instr, (w.attachments || []).filter(x => x.role === "input"));
  draft.bindFields({agent});
  let expectedHead = null, starting = false;
  const go = h("button", { class: "primary", onclick: async () => {
    const instructions = instr.value.trim() ? instr.value : (draft.refs().length ? t("dispatch_inspect_images") : "");
    if (!instructions || starting) return;
    starting = true;
    go.disabled = false;
    go.setAttribute("aria-disabled", "true");
    out.setAttribute("aria-busy", "true");
    let op;
    try {
      if (!draft.ready() && !draft.pending()) throw new Error(t("attachments_not_ready"));
      if (state.caps?.artifacts && !expectedHead && !draft.pending()) throw new Error(t("source_unavailable"));
      op = await draft.perform("checkpoint.continue", { checkpoint_id: checkpointId },
          { instructions, agent: agent.value, ...(state.caps?.artifacts ? {artifacts: draft.refs(), work_item_id: w.work_item_id} : {}) },
          state.caps?.artifacts ? { expected_source_head_sha: expectedHead, expected_work_item_fingerprint: w.completion.fingerprint } : {}, `continue.${checkpointId}`);
    } catch (e) {
      fill(out, errorBox(e, {sourceRefresh:false, within:out}));
      if (go.isConnected) {
        go.removeAttribute("aria-disabled");
        out.removeAttribute("aria-busy");
        go.disabled = false;
      }
      starting = false;
      return;
    }
    assertView(connection);
    // Started (or refused for good): this form never starts another agent. Its result stays here; the item lists
    // the run once the form closes and the page reloads.
    const link = h("a", { href: `#/op/${op.operation_id}` }, op.operation_id);
    fill(out, opStatus(op), " ", link);
    const active = document.activeElement;
    const owned = active === go || active === document.body;
    if (go.isConnected) {
      go.removeAttribute("aria-disabled");
      out.removeAttribute("aria-busy");
      go.disabled = true;
      if (owned) link.focus();
    }
    if (TERMINAL.includes(op.status) && op.status !== "succeeded") return;
    if (!state.caps?.artifacts) {
      const linked = await change(notice, "work_item.link", {work_item_id: w.work_item_id}, {kind: "operation", ref: op.operation_id}, {},
        `wi.link.${w.work_item_id}.${op.operation_id}`);
      if (linked) out.append(" · ", t("linked_back"));
    } else out.append(" · ", t("linked_back"));
  } }, t("start_agent_work"));
  const note = confinementNote(w.links?.find(l => l.ref === checkpointId)?.target?.host, agent);
  api("GET", `/checkpoints/${encodeURIComponent(checkpointId)}`).then(x => note.setHost(x.checkpoint.host)).catch(() => {});
  const unfinished = children.filter(child => child.display_state !== "done");
  const caution = unfinished.length ? h("p", {class: "note warn"}, t("results_unfinished", {count: unfinished.length}), " ",
    ...unfinished.flatMap((child, index) => [index ? " · " : "", h("a", {href: `#/item/${encodeURIComponent(child.work_item_id)}`}, child.title)])) : null;
  const d = drawer(caution, note, instr, draft.box, h("div", { class: "actions" }, agent, go), out);
  // Starting needs start; linking the run back needs manage. Without both, nothing starts (an unlinked run is untracked).
  const why = !may("start") ? t("needs_start_scope") : !may("manage") ? t("needs_manage_scope") : null;
  const open = h("button", { class: "secondary", disabled: Boolean(why), title: why, onclick: async () => { d.toggle.click();
      if (state.caps?.artifacts && !expectedHead) { try { expectedHead = (await api("GET", `/checkpoints/${checkpointId}?live=true`)).source.head; }
        catch (e) { fill(out, errorBox(e, {within:out})); } }
    } },
    t("start_from_checkpoint"));
  return h("div", { class: "grow" }, open, d.box);
}
function eventDetail(b) {
  if (b.fields) return b.fields.map(f => t(f === "steps" ? "steps_title" : f)).join(", ");
  if (b.from && b.to) return `${t("wi_state_" + b.from)} → ${t("wi_state_" + b.to)}${b.note ? ` · ${b.note}` : ""}`;
  if (b.kind && b.ref) return `${t("link_" + b.kind)} ${b.ref}`;
  return b.note || "";
}
function linkedItems(items) {
  return h("p", { class: "muted" }, t("linked_items"), ": ",
    ...items.flatMap((x, i) => [i ? " · " : "", h("a", { href: `#/item/${x.work_item_id}` }, x.title)]));
}
function sessionWorkLine(items) {
  return h("p", {class: "session-delivery-row session-work-line"}, t("ui_work"), "\u00a0· ",
    ...items.flatMap((item, index) => [index ? " · " : "", h("a", {href: `#/item/${item.work_item_id}`}, item.title)]));
}
function workItemRow(w, open) {
  const title = w.title || w.work_item_id;
  const href = `#/item/${encodeURIComponent(w.work_item_id)}`;
  const key = w.work_item_id;
  const meta = joinLabel([w.project_name, w.completion?.claimed_by ? t("claimed_by", {who: w.completion.claimed_by}) : "",
    w.reading?.unread ? t("reading_unread") : ""]);
  return inboxRow({
    h, key, title, href, meta,
    pill: statusPill(workStatusKey(w.display_state || w.completion?.display_state)),
    action: h("a", {class: "ui-button secondary ui-sm", href}, t("global_review")),
    detailsLabel: t("global_details_for", {title}),
    onOpen: () => open?.(key, title, [
      statusPill(workStatusKey(w.display_state || w.completion?.display_state)),
      meta ? h("p", {}, meta) : null,
      w.completion?.pending ? h("p", {}, t("needs_decision")) : null,
      disclosure({label: t("global_details"), body: h("dl", {class: "kv"},
        h("dt", {}, t("global_operation_id")), h("dd", {}, h("code", {}, w.work_item_id || "—")))}),
    ]),
  });
}

// ------------------------------------------------------------------ router
async function viewCleanup(main, section, ident) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const draftKey = `batc.cleanup.draft.${connection.namespace}`;
  const pendingKey = `batc.cleanup.pending.${connection.namespace}`;
  const intentKey = `batc.cleanup.intent.${connection.namespace}`;
  const readStored = key => {
    try {return JSON.parse(localStorage.getItem(key) || sessionStorage.getItem(key) || "null");} catch {return null;}
  };
  const persist = (key, value) => {
    assertView(connection);
    try {
      // Retain old per-tab entries for compatibility while native restarts use the durable namespace.
      for (const storage of [localStorage, sessionStorage]) {
        if (value === null) storage.removeItem(key); else storage.setItem(key, JSON.stringify(value));
      }
    }
    catch (error) { if (error.code) throw error; /* this view retains the request */ }
  };
  const stored = readStored(draftKey) || {};
  const choices = ["item", "task", "host"].includes(section) && stored.id !== ident ? { discard_uncommitted: [], release_undelivered: [] }
    : (stored.choices || { discard_uncommitted: [], release_undelivered: [] });
  const pending = readStored(pendingKey);
  let intent = readStored(intentKey);
  if (intent) {
    const request = intent.request;
    const valid = request?.action === "cleanup.apply" && request.target?.preview_id === pending?.preview_id &&
      request.params?.preview_token === pending?.preview_token && request.preconditions?.preview_fingerprint === pending?.fingerprint &&
      typeof intent.key === "string" && intent.key.length > 0 && intent.key.length <= 200 && pending;
    intent = {key: valid ? intent.key : null, request: valid ? {action: "cleanup.apply", target: {preview_id: pending.preview_id},
      params: {preview_token: pending.preview_token}, preconditions: {preview_fingerprint: pending.fingerprint}} : null,
      operation_id: typeof intent.operation_id === "string" && /^op_[0-9a-f]{32}$/.test(intent.operation_id) ? intent.operation_id : null,
      refused: valid && ["PREVIEW_TOKEN_INVALID", "PREVIEW_EXPIRED", "PREVIEW_MISMATCH", "PREVIEW_BLOCKED"].includes(intent.refused) ? intent.refused : null};
  }
  let operation = null, operationRead = null, submission = null, readFailed = false;
  const supportsTask = state.caps?.features?.cleanup_task === true;
  const kind = h("select", { "aria-label": t("cleanup_target") }, ...["work_item", "checkpoint", "integration", "host", ...(supportsTask || pending?.target?.kind === "task" ? ["task"] : [])].map(k =>
    h("option", { value: k }, t("cleanup_target_" + k))));
  kind.value = section === "host" ? "host" : section === "item" ? "work_item" : section === "task" && supportsTask ? "task" : (stored.kind || "host");
  const targetId = h("input", { value: ["item", "task", "host"].includes(section) ? ident : (stored.id || ""), "aria-label": t("cleanup_id"),
    placeholder: t("cleanup_id"), class: "cleanup-id" });
  const children = h("input", { type: "checkbox", checked: stored.children || false });
  const childrenLabel = h("label", { class: "cleanup-choice" }, children, t("cleanup_children"));
  const previewOut = h("div", { "aria-live": "polite" });
  const status = h("div", { "aria-live": "polite" });
  const historyOut = h("div", { "aria-live": "polite" });
  const retainedOut = h("div", { "aria-live": "polite" });
  let doc = pending, busy = false, loading = true, pendingRequest = !!pending, previewRevision = 0;
  function changed() {
    assertView(connection);
    previewRevision++; doc = null;
    apply.disabled = true;
    reviewed.checked = false;
    persist(draftKey, { kind: kind.value, id: targetId.value, children: children.checked, choices });
    childrenLabel.hidden = kind.value !== "work_item";
    fill(status, h("p", { class: "muted" }, t("cleanup_repreview")));
  }
  function targetChanged() { choices.discard_uncommitted = []; choices.release_undelivered = []; changed(); }
  kind.addEventListener("change", targetChanged); targetId.addEventListener("input", targetChanged); children.addEventListener("change", targetChanged);
  childrenLabel.hidden = kind.value !== "work_item";
  function choice(item, key, label) {
    const input = h("input", { type: "checkbox", checked: choices[key].includes(item.resource_id),
      disabled: pendingRequest || key === "discard_uncommitted" && !may("cleanup_discard"), onchange: () => {
        choices[key] = choices[key].filter(id => id !== item.resource_id);
        if (input.checked) choices[key].push(item.resource_id);
        changed();
      } });
    return h("label", { class: "cleanup-choice" }, input, label);
  }
  function resourceRow(item) {
    const codes = (item.reasons || []).map(r => r.code);
    const eligible = item.proven && item.kind === "worktree" && (!item.task_owned || item.task_cleanup?.eligible === true);
    return h("article", { class: "cleanup-resource" },
      h("div", { class: "row" }, h("strong", { class: "grow" }, t("cleanup_kind_" + item.kind)),
        chip(t("cleanup_decision_" + item.decision), item.decision === "reclaim" ? "ok" : "")),
      h("div", { class: "cleanup-binding" }, item.host || "", " ", item.path || item.ref || item.resource_id),
      item.observation?.head ? h("p", { class: "muted" }, t("cleanup_commit_kept"), " ", h("code", {}, item.observation.head)) : null,
      item.delivery && !item.delivery.delivered ? h("p", { class: "note warn" }, t("cleanup_not_delivered")) : null,
      ...(item.reasons || []).map(r => h("div", { class: "cleanup-reason" }, h("code", {}, r.code), " · ", t("cleanup_reason_" + r.code))),
      ...(item.overridden_reasons || []).map(r => h("p", { class: "muted" }, t("cleanup_choice_" + r.code))),
      item.steps?.length ? h("p", {}, t("cleanup_plan"), ": ", item.steps.map(x => t("cleanup_step_" + x)).join(" → ")) : null,
      eligible && codes.includes("RESULTS_NOT_DELIVERED") ? choice(item, "release_undelivered", t("cleanup_release")) : null,
      eligible && !item.task_owned && codes.includes("UNCOMMITTED_CHANGES") ? choice(item, "discard_uncommitted", t("cleanup_discard")) : null,
      h("details", {}, h("summary", {}, t("cleanup_evidence")), h("pre", { class: "pre" }, JSON.stringify({
        resource_id: item.resource_id, original_ids: item.original_ids, reasons: item.reasons,
        consumers: item.consumers, task_cleanup: item.task_cleanup, delivery: item.delivery, manifest: item.observation?.manifest }, null, 2))));
  }
  const reviewed = h("input", { type: "checkbox", onchange: () => { apply.disabled = loading || busy || readFailed || Boolean(intent?.operation_id) || !doc?.ready || !may("cleanup") || !reviewed.checked; } });
  function acceptCleanup(op) {
    assertView(connection);
    if (!intent || !/^op_[0-9a-f]{32}$/.test(op?.operation_id) || op.action !== "cleanup.apply" ||
        op.actor !== state.caps.actor || op.idempotency_key !== intent.key ||
        !intent.request || op.target?.preview_id !== intent.request.target.preview_id ||
        op.params?.preview_token !== intent.request.params.preview_token ||
        op.preconditions?.preview_fingerprint !== intent.request.preconditions.preview_fingerprint ||
        (intent.operation_id && intent.operation_id !== op.operation_id)) throw new Error(t("cleanup_invalid_result"));
    operation = op; intent.operation_id = op.operation_id; persist(intentKey, intent); readFailed = false;
    fill(status, opStatus(op), " ", h("a", {href: `#/op/${op.operation_id}`}, t("cleanup_open_receipts")),
      ...(op.result?.items || []).map(r => h("p", {}, h("code", {}, r.resource_id), " · ", t("cleanup_receipt_" + r.status))));
    cleanupControls();
  }
  function cleanupControls() {
    const fixed = Boolean(intent || pendingRequest);
    kind.disabled = targetId.disabled = children.disabled = loading || busy || fixed;
    previewButton.disabled = loading || busy || fixed || section === "task" && !supportsTask;
    apply.hidden = Boolean(intent?.operation_id || intent?.refused);
    apply.disabled = loading || busy || readFailed || Boolean(intent && !intent.request) || !doc?.ready || !reviewed.checked || !may("cleanup");
    check.hidden = !intent?.operation_id; check.disabled = loading || busy || Boolean(operationRead);
    another.hidden = !(TERMINAL.includes(operation?.status) || intent?.refused);
    another.disabled = loading || busy || readFailed || Boolean(operationRead);
  }
  async function refreshCleanup(fresh = false) {
    if (submission) {await submission; assertView(connection);}
    if (operationRead) {await operationRead; if (fresh) return refreshCleanup(true); return;}
    if (!intent?.operation_id) return;
    operationRead = (async () => {const data = await api("GET", `/operations/${intent.operation_id}`); acceptCleanup(data.operation);})();
    cleanupControls();
    try {await operationRead;}
    catch (e) {assertView(connection); readFailed = true; fill(status, errorBox(e, {within:status}),
      h("a", {href: `#/op/${intent.operation_id}`}, t("cleanup_open_receipts"))); throw e;}
    finally {operationRead = null; cleanupControls();}
  }
  const check = h("button", {class: "secondary", hidden: true, onclick: () => refreshCleanup(true).catch(() => {})}, t("cleanup_check"));
  const another = h("button", {class: "secondary", hidden: true, onclick: () => {
    assertView(connection);
    if (loading || busy || readFailed || operationRead || !(TERMINAL.includes(operation?.status) || intent?.refused)) return;
    intent = operation = doc = null; pendingRequest = false;
    persist(intentKey, null); persist(pendingKey, null); reviewed.checked = false;
    previewOut.replaceChildren(); status.replaceChildren(); cleanupControls();
  }}, t("cleanup_new"));
  const apply = h("button", { class: "primary", disabled: true, onclick: async () => {
    if (loading || busy || readFailed || !doc || !reviewed.checked || intent?.operation_id || intent?.refused || intent && !intent.request) return;
    assertView(connection);
    const reviewedDoc = doc; busy = true; pendingRequest = true;
    apply.disabled = true;
    previewButton.disabled = true; kind.disabled = true; targetId.disabled = true; children.disabled = true;
    previewOut.querySelectorAll("input").forEach(input => { input.disabled = true; });
    // Keep the reviewed request as well as submit()'s stable key across a lost reply or page reload.
    persist(pendingKey, reviewedDoc);
    let finish;
    submission = new Promise(resolve => {finish = resolve;});
    try {
      if (!intent) {
        const request = {action: "cleanup.apply", target: {preview_id: reviewedDoc.preview_id},
          params: {preview_token: reviewedDoc.preview_token}, preconditions: {preview_fingerprint: reviewedDoc.fingerprint}};
        // Adopt the exact old UI key when reopening a legacy pending preview. Generic auth errors
        // after an earlier lost reply cannot prove that this key was never accepted.
        const scope = await draftId("cleanup.apply", request);
        intent = {request, key: await keyFor(scope, connection), operation_id: null}; persist(intentKey, intent);
      }
      const data = await api("POST", "/operations?wait=3", intent.request, intent.key);
      assertView(connection);
      acceptCleanup(data.operation);
      await loadHistory(); await loadRetained();
    } catch (e) {
      if (connection.epoch !== state.epoch || connection.namespace !== state.namespace || connection.generation !== generation) return;
      if (intent && !intent.operation_id && e.status >= 400 && e.status < 500 &&
          ["PREVIEW_TOKEN_INVALID", "PREVIEW_EXPIRED", "PREVIEW_MISMATCH", "PREVIEW_BLOCKED"].includes(e.code)) {
        intent.refused = e.code; persist(intentKey, intent);
      }
      fill(status, errorBox(e, {sourceRefresh:false, within:status}), h("p", {}, t(intent?.refused ? "cleanup_repreview" : "cleanup_retry_same")));
    } finally { busy = false; finish(); submission = null;
      if (connection.epoch === state.epoch && connection.generation === generation) cleanupControls(); }
  } }, t("cleanup_apply"));
  function renderPreview() {
    fill(previewOut, h("h2", {}, t("cleanup_preview")), h("p", {}, t("cleanup_counts", { reclaim: doc.impact.reclaim, retain: doc.impact.retain })),
      h("p", { class: "muted" }, timeLabel(t, "cleanup_expires", { time: when(doc.expires_at * 1000) })),
      ...(doc.items || []).map(resourceRow), !doc.ready ? h("p", { class: "note" }, t("cleanup_blocked")) : null);
  }
  const previewButton = h("button", { class: "secondary", onclick: async () => {
    if (loading) return;
    assertView(connection);
    const revision = ++previewRevision;
    previewButton.disabled = true; doc = null; apply.disabled = true; reviewed.checked = false;
    const key = { work_item: "work_item_id", checkpoint: "checkpoint_id", integration: "operation_id", host: "host", task: "task_id" }[kind.value];
    const target = { kind: kind.value, [key]: targetId.value.trim(), ...(kind.value === "work_item" ? { include_children: children.checked } : {}) };
    try {
      const result = await api("POST", "/cleanup-previews", { target, choices: structuredClone(choices) });
      assertView(connection);
      if (revision !== previewRevision) return;
      doc = result.preview; renderPreview();
      fill(status);
    } catch (e) { if (revision === previewRevision && connection.epoch === state.epoch) fill(status, errorBox(e, {sourceRefresh:false, within:status})); }
    finally { previewButton.disabled = false; }
  } }, t("cleanup_preview"));
  if (pending && section !== "resource") {
    kind.value = pending.target.kind;
    targetId.value = pending.target[{work_item: "work_item_id", checkpoint: "checkpoint_id", integration: "operation_id", host: "host", task: "task_id"}[kind.value]];
    children.checked = !!pending.target.include_children;
    childrenLabel.hidden = kind.value !== "work_item";
    renderPreview(); reviewed.checked = true; apply.disabled = !may("cleanup");
    previewButton.disabled = true; kind.disabled = true; targetId.disabled = true; children.disabled = true;
    fill(status, h("p", { class: "note" }, t("cleanup_retry_same")));
  }
  const search = h("input", { class: "cleanup-id", "aria-label": t("cleanup_search"), placeholder: t("cleanup_search") });
  async function loadHistory(cursor = "") {
    try {
      const result = await api("GET", `/cleanup-tombstones?query=${encodeURIComponent(search.value)}&cursor=${encodeURIComponent(cursor)}`);
      assertView(connection);
      const rows = (result.tombstones || []).map(x => h("article", { class: "cleanup-resource" },
        h("a", { href: `#/cleanup/resource/${x.resource_id}` }, t("cleanup_kind_" + x.kind)),
        h("div", { class: "cleanup-binding" }, x.host, " ", x.path || x.ref || ""),
        h("p", { class: "muted" }, x.actor, " · ", when(x.cleaned_at * 1000)),
        h("p", {}, t(({reviewed_cleanup: "cleanup_reason_reviewed", task_lifecycle: "cleanup_reason_automatic",
          historical_task_cleanup: "cleanup_reason_historical"})[x.reason] || "cleanup_reason_recorded")),
        ...(x.pull_requests || []).map(pr => h("p", {}, `${pr.repository} #${pr.pull_number}`))));
      if (cursor) historyOut.append(...rows); else fill(historyOut, ...rows, rows.length ? null : h("p", { class: "muted" }, t("cleanup_empty_history")));
      if (result.next_cursor) historyOut.append(h("button", { class: "secondary", onclick: e => {
        e.currentTarget.remove(); loadHistory(result.next_cursor);
      } }, t("more")));
    } catch (e) { fill(historyOut, errorBox(e, {within:historyOut})); }
  }
  async function loadRetained(cursor = "") {
    try {
      const result = await api("GET", `/cleanup-retained?cursor=${encodeURIComponent(cursor)}`);
      assertView(connection);
      const rows = (result.retained || []).map(x => h("article", { class: "cleanup-resource" },
        h("code", {}, x.commit_sha), h("div", { class: "cleanup-binding" }, x.host, " ", x.repository),
        h("p", { class: "muted" }, x.ref)));
      const unavailable = (result.unavailable || []).map(x => h("p", { class: "note warn" }, x.host, " ", x.ref, " · ", t("cleanup_unavailable")));
      if (cursor) retainedOut.append(...rows, ...unavailable); else fill(retainedOut, ...rows, ...unavailable,
        rows.length || unavailable.length ? null : h("p", { class: "muted" }, t("cleanup_empty_retained")));
      if (result.next_cursor) retainedOut.append(h("button", { class: "secondary", onclick: e => {
        e.currentTarget.remove(); loadRetained(result.next_cursor);
      } }, t("more")));
    } catch (e) { fill(retainedOut, errorBox(e, {within:retainedOut})); }
  }
  main.append(pageHeader({title: t("nav_cleanup"), actions: [iconButton({icon: "refresh-cw", label: t("global_refresh"),
      onClick: () => settleRefreshes([loadHistory(), loadRetained(), refreshCleanup(true)]).catch(() => {})})]}),
    disclosure({label: t("global_details"), body: h("div", {}, h("p", {}, t("cleanup_intro")),
      section === "task" || supportsTask ? h("p", {}, t("cleanup_task_help")) : null)}));
  if (section === "resource") {
    try {
      const data = await api("GET", `/cleanup-tombstones/${encodeURIComponent(ident)}`);
      main.append(h("a", { href: "#/cleanup" }, t("nav_cleanup")), resourceRow(data.tombstone),
        h("h2", {}, t("cleanup_open_receipts")), h("pre", { class: "panel pre" }, JSON.stringify(data.receipts, null, 2)));
    } catch (e) { main.append(errorBox(e, {within:main})); }
    return;
  }
  main.append(h("div", { class: "panel" }, h("h2", {}, t("cleanup_target")), h("div", { class: "filters" }, kind, targetId),
    childrenLabel, h("div", { class: "actions" }, previewButton)), previewOut,
    h("div", { class: "panel" }, h("label", { class: "cleanup-choice" }, reviewed, t("cleanup_reviewed")),
      !may("cleanup") ? h("p", { class: "muted" }, t("cleanup_scope")) : null, h("div", { class: "actions" }, apply, check, another), status),
    h("h2", {}, t("cleanup_history")), h("div", { class: "panel" }, h("form", { class: "filters", onsubmit: e => { e.preventDefault(); loadHistory(); } },
      search, h("button", { class: "secondary", type: "submit" }, t("cleanup_search_button"))), historyOut),
    h("h2", {}, t("cleanup_retained")), h("p", { class: "muted" }, t("cleanup_retained_help")), h("div", { class: "panel" }, retainedOut));
  cleanupControls();
  await loadHistory(); await loadRetained();
  try {await refreshCleanup();} catch { /* keep the exact accepted intent and show read error */ }
  // The router admits writes only after this initial snapshot finishes. Keep a
  // recovered Apply button disabled until then, rather than exposing a false offline refusal.
  loading = false; cleanupControls();
  const refresh = debounceRefresh(() => settleRefreshes([loadHistory(), loadRetained(), refreshCleanup(true)]), 500);
  return onEvents(ev => { if (["cleanup", "operation"].includes(ev.resource_type)) return refresh(); });
}

async function viewApprovals(main) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const panel = approvalsPanel({h, t, api, caps: () => state.caps, guard: () => assertView(connection),
    ready: () => state.online && !state.nativeBusy, errorBox, opStatus,
    storageKey: `batc.approvals.${connection.namespace}`});
  panel.box.classList.add("global-form");
  main.append(h("a", {href: "#/sessions"}, t("sessions_title")), pageHeader({title: t("bulk_title")}), panel.box);
  try {await panel.refresh();} catch { /* panel retains its fixed intent and displays the read error */ }
  return onEvents(event => event.resource_type === "operation" ? panel.refresh(true) : panel.update());
}

async function viewStart(main) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const panel = sessionStartPanel({h, t, api, caps: () => state.caps, guard: () => assertView(connection),
    submitPreference: submit, initialHost: new URLSearchParams(location.hash.split("?")[1] || "").get("host"),
    ready: () => state.online && !state.nativeBusy, errorBox, opStatus,
    storageKey: `batc.start.${connection.namespace}`});
  main.append(h("a", {href: "#/sessions"}, t("nav_sessions")), pageHeader({title: t("start_title_page")}),
    h("p", {class: "muted"}, t("start_intro")),
    ...(state.caps?.features?.repository_sync?.length ? [h("p", {}, h("a", {href: "#/published"}, t("pub_title")))] : []), panel.box, dispatchExplanation({h, t}));
  try {await panel.init();} catch { /* panel retains the original intent and shows the read error */ }
  assertView(connection);
  return onEvents(ev => {if (["operation", "host", "session"].includes(ev.resource_type)) return panel.refresh(true);});
}

async function viewOrchestration(main, selectedMode = "relay", host, sid) {
  const mode = Object.hasOwn(orchestrationActions, selectedMode) ? selectedMode : "relay";
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const context = host && sid ? {host, session_id: sid} : {};
  const panel = orchestrationPanel({h, t, api, caps: () => state.caps, guard: () => assertView(connection),
    ready: () => state.online && !state.nativeBusy, errorBox, opStatus, mode, context,
    storageKey: `batc.orchestrate.${connection.namespace}.${mode}.${JSON.stringify(context)}`});
  main.append(h("a", {href: "#/sessions"}, t("nav_sessions")), pageHeader({title: t("orch_title")}),
    h("nav", {class: "orch-modes", "aria-label": t("orch_title")}, ...Object.keys(orchestrationActions).map(key =>
      h("a", {href: `#/orchestrate/${key}`, "aria-current": key === mode ? "page" : null}, t("orch_" + key)))),
    h("h2", {}, t("orch_" + mode)), panel.box);
  try {await panel.init();} catch { /* preserve the original intent and show the read error */ }
  assertView(connection);
  return onEvents(event => {if (["operation", "session", "host"].includes(event.resource_type)) return panel.refresh(true);});
}

async function viewPublished(main) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const panel = repositoryStartPanel({h, t, api, caps: () => state.caps, guard: () => assertView(connection),
    submitPreference: submit,
    ready: () => state.online && !state.nativeBusy, errorBox, opStatus, storageKey: `batc.published.${connection.namespace}`,
    attachmentFactory: (prompt, changed) => attachmentDraft("published", prompt, [], false, changed)});
  main.append(h("a", {href: "#/sessions"}, t("nav_sessions")), pageHeader({title: t("pub_title")}),
    h("p", {class: "muted"}, t("pub_intro")), panel.box);
  try {await panel.init();} catch { /* original intent and read error remain visible */ }
  assertView(connection);
  const offOnline = onOnline(() => {try {assertView(connection); panel.update();} catch { /* retired view */ }});
  const offEvents = onEvents(ev => {if (["operation", "host", "session"].includes(ev.resource_type)) return panel.refresh(true);});
  return () => {offOnline(); offEvents();};
}

async function viewProjectDispatch(main, pid, repairWid = null) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  let repairSeed = null;
  if (repairWid) {
    try {
      if (!/^wi_[0-9a-f]{20}$/.test(repairWid) || state.caps?.features?.managed_repairs?.version !== 1) throw new Error(t("repair_unavailable"));
      const record = await api("GET", `/work-items/${repairWid}/repair`); assertView(connection);
      validateRepairRecord(record, pid, repairWid);
      if (record.dispatch_operation_id) {
        main.append(pageHeader({title: t("repair_title")}), h("p", {}, t("repair_dispatched")),
          h("a", {href: `#/op/${record.dispatch_operation_id}`}, t("repair_open_dispatch")));
        return;
      }
      repairSeed = repairDispatchSeed(record);
    } catch (error) {
      if (!["VIEW_CHANGED", "CONNECTION_CHANGED"].includes(error.code)) main.append(errorBox(error, {within:main}));
      return;
    }
  }
  const panel = repositoryStartPanel({h, t, api, caps: () => state.caps, guard: () => assertView(connection),
    submitPreference: submit, repairSeed,
    ready: () => state.online && !state.nativeBusy, errorBox, opStatus, project: pid,
    storageKey: `batc.dispatch.${connection.namespace}.${pid}${repairWid ? "." + repairWid : ""}`,
    attachmentFactory: repairSeed ? null : (prompt, changed) => attachmentDraft(`dispatch.${pid}`, prompt, [], false, changed)});
  main.append(h("a", {href: `#/project/${pid}`}, t("dispatch_back")), pageHeader({title: t("dispatch_title")}),
    repairSeed ? h("p", {class: "muted"}, t("repair_dispatch_help")) : '', panel.box, dispatchExplanation({h, t}));
  try {await panel.init();} catch { /* original intent and project read failure remain visible */ }
  try {assertView(connection);} catch {return;}
  const offOnline = onOnline(() => {try {assertView(connection); panel.update();} catch { /* retired view */ }});
  const offEvents = onEvents(ev => {
    if (["operation", "host"].includes(ev.resource_type) || ev.resource_type === "project" && ev.resource_id === pid) return panel.refresh(true);
  });
  return () => {offOnline(); offEvents();};
}

async function viewArtifactReview(main, kind, first, second) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  const context = kind === "session" ? {kind, host: first, session_id: second} : kind === "task" ? {kind, task_id: first}
    : kind === "operation" ? {kind, operation_id: first} : kind === "artifact" ? {kind, artifact_id: first, revision: Number(second)} : {kind: "catalog"};
  return mountArtifactReview({main, h, t, api, caps: () => state.caps, guard: () => assertView(connection), onEvents,
    errorBox, opStatus, context, readBrowser: (ref, size, signal) => {assertView(connection); return readArtifactContent(ref, size, state.token, signal);},
    storageKey: `batc.artifact-review.${connection.namespace}.${JSON.stringify(context)}`});
}

async function viewLinkedWorkItem(main, wid, host, sid) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  let data;
  try {data = await api("GET", `/work-items/${encodeURIComponent(wid)}`); assertView(connection);}
  catch (error) {
    if (!["VIEW_CHANGED", "CONNECTION_CHANGED"].includes(error.code)) main.append(errorBox(error, {within:main}));
    return;
  }
  const sessions = new Map();
  for (const link of data.links || []) {
    const target = link.target;
    if (!target?.found) continue;
    const session = link.kind === "session" ? target : link.kind === "operation" ? target.session : null;
    if (session?.host && session?.session_id) sessions.set(JSON.stringify([session.host, session.session_id]), session);
  }
  if (!sessions.size) return viewWorkItem(main, wid);
  const selected = host && sid ? sessions.get(JSON.stringify([host, sid])) : sessions.values().next().value;
  if (!selected) {main.append(errorBox(new Error(t("workspace_work_missing")), {within:main})); return;}
  main.classList.add("session-view");
  const itemBox = h("section", {class: "workspace-work-item"});
  const picker = sessions.size > 1 ? h("label", {class: "workspace-session-picker"}, t("nav_sessions"), " ",
    h("select", {"aria-label": t("nav_sessions"), onchange: event => {
      const [nextHost, nextSid] = JSON.parse(event.target.value);
      location.hash = `#/item/${[wid, nextHost, nextSid].map(encodeURIComponent).join("/")}`;
    }}, ...[...sessions].map(([key, session]) => h("option", {value: key, selected: session === selected},
      `${session.title || session.session_id} · ${session.host}`)))) : null;
  const offSession = await viewSession(main, selected.host, selected.session_id, {project: data.project, itemBox, picker, workRecord: data});
  try {
    assertView(connection);
    const offItem = await viewWorkItem(itemBox, wid);
    return () => {offSession?.(); offItem?.();};
  } catch (error) {
    offSession?.();
    if (!["VIEW_CHANGED", "CONNECTION_CHANGED"].includes(error.code)) throw error;
  }
}

async function viewProjectWork(main, pid, kind, id) {
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  try {
    const data = await api("GET", `/projects/${encodeURIComponent(pid)}`); assertView(connection);
    const item = data.work?.find(item => item.kind === kind && item.id === id);
    if (!item) throw new Error(t("workspace_work_missing"));
    if (item.host && item.session_id) return viewSession(main, item.host, item.session_id, {project: data.project});
    main.append(pageHeader({title: item.title || item.action || item.id}), deliveryWork(item, data.project.repositories));
  } catch (error) {
    if (!["VIEW_CHANGED", "CONNECTION_CHANGED"].includes(error.code)) main.append(errorBox(error, {within:main}));
  }
}

async function viewPmWorkspace(main, workspaceId, viewOrThread, thread) {
  const threadId = viewOrThread === 'discussions' ? thread : viewOrThread?.startsWith('pmt_') ? viewOrThread : null;
  const view = threadId ? 'discussions' : viewOrThread || lastProjectView(state.namespace, workspaceId);
  const connection = {epoch: state.epoch, namespace: state.namespace, generation};
  return mountPmWorkspace({main, h, t, api, caps: () => state.caps,
    canSend: () => !state.nativeBusy && state.online && state.viewReady, onOnline,
    clientGate: () => clientActionGate(state, {post: true}),
    guard: () => assertView(connection), namespace: connection.namespace,
    workspaceId: workspaceId === 'new' ? null : workspaceId, threadId, view, onEvents, errorBox,
    onContext: context => shell?.update(context), openCreate: workspaceId === 'new'});
}

let workspaceNav = null, workspaceIdentity = "", workspaceSidebar = null, shell = null;
const PROJECT_VIEWS = new Set(["overview", "discussions", "requirements", "schedule", "dependencies", "issues", "agents", "board"]);
let lastAppRoute = {name: "", view: ""};
let sessionFromMemory = "";
function mountWorkspace(name) {
  const identity = state.token ? `${state.epoch}:${state.namespace}:${locale}` : "";
  if (workspaceIdentity !== identity) {
    shell?.dispose(); shell = null; workspaceSidebar?.dispose(); workspaceSidebar = null;
    workspaceNav?.dispose(); workspaceNav = null; workspaceIdentity = identity;
    if (identity) {
      const connection = {epoch: state.epoch, namespace: state.namespace};
      workspaceNav = workspaceNavigation({h, t, api, guard: () => assertConnection(connection), onEvents,
        namespace: state.namespace, errorBox, caps: () => state.caps});
      document.getElementById("workspace").prepend(workspaceNav.box);
      workspaceSidebar = setupSidebarResizer({aside: workspaceNav.box, workspace: document.getElementById("workspace"), t});
      shell = dashboardShell({navigation: workspaceNav, namespace: state.namespace, caps: () => state.caps,
        api, guard: () => assertConnection(connection), onEvents, errorBox});
      shell.setRoute(name); void shell.refresh();
    }
  }
  document.body.classList.toggle("has-workspace", Boolean(state.token));
  document.body.classList.toggle("pm-mode", Boolean(state.token) && name === "pm");
  document.getElementById("main").className = ["session", "work"].includes(name) ? "session-view" : name === "pm" ? "pm-view" : "";
  document.getElementById("workspace-menu").hidden = !state.token;
  shell?.setRoute(name); workspaceNav?.select(location.hash || "#/home");
  if (!state.token) document.getElementById('nav').replaceChildren(h('a', {href: '#/settings', class: 'on'}, t('ui_settings')));
}
let teardown = null;
let generation = 0;
async function route() {
  diagnosticsPreview?.close();
  let [nextName, nextWorkspace, nextView, nextThread] = (location.hash.split('?')[0].replace(/^#\//, '') || 'home').split('/').map(decodeURIComponent);
  if (state.health && nextName !== 'settings') {
    if (nextName === 'session') selectHealthSession(state.health, nextWorkspace, nextView);
    else state.health.selected = null;
  }
  if (nextName === 'pm') {
    if (nextView?.startsWith('pmt_')) {nextThread = nextView; nextView = 'discussions';
      history.replaceState(null, '', `#/pm/${encodeURIComponent(nextWorkspace)}/discussions/${encodeURIComponent(nextThread)}`);}
    if (!nextWorkspace && lastWorkspace(state.namespace)) {nextWorkspace = lastWorkspace(state.namespace);
      history.replaceState(null, '', `#/pm/${encodeURIComponent(nextWorkspace)}`);}
    if (teardown?.navigate?.(nextWorkspace, nextThread, nextView || lastProjectView(state.namespace, nextWorkspace))) {
      // In-workspace switches return before the full route, but Back still needs this view.
      const explicit = nextView === "discussions" || String(nextView || "").startsWith("pmt_") ? "discussions" : nextView;
      const remembered = PROJECT_VIEWS.has(explicit) ? explicit : lastProjectView(state.namespace, nextWorkspace);
      lastAppRoute = {name: "pm", view: PROJECT_VIEWS.has(remembered) ? remembered : "overview"};
      return;
    }
  }
  const mine = ++generation;
  state.viewReady = false;
  if (teardown) { teardown(); teardown = null; }
  freshPage();
  let [name, ...rest] = (location.hash.split("?")[0].replace(/^#\//, "") || "home").split("/").map(decodeURIComponent);
  if (name === "home" && state.token && state.caps?.managed_installation && !state.caps.hosts?.length) name = "settings";
  if (lastAppRoute.name === "pm" && ["session", "item", "work"].includes(name) && PROJECT_VIEWS.has(lastAppRoute.view))
    sessionFromMemory = lastAppRoute.view;
  if (name === "pm") sessionFromMemory = "";
  // Record before the first await so a fast follow-up navigation still sees this view.
  if (name === "pm") {
    const explicit = rest[1] === "discussions" || String(rest[1] || "").startsWith("pmt_") ? "discussions" : rest[1];
    const remembered = PROJECT_VIEWS.has(explicit) ? explicit : lastProjectView(state.namespace, rest[0]);
    lastAppRoute = {name, view: PROJECT_VIEWS.has(remembered) ? remembered : "overview"};
  } else lastAppRoute = {name, view: lastAppRoute.view};
  mountWorkspace(name);
  // The event loop starts after the initial view read. Name that waiting state
  // immediately instead of leaving the connection control blank during startup.
  paintConnection(true);
  const main = document.getElementById("main");
  const recoveryPanel = earlierPanel, recoveryFocus = earlierPanel?.contains(document.activeElement) ? document.activeElement : null, recoveryScroll = main.scrollTop;
  main.replaceChildren();
  if (!state.token && name !== "settings") {
    const off = await viewSettings(main);
    if (mine !== generation) {off?.(); return;}
    teardown = off || null;
    return;
  }
  const views = { 'work-list': isWorkbench ? () => {} : viewSessions, pm: viewPmWorkspace, home: viewHome, projects: viewProjects, project: viewProject, item: viewLinkedWorkItem, sessions: viewSessions,
    cleanup: viewCleanup, approvals: viewApprovals, delivery: viewDelivery, operations: viewOperations, session: viewSession, start: viewStart, published: viewPublished, orchestrate: viewOrchestration, op: viewOperation, settings: viewSettings,
    "artifact-review": viewArtifactReview, work: viewProjectWork, dispatch: viewProjectDispatch,
    host: viewHostDiscovery, task: viewTask,
    worktree: (main, id) => viewObservedResource(main, "worktree", id) };
  const off = await (views[name] || viewHome)(main, ...rest);
  if (mine !== generation) { if (off) off(); return; } // the user navigated away while this view loaded
  let preferences, skills;
  if (name === "settings" && state.caps?.features?.host_preferences?.version === 1) {
    const connection = {epoch: state.epoch, namespace: state.namespace, generation};
    preferences = modelPreferencesPanel({h, t, api, caps: () => state.caps, guard: () => assertView(connection), submit,
      storageKey: `batc.model-preferences.${connection.namespace}`, onEvents, errorBox});
    const slot = main.querySelector("[data-settings-models]");
    const tab = main.querySelector('[data-settings-nav="models"]');
    if (slot) {
      slot.append(preferences.box);
      if (tab) tab.hidden = false;
      if (main.querySelector(".global-settings")?.dataset.settingsExclusive !== "true") slot.hidden = false;
      main.querySelector(".global-settings")?.reapply?.();
    }
    else main.append(preferences.box);
  }
  if (name === "project" && state.caps?.features?.project_skills?.version === 1) {
    const connection = {epoch: state.epoch, namespace: state.namespace, generation};
    skills = projectSkillsPanel({h, t, api, caps: () => state.caps, guard: () => assertView(connection), submit,
      projectId: rest[0], storageKey: `batc.project-skills.${connection.namespace}.${rest[0]}`, onEvents, errorBox});
    main.append(skills.box);
  }
  teardown = () => {off?.(); preferences?.dispose(); skills?.dispose();};
  if (off?.navigate) teardown.navigate = off.navigate;
  state.viewReady = true;
  paintEarlierDrafts(); paintEarlierNotice();
  if (recoveryPanel === earlierPanel && recoveryFocus?.isConnected && document.activeElement === document.body) {
    main.scrollTop = recoveryScroll; recoveryFocus.focus({preventScroll: true});
  }
}

async function start() {
  // Keep the first OS-derived choice too, before any setup interaction.
  setLocale(locale);
  observeViewport();
  window.addEventListener("hashchange", route);
  window.addEventListener('storage', event => {
    if (event.key && !event.key.startsWith('batc.credential-refused.')) return;
    browserCredentialRefusals.storageChanged(event);
    wakeEventPoll?.();
  });
  document.addEventListener('visibilitychange', foregroundChanged);
  window.addEventListener('pageshow', event => {if (event.persisted) foregroundChanged();});
  if (nativeDesktop) {
    await clearToken(); // discard obsolete browser credentials if an older build ever stored them
    const attempt = ++state.nativeAttempt;
    let startupManaged = false;
    try {
      const status = await nativeStatus();
      startupManaged = status.configuration_source === "managed";
      if (!status.error && status.credential_available) {
        state.nativeBusy = true;
        // Render recovery controls while network verification is pending. A slow central must
        // not leave a blank startup window; Disconnect invalidates this attempt immediately.
        route().catch(() => {});
        const caps = await nativeConnect();
        if (attempt === state.nativeAttempt) {
          state.token = "native-credential";
          await activate(caps, status.endpoint, false, status.configuration_source);
        }
      }
    } catch (error) {
      if (attempt === state.nativeAttempt) {
        disconnect(); state.connectionError = error;
        // A failed initial read is not an explicit Disconnect. Keep the already
        // prepared managed installation eligible for bounded native recovery.
        if (!startupManaged) await nativeDisconnect().catch(() => {});
      }
    } finally { if (attempt === state.nativeAttempt) state.nativeBusy = false; }
  } else {
    const browserSession = await restoreBrowserSession().catch(() => false);
    if (browserSession) await clearToken();
    state.token = browserSession ? browserSessionToken : loadToken();
    if (state.token) {
      try { await activate(await api("GET", "/capabilities")); }
      catch (error) { state.token = null; state.caps = null; state.connectionError = error; }
    }
  }
  // Start before the first view: a hung source read must not freeze freshness.
  setInterval(tickHealth, 1000);
  await route();
  streamEvents();
}
start();
