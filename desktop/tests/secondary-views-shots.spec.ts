import {test, expect, type Locator} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {reviewFixture} from './requirements-review-fixture.ts';
import {openPmPanel, openPmSection} from './pm-navigation.ts';

// Opt-in evidence capture uses the same mock central records as the experience tests.
for (const locale of ['en', 'zh-TW']) for (const width of [1440, 1000, 768, 390]) {
  test(`secondary views screenshots ${locale} ${width}`, async ({browser, baseURL}) => {
    test.skip(!process.env.BATC_SECONDARY_SHOTS, 'Opt-in screenshots');
    test.setTimeout(120_000);
    const context = await browser.newContext({baseURL, locale, timezoneId: 'UTC', colorScheme: 'light', reducedMotion: 'reduce', viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage(), f = reviewFixture(locale);
    let mode = 'normal';
    f.overrides.push((url, r) => {
      if (['/bootstrap', '/capabilities'].includes(url.pathname)) {
        const caps = url.pathname === '/bootstrap' ? r.data.capabilities : r.data;
        caps.features.pm_workspaces.brief_export = {formats: ['markdown', 'html', 'svg'], locales: ['en', 'zh-TW']};
      }
      if (url.pathname.endsWith('/overview') && r.status === 200) {
        r.data.progress = {...r.data.progress, requirements: 2, accepted: 0, in_review: 2, in_progress: 0, blocked: 0, not_started: 0};
        if (mode === 'empty') {r.data.attention = []; r.data.attention_counts = {blocker: 0, decision: 0, risk: 0, deviation: 0}; r.data.milestones = []; r.data.recent_deliveries = [];}
      }
      if (mode === 'empty' && url.pathname.endsWith('/plans/accepted') && r.status === 200) r.data.plan.document.requirements = [];
      if (mode === 'empty' && /\/snapshots(?:\/latest)?$/.test(url.pathname)) {if (r.data.snapshots) r.data.snapshots = []; else r.data.snapshot = null;}
      if (mode === 'empty' && url.pathname.endsWith('/kanban')) {r.data.cards = []; r.data.next_cursor = null;}
      if (mode === 'no-criteria' && url.pathname.endsWith('/plans/accepted')) r.data.plan.document.requirements.forEach((row: any) => {row.acceptance = [];});
      if (mode === 'no-criteria' && url.pathname.endsWith('/quality-reviews')) r.data.reviews = [];
      if (mode === 'error' && /\/(overview|kanban|snapshots(?:\/[^/]+)?)$/.test(url.pathname)) return {status: 404, data: {error: {code: 'NOT_FOUND', message: locale === 'en' ? 'Record unavailable' : '無法取得紀錄'}}};
      return r;
    });
    const out = process.env.BATC_SECONDARY_SHOTS!;
    await mkdir(out, {recursive: true});
    const shot = async (name: string, target?: Locator) => {
      if (target) await target.screenshot({path: `${out}/${name}-${locale}-${width}.png`, animations: 'disabled'});
      else await page.screenshot({path: `${out}/${name}-${locale}-${width}.png`, animations: 'disabled', fullPage: false});
    };
    const go = async (view: string, select = '') => {
      await page.goto('about:blank');
      await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/${view}${select ? '?select=' + select : ''}`);
      await expect(page.locator(`[data-pm-region=${view}]`)).toBeVisible();
    };
    await f.mount(page, 'overview');
    await expect(page.locator('[data-overview-metric=progress]')).toContainText(locale === 'en' ? '0 of 2 accepted' : '2 項中 0 項已驗收');
    await shot('overview-progress', page.locator('[data-overview-status]'));
    await shot('open-decisions', page.locator('[data-overview-metric=decision]'));
    await openPmPanel(page, 'ui_report');
    await expect(page.locator('[data-report-compare]')).toBeEnabled();
    await page.locator('[data-pm-brief-export] > summary').click();
    await shot('report');
    await page.locator('[data-report-compare]').click();
    await expect(page.locator('.pm-charter-comparison')).toBeVisible();
    await shot('comparison', page.locator('.pm-charter-comparison > section').first());
    await page.keyboard.press('Escape');
    await openPmSection(page, 'requirements');
    await expect(page.locator('[data-rtm-requirement]')).toHaveCount(8);
    await shot('requirements-closed');
    await go('requirements', f.ids.requirements.email);
    await expect(page.locator('[data-work-section=criteria]')).toBeVisible();
    await shot('requirements-open');
    await shot('requirement-inspector', page.locator('[data-work-inspector]'));
    await page.keyboard.press('Escape');
    mode = 'no-criteria';
    await go('requirements', f.ids.requirements.email);
    await expect(page.locator('[data-work-section=criteria] > [data-status=not_assessed]')).toBeVisible();
    await expect(page.locator('[data-work-section=criteria] li')).toHaveCount(0);
    await shot('requirement-inspector-empty', page.locator('[data-work-inspector]'));
    mode = 'normal';
    // Day is intentionally unavailable on phones; capture its restored Week layout.
    await go('schedule');
    await expect(page.locator('.schedule-row').first()).toBeAttached();
    if (width === 390) {
      await page.setViewportSize({width: 1000, height: 900});
      await page.getByRole('radio', {name: locale === 'en' ? 'Day' : '日', exact: true}).click();
      await page.setViewportSize({width, height: 844});
      await expect(page.locator('[data-pm-schedule]')).toHaveAttribute('data-schedule-layout', 'phone');
      await shot('schedule-day-restored-week');
      await shot('schedule-week');
    }
    for (const state of ['empty', 'error']) {
      mode = state;
      await go('overview');
      await expect(page.locator('[data-pm-refresh]')).toBeEnabled();
      if (state === 'empty') await expect(page.locator('[data-overview-row]')).toHaveCount(0);
      else await expect(page.locator('[data-pm-overview]')).toContainText('NOT_FOUND');
      await shot(`overview-${state}`);
      await openPmPanel(page, 'ui_report');
      await expect(page.locator('[data-report-compare]')).toBeDisabled();
      await shot(`report-${state}`);
      await page.keyboard.press('Escape');
      await go('requirements');
      if (state === 'empty') await expect(page.locator('[data-pm-work-meta]')).toContainText(locale === 'en' ? '0 requirements' : '0 項需求');
      else await expect(page.locator('[data-pm-rtm]')).toContainText(locale === 'en' ? 'Requirement records unavailable' : '無法取得需求紀錄');
      await expect(page.locator('[data-rtm-requirement]')).toHaveCount(0);
      await shot(`requirements-${state}`);
      if (state === 'error') {
        await go('requirements', f.ids.requirements.email);
        await expect(page.locator('[data-pm-rtm]')).toContainText(locale === 'en' ? 'Requirement records unavailable' : '無法取得需求紀錄');
        await expect(page.locator('[data-work-inspector]')).toHaveCount(0);
        await shot('requirement-inspector-error');
      }
    }
    expect(f.writes).toEqual([]);
    await context.close();
  });
}
