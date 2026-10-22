import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {BROWSER_TOKEN_KEY, credentialRefusals, isCredentialRefusal} from '../src/credential-refusals.js';

function store() {
  const values = new Map<string, string>();
  return {values, getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key)};
}

test('importing credential helpers does not read browser storage', async () => {
  let reads = 0;
  const names = ['sessionStorage', 'localStorage'];
  const descriptors = names.map(name => Object.getOwnPropertyDescriptor(globalThis, name));
  try {
    for (const name of names) Object.defineProperty(globalThis, name, {configurable: true,
      get: () => {reads++; return store();}});
    await import('../src/credential-refusals.js?without-startup');
    assert.equal(reads, 0);
  } finally {
    names.forEach((name, i) => {if (descriptors[i]) Object.defineProperty(globalThis, name, descriptors[i]!);
      else Reflect.deleteProperty(globalThis, name);});
  }
});

for (const replacement of [null, 'new-token']) for (const event of [false, true]) {
  test(`another tab forgetting or replacing its token retains the old refusal ${replacement !== null} ${event}`, () => {
    const storage = store(); storage.setItem(BROWSER_TOKEN_KEY, 'example');
    const options = {stores: () => [storage], endpoint: () => 'http://localhost'};
    const first = credentialRefusals(options), second = credentialRefusals(options);
    first.refuse('example', 401, 'GET', '/events');
    const key = [...storage.values.keys()].find(key => key.startsWith('batc.credential-refused.'))!;
    second.forget('example');
    if (replacement) storage.setItem(BROWSER_TOKEN_KEY, replacement); else storage.removeItem(BROWSER_TOKEN_KEY);
    if (event) first.storageChanged({key});
    assert.equal(first.read('example')?.status, 401);
  });
}

test('prepared credential lookups and retries are synchronous and domain separated', () => {
  for (const token of ['example', '權杖🙂', 'x'.repeat(1000), ...Array.from({length: 128}, (_, i) => 'x'.repeat(i + 1))]) {
    const storage = store(); storage.setItem(BROWSER_TOKEN_KEY, token);
    const gate = credentialRefusals({stores: () => [storage], endpoint: () => 'http://localhost'});
    gate.prepare(token);
    assert.equal(gate.read(token), null);
    gate.refuse(token, 401, 'GET', '/capabilities');
    const digest = createHash('sha256').update('batc.dashboard.credential-refusal.v1\0' + token).digest('hex');
    assert.equal(JSON.parse(storage.getItem('batc.credential-refused.http%3A%2F%2Flocalhost.' + digest)!).status, 401);
    assert.equal(gate.read(token)?.status, 401);
    gate.retry(token);
    assert.equal(gate.read(token), null);
  }
});

test('an unavailable owning store retains the in-memory refusal', () => {
  const session = store(), local = store(); local.setItem(BROWSER_TOKEN_KEY, 'example');
  const gate = credentialRefusals({stores: () => [session, local], endpoint: () => 'http://localhost'});
  gate.refuse('example', 401, 'GET', '/events');
  local.getItem = () => {throw Error('Unavailable');};
  assert.equal(gate.read('example')?.status, 401);
});

test('another tab can clear a persisted refusal without leaving the original tab paused', () => {
  const storage = store(); storage.setItem(BROWSER_TOKEN_KEY, 'example');
  const options = {stores: () => [storage], endpoint: () => 'http://localhost'};
  const first = credentialRefusals(options), second = credentialRefusals(options);
  first.prepare('example'); second.prepare('example');
  first.refuse('example', 401, 'GET', '/events');
  assert.equal(second.read('example')?.status, 401);
  second.retry('example');
  assert.equal(first.read('example'), null);
});

test('refusal persists for its credential and endpoint until an explicit retry', async () => {
  const storage = store();
  storage.setItem('batc.draft.example', 'original');
  storage.setItem(BROWSER_TOKEN_KEY, 'example-credential');
  let endpoint = 'http://localhost:8000';
  const options = {stores: () => [storage], endpoint: () => endpoint};
  const gate = credentialRefusals(options);
  await gate.refuse('example-credential', 401, 'GET', '/capabilities');
  assert.equal((await credentialRefusals(options).read('example-credential'))?.status, 401);
  assert.equal(await gate.read('replacement'), null);
  endpoint = 'http://localhost:8001';
  assert.equal(await gate.read('example-credential'), null);
  endpoint = 'http://localhost:8000';
  assert.equal((await gate.read('example-credential'))?.status, 401);
  assert.ok(!JSON.stringify([...storage.values].filter(([key]) => key !== BROWSER_TOKEN_KEY)).includes('example-credential'));
  await gate.retry('example-credential');
  assert.equal(await gate.read('example-credential'), null);
  assert.deepEqual([...storage.values], [['batc.draft.example', 'original'], [BROWSER_TOKEN_KEY, 'example-credential']]);
});

test('unavailable storage keeps refusal in memory and transient failures do not refuse', async () => {
  const gate = credentialRefusals({stores: () => {throw Error('Unavailable');}, endpoint: () => 'http://localhost'});
  await gate.refuse('example', 503, 'GET', '/capabilities');
  assert.equal(await gate.read('example'), null);
  await gate.refuse('example', 403, 'GET', '/capabilities');
  assert.equal((await gate.read('example'))?.status, 403);
  await gate.retry('example');
  assert.equal(await gate.read('example'), null);
});

test('only required Dashboard reads turn a permission refusal into a credential refusal', () => {
  for (const path of ['/capabilities', '/bootstrap', '/events?after=2']) {
    assert.equal(isCredentialRefusal(403, 'GET', path), true);
    assert.equal(isCredentialRefusal(403, 'POST', path), false);
    assert.equal(isCredentialRefusal(503, 'GET', path), false);
  }
  for (const path of ['/operations?wait=3', '/artifacts/uploads/op_example/content',
    '/artifacts/art_example/revisions/1/content', '/projects', '/browser-session', '/managed/stop']) {
    for (const method of ['GET', 'POST']) {
      assert.equal(isCredentialRefusal(403, method, path), false);
      assert.equal(isCredentialRefusal(401, method, path), true);
    }
  }
});

test('refusal uses the token store and forgetting removes only that credential record', async () => {
  for (const remembered of [false, true]) {
    const session = store(), local = store();
    session.setItem(BROWSER_TOKEN_KEY, 'example');
    if (remembered) local.setItem(BROWSER_TOKEN_KEY, 'example');
    local.setItem('batc.key.example', '{"key":"original"}');
    const gate = credentialRefusals({stores: () => [session, local], endpoint: () => 'http://localhost'});
    await gate.refuse('example', 403, 'POST', '/operations');
    assert.equal(await gate.read('example'), null);
    await gate.refuse('example', 401, 'POST', '/artifacts/uploads/op_example/content');
    const markers = (storage: ReturnType<typeof store>) => [...storage.values.keys()].filter(key => key.startsWith('batc.credential-refused.'));
    assert.equal(markers(session).length, remembered ? 0 : 1);
    assert.equal(markers(local).length, remembered ? 1 : 0);
    await gate.forget('example');
    assert.equal(await gate.read('example'), null);
    assert.deepEqual(markers(session), []);
    assert.deepEqual(markers(local), []);
    assert.equal(local.getItem('batc.key.example'), '{"key":"original"}');
  }
});
