// Real central + file POST navigation, HttpOnly/CSRF and restart continuity.
import {testScratchRoot} from './test-scratch.mjs';
import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {mkdtemp, rm} from "node:fs/promises";
import {resolve, join} from "node:path";
import {pathToFileURL} from "node:url";
import {chromium} from "@playwright/test";
import {STRINGS} from '../src/i18n.js';
const locale = process.env.BATC_TEST_LOCALE || 'en-US';
const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
const temporary = await mkdtemp(join(testScratchRoot(), "managed-browser-"));
// Keep browser and child-process temporary files inside this owned directory.
process.env.TMPDIR = process.env.TEMP = process.env.TMP = temporary;
const directory = join(temporary, "installation");
const python = process.env.BATC_TEST_PYTHON || resolve(process.platform === "win32" ? "../.venv/Scripts/python.exe" : "../.venv/bin/python");
function runtime(command) {
  const result = spawnSync(python, ["-m", "bat_agent_connector.managed_runtime", command, "--data-dir", directory],
    {encoding: "utf8", timeout: 60000, cwd: resolve("..")});
  assert.equal(result.status, 0, `managed ${command} failed (${result.error?.name || result.status})`);
  return JSON.parse(result.stdout);
}
let browser, active = false;
try {
  const first = runtime("ensure"); active = true;
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
  await page.goto(first.endpoint + "/dashboard/#/settings");
  await page.locator("[data-setup-advanced] > summary").click();
  await page.getByRole("heading", {name: copy.managed_browser_connected}).waitFor();
  await page.getByRole("button", {name: copy.disconnect, exact: true}).click();
  await page.waitForFunction(() => !document.body.classList.contains("has-workspace"));
  await page.reload();
  assert.equal(await page.locator("body.has-workspace").count(), 0);
  assert.deepEqual(errors, []);
  await context.close();
  runtime("stop"); active = false;
  const second = runtime("ensure"); active = true;
  assert.equal(second.server_id, first.server_id);
  assert.equal(second.principal_id, first.principal_id);
  console.log(`Managed browser (${locale}): file POST, authenticated dashboard, reload, sign-out and service restart passed`);
} finally {
  await browser?.close();
  if (active) runtime("stop");
  await rm(temporary, {recursive: true, force: true});
}
