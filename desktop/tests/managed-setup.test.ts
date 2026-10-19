import {test} from 'node:test';
import assert from 'node:assert/strict';
import {normalizeSetupFingerprint, parseBatConnectionUrl, validSetupEndpoint, validSetupHostId} from '../src/managed-setup.js';

const fingerprint = 'ab'.repeat(32);
for (const prefix of ['wss://', 'ws://', '']) test(`BAT URL grammar ${prefix || 'scheme-less'} and default port`, () => {
  assert.deepEqual(parseBatConnectionUrl(`${prefix}localhost?token=synthetic-secret&fp=SHA256:${fingerprint}`), {
    endpoint: 'wss://localhost:9876', fingerprint: fingerprint.toUpperCase(), token: 'synthetic-secret', loopback: true});
  assert.equal(parseBatConnectionUrl(`${prefix}demo.example:9999?token=synthetic-secret&fp=${fingerprint}`)?.endpoint, 'wss://demo.example:9999');
});
test('IPv6 loopback is a name suggestion, and URL parsing rejects malformed inputs', () => {
  assert.equal(parseBatConnectionUrl(`ws://[::1]:9876?token=synthetic-secret&fp=${fingerprint}`)?.loopback, true);
  for (const value of ['', 'bad', `https://localhost?token=x&fp=${fingerprint}`, `wss://localhost?fp=${fingerprint}`,
    'wss://localhost?token=synthetic-secret&fp=wrong', `wss://user:pass@localhost?token=x&fp=${fingerprint}`, `wss://localhost:0?token=x&fp=${fingerprint}`])
    assert.equal(parseBatConnectionUrl(value), null);
});
test('host IDs and manually entered origins match central admission rules', () => {
  for (const id of ['local', 'A_1.-', 'a'.repeat(64)]) assert.equal(validSetupHostId(id), true);
  for (const id of ['本機', '-local', '.local', '', 'a'.repeat(65), 'local\n']) assert.equal(validSetupHostId(id), false);
  for (const value of ['wss://localhost:9876', 'wss://[::1]:9876/', 'wss://demo.example:9876/']) assert.equal(validSetupEndpoint(value), true);
  for (const value of ['ws://localhost:9876', 'wss://localhost', 'wss://localhost:0', 'wss://localhost:9876/a',
    'wss://localhost:9876?token=secret', 'wss://user:pass@localhost:9876', 'wss://localhost:9876#frag', 'wss://localhost:9876/../',
    'wss://localhost:9876\n']) assert.equal(validSetupEndpoint(value), false);
});
test('fingerprints normalize optional prefix, spaces and colons without accepting partial values', () => {
  assert.equal(normalizeSetupFingerprint(` sha256:${Array(32).fill('ab').join(': ')} `), fingerprint.toUpperCase());
  assert.equal(normalizeSetupFingerprint('a'.repeat(63)), null);
  assert.equal(normalizeSetupFingerprint('g'.repeat(64)), null);
});
test('explicit protocol-default BAT ports survive URL normalization', () => {
  assert.equal(parseBatConnectionUrl(`wss://localhost:443?token=synthetic-secret&fp=${fingerprint}`)?.endpoint, 'wss://localhost:443');
  assert.equal(parseBatConnectionUrl(`ws://localhost:80?token=synthetic-secret&fp=${fingerprint}`)?.endpoint, 'wss://localhost:80');
  assert.equal(validSetupEndpoint('wss://localhost:443/'), true);
});

test('an absent legacy socket is unknown reachability, while explicit observation states are preserved', async () => {
  const {setupConnectionStatus} = await import('../src/managed-setup.js');
  assert.equal(setupConnectionStatus({connected: false}), 'checking');
  assert.equal(setupConnectionStatus({}), 'checking');
  assert.equal(setupConnectionStatus({connected: true}), 'connected');
  for (const status of ['connected', 'checking', 'unreachable'])
    assert.equal(setupConnectionStatus({connected: false, connection_status: status}), status);
});
