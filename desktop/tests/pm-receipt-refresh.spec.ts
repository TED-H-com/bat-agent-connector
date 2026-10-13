import {openThreadTab} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture,workspaceId,threadId,pmRoute,pmPath} from './pm-fixture.ts';

const home=`/dashboard/#/pm/${workspaceId}`;
const form=(page:any)=>page.locator('[data-pm-action="pm.thread.create"]');
const intents=(page:any)=>page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('.pm.thread.create.')).map(([key,value])=>({key,...JSON.parse(value)})));

for(const native of [false,true])test.describe(native?'Native receipt continuity':'Web receipt continuity',()=>{
  for(const interruption of ['read failure','view remount','access reset'])test(`${interruption} preserves an acknowledged request and recovers only by original ID`,async({page})=>{
    const f=pmFixture();let release=()=>{};f.state.defer=new Promise<void>(resolve=>release=resolve);
    let reset=false,bootstraps=0,replied=false;const reads:string[]=[];
    await mountConversation(page,native,async(input)=>{
      const path=new URL(input.path,'http://fixture').pathname;
      if(path==='/bootstrap')bootstraps++;
      if(path==='/events'&&reset){reset=false;return {status:409,data:{error:{code:'EVENT_CURSOR_RESET',reason:'access_changed',message:'Access changed'}}};}
      if(path.startsWith('/operations/')&&input.method==='GET')reads.push(path);
      const result=await f.dispatch(input);if(input.method==='POST')replied=true;return result;
    });
    await page.goto(home);await form(page).getByLabel('Discussion title',{exact:true}).fill('Keep the original request');
    await form(page).getByRole('button',{name:'Start discussion',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(1);
    const original=(await intents(page))[0];
    if(interruption==='read failure'){
      f.state.failRead=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();
      await expect(page.locator('.pm-status')).toContainText('PM read unavailable');
    }else if(interruption==='view remount'){
      await page.evaluate(()=>{location.hash='#/settings';});
      await expect(page.getByRole('heading',{name:'Start an idea discussion',exact:true})).toHaveCount(0);
      await page.evaluate(wid=>{location.hash=`#/pm/${wid}`;},workspaceId);
      await expect(form(page).getByRole('button',{name:'Check original request',exact:true})).toBeVisible();
    }else{
      reset=true;await expect.poll(()=>bootstraps).toBe(2);
      await expect(form(page).getByRole('button',{name:'Check original request',exact:true})).toBeVisible();
    }
    release();await expect.poll(()=>replied).toBe(true);
    await expect.poll(async()=>Boolean((await intents(page))[0]?.intent?.operation_id)).toBe(true);
    if(interruption==='read failure'){
      expect(reads).toEqual([]);await expect(page).toHaveURL(new RegExp(`${workspaceId}/discussions$`));
      f.state.failRead=false;await page.getByRole('button',{name:'Refresh',exact:true}).click();
    }
    await expect(page).toHaveURL(new RegExp(`${workspaceId}/discussions/${threadId}$`));
    expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].idempotency_key).toBe(original.intent.key);
    expect(f.state.writes[0].body).toEqual(original.intent.request);
    expect(reads).toEqual([`/operations/${[...f.state.ops.values()][0].operation_id}`]);
  });
  for(const identity of ['principal','server'])test(`another ${identity} receives no result or operation read; only the original namespace gains the ID`,async({page})=>{
    const f=pmFixture();let release=()=>{};f.state.defer=new Promise<void>(resolve=>release=resolve);
    let reset=false,bootstraps=0,server='pm-fixture';const reads:string[]=[];
    await mountConversation(page,native,async(input)=>{
      const path=new URL(input.path,'http://fixture').pathname;
      if(path==='/bootstrap')bootstraps++;
      if(path==='/events'&&reset){reset=false;return {status:409,data:{error:{code:'EVENT_CURSOR_RESET',reason:'access_changed'}}};}
      if(path.startsWith('/operations/')&&input.method==='GET')reads.push(path);
      const response=await f.dispatch(input);
      if(path==='/bootstrap')response.data.sync.server_id=server;
      return response;
    });
    await page.goto(home);await form(page).getByLabel('Discussion title',{exact:true}).fill('Original private draft');
    await form(page).getByRole('button',{name:'Start discussion',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
    const original=(await intents(page))[0];
    if(identity==='principal')f.state.principal='different-person';else server='different-server';
    reset=true;
    await expect.poll(()=>bootstraps).toBe(2);await expect(form(page).getByRole('button',{name:'Start discussion',exact:true})).toBeVisible();
    release();await expect.poll(async()=>(await intents(page)).find(row=>row.key===original.key)?.intent?.operation_id).toBe([...f.state.ops.values()][0].operation_id);
    await page.waitForTimeout(400);
    await expect(form(page).getByLabel('Discussion title',{exact:true})).toHaveValue('');
    await expect(page).toHaveURL(new RegExp(`${workspaceId}/discussions$`));expect(reads).toEqual([]);expect(f.state.writes).toHaveLength(1);
    const stored=(await intents(page)).find(row=>row.key===original.key);
    expect(stored).toEqual({...original,intent:{...original.intent,operation_id:[...f.state.ops.values()][0].operation_id}});
  });
  test('source withdrawal refuses the recovered GET and keeps the original draft until an authorized read',async({page})=>{
    const f=pmFixture();let release=()=>{};f.state.defer=new Promise<void>(resolve=>release=resolve);
    let deny=false;const reads:string[]=[];
    await mountConversation(page,native,async(input)=>{
      const path=new URL(input.path,'http://fixture').pathname;
      if(path.startsWith('/operations/')&&input.method==='GET'){
        reads.push(path);if(deny)return {status:403,data:{error:{code:'FORBIDDEN',message:'Original source unavailable'}}};
      }
      return f.dispatch(input);
    });
    await page.goto(home);await form(page).getByLabel('Discussion title',{exact:true}).fill('Preserve the exact source');
    await form(page).getByRole('button',{name:'Start discussion',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
    const original=(await intents(page))[0];await page.evaluate(()=>{location.hash='#/settings';});
    await expect(form(page)).toHaveCount(0);await page.evaluate(wid=>{location.hash=`#/pm/${wid}`;},workspaceId);
    await expect(form(page).getByRole('button',{name:'Check original request',exact:true})).toBeVisible();deny=true;release();
    await expect.poll(()=>reads.length).toBe(1);await expect(form(page).locator('.error')).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`${workspaceId}/discussions$`));
    await expect(form(page).getByLabel('Discussion title',{exact:true})).toHaveValue('Preserve the exact source');
    await expect(form(page).getByLabel('Discussion title',{exact:true})).toBeDisabled();
    expect((await intents(page))[0].intent.request).toEqual(original.intent.request);
    deny=false;await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(page).toHaveURL(new RegExp(`${workspaceId}/discussions/${threadId}$`));
    expect(f.state.writes).toHaveLength(1);expect(reads).toHaveLength(2);
  });
  for(const mismatch of ['actor','key','action','target','params','preconditions','id'])test(`a mismatched ${mismatch} cannot acknowledge the original intent`,async({page})=>{
    const f=pmFixture();await mountConversation(page,native,async(input)=>{
      const response=await f.dispatch(input);
      if(input.method==='POST'){
        const op={...response.data.operation};
        if(mismatch==='actor')op.actor='other';
        if(mismatch==='key')op.idempotency_key='other';
        if(mismatch==='action')op.action='pm.workspace.create';
        if(mismatch==='target')op.target={workspace_id:'ws_'+'9'.repeat(32)};
        if(mismatch==='params')op.params={title:'Other request'};
        if(mismatch==='preconditions')op.preconditions={expected_version:100};
        if(mismatch==='id')op.operation_id='not-an-operation';
        return {status:200,data:{operation:op}};
      }
      return response;
    });
    await page.goto(home);await form(page).getByLabel('Discussion title',{exact:true}).fill('Exact original identity');
    await form(page).getByRole('button',{name:'Start discussion',exact:true}).click();
    await expect(form(page)).toContainText('The receipt does not match');
    expect((await intents(page))[0].intent.operation_id).toBeUndefined();expect(f.state.writes).toHaveLength(1);
    await expect(page).toHaveURL(new RegExp(`${workspaceId}/discussions$`));
  });
  test('an input receipt across remount restores the Topic control after reading the original operation',async({page})=>{
    const f=pmFixture();let release=()=>{};f.state.defer=new Promise<void>(resolve=>release=resolve);
    const reads:string[]=[];await mountConversation(page,native,async(input)=>{
      if(input.method==='GET'&&input.path.startsWith('/operations/'))reads.push(input.path);
      return f.dispatch(input);
    });
    await page.goto(pmRoute);await openThreadTab(page,'inputs');await page.locator('[data-pm-add-input] summary').click();
    const inputForm=page.locator('[data-pm-action="pm.input.record"]');
    await inputForm.getByLabel('Topic',{exact:true}).selectOption('unknowns');
    await inputForm.getByLabel('Statement or question',{exact:true}).fill('What belongs in the first visit?');
    await inputForm.getByRole('button',{name:'Save input',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
    await page.evaluate(wid=>{location.hash=`#/pm/${wid}`;},workspaceId);await expect(inputForm).toHaveCount(0);
    await page.evaluate(({wid,tid})=>{location.hash=`#/pm/${wid}/${tid}`;},{wid:workspaceId,tid:threadId});
    await expect(inputForm).toHaveCount(1);await openThreadTab(page,'inputs');
    if(!await page.locator('[data-pm-add-input]').evaluate((node:HTMLDetailsElement)=>node.open))await page.locator('[data-pm-add-input]>summary').click();
    await expect(inputForm.getByRole('button',{name:'Check original request',exact:true})).toBeVisible();release();
    await expect(inputForm.getByLabel('Topic',{exact:true})).toBeEnabled();
    await expect(inputForm.getByLabel('Statement or question',{exact:true})).toHaveValue('');
    expect(f.state.writes).toHaveLength(1);expect(reads).toHaveLength(1);
    expect(f.state.inputs.filter(input=>input.value==='What belongs in the first visit?')).toHaveLength(1);
  });
  test('a late first response cannot restore an intent already recovered or overwrite a new draft',async({page})=>{
    const f=pmFixture();let release=()=>{};f.state.defer=new Promise<void>(resolve=>release=resolve);
    let responses=0;await mountConversation(page,native,async(input)=>{
      const result=await f.dispatch(input);if(input.method==='POST')responses++;return result;
    });
    await page.goto(home);await form(page).getByLabel('Discussion title',{exact:true}).fill('Recover once');
    await form(page).getByRole('button',{name:'Start discussion',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
    await page.evaluate(()=>{location.hash='#/settings';});await expect(form(page)).toHaveCount(0);
    await page.evaluate(wid=>{location.hash=`#/pm/${wid}`;},workspaceId);
    await expect(form(page).getByRole('button',{name:'Check original request',exact:true})).toBeVisible();
    // The user explicitly checks an unacknowledged original key. Its second
    // response arrives before the first; automatic recovery must not issue it.
    f.state.defer=null;await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(page).toHaveURL(new RegExp(`${workspaceId}/discussions/${threadId}$`));
    await page.evaluate(wid=>{location.hash=`#/pm/${wid}`;},workspaceId);
    await form(page).getByLabel('Discussion title',{exact:true}).fill('A separate new draft');
    const before=await intents(page);release();await expect.poll(()=>responses).toBe(2);await page.waitForTimeout(350);
    expect(await intents(page)).toEqual(before);
    await expect(form(page).getByLabel('Discussion title',{exact:true})).toHaveValue('A separate new draft');
    await expect(form(page).getByRole('button',{name:'Start discussion',exact:true})).toBeEnabled();
    expect(f.state.writes).toHaveLength(2);expect(f.state.ops.size).toBe(1);
    expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
    expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
  });
  test('a retired form admission refusal cannot poison a current event refresh or unlock its old intent',async({page})=>{
    const f=pmFixture();let rejectOriginal=()=>{},completeRead=()=>{};
    const originalWait=new Promise<void>(resolve=>rejectOriginal=resolve),readWait=new Promise<void>(resolve=>completeRead=resolve);
    let holdRead=false,reading=false,rejected=false;
    await mountConversation(page,native,async(input)=>{
      if(input.method==='POST'){
        f.state.writes.push(structuredClone(input));await originalWait;rejected=true;
        return {status:403,data:{error:{code:'FORBIDDEN',message:'Earlier request refused',admission_refused:true}}};
      }
      if(input.method==='GET'&&input.path===pmPath&&holdRead){reading=true;await readWait;}
      return f.dispatch(input);
    });
    await page.goto(home);await form(page).getByLabel('Discussion title',{exact:true}).fill('Earlier view original');
    await form(page).getByRole('button',{name:'Start discussion',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
    const original=await intents(page);await page.evaluate(({wid,tid})=>{location.hash=`#/pm/${wid}/${tid}`;},{wid:workspaceId,tid:threadId});
    await expect(page.locator('.pm-notes')).toContainText('Visitors should find');
    holdRead=true;f.state.cursor=1;await expect.poll(()=>reading).toBe(true);
    rejectOriginal();await expect.poll(()=>rejected).toBe(true);await page.waitForTimeout(100);completeRead();
    // A matching current read must acknowledge this event without being held by
    // a rejected mutation belonging to a detached previous view.
    await expect.poll(()=>page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('batc.sync.')).map(([,raw])=>JSON.parse(raw).cursor)),{timeout:1500}).toContain(1);
    expect(await intents(page)).toEqual(original);
    await expect(page.locator('.pm-status')).not.toContainText('Earlier request refused');
    expect(f.state.writes).toHaveLength(1);
  });
});
