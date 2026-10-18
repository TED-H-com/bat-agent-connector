import {openThreadAnalysis} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {nativeFixture} from './pm-analysis-fixture.ts';
import {pmRoute,workspaceId} from './pm-fixture.ts';
import {proposalFixture,PROPOSE,ADOPT} from './pm-plan-proposals-fixture.ts';
import {structuredCapability,BAT,NATIVE} from './pm-structured-analysis-fixture.ts';

const ACTION='pm.workspace.adapter.select';
function fixture(){
  const f=nativeFixture(),proposal=proposalFixture();
  Object.assign(f.state,{actor:'owner',plans:true,bound:true});f.bat.available=true;
  const selection={supported:true,choice:null as any,lost:false,reason:null as string|null,reads:0};
  const pre=()=>({expected_selection_version:selection.choice?.version||0,expected_writer_generation:3,
    expected_plan_version:0,expected_accepted_revision_id:null,expected_continuity_digest:'a'.repeat(64)});
  const fixed=(adapter:string,version=1)=>({workspace_id:workspaceId,adapter,version,operation_id:'op_'+'c'.repeat(32),
    rationale:'Original owner choice',execution_authorized:false,successor_started:false,runtime_preflight:'not_performed',
    writer_generation:3,continuity:{accepted_context_ref:null,source_operation_ids:[]}});
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path.includes('/conversation-proposals')||input.body?.action?.startsWith('pm.conversation.'))return proposal.dispatch(input);
    if(input.method==='POST'&&input.body?.action===ACTION){
      f.state.writes.push(structuredClone(input));
      let operation=f.state.ops.get(input.idempotency_key);
      if(!operation){selection.choice=fixed(input.body.params.adapter,(selection.choice?.version||0)+1);
        operation={...structuredClone(input.body),actor:f.state.actor,idempotency_key:input.idempotency_key,
          operation_id:selection.choice.operation_id,status:'succeeded',result:selection.choice};f.state.ops.set(input.idempotency_key,operation);}
      if(selection.lost){selection.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Original choice reply unavailable'}}};}
      return {status:200,data:{operation}};
    }
    const value=await f.dispatch(input);
    if(path==='/bootstrap'||path==='/capabilities'){
      const caps=path==='/bootstrap'?value.data.capabilities:value.data;
      caps.features.pm_workspaces.conversation_proposals={version:1};
      caps.actions.push(...[PROPOSE,ADOPT,ACTION].map(action=>({action,allowed:f.state.manage})));
      if(selection.supported)caps.features.pm_workspaces.workspace_adapter={version:1,action:ACTION};
    }
    if(path===`/pm/workspaces/${workspaceId}/capabilities`&&value.status===200){
      selection.reads++;value.data.structured_analysis=structuredCapability({native:true});
      if(selection.supported)value.data.workspace_adapter={version:1,action:ACTION,adapters:[NATIVE,BAT],choice:selection.choice,
        available:!selection.reason&&f.state.role==='owner',reason:selection.reason,preconditions:pre(),
        writer:{generation:3,occupied:selection.reason==='original_writer_unresolved'},
        runtime_preflight:'not_performed',execution_authorized:false};
    }
    return value;
  };
  return {...f,dispatch,selection,fixed,proposal};
}
const panel=(page:any)=>page.locator('[data-pm-workspace-adapter]');
const selector=(page:any)=>panel(page).locator('select');
const analysis=(page:any,action='pm.run.analyze')=>page.locator(`[data-pm-action="${action}"]`);
async function setup(browser:any,baseURL:any,native:boolean,locale:string,f:any){
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage();
  await mountConversation(page,native,f.dispatch);
  if(native)await page.addInitScript(caps=>{const ipc=(window as any).__TAURI_INTERNALS__,invoke=ipc.invoke;
    ipc.invoke=(command:string,args:any)=>command==='connector_connect'?Promise.resolve(caps):invoke(command,args);
  },(await f.dispatch({method:'GET',path:'/capabilities'})).data);
  await page.goto(pmRoute);await openThreadAnalysis(page);return {page,context};
}

for(const native of [false,true])for(const locale of ['en','zh-TW']){
  test(`workspace choice ${native?'Tauri':'Web'} ${locale} saves one local receipt then routes new analysis`,async({browser,baseURL},info)=>{
    const f=fixture(),{page,context}=await setup(browser,baseURL,native,locale,f);
    try{
      await expect(panel(page).locator('summary')).toHaveText(locale==='zh-TW'?'工作區 PM 執行方式':'Workspace PM runtime');
      await panel(page).locator('summary').click();await expect(selector(page)).toHaveValue('');
      await page.screenshot({path:info.outputPath(`before-${locale}.png`),fullPage:true});
      await selector(page).selectOption(BAT);await panel(page).locator('textarea').fill('Use the already selected BAT carrier.');
      await page.reload();await openThreadAnalysis(page);await expect(selector(page)).toHaveValue(BAT);await expect(panel(page).locator('textarea')).toHaveValue('Use the already selected BAT carrier.');
      if(!await panel(page).getAttribute('open'))await panel(page).locator('summary').click();
      await panel(page).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);
      expect(f.state.writes[0].body).toEqual({action:ACTION,target:{workspace_id:workspaceId},
        params:{adapter:BAT,rationale:'Use the already selected BAT carrier.'},preconditions:{expected_selection_version:0,
          expected_writer_generation:3,expected_plan_version:0,expected_accepted_revision_id:null,expected_continuity_digest:'a'.repeat(64)}});
      await expect(page.locator('[data-pm-analysis-adapter]')).toHaveValue('bat_analysis');
      await expect(page.locator('[data-pm-analysis-adapter]')).toBeDisabled();
      await expect(panel(page).locator('[data-pm-choice-current]')).toContainText(locale==='zh-TW'?'選擇版本 1':'Choice 1');
      for(const width of [390,768,1440]){
        await page.setViewportSize({width,height:900});await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
        await panel(page).scrollIntoViewIfNeeded();
        await page.screenshot({path:info.outputPath(`choice-${locale}-${width}.png`),fullPage:true});
      }
      await analysis(page,'pm.run.bat.analyze').locator('textarea').fill('Discuss the saved fixed sources.');
      await analysis(page,'pm.run.bat.analyze').locator('button[type=submit]').click();
      await expect.poll(()=>f.state.writes.length).toBe(2);expect(f.state.writes[1].body.action).toBe('pm.run.bat.analyze');
      f.state.failRead=true;await page.getByRole('button',{name:locale==='zh-TW'?'重新整理':'Refresh',exact:true}).first().click();
      await expect(panel(page)).toBeHidden();
      expect(f.state.writes).toHaveLength(2);
    }finally{await context.close();}
  });

  test(`workspace unknown key ${native?'Tauri':'Web'} ${locale} survives changed choice and reload`,async({browser,baseURL})=>{
    const f=fixture();f.selection.lost=true;const {page,context}=await setup(browser,baseURL,native,locale,f);
    try{
      await panel(page).locator('summary').click();await selector(page).selectOption(BAT);
      await panel(page).locator('textarea').fill('Recover this original choice only.');await panel(page).locator('button[type=submit]').click();
      await expect(panel(page)).toContainText('Original choice reply unavailable');
      const original=structuredClone(f.state.writes[0]);f.selection.choice=f.fixed(NATIVE,2);
      await page.reload();await openThreadAnalysis(page);if(!await panel(page).getAttribute('open'))await panel(page).locator('summary').click();
      await expect(selector(page)).toHaveValue(BAT);await expect(selector(page)).toBeDisabled();
      await panel(page).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(2);
      expect(f.state.writes[1]).toEqual(original);expect(f.state.ops.size).toBe(1);
      await expect(page.locator('[data-pm-analysis-adapter]')).toHaveValue('native_analysis');
    }finally{await context.close();}
  });

  test(`saved workspace BAT ${native?'Tauri':'Web'} ${locale} flows into proposal without a second choice`,async({browser,baseURL})=>{
    const f=fixture();f.selection.choice=f.fixed(BAT);const {page,context}=await setup(browser,baseURL,native,locale,f);
    try{
      const form=analysis(page,PROPOSE),runtime=form.locator('[data-pm-structured-analysis] select');
      await expect(runtime).toHaveValue(BAT);await expect(runtime).toBeDisabled();
      await page.locator('[data-discussion-panel-toggle]').click();
      await page.locator('[data-discussion-composer]').fill('Prepare a proposal using the saved workspace adapter.');
      await page.locator('[data-discussion-ask]').click();await expect.poll(()=>f.proposal.state.writes.length).toBe(1);
      expect(f.proposal.state.writes[0].body.params.adapter).toBe(BAT);expect(f.state.writes).toEqual([]);
    }finally{await context.close();}
  });
}

test('unconfigured legacy server keeps per-request controls; unknown writer blocks only a new workspace choice',async({browser,baseURL})=>{
  const f=fixture();f.selection.supported=false;const {page,context}=await setup(browser,baseURL,false,'en',f);
  try{
    await expect(panel(page)).toBeHidden();await expect(page.locator('[data-pm-analysis-adapter]')).toBeEnabled();
    f.selection.supported=true;f.selection.reason='original_writer_unresolved';await page.reload();await openThreadAnalysis(page);
    await panel(page).locator('summary').click();await expect(panel(page)).toContainText('Wait for the current PM analysis');
    await expect(selector(page)).toBeDisabled();expect(f.state.writes).toEqual([]);
  }finally{await context.close();}
});

test('pending original analysis stays on its native key when workspace changes to BAT',async({browser,baseURL})=>{
  const f=fixture();f.state.lost=true;const {page,context}=await setup(browser,baseURL,false,'en',f);
  try{
    await analysis(page).locator('textarea').fill('Original native analysis.');await analysis(page).locator('button[type=submit]').click();
    await expect(analysis(page)).toContainText('Reply unavailable');const original=structuredClone(f.state.writes[0]);
    f.selection.choice=f.fixed(BAT);await page.reload();await openThreadAnalysis(page);await expect(page.locator('[data-pm-analysis-adapter]')).toHaveValue('native_analysis');
    await expect(page.locator('[data-pm-analysis-adapter]')).toBeDisabled();
    await analysis(page).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(2);
    expect(f.state.writes[1]).toEqual(original);await expect(page.locator('[data-pm-analysis-adapter]')).toHaveValue('bat_analysis');
  }finally{await context.close();}
});
