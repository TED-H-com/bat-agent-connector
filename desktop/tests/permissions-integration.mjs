import {httpEventDelivery} from './event-delivery.mjs';
// Shared generated UI against actual central operations. Every BAT frame goes to MockBat.
import {spawn} from "node:child_process";
import {createInterface} from "node:readline";
import {mkdir} from "node:fs/promises";
import {delimiter, resolve} from "node:path";
import {once} from "node:events";
import {setTimeout as delay} from "node:timers/promises";
import assert from "node:assert/strict";
import {chromium, expect} from "@playwright/test";
const backend = resolve(process.env.BATC_PERMISSIONS_ROOT || "..");
const python = process.env.BATC_PERMISSIONS_PYTHON || resolve(backend, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const child = spawn(python, [resolve("tests/permissions-fixture.py")], {cwd: backend,
  env: {...process.env, PYTHONPATH: [resolve(backend, "src"), backend].join(delimiter)}, stdio: ["pipe", "pipe", "inherit"]});
const stopped = once(child, "exit");
const lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
const next = async () => {const line = await lines.next(); if (line.done) throw new Error("Permission fixture stopped"); return JSON.parse(line.value);};
const control = async command => {child.stdin.write(JSON.stringify(command) + "\n"); return next();};
const browser = await chromium.launch();
try {
  const fixture = await next(), origin = `http://127.0.0.1:${fixture.port}`;
  const page = await browser.newPage({locale: "en-US"}), errors = [], posts = [];
  const events = await httpEventDelivery(page);
  page.on("pageerror", error => errors.push(error.message));
  // This portable UI commit can exercise another compatible backend checkout without modifying it.
  await page.route("**/dashboard/**", async route => {
    const name = new URL(route.request().url()).pathname.split("/").at(-1) || "index.html";
    if (!["index.html", "app.js", "app.css", "i18n.js"].includes(name)) return route.abort();
    await route.fulfill({path: resolve("../src/bat_agent_connector/dashboard", name),
      contentType: name.endsWith("html") ? "text/html" : name.endsWith("css") ? "text/css" : "text/javascript"});
  });
  let loseReply = false;
  await page.route("**/api/v1/operations?wait=3", async route => {
    const request = route.request();
    posts.push({body: request.postDataJSON(), key: request.headers()["idempotency-key"]});
    const response = await route.fetch();
    if (loseReply) {loseReply = false; await route.abort("failed");}
    else await route.fulfill({response});
  });
  await page.addInitScript(token => sessionStorage.setItem("batc.dashboard.token", token), fixture.token);
  const open = async sid => {
    await page.goto(origin + "/dashboard/#/session/h1/" + sid);
    const form = page.locator("[data-permissions]"); await form.locator("summary").click(); return form;
  };
  const id = async form => (await form.locator('a[href^="#/op/"]').getAttribute("href")).split("/").at(-1);
  let form = await open(fixture.claude);
  await expect(form.getByRole("combobox")).toHaveValue("default");
  await control({action: "policy-default"});
  await form.getByRole("combobox").selectOption("allow_all");
  await events.post(form.getByRole("button", {name: "Apply permissions", exact: true}));
  await expect(form).toContainText("PERMISSIONS_HOST_POLICY");
  await control({action: "verify-refused-admission"});
  await form.getByRole("button", {name: "Start another change"}).click();
  await expect(form.getByRole("combobox")).toHaveValue("default");
  await events.post(form.getByRole("button", {name: "Apply permissions", exact: true}));
  await expect(form).toContainText("Claude is streaming", {timeout: 30000});
  const refused = await id(form);
  await control({action: "verify", operation_id: refused, session_id: fixture.claude,
    status: "failed", error_code: "PERMISSIONS_STREAMING", frames: 0, operations: 1});
  await control({action: "idle"});
  await control({action: "policy-all"});
  await form.getByRole("button", {name: "Start another change"}).click();
  await form.getByRole("combobox").selectOption("allow_all");
  loseReply = true;
  await events.post(form.getByRole("button", {name: "Apply permissions", exact: true}));
  await expect(form.locator(".error")).toBeVisible();
  await expect(form.getByRole("combobox")).toBeDisabled();
  // Replay must find the already accepted key before evaluating changed admission policy.
  await control({action: "policy-default"});
  await page.reload(); form = page.locator("[data-permissions]"); await form.locator("summary").click();
  await expect(form.getByRole("combobox")).toHaveValue("allow_all");
  await events.post(form.getByRole("button", {name: "Retry original request"}));
  await expect(form).toContainText("BAT accepted the requested configuration", {timeout: 30000});
  const accepted = await id(form);
  assert.notEqual(accepted, refused);
  assert.notEqual(posts[1].key, posts[2].key);
  assert.deepEqual(posts[3], posts[2]);
  await page.reload(); await form.locator("summary").click();
  await expect(form).toContainText("BAT accepted the requested configuration");
  await control({action: "verify", operation_id: accepted, session_id: fixture.claude, status: "succeeded", frames: 1, operations: 2});
  assert.equal(posts.length, 4);

  await control({action: "codex-partial"});
  form = await open(fixture.codex);
  await events.post(form.getByRole("button", {name: "Apply permissions", exact: true}));
  await expect(form.locator(".status-uncertain")).toBeVisible({timeout: 30000});
  const partial = await id(form);
  await form.getByRole("link", {name: "View operation and step receipts"}).click();
  await expect(page.getByText("permissions.sandbox", {exact: true})).toBeVisible();
  await expect(page.getByText("permissions.approval", {exact: true})).toBeVisible();
  form = await open(fixture.codex); await page.reload(); await form.locator("summary").click();
  await expect(form.locator(".status-uncertain")).toBeVisible();
  await expect(form.getByRole("button", {name: "Start another change"})).toBeHidden();
  await form.getByRole("button", {name: "Check original operation"}).click();
  await control({action: "verify", operation_id: partial, session_id: fixture.codex, status: "uncertain", frames: 3, operations: 3});
  assert.equal(posts.length, 5);
  assert.deepEqual(errors, []);
  await mkdir("test-results", {recursive: true});
  await page.screenshot({path: "test-results/permissions-real-central.png", fullPage: true});
  console.log("Real central permissions: safe admission reset, zero-frame streaming refusal, explicit new key, lost-reply replay before changed policy, accepted reload and partial Codex receipts without resend passed");
} finally {
  await browser.close();
  if (child.exitCode === null && child.signalCode === null) child.stdin.end(JSON.stringify({action: "stop"}) + "\n");
  await Promise.race([stopped, delay(3000, undefined, {ref: false}).then(() => child.kill())]);
}
