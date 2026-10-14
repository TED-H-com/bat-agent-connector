import {test, expect} from '@playwright/test';
import {reviewFixture} from './requirements-review-fixture.ts';
import {openPmPanel, openPmSection} from './pm-navigation.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';

for (const locale of ['en', 'zh-TW']) {
  for (const width of [1440, 1000]) test(`Requirements header matches its rows ${locale} ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 900});
    const f = reviewFixture(locale);
    await f.mount(page, 'requirements');
    await expect(page.locator('[data-rtm-requirement]')).toHaveCount(8);
    const header = page.locator('.pm-requirements thead'), compact = page.locator('.ui-data-table-compact:visible');
    if (width === 1000) {await expect(header).toBeHidden(); await expect(compact).toHaveCount(8);}
    else {await expect(header).toBeVisible(); await expect(compact).toHaveCount(0);}
    await page.evaluate(id => {location.hash += '?select=' + id;}, f.ids.requirements.email);
    await expect(page.locator('[data-work-section=criteria]')).toBeVisible();
    // A modal makes the background inert at 1000; measure display rather than accessibility visibility.
    expect(await header.evaluate(node => getComputedStyle(node).display)).toBe(width === 1000 ? 'none' : 'contents');
    await page.keyboard.press('Escape');
    if (width === 1000) await expect(header).toBeHidden(); else await expect(header).toBeVisible();
    expect(f.writes).toEqual([]);
  });

  for (const width of [800, 768, 390]) test(`Report controls fit touch ${locale} ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 900});
    const f = reviewFixture(locale);
    f.overrides.push((url, r) => {
      if (['/bootstrap', '/capabilities'].includes(url.pathname)) {
        const caps = url.pathname === '/bootstrap' ? r.data.capabilities : r.data;
        caps.features.pm_workspaces.brief_export = {formats: ['markdown', 'html', 'svg'], locales: ['en', 'zh-TW']};
      }
      return r;
    });
    await f.mount(page, 'overview'); await openPmPanel(page, 'ui_report');
    await expect(page.locator('[data-report-compare]')).toBeEnabled();
    await page.locator('[data-pm-brief-export] > summary').click();
    const tools = page.locator('[data-pm-report-tools]');
    expect(await tools.evaluate(node => Boolean(node.closest('.pm-main')))).toBe(false);
    const measurements = await tools.locator('select:visible, button:visible').evaluateAll(nodes => nodes.map(node => {
      const css = getComputedStyle(node), r = node.getBoundingClientRect();
      const range = document.createRange(); range.selectNodeContents(node);
      return {text: node.textContent, height: r.height, input: node.tagName === 'SELECT', font: parseFloat(css.fontSize),
        wraps: node.tagName === 'BUTTON' && range.getBoundingClientRect().height > parseFloat(css.lineHeight) + 1};
    }));
    expect(measurements).toHaveLength(7);
    for (const m of measurements) {expect(m.height, m.text || '').toBeGreaterThanOrEqual(44); if (m.input) expect(m.font).toBe(16); else expect(m.wraps, m.text || '').toBe(false);}
    await expect(page.locator('[data-report-compare]')).toHaveText(locale === 'en' ? 'Compare' : '比較');
    await expect(page.locator('[data-report-compare]')).toHaveAttribute('aria-description', /#1/);
    await expect(page.locator('[data-report-print]')).toHaveText(locale === 'en' ? 'Print' : '列印');
    expect(f.writes).toEqual([]);
  });

  test(`Report comparison foregrounds changed statements ${locale}`, async ({page}) => {
    const f = reviewFixture(locale);
    await f.mount(page, 'overview'); await openPmPanel(page, 'ui_report');
    await expect(page.locator('[data-report-compare]')).toBeEnabled(); await page.locator('[data-report-compare]').click();
    const purpose = page.locator('[data-report-section=definition]');
    await expect(purpose.locator('[data-report-pair]')).toHaveCount(1);
    await expect(purpose.locator('[data-report-side=before]')).toContainText(f.copy.goalV2);
    await expect(purpose.locator('[data-report-side=after]')).toContainText(f.copy.goal);
    await expect(purpose.locator('[data-report-side=before]')).toContainText('#1');
    await expect(purpose.locator('[data-report-side=after]')).toContainText('#2');
    const unchanged = purpose.locator('[data-report-unchanged]');
    await expect(unchanged).not.toHaveAttribute('open', '');
    await expect(unchanged.locator('summary')).toHaveText(locale === 'en' ? 'Unchanged (2)' : '未變更（2）');
    await expect(purpose.getByText(f.copy.stakeholders, {exact: true})).toBeHidden();
    await unchanged.locator('summary').click();
    await expect(purpose.getByText(f.copy.stakeholders, {exact: true})).toBeVisible();
    await expect(page.locator('[data-report-change=added]').first()).toHaveText(locale === 'en' ? 'Added' : '新增');
    expect(f.writes).toEqual([]);
  });

  test(`Requirement acceptance tells one story ${locale}`, async ({page}) => {
    const f = reviewFixture(locale);
    await f.mount(page, 'requirements', f.ids.requirements.email);
    const criteria = page.locator('[data-work-section=criteria]'), result = page.locator('[data-work-section=result]');
    await expect(criteria.locator('li')).toHaveCount(1);
    await expect(criteria.locator('[data-status=accepted]')).toHaveCount(1);
    await expect(result).toContainText(locale === 'en' ? 'Quality review #1' : '品質審閱 #1');
    await expect(result.locator('time')).toHaveAttribute('datetime', /\d{4}-/);
    await expect(result).toContainText(locale === 'en' ? 'Matches this plan, scope and every acceptance input.' : '符合本輪計畫、範圍與每項驗收條件。');
    const text = f.copy.requirements[0].acceptance;
    await expect(page.locator('[data-work-inspector] li').filter({hasText: text})).toHaveCount(2);
    await expect(result.locator('li').filter({hasText: text})).toBeHidden();
    await result.locator('[data-requirement-review-criteria] > summary').click();
    await expect(result.locator('li').filter({hasText: text})).toBeVisible();
    expect(f.writes).toEqual([]);
  });

  test(`Open decisions preserve text width and keyboard access ${locale}`, async ({page}) => {
    await page.setViewportSize({width: 390, height: 844});
    const f = reviewFixture(locale); await f.mount(page, 'overview');
    const row = page.locator('[data-overview-open-decision]'), title = row.locator('.pm-overview-link'), link = row.getByRole('link');
    await expect(title).toHaveText(f.copy.decisions.budget);
    const bounds = await row.evaluate(node => {
      const card = node.closest('.ui-metric')!.getBoundingClientRect(), text = node.querySelector('.pm-overview-link')!.getBoundingClientRect(), action = node.querySelector('a')!.getBoundingClientRect();
      return {card: card.width, text: text.width, textBottom: text.bottom, actionTop: action.top};
    });
    expect(bounds.text).toBeGreaterThanOrEqual(bounds.card * .6); expect(bounds.actionTop).toBeGreaterThanOrEqual(bounds.textBottom);
    await title.focus(); await page.keyboard.press('Tab'); await expect(link).toBeFocused(); await page.keyboard.press('Enter');
    await expect(page.locator('[data-discussion-title]')).toHaveText(f.copy.decisions.budget);
    expect(f.writes).toEqual([]);
  });

  test(`Delivered work explains missing acceptance and opens a sourced draft ${locale}`, async ({page}) => {
    const f = reviewFixture(locale);
    let state = 'delivered';
    f.overrides.push((url, r) => {
      if (url.pathname.endsWith('/overview') && r.status === 200) {
        r.data.progress = {...r.data.progress, requirements: 2, accepted: 0, in_review: 2, in_progress: 0, blocked: 0, not_started: 0};
        if (state === 'unknown') r.data.progress = null;
        if (state === 'partial') r.data.progress.complete = false;
        if (state === 'no-delivery') r.data.recent_deliveries = [];
        if (state === 'accepted') r.data.progress = {...r.data.progress, accepted: 2, in_review: 0};
      }
      return r;
    });
    await f.mount(page, 'overview');
    const gap = page.locator('[data-overview-acceptance-gap]');
    await expect(gap).toContainText(locale === 'en' ? "Work was delivered; acceptance against this round's criteria is still missing." : '已有交付紀錄，尚缺對應本輪接受條件的驗收。');
    await page.locator('[data-overview-unaccepted]').click();
    await expect(page.locator('[data-pm-region=requirements]')).toBeVisible();
    await openPmSection(page, 'overview');
    const ask = page.locator('[data-overview-ask-progress]'), launch = parseDiscussRefs((await ask.getAttribute('href'))!.split('?')[1]);
    const title = locale === 'en' ? 'Progress · 0 of 2 accepted' : '進度 · 2 項中 0 項已驗收';
    expect(launch.title).toBe(title); expect(launch.refs).toEqual([{kind: 'plan_revision', id: f.ids.planV3}]);
    await ask.click();
    await expect(page.locator('[data-discussion-title]')).toHaveText(title);
    await expect(page.locator('[data-discussion-context=plan_revision]')).toContainText(f.copy.planTitle);
    await expect(page.locator('[data-discussion-composer]')).toBeFocused();
    await openPmSection(page, 'overview');
    for (state of ['unknown', 'partial', 'no-delivery', 'accepted']) {
      await page.locator('[data-pm-refresh]').click(); await expect(gap).toHaveCount(0);
    }
    state = 'delivered'; await page.locator('[data-pm-refresh]').click(); await expect(gap).toBeVisible();
    // Withdrawal removes the explanation and both links, together with its evidence.
    f.overrides.push((url, r) => url.pathname.endsWith('/overview') ? {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Unavailable'}}} : r);
    await page.locator('[data-pm-refresh]').click(); await expect(gap).toHaveCount(0);
    await expect(page.locator('[data-overview-delivery]')).toHaveCount(0);
    expect(f.writes).toEqual([]);
  });
}
