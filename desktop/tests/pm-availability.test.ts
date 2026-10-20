import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {STRINGS} from '../src/i18n.js';
const fixture = JSON.parse(readFileSync(new URL('../../tests/fixtures/pm-availability.json', import.meta.url), 'utf8'));
const modulePath = '../src/pm-availability.js';
const input = (name = 'owner') => ({caps: fixture[name].capabilities, document: fixture[name].workspace, checked: true});

test('PM maps producer facts to reasons, never to a connection guess', async () => {
  const {pmAvailability} = await import(modulePath);
  for (const [name, state, reason] of [
    ['owner', 'ready', 'installation_hint'], ['observer', 'missing_scope', 'analysis_scope'],
    ['executable_unavailable', 'ineligible', 'PM_NATIVE_EXECUTABLE_UNAVAILABLE'],
    ['action_unregistered', 'unregistered', 'action_unavailable'],
  ]) {
    const result = pmAvailability(input(name));
    assert.equal(result.state, state);
    assert.equal(result.reason, reason);
    assert.equal(result.ready, state === 'ready');
    for (const lang of ['en', 'zh-TW']) for (const key of [result.statusKey, result.messageKey]) assert.ok(STRINGS[lang][key]?.trim(), `${lang}: ${key}`);
  }
});

test('unknown, unchecked, checking, refused, failed and unsupported stay distinct', async () => {
  const {pmAvailability} = await import(modulePath);
  const facts = input();
  const unsupported = structuredClone(facts); delete unsupported.caps.features.pm_workspaces; // compatibility omission of a real row
  const noObserve = structuredClone(facts); noObserve.caps.scopes = ['start'];
  for (const [value, state] of [
    [{caps: null}, 'unknown'], [{caps: facts.caps, checked: false}, 'not_checked'],
    [{caps: facts.caps, checking: true}, 'checking'], [{...facts, document: {}}, 'unknown'],
    [unsupported, 'unsupported'], [{...noObserve, kind: 'workspace'}, 'missing_scope'],
    ...[401, 403, 503, 0].map(status => [{...facts, readError: {status, code: status === 0 ? 'NETWORK_ERROR' : `HTTP_${status}`}}, 'failed']),
  ]) {
    const result = pmAvailability(value);
    assert.equal(result.state, state);
    assert.equal(result.ready, false);
    for (const lang of ['en', 'zh-TW']) for (const key of [result.statusKey, result.messageKey]) assert.ok(STRINGS[lang][key]?.trim(), `${lang}: ${key}`);
  }
  assert.equal(pmAvailability({...noObserve, kind: 'workspace'}).reason, 'observe_scope');
  assert.equal(pmAvailability(unsupported).reason, 'pm_unsupported');
});

test('Settings retains its any-installation predicate and descriptor order', async () => {
  const {pmAvailability} = await import(modulePath);
  const facts = input();
  const bat = {...facts.document.native_analysis, adapter: 'bat-pm-linux-v1', reason: 'PM_WORKTREE_REQUIRED', eligible_for_preflight: false};
  assert.equal(pmAvailability({...facts, document: {...facts.document, bat_analysis: bat}}).ready, true);
  const blockedNative = {...facts.document.native_analysis, eligible_for_preflight: false, reason: 'PM_NATIVE_EXECUTABLE_UNAVAILABLE'};
  const readyBat = {...bat, eligible_for_preflight: true, reason: null};
  assert.equal(pmAvailability({...facts, document: {...facts.document, native_analysis: blockedNative, bat_analysis: readyBat}}).ready, true);
  assert.equal(pmAvailability({...facts, document: {...facts.document, native_analysis: blockedNative, bat_analysis: bat}}).reason, 'PM_NATIVE_EXECUTABLE_UNAVAILABLE');
});

test('unexplained authorization and future eligibility reasons are not guessed', async () => {
  const {pmAvailability, eligibilityReasons} = await import(modulePath);
  const facts = input();
  for (const descriptor of [
    {...facts.document.native_analysis, authorized: false},
    {...facts.document.native_analysis, eligible_for_preflight: false, reason: 'future_pm_reason'},
  ]) {
    const result = pmAvailability({...facts, descriptors: [descriptor]});
    assert.equal(result.ready, false);
    assert.equal(result.state, descriptor.authorized === false ? 'unauthorized' : 'ineligible');
    for (const lang of ['en', 'zh-TW']) assert.ok(STRINGS[lang][result.messageKey]?.trim());
  }
  for (const key of Object.values(eligibilityReasons)) for (const lang of ['en', 'zh-TW']) assert.ok(STRINGS[lang][key as string]?.trim());
});
