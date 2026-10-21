import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import {testScratchRoot} from './test-scratch.mjs';
import { join } from 'node:path';
import { test } from 'node:test';
import { probeManaged, probeManagedOnce, serviceFailure } from './managed-installed-probe.mjs';

async function fixture(t: any) {
  const root = await mkdtemp(join(testScratchRoot(), 'managed-probe-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, 'managed-central/state'), { recursive: true });
  return root;
}
async function put(root: string, name: string, value: unknown) {
  await writeFile(join(root, 'managed-central', name), JSON.stringify(value));
}

test('startup evidence identifies missing, initializing and invalid pointer phases without private values', async t => {
  const root = await fixture(t);
  async function rejects(phase: string, failure: string) {
    await assert.rejects(probeManagedOnce(root), (error: any) => {
      assert.deepEqual(error.diagnostic, { phase, failure });
      assert(!JSON.stringify(error).includes('private-secret'));
      assert(!error.message.includes(root));
      return true;
    });
  }
  await rejects('installation', 'ENOENT');
  await put(root, 'installation.json', { state: 'private-secret' });
  await rejects('installation_ready', 'ERR_ASSERTION');
  await put(root, 'installation.json', { state: 'ready' });
  await rejects('service_pointer', 'ENOENT');
  await put(root, 'state/task-service.json', { pid: process.pid });
  await rejects('service_pointer', 'ERR_ASSERTION');
  await put(root, 'state/task-service.json', { endpoint: 'http://127.0.0.1:18796/rpc' });
  await rejects('service_pointer', 'ERR_ASSERTION');
  await put(root, 'state/task-service.json', { endpoint: 'https://private-secret.invalid/rpc' });
  await rejects('service_pointer', 'ERR_ASSERTION');
});

test('startup still requires authenticated bootstrap and the exact original identity', async t => {
  const root = await fixture(t);
  let status = 401;
  let serverId = 'wrong-private-secret';
  let proven = false;
  let bootstrapCalls = 0;
  const server = createServer((request, response) => {
    if (request.url?.startsWith('/api/v1/managed/identity?challenge=')) {
      assert.equal(request.headers.authorization, undefined);
      const challenge = request.url.split('=')[1];
      const proof = createHmac('sha256', 'fixture-installation-key')
        .update(JSON.stringify([1, challenge, `http://127.0.0.1:${port}`, 'server'])).digest('hex');
      response.end(JSON.stringify({ protocol: 1, runtime_version: '0.1.0', proof: proven ? proof : '0'.repeat(64) }));
      return;
    }
    bootstrapCalls++;
    assert.equal(request.url, '/api/v1/bootstrap');
    assert.equal(request.headers.authorization, 'Bearer private-secret');
    response.writeHead(status, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ sync: { server_id: serverId, principal_id: 'principal' } }));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise<void>(resolve => { server.close(resolve); server.closeAllConnections(); }));
  const port = (server.address() as { port: number }).port;
  await put(root, 'installation.json', { state: 'ready', installation_id: 'installation',
    server_id: 'server', principal_id: 'principal', runtime_version: '0.1.0' });
  await put(root, 'state/task-service.json', { endpoint: `http://127.0.0.1:${port}/rpc` });
  await writeFile(join(root, 'managed-central/installation.key'), 'fixture-installation-key');
  await writeFile(join(root, 'managed-central/identity.token'), 'private-secret');
  await assert.rejects(probeManagedOnce(root), (error: any) => {
    assert.deepEqual(error.diagnostic, { phase: 'owner_identity', failure: 'ERR_ASSERTION' });
    return true;
  });
  assert.equal(bootstrapCalls, 0);
  proven = true;
  for (const phase of ['authenticated_bootstrap', 'identity']) {
    await assert.rejects(probeManagedOnce(root), (error: any) => {
      assert.deepEqual(error.diagnostic, { phase, failure: 'ERR_ASSERTION' });
      assert(!error.message.includes('private-secret'));
      return true;
    });
    status = 200;
  }
  serverId = 'server';
  assert.deepEqual(await probeManagedOnce(root), { installation_id: 'installation', server_id: 'server',
    principal_id: 'principal', runtime_version: '0.1.0', authenticated_bootstrap: true });
  await put(root, 'state/task-service.json', { pid: process.pid });
  const waiting = probeManaged(root);
  await put(root, 'state/task-service.json', { endpoint: `http://127.0.0.1:${port}/rpc` });
  assert.equal((await waiting).authenticated_bootstrap, true);
});

test('bounded runtime failure evidence only retains fixed codes and numeric OS errors', async t => {
  const root = await fixture(t);
  assert.deepEqual(await serviceFailure(root), { state: 'absent' });
  await put(root, 'service.log', { protocol: 1, error: 'MANAGED_RUNTIME_UNAVAILABLE',
    message: 'PermissionError: managed installation needs recovery', errno: 13, winerror: 5,
    private: 'private-secret' });
  assert.deepEqual(await serviceFailure(root), { state: 'reported', error: 'MANAGED_RUNTIME_UNAVAILABLE',
    exception: 'PermissionError', errno: 13, winerror: 5 });
  await put(root, 'service.log', { protocol: 1, error: 'MANAGED_RUNTIME_UNAVAILABLE',
    message: 'private-secret', errno: 'private-secret', winerror: ['private-secret'] });
  assert.deepEqual(await serviceFailure(root), { state: 'reported', error: 'MANAGED_RUNTIME_UNAVAILABLE',
    exception: 'other', errno: null, winerror: null });
  for (const bytes of ['private-secret', 'x'.repeat(16385)]) {
    await writeFile(join(root, 'managed-central/service.log'), bytes);
    assert.deepEqual(await serviceFailure(root), { state: 'unavailable' });
  }
});

test('installed startup evidence preserves only existing finite runtime locations', async t => {
  const root = await fixture(t);
  const failure = { protocol: 1, error: 'MANAGED_RUNTIME_UNAVAILABLE',
    message: 'PermissionError: managed installation needs recovery', errno: 13, winerror: 5 };
  const phases = ['root', 'launcher_lock', 'prepare', 'manifest', 'service_lease', 'spawn',
    'endpoint', 'verify', 'ensure', 'serve', 'stop', 'unknown'];
  const storage = ['directory', 'open', 'read', 'write', 'lock', 'none'];
  const locations = [...phases.map(phase => ({ phase, storage: 'write' })),
    ...storage.map(storage => ({ phase: 'serve', storage }))];
  for (const location of locations) {
    await put(root, 'service.log', { ...failure, location: { ...location, filename: 'private-secret' } });
    assert.deepEqual(await serviceFailure(root), {
      state: 'reported', error: 'MANAGED_RUNTIME_UNAVAILABLE', exception: 'PermissionError',
      errno: 13, winerror: 5, location,
    });
  }
});

test('unknown runtime locations cannot disclose paths, tokens or arbitrary diagnostics', async t => {
  const root = await fixture(t);
  const secret = '/private-secret/customer-token';
  const cases = [
    { input: null, phase: 'unknown', storage: 'unknown' },
    { input: secret, phase: 'unknown', storage: 'unknown' },
    { input: [secret], phase: 'unknown', storage: 'unknown' },
    { input: { phase: secret, storage: secret, stack: secret }, phase: 'unknown', storage: 'unknown' },
    { input: { phase: 'serve', storage: { token: secret } }, phase: 'serve', storage: 'unknown' },
    { input: { phase: [secret], storage: 'write' }, phase: 'unknown', storage: 'write' },
  ];
  for (const { input, phase, storage } of cases) {
    await put(root, 'service.log', { protocol: 1, error: 'MANAGED_RUNTIME_UNAVAILABLE',
      message: secret, errno: secret, winerror: secret, location: input, traceback: secret });
    const result = await serviceFailure(root);
    assert.deepEqual(result, { state: 'reported', error: 'MANAGED_RUNTIME_UNAVAILABLE',
      exception: 'other', errno: null, winerror: null, location: { phase, storage } });
    assert(!JSON.stringify(result).includes(secret));
  }
});
