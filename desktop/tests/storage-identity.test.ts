import {test} from 'node:test';
import assert from 'node:assert/strict';
import {storageScope} from '../src/state/events.ts';
import {managedStorageScope, migrateManagedStorage} from '../src/state/storage-identity.ts';

const identity = {server_id: 'fixture-server', principal_id: 'fixture-principal', actor: 'fixture.operator'};
const old = (port = 41001, who = identity) => storageScope(`http://127.0.0.1:${port}/`, who.actor, who.server_id, who.principal_id);
const target = managedStorageScope(identity);
class MemoryStorage {
  values = new Map<string, string>();
  get length() {return this.values.size;}
  key(index: number) {return [...this.values.keys()][index] ?? null;}
  getItem(key: string) {return this.values.get(key) ?? null;}
  setItem(key: string, value: string) {this.values.set(key, value);}
  removeItem(key: string) {this.values.delete(key);}
  clear() {this.values.clear();}
}
function stores(...sources: string[]) {
  const local = new MemoryStorage(), session = new MemoryStorage();
  for (const source of sources) {
    local.setItem(`batc.sync.${source}`, JSON.stringify({cursor: 1, token: 'fixture-proof'}));
    local.setItem(`batc.draft.${source}.send.demo.session-1`, 'exact old draft');
    local.setItem(`batc.key.${source}.send.demo.session-1`, JSON.stringify({key: 'original-key', op: 'op_' + 'a'.repeat(32)}));
  }
  return {local, session};
}

test('managed identity is independent of address and isolates every identity component', () => {
  for (const key of ['actor', 'server_id', 'principal_id'])
    assert.notEqual(managedStorageScope({...identity, [key]: 'other'}), target);
  assert.notEqual(target, old());
  assert.notEqual(storageScope('https://one.example/', identity.actor, identity.server_id, identity.principal_id),
    storageScope('https://two.example/', identity.actor, identity.server_id, identity.principal_id));
});

test('one exact legacy identity copies every scoped key byte for byte and retains originals and mapping', () => {
  const {local, session} = stores(old());
  // All audited families, including nested PM selectors and derived form namespaces.
  const families = ['draft', 'key', 'pm', 'pm.batch-selection', 'pm.repair-selection', 'pm.quality-navigation',
    'pm.verifier', 'pm.governance.selection', 'pm.meeting-navigation', 'pm.review-navigation', 'managed.setup',
    'create', 'repair', 'capture', 'task-control', 'labels', 'session-bat', 'permissions', 'cleanup.draft',
    'cleanup.pending', 'cleanup.intent', 'approvals', 'start', 'orchestrate', 'published', 'dispatch', 'artifact-review',
    'model-preferences', 'project-skills', 'tree', 'sessions', 'attention', 'composer-shortcut'];
  for (const storage of [local, session]) for (const family of families)
    storage.setItem(`batc.${family}.${old()}.work-start.exact-work.exact-attempt`, JSON.stringify({key: 'original', work: 'original', attempt: 'original'}));
  local.setItem('batc.lang', 'zh-TW');
  local.setItem('batc.draft.unrelated', 'unchanged');
  const before = [new Map(local.values), new Map(session.values)];
  migrateManagedStorage(identity, local, session);
  for (const [index, storage] of [local, session].entries()) for (const [key, value] of before[index]) {
    assert.equal(storage.getItem(key), value);
    if (key.includes(old())) assert.equal(storage.getItem(key.replace(old(), target)), value);
  }
  const marker = JSON.parse(local.getItem(`batc.storage-migration.${target}`)!);
  assert.equal(marker.source, old());
  assert.equal(marker.status, 'complete');
  // Leave only the receipt: destination occupancy must not be what prevents
  // reimporting a request whose copied data has since been consumed/removed.
  for (const storage of [local, session]) for (const key of [...storage.values.keys()])
    if (key.includes(target) && key !== `batc.storage-migration.${target}`) storage.removeItem(key);
  migrateManagedStorage(identity, local, session);
  assert.equal(local.getItem(`batc.draft.${target}.send.demo.session-1`), null, 'a consumed draft must never be resurrected');
  assert.equal(local.getItem(`batc.key.${target}.send.demo.session-1`), null, 'an old operation key must never be reimported');
});

for (const field of ['server_id', 'principal_id', 'actor']) test(`migration cannot cross ${field}`, () => {
  const {local, session} = stores(old(41001, {...identity, [field]: 'other'}));
  const before = [...local.values];
  migrateManagedStorage(identity, local, session);
  assert.deepEqual([...local.values], before);
});

test('multiple legacy ports are left untouched even when their contents match', () => {
  const {local, session} = stores(old(), old(41002));
  const before = [...local.values];
  migrateManagedStorage(identity, local, session);
  assert.deepEqual([...local.values], before);
});

test('a second matching old port without a checkpoint still makes migration ambiguous', () => {
  const {local, session} = stores(old());
  local.setItem(`batc.draft.${old(41002)}.send.demo.session-1`, 'Orphan draft from another restart');
  const before = [...local.values];
  migrateManagedStorage(identity, local, session);
  assert.deepEqual([...local.values], before);
});

test('an existing destination in either storage area prevents every legacy copy', () => {
  for (const area of ['local', 'session'] as const) {
    const pair = stores(old());
    pair[area].setItem(`batc.pm.batch-selection.${target}.workspace`, 'current selection');
    const before = [new Map(pair.local.values), new Map(pair.session.values)];
    migrateManagedStorage(identity, pair.local, pair.session);
    assert.deepEqual([pair.local.values, pair.session.values], before);
  }
});

test('missing identity checkpoint, legacy identity, malformed and nonlocal endpoints stay untouched', () => {
  for (const source of [storageScope('http://127.0.0.1:41001/', identity.actor),
    storageScope('https://central.example/', identity.actor, identity.server_id, identity.principal_id),
    storageScope('HTTP://127.0.0.1:41001/', identity.actor, identity.server_id, identity.principal_id),
    'malformed%:fixture-server:fixture-principal:fixture.operator']) {
    const {local, session} = stores(source);
    const before = [...local.values];
    migrateManagedStorage(identity, local, session);
    assert.deepEqual([...local.values], before);
  }
  const {local, session} = stores(old());
  local.removeItem(`batc.sync.${old()}`);
  const before = [...local.values];
  migrateManagedStorage(identity, local, session);
  assert.deepEqual([...local.values], before);
});

test('a failed copy rolls back its destination and preserves all original keys', () => {
  const {local, session} = stores(old());
  const original = local.setItem.bind(local), before = [...local.values];
  local.setItem = (key, value) => {if (key.startsWith(`batc.draft.${target}`)) throw Error('fixture quota'); original(key, value);};
  assert.throws(() => migrateManagedStorage(identity, local, session), /fixture quota/);
  assert.deepEqual([...local.values], before);
});

test('an interrupted migration refuses partial destination data without replay or deletion', () => {
  const {local, session} = stores(old());
  local.setItem(`batc.storage-migration.${target}`, JSON.stringify({version: 1, status: 'copying', source: old(), target}));
  const before = [...local.values];
  assert.throws(() => migrateManagedStorage(identity, local, session), /migration/);
  assert.deepEqual([...local.values], before);
});
