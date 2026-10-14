import {openPmSection, expectPmWithdrawn} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {mountConversation} from './conversation-fixture.ts';
import {policyFixture,policyProfile,policyRoute,workspaceId} from './pm-maintenance-policy-fixture.ts';
const section=(page:any)=>page.locator('[data-pm-maintenance-policy]');
const form=(page:any)=>page.locator('[data-pm-action="pm.maintenance_policy.adopt"]');
const save=(page:any)=>form(page).getByRole('button',{name:'Adopt maintenance policy',exact:true});
async function openEditor(page:any){await page.locator('[data-maintenance-editor] > summary').click();}
async function fillDraft(page:any){
  await openEditor(page);
  await page.locator('[data-policy-group="pmp_verification"] > summary').click();
  await form(page).getByLabel('Required acceptance evidence (one per line)',{exact:true}).fill('Verify the affected release in an owned workspace');
  await page.locator('[data-policy-group="pmp_schedule"] > summary').click();
  await form(page).getByLabel('GitHub workload coverage',{exact:true}).selectOption(policyProfile);
  await form(page).getByLabel('Reason for this policy',{exact:true}).fill('Keep the supported release observable');
}
for(const native of [false,true])test.describe(native?'Native policy':'Web policy',()=>{
  test('new draft is disabled and report only; adoption records exact reviewed versions and no execution',async({page})=>{
    const f=policyFixture();await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await fillDraft(page);
    await expect(section(page)).toContainText('Adopting a policy saves your choices');
    await expect(form(page).getByLabel('Policy permission',{exact:true})).toHaveValue('false');
    await expect(form(page).getByLabel('After release',{exact:true})).toHaveValue('manual');
    await expect(form(page).getByLabel('Maximum delivery',{exact:true})).toHaveValue('report');
    expect(f.state.writes).toHaveLength(0);
    await save(page).click();await expect(section(page).locator('[data-maintenance-current]')).toContainText('Policy revision 1');
    expect(f.state.writes).toHaveLength(1);
    expect(f.state.writes[0].body).toEqual({action:'pm.maintenance_policy.adopt',target:{workspace_id:workspaceId},
      params:{policy:f.documentPolicy(),rationale:'Keep the supported release observable'},
      preconditions:{expected_policy_revision:0,expected_previous_policy_id:null,expected_baseline_id:f.release.state.baselines[0].baseline_id,expected_lifecycle_revision:0}});
    expect(f.state.policies[0].document.automatic_execution_started).toBe(false);expect(f.base.state.writes).toHaveLength(0);
    await expect(form(page).getByLabel('Reason for this policy',{exact:true})).toHaveValue('');
  });
  test('precise GitHub coverage requires an explicit selection and legacy policy is not silently upgraded',async({page})=>{
    const f=policyFixture(),old:any=f.documentPolicy();delete old.observation.github_profile;f.addPolicy(old);
    await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await openEditor(page);
    await page.locator('[data-policy-group="pmp_schedule"] > summary').click();
    await expect(form(page).getByLabel('GitHub workload coverage',{exact:true})).toHaveValue('');
    await form(page).getByLabel('Reason for this policy',{exact:true}).fill('Review the narrower source scope');
    await save(page).click();await expect(form(page)).toContainText('Choose the available GitHub workload coverage');expect(f.state.writes).toHaveLength(0);
    await section(page).getByText('Full policy and original versions',{exact:true}).click();
    await expect(section(page)).toContainText('Repository-wide coverage (not established)');await expect(section(page)).toContainText('A narrower observation cannot establish it');
    await form(page).getByLabel('GitHub workload coverage',{exact:true}).selectOption(policyProfile);await save(page).click();
    await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.params.policy.observation.github_profile).toBe(policyProfile);
  });
  for(const changed of ['baseline','policy','lifecycle'])test(`${changed} change keeps the draft fixed until explicit version review`,async({page})=>{
    const f=policyFixture();await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await fillDraft(page);
    if(changed==='baseline')f.release.addBaseline();else if(changed==='policy')f.addPolicy();else f.state.lifecycle.revision++;
    await page.getByRole('button',{name:'Refresh maintenance policy',exact:true}).click();
    await expect(save(page)).toBeDisabled();await expect(form(page).getByLabel('Reason for this policy',{exact:true})).toHaveValue('Keep the supported release observable');
    await expect(form(page)).toContainText('Your draft still uses its original versions');
    await page.getByRole('button',{name:'Use these reviewed versions',exact:true}).click();await save(page).click();
    await expect.poll(()=>f.state.writes.length).toBe(1);
    const pre=f.state.writes[0].body.preconditions;
    expect(pre.expected_baseline_id).toBe(f.release.state.baselines.at(-1)!.baseline_id);
    expect(pre.expected_policy_revision).toBe(changed==='policy'?1:0);expect(pre.expected_lifecycle_revision).toBe(changed==='lifecycle'?1:0);
  });
  test('unknown reply retains original key and envelope across changed versions and a closed lifecycle',async({page})=>{
    const f=policyFixture();await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await fillDraft(page);f.state.lost=true;
    await save(page).click();await expect(form(page)).toContainText('Reply unavailable');
    f.release.addBaseline();f.state.lifecycle={stage:'closed',status:'closed',revision:1};
    await page.reload();await openPmSection(page, 'dependencies' , 'ui_policy');await openEditor(page);await expect(form(page).getByLabel('Reason for this policy',{exact:true})).toBeDisabled();
    await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(form(page).getByLabel('Reason for this policy',{exact:true})).toHaveValue('');
    expect(f.state.writes).toHaveLength(2);expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
    expect(f.state.policies).toHaveLength(1);await expect(save(page)).toBeDisabled();
  });
  test('acknowledged original is recovered by ID after owner becomes a read-only member',async({page})=>{
    const f=policyFixture();f.state.operationStatus='accepted';await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await fillDraft(page);
    await save(page).click();await expect(form(page).locator('.pm-receipt')).toContainText('accepted');
    f.base.state.role='reader';await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect(page.locator('[data-maintenance-editor]')).toBeVisible();
    await expect(form(page).getByRole('button',{name:'Check original request',exact:true})).toBeEnabled();
    [...f.state.ops.values()][0].status='succeeded';await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(page.locator('[data-maintenance-editor]')).toBeHidden();expect(f.state.writes).toHaveLength(1);expect(f.state.receiptReads).toBeGreaterThan(0);
  });
  test('source withdrawal removes facts and retains the local draft through remount',async({page})=>{
    const f=policyFixture();f.addPolicy();await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await fillDraft(page);
    f.state.fail=403;await page.getByRole('button',{name:'Refresh maintenance policy',exact:true}).click();
    await expect(section(page)).toContainText('Policy sources could not be verified');await expect(section(page).locator('[data-maintenance-policy]')).toHaveCount(0);
    await expect(section(page).locator('[data-maintenance-source]')).toBeEmpty();await expect(save(page)).toBeDisabled();
    await expect(form(page).getByLabel('Reason for this policy',{exact:true})).toHaveValue('Keep the supported release observable');
    await page.reload();await openPmSection(page, 'dependencies' , 'ui_policy');await openEditor(page);await expect(form(page).getByLabel('Reason for this policy',{exact:true})).toHaveValue('Keep the supported release observable');
    f.state.fail=null;await page.getByRole('button',{name:'Refresh maintenance policy',exact:true}).click();await expect(save(page)).toBeEnabled();
  });
  test('late history cannot restore source facts after the parent loses access',async({page})=>{
    const f=policyFixture();f.addPolicy();await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');
    let release!:()=>void;f.state.deferHistory=new Promise(resolve=>{release=resolve;});
    await section(page).getByText('Policy history',{exact:true}).click();await expect.poll(()=>f.state.reads.some(path=>path.includes('?after='))).toBe(true);
    f.base.state.failRead=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(page.locator('.pm-status')).toContainText('PM read unavailable');release();
    await expect(section(page)).toHaveJSProperty('hidden',true);await expectPmWithdrawn(page,'dependencies','[data-pm-maintenance-policy]','ui_policy');await expect(section(page).locator('[data-maintenance-policy]')).toHaveCount(0);
    f.state.deferHistory=null;f.base.state.failRead=false;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(section(page)).toBeVisible();
  });
  test('request-side rejection withdraws sources without forgetting the frozen original request',async({page})=>{
    const f=policyFixture();f.addPolicy();await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await fillDraft(page);f.state.rejectWrite=true;
    await save(page).click();await expect(section(page)).toContainText('Policy sources could not be verified');
    await expect(section(page).locator('[data-maintenance-policy]')).toHaveCount(0);await expect(form(page).getByLabel('Reason for this policy',{exact:true})).toBeDisabled();
    await expect(form(page).getByLabel('Reason for this policy',{exact:true})).toHaveValue('Keep the supported release observable');expect(f.state.writes).toHaveLength(1);
  });
  test('filtered empty history remains pageable and hidden head is not an empty policy',async({page})=>{
    const f=policyFixture();f.addPolicy();f.state.emptyHistoryPage=true;
    await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await section(page).getByText('Policy history',{exact:true}).click();
    await page.getByRole('button',{name:'Load more policies',exact:true}).click();await expect(section(page).locator('[data-maintenance-policy]')).toHaveCount(2);
    await expect(section(page)).toContainText('partial history');f.state.hidden=true;await page.getByRole('button',{name:'Refresh maintenance policy',exact:true}).click();
    await expect(section(page).locator('[data-maintenance-current]')).toContainText('original sources are outside your current access');await openEditor(page);await expect(save(page)).toBeDisabled();
    await expect(section(page).locator('[data-maintenance-current]')).not.toContainText('No maintenance policy');
  });
  test('non-owner admin sees records without adoption permission',async({page})=>{
    const f=policyFixture();f.addPolicy();f.base.state.role='contributor';
    const dispatch=async(input:any)=>{const result=await f.dispatch(input);if(input.path==='/capabilities'||input.path==='/bootstrap'){
      (input.path==='/bootstrap'?result.data.capabilities:result.data).scopes.push('admin');}return result;};
    await mountConversation(page,native,dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await expect(section(page).locator('[data-maintenance-current]')).toContainText('Policy revision 1');
    await expect(page.locator('[data-maintenance-editor]')).toBeHidden();expect(f.state.writes).toHaveLength(0);
  });
  test('paused lifecycle permits draft editing and prevents new policy adoption',async({page})=>{
    const f=policyFixture();f.state.lifecycle={stage:'paused',status:'paused',revision:1};await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await fillDraft(page);
    await expect(form(page)).toContainText('New policy decisions are paused or closed');await expect(save(page)).toBeDisabled();expect(f.state.writes).toHaveLength(0);
  });
  test('unreadable baseline or mismatched current response cannot enable adoption',async({page})=>{
    const f=policyFixture();f.release.state.hidden=true;await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await openEditor(page);
    await expect(form(page)).toContainText('current release baseline is outside your access');await expect(save(page)).toBeDisabled();
    f.release.state.hidden=false;f.state.mismatch=true;await page.getByRole('button',{name:'Refresh maintenance policy',exact:true}).click();
    await expect(section(page)).toContainText('does not match this workspace');await expect(save(page)).toBeDisabled();expect(f.state.writes).toHaveLength(0);
  });
  test('repairs require branch proof and a PR ceiling; precise decimal budgets stay integer millionths',async({page})=>{
    const f=policyFixture();await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await fillDraft(page);
    await page.locator('[data-policy-group="pmp_scope"] > summary').click();await form(page).getByLabel('Permitted repair kinds: Bug fixes',{exact:true}).check();
    await save(page).click();await expect(form(page)).toContainText('Repairs need a verified maintenance branch');expect(f.state.writes).toHaveLength(0);
    f.release.state.baselines[0].document.proof.maintenance_branch={status:'verified_at_observation',ref:'refs/heads/release/1.x',head_sha:'b'.repeat(40)};
    await page.getByRole('button',{name:'Refresh maintenance policy',exact:true}).click();
    await form(page).getByLabel('Maximum delivery',{exact:true}).selectOption('pull_request');
    await form(page).getByLabel('Repository paths (one per line)',{exact:true}).fill('src/\ndocs/guide.md');
    await page.locator('[data-policy-group="pmp_limits"] > summary').click();await form(page).getByLabel('Cost budget (USD, optional)',{exact:true}).fill('0.000001');
    await save(page).click();await expect.poll(()=>f.state.writes.length).toBe(1);
    expect(f.state.writes[0].body.params.policy.allowed_work_kinds).toEqual(['bug_fix']);expect(f.state.writes[0].body.params.policy.limits.cost_microusd).toBe(1);
    expect(f.state.writes[0].body.params.policy.allowed_paths).toEqual(['src/','docs/guide.md']);
  });
  test('unsupported paths, fractional limits and invalid schedules remain local validation errors',async({page})=>{
    const f=policyFixture();await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await fillDraft(page);
    await page.locator('[data-policy-group="pmp_scope"] > summary').click();await page.locator('[data-policy-group="pmp_limits"] > summary').click();
    for(const [label,value,restore] of [['Repository paths (one per line)','../foreign','.'],['Repository paths (one per line)','src/**','.'],
      ['Maximum concurrent work','1.5','1'],['Check interval (seconds)','0','3600'],['Time zone (IANA)','Missing/Place','UTC'],['Weekly review time','25:30','09:00'],['Cost budget (USD, optional)','0.0000001','']]){
      await form(page).getByLabel(label,{exact:true}).fill(value);await save(page).click();await expect(form(page)).toContainText('Review all policy fields');
      expect(f.state.writes).toHaveLength(0);await form(page).getByLabel(label,{exact:true}).fill(restore);
    }
  });
  test('older central retains readable policy and original broad scope without unsupported profile fields',async({page})=>{
    const f=policyFixture();f.state.profiles=false;const old:any=f.documentPolicy();delete old.observation.github_profile;f.addPolicy(old);
    await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await openEditor(page);
    await page.locator('[data-policy-group="pmp_schedule"] > summary').click();await expect(form(page)).toContainText('Repository-wide coverage remains unknown');
    await expect(form(page).getByLabel('GitHub workload coverage',{exact:true})).toBeHidden();
    await form(page).getByLabel('Reason for this policy',{exact:true}).fill('Preserve observation-only policy');await save(page).click();
    await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.params.policy.observation).toEqual({max_age_seconds:1800,stable_for_seconds:300});
  });
  test('feature gate keeps older central free of unsupported reads',async({page})=>{
    const f=policyFixture();f.state.enabled=false;await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');
    await expect(page.locator('.shell-workspace-block')).toContainText('Library refresh');await expect(section(page)).toHaveCount(0);expect(f.state.reads).toHaveLength(0);
  });
});
for(const language of ['en','zh-TW'])for(const width of [390,768,1440])test(`${language} maintenance policy at ${width}px`,async({browser,baseURL},testInfo)=>{
  const context=await browser.newContext({baseURL,locale:language,viewport:{width,height:1000}}),page=await context.newPage(),f=policyFixture();f.addPolicy();
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await mountConversation(page,false,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await openEditor(page);
  await page.locator('[data-policy-group="pmp_scope"] > summary').click();await page.locator('[data-policy-group="pmp_schedule"] > summary').click();
  await expect(section(page)).toContainText(language==='en'?'Open PR heads and GitHub Actions':'開啟中的 PR 固定 head 與 GitHub Actions');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);
  const folder=process.env.PM_SCREENSHOTS||testInfo.outputPath('screenshots');mkdirSync(folder,{recursive:true});
  await section(page).screenshot({path:`${folder}/policy-${language}-${width}.png`,style:'.top { position: static !important; }'});await context.close();
});
for(const native of [false,true])test(`${native?'Native':'Web'} Chinese adoption has the same explicit fixed policy`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale:'zh-TW'}),page=await context.newPage(),f=policyFixture();
  await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await openPmSection(page, 'dependencies' , 'ui_policy');await openEditor(page);
  await page.locator('[data-policy-group="pmp_verification"] > summary').click();await form(page).getByLabel('必要驗收證據（每行一項）',{exact:true}).fill('使用自有工作區核對受影響的發布版本');
  await page.locator('[data-policy-group="pmp_schedule"] > summary').click();await form(page).getByLabel('GitHub 工作觀察範圍',{exact:true}).selectOption(policyProfile);
  await form(page).getByLabel('採用此政策的理由',{exact:true}).fill('先維持觀察；保留 <script>來源文字</script>');await form(page).getByRole('button',{name:'採用維護政策',exact:true}).click();
  await expect(section(page).locator('[data-maintenance-current]')).toContainText('政策版本 1');expect(f.state.writes).toHaveLength(1);
  expect(f.state.writes[0].body.params.policy.enabled).toBe(false);expect(f.state.writes[0].body.params.policy.observation.github_profile).toBe(policyProfile);
  await section(page).getByText('完整政策與原始版本',{exact:true}).click();await expect(section(page)).toContainText('保留 <script>來源文字</script>');await expect(section(page).locator('script')).toHaveCount(0);await context.close();
});
