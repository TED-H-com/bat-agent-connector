import {mkdirSync} from 'node:fs';
import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture, demoRoute} from './demo/index.ts';
import {demoProject} from './demo/project.ts';
import {registerIds} from './demo/register.ts';

const shots = `${process.env.TMPDIR || '/tmp'}/finding-name-shots`;

function copyFor(locale: 'en' | 'zh-TW') {
  const zh = locale === 'zh-TW';
  return {
    zh,
    issue: zh ? 'GitHub 議題 #87' : 'GitHub issue #87',
    pull: 'Dependabot PR #12',
    alert: zh ? '安全性警示：lodash' : 'Security alert: lodash',
    bare: zh ? '安全性警示 #5' : 'Security alert #5',
    severity: zh ? '高' : 'High',
    packageLabel: zh ? '套件' : 'Package',
    ecosystemLabel: zh ? '生態系' : 'Ecosystem',
    manifestLabel: zh ? '清單路徑' : 'Manifests',
    labelLabel: zh ? '標籤' : 'Labels',
    details: zh ? '詳細資料' : 'Details',
    titleMissing: zh ? '標題：尚無紀錄' : 'Title: Not recorded',
    neutral: zh ? '紀錄沒有類型，先列為問題' : 'Listed as an issue: the record has no type',
    oldAlert: zh ? '發現' : 'Finding',
    unavailable: zh ? '發現 · 無法取得' : 'Finding · Unavailable',
    oldRegister: zh ? '發現 · 上線' : 'Finding · Launch',
    oldChip: zh ? '發現 · #14' : 'Finding · #14',
    ghsa: 'GHSA-35jh-r3h4-6jhm',
  };
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

async function boot(browser: import('@playwright/test').Browser, baseURL: string | undefined, locale: 'en' | 'zh-TW', width: number, dispatch = demoFixture({locale}).dispatch) {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
  const page = await context.newPage();
  await mountConversation(page, false, dispatch);
  await page.goto(demoRoute());
  await expect(page.locator('[data-pm-overview]')).toBeVisible();
  return {page, context};
}

for (const [locale, width] of [['en', 1440], ['zh-TW', 1440], ['zh-TW', 390]] as const) {
  test(`finding names in the register, overview and a chip (${locale} ${width})`, async ({browser, baseURL}) => {
    test.setTimeout(90_000);
    const copy = copyFor(locale);
    const fixture = demoFixture({locale});
    const ids = registerIds(demoProject(locale));
    const {page, context} = await boot(browser, baseURL, locale, width, fixture.dispatch);
    mkdirSync(shots, {recursive: true});
    await openView(page, 'issues');
    const register = page.locator('[data-pm-register]');
    await expect(register.locator(`[data-row-key="${ids.finding}"]`)).toContainText(copy.issue);
    await page.locator('[data-reg-more]').click();
    await expect(register.locator(`[data-row-key="${ids.pull}"]`)).toContainText(copy.pull);
    await expect(register.locator(`[data-row-key="${ids.alert}"]`)).toContainText(copy.alert);
    await expect(register.locator(`[data-row-key="${ids.bare}"]`)).toContainText(copy.bare);
    await expect(register).not.toContainText('undefined');
    await register.screenshot({path: `${shots}/register-list-${locale}-${width}.png`});

    await register.locator(`[data-row-key="${ids.finding}"]`).click();
    const inspector = page.locator('[data-reg-inspector]');
    await expect(inspector.locator('h2')).toHaveText(copy.issue);
    await expect(inspector.locator('[data-finding-labels] .reg-chip')).toHaveText('dependency');
    await page.keyboard.press('Escape');

    await register.locator(`[data-row-key="${ids.pull}"]`).click();
    await expect(inspector.locator('h2')).toHaveText(copy.pull);
    await expect(inspector.locator('[data-finding-manifests]')).toContainText('package.json');
    await page.keyboard.press('Escape');

    await register.locator(`[data-row-key="${ids.alert}"]`).click();
    await expect(inspector.locator('h2')).toHaveText(copy.alert);
    const severity = inspector.locator('[data-finding-severity=high]');
    await expect(severity).toHaveText(copy.severity);
    await expect(severity).toHaveClass(/tone-bad/);
    await expect(inspector.locator('dt', {hasText: copy.packageLabel})).toBeVisible();
    await expect(inspector).toContainText('lodash');
    await expect(inspector.locator('dt', {hasText: copy.ecosystemLabel})).toBeVisible();
    await expect(inspector).toContainText('npm');
    await inspector.locator('summary', {hasText: copy.details}).click();
    await expect(inspector.locator('[data-finding-ghsa] code')).toHaveText(copy.ghsa);
    await expect(inspector).toContainText(copy.neutral);
    await expect(inspector).not.toContainText(copy.titleMissing);
    await expect(inspector).not.toContainText('finding.package');
    await expect(inspector).not.toContainText('dependabot_alerts');
    const marker = inspector.locator('summary .ui-icon');
    const box = await marker.boundingBox();
    expect(box).toBeTruthy();
    expect(box!.width).toBeGreaterThanOrEqual(12);
    expect(box!.height).toBeGreaterThanOrEqual(12);
    expect(Math.abs(box!.width - box!.height)).toBeLessThan(0.5);
    await inspector.screenshot({path: `${shots}/register-alert-${locale}-${width}.png`});
    await page.keyboard.press('Escape');

    await register.locator(`[data-row-key="${ids.bare}"]`).click();
    await expect(inspector.locator('h2')).toHaveText(copy.bare);
    await expect(inspector.locator('dt', {hasText: copy.packageLabel})).toHaveCount(0);
    await expect(inspector.locator('[data-finding-ghsa]')).toHaveCount(0);
    await expect(inspector).not.toContainText('lodash');
    await expect(inspector).not.toContainText('finding.package');
    await page.keyboard.press('Escape');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

    await openView(page, 'overview');
    const attention = page.locator('[data-overview-row]').filter({hasText: copy.alert});
    await expect(attention.locator('strong')).toHaveText(copy.alert);
    await expect(attention.locator('[data-finding-severity=high]')).toHaveText(copy.severity);
    await expect(attention.locator('a')).toBeVisible();
    const href = await attention.locator('a').getAttribute('href');
    expect(new URLSearchParams(href!.split('?')[1]).get('title')).toBe(copy.alert);
    await expect(attention.locator('strong')).not.toHaveText('14');
    await attention.screenshot({path: `${shots}/overview-attention-${locale}-${width}.png`});

    await openView(page, 'discussions');
    const chip = page.locator('[data-discussion-context=finding]');
    await expect(chip).toContainText(copy.alert);
    await expect(chip).not.toContainText('#14');
    await expect(chip).not.toContainText(copy.zh ? '發現 ·' : 'Finding ·');
    const thread = page.locator('.pm-thread-link').filter({has: chip});
    await thread.screenshot({path: `${shots}/discussion-chip-${locale}-${width}.png`});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await context.close();
  });
}

for (const locale of ['en', 'zh-TW'] as const) {
  test(`a withdrawn finding chip shows no identity (${locale})`, async ({browser, baseURL}) => {
    const copy = copyFor(locale);
    const fixture = demoFixture({locale});
    const leaked = {kind: 'finding', position: 0, available: false, id: 'pmfn_' + 'b'.repeat(32), label: '#14', bound_label: 'lodash',
      finding: {source: 'dependabot_alerts', package: 'lodash', number: 14, ghsa_id: copy.ghsa, severity: 'high'}};
    const dispatch = async (input: {method?: string, path: string}) => {
      const result = await fixture.dispatch(input);
      const data = result.data;
      for (const thread of data?.threads || []) if (thread.context?.some((ref: {kind?: string}) => ref.kind === 'finding')) thread.context = [{...leaked}];
      if (data?.thread?.context?.some((ref: {kind?: string}) => ref.kind === 'finding')) data.thread.context = [{...leaked}];
      return result;
    };
    const {page, context} = await boot(browser, baseURL, locale, 1440, dispatch);
    await openView(page, 'discussions');
    const region = page.locator('[data-pm-region="discussions"]');
    const chip = region.locator('[data-discussion-context=finding]');
    await expect(chip).toHaveAttribute('title', copy.unavailable);
    await expect(chip).toContainText(locale === 'en' ? 'Unavailable' : '無法取得');
    await expect(chip).not.toContainText('lodash');
    await expect(chip).not.toContainText(copy.ghsa);
    await expect(chip).not.toContainText('#14');
    await expect(region).not.toContainText('lodash');
    await expect(region).not.toContainText(copy.ghsa);
    await expect(region).not.toContainText('#14');
    const thread = region.locator('.pm-thread-link').filter({hasText: copy.unavailable});
    const title = await thread.locator('strong').innerText();
    await thread.click();
    await expect(page).toHaveURL(new RegExp(`#/pm/${fixture.ids.workspaceId}/discussions/${fixture.ids.threadFollow}$`));
    await expect(region).toHaveCount(0);
    const header = page.locator('main .pd-thread-header');
    await expect(header.locator('h2')).toHaveText(title);
    const openChip = header.locator('[data-discussion-context=finding]');
    await expect(openChip).toHaveAttribute('title', copy.unavailable);
    await expect(openChip).toContainText(locale === 'en' ? 'Unavailable' : '無法取得');
    await expect(openChip).not.toContainText('lodash');
    await expect(openChip).not.toContainText(copy.ghsa);
    await expect(openChip).not.toContainText('#14');
    await expect(page.locator('main')).not.toContainText('lodash');
    await expect(page.locator('main')).not.toContainText(copy.ghsa);
    await expect(page.locator('main')).not.toContainText('#14');
    await context.close();
  });

  test(`an old server without finding keeps the fallback (${locale})`, async ({browser, baseURL}) => {
    const copy = copyFor(locale);
    const fixture = demoFixture({locale});
    const dispatch = async (input: {method?: string, path: string}) => {
      const result = await fixture.dispatch(input);
      const data = result.data;
      for (const row of data?.rows || []) delete row.finding;
      if (data?.row) delete data.row.finding;
      for (const item of data?.attention || []) delete item.finding;
      for (const thread of data?.threads || []) for (const ref of thread.context || []) delete ref.finding;
      for (const ref of data?.thread?.context || []) delete ref.finding;
      return result;
    };
    const {page, context} = await boot(browser, baseURL, locale, 1440, dispatch);
    await openView(page, 'issues');
    const register = page.locator('[data-pm-register]');
    await expect(register).toContainText(copy.oldRegister);
    await expect(register).not.toContainText(copy.issue);
    await expect(register).not.toContainText(locale === 'zh-TW' ? '安全性警示' : 'Security alert');
    const ids = registerIds(demoProject(locale));
    await register.locator(`[data-row-key="${ids.finding}"]`).click();
    const inspector = page.locator('[data-reg-inspector]');
    await expect(inspector.locator('h2')).toHaveText(copy.oldRegister);
    await expect(inspector.locator('[data-finding-severity]')).toHaveCount(0);
    await expect(inspector).not.toContainText(copy.ghsa);
    await page.locator('[data-reg-more]').click();
    await register.locator(`[data-row-key="${ids.alert}"]`).click();
    await expect(inspector.locator('h2')).toHaveText(copy.oldAlert);
    await inspector.locator('summary', {hasText: copy.details}).click();
    await expect(inspector).toContainText(copy.titleMissing);
    await expect(inspector).not.toContainText(copy.alert);
    await openView(page, 'overview');
    const bare = page.locator('[data-overview-row] strong', {hasText: /^14$/});
    await expect(bare).toHaveCount(1);
    await expect(page.locator('[data-pm-region="overview"] [data-finding-severity]')).toHaveCount(0);
    await openView(page, 'discussions');
    const chip = page.locator('[data-pm-region="discussions"] [data-discussion-context=finding]');
    await expect(chip).toContainText(copy.oldChip);
    await expect(chip).not.toContainText('lodash');
    await expect(page.locator('[data-pm-region="discussions"]')).not.toContainText(copy.ghsa);
    await context.close();
  });
}
