// Reproducible combined frontend + real central checkpoint contract test. Every host is MockBat.
// First run `uv sync --extra dev` at repo root and `npm run build:all` in desktop.
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { resolve } from "node:path";
import assert from "node:assert/strict";
import { chromium, expect } from "@playwright/test";
import { once } from "node:events";
import { setTimeout as delay } from "node:timers/promises";

const python = resolve(process.platform === "win32" ? "../.venv/Scripts/python.exe" : "../.venv/bin/python");
const child = spawn(python, [resolve("tests/central-fixture.py")], {cwd: resolve(".."),
  env: {...process.env, PYTHONPATH: resolve("..")}, stdio: ["pipe", "pipe", "inherit"]});
const lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
const next = async () => {
  const line = await lines.next();
  if (line.done) throw new Error("Central fixture stopped before replying");
  return JSON.parse(line.value);
};
const control = async action => { child.stdin.write(JSON.stringify({action}) + "\n"); return next(); };
const browser = await chromium.launch();
try {
  const fixture = await next();
  const page = await browser.newPage({locale: "en-US"});
  const errors = [];
  let bootstraps = 0;
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", response => { if (new URL(response.url()).pathname === "/api/v1/bootstrap") bootstraps++; });
  await page.addInitScript(token => sessionStorage.setItem("batc.dashboard.token", token), fixture.token);
  await page.goto(`http://127.0.0.1:${fixture.port}/dashboard/#/session/h1/sess-claude-0001`);
  const draft = page.locator("textarea").first();
  await expect(draft).toBeVisible();
  await draft.fill("Keep the actual central fixture draft");
  const readCheckpoint = () => page.evaluate(() => {
    const entry = Object.entries(localStorage).find(([key]) => key.startsWith("batc.sync."));
    return entry ? JSON.parse(entry[1]) : null;
  });
  const appended = await control("append");
  await expect.poll(async () => (await readCheckpoint())?.cursor).toBe(appended.cursor);
  assert.match((await readCheckpoint()).token, /^s1\./);
  await control("prune");
  await expect.poll(() => bootstraps).toBe(2);
  await expect(draft).toHaveValue("Keep the actual central fixture draft");
  await draft.press("Tab");
  await expect(draft).toHaveValue("Keep the actual central fixture draft");
  await expect(page.locator('#live')).toHaveAttribute('data-connection','connected');
  assert.equal((await readCheckpoint()).cursor, appended.cursor);
  assert.deepEqual(errors, []);
  console.log("Actual central + shared frontend: signed checkpoint replay, retention reset, and draft recovery passed");
} finally {
  await browser.close();
  child.stdin.end(JSON.stringify({action: "stop"}) + "\n");
  await Promise.race([once(child, "exit"), delay(3000, undefined, {ref: false}).then(() => child.kill())]);
}
