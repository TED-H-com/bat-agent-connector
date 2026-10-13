import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {workbenchFixture,route,workspaceId} from './pm-workbench-fixture.ts';

function fixture(){
  const base=workbenchFixture(),at=new Date().toISOString();
  const values=(total:number)=>Object.fromEntries(Object.entries({input:800,cache_read:400,cache_write:null,output:200,reasoning:100,total})
    .map(([key,value])=>[key,{value,observed:value,covered:value==null?0:3}]));
  const row={source_id:'Implementation',adapter:'codex-rollout-v1',role:'implementer',status:'current',metrics:values(1000),observations:3,calls:null,
    checked_at:at,successful_at:at,first_at:at,last_at:at,configurations:[{actual_model:null,requested_model:'example-codex',effort:'high',cli_version:'0.120.0'}]};
  const state={deny:false,hold:null as Promise<void>|null,reads:[] as string[],data:{workspace_id:workspaceId,as_of:at,interval_s:60,rows:[row,
    {...row,source_id:'Review',adapter:'claude-transcript-v1',role:'reviewer',calls:3,metrics:values(650),configurations:[{actual_model:'example-claude',requested_model:null,effort:null,cli_version:'2.1.0'}]},
    {...row,source_id:'Investigation',status:'source_unavailable',metrics:null,observations:null,configurations:[]}],quota_pools:[{
      pool_id:'pool_'+'a'.repeat(32),limit_id:'codex',sources:['Implementation'],status:'reported',snapshot:{observed_at:at,windows:[{window:'primary',used_percent:25,window_minutes:300,resets_at:Math.floor(Date.now()/1000)+3600}]}}]}};
  async function dispatch(input:any){
    const path=input.path.split('?')[0];
    if(path.endsWith('/resources')){state.reads.push(input.path);const result=state.deny?{status:403,data:{error:{code:'FORBIDDEN',message:'Source withdrawn'}}}:{status:200,data:structuredClone(state.data)};if(state.hold)await state.hold;return result;}
    const result=await base.dispatch(input);
    if(path==='/bootstrap'||path==='/capabilities'){const caps=path==='/bootstrap'?result.data.capabilities:result.data;caps.features.pm_resources={version:1};}
    return result;
  }
  return {state,dispatch};
}
const resources=(page:any)=>page.locator('[data-pm-resources]');
async function open(page:any,native:boolean,f:any){await mountConversation(page,native,f.dispatch);await page.goto(route);await page.locator('[data-pm-view="agents"]').click();await expect(resources(page).locator('[data-resource-row]')).toHaveCount(3);}

for(const native of [false,true])test.describe(native?'native resources':'web resources',()=>{
  test('lazy resource read, distinct model evidence, missing counters and quota',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await expect(page.locator('[data-pm-charter]')).toBeVisible();expect(f.state.reads).toHaveLength(0);
    await page.locator('[data-pm-view="agents"]').click();await expect(resources(page)).toContainText('1,000');
    await resources(page).getByRole('button',{name:'Implementation',exact:true}).click();const detail=resources(page).locator('[data-resource-detail]');
    await expect(detail).toBeVisible();await expect(detail).toContainText('Requested model');await expect(detail).toContainText('Not provided');
    await expect(resources(page).locator('[data-resource-quota]')).toContainText('75%');
    await resources(page).getByRole('searchbox').fill('reviewer');await expect(resources(page).locator('[data-resource-row]')).toHaveCount(1);
    await expect(resources(page).locator('[data-resource-quota]')).not.toContainText('75%');
    await resources(page).getByLabel('Observation window').selectOption('30');await expect.poll(()=>f.state.reads.at(-1)).toContain('since=');
  });
  test('source withdrawal removes details, quota and counts',async({page})=>{
    const f=fixture();await open(page,native,f);await resources(page).getByRole('button',{name:'Implementation',exact:true}).click();await expect(resources(page).locator('[data-resource-detail]')).toBeVisible();
    f.state.deny=true;await resources(page).getByRole('button',{name:'Update records',exact:true}).click();
    await expect(resources(page)).toContainText('Your token lacks this scope');await expect(resources(page).locator('[data-resource-row]')).toHaveCount(0);
    await expect(resources(page).locator('[data-resource-detail]')).toBeHidden();await expect(resources(page)).not.toContainText('75%');await expect(resources(page)).not.toContainText('1,000');
  });
  test('unchanged refresh preserves details disclosure and keyboard focus',async({page})=>{
    const f=fixture();await open(page,native,f);const select=resources(page).getByRole('button',{name:'Implementation',exact:true});await select.click();
    const disclosure=resources(page).locator('[data-resource-detail] details');await disclosure.locator('summary').click();
    await select.focus();await page.clock.install();await page.clock.fastForward(61000);
    await expect(select).toBeFocused();await expect(disclosure).toHaveAttribute('open','');
    await resources(page).getByRole('button',{name:'Close details',exact:true}).click();await expect(select).toBeFocused();
  });
  test('pause and view exit stop polling and late reads cannot restore a departed view',async({page})=>{
    const f=fixture();await page.clock.install();await open(page,native,f);await resources(page).getByRole('button',{name:'Pause updates',exact:true}).click();
    const count=f.state.reads.length;await page.clock.fastForward(120000);expect(f.state.reads).toHaveLength(count);
    await resources(page).getByRole('button',{name:'Resume updates',exact:true}).click();await expect.poll(()=>f.state.reads.length).toBe(count+1);
    await page.locator('[data-pm-view="charter"]').click();await page.clock.fastForward(120000);expect(f.state.reads).toHaveLength(count+1);
  });
  test('empty sources remain unknown and model text is inert',async({page})=>{
    const f=fixture();f.state.data.rows[0].configurations[0].requested_model='<img src=x onerror=alert(1)>';
    await open(page,native,f);await expect(resources(page)).toContainText('<img src=x');await expect(resources(page).locator('img')).toHaveCount(0);
    f.state.data.rows=[];f.state.data.quota_pools=[];await resources(page).getByRole('button',{name:'Update records',exact:true}).click();
    await expect(resources(page)).toContainText('No usage sources are connected');await expect(resources(page)).toContainText('No current quota balance');
  });
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`resource layout ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:1000},colorScheme:'light',reducedMotion:'reduce'}),page=await context.newPage(),f=fixture();
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await open(page,false,f);
  await resources(page).locator('[data-resource-select]').first().click();await expect(resources(page).locator('[data-resource-detail]')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(await resources(page).innerText()).not.toMatch(/\b(?:undefined|null|res_\w+)\b/);
  const out=process.env.BATC_RESOURCE_SCREENSHOTS;if(out){await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/resources-${locale}-${width}.png`,fullPage:true});}
  expect(errors).toEqual([]);await context.close();
});
