import {test, expect, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';

const copy = {
  en: {board: 'Board', overview: 'Overview', discussions: 'Discussions', fallback: 'Project discussions', stale: 'Could not confirm the current version'},
  'zh-TW': {board: '工作看板', overview: '專案總覽', discussions: '討論', fallback: '專案討論', stale: '目前無法確認最新版本'},
} as const;

type Locale = keyof typeof copy;
type ViewName = 'board' | 'overview' | 'discussions';

function routeFor(locale: Locale, view: ViewName) {
  const ids = demoFixture({locale}).ids;
  const hash = view === 'discussions'
    ? `#/pm/${ids.workspaceId}/discussions/${ids.threadIdea}`
    : `#/pm/${ids.workspaceId}/${view}`;
  return {ids, hash, url: `/dashboard/?workbench=1${hash}`};
}

function holdWorkspaceRead(locale: Locale, fail: boolean) {
  const base = demoFixture({locale});
  let held = false;
  let release = () => {};
  const gate = new Promise<void>(resolve => {release = resolve;});
  let armHold = !fail;
  const posts: string[] = [];
  const dispatch = async (input: {method?: string, path: string, body?: unknown, idempotency_key?: string}) => {
    const method = input.method || 'GET';
    if (method !== 'GET') posts.push(input.path);
    const path = new URL(input.path, 'http://fixture').pathname;
    if (method === 'GET' && path === '/pm/workspaces') {
      if (armHold) {armHold = false; held = true; await gate;}
      if (fail) return {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'PM read unavailable'}}};
    }
    return base.dispatch(input);
  };
  return {dispatch, posts, isHeld: () => held, release: () => release()};
}

async function openProject(page: Page, locale: Locale, width: number, url: string, dispatch: (input: any) => Promise<any>) {
  await page.setViewportSize({width, height: width < 600 ? 844 : 900});
  await page.addInitScript(language => Object.defineProperty(navigator, 'language', {value: language}), locale);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(String(error)));
  await mountConversation(page, false, dispatch);
  await page.goto(url);
  return errors;
}

// Same reachability the product uses for Tab order: a hidden or disabled control is skipped.
async function managementTabState(page: Page) {
  return page.locator('select[data-workbench-management]').evaluate((el: HTMLSelectElement) => {
    const tabbable = (node: Element) => node instanceof HTMLElement && node.getClientRects().length > 0
      && !node.closest('[hidden], [inert]') && getComputedStyle(node).visibility === 'visible'
      && !(node as HTMLButtonElement).disabled && node.tabIndex >= 0;
    const stops = [...document.querySelectorAll('a[href], button, input, select, textarea, summary, [tabindex="0"]')].filter(tabbable);
    const index = stops.indexOf(el);
    if (index > 0) (stops[index - 1] as HTMLElement).focus();
    return {index, options: el.options.length, shown: tabbable(el)};
  });
}

async function expectEmptySelectSkipped(page: Page) {
  const select = page.locator('select[data-workbench-management]');
  await expect(select).toHaveCount(1);
  const state = await managementTabState(page);
  if (state.index <= 0) await page.locator('#live').focus();
  await page.keyboard.press('Tab');
  const landed = await select.evaluate(el => document.activeElement === el);
  const empty = state.options === 0;
  expect({
    emptyVisible: empty && state.shown,
    emptyInTabOrder: empty && state.index >= 0,
    tabMovedToEmptySelect: empty && landed,
  }, 'an empty view select must stay hidden and out of the tab order').toEqual({
    emptyVisible: false, emptyInTabOrder: false, tabMovedToEmptySelect: false,
  });
  await expect(select).not.toBeFocused();
}

for (const [locale, width, view] of [
  ['en', 1440, 'board'],
  ['zh-TW', 390, 'overview'],
  ['en', 1440, 'discussions'],
  ['zh-TW', 390, 'discussions'],
] as const) {
  test(`project header names ${view} while the first workspace read is held (${locale} ${width})`, async ({page}) => {
    const labels = copy[locale];
    const title = labels[view];
    const {hash, url} = routeFor(locale, view);
    const fixture = holdWorkspaceRead(locale, false);
    const errors = await openProject(page, locale, width, url, fixture.dispatch);
    await expect.poll(fixture.isHeld).toBe(true);
    const heading = page.locator('.pm-main > .ui-page-header h1');
    const routeLabel = page.locator(width >= 1000 ? '.shell-crumb-view' : '.shell-mobile-name');
    await expect(heading).toBeVisible();
    await expect(routeLabel).toHaveText(title);
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(hash);
    // The shell already names this view. The page title must use that label before the read returns.
    await expect.soft(heading).toHaveText(title);
    await expectEmptySelectSkipped(page);
    const live = page.locator('#live');
    await live.focus();
    await expect(live).toBeFocused();
    fixture.release();
    const refresh = page.locator('[data-pm-refresh]');
    await expect(refresh).toBeEnabled();
    await expect(heading).toHaveText(title);
    const select = page.locator('select[data-workbench-management]');
    await expect(select).toBeVisible();
    await expect(select).toHaveValue(view);
    await expect(select.locator('option:checked')).toHaveText(title);
    await expect(page.locator(`[data-pm-view="${view}"] > .shell-view-copy > span:first-child`).first()).toHaveText(title);
    await expect(live).toBeFocused();
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(hash);
    expect(fixture.posts).toEqual([]);
    expect(errors).toEqual([]);
  });
}

for (const [locale, width, view] of [
  ['en', 1440, 'board'],
  ['zh-TW', 390, 'overview'],
] as const) {
  test(`a failed workspace read keeps the discussions fallback (${locale} ${width})`, async ({page}) => {
    const labels = copy[locale];
    const {hash, url} = routeFor(locale, view);
    const fixture = holdWorkspaceRead(locale, true);
    const errors = await openProject(page, locale, width, url, fixture.dispatch);
    await expect(page.locator('.pm-main [data-error-summary]')).toContainText(labels.stale);
    await expect(page.locator('.pm-main > .ui-page-header h1')).toHaveText(labels.fallback);
    const select = page.locator('select[data-workbench-management]');
    await expect(select).toBeHidden();
    await expect(select).not.toBeFocused();
    const state = await managementTabState(page);
    expect(state.shown, 'failed workspace read leaves the view select visible').toBe(false);
    expect(state.index, 'failed workspace read leaves the view select in the tab order').toBe(-1);
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(hash);
    expect(fixture.posts).toEqual([]);
    expect(errors).toEqual([]);
  });
}
