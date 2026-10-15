import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';

for (const width of [390,768,1440]) test(`shared search and filter chip sizes survive legacy view styles at ${width}`,async({page})=>{
  await page.setViewportSize({width,height:900});const f=demoFixture(),writes:string[]=[];
  await mountConversation(page,false,async input=>{if(input.method!=='GET')writes.push(input.path);return f.dispatch(input);});
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/dependencies`);
  const search=page.locator('.dep-search input[type=search]'),chip=page.locator('[data-dep-relation]').first();
  await expect(search).toHaveAttribute('placeholder','Search');await expect(chip).toBeVisible();
  expect(await search.evaluate(node=>node.getBoundingClientRect().height)).toBe(32);
  expect(await chip.evaluate(node=>node.getBoundingClientRect().height)).toBe(24);
  if(width<=800)expect(await chip.evaluate(node=>parseFloat(getComputedStyle(node,'::before').minHeight))).toBeGreaterThanOrEqual(44);
  await search.fill('pytest');await expect(page.locator(width<=800?'[data-dep-list]':'[data-dep-matrix]')).toContainText('pytest');
  expect(writes).toEqual([]);
});

for (const locale of ['en', 'zh-TW'] as const) for (const view of ['session', 'overview', 'issues', 'dependencies']) {
  test(`responsive mobile header ${locale} ${view}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width:390, height:844}}), page = await context.newPage();
    const f = demoFixture({locale}); await mountConversation(page, false, f.dispatch);
    const route = view === 'session' ? `session/${f.ids.host}/${f.ids.sessions.implement}` : `pm/${f.ids.workspaceId}/${view}`;
    await page.goto('/dashboard/#/' + route); const title = page.locator('#main .ui-page-header h1'); await expect(title).toBeVisible();
    const text = (locale === 'en' ? 'A long project title that retains readable words and its complete accessible name ' : '這是一個完整保留閱讀順序與無障礙名稱的專案標題，').repeat(8).slice(0,120);
    await title.evaluate((node, value) => {node.textContent = value;}, text);
    await expect(title).toHaveAttribute('title', text); await expect(title).toHaveAccessibleName(text);
    const geometry = await title.evaluate(node => {const box=node.getBoundingClientRect(), header=node.closest('.ui-page-header')!;
      const actions=header.querySelector('.ui-header-actions')!.getBoundingClientRect();
      return {width:box.width, height:box.height,line:parseFloat(getComputedStyle(node).lineHeight),actionsY:actions.y,titleBottom:box.bottom,
        iconsOnly: !header.querySelector('.ui-header-actions > :not(.ui-icon-button, .ui-menu, [hidden])'), actionsRight:actions.right, titleRight:box.right,
        overflow:document.documentElement.scrollWidth-innerWidth, wrap:getComputedStyle(node).overflowWrap};});
    expect(geometry.width).toBeGreaterThan(geometry.iconsOnly ? 150 : 250); expect(geometry.height).toBeLessThanOrEqual(geometry.line*2+1);
    if (geometry.iconsOnly) {
      expect(geometry.actionsY).toBeLessThan(geometry.titleBottom);
      expect(geometry.actionsRight).toBeGreaterThan(geometry.titleRight);
    } else expect(geometry.actionsY).toBeGreaterThanOrEqual(geometry.titleBottom);
    expect(geometry.overflow).toBeLessThanOrEqual(1);
    expect(geometry.wrap).toBe('break-word');
    for (const button of await page.locator('#main .ui-page-header > .ui-header-actions > button:visible, #main .ui-page-header > .ui-header-actions > .ui-menu > button:visible').all()) {
      const box=await button.boundingBox(); expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x+box!.width).toBeLessThanOrEqual(390);
    }
    await context.close();
  });
}

test('table expanders do not add Tab stops and editable Escape preserves an inspector', async ({page}) => {
  await page.goto('/ui-components.html');
  await page.evaluate(async () => {const ui=await import('/src/ui/components.js'), {h}=await import('/src/ui/dom.js');
    const root=document.getElementById('fixture')!;
    root.append(h('input', {'aria-label':'Search requirements'}),ui.dataTable({columns:[{key:'title',label:'Requirement'}],rows:[{id:'one',title:'Email sign-in'}],rowKey:'id',expandable:()=> 'Acceptance criteria'}),
      ui.inspector({title:'Email sign-in'}));});
  const row=page.locator('[data-row-key=one]'); await expect(row.locator('button')).toHaveAttribute('tabindex','-1');
  await expect(page.locator('thead th').first()).toHaveAttribute('aria-hidden','true');
  await row.focus(); await page.keyboard.press('ArrowRight'); await expect(row.locator('button')).toHaveAttribute('aria-expanded','true');
  await page.getByRole('textbox',{name:'Search requirements'}).focus(); await page.keyboard.press('Escape');
  await expect(page.locator('.ui-inspector')).toBeVisible(); await expect(page.getByRole('textbox')).toBeFocused();
  await page.locator('.ui-inspector').focus(); await page.keyboard.press('Escape'); await expect(page.locator('.ui-inspector')).toHaveCount(0);
});

test('sheet uses the shared scrim and returns focus when dismissed', async ({page}) => {
  await page.setViewportSize({width:390,height:844}); await page.goto('/ui-components.html');
  await page.evaluate(async () => {const ui=await import('/src/ui/components.js'),root=document.getElementById('fixture')!;
    root.append(ui.button({label:'Inspect work',onClick:()=>root.append(ui.inspector({title:'Work'}))}));});
  await page.getByRole('button',{name:'Inspect work'}).click(); await expect(page.locator('.ui-inspector-scrim')).toBeVisible();
  await page.locator('.ui-inspector-scrim').dispatchEvent('click'); await expect(page.locator('.ui-inspector')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Inspect work'})).toBeFocused();
});

for (const width of [1000,1200,1280]) test(`requirement titles retain room with and without inspector at ${width}`, async ({page}) => {
  await page.setViewportSize({width,height:900}); const f=demoFixture(); await mountConversation(page,false,f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/requirements`);
  const row=page.locator(`[data-rtm-requirement="${f.ids.requirements.email}"]`); await expect(row).toBeVisible();
  const titleWidth=()=>row.evaluate(node=>{
    const title=[node.querySelector('[data-column=title]'),node.querySelector('.pm-requirement-title')].find(item=>item?.getClientRects().length)!;
    return title.getBoundingClientRect().width;
  });
  expect(await titleWidth()).toBeGreaterThanOrEqual(160);
  await page.evaluate(id=>{location.hash += '?select='+id;}, f.ids.requirements.email);
  await expect(page.locator('[data-work-inspector]')).toBeVisible(); expect(await titleWidth()).toBeGreaterThanOrEqual(160);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBeLessThanOrEqual(1);
});
