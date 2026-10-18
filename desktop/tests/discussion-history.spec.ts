import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';

for (const locale of ['en', 'zh-TW']) for (const width of [390, 768, 1440]) {
  test(`discussion Back and Forward retain one composer and its context ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: 844}});
    const page = await context.newPage(), f = demoFixture({locale, discussions: true});
    await mountConversation(page, false, f.dispatch);
    const route = `#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`;
    await page.goto('/dashboard/' + route);
    const composer = page.locator('[data-discussion-composer]'), panel = page.locator('.pd-panel-overlay');
    await expect(composer).toBeEnabled();
    await composer.fill('保留原來的目標\nDo not send this draft.');
    const original = await composer.elementHandle();
    const trigger = page.locator('[data-discussion-panel-toggle]');
    await trigger.click();
    const inputs = page.getByRole('tab', {name: locale === 'en' ? 'Inputs' : '資訊', exact: true});
    await inputs.click();
    await expect(panel).toBeVisible();
    const history = await page.evaluate(() => window.history.state);
    expect(typeof history.batcSheet).toBe('string');
    expect(JSON.stringify(history)).not.toContain('Do not send this draft.');
    expect(JSON.stringify(history)).not.toContain(f.ids.threadIdea);
    const shots = process.env.BATC_DISCUSSION_SHOTS;
    if (shots) {mkdirSync(shots, {recursive: true}); await page.screenshot({path: `${shots}/discussion-${locale}-${width}.png`});}
    await page.goBack();
    await expect(panel).toHaveCount(0);
    expect(await page.evaluate(() => location.hash)).toBe(route);
    await expect(trigger).toBeFocused();
    await expect(composer).toHaveValue('保留原來的目標\nDo not send this draft.');
    expect(await composer.evaluate((node, original) => node === original, original)).toBe(true);
    await page.goForward();
    await expect(panel).toBeVisible();
    await expect(inputs).toHaveAttribute('aria-selected', 'true');
    // Responsive changes move the same panel body; neither form acquires a second sender.
    const body = await panel.locator('[data-discussion-panel]').elementHandle();
    await page.setViewportSize({width: width < 1200 ? 1440 : 390, height: 844});
    expect(await panel.locator('[data-discussion-panel]').evaluate((node, body) => node === body, body)).toBe(true);
    await page.keyboard.press('Escape');
    await expect.poll(() => page.evaluate(() => history.state?.batcSheet || null)).toBeNull();
    await expect(trigger).toBeFocused();
    await page.getByRole('button', {name: locale === 'en' ? 'Refresh' : '重新整理', exact: true}).click();
    await expect(composer).toBeEnabled();
    await expect(panel).toHaveCount(0);
    await expect(composer).toHaveValue('保留原來的目標\nDo not send this draft.');
    expect(await composer.evaluate((node, original) => node === original, original)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(f.discussion.writes).toEqual([]);
    await context.close();
  });
}

for (const native of [false, true]) test(`new discussion history retains a local draft without creating work ${native ? 'IPC' : 'HTTP'}`, async ({page}) => {
  const f = demoFixture({discussions: true});
  await mountConversation(page, native, f.dispatch);
  await page.setViewportSize({width: 390, height: 844});
  const route = `#/pm/${f.ids.workspaceId}/discussions/new`;
  await page.goto('/dashboard/' + route);
  const composer = page.locator('[data-discussion-composer]'), panel = page.locator('.pd-panel-overlay');
  await expect(composer).toBeEnabled(); await composer.fill('還在想，先不要建立工作。');
  const original = await composer.elementHandle();
  await page.locator('[data-discussion-panel-toggle]').click();
  await page.goBack(); await expect(panel).toHaveCount(0);
  expect(await page.evaluate(() => location.hash)).toBe(route);
  await page.goForward(); await expect(panel).toBeVisible();
  await panel.locator('.ui-inspector-back').click();
  await expect.poll(() => page.evaluate(() => history.state?.batcSheet || null)).toBeNull();
  expect(await composer.evaluate((node, original) => node === original, original)).toBe(true);
  await expect(composer).toHaveValue('還在想，先不要建立工作。');
  expect(f.discussion.writes).toEqual([]);
});

for (const width of [390, 1440]) test(`panel reading position survives Back and Forward ${width}`, async ({page}) => {
  const f = demoFixture({discussions: true});
  const data = f.discussion.details.get(f.ids.threadIdea);
  data.inputs = Array.from({length: 24}, (_, n) => ({...data.inputs[0], input_id: 'pmi_' + n.toString(16).padStart(32, '0')}));
  await mountConversation(page, false, f.dispatch);
  await page.setViewportSize({width, height: 844});
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await expect(page.locator('[data-discussion-composer]')).toBeEnabled();
  await page.locator('[data-discussion-panel-toggle]').click();
  await page.getByRole('tab', {name: 'Inputs', exact: true}).click();
  const scroller = page.locator(width < 1200 ? '.pd-panel-overlay .ui-inspector-body' : '.pd-panel-home');
  await scroller.evaluate(node => {node.scrollTop = 450;});
  await expect.poll(() => scroller.evaluate(node => node.scrollTop)).toBe(450);
  await page.goBack();
  await expect(page.locator('.pd-panel-overlay')).toHaveCount(0);
  await page.goForward();
  await expect.poll(() => scroller.evaluate(node => node.scrollTop)).toBe(450);
  expect(f.discussion.writes).toEqual([]);
});

test('withdrawal closes an open panel without changing the route or reviving it on Forward', async ({page}) => {
  const f = demoFixture({discussions: true});
  let failed = false;
  await mountConversation(page, false, async input => input.path === `/pm/workspaces/${f.ids.workspaceId}` && failed
    ? {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Workspace withdrawn'}}} : f.dispatch(input));
  // The desktop header remains available while its discussion panel is open.
  await page.setViewportSize({width: 1440, height: 844});
  const route = `#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`;
  await page.goto('/dashboard/' + route);
  await expect(page.locator('[data-discussion-composer]')).toBeEnabled();
  await page.locator('[data-discussion-panel-toggle]').click();
  failed = true;
  await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect(page.locator('.pd-panel-overlay')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => history.state?.batcSheet || null)).toBeNull();
  expect(await page.evaluate(() => location.hash)).toBe(route);
  failed = false;
  await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect(page.locator('[data-discussion-composer]')).toBeEnabled();
  await page.goBack(); await page.goForward();
  await expect(page.locator('.pd-panel-overlay')).toHaveCount(0);
  expect(f.discussion.writes).toEqual([]);
});

for (const native of [false, true]) for (const code of [403, 503]) {
  test(`withdrawn discussion history stays closed after recovery ${native ? 'IPC' : 'HTTP'} ${code}`, async ({page}) => {
    const f = demoFixture({discussions: true});
    const threadPath = `/pm/workspaces/${f.ids.workspaceId}/threads/${f.ids.threadIdea}`;
    let failed = false;
    await mountConversation(page, native, async input => input.path === threadPath && failed
      ? {status: code, data: {error: {code: 'UNAVAILABLE', message: 'Thread unavailable'}}} : f.dispatch(input));
    await page.setViewportSize({width: 390, height: 844});
    const route = `#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`;
    await page.goto('/dashboard/' + route);
    const composer = page.locator('[data-discussion-composer]'), panel = page.locator('.pd-panel-overlay');
    await expect(composer).toBeEnabled();
    await composer.fill('Keep the original unsent note.');
    await page.locator('[data-discussion-panel-toggle]').click();
    await page.goBack();
    await expect(panel).toHaveCount(0);
    failed = true;
    await page.getByRole('button', {name: 'Refresh', exact: true}).click();
    await expect(page.locator('.pm-main')).toContainText('Thread unavailable');
    expect(await page.evaluate(() => location.hash)).toBe(route);
    failed = false;
    await page.getByRole('button', {name: 'Refresh', exact: true}).click();
    await expect(composer).toBeEnabled();
    await page.goForward();
    await expect(panel).toHaveCount(0);
    await expect(composer).toHaveValue('Keep the original unsent note.');
    // An explicit new reading gets a new history identity after recovery.
    await page.locator('[data-discussion-panel-toggle]').click();
    await expect(panel).toBeVisible();
    await page.goBack();
    await expect(panel).toHaveCount(0);
    expect(await page.evaluate(() => location.hash)).toBe(route);
    expect(f.discussion.writes).toEqual([]);
  });
}
