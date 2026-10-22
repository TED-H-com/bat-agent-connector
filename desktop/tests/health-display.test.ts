import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import * as health from '../src/health-display.js';
import {pollFailure} from '../src/central-service-status.js';
import {failedPolls} from './poll-observation-fixture.ts';
import {runtimeGolden} from './session-runtime-fixture.ts';
import {STRINGS} from '../src/i18n.js';

const bootstrap = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));
const fleet = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));
const at = Date.parse(fleet.readiness.observed_at);
const facts = () => ({token: 'fixture', online: true, pollHealth: 'connected', namespace: 'fixture',
  lastPoll: {at, namespace: 'fixture'}, caps: bootstrap.capabilities,
  health: {bootstrap: 1, capabilities: {data: bootstrap.capabilities, bootstrap: 1}, sessions: [], hosts: []}});

test('a successful poll expires at sixty seconds, retaining its value and age', () => {
  const input = facts();
  assert.equal(health.healthDisplay(input, at + 59_999).connection.freshness, 'current');
  const result = health.healthDisplay(input, at + 60_000);
  assert.equal(result.connection.freshness, 'stale');
  assert.equal(result.connection.key, 'ui_connected');
  assert.equal(result.connection.ageSeconds, 60);
  assert.equal(result.connection.connection, 'connected');
});

test('producer validity takes precedence, including an earlier expiry', () => {
  for (const seconds of [10, 120]) {
    const fact = {value: 'connected', observed_at: new Date(at).toISOString(), valid_until: new Date(at + seconds * 1000).toISOString()};
    assert.equal(health.displayFact(fact, {now: at + seconds * 1000 - 1}).freshness, 'current');
    assert.equal(health.displayFact(fact, {now: at + seconds * 1000}).freshness, 'stale');
  }
});

test('bootstrap and version/build changes invalidate static facts without a time expiry', () => {
  const input = facts();
  assert.equal(health.healthDisplay(input, at + 999_000).capabilities.freshness, 'current');
  for (const changed of [
    {...input, caps: {...input.caps, connector: '0.2.13'}},
    {...input, caps: {...input.caps, build: {...input.caps.build, commit: 'a'.repeat(40)}}},
    {...input, health: {...input.health, bootstrap: 2}},
  ]) assert.equal(health.healthDisplay(changed, at).capabilities.freshness, 'invalidated');
});

test('other Fleet machines cannot change the selected installation summary', () => {
  const input = facts();
  const before = health.healthDisplay(input, at);
  const snapshot = structuredClone(fleet);
  for (const row of snapshot.readiness.hosts.slice(0, 2)) Object.assign(row, {level: 'down', blocking: 'auth', code: 'BAT_AUTH_REFUSED', layers: {tunnel: true, tls: true, auth: 'refused', bat: false, workspace: false}});
  const after = health.healthDisplay({...input, health: {...input.health, fleet: snapshot}}, at);
  assert.deepEqual(after.connection, before.connection);
  assert.deepEqual(after.summary, before.summary);
  assert.equal(after.otherAttention, 2);
  assert.equal(health.healthDisplay({...input, health: {...input.health, fleet: snapshot}}, at + 60_000).otherAttention, 2);
});

test('compact precedence preserves attention, staleness and output as separate facets', () => {
  for (const [name, expected] of [
    ['streaming_pending_permission', 'attention'], ['stale_pending', 'attention'],
    ['managed_blocked_runtime', 'attention'],
    ['stale_activity_evidence', 'stale'], ['unknown_activity', 'unknown'],
  ]) {
    const session = runtimeGolden[name].listed;
    const now = Date.parse(session.observed_at);
    const input = facts();
    const result = health.healthDisplay({...input, health: {...input.health,
      selected: {host: session.host, sessionId: session.session_id}, sessions: [session]}}, now);
    assert.equal(result.summary.compact, expected);
    if (name === 'streaming_pending_permission') assert.equal(result.summary.activity, 'working');
    if (name === 'stale_pending') assert.equal(result.summary.stale, true);
  }
  assert.equal(health.compactHealth({attention: true, stale: true, activity: 'working'}), 'attention');
  assert.equal(health.compactHealth({attention: false, stale: true, activity: 'working'}), 'stale');
  assert.equal(health.compactHealth({attention: false, stale: false, activity: 'unknown'}), 'unknown');
  assert.equal(health.compactHealth({attention: false, stale: false, activity: 'working'}), 'working');
  assert.equal(health.compactHealth({attention: false, stale: false, activity: 'idle'}), 'idle');
});

test('real poll failures retain their cause when they age; neither locale calls them offline', () => {
  for (const [status, code, , key] of failedPolls) {
    const input = {...facts(), online: false, pollHealth: pollFailure({status, code}, at)};
    const result = health.healthDisplay(input, at + 60_000);
    assert.equal(result.connection.key, key);
    assert.equal(result.connection.freshness, 'stale');
    for (const locale of ['en', 'zh-TW']) {
      const t = (key: string, args: any = {}) => Object.entries(args).reduce((s, [k, v]) => s.replaceAll(`{${k}}`, String(v)), STRINGS[locale][key]);
      const label = health.healthLabel(result.connection, t);
      assert.match(label, /60/);
      assert.ok(label.includes(STRINGS[locale].health_checking));
      assert.doesNotMatch(label, /offline|down|離線|停機/i);
      assert.ok(t('health_other_machines', {count: 2}).includes('2'));
    }
  }
});

test('unknown, refused, unconfigured and unsupported facts keep their original values', () => {
  for (const value of ['unknown', 'refused', 'unconfigured', 'unsupported']) {
    const result = health.displayFact({value}, {now: at});
    assert.equal(result.value, value);
    assert.equal(result.freshness, 'unknown');
  }
});

test('an unknown inventory axis does not borrow a newer session timestamp', () => {
  const session = runtimeGolden.unknown_activity.listed;
  const input = facts();
  const result = health.healthDisplay({...input, health: {...input.health,
    selected: {host: session.host, sessionId: session.session_id}, sessions: [session]}}, Date.parse(session.observed_at));
  assert.equal(result.summary.facts[0].activity.freshness, 'unknown');
});

test('unknown evidence takes compact precedence over working without hiding activity', () => {
  assert.equal(health.compactHealth({attention: false, stale: false, unknown: true, activity: 'working'}), 'unknown');
});

test('selected inventory connection failure remains separate from output activity', () => {
  const session = structuredClone(runtimeGolden.unknown_activity.listed);
  // Inventory._state emits not_connected for an observed reachable=false host.
  session.state.connection = 'not_connected';
  const input = facts();
  const result = health.healthDisplay({...input, health: {...input.health,
    selected: {host: session.host, sessionId: session.session_id}, sessions: [session]}}, Date.parse(session.observed_at));
  assert.equal(result.summary.compact, 'attention');
  assert.equal(result.summary.activity, 'unknown');
  assert.equal(result.summary.connection.value, 'not_connected');
});
