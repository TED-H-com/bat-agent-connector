// M04: one client, two independent real TaskDaemon HTTP services/journals.
// See docs/design/two-service-isolation.md for the native boundary and evidence.
import assert from 'node:assert/strict';
import {storageSnapshot, assertStoragePreserved} from './storage-snapshot.ts';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir, mkdtemp, rm, writeFile} from 'node:fs/promises';
import {homedir} from 'node:os';
import {delimiter, join, resolve} from 'node:path';
import {createInterface} from 'node:readline';
import {setTimeout as delay} from 'node:timers/promises';
import {chromium, expect} from '@playwright/test';

const backend = resolve('..'), started = performance.now();
const managedMode = process.argv.includes('--managed');
const temporaryParent = process.env.RUNNER_TEMP || process.env.TMPDIR || join(homedir(), 'agent-work/tmp');
await mkdir(temporaryParent, {recursive: true});
const scratch = await mkdtemp(join(temporaryParent, 'two-service-isolation-'));
// Chromium and Python must use disk scratch, including on development hosts.
process.env.TMPDIR = process.env.TMP = process.env.TEMP = scratch;
const evidence = resolve(process.env.BATC_ISOLATION_EVIDENCE || 'test-results/two-service-isolation');
await mkdir(evidence, {recursive: true});
const python = resolve(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const children = [], requests = [], snapshots = [], results = [];
const locales = process.env.BATC_ISOLATION_LOCALE ? [process.env.BATC_ISOLATION_LOCALE] : ['en-US', 'zh-TW'];
assert.ok(locales.every(locale => ['en-US', 'zh-TW'].includes(locale)), 'Unsupported isolation locale');
let browser, currentPage;

async function fixture(label) {
  const child = spawn(python, [resolve('tests/two-service-fixture.py'), label], {cwd: backend,
    env: {...process.env, PYTHONPATH: [resolve(backend, 'src'), backend].join(delimiter)}, stdio: ['pipe', 'pipe', 'inherit']});
  const stopped = once(child, 'exit');
  const lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
  const next = async () => {
    const line = await lines.next();
    if (line.done) throw Error(`Service ${label} stopped before replying`);
    return JSON.parse(line.value);
  };
  const control = async (action, data = {}) => {child.stdin.write(JSON.stringify({action, ...data}) + '\n'); return next();};
  let stopping;
  const stop = () => stopping ||= (async () => {
    if (child.exitCode !== null || child.signalCode) return;
    child.stdin.end(JSON.stringify({action:'stop'}) + '\n');
    await Promise.race([stopped, delay(3000, undefined, {ref:false})]);
    if (child.exitCode === null && !child.signalCode) {child.kill('SIGTERM'); await stopped;}
  })();
  children.push({stop});
  return {...await next(), control, stop};
}

const origin = service => `http://127.0.0.1:${service.port}`;
const scope = (endpoint, service) => [endpoint, service.server_id, service.principal_id, service.actor].map(encodeURIComponent).join(':');
const session = '#/session/h1/sess-claude-0001';
const texts = locale => locale === 'zh-TW' ? {connect:'連線', disconnect:'中斷', send:'送出', reload:'重新載入設定',
  repository:'儲存庫或 GitHub PR 網址', pr:'PR 編號', load:'讀取 PR', emptyReceipts:'這個工作階段尚無送出紀錄', pending:'等待送出結果'} :
  {connect:'Connect', disconnect:'Disconnect', send:'Send', reload:'Reload configuration', repository:'Repository or GitHub PR URL',
    pr:'PR number', load:'Load PR', emptyReceipts:'No send receipts for this session', pending:'Send pending'};
async function route(page, hash) {await page.evaluate(value => {location.hash = value;}, hash);}
const composer = page => page.locator('[data-session-view] textarea').first();
const dom = page => page.evaluate(() => document.documentElement.outerHTML + '\n' +
  [...document.querySelectorAll('input,textarea,select')].map(input => input.value).join('\n'));

// Capture all network requests, not just writes. Credentials are compared in RAM;
// evidence records presence and owner, never the actual Bearer token.
function record(services, run, transport, service, method, url, headers, body) {
  const authorization = headers.authorization || headers.Authorization;
  const owner = services.find(value => authorization === `Bearer ${value.token}`)?.label;
  const row = {run, transport, service:service?.label ?? null, method, url, target_endpoint:new URL(url).origin,
    idempotency_key:headers['idempotency-key'] || headers['Idempotency-Key'] || null,
    authorization_present:Boolean(authorization), authorization_owner:owner || (authorization ? 'unknown' : null), body:body || null};
  requests.push(row);
  return row;
}
async function snapshot(page, run, step, namespaces) {
  const value = await page.evaluate(() => ({localStorage:Object.entries(localStorage).sort(), sessionStorage:Object.entries(sessionStorage).sort(), route:location.hash}));
  const grouped = {};
  for (const [label, namespace] of Object.entries(namespaces)) grouped[label] = Object.fromEntries(['localStorage', 'sessionStorage'].map(area =>
    [area, value[area].filter(([key]) => key.includes(namespace))]));
  const globals = Object.fromEntries(['localStorage', 'sessionStorage'].map(area => [area, value[area]
    .filter(([key]) => !Object.values(namespaces).some(namespace => key.includes(namespace)))
    .map(([key, data]) => [key, key === 'batc.dashboard.token' ? '[credential present]' : data])]));
  snapshots.push({run, step, namespaces, grouped, globals, route:value.route});
  return grouped;
}
async function checkpoint(page, namespace, service) {
  const saved = () => page.evaluate(key => JSON.parse(localStorage.getItem(key)), `batc.sync.${namespace}`);
  const head = (await service.control('report')).sync.checkpoint.cursor;
  await expect.poll(async () => (await saved())?.cursor).toBe(head);
  const value = await saved(), claims = JSON.parse(Buffer.from(value.token.split('.')[1], 'base64url'));
  assert.equal(claims.server_id, service.server_id);
  assert.equal(claims.principal_id, service.principal_id);
  return value;
}
async function privateDOM(page, own, other) {
  await expect(page.locator('.message-body').filter({hasText:own.marker}).first()).toBeVisible();
  assert.equal((await dom(page)).includes(other.marker), false, 'Other source private fact in DOM');
}
async function receipts(page, labels, original = null, text = '') {
  const panel = page.locator('[data-instruction-receipts]');
  await panel.locator('summary').click();
  if (original) {
    const row = panel.locator(`[data-instruction-operation="${original.op}"]`);
    await expect(row).toBeVisible();
    await expect(row).toContainText(text);
    await expect(row).toContainText(labels.pending);
  } else {
    await expect(panel).toContainText(labels.emptyReceipts);
    await expect(panel.locator('[data-instruction-operation]')).toHaveCount(0);
  }
}
async function lists(page, own, other, selectedHost = '') {
  await route(page, '#/sessions');
  const filter = page.locator('.session-filters select').first();
  await expect(filter.locator('option')).toHaveCount(2); // blank plus exactly one h1
  await expect(filter).toHaveValue(selectedHost);
  await expect(page.locator('.session-inventory a.title')).toHaveCount(1);
  await expect(page.locator('.session-inventory a.title')).toHaveAttribute('href', session);
  await filter.selectOption('h1');
  await route(page, `#/project/${own.project_id}`);
  await expect(page.locator(`#main a[href="#/item/${own.work_item_id}"]`).first()).toHaveText('Shared work');
  assert.equal(await page.locator(`a[href*="${other.work_item_id}"]`).count(), 0);
  // The PM selector must also contain one independent workspace, not a merged list.
  await route(page, `#/pm/${own.workspace_id}/discussions`);
  await expect(page.locator('[data-workspace-id]').first()).toHaveAttribute('data-workspace-id', own.workspace_id);
  assert.equal((await page.content()).includes(other.workspace_id), false);
  await route(page, session);
  await expect(composer(page)).toBeVisible();
  await privateDOM(page, own, other);
}
async function delivery(page, labels, repository, pr) {
  await route(page, '#/delivery');
  const input = page.getByRole('textbox', {name:labels.repository, exact:true});
  await expect(input).toBeVisible();
  if (repository !== undefined) {
    await input.fill(repository);
    await page.getByRole('textbox', {name:labels.pr, exact:true}).fill(pr);
    await page.getByRole('button', {name:labels.load, exact:true}).click();
  }
  return input;
}

// Substitute only the OS configuration/credential boundary. All Connector reads
// and writes go unchanged to the selected real HTTP server, with its own token.
async function nativeBridge(page, services, run, initial) {
  let selected = initial, next = initial, connected = false;
  let recovering = false, rebind = 0, holdEvents = null, holdBootstrap = null;
  const managed = initial.label === 'M', statuses = [], responses = [];
  const gate = () => {let release; const promise = new Promise(resolve => {release = resolve;}); return {promise, release};};
  const status = () => ({endpoint:origin(selected) + '/', expected_actor:selected.actor, configuration_source:managed ? 'managed' : 'external', managed_recovery:recovering ? 'MANAGED_RECOVERING' : null, managed_rebind:rebind,
    configuration_reload:true, configuration_setup:false, credential_available:true, credential_saved:true,
    enrollment_supported:false, connected, error:null});
  const http = async (service, input) => {
    if (managed && recovering) throw Error('Managed child transport unavailable');
    if (input.path.startsWith('/events') && holdEvents) await holdEvents.promise;
    if (input.path === '/bootstrap' && holdBootstrap) await holdBootstrap.promise;
    const url = origin(service) + '/api/v1' + input.path;
    const headers = {Authorization:`Bearer ${service.token}`, ...(input.body ? {'Content-Type':'application/json'} : {}),
      ...(input.idempotency_key ? {'Idempotency-Key':input.idempotency_key} : {})};
    const body = input.body ? JSON.stringify(input.body) : undefined;
    const row = record(services, run, 'native-http', service, input.method, url, headers, body);
    const response = await fetch(url, {method:input.method, headers, body, redirect:'error'});
    row.status = response.status;
    const data = await response.json();
    if (managed) responses.push({path:input.path, status:response.status, data});
    return {status:response.status, data};
  };
  await page.exposeFunction('twoServiceInvoke', async (command, args) => {
    if (command === 'native_status') {const value = status(); statuses.push(value); return value;}
    if (command === 'connector_connect') {
      const caps = await http(selected, {method:'GET', path:'/capabilities'});
      const bootstrap = await http(selected, {method:'GET', path:'/bootstrap'});
      assert.equal(caps.status, 200); assert.equal(bootstrap.status, 200);
      assert.equal(caps.data.actor, selected.actor);
      const identity = {server_id:bootstrap.data.sync.server_id, principal_id:bootstrap.data.sync.principal_id};
      assert.equal(identity.server_id, selected.server_id); assert.equal(identity.principal_id, selected.principal_id);
      connected = true;
      return managed ? bootstrap.data.capabilities : {...caps.data, desktop_identity:identity};
    }
    if (command === 'connector_request') return http(selected, args.input);
    if (command === 'connector_disconnect') {connected = false; return;}
    if (command === 'connector_reload_configuration') {selected = next; connected = false; return status();}
    if (command === 'fleet_availability') return {configured:false, platform_supported:false};
    throw Error(`Unexpected IPC command ${command}`);
  });
  await page.addInitScript(() => Object.assign(window, {isTauri:true, __TAURI_INTERNALS__:{invoke:(command, args) => window.twoServiceInvoke(command, args)}}));
  await page.route('**/api/v1/**', () => {throw Error('Native Connector request bypassed IPC');});
  return Object.assign(value => {next = value;}, {
    statuses, responses,
    crash() {recovering = true;},
    recover(port) {selected.port = port; recovering = false; rebind++;},
    holdEvents() {holdEvents = gate();}, releaseEvents() {holdEvents?.release(); holdEvents = null;},
    holdBootstrap() {holdBootstrap = gate();}, releaseBootstrap() {holdBootstrap?.release(); holdBootstrap = null;},
  }); // external edit of the fixed configuration, then UI reload
}

async function pass(services, locale, native) {
  const [a, b] = services, run = `${native ? 'native' : 'browser'}-${locale}`, labels = texts(locale);
  const context = await browser.newContext({locale});
  const page = currentPage = await context.newPage(), errors = [];
  await page.addInitScript(() => {
    // Retained legacy globals have no source proof. No identity may adopt them.
    sessionStorage.setItem('batc.repo', 'example/UNOWNED_LEGACY_FACT');
    sessionStorage.setItem('batc.pr', '99');
  });
  page.on('pageerror', error => errors.push(error.message));
  let active = a;
  const browserRequests = new WeakMap();
  context.on('request', request => browserRequests.set(request, record(services, run, 'browser-http', native ? a :
    new URL(request.url()).origin === origin(active) ? active : services.find(value => origin(value) === new URL(request.url()).origin),
    request.method(), request.url(), request.headers(), request.postData())));
  context.on('response', response => {const row = browserRequests.get(response.request()); if (row) row.status = response.status();});
  let setConfiguration;
  if (native) setConfiguration = await nativeBridge(page, services, run, a);
  const namespaces = Object.fromEntries(services.map(value => [value.label, scope(origin(value) + (native ? '/' : ''), value)]));
  // Same port for both browser services, hence exactly the same origin storage.
  if (!native) namespaces.B = scope(origin(a), b);
  const connect = async service => {
    await route(page, '#/settings');
    if (!native) await page.locator('input[type=password]').fill(service.token);
    await page.getByRole('button', {name:labels.connect, exact:true}).click();
    await expect(page).toHaveURL(/#\/home$/);
    await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected');
    await route(page, session);
    await expect(composer(page)).toBeVisible();
    await checkpoint(page, namespaces[service.label], service);
  };
  const switchTo = async (from, to) => {
    await route(page, '#/settings');
    if (native) {
      setConfiguration(to);
      await page.getByRole('button', {name:labels.reload, exact:true}).click();
      await expect(page.getByRole('button', {name:labels.disconnect, exact:true})).toBeDisabled();
    } else {
      await page.getByRole('button', {name:labels.disconnect, exact:true}).click();
      await expect(page.locator('input[type=password]')).toBeVisible();
      assert.equal(await page.evaluate(() => sessionStorage.getItem('batc.dashboard.token') || localStorage.getItem('batc.dashboard.token')), null);
      await from.control('unbind');
      await to.control('unbind');
      to.port = (await to.control('bind', {port:from.port})).port;
      active = to;
      await page.reload();
    }
    await connect(to);
  };
  try {
    await page.goto(origin(a) + '/dashboard/#/settings');
    await connect(a);
    await snapshot(page, run, 'A-connected', namespaces);
    await lists(page, a, b);
    const draftA = `${a.marker} ${run} draft\n保留原始位元組 ✎`, draftB = `${b.marker} ${run} other draft`;
    await composer(page).fill(draftA);
    await snapshot(page, run, 'A-typed', namespaces);
    const response = native ? null : page.waitForResponse(value => value.request().method() === 'POST' && value.url().includes('/operations?wait=3'));
    await page.getByRole('button', {name:labels.send, exact:true}).click();
    if (response) assert.equal((await response).status(), 202);
    const pending = () => page.evaluate(namespace => Object.entries(localStorage)
      .filter(([key]) => key.startsWith(`batc.key.${namespace}.send.`)).map(([key, value]) => ({storageKey:key, raw:value, ...JSON.parse(value)})), namespaces.A);
    await expect.poll(async () => (await pending())[0]?.op || '').toMatch(/^op_/);
    const original = (await pending())[0];
    const report = await a.control('report');
    assert.equal(report.instructions.operations.find(value => value.operation_id === original.op).status, 'accepted');
    await checkpoint(page, namespaces.A, a);
    await receipts(page, labels, original, draftA);
    // A standalone delivery selection is also a source-private route/filter.
    await expect(await delivery(page, labels)).toHaveValue('');
    await delivery(page, labels, `example/${a.marker}`, '17');
    await route(page, session);
    await expect(composer(page)).toHaveValue(draftA);
    const before = await snapshot(page, run, 'A-pending', namespaces);
    if (!native) {
      const otherOrigin = await context.newPage();
      await otherOrigin.goto(origin(b) + '/dashboard/#/settings');
      assert.deepEqual(await otherOrigin.evaluate(() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)]
        .filter(key => key !== 'batc.lang')), [], 'Different origins isolate source storage and credentials');
      await otherOrigin.close();
    }
    await switchTo(a, b);
    await expect(composer(page)).toHaveValue('');
    await privateDOM(page, b, a);
    const enteredB = await snapshot(page, run, 'B-connected', namespaces);
    assert.deepEqual(enteredB.A, before.A, 'A namespace changed on entering B');
    if (native) {
      await route(page, '#/pm');
      await expect(page).not.toHaveURL(new RegExp(a.workspace_id));
    }
    assert.equal(await page.locator(`[data-instruction-operation="${original.op}"]`).count(), 0);
    await lists(page, b, a);
    await receipts(page, labels);
    const inputB = await delivery(page, labels);
    await expect(inputB).toHaveValue('');
    await expect(page.getByRole('textbox', {name:labels.pr, exact:true})).toHaveValue('');
    assert.equal((await dom(page)).includes(a.marker), false);
    assert.equal((await dom(page)).includes('UNOWNED_LEGACY_FACT'), false);
    await route(page, session);
    await expect(composer(page)).toHaveValue('');
    await composer(page).fill(draftB);
    await checkpoint(page, namespaces.B, b);
    const onB = await snapshot(page, run, 'B-draft', namespaces);
    assert.deepEqual(onB.A, before.A, 'A namespace changed while on B');
    const bRequests = requests.filter(value => value.run === run && value.service === 'B');
    assert.ok(bRequests.some(value => value.url.includes('/bootstrap')));
    for (const request of bRequests) {
      assert.notEqual(request.authorization_owner, 'A');
      for (const privateValue of [a.marker, draftA, original.key, original.op, namespaces.A, a.token])
        assert.equal(JSON.stringify(request).includes(privateValue), false, 'A data sent to B');
    }
    await switchTo(b, a);
    await expect(composer(page)).toHaveValue(draftA);
    assert.deepEqual((await pending())[0], original, 'Pending original key/receipt bytes changed');
    const returned = await snapshot(page, run, 'A-returned', namespaces);
    assert.deepEqual(returned.B, onB.B, 'B namespace changed while on A');
    assert.deepEqual(returned.A, before.A, 'A namespace did not restore byte for byte');
    await receipts(page, labels, original, draftA);
    if (native) {
      await route(page, '#/pm');
      await expect(page.locator('[data-workspace-id]').first()).toHaveAttribute('data-workspace-id', a.workspace_id);
      await expect(page).toHaveURL(new RegExp(a.workspace_id));
    }
    await lists(page, a, b, 'h1');
    await expect(await delivery(page, labels)).toHaveValue(`example/${a.marker}`);
    await route(page, session);
    await page.getByRole('button', {name:labels.send, exact:true}).click();
    await expect.poll(() => requests.filter(value => value.run === run && value.method === 'POST' && value.idempotency_key === original.key).length).toBe(2);
    await expect.poll(async () => (await pending())[0]?.raw).toBe(original.raw);
    const sends = requests.filter(value => value.run === run && value.method === 'POST' && value.idempotency_key === original.key);
    await expect.poll(() => sends[1].status).toBe(200);
    assert.equal(sends[0].status, 202);
    assert.ok(sends.every(value => value.service === 'A' && value.target_endpoint === origin(a)));
    assert.equal(sends[0].body, sends[1].body);
    await checkpoint(page, namespaces.A, a);
    assert.deepEqual((await snapshot(page, run, 'A-replayed', namespaces)).B, onB.B);
    assert.equal((await a.control('report')).sends.length, 0, 'Paused workers must not send to BAT');
    assert.equal((await b.control('report')).instructions.operations.length, 0);
    assert.deepEqual(errors, []);
    assert.deepEqual(await page.evaluate(() => [sessionStorage.getItem('batc.repo'), sessionStorage.getItem('batc.pr')]), ['example/UNOWNED_LEGACY_FACT', '99']);
    if (native) assert.equal(await page.evaluate(() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)].includes('batc.dashboard.token')), false);
    results.push({run, requests:requests.filter(value => value.run === run).length, snapshots:snapshots.filter(value => value.run === run).length});
    console.log(`${run}: A → B → A, pending original-key replay, private facts, lists, checkpoints and byte-exact storage passed`);
  } catch (error) {
    await snapshot(page, run, 'failure', namespaces);
    await page.screenshot({path:join(evidence, `${run}-failure.png`), fullPage:true});
    throw error;
  } finally {await context.close(); currentPage = null;}
}

async function managedPass(service, locale, reason) {
  const run = `managed-${reason}-${locale}`, labels = texts(locale);
  const context = await browser.newContext({locale, viewport:{width:1280, height:900}});
  const page = currentPage = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const bridge = await nativeBridge(page, [service], run, service);
  let before, namespace;
  const raw = async step => {
    const storage = await storageSnapshot(page);
    snapshots.push({run, step, namespace, ...storage});
    return storage;
  };
  try {
    // Page assets keep one browser origin while native transport changes ports.
    await page.goto(origin(service) + '/dashboard/' + session);
    await expect(composer(page)).toBeVisible();
    const identity = bridge.responses.find(value => value.path === '/bootstrap').data;
    namespace = ['managed', 'v1', identity.sync.server_id, identity.sync.principal_id, identity.capabilities.actor].map(encodeURIComponent).join(':');
    const checkpointKey = `batc.sync.${namespace}`;
    const pending = () => page.evaluate(namespace => Object.entries(localStorage)
      .filter(([key]) => key.startsWith(`batc.key.${namespace}.send.`)).map(([storageKey, value]) => ({storageKey, raw:value, ...JSON.parse(value)})), namespace);
    const sentText = `Real accepted instruction ${run}`;
    await composer(page).fill(sentText);
    await page.getByRole('button', {name:labels.send, exact:true}).click();
    await expect.poll(async () => (await pending())[0]?.op || '').toMatch(/^op_/);
    const original = (await pending())[0];
    assert.equal((await service.control('report')).instructions.operations.find(value => value.operation_id === original.op).status, 'accepted');
    const draft = `Unsent ${run}\n保留原始位元組 ✎`;
    await composer(page).fill(draft);
    await checkpoint(page, namespace, service);
    // Other namespaces and unscoped bytes must survive too. These are storage
    // sentinels, never substitute API facts/receipts/checkpoints.
    await page.evaluate(() => {
      localStorage.setItem('batc.draft.managed:v1:other:principal:actor.send.h1.other', 'Other namespace ✎');
      localStorage.setItem('batc.draft..untouched', 'Empty namespace sentinel');
      sessionStorage.setItem('batc.tree.managed:v1:other:principal:actor', '["other"]');
      sessionStorage.setItem('batc.repo', 'example/retained-legacy');
    });
    before = await raw('before-crash');
    const stale = before.localStorage.find(([key]) => key === checkpointKey)[1];
    const oldPort = service.port, crashIndex = requests.length;
    bridge.crash();
    const crash = await service.control('crash');
    assert.equal(crash.occupied_port, oldPort);
    await expect(page.locator('#live')).toContainText(locale === 'zh-TW' ? '正在重新啟動本機背景服務' : 'Restarting local service', {timeout:10000});
    if (reason === 'cursor_expired') await service.control(reason);
    bridge.holdEvents(); bridge.holdBootstrap();
    const recovered = await service.control('recover');
    assert.equal(recovered.same_identity_and_token, true);
    assert.equal(recovered.children, 2);
    assert.notEqual(recovered.port, oldPort);
    bridge.recover(recovered.port);
    if (reason === 'access_changed') {
      const membership = await service.control(reason);
      assert.equal(membership.status, 'succeeded');
    }
    const bootstrapCount = bridge.responses.filter(value => value.path === '/bootstrap').length;
    bridge.releaseEvents();
    await expect.poll(() => bridge.responses.filter(value => value.path.startsWith('/events') && new URL('http://fixture' + value.path).searchParams.get('checkpoint') === JSON.parse(stale).token && value.status === 409).length, {timeout:10000}).toBe(1);
    const first = requests.slice(crashIndex).find(value => value.url.includes('/events'));
    assert.equal(first.target_endpoint, origin(service));
    assert.equal(first.status, 409, 'The first forwarded poll after recovery must reset');
    const reset = bridge.responses.findLast(value => value.path.startsWith('/events'));
    assert.equal(reset.data.error.code, 'EVENT_CURSOR_RESET');
    assert.equal(reset.data.error.reason, reason);
    if (reason === 'access_changed') {
      await expect(composer(page)).toHaveCount(0);
      await expect(page.locator('.message-body').filter({hasText:service.marker})).toHaveCount(0);
    } else {
      await expect(composer(page)).toHaveValue(draft);
      await expect(page.locator('.message-body').filter({hasText:service.marker}).first()).toBeVisible();
    }
    assertStoragePreserved(before, await raw('reset-bootstrap-held'), checkpointKey);
    bridge.releaseBootstrap();
    await expect.poll(() => bridge.responses.filter(value => value.path === '/bootstrap').length).toBe(bootstrapCount + 1);
    const refreshed = bridge.responses.findLast(value => value.path === '/bootstrap').data.sync.checkpoint;
    await expect(composer(page)).toHaveValue(draft);
    await expect(page.locator('.message-body').filter({hasText:service.marker}).first()).toBeVisible();
    await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected', {timeout:10000});
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), checkpointKey)).toBe(JSON.stringify(refreshed));
    assert.notEqual(JSON.stringify(refreshed), stale);
    const after = await raw('reset-settled');
    assertStoragePreserved(before, after, checkpointKey);
    assert.deepEqual(await pending(), [original]);
    assert.equal(requests.slice(crashIndex).filter(value => value.method === 'POST').length, 0, 'No writes during crash/recovery/reset');
    assert.ok(bridge.statuses.some(value => value.managed_rebind === 1 && value.endpoint === origin(service) + '/'));
    // Preserve origin/storage while reloading; the old port is occupied. Serve
    // only page assets from the current runtime. Connector traffic still uses IPC.
    await page.route('**/dashboard/**', async route => {
      const url = new URL(route.request().url());
      await route.fulfill({response:await route.fetch({url:origin(service) + url.pathname + url.search})});
    });
    await page.reload();
    await expect(composer(page)).toHaveValue(draft);
    await checkpoint(page, namespace, service);
    assertStoragePreserved(before, await raw('reloaded'), checkpointKey);
    assert.deepEqual(await pending(), [original]);
    await receipts(page, labels, original, sentText);
    // This pending receipt has no resend control. Submitting the identical
    // original composer request is the UI's explicit original-key retry path.
    assert.equal(await page.locator(`[data-instruction-operation="${original.op}"] button`).count(), 0);
    assert.equal(requests.slice(crashIndex).filter(value => value.method === 'POST').length, 0);
    await composer(page).fill(sentText);
    await page.getByRole('button', {name:labels.send, exact:true}).click();
    await expect.poll(() => requests.filter(value => value.run === run && value.method === 'POST' && value.idempotency_key === original.key).length).toBe(2);
    const sends = requests.filter(value => value.run === run && value.method === 'POST');
    await expect.poll(() => sends[1].status).toBe(200);
    assert.equal(sends[1].target_endpoint, origin(service));
    assert.equal(sends[1].body, sends[0].body);
    assert.deepEqual(await pending(), [original]);
    const report = await service.control('report');
    assert.equal(report.instructions.operations.length, 1);
    assert.equal(report.instructions.operations[0].operation_id, original.op);
    assert.deepEqual(report.sends, []);
    assert.deepEqual(errors, []);
    results.push({run, reason, oldPort, newPort:service.port, checkpointChanged:true, namespacesByteIdentical:true,
      noPostsDuringRecovery:true, receiptResendControl:false, explicitComposerReplay:true});
    console.log(`${run}: real crash/rebind → first-poll ${reason} → byte-exact storage → reload → original-key replay passed`);
  } catch (error) {
    await raw('failure');
    await page.screenshot({path:join(evidence, `${run}-failure.png`), fullPage:true});
    throw error;
  } finally {
    bridge.releaseEvents(); bridge.releaseBootstrap();
    await context.close(); currentPage = null;
  }
}

try {
  browser = await chromium.launch();
  for (const locale of locales) {
    if (managedMode) {
      for (const reason of ['cursor_expired', 'access_changed']) {
        const service = await fixture('managed');
        await managedPass(service, locale, reason);
        await service.stop();
      }
      continue;
    }
    const services = await Promise.all([fixture('A'), fixture('B')]);
    const [a, b] = services;
    assert.equal(a.actor, b.actor);
    for (const field of ['server_id', 'principal_id', 'project_id', 'work_item_id', 'workspace_id']) assert.notEqual(a[field], b[field]);
    await pass(services, locale, false);
    // Restore independent listeners before the external native configuration run.
    b.port = (await b.control('bind')).port;
    await pass(services, locale, true);
    await Promise.all(services.map(service => service.stop()));
  }
  console.log(`${managedMode ? 'Managed recovery/reset' : 'Two-service isolation'}: ${results.length} full passes in ${((performance.now() - started) / 1000).toFixed(2)}s`);
} catch (error) {
  if (currentPage && !currentPage.isClosed()) await currentPage.screenshot({path:join(evidence, 'failure.png'), fullPage:true});
  throw error;
} finally {
  await writeFile(join(evidence, 'requests.json'), JSON.stringify(requests, null, 2) + '\n');
  await writeFile(join(evidence, 'storage.json'), JSON.stringify(snapshots, null, 2) + '\n');
  await writeFile(join(evidence, 'results.json'), JSON.stringify({results, seconds:(performance.now() - started) / 1000}, null, 2) + '\n');
  await browser?.close();
  await Promise.all(children.map(child => child.stop()));
  await rm(scratch, {recursive:true, force:true});
}
