import {test, expect} from '@playwright/test';
import {openSession, go} from './session-fixture';

for (const width of [1440, 768, 390]) for (const readingEarlier of [false, true]) {
  test(`${width}px late session controls preserve ${readingEarlier ? 'the earlier reading position' : 'latest-message pinning'}`, async ({browser}) => {
    const s = await openSession(browser, {width});
    let hold = false, release!: () => void;
    const observation = new Promise<void>(resolve => {release = resolve;});
    s.overrides.push(async (_input, url) => {
      if (hold && url.pathname === `/sessions/${s.ids.host}/${s.ids.sessions.implement}`) await observation;
    });
    await s.page.goto(`/dashboard/#/pm/${s.ids.workspaceId}/requirements`);
    await expect(s.page.locator('[data-pm-view=requirements][aria-current=page]')).toBeAttached();
    hold = true;
    await go(s.page, `#/session/${s.ids.host}/${s.ids.sessions.implement}`);
    const scroller = s.page.locator('.conversation-scroll');
    await expect(scroller.locator('.msg').first()).toBeVisible();
    const distance = () => scroller.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight);
    await expect.poll(distance).toBeLessThanOrEqual(1);
    if (readingEarlier) {
      await scroller.evaluate(el => {el.scrollTop = 0;});
      await expect(s.page.locator('.conversation-jump')).toBeVisible();
    }
    release();
    await expect(s.page.getByRole('button', {name: 'Send', exact: true})).toBeEnabled();
    if (readingEarlier) {
      await expect.poll(() => scroller.evaluate(el => el.scrollTop)).toBe(0);
      await expect(s.page.locator('.conversation-jump')).toBeVisible();
    } else {
      await expect.poll(distance).toBeLessThanOrEqual(1);
      await expect(s.page.locator('.conversation-jump')).toBeHidden();
    }
    expect(s.writes).toEqual([]);
    await s.context.close();
  });
}
