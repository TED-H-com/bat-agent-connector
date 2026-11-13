import {test} from 'node:test';
import assert from 'node:assert/strict';
import {storageScope} from '../src/state/events.ts';
import {managedStorageScope, migrateManagedStorage} from '../src/state/storage-identity.ts';
import {historyRecovery} from '../src/pm-history-recovery.js';

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

test('source-independent withdrawal migrates with the complete scope and original request bytes',()=>{
  const {local,session}=stores(old());
  const workspace_id='ws_'+'a'.repeat(32),work_item_id='wi_'+'b'.repeat(20),link_id='hxl_'+'c'.repeat(32);
  const request={action:'pm.history.unlink',target:{workspace_id,work_item_id,link_id},params:{},preconditions:{expected_link_version:1}};
  const raw=JSON.stringify({version:1,values:{},intent:{key:'original-cleanup-key',request,operation_id:'op_'+'d'.repeat(32)}},null,2);
  const key=`batc.pm.${old()}.history-cleanup.${workspace_id}.pm.history.unlink.${JSON.stringify(request.target)}`;
  local.setItem(key,raw);migrateManagedStorage(identity,local,session);
  const destination=key.replace(old(),target),found=historyRecovery(local,target,workspace_id).read();
  assert.equal(local.getItem(key),raw);assert.equal(local.getItem(destination),raw);
  assert.equal(found.length,1);assert.equal(found[0].storageKey,destination);
  assert.deepEqual(found[0].intent.request,request);assert.equal(found[0].intent.key,'original-cleanup-key');
  local.removeItem(destination);migrateManagedStorage(identity,local,session);
  assert.equal(local.getItem(destination),null,'completed migration cannot resurrect a consumed pending intent');
});

test('managed migration scrubs setup source and destination without replaying or copying selectors', () => {
  for (const unsafe of [false, true]) {
    const {local, session} = stores(old());
    const sourceKey = `batc.managed.setup.${old()}`;
    const intent = {key: 'original-key', operation_id: 'op_' + 'a'.repeat(32), request: {
      action: 'setup.repository', target: {repository: 'o/r'}, params: {
        remote_url: unsafe ? 'https://synthetic-secret@github.com/o/r' : 'https://github.com/o/r',
        secret_ref: 'setup_' + 'b'.repeat(32)}, preconditions: {config_revision: 'c'.repeat(64)}}};
    local.setItem(sourceKey, JSON.stringify({intent, github_secret: 'synthetic-secret'}));
    migrateManagedStorage(identity, local, session);
    const source = local.getItem(sourceKey)!;
    assert.equal(local.getItem(`batc.managed.setup.${target}`), source);
    assert(!source.includes('synthetic-secret'));
    assert(!source.includes('setup_' + 'b'.repeat(32)));
    const saved = JSON.parse(source).intent;
    assert.equal(saved.key, intent.key);
    assert.equal(saved.operation_id, intent.operation_id);
    if (unsafe) assert.equal(saved.problemCode, 'INVALID_CONFIGURATION');
    else assert.equal(saved.lookup_only, true);
  }
});

test('failed privacy scrub blocks migration without copying the unsafe source', () => {
  const {local, session} = stores(old());
  const sourceKey = `batc.managed.setup.${old()}`;
  local.setItem(sourceKey, JSON.stringify({remote_url: 'https://synthetic-secret@github.com/o/r'}));
  const setItem = local.setItem.bind(local);
  local.setItem = (key, value) => {if (key === sourceKey) throw Error('fixture quota'); setItem(key, value);};
  assert.throws(() => migrateManagedStorage(identity, local, session), /fixture quota/);
  assert.equal(local.getItem(`batc.managed.setup.${target}`), null);
  assert.equal(local.getItem(`batc.storage-migration.${target}`), null);
});

for (const area of ['local', 'session'] as const) for (const kind of ['url', 'handle'])
  test(`completed migration cleans ${kind} copies in ${area} without resurrecting consumed targets`, () => {
    const pair = stores(old()), storage = pair[area];
    const sourceKey = `batc.managed.setup.${old()}`, targetKey = `batc.managed.setup.${target}`;
    const marker = `batc.storage-migration.${target}`;
    const secret = kind === 'url' ? 'synthetic-legacy-secret' : 'setup_' + 'b'.repeat(32);
    const intent = {key: 'original-key', operation_id: 'op_' + 'a'.repeat(32), request: {
      action: 'setup.repository', target: {repository: 'o/r'}, params: kind === 'url'
        ? {remote_url: `https://${secret}@github.com/o/r`}
        : {remote_url: 'https://github.com/o/r', secret_ref: secret}, preconditions: {config_revision: 'c'.repeat(64)}}};
    for (const consumed of [false, true]) {
      storage.setItem(sourceKey, JSON.stringify({intent}));
      if (consumed) storage.removeItem(targetKey);
      else storage.setItem(targetKey, JSON.stringify({intent}));
      const receipt = JSON.stringify({version: 1, source: old(), target, status: 'complete'});
      pair.local.setItem(marker, receipt);
      migrateManagedStorage(identity, pair.local, pair.session);
      for (const key of consumed ? [sourceKey] : [sourceKey, targetKey]) {
        const value = storage.getItem(key)!;
        assert(!value.includes(secret));
        const saved = JSON.parse(value).intent;
        assert.equal(saved.key, intent.key); assert.equal(saved.operation_id, intent.operation_id);
        assert.equal(kind === 'url' ? saved.problemCode : saved.lookup_only, kind === 'url' ? 'INVALID_CONFIGURATION' : true);
      }
      if (consumed) assert.equal(storage.getItem(targetKey), null);
      assert.equal(pair.local.getItem(marker), receipt);
      const before = [new Map(pair.local.values), new Map(pair.session.values)];
      migrateManagedStorage(identity, pair.local, pair.session);
      assert.deepEqual([pair.local.values, pair.session.values], before);
    }
  });

test('completed migration scrub failure stops before other writes and retries next start', () => {
  for (const area of ['local', 'session'] as const) for (const failure of ['source', 'target']) {
    const pair = stores(old()), storage = pair[area];
    const sourceKey = `batc.managed.setup.${old()}`, targetKey = `batc.managed.setup.${target}`;
    const unsafe = JSON.stringify({remote_url: 'https://synthetic-secret@github.com/o/r'});
    storage.setItem(sourceKey, unsafe); storage.setItem(targetKey, unsafe);
    pair.local.setItem(`batc.storage-migration.${target}`, JSON.stringify({version: 1, source: old(), target, status: 'complete'}));
    const before = [new Map(pair.local.values), new Map(pair.session.values)];
    const write = storage.setItem.bind(storage);
    storage.setItem = (key, value) => {if (key === (failure === 'source' ? sourceKey : targetKey)) throw Error('fixture quota'); write(key, value);};
    assert.throws(() => migrateManagedStorage(identity, pair.local, pair.session), /fixture quota/);
    // Earlier privacy writes stay scrubbed; no unrelated data or receipt changes.
    if (failure === 'target') before[area === 'local' ? 0 : 1].set(sourceKey, '{"remote_url":""}');
    assert.deepEqual([pair.local.values, pair.session.values], before);
    storage.setItem = write;
    migrateManagedStorage(identity, pair.local, pair.session);
    assert.equal(storage.getItem(sourceKey), '{"remote_url":""}');
    assert.equal(storage.getItem(targetKey), '{"remote_url":""}');
  }
});

test('completed receipt requires its exact source identity and checkpoint proof before cleanup', () => {
  for (const source of [old(41001, {...identity, actor: 'other'}), old(41002)]) {
    const {local, session} = stores(old());
    if (source.includes('other')) local.setItem(`batc.sync.${source}`, '{"cursor":1,"token":"other-proof"}');
    local.setItem(`batc.storage-migration.${target}`, JSON.stringify({version: 1, source, target, status: 'complete'}));
    for (const storage of [local, session]) for (const scope of [source, old(), target])
      storage.setItem(`batc.managed.setup.${scope}`, '{"remote_url":"https://synthetic-secret@github.com/o/r"}');
    const before = [new Map(local.values), new Map(session.values)];
    migrateManagedStorage(identity, local, session);
    assert.deepEqual([local.values, session.values], before);
  }
});

test('occupied target still cleans proven setup copies without importing other drafts', () => {
  const {local, session} = stores(old());
  for (const storage of [local, session]) for (const scope of [old(), target])
    storage.setItem(`batc.managed.setup.${scope}`, '{"remote_url":"https://synthetic-secret@github.com/o/r"}');
  migrateManagedStorage(identity, local, session);
  for (const storage of [local, session]) for (const scope of [old(), target])
    assert.equal(storage.getItem(`batc.managed.setup.${scope}`), '{"remote_url":""}');
  assert.equal(local.getItem(`batc.draft.${target}.send.demo.session-1`), null);
  assert.equal(local.getItem(`batc.storage-migration.${target}`), null);
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
  session.setItem('batc.repo', 'example/private-legacy-repository');
  session.setItem('batc.pr', '17');
  const before = [new Map(local.values), new Map(session.values)];
  migrateManagedStorage(identity, local, session);
  for (const [index, storage] of [local, session].entries()) for (const [key, value] of before[index]) {
    assert.equal(storage.getItem(key), value);
    if (key.includes(old())) assert.equal(storage.getItem(key.replace(old(), target)), value);
  }
  const marker = JSON.parse(local.getItem(`batc.storage-migration.${target}`)!);
  assert.equal(marker.source, old());
  assert.equal(marker.status, 'complete');
  assert.equal(session.getItem(`batc.delivery.${target}.repo`), null, 'global repository has no identity proof');
  assert.equal(session.getItem(`batc.delivery.${target}.pr`), null, 'global PR has no identity proof');
  // Leave only the receipt: destination occupancy must not be what prevents
  // reimporting a request whose copied data has since been consumed/removed.
  for (const storage of [local, session]) for (const key of [...storage.values.keys()])
    if (key.includes(target) && key !== `batc.storage-migration.${target}`) storage.removeItem(key);
  migrateManagedStorage(identity, local, session);
  assert.equal(local.getItem(`batc.draft.${target}.send.demo.session-1`), null, 'a consumed draft must never be resurrected');
  assert.equal(local.getItem(`batc.key.${target}.send.demo.session-1`), null, 'an old operation key must never be reimported');
});

test('delivery migration copies the real scoped repo/pr keys and never adopts unowned globals', () => {
  const {local, session} = stores(old());
  session.setItem(`batc.delivery.${old()}.repo`, 'example/scoped-selection');
  session.setItem(`batc.delivery.${old()}.pr`, '23');
  session.setItem('batc.repo', 'example/unowned-selection');
  session.setItem('batc.pr', '17');
  const before = new Map(session.values);
  migrateManagedStorage(identity, local, session);
  for (const [key, value] of before) assert.equal(session.getItem(key), value);
  assert.equal(session.getItem(`batc.delivery.${target}.repo`), 'example/scoped-selection');
  assert.equal(session.getItem(`batc.delivery.${target}.pr`), '23');
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

// Exact assessment navigation uses typed URL/memory state, never a storage family.
import {assessmentQuery, parseAssessmentSelection} from '../src/pm-assessment-selection.js';
test('new_assessment_pin_is_not_written_to_local_storage', () => {
  const pin = {partition:target, workspace_id:'ws_'+'1'.repeat(32), work_item_id:'wi_'+'1'.repeat(20),
    review_id:'pmqr_'+'1'.repeat(32), operation_id:'op_'+'1'.repeat(32), version:2,
    document_digest:'a'.repeat(64), origin:'overview'};
  const descriptor=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
  Object.defineProperty(globalThis,'localStorage',{configurable:true,get:()=>{throw Error('Selection must not access localStorage');}});
  try {
    assert.equal(parseAssessmentSelection(assessmentQuery(pin,target),target)?.operation_id,pin.operation_id);
    assert.equal(parseAssessmentSelection(assessmentQuery(pin,target),old()),null);
  } finally {
    if(descriptor)Object.defineProperty(globalThis,'localStorage',descriptor);
    else delete (globalThis as any).localStorage;
  }
});

test('guided Review uses existing durable action keys with exact references and original replay identity',()=>{
  const {local,session}=stores(old());
  const request={action:'pm.meeting.draft',target:{workspace_id:'ws_'+'1'.repeat(32),meeting_id:'pmmeet_'+'2'.repeat(32)},
    params:{review_basis:{version:1,results:[{attempt_id:'att_'+'3'.repeat(32),result_id:'res_'+'4'.repeat(32),digest:'sha256:'+'a'.repeat(64),assessment_ref:null}],
      feedback_items:[{id:'feedback',source_ref:{kind:'meeting_note',note_id:'pmmnote_'+'5'.repeat(32)}}],governance_revision_ids:[],follow_up_ref:null}},preconditions:{expected_version:2}};
  const key=`batc.pm.${old()}.${request.action}.${JSON.stringify(request.target)}`;
  const raw=JSON.stringify({version:1,values:{goal_review:'My authored observation',review_basis:JSON.stringify(request.params.review_basis)},
    intent:{key:'original-review-key',request,operation_id:'op_'+'6'.repeat(32)}});
  local.setItem(key,raw);migrateManagedStorage(identity,local,session);
  assert.equal(local.getItem(key.replace(old(),target)),raw);
  assert(![...local.values.keys()].some(key=>/sprint-review|review-progress|review-selection/.test(key)));
});
