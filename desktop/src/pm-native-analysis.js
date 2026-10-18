import {timeStamp} from './ui/stamps.js';
import {discussionFeedback, discussionSettingsHref} from './pm-discussions.js';
import {agentMark} from './ui/components.js';
import {pmContextBundle} from './pm-context-bundle.js';
import {workspaceAdapter,workspaceAdapterAllows,workspaceAdapterChoice} from './pm-workspace-adapter.js';
// A native turn consumes a frozen central snapshot; the client never starts a local agent.
export function pmNativeAnalysis({h, t, api, form, input, labelled, caps, capability, thread, permitted, target, refresh, namespace, guard, errorBox}) {
  const specs = [
    {key: 'native_analysis', adapter: 'codex-native-linux', action: 'pm.run.analyze', label: 'pm_analysis_local'},
    {key: 'bat_analysis', adapter: 'bat-pm-linux-v1', action: 'pm.run.bat.analyze', label: 'pm_analysis_bat'},
  ];
  const advertised = spec => caps()?.features?.pm_workspaces?.[spec.key] === true;
  const storage = typeof namespace === 'string' ? `batc.pm.${namespace}.analysis-adapter.${JSON.stringify(target)}` : null;
  let selected = specs.find(spec => advertised(spec) && capability()?.[spec.key]?.eligible_for_preflight === true)?.key || specs.find(advertised)?.key || specs[0].key, ready = false, restored = false;
  try {
    const saved = storage && localStorage.getItem(storage);
    if (specs.some(spec => spec.key === saved)) {selected = saved; restored = true;}
  } catch { /* A preference cannot grant authority or replace a durable request. */ }
  const selector = h('select', {'aria-label': t('pm_analysis_adapter'), 'data-pm-analysis-adapter': ''});
  const explanation = h('p', {class: 'muted', 'data-pm-native-eligibility': ''});
  const binding = h('p', {class: 'pm-source', 'data-pm-analysis-binding': '', hidden: true});
  const recovery = h('p', {class: 'muted', 'data-pm-analysis-recovery': '', hidden: true});
  const current = h('div', {'data-pm-native-runs': ''});
  const authorized = () => permitted(false) && thread()?.capabilities?.write === true
    && caps()?.scopes?.includes('start');
  const requests = [];
  let primary = true, composerNode = null, ask = null, connection = null, working = null, clock = null, composerText = null, clearDraft = null, limit = null, requestDetails = null;
  const choice = workspaceAdapterChoice({h,t,form,input,labelled,caps,capability,permitted,target,refresh,
    onStateChange:()=>{if(ready)for(const row of requests)row.request.update();}});
  const otherLocked = spec => requests.some(row => row.spec !== spec && row.request.locked());
  for (const spec of specs) {
    const purpose = input('pm_native_purpose', {multiline: true, max: 4000});
    const model = input('pm_native_model', {max: 128, required: false});
    model.placeholder = t('pm_native_default_model');
    const context = pmContextBundle({h,t,api,guard,caps,target,thread,errorBox});
    const request = form({action: spec.action, target, fields: {purpose, model, accepted_context: context.value},
      params: values => ({purpose: values.purpose, ...context.params(values), ...(values.model.trim() ? {model: values.model.trim()} : {})}),
      preconditions: () => ({expected_version: thread().thread.version}),
      permitted: () => selected === spec.key && advertised(spec) && authorized() && !otherLocked(spec)
        && !choice.locked() && workspaceAdapterAllows(capability(),spec.adapter)
        && context.valid() && capability()?.[spec.key]?.authorized !== false && capability()?.[spec.key]?.eligible_for_preflight === true,
      recoverPermitted: authorized, label: 'pm_native_analyze', onSaved: refresh,
      onBeforeSaved: (_result, values) => clearDraft?.(values.purpose),
      onStateChange: () => {context.update();if (ready) render();},
      extra: h('div', {class: 'pm-fields'}, labelled('pm_native_purpose', purpose), context.box, labelled('pm_native_model', model))});
    context.bind(request);
    requests.push({spec, request});
  }
  if (!restored) selected = requests.find(row => row.request.locked())?.spec.key || selected;
  selector.addEventListener('change', () => {
    try {guard?.();} catch {return;}
    if (!specs.some(spec => spec.key === selector.value)) return;
    selected = selector.value;
    try {if (storage) localStorage.setItem(storage, selected);} catch { /* Request drafts remain independently durable. */ }
    update();
  });
  const box = h('section', {class: 'pm-packages pm-native-analysis', 'data-pm-native-analysis': ''},
    h('h3', {}, t('pm_native_title')), h('p', {class: 'muted'}, t('pm_native_context_only')),
    choice.box,
    h('div', {class: 'pm-fields'}, labelled('pm_analysis_adapter', selector)),
    explanation, binding, recovery, ...requests.map(row => row.request.box), current);
  function render() {
    const locked=requests.find(row=>row.request.locked());
    const fixed=workspaceAdapter(capability())?.choice?.adapter;
    if(fixed) selected=locked?.spec.key||specs.find(spec=>spec.adapter===fixed)?.key||selected;
    const active = specs.find(spec => spec.key === selected);
    const descriptor = capability()?.[selected];
    const options = requests.filter(row => advertised(row.spec) || row.request.locked() || row.spec === active);
    selector.replaceChildren(...options.map(row => h('option', {value: row.spec.key},
      t(row.spec.label) + (row.request.locked() ? ' · ' + t('pm_analysis_original') : ''))));
    selector.value = selected;
    selector.disabled=Boolean(fixed);
    for (const row of requests) row.request.box.hidden = row.spec !== active;
    const pending = requests.filter(row => row.spec !== active && row.request.locked());
    recovery.hidden = !pending.length;
    recovery.textContent = pending.length ? t('pm_analysis_recover_other') + ' ' + pending.map(row => t(row.spec.label)).join(', ') : '';
    const eligible = advertised(active) && descriptor?.eligible_for_preflight === true;
    const reason = selected === 'bat_analysis' ? {
      PM_WORKTREE_REQUIRED: 'pm_analysis_bat_worktree', REPOSITORY_BINDING_CHANGED: 'pm_analysis_bat_binding_changed',
      HOST_READ_ONLY: 'pm_analysis_bat_readonly',
    } : {
      PM_NATIVE_PLATFORM_UNSUPPORTED: 'pm_native_platform', PM_NATIVE_EXECUTABLE_UNAVAILABLE: 'pm_native_install',
      PM_NATIVE_MANAGED_CONFIG_UNSUPPORTED: 'pm_native_managed', PM_NATIVE_AUTH_UNAVAILABLE: 'pm_native_auth',
      PM_NATIVE_HOME_UNAVAILABLE: 'pm_native_auth',
    };
    explanation.textContent = t(!authorized() || descriptor?.authorized === false ? 'pm_native_permissions'
      : eligible ? selected === 'bat_analysis' ? 'pm_analysis_bat_preflight' : 'pm_native_preflight_pending'
      : reason[descriptor?.reason] || (selected === 'bat_analysis' ? 'pm_analysis_bat_unavailable' : 'pm_native_unavailable'));
    if (composerNode) {
      const request = requests.find(row => row.spec === active)?.request;
      const mayReply = eligible && authorized() && descriptor?.authorized !== false
        && caps()?.actions?.some(row => row.action === active.action && row.allowed === true);
      ask.textContent = t(request?.locked() ? 'pm_check_original' : 'pd_send_pm');
      const content = composerText.value.trim();
      ask.hidden = request?.replaceable() || !mayReply && !request?.locked();
      ask.classList.toggle('primary', primary); ask.classList.toggle('secondary', !primary);
      ask.disabled = !request?.canSubmit() || !request?.locked() && (!content || content.length > 4000);
      limit.hidden = request?.locked() || content.length <= 4000;
      requestDetails.hidden = !request?.operation() && !request?.box.querySelector(':scope > [role=status] .error');
      connection.hidden = mayReply;
      discussionFeedback({t, request, node: working, eligible: mayReply});
    }
    const source = thread() && selected === 'bat_analysis' && advertised(active) ? descriptor?.selected_binding : null;
    if (!thread()) current.replaceChildren();
    binding.hidden = !source;
    binding.textContent = source ? t('pm_analysis_selected_binding') + ' ' + [source.host, source.workspace_id, source.repository].filter(Boolean).join(' · ') : '';
  }
  let lastRuns = [];
  function update(runs) {
    if (runs) lastRuns = runs;
    choice.update();
    render();
    current.replaceChildren(...lastRuns.filter(run => specs.some(spec => spec.adapter === run.adapter)).map(run =>
      h('p', {class: 'pm-source'}, h('strong', {}, run.purpose), ' · ',
        t(specs.find(spec => spec.adapter === run.adapter).label), ' · ', nativeRunState(t, run),
        run.outcome?.actual_model ? ' · ' + run.outcome.actual_model : '', ' · ',
        h('a', {href: `#/op/${encodeURIComponent(run.operation_id)}`}, t('pm_operation')))));
    for (const row of requests) row.request.update();
  }
  ready = true;
  update();
  return {box, update, setPrimary(value) {primary = value;if (composerNode) render();}, recheck() {for (const {request} of requests) request.recheck();}, composer(textarea, onCompleted, onDetails) {
    composerText = textarea; clearDraft = onCompleted;
    ask = h('button', {type: 'button', class: 'primary', 'data-discussion-reply': '', onclick: () => {
      const request = requests.find(row => row.spec.key === selected)?.request;
      if (!request) return;
      if (!request.locked()) {
        const content = textarea.value.trim();
        if (!content || content.length > 4000) return;
        request.set('purpose', content);
      }
      void request.submit();
    }}, t('pd_send_pm'));
    connection = h('p', {class: 'muted'}, h('span', {}, t('pd_reply_unavailable')), ' · ', h('a', {href: discussionSettingsHref()}, t('pd_setup')));
    working = h('p', {class: 'muted', role: 'status', 'data-discussion-reply-working': ''});
    limit = h('p', {class: 'muted', hidden: true}, t('pd_reply_limit'));
    requestDetails = h('button', {type: 'button', class: 'ui-button ghost ui-sm', hidden: true, 'data-discussion-reply-details': '', onclick: onDetails}, t('pd_details'));
    composerNode = h('div', {class: 'pd-ask'}, ask, connection, h('div', {class: 'actions'}, working, requestDetails), limit);
    textarea.addEventListener('input', render);
    clock = setInterval(() => {const request = requests.find(row => row.spec.key === selected)?.request;
      if (composerNode.isConnected && request?.checking() && ['running', 'accepted', 'queued', 'pending'].includes(request?.operation()?.status)) render();}, 1000);
    render(); return composerNode;
  }, dispose() {clearInterval(clock);composerText?.removeEventListener('input', render);}};
}

export function pmDiscussionRequest({h, t, run}) {
  return h('article', {class: 'pm-note', 'data-pm-request': run.run_id},
    h('div', {class: 'pm-note-stamp'}, h('strong', {}, run.requester || t('pd_request')), timeStamp(run.created_at),
      run.requester ? h('span', {class: 'ui-meta'}, t('pd_request')) : ''),
    h('div', {class: 'pm-note-text'}, run.purpose),
    h('p', {class: 'pm-source'}, nativeRunState(t, run), ' · ',
      h('a', {href: '#/op/' + encodeURIComponent(run.operation_id)}, t('pm_operation'))));
}

export function nativeRunState(t, run) {
  return t({completed: 'pm_native_completed', aborted: 'pm_native_aborted',
    running: 'pm_native_running', reserved: 'pm_native_preparing',
    blocked: 'pm_native_blocked', unknown: 'pm_native_unknown'}[run.state || run.status] || 'pm_native_unknown');
}

export function pmDiscussionMessage({h, t, message, time, timestamp}) {
  const assistant = message.role === 'assistant';
  const provenance = message.provenance;
  const complete = assistant && provenance?.completeness === 'complete'
    && typeof provenance.native_turn_id === 'string' && provenance.native_turn_id
    && typeof provenance.run_id === 'string' && /^pmr_[0-9a-f]{32}$/.test(provenance.run_id);
  const source = provenance?.source_refs || {};
  const article = h('article', {class: 'pm-note', 'data-note-id': message.message_id, 'data-pm-role': assistant ? 'assistant' : 'user'},
    h('div', {class: 'pm-note-stamp'}, assistant ? agentMark('pm') : h('span', {class: 'ui-agent-mark', 'aria-hidden': true}, (message.actor || '?').slice(0, 2).toUpperCase()), h('strong', {}, assistant ? t('pm_native_assistant') : message.actor),
      assistant ? h('span', {}, provenance?.model || t('pm_native_unknown_model')) : '',
      timeStamp(message.created_at)),
    h('div', {class: 'pm-note-text'}, message.content));
  if (assistant) article.append(h('p', {class: 'muted'}, t(complete ? 'pm_native_reply_recorded' : 'pm_native_reply_unproven')),
    h('details', {class: 'pm-source'}, h('summary', {}, t('pm_native_evidence')),
      h('p', {}, t('pm_native_origin'), ' ', String(source.adapter || t('pm_native_unknown_source'))),
      h('p', {}, t('pm_native_run'), ' ', String(provenance?.run_id || '—')),
      h('p', {}, t('pm_native_thread'), ' ', String(source.native_thread_id || provenance?.sdk_session_id || '—')),
      h('p', {}, t('pm_native_turn'), ' ', String(provenance?.native_turn_id || '—')),
      h('p', {}, t('pm_native_message'), ' ', String(provenance?.native_message_id || '—')),
      h('p', {}, t('pm_native_context'), ' ', String(provenance?.context_digest || '—'))));
  return article;
}
