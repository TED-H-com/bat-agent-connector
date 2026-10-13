import {openPmSection} from './pm-navigation.ts';
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
    const f=fullFixture();await mountWorkspace(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'schedule' , 'ui_sprints');
    const region=page.locator('[data-pm-sprints]');await expect(region).toHaveCount(1);
    await expect(region).toContainText('A returning member can sign in');await expect(region).toContainText('Relevant tests verify the fixed change');
    await expect(region.getByRole('tab')).toHaveCount(0);await region.locator('summary').filter({hasText:/^Cancel Sprint$/}).click();
    const form=region.locator('[data-pm-action="pm.sprint.cancel"]');await form.getByLabel('Reason for this decision',{exact:true}).fill('The selected Goal is no longer useful');
    await form.getByRole('button',{name:'Cancel Sprint',exact:true}).click();await expect(region.locator('[data-pm-sprint-closure]')).toContainText('DoD has not been assessed');
    expect(f.state.writes.map((request:any)=>request.body.action)).toEqual(['pm.sprint.cancel']);
  });
  test('lost start is recovered after whole-page reload with original identity',async({page})=>{
    const f=fullFixture();await mountWorkspace(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'schedule' , 'ui_sprints');const region=page.locator('[data-pm-sprints]');
    await region.locator('summary').filter({hasText:/^Start Sprint$/}).click();const form=region.locator('[data-pm-action="pm.sprint.start"]');
    await form.getByLabel('Reason for this decision',{exact:true}).fill('Start the selected work period');f.state.lost=true;
    await form.getByRole('button',{name:'Start Sprint',exact:true}).click();await expect(form).toContainText('Reply unavailable');
    await page.reload();await openPmSection(page, 'schedule' , 'ui_sprints');await form.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(2);
    expect(f.state.ops.size).toBe(1);expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
    expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
  });
  test('reader sees fixed Goal and cannot decide the Sprint',async({page})=>{
    const f=fullFixture();f.state.role='reader';f.state.manage=false;await mountWorkspace(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'schedule' , 'ui_sprints');
    const region=page.locator('[data-pm-sprints]');await expect(region).toContainText('A returning member can sign in');
    await region.locator('summary').filter({hasText:/^Cancel Sprint$/}).click();await expect(region.locator('[data-pm-action="pm.sprint.cancel"] button[type=submit]')).toBeDisabled();
    expect(f.state.writes).toHaveLength(0);
  });
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`mounted Sprint ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:1000}}),page=await context.newPage(),f=fullFixture();
  await mountConversation(page,false,f.dispatch);await page.goto(route);await openPmSection(page, 'schedule' , 'ui_sprints');const region=page.locator('[data-pm-sprints]');
  await expect(region.locator('h2')).toHaveText(locale==='en'?'Sprint and iteration':'Sprint 與迭代');
  expect(await region.innerText()).not.toMatch(/pm_sprint_|\bundefined\b/);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const directory=process.env.BATC_PM_SPRINT_WORKSPACE_SCREENSHOTS;if(directory){await mkdir(directory,{recursive:true});await page.screenshot({path:`${directory}/sprint-${locale}-${width}.png`,fullPage:true});}
  await context.close();
});

// Exercise the shared quality-to-Sprint selector in the actual browser/IPC mount.
import {qualityFixture,workspaceId as qualityWorkspaceId,workId as qualityWorkId,dodId as qualityDodId} from './pm-quality-reviews-fixture.ts';
function withQuality(){
  const sprint=fullFixture(),quality=qualityFixture();quality.state.actor='owner';
  const row=quality.save(true);sprint.state.sprints[0].status='active';sprint.state.elapsed=true;
  const replacements=[[qualityWorkspaceId,workspaceId],[qualityWorkId,sprint.bindings[0].work_item_id],
    [qualityDodId,sprint.state.artifacts.dod.revision_id],[row.context.work_ref.binding_id,sprint.bindings[0].binding_id]];
  const map=(value:any)=>JSON.parse(replacements.reduce((text,[from,to])=>text.split(from).join(to),JSON.stringify(value)));
  const originalDispatch=sprint.dispatch;
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path==='/bootstrap'||path==='/capabilities'){
      const result=await originalDispatch(input),caps=path==='/bootstrap'?result.data.capabilities:result.data;
      caps.features.pm_workspaces.quality_reviews={version:1};caps.actions.push(...["pm.quality.review.create","pm.quality.review.record","pm.quality.review.revoke"].map(action=>({action,allowed:true})));return result;
    }
    if(path.startsWith(`/pm/workspaces/${workspaceId}/quality-reviews/`))return quality.state.deny
      ?{status:404,data:{error:{code:'NOT_FOUND',message:'Original sources withdrawn'}}}
      :{status:200,data:{workspace_id:workspaceId,review:map(row)}};
    if(path==='/operations'||path.endsWith('/work-packages')||path.endsWith('/quality')||path.endsWith('/sprint-artifacts/dod')||path.endsWith('/evidence')||path.includes('/evidence/')||path.endsWith('/deliveries')||path.endsWith('/github-observations')){
      if(input.method==='POST')return originalDispatch(input);
      let translated=input.path;for(const[from,to]of replacements)translated=translated.split(to).join(from);
      return map(await quality.dispatch({...input,path:translated}));
    }
    return originalDispatch(input);
  };
  return {...sprint,dispatch,quality,row};
}
for(const native of [false,true])for(const locale of ['en','zh-TW'])test(`mounted original quality enters Sprint ${native?'IPC':'browser'} ${locale}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width:locale==='en'?1440:390,height:900}}),page=await context.newPage(),f=withQuality();
  await mountWorkspace(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'schedule' , 'ui_sprints');const quality=page.locator('[data-pm-quality-reviews]');
  await quality.getByLabel(locale==='en'?'Work item for review':'審查工作項目',{exact:true}).selectOption(f.bindings[0].work_item_id);
  await quality.locator('select').filter({has:page.locator(`option[value="${f.row.review_id}"]`)}).selectOption(f.row.review_id);
  await quality.getByRole('button',{name:locale==='en'?'Use this assessment':'使用這份評估',exact:true}).click();
  const region=page.locator('[data-pm-sprints]');await region.locator('summary').filter({hasText:locale==='en'?/^Close elapsed Sprint$/:/^結束已到期的 Sprint$/}).click();
  const form=region.locator('[data-pm-action="pm.sprint.close"]');
  await form.getByRole('button',{name:locale==='en'?'Include chosen assessment':'納入已選評估',exact:true}).click();
  await expect(form.locator('[data-pm-quality-picker]')).toContainText(f.row.context.native_result.candidate_commit);
  await form.getByLabel(locale==='en'?'Reason for this decision':'這項決定的理由',{exact:true}).fill('Preserve selected original review');
  await form.locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params.quality_refs).toEqual({[f.bindings[0].work_item_id]:{
    review_id:f.row.review_id,operation_id:f.row.assessment.operation_id,version:f.row.assessment.version,
    document_digest:f.row.assessment.document_digest,candidate_commit:f.row.context.native_result.candidate_commit}});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await context.close();
});
