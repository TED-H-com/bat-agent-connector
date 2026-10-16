import {test, expect} from "@playwright/test";

for (const native of [false, true]) {
  test(`PR selector over ${native ? "native IPC" : "browser HTTP"}`, async ({page}) => {
    let requests: any[] = [];
    let failList = false;
    const caps = {actor: "test", scopes: ["observe"], api_version: 1, contract_version: "2026-10-08", hosts: [], repositories: [], features: {}};
    const dispatch = async (input: any) => {
      requests.push(input);
      const url = new URL(input.path, "http://fixture");
      if (input.method === "GET" && input.path === "/capabilities") {
        return {status: 200, data: caps};
      }
      if (input.method === "GET" && input.path === "/bootstrap") {
        return {status: 200, data: {capabilities: caps, sync: {version: 1, server_id: "server", principal_id: "principal", checkpoint: {cursor: 0, token: "token"}}}};
      }
      if (input.method === "GET" && input.path.startsWith("/repositories/")) {
        if (failList) return {status: 503, data: {error:{code:'UNAVAILABLE',message:'Provider unavailable'}}};
        const pathParts = url.pathname.split("/");
        const owner = pathParts[2];
        const repo = pathParts[3];
        const repoFull = `${owner}/${repo}`;
        const queryPage = url.searchParams.get("page");
        const queryState = url.searchParams.get("state");
        
        if (repoFull === "delay/repo") {
          await new Promise(r => setTimeout(r, 100)); // Simulate delay for stale race
          return {status: 200, data: {pulls: [{number: 99, title: "Stale PR", state: "open"}], loaded_scope: repoFull}};
        }
        
        if (queryPage === "1") {
          return {status: 200, data: {
            pulls: [
              {number: 1, title: "Fix bug", state: queryState, draft: false},
              {number: 2, title: "Add feature", state: queryState, draft: true}
            ],
            has_more: true,
            next_page: 2,
            loaded_scope: repoFull
          }};
        } else if (queryPage === "2") {
          return {status: 200, data: {
            pulls: [
              {number: 3, title: "Third PR", state: queryState, draft: false}
            ],
            has_more: false,
            loaded_scope: repoFull
          }};
        }
      }
      return {status: 200, data: {sessions: [], work_items: [], operations: [], hosts: []}};
    };

    if (native) {
      await page.exposeFunction("fixtureConnector", dispatch);
      await page.addInitScript(caps => {
        Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
          if (command === "native_status") return {endpoint: "https://central.example/", credential_available: true};
          if (command === "connector_connect") return caps;
          if (command === "connector_disconnect") return null;
          if (command === "connector_request") return (window as any).fixtureConnector(args.input);
          throw new Error(`Unexpected native command: ${command}`);
        }}});
      }, caps);
    } else await page.addInitScript(() => sessionStorage.setItem("batc.dashboard.token", "fixture-token"));
    
    await page.route("**/api/v1/**", async route => {
      if (native) return route.abort();
      const request = route.request(), url = new URL(request.url());
      const response = await dispatch({method: request.method(), path: url.pathname.slice("/api/v1".length) + url.search,
        body: request.postDataJSON(), idempotency_key: request.headers()["idempotency-key"]});
      return route.fulfill({status: response.status, json: response.data});
    });
    
    await page.addInitScript(locale => Object.defineProperty(navigator, "language", {value: locale}), "en-US"); await page.goto(`/dashboard/#/delivery`);
    
    // Wait for the delivery view to load
    await expect(page.locator("h2", {hasText: "Pull request"})).toBeVisible();

    const repoInput = page.getByLabel("Repository");
    const numInput = page.getByLabel("PR number");
    const listPrs = page.locator("summary", {hasText: "List Pull Requests"});
    
    await repoInput.fill("owner/repo");
    await listPrs.click();
    
    // Wait for PR list to load
    const prList = page.locator(".pr-list");
    await expect(prList.getByText("#1 Fix bug")).toBeVisible();
    await expect(prList.getByText("#2 Add feature")).toBeVisible();
    await expect(prList.getByText("Open · draft", {exact: true})).toBeVisible();

    // Test Search
    const searchInput = page.getByRole("searchbox", {name: "Search this page of PRs"});
    await searchInput.fill("feature");
    await expect(prList.getByText("#2 Add feature")).toBeVisible();
    await expect(prList.getByText("#1 Fix bug")).not.toBeVisible();
    
    // Test Exact Repo Selection
    await prList.getByRole("button", {name: "#2 Add feature"}).click();
    await expect(numInput).toHaveValue("2");
    await expect(repoInput).toHaveValue("owner/repo");
    
    // Test Pagination
    await searchInput.fill(""); // clear search
    const nextBtn = page.getByRole("button", {name: "Next"});
    await expect(nextBtn).toBeEnabled();
    await nextBtn.click();
    
    await expect(prList.getByText("#3 Third PR")).toBeVisible();
    const prevBtn = page.getByRole("button", {name: "Previous"});
    await expect(prevBtn).toBeEnabled();
    await expect(nextBtn).toBeDisabled();

    failList = true;
    await page.getByRole('button',{name:'Refresh this page of PRs',exact:true}).click();
    await expect(prList).toContainText('Provider unavailable');
    await expect(prList.getByRole('button',{name:'#3 Third PR'})).toBeDisabled();
    await searchInput.fill('Third');
    await expect(prList.getByRole('button',{name:'#3 Third PR'})).toBeDisabled();
    await expect(prList).toContainText('Refresh before selecting');
    failList = false; await searchInput.fill('');
    
    // Test Stale Race: Type a repo that delays, then quickly type another
    await repoInput.fill("delay/repo");
    const refreshBtn = page.getByRole("button", {name: "Refresh this page of PRs", exact: true});
    await refreshBtn.click(); // Fires request 1
    await repoInput.fill("owner/repo");
    await refreshBtn.click(); // Fires request 2
    
    // Wait for request 2 to resolve
    await expect(prList.getByText("#1 Fix bug")).toBeVisible();
    // Verify Stale PR never appears
    await expect(prList.getByText("#99 Stale PR")).not.toBeVisible();
  });

  test(`PR selector bilingual zh-TW over ${native ? "native IPC" : "browser HTTP"}`, async ({page}) => {
    let requests: any[] = [];
    const caps = {actor: "test", scopes: ["observe"], api_version: 1, contract_version: "2026-10-08", hosts: [], repositories: [], features: {}};
    const dispatch = async (input: any) => {
      requests.push(input);
      const url = new URL(input.path, "http://fixture");
      if (input.method === "GET" && input.path === "/capabilities") {
        return {status: 200, data: caps};
      }
      if (input.method === "GET" && input.path === "/bootstrap") {
        return {status: 200, data: {capabilities: caps, sync: {version: 1, server_id: "server", principal_id: "principal", checkpoint: {cursor: 0, token: "token"}}}};
      }
      return {status: 200, data: {pulls: [{number: 1, title: "Test", state: "open", draft: true}], loaded_scope: "a/b"}};
    };
    if (native) {
      await page.exposeFunction("fixtureConnector", dispatch);
      await page.addInitScript(caps => {
        Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
          if (command === "native_status") return {endpoint: "https://central.example/", credential_available: true};
          if (command === "connector_connect") return caps;
          if (command === "connector_disconnect") return null;
          if (command === "connector_request") return (window as any).fixtureConnector(args.input);
          throw new Error(`Unexpected native command: ${command}`);
        }}});
      }, caps);
    } else await page.addInitScript(() => sessionStorage.setItem("batc.dashboard.token", "fixture-token"));
    
    await page.route("**/api/v1/**", async route => {
      if (native) return route.abort();
      const request = route.request(), url = new URL(request.url());
      const response = await dispatch({method: request.method(), path: url.pathname.slice("/api/v1".length) + url.search,
        body: request.postDataJSON(), idempotency_key: request.headers()["idempotency-key"]});
      return route.fulfill({status: response.status, json: response.data});
    });
    await page.addInitScript(locale => Object.defineProperty(navigator, "language", {value: locale}), "zh-TW"); await page.goto(`/dashboard/#/delivery`);
    
    await expect(page.locator("h2", {hasText: "合併請求（PR）"})).toBeVisible();
    await page.getByLabel("儲存庫").fill("a/b");
    await page.locator("summary", {hasText: "瀏覽 Pull Requests"}).click();
    
    const prList = page.locator(".pr-list");
    await expect(prList.getByText("#1 Test")).toBeVisible();
    await expect(prList.getByText("開啟 · 草稿", {exact: true})).toBeVisible();
    await expect(page.getByRole("searchbox", {name: "搜尋這一頁的 PR"})).toBeVisible();
    await expect(page.getByRole("button", {name: "下一頁"})).toBeVisible();
  });
}

function pullPage(pageNum: string, lastPage: number) {
  const page = Number(pageNum || "1");
  const more = page < lastPage;
  return {pulls: [{number: page, title: `PR ${page}`, state: "open", draft: false}], has_more: more,
    next_page: more ? page + 1 : null, loaded_scope: "owner/repo"};
}

async function openPulls(page: import("@playwright/test").Page, lastPage: number) {
  let pulls = 0, holding = false, release = () => {};
  let gate = Promise.resolve();
  const caps = {actor: "test", scopes: ["observe"], api_version: 1, contract_version: "2026-10-08", hosts: [], repositories: [], features: {}};
  const dispatch = async (input: any) => {
    const url = new URL(input.path, "http://fixture");
    if (input.method === "GET" && input.path === "/capabilities") return {status: 200, data: caps};
    if (input.method === "GET" && input.path === "/bootstrap") return {status: 200, data: {capabilities: caps, sync: {version: 1, server_id: "server", principal_id: "principal", checkpoint: {cursor: 0, token: "token"}}}};
    if (input.method === "GET" && url.pathname.includes("/pulls")) {
      pulls++;
      if (holding) await gate;
      const repo = url.pathname.split("/").slice(2, 4).join("/");
      const body = pullPage(url.searchParams.get("page") || "1", lastPage);
      body.loaded_scope = repo;
      return {status: 200, data: body};
    }
    return {status: 200, data: {sessions: [], work_items: [], operations: [], hosts: []}};
  };
  await page.addInitScript(() => sessionStorage.setItem("batc.dashboard.token", "fixture-token"));
  await page.route("**/api/v1/**", async route => {
    const request = route.request(), url = new URL(request.url());
    const response = await dispatch({method: request.method(), path: url.pathname.slice("/api/v1".length) + url.search});
    return route.fulfill({status: response.status, json: response.data});
  });
  await page.addInitScript(locale => Object.defineProperty(navigator, "language", {value: locale}), "en-US");
  await page.goto("/dashboard/#/delivery");
  await expect(page.locator("h2", {hasText: "Pull request"})).toBeVisible();
  const repoInput = page.getByLabel("Repository");
  await repoInput.fill("owner/repo");
  await page.locator("summary", {hasText: "List Pull Requests"}).click();
  const list = page.locator(".pr-list");
  const refresh = page.getByRole("button", {name: "Refresh this page of PRs", exact: true});
  const next = list.getByRole("button", {name: "Next", exact: true});
  const previous = list.getByRole("button", {name: "Previous", exact: true});
  const first = list.getByRole("button", {name: "#1 PR 1"});
  await expect(first).toBeVisible();
  await expect(first).toBeEnabled();
  await expect(refresh).toBeEnabled();
  // The delivery view also loads the list once on a short timer. Finish that
  // reload before a test presses a control, so the press is not replaced.
  const settled = pulls;
  await refresh.click();
  await expect.poll(() => pulls).toBeGreaterThan(settled);
  await expect(first).toBeEnabled();
  await expect(refresh).toBeEnabled();
  return {repoInput, list, refresh, next, previous, gets: () => pulls, hold() {holding = true; gate = new Promise(resolve => {release = resolve;});}, release() {holding = false; const done = release; release = () => {}; done();}};
}

async function buttonBusy(button: import("@playwright/test").Locator) {
  return button.evaluate(node => {
    const region = node.closest(".pr-selector")?.querySelector(".pr-list");
    const active = document.activeElement;
    const activeName = !active ? "" : active === document.body || active === document.documentElement ? active.tagName
      : active.getAttribute("aria-label") || (active.textContent || "").trim() || active.tagName;
    return {focused: active === node, active: activeName,
      ariaDisabled: node.getAttribute("aria-disabled"), disabled: (node as HTMLButtonElement).disabled, ariaBusy: region?.getAttribute("aria-busy") ?? null};
  });
}

test("PR buttons stay disabled without aria-disabled while a changed repository reloads", async ({page}) => {
  const ui = await openPulls(page, 2);
  const pr = ui.list.getByRole("button", {name: "#1 PR 1"});
  await ui.repoInput.blur();
  await expect(pr).toBeEnabled();
  const before = ui.gets();
  ui.hold();
  try {
    await ui.repoInput.evaluate(node => {node.value = "other/repo"; node.dispatchEvent(new Event("input", {bubbles: true}));});
    await ui.refresh.click();
    await expect.poll(() => ui.gets()).toBe(before + 1);
    expect(await pr.evaluate(node => ({disabled: (node as HTMLButtonElement).disabled, ariaDisabled: node.getAttribute("aria-disabled")}))).toEqual({disabled: true, ariaDisabled: null});
  } finally {ui.release();}
});

test("PR next keeps focus and ignores a second press while the next page loads", async ({page}) => {
  const ui = await openPulls(page, 3);
  const before = ui.gets();
  ui.hold();
  try {
    await ui.next.focus();
    await page.keyboard.press("Enter");
    await expect.poll(() => ui.gets()).toBe(before + 1);
    expect(await buttonBusy(ui.next)).toEqual({focused: true, active: "Next", ariaDisabled: "true", disabled: false, ariaBusy: "true"});
    await page.keyboard.press("Enter");
    ui.release();
    await expect(ui.list).not.toHaveAttribute("aria-busy", "true");
    expect(ui.gets()).toBe(before + 1);
    await expect(ui.next).toBeFocused();
  } finally {ui.release();}
});

test("PR next on the last page moves focus to previous", async ({page}) => {
  const ui = await openPulls(page, 2);
  await ui.next.focus();
  await page.keyboard.press("Enter");
  await expect(ui.list.getByRole("button", {name: "#2 PR 2"})).toBeVisible();
  await expect(ui.previous).toBeFocused();
});

test("PR previous on the first page moves focus to next", async ({page}) => {
  const ui = await openPulls(page, 2);
  await ui.next.click();
  await expect(ui.list.getByRole("button", {name: "#2 PR 2"})).toBeVisible();
  await ui.previous.focus();
  await page.keyboard.press("Enter");
  await expect(ui.list.getByRole("button", {name: "#1 PR 1"})).toBeVisible();
  await expect(ui.next).toBeFocused();
});

test("PR refresh keeps focus while the list reloads", async ({page}) => {
  const ui = await openPulls(page, 2);
  const before = ui.gets();
  ui.hold();
  try {
    await ui.refresh.focus();
    await page.keyboard.press("Enter");
    await expect.poll(() => ui.gets()).toBe(before + 1);
    expect(await buttonBusy(ui.refresh)).toEqual({focused: true, active: "Refresh this page of PRs", ariaDisabled: "true", disabled: false, ariaBusy: "true"});
    ui.release();
    await expect(ui.list).not.toHaveAttribute("aria-busy", "true");
    await expect(ui.refresh).toBeFocused();
  } finally {ui.release();}
});
