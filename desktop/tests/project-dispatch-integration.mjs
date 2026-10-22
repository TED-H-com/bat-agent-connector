import {eventDelivery, httpEventDelivery} from './event-delivery.mjs';
// Shared project composer against actual central receipts and temporary Git/artifact bytes.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {delimiter, resolve} from 'node:path';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
import {chromium, expect} from '@playwright/test';
const backend = resolve('..');
const python = process.env.BATC_DISPATCH_PYTHON || resolve(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const child = spawn(python, [resolve('tests/repository-start-fixture.py')], {cwd: backend,
  env: {...process.env, BATC_DISPATCH_CREATE_PROJECT: '1', PYTHONPATH: [resolve(backend, 'src'), backend].join(delimiter)}, stdio: ['pipe', 'pipe', 'inherit']});
const stopped = once(child, 'exit'), lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
const next = async () => {const line = await lines.next(); if (line.done) throw Error('Dispatch fixture stopped'); return JSON.parse(line.value);};
const control = async command => {child.stdin.write(JSON.stringify(command)+'\n'); return next();};
const browser = await chromium.launch();
try {
  const fixture = await next(), origin = `http://127.0.0.1:${fixture.port}`;
  const posts = [], errors = [];
  const web = await browser.newPage(), desktop = await browser.newPage();
  const webEvents = await httpEventDelivery(web, {initiallyPaused:true}), desktopEvents = eventDelivery(desktop, {initiallyPaused:true});
  for (const page of [web, desktop]) page.on('pageerror', e => errors.push(e.message));
  await web.addInitScript(token => sessionStorage.setItem('batc.dashboard.token', token), fixture.token);
  await web.goto(origin+'/dashboard/#/projects');
  await webEvents.pause();
  await web.getByRole('button', {name: 'New project', exact: true}).click();
  await web.getByRole('textbox', {name: 'New project name'}).fill('First project');
  const repository = web.getByRole('combobox', {name: 'Repository for dispatch (optional)'});
  await expect(repository).toHaveValue('');
  await repository.selectOption('o/r');
  await webEvents.post(() => web.getByRole('textbox', {name: 'New project name'}).press('Enter'), {resume:false});
  await expect(web).toHaveURL(/#\/project\/prj_[a-f0-9]{20}$/);
  const project = await control({action: 'prepare-project', project_id: web.url().split('/').at(-1)});
  const central = async input => {
    if (input.method === 'POST' && input.path.startsWith('/operations')) posts.push(input);
    const response = await fetch(origin + '/api/v1' + input.path, {method: input.method,
      headers: {Authorization: 'Bearer ' + fixture.token, ...(input.body ? {'Content-Type': 'application/json'} : {}),
        ...(input.idempotency_key ? {'Idempotency-Key': input.idempotency_key} : {})},
      body: input.body ? JSON.stringify(input.body) : undefined});
    return {status: response.status, data: await response.json()};
  };
  const caps = (await central({method: 'GET', path: '/capabilities'})).data;
  await desktop.exposeFunction('dispatchCentral', input => desktopEvents.forward(input, central));
  await desktop.addInitScript(({caps, origin}) => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {
    invoke: async (command, args) => {
      if (command === 'native_status') return {endpoint: origin, credential_available: true};
      if (command === 'connector_connect') return caps;
      if (command === 'connector_request') return window.dispatchCentral(args.input);
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      throw Error('Unexpected native fixture command ' + command);
    }
  }}), {caps, origin});
  await desktop.route('**/api/v1/**', () => {throw Error('Desktop must use IPC');});
  let lose = true, lostOperation;
  await web.route('**/api/v1/operations?wait=3', async route => {
    const req = route.request(); posts.push({method: req.method(), path: '/operations?wait=3', body: req.postDataJSON(), idempotency_key: req.headers()['idempotency-key']});
    const response = await route.fetch();
    if (lose) {
      lostOperation = (await response.json()).operation.operation_id;
      lose = false; await route.abort('failed');
    } else await route.fulfill({response});
  });
  const original = '  Keep the project request.\nUse the attached bytes.  ';
  let count = 0;
  const operations = [];
  for (const page of [web, desktop]) {
    const events = page === web ? webEvents : desktopEvents;
    await page.goto(origin+'/dashboard/#/project/'+project.project_id);
    await events.pause();
    try {await page.getByRole('link', {name: 'Quick project dispatch'}).click();}
    catch (error) {console.error({client: page === web ? 'HTTP' : 'IPC', errors, body: await page.locator('body').innerText()}); throw error;}
    const form = page.locator('[data-published-start]');
    await expect(form.getByRole('combobox', {name: 'Repository · host · workspace ID'})).toHaveValue(JSON.stringify({repository:'o/r',host:'h1',workspace_id:'ws-1'}));
    await form.getByRole('textbox', {name: 'Published branch ref'}).fill('main');
    await events.post(form.getByRole('button', {name: 'Preview published version'}), {resume:false});
    await expect(form.locator('[data-published-preview]')).toContainText(fixture.sha);
    await form.getByRole('textbox', {name: 'Original instructions', exact: true}).fill(original);
    await form.getByText('Advanced settings', {exact: true}).click();
    await form.getByLabel('Model (optional)', {exact: true}).fill('selected-model');
    const inputs = form.getByRole('combobox', {name: 'Uploaded attachment'});
    await expect(inputs.locator('option')).toHaveCount(2);
    await inputs.selectOption(`${project.artifact.artifact_id}:1`);
    await form.getByRole('button', {name: 'Add attachment', exact: true}).click();
    await expect(form.locator('.attachment-list')).toContainText('notes.txt');
    await events.post(form.getByRole('button', {name: 'Start from this version', exact: true}), {resume:false});
    if (page === web) {
      await expect(form.locator('.error')).toBeVisible({timeout: 30000});
      // The response wait limit is not a completion barrier. Archive only after
      // the original dispatch settles, then replay its receipt with the same key.
      await expect.poll(async () => (await central({method: 'GET', path: '/operations/' + lostOperation})).data.operation.status,
        {timeout: 30000}).toBe('succeeded');
      await control({action: 'archive-project', project_id: project.project_id, archived: true});
      await control({action: 'writes', enabled: false}); await page.reload();await events.pause();
      await events.post(form.getByRole('button', {name: 'Retry original request'}), {resume:false});
      assert.deepEqual(posts[0], posts[1]);
    }
    await expect(form).toContainText('Start confirmed', {timeout: 30000});
    await expect(form).toContainText('Initial instructions were accepted', {timeout: 30000});
    const id = (await form.locator('a[href^="#/op/"]').getAttribute('href')).split('/').at(-1);
    operations.push(id);
    await page.reload();await events.pause(); await expect(form).toContainText('Start confirmed');
    count++;
    await control({action: 'verify-project', ...project, operation_id: id, operations: count, frames: count * 2,
      status: 'succeeded', prompt: original, version: page === web ? 1 : 3});
    if (page === web) {
      await control({action: 'archive-project', project_id: project.project_id, archived: false});
      await control({action: 'writes', enabled: true});
    }
  }
  assert.equal(posts.length, 3); assert.notEqual(posts[1].idempotency_key, posts[2].idempotency_key);
  const delivered = await control({action: 'prepare-delivery', operations});
  for (const [index, page] of [web, desktop].entries()) {
    const events = page === web ? webEvents : desktopEvents;
    await page.goto(origin+'/dashboard/#/project/'+project.project_id);
    await events.pause();
    await page.locator('.workspace-tree a[href="#/work/'+project.project_id+'/execution/'+operations[index]+'"]').click();
    await expect(page.locator('.workspace-conversation')).toBeVisible();
    await expect(page.locator('.workspace-nav')).toBeVisible();
    const work = page.locator('[data-workspace-result] [data-project-work="'+operations[index]+'"]');
    await expect(work).toContainText('Result unverified');
    await expect(work).not.toContainText('done');
    await work.getByRole('link', {name: 'Review result', exact: true}).click();
    // Delivery defers renders while this PR editor is open. Hold its poll
    // before filling fields, rather than waiting for that editor to close.
    await events.pause();
    await expect(page.getByPlaceholder('owner/name')).toHaveValue('o/r');
    await page.getByRole('textbox', {name: 'Repository or GitHub PR URL'}).fill('https://github.com/o/r/pull/1');
    await page.getByRole('button', {name: 'Load PR', exact: true}).click();
    const source = page.locator('[data-delivery-source="'+operations[index]+'"]');
    await expect(source).toContainText('not streaming');
    await source.getByRole('button', {name: 'Add', exact: true}).click();
    try {
      await expect(page.getByRole('button', {name: 'Update PR results', exact: true})).toBeEnabled({timeout: 30000});
    } catch (error) {
      console.error(await page.locator('[data-integration-review]').innerText());
      throw error;
    }
    await events.post(page.getByRole('button', {name: 'Update PR results', exact: true}), {resume:false});
    await expect(source.getByRole('link', {name: 'sent to #1', exact: true})).toBeVisible({timeout: 30000});
  }
  await control({action: 'verify-delivery', results: delivered.results});
  assert.equal(await desktop.evaluate(() => JSON.stringify({...localStorage, ...sessionStorage}).includes('batc.dashboard.token')), false);
  assert.deepEqual(errors, []);
  console.log('Real central project tree/conversation/creation/dispatch/delivery passed: a new project selects its configured repository in the UI; HTTP and IPC preserve project/version, fixed GitHub head, model, attachment bytes and lost-reply keys; both original executions land in one fixture PR using a pasted PR link with exact source receipts; manual checkout stays unchanged and no redundant task or start is created.');
} finally {
  await browser.close();
  if (child.exitCode === null && child.signalCode === null) child.stdin.end(JSON.stringify({action: 'stop'})+'\n');
  await Promise.race([stopped, delay(3000, undefined, {ref: false}).then(() => child.kill())]);
}
