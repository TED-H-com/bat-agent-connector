import {openPmSection} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {fixture,rid,bid,sid,path,qualityObservation,qualityRef} from './pm-roadmap-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
for(const native of [false,true])test.describe(native?'native Roadmap':'web Roadmap',()=>{
  test('baseline explicitly binds a stable milestone to fixed accepted requirements',async({page})=>{
    const f=fixture(false);await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');await page.locator('[data-pm-roadmap-editor] > summary').click();
    const form=page.locator('[data-pm-action="pm.roadmap.baseline.set"]');await form.getByRole('button',{name:'Add milestone',exact:true}).click();
    await form.getByLabel('Milestone',{exact:true}).fill('A usable membership flow');await form.getByLabel('Baseline date',{exact:true}).fill('2026-11-01');
    await form.getByText('Linked requirements',{exact:true}).click();await form.getByRole('checkbox',{name:'Members can sign in'}).check();
    await form.getByLabel('Reason for this change',{exact:true}).fill('Adopt the first delivery target');await form.getByRole('button',{name:'Adopt scheduling baseline',exact:true}).click();
    await expect(page.locator('[data-pm-milestone]')).toContainText('A usable membership flow');const request=f.state.writes[0].body;
    expect(request.preconditions).toEqual({expected_roadmap_version:0,expected_plan_version:2});expect(request.params.milestones[0]).toMatchObject({plan_revision_id:rid,requirement_ids:['login'],work_binding_ids:[],baseline_date:'2026-11-01'});
    expect(request.params.milestones[0].milestone_id).toMatch(/^ms_[a-f0-9]{16}$/);expect(f.base.state.writes).toHaveLength(0);
  });
  test('forecast and selected snapshot preserve baseline and unknown completion',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');await page.getByText('Update a forecast',{exact:true}).click();
    const form=page.locator('[data-pm-action="pm.roadmap.forecast.set"]');await form.getByLabel('Milestone',{exact:true}).selectOption('first_release');await form.getByLabel('Forecast date',{exact:true}).fill('2026-11-08');
    await form.getByLabel('Reason for this change',{exact:true}).fill('Known dependency needs more time');await form.getByRole('button',{name:'Save forecast',exact:true}).click();
    const row=page.locator('[data-pm-milestone]');await expect(row.locator('td').nth(0).locator('time')).toHaveAttribute('datetime','2026-11-01');await expect(row.locator('td').nth(1).locator('time')).toHaveAttribute('datetime','2026-11-08');await page.getByText('Review saved results',{exact:true}).click();
    const observe=page.locator('[data-pm-action="pm.roadmap.observe"]');await observe.getByRole('button',{name:'Use the current baseline',exact:true}).click();await observe.getByLabel('Saved project snapshot',{exact:true}).selectOption(sid);
    await observe.getByRole('button',{name:'Attach snapshot results',exact:true}).click();await expect(row).toContainText('Completion has not been assessed');
    expect(f.state.baseline.document.milestones[0].baseline_date).toBe('2026-11-01');expect(f.state.writes[1].body.params).toEqual({baseline_revision_id:bid,snapshot_id:sid});
  });
  test('lost forecast response retains exact request when baseline changes',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');await page.getByText('Update a forecast',{exact:true}).click();const form=page.locator('[data-pm-action="pm.roadmap.forecast.set"]');
    await form.getByLabel('Milestone',{exact:true}).selectOption('first_release');await form.getByLabel('Reason for this change',{exact:true}).fill('Date remains unknown');f.state.lost=true;
    await form.getByRole('button',{name:'Save forecast',exact:true}).click();await expect(form).toContainText('Reply unavailable');f.state.version=4;f.state.baseline.revision_id='pmrb_'+'e'.repeat(32);
    await page.reload();await openPmSection(page, 'schedule');await page.getByText('Update a forecast',{exact:true}).click();await form.getByRole('button',{name:'Check original request',exact:true}).click();
    expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
  });
  test('unsent baseline draft survives reload and successive saves use the new version',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');await page.locator('[data-pm-roadmap-editor] > summary').click();
    const form=page.locator('[data-pm-action="pm.roadmap.baseline.set"]');await form.getByLabel('Milestone',{exact:true}).fill('Retained draft');
    await page.reload();await openPmSection(page, 'schedule');await page.locator('[data-pm-roadmap-editor] > summary').click();await expect(form.getByLabel('Milestone',{exact:true})).toHaveValue('Retained draft');
    await form.getByLabel('Reason for this change',{exact:true}).fill('First explicit update');await form.getByRole('button',{name:'Adopt scheduling baseline',exact:true}).click();
    await expect(form.getByRole('button',{name:'Adopt scheduling baseline',exact:true})).toBeEnabled();
    await form.getByLabel('Baseline date',{exact:true}).fill('2026-11-03');await form.getByLabel('Reason for this change',{exact:true}).fill('Second explicit update');
    await form.getByRole('button',{name:'Adopt scheduling baseline',exact:true}).click();await expect(page.locator('[data-pm-milestone] td').first().locator('time')).toHaveAttribute('datetime','2026-11-03');
    expect(f.state.writes.map(row=>row.body.preconditions.expected_roadmap_version)).toEqual([1,2]);
    expect(f.state.writes[1].body.params.milestones[0].milestone_id).toBe('first_release');
  });
  test('historical associations and removed dependencies stay visible for explicit removal',async({page})=>{
    const f=fixture(),old='pmwb_'+'1'.repeat(32),latest='pmwb_'+'2'.repeat(32);
    f.state.baseline.document.milestones[0].work_binding_ids=[old];f.state.baseline.document.milestones[0].depends_on=['removed'];
    f.state.bindings=[{binding_id:latest,work_item_id:'wi_'+'1'.repeat(20),project_id:'proj_'+'1'.repeat(20),plan_revision_id:rid,requirement_ids:['login']}];
    await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');await page.locator('[data-pm-roadmap-editor] > summary').click();
    const form=page.locator('[data-pm-action="pm.roadmap.baseline.set"]');await form.getByText('Associated work',{exact:true}).click();
    await form.getByRole('checkbox',{name:new RegExp('Previously selected work association')}).uncheck();
    await form.getByRole('checkbox',{name:'Login implementation',exact:true}).check();
    await form.getByText('Depends on milestones',{exact:true}).click();await form.getByRole('checkbox',{name:new RegExp('Removed milestone dependency')}).uncheck();
    await form.getByLabel('Reason for this change',{exact:true}).fill('Explicitly replace the old association and remove stale dependency');
    await form.getByRole('button',{name:'Adopt scheduling baseline',exact:true}).click();await expect(form).toContainText('Saved');
    expect(f.state.writes[0].body.params.milestones[0]).toMatchObject({work_binding_ids:[latest],depends_on:[]});
  });
  test('reader observes date distinctions without changing the schedule',async({page})=>{
    const f=fixture();f.base.state.role='reader';f.base.state.manage=false;await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');await expect(page.locator('[data-pm-milestone]')).toContainText('Not specified');
    await page.locator('[data-pm-roadmap-editor] > summary').click();await expect(page.getByRole('button',{name:'Adopt scheduling baseline',exact:true})).toBeDisabled();expect(f.state.writes).toHaveLength(0);
  });
});
for(const native of [false,true])for(const locale of ['en','zh-TW'])test(`fixed milestone quality ${native?'Tauri':'Web'} ${locale} uses the original selected snapshot`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage();try{
    const f=fixture(),reads:string[]=[];f.state.observation=qualityObservation();
    await mountConversation(page,native,async input=>{reads.push(input.path);return f.dispatch(input);});await page.goto(path);await openPmSection(page, 'schedule');
    const row=page.locator('[data-pm-milestone]'),quality=row.locator('[data-pm-roadmap-quality]');
    await expect(quality).toContainText(locale==='en'?'Qualified by the selected assessments':'所選評估判定合格');
    await expect(quality).toContainText(locale==='en'?'1 of 1 requirements qualified':'1 項需求中有 1 項合格');
    await expect(quality).toContainText(locale==='en'?'current applicability has not been evaluated':'目前適用性尚未核對');
    await quality.locator('summary').click();await expect(quality).toContainText('login');await expect(quality).toContainText(qualityRef.document_digest);
    await expect(quality.locator('a')).toHaveAttribute('href',`#/op/${qualityRef.operation_id}`);
    await expect(row.locator('td').nth(0).locator('time')).toHaveAttribute('datetime','2026-11-01');await expect(row.locator('td').nth(1)).toHaveText(locale==='en'?'Not specified':'尚未指定');
    await page.reload();await openPmSection(page, 'schedule');await expect(quality).toContainText(locale==='en'?'1 of 1 requirements qualified':'1 項需求中有 1 項合格');
    expect(reads.some(value=>value.includes('/quality-reviews'))).toBe(false);expect(f.state.writes).toEqual([]);
  }finally{await context.close();}
});
for(const native of [false,true])test(`Roadmap ${native?'Tauri':'Web'} keeps rejection, incomplete evidence and legacy observations distinct`,async({page})=>{
  const f=fixture();f.state.observation=qualityObservation('not_qualified');await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');
  const row=page.locator('[data-pm-milestone]'),quality=row.locator('[data-pm-roadmap-quality]');await expect(quality).toContainText('Not qualified by the selected assessments');await expect(quality).toContainText('Definition of done: Rejected');
  f.state.observation=qualityObservation('unassessed',false);await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(quality).toContainText('Work contribution coverage is incomplete');await expect(quality).toContainText('Definition of done: Unassessed');await expect(quality).toContainText('0 of 1 requirements qualified');
  delete f.state.observation.document.milestones[0].actual.quality;await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(quality).toHaveCount(0);await expect(row).toContainText('Completion has not been assessed');await expect(row).not.toContainText(qualityRef.review_id);expect(f.state.writes).toEqual([]);
});
for(const native of [false,true])test(`Roadmap ${native?'Tauri':'Web'} withdraws original quality refs and recovers the same unchanged observation`,async({page})=>{
  const f=fixture(),original=qualityObservation();f.state.observation=structuredClone(original);let denied=false;
  await mountConversation(page,native,async input=>denied&&input.path.endsWith('/roadmap')?{status:403,data:{error:{code:'FORBIDDEN',message:'Original quality source withdrawn'}}}:f.dispatch(input));await page.goto(path);await openPmSection(page, 'schedule');
  const row=page.locator('[data-pm-milestone]'),quality=row.locator('[data-pm-roadmap-quality]');await expect(quality).toContainText('1 of 1 requirements qualified');
  delete f.state.observation.document.milestones[0].actual.quality;f.state.observation.document.milestones[0].actual.completion='unassessed';f.state.observation.document.milestones[0].coverage.inaccessible_sources=true;f.state.observation.view_digest='c'.repeat(64);
  await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(quality).toHaveCount(0);await expect(row).not.toContainText(qualityRef.document_digest);
  f.state.observation=structuredClone(original);await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(quality).toContainText('1 of 1 requirements qualified');
  denied=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(row).toHaveCount(0);await expect(page.locator('[data-pm-roadmap]')).not.toContainText(qualityRef.review_id);
  denied=false;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(quality).toContainText('1 of 1 requirements qualified');expect(f.state.writes).toEqual([]);
});
test('a selected quality summary from a different frozen scope is never labelled qualified',async({page})=>{
  const f=fixture();f.state.observation=qualityObservation();f.state.observation.document.milestones[0].actual.quality.scope_revision=99;
  await mountConversation(page,false,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');await expect(page.locator('[data-pm-roadmap-quality]')).toHaveCount(0);await expect(page.locator('[data-pm-milestone]')).toHaveCount(0);expect(f.state.writes).toEqual([]);
});
test('historical selected quality keeps the fixed-assessment boundary instead of an unassessed caption',async({page})=>{
  const f=fixture();f.state.observation=qualityObservation();await mountConversation(page,false,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');
  const history=page.locator('[data-pm-roadmap-history]');await history.locator(':scope > summary').click();await history.getByText(/Saved result observation/).click();
  await expect(history).toContainText('Original selected assessments; current applicability has not been evaluated.');await expect(history).toContainText(qualityRef.document_digest);
  await expect(history).not.toContainText('Completion has not been assessed');expect(f.state.writes).toEqual([]);
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`Roadmap ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({locale,baseURL,viewport:{width,height:width===390?844:900}}),page=await context.newPage(),f=fixture();f.state.observation=qualityObservation();await mountConversation(page,false,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');
  await expect(page.locator('[data-pm-milestone]')).toHaveCount(1);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(await page.locator('[data-pm-roadmap]').innerText()).not.toMatch(/\bundefined\b/);
  await expect(page.locator('[data-pm-roadmap-quality]')).toContainText(locale==='en'?'Qualified by the selected assessments':'所選評估判定合格');await page.locator('[data-pm-roadmap-quality] summary').click();
  const out=process.env.BATC_PM_ROADMAP_SCREENSHOTS;if(out){await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/roadmap-${locale}-${width}.png`,fullPage:true});}await context.close();
});
