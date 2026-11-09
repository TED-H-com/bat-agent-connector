import {test,expect,type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {maintenanceFixture,attemptId} from './history-maintenance-fixture.ts';
import {open,select,reopen,relations,detail,linkId} from './external-history-links-fixture.ts';
import {workspaceId,workId} from './pm-workbench-fixture.ts';

const replacement=(page:Page)=>page.locator('[data-history-replacement]');
const cleanup=(page:Page)=>page.locator('[data-history-cleanup]');
const submit=(page:Page)=>relations(page).getByRole('button',{name:'Link excerpt to Work',exact:true});
async function chooseReplacement(page:Page,id=linkId) {
  await replacement(page).locator('summary').click(); await replacement(page).getByRole('combobox').selectOption(id);
  await expect(replacement(page).locator('[data-history-replacement-detail]')).toContainText(id); await expect(submit(page)).toBeEnabled();
}
async function savedList(page:Page) {
  if(await detail(page).isVisible()) await detail(page).getByRole('button',{name:'Close',exact:true}).click();
  if(await cleanup(page).getAttribute('open')===null) await cleanup(page).locator('summary').click();
}
async function reloadDraft(page:Page) {
  await page.reload(); await reopen(page); await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click();
}
for(const native of [false,true]) {
  test(`replacement uses one link operation with original version, and returns to create mode ${native}`,async({page})=>{
    const f=maintenanceFixture(); await open(page,native,f); await select(page,workId(2)); await chooseReplacement(page);
    expect(f.state.writes).toEqual([]); await submit(page).click(); await expect(relations(page)).toContainText('Excerpt linked to Work.');
    expect(f.state.writes).toHaveLength(1); expect(f.state.writes[0].body).toMatchObject({action:'pm.history.link',target:{workspace_id:workspaceId,work_item_id:workId(2)},
      params:{fixed_ref:f.ref,replaces_link_id:linkId,expected_old_link_version:1}});
    expect(f.maintenance.events).toEqual(['replaced','created']); await expect(replacement(page).getByRole('combobox')).toHaveValue('');
    await expect(submit(page)).toBeEnabled(); await savedList(page);
    await expect(cleanup(page).locator(`[data-history-cleanup-link="${linkId}"]`)).toHaveCount(0);
    await expect(cleanup(page).locator('[data-history-cleanup-link]')).toHaveCount(1);
  });
  test(`lost replacement reply restores the original operation after the old relationship disappears ${native}`,async({page})=>{
    const f=maintenanceFixture(); await open(page,native,f); await select(page,workId(2)); await chooseReplacement(page); f.state.lost=true;
    await submit(page).click(); await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();
    const original=structuredClone(f.state.writes[0]); await reloadDraft(page);
    await expect(replacement(page).locator('[data-api-error]')).toBeVisible(); await expect(replacement(page).getByRole('combobox')).toBeDisabled();
    await relations(page).getByRole('button',{name:'Check original request'}).click(); await expect(submit(page)).toBeEnabled();
    expect(f.state.writes[1]).toEqual(original); expect(f.state.ops.size).toBe(1); expect(f.maintenance.events).toEqual(['replaced','created']);
  });
  test(`known IDs permit explicit withdrawal after source revocation, without source reads ${native}`,async({page})=>{
    const f=maintenanceFixture(); await open(page,native,f); await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
    f.state.deny=true; f.maintenance.hiddenSource=true; await page.reload(); await savedList(page);
    await expect(cleanup(page).locator('[data-history-cleanup-link]')).toHaveCount(1);
    const reads=f.state.reads.length; expect(f.state.writes).toEqual([]);
    await cleanup(page).getByRole('button',{name:'Withdraw relationship',exact:true}).click();
    await expect(cleanup(page)).toContainText('Relationship withdrawn.');
    expect(f.state.writes[0].body).toEqual({action:'pm.history.unlink',target:{workspace_id:workspaceId,work_item_id:workId(1),link_id:linkId},params:{},preconditions:{expected_link_version:1}});
    expect(f.state.reads.slice(reads).some(path=>path.includes('/history') || path.includes('/external-links/'))).toBe(false);
    await cleanup(page).getByRole('button',{name:'Refresh saved list'}).click(); await expect(cleanup(page)).toContainText('No saved relationships');
  });
  test(`revoked-source lost withdrawal reply retains the original key across reload ${native}`,async({page})=>{
    const f=maintenanceFixture(); await open(page,native,f); await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
    f.state.deny=true; f.maintenance.hiddenSource=true; await page.reload(); await savedList(page); f.state.lost=true;
    await cleanup(page).getByRole('button',{name:'Withdraw relationship',exact:true}).click();
    await expect(cleanup(page).getByRole('button',{name:'Check original request'})).toBeVisible(); const original=structuredClone(f.state.writes[0]);
    await page.reload(); await savedList(page); await cleanup(page).getByRole('button',{name:'Check original request'}).click();
    await expect(cleanup(page)).toContainText('Relationship withdrawn.'); expect(f.state.writes[1]).toEqual(original); expect(f.maintenance.events).toEqual(['withdrawn']);
  });
}
test('replacement draft reloads exact old version and metadata without storing titles',async({page})=>{
  const f=maintenanceFixture(); await open(page,false,f); await select(page,workId(2)); await chooseReplacement(page); await reloadDraft(page);
  await expect(replacement(page).locator('[data-history-replacement-detail]')).toContainText(linkId); await expect(submit(page)).toBeEnabled();
  const saved=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('batc.pm.')).map(([,value])=>value).join('\n'));
  expect(saved).not.toContain('Member login'); expect(saved).not.toContain('keyboard fix'); expect(saved).not.toContain('🦇'); expect(f.state.writes).toEqual([]);
});
test('replacement combines a fixed text range and explicit attempt without execution',async({page})=>{
  const f=maintenanceFixture();f.maintenance.attempts=true; await open(page,false,f); await select(page,workId(2)); await chooseReplacement(page);
  const attempts=page.locator('[data-history-attempt-picker]'); await attempts.locator('summary').click(); await attempts.getByRole('combobox').selectOption(attemptId);
  await expect(attempts.locator('[data-history-attempt-detail]')).toContainText(attemptId);
  const range=page.locator('[data-history-text-picker]'); await range.locator('summary').click();
  await range.locator('textarea').evaluate((node:HTMLTextAreaElement)=>node.setSelectionRange(7,9)); await range.getByRole('button',{name:'Preview selected text'}).click();
  await expect(page.locator('[data-history-preview] .exh-text')).toHaveText('🦇'); await submit(page).click();
  await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params).toMatchObject({attempt_id:attemptId,replaces_link_id:linkId,expected_old_link_version:1,fixed_ref:{selection:{kind:'text',part_index:0,start_codepoint:7,end_codepoint:8}}});
  expect(f.state.writes[0].body.action).toBe('pm.history.link');
});
for(const bad of ['id','workspace','reference','version','withdrawn','creator','denied','work-detail']) test(`replacement refuses stale or mismatched old relationship: ${bad}`,async({page})=>{
  const f=maintenanceFixture(); await open(page,false,f); await select(page,workId(2));
  f.maintenance.linkDenied=bad==='denied'; f.maintenance.linkTransform=data=>{
    if(bad==='id') data.link.link_id='hxl_'+'9'.repeat(32);
    if(bad==='workspace') data.link.workspace_id='ws_'+'9'.repeat(32);
    if(bad==='reference') data.link.fixed_ref.entry_id='shi_'+'9'.repeat(32);
    if(bad==='version') data.link.version++;
    if(bad==='withdrawn') data.link.state='withdrawn';
    if(bad==='creator') data.link.created_by='another-reader';
    if(bad==='work-detail') data.link.work_item_id='wi_'+'9'.repeat(20);
  };
  await replacement(page).locator('summary').click(); await replacement(page).getByRole('combobox').selectOption(linkId);
  await expect(replacement(page).locator('[data-api-error]')).toBeVisible(); await expect(submit(page)).toBeDisabled();
  await expect(replacement(page).locator('[data-history-replacement-detail]')).toBeEmpty();
  await replacement(page).getByRole('combobox').selectOption(''); await expect(submit(page)).toBeEnabled(); expect(f.state.writes).toEqual([]);
});
test('failed replacement needs explicit new request and reread before using a new version',async({page})=>{
  const f=maintenanceFixture(); await open(page,false,f); await select(page,workId(2)); await chooseReplacement(page);
  f.state.links[0].version=2; await submit(page).click(); await expect(relations(page).getByRole('button',{name:'New request',exact:true})).toBeVisible();
  await expect(replacement(page).getByRole('button',{name:'Reread selected relationship',exact:true})).toBeDisabled();
  await expect(replacement(page).getByRole('button',{name:'Read relationship',exact:true})).toBeDisabled();
  await relations(page).getByRole('button',{name:'New request',exact:true}).click();
  await replacement(page).getByRole('button',{name:'Reread selected relationship'}).click();
  await expect(replacement(page).locator('[data-history-replacement-detail]')).toContainText('version 2'); await submit(page).click();
  await expect(relations(page)).toContainText('Excerpt linked to Work.'); expect(f.state.writes[1].body.params.expected_old_link_version).toBe(2);
  expect(f.state.writes[1].idempotency_key).not.toBe(f.state.writes[0].idempotency_key);
});
test('late replacement read cannot retarget a different selected Work',async({page})=>{
  const f=maintenanceFixture(); await open(page,false,f); await select(page,workId(2)); let release!:()=>void;
  f.maintenance.holdLink=new Promise(resolve=>release=resolve); await replacement(page).locator('summary').click();
  await replacement(page).getByRole('combobox').selectOption(linkId); await expect(submit(page)).toBeDisabled();
  await relations(page).getByRole('combobox',{name:'Existing Work',exact:true}).selectOption(workId(1)); await expect(submit(page)).toBeEnabled();
  release(); await expect(replacement(page).locator('[data-history-replacement-detail]')).toBeEmpty(); await submit(page).click();
  await expect.poll(()=>f.state.writes.length).toBe(1); expect(f.state.writes[0].body.params.replaces_link_id).toBeUndefined();
});
test('receipt success during replacement restoration clears the retired choice',async({page})=>{
  const f=maintenanceFixture(); await open(page,false,f); await select(page,workId(2)); await chooseReplacement(page); f.maintenance.pending=true;
  await submit(page).click(); await expect.poll(()=>f.state.ops.size).toBe(1); f.maintenance.pending=false;
  let release!:()=>void; f.maintenance.holdLink=new Promise(resolve=>release=resolve); await reloadDraft(page);
  await expect(relations(page)).toContainText('Excerpt linked to Work.'); release();
  await expect(replacement(page).getByRole('combobox')).toHaveValue(''); await expect(submit(page)).toBeEnabled(); expect(f.state.writes).toHaveLength(1);
});
test('legacy pending withdrawal is recoverable with its exact workspace intent after grant loss',async({page})=>{
  const f=maintenanceFixture(); await open(page,false,f); await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
  f.state.lost=true; await relations(page).getByRole('button',{name:'Withdraw relationship',exact:true}).click();
  await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();
  await page.evaluate(()=>{for(const key of Object.keys(localStorage)) if(key.includes('.history-known.')) localStorage.removeItem(key);});
  f.state.deny=true; f.maintenance.hiddenSource=true; await page.reload(); await savedList(page);
  await cleanup(page).getByRole('button',{name:'Check original request'}).click(); await expect(cleanup(page)).toContainText('Relationship withdrawn.');
  expect(f.state.writes[1]).toEqual(f.state.writes[0]); expect(f.state.ops.size).toBe(1);
});
test('legacy index alone cannot invent a withdrawal when there is no original intent',async({page})=>{
  const f=maintenanceFixture(); await open(page,false,f); await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
  f.state.stale=true; await relations(page).getByRole('button',{name:'Withdraw relationship',exact:true}).click();
  await expect(relations(page).getByRole('button',{name:'New request',exact:true})).toBeVisible();
  await page.evaluate(()=>{for(const key of Object.keys(localStorage)) if(key.includes('.history-known.') || key.includes('.pm.history.unlink.')) localStorage.removeItem(key);});
  f.state.deny=true; f.maintenance.hiddenSource=true; await page.reload(); await savedList(page);
  await expect(cleanup(page)).toContainText('No saved relationships'); expect(f.state.writes).toHaveLength(1);
});
for(const mode of ['other-creator','reader-creator','owner','no-manage','no-member','old-capability']) test(`cleanup follows current role and action authority: ${mode}`,async({page})=>{
  const f=maintenanceFixture();
  if(mode==='owner') {f.base.base.state.role='owner';f.state.links[0].created_by='another-reader';}
  await open(page,false,f); await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
  if(mode==='other-creator') f.base.base.state.actor='someone-else';
  if(mode==='reader-creator') f.base.base.state.role='reader';
  if(mode==='no-manage') f.base.base.state.manage=false;
  if(mode==='no-member') {
    const dispatch=f.dispatch; f.dispatch=async input=>{const result=await dispatch(input);if(input.path===`/pm/workspaces/${workspaceId}`) result.data.workspace.membership=null;return result;};
  }
  if(mode==='old-capability') f.state.supported=false;
  f.state.deny=true; f.maintenance.hiddenSource=true; await page.reload();
  if(['no-manage','no-member','old-capability'].includes(mode)) await expect(cleanup(page)).toBeHidden();
  else {await savedList(page);await expect(cleanup(page).getByRole('button',{name:'Withdraw relationship',exact:true})).toHaveCount(mode==='other-creator'?0:1);}
  expect(f.state.writes).toEqual([]);
});
test('damaged known metadata fails closed and does not erase pending original requests',async({page})=>{
  const f=maintenanceFixture(); await open(page,false,f); await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
  f.state.lost=true; await relations(page).getByRole('button',{name:'Withdraw relationship',exact:true}).click();
  await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();
  await page.evaluate(()=>{for(const key of Object.keys(localStorage)) if(key.includes('.history-known.')) localStorage.setItem(key,'broken');});
  f.state.deny=true; f.maintenance.hiddenSource=true; await page.reload(); await savedList(page);
  await expect(cleanup(page)).toContainText('Saved relationship metadata could not be read');
  expect(await page.evaluate(()=>Object.entries(localStorage).some(([key,value])=>key.includes('.pm.history.unlink.') && Boolean(JSON.parse(value).intent)))).toBe(true);
  expect(f.state.writes).toHaveLength(1);
});
for(const action of ['replacement','cleanup']) for(const field of ['link_id','state','version']) test(`invalid ${action} receipt ${field} retains the original intent and recovery record`,async({page})=>{
  const f=maintenanceFixture(),dispatch=f.dispatch; let corrupt=true;
  f.dispatch=async input=>{
    const result=structuredClone(await dispatch(input));
    if(corrupt && result.data.operation?.status==='succeeded') result.data.operation.result[field]=field==='version'?0:'invalid';
    return result;
  };
  await open(page,false,f); await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
  if(action==='replacement') {await select(page,workId(2));await chooseReplacement(page);await submit(page).click();}
  else {await savedList(page);await cleanup(page).getByRole('button',{name:'Withdraw relationship',exact:true}).click();}
  const region=action==='replacement'?relations(page):cleanup(page);
  await expect(region.locator('[data-api-error]')).toBeVisible(); await expect(region.getByRole('button',{name:'Check original request'})).toBeVisible();
  const stored=await page.evaluate(()=>Object.entries(localStorage));
  expect(stored.some(([key,value])=>key.includes('.history-known.') && JSON.parse(value).records.some(row=>row.link_id==='hxl_'+'b'.repeat(32)))).toBe(true);
  expect(stored.some(([key,value])=>key.includes('.pm.history.') && Boolean(JSON.parse(value).intent))).toBe(true);
  corrupt=false; await region.getByRole('button',{name:'Check original request'}).click();
  await expect(region).toContainText(action==='replacement'?'Excerpt linked to Work.':'Relationship withdrawn.');
  expect(f.state.writes).toHaveLength(1); expect(f.state.ops.size).toBe(1);
});
test('revoked-source cleanup keeps the known version on conflict and never retries automatically',async({page})=>{
  const f=maintenanceFixture();await open(page,false,f);await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
  f.state.links[0].version=2;f.state.deny=true;f.maintenance.hiddenSource=true;await page.reload();await savedList(page);
  await cleanup(page).getByRole('button',{name:'Withdraw relationship',exact:true}).click();
  await expect(cleanup(page).getByRole('button',{name:'New request',exact:true})).toBeVisible();
  await cleanup(page).getByRole('button',{name:'Refresh saved list'}).click();
  await expect(cleanup(page).getByRole('button',{name:'New request',exact:true})).toBeVisible();
  expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].body.preconditions.expected_link_version).toBe(1);expect(f.maintenance.events).toEqual([]);
});
test('saved relationships page locally and ignore a different workspace index',async({page})=>{
  const f=maintenanceFixture();await open(page,false,f);await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
  await page.evaluate(()=>{
    const [key,raw]=Object.entries(localStorage).find(([key])=>key.includes('.history-known.'))!;
    const saved=JSON.parse(raw),row=saved.records[0];saved.records=Array.from({length:21},(_,n)=>({...row,link_id:'hxl_'+n.toString(16).padStart(32,'0')}));
    localStorage.setItem(key,JSON.stringify(saved));localStorage.setItem(key.replace(row.workspace_id,'ws_'+'9'.repeat(32)),'broken');
  });
  f.state.deny=true;f.maintenance.hiddenSource=true;await page.reload();await savedList(page);
  await expect(cleanup(page).locator('[data-history-cleanup-link]')).toHaveCount(20);
  await cleanup(page).getByRole('button',{name:'Show more saved relationships'}).click();
  await expect(cleanup(page).locator('[data-history-cleanup-link]')).toHaveCount(21);expect(f.state.writes).toEqual([]);
});
test('older Work-only intent cannot acquire replacement metadata from a newer draft',async({page})=>{
  const f=maintenanceFixture();await open(page,false,f);await select(page,workId(2));f.state.lost=true;await submit(page).click();
  await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();const original=structuredClone(f.state.writes[0]);
  await page.evaluate(id=>{for(const [key,raw] of Object.entries(localStorage)) if(key.includes('.pm.history.link.')) {const saved=JSON.parse(raw);saved.values.replacement=JSON.stringify({link_id:id,version:1});localStorage.setItem(key,JSON.stringify(saved));}},linkId);
  await reloadDraft(page);await replacement(page).locator('summary').click();await expect(replacement(page).getByRole('combobox')).toHaveValue('');
  await relations(page).getByRole('button',{name:'Check original request'}).click();await expect(submit(page)).toBeEnabled();
  expect(f.state.writes[1]).toEqual(original);expect(f.maintenance.events).toEqual(['created']);
});
for(const locale of ['en','zh-TW']) for(const width of [390,1440]) test(`maintenance layout ${locale} ${width}`,async({page})=>{
  const f=maintenanceFixture(); await page.setViewportSize({width,height:900}); await page.addInitScript(locale=>localStorage.setItem('batc.lang',locale),locale);
  await open(page,false,f); await relations(page).getByRole('button',{name:locale==='en'?'Choose a Work':'選擇工作',exact:true}).click();
  await relations(page).getByRole('combobox').selectOption(workId(2)); await replacement(page).locator('summary').focus(); await page.keyboard.press('Enter');
  await replacement(page).getByRole('combobox').selectOption(linkId); await expect(replacement(page).locator('[data-history-replacement-detail]')).toContainText(linkId);
  await replacement(page).scrollIntoViewIfNeeded(); expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const dir=process.env.BATC_SCREENSHOT_DIR;
  if(dir){await mkdir(dir,{recursive:true});await page.screenshot({path:`${dir}/replacement-${locale}-${width}.png`,fullPage:true});}
  await detail(page).getByRole('button',{name:width===390?(locale==='en'?'Back':'返回'):(locale==='en'?'Close':'關閉'),exact:true}).click(); await cleanup(page).locator('summary').click();
  await expect(cleanup(page).locator('[data-history-cleanup-link]')).toHaveCount(1); await cleanup(page).scrollIntoViewIfNeeded();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  if(width===390) expect((await cleanup(page).getByRole('button',{name:locale==='en'?'Withdraw relationship':'撤回關聯',exact:true}).boundingBox())!.height).toBeGreaterThanOrEqual(44);
  if(dir) await page.screenshot({path:`${dir}/cleanup-${locale}-${width}.png`,fullPage:true});
  expect(f.state.writes).toEqual([]);
});
