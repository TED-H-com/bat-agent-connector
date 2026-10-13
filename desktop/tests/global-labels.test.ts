import {test} from 'node:test';
import assert from 'node:assert/strict';
import {actionLabel, formatTime, operationDuration, operationMeta, operationStatusKey, railPresentation, reasonText, workStatusKey} from '../src/global-labels.js';

function either(actual: string, options: string[]) {
  assert.ok(options.includes(actual), `${actual} not in ${options.join(' | ')}`);
}

test('operation metadata omits empty separators and unknown durations', () => {
  assert.equal(operationMeta({action: 'checkpoint.continue', status: 'uncertain'}), '');
  assert.equal(formatTime(0), '');
  assert.equal(formatTime(Number.NaN), '');
  assert.equal(operationDuration({created_at: 10}), '');
  assert.equal(operationDuration({created_at: 20, finished_at: 10}), '');
  assert.equal(operationStatusKey('uncertain'), 'unknown');
  assert.equal(operationStatusKey('running'), 'in_progress');
  assert.equal(operationStatusKey('needs_attention'), 'blocked');
  assert.equal(workStatusKey('awaiting_approval'), 'in_review');
  assert.equal(workStatusKey('doing'), 'in_progress');
});

test('rail words are loud only when the user is needed', () => {
  const streaming = railPresentation({key: 'obs_value_streaming', tone: 'info'});
  assert.equal(streaming?.loud, false);
  assert.equal(streaming?.labelKey, 'rail_running');
  assert.equal(streaming?.tone, 'info');
  const asking = railPresentation({key: 'pending_ask_user', tone: 'stale'});
  assert.equal(asking?.loud, true);
  assert.equal(asking?.labelKey, 'rail_asking');
  assert.equal(railPresentation({key: 'workspace_needs_you'})?.loud, true);
  assert.equal(railPresentation({key: 'workspace_needs_you'})?.labelKey, 'rail_needs_you');
  assert.equal(railPresentation({key: 'wi_state_doing', tone: ''})?.loud, false);
  assert.equal(railPresentation(null), null);
});

test('action labels and reason codes stay out of the raw id', () => {
  const label = actionLabel('checkpoint.continue');
  either(label, ['Continue from checkpoint', '從檢查點繼續']);
  assert.notEqual(label, 'checkpoint.continue');
  either(actionLabel('not.a.real.action'), ['Operation', '操作']);
  const unknown = reasonText('totally_unknown_code');
  either(unknown, ['Not available', '無法取得']);
  assert.notEqual(unknown, 'totally_unknown_code');
  either(reasonText('host_unreachable'), ['Host unreachable', '主機連不上']);
});
