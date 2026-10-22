import {test, expect, type Page} from '@playwright/test';
import {readFileSync, writeFileSync} from 'node:fs';
import {STRINGS} from '../src/i18n.js';
import {conversationFixture, mountConversation} from './conversation-fixture';
import {expectHeaderFits, headerFonts, headerStatusLabels, useHeaderFont} from './header-layout';

async function baselineAssets(page: Page) {
  if (!process.env.CENTRAL_STATUS_BASELINE) return;
  for (const file of ['index.html', 'app.js', 'app.css']) await page.route(
    file === 'index.html' ? '**/dashboard/' : `**/dashboard/${file}`,
    route => route.fulfill({body: readFileSync(`${process.env.CENTRAL_STATUS_BASELINE}/${file}`),
      contentType: file.endsWith('.html') ? 'text/html' : file.endsWith('.css') ? 'text/css' : 'text/javascript'}));
}

for (const locale of ['en-US', 'zh-TW']) {
  const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
  const connected = locale === 'zh-TW' ? '已連線' : 'Connected';
  const disconnected = locale === 'zh-TW' ? '未連線' : 'Not connected';
  const scoped = locale === 'zh-TW' ? 'Connector：未連線' : 'Connector: Not connected';

  for (const [state, key, helpKey] of [
    ['not-connected', 'ui_not_connected', 'ui_not_connected_help'],
    ['connecting', 'ui_connecting', 'ui_connecting_help'],
    ['connected', 'ui_connected', 'desktop_polling'],
    ['reconnecting', 'ui_reconnecting', 'offline_actions_paused'],
  ]) test(`complete Connector title follows ${state} evidence ${locale}`, async ({browser}, info) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const fixture = conversationFixture();
    let failed = false, release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    await mountConversation(page, false, async input => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (state === 'connecting' && path === '/capabilities') await pending;
      return fixture.dispatch(input);
    });
    await page.route('**/api/v1/events?*', route => failed ? route.abort('connectionfailed') : route.fallback());
    if (state === 'not-connected' || state === 'connecting') await page.addInitScript(() => sessionStorage.clear());
    try {
      await page.goto('/dashboard/#/settings');
      if (state === 'connecting') {
        await page.locator('.global-connect-form input[type=password]').fill('fixture-token');
        await page.locator('.global-connect-form button[type=submit]').click();
      } else if (state === 'reconnecting') {
        await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected');
        failed = true;
      }
      const live = page.locator('#live');
      const name = copy.ui_connector_status.replace('{status}', copy[key]);
      await expect(live).toHaveText(copy[key]);
      await expect(live).toHaveAccessibleName(name);
      const actual = {state, locale, title: await live.getAttribute('title'),
        accessible_name: await live.getAttribute('aria-label')};
      writeFileSync(info.outputPath(`title-${state}-${locale}.json`), JSON.stringify(actual, null, 2));
      if (state === 'not-connected' || state === 'connecting')
        await expect(live).not.toHaveAttribute('title', new RegExp(copy.offline_actions_paused));
      await expect(live).toHaveAttribute('title', `${name} — ${copy[helpKey]}`);
      // Capture the settled value, including polling help after the initial read.
      actual.title = await live.getAttribute('title');
      writeFileSync(info.outputPath(`title-${state}-${locale}.json`), JSON.stringify(actual, null, 2));
    } finally { release(); await context.close(); }
  });

  test(`failed poll after success agrees on both surfaces at 390px ${locale}`, async ({browser}, info) => {
    const context = await browser.newContext({locale, viewport: {width: 390, height: 844}}), page = await context.newPage();
    const fixture = conversationFixture();
    let failed = false;
    await mountConversation(page, false, async input => failed && new URL(input.path, 'http://fixture').pathname === '/events'
      ? {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Events unavailable'}}} : fixture.dispatch(input));
    try {
      await page.goto('/dashboard/#/settings');
      await useHeaderFont(page, 'DejaVu Sans');
      await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected');
      failed = true;
      const text = copy.ui_service_error;
      await expect(page.locator('#live')).toHaveText(text);
      await expect(page.locator('.global-connection-facts .ui-pill')).toHaveText(text);
      await expect(page.locator('#live')).toHaveAccessibleName(locale === 'zh-TW' ? `Connector：${text}` : `Connector: ${text}`);
      await expect(page.locator('.central-service-scope')).toHaveText(locale === 'zh-TW' ? 'Connector：' : 'Connector:');
      expect(await page.locator('.central-service-state').evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await expectHeaderFits(page, info);
    } finally { await context.close(); }
  });

  for (const reason of ['first run', 'restored credential rejected', 'Disconnect']) {
    test(`missing session is not a reachability failure: ${reason} ${locale}`, async ({browser}) => {
      const context = await browser.newContext({locale}), page = await context.newPage();
      const fixture = conversationFixture();
      await mountConversation(page, false, async input => reason === 'restored credential rejected' && input.path === '/capabilities'
        ? {status: 401, data: {error: {code: 'UNAUTHORIZED', message: 'a valid bearer token is required'}}}
        : fixture.dispatch(input));
      if (reason === 'first run') await page.addInitScript(() => sessionStorage.clear());
      try {
        await page.goto('/dashboard/#/settings');
        if (reason === 'Disconnect') {
          await expect(page.locator('.global-connection-facts .ui-pill')).toHaveText(connected);
          await page.getByRole('button', {name: locale === 'zh-TW' ? '中斷' : 'Disconnect', exact: true}).click();
        }
        await expect(page.locator('#live')).toHaveText(disconnected);
        await expect(page.locator('#live')).toHaveAccessibleName(scoped);
        await expect(page.locator('#live')).toHaveAttribute('data-connection', 'offline');
        await expect(page.locator('#live')).toHaveAttribute('title', `${scoped} — ${copy.ui_not_connected_help}`);
        await expect(page.locator('.central-service-scope')).toBeVisible();
      } finally { await context.close(); }
    });
  }

  test(`bootstrap reachability agrees before the first event poll ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    await baselineAssets(page);
    const fixture = conversationFixture();
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    await mountConversation(page, false, async input => {
      if (new URL(input.path, 'http://fixture').pathname === '/events') await pending;
      return fixture.dispatch(input);
    });
    try {
      await page.goto('/dashboard/#/settings');
      const facts = page.locator('.global-connection-facts .ui-pill');
      await expect(facts).toHaveText(connected);
      await expect(page.locator('#live')).toHaveText(await facts.innerText());
      await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected');
    } finally { release(); await context.close(); }
  });

  for (const {width, font} of [{width: 1440, font: null}, ...headerFonts.map(font => ({width: 390, font}))])
    test(`central scope beside BAT rows ${locale} ${width}${font ? ` ${font}` : ''}`, async ({browser}, info) => {
    const context = await browser.newContext({locale, viewport: {width, height: 1000}}), page = await context.newPage();
    await baselineAssets(page);
    const doc = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));
    // This scenario asserts a current BAT observation beside an unconfigured
    // Connector; keep its display clock at the producer golden's instant.
    await page.clock.setFixedTime(new Date(doc.readiness.observed_at));
    // Native overview wraps the shared sanitized status with these control facts.
    Object.assign(doc, {control_version: 1, backend: 'rust', pending_migration: null,
      login: {show_picker: true, revision: 'f'.repeat(64)},
      profiles: [{id: 'default', label: 'Local BAT', connection: null}]});
    Object.assign(doc.selection, {profiles: [], dashboard: true});
    await page.addInitScript(doc => {
      Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
        if (command === 'native_status') return {endpoint: null, credential_available: false};
        if (command === 'fleet_availability') return {configured: true, platform_supported: true, native_controls: true};
        if (command === 'fleet_control' && args.input.action === 'overview') return structuredClone(doc);
        throw new Error(`Unexpected fixture command ${command}`);
      }}});
    }, doc);
    try {
      await page.goto('/dashboard/#/settings');
      if (font) await useHeaderFont(page, font);
      await page.getByRole('link', {name: /^(Fleet|機群)$/}).click();
      await expect(page.locator('[data-fleet-diagnosis="node-1"]')).toHaveAttribute('data-fleet-state', 'ready');
      // Kept outside Git in the selected Playwright output directory, before assertions
      // so the original misleading header is preserved as evidence too.
      await page.screenshot({path: info.outputPath(`central-fleet-${locale}-${width}.png`)});
      await expectHeaderFits(page, info, headerStatusLabels(copy));
      await expect.soft(page.locator('#live')).toHaveText(disconnected);
      await expect.soft(page.locator('#live')).toHaveAccessibleName(scoped);
      await expect.soft(page.locator('#live')).toHaveAttribute('title', `${scoped} — ${copy.ui_not_connected_help}`);
      await expect.soft(page.locator('.central-service-scope')).toBeVisible();
      await expect(page.locator('.central-service-scope')).toHaveText(locale === 'zh-TW' ? 'Connector：' : 'Connector:');
      expect(await page.locator('.central-service-state').evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    } finally { await context.close(); }
  });
}
