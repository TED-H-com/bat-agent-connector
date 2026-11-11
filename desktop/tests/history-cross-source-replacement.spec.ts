import {test,expect,type Page} from '@playwright/test';
import {maintenanceFixture} from './history-maintenance-fixture.ts';
import {open,select,reopen,relations} from './external-history-links-fixture.ts';
import {workspaceId,workId} from './pm-workbench-fixture.ts';

const oldId='hxl_'+'8'.repeat(32);
const panel=(page:Page)=>page.locator('[data-history-replacement]');
const submit=(page:Page)=>relations(page).getByRole('button',{name:'Link excerpt to Work',exact:true});
function crossFixture(source='other-source') {
  const f=maintenanceFixture();
  const old={...f.link(),link_id:oldId,version:3,
    fixed_ref:{...structuredClone(f.ref),source_id:source,entry_id:'shi_'+'8'.repeat(32)}};
  f.state.links.push(old);
  return Object.assign(f,{old});
}
async function lookup(page:Page,id=oldId) {
  if(await panel(page).getAttribute('open')===null) await panel(page).locator('summary').click();
  await panel(page).getByRole('textbox',{name:'Relationship ID',exact:true}).fill(id);
  await expect(submit(page)).toBeDisabled();
  await panel(page).getByRole('button',{name:'Read relationship',exact:true}).click();
}
async function restored(page:Page) {
  await page.reload(); await reopen(page);
  await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click();
}
for(const native of [false,true]) for(const source of ['example','other-source']) {
  test(`explicit replacement from another entry ${source} ${native}`,async({page})=>{
    const f=crossFixture(source);await open(page,native,f);await select(page,workId(2));await lookup(page);
    await expect(panel(page).locator('[data-history-replacement-detail]')).toContainText(`Source: ${source}`);
    await expect(panel(page).locator('[data-history-replacement-detail]')).toContainText('version 3');
    await expect(submit(page)).toBeEnabled();expect(f.state.writes).toEqual([]);
    await submit(page).click();await expect(relations(page)).toContainText('Excerpt linked to Work.');
    expect(f.state.writes).toHaveLength(1);
    expect(f.state.writes[0].body).toMatchObject({action:'pm.history.link',target:{workspace_id:workspaceId,work_item_id:workId(2)},
      params:{fixed_ref:f.ref,replaces_link_id:oldId,expected_old_link_version:3}});
    expect(f.maintenance.events).toEqual(['replaced','created']);
    expect(f.maintenance.retired.get(oldId).fixed_ref).toEqual(f.old.fixed_ref);
  });
}
for(const native of [false,true]) test(`cross-source lost reply recovers the immutable request ${native}`,async({page})=>{
  const f=crossFixture();await open(page,native,f);await select(page,workId(2));await lookup(page);await expect(submit(page)).toBeEnabled();
  f.state.lost=true;await submit(page).click();await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();
  const original=structuredClone(f.state.writes[0]);await restored(page);
  await expect(panel(page).locator('[data-api-error]')).toBeVisible();
  await expect(panel(page).getByRole('textbox')).toBeDisabled();
  await relations(page).getByRole('button',{name:'Check original request'}).click();
  await expect(relations(page)).toContainText('Excerpt linked to Work.');
  expect(f.state.writes[1]).toEqual(original);expect(f.state.ops.size).toBe(1);
  expect(f.maintenance.events).toEqual(['replaced','created']);
});
test('cross-source draft restores only the selected ID and original version',async({page})=>{
  const f=crossFixture();await open(page,false,f);await select(page,workId(2));await lookup(page);await expect(submit(page)).toBeEnabled();
  const values=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('.pm.history.link.')).map(([,value])=>JSON.parse(value)));
  expect(values).toHaveLength(1);expect(JSON.parse(values[0].values.replacement)).toEqual({link_id:oldId,version:3});
  expect(JSON.stringify(values)).not.toContain('other-source');
  await restored(page);await expect(submit(page)).toBeEnabled();
  await expect(panel(page).locator('[data-history-replacement-detail]')).toContainText('other-source');expect(f.state.writes).toEqual([]);
});
for(const invalid of ['hxl_bad','../external-links/x','hxl_'+'A'.repeat(32)]) test(`invalid relationship hint makes no read: ${invalid}`,async({page})=>{
  const f=crossFixture();await open(page,false,f);await select(page,workId(2));const before=f.state.reads.filter(path=>path.includes('/external-links/')).length;
  await lookup(page,invalid);await expect(panel(page).getByRole('alert')).toContainText('Enter a complete relationship ID');
  await expect(submit(page)).toBeDisabled();expect(f.state.reads.filter(path=>path.includes('/external-links/'))).toHaveLength(before);
  await panel(page).getByRole('textbox').fill('');await expect(submit(page)).toBeEnabled();expect(f.state.writes).toEqual([]);
});
for(const invalid of ['server','workspace','source','entry','reference','creator','withdrawn','denied']) test(`cross-source lookup refuses ${invalid}`,async({page})=>{
  const f=crossFixture();await open(page,false,f);await select(page,workId(2));
  f.maintenance.linkDenied=invalid==='denied';f.maintenance.linkTransform=data=>{
    if(invalid==='server') data.link.fixed_ref.server_id='another-server';
    if(invalid==='workspace') data.link.workspace_id='ws_'+'9'.repeat(32);
    if(invalid==='source') data.link.fixed_ref.source_id='../another-source';
    if(invalid==='entry') data.link.fixed_ref.entry_id='another-entry';
    if(invalid==='reference') data.link.fixed_ref.schema='unsupported';
    if(invalid==='creator') data.link.created_by='another-reader';
    if(invalid==='withdrawn') data.link.state='withdrawn';
  };
  await lookup(page);await expect(panel(page).locator('[data-api-error]')).toBeVisible();await expect(submit(page)).toBeDisabled();
  await expect(panel(page).locator('[data-history-replacement-detail]')).toBeEmpty();expect(f.state.writes).toEqual([]);
});
test('editing a verified ID retires its proof until explicit reread',async({page})=>{
  const f=crossFixture();await open(page,false,f);await select(page,workId(2));await lookup(page);await expect(submit(page)).toBeEnabled();
  await panel(page).getByRole('textbox').fill('hxl_'+'9'.repeat(32));
  await expect(submit(page)).toBeDisabled();await expect(panel(page).locator('[data-history-replacement-detail]')).toBeEmpty();
  await panel(page).getByRole('combobox').selectOption('');await expect(submit(page)).toBeEnabled();
  await submit(page).click();await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params).not.toHaveProperty('replaces_link_id');expect(f.maintenance.retired.has(oldId)).toBe(false);
});
test('late cross-source proof cannot return after changing Work',async({page})=>{
  const f=crossFixture();await open(page,false,f);await select(page,workId(2));let release!:()=>void;
  f.maintenance.holdLink=new Promise(resolve=>release=resolve);await lookup(page);
  await expect.poll(()=>f.state.reads.some(path=>path.endsWith(oldId))).toBe(true);
  await relations(page).getByRole('combobox',{name:'Existing Work',exact:true}).selectOption(workId(1));await expect(submit(page)).toBeEnabled();
  release();await expect(panel(page).locator('[data-history-replacement-detail]')).toBeEmpty();
  await submit(page).click();await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.params).not.toHaveProperty('replaces_link_id');
});
for(const locale of ['en','zh-TW']) for(const width of [390,1440]) test(`cross-source controls ${locale} ${width}`,async({page})=>{
  const f=crossFixture();await page.setViewportSize({width,height:900});await page.addInitScript(locale=>localStorage.setItem('batc.lang',locale),locale);
  await open(page,false,f);await relations(page).getByRole('button',{name:locale==='en'?'Choose a Work':'選擇工作',exact:true}).click();
  await relations(page).getByRole('combobox').selectOption(workId(2));await panel(page).locator('summary').click();
  await panel(page).getByRole('textbox').fill(oldId);await panel(page).getByRole('button',{name:locale==='en'?'Read relationship':'讀取關聯',exact:true}).click();
  await expect(panel(page).locator('[data-history-replacement-detail]')).toContainText('other-source');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  if(width===390) expect((await panel(page).getByRole('button',{name:locale==='en'?'Read relationship':'讀取關聯',exact:true}).boundingBox())!.height).toBeGreaterThanOrEqual(44);
});
