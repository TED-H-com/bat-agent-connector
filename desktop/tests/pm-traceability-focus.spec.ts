import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {reviewFixture} from './requirements-review-fixture.ts';

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) {
  test(`requirement group focus stays on the heading ${locale} ${width}`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage(), f = reviewFixture(locale);
    await f.mount(page, 'requirements', 'milestone:design_review');
    const heading = page.locator('.pm-requirement-group:focus');
    await expect(heading).toHaveText(locale === 'en' ? 'Design review' : '設計審查');
    await expect(heading).toHaveAttribute('tabindex', '-1');
    await expect(heading).toHaveCSS('outline-style', 'none');
    await expect(heading).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    if (process.env.FR4_FIX1_SHOTS) {
      mkdirSync(process.env.FR4_FIX1_SHOTS, {recursive: true});
      await page.screenshot({path: `${process.env.FR4_FIX1_SHOTS}/requirements-${locale}-${width}.png`, fullPage: true});
    }
    expect(f.writes).toHaveLength(0);
    await context.close();
  });
}
