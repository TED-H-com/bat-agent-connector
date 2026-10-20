import {test, expect, type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {conversationFixture, mountConversation} from './conversation-fixture';

async function baselineAssets(page: Page) {
  if (!process.env.CENTRAL_STATUS_BASELINE) return;
  for (const file of ['index.html', 'app.js', 'app.css']) await page.route(
    file === 'index.html' ? '**/dashboard/' : `**/dashboard/${file}`,
    route => route.fulfill({body: readFileSync(`${process.env.CENTRAL_STATUS_BASELINE}/${file}`),
      contentType: file.endsWith('.html') ? 'text/html' : file.endsWith('.css') ? 'text/css' : 'text/javascript'}));
}

for (const locale of ['en-US', 'zh-TW']) {
  const connected = locale === 'zh-TW' ? '已連線' : 'Connected';
  const disconnected = locale === 'zh-TW' ? '未連線' : 'Not connected';
  const scoped = locale === 'zh-TW' ? '中央服務：未連線' : 'Central service: Not connected';

  test(`failed poll after success agrees on both surfaces at 390px ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width: 390, height: 844}}), page = await context.newPage();
    const fixture = conversationFixture();
    let failed = false;
    await mountConversation(page, false, async input => failed && new URL(input.path, 'http://fixture').pathname === '/events'
      ? {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Events unavailable'}}} : fixture.dispatch(input));
    try {
      await page.goto('/dashboard/#/settings');
      await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected');
      failed = true;
      const text = locale === 'zh-TW' ? '重新連線中' : 'Reconnecting';
      await expect(page.locator('#live')).toHaveText(text);
      await expect(page.locator('.global-connection-facts .ui-pill')).toHaveText(text);
      await expect(page.locator('#live')).toHaveAccessibleName(locale === 'zh-TW' ? `中央服務：${text}` : `Central service: ${text}`);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
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

  for (const width of [1440, 390]) test(`central scope beside BAT rows ${locale} ${width}`, async ({browser}, info) => {
    const context = await browser.newContext({locale, viewport: {width, height: 1000}}), page = await context.newPage();
    await baselineAssets(page);
    const doc = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));
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
      await page.getByRole('link', {name: /^(Fleet|機群)$/}).click();
      await expect(page.locator('[data-fleet-diagnosis="node-1"]')).toHaveAttribute('data-fleet-state', 'ready');
      // Kept outside Git in the selected Playwright output directory, before assertions
      // so the original misleading header is preserved as evidence too.
      await page.screenshot({path: info.outputPath(`central-fleet-${locale}-${width}.png`)});
      await expect.soft(page.locator('#live')).toHaveText(disconnected);
      await expect.soft(page.locator('#live')).toHaveAccessibleName(scoped);
      await expect.soft(page.locator('#live')).toHaveAttribute('title', new RegExp(scoped));
      await expect.soft(page.locator('.central-service-scope')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    } finally { await context.close(); }
  });
}
