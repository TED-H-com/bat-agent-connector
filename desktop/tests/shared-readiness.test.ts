import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fleetStatus, fleetDiagnosis, fleetHealth} from '../src/fleet-status.js';
import {profileStateFacts} from '../src/pm-work-profile-state.js';
import {displayFact, healthDisplay, healthLabel} from '../src/health-display.js';
import {pollFailure} from '../src/central-service-status.js';
import {runtimeGolden} from './session-runtime-fixture.ts';
import {STRINGS} from '../src/i18n.js';

const fleet = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));
const bootstrap = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));
const profiles = JSON.parse(readFileSync(new URL('../../tests/fixtures/work-profile-readiness.json', import.meta.url), 'utf8'));
const at = Date.parse(fleet.readiness.observed_at);
const h = (tag, attrs, ...children) => ({tag, attrs, children});
const translate = locale => (key, args = {}) => Object.entries(args).reduce((s, [k, v]) => s.replaceAll(`{${k}}`, String(v)), STRINGS[locale][key]);
const input = () => ({token: 'fixture', online: true, pollHealth: 'connected', namespace: 'fixture',
  lastPoll: {at, namespace: 'fixture'}, caps: bootstrap.capabilities, health: {fleet}});
const render = (row, t, now) => fleetStatus(row, {h, t, now, snapshot: fleet, readable: true,
  entry: fleet.configuration.connections.find(entry => entry.name === row.name), detailsState: new Map()});

for (const locale of ['en', 'zh-TW']) {
  const t = translate(locale);
  test(`Fleet and Connector use the same last connected value and age at expiry ${locale}`, () => {
    const now = at + 60_000, row = fleet.readiness.hosts[0];
    const header = healthDisplay(input(), now).connection;
    const view = render(row, t, now);
    assert.equal(view.children[0].children[0], healthLabel(header, t));
    assert.equal(view.attrs['data-fleet-state'], 'stale');
    assert.equal(view.attrs['data-freshness'], header.freshness);
  });
  test(`Fleet pending evidence uses the shared unknown wording ${locale}`, () => {
    // The monitor's pending row has no observation, unlike an HTTP receipt.
    const row = {...fleet.readiness.hosts[0], level: 'degraded', code: 'PROBE_PENDING', blocking: 'bat',
      observed_at: null, layers: {tunnel: false, tls: false, bat: false, workspace: false}};
    const view = render(row, t, at);
    assert.equal(view.children[0].children[0], healthLabel(displayFact({key: 'health_unknown'}, {now: at}), t));
    assert.doesNotMatch(view.children[0].children[0], /offline|down|離線|停機/i);
  });
  test(`Work producer stale and absent evidence use shared freshness without borrowing read time ${locale}`, () => {
    for (const name of ['never_observed', 'host_not_refreshed', 'action_unavailable']) {
      const body = profiles.cases[name], state = body.states.can_observe;
      const facts = profileStateFacts(state, {now: body.observed_at * 1000});
      assert.equal(facts.display?.freshness, name === 'host_not_refreshed' ? 'stale' : 'unknown');
      assert.equal(facts.display.ageSeconds, null);
      assert.equal(facts.display.key, facts.valueKey);
      assert.ok(healthLabel(facts.display, t).includes(t('health_checking')));
      assert.equal(facts.reasonCode, state.reason);
    }
  });
}

test('reachable Connector does not supply an unavailable Work capability or PM readiness', () => {
  const body = profiles.cases.action_unavailable;
  const facts = profileStateFacts(body.states.can_observe);
  assert.equal(healthDisplay(input(), at).connection.key, 'ui_connected');
  assert.equal(facts.valueKey, 'wpf_unavailable');
  assert.equal(facts.reasonCode, 'action_unavailable');
  assert.notEqual(body.states.can_start_own.value, 'ready');
  assert.equal(Object.hasOwn(facts, 'pm_ready'), false);
});

test('unauthenticated Connector stays refused alongside connected BAT in its own domain', () => {
  const row = runtimeGolden.ended_runtime.listed;
  const now = Date.parse(row.observed_at);
  const result = healthDisplay({...input(), online: false, pollHealth: pollFailure({status: 401, code: 'UNAUTHORIZED'}, now),
    health: {sessions: [row], selected: {host: row.host, sessionId: row.session_id}, fleet}}, now);
  assert.equal(result.connection.key, 'ui_connection_refused');
  assert.equal(result.summary.connection.value, 'connected');
  assert.equal(result.otherAttention, 0); // Never join the selected host to native aliases.
  assert.equal(fleetDiagnosis(fleet.readiness.hosts[0], {snapshot: fleet, readable: true}).state, 'ready');
});

test('a broken Fleet host leaves the other host and central Connector healthy', () => {
  const snapshot = structuredClone(fleet);
  Object.assign(snapshot.readiness.hosts[1], {level: 'down', code: 'BAT_AUTH_REFUSED', blocking: 'auth',
    layers: {tunnel: true, tls: true, auth: 'refused', bat: false, workspace: false}});
  assert.equal(fleetDiagnosis(snapshot.readiness.hosts[1], {snapshot, readable: true}).state, 'identity');
  assert.equal(fleetDiagnosis(snapshot.readiness.hosts[0], {snapshot, readable: true}).state, 'ready');
  const result = healthDisplay({...input(), health: {fleet: snapshot}}, at);
  assert.equal(result.connection.key, 'ui_connected');
  assert.equal(result.otherAttention, 1);
});

test('unconfirmed stop and unsupported version reasons survive display expiry', () => {
  for (const [code, blocking, state] of [['STOP_UNCONFIRMED', 'tunnel', 'ownership'],
    ['BAT_VERSION_UNSUPPORTED', 'bat', 'version']]) {
    const layers = {tunnel: false, tls: false, bat: false, workspace: false};
    const row = {...fleet.readiness.hosts[0], code, blocking, level: 'down',
      layers: state === 'ownership' ? layers : {...layers, tunnel: true, tls: true, auth: 'ok', version: '3.0.0'}};
    const result = fleetHealth(row, {snapshot: fleet, readable: true, now: at + 60_000});
    assert.equal(result.state, state);
    assert.equal(result.fact.freshness, 'stale');
    assert.ok(result.steps.every(step => step.state === 'unchecked'));
    for (const locale of ['en', 'zh-TW']) {
      const t = translate(locale);
      assert.ok(healthLabel(result.fact, t).startsWith(t('fleet_check_title_' + state)));
    }
  }
});

test('Work host success without Inventory validity retains age but cannot borrow the live-reader timeout', () => {
  const body = profiles.cases.owned_execution, state = body.states.can_observe;
  for (const now of [body.observed_at * 1000, body.observed_at * 1000 + 600_000]) {
    const facts = profileStateFacts(state, {now});
    assert.equal(facts.display.freshness, 'unknown');
    assert.equal(facts.display.ageSeconds, (now - state.evidence_ref.last_success_at * 1000) / 1000);
    assert.equal(facts.valueKey, 'wpf_ready');
    assert.equal(facts.lastSuccessAt, null); // The existing past section already shows this instant.
    assert.equal(facts.past.at, state.verified.observed_at);
  }
});

test('Fleet expiry is per row, with no cross-host or cross-domain inference', () => {
  const snapshot = structuredClone(fleet);
  snapshot.readiness.hosts[1].observed_at = new Date(at + 30_000).toISOString();
  const options = {snapshot, readable: true, now: at + 60_000};
  assert.equal(fleetHealth(snapshot.readiness.hosts[0], options).state, 'stale');
  assert.equal(fleetHealth(snapshot.readiness.hosts[1], options).state, 'ready');
  assert.equal(healthDisplay({...input(), lastPoll: {at: at + 60_000, namespace: 'fixture'}}, at + 60_000).connection.freshness, 'current');
});
