import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {DEMO_NOW} from './demo/project.ts';

for (const locale of ['en', 'zh-TW']) for (const width of [390, 768, 1440]) {
  test(`Resources and Overview retain their age clocks ${locale} ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 960});
    await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
    await page.clock.setFixedTime(DEMO_NOW);
    const fixture = demoFixture({locale}), reset = DEMO_NOW / 1000 + 3600;
    await mountConversation(page, false, async input => {
      const result = await fixture.dispatch(input);
      if (new URL(input.path, 'http://fixture').pathname.endsWith('/resources')) {
        // Resource snapshots expose reset times in epoch seconds, independently
        // of the ISO observation time and the Overview projection's as_of.
        result.data.quota_pools[0].snapshot.windows[0].resets_at = reset;
      }
      return result;
    });
    await page.goto(`/dashboard/#/pm/${fixture.ids.workspaceId}/agents`);
    await expect(page.locator(`[data-resource-quota] time[datetime="${new Date(reset * 1000).toISOString()}"]`))
      .toHaveText(locale === 'en' ? 'in 1 hr' : '1 小時後');

    // Overview ages use its producer's projection clock, even when the browser
    // has moved two days beyond that captured projection.
    await page.clock.setFixedTime(DEMO_NOW + 2 * 86400000);
    await page.goto(`/dashboard/#/pm/${fixture.ids.workspaceId}/overview`);
    await expect(page.locator('[data-overview-age]').first()).toHaveText(locale === 'en' ? '5 d' : '5 天');
  });
}
