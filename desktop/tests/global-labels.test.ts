import {test} from 'node:test';
import assert from 'node:assert/strict';
import {actionLabel, formatTime, operationDuration, operationMeta, operationStatusKey, operationTargetText, railPresentation, reasonText, workStatusKey} from '../src/global-labels.js';
import {demoProject} from './demo/project.ts';
import {globalRoutes} from './demo/global.ts';

function either(actual: string, options: string[]) {
  assert.ok(options.includes(actual), `${actual} not in ${options.join(' | ')}`);
}

test('operation metadata omits empty separators and unknown durations', () => {
  assert.equal(operationMeta({action: 'checkpoint.continue', status: 'uncertain'}), '');
  assert.equal(formatTime(0), '');
  assert.equal(formatTime(Number.NaN), '');
  assert.equal(operationDuration({created_at: 10}), '');
  assert.equal(operationDuration({created_at: 20, finished_at: 10}), '');
  assert.equal(operationDuration({status: 'succeeded', created_at: 10}), '');
  assert.equal(operationDuration({status: 'uncertain', created_at: 20, updated_at: 10}), '');
  either(operationDuration({status: 'succeeded', created_at: 10, updated_at: 70}), ['1 min', '1 分']);
  either(operationDuration({status: 'failed', created_at: 10, updated_at: 10}), ['0 s', '0 秒']);
  either(operationDuration({status: 'running', created_at: 10}, 40), ['30 s', '30 秒']);
  either(operationDuration({status: 'accepted', created_at: 1000}, 1075), ['1 min', '1 分']);
  assert.equal(operationStatusKey('uncertain'), 'unknown');
  assert.equal(operationStatusKey('running'), 'in_progress');
  assert.equal(operationStatusKey('needs_attention'), 'blocked');
  assert.equal(workStatusKey('awaiting_approval'), 'in_review');
  assert.equal(workStatusKey('doing'), 'in_progress');
  for (const lane of ['github_closed', 'github_completed', 'github_done']) assert.equal(workStatusKey(lane), lane);
});

test('duration boundaries use a compact day and hour form', () => {
  const at = (seconds: number) => operationDuration({status: 'succeeded', created_at: 1, updated_at: 1 + seconds});
  either(at(0), ['0 s', '0 秒']);
  either(at(59), ['59 s', '59 秒']);
  either(at(60), ['1 min', '1 分']);
  either(at(3599), ['59 min', '59 分']);
  either(at(3600), ['1 h', '1 小時']);
  either(at(86399), ['23 h 59 min', '23 小時 59 分']);
  either(at(86400), ['1 d', '1 天']);
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
  assert.equal(railPresentation({key: 'sessions_stale'})?.loud, true);
  assert.equal(railPresentation({key: 'sessions_stale'})?.labelKey, 'sessions_pill_stale');
  assert.equal(railPresentation({key: 'sessions_not_seen'})?.labelKey, 'sessions_pill_stale');
  assert.equal(railPresentation({key: 'sessions_not_loaded'}), null);
  assert.equal(railPresentation({key: 'sessions_activity_unknown'}), null);
  assert.equal(railPresentation({key: 'wi_state_doing', tone: ''}), null);
  assert.equal(railPresentation({key: 'op_failed'})?.labelKey, 'rail_failed');
  assert.equal(railPresentation(null), null);
});

test('operation targets use discussion, plan and workspace words', () => {
  either(operationTargetText({target: {plan_id: 'plan', plan_version: 3}}), ['Plan v3', '計畫 v3']);
  either(operationTargetText({target: {revision_id: 'rev-9', version: 3}}), ['Plan v3', '計畫 v3']);
  either(operationTargetText({target: {plan_id: 'plan'}}), ['Plan', '計畫']);
  either(operationTargetText({target: {thread_id: 'thread-1', title: 'Rate limit'}}), ['Discussion · Rate limit', '討論 · Rate limit']);
  either(operationTargetText({target: {thread_id: 'thread-1', thread_title: 'thread-1'}}), ['Discussion', '討論']);
  either(operationTargetText({target: {workspace_id: 'ws', workspace_name: 'Acme'}}), ['Acme']);
  either(operationTargetText({target: {workspace_id: 'ws', name: 'ws'}}), ['Workspace', '工作區']);
});

test('demo operations keep the backend fields and a manual asking session', () => {
  const project = demoProject('en');
  const listed = globalRoutes(project, new URL('http://fixture/operations'));
  assert.ok(listed);
  for (const op of listed.data.operations) {
    assert.equal(Object.hasOwn(op, 'finished_at'), false);
    assert.equal(typeof op.created_at, 'number');
    assert.ok(op.created_at > 0);
    assert.ok(op.updated_at >= op.created_at);
    assert.equal(typeof op.actor, 'string');
    assert.ok(op.actor);
    assert.equal(typeof op.target?.repository, 'string');
  }
  const asking = globalRoutes(project, new URL('http://fixture/sessions?attention=true'));
  const manual = asking.data.sessions.find(row => row.provenance === 'manual');
  assert.equal(manual.api_access, 'read_only');
  assert.equal(manual.pending.kind, 'ask_user');
  assert.equal(manual.actor, undefined);
});

test('action labels and reason codes stay out of the raw id', () => {
  either(actionLabel('connection.reference.detach'), ['Detach connection reference', '中斷連線關聯']);
  const label = actionLabel('checkpoint.continue');
  either(label, ['Continue from checkpoint', '從檢查點繼續']);
  assert.notEqual(label, 'checkpoint.continue');
  assert.equal(actionLabel('not.a.real.action'), 'Not a real action');
  assert.notEqual(actionLabel('task.submit'), 'Operation');
  assert.notEqual(actionLabel('task.submit'), '操作');
  const unknown = reasonText('totally_unknown_code');
  either(unknown, ['Not available', '無法取得']);
  assert.notEqual(unknown, 'totally_unknown_code');
  either(reasonText('host_unreachable'), ['Host unreachable', '主機連不上']);
});
