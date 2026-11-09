import {test, expect, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {route, workspaceId, workId} from './pm-workbench-fixture.ts';
import {fixture, linkId, fullText} from './work-history-fixture.ts';

function surfaceFixture() {
  const f = fixture(), work = f.base.board.state.cards[0].work;
  const state = {denyRecord:0, denyWorkspace:0, denySession:0, cursor:0, eventType:'work_item', holdRecord:null as Promise<void>|null,
    mutateCard:null as ((card:any)=>void)|null, mutateWorkspace:null as ((workspace:any)=>void)|null,
    record:{work_item:{...work, goal:'A clear reading flow', request:'Keep the selected scope', acceptance:'Read only', archived:false,
      completion:{pending:false,approved:false,display_state:'doing',fingerprint:'fixture'},steps:[],attachments:[]},
      project:{project_id:work.project_id,name:'Library',archived:false},path:[],children:[],derived:[],links:[] as any[],events:[]}};
  async function dispatch(input:any) {
    const url = new URL(input.path, 'http://fixture');
    if (url.pathname === '/work-items/'+workId(1)) {
      f.state.reads.push(input.path);
      const result = state.denyRecord ? {status:state.denyRecord,data:{error:{code:'FORBIDDEN',message:'Work unavailable'}}}
        : {status:200,data:structuredClone(state.record)};
      if (state.holdRecord) await state.holdRecord;
      return result;
    }
    if (url.pathname === '/sessions/demo/session-1' && state.denySession) return {status:state.denySession,data:{error:{code:'FORBIDDEN',message:'Session unavailable'}}};
    if (url.pathname === '/events') return {status:200,data:{events:Number(url.searchParams.get('after')) < state.cursor
      ? [{seq:state.cursor,resource_type:state.eventType,resource_id:state.eventType === 'work_item' ? workId(1) : state.eventType === 'session' ? 'demo/session-1' : workspaceId,kind:'updated'}] : [],
      next_cursor:state.cursor,has_more:false,head_cursor:state.cursor,sync:{checkpoint:{cursor:state.cursor,token:'pm-'+state.cursor}}}};
    const result = await f.dispatch(input);
    if (url.pathname === '/pm/workspaces/'+workspaceId) {
      if (state.denyWorkspace) return {status:state.denyWorkspace,data:{error:{code:'FORBIDDEN',message:'Workspace unavailable'}}};
      state.mutateWorkspace?.(result.data.workspace);
    }
    if (url.pathname === `/pm/workspaces/${workspaceId}/kanban/${workId(1)}` && result.data.card) state.mutateCard?.(result.data.card);
    return result;
  }
  return {...f,surface:state,dispatch};
}
const standalone = (page:Page) => page.locator('[data-work-item-history]');
const requirements = (page:Page) => page.locator('[data-pm-rtm]');
const panel = (page:Page) => page.locator('[data-work-history]:visible');
const excerpt = (page:Page) => panel(page).locator('[data-work-history-excerpt]');
const outer = (page:Page) => standalone(page).locator('[data-work-item-history-reader]');
const scope = (page:Page) => standalone(page).locator('[data-history-workspace]');
const noWrites = (f:ReturnType<typeof surfaceFixture>) => expect(f.state.writes).toEqual([]);
async function mountItem(page:Page,native:boolean,f:ReturnType<typeof surfaceFixture>,hint:string|null=workspaceId,open=true) {
  await mountConversation(page,native,f.dispatch);
  await page.goto(`/dashboard/#/item/${workId(1)}${hint === null ? '' : '?workspace='+hint}`);
  await expect(standalone(page)).toBeVisible();
  if (open) await outer(page).locator('summary').click();
}
async function read(page:Page) {
  await panel(page).locator(`[data-history-open="${linkId()}"]`).click();
  await expect(excerpt(page)).toContainText(fullText);
}
async function mountRequirements(page:Page,native:boolean,f:ReturnType<typeof surfaceFixture>,open=true) {
  await mountConversation(page,native,f.dispatch); await page.goto(`${route}/requirements?select=${workId(1)}`);
  await expect(requirements(page).locator('[data-work-history]')).toBeVisible();
  if (open) await panel(page).locator('[data-work-history-reader] > summary').click();
}

for (const native of [false,true]) test.describe(native ? 'IPC history surfaces' : 'HTTP history surfaces',()=>{
  test('requirement Work uses the shared lazy reader without adopting a requirement',async({page})=>{
    const f=surfaceFixture(); f.base.base.state.manage=false; f.base.base.state.role='reader';
    await mountRequirements(page,native,f,false);
    expect(f.state.reads.some(path=>path.includes('/external-links'))).toBe(false);
    await panel(page).locator('summary').first().click(); await read(page);
    await expect(excerpt(page)).toContainText('Execution status unknown');
    await requirements(page).locator('[data-rtm-select="membership.login"]').click();
    await expect(page.getByText(fullText,{exact:true})).toHaveCount(0);
    await expect(requirements(page).locator('[data-work-history]')).toBeHidden(); noWrites(f);
  });
  test('standalone Work reads only the selected workspace and rechecks it before publishing',async({page})=>{
    const f=surfaceFixture(); f.base.base.state.manage=false; f.base.base.state.role='reader';
    await mountItem(page,native,f,workspaceId,false);
    expect(f.state.reads.some(path=>path.includes('/external-links'))).toBe(false);
    await outer(page).locator('summary').click(); await read(page);
    expect(f.state.reads.filter(path=>path.endsWith('/kanban/'+workId(1))).length).toBeGreaterThanOrEqual(4);
    expect(f.state.reads.some(path=>/\/kanban\?/.test(path))).toBe(false);
    expect(f.state.linkReads).toBe(2); await expect(excerpt(page).locator('img')).toHaveCount(0);
    expect(await page.evaluate(()=>JSON.stringify({...localStorage}))).not.toContain(fullText); noWrites(f);
  });
  test('no workspace hint asks for a selection without guessing a binding',async({page})=>{
    const f=surfaceFixture(); await mountItem(page,native,f,null);
    await expect(scope(page)).toHaveValue('');
    expect(f.state.reads.some(path=>path.includes('/kanban/') || path.includes('/external-links'))).toBe(false);
    await scope(page).selectOption(workspaceId); await read(page); noWrites(f);
  });
  test('parent safety clears history while an unrelated Work draft stays focused',async({page})=>{
    const f=surfaceFixture(); await mountItem(page,native,f); await read(page);
    const draft=page.getByPlaceholder('New step'); await draft.fill('Keep this unfinished step');
    let release!:()=>void; f.surface.holdRecord=new Promise(resolve=>release=resolve);
    const before=f.state.reads.filter(path=>path==='/work-items/'+workId(1)).length; f.surface.cursor++;
    await expect.poll(()=>f.state.reads.filter(path=>path==='/work-items/'+workId(1)).length).toBeGreaterThan(before);
    await expect(page.getByText(fullText,{exact:true})).toHaveCount(0);
    await expect(draft).toHaveValue('Keep this unfinished step'); await expect(draft).toBeFocused();
    f.base.state.denyWork=true; f.surface.holdRecord=null; release();
    await expect(standalone(page)).toContainText('This history is unavailable');
    await expect(draft).toHaveValue('Keep this unfinished step'); noWrites(f);
  });
});

for (const surface of ['requirements','item']) for (const destination of ['close','other selection','navigation'])
  test(`${surface} discards a late excerpt after ${destination}`,async({page})=>{
    const f=surfaceFixture(); let release!:()=>void; f.state.holdExcerpt=new Promise(resolve=>release=resolve);
    if (surface==='requirements') await mountRequirements(page,false,f); else await mountItem(page,false,f);
    await panel(page).locator('[data-history-open]').click(); await expect.poll(()=>f.state.excerptReads).toBe(1);
    if (destination==='navigation') await page.goto(`${route}/overview`);
    else if (surface==='requirements') {
      if (destination==='close') await requirements(page).locator('[data-work-inspector]').getByRole('button',{name:'Close',exact:true}).click();
      else await requirements(page).locator('[data-rtm-select="membership.login"]').click();
    } else if (destination==='close') await outer(page).locator('summary').click();
    else await scope(page).selectOption('');
    release(); await page.waitForTimeout(150); await expect(page.getByText(fullText,{exact:true})).toHaveCount(0); noWrites(f);
  });

for (const status of [403,404,503]) test(`workspace refresh refusal ${status} removes a visible standalone excerpt`,async({page})=>{
  const f=surfaceFixture(); await mountItem(page,false,f); await read(page); f.surface.denyWorkspace=status;
  await panel(page).getByRole('button',{name:'Refresh relationships',exact:true}).click();
  await expect(standalone(page)).toContainText('This history is unavailable');
  await expect(page.getByText(fullText,{exact:true})).toHaveCount(0); noWrites(f);
});
for (const [name,mutate] of [
  ['Work',(card:any)=>{card.work.work_item_id=workId(2);}],
  ['project',(card:any)=>{card.binding.project_id='prj_'+'9'.repeat(20);}],
  ['workspace',(card:any)=>{card.binding.workspace_id='ws_'+'9'.repeat(32);}],
  ['binding',(card:any)=>{card.binding.binding_id='pmwb_'+'9'.repeat(32);}],
] as const) test(`changed ${name} during an excerpt read cannot publish plaintext`,async({page})=>{
  const f=surfaceFixture(); let release!:()=>void; f.state.holdExcerpt=new Promise(resolve=>release=resolve);
  await mountItem(page,false,f); await panel(page).locator('[data-history-open]').click(); await expect.poll(()=>f.state.excerptReads).toBe(1);
  f.surface.mutateCard=mutate; release(); await expect(standalone(page)).toContainText('This history is unavailable');
  await expect(page.getByText(fullText,{exact:true})).toHaveCount(0); noWrites(f);
});
for (const kind of ['server','membership']) test(`changed workspace ${kind} refuses a stored excerpt selection`,async({page})=>{
  const f=surfaceFixture(); await mountItem(page,false,f); await read(page);
  f.surface.mutateWorkspace=workspace=>{if(kind==='server')workspace.server_id='another-central';else workspace.membership.role='removed';};
  await panel(page).getByRole('button',{name:'Refresh relationships',exact:true}).click();
  await expect(standalone(page)).toContainText('This history is unavailable'); await expect(page.getByText(fullText,{exact:true})).toHaveCount(0); noWrites(f);
});
test('invalid explicit workspace does not fall back to another accessible workspace',async({page})=>{
  const f=surfaceFixture(); await mountItem(page,false,f,'ws_'+'9'.repeat(32));
  await expect(standalone(page)).toContainText('This history is unavailable');
  expect(f.state.reads.some(path=>path.includes('/kanban/') || path.includes('/external-links'))).toBe(false);
  await scope(page).selectOption(workspaceId); await read(page); noWrites(f);
});
test('last workspace is revalidated as a hint after leaving the Board',async({page})=>{
  const f=surfaceFixture(); await mountConversation(page,false,f.dispatch); await page.goto(`${route}/board`);
  await page.locator(`[data-board-open="${workId(1)}"]`).click();
  await page.getByRole('link',{name:'Open work details',exact:true}).click();
  await expect(standalone(page)).toBeVisible(); await outer(page).locator('summary').click(); await read(page); noWrites(f);
});
test('periodic read rechecks the binding and withdraws removed workspace access',async({page})=>{
  await page.clock.install(); const f=surfaceFixture(); await mountItem(page,false,f); await read(page);
  f.surface.denyWorkspace=403; await page.clock.fastForward(60000);
  await expect(standalone(page)).toContainText('This history is unavailable'); await expect(page.getByText(fullText,{exact:true})).toHaveCount(0); noWrites(f);
});
test('workspace events revoke plaintext even while the Work draft has focus',async({page})=>{
  const f=surfaceFixture(); await mountItem(page,false,f); await read(page);
  const draft=page.getByPlaceholder('New step'); await draft.fill('Preserve this draft');
  f.state.deny=404; f.surface.eventType='pm_workspace'; f.surface.cursor++;
  await expect(standalone(page)).toContainText('This history is unavailable'); await expect(draft).toHaveValue('Preserve this draft');
  await expect(draft).toBeFocused(); await expect(page.getByText(fullText,{exact:true})).toHaveCount(0); noWrites(f);
});
test('requirements refresh fences old excerpt while Work proof is pending',async({page})=>{
  const f=surfaceFixture(); await mountRequirements(page,false,f); await read(page);
  let release!:()=>void; f.base.state.heldDetail=new Promise(resolve=>release=resolve);
  await page.locator('[data-pm-refresh]').click(); await expect(page.getByText(fullText,{exact:true})).toHaveCount(0);
  f.state.links=[]; release(); await expect(panel(page)).toContainText('This Work has no visible external conversation relationships'); noWrites(f);
});
for (const surface of ['requirements','item']) test(`older capability hides ${surface} history`,async({page})=>{
  const f=surfaceFixture(); f.state.supported=false; await mountConversation(page,false,f.dispatch);
  await page.goto(surface==='item'?`/dashboard/#/item/${workId(1)}?workspace=${workspaceId}`:`${route}/requirements?select=${workId(1)}`);
  await expect(page.locator('#main h1')).toBeVisible(); await expect(page.locator('[data-work-history]')).toBeHidden(); await expect(standalone(page)).toBeHidden();
  expect(f.state.reads.some(path=>path.includes('/external-links'))).toBe(false); noWrites(f);
});
for (const surface of ['requirements','item']) for (const locale of ['en','zh-TW']) for (const width of [390,1440])
  test(`${surface} ${locale} excerpt keyboard reading at ${width}px`,async({page})=>{
    await page.setViewportSize({width,height:1000}); await page.addInitScript(locale=>localStorage.setItem('batc.lang',locale),locale);
    const f=surfaceFixture(); if(surface==='item')await mountItem(page,false,f);else await mountRequirements(page,false,f);
    const open=panel(page).locator('[data-history-open]'); await open.focus(); await page.keyboard.press('Enter'); await expect(excerpt(page)).toContainText(fullText);
    await expect(excerpt(page)).toContainText(locale==='en'?'Execution status unknown':'執行狀態未知');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if(surface==='item' && width===390) {
      expect((await scope(page).boundingBox())?.height).toBeGreaterThanOrEqual(44);
      expect((await outer(page).locator('summary').first().boundingBox())?.height).toBeGreaterThanOrEqual(44);
    }
    await excerpt(page).scrollIntoViewIfNeeded();
    if(process.env.BATC_SCREENSHOT_DIR){await mkdir(process.env.BATC_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:`${process.env.BATC_SCREENSHOT_DIR}/history-${surface}-${locale}-${width}.png`});}
    noWrites(f);
  });

for (const native of [false,true]) for (const workbench of [false,true]) test(`linked session Work reads the same excerpts without action authority ${native ? 'IPC' : 'HTTP'} ${workbench ? 'workbench' : 'classic'}`,async({page})=>{
  const f=surfaceFixture(); f.surface.record.links=[{kind:'session',ref:'demo/session-1',target:{found:true,host:'demo',session_id:'session-1'}}];
  await mountConversation(page,native,f.dispatch); await page.goto(`/dashboard/${workbench ? '?workbench=1' : ''}#/item/${workId(1)}?workspace=${workspaceId}`);
  await expect(page.locator('[data-session-view]')).toBeVisible();
  if (workbench) await page.getByRole('button',{name:'Results',exact:true}).click();
  await expect(standalone(page)).toBeVisible(); await outer(page).locator('summary').click(); await read(page);
  await expect(page.locator('[data-follow-up-input]')).toHaveCount(0); noWrites(f);
});
for (const status of [403,404,503]) test(`parent Work denial ${status} withdraws history without discarding its step draft`,async({page})=>{
  const f=surfaceFixture(); await mountItem(page,false,f); await read(page);
  const draft=page.getByPlaceholder('New step'); await draft.fill('Keep the pending step');
  f.surface.denyRecord=status; f.surface.cursor++;
  await expect(standalone(page)).toBeHidden(); await expect(page.getByText(fullText,{exact:true})).toHaveCount(0);
  await expect(draft).toHaveValue('Keep the pending step'); noWrites(f);
});
test('late initial workspace proof cannot reopen a closed reader',async({page})=>{
  const f=surfaceFixture(); let release!:()=>void; f.base.state.heldDetail=new Promise(resolve=>release=resolve);
  await mountItem(page,false,f);
  await expect.poll(()=>f.state.reads.some(path=>path.endsWith('/kanban/'+workId(1)))).toBe(true);
  await outer(page).locator('summary').click(); f.base.state.heldDetail=null; release(); await page.waitForTimeout(150);
  expect(f.state.reads.some(path=>path.includes('/external-links'))).toBe(false);
  await expect(standalone(page).locator('[data-work-history]')).toHaveCount(0);
  await outer(page).locator('summary').click(); await read(page); noWrites(f);
});
test('requirement Work switch cannot publish the previous selected excerpt',async({page})=>{
  const f=surfaceFixture(); let release!:()=>void; f.state.holdExcerpt=new Promise(resolve=>release=resolve);
  await mountRequirements(page,false,f); await panel(page).locator('[data-history-open]').click(); await expect.poll(()=>f.state.excerptReads).toBe(1);
  await requirements(page).locator(`[data-rtm-select="${workId(2)}"]`).click();
  release(); await page.waitForTimeout(150); await expect(page.getByText(fullText,{exact:true})).toHaveCount(0);
  await panel(page).locator('[data-work-history-reader] > summary').click();
  await expect(panel(page).locator('[data-history-open]')).toHaveAttribute('data-history-open',linkId(2)); noWrites(f);
});

test('an older successful Work refresh cannot undo a newer parent denial',async({page})=>{
  const f=surfaceFixture(), dispatch=f.dispatch;
  f.dispatch=async input=>{
    if (input.method==='POST') {
      f.state.writes.push(input);
      expect(input.body.action).toBe('work_item.update');
      return {status:200,data:{operation:{operation_id:'op_'+'8'.repeat(32),status:'succeeded',...input.body}}};
    }
    const result=await dispatch(input);
    if (input.path==='/bootstrap' || input.path==='/capabilities') {
      const caps=input.path==='/bootstrap'?result.data.capabilities:result.data;
      caps.actions.push({action:'work_item.update',allowed:true});
    }
    return result;
  };
  await mountItem(page,false,f); await read(page);
  let release!:()=>void; f.surface.holdRecord=new Promise(resolve=>release=resolve);
  const before=f.state.reads.filter(path=>path==='/work-items/'+workId(1)).length;
  await page.getByPlaceholder('New step').fill('An explicitly requested step');
  await page.getByRole('button',{name:'Add',exact:true}).click();
  await expect.poll(()=>f.state.reads.filter(path=>path==='/work-items/'+workId(1)).length).toBeGreaterThan(before);
  f.surface.holdRecord=null; f.surface.denyRecord=403; f.surface.cursor++;
  await expect(standalone(page)).toBeHidden();
  const historyReads=f.state.reads.filter(path=>path.includes('/external-links')).length;
  release(); await page.waitForTimeout(150);
  await expect(standalone(page)).toBeHidden(); await expect(page.getByText(fullText,{exact:true})).toHaveCount(0);
  expect(f.state.reads.filter(path=>path.includes('/external-links')).length).toBe(historyReads);
  expect(f.state.writes).toHaveLength(1);
});

for (const native of [false,true]) for (const workbench of [false,true])
  test(`closing linked Results fences a pending excerpt ${native ? 'IPC' : 'HTTP'} ${workbench ? 'workbench' : 'classic'}`,async({page})=>{
    await page.setViewportSize({width:workbench?1440:390,height:1000});
    const f=surfaceFixture(); f.surface.record.links=[{kind:'session',ref:'demo/session-1',target:{found:true,host:'demo',session_id:'session-1'}}];
    let release!:()=>void; f.state.holdExcerpt=new Promise(resolve=>release=resolve);
    await mountConversation(page,native,f.dispatch); await page.goto(`/dashboard/${workbench?'?workbench=1':''}#/item/${workId(1)}?workspace=${workspaceId}`);
    await page.getByRole('button',{name:'Results',exact:true}).click();
    await outer(page).locator('summary').click(); await panel(page).locator('[data-history-open]').click();
    await expect.poll(()=>f.state.excerptReads).toBe(1);
    const inspector=page.locator('.ui-inspector').filter({has:standalone(page)});
    if (workbench) await page.keyboard.press('Escape');
    else await inspector.locator(':scope > header').getByRole('button',{name:'Back',exact:true}).click();
    await expect(inspector).toBeHidden();
    f.state.holdExcerpt=null; release(); await page.waitForTimeout(150);
    await expect(page.getByText(fullText,{exact:true})).toHaveCount(0);
    await page.getByRole('button',{name:'Results',exact:true}).click();
    await expect(outer(page)).toHaveJSProperty('open',false);
    await outer(page).locator('summary').click(); await read(page); noWrites(f);
  });
for (const workbench of [false,true]) test(`session withdrawal retires the linked history ${workbench?'workbench':'classic'}`,async({page})=>{
  const f=surfaceFixture(); f.surface.record.links=[{kind:'session',ref:'demo/session-1',target:{found:true,host:'demo',session_id:'session-1'}}];
  await mountConversation(page,false,f.dispatch); await page.goto(`/dashboard/${workbench?'?workbench=1':''}#/item/${workId(1)}?workspace=${workspaceId}`);
  if(workbench)await page.getByRole('button',{name:'Results',exact:true}).click();
  await outer(page).locator('summary').click(); await read(page);
  f.surface.denySession=403; f.surface.eventType='session'; f.surface.cursor++;
  await expect(page.locator('[data-session-withdrawn]')).toBeVisible();
  await expect(page.getByText(fullText,{exact:true})).toHaveCount(0); noWrites(f);
});
test('a linked Results resize clears plaintext and cancels its history timer',async({page})=>{
  await page.clock.install(); await page.setViewportSize({width:1440,height:1000});
  const f=surfaceFixture(); f.surface.record.links=[{kind:'session',ref:'demo/session-1',target:{found:true,host:'demo',session_id:'session-1'}}];
  await mountItem(page,false,f); await read(page); const before=f.state.reads.filter(path=>path.includes('/external-links')).length;
  await page.setViewportSize({width:390,height:1000}); await expect(page.getByText(fullText,{exact:true})).toHaveCount(0);
  await page.clock.fastForward(60000);
  expect(f.state.reads.filter(path=>path.includes('/external-links')).length).toBe(before);
  await page.getByRole('button',{name:'Results',exact:true}).click(); await expect(outer(page)).toHaveJSProperty('open',false);
  await outer(page).locator('summary').click(); await read(page); noWrites(f);
});
