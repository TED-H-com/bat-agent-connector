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
    findingName: zh ? 'GitHub 議題 #87' : 'GitHub issue #87',
    matching: (n: number) => zh ? `${n} 筆符合` : `${n} matching`,
    title: zh ? '問題與變更' : 'Issues & changes',
    totals: (counts: number[]) => (zh ? ['風險', '問題', '行動', '變更', '決策'] : ['Risk', 'Issue', 'Action', 'Change', 'Decision'])
      .flatMap((label, index) => counts[index] > 0 ? [`${label} ${counts[index]}`] : []).join(' · '),
    shown: (n: number) => zh ? `顯示 ${n} 筆` : `${n} shown`,
    none: zh ? '沒有顯示項目' : 'None shown',
    empty: zh ? '沒有符合的紀錄' : 'No matching records',
    incomplete: zh ? '紀錄不完整' : 'Records incomplete',
    unavailable: zh ? '來源無法取得' : 'Source unavailable',
    loadMore: zh ? '載入更多' : 'Load more',
    noReasons: zh ? '尚無紀錄' : 'None recorded',
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
  const writes: unknown[] = [];
  const gate = {denyList: false, held: 0, release: null as null | (() => void), lateUsed: false, detailHeld: false, releaseDetail: null as null | (() => void)};
  const dispatch = async (input: {method?: string, path: string}) => {
    calls.push(input.path);
    if (input.method && input.method !== 'GET') writes.push(input);
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
  return {page, context, errors, calls, writes, gate, fixture, copy: textFor(locale)};
}

for (const locale of locales) {
  for (const width of [1440, 1280, 1000, 390]) test(`Issues keeps its list clear of the full-height inspector (${locale}, ${width})`, async ({browser, baseURL}) => {
    const {page, context, copy, errors, writes} = await boot(browser, baseURL, locale, width);
    try {
      await openView(page, 'issues');
      await page.locator(`[data-row-key="${copy.rows.finding}"]`).click();
      const inspector = page.locator('[data-reg-inspector]');
      await expect(inspector).toContainText(copy.findingName);
      await expect(inspector).toHaveAttribute('role', width >= 1200 ? 'complementary' : 'dialog');
      const bounds = await inspector.boundingBox();
      expect(bounds!.y + bounds!.height).toBe(page.viewportSize()!.height);
      if (width >= 1200) {
        expect(bounds!.y).toBe(48);
        const list = await page.locator('.reg-main').boundingBox();
        expect(list!.x + list!.width).toBeLessThanOrEqual(bounds!.x);
        await expect(page.locator(`[data-reg-table] [data-reg-requirement="${copy.project.ids.requirements.email}"]`)).toBeHidden();
        await page.locator('[data-pm-refresh]').click();
        await expect(inspector).toContainText(copy.findingName);
      }
      await expectNoOverflow(page);
      if (process.env.BATC_FIX2_SHOTS) {
        mkdirSync(process.env.BATC_FIX2_SHOTS, {recursive: true});
        await page.screenshot({path: `${process.env.BATC_FIX2_SHOTS}/fix2-issues-docked-${locale}-${width}.png`, animations: 'disabled'});
      }
      expect(writes).toHaveLength(0);
      expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
    } finally {await context.close();}
  });

  test(`returning Issues reserves its inspector before delayed reads (${locale})`, async ({browser, baseURL}) => {
    const {page, context, copy, errors} = await boot(browser, baseURL, locale, 1440);
    let releaseList!: () => void, releaseDetail!: () => void, detailStarted = false;
    const list = new Promise<void>(resolve => {releaseList = resolve;});
    const detail = new Promise<void>(resolve => {releaseDetail = resolve;});
    try {
      await openView(page, 'issues');
      await page.locator(`[data-row-key="${copy.rows.finding}"]`).click();
      await expect(page.locator('[data-reg-timeline] [data-reg-step]')).toHaveCount(3);
      await openView(page, 'requirements');
      await page.route('**/register?*', async route => {await list; await route.fallback();});
      await page.route('**/register/*', async route => {detailStarted = true; await detail; await route.fallback();});
      await openView(page, 'issues');
      const inspector = page.locator('[data-reg-inspector]');
      await expect(inspector).toBeVisible();
      await expect(inspector).toHaveAttribute('data-reg-loading', '');
      const tableLink = page.locator(`[data-reg-table] [data-reg-requirement="${copy.project.ids.requirements.email}"]`);
      await expect(tableLink).toBeHidden();
      releaseList();
      await expect.poll(() => detailStarted).toBe(true);
      await expect(tableLink).toBeHidden();
      releaseDetail();
      await expect(inspector).not.toHaveAttribute('data-reg-loading');
      await inspector.locator(`[data-reg-requirement="${copy.project.ids.requirements.email}"]`).click();
      await expect(page.locator('[data-pm-view="requirements"]')).toHaveAttribute('aria-current', 'page');
      await expect(page.locator(`[data-rtm-requirement="${copy.project.ids.requirements.email}"] button`)).toBeFocused();
      expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
    } finally {releaseList(); releaseDetail(); await context.close();}
  });

  test(`issues register experience (${locale})`, async ({browser, baseURL}) => {
    test.setTimeout(60_000);
    const {page, context, errors, calls, copy} = await boot(browser, baseURL, locale, 1440);
    await openView(page, 'issues');
    await expect(page.getByRole('heading', {level: 1})).toHaveText(copy.title);
    const statusOptions = page.locator('[data-reg-status] option');
    await expect(statusOptions).toHaveCount(10);
    await expect(page.locator('[data-reg-status] option[value="format_valid"]')).toHaveCount(0);
    await expect(page.locator('[data-reg-status] option[value="in_review"]')).toHaveText(copy.zh ? '審閱中' : 'In review');
    await expect(page.locator('[data-reg-status] option[value="blocked"]')).toHaveText(copy.zh ? '受阻' : 'Blocked');
    await expect(page.locator('[data-reg-status]')).not.toContainText(copy.zh ? '格式有效' : 'Format valid');
    const meta = page.locator('[data-pm-header-meta]');
    await expect(meta).toHaveText(copy.totals([1, 6, 1, 3, 1]) + ' · ' + copy.shown(3));
    await expect(page.locator('[data-reg-summary]')).toBeVisible();
    await expect(page.locator('[data-reg-age="0-7d"]')).toContainText('4');
    await expect(page.locator('[data-reg-unknown-age]')).toHaveText(copy.age);
    await expect(page.locator('[data-reg-blocked]')).toHaveText(copy.blocked);
    await expect(page.locator('.reg-week').first()).toHaveAttribute('aria-label', /.+/);
    const table = page.locator('[data-pm-register] tbody tr');
    await expect(table).toHaveCount(3);
    await expect(page.locator('[data-reg-footer]')).not.toContainText(copy.shown(3));
    await expect(page.locator('[data-reg-trends] > summary')).toBeHidden();
    const optionGroups = await statusOptions.evaluateAll(nodes => nodes.map(node => (node as HTMLOptionElement).value));
    for (const pill of await page.locator('[data-pm-register] tbody .ui-pill').all()) expect(optionGroups).toContain(await pill.getAttribute('data-status'));
    await expect(table.first().locator('.ui-pill')).toHaveText(copy.zh ? '已觀察' : 'Observed');
    await expect(table.first()).toContainText(copy.zh ? '問題' : 'Issue');
    await expect(page.locator('[data-pm-register] thead')).not.toContainText(copy.zh ? '來源' : 'Source');
    await expect(table.first().locator('.reg-chip-more')).toHaveText(copy.zh ? '+1 項' : '+1 more');
    await expect(page.locator('[data-pm-register]')).toContainText(copy.findingName);
    await expect(page.locator('[data-pm-register]')).not.toContainText(copy.labels.risk);
    await expect(page.locator('[data-reg-count]')).toHaveAttribute('aria-label', copy.shown(3));
    await page.locator('[data-reg-more]').click();
    await expect.poll(() => calls.some(path => path.includes(`/register?`) && path.includes(`after=${REGISTER_PAGE_CURSOR}`))).toBe(true);
    await expect(table).toHaveCount(12);
    await expect(page.locator(`[data-row-key="${copy.rows.mapping}"]`)).toContainText(copy.zh ? '未命名議題' : 'Untitled issue');
    await expect(page.locator(`[data-row-key="${copy.rows.mapping}"]`)).not.toContainText(copy.zh ? '尚無紀錄' : 'Not recorded');
    await expect(page.locator('[data-pm-register]')).toContainText(copy.labels.risk);
    await expect(page.locator('[data-reg-more]')).toHaveCount(0);
    await expect(page.locator('[data-reg-count]')).toHaveCount(0);
    await expect(meta).toHaveText(copy.totals([1, 6, 1, 3, 1]));

    await page.locator('[data-reg-type="decision"]').click();
    await expect(meta).toHaveText(copy.matching(1));
    await expect(table).toHaveCount(1);
    await expect(page.locator('[data-pm-register]')).toContainText(copy.project.copy.planTitle);
    await expect(page.locator('[data-reg-summary]')).toBeVisible();
    await page.locator(`[data-row-key="${copy.rows.decision}"]`).click();
    const bound = parseDiscussRefs((await page.locator('[data-reg-discuss]').getAttribute('href'))!.split('?')[1]);
    expect(bound.refs).toEqual([{kind: 'plan_revision', id: copy.project.ids.planV3}]);
    expect(bound.from).toBe('issues');
    expect(bound.title).toBe(copy.project.copy.planTitle);
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
    await expect(page.getByRole('heading', {level: 2, name: copy.incomplete})).toBeVisible();
    await expect(page.getByRole('heading', {level: 2, name: copy.empty})).toHaveCount(0);
    await expect(page.locator('[data-reg-summary]')).toBeHidden();
    await page.locator('[data-reg-owner]').fill('');
    await expect(table).toHaveCount(3);
    await expect(meta).toHaveText(copy.totals([1, 6, 1, 3, 1]) + ' · ' + copy.shown(3));

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
    await page.locator('[data-reg-overflow]').click();
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
    await expect(page.locator('[data-pm-register]')).toContainText(copy.project.copy.planTitle);
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
    await page.locator('[data-reg-age="0-7d"]').click();
    await expect(page.locator('[data-reg-filter="age"]')).toBeVisible();
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
    await expect(page.locator('[data-reg-age]')).toHaveCount(0);
    await expect(page.locator('[data-reg-filter]')).toHaveCount(0);
    await expect(page.locator('[data-pm-register] tbody tr')).toHaveCount(0);
    await expectNoJunk(page);
    expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
    await context.close();
  });

  test(`issues age and week filters (${locale})`, async ({browser, baseURL}) => {
    test.setTimeout(60_000);
    const {page, context, errors, copy} = await boot(browser, baseURL, locale, 1440);
    await openView(page, 'issues');
    const meta = page.locator('[data-pm-header-meta]');
    const rows = page.locator('[data-pm-register] tbody tr');
    await expect(rows).toHaveCount(3);
    await expect(meta).toHaveText(copy.totals([1, 6, 1, 3, 1]) + ' · ' + copy.shown(3));
    const bar = page.locator('[data-reg-age="0-7d"]');
    await bar.focus();
    await page.keyboard.press('Enter');
    await expect(bar).toHaveAttribute('aria-pressed', 'true');
    await expect(bar).toBeFocused();
    await expect(rows).toHaveCount(1);
    await expect(meta).toHaveText(copy.shown(1));
    await expect(meta).not.toContainText(copy.zh ? '風險' : 'Risk');
    await expect(page.locator('[data-reg-count]')).toHaveAttribute('aria-label', copy.shown(1));
    await expect(page.locator('[data-reg-age="0-7d"]')).toContainText('4');
    const remove = locale === 'zh-TW' ? /移除/ : /Remove/;
    await page.locator('[data-reg-filter="age"]').getByRole('button', {name: remove}).click();
    await expect(bar).toHaveAttribute('aria-pressed', 'false');
    await expect(rows).toHaveCount(3);
    await expect(page.locator('[data-reg-age-filter]')).toBeFocused();
    await expect(meta).toHaveText(copy.totals([1, 6, 1, 3, 1]) + ' · ' + copy.shown(3));
    await expect(page.locator('[data-reg-count]')).toHaveAttribute('aria-label', copy.shown(3));
    await page.locator('[data-reg-age-filter]').selectOption('31-90d');
    await expect(rows).toHaveCount(0);
    await expect(meta).toHaveText(copy.none);
    await expect(meta).not.toContainText(/\d/);
    await expect(page.locator('[data-reg-age="31-90d"]')).toContainText('2');
    await page.locator('[data-reg-filter="age"]').getByRole('button', {name: remove}).click();
    await expect(rows).toHaveCount(3);
    await page.locator('[data-reg-week]').last().click();
    await expect(page.locator('[data-reg-week]').last()).toHaveAttribute('aria-pressed', 'true');
    await expect(rows).toHaveCount(2);
    await expect(meta).toHaveText(copy.shown(2));
    await page.locator('[data-reg-filter="week"]').getByRole('button', {name: remove}).click();
    await expect(rows).toHaveCount(3);
    await expect(meta).toHaveText(copy.totals([1, 6, 1, 3, 1]) + ' · ' + copy.shown(3));
    await expectNoJunk(page);
    expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
    await context.close();
  });

  test(`issues columns stay intact (${locale})`, async ({browser, baseURL}) => {
    test.setTimeout(60_000);
    const {page, context, errors, copy} = await boot(browser, baseURL, locale, 1440);
    await openView(page, 'issues');
    await expect(page.locator('[data-pm-register] tbody tr')).toHaveCount(3);
    for (const width of [1440, 1000]) {
      await page.setViewportSize({width, height: 900});
      for (const open of [false, true]) {
        if (open) {
          await page.locator(`[data-row-key="${copy.rows.finding}"]`).click();
          await expect(page.locator('[data-reg-inspector]')).toBeVisible();
        } else if (await page.locator('[data-reg-inspector]').count()) {
          await page.keyboard.press('Escape');
          await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
        }
        await expectNoOverflow(page);
        const clipped = await page.locator('.reg-toolbar').evaluate(toolbar => {
          const box = toolbar.getBoundingClientRect();
          return [...toolbar.querySelectorAll('select, input, .ui-segmented button')].filter(node => {
            const rect = node.getBoundingClientRect();
            if (rect.width < 1) return false;
            const cut = node.scrollWidth > node.clientWidth + 2;
            const outside = rect.left < box.left - 1 || rect.right > box.right + 1;
            return cut || outside;
          }).map(node => node.getAttribute('aria-label') || node.textContent || node.tagName);
        });
        expect(clipped).toEqual([]);
        if (width >= 1200 || !open) {
          for (const column of ['type', 'title', 'status']) {
            await expect(page.locator(`[data-pm-register] th[data-column=${column}]`)).toBeVisible();
          }
          const next = page.locator('[data-pm-register] th[data-column=next]');
          if (width === 1440 && !open) await expect(next).toBeVisible();
          else await expect(next).toBeHidden();
          const typeCell = page.locator(`[data-row-key="${copy.rows.decision}"] [data-column=type] .reg-type`);
          await expect(typeCell).toContainText(copy.zh ? '決策' : 'Decision');
          expect(await typeCell.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
          const statusCell = page.locator(`[data-row-key="${copy.rows.decision}"] [data-column=status]`);
          await expect(statusCell).toBeVisible();
          await expect(statusCell).toContainText(copy.zh ? '已採用' : 'Adopted');
        }
      }
    }
    await expectNoJunk(page);
    expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
    await context.close();
  });
}

for (const locale of locales) test(`issues status groups keep every recorded state (${locale})`, async ({browser, baseURL}) => {
  const {page, context, fixture, copy} = await boot(browser, baseURL, locale, 1440);
  await openView(page, 'issues');
  const statuses = {
    open: ['open', 'monitoring', 'observed_open', 'tracking'],
    in_progress: ['in_progress', 'running', 'accepted', 'continue_maintenance'],
    blocked: ['blocked'], waiting: ['waiting_checks', 'waiting_external'],
    in_review: ['needs_review', 'proposed', 'uncertain', 'needs_attention', 'format_valid', 'bounded_repair_proposal'],
    done: ['reported_resolved', 'reported_done', 'succeeded', 'adopted', 'adopted_direction', 'recorded_discussion_decision'],
    closed: ['rejected', 'cancelled', 'failed'], draft: ['draft'],
    other: ['future_state', '__proto__', 'constructor', null],
  };
  const seed = (await fixture.dispatch({method: 'GET', path: `/pm/workspaces/${fixture.ids.workspaceId}/register?limit=50`})).data;
  const records = Object.entries(statuses).flatMap(([group, values]) => values.map((status, index) => ({
    ...seed.rows[0], row_id: `finding.${group}_${index}`, title: `${group} ${index}`, status,
  })));
  await page.route('**/register?*', route => {
    const raw = new URL(route.request().url()).searchParams.get('status');
    return route.fulfill({json: {...seed, rows: raw ? records.filter(row => row.status === raw) : records,
      next_cursor: null, summary: null, total_by_type: null, coverage: {...seed.coverage, complete: false}}});
  });
  await page.locator('.ui-page-header').getByRole('button', {name: copy.zh ? '重新整理' : 'Refresh'}).click();
  const rows = page.locator('[data-pm-register] tbody tr');
  await expect(rows).toHaveCount(records.length);
  await expect(page.locator('[data-reg-status] option')).toHaveCount(10);
  for (const [group, values] of Object.entries(statuses)) {
    await page.locator('[data-reg-status]').selectOption(group);
    await expect(rows).toHaveCount(values.length);
    await expect(page.locator('[data-pm-header-meta]')).toHaveText(copy.shown(values.length));
    for (let index = 0; index < values.length; index++) {
      await expect(page.locator(`[data-row-key="finding.${group}_${index}"]`)).toBeVisible();
    }
  }
  await page.locator('[data-reg-status]').selectOption('');
  await expect(rows).toHaveCount(records.length);
  await expectNoJunk(page);
  await context.close();
});

for (const locale of locales) test(`issues age filters exclude resolved and undated rows (${locale})`, async ({browser, baseURL}) => {
  const {page, context, fixture, copy} = await boot(browser, baseURL, locale, 1440);
  await openView(page, 'issues');
  const seed = (await fixture.dispatch({method: 'GET', path: `/pm/workspaces/${fixture.ids.workspaceId}/register?limit=50`})).data;
  const rows = [
    {...seed.rows[0], row_id: 'finding.open', status: 'open', created_at: seed.as_of},
    {...seed.rows[0], row_id: 'finding.resolved', status: 'reported_done', created_at: seed.as_of},
    {...seed.rows[0], row_id: 'finding.undated', status: 'open', created_at: null},
  ];
  await page.route('**/register?*', route => route.fulfill({json: {...seed, rows, next_cursor: null,
    summary: null, total_by_type: null, coverage: {...seed.coverage, complete: false}}}));
  await page.locator('.ui-page-header').getByRole('button', {name: copy.zh ? '重新整理' : 'Refresh'}).click();
  await expect(page.locator('[data-pm-register] tbody tr')).toHaveCount(3);
  await page.locator('[data-reg-age-filter]').selectOption('0-7d');
  await expect(page.locator('[data-pm-register] tbody tr')).toHaveCount(1);
  await expect(page.locator('[data-row-key="finding.open"]')).toBeVisible();
  await expect(page.locator('[data-pm-header-meta]')).toHaveText(copy.shown(1));
  await expect(page.locator('[data-reg-summary]')).toBeHidden();
  await page.locator('[data-reg-filter="age"] button').click();
  await expect(page.locator('[data-pm-register] tbody tr')).toHaveCount(3);
  await context.close();
});

for (const locale of locales) test(`issues read errors are localized and recover cleanly (${locale})`, async ({browser, baseURL}) => {
  const {page, context, copy} = await boot(browser, baseURL, locale, 1440);
  await openView(page, 'issues');
  await page.locator(`[data-row-key="${copy.rows.finding}"]`).click();
  await expect(page.locator('[data-reg-inspector]')).toBeVisible();
  const selected = page.locator(`[data-row-key="${copy.rows.finding}"]`);
  expect(await selected.evaluate(row => getComputedStyle(row.querySelector('td')!).backgroundColor
    !== getComputedStyle(row.querySelector('.ui-pill')!).backgroundColor)).toBe(true);
  let failed = true;
  await page.route('**/register?*', route => failed
    ? route.fulfill({status: 503, json: {error: {code: 'UNAVAILABLE', message: 'Provider detail is not UI copy'}}})
    : route.fallback());
  await page.locator('.ui-page-header').getByRole('button', {name: copy.zh ? '重新整理' : 'Refresh'}).click();
  const panel = page.locator('[data-pm-register]');
  await expect(panel.getByRole('alert')).toContainText(copy.zh ? '無法讀取紀錄' : 'Could not read records');
  await expect(panel).not.toContainText('Provider detail is not UI copy');
  await expect(panel.locator('tbody tr')).toHaveCount(0);
  await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
  await expect(page.locator('[data-pm-header-meta]')).toBeHidden();
  await expect(page.locator('[data-reg-summary]')).toBeHidden();
  failed = false;
  await panel.getByRole('button', {name: copy.zh ? '重試' : 'Retry', exact: true}).click();
  await expect(panel.locator('tbody tr')).toHaveCount(3);
  await expect(panel.getByRole('alert')).toHaveCount(0);
  await expect(page.locator('[data-pm-header-meta]')).toContainText(copy.shown(3));
  await context.close();
});

for (const locale of locales) test(`issues mobile related chips keep the full touch target (${locale})`, async ({browser, baseURL}) => {
  const {page, context, copy} = await boot(browser, baseURL, locale, 390);
  await openView(page, 'issues');
  await page.locator(`[data-row-key="${copy.rows.finding}"]`).click();
  const chip = page.locator('[data-reg-inspector] [data-reg-requirement]').first();
  await expect(chip).toBeVisible();
  const box = (await chip.boundingBox())!;
  // A tap inside the shared 44px target, above the visible 24px chip, must open its requirement.
  await page.mouse.click(box.x + box.width / 2, box.y - 6);
  await expect(page).toHaveURL(new RegExp(`/requirements\\?select=${copy.project.ids.requirements.email}`));
  await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
  await expect(page.locator('[data-pm-region="requirements"]')).toBeVisible();
  await context.close();
});

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
  await expect(page.locator('[data-pm-register] tbody tr')).toHaveCount(12);
  await page.locator(`[data-row-key="${copy.rows.risk}"]`).click();
  await expect(page.locator('[data-reg-inspector]')).toBeVisible();
  await openView(page, 'board');
  await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
  await openView(page, 'issues');
  await expect(page.locator('[data-pm-register] tbody tr')).toHaveCount(12);
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
