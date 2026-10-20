import assert from 'node:assert/strict';
import {test} from 'node:test';
import {fleetDiagnosis, fleetStatus} from '../src/fleet-status.js';
import {STRINGS} from '../src/i18n.js';
import {readFileSync} from 'node:fs';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));
const diagnose = (snapshot: any, readable = true, row = snapshot.readiness.hosts[0], kind = 'host') =>
  fleetDiagnosis(row, {snapshot, readable, name: row?.name || 'node-1', kind});

test('the shared status fixture is the safe native IPC output, including Connector layers', () => {
  assert.equal(fixture.configuration.issue_count, 0);
  assert.equal(Object.hasOwn(fixture.configuration, 'issues'), false);
  assert.deepEqual(fixture.readiness.connector.layers,
    {tunnel: true, tls: false, bat: false, workspace: false, auth: 'ok'});
});

test('effective row selection includes launch prerequisites and excludes an unconfigured connector', () => {
  const doc = structuredClone(fixture);
  // launch_plan adds hosts for selected BAT windows and the Dashboard without
  // changing the saved connections list (selection.rs, selection_io.rs).
  doc.selection.connections = [];
  assert.equal(diagnose(doc).state, 'ready');
  assert.equal(diagnose(doc, true, doc.readiness.connector, 'connector').state, 'ready');
  const off = {...doc.readiness.connector, selected: false, level: 'off', observed_at: null,
    layers: {tunnel: false, tls: false, bat: false, workspace: false}};
  assert.equal(diagnose(doc, true, off, 'connector').state, 'off');
});

test('configuration failures belong only to the affected row', () => {
  const doc = structuredClone(fixture);
  doc.configuration.valid = false;
  doc.configuration.issue_count = 1;
  Object.assign(doc.readiness.hosts[1], {level: 'down', code: 'PROFILE_DRIFT', blocking: 'inventory',
    layers: {tunnel: false, tls: false, bat: false, workspace: false}});
  assert.equal(diagnose(doc).state, 'ready');
  assert.equal(diagnose(doc, true, doc.readiness.connector, 'connector').state, 'ready');
  assert.equal(diagnose(doc, true, doc.readiness.hosts[1]).state, 'configuration');
});

test('tunnel ownership and local PC failures offer distinct next steps in both locales', () => {
  for (const [code, state] of Object.entries({TUNNEL_START_UNSETTLED: 'ownership',
    TUNNEL_RECORD_CHANGED: 'ownership', OTHER_LOGIN_OWNER: 'ownership',
    ENDPOINT_IN_USE: 'local_endpoint', SSH_UNAVAILABLE: 'local_ssh'})) {
    const row = {...fixture.readiness.hosts[0], level: 'down', code, blocking: 'tunnel',
      layers: {tunnel: false, tls: false, bat: false, workspace: false}};
    assert.equal(diagnose(fixture, true, row).state, state, code);
    for (const locale of ['en', 'zh-TW']) {
      assert.ok(STRINGS[locale]['fleet_check_title_' + state]);
      assert.ok(STRINGS[locale]['fleet_check_help_' + state]);
    }
  }
});

test('starting and pending selection wait for the monitor while an unconfirmed stop remains visible', () => {
  const starting = structuredClone(fixture);
  starting.readiness = {state: 'unavailable', observed_at: null, hosts: [], connector: null};
  starting.selection.applied_revision = null;
  assert.equal(diagnose(starting).state, 'checking');
  const pending = structuredClone(fixture);
  pending.selection.applied_revision = 'e'.repeat(64);
  pending.readiness.state = 'stale';
  Object.assign(pending.readiness.hosts[0], {level: 'degraded', code: 'PROBE_PENDING', blocking: 'bat',
    observed_at: null, layers: {tunnel: false, tls: false, bat: false, workspace: false}});
  assert.equal(diagnose(pending).state, 'selection_pending');
  Object.assign(pending.readiness.hosts[0], {level: 'down', code: 'STOP_UNCONFIRMED', blocking: 'tunnel'});
  assert.equal(diagnose(pending).state, 'ownership');
  for (const locale of ['en', 'zh-TW']) {
    assert.ok(STRINGS[locale].fleet_check_title_selection_pending);
    assert.ok(STRINGS[locale].fleet_check_help_selection_pending);
  }
});

test('protocol and contract failures agree with their blocked step and available identity evidence', () => {
  const protocol = {...fixture.readiness.hosts[0], level: 'down', code: 'BAT_PROTOCOL_INVALID', blocking: 'auth',
    layers: {tunnel: true, tls: true, auth: 'unknown', bat: false, workspace: false}};
  const result = diagnose(fixture, true, protocol);
  assert.equal(result.state, 'service');
  assert.deepEqual(result.steps.map(step => step.state), ['passed', 'unchecked', 'attention', 'unchecked']);
  const contract = {...fixture.readiness.connector, level: 'degraded', code: 'CONNECTOR_CONTRACT_UNSUPPORTED', blocking: 'contract',
    layers: {tunnel: true, tls: false, auth: 'unknown', bat: false, workspace: false}};
  const connector = diagnose(fixture, true, contract, 'connector');
  assert.equal(connector.state, 'version');
  assert.deepEqual(connector.steps.map(step => step.state), ['passed', 'unchecked', 'attention']);
});

test('cached success is withdrawn on failed reads, stale evidence, stopped monitors and changed selection', () => {
  assert.equal(diagnose(fixture).state, 'ready');
  assert.equal(diagnose(fixture, false).state, 'unavailable');
  for (const [change, state] of [
    [(doc: any) => {doc.readiness.state = 'stale';
      for (const row of [...doc.readiness.hosts, doc.readiness.connector]) Object.assign(row, {
        level: 'degraded', stale: true, code: 'PROBE_STALE', blocking: 'stale',
        layers: {tunnel: false, tls: false, bat: false, workspace: false}});}, 'stale'],
    [(doc: any) => {Object.assign(doc.readiness.hosts[0], {level: 'degraded', stale: true,
      code: 'PROBE_STALE', blocking: 'stale', layers: {tunnel: false, tls: false, bat: false, workspace: false}});}, 'stale'],
    [(doc: any) => {doc.monitor = {state: 'stopped', epoch: null, controllable: false};
      doc.selection.applied_revision = null;
      doc.readiness = {state: 'unavailable', observed_at: null, hosts: [], connector: null};}, 'stopped'],
    [(doc: any) => {doc.selection.applied_revision = 'e'.repeat(64); doc.readiness.state = 'stale';}, 'selection_pending'],
    [(doc: any) => {Object.assign(doc.readiness.hosts[0], {selected: false, level: 'off', observed_at: null,
      layers: {tunnel: false, tls: false, bat: false, workspace: false}});}, 'off'],
  ] as const) {
    const doc = structuredClone(fixture); change(doc);
    const result = diagnose(doc);
    assert.equal(result.state, state);
    assert.ok(result.steps.every(step => step.state === 'unchecked'));
  }
});

test('identity failures leave downstream service and workspace unchecked', () => {
  const doc = structuredClone(fixture);
  Object.assign(doc.readiness.hosts[0], {level: 'down', code: 'BAT_AUTH_REFUSED', blocking: 'auth',
    layers: {tunnel: true, tls: true, auth: 'refused', bat: false, workspace: false}});
  const result = diagnose(doc);
  assert.equal(result.state, 'identity');
  assert.deepEqual(result.steps.map(step => step.state), ['passed', 'attention', 'unchecked', 'unchecked']);
});

test('BAT requires complete evidence; Connector never claims a BAT workspace or execution capability', () => {
  for (const field of ['tunnel', 'tls', 'auth', 'bat', 'workspace']) {
    const doc = structuredClone(fixture);
    delete doc.readiness.hosts[0].layers[field];
    assert.equal(diagnose(doc).state, 'checking');
  }
  const result = diagnose(fixture, true, fixture.readiness.connector, 'connector');
  assert.equal(result.state, 'ready');
  assert.deepEqual(result.steps.map(step => step.id), ['network', 'identity', 'service']);
  assert.equal(result.steps[1].state, 'passed');
});

test('errors have bilingual next steps, with raw diagnostic codes confined to details', () => {
  const examples = {TAILSCALE_WAKING: 'waking', ROUTE_UNAVAILABLE: 'network', TLS_IDENTITY_MISMATCH: 'trust',
    BAT_AUTH_REFUSED: 'identity', CONNECTOR_SCOPE_MISSING: 'identity', BAT_VERSION_UNSUPPORTED: 'version',
    BAT_PROTOCOL_INVALID: 'service', WORKSPACE_BINDING_MISMATCH: 'workspace', INVENTORY_INVALID: 'configuration',
    OWNER_UNPROVEN: 'ownership', STOP_UNCONFIRMED: 'ownership', PROBE_TIMEOUT: 'timeout', PROBE_PENDING: 'checking',
    PROBE_STALE: 'stale', NEW_UNKNOWN_CODE: 'unknown', constructor: 'unknown'};
  for (const locale of ['en', 'zh-TW']) for (const [code, expected] of Object.entries(examples)) {
    const row = {...fixture.readiness.hosts[0], code, level: code === 'PROBE_PENDING' || code === 'TAILSCALE_WAKING' || code === 'PROBE_STALE' || code === 'PROBE_TIMEOUT' ? 'degraded' : 'down',
      blocking: null, stale: code === 'PROBE_STALE',
      layers: {tunnel: false, tls: false, bat: false, workspace: false}};
    assert.equal(diagnose(fixture, true, row).state, expected);
    const t = (key: string) => {assert.ok(STRINGS[locale][key], key); return STRINGS[locale][key];};
    const h = (tag: string, attrs: unknown, ...children: unknown[]) => ({tag, attrs, children});
    const view = fleetStatus(row, {h, t, snapshot: fixture, readable: true, entry: fixture.configuration.connections[0], detailsState: new Map()});
    const text = JSON.stringify(view.children.slice(0, 2));
    assert.ok(!text.includes(code));
    assert.equal(/[\u3400-\u9fff]/u.test(text), locale === 'zh-TW');
    assert.ok(JSON.stringify(view.children[2]).includes(code));
    if (code === 'ROUTE_UNAVAILABLE') assert.ok(!text.includes('Tailscale'));
  }
});
