import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
const requirements = JSON.parse(readFileSync(new URL('./fixtures/fleet-requirement.golden.json', import.meta.url), 'utf8'));
const pending = JSON.parse(readFileSync(new URL('./fixtures/fleet-pending.golden.json', import.meta.url), 'utf8'));
const stops = JSON.parse(readFileSync(new URL('./fixtures/fleet-stop.golden.json', import.meta.url), 'utf8'));
const words = {
  en: ['Only Tailscale direct is configured. The peer evidence is unknown.',
    'Only Tailscale direct is configured. A direct peer connection was not confirmed.',
    'Only Tailscale direct is configured. The direct TCP check did not succeed.',
    'None of the configured routes met the connection requirements.'],
  'zh-TW': ['僅設定 Tailscale 直連。目前的 peer 證據未知。',
    '僅設定 Tailscale 直連。尚未確認 peer 的直連連線。',
    '僅設定 Tailscale 直連。直連 TCP 檢查未成功。',
    '所有已設定的路徑都未滿足連線條件。'],
};
for (const locale of ['en', 'zh-TW']) test.describe(`Fleet evidence ${locale}`, () => {
  test.use({locale: locale === 'en' ? 'en-US' : locale});
  async function mount(page: any, snapshot: any, elapsed = 0) {
    await page.goto('/ui-components.html');
    await page.evaluate(async ({snapshot, locale, elapsed}) => {
      const {fleetStatus, paintFleetHealth} = await import('/src/fleet-status.js');
      const {h} = await import('/src/ui/dom.js');
      const {t, setLocale} = await import('/src/i18n.js');
      setLocale(locale);
      const now = Date.parse(snapshot.readiness.observed_at) + elapsed;
      const root = document.getElementById('fixture')!;
      for (const row of snapshot.readiness.hosts) {
        const entry = snapshot.configuration.connections.find((entry: any) => entry.name === row.name);
        root.append(fleetStatus(row, {h, t, snapshot, now, readable: true, entry, detailsState: new Map()}));
      }
      paintFleetHealth(root, {t, snapshot, readable: true, now});
    }, {snapshot, locale, elapsed});
  }
  test('the producer explains direct-only and exhausted routes while another host stays connected', async ({page}) => {
    for (const [name, index] of [['missing', 0], ['unknown', 0], ['idle', 1], ['tcp', 2], ['exhausted', 3]] as const) {
      await mount(page, requirements[name]);
      await expect(page.locator('[data-fleet-route-requirement="node-1"]')).toHaveText(words[locale][index]);
      await expect(page.locator('[data-fleet-route="node-1"]')).toHaveCount(0);
      await expect(page.locator('[data-fleet-diagnosis="node-2"]')).toHaveAttribute('data-fleet-state', 'ready');
      await expect(page.locator('[data-fleet-route-requirement="node-2"]')).toHaveCount(0);
    }
  });
  test('unresolved stop survives age, deselection, clock paint and repeated reads', async ({page}) => {
    for (const snapshot of Object.values(stops)) {
      for (let read = 0; read < 2; read++) {
        await mount(page, snapshot, 70_000);
        const row = page.locator('[data-fleet-diagnosis="node-1"]');
        await expect(row).toHaveAttribute('data-fleet-state', 'ownership');
        await expect(row.locator('.fleet-diagnosis-title')).toHaveText(locale === 'en'
          ? 'The background connection outcome is unconfirmed · Checking' : '背景連線的操作結果尚未確認 · 檢查中');
        await row.locator('summary').click();
        await expect(row.locator('code').filter({hasText: 'STOP_UNCONFIRMED'})).toBeVisible();
        await expect(row.locator('[data-check-state="passed"]')).toHaveCount(0);
      }
    }
  });
  test('aged route failure withdraws its explanation with the code', async ({page}) => {
    await mount(page, requirements.aged, 60_001);
    await expect(page.locator('[data-fleet-diagnosis="node-1"]')).toHaveAttribute('data-fleet-state', 'stale');
    await expect(page.locator('[data-fleet-route-requirement="node-1"]')).toHaveCount(0);
    const row = page.locator('[data-fleet-diagnosis="node-1"]');
    for (const text of words[locale]) await expect(row).not.toContainText(text);
    await row.locator('summary').click();
    await expect(row.locator('code').filter({hasText: 'PROBE_STALE'})).toBeVisible();
    await expect(row.locator('[data-check-state="passed"]')).toHaveCount(0);
  });
  test('between-attempts rows show Checking and the last failed selection', async ({page}) => {
    for (const [name, index] of [['unknown', 0], ['tcp', 2], ['exhausted', 3]] as const) {
      await mount(page, pending[name].pending);
      const row = page.locator('[data-fleet-diagnosis="node-1"]');
      await expect(row).toHaveAttribute('data-fleet-state', 'checking');
      await expect(row.locator('.fleet-diagnosis-title')).toHaveText(locale === 'en' ? 'unknown · Checking' : '未知 · 檢查中');
      await expect(row.locator('[data-fleet-route-requirement="node-1"]')).toHaveText(words[locale][index]);
      await expect(page.locator('[data-fleet-diagnosis="node-2"]')).toHaveAttribute('data-fleet-state', 'ready');
    }
  });
  test('expiry withdraws between-attempts explanations', async ({page}) => {
    for (const [name, index] of [['unknown', 0], ['tcp', 2], ['exhausted', 3]] as const) {
      await mount(page, pending[name].aged, 60_001);
      const row = page.locator('[data-fleet-diagnosis="node-1"]');
      await expect(row).toHaveAttribute('data-fleet-state', 'stale');
      await expect(row.locator('[data-fleet-route-requirement="node-1"]')).toHaveCount(0);
      await expect(row).not.toContainText(words[locale][index]);
    }
  });
});
