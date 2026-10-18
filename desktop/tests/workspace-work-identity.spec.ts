import {test, expect} from '@playwright/test';
import {workspaceFixture, mountWorkspace, workspaceRoute} from './workspace-fixture';

const workspace = 'ws_' + 'c'.repeat(32), attempt = 'att_' + 'd'.repeat(32);
function fixture() {
  const f = workspaceFixture();
  const item = {...f.fixture.data.items[0], title: 'Repair login'};
  f.data.detail.work_items = [item];
  f.data.work.title = item.title;
  (f.data.work as any).work_identity = {workspace_id: workspace, work_item_id: item.work_item_id, attempt_id: attempt};
  f.data.detail.work[1].title = item.title; // Same title, independent source: never merge it.
  return {f, item};
}

for (const native of [false, true]) test(`${native ? 'IPC' : 'HTTP'} Work navigation joins only proven identity`, async ({page}) => {
  const {f, item} = fixture();
  await mountWorkspace(page, native, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  const tree = page.locator('.workspace-tree-scroll > .workspace-tree');
  await expect(tree.getByRole('link', {name: /Repair login/})).toHaveCount(2);
  const work = tree.locator(`[data-work-item="${item.work_item_id}"]`);
  await expect(work).toHaveAttribute('aria-current', 'page');
  await expect(work).toHaveAttribute('href', `#/session/demo/search-results?workspace=${workspace}&work=${item.work_item_id}&attempt=${attempt}`);
  await work.focus();
  f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect.poll(() => f.data.after).toBe(1);
  await expect(work).toBeFocused();
  delete (f.data.work as any).work_identity;
  f.data.events.push({seq: 2, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect(tree.getByRole('link', {name: /Repair login/})).toHaveCount(3);
  await expect(work).toBeFocused();
  expect(f.data.posts).toEqual([]);
});

for (const kind of ['unknown-work', 'invalid-attempt', 'invalid-workspace']) test(`Work navigation retains ${kind} sources`, async ({page}) => {
  const {f} = fixture();
  const identity = (f.data.work as any).work_identity;
  if (kind === 'unknown-work') identity.work_item_id = 'wi_' + 'f'.repeat(20);
  else if (kind === 'invalid-attempt') identity.attempt_id = 'not-an-attempt';
  else identity.workspace_id = 'not-a-workspace';
  await mountWorkspace(page, false, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  await expect(page.locator('.workspace-tree-scroll > .workspace-tree').getByRole('link', {name: /Repair login/})).toHaveCount(3);
  expect(f.data.posts).toEqual([]);
});

test('An older execution link keeps its exact Attempt in the single Work row', async ({page}) => {
  const {f, item} = fixture();
  const newer = {...f.data.work, id: 'op_' + 'e'.repeat(32), operation_id: 'op_' + 'e'.repeat(32),
    session_id: 'newer-search', work_identity: {...(f.data.work as any).work_identity, attempt_id: 'att_' + 'e'.repeat(32)}};
  f.data.detail.work.unshift(newer);
  await mountWorkspace(page, false, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  const tree = page.locator('.workspace-tree-scroll > .workspace-tree');
  await expect(tree.getByRole('link', {name: /Repair login/})).toHaveCount(2);
  const work = tree.locator(`[data-work-item="${item.work_item_id}"]`);
  await expect(work).toHaveAttribute('aria-current', 'page');
  await expect(work).toHaveAttribute('href', `#/session/demo/search-results?workspace=${workspace}&work=${item.work_item_id}&attempt=${attempt}`);
  expect(f.data.posts).toEqual([]);
});

for (const width of [390, 768, 1440]) test(`Work identity navigation layout ${width}`, async ({page}) => {
  const {f} = fixture();
  await page.setViewportSize({width, height: width === 390 ? 844 : 900});
  await mountWorkspace(page, false, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  if (width < 1000) await page.locator('.wc-list-back').click();
  const tree = page.locator('.workspace-tree-scroll > .workspace-tree');
  await expect(tree.getByRole('link', {name: /Repair login/}).first()).toBeVisible();
  await page.screenshot({path: `test-results/work-identity-${width}.png`});
  await expect(tree.getByRole('link', {name: /Repair login/})).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
