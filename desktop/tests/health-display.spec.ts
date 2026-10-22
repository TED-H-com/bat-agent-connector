import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {conversationFixture, mountConversation} from './conversation-fixture';
import {STRINGS} from '../src/i18n.js';
import {runtimeGolden} from './session-runtime-fixture';

const bootstrap = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));
const at = Date.parse('2026-10-08T12:00:00Z');
const fleetGolden = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));

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
        document.dispatchEvent(new Event('visibilitychange'));
      });
      await expect(page.locator('#live')).toHaveAttribute('title', new RegExp(copy.sync_actions_paused));
      await page.locator('[data-fleet-action=refresh]').click();
      await expect(page.locator('#live')).toHaveAttribute('title', new RegExp(copy.sync_actions_paused));
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
      await expect(page.locator('#installation-health .health-last-value')).toContainText(locale === 'zh-TW' ? '檢查中' : 'Checking');
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
    await page.clock.install({time: at}); await page.clock.pauseAt(at);
    await mountConversation(page, false, async input => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (path === '/bootstrap') {
        bootstraps++;
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
      await page.clock.runFor(1_000);
      await page.clock.runFor(1_000);
      await expect.poll(() => bootstraps).toBe(2);
      if (failure === 'pending') await page.clock.runFor(61_000);
      if (failure) await expect(page.locator('#installation-health [data-health-key=reload]')).toBeVisible();
      else await expect(page.locator('#installation-health [data-health-key=capabilities]')).toHaveCount(0);
      await expect(page.locator('#installation-health [data-health-selected]')).toBeVisible();
      await page.clock.runFor(10_000); expect(bootstraps).toBe(2);
      expect(await page.evaluate(() => localStorage.getItem('batc.key.fixture.health'))).toBe('retained-key');
    } finally {release(); await context.close();}
  });
}
