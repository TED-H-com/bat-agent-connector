import {test, expect, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';

// Sending creates the thread and ends the local draft. The next new discussion
// in that workspace must open with the rename closed and the composer focused.
const cases = [
  {locale: 'en' as const, width: 1440, height: 900, rename: 'Rename', title: 'Discussion title', save: 'Save', send: 'Save note only', typed: 'Choose a first improvement', message: 'Keep the first action visible.'},
  {locale: 'zh-TW' as const, width: 390, height: 844, rename: '重新命名', title: '討論標題', save: '儲存', send: '只存筆記', typed: '先選一項改善', message: '先把第一個動作留在畫面上。'},
];

async function openAnother(page: Page, spec: typeof cases[number], workspaceId: string) {
  const create = page.locator('[data-discussion-new]');
  if (spec.width < 800) {
    await page.locator('[data-discussion-back]').click();
    await expect(create).toBeVisible();
  }
  await create.click();
  await expect(page).toHaveURL(new RegExp(`/pm/${workspaceId}/discussions/new$`));
  await expect(page.locator('.pd-new-layout')).toBeVisible();
}

for (const spec of cases) {
  test(`sending a new discussion closes the remembered rename (${spec.locale} ${spec.width})`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale: spec.locale, viewport: {width: spec.width, height: spec.height}});
    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    const fixture = demoFixture({locale: spec.locale, discussions: true});
    await mountConversation(page, false, fixture.dispatch);
    await page.goto(`/dashboard/#/pm/${fixture.ids.workspaceId}/discussions/new`);
    const composer = page.locator('[data-discussion-composer]');
    await expect(composer).toBeFocused();
    const field = page.getByLabel(spec.title, {exact: true});
    await page.getByRole('button', {name: spec.rename, exact: true}).click();
    await field.fill(spec.typed);
    await composer.click();
    await expect(field).toBeVisible();
    await composer.fill(spec.message);
    await page.getByRole('button', {name: spec.send, exact: true}).click();
    await expect(page).toHaveURL(new RegExp(`/pm/${fixture.ids.workspaceId}/discussions/pmt_[a-f0-9]{32}$`));
    await expect(page.locator('.pm-notes')).toContainText(spec.message);
    await expect(page.locator('[data-discussion-title]')).toHaveText(spec.typed);
    await openAnother(page, spec, fixture.ids.workspaceId);
    await expect(page.getByRole('button', {name: spec.save, exact: true})).toBeHidden();
    await expect(composer).toBeFocused();
    await expect(field).toBeHidden();
    expect(errors).toEqual([]);
    await context.close();
  });
}
