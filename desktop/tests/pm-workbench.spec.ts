import {openPmPanel} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {workbenchFixture,route,workId} from './pm-workbench-fixture.ts';
const charter=(page:any)=>page.locator('[data-pm-charter]'),rtm=(page:any)=>page.locator('[data-pm-rtm]');
for(const native of [false,true])test.describe(native?'native workbench':'web workbench',()=>{
  test('Charter separates report history from the same adopted plan and compares fixed sources',async({page})=>{
    const f=workbenchFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);
    await expect(charter(page)).toBeVisible();await expect(charter(page).locator('[data-charter-section]')).toHaveCount(6);
    await expect(page.locator('[data-pm-kanban]')).toBeHidden();await expect(charter(page)).toContainText('Recovery progress');
    await openPmPanel(page,'ui_report');await page.getByRole('button',{name:'Compare with previous report',exact:true}).click();
    await expect(charter(page)).toContainText('Report changes');await expect(charter(page).locator('.pm-charter-comparison')).toContainText('Login PR merge recorded');
    await page.getByRole('button',{name:'Previous report',exact:true}).click();await page.getByRole('button',{name:'Close',exact:true}).click();
    await expect(charter(page)).toContainText('Scope agreed');await expect(charter(page)).not.toContainText('Login PR merge recorded');
    f.plan.document.sections.scope.statements[0].value='A later live plan statement';f.base.state.cursor++;
    await expect.poll(()=>f.state.reads.filter(path=>path.includes('/snapshots/')).length).toBeGreaterThan(4);
    await expect(charter(page)).toContainText('Login and password recovery; billing is outside this release.');
    await expect(charter(page)).not.toContainText('A later live plan statement');
    await charter(page).locator('[data-charter-plan] > summary').click();await expect(charter(page)).toContainText('A member can regain access');
    expect(f.state.writes).toHaveLength(0);expect(f.base.state.writes).toHaveLength(0);
  });
  test('partial and empty Charter remain usable without fabricated quota or adopted direction',async({page})=>{
    const f=workbenchFixture();f.state.snapshots=[];await mountConversation(page,native,f.dispatch);await page.goto(route);
    await expect(charter(page)).toContainText('Incomplete information is welcome');
    await page.locator('[data-charter-discuss]').click();
    await expect(page.locator('[data-pm-action="pm.thread.create"]')).toBeVisible();
    expect(f.base.state.writes).toHaveLength(0);
  });
  test('sidebar discussion entry opens the existing form from both workspace and thread',async({page})=>{
    const f=workbenchFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);
    await page.locator('[data-pm-view="discussions"]').click();
    await page.getByRole('button',{name:'New discussion',exact:true}).click();
    await expect(page.locator('[data-pm-action="pm.thread.create"]')).toBeVisible();
    await page.getByRole('link',{name:/A clearer first visit/}).click();
    await expect(page).toHaveURL(/\/pmt_[a-f0-9]{32}$/);
    await page.locator('[data-pm-view="discussions"]').click();
    await page.getByRole('button',{name:'New discussion',exact:true}).click();
    await expect(page.locator('[data-pm-action="pm.thread.create"]')).toBeVisible();
    expect(f.base.state.writes).toHaveLength(0);
  });
  test('snapshot lost reply recovers its original request and expected plan version',async({page})=>{
    const f=workbenchFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);f.state.lost=true;
    await page.locator('[data-pm-report-menu]').click();await page.locator('[data-menu-key=save]').click();
    const form=page.locator('[data-pm-action="pm.snapshot.create"]');
    await form.getByRole('button',{name:'Save report',exact:true}).click();await expect(form).toContainText('Reply unavailable');
    f.base.state.planHead.version=5;await page.reload();await page.locator('[data-pm-report-menu]').click();await page.locator('[data-menu-key=save]').click();
    await form.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(page.getByLabel('Report version')).toHaveValue('pmsnap_'+'3'.repeat(32));
    expect(f.state.writes).toHaveLength(2);expect(f.state.writes[1]).toEqual(f.state.writes[0]);expect(f.state.snapshots).toHaveLength(3);
  });
  test('comparison withdrawal removes both summary and comparison facts',async({page})=>{
    const f=workbenchFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);
    await expect(charter(page)).toContainText('Recovery progress');f.state.denySnapshot=true;
    await openPmPanel(page,'ui_report');await page.getByRole('button',{name:'Compare with previous report',exact:true}).click();
    await expect(charter(page)).toContainText('Your token lacks this scope');await expect(charter(page)).not.toContainText('Members can reliably');
    await expect(page.getByRole('button',{name:'Print report',exact:true})).toBeDisabled();
  });
  test('RTM uses exact revisions, distinct work and an Issue with two PRs and three attempts',async({page})=>{
    const f=workbenchFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);
    await page.locator('[data-pm-view="requirements"]').click();await expect(page.locator('[data-pm-work-meta]')).toContainText('2 loaded');
    await rtm(page).getByRole('button',{name:'Expand Members can sign in',exact:true}).click();
    await rtm(page).getByRole('button',{name:'Expand Members can recover access',exact:true}).click();
    await expect(rtm(page).locator(`[data-rtm-work="${workId(1)}"]`)).toHaveCount(2);
    await expect(rtm(page).locator('[data-rtm-unassigned-issue]')).toContainText('Unassigned export request');
    await rtm(page).locator(`[data-rtm-select="${workId(1)}"]`).first().click();
    const detail=rtm(page).locator('[data-rtm-detail]');await expect(detail.locator('[data-rtm-pr]')).toHaveCount(2);
    await detail.locator('[data-work-section=details] > summary').click();
    for(const n of [1,2,3])await expect(detail).toContainText('command-'+n);
    await expect(detail).toContainText('Historical deployment');await expect(detail).toContainText('product-owner');
    await expect(rtm(page).locator('[data-rtm-requirement="membership.login"]')).toContainText('Unknown');
    await page.locator('[data-pm-view="overview"]').click();await expect(charter(page)).toBeVisible();
    await page.locator('[data-pm-view="requirements"]').click();await expect(detail).toBeVisible();
    expect(f.state.writes).toHaveLength(0);expect(f.base.state.writes).toHaveLength(0);
  });
  test('RTM clears private rows and details when the work catalogue is withdrawn',async({page})=>{
    const f=workbenchFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await page.locator('[data-pm-view="requirements"]').click();
    await rtm(page).getByRole('button',{name:'Expand Members can sign in',exact:true}).click();await rtm(page).getByRole('button',{name:'Member login',exact:true}).click();
    await expect(rtm(page).locator('[data-rtm-detail]')).toContainText('PR #10');
    f.state.denyWork=true;await page.locator('[data-pm-refresh]').click();
    await expect(rtm(page)).toContainText('Your token lacks this scope');await expect(rtm(page)).not.toContainText('PR #10');
    await expect(rtm(page)).not.toContainText('Members can sign in');await expect(rtm(page)).not.toContainText('3 distinct');
    f.state.denyWork=false;await page.locator('[data-pm-refresh]').click();
    await expect(rtm(page).locator('[data-rtm-detail]')).toBeVisible();await expect(rtm(page)).toContainText('PR #10');
  });
  test('pagination rereads earlier receipts and retains unassigned Issues',async({page})=>{
    const f=workbenchFixture();f.state.deliveryPageSize=1;f.state.issuePageSize=1;
    await mountConversation(page,native,f.dispatch);await page.goto(route);await page.locator('[data-pm-view="requirements"]').click();
    await expect(rtm(page)).toContainText('outside the loaded range');
    await rtm(page).getByRole('button',{name:'More issue links',exact:true}).click();
    await expect(rtm(page).locator('[data-rtm-unassigned-issue]')).toContainText('Unassigned export request');
    await rtm(page).getByRole('button',{name:'Expand Members can sign in',exact:true}).click();
    await rtm(page).getByRole('button',{name:'Member login',exact:true}).click();
    await expect(rtm(page).locator('[data-rtm-detail]')).toContainText('PR #10');f.state.receipts.shift();
    await rtm(page).getByRole('button',{name:'More delivery records',exact:true}).click();
    await expect(rtm(page).locator('[data-rtm-detail]')).toContainText('Historical deployment');
    await expect(rtm(page).locator('[data-rtm-detail]')).not.toContainText('PR #10');
  });
  test('a late work response cannot restore details after sources are withdrawn',async({page})=>{
    const f=workbenchFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await page.locator('[data-pm-view="requirements"]').click();
    await rtm(page).getByRole('button',{name:'Expand Members can sign in',exact:true}).click();
    let release:()=>void=()=>{};f.state.heldDetail=new Promise<void>(resolve=>release=resolve);
    await rtm(page).getByRole('button',{name:'Member login',exact:true}).click();
    await expect(rtm(page).locator('[data-rtm-detail]').getByRole('status',{name:'Reading the selected work and its delivery records…',exact:true})).toBeVisible();
    f.state.denyWork=true;
    try{await page.locator('[data-pm-refresh]').click();await expect(rtm(page)).toContainText('Your token lacks this scope');}
    finally{release();}
    await expect(rtm(page).locator('[data-rtm-detail]')).toBeHidden();await expect(rtm(page)).not.toContainText('PR #10');
  });
  test('keyboard expansion and detail close keep a usable focus target',async({page})=>{
    const f=workbenchFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);
    const nav=page.locator('[data-pm-view="requirements"]');await nav.focus();await page.keyboard.press('Enter');
    const toggle=rtm(page).locator('[data-rtm-toggle="membership.login"]');await toggle.focus();await page.keyboard.press('Enter');
    await expect(toggle).toBeFocused();await expect(toggle).toHaveAttribute('aria-expanded','true');
    const work=rtm(page).getByRole('button',{name:'Member login',exact:true});await work.focus();await page.keyboard.press('Enter');
    await expect(rtm(page).locator('[data-rtm-detail] > header > .ui-icon-button')).toBeFocused();
    await page.keyboard.press('Escape');await expect(work).toBeFocused();
  });
  test('unchanged background reads preserve the open plan, reading rows and detail focus',async({page})=>{
    const f=workbenchFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);
    await charter(page).locator('[data-charter-plan] > summary').click();
    await page.locator('[data-pm-view="requirements"]').click();
    await rtm(page).getByRole('button',{name:'Expand Members can sign in',exact:true}).click();
    await rtm(page).getByRole('button',{name:'Member login',exact:true}).click();
    const heading=rtm(page).locator('[data-rtm-detail] > header > .ui-icon-button');await expect(heading).toBeFocused();
    await expect(rtm(page).locator('[data-rtm-detail] pre')).toContainText('"number": 41');
    await expect(page.locator('[data-pm-refresh]')).toBeEnabled();
    await heading.evaluate(element=>element.setAttribute('data-reading-marker','detail'));
    await rtm(page).locator('[data-rtm-requirement="membership.login"]').evaluate(element=>element.setAttribute('data-reading-marker','row'));
    const reads=f.state.reads.filter(path=>path.includes('/deliveries?')).length;
    f.board.state.time++;f.board.state.cards[0].flow.age_seconds++;f.base.state.cursor++;
    await expect.poll(()=>f.state.reads.filter(path=>path.includes('/deliveries?')).length).toBeGreaterThan(reads);
    await expect(page.locator('[data-pm-refresh]')).toBeEnabled();
    await expect(heading).toHaveAttribute('data-reading-marker','detail');await expect(heading).toBeFocused();
    await expect(rtm(page).locator('[data-reading-marker="row"]')).toHaveCount(1);
    f.state.issues[0].document.issue.number=84;f.base.state.cursor++;
    await expect(rtm(page).locator('[data-rtm-detail] pre')).toContainText('"number": 84');
    await page.locator('[data-pm-view="overview"]').click();
    await expect(charter(page).locator('[data-charter-plan]')).toHaveAttribute('open','');
  });
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`workbench layout ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:1000},colorScheme:'light',reducedMotion:'reduce'}),page=await context.newPage();
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));const f=workbenchFixture();await mountConversation(page,false,f.dispatch);await page.goto(route);
  await expect(charter(page).locator('[data-charter-section]')).toHaveCount(6);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const out=process.env.BATC_WORKBENCH_SCREENSHOTS;if(out){await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/charter-${locale}-${width}.png`,fullPage:true});}
  if(width<1000)await page.locator('#workspace-menu').click();
  await page.locator('[data-pm-view="requirements"]').click();await expect(rtm(page).locator('[data-rtm-requirement]')).toHaveCount(2);
  const row=rtm(page).locator('[data-rtm-requirement]').first();await (width<800?row:row.getByRole('button')).click();await rtm(page).locator('[data-rtm-work] [data-rtm-select]').first().click();
  await expect(rtm(page).locator('[data-rtm-pr]')).toHaveCount(2);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(await rtm(page).innerText()).not.toMatch(/\b(?:undefined|null|wb_\w+)\b/);
  if(out)await page.screenshot({path:`${out}/rtm-${locale}-${width}.png`,fullPage:true});
  expect(errors).toEqual([]);await context.close();
});
