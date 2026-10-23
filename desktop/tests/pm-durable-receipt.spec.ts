import {openThreadTab} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {setTimeout as delay} from 'node:timers/promises';
import {STRINGS} from '../src/i18n.js';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture,pmRoute,workspaceId} from './pm-fixture.ts';

const action=(page:any)=>page.locator('[data-pm-action="pm.input.record"]');
const draft=async(page:any)=>(await page.evaluate(()=>Object.entries(localStorage)
  .filter(([key])=>key.includes('.pm.input.record.')).map(([key,value])=>({key,value:JSON.parse(value)}))))[0];

function fixture() {
  const base=pmFixture(), state={complete:false,gets:[] as string[],getError:0,mismatch:false,hold:null as Promise<void>|null,postStatus:'running',workspaceHold:null as Promise<void>|null,workspaceReads:0};
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(input.method==='GET'&&path===`/pm/workspaces/${workspaceId}`){state.workspaceReads++;if(state.workspaceHold)await state.workspaceHold;}
    if(input.method==='POST'&&path==='/operations'&&input.body.action==='pm.input.record'){
      const response=await base.dispatch(input);
      if(response.status>=400)return response;
      // The central domain effect may become visible before the worker publishes
      // its terminal operation receipt. This is the real CI ordering boundary.
      base.state.cursor++;
      return {status:202,data:{operation:{...response.data.operation,status:state.postStatus,result:null}}};
    }
    if(input.method==='GET'&&path.startsWith('/operations/')){
      state.gets.push(path);
      if(state.hold)await state.hold;
      if(state.getError)return {status:state.getError,data:{error:{code:state.getError===403?'FORBIDDEN':'UNAVAILABLE',message:'Original receipt unavailable'}}};
      const response=await base.dispatch(input);
      return {status:200,data:{operation:{...response.data.operation,
        status:state.complete?'succeeded':'running',...(state.mismatch?{idempotency_key:'foreign-key'}:{})}}};
    }
    return base.dispatch(input);
  };
  return {base,state,dispatch};
}

async function submit(page:any,copy=STRINGS.en) {
  await page.locator('[data-pm-add-input] > summary').click();
  const form=action(page);
  await form.getByLabel(copy.pm_input_slot,{exact:true}).selectOption('problem');
  await form.getByLabel(copy.pm_input_value,{exact:true}).fill('A fixed original input');
  await form.getByRole('button',{name:copy.pm_record_input,exact:true}).click();
  await expect.poll(async()=>(await draft(page))?.value.intent?.operation_id).toMatch(/^op_[0-9a-f]{32}$/);
  return form;
}

for(const native of [false,true])test.describe(native?'Native original PM receipt':'Web original PM receipt',()=>{
  test('a recovery form detached during refresh follows the current workspace gate after reattachment',async({page})=>{
    const f=fixture();f.base.state.lost=true;await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadTab(page,'inputs');
    await page.locator('[data-pm-add-input] > summary').click();const form=action(page);
    await form.getByLabel('Topic',{exact:true}).selectOption('problem');
    await form.getByLabel('Statement or question',{exact:true}).fill('Keep the original detached recovery');
    await form.getByRole('button',{name:'Save input',exact:true}).click();
    await expect(form).toContainText('Reply unavailable');const saved=await draft(page);
    expect(saved.value.intent.operation_id).toBeUndefined();expect(f.base.state.writes).toHaveLength(1);
    let release=()=>{};f.state.workspaceHold=new Promise<void>(resolve=>release=resolve);
    const before=f.state.workspaceReads;
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect.poll(()=>f.state.workspaceReads).toBeGreaterThan(before);
    await form.evaluate(node=>{
      const parent=node.parentElement!;(window as any).detachedRecovery={node,parent};
      parent.replaceChildren(...[...parent.childNodes].filter(child=>child!==node));
    });
    f.base.state.role='reader';f.state.workspaceHold=null;release();
    await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
    await page.evaluate(()=>{const {node,parent}=(window as any).detachedRecovery;parent.append(node);});
    const retry=form.getByRole('button',{name:'Check original request',exact:true});
    await expect(retry).toBeDisabled();
    // A submit already queued when access closes must explain the refusal too.
    await form.evaluate(node=>node.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
    await expect(form.locator(':scope > [role=status]')).toContainText('This action is currently unavailable.');
    expect((await draft(page)).value.intent).toEqual(saved.value.intent);expect(f.base.state.writes).toHaveLength(1);
    f.base.state.role='owner';await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect(retry).toBeEnabled();f.state.postStatus='succeeded';await retry.click();
    await expect(form.getByLabel('Topic',{exact:true})).toBeEnabled();
    expect(f.base.state.writes).toHaveLength(2);expect(f.base.state.writes[1].idempotency_key).toBe(saved.value.intent.key);
    expect(f.base.state.writes[1].body).toEqual(saved.value.intent.request);expect(f.base.state.ops.size).toBe(1);
    expect(f.base.state.inputs.filter(row=>row.value==='Keep the original detached recovery')).toHaveLength(1);
  });

  test('visible input plus running receipt is reconciled by GET before editing another input',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadTab(page,'inputs');
    const form=await submit(page);
    await expect(page.locator('.pm-facts')).toContainText('A fixed original input');
    await expect(form.getByLabel('Topic',{exact:true})).toBeDisabled();
    const saved=await draft(page);expect(saved.value.intent.operation_id).toMatch(/^op_[0-9a-f]{32}$/);
    f.state.complete=true;
    await expect(form.getByLabel('Topic',{exact:true})).toBeEnabled();
    await form.getByLabel('Topic',{exact:true}).selectOption('alternatives');
    expect(f.base.state.writes).toHaveLength(1);
    expect(f.state.gets.length).toBeGreaterThan(0);
    expect(f.state.gets.every(path=>path==='/operations/'+saved.value.intent.operation_id)).toBe(true);
    expect((await draft(page)).value.intent).toBeUndefined();
    expect(f.base.state.inputs.filter(row=>row.value==='A fixed original input')).toHaveLength(1);
  });

  test('reload checks an acknowledged original ID without another POST',async({page})=>{
    const f=fixture();f.state.postStatus='accepted';await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadTab(page,'inputs');
    await submit(page);const saved=await draft(page);
    await page.reload();await openThreadTab(page,'inputs');f.state.complete=true;
    await expect(action(page).getByLabel('Topic',{exact:true})).toBeEnabled();
    expect(f.base.state.writes).toHaveLength(1);
    expect(f.state.gets.every(path=>path==='/operations/'+saved.value.intent.operation_id)).toBe(true);
    expect((await draft(page)).value.intent).toBeUndefined();
  });

  for(const locale of ['en-US','zh-TW'])test.describe(`bounded receipt ${locale}`,()=>{
  test.use({locale});
  test('bounded polling stops with the original key intact and retains manual recovery',async({page})=>{
    const copy=STRINGS[locale==='en-US'?'en':'zh-TW'];
    const f=fixture();await page.clock.install();await mountConversation(page,native,async input=>{
      const firstReceipt=input.method==='GET'&&input.path.startsWith('/operations/')&&f.state.gets.length===0;
      const result=await f.dispatch(input);
      // Delay the first reply in real time, independently of the page's backoff clock.
      if(firstReceipt)await delay(1_500);
      return result;
    });await page.goto(pmRoute);await openThreadTab(page,'inputs');
    await page.clock.pauseAt(await page.evaluate(()=>Date.now()+1000));
    const form=await submit(page,copy),saved=await draft(page);
    await expect(form.getByRole('button',{name:copy.pm_record_input,exact:true})).toBeDisabled();
    // Allow each asynchronous GET to settle before advancing the next backoff.
    const delays=[250,500,1000,1500,2000,3000];
    for(let i=1;i<=6;i++){
      await page.clock.runFor(delays[i-1]);
      await expect(form.locator(':scope > [role=status]')).not.toHaveAttribute('aria-busy','true');
      expect(f.state.gets).toHaveLength(i);
      if (i<6) await expect(form.getByRole('button',{name:copy.pm_record_input,exact:true})).toBeDisabled();
      else await expect(form.getByRole('button',{name:copy.pm_check_original,exact:true})).toBeEnabled();
    }
    await page.clock.runFor(30000);
    expect(f.state.gets).toHaveLength(6);
    expect((await draft(page)).value.intent).toEqual(saved.value.intent);
    await expect(form.getByLabel(copy.pm_input_slot,{exact:true})).toBeDisabled();
    await expect(form.getByRole('button',{name:copy.pm_check_original,exact:true})).toBeEnabled();
    f.state.complete=true;await form.getByRole('button',{name:copy.pm_check_original,exact:true}).click();
    await expect(form.getByLabel(copy.pm_input_slot,{exact:true})).toBeEnabled();
    expect(f.state.gets).toHaveLength(7);expect(f.base.state.writes).toHaveLength(1);
  });
  });

  test('an uncertain original receipt needs explicit recovery and is never automatically resubmitted',async({page})=>{
    const f=fixture();f.state.postStatus='uncertain';await page.clock.install();
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadTab(page,'inputs');
    const form=await submit(page),saved=await draft(page);
    await expect(form.getByRole('button',{name:'Check original request',exact:true})).toBeEnabled();
    await page.clock.runFor(30000);
    expect(f.state.gets).toHaveLength(0);expect(f.base.state.writes).toHaveLength(1);
    expect((await draft(page)).value.intent).toEqual(saved.value.intent);
    f.state.complete=true;await form.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(form.getByLabel('Topic',{exact:true})).toBeEnabled();
    expect(f.state.gets).toEqual(['/operations/'+saved.value.intent.operation_id]);
    expect(f.base.state.writes).toHaveLength(1);
  });

  test('lost admission without an operation ID never starts an automatic POST',async({page})=>{
    const f=fixture();f.base.state.lost=true;await page.clock.install();
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadTab(page,'inputs');
    await page.locator('[data-pm-add-input] > summary').click();const form=action(page);
    await form.getByLabel('Topic',{exact:true}).selectOption('problem');
    await form.getByLabel('Statement or question',{exact:true}).fill('Preserve a lost original admission');
    await form.getByRole('button',{name:'Save input',exact:true}).click();
    await expect(form).toContainText('Reply unavailable');const saved=await draft(page);
    expect(saved.value.intent.operation_id).toBeUndefined();
    await page.clock.runFor(30000);
    expect(f.state.gets).toHaveLength(0);expect(f.base.state.writes).toHaveLength(1);
    expect((await draft(page)).value.intent).toEqual(saved.value.intent);
    f.state.postStatus='succeeded';await form.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(form.getByLabel('Topic',{exact:true})).toBeEnabled();
    expect(f.base.state.writes).toHaveLength(2);
    expect(f.base.state.writes[1].idempotency_key).toBe(saved.value.intent.key);
    expect(f.base.state.writes[1].body).toEqual(saved.value.intent.request);
    expect(f.base.state.ops.size).toBe(1);expect(f.state.gets).toHaveLength(0);
  });

  for(const failure of ['403','network','mismatch'])test(`a ${failure} receipt stops polling and never clears the intent`,async({page})=>{
    const f=fixture();f.state.getError=failure==='403'?403:failure==='network'?503:0;f.state.mismatch=failure==='mismatch';
    await page.clock.install();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadTab(page,'inputs');
    const form=await submit(page),saved=await draft(page);
    await page.clock.runFor(1000);await expect.poll(()=>f.state.gets.length).toBe(1);
    await expect(form).toContainText(failure==='mismatch'?'The receipt does not match':failure==='403'?'Your account does not have the required scope':'Original receipt unavailable');
    await page.clock.runFor(30000);expect(f.state.gets).toHaveLength(1);
    expect((await draft(page)).value.intent).toEqual(saved.value.intent);
    f.state.getError=0;f.state.mismatch=false;f.state.complete=true;
    await form.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(form.getByLabel('Topic',{exact:true})).toBeEnabled();
    expect(f.base.state.writes).toHaveLength(1);expect(f.state.gets).toHaveLength(2);
  });

  test('a late receipt after leaving the view cannot clear or replace the original request',async({page})=>{
    const f=fixture();let release=()=>{};f.state.hold=new Promise<void>(resolve=>release=resolve);
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadTab(page,'inputs');await submit(page);
    await expect.poll(()=>f.state.gets.length).toBe(1);const saved=await draft(page);
    await page.locator('a[href="#/settings"]').first().click();
    await expect(page).toHaveURL(/#\/settings$/);
    await expect(action(page)).toHaveCount(0);
    f.state.complete=true;release();
    await page.waitForTimeout(400);
    expect((await draft(page)).value.intent).toEqual(saved.value.intent);
    expect(f.base.state.writes).toHaveLength(1);expect(f.state.gets).toHaveLength(1);
  });

  test('write access lost during receipt read preserves the original local intent',async({page})=>{
    const f=fixture();let release=()=>{};f.state.hold=new Promise<void>(resolve=>release=resolve);
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadTab(page,'inputs');const form=await submit(page);
    await expect.poll(()=>f.state.gets.length).toBe(1);const saved=await draft(page);
    f.base.state.role='reader';await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect(page.getByText('You can read this workspace. Editing requires contributor access.',{exact:true})).toBeVisible();
    f.state.complete=true;release();await page.waitForTimeout(400);
    await expect(form.locator(':scope > [role=status]')).toContainText('This action is currently unavailable.');
    expect((await draft(page)).value.intent).toEqual(saved.value.intent);
    await expect(form.getByRole('button',{name:'Check original request',exact:true})).toBeDisabled();
    expect(f.base.state.writes).toHaveLength(1);expect(f.state.gets).toHaveLength(1);
  });
});
