import {test} from 'node:test';
import assert from 'node:assert/strict';
import {healthDisplay, healthLabel, rememberHealthRead, takeHealthRenewal, withdrawHealthRead, resetHealthBootstrap} from '../src/health-display.js';
import {STRINGS} from '../src/i18n.js';
import {hostGolden} from './header-hosts-fixture.ts';
import {runtimeGolden} from './session-runtime-fixture.ts';

const translate = locale => (key, args = {}) => Object.entries(args).reduce((s, [k, v]) => s.replaceAll(`{${k}}`, String(v)), STRINGS[locale][key]);
const input = health => ({health, token: 'fixture', namespace: 'fixture', online: true, caps: {}});

for (const locale of ['en', 'zh-TW']) for (const source of ['host', 'session']) {
  test(`header reconnect preserves current ${source} until its deadline ${locale}`, () => {
    const doc = structuredClone(hostGolden.host_not_refreshed), host = doc.hosts[0];
    const deadline = Date.parse(host.valid_until), now = deadline - 170_000;
    const session = structuredClone(runtimeGolden.ended_runtime.listed);
    for (const axis of ['activity', 'connection']) Object.assign(session.state.evidence[axis], {
      observed_at: host.last_attempt_at, valid_until: host.valid_until});
    const current = structuredClone(doc);
    // Inventory marks this observation host_not_refreshed only once it expires.
    Object.assign(current.hosts[0], {stale: false, stale_reason: null});
    const path = source === 'host' ? '/hosts' : '/sessions?order=id';
    const response = source === 'host' ? current : {sessions: [session], next_cursor: null};
    const previous: any = {selected: {host: session.host, sessionId: session.session_id}};
    rememberHealthRead(previous, path, response);
    const health: any = resetHealthBootstrap(previous, {sameScope: true, caps: {}, now});
    assert.equal(takeHealthRenewal(health, now), path);
    rememberHealthRead(health, path, response, {renewalDeadline: health.renewalDeadline});
    const facts = at => {
      const projection = healthDisplay(input(health), at);
      return source === 'host' ? [{...projection.hosts[0], key: 'wpf_unavailable'}]
        : [{...projection.summary.facts[0].activity, key: 'health_idle'}, {...projection.summary.connection, key: 'ui_connected'}];
    };
    for (const fact of facts(now)) {assert.equal(fact.freshness, 'current'); assert.ok(!fact.error);}
    assert.deepEqual(facts(now).map(fact => healthLabel(fact, translate(locale))), source === 'host'
      ? [locale === 'en' ? 'Unavailable' : '不可用'] : locale === 'en' ? ['Idle', 'Connected'] : ['閒置', '已連線']);
    assert.equal(takeHealthRenewal(health, now + 1_000), null);
    assert.equal(takeHealthRenewal(health, deadline), path);
    rememberHealthRead(health, path, source === 'host' ? doc : response, {renewalDeadline: health.renewalDeadline});
    const expected = locale === 'en'
      ? ['Unavailable · 3 min ago · Could not confirm (OBSERVATION_NOT_RENEWED)', 'Idle · 3 min ago · Could not confirm (OBSERVATION_NOT_RENEWED)', 'Connected · 3 min ago · Could not confirm (OBSERVATION_NOT_RENEWED)']
      : ['不可用 · 3 分鐘前 · 無法確認（OBSERVATION_NOT_RENEWED）', '閒置 · 3 分鐘前 · 無法確認（OBSERVATION_NOT_RENEWED）', '已連線 · 3 分鐘前 · 無法確認（OBSERVATION_NOT_RENEWED）'];
    assert.deepEqual(facts(deadline).map(fact => healthLabel(fact, translate(locale))), source === 'host' ? expected.slice(0, 1) : expected.slice(1));
    assert.equal(takeHealthRenewal(health, deadline + 600_000), null);
  });
}

test('a forced reconnect read does not spend a future deadline, even if its response arrives after it', () => {
  const doc = structuredClone(hostGolden.host_not_refreshed), deadline = Date.parse(doc.hosts[0].valid_until);
  const previous = {};
  rememberHealthRead(previous, '/hosts', doc);
  const health: any = resetHealthBootstrap(previous, {sameScope: true, caps: {}, now: deadline - 170_000});
  assert.equal(takeHealthRenewal(health, deadline - 170_000), '/hosts');
  const startedCutoff = health.renewalDeadline;
  assert.equal(takeHealthRenewal(health, deadline - 1), null);
  assert.equal(takeHealthRenewal(health, deadline), '/hosts');
  rememberHealthRead(health, '/hosts', doc, {renewalDeadline: startedCutoff});
  assert.ok(!healthDisplay(input(health), deadline + 1).hosts[0].error);
  assert.equal(takeHealthRenewal(health, deadline + 600_000), null);
});

test('an expired reconnect read spends the deadline; an advancing normal read replaces a future deadline', () => {
  const doc = structuredClone(hostGolden.host_not_refreshed), deadline = Date.parse(doc.hosts[0].valid_until);
  const previous = {};
  rememberHealthRead(previous, '/hosts', doc);
  const expired: any = resetHealthBootstrap(previous, {sameScope: true, caps: {}, now: deadline});
  assert.equal(takeHealthRenewal(expired, deadline), '/hosts');
  rememberHealthRead(expired, '/hosts', doc, {renewalDeadline: expired.renewalDeadline});
  assert.equal(healthDisplay(input(expired), deadline).hosts[0].error.code, 'OBSERVATION_NOT_RENEWED');
  assert.equal(takeHealthRenewal(expired, deadline + 600_000), null);
  const current: any = resetHealthBootstrap(previous, {sameScope: true, caps: {}, now: deadline - 170_000});
  assert.equal(takeHealthRenewal(current, deadline - 170_000), '/hosts');
  rememberHealthRead(current, '/hosts', doc, {renewalDeadline: current.renewalDeadline});
  Object.assign(doc.hosts[0], {last_attempt_at: new Date(deadline).toISOString(), last_success_at: new Date(deadline).toISOString(),
    valid_until: new Date(deadline + 180_000).toISOString(), stale: false, stale_reason: null});
  rememberHealthRead(current, '/hosts', doc);
  assert.equal(takeHealthRenewal(current, deadline), null);
  assert.equal(takeHealthRenewal(current, deadline + 180_000), '/hosts');
});

for (const locale of ['en', 'zh-TW']) for (const scenario of ['host_not_refreshed', 'host_unreachable']) {
  test(`header unchanged host renewal ${scenario} ${locale}`, () => {
    const health: any = {}, doc = structuredClone(hostGolden[scenario]), row = doc.hosts[0];
    const now = scenario === 'host_unreachable' ? 1790000900000 : 1790000600000;
    rememberHealthRead(health, '/hosts', doc);
    assert.equal(takeHealthRenewal(health, now), '/hosts');
    rememberHealthRead(health, '/hosts', doc, {renewalDeadline: Date.parse(row.valid_until)});
    const fact = healthDisplay(input(health), now).hosts?.[0] || {};
    const expected = locale === 'en' ? {
      host_not_refreshed: 'Unavailable · 10 min ago · Could not confirm (OBSERVATION_NOT_RENEWED)',
      host_unreachable: 'Unavailable · Last attempt: 1 min ago; host unreachable, last successful observation is stale',
    } : {
      host_not_refreshed: '不可用 · 10 分鐘前 · 無法確認（OBSERVATION_NOT_RENEWED）',
      host_unreachable: '不可用 · 上次嘗試：1 分鐘前；無法連至主機，上次成功觀察已過期',
    };
    // Use Work's value label to compare the header's per-host fact byte for byte.
    assert.equal(healthLabel({...fact, key: 'wpf_unavailable'}, translate(locale)), expected[scenario]);
    assert.equal(takeHealthRenewal(health, now + 600_000), null);
  });
}

test('header renewal outcomes are per source and deadline; read errors override every reason', () => {
  const health: any = {}, doc = structuredClone(hostGolden.host_unreachable), now = 1790000900000;
  const path = '/hosts?host=fixture-host';
  rememberHealthRead(health, path, doc, {renewalDeadline: Date.parse(doc.hosts[0].valid_until)});
  const project = () => healthDisplay(input(health), now).hosts[0];
  for (const code of ['READ_FAILED', 'OBSERVATION_NOT_RENEWED']) {
    withdrawHealthRead(health, 'GET', path, {status: 503, code});
    assert.equal(project().error.code, code);
  }
  const renewed = structuredClone(doc);
  Object.assign(renewed.hosts[0], {reachable: true, stale: false, stale_reason: null, error: null,
    last_attempt_at: new Date(now).toISOString(), valid_until: new Date(now + 180_000).toISOString()});
  rememberHealthRead(health, path, renewed);
  assert.ok(!project().error);
  assert.equal(health.renewalOutcomes.size, 0);
  assert.equal(project().freshness, 'current');
  assert.equal(takeHealthRenewal(health, now + 180_000), path);
});

test('one source classifies each host independently at or before the attempted deadline', () => {
  const health: any = {}, old = hostGolden.host_not_refreshed.hosts[0];
  const deadline = Date.parse(old.valid_until);
  const row = (host, expires) => ({...old, host, valid_until: new Date(expires).toISOString(),
    last_success_at: new Date(expires - 180_000).toISOString(), last_attempt_at: new Date(expires - 180_000).toISOString()});
  const doc = {hosts: [
    row('older', deadline - 1_000), row('equal', deadline), row('newer', deadline + 1_000),
    {...hostGolden.host_unreachable.hosts[0], ...row('unreachable', deadline), reachable: false,
      error: 'CONNECTION_FAILED', stale_reason: 'host_unreachable', last_attempt_at: new Date(deadline + 540_000).toISOString()},
  ]};
  rememberHealthRead(health, '/hosts', doc, {renewalDeadline: deadline});
  const facts = healthDisplay(input(health), deadline + 600_000).hosts;
  assert.deepEqual(facts.map(fact => fact.error?.code || null),
    ['OBSERVATION_NOT_RENEWED', 'OBSERVATION_NOT_RENEWED', null, null]);
  rememberHealthRead(health, '/hosts?host=equal', {hosts: [doc.hosts[1]]});
  assert.ok(!healthDisplay(input(health), deadline + 600_000).hosts.find(fact => fact.host === 'equal').error);
});

test('session axes keep independent deadlines and never inherit the host unreachable exception', () => {
  const row = structuredClone(runtimeGolden.ended_runtime.listed), path = '/sessions?order=id';
  const at = Date.parse(row.state.evidence.activity.observed_at), deadline = at + 180_000;
  for (const axis of ['activity', 'connection']) Object.assign(row.state.evidence[axis], {valid_until: new Date(deadline).toISOString()});
  const health: any = {selected: {host: row.host, sessionId: row.session_id}};
  const doc = {sessions: [row], next_cursor: null};
  rememberHealthRead(health, path, doc);
  assert.equal(takeHealthRenewal(health, deadline), path);
  rememberHealthRead(health, path, doc, {renewalDeadline: deadline});
  const project = () => healthDisplay(input(health), deadline).summary;
  assert.equal(project().facts[0].activity.error.code, 'OBSERVATION_NOT_RENEWED');
  assert.equal(project().connection.error.code, 'OBSERVATION_NOT_RENEWED');
  row.state.evidence.connection.valid_until = new Date(deadline + 180_000).toISOString();
  rememberHealthRead(health, path, doc);
  assert.equal(project().facts[0].activity.error.code, 'OBSERVATION_NOT_RENEWED');
  assert.ok(!project().connection.error);
  withdrawHealthRead(health, 'GET', path, {status: 503, code: 'READ_FAILED'});
  assert.equal(project().connection.error.code, 'READ_FAILED');
  assert.equal(project().facts[0].activity.error.code, 'READ_FAILED');
  for (const sameScope of [true, false]) {
    const reset = resetHealthBootstrap(health, {sameScope, caps: {}, now: deadline});
    assert.ok(!reset.readErrors?.size);
    assert.ok(!reset.renewalOutcomes?.size);
  }
  withdrawHealthRead(health, 'GET', path, {status: 403, code: 'FORBIDDEN'});
  assert.ok(!health.renewalOutcomes?.size);
});
