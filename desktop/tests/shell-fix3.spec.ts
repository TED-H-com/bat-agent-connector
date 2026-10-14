import {test, expect, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {reviewFixture} from './requirements-review-fixture.ts';
import {openPmPanel} from './pm-navigation.ts';

async function shot(page: Page, state: string, locale: string, width: number) {
  const directory = process.env.BATC_FIX3_SHOTS;
  if (!directory) return;
  await mkdir(directory, {recursive: true});
  await page.screenshot({path: `${directory}/fix3-${state}-${locale}-${width}.png`, animations: 'disabled', fullPage: false});
}

for (const locale of ['en', 'zh-TW']) {
  for (const width of [1440, 390]) test(`Project phases agree in the shell, Overview, choices and history ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: 900}}), page = await context.newPage(), f = reviewFixture(locale);
    const labels = locale === 'en' ? ['Idea', 'Planning', 'Development', 'Maintain', 'Paused', 'Closed'] : ['構想', '規劃', '開發', '維護', '已暫停', '已結案'];
    const phases = ['idea', 'planning', 'development', 'maintain', 'paused', 'closed'];
    let stage = phases[0];
    const transitionId = 'pmlt_' + 'a'.repeat(32), transition = () => ({transition_id: transitionId, from_stage: 'idea', to_stage: stage,
      reason: locale === 'en' ? 'Review project phase' : '檢視專案階段', actor: 'reader'});
    f.overrides.push((url, result) => {
      if (['/bootstrap', '/capabilities'].includes(url.pathname)) {
        const caps = url.pathname === '/bootstrap' ? result.data.capabilities : result.data;
        caps.features.pm_workspaces.lifecycle = {version: 1, gated_actions: [], record_actions: []};
        caps.actions.push({action: 'pm.lifecycle.set_stage', allowed: true});
      }
      if (url.pathname.endsWith('/overview')) result.data.lifecycle.stage = stage;
      if (url.pathname.endsWith('/lifecycle')) return {status: 200, data: {workspace_id: f.ids.workspaceId,
        lifecycle: {stage, status: ['paused', 'closed'].includes(stage) ? stage : 'active', revision: 1, execution_epoch: 1, transition_id: null, coverage: 'recorded'},
        transition: null, blocked_actions: [], available_stages: phases.slice(0, 4), inflight_summary: {visible_unresolved: 0, visible_retained: 0, visibility_filtered: false}, maintenance: {available: false}}};
      if (url.pathname.endsWith('/lifecycle/transitions')) return {status: 200, data: {workspace_id: f.ids.workspaceId, transitions: [transition()], next_cursor: null}};
      if (url.pathname.endsWith('/lifecycle/transitions/' + transitionId)) return {status: 200, data: {workspace_id: f.ids.workspaceId, transition: transition()}};
      return result;
    });
    await f.mount(page, 'overview');
    for (const [index, phase] of phases.entries()) {
      stage = phase;
      if (index) {await page.goto('about:blank'); await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/overview`);}
      await expect(page.locator('.pm-main > .ui-page-header .ui-title-row > .ui-pill')).toHaveText(labels[index]);
      if (width < 1000) await page.locator('#workspace-menu').click();
      await expect(page.locator('[data-shell-phase]')).toHaveText(labels[index]);
      if (width < 1000) await page.keyboard.press('Escape');
      await openPmPanel(page, 'ui_change_phase');
      const lifecycle = page.locator('[data-pm-lifecycle]');
      await expect(lifecycle.locator('.pm-lifecycle-stage')).toHaveText(labels[index]);
      await shot(page, 'phase-' + phase + '-state', locale, width);
      await lifecycle.locator('[data-pm-lifecycle-controls] > summary').click();
      await lifecycle.locator(`[data-pm-lifecycle-intent=${['paused', 'closed'].includes(stage) ? 'reopen' : 'set_stage'}] > summary`).click();
      await expect(lifecycle.locator('select:visible option').filter({hasNotText: locale === 'en' ? 'Choose phase' : '選擇階段'})).toHaveText(labels.slice(0, 4));
      await lifecycle.locator('[data-pm-lifecycle-history] > summary').click();
      await lifecycle.locator('[data-pm-lifecycle-history] details > summary').click();
      await expect(lifecycle.locator('[data-pm-lifecycle-history-phase]')).toHaveText(`${labels[0]} → ${labels[index]}`);
      await shot(page, 'phase-' + phase, locale, width);
      await page.keyboard.press('Escape');
    }
    expect(f.writes).toEqual([]); await context.close();
  });

  for (const width of [1440, 1280, 1000, 390]) for (const view of ['issues', 'requirements', 'overview', 'agents', 'schedule']) test(`Docked ${view} keeps one main-column edge ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: 900}}), page = await context.newPage(), f = reviewFixture(locale);
    await f.mount(page, view, view === 'requirements' ? f.ids.requirements.email : '');
    if (view === 'issues') await page.locator('[data-reg-table] [data-row-key]').first().click();
    if (view === 'overview') await page.locator('[data-charter-plan]').click();
    if (view === 'agents') await page.locator('[data-resource-row]').first().click();
    if (view === 'schedule') await page.locator('[data-schedule-select^="milestone:"]').first().click();
    const panel = page.locator('.ui-inspector:visible');
    await expect(panel).toHaveAttribute('role', width >= 1200 ? 'complementary' : 'dialog');
    const region = page.locator(`[data-pm-region=${view}]`);
    const edges = await region.evaluate(node => {
      const candidates = node.querySelectorAll(':scope > [data-pm-section], .reg-main, .pm-requirements, .pm-requirements-toolbar, .pm-requirements-tables, .pm-requirements > .ui-disclosure, .pm-overview-status, .pm-overview-columns, .pm-overview-charter, .pm-agents-main, .pm-agents-list, [data-resource-quota], .schedule-main, .schedule-toolbar');
      return [...candidates].filter(child => child.getBoundingClientRect().width > 0 && !child.closest('.ui-inspector')).map(child => {
        const bounds = child.getBoundingClientRect(); return {name: child.className, right: bounds.right};
      });
    });
    expect(edges.length).toBeGreaterThan(1);
    if (width >= 1200) {
      const right = Math.min(...edges.map(edge => edge.right));
      for (const edge of edges) expect(Math.abs(edge.right - right), edge.name).toBeLessThanOrEqual(1);
      expect(right).toBeLessThan((await panel.boundingBox())!.x);
      const header = await page.locator('.pm-main > .ui-page-header').boundingBox();
      expect(header!.x + header!.width).toBeCloseTo(right, 0);
      if (view === 'agents') expect(await page.locator('[data-resource-row] .ui-pill').evaluateAll(nodes => nodes.flatMap(node => {
        const pill = node.getBoundingClientRect();
        const port = node.closest('.ui-data-table, .pm-agents-list')?.getBoundingClientRect();
        const cut = node.scrollWidth > node.clientWidth + 1 || (port && pill.width > 0 && (pill.left < port.left - 1 || pill.right > port.right + 1));
        return cut ? [node.textContent] : [];
      }))).toEqual([]);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await shot(page, view + '-docked', locale, width);
    await page.keyboard.press('Escape');
    await expect(panel).toHaveCount(0);
    await expect(region).toBeVisible();
    expect(f.writes).toEqual([]); await context.close();
  });
}
