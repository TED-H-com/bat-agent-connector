import {managedRecoveryKey} from './managed-recovery.js';

// Read existing client observations; this is not another health store. Bootstrap
// success proves reachability before the first event poll. Later view refresh
// failures pause actions without withdrawing a successful transport observation.
export function centralServiceStatus({token, pollHealth, online, managedRecovery}) {
  const connection = !token ? 'offline'
    : pollHealth === 'connected' || (pollHealth === 'initial' && online) ? 'connected' : 'reconnecting';
  const recoveryKey = managedRecoveryKey(managedRecovery);
  const key = recoveryKey || (!token ? 'ui_not_connected' : connection === 'connected' ? 'ui_connected'
    : pollHealth === 'initial' ? 'ui_connecting' : 'ui_reconnecting');
  const tone = key === 'ui_connected' ? 'ok' : key === 'ui_reconnecting' ? 'warn' : 'neutral';
  // Keep the established data-connection enum for observers. "offline" means
  // no client session here; its displayed label makes no reachability claim.
  return {key, tone, connection, recoveryKey};
}

// Requirements describe the checks each caller already made. Transport labels
// explain a refusal; they never add a new requirement to an otherwise allowed action.
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
    : !facts.online && key === 'ui_reconnecting' ? 'reconnecting'
    : !facts.online && key === 'ui_connecting' ? 'connecting'
    : !facts.viewReady || key === 'ui_connected' ? 'view_not_ready' : 'connecting';
  return {allowed: false, reason};
}

const clientReasons = ['not_connected', 'connecting', 'reconnecting', 'connection_changing',
  'view_not_ready', 'manage_required', 'attachments_unavailable', 'uploads_not_allowed'];
export const clientActionCode = reason => `CLIENT_ACTION_${reason.toUpperCase()}`;
export const clientActionMessageKey = reason => `error_code_${clientActionCode(reason)}`;
export const clientActionReason = code => clientReasons.find(reason => clientActionCode(reason) === code) || null;
