import {operationStatusLabel} from './global-labels.js';
import {timeStamp, timeLabel} from './ui/stamps.js';
import {discussionContext, discussionLabel, discussionPanel, discussionDom, discussionTitle, discussionComposer} from './pm-discussions.js';
import {icon} from './ui/icons.js';
import {parseDiscussRefs, discussHref} from './pm-discuss-link.js';
import {locale} from './i18n.js';
import {findingFromNative, findingName} from './ui/finding-name.js';
import {sessionOrigin} from './session-navigation.js';
import {viewState} from './ui/view-state.js';
import {settleRefreshes} from './state/events.ts';
import {skeleton, searchField, pageHeader, iconButton, inspector, disclosure, sectionCard, button, menu, emptyState} from './ui/components.js';
import {projectViews, rememberProjectView, availableProjectViews, forgetWorkspace} from './pm-workbench.js';
import {workspaceResources} from './pm-resources.js';
import {workspaceOverview} from './pm-overview.js';
import {workspaceDependencies} from './pm-dependencies.js';
import {workspaceCharter} from './pm-charter.js';
import {workspaceTraceability} from './pm-traceability.js';
import {workspaceAhpObserver} from './pm-ahp-observer.js';
import {planProposals} from './pm-plan-proposals.js';
import {workspaceWorkDeliveries} from './pm-work-deliveries.js';
import {workspaceSpecMaterialization} from './pm-spec-materialization.js';
import {workspaceWorkBatches} from './pm-work-batches.js';
// PM facts and operations remain central; this client submits explicit durable requests.
import {workspaceGovernance} from './pm-governance.js';
import {workspaceRegister} from './pm-register.js';
import {workspacePackageHandoff} from './pm-package-handoff.js';
import {workspaceLifecycle} from './pm-lifecycle.js';
import {workspaceIssueMappings} from './pm-issue-mappings.js';
import {specSources} from './pm-spec-sources.js';
import {workspaceFollowUp} from './pm-follow-up.js';
import {workspaceWorkPackages} from './pm-work-packages.js';
import {workspaceSprints} from './pm-sprints.js';
import {workspaceQualityReviews} from './pm-quality-reviews.js';
import {workspaceKanban} from './pm-kanban.js';
import {workspaceMeetings} from './pm-meetings.js';
import {workspaceSchedule} from './pm-schedule.js';
import {workspaceRoadmap} from './pm-roadmap.js';
import {projectBrief} from './pm-brief.js';
import {workspaceMaintenanceReview} from './pm-maintenance-review.js';
import {reviewCandidateProof} from './pm-review-candidate.js';
import {workspaceMaintenanceRepairs} from './pm-maintenance-repairs.js';
import {workspaceMaintenancePolicy} from './pm-maintenance-policy.js';
import {workspaceReleaseBaselines} from './pm-release-baselines.js';
import {workspaceEvidence} from './pm-evidence.js';
import {planRevisions} from './pm-plans.js';
import {pmWorktreeChoice} from './pm-worktree-choice.js';
import {nativeRunState, pmDiscussionMessage, pmNativeAnalysis} from './pm-native-analysis.js';
const same = (a, b) => a === b || (a && b && typeof a === 'object' && typeof b === 'object'
  && Array.isArray(a) === Array.isArray(b) && Object.keys(a).length === Object.keys(b).length
  && Object.keys(a).every(key => Object.hasOwn(b, key) && same(a[key], b[key])));
const retired = error => ['VIEW_CHANGED', 'CONNECTION_CHANGED', 'PM_DISPOSED'].includes(error.code);
// A remounted form can recover an acknowledged ID without resending an unknown
// POST. Notifications contain no result; the active form reads with current access.
const receiptSubscribers = new Map();
// An autonomous, hidden child tracks the ordinary form's connection without
// changing editor.box, form selectors, layout or the form's hidden behavior.
const formConnectionTag = 'batc-pm-form-connection';
if (!customElements.get(formConnectionTag)) customElements.define(formConnectionTag, class extends HTMLElement {
  #changed = () => {};
  observe(callback) {this.#changed = callback;}
  connectedCallback() {this.#changed(true);}
  disconnectedCallback() {this.#changed(false);}
});
export const pmAvailable = caps => caps?.features?.pm_workspaces?.version === 1 && caps?.scopes?.includes('observe');

// Save the exact request before admission. A later authorization/validation error can never
// release an earlier ambiguous request; only its matching terminal receipt permits replacement.
function durableForm({h, t, api, caps, guard, namespace, action, target, fields, params, preconditions, permitted, onSaved, errorBox, label, extra, canSend = () => true, onStateChange = () => {}, onConnectionChange = () => {}, recoverPermitted = permitted, requiredScope = 'manage', onBeforeSaved = () => {}, onError = () => false, statusText = operationStatusLabel, pruneEmpty = false}) {
  const storage = `batc.pm.${namespace}.${action}.${JSON.stringify(target)}`;
  let saved = {version: 1, values: {}}, damaged = false, busy = false, operation = null;
  const receiptDelays = [250, 500, 1000, 1500, 2000, 3000];
  let receiptTimer = null, receiptAttempt = 0, receiptPolling = true, disposed = false, stagedHere = false;
  const stopTimer = () => {clearTimeout(receiptTimer); receiptTimer = null;};
  try {
    const raw = localStorage.getItem(storage);
    if (raw) {
      saved = JSON.parse(raw);
      if (saved?.version !== 1 || !saved.values || typeof saved.values !== 'object' || Array.isArray(saved.values)
        || Object.values(saved.values).some(value => typeof value !== 'string')
        || saved.intent && (saved.intent.request?.action !== action || !same(saved.intent.request.target, target)
          || typeof saved.intent.key !== 'string' || !saved.intent.key
          || saved.intent.operation_id && !/^op_[0-9a-f]{32}$/.test(saved.intent.operation_id))) throw Error();
    }
  } catch {damaged = true;}
  const status = h('div', {role: 'status'}), receipt = h('div', {class: 'pm-receipt'});
  const persist = () => {guard(); if (pruneEmpty && !saved.intent && !Object.keys(saved.values).length) localStorage.removeItem(storage); else localStorage.setItem(storage, JSON.stringify(saved));};
  const capture = () => Object.fromEntries(Object.entries(fields).map(([name, input]) => [name, input.value]));
  const changed = () => {
    if (busy || saved.intent || damaged) return;
    const values = capture();
    if (same(saved.values, values)) return;
    saved.values = values;
    try {persist();} catch (error) {status.replaceChildren(errorBox(error));}
  };
  for (const [name, input] of Object.entries(fields)) {
    const value = saved.values[name] ?? input.value;
    if (input.tagName === 'SELECT' && value && ![...input.options].some(option => option.value === value)) input.append(h('option', {value}, value));
    input.value = value;
    input.addEventListener('input', changed); input.addEventListener('change', changed);
  }
  const allowed = () => (saved.intent ? recoverPermitted() : permitted()) && caps()?.scopes?.includes(requiredScope)
    && caps()?.actions?.some(row => row.action === action && row.allowed === true);
  // An acknowledged ID can be read while sending is paused. An unknown admission
  // still needs its explicit original-key POST once the transport admits sends.
  const sendReady = () => Boolean(saved.intent?.operation_id) || canSend();
  const receiveReceipt = () => {
    if (disposed || !form.isConnected || damaged || !saved.intent) return;
    try {
      guard();
      const stored = JSON.parse(localStorage.getItem(storage));
      if (stored?.intent?.key !== saved.intent.key || !same(stored.intent.request, saved.intent.request)
        || !/^op_[0-9a-f]{32}$/.test(stored.intent.operation_id)
        || saved.intent.operation_id && saved.intent.operation_id !== stored.intent.operation_id) return;
      saved.intent.operation_id = stored.intent.operation_id;
      receiptPolling = true;
      update();
    } catch { /* a retired or unreadable form cannot apply the receipt */ }
  };
  let subscribers = null;
  const unsubscribe = () => {
    subscribers?.delete(receiveReceipt);
    if (subscribers && !subscribers.size && receiptSubscribers.get(storage) === subscribers) receiptSubscribers.delete(storage);
    subscribers = null;
  };
  const terminal = () => ['failed', 'cancelled'].includes(operation?.status);
  const reset = h('button', {type: 'button', class: 'secondary', hidden: true, onclick: () => {
    try {
      guard(); if (!terminal() && !saved.intent?.initialAdmissionRefused) return;
      saved = {version: 1, values: capture()}; persist(); operation = null; status.replaceChildren(); receipt.replaceChildren(); update();
    } catch (error) {if (!retired(error)) status.replaceChildren(errorBox(error));}
  }}, t('pm_edit_request'));
  const button = h('button', {type: 'submit', class: 'primary'}, t(label));
  const form = h('form', {'data-pm-action': action, onsubmit: event => {event.preventDefault(); void run();}},
    extra, h('div', {class: 'actions'}, button, reset), status, receipt);
  function pollReceipt() {
    // Only an acknowledged original ID may be checked automatically. An unknown
    // admission keeps its exact key and the existing explicit recovery control.
    if (disposed || receiptTimer !== null || busy || damaged || !receiptPolling || receiptAttempt >= receiptDelays.length
      || !saved.intent?.operation_id || !form.isConnected || !allowed()) return;
    if (operation && !['accepted', 'queued', 'pending', 'running', 'waiting_external', 'waiting_checks'].includes(operation.status)) return;
    try {guard();} catch {receiptPolling = false; return;}
    receiptTimer = setTimeout(() => {
      receiptTimer = null;
      void run(true);
    }, receiptDelays[receiptAttempt++]);
  }
  function update() {
    if (disposed || !form.isConnected) return;
    const locked = busy || damaged || Boolean(saved.intent) || !allowed();
    for (const input of Object.values(fields)) input.disabled = locked;
    button.disabled = busy || damaged || !allowed() || !sendReady() || terminal() || Boolean(saved.intent?.initialAdmissionRefused);
    button.textContent = t(saved.intent ? 'pm_check_original' : label);
    reset.hidden = !terminal() && !saved.intent?.initialAdmissionRefused;
    reset.disabled = busy || !allowed();
    pollReceipt();
    onStateChange();
  }
  async function run(automatic = false) {
    if (disposed || busy || damaged || automatic && (!saved.intent?.operation_id || !form.isConnected)) return;
    if (!allowed() || !sendReady()) {
      if (!automatic) {status.replaceChildren(h('p', {class: 'muted'}, t('pm_action_unavailable'))); update();}
      return;
    }
    stopTimer();
    if (!automatic) {receiptAttempt = 0; receiptPolling = true;}
    let firstAttempt = stagedHere; stagedHere = false;
    try {
      guard(); busy = true; status.replaceChildren();
      if (!saved.intent) {
        saved.values = capture();
        const request = {action, target, params: params(saved.values), preconditions: preconditions()};
        saved.intent = {key: crypto.randomUUID(), request}; persist(); firstAttempt = true;
        operation = null; receipt.replaceChildren();
      }
      update();
      const intent = saved.intent, actor = caps()?.actor; persist();
      let collected = false;
      const collectReceipt = data => {
        if (collected) return;
        const op = data.operation;
        if (!/^op_[0-9a-f]{32}$/.test(op?.operation_id) || op.actor !== actor || op.idempotency_key !== intent.key
          || !same({action: op.action, target: op.target, params: op.params, preconditions: op.preconditions}, intent.request)
          || intent.operation_id && intent.operation_id !== op.operation_id) throw Error(t('pm_receipt_mismatch'));
        // Only enrich the still-matching original intent. Do not overwrite a newer
        // draft or save any result/private source after a view/identity change.
        const stored = JSON.parse(localStorage.getItem(storage));
        if (stored?.intent?.key === intent.key && same(stored.intent.request, intent.request)) {
          if (stored.intent.operation_id && stored.intent.operation_id !== op.operation_id) throw Error(t('pm_receipt_mismatch'));
          stored.intent.operation_id = op.operation_id;
          localStorage.setItem(storage, JSON.stringify(stored));
          for (const notify of receiptSubscribers.get(storage) || []) notify();
        }
        collected = true;
      };
      const data = intent.operation_id ? await api('GET', `/operations/${intent.operation_id}`, undefined, undefined, collectReceipt)
        : await api('POST', '/operations?wait=3', intent.request, intent.key, collectReceipt);
      collectReceipt(data);
      guard();
      if (disposed) return;
      if (!allowed()) {status.replaceChildren(h('p', {class: 'muted'}, t('pm_action_unavailable'))); return;}
      const op = data.operation;
      saved.intent.operation_id = op.operation_id; persist(); operation = op;
      receipt.replaceChildren(h('a', {href: `#/op/${op.operation_id}`}, t('pm_operation')), ' · ', statusText(op.status));
      if (op.status === 'succeeded') {
        onBeforeSaved(op.result, saved.values);
        saved = {version: 1, values: {}}; persist();
        for (const input of Object.values(fields)) input.value = input.dataset.pmDefault || '';
        await onSaved(op.result); guard(); status.replaceChildren(h('span', {class: 'muted', 'data-pm-saved': ''}, t('pm_saved')));
      } else if (!onError(op.error || {})) status.replaceChildren(h('p', {class: 'muted'}, t(terminal() ? 'pm_terminal' : 'pm_pending')));
    } catch (error) {
      receiptPolling = false;
      // Reporting a read/write error also affects the current event checkpoint.
      // A detached request must not poison another view's successful refresh or
      // change an original intent after a route/authority boundary has moved.
      if (disposed) return;
      try {guard();} catch {return;}
      if (!retired(error)) {
        // Explicit server pre-admission refusal is useful only for the first attempt.
        if (firstAttempt && error.admissionRefused === true && !saved.intent?.operation_id) {
          saved.intent.initialAdmissionRefused = true;
          try {persist();} catch { /* keep the frozen original in memory */ }
        }
        const handled = onError(error);
        if (!handled) status.replaceChildren(errorBox(error));
        if (error.code === 'VERSION_CONFLICT' && !handled) status.append(h('p', {class: 'muted'}, t('pm_stale')));
      }
    } finally {busy = false; update();}
  }
  if (damaged) status.append(h('p', {class: 'error'}, t('pm_damaged')));
  const connection = h(formConnectionTag, {hidden: true});
  connection.observe(connected => {
    onConnectionChange(connected);
    if (disposed) return;
    if (connected) {
      subscribers = receiptSubscribers.get(storage) || new Set();
      subscribers.add(receiveReceipt); receiptSubscribers.set(storage, subscribers);
      receiveReceipt(); update();
    } else {stopTimer(); unsubscribe();}
  });
  form.append(connection);
  return {box: form, fields, update, changed, hasDraft: () => Object.keys(saved.values).length > 0, submit: run, edit: () => reset.click(), replaceable: () => terminal() || Boolean(saved.intent?.initialAdmissionRefused), canSubmit: () => !disposed && !busy && !damaged && allowed() && sendReady() && !terminal() && !saved.intent?.initialAdmissionRefused, operation: () => operation, locked: () => busy || damaged || Boolean(saved.intent),
    stage: () => {
      if (disposed || busy || saved.intent || damaged) return false;
      guard(); saved.values = capture();
      saved.intent = {key: crypto.randomUUID(), request: {action, target, params: params(saved.values), preconditions: preconditions()}};
      persist(); stagedHere = true; update(); return true;
    },
    discard: () => {if (busy || saved.intent || damaged) return; saved.values = {}; persist();},
    checking: () => busy || receiptTimer !== null,
    recheck: () => {
      if (disposed || busy || !saved.intent?.operation_id || !allowed()) return;
      receiptAttempt = 0; receiptPolling = true; void run(true);
    },
    dispose: () => {
      disposed = true; receiptPolling = false; stopTimer(); unsubscribe(); onConnectionChange(false);
      for (const input of Object.values(fields)) input.disabled = true;
      button.disabled = reset.disabled = true;
    },
    set: (name, value) => {if (!busy && !saved.intent && !damaged) {fields[name].value = value; changed();}},
    values: () => capture()};
}

export async function mountPmWorkspace({main, h, t, api, caps, guard, namespace, workspaceId, threadId, view, onEvents, errorBox, canSend = () => true, onOnline = () => () => {}, onContext = () => {}, openCreate = false}) {
  if (!pmAvailable(caps())) {main.append(h('p', {}, t('pm_unavailable'))); return;}
  // Navigation preference must not wait for unrelated secondary reads to settle.
  if (workspaceId && threadId) rememberProjectView(namespace, workspaceId, 'discussions');
  let disposed = false, workspace = null, threadData = null, loaded = false, initialReadComplete = false, refreshing = false, sequence = 0, threadPages = 1, lifecycle = null;
  const launch = parseDiscussRefs(location.hash.split('?')[1] || '');
  const uiState = viewState(namespace, workspaceId, 'discussions');
  const handoffKey = `batc.pm.${namespace}.discussion-send.${workspaceId}`;
  const originKey = `batc.pm.${namespace}.discussion-origin.${workspaceId}.`;
  let sendHandoff = null, firstMessagePending = false;
  try {sendHandoff = JSON.parse(localStorage.getItem(handoffKey));} catch { /* An invalid draft cannot authorize a write. */ }
  const staleSections = new Set(), activationReads = new Map(), maskedSources = new Map();
  let discussionTools = null, boundContext = null, panelContext = null, renameForm = null, threadSearch = null, titleView = null;
  let draftSourceUnavailable = false;
  async function draftSourceRefs(refs = launch.refs, reads = new Map()) {
    const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}`;
    const read = path => {
      if (!reads.has(path)) reads.set(path, api('GET', endpoint + path));
      return reads.get(path);
    };
    const unavailable = () => {throw Error(t('pd_unavailable'));};
    const scoped = (value, id, key) => {
      if (!value || value.workspace_id !== workspaceId || key && value[key] !== id) unavailable();
      return value;
    };
    const text = value => typeof value === 'string' && value.trim() && !/(?:pm[a-z]*|wi|op)_[0-9a-f]{20,64}/.test(value.trim()) ? value.trim() : '';
    return Promise.all(refs.map(async (ref, position) => {
      if (ref.available === false) return {kind: ref.kind, position, available: false};
      if (ref.available === true && !['governance','plan_revision'].includes(ref.kind) && text(ref.label))
        return {...ref, label:text(ref.label), bound_label:text(ref.bound_label)};
      try {
        let label = '', recordType = null, findingIdentity = null;
        if (ref.kind === 'governance') {
          const value = await read(`/governance/${encodeURIComponent(ref.id)}`);
          scoped(value, workspaceId, 'workspace_id');
          if (value.record?.record_id !== ref.id) unavailable();
          label = text(value.revision?.document?.title);
          recordType = ['risk','issue','action','change','decision'].includes(value.record.kind) ? value.record.kind : 'issue';
        } else if (ref.kind === 'plan_revision' || ref.kind === 'requirement') {
          const id = ref.kind === 'plan_revision' ? ref.id : ref.revision_id;
          const plan = scoped((await read(`/plans/${encodeURIComponent(id)}`)).plan, id, 'revision_id');
          if (ref.kind === 'requirement') {
            const requirement = plan.document?.requirements?.find(item => item.id === ref.id);
            if (!requirement) unavailable();
            label = text(requirement.text);
          } else label = [text(plan.document?.title), Number.isSafeInteger(plan.sequence) ? t('pm_revision_number', {number: plan.sequence}) : ''].filter(Boolean).join(' · ');
        } else if (ref.kind === 'work_item') {
          const value = await read(`/kanban/${encodeURIComponent(ref.id)}`);
          scoped(value, workspaceId, 'workspace_id');
          const card = value.card;
          if (card?.work_item_id !== ref.id || card.work?.work_item_id !== ref.id || card.binding?.workspace_id !== workspaceId || card.binding?.work_item_id !== ref.id) unavailable();
          label = text(card.work.title);
        } else if (ref.kind === 'snapshot') {
          const snapshot = scoped((await read(`/snapshots/${encodeURIComponent(ref.id)}`)).snapshot, ref.id, 'snapshot_id');
          label = text(snapshot.title);
        } else if (ref.kind === 'milestone') {
          const revision = scoped((await read(`/roadmap/revisions/${encodeURIComponent(ref.revision_id)}`)).revision, ref.revision_id, 'revision_id');
          const milestone = revision.document?.milestones?.find(item => item.milestone_id === ref.id);
          if (!milestone) unavailable();
          label = text(milestone.title);
        } else if (ref.kind === 'finding') {
          const value = scoped(await read('/maintenance-findings/current'), workspaceId, 'workspace_id');
          if (value.visibility_filtered || value.observation_ref?.observation_id !== ref.observation_id || value.observation_ref?.document_digest !== ref.document_digest) unavailable();
          const finding = Object.values(value.sources || {}).flatMap(source => source.findings || []).find(item => item.finding_id === ref.id);
          if (!finding) unavailable();
          const identity = findingFromNative(finding);
          const named = findingName(identity, locale, '');
          if (named) {label = named; findingIdentity = identity;}
          else {
            label = text(finding.title) || text(finding.statement) || text(finding.facts?.statement) || text(finding.native?.title);
            if (!label && Number.isSafeInteger(finding.native?.number)) label = `#${finding.native.number}`;
          }
        } else if (ref.kind === 'repair') {
          const value = scoped(await read(`/maintenance-repairs/${encodeURIComponent(ref.id)}`), workspaceId, 'workspace_id');
          const repair = value.repair;
          if (repair?.repair_id !== ref.id) unavailable();
          const work = await draftSourceRefs([{kind:'work_item', id:repair.work_item_id}], reads);
          if (work[0].available === false) unavailable();
          label = work[0].label;
        } else if (ref.kind === 'follow_up') {
          const value = scoped(await read(`/follow-ups/${encodeURIComponent(ref.id)}`), workspaceId, 'workspace_id');
          if (value.intake?.intake_id !== ref.id) unavailable();
          label = text(value.intake.document?.title) || text(value.intake.document?.statement);
        } else if (ref.kind === 'proposal') {
          const value = scoped(await read(`/conversation-proposals/${encodeURIComponent(ref.id)}`), workspaceId, 'workspace_id');
          if (value.proposal?.proposal_id !== ref.id) unavailable();
          label = text(value.proposal.document?.title);
        } else if (ref.kind === 'issue_mapping') {
          const value = scoped(await read(`/issue-mappings/${encodeURIComponent(ref.id)}`), workspaceId, 'workspace_id');
          if (value.current?.mapping_id !== ref.id) unavailable();
          const work = await draftSourceRefs([{kind:'work_item', id:value.current.work_item_id}], reads);
          if (work[0].available === false) unavailable();
          label = text(value.current.document?.public_title) || work[0].label;
        } else if (ref.kind === 'meeting') {
          const value = scoped(await read(`/meetings/${encodeURIComponent(ref.id)}`), workspaceId, 'workspace_id');
          if (value.meeting?.meeting_id !== ref.id) unavailable();
          label = text(value.meeting.title);
        } else if (ref.kind === 'lifecycle_transition') {
          const value = scoped(await read(`/lifecycle/transitions/${encodeURIComponent(ref.id)}`), workspaceId, 'workspace_id');
          if (value.transition?.transition_id !== ref.id) unavailable();
          label = text(value.transition.reason);
        } else unavailable();
        const result = {...ref, available:true, label: label || t('ui_reason_not_recorded'), record_type: recordType, bound_label: text(ref.bound_label)};
        if (ref.kind === 'finding') {if (findingIdentity) result.finding = findingIdentity; else delete result.finding;}
        return result;
      } catch (error) {
        // Display metadata never authorizes Send, and failed reads never consult a URL or old label.
        return {kind: ref.kind, position, available: false};
      }
    }));
  }
  const threadRows = h('div', {class: 'pd-thread-rows'});
  const threadState = viewState(namespace, workspaceId, 'discussion.' + (threadId || 'list'));
  // Bound cosmetic return locations without evicting durable intents or drafts.
  const rememberOrigin = (id, origin) => {
    const indexKey = originKey + 'index';
    let index = []; try {index = JSON.parse(localStorage.getItem(indexKey)) || [];} catch {}
    if (!Array.isArray(index)) index = [];
    index = [...new Set([...Object.keys(localStorage).filter(key => key.startsWith(originKey) && key !== indexKey).map(key => key.slice(originKey.length)), ...index])];
    index = index.filter(value => typeof value === 'string' && /^pmt_[0-9a-f]{32}$/.test(value) && value !== id);
    index.push(id);
    while (index.length > 40) localStorage.removeItem(originKey + index.shift());
    for (const key of Object.keys(localStorage)) if (key.startsWith(originKey) && key !== indexKey && !index.includes(key.slice(originKey.length))) localStorage.removeItem(key);
    localStorage.setItem(originKey + id, JSON.stringify(origin));
    localStorage.setItem(indexKey, JSON.stringify(index));
  };
  const backLink = () => {
    let savedOrigin = {};
    try {savedOrigin = JSON.parse(localStorage.getItem(originKey + threadId)) || {};} catch {}
    const source = parseDiscussRefs(new URLSearchParams({from: launch.from || savedOrigin.from || '', report: launch.report || savedOrigin.report || ''}));
    const origin = source.from || 'discussions';
    const session = sessionOrigin(origin);
    return h('a', {class: 'pd-back', href: session || `#/pm/${encodeURIComponent(workspaceId)}/${origin}`, 'data-discussion-back': '', onclick: () => {
      // Restore the originating fixed report even after a discussion-draft reload.
      if (origin === 'overview' && source.report) Object.assign(viewState(namespace, workspaceId, 'overview'), {snapshotId: source.report, mode: 'saved'});
    }}, icon('chevron-left'), t('pd_back', {view: t(session ? 'ui_session' : 'ui_' + origin)}));
  };
  if (threadId) h = discussionDom(h, JSON.stringify([namespace, workspaceId, threadId]));
  const forms = new Set(), alive = () => {guard(); if (disposed) throw Object.assign(Error(), {code: 'PM_DISPOSED'});};
  const permitted = decide => loaded && Boolean(workspace) && (caps()?.scopes?.includes('admin')
    || (decide ? workspace.membership?.role === 'owner' : ['owner', 'contributor'].includes(workspace.membership?.role)));
  const input = (label, {multiline = false, max = 2000, required = true, value = ''} = {}) => {
    const field = h(multiline ? 'textarea' : 'input', {'aria-label': t(label), maxlength: max, required}); field.value = value;
    return field;
  };
  const labelled = (label, field) => h('label', {}, t(label), field);
  const form = options => {
    const value = durableForm({h, t, api, caps, guard: alive, namespace, errorBox, statusText: threadId ? value => discussionLabel(t, 'pd_op_', value) : undefined, ...options, canSend,
      permitted:()=>initialReadComplete&&options.permitted()&&(lifecycle?.newActionAllowed(options.action,()=>options.params(Object.fromEntries(Object.entries(options.fields).map(([key,input])=>[key,input.value]))))??true),
      recoverPermitted:()=>initialReadComplete&&(options.recoverPermitted||options.permitted)(),
      onConnectionChange: connected => {
        if (connected && !disposed) forms.add(value);
        else forms.delete(value);
        if (connected && disposed) value.dispose();
      }});
    const dispose = value.dispose;
    value.dispose = () => {forms.delete(value); dispose();};
    return value;
  };
  const status = h('div', {role: 'status', class: 'pm-status'}), workspaceList = h('div', {class: 'pm-workspace-list'});
  const name = input('pm_workspace_name', {max: 120}), repositories = input('pm_repositories', {required: false});
  const workspaceForm = form({action: 'pm.workspace.create', target: {}, fields: {name, repositories},
    params: values => ({name: values.name.trim(), repositories: values.repositories.split(/[\n,]/).map(value => value.trim()).filter(Boolean)}),
    preconditions: () => ({}), permitted: () => true, label: 'pm_create_workspace',
    onSaved: result => {alive(); location.hash = `#/pm/${encodeURIComponent(result.workspace.workspace_id)}`;},
    extra: h('div', {class: 'pm-fields'}, labelled('pm_workspace_name', name),
      h('details', {}, h('summary', {}, t('pm_repositories_optional')), labelled('pm_repositories', repositories), h('p', {class: 'muted'}, t('pm_repository_help'))))});
  const title = h('span', {}, t('pm_title'));
  const refresh = iconButton({icon: 'refresh-cw', label: t('pm_refresh'), 'data-pm-refresh': '', onClick: () => void load()});
  const phasePill = h('span', {class: 'ui-pill tone-neutral', hidden: true});
  const viewMeta = h('p', {class: 'ui-meta', 'data-pm-work-meta': '', hidden: true});
  const heading = pageHeader({title, pill: phasePill, meta: viewMeta, actions: [refresh]});
  viewMeta.hidden = true;
  const headerMeta = h('p', {class: 'ui-meta', 'data-dependencies-meta': '', hidden: true});
  const headerPills = h('span', {class: 'dep-header-pills', hidden: true});
  heading.querySelector('.ui-title-row')?.append(headerPills);
  heading.querySelector('.ui-page-heading')?.append(headerMeta);
  const registerMeta = h('p', {class: 'ui-meta', 'data-pm-header-meta': '', hidden: true});
  heading.querySelector('.ui-page-heading')?.append(registerMeta);
  const setHeaderMeta = text => {registerMeta.textContent = text || ''; registerMeta.hidden = !text;};
  const content = h('div', {class: 'pm-content', 'data-discussions': ''}), body = h('section', {class: 'pm-main'}, heading, status, content);
  const creationHome = h('div', {hidden: true}, workspaceForm.box);
  main.append(h('div', {class: 'pm-shell'}, body, creationHome));
  let createPanel = null;
  function showCreate() {
    if (disposed) {location.hash = '#/pm/new'; return;}
    createPanel?.close();
    createPanel = inspector({title: t('ui_new_workspace'), onClose: () => {creationHome.append(workspaceForm.box); createPanel = null;}});
    createPanel.body.append(workspaceForm.box); main.append(createPanel);
  }
  let lastList = [], lastThreadList = null;
  function publishContext() {
    const stage = workspace ? lifecycle?.phase?.() : null;
    phasePill.hidden = !['idea','planning','development','maintain','paused','closed'].includes(stage);
    phasePill.textContent = phasePill.hidden ? '' : t('ui_phase_' + stage);
    onContext({workspace, workspaces: lastList, view: threadId ? 'discussions' : workbench?.selected() || view || 'discussions',
      views: threadId ? availableProjectViews(caps()) : workbench?.views || (workspace ? ['discussions'] : []), create: showCreate,
      phase: phasePill.hidden ? null : phasePill.textContent,
      threadCount: lastThreadList?.threads?.length ?? null, threadMore: Boolean(lastThreadList?.next_cursor)});
  }
  let mountedThread = null, notes = null, facts = null, draft = null, noteForm = null, factForm = null, planForm = null, planningRevisionForm = null,
    sourceSelect = null, slotSelect = null, permissions = null, coverage = null, threadTitle = null, mode = null, factSignature = '', earlier = null, historyCursor = null, historyExpanded = false,
    packages = null, packageSignature = '', revisions = null, pmCapabilities = null, worktreeChoice = null, nativeAnalysis = null, proposalReview = null, discussionHint = null;
  lifecycle = workspaceId && caps()?.features?.pm_workspaces?.lifecycle?.version === 1
    ? workspaceLifecycle({h,t,api,alive,workspaceId,caps,permitted:()=>loaded&&workspace?.membership?.role==='owner',form,input,labelled,errorBox,onChange:()=>{updateForms();publishContext();}}) : null;
  if(lifecycle&&!threadId)body.insertBefore(lifecycle.element,content);
  const qualitySelections = new Map();
  const qualitySelection = !threadId && caps()?.features?.pm_workspaces?.quality_reviews?.version === 1
    ? id => qualitySelections.get(id) || null : null;
  const evidence = workspaceId && threadId !== 'new' && caps()?.features?.pm_workspaces?.work_evidence
    ? workspaceEvidence({h, t, api, alive, namespace, workspaceId, workspace: () => workspace, permitted, form, input, labelled, errorBox, qualitySelection}) : null;
  if (evidence) body.append(evidence.box);
  let workbench = null;
  const liveOverview = !threadId && caps()?.features?.pm_overview?.version === 1;
  const overviewMeta = h('p', {class: 'ui-meta', hidden: true});
  heading.querySelector('.ui-page-heading').append(overviewMeta);
  const charter = workspaceId && !threadId && caps()?.features?.pm_workspaces?.plan_drafting;
  const brief = workspaceId && caps()?.features?.pm_workspaces?.brief_snapshots
    ? charter ? (liveOverview ? workspaceOverview : workspaceCharter)({h, t, api, alive, namespace, workspaceId, permitted, form, input, labelled, errorBox, caps, canRead: () => loaded && Boolean(workspace),
      onCompare: () => {openPanel(t('ui_compare'), brief.reportTools); void brief.prepareReports().then(() => brief.reportActions.compare());},
      onHeader: ({meta, phase, active}) => {overviewMeta.hidden = !active; overviewMeta.replaceChildren(meta);
        if (active) {phasePill.hidden = !phase; phasePill.textContent = phase || ''; }},
      onOpen: async (ref, row) => {
        if (!ref) return;
        const destination = ['requirement', 'work_item'].includes(ref.kind) ? 'requirements' : ref.kind === 'milestone' && !row.kind ? 'schedule' : 'issues';
        location.hash = `#/pm/${encodeURIComponent(workspaceId)}/${destination}`; workbench?.select(destination);
        if (destination === 'requirements') await traceability?.openRef?.(ref);
        else if (ref.kind === 'governance' || ref.kind === 'plan_revision' && register) {
          if (register) await register.openRef(ref);
          else await governance?.openRef?.(ref);
        }
        else if (ref.kind === 'milestone' && destination === 'schedule' && schedule) await schedule.openRef(ref);
        else if (ref.kind === 'milestone' && destination === 'schedule') {
          const node = roadmap?.box.querySelector(`[data-pm-milestone="${CSS.escape(ref.id)}"]`);
          if (node) {node.tabIndex = -1; node.focus(); node.scrollIntoView({block: 'nearest'});}
        } else {const returnFocus = heading.querySelector('h1'); returnFocus.tabIndex = -1; brief?.openRelated?.(row, main, returnFocus);}
      },
      ...(!liveOverview ? {onDiscuss: () => {const report = brief?.current?.()?.snapshot_id; location.hash = discussHref({workspaceId, from: 'overview', report, refs: report ? [{kind: 'snapshot', id: report}] : []});}} : {})})
      : projectBrief({h, t, api, alive, workspaceId, workspace: () => workspace, permitted, form, input, labelled, errorBox,caps,canRead:()=>loaded&&Boolean(workspace)}) : null;
  if (brief && !threadId) body.insertBefore(brief.box, lifecycle?.element||content);
  const releaseBaselines = workspaceId && !threadId && caps()?.features?.pm_workspaces?.release_baselines
    ? workspaceReleaseBaselines({h,t,api,alive,workspaceId,workspace:()=>workspace,caps,permitted,form,input,labelled,errorBox,namespace}) : null;
  if(releaseBaselines)body.insertBefore(releaseBaselines.element,content);
  const ahp = workspaceId && !threadId && caps()?.features?.pm_workspaces?.ahp_observation?.contract === 'ahp_observer_v1'
    ? workspaceAhpObserver({h,t,api,alive,caps,namespace,workspaceId,workspace:()=>workspace,form,errorBox}) : null;
  if(ahp)body.insertBefore(ahp.element,content);
  let handoff = null;
  const maintenancePolicy = workspaceId && !threadId && caps()?.features?.pm_workspaces?.maintenance_policy?.version === 1
    ? workspaceMaintenancePolicy({h,t,api,alive,workspaceId,workspace:()=>workspace,caps,permitted,form,input,labelled,errorBox,reviewedHandoff:()=>handoff?.batchSelection()}) : null;
  if(maintenancePolicy)body.insertBefore(maintenancePolicy.element,content);
  const maintenanceRepairs = workspaceId && !threadId && caps()?.features?.pm_workspaces?.maintenance_repairs?.version === 1
    ? workspaceMaintenanceRepairs({h,t,api,alive,workspaceId,workspace:()=>workspace,caps,permitted,form,labelled,errorBox,namespace}) : null;
  if(maintenanceRepairs)body.insertBefore(maintenanceRepairs.element,content);
  const maintenanceReview = workspaceId && !threadId && caps()?.features?.pm_workspaces?.maintenance_review?.version === 1
    ? workspaceMaintenanceReview({h,t,api,alive,workspaceId,workspace:()=>workspace,caps,permitted,form,input,labelled,errorBox,namespace}) : null;
  if(maintenanceReview)body.insertBefore(maintenanceReview.element,content);
  const roadmap = workspaceId && !threadId && caps()?.features?.pm_workspaces?.roadmap
    ? workspaceRoadmap({h,t,api,alive,workspaceId,permitted,form,input,labelled,errorBox}) : null;
  if(roadmap)body.insertBefore(roadmap.box,content);
  const schedule = workspaceId && !threadId && caps()?.features?.pm_schedule?.version === 1
    ? workspaceSchedule({api, alive, namespace, workspaceId, canRead: () => loaded && Boolean(workspace) && caps()?.features?.pm_schedule?.version === 1
      && caps()?.scopes?.some(scope => scope === 'observe' || scope === 'admin')}) : null;
  if (schedule) body.insertBefore(schedule.element, content);
  const kanban = workspaceId && !threadId && caps()?.features?.pm_workspaces?.kanban
    ? workspaceKanban({h,t,api,alive,namespace,workspaceId,errorBox,caps,embedded:true}) : null;
  if(kanban)body.insertBefore(kanban.element,content);
  const sprints = workspaceId && !threadId && caps()?.features?.pm_workspaces?.sprints
    ? workspaceSprints({h,t,api,alive,workspaceId,workspace:()=>workspace,permitted,form,input,labelled,errorBox,caps,qualitySelection}) : null;
  if(sprints)body.insertBefore(sprints.box,content);
  const meetings = workspaceId && threadId !== 'new' && caps()?.features?.pm_workspaces?.meetings
    ? workspaceMeetings({h,t,api,alive,namespace,workspaceId,workspace:()=>workspace,permitted,form,input,labelled,errorBox,caps}) : null;
  if(meetings)body.insertBefore(meetings.element,content);
  const governance = workspaceId && !threadId && caps()?.features?.pm_workspaces?.governance
    ? workspaceGovernance({h,t,api,alive,namespace,workspaceId,workspace:()=>workspace,permitted,form,input,labelled,errorBox,caps}) : null;
  if(governance)body.insertBefore(governance.element,content);
  let openRegisterForm = () => {};
  const routeRegisterAction = action => {
    const name = action?.action || '';
    if (name.startsWith('pm.maintenance_review.')) openRegisterForm('issues', 'ui_maintenance_review');
    else if (name.startsWith('pm.maintenance.repair')) {maintenanceRepairs?.focusFinding?.(action?.required?.finding_ref); openRegisterForm('issues', 'ui_repairs');}
    else if (name.startsWith('pm.governance.')) openRegisterForm('issues', 'ui_governance');
    else if (name.startsWith('pm.issue.')) openRegisterForm('requirements', 'ui_publication');
    else if (name.startsWith('pm.meeting.')) openRegisterForm('discussions', 'ui_meetings');
    else if (name.includes('follow_up.record')) openRegisterForm('issues', 'ui_follow_up');
  };
  const register = workspaceId && !threadId && caps()?.features?.pm_register?.version === 1
    ? workspaceRegister({h, t, api, alive, namespace, workspaceId, errorBox, onMeta: setHeaderMeta,
      onOpenWork: id => {
        // Select the mounted Requirements view before restoring its record focus.
        location.hash = `#/pm/${encodeURIComponent(workspaceId)}/requirements`;
        workbench?.select('requirements');
        void traceability?.focusWork?.(id);
      },
      onOpenRequirement: id => {
        location.hash = `#/pm/${encodeURIComponent(workspaceId)}/requirements`;
        workbench?.select('requirements');
        void traceability?.focusRequirement?.(id);
      },
      onAction: routeRegisterAction,
      nameFor: (kind, id) => traceability?.labelFor?.(kind, id) || brief?.labelFor?.(kind, id) || (kind === 'work_item' ? kanban?.titleFor?.(id) : '') || ''}) : null;
  openRegisterForm = (viewId, section) => {
    const host = body.querySelector(`[data-pm-section="${section}"]`) || viewTools[viewId]?.find(([key]) => key === section)?.[1];
    if (!host) return;
    if (matchMedia('(max-width: 1199px)').matches) register?.closeSheet?.(true);
    if (viewId && workspaceId) {
      location.hash = `#/pm/${encodeURIComponent(workspaceId)}/${viewId}`;
      workbench?.select(viewId);
    }
    const fold = host.querySelector(':scope > details');
    const focusForm = root => {
      const field = [...root.querySelectorAll('input:not([type="hidden"]), select, textarea')].find(node => !node.disabled);
      (field || root.querySelector('summary'))?.focus();
      root.scrollIntoView({block: 'nearest'});
    };
    if (host.dataset.pmTool) {openPanel(t(section), host); queueMicrotask(() => focusForm(host)); return;}
    if (fold instanceof HTMLDetailsElement) {fold.open = true; queueMicrotask(() => focusForm(fold)); return;}
    queueMicrotask(() => focusForm(host));
  };
  const specifications = workspaceId && threadId !== 'new' && caps()?.features?.pm_workspaces?.spec_sources
    ? specSources({h,t,api,alive,workspaceId,errorBox}) : null;
  if (specifications) {
    if (threadId) body.append(specifications.element);
    else body.insertBefore(specifications.element, content);
  }
  const followUp = workspaceId && threadId !== 'new' && caps()?.features?.pm_workspaces?.follow_up_prepare
    ? workspaceFollowUp({h,t,api,alive,workspaceId,permitted,form,input,labelled,errorBox,caps,namespace}) : null;
  if (followUp) body.insertBefore(followUp.element, roadmap?.box || kanban?.element || sprints?.box || meetings?.element || governance?.element || specifications?.element || content);
  const issues = workspaceId && !threadId && caps()?.features?.pm_workspaces?.issue_mapping
    ? workspaceIssueMappings({h,t,api,alive,workspaceId,permitted,form,input,labelled,errorBox}) : null;
  let workBatches = null;
  const materialization = workspaceId && !threadId && caps()?.features?.pm_workspaces?.spec_materialization?.version === 1
    ? workspaceSpecMaterialization({h,t,api,alive,workspaceId,workspace:()=>workspace,caps,permitted,form,errorBox,namespace,onSourceFailure:()=>{engineering?.withdraw();handoff?.withdraw();workBatches?.withdraw();}}) : null;
  handoff = workspaceId && !threadId && caps()?.features?.pm_workspaces?.package_handoff?.available
    ? workspacePackageHandoff({h,t,api,alive,workspaceId,workspace:()=>workspace,caps,permitted,form,input,labelled,errorBox,namespace,onReviewChange:()=>{workBatches?.update();maintenancePolicy?.update();},onSourceFailure:()=>{engineering?.withdraw();materialization?.withdraw();workBatches?.withdraw();}}) : null;
  workBatches = workspaceId && !threadId && caps()?.features?.pm_workspaces?.work_batches?.version === 1
    ? workspaceWorkBatches({h,t,api,alive,workspaceId,workspace:()=>workspace,caps,permitted,form,input,labelled,errorBox,namespace,reviewedHandoff:()=>handoff?.batchSelection()}) : null;
  const deliveries = workspaceId && !threadId && caps()?.features?.pm_workspaces?.engineering_delivery?.contract === 'pm-engineering-delivery/v1'
    ? workspaceWorkDeliveries({h,t,api,alive,workspaceId,errorBox}) : null;
  const engineering = workspaceId && !threadId && caps()?.features?.pm_workspaces?.work_package_prepare
    ? workspaceWorkPackages({h,t,api,alive,workspaceId,errorBox,onSelection:row=>{issues?.setPackage(row);handoff?.setPackage(row);void materialization?.setPackage(row);deliveries?.setWork(row?.work_item_id);}}) : null;
  if(engineering)body.insertBefore(engineering.element,specifications?.element||content);
  if(engineering&&deliveries)engineering.element.append(deliveries.element);
  if(materialization)body.insertBefore(materialization.element,specifications?.element||content);
  if(handoff)body.insertBefore(handoff.element,specifications?.element||content);
  if(workBatches)body.insertBefore(workBatches.element,specifications?.element||content);
  if(issues)body.insertBefore(issues.element,specifications?.element||content);
  const qualityReviews = workspaceId && qualitySelection
    ? workspaceQualityReviews({h,t,api,alive,workspaceId,workspace:()=>workspace,caps,permitted,form,input,labelled,errorBox,namespace,
      onSelectionChange:(id,ref)=>{if(ref)qualitySelections.set(id,ref);else qualitySelections.delete(id);}}) : null;
  if(qualityReviews)body.insertBefore(qualityReviews.element,sprints?.box||content);
  const traceability = charter && brief && kanban
    ? workspaceTraceability({h, t, api, alive, namespace, workspaceId, caps, errorBox}) : null;
  if (traceability) body.insertBefore(traceability.element, content);
  const resources = workspaceId && !threadId && caps()?.features?.pm_resources
    ? workspaceResources({h, t, api, alive, namespace, workspaceId, errorBox, bucketsSupported: caps()?.features?.pm_resources?.buckets?.granularity === 'day'}) : null;
  if (resources) body.insertBefore(resources.element, content);
  const drawersHome = h('div', {hidden: true});
  body.append(drawersHome);
  let detailPanel = null;
  const toolPreference = viewState(namespace, workspaceId, 'work-tools');
  function openPanel(label, node, refreshStale = true) {
    if (!node || !loaded) return;
    if (node === reportTools) void brief?.prepareReports?.();
    detailPanel?.close(); schedule?.setActive(false); traceability?.hideInspector(); kanban?.hideInspector();
    const home = node.parentNode, next = node.nextSibling;
    toolPreference.open = node.dataset.pmTool || null;
    detailPanel = inspector({title: label, onClose: () => {home.insertBefore(node, next?.parentNode === home ? next : null); detailPanel = null; if (!disposed) toolPreference.open = null; schedule?.setActive(workbench?.selected() === 'schedule');}});
    node.hidden = false; detailPanel.body.append(node); main.append(detailPanel);
    if (refreshStale) void refreshStaleVisible();
  }
  if (lifecycle) drawersHome.append(lifecycle.element);
  const dependencies = workspaceId && !threadId && caps()?.features?.pm_dependencies?.version === 1
    ? workspaceDependencies({h, t, api, alive, namespace, workspaceId, permitted, form, errorBox, main, header: {meta: headerMeta, pills: headerPills}}) : null;
  const headerActions = heading.querySelector('.ui-header-actions');
  const primary = brief?.primary || (charter ? brief?.box.querySelector(':scope > header .primary') : null);
  if (primary) {headerActions.prepend(primary); brief.box.querySelector(':scope > header')?.remove();}
  const reportTools = brief?.reportTools || h('div', {class: 'pm-report-tools', 'data-pm-report-tools': ''});
  if (charter && brief) {
    for (const node of brief.box.querySelectorAll(':scope > .pm-workbench-toolbar, :scope > .pm-charter-save, :scope > [data-pm-brief-export]')) reportTools.append(node);
    drawersHome.append(reportTools);
  } else if (brief && !threadId) {
    // Older central capabilities expose the immutable brief without the Charter.
    // Move the complete durable form, including its submit control and recovery state.
    const header = brief.box.querySelector(':scope > header');
    if (header?.lastElementChild) reportTools.append(header.lastElementChild);
    header?.remove();
    for (const node of brief.box.querySelectorAll(':scope > details, :scope > [data-pm-brief-export]')) reportTools.append(node);
    drawersHome.append(reportTools);
  }
  const reportMenu = reportTools.children.length ? menu({button: button({label: t('ui_report'), 'data-pm-report-menu': '', icon: 'chevron-down', iconPosition: 'end'}), items: [
    {key: 'versions', label: t('ui_report_versions'), onSelect: () => openPanel(t('ui_report'), reportTools)},
    {label: t('ui_save_snapshot'), onSelect: () => openPanel(t('ui_report'), reportTools)},
    ...(charter ? [{label: t('ui_compare'), onSelect: () => {if (brief?.reportActions) {openPanel(t('ui_compare'), reportTools); void brief.prepareReports().then(() => brief.reportActions.compare());} else reportTools.querySelector('button[data-report-compare]')?.click();}},
      {label: t('ui_print'), onSelect: () => brief?.reportActions ? brief.reportActions.print() : reportTools.querySelector('button[data-report-print]')?.click()}] : []),
    {label: t('ui_export_brief'), onSelect: () => openPanel(t('ui_report'), reportTools)},
  ]}) : null;
  if (reportMenu) headerActions.insertBefore(reportMenu, refresh);
  if (dependencies?.captureButton) headerActions.insertBefore(dependencies.captureButton, refresh);
  const issueTools = [];
  if (register) {
    if (governance) issueTools.push({key: 'governance', label: t('ui_governance'), section: 'ui_governance'});
    if (maintenanceRepairs) issueTools.push({key: 'repairs', label: t('ui_repairs'), section: 'ui_repairs'});
    if (maintenanceReview) issueTools.push({key: 'review', label: t('ui_maintenance_review'), section: 'ui_maintenance_review'});
    if (followUp) issueTools.push({key: 'follow', label: t('ui_follow_up'), section: 'ui_follow_up'});
  }
  const openIssueTool = section => {
    if (matchMedia('(max-width: 1199px)').matches) register?.closeSheet?.(true);
    const fold = body.querySelector(`[data-pm-section="${section}"] > details`);
    if (fold instanceof HTMLDetailsElement) {
      fold.open = true;
      queueMicrotask(() => {fold.querySelector('summary')?.focus(); fold.scrollIntoView({block: 'nearest'});});
      return;
    }
    body.querySelector(`[data-pm-section="${section}"]`)?.scrollIntoView({block: 'nearest'});
  };
  const overflow = menu({button: iconButton({icon: 'more-horizontal', label: t('ui_more'), 'data-pm-overflow': ''}), items: [
    ...(lifecycle ? [{key: 'phase', label: t('ui_change_phase'), onSelect: () => openPanel(t('ui_change_phase'), lifecycle.element)}] : []),
    ...(evidence ? [{key: 'settings', label: t('ui_workspace_settings'), onSelect: () => openPanel(t('ui_workspace_settings'), evidence.box)}] : []),
    ...issueTools.map(tool => ({key: tool.key, label: tool.label, onSelect: () => openIssueTool(tool.section)})),
  ]});
  if (lifecycle || evidence || issueTools.length) headerActions.append(overflow);
  const scheduleTools = schedule && (roadmap || sprints) ? menu({button: iconButton({icon: 'more-horizontal', label: t('ui_more'), 'data-schedule-tools': ''}), items: [
    ...(roadmap ? [{label: t('sched_roadmap'), onSelect: () => openPanel(t('sched_roadmap'), roadmap.box)}] : []),
    ...(sprints ? [{label: t('ui_sprints'), onSelect: () => openPanel(t('ui_sprints'), sprints.box)}] : []),
  ]}) : null;
  if (scheduleTools) {headerActions.append(scheduleTools); if (roadmap) drawersHome.append(roadmap.box); if (sprints) drawersHome.append(sprints.box);}

  const syncIssueTools = view => {
    const on = view === 'issues';
    overflow.hidden = view === 'overview' ? !(lifecycle || evidence) : !(on && issueTools.length);
    for (const key of ['phase', 'settings']) {
      const node = overflow.querySelector(`[data-menu-key="${key}"]`);
      if (!node) continue;
      node.hidden = view !== 'overview'; node.disabled = view !== 'overview';
      if (view === 'overview') node.removeAttribute('aria-disabled'); else node.setAttribute('aria-disabled', 'true');
    }
    for (const tool of issueTools) {
      const node = overflow.querySelector(`[data-menu-key="${tool.key}"]`);
      if (!node) continue;
      node.hidden = !on;
      if ('disabled' in node) node.disabled = !on;
      if (on) node.removeAttribute('aria-disabled'); else node.setAttribute('aria-disabled', 'true');
    }
  };
  const lazyThreadSections = new Map();
  const viewToolMenus = new Map();
  const viewTools = {
    requirements: [['ui_quality', qualityReviews?.element], ['ui_evidence', evidence?.box], ['ui_specs', specifications?.element],
      ['ui_materialization', materialization?.element], ['ui_packages', engineering?.element], ['ui_handoff', handoff?.element], ['ui_publication', issues?.element]],
    board: [['ui_batches', workBatches?.element]],
  };
  for (const [id, entries] of Object.entries(threadId ? {} : viewTools)) {
    const available = entries.filter(([, node]) => node);
    const items = available.map(([key, node]) => ({key, label: t(key), onSelect: () => openPanel(t(key), node)}));
    for (const [key, node] of available) {node.dataset.pmTool = key; drawersHome.append(node);}
    if (items.length) {
      const control = menu({button: iconButton({icon: 'more-horizontal', label: t('ui_more'), 'data-pm-view-tools': id}), items});
      headerActions.append(control); viewToolMenus.set(id, control);
    }
    if (id === 'requirements') {
      traceability?.setTools(() => items);
      kanban?.setTools(() => items);
    }
  }
  function updateWorkView(id) {
    activateSection(traceability, id === 'requirements'); activateSection(kanban, id === 'board');
    const meta = id === 'requirements' ? traceability?.meta : id === 'board' ? kanban?.meta : id === 'schedule' ? schedule?.meta : null;
    viewMeta.replaceChildren(...(meta ? [meta] : [])); viewMeta.hidden = !meta;
    for (const [viewId, control] of viewToolMenus) {control.hidden = viewId !== id; if (control.hidden) control.close(false);}
  }
  function secondary(label, node) {
    if (!node) return null;
    const details = disclosure({label: t(label), key: JSON.stringify([namespace, workspaceId, label]), body: node});
    const card = threadId ? h('section', {}, details) : sectionCard({body: details});
    const section = node === meetings?.element ? meetings : node === followUp?.element ? followUp : null;
    if (threadId && section) {
      lazyThreadSections.set(section, details);
      details.addEventListener('toggle', () => {if (details.open && !details.closest('[data-discussion-tab]')?.hidden && loaded) void refreshSection(section);});
    }
    card.dataset.pmSection = label; node.classList.add('pm-secondary-content'); return card;
  }
  const groups = [];
  if (workspaceId && !threadId) {
    const definitions = [
      ['overview', brief?.box || (lifecycle ? h('div') : null), []],
      ['discussions', content, [['ui_meetings', meetings?.element]]],
      ['requirements', traceability?.element || (viewTools.requirements.some(([, node]) => node) ? emptyState({title: t('wv_requirements_unavailable')}) : null), []],
      ['schedule', schedule?.element || roadmap?.box, schedule ? [] : [['ui_sprints', sprints?.box]]],
      ['dependencies', dependencies?.element || null, [['ui_policy', maintenancePolicy?.element], ['ui_baselines', releaseBaselines?.element]]],
      ['issues', register?.element || governance?.element, [...(register && governance ? [['ui_governance', governance.element]] : []), ['ui_repairs', maintenanceRepairs?.element], ['ui_maintenance_review', maintenanceReview?.element], ['ui_follow_up', followUp?.element]]],
      ['agents', resources?.element, [['ui_observations', ahp?.element]]],
      ['board', kanban?.element || (workBatches ? emptyState({title: t('wv_board_unavailable')}) : null), []],
    ];
    for (const [id, primarySection, secondaries] of definitions) {
      const sections = [primarySection, ...secondaries.map(([label, node]) => secondary(label, node))].filter(Boolean);
      if (!sections.length) continue;
      const region = h('div', {'data-pm-region': id}, ...sections); body.append(region);
      if (primarySection) {
        primarySection.classList.add('pm-primary-content');
        for (const repeated of primarySection.querySelectorAll(':scope > h2, :scope > header h2')) repeated.hidden = true;
      }
      groups.push({id, sections: [region]});
    }
    let shownView = null;
    workbench = projectViews({h, t, namespace, workspaceId, view, groups, beforeSelect: (id, previous) => {maskStaleView(id); if (previous === 'overview') brief?.setActive?.(false);}, onSelect: id => {
      if (shownView === 'issues' && id !== 'issues') register?.rememberScroll();
      if (id !== 'issues') brief?.closeRelated?.();
      title.textContent = t('ui_' + id); activateSection(resources, id === 'agents'); activateSection(schedule, id === 'schedule');
      activateSection(dependencies, id === 'dependencies');
      brief?.setActive?.(id === 'overview');
      activateSection(register, id === 'issues');
      if (scheduleTools) scheduleTools.hidden = id !== 'schedule';
      updateWorkView(id);
      if (!['requirements', 'board'].includes(id) && (id !== 'overview' || !liveOverview)) window.scrollTo(0, 0);
      if (id === 'issues') register?.restoreScroll();
      else setHeaderMeta('');
      syncIssueTools(id);
      detailPanel?.close(); if (primary) primary.hidden = id !== 'overview';
      if (reportMenu) reportMenu.hidden = id !== 'overview';
      const focused = document.activeElement;
      if (id !== 'issues' && focused && register?.element.contains(focused)) document.querySelector(`[data-pm-view="${id}"]`)?.focus();
      publishContext();
      brief?.setActive?.(id === 'overview');
      shownView = id;
      void refreshStaleVisible();
    }});
    workbench.apply(); brief?.setActive?.(workbench.selected() === 'overview');
    if (scheduleTools) scheduleTools.hidden = workbench.selected() !== 'schedule';
    shownView = workbench.selected();
    syncIssueTools(shownView);
    updateWorkView(workbench.selected());
    if (view && view !== workbench.selected()) history.replaceState(null, '', `#/pm/${encodeURIComponent(workspaceId)}/${workbench.selected()}`);
    if (primary) primary.hidden = workbench.selected() !== 'overview';
    if (reportMenu) reportMenu.hidden = workbench.selected() !== 'overview';
  }
  const sourceSections = () => [...groups.flatMap(group => group.sections), content, schedule?.element, resources?.element, dependencies?.element, traceability?.element, ahp?.element, lifecycle?.element, evidence?.box, brief?.box, releaseBaselines?.element, maintenancePolicy?.element, maintenanceRepairs?.element, maintenanceReview?.element, qualityReviews?.element, roadmap?.box, kanban?.element, sprints?.box, meetings?.element, governance?.element, register?.element, specifications?.element, followUp?.element, engineering?.element,materialization?.element,handoff?.element,workBatches?.element,issues?.element].filter(Boolean);
  const sectionModules = () => [lifecycle, evidence, brief, ahp, releaseBaselines, maintenancePolicy, maintenanceRepairs, maintenanceReview, qualityReviews, roadmap, schedule, kanban, sprints, meetings, governance, register, specifications, followUp, engineering, materialization, handoff, workBatches, issues, traceability, resources, dependencies].filter(Boolean);
  const sectionNode = section => section?.element || section?.box;
  const sectionVisible = section => {
    const node = sectionNode(section);
    if (section === brief && main.querySelector('[data-overview-related]')) return true;
    return node?.isConnected && !node.closest('[hidden], details:not([open])');
  };
  // Lifecycle is an admission source for every durable form, including forms
  // outside its drawer. Its central read cannot wait for that drawer to open.
  const sectionRequired = section => section === lifecycle || sectionVisible(section);
  function activateSection(section, value) {
    if (!section) return;
    activationReads.delete(section);
    const read = section.setActive?.(value);
    if (value && read?.then) activationReads.set(section, read);
  }
  function maskSource(section) {
    if (maskedSources.get(section)?.isConnected) return;
    maskedSources.delete(section);
    const node = sectionNode(section); if (!node) return;
    const placeholder = skeleton(3); placeholder.dataset.pmSourceLoading = '';
    node.dataset.pmSourcePending = ''; node.prepend(placeholder);
    if (section.meta) section.meta.dataset.pmSourcePending = '';
    maskedSources.set(section, placeholder);
  }
  function revealSource(section) {
    maskedSources.get(section)?.remove(); maskedSources.delete(section);
    sectionNode(section)?.removeAttribute('data-pm-source-pending');
    section.meta?.removeAttribute('data-pm-source-pending');
    staleSections.delete(section); activationReads.delete(section);
  }
  function maskStaleView(id) {
    const group = groups.find(group => group.id === id);
    for (const section of staleSections) {
      const node = sectionNode(section);
      if (group?.sections.some(root => root === node || root.contains(node))) maskSource(section);
    }
    if (id === 'issues' && staleSections.has(register)) setHeaderMeta('');
  }
  async function refreshSection(section, activation = false) {
    const read = activation ? activationReads.get(section) : null;
    activationReads.delete(section);
    await (read || (section === dependencies ? section.setActive(true) : section === resources ? section.refresh({event:true}) : section.refresh()));
    alive();
    if (sectionRequired(section)) revealSource(section);
  }
  function refreshStaleVisible() {
    if (!loaded) return Promise.resolve();
    const reads = [];
    for (const section of staleSections) if (sectionVisible(section)) {maskSource(section); reads.push(refreshSection(section, true));}
    return settleRefreshes(reads);
  }
  function invalidateSections() {
    for (const section of sectionModules()) if (!sectionVisible(section)) {staleSections.add(section); maskSource(section);}
  }
  const openedSource = event => {if (event.target instanceof HTMLDetailsElement && event.target.open && loaded && ![...lazyThreadSections.values()].includes(event.target)) void refreshStaleVisible();};
  main.addEventListener('toggle', openedSource, true);
  const noteCache = new Map();
  const selectedSourcesForm = () => threadData?.thread.mode === 'planning' && planningRevisionForm ? planningRevisionForm : planForm;
  const canSelectSources = () => permitted(true) && threadData?.capabilities?.decide
    && (threadData.thread.mode === 'idea' || Boolean(planningRevisionForm));
  const updateSourceControls = () => {
    for (const checkbox of facts?.querySelectorAll('input[type=checkbox]') || [])
      checkbox.disabled = Boolean(selectedSourcesForm()?.locked()) || !canSelectSources();
  };
  // Discarded forms leave the registry immediately; temporarily detached forms
  // rejoin and derive the current gates synchronously on reconnection.
  const updateForms = () => {for (const value of forms) value.update();};
  const offOnline = onOnline(() => {try {alive(); updateForms();} catch { /* a retired view keeps its original intent */ }});
  const timestamp = value => typeof value === 'number' ? value * 1000 : value;
  const time = value => timeStamp(value, {empty:''});
  const sourceText = item => {
    const source = item.source_ref || {};
    if (source.kind === 'message') {
      const message = threadData?.messages?.find(row => row.message_id === source.message_id);
      return message ? timeLabel(t, 'pm_note_source', {author: message.actor, time: time(message.created_at)}) : t('pm_saved_note_source');
    }
    if(source.kind==='maintenance_review_candidate')return t('pmrv_idea_original')+' · '+source.event_id;
    return [source.ref, source.revision, source.path].filter(Boolean).join(' · ');
  };
  function renderThread(data) {
    const thread = data.thread;
    if (mountedThread !== thread.thread_id) {noteCache.clear(); historyExpanded = false;}
    for (const note of data.messages || []) noteCache.set(note.message_id, note);
    if (!historyExpanded) historyCursor = data.next_message_cursor;
    data = {...data, messages: [...noteCache.values()].sort((a, b) => a.sequence - b.sequence)};
    threadData = data;
    if (mountedThread !== thread.thread_id) {
      mountedThread = thread.thread_id; content.replaceChildren();
      threadTitle = input('pd_title', {max: 160, value: thread.title}); mode = h('span', {class: 'ui-pill tone-neutral'});
      notes = h('div', {class: 'pm-notes', 'aria-label': t('pm_notes')});
      earlier = h('button', {type: 'button', class: 'secondary', hidden: true, onclick: () => void loadEarlier()}, t('pm_earlier_notes'));
      coverage = h('p', {class: 'muted'}); permissions = h('p', {class: 'muted'});
      const text = input('pd_message', {multiline: true, max: 12000});
      const target = {workspace_id: workspaceId, thread_id: threadId};
      const preconditions = () => ({expected_version: threadData.thread.version});
      const write = () => permitted(false) && threadData?.capabilities?.write === true;
      const decide = () => permitted(true) && threadData?.capabilities?.decide === true;
      noteForm = form({action: 'pm.message.append', target, fields: {content: text}, params: values => ({content: values.content}), preconditions,
        permitted: write, label: 'pd_send', onSaved: () => load(),
        extra: text});
      text.placeholder = t('pd_message'); text.dataset.discussionComposer = '';
      if (sendHandoff?.threadId === threadId) {
        const handoff = sendHandoff; sendHandoff = null;
        if (!noteForm.locked() && !noteForm.values().content.trim() && typeof handoff.content === 'string' && handoff.content.trim()) {
          noteForm.set('content', handoff.content);
          // Transfer both the draft and exact intent on this first mount, before
          // any secondary reads. A reload may recover it, never auto-submit it.
          firstMessagePending = noteForm.stage();
        }
        localStorage.removeItem(handoffKey);
      }
      renameForm = form({action: 'pm.thread.rename', target, fields: {title: threadTitle}, params: values => ({title: values.title.trim()}),
        preconditions, permitted: write, label: 'pd_save', onBeforeSaved: (_result, values) => {threadTitle.dataset.pmDefault = values.title;},
        onSaved: () => {titleView.finish({focus: true}); return load();}, onStateChange: () => {if (threadData) titleView?.update(threadData.thread.title);}, extra: threadTitle});
      const threadDetails = h('details', {'data-discussion-details': ''}, h('summary', {}, t('pd_details')), renameForm.box.querySelector('.pm-receipt'));
      titleView = discussionTitle({t, field: threadTitle, form: renameForm, value: thread.title});
      boundContext = h('div', {class: 'pd-context'}); panelContext = h('div', {class: 'pd-context-details'});
      discussionHint = h('p', {class: 'muted'});
      const column = h('section', {class: 'pm-discussion'},
        h('header', {class: 'pd-thread-header'}, h('div', {class: 'pd-header-main'}, h('div', {class: 'pd-heading'}, backLink(), titleView.box)), h('div', {class: 'pd-thread-meta'}, mode, boundContext)), discussionHint, h('div', {class: 'pd-transcript'}, earlier, notes, coverage));
      if (caps()?.features?.pm_workspaces?.native_analysis || caps()?.features?.pm_workspaces?.bat_analysis) {
        nativeAnalysis = pmNativeAnalysis({h, t, api, form, input, labelled, caps, namespace, guard: alive,
          capability: () => pmCapabilities, thread: () => threadData, permitted, target, refresh: load});
      }
      if (caps()?.features?.pm_workspaces?.conversation_proposals?.version === 1) {
        proposalReview = planProposals({h,t,api,alive,form,input,labelled,caps,workspaceId,threadId,
          thread:()=>threadData,permitted,refresh:load,errorBox, conversation: true, backLink, onRevision: id => {
            discussionTools.open('plan');
            // Let a new mobile sheet establish its focus boundary first.
            queueMicrotask(() => {try {alive();} catch {return;} revisions?.open(id);});
          },
          onFollowUp: () => text.focus(), onAnswer: value => {if (!value || noteForm.locked()) return; noteForm.set('content', [text.value, value].filter(Boolean).join('\n\n')); void noteForm.submit();}});
        column.querySelector('.pd-transcript').append(proposalReview.box);
      }
      if (caps()?.features?.pm_workspaces?.plan_drafting) {
        revisions = planRevisions({h, t, api, alive, workspaceId, threadId, thread: () => threadData,
          form, input, labelled, permitted, refresh: load, errorBox, deferReads: true});
        column.append(revisions.box);
      }
      if (caps()?.features?.pm_workspaces?.context_packages) {
        const purpose = input('pm_package_purpose', {multiline: true, max: 4000});
        if (caps()?.features?.pm_workspaces?.plan_drafting) worktreeChoice = pmWorktreeChoice({h, t, api, alive, input, labelled,
          capability: () => pmCapabilities, scopes: () => caps()?.scopes || [], editable: () => write() && caps()?.scopes?.includes('manage'), errorBox});
        const prepare = form({action: 'pm.run.prepare', target, fields: {purpose, ...worktreeChoice?.fields},
          params: values => ({purpose: values.purpose, ...worktreeChoice?.params(values)}), preconditions, permitted: write,
          onStateChange: () => worktreeChoice?.update(),
          label: 'pm_prepare_package', onSaved: () => load(),
          extra: h('div', {}, labelled('pm_package_purpose', purpose), worktreeChoice?.box || '', h('p', {class: 'muted'}, t('pm_package_help')))});
        worktreeChoice?.attach(prepare);
        packages = h('div', {'data-pm-packages': ''});
        column.append(h('details', {class: 'pm-package-preparation', 'data-pm-prepare-context': ''}, h('summary', {}, t('pm_package_prepare_title')), prepare.box),
          h('section', {class: 'pm-packages'}, h('h3', {}, t('pm_packages')), packages));
      }
      facts = h('div', {class: 'pm-facts'}); draft = h('section', {class: 'pm-draft'});
      slotSelect = h('select', {'aria-label': t('pm_input_slot'), required: true});
      const value = input('pm_input_value', {multiline: true, max: 4000});
      const statusSelect = h('select', {'aria-label': t('pm_input_status')},
        ...['unresolved', 'agent_assumption', 'repo_evidence', 'conflicting', 'not_applicable'].map(value => h('option', {value}, discussionLabel(t, 'pm_status_', value))));
      statusSelect.dataset.pmDefault = 'unresolved';
      sourceSelect = h('select', {'aria-label': t('pm_input_source')}, h('option', {value: 'statement'}, t('pm_source_statement')),
        h('option', {value: 'repository'}, t('pm_source_repository')), h('option', {value: 'external'}, t('pm_source_external')));
      sourceSelect.dataset.pmDefault = 'statement';
      const reference = input('pm_source_ref', {required: false, max: 1000});
      const requireReference = () => {reference.required = ['repository', 'external'].includes(sourceSelect.value);};
      sourceSelect.addEventListener('change', requireReference);
      factForm = form({action: 'pm.input.record', target, fields: {slot: slotSelect, value, status: statusSelect, source: sourceSelect, reference},
        params: values => {if (values.status === 'repo_evidence' && values.source !== 'repository') throw Error(t('pm_repository_source_required'));
          return {slot: values.slot, value: values.value, status: values.status,
          source_ref: values.source.startsWith('message:') ? {kind: 'message', message_id: values.source.slice(8)}
            : {kind: values.source, ref: values.reference.trim() || (values.source === 'statement' ? values.value.slice(0, 1000) : '')}};},
        preconditions, permitted: write, label: 'pm_record_input', onSaved: () => load(),
        extra: h('div', {class: 'pm-fields'}, labelled('pm_input_slot', slotSelect), labelled('pm_input_value', value),
          labelled('pm_input_status', statusSelect), labelled('pm_input_source', sourceSelect), labelled('pm_source_ref', reference),
          h('p', {class: 'muted'}, t('pm_source_help')))});
      requireReference();
      const selected = h('input', {type: 'hidden'}); selected.value = '[]'; selected.dataset.pmDefault = '[]';
      const rationale = input('pm_plan_rationale', {multiline: true, max: 4000});
      planForm = form({action: 'pm.thread.plan', target, fields: {selected, rationale},
        params: values => ({selected_input_ids: JSON.parse(values.selected || '[]'), rationale: values.rationale}),
        preconditions, permitted: () => decide() && threadData.thread.mode === 'idea', label: 'pm_create_plan', onSaved: () => load(), recoverPermitted: decide,
        onStateChange: () => {
          const section = planForm?.box.closest('[data-pm-plan]');
          if (section) section.hidden = (threadData?.thread.mode !== 'idea' && !planForm.locked()) || !threadData?.capabilities?.decide;
          updateSourceControls();},
        extra: h('div', {}, selected, labelled('pm_plan_rationale', rationale), disclosure({label: t('pd_details'), body: h('p', {class: 'muted'}, t('pm_plan_help'))}))});
      if (caps()?.actions?.some(action => action.action === 'pm.thread.plan.revise')) {
        const selected = h('input', {type: 'hidden'}); selected.value = JSON.stringify(data.draft?.selected_inputs?.map(item => item.input_id) || []);
        selected.dataset.pmDefault = '[]';
        const draftId = h('input', {type: 'hidden'}); draftId.value = data.draft?.draft_id || '';
        const rationale = input('pm_planning_revision_rationale', {multiline: true, max: 4000});
        planningRevisionForm = form({action: 'pm.thread.plan.revise', target, fields: {selected, draftId, rationale},
          params: values => ({selected_input_ids: JSON.parse(values.selected || '[]'), rationale: values.rationale}),
          preconditions: () => ({expected_version: threadData.thread.version, expected_draft_id: planningRevisionForm.values().draftId}),
          permitted: () => decide() && threadData.thread.mode === 'planning', recoverPermitted: decide,
          label: 'pm_planning_revision_save', onSaved: () => load(), onStateChange: updateSourceControls,
          extra: h('div', {}, selected, draftId, labelled('pm_planning_revision_rationale', rationale), disclosure({label: t('pd_details'), body: h('p', {class: 'muted'}, t('pm_planning_revision_help'))}))});
      }
      const information = h('details', {'data-pm-information': '', open: true}, h('summary', {}, t('pm_information')), permissions, facts,
        h('details', {'data-pm-add-input': ''}, h('summary', {}, t('pm_add_input')), factForm.box),
      );
      const contextPane = h('section', {'data-discussion-tab': 'context'}, panelContext, threadDetails);
      for (const node of [...column.querySelectorAll(':scope > .pm-package-preparation, :scope > .pm-packages')]) contextPane.append(node);
      if (nativeAnalysis) contextPane.append(h('details', {}, h('summary', {}, t('pd_analysis')), nativeAnalysis.box));
      if (meetings) contextPane.append(secondary('ui_meetings', meetings.element));
      if (followUp) contextPane.append(secondary('ui_follow_up', followUp.element));
      if (specifications) contextPane.append(secondary('ui_specs', specifications.element));
      if (evidence) drawersHome.append(evidence.box);
      const planPane = h('section', {'data-discussion-tab': 'plan'}, draft,
        h('section', {'data-pm-plan': ''}, h('h2', {}, t('pm_direction')), planForm.box),
        planningRevisionForm ? h('details', {'data-pm-planning-revision': ''}, h('summary', {}, t('pm_planning_revision_title')), planningRevisionForm.box) : '',
        proposalReview?.tools || '', revisions?.box || '', brief ? secondary('pm_brief_title', brief.box) : '');
      discussionTools = discussionPanel({t, namespace, workspaceId, panes: {context: contextPane,
        inputs: h('section', {'data-discussion-tab': 'inputs'}, information), plan: planPane}, onSelect: key => {
          revisions?.setActive(key === 'plan');
          if (key === 'context' && initialReadComplete) for (const [section, details] of lazyThreadSections) if (details.open) void refreshSection(section);
        }});
      column.querySelector('.pd-header-main').append(discussionTools.trigger);
      const composer = discussionComposer({t, form: noteForm, details: threadDetails,
        analysis: proposalReview?.composer(text) || nativeAnalysis?.composer(text)});
      column.append(composer);
      content.append(h('div', {class: 'pm-thread-layout'}, column, discussionTools.home));
      // Open recovery once per thread mount/reload. Later reads preserve the
      // reader's tab and keyed disclosure choices, even while the intent is pending.
      if (!threadState.inputsInitialized && (planForm.locked() || factForm.locked() || planningRevisionForm?.locked())) {
        information.open = true; discussionTools.open('inputs');
      }
      threadState.inputsInitialized = true;
    }
    if (!renameForm.locked() && document.activeElement !== threadTitle && !threadTitle.value) threadTitle.value = thread.title;
    titleView.update(thread.title);
    mode.textContent = discussionLabel(t, 'pm_mode_', thread.mode);
    boundContext.replaceChildren(...discussionContext(t, thread.context, {limit: 3, onMore: () => discussionTools.open('context')}));
    panelContext.replaceChildren(...discussionContext(t, thread.context), ...(thread.context?.length ? [] : [h('p', {class: 'muted'}, t('pd_no_context'))]));
    discussionHint.textContent = '';
    const transcript = notes.closest('.pd-transcript');
    const bottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 32, scroll = transcript.scrollTop;
    notes.replaceChildren(...(data.messages || []).map(message => pmDiscussionMessage({h, t, message, time, timestamp})));
    if (!data.messages?.length) notes.append(emptyState({title:t('pm_no_notes')}));
    transcript.scrollTop = bottom ? transcript.scrollHeight : scroll;
    earlier.hidden = !historyCursor; earlier.disabled = false;
    coverage.textContent = Object.values(data.coverage || {}).some(Boolean) ? t('pm_partial') : '';
    permissions.textContent = !data.capabilities?.write ? t('pm_readonly') : !data.capabilities?.decide ? t('pm_owner_decides') : '';
    if (planningRevisionForm && data.draft && !planningRevisionForm.values().draftId && !planningRevisionForm.locked()) {
      planningRevisionForm.set('draftId', data.draft.draft_id);
      planningRevisionForm.set('selected', JSON.stringify(data.draft.selected_inputs.map(item => item.input_id)));
    }
    const oldSource = sourceSelect.value, oldSlot = slotSelect.value;
    // Preserve saved values even if the current bounded read no longer contains their source.
    for (const message of data.messages || []) if (![...sourceSelect.options].some(option => option.value === 'message:' + message.message_id))
      sourceSelect.append(h('option', {value: 'message:' + message.message_id}, `${message.actor} · ${message.content.slice(0, 50)}`));
    sourceSelect.value = oldSource || 'statement';
    const slots = data.profile?.input_slots || [];
    for (const slot of slots) {
      const id = typeof slot === 'string' ? slot : slot.key || slot.slot || slot.id;
      if (id && ![...slotSelect.options].some(option => option.value === id)) slotSelect.append(h('option', {value: id}, discussionLabel(t, 'pm_slot_', id) === 'pm_slot_' + id ? slot.label || id : discussionLabel(t, 'pm_slot_', id)));
    }
    if (oldSlot) slotSelect.value = oldSlot;
    const signature = JSON.stringify([data.inputs, data.capabilities, thread.mode]);
    if (signature !== factSignature) {
    factSignature = signature;
    const sourceForm = selectedSourcesForm();
    const selection = new Set(JSON.parse(sourceForm.values().selected || '[]'));
    const rows = (data.inputs || []).map(item => {
      const checkbox = h('input', {type: 'checkbox', 'aria-label': t('pm_select_input', {value: item.value}), checked: selection.has(item.input_id),
        disabled: sourceForm.locked() || !canSelectSources(), onchange: event => {
          const selected = new Set(JSON.parse(sourceForm.values().selected || '[]'));
          if (event.target.checked) selected.add(item.input_id); else selected.delete(item.input_id);
          sourceForm.set('selected', JSON.stringify([...selected]));
        }});
      const row = h('article', {class: 'pm-fact', 'data-input-id': item.input_id},
        h('div', {class: 'pm-fact-heading'}, checkbox, h('strong', {}, discussionLabel(t, 'pm_slot_', item.slot) === 'pm_slot_' + item.slot ? item.slot : discussionLabel(t, 'pm_slot_', item.slot)),
          h('span', {class: `chip ${item.status === 'user_confirmed' ? 'ok' : ['unresolved', 'conflicting', 'agent_assumption'].includes(item.status) ? 'warn' : ''}`}, discussionLabel(t, 'pm_status_', item.status))),
        h('p', {class: 'pm-note-text'}, item.value), h('p', {class: 'muted pm-source'}, sourceText(item)),
        h('p', {class: 'muted'}, item.status === 'user_confirmed' ? t('pm_confirmed_by', {actor: item.confirmed_by || item.recorded_by}) : t('pm_unverified')));
      if(item.source_candidate)row.append(reviewCandidateProof({h,t,source:item.source_candidate}));
      if (data.capabilities?.decide && item.status !== 'user_confirmed') {
        const rationale = input('pm_confirm_rationale', {max: 2000});
        const confirm = form({action: 'pm.input.confirm', target: {workspace_id: workspaceId, thread_id: threadId},
          // Each input owns a separate intent. The source input ID is part of params, never inferred from its label.
          namespace: `${namespace}.${item.input_id}`, fields: {rationale}, params: values => ({input_id: item.input_id, rationale: values.rationale}),
          preconditions: () => ({expected_version: threadData.thread.version}), permitted: () => permitted(true) && threadData.capabilities?.decide === true,
          onSaved: () => load(), label: 'pm_confirm', extra: labelled('pm_confirm_rationale', rationale)});
        row.append(h('details', {}, h('summary', {}, t('pm_confirm_review')), confirm.box));
      }
      return row;
    });
    for (const id of selection) if (!(data.inputs || []).some(item => item.input_id === id)) {
      const checkbox = h('input', {type: 'checkbox', checked: true, 'aria-label': t('pm_missing_selection'),
        disabled: sourceForm.locked() || !canSelectSources(), onchange: event => {
          if (event.target.checked) return;
          const selected = new Set(JSON.parse(sourceForm.values().selected || '[]')); selected.delete(id);
          sourceForm.set('selected', JSON.stringify([...selected])); row.remove();
        }});
      const old = data.input_history?.find(item => item.input_id === id);
      const row = h('article', {class: 'pm-fact', 'data-pm-missing-selection': id},
        h('label', {class: 'pm-fact-heading'}, checkbox, t('pm_missing_selection')),
        h('p', {class: 'muted'}, t('pm_missing_selection_help')), old ? h('p', {}, old.value) : '');
      rows.push(row);
    }
    facts.replaceChildren(...rows);
    if (!rows.length) facts.append(h('p', {class: 'muted'}, t('pm_no_inputs')));
    }
    draft.replaceChildren();
    if (data.draft) {
      const selected = data.draft.selected_inputs || [], candidates = data.draft.candidate_inputs || [];
      draft.append(h('h2', {}, t('pm_draft_title')), h('p', {}, data.draft.title), h('p', {class: 'pm-note-text'}, data.draft.rationale),
        h('h3', {}, t('pm_selected')), h('ul', {}, ...selected.map(item => h('li', {}, item.value))),
        h('h3', {}, t('pm_candidates')), h('ul', {}, ...candidates.map(item => h('li', {}, item.value))),
        h('h3', {}, t('pm_open_planning')), h('ul', {}, ...(data.draft.unknowns || []).map(item => h('li', {}, discussionLabel(t, 'pm_slot_', item.slot)))),
        data.draft.candidates_truncated ? h('p', {class: 'muted'}, t('pm_partial')) : '',
        h('p', {class: 'muted'}, t(caps()?.features?.pm_workspaces?.plan_drafting ? 'pm_planning_source_help' : 'pm_draft_help')));
    }
    planForm.box.closest('[data-pm-plan]').hidden = (thread.mode !== 'idea' && !planForm.locked()) || !data.capabilities?.decide;
    if (planningRevisionForm) planningRevisionForm.box.closest('[data-pm-planning-revision]').hidden =
      (thread.mode !== 'planning' && !planningRevisionForm.locked()) || !data.capabilities?.decide;
    updateForms();
  }
  function renderPackages(page, append = false) {
    if (!packages) return;
    const signature = JSON.stringify(page);
    if (!append && signature === packageSignature) return;
    if (!append) {packages.replaceChildren(); packageSignature = signature;}
    for (const run of page.runs || []) {
      const adoptionRef = typeof run.purpose === 'string' && run.purpose.match(/^Adopt plan (pmrev_[0-9a-f]{32})$/);
      const caption = h('span', {'data-pm-run-purpose': ''}, adoptionRef ? t('pcp_plan_context') : run.purpose);
      const detail = h('div'), item = h('details', {'data-pm-run': run.run_id},
        h('summary', {}, h('span', {}, caption, ' · ', time(run.created_at),
          run.adapter === 'codex-native-linux' ? ' · ' + nativeRunState(t, run) : '')), detail);
      if (adoptionRef) void (async () => {
        try {
          const value = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/plans/${adoptionRef[1]}`);
          alive(); if (!item.isConnected) return;
          if (value.plan?.workspace_id !== workspaceId || value.plan.revision_id !== adoptionRef[1]
            || typeof value.plan.document?.title !== 'string' || !Number.isSafeInteger(value.plan.sequence)) throw Error(t('pm_scope_mismatch'));
          caption.textContent = value.plan.document.title + ' · ' + t('pm_revision_number', {number: value.plan.sequence});
        } catch (error) {
          if (!item.isConnected || retired(error)) return;
          if ([403, 404].includes(error.status)) item.remove();
          else detail.replaceChildren(errorBox(error));
        }
      })();
      let fetched = false, pending = false;
      item.addEventListener('toggle', async () => {
        if (!item.open || fetched || pending) return;
        pending = true; detail.replaceChildren(skeleton(2));
        try {
          const data = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(run.run_id)}`);
          alive(); if (!item.isConnected) return;
          const saved = data.run;
          if (saved?.workspace_id !== workspaceId || saved?.thread_id !== threadId || saved?.run_id !== run.run_id) throw Error(t('pm_scope_mismatch'));
          const context = saved.context;
          detail.replaceChildren(h('p', {class: 'muted'}, t('pm_package_version', {version: context.thread_version})),
            h('a', {href: `#/op/${saved.operation_id}`}, t('pm_operation')),
            adoptionRef ? h('details', {}, h('summary', {}, t('pcp_original')), h('p', {}, saved.purpose)) : '',
            ...Object.entries(context.inputs_by_status || {}).filter(([, inputs]) => inputs.length).map(([status, inputs]) =>
              h('section', {}, h('h4', {}, discussionLabel(t, 'pm_status_', status)), ...inputs.map(input => h('div', {}, h('p', {}, input.value),
                h('small', {class: 'muted'}, [input.source_ref?.ref || input.source_ref?.message_id,
                  input.source_ref?.path, input.source_ref?.revision].filter(Boolean).join(' · ')))))),
            h('h4', {}, t('pm_notes')), ...(context.messages || []).map(message => h('p', {},
              h('strong', {}, (message.role === 'assistant' ? t('pm_native_assistant') : message.actor) + ': '), message.content)),
            h('p', {class: 'muted'}, t('pm_package_frozen')));
          if (context.coverage?.omitted_message_ids?.length || context.coverage?.omitted_input_ids?.length
              || context.coverage?.messages_truncated || context.coverage?.inputs_truncated)
            detail.append(h('p', {class: 'muted'}, t('pm_partial')));
          fetched = true;
        } catch (error) {if (!retired(error) && item.isConnected) detail.replaceChildren(errorBox(error));}
        finally {pending = false;}
      });
      packages.append(item);
    }
    if (!packages.children.length) packages.append(emptyState({title:t('pm_packages_empty')}));
    if (page.next_cursor) {
      const more = h('button', {type: 'button', class: 'secondary', onclick: async () => {
        more.disabled = true;
        try {
          const next = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/runs?thread_id=${encodeURIComponent(threadId)}&limit=20&after=${encodeURIComponent(page.next_cursor)}`);
          alive(); if (next.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
          more.remove(); renderPackages(next, true);
        } catch (error) {if (!retired(error)) {status.replaceChildren(errorBox(error)); more.disabled = false;}}
      }}, t('pm_packages_more'));
      packages.append(more);
    }
  }
  async function loadEarlier() {
    if (!historyCursor || refreshing || !loaded) return;
    const ticket = ++sequence; refreshing = true; earlier.disabled = true; refresh.disabled = true;
    try {
      alive(); const data = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/threads/${encodeURIComponent(threadId)}?before_message=${historyCursor}`); alive();
      if (ticket !== sequence) return;
      if (data.thread?.workspace_id !== workspaceId || data.thread?.thread_id !== threadId) throw Error(t('pm_scope_mismatch'));
      historyCursor = data.next_message_cursor; historyExpanded = true; renderThread(data);
    } catch (error) {
      if (!retired(error) && ticket === sequence) {
        loaded = false; lastThreadList = null; workspace = null; publishContext(); detailPanel?.close(); discussionTools?.close(); brief?.withdraw();lifecycle?.withdraw();proposalReview?.withdraw();
        for (const section of sourceSections()) section.hidden = true;
        updateForms(); status.replaceChildren(errorBox(error), h('p', {class: 'muted'}, t('pm_stale')));
      }
    } finally {if (ticket === sequence) {refreshing = false; refresh.disabled = false; earlier.disabled = !loaded;}}
  }
  function filterThreads() {for (const row of workspaceList.querySelectorAll('.pm-thread-link')) row.hidden = !row.textContent.toLocaleLowerCase().includes((uiState.search || '').toLocaleLowerCase());}
  function renderThreadRows(page) {
    threadRows.replaceChildren(...(page?.threads || []).map(thread => h('a', {class: 'pm-thread-link',
          href: `#/pm/${encodeURIComponent(workspaceId)}/discussions/${encodeURIComponent(thread.thread_id)}`,
          'aria-current': thread.thread_id === threadId ? 'page' : null, onclick: () => rememberOrigin(thread.thread_id, {from: 'discussions'})}, h('strong', {title: thread.title}, thread.title),
          h('span', {class: 'pd-thread-meta'}, h('span', {class: 'ui-pill tone-neutral'}, discussionLabel(t, 'pm_mode_', thread.mode)), time(thread.updated_at)),
          ...discussionContext(t, thread.context?.slice(0, 1)))));
    filterThreads();
  }
  async function load({event = false} = {}) {
    const ticket = ++sequence; refreshing = true; refresh.disabled = true;
    if (!event) {lifecycle?.withdraw();engineering?.withdraw();materialization?.withdraw();handoff?.withdraw();workBatches?.withdraw();issues?.withdraw();
      ahp?.withdraw();releaseBaselines?.withdraw();maintenancePolicy?.withdraw();maintenanceRepairs?.withdraw();maintenanceReview?.withdraw();qualityReviews?.withdraw();qualitySelections.clear();}
    try {
      alive(); const list = event ? {workspaces:lastList} : await api('GET', '/pm/workspaces'); alive();
      let detail = null, threadList = null, data = null, runs = null, plans = null, accepted = null, workspaceCaps = null, draftRefs = null;
      if (workspaceId) {
        [detail, threadList, data, runs, plans, accepted, workspaceCaps, draftRefs] = await Promise.all([api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}`),
          api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/threads`),
          threadId && threadId !== 'new' ? api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/threads/${encodeURIComponent(threadId)}`) : Promise.resolve(null),
          threadId && threadId !== 'new' && caps()?.features?.pm_workspaces?.context_packages
            ? api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/runs?thread_id=${encodeURIComponent(threadId)}&limit=20`) : Promise.resolve(null),
          threadId && threadId !== 'new' && caps()?.features?.pm_workspaces?.plan_drafting
            ? api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/plans?limit=20`) : Promise.resolve(null),
          threadId && threadId !== 'new' && caps()?.features?.pm_workspaces?.plan_drafting
            ? api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/plans/accepted`) : Promise.resolve(null),
          threadId && threadId !== 'new' && (caps()?.features?.pm_workspaces?.plan_drafting || caps()?.features?.pm_workspaces?.native_analysis || caps()?.features?.pm_workspaces?.bat_analysis)
            ? api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/capabilities`) : Promise.resolve(null),
          threadId === 'new' ? draftSourceRefs() : Promise.resolve(null)]);
        alive();
        for (let page = 1; page < threadPages && threadList.next_cursor; page++) {
          const next = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/threads?after=${encodeURIComponent(threadList.next_cursor)}`); alive();
          threadList = {...next, threads: [...threadList.threads, ...next.threads]};
        }
      }
      const contextReads = new Map();
      if (data?.thread?.context) data = {...data, thread:{...data.thread, context:await draftSourceRefs(data.thread.context, contextReads)}};
      if (threadList?.threads) threadList = {...threadList, threads:await Promise.all(threadList.threads.map(async thread => ({...thread, context:await draftSourceRefs(thread.context || [], contextReads)})))};
      alive();
      if (ticket !== sequence) return;
      if (detail && detail.workspace?.workspace_id !== workspaceId || data && (data.thread?.workspace_id !== workspaceId || data.thread?.thread_id !== threadId)) throw Error(t('pm_scope_mismatch'));
      workspace = detail?.workspace || null; pmCapabilities = workspaceCaps; loaded = true; status.replaceChildren();
      draftSourceUnavailable = Boolean(draftRefs?.some(ref => ref.available === false));
      title.textContent = workspace ? t('ui_' + (threadId ? 'discussions' : workbench?.selected() || 'discussions')) : t('pm_title');
      lastList = list.workspaces || []; lastThreadList = threadList;
      if (!threadSearch) {
        threadSearch = input('pd_search', {required: false}); threadSearch.type = 'search'; threadSearch.placeholder = t('pd_search'); threadSearch.value = uiState.search || '';
        threadSearch.addEventListener('input', () => {uiState.search = threadSearch.value; filterThreads();});
      }
      const scroll = workspaceList.scrollTop;
      if (!workspaceList.contains(threadSearch)) workspaceList.replaceChildren(h('div', {class: 'pd-list-header'}, searchField(threadSearch), iconButton({icon: 'plus', label: t('pd_new'), 'data-discussion-new': '', onClick: () => {location.hash = discussHref({workspaceId});}})), threadRows);
      renderThreadRows(threadList);
      if (!workspaceId) workspaceList.replaceChildren(...lastList.map(row => h('a', {href: `#/pm/${row.workspace_id}`}, row.name)), h('button', {type: 'button', class: 'primary', onclick: showCreate}, t('ui_new_workspace')));
      filterThreads(); workspaceList.scrollTop = scroll;
      workspaceList.querySelector('[data-discussion-more]')?.remove();
      if (threadList?.next_cursor) workspaceList.append(h('button', {type: 'button', class: 'secondary', 'data-discussion-more': '', onclick: () => {threadPages++; void load();}}, t('pm_more_threads')));
      if (data) renderThread(data);
      else if (!content.querySelector('.pm-thread-layout')) {
        if (!workspace) content.append(emptyState({icon:'message-square',title:t('pm_start_title'),text:t('pm_start_help')}));
        else {
          const name = input('pd_title', {max: 160, value: threadId === 'new' ? launch.title || t('pd_new') : ''});
          const text = input('pd_message', {multiline: true, max: 12000, required: threadId === 'new'}); text.placeholder = t('pd_message'); text.dataset.discussionComposer = '';
          let titleDraft = null;
          const create = form({action: 'pm.thread.create', pruneEmpty: true, target: {workspace_id: workspaceId}, fields: {title: name, content: text},
            namespace: threadId === 'new' ? namespace + '.discussion.' + JSON.stringify(launch) : namespace,
            params: values => ({title: values.title.trim(), ...(launch.refs.length ? {context: launch.refs} : {})}), preconditions: () => ({expected_version: workspace.version}),
            permitted: () => permitted(false) && !draftSourceUnavailable, recoverPermitted: () => permitted(false), label: threadId === 'new' ? 'pd_send' : 'pm_create_thread',
            onStateChange: () => titleDraft?.updateState(),
            onBeforeSaved: (result, values) => {
              if (values.content.trim()) localStorage.setItem(handoffKey, JSON.stringify({threadId: result.thread_id, content: values.content}));
              rememberOrigin(result.thread_id, {from: launch.from || 'discussions', report: launch.report});
            },
            onSaved: result => {alive(); location.replace(`#/pm/${encodeURIComponent(workspaceId)}/discussions/${encodeURIComponent(result.thread_id)}`);},
            extra: threadId === 'new' ? text : h('div', {}, labelled('pd_title', name), text)});
          titleDraft = threadId === 'new' ? discussionTitle({t, field: name, form: create, value: name.value, local: true}) : null;
          const contextDraft = h('section', {'data-discussion-tab': 'context'}, ...discussionContext(t, draftRefs || launch.refs));
          const draftPanel = discussionPanel({t, namespace, workspaceId, panes: {context: contextDraft}});
          if (threadId === 'new') discussionTools = draftPanel;
          boundContext = h('div', {class: 'pd-context'});
          panelContext = contextDraft;
          content.append(h('div', {class: 'pm-thread-layout pd-new-layout'}, h('section', {class: 'pm-discussion'},
            h('header', {class: 'pd-thread-header'}, h('div', {class: 'pd-header-main'}, h('div', {class: 'pd-heading'}, backLink(), titleDraft?.box || h('h2', {}, t('pd_new')))),
              h('div', {class: 'pd-thread-meta'}, h('span', {class: 'ui-pill tone-neutral'}, t('ui_status_draft')),
                boundContext)),
            h('div', {class: 'pd-transcript'}, emptyState({title:t('pd_empty')})), discussionComposer({t, form: create, draft: true})),
            launch.refs.length > 3 ? draftPanel.home : ''));


        }
      }
      if (threadId === 'new' && draftRefs && boundContext) {
        boundContext.replaceChildren(...discussionContext(t, draftRefs, {limit: 3, onMore: () => discussionTools.open('context')}));
        panelContext.replaceChildren(...discussionContext(t, draftRefs));
      }
      if (!workspaceList.isConnected) content.prepend(workspaceList);
      if (runs) {
        if (runs.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
        renderPackages(runs);
      }
      if (plans && revisions) revisions.update(plans, accepted, workspaceCaps);
      if (proposalReview) {
        proposalReview.update({head: plans?.head, capabilities: workspaceCaps});
        await proposalReview.refresh();
        alive(); if (ticket !== sequence) return;
      }
      worktreeChoice?.update();
      for (const section of [lifecycle, evidence, brief, ahp, releaseBaselines, maintenancePolicy, maintenanceRepairs, maintenanceReview, qualityReviews, roadmap, schedule, kanban, sprints, meetings, governance, register, specifications, followUp, engineering,materialization,handoff,workBatches,issues,traceability]) {
        const lazy = lazyThreadSections.get(section);
        if (section && (!event || sectionRequired(section)) && (!threadId || !lazy || lazy.open && !lazy.closest('[data-discussion-tab]')?.hidden)) {
          // The initial view activation already started its projection read.
          // Reuse that read rather than replacing its just-rendered status.
          const activation = !initialReadComplete && activationReads.get(section);
          activationReads.delete(section);
          await (activation || section.refresh());
          if (sectionRequired(section)) revealSource(section);
        }
        alive(); if (ticket !== sequence) return;
      }
      nativeAnalysis?.update(runs?.runs || []);
      for (const section of sourceSections()) section.hidden = false;
      workbench?.apply();
      const stage = lifecycle?.phase?.(); phasePill.hidden = !stage;
      phasePill.textContent = stage && ['idea','planning','development','maintain','paused','closed'].includes(stage) ? t('ui_phase_' + stage) : '';
      publishContext(); brief?.setActive?.(workbench?.selected() === 'overview');
      updateWorkView(workbench?.selected());
      resources?.setActive(workbench?.selected() === 'agents');
      schedule?.setActive(workbench?.selected() === 'schedule' && !detailPanel);
      if (dependencies) await dependencies.setActive(workbench?.selected() === 'dependencies');
      register?.setActive(workbench?.selected() === 'issues');
      updateForms();
      if (initialReadComplete) {proposalReview?.recheck(); nativeAnalysis?.recheck();}
    } catch (error) {
      if (!retired(error) && ticket === sequence) {
        loaded = false; workspace = null; threadData = null;
        if ([403, 404].includes(error.status)) forgetWorkspace(namespace, workspaceId);
        brief?.withdraw();traceability?.withdraw();kanban?.withdraw();resources?.withdraw();schedule?.withdraw();dependencies?.withdraw();qualityReviews?.withdraw();proposalReview?.withdraw();qualitySelections.clear();
        lifecycle?.withdraw();engineering?.withdraw();materialization?.withdraw();handoff?.withdraw();workBatches?.withdraw();issues?.withdraw();register?.withdraw();ahp?.withdraw();releaseBaselines?.withdraw();maintenancePolicy?.withdraw();maintenanceRepairs?.withdraw();maintenanceReview?.withdraw();
        detailPanel?.close(); discussionTools?.close(); phasePill.hidden = true;
        title.textContent = t('pm_title'); workspaceList.replaceChildren(); lastThreadList = null; publishContext();
        for (const section of sourceSections()) section.hidden = true;
        updateForms(); status.replaceChildren(errorBox(error), h('p', {class: 'muted'}, t('pm_stale')));
      }
    } finally {if (ticket === sequence) {refreshing = false; refresh.disabled = false;}}
  }
  await load();
  // The router admits POSTs only after this complete initial snapshot returns.
  // Do not expose forms that would freeze an unsent intent while that gate is closed.
  initialReadComplete = true; updateForms();
  workspaceList.scrollTop = uiState.listScroll || 0;
  const initialTranscript = content.querySelector('.pd-transcript');
  if (initialTranscript && threadState.scroll) initialTranscript.scrollTop = threadState.scroll;
  if (threadId === 'new') setTimeout(() => {if (!disposed) content.querySelector('[data-discussion-composer]')?.focus();}, 0);
  proposalReview?.recheck(); nativeAnalysis?.recheck();
  if (firstMessagePending) setTimeout(() => {
    if (!disposed) {firstMessagePending = false; void noteForm.submit();}
  }, 0);
  const linkedRevision = new URLSearchParams(location.hash.split('?')[1] || '').get('revision');
  if (/^pmrev_[0-9a-f]{32}$/.test(linkedRevision || '') && revisions) {discussionTools.open('plan'); revisions.open(linkedRevision);}
  const rememberedTool = viewTools[workbench?.selected()]?.find(([key]) => key === toolPreference.open);
  if (rememberedTool?.[1]) openPanel(t(rememberedTool[0]), rememberedTool[1]);
  if (openCreate || !workspaceId && !lastList.length) showCreate();
  // Coalesce only reads that have not started. Every event's ACK waits for a
  // post-event read, while a held older read cannot block a newer withdrawal.
  const pendingReads = new Map();
  function eventRead(key, read) {
    if (!pendingReads.has(key)) pendingReads.set(key, Promise.resolve().then(() => {
      pendingReads.delete(key); alive(); return read();
    }));
    return pendingReads.get(key);
  }
  const authorityHidden = new Map();
  let authorityTool = null;
  async function refreshAuthority() {
    const ticket = ++sequence;
    try {
      const detail = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}`); alive();
      if (ticket !== sequence) return;
      if (detail.workspace?.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
      workspace = detail.workspace; loaded = true;
      if (authorityHidden.size) {
        for (const [node,hidden] of authorityHidden) node.hidden = hidden;
        authorityHidden.clear(); workbench?.apply();
        const tool = authorityTool; authorityTool = null;
        if (tool?.node && tool.view === workbench?.selected()) openPanel(tool.label,tool.node,false);
        await refreshStaleVisible(); alive(); if (ticket !== sequence) return;
      }
      status.replaceChildren(); publishContext(); updateForms();
    } catch (error) {
      alive(); if (ticket !== sequence) return;
      loaded = false; workspace = null; threadData = null;
      if ([403,404].includes(error.status)) forgetWorkspace(namespace,workspaceId);
      if (!authorityHidden.size) {
        for (const node of sourceSections()) authorityHidden.set(node,node.hidden);
        authorityTool = detailPanel ? {node:detailPanel.body.firstElementChild,label:detailPanel.querySelector('h2').textContent,view:workbench?.selected()} : null;
      }
      for (const section of sectionModules()) {section.withdraw?.(); staleSections.add(section); maskSource(section);}
      for (const node of sourceSections()) node.hidden = true;
      workspaceList.replaceChildren(); lastThreadList = null; phasePill.hidden = true;
      detailPanel?.close(); discussionTools?.close(); publishContext(); updateForms();
      status.replaceChildren(errorBox(error));
      throw error;
    }
  }
  let contextSequence = 0;
  async function refreshDiscussionSources() {
    const ticket = ++contextSequence, generation = sequence;
    try {
    if (threadId === 'new') {
      const refs = await draftSourceRefs(); alive();
      if (ticket !== contextSequence || generation !== sequence) return;
      draftSourceUnavailable = refs.some(ref => ref.available === false);
      boundContext?.replaceChildren(...discussionContext(t,refs,{limit:3,onMore:()=>discussionTools.open('context')}));
      panelContext?.replaceChildren(...discussionContext(t,refs)); updateForms();
    } else if (threadId) {
      let data = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/threads/${encodeURIComponent(threadId)}`); alive();
      if (data.thread?.workspace_id !== workspaceId || data.thread?.thread_id !== threadId) throw Error(t('pm_scope_mismatch'));
      data = {...data, thread:{...data.thread,context:await draftSourceRefs(data.thread.context || [])}}; alive();
      if (ticket !== contextSequence || generation !== sequence) return;
      renderThread(data); updateForms();
    } else {
      let page = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/threads`); alive();
      for (let index = 1; index < threadPages && page.next_cursor; index++) {
        const next = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/threads?after=${encodeURIComponent(page.next_cursor)}`); alive();
        page = {...next,threads:[...page.threads,...next.threads]};
      }
      const reads = new Map();
      page = {...page,threads:await Promise.all((page.threads || []).map(async row => ({...row,context:await draftSourceRefs(row.context || [],reads)})))}; alive();
      if (ticket !== contextSequence || generation !== sequence) return;
      lastThreadList = page; renderThreadRows(page); publishContext();
    }
    content.hidden = false;
    } catch (error) {
      alive(); if (ticket !== contextSequence || generation !== sequence) return;
      if ([403,404].includes(error.status)) {
        loaded = false; threadData = null; lastThreadList = null; content.hidden = true;
        workspaceList.replaceChildren(); boundContext?.replaceChildren(); panelContext?.replaceChildren();
        discussionTools?.close(); publishContext(); updateForms();
      }
      status.replaceChildren(errorBox(error)); throw error;
    }
  }
  const pmEvent = kind => kind.startsWith('pm_');
  const liveEvent = kind => ['work_item','project','task','command','session','operation','deployment','integration'].includes(kind);
  const off = onEvents(event => {
    const kind = event.resource_type;
    if (!pmEvent(kind) && !liveEvent(kind)) return;
    invalidateSections();
    // A thread refresh retains its own context, proposal and receipt readers, while
    // the rest of the workspace stays stale until its view or drawer is opened.
    if (threadId && pmEvent(kind)) return eventRead(load, () => load({event:true}));
    const reads = [];
    if (!pmEvent(kind) && (threadId || workbench?.selected() === 'discussions')) reads.push(eventRead(refreshDiscussionSources,refreshDiscussionSources));
    if (pmEvent(kind)) reads.push(eventRead(refreshAuthority, refreshAuthority));
    for (const section of sectionModules()) if (sectionRequired(section)) {
      if (section === issues && kind !== 'pm_workspace' && !kind.startsWith('pm_issue')) continue;
      if (section === dependencies && !pmEvent(kind) && !['project','integration'].includes(kind)) continue;
      reads.push(eventRead(section, () => refreshSection(section)));
    }
    // Discussions has no primary projection object. Its own thread list is the read.
    if (!threadId && workbench?.selected() === 'discussions' && pmEvent(kind)) reads.push(eventRead(refreshDiscussionSources, refreshDiscussionSources));
    return settleRefreshes(reads);
  });
  const dispose = () => {
    uiState.listScroll = workspaceList.scrollTop;
    threadState.scroll = content.querySelector('.pd-transcript')?.scrollTop || 0;
    disposed = true; main.removeEventListener('toggle', openedSource, true); for (const placeholder of maskedSources.values()) placeholder.remove(); maskedSources.clear();
    discussionTools?.dispose(); sequence++; off(); offOnline(); createPanel?.close(); detailPanel?.close(); reportMenu?.dispose(); overflow.dispose();
    brief?.dispose?.();traceability?.dispose();kanban?.dispose();resources?.dispose();schedule?.dispose();scheduleTools?.dispose();dependencies?.dispose();register?.dispose();
    proposalReview?.dispose();nativeAnalysis?.dispose();ahp?.dispose();
    for (const control of viewToolMenus.values()) control.dispose();
    for (const value of forms) value.dispose();
  };
  dispose.navigate = (nextWorkspace, nextThread, nextView) => {
    if (disposed || nextWorkspace !== workspaceId || (nextThread || null) !== (threadId || null) || !workbench) return false;
    workbench.select(nextView);
    updateWorkView(workbench.selected());
    if (nextView !== workbench.selected()) history.replaceState(null, '', `#/pm/${encodeURIComponent(workspaceId)}/${workbench.selected()}`);
    return true;
  };
  return dispose;
}
