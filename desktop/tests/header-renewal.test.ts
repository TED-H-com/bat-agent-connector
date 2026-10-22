import {test} from 'node:test';
import assert from 'node:assert/strict';
import {healthDisplay, healthLabel, rememberHealthRead, takeHealthRenewal, withdrawHealthRead, resetHealthBootstrap} from '../src/health-display.js';
import {STRINGS} from '../src/i18n.js';
import {hostGolden} from './header-hosts-fixture.ts';
import {runtimeGolden} from './session-runtime-fixture.ts';

const translate = locale => (key, args = {}) => Object.entries(args).reduce((s, [k, v]) => s.replaceAll(`{${k}}`, String(v)), STRINGS[locale][key]);
const input = health => ({health, token: 'fixture', namespace: 'fixture', online: true, caps: {}});

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
  const doc = {hosts: [
    {...old, host: 'older', valid_until: new Date(deadline - 1_000).toISOString()},
    {...old, host: 'equal'},
    {...old, host: 'newer', valid_until: new Date(deadline + 1_000).toISOString()},
    {...hostGolden.host_unreachable.hosts[0], host: 'unreachable', valid_until: old.valid_until},
  ]};
  rememberHealthRead(health, '/hosts', doc, {renewalDeadline: deadline});
  const facts = healthDisplay(input(health), deadline).hosts;
  assert.deepEqual(facts.map(fact => fact.error?.code || null),
    ['OBSERVATION_NOT_RENEWED', 'OBSERVATION_NOT_RENEWED', null, null]);
  rememberHealthRead(health, '/hosts?host=equal', {hosts: [doc.hosts[1]]});
  assert.ok(!healthDisplay(input(health), deadline).hosts.find(fact => fact.host === 'equal').error);
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
