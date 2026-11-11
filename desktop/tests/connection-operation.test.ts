import assert from 'node:assert/strict';
import {test} from 'node:test';
import {connectionOperationRecovery as recovery} from '../src/connection-operation.js';
import {STRINGS} from '../src/i18n.js';

const operation = (extra: any = {}) => ({action: 'setup.connection.apply', status: 'needs_attention',
  external_refs: {native_effects_sent: true, receipt_refs: ['receipt-original']},
  steps: [{seq: 1, name: 'managed_configuration', status: 'succeeded'},
    {seq: 2, name: 'native_effects', status: 'uncertain'}], ...extra});

test('only native connection actions receive the recovery presentation', () => {
  for (const action of ['setup.host', 'connection.reference.apply', 'session.send', 'worktree.merge', undefined])
    assert.equal(recovery(operation({action})), null);
  for (const action of ['setup.connection.apply', 'connection.reference.detach'])
    assert.equal(recovery(operation({action}))?.cancelKey, 'cop_close_unknown');
});

test('dispatch and receipt references do not establish a completed local effect', () => {
  assert.equal(recovery(operation())?.kind, 'unknown');
  const dispatched = recovery(operation({status: 'waiting_external'}));
  assert.equal(dispatched?.kind, 'dispatched');
  assert.equal(dispatched?.cancelBlocked, true);
  assert.equal(recovery(operation({status: 'waiting_external', external_refs: {native_effects_sent: false}}))?.kind, 'waiting');
  assert.equal(recovery(operation({steps: []}))?.kind, 'dispatched');
});

test('configuration-only uncertainty cannot be closed as an unproven native result', () => {
  const config = operation({external_refs: {native_effects_sent: false},
    steps: [{seq: 1, name: 'managed_configuration', status: 'uncertain'}]});
  assert.equal(recovery(config)?.kind, 'original');
  assert.equal(recovery(config)?.cancelKey, 'cancel');
  const later = operation();
  later.steps.push({seq: 3, name: 'connection_binding', status: 'uncertain'});
  assert.notEqual(recovery(later)?.kind, 'unknown');
});

test('a completed native step retains its original completion path and specific projection exit', () => {
  const op = operation({steps: [{seq: 1, name: 'native_effects', status: 'succeeded'}]});
  assert.equal(recovery(op)?.kind, 'completion_pending');
  assert.equal(recovery(op)?.cancelBlocked, true);
  op.error_code = 'CONNECTION_PROJECTION_CHANGED';
  assert.equal(recovery(op)?.kind, 'projection_conflict');
  assert.equal(recovery(op)?.cancelKey, 'cop_close_projection');
  assert.equal(recovery(op)?.cancelBlocked, false);
});

test('terminal records describe the recorded decision without inferring rollback', () => {
  const op = operation({status: 'cancelled'});
  assert.equal(recovery(op)?.kind, 'original');
  op.steps[1].error = {code: 'LOCAL_EFFECT_NEVER_PROVEN'};
  assert.equal(recovery(op)?.kind, 'unknown_closed');
  op.steps[1] = {seq: 2, name: 'native_effects', status: 'succeeded'};
  op.external_refs.projection_decision = {code: 'CONNECTION_PROJECTION_NOT_SAVED'};
  assert.equal(recovery(op)?.kind, 'projection_closed');
  for (const status of ['succeeded', 'failed']) {
    assert.equal(recovery({...op, status})?.kind, 'original');
    assert.equal(recovery({...op, status})?.terminal, true);
  }
});

test('every recovery state and control has English and Traditional Chinese wording', () => {
  for (const locale of ['en', 'zh-TW']) for (const key of [
    'original', 'waiting', 'dispatched', 'unknown', 'unknown_closed', 'projection_conflict',
    'projection_closed', 'completion_pending', 'preserved', 'close_unknown', 'close_projection',
    'resume', 'refresh', 'readback', 'title',
  ]) assert.ok(STRINGS[locale]['cop_' + key], `${locale}: ${key}`);
});
