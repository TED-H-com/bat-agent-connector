import {test, expect, type Page} from "@playwright/test";
import {createHash} from "node:crypto";
const aid = "art_" + "a".repeat(32), uploadId = "op_" + "b".repeat(32), applyId = "op_" + "c".repeat(32);
const wid = "wi_" + "d".repeat(20), cp = "cp_" + "e".repeat(32);
const binary = Buffer.from([0, 255, 128, 13, 10, 60, 38, 34, 195, 169]);
const digest = createHash("sha256").update(binary).digest("hex");
const ref = {artifact_id: aid, revision: 1, digest};
const caps = {actor: "artifact-person", scopes: ["observe", "manage", "start", "operate"], api_version: 1,
  contract_version: "2026-10-08", features: {checkpoints: ["demo"]}, hosts: [], actions: [],
  artifacts: {limits: {max_file_bytes: 16 * 1024 * 1024}}};
const item = {work_item_id: wid, title: "Artifact fixture", version: 1, state: "todo", goal: "", request: "", acceptance: "", steps: [],
  attachments: [], completion: {display_state: "todo", fingerprint: "fixture-fingerprint"}};
async function fixture(page: Page, native: boolean) {
  const state = {actor: "artifact-person", uploaded: false, bytes: [] as number[], writes: [] as any[],
    loseApply: true, source: "1".repeat(40), sourceReads: 0, errors: [] as string[]};
  const dispatch = async (input: any) => {
    const url = new URL(input.path, "http://fixture"), path = url.pathname;
    if (input.method === "BINARY") {state.bytes = input.bytes; state.uploaded = true; return {status: 200, data: {}};}
    if (input.method === "POST") {
      state.writes.push(input);
      if (input.body.action === "artifact.upload") return {status: 200, data: {operation: {operation_id: uploadId,
        status: "waiting_external", external_refs: {content_url: "https://untrusted.invalid/do-not-follow"}}}};
      if (state.loseApply) {state.loseApply = false; return {status: 503, data: {error: {code: "LOST", message: "Lost apply reply"}}};}
      return {status: 200, data: {operation: {operation_id: applyId, status: "accepted"}}};
    }
    if (path === "/checkpoints/" + cp) {state.sourceReads++; return {status: 200, data: {checkpoint: {host: "demo"}, source: {head: state.source}}};}
    const identity = {...caps, actor: state.actor}, proof = {cursor: 0, token: "artifact-proof"};
    return {status: 200, data: path === "/capabilities" ? identity : path === "/bootstrap" ? {capabilities: identity,
      sync: {version: 1, server_id: "artifact-server", principal_id: state.actor, checkpoint: proof}}
      : path === "/events" ? {events: [], next_cursor: 0, head_cursor: 0, sync: {checkpoint: proof}}
      : path === "/work-items/" + wid ? {work_item: {...item}, project: {project_id: "prj_fixture", name: "Fixture"},
        links: [], path: [], children: [], derived: [], events: []}
      : path === "/artifacts" ? {artifacts: [{revision: {...ref, state: "ready", display_name: "Existing fixture"}}]}
      : path.startsWith("/artifacts/") ? {artifact: {...ref, state: "ready", display_name: "Existing fixture"}}
      : path === "/operations/" + uploadId ? {operation: {operation_id: uploadId,
        status: state.uploaded ? "succeeded" : "waiting_external", result: ref, external_refs: {content_url: "https://untrusted.invalid"}}}
      : path === "/operations/" + applyId ? {operation: {operation_id: applyId, status: "accepted"}}
      : path === "/sessions/demo/session-1" ? {session: {host: "demo", session_id: "session-1", title: "Artifact session", api_access: "managed", provenance: "connector_managed"}}
      : path.endsWith("/checkpoint-preview") ? {preview: {head: state.source, commits: [{hash: state.source, message: "Source"}], dirty: 0}}
      : path === "/checkpoints" ? {checkpoints: [{checkpoint_id: cp, commit_sha: "1".repeat(40), captured_at: 1, excerpt_messages: 0, dirty: 0, artifacts: [ref]}]}
      : {messages: [], sessions: [], operations: [], hosts: [], work_items: []}};
  };
  page.on("pageerror", error => state.errors.push(error.message));
  if (native) {
    await page.exposeFunction("artifactFixture", dispatch);
    await page.addInitScript(() => {
      Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any, options: any) => {
        if (command === "native_status") return {endpoint: "https://central.example/", credential_available: true};
        if (command === "connector_connect") return (await (window as any).artifactFixture({method: "GET", path: "/capabilities"})).data;
        if (command === "connector_disconnect") return null;
        if (command === "connector_request") return (window as any).artifactFixture(args.input);
        if (command === "connector_upload_artifact") return (window as any).artifactFixture({method: "BINARY",
          path: "/artifacts/uploads/" + options.headers["x-batc-upload-operation"] + "/content", bytes: [...new Uint8Array(args)]});
        throw new Error(`Unexpected command: ${command}`);
      }}});
    });
  } else await page.addInitScript(() => sessionStorage.setItem("batc.dashboard.token", "fixture-token"));
  await page.route("**/api/v1/**", async route => {
    if (native) throw new Error("Native artifact client cannot issue HTTP from JS");
    const request = route.request(), url = new URL(request.url()), binary = url.pathname.endsWith("/content");
    const out = await dispatch({method: binary ? "BINARY" : request.method(), path: url.pathname.slice("/api/v1".length) + url.search,
      body: binary ? null : request.postDataJSON(), bytes: binary ? [...request.postDataBuffer()!] : undefined,
      idempotency_key: request.headers()["idempotency-key"]});
    await route.fulfill({status: out.status, json: out.data});
  });
  await page.route("https://untrusted.invalid/**", () => {throw new Error("A server-supplied content URL was followed");});
  return state;
}

for (const native of [false, true]) {
  test(`artifact bytes and lost work-item reply (${native ? "native" : "browser"})`, async ({page}) => {
    const state = await fixture(page, native);
    await page.goto("/dashboard/#/item/" + wid);
    await page.getByRole("button", {name: "More", exact: true}).click();
    await page.locator('input[type="file"]').setInputFiles({name: "binary-fixture.dat", mimeType: "application/octet-stream", buffer: binary});
    await expect(page.locator(".attachment-list")).toContainText(aid);
    expect(state.bytes).toEqual([...binary]);
    expect(state.writes[0].body.params).toEqual({display_name: "binary-fixture.dat", media_type: "application/octet-stream", size_bytes: binary.length, expected_digest: digest});
    await page.getByRole("button", {name: "Save", exact: true}).click();
    await expect(page.getByText("LOST Lost apply reply")).toBeVisible();
    await page.reload();
    await page.getByRole("button", {name: "More", exact: true}).click();
    await expect(page.locator(".attachment-list")).toContainText(aid);
    await page.getByRole("button", {name: "Save", exact: true}).click();
    await expect.poll(() => state.writes.length).toBe(3);
    expect(state.writes[1].idempotency_key).toBe(state.writes[2].idempotency_key);
    expect(state.writes[1].body).toEqual(state.writes[2].body);
    expect(state.writes[2].body.params.attachments).toEqual([{...ref, role: "input"}]);
    expect(state.errors).toEqual([]);
  });

  test(`continuation retry keeps reviewed source after reopening (${native ? "native" : "browser"})`, async ({page}) => {
    const state = await fixture(page, native);
    await page.goto("/dashboard/#/session/demo/session-1");
    await page.getByRole("button", {name: "Start agent work from this version", exact: true}).click();
    await expect.poll(() => state.sourceReads).toBe(1);
    await page.getByPlaceholder("What should the agent do next…").fill("Use the original attached input");
    await page.getByRole("combobox", {name: "Agent"}).selectOption("codex");
    await page.getByRole("button", {name: "Start agent work", exact: true}).click();
    await expect(page.getByText("LOST Lost apply reply")).toBeVisible();
    state.source = "2".repeat(40);
    await page.reload();
    await page.getByRole("button", {name: "Start agent work from this version", exact: true}).click();
    await expect.poll(() => state.sourceReads).toBe(2);
    await expect(page.getByPlaceholder("What should the agent do next…")).toHaveValue("Use the original attached input");
    await expect(page.getByRole("combobox", {name: "Agent"})).toHaveValue("codex");
    await page.getByRole("button", {name: "Start agent work", exact: true}).click();
    await expect.poll(() => state.writes.length).toBe(2);
    expect(state.writes[0].idempotency_key).toBe(state.writes[1].idempotency_key);
    expect(state.writes[0].body).toEqual(state.writes[1].body);
    expect(state.writes[1].body.preconditions).toEqual({expected_source_head_sha: "1".repeat(40)});
    expect(state.writes[1].body.params.artifacts).toEqual([ref]);
    expect(state.errors).toEqual([]);
  });
}

for (const native of [false, true]) {
  test(`delayed file read cannot upload under a reconnected account (${native ? "native" : "browser"})`, async ({page}) => {
    const state = await fixture(page, native);
    await page.addInitScript(() => {
      const original = File.prototype.arrayBuffer;
      let release!: () => void;
      const hold = new Promise<void>(resolve => {release = resolve;});
      Object.assign(window, {releaseFixtureFile: release});
      File.prototype.arrayBuffer = async function() {
        const bytes = await original.call(this);
        (window as any).fixtureReadingFile = true;
        await hold; return bytes;
      };
    });
    await page.goto("/dashboard/#/item/" + wid);
    await page.getByRole("button", {name: "More", exact: true}).click();
    await page.locator('input[type="file"]').setInputFiles({name: "delayed.dat", mimeType: "application/octet-stream", buffer: binary});
    await expect.poll(() => page.evaluate(() => (window as any).fixtureReadingFile)).toBe(true);
    await page.locator("#live").click();
    await page.getByRole("button", {name: "Disconnect", exact: true}).click();
    state.actor = "second-artifact-person";
    if (!native) await page.locator('input[type="password"]').fill("second-fixture-token");
    await page.getByRole("button", {name: "Connect", exact: true}).click();
    await expect(page.getByText("Nothing needs you right now.")).toBeVisible();
    await page.evaluate(() => (window as any).releaseFixtureFile());
    await page.waitForTimeout(100);
    expect(state.writes).toEqual([]);
    expect(state.bytes).toEqual([]);
    await page.goto("/dashboard/#/item/" + wid);
    await page.getByRole("button", {name: "More", exact: true}).click();
    await expect(page.locator(".attachment-list")).toBeEmpty();
    expect(state.errors).toEqual([]);
  });
}

for (const locale of ["en-US", "zh-TW"]) for (const width of [390, 768, 1440]) {
  test(`attachment form ${locale} at ${width}px`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}});
    const page = await context.newPage();
    try {
      const state = await fixture(page, false);
      await page.goto("/dashboard/#/item/" + wid);
      await page.getByRole("button", {name: locale === "en-US" ? "More" : "更多", exact: true}).click();
      const existing = page.locator(".attachments select").first();
      await expect(existing.locator("option")).toHaveCount(2);
      await existing.selectOption(aid + ":1");
      await page.getByRole("button", {name: locale === "en-US" ? "Add attachment" : "加入附件", exact: true}).click();
      await expect(page.locator(".attachment-list")).toContainText(aid);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({path: `test-results/attachment-${locale}-${width}.png`, fullPage: true});
      expect(state.errors).toEqual([]);
    } finally {await context.close();}
  });
}
