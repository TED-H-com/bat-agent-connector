import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture, demoRoute} from './demo/index.ts';
import {dependencyDocument, dependencyFixtureIds} from './demo/dependencies.ts';
import {demoProject} from './demo/project.ts';

const ids = dependencyFixtureIds;
type Document = ReturnType<typeof dependencyDocument>;

async function mount(page: Page, options: {locale?: 'en' | 'zh-TW', manage?: boolean, projection?: (doc: Document) => unknown} = {}) {
  const locale = options.locale || 'en';
  const fixture = demoFixture({locale});
  let cursor = 0, ack = 0, failure = 0, impactReads = 0;
  const posts: string[] = [];
  const dispatch = async (input: {method?: string, path: string}) => {
    const url = new URL(input.path, 'http://fixture');
    if ((input.method || 'GET') !== 'GET') posts.push(input.path);
    if (url.pathname === '/events') {
      const after = Number(url.searchParams.get('after') || 0);
      ack = Math.max(ack, after);
      const events = after < cursor ? [{seq: cursor, resource_type: 'pm_dependencies', resource_id: demoProject(locale).ids.workspaceId, kind: 'pm_dependencies.updated'}] : [];
      return {status: 200, data: {events, next_cursor: cursor, head_cursor: cursor, has_more: false, sync: {checkpoint: {cursor, token: `demo-${cursor}`}}}};
    }
    if (url.pathname.endsWith('/dependencies')) {
      if (failure) return {status: failure, data: {error: {code: 'DEPENDENCY_READ_FAILED', message: 'Dependency read failed'}}};
      const doc = dependencyDocument(demoProject(locale));
      return {status: 200, data: options.projection ? options.projection(doc) : doc};
    }
    if (url.pathname.endsWith('/dependencies/impact')) impactReads++;
    const result = await fixture.dispatch(input);
    if (options.manage && ['/capabilities', '/bootstrap'].includes(url.pathname)) {
      const caps = url.pathname === '/bootstrap' ? result.data.capabilities : result.data;
      caps.scopes = ['observe', 'operate', 'manage', 'admin'];
      for (const action of ['pm.dependencies.capture', 'pm.dependencies.declare', 'pm.dependencies.retract']) {
        const row = caps.actions.find((row: {action: string}) => row.action === action);
        if (row) row.allowed = true; else caps.actions.push({action, allowed: true});
      }
    }
    return result;
  };
  await mountConversation(page, false, dispatch);
  await page.goto(demoRoute());
  const entry = page.locator('[data-pm-view="dependencies"]');
  await expect(entry).toBeAttached();
  if (!await entry.isVisible()) await page.locator('#workspace-menu').click();
  await entry.click();
  await expect(page.locator('[data-dependencies-meta]')).toBeVisible();
  return {
    posts, fail: (status: number) => {failure = status;}, impacts: () => impactReads,
    event: async () => {cursor++; await expect.poll(() => ack, {timeout: 15_000}).toBe(cursor);},
  };
}

test('BLOCKER-2: graph activation, Escape and live reads retain logical focus', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  const harness = await mount(page);
  await page.getByRole('radio', {name: 'Graph', exact: true}).click();
  const pytest = page.locator(`[data-dep-node="d:${ids.keys.pytest}"]`);
  const vitest = page.locator(`[data-dep-node="d:${ids.keys.vitest}"]`);
  await pytest.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('[data-dep-inspector]')).toBeVisible();
  await vitest.focus();
  await page.keyboard.press('Space');
  await expect(vitest).toBeFocused();
  await harness.event();
  await expect(vitest).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(vitest).toBeFocused();
  await harness.event();
  await expect(vitest).toBeFocused();
  expect(harness.posts).toEqual([]);
});

for (const width of [768, 390]) test(`BLOCKER-3: refreshed list sheet returns focus at ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  const harness = await mount(page);
  const pick = page.locator(`[data-dep-pick="${ids.api}|${ids.keys.pytest}"]`);
  await pick.click();
  await expect(page.locator('[data-dep-inspector]')).toBeVisible();
  await harness.event();
  await page.keyboard.press('Escape');
  await expect(pick).toBeFocused();
});

test('SF-6 and M1: valid selection semantics and graph keyboard follows visual order', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await mount(page);
  await expect(page.locator('[data-dep-view] button[aria-selected]')).toHaveCount(0);
  await expect(page.locator('[data-dep-matrix]')).not.toHaveAttribute('role', 'grid');
  await expect(page.locator('[data-dep-matrix]')).not.toHaveAttribute('tabindex', '0');
  await page.locator('[data-dep-details] > summary').click();
  await expect(page.getByRole('heading', {name: 'Captures', exact: true})).toHaveJSProperty('tagName', 'H2');
  await page.getByRole('radio', {name: 'Graph', exact: true}).click();
  const order = await page.locator('[data-dep-node]').evaluateAll(nodes => nodes.map(node => {
    const rect = node.getBoundingClientRect(); return {key: node.getAttribute('data-dep-node'), x: rect.x, y: rect.y};
  }));
  expect(order).toEqual([...order].sort((a, b) => a.x - b.x || a.y - b.y));
  const nodes = page.locator('[data-dep-node]');
  await nodes.first().focus();
  await page.keyboard.press('End');
  await expect(nodes.last()).toBeFocused();
  await page.keyboard.press('Home');
  await expect(nodes.first()).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(nodes.first()).toHaveAttribute('aria-pressed', 'true');
});
