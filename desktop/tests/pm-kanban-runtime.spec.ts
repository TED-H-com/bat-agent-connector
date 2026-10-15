import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {runtimeFixture,mountRuntime,action,executionId,observerId} from './pm-kanban-runtime-fixture.ts';
import {workId} from './pm-kanban-fixture.ts';
const row=(page:any)=>page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
const panel=(page:any)=>page.locator('[data-work-inspector]');
const editor=(page:any)=>panel(page).locator(`[data-pm-action="${action}"]`);
const openDetail=async(page:any)=>{await row(page).locator('[data-board-open]').click();await panel(page).locator('[data-work-section=details] > summary').click();};
const labels=(locale:string)=>locale==='en'?{heading:'Board',detail:'Details',check:'Check current activity',recover:'Check original request',refresh:'Refresh'}:
  {heading:'工作看板',detail:'詳細資料',check:'查核目前活動',recover:'查回原請求',refresh:'重新整理'};

for(const native of [false,true])for(const locale of ['en','zh-TW'])test(`original completion and manual original-key recovery ${native?'Tauri':'Web'} ${locale}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage(),f=runtimeFixture(),l=labels(locale);
  try{
    await mountRuntime(page,native,f);await expect(page.locator('.pm-main > .ui-page-header h1')).toHaveText(l.heading);
    await expect(page.locator('[data-pm-kanban-column]')).toHaveCount(5);
    await expect(row(page)).toContainText(locale==='en'?'Original turn completed':'已記錄原回合完成');
    await expect(row(page).locator('[data-runtime-completion]')).toHaveCount(0);
    await openDetail(page);
    await expect(panel(page)).toContainText(locale==='en'?'The original execution turn completed.':'原始執行回合已完成。');
    await expect(panel(page)).toContainText(locale==='en'?'Definition of done not assessed':'完成定義尚未評估');
    await expect(panel(page)).toContainText('fixture-host/retained-managed');
    await expect(panel(page)).toContainText(locale==='en'?'Session · Idle':'工作階段 · 閒置中');
    await expect(panel(page).locator(`[data-runtime-completion="${executionId}"] a`)).toHaveAttribute('href',`#/op/${observerId}`);
    await editor(page).locator('select').selectOption({label:'fixture-host / manual-session-2'});f.state.lost=true;
    await editor(page).getByRole('button',{name:l.check,exact:true}).click();await expect(editor(page)).toContainText('Original reply was lost');
    const original=structuredClone(f.state.writes[0]);expect(original.body.params).toEqual({link_id:2});
    expect(original.body.preconditions).toEqual({expected_link_operation_id:'op_'+'2'.repeat(32),expected_project_binding_id:'pmpb_'+'4'.repeat(32),expected_session_identity_digest:'2'.repeat(64)});
    await page.reload();await openDetail(page);await expect(editor(page).locator('select')).toBeDisabled();
    await expect(editor(page).locator('select')).toHaveValue(JSON.stringify([2,'op_'+'2'.repeat(32),'pmpb_'+'4'.repeat(32),'2'.repeat(64)]));
    await editor(page).getByRole('button',{name:l.recover,exact:true}).click();await expect(editor(page)).toContainText(/Waiting externally|等待外部條件/);
    expect(f.state.writes).toHaveLength(2);expect(f.state.writes[1].body).toEqual(original.body);expect(f.state.writes[1].idempotency_key).toBe(original.idempotency_key);
    f.complete();await editor(page).getByRole('button',{name:l.recover,exact:true}).click();
    await expect(editor(page)).toContainText(/Succeeded|已成功/);await expect(panel(page)).toContainText(locale==='en'?'manual session remains under its existing control':'人工工作階段仍維持原控制權');
    expect(f.state.writes).toHaveLength(2);expect(f.base.state.writes).toEqual([]);
  }finally{await context.close();}
});

test('changed original native identity never silently replaces a saved draft or pending source',async({page})=>{
  const f=runtimeFixture();await mountRuntime(page,false,f);await openDetail(page);await editor(page).locator('select').selectOption({label:'fixture-host / manual-session-1'});
  f.card.execution.manual_observation_sources[0].expected_session_identity_digest='f'.repeat(64);await page.reload();await openDetail(page);
  await expect(editor(page).locator('select option:checked')).toHaveText('Previously selected source is unavailable');
  await expect(editor(page).getByRole('button',{name:'Check current activity',exact:true})).toBeDisabled();expect(f.state.writes).toEqual([]);
  await editor(page).locator('select').selectOption({label:'fixture-host / manual-session-1'});await expect(editor(page).getByRole('button',{name:'Check current activity',exact:true})).toBeEnabled();
  f.state.lost=true;await editor(page).getByRole('button',{name:'Check current activity',exact:true}).click();await expect(editor(page)).toContainText('Original reply was lost');
  f.card.execution.manual_observation_sources=[];await page.reload();await openDetail(page);
  await expect(editor(page).locator('select')).toBeDisabled();await expect(editor(page).locator('select option:checked')).toHaveText('Previously selected source is unavailable');
  await editor(page).getByRole('button',{name:'Check original request',exact:true}).click();expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
});

for(const reason of ['manual_native_identity_unavailable','manual_multiple_inputs_unsupported','manual_native_turn_active'])test(`${reason} stays unavailable without a manual completion claim`,async({page})=>{
  const f=runtimeFixture();f.card.execution.manual_observation_sources[0].available=false;f.card.execution.manual_observation_sources[0].reason=reason;
  await mountRuntime(page,false,f);await openDetail(page);await editor(page).locator('select').selectOption({index:1});
  await expect(editor(page).getByRole('button',{name:'Check current activity',exact:true})).toBeDisabled();
  await expect(editor(page)).not.toContainText('pm_kanban_');expect(f.state.writes).toEqual([]);
});

test('legacy board and absent action keep the existing read-only view',async({page})=>{
  const f=runtimeFixture();f.state.enabled=false;delete f.card.execution.attempts[0].runtime_completion;delete f.card.execution.manual_observation_sources;
  await mountRuntime(page,false,f);await openDetail(page);await expect(panel(page).locator('[data-pm-manual-observation]')).toHaveCount(0);
  await expect(panel(page).locator('[data-runtime-completion]')).toHaveCount(0);expect(f.state.writes).toEqual([]);
});

test('source withdrawal removes original completion refs, quality and manual controls; a reader cannot observe',async({page})=>{
  const f=runtimeFixture();f.base.state.role='reader';await mountRuntime(page,false,f);await openDetail(page);
  await expect(editor(page).getByRole('button',{name:'Check current activity',exact:true})).toBeHidden();
  await expect(editor(page)).toContainText('workspace writer role');f.board.state.failRead=403;
  await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(row(page)).toHaveCount(0);
  await expect(page.locator('[data-pm-kanban]')).not.toContainText(observerId);await expect(page.locator('[data-pm-manual-observation]')).toHaveCount(0);
});

test('mismatched execution completion fails closed instead of claiming another attempt completed',async({page})=>{
  const f=runtimeFixture();f.card.execution.attempts[0].runtime_completion.execution_operation_id='op_'+'9'.repeat(32);
  await mountRuntime(page,false,f);await expect(row(page)).toHaveCount(0);await expect(page.locator('[data-pm-kanban]')).toContainText('does not match this workspace');
});

for(const native of [false,true])test(`a late original receipt cannot restore withdrawn sources ${native?'Tauri':'Web'}`,async({page})=>{
  const f=runtimeFixture();await mountRuntime(page,native,f);await openDetail(page);await editor(page).locator('select').selectOption({label:'fixture-host / manual-session-1'});
  await editor(page).getByRole('button',{name:'Check current activity',exact:true}).click();await expect(editor(page)).toContainText(/Waiting externally|等待外部條件/);
  f.complete();let release=()=>{};f.state.deferReceipt=new Promise(resolve=>{release=resolve;});
  try{
    await editor(page).getByRole('button',{name:'Check original request',exact:true}).click();await expect.poll(()=>f.state.receiptReads).toBe(1);
    f.board.state.failRead=403;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(row(page)).toHaveCount(0);release();
    await expect(page.locator('[data-pm-kanban]')).not.toContainText('single input was quiescent');await expect(page.locator('[data-pm-kanban]')).not.toContainText(observerId);
    expect(f.state.writes).toHaveLength(1);
  }finally{release();}
});

test('actor identity change does not inherit another original source or key',async({page})=>{
  const f=runtimeFixture();await mountRuntime(page,false,f);await openDetail(page);await editor(page).locator('select').selectOption({label:'fixture-host / manual-session-1'});
  f.state.lost=true;await editor(page).getByRole('button',{name:'Check current activity',exact:true}).click();await expect(editor(page)).toContainText('Original reply was lost');
  f.base.state.actor='different-actor';f.base.state.principal='different-principal';await page.reload();await openDetail(page);
  await expect(editor(page).locator('select')).toHaveValue('');await expect(editor(page).locator('select')).toBeEnabled();
  await expect(editor(page).getByRole('button',{name:'Check original request',exact:true})).toHaveCount(0);expect(f.state.writes).toHaveLength(1);
});

for(const native of [false,true])test(`an authorized reader recovers the original receipt without new observation ${native?'Tauri':'Web'}`,async({page})=>{
  const f=runtimeFixture();await mountRuntime(page,native,f);await openDetail(page);await editor(page).locator('select').selectOption({label:'fixture-host / manual-session-1'});
  await editor(page).getByRole('button',{name:'Check current activity',exact:true}).click();await expect(editor(page)).toContainText(/Waiting externally|等待外部條件/);
  f.base.state.role='reader';f.base.state.manage=false;f.complete();await page.reload();await openDetail(page);
  await expect(editor(page).locator('select')).toBeDisabled();await editor(page).getByRole('button',{name:'Check original request',exact:true}).click();
  await expect(editor(page)).toContainText(/Succeeded|已成功/);await expect(editor(page).getByRole('button',{name:'Check current activity',exact:true})).toBeHidden();
  expect(f.state.receiptReads).toBeGreaterThan(0);expect(f.state.writes).toHaveLength(1);
});

test('an observe-scoped contributor can read a manual source without manage or operate authority',async({page})=>{
  const f=runtimeFixture();f.base.state.role='contributor';f.base.state.manage=false;await mountRuntime(page,false,f);await openDetail(page);
  await editor(page).locator('select').selectOption({label:'fixture-host / manual-session-1'});
  await editor(page).getByRole('button',{name:'Check current activity',exact:true}).click();await expect(editor(page)).toContainText(/Waiting externally|等待外部條件/);
  expect(f.state.writes).toHaveLength(1);expect(f.base.state.writes).toEqual([]);
});

for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`runtime detail layout ${locale} ${width}`,async({browser,baseURL},info)=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:width===390?844:900}}),page=await context.newPage();try{
    const f=runtimeFixture();await mountRuntime(page,false,f);
    const folder=process.env.PM_SCREENSHOTS||info.outputPath('screenshots');await mkdir(folder,{recursive:true});
    await page.screenshot({path:`${folder}/board-${locale}-${width}.png`,animations:'disabled'});
    await openDetail(page);await editor(page).locator('select').selectOption({label:'fixture-host / manual-session-1'});
    await expect(page.locator('.pm-main > .ui-page-header h1')).toHaveText(labels(locale).heading);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await expect(panel(page)).not.toContainText(/\bundefined\b|\bnull\b|pm_kanban_/);
    await editor(page).scrollIntoViewIfNeeded();await page.screenshot({path:`${folder}/runtime-${locale}-${width}.png`,animations:'disabled'});
    if(locale==='en'&&width===1440){await page.emulateMedia({colorScheme:'dark'});await page.screenshot({path:`${folder}/runtime-dark-${width}.png`,animations:'disabled'});}
  }finally{await context.close();}
});
