import {test, expect, type Page} from "@playwright/test";

const rid = "wt_" + "a".repeat(32), oid = "op_" + "b".repeat(32);
const cp = "cp_" + "c".repeat(32);
const caps = {actor: "cleanup-person", scopes: ["observe", "operate", "start", "cleanup", "cleanup_discard"], api_version: 1,
  contract_version: "2026-10-08", hosts: [{host: "demo", confinement: {host_account: {start_effect: "recheck"}}}],
  features: {cleanup: true, cleanup_task: true, checkpoints: ["demo"]}, actions: []};
const item = {resource_id: rid, host: "demo", kind: "worktree", path: "/managed/fixture", proven: true,
  task_owned: false, decision: "reclaim", reasons: [], observation: {head: "a".repeat(40)}, steps: ["preserve", "remove.worktree"]};
const preview = {preview_id: "clpv_" + "d".repeat(32), preview_token: "fixture-signed-preview", fingerprint: "fixture-fingerprint",
  target: {kind: "host", host: "demo"}, ready: true, expires_at: 2000000000, impact: {reclaim: 1, retain: 0}, items: [item]};
const checkpoint = (cursor: number) => ({cursor, token: "fixture-proof-" + cursor});

async function setup(page: Page, native: boolean, custom: (input: any, url: URL) => any) {
  const errors: string[] = [], reads: string[] = [];
  let cleanupOperation: any = null;
  const dispatch = async (input: any) => {
    const url = new URL(input.path, "http://fixture");
    reads.push(input.path);
    const extra = await custom(input, url);
    if (input.method === "POST" && input.body?.action === "cleanup.apply") {
      cleanupOperation ||= {...input.body, actor: caps.actor, idempotency_key: input.idempotency_key, operation_id: oid, status: 'accepted'};
      if (extra?.data?.operation) extra.data.operation = {...cleanupOperation, ...extra.data.operation};
    }
    if (extra) return extra;
    if (url.pathname === '/operations/'+oid && cleanupOperation) return {status: 200, data: {operation: cleanupOperation}};
    return {status: 200, data: url.pathname === "/capabilities" ? caps : url.pathname === "/bootstrap" ? {
      capabilities: caps, sync: {version: 1, server_id: "cleanup-server", principal_id: "cleanup-principal", checkpoint: checkpoint(0)}}
      : url.pathname === "/events" ? {events: [], next_cursor: 0, head_cursor: 0, sync: {checkpoint: checkpoint(0)}}
      : url.pathname.endsWith("/checkpoint-preview") ? {preview: {head: "c".repeat(40), commits: [{hash: "c".repeat(40), message: "Fixture"}], dirty: 0}}
      : url.pathname === "/cleanup-previews" ? {preview}
      : url.pathname === "/cleanup-tombstones" ? {tombstones: [], next_cursor: null}
      : url.pathname === "/cleanup-retained" ? {retained: [], unavailable: []}
      : url.pathname.startsWith("/operations/") ? {operation: {operation_id: oid, status: "accepted"}}
      : {sessions: [], work_items: [], operations: [], hosts: [], messages: [], checkpoints: []}};
  };
  page.on("pageerror", error => errors.push(error.message));
  if (native) {
    await page.exposeFunction("fixtureConnector", dispatch);
    await page.addInitScript(() => {
      Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
        if (command === "native_status") return {endpoint: "https://central.example/", credential_available: true};
        if (command === "connector_connect") return (await (window as any).fixtureConnector({method: "GET", path: "/capabilities"})).data;
        if (command === "connector_disconnect") return null;
        if (command === "connector_request") return (window as any).fixtureConnector(args.input);
        throw new Error(`Unexpected command: ${command}`);
      }}});
    });
  } else await page.addInitScript(() => sessionStorage.setItem("batc.dashboard.token", "fixture-token"));
  await page.route("**/api/v1/**", async route => {
    if (native) throw new Error("Native client must use IPC");
    const request = route.request(), url = new URL(request.url());
    const result = await dispatch({method: request.method(), path: url.pathname.slice("/api/v1".length) + url.search,
      body: request.postDataJSON(), idempotency_key: request.headers()["idempotency-key"]});
    return route.fulfill({status: result.status, json: result.data});
  });
  return {errors, reads};
}

for (const native of [false, true]) {
  test(`cleanup history distinguishes task lifecycle from reviewed authority (${native ? 'native' : 'browser'})`, async ({page}) => {
    await setup(page, native, (_, url) => url.pathname === '/cleanup-tombstones' ? {status: 200, data: {
      tombstones: ['reviewed_cleanup', 'task_lifecycle', 'historical_task_cleanup'].map((reason, index) => ({...item,
        resource_id: 'wt_' + String(index).repeat(32), actor: reason === 'historical_task_cleanup' ? null : 'fixture',
        reason, cleaned_at: 1})), next_cursor: null}} : null);
    await page.goto('/dashboard/#/cleanup');
    await expect(page.getByText('Removed by a reviewed cleanup operation.', {exact: true})).toHaveCount(1);
    await expect(page.getByText('Automatically cleaned by the task service', {exact: false})).toBeVisible();
    await expect(page.getByText('Historical record from an earlier task cleanup event', {exact: false})).toBeVisible();
  });
  test(`task cleanup keeps coordinator verdict and refuses dirty-task discard (${native ? 'native' : 'browser'})`, async ({page}) => {
    const tid = '11111111-2222-4333-8444-555555555555', previews: any[] = [];
    await setup(page, native, (input, url) => {
      if (url.pathname === '/cleanup-previews') {
        previews.push(input.body);
        return {status: 200, data: {preview: {...preview, target: input.body.target, items: [{...item, task_owned: true,
          task_cleanup: {eligible: true, task_ids: [tid]}, reasons: [{code: 'RESULTS_NOT_DELIVERED'}, {code: 'UNCOMMITTED_CHANGES'}]}]}}};
      }
    });
    await page.goto('/dashboard/#/cleanup/task/'+tid);
    await expect(page.getByRole('combobox', {name: 'Choose a scope'})).toHaveValue('task');
    await expect(page.getByRole('textbox', {name: 'Host name or original ID'})).toHaveValue(tid);
    await page.getByRole('button', {name: 'Preview cleanup', exact: true}).click();
    expect(previews[0].target).toEqual({kind: 'task', task_id: tid});
    await expect(page.locator('article.cleanup-resource').getByRole('checkbox', {name: /uncommitted|Discard/i})).toHaveCount(0);
    await expect(page.locator('article.cleanup-resource input[type=checkbox]')).toHaveCount(1);
    await page.locator('article.cleanup-resource summary').click();
    await expect(page.locator('article.cleanup-resource pre')).toContainText('task_cleanup');
  });
  test(`cleanup auth after lost reply retains its exact key and accepted reload only reads (${native ? 'native' : 'browser'})`, async ({page}) => {
    const writes: any[] = []; let outcome = 'lost';
    const {errors, reads} = await setup(page, native, (input, url) => {
      if (input.method === 'POST' && url.pathname === '/operations') {
        writes.push(input);
        return outcome === 'lost' ? {status: 503, data: {error: {code: 'LOST', message: 'Lost reply'}}}
          : outcome === 'auth' ? {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Scope unavailable'}}}
          : {status: 200, data: {operation: {operation_id: oid, status: 'accepted'}}};
      }
    });
    await page.goto('/dashboard/#/cleanup');
    await page.getByRole('textbox', {name: 'Host name or original ID'}).fill('demo');
    await page.getByRole('button', {name: 'Preview cleanup', exact: true}).click();
    await page.getByRole('checkbox', {name: 'I reviewed the resources', exact: false}).check();
    await page.getByRole('button', {name: 'Apply reviewed cleanup'}).click();
    await expect(page.getByText('LOST Lost reply')).toBeVisible();
    outcome = 'auth'; await page.reload();
    await page.getByRole('button', {name: 'Apply reviewed cleanup'}).click();
    await expect(page.locator('p.error')).toBeVisible();
    await expect(page.getByRole('button', {name: 'Preview cleanup', exact: true})).toBeDisabled();
    outcome = 'accepted'; await page.reload();
    await page.getByRole('button', {name: 'Apply reviewed cleanup'}).click();
    await expect(page.getByRole('link', {name: 'View item receipts'})).toHaveAttribute('href', '#/op/'+oid);
    expect(new Set(writes.map(w => w.idempotency_key)).size).toBe(1);
    expect(writes[2].body).toEqual(writes[0].body);
    await page.reload(); await expect.poll(() => reads.includes('/operations/'+oid)).toBe(true);
    await expect(page.getByRole('button', {name: 'Apply reviewed cleanup'})).toBeHidden();
    expect(writes).toHaveLength(3); expect(errors).toEqual([]);
  });
  test(`cleanup retains reviewed request after lost reply (${native ? "native" : "browser"})`, async ({page}) => {
    const writes: any[] = [];
    let delaySnapshot = false;
    let finishSnapshot!: () => void;
    const snapshot = new Promise<void>(resolve => {finishSnapshot = resolve;});
    const {errors} = await setup(page, native, async (input, url) => {
      if (delaySnapshot && url.pathname === "/cleanup-retained") await snapshot;
      if (input.method === "POST" && url.pathname === "/operations") {
        writes.push(input);
        return writes.length === 1 ? {status: 503, data: {error: {code: "REPLY_LOST", message: "Fixture lost reply"}}}
          : {status: 200, data: {operation: {operation_id: oid, status: "accepted"}}};
      }
    });
    await page.goto("/dashboard/#/cleanup");
    await page.getByRole("textbox", {name: "Host name or original ID"}).fill("demo");
    await page.getByRole("button", {name: "Preview cleanup", exact: true}).click();
    await expect(page.getByText("1 resources to reclaim · 0 retained")).toBeVisible();
    await page.getByRole("checkbox", {name: "I reviewed the resources", exact: false}).check();
    await page.getByRole("button", {name: "Apply reviewed cleanup"}).click();
    await expect(page.getByText("REPLY_LOST Fixture lost reply")).toBeVisible();
    delaySnapshot = true;
    await page.reload();
    await expect(page.getByText("1 resources to reclaim · 0 retained")).toBeVisible();
    await expect(page.getByRole("textbox", {name: "Host name or original ID"})).toBeDisabled();
    await expect(page.getByRole("button", {name: "Apply reviewed cleanup"})).toBeDisabled();
    expect(writes).toHaveLength(1);
    finishSnapshot();
    await page.getByRole("button", {name: "Apply reviewed cleanup"}).click();
    await expect(page.getByRole("link", {name: "View item receipts"})).toHaveAttribute("href", "#/op/" + oid);
    expect(writes).toHaveLength(2);
    expect(writes[0].idempotency_key).toBeTruthy();
    expect(writes[0].idempotency_key).toBe(writes[1].idempotency_key);
    expect(writes[0].body).toEqual(writes[1].body);
    expect(writes[1].body).toEqual({action: "cleanup.apply", target: {preview_id: preview.preview_id},
      params: {preview_token: preview.preview_token}, preconditions: {preview_fingerprint: preview.fingerprint}});
    expect(errors).toEqual([]);
  });

  test(`cleanup rejects a response for another key and preserves recovery after a fresh tab (${native ? 'native' : 'browser'})`, async ({page}) => {
    const writes: any[] = []; let bad = true;
    await setup(page, native, (input, url) => {
      if (input.method === 'POST' && url.pathname === '/operations') {
        writes.push(input);
        return {status: 200, data: {operation: {operation_id: oid, status: 'accepted', idempotency_key: bad ? 'different-key' : input.idempotency_key}}};
      }
    });
    await page.goto('/dashboard/#/cleanup');
    await page.getByRole('textbox', {name: 'Host name or original ID'}).fill('demo');
    await page.getByRole('button', {name: 'Preview cleanup', exact: true}).click();
    await page.getByRole('checkbox', {name: 'I reviewed the resources', exact: false}).check();
    await page.getByRole('button', {name: 'Apply reviewed cleanup'}).click();
    await expect(page.locator('.error')).toContainText('does not match');
    await expect(page.getByRole('link', {name: 'View item receipts'})).toHaveCount(0);
    await page.evaluate(() => {for (const key of Object.keys(sessionStorage).filter(k => k.startsWith('batc.cleanup.'))) sessionStorage.removeItem(key);});
    bad = false; await page.reload();
    await expect(page.getByRole('textbox', {name: 'Host name or original ID'})).toBeDisabled();
    await page.getByRole('button', {name: 'Apply reviewed cleanup'}).click();
    await expect(page.getByRole('link', {name: 'View item receipts'})).toBeVisible();
    expect(writes[1]).toEqual(writes[0]);
  });

  test(`expired cleanup admission permits only an explicit fresh preview (${native ? 'native' : 'browser'})`, async ({page}) => {
    await setup(page, native, (input, url) => input.method === 'POST' && url.pathname === '/operations'
      ? {status: 409, data: {error: {code: 'PREVIEW_EXPIRED', message: 'Review again'}}} : null);
    await page.goto('/dashboard/#/cleanup');
    await page.getByRole('textbox', {name: 'Host name or original ID'}).fill('demo');
    await page.getByRole('button', {name: 'Preview cleanup', exact: true}).click();
    await page.getByRole('checkbox', {name: 'I reviewed the resources', exact: false}).check();
    await page.getByRole('button', {name: 'Apply reviewed cleanup'}).click();
    await expect(page.getByRole('button', {name: 'Apply reviewed cleanup'})).toBeHidden();
    await page.reload(); await page.getByRole('button', {name: 'Preview another cleanup'}).click();
    await expect(page.getByRole('button', {name: 'Preview cleanup', exact: true})).toBeEnabled();
    await expect(page.getByRole('button', {name: 'Apply reviewed cleanup'})).toBeDisabled();
  });

  test(`cleanup event updates history without discarding reviewed preview (${native ? "native" : "browser"})`, async ({page}) => {
    let changed = false;
    let release!: () => void;
    const hold = new Promise<void>(resolve => {release = resolve;});
    const {errors, reads} = await setup(page, native, async (_, url) => {
      if (url.pathname === "/events") return {status: 200, data: {events: changed && url.searchParams.get("after") === "0"
        ? [{seq: 1, resource_type: "cleanup", resource_id: rid, kind: "cleanup.cleaned"}] : [],
        next_cursor: changed ? 1 : 0, head_cursor: changed ? 1 : 0, sync: {checkpoint: checkpoint(changed ? 1 : 0)}}};
      if (changed && url.pathname === "/cleanup-tombstones") {
        await hold;
        return {status: 200, data: {tombstones: [{...item, actor: "Fixture cleaner", cleaned_at: 1}], next_cursor: null}};
      }
    });
    await page.goto("/dashboard/#/cleanup");
    await page.getByRole("textbox", {name: "Host name or original ID"}).fill("demo");
    await page.getByRole("button", {name: "Preview cleanup", exact: true}).click();
    await page.getByRole("checkbox", {name: "I reviewed the resources", exact: false}).check();
    changed = true;
    await expect.poll(() => reads.filter(path => path.startsWith("/cleanup-tombstones")).length).toBe(2);
    const saved = () => page.evaluate(() => JSON.parse(Object.entries(localStorage).find(([k]) => k.startsWith("batc.sync."))![1]));
    expect(await saved()).toEqual(checkpoint(0));
    await expect(page.getByRole("button", {name: "Apply reviewed cleanup"})).toBeEnabled();
    release();
    await expect(page.getByText("Fixture cleaner", {exact: false})).toBeVisible();
    await expect.poll(saved).toEqual(checkpoint(1));
    await expect(page.getByText("1 resources to reclaim · 0 retained")).toBeVisible();
    expect(errors).toEqual([]);
  });

  test(`pending cleanup cannot migrate to another account (${native ? "native" : "browser"})`, async ({page}) => {
    let actor = "cleanup-person";
    const {errors} = await setup(page, native, (input, url) => {
      const identity = {...caps, actor};
      if (url.pathname === "/capabilities") return {status: 200, data: identity};
      if (url.pathname === "/bootstrap") return {status: 200, data: {capabilities: identity,
        sync: {version: 1, server_id: "cleanup-server", principal_id: actor, checkpoint: checkpoint(0)}}};
      if (input.method === "POST" && url.pathname === "/operations") return {status: 503, data: {error: {code: "LOST", message: "Lost reply"}}};
    });
    await page.goto("/dashboard/#/cleanup");
    await page.getByRole("textbox", {name: "Host name or original ID"}).fill("demo");
    await page.getByRole("button", {name: "Preview cleanup", exact: true}).click();
    await page.getByRole("checkbox", {name: "I reviewed the resources", exact: false}).check();
    await page.getByRole("button", {name: "Apply reviewed cleanup"}).click();
    await expect(page.getByText("LOST Lost reply")).toBeVisible();
    actor = "second-person";
    await page.reload();
    await expect(page.getByRole("textbox", {name: "Host name or original ID"})).toHaveValue("");
    await expect(page.getByRole("button", {name: "Apply reviewed cleanup"})).toBeDisabled();
    expect(await page.evaluate(() => Object.keys(sessionStorage).filter(k => k.startsWith("batc.cleanup.pending.")).length)).toBe(1);
    expect(errors).toEqual([]);
  });
}

for (const locale of ["en-US", "zh-TW"]) for (const width of [390, 768, 1440]) {
  test(`confinement and cleanup ${locale} at ${width}px`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}});
    const page = await context.newPage();
    try {
      const {errors} = await setup(page, false, (_, url) => {
        if (url.pathname === "/sessions/demo/session-1") return {status: 200, data: {session: {
          host: "demo", session_id: "session-1", title: "Confinement fixture", api_access: "managed", provenance: "connector_managed",
          confinement: {level: "os_sandbox", verification: {status: "unknown"}, gap: "sandbox_enforcement_unverified"},
          current_verification: {status: "mismatch"}}}};
        if (url.pathname === "/checkpoints") return {status: 200, data: {checkpoints: [{checkpoint_id: cp,
          commit_sha: "c".repeat(40), branch: "fixture", captured_at: 1, excerpt_messages: 0, dirty: 0}]}};
      });
      await page.goto("/dashboard/#/session/demo/session-1");
      await expect(page.locator("[data-session-view]")).toBeVisible();
      const opener = page.getByRole("button", {name: /^(Results|成果)$/, exact: true});
      if (await opener.isVisible()) await opener.click();
      await page.locator(".workspace-evidence > summary").click();
      await page.locator("button.secondary").filter({hasText: locale === "en-US" ? "Start from here" : "從這裡接續"}).click();
      const note = page.locator("[data-confinement-note]");
      await expect(note).toContainText(locale === "en-US" ? "acceptEdits" : "自動接受編輯");
      await page.getByRole("combobox", {name: locale === "en-US" ? "Agent" : "代理", exact: true}).selectOption("codex");
      await expect(note).toContainText(locale === "en-US" ? "workspace-write" : "工作區寫入");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({path: `test-results/confinement-${locale}-${width}.png`, fullPage: true});
      await page.goto("/dashboard/#/cleanup");
      await page.locator("input.cleanup-id").first().fill("demo");
      await page.locator("button.secondary").first().click();
      await expect(page.locator("article.cleanup-resource")).toHaveCount(1);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({path: `test-results/cleanup-${locale}-${width}.png`, fullPage: true});
      expect(errors).toEqual([]);
    } finally {await context.close();}
  });
}

for (const native of [false, true]) {
  test(`repair agent choice survives an operation event (${native ? "native" : "browser"})`, async ({page}) => {
    let changed = false;
    const writes: any[] = [];
    const {errors} = await setup(page, native, (input, url) => {
      if (input.method === "POST") {
        writes.push(input);
        return {status: 200, data: {operation: {operation_id: "op_" + "e".repeat(32), status: "accepted"}}};
      }
      if (url.pathname === "/operations/" + oid) return {status: 200, data: {operation: {operation_id: oid,
        action: "integration.apply", status: "needs_attention", error_code: "INTEGRATION_CONFLICT", target: {host: "demo"}, steps: []}}};
      if (url.pathname === "/integrations/" + oid) return {status: 200, data: {receipts: []}};
      if (url.pathname === "/events") return {status: 200, data: {events: changed && url.searchParams.get("after") === "0"
        ? [{seq: 1, resource_type: "operation", resource_id: oid, kind: "operation.updated"}] : [],
        next_cursor: changed ? 1 : 0, head_cursor: changed ? 1 : 0, sync: {checkpoint: checkpoint(changed ? 1 : 0)}}};
    });
    await page.goto("/dashboard/#/op/" + oid);
    await page.getByRole("button", {name: "Ask an agent to resolve", exact: true}).click();
    await page.getByRole("combobox", {name: "Agent"}).selectOption("codex");
    changed = true;
    await expect(page.locator('#live')).toHaveAttribute('data-connection','connected');await expect(page.locator('#live')).toHaveAttribute('title',/Waiting to refresh/);
    await expect(page.getByRole("combobox", {name: "Agent"})).toHaveValue("codex");
    await page.getByRole("button", {name: "Start agent work", exact: true}).click();
    await expect.poll(() => writes.length).toBe(1);
    expect(writes[0].body).toEqual({action: "integration.handoff", target: {operation_id: oid}, params: {agent: "codex"}, preconditions: {}});
    expect(errors).toEqual([]);
  });
}
