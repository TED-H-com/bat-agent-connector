import {test, expect, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {shellReviewFixture} from './shell-review-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {pmRoute, workspaceId} from './pm-fixture.ts';
import {openPmPanel} from './pm-navigation.ts';
import {openSession, fromRequirements} from './session-fixture.ts';
import {STRINGS} from '../src/i18n.js';
async function shot(page: Page, state: string, locale: string, width: number) {
  if (!process.env.BATC_FIX2_SHOTS) return;
  await mkdir(process.env.BATC_FIX2_SHOTS, {recursive: true});
  await page.screenshot({path: `${process.env.BATC_FIX2_SHOTS}/fix2-${state}-${locale}-${width}.png`, animations: 'disabled', fullPage: false});
}
for (const locale of ['en', 'zh-TW']) for (const width of [1440, 1280, 1000, 390]) {
  test(`Discussion tools overlay without squeezing the thread ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({locale, baseURL, viewport: {width, height: 900}}), page = await context.newPage(), f = shellReviewFixture();
    await mountConversation(page, false, f.dispatch); await page.goto(pmRoute);
    const thread = page.locator('.pm-discussion'); await expect(thread).toBeVisible(); const before = await thread.boundingBox();
    for (const task of ['ui_workspace_settings', 'ui_change_phase'] as const) {
      await openPmPanel(page, task); const panel = page.locator('main > .ui-inspector');
      await expect(panel).toHaveAttribute('role', 'dialog'); await expect(panel).toHaveAttribute('aria-modal', 'true');
      await expect(panel.getByRole('heading', {name: locale === 'en' ? task === 'ui_change_phase' ? 'Change phase' : 'Workspace settings' : task === 'ui_change_phase' ? '變更階段' : '工作空間設定', exact: true})).toBeVisible();
      expect((await thread.boundingBox())!.width).toBeCloseTo(before!.width, 1);
      expect(await panel.evaluate(node => getComputedStyle(node).boxShadow)).not.toBe('none');
      if (task === 'ui_workspace_settings') {
        await expect(panel.getByRole('heading', {name: locale === 'en' ? 'No projects yet' : '尚無專案', exact: true})).toBeVisible();
      } else {
        await expect(panel.getByRole('heading', {name: locale === 'en' ? 'No admitted work' : '尚無已受理工作', exact: true})).toBeVisible();
        await expect(panel.locator('[data-pm-lifecycle-history] > summary')).toHaveText(locale === 'en' ? 'Phase history' : '管理階段歷史');
      }
      await expect(panel.locator('[data-pm-source-pending]')).toHaveCount(0);
      await shot(page, 'discussions-' + (task === 'ui_change_phase' ? 'phase' : 'settings'), locale, width);
      const first = panel.locator(width < 800 ? '.ui-inspector-back' : ':scope > header > .ui-icon-button');
      const last = task === 'ui_workspace_settings'
        ? panel.getByRole('combobox', {name: locale === 'en' ? 'Connector project' : 'Connector 專案', exact: true})
        : panel.locator('[data-pm-lifecycle-history] > summary');
      await first.focus();
      await page.keyboard.press('Shift+Tab');
      expect(await panel.evaluate(node => node.contains(document.activeElement))).toBe(true);
      await expect(last).toBeFocused();
      await page.keyboard.press('Tab'); await expect(first).toBeFocused();
      await page.keyboard.press('Escape'); await expect(panel).toHaveCount(0); await expect(page.locator('[data-pm-overflow]')).toBeFocused();
      await expect(thread.getByRole('textbox').first()).toBeEditable();
    }
    expect(f.base.base.state.writes).toHaveLength(0); await context.close();
  });
  test(`Report controls fit their text and Save is primary ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({locale, baseURL, viewport: {width, height: 900}}), page = await context.newPage(), f = shellReviewFixture();
    await mountConversation(page, false, f.dispatch); await page.goto(`/dashboard/#/pm/${workspaceId}/overview`);
    await page.locator('[data-pm-report-menu]').click(); await page.locator('[data-menu-key=export]').click();
    const fields = page.locator('.ui-inspector .pm-export-fields'); await expect(fields).toBeVisible();
    for (const format of ['markdown', 'html', 'svg']) {
      const select = fields.locator('select').first(); await select.selectOption(format);
      const fit = await select.evaluate(node => {
        const input = node as HTMLSelectElement, css = getComputedStyle(input), ctx = document.createElement('canvas').getContext('2d')!; ctx.font = css.font;
        return {text: input.selectedOptions[0].textContent, needed: ctx.measureText(input.selectedOptions[0].textContent!).width + parseFloat(css.paddingLeft) + parseFloat(css.paddingRight) + 24, width: input.clientWidth};
      }); expect(fit.width, fit.text!).toBeGreaterThanOrEqual(fit.needed);
    }
    const formatBox = await fields.locator('label').first().boundingBox(), languageBox = await fields.locator('label').last().boundingBox();
    expect(languageBox!.y).toBeGreaterThanOrEqual(formatBox!.y + formatBox!.height);
    await page.mouse.move(0, 0);
    const download = page.locator('[data-pm-brief-export] .ui-button.primary');
    const accent = await page.evaluate(() => {
      const swatch = document.createElement('span'); swatch.style.color = 'var(--accent)'; document.body.append(swatch);
      const colour = getComputedStyle(swatch).color; swatch.remove(); return colour;
    });
    await expect.poll(() => download.evaluate(node => getComputedStyle(node).backgroundColor)).toBe(accent);
    const primaryColour = await download.evaluate(node => getComputedStyle(node).backgroundColor);
    await fields.locator('select').first().selectOption('markdown'); await shot(page, 'report-export', locale, width);
    await page.keyboard.press('Escape'); await page.locator('[data-pm-report-menu]').click(); await page.locator('[data-menu-key=save]').click();
    const save = page.locator('[data-pm-action="pm.snapshot.create"] button[type=submit]');
    await expect(save).toHaveText(locale === 'en' ? 'Save report' : '儲存報告'); await expect(save).toBeEnabled(); await expect(save).toHaveClass(/\bprimary\b/); await expect(save).toBeFocused();
    await page.mouse.move(0, 0);
    await expect.poll(() => save.evaluate(node => getComputedStyle(node).backgroundColor)).toBe(primaryColour);
    await shot(page, 'report-save', locale, width);
    const panel = page.locator('main > .ui-inspector'), bounds = await panel.boundingBox(); expect(bounds!.y + bounds!.height).toBe(900);
    if (width >= 1200) {await expect(panel).toHaveAttribute('role', 'complementary'); expect(bounds!.y).toBe(48);}
    expect(f.base.base.state.writes).toHaveLength(0); await context.close();
  });
}
for (const locale of ['en', 'zh-TW']) for (const width of [1000, 390]) {
  test(`Discussion overlay focus stays inside inspector while loading ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({locale, baseURL, viewport: {width, height: 900}}), page = await context.newPage(), f = shellReviewFixture();
    let releaseEvidence!: () => void;
    f.state.holdEvidence = new Promise<void>(resolve => { releaseEvidence = resolve; });
    await mountConversation(page, false, f.dispatch); await page.goto(pmRoute);
    await openPmPanel(page, 'ui_workspace_settings');
    const panel = page.locator('main > .ui-inspector');
    await expect(panel.locator('[data-pm-source-pending]')).toHaveCount(1);
    await expect(panel.locator('[data-pm-source-loading]')).toBeVisible();
    const first = panel.locator(width < 800 ? '.ui-inspector-back' : ':scope > header > .ui-icon-button');
    await first.focus();
    await page.keyboard.press('Shift+Tab');
    expect(await panel.evaluate(node => node.contains(document.activeElement))).toBe(true);
    expect(await page.evaluate(() => getComputedStyle(document.activeElement!).visibility)).toBe('visible');
    expect(await page.evaluate(() => document.activeElement !== document.body)).toBe(true);
    await page.keyboard.press('Tab');
    await expect(first).toBeFocused();
    expect(await page.evaluate(() => document.activeElement !== document.body && getComputedStyle(document.activeElement!).visibility === 'visible')).toBe(true);
    releaseEvidence();
    await expect(panel.getByRole('heading', {name: locale === 'en' ? 'No projects yet' : '尚無專案', exact: true})).toBeVisible();
    await expect(panel.locator('[data-pm-source-pending]')).toHaveCount(0);
    const last = panel.getByRole('combobox', {name: locale === 'en' ? 'Connector project' : 'Connector 專案', exact: true});
    await page.keyboard.press('Shift+Tab');
    await expect(last).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(first).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(panel).toHaveCount(0);
    expect(f.base.base.state.writes).toHaveLength(0);
    await context.close();
  });
}
for (const locale of ['en', 'zh-TW']) {
  for (const width of [1440, 1280, 1000, 390]) test(`Lifecycle uses phase and identifies admitted work ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({locale, baseURL, viewport: {width, height: 900}}), page = await context.newPage(), f = shellReviewFixture(); f.state.stage = null;
    await mountConversation(page, false, f.dispatch); await page.goto(pmRoute); await openPmPanel(page, 'ui_change_phase'); const panel = page.locator('[data-pm-lifecycle]');
    await expect(panel).toContainText(locale === 'en' ? 'Phase not yet selected' : '尚未指定管理階段');
    await expect(panel).toContainText(locale === 'en' ? 'Existing work keeps its classification.' : '既有工作保留原分類。');
    await expect(panel.getByRole('heading', {name: locale === 'en' ? 'No admitted work' : '尚無已受理工作', exact: true})).toBeVisible();
    await expect(panel.locator('[data-pm-lifecycle-history] > summary')).toHaveText(locale === 'en' ? 'Phase history' : '管理階段歷史');
    await shot(page, 'lifecycle-unclassified', locale, width);
    await panel.locator('[data-pm-lifecycle-controls] > summary').click(); await panel.locator('[data-pm-lifecycle-intent=set_stage] > summary').click();
    await shot(page, 'lifecycle-controls', locale, width);
    await expect(panel.getByRole('combobox', {name: locale === 'en' ? 'Management phase' : '管理階段', exact: true})).toBeVisible();
    if (locale === 'en') await expect(panel).not.toContainText(/\bstage\b/i); expect(f.base.base.state.writes).toHaveLength(0); await context.close();
  });
  for (const width of [1440, 390]) for (const code of [401, 403, 404, 503]) test(`Settings gives one access reason ${code} ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({locale, baseURL, viewport: {width, height: 900}}), page = await context.newPage(), f = shellReviewFixture(); f.state.projectError = code;
    await mountConversation(page, false, f.dispatch); await page.goto(`/dashboard/#/pm/${workspaceId}/overview`);
    if (code === 401) {
      // A refused credential pauses every resource read. Connection Settings
      // owns its one access reason and explicit recovery, including this route.
      const copy = STRINGS[locale];
      await expect(page.locator('#live')).toHaveText(copy.ui_connection_refused);
      let requests = 0;
      page.on('request', request => {if (new URL(request.url()).pathname.startsWith('/api/v1/')) requests++;});
      await page.locator('#live').click();
      await expect(page.locator('.global-connection-facts .ui-pill')).toHaveText(copy.ui_connection_refused);
      await expect(page.locator('#main').getByText(copy.ui_connection_refused, {exact: true})).toHaveCount(1);
      await expect(page.getByRole('button', {name: copy.connection_retry_remembered, exact: true})).toBeVisible();
      await expect(page.locator('[data-pm-evidence]')).toHaveCount(0);
      expect(requests).toBe(0);
      expect(f.base.base.state.writes).toHaveLength(0); await context.close(); return;
    }
    await openPmPanel(page, 'ui_workspace_settings');
    const panel = page.locator('[data-pm-evidence]'), reason = locale === 'en' ? {401: 'Token not accepted.', 403: 'Your token lacks this permission.', 404: 'Project data was not found.', 503: 'Connector is unavailable.'}[code]!
      : {401: '權杖無效。', 403: '你的權杖沒有這個權限。', 404: '找不到專案資料。', 503: '無法連線至 Connector。'}[code]!;
    await expect(panel.getByRole('heading', {name: locale === 'en' ? 'Projects unavailable' : '無法取得專案', exact: true})).toBeVisible();
    await expect(panel.locator('.ui-empty > p')).toHaveText(reason); await expect(panel.getByText(reason, {exact: true})).toHaveCount(1);
    await expect(panel.getByRole('combobox')).toBeHidden(); await expect(panel.locator('.error, [role=alert]')).toHaveCount(0);
    if (locale === 'zh-TW') await expect(panel).not.toContainText('token'); await shot(page, `settings-${code}`, locale, width);
    expect(f.base.base.state.writes).toHaveLength(0); await context.close();
  });
  for (const width of [1440, 390]) test(`Docked phase fills the viewport and its body scrolls ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({locale, baseURL, viewport: {width, height: 900}}), page = await context.newPage(), f = shellReviewFixture();
    await mountConversation(page, false, f.dispatch); await page.goto(`/dashboard/#/pm/${workspaceId}/overview`); await openPmPanel(page, 'ui_change_phase');
    const panel = page.locator('main > .ui-inspector'); if (width >= 1200) await expect(panel).toHaveAttribute('role', 'complementary');
    await panel.locator('[data-pm-lifecycle-controls] > summary').click();
    for (const summary of await panel.locator('[data-pm-lifecycle-intent] > summary').all()) await summary.click();
    const bounds = await panel.boundingBox(); expect(bounds!.y + bounds!.height).toBe(900);
    const body = panel.locator('.ui-inspector-body'); expect(await body.evaluate(node => getComputedStyle(node).overflowY)).toBe('auto');
    expect(await body.evaluate(node => node.scrollHeight > node.clientHeight)).toBe(true); await body.evaluate(node => {node.scrollTop = node.scrollHeight;});
    await expect(panel.locator('[data-pm-lifecycle-history] > summary')).toBeInViewport(); expect((await panel.boundingBox())!.y).toBe(bounds!.y);
    await shot(page, 'docked-phase-scroll', locale, width); expect(f.base.base.state.writes).toHaveLength(0); await context.close();
  });
  for (const width of [1440, 390]) test(`Global crumbs have no initial slash and project crumbs keep one ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({locale, baseURL, viewport: {width, height: 900}}), page = await context.newPage(), f = shellReviewFixture(); const projects = f.base.base.state.workspaces; f.base.base.state.workspaces = [];
    await mountConversation(page, false, f.dispatch);
    for (const route of ['projects', 'operations']) {
      await page.goto('/dashboard/#/' + route); const crumb = page.locator('#nav'), label = locale === 'en' ? route === 'projects' ? 'All projects' : 'Operations' : route === 'projects' ? '所有專案' : '操作紀錄';
      await expect(crumb.locator(width >= 1000 ? '.shell-crumb-view' : '.shell-mobile-name')).toHaveText(label);
      await expect(crumb.locator('.shell-breadcrumb-divider')).toHaveCount(0); await shot(page, 'crumb-' + route, locale, width);
    }
    f.base.base.state.workspaces = projects; await page.goto(`/dashboard/#/pm/${workspaceId}/requirements`);
    await expect(page.locator('#nav .shell-breadcrumb-divider')).toHaveCount(1); await expect(page.locator('#nav .shell-crumb-workspace')).toHaveText('Library refresh');
    await shot(page, 'crumb-project', locale, width); expect(f.base.base.state.writes).toHaveLength(0); await context.close();
  });
}
for (const locale of ['en', 'zh-TW']) for (const width of [1440, 1280, 1000, 390]) test(`Requirements keeps its inspector docked only on wide screens ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({locale, baseURL, viewport: {width, height: 900}}), page = await context.newPage(), f = shellReviewFixture();
  await mountConversation(page, false, f.dispatch); await page.goto(`/dashboard/#/pm/${workspaceId}/requirements`);
  const row = page.locator('[data-rtm-requirement]').first();
  await row.focus(); await row.press('ArrowRight'); await expect(row).toHaveAttribute('aria-expanded', 'true');
  await page.locator('[data-rtm-work] [data-rtm-select]').first().click(); const panel = page.locator('[data-work-inspector]');
  await expect(panel).toBeVisible(); await expect(panel).toContainText('PR #10');
  await expect(panel).toHaveAttribute('role', width >= 1200 ? 'complementary' : 'dialog');
  if (width >= 1200) {
    const header = await page.locator('.pm-main > .ui-page-header').boundingBox(), dock = await panel.boundingBox();
    expect(header!.x + header!.width).toBeLessThanOrEqual(dock!.x);
    await page.locator('[data-pm-refresh]').click(); await expect(panel).toContainText('PR #10');
  }
  const bounds = await panel.boundingBox(); expect(bounds!.y + bounds!.height).toBe(900); if (width >= 1200) expect(bounds!.y).toBe(48);
  await shot(page, 'requirements-docked', locale, width); expect(f.base.base.state.writes).toHaveLength(0); await context.close();
});

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 1280, 1000, 390]) test(`Session header actions stay clear of Results ${locale} ${width}`, async ({browser}) => {
  const s = await openSession(browser, {locale, width, height: 900});
  try {
    await fromRequirements(s.page, s.ids, 'manual');
    const panel = s.page.locator('.ui-inspector'), header = s.page.locator('.workspace-session-heading .ui-page-header');
    if (width >= 1200) {
      await expect(panel).toBeVisible();
      const title = await header.boundingBox(), dock = await panel.boundingBox();
      expect(title!.x + title!.width).toBeLessThanOrEqual(dock!.x);
      expect(dock!.y).toBe(48); expect(dock!.y + dock!.height).toBe(900);
    } else await expect(panel).toBeHidden();
    const more = header.getByRole('button', {name: locale === 'en' ? 'More actions' : '更多操作', exact: true});
    await more.click();
    await expect(s.page.getByRole('menuitem', {name: locale === 'en' ? 'Discuss' : '討論', exact: true})).toBeVisible();
    await s.page.keyboard.press('Escape'); await expect(more).toBeFocused();
    await shot(s.page, 'session-header', locale, width);
    expect(s.writes).toHaveLength(0);
  } finally {await s.context.close();}
});
