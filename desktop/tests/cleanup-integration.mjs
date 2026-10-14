// Run the canonical generated UI against an independently integrated cleanup backend.
import {spawn} from "node:child_process";
import {createInterface} from "node:readline";
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {once} from "node:events";
import {setTimeout as delay} from "node:timers/promises";
import assert from "node:assert/strict";
import {chromium, expect} from "@playwright/test";

const backend = resolve(process.env.BATC_CLEANUP_ROOT || "..");
const python = resolve("../.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const child = spawn(python, [resolve("tests/cleanup-fixture.py")], {cwd: backend,
  env: {...process.env, PYTHONPATH: `${backend}/src:${backend}`}, stdio: ["pipe", "pipe", "inherit"]});
const lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
const browser = await chromium.launch();
try {
  const first = await lines.next();
  if (first.done) throw new Error("Cleanup fixture stopped before startup");
  const fixture = JSON.parse(first.value), origin = `http://127.0.0.1:${fixture.port}`;
  const page = await browser.newPage({locale: "en-US"});
  const errors = [], writes = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("request", req => {
    if (req.method() === "POST" && req.url().includes("/api/v1/operations")) writes.push(req.postDataJSON());
  });
  await page.addInitScript(token => sessionStorage.setItem("batc.dashboard.token", token), fixture.token);
  // The external backend has legacy static files; serve only this branch's generated assets.
  await page.route("**/dashboard/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const file = path === "/dashboard/" ? "index.html" : path.slice("/dashboard/".length);
    if (!["index.html", "app.js", "app.css", "i18n.js"].includes(file)) return route.abort();
    await route.fulfill({body: await readFile(resolve("../src/bat_agent_connector/dashboard", file)),
      contentType: file.endsWith("html") ? "text/html" : file.endsWith("css") ? "text/css" : "text/javascript"});
  });
  await page.goto(origin + "/dashboard/#/cleanup");
  await page.getByRole("combobox", {name: "Choose a scope"}).selectOption("checkpoint");
  await page.getByRole("textbox", {name: "Host name or original ID"}).fill(fixture.checkpoint_id);
  await page.getByRole("button", {name: "Preview cleanup", exact: true}).click();
  await expect(page.getByText("Plan:", {exact: false}).first()).toBeVisible();
  await page.getByRole("checkbox", {name: "I reviewed the resources", exact: false}).check();
  await expect(page.getByRole("button", {name: "Apply reviewed cleanup"})).toBeEnabled();
  await page.getByRole("button", {name: "Apply reviewed cleanup"}).click();
  const receipt = page.getByRole("link", {name: "View item receipts"});
  await expect(receipt).toBeVisible({timeout: 30000});
  const href = await receipt.getAttribute("href");
  await receipt.click();
  await expect(page.getByRole("heading", {name: "Apply cleanup", exact: true})).toBeVisible();
  await expect(page.locator("dt").filter({hasText: /^Action ID$/}).locator("+ dd code")).toHaveText("cleanup.apply");
  await expect(page.getByRole("heading", {name: "View item receipts", exact: true})).toBeVisible({timeout: 30000});
  // Item receipts land before the parent operation is terminal. Keep reading that same GET.
  const operationUrl = origin + "/api/v1/operations/" + href.split("/").at(-1);
  let op;
  await expect.poll(async () => {
    const response = await page.request.get(operationUrl, {headers: {Authorization: "Bearer " + fixture.token}});
    if (!response.ok()) return "unavailable";
    op = await response.json();
    return op.operation?.status ?? "missing";
  }, {timeout: 30000, intervals: [250]}).toMatch(/^(succeeded|failed|cancelled)$/);
  assert.equal(op.operation.status, "succeeded", JSON.stringify(op.operation));
  assert.ok(op.cleanup_receipts.some(r => ["succeeded", "already_absent"].includes(r.status)));
  assert.equal(writes.length, 1);
  assert.equal(writes[0].action, "cleanup.apply");
  // The same receipt has a localized title; its technical action ID stays exact.
  await page.addInitScript(() => Object.defineProperty(navigator, "language", {value: "zh-TW"}));
  await page.reload();
  await expect(page.getByRole("heading", {name: "套用清理", exact: true})).toBeVisible();
  await expect(page.locator("dt").filter({hasText: /^動作 ID$/}).locator("+ dd code")).toHaveText("cleanup.apply");
  assert.equal(writes.length, 1);
  assert.deepEqual(errors, []);
  await page.screenshot({path: "test-results/cleanup-real-central.png", fullPage: true});
  console.log("Real central cleanup: signed preview, reviewed operation, Git reclamation and per-item receipts passed");
} finally {
  await browser.close();
  child.stdin.end(JSON.stringify({action: "stop"}) + "\n");
  await Promise.race([once(child, "exit"), delay(3000, undefined, {ref: false}).then(() => child.kill())]);
}
