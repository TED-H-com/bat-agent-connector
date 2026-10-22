import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fleetStatus, fleetDiagnosis, fleetHealth} from '../src/fleet-status.js';
import {profileStateFacts} from '../src/pm-work-profile-state.js';
import {healthDisplay, healthLabel} from '../src/health-display.js';
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
  const labels = locale === 'en' ? {
    expired: 'Connected · 1 min ago · Checking', unknown: 'unknown · Checking',
    values: {ready: 'Ready', missing: 'Setup still missing', unknown: 'Not verified yet', unavailable: 'Unavailable'},
  } : {
    expired: '已連線 · 1 分鐘前 · 檢查中', unknown: '未知 · 檢查中',
    values: {ready: '已就緒', missing: '還缺設定', unknown: '尚未驗證', unavailable: '不可用'},
  };
  test(`Fleet and Connector use the same last connected value and age at expiry ${locale}`, () => {
    const now = at + 60_000, row = fleet.readiness.hosts[0];
    const header = healthDisplay(input(), now).connection;
    const view = render(row, t, now);
    assert.equal(view.children[0].children[0], labels.expired);
    assert.equal(healthLabel(header, t), labels.expired);
    assert.equal(view.attrs['data-fleet-state'], 'stale');
    assert.equal(view.attrs['data-freshness'], 'stale');
    assert.equal(header.freshness, 'stale');
  });
  test(`Fleet pending evidence uses the shared unknown wording ${locale}`, () => {
    // The monitor's pending row has no observation, unlike an HTTP receipt.
    const row = {...fleet.readiness.hosts[0], level: 'degraded', code: 'PROBE_PENDING', blocking: 'bat',
      observed_at: null, layers: {tunnel: false, tls: false, bat: false, workspace: false}};
    const view = render(row, t, at);
    assert.equal(view.children[0].children[0], labels.unknown);
    assert.doesNotMatch(view.children[0].children[0], /offline|down|離線|停機/i);
  });
  test(`Work producer has no per-capability validity; reported values and reasons remain unchanged ${locale}`, () => {
    for (const body of Object.values(profiles.cases) as any[]) for (const state of Object.values(body.states) as any[]) {
      const facts = profileStateFacts(state);
      assert.equal(t(facts.valueKey), labels.values[state.value]);
      assert.equal(facts.reasonCode, state.reason);
      assert.equal(Object.hasOwn(state, 'valid_until'), false);
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
      const labels = locale === 'en' ? {
        ownership: 'The background connection outcome is unconfirmed · 1 min ago · Checking',
        version: 'The service version is incompatible · 1 min ago · Checking',
      } : {
        ownership: '背景連線的操作結果尚未確認 · 1 分鐘前 · 檢查中',
        version: '服務版本不相容 · 1 分鐘前 · 檢查中',
      };
      assert.equal(healthLabel(result.fact, t), labels[state]);
    }
  }
});

test('Work retains its reported value and past verification while per-capability validity is absent', () => {
  const body = profiles.cases.owned_execution, state = body.states.can_observe;
  const facts = profileStateFacts(state);
  assert.equal(facts.valueKey, 'wpf_ready');
  assert.equal(facts.lastSuccessAt, null); // The existing past section already shows this instant.
  assert.equal(facts.past.at, state.verified.observed_at);
  assert.equal(Object.hasOwn(state, 'observed_at'), false);
  assert.equal(Object.hasOwn(state, 'valid_until'), false);
});

test('Fleet expiry is per row, with no cross-host or cross-domain inference', () => {
  const snapshot = structuredClone(fleet);
  snapshot.readiness.hosts[1].observed_at = new Date(at + 30_000).toISOString();
  const options = {snapshot, readable: true, now: at + 60_000};
  assert.equal(fleetHealth(snapshot.readiness.hosts[0], options).state, 'stale');
  assert.equal(fleetHealth(snapshot.readiness.hosts[1], options).state, 'ready');
  assert.equal(healthDisplay({...input(), lastPoll: {at: at + 60_000, namespace: 'fixture'}}, at + 60_000).connection.freshness, 'current');
});
