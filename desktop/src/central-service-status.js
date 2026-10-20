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
