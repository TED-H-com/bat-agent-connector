import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {fixture,mount,commit,sourcePath} from './pm-spec-sources-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture,pmRoute} from './pm-fixture.ts';

const box=(page:any)=>page.locator('[data-pm-spec-sources]'),result=(page:any)=>box(page).locator('[data-pm-spec-result]');
const shots=process.env.PM_SPEC_SCREENSHOTS;
async function selectPaths(page:any, value:string){
  const selection=box(page).locator('[data-pm-spec-selection]');
  if(!await selection.evaluate((node:HTMLDetailsElement)=>node.open))await selection.locator('summary').click();
  await selection.getByLabel('Exact artifact paths (optional)',{exact:true}).fill(value);
}
test('existing PM page before mounting specification sources',async({page})=>{
  const f=pmFixture();await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);
  await expect(page.locator('.shell-workspace-block')).toContainText(f.state.workspaces[0].name);
  await expect(page.getByRole('textbox',{name:'Discussion title',exact:true})).toHaveValue(f.state.thread.title);
  if(shots){mkdirSync(shots,{recursive:true});for(const width of [1440,390]){
    await page.setViewportSize({width,height:900});await page.screenshot({path:`${shots}/before-${width}.png`,fullPage:true});}}
});

for(const native of [false,true])test.describe(native?'native specification reader':'browser specification reader',()=>{
  test('explicit fixed read preserves source truth and never executes markup or writes',async({page})=>{
    const f=fixture();await mount(page,f,native);await expect(box(page)).toContainText(commit);expect(f.state.reads).toHaveLength(0);
    await page.evaluate(()=>(window as any).refreshSpec());expect(f.state.reads).toHaveLength(0);
    await box(page).getByRole('button',{name:'Read fixed sources',exact:true}).click();await expect(result(page)).toContainText('Some documents could not be read');
    await result(page).getByText(sourcePath+' · Main specification',{exact:true}).click();
    await result(page).getByText('Original document text',{exact:true}).first().click();
    await expect(result(page)).toContainText('<script>window.fixtureUnsafe=true</script>');expect(await page.evaluate(()=>(window as any).fixtureUnsafe)).toBeUndefined();
    await result(page).getByText('openspec/changes/archive/2026-01-01-membership/tasks.md · Task document',{exact:true}).click();
    await expect(result(page)).toContainText('Archived source; archive does not establish acceptance or delivery.');
    await result(page).getByText('Task claims in this document',{exact:true}).click();await expect(result(page)).toContainText('Reported checked');
    await expect(result(page)).toContainText('It is not verification, merge, deployment or a completed WorkItem.');
    await result(page).getByText('openspec/changes/recovery/design.md · Design',{exact:true}).click();
    await expect(result(page)).toContainText('This document exceeds the read size limit.');
    await page.evaluate(()=>(window as any).refreshSpec());await expect(result(page).getByText('Original document text',{exact:true}).first()).toBeVisible();
    await expect(result(page).locator('.pm-plan-document').first()).toBeVisible();
    await expect(result(page)).toContainText('<script>window.fixtureUnsafe=true</script>');expect(f.state.reads).toHaveLength(1);
    expect(f.state.writes).toHaveLength(0);await expect(result(page).locator('script,img,input[type=checkbox]')).toHaveCount(0);
    await selectPaths(page,sourcePath);await expect(result(page)).toBeEmpty();
    await box(page).getByRole('button',{name:'Read fixed sources',exact:true}).click();await expect(result(page)).toContainText('Main specification');
    expect(new URL(f.state.reads[1],'http://fixture').searchParams.get('paths')).toBe(sourcePath);
  });
  test('unavailable capability and revoked access clear source facts',async({page})=>{
    const f=fixture();f.state.eligible=false;await mount(page,f,native);await expect(box(page)).toContainText('No prepared PM source repository');
    await expect(box(page).getByRole('button',{name:'Read fixed sources',exact:true})).toBeHidden();expect(f.state.reads).toHaveLength(0);
    f.state.eligible=true;await box(page).getByRole('button',{name:'Check availability',exact:true}).click();
    await box(page).getByRole('button',{name:'Read fixed sources',exact:true}).click();await expect(result(page)).not.toBeEmpty();
    f.state.denied=true;await page.evaluate(()=>(window as any).refreshSpec());await expect(result(page)).toBeEmpty();
    await expect(box(page)).not.toContainText(commit);await expect(box(page)).not.toContainText('example/membership');
    await expect(box(page)).toContainText('Current source access was revoked.');
  });
  test('late source response is discarded after selection or authority changes',async({page})=>{
    const f=fixture();let release!:()=>void;f.state.delay=new Promise<void>(resolve=>release=resolve);await mount(page,f,native);
    await box(page).getByRole('button',{name:'Read fixed sources',exact:true}).click();await expect.poll(()=>f.state.reads.length).toBe(1);
    await selectPaths(page,sourcePath);release();
    await expect(box(page).getByRole('button',{name:'Read fixed sources',exact:true})).toBeEnabled();await expect(result(page)).toBeEmpty();
    f.state.delay=new Promise<void>(resolve=>release=resolve);await box(page).getByRole('button',{name:'Read fixed sources',exact:true}).click();
    await expect.poll(()=>f.state.reads.length).toBe(2);f.state.denied=true;await page.evaluate(()=>(window as any).refreshSpec());release();
    await expect(result(page)).toBeEmpty();await expect(box(page)).not.toContainText(commit);
  });
  test('mismatched fixed response is rejected and duplicate selection never reaches Git',async({page})=>{
    const f=fixture();await mount(page,f,native);await selectPaths(page,sourcePath+'\n'+sourcePath);
    await box(page).getByRole('button',{name:'Read fixed sources',exact:true}).click();await expect(box(page)).toContainText('100 distinct');expect(f.state.reads).toHaveLength(0);
    await selectPaths(page,Array.from({length:60},(_,i)=>`openspec/specs/c${i}${'member'.repeat(10)}/spec.md`).join('\n'));
    await box(page).getByRole('button',{name:'Read fixed sources',exact:true}).click();await expect(box(page)).toContainText('Select fewer or shorter paths');expect(f.state.reads).toHaveLength(0);
    await selectPaths(page,'');f.state.bad=true;await box(page).getByRole('button',{name:'Read fixed sources',exact:true}).click();
    await expect(box(page)).toContainText('The response does not match');await expect(result(page)).toBeEmpty();
  });
});

test('readable in both locales and all shared widths',async({browser,baseURL})=>{
  if(shots)mkdirSync(shots,{recursive:true});
  for(const locale of ['en-US','zh-TW']){
    const context=await browser.newContext({baseURL,locale}),page=await context.newPage(),f=fixture();await mount(page,f);
    await box(page).getByRole('button',{name:locale==='zh-TW'?'讀取固定來源':'Read fixed sources',exact:true}).click();await expect(result(page)).not.toBeEmpty();
    await result(page).locator('details').first().locator('summary').first().click();
    for(const width of [1440,768,390]){await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      if(width===390){expect((await box(page).getByRole('button').first().boundingBox())!.height).toBeGreaterThanOrEqual(44);
        expect(await box(page).locator('textarea').evaluate((node:HTMLElement)=>Number.parseFloat(getComputedStyle(node).fontSize))).toBeGreaterThanOrEqual(16);}
      if(shots)await page.screenshot({path:`${shots}/sources-${locale}-${width}.png`,fullPage:true});}
    await context.close();
  }
});
