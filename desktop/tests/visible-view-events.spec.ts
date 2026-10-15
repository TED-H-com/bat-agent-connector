import {readFileSync} from 'node:fs';
import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';

// Complete event pages coalesce per visible projection. These budgets include
// the rail, context resolution, and an open inspector's own reads; polling is excluded.
const budgets: Record<string,number> = {overview:4,discussions:4,requirements:24,board:22,schedule:3,dependencies:4,issues:5,agents:8};
const allTypes=['pm_workspace','session','pm_governance','task','operation','pm_dependencies','work_item','command','deployment','integration'];
for(const [view,budget] of Object.entries(budgets)) for(const size of [10,20]) test(`${size} PM and live events refresh only ${view} (budget ${budget})`,async({page},info)=>{
  const f=demoFixture({discussions:true}),reads:{path:string,at:number}[]=[],writes:string[]=[];
  let cursor=0,delivered=0;
  const types=size===10?['session','task','operation','pm_workspace','pm_governance','session','task','operation','pm_workspace','pm_governance']:allTypes;
  if(process.env.BATC_EVENT_BASELINE_JS) await page.route('**/dashboard/app.js',route=>route.fulfill({contentType:'text/javascript',body:readFileSync(process.env.BATC_EVENT_BASELINE_JS!,'utf8')}));
  await mountConversation(page,false,async input=>{
    const url=new URL(input.path,'http://fixture');
    if(url.pathname==='/events'){
      const after=Number(url.searchParams.get('after'));if(cursor>after)delivered=Date.now();
      return {status:200,data:{events:Array.from({length:cursor},(_,index)=>({seq:index+1,resource_type:index>=size?'pm_workspace':types[index%types.length],
        resource_id:types[index%types.length]==='session'?`${f.ids.host}/${f.ids.sessions.implement}`:f.ids.workspaceId,kind:types[index%types.length]+'.updated'})).filter(row=>row.seq>after),
        next_cursor:cursor,head_cursor:cursor,has_more:false,sync:{checkpoint:{cursor,token:`visible-${cursor}`}}}};
    }
    if(input.method==='GET')reads.push({path:input.path,at:Date.now()});else writes.push(input.path);
    return f.dispatch(input);
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/${view}`);await expect(page.locator('#main h1')).toBeVisible();
  if(view==='schedule'&&size===20){await page.locator(`[data-schedule-select="work:${f.ids.work.resetEmail}"]`).click();await expect(page.locator('[data-schedule-inspector]')).toBeVisible();}
  await page.waitForTimeout(1200);reads.length=0;cursor=size;
  await expect.poll(()=>page.evaluate(token=>Object.values(localStorage).some(value=>value.includes(token)),`visible-${size}`)).toBe(true);
  await page.waitForTimeout(400);
  console.log(JSON.stringify({view,size,baseline:Boolean(process.env.BATC_EVENT_BASELINE_JS),gets:reads.length,paths:reads.map(row=>row.path)}));
  await info.attach('event-read-budget',{body:JSON.stringify(reads),contentType:'application/json'});
  expect(writes).toEqual([]);expect(reads.some(row=>row.at>=delivered)).toBe(true);
  if(!process.env.BATC_EVENT_BASELINE_JS){
    expect(reads.length,JSON.stringify(reads)).toBeLessThanOrEqual(budget);
    expect(reads.filter(row=>row.path==='/projects')).toHaveLength(1);
    expect(reads.filter(row=>row.path==='/pm/workspaces')).toHaveLength(0);
    if(view==='issues')expect(reads.filter(row=>new URL(row.path,'http://fixture').pathname===`/pm/workspaces/${f.ids.workspaceId}/plans/accepted`)).toHaveLength(1);
    if(view!=='schedule')expect(reads.filter(row=>/\/schedule(?:\?|$)/.test(row.path))).toHaveLength(0);
    if(view!=='requirements'&&view!=='board')expect(reads.filter(row=>/\/deliveries(?:\?|$)/.test(row.path))).toHaveLength(0);
  }
  // A separate later page must start another read, rather than sharing a settled promise.
  const previous=reads.length;cursor++;await expect.poll(()=>reads.length).toBeGreaterThan(previous);
});

for(const view of ['board','requirements']) test(`a hidden withdrawn ${view} never paints old rows when reopened`,async({page})=>{
  const f=demoFixture(),wid=f.ids.workspaceId;let cursor=0,deny=false,held:(()=>void)|null=null,hold=false;
  const reads:string[]=[],writes:string[]=[];
  const source = `/pm/workspaces/${wid}/${view === 'board' ? 'kanban' : 'plans/accepted'}`;
  const rows = page.locator(view === 'board' ? '[data-board-open]' : '[data-rtm-requirement]');
  await mountConversation(page,false,async input=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path==='/events')return {status:200,data:{events:Number(new URL(input.path,'http://fixture').searchParams.get('after'))<cursor?[{seq:cursor,resource_type:'pm_governance',resource_id:wid,kind:'pm_governance.updated'}]:[],next_cursor:cursor,head_cursor:cursor,has_more:false,sync:{checkpoint:{cursor,token:`hidden-${cursor}`}}}};
    if(input.method==='GET')reads.push(path);else writes.push(path);
    if(path===source&&deny){if(hold)await new Promise<void>(resolve=>{held=resolve;});return {status:403,data:{error:{code:'FORBIDDEN_SCOPE',message:'Unavailable'}}};}
    return f.dispatch(input);
  });
  await page.goto(`/dashboard/#/pm/${wid}/${view}`);await expect(rows).toHaveCount(view === 'board' ? 12 : 8);
  await rows.first().click();await expect(page.locator('[data-work-inspector]')).toBeVisible();
  await page.locator('[data-pm-view=overview]').click();await expect(page.locator('[data-pm-overview]')).toBeVisible();
  await page.waitForTimeout(1200);const before=reads.length;deny=true;hold=true;cursor++;
  await expect.poll(()=>page.evaluate(()=>Object.values(localStorage).some(value=>value.includes('hidden-1')))).toBe(true);
  expect(reads.slice(before)).not.toContain(source);
  await page.locator(`[data-pm-view=${view}]`).click();await expect.poll(()=>Boolean(held)).toBe(true);
  await expect(rows.filter({visible:true})).toHaveCount(0);
  await expect(page.locator('[data-pm-source-loading]:visible')).not.toHaveCount(0);
  held!();await expect(rows).toHaveCount(0);
  await expect(page.locator('[data-work-inspector]')).toHaveCount(0);expect(writes).toEqual([]);
});

test('the untouched sidebar is 232px at 1000 and 1199',async({page})=>{
  const f=demoFixture();await mountConversation(page,false,f.dispatch);
  for(const width of [1000,1199]){
    await page.setViewportSize({width,height:900});await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/overview`);
    await expect(page.locator('[data-pm-overview]')).toBeVisible();
    expect(await page.locator('#workspace-sidebar').evaluate(node=>node.getBoundingClientRect().width)).toBe(232);
  }
});


test('Agents pause survives event reads and resumes with a fresh source read',async({page})=>{
  const f=demoFixture(),wid=f.ids.workspaceId;let cursor=0;const reads:string[]=[],writes:string[]=[];
  await mountConversation(page,false,async input=>{
    const url=new URL(input.path,'http://fixture');
    if(url.pathname==='/events')return {status:200,data:{events:Number(url.searchParams.get('after'))<cursor?[{seq:cursor,resource_type:'pm_workspace',resource_id:wid,kind:'pm_workspace.updated'}]:[],next_cursor:cursor,head_cursor:cursor,has_more:false,sync:{checkpoint:{cursor,token:`paused-${cursor}`}}}};
    if(input.method==='GET')reads.push(url.pathname);else writes.push(url.pathname);
    return f.dispatch(input);
  });
  await page.goto(`/dashboard/#/pm/${wid}/agents`);await expect(page.locator('[data-resource-row]')).toHaveCount(3);
  await page.getByRole('button',{name:'Pause updates',exact:true}).click();
  const stamp=page.locator('[data-resource-meta] time'),frozen=await stamp.getAttribute('datetime');
  await page.waitForTimeout(1200);reads.length=0;cursor++;
  await expect.poll(()=>page.evaluate(()=>Object.values(localStorage).some(value=>value.includes('paused-1')))).toBe(true);
  expect(reads).not.toContain(`/pm/workspaces/${wid}/resources`);
  await expect(stamp).toHaveAttribute('datetime',frozen!);
  await expect(page.getByRole('button',{name:'Resume updates',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Resume updates',exact:true}).click();
  await expect.poll(()=>reads.filter(path=>path===`/pm/workspaces/${wid}/resources`).length).toBe(1);
  expect(writes).toEqual([]);
});
