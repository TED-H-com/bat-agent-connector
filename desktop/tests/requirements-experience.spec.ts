import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';

const order = ['requirements', 'status', 'result', 'blocker', 'delivery', 'details'];
async function tableFits(page: any) {
  expect(await page.locator('.pm-requirements-tables, .pm-requirements .ui-data-table').evaluateAll(nodes =>
    nodes.every(node => node.scrollWidth <= node.clientWidth))).toBe(true);
  expect(await page.locator('.pm-requirements .ui-data-table :is(th, td):visible').evaluateAll(nodes =>
    nodes.filter(node => node.scrollWidth > node.clientWidth).map(node => ({column:node.getAttribute('data-column'),text:node.textContent,scroll:node.scrollWidth,width:node.clientWidth})))).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}
for (const locale of ['en', 'zh-TW']) for (const width of [1440, 768, 390]) test(`requirements delivery experience ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, reducedMotion: 'reduce', viewport: {width, height: 900}}), page = await context.newPage();
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {if (message.type() === 'error') errors.push(message.text() + ' ' + message.location().url);});
  const fixture = demoFixture({locale, managedSession: true}), {ids} = fixture;
  await mountConversation(page, false, fixture.dispatch);
  await page.goto(`/dashboard/#/pm/${ids.workspaceId}/requirements`);
  const rtm = page.locator('[data-pm-rtm]');
  await expect(rtm.locator('[data-rtm-requirement]')).toHaveCount(8);
  await expect(page.locator('[data-pm-view="requirements"]')).toHaveAttribute('aria-current', 'page');
  await tableFits(page);
  const emailRow = rtm.locator(`[data-rtm-requirement="${ids.requirements.email}"]`);
  if (width < 800) {
    await expect(rtm.locator('thead')).toBeHidden();
    await expect(emailRow.locator('.pm-requirement-facts')).toContainText(locale === 'en' ? '1 work' : '1 項工作');
    await expect(emailRow.locator('.pm-requirement-facts')).toContainText(locale === 'en' ? '2 PRs' : '2 個 PR');
    await expect(emailRow.locator('.pm-requirement-blocker')).toHaveCount(0);
    await expect(rtm.locator('.pm-requirement-blocker:visible')).toHaveCount(1);
    // The summary is the only row target; its desktop chevron is hidden.
    await expect(emailRow.locator('button:visible, a:visible')).toHaveCount(0);
    await expect(emailRow).toHaveAttribute('tabindex', '0');
  } else {
    await expect(emailRow.locator('[data-column=milestone]')).toBeVisible();
    await expect(emailRow.locator('[data-column=blocker]')).toBeVisible();
    await expect(emailRow.locator('.pm-requirement-milestone').first()).toHaveAttribute('title', /.+/);
  }
  const out = process.env.BATC_WORK_VIEW_SCREENSHOTS || `${process.env.TMPDIR || '/tmp'}/shots`; await mkdir(out, {recursive: true});
  await page.screenshot({path: `${out}/requirements-${locale}-${width}.png`, fullPage: true});
  const search = rtm.locator('input[type=search]'); await search.fill('#41');
  await expect(rtm.locator('[data-rtm-requirement]')).toHaveCount(1);
  const toggle = rtm.locator(width < 800 ? `[data-rtm-requirement="${ids.requirements.email}"]` : `[data-rtm-toggle="${ids.requirements.email}"]`); await toggle.focus(); await page.keyboard.press('Enter');
  await expect(toggle).toBeFocused(); await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const work = rtm.locator(`[data-rtm-work="${ids.work.email}"]`); await expect(work).toContainText('×3');
  await expect(work).toContainText('PR #87'); await expect(work).toContainText('PR #91');
  const select = work.locator('[data-rtm-select]'); await select.focus(); await page.keyboard.press('Enter');
  const inspector = page.locator('[data-work-inspector]'); await expect(inspector.locator('[data-work-attempt]')).toHaveCount(3);
  await expect(inspector.locator('.ui-inspector-body > :not([data-work-section])')).toHaveCount(0);
  expect(await inspector.locator('[data-work-section]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-work-section')))).toEqual(order);
  const link = await inspector.locator('[data-work-discuss]').getAttribute('href');
  expect(parseDiscussRefs(new URLSearchParams(link!.split('?')[1])).refs).toEqual([{kind: 'work_item', id: ids.work.email}]);
  const pr87 = inspector.locator('[data-rtm-pr="87"]'); await expect(pr87.locator('[data-status=merged]')).toHaveCount(1);
  await expect(pr87.locator('[data-status=deployed]')).toHaveCount(0);
  await expect(pr87).toContainText(locale === 'en' ? 'Deploy' : '部署');
  await expect(inspector.locator('[data-rtm-pr="91"] [data-status=deployed]')).toHaveCount(1);
  await expect(inspector.locator('[data-status=accepted]')).toHaveCount(0);
  await expect(inspector.locator('[data-work-section=status]')).toContainText(locale === 'en' ? 'Owner · Not recorded' : '負責人 · 未記錄');
  await expect(inspector.locator('[data-work-section=blocker] p').first()).toHaveText(locale === 'en' ? 'Unknown' : '未知');
  await expect(inspector.locator('[data-work-section=requirements]')).toContainText(locale === 'en' ? 'Milestone' : '里程碑');
  if (width >= 1200) {
    await expect(emailRow.locator('[data-column=milestone]')).toBeHidden();
    await expect(emailRow.locator('[data-column=blocker]')).toBeHidden();
    await expect(rtm.locator('th:visible')).toHaveText(['', locale === 'en' ? 'Requirement' : '需求', locale === 'en' ? 'Work' : '工作', 'PR', locale === 'en' ? 'Acceptance' : '驗收', locale === 'en' ? 'Deploy' : '部署']);
    expect(await emailRow.locator('[data-column=title]').evaluate(node => node.clientWidth)).toBeGreaterThan(180);
    expect(await emailRow.evaluate(node => {
      const edge = node.querySelector('[data-column=deploy]')!.getBoundingClientRect().right;
      return Math.abs(edge - node.getBoundingClientRect().right) < 1;
    })).toBe(true);
  }
  await tableFits(page);
  await page.screenshot({path: `${out}/requirements-inspector-${locale}-${width}.png`, fullPage: false});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await page.locator('body').innerText()).not.toMatch(/\b(?:undefined|null|NaN|wv_\w+)\b/);
  await page.keyboard.press('Escape'); await expect(select).toBeFocused();
  if (width >= 800) {await expect(emailRow.locator('[data-column=milestone]')).toBeVisible(); await expect(emailRow.locator('[data-column=blocker]')).toBeVisible();}
  if (width < 800) {
    await rtm.getByRole('button', {name: locale === 'en' ? 'Open requirement' : '開啟需求', exact: true}).click();
    await expect(inspector.locator('[data-work-section=criteria]')).toContainText(fixture.copy.requirements[0].acceptance);
    await page.keyboard.press('Escape');
    await expect(rtm.getByRole('button', {name: locale === 'en' ? 'Open requirement' : '開啟需求', exact: true})).toBeFocused();
    await toggle.focus(); await page.keyboard.press('ArrowLeft'); await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await page.keyboard.press('ArrowRight'); await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  }
  await search.fill('No matching requirement');
  await expect(rtm.locator('[data-rtm-requirement]')).toHaveCount(0);
  await page.evaluate(hash => {location.hash = hash;}, `#/pm/${ids.workspaceId}/requirements?select=${ids.work.email}`);
  await expect(inspector.locator('[data-work-attempt]')).toHaveCount(3);
  await expect(search).toHaveValue(''); await expect(select).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(rtm.locator('[data-rtm-requirement][tabindex="0"]')).toHaveCount(1);
  if (width < 800) {
    const first = rtm.locator('[data-rtm-requirement]').first(), second = rtm.locator('[data-rtm-requirement]').nth(1);
    await first.focus(); await page.keyboard.press('ArrowDown'); await expect(second).toBeFocused();
    await page.keyboard.press('Enter'); await expect(second).toHaveAttribute('aria-expanded', 'true');
  }
  await page.evaluate(hash => {location.hash = hash;}, `#/pm/${ids.workspaceId}/requirements?select=${ids.requirements.email}`);
  await expect(inspector.locator('[data-work-section=criteria]')).toContainText(fixture.copy.requirements[0].acceptance);
  expect(parseDiscussRefs(new URLSearchParams((await inspector.locator('[data-work-discuss]').getAttribute('href'))!.split('?')[1])).refs).toEqual([
    {kind: 'requirement', id: ids.requirements.email, revision_id: ids.planV3},
  ]);
  await page.keyboard.press('Escape');
  const grouping = rtm.getByRole('combobox'); await grouping.selectOption('milestone');
  await expect(rtm.locator('.pm-requirement-group')).toHaveCount(3);
  await grouping.selectOption('phase'); await expect(rtm.locator('.pm-requirement-group')).toHaveText(locale === 'en' ? 'Phase not recorded' : '尚無階段紀錄');
  await grouping.selectOption('none');
  if (width === 1440) {await page.emulateMedia({colorScheme: 'dark'}); await page.screenshot({path: `${out}/requirements-dark-${locale}-1440.png`, fullPage: true});}
  expect(errors).toEqual([]); await context.close();
});

test('source withdrawal clears requirement facts, counts and the open inspector; late replies stay withdrawn', async ({page}) => {
  const fixture = demoFixture(); let withdrawn = false, hold = false, release = () => {}, held = false;
  await mountConversation(page, false, async (input: any) => {
    if (withdrawn && input.path.includes('/plans/accepted')) return {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Withdrawn'}}};
    const result = await fixture.dispatch(input);
    if (hold && input.path.includes('/kanban/')) {hold = false; held = true; await new Promise<void>(resolve => {release = resolve;});}
    return result;
  });
  await page.goto(`/dashboard/#/pm/${fixture.ids.workspaceId}/requirements`);
  await page.locator(`[data-rtm-toggle="${fixture.ids.requirements.email}"]`).click();
  hold = true; await page.locator(`[data-rtm-select="${fixture.ids.work.email}"]`).click(); await expect.poll(() => held).toBe(true);
  withdrawn = true; await page.locator('[data-pm-refresh]').click();
  await expect(page.locator('[data-rtm-requirement]')).toHaveCount(0);
  release(); await expect(page.locator('[data-work-inspector]')).toHaveCount(0);
  await expect(page.locator('[data-pm-work-meta]')).toHaveText('');
  await expect(page.locator('[data-pm-rtm]')).not.toContainText('PR #87');
});
