import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';

const order = ['requirements', 'status', 'result', 'blocker', 'delivery', 'details'];
for (const locale of ['en', 'zh-TW']) for (const width of [1440, 768, 390]) test(`requirements delivery experience ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: 900}}), page = await context.newPage();
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  const fixture = demoFixture({locale}), {ids} = fixture;
  await mountConversation(page, false, fixture.dispatch);
  await page.goto(`/dashboard/#/pm/${ids.workspaceId}/requirements`);
  const rtm = page.locator('[data-pm-rtm]');
  await expect(rtm.locator('[data-rtm-requirement]')).toHaveCount(8);
  await expect(page.locator('[data-pm-view="requirements"]')).toHaveAttribute('aria-current', 'page');
  const out = `${process.env.TMPDIR || '/tmp'}/shots`; await mkdir(out, {recursive: true});
  await page.screenshot({path: `${out}/requirements-${locale}-${width}.png`, fullPage: true});
  const search = rtm.locator('input[type=search]'); await search.fill('#41');
  await expect(rtm.locator('[data-rtm-requirement]')).toHaveCount(1);
  const toggle = rtm.locator(`[data-rtm-toggle="${ids.requirements.email}"]`); await toggle.focus(); await page.keyboard.press('Enter');
  await expect(toggle).toBeFocused(); await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const work = rtm.locator(`[data-rtm-work="${ids.work.email}"]`); await expect(work).toContainText('×3');
  await expect(work).toContainText('PR #87'); await expect(work).toContainText('PR #91');
  const select = work.locator('[data-rtm-select]'); await select.focus(); await page.keyboard.press('Enter');
  const inspector = page.locator('[data-work-inspector]'); await expect(inspector.locator('[data-work-attempt]')).toHaveCount(3);
  expect(await inspector.locator('[data-work-section]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-work-section')))).toEqual(order);
  const link = await inspector.locator('[data-work-discuss]').getAttribute('href');
  expect(parseDiscussRefs(new URLSearchParams(link!.split('?')[1])).refs).toEqual([{kind: 'work_item', id: ids.work.email}]);
  const pr87 = inspector.locator('[data-rtm-pr="87"]'); await expect(pr87.locator('[data-status=merged]')).toHaveCount(1);
  await expect(pr87.locator('[data-status=deployed]')).toHaveCount(0);
  await expect(inspector.locator('[data-rtm-pr="91"] [data-status=deployed]')).toHaveCount(1);
  await expect(inspector.locator('[data-status=accepted]')).toHaveCount(0);
  await page.screenshot({path: `${out}/requirements-inspector-${locale}-${width}.png`, fullPage: true});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await page.locator('body').innerText()).not.toMatch(/\b(?:undefined|null|NaN|wv_\w+)\b/);
  await page.keyboard.press('Escape'); await expect(select).toBeFocused();
  await search.fill('');
  await page.evaluate(hash => {location.hash = hash;}, `#/pm/${ids.workspaceId}/requirements?select=${ids.work.email}`);
  await expect(inspector.locator('[data-work-attempt]')).toHaveCount(3);
  await page.keyboard.press('Escape');
  await page.evaluate(hash => {location.hash = hash;}, `#/pm/${ids.workspaceId}/requirements?select=${ids.requirements.email}`);
  await expect(inspector.locator('[data-work-section=requirements]')).toContainText(fixture.copy.requirements[0].text);
  expect(parseDiscussRefs(new URLSearchParams((await inspector.locator('[data-work-discuss]').getAttribute('href'))!.split('?')[1])).refs).toEqual([
    {kind: 'requirement', id: ids.requirements.email, revision_id: ids.planV3},
  ]);
  await page.keyboard.press('Escape');
  if (width === 1440) {await page.emulateMedia({colorScheme: 'dark'}); await page.screenshot({path: `${out}/requirements-dark-${locale}-1440.png`, fullPage: true});}
  expect(errors).toEqual([]); await context.close();
});

test('source withdrawal clears requirement facts, counts and the open inspector; late replies stay withdrawn', async ({page}) => {
  const fixture = demoFixture(); let withdrawn = false, hold = false, release = () => {}, held = false;
  await mountConversation(page, false, async (input: any) => {
    if (withdrawn && input.path.includes('/issue-mappings')) return {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Withdrawn'}}};
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
  await expect(page.locator('.ui-page-heading .ui-meta')).toHaveText('');
  await expect(page.locator('[data-pm-rtm]')).not.toContainText('PR #87');
});
