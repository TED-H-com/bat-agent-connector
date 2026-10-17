import {test, expect, type Page} from '@playwright/test';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';
import {followUpFixture, openWork, WS, WI, ATTEMPT} from './work-next-turn-fixture.ts';

async function discuss(page: Page) {
  await page.getByRole('button', {name: /^(More actions|更多操作)$/}).click();
  return page.locator('[data-menu-key="discuss"]');
}

for (const native of [false, true]) for (const locale of ['en', 'zh-TW']) {
  const transport = `${native ? 'native IPC' : 'Web HTTP'} ${locale}`;
  test(`${transport}: PM round trip retains the selected Work, Attempt and unsent draft`, async ({page}, testInfo) => {
    await page.setViewportSize({width: locale === 'en' ? 1440 : 390, height: 900});
    const f = followUpFixture(locale, {native});
    await openWork(page, f, locale);
    await page.locator('[data-follow-up-input]').fill('Keep the retry instruction');
    const link = await discuss(page);
    const href = (await link.getAttribute('href'))!;
    const parsed = parseDiscussRefs(href.split('?')[1]);
    expect(parsed.refs[0]).toEqual({kind: 'work_item', id: WI});
    const returnQuery = new URLSearchParams(parsed.from.split('?')[1]);
    expect(Object.fromEntries(returnQuery)).toMatchObject({workspace: WS, work: WI, attempt: ATTEMPT});
    await link.click();
    await expect(page.locator('[data-discussion-back]')).toHaveAttribute('href', parsed.from);
    await expect(page.locator('[data-discussion-back]')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path: testInfo.outputPath('discussion-return.png'), animations: 'disabled'});
    await page.reload();
    f.follow.latest = 'att_' + 'c'.repeat(32);
    await page.locator('[data-discussion-back]').click();
    await expect(page.locator('[data-follow-up-input]')).toHaveValue('Keep the retry instruction');
    expect(f.follow.posts).toEqual([]);
    await page.getByRole('button', {name: /^(Send next|送出下一步)$/}).click();
    await expect.poll(() => f.follow.posts.length).toBe(1);
    expect(f.follow.posts[0].body.target).toEqual({workspace_id: WS, work_item_id: WI});
    expect(f.follow.posts[0].body.preconditions).toEqual({expected_attempt_id: ATTEMPT});
    expect(f.follow.errors).toEqual([]);
  });

  test(`${transport}: returning from PM rechecks a withdrawn Attempt`, async ({page}) => {
    const f = followUpFixture(locale, {native});
    await openWork(page, f, locale);
    await (await discuss(page)).click();
    await expect(page.locator('[data-discussion-back]')).toBeVisible();
    f.follow.attemptStatus = 404;
    await page.locator('[data-discussion-back]').click();
    await expect(page.locator('#main')).toContainText('gone');
    await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
    await expect(page.locator('#workspace-message-input')).toHaveCount(0);
    expect(f.follow.posts).toEqual([]);
  });

  test(`${transport}: an ordinary session never recovers Work authority from a prior route`, async ({page}) => {
    const f = followUpFixture(locale, {native});
    await openWork(page, f, locale);
    await page.evaluate(() => { location.hash = '#/session/demo/search-results?from=discussions'; });
    await expect(page.locator('[data-session-view]')).toBeVisible();
    const href = (await (await discuss(page)).getAttribute('href'))!;
    const parsed = parseDiscussRefs(href.split('?')[1]);
    expect(parsed.from).toBe('#/session/demo/search-results?from=discussions');
    expect(parsed.refs).toEqual([]);
    expect(f.follow.posts).toEqual([]);
  });
}
