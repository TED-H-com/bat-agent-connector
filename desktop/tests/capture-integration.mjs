// Generated shared UI, real central HTTP/readonly capture helper, temporary source and mock BAT.
import {spawn} from "node:child_process";
import {createInterface} from "node:readline";
import {mkdir} from "node:fs/promises";
import {resolve} from "node:path";
import {once} from "node:events";
import {setTimeout as delay} from "node:timers/promises";
import assert from "node:assert/strict";
import {STRINGS} from '../src/i18n.js';
import {httpEventDelivery} from './event-delivery.mjs';
import {chromium, expect} from "@playwright/test";
const backend = resolve("..");
const python = resolve("../.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const child = spawn(python, [resolve("tests/capture-fixture.py")], {cwd: backend,
  env: {...process.env, PYTHONPATH: `${backend}/src:${backend}`}, stdio: ["pipe", "pipe", "inherit"]});
const lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
const next = async () => {const result = await lines.next(); if (result.done) throw new Error("Capture fixture stopped"); return JSON.parse(result.value);};
const browser = await chromium.launch();
try {
  const fixture = await next(), origin = `http://127.0.0.1:${fixture.port}`;
  for (const locale of ['en', 'zh-TW']) {
    const page = await browser.newPage({locale: locale === 'en' ? 'en-US' : locale});
    const copy = STRINGS[locale], events = await httpEventDelivery(page), posts = [];
    page.on('request', request => {if (request.method() === 'POST' && new URL(request.url()).pathname.startsWith('/api/v1/')) posts.push(request);});
    await page.addInitScript(token => sessionStorage.setItem('batc.dashboard.token', token), fixture.token);
    const firstEventPage = page.waitForResponse(response => response.ok()
      && new URL(response.url()).pathname === '/api/v1/events');
    await page.goto(origin + '/dashboard/#/session/h1/' + fixture.session_id);
    const box = page.locator('[data-capture]');
    await box.locator('summary').click();
    await box.locator('input').nth(1).fill('notes.txt');
    const preview = box.getByRole('button', {name: copy.capture_preview, exact: true});
    // Observe a successful page before holding the next poll. That next request
    // proves the reader applied the page and settled its listeners, so pageshow
    // exercises a connected view rather than the initial connection check.
    await firstEventPage;
    await events.pause();
    const draft = await box.locator('input').evaluateAll(nodes => nodes.map(node => node.value));
    const stored = () => page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.capture.')));
    const before = await stored();
    assert.equal(before.length, 1);
    // Synthetic bfcache return forces the same shared write gate to stay
    // paused while the real post-return event read is held. No API body is replaced.
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true})));
    await preview.click();
    await expect(box.locator('[data-capture-notice]')).toContainText(copy.error_code_CLIENT_ACTION_VIEW_NOT_READY);
    await expect(box.locator('[data-capture-notice]')).toContainText('CLIENT_ACTION_VIEW_NOT_READY');
    assert.equal(posts.length, 0);
    assert.deepEqual(await box.locator('input').evaluateAll(nodes => nodes.map(node => node.value)), draft);
    const after = await stored();
    assert.equal(after[0][0], before[0][0]);
    assert.deepEqual(JSON.parse(after[0][1]).input, JSON.parse(before[0][1]).input);
    assert.equal(Boolean(JSON.parse(after[0][1]).intent), false);
    const refreshed = page.waitForResponse(response => response.ok()
      && new URL(response.url()).pathname === '/api/v1/events');
    events.resume();
    await refreshed;
    // One deliberate person retry after the refresh, not a click-until-success loop.
    await events.post(preview);
    await expect(box.locator('[data-capture-evidence]')).toContainText(fixture.digest);
    assert.equal(posts.length, 1);
    assert.deepEqual(posts[0].postDataJSON(), {host:'h1',session_id:fixture.session_id,relative_path:'notes.txt'});
    await page.close();
    console.log(JSON.stringify({locale,refusedPosts:0,explicitRetryPosts:1,draftPreserved:true}));
  }
  if (!process.argv.includes('--refresh-pause')) {
    const page = await browser.newPage({locale: "en-US"}), errors = [];
    const events = await httpEventDelivery(page);
    page.on("pageerror", error => errors.push(error.message));
    await page.addInitScript(token => sessionStorage.setItem("batc.dashboard.token", token), fixture.token);
    await page.goto(origin + "/dashboard/#/session/h1/" + fixture.session_id);
    // Session panels mount before the route's initial reads finish. The preview is
    // a POST. The later event fence proves the view settled before the click.
    await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected');
    let box = page.locator('[data-capture]');
    await box.locator("summary").click();
    await expect(box.locator("select")).toHaveValue("h1");
    await expect(box.locator("input").nth(0)).toHaveValue(fixture.session_id);
    await box.locator("input").nth(1).fill("notes.txt");
    await events.post(box.getByRole("button", {name: "Preview source file", exact: true}));
    await expect(box.locator("[data-capture-evidence]")).toContainText(fixture.digest);
    child.stdin.write(JSON.stringify({action: "verify"}) + "\n");
    assert.equal((await next()).source_unchanged, true);
    await box.getByRole("checkbox").check();
    await events.post(box.getByRole("button", {name: "Save reviewed file", exact: true}));
    await expect(box).toContainText("Saved a fixed attachment revision", {timeout: 30000});
    const operationId = (await box.locator('a[href^="#/op/"]').getAttribute("href")).split("/").at(-1);
    await page.reload();
    box = page.locator('[data-capture]'); await box.locator("summary").click();
    await expect(box).toContainText("Saved a fixed attachment revision", {timeout: 30000});
    child.stdin.write(JSON.stringify({action: "verify", operation_id: operationId}) + "\n");
    const verified = await next();
    assert.equal(verified.source_unchanged, true);
    assert.deepEqual(verified.bytes, [0,255,128,13,10,60,38,34,195,169]);
    assert.deepEqual(errors, []);
    await mkdir("test-results", {recursive: true});
    await page.screenshot({path: "test-results/capture-real-central.png", fullPage: true});
    console.log("Real central manual capture: reviewed binary bytes, accepted readback after reload, no source/index/refs changes, no BAT writes or inferred ownership passed");
  }
} finally {
  await browser.close();
  child.stdin.end(JSON.stringify({action: "stop"}) + "\n");
  await Promise.race([once(child, "exit"), delay(3000, undefined, {ref: false}).then(() => child.kill())]);
}
