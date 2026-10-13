import {openPmSection, expectPmWithdrawn} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {pmFixture,workspaceId,threadId} from './pm-fixture.ts';
import {fixture as sourceFixture,workspaceId as sourceWorkspace,sourcePath} from './pm-spec-sources-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';

function fixture(){
  const base=pmFixture(),sources=sourceFixture();
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path.includes('/spec-sources')){
      const result=await sources.dispatch(input);
      if(result.data.workspace_id===sourceWorkspace)result.data.workspace_id=workspaceId;
      return result;
    }
    const result=await base.dispatch(input);
    if(path==='/bootstrap'||path==='/capabilities'){
      const caps=path==='/bootstrap'?result.data.capabilities:result.data;
      caps.features.pm_workspaces.spec_sources=true;
    }
    return result;
  };
  return{base,sources,dispatch};
}
const route=`/dashboard/#/pm/${workspaceId}`,box=(page:any)=>page.locator('[data-pm-spec-sources]');
for(const native of [false,true])test.describe(native?'native workspace specifications':'web workspace specifications',()=>{
  for(const thread of [false,true])test(`fixed sources are available on the shared ${thread?'discussion':'workspace'} page`,async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route+(thread?'/'+threadId:''));if(!thread)await openPmSection(page, 'requirements' , 'ui_specs');
    await expect(box(page)).toContainText('example/membership');expect(f.sources.state.reads).toHaveLength(0);
    await box(page).getByRole('button',{name:'Read fixed sources',exact:true}).click();
    await expect(box(page)).toContainText(sourcePath);await expect(box(page)).toContainText('Scope adoption, implementation verification, merge and deployment remain unknown.');
    await box(page).getByText(sourcePath+' · Main specification',{exact:true}).click();
    await box(page).getByText('Original document text',{exact:true}).first().click();
    await expect(box(page).locator('.pm-plan-document').first()).toBeVisible();
    f.base.state.cursor++;
    await expect.poll(()=>f.sources.state.checks,{timeout:10000}).toBeGreaterThan(1);
    expect(f.sources.state.reads).toHaveLength(1);await expect(box(page).locator('.pm-plan-document').first()).toBeVisible();
    expect(f.sources.state.writes).toHaveLength(0);expect(f.base.state.writes).toHaveLength(0);
  });
  test('parent authorization failure hides sources and an eligible retry does not read Git again',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_specs');
    await box(page).getByRole('button',{name:'Read fixed sources',exact:true}).click();await expect(box(page)).toContainText(sourcePath);
    f.base.state.failRead=true;f.base.state.cursor++;
    await expect(box(page)).toHaveJSProperty('hidden',true,{timeout:10000});await expectPmWithdrawn(page,'requirements','[data-pm-spec-sources]','ui_specs');await expect(page.locator('.shell-workspace-block')).not.toContainText('Library refresh');
    f.base.state.failRead=false;f.base.state.cursor++;
    await expect(box(page)).toBeVisible({timeout:10000});expect(f.sources.state.reads).toHaveLength(1);
    f.sources.state.denied=true;f.base.state.cursor++;
    await expect(box(page)).not.toContainText(sourcePath,{timeout:10000});await expect(box(page)).not.toContainText('example/membership');
    await openPmSection(page,'discussions');await expect(page.getByRole('button',{name:'Start discussion',exact:true})).toBeVisible();
  });
});
for(const locale of ['en-US','zh-TW'])for(const width of [390,768,1440])test(`shared specification layout ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=fixture();
  await mountConversation(page,false,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_specs');
  await box(page).getByRole('button',{name:locale==='zh-TW'?'讀取固定來源':'Read fixed sources',exact:true}).click();
  await expect(box(page)).toContainText(sourcePath);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const output=process.env.PM_SPEC_WORKSPACE_SCREENSHOTS;
  if(output){await mkdir(output,{recursive:true});await page.screenshot({path:`${output}/${locale}-${width}.png`,fullPage:true});}
  await context.close();
});
