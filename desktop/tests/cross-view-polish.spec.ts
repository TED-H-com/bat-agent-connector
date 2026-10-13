import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';

for (const view of ['agents','issues','requirements','operations']) for (const width of [1440,1000,768,390]) test(`${view} table and inspector fit ${width}`, async ({page}) => {
  await page.setViewportSize({width,height:900});
  const f = demoFixture({discussions:true}); await mountConversation(page,false,f.dispatch);
  const route = view === 'operations' ? '#/operations' : `#/pm/${f.ids.workspaceId}/${view}`;
  await page.goto('/dashboard/' + route);
  const root = page.locator(view === 'operations' ? '[data-operation-list]' : view === 'requirements' ? '[data-pm-rtm]' : view === 'agents' ? '[data-pm-resources]' : '[data-pm-register]');
  const row = root.locator(width < 800 && ['agents','issues'].includes(view) ? view === 'agents' ? '[data-resource-row]' : '[data-row-key]' : '[data-row-key]').first();
  await expect(row).toBeVisible();
  if (width >= 800) {
    for (const th of await root.locator('.ui-data-table th[data-column]').all()) await expect(th).toHaveAttribute('title', /.+/);
  } else if (view === 'operations') {
    await expect(row.locator('.ui-data-table-compact')).toBeVisible();
    expect(await row.locator('button,a,input,select').count()).toBe(0);
  }
  await row.click();
  if (view === 'requirements' && width < 800) await root.locator('[data-rtm-select]').filter({visible:true}).first().click();
  const panel = page.locator('.ui-inspector').filter({visible:true}).first(); await expect(panel).toBeVisible();
  if (width === 1440) {
    await expect(root.locator('th[data-secondary]').first()).toBeHidden();
    expect(await root.locator('.ui-data-table').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    if (view === 'issues') {
      expect(await root.locator('[data-reg-search]').evaluate(node => node.getBoundingClientRect().right <= document.querySelector('.reg-main')!.getBoundingClientRect().right + 1)).toBe(true);
    }
  } else if (width < 800) {
    expect(await panel.evaluate(node => getComputedStyle(node).borderLeftWidth)).toBe('0px');
    expect((await panel.boundingBox())!.x).toBe(0);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('rail surface continues through long pages and footer destinations show selection', async ({page}) => {
  await page.setViewportSize({width:1440,height:900});
  const f=demoFixture(); await mountConversation(page,false,f.dispatch); await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/overview`);
  await expect(page.locator('[data-overview-status]')).toBeVisible();
  expect(await page.locator('#workspace').evaluate(node => parseFloat(getComputedStyle(node,'::before').height) >= node.getBoundingClientRect().height - 1)).toBe(true);
  for (const [route, hook] of [['home','pending'],['operations','tools'],['settings','settings']]) {
    await page.goto('/dashboard/#/' + route); await expect(page.locator(`[data-shell-${hook}]`)).toHaveAttribute('aria-current','page');
  }
});
