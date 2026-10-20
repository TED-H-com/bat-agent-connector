import {test} from 'node:test';
import assert from 'node:assert/strict';
import {centralServiceStatus} from '../src/central-service-status.js';

test('credentials and authenticated bootstrap/poll observations settle central status', () => {
  for (const token of [null, '', 'credential']) for (const pollHealth of ['initial', 'connected', 'reconnecting']) {
    for (const online of [false, true]) {
      const connected = pollHealth === 'connected' || (pollHealth === 'initial' && online);
      const expected = !token ? {key: 'ui_not_connected', tone: 'neutral', connection: 'offline'}
        : connected ? {key: 'ui_connected', tone: 'ok', connection: 'connected'}
        : pollHealth === 'initial' ? {key: 'ui_connecting', tone: 'neutral', connection: 'reconnecting'}
        : {key: 'ui_reconnecting', tone: 'warn', connection: 'reconnecting'};
      assert.deepEqual(centralServiceStatus({token, pollHealth, online}), {...expected, recoveryKey: null});
    }
  }
});

test('managed recovery preserves priority over every transport observation', () => {
  for (const [managedRecovery, key] of [
    ['MANAGED_RECOVERING', 'managed_service_recovering'],
    ['MANAGED_RECOVERY_IDENTITY_CHANGED', 'managed_service_identity'],
    ['MANAGED_RUNTIME_START_TIMEOUT', 'managed_service_retry'],
  ]) for (const token of [null, 'credential']) for (const pollHealth of ['initial', 'connected', 'reconnecting']) {
    const result = centralServiceStatus({token, pollHealth, online: true, managedRecovery});
    assert.equal(result.key, key);
    assert.equal(result.recoveryKey, key);
    assert.equal(result.tone, 'neutral');
  }
});
