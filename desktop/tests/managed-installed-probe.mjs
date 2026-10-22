// Used only by disposable installed-app fixtures. Never writes or logs credentials.
import assert from 'node:assert/strict';
import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFile, readdir, lstat, open, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

// Keep diagnostics finite and non-sensitive: never return file contents, paths,
// endpoint/identity values, or arbitrary exception messages from the runtime.
export async function serviceFailure(dataRoot) {
  let file;
  try {
    const path = join(dataRoot, 'managed-central/service.log');
    const stat = await lstat(path);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 16384) return { state: 'unavailable' };
    file = await open(path, 'r');
    const bytes = Buffer.alloc(16385);
    const { bytesRead } = await file.read(bytes, 0, bytes.length, 0);
    if (bytesRead > 16384) return { state: 'unavailable' };
    const value = JSON.parse(bytes.subarray(0, bytesRead).toString('utf8'));
    if (value.protocol !== 1 || value.error !== 'MANAGED_RUNTIME_UNAVAILABLE') return { state: 'unavailable' };
    const classes = ['PermissionError', 'FileNotFoundError', 'OSError', 'ValueError', 'KeyError',
      'RuntimeError', 'ImportError', 'ModuleNotFoundError', 'TimeoutError'];
    const kind = classes.find(name => value.message === `${name}: managed installation needs recovery`);
    // Mirror only the runtime's existing finite traceback-location projection.
    // Never copy arbitrary location keys, filenames, exception text or locals.
    const phases = ['root', 'launcher_lock', 'prepare', 'manifest', 'service_lease', 'spawn',
      'endpoint', 'verify', 'ensure', 'serve', 'stop', 'unknown'];
    const storage = ['directory', 'open', 'read', 'write', 'lock', 'none'];
    const location = value.location && typeof value.location === 'object' && !Array.isArray(value.location)
      ? value.location : {};
    return { state: 'reported', error: 'MANAGED_RUNTIME_UNAVAILABLE', exception: kind || 'other',
      errno: Number.isSafeInteger(value.errno) ? value.errno : null,
      winerror: Number.isSafeInteger(value.winerror) ? value.winerror : null,
      ...(value.location === undefined ? {} : { location: {
        phase: phases.includes(location.phase) ? location.phase : 'unknown',
        storage: storage.includes(location.storage) ? location.storage : 'unknown',
      } }) };
  } catch (error) {
    return { state: error.code === 'ENOENT' ? 'absent' : 'unavailable' };
  } finally { await file?.close(); }
}

export async function probeManagedOnce(dataRoot) {
  const root = join(dataRoot, 'managed-central');
  let phase = 'installation';
  try {
    const path = join(root, 'installation.json');
    assert(!(await lstat(path)).isSymbolicLink());
    const installation = JSON.parse(await readFile(path, 'utf8'));
    phase = 'installation_ready';
    assert.equal(installation.state, 'ready');
    phase = 'service_pointer';
    const pointer = JSON.parse(await readFile(join(root, 'state/task-service.json'), 'utf8'));
    assert(typeof pointer.endpoint === 'string' && pointer.endpoint.length > 0);
    const endpoint = new URL(pointer.endpoint);
    assert.equal(endpoint.hostname, '127.0.0.1');
    assert.equal(endpoint.protocol, 'http:');
    assert.equal(endpoint.pathname, '/rpc');
    // Disposable managed cores use port zero; never probe an operator's fixed port.
    assert(endpoint.port && endpoint.port !== '18796');
    phase = 'owner_identity';
    const challenge = randomBytes(32).toString('hex');
    const proofResponse = await fetch(`${endpoint.origin}/api/v1/managed/identity?challenge=${challenge}`, {
      redirect: 'error', signal: AbortSignal.timeout(3000),
    });
    assert.equal(proofResponse.status, 200);
    const proof = (await proofResponse.json()).proof;
    const key = await readFile(join(root, 'installation.key'));
    const expected = createHmac('sha256', key)
      .update(JSON.stringify([1, challenge, endpoint.origin, installation.server_id])).digest('hex');
    assert(typeof proof === 'string' && /^[a-f0-9]{64}$/.test(proof));
    assert(timingSafeEqual(Buffer.from(proof, 'hex'), Buffer.from(expected, 'hex')));
    phase = 'credential';
    const token = (await readFile(join(root, 'identity.token'), 'utf8')).trim();
    phase = 'bootstrap';
    const response = await fetch(`${endpoint.origin}/api/v1/bootstrap`, {
      headers: { Authorization: `Bearer ${token}` }, redirect: 'error', signal: AbortSignal.timeout(3000),
    });
    phase = 'authenticated_bootstrap';
    assert.equal(response.status, 200);
    const bootstrap = await response.json();
    phase = 'identity';
    assert.equal(bootstrap.sync.server_id, installation.server_id);
    assert.equal(bootstrap.sync.principal_id, installation.principal_id);
    return { installation_id: installation.installation_id, server_id: installation.server_id,
      principal_id: installation.principal_id, runtime_version: installation.runtime_version,
      authenticated_bootstrap: true };
  } catch (error) {
    const codes = ['ENOENT', 'EACCES', 'EPERM', 'ERR_ASSERTION', 'ECONNREFUSED'];
    const failure = codes.includes(error.code) ? error.code
      : error.name === 'TimeoutError' ? 'timeout' : 'invalid_or_unavailable';
    const safe = new Error(`Managed startup phase ${phase}: ${failure}`);
    safe.diagnostic = { phase, failure };
    throw safe;
  }
}

export async function probeManaged(dataRoot, { wait = false } = {}) {
  const deadline = Date.now() + (wait ? 70_000 : 5_000);
  do {
    try { return await probeManagedOnce(dataRoot); }
    catch (error) {
      if (Date.now() >= deadline) {
        const failure = new Error(`Installed managed service did not authenticate (${error.message})`);
        failure.diagnostic = { ...error.diagnostic, service: await serviceFailure(dataRoot) };
        throw failure;
      }
      await delay(200);
    }
  } while (true);
}

export async function stopManaged(dataRoot) {
  const data = join(dataRoot, 'managed-central');
  try { await lstat(join(data, 'installation.json')); } catch (error) {
    if (error.code === 'ENOENT') return;
    throw error;
  }
  const cache = join(dataRoot, 'managed-runtimes');
  const entries = (await readdir(cache)).filter(name => /^[a-f0-9]{64}$/.test(name));
  assert.equal(entries.length, 1, 'Fixture owns exactly one extracted runtime');
  const executable = join(cache, entries[0], process.platform === 'win32' ? 'batc-managed-runtime.exe' : 'batc-managed-runtime');
  const reply = execFileSync(executable, ['stop', '--data-dir', data], {
    env: { ...process.env, PATH: '', PYINSTALLER_RESET_ENVIRONMENT: '1' },
    windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], timeout: 30_000, maxBuffer: 16384,
  });
  assert.equal(JSON.parse(reply).stopped, true);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const [action, root, evidence] = process.argv.slice(2);
  assert(root && ['wait', 'probe', 'stop'].includes(action));
  if (action === 'stop') await stopManaged(root);
  else {
    try { console.log(JSON.stringify(await probeManaged(root, { wait: action === 'wait' }))); }
    catch (error) {
      if (evidence) await writeFile(evidence, JSON.stringify(error.diagnostic) + '\n');
      throw error;
    }
  }
}
