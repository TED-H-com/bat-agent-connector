import assert from 'node:assert/strict';
import {test} from 'node:test';
import {fleetDiagnosis, fleetStatus} from '../src/fleet-status.js';
import {STRINGS} from '../src/i18n.js';
import {readFileSync} from 'node:fs';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.json', import.meta.url), 'utf8')).result;
const diagnose = (snapshot: any, readable = true, row = snapshot.readiness.hosts[0], kind = 'host') =>
  fleetDiagnosis(row, {snapshot, readable, name: row?.name || 'node-1', kind});

test('cached success is withdrawn on failed reads, stale evidence, stopped monitors and changed selection', () => {
  assert.equal(diagnose(fixture).state, 'ready');
  assert.equal(diagnose(fixture, false).state, 'unavailable');
  for (const [change, state] of [
    [(doc: any) => {doc.readiness.state = 'stale';}, 'stale'],
    [(doc: any) => {doc.readiness.hosts[0].stale = true;}, 'stale'],
    [(doc: any) => {doc.monitor.state = 'stopped';}, 'stopped'],
    [(doc: any) => {doc.selection.applied_revision = 'old';}, 'checking'],
    [(doc: any) => {doc.readiness.hosts[0].selected = false;}, 'checking'],
    [(doc: any) => {doc.selection.connections = [];}, 'off'],
  ] as const) {
    const doc = structuredClone(fixture); change(doc);
    const result = diagnose(doc);
    assert.equal(result.state, state);
    assert.ok(result.steps.every(step => step.state === 'unchecked'));
  }
  assert.equal(diagnose(fixture, true, null).state, 'checking');
});

test('identity failures leave downstream service and workspace unchecked even with contradictory flags', () => {
  const doc = structuredClone(fixture);
  Object.assign(doc.readiness.hosts[0], {code: 'BAT_AUTH_REFUSED', blocking: 'auth'});
  const result = diagnose(doc);
  assert.equal(result.state, 'identity');
  assert.deepEqual(result.steps.map(step => step.state), ['passed', 'attention', 'unchecked', 'unchecked']);
  doc.readiness.hosts[0].blocking = null;
  assert.deepEqual(diagnose(doc).steps.map(step => step.state), ['passed', 'attention', 'unchecked', 'unchecked']);
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
  assert.equal(result.steps[1].state, 'unchecked'); // Legacy connector has no auth layer.
});

test('errors have bilingual next steps, with raw diagnostic codes confined to details', () => {
  const examples = {TAILSCALE_WAKING: 'waking', ROUTE_UNAVAILABLE: 'network', TLS_IDENTITY_MISMATCH: 'trust',
    BAT_AUTH_REFUSED: 'identity', CONNECTOR_SCOPE_MISSING: 'identity', BAT_VERSION_UNSUPPORTED: 'version',
    BAT_PROTOCOL_INVALID: 'service', WORKSPACE_BINDING_MISMATCH: 'workspace', INVENTORY_INVALID: 'configuration',
    OWNER_UNPROVEN: 'ownership', STOP_UNCONFIRMED: 'ownership', PROBE_TIMEOUT: 'timeout', PROBE_PENDING: 'checking',
    PROBE_STALE: 'stale', NEW_UNKNOWN_CODE: 'unknown', constructor: 'unknown'};
  for (const locale of ['en', 'zh-TW']) for (const [code, expected] of Object.entries(examples)) {
    const row = {...fixture.readiness.hosts[0], code};
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
