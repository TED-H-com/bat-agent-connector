import {eventDelivery, httpEventDelivery} from './event-delivery.mjs';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve, delimiter} from 'node:path';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
import {chromium, expect} from '@playwright/test';

const backend = resolve('..');
const python = process.env.BATC_ATTENTION_PYTHON || resolve(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [resolve('tests/attention-fixture.py')], {cwd: backend,
  env: {...process.env, PYTHONPATH: [resolve(backend, 'src'), backend].join(delimiter)}, stdio: ['pipe', 'pipe', 'inherit']});
const stopped = once(child, 'exit'), lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
const next = async () => {const line = await lines.next(); if (line.done) throw Error('Attention fixture stopped'); return JSON.parse(line.value);};
const control = async action => {child.stdin.write(JSON.stringify({action}) + '\n'); return next();};
const browser = await chromium.launch();
try {
  const fixture = await next(), origin = `http://127.0.0.1:${fixture.port}`;
  const web = await browser.newPage(), desktop = await browser.newPage(), other = await browser.newPage();
  const webEvents = await httpEventDelivery(web), desktopEvents = eventDelivery(desktop);
  const errors = [], posts = [];
  for (const page of [web, desktop, other]) page.on('pageerror', e => errors.push(e.message));
  await web.addInitScript(token => sessionStorage.setItem('batc.dashboard.token', token), fixture.token);
  await other.addInitScript(token => sessionStorage.setItem('batc.dashboard.token', token), fixture.other);
  const central = async input => {
    const response = await fetch(origin + '/api/v1' + input.path, {method: input.method,
      headers: {Authorization: 'Bearer ' + fixture.token, ...(input.body ? {'Content-Type': 'application/json'} : {}),
        ...(input.idempotency_key ? {'Idempotency-Key': input.idempotency_key} : {})},
      body: input.body ? JSON.stringify(input.body) : undefined});
    return {status: response.status, data: await response.json()};
  };
  const caps = (await central({method: 'GET', path: '/capabilities'})).data;
  await desktop.exposeFunction('centralFixture', input => desktopEvents.forward(input, central));
  await desktop.addInitScript(({caps, origin}) => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {
    invoke: async (command, args) => {
      if (command === 'native_status') return {endpoint: origin, credential_available: true};
      if (command === 'connector_connect') return caps;
      if (command === 'connector_request') return window.centralFixture(args.input);
      throw Error('Unexpected native fixture command ' + command);
    }
  }}), {caps, origin});
  await desktop.route('**/api/v1/**', () => {throw Error('Desktop must use IPC');});
  const url = origin + '/dashboard/#/item/' + fixture.wid;
  for (const page of [web, desktop, other]) {
    await page.goto(url); await expect(page.locator('.reading-state')).toContainText('Unread update');
  }
  assert.equal((await control('verify')).markers, 0);
  await webEvents.post(web.getByRole('button', {name: 'Mark this version read'}));
  await expect(desktop.locator('.reading-state')).toContainText('This version is read');
  await expect(other.locator('.reading-state')).toContainText('Unread update');
  let evidence = await control('verify'); assert.equal(evidence.read_events, 1); assert.equal(evidence.version, 2);
  await control('edit');
  for (const page of [web, desktop]) await expect(page.locator('.reading-state')).toContainText('Unread update');
  await desktopEvents.post(desktop.getByRole('button', {name: 'Mark this version read'}));
  await expect(web.locator('.reading-state')).toContainText('This version is read');
  evidence = await control('verify'); assert.equal(evidence.read_events, 2); assert.equal(evidence.reading.read_version, 3);
  assert.equal(await desktop.evaluate(() => JSON.stringify({...localStorage, ...sessionStorage}).includes('batc.dashboard.token')), false);
  await control('edit');
  await expect(web.locator('.reading-state')).toContainText('Unread update');
  await web.route('**/api/v1/operations?wait=3', async route => {
    const req = route.request(); posts.push({method: req.method(), path: '/operations?wait=3', body: req.postDataJSON(), idempotency_key: req.headers()['idempotency-key']});
    await route.fetch(); await route.abort('failed');
  });
  await webEvents.post(web.getByRole('button', {name: 'Mark this version read'}));
  await expect(web.locator('.reading-state')).toContainText('This version is read'); // readback, not the lost receipt
  await expect(desktop.locator('.reading-state')).toContainText('This version is read');
  const replay = await central(posts[0]); assert.equal(replay.data.operation.status, 'succeeded');
  evidence = await control('verify'); assert.equal(evidence.read_events, 3); assert.equal(evidence.version, 4);
  assert.equal(evidence.markers, 1); assert.deepEqual(errors, []);
  console.log('Real central reading passed: Web and IPC share versions, other principal isolated, explicit reads only, newer content remains unread, lost reply replays the original operation, pending approval preserved, zero BAT frames.');
} finally {
  await browser.close(); child.stdin.write(JSON.stringify({action: 'stop'}) + '\n');
  await Promise.race([stopped, delay(10000)]); if (child.exitCode === null) child.kill('SIGTERM');
}
