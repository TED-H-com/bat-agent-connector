// @ts-nocheck
import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';

const manifest = JSON.parse(readFileSync(new URL('../../dist-suite/manifest.json', import.meta.url), 'utf8'));
const csp = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; connect-src ipc: http://ipc.localhost; frame-src 'none'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";

test('fixture serves only manifest assets with the required headers', async ({request}) => {
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
    }
    expect((await get.body()).length).toBe(asset.length);
    expect((await head.body()).length).toBe(0);
  }
  expect((await request.get('/missing.js')).status()).toBe(404);
  expect((await request.get('/manifest.json')).status()).toBe(404);
  expect((await request.get('/index.html?other=1')).status()).toBe(404);
  expect((await request.post('/index.html')).status()).toBe(404);
});
