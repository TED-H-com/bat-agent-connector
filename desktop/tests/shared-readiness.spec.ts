import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {mountProfilePicker} from './work-profile-picker-fixture.ts';

const fleet = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));
const profiles = JSON.parse(readFileSync(new URL('../../tests/fixtures/work-profile-readiness.json', import.meta.url), 'utf8'));

for (const locale of ['en-US', 'zh-TW']) test.describe(`shared readiness ${locale}`, () => {
  test.use({locale});
  test('Fleet uses the header formatter at the same clock boundary', async ({page}) => {
    await page.goto('/ui-components.html');
    const result = await page.evaluate(async ({fleet, locale}) => {
      const {fleetStatus} = await import('/src/fleet-status.js');
      const {displayFact, healthLabel} = await import('/src/health-display.js');
      const {h} = await import('/src/ui/dom.js');
      const {t, setLocale} = await import('/src/i18n.js');
      setLocale(locale === 'en-US' ? 'en' : locale);
      const now = Date.parse(fleet.readiness.observed_at) + 60_000;
      const node = fleetStatus(fleet.readiness.hosts[0], {h, t, now, snapshot: fleet,
        readable: true, entry: fleet.configuration.connections[0], detailsState: new Map()});
      document.getElementById('fixture')!.append(node);
      return healthLabel(displayFact({key: 'ui_connected', observed_at: fleet.readiness.observed_at}, {now}), t);
    }, {fleet, locale});
    await expect(page.locator('.fleet-diagnosis-title')).toHaveText(result);
    await expect(page.locator('[data-fleet-diagnosis]')).toHaveAttribute('data-freshness', 'stale');
    await page.locator('summary').click();
    await expect(page.locator('[data-check-state="passed"]')).toHaveCount(0);
  });

  for (const [name, body] of Object.entries(profiles.cases) as [string, any][]) {
    test(`Work ${name} keeps independent values and shared freshness`, async ({page}) => {
      const {facts, reads, posts} = await mountProfilePicker(page, body, profiles.profile, locale);
      const value = facts.locator('[data-work-profile-state="can_observe"] [data-work-profile-value]');
      await expect(value).toContainText(locale === 'en-US' ? 'Checking' : '檢查中');
      await expect(value).toHaveAttribute('data-freshness', name === 'host_not_refreshed' ? 'stale' : 'unknown');
      if (body.states.can_observe.reason) await expect(facts.locator('[data-work-profile-state="can_observe"]'))
        .toContainText(body.states.can_observe.reason);
      await expect(facts.locator('[data-work-profile-state]')).toHaveCount(6);
      await expect(facts).toContainText(locale === 'en-US' ? 'PM analysis' : 'PM 分析');
      await expect(facts).toContainText(locale === 'en-US' ? 'A saved setting is not a ready check.' : '已設定不等於就緒。');
      expect(reads.filter(path => path.endsWith('/readiness'))).toHaveLength(1);
      expect(posts).toEqual([]);
    });
  }
});
