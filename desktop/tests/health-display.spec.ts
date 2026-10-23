import {test, expect, type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {setTimeout as delay} from 'node:timers/promises';
import {conversationFixture, mountConversation} from './conversation-fixture';
import {STRINGS} from '../src/i18n.js';
import {runtimeGolden} from './session-runtime-fixture';
import {setup as setupManaged} from './managed-storage-fixture';

const bootstrap = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));
const at = Date.parse('2026-10-08T12:00:00Z');
const fleetGolden = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));

async function managedHealthReset(page: Page) {
  await setupManaged(page);
  await page.evaluate(row => {
    const bridge = (window as any).__TAURI_INTERNALS__, invoke = bridge.invoke;
    const checkpoint = {cursor: 5, token: 'fixture-health-refreshed-proof'};
    const reset = (window as any).__healthReset = {reason: '', waiting: false, returned: false, issued: false, refreshed: false, renewals: 0};
    const now = new Date().toISOString(), valid = new Date(Date.now() + 180_000).toISOString();
    const session = () => {
      const value = structuredClone(row);
      Object.assign(value, {host: 'demo', session_id: 'session-1', workspace: 'Original workspace', workspace_id: 'ws-1', observed_at: now});
      for (const axis of Object.values(value.state.evidence) as any[]) if (axis.observed_at) {
        axis.observed_at = now;
        if (axis.valid_until) axis.valid_until = valid;
      }
      return value;
    };
    const hosts = () => [{host: 'other', reachable: false, error: 'CONNECTION_FAILED',
      last_attempt_at: now, last_success_at: null, valid_until: null, stale: true, stale_reason: 'host_unreachable'}];
    bridge.invoke = async (command: string, args: any) => {
      if (command !== 'connector_request') return invoke(command, args);
      const path = args.input.path, pathname = path.split('?')[0];
      if (pathname === '/events' && reset.reason && (!reset.issued || reset.reason !== 'cursor_expired')) {
        reset.issued = true;
        return {status: 409, data: {error: {code: 'EVENT_CURSOR_RESET', reason: reset.reason, resnapshot: true, preserve_drafts: true}}};
      }
      if (path === '/bootstrap' && reset.issued) {
        reset.waiting = true;
        await new Promise(resolve => {reset.release = resolve;});
        const fixture = (window as any).__storage;
        if (reset.reason === 'principal_changed') fixture.principal = 'different-principal';
        if (reset.reason === 'server_changed') fixture.server = 'different-server';
        const response = await invoke(command, args);
        if (reset.reason === 'cursor_expired') {
          reset.refreshed = true;
          Object.assign(response.data.sync, {checkpoint, head_cursor: checkpoint.cursor});
        }
        reset.returned = true;
        return response;
      }
      if (pathname === '/sessions') return {status: 200, data: {sessions: [session()], hosts: hosts(), next_cursor: null}};
      if (pathname === '/hosts') return {status: 200, data: {hosts: hosts()}};
      if (pathname === '/sessions/demo/session-1') {
        if (reset.refreshed) reset.renewals++;
        return {status: 200, data: {session: session()}};
      }
      const response = await invoke(command, args);
      if (pathname === '/events' && reset.refreshed) Object.assign(response.data, {next_cursor: checkpoint.cursor,
        head_cursor: checkpoint.cursor, sync: {...response.data.sync, checkpoint}});
      return response;
    };
  }, runtimeGolden.ended_runtime.listed);
  await page.evaluate(() => {location.hash = '#/sessions';});
  await expect(page.locator('.session-inventory a.title')).toHaveCount(1);
  await page.evaluate(() => {location.hash = '#/session/demo/session-1';});
  await expect(page.locator('#installation-health [data-health-selected]')).toContainText('Original workspace');
  await expect(page.locator('#installation-health [data-health-compact]')).toHaveAttribute('data-health-compact', 'idle');
  await expect(page.locator('#installation-health [data-health-other]')).toContainText('1');
  await page.locator('#main textarea').first().fill('Keep the original draft');
  await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected');
}

for (const locale of ['en-US', 'zh-TW']) {
  test(`capability verification retains a refused native replacement notice ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage(), fixture = conversationFixture();
    const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
    const refusal = 'Fixture identity mismatch; previous credential retained';
    let changed = false, bootstraps = 0;
    await mountConversation(page, true, async input => {
      const result = structuredClone(await fixture.dispatch(input));
      if (input.path === '/bootstrap') bootstraps++;
      const caps = input.path === '/capabilities' ? result.data : input.path === '/bootstrap' ? result.data.capabilities : null;
      if (caps && changed) caps.connector = '0.2.99';
      return result;
    });
    await page.addInitScript(refusal => {
      const bridge = (window as any).__TAURI_INTERNALS__, invoke = bridge.invoke;
      bridge.invoke = (command: string, args: any) => {
        if (command === 'native_status') return Promise.resolve({endpoint: 'https://central.example/', expected_actor: 'reader',
          credential_available: true, credential_saved: true, enrollment_supported: true, connected: true, configuration_source: 'external'});
        if (command === 'connector_enroll') throw new Error(refusal);
        return invoke(command, args);
      };
    }, refusal);
    try {
      await page.goto('/dashboard/#/settings');
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      await page.getByRole('button', {name: copy.desktop_replace_credential, exact: true}).click();
      await expect(page.locator('.native-connection [data-api-error]')).toContainText(refusal);
      changed = true;
      await page.evaluate(() => {location.hash = '#/home';});
      await expect(page.locator('.native-connection')).toHaveCount(0);
      await page.evaluate(() => {location.hash = '#/settings';});
      await expect.poll(() => bootstraps).toBe(2);
      await expect(page.locator('#installation-health [data-health-key=capabilities]')).toHaveCount(0);
      await page.evaluate(() => {location.hash = '#/home';});
      await expect(page.locator('.native-connection')).toHaveCount(0);
      await page.evaluate(() => {location.hash = '#/settings';});
      await expect(page.locator('.native-connection [data-api-error]')).toContainText(refusal);
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      expect(fixture.data.writes).toEqual([]);
    } finally {await context.close();}
  });

  for (const reason of ['principal_changed', 'server_changed', 'cursor_expired']) {
    test(`health follows the identity boundary during ${reason} ${locale}`, async ({browser}) => {
      const context = await browser.newContext({locale}), page = await context.newPage();
      try {
        await managedHealthReset(page);
        await page.evaluate(reason => {(window as any).__healthReset.reason = reason;}, reason);
        await expect.poll(() => page.evaluate(() => (window as any).__healthReset.waiting)).toBe(true);
        const retained = reason === 'cursor_expired', summary = page.locator('#installation-health');
        const check = async (completed = false) => {
          if (retained) {
            await expect.soft(summary.locator('[data-health-selected]')).toContainText('Original workspace');
            await expect.soft(summary.locator('[data-health-other]')).toContainText('1');
            if (!completed) await expect.soft(summary.locator('[data-health-compact]')).toHaveAttribute('data-health-compact', 'idle');
            await expect.soft(page.locator('#main textarea').first()).toHaveValue('Keep the original draft');
          } else {
            // Withdrawal must precede the held bootstrap, not wait for a display-clock tick.
            expect.soft(await summary.locator('[data-health-selected], [data-health-other], [data-health-compact], .health-facets').count()).toBe(0);
            expect.soft(await summary.textContent()).not.toContain('Original workspace');
            await expect.soft(page.locator('#live')).not.toHaveAttribute('data-connection', 'connected');
          }
          expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
        };
        await check();
        await page.evaluate(() => (window as any).__healthReset.release());
        await expect.poll(() => page.evaluate(() => (window as any).__healthReset.returned)).toBe(true);
        if (retained) {
          await expect.poll(() => page.evaluate(() => (window as any).__storage.events.some((path: string) =>
            path.includes('checkpoint=fixture-health-refreshed-proof')))).toBe(true);
          // Count all ordinary view/renewal reads, not only the health clock's GET.
          await expect.poll(() => page.evaluate(() => (window as any).__healthReset.renewals)).toBeGreaterThan(0);
        } else await expect(page.locator('#main')).toHaveText(locale === 'zh-TW' ? '等待更新，草稿已保留' : 'Waiting to refresh · draft preserved');
        await check(true);
      } finally {await context.close();}
    });
  }

  for (const status of [401, 403]) test(`credential refusal and Retry repaint health ${locale} ${status}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage(), fixture = conversationFixture();
    const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
    let refused = false, hold = false, waiting = false, release!: () => void;
    const held = new Promise<void>(resolve => {release = resolve;});
    await mountConversation(page, false, async input => {
      if (input.path.startsWith('/events') && refused) return {status, data: {error: {code: status === 401 ? 'UNAUTHORIZED' : 'FORBIDDEN'}}};
      if (input.path === '/capabilities' && hold) {waiting = true; await held;}
      return fixture.dispatch(input);
    });
    try {
      await page.goto('/dashboard/#/session/demo/session-1');
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      await expect(page.locator('#installation-health [data-health-selected]')).toBeVisible();
      refused = true;
      await expect(page.locator('#live')).toHaveText(copy.ui_connection_refused);
      await expect(page.locator('#live')).not.toHaveAttribute('data-connection', 'connected');
      await page.evaluate(() => {location.hash = '#/settings';});
      hold = true;
      await page.getByRole('button', {name: copy.connection_retry_remembered, exact: true}).click();
      await expect.poll(() => waiting).toBe(true);
      await expect(page.locator('#installation-health [data-health-selected], #installation-health [data-health-other]')).toHaveCount(0);
      await expect(page.locator('#live')).not.toHaveAttribute('data-connection', 'connected');
      refused = false; release();
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      expect(fixture.data.writes).toEqual([]);
    } finally {release(); await context.close();}
  });
}

for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) {
  test(`other machines stay separate from the selected Connector ${locale} ${width}`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}}), page = await context.newPage();
    const fixture = conversationFixture();
    const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
    const doc = structuredClone(fleetGolden);
    for (const row of doc.readiness.hosts.slice(0, 2)) Object.assign(row, {level: 'down', blocking: 'auth', code: 'BAT_AUTH_REFUSED',
      layers: {tunnel: true, tls: true, auth: 'refused', bat: false, workspace: false}});
    await page.clock.install({time: at});
    await page.clock.pauseAt(at);
    await mountConversation(page, true, fixture.dispatch);
    await page.addInitScript(doc => {
      const original = (window as any).__TAURI_INTERNALS__.invoke;
      (window as any).__TAURI_INTERNALS__.invoke = async (command: string, args: any) => {
        if (command === 'fleet_availability') return {configured: true, platform_supported: true};
        if (command === 'fleet_request' && args.input.action === 'status') return structuredClone(doc);
        return original(command, args);
      };
    }, doc);
    try {
      await page.goto('/dashboard/#/settings');
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      const count = page.locator('#installation-health [data-health-other]');
      await expect(count).toHaveText(copy.health_other_machines_many.replace('{count}', '2'));
      await count.click();
      await expect(page).toHaveURL(/section=fleet/);
      await expect(page.locator('[data-fleet-diagnosis="node-1"]')).toHaveAttribute('data-fleet-state', 'identity');
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      await page.evaluate(() => {
        Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
        Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
        document.dispatchEvent(new Event('visibilitychange'));
      });
      await expect(page.locator('#live')).toHaveAttribute('title', new RegExp(copy.sync_waiting));
      await page.locator('[data-fleet-action=refresh]').click();
      await expect(page.locator('#live')).toHaveAttribute('title', new RegExp(copy.sync_waiting));
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    } finally {await context.close();}
  });

  test(`selected work keeps attention above output and stale ${locale} ${width}`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}}), page = await context.newPage();
    const fixture = conversationFixture(), session = runtimeGolden.stale_pending.listed;
    await page.clock.install({time: Date.parse(session.observed_at)});
    await mountConversation(page, false, async input => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (path === '/sessions/fixture-host/fixture-session') return {status: 200, data: {session}};
      return fixture.dispatch(input);
    });
    try {
      await page.goto('/dashboard/#/session/fixture-host/fixture-session');
      await expect(page.locator('#installation-health [data-health-compact]')).toHaveAttribute('data-health-compact', 'attention');
      await expect(page.locator('#installation-health .health-last-value')).not.toContainText(locale === 'zh-TW' ? '檢查中' : 'Checking');
      await expect(page.locator('#installation-health [data-health-checking]')).toHaveText(locale === 'zh-TW' ? '檢查中' : 'Checking');
      await page.goto('/dashboard/#/settings');
      await expect(page.locator('[data-health-settings] [data-health-compact]')).toHaveAttribute('data-health-compact', 'attention');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    } finally {await context.close();}
  });

  test(`header and Settings age the same observation ${locale} ${width}`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}}), page = await context.newPage();
    const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
    const fixture = conversationFixture();
    let release!: () => void;
    const held = new Promise<void>(resolve => {release = resolve;});
    await page.clock.install({time: at});
    await page.clock.pauseAt(at);
    await mountConversation(page, false, async input => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (path === '/events') await held;
      if (path === '/bootstrap') return {status: 200, data: bootstrap};
      if (path === '/capabilities') return {status: 200, data: bootstrap.capabilities};
      return fixture.dispatch(input);
    });
    try {
      await page.goto('/dashboard/#/settings');
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      await page.clock.runFor(59_000);
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      await page.clock.runFor(2_000);
      await expect(page.locator('#live')).toContainText(locale === 'zh-TW' ? '檢查中' : 'Checking');
      await expect(page.locator('#live')).toHaveAttribute('data-freshness', 'stale');
      await expect(page.locator('#live')).toContainText(copy.ui_connected);
      await expect(page.locator('.global-connection-facts .ui-pill')).toContainText(copy.ui_connected);
      await expect(page.locator('#live')).not.toContainText(locale === 'zh-TW' ? '分鐘前' : 'min ago');
      await expect(page.locator('#installation-health [data-health-age]')).toContainText(locale === 'zh-TW' ? '分鐘前' : 'min ago');
      await expect(page.locator('#live')).not.toHaveAttribute('data-connection', 'offline');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    } finally {release(); await context.close();}
  });
}

for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 1440]) {
  test(`wrapped health summary keeps the session composer reachable ${locale} ${width}`, async ({browser}) => {
    const {openSession, fromRequirements} = await import('./session-fixture');
    const s = await openSession(browser, {locale: locale === 'zh-TW' ? 'zh-TW' : 'en', width, height: 844});
    s.overrides.push(async (input, url) => {
      if (url.pathname !== `/sessions/${s.ids.host}/${s.ids.sessions.review}`) return;
      const result = await s.fixture.dispatch(input);
      result.data.session.workspace = 'A workspace with a deliberately long display name for wrapping';
      return result;
    });
    try {
      await fromRequirements(s.page, s.ids, 'review');
      await expect(s.page.locator('#installation-health')).toBeVisible();
      if (width === 390) expect((await s.page.locator('#installation-health').boundingBox())!.height).toBeGreaterThan(48);
      await expect(s.page.getByRole('button', {name: locale === 'zh-TW' ? '送出' : 'Send', exact: true})).toBeInViewport({ratio: 1});
      await expect(s.page.getByRole('banner')).toHaveCount(1);
    } finally {await s.context.close();}
  });
}

test('a held first session read does not prevent the display clock from starting', async ({page}) => {
  await page.clock.install({time: at}); await page.clock.pauseAt(at);
  const fixture = conversationFixture(); let release!: () => void;
  const held = new Promise<void>(resolve => {release = resolve;});
  await mountConversation(page, false, async input => {
    if (input.path === '/sessions/demo/session-1') await held;
    return fixture.dispatch(input);
  });
  try {
    await page.goto('/dashboard/#/session/demo/session-1');
    await expect(page.locator('#live')).toHaveAttribute('data-freshness', 'current');
    await page.clock.runFor(61_000);
    await expect(page.locator('#live')).toHaveAttribute('data-freshness', 'stale');
  } finally {release();}
});

for (const locale of ['en-US', 'zh-TW']) {
  test(`quiet inventory renews its declared validity once without a BAT probe ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const fixture = conversationFixture(), reads: string[] = [];
    let stamp = at, failed = false;
    const row = () => {
      const session = structuredClone(runtimeGolden.ended_runtime.listed);
      Object.assign(session, {host: 'demo', session_id: 'session-1', workspace_id: 'workspace-fixture', workspace: 'Fixture workspace'});
      for (const axis of ['activity', 'connection']) Object.assign(session.state.evidence[axis], {
        observed_at: new Date(stamp).toISOString(), valid_until: new Date(stamp + 180_000).toISOString()});
      return session;
    };
    await page.clock.install({time: at}); await page.clock.pauseAt(at);
    await mountConversation(page, false, async input => {
      const url = new URL(input.path, 'http://fixture');
      if (url.pathname === '/hosts') return {status: 200, data: {hosts: []}};
      if (url.pathname === '/sessions' || url.pathname === '/sessions/demo/session-1') {
        reads.push(input.path);
        if (failed) return {status: 503, data: {error: {code: 'TEMPORARY_UNAVAILABLE', message: 'Fixture read unavailable'}}};
        return {status: 200, data: url.pathname === '/sessions' ? {sessions: [row()], next_cursor: null} : {session: row()}};
      }
      return fixture.dispatch(input);
    });
    try {
      await page.goto('/dashboard/#/sessions');
      await expect.poll(() => reads.some(path => path.startsWith('/sessions?'))).toBe(true);
      await page.evaluate(() => {location.hash = '#/session/demo/session-1';});
      const pill = page.locator('#installation-health [data-health-compact]');
      await expect(pill).toHaveAttribute('data-health-compact', 'idle');
      const initial = reads.length;
      await page.clock.runFor(61_000); await expect(pill).toHaveAttribute('data-health-compact', 'idle');
      expect(reads.length).toBe(initial);
      stamp = at + 180_000;
      await page.clock.runFor(120_000);
      await expect(pill).toHaveAttribute('data-health-compact', 'idle');
      expect(reads.length).toBe(initial + 1);
      failed = true;
      await page.clock.runFor(180_000);
      await expect(pill).toHaveAttribute('data-health-compact', 'stale');
      await expect(page.locator('.health-last-value').first()).toContainText('TEMPORARY_UNAVAILABLE');
      const afterFailure = reads.length;
      await page.clock.runFor(60_000); expect(reads.length).toBe(afterFailure);
    } finally {await context.close();}
  });

  test(`health links retain focus and the live label does not announce age ticks ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const fixture = conversationFixture(), doc = structuredClone(fleetGolden);
    Object.assign(doc.readiness.hosts[0], {level: 'down', blocking: 'auth', code: 'BAT_AUTH_REFUSED'});
    await page.clock.install({time: at}); await page.clock.pauseAt(at);
    await mountConversation(page, true, async input => {
      const result = structuredClone(await fixture.dispatch(input));
      const caps = input.path === '/capabilities' ? result.data : input.path === '/bootstrap' ? result.data.capabilities : null;
      if (caps) caps.managed_installation = bootstrap.capabilities.managed_installation;
      return result;
    });
    await page.addInitScript(doc => {
      const original = (window as any).__TAURI_INTERNALS__.invoke;
      (window as any).__TAURI_INTERNALS__.invoke = async (command, args) => {
        if (command === 'fleet_availability') return {configured: true, platform_supported: true};
        if (command === 'fleet_request' && args.input.action === 'status') return structuredClone(doc);
        return original(command, args);
      };
    }, doc);
    try {
      await page.goto('/dashboard/#/settings?section=connection');
      const link = page.locator('[data-health-settings] a[data-health-other]');
      await page.locator('[data-setup-advanced] > summary').click();
      await expect(link).toBeVisible(); await link.focus();
      await page.evaluate(() => {
        (window as any).healthFocused = document.activeElement;
        (window as any).healthFocusEvents = 0;
        document.addEventListener('focusin', () => (window as any).healthFocusEvents++);
      });
      await page.clock.runFor(62_000);
      expect(await page.evaluate(() => document.activeElement === (window as any).healthFocused)).toBe(true);
      expect(await page.evaluate(() => (window as any).healthFocusEvents)).toBe(0);
      const live = await page.locator('#live').textContent();
      await page.clock.runFor(2_000); await expect(page.locator('#live')).toHaveText(live!);
      const headerLink = page.locator('#installation-health a[data-health-other]');
      await headerLink.focus();
      await page.evaluate(() => {(window as any).healthFocusEvents = 0;});
      await page.clock.runFor(60_000);
      await expect(headerLink).toBeFocused();
      expect(await page.evaluate(() => (window as any).healthFocusEvents)).toBe(0);
    } finally {await context.close();}
  });

  for (const failure of [false, true, 'identity', 'pending']) test(`capability changes verify once or offer reload ${locale} ${failure}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const fixture = conversationFixture(); let changed = false, bootstraps = 0;
    let release!: () => void; const held = new Promise<void>(resolve => {release = resolve;});
    let finishTicks!: () => void; const ticksFinished = new Promise<void>(resolve => {finishTicks = resolve;});
    // pauseAt installs the clock too, without a running install-to-pause gap.
    await page.clock.pauseAt(at);
    await mountConversation(page, false, async input => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (path === '/bootstrap') {
        bootstraps++;
        // Keep the verification reply behind the test's initial display ticks.
        // This is a real Node timer, independent of the paused page clock.
        if (bootstraps > 1) {await ticksFinished; await delay(1_500);}
        if (failure === 'pending' && bootstraps > 1) await held;
        if (failure === true && bootstraps > 1) return {status: 503, data: {error: {code: 'TEMPORARY_UNAVAILABLE'}}};
      }
      const result = structuredClone(await fixture.dispatch(input));
      if (changed && path === '/capabilities') result.data.connector = '0.2.99';
      if (changed && path === '/bootstrap') {
        result.data.capabilities.connector = '0.2.99';
        if (failure === 'identity') result.data.sync.principal_id = 'another-principal';
      }
      return result;
    });
    try {
      await page.goto('/dashboard/#/session/demo/session-1');
      await expect(page.locator('#installation-health [data-health-selected]')).toBeVisible();
      await page.evaluate(() => localStorage.setItem('batc.key.fixture.health', 'retained-key'));
      changed = true;
      await page.evaluate(() => {location.hash = '#/settings?section=connection';});
      await expect(page.locator('.global-connection-facts')).toBeVisible();
      const verification = failure === 'pending' ? null : page.waitForResponse('**/api/v1/bootstrap');
      await page.clock.runFor(1_000);
      await page.clock.runFor(1_000);
      finishTicks();
      if (failure === 'pending') {
        const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
        await expect.poll(async () => {
          await page.clock.runFor(1_000);
          return page.locator('#installation-health [data-health-key=capabilities]').allTextContents();
        }).toEqual([copy.health_capabilities_changed]);
        const checking = page.locator('#installation-health [data-health-checking]');
        await expect(checking).toHaveCount(1);
        await expect(checking).toHaveText(copy.health_checking);
        await page.clock.runFor(61_000);
      } else await (await verification!).finished();
      // Verification applies on receipt; the health strip paints on its 1 s tick.
      // Keep ticking until the shell displays the applied result, not a request count.
      await expect.poll(async () => {
        await page.clock.runFor(1_000);
        return failure ? page.locator('#installation-health [data-health-key=reload]').isVisible()
          : await page.locator('#installation-health [data-health-key=capabilities]').count() === 0;
      }).toBe(true);
      if (failure) await expect(page.locator('#installation-health [data-health-key=reload]')).toBeVisible();
      else await expect(page.locator('#installation-health [data-health-key=capabilities]')).toHaveCount(0);
      await expect(page.locator('#installation-health [data-health-selected]')).toBeVisible();
      await page.clock.runFor(10_000); expect(bootstraps).toBe(2);
      expect(await page.evaluate(() => localStorage.getItem('batc.key.fixture.health'))).toBe('retained-key');
    } finally {finishTicks(); release(); await context.close();}
  });
}
