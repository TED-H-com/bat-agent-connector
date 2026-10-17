import assert from 'node:assert/strict';
import test from 'node:test';
import {classifyPublicationEntry} from '../src/pm-work-publish.js';
import {workPublishStrings} from '../src/pm-work-publish-strings.js';

const SHA = 'abcdef1234567890abcdef1234567890abcdef12';
const BASE = '1234567890abcdef1234567890abcdef12345678';

function target(extra = {}) {
  return {
    workspace_id: 'ws_' + '1'.repeat(32),
    repository: 'acme/widget',
    host: 'fixture-host',
    source_operation_id: 'op_' + '2'.repeat(32),
    ...extra,
  };
}

function completeRow() {
  return {
    kind: 'action',
    action: 'github.pr.publish',
    target: target(),
    required_scopes: ['integrate', 'manage', 'observe'],
    required_params: ['candidate_sha', 'base_ref', 'public_title', 'public_body', 'draft'],
    optional_params: ['quality_ref'],
    prefilled_params: {candidate_sha: SHA, base_ref: 'main'},
    preconditions: {repository_id: 7, expected_base_sha: BASE},
  };
}

test('a complete publication entry is the only submittable shape', () => {
  const found = classifyPublicationEntry({next_allowed_actions: [completeRow()], blocked_actions: []});
  assert.equal(found.mode, 'ready');
  assert.equal(found.entry?.candidate, SHA);
  assert.equal(found.entry?.baseRef, 'main');
  assert.deepEqual(found.entry?.preconditions, {repository_id: 7, expected_base_sha: BASE});
  assert.deepEqual(found.entry?.target, target());
});

test('the current candidate-only offer keeps a draft and does not submit', () => {
  const row = completeRow();
  row.prefilled_params = {candidate_sha: SHA};
  delete row.preconditions;
  const found = classifyPublicationEntry({next_allowed_actions: [row], blocked_actions: []});
  assert.equal(found.mode, 'incomplete');
  assert.equal(found.gap, 'preconditions');
  assert.equal(found.entry?.candidate, SHA);
  assert.equal(found.entry?.target.repository, 'acme/widget');
});

test('a missing publication entry draws nothing', () => {
  const found = classifyPublicationEntry({next_allowed_actions: [], blocked_actions: []});
  assert.equal(found.mode, 'absent');
  assert.equal(found.entry, null);
});

test('an extra target key is not rewritten into a submittable target', () => {
  const row = completeRow();
  row.target = target({account: 'someone'});
  const found = classifyPublicationEntry({next_allowed_actions: [row]});
  assert.equal(found.mode, 'incomplete');
  assert.equal(found.gap, 'target');
  assert.equal(found.entry, null);
});

test('a string repository id is not a complete precondition', () => {
  const row = completeRow();
  row.preconditions = {repository_id: '7', expected_base_sha: BASE};
  const found = classifyPublicationEntry({next_allowed_actions: [row]});
  assert.equal(found.mode, 'incomplete');
  assert.equal(found.gap, 'preconditions');
});

test('a blocked publication wins over a listed action', () => {
  const found = classifyPublicationEntry({
    next_allowed_actions: [completeRow()],
    blocked_actions: [{action: 'github.pr.publish', reason: 'not_controller'}],
  });
  assert.equal(found.mode, 'blocked');
  assert.equal(found.reason, 'not_controller');
  assert.equal(found.entry, null);
});

test('publication copy exists in English and Traditional Chinese', () => {
  const en = Object.keys(workPublishStrings.en).sort();
  const zh = Object.keys(workPublishStrings['zh-TW']).sort();
  assert.deepEqual(zh, en);
  for (const key of en) {
    assert.ok(workPublishStrings.en[key]);
    assert.ok(workPublishStrings['zh-TW'][key]);
    assert.equal(workPublishStrings['zh-TW'][key].toLowerCase().includes('controller'), false);
  }
  assert.match(workPublishStrings.en.wpub_source_changed, /repository/i);
  assert.match(workPublishStrings['zh-TW'].wpub_source_changed, /儲存庫/);
  assert.match(workPublishStrings['zh-TW'].wpub_blocked_not_controller, /控制者/);
});


test('foreign or explicitly denied publication targets cannot be submitted', () => {
  for (const row of [
    {...completeRow(), allowed: false},
    {...completeRow(), target: target({workspace_id: 'ws_1'})},
    {...completeRow(), target: target({source_operation_id: 'not-an-operation'})},
    {...completeRow(), required_params: [...completeRow().required_params, 'unimplemented_field']},
  ]) assert.notEqual(classifyPublicationEntry({next_allowed_actions: [row]}).mode, 'ready');
  assert.notEqual(classifyPublicationEntry({workspace_id: 'ws_' + '2'.repeat(32), next_allowed_actions: [completeRow()]}).mode, 'ready');
  assert.notEqual(classifyPublicationEntry({identity: {execution_operation_id: 'op_' + '4'.repeat(32)}, next_allowed_actions: [completeRow()]}).mode, 'ready');
});
