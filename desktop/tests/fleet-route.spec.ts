import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
const fixture = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));
for (const locale of ['en-US', 'zh-TW']) test.describe(`Fleet route ${locale}`, () => {
  test.use({locale});
  async function mount(page: any, elapsed: number, down = false) {
    await page.goto('/ui-components.html');
    await page.evaluate(async ({fixture, locale, elapsed, down}) => {
      const {fleetStatus} = await import('/src/fleet-status.js');
      const {h} = await import('/src/ui/dom.js');
      const {t, setLocale} = await import('/src/i18n.js');
      setLocale(locale === 'en-US' ? 'en' : locale);
      const row = structuredClone(fixture.readiness.hosts[0]);
      if (down) Object.assign(row, {level: 'down', code: 'TUNNEL_DOWN', blocking: 'tunnel',
        layers: {tunnel: false, tls: false, auth: 'unknown', bat: false, workspace: false}});
      document.getElementById('fixture')!.append(fleetStatus(row, {h, t, snapshot: fixture,
        now: Date.parse(fixture.readiness.observed_at) + elapsed, readable: true,
        entry: fixture.configuration.connections[0], detailsState: new Map()}));
    }, {fixture, locale, elapsed, down});
  }
  test('current route from the producer golden', async ({page}) => {
    await mount(page, 0);
    await expect(page.locator('[data-fleet-route]')).toHaveText(locale === 'en-US'
      ? 'Route: Tailscale direct' : '路徑：Tailscale 直連');
  });
  test('at the route deadline, retain its value and age with Checking', async ({page}) => {
    await mount(page, 60_000);
    await expect(page.locator('[data-fleet-route]')).toHaveText(locale === 'en-US'
      ? 'Route: Tailscale direct · 1 min ago · Checking' : '路徑：Tailscale 直連 · 1 分鐘前 · 檢查中');
  });
  test('down readiness keeps its wording without route reassurance', async ({page}) => {
    await mount(page, 0, true);
    await expect(page.locator('[data-fleet-route]')).toHaveCount(0);
    await expect(page.locator('.fleet-diagnosis-title')).toHaveText(locale === 'en-US'
      ? 'This machine cannot be reached' : '目前連不到這台機器');
  });
});
