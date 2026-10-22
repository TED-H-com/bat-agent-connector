import {httpEventDelivery} from './event-delivery.mjs';
// Shared frontend against real central immutable uploads and verified local materialization.
import {spawn} from "node:child_process";
import {createInterface} from "node:readline";
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {once} from "node:events";
import {setTimeout as delay} from "node:timers/promises";
import assert from "node:assert/strict";
import {chromium, expect} from "@playwright/test";
const backend = resolve(process.env.BATC_ARTIFACT_ROOT || "..");
const python = resolve("../.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const child = spawn(python, [resolve("tests/artifact-fixture.py")], {cwd: backend,
  env: {...process.env, PYTHONPATH: `${backend}/src:${backend}`}, stdio: ["pipe", "pipe", "inherit"]});
const lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
const next = async () => {const result = await lines.next(); if (result.done) throw new Error("Artifact fixture stopped"); return JSON.parse(result.value);};
const browser = await chromium.launch();
try {
  const fixture = await next(), origin = `http://127.0.0.1:${fixture.port}`;
  const page = await browser.newPage({locale: "en-US"}), errors = [];
  const events = await httpEventDelivery(page);
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(token => sessionStorage.setItem("batc.dashboard.token", token), fixture.token);
  await page.route("**/dashboard/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const file = path === "/dashboard/" ? "index.html" : path.slice("/dashboard/".length);
    if (!["index.html", "app.js", "app.css", "i18n.js"].includes(file)) return route.abort();
    await route.fulfill({body: await readFile(resolve("../src/bat_agent_connector/dashboard", file)),
      contentType: file.endsWith("html") ? "text/html" : file.endsWith("css") ? "text/css" : "text/javascript"});
  });
  await page.goto(origin + "/dashboard/#/session/h1/" + fixture.session_id);
  await page.getByRole("button", {name: "Start from here", exact: true}).click();
  const bytes = Buffer.from([0, 255, 128, 13, 10, 60, 38, 34, 195, 169]);
  await events.pause();
  await page.locator('input[type="file"]').setInputFiles({name: "fixture-input.dat", mimeType: "application/octet-stream", buffer: bytes});
  await expect(page.locator(".attachment-list")).toContainText("art_", {timeout: 30000});
  events.resume();
  await page.getByPlaceholder("What should the agent do next…").fill("Use the verified fixture attachment.");
  await events.post(page.getByRole("button", {name: "Start agent work", exact: true}));
  const receipt = page.locator(".session-checkpoint-result");
  await receipt.getByText("Operation details", {exact: true}).click();
  const link = receipt.locator('a[href^="#/op/"]');
  await expect(link).toBeVisible({timeout: 30000});
  const opId = (await link.getAttribute("href")).split("/").at(-1);
  let operation;
  await expect.poll(async () => {
    const response = await page.request.get(origin + "/api/v1/operations/" + opId, {headers: {Authorization: "Bearer " + fixture.token}});
    operation = (await response.json()).operation; return operation.status;
  }, {timeout: 60000}).toBe("succeeded");
  child.stdin.write(JSON.stringify({action: "verify", operation_id: opId}) + "\n");
  const verified = await next();
  assert.deepEqual(verified.contents, [[...bytes]]);
  assert.deepEqual(verified.states, ["verified"]);
  await link.click();
  await expect(page.getByRole("heading", {name: "Attachment transfer", exact: false})).toBeVisible();
  assert.deepEqual(errors, []);
  await page.screenshot({path: "test-results/artifact-real-central.png", fullPage: true});
  console.log("Real central artifacts: immutable raw upload, exact continuation envelope and verified bytes in temporary managed worktree passed");
} finally {
  await browser.close();
  child.stdin.end(JSON.stringify({action: "stop"}) + "\n");
  await Promise.race([once(child, "exit"), delay(3000, undefined, {ref: false}).then(() => child.kill())]);
}
