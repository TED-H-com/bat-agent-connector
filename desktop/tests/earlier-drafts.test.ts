import {test} from 'node:test';
import assert from 'node:assert/strict';
import {storageScope} from '../src/state/events.ts';
import {managedStorageScope, migrateManagedStorage} from '../src/state/storage-identity.ts';
import {draftText, earlierDrafts, hideEarlierDraft} from '../src/state/earlier-drafts.ts';

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
    const raw = JSON.stringify({version: 1, values, intent: {key: 'original-key', request: {params: {text: 'must not escape'}}}});
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

test('non-PM text readers omit surrounding metadata and preserve exact authored whitespace', () => {
  const cases: [string, string, unknown, string[]][] = [
    ['draft', 'answer.demo.session-1.tool-1', {identity: 'pending', answers: [' One\n', 'Two']}, [' One\n', 'Two']],
    ['draft', 'wi.edit.wi_fixture', {text: 'Request', fields: {title: 'Title', request: 'Request', agent: 'claude'}, attachments: [{name: 'private'}]}, ['Title', 'Request']],
    ['draft', 'continue.cp_fixture', {text: 'Continue here', fields: {agent: 'claude'}, submission: {key: 'private'}}, ['Continue here']],
    ['create', 'project.root', {version: 1, values: {name: 'Project', description: 'Description', repository: 'private'}}, ['Project', 'Description']],
    ['start', '', {title: 'Title', prompt: 'Prompt', host: 'private', model: 'private'}, ['Title', 'Prompt']],
    ['orchestrate', 'items.{}', {message: 'Message', items: [{title: 'Title', prompt: 'Prompt'}], selection: 'private'}, ['Message', 'Title', 'Prompt']],
    ['labels', '[]', {text: 'label', base_version: 2}, ['label']],
    ['artifact-review', '{}', {reviewText: 'Review', relative_path: 'private'}, ['Review']],
    ['pm', 'discussion-send.ws_fixture', {threadId: 'private', content: 'Discussion'}, ['Discussion']],
  ];
  for (const [family, suffix, value, expected] of cases)
    assert.deepEqual(draftText(family, suffix, JSON.stringify(value)).map(row => row.text), expected, family);
});
