import {managedRecoveryKey} from './managed-recovery.js';

// The existing pollHealth slot holds either initial/connected or this failed
// /events observation. HTTP status and code are from the existing response;
// at is the client's observation time. No server health authority is inferred.
export function pollFailure(error, at) {
  const status = Number.isFinite(error.status) ? error.status : 200;
  const code = error.code || 'INVALID_EVENT_PAGE';
  const kind = code === 'EVENT_CURSOR_RESET' ? 'continuity'
    : status === 401 || status === 403 ? 'refused'
    : status >= 500 ? 'service_error' : status === 0 ? 'transport' : 'invalid_response';
  return {kind, status, code, at};
}

const pollKeys = {transport: 'ui_reconnecting', refused: 'ui_connection_refused',
  service_error: 'ui_service_error', invalid_response: 'ui_invalid_response', continuity: 'ui_connecting'};
const pollReasons = {ui_reconnecting: 'reconnecting', ui_connection_refused: 'refused',
  ui_service_error: 'service_error', ui_invalid_response: 'invalid_response'};

// Read existing client observations; this is not another health store. Bootstrap
// success proves reachability before the first event poll. Later view refresh
// failures pause actions without withdrawing a successful transport observation.
export function centralServiceStatus({token, pollHealth, online, managedRecovery}) {
  const connection = !token ? 'offline'
    : pollHealth === 'connected' || (pollHealth === 'initial' && online) ? 'connected' : 'reconnecting';
  const recoveryKey = managedRecoveryKey(managedRecovery);
  const key = recoveryKey || (!token ? 'ui_not_connected' : connection === 'connected' ? 'ui_connected'
    : pollHealth === 'initial' ? 'ui_connecting' : pollKeys[pollHealth?.kind] || (pollHealth === 'reconnecting' ? 'ui_reconnecting' : 'ui_connecting'));
  const tone = key === 'ui_connected' ? 'ok' : pollReasons[key] ? 'warn' : 'neutral';
  // Keep the established data-connection enum for observers. "offline" means
  // no client session here; its displayed label makes no reachability claim.
  return {key, tone, connection, recoveryKey};
}

// Requirements describe each caller’s action checks. Transport labels explain
// refusals; clipboard format support is reported by the native reader.
export function clientActionGate(facts, requirements) {
  const ready = requirements.post || requirements.attachment;
  const blocked = (requirements.post && (!facts.token || facts.nativeBusy))
    || (ready && (!facts.online || !facts.viewReady))
    || (requirements.supported && !facts.attachmentsSupported)
    || (requirements.manage && !facts.manageAllowed)
    || (requirements.uploads && !facts.uploadsAllowed);
  if (!blocked) return {allowed: true, reason: null};
  const key = centralServiceStatus(facts).key;
  const reason = (requirements.post && !facts.token) || key === 'ui_not_connected' ? 'not_connected'
    : requirements.post && facts.nativeBusy ? 'connection_changing'
    : requirements.supported && !facts.attachmentsSupported ? 'attachments_unavailable'
    : requirements.manage && !facts.manageAllowed ? 'manage_required'
    : requirements.uploads && !facts.uploadsAllowed ? 'uploads_not_allowed'
    : !facts.online && pollReasons[key] ? pollReasons[key]
    : !facts.online && key === 'ui_connecting' ? 'connecting'
    : !facts.viewReady || key === 'ui_connected' ? 'view_not_ready' : 'connecting';
  return {allowed: false, reason};
}

const clientReasons = ['not_connected', 'connecting', ...Object.values(pollReasons), 'connection_changing',
  'view_not_ready', 'manage_required', 'attachments_unavailable', 'uploads_not_allowed', 'native_paste_unsupported'];
export const clientActionCode = reason => `CLIENT_ACTION_${reason.toUpperCase()}`;
export const clientActionMessageKey = reason => `error_code_${clientActionCode(reason)}`;
export const clientActionReason = code => clientReasons.find(reason => clientActionCode(reason) === code) || null;
