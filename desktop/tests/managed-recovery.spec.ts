import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';

const bootstrap = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));

for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 1280]) test(`native managed recovery status and same-identity reset retain drafts (${locale}, ${width})`, async ({browser}) => {
  const context = await browser.newContext({locale, viewport: {width, height: 844}});
  const page = await context.newPage();
  await page.addInitScript(bootstrap => {
    // Managed production includes desktop_identity in both responses.
    const caps = structuredClone(bootstrap.capabilities);
    const project = {project_id: 'prj_' + 'c'.repeat(20), name: 'Recovered project', description: '', parent_id: '', derived_from: '',
      repositories: [], task_project: '', pinned: false, archived: false, archived_at: null, archived_by: null, version: 1,
      created_by: caps.actor, created_at: 1, updated_at: 1,
      counts: {total: 0, todo: 0, doing: 0, waiting: 0, awaiting_approval: 0, done: 0, pending: 0}};
    const fixture = {code: null as string | null, rebind: 0, reset: false, connects: 0, disconnects: 0, endpoint: 'http://127.0.0.1:41001/', writes: 0};
    Object.assign(window, {isTauri: true, __recovery: fixture, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === 'native_status') return {endpoint: fixture.endpoint, configuration_source: 'managed',
        managed_recovery: fixture.code, managed_rebind: fixture.rebind, credential_available: true, connected: fixture.rebind > 0, error: null};
      if (command === 'connector_connect') {
        if (++fixture.connects === 1) throw 'fixture initial transport unavailable';
        return caps;
      }
      if (command === 'connector_disconnect') {fixture.disconnects++; return;}
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      if (command === 'connector_request') {
        const {method, path} = args.input;
        if (method === 'POST') fixture.writes++;
        if (path === '/bootstrap') return {status: 200, data: bootstrap};
        if (path === '/projects') return {status: 200, data: {projects: fixture.rebind > 1
          ? [{...project, children: []}] : []}};
        if (path.split('?')[0] === '/projects/' + project.project_id) return {status: 200, data: {
          project, path: [], sub_projects: [], work: [], work_items: [], ...(path.includes('projection=navigation') ? {projection: 'navigation'} : {})}};
        if (path.startsWith('/events')) {
          if (fixture.code) throw 'fixture transport unavailable';
          if (fixture.reset) {
            fixture.reset = false;
            return {status: 409, data: {error: {code: 'EVENT_CURSOR_RESET', reason: 'cursor_expired', resnapshot: true, preserve_drafts: true}}};
          }
          return {status: 200, data: {events: [], next_cursor: 0, head_cursor: 0, has_more: false,
            sync: {...bootstrap.sync, checkpoint: bootstrap.sync.checkpoint}}};
        }
        if (path === '/sessions/demo/session-1') return {status: 200, data: {
          session: {host: 'demo', session_id: 'session-1', title: 'Fixture session', api_access: 'managed', provenance: 'connector'}}};
        const pathname = path.split('?')[0];
        for (const [route, model] of Object.entries(bootstrap.snapshot))
          if (pathname === '/' + route.replace('_', '-')) return {status: 200, data: model};
        if (pathname === '/checkpoints') return {status: 200, data: {checkpoints: []}};
        return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Fixture read model unavailable'}}};
      }
      return {};
    }}});
  }, bootstrap);
  await page.goto('/dashboard/#/session/demo/session-1');
  await expect(page.locator('.native-connection')).toBeVisible();
  expect(await page.evaluate(() => (window as any).__recovery.disconnects)).toBe(0);
  await page.evaluate(() => Object.assign((window as any).__recovery, {rebind: 1, endpoint: 'http://127.0.0.1:41002/'}));
  const draft = page.locator('textarea').first();
  await draft.fill('Retain this exact draft across local recovery');
  const storageBefore = await page.evaluate(() => {
    const sync = Object.keys(localStorage).find(key => key.startsWith('batc.sync.'))!;
    localStorage.setItem('batc.key.' + sync.slice('batc.sync.'.length) + '.fixture-original',
      JSON.stringify({key: 'original-fixture-key', op: 'op_' + 'a'.repeat(32)}));
    return Object.entries(localStorage).filter(([key]) => key.startsWith('batc.sync.') || key.startsWith('batc.key.')).sort();
  });
  for (const [code, text] of [
    ['MANAGED_RECOVERING', locale === 'zh-TW' ? '正在重新啟動本機背景服務…' : 'Restarting local service…'],
    ['MANAGED_RUNTIME_START_TIMEOUT', locale === 'zh-TW' ? '本機背景服務恢復失敗' : 'Local service recovery failed'],
    ['MANAGED_RECOVERY_IDENTITY_CHANGED', locale === 'zh-TW' ? '本機服務身分已變更' : 'Local service identity changed'],
  ]) {
    await page.evaluate(code => { (window as any).__recovery.code = code; }, code);
    await expect(page.locator('#live')).toContainText(text, {timeout: 10000});
    await expect(page.locator('#live')).not.toContainText(code);
    await expect(page.locator('#managed-recovery-notice > p')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.locator('#managed-recovery-details > summary').click();
    await expect(page.locator('#managed-recovery-details code')).toHaveText(code);
    await page.locator('#managed-recovery-details > summary').click();
    await expect(draft).toHaveValue('Retain this exact draft across local recovery');
  }
  await page.evaluate(() => { Object.assign((window as any).__recovery, {code: null, rebind: 2, endpoint: 'http://127.0.0.1:41003/'}); });
  await expect(page.locator('#managed-recovery-details')).toBeHidden({timeout: 10000});
  await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected', {timeout: 10000});
  await page.evaluate(() => { (window as any).__recovery.reset = true; });
  await expect.poll(() => page.evaluate(() => (window as any).__recovery.reset)).toBe(false);
  await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected', {timeout: 10000});
  await expect(draft).toHaveValue('Retain this exact draft across local recovery');
  if (width < 901) await page.locator('#workspace-menu').click();
  await expect(page.locator('.workspace-tree').getByText('Recovered project', {exact: true})).toBeVisible();
  expect(await page.evaluate(() => (window as any).__recovery.writes)).toBe(0);
  expect(await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.sync.') || key.startsWith('batc.key.')).sort())).toEqual(storageBefore);
  await context.close();
});
