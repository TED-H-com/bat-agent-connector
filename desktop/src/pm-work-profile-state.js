// Readiness facts are independent: a past success never replaces today's value.
const values=new Set(['ready','missing','unknown','unavailable']);
const reasons=new Set(['REPOSITORY_NOT_BOUND','host_unknown','configured_unverified','DISCOVERY_SCOPE_CHANGED','SOURCE_UNAVAILABLE',
  'CREDENTIAL_REQUIRED','unauthorized','auth_validity_unverified','local_git_not_enabled','local_git_platform_unsupported',
  'local_git_tools_missing','git_execution_location_changed','ssh_alias_missing','REPOSITORY_HOST_UNAVAILABLE',
  'never_observed','host_unreachable','host_not_refreshed','action_unavailable','no_owned_session_verified','execution_not_verified']);
const operation=value=>typeof value==='string'&&/^op_[0-9a-f]{32}$/.test(value)?value:null;
const valueKey=value=>'wpf_'+(values.has(value)?value:'unknown');
export const profileTimestamp=value=>typeof value==='number'&&Number.isFinite(value)&&value>0&&value<253402300800?value:null;
export function profileStateFacts(state){
  const reason=typeof state?.reason==='string'&&/^[A-Za-z][A-Za-z0-9_]{0,79}$/.test(state.reason)?state.reason:null;
  const past=state?.verified&&typeof state.verified==='object'&&!Array.isArray(state.verified)?state.verified:null;
  const pastAt=profileTimestamp(past?.observed_at),lastSuccessAt=profileTimestamp(state?.evidence_ref?.last_success_at);
  return {valueKey:valueKey(state?.value),reasonKey:reasons.has(reason)?'wpf_reason_'+reason:'wpf_unverified',reasonCode:reason,
    operationId:operation(state?.evidence_ref?.operation_id),
    lastSuccessAt:lastSuccessAt===pastAt?null:lastSuccessAt,
    past:past?{valueKey:valueKey(past.value),at:pastAt,operationId:operation(past.evidence_ref?.operation_id)}:null};
}
