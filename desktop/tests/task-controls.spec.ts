import {expectErrorDetails} from './api-error-assertions.ts';
import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {taskFixture, taskId, opId} from './task-controls-fixture';
const form = (page: any) => page.locator('[data-task-controls]');
const open = async (page: any) => {await page.goto('/dashboard/#/task/'+taskId); await expect(form(page)).toBeVisible();};
for (const native of [false,true]) {
  const mode = native ? 'native' : 'browser';
  test(`${mode}: explicit receipt check also refreshes task state when no event arrives`, async ({page}) => {
    const state=await taskFixture(page,native,{status:'running'}); await open(page);
    await form(page).getByRole('button',{name:'Pause dispatch',exact:true}).click();
    await expect(form(page).locator('.status-running')).toBeVisible();
    await expect.poll(()=>state.reads.filter((p:string)=>p==='/tasks/'+taskId).length).toBeGreaterThanOrEqual(2);
    state.status='succeeded'; state.paused=true; state.version=8;
    await form(page).getByRole('button',{name:'Check original operation'}).click();
    await expect(form(page)).toContainText('control operation completed');
    await form(page).getByRole('button',{name:'Prepare another control'}).click();
    await expect(form(page).getByRole('button',{name:'Resume dispatch',exact:true})).toBeVisible();
    expect(state.posts).toHaveLength(1);
  });
  test(`${mode}: late response cannot read the old task through a new principal`, async ({page}) => {
    const state=await taskFixture(page,native,{delayPost:true}); await open(page);
    await form(page).getByRole('button',{name:'Pause dispatch',exact:true}).click();
    await expect.poll(()=>Boolean(state.holdPost)).toBe(true);
    await page.goto('/dashboard/#/settings');
    await page.getByRole('button',{name:'Disconnect',exact:true}).click();
    state.principal='another-principal';
    if(!native) await page.locator('input[type=password]').fill('other-token');
    await page.getByRole('button',{name:'Connect',exact:true}).click();
    await expect(page).toHaveURL(/#\/home$/);
    const reads=state.reads.filter((p:string)=>p==='/tasks/'+taskId).length;
    state.holdPost();
    // Drain the original callback after native/browser transport resolves.
    await page.waitForTimeout(300);
    expect(state.reads.filter((p:string)=>p==='/tasks/'+taskId)).toHaveLength(reads);
    expect(await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('batc.task-control.'))
      .map(([,value])=>JSON.parse(value).intent?.operation_id))).toEqual([null]);
  });
  test(`${mode}: pause and explicit resume bind observed versions without reapplying`, async ({page}) => {
    const state = await taskFixture(page,native); await open(page);
    await expect(form(page).getByRole('checkbox')).not.toBeChecked();
    await form(page).getByRole('button',{name:'Pause dispatch',exact:true}).click();
    await expect(form(page)).toContainText('control operation completed');
    await expect(page.getByText('Dispatch paused',{exact:true})).toBeVisible();
    expect(state.posts[0].body).toEqual({action:'task.pause',target:{task_id:taskId},params:{abort_current:false},preconditions:{control_version:7}});
    await page.reload(); await expect(form(page)).toContainText('control operation completed');
    expect(state.posts).toHaveLength(1);
    await form(page).getByRole('button',{name:'Prepare another control'}).click();
    expect(state.posts).toHaveLength(1);
    await form(page).getByRole('button',{name:'Resume dispatch',exact:true}).click();
    await expect(page.getByText('Dispatch not paused',{exact:true})).toBeVisible();
    expect(state.posts[1].body).toEqual({action:'task.resume',target:{task_id:taskId},params:{},preconditions:{control_version:8}});
    expect(state.posts[1].idempotency_key).not.toBe(state.posts[0].idempotency_key);
    expect(state.errors).toEqual([]);
  });
  test(`${mode}: lost pause reply retains interrupt, key and original version through reload`, async ({page}) => {
    const state = await taskFixture(page,native,{lost:true}); await open(page);
    await form(page).getByRole('checkbox').check();
    await form(page).getByRole('button',{name:'Pause dispatch',exact:true}).click();
    await expect(form(page)).toContainText('Task reply lost');
    state.version = 20;
    await page.reload();
    await expect(form(page).getByRole('checkbox')).toBeChecked();
    await expect(form(page).getByRole('checkbox')).toBeDisabled();
    await form(page).getByRole('button',{name:'Retry original request'}).click();
    await expect(form(page)).toContainText('control operation completed');
    expect(state.posts).toHaveLength(2); expect(state.posts[1]).toEqual(state.posts[0]);
    expect(state.posts[0].body.params.abort_current).toBe(true);
    expect(state.posts[0].body.preconditions.control_version).toBe(7);
  });
  test(`${mode}: version refusal keeps frozen intent until explicit new draft`, async ({page}) => {
    const state = await taskFixture(page,native,{refuse:'CONTROL_VERSION_CONFLICT'}); await open(page);
    await form(page).getByRole('button',{name:'Pause dispatch',exact:true}).click();
    await expect(form(page).getByRole('button',{name:'Prepare another control'})).toBeVisible();
    const key = state.posts[0].idempotency_key;
    state.version = 10; await page.reload();
    await expect(form(page)).toContainText('control version 7');
    await form(page).getByRole('button',{name:'Prepare another control'}).click();
    expect(state.posts).toHaveLength(1); state.refuse = null;
    await form(page).getByRole('button',{name:'Pause dispatch',exact:true}).click();
    await expect(form(page)).toContainText('control operation completed');
    expect(state.posts[1].body.preconditions.control_version).toBe(10);
    expect(state.posts[1].idempotency_key).not.toBe(key);
  });
  test(`${mode}: unknown and generic auth failures never produce replacement keys`, async ({page}) => {
    const state = await taskFixture(page,native,{lost:true,status:'uncertain'}); await open(page);
    await form(page).getByRole('button',{name:'Pause dispatch',exact:true}).click();
    await expect(form(page)).toContainText('Task reply lost'); state.refuse='FORBIDDEN';
    await form(page).getByRole('button',{name:'Retry original request'}).click();
    await expect(form(page)).toContainText('FORBIDDEN');
    await expect(form(page).getByRole('button',{name:'Prepare another control'})).toBeHidden();
    state.refuse=null; await form(page).getByRole('button',{name:'Retry original request'}).click();
    await expect(form(page).locator('.status-uncertain')).toBeVisible();
    await form(page).getByRole('button',{name:'Check original operation'}).click();
    expect(new Set(state.posts.map((p:any)=>p.idempotency_key)).size).toBe(1);
    expect(state.posts).toHaveLength(3);
    await form(page).getByRole('link',{name:'View operation and step receipts'}).click();
    await expect(page.getByText('task_pause',{exact:true})).toBeVisible();
  });
  test(`${mode}: mismatched key or version cannot establish an operation identity`, async ({page}) => {
    const state = await taskFixture(page,native,{badKey:true}); await open(page);
    await form(page).getByRole('button',{name:'Pause dispatch',exact:true}).click();
    await expect(form(page)).toContainText('does not match');
    state.badKey=false; state.badVersion=true;
    await form(page).getByRole('button',{name:'Retry original request'}).click();
    await expect(form(page)).toContainText('does not match');
    await expect(form(page).getByRole('link',{name:'View operation and step receipts'})).toHaveCount(0);
    state.badVersion=false; await form(page).getByRole('button',{name:'Retry original request'}).click();
    await expect(form(page)).toContainText('control operation completed');
  });
  test(`${mode}: event before POST reply awaits identity and failed reads preserve checkpoint`, async ({page}) => {
    const state = await taskFixture(page,native,{delayPost:true,status:'running'}); await open(page);
    await form(page).getByRole('button',{name:'Pause dispatch',exact:true}).click();
    await expect.poll(()=>Boolean(state.holdPost)).toBe(true);
    state.events.push({seq:1,resource_type:'operation',resource_id:opId,kind:'operation.succeeded'});
    state.status='succeeded'; state.failRead=true;
    await expect.poll(()=>state.reads.some((p:string)=>p.includes('/events'))).toBe(true);
    state.holdPost(); await expectErrorDetails(page, 'Operation read failed');
    expect(state.after).toBe(0);
    state.failRead=false;
    await expect(form(page)).toContainText('control operation completed');
    await expect.poll(()=>state.after).toBe(1); expect(state.posts).toHaveLength(1);
  });
  test(`${mode}: missing capability and initial failed task reads disable controls and recover`, async ({page}) => {
    const state = await taskFixture(page,native,{failTask:true}); await open(page);
    await expect(form(page).getByRole('button',{name:'Pause dispatch',exact:true})).toBeDisabled();
    state.failTask=false; state.events.push({seq:1,resource_type:'task',resource_id:taskId,kind:'task.updated'});
    await expect(form(page).getByRole('button',{name:'Pause dispatch',exact:true})).toBeEnabled();
    state.allowed=null; await page.reload();
    await expect(form(page).getByRole('button',{name:'Pause dispatch',exact:true})).toBeDisabled();
    state.allowed=true; state.scopes=['observe']; await page.reload();
    await expect(form(page).getByRole('button',{name:'Pause dispatch',exact:true})).toBeDisabled();
    expect(state.posts).toHaveLength(0);
  });
  test(`${mode}: backend identity change leaves the old key isolated`, async ({page}) => {
    const state = await taskFixture(page,native,{lost:true}); await open(page);
    await form(page).getByRole('button',{name:'Pause dispatch',exact:true}).click();
    await expect(form(page)).toContainText('Task reply lost');
    state.server='another-server'; state.paused=false; await page.reload();
    await expect(form(page).getByRole('button',{name:'Pause dispatch',exact:true})).toBeVisible();
    expect(state.posts).toHaveLength(1);
    await form(page).getByRole('button',{name:'Pause dispatch',exact:true}).click();
    await expect(form(page)).toContainText('control operation completed');
    expect(state.posts[1].idempotency_key).not.toBe(state.posts[0].idempotency_key);
  });
}
for (const language of ['en-US','zh-TW']) for (const width of [390,768,1440]) test(`task layout ${language} ${width}`, async ({browser})=>{
  const page=await browser.newPage({locale:language,viewport:{width,height:900}});
  await taskFixture(page); await open(page);
  await expect(form(page).getByRole('button').first()).toBeEnabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await mkdir('/tmp/bac-task-controls-after',{recursive:true});
  await page.screenshot({path:`/tmp/bac-task-controls-after/${language}-${width}.png`,fullPage:true}); await page.close();
});
