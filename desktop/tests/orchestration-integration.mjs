import {httpEventDelivery} from './event-delivery.mjs';
// Browser recovery against real central preparation, child authority and receipts; MockBat only.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {delimiter, resolve} from 'node:path';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
import {chromium, expect} from '@playwright/test';
const backend = resolve(process.env.BATC_ORCHESTRATION_ROOT || '..');
const python = process.env.BATC_ORCHESTRATION_PYTHON || resolve(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [resolve('tests/orchestration-fixture.py')], {cwd: backend,
  env: {...process.env, PYTHONPATH: [resolve(backend, 'src'), backend].join(delimiter)}, stdio: ['pipe', 'pipe', 'inherit']});
const stopped = once(child, 'exit'), lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
const next = async () => {const line = await lines.next(); if (line.done) throw new Error('Orchestration fixture stopped'); return JSON.parse(line.value);};
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
    const response = await route.fetch(); if (lose) {lose = false; await route.abort('failed');} else await route.fulfill({response});
  });
  const open = async mode => {
    await page.goto(origin+'/dashboard/#/orchestrate/'+mode);
    const form = page.locator('[data-orchestration]');
    await form.getByRole('combobox', {name: 'Host', exact: true}).selectOption('h1');
    const selection = form.getByRole('combobox').nth(1); await expect(selection).toBeEnabled();
    await selection.selectOption(mode === 'relay' ? fixture.relay : mode === 'failover' ? fixture.source : 'ws-1');
    return form;
  };
  const submit = async form => {await form.locator('.orch-check input').last().check(); await events.post(form.locator('button.primary'));};
  const id = async form => (await form.locator('a[href^="#/op/"]').first().getAttribute('href')).split('/').at(-1);
  let form = await open('relay'); await form.getByRole('textbox').fill('  Exact original relay.\nPreserve this text.  ');
  await submit(form); await expect(form.locator('.error')).toBeVisible(); await control({action: 'writes', enabled: false}); await page.reload();
  await events.post(form.getByRole('button', {name: 'Retry original request'})); await expect(form).toContainText('target accepted the relayed instructions', {timeout: 30000});
  assert.deepEqual(posts[0], posts[1]); const relay = await id(form); await page.reload(); await expect(form).toContainText('target accepted'); assert.equal(posts.length, 2);
  await control({action: 'verify', operation_id: relay, operation_action: 'session.relay', status: 'succeeded', frames: 1});
  await control({action: 'writes', enabled: true});
  await page.reload(); // bootstrap reads the restored host policy, before a new request
  form = await open('planner'); await form.getByRole('textbox').fill('Plan the exact requested change.'); await submit(form);
  await expect(form).toContainText('Planner instructions accepted', {timeout: 30000});
  await control({action: 'verify', operation_id: await id(form), operation_action: 'fanout.plan', status: 'succeeded', frames: 4});
  form = await open('failover'); await form.getByRole('textbox').fill('Preserve original work.'); await submit(form);
  await expect(form).toContainText('Codex handoff accepted', {timeout: 30000});
  await control({action: 'verify', operation_id: await id(form), operation_action: 'session.failover', status: 'succeeded', frames: 6});
  form = await open('items'); await form.getByRole('textbox', {name: 'Item 1 title', exact: true}).fill('First item');
  await form.getByRole('textbox', {name: 'Item 1 instructions', exact: true}).fill('Literal item one.'); await form.getByRole('button', {name: 'Add another item'}).click();
  await form.getByRole('textbox', {name: 'Item 2 title', exact: true}).fill('Second item'); await form.getByRole('textbox', {name: 'Item 2 instructions', exact: true}).fill('Must not start after unknown first ACK.');
  await control({action: 'lose-next-send'}); await submit(form); await expect(form).toContainText('Completion is not proven', {timeout: 30000});
  const fanout = await id(form); await expect(form).toContainText('0 of 2'); await expect(form.getByRole('button', {name: 'Prepare another request'})).toBeHidden();
  await page.reload(); await expect(form).toContainText('Completion is not proven'); await form.getByRole('button', {name: 'Check original operation'}).click();
  await control({action: 'verify', operation_id: fanout, operation_action: 'fanout.start', status: 'waiting_external', frames: 9});
  assert.equal(posts.length, 5); assert.deepEqual(errors, []);
  console.log('Real central orchestration passed: relay lost-reply replay before changed tier; read-only planner child; single same-carrier failover; literal fanout partial unknown child, no second dispatch or resend; manual state unchanged.');
} catch (error) {
  if (child.exitCode === null && child.signalCode === null) console.error('Fixture diagnostics:', await control({action: 'diagnostic'}));
  throw error;
} finally {
  await browser.close();
  if (child.exitCode === null && child.signalCode === null) child.stdin.end(JSON.stringify({action: 'stop'})+'\n');
  await Promise.race([stopped, delay(3000, undefined, {ref: false}).then(() => child.kill())]);
}
