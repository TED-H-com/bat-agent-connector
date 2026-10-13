import {mkdirSync} from 'node:fs';
import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture, demoRoute} from './demo/index.ts';
import {dependencyFixtureIds} from './demo/dependencies.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';

const ids = dependencyFixtureIds;
const files = ['api/pyproject.toml', 'api/uv.lock', 'api/constraints.txt', 'web/package.json', 'web/package-lock.json', 'web/vendor.lock', 'tools/broken.toml', 'vendor/closed'];

function shotDir() {
  return process.env.BATC_DEP_SHOT_DIR || `${process.env.TMPDIR || '/tmp'}/shots`;
}

async function shot(page: Page, name: string) {
  if (process.env.BATC_DEP_SHOTS !== '1') return;
  mkdirSync(shotDir(), {recursive: true});
  await page.screenshot({path: `${shotDir()}/${name}.png`, fullPage: true});
}

async function expectNoOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

async function openView(page: Page, id: string) {
  await expect.poll(() => page.locator('[data-pm-view]').count()).toBeGreaterThan(0);
  const entry = page.locator(`[data-pm-view="${id}"]`);
  const narrow = (page.viewportSize()?.width ?? 0) < 1000;
  if (!await entry.isVisible()) {
    await page.locator('#workspace-menu').click();
    await expect(page.locator('.workspace-nav')).toHaveAttribute('aria-modal', 'true');
  }
  await entry.click();
  await expect(page.locator(`[data-pm-region="${id}"]`)).toBeVisible();
  if (narrow) await expect(page.locator('.workspace-nav')).toBeHidden();
}

function mount(page: Page, locale: 'en' | 'zh-TW', options: {stale?: boolean, fail?: boolean, hold?: string} = {}) {
  const fixture = demoFixture({locale});
  const posts: string[] = [];
  let dependencyReads = 0;
  let impactReads = 0;
  let releaseHold = () => {};
  let markHeld = () => {};
  const held = new Promise<void>(resolve => {markHeld = resolve;});
  const dispatch = async (input: {method?: string, path: string}) => {
    const url = new URL(input.path, 'http://fixture');
    const method = input.method || 'GET';
    if (method !== 'GET') posts.push(input.path);
    const impact = url.pathname.endsWith('/dependencies/impact');
    const revisions = url.pathname.endsWith('/dependencies/revisions');
    const inventory = url.pathname.endsWith('/dependencies') && !impact && !revisions;
    if (inventory) dependencyReads += 1;
    if (options.fail && inventory) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Not found'}}};
    if (options.hold && impact && url.searchParams.get('dependency') === options.hold) {
      markHeld();
      await new Promise<void>(resolve => {releaseHold = resolve;});
    }
    const result = await fixture.dispatch(input);
    if (options.stale && inventory && result.status === 200) result.data.stale = true;
    if (impact) impactReads += 1;
    return result;
  };
  return {fixture, posts, held, release: () => releaseHold(), reads: () => dependencyReads, impacts: () => impactReads, dispatch};
}

async function settle(page: Page, locale: 'en' | 'zh-TW') {
  const refresh = locale === 'zh-TW' ? '重新整理' : 'Refresh';
  await expect(page.locator('.ui-page-header').getByRole('button', {name: refresh, exact: true})).toBeEnabled();
}

test('filtering to test relations shows what to re-check, and the graph keeps that selection', async ({page}) => {
  test.setTimeout(60_000);
  const locale = 'en' as const;
  const harness = mount(page, locale);
  const errors: string[] = [];
  const httpErrors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
  page.on('response', response => {if (response.status() >= 400) httpErrors.push(`${response.status()} ${new URL(response.url()).pathname}`);});
  await page.setViewportSize({width: 1440, height: 900});
  await mountConversation(page, false, harness.dispatch);
  await page.goto(demoRoute());
  await settle(page, locale);
  expect(harness.reads()).toBe(0);

  await openView(page, 'dependencies');
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Dependencies');
  await expect(page.locator('[data-dependencies-meta]')).toHaveText('Captured at c0ffee1 · 9h ago');
  await expect(page.locator('.ui-page-header [data-status=partial]')).toBeVisible();
  await expect(page.locator('[data-status=stale]')).toHaveCount(0);
  await expect(page.locator('[data-dependencies-meta]')).not.toContainText(/fresh/i);
  await expect(page.locator('[data-dep-capture]')).toBeVisible();
  await expect(page.locator('[data-dep-capture]')).toBeDisabled();
  await expect(page.locator('[data-dep-capture]')).toHaveClass(/\bsecondary\b/);
  await expect(page.locator('[data-dep-capture]')).not.toHaveClass(/\bprimary\b/);
  await expect(page.locator('[data-dep-capture-reason]')).toHaveText('Capture needs manage access.');
  await expect(page.locator('[data-dep-capture]')).toHaveAttribute('aria-describedby', 'dep-capture-reason');
  await expect(page.locator('[data-dep-capture]')).toHaveAttribute('title', 'Capture needs manage access.');
  await expect(page.locator('.ui-header-actions button.primary:visible')).toHaveCount(0);
  await expect(page.locator('[data-dep-relation=test]')).toContainText('2');
  await expect(page.locator('.dep-filter-label').first()).toHaveText('Relation');
  await expect(page.locator('.dep-filter').nth(1).locator('.dep-filter-label')).toHaveText('Evidence');
  await expect(page.locator('[data-dep-matrix]')).toContainText('membership-api');
  await expect(page.locator('[data-dep-matrix]')).toContainText('membership-web');
  await expect(page.locator('[data-dep-matrix]')).toContainText('pytest');
  await expect(page.locator('[data-dep-matrix]')).toContainText('vitest');
  await expect(page.locator(`[data-dep-cell="${ids.web}|${ids.keys.vitest}"]`)).toContainText('T');
  await expect(page.locator(`[data-dep-cell="${ids.web}|${ids.keys.vite}"]`)).toContainText('B');
  await expect(page.locator(`[data-dep-cell="${ids.web}|${ids.keys.react}"]`)).toContainText('R');
  await expect(page.locator('[data-dep-show-all]')).toBeHidden();
  await expect(page.locator('[data-dep-legend]')).toContainText('Ref');
  await expect(page.locator('[data-dep-legend]')).toContainText('Solid verified');
  await expect(page.locator(`[data-dep-cell="${ids.api}|${ids.keys.httpx}"]`)).toHaveAttribute('aria-label', 'httpx · Runtime · >=0.27 · Declared');
  await expect(page.locator(`[data-dep-cell="${ids.api}|${ids.keys.legacy}"]`)).toContainText('Ref');
  const searchBox = await page.locator('.dep-search').boundingBox();
  const searchIcon = await page.locator('.dep-search .ui-icon').boundingBox();
  const searchInput = await page.locator('.dep-search input').boundingBox();
  expect(searchBox?.height ?? 0).toBeLessThanOrEqual(34);
  expect(searchIcon && searchInput && searchIcon.y >= searchInput.y - 1 && searchIcon.y + searchIcon.height <= searchInput.y + searchInput.height + 1).toBe(true);

  await page.locator('[data-dep-details] > summary').click();
  await expect(page.locator('[data-dep-freshness]')).toHaveText('Freshness is unknown: Head not observed');
  await expect(page.locator('[data-dep-omitted]')).toHaveText('7 omitted');
  await expect(page.locator('[data-dep-reason=future_parser_code]')).toHaveText('Not available (future_parser_code)');
  await expect(page.locator('[data-dep-reason=capture_budget]')).toHaveText('Capture stopped early');
  await expect(page.locator('[data-dep-matrix]')).not.toContainText('future_parser_code');
  for (const path of files) await expect(page.locator(`[data-dep-file="${path}"]`)).toBeVisible();
  await expect(page.locator('[data-dep-file="api/constraints.txt"]')).toContainText('Missing');
  await expect(page.locator('[data-dep-file="web/vendor.lock"]')).toContainText('Too large');
  await expect(page.locator('[data-dep-file="tools/broken.toml"]')).toContainText('Unparsable');
  await expect(page.locator('[data-dep-file="vendor/closed"]')).toContainText('Unreadable');
  await expect(page.locator('[data-pm-action="pm.dependencies.declare"] button[type=submit]')).toBeDisabled();
  await expect(page.locator('[data-pm-action="pm.dependencies.retract"] button[type=submit]')).toBeDisabled();

  const legacy = page.locator(`[data-dep-cell="${ids.api}|${ids.keys.legacy}"]`);
  await legacy.click();
  await expect(page.locator('[data-dep-affected]')).toContainText('Not in this capture');
  await expect(page.locator('[data-dep-affected]')).not.toContainText('0 loaded');
  await expect(page.locator('[data-dep-affected]')).not.toContainText('no impact');
  const missed = parseDiscussRefs(new URL(await page.locator('[data-dep-discuss]').getAttribute('href') || '', 'http://local').hash.split('?')[1] || '');
  expect(missed.refs).toEqual([]);
  expect(missed.from).toBe('dependencies');
  expect(missed.title).toContain('legacy-mailer');
  expect(missed.title).toContain('Not in this capture');
  expect(missed.title.length).toBeLessThanOrEqual(160);

  for (const relation of ['runtime', 'build', 'contract', 'reference']) await page.locator(`[data-dep-relation=${relation}]`).click();
  await expect(page.locator('[data-dep-relation=test]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-dep-relation=runtime]')).toHaveAttribute('aria-pressed', 'false');
  const pytest = page.locator(`[data-dep-cell="${ids.api}|${ids.keys.pytest}"]`);
  await pytest.click();
  await expect(page.locator('[data-dep-tests]')).toContainText('python');
  await expect(page.locator('[data-dep-tests]')).toContainText('pytest');
  await expect(page.locator('[data-dep-affected]')).toContainText('membership-api');
  await expect(page.locator('[data-dep-affected]')).toContainText('1 loaded');
  await expect(page.locator('[data-dep-inspector]')).toContainText('8.3.2');
  await expect(page.locator('[data-dep-inspector]')).toContainText('api/uv.lock @ c0ffee1');
  const href = await page.locator('[data-dep-discuss]').getAttribute('href');
  const parsed = parseDiscussRefs(new URL(href || '', 'http://local').hash.split('?')[1] || '');
  expect(parsed.refs).toEqual([]);
  expect(parsed.from).toBe('dependencies');
  expect(parsed.title).toContain('pytest');
  expect(parsed.title).toContain('1 loaded');
  expect(parsed.title.length).toBeLessThanOrEqual(160);

  await page.getByRole('radio', {name: 'Graph', exact: true}).click();
  await expect(page.locator(`[data-dep-node="d:${ids.keys.pytest}"]`)).toHaveAttribute('data-dep-mark', 'selected');
  await expect(page.locator('[data-dep-loaded]')).toHaveText('Loaded');
  await page.getByRole('radio', {name: 'Matrix', exact: true}).click();
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-dep-inspector]')).toHaveCount(0);
  await pytest.focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('[data-dep-inspector]')).toHaveCount(0);
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('Enter');
  await expect(page.locator('[data-dep-inspector]')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(pytest).toBeFocused();

  await openView(page, 'overview');
  await expect(page.locator('[data-dep-capture]')).toBeHidden();
  await openView(page, 'dependencies');
  await expect(page.locator('[data-dep-relation=test]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-dep-relation=runtime]')).toHaveAttribute('aria-pressed', 'false');
  await expect(pytest).toHaveAttribute('aria-selected', 'true');
  await expectNoOverflow(page);
  const text = await page.locator('#main').innerText();
  expect(text).not.toMatch(/\b(?:undefined|null|NaN)\b/);
  expect(text).not.toMatch(/dep_|ui_reason_/);
  expect(harness.posts).toEqual([]);
  expect(httpErrors).toEqual(['404 /api/v1/browser-session']);
  expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
});

test('a stale capture shows the pill and an unknown capture does not', async ({page}) => {
  const harness = mount(page, 'en', {stale: true});
  await page.setViewportSize({width: 1440, height: 900});
  await mountConversation(page, false, harness.dispatch);
  await page.goto(demoRoute());
  await openView(page, 'dependencies');
  await expect(page.locator('.ui-page-header [data-status=stale]')).toHaveText('Stale');
  await expect(page.locator('[data-dep-capture]')).toBeVisible();
  await page.locator('[data-dep-details] > summary').click();
  await expect(page.locator('[data-dep-freshness]')).toHaveCount(0);
  await expect(page.locator('.ui-page-header')).not.toContainText(/fresh/i);
});

test('a withdrawn dependency read removes rows, counts and the inspector together', async ({page}) => {
  let fail = false;
  const fixture = demoFixture({locale: 'en'});
  const dispatch = async (input: {method?: string, path: string}) => {
    const url = new URL(input.path, 'http://fixture');
    const inventory = url.pathname.endsWith('/dependencies') && !url.pathname.endsWith('/impact') && !url.pathname.endsWith('/revisions');
    if (fail && inventory) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Not found'}}};
    return fixture.dispatch(input);
  };
  await page.setViewportSize({width: 1440, height: 900});
  await mountConversation(page, false, dispatch);
  await page.goto(demoRoute());
  await openView(page, 'dependencies');
  await page.locator(`[data-dep-cell="${ids.api}|${ids.keys.pytest}"]`).click();
  await expect(page.locator('[data-dep-inspector]')).toBeVisible();
  fail = true;
  await page.locator('.ui-page-header').getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect(page.locator('[data-dep-cell]')).toHaveCount(0);
  await expect(page.locator('[data-dep-inspector]')).toHaveCount(0);
  await expect(page.locator('[data-dep-view]')).not.toContainText('pytest');
  await expect(page.locator('[data-dep-view]')).not.toContainText('8.3.2');
  await expect(page.locator('[data-dep-relation=test]')).toHaveText('Test');
  await expect(page.locator('.ui-page-header [data-status=partial]')).toHaveCount(0);
});

test('a late impact response does not replace the dependency now on screen', async ({page}) => {
  const harness = mount(page, 'en', {hold: ids.keys.pytest});
  await page.setViewportSize({width: 1440, height: 900});
  await mountConversation(page, false, harness.dispatch);
  await page.goto(demoRoute());
  await openView(page, 'dependencies');
  for (const relation of ['runtime', 'build', 'contract', 'reference']) await page.locator(`[data-dep-relation=${relation}]`).click();
  const pytest = page.locator(`[data-dep-cell="${ids.api}|${ids.keys.pytest}"]`);
  const vitest = page.locator(`[data-dep-cell="${ids.web}|${ids.keys.vitest}"]`);
  await pytest.click();
  await harness.held;
  await vitest.click();
  await expect(page.locator('[data-dep-affected]')).toContainText('membership-web');
  const before = harness.impacts();
  harness.release();
  await expect.poll(() => harness.impacts()).toBeGreaterThan(before);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(undefined)))));
  await expect(page.locator('[data-dep-inspector] h2')).toContainText('vitest');
  await expect(page.locator('[data-dep-affected]')).toContainText('membership-web');
  await expect(page.locator('[data-dep-affected]')).not.toContainText('membership-api');
});

for (const locale of ['en', 'zh-TW'] as const) {
  for (const width of [1440, 768, 390]) {
    test(`dependencies layout ${locale} at ${width}`, async ({browser, baseURL}) => {
      const context = await browser.newContext({
        locale: locale === 'zh-TW' ? 'zh-TW' : 'en-US', baseURL,
        viewport: {width, height: width === 390 ? 844 : width === 768 ? 1024 : 900},
      });
      const page = await context.newPage();
      const harness = mount(page, locale);
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => {if (message.type() === 'error' && !message.text().startsWith('Failed to load resource:')) errors.push(message.text());});
      await mountConversation(page, false, harness.dispatch);
      await page.goto(demoRoute());
      await openView(page, 'dependencies');
      const zh = locale === 'zh-TW';
      await expect(page.getByRole('heading', {level: 1})).toHaveText(zh ? '技術相依' : 'Dependencies');
      await expect(page.locator('[data-dependencies-meta]')).toContainText(zh ? '擷取於 c0ffee1 · 9 小時前' : 'Captured at c0ffee1 · 9h ago');
      await expect(page.locator('[data-dep-capture]')).toHaveText(zh ? '再次擷取' : 'Capture again');
      const searchBox = await page.locator('.dep-search').boundingBox();
      const searchIcon = await page.locator('.dep-search .ui-icon').boundingBox();
      const searchInput = await page.locator('.dep-search input').boundingBox();
      expect(searchBox && searchIcon && searchInput).toBeTruthy();
      expect(Math.abs((searchBox?.height ?? 0) - (width <= 800 ? 44 : 32))).toBeLessThanOrEqual(2);
      expect(searchIcon!.y).toBeGreaterThanOrEqual(searchInput!.y - 1);
      expect(searchIcon!.y + searchIcon!.height).toBeLessThanOrEqual(searchInput!.y + searchInput!.height + 1);
      expect(searchIcon!.x).toBeGreaterThanOrEqual(searchInput!.x);
      expect(searchIcon!.x).toBeLessThan(searchInput!.x + 28);
      if (width > 800) {
        await expect(page.locator('[data-dep-matrix]')).toBeVisible();
        await expect(page.locator('[data-dep-matrix]')).toContainText('membership-api');
        await expect(page.locator('[data-dep-matrix]')).toContainText('membership-web');
        await expect(page.locator('[data-dep-matrix]')).toContainText('vitest');
        await expect(page.locator('[data-dep-show-all]')).toBeHidden();
        await expect(page.locator('[data-dep-legend]')).toBeVisible();
        await expect(page.locator('[data-dep-legend]')).toContainText(zh ? '實線為已核對' : 'Solid verified');
        await expect(page.locator('.dep-filter-label').nth(0)).toHaveText(zh ? '關係' : 'Relation');
        await expect(page.locator('.dep-filter-label').nth(1)).toHaveText(zh ? '證據' : 'Evidence');
      } else {
        await expect(page.locator('[data-dep-matrix]')).toBeHidden();
        await expect(page.locator('[data-dep-list]')).toBeVisible();
        await expect(page.locator('[data-dep-list]')).toContainText('membership-api');
        await expect(page.locator('[data-dep-list]')).toContainText('membership-web');
        await expect(page.locator('[data-dep-list]')).toContainText('vitest');
        await expect(page.locator('.dep-filter-label').nth(0)).toHaveText(zh ? '關係' : 'Relation');
        await expect(page.locator('.dep-filter-label').nth(1)).toHaveText(zh ? '證據' : 'Evidence');
      }
      await expectNoOverflow(page);
      const text = await page.locator('#main').innerText();
      expect(text).not.toMatch(/\b(?:undefined|null|NaN)\b/);
      expect(text).not.toMatch(/dep_|ui_reason_/);
      if (zh) {
        expect(text).toContain('矩陣');
        expect(text).not.toMatch(/\b(?:Capture|Matrix|Stale|Partial|Loaded|Dependencies)\b/);
      }
      expect(errors).toEqual([]);
      await shot(page, `dependencies-${locale}-${width}`);
      if (locale === 'en' && width === 1440) {
        await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
        await shot(page, 'dependencies-en-1440-dark');
        await page.getByRole('radio', {name: 'Graph', exact: true}).click();
        await expect(page.locator('[data-dep-loaded]')).toHaveText('Loaded');
        await expect(page.getByRole('button', {name: 'Zoom out', exact: true})).toBeVisible();
        await expect(page.getByRole('button', {name: 'Zoom in', exact: true})).toBeVisible();
        const viewBox = await page.locator('[data-dep-view]').boundingBox();
        const graphBox = await page.locator('[data-dep-graph]').boundingBox();
        expect((graphBox?.width ?? 0)).toBeGreaterThan((viewBox?.width ?? 1) * 0.9);
        await expectNoOverflow(page);
        await shot(page, 'dependencies-en-1440-graph');
      }
      await context.close();
    });
  }
}
