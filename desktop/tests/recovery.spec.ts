import {test, expect} from "@playwright/test";

test("cursor reset preserves an active draft, then resumes with the new paired checkpoint", async ({page}) => {
  const caps = {actor: "fixture-operator", scopes: ["observe", "operate"], features: {}, hosts: [], actions: []};
  let reset = false, resetSent = false, bootstrapCount = 0;
  const eventRequests: URL[] = [];
  await page.addInitScript(() => sessionStorage.setItem("batc.dashboard.token", "fixture-token"));
  await page.route("**/api/v1/**", route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/capabilities")) return route.fulfill({json: caps});
    const checkpoint = bootstrapCount > 1 ? {cursor: 5, token: "new-proof"} : {cursor: 0, token: "original-proof"};
    if (url.pathname.endsWith("/bootstrap")) {
      bootstrapCount++;
      return route.fulfill({json: {capabilities: caps, sync: {version: 1, server_id: "fixture-server", principal_id: "fixture-principal",
        checkpoint: bootstrapCount > 1 ? {cursor: 5, token: "new-proof"} : checkpoint}}});
    }
    if (url.pathname.endsWith("/events")) {
      eventRequests.push(url);
      if (reset && !resetSent) { resetSent = true; return route.fulfill({status: 409, json: {error: {
        code: "EVENT_CURSOR_RESET", reason: "retention", resnapshot: true, preserve_drafts: true}}}); }
      return route.fulfill({json: {events: [], next_cursor: checkpoint.cursor, head_cursor: checkpoint.cursor,
        has_more: false, sync: {checkpoint}}});
    }
    return route.fulfill({json: url.pathname.endsWith("/sessions/demo/session-1")
      ? {session: {host: "demo", session_id: "session-1", title: "Fixture session", api_access: "managed", provenance: "connector"}}
      : {messages: [], checkpoints: []}});
  });
  await page.goto("/dashboard/#/session/demo/session-1");
  const draft = page.locator("textarea").first();
  await draft.fill("Preserve this exact draft");
  reset = true;
  await expect.poll(() => bootstrapCount).toBe(2);
  await expect(draft).toHaveValue("Preserve this exact draft");
  await page.getByRole("heading", {name: "Fixture session"}).click();
  await expect(draft).toHaveValue("Preserve this exact draft");
  await expect.poll(() => eventRequests.some(url => url.searchParams.get("after") === "5"
    && url.searchParams.get("checkpoint") === "new-proof")).toBe(true);
  const saved = await page.evaluate(() => Object.entries(localStorage).find(([key]) => key.startsWith("batc.sync."))?.[1]);
  expect(JSON.parse(saved!)).toEqual({cursor: 5, token: "new-proof"});
});

test("offline observation blocks operations and preserves the draft for reconnect", async ({page}) => {
  let online = false, writes = 0;
  const caps = {actor: "fixture-operator", scopes: ["observe", "operate"], features: {}, hosts: [], actions: []};
  await page.addInitScript(() => sessionStorage.setItem("batc.dashboard.token", "fixture-token"));
  await page.route("**/api/v1/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST") {
      writes++;
      return route.fulfill({json: {operation: {operation_id: "op_" + "b".repeat(32), status: "accepted"}}});
    }
    if (path.endsWith("/capabilities")) return route.fulfill({json: caps});
    if (path.endsWith("/bootstrap")) return route.fulfill({status: 404, json: {error: {code: "NOT_FOUND"}}});
    if (path.endsWith("/events")) return route.fulfill(online ? {json: {events: [], next_cursor: 0, head_cursor: 0, has_more: false}}
      : {status: 503, json: {error: {code: "UNAVAILABLE"}}});
    return route.fulfill({json: path.endsWith("/sessions/demo/session-1")
      ? {session: {host: "demo", session_id: "session-1", title: "Fixture session", api_access: "managed", provenance: "connector"}}
      : {messages: [], checkpoints: []}});
  });
  await page.goto("/dashboard/#/session/demo/session-1");
  const draft = page.locator("textarea").first();
  await draft.fill("Retry after the central returns");
  await expect(page.locator('#live')).toHaveAttribute('data-connection','reconnecting');await expect(page.locator('#live')).toHaveAttribute('title',/actions paused/);
  await expect(page.getByRole("button", {name: "Send", exact: true})).toBeDisabled();
  expect(writes).toBe(0);
  await expect(draft).toHaveValue("Retry after the central returns");
  online = true;
  await expect(page.locator('#live')).toHaveAttribute('data-connection','connected');
  await page.getByRole("button", {name: "Send", exact: true}).click();
  await expect.poll(() => writes).toBe(1);
});
