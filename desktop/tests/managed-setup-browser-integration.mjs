// Packaged central with an empty PATH; development Python supplies only MockBAT.
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

const temporaryRoot = process.env.BATC_TEST_TMP || resolve("../.test-tmp");
await mkdir(temporaryRoot, {recursive: true, mode: 0o700});
const temporary = await mkdtemp(join(temporaryRoot, "managed-setup-browser-"));
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
function runtime(command) {
  const result = spawnSync(executable, [command, "--data-dir", directory], {encoding: "utf8", timeout: 60000,
    env: {...process.env, PATH: "", PYTHONPATH: "", PYTHONHOME: ""}});
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
  await form.getByRole("button", {name: "Verify and save host", exact: true}).click();
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
  runtime("stop"); active = false;
  active = true;
  const second = runtime("ensure");
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
