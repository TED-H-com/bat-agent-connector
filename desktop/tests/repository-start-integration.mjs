import {httpEventDelivery} from './event-delivery.mjs';
// Actual central admission and receipts; browser reply loss is injected after its request reached central.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {delimiter, resolve} from 'node:path';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
import {chromium, expect} from '@playwright/test';
const backend = resolve(process.env.BATC_PUBLISHED_ROOT || '..');
const python = process.env.BATC_PUBLISHED_PYTHON || resolve(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [resolve('tests/repository-start-fixture.py')], {cwd: backend,
  env: {...process.env, PYTHONPATH: [resolve(backend, 'src'), backend].join(delimiter)}, stdio: ['pipe', 'pipe', 'inherit']});
const stopped = once(child, 'exit'), lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
const next = async () => {const line = await lines.next(); if (line.done) throw new Error('Start fixture stopped'); return JSON.parse(line.value);};
const control = async command => {child.stdin.write(JSON.stringify(command)+'\n'); return next();};
const browser = await chromium.launch();
try {
  const fixture = await next(), origin = `http://127.0.0.1:${fixture.port}`;
  const page = await browser.newPage({locale: 'en-US'}), posts = [], errors = [];
  const events = await httpEventDelivery(page);
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(token => sessionStorage.setItem('batc.dashboard.token', token), fixture.token);
  let lose = true;
  await page.route('**/api/v1/operations?wait=3', async route => {
    const req = route.request(); posts.push({body: req.postDataJSON(), key: req.headers()['idempotency-key']});
    const response = await route.fetch();
    if (lose) {lose = false; await route.abort('failed');} else await route.fulfill({response});
  });
  await page.goto(origin+'/dashboard/#/published'); const form = page.locator('[data-published-start]');
  await form.getByRole('combobox', {name: 'Repository · host · workspace ID'}).selectOption(JSON.stringify({repository:'o/r',host:'h1',workspace_id:'ws-1'}));
  await form.getByRole('textbox', {name: 'Published branch ref'}).fill('refs/heads/main');
  await events.post(form.getByRole('button', {name: 'Preview published version'}));
  await expect(form.locator('[data-published-preview]')).toContainText(fixture.sha);
  const original = '  Keep my original words.\nDo not replace them.  ';
  await form.getByRole('textbox', {name: 'Original instructions', exact: true}).fill(original);
  await events.post(form.getByRole('button', {name: 'Start from this version', exact: true})); await expect(form.locator('.error')).toBeVisible();
  await control({action: 'writes', enabled: false}); await page.reload();
  await events.post(form.getByRole('button', {name: 'Retry original request'})); await expect(form).toContainText('Initial instructions were accepted');
  const id = async () => (await form.locator('a[href^="#/op/"]').getAttribute('href')).split('/').at(-1);
  const first = await id(); assert.deepEqual(posts[0], posts[1]);
  await page.reload(); await expect(form).toContainText('Start confirmed'); assert.equal(posts.length, 2);
  await control({action: 'verify', operation_id: first, operations: 1, frames: 2, status: 'succeeded', prompt: original});
  await control({action: 'writes', enabled: true}); await page.reload();
  await form.getByRole('button', {name: 'Prepare another published version'}).click();
  await events.post(form.getByRole('button', {name: 'Preview published version'}));
  await expect(form.locator('[data-published-preview]')).toContainText(fixture.sha);
  await form.getByRole('combobox', {name: 'Agent', exact: true}).selectOption('codex');
  await form.getByRole('textbox', {name: 'Original instructions', exact: true}).fill('second original');
  await control({action: 'lose-send'});
  await events.post(form.getByRole('button', {name: 'Start from this version', exact: true}));
  await expect(form).toContainText('Started, but acceptance of the initial instructions is unconfirmed', {timeout: 30000});
  const second = await id(); assert.notEqual(first, second);
  await page.reload(); await expect(form).toContainText('Started, but acceptance');
  await expect(form.getByRole('button', {name: 'Prepare another published version'})).toBeHidden();
  await form.getByRole('button', {name: 'Check original operation'}).click();
  await control({action: 'verify', operation_id: second, operations: 2, frames: 4, status: 'uncertain', prompt: 'second original'});
  assert.equal(posts.length, 3); assert.notEqual(posts[1].key, posts[2].key); assert.deepEqual(errors, []);
  console.log('Real central published start: fixed GitHub head and temp Git bytes, original prompt, lost reply replay before changed tier, accepted reload, new explicit key, uncertain Codex send without resend, unchanged manual checkout and remote refs passed');
} finally {
  await browser.close();
  if (child.exitCode === null && child.signalCode === null) child.stdin.end(JSON.stringify({action: 'stop'})+'\n');
  await Promise.race([stopped, delay(3000, undefined, {ref: false}).then(() => child.kill())]);
}
