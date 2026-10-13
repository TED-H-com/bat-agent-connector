import {openThreadBrief} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {pmFixture,pmRoute,workspaceId} from './pm-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
const sid='pmsnap_'+'a'.repeat(32),rid='pmrev_'+'b'.repeat(32);
function fixture(){
  const base=pmFixture(),state={snapshots:[] as any[],writes:[] as any[],ops:new Map<string,any>(),lost:false,headVersion:2,goal:'Keep the first useful action visible'};
  const make=()=>({snapshot_id:sid,sequence:1,workspace_id:workspaceId,title:'First visit review',as_of:1791720000,
    journal_cursor:8,snapshot_digest:'1'.repeat(64),view_digest:'2'.repeat(64),coverage:{sources:'saved'},visibility:{restricted:false},evidence:[],
    brief:{goals:{status:'accepted_sources',items:[{kind:'goal',text:state.goal,status:'user_confirmed',source:{kind:'plan_input',revision_id:rid,source_ref:{ref:'Owner direction'}}}]},
      delivery:{status:'quality_unassessed',items:[]},blockers:{status:'partial_source_states',items:[]},
      deviations:{status:'unassessed',items:[]},decisions:{status:'recorded_sources',items:[{kind:'decision',text:'Preserve other ideas as candidates',source:{kind:'plan_adoption',revision_id:rid}}]}}});
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname,endpoint='/pm/workspaces/'+workspaceId;
    if(path==='/capabilities'||path==='/bootstrap'){
      const response=await base.dispatch(input),caps=path==='/bootstrap'?response.data.capabilities:response.data;
      caps.features.pm_workspaces.brief_snapshots=true;caps.actions.push({action:'pm.snapshot.create',allowed:base.state.manage});return response;
    }
    if(path===endpoint+'/plans/head')return {status:200,data:{head:{workspace_id:workspaceId,version:state.headVersion}}};
    if(path===endpoint+'/snapshots')return {status:200,data:{workspace_id:workspaceId,snapshots:state.snapshots.map(({brief,evidence,...rest})=>rest),next_cursor:null}};
    if(path.startsWith(endpoint+'/snapshots/'))return {status:200,data:{workspace_id:workspaceId,snapshot:state.snapshots.at(-1)||null}};
    if(input.method==='GET'&&path.startsWith('/operations/'))return {status:200,data:{operation:[...state.ops.values()].find(op=>path.endsWith(op.operation_id))}};
    if(input.method==='POST'&&path==='/operations'&&input.body.action==='pm.snapshot.create'){
      state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
      if(!op){const snapshot=make();state.snapshots.push(snapshot);op={...input.body,actor:base.state.actor,idempotency_key:input.idempotency_key,
        operation_id:'op_'+'c'.repeat(32),status:'succeeded',result:{snapshot_id:snapshot.snapshot_id}};state.ops.set(input.idempotency_key,op);}
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status:200,data:{operation:op}};
    }return base.dispatch(input);
  };return{base,state,dispatch,make};
}
const form=(page:any)=>page.locator('[data-pm-action="pm.snapshot.create"]');
for(const native of [false,true])test.describe(native?'native Brief':'web Brief',()=>{
  test('one explicit snapshot renders all five questions with unassessed areas visible',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadBrief(page);
    await form(page).getByRole('button',{name:'Save project snapshot',exact:true}).click();
    await expect(page.locator('[data-pm-brief-section]')).toHaveCount(5);
    await expect(page.locator('[data-pm-brief]')).toContainText('Keep the first useful action visible');
    await expect(page.locator('[data-pm-brief-section="delivery"]')).toContainText('Definition of Done has not been assessed');
    await expect(page.locator('[data-pm-brief-section="deviations"]')).toContainText('have not been evaluated for drift');
    expect(f.state.writes[0].body).toEqual({action:'pm.snapshot.create',target:{workspace_id:workspaceId},params:{},preconditions:{expected_plan_version:2}});
    expect(f.base.state.writes).toHaveLength(0);
    f.state.goal='A later unselected direction';f.state.headVersion=3;await page.reload();await openThreadBrief(page);
    await expect(page.locator('[data-pm-brief]')).toContainText('Keep the first useful action visible');
    await expect(page.locator('[data-pm-brief]')).not.toContainText('A later unselected direction');
  });
  test('lost snapshot response preserves original key and plan version across reload',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadBrief(page);f.state.lost=true;
    await form(page).getByRole('button',{name:'Save project snapshot',exact:true}).click();await expect(form(page)).toContainText('Reply unavailable');
    f.state.headVersion=4;await page.reload();await openThreadBrief(page);await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    expect(f.state.snapshots).toHaveLength(1);expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
    expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
  });
  test('refreshing the plan head disables new snapshot submission and preserves its draft',async({page})=>{
    const f=fixture();let hold=false,held=false,release:()=>void=()=>{};
    const gate=new Promise<void>(resolve=>release=resolve);
    const dispatch=async(input:any)=>{
      const result=await f.dispatch(input);
      if(new URL(input.path,'http://fixture').pathname===`/pm/workspaces/${workspaceId}/plans/head`&&hold){held=true;await gate;}
      return result;
    };
    await mountConversation(page,native,dispatch);await page.goto(pmRoute);await openThreadBrief(page);
    const editor=form(page),button=editor.getByRole('button',{name:'Save project snapshot',exact:true});
    await expect(button).toBeEnabled();await editor.locator('summary').click();
    const title=editor.getByLabel('Snapshot title',{exact:true});await title.fill('Original review title');
    hold=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();
    try{
      await expect.poll(()=>held).toBe(true);await expect(button).toBeDisabled();
      await expect(title).toBeDisabled();await expect(title).toHaveValue('Original review title');
      await editor.evaluate((node:HTMLFormElement)=>node.requestSubmit());
      expect(f.state.writes).toHaveLength(0);
    }finally{release();}
    await button.click();await expect(page.locator('[data-pm-brief-section]')).toHaveCount(5);
    expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].body.params).toEqual({title:'Original review title'});
    expect(f.state.writes[0].body.preconditions).toEqual({expected_plan_version:2});
  });
  test('reader sees fixed Brief without permission to save a new snapshot',async({page})=>{
    const f=fixture();f.state.snapshots.push(f.make());f.base.state.role='reader';f.base.state.manage=false;
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadBrief(page);
    await expect(page.locator('[data-pm-brief-section]')).toHaveCount(5);await expect(form(page).getByRole('button')).toBeDisabled();
    expect(f.state.writes).toHaveLength(0);
  });
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`Brief layout ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({locale,baseURL,viewport:{width,height:1100}}),page=await context.newPage();
  const f=fixture();f.state.snapshots.push(f.make());await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openThreadBrief(page);
  await expect(page.locator('[data-pm-brief-section]')).toHaveCount(5);
  await expect(page.locator('[data-pm-brief] h2')).toHaveText(locale==='en'?'Project Brief':'專案總覽');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(await page.locator('[data-pm-brief]').innerText()).not.toMatch(/\b(undefined|null)\b/);
  const out=process.env.BATC_PM_BRIEF_SCREENSHOTS;if(out){await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/brief-${locale}-${width}.png`,fullPage:true});}
  await context.close();
});
test('legacy workspace brief keeps its complete snapshot form in Report and recovers the original key',async({page})=>{
  const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(`/dashboard/#/pm/${workspaceId}/overview`);
  await page.locator('[data-pm-report-menu]').click();await page.locator('[data-menu-key=versions]').click();
  f.state.lost=true;await form(page).getByRole('button',{name:'Save project snapshot',exact:true}).click();
  await expect(form(page)).toContainText('Reply unavailable');f.state.headVersion=4;
  await page.reload();await page.locator('[data-pm-report-menu]').click();await page.locator('[data-menu-key=versions]').click();
  await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
  expect(f.state.snapshots).toHaveLength(1);expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
  expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
});
