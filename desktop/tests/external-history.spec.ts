import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {workbenchFixture, route, workspaceId} from './pm-workbench-fixture.ts';
function fixture() {
  const base=workbenchFixture(), at='2026-10-18T10:00:00Z';
  const row={source_id:'example',adapter:'codex-history-jsonl-v1',status:'current',readable:true,stale:false,entries:3,collected_at:at,cli_version:'0.160.0',ownership:'external_read_only',execution_status:'unknown',coverage:{history_complete:false,omitted_records:2,omitted_parts:1,clipped_records:0}};
  const records=[1,2,3].map(n=>({entry_id:`shi_${n}`,sequence:n,role:n===1?'user':'assistant',observed_at:at,clipped:false,omitted_parts:0,parts:[{kind:'text',text:n===1?'Please review the keyboard fix.':n===2?'The viewport keeps the composer visible.':'<img src=x onerror=alert(1)> is literal source text.'}]}));
  const state={rows:[row],records,deny:false,supported:true,mismatch:false,reads:[] as string[],writes:[] as string[],hold:null as Promise<void>|null};
  async function dispatch(input:any) {
    const url=new URL(input.path,'http://fixture');
    if(input.method&&input.method!=='GET')state.writes.push(input.path);
    if(url.pathname.endsWith('/history')&&url.pathname.includes('/pm/workspaces/')){
      state.reads.push(input.path);return state.deny?{status:403,data:{error:{code:'FORBIDDEN',message:'History withdrawn'}}}:{status:200,data:{workspace_id:workspaceId,rows:structuredClone(state.rows),partial:false}};
    }
    if(url.pathname.endsWith('/history/example')){
      state.reads.push(input.path);const after=Number(url.searchParams.get('after')||0),entries=structuredClone(state.records.filter(e=>e.sequence>after).slice(0,2));
      const result=state.deny?{status:404,data:{error:{code:'NOT_FOUND',message:'History withdrawn'}}}:{status:200,data:{workspace_id:workspaceId,source:structuredClone(state.rows[0]),entries,next_after:state.records.some(e=>e.sequence>(entries.at(-1)?.sequence||after))?entries.at(-1)?.sequence:null}};
      if(state.mismatch)result.data.workspace_id='ws_'+ '2'.repeat(32);
      if(state.hold)await state.hold;return result;
    }
    if(url.pathname.endsWith('/resources'))return {status:200,data:{workspace_id:workspaceId,rows:[],quota_pools:[],as_of:at}};
    const result=await base.dispatch(input);
    if(url.pathname==='/bootstrap'||url.pathname==='/capabilities'){
      const caps=url.pathname==='/bootstrap'?result.data.capabilities:result.data;caps.features.pm_resources={version:1};
      if(state.supported)caps.features.external_history={version:1,read_history:true,start_owned:false,send_owned:false};
    }
    return result;
  }
  return {state,dispatch};
}
const area=(page:any)=>page.locator('[data-external-history]'),detail=(page:any)=>page.locator('[data-history-detail]');
async function open(page:any,native:boolean,f:any){
  await mountConversation(page,native,f.dispatch);await page.goto(route);
  if((page.viewportSize()?.width||1440)<1000)await page.locator('#workspace-menu').click();
  await page.locator('[data-pm-view="agents"]').click();await expect(area(page).locator('[data-history-source]')).toHaveCount(f.state.rows.length);
}
for(const native of [false,true]){
 test(`shared read-only history pages ${native?'IPC':'HTTP'}`,async({page})=>{
  const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await expect(page.locator('[data-pm-charter]')).toBeVisible();expect(f.state.reads).toEqual([]);
  await page.locator('[data-pm-view="agents"]').click();await area(page).locator('[data-history-source]').click();await expect(detail(page).locator('[data-history-entry]')).toHaveCount(2);await expect(detail(page).locator('[data-history-entry]').first()).toBeVisible();
  await detail(page).getByRole('button',{name:'Read more',exact:true}).click();await expect(detail(page).locator('[data-history-entry]')).toHaveCount(3);
  await expect(detail(page)).toContainText('<img src=x');await expect(detail(page).locator('img')).toHaveCount(0);await expect(detail(page)).toContainText('Partial history');
  expect(f.state.writes).toEqual([]);await expect(detail(page).getByRole('button',{name:/Send|Start|Resume session/})).toHaveCount(0);
 });
 test(`refresh keeps reading and announces new entries ${native}`,async({page})=>{
  const f=fixture();await page.clock.install();await open(page,native,f);await area(page).locator('[data-history-source]').click();await detail(page).getByRole('button',{name:'Read more',exact:true}).click();await expect(detail(page).locator('[data-history-entry]')).toHaveCount(3);
  const entry=detail(page).locator('[data-history-entry]').first();await entry.evaluate((node:any)=>{node.dataset.preserved='yes';});
  f.state.records.push({...f.state.records[2],entry_id:'shi_4',sequence:4,parts:[{kind:'text',text:'New recorded answer'}]});f.state.rows[0].entries=4;await page.clock.fastForward(61000);
  await expect(detail(page).getByRole('button',{name:'Read new entries',exact:true})).toBeVisible();await expect(entry).toHaveAttribute('data-preserved','yes');await expect(detail(page)).not.toContainText('New recorded answer');
  await detail(page).getByRole('button',{name:'Read new entries',exact:true}).click();await expect(detail(page)).toContainText('New recorded answer');f.state.deny=true;await page.clock.fastForward(61000);
  await expect(detail(page)).toBeHidden();await expect(area(page)).not.toContainText('New recorded answer');expect(f.state.writes).toEqual([]);
 });
}
test('late page cannot reopen a dismissed or withdrawn source',async({page})=>{
 const f=fixture();await page.clock.install();await open(page,false,f);let release!:()=>void;f.state.hold=new Promise<void>(r=>release=r);
 await area(page).locator('[data-history-source]').click();await expect(detail(page)).toBeVisible();await detail(page).getByRole('button',{name:'Close',exact:true}).click();release();f.state.hold=null;
 await expect(detail(page)).toBeHidden();await expect(area(page).locator('[data-history-source]')).toBeFocused();f.state.hold=new Promise<void>(r=>release=r);await area(page).locator('[data-history-source]').click();
 f.state.rows=[];await page.clock.fastForward(61000);release();await expect(detail(page)).toBeHidden();await expect(area(page)).not.toContainText('Please review');
});
test('browser Back closes the reader, and view exit stops polling',async({page})=>{
 const f=fixture();await page.clock.install();await open(page,false,f);await area(page).locator('[data-history-source]').click();await expect(detail(page).locator('[data-history-entry]')).toHaveCount(2);await expect(detail(page).locator('[data-history-entry]').first()).toBeVisible();
 await page.goBack();await expect(detail(page)).toBeHidden();await expect(area(page).locator('[data-history-source]')).toBeFocused();await page.locator('[data-pm-view="overview"]').click();const count=f.state.reads.length;await page.clock.fastForward(120000);expect(f.state.reads.length).toBe(count);
});
test('unavailable source hides plaintext and empty grants have a useful state',async({page})=>{
 const f=fixture();f.state.rows[0].readable=false;f.state.rows[0].status='source_unavailable';await open(page,false,f);await area(page).locator('[data-history-source]').click();await expect(detail(page)).toContainText('unavailable');await expect(detail(page).locator('[data-history-entry]')).toHaveCount(0);
 await detail(page).getByRole('button',{name:'Close',exact:true}).click();f.state.rows=[];await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(area(page)).toContainText('No conversation histories are shared');
});
test('older server does not receive history reads',async({page})=>{
 const f=fixture();f.state.supported=false;await mountConversation(page,false,f.dispatch);await page.goto(route);await page.locator('[data-pm-view="agents"]').click();await expect(page.locator('[data-pm-resources]')).toBeVisible();await expect(area(page)).toHaveCount(0);expect(f.state.reads).toEqual([]);
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`external history layout ${locale} ${width}`,async({browser,baseURL})=>{
 const context=await browser.newContext({baseURL,locale,viewport:{width,height:900},colorScheme:'light'}),page=await context.newPage(),f=fixture();const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await open(page,false,f);
 const dir=process.env.BATC_HISTORY_SCREENSHOTS;if(dir){await mkdir(dir,{recursive:true});await page.screenshot({path:`${dir}/list-${locale}-${width}.png`,fullPage:true});}
 await area(page).locator('[data-history-source]').click();await expect(detail(page).locator('[data-history-entry]')).toHaveCount(2);await expect(detail(page).locator('[data-history-entry]').first()).toBeVisible();await expect(detail(page)).not.toContainText(/exh_|undefined/);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 if(dir)await page.screenshot({path:`${dir}/detail-${locale}-${width}.png`,fullPage:true});expect(errors).toEqual([]);await context.close();
});

for(const width of [320,768,1440])test(`reader close control remains reachable ${width}`,async({page})=>{
 await page.setViewportSize({width,height:844});const f=fixture();await open(page,false,f);await area(page).locator('[data-history-source]').click();await expect(detail(page).locator('[data-history-entry]').first()).toBeVisible();
 await detail(page).getByRole('button',{name:width<800?'Back':'Close',exact:true}).click();await expect(detail(page)).toBeHidden();await expect(area(page).locator('[data-history-source]')).toBeFocused();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
test('mismatched workspace detail is never painted',async({page})=>{
 const f=fixture();f.state.mismatch=true;await open(page,false,f);await area(page).locator('[data-history-source]').click();await expect(detail(page)).toContainText('does not belong');await expect(detail(page).locator('[data-history-entry]')).toHaveCount(0);
});
test('departed workspace rejects held detail without an unhandled error',async({page})=>{
 const f=fixture(),errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await open(page,false,f);let release!:()=>void;f.state.hold=new Promise<void>(r=>release=r);
 await area(page).locator('[data-history-source]').click();await expect(detail(page)).toBeVisible();await page.goto('/dashboard/#/settings');release();await expect(detail(page)).toHaveCount(0);await expect(page.locator('body')).not.toContainText('Please review the keyboard fix.');expect(errors).toEqual([]);
});
