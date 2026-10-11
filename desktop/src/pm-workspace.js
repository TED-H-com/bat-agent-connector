// PM is a central, durable discussion record. This client does not run an agent or authorize execution.
import {workspaceGovernance} from './pm-governance.js';
import {workspaceLifecycle} from './pm-lifecycle.js';
import {workspaceIssueMappings} from './pm-issue-mappings.js';
import {specSources} from './pm-spec-sources.js';
import {workspaceFollowUp} from './pm-follow-up.js';
import {workspaceWorkPackages} from './pm-work-packages.js';
import {workspaceSprints} from './pm-sprints.js';
import {workspaceKanban} from './pm-kanban.js';
import {workspaceMeetings} from './pm-meetings.js';
import {workspaceRoadmap} from './pm-roadmap.js';
import {projectBrief} from './pm-brief.js';
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
// Only disclosure preferences survive a same-principal remount. Facts are always
// removed and reread after an access reset; drafts retain their existing namespace.
const threadDisclosures = new Map();
const threadDisclosureSelectors = ['[data-pm-add-input]', '[data-pm-planning-revision]', '[data-pm-note-only]', '[data-pm-prepare-context]'];
export const pmAvailable = caps => caps?.features?.pm_workspaces?.version === 1 && caps?.scopes?.includes('observe');

// Save the exact request before admission. A later authorization/validation error can never
// release an earlier ambiguous request; only its matching terminal receipt permits replacement.
function durableForm({h, t, api, caps, guard, namespace, action, target, fields, params, preconditions, permitted, onSaved, errorBox, label, extra, onStateChange = () => {}, recoverPermitted = permitted}) {
  const storage = `batc.pm.${namespace}.${action}.${JSON.stringify(target)}`;
  let saved = {version: 1, values: {}}, damaged = false, busy = false, operation = null;
  const receiptDelays = [250, 500, 1000, 1500, 2000, 3000];
  let receiptTimer = null, receiptAttempt = 0, receiptPolling = true, disposed = false;
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
  const persist = () => {guard(); localStorage.setItem(storage, JSON.stringify(saved));};
  const capture = () => Object.fromEntries(Object.entries(fields).map(([name, input]) => [name, input.value]));
  const changed = () => {
    if (busy || saved.intent || damaged) return;
    saved.values = capture();
    try {persist();} catch (error) {status.replaceChildren(errorBox(error));}
  };
  for (const [name, input] of Object.entries(fields)) {
    const value = saved.values[name] ?? input.value;
    if (input.tagName === 'SELECT' && value && ![...input.options].some(option => option.value === value)) input.append(h('option', {value}, value));
    input.value = value;
    input.addEventListener('input', changed); input.addEventListener('change', changed);
  }
  const allowed = () => (saved.intent ? recoverPermitted() : permitted()) && caps()?.scopes?.includes('manage')
    && caps()?.actions?.some(row => row.action === action && row.allowed === true);
  const receiveReceipt = () => {
    if (disposed || damaged || !saved.intent) return;
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
  const subscribers = receiptSubscribers.get(storage) || new Set();
  subscribers.add(receiveReceipt); receiptSubscribers.set(storage, subscribers);
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
    if (operation && !['accepted', 'queued', 'pending', 'running'].includes(operation.status)) return;
    try {guard();} catch {receiptPolling = false; return;}
    receiptTimer = setTimeout(() => {
      receiptTimer = null;
      void run(true);
    }, receiptDelays[receiptAttempt++]);
  }
  function update() {
    if (disposed) return;
    const locked = busy || damaged || Boolean(saved.intent) || !allowed();
    for (const input of Object.values(fields)) input.disabled = locked;
    button.disabled = busy || damaged || !allowed() || terminal() || Boolean(saved.intent?.initialAdmissionRefused);
    button.textContent = t(saved.intent ? 'pm_check_original' : label);
    reset.hidden = !terminal() && !saved.intent?.initialAdmissionRefused;
    reset.disabled = busy || !allowed();
    onStateChange();
    pollReceipt();
  }
  async function run(automatic = false) {
    if (disposed || busy || damaged || !allowed() || automatic && (!saved.intent?.operation_id || !form.isConnected)) return;
    stopTimer();
    if (!automatic) {receiptAttempt = 0; receiptPolling = true;}
    let firstAttempt = false;
    try {
      guard(); busy = true; status.replaceChildren();
      if (!saved.intent) {
        saved.values = capture();
        const request = {action, target, params: params(saved.values), preconditions: preconditions()};
        saved.intent = {key: crypto.randomUUID(), request}; persist(); firstAttempt = true;
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
      if (disposed || !allowed()) return;
      const op = data.operation;
      saved.intent.operation_id = op.operation_id; persist(); operation = op;
      receipt.replaceChildren(h('a', {href: `#/op/${op.operation_id}`}, t('pm_operation')), ' · ', String(op.status));
      if (op.status === 'succeeded') {
        saved = {version: 1, values: {}}; persist();
        for (const input of Object.values(fields)) input.value = input.dataset.pmDefault || '';
        await onSaved(op.result); guard(); status.replaceChildren(h('span', {class: 'muted'}, t('pm_saved')));
      } else status.replaceChildren(h('p', {class: 'muted'}, t(terminal() ? 'pm_terminal' : 'pm_pending')));
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
        status.replaceChildren(errorBox(error));
        if (error.code === 'VERSION_CONFLICT') status.append(h('p', {class: 'muted'}, t('pm_stale')));
      }
    } finally {busy = false; update();}
  }
  if (damaged) status.append(h('p', {class: 'error'}, t('pm_damaged')));
  update();
  return {box: form, fields, update, changed, locked: () => busy || damaged || Boolean(saved.intent),
    dispose: () => {
      disposed = true; receiptPolling = false; stopTimer(); subscribers.delete(receiveReceipt);
      if (!subscribers.size && receiptSubscribers.get(storage) === subscribers) receiptSubscribers.delete(storage);
    },
    set: (name, value) => {if (!busy && !saved.intent && !damaged) {fields[name].value = value; changed();}},
    values: () => capture()};
}

export async function mountPmWorkspace({main, h, t, api, caps, guard, namespace, workspaceId, threadId, onEvents, errorBox}) {
  if (!pmAvailable(caps())) {main.append(h('p', {}, t('pm_unavailable'))); return;}
  let disposed = false, workspace = null, threadData = null, loaded = false, refreshing = false, sequence = 0, threadPages = 1, lifecycle = null;
  const disclosureKey = JSON.stringify([namespace, workspaceId, threadId]);
  const forms = [], alive = () => {guard(); if (disposed) throw Object.assign(Error(), {code: 'PM_DISPOSED'});};
  const permitted = decide => loaded && Boolean(workspace) && (caps()?.scopes?.includes('admin')
    || (decide ? workspace.membership?.role === 'owner' : ['owner', 'contributor'].includes(workspace.membership?.role)));
  const input = (label, {multiline = false, max = 2000, required = true, value = ''} = {}) => {
    const field = h(multiline ? 'textarea' : 'input', {'aria-label': t(label), maxlength: max, required}); field.value = value;
    return field;
  };
  const labelled = (label, field) => h('label', {}, t(label), field);
  const form = options => {
    const value = durableForm({h, t, api, caps, guard: alive, namespace, errorBox, ...options,
      permitted:()=>options.permitted()&&(lifecycle?.newActionAllowed(options.action,()=>options.params(Object.fromEntries(Object.entries(options.fields).map(([key,input])=>[key,input.value]))))??true),
      recoverPermitted:options.recoverPermitted||options.permitted}); forms.push(value); return value;
  };
  const status = h('div', {role: 'status', class: 'pm-status'}), workspaceList = h('div', {class: 'pm-workspace-list'});
  const name = input('pm_workspace_name', {max: 120}), repositories = input('pm_repositories', {required: false});
  const workspaceForm = form({action: 'pm.workspace.create', target: {}, fields: {name, repositories},
    params: values => ({name: values.name.trim(), repositories: values.repositories.split(/[\n,]/).map(value => value.trim()).filter(Boolean)}),
    preconditions: () => ({}), permitted: () => true, label: 'pm_create_workspace',
    onSaved: result => {alive(); location.hash = `#/pm/${encodeURIComponent(result.workspace.workspace_id)}`;},
    extra: h('div', {class: 'pm-fields'}, labelled('pm_workspace_name', name),
      h('details', {}, h('summary', {}, t('pm_repositories_optional')), labelled('pm_repositories', repositories), h('p', {class: 'muted'}, t('pm_repository_help'))))});
  const navigationMedia = matchMedia('(min-width: 800px)');
  const nav = h('details', {class: 'pm-navigation', open: navigationMedia.matches},
    h('summary', {}, t('pm_workspaces')), h('div', {class: 'pm-navigation-body'}, workspaceList,
      h('details', {class: 'pm-create-workspace'}, h('summary', {}, t('pm_new_workspace')), workspaceForm.box),
      h('a', {class: 'pm-projects-link', href: '#/projects'}, t('pm_back_projects'))));
  const resizeNavigation = () => {nav.open = navigationMedia.matches;};
  navigationMedia.addEventListener('change', resizeNavigation);
  const title = h('h1', {}, t('pm_title')), subtitle = h('p', {class: 'muted'}, t('pm_intro'));
  const refresh = h('button', {type: 'button', class: 'secondary', onclick: () => void load()}, t('pm_refresh'));
  const heading = h('header', {class: 'pm-heading'}, h('div', {}, title, subtitle), refresh);
  const content = h('div', {class: 'pm-content'}), body = h('section', {class: 'pm-main'}, heading, status, content);
  main.append(h('div', {class: 'pm-shell'}, h('aside', {}, nav), body));
  let mountedThread = null, notes = null, facts = null, draft = null, noteForm = null, factForm = null, planForm = null, planningRevisionForm = null,
    sourceSelect = null, slotSelect = null, permissions = null, coverage = null, threadTitle = null, mode = null, factSignature = '', earlier = null, historyCursor = null, historyExpanded = false,
    packages = null, packageSignature = '', revisions = null, pmCapabilities = null, worktreeChoice = null, nativeAnalysis = null, discussionHint = null;
  lifecycle = workspaceId && caps()?.features?.pm_workspaces?.lifecycle?.version === 1
    ? workspaceLifecycle({h,t,api,alive,workspaceId,caps,permitted:()=>loaded&&workspace?.membership?.role==='owner',form,input,labelled,errorBox,onChange:()=>updateForms()}) : null;
  if(lifecycle&&!threadId)body.insertBefore(lifecycle.element,content);
  const evidence = workspaceId && caps()?.features?.pm_workspaces?.work_evidence
    ? workspaceEvidence({h, t, api, alive, namespace, workspaceId, workspace: () => workspace, permitted, form, input, labelled, errorBox}) : null;
  if (evidence) body.append(evidence.box);
  const brief = workspaceId && caps()?.features?.pm_workspaces?.brief_snapshots
    ? projectBrief({h, t, api, alive, workspaceId, workspace: () => workspace, permitted, form, input, labelled, errorBox,caps,canRead:()=>loaded&&Boolean(workspace)}) : null;
  if (brief && !threadId) body.insertBefore(brief.box, lifecycle?.element||content);
  const releaseBaselines = workspaceId && !threadId && caps()?.features?.pm_workspaces?.release_baselines
    ? workspaceReleaseBaselines({h,t,api,alive,workspaceId,workspace:()=>workspace,caps,permitted,form,input,labelled,errorBox,namespace}) : null;
  if(releaseBaselines)body.insertBefore(releaseBaselines.element,content);
  const roadmap = workspaceId && !threadId && caps()?.features?.pm_workspaces?.roadmap
    ? workspaceRoadmap({h,t,api,alive,workspaceId,permitted,form,input,labelled,errorBox}) : null;
  if(roadmap)body.insertBefore(roadmap.box,content);
  const kanban = workspaceId && !threadId && caps()?.features?.pm_workspaces?.kanban
    ? workspaceKanban({h,t,api,alive,workspaceId,errorBox}) : null;
  if(kanban)body.insertBefore(kanban.element,content);
  const sprints = workspaceId && !threadId && caps()?.features?.pm_workspaces?.sprints
    ? workspaceSprints({h,t,api,alive,workspaceId,workspace:()=>workspace,permitted,form,input,labelled,errorBox,caps}) : null;
  if(sprints)body.insertBefore(sprints.box,content);
  const meetings = workspaceId && !threadId && caps()?.features?.pm_workspaces?.meetings
    ? workspaceMeetings({h,t,api,alive,workspaceId,workspace:()=>workspace,permitted,form,input,labelled,errorBox,caps}) : null;
  if(meetings)body.insertBefore(meetings.element,content);
  const governance = workspaceId && !threadId && caps()?.features?.pm_workspaces?.governance
    ? workspaceGovernance({h,t,api,alive,workspaceId,workspace:()=>workspace,permitted,form,input,labelled,errorBox,caps}) : null;
  if(governance)body.insertBefore(governance.element,content);
  const specifications = workspaceId && caps()?.features?.pm_workspaces?.spec_sources
    ? specSources({h,t,api,alive,workspaceId,errorBox}) : null;
  if (specifications) {
    if (threadId) body.append(specifications.element);
    else body.insertBefore(specifications.element, content);
  }
  const followUp = workspaceId && !threadId && caps()?.features?.pm_workspaces?.follow_up_prepare
    ? workspaceFollowUp({h,t,api,alive,workspaceId,permitted,form,input,labelled,errorBox,caps,namespace}) : null;
  if (followUp) body.insertBefore(followUp.element, roadmap?.box || kanban?.element || sprints?.box || meetings?.element || governance?.element || specifications?.element || content);
  const issues = workspaceId && !threadId && caps()?.features?.pm_workspaces?.issue_mapping
    ? workspaceIssueMappings({h,t,api,alive,workspaceId,permitted,form,input,labelled,errorBox}) : null;
  const engineering = workspaceId && !threadId && caps()?.features?.pm_workspaces?.work_package_prepare
    ? workspaceWorkPackages({h,t,api,alive,workspaceId,errorBox,onSelection:row=>issues?.setPackage(row)}) : null;
  if(engineering)body.insertBefore(engineering.element,specifications?.element||content);
  if(issues)body.insertBefore(issues.element,specifications?.element||content);
  const sourceSections = () => [content, lifecycle?.element, evidence?.box, brief?.box, releaseBaselines?.element, roadmap?.box, kanban?.element, sprints?.box, meetings?.element, governance?.element, specifications?.element, followUp?.element, engineering?.element,issues?.element].filter(Boolean);
  const noteCache = new Map();
  const selectedSourcesForm = () => threadData?.thread.mode === 'planning' && planningRevisionForm ? planningRevisionForm : planForm;
  const canSelectSources = () => permitted(true) && threadData?.capabilities?.decide
    && (threadData.thread.mode === 'idea' || Boolean(planningRevisionForm));
  const updateSourceControls = () => {
    for (const checkbox of facts?.querySelectorAll('input[type=checkbox]') || [])
      checkbox.disabled = Boolean(selectedSourcesForm()?.locked()) || !canSelectSources();
  };
  const updateForms = () => {for (let i = forms.length - 1; i >= 0; i--) {if (!forms[i].box.isConnected) forms.splice(i, 1); else forms[i].update();}};
  const timestamp = value => typeof value === 'number' ? value * 1000 : value;
  const time = value => {const date = new Date(timestamp(value)); return Number.isNaN(date.valueOf()) ? String(value || '') : date.toLocaleString();};
  const sourceText = item => {
    const source = item.source_ref || {};
    if (source.kind === 'message') {
      const message = threadData?.messages?.find(row => row.message_id === source.message_id);
      return message ? t('pm_note_source', {author: message.actor, time: time(message.created_at)}) : t('pm_saved_note_source');
    }
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
      threadTitle = h('h2'); mode = h('span', {class: 'chip'});
      notes = h('div', {class: 'pm-notes', 'aria-label': t('pm_notes')});
      earlier = h('button', {type: 'button', class: 'secondary', hidden: true, onclick: () => void loadEarlier()}, t('pm_earlier_notes'));
      coverage = h('p', {class: 'muted'}); permissions = h('p', {class: 'muted'});
      const text = input('pm_note_content', {multiline: true, max: 12000});
      const target = {workspace_id: workspaceId, thread_id: threadId};
      const preconditions = () => ({expected_version: threadData.thread.version});
      const write = () => permitted(false) && threadData?.capabilities?.write === true;
      const decide = () => permitted(true) && threadData?.capabilities?.decide === true;
      noteForm = form({action: 'pm.message.append', target, fields: {content: text}, params: values => ({content: values.content}), preconditions,
        permitted: write, label: 'pm_save_note', onSaved: () => load(),
        extra: h('div', {}, labelled('pm_note_content', text), h('p', {class: 'muted'}, t('pm_note_help')))});
      discussionHint = h('p', {class: 'muted'});
      const column = h('section', {class: 'pm-discussion'},
        h('div', {class: 'pm-thread-heading'}, threadTitle, mode), discussionHint, earlier, notes, coverage);
      if (caps()?.features?.pm_workspaces?.native_analysis) {
        nativeAnalysis = pmNativeAnalysis({h, t, form, input, labelled, caps,
          capability: () => pmCapabilities, thread: () => threadData, permitted, target, refresh: load});
        column.append(nativeAnalysis.box, h('details', {class: 'pm-package-preparation', 'data-pm-note-only': ''},
          h('summary', {}, t('pm_native_note_only')), noteForm.box));
      } else column.append(noteForm.box);
      if (caps()?.features?.pm_workspaces?.plan_drafting) {
        revisions = planRevisions({h, t, api, alive, workspaceId, threadId, thread: () => threadData,
          form, input, labelled, permitted, refresh: load, errorBox});
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
        ...['unresolved', 'agent_assumption', 'repo_evidence', 'conflicting', 'not_applicable'].map(value => h('option', {value}, t('pm_status_' + value))));
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
        extra: h('div', {}, selected, labelled('pm_plan_rationale', rationale), h('p', {class: 'muted'}, t('pm_plan_help')))});
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
          extra: h('div', {}, selected, draftId, labelled('pm_planning_revision_rationale', rationale), h('p', {class: 'muted'}, t('pm_planning_revision_help')))});
      }
      const inspector = h('aside', {class: 'pm-inspector', 'aria-label': t('pm_inputs')},
        h('h2', {}, t('pm_inputs')), h('p', {class: 'muted'}, t('pm_inputs_help')), permissions, facts,
        h('details', {'data-pm-add-input': ''}, h('summary', {}, t('pm_add_input')), factForm.box),
        draft, h('section', {'data-pm-plan': ''}, h('h2', {}, t('pm_direction')), planForm.box),
        planningRevisionForm ? h('details', {'data-pm-planning-revision': ''}, h('summary', {}, t('pm_planning_revision_title')), planningRevisionForm.box) : '');
      if (brief) inspector.prepend(brief.box);
      if (lifecycle) inspector.prepend(lifecycle.element);
      content.append(h('div', {class: 'pm-thread-layout'}, column, inspector));
      const disclosures = threadDisclosures.get(disclosureKey);
      for (const selector of threadDisclosureSelectors) {
        const element = content.querySelector(selector);
        if (element && disclosures?.has(selector)) element.open = disclosures.get(selector);
      }
    }
    threadTitle.textContent = thread.title; mode.textContent = t('pm_mode_' + thread.mode);
    discussionHint.textContent = t(nativeAnalysis || data.messages?.some(message => message.role === 'assistant') ? 'pm_native_discussion' : 'pm_manual');
    const bottom = notes.scrollHeight - notes.scrollTop - notes.clientHeight < 32, scroll = notes.scrollTop;
    notes.replaceChildren(...(data.messages || []).map(message => pmDiscussionMessage({h, t, message, time, timestamp})));
    if (!data.messages?.length) notes.append(h('p', {class: 'muted pm-empty'}, t('pm_no_notes')));
    notes.scrollTop = bottom ? notes.scrollHeight : scroll;
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
      if (id && ![...slotSelect.options].some(option => option.value === id)) slotSelect.append(h('option', {value: id}, t('pm_slot_' + id) === 'pm_slot_' + id ? slot.label || id : t('pm_slot_' + id)));
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
        h('div', {class: 'pm-fact-heading'}, checkbox, h('strong', {}, t('pm_slot_' + item.slot) === 'pm_slot_' + item.slot ? item.slot : t('pm_slot_' + item.slot)),
          h('span', {class: `chip ${item.status === 'user_confirmed' ? 'ok' : ['unresolved', 'conflicting', 'agent_assumption'].includes(item.status) ? 'warn' : ''}`}, t('pm_status_' + item.status))),
        h('p', {class: 'pm-note-text'}, item.value), h('p', {class: 'muted pm-source'}, sourceText(item)),
        h('p', {class: 'muted'}, item.status === 'user_confirmed' ? t('pm_confirmed_by', {actor: item.confirmed_by || item.recorded_by}) : t('pm_unverified')));
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
        h('h3', {}, t('pm_open_planning')), h('ul', {}, ...(data.draft.unknowns || []).map(item => h('li', {}, t('pm_slot_' + item.slot)))),
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
      const detail = h('div'), item = h('details', {'data-pm-run': run.run_id},
        h('summary', {}, run.purpose, ' · ', time(run.created_at),
          run.adapter === 'codex-native-linux' ? ' · ' + nativeRunState(t, run) : ''), detail);
      let fetched = false, pending = false;
      item.addEventListener('toggle', async () => {
        if (!item.open || fetched || pending) return;
        pending = true; detail.replaceChildren(h('p', {class: 'muted'}, t('pm_package_loading')));
        try {
          const data = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(run.run_id)}`);
          alive(); if (!item.isConnected) return;
          const saved = data.run;
          if (saved?.workspace_id !== workspaceId || saved?.thread_id !== threadId || saved?.run_id !== run.run_id) throw Error(t('pm_scope_mismatch'));
          const context = saved.context;
          detail.replaceChildren(h('p', {class: 'muted'}, t('pm_package_version', {version: context.thread_version})),
            h('a', {href: `#/op/${saved.operation_id}`}, t('pm_operation')),
            ...Object.entries(context.inputs_by_status || {}).filter(([, inputs]) => inputs.length).map(([status, inputs]) =>
              h('section', {}, h('h4', {}, t('pm_status_' + status)), ...inputs.map(input => h('div', {}, h('p', {}, input.value),
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
    if (!packages.children.length) packages.append(h('p', {class: 'muted'}, t('pm_packages_empty')));
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
      if (!retired(error) && ticket === sequence) {loaded = false; brief?.withdraw();lifecycle?.withdraw();updateForms(); status.replaceChildren(errorBox(error), h('p', {class: 'muted'}, t('pm_stale')));}
    } finally {if (ticket === sequence) {refreshing = false; refresh.disabled = false; earlier.disabled = !loaded;}}
  }
  async function load() {
    const ticket = ++sequence; refreshing = true; refresh.disabled = true;
    lifecycle?.withdraw();issues?.withdraw();
    releaseBaselines?.withdraw();
    try {
      alive(); const list = await api('GET', '/pm/workspaces'); alive();
      let detail = null, threadList = null, data = null, runs = null, plans = null, accepted = null, workspaceCaps = null;
      if (workspaceId) {
        [detail, threadList, data, runs, plans, accepted, workspaceCaps] = await Promise.all([api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}`),
          api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/threads`),
          threadId ? api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/threads/${encodeURIComponent(threadId)}`) : Promise.resolve(null),
          threadId && caps()?.features?.pm_workspaces?.context_packages
            ? api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/runs?thread_id=${encodeURIComponent(threadId)}&limit=20`) : Promise.resolve(null),
          threadId && caps()?.features?.pm_workspaces?.plan_drafting
            ? api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/plans?limit=20`) : Promise.resolve(null),
          threadId && caps()?.features?.pm_workspaces?.plan_drafting
            ? api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/plans/accepted`) : Promise.resolve(null),
          threadId && (caps()?.features?.pm_workspaces?.plan_drafting || caps()?.features?.pm_workspaces?.native_analysis)
            ? api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/capabilities`) : Promise.resolve(null)]);
        alive();
        for (let page = 1; page < threadPages && threadList.next_cursor; page++) {
          const next = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/threads?after=${encodeURIComponent(threadList.next_cursor)}`); alive();
          threadList = {...next, threads: [...threadList.threads, ...next.threads]};
        }
      }
      if (ticket !== sequence) return;
      if (detail && detail.workspace?.workspace_id !== workspaceId || data && (data.thread?.workspace_id !== workspaceId || data.thread?.thread_id !== threadId)) throw Error(t('pm_scope_mismatch'));
      workspace = detail?.workspace || null; pmCapabilities = workspaceCaps; loaded = true; status.replaceChildren();
      title.textContent = workspace?.name || t('pm_title');
      workspaceList.replaceChildren(...(list.workspaces || []).map(item => h('div', {class: 'pm-workspace-row'},
        h('a', {href: `#/pm/${encodeURIComponent(item.workspace_id)}`, 'aria-current': item.workspace_id === workspaceId ? 'page' : null}, item.name),
        ...(item.workspace_id === workspaceId ? (threadList?.threads || []).map(thread => h('a', {class: 'pm-thread-link',
          href: `#/pm/${encodeURIComponent(workspaceId)}/${encodeURIComponent(thread.thread_id)}`, 'aria-current': thread.thread_id === threadId ? 'page' : null},
          h('span', {}, thread.title), h('small', {}, t('pm_mode_' + thread.mode)))) : []))));
      if (!list.workspaces?.length) {workspaceList.append(h('p', {class: 'muted'}, t('pm_no_workspaces'))); nav.querySelector('.pm-create-workspace').open = true;}
      if (workspace) workspaceList.append(h('a', {href: `#/pm/${encodeURIComponent(workspaceId)}`}, t('pm_new_thread')));
      if (threadList?.next_cursor) workspaceList.append(h('button', {type: 'button', class: 'secondary', onclick: () => {threadPages++; void load();}}, t('pm_more_threads')));
      if (data) renderThread(data);
      else if (!content.children.length) {
        if (!workspace) content.append(h('div', {class: 'pm-empty'}, h('h2', {}, t('pm_start_title')), h('p', {}, t('pm_start_help'))));
        else {
          const title = input('pm_thread_title', {max: 160});
          const create = form({action: 'pm.thread.create', target: {workspace_id: workspaceId}, fields: {title},
            params: values => ({title: values.title.trim()}), preconditions: () => ({expected_version: workspace.version}),
            permitted: () => permitted(false), label: 'pm_create_thread',
            onSaved: result => {alive(); location.hash = `#/pm/${encodeURIComponent(workspaceId)}/${encodeURIComponent(result.thread_id)}`;},
            extra: labelled('pm_thread_title', title)});
          content.append(h('section', {class: 'pm-start panel'}, h('h2', {}, t('pm_new_thread')), h('p', {class: 'muted'}, t('pm_thread_help')), create.box));
        }
      }
      if (runs) {
        if (runs.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
        renderPackages(runs);
      }
      if (plans && revisions) revisions.update(plans, accepted, workspaceCaps);
      worktreeChoice?.update();
      for (const section of [lifecycle, evidence, brief, releaseBaselines, roadmap, kanban, sprints, meetings, governance, specifications, followUp, engineering,issues]) {
        if (section) await section.refresh();
        alive(); if (ticket !== sequence) return;
      }
      nativeAnalysis?.update(runs?.runs || []);
      for (const section of sourceSections()) section.hidden = false;
      updateForms();
    } catch (error) {
      if (!retired(error) && ticket === sequence) {
        loaded = false; workspace = null; threadData = null;
        brief?.withdraw();
        lifecycle?.withdraw();issues?.withdraw();releaseBaselines?.withdraw();
        title.textContent = t('pm_title'); workspaceList.replaceChildren();
        for (const section of sourceSections()) section.hidden = true;
        updateForms(); status.replaceChildren(errorBox(error), h('p', {class: 'muted'}, t('pm_stale')));
      }
    } finally {if (ticket === sequence) {refreshing = false; refresh.disabled = false;}}
  }
  await load();
  let eventReads = Promise.resolve();
  const off = onEvents(event => {
    if (['pm_workspace', 'pm_thread', 'pm_run', 'pm_plan', 'pm_evidence', 'pm_snapshot', 'pm_roadmap', 'pm_sprint', 'pm_meeting', 'pm_governance', 'pm_follow_up', 'pm_follow_up_assessment', 'pm_work_package', 'pm_lifecycle','pm_issue_mapping','pm_release_baseline'].includes(event.resource_type)) {
      // A newer event must not be ACKed merely because an older manual read is in flight.
      eventReads = eventReads.catch(() => {}).then(() => load());
      return eventReads;
    }
    if (kanban && ['work_item', 'project', 'task', 'command', 'operation', 'session'].includes(event.resource_type)) {
      eventReads = eventReads.catch(() => {}).then(() => kanban.refresh());
      return eventReads;
    }
  });
  return () => {
    if (threadId && mountedThread === threadId) {
      const disclosures = new Map();
      for (const selector of threadDisclosureSelectors) {
        const element = content.querySelector(selector);
        if (element) disclosures.set(selector, element.open);
      }
      threadDisclosures.delete(disclosureKey); threadDisclosures.set(disclosureKey, disclosures);
      if (threadDisclosures.size > 40) threadDisclosures.delete(threadDisclosures.keys().next().value);
    }
    disposed = true; sequence++; off(); navigationMedia.removeEventListener('change', resizeNavigation);
    for (const value of forms) value.dispose();
  };
}
