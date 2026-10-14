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
