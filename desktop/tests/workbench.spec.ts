import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {workspaceFixture, mountWorkspace, workspaceRoute} from './workspace-fixture.ts';

for (const lang of ['en', 'zh-TW']) for (const width of [1920, 1440, 1280, 1024, 768, 390, 320]) {
  test(`workbench continuous reading ${lang} ${width}`, async ({page}) => {
    const f = workspaceFixture(lang);
    await mountWorkspace(page, false, f);
    await page.addInitScript(lang => Object.defineProperty(navigator, 'language', {value: lang}), lang);
    await page.setViewportSize({width, height: 900});
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    const shots = process.env.BATC_WORKBENCH_SHOTS;
    if (shots) {
      mkdirSync(shots, {recursive: true});
      await page.goto('/dashboard/' + workspaceRoute);
      await expect(page.locator('.conversation .msg')).toHaveCount(2);
      await page.screenshot({path: `${shots}/before-${lang}-${width}.png`, animations: 'disabled'});
    }
    await page.goto('/dashboard/?workbench=1' + workspaceRoute);
    await expect(page.locator('.conversation .msg')).toHaveCount(2);
    const heading = page.locator('#main h1');
    await expect(heading).toHaveText(f.data.session.title);
    await expect(page.locator('.wc-daily > a')).toHaveCount(4);
    const draft = page.locator('#workspace-message-input textarea');
    await draft.fill('下一輪補上失敗重試\nKeep this draft.');
    const tabs = page.locator('[data-workbench-views]');
    for (const name of lang === 'en' ? ['Lineage', 'Live', 'Work'] : ['關聯', '現場', '工作']) {
      await tabs.getByRole('tab', {name, exact: true}).click();
      await expect(heading).toHaveText(f.data.session.title);
      await expect(heading).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      await expect(page.locator('#main')).not.toContainText(/undefined|NaN|\bnull\b/);
      if (shots) await page.screenshot({path: `${shots}/after-${name}-${lang}-${width}.png`, animations: 'disabled'});
    }
    await expect(draft).toHaveValue('下一輪補上失敗重試\nKeep this draft.');
    await page.reload();
    await expect(draft).toHaveValue('下一輪補上失敗重試\nKeep this draft.');
    expect(f.data.posts).toEqual([]);
    expect(errors).toEqual([]);
  });
}

for (const native of [false, true]) test(`source withdrawal clears every perspective ${native ? 'IPC' : 'HTTP'}`, async ({page}) => {
  const f = workspaceFixture();
  let denied = false, relatedTitle = 'Another work';
  const dispatch = f.dispatch;
  f.dispatch = async input => {
    if (input.path === '/sessions/demo/search-results' && denied) return {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Removed'}}};
    const result = await dispatch(input);
    if (input.path === '/sessions/demo/search-results' && result.status === 200)
      Object.assign(result.data, {work_items: [{work_item_id: 'wi_' + '1'.repeat(20), title: relatedTitle}]});
    return result;
  };
  await mountWorkspace(page, native, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  await expect(page.locator('.conversation .msg')).toHaveCount(2);
  const tabs = page.locator('[data-workbench-views]');
  await tabs.getByRole('tab', {name: 'Lineage'}).click();
  const edge = page.locator('[data-edge]');
  await edge.click();
  await expect(page.locator('[data-workbench-inspect]')).toContainText('Another work');
  await expect(page.locator('#main h1')).toHaveText(f.data.session.title);
  const open = page.locator('[data-workbench-inspect]').getByRole('link', {name: 'Open record'});
  await open.focus();
  relatedTitle = 'Updated related work';
  f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect(page.locator('[data-workbench-inspect]')).toContainText(relatedTitle);
  await expect(open).toBeFocused();
  await page.keyboard.press('Escape'); await expect(edge).toBeFocused();
  if (process.env.BATC_WORKBENCH_SHOTS && !native) await page.screenshot({path: `${process.env.BATC_WORKBENCH_SHOTS}/after-Lineage-related-en.png`, animations: 'disabled'});
  await tabs.getByRole('tab', {name: 'Live'}).click();
  const returnWork = page.locator('[data-workbench-live]').getByRole('button', {name: 'Work', exact: true});
  await returnWork.focus();
  f.data.session.observed_at = '2026-10-10T12:04:00Z';
  f.data.events.push({seq: 2, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect(page.locator('[data-workbench-live] time')).toHaveAttribute('datetime', new Date(f.data.session.observed_at).toISOString());
  await expect(returnWork).toBeFocused();
  denied = true;
  f.data.events.push({seq: 3, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect(page.locator('[data-session-withdrawn]')).toBeVisible();
  await expect(page.locator('[data-workbench-live]')).not.toContainText(f.data.session.title);
  await expect(page.locator('[data-workbench-lineage]')).not.toContainText('Another work');
  await expect(page.locator('[data-workbench-views]')).toBeHidden();
  await expect(page.locator('[data-workbench-inspect]')).toHaveCount(0);
  expect(f.data.posts).toEqual([]);
});

test('unknown activity, stale observations and manual sessions remain honest and read only', async ({page}) => {
  const f = workspaceFixture(); f.data.session.api_access = 'read_only'; f.data.session.provenance = 'manual';
  await mountWorkspace(page, false, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  await expect(page.locator('[data-session-readonly]')).toBeVisible();
  await expect(page.locator('#workspace-message-input')).toBeHidden();
  await page.locator('[data-workbench-views]').getByRole('tab', {name: 'Live'}).click();
  Object.assign(f.data.session, {stale: true});
  f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect(page.locator('[data-workbench-summary]')).toContainText('out of date');
  await expect(page.locator('.wc-live-lane .wc-live-card')).toHaveCount(0);
  expect(f.data.posts).toEqual([]);
});

test('work identity survives linked session names and the result panel opens on demand', async ({page}) => {
  const f = workspaceFixture(); f.data.linkedItem = true;
  const item = f.fixture.data.items[0];
  item.title = 'Fix empty search'; Object.assign(item, {goal: 'Keep the original query.'});
  await mountWorkspace(page, false, f);
  await page.goto('/dashboard/?workbench=1#/item/' + item.work_item_id);
  await expect(page.locator('.workspace-session-heading h1')).toHaveText(item.title);
  await expect(page.locator('.wc-goal')).toHaveText('Keep the original query.');
  await expect(page.locator('.wc-inspector-host > .ui-inspector')).toBeHidden();
  await page.locator('[data-workbench-views]').getByRole('tab', {name: 'Lineage'}).click();
  await page.locator('.workspace-session-heading').getByRole('button', {name: 'Results', exact: true}).click();
  await expect(page.locator('.wc-inspector-host > .ui-inspector')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.wc-inspector-host > .ui-inspector')).toBeHidden();
  expect(f.data.posts).toEqual([]);
});

test('background output in another perspective preserves the Work reading position', async ({page}) => {
  const f = workspaceFixture();
  f.data.messages = Array.from({length: 30}, (_, n) => ({id: 'message-' + n, role: 'assistant', ts: '2026-10-10T12:02:00Z', text: `Update ${n}\n` + 'Readable history. '.repeat(30)}));
  await mountWorkspace(page, false, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  const scroll = page.locator('.conversation-scroll');
  await expect(page.locator('.conversation .msg')).toHaveCount(30);
  await scroll.evaluate(node => {node.scrollTop = 250; node.dispatchEvent(new Event('scroll'));});
  const before = await scroll.evaluate(node => node.scrollTop);
  await page.locator('[data-workbench-views]').getByRole('tab', {name: 'Lineage'}).click();
  f.data.messages.push({id: 'new', role: 'assistant', ts: '2026-10-10T12:03:00Z', text: 'New output while reading Lineage'});
  f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect.poll(() => f.data.after).toBe(1);
  await page.locator('[data-workbench-views]').getByRole('tab', {name: 'Work', exact: true}).click();
  await expect(page.locator('.conversation .msg')).toHaveCount(31);
  await expect.poll(() => scroll.evaluate(node => node.scrollTop)).toBe(before);
  expect(f.data.posts).toEqual([]);
});

test('a failed source read removes live claims and recovery restores the same draft', async ({page}) => {
  const f = workspaceFixture(); const dispatch = f.dispatch;
  let failed = false;
  f.dispatch = async input => input.path === '/sessions/demo/search-results' && failed
    ? {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Read failed'}}} : dispatch(input);
  await mountWorkspace(page, false, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  await page.locator('#workspace-message-input textarea').fill('Keep this request');
  await page.locator('[data-workbench-views]').getByRole('tab', {name: 'Live'}).click();
  failed = true;
  f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect(page.locator('[data-workbench-live]')).toContainText('could not be refreshed');
  await expect(page.locator('.wc-live-card')).toHaveCount(0);
  failed = false;
  await expect(page.locator('[data-workbench-summary]')).toContainText('Output is arriving', {timeout: 10000});
  await page.locator('[data-workbench-views]').getByRole('tab', {name: 'Work', exact: true}).click();
  await expect(page.locator('#workspace-message-input textarea')).toHaveValue('Keep this request');
  expect(f.data.posts).toEqual([]);
});

test('preview rollback keeps the route and existing draft', async ({page}) => {
  const f = workspaceFixture(); await mountWorkspace(page, false, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  await page.locator('#workspace-message-input textarea').fill('Keep through rollout');
  await page.locator('[data-shell-tools]').click();
  await page.getByRole('menuitem', {name: 'Classic layout'}).click();
  await expect(page).toHaveURL(new RegExp('workbench=0' + workspaceRoute));
  await expect(page.locator('[data-workbench-views]')).toHaveCount(0);
  await expect(page.locator('#workspace-message-input textarea')).toHaveValue('Keep through rollout');
  expect(f.data.posts).toEqual([]);
});
