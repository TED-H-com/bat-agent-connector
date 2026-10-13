import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture, demoRoute} from './demo/index.ts';
import {dependencyDocument, dependencyFixtureIds, dependencyImpact} from './demo/dependencies.ts';
import {demoProject} from './demo/project.ts';

const ids = dependencyFixtureIds;
type Document = ReturnType<typeof dependencyDocument>;

async function mount(page: Page, options: {locale?: 'en' | 'zh-TW', manage?: boolean, projection?: (doc: Document) => unknown, impact?: (doc: ReturnType<typeof dependencyImpact>) => unknown} = {}) {
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
    if (url.pathname.endsWith('/dependencies/impact')) {
      impactReads++;
      if (options.impact) return {status: 200, data: options.impact(dependencyImpact(demoProject(locale), url.searchParams.get('dependency') || ''))};
    }
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
  const read = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/dependencies'));
  await entry.click();
  await (await read).finished();
  await expect(page.locator('[data-dep-view] > [role=status]')).toBeEmpty();
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

for (const locale of ['en', 'zh-TW'] as const) test.describe(locale, () => {
  test.use({locale: locale === 'zh-TW' ? 'zh-TW' : 'en-US'});
  test(`BLOCKER-4: component facts and named dependency evidence ${locale}`, async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    await mount(page, {locale});
    await page.locator(`[data-dep-focus="row:${ids.api}"]`).click();
    const panel = page.locator('[data-dep-inspector]');
    await expect(panel.getByRole('heading', {name: locale === 'en' ? 'Dependencies' : '技術相依', exact: true})).toBeVisible();
    await expect(panel).not.toContainText(locale === 'en' ? 'Affected components' : '受影響元件');
    await expect(panel.locator('[data-dep-component-facts]')).toContainText('api/pyproject.toml');
    const pytest = panel.locator(`[data-dep-dependency-row="${ids.keys.pytest}"]`);
    await expect(pytest).toContainText('pytest');
    await expect(pytest).toContainText('8.3.2');
    await expect(pytest).toContainText(locale === 'en' ? 'Test' : '測試');
    await expect(pytest).toContainText(locale === 'en' ? 'Verified' : '已核對');
    await expect(pytest.locator('dt')).toHaveCount(1);
    await expect(pytest).toContainText('api/uv.lock @ c0ffee1');
  });

  test(`BLOCKER-5: declarations count and bounded impact explains coverage ${locale}`, async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const harness = await mount(page, {
      locale,
      projection: doc => ({...doc, edges: doc.edges.filter(edge => edge.basis !== 'owner_declaration')}),
      impact: doc => ({...doc, reason: 'bounded_captured_graph', partial: true,
        declared: doc.dependency === ids.keys.legacy ? dependencyDocument(demoProject(locale)).declared : [],
        cross_repository: {status: 'partial', complete: false, reason: 'selection_budget', selection_reasons: ['selection_budget', 'unreadable_manifest']}}),
    });
    await expect(page.locator('[data-dep-relation=reference]')).toHaveText(locale === 'en' ? 'Reference 1' : '參考 1');
    await expect(page.locator('[data-dep-evidence=declared]')).toHaveText(locale === 'en' ? 'Declared 3' : '已宣告 3');
    await page.locator(`[data-dep-cell="${ids.api}|${ids.keys.legacy}"]`).click();
    const affected = page.locator('[data-dep-affected]');
    await expect(affected.locator('[data-status=partial]')).toBeVisible();
    await expect(affected).toContainText('legacy-mailer');
    await expect(affected).toContainText('membership-api');
    await expect(page.locator('[data-dep-inspector]')).not.toContainText(locale === 'en' ? '0 loaded' : '已載入 0');
    await expect(affected).toContainText(locale === 'en' ? 'Loaded graph only' : '僅已載入的圖');
    await expect(affected).toContainText(locale === 'en' ? 'Peer selection stopped early' : '跨儲存庫選取提前結束');
    await expect(affected).toContainText(locale === 'en' ? 'Manifest unreadable' : '無法讀取清單檔');
    await expect(affected).not.toContainText(/selection_budget|unreadable_manifest/);
    expect(harness.posts).toEqual([]);
  });

  test(`SF-4: capture details explain catalog gaps and unreadable parent path ${locale}`, async ({page}) => {
    await mount(page, {locale, projection: doc => ({...doc, catalog_reasons: ['tree_limit', 'file_limit', 'component_limit'],
      projection_omitted_collections: ['files'], cross_repository: {status: 'partial', complete: false, reason: 'selection_budget'},
      unreadable_paths: [{kind: 'entry', path: null, parent_path: 'services/secret-submodule', reason: 'unsupported_tree_entry'}]})});
    await page.locator('[data-dep-details] > summary').click();
    const details = page.locator('[data-dep-details]');
    await expect(details).toContainText(locale === 'en' ? 'Tree limit reached' : '已達目錄上限');
    await expect(details).toContainText(locale === 'en' ? 'File limit reached' : '已達檔案上限');
    await expect(details).toContainText(locale === 'en' ? 'Component limit reached' : '已達元件上限');
    await expect(details).toContainText(locale === 'en' ? 'Omitted collections: Files' : '未列入的資料：檔案清單');
    await expect(details).toContainText(locale === 'en' ? 'Peer selection stopped early' : '跨儲存庫選取提前結束');
    await expect(details.locator('[data-dep-file="services/secret-submodule"]')).toContainText(locale === 'en' ? 'Unsupported tree entry' : '不支援的目錄項目');
  });

  test(`SF-8: discussion names dependency or component and captured commit ${locale}`, async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    await mount(page, {locale});
    for (const [focus, name] of [[`col:${ids.keys.pytest}`, 'pytest'], [`row:${ids.api}`, 'membership-api']]) {
      await page.locator(`[data-dep-matrix] [data-dep-focus="${focus}"]`).click();
      const link = page.locator('[data-dep-discuss]');
      await expect(link).toHaveText(locale === 'en' ? 'Discuss with PM' : '與 PM 討論');
      const params = new URLSearchParams((await link.getAttribute('href'))!.split('?')[1]);
      expect(params.getAll('ref')).toEqual([]);
      expect(params.get('title')).toContain(name);
      expect(params.get('title')).toContain('c0ffee1');
    }
  });
});

test('SF-1: uncaptured inventory has no partial badge, zero chips or freshness claim', async ({page}) => {
  await mount(page, {projection: doc => ({...doc, capture: {revision_id: null, status: 'unknown'}, reason: 'not_captured',
    components: [], dependencies: {}, edges: [], declared: [], counts: null})});
  await expect(page.locator('.ui-page-header [data-status=partial]')).toHaveCount(0);
  await expect(page.locator('[data-dep-relation=test]')).toHaveText('Test');
  await page.locator('[data-dep-details] > summary').click();
  await expect(page.locator('[data-dep-freshness]')).toHaveCount(0);
  await expect(page.locator('[data-dep-view]')).toContainText('Not captured yet');
});

for (const status of [403, 404, 409, 413]) test(`BLOCKER-7: failed read ${status} clears options, forms and all source surfaces`, async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  const harness = await mount(page, {manage: true});
  const declare = page.locator('[data-pm-action="pm.dependencies.declare"]');
  const retract = page.locator('[data-pm-action="pm.dependencies.retract"]');
  await expect(declare.getByLabel('Component', {exact: true}).locator('option')).toHaveCount(2);
  await expect(retract.locator('option')).toHaveCount(1);
  await page.locator('[data-dep-view] .ui-disclosure').nth(1).locator('summary').click();
  await declare.getByLabel('Name', {exact: true}).fill('preserved-draft');
  await page.locator(`[data-dep-cell="${ids.api}|${ids.keys.pytest}"]`).click();
  harness.fail(status);
  await page.locator('[data-pm-refresh]').click();
  await expect(page.locator('[data-dep-inspector]')).toHaveCount(0);
  await expect(declare.getByLabel('Component', {exact: true}).locator('option')).toHaveCount(0);
  await expect(retract.locator('option')).toHaveCount(0);
  await expect(declare.locator('button[type=submit]')).toBeDisabled();
  await expect(retract.locator('button[type=submit]')).toBeDisabled();
  await expect(page.locator('[data-dep-capture]')).toBeDisabled();
  await expect(page.locator('.dep-toolbar')).toBeHidden();
  await expect(page.locator('[data-dep-details]')).toBeHidden();
  await expect(page.locator('[data-dep-record]')).toBeHidden();
  await expect(page.locator('[data-dep-cell], [data-dep-node], [data-dep-pick]')).toHaveCount(0);
  await expect(page.locator('[data-dependencies-meta]')).toBeHidden();
  // Retry calls Dependencies.refresh directly, rather than the workspace's form update.
  await page.locator('[data-dep-view] > [role=status]').getByRole('button', {name: 'Retry', exact: true}).click();
  await expect(declare.locator('button[type=submit]')).toBeDisabled();
  harness.fail(0);
  await page.locator('[data-dep-view] > [role=status]').getByRole('button', {name: 'Retry', exact: true}).click();
  await expect(page.locator('.dep-toolbar')).toBeVisible();
  await expect(declare.getByLabel('Name', {exact: true})).toHaveValue('preserved-draft');
  expect(harness.posts).toEqual([]);
});

test('SF-3 part 1: open inspector rereads impact and preserves its focused action', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  let version = 1;
  const harness = await mount(page, {impact: doc => ({...doc, components: [{...doc.components[0], name: `affected-${version}`}], counts: {components: 1, basis: 'returned_components', partial: true}})});
  await page.locator(`[data-dep-cell="${ids.api}|${ids.keys.pytest}"]`).click();
  await expect(page.locator('[data-dep-affected]')).toContainText('affected-1');
  const discuss = page.locator('[data-dep-discuss]');
  await discuss.focus();
  version++;
  await harness.event();
  await expect(page.locator('[data-dep-affected]')).toContainText('affected-2');
  await expect(discuss).toBeFocused();
  expect(harness.impacts()).toBeGreaterThanOrEqual(2);
  version++;
  await page.locator('[data-pm-refresh]').click();
  await expect(page.locator('[data-dep-affected]')).toContainText('affected-3');
});

test.describe('zh-TW draft', () => {
test.use({locale: 'zh-TW'});
test('SF-5: inferred draft restores translated relations and record form stays on page', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await mount(page, {locale: 'zh-TW', manage: true});
  // Use stable form fields here; the locale-specific option copy is checked after reload.
  const form = page.locator('[data-pm-action="pm.dependencies.declare"]');
  const selects = form.locator('select');
  await page.locator('[data-dep-view] .ui-disclosure').nth(1).locator('summary').click();
  await selects.nth(2).selectOption('inferred');
  await selects.nth(3).selectOption('runtime');
  await form.locator('input').first().fill('draft-pkg');
  await page.reload();
  await expect(selects.nth(2)).toHaveValue('inferred');
  await expect(selects.nth(3)).toHaveValue('runtime');
  await expect(selects.nth(3).locator('option')).toHaveText(['執行期', '建置', '測試', '契約', '參考']);
  await page.locator(`[data-dep-cell="${ids.api}|${ids.keys.pytest}"]`).click();
  await expect(page.locator('[data-dep-inspector] [data-pm-action]')).toHaveCount(0);
  await expect(page.locator('[data-dep-view] [data-pm-action="pm.dependencies.declare"]')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await expect(form.locator('input').first()).toHaveValue('draft-pkg');
});
});


test('M2: relation form labels source and declaration with a single primary action', async ({page}) => {
  await mount(page, {manage: true});
  await page.locator('[data-dep-view] .ui-disclosure').nth(1).locator('summary').click();
  await expect(page.locator('[data-dep-source-commit]')).toHaveText('Captured at c0ffee1');
  const retract = page.locator('[data-pm-action="pm.dependencies.retract"]');
  await expect(retract.getByLabel('Declaration', {exact: true})).toBeVisible();
  await expect(retract.locator('button[type=submit]')).toHaveClass(/secondary/);
  await expect(page.locator('[data-dep-record] button.primary[type=submit]')).toHaveCount(1);
});
