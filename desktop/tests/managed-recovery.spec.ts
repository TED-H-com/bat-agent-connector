import {test, expect} from '@playwright/test';

for (const locale of ['en-US', 'zh-TW']) test(`native recovery status and same-identity rebind retain drafts (${locale})`, async ({browser}) => {
  const context = await browser.newContext({locale});
  const page = await context.newPage();
  await page.addInitScript(() => {
    const caps = {actor: 'fixture-operator', scopes: ['observe', 'operate'], features: {}, hosts: [], actions: [],
      desktop_identity: {server_id: 'fixture-server', principal_id: 'fixture-principal'}};
    const fixture = {code: null as string | null, endpoint: 'http://127.0.0.1:41001/', writes: 0};
    Object.assign(window, {isTauri: true, __recovery: fixture, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === 'native_status') return {endpoint: fixture.endpoint, configuration_source: 'managed',
        managed_recovery: fixture.code, credential_available: true, connected: true, error: null};
      if (command === 'connector_connect') return caps;
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      if (command === 'connector_request') {
        const {method, path} = args.input;
        if (method === 'POST') fixture.writes++;
        if (path === '/bootstrap') return {status: 200, data: {capabilities: caps,
          sync: {version: 1, ...caps.desktop_identity, checkpoint: {cursor: 0, token: 'fixture-proof'}}}};
        if (path.startsWith('/events')) {
          if (fixture.code) throw 'fixture transport unavailable';
          return {status: 200, data: {events: [], next_cursor: 0, head_cursor: 0, has_more: false,
            sync: {checkpoint: {cursor: 0, token: 'fixture-proof'}}}};
        }
        return {status: 200, data: path === '/sessions/demo/session-1'
          ? {session: {host: 'demo', session_id: 'session-1', title: 'Fixture session', api_access: 'managed', provenance: 'connector'}}
          : {messages: [], checkpoints: [], sessions: [], hosts: [], operations: [], work_items: []}};
      }
      return {};
    }}});
  });
  await page.goto('/dashboard/#/session/demo/session-1');
  const draft = page.locator('textarea').first();
  await draft.fill('Retain this exact draft across local recovery');
  const storageBefore = await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('batc.sync.') || key.startsWith('batc.key.')).sort());
  for (const [code, text] of [
    ['MANAGED_RECOVERING', locale === 'zh-TW' ? '正在重新啟動本機背景服務…' : 'Restarting the local background service…'],
    ['MANAGED_RUNTIME_START_TIMEOUT', locale === 'zh-TW' ? '本機背景服務無法恢復' : 'The local background service could not recover.'],
    ['MANAGED_RECOVERY_IDENTITY_CHANGED', locale === 'zh-TW' ? '本機服務身分已變更' : 'The local service identity changed.'],
  ]) {
    await page.evaluate(code => { (window as any).__recovery.code = code; }, code);
    await expect(page.locator('#live')).toContainText(text, {timeout: 10000});
    await expect(page.locator('#live')).not.toContainText(code);
    await page.locator('#managed-recovery-details > summary').click();
    await expect(page.locator('#managed-recovery-details code')).toHaveText(code);
    await page.locator('#managed-recovery-details > summary').click();
    await expect(draft).toHaveValue('Retain this exact draft across local recovery');
  }
  await page.evaluate(() => { Object.assign((window as any).__recovery, {code: null, endpoint: 'http://127.0.0.1:41002/'}); });
  await expect(page.locator('#managed-recovery-details')).toBeHidden({timeout: 10000});
  await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected', {timeout: 10000});
  await expect(draft).toHaveValue('Retain this exact draft across local recovery');
  expect(await page.evaluate(() => (window as any).__recovery.writes)).toBe(0);
  expect(await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('batc.sync.') || key.startsWith('batc.key.')).sort())).toEqual(storageBefore);
  await context.close();
});
