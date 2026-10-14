import {mkdir} from 'node:fs/promises';
import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {openPmSection} from './pm-navigation.ts';

async function screenshot(page: Page, name: string) {
  const directory = process.env.BATC_DEMO_PEOPLE_SCREENSHOTS;
  if (!directory) return;
  await mkdir(directory, {recursive: true});
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({path: `${directory}/${name}.png`, fullPage: true, animations: 'disabled'});
}
for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) {
  test(`demo people keep names and read-only authority ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage(), f = demoFixture({locale}), errors: string[] = [], writes: unknown[] = [];
    page.on('pageerror', error => errors.push(error.message));
    try {
      await mountConversation(page, false, async input => {if (input.method !== 'GET') writes.push(input); return f.dispatch(input);});
      await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/issues`);
      await expect(page.locator('[data-reg-owner]')).toBeVisible();
      await expect(page.locator('[data-pm-register]')).toContainText('mei.lin');
      await screenshot(page, `register-${locale}-${width}`);
      for (const view of ['issues', 'overview', 'discussions']) {
        await openPmSection(page, view);
        if (view === 'overview') {
          await expect(page.locator('.pm-overview-charter-grid > section')).toHaveCount(6);
          await screenshot(page, `overview-${locale}-${width}`);
        }
        if (view === 'discussions') {
          await page.locator('.pm-thread-link').first().click();
          await expect(page.locator('.pd-transcript')).toContainText('mei.lin');
        }
        const text = await page.locator('#main').innerText();
        expect(text).not.toMatch(/\b(?:reader|developer|undefined|null|NaN)\b|\b(?:ui_|pm_)\w+\b/);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        if (view === 'overview') {
          await page.locator('[data-overview-link="charter:definition:sources"]').click();
          await expect(page.locator('[data-pm-overview]')).toContainText('mei.lin');
        }
      }
      const record = await f.dispatch({path: `/pm/workspaces/${f.ids.workspaceId}`});
      expect(record.data.workspace.membership).toMatchObject({actor: 'mei.lin', role: 'reader'});
      expect(f.ids.sources).toMatchObject({codex: 'codex-implementer', claude: 'claude-reviewer'});
      expect(writes).toEqual([]); expect(errors).toEqual([]);
    } finally {await context.close();}
  });
}

test('demo governance history and repair lifecycle remain readable', async ({page}) => {
  const f = demoFixture(), root = `/pm/workspaces/${f.ids.workspaceId}`;
  // The fallback governance reader exposes history; the Register capability uses
  // its processing timeline instead. Exercise the same demo records and route.
  await mountConversation(page, false, async input => {
    const result = await f.dispatch(input);
    if (input.path === '/bootstrap' || input.path === '/capabilities') {
      const caps = input.path === '/bootstrap' ? result.data.capabilities : result.data;
      delete caps.features.pm_register;
    }
    return result;
  });
  await page.setViewportSize({width: 1440, height: 900});
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/issues`);
  await page.locator('[data-pm-governance-row]').first().getByRole('button').click();
  const detail = page.locator('[data-pm-governance-detail]');
  await detail.locator(':scope > details > summary').click();
  await detail.getByText('History', {exact: true}).click();
  await expect(detail).toContainText('mei.lin');
  await screenshot(page, 'governance-history-en-1440');
  await openPmSection(page, 'issues', 'ui_repairs');
  const repairs = page.locator('[data-pm-maintenance-repairs]');
  await expect(repairs).toContainText('Operation completed');
  await repairs.getByRole('combobox', {name: 'Saved repair', exact: true}).selectOption('pmrp_00000000000000000000000000000001');
  const saved = repairs.locator('[data-repair]');
  await expect(saved).toBeVisible();
  await saved.locator(':scope > details > summary').click();
  await expect(saved).toContainText('Operation completed');
  await expect(saved).toContainText('Not proven');
  const lifecycle = await f.dispatch({path: root + '/lifecycle'});
  expect(lifecycle.data.lifecycle).toMatchObject({stage: 'development', status: 'active'});
  await screenshot(page, 'maintenance-repairs-en-1440');
});
