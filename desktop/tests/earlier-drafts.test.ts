import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {storageScope} from '../src/state/events.ts';
import {managedStorageScope, migrateManagedStorage} from '../src/state/storage-identity.ts';
import {draftText, earlierDrafts, hideEarlierDraft, firstEarlierDraftNotice} from '../src/state/earlier-drafts.ts';

const identity = {server_id: 'fixture-server', principal_id: 'fixture-principal', actor: 'fixture-operator'};
const old = (port = 41001, who = identity, endpoint = `http://127.0.0.1:${port}/`) =>
  storageScope(endpoint, who.actor, who.server_id, who.principal_id);
class MemoryStorage {
  values = new Map<string, string>();
  get length() {return this.values.size;}
  key(index: number) {return [...this.values.keys()][index] ?? null;}
  getItem(key: string) {return this.values.get(key) ?? null;}
  setItem(key: string, value: string) {this.values.set(key, value);}
  removeItem(key: string) {this.values.delete(key);}
  clear() {this.values.clear();}
}
function seed(local: MemoryStorage, scope: string) {
  local.setItem(`batc.sync.${scope}`, JSON.stringify({cursor: 1, token: 'fixture-proof'}));
  local.setItem(`batc.draft.${scope}.send.demo.session-1`, 'Typed message');
  local.setItem(`batc.draft.${scope}.send.demo.session-1.queue`, 'true');
  local.setItem(`batc.key.${scope}.send.demo.session-1`, JSON.stringify({key: 'original-key'}));
}
test('earlier drafts expose only text and hide without changing original or current data', async () => {
  const local = new MemoryStorage(); seed(local, old()); seed(local, old(41002));
  local.setItem(`batc.draft.${managedStorageScope(identity)}.send.demo.session-1`, 'Current draft');
  const before = new Map(local.values);
  const entries = await earlierDrafts(identity, local);
  assert.deepEqual(entries.map(entry => entry.text), ['Typed message', 'Typed message']);
  assert.deepEqual(local.values, before);
  await hideEarlierDraft(identity, local, entries[0]);
  assert.equal((await earlierDrafts(identity, local)).length, 1);
  for (const [key, value] of before) assert.equal(local.getItem(key), value);
});
test('identity boundaries, non-loopback endpoints, orphans and migrated sources are excluded', async () => {
  for (const scope of [old(41001, {...identity, principal_id: 'other'}), old(41001, {...identity, server_id: 'other'}),
    old(41001, {...identity, actor: 'other'}), old(41001, identity, 'http://example.test:41001/'),
    old(41001, identity, 'https://localhost:41001/')]) {
    const local = new MemoryStorage(); seed(local, scope);
    assert.deepEqual(await earlierDrafts(identity, local), []);
  }
  const local = new MemoryStorage(); seed(local, old());
  local.removeItem(`batc.sync.${old()}`);
  assert.deepEqual(await earlierDrafts(identity, local), []);
  seed(local, old()); seed(local, old(41001, {...identity, actor: identity.actor + '.send'}));
  assert.deepEqual(await earlierDrafts(identity, local), []);
  const migrated = new MemoryStorage(); seed(migrated, old());
  migrateManagedStorage(identity, migrated, new MemoryStorage());
  assert.deepEqual(await earlierDrafts(identity, migrated), []);
});
test('unreadable storage and malformed drafts are skipped with content-free diagnostics', async () => {
  const diagnostics: string[] = [], local = new MemoryStorage(); seed(local, old());
  local.setItem(`batc.pm.${old()}.pm.message.append.{}`, 'broken private text');
  assert.equal((await earlierDrafts(identity, local, code => diagnostics.push(code))).length, 1);
  assert.deepEqual(diagnostics, ['draft_recovery_unreadable']);
  local.getItem = () => {throw Error('private storage details');};
  assert.deepEqual(await earlierDrafts(identity, local, code => diagnostics.push(code)), []);
  assert.ok(diagnostics.every(code => code === 'draft_recovery_unreadable'));
});

test('PM writers recover authored fields and collections without hidden selectors or original requests', () => {
  const cases: [string, Record<string, string>, string[]][] = [
    ['work.start', {instruction: 'Do this work', model: 'selected-model', profile: 'selected-profile', review: '{"receipt":"private"}'}, ['Do this work']],
    ['repository.continue', {basis: 'Reviewed basis', prompt: 'Implement this', title: 'Work title', review: 'private'}, ['Reviewed basis', 'Implement this', 'Work title']],
    ['pm.run.bat.analyze', {purpose: 'Assess scope', model: 'selected-model', accepted_context: 'private'}, ['Assess scope']],
    ['pm.governance.record', {title: 'Risk', description: 'Impact', rationale: 'Reason', status: 'open', source_refs: 'private'}, ['Risk', 'Impact', 'Reason']],
    ['pm.quality.review.record', {rationale: 'Review reason', reason_0: 'Criterion reason', proof_0: 'receipt', verdict_0: 'accepted'}, ['Review reason', 'Criterion reason']],
    ['pm.meeting.draft', {why: 'Planning purpose', decisions: JSON.stringify([{id: 'decision_1', text: 'Decision text', rationale: 'Decision reason', source_refs: ['private']}]), follow_ups: JSON.stringify([{id: 'followup_1', text: 'Follow this up', owner_actor: 'other', expected_effect: 'Expected effect', verification: 'Verify it'}])}, ['Planning purpose', 'Decision text', 'Decision reason', 'Follow this up', 'Expected effect', 'Verify it']],
  ];
  for (const [action, values, expected] of cases) {
    const raw = JSON.stringify({version: 1, values});
    assert.deepEqual(draftText('pm', `nested.form.${action}.{"workspace_id":"ws_fixture"}`, raw).map(row => row.text), expected, action);
  }
  for (const family of ['key', 'sync', 'managed.setup', 'repair', 'capture', 'task-control', 'session-bat', 'permissions',
    'model-preferences', 'project-skills', 'approvals', 'cleanup.draft', 'cleanup.pending', 'cleanup.intent', 'tree', 'sessions',
    'attention', 'composer-shortcut', 'delivery', 'pm.batch-selection', 'pm.meeting-navigation'])
    assert.deepEqual(draftText(family, 'selection', JSON.stringify({text: 'must not escape', values: {text: 'must not escape'}})), [], family);
});

test('attachment prompt mirrors appear once while orphan or differing text stays available', async () => {
  const local = new MemoryStorage(); seed(local, old());
  local.setItem(`batc.published.${old()}`, JSON.stringify({title: '', prompt: 'Mirrored prompt', agent: 'claude', model: ''}));
  local.setItem(`batc.draft.${old()}.published`, JSON.stringify({text: 'Mirrored prompt', attachments: []}));
  assert.deepEqual((await earlierDrafts(identity, local)).map(row => row.text).sort(), ['Mirrored prompt', 'Typed message']);
  local.removeItem(`batc.published.${old()}`);
  assert.deepEqual((await earlierDrafts(identity, local)).map(row => row.text).sort(), ['Mirrored prompt', 'Typed message']);
  local.setItem(`batc.published.${old()}`, JSON.stringify({title: '', prompt: 'Different prompt', agent: 'claude', model: ''}));
  assert.deepEqual((await earlierDrafts(identity, local)).map(row => row.text).sort(), ['Different prompt', 'Mirrored prompt', 'Typed message']);
});

test('real writer snapshots distinguish retained requests from ordinary text', async () => {
  for (const name of ['start', 'published', 'dispatch', 'orchestrate', 'labels', 'forms']) {
    const rows = JSON.parse(readFileSync(new URL(`./fixtures/earlier-drafts-${name}.json`, import.meta.url), 'utf8'));
    for (const row of rows) {
      const value = JSON.parse(row.raw), local = new MemoryStorage();
      local.setItem(`batc.sync.${old()}`, JSON.stringify({cursor: 1, token: 'fixture-proof'}));
      local.setItem(`batc.${row.family}.${old()}${row.suffix ? '.' + row.suffix : ''}`, row.raw);
      const entries = await earlierDrafts(identity, local);
      assert.ok(entries.length, row.family);
      const state = value.intent || value.submission || value.accept ? 'possibly-sent' : 'draft';
      assert.ok(entries.every(entry => entry.state === state), row.family + ' ' + state);
      assert.ok(entries.every(entry => entry.field && entry.href.startsWith('#/')), row.family);
      assert.deepEqual(entries.map(entry => entry.text), draftText(row.family, row.suffix, row.raw).map(field => field.text));
    }
  }
});

test('retained session requests with exact or hashed sibling keys are possibly sent', async () => {
  for (const suffix of ['', '.' + 'a'.repeat(24)]) for (const raw of [
    JSON.stringify({key: 'original-key', op: 'op_' + 'a'.repeat(32)}),
    JSON.stringify({key: 'original-key'}), 'legacy-key', '{damaged', '',
  ]) {
    const local = new MemoryStorage(); seed(local, old());
    local.removeItem(`batc.key.${old()}.send.demo.session-1`);
    local.setItem(`batc.key.${old()}.send.demo.session-1${suffix}`, raw);
    const [entry] = await earlierDrafts(identity, local, () => {});
    assert.equal(entry.state, 'possibly-sent');
    assert.equal(entry.href, raw.includes('op_') ? '#/op/op_' + 'a'.repeat(32) : '#/session/demo/session-1');
  }
});

test('unreadable sibling key never becomes an ordinary draft', async () => {
  const local = new MemoryStorage(); seed(local, old());
  const read = local.getItem.bind(local), diagnostics: string[] = [];
  local.getItem = key => {if (key.startsWith('batc.key.')) throw Error('private'); return read(key);};
  const [entry] = await earlierDrafts(identity, local, code => diagnostics.push(code));
  assert.equal(entry.state, 'possibly-sent');
  assert.equal(entry.href, '#/session/demo/session-1');
  assert.ok(diagnostics.length > 0);
  assert.ok(diagnostics.every(code => code === 'draft_recovery_unreadable'));
});

test('generated continuation, repair prompts, hidden titles and seeded goals are not authored drafts', () => {
  assert.deepEqual(draftText('draft', 'continue.cp_fixture', JSON.stringify({text: 'Generated Work summary', attachments: []})), []);
  assert.deepEqual(draftText('dispatch', 'prj_' + 'a'.repeat(20) + '.wi_' + 'b'.repeat(20), JSON.stringify({prompt: 'Server repair instructions', title: ''})), []);
  assert.deepEqual(draftText('pm', 'overview-assessment.ws_fixture.pm.thread.create.{"workspace_id":"ws_fixture"}',
    JSON.stringify({version: 1, values: {title: 'Project overview assessment', _version: '1', _refs: '[]'}})), []);
  for (const action of ['pm.product_goal.revise', 'pm.dod.revise'])
    assert.deepEqual(draftText('pm', action + '.{"workspace_id":"ws_fixture"}', JSON.stringify({version: 1,
      values: {text: 'Existing server text', rationale: 'My reason'}})).map(entry => entry.text), ['My reason']);
  assert.deepEqual(draftText('pm', 'pm.issue.update.{}', JSON.stringify({version: 1, values: {title: 'No such writer'}})), []);
});

test('shortcuts are skipped and a malformed collection does not discard other fields', async () => {
  const local = new MemoryStorage(); seed(local, old());
  const diagnostics: string[] = [];
  local.setItem(`batc.published.${old()}.shortcut`, 'ctrl-enter');
  local.setItem(`batc.pm.${old()}.pm.meeting.draft.{"workspace_id":"ws_fixture"}`, JSON.stringify({version: 1,
    values: {why: 'Keep the purpose', decisions: '{broken', follow_ups: JSON.stringify([{text: 'Keep this follow-up'}])}}));
  assert.deepEqual((await earlierDrafts(identity, local, code => diagnostics.push(code))).map(entry => entry.text).sort(),
    ['Typed message', 'Keep the purpose', 'Keep this follow-up'].sort());
  assert.deepEqual(diagnostics, ['draft_recovery_unreadable']);
});

test('notice inspection does not consume the notice', () => {
  const local = new MemoryStorage();
  assert.equal(firstEarlierDraftNotice(identity, local), true);
  assert.equal(firstEarlierDraftNotice(identity, local), true);
  assert.equal(local.length, 0);
});


test('mirrored text preserves submission evidence even when only the attachment knows it', async () => {
  const local = new MemoryStorage(); seed(local, old());
  local.setItem(`batc.published.${old()}`, JSON.stringify({target: null, source_ref: '', agent: 'claude', prompt: 'Original prompt', model: '', title: ''}));
  local.setItem(`batc.draft.${old()}.published`, JSON.stringify({text: 'Original prompt', attachments: [],
    submission: {key: 'original-key', operation_id: 'op_' + 'a'.repeat(32), request: {action: 'repository.continue'}, snapshot: '{}', scope: 'published'}}));
  const entries = (await earlierDrafts(identity, local)).filter(entry => entry.text === 'Original prompt');
  assert.equal(entries.length, 1); assert.equal(entries[0].state, 'possibly-sent');
  assert.equal(entries[0].href, '#/op/op_' + 'a'.repeat(32));
});


test('real session writer output preserves its hashed operation evidence', async () => {
  const rows = JSON.parse(readFileSync(new URL('./fixtures/earlier-drafts-session.json', import.meta.url), 'utf8'));
  const local = new MemoryStorage(); local.setItem(`batc.sync.${old()}`, JSON.stringify({cursor: 1, token: 'fixture-proof'}));
  for (const row of rows) local.setItem(`batc.${row.family}.${old()}.${row.suffix}`, row.raw);
  const [entry] = await earlierDrafts(identity, local);
  assert.equal(entry.text, 'Original session message'); assert.equal(entry.state, 'possibly-sent');
  assert.equal(entry.href, '#/op/op_' + 'a'.repeat(32));
});

test('ambiguous session names link to the session list without guessing the host boundary', async () => {
  const local = new MemoryStorage();
  local.setItem(`batc.sync.${old()}`, JSON.stringify({cursor: 1, token: 'fixture-proof'}));
  local.setItem(`batc.draft.${old()}.send.demo.example.session-1`, 'Retained message');
  local.setItem(`batc.key.${old()}.send.demo.example.session-1`, JSON.stringify({key: 'original-key'}));
  const [entry] = await earlierDrafts(identity, local);
  assert.equal(entry.state, 'possibly-sent'); assert.equal(entry.href, '#/sessions');
});

test('recovery warnings and field labels have matching English and Traditional Chinese strings', async () => {
  const {earlierDraftStrings} = await import('../src/earlier-drafts-strings.js');
  const {earlierFieldLabel} = await import('../src/earlier-drafts.js');
  assert.deepEqual(Object.keys(earlierDraftStrings.en).sort(), Object.keys(earlierDraftStrings['zh-TW']).sort());
  for (const locale of ['en', 'zh-TW']) {
    const strings = earlierDraftStrings[locale], t = (key: string) => strings[key] || key;
    assert.ok(Object.values(strings).every(value => typeof value === 'string' && value.trim()));
    for (const field of ['title', 'goal', 'rationale', 'reason_0', 'follow_ups.0.verification', 'items.1.prompt', 'reviewText'])
      assert.ok(!earlierFieldLabel(t, field).includes('earlier_field_'), `${locale}: ${field}`);
    assert.equal(earlierFieldLabel(t, 'reason_0'), t('earlier_field_reason') + ' 1');
  }
});
