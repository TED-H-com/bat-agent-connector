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
      await expect(count).toHaveText(copy.health_other_machines.replace('{count}', '2'));
      await count.click();
      await expect(page).toHaveURL(/section=fleet/);
      await expect(page.locator('[data-fleet-diagnosis="node-1"]')).toHaveAttribute('data-fleet-state', 'identity');
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
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
      await expect(page.locator('.global-connection-facts .ui-pill')).toHaveText(await page.locator('#live').innerText());
      await expect(page.locator('#live')).not.toHaveAttribute('data-connection', 'offline');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    } finally {release(); await context.close();}
  });
}
