import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {mountConversation} from './conversation-fixture.ts';
import {checkFixture,policyRoute,workspaceId} from './pm-maintenance-checks-fixture.ts';
const section=(page:any)=>page.locator('[data-pm-maintenance-checks]');
const submit=(page:any)=>section(page).getByRole('button',{name:'Check once',exact:true});
for(const native of [false,true])test.describe(native?'Native maintenance checks':'Web maintenance checks',()=>{
  test('schedule and original children are readable and Check once fixes all decision versions',async({page})=>{
    const f=checkFixture();f.add('incomplete');f.state.coalesced=4;await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);
    await expect(section(page)).toContainText('Observation coverage incomplete');await expect(section(page)).toContainText('Earlier windows have no observation coverage');
    await expect(section(page)).toContainText('This does not establish idle workload');expect(f.state.writes).toHaveLength(0);
    await expect(section(page).locator('a[href^="#/op/"]')).toHaveCount(4);
    await submit(page).click();await expect.poll(()=>f.state.writes.length).toBe(1);
    expect(f.state.writes[0].body).toEqual({action:'pm.maintenance.check',target:{workspace_id:workspaceId},params:{policy_id:f.parent.state.policies[0].policy_id},
      preconditions:{expected_policy_version:1,expected_baseline_id:f.parent.release.state.baselines[0].baseline_id,expected_lifecycle_revision:0}});
    await expect(section(page)).toContainText('Observation coverage complete');expect(f.parent.state.writes).toHaveLength(0);
  });
  test('lost response recovers the same key across changed policy and paused stage',async({page})=>{
    const f=checkFixture();f.state.lost=true;await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await submit(page).click();
    await expect(section(page)).toContainText('Check reply unavailable');f.parent.addPolicy();f.parent.state.lifecycle={stage:'paused',status:'paused',revision:2};f.state.blocker='PM_MAINTENANCE_STAGE_STOPPED';
    await page.reload();await section(page).getByRole('button',{name:'Check original request',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(2);
    expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);expect(f.state.ops.size).toBe(1);
  });
  test('acknowledged original ID is recovered with GET and capacity remains explicit',async({page})=>{
    const f=checkFixture();f.state.operationStatus='waiting_external';await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await submit(page).click();
    await expect(section(page)).toContainText('waiting_external');await page.reload();
    await expect(section(page)).toContainText('Run capacity remains reserved');await section(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect.poll(()=>f.state.receiptReads).toBeGreaterThan(0);expect(f.state.writes).toHaveLength(1);
  });
  test('revoked current credential blocks new check but preserves original receipt recovery',async({page})=>{
    const f=checkFixture();f.state.lost=true;await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await submit(page).click();
    await expect(section(page)).toContainText('Check reply unavailable');f.state.blocker='PM_MAINTENANCE_AUTHORITY_UNAVAILABLE';await page.reload();
    await expect(section(page)).toContainText('original policy owner needs a current credential');
    await section(page).getByRole('button',{name:'Check original request',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(2);
    await expect(submit(page)).toBeDisabled();
  });
  test('readers see status without new admission, and exhausted capacity explains disabled action',async({page})=>{
    const f=checkFixture();f.add(null);f.parent.base.state.role='reader';await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);
    await expect(submit(page)).toBeDisabled();await expect(section(page)).toContainText('current workspace owner');
    f.parent.base.state.role='owner';f.state.blocker='PM_MAINTENANCE_BUDGET_EXHAUSTED';await page.reload();await expect(submit(page)).toBeDisabled();
    await expect(section(page)).toContainText('Concurrent or period run capacity is exhausted');expect(f.state.writes).toHaveLength(0);
  });
  test('Idea and disabled policies never offer new checks',async({page})=>{
    const f=checkFixture();f.parent.state.lifecycle.stage='idea';await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);
    await expect(submit(page)).toBeDisabled();await expect(section(page)).toContainText('New checks require Development or Maintain');
    f.parent.state.lifecycle.stage='development';f.parent.state.policies[0].document.policy.enabled=false;await section(page).getByRole('button',{name:'Refresh maintenance checks',exact:true}).click();
    await expect(submit(page)).toBeDisabled();await expect(section(page)).toContainText('Maintenance is disabled');expect(f.state.writes).toHaveLength(0);
    f.parent.state.policies[0].document.policy.enabled=true;f.parent.state.policies[0].document.scope.github_scope='repository';
    await section(page).getByRole('button',{name:'Refresh maintenance checks',exact:true}).click();await expect(submit(page)).toBeDisabled();
    await expect(section(page)).toContainText('Adopt the supported GitHub workload coverage');
  });
  test('hidden newest facts are not mistaken for an empty schedule or history',async({page})=>{
    const f=checkFixture();f.add();f.state.hidden=true;await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);
    await expect(section(page)).toContainText('latest record is outside your current source access');await expect(section(page)).not.toContainText('No maintenance check');
    await expect(submit(page)).toBeDisabled();await section(page).getByText('Check history',{exact:true}).click();await expect(section(page)).toContainText('partial history');
  });
  test('source withdrawal clears facts and late reads cannot restore them',async({page})=>{
    const f=checkFixture();f.add();await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await expect(section(page)).toContainText('Observation coverage complete');
    let release!:()=>void;f.state.defer=new Promise(resolve=>{release=resolve;});const before=f.state.reads.length;
    await section(page).getByRole('button',{name:'Refresh maintenance checks',exact:true}).click();await expect.poll(()=>f.state.reads.length).toBeGreaterThan(before);
    f.parent.state.fail=403;await page.getByRole('button',{name:'Refresh maintenance policy',exact:true}).click();
    await expect(page.locator('[data-pm-maintenance-policy]')).toContainText('Policy sources could not be verified');release();
    await expect(section(page).locator('[data-check-run]')).toHaveCount(0);await expect(submit(page)).toBeDisabled();
  });
  test('mismatched immutable context hides all facts and prevents admission',async({page})=>{
    const f=checkFixture(),row=f.add();row.document.baseline_id='pmrb_'+'e'.repeat(32);await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);
    await expect(section(page)).toContainText('check response does not match');await expect(section(page).locator('[data-check-run]')).toHaveCount(0);await expect(submit(page)).toBeDisabled();
  });
  test('partial empty history pages advance and never imply whole-period coverage',async({page})=>{
    const f=checkFixture();f.add('stopped');f.state.emptyPage=true;await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);
    await section(page).getByText('Check history',{exact:true}).click();await expect(section(page)).toContainText('partial history');
    await section(page).getByRole('button',{name:'Load more checks',exact:true}).click();await expect(section(page).locator('[data-check-history] [data-check-run]')).toHaveCount(1);
    expect(f.state.reads.some(path=>path.includes('after=10'))).toBe(true);await expect(section(page)).toContainText('original run reached its time limit');
  });
  test('changed server versions require explicit new intent and preserve the refused original',async({page})=>{
    const f=checkFixture();await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await expect(submit(page)).toBeEnabled();
    f.parent.addPolicy(f.parent.state.policies[0].document.policy);await submit(page).click();await expect(section(page)).toContainText('Check versions changed');
    await expect(section(page).getByRole('button',{name:'Check original request',exact:true})).toBeDisabled();
    await section(page).getByRole('button',{name:'Refresh maintenance checks',exact:true}).click();
    await section(page).getByRole('button',{name:'Edit a new request',exact:true}).click();await submit(page).click();await expect.poll(()=>f.state.writes.length).toBe(2);
    expect(f.state.writes[0].body.preconditions.expected_policy_version).toBe(1);expect(f.state.writes[1].body.preconditions.expected_policy_version).toBe(2);
    expect(f.state.writes[1].idempotency_key).not.toBe(f.state.writes[0].idempotency_key);expect(f.state.ops.size).toBe(1);
  });
  test('older central omits scheduling component and retains unavailable wording',async({page})=>{
    const f=checkFixture();f.state.available=false;await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);
    await expect(page.locator('[data-pm-maintenance-policy]')).toContainText('Automatic checks and repairs are not available');await expect(section(page)).toHaveCount(0);expect(f.state.reads).toHaveLength(0);
  });
});
for(const language of ['en','zh-TW'])for(const width of [390,768,1440])test(`maintenance checks ${language} ${width}`,async({browser,baseURL},testInfo)=>{
  const context=await browser.newContext({baseURL,locale:language,viewport:{width,height:1000}}),page=await context.newPage(),f=checkFixture();f.add('incomplete');f.state.coalesced=4;
  f.state.blocker='PM_MAINTENANCE_AUTHORITY_UNAVAILABLE';const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await mountConversation(page,false,f.dispatch);await page.goto(policyRoute);await expect(section(page)).toContainText(language==='en'?'Observation coverage incomplete':'本次觀察涵蓋不完整');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);
  const folder=process.env.PM_SCREENSHOTS||testInfo.outputPath('screenshots');mkdirSync(folder,{recursive:true});await page.locator('.top').evaluate(element=>element.remove());
  await section(page).screenshot({path:`${folder}/checks-${language}-${width}.png`});await context.close();
});
