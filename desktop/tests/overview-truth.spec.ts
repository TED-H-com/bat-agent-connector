import {test, expect} from '@playwright/test';
import {demoFixture, demoRoute} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';
import {openPmSection} from './pm-navigation.ts';

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 1000, 390]) {
  test(`Overview opens the exact record without inventing loaded counts ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport:{width,height:900}}), page = await context.newPage();
    const f = demoFixture({locale, discussions:true}), errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await mountConversation(page,false,f.dispatch); await page.goto(demoRoute());
    const blocker = page.locator('[data-overview-row]').filter({hasText:f.copy.blocker});
    await expect(blocker).toBeVisible();
    const phase = page.locator('.pm-main > .ui-page-header .ui-title-row > .ui-pill').first();
    await expect(phase).toHaveText(locale === 'en' ? 'Development' : '開發');
    await blocker.locator('[data-overview-open]').click();
    const inspector = page.locator('[data-reg-inspector]');
    await expect(inspector.locator('h2')).toHaveText(f.copy.blocker);
    await expect(inspector.locator('[data-reg-outside-page]')).toBeVisible();
    await expect(page.locator('[data-reg-table] [data-row-key]')).toHaveCount(3);
    await expect(page.locator('[data-reg-count]')).toHaveAttribute('aria-label',locale === 'en' ? '3 shown' : '顯示 3 筆');
    await expect(phase).toHaveText(locale === 'en' ? 'Development' : '開發');
    await expect(inspector.locator('[data-reg-discuss]')).toHaveClass(/primary/);
    await expect(inspector.locator('.reg-actions > [data-reg-propose]')).toHaveCount(0);
    await inspector.locator('[data-reg-overflow]').click();
    await expect(inspector.getByRole('menuitem')).toHaveText(locale === 'en' ? 'Propose change' : '提議變更');
    await page.keyboard.press('Escape');
    await inspector.locator('[data-reg-discuss]').click();
    await expect(page).toHaveURL(/discussions\/new/);
    await expect(page.locator('[data-discussion-title]')).toHaveText(f.copy.blocker);
    await expect(page.locator('.pd-context [data-discussion-context=governance]')).toContainText(f.copy.blocker);
    expect(errors).toEqual([]); expect(f.discussion!.writes).toEqual([]);
    await context.close();
  });
}

for (const locale of ['en', 'zh-TW']) test(`resolved register records have no current response ${locale}`,async ({browser,baseURL}) => {
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage(),f=demoFixture({locale});
  const response='Previous recovery instruction';
  const dispatch=async (input: any) => {
    const result=await f.dispatch(input);
    const records=result.data?.rows || (result.data?.row ? [result.data.row] : []);
    for(const row of records) if(row.source?.kind==='finding') {row.status='reported_resolved';row.next_step=response;}
    return result;
  };
  await mountConversation(page,false,dispatch); await page.goto(demoRoute()); await openPmSection(page,'issues');
  const row=page.locator('[data-reg-table] [data-row-key]').first();
  await expect(row).not.toContainText(response); await row.click();
  const inspector=page.locator('[data-reg-inspector]');
  await expect(inspector.locator('[data-record-status=reported_resolved]').first()).toBeVisible();
  await expect(inspector).not.toContainText(response);
  await page.keyboard.press('Escape');
  for(const key of ['ui_governance','ui_repairs','ui_maintenance_review','ui_follow_up'])
    await expect(page.locator(`[data-pm-section=${key}]`)).toBeHidden();
  await page.locator('[data-pm-overflow]').click(); await page.locator('[data-menu-key=governance]').click();
  await expect(page.locator('[data-pm-section=ui_governance] [data-pm-governance]')).toBeVisible();
  await page.locator('[data-pm-section=ui_governance] > details > summary').click(); await expect(page.locator('[data-pm-section=ui_governance]')).toBeHidden();
  await context.close();
});

for (const locale of ['en', 'zh-TW']) for (const width of [1440,1000,768,390]) test(`register gives the title room ${locale} ${width}`,async ({browser,baseURL}) => {
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=demoFixture({locale});
  await mountConversation(page,false,async (input: any) => {
    const result=await f.dispatch(input);
    for(const row of result.data?.rows || (result.data?.row ? [result.data.row] : [])) if(row.owner) row.owner='walkthrough-owner';
    return result;
  });
  await page.goto(demoRoute()); await openPmSection(page,'issues');
  await expect(page.locator('[data-reg-table] [data-row-key]')).toHaveCount(3);
  if(width===1440) {
    const owner=page.locator('.reg-owner-cell').filter({hasText:'walkthrough-owner'}).first();
    await expect(owner).toHaveAttribute('title','walkthrough-owner');
    expect(await owner.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    const title=page.locator('[data-column=title] .reg-clamp').first();
    expect(await title.evaluate(node=>node.clientHeight<=Number.parseFloat(getComputedStyle(node).lineHeight)+1)).toBe(true);
  }
  const take=async (state: string) => {
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    if(process.env.BATC_LAYOUT_SHOTS) await page.screenshot({path:`${process.env.BATC_LAYOUT_SHOTS}/${locale}-${width}-${state}.png`,fullPage:width>=1200});
  };
  await take('table'); await page.locator('[data-reg-table] [data-row-key]').nth(1).click();
  await expect(page.locator('[data-reg-inspector] h2')).toHaveText(f.copy.planTitle); await take('inspector');
  if(width>=1200) expect(await page.locator('[data-column=title]').first().evaluate(node=>node.getBoundingClientRect().width)).toBeGreaterThan(200);
  await context.close();
});

for(const locale of ['en','zh-TW']) test(`phase stays fresh across workspace views ${locale}`,async ({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage(),f=demoFixture({locale});
  let cursor=0,stage='development',overviewReads=0;
  await mountConversation(page,false,async (input: any)=>{
    const url=new URL(input.path,'http://fixture');
    if(url.pathname==='/events') return {status:200,data:{events:Number(url.searchParams.get('after'))<cursor?[{seq:cursor,resource_type:'pm_lifecycle',resource_id:f.ids.workspaceId,kind:'fixture.updated'}]:[],next_cursor:cursor,head_cursor:cursor,has_more:false,sync:{checkpoint:{cursor,token:`demo-${cursor}`}}}};
    const result=await f.dispatch(input);
    if(url.pathname.endsWith('/overview')) {overviewReads++;result.data.lifecycle.stage=stage;}
    return result;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/issues`);
  await expect(page.locator('[data-reg-table] [data-row-key]').first()).toBeVisible();
  const phase=page.locator('.pm-main > .ui-page-header .ui-title-row > .ui-pill').first();
  await expect(phase).toBeHidden();expect(overviewReads).toBe(0);
  await openPmSection(page,'overview'); await expect(page.locator('[data-overview-row]').first()).toBeVisible();
  for(const view of ['issues','discussions']) {await openPmSection(page,view);await expect(phase).toHaveText(locale==='en'?'Development':'開發');}
  stage='planning';cursor=1;
  await expect(phase).toBeHidden();expect(overviewReads).toBe(1);
  await openPmSection(page,'overview');await expect(phase).toHaveText(locale==='en'?'Planning':'規劃');
  await openPmSection(page,'issues');await expect(phase).toHaveText(locale==='en'?'Planning':'規劃');
  await context.close();
});
