import {test, expect, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {fixture, fullText} from './work-history-fixture.ts';
import {maintenanceFixture} from './history-maintenance-fixture.ts';
import {route, workspaceId, workId} from './pm-workbench-fixture.ts';

function rememberFixture() {
  const maintenance=maintenanceFixture(), f=fixture(maintenance.base);
  f.state.links=maintenance.state.links;
  const state={advertised:true, allowed:true, hold:null as Promise<void>|null};
  async function dispatch(input:any) {
    const path=new URL(input.path,'http://fixture').pathname;
    if(path.includes('/history') && !path.includes('/history-excerpts') || path.startsWith('/operations')) return maintenance.dispatch(input);
    const result=await f.dispatch(input);
    if(path==='/bootstrap'||path==='/capabilities') {
      const caps=path==='/bootstrap'?result.data.capabilities:result.data;
      caps.features.external_history.relationships.actions=state.advertised?['pm.history.unlink']:[];
      caps.actions.push({action:'pm.history.unlink',allowed:state.allowed});
    }
    if(path.includes('/external-links/') && state.hold) await state.hold;
    return result;
  }
  return {...f,maintenance,remember:state,dispatch};
}
const panel=(page:Page)=>page.locator('[data-work-history]');
const save=(page:Page)=>panel(page).locator('[data-history-remember]');
const saved=(page:Page)=>page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('.history-known.')));
async function open(page:Page,native:boolean,f:ReturnType<typeof rememberFixture>) {
  await mountConversation(page,native,f.dispatch); await page.goto(`${route}/board`);
  await page.locator(`[data-board-open="${workId(1)}"]`).click();
  await panel(page).locator('summary').first().click();
  await panel(page).locator('[data-history-open]').click();
  await expect(panel(page)).toContainText(fullText);
}
for(const native of [false,true]) {
  test(`explicit save feeds revoked-source cleanup and original-key recovery ${native}`,async({page})=>{
    const f=rememberFixture(); await open(page,native,f);
    expect(await saved(page)).toEqual([]);
    const reads=f.state.linkReads; await save(page).click();
    await expect(panel(page)).toContainText('Relationship ID saved.'); expect(f.state.linkReads).toBe(reads+1);
    const entries=await saved(page); expect(entries).toHaveLength(1);
    const stored=JSON.parse(entries[0][1]).records[0];
    expect(Object.keys(stored).sort()).toEqual(['workspace_id','link_id','work_item_id','version','created_by','source_id','entry_id'].sort());
    expect(stored).toMatchObject({workspace_id:workspaceId,work_item_id:workId(1),version:1});
    expect(JSON.stringify(entries)).not.toContain(fullText); expect(JSON.stringify(entries)).not.toContain('digest');
    expect(f.maintenance.state.writes).toEqual([]); expect(f.state.writes).toEqual([]);
    f.maintenance.state.deny=true; f.maintenance.maintenance.hiddenSource=true;
    await page.goto(`${route}/agents`);
    const cleanup=page.locator('[data-history-cleanup]'); await cleanup.locator('summary').click();
    await expect(cleanup.locator('[data-history-cleanup-link]')).toHaveCount(1);
    f.maintenance.state.lost=true;
    const before=f.maintenance.state.reads.length;
    await cleanup.getByRole('button',{name:'Withdraw relationship',exact:true}).click();
    await expect(cleanup.getByRole('button',{name:'Check original request'})).toBeVisible();
    const original=structuredClone(f.maintenance.state.writes[0]);
    expect(f.maintenance.state.reads.slice(before).some(path=>path.includes('/history')||path.includes('/external-links'))).toBe(false);
    await page.reload(); await cleanup.locator('summary').click();
    await cleanup.getByRole('button',{name:'Check original request'}).click();
    await expect(cleanup).toContainText('Relationship withdrawn.');
    expect(f.maintenance.state.writes[1]).toEqual(original);
    expect(f.maintenance.maintenance.events).toEqual(['withdrawn']); expect(await saved(page)).toEqual([]);
  });
}
for(const denied of ['observe only','other creator','unadvertised','action refused']) test(`save is unavailable for ${denied}`,async({page})=>{
  const f=rememberFixture();
  if(denied==='observe only') f.base.base.state.manage=false;
  if(denied==='other creator') f.state.links[0].created_by='someone-else';
  if(denied==='unadvertised') f.remember.advertised=false;
  if(denied==='action refused') f.remember.allowed=false;
  await open(page,false,f); await expect(save(page)).toHaveCount(0); expect(await saved(page)).toEqual([]);
});
test('owner may remember another creator',async({page})=>{
  const f=rememberFixture(); f.base.base.state.role='owner'; f.state.links[0].created_by='someone-else';
  await open(page,false,f); await save(page).click(); await expect(panel(page)).toContainText('Relationship ID saved.');
});
for(const changed of ['version','creator','source','withdrawn','denied']) test(`save reread refuses ${changed}`,async({page})=>{
  const f=rememberFixture(); await open(page,false,f);
  if(changed==='denied') f.state.deny=404;
  else f.state.mutateDetail=data=>{
    if(changed==='version') data.link.version++;
    if(changed==='creator') data.link.created_by='someone-else';
    if(changed==='source') data.link.fixed_ref.source_id='another';
    if(changed==='withdrawn') data.link.state='withdrawn';
  };
  await save(page).click(); await expect(panel(page)).not.toContainText(fullText); expect(await saved(page)).toEqual([]);
});
for(const destination of ['close','another Work','another view']) test(`late save after ${destination} cannot write storage`,async({page})=>{
  const f=rememberFixture(); await open(page,false,f); let release!:()=>void;
  f.remember.hold=new Promise(resolve=>release=resolve); const count=f.state.linkReads;
  await save(page).click(); await expect.poll(()=>f.state.linkReads).toBe(count+1);
  if(destination==='close') await panel(page).locator('summary').first().click();
  else if(destination==='another Work') await page.locator(`[data-board-open="${workId(2)}"]`).click();
  else await page.goto(`${route}/overview`);
  release(); await page.waitForTimeout(150); expect(await saved(page)).toEqual([]);
});
test('storage failure is explicit and retry preserves the readable excerpt',async({page})=>{
  const f=rememberFixture(); await open(page,false,f);
  await page.evaluate(()=>{const original=Storage.prototype.setItem; (window as any).restoreSave=()=>{Storage.prototype.setItem=original;};
    Storage.prototype.setItem=function(key,value){if(key.includes('.history-known.')) throw Error('fixture quota'); return original.call(this,key,value);};});
  await save(page).click(); await expect(panel(page)).toContainText('could not be saved'); await expect(panel(page)).toContainText(fullText);
  expect(await saved(page)).toEqual([]); await page.evaluate(()=>(window as any).restoreSave());
  await save(page).click(); await expect(panel(page)).toContainText('Relationship ID saved.');
});
test('Traditional Chinese save control is shared at phone width',async({page})=>{
  const f=rememberFixture(); await page.setViewportSize({width:390,height:844});
  await page.addInitScript(()=>localStorage.setItem('batc.lang','zh-TW'));
  await open(page,false,f); await expect(save(page)).toHaveText('保存關聯 ID 供日後撤回');
  await save(page).click(); await expect(panel(page)).toContainText('已保存關聯 ID，沒有保存對話正文。');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'test-results/work-history-remember-zh.png',fullPage:true});
});
for(const broken of ['full','damaged']) test(`a ${broken} index is preserved and reported without discarding pending intents`,async({page})=>{
  const f=rememberFixture(); await open(page,false,f); await save(page).click(); await expect(panel(page)).toContainText('Relationship ID saved.');
  const [[key,value]]=await saved(page);
  const initial=broken==='damaged'?'invalid JSON':JSON.stringify({version:1,records:Array.from({length:500},(_,n)=>({...JSON.parse(value).records[0],link_id:'hxl_'+(n+100).toString(16).padStart(32,'0')}))});
  await page.evaluate(({key,initial})=>{localStorage.setItem(key,initial);localStorage.setItem('batc.pm.fixture.original-intent','unchanged');},{key,initial});
  await panel(page).getByRole('button',{name:'Refresh relationships',exact:true}).click(); await expect(save(page)).toBeEnabled(); await save(page).click();
  await expect(panel(page)).toContainText(broken==='full'?'500':'could not be saved');
  expect(await page.evaluate(key=>localStorage.getItem(key),key)).toBe(initial);
  expect(await page.evaluate(()=>localStorage.getItem('batc.pm.fixture.original-intent'))).toBe('unchanged');
});
test('account change uses a separate recovery namespace',async({page})=>{
  const f=rememberFixture(); await open(page,false,f); await save(page).click(); await expect(panel(page)).toContainText('Relationship ID saved.');
  const [[key,value]]=await saved(page); f.base.base.state.actor='another-owner'; f.base.base.state.principal='person-2'; f.base.base.state.role='owner';
  await page.reload(); await page.locator(`[data-board-open="${workId(1)}"]`).click();
  await panel(page).locator('summary').first().click(); await panel(page).locator('[data-history-open]').click();
  await save(page).click(); await expect(panel(page)).toContainText('Relationship ID saved.');
  const entries=await saved(page); expect(entries).toHaveLength(2); expect(entries.find(([k])=>k===key)?.[1]).toBe(value);
});

test('reader with manage scope can remember their own relationship for cleanup',async({page})=>{
  const f=rememberFixture(); f.base.base.state.role='reader'; await open(page,false,f);
  await save(page).click(); await expect(panel(page)).toContainText('Relationship ID saved.');
});
