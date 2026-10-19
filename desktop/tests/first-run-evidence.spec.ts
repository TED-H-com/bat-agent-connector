// Synthetic fixtures only; opt-in visual evidence for the setup contract.
import {test, expect} from '@playwright/test';
import {managedSetupFixture} from './managed-setup-fixture.ts';
for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) test(`first-run evidence ${locale} ${width}`, async ({page}) => {
  test.skip(!process.env.BATC_FIRST_RUN_SHOTS, 'Opt-in screenshot capture');
  await page.setViewportSize({width, height: width === 390 ? 844 : 900});
  await managedSetupFixture(page, false, locale);
  await page.addInitScript(value => Object.defineProperty(navigator, 'language', {get: () => value}), locale);
  await page.goto('/dashboard/');
  await expect(page.locator('[data-managed-setup]')).toBeVisible();
  await expect(page.locator('[data-managed-setup]')).toContainText(locale === 'en' ? 'Configured:' : '已設定');
  await page.screenshot({path: `../docs/design/first-run-simple/${process.env.BATC_FIRST_RUN_SHOTS}-${locale}-${width}.png`, animations: 'disabled'});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
