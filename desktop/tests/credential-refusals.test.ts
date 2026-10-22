import {test} from 'node:test';
import assert from 'node:assert/strict';
import {credentialRefusals} from '../src/credential-refusals.js';

function store() {
  const values = new Map<string, string>();
  return {values, getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key)};
}

test('refusal persists for its credential and endpoint until an explicit retry', async () => {
  const storage = store();
  storage.setItem('batc.draft.example', 'original');
  let endpoint = 'http://localhost:8000';
  const options = {stores: () => [storage], endpoint: () => endpoint};
  const gate = credentialRefusals(options);
  await gate.refuse('example-credential', 401);
  assert.equal((await credentialRefusals(options).read('example-credential'))?.status, 401);
  assert.equal(await gate.read('replacement'), null);
  endpoint = 'http://localhost:8001';
  assert.equal(await gate.read('example-credential'), null);
  endpoint = 'http://localhost:8000';
  assert.equal((await gate.read('example-credential'))?.status, 401);
  assert.ok(!JSON.stringify([...storage.values]).includes('example-credential'));
  await gate.retry('example-credential');
  assert.equal(await gate.read('example-credential'), null);
  assert.deepEqual([...storage.values], [['batc.draft.example', 'original']]);
});

test('unavailable storage keeps refusal in memory and transient failures do not refuse', async () => {
  const gate = credentialRefusals({stores: () => {throw Error('Unavailable');}, endpoint: () => 'http://localhost'});
  await gate.refuse('example', 503);
  assert.equal(await gate.read('example'), null);
  await gate.refuse('example', 403);
  assert.equal((await gate.read('example'))?.status, 403);
  await gate.retry('example');
  assert.equal(await gate.read('example'), null);
});
