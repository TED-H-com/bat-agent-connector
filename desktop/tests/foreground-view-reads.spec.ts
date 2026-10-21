import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';

for (const native of [false, true]) for (const locale of ['en', 'zh-TW']) {
  test(`an initial requirements read survives foreground recovery with no events ${native ? 'IPC' : 'HTTP'} ${locale}`, async ({page}, info) => {
    const f = demoFixture(), reads: string[] = [], posts: string[] = [];
    const source = `/pm/workspaces/${f.ids.workspaceId}/plans/accepted`;
    let entered = false, resumed = false, resumedPolls = 0, release!: () => void;
    const gate = new Promise<void>(resolve => {release = resolve;});
    await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
    await mountConversation(page, native, async input => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (input.method !== 'GET') posts.push(input.path);
      else reads.push(input.path);
      if (path === '/events') {
        if (resumed) resumedPolls++;
        return {status: 200, data: {events: [], next_cursor: 0, head_cursor: 0, has_more: false,
          sync: {checkpoint: {cursor: 0, token: 'empty-foreground'}}}};
      }
      if (path === source) {entered = true; await gate;}
      return f.dispatch(input);
    });
    try {
      await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/overview`);
      await expect(page.locator('[data-pm-overview]')).toBeVisible();
      await page.evaluate(id => {location.hash = `#/pm/${id}/requirements`;}, f.ids.workspaceId);
      await expect.poll(() => entered).toBe(true);
      await page.evaluate(() => {
        Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
        document.dispatchEvent(new Event('visibilitychange'));
      });
      resumed = true;
      await page.evaluate(() => {
        Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
        document.dispatchEvent(new Event('visibilitychange'));
      });
      await expect.poll(() => resumedPolls).toBeGreaterThan(0);
      release();
      await expect(page.locator('[data-rtm-requirement]')).toHaveCount(8);
      await expect(page.locator('[data-api-error]:visible')).toHaveCount(0);
      expect(posts).toEqual([]);
    } finally {
      release();
      await info.attach('requests', {body: JSON.stringify({reads, posts, resumedPolls}), contentType: 'application/json'});
    }
  });
}
