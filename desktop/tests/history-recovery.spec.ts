import {test,expect,type Page} from '@playwright/test';
import {STRINGS} from '../src/i18n.js';
import {maintenanceFixture} from './history-maintenance-fixture.ts';
import {open,relations,detail,reopen,linkId} from './external-history-links-fixture.ts';
import {workspaceId,workId} from './pm-workbench-fixture.ts';

const cleanup=(page:Page)=>page.locator('[data-history-cleanup]');
async function savedList(page:Page,locale='en') {
  if(await detail(page).isVisible()) await detail(page).getByRole('button',{name:locale==='en'?'Back':'返回',exact:true}).click();
  if(await cleanup(page).getAttribute('open')===null) await cleanup(page).locator('summary').click();
}
// Simulate a staged discovery intent from an earlier client session, without
// introducing a discovery control or using source IDs in its durable record.
async function seed(page:Page) {
  return page.evaluate(({workspaceId,workId,linkId})=>{
    const known=Object.keys(localStorage).find(key=>key.includes('.history-known.'))!;
    const prefix=known.slice(0,known.indexOf('.history-known.'));
    const target={workspace_id:workspaceId,work_item_id:workId,link_id:linkId};
    const key=`${prefix}.history-cleanup.${workspaceId}.pm.history.unlink.${JSON.stringify(target)}`;
    const saved={version:1,values:{},intent:{key:'synthetic-original-withdrawal',request:{action:'pm.history.unlink',target,params:{},preconditions:{expected_link_version:1}}}};
    localStorage.removeItem(known);localStorage.setItem(key,JSON.stringify(saved));
    return {key,saved};
  },{workspaceId,workId:workId(1),linkId});
}
for(const native of [false,true]) for(const locale of ['en','zh-TW'] as const) test(`source-independent original survives lost reply and account return ${native} ${locale}`,async({page})=>{
  await page.setViewportSize({width:390,height:900});await page.addInitScript(locale=>localStorage.setItem('batc.lang',locale),locale);
  const f=maintenanceFixture();await open(page,native,f);await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
  const original=await seed(page);const actor=f.base.base.state.actor,principal=f.base.base.state.principal;
  // The fixture advertises the older relationship capability, without discovery.
  f.state.deny=true;f.maintenance.hiddenSource=true;await page.reload();await savedList(page,locale);
  const recover=cleanup(page).getByRole('button',{name:STRINGS[locale].pm_check_original,exact:true});
  await expect(recover).toBeEnabled();expect(f.state.writes).toHaveLength(0);
  const reads=f.state.reads.length;f.state.lost=true;await recover.click();await expect(cleanup(page).locator('[data-api-error]')).toBeVisible();await expect(recover).toBeEnabled();
  expect(f.state.writes[0].body).toEqual(original.saved.intent.request);expect(f.state.writes[0].idempotency_key).toBe(original.saved.intent.key);
  expect(f.state.reads.slice(reads)).toEqual([]);expect(f.state.links).toHaveLength(0);
  f.base.base.state.actor='another-owner';f.base.base.state.principal='person-2';f.base.base.state.role='owner';
  await page.reload();await savedList(page,locale);await expect(cleanup(page).locator('[data-history-cleanup-link]')).toHaveCount(0);
  expect(await page.evaluate(key=>localStorage.getItem(key),original.key)).toBe(JSON.stringify(original.saved));
  f.base.base.state.actor=actor;f.base.base.state.principal=principal;f.base.base.state.role='contributor';
  await page.reload();await savedList(page,locale);await recover.click();
  await expect(cleanup(page)).toContainText(locale==='en'?'Relationship withdrawn.':'已撤回關聯。');
  expect(f.state.writes[1]).toEqual(f.state.writes[0]);expect(f.maintenance.events).toEqual(['withdrawn']);
  expect(await page.evaluate(key=>localStorage.getItem(key),original.key)).toBeNull();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
test('source inspector resolves a source-independent original instead of allocating another request',async({page})=>{
  const f=maintenanceFixture();await open(page,false,f);await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
  const original=await seed(page);await page.reload();await reopen(page);
  await relations(page).getByRole('button',{name:STRINGS.en.pm_check_original,exact:true}).click();
  // The inspector's successful receipt refreshes the relationship list before
  // showing its empty state. A received POST alone does not mean the draft cleared.
  await expect(relations(page)).toContainText(STRINGS.en.exh_no_links);
  await expect(relations(page).locator('[data-history-link]')).toHaveCount(0);
  expect(await page.evaluate(key=>localStorage.getItem(key),original.key)).toBeNull();
  expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].idempotency_key).toBe(original.saved.intent.key);
  expect(f.state.writes[0].body).toEqual(original.saved.intent.request);
  expect(await page.evaluate(()=>Object.keys(localStorage).filter(key=>key.includes('.history.') && key.includes('.pm.history.unlink.')))).toEqual([]);
});
test('orphaned old-format intent recovers after both saved index and marker disappear',async({page})=>{
  const f=maintenanceFixture();await open(page,false,f);await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
  f.state.lost=true;await relations(page).getByRole('button',{name:'Withdraw relationship',exact:true}).click();
  await expect(relations(page).locator('[data-api-error]')).toBeVisible();
  await expect(relations(page).getByRole('button',{name:STRINGS.en.pm_check_original,exact:true})).toBeEnabled();
  await page.evaluate(()=>{for(const key of Object.keys(localStorage)) if(key.includes('.history-known.') || key.endsWith('.withdrawals')) localStorage.removeItem(key);});
  f.state.deny=true;f.maintenance.hiddenSource=true;await page.reload();await savedListDesktop(page);
  await cleanup(page).getByRole('button',{name:STRINGS.en.pm_check_original,exact:true}).click();
  await expect(cleanup(page)).toContainText('Relationship withdrawn.');expect(f.state.writes[1]).toEqual(f.state.writes[0]);
});
async function savedListDesktop(page:Page) {
  if(await detail(page).isVisible()) await detail(page).getByRole('button',{name:'Close',exact:true}).click();
  if(await cleanup(page).getAttribute('open')===null) await cleanup(page).locator('summary').click();
}
test('storage failure before withdrawal sends nothing and retains the same in-memory key',async({page})=>{
  const f=maintenanceFixture();await open(page,false,f);await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
  await savedListDesktop(page);
  await page.evaluate(()=>{
    const set=Storage.prototype.setItem;
    (window as any).failedWithdrawal=[];
    Storage.prototype.setItem=function(key,value) {
      if(key.includes('.pm.history.unlink.')) {(window as any).failedWithdrawal.push(JSON.parse(value));throw new DOMException('Storage full','QuotaExceededError');}
      return set.call(this,key,value);
    };
    (window as any).restoreWithdrawalStorage=()=>{Storage.prototype.setItem=set;};
  });
  await cleanup(page).getByRole('button',{name:'Withdraw relationship',exact:true}).click();
  await expect(cleanup(page).locator('[data-api-error]')).toBeVisible();expect(f.state.writes).toHaveLength(0);
  const original=await page.evaluate(()=>{(window as any).restoreWithdrawalStorage();return (window as any).failedWithdrawal[0].intent;});
  await cleanup(page).getByRole('button',{name:STRINGS.en.pm_check_original,exact:true}).click();
  await expect(cleanup(page)).toContainText('Relationship withdrawn.');expect(f.state.writes[0].idempotency_key).toBe(original.key);
  expect(f.state.writes[0].body).toEqual(original.request);
});
test('conflict reset cannot send or advance the saved version without a fresh review',async({page})=>{
  const f=maintenanceFixture();await open(page,false,f);await expect(relations(page).locator('[data-history-link]')).toHaveCount(1);
  await savedListDesktop(page);f.state.links[0].version=2;
  await cleanup(page).getByRole('button',{name:'Withdraw relationship',exact:true}).click();
  await cleanup(page).getByRole('button',{name:'New request',exact:true}).click();
  await expect(cleanup(page).getByRole('button',{name:'Withdraw relationship',exact:true})).toBeDisabled();
  expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].body.preconditions.expected_link_version).toBe(1);
});
