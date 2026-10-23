import {managedStatusFixture} from './managed-status-fixture.ts';
import {test, expect, type Page} from "@playwright/test";

const cp = (cursor: number) => ({cursor, token: `observation-proof-${cursor}`});
const caps = {actor: "observer", scopes: ["observe", "operate", "manage"], api_version: 1,
  contract_version: "2026-10-08", hosts: [], features: {}, actions: []};
async function mount(page: Page, native: boolean, dispatch: (request: any) => Promise<any>) {
  if (native) {
    await page.exposeFunction("fixtureConnector", dispatch);
    await page.addInitScript(({caps, managed}) => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === "managed_control" && args.input.action === "status") return managed;
      if (command === "native_status") return {endpoint: "https://central.example/", credential_available: true};
      if (command === "connector_connect") return caps;
      if (command === "connector_disconnect") return null;
      if (command === "connector_request") return (window as any).fixtureConnector(args.input);
      throw new Error(`Unexpected native command ${command}`);
    }}}), {caps, managed: managedStatusFixture(false)});
  } else await page.addInitScript(() => sessionStorage.setItem("batc.dashboard.token", "fixture-token"));
  await page.route("**/api/v1/**", async route => {
    if (native) throw new Error("Native requests must use IPC");
    const request = route.request(), url = new URL(request.url());
    const result = await dispatch({method: request.method(), path: url.pathname.slice(7) + url.search,
      body: request.postDataJSON(), idempotency_key: request.headers()["idempotency-key"]});
    return route.fulfill({status: result.status, json: result.data});
  });
}
const saved = (page: Page) => page.evaluate(() => JSON.parse(Object.entries(localStorage).find(([key]) => key.startsWith("batc.sync."))?.[1] || "null"));
const ask = (id: string) => ({kind: "ask_user", toolUseId: id, questions: [{question: "Which branch?", options: ["main", "feature"]}]});

for (const native of [false, true]) {
  test(`mounted pending refresh preserves drafts and refuses stale answers (${native ? "native" : "browser"})`, async ({page}) => {
    let cursor = 0, reads = 0, failRead = false;
    let pending: any = null;
    const writes: any[] = [];
    const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
    await mount(page, native, async input => {
      const url = new URL(input.path, "http://fixture"), path = url.pathname;
      if (input.method === "POST") { writes.push(input); return {status: 200, data: {operation: {operation_id: "op_" + "f".repeat(32), status: "accepted"}}}; }
      if (path === "/sessions/demo/session-1") {
        reads++;
        if (failRead) return {status: 503, data: {error: {code: "UNAVAILABLE", message: "Session observation unavailable"}}};
        return {status: 200, data: {session: {host: "demo", session_id: "session-1", title: "Observed session",
          api_access: "managed", pending, provenance: "connector_managed"}}};
      }
      return {status: 200, data: path === "/capabilities" ? caps : path === "/bootstrap" ? {capabilities: caps,
        sync: {version: 1, server_id: "server", principal_id: "principal", checkpoint: cp(0)}}
        : path === "/events" ? {events: Number(url.searchParams.get("after")) < cursor
          ? [{seq: cursor, resource_type: "session", resource_id: "demo/session-1", kind: "session.updated"}] : [],
          head_cursor: cursor, next_cursor: cursor, has_more: false, sync: {checkpoint: cp(cursor)}}
        : {messages: [], checkpoints: [], operations: [], hosts: [], sessions: [], work_items: []}};
    });
    await page.goto("/dashboard/#/session/demo/session-1");
    const composer = page.locator("textarea").first();
    await composer.fill("Preserved session instructions");
    pending = ask("question-1"); cursor = 1;
    await expect(page.getByRole("textbox", {name: "Which branch?"})).toBeVisible();
    await expect.poll(() => saved(page)).toEqual(cp(1));
    const answer = page.getByRole("textbox", {name: "Which branch?"});
    await answer.fill("Original answer draft");
    cursor = 2;
    await expect.poll(() => saved(page)).toEqual(cp(2));
    await expect(answer).toHaveValue("Original answer draft");
    await expect(composer).toHaveValue("Preserved session instructions");
    // The pending ID changes before its event reaches this view. Preflight must refuse the old answer.
    pending = {kind: "permission", toolUseId: "permission-2", toolName: "git", input_preview: "Publish branch"};
    await page.getByRole("button", {name: "Answer", exact: true}).click();
    await expect(page.locator('[data-session-error=PENDING_CHANGED] .session-error-line')).toContainText('The question changed');
    await expect(page.locator('[data-session-error=PENDING_CHANGED] details')).toContainText('PENDING_CHANGED');
    expect(writes).toHaveLength(0);
    await expect(page.getByRole("button", {name: "Allow", exact: true})).toBeVisible();
    await page.getByRole("button", {name: "Allow", exact: true}).click();
    await expect.poll(() => writes.length).toBe(1);
    expect(writes[0].body.params).toEqual({permission: "allow", tool_use_id: "permission-2"});
    pending = ask("question-1"); cursor = 3;
    await expect.poll(() => saved(page)).toEqual(cp(3));
    await expect(answer).toHaveValue("Original answer draft");
    failRead = true; cursor = 4;
    await expect(page.locator('[data-session-error=UNAVAILABLE] .session-error-line')).toContainText('Server error');
    await expect(page.locator('[data-session-error=UNAVAILABLE] details')).toContainText('Session observation unavailable');
    expect(await saved(page)).toEqual(cp(3));
    await expect(page.getByRole("button", {name: "Send", exact: true})).toBeDisabled();
    expect(writes).toHaveLength(1);
    failRead = false;
    await expect.poll(() => saved(page), {timeout: 10000}).toEqual(cp(4));
    await expect(answer).toHaveValue("Original answer draft");
    await expect(composer).toHaveValue("Preserved session instructions");
    expect(reads).toBeGreaterThan(5); expect(errors).toEqual([]);
  });

  test(`initial message failure retains subscription and pauses controls until recovery (${native ? "native" : "browser"})`, async ({page}) => {
    let cursor = 0, failMessages = true, reads = 0, pending: any = ask("question-1");
    const writes: any[] = [], errors: string[] = [];
    page.on("pageerror", e => errors.push(e.message));
    await mount(page, native, async input => {
      const url = new URL(input.path, "http://fixture"), path = url.pathname;
      if (input.method === "POST") {writes.push(input); return {status: 200, data: {operation: {status: "accepted"}}};}
      if (path === "/sessions/demo/session-1") {
        reads++;
        return {status: 200, data: {session: {host: "demo", session_id: "session-1", title: "Initially unavailable",
          api_access: "managed", provenance: "connector_managed", pending}}};
      }
      if (path.endsWith("/messages") && failMessages)
        return {status: 503, data: {error: {code: "UNAVAILABLE", message: "Messages temporarily unavailable"}}};
      return {status: 200, data: path === "/capabilities" ? caps : path === "/bootstrap" ? {capabilities: caps,
        sync: {version: 1, server_id: "server", principal_id: "principal", checkpoint: cp(0)}}
        : path === "/events" ? {events: Number(url.searchParams.get("after")) < cursor
          ? [{seq: cursor, resource_type: "session", resource_id: "demo/session-1", kind: "session.updated"}] : [],
          head_cursor: cursor, next_cursor: cursor, has_more: false, sync: {checkpoint: cp(cursor)}}
        : {messages: [], checkpoints: [], operations: [], hosts: [], sessions: [], work_items: []}};
    });
    await page.goto("/dashboard/#/session/demo/session-1");
    const error = page.locator('[data-session-error=UNAVAILABLE]');
    await expect(error.locator('.session-error-line')).toContainText('Messages unavailable');
    await expect(error.locator('details')).toContainText('Messages temporarily unavailable');
    const composer = page.locator("textarea").first(), answer = page.getByRole("textbox", {name: "Which branch?"});
    await composer.fill("Initial read failure must retain these instructions");
    await answer.fill("Retained answer");
    await expect(page.getByRole("button", {name: "Send", exact: true})).toBeDisabled();
    await expect(page.getByRole("button", {name: "Answer", exact: true})).toBeDisabled();
    // With no journal event, the initial failed read itself must still recover.
    failMessages = false;
    await expect(page.getByRole("button", {name: "Answer", exact: true})).toBeEnabled();
    await expect(error).toHaveCount(0);
    await expect(answer).toHaveValue("Retained answer");
    pending = {kind: "permission", toolUseId: "permission-2", toolName: "git", input_preview: "Reviewed action"};
    cursor = 1;
    await expect.poll(() => saved(page)).toEqual(cp(1));
    await expect(page.getByRole("button", {name: "Allow", exact: true})).toBeVisible();
    await expect(composer).toHaveValue("Initial read failure must retain these instructions");
    const beforeLeaving = reads;
    await page.goto("/dashboard/#/settings");
    await page.waitForTimeout(1400);
    expect(reads).toBeLessThanOrEqual(beforeLeaving + 1);
    expect(writes).toEqual([]); expect(errors).toEqual([]);
  });

  test(`parent archive updates mounted controls while retaining the edit (${native ? "native" : "browser"})`, async ({page}) => {
    let archived = false, cursor = 0, linkedStatus = "running";
    const writes: any[] = [];
    await mount(page, native, async input => {
      const url = new URL(input.path, "http://fixture"), path = url.pathname;
      if (input.method === "POST") {writes.push(input); return {status: 200, data: {operation: {status: "succeeded"}}};}
      return {status: 200, data: path === "/capabilities" ? caps : path === "/bootstrap" ? {capabilities: caps,
        sync: {version: 1, server_id: "server", principal_id: "principal", checkpoint: cp(0)}}
        : path === "/events" ? {events: Number(url.searchParams.get("after")) < cursor ? [{seq: cursor,
          resource_type: cursor === 1 ? "operation" : "project", resource_id: cursor === 1 ? "op_link" : "p_fixture", kind: "updated"}] : [],
          head_cursor: cursor, next_cursor: cursor, has_more: false, sync: {checkpoint: cp(cursor)}}
        : path === "/work-items/wi_fixture" ? {work_item: {work_item_id: "wi_fixture", title: "Linked work", version: 1,
          state: "todo", goal: "", request: "", acceptance: "", steps: [], completion: {display_state: "todo", fingerprint: "fp"}},
          project: {project_id: "p_fixture", name: "Parent", archived}, links: [{kind: "operation", ref: "op_link",
            target: {found: true, action: "fixture.operation", status: linkedStatus}}], children: [], derived: [], path: [], events: []}
        : {operations: [], sessions: [], hosts: [], work_items: []}};
    });
    await page.goto("/dashboard/#/item/wi_fixture");
    await expect(page.getByRole("heading", {name: "Linked work"})).toBeVisible();
    linkedStatus = "succeeded"; cursor = 1;
    await expect(page.locator(".status-succeeded")).toBeVisible();
    await expect.poll(() => saved(page)).toEqual(cp(1));
    await page.getByRole("button", {name: "More", exact: true}).click();
    await page.locator("input[maxlength='120']").fill("Retained project archive draft");
    cursor = 2;
    await expect(page.locator('#live')).toHaveAttribute('data-connection','connected');await expect(page.locator('#live')).toHaveAttribute('title',/Waiting to refresh/);
    // The first refresh is held by this editor. A later archive must still update safety controls.
    archived = true; cursor = 3;
    await expect(page.getByText("This item or its project is archived. Your edit draft is retained.")).toBeVisible();
    await expect(page.getByRole("button", {name: "Save", exact: true})).toBeDisabled();
    await expect(page.locator("input[maxlength='120']")).toHaveValue("Retained project archive draft");
    expect(await saved(page)).toEqual(cp(1)); expect(writes).toHaveLength(0);
    await page.getByRole("button", {name: "More", exact: true}).click();
    await expect.poll(() => saved(page)).toEqual(cp(3));
    expect(await page.evaluate(() => JSON.stringify({...localStorage, ...sessionStorage}))).toContain("Retained project archive draft");
  });
}

function historyFixture() {
  let cursor = 10;
  const requests: any[] = [];
  const scope = {profile_id: "profile-fixture", status: "partial", last_success_at: "2026-10-08T12:00:00Z",
    finished_at: "2026-10-08T12:01:00Z", error_code: "SOURCE_UNAVAILABLE", authority: {kind: "bat_authenticated_read", verified: true},
    coverage: {workspace_ids: ["workspace-fixture"], session_count: 1}, methods: {"workspace:load": {status: "succeeded"}},
    outside_scan: [{scope: "manual_sessions_without_tabs_or_facts", reason: "not_enumerable"}]};
  const read = async (input: any) => {
    requests.push(input);
    const url = new URL(input.path, "http://fixture"), path = url.pathname;
    const event = (seq: number) => ({seq, kind: "session.updated", resource_type: "session", resource_id: "demo/session-1",
      actor: "inventory", created_at: 1791460800, body: {worktree_id: "wt_" + "a".repeat(32)},
      context: {occurred_at: null, recorded_at: "2026-10-08T12:00:00Z"}});
    const data = path === "/capabilities" ? caps : path === "/bootstrap" ? {capabilities: caps,
      sync: {version: 1, server_id: "history-server", principal_id: "principal", checkpoint: cp(10)}}
      : path === "/events" ? {events: Number(url.searchParams.get("after")) < cursor ? [event(cursor)] : [],
        head_cursor: cursor, next_cursor: cursor, has_more: false, sync: {checkpoint: cp(cursor)}}
      : path === "/sessions/demo/session-1" ? {session: {host: "demo", session_id: "session-1", title: "Observed history",
        api_access: "managed", provenance: "connector_managed", loaded: false, has_tab: false, streaming: false,
        state: {connection: "connected", loading: "not_loaded", tab: "no_tab", activity: "not_streaming", lifecycle: "unknown",
          enumeration: "present", freshness: "stale", evidence: {lifecycle: {observed_at: null, source_ref: "journal_identity"},
            loading: {observed_at: "2026-10-08T12:00:00Z", source_ref: "sessions_observed:demo/session-1", stale: true}}}}, discovery: [scope]}
      : path.endsWith("/history") ? {events: url.searchParams.has("cursor") ? [event(8)] : [event(cursor)],
        as_of: url.searchParams.has("cursor") ? 10 : cursor, next_cursor: url.searchParams.has("cursor") ? null : "history-page-2",
        coverage: {source: "journal", first_recorded_at: null, legacy_transitions: "may_be_incomplete"}}
      : path.endsWith("/relations") || path === "/tasks/aaaaaaaa/sessions" ? {relations: [{relation_id: "relation-fixture",
        execution_id: "aaaaaaaa", session_resource_id: "demo/session-1", role: "carrier", status: "closed", reason: "failover",
        start_seq: 2, end_seq: 7, started_at: null, ended_at: null, command_ids: ["op_" + "b".repeat(32)]}], as_of: cursor, next_cursor: null}
      : path.endsWith("/discovery") ? {host: "demo", scopes: [scope]}
      : path.startsWith("/worktrees/") ? {worktree: {resource_id: "wt_" + "a".repeat(32), host: "demo", scope_status: "current"}}
      : path === "/tasks/aaaaaaaa" ? {task: {task_id: "aaaaaaaa", state: "waiting", host: "demo"}}
      : {messages: [], checkpoints: [], operations: [], hosts: [], sessions: [], work_items: []};
    return {status: 200, data};
  };
  return {read, requests, advance: () => {cursor = 11;}};
}

for (const native of [false, true]) {
  test(`history pages keep their snapshot and link known identities (${native ? "native" : "browser"})`, async ({page}) => {
    const fixture = historyFixture(); await mount(page, native, fixture.read);
    await page.goto("/dashboard/#/session/demo/session-1");
    const history = page.locator('[data-observation="history"]'), relations = page.locator('[data-observation="relations"]');
    await history.locator("summary").first().click();
    await expect(history.locator('[data-history-seq="10"]')).toBeVisible();
    await expect(history).toContainText("Occurred: unknown");
    await relations.locator("summary").first().click();
    await expect(relations).toContainText("Sequence interval [2, 7)");
    await expect(relations.getByRole("link", {name: "aaaaaaaa", exact: true})).toHaveAttribute("href", "#/task/aaaaaaaa");
    await page.locator("textarea").first().fill("Draft survives history invalidation");
    fixture.advance();
    await expect.poll(() => saved(page)).toEqual(cp(11));
    await expect(history).toContainText("New journal facts are available.");
    await expect(history).toContainText("Fixed journal snapshot: 10.");
    await history.getByRole("button", {name: "Load more"}).click();
    await expect(history.locator("[data-history-seq]")).toHaveCount(2);
    const read = fixture.requests.find(x => x.path.includes("history-page-2"));
    expect(new URL(read.path, "http://fixture").searchParams.get("order")).toBe("desc");
    await expect(page.locator("textarea").first()).toHaveValue("Draft survives history invalidation");
    await history.getByRole("button", {name: "Read latest records"}).click();
    await expect(history.locator('[data-history-seq="11"]')).toBeVisible();
    await expect(history.locator('[data-history-seq="8"]')).toHaveCount(0);
    await history.getByRole("link", {name: "wt_" + "a".repeat(32)}).click();
    await expect(page.getByRole("heading", {name: "Worktree", exact: true})).toBeVisible();
    await page.locator('[data-observation="relations"] summary').first().click();
    await page.getByRole("link", {name: "aaaaaaaa", exact: true}).click();
    await expect(page.getByRole("heading", {name: "Execution", exact: true})).toBeVisible();
    await page.locator('[data-observation="relations"] summary').first().click();
    await expect.poll(() => fixture.requests.some(x => x.path.startsWith("/tasks/aaaaaaaa/sessions?"))).toBe(true);
  });

  test(`session inventory refresh retains loaded pages with stable ID order (${native ? "native" : "browser"})`, async ({page}) => {
    let changed = false;
    const requests: string[] = [];
    await mount(page, native, async input => {
      const url = new URL(input.path, "http://fixture"), path = url.pathname; requests.push(input.path);
      const data = path === "/capabilities" ? caps : path === "/bootstrap" ? {capabilities: caps,
        sync: {version: 1, server_id: "inventory-server", principal_id: "principal", checkpoint: cp(0)}}
        : path === "/events" ? {events: changed && url.searchParams.get("after") === "0" ? [{seq: 1,
          resource_type: "session", resource_id: "demo/session-2", kind: "session.updated"}] : [],
          head_cursor: changed ? 1 : 0, next_cursor: changed ? 1 : 0, has_more: false, sync: {checkpoint: cp(changed ? 1 : 0)}}
        : path === "/hosts" ? {hosts: [{host: "demo"}]} : path === "/sessions" ? {sessions: [{host: "demo",
          session_id: url.searchParams.has("cursor") ? "session-2" : "session-1", api_access: "managed", provenance: "connector_managed",
          title: url.searchParams.has("cursor") ? changed ? "Updated second session" : "Second session" : "First session"}],
          next_cursor: url.searchParams.has("cursor") ? null : "inventory-second-page"} : {};
      return {status: 200, data};
    });
    await page.goto("/dashboard/#/sessions");
    await page.locator("#main").getByRole("button", {name: "Load more"}).click();
    await expect(page.getByRole("link", {name: "Second session", exact: true})).toBeVisible();
    changed = true;
    await expect(page.getByRole("link", {name: "Updated second session", exact: true})).toBeVisible();
    await expect(page.locator("[data-resource-id]")).toHaveCount(2);
    await expect.poll(() => saved(page)).toEqual(cp(1));
    expect(requests.filter(x => x.startsWith("/sessions?")).every(x => new URL(x, "http://fixture").searchParams.get("order") === "id")).toBe(true);
    await expect(page.getByRole("combobox", {name: "Host", exact: true})).toHaveValue("");
  });
}

for (const locale of ["en-US", "zh-TW"]) for (const width of [390, 768, 1440]) {
  test(`observation evidence layout ${locale} ${width}`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}});
    const page = await context.newPage();
    const fixture = historyFixture(); await mount(page, false, fixture.read);
    const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
    await page.goto("/dashboard/#/session/demo/session-1");
    await expect(page.locator("[data-session-view]")).toBeVisible();
    const opener = page.getByRole("button", {name: /^(Results|成果)$/, exact: true});
    if (await opener.isVisible()) await opener.click();
    await page.locator(".workspace-evidence > summary").click();
    await page.locator(".observation-evidence > summary").click();
    await page.locator('[data-observation="history"] > summary').click();
    await expect(page.locator('[data-history-seq="10"]')).toBeVisible();
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({path: `test-results/observation-${locale}-${width}.png`, fullPage: true});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const sheet = page.getByRole("dialog");
    if (await sheet.isVisible()) {await page.keyboard.press("Escape"); await expect(sheet).toBeHidden();}
    await page.locator(".workspace-session-heading").getByRole("link", {name: "demo", exact: true}).click();
    await expect(page.getByText("profile-fixture", {exact: true})).toBeVisible();
    await expect(page.getByText("not_enumerable", {exact: true})).toBeVisible();
    expect(errors).toEqual([]); await context.close();
  });
}

for (const native of [false, true]) test(`failed first read never invents manual session ownership (${native ? 'native' : 'browser'})`, async ({page}) => {
  let provenance: string | null = null;
  await mount(page, native, async input => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path === '/sessions/demo/missing') return provenance ? {status: 200, data: {session: {
      host: 'demo', session_id: 'missing', provenance, api_access: 'read_only'}}}
      : {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Session not found'}}};
    return {status: 200, data: path === '/capabilities' ? caps : path === '/bootstrap' ? {capabilities: caps,
      sync: {version: 1, server_id: 'server', principal_id: 'principal', checkpoint: cp(0)}}
      : path === '/events' ? {events: [], head_cursor: 0, next_cursor: 0, sync: {checkpoint: cp(0)}}
      : {messages: [], checkpoints: [], operations: [], hosts: [], sessions: [], work_items: []}};
  });
  await page.goto('/dashboard/#/session/demo/missing');
  await expect(page.locator('[data-session-withdrawn=gone]')).toContainText('This session is no longer available');
  await expect(page.locator('[data-session-view]')).toHaveCount(0);
  await expect(page.locator('[data-session-readonly]')).toHaveCount(0);
  await expect(page.getByText('A person created this session in BAT', {exact: false})).toHaveCount(0);
  provenance = 'unknown'; await page.reload();
  await expect(page.locator('[data-session-readonly]')).toHaveText('Control is unconfirmed, so it is read-only here.');
  await expect(page.getByRole('button', {name: 'Send', exact: true})).toBeHidden();
  provenance = 'manual'; await page.reload();
  await expect(page.locator('[data-session-readonly]').getByRole('heading')).toHaveText('View only');
  await expect(page.getByRole('button', {name: 'Send', exact: true})).toBeHidden();
});
