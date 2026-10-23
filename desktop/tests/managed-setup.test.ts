import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
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

test('legacy and unsupported observations cannot establish readiness', async () => {
  const {setupConnectionFacts} = await import('../src/managed-setup.js');
  const unknown = host => assert.equal(setupConnectionFacts(host).connection.value, 'unknown');
  unknown({connected:false}); unknown({}); unknown({connected:true});
  for (const status of ['connected', 'checking', 'unreachable'])
    unknown({connected:true,connection_status:status});
  unknown({connected:true,connection_observation:{version:2}});
});

test('setup status separates the original operation from each current host observation', async () => {
  const {setupStatusLine} = await import('../src/managed-setup.js');
  const {operation, states} = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-setup-evidence.json', import.meta.url), 'utf8'));
  const status = (hosts: any[], op: any = operation, extra = {}) => setupStatusLine({hosts, operation: op, readReady: true, now:1800000600000, ...extra});
  const past = {key: 'setup_verified_saved', verifiedAt: operation.updated_at};
  for (const condition of ['unreachable', 'checking']) {
    assert.deepEqual(status(states[condition].hosts), past);
    assert.deepEqual(status([...states[condition].hosts, {...states.connected.hosts[0], name: 'other'}]), past);
  }
  assert.deepEqual(status(states.connected.hosts), {key: 'setup_ready'});
  assert.deepEqual(status([{...states.connected.hosts[0], name: 'other'}]), past);
  assert.deepEqual(status([]), past);
  assert.deepEqual(status(states.connected.hosts, operation, {readReady: false}), past);
  assert.deepEqual(status(states.connected.hosts, operation, {busy: true}), {key: 'setup_checking'});
  assert.deepEqual(status(states.connected.hosts, null), {key: 'setup_ready'});
  assert.deepEqual(status([...states.checking.hosts, {...states.connected.hosts[0], name: 'other'}], null), {key: ''});
  assert.deepEqual(status(states.checking.hosts, {...operation, status: 'running'}), {key: 'setup_saving'});
  assert.deepEqual(status(states.checking.hosts, {...operation, status: 'failed'}), {key: 'setup_failed_operation'});
  assert.deepEqual(status([], null), {key: 'setup_waiting'});
  assert.deepEqual(status([], null, {readReady: false}), {key: 'setup_loading'});
});

test('all producer facets use the shared clock and Settings agrees with Work', async () => {
  const {setupConnectionFacts, setupStatusLine} = await import('../src/managed-setup.js');
  const {profileStateFacts} = await import('../src/pm-work-profile-state.js');
  const {healthLabel} = await import('../src/health-display.js');
  const {t,setLocale} = await import('../src/i18n.js');
  const golden = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-setup-evidence.json', import.meta.url),'utf8'));
  for(const locale of ['en','zh-TW']) {
    setLocale(locale);
    for(const [name,body] of Object.entries(golden.observations) as [string,any][]) {
      const now=1800000780000;
      const facts=setupConnectionFacts(body.setup.hosts[0],{now});
      assert.equal(Object.keys(facts).length,4);
      const work=profileStateFacts(body.work,{now});
      if(work.fact)assert.equal(healthLabel(facts.connection,t),healthLabel(work.fact,t),name);
      if(name==='active_writer')assert.notEqual(setupStatusLine({hosts:body.setup.hosts,readReady:true,now}).key,'setup_ready');
    }
    const host=golden.observations.connected.setup.hosts[0];
    const at=Date.parse(host.connection_observation.facets.connection.valid_until);
    assert.equal(setupConnectionFacts(host,{now:at-1}).connection.freshness,'current');
    assert.equal(setupConnectionFacts(host,{now:at}).connection.freshness,'stale');
    assert.notEqual(setupStatusLine({hosts:[host],readReady:true,now:at}).key,'setup_ready');
  }
});
