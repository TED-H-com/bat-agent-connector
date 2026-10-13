import {test, expect, type Page} from '@playwright/test';
import {taskFixture} from './task-controls-fixture';
const openReason = (page: Page) => page.locator('.ui-inspector details.ui-disclosure > summary').click();
const operations = () => Array.from({length:7},(_,i)=>({operation_id:'op_'+String(i+1).padStart(32,'0'),
  action:'task.pause', actor:'history-person', status:i%2 ? 'running':'needs_attention',
  created_at:100-i, status_reason:`Historical receipt ${i+1}`}));
for(const native of [false,true]) {
  const mode=native?'native':'browser';
  test(`${mode}: history loads older pages and refreshes all loaded pages without duplicates`,async({page})=>{
    const state=await taskFixture(page,native,{list:operations()});
    await page.goto('/dashboard/#/operations');
    await expect(page.locator('[data-operation-count]')).toHaveText('2 shown');
    await page.getByRole('button',{name:'Load earlier operations'}).click();
    await expect(page.locator('[data-operation-count]')).toHaveText('4 shown');
    expect(state.reads.some((p:string)=>p.includes('before=99'))).toBe(true);
    state.list[3].status_reason='Older receipt changed';
    state.events.push({seq:1,resource_type:'operation',resource_id:state.list[3].operation_id,kind:'operation.updated'});
    await expect.poll(()=>state.after).toBe(1);
    await page.locator(`[data-row-key="${state.list[3].operation_id}"]`).click();
    await openReason(page);
    await expect(page.getByText('Older receipt changed',{exact:true})).toBeVisible();
    expect(await page.locator('[data-operation-list] [data-row-key]').count()).toBe(4);
    await page.getByRole('button',{name:'Load earlier operations'}).click();
    await page.getByRole('button',{name:'Load earlier operations'}).click();
    await expect(page.locator('[data-operation-count]')).toBeHidden();
    await expect(page.getByRole('button',{name:'Load earlier operations'})).toBeHidden();
    expect(await page.locator('[data-operation-list] [data-row-key]').count()).toBe(7);
    expect(state.errors).toEqual([]);
  });
  test(`${mode}: event during pagination joins it and reads again before acknowledging`,async({page})=>{
    const state=await taskFixture(page,native,{list:operations()});
    await page.goto('/dashboard/#/operations');
    await expect(page.locator('[data-operation-count]')).toContainText('2 shown');
    state.delayList=true;
    await page.getByRole('button',{name:'Load earlier operations'}).click();
    await expect.poll(()=>Boolean(state.holdList)).toBe(true);
    state.events.push({seq:1,resource_type:'operation',resource_id:state.list[3].operation_id,kind:'operation.updated'});
    state.failList=true; state.holdList();
    await expect(page.getByText('List read failed',{exact:true})).toBeVisible();
    expect(state.after).toBe(0);
    await expect(page.locator('[data-operation-count]')).toContainText('2 shown');
    state.failList=false;
    await expect.poll(()=>state.after).toBe(1);
    await page.getByRole('button',{name:'Load earlier operations'}).click();
    await expect(page.locator('[data-operation-count]')).toContainText('4 shown');
  });
  test(`${mode}: newly inserted rows use fresh cursors and retain the loaded window`,async({page})=>{
    const state=await taskFixture(page,native,{list:operations()});
    await page.goto('/dashboard/#/operations');
    await page.getByRole('button',{name:'Load earlier operations'}).click();
    await expect(page.locator('[data-operation-count]')).toContainText('4 shown');
    const inserted='op_'+'e'.repeat(32);
    state.list.unshift({...state.list[0],operation_id:inserted,created_at:101,status_reason:'New operation'});
    state.events.push({seq:1,resource_type:'operation',resource_id:inserted,kind:'operation.accepted'});
    await expect.poll(()=>state.after).toBe(1);
    await page.locator(`[data-row-key="${inserted}"]`).click();
    await openReason(page);
    await expect(page.getByText('New operation',{exact:true})).toBeVisible();
    await expect(page.locator('[data-operation-count]')).toContainText('4 shown');
    await page.getByRole('button',{name:'Load earlier operations'}).click();
    await page.locator('[data-row-key="op_'+String(4).padStart(32,'0')+'"]').click();
    await openReason(page);
    await expect(page.getByText('Historical receipt 4',{exact:true})).toBeVisible();
    await expect(page.locator('[data-operation-count]')).toContainText('6 shown');
  });
  test(`${mode}: Home continuation preserves attention or active filter`,async({page})=>{
    await taskFixture(page,native,{list:operations()});
    await page.goto('/dashboard/#/home');
    await page.getByRole('link',{name:'View all',exact:true}).click();
    await expect(page.locator('[data-operation-count]')).toContainText('2 shown');
    await expect(page.getByText('Historical receipt 2',{exact:true})).toHaveCount(0);
    await page.getByRole('button',{name:'Load earlier operations'}).click();
    await page.locator('[data-row-key="op_'+String(7).padStart(32,'0')+'"]').click();
    await openReason(page);
    await expect(page.getByText('Historical receipt 7',{exact:true})).toBeVisible();
    await page.goto('/dashboard/#/home');
    await page.getByRole('radio',{name:'Active operations',exact:true}).click();
    await page.getByRole('link',{name:'To confirm', exact:true}).click();
    await page.locator('[data-row-key="op_'+String(2).padStart(32,'0')+'"]').click();
    await openReason(page);
    await expect(page.getByText('Historical receipt 2',{exact:true})).toBeVisible();
    await expect(page.getByText('Historical receipt 1',{exact:true})).toHaveCount(0);
  });
}
