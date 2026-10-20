import {test, expect, type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';

// Generated and drift-checked by the real Python ApiV1.bootstrap handler.
const bootstrap = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));

async function setup(page: Page) {
  await page.addInitScript((golden: any) => {
    const saved = JSON.parse(sessionStorage.getItem('fixture.native.identity') || '{}');
    const fixture = {endpoint: 'http://127.0.0.1:41001/', server: golden.sync.server_id,
      principal: golden.sync.principal_id, actor: golden.capabilities.actor, ...saved,
      connected: false, writes: 0, events: [] as string[]};
    const response = () => {
      const value = structuredClone(golden);
      const identity = {server_id: fixture.server, principal_id: fixture.principal};
      Object.assign(value.sync, identity);
      Object.assign(value.capabilities, {actor: fixture.actor, identity, desktop_identity: identity});
      return value;
    };
    Object.assign(window, {isTauri: true, __storage: fixture, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === 'native_status') return {endpoint: fixture.endpoint, configuration_source: 'managed',
        credential_available: true, connected: fixture.connected, managed_rebind: 0, error: null};
      if (command === 'connector_connect') {fixture.connected = true; return response().capabilities;}
      if (command === 'connector_disconnect') {fixture.connected = false; return;}
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      if (command === 'connector_request') {
        const {method, path} = args.input;
        if (method === 'POST') {fixture.writes++; throw Error('Unexpected mutation during storage recovery');}
        if (path === '/bootstrap') return {status: 200, data: response()};
        if (path.startsWith('/events')) {
          fixture.events.push(path);
          return {status: 200, data: {events: [], next_cursor: 1, head_cursor: 1, has_more: false,
            sync: {...response().sync, checkpoint: {cursor: 1, token: 'fixture-saved-checkpoint-proof'}}}};
        }
        if (path === '/sessions/demo/session-1') return {status: 200, data: {session: {host: 'demo', session_id: 'session-1',
          title: 'Fixture session', api_access: 'managed', provenance: 'connector'}}};
        return {status: 200, data: {messages: [], checkpoints: [], sessions: [], hosts: [], projects: [], operations: [], work_items: []}};
      }
      return {};
    }}});
  }, bootstrap);
  await page.goto('/dashboard/#/session/demo/session-1');
  await expect(page.locator('textarea').first()).toBeVisible();
}

async function changeIdentity(page: Page, identity: Record<string, string>) {
  await page.evaluate(identity => {
    Object.assign((window as any).__storage, identity);
    const {endpoint, server, principal, actor} = (window as any).__storage;
    sessionStorage.setItem('fixture.native.identity', JSON.stringify({endpoint, server, principal, actor}));
  }, identity);
}

for (const locale of ['en-US', 'zh-TW']) {
  test(`managed port change and app relaunch restore the verified identity storage (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale});
    const page = await context.newPage();
    await setup(page);
    await page.locator('textarea').first().fill('Keep my original managed draft');
    await expect.poll(() => page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith('batc.sync.')))).toBe(true);
    const before = await page.evaluate(() => {
      const namespace = Object.keys(localStorage).find(key => key.startsWith('batc.sync.'))!.slice('batc.sync.'.length);
      localStorage.setItem(`batc.key.${namespace}.fixture-original`, JSON.stringify({key: 'original-fixture-key', op: 'op_' + 'a'.repeat(32)}));
      localStorage.setItem(`batc.pm.${namespace}.fixture-request`, JSON.stringify({intent: {key: 'original-pm-key', operation_id: 'op_' + 'b'.repeat(32)}}));
      return {namespace, entries: Object.entries(localStorage).sort()};
    });
    await changeIdentity(page, {endpoint: 'http://127.0.0.1:41002/'});
    await page.reload();
    await expect(page.locator('textarea').first()).toHaveValue('Keep my original managed draft');
    await expect.poll(() => page.evaluate(() => (window as any).__storage.events.length)).toBeGreaterThan(0);
    const after = await page.evaluate(() => ({entries: Object.entries(localStorage).sort(), fixture: (window as any).__storage}));
    expect(after.entries).toEqual(before.entries);
    expect(new URL(after.fixture.events[0], 'https://fixture.example').searchParams.get('after')).toBe('1');
    expect(new URL(after.fixture.events[0], 'https://fixture.example').searchParams.get('checkpoint')).toBe('fixture-saved-checkpoint-proof');
    expect(after.fixture.writes).toBe(0);
    await context.close();
  });
}
