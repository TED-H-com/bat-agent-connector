import {mkdirSync} from 'node:fs';
import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture, demoRoute} from './demo/index.ts';
import {demoProject} from './demo/project.ts';
import {REGISTER_PAGE_CURSOR, registerIds, registerLabels} from './demo/register.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';

const locales = ['en', 'zh-TW'] as const;
type Locale = typeof locales[number];

function shotDir() {
  return process.env.BATC_ISSUES_SHOTS_DIR || `${process.env.TMPDIR || '/tmp'}/issues-shots`;
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

async function expectNoOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

async function expectNoJunk(page: Page) {
  const text = await page.locator('#main, .workspace-nav').evaluateAll(nodes => nodes.map(node => (node as HTMLElement).innerText).join('\n'));
  expect(text, text.slice(0, 500)).not.toMatch(/(?:^|[^\p{L}\p{N}_])(?:undefined|null|NaN|\[object Object\])(?![\p{L}\p{N}_])/u);
}

function textFor(locale: Locale) {
  const zh = locale === 'zh-TW';
  const project = demoProject(locale);
  return {
    zh, project, labels: registerLabels(project), rows: registerIds(project),
    findingName: zh ? 'GitHub Issue #87' : 'GitHub issue #87',
    matching: (n: number) => zh ? `${n} 筆符合` : `${n} matching`,
    title: zh ? '問題與變更' : 'Issues & changes',
    totals: (counts: number[]) => (zh ? ['風險', '問題', '行動', '變更', '決策'] : ['Risk', 'Issue', 'Action', 'Change', 'Decision'])
      .map((label, index) => `${label} ${counts[index]}`).join(' · '),
    shown: (n: number) => zh ? `顯示 ${n} 筆` : `${n} shown`,
    none: zh ? '目前沒有顯示' : 'None shown',
    empty: zh ? '沒有符合的紀錄' : 'No matching records',
    unavailable: zh ? '來源無法取得' : 'Source unavailable',
    loadMore: zh ? '載入更多' : 'Load more',
    noReasons: zh ? '沒有記下原因欄位' : 'No reason fields recorded',
    blocked: zh ? '沒有記錄原因 · 1' : 'No reason recorded · 1',
    age: zh ? '1 筆沒有已知時間' : '1 without a known age',
    repair: zh ? '轉成工作' : 'Turn into work',
    change: zh ? '變更：' : 'Change: ',
    search: zh ? '預算' : 'budget',
  };
}

async function boot(browser: import('@playwright/test').Browser, baseURL: string | undefined, locale: Locale, width: number, options: {dark?: boolean, gate?: boolean, holdDetail?: boolean} = {}) {
  const fixture = demoFixture({locale});
  const calls: string[] = [];
  const gate = {denyList: false, held: 0, release: null as null | (() => void), lateUsed: false, detailHeld: false, releaseDetail: null as null | (() => void)};
  const dispatch = async (input: {method?: string, path: string}) => {
    calls.push(input.path);
    const url = new URL(input.path, 'http://fixture');
    const list = url.pathname.endsWith('/register');
    if (options.gate && list && url.searchParams.get('owner') === 'late-reader' && !gate.lateUsed) {
      gate.lateUsed = true;
      gate.held += 1;
      await new Promise<void>(resolve => {gate.release = resolve;});
      url.searchParams.delete('owner');
      url.searchParams.delete('status');
      return fixture.dispatch({...input, path: `${url.pathname}?${url.searchParams}`});
    }
    if (options.gate && list && gate.denyList) return {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Register withdrawn'}}};
    if (options.holdDetail && /\/register\/[^/]+$/.test(url.pathname) && !gate.detailHeld) {
      gate.detailHeld = true;
      gate.held += 1;
      await new Promise<void>(resolve => {gate.releaseDetail = resolve;});
    }
    return fixture.dispatch(input);
  };
  const context = await browser.newContext({
    locale, baseURL, viewport: {width, height: width === 390 ? 844 : 900},
    colorScheme: options.dark ? 'dark' : 'light', reducedMotion: 'reduce',
  });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
  await mountConversation(page, false, dispatch);
  await page.goto(demoRoute());
  return {page, context, errors, calls, gate, fixture, copy: textFor(locale)};
}

for (const locale of locales) {
  test(`issues register experience (${locale})`, async ({browser, baseURL}) => {
    test.setTimeout(60_000);
    const {page, context, errors, calls, copy} = await boot(browser, baseURL, locale, 1440);
    await openView(page, 'issues');
    await expect(page.getByRole('heading', {level: 1})).toHaveText(copy.title);
    const meta = page.locator('[data-pm-header-meta]');
    await expect(meta).toHaveText(copy.totals([1, 5, 1, 1, 1]));
    await expect(page.locator('[data-reg-summary]')).toBeVisible();
    await expect(page.locator('[data-reg-age="0-7d"]')).toContainText('2');
    await expect(page.locator('[data-reg-unknown-age]')).toHaveText(copy.age);
    await expect(page.locator('[data-reg-blocked]')).toHaveText(copy.blocked);
    await expect(page.locator('.reg-week').first()).toHaveAttribute('aria-label', /.+/);
    const table = page.locator('[data-pm-register] tbody tr');
    await expect(table).toHaveCount(3);
    await expect(table.first()).toContainText(copy.zh ? '問題' : 'Issue');
    await expect(page.locator('[data-pm-register] thead')).not.toContainText(copy.zh ? '來源' : 'Source');
    await expect(table.first().locator('.reg-chip-more')).toHaveText(copy.zh ? '+1 項' : '+1 more');
    await expect(page.locator('[data-pm-register]')).toContainText(copy.findingName);
    await expect(page.locator('[data-pm-register]')).not.toContainText(copy.labels.risk);
    await expect(page.locator('[data-reg-count]')).toHaveText(copy.shown(3));
    await page.locator('[data-reg-more]').click();
    await expect.poll(() => calls.some(path => path.includes(`/register?`) && path.includes(`after=${REGISTER_PAGE_CURSOR}`))).toBe(true);
    await expect(table).toHaveCount(9);
    await expect(page.locator('[data-pm-register]')).toContainText(copy.labels.risk);
    await expect(page.locator('[data-reg-more]')).toHaveCount(0);
    await expect(page.locator('[data-reg-count]')).toHaveCount(0);
    await expect(meta).toHaveText(copy.totals([1, 5, 1, 1, 1]));

    await page.locator('[data-reg-type="decision"]').click();
    await expect(meta).toHaveText(copy.matching(1));
    await expect(table).toHaveCount(1);
    await expect(page.locator('[data-pm-register]')).toContainText(copy.project.copy.decisions.budget);
    await expect(page.locator('[data-reg-summary]')).toBeVisible();
    await page.locator(`[data-row-key="${copy.rows.decision}"]`).click();
    const bound = parseDiscussRefs((await page.locator('[data-reg-discuss]').getAttribute('href'))!.split('?')[1]);
    expect(bound.refs).toEqual([{kind: 'plan_revision', id: copy.project.ids.planV3}]);
    expect(bound.from).toBe('issues');
    expect(bound.title).toBe(copy.project.copy.decisions.budget);
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);

    await page.locator('[data-reg-type="change"]').click();
    await expect(page.locator('[data-reg-no-reasons]')).toHaveText(copy.noReasons);
    await expect(page.locator('[data-reg-blocked]')).toHaveCount(0);
    await expect(page.locator('[data-pm-register]')).not.toContainText(/0 blocked|0 個受阻/);

    await page.locator('[data-reg-type=""]').click();
    await page.locator('[data-reg-search]').fill(copy.search);
    await expect(table).toHaveCount(1);
    await expect(page.locator('[data-pm-register]')).toContainText(copy.project.copy.decisions.budget);
    await expect(page.locator('[data-pm-register]')).not.toContainText(copy.findingName);
    await page.locator('[data-reg-search]').fill('');
    await expect(table).toHaveCount(3);

    await page.locator('[data-reg-owner]').fill('unavailable-source');
    await expect(page.locator('[data-reg-summary]')).toBeHidden();
    await expect(meta).toHaveText(copy.shown(2));
    await expect(meta).not.toContainText(copy.zh ? '風險' : 'Risk');
    await page.locator('[data-reg-owner]').fill('empty-source');
    await expect(meta).toHaveText(copy.none);
    await expect(page.getByRole('heading', {level: 2, name: copy.empty})).toBeVisible();
    await expect(page.locator('[data-reg-summary]')).toBeHidden();
    await page.locator('[data-reg-owner]').fill('');
    await expect(table).toHaveCount(3);
    await expect(meta).toHaveText(copy.totals([1, 5, 1, 1, 1]));

    const finding = page.locator(`[data-row-key="${copy.rows.finding}"]`);
    await finding.focus();
    await finding.press('ArrowDown');
    await expect(page.locator(`[data-row-key="${copy.rows.decision}"]`)).toBeFocused();
    await page.locator(`[data-row-key="${copy.rows.decision}"]`).press('ArrowUp');
    await finding.press('Enter');
    await expect(page.locator('[data-reg-inspector]')).toBeVisible();
    const steps = page.locator('[data-reg-timeline] [data-reg-step]');
    await expect(steps).toHaveCount(3);
    await expect(steps.nth(0)).toHaveAttribute('data-reg-step', 'finding');
    await expect(steps.nth(1)).toHaveAttribute('data-reg-step', 'action');
    await expect(steps.nth(2)).toHaveAttribute('data-reg-step', 'work');
    const unbound = parseDiscussRefs((await page.locator('[data-reg-discuss]').getAttribute('href'))!.split('?')[1]);
    expect(unbound.refs).toEqual([copy.rows.findingRef]);
    expect(unbound.from).toBe('issues');
    expect(unbound.title).toBe(copy.findingName);
    const proposed = parseDiscussRefs((await page.locator('[data-reg-propose]').getAttribute('href'))!.split('?')[1]);
    expect(proposed.refs).toEqual([copy.rows.findingRef]);
    expect(proposed.title.startsWith(copy.change)).toBe(true);
    expect(proposed.title).toContain(copy.findingName);
    await expect(page.locator('[data-reg-inspector] [data-reg-pr]')).toHaveAttribute('href', 'https://github.com/example/membership/pull/87');
    await page.locator('[data-reg-action="pm.maintenance.repair"]').click();
    await expect(page.locator('[data-reg-inspector]')).toBeVisible();
    await expect(page.locator('[data-pm-section="ui_repairs"] > details')).toHaveAttribute('open', '');
    await expect(page.locator('[data-pm-repair-finding]')).toHaveValue(copy.rows.findingSource);
    await expect(page.locator('[data-reg-action="pm.maintenance.repair"]')).toHaveText(copy.repair);
    await page.keyboard.press('Escape');
    // Escape in the repair's editable field leaves the other inspector alone.
    if (await page.locator('[data-reg-inspector]').isVisible()) {
      await page.locator('[data-reg-inspector] [data-reg-discuss]').focus();
      await page.keyboard.press('Escape');
    }
    await expect(finding).toBeFocused();
    await finding.press('Enter');
    await page.locator('[data-reg-inspector] [data-reg-step="work"] [data-reg-work]').click();
    await expect(page.locator('[data-pm-view="requirements"]')).toHaveAttribute('aria-current', 'page');
    await expect(page.locator(`[data-rtm-select="${copy.project.ids.work.email}"]`)).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator(`[data-rtm-select="${copy.project.ids.work.email}"]`)).toBeFocused();

    // A link into an already mounted Requirements view clears only the obstructing
    // filter and keeps that view's selection, expansion and keyboard focus behavior.
    await page.locator('[data-pm-rtm] input[type=search]').fill('no-matching-requirement');
    await openView(page, 'issues');
    await page.locator(`[data-reg-requirement="${copy.project.ids.requirements.email}"]:visible`).first().click();
    await expect(page.locator('[data-pm-view="requirements"]')).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('[data-pm-rtm] input[type=search]')).toHaveValue('');
    const requirement = page.locator(`[data-rtm-requirement="${copy.project.ids.requirements.email}"]`);
    await expect(requirement).toHaveAttribute('aria-current', 'true');
    await expect(requirement.locator('button')).toBeFocused();
    await expect(requirement.locator('button')).toHaveAttribute('aria-expanded', 'true');

    await openView(page, 'issues');
    await page.locator('[data-reg-type="decision"]').click();
    await expect(page.locator('[data-reg-type="decision"]')).toHaveAttribute('aria-checked', 'true');
    await openView(page, 'board');
    await openView(page, 'issues');
    await expect(page.locator('[data-reg-type="decision"]')).toHaveAttribute('aria-checked', 'true');
    await expect(page.locator('[data-pm-register]')).toContainText(copy.project.copy.decisions.budget);
    await expect(page.locator('[data-pm-register]')).not.toContainText(copy.findingName);
    await expectNoJunk(page);
    await expectNoOverflow(page);
    expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
    await context.close();
  });

  test(`issues register layout (${locale})`, async ({browser, baseURL}) => {
    test.setTimeout(60_000);
    const {page, context, errors, copy} = await boot(browser, baseURL, locale, 1440);
    mkdirSync(shotDir(), {recursive: true});
    await openView(page, 'issues');
    await expect(page.locator('[data-reg-summary]')).toBeVisible();
    for (const width of [1440, 390, 768]) {
      await page.setViewportSize({width, height: width === 390 ? 844 : 900});
      const narrow = width <= 800;
      if (narrow) {
        await expect(page.locator('[data-pm-register] .reg-list-row').first()).toBeVisible();
        await expect(page.locator('[data-pm-register] table')).toHaveCount(0);
      } else {
        await expect(page.locator('[data-pm-register] tbody tr').first()).toBeVisible();
      }
      await expectNoOverflow(page);
      await page.screenshot({path: `${shotDir()}/issues-${locale}-${width}.png`, fullPage: true});
      if (width === 1440) {
        await page.locator(`[data-row-key="${copy.rows.finding}"]`).click();
        await expect(page.locator('[data-reg-inspector]')).toBeVisible();
        await expect(page.locator('[data-reg-timeline] [data-reg-step]').nth(0)).toHaveAttribute('data-reg-step', 'finding');
        await page.screenshot({path: `${shotDir()}/issues-${locale}-1440-finding.png`});
        await page.keyboard.press('Escape');
        await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
        await page.locator(`[data-row-key="${copy.rows.decision}"]`).click();
        await expect(page.locator('[data-reg-step="decision"]')).toBeVisible();
        await page.screenshot({path: `${shotDir()}/issues-${locale}-1440-decision.png`});
        await page.keyboard.press('Escape');
        await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
      }
      if (width === 390) {
        await page.locator(`[data-row-key="${copy.rows.finding}"]`).click();
        await expect(page.locator('[data-reg-inspector]')).toBeVisible();
        await expectNoOverflow(page);
        await page.screenshot({path: `${shotDir()}/issues-${locale}-390-inspector.png`});
        await page.keyboard.press('Escape');
        await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
      }
      if (width === 768) {
        await page.locator('[data-reg-trends] > summary').click();
        await expect(page.locator('[data-reg-summary]')).toBeVisible();
        await expect(page.locator('[data-reg-trends]')).toContainText(copy.zh ? '趨勢' : 'Trends');
        await expectNoOverflow(page);
        await page.screenshot({path: `${shotDir()}/issues-${locale}-768-trends.png`, fullPage: true});
        await page.locator('[data-reg-trends] > summary').click();
        await expect(page.locator('[data-reg-summary]')).toBeHidden();
        await page.locator('[data-reg-more]').click();
        await page.locator(`[data-row-key="${copy.rows.risk}"]`).click();
        await expect(page.locator('[data-reg-inspector]')).toBeVisible();
        await page.locator('[data-reg-action="pm.governance.revise"]').click();
        await expect(page.locator('[data-pm-section="ui_governance"] > details')).toHaveAttribute('open', '');
        await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
        await expect.poll(() => page.evaluate(() => {
          const fold = document.querySelector('[data-pm-section="ui_governance"] > details');
          return Boolean(fold && fold.contains(document.activeElement));
        })).toBe(true);
        await expectNoOverflow(page);
        await page.screenshot({path: `${shotDir()}/issues-${locale}-768-revise.png`, fullPage: true});
      }
    }
    await expectNoJunk(page);
    expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
    await context.close();
  });

  test(`issues register withdrawal (${locale})`, async ({browser, baseURL}) => {
    test.setTimeout(60_000);
    const {page, context, errors, gate, copy} = await boot(browser, baseURL, locale, 1440, {gate: true});
    await openView(page, 'issues');
    await page.locator(`[data-row-key="${copy.rows.finding}"]`).click();
    await expect(page.locator('[data-reg-inspector]')).toBeVisible();
    gate.denyList = true;
    await page.locator('.ui-page-header').getByRole('button', {name: copy.zh ? '重新整理' : 'Refresh'}).click();
    await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
    await expect(page.getByRole('heading', {level: 2, name: copy.unavailable})).toBeVisible();
    await expect(page.getByRole('heading', {level: 1})).toHaveText(copy.title);
    await expect(page.locator('[data-pm-header-meta]')).toBeHidden();
    await expect(page.locator('[data-pm-register]')).not.toContainText(copy.findingName);
    await expect(page.locator('[data-reg-summary]')).toBeHidden();
    await expectNoJunk(page);
    expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
    await context.close();
  });
}

test('a late register page does not restore withdrawn rows', async ({browser, baseURL}) => {
  test.setTimeout(60_000);
  const {page, context, errors, gate, copy} = await boot(browser, baseURL, 'en', 1440, {gate: true});
  await openView(page, 'issues');
  await expect(page.locator('[data-pm-register] tbody tr')).toHaveCount(3);
  await page.locator('[data-reg-owner]').fill('late-reader');
  await expect.poll(() => gate.held).toBe(1);
  gate.denyList = true;
  await page.locator('[data-reg-status]').selectOption('blocked');
  await expect(page.getByRole('heading', {level: 2, name: copy.unavailable})).toBeVisible();
  gate.release?.();
  // The held 200 still carries the unfiltered rows. It must not paint them after the 403.
  await page.waitForTimeout(400);
  await expect(page.locator('[data-pm-register] tbody tr')).toHaveCount(0);
  await expect(page.getByRole('heading', {level: 2, name: copy.unavailable})).toBeVisible();
  await expect(page.locator('[data-pm-header-meta]')).toBeHidden();
  await expect(page.locator('[data-pm-register]')).not.toContainText(copy.findingName);
  expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
  await context.close();
});

test('a held register detail does not leave a modal sheet', async ({browser, baseURL}) => {
  test.setTimeout(60_000);
  const {page, context, errors, gate, copy} = await boot(browser, baseURL, 'en', 768, {holdDetail: true});
  await openView(page, 'issues');
  await page.locator(`[data-row-key="${copy.rows.finding}"]`).click();
  await expect.poll(() => gate.held).toBe(1);
  await openView(page, 'board');
  gate.releaseDetail?.();
  await page.waitForTimeout(400);
  await expect(page.locator('[inert]')).toHaveCount(0);
  await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
  await openView(page, 'schedule');
  expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
  await context.close();
});

test('issues keeps loaded pages and the selected row across a view change', async ({browser, baseURL}) => {
  test.setTimeout(60_000);
  const {page, context, errors, copy} = await boot(browser, baseURL, 'en', 1440);
  await openView(page, 'issues');
  await page.locator('[data-reg-more]').click();
  await expect(page.locator('[data-pm-register] tbody tr')).toHaveCount(9);
  await page.locator(`[data-row-key="${copy.rows.risk}"]`).click();
  await expect(page.locator('[data-reg-inspector]')).toBeVisible();
  await openView(page, 'board');
  await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
  await openView(page, 'issues');
  await expect(page.locator('[data-pm-register] tbody tr')).toHaveCount(9);
  await expect(page.locator(`[data-row-key="${copy.rows.risk}"]`)).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('[data-reg-inspector]')).toBeVisible();
  await expect(page.locator('[data-reg-inspector]')).toContainText(copy.labels.risk);
  expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
  await context.close();
});

test('issues overflow tools open their folds', async ({browser, baseURL}) => {
  test.setTimeout(90_000);
  const fixture = demoFixture({locale: 'en'});
  const dispatch = async (input: {method?: string, path: string}) => {
    const url = new URL(input.path, 'http://fixture');
    if (url.pathname === '/capabilities' || url.pathname === '/bootstrap') {
      const result = await fixture.dispatch(input);
      const caps = url.pathname === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_workspaces.maintenance_review = {version: 1};
      caps.features.pm_workspaces.follow_up_prepare = true;
      return result;
    }
    try {
      return await fixture.dispatch(input);
    } catch (_error) {
      return {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'stub'}}};
    }
  };
  const context = await browser.newContext({locale: 'en', baseURL, viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  await mountConversation(page, false, dispatch);
  await page.goto(demoRoute());
  await openView(page, 'issues');
  await expect(page.locator('[data-pm-register] tbody tr').first()).toBeVisible();
  for (const [key, section] of [['governance', 'ui_governance'], ['repairs', 'ui_repairs'], ['review', 'ui_maintenance_review'], ['follow', 'ui_follow_up']] as const) {
    await page.locator('[data-pm-overflow]').click();
    const item = page.locator(`[data-menu-key="${key}"]`);
    await expect(item).toBeVisible();
    if (key === 'governance') await expect(item).toBeFocused();
    await item.click();
    const fold = page.locator(`[data-pm-section="${section}"] > details`);
    await expect(fold).toHaveAttribute('open', '');
    await expect(fold.locator(':scope > summary')).toBeFocused();
  }
  await context.close();
});

test('issues register dark layout', async ({browser, baseURL}) => {
  const {page, context, errors} = await boot(browser, baseURL, 'en', 1440, {dark: true});
  mkdirSync(shotDir(), {recursive: true});
  await openView(page, 'issues');
  await expect(page.locator('[data-reg-summary]')).toBeVisible();
  await expectNoOverflow(page);
  await page.screenshot({path: `${shotDir()}/issues-en-1440-dark.png`, fullPage: true});
  expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
  await context.close();
});
