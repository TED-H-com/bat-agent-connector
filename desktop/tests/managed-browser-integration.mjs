// Real central + file POST navigation, HttpOnly/CSRF and restart continuity.
import {testScratchRoot} from './test-scratch.mjs';
import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {mkdtemp, rm, cp, appendFile, readFile} from "node:fs/promises";
import {resolve, join, delimiter} from "node:path";
import {pathToFileURL, fileURLToPath} from "node:url";
import {chromium} from "@playwright/test";
import {STRINGS} from '../src/i18n.js';
const locale = process.env.BATC_TEST_LOCALE || 'en-US';
const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
const temporary = await mkdtemp(join(testScratchRoot(), "managed-browser-"));
// Keep browser and child-process temporary files inside this owned directory.
process.env.TMPDIR = process.env.TEMP = process.env.TMP = temporary;
const directory = join(temporary, "installation");
// A private package copy lets the real runtime read changed assets without ever
// mutating the checkout or another installation.
const pythonRoot = join(temporary, 'python');
const packageRoot = join(pythonRoot, 'bat_agent_connector');
await cp(fileURLToPath(new URL('../../src/bat_agent_connector', import.meta.url)), packageRoot,
  {recursive: true, filter: source => !source.includes('__pycache__')});
const python = process.env.BATC_TEST_PYTHON || resolve(process.platform === "win32" ? "../.venv/Scripts/python.exe" : "../.venv/bin/python");
const stoppedGolden = JSON.parse(await readFile(new URL('../../tests/fixtures/managed-stop-hold.json', import.meta.url), 'utf8'));
function runtime(command, {stopped} = {}) {
  const result = spawnSync(python, ["-m", "bat_agent_connector.managed_runtime", command, "--data-dir", directory],
    {encoding: "utf8", timeout: 60000, cwd: resolve(".."),
      env: {...process.env, PYTHONPATH: [pythonRoot, process.env.PYTHONPATH].filter(Boolean).join(delimiter)}});
  if (stopped) {
    assert.equal(result.status, stoppedGolden.exit);
    const reply = JSON.parse(result.stdout);
    assert.deepEqual(reply, {...stoppedGolden.reply, lifecycle: {...stoppedGolden.reply.lifecycle,
      requested_at: stopped.requested_at, stopped_at: stopped.stopped_at}});
    return reply;
  }
  assert.equal(result.status, 0, `managed ${command} failed (${result.error?.name || result.status})`);
  return JSON.parse(result.stdout);
}
let browser, active = false;
try {
  active = true;
  const first = runtime("ensure");
  browser = await chromium.launch();
  const context = await browser.newContext({locale});
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const bearerRequests = [];
  page.on("request", request => {if (request.headers().authorization) bearerRequests.push(request.url());});
  const file = runtime("browser").handoff_file;
  await page.goto(pathToFileURL(file).href);
  await page.waitForURL(first.endpoint + "/dashboard/**");
  await page.locator("body.has-workspace").waitFor();
  assert.equal(new URL(page.url()).search, "");
  assert.equal(bearerRequests.length, 0);
  const storage = await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
  assert(!storage.includes(first.token) && !storage.includes("managed-browser-session"));
  const cookies = await context.cookies();
  assert(cookies.some(cookie => cookie.name.startsWith("batc_") && cookie.httpOnly && cookie.sameSite === "Strict"));
  await page.reload();
  await page.locator("body.has-workspace").waitFor();
  await page.goto(first.endpoint + "/dashboard/#/settings?section=about");
  const readClient = async () => {
    await page.locator('[data-export-diagnostics]').click();
    await page.locator('[data-diagnostics-json]').filter({hasText:'batc.diagnostics.v1'}).waitFor();
    const bundle = JSON.parse(await page.locator('[data-diagnostics-json]').textContent());
    const client = bundle.client;
    for(const [id,key] of [['loaded_ui','frontend_digest'],['served_ui','served_frontend_digest']]) {
      const row=bundle.versions.rows.find(row=>row.id===id);
      assert.equal(row.digest,client[key]);
      assert.equal(row.source,id==='loaded_ui'?'loaded_ui':'capabilities');
      assert(Number.isFinite(Date.parse(row.observed_at)));
      for(const surface of ['#settings-panel-about','[data-diagnostics-summary]'])
        assert((await page.locator(`${surface} [data-version-row=${id}]`).textContent()).includes(client[key]));
    }
    await page.getByRole('dialog').getByRole('button', {name:copy.diag_close,exact:true}).click();
    return client;
  };
  const capabilities = () => page.evaluate(async () => {
    const session = await (await fetch('/api/v1/browser-session')).json();
    const response = await fetch('/api/v1/capabilities', {headers:{'X-BATC-CSRF':session.csrf}});
    if(!response.ok)throw Error(`Capabilities read failed (${response.status})`);
    return response.json();
  });
  const initial = await capabilities();
  assert.match(initial.frontend?.digest || '', /^[a-f0-9]{64}$/);
  const loaded = await readClient();
  assert.equal(loaded.frontend_digest, initial.frontend.digest);
  assert.equal(loaded.served_frontend_digest, initial.frontend.digest);
  await appendFile(join(packageRoot,'dashboard/app.css'), '\n/* fixture UI upgrade */\n');
  const changed = await capabilities();
  assert.notEqual(changed.frontend.digest, initial.frontend.digest);
  await page.evaluate(() => {location.hash = '#/settings?section=connection';});
  await page.locator('#settings-panel-connection').waitFor();
  await page.evaluate(() => {location.hash = '#/settings?section=about';});
  await page.locator('[data-build-line]').filter({hasText:copy.diag_frontend_stale}).waitFor();
  const stale = await readClient();
  assert.equal(stale.frontend_digest, initial.frontend.digest);
  assert.equal(stale.served_frontend_digest, changed.frontend.digest);
  await page.reload();
  await page.locator('body.has-workspace').waitFor();
  const current = await readClient();
  assert.equal(current.frontend_digest, changed.frontend.digest);
  assert.equal(current.served_frontend_digest, changed.frontend.digest);
  assert(!(await page.locator('[data-build-line]').textContent()).includes(copy.diag_frontend_stale));
  await page.goto(first.endpoint + "/dashboard/#/settings");
  await page.locator("[data-setup-advanced] > summary").click();
  await page.getByRole("heading", {name: copy.ui_local_connector}).waitFor();
  await page.getByRole("button", {name: copy.disconnect, exact: true}).click();
  await page.waitForFunction(() => !document.body.classList.contains("has-workspace"));
  await page.reload();
  assert.equal(await page.locator("body.has-workspace").count(), 0);
  assert.deepEqual(errors, []);
  await context.close();
  const stopped = runtime("stop").lifecycle;
  runtime("ensure", {stopped});
  runtime("browser", {stopped});
  const second = runtime("resume");
  assert.equal(second.server_id, first.server_id);
  assert.equal(second.principal_id, first.principal_id);
  console.log(`Managed browser (${locale}): served/loaded identity, in-place upgrade, stale/reload, file POST, authenticated dashboard, sign-out and service restart passed`);
} finally {
  await browser?.close();
  if (active) runtime("stop");
  await rm(temporary, {recursive: true, force: true});
}
