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
  const [major, minor, patch] = input.caps.connector.split('.').map(Number);
  const changedVersion = `${major}.${minor}.${patch + 1}`;
  assert.notEqual(changedVersion, input.caps.connector);
  assert.equal(health.healthDisplay(input, at + 999_000).capabilities.freshness, 'current');
  for (const changed of [
    {...input, caps: {...input.caps, connector: changedVersion}},
    {...input, caps: {...input.caps, build: {...input.caps.build, commit: 'a'.repeat(40)}}},
    {...input, health: {...input.health, bootstrap: 2}},
  ]) assert.equal(health.healthDisplay(changed, at).capabilities.freshness, 'invalidated');
});

test('other central machines cannot change a nonempty selected workspace summary', () => {
  const input = workspaceFacts();
  const before = health.healthDisplay(input, at).summary;
  assert.equal(before?.compact, 'idle');
  health.rememberHealthRead(input.health, '/hosts', {hosts: [host('fixture-host'), host('other', false)]});
  const after = health.healthDisplay(input, at);
  assert.deepEqual(after.summary, before);
  assert.equal(after.otherAttention, 1);
  assert.equal(after.other.href, null); // native aliases cannot list central identities
  health.rememberHealthRead(input.health, '/hosts', {hosts: [host('fixture-host', false)]});
  assert.equal(health.healthDisplay(input, at).otherAttention, 0);
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
      assert.ok(label.includes(t('age_past', {age: t('age_minute', {n: 1})})));
      assert.ok(label.includes(STRINGS[locale].health_checking));
      assert.doesNotMatch(label, /offline|down|離線|停機/i);
      assert.ok(t('health_other_machines_many', {count: 2}).includes('2'));
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
  assert.equal(result.summary.attention, false);
  assert.equal(result.summary.activity, 'unknown');
  assert.equal(result.summary.connection.value, 'not_connected');
});

const iso = (ms: number) => new Date(ms).toISOString();
function session(id = 'fixture-session', stamp = at) {
  const row = structuredClone(runtimeGolden.ended_runtime.listed);
  Object.assign(row, {session_id: id, workspace_id: 'workspace-fixture', workspace: 'Fixture workspace', observed_at: iso(stamp)});
  for (const axis of ['activity', 'connection']) Object.assign(row.state.evidence[axis], {observed_at: iso(stamp), valid_until: iso(stamp + 180_000)});
  return row;
}
const host = (name: string, reachable = true) => ({host: name, reachable, last_attempt_at: iso(at), last_success_at: iso(at),
  valid_until: iso(at + 180_000), stale: !reachable, stale_reason: reachable ? null : 'host_unreachable'});
function workspaceFacts() {
  const input: any = facts();
  input.health.selected = {host: 'fixture-host', sessionId: 'fixture-session'};
  health.rememberHealthRead(input.health, '/sessions?order=id&include_gone=true&limit=50', {sessions: [session()], next_cursor: null});
  return input;
}

test('production list page chains establish completeness only from first to last page', () => {
  const input: any = facts(); input.health.selected = {host: 'fixture-host', sessionId: 'fixture-session'};
  const path = '/sessions?order=id&include_gone=true&limit=1&host=fixture-host';
  health.rememberHealthRead(input.health, path, {sessions: [session()], next_cursor: 'next'});
  assert.equal(health.healthDisplay(input, at).summary.compact, 'unknown');
  health.rememberHealthRead(input.health, path + '&cursor=next', {sessions: [session('second')], next_cursor: null});
  assert.equal(health.healthDisplay(input, at).summary.compact, 'idle');
  health.rememberHealthRead(input.health, '/sessions?attention=true', {sessions: [], next_cursor: null});
  assert.equal(health.healthDisplay(input, at).summary.compact, 'idle');
  const partial: any = {selected: input.health.selected};
  health.rememberHealthRead(partial, path + '&cursor=orphan', {sessions: [session()], next_cursor: null});
  assert.equal(health.healthDisplay({...input, health: partial}, at).summary.compact, 'unknown');
  const filtered: any = {selected: input.health.selected};
  health.rememberHealthRead(filtered, '/sessions?orchestrated=true', {sessions: [session()], next_cursor: null});
  assert.equal(health.healthDisplay({...input, health: filtered}, at).summary.compact, 'unknown');
});

test('Inventory owns idle and working validity beyond sixty seconds and renews each deadline once', () => {
  for (const working of [false, true]) {
    const input = workspaceFacts();
    if (working) input.health.sessions[0].state.activity = 'streaming';
    assert.equal(health.healthDisplay(input, at + 61_000).summary.compact, working ? 'working' : 'idle');
    assert.equal(health.takeHealthRenewal(input.health, at + 179_999), null);
    const path = health.takeHealthRenewal(input.health, at + 180_000);
    assert.equal(path, '/sessions?order=id&include_gone=true&limit=50');
    assert.equal(health.takeHealthRenewal(input.health, at + 181_000), null);
    health.withdrawHealthRead(input.health, 'GET', path, {status: 503, code: 'TEMPORARY_UNAVAILABLE'});
    const stale = health.healthDisplay(input, at + 181_000).summary;
    assert.equal(stale.stale, true); assert.equal(stale.facts[0].activity.ageSeconds, 181);
    assert.equal(stale.facts[0].activity.error.code, 'TEMPORARY_UNAVAILABLE');
    health.rememberHealthRead(input.health, path, {sessions: [session('fixture-session', at + 180_000)], next_cursor: null});
    assert.equal(health.healthDisplay(input, at + 181_000).summary.compact, 'idle');
    assert.equal(health.takeHealthRenewal(input.health, at + 360_000), path);
  }
});

test('gone rows supply neither attention nor last-value facts', () => {
  const input = workspaceFacts(), gone = session('historical');
  Object.assign(gone, {gone_at: iso(at), stale: true, pending: {kind: 'permission'}});
  input.health.sessions.push(gone);
  const summary = health.healthDisplay(input, at).summary;
  assert.equal(summary.compact, 'idle'); assert.equal(summary.facts.length, 1);
});

test('withdrawal follows principal, inventory or selected detail without losing selection', () => {
  for (const [method, path, status, withdrawn] of [
    ['GET', '/pm/governance', 404, false], ['POST', '/operations', 403, false],
    ['GET', '/sessions/other/absent', 404, false], ['GET', '/sessions/fixture-host/fixture-session', 404, true],
    ['GET', '/hosts', 403, true], ['POST', '/operations', 401, true],
  ] as const) {
    const input = workspaceFacts(), selected = structuredClone(input.health.selected);
    health.withdrawHealthRead(input.health, method, path, {status});
    assert.deepEqual(input.health.selected, selected);
    assert.equal(input.health.sessions.length === 0, withdrawn, `${status} ${path}`);
  }
});

test('bootstrap resets currentness while retaining selected last values only within the same namespace', () => {
  const input = workspaceFacts();
  const reset = health.resetHealthBootstrap(input.health, {sameScope: true, caps: input.caps, now: at});
  assert.deepEqual(reset.selected, input.health.selected);
  assert.equal(health.healthDisplay({...input, health: reset}, at).summary.stale, true);
  assert.equal(health.healthDisplay({...input, health: reset}, at).capabilities.freshness, 'current');
  const different = health.resetHealthBootstrap(input.health, {sameScope: false, caps: input.caps, now: at});
  assert.equal(different.selected, null); assert.deepEqual(different.sessions, []);
});

test('filtered host reads merge without claiming the complete central inventory; null reads are harmless', () => {
  const h: any = {};
  health.rememberHealthRead(h, '/hosts?host=one', {hosts: [host('one')]});
  assert.ok(!h.hostsLoaded);
  health.rememberHealthRead(h, '/hosts', {hosts: [host('one'), host('two')]});
  health.rememberHealthRead(h, '/hosts?host=one', {hosts: [host('one', false)]});
  assert.deepEqual(h.hosts.map(r => r.host), ['one', 'two']);
  assert.doesNotThrow(() => health.rememberHealthRead(h, '/empty', null));
});

test('bounded caches pin the selected row and do not claim completeness after eviction', () => {
  const input = workspaceFacts();
  health.rememberHealthRead(input.health, '/sessions?order=id&include_gone=true', {
    sessions: [session(), ...Array.from({length: 501}, (_, i) => session(`other-${i}`))], next_cursor: null});
  assert.ok(input.health.sessions.some(row => row.session_id === 'fixture-session'));
  assert.equal(health.healthDisplay(input, at).summary.compact, 'unknown');
  const selected = structuredClone(input.health.selected);
  health.selectHealthSession(input.health, 'fixture-host', 'fixture-session');
  assert.deepEqual(input.health.selected, selected);
});

test('native count uses the Fleet diagnosis and is omitted for central selections', () => {
  const snapshot = structuredClone(fleet);
  for (const row of snapshot.readiness.hosts.slice(0, 2)) Object.assign(row, {level: 'down', blocking: 'auth', code: 'BAT_AUTH_REFUSED'});
  const input: any = {...facts(), health: {fleet: snapshot}};
  assert.equal(health.healthDisplay(input, at).otherAttention, 2);
  assert.equal(health.healthDisplay(input, at).other.href, '#/settings?section=fleet');
  input.health.selected = {host: 'fixture-host', sessionId: 'fixture-session'};
  assert.equal(health.healthDisplay(input, at).otherAttention, 0);
  delete input.health.selected;
  for (const mutate of [s => s.monitor.state = 'stopped', s => s.selection.applied_revision = 'pending',
    s => s.readiness.hosts.forEach(r => r.blocking = 'stale')]) {
    const doc = structuredClone(snapshot); mutate(doc);
    assert.equal(health.healthDisplay({...input, health: {fleet: doc}}, at).otherAttention, 0);
  }
});

test('localized labels have one Checking, coarse ages, plural forms and producer reasons', () => {
  for (const locale of ['en', 'zh-TW']) {
    const t = (key, args = {}) => Object.entries(args).reduce((s, [k, v]) => s.replaceAll(`{${k}}`, String(v)), STRINGS[locale][key]);
    const unknown = health.healthLabel({key: 'health_unknown', freshness: 'unknown'}, t);
    assert.equal(unknown.split(t('health_checking')).length, 2);
    const staleUnknown = health.healthLabel({key: 'health_unknown', freshness: 'stale', ageSeconds: 61}, t);
    assert.equal(staleUnknown.split(t('health_checking')).length, 2);
    const unreachable = health.healthLabel({key: 'health_connection_problem', freshness: 'stale', ageSeconds: 2, stale_reason: 'host_unreachable'}, t);
    assert.ok(unreachable.includes(locale === 'en' ? 'host unreachable' : '無法連至主機'));
    const stale = {key: 'ui_connected', freshness: 'stale', ageSeconds: 61};
    assert.equal(health.healthLabel(stale, t), health.healthLabel({...stale, ageSeconds: 62}, t));
    assert.ok(t('health_other_machines_one', {count: 1}).includes('1'));
    assert.ok(t('health_other_machines_many', {count: 2}).includes('2'));
  }
});
