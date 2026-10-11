import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {fixture,workspaceId,pid,actions} from './pm-sprint-fixture.ts';
import {pmFixture} from './pm-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
const route=`/dashboard/#/pm/${workspaceId}`;
async function mountWorkspace(page:any,native:boolean,dispatch:(input:any)=>Promise<any>){
  if(!native)return mountConversation(page,false,dispatch);
  await page.exposeFunction('sprintConnector',dispatch);
  await page.addInitScript(()=>Object.assign(window,{isTauri:true,__TAURI_INTERNALS__:{
    invoke:async(command:string,args:any)=>{
      if(command==='native_status')return{endpoint:'https://central.example/',credential_available:true};
      if(command==='connector_connect')return(await(window as any).sprintConnector({method:'GET',path:'/capabilities'})).data;
      if(command==='connector_disconnect')return null;
      if(command==='connector_request')return(window as any).sprintConnector(args.input);
      throw new Error(`Unexpected native command ${command}`);
    }
  }}));
  await page.route('**/api/v1/**',()=>{throw new Error('Native must use IPC');});
}
function fullFixture(){
  const sprint=fixture(),base=pmFixture(),prefix=`/pm/workspaces/${workspaceId}`;
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    base.state.actor=sprint.state.actor;base.state.role=sprint.state.role;base.state.manage=sprint.state.manage;
    if(path==='/bootstrap'||path==='/capabilities'){
      const result=await base.dispatch(input),caps=path==='/bootstrap'?result.data.capabilities:result.data;
      caps.features.pm_workspaces.sprints=true;caps.actions.push(...sprint.config().caps.actions);return result;
    }
    if(path===prefix){const result=await base.dispatch(input);result.data.workspace={...result.data.workspace,...sprint.config().workspace};return result;}
    if(path.startsWith(prefix+'/sprint')||path===prefix+'/plans/accepted'||path===prefix+'/work'||path==='/projects/'+pid
      ||path.startsWith('/operations/')||input.method==='POST'&&actions.includes(input.body?.action))return sprint.dispatch(input);
    return base.dispatch(input);
  };return{...sprint,dispatch};
}
for(const native of [false,true])test.describe(native?'native Sprint workspace':'web Sprint workspace',()=>{
  test('one mounted Sprint presents fixed facts and explicit cancellation',async({page})=>{
    const f=fullFixture();await mountWorkspace(page,native,f.dispatch);await page.goto(route);
    const region=page.locator('[data-pm-sprints]');await expect(region).toHaveCount(1);
    await expect(region).toContainText('A returning member can sign in');await expect(region).toContainText('Relevant tests verify the fixed change');
    await expect(region.getByRole('tab')).toHaveCount(0);await region.locator('summary').filter({hasText:/^Cancel Sprint$/}).click();
    const form=region.locator('[data-pm-action="pm.sprint.cancel"]');await form.getByLabel('Reason for this decision',{exact:true}).fill('The selected Goal is no longer useful');
    await form.getByRole('button',{name:'Cancel Sprint',exact:true}).click();await expect(region.locator('[data-pm-sprint-closure]')).toContainText('DoD has not been assessed');
    expect(f.state.writes.map((request:any)=>request.body.action)).toEqual(['pm.sprint.cancel']);
  });
  test('lost start is recovered after whole-page reload with original identity',async({page})=>{
    const f=fullFixture();await mountWorkspace(page,native,f.dispatch);await page.goto(route);const region=page.locator('[data-pm-sprints]');
    await region.locator('summary').filter({hasText:/^Start Sprint$/}).click();const form=region.locator('[data-pm-action="pm.sprint.start"]');
    await form.getByLabel('Reason for this decision',{exact:true}).fill('Start the selected work period');f.state.lost=true;
    await form.getByRole('button',{name:'Start Sprint',exact:true}).click();await expect(form).toContainText('Reply unavailable');
    await page.reload();await form.getByRole('button',{name:'Check original request',exact:true}).click();
    expect(f.state.ops.size).toBe(1);expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
    expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
  });
  test('reader sees fixed Goal and cannot decide the Sprint',async({page})=>{
    const f=fullFixture();f.state.role='reader';f.state.manage=false;await mountWorkspace(page,native,f.dispatch);await page.goto(route);
    const region=page.locator('[data-pm-sprints]');await expect(region).toContainText('A returning member can sign in');
    await region.locator('summary').filter({hasText:/^Cancel Sprint$/}).click();await expect(region.locator('[data-pm-action="pm.sprint.cancel"] button[type=submit]')).toBeDisabled();
    expect(f.state.writes).toHaveLength(0);
  });
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`mounted Sprint ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:1000}}),page=await context.newPage(),f=fullFixture();
  await mountConversation(page,false,f.dispatch);await page.goto(route);const region=page.locator('[data-pm-sprints]');
  await expect(region.locator('h2')).toHaveText(locale==='en'?'Sprint and iteration':'Sprint 與迭代');
  expect(await region.innerText()).not.toMatch(/pm_sprint_|\bundefined\b/);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const directory=process.env.BATC_PM_SPRINT_WORKSPACE_SCREENSHOTS;if(directory){await mkdir(directory,{recursive:true});await page.screenshot({path:`${directory}/sprint-${locale}-${width}.png`,fullPage:true});}
  await context.close();
});
