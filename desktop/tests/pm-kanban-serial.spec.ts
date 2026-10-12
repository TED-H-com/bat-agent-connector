import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {runtimeFixture,mountRuntime,action} from './pm-kanban-runtime-fixture.ts';
import {workId} from './pm-kanban-fixture.ts';

const row=(page:any)=>page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
const editor=(page:any)=>row(page).locator(`[data-pm-action="${action}"]`);
const receipt=(page:any)=>row(page).locator('[data-pm-manual-observation]');
const labels=(locale:string)=>locale==='en'?{heading:'Work flow',check:'Check current activity',recover:'Check original request',
  recorded:'all 64 recorded inputs had completed and no input was pending',limits:'Quality acceptance, current workload eligibility and process termination are not established',refresh:'Refresh work flow'}:
  {heading:'工作流',check:'查核目前活動',recover:'查回原請求',recorded:'全部 64 次已記錄輸入均已完成，沒有待處理輸入',limits:'不代表品質已驗收、目前符合工作量排除條件，或程序已終止',refresh:'重新整理工作流'};

for(const native of [false,true])for(const locale of ['en','zh-TW'])test(`serial receipt keeps original key and scope ${native?'Tauri':'Web'} ${locale}`,async({browser,baseURL},info)=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage(),f=runtimeFixture(),l=labels(locale);
  try{
    await mountRuntime(page,native,f);await expect(page.locator('[data-pm-kanban] h2')).toHaveText(l.heading);
    await row(page).locator('summary').first().click();await editor(page).locator('select').selectOption({label:'fixture-host / manual-session-2'});
    // Selector availability authorizes a read attempt, not a completion claim.
    await expect(receipt(page)).not.toContainText(l.recorded);f.state.lost=true;
    await editor(page).getByRole('button',{name:l.check,exact:true}).click();await expect(editor(page)).toContainText('Original reply was lost');
    const original=structuredClone(f.state.writes[0]);await page.reload();await row(page).locator('summary').first().click();
    await expect(editor(page).locator('select')).toBeDisabled();
    await expect(editor(page).locator('select')).toHaveValue(JSON.stringify([2,'op_'+'2'.repeat(32),'pmpb_'+'4'.repeat(32),'2'.repeat(64)]));
    await editor(page).getByRole('button',{name:l.recover,exact:true}).click();await expect(editor(page)).toContainText('waiting_external');
    expect(f.state.writes).toHaveLength(2);expect(f.state.writes[1]).toEqual(original);
    f.complete(64);f.base.state.role='reader';f.base.state.manage=false;
    await page.reload();await row(page).locator('summary').first().click();
    await editor(page).getByRole('button',{name:l.recover,exact:true}).click();await expect(receipt(page)).toContainText(l.recorded);
    await expect(receipt(page)).toContainText(l.limits);await expect(editor(page).getByRole('button',{name:l.check,exact:true})).toBeDisabled();
    expect(f.state.writes).toHaveLength(2);expect(f.state.ops.size).toBe(1);expect(f.base.state.writes).toEqual([]);
    if(!native){
      const folder=process.env.PM_SCREENSHOTS||info.outputPath('screenshots');await mkdir(folder,{recursive:true});
      await page.locator('.top').evaluate((node:any)=>node.remove());
      for(const width of [390,768,1440]){
        await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
        await receipt(page).screenshot({path:`${folder}/manual-serial-${locale}-${width}.png`});
      }
    }
    f.board.state.failRead=403;await page.getByRole('button',{name:l.refresh,exact:true}).click();
    await expect(row(page)).toHaveCount(0);await expect(page.locator('[data-pm-kanban]')).not.toContainText(l.recorded);
    await expect(page.locator('[data-pm-kanban]')).not.toContainText('e'.repeat(64));
  }finally{await context.close();}
});

const invalid:[string,(result:any)=>void][]=[
  ['count below serial range',r=>{r.completed_turn_count=1;r.input_state.received=1;}],
  ['count above serial bound',r=>{r.completed_turn_count=65;r.input_state.received=65;}],
  ['fractional completed inputs',r=>{r.completed_turn_count=2.5;r.input_state.received=2.5;}],
  ['completed count differs from native received',r=>{r.completed_turn_count=3;}],
  ['pending native input',r=>{r.input_state.pending=1;}],
  ['unsupported native input schema',r=>{r.input_state.schema='unknown_counter_v1';}],
  ['mixed single-input runtime status',r=>{r.runtime_status='current_single_input_quiescent';}],
  ['single-turn identity on serial receipt',r=>{r.native_turn_id='foreign-turn';}],
  ['private turn history on public receipt',r=>{r.turns=[{private_prompt:'not public'}];}],
  ['missing fixed turns digest',r=>{delete r.turns_digest;}],
  ['malformed fixed turns digest',r=>{r.turns_digest='not-a-sha256';}],
  ['wrong original link',r=>{r.link_id=2;}],
  ['wrong original operation',r=>{r.observation_operation_id='op_'+'f'.repeat(32);}],
  ['missing native identity',r=>{delete r.sdk_session_id;}],
  ['inexact replay cursor',r=>{r.completion_cursor.seq='8';}],
  ['process death claim',r=>{r.process_death_proven=true;}],
  ['work acceptance claim',r=>{r.work_acceptance='accepted';}],
];
for(const [index,[name,mutate]] of invalid.entries())test(`invalid serial receipt retains original recovery: ${name}`,async({page})=>{
  const f=runtimeFixture();await mountRuntime(page,index%2===1,f);await row(page).locator('summary').first().click();
  await editor(page).locator('select').selectOption({label:'fixture-host / manual-session-1'});
  await editor(page).getByRole('button',{name:'Check current activity',exact:true}).click();await expect(editor(page)).toContainText('waiting_external');
  const original=structuredClone(f.state.writes[0]);f.complete(2);const operation=[...f.state.ops.values()][0];mutate(operation.result);
  await editor(page).getByRole('button',{name:'Check original request',exact:true}).click();
  await expect(editor(page)).toContainText('does not match this workspace');await expect(editor(page).locator('select')).toBeDisabled();
  await expect(receipt(page)).not.toContainText('recorded inputs had completed');await expect(receipt(page)).not.toContainText('e'.repeat(64));
  await page.reload();await row(page).locator('summary').first().click();await expect(editor(page).locator('select')).toBeDisabled();
  f.complete(2);await editor(page).getByRole('button',{name:'Check original request',exact:true}).click();
  await expect(receipt(page)).toContainText('all 2 recorded inputs had completed');
  expect(f.state.writes).toEqual([original]);expect(f.state.ops.size).toBe(1);expect(f.base.state.writes).toEqual([]);
});
