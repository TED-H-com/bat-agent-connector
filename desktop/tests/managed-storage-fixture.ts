import {expect, type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';

// Generated and drift-checked by the real Python ApiV1.bootstrap handler.
export const bootstrap = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));

export async function setup(page: Page, options: {source?: string; legacy?: boolean; legacyCursors?: number[]} = {}) {
  await page.addInitScript(({golden, options}: any) => {
    const saved = JSON.parse(sessionStorage.getItem('fixture.native.identity') || '{}');
    const fixture = {endpoint: 'http://127.0.0.1:41001/', server: golden.sync.server_id,
      principal: golden.sync.principal_id, actor: golden.capabilities.actor,
      source: 'managed', connected: false, writes: 0, connects: 0, statuses: 0, reset: false, bootstraps: 0, events: [] as string[], ...options, ...saved};
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
        JSON.stringify({version: 1, values: {text: 'Keep this request'}, intent: {key: 'original-pm-key', operation_id: 'op_' + 'b'.repeat(32),
          request: {action: 'session.send', target: {workspace_id: 'ws_' + 'c'.repeat(32), work_item_id: 'wi_' + 'd'.repeat(20)},
            params: {text: 'Keep this request', schedule: 'after_current'}, preconditions: {expected_attempt_id: 'att_' + 'e'.repeat(32)}}}}));
      sessionStorage.setItem(`batc.tree.${scope}`, JSON.stringify(['fixture-workspace']));
      if (options.legacyCursors) {
        const entries = Object.entries(localStorage), tree = sessionStorage.getItem(`batc.tree.${scope}`)!;
        options.legacyCursors.forEach((cursor: number, index: number) => {
          const old = [`http://127.0.0.1:${41001 + index}/`, fixture.server, fixture.principal, fixture.actor].map(encodeURIComponent).join(':');
          for (const [key, value] of entries) localStorage.setItem(key.replace(scope, old), value);
          localStorage.setItem(`batc.sync.${old}`, JSON.stringify({cursor, token: 'fixture-saved-checkpoint-proof'}));
          localStorage.setItem(`batc.draft.${old}.send.demo.session-1`, `Retained port ${41001 + index} draft`);
          sessionStorage.setItem(`batc.tree.${old}`, tree);
        });
        Object.assign(fixture, {legacyEntries: Object.entries(localStorage).sort(),
          legacyTabs: Object.entries(sessionStorage).filter(([key]) => key.startsWith('batc.tree.')).sort()});
      }
      sessionStorage.setItem('fixture.legacy.seeded', '1');
      fixture.endpoint = `http://127.0.0.1:${41001 + (options.legacyCursors?.length || 1)}/`;
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

export async function changeIdentity(page: Page, identity: Record<string, string>) {
  await page.evaluate(identity => {
    Object.assign((window as any).__storage, identity);
    const {endpoint, server, principal, actor, source} = (window as any).__storage;
    sessionStorage.setItem('fixture.native.identity', JSON.stringify({endpoint, server, principal, actor, source}));
  }, identity);
}
