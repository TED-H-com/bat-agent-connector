import {test} from 'node:test';
import assert from 'node:assert/strict';
import {groupedSessions, workspaceGroup, matchesSession, runtimeStale, sessionActivity, sessionPill, sessionStatus} from '../src/state/sessions.js';
import {readingSummary} from '../src/conversation.js';
import {STRINGS} from '../src/i18n.js';

const tr = (lang: 'en' | 'zh-TW') => (key: string, vars: Record<string, unknown> = {}) =>
  String((STRINGS as Record<string, Record<string, string>>)[lang][key] ?? key)
    .replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''));

test('workspace groups keep host and recorded workspace identity distinct', () => {
  const rows = [{host: 'a', workspace: 'same', workspace_id: 'one'}, {host: 'a', workspace: 'same', workspace_id: 'two'},
    {host: 'b', workspace: 'same', workspace_id: 'one'}, {host: 'a', workspace: 'same'}, {host: 'a'}];
  assert.equal(groupedSessions(rows).length, 5);
  assert.equal(workspaceGroup({...rows[0], workspace: 'renamed'}).key, workspaceGroup(rows[0]).key);
  assert.notEqual(workspaceGroup(rows[0]).key, workspaceGroup(rows[3]).key);
});
test('loaded search matches factual fields and all query words without project inference', () => {
  const row = {host: 'node-a', workspace: 'Research', session_id: 'sid-exact', worktree_branch: 'fix/42', title: 'Repair parser'};
  assert.equal(matchesSession(row, ' NODE-a repair '), true);
  assert.equal(matchesSession(row, 'sid-exact fix/42'), true);
  assert.equal(matchesSession(row, 'project-research'), false);
});
test('idle, unloaded, unknown and missing sessions are never inferred completed', () => {
  assert.equal(sessionActivity({loaded: false, streaming: false}).key, 'obs_value_not_streaming');
  assert.equal(sessionActivity({loaded: false}).key, 'sessions_not_loaded');
  assert.equal(sessionActivity({state: {loading: 'not_loaded'}}).key, 'sessions_not_loaded');
  assert.equal(sessionActivity({loaded: true}).key, 'sessions_activity_unknown');
  assert.equal(sessionActivity({}).key, 'sessions_activity_unknown');
  assert.equal(sessionActivity({streaming: true, stale: true}).key, 'sessions_stale');
  assert.equal(sessionActivity({streaming: false, state: {enumeration: 'gone'}}).key, 'sessions_not_seen');
  assert.equal(sessionActivity({state: {lifecycle: 'ended'}}).key, 'obs_value_ended');
  assert.equal(sessionActivity({pending: {kind: 'ask_user'}, streaming: false}).key, 'pending_ask_user');
  assert.equal(sessionActivity({api_access: 'managed', status: 'failed'}).key, 'op_failed');
  assert.equal(sessionActivity({api_access: 'managed', status: 'needs_attention'}).key, 'workspace_needs_you');
});

test('session status and pills speak up only when the session needs the user, is running, or is out of sync', () => {
  assert.equal(sessionStatus({api_access: 'read_only', streaming: true}), 'running');
  assert.equal(sessionStatus({api_access: 'read_only', loaded: false}), null);
  assert.equal(sessionStatus({api_access: 'read_only', streaming: false}), null);
  assert.equal(sessionStatus({api_access: 'managed', streaming: true}), 'running');
  assert.equal(sessionStatus({api_access: 'managed', streaming: false}), null);
  assert.equal(sessionStatus({api_access: 'managed', pending: {kind: 'permission'}}), 'asking_you');
  assert.equal(sessionStatus({api_access: 'managed', pending: {kind: 'ask_user'}}), 'asking_you');
  assert.equal(sessionStatus({api_access: 'managed', pending: {kind: 'other'}}), 'needs_you');
  assert.equal(sessionStatus({api_access: 'managed', state: {lifecycle: 'ended'}, streaming: true}), null);
  assert.equal(sessionStatus({api_access: 'managed', gone_at: '2026-01-01', streaming: false}), 'stale');
  assert.equal(sessionStatus({api_access: 'managed', stale: true, streaming: true}), 'stale');
  assert.equal(sessionStatus({api_access: 'managed', status: 'failed'}), 'failed');
  assert.equal(sessionStatus({api_access: 'managed', status: 'needs_attention'}), 'needs_you');
  assert.equal(sessionStatus({api_access: 'managed'}), null);
  assert.equal(sessionPill({loaded: false}), null);
  assert.deepEqual(sessionPill({pending: {kind: 'ask_user'}}), {key: 'sessions_pill_waiting', tone: 'stale', status: 'asking_you'});
  assert.deepEqual(sessionPill({stale: true}), {key: 'sessions_pill_stale', tone: 'stale', status: 'stale'});
  assert.deepEqual(sessionPill({streaming: true}), {key: 'sessions_pill_running', tone: 'info', status: 'running'});
  assert.deepEqual(sessionPill({api_access: 'managed', status: 'failed'}), {key: 'op_failed', tone: 'bad', status: 'failed'});
});

test('fresh enumeration does not freshen stale runtime or pending evidence', () => {
  const row = {stale: false, streaming: true, fields_stale: true, state: {enumeration: 'present'}};
  assert.equal(sessionActivity(row).key, 'sessions_stale');
  assert.equal(sessionActivity({...row, pending: {kind: 'ask_user'}}).key, 'pending_ask_user');
  assert.equal(runtimeStale({...row, pending: {kind: 'ask_user'}}), true);
  assert.equal(sessionActivity({streaming: true, state: {evidence: {activity: {stale: true}}}}).key, 'sessions_stale');
});

test('reading summary explains incomplete reasons without alarming partial-history wording', () => {
  const zh = tr('zh-TW');
  assert.deepEqual(readingSummary(zh, {unread_count: 34, complete: false, incomplete_reasons: ['message_ids'], unindexed_count: 2}), {
    line: '34+ 則未讀',
    detail: '部分訊息沒有 ID，未計入未讀數。',
  });
  assert.deepEqual(readingSummary(zh, {unread_count: 120, complete: false, incomplete_reasons: ['archive_budget']}), {
    line: '120+ 則未讀 · 顯示最近 3,000 則訊息',
    detail: '',
  });
  assert.deepEqual(readingSummary(zh, {unread_count: 12, complete: false, incomplete_reasons: ['archive_unconfirmed']}), {
    line: '12+ 則未讀 · 較早的訊息可能未載入',
    detail: 'BAT 未確認歷史紀錄是否已到結尾。',
  });
  assert.deepEqual(readingSummary(zh, {unread_count: 9, complete: false}), {
    line: '9+ 則未讀 · 較早的訊息可能未載入',
    detail: '',
  });
  const en = tr('en');
  assert.deepEqual(readingSummary(en, {unread_count: 34, complete: false, incomplete_reasons: ['message_ids'], unindexed_count: 2}), {
    line: '34+ unread',
    detail: 'Some messages have no ID and are not counted.',
  });
  assert.deepEqual(readingSummary(en, {unread_count: 120, complete: false, incomplete_reasons: ['archive_budget']}), {
    line: '120+ unread · Showing the latest 3,000 messages',
    detail: '',
  });
  assert.deepEqual(readingSummary(en, {unread_count: 12, complete: false, incomplete_reasons: ['archive_unconfirmed']}), {
    line: '12+ unread · Earlier messages may not be loaded',
    detail: 'BAT did not confirm the end of the history.',
  });
  assert.deepEqual(readingSummary(en, {unread_count: 9, complete: false}), {
    line: '9+ unread · earlier messages may not be loaded',
    detail: '',
  });
});

for (const lang of ['en', 'zh-TW'] as const) {
  test(`reading summary retains counts and all explanations for combined reasons (${lang})`, () => {
    const t = tr(lang), ids = t('conversation_message_ids_detail');
    const summary = (reasons: string[], count = 34) => readingSummary(t,
      {unread_count: count, complete: false, incomplete_reasons: reasons, unindexed_count: 2});
    assert.deepEqual(summary(['archive_budget', 'message_ids']), {
      line: lang === 'en' ? '34+ unread · Showing the latest 3,000 messages' : '34+ 則未讀 · 顯示最近 3,000 則訊息',
      detail: ids,
    });
    assert.deepEqual(summary(['archive_unconfirmed', 'message_ids']), {
      line: lang === 'en' ? '34+ unread · Earlier messages may not be loaded' : '34+ 則未讀 · 較早的訊息可能未載入',
      detail: `${t('conversation_archive_unconfirmed_detail')} ${ids}`,
    });
    assert.equal(summary(['archive_budget'], 0).line,
      lang === 'en' ? '0+ unread · Showing the latest 3,000 messages' : '0+ 則未讀 · 顯示最近 3,000 則訊息');
  });
  test(`complete and unknown reading counts retain their meaning (${lang})`, () => {
    const t = tr(lang);
    for (const count of [0, 34]) assert.deepEqual(readingSummary(t, {unread_count: count, complete: true, incomplete_reasons: []}), {
      line: lang === 'en' ? `Last observed: ${count} unread` : `上次觀察：${count} 則未讀`, detail: '',
    });
    for (const reading of [null, {}, {unread_count: null, complete: false, incomplete_reasons: ['archive_budget']},
      {complete: false, incomplete_reasons: ['message_ids']}]) assert.deepEqual(readingSummary(t, reading), {
      line: lang === 'en' ? 'Conversation has not been observed' : '尚未讀取對話紀錄', detail: '',
    });
    assert.equal(t('workspace'), lang === 'en' ? 'Workspace' : '工作區');
    assert.equal(t('sessions_id'), lang === 'en' ? 'Session ID' : '工作階段 ID');
  });
}
