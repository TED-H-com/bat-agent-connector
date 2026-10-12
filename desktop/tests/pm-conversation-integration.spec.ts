import {test,expect} from '@playwright/test';
import {structuredCapability,BAT} from './pm-structured-analysis-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture,pmRoute,pmPath,workspaceId} from './pm-fixture.ts';
import {proposalFixture,proposalId,PROPOSE,ADOPT} from './pm-plan-proposals-fixture.ts';

for(const native of [false,true])test(`${native?'Native':'Web'} workspace mounts sourced plan review beside the original discussion`,async({page},testInfo)=>{
  const base=pmFixture(),proposal=proposalFixture();
  Object.assign(base.state,{actor:'owner',plans:true,start:true,bound:true});base.state.thread.version=4;proposal.save();
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path.includes('/conversation-proposals')||input.body?.action?.startsWith('pm.conversation.'))return proposal.dispatch(input);
    const value=await base.dispatch(input);
    if(path===pmPath&&value.status===200)value.data.next_message_cursor=1;
    if(path==='/bootstrap'||path==='/capabilities'){
      const caps=path==='/bootstrap'?value.data.capabilities:value.data;
      caps.features.pm_workspaces.conversation_proposals={version:1};caps.actions.push(...[PROPOSE,ADOPT].map(action=>({action,allowed:true})));
    }
    if(path===`/pm/workspaces/${workspaceId}/capabilities`)value.data.native_analysis={eligible_for_preflight:true,authorized:true};
    return value;
  };
  await mountConversation(page,native,dispatch);
  if(native)await page.addInitScript(caps=>{
    const ipc=(window as any).__TAURI_INTERNALS__,invoke=ipc.invoke;
    ipc.invoke=(command:string,args:any)=>command==='connector_connect'?Promise.resolve(caps):invoke(command,args);
  },(await dispatch({method:'GET',path:'/capabilities'})).data);
  await page.goto(pmRoute);
  const panel=page.locator('[data-pm-plan-proposals]');await expect(panel).toBeVisible();
  await panel.getByRole('combobox',{name:'Saved proposal',exact:true}).selectOption(proposalId);
  const apply=panel.locator(`[data-pm-action="${ADOPT}"]`);
  await expect(apply).toContainText('Add paid member login and password reset');
  await apply.getByLabel('Reason for adopting this update').fill('Adopt this reviewed member flow');
  for(const width of [390,768,1440]){
    await page.setViewportSize({width,height:900});
    await page.evaluate(()=>window.scrollTo(0,0));
    await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath(`${native?'native':'web'}-${width}.png`),fullPage:true});
  }
  await apply.getByRole('button',{name:'Adopt this update',exact:true}).click();
  await expect.poll(()=>proposal.state.writes.length).toBe(1);
  expect(base.state.writes).toEqual([]);
  expect(proposal.state.writes[0].body.params.review.rows.find((row:any)=>row.key==='referral').include).toBe(false);
  await expect(page.locator('.pm-notes')).toContainText('Visitors should find their first useful action');
  await expect(panel.getByRole('combobox',{name:'Saved proposal',exact:true})).toHaveValue('');
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  base.state.failRead=true;
  await page.getByRole('button',{name:'Load earlier notes',exact:true}).click();
  await expect(panel).not.toBeVisible();
  await expect(page.locator('.pm-notes')).not.toBeVisible();
  expect(proposal.state.writes).toHaveLength(1);
});

for(const native of [false,true])for(const locale of ['en','zh-TW'])test(`structured proposal ${native?'Tauri':'Web'} ${locale} sends an explicit BAT intent`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage();try{
  const base=pmFixture(),proposal=proposalFixture();Object.assign(base.state,{actor:'owner',plans:true,start:true,bound:true});base.state.thread.version=4;
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path.includes('/conversation-proposals')||input.body?.action?.startsWith('pm.conversation.'))return proposal.dispatch(input);
    const value=await base.dispatch(input);
    if(path==='/bootstrap'||path==='/capabilities'){const caps=path==='/bootstrap'?value.data.capabilities:value.data;caps.features.pm_workspaces.conversation_proposals={version:1};caps.actions.push(...[PROPOSE,ADOPT].map(action=>({action,allowed:true})));}
    if(path===`/pm/workspaces/${workspaceId}/capabilities`){value.data.native_analysis={eligible_for_preflight:false,authorized:true};value.data.structured_analysis=structuredCapability();}return value;
  };
  await mountConversation(page,native,dispatch);
  if(native)await page.addInitScript(caps=>{const ipc=(window as any).__TAURI_INTERNALS__,invoke=ipc.invoke;ipc.invoke=(command:string,args:any)=>command==='connector_connect'?Promise.resolve(caps):invoke(command,args);},(await dispatch({method:'GET',path:'/capabilities'})).data);
  await page.goto(pmRoute);const form=page.locator(`[data-pm-action="${PROPOSE}"]`),choice=form.locator('[data-pm-structured-analysis] select');
  const heading=page.locator('[data-pm-plan-proposals] > h3'),title=locale==='zh-TW'?'從這段討論整理計畫':'Plan update from this discussion';
  await expect(heading).toHaveText(title);
  await expect(choice).toBeEnabled();await choice.selectOption(BAT);await form.locator('textarea').fill('Prepare the original member-access conversation.');
  await page.reload();await expect(heading).toHaveText(title);await expect(choice).toHaveValue(BAT);await expect(form.locator('textarea')).toHaveValue('Prepare the original member-access conversation.');
  await form.locator('button[type=submit]').click();await expect.poll(()=>proposal.state.writes.length).toBe(1);
  expect(proposal.state.writes[0].body.params.adapter).toBe(BAT);expect(base.state.writes).toEqual([]);
  }finally{await context.close();}
});
