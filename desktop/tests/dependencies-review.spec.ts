import {revealPmView} from './pm-navigation.ts';
import {mkdirSync} from 'node:fs';
import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture, demoRoute} from './demo/index.ts';
import {dependencyDocument, dependencyFixtureIds, dependencyImpact} from './demo/dependencies.ts';
import {demoProject} from './demo/project.ts';

test.describe.configure({mode: 'parallel'});
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
      if (failure) return {status: failure, data: {error: {code: 'DEPENDENCY_READ_FAILED', message: locale === 'en' ? 'Dependency read failed' : '無法讀取技術相依'}}};
      const doc = dependencyDocument(demoProject(locale));
      return {status: 200, data: options.projection ? options.projection(doc) : doc};
    }
    if (url.pathname.endsWith('/dependencies/impact')) {
      impactReads++;
      if (options.impact) return {status: 200, data: await options.impact(dependencyImpact(demoProject(locale), url.searchParams.get('dependency') || ''))};
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
  const entry = await revealPmView(page, 'dependencies');
  const read = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/dependencies'));
  await entry.click();
  await (await read).finished();
  await expect(page.locator('[data-dep-view] > [role=status]')).toBeEmpty();
  return {
    posts, fail: (status: number) => {failure = status;}, impacts: () => impactReads,
    event: async () => {cursor++; await expect.poll(() => ack, {timeout: 15_000}).toBe(cursor);},
  };
}

for (const locale of ['en', 'zh-TW'] as const) test.describe(`loading ${locale}`, () => {
test.use({locale, viewport: {width: 1440, height: 900}});
test(`loading has its own status until dependency history settles ${locale}`, async ({page}) => {
  const fixture = demoFixture({locale});
  let release!: () => void, held = false;
  const history = new Promise<void>(resolve => {release = resolve;});
  await mountConversation(page, false, async input => {
    if (new URL(input.path, 'http://fixture').pathname.endsWith('/dependencies/revisions')) {held = true; await history;}
    return fixture.dispatch(input);
  });
  await page.goto(demoRoute());
  await (await revealPmView(page, 'dependencies')).click();
  await expect.poll(() => held).toBe(true);
  const panel = page.locator('[data-dep-view]');
  const shot = async (state: string) => {
    const directory = process.env.BATC_PM_LOADING_SHOTS;
    if (!directory) return;
    mkdirSync(directory, {recursive: true});
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: width === 390 ? 844 : 900});
      await page.screenshot({path: `${directory}/dependencies-${locale}-${width}-${state}.png`, fullPage: true, animations: 'disabled'});
    }
  };
  try {
    // The panel owns one direct status region; its loading mask has a separate host.
    await expect(panel.locator(':scope > [role=status]')).toHaveCount(1);
    await expect(panel.getByRole('status')).toHaveCount(1);
    await expect(panel.locator('[data-pm-source-loading]')).toBeVisible();
    await expect(panel.locator('.dep-toolbar')).toBeHidden();
    await shot('loading');
  } finally {release();}
  await expect(panel.locator('.dep-toolbar')).toBeVisible();
  await expect(panel.locator('[data-pm-source-loading]')).toHaveCount(0);
  await expect(panel.locator(':scope > [role=status]')).toBeEmpty();
  await shot('loaded');
});
});

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

test('SF-6: valid selection semantics and heading hierarchy', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await mount(page);
  await expect(page.locator('[data-dep-view] button[aria-selected]')).toHaveCount(0);
  await expect(page.locator('[data-dep-matrix]')).not.toHaveAttribute('role', 'grid');
  await expect(page.locator('[data-dep-matrix]')).not.toHaveAttribute('tabindex', '0');
  await page.locator('[data-dep-details] > summary').click();
  await expect(page.getByRole('heading', {name: 'Captures', exact: true})).toHaveJSProperty('tagName', 'H2');
  await page.getByRole('radio', {name: 'Graph', exact: true}).click();
  expect(await page.locator('[data-dep-graph]').evaluate(node => node.textContent || '')).not.toMatch(/null|undefined/);
  const node = page.locator('[data-dep-node]').first();
  await node.focus();
  await page.keyboard.press('Enter');
  await expect(node).toHaveAttribute('aria-pressed', 'true');
});

test('M1: graph keyboard follows visual column and row order', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await mount(page);
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
      await expect(link).toHaveText(locale === 'en' ? 'Discuss' : '討論');
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
  await expect(page.locator('[data-dep-matrix-host]')).toBeHidden();
  await expect(page.locator('[data-dep-graph-host]')).toBeHidden();
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

for (const filter of ['relation', 'evidence', 'search']) test(`BLOCKER-6: graph filters cross-repository nodes by ${filter}`, async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await mount(page);
  await page.getByRole('radio', {name: 'Graph', exact: true}).click();
  await expect(page.locator(`[data-dep-node="c:${ids.billing}"]`)).toBeVisible();
  if (filter === 'relation') for (const relation of ['runtime', 'build', 'contract', 'reference']) await page.locator(`[data-dep-relation=${relation}]`).click();
  if (filter === 'evidence') await page.locator('[data-dep-evidence=declared]').click();
  if (filter === 'search') await page.locator('[data-dep-view]').getByRole('searchbox').fill('pytest');
  await expect(page.locator(`[data-dep-node="c:${ids.billing}"]`)).toHaveCount(0);
  await expect(page.locator('[data-dep-graph] path.dep-rel-contract')).toHaveCount(0);
  await expect(page.locator(`[data-dep-node="d:${ids.keys.pytest}"]`)).toBeVisible();
});

function largeDocument(doc: Document) {
  doc.components = Array.from({length: 520}, (_, index) => ({...doc.components[index % 2], component_id: `pmcomp_${(100 + index).toString(16).padStart(32, '0')}`, name: `service-${String(index + 1).padStart(3, '0')}`}));
  const samples = doc.edges;
  doc.edges = doc.components.map((row, index) => ({...samples[index % samples.length], component_id: row.component_id, edge_id: `pmde_${(100 + index).toString(16).padStart(32, '0')}`}));
  for (let index = 10; index < 25; index++) {
    const key = `pmdepkey_${index.toString(16).padStart(32, '0')}`;
    doc.dependencies.runtime_library.push({key, name: `pkg-${index}`, ecosystem: 'python', kinds: ['runtime_library']});
    doc.edges.push({...samples[1], edge_id: `pmde_${(1000 + index).toString(16).padStart(32, '0')}`, dependency_key: key, component_id: doc.components[0].component_id});
  }
  doc.declared = []; doc.cross_repository_edges = []; doc.external_components = [];
  doc.counts = {...doc.counts, components: 520, dependencies: 22, edges: doc.edges.length, declared: 0};
  return doc;
}

test('SF-2: matrix column cap keeps every shown component represented', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await mount(page, {projection: largeDocument});
  await expect(page.locator('[data-dep-matrix] tbody tr')).toHaveCount(12);
  await expect(page.locator('[data-dep-matrix] thead button')).toHaveCount(12);
  const represented = await page.locator('[data-dep-matrix] tbody tr').evaluateAll(rows => rows.every(row => row.querySelector('[data-dep-cell]')));
  expect(represented).toBe(true);
  await expect(page.locator('[data-dep-coverage]')).toContainText('12 of 520 loaded components shown');
  await expect(page.locator('[data-dep-coverage]')).toContainText('12 of 22 loaded dependencies shown');
  await expect(page.locator('[data-dep-show-all]')).toHaveText('All columns (22)');
  await page.locator('[data-dep-show-all]').click();
  await expect(page.locator('[data-dep-matrix] thead button')).toHaveCount(22);
  await expect(page.locator('[data-dep-list] .dep-group')).toHaveCount(0);
});

for (const width of [1440, 390]) test(`SF-2: bounded graph and list expose loaded limits at ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  await mount(page, {projection: largeDocument});
  if (width === 390) {
    await expect(page.locator('[data-dep-list] .dep-group')).toHaveCount(12);
    await expect(page.locator('[data-dep-show-rows]')).toBeVisible();
    await expect(page.locator('[data-dep-coverage]')).toContainText('12 of 520 loaded components shown');
  }
  await page.getByRole('radio', {name: 'Graph', exact: true}).click();
  expect(await page.locator('[data-dep-node]').count()).toBeLessThanOrEqual(60);
  expect(Number(await page.locator('[data-dep-graph] svg').getAttribute('height'))).toBeLessThan(3500);
  await expect(page.locator('[data-dep-coverage]')).toContainText('of 542 loaded nodes shown');
  await expect(page.locator('[data-dep-coverage]')).toContainText('Search or filter to narrow');
  await expect(page.locator('[data-dep-legend]')).toBeVisible();
  await expect(page.locator('[data-dep-show-rows]')).toBeVisible();
});

test('SF-7: graph shows relation and evidence legend', async ({page}) => {
  await mount(page);
  await page.getByRole('radio', {name: 'Graph', exact: true}).click();
  await expect(page.locator('[data-dep-legend]')).toBeVisible();
  await expect(page.locator('[data-dep-legend]')).toContainText('Contract');
  await expect(page.locator('[data-dep-legend]')).toContainText('Outline declared');
  await expect(page.locator('[data-dep-legend]')).toContainText('Dashed inferred');
});

for (const locale of ['en', 'zh-TW'] as const) test.describe(`polish ${locale}`, () => {
  test.use({locale: locale === 'zh-TW' ? 'zh-TW' : 'en-US'});
  test('BLOCKER-1: capture reason wraps outside header actions', async ({page}) => {
    await page.setViewportSize({width: 390, height: 844});
    await mount(page, {locale});
    const hint = page.locator('[data-dep-capture-reason]');
    await expect(hint).toBeVisible();
    await expect(page.locator('.ui-header-actions [data-dep-capture-reason]')).toHaveCount(0);
    const bounds = await hint.boundingBox();
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    expect(await hint.evaluate(node => getComputedStyle(node).whiteSpace)).toBe('normal');
    const refresh = await page.locator('[data-pm-refresh]').boundingBox();
    expect(refresh!.x + refresh!.width).toBeLessThanOrEqual(390);
  });

  test('M4: Graph label and omitted-relation copy describe the selection', async ({page}) => {
    let omit = false;
    const harness = await mount(page, {locale, projection: doc => omit ? {...doc, edges: [], declared: []} : doc});
    await expect(page.getByRole('radio', {name: locale === 'en' ? 'Graph' : '關係圖', exact: true})).toBeVisible();
    await page.locator(`[data-dep-matrix] [data-dep-focus="row:${ids.api}"]`).click();
    omit = true;
    await harness.event();
    await expect(page.locator('[data-dep-affected]')).toContainText(locale === 'en' ? 'More relations were omitted' : '還有未列入的關係');
    await expect(page.locator('[data-dep-affected]')).not.toContainText(locale === 'en' ? 'More tests' : '未列入的測試');
  });
});

for (const width of [1440, 768, 390]) test(`SF-9: form and graph controls meet target sizes at ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  await mount(page, {manage: true});
  await page.locator('[data-dep-view] .ui-disclosure').nth(1).locator('summary').click();
  const form = page.locator('[data-pm-action="pm.dependencies.declare"]');
  const inputBoxes = await form.locator('input, select').evaluateAll(nodes => nodes.map(node => ({height: node.getBoundingClientRect().height, width: node.getBoundingClientRect().width, font: parseFloat(getComputedStyle(node).fontSize)})));
  expect(inputBoxes.every(box => box.height >= (width <= 800 ? 44 : 32))).toBe(true);
  if (width === 390) expect(inputBoxes.every(box => box.font >= 16)).toBe(true);
  await page.getByRole('radio', {name: 'Graph', exact: true}).click();
  const buttons = await page.locator('.dep-graph-bar button').evaluateAll(nodes => nodes.map(node => ({width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height})));
  expect(buttons.every(box => box.height >= (width <= 800 ? 44 : 32) && box.width >= (width <= 800 ? 44 : 32))).toBe(true);
  const chips = await page.locator('.dep-chip').evaluateAll(nodes => nodes.map(node => {
    const box=node.getBoundingClientRect(),hit=getComputedStyle(node,'::before');
    return {height:box.height,targetHeight:box.height-parseFloat(hit.top)-parseFloat(hit.bottom),targetWidth:box.width};
  }));
  expect(chips.every(box => box.height === 24)).toBe(true);
  if(width<=800){
    expect(chips.every(box => box.targetHeight>=44 && box.targetWidth>=44)).toBe(true);
    const chip=page.locator('[data-dep-relation]').first(),box=(await chip.boundingBox())!;
    await expect(chip).toHaveAttribute('aria-pressed','true');
    await page.mouse.click(box.x+box.width/2,box.y-6);
    await expect(chip).toHaveAttribute('aria-pressed','false');
  }
});

test('VP-3: graph uses the available width and has a roomy zoom bar', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await mount(page);
  await page.getByRole('radio', {name: 'Graph', exact: true}).click();
  const box = (await page.locator('[data-dep-graph]').boundingBox())!;
  const picture = (await page.locator('[data-dep-graph] svg').boundingBox())!;
  expect(picture.width).toBeGreaterThanOrEqual(box.width * 0.85);
  const bar = await page.locator('.dep-graph-bar').boundingBox();
  expect(bar!.height).toBeGreaterThanOrEqual(32);
});

test('VP-4: selected matrix cell centres and frames its badge', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await mount(page);
  const cell = page.locator(`[data-dep-cell="${ids.api}|${ids.keys.pytest}"]`);
  await cell.click();
  const style = await cell.evaluate(node => {
    const css = getComputedStyle(node), badge = node.querySelector('.dep-mark')!.getBoundingClientRect(), box = node.getBoundingClientRect();
    return {align: css.justifyContent, ring: css.boxShadow, gap: Math.min(badge.x - box.x, badge.y - box.y, box.right - badge.right, box.bottom - badge.bottom)};
  });
  expect(style.align).toBe('center');
  expect(style.ring).not.toBe('none');
  expect(style.gap).toBeGreaterThanOrEqual(4);
});

test('M3: dark selected chips use the readable text token and meet 4.5 to 1', async ({page}) => {
  await page.emulateMedia({reducedMotion: 'reduce'});
  await mount(page);
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
  const colors = await page.locator('[data-dep-relation=test]').evaluate(node => {
    const rgb = (value: string) => value.match(/[\d.]+/g)!.map(Number);
    const css = getComputedStyle(node);
    return {fg: rgb(css.color), primary: rgb(getComputedStyle(document.body).color), bg: rgb(css.backgroundColor), canvas: rgb(getComputedStyle(document.body).backgroundColor)};
  });
  expect(colors.fg).toEqual(colors.primary);
  const luminance = (rgb: number[]) => rgb.slice(0, 3).map(value => value / 255).map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  const alpha = colors.bg[3] ?? 1;
  const background = colors.bg.slice(0, 3).map((value, index) => value * alpha + colors.canvas[index] * (1 - alpha));
  const a = luminance(colors.fg), b = luminance(background);
  expect((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)).toBeGreaterThanOrEqual(4.5);
});


test('P1: selection dims unrelated edges and keyboard focus does not', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await mount(page);
  await page.getByRole('radio', {name: 'Graph', exact: true}).click();
  const pytest = page.locator(`[data-dep-node="d:${ids.keys.pytest}"]`);
  await pytest.focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('[data-dep-mark=dim]')).toHaveCount(0);
  await expect(page.locator('[data-dep-inspector]')).toHaveCount(0);
  await page.locator(`[data-dep-node="d:${ids.keys.contract}"]`).click();
  const own = page.locator(`[data-dep-from="d:${ids.keys.contract}"][data-dep-to="c:${ids.api}"]`);
  const other = page.locator(`[data-dep-from="d:${ids.keys.pytest}"][data-dep-to="c:${ids.api}"]`);
  const external = page.locator(`[data-dep-from="c:${ids.billing}"][data-dep-to="c:${ids.api}"]`);
  await expect(own).toHaveCount(1);
  await expect(own).not.toHaveAttribute('data-dep-mark', 'dim');
  await expect(other).toHaveAttribute('data-dep-mark', 'dim');
  await expect(external).toHaveAttribute('data-dep-mark', 'dim');
  await expect(page.locator(`[data-dep-node="c:${ids.api}"]`)).not.toHaveAttribute('data-dep-mark', 'dim');
  await expect(pytest).toHaveAttribute('data-dep-mark', 'dim');
  const opacity = await other.evaluate(node => Number(getComputedStyle(node).opacity));
  const ownOpacity = await own.evaluate(node => Number(getComputedStyle(node).opacity));
  expect(opacity).toBeLessThan(0.5);
  expect(ownOpacity).toBeGreaterThan(0.9);
  expect(opacity).toBeCloseTo(await pytest.locator('.dep-node-shape').evaluate(node => Number(getComputedStyle(node).opacity)), 2);
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => document.documentElement.setAttribute('data-theme', value), theme);
    const ratio = await pytest.locator('.dep-node-name').evaluate(node => {
      const rgb = (value: string) => value.match(/[\d.]+/g)!.map(Number);
      const text = rgb(getComputedStyle(node).fill);
      const shape = getComputedStyle(node.parentElement!.querySelector('.dep-node-shape')!);
      const fill = rgb(shape.fill);
      const alpha = Number(shape.opacity);
      let host: Element | null = node.ownerSVGElement;
      let canvas = [241, 243, 246];
      while (host) {
        const color = getComputedStyle(host).backgroundColor;
        const parsed = color.match(/[\d.]+/g)?.map(Number);
        if (parsed && parsed.length >= 3 && color !== 'rgba(0, 0, 0, 0)') {canvas = parsed; break;}
        host = host.parentElement;
      }
      const blend = fill.slice(0, 3).map((channel, index) => channel * alpha + canvas[index] * (1 - alpha));
      const linear = (channel: number) => {const value = channel / 255; return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;};
      const luminance = (color: number[]) => 0.2126 * linear(color[0]) + 0.7152 * linear(color[1]) + 0.0722 * linear(color[2]);
      const left = luminance(text), right = luminance(blend);
      return (Math.max(left, right) + 0.05) / (Math.min(left, right) + 0.05);
    });
    expect(ratio).toBeGreaterThanOrEqual(4.5);
  }
  await page.keyboard.press('ArrowDown');
  await expect(page.locator(`[data-dep-node="d:${ids.keys.contract}"]`)).toHaveAttribute('aria-pressed', 'true');
  await expect(other).toHaveAttribute('data-dep-mark', 'dim');
});

test('P2: another repository keeps its own node, legend and counts', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await mount(page);
  await expect(page.locator('[data-dep-relation=contract]')).toHaveText('Contract 1');
  await expect(page.locator(`[data-dep-focus="col:${ids.keys.contract}"] [data-dep-peer]`)).toContainText('billing-api');
  await expect(page.locator(`[data-dep-focus="col:${ids.keys.contract}"] [data-dep-peer]`)).toContainText('example/billing');
  await expect(page.locator('[data-dep-legend]')).toContainText('Other repository');
  await page.getByRole('radio', {name: 'Graph', exact: true}).click();
  const node = page.locator(`[data-dep-node="c:${ids.billing}"]`);
  await expect(node).toHaveAttribute('data-dep-external', 'true');
  await expect(node).toHaveAttribute('aria-label', 'billing-api, example/billing');
  await expect(node.locator('.dep-node-repo')).toHaveText('example/billing');
  const fills = await page.locator('.dep-node-shape').evaluateAll(nodes => nodes.map(item => getComputedStyle(item).fill));
  expect(new Set(fills).size).toBeGreaterThan(1);
  expect(await node.locator('.dep-node-shape').evaluate(item => getComputedStyle(item).strokeDasharray)).not.toBe('none');
  await page.locator('[data-dep-relation=contract]').click();
  await expect(node).toHaveCount(0);
  await expect(page.locator('[data-dep-legend-external]')).toHaveCount(0);
  await expect(page.locator('[data-dep-relation=contract]')).toHaveText('Contract 1');
  await expect(page.locator('[data-dep-peer]')).toHaveCount(0);
});

test('P2: a peer without a repository name says it is another repository', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await mount(page, {projection: doc => ({...doc, external_components: doc.external_components.map(row => ({...row, repository: ''}))})});
  await page.getByRole('radio', {name: 'Graph', exact: true}).click();
  await expect(page.locator(`[data-dep-node="c:${ids.billing}"]`)).toHaveAttribute('aria-label', 'billing-api, Other repository');
  await expect(page.locator(`[data-dep-node="c:${ids.billing}"] .dep-node-repo`)).toHaveText('Other repository');
});

for (const locale of ['en', 'zh-TW'] as const) test.describe(`dependency clarity ${locale}`, () => {
  test.use({locale: locale === 'zh-TW' ? 'zh-TW' : 'en-US'});
  const copy = locale === 'en'
    ? {partial: 'Partial: only the loaded graph was checked; peer selection stopped early, and one manifest could not be read.', none: 'None listed in this capture. More may exist.', omitted: 'More were omitted.', recorded: 'None recorded in this capture', contract: 'Contract version', reference: 'Reference version', service: 'Service version', range: 'Allowed range', other: 'Other repository'}
    : {partial: '部分：只檢查了已載入的關係圖；跨儲存庫選取提前結束，而且有一份清單檔無法讀取。', none: '這次擷取沒有列出。可能還有其他。', omitted: '還有未列入的。', recorded: '這次擷取沒有記錄', contract: '契約版本', reference: '參考版本', service: '服務版本', range: '允許範圍', other: '其他儲存庫'};

  test('P2 legend names the other repository', async ({page}) => {
    await mount(page, {locale});
    await expect(page.locator('[data-dep-legend]')).toContainText(locale === 'en' ? 'Other repository' : copy.other);
  });

  test('P3 to P6: title, partial sentence, tests and kind labels', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    await mount(page, {locale, projection: doc => ({...doc, edges: doc.edges.map(edge => edge.dependency_key === ids.keys.smtp ? {...edge, version: '2'} : edge)})});
    await page.locator(`[data-dep-cell="${ids.api}|${ids.keys.contract}"]`).click();
    const panel = page.locator('[data-dep-inspector]');
    await expect(panel.locator('h2')).toHaveText('billing-contract');
    await expect(panel.locator('h2')).not.toContainText(locale === 'en' ? 'loaded' : '已載入');
    await expect(panel.locator('[data-dep-affected]')).toContainText(locale === 'en' ? '1 loaded' : '已載入 1');
    await expect(panel.locator('[data-dep-impact-summary]')).toHaveText(copy.partial);
    await expect(panel.locator('[data-dep-affected] [data-status=partial]')).toBeVisible();
    const reasons = panel.locator('[data-dep-impact-reasons]');
    await expect(reasons).toBeHidden();
    await panel.locator('[data-dep-affected] summary').click();
    await expect(reasons).toBeVisible();
    await expect(panel.locator('[data-dep-impact-reason=selection_budget]')).toBeVisible();
    await expect(panel.locator('[data-dep-impact-reason=unreadable_manifest]')).toBeVisible();
    await expect(panel.locator('[data-dep-impact-summary]')).not.toContainText(/selection_budget|unreadable_manifest/);
    await expect(panel.locator('[data-dep-tests]')).toHaveText(new RegExp(copy.none.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    await expect(panel.locator('dt', {hasText: copy.contract})).toBeVisible();
    await expect(panel.locator('.dep-facts')).toContainText('1');
    await expect(panel.locator('.dep-facts')).not.toContainText(copy.range);
    await page.keyboard.press('Escape');
    await page.locator(`[data-dep-cell="${ids.api}|${ids.keys.legacy}"]`).click();
    await expect(panel.locator('h2')).toHaveText('legacy-mailer');
    await expect(panel.locator('dt', {hasText: copy.reference})).toBeVisible();
    await page.keyboard.press('Escape');
    await page.locator(`[data-dep-cell="${ids.api}|${ids.keys.smtp}"]`).click();
    await expect(panel.locator('dt', {hasText: copy.service})).toBeVisible();
    await expect(panel.locator('.dep-facts dd').filter({hasText: /^2$/})).toHaveCount(1);
    await page.keyboard.press('Escape');
    await page.locator(`[data-dep-cell="${ids.api}|${ids.keys.pytest}"]`).click();
    await expect(panel.locator('dt', {hasText: copy.range})).toBeVisible();
    await expect(panel.locator('[data-dep-tests]')).toContainText('pytest');
    await expect(panel.locator('[data-dep-tests-note=omitted]')).toHaveText(copy.omitted);
  });

  test('P5: a complete capture with no matching hint says none were recorded', async ({page}) => {
    await mount(page, {locale, projection: doc => ({...doc, omitted_hints: 0, projection_omitted_collections: []})});
    await page.locator(`[data-dep-cell="${ids.api}|${ids.keys.contract}"]`).click();
    await expect(page.locator('[data-dep-tests-note=none]')).toHaveText(copy.recorded);
    await expect(page.locator('[data-dep-tests]')).not.toContainText(copy.none);
  });

  test('P6: the narrow list labels a bare contract version and the same peer', async ({page}) => {
    await page.setViewportSize({width: 390, height: 844});
    await mount(page, {locale});
    const row = page.locator(`[data-dep-pick="${ids.api}|${ids.keys.contract}"]`);
    await expect(row).toContainText(`${copy.contract} 1`);
    await expect(row.locator('[data-dep-peer]')).toContainText('example/billing');
    await expect(row.locator('.dep-pick-meta')).not.toHaveText(/^\s*1\s*$/);
  });
});

if (process.env.BATC_DEP_SHOTS === '1') for (const locale of ['en', 'zh-TW'] as const) {
  for (const variant of [{width: 1440, dark: false}, {width: 1000, dark: false}, {width: 768, dark: false}, {width: 390, dark: false}, {width: 1440, dark: true}]) {
    test(`screenshots: ${locale} ${variant.width} ${variant.dark ? 'dark' : 'light'}`, async ({browser, baseURL}) => {
      const {width, dark} = variant;
      const context = await browser.newContext({baseURL, locale: locale === 'en' ? 'en-US' : 'zh-TW', viewport: {width, height: width === 390 ? 844 : width === 768 ? 1024 : 900}, reducedMotion: 'reduce'});
      const page = await context.newPage();
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      const harness = await mount(page, {locale});
      await page.evaluate(value => document.documentElement.setAttribute('data-theme', value), dark ? 'dark' : 'light');
      const directory = process.env.BATC_DEP_SHOT_DIR || `${process.env.TMPDIR || '/tmp'}/shots`;
      mkdirSync(directory, {recursive: true});
      const shot = async (state: string) => {
        if (process.env.BATC_DEP_SHOT_STATE && state !== process.env.BATC_DEP_SHOT_STATE) return;
        expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
        const text = await page.locator('#main').innerText();
        expect(text).not.toMatch(/\b(?:undefined|null|NaN)\b|ui_reason_|dep_/);
        await page.evaluate(() => scrollTo(0, 0));
        await page.screenshot({path: `${directory}/dep-${locale}-${width}-${dark ? 'dark' : 'light'}-${state}.png`, fullPage: true, animations: 'disabled'});
      };
      await shot(width > 800 ? 'matrix' : 'list');
      await page.getByRole('radio', {name: locale === 'en' ? 'Graph' : '關係圖', exact: true}).click();
      await shot('graph');
      await page.locator(`[data-dep-node="d:${ids.keys.pytest}"]`).focus();
      await shot('graph-focus');
      await page.locator(`[data-dep-node="c:${ids.api}"]`).click();
      await expect(page.locator('[data-dep-component-facts]')).toBeVisible();
      await shot('component');
      await page.keyboard.press('Escape');
      await page.locator(`[data-dep-node="d:${ids.keys.contract}"]`).click();
      await expect(page.locator('[data-dep-impact-summary]')).toBeVisible();
      await page.locator('[data-dep-affected]').scrollIntoViewIfNeeded();
      await shot('partial-impact');
      await page.keyboard.press('Escape');
      await page.locator(`[data-dep-node="d:${ids.keys.legacy}"]`).click();
      await expect(page.locator('[data-dep-impact-declaration]')).toBeVisible();
      await page.locator('[data-dep-affected]').scrollIntoViewIfNeeded();
      await shot('declared-only');
      await page.keyboard.press('Escape');
      await page.getByRole('radio', {name: locale === 'en' ? 'Matrix' : '矩陣', exact: true}).click();
      await page.locator('[data-dep-record] > summary').click();
      await shot('record');
      harness.fail(404);
      await page.locator('[data-pm-refresh]').click();
      await expect(page.locator('.dep-toolbar')).toBeHidden();
      await shot('withdrawn');
      expect(harness.posts).toEqual([]);
      expect(errors).toEqual([]);
      await context.close();
    });
  }
}


test('BLOCKER-5: distinct declarations sharing a captured relation count and render', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await mount(page, {projection: doc => ({...doc, declared: [...doc.declared, {...doc.declared[0],
    declaration_id: doc.declared[0].declaration_id.slice(0, -1) + '2', dependency_key: ids.keys.httpx,
    dependency: {...doc.dependencies.runtime_library[0], kind: 'runtime_library', version: '>=0.27'},
    relation: 'runtime', evidence_level: 'inferred'}]})});
  await expect(page.locator('[data-dep-relation=runtime]')).toHaveText('Runtime 4');
  await expect(page.locator('[data-dep-evidence=inferred]')).toHaveText('Inferred 2');
  await expect(page.locator(`[data-dep-cell="${ids.api}|${ids.keys.httpx}"] .dep-mark`)).toHaveCount(2);
  await page.locator(`[data-dep-focus="row:${ids.api}"]`).click();
  await expect(page.locator(`[data-dep-dependency-row="${ids.keys.httpx}"]`)).toHaveCount(2);
});

test('BLOCKER-7: late impact cannot revive withdrawn data', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  let release: (() => void) | undefined;
  const harness = await mount(page, {manage: true, impact: doc => new Promise(resolve => {release = () => resolve(doc);})});
  await page.locator(`[data-dep-cell="${ids.api}|${ids.keys.pytest}"]`).click();
  await expect.poll(harness.impacts).toBe(1);
  harness.fail(403);
  await page.locator('[data-pm-refresh]').click();
  await expect(page.locator('[data-dep-inspector]')).toHaveCount(0);
  const response = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/dependencies/impact'));
  release!();
  await (await response).finished();
  await expect(page.locator('[data-dep-inspector], [data-dep-cell], [data-dep-node], [data-dep-pick]')).toHaveCount(0);
  await expect(page.locator('[data-pm-action="pm.dependencies.declare"] select[aria-label="Component"] option')).toHaveCount(0);
  await expect(page.locator('[data-pm-action="pm.dependencies.retract"] option')).toHaveCount(0);
  await expect(page.locator('.dep-toolbar')).toBeHidden();
  expect(harness.posts).toEqual([]);
});

for (const width of [1440, 1000, 390]) test(`VP-1: bounded relation form uses responsive columns at ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  await mount(page, {manage: true});
  await page.locator('[data-dep-view] .ui-disclosure').nth(1).locator('summary').click();
  const form = page.locator('[data-pm-action="pm.dependencies.declare"]');
  const positions = await form.locator('input').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().x));
  expect(new Set(positions).size).toBe(width === 390 ? 1 : 2);
  expect((await form.locator('.dep-declare-grid').boundingBox())!.width).toBeLessThanOrEqual(640);
});
