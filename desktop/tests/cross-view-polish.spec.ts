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

for (const locale of ['en','zh-TW']) test(`remaining labels stay localized ${locale}`, async ({browser,baseURL}) => {
  const context=await browser.newContext({baseURL,locale,viewport:{width:1440,height:900}}), page=await context.newPage(), f=demoFixture({locale});
  await page.clock.install({time:new Date('2026-10-13T09:00:00Z')}); await mountConversation(page,false,f.dispatch);
  await page.goto('/dashboard/#/operations'); await page.locator('[data-row-key]').first().click();
  const panel=page.locator('.ui-inspector'); await panel.locator('summary').click();
  await expect(panel).toContainText(locale==='en'?'outcome unknown, reading back':'結果不明，回查中');
  await expect(panel.locator('details')).not.toContainText('· uncertain');
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/agents`); await page.locator('[data-resource-row]').first().click();
  await expect(page.locator('[data-resource-detail]')).toContainText(locale==='en'?'Total':'總計');
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/issues`);
  await page.locator('[data-reg-more]').click();
  // The mapping has no recorded title, so its type and source reference identify it.
  await expect(page.locator('.reg-clamp').filter({hasText:locale==='en'?/^Issue · /:/^問題 · /})).toHaveCount(1);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/schedule`);
  await expect(page.locator('.schedule-meta')).toContainText(locale==='en'?'27 items':'共 27 筆');
  await expect(page.locator('.schedule-meta')).not.toContainText(locale==='en'?'loaded':'已載入');
  expect((await page.locator('[data-schedule-row]').first().boundingBox())!.height).toBe(44);
  const firstTick=page.locator('.schedule-tick time').first();
  expect(await firstTick.evaluate(node => node.getBoundingClientRect().left >= node.closest('.schedule-axis')!.getBoundingClientRect().left)).toBe(true);
  await context.close();
});

for (const status of [403,404]) test(`operation withdrawal removes rows, count and a held inspector (${status})`, async ({page}) => {
  const f=demoFixture(); let withdrawn=false, held=false, release!:() => void;
  const wait=new Promise<void>(resolve => release=resolve);
  await mountConversation(page,false,async (input:any) => {
    if (input.path.startsWith('/operations?') && withdrawn) return {status,data:{error:{code:'FORBIDDEN',message:'Operations withdrawn'}}};
    const result=await f.dispatch(input);
    if (/^\/operations\/op_/.test(input.path)) {held=true; await wait;}
    return result;
  });
  await page.goto('/dashboard/#/operations'); await page.locator('[data-row-key]').first().click();
  await expect.poll(() => held).toBe(true); withdrawn=true;
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(page.locator('[data-operation-list] [data-row-key]')).toHaveCount(0);
  await expect(page.locator('[data-operation-count]')).toBeHidden(); await expect(page.locator('.ui-inspector')).toHaveCount(0);
  release(); await page.waitForTimeout(100);
  await expect(page.locator('.ui-inspector')).toHaveCount(0); await expect(page.locator('[data-operation-count]')).toBeHidden();
});
