import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {workspaceFixture, mountWorkspace, workspaceRoute} from './workspace-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {workbenchFixture, workspaceId as fixtureWorkspaceId} from './pm-workbench-fixture.ts';

for (const lang of ['en', 'zh-TW']) for (const width of [1920, 1440, 1280, 1024, 768, 390, 320]) {
  test(`workbench continuous reading ${lang} ${width}`, async ({page}) => {
    const f = workspaceFixture(lang);
    await mountWorkspace(page, false, f);
    await page.addInitScript(lang => Object.defineProperty(navigator, 'language', {value: lang}), lang);
    await page.setViewportSize({width, height: 900});
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    const shots = process.env.BATC_WORKBENCH_SHOTS;
    if (shots) {
      mkdirSync(shots, {recursive: true});
      await page.goto('/dashboard/' + workspaceRoute);
      await expect(page.locator('.conversation .msg')).toHaveCount(2);
      await page.screenshot({path: `${shots}/before-${lang}-${width}.png`, animations: 'disabled'});
    }
    await page.goto('/dashboard/?workbench=1' + workspaceRoute);
    await expect(page.locator('.conversation .msg')).toHaveCount(2);
    const heading = page.locator('#main h1');
    await expect(heading).toHaveText(f.data.session.title);
    await expect(page.locator('.wc-daily > a')).toHaveCount(4);
    const draft = page.locator('#workspace-message-input textarea');
    await draft.fill('下一輪補上失敗重試\nKeep this draft.');
    const tabs = page.locator('[data-workbench-views]');
    for (const name of lang === 'en' ? ['Lineage', 'Live', 'Work'] : ['關聯', '現場', '工作']) {
      await tabs.getByRole('tab', {name, exact: true}).click();
      await expect(tabs.getByRole('tab', {name, exact: true})).toBeFocused();
      await expect(heading).toHaveText(f.data.session.title);
      await expect(heading).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      await expect(page.locator('#main')).not.toContainText(/undefined|NaN|\bnull\b/);
      if (shots) await page.screenshot({path: `${shots}/after-${name}-${lang}-${width}.png`, animations: 'disabled'});
    }
    await expect(draft).toHaveValue('下一輪補上失敗重試\nKeep this draft.');
    await page.reload();
    await expect(draft).toHaveValue('下一輪補上失敗重試\nKeep this draft.');
    expect(f.data.posts).toEqual([]);
    expect(errors).toEqual([]);
  });
}

for (const native of [false, true]) test(`source withdrawal clears every perspective ${native ? 'IPC' : 'HTTP'}`, async ({page}) => {
  const f = workspaceFixture();
  let denied = false, relatedTitle = 'Another work';
  const dispatch = f.dispatch;
  f.dispatch = async input => {
    if (input.path === '/sessions/demo/search-results' && denied) return {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Removed'}}};
    const result = await dispatch(input);
    if (input.path === '/sessions/demo/search-results' && result.status === 200)
      Object.assign(result.data, {work_items: [{work_item_id: 'wi_' + '1'.repeat(20), title: relatedTitle}]});
    return result;
  };
  await mountWorkspace(page, native, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  await expect(page.locator('.conversation .msg')).toHaveCount(2);
  const tabs = page.locator('[data-workbench-views]');
  await tabs.getByRole('tab', {name: 'Lineage'}).click();
  await page.keyboard.press('ArrowRight');
  await expect(tabs.getByRole('tab', {name: 'Live'})).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await expect(tabs.getByRole('tab', {name: 'Lineage'})).toBeFocused();
  const edge = page.locator('[data-edge]');
  await edge.click();
  await expect(page.locator('[data-workbench-inspect]')).toContainText('Another work');
  await expect(page.locator('#main h1')).toHaveText(f.data.session.title);
  const open = page.locator('[data-workbench-inspect]').getByRole('link', {name: 'Open record'});
  await open.focus();
  relatedTitle = 'Updated related work';
  f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect(page.locator('[data-workbench-inspect]')).toContainText(relatedTitle);
  await expect(open).toBeFocused();
  await page.keyboard.press('Escape'); await expect(edge).toBeFocused();
  if (process.env.BATC_WORKBENCH_SHOTS && !native) await page.screenshot({path: `${process.env.BATC_WORKBENCH_SHOTS}/after-Lineage-related-en.png`, animations: 'disabled'});
  await tabs.getByRole('tab', {name: 'Live'}).click();
  const returnWork = page.locator('[data-workbench-live]').getByRole('button', {name: 'Work', exact: true});
  await returnWork.focus();
  f.data.session.observed_at = '2026-10-10T12:04:00Z';
  f.data.events.push({seq: 2, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect(page.locator('[data-workbench-live] time')).toHaveAttribute('datetime', new Date(f.data.session.observed_at).toISOString());
  await expect(returnWork).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(tabs.getByRole('tab', {name: 'Work', exact: true})).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await expect(tabs.getByRole('tab', {name: 'Live'})).toBeFocused();
  denied = true;
  f.data.events.push({seq: 3, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect(page.locator('[data-session-withdrawn]')).toBeVisible();
  await expect(page.locator('[data-workbench-live]')).not.toContainText(f.data.session.title);
  await expect(page.locator('[data-workbench-lineage]')).not.toContainText('Another work');
  await expect(page.locator('[data-workbench-views]')).toBeHidden();
  await expect(page.locator('[data-workbench-inspect]')).toHaveCount(0);
  expect(f.data.posts).toEqual([]);
});

test('unknown activity, stale observations and manual sessions remain honest and read only', async ({page}) => {
  const f = workspaceFixture(); f.data.session.api_access = 'read_only'; f.data.session.provenance = 'manual';
  await mountWorkspace(page, false, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  await expect(page.locator('[data-session-readonly]')).toBeVisible();
  await expect(page.locator('#workspace-message-input')).toBeHidden();
  await page.locator('[data-workbench-views]').getByRole('tab', {name: 'Live'}).click();
  Object.assign(f.data.session, {stale: true});
  f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect(page.locator('[data-workbench-summary]')).toContainText('out of date');
  await expect(page.locator('.wc-live-lane .wc-live-card')).toHaveCount(0);
  expect(f.data.posts).toEqual([]);
});

for (const lang of ['en', 'zh-TW']) for (const native of [false, true]) for (const draft of [false, true]) test(`GitHub source closure remains separate from Work runtime ${lang} ${native} draft=${draft}`, async ({page}) => {
  const f = workspaceFixture(lang); f.data.linkedItem = true;
  const item = f.fixture.data.items[0];
  Object.assign(item, {state: 'doing', display_state: 'github_closed', source_lane: 'github_closed',
    completion: {...item.completion, display_state: 'doing', pending: false, approved: false},
    external: {source: 'github', repository: 'example/board', repository_id: 1, number: 1, node_id: 'PR_1',
      url: 'https://github.com/example/board/pull/1', kind: 'pull_request', state: 'closed',
      state_reason: null, draft, availability: 'current', board_lane: 'github_closed'}});
  await page.addInitScript(lang => Object.defineProperty(navigator, 'language', {value: lang}), lang);
  await mountWorkspace(page, native, f);
  await page.goto('/dashboard/?workbench=1#/item/' + item.work_item_id);
  const source = page.locator('[data-workbench-github]');
  await expect(source).toContainText(lang === 'en' ? 'Closed (not completed)' : '已關閉（未完成）');
  if (draft) await expect(source).toContainText(lang === 'en' ? 'Draft' : '草稿');
  await expect(source.getByRole('link')).toHaveAttribute('href', item.external.url);
  await expect(page.locator('[data-workbench-summary]')).toContainText(lang === 'en' ? 'Output is arriving' : '正在收到輸出');
  expect(f.data.posts).toEqual([]);
});

test('work identity survives linked session names and the result panel opens on demand', async ({page}) => {
  const f = workspaceFixture(); f.data.linkedItem = true;
  const item = f.fixture.data.items[0];
  item.title = 'Fix empty search'; Object.assign(item, {goal: 'Keep the original query.'});
  await mountWorkspace(page, false, f);
  await page.goto('/dashboard/?workbench=1#/item/' + item.work_item_id);
  await expect(page.locator('.workspace-session-heading h1')).toHaveText(item.title);
  await expect(page.locator('.wc-goal')).toHaveText('Keep the original query.');
  await expect(page.locator('.wc-inspector-host > .ui-inspector')).toBeHidden();
  await page.locator('[data-workbench-views]').getByRole('tab', {name: 'Lineage'}).click();
  await page.locator('.workspace-session-heading').getByRole('button', {name: 'Results', exact: true}).click();
  await expect(page.locator('.wc-inspector-host > .ui-inspector')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.wc-inspector-host > .ui-inspector')).toBeHidden();
  // A work link can disappear while its former session is still accessible.
  f.data.linkedItem = false;
  f.data.events.push({seq: 1, resource_type: 'work_item', resource_id: item.work_item_id, kind: 'work_item.updated'});
  await expect(page.locator('[data-session-withdrawn]')).toBeVisible();
  await expect(page.locator('#workspace-message-input')).toBeHidden();
  await expect(page.locator('.workspace-session-heading')).not.toContainText(item.title);
  expect(f.data.posts).toEqual([]);
});

for (const native of [false, true]) test(`existing next-turn submission stays on the selected work ${native ? 'IPC' : 'HTTP'}`, async ({page}) => {
  const f = workspaceFixture(); await mountWorkspace(page, native, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  const draft = page.locator('#workspace-message-input textarea');
  await draft.fill('Add a retry case after the current turn.');
  await expect(page.locator('.composer-queue input')).toBeChecked();
  await page.getByRole('button', {name: 'Send', exact: true}).click();
  await expect.poll(() => f.data.posts.length).toBe(1);
  expect(f.data.posts[0].body).toMatchObject({action: 'session.send', target: {host: 'demo', session_id: 'search-results'},
    params: {text: 'Add a retry case after the current turn.', queue: true}});
  expect(f.data.posts[0].idempotency_key).toBeTruthy();
  await expect(draft).toHaveValue('');
  await expect(page.locator('.session-send-line')).toBeVisible();
  await draft.fill('A later unsent draft');
  const views = page.locator('[data-workbench-views]');
  await views.getByRole('tab', {name: 'Live'}).click();
  await views.getByRole('tab', {name: 'Work', exact: true}).click();
  await expect(draft).toHaveValue('A later unsent draft');
  await expect(page.locator('.session-send-line')).toBeVisible();
  expect(f.data.posts).toHaveLength(1);
});

test('background output in another perspective preserves the Work reading position', async ({page}) => {
  const f = workspaceFixture();
  f.data.messages = Array.from({length: 30}, (_, n) => ({id: 'message-' + n, role: 'assistant', ts: '2026-10-10T12:02:00Z', text: `Update ${n}\n` + 'Readable history. '.repeat(30)}));
  await mountWorkspace(page, false, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  const scroll = page.locator('.conversation-scroll');
  await expect(page.locator('.conversation .msg')).toHaveCount(30);
  await scroll.evaluate(node => {node.scrollTop = 250; node.dispatchEvent(new Event('scroll'));});
  const before = await scroll.evaluate(node => node.scrollTop);
  await page.locator('[data-workbench-views]').getByRole('tab', {name: 'Lineage'}).click();
  f.data.messages.push({id: 'new', role: 'assistant', ts: '2026-10-10T12:03:00Z', text: 'New output while reading Lineage'});
  f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect.poll(() => f.data.after).toBe(1);
  await page.locator('[data-workbench-views]').getByRole('tab', {name: 'Work', exact: true}).click();
  await expect(page.locator('.conversation .msg')).toHaveCount(31);
  await expect.poll(() => scroll.evaluate(node => node.scrollTop)).toBe(before);
  expect(f.data.posts).toEqual([]);
});

for (const native of [false, true]) for (const locale of ['en', 'zh-TW']) test(`hidden conversation refresh preserves the observed reader position ${native ? 'IPC' : 'Web'} ${locale}`, async ({page}, info) => {
  const f = workspaceFixture(locale);
  f.data.messages = Array.from({length: 30}, (_, n) => ({id: 'message-' + n, role: 'assistant', ts: '2026-10-10T12:02:00Z', text: `Update ${n}\n` + 'Readable history. '.repeat(30)}));
  let waiting = false, release!: () => void;
  const gate = new Promise<void>(resolve => release = resolve), dispatch = f.dispatch;
  f.dispatch = async input => {
    if (f.data.events.length && input.path.includes('/messages')) {waiting = true; await gate;}
    return dispatch(input);
  };
  await page.addInitScript(language => Object.defineProperty(navigator, 'language', {value: language}), locale);
  await mountWorkspace(page, native, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  const scroll = page.locator('.conversation-scroll'), views = page.locator('[data-workbench-views]');
  await expect(page.locator('.conversation .msg')).toHaveCount(30);
  await scroll.scrollIntoViewIfNeeded();
  await scroll.evaluate(node => {node.scrollTop = 250; node.dispatchEvent(new Event('scroll'));});
  // Prove the reader has already observed the move before hiding its geometry.
  await expect(page.getByRole('button', {name: locale === 'zh-TW' ? '回到最新訊息' : 'Back to latest', exact: true})).toBeVisible();
  await views.getByRole('tab', {name: locale === 'zh-TW' ? '關聯' : 'Lineage', exact: true}).click();
  await expect(scroll).toBeHidden();
  expect(await scroll.evaluate(node => node.clientHeight)).toBe(0);
  f.data.messages.push({id: 'new', role: 'assistant', ts: '2026-10-10T12:03:00Z', text: 'New output while reading Lineage'});
  f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect.poll(() => waiting).toBe(true);
  release();
  await expect.poll(() => f.data.after).toBe(1);
  await expect(page.locator('.conversation .msg')).toHaveCount(31);
  await views.getByRole('tab', {name: locale === 'zh-TW' ? '工作' : 'Work', exact: true}).click();
  // The show transition must pass layout/ResizeObserver delivery before asserting.
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  const rendered = await scroll.evaluate(node => ({scrollTop: node.scrollTop, bottom: node.scrollHeight - node.clientHeight}));
  await info.attach('shown-after-hidden-refresh', {body: JSON.stringify(rendered), contentType: 'application/json'});
  expect(rendered.scrollTop).toBe(250);
  expect(f.data.posts).toEqual([]);
});

test('a failed source read removes live claims and recovery restores the same draft', async ({page}) => {
  const f = workspaceFixture(); const dispatch = f.dispatch;
  let failed = false;
  f.dispatch = async input => input.path === '/sessions/demo/search-results' && failed
    ? {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Read failed'}}} : dispatch(input);
  await mountWorkspace(page, false, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  await page.locator('#workspace-message-input textarea').fill('Keep this request');
  await page.locator('[data-workbench-views]').getByRole('tab', {name: 'Live'}).click();
  failed = true;
  f.data.events.push({seq: 1, resource_type: 'session', resource_id: 'demo/search-results', kind: 'session.updated'});
  await expect(page.locator('[data-workbench-live]')).toContainText('could not be refreshed');
  await expect(page.locator('.wc-live-card')).toHaveCount(0);
  failed = false;
  await expect(page.locator('[data-workbench-summary]')).toContainText('Output is arriving', {timeout: 10000});
  await page.locator('[data-workbench-views]').getByRole('tab', {name: 'Work', exact: true}).click();
  await expect(page.locator('#workspace-message-input textarea')).toHaveValue('Keep this request');
  expect(f.data.posts).toEqual([]);
});

test('preview rollback keeps the route and existing draft', async ({page}) => {
  const f = workspaceFixture(); await mountWorkspace(page, false, f);
  await page.goto('/dashboard/?workbench=1' + workspaceRoute);
  await page.locator('#workspace-message-input textarea').fill('Keep through rollout');
  await page.locator('[data-shell-tools]').click();
  await page.getByRole('menuitem', {name: 'Classic layout'}).click();
  await expect(page).toHaveURL(new RegExp('workbench=0' + workspaceRoute));
  await expect(page.locator('[data-workbench-views]')).toHaveCount(0);
  await expect(page.locator('#workspace-message-input textarea')).toHaveValue('Keep through rollout');
  expect(f.data.posts).toEqual([]);
});

const previewLayoutRoutes = [
  {id: 'today', url: '/dashboard/?workbench=1#/home', mount: async (page: any, lang: string) => mountWorkspace(page, false, workspaceFixture(lang))},
  {id: 'overview', url: `/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/overview`, mount: async (page: any) => mountConversation(page, false, workbenchFixture().dispatch)},
  {id: 'discussions', url: `/dashboard/?workbench=1#/pm/${fixtureWorkspaceId}/discussions`, mount: async (page: any) => mountConversation(page, false, workbenchFixture().dispatch)},
  {id: 'work', url: '/dashboard/?workbench=1' + workspaceRoute, mount: async (page: any, lang: string) => mountWorkspace(page, false, workspaceFixture(lang))}
];

for (const route of previewLayoutRoutes) {
  for (const lang of ['en', 'zh-TW']) {
    for (const width of [1440, 1280, 1024, 768, 390, 320]) {
      test(`workbench preview layout ${route.id} ${lang} ${width}`, async ({page}) => {
        await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), lang);
        await page.setViewportSize({width, height: width < 600 ? 844 : 900});
        await route.mount(page, lang);
        await page.goto(route.url);
        await expect(page.locator('#main h1, .ui-page-header h1')).toBeVisible();

        // 1. No page-wide horizontal scroll: document.scrollingElement.scrollWidth <= innerWidth
        const scrollWidth = await page.evaluate(() => document.scrollingElement?.scrollWidth ?? document.documentElement.scrollWidth);
        expect(scrollWidth).toBeLessThanOrEqual(width);

        // 2. The page h1 is uncovered: document.elementFromPoint(rect.left + 2, rect.top + rect.height / 2) is the h1 or inside it.
        // When the rail is visible, rect.left is at least the rail's right edge.
        const h1 = page.locator('#main h1, .ui-page-header h1').first();
        const rect = (await h1.boundingBox())!;
        expect(rect).toBeTruthy();
        const uncovered = await page.evaluate(({x, y}) => {
          const topEl = document.elementFromPoint(x, y);
          const heading = document.querySelector('#main h1, .ui-page-header h1');
          return Boolean(heading && (topEl === heading || heading.contains(topEl)));
        }, {x: rect.x + 2, y: rect.y + rect.height / 2});
        expect(uncovered).toBe(true);

        const railCheck = await page.evaluate(() => {
          const ws = document.querySelector('#workspace');
          const rail = document.querySelector('#workspace-sidebar, .workspace-nav');
          const band = ws ? parseFloat(window.getComputedStyle(ws, '::before').width || '0') : 0;
          const railVisible = window.innerWidth >= 1000 && rail && window.getComputedStyle(rail).display !== 'none';
          const railRight = railVisible ? Math.max(band, rail.getBoundingClientRect().right) : 0;
          return {railVisible, railRight};
        });
        if (railCheck.railVisible) {
          expect(rect.x).toBeGreaterThanOrEqual(railCheck.railRight);
        }

        // 3. Header actions on-screen: every visible header action satisfies left >= 0 and right <= innerWidth
        const actionsOnScreen = await page.evaluate(() => {
          const actions = Array.from(document.querySelectorAll('.ui-header-actions > :not([hidden])'))
            .filter(el => window.getComputedStyle(el).display !== 'none');
          return actions.every(el => {
            const r = el.getBoundingClientRect();
            return r.left >= 0 && r.right <= window.innerWidth;
          });
        });
        expect(actionsOnScreen).toBe(true);

        // 4. One-line heading: in zh-TW at 390 and 320, the Overview h1 is one line. Its height must be less than 1.5x its computed line-height.
        if (route.id === 'overview' && lang === 'zh-TW' && (width === 390 || width === 320)) {
          const lineHeight = await h1.evaluate(node => parseFloat(window.getComputedStyle(node).lineHeight));
          expect(rect.height).toBeLessThan(1.5 * lineHeight);
        }
      });
    }
  }
}

for (const lang of ['en', 'zh-TW']) {
  for (const width of [390, 320]) {
    test(`workbench preview work header more button stays on title row ${lang} ${width}`, async ({page}) => {
      await page.addInitScript(l => Object.defineProperty(navigator, 'language', {value: l}), lang);
      await page.setViewportSize({width, height: 844});
      await mountWorkspace(page, false, workspaceFixture(lang));
      await page.goto('/dashboard/?workbench=1' + workspaceRoute);
      await expect(page.locator('.ui-page-header h1')).toBeVisible();

      // No page-wide horizontal scroll
      const scrollWidth = await page.evaluate(() => document.scrollingElement?.scrollWidth ?? document.documentElement.scrollWidth);
      expect(scrollWidth).toBeLessThanOrEqual(width);

      // The more ("⋯") button's vertical centre lies within the h1 row's vertical span
      const h1 = page.locator('.ui-page-header h1');
      const more = page.locator('.ui-page-header .ui-header-actions .ui-icon-button');
      await expect(more).toBeVisible();

      const h1Box = (await h1.boundingBox())!;
      const moreBox = (await more.boundingBox())!;
      const moreCenterY = moreBox.y + moreBox.height / 2;

      expect(moreCenterY).toBeGreaterThanOrEqual(h1Box.y);
      expect(moreCenterY).toBeLessThanOrEqual(h1Box.y + h1Box.height);
    });
  }
}

