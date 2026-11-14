// @ts-nocheck
import {test, expect} from '@playwright/test';
import {once} from 'node:events';
import {readFileSync} from 'node:fs';
import {createServer, request as httpRequest} from 'node:http';
import {fileURLToPath} from 'node:url';
import {spawn} from '../../../scripts/hidden-child-process.mjs';

const manifest = JSON.parse(readFileSync(new URL('../../dist-suite/manifest.json', import.meta.url), 'utf8'));
const csp = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; connect-src ipc: http://ipc.localhost; frame-src 'none'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";

function rawGet(port, path) {
  return new Promise((resolve, reject) => {
    const req = httpRequest({host: '127.0.0.1', port, path, method: 'GET'}, response => {
      response.resume();
      response.on('end', () => resolve({status: response.statusCode, headers: response.headers}));
    });
    req.on('error', reject);
    req.end();
  });
}

test('fixture serves only manifest assets with the required headers', async ({request, baseURL}) => {
  for (const asset of manifest.files) {
    const path = '/' + asset.path.split('/').map(encodeURIComponent).join('/');
    const get = await request.get(path), head = await request.head(path);
    expect(get.status()).toBe(200); expect(head.status()).toBe(200);
    for (const response of [get, head]) {
      expect(response.headers()['content-type']).toBe(asset.mime);
      expect(response.headers()['content-length']).toBe(String(asset.length));
      expect(response.headers()['x-content-type-options']).toBe('nosniff');
      expect(response.headers()['cache-control']).toBe('no-store');
      expect(response.headers()['x-frame-options']).toBe('DENY');
      expect(response.headers()['content-security-policy']).toBe(csp);
      expect(response.headers()['access-control-allow-origin']).toBeUndefined();
    }
    expect((await get.body()).length).toBe(asset.length);
    expect((await head.body()).length).toBe(0);
  }
  expect((await request.get('/missing.js')).status()).toBe(404);
  expect((await request.get('/manifest.json')).status()).toBe(404);
  expect((await request.get('/index.html?other=1')).status()).toBe(404);
  expect((await request.post('/index.html')).status()).toBe(404);
  const port = Number(new URL(baseURL).port);
  for (const variant of ['/%2e%2e/manifest.json', '/./index.html', '//index.html', '/INDEX.HTML', '/index.html/', '/%69ndex.html']) {
    const response = await rawGet(port, variant);
    expect(response.status).toBe(404);
    expect(response.headers['x-content-type-options']).toBe('nosniff');
    expect(response.headers['cache-control']).toBe('no-store');
    expect(response.headers['x-frame-options']).toBe('DENY');
    expect(response.headers['content-security-policy']).toBe(csp);
  }
  const freePort = await new Promise((resolve, reject) => {
    const probe = createServer();
    probe.on('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const address = probe.address();
      probe.close(error => error ? reject(error) : resolve(address.port));
    });
  });
  const child = spawn(process.execPath, [fileURLToPath(new URL('./suite-server.mjs', import.meta.url))], {
    env: {...process.env, BATC_UI_TEST_PORT: String(freePort)},
    stdio: 'ignore',
  });
  try {
    await expect.poll(async () => {
      try { return (await rawGet(freePort, '/index.html')).status; } catch { return 0; }
    }).toBe(200);
    expect((await rawGet(freePort, '/index.html')).status).toBe(200);
    expect((await rawGet(freePort, '/__harness/suite/tests/harness/tailscale.html')).status).toBe(404);
  } finally {
    child.kill();
    await once(child, 'exit');
  }
});
