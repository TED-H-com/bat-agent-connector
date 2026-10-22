import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {mountProfilePicker} from './work-profile-picker-fixture.ts';

const fleet = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));
const profiles = JSON.parse(readFileSync(new URL('../../tests/fixtures/work-profile-readiness.json', import.meta.url), 'utf8'));

for (const locale of ['en-US', 'zh-TW']) test.describe(`shared readiness ${locale}`, () => {
  test.use({locale});
  test('Fleet uses the header formatter at the same clock boundary', async ({page}) => {
    await page.goto('/ui-components.html');
    await page.evaluate(async ({fleet, locale}) => {
      const {fleetStatus} = await import('/src/fleet-status.js');
      const {h} = await import('/src/ui/dom.js');
      const {t, setLocale} = await import('/src/i18n.js');
      setLocale(locale === 'en-US' ? 'en' : locale);
      const now = Date.parse(fleet.readiness.observed_at) + 60_000;
      const node = fleetStatus(fleet.readiness.hosts[0], {h, t, now, snapshot: fleet,
        readable: true, entry: fleet.configuration.connections[0], detailsState: new Map()});
      document.getElementById('fixture')!.append(node);
    }, {fleet, locale});
    await expect(page.locator('.fleet-diagnosis-title')).toHaveText(locale === 'en-US'
      ? 'Connected · 1 min ago · Checking' : '已連線 · 1 分鐘前 · 檢查中');
    await expect(page.locator('[data-fleet-diagnosis]')).toHaveAttribute('data-freshness', 'stale');
    await page.locator('summary').click();
    await expect(page.locator('[data-check-state="passed"]')).toHaveCount(0);
  });

  for (const [name, body] of Object.entries(profiles.cases) as [string, any][]) {
    test(`Work ${name}: producer lacks per-capability validity, so value text stays unchanged`, async ({page}) => {
      await page.clock.install({time: new Date(body.observed_at * 1000)});
      const {facts, reads, posts} = await mountProfilePicker(page, body, profiles.profile, locale);
      const values = locale === 'en-US'
        ? {ready: 'Ready', missing: 'Setup still missing', unknown: 'Not verified yet', unavailable: 'Unavailable'}
        : {ready: '已就緒', missing: '還缺設定', unknown: '尚未驗證', unavailable: '不可用'};
      // There is no deadline or automatic renewal in this producer/picker.
      // Reading or waiting cannot justify adding "Checking" to any capability.
      for (const elapsed of [0, 60_000]) {
        await page.clock.fastForward(elapsed);
        for (const [key, state] of Object.entries(body.states) as [string, any][]) {
          await expect(facts.locator(`[data-work-profile-state="${key}"] [data-work-profile-value]`)).toHaveText(values[state.value]);
        }
      }
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
