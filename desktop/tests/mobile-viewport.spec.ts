import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {workspaceFixture, mountWorkspace, workspaceRoute} from './workspace-fixture.ts';
import {followUpFixture, openWork, WS, WI, ATTEMPT} from './work-next-turn-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';
import {installViewport, resizeViewport} from './viewport-fixture.ts';

for (const locale of ['en', 'zh-TW']) for (const surface of ['session', 'work', 'discussion']) for (const width of [390, 768, 844, 1440]) {
  test(`keyboard keeps the ${surface} composer usable without remounting ${locale} ${width}`, async ({browser, baseURL}) => {
    const height = width === 844 ? 390 : 844;
    const context = await browser.newContext({baseURL, locale, viewport: {width, height}, isMobile: width < 1000, hasTouch: width < 1000});
    try {
    const page = await context.newPage(); await installViewport(page);
    const work = workspaceFixture(locale), next = followUpFixture(locale), discussion = demoFixture({locale, discussions: true});
    if (surface === 'work') await openWork(page, next, locale);
    else if (surface === 'session') await mountWorkspace(page, false, work);
    else await mountConversation(page, false, discussion.dispatch);
    const route = surface === 'session' ? workspaceRoute : `#/pm/${discussion.ids.workspaceId}/discussions/${discussion.ids.threadIdea}`;
    if (surface !== 'work') await page.goto('/dashboard/?workbench=1' + route);
    const draft = page.locator(surface === 'work' ? '[data-follow-up-input]' : surface === 'session' ? '#workspace-message-input textarea' : '[data-discussion-composer]');
    const send = surface === 'work' ? page.getByRole('button', {name: locale === 'en' ? 'Send next' : '送出下一步', exact: true})
      : surface === 'session' ? page.getByRole('button', {name: locale === 'en' ? 'Send' : '送出', exact: true})
      : page.getByRole('button', {name: locale === 'en' ? 'Save note only' : '只存筆記', exact: true});
    await draft.fill('保留原目標\nKeep the unsent text.'); await expect(send).toBeEnabled();
    const original = await draft.elementHandle();
    const shots = process.env.BATC_VIEWPORT_SHOTS;
    if (shots) {mkdirSync(shots, {recursive: true}); await page.screenshot({path: `${shots}/${surface}-${locale}-${width}-before.png`});}
    if (width < 1000) {
      const visible = width === 844 ? 260 : 450, offsetTop = width === 844 ? 0 : 36;
      await resizeViewport(page, {height: visible, offsetTop});
      await expect(page.locator('body')).toHaveClass(/mobile-keyboard/);
      if (shots) await page.screenshot({path: `${shots}/${surface}-${locale}-${width}-keyboard.png`});
      await expect.poll(async () => Math.round((await send.boundingBox())!.y + (await send.boundingBox())!.height)).toBeLessThanOrEqual(visible + offsetTop);
      await expect(send).toBeInViewport({ratio: 1});
      if (surface !== 'discussion' && width === 844) expect((await page.locator('.workspace-session-heading h1').boundingBox())!.y).toBeGreaterThanOrEqual(offsetTop + 48);
      expect((await draft.boundingBox())!.y).toBeGreaterThanOrEqual(offsetTop);
      expect((await draft.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      expect(await draft.evaluate((node, original) => node === original, original)).toBe(true);
      await resizeViewport(page, {height: visible, scale: 1.6});
      await expect(page.locator('body')).not.toHaveClass(/mobile-keyboard/);
      await resizeViewport(page, {height});
      await expect(page.locator('body')).not.toHaveClass(/mobile-keyboard/);
    }
    await expect(draft).toHaveValue('保留原目標\nKeep the unsent text.');
    expect(await draft.evaluate((node, original) => node === original, original)).toBe(true);
    expect(work.data.posts).toEqual([]); expect(next.follow.posts).toEqual([]); expect(discussion.discussion.writes).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    } finally {await context.close();}
  });
}

for (const native of [false, true]) test(`queued Work rotates with the same draft and readable receipts ${native ? 'IPC' : 'HTTP'}`, async ({page}) => {
  await installViewport(page);
  await page.setViewportSize({width: 390, height: 844});
  const f = followUpFixture('zh-TW', {native});
  f.follow.items = Array.from({length: 8}, (_, i) => ({operation_id: 'op_' + (i + 1).toString(16).padStart(32, '0'),
    workspace_id: WS, work_item_id: WI, attempt_id: ATTEMPT, sequence: i + 1, state: 'queued', actor: 'owner',
    text_excerpt: `保留指令 ${i + 1}`, labels: {state: {en: 'Queued', 'zh-TW': '排隊中'}, detail: {en: 'Waiting', 'zh-TW': '等待這一輪結束'}}, next_allowed_actions: []}));
  await openWork(page, f, 'zh-TW');
  const field = page.locator('[data-follow-up-input]'), send = page.getByRole('button', {name: '送出下一步', exact: true});
  await field.fill('旋轉後仍是同一個草稿'); const original = await field.elementHandle();
  await resizeViewport(page, {height: 450});
  await expect.poll(async () => (await send.boundingBox())!.y + (await send.boundingBox())!.height).toBeLessThanOrEqual(450);
  const list = page.locator('[data-follow-up-list]');
  await list.evaluate(node => {node.scrollTop = node.scrollHeight;});
  await expect(page.getByText('保留指令 8', {exact: true})).toBeInViewport();
  await page.setViewportSize({width: 844, height: 390}); await resizeViewport(page, {height: 260});
  await expect.poll(async () => (await send.boundingBox())!.y + (await send.boundingBox())!.height).toBeLessThanOrEqual(260);
  await expect(send).toBeInViewport({ratio: 1});
  await page.setViewportSize({width: 1440, height: 900});
  await expect(page.locator('body')).not.toHaveClass(/mobile-keyboard/);
  await expect(page.locator('.wc-view-bar')).toBeVisible();
  await expect(field).toHaveValue('旋轉後仍是同一個草稿');
  expect(await field.evaluate((node, original) => node === original, original)).toBe(true);
  expect(f.follow.posts).toEqual([]);
});

test('Discussion sheet stays inside the keyboard viewport and returns to the original draft', async ({page}) => {
  await installViewport(page); await page.setViewportSize({width: 390, height: 844});
  const f = demoFixture({locale: 'zh-TW', discussions: true});
  await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  const field = page.locator('[data-discussion-composer]'); await field.fill('先保留');
  const original = await field.elementHandle();
  await resizeViewport(page, {height: 450, offsetTop: 36});
  await page.locator('[data-discussion-panel-toggle]').click();
  const panel = page.locator('.pd-panel-overlay'); await expect(panel).toBeVisible();
  const bounds = (await panel.boundingBox())!;
  expect(bounds.y).toBe(36); expect(bounds.y + bounds.height).toBeLessThanOrEqual(486);
  await page.goBack(); await expect(panel).toHaveCount(0);
  await resizeViewport(page, {height: 844});
  await expect(field).toHaveValue('先保留');
  expect(await field.evaluate((node, original) => node === original, original)).toBe(true);
  expect(f.discussion.writes).toEqual([]);
});

test('without visualViewport the ordinary Discussion layout and draft stay usable', async ({page}) => {
  await page.addInitScript(() => Object.defineProperty(window, 'visualViewport', {value: undefined, configurable: true}));
  await page.setViewportSize({width: 390, height: 844});
  const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  const field = page.locator('[data-discussion-composer]'); await field.fill('Keep this note');
  await page.setViewportSize({width: 844, height: 390});
  await expect(page.locator('body')).not.toHaveClass(/mobile-keyboard/);
  await expect(page.getByRole('button', {name: 'Save note only', exact: true})).toBeEnabled();
  await expect(field).toHaveValue('Keep this note'); expect(f.discussion.writes).toEqual([]);
});
