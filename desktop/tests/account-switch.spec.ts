import {expectClientError} from './api-error-assertions.ts';
import {test, expect} from "@playwright/test";

for (const pause of ["digest", "lookup"]) {
  test(`account switch during ${pause} never submits the previous account's draft`, async ({page}) => {
    const writes: string[] = [];
    let lookupPending = false;
    let releaseLookup: (() => void) | undefined;
    await page.addInitScript(() => {
      sessionStorage.setItem("batc.dashboard.token", "first-fixture-token");
      const original = crypto.subtle.digest.bind(crypto.subtle);
      Object.defineProperty(crypto.subtle, "digest", {value: async (...args: Parameters<typeof original>) => {
        if ((window as any).__pauseDigest) {
          (window as any).__digestPending = true;
          await new Promise(resolve => { (window as any).__releaseDigest = resolve; });
        }
        return original(...args);
      }});
    });
    await page.route("**/api/v1/**", async route => {
      const req = route.request();
      const path = new URL(req.url()).pathname;
      const actor = req.headers().authorization?.includes("second-fixture") ? "second-actor" : "first-actor";
      const caps = {actor, scopes: ["observe", "operate"], features: {}, hosts: [], actions: []};
      if (req.method() === "POST") {
        writes.push(actor);
        return route.fulfill({json: {operation: {operation_id: "op_" + "c".repeat(32), status: "accepted", actor}}});
      }
      if (path.includes("/operations/op_")) {
        lookupPending = true;
        await new Promise<void>(resolve => { releaseLookup = resolve; });
        return route.fulfill({json: {operation: {status: "accepted", actor}}});
      }
      if (path.endsWith("/capabilities")) return route.fulfill({json: caps});
      if (path.endsWith("/bootstrap")) return route.fulfill({status: 404, json: {error: {code: "NOT_FOUND"}}});
      if (path.endsWith("/events")) return route.fulfill({json: {events: [], next_cursor: 0, head_cursor: 0, has_more: false}});
      return route.fulfill({json: path.endsWith("/sessions/demo/session-1")
        ? {session: {host: "demo", session_id: "session-1", title: "Fixture session", api_access: "managed", provenance: "connector"}}
        : {messages: [], checkpoints: [], sessions: [], hosts: [], operations: [], work_items: []}});
    });
    await page.goto("/dashboard/#/session/demo/session-1");
    await page.locator("textarea").first().fill("This belongs to the first actor");
    const send = page.getByRole("button", {name: "Send", exact: true});
    if (pause === "lookup") {
      await send.click();
      await page.locator(".session-send-record summary").click();
      await expect(page.getByText("op_" + "c".repeat(32), {exact: true})).toBeVisible();
    } else await page.evaluate(() => { (window as any).__pauseDigest = true; });
    await send.click();
    if (pause === "lookup") await expect.poll(() => lookupPending).toBe(true);
    else await expect.poll(() => page.evaluate(() => !!(window as any).__digestPending)).toBe(true);
    await page.locator("#live").click();
    await page.getByRole("button", {name: "Disconnect", exact: true}).click();
    await page.locator('input[type="password"]').fill("second-fixture-token");
    await page.getByRole("button", {name: "Connect", exact: true}).click();
    await expect(page.getByText("Nothing needs you right now.")).toBeVisible();
    if (pause === "lookup") releaseLookup!();
    else await page.evaluate(() => (window as any).__releaseDigest());
    await page.waitForTimeout(100);
    expect(writes).toEqual(pause === "lookup" ? ["first-actor"] : []);
    const keys = await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("batc.key.")));
    expect(keys.every(key => !key.includes("second-actor"))).toBe(true);
  });
}

test("native bootstrap mismatch returns to connection settings without reading domain data", async ({page}) => {
  await page.addInitScript(() => {
    const requests: string[] = [];
    Object.assign(window, {isTauri: true, __requests: requests, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === "native_status") return {endpoint: "https://central.example/", credential_available: true, error: null};
      if (command === "connector_connect") return {actor: "expected-actor", scopes: ["observe"]};
      if (command === "connector_request") {
        requests.push(args.input.path);
        return {status: 200, data: {sync: {version: 1, server_id: "server", principal_id: "principal", checkpoint: {cursor: 0, token: "proof"}},
          capabilities: {actor: "different-actor", scopes: ["observe"]}}};
      }
    }}});
  });
  await page.goto("/dashboard/");
  await expect(page.getByRole("heading", {name: "Desktop central connection"})).toBeVisible();
  await expectClientError(page, "Invalid central bootstrap identity");
  expect(await page.evaluate(() => (window as any).__requests)).toEqual(["/bootstrap"]);
});
