import {openPmSection} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {fixture,rid,bid,sid,path} from './pm-roadmap-fixture.ts';
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
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`Roadmap ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({locale,baseURL,viewport:{width,height:1000}}),page=await context.newPage(),f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');
  await expect(page.locator('[data-pm-milestone]')).toHaveCount(1);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(await page.locator('[data-pm-roadmap]').innerText()).not.toMatch(/\bundefined\b/);
  const out=process.env.BATC_PM_ROADMAP_SCREENSHOTS;if(out){await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/roadmap-${locale}-${width}.png`,fullPage:true});}await context.close();
});
