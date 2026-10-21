import {errorPresentation} from './error-presentation.js';

export const eligibilityReasons = {
  PM_NATIVE_PLATFORM_UNSUPPORTED: 'agents_platform', PM_NATIVE_EXECUTABLE_UNAVAILABLE: 'agents_executable',
  PM_NATIVE_AUTH_UNAVAILABLE: 'agents_auth', PM_NATIVE_HOME_UNAVAILABLE: 'agents_home',
  PM_NATIVE_MANAGED_CONFIG_UNSUPPORTED: 'agents_policy', PM_NATIVE_INSTALLATION_UNAVAILABLE: 'agents_installation',
  action_unavailable: 'agents_action', PM_WORKTREE_REQUIRED: 'agents_worktree',
  REPOSITORY_BINDING_CHANGED: 'agents_binding', PM_BAT_BINDING_CHANGED: 'agents_binding',
  REPOSITORY_NOT_CONFIGURED: 'agents_bat_unknown', REPOSITORY_NOT_BOUND: 'agents_binding',
  REPOSITORY_HOST_UNAVAILABLE: 'agents_tier',
  TIER_DISABLED: 'agents_tier', HOST_READ_ONLY: 'agents_tier', START_WORKTREE_REQUIRED: 'agents_start_worktree',
};

// Availability describes existing facts only. Ready remains the existing
// any-installation hint; it is never evidence that a model has executed.
export function pmAvailability({caps, document, readError = null, checked = document != null,
  checking = false, kind = 'analysis', descriptors} = {}) {
  const result = (state, reason, messageKey, statusKey = 'agents_unavailable', code = '') =>
    ({state, reason, messageKey, statusKey, code, ready: state === 'ready', error: null, failures: []});
  if (readError) return {...result('failed', 'read_failed', 'agents_read_failed', 'agents_failed'), error: readError};
  if (!caps) return result('unknown', 'capabilities_unknown', 'agents_unknown', 'agents_unknown_status');
  const feature = caps.features?.pm_workspaces;
  if (feature?.version !== 1) return result('unsupported', 'pm_unsupported', 'pm_availability_unsupported');
  if (!Array.isArray(caps.scopes)) return result('unknown', 'scopes_unknown', 'agents_unknown', 'agents_unknown_status');
  if (!caps.scopes.includes('observe')) return result('missing_scope', 'observe_scope', 'pm_availability_observe');
  if (kind === 'workspace') return result('ready', 'workspace_supported', 'agents_preflight', 'agents_ready');
  if (checking) return result('checking', 'checking', 'agents_checking_help', 'agents_checking');
  if (!checked) return result('not_checked', 'not_checked', 'agents_not_checked', 'agents_unchecked');
  const rows = (descriptors || [document?.native_analysis, document?.bat_analysis])
    .filter(value => value && typeof value.eligible_for_preflight === 'boolean');
  if (!rows.length) {
    if (!feature.native_analysis && !feature.bat_analysis && !feature.installation_eligibility)
      return result('unsupported', 'analysis_unsupported', 'agents_action');
    return result('unknown', 'eligibility_unknown', 'agents_unknown', 'agents_unknown_status');
  }
  const permitted = ['observe', 'manage', 'start'].every(scope => caps.scopes.includes(scope));
  const failures = rows.map(value => {
    if (value.action_registered === false) return result('unregistered', 'action_unavailable', eligibilityReasons.action_unavailable);
    if (value.reason) return result('ineligible', value.reason,
      Object.hasOwn(eligibilityReasons, value.reason) ? eligibilityReasons[value.reason] : 'agents_unknown', 'agents_unavailable', value.reason);
    if (!permitted) return result('missing_scope', 'analysis_scope', 'agents_scope');
    // authorized=false does not say whether membership, policy or another gate
    // refused the action. Do not invent the missing I10 reason field.
    if (value.authorized === false) return result('unauthorized', 'authorization_unexplained', 'agents_authorization');
    return result('unknown', 'eligibility_unknown', 'agents_unknown', 'agents_unknown_status');
  });
  // Preserve Settings' descriptor order and its exact pre-existing ready predicate.
  const ready = rows.some(value => value.eligible_for_preflight === true && value.action_registered === true
    && value.authorized !== false) && permitted;
  return {...(ready ? result('ready', 'installation_hint', 'agents_preflight', 'agents_ready') : failures[0]), failures};
}

const painted = new WeakMap();
export function renderPmAvailability(node, {h, t, state, includeReady = false, includeMessage = true, setupHref = ''}) {
  const error = state.error ? errorPresentation(state.error, t) : null;
  const signature = JSON.stringify([state.state, t(state.messageKey), state.code, error, includeReady, includeMessage, setupHref, t('pd_setup')]);
  if (painted.get(node) === signature) return node;
  painted.set(node, signature);
  node.dataset.pmAvailability = state.state;
  node.hidden = state.ready && !includeReady;
  const children = includeMessage ? [h('p', {class: 'muted'}, t(state.messageKey))] : [];
  if (error) {
    children.push(h('div', {class: 'error', role: 'alert'}, h('p', {}, error.summary),
      error.code ? h('code', {}, error.code) : null,
      error.message ? h('details', {}, h('summary', {}, t('global_details')), h('p', {}, error.message)) : null));
  } else if (state.code && state.messageKey === 'agents_unknown') children.push(h('code', {}, state.code));
  if (!state.ready && setupHref) children.push(h('a', {href: setupHref}, t('pd_setup')));
  node.replaceChildren(...children);
  return node;
}
