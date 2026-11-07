import {test, expect, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {workbenchFixture, route, workspaceId, workId} from './pm-workbench-fixture.ts';
import {workStartFixture} from './work-start-fixture.ts';
import {workResultFixture} from './work-result-fixture.ts';

const linkId = (n=1) => 'hxl_'+String(n).repeat(32);
const fullText = 'Review 🦇 the keyboard fix.\n<img src=x onerror=alert(1)> remains plain text.';
function fixture(base = workbenchFixture()) {
  const makeLink = (n=1, work=1):any => ({link_id:linkId(n), workspace_id:workspaceId, work_item_id:workId(work), attempt_id:null,
    excerpt_id:'hxe_'+String(n).repeat(64), purpose:'reference', state:'active', version:1, created_by:'reader', created_at:'2026-11-07T10:00:00Z',
    fixed_ref:{schema:'external_history_ref/v1', server_id:'pm-fixture', workspace_id:workspaceId, source_id:'example', entry_id:'shi_'+String(n).repeat(32),
      source_digest:'a'.repeat(64), content_digest:'b'.repeat(64), excerpt_digest:'c'.repeat(64), selection:{kind:'entry'}}});
  const state = {supported:true, deny:0, hiddenFirst:false, nonAdvancing:false, pageSize:20, links:[makeLink(),makeLink(2,2)],
    reads:[] as string[], writes:[] as any[], linkReads:0, excerptReads:0, finalWithdraw:false,
    holdList:null as Promise<void>|null, holdExcerpt:null as Promise<void>|null,
    mutateExcerpt:null as ((data:any)=>void)|null, mutateDetail:null as ((data:any)=>void)|null};
  async function dispatch(input:any):Promise<any> {
    const url = new URL(input.path,'http://fixture'), path = url.pathname, endpoint = `/pm/workspaces/${workspaceId}`;
    if (input.method === 'GET') state.reads.push(input.path); else state.writes.push(input);
    const unavailable = {status:state.deny || 404,data:{error:{code:'PM_HISTORY_SOURCE_UNAVAILABLE',message:'History unavailable'}}};
    if (path.endsWith('/external-links')) {
      const work = path.split('/').at(-2), after = Number(url.searchParams.get('after') || 0);
      const matches = state.links.filter(row=>row.work_item_id === work);
      const links = state.hiddenFirst && after === 0 ? [] : matches.slice(state.hiddenFirst ? 0 : after, (state.hiddenFirst ? 0 : after)+state.pageSize);
      const next = state.nonAdvancing ? after : state.hiddenFirst && after === 0 ? 5 : after+links.length < matches.length ? after+links.length : null;
      const result = state.deny ? unavailable : {status:200,data:{links:structuredClone(links),next_after:next}};
      if (state.holdList) await state.holdList;
      return result;
    }
    if (path.startsWith(endpoint+'/external-links/')) {
      state.linkReads++;
      const link = structuredClone(state.links.find(row=>path.endsWith(row.link_id)));
      if (state.finalWithdraw && state.linkReads % 2 === 0) link.state = 'withdrawn';
      const data = {link}; state.mutateDetail?.(data);
      return state.deny || !link ? unavailable : {status:200,data};
    }
    if (path.startsWith(endpoint+'/history-excerpts/')) {
      state.excerptReads++;
      const link = state.links.find(row=>path.endsWith(row.excerpt_id));
      const data:any = {excerpt_id:link?.excerpt_id,ref:structuredClone(link?.fixed_ref),history_complete:false,execution_status:'unknown',
        content:{role:'assistant',parts:link?.fixed_ref.selection.kind === 'text' ? [{kind:'text',text:'🦇'}]
          : [{kind:'text',text:fullText},{kind:'tool_request',name:'shell'},{kind:'tool_result',name:null}],clipped:true,omitted_parts:2}};
      state.mutateExcerpt?.(data);
      const result = state.deny || !link ? unavailable : {status:200,data};
      if (state.holdExcerpt) await state.holdExcerpt;
      return result;
    }
    const result = await base.dispatch(input);
    if (path === '/bootstrap' || path === '/capabilities') {
      const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.external_history = {version:1,...(state.supported ? {relationships:{version:1,refs:['external_history_ref/v1'],derive_ready:false}} : {})};
    }
    return result;
  }
  return {base,state,makeLink,dispatch};
}
const panel = (page:Page) => page.locator('[data-work-history]');
const reader = (page:Page) => panel(page).locator('[data-work-history-reader]');
const excerpt = (page:Page) => panel(page).locator('[data-work-history-excerpt]');
async function mount(page:Page, native:boolean, f:ReturnType<typeof fixture>, open=true) {
  await mountConversation(page,native,f.dispatch); await page.goto(`${route}/board`);
  await page.locator(`[data-board-open="${workId(1)}"]`).click();
  await expect(panel(page)).toBeVisible();
  if (open) {await reader(page).locator('summary').click(); await expect(panel(page).locator('[data-history-open]')).toHaveCount(1);}
}
const refresh = (page:Page) => panel(page).getByRole('button',{name:'Refresh relationships',exact:true}).click();
const read = (page:Page, n=1) => panel(page).locator(`[data-history-open="${linkId(n)}"]`).click();
const noWrites = (f:ReturnType<typeof fixture>) => expect(f.state.writes).toEqual([]);

for (const native of [false,true]) test.describe(native ? 'IPC Work history' : 'HTTP Work history', ()=>{
  test('lazy fixed excerpts recheck the relationship and render literal text with honest coverage', async({page})=>{
    const f=fixture(); f.base.base.state.manage=false; f.base.base.state.role='reader';
    await mount(page,native,f,false);
    expect(f.state.reads.filter(path=>path.includes('/external-links'))).toHaveLength(0);
    await reader(page).locator('summary').click(); await read(page);
    await expect(excerpt(page)).toContainText(fullText);
    await expect(excerpt(page)).toContainText('Partial history');
    await expect(excerpt(page)).toContainText('Execution status unknown');
    await expect(excerpt(page)).toContainText('Tool requested: shell');
    await expect(excerpt(page)).toContainText('2 parts omitted');
    await expect(excerpt(page).locator('img')).toHaveCount(0);
    expect(f.state.linkReads).toBe(2); expect(f.state.excerptReads).toBe(1);
    expect(await page.evaluate(()=>JSON.stringify({...localStorage}))).not.toContain(fullText);
    noWrites(f);
  });
  test('selected text and optional Attempt remain explicit source metadata', async({page})=>{
    const f=fixture(), link=f.state.links[0]; link.attempt_id='att_'+'3'.repeat(32);
    link.fixed_ref.selection={kind:'text',part_index:0,start_codepoint:7,end_codepoint:8};
    await mount(page,native,f); await expect(panel(page)).toContainText('Linked to a specific attempt'); await read(page);
    await expect(excerpt(page)).toContainText('Selected text'); await expect(excerpt(page).locator('.exh-text')).toHaveText('🦇');
    await expect(excerpt(page)).not.toContainText(fullText); noWrites(f);
  });
  test('hidden pages advance and loading more rechecks earlier relationships', async({page})=>{
    const f=fixture(); f.state.hiddenFirst=true;
    await mount(page,native,f,false); await reader(page).locator('summary').click();
    await expect(panel(page)).toContainText('No visible relationships on this page');
    await panel(page).getByRole('button',{name:'Read more relationships',exact:true}).click();
    await read(page); await expect(excerpt(page)).toContainText(fullText);
    expect(f.state.reads.some(path=>path.endsWith('/external-links?after=5&limit=20'))).toBe(true);
    f.state.links=[]; await refresh(page);
    await expect(panel(page).locator('[data-history-open]')).toHaveCount(0);
    await expect(excerpt(page)).not.toContainText(fullText); noWrites(f);
  });
  test('source withdrawal during excerpt read fails the final relationship check', async({page})=>{
    const f=fixture(); let release!:()=>void; f.state.holdExcerpt=new Promise(resolve=>release=resolve);
    await mount(page,native,f); await read(page); await expect.poll(()=>f.state.excerptReads).toBe(1);
    f.state.deny=404; release(); await expect(panel(page)).toContainText('This history is unavailable');
    await expect(excerpt(page)).not.toContainText(fullText); await expect(panel(page).locator('[data-history-open]')).toHaveCount(0);
    f.state.deny=0; f.state.holdExcerpt=null; await refresh(page); await read(page); await expect(excerpt(page)).toContainText(fullText); noWrites(f);
  });
  test('withdrawn relationship cannot publish an otherwise readable excerpt', async({page})=>{
    const f=fixture(); f.state.finalWithdraw=true; await mount(page,native,f); await read(page);
    await expect(panel(page)).toContainText('This history is unavailable'); await expect(excerpt(page)).not.toContainText(fullText); noWrites(f);
  });
});

for (const status of [403,404,503]) test(`failed refresh ${status} removes all previously visible text`, async({page})=>{
  const f=fixture(); await mount(page,false,f); await read(page); await expect(excerpt(page)).toContainText(fullText);
  f.state.deny=status; await refresh(page); await expect(panel(page)).toContainText('This history is unavailable');
  await expect(excerpt(page)).not.toContainText(fullText); noWrites(f);
});

for (const [name, change] of [
  ['workspace',(link:any)=>{link.workspace_id='ws_'+'9'.repeat(32);}],
  ['Work',(link:any)=>{link.work_item_id=workId(9);}],
  ['server',(link:any)=>{link.fixed_ref.server_id='another-fixture';}],
  ['reference digest',(link:any)=>{link.fixed_ref.excerpt_digest='changed';}],
  ['selection',(link:any)=>{link.fixed_ref.selection={kind:'text',part_index:0,start_codepoint:2,end_codepoint:1};}],
] as const) test(`mismatched ${name} on link detail refuses the excerpt`, async({page})=>{
  const f=fixture(); f.state.mutateDetail=data=>change(data.link); await mount(page,false,f); await read(page);
  await expect(panel(page)).toContainText('This history is unavailable'); expect(f.state.excerptReads).toBe(0); noWrites(f);
});

for (const [name, change] of [
  ['source',(data:any)=>{data.ref.source_id='other';}],
  ['selection',(data:any)=>{data.ref.selection={kind:'text',part_index:0,start_codepoint:0,end_codepoint:1};}],
  ['excerpt ID',(data:any)=>{data.excerpt_id='hxe_'+'9'.repeat(64);}],
  ['coverage',(data:any)=>{data.history_complete=true;}],
  ['execution',(data:any)=>{data.execution_status='succeeded';}],
  ['content',(data:any)=>{data.content.parts=[{kind:'text',text:'x'.repeat(16001)}];}],
] as const) test(`mismatched excerpt ${name} never reaches the page`, async({page})=>{
  const f=fixture(); f.state.mutateExcerpt=change; await mount(page,false,f); await read(page);
  await expect(panel(page)).toContainText('This history is unavailable'); await expect(excerpt(page)).not.toContainText(fullText); noWrites(f);
});

test('non-advancing pagination is refused', async({page})=>{
  const f=fixture(); f.state.nonAdvancing=true; await mount(page,false,f,false); await reader(page).locator('summary').click();
  await expect(panel(page)).toContainText('This history is unavailable'); noWrites(f);
});
test('older capability keeps the reader hidden', async({page})=>{
  const f=fixture(); f.state.supported=false; await mountConversation(page,false,f.dispatch); await page.goto(`${route}/board`);
  await page.locator(`[data-board-open="${workId(1)}"]`).click(); await expect(panel(page)).toBeHidden();
  expect(f.state.reads.filter(path=>path.includes('/external-links'))).toHaveLength(0); noWrites(f);
});
for (const destination of ['close reader','close inspector','another Work','another view']) test(`late excerpt after ${destination} stays hidden`, async({page})=>{
  const f=fixture(); let release!:()=>void; f.state.holdExcerpt=new Promise(resolve=>release=resolve);
  await mount(page,false,f); await read(page); await expect.poll(()=>f.state.excerptReads).toBe(1);
  if (destination==='close reader') await reader(page).locator('summary').click();
  else if (destination==='close inspector') await page.locator('[data-work-inspector]').getByRole('button',{name:'Close',exact:true}).click();
  else if (destination==='another Work') await page.locator(`[data-board-open="${workId(2)}"]`).click();
  else await page.goto(`${route}/overview`);
  release(); await page.waitForTimeout(150); await expect(page.getByText(fullText,{exact:true})).toHaveCount(0);
  noWrites(f);
});
test('late list cannot replace another selected Work', async({page})=>{
  const f=fixture(); let release!:()=>void; f.state.holdList=new Promise(resolve=>release=resolve);
  await mount(page,false,f,false); await reader(page).locator('summary').click();
  await expect.poll(()=>f.state.reads.some(path=>path.includes('/external-links?'))).toBe(true);
  await page.locator(`[data-board-open="${workId(2)}"]`).click(); f.state.holdList=null; release();
  await reader(page).locator('summary').click(); await expect(panel(page).locator('[data-history-open]')).toHaveCount(1);
  await expect(panel(page).locator('[data-history-open]')).toHaveAttribute('data-history-open',linkId(2)); noWrites(f);
});
test('board access withdrawal clears the excerpt without another history read', async({page})=>{
  const f=fixture(); await mount(page,false,f); await read(page); await expect(excerpt(page)).toContainText(fullText);
  f.base.state.denyWork=true; await page.locator('[data-pm-refresh]').click();
  await expect(page.getByText(fullText,{exact:true})).toHaveCount(0); noWrites(f);
});
test('periodic current-grant read removes a relationship that is no longer visible', async({page})=>{
  await page.clock.install();
  const f=fixture(); await mount(page,false,f); await read(page); await expect(excerpt(page)).toContainText(fullText);
  f.state.links=[]; await page.clock.fastForward(60000);
  await expect(panel(page)).toContainText('This Work has no visible external conversation relationships');
  await expect(page.getByText(fullText,{exact:true})).toHaveCount(0); noWrites(f);
});
test('refresh fences an old excerpt while the current Work read is pending', async({page})=>{
  const f=fixture(); let release!:()=>void; f.state.holdExcerpt=new Promise(resolve=>release=resolve);
  await mount(page,false,f); await read(page); await expect.poll(()=>f.state.excerptReads).toBe(1);
  let finish!:()=>void; f.base.state.heldDetail=new Promise(resolve=>finish=resolve);
  await page.locator('[data-pm-refresh]').click();
  await expect.poll(()=>f.base.state.reads.filter(path=>path.endsWith('/kanban/'+workId(1))).length).toBeGreaterThan(1);
  release(); await page.waitForTimeout(150); await expect(page.getByText(fullText,{exact:true})).toHaveCount(0);
  f.state.links=[]; finish(); await expect(panel(page)).toContainText('This Work has no visible external conversation relationships'); noWrites(f);
});
test('bounded opaque source IDs and reordered reference fields retain their identity', async({page})=>{
  const f=fixture(); f.state.links[0].fixed_ref.source_id='example:'+ 's'.repeat(120);
  f.state.mutateExcerpt=data=>{data.ref=Object.fromEntries(Object.entries(data.ref).reverse());};
  await mount(page,false,f); await read(page); await expect(excerpt(page)).toContainText(fullText); noWrites(f);
});
test('reading references preserves the existing Work-start instruction draft', async({page})=>{
  const start=workStartFixture(), f=fixture(start); await mount(page,false,f);
  const instruction=page.locator('[data-work-start-instruction]');
  await instruction.fill('Keep the user’s chosen implementation scope.');
  await instruction.evaluate(node=>node.setAttribute('data-preserved-draft','true'));
  await read(page); await expect(excerpt(page)).toContainText(fullText); await refresh(page);
  await expect(excerpt(page)).toContainText(fullText);
  await expect(instruction).toHaveAttribute('data-preserved-draft','true');
  await expect(instruction).toHaveValue('Keep the user’s chosen implementation scope.');
  expect(start.state.previews).toEqual([]); expect(start.state.writes).toEqual([]); noWrites(f);
});
test('fixed history stays separate from the existing Attempt result', async({page})=>{
  const result=workResultFixture(), f=fixture(result); await mount(page,false,f);
  const attempt=page.locator('[data-work-section="attempt-result"]');
  await expect(attempt).toContainText('Implemented login flow and validated credentials handling.');
  await read(page); await expect(excerpt(page)).toContainText(fullText);
  await expect(attempt).toContainText('Implemented login flow and validated credentials handling.');
  await expect(attempt).not.toContainText(fullText);
  await expect(page.locator('[data-work-section="result"]')).toBeHidden();
  expect(result.state.writes).toEqual([]); noWrites(f);
});
for (const locale of ['en','zh-TW']) for (const width of [390,1440]) test(`${locale} Work history at ${width}px`, async({page})=>{
  await page.setViewportSize({width,height:1000}); await page.addInitScript(locale=>localStorage.setItem('batc.lang',locale),locale);
  const f=fixture(); await mount(page,false,f); await read(page); await expect(excerpt(page)).toContainText(fullText);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(panel(page)).toContainText(locale==='en'?'Execution status unknown':'執行狀態未知');
  await excerpt(page).scrollIntoViewIfNeeded();
  if (process.env.BATC_SCREENSHOT_DIR) {await mkdir(process.env.BATC_SCREENSHOT_DIR,{recursive:true}); await page.screenshot({path:`${process.env.BATC_SCREENSHOT_DIR}/work-history-${locale}-${width}.png`});}
  noWrites(f);
});
