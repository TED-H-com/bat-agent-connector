import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as projection from '../src/central-service-status.js';
import {STRINGS} from '../src/i18n.js';
import {ApiError, errorPresentation} from '../src/error-presentation.js';
import * as errors from '../src/error-presentation.js';
import {failedPolls} from './poll-observation-fixture.ts';

const gate = (facts: any, requirements: any) => (projection as any).clientActionGate(facts, requirements);
const fields = ['token', 'nativeBusy', 'online', 'viewReady', 'manageAllowed', 'attachmentsSupported', 'uploadsAllowed'];
const sites = [
  ['POST including its existing credential check', {post: true}, (f: any) => !!f.token && !f.nativeBusy && f.online && f.viewReady],
  ['waiting_external', {attachment: true}, (f: any) => f.online && f.viewReady],
  ['browser paste', {attachment: true, supported: true, manage: true}, (f: any) => f.attachmentsSupported && f.manageAllowed && f.online && f.viewReady],
  ['native paste', {attachment: true, supported: true, manage: true, uploads: true}, (f: any) => f.attachmentsSupported && f.manageAllowed && f.uploadsAllowed && f.online && f.viewReady],
  // nativeFiles already returned before the old browser-drop expression.
  ['browser drop', {attachment: true, supported: true, manage: true}, (f: any) => f.attachmentsSupported && f.manageAllowed && f.online && f.viewReady],
  ['native canWrite', {attachment: true, manage: true, uploads: true}, (f: any) => f.online && f.viewReady && f.manageAllowed && f.uploadsAllowed],
] as const;

for (const [name, requirements, oldAllowed] of sites) test(`${name}: exhaustive old/new allowance equivalence`, () => {
  for (let bits = 0; bits < 2 ** fields.length; bits++) {
    const facts: any = Object.fromEntries(fields.map((field, i) => [field, !!(bits & (1 << i))]));
    facts.token = facts.token ? 'credential' : null;
    for (const pollHealth of ['initial', 'connected', 'reconnecting', ...failedPolls.map(([status, code, kind]) =>
      ({kind, status, code, at: 1234}))]) for (const managedRecovery of [null, 'MANAGED_RECOVERING']) {
      facts.pollHealth = pollHealth; facts.managedRecovery = managedRecovery;
      const result = gate(facts, requirements);
      assert.equal(result.allowed, !!oldAllowed(facts), `${name}: ${JSON.stringify(facts)}`);
      assert.equal(result.reason === null, result.allowed);
      if (result.reason === 'reconnecting') assert.equal(projection.centralServiceStatus(facts).key, 'ui_reconnecting');
    }
  }
});

const ready = {token: 'credential', online: true, viewReady: true, pollHealth: 'connected',
  nativeBusy: false, manageAllowed: true, attachmentsSupported: true, uploadsAllowed: true};
export const reasons = [
  ['not_connected', {...ready, token: null, online: false, viewReady: false}, {post: true}],
  ['connecting', {...ready, online: false, viewReady: false, pollHealth: 'initial'}, {post: true}],
  ['reconnecting', {...ready, online: false, pollHealth: 'reconnecting'}, {post: true}],
  ['connection_changing', {...ready, nativeBusy: true, online: false}, {post: true}],
  ['view_not_ready', {...ready, viewReady: false}, {post: true}],
  ['manage_required', {...ready, manageAllowed: false}, {attachment: true, manage: true}],
  ['attachments_unavailable', {...ready, attachmentsSupported: false}, {attachment: true, supported: true}],
  ['uploads_not_allowed', {...ready, uploadsAllowed: false}, {attachment: true, uploads: true}],
  ...failedPolls.filter(([, , kind]) => kind !== 'transport').map(([status, code, kind, , reason]) =>
    [reason, {...ready, online: false, pollHealth: {kind, status, code, at: 1234}}, {post: true}] as const),
] as const;
for (const [reason, facts, requirements] of reasons) test(`distinct reason: ${reason}`, () => {
  assert.deepEqual(gate(facts, requirements), {allowed: false, reason});
});

for (const locale of ['en', 'zh-TW'] as const) for (const [reason] of reasons) {
  test(`${locale}: ${reason} survives known-code error presentation`, () => {
    const code = `CLIENT_ACTION_${reason.toUpperCase()}`, key = `error_code_${code}`;
    const t = (key: string) => STRINGS[locale][key] || key;
    assert.ok(STRINGS[locale][key], key);
    const result = errorPresentation(new ApiError(0, code, t(key), {localized: true}), t);
    assert.equal(result.summary, t(key));
    assert.equal((errors as any).sessionErrorLine(new ApiError(0, code, t(key), {localized: true}), t), t(key));
    if (reason !== 'reconnecting') assert.doesNotMatch(result.summary, /offline|cannot reach|離線|無法連線/i);
  });
}

for (const locale of ['en', 'zh-TW'] as const) test(`${locale}: session server/transport classifications stay unchanged`, () => {
  const t = (key: string) => STRINGS[locale][key] || key;
  for (const [status, code, key] of [
    [0, 'CENTRAL_OFFLINE', 'offline_actions_paused'], [0, 'NETWORK_ERROR', 'session_err_unknown'],
    [0, 'TIMEOUT', 'session_err_unknown'], [409, 'SESSION_BUSY', 'session_err_busy'],
    [409, 'PENDING_CHANGED', 'session_err_changed'], [404, 'NOT_FOUND', 'session_err_gone'],
    [403, 'UNKNOWN', 'session_err_forbidden'], [410, 'UNKNOWN', 'session_err_gone'],
    [413, 'UNKNOWN', 'session_err_too_large'], [503, 'UNKNOWN', 'session_err_server'],
  ] as const) assert.equal((errors as any).sessionErrorLine(new ApiError(status, code, 'fixture diagnostic'), t), t(key));
  const refusal = (errors as any).clientActionError('not_connected', t, 401);
  assert.equal(refusal.status, 401, 'preserve the existing missing-token draft-key classification');
});
