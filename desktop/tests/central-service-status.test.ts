import {test} from 'node:test';
import assert from 'node:assert/strict';
import {centralServiceStatus} from '../src/central-service-status.js';
import {STRINGS} from '../src/i18n.js';
import * as projection from '../src/central-service-status.js';

import {failedPolls} from './poll-observation-fixture.ts';

for (const [status, code, kind, key] of failedPolls) test(`poll observation retains ${status}/${code}`, () => {
  assert.equal(typeof (projection as any).pollFailure, 'function');
  const pollHealth = (projection as any).pollFailure({status, code}, 1234);
  assert.deepEqual(pollHealth, {kind, status, code, at: 1234});
  assert.equal(centralServiceStatus({token: 'credential', online: false, pollHealth}).key, key);
  assert.equal(centralServiceStatus({token: null, online: false, pollHealth}).key, 'ui_not_connected');
  assert.equal(centralServiceStatus({token: 'credential', online: false, pollHealth,
    managedRecovery: 'MANAGED_RECOVERING'}).key, 'managed_service_recovering');
});

test('pending reachability says Connecting in both locales', () => {
  const {key} = centralServiceStatus({token: 'credential', pollHealth: 'initial', online: false});
  assert.equal(STRINGS.en[key], 'Connecting');
  assert.equal(STRINGS['zh-TW'][key], '連線中');
});

test('credentials and authenticated bootstrap/poll observations settle central status', () => {
  for (const token of [null, '']) for (const pollHealth of ['initial', 'connected', 'reconnecting']) {
    for (const online of [false, true]) {
      assert.deepEqual(centralServiceStatus({token, pollHealth, online}),
        {key: 'ui_not_connected', tone: 'neutral', connection: 'offline', recoveryKey: null});
    }
  }
  for (const [pollHealth, online, key, tone, connection] of [
    ['initial', false, 'ui_connecting', 'neutral', 'reconnecting'],
    ['initial', true, 'ui_connected', 'ok', 'connected'],
    ['connected', false, 'ui_connected', 'ok', 'connected'],
    ['connected', true, 'ui_connected', 'ok', 'connected'],
    ['reconnecting', false, 'ui_reconnecting', 'warn', 'reconnecting'],
    ['reconnecting', true, 'ui_reconnecting', 'warn', 'reconnecting'],
  ]) {
    assert.deepEqual(centralServiceStatus({token: 'credential', pollHealth, online}),
      {key, tone, connection, recoveryKey: null});
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
