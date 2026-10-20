import {test, expect, type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';

// Generated and drift-checked by the real Python ApiV1.bootstrap handler.
const bootstrap = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));

async function setup(page: Page, options: {source?: string; legacy?: boolean} = {}) {
  await page.addInitScript(({golden, options}: any) => {
    const saved = JSON.parse(sessionStorage.getItem('fixture.native.identity') || '{}');
    const fixture = {endpoint: 'http://127.0.0.1:41001/', server: golden.sync.server_id,
      principal: golden.sync.principal_id, actor: golden.capabilities.actor, ...saved,
      source: 'managed', connected: false, writes: 0, connects: 0, statuses: 0, reset: false, bootstraps: 0, events: [] as string[], ...options};
    const response = () => {
      const value = structuredClone(golden);
      const identity = {server_id: fixture.server, principal_id: fixture.principal};
      Object.assign(value.sync, identity);
      Object.assign(value.capabilities, {actor: fixture.actor, identity, desktop_identity: identity});
      if (fixture.source === 'external') {delete value.capabilities.desktop_identity; delete value.capabilities.managed_installation;}
      return value;
    };
    if (options.legacy && !sessionStorage.getItem('fixture.legacy.seeded')) {
      const scope = [fixture.endpoint, fixture.server, fixture.principal, fixture.actor].map(encodeURIComponent).join(':');
      localStorage.setItem(`batc.sync.${scope}`, JSON.stringify({cursor: 1, token: 'fixture-saved-checkpoint-proof'}));
      localStorage.setItem(`batc.draft.${scope}.send.demo.session-1`, 'Recovered legacy managed draft');
      localStorage.setItem(`batc.key.${scope}.send.demo.session-1`, JSON.stringify({key: 'original-key', op: 'op_' + 'a'.repeat(32)}));
      // A durableForm envelope with its original Work/Attempt request. It is
      // retained as bytes here; this session view must never submit the PM form.
      localStorage.setItem(`batc.pm.${scope}.session.send.${JSON.stringify({workspace_id: 'ws_' + 'c'.repeat(32), work_item_id: 'wi_' + 'd'.repeat(20)})}`,
        JSON.stringify({version: 1, values: {rationale: 'Keep this request'}, intent: {key: 'original-pm-key', operation_id: 'op_' + 'b'.repeat(32),
          request: {action: 'session.send', target: {workspace_id: 'ws_' + 'c'.repeat(32), work_item_id: 'wi_' + 'd'.repeat(20)},
            params: {text: 'Keep this request', schedule: 'after_current'}, preconditions: {expected_attempt_id: 'att_' + 'e'.repeat(32)}}}}));
      sessionStorage.setItem(`batc.tree.${scope}`, JSON.stringify(['fixture-workspace']));
      sessionStorage.setItem('fixture.legacy.seeded', '1');
      fixture.endpoint = 'http://127.0.0.1:41002/';
    }
    Object.assign(window, {isTauri: true, __storage: fixture, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === 'native_status') {fixture.statuses++; return {endpoint: fixture.endpoint, expected_actor: fixture.actor, configuration_source: fixture.source,
        credential_available: true, credential_saved: true, enrollment_supported: true,
        connected: fixture.connected, managed_rebind: 0, error: null};}
      if (command === 'connector_connect' || command === 'connector_enroll') {
        fixture.connects++; fixture.connected = true;
        return {...response().capabilities, desktop_identity: {server_id: fixture.server, principal_id: fixture.principal}};
      }
      if (command === 'connector_disconnect') {fixture.connected = false; return;}
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      if (command === 'connector_request') {
        const {method, path} = args.input;
        if (method === 'POST') {fixture.writes++; throw Error('Unexpected mutation during storage recovery');}
        if (path === '/bootstrap') {fixture.bootstraps++; return {status: 200, data: response()};}
        if (path.startsWith('/events')) {
          fixture.events.push(path);
          if (fixture.reset) {fixture.reset = false; return {status: 409, data: {error: {code: 'EVENT_CURSOR_RESET',
            reason: 'cursor_expired', resnapshot: true, preserve_drafts: true}}};}
          return {status: 200, data: {events: [], next_cursor: 1, head_cursor: 1, has_more: false,
            sync: {...response().sync, checkpoint: {cursor: 1, token: 'fixture-saved-checkpoint-proof'}}}};
        }
        if (path === '/sessions/demo/session-1') return {status: 200, data: {session: {host: 'demo', session_id: 'session-1',
          title: 'Fixture session', api_access: 'managed', provenance: 'connector'}}};
        const pathname = path.split('?')[0];
        if (pathname === '/capabilities') return {status: 200, data: response().capabilities};
        for (const [route, model] of Object.entries(response().snapshot))
          if (pathname === '/' + route.replace('_', '-')) return {status: 200, data: model};
        if (pathname === '/checkpoints') return {status: 200, data: {checkpoints: []}};
        // Unsupported optional session projections return the API's normal error
        // envelope, rather than a made-up combined list response.
        return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Fixture read model unavailable'}}};
      }
      return {};
    }}});
  }, {golden: bootstrap, options});
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

  test(`one old managed port restores drafts, pending keys and exact targets without replay (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale});
    const page = await context.newPage();
    await setup(page, {legacy: true});
    await expect(page.locator('textarea').first()).toHaveValue('Recovered legacy managed draft');
    const result = await page.evaluate(() => {
      const markerKey = Object.keys(localStorage).find(key => key.startsWith('batc.storage-migration.'))!;
      const marker = JSON.parse(localStorage.getItem(markerKey)!);
      const originals = Object.entries(localStorage).filter(([key]) => key.includes(marker.source));
      return {marker, originals, copies: originals.map(([key]) => localStorage.getItem(key.replace(marker.source, marker.target))),
        oldTree: sessionStorage.getItem(`batc.tree.${marker.source}`), newTree: sessionStorage.getItem(`batc.tree.${marker.target}`),
        writes: (window as any).__storage.writes};
    });
    expect(result.marker.status).toBe('complete');
    expect(result.originals).toHaveLength(4);
    expect(result.copies).toEqual(result.originals.map(([, value]) => value));
    expect(result.newTree).toBe(result.oldTree);
    expect(result.writes).toBe(0);
    await context.close();
  });

  test(`managed relaunch A to B to A across ports isolates principal storage (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale});
    const page = await context.newPage();
    await setup(page);
    await page.locator('textarea').first().fill('Managed principal A draft');
    await changeIdentity(page, {principal: 'fixture-principal-b', endpoint: 'http://127.0.0.1:41002/'});
    await page.reload();
    await expect(page.locator('textarea').first()).toHaveValue('');
    await page.locator('textarea').first().fill('Managed principal B draft');
    await changeIdentity(page, {principal: bootstrap.sync.principal_id, endpoint: 'http://127.0.0.1:41003/'});
    await page.reload();
    await expect(page.locator('textarea').first()).toHaveValue('Managed principal A draft');
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    await context.close();
  });

  test(`disconnect then a new external port waits for verification and cannot inherit the previous binding (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale});
    const page = await context.newPage();
    await setup(page, {source: 'external'});
    await page.locator('textarea').first().fill('Original draft before disconnect');
    await page.locator('#live').click();
    await page.getByRole('button', {name: locale === 'zh-TW' ? '中斷' : 'Disconnect', exact: true}).click();
    const connect = page.getByRole('button', {name: locale === 'zh-TW' ? '連線' : 'Connect', exact: true});
    await expect(connect).toBeVisible();
    await changeIdentity(page, {endpoint: 'http://127.0.0.1:41002/'});
    const statuses = await page.evaluate(() => (window as any).__storage.statuses);
    await expect.poll(() => page.evaluate(() => (window as any).__storage.statuses)).toBeGreaterThan(statuses);
    expect(await page.evaluate(() => (window as any).__storage.connects)).toBe(1);
    await expect(page.locator('[data-connection-access]')).toHaveCount(0);
    await connect.click();
    await expect(page).toHaveURL(/#\/home$/);
    await page.evaluate(() => {location.hash = '#/session/demo/session-1';});
    await expect(page.locator('textarea').first()).toHaveValue('');
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    await changeIdentity(page, {endpoint: 'http://127.0.0.1:41001/'});
    await page.reload();
    await expect(page.locator('textarea').first()).toHaveValue('Original draft before disconnect');
    await context.close();
  });

  test(`native principal A to B to A keeps drafts and pending operation identity isolated (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale});
    const page = await context.newPage();
    await setup(page, {source: 'external'});
    await page.locator('textarea').first().fill('Principal A draft');
    await expect.poll(() => page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith('batc.sync.')))).toBe(true);
    const original = await page.evaluate(() => {
      const ns = Object.keys(localStorage).find(key => key.startsWith('batc.sync.'))!.slice('batc.sync.'.length);
      const key = `batc.key.${ns}.send.demo.session-1`, value = JSON.stringify({key: 'principal-a-original', op: 'op_' + 'a'.repeat(32)});
      localStorage.setItem(key, value); return {key, value};
    });
    const replace = async (principal: string, endpoint: string) => {
      await page.locator('#live').click();
      await changeIdentity(page, {principal, endpoint});
      await page.getByRole('button', {name: locale === 'zh-TW' ? '更換憑證' : 'Replace credential', exact: true}).click();
      await expect(page.locator('[data-connection-access]')).toBeVisible();
      await page.evaluate(() => {location.hash = '#/session/demo/session-1';});
      await expect(page.locator('textarea').first()).toBeVisible();
    };
    await replace('fixture-principal-b', 'http://127.0.0.1:41001/');
    await expect(page.locator('textarea').first()).toHaveValue('');
    await page.locator('textarea').first().fill('Principal B draft');
    await replace(bootstrap.sync.principal_id, 'http://127.0.0.1:41001/');
    await expect(page.locator('textarea').first()).toHaveValue('Principal A draft');
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.value);
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    await context.close();
  });

  test(`external central bootstrap without desktop_identity retains drafts after cursor reset (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale});
    const page = await context.newPage();
    await setup(page, {source: 'external'});
    await page.locator('textarea').first().fill('External central draft');
    await page.evaluate(() => {(window as any).__storage.reset = true;});
    await expect.poll(() => page.evaluate(() => (window as any).__storage.bootstraps)).toBeGreaterThan(1);
    await expect(page.locator('textarea').first()).toHaveValue('External central draft');
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    expect(await page.evaluate(() => Object.keys(localStorage).some(key => key.includes('managed:v1:')))).toBe(false);
    await context.close();
  });
}
