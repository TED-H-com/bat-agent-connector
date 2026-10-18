import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {workspaceFixture, mountWorkspace, workspaceRoute} from './workspace-fixture.ts';

for (const lang of ['en', 'zh-TW']) for (const width of [390, 768, 1440]) {
  test(`list → Work → sheet → Back preserves query, selection and draft ${lang} ${width}`, async ({page}) => {
    const f = workspaceFixture(lang);
    for (let n = 1; n <= 40; n++) f.data.detail.work.push({...f.data.work,
      id: 'op_' + n.toString(16).padStart(32, '0'), title: `${f.data.session.title} ${n}`});
    await mountWorkspace(page, false, f);
    await page.addInitScript(lang => Object.defineProperty(navigator, 'language', {value: lang}), lang);
    await page.setViewportSize({width, height: 844});
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/dashboard/?workbench=1#/work-list');
    const list = page.locator('.wc-context-list');
    const query = list.getByRole('searchbox');
    await expect(list).toBeVisible();
    await query.fill(lang === 'en' ? 'search' : '搜尋');
    const shots = process.env.BATC_MOBILE_SHOTS;
    if (shots) {mkdirSync(shots, {recursive: true}); await page.screenshot({path: `${shots}/list-top-${lang}-${width}.png`});}
    const chosen = list.locator('.workspace-tree-link').filter({hasText: `${f.data.session.title} 25`});
    await chosen.scrollIntoViewIfNeeded();
    const scroll = await list.evaluate(node => node.scrollTop);
    expect(scroll).toBeGreaterThan(300);
    const href = await chosen.getAttribute('href');
    if (shots) {mkdirSync(shots, {recursive: true}); await page.screenshot({path: `${shots}/list-${lang}-${width}.png`});}
    await chosen.click();
    const draft = page.locator('#workspace-message-input textarea');
    await draft.fill('下一輪再補測試\nKeep my draft.');
    const original = await draft.elementHandle();
    const results = page.locator('.wc-inspector-host > .ui-inspector');
    await page.locator('.workspace-session-heading').getByRole('button', {name: lang === 'en' ? 'Results' : '成果', exact: true}).click();
    await expect(results).toBeVisible();
    if (width < 800) expect((await results.boundingBox())!.width).toBe(width);
    if (shots) await page.screenshot({path: `${shots}/sheet-${lang}-${width}.png`});
    await page.goBack();
    await expect(results).toBeHidden();
    expect(await page.evaluate(() => location.hash)).toBe(href);
    await expect(draft).toHaveValue('下一輪再補測試\nKeep my draft.');
    await page.goForward();
    await expect(results).toBeVisible();
    await page.keyboard.press('Escape');
    await expect.poll(() => page.evaluate(() => history.state?.batcSheet || null)).toBeNull();
    // Resizing preserves the mounted composer and never registers another sender.
    await page.setViewportSize({width: 844, height: 390});
    expect(await draft.evaluate((node, original) => node === original, original)).toBe(true);
    await page.setViewportSize({width, height: 844});
    if (shots) await page.screenshot({path: `${shots}/work-${lang}-${width}.png`});
    await page.goBack();
    await expect(list).toBeVisible();
    await expect(query).toHaveValue(lang === 'en' ? 'search' : '搜尋');
    await expect(chosen).toHaveAttribute('aria-current', 'page');
    await expect.poll(() => list.evaluate(node => node.scrollTop)).toBe(scroll);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.goForward();
    await expect(draft).toHaveValue('下一輪再補測試\nKeep my draft.');
    await page.reload();
    await expect(draft).toHaveValue('下一輪再補測試\nKeep my draft.');
    expect(f.data.posts).toEqual([]);
    expect(errors).toEqual([]);
  });
}

test('phone workspace navigation and the Work list are separate, with one shared search', async ({page}) => {
  const f = workspaceFixture();
  await mountWorkspace(page, false, f);
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  const draft = page.locator('#workspace-message-input textarea');
  await draft.fill('Retain this while choosing work.');
  await page.locator('#workspace-menu').click();
  await expect(page.locator('.workspace-nav')).toBeVisible();
  await expect(page.locator('.workspace-nav .workspace-search')).toHaveCount(0);
  await page.locator('.wc-daily [data-daily=work]').click();
  await expect(page.locator('.wc-context-list')).toBeVisible();
  await expect(page.locator('.workspace-nav')).toBeHidden();
  await expect(page.locator('.workspace-search')).toHaveCount(1);
  await page.locator(`.wc-context-list a[href="${workspaceRoute}"]`).click();
  await expect(draft).toHaveValue('Retain this while choosing work.');
  await page.locator('.wc-list-back').click();
  await expect(page.locator('.wc-list-title')).toBeFocused();
  await page.locator('#workspace-menu').click();
  await expect(page.locator('.wc-context-list')).toHaveJSProperty('inert', true);
  await page.keyboard.press('Escape');
  await expect(page.locator('.wc-context-list')).toHaveJSProperty('inert', false);
  expect(f.data.posts).toEqual([]);
});

for (const native of [false, true]) test(`rotation and sheet return retain one sender and ignore composing Enter ${native ? 'IPC' : 'HTTP'}`, async ({page}) => {
  const f = workspaceFixture();
  await mountWorkspace(page, native, f);
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  const draft = page.locator('#workspace-message-input textarea');
  await draft.fill('補上重試測試');
  for (const viewport of [{width: 844, height: 390}, {width: 1440, height: 900}, {width: 390, height: 844}]) {
    await page.setViewportSize(viewport);
    await draft.dispatchEvent('keydown', {key: 'Enter', code: 'Enter', ctrlKey: true, isComposing: true, keyCode: 229});
  }
  expect(f.data.posts).toEqual([]);
  await page.locator('.workspace-session-heading').getByRole('button', {name: 'Results', exact: true}).click();
  await page.goBack();
  await page.getByRole('button', {name: 'Send', exact: true}).click();
  await expect.poll(() => f.data.posts.length).toBe(1);
  expect(f.data.posts[0].body).toMatchObject({action: 'session.send', target: {host: 'demo', session_id: 'search-results'}, params: {text: '補上重試測試', queue: true}});
});

for (const native of [false, true]) test(`Forward cannot reopen withdrawn Results ${native ? 'IPC' : 'HTTP'}`, async ({page}) => {
  const f = workspaceFixture();
  const dispatch = f.dispatch;
  let denied = false;
  f.dispatch = async input => input.path === '/sessions/demo/search-results' && denied
    ? {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Withdrawn'}}} : dispatch(input);
  await mountWorkspace(page, native, f);
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  await expect(page.locator('.conversation .msg')).toHaveCount(2);
  await page.locator('.workspace-session-heading').getByRole('button', {name: 'Results', exact: true}).click();
  const results = page.locator('.wc-inspector-host > .ui-inspector');
  await expect(results).toBeVisible();
  denied = true;
  f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect(page.locator('[data-session-withdrawn]')).toBeVisible();
  await expect.poll(() => page.evaluate(() => history.state?.batcSheet || null)).toBeNull();
  await page.goForward();
  await expect(results).toBeHidden();
  await expect(page.locator('[data-session-withdrawn]')).toBeVisible();
  expect(f.data.posts).toEqual([]);
});
