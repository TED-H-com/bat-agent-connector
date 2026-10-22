import {httpEventDelivery} from './event-delivery.mjs';
// Shared generated UI against actual central bulk/child operations and MockBat only.
import {spawn} from "node:child_process";
import {createInterface} from "node:readline";
import {delimiter, resolve} from "node:path";
import {once} from "node:events";
import {setTimeout as delay} from "node:timers/promises";
import assert from "node:assert/strict";
import {chromium, expect} from "@playwright/test";
const backend = resolve("..");
const python = process.env.BATC_APPROVALS_PYTHON || resolve(backend, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const child = spawn(python, [resolve("tests/approvals-fixture.py")], {cwd: backend,
  env: {...process.env, PYTHONPATH: [resolve(backend, "src"), backend].join(delimiter)}, stdio: ["pipe", "pipe", "inherit"]});
const stopped = once(child, "exit"), lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
const next = async () => {const line = await lines.next(); if (line.done) throw new Error("Approval fixture stopped"); return JSON.parse(line.value);};
const browser = await chromium.launch();
try {
  const fixture = await next(), origin = `http://127.0.0.1:${fixture.port}`, posts = [], errors = [];
  const page = await browser.newPage({locale: "en-US"});
  const events = await httpEventDelivery(page);
  page.on("pageerror", error => errors.push(error.message));
  let loseReply = true;
  await page.route("**/api/v1/operations?wait=3", async route => {
    const request = route.request(); posts.push({body: request.postDataJSON(), key: request.headers()["idempotency-key"]});
    const response = await route.fetch();
    if (loseReply) {loseReply = false; await route.abort("failed");} else await route.fulfill({response});
  });
  await page.addInitScript(token => sessionStorage.setItem("batc.dashboard.token", token), fixture.token);
  await page.goto(origin+"/dashboard/#/approvals");
  const box = page.locator("[data-approvals]");
  await box.getByRole("combobox", {name: "Host", exact: true}).selectOption("h1");
  await events.post(box.getByRole("button", {name: "Preview pending requests"}));
  await expect(box.getByRole("checkbox", {name: "Approve "+fixture.manual, exact: true})).toBeDisabled();
  await box.getByRole("checkbox", {name: "Approve "+fixture.managed, exact: true}).check();
  await box.getByRole("combobox", {name: "Subsequent permission mode for "+fixture.managed}).selectOption("allow_all");
  await events.post(box.getByRole("button", {name: "Approve selected requests"}));
  await expect(box.locator(".error")).toBeVisible();
  await page.reload();
  await events.post(box.getByRole("button", {name: "Retry original request"}));
  await expect(box).toContainText("Each selected approval and mode change has an acceptance receipt", {timeout: 30000});
  const operationId = (await box.getByRole("link", {name: "View operation and step receipts"}).getAttribute("href")).split("/").at(-1);
  await page.reload();
  await expect(box).toContainText("Each selected approval and mode change has an acceptance receipt");
  assert.equal(posts.length, 2); assert.deepEqual(posts[1], posts[0]); assert.deepEqual(errors, []);
  child.stdin.write(JSON.stringify({action: "verify", operation_id: operationId})+"\n");
  assert.equal((await next()).verified, true);
  console.log("Real central batch: fixed reviewed selection, manual refusal, lost-reply replay, exact parent/child receipts and reload without duplicate frames passed");
} finally {
  await browser.close();
  if (child.exitCode === null && child.signalCode === null) child.stdin.end(JSON.stringify({action: "stop"})+"\n");
  await Promise.race([stopped, delay(3000, undefined, {ref: false}).then(() => child.kill())]);
}
