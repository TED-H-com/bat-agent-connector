import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {fixture, mount, projectId, workId} from './pm-kanban-fixture.ts';

const row = (page:any, n:number) => page.locator(`[data-pm-kanban-work="${workId(n)}"]`);
const reload = (page:any) => page.evaluate(() => (window as any).refreshKanban());

test('central stages form a board with compact cards and accounting available on demand', async ({page}) => {
  const f=fixture(); await mount(page,f);
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(2);
  await expect(page.locator('[data-pm-kanban-column]')).toHaveCount(5);
  await expect(page.locator('[data-pm-kanban-column="in_progress"] [data-pm-kanban-work]')).toHaveCount(1);
  await expect(row(page,1)).not.toContainText('3 accepted sends');
  await expect(page.getByText('Latest page: 2 WIP', {exact:false})).toBeHidden();
  await page.getByText('Board details',{exact:true}).click();
  await expect(page.locator('[data-pm-kanban]')).toContainText('Latest page: 2 WIP');
  await expect(page.locator('[data-pm-kanban]')).toContainText('More work remains outside this page');
  await expect(row(page,2)).toContainText('Review / verify');
  await expect(page.getByRole('tab')).toHaveCount(0);
  await expect(page.locator('[draggable=true]')).toHaveCount(0);
  await expect(page.locator('[data-pm-kanban] input')).toHaveCount(0);
  expect(f.state.writes).toHaveLength(0);
});

test('pagination deduplicates a rebound work by stable identity without claiming workspace totals', async ({page}) => {
  const f=fixture(); await mount(page,f);
  f.state.cards[0].binding.sequence=4;f.state.cards[0].binding.binding_id='pmwb_'+'9'.repeat(32);f.state.cards[0].work.title='Member login, explicitly reassociated';
  f.state.cards=[f.state.cards[1],f.state.cards[2],f.state.cards[0]];
  await page.getByRole('button',{name:'Load more work',exact:true}).click();
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(3);
  await expect(row(page,1)).toContainText('explicitly reassociated');
  await expect(row(page,3)).toContainText('Content approved');
  await expect(row(page,3)).toContainText('DoD unassessed');
  await expect(page.locator('[data-pm-kanban]')).toContainText('Latest page: 1 WIP');
  await expect(page.locator('[data-pm-kanban]')).toContainText('3 distinct work items shown');
  await expect(page.getByRole('button',{name:'Load more work',exact:true})).toBeHidden();
  expect(f.state.reads[1]).toContain('after=2');expect(f.state.writes).toHaveLength(0);
});

test('expanded card rechecks access and displays current execution separately from the original capture', async ({page}) => {
  const f=fixture();await mount(page,f);f.state.cards[0].work.title='Login with reviewed title';f.state.time+=600;
  await row(page,1).getByText('Execution and sources',{exact:true}).click();
  await expect(row(page,1)).toContainText('Login with reviewed title');
  await expect(row(page,1)).toContainText('Current execution read');
  await expect(row(page,1)).toContainText('Saved evidence snapshot');
  await expect(row(page,1)).toContainText('This snapshot describes an earlier binding or work version');
  await expect(row(page,1)).toContainText('Acceptance recorded · Accepted');
  await expect(row(page,1).getByText('Content approved',{exact:true})).toHaveCount(0);
  await expect(row(page,1)).toContainText('command-1');
  const times=await row(page,1).locator('time').evaluateAll((nodes:any[])=>nodes.map(node=>node.dateTime));
  expect(new Set(times).size).toBeGreaterThan(2);
  expect(f.state.reads.at(-1)).toContain('/kanban/'+workId(1));
});

test('a fresh central stage moves the original card and updates both column counts', async ({page}) => {
  const f=fixture();await mount(page,f);
  f.state.cards[0].column='waiting';f.state.cards[0].work.state='waiting';
  await row(page,1).getByText('Execution and sources',{exact:true}).click();
  await expect(page.locator('[data-pm-kanban-column="waiting"] [data-pm-kanban-work]')).toHaveCount(1);
  await expect(page.locator('[data-pm-kanban-column="in_progress"] .pm-kanban-count')).toHaveText('0');
  await expect(row(page,1).locator('details').first()).toHaveAttribute('open','');
  await expect(row(page,1)).toContainText('command-1');
  expect(f.state.writes).toHaveLength(0);
});

test('standalone execution remains visible when there is no Task Service task',async({page})=>{
  const f=fixture();f.state.cards[0].execution.tasks=[];await mount(page,f);
  await row(page,1).getByText('Execution and sources',{exact:true}).click();
  await expect(row(page,1)).toContainText('3 accepted sends');await expect(row(page,1)).toContainText('Accepted');
  await expect(row(page,1)).not.toContainText('No visible linked execution');
});

test('foreign capture scope is rejected before showing even its timestamp',async({page})=>{
  const f=fixture();await mount(page,f);f.state.cards[0].latest_capture.workspace_id='ws_'+'9'.repeat(32);await reload(page);
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(0);
  await expect(page.locator('[data-pm-kanban]')).toContainText('does not match this workspace or page');
});

for(const failure of [403,503])test(`failed full refresh ${failure} removes all previously visible facts and can retry`,async({page})=>{
  const f=fixture();await mount(page,f);await row(page,1).getByText('Execution and sources',{exact:true}).click();
  await expect(row(page,1)).toContainText('command-1');f.state.failRead=failure;await reload(page);
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(0);
  await expect(page.locator('[data-pm-kanban]')).not.toContainText('command-1');
  await expect(page.locator('[data-pm-kanban]')).not.toContainText('Latest page: 2 WIP');
  f.state.failRead=null;await page.getByRole('button',{name:'Retry work flow',exact:true}).click();
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(2);
  expect(f.state.reads.at(-1)).toContain('after=0');expect(f.state.writes).toHaveLength(0);
});

test('pagination failure clears facts and retry starts from the first page rather than skipping work',async({page})=>{
  const f=fixture();await mount(page,f);f.state.failMore=503;await page.getByRole('button',{name:'Load more work',exact:true}).click();
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(0);
  f.state.failMore=null;await page.getByRole('button',{name:'Retry work flow',exact:true}).click();
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(2);
  await page.getByRole('button',{name:'Load more work',exact:true}).click();
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(3);
});

test('revocation during a lazy card read clears list, detail and counters',async({page})=>{
  const f=fixture();await mount(page,f);f.state.failRead=403;
  await row(page,1).getByText('Execution and sources',{exact:true}).click();
  await expect(page.locator('[data-pm-kanban]')).toContainText('Workspace access removed');
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(0);
  await expect(page.locator('[data-pm-kanban]')).not.toContainText('Member login');
});

test('source permission change in a successful card refresh removes old source data',async({page})=>{
  const f=fixture();await mount(page,f);const control=row(page,1).getByText('Execution and sources',{exact:true});await control.click();
  await expect(row(page,1)).toContainText('command-1');await control.click();
  f.state.cards[0].execution.attempts=[];f.state.cards[0].execution.accepted_attempts=0;f.state.cards[0].execution.coverage.unavailable_sources=true;
  f.state.cards[0].latest_capture.evidence=[];f.state.cards[0].latest_capture.coverage.inaccessible_sources=true;
  await control.click();await expect(row(page,1)).not.toContainText('command-1');
  await expect(row(page,1)).toContainText('0 accepted sends');
  await expect(row(page,1)).toContainText('Some linked sources are unavailable');
});

for(const mismatch of ['wrongScope','wrongCard','nonAdvancing'])test(`rejects ${mismatch} without retaining stale facts`,async({page})=>{
  const f=fixture();await mount(page,f);f.state[mismatch]=true;
  if(mismatch==='wrongCard')await row(page,1).getByText('Execution and sources',{exact:true}).click();else await reload(page);
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(0);
  await expect(page.locator('[data-pm-kanban]')).toContainText('does not match this workspace or page');
});

test('a late old refresh never restores facts cleared by a newer permission failure',async({page})=>{
  const f=fixture();await mount(page,f);f.state.holdNext=true;
  await page.getByRole('button',{name:'Refresh work flow',exact:true}).click();await expect.poll(()=>f.state.held.length).toBe(1);
  f.state.failRead=403;await reload(page);f.state.held.shift()();
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(0);
  await expect(page.locator('[data-pm-kanban]')).toContainText('Workspace access removed');
});

test('a late expanded-card read cannot replace a newer board result',async({page})=>{
  const f=fixture();await mount(page,f);f.state.holdNext=true;
  await row(page,1).getByText('Execution and sources',{exact:true}).click();await expect.poll(()=>f.state.held.length).toBe(1);
  f.state.cards[0].work.title='Fresh central work title';await reload(page);f.state.held.shift()();
  await expect(row(page,1)).toContainText('Fresh central work title');
  await expect(row(page,1)).not.toContainText('command-1');
});

test('partial coverage is concise while unknown timing is available in details',async({page})=>{
  const f=fixture();f.state.byteLimited=true;await mount(page,f);
  await expect(row(page,2)).toContainText('Sources incomplete');
  await expect(row(page,2)).not.toContainText('Work age: Unknown');
  await row(page,2).getByText('Execution and sources',{exact:true}).click();
  await expect(row(page,2)).toContainText('Work age: Unknown');
  await expect(row(page,2)).toContainText('Recorded flow history cannot establish reliable timing');
  await expect(row(page,2)).toContainText('Source coverage is limited');
  await expect(page.locator('[data-pm-kanban]')).toContainText('shortened to fit the response limit');
});

test('empty and project-filtered reads are explicit, and work titles stay literal',async({page})=>{
  const f=fixture();f.state.cards[0].work.title='<img src=x onerror=alert(1)> literal title';await mount(page,f,{project:projectId});
  await expect(row(page,1)).toContainText('<img src=x onerror=alert(1)> literal title');
  await expect(page.locator('[data-pm-kanban] img')).toHaveCount(0);expect(f.state.reads[0]).toContain('project_id='+projectId);
  f.state.cards=[];await reload(page);await expect(page.locator('[data-pm-kanban]')).toContainText('No work has been explicitly associated');
});

for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`Kanban layout ${locale} ${width}`,async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setViewportSize({width,height:900});const f=fixture();await mount(page,f,{locale,pageSize:3});
  await expect(page.locator('[data-pm-kanban] h2')).toHaveText(locale==='en'?'Work board':'工作看板');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const out=process.env.BATC_PM_KANBAN_SCREENSHOTS||info.outputPath('screenshots');await mkdir(out,{recursive:true});
  await page.screenshot({path:`${out}/kanban-list-${locale}-${width}.png`,fullPage:true});
  await row(page,1).locator('summary').click();
  await expect(row(page,1)).toContainText('command-1');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(await page.locator('[data-pm-kanban]').innerText()).not.toContain('pm_kanban_');expect(errors).toEqual([]);
  if(width===390)expect(await page.locator('[data-pm-kanban] summary').first().evaluate(el=>el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await page.screenshot({path:`${out}/kanban-${locale}-${width}.png`,fullPage:true});
});

test('dark theme keeps the shared palette and keyboard details access',async({page},info)=>{
  await page.emulateMedia({colorScheme:'dark'});const f=fixture();await mount(page,f);
  const summary=row(page,1).locator('summary');await summary.focus();await page.keyboard.press('Enter');
  await expect(row(page,1)).toContainText('command-1');
  const out=process.env.BATC_PM_KANBAN_SCREENSHOTS||info.outputPath('screenshots');await mkdir(out,{recursive:true});
  await page.screenshot({path:`${out}/kanban-dark.png`,fullPage:true});
});
