import {openPmSection} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {policyFixture,policyRoute,workspaceId} from './pm-maintenance-policy-fixture.ts';
const form=(page:any)=>page.locator('[data-pm-action="pm.maintenance_policy.adopt"]');
function fixture(){
  const base=policyFixture(),policy=base.addPolicy();
  const item={finding_id:'pmfn_'+'1'.repeat(32),native:{kind:'issues',id:501,number:50,repository_id:7,provider_digest:'a'.repeat(64)},facts:{labels:['bug']},existing_work:[],existing_pull_request:null};
  const state={fail:false,mismatch:false,reads:0,hold:null as null|Promise<void>,findings:{workspace_id:workspaceId,automatic_repair_authorized:false,visibility_filtered:false,
    observation_ref:{observation_id:'pmgh_'+'2'.repeat(32),document_digest:'b'.repeat(64)},sources:{
      issues:{status:'complete',findings:[item]},dependabot_pull_requests:{status:'disabled',findings:[]},dependabot_alerts:{status:'disabled',findings:[]}}}};
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path.endsWith('/maintenance-findings/current')){state.reads++;if(state.hold)await state.hold;
      return state.fail?{status:404,data:{error:{code:'NOT_FOUND',message:'Original finding unavailable'}}}:{status:200,data:{...structuredClone(state.findings),workspace_id:state.mismatch?'ws_'+'0'.repeat(32):workspaceId}};}
    if(path.endsWith('/workload-snapshots/current'))return {status:200,data:{workspace_id:workspaceId,snapshot:null,visibility_filtered:false}};
    if(path.endsWith('/github-observations/current'))return {status:200,data:{workspace_id:workspaceId,observation:null,visibility_filtered:false}};
    const result=await base.dispatch(input);
    if(path==='/capabilities'||path==='/bootstrap'){const caps=path==='/bootstrap'?result.data.capabilities:result.data;
      caps.features.pm_workspaces.workload={version:1};caps.features.pm_github_observation.maintenance_findings={version:1};}
    return result;
  };return {base,policy,state,dispatch};
}
for(const native of [false,true])test.describe(native?'native configured findings':'web configured findings',()=>{
  test('a reader can inspect exact adopted finding filters without an edit form',async({page})=>{
    const f=fixture();f.base.base.state.role='reader';
    f.policy.document.policy.observation.maintenance_sources={issues:{labels_all:[]},dependencies:{manifests:['package.json'],base_branch:'support/1.x',dependabot_actor_id:900,alerts:true,pull_requests:false}};
    await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page,'dependencies','ui_policy');
    await page.locator('[data-maintenance-current] details>summary').first().click();
    const read=page.locator('[data-maintenance-current] [data-finding-policy]');await expect(read).toBeVisible();
    await expect(read).toContainText('All open Issues');await expect(read).toContainText('package.json');await expect(read).toContainText('support/1.x');await expect(read).toContainText('900');
    await expect(page.locator('[data-maintenance-editor]')).toBeHidden();expect(f.base.state.writes).toHaveLength(0);
  });
  test('dependency observation fixes manifest, native bot and verified branch without enabling repairs',async({page})=>{
    const f=fixture(),branch='support/1.x';
    f.base.release.state.baselines[0].document.proof.maintenance_branch={status:'verified_at_observation',ref:'refs/heads/'+branch};
    await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page,'dependencies','ui_policy');
    await page.locator('[data-maintenance-editor]>summary').click();await page.locator('[data-policy-group="pmp_scope"]>summary').click();
    await form(page).getByLabel('Sources to check: Dependencies and upstream',{exact:true}).check();
    await page.locator('[data-policy-group="pmf_sources"]>summary').click();
    await form(page).getByLabel('Observe dependency sources',{exact:true}).selectOption('true');
    await form(page).getByLabel('Exact dependency manifest paths (one per line)',{exact:true}).fill('package.json\npackage-lock.json');
    await form(page).getByLabel('Adopted maintenance branch',{exact:true}).fill(branch);
    await form(page).getByLabel('Dependabot native Bot ID',{exact:true}).fill('900');
    await form(page).getByLabel('Read Dependabot alerts',{exact:true}).selectOption('true');
    await form(page).getByLabel('Reason for this policy',{exact:true}).fill('Read configured dependency evidence');
    await form(page).getByRole('button',{name:'Adopt maintenance policy',exact:true}).click();await expect.poll(()=>f.base.state.writes.length).toBe(1);
    const p=f.base.state.writes[0].body.params.policy;
    expect(p.observation.maintenance_sources).toEqual({issues:null,dependencies:{manifests:['package.json','package-lock.json'],base_branch:branch,dependabot_actor_id:900,alerts:true,pull_requests:false}});
    expect(p.allowed_work_kinds).toEqual([]);expect(p.delivery.maximum).toBe('report');
  });
  test('explicit source decision preserves all filters and original lost request',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page,'dependencies','ui_policy');
    await page.locator('[data-maintenance-editor]>summary').click();await page.locator('[data-policy-group="pmf_sources"]>summary').click();
    await form(page).getByLabel('Observe matching Issues',{exact:true}).selectOption('true');
    await form(page).getByLabel('Required Issue labels (one per line)',{exact:true}).fill('bug\nreleased');
    await form(page).getByLabel('Reason for this policy',{exact:true}).fill('Observe only the accepted release issue labels');f.base.state.lost=true;
    await form(page).getByRole('button',{name:'Adopt maintenance policy',exact:true}).click();await expect(form(page)).toContainText('Reply unavailable');
    expect(f.base.state.writes[0].body.params.policy.observation.maintenance_sources).toEqual({issues:{labels_all:['bug','released']},dependencies:null});
    const original=structuredClone(f.base.state.writes[0]);f.base.state.lifecycle.stage='paused';await page.reload();await openPmSection(page,'dependencies','ui_policy');
    await page.locator('[data-maintenance-editor]>summary').click();await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect.poll(()=>f.base.state.writes.length).toBe(2);expect(f.base.state.writes[1]).toEqual(original);
  });
  test('saved original findings show coverage and clear after source revocation',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page,'dependencies','ui_policy');
    const view=page.locator('[data-maintenance-findings]');await view.getByText('Issue #50',{exact:true}).click();
    await expect(view).toContainText('Whether this affects the released version remains unproven.');expect(f.base.state.writes).toHaveLength(0);
    f.state.fail=true;await page.getByRole('button',{name:'Refresh saved observations',exact:true}).click();
    await expect(view).toHaveCount(0);await expect(page.locator('[data-pm-maintenance-observations]')).not.toContainText('Issue #50');
  });
  test('mismatched observation and pending source response cannot restore facts',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page,'dependencies','ui_policy');await expect(page.locator('[data-maintenance-findings]')).toBeVisible();
    let release!:()=>void;f.state.hold=new Promise(resolve=>release=resolve);const before=f.state.reads;
    await page.getByRole('button',{name:'Refresh saved observations',exact:true}).click();await expect.poll(()=>f.state.reads).toBeGreaterThan(before);
    f.base.state.fail=403;await page.getByRole('button',{name:'Refresh maintenance policy',exact:true}).click();release();
    await expect(page.locator('[data-maintenance-findings]')).toHaveCount(0);expect(f.base.state.writes).toHaveLength(0);
  });
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`configured findings ${locale} ${width}`,async({browser,baseURL},info)=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=fixture();
  await mountConversation(page,false,f.dispatch);await page.goto(policyRoute);await openPmSection(page,'dependencies','ui_policy');await expect(page.locator('[data-maintenance-findings]')).toBeVisible();
  await page.locator('[data-maintenance-editor]>summary').click();await page.locator('[data-policy-group="pmf_sources"]>summary').click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(await page.locator('main').innerText()).not.toMatch(/pmf_[a-z_]+/);
  await page.screenshot({path:info.outputPath(`findings-${locale}-${width}.png`),fullPage:true});await context.close();
});
