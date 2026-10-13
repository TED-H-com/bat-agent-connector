import {test, expect} from "@playwright/test";

for (const native of [false, true]) for (const refresh of ["messages", "preview"]) {
  test(`checkpoint waits for successful ${refresh} refresh (${native ? "native" : "browser"})`, async ({page}) => {
    const caps = {actor: "refresh-fixture", scopes: ["observe", "operate"], api_version: 1,
      contract_version: "2026-10-08", hosts: [], features: {checkpoints: refresh === "preview" ? ["demo"] : []}, actions: []};
    let changed = false, failing = true, messageReads = 0, listReads = 0;
    const writes: any[] = [], errors: string[] = [];
    let release!: () => void;
    const hold = new Promise<void>(resolve => { release = resolve; });
    const checkpoint = (cursor: number) => ({cursor, token: `fixture-proof-${cursor}`});
    const dispatch = async (input: any) => {
      const url = new URL(input.path, "http://fixture"), path = url.pathname;
      if (input.method === "POST") {
        writes.push(input);
        return {status: 200, data: {operation: {operation_id: "op_" + "d".repeat(32), status: "accepted"}}};
      }
      if (path === "/checkpoints") listReads++;
      if (path.endsWith(refresh === "preview" ? "/checkpoint-preview" : "/messages")) {
        messageReads++;
        if (changed) {
          if (messageReads === 2) await hold;
          if (failing) return {status: 503, data: {error: {code: "UNAVAILABLE", message: "View refresh unavailable"}}};
        }
        return {status: 200, data: refresh === "preview" ? {preview: {head: "a".repeat(40), commits: [
          {hash: "a".repeat(40), message: "Current"}, {hash: "b".repeat(40), message: "Selected"}], dirty: 0}}
          : {messages: [{role: "assistant", text: changed ? "Refreshed evidence" : "Original evidence"}]}};
      }
      const data = path === "/capabilities" ? caps : path === "/bootstrap" ? {capabilities: caps,
        sync: {version: 1, server_id: "refresh-server", principal_id: "refresh-principal", checkpoint: checkpoint(0)}}
        : path === "/events" ? {events: changed && url.searchParams.get("after") === "0"
          ? [{seq: 1, kind: refresh === "preview" ? "checkpoint.created" : "session.updated", resource_type: refresh === "preview" ? "checkpoint" : "session", resource_id: "demo/session-1"}] : [],
          head_cursor: changed ? 1 : 0, next_cursor: changed ? 1 : 0, has_more: false, sync: {checkpoint: checkpoint(changed ? 1 : 0)}}
        : path === "/sessions/demo/session-1" ? {session: {host: "demo", session_id: "session-1", title: "Refresh fixture",
          api_access: "managed", provenance: "connector"}} : {messages: [], checkpoints: [], sessions: [], operations: [], hosts: [], work_items: []};
      return {status: 200, data};
    };
    page.on("pageerror", error => errors.push(error.message));
    if (native) {
      await page.exposeFunction("fixtureConnector", dispatch);
      await page.addInitScript(caps => {
        Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
          if (command === "native_status") return {endpoint: "https://central.example/", credential_available: true};
          if (command === "connector_connect") return caps;
          if (command === "connector_disconnect") return null;
          if (command === "connector_request") return (window as any).fixtureConnector(args.input);
          throw new Error(`Unexpected command: ${command}`);
        }}});
      }, caps);
    } else await page.addInitScript(() => sessionStorage.setItem("batc.dashboard.token", "fixture-token"));
    await page.route("**/api/v1/**", async route => {
      if (native) throw new Error("Native client must use IPC");
      const request = route.request(), url = new URL(request.url());
      const result = await dispatch({method: request.method(), path: url.pathname.slice("/api/v1".length) + url.search,
        body: request.postDataJSON(), idempotency_key: request.headers()["idempotency-key"]});
      return route.fulfill({status: result.status, json: result.data});
    });
    await page.goto("/dashboard/#/session/demo/session-1");
    const saved = () => page.evaluate(() => JSON.parse(Object.entries(localStorage)
      .find(([key]) => key.startsWith("batc.sync."))?.[1] || "null"));
    await expect.poll(saved).toEqual(checkpoint(0));
    const draft = page.locator("textarea").first();
    await draft.fill("Keep this exact draft and original operation key");
    if (refresh === "preview") {
      await page.getByRole("combobox", {name: "Commit"}).selectOption("b".repeat(40));
      await page.locator("textarea").nth(1).fill("Keep this checkpoint note");
    }
    // Establish a real accepted intent before the read failure; its key must survive recovery.
    await page.getByRole("button", {name: "Send", exact: true}).click();
    await expect.poll(() => writes.length).toBe(1);
    const originalKey = writes[0].idempotency_key;
    expect(originalKey).toBeTruthy();
    changed = true;
    await expect.poll(() => messageReads).toBe(2);
    expect(await saved()).toEqual(checkpoint(0));
    release();
    await expect(page.getByText("UNAVAILABLE View refresh unavailable")).toBeVisible();
    expect(await saved()).toEqual(checkpoint(0));
    if (refresh === "preview") {
      expect(listReads).toBeGreaterThanOrEqual(2);
      await expect(page.getByRole("button", {name: "Record this version"})).toBeDisabled();
      await expect(page.getByRole("combobox", {name: "Commit"})).toHaveValue("b".repeat(40));
    }
    if (refresh === "messages") {
      await expect(page.getByRole("button", {name: "Send", exact: true})).toBeDisabled();
    } else {
      await page.getByRole("button", {name: "Send", exact: true}).click();
      await expect(page.getByText("CENTRAL_OFFLINE Central offline · actions paused")).toBeVisible();
    }
    expect(writes).toHaveLength(1);
    await expect(draft).toHaveValue("Keep this exact draft and original operation key");
    failing = false;
    await expect.poll(saved, {timeout: 10000}).toEqual(checkpoint(1));
    if (refresh === "messages") await expect(page.getByText("Refreshed evidence")).toBeVisible();
    await page.getByRole("button", {name: "Send", exact: true}).click();
    await expect.poll(() => writes.length).toBe(2);
    expect(writes[1].idempotency_key).toBe(originalKey);
    if (refresh === "preview") {
      await expect(page.getByRole("combobox", {name: "Commit"})).toHaveValue("b".repeat(40));
      await expect(page.locator("textarea").nth(1)).toHaveValue("Keep this checkpoint note");
      await page.getByRole("button", {name: "Record this version"}).click();
      await expect.poll(() => writes.length).toBe(3);
      expect(writes[2].body.params).toEqual({last_n: 20, commit: "b".repeat(40), note: "Keep this checkpoint note"});
    }
    expect(errors).toEqual([]);
  });
}

for (const native of [false, true]) {
  test(`open edit saves during deferred acknowledgment (${native ? "native" : "browser"})`, async ({page}) => {
    const caps = {actor: "edit-fixture", scopes: ["observe", "manage"], api_version: 1,
      contract_version: "2026-10-08", hosts: [], features: {}, actions: []};
    const item = {work_item_id: "wi_fixture", title: "Original title", version: 1, state: "todo",
      goal: "", request: "", acceptance: "", steps: [], completion: {display_state: "todo", fingerprint: "fixture"}};
    const checkpoint = (cursor: number) => ({cursor, token: `edit-proof-${cursor}`});
    let changed = false, delivered = false, reads = 0;
    const writes: any[] = [], errors: string[] = [];
    const dispatch = async (input: any) => {
      const url = new URL(input.path, "http://fixture"), path = url.pathname;
      if (input.method === "POST") {
        writes.push(input); Object.assign(item, input.body.params, {version: 2});
        return {status: 200, data: {operation: {operation_id: "op_" + "e".repeat(32), status: "succeeded"}}};
      }
      if (path === "/work-items/wi_fixture") {
        reads++;
        return {status: 200, data: {work_item: {...item}, project: {project_id: "p_fixture", name: "Fixture"},
          links: [], path: [], children: [], derived: [], events: []}};
      }
      if (path === "/events" && changed) delivered = true;
      return {status: 200, data: path === "/capabilities" ? caps : path === "/bootstrap" ? {capabilities: caps,
        sync: {version: 1, server_id: "edit-server", principal_id: "edit-principal", checkpoint: checkpoint(0)}}
        : path === "/events" ? {events: changed && url.searchParams.get("after") === "0"
          ? [{seq: 1, kind: "work_item.updated", resource_type: "work_item", resource_id: "wi_fixture"}] : [],
          head_cursor: changed ? 1 : 0, next_cursor: changed ? 1 : 0, has_more: false, sync: {checkpoint: checkpoint(changed ? 1 : 0)}}
        : {operations: [], sessions: [], hosts: [], work_items: []}};
    };
    page.on("pageerror", error => errors.push(error.message));
    if (native) {
      await page.exposeFunction("fixtureConnector", dispatch);
      await page.addInitScript(caps => {
        Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
          if (command === "native_status") return {endpoint: "https://central.example/", credential_available: true};
          if (command === "connector_connect") return caps;
          if (command === "connector_disconnect") return null;
          if (command === "connector_request") return (window as any).fixtureConnector(args.input);
          throw new Error(`Unexpected command: ${command}`);
        }}});
      }, caps);
    } else await page.addInitScript(() => sessionStorage.setItem("batc.dashboard.token", "fixture-token"));
    await page.route("**/api/v1/**", async route => {
      if (native) throw new Error("Native client must use IPC");
      const request = route.request(), url = new URL(request.url());
      const result = await dispatch({method: request.method(), path: url.pathname.slice("/api/v1".length) + url.search,
        body: request.postDataJSON(), idempotency_key: request.headers()["idempotency-key"]});
      return route.fulfill({status: result.status, json: result.data});
    });
    await page.goto("/dashboard/#/item/wi_fixture");
    await expect(page.getByRole("heading", {name: "Original title"})).toBeVisible();
    await page.getByRole("button", {name: "More", exact: true}).click();
    await page.locator("input[maxlength='120']").fill("Saved without closing the form");
    const saved = () => page.evaluate(() => JSON.parse(Object.entries(localStorage)
      .find(([key]) => key.startsWith("batc.sync."))?.[1] || "null"));
    await expect.poll(saved).toEqual(checkpoint(0));
    changed = true;
    await expect.poll(() => delivered).toBe(true);
    await expect(page.locator('#live')).toHaveAttribute('data-connection','reconnecting');await expect(page.locator('#live')).toHaveAttribute('title',/Waiting to refresh/);
    expect(await saved()).toEqual(checkpoint(0));
    const readsBefore = reads;
    await page.getByRole("button", {name: "Save", exact: true}).click();
    await expect.poll(() => writes.length).toBe(1);
    expect(writes[0].body).toEqual({action: "work_item.update", target: {work_item_id: "wi_fixture"},
      params: {title: "Saved without closing the form"}, preconditions: {expected_version: 1}});
    expect(writes[0].idempotency_key).toBeTruthy();
    await expect(page.getByRole("heading", {name: "Saved without closing the form"})).toBeVisible();
    await expect.poll(saved).toEqual(checkpoint(1));
    expect(reads).toBeGreaterThan(readsBefore);
    expect(errors).toEqual([]);
  });
}
