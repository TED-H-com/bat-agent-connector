// Packaged central with an empty PATH; development Python supplies only MockBAT.
import {testScratchRoot} from './test-scratch.mjs';
// No live host/provider writes. All tokens travel through anonymous pipes or HTTP.
import assert from "node:assert/strict";
import {spawn, spawnSync} from "node:child_process";
import {createHash} from "node:crypto";
import {once} from "node:events";
import {chmod, mkdir, mkdtemp, readFile, readdir, rm, writeFile} from "node:fs/promises";
import {join, resolve} from "node:path";
import {createInterface} from "node:readline";
import {setTimeout as delay} from "node:timers/promises";
import {pathToFileURL} from "node:url";
import {gunzipSync} from "node:zlib";
import {chromium, expect} from "@playwright/test";

// Pytest supplies the existing managed installation instead of a packaged child.
// Both modes drive the same served frontend through Chromium and real HTTP.
if (process.argv.includes('--read-existing')) {
  await readExistingSession();
  process.exit(0);
}
if (process.argv.includes('--resume-initial')) {
  await resumeInitialRead();
  process.exit(0);
}

async function resumeInitialRead() {
  const lines = createInterface({input: process.stdin})[Symbol.asyncIterator]();
  const fixture = JSON.parse((await lines.next()).value);
  const browser = await chromium.launch();
  const context = await browser.newContext({locale: fixture.locale === 'zh-TW' ? 'zh-TW' : 'en-US'});
  const finished = [], reads = [], errors = [], posts = [], released = new Set();
  const send = command => process.stdout.write(JSON.stringify(command) + '\n');
  const releases = Array.from({length: 4}, (_, index) => () => {
    if (!released.has(index)) {released.add(index); send({command: 'release', index});}
  });
  let armed = false;
  const notifications = (async () => {
    for await (const line of lines) {
      const event = JSON.parse(line);
      if (event.stage === 'armed') armed = true;
      if (event.stage === 'source') reads[event.index] = {status: event.status};
      if (event.stage === 'released') finished[event.index] = true;
    }
  })();
  let page, polls = 0, completedSources = 0;
  const transport = [];
  context.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.startsWith('/api/v1/operations')) posts.push(request.url());
  });
  const visibility = value => page.evaluate(value => {
    Object.defineProperty(document, 'visibilityState', {configurable: true, value});
    document.dispatchEvent(new Event('visibilitychange'));
  }, value);
  const storage = target => target.evaluate(() => ({...localStorage}));
  const sourceView = () => page.locator('[data-managed-setup]').evaluate(node => {
    const source = node.cloneNode(true);
    // The separate connection card's poll clock advances independently of Setup.
    source.querySelector('[data-connection-card]')?.remove();
    return source.innerHTML;
  });
  const draftField = target => target.locator('[data-setup-field=host]');
  try {
    // Produce the draft through the existing form, before starting a new app page.
    const prime = await context.newPage();
    await prime.goto(pathToFileURL(fixture.handoff).href);
    await prime.waitForURL(fixture.origin + '/dashboard/**');
    const automatic = prime.locator('.setup-automatic');
    await expect(automatic).toBeEnabled();
    await prime.locator('[data-setup-advanced] > summary').click();
    const beforeDraft = await storage(prime);
    const draft = 'unfinished-connector';
    await draftField(prime).fill(draft);
    const saved = await storage(prime);
    const changed = Object.keys(saved).filter(key => saved[key] !== beforeDraft[key]);
    assert.equal(changed.length, 1, 'The app must produce the draft storage entry');
    const draftStorage = changed[0], draftBytes = saved[draftStorage];
    assert.equal(JSON.parse(draftBytes).intent?.key ?? null, null);
    await prime.close();

    page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.on('requestfailed', request => transport.push({path: new URL(request.url()).pathname, failure: request.failure()}));
    page.on('requestfinished', request => {
      if (new URL(request.url()).pathname === '/api/v1/managed/setup') completedSources++;
    });
    page.on('request', request => {if (new URL(request.url()).pathname === '/api/v1/events') polls++;});
    // The daemon holds its own handler result after the real producer returns.
    // Browser HTTP, cookie, origin and CSRF checks run without interception.
    send({command: 'arm'});
    await expect.poll(() => armed).toBe(true);
    if (fixture.entry === 'route') {
      await page.goto(fixture.origin + '/dashboard/#/projects');
      await expect.poll(() => polls).toBeGreaterThan(0);
      await page.evaluate(() => {location.hash = '#/settings';});
    } else await page.goto(fixture.origin + '/dashboard/#/settings');
    await expect.poll(() => reads.length).toBe(1);
    const button = page.locator('.setup-automatic');
    await page.locator('[data-setup-advanced] > summary').click();
    await expect(draftField(page)).toHaveValue(draft);
    await expect(button).toBeDisabled();
    if (fixture.entry === 'startup') assert.equal(polls, 0);
    await visibility('hidden'); await visibility('visible');
    // The first real response stays held. The replacement has its own response.
    await expect.poll(() => reads.length).toBe(2);
    await expect(button).toBeDisabled();
    assert.equal(finished[0], undefined);
    assert.equal((await storage(page))[draftStorage], draftBytes);
    const beforeFresh = polls;
    releases[1]();
    await expect(button).toBeEnabled();
    await expect.poll(() => polls).toBeGreaterThan(beforeFresh);
    await expect(draftField(page)).toHaveValue(draft);
    const editor = await draftField(page).elementHandle();
    const fresh = await sourceView();
    await page.locator('[data-managed-setup]').evaluate(node => {
      window.setupSourceMutations = [];
      window.setupSourceObserver = new MutationObserver(records => {
        for (const record of records) {
          const target = record.target instanceof Element ? record.target : record.target.parentElement;
          if (!target?.closest('[data-connection-card]')) window.setupSourceMutations.push(record.type);
        }
      });
      window.setupSourceObserver.observe(node, {subtree: true, childList: true, attributes: true, characterData: true});
    });
    releases[0]();
    await expect.poll(() => finished[0]).toBe(true);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.deepEqual(await page.evaluate(() => {
      window.setupSourceObserver.disconnect();
      return window.setupSourceMutations;
    }), []);
    assert.equal(await sourceView(), fresh);
    assert.equal(await draftField(page).evaluate((node, original) => node === original, editor), true);
    await expect(draftField(page)).toHaveValue(draft);
    assert.equal((await storage(page))[draftStorage], draftBytes);

    // Full route changes also start with viewReady=false. Leave during the
    // renewed read to exercise the real route-generation guard before publish.
    await page.evaluate(() => {location.hash = '#/projects';});
    await expect(page.locator('[data-managed-setup]')).toHaveCount(0);
    await page.evaluate(() => {location.hash = '#/settings';});
    await expect.poll(() => reads.length).toBe(3);
    await visibility('hidden'); await visibility('visible');
    await expect.poll(() => reads.length).toBe(4);
    await expect(button).toBeDisabled();
    await page.evaluate(() => {location.hash = '#/projects';});
    await expect(page.locator('[data-managed-setup]')).toHaveCount(0);
    await expect(page.locator('#main h1')).toHaveText(fixture.locale === 'zh-TW' ? '專案' : 'Projects');
    await expect(page.locator('#main .or-archive-filter')).toBeVisible();
    const left = await page.locator('#main').innerHTML();
    releases[3](); releases[2]();
    await expect.poll(() => finished.filter(Boolean).length).toBe(4);
    await expect.poll(() => completedSources).toBe(2);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await page.locator('#main').innerHTML(), left);
    assert.equal((await storage(page))[draftStorage], draftBytes);
    assert.deepEqual(posts, []);
    assert.deepEqual(errors, []);
    assert(reads.every(read => read.status === 200));
    const identity = await page.evaluate(async () => {
      const session = await (await fetch('/api/v1/browser-session')).json();
      const response = await fetch('/api/v1/bootstrap', {headers: {'X-Batc-CSRF': session.csrf}});
      if (!response.ok) throw Error(`Bootstrap read failed (${response.status})`);
      return (await response.json()).capabilities.identity;
    });
    assert.deepEqual(identity, fixture.identity);
    await page.screenshot({path: join(fixture.shots, fixture.entry + '-renewed.png')});
    process.stdout.write(JSON.stringify({identity, operation_posts: posts.length, source_reads: reads.length,
      draft_key: JSON.parse(draftBytes).intent?.key ?? null, polls, entry: fixture.entry}) + '\n');
  } catch (error) {
    console.error(JSON.stringify({reads, finished, polls, operation_posts: posts.length, errors, transport, text: await page?.locator('#main').textContent()}));
    await page?.screenshot({path: join(fixture.shots, fixture.entry + '-failure.png')}).catch(() => {});
    throw error;
  } finally {
    releases.forEach(release => release());
    await browser.close();
    void notifications;
  }
}

async function readExistingSession() {
  const lines = createInterface({input: process.stdin})[Symbol.asyncIterator]();
  const fixture = JSON.parse((await lines.next()).value);
  const zh = fixture.locale === 'zh-TW';
  const browser = await chromium.launch();
  let page;
  try {
    const context = await browser.newContext({locale: zh ? 'zh-TW' : 'en-US',
      viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'});
    page = await context.newPage();
    page.setDefaultTimeout(20000);
    const errors = [], posts = [], operations = [], bearer = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (request.headers().authorization) bearer.push(new URL(request.url()).pathname);
      if (request.method() !== 'POST') return;
      const path = new URL(request.url()).pathname;
      posts.push(path);
      if (path === '/api/v1/operations') operations.push(request.postDataJSON());
    });
    const read = path => page.evaluate(async path => {
      const session = await (await fetch('/api/v1/browser-session')).json();
      const response = await fetch('/api/v1' + path, {headers: {'X-Batc-CSRF': session.csrf}});
      if (!response.ok) throw Error(`Read failed (${response.status})`);
      return response.json();
    }, path);
    await page.goto(pathToFileURL(fixture.handoff).href);
    await page.waitForURL(fixture.origin + '/dashboard/**');
    const before = await read('/bootstrap');
    assert.deepEqual(before.capabilities.identity, fixture.identity);
    assert.deepEqual(before.snapshot.hosts.hosts, []);
    assert.deepEqual((await read('/managed/setup')).hosts, []);
    const setup = page.locator('[data-managed-setup]');
    await expect(setup).toBeVisible();
    await expect(page.locator('[data-setup-advanced]')).not.toHaveAttribute('open', '');
    await setup.getByRole('button', {name: zh ? '尋找設定檔與工作區' : 'Find profiles and workspaces', exact: true}).click();
    const copy = setup.locator('[data-setup-copy]');
    await expect(copy.locator('input')).toHaveAttribute('type', 'password');
    // This is the only user input: a connection copied from BAT, never an internal ID.
    await copy.locator('input').fill(fixture.connection_url);
    await expect(copy.locator('input')).toHaveValue('');
    await expect(copy.locator('[role=status]')).toBeVisible();
    for (const name of ['host', 'url', 'fingerprint', 'profile_id', 'managed_roots', 'ssh_alias'])
      await expect(setup.locator(`[data-setup-field=${name}]`)).toBeHidden();
    assert.deepEqual(operations, []); // Review precedes secret staging and apply.
    await copy.locator('button[type=submit]').click();
    await expect(setup.locator('[data-setup-discovered-profile]')).toHaveValue('default');
    await setup.getByRole('button', {name: zh ? '預覽連線' : 'Review connection', exact: true}).first().click();
    await expect(setup.locator('[data-setup-review]')).toBeVisible();
    assert.deepEqual(operations, []);
    await setup.getByRole('button', {name: zh ? '套用連線' : 'Apply connection', exact: true}).click();
    const sessions = setup.locator('[data-setup-sessions]');
    await expect(sessions).toBeVisible();
    assert.equal(operations.length, 1);
    assert.equal(operations[0].action, 'setup.host');
    assert.equal(operations[0].params.writes, false);
    assert.equal(operations[0].params.orchestrate, false);
    const receipt = await read('/operations?limit=50');
    assert.equal(receipt.operations.length, 1);
    assert.equal(receipt.operations[0].status, 'succeeded');
    await sessions.click();
    await expect(page).toHaveURL(/#\/sessions$/);
    const source = page.locator('#main .session-results a.title').filter({hasText: fixture.title}).first();
    await expect(source).toHaveText(fixture.title);
    const href = await source.getAttribute('href');
    assert.equal(href, `#/session/${operations[0].target.host}/${fixture.session_id}`);
    await source.click();
    await expect(page.locator('.workspace-session-heading')).toContainText(fixture.title);
    await expect(page.locator('.conversation')).toContainText('live message 9');
    await expect(page.locator('.conversation')).toContainText('archived 49');
    const details = page.locator('[data-session-details]');
    if (!(await details.evaluate(node => node.open))) await details.locator(':scope > summary').click();
    await expect(details).toContainText(operations[0].target.host);
    await expect(details).toContainText(fixture.session_id);
    await expect(details).toContainText('demo-project');
    await expect(details).toContainText(zh ? '人工建立（BAT）' : 'created in BAT');
    await expect(details).toContainText(zh ? 'API 唯讀' : 'API read-only');
    const session = await read(`/sessions/${operations[0].target.host}/${fixture.session_id}`);
    assert.equal(session.session.provenance, 'manual');
    assert.equal(session.session.api_access, 'read_only');
    assert.deepEqual(session.work_items || [], []);
    assert.equal(new URL(page.url()).hash, href);
    await page.reload();
    await expect(page.locator('.workspace-session-heading')).toContainText(fixture.title);
    await expect(page.locator('.conversation')).toContainText('live message 9');
    await expect(page.locator('.conversation')).toContainText('archived 49');
    assert.deepEqual((await read('/bootstrap')).capabilities.identity, fixture.identity);
    assert.deepEqual(posts, ['/api/v1/browser-handoff', '/api/v1/managed/setup/secrets', '/api/v1/managed/setup/discover', '/api/v1/managed/setup/preview', '/api/v1/operations']);
    assert.deepEqual(bearer, []);
    assert.deepEqual(errors, []);
    const cookies = await context.cookies();
    assert(cookies.some(cookie => cookie.httpOnly && cookie.sameSite === 'Strict' && cookie.name.startsWith('batc_')));
    await page.screenshot({path: join(fixture.shots, 'session-readable.png')});
    process.stdout.write(JSON.stringify({identity: fixture.identity, actions: operations.map(op => op.action),
      operation_id: receipt.operations[0].operation_id, route: href, posts, browser: browser.version()}) + '\n');
  } catch (error) {
    await page?.screenshot({path: join(fixture.shots, 'failure.png')}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
}

const temporary = await mkdtemp(join(testScratchRoot(), "managed-setup-browser-"));
// Keep browser and child-process temporary files inside this owned directory.
process.env.TMPDIR = process.env.TEMP = process.env.TMP = temporary;
const directory = join(temporary, "installation");
const python = process.env.BATC_TEST_PYTHON || resolve(process.platform === "win32" ? "../.venv/Scripts/python.exe" : "../.venv/bin/python");
const packageDirectory = resolve(process.env.BATC_TEST_RUNTIME_PACKAGE || "managed-runtime");
const metadata = JSON.parse(await readFile(join(packageDirectory, "manifest.json"), "utf8"));
const payload = await readFile(join(packageDirectory, "runtime.gz"));
const digest = data => createHash("sha256").update(data).digest("hex");
assert.equal(digest(payload), metadata.payload_sha256);
const executableBytes = gunzipSync(payload);
assert.equal(digest(executableBytes), metadata.sha256);
assert.equal(executableBytes.length, metadata.size);
const executable = join(temporary, process.platform === "win32" ? "runtime.exe" : "runtime");
await writeFile(executable, executableBytes, {mode: 0o700});
await chmod(executable, 0o700);
const stoppedGolden = JSON.parse(await readFile(new URL('../../tests/fixtures/managed-stop-hold.json', import.meta.url), 'utf8'));
function runtime(command, {stopped} = {}) {
  const result = spawnSync(executable, [command, "--data-dir", directory], {encoding: "utf8", timeout: 60000,
    env: {...process.env, PATH: "", PYTHONPATH: "", PYTHONHOME: ""}});
  if (stopped) {
    assert.equal(result.status, stoppedGolden.exit);
    const reply = JSON.parse(result.stdout);
    assert.deepEqual(reply, {...stoppedGolden.reply, lifecycle: {...stoppedGolden.reply.lifecycle,
      requested_at: stopped.requested_at, stopped_at: stopped.stopped_at}});
    return reply;
  }
  let failure = {};
  if (result.status !== 0) {try {failure = JSON.parse(result.stdout);} catch { /* no unsafe output */ }}
  assert.equal(result.status, 0, `packaged managed ${command} failed (${result.error?.name || result.status}; ${failure.error || ""}; ${failure.message || ""})`);
  return JSON.parse(result.stdout);
}
async function disposeFailedFixture() {
  // Diagnostics contain synthetic fixture data only; never copy identity tokens.
  const evidence = resolve("test-results/managed-setup");
  await mkdir(evidence, {recursive: true, mode: 0o700});
  for (const name of ["installation.json", "service.log", "state/task-service.json"]) {
    try {
      const data = (await readFile(join(directory, name), "utf8")).replace(/batc_[A-Za-z0-9_-]+/g, "[redacted]");
      await writeFile(join(evidence, name.replaceAll("/", "-")), data, {mode: 0o600});
    } catch { /* initialization may not have reached this file */ }
  }
  if (process.platform !== "linux") throw Error("Preserved failed fixture; owned runtime stop needs review");
  // Only test-created processes with this unique extracted executable AND data
  // directory are eligible. A PID, port or production service pointer is insufficient.
  for (const entry of await readdir("/proc")) {
    if (!/^\d+$/.test(entry)) continue;
    try {
      const args = (await readFile(`/proc/${entry}/cmdline`, "utf8")).split("\0");
      if (args[0] === executable && args[1] === "serve" && args[2] === "--data-dir" && args[3] === directory) {
        process.kill(Number(entry), "SIGTERM");
      }
    } catch { /* process exited while inspecting its exact owned arguments */ }
  }
}
let mock, browser, active = false;
try {
  mock = spawn(python, [resolve("tests/managed-setup-mock.py")], {cwd: resolve(".."),
    env: {...process.env, PYTHONPATH: resolve("..")}, stdio: ["pipe", "pipe", "inherit"]});
  const lines = createInterface({input: mock.stdout})[Symbol.asyncIterator]();
  const next = async () => {
    const result = await lines.next();
    assert(!result.done, "mock BAT exited before replying");
    return JSON.parse(result.value);
  };
  const snapshot = async () => {mock.stdin.write(JSON.stringify({action: "snapshot"}) + "\n"); return next();};
  const bat = await next();
  active = true;
  const first = runtime("ensure");
  browser = await chromium.launch();
  const context = await browser.newContext({locale: "en-US"});
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  const errors = [], bearerRequests = [];
  page.on("pageerror", error => errors.push(error.stack || error.message));
  page.on("request", request => {if (request.headers().authorization) bearerRequests.push(request.url());});
  await page.goto(pathToFileURL(runtime("browser").handoff_file).href);
  await page.waitForURL(first.endpoint + "/dashboard/**");
  await page.goto(first.endpoint + "/dashboard/#/settings");
  const form = page.locator("[data-setup-host]");
  await page.locator("[data-setup-advanced] > summary").click();
  await expect(form).toBeVisible();
  const session = await page.evaluate(async () => (await fetch("/api/v1/browser-session")).json());
  assert.equal(session.principal_id, first.principal_id);
  const call = (method, path, body, key, csrf = session.csrf) => page.evaluate(async input => {
    const response = await fetch("/api/v1" + input.path, {method: input.method, credentials: "same-origin",
      headers: {"x-batc-csrf": input.csrf, ...(input.body ? {"Content-Type": "application/json"} : {}),
        ...(input.key ? {"Idempotency-Key": input.key} : {})}, ...(input.body ? {body: JSON.stringify(input.body)} : {})});
    return {status: response.status, body: await response.json()};
  }, {method, path, body, key, csrf});
  const initial = await call("GET", "/managed/setup");
  assert.equal(initial.status, 200); assert.equal(initial.body.hosts.length, 0);
  const rejected = await call("POST", "/managed/setup/secrets", {kind: "bat", value: bat.token}, null, "wrong-csrf");
  assert.equal(rejected.status, 403);
  // Exercise the real shared host form, secret staging and durable operation receipt.
  await form.getByLabel("Paste BAT connection URL", {exact: true}).fill(`${bat.url}?token=${encodeURIComponent(bat.token)}&fp=${encodeURIComponent(bat.fingerprint)}`);
  await expect(form.getByLabel("Paste BAT connection URL", {exact: true})).toHaveValue("");
  await form.getByLabel("Host ID", {exact: true}).fill("fixture");
  await form.getByRole("button", {name: "Find profiles and workspaces", exact: true}).click();
  await expect(page.locator('[data-setup-discovered-profile]')).toHaveValue('default');
  await form.getByRole("button", {name: "Review connection", exact: true}).click();
  await page.getByRole("button", {name: "Apply connection", exact: true}).click();
  await expect.poll(async () => (await call("GET", "/managed/setup")).body.hosts.length, {timeout: 20000}).toBe(1);
  const configured = (await call("GET", "/managed/setup")).body;
  assert.equal(configured.hosts[0].name, "fixture");
  assert(configured.hosts[0].token_available);
  assert.notEqual(configured.revision, initial.body.revision);
  const facts = await snapshot();
  assert(facts.auth_count >= 1); assert.deepEqual(facts.writes, []);
  const storage = await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
  assert(!storage.includes(first.token) && !storage.includes(bat.token));
  const rawConfig = await readFile(join(directory, "config", "hosts.toml"), "utf8");
  assert(!rawConfig.includes(bat.token));
  assert.deepEqual(bearerRequests, []);
  assert((await context.cookies()).some(cookie => cookie.httpOnly && cookie.sameSite === "Strict" && cookie.name.startsWith("batc_")));
  // A valid manage credential for the same actor but another scope identity is
  // deliberately insufficient to modify the personal installation.
  const admin = (await readFile(join(directory, "state", "task-admin.token"), "utf8")).trim();
  const issue = await fetch(first.endpoint + "/rpc", {method: "POST", headers: {Authorization: "Bearer " + admin, "Content-Type": "application/json"},
    body: JSON.stringify({method: "api_token_issue", params: {actor: first.actor, scopes: ["observe", "manage"]}})});
  const other = (await issue.json()).result.token;
  const forged = await fetch(first.endpoint + "/api/v1/managed/setup/secrets", {method: "POST",
    headers: {Authorization: "Bearer " + other, "Content-Type": "application/json"}, body: JSON.stringify({kind: "bat", value: bat.token})});
  assert.equal(forged.status, 403);
  const settings = {commands: {"fixture-project": ["python3", "-m", "pytest", "-q"]}, timeout_s: 120};
  const verification = await call("POST", "/operations?wait=3", {action: "setup.verification", target: {}, params: settings,
    preconditions: {config_revision: configured.revision}}, "fixture-verification-original-intent");
  assert(verification.body.operation?.operation_id, "verification must have a durable operation receipt");
  const operationPath = `/operations/${verification.body.operation.operation_id}`;
  await expect.poll(async () => (await call("GET", operationPath)).body.operation.status, {timeout: 20000}).toBe("succeeded");
  const finalRevision = (await call("GET", operationPath)).body.operation.result.revision;
  await context.close();
  const stopped = runtime("stop").lifecycle;
  // Keep cleanup armed: a refused command or partial resume must never leave a
  // service or its stop hold behind when this owned directory is removed.
  runtime("ensure", {stopped});
  runtime("browser", {stopped});
  const second = runtime("resume");
  assert.equal(second.server_id, first.server_id); assert.equal(second.principal_id, first.principal_id);
  const reopened = await browser.newContext({locale: "en-US"});
  const secondPage = await reopened.newPage();
  await secondPage.goto(pathToFileURL(runtime("browser").handoff_file).href);
  await secondPage.waitForURL(second.endpoint + "/dashboard/**");
  const restored = await secondPage.evaluate(async () => {
    const session = await (await fetch("/api/v1/browser-session")).json();
    return (await fetch("/api/v1/managed/setup", {headers: {"x-batc-csrf": session.csrf}})).json();
  });
  assert.equal(restored.revision, finalRevision); assert.equal(restored.hosts[0].name, "fixture");
  assert.deepEqual(restored.verification, settings);
  assert.deepEqual((await snapshot()).writes, []);
  await reopened.close();
  assert.deepEqual(errors, []);
  console.log("Packaged managed setup: real browser form, cookie/CSRF, pinned MockBAT, no host writes, identity refusal and restart passed");
} finally {
  await browser?.close();
  try {
    if (active) {
      try {runtime("stop");}
      catch (error) {await disposeFailedFixture(); throw error;}
    }
  } finally {
    if (mock && mock.exitCode === null) {
      mock.stdin.end(JSON.stringify({action: "stop"}) + "\n");
      await Promise.race([once(mock, "exit"), delay(5000, undefined, {ref: false}).then(() => mock.kill())]);
    }
    await rm(temporary, {recursive: true, force: true});
  }
}
