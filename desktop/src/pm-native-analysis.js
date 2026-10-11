// A native turn consumes a frozen central snapshot; the client never starts a local agent.
export function pmNativeAnalysis({h, t, form, input, labelled, caps, capability, thread, permitted, target, refresh}) {
  const purpose = input('pm_native_purpose', {multiline: true, max: 4000});
  const model = input('pm_native_model', {max: 128, required: false});
  model.placeholder = t('pm_native_default_model');
  const explanation = h('p', {class: 'muted', 'data-pm-native-eligibility': ''});
  const current = h('div', {'data-pm-native-runs': ''});
  const authorized = () => permitted(false) && thread()?.capabilities?.write === true
    && caps()?.scopes?.includes('start');
  const request = form({action: 'pm.run.analyze', target, fields: {purpose, model},
    params: values => ({purpose: values.purpose, ...(values.model.trim() ? {model: values.model.trim()} : {})}),
    preconditions: () => ({expected_version: thread().thread.version}),
    permitted: () => authorized() && capability()?.native_analysis?.eligible_for_preflight === true,
    recoverPermitted: authorized, label: 'pm_native_analyze', onSaved: refresh,
    extra: h('div', {class: 'pm-fields'}, labelled('pm_native_purpose', purpose), labelled('pm_native_model', model))});
  const box = h('section', {class: 'pm-packages pm-native-analysis', 'data-pm-native-analysis': ''},
    h('h3', {}, t('pm_native_title')), h('p', {class: 'muted'}, t('pm_native_context_only')),
    explanation, request.box, current);
  function update(runs = []) {
    const native = capability()?.native_analysis;
    explanation.textContent = t(!authorized() ? 'pm_native_permissions' : native?.eligible_for_preflight
      ? 'pm_native_preflight_pending' : {
          PM_NATIVE_PLATFORM_UNSUPPORTED: 'pm_native_platform', PM_NATIVE_EXECUTABLE_UNAVAILABLE: 'pm_native_install',
          PM_NATIVE_MANAGED_CONFIG_UNSUPPORTED: 'pm_native_managed', PM_NATIVE_AUTH_UNAVAILABLE: 'pm_native_auth',
          PM_NATIVE_HOME_UNAVAILABLE: 'pm_native_auth',
        }[native?.reason] || 'pm_native_unavailable');
    current.replaceChildren(...runs.filter(run => run.adapter === 'codex-native-linux').map(run =>
      h('p', {class: 'pm-source'}, h('strong', {}, run.purpose), ' · ', nativeRunState(t, run),
        run.outcome?.actual_model ? ' · ' + run.outcome.actual_model : '', ' · ',
        h('a', {href: `#/op/${encodeURIComponent(run.operation_id)}`}, t('pm_operation')))));
    request.update();
  }
  return {box, update};
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
    h('div', {class: 'pm-note-stamp'}, h('strong', {}, assistant ? t('pm_native_assistant') : message.actor),
      assistant ? h('span', {}, provenance?.model || t('pm_native_unknown_model')) : '',
      h('time', {datetime: new Date(timestamp(message.created_at)).toISOString()}, time(message.created_at))),
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
