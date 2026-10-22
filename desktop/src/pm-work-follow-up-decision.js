const serverReasons = {
  codex_uncorrelated_turn_boundary: ['loop_next_codex', 'loop_next_codex_step', 'loop_next_blocked'],
  not_controller: ['loop_next_not_controller', 'loop_next_controller_step', 'loop_next_blocked'],
  not_writer_member: ['loop_next_member_blocked', 'loop_next_member_step', 'loop_next_member_readonly'],
  missing_scope: ['loop_next_scope_blocked', 'loop_next_scope_step', 'loop_next_scope_readonly'],
};

// The attempt result owns authorization. Client readiness is a separate source;
// it never turns a connection or an unfinished read into a server reason.
export function workFollowUpDecision({stopped = false, disposed = false, suspended = false,
  capabilitiesLoaded, reading, admissionReady, resultSettled, blockedReason = '', sendOffer,
  managed, connected}) {
  const canReadReceipt = Boolean(capabilitiesLoaded && managed && !stopped && !disposed && !suspended);
  const client = (reason, messageKey = '', nextStepKey = '') => ({allowed: false,
    source: 'client', reason, messageKey, nextStepKey, pillKey: '', showCode: false, canReadReceipt});
  if (stopped || disposed) return client('stopped');
  if (suspended) return client('suspended');
  if (!capabilitiesLoaded) return client('capabilities_pending');
  if (reading || !resultSettled) return client('admission_pending');
  if (!admissionReady) return client('admission_unavailable');
  if (blockedReason) {
    const known = Object.hasOwn(serverReasons, blockedReason);
    const [messageKey, nextStepKey, pillKey] = known ? serverReasons[blockedReason]
      : ['loop_next_blocked_unknown', 'loop_next_refresh_step', 'loop_next_blocked'];
    return {allowed: false, source: 'server', reason: blockedReason,
      messageKey, nextStepKey, pillKey, showCode: !known, canReadReceipt};
  }
  if (!sendOffer) return client('no_send_offer', 'loop_next_no_offer', 'loop_next_refresh_step');
  if (!managed) return client('unmanaged', 'loop_next_err_PM_WORK_SESSION_NOT_MANAGED', 'loop_next_refresh_step');
  if (!connected) return client('connection');
  return {allowed: true, source: 'server', reason: '', messageKey: '', nextStepKey: '', pillKey: '', showCode: false, canReadReceipt};
}
