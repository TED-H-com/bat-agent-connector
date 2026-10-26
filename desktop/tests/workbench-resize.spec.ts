import {test, expect} from '@playwright/test';
import {workspaceFixture, mountWorkspace, workspaceRoute} from './workspace-fixture.ts';

for (const lang of ['en', 'zh-TW']) for (const native of [false, true]) {
  test(`work list width preserves reading and drafts ${lang} ${native ? 'IPC' : 'HTTP'}`, async ({page}) => {
    const f = workspaceFixture(lang);
    await mountWorkspace(page, native, f);
    await page.addInitScript(lang => Object.defineProperty(navigator, 'language', {value: lang}), lang);
    await page.setViewportSize({width: 1440, height: 900});
    await page.goto('/dashboard/?workbench=1' + workspaceRoute);
    const handle = page.getByRole('separator', {name: lang === 'en' ? 'Resize work list' : '調整工作清單寬度'});
    const list = page.locator('.wc-context-list');
    const draft = page.locator('#workspace-message-input textarea');
    await draft.fill('Keep this draft. 保留草稿。');
    const original = await draft.elementHandle();
    const query = list.getByRole('searchbox');
    await query.fill(lang === 'en' ? 'search' : '搜尋');
    const selected = list.locator(`a[href="${workspaceRoute}"]`);
    await expect(selected).toHaveAttribute('aria-current', 'page');
    await expect(handle).toHaveAttribute('aria-controls', 'workbench-context-list');
    await expect(handle).toHaveAttribute('aria-valuenow', '256');
    await handle.focus();
    await page.keyboard.press('End');
    await expect(handle).toHaveAttribute('aria-valuenow', '500');
    expect((await list.boundingBox())!.width).toBe(500);
    expect(await page.evaluate(() => localStorage.getItem('batc.workbench.list.width'))).toBe('500');
    expect(await page.evaluate(() => localStorage.getItem('batc.sidebar.width'))).toBeNull();

    // Narrow layouts clamp presentation without overwriting the saved preference.
    await page.setViewportSize({width: 1000, height: 900});
    await expect(handle).toHaveAttribute('aria-valuenow', '456');
    expect((await page.locator('#main').boundingBox())!.width).toBeGreaterThanOrEqual(360);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    const title = (await page.locator('.workspace-session-heading h1').boundingBox())!;
    expect(title.width).toBeGreaterThan(150);
    await page.setViewportSize({width: 1440, height: 900});
    await expect(handle).toHaveAttribute('aria-valuenow', '500');
    await page.setViewportSize({width: 390, height: 844});
    await expect(handle).toBeHidden();
    expect(await draft.evaluate((node, original) => node === original, original)).toBe(true);
    await page.locator('.wc-list-back').click();
    await expect(list).toBeVisible();
    await expect(handle).toBeHidden();
    await expect(query).toHaveValue(lang === 'en' ? 'search' : '搜尋');
    await selected.click();
    await page.setViewportSize({width: 1440, height: 900});
    await expect(handle).toHaveAttribute('aria-valuenow', '500');
    await expect(draft).toHaveValue('Keep this draft. 保留草稿。');
    await expect(selected).toHaveAttribute('aria-current', 'page');
    await page.reload();
    await expect(handle).toHaveAttribute('aria-valuenow', '500');
    await expect(draft).toHaveValue('Keep this draft. 保留草稿。');
    expect(f.data.posts).toEqual([]);
  });
}

test('pointer resize commits once; Escape, lost capture and route changes abandon previews', async ({page}) => {
  const f = workspaceFixture();
  await mountWorkspace(page, false, f);
  await page.setViewportSize({width: 1440, height: 900});
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  const handle = page.locator('.wc-list-resizer');
  await expect(handle).toBeVisible();
  const drag = async (delta: number) => {
    const box = (await handle.boundingBox())!;
    const x = box.x + box.width / 2, y = box.y + 100;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + delta, y);
  };
  await drag(80);
  await expect(handle).toHaveAttribute('aria-valuenow', '336');
  expect(await page.evaluate(() => localStorage.getItem('batc.workbench.list.width'))).toBeNull();
  await page.mouse.up();
  expect(await page.evaluate(() => localStorage.getItem('batc.workbench.list.width'))).toBe('336');
  for (const cancel of ['Escape', 'pointercancel', 'lostpointercapture', 'route', 'mobile']) {
    await drag(60);
    await expect(handle).toHaveAttribute('aria-valuenow', '396');
    if (cancel === 'Escape') await page.keyboard.press('Escape');
    else if (cancel === 'route') await page.evaluate(() => {location.hash = '#/work-list';});
    else if (cancel === 'mobile') await page.setViewportSize({width: 390, height: 844});
    else await handle.dispatchEvent(cancel);
    await expect(page.locator('body')).not.toHaveClass(/workspace-resizing/);
    await page.mouse.up();
    expect(await page.evaluate(() => localStorage.getItem('batc.workbench.list.width'))).toBe('336');
    if (cancel === 'route') await page.evaluate(route => {location.hash = route;}, workspaceRoute);
    if (cancel === 'mobile') await page.setViewportSize({width: 1440, height: 900});
    await expect(handle).toHaveAttribute('aria-valuenow', '336');
  }
  await handle.focus();
  await page.keyboard.press('Home');
  await expect(handle).toHaveAttribute('aria-valuenow', '200');
  await page.keyboard.press('Shift+ArrowRight');
  await expect(handle).toHaveAttribute('aria-valuenow', '248');
  await handle.dblclick();
  await expect(handle).toHaveAttribute('aria-valuenow', '256');
  await page.setViewportSize({width: 1100, height: 900});
  await handle.dblclick();
  await expect(handle).toHaveAttribute('aria-valuenow', '220');
  expect(f.data.posts).toEqual([]);
});

test('coarse desktop pointer has a 44px resize target without widening the grid', async ({browser}) => {
  const context = await browser.newContext({hasTouch: true, viewport: {width: 1440, height: 900}});
  const page = await context.newPage();
  await mountWorkspace(page, false, workspaceFixture());
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  const handle = page.locator('.wc-list-resizer');
  await expect(handle).toBeVisible();
  expect((await handle.boundingBox())!.width).toBeGreaterThanOrEqual(44);
  expect((await page.locator('.wc-context-list').boundingBox())!.width).toBe(256);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await context.close();
});

test('invalid or unavailable local width preferences do not block resizing', async ({page}) => {
  await mountWorkspace(page, false, workspaceFixture());
  await page.addInitScript(() => localStorage.setItem('batc.workbench.list.width', '300garbage'));
  await page.setViewportSize({width: 1440, height: 900});
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  const handle = page.locator('.wc-list-resizer');
  await expect(handle).toHaveAttribute('aria-valuenow', '256');
  await page.evaluate(() => Object.defineProperty(window, 'localStorage', {get() {throw new Error('Unavailable');}}));
  await handle.focus();
  await page.keyboard.press('ArrowRight');
  await expect(handle).toHaveAttribute('aria-valuenow', '272');
});
