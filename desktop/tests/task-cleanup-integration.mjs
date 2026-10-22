import {httpEventDelivery} from './event-delivery.mjs';
// Task UI and recovery against real central cleanup; temporary Git and MockBat only.
import {spawn} from "node:child_process";
import {createInterface} from "node:readline";
import {readFile} from "node:fs/promises";
import {delimiter, resolve} from "node:path";
import {once} from "node:events";
import {setTimeout as delay} from "node:timers/promises";
import assert from "node:assert/strict";
import {chromium, expect} from "@playwright/test";

const backend = resolve(process.env.BATC_CLEANUP_ROOT || "..");
const python = process.env.BATC_CLEANUP_PYTHON || resolve("../.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const child = spawn(python, [resolve("tests/cleanup-fixture.py"), "--task"], {cwd: backend,
  env: {...process.env, PYTHONPATH: [resolve(backend, "src"), backend].join(delimiter)}, stdio: ["pipe", "pipe", "inherit"]});
const stopped = once(child, "exit"), lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
const next = async () => {const line = await lines.next(); if (line.done) throw new Error("Task cleanup fixture stopped"); return JSON.parse(line.value);};
const browser = await chromium.launch();
let page;
const responses = [];
try {
  const fixture = await next(), origin = `http://127.0.0.1:${fixture.port}`, writes = [], errors = [];
  page = await browser.newPage({locale: "en-US"});
  const events = await httpEventDelivery(page);
  page.on("pageerror", error => errors.push(error.message));
  let loseReply = true;
  await page.route("**/api/v1/operations?wait=3", async route => {
    const request = route.request(); writes.push({body: request.postDataJSON(), key: request.headers()["idempotency-key"]});
    const response = await route.fetch();
    const data = await response.json();
    // Synthetic diagnostics: outcome and binding checks, without tokens or request bodies.
    responses.push({status: response.status(), code: data.error?.code, operation: data.operation?.operation_id,
      state: data.operation?.status, actor: data.operation?.actor, action: data.operation?.action,
      key_matches: data.operation?.idempotency_key === writes.at(-1).key,
      target_matches: data.operation?.target?.preview_id === writes.at(-1).body.target.preview_id,
      token_matches: data.operation?.params?.preview_token === writes.at(-1).body.params.preview_token,
      fingerprint_matches: data.operation?.preconditions?.preview_fingerprint === writes.at(-1).body.preconditions.preview_fingerprint});
    if (loseReply) {loseReply = false; await route.abort("failed");} else await route.fulfill({response});
  });
  await page.route("**/dashboard/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const file = path === "/dashboard/" ? "index.html" : path.slice("/dashboard/".length);
    if (!["index.html", "app.js", "app.css", "i18n.js"].includes(file)) return route.abort();
    await route.fulfill({body: await readFile(resolve("../src/bat_agent_connector/dashboard", file)),
      contentType: file.endsWith("html") ? "text/html" : file.endsWith("css") ? "text/css" : "text/javascript"});
  });
  await page.addInitScript(token => sessionStorage.setItem("batc.dashboard.token", token), fixture.token);
  await page.goto(origin+"/dashboard/#/cleanup/task/"+fixture.task_id);
  await expect(page.getByRole("combobox", {name: "Choose a scope"})).toHaveValue("task");
  await events.post(page.getByRole("button", {name: "Preview cleanup", exact: true}));
  await expect(page.getByText("Plan:", {exact: false}).first()).toBeVisible();
  await page.getByRole("checkbox", {name: "I reviewed the resources", exact: false}).check();
  await events.post(page.getByRole("button", {name: "Apply reviewed cleanup"}));
  await expect(page.locator("p.error")).toBeVisible();
  await page.reload();
  await events.post(page.getByRole("button", {name: "Apply reviewed cleanup"}));
  const receipt = page.getByRole("link", {name: "View item receipts"});
  await expect(receipt).toBeVisible({timeout: 30000});
  const operationId = (await receipt.getAttribute("href")).split("/").at(-1);
  await expect(page.getByRole("button", {name: "Preview another cleanup"})).toBeVisible({timeout: 30000});
  await page.reload();
  await expect(page.getByRole("button", {name: "Preview another cleanup"})).toBeVisible();
  await expect(page.getByRole("button", {name: "Apply reviewed cleanup"})).toBeHidden();
  assert.equal(writes.length, 2); assert.deepEqual(writes[1], writes[0]); assert.deepEqual(errors, []);
  child.stdin.write(JSON.stringify({action: "verify_task", operation_id: operationId})+"\n");
  assert.equal((await next()).verified, true);
  await page.screenshot({path: "test-results/task-cleanup-real-central.png", fullPage: true});
  console.log("Real central task cleanup: fixed reviewed plan, lost-reply replay, single stop/removal, retained branch/history, read-only reload and unchanged human source passed");
} catch (error) {
  console.error("Task cleanup fixture outcomes:", JSON.stringify(responses));
  if (page && !page.isClosed()) {
    console.error("Task cleanup UI errors:", await page.locator("p.error").allTextContents());
    await page.screenshot({path: "test-results/task-cleanup-failure.png", fullPage: true});
  }
  throw error;
} finally {
  await browser.close();
  if (child.exitCode === null && child.signalCode === null) child.stdin.end(JSON.stringify({action: "stop"})+"\n");
  await Promise.race([stopped, delay(3000, undefined, {ref: false}).then(() => child.kill())]);
}
