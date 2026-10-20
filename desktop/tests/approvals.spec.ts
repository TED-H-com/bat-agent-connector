import {test, expect, type Page} from '@playwright/test';
const id = 'op_'+'a'.repeat(32), child = 'op_'+'b'.repeat(32), item = 'bapi_'+'c'.repeat(24);
async function fixture(page: Page, native: boolean, options: any = {}) {
  const state = {actor: 'approval-person', server: 'approval-server', principal: 'approval-principal',
    scopes: ['observe', 'operate'], allowed: true, writes: true, status: 'succeeded',
    posts: [] as any[], previews: [] as any[], reads: [] as string[], events: [] as any[], errors: [] as string[],
    after: 0, lost: false, expired: false, badKey: false, operation: null as any, ...options};
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    const caps = {actor: state.actor, scopes: state.scopes, api_version: 1, contract_version: '2026-10-08',
      hosts: [{host: 'demo', writes: state.writes}], actions: [{action: 'session.approve_pending', allowed: state.allowed}], features: {checkpoints: []}};
    if (path === '/approval-previews') {
      state.previews.push(input);
      if (state.delayPreview) await new Promise(resolve => {state.holdPreview = resolve;});
      return {status: 200, data: {host: input.body.host, workspace: input.body.workspace || null,
        preview_id: 'bapv_'+'d'.repeat(32), preview_token: 'bap1.fixture.signature', fingerprint: 'e'.repeat(64), // gitleaks:allow (unsigned fixture)
        issued_at: Date.now()/1000, expires_at: Date.now()/1000 + (state.expired ? -1 : 600),
        answer: {permission: 'allow', dont_ask_again: true}, truncated: state.truncated || false,
        items: [{item_id: item, host: 'demo', session_id: 'managed-session-1', eligible: true, agent_kind: 'claude',
          prompt: {toolUseId: 'tool-1', toolName: 'Bash', input: {command: '<script>Never execute markup</script>\n'+ 'long-command-'.repeat(30)}},
          allowed_modes: [null, 'default', 'allow_all']},
          {item_id: 'bapi_'+'f'.repeat(24), host: 'demo', session_id: 'manual-session-1', eligible: false, code: 'MANUAL_READ_ONLY'}]}};
    }
    if (input.method === 'POST') {
      state.posts.push(input);
      if (state.refuse) return {status: 409, data: {error: {code: state.refuse, message: 'Fixture refusal'}}};
      if (!state.operation) state.operation = {operation_id: id, idempotency_key: input.idempotency_key,
        actor: state.actor, ...input.body, status: state.status, steps: [], external_refs: {}, result: {all_succeeded: false,
          items: [{item_id: item, host: 'demo', session_id: 'managed-session-1', complete: false,
            answer: {operation_id: child, status: 'failed', code: 'BULK_PROMPT_CHANGED'}}]}};
      if (state.delayPost) await new Promise(resolve => {state.holdPost = resolve;});
      if (state.lost) {state.lost = false; return {status: 503, data: {error: {code: 'LOST', message: 'Lost batch reply'}}};}
      const op = structuredClone(state.operation);
      if (state.badKey) op.idempotency_key = 'wrong-key';
      if (state.badSelection) op.params.selection[0].mode = 'allow_all';
      if (state.badActor) op.actor = 'another-person';
      return {status: 200, data: {operation: op}};
    }
    state.reads.push(input.path);
    if (path === '/operations/'+id) {
      if (state.delayRead) {state.delayRead = false; await new Promise(resolve => {state.holdRead = resolve;});}
      if (state.failRead) return {status: 503, data: {error: {code: 'READ_FAILED', message: 'Batch read failed'}}};
      return {status: 200, data: {operation: {...state.operation, status: state.status}}};
    }
    if (path === '/events') state.after = Number(url.searchParams.get('after'));
    const events = state.events.filter((e: any) => e.seq > state.after), cursor = events.at(-1)?.seq || state.after;
    if (path === '/events' && events.length) state.delivered = true;
    return {status: 200, data: path === '/capabilities' ? caps : path === '/bootstrap' ? {capabilities: caps,
      sync: {version: 1, server_id: state.server, principal_id: state.principal, checkpoint: {cursor: 0, token: 'proof-0'}}}
      : path === '/events' ? {events, head_cursor: cursor, next_cursor: cursor, sync: {checkpoint: {cursor, token: 'proof-'+cursor}}}
      : {hosts: [{host: 'demo'}], sessions: [], operations: [], work_items: [], checkpoints: []}};
  };
  page.on('pageerror', e => state.errors.push(e.message));
  if (native) {
    await page.exposeFunction('approvalFixture', dispatch);
    await page.addInitScript(() => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === 'native_status') return {endpoint: 'https://fixture.example', credential_available: true};
      if (command === 'connector_connect') return (await (window as any).approvalFixture({method: 'GET', path: '/capabilities'})).data;
      if (command === 'connector_disconnect') return;
      if (command === 'connector_request') return (window as any).approvalFixture(args.input);
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      throw new Error(command);
    }}}));
  } else await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
  await page.route('**/api/v1/**', async route => {
    if (native) throw new Error('Native request cannot use browser HTTP');
    const req = route.request(), url = new URL(req.url());
    const response = await dispatch({method: req.method(), path: url.pathname.slice('/api/v1'.length)+url.search,
      body: req.postDataJSON(), idempotency_key: req.headers()['idempotency-key']});
    await route.fulfill({status: response.status, json: response.data});
  });
  return state;
}
async function open(page: Page, locale = 'en-US') {
  await page.goto('/dashboard/#/approvals');
  const box = page.locator('[data-approvals]');
  await box.locator('select').first().selectOption('demo');
  await box.getByRole('button', {name: locale === 'en-US' ? 'Preview pending requests' : '預覽待核准請求'}).click();
  await expect(box.locator('[data-approval-item]')).toHaveCount(2);
  return box;
}
for (const native of [false, true]) {
  const kind = native ? 'native' : 'browser';
  test(`${kind}: unavailable durable storage prevents approval admission`, async ({page}) => {
    const state = await fixture(page, native); const box = await open(page);
    await box.getByRole('checkbox').first().check();
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      (window as any).restoreStorage = () => {Storage.prototype.setItem = original;};
      Storage.prototype.setItem = function(key, value) {
        if (key.startsWith('batc.approvals.')) throw new Error('Fixture storage unavailable');
        return original.call(this, key, value);
      };
    });
    await box.getByRole('button', {name: 'Approve selected requests'}).click();
    await expect(box).toContainText('Fixture storage unavailable');
    expect(state.posts).toEqual([]);
    await expect(box.locator('select').first()).toBeDisabled();
    await page.evaluate(() => (window as any).restoreStorage());
    await box.getByRole('button', {name: 'Retry original request'}).click();
    await expect(box).toContainText('Not all items are proven successful');
    expect(state.posts).toHaveLength(1);
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k => k.startsWith('batc.approvals.'))!)!));
    expect(saved.intent.key).toBe(state.posts[0].idempotency_key);
    expect(saved.intent.request).toEqual(state.posts[0].body);
    expect(state.errors).toEqual([]);
  });
  test(`${kind}: explicit selection and partial receipts, no implicit mode raise`, async ({page}) => {
    const state = await fixture(page, native); const box = await open(page);
    const apply = box.getByRole('button', {name: 'Approve selected requests'});
    await expect(apply).toBeDisabled();
    await expect(box.getByRole('checkbox').nth(1)).toBeDisabled();
    await expect(box.locator('pre').first()).toContainText('<script>Never execute markup</script>');
    await box.getByRole('checkbox').first().check(); await apply.click();
    await expect(box).toContainText('Not all items are proven successful');
    await expect(box).toContainText('BULK_PROMPT_CHANGED');
    expect(state.posts[0].body.params.selection).toEqual([{item_id: item, mode: null}]);
    await expect(box.getByRole('link', {name: 'failed', exact: true})).toHaveAttribute('href', '#/op/'+child);
    expect(state.errors).toEqual([]);
  });
  test(`${kind}: selection draft survives reload and lost reply preserves original key after expiry`, async ({page}) => {
    const state = await fixture(page, native, {lost: true}); let box = await open(page);
    await box.getByRole('checkbox').first().check();
    await box.getByRole('combobox', {name: 'Subsequent permission mode for managed-session-1'}).selectOption('allow_all');
    await page.reload(); box = page.locator('[data-approvals]');
    await expect(box.getByRole('checkbox').first()).toBeChecked();
    await expect(box.getByRole('combobox', {name: 'Subsequent permission mode for managed-session-1'})).toHaveValue('allow_all');
    await box.getByRole('button', {name: 'Approve selected requests'}).click();
    await expect(box).toContainText('Lost batch reply');
    const original = structuredClone(state.posts[0]);
    await page.evaluate(() => {for (const key of Object.keys(localStorage).filter(k => k.startsWith('batc.approvals.'))) {
      const value = JSON.parse(localStorage.getItem(key)!); value.preview.expires_at = 1; localStorage.setItem(key, JSON.stringify(value));
    }});
    await page.reload(); box = page.locator('[data-approvals]');
    await expect(box.locator('select').first()).toBeDisabled();
    await box.getByRole('button', {name: 'Retry original request'}).click();
    await expect(box).toContainText('Not all items are proven successful');
    expect(state.posts[1]).toEqual(original); expect(state.previews).toHaveLength(1);
    await page.reload(); await expect.poll(() => state.reads.includes('/operations/'+id)).toBe(true);
    expect(state.posts).toHaveLength(2);
  });
  test(`${kind}: response identity mismatches cannot release the original request`, async ({page}) => {
    const state = await fixture(page, native, {badKey: true}); const box = await open(page);
    await box.getByRole('checkbox').first().check(); await box.getByRole('button', {name: 'Approve selected requests'}).click();
    for (const invalid of ['badKey', 'badActor', 'badSelection']) {
      state.badKey = false; state.badActor = false; state.badSelection = false; state[invalid] = true;
      await box.getByRole('button', {name: 'Retry original request'}).click();
      await expect(box).toContainText('does not match');
      await expect(box.getByRole('button', {name: 'Review another batch'})).toBeHidden();
    }
    expect(new Set(state.posts.map(p => p.idempotency_key)).size).toBe(1);
  });
  test(`${kind}: expired preview, missing scope and disabled host never apply`, async ({page}) => {
    const state = await fixture(page, native, {expired: true}); let box = await open(page);
    await expect(box).toContainText('This preview expired'); await expect(box.getByRole('checkbox').first()).toBeDisabled();
    state.expired = false; state.scopes = ['observe']; await page.reload(); box = await open(page);
    await expect(box.getByRole('checkbox').first()).toBeDisabled();
    state.scopes = ['observe', 'operate']; state.writes = false; await page.reload(); box = await open(page);
    await expect(box.getByRole('button', {name: 'Approve selected requests'})).toBeDisabled();
    expect(state.posts).toEqual([]);
  });
  test(`${kind}: changing backend isolates saved accepted work`, async ({page}) => {
    const state = await fixture(page, native, {lost: true}); const box = await open(page);
    await box.getByRole('checkbox').first().check(); await box.getByRole('button', {name: 'Approve selected requests'}).click();
    await expect(box).toContainText('Lost batch reply');
    state.server = 'another-server'; await page.reload();
    await expect(page.getByRole('button', {name: 'Retry original request'})).toHaveCount(0);
    await expect(page.locator('[data-approval-item]')).toHaveCount(0);
    expect(state.posts).toHaveLength(1);
  });
  test(`${kind}: event received before POST reply waits for identity and latest outcome`, async ({page}) => {
    const state = await fixture(page, native, {delayPost: true, status: 'running'}); const box = await open(page);
    await box.getByRole('checkbox').first().check(); await box.getByRole('button', {name: 'Approve selected requests'}).click();
    await expect.poll(() => Boolean(state.holdPost)).toBe(true);
    state.events.push({seq: 1, kind: 'operation.updated', resource_type: 'operation', resource_id: id});
    await expect.poll(() => state.delivered).toBe(true);
    expect(state.reads).not.toContain('/operations/'+id);
    state.status = 'succeeded'; state.holdPost();
    await expect.poll(() => state.reads.includes('/operations/'+id)).toBe(true);
    await expect(box.getByRole('button', {name: 'Review another batch'})).toBeVisible();
    expect(state.posts).toHaveLength(1);
  });
  test(`${kind}: accepted read failure retains intent and expired admission can explicitly reset`, async ({page}) => {
    const state = await fixture(page, native, {refuse: 'BULK_PREVIEW_EXPIRED'}); const box = await open(page);
    await box.getByRole('checkbox').first().check(); await box.getByRole('button', {name: 'Approve selected requests'}).click();
    await box.getByRole('button', {name: 'Review another batch'}).click();
    state.refuse = null; await box.getByRole('button', {name: 'Preview pending requests'}).click();
    await box.getByRole('checkbox').first().check(); await box.getByRole('button', {name: 'Approve selected requests'}).click();
    await expect(box.getByRole('button', {name: 'Check batch outcome'})).toBeVisible();
    state.failRead = true; await box.getByRole('button', {name: 'Check batch outcome'}).click();
    await expect(box).toContainText('Batch read failed'); await expect(box.getByRole('button', {name: 'Review another batch'})).toBeDisabled();
    expect(state.posts[0].idempotency_key).not.toBe(state.posts[1].idempotency_key);
  });
  test(`${kind}: auth and conflict after lost reply never release the accepted key`, async ({page}) => {
    const state = await fixture(page, native, {lost: true}); const box = await open(page);
    await box.getByRole('checkbox').first().check(); await box.getByRole('button', {name: 'Approve selected requests'}).click();
    await expect(box).toContainText('Lost batch reply');
    for (const code of ['FORBIDDEN', 'UNKNOWN_ACTION', 'IDEMPOTENCY_CONFLICT']) {
      state.refuse = code; await box.getByRole('button', {name: 'Retry original request'}).click();
      await expect(box).toContainText(code); await expect(box.getByRole('button', {name: 'Review another batch'})).toBeHidden();
    }
    expect(new Set(state.posts.map(p => p.idempotency_key)).size).toBe(1);
  });
  for (const code of ['CONTROL_VERSION_CONFLICT', 'BULK_BINDING_CHANGED']) test(`${kind}: ${code} permits explicit reviewed replacement only`, async ({page}) => {
    const state = await fixture(page, native, {refuse: code}); const box = await open(page);
    await box.getByRole('checkbox').first().check(); await box.getByRole('button', {name: 'Approve selected requests'}).click();
    await expect(box).toContainText(code);
    const before = await page.evaluate(() => JSON.parse(Object.entries(localStorage).find(([k]) => k.startsWith('batc.approvals.'))![1]).intent);
    await page.reload();
    await expect(box.getByRole('button', {name: 'Review another batch'})).toBeVisible();
    expect(state.posts).toHaveLength(1); expect(state.operation).toBeNull();
    const stored = await page.evaluate(() => JSON.parse(Object.entries(localStorage).find(([k]) => k.startsWith('batc.approvals.'))![1]).intent);
    expect(stored.request).toEqual(before.request); expect(stored.key).toBe(before.key);
    await expect(box.getByRole('button', {name: 'Preview pending requests'})).toBeDisabled();
    await box.getByRole('button', {name: 'Review another batch'}).click();
    state.refuse = null; await box.getByRole('button', {name: 'Preview pending requests'}).click();
    await expect(box.getByRole('checkbox').first()).not.toBeChecked();
    await box.getByRole('checkbox').first().check();
    await box.getByRole('combobox', {name: 'Subsequent permission mode for managed-session-1'}).selectOption('default');
    await box.getByRole('button', {name: 'Approve selected requests'}).click();
    await expect(box.getByRole('button', {name: 'Check batch outcome'})).toBeVisible();
    expect(state.posts[1].idempotency_key).not.toBe(state.posts[0].idempotency_key);
    expect(state.posts[1].body.params.selection).toEqual([{item_id: item, mode: 'default'}]);
  });
  test(`${kind}: late preview cannot write into a different page or create an operation`, async ({page}) => {
    const state = await fixture(page, native, {delayPreview: true});
    await page.goto('/dashboard/#/approvals');
    await page.locator('[data-approvals] select').first().selectOption('demo');
    await page.getByRole('button', {name: 'Preview pending requests'}).click();
    await expect.poll(() => Boolean(state.holdPreview)).toBe(true);
    await page.locator('.workspace-nav-footer').getByRole('link', {name: 'Settings', exact: true}).click();
    await expect(page.locator('[data-approvals]')).toHaveCount(0);
    state.holdPreview();
    const saved = await page.evaluate(() => JSON.parse(Object.entries(localStorage).find(([k]) => k.startsWith('batc.approvals.'))![1]));
    expect(saved.preview).toBeUndefined(); expect(state.posts).toEqual([]); expect(state.errors).toEqual([]);
  });
  test(`${kind}: damaged stored accepted request remains readback only`, async ({page}) => {
    const state = await fixture(page, native); const box = await open(page);
    await box.getByRole('checkbox').first().check(); await box.getByRole('button', {name: 'Approve selected requests'}).click();
    await expect(box.getByRole('button', {name: 'Check batch outcome'})).toBeVisible();
    await page.evaluate(() => {for (const key of Object.keys(localStorage).filter(k => k.startsWith('batc.approvals.'))) {
      const value = JSON.parse(localStorage.getItem(key)!); value.intent.request.params.selection = null; value.preview.items = null;
      localStorage.setItem(key, JSON.stringify(value));
    }});
    await page.reload();
    await expect(box.getByRole('link', {name: 'View operation and step receipts'})).toHaveAttribute('href', '#/op/'+id);
    await expect(box.getByRole('button', {name: 'Review another batch'})).toBeHidden();
    expect(state.posts).toHaveLength(1); expect(state.errors).toEqual([]);
  });
}
for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) {
  test(`approval layout ${locale} ${width}`, async ({page}, testInfo) => {
    await page.addInitScript(language => Object.defineProperty(navigator, 'language', {value: language}), locale);
    await page.setViewportSize({width, height: 1000}); await fixture(page, true, {truncated: true}); await open(page, locale);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path: testInfo.outputPath(`${locale}-${width}.png`), fullPage: true});
  });
}
