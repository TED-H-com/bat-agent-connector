import {openPmSection} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {mountConversation} from './conversation-fixture.ts';
import {handoffFixture,packageId,route,workspaceId,projectId,workId,codeSha,specSha,target} from './pm-package-handoff-fixture.ts';
const section=(page:any)=>page.locator('[data-pm-package-handoff]'),form=(page:any)=>section(page).locator('[data-pm-action="repository.continue"]');
async function select(page:any,sequence=1){await openPmSection(page,'requirements','ui_packages');await page.locator('[data-pm-work-packages]').getByRole('button',{name:`Restore member access · Preparation ${sequence}`,exact:true}).click();}
async function open(page:any){await openPmSection(page,'requirements','ui_handoff');const fold=section(page).locator('[data-pm-package-handoff-controls]');if(!await fold.evaluate((node:any)=>node.open))await fold.locator(':scope > summary').click();}
async function review(page:any){await section(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();await expect(section(page).getByRole('heading',{name:'Reviewed implementation base',exact:true})).toBeVisible();}
async function fill(page:any,text='Use the existing recovery module as the implementation base'){
  await form(page).getByLabel('Why use this implementation base?',{exact:true}).fill(text);
  await form(page).getByLabel('Instructions for this implementation',{exact:true}).fill('Implement only the fixed recovery requirements.');
}
for(const native of [false,true])test.describe(native?'Native fixed handoff':'Web fixed handoff',()=>{
  test('contributor starts one exact existing-engine request with distinct spec and code versions',async({page})=>{
    const f=handoffFixture();f.fixture.base.state.role='contributor';await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);
    expect(f.state.reads.some(read=>read.path==='/repository-previews')).toBe(false);expect(f.state.writes).toHaveLength(0);
    await review(page);await section(page).getByText('Fixed versions and original sources',{exact:true}).click();await expect(section(page)).toContainText(codeSha);await expect(section(page)).toContainText(specSha);
    await fill(page);await form(page).getByLabel('BAT agent',{exact:true}).selectOption('codex');await form(page).getByText('Optional session title and model',{exact:true}).click();
    await form(page).getByLabel('Title (optional)',{exact:true}).fill('Recovery implementation');await form(page).getByLabel('Model (optional)',{exact:true}).fill('model-candidate');
    await form(page).getByRole('button',{name:'Start implementation',exact:true}).click();await expect(section(page)).toContainText('Implementation was started');
    expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].body).toEqual({action:'repository.continue',target,
      params:{source_ref:'refs/heads/main',source_sha:codeSha,project_id:projectId,work_item_id:workId,agent:'codex',prompt:'Implement only the fixed recovery requirements.',
        title:'Recovery implementation',model:'model-candidate',pm_package_ref:{workspace_id:workspaceId,package_id:packageId,document_digest:f.row.document_digest},
        pm_code_basis:{mode:'implement_fixed_spec_from_selected_base',rationale:'Use the existing recovery module as the implementation base'}},
      preconditions:{repository_id:7,binding_digest:'b'.repeat(64),expected_project_version:3,expected_work_item_fingerprint:'f'.repeat(64)}});
    await expect(section(page).getByRole('link',{name:'Open managed execution',exact:true})).toHaveAttribute('href',`#/session/${target.host}/managed-session`);
  });
  test('a different published code SHA cannot replace the fixed base',async({page})=>{
    const f=handoffFixture();f.state.codeChanged=true;await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);
    await section(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();await expect(section(page)).toContainText('differs from the fixed package');
    await expect(form(page).getByRole('button',{name:'Start implementation',exact:true})).toBeDisabled();expect(f.state.writes).toHaveLength(0);
  });
  test('scope and dependency failures are not waived by choosing an implementation base',async({page})=>{
    const f=handoffFixture();f.state.blocked='dependency:mail';await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);
    await section(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();await expect(section(page)).toContainText('unresolved checks beyond');
    await expect(form(page).getByRole('button',{name:'Start implementation',exact:true})).toBeDisabled();expect(f.state.reads.some(read=>read.path==='/repository-previews')).toBe(false);
  });
  test('project refresh preserves draft and requires explicit project-version review',async({page})=>{
    const f=handoffFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);await review(page);await fill(page,'Keep my base rationale');
    f.state.version=4;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(form(page).getByLabel('Why use this implementation base?',{exact:true})).toHaveValue('Keep my base rationale');
    await expect(form(page).getByRole('button',{name:'Start implementation',exact:true})).toBeDisabled();await review(page);
    await expect(form(page).getByRole('button',{name:'Start implementation',exact:true})).toBeDisabled();await section(page).getByRole('button',{name:'Use this reviewed project version',exact:true}).click();
    await form(page).getByRole('button',{name:'Start implementation',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.preconditions.expected_project_version).toBe(4);
  });
  test('unknown original request stays recoverable while paused after a newer package appears',async({page})=>{
    const f=handoffFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);await review(page);await fill(page);f.state.lost=true;
    await form(page).getByRole('button',{name:'Start implementation',exact:true}).click();await expect(form(page)).toContainText('Reply unavailable');
    f.fixture.addPackage();f.fixture.state.stage='paused';await page.reload();await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);
    await form(page).getByRole('button',{name:'Check original request',exact:true}).click();await expect(section(page)).toContainText('Implementation was started');
    expect(f.state.writes).toHaveLength(2);expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);expect(f.state.ops.size).toBe(1);
  });
  test('paused and unreadable lifecycle stop new package lineage starts despite a non-PM action name',async({page})=>{
    const f=handoffFixture();f.fixture.state.stage='paused';await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);await review(page);await fill(page);
    await expect(form(page).getByRole('button',{name:'Start implementation',exact:true})).toBeDisabled();
    f.fixture.state.stage='development';f.state.lifecycleFailed=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();await review(page);
    await expect(form(page).getByRole('button',{name:'Start implementation',exact:true})).toBeDisabled();expect(f.state.writes).toHaveLength(0);
  });
  test('each immutable package owns its draft; older packages cannot create a new attempt',async({page})=>{
    const f=handoffFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);await review(page);await fill(page,'First package draft');
    f.fixture.addPackage();await openPmSection(page,'requirements','ui_packages');await page.getByRole('button',{name:'Refresh work packages',exact:true}).click();
    await select(page,2);await open(page);await expect(form(page).getByLabel('Why use this implementation base?',{exact:true})).toHaveValue('');await fill(page,'Second package draft');
    await select(page,1);await open(page);await expect(form(page).getByLabel('Why use this implementation base?',{exact:true})).toHaveValue('First package draft');
    await section(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();await expect(section(page)).toContainText('unresolved checks beyond');
    expect(f.state.writes).toHaveLength(0);
  });
  test('late preview cannot restore sources after membership or source withdrawal',async({page})=>{
    const f=handoffFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);
    let release!:()=>void;f.state.previewGate=new Promise(resolve=>release=resolve);await section(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();
    await expect.poll(()=>f.state.reads.some(read=>read.path==='/repository-previews')).toBe(true);f.state.deny=true;
    await page.getByRole('button',{name:'Refresh',exact:true}).click();release();await expect(section(page)).not.toContainText('Restore member access');
    await expect(section(page).getByRole('heading',{name:'Reviewed implementation base',exact:true})).toHaveCount(0);await expect(page.locator('[data-pm-wp-detail]')).not.toContainText('Private');
  });
  test('request-side source refusal clears the selected package and preserves its draft for recovery',async({page})=>{
    const f=handoffFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);await review(page);await fill(page,'Keep after refusal');f.state.reject=true;
    await form(page).getByRole('button',{name:'Start implementation',exact:true}).click();await expect(section(page)).toContainText('sources or permissions could not be verified');
    await expect(section(page)).not.toContainText('Restore member access');await expect(page.locator('[data-pm-wp-detail]')).not.toContainText('Private');
    f.state.reject=false;await openPmSection(page,'requirements','ui_packages');await page.getByRole('button',{name:'Refresh work packages',exact:true}).click();await select(page);await open(page);
    await expect(form(page).getByLabel('Why use this implementation base?',{exact:true})).toHaveValue('Keep after refusal');expect(f.state.writes).toHaveLength(1);
  });
  test('late ACK during a parent refresh recovers one original operation without resending',async({page})=>{
    const f=handoffFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);await review(page);await fill(page);
    let release!:()=>void;f.state.writeGate=new Promise(resolve=>release=resolve);await form(page).getByRole('button',{name:'Start implementation',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(1);await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect(form(page).getByRole('button',{name:'Check original request',exact:true})).toBeVisible();release();
    await expect(section(page)).toContainText('Implementation was started');expect(f.state.writes).toHaveLength(1);expect(f.state.ops.size).toBe(1);
  });
  test('a stale project admission cannot silently rewrite the original request',async({page})=>{
    const f=handoffFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);await review(page);await fill(page);f.state.version=4;
    await form(page).getByRole('button',{name:'Start implementation',exact:true}).click();await expect(form(page)).toContainText('Project version changed');
    expect(f.state.writes[0].body.preconditions.expected_project_version).toBe(3);expect(f.state.ops.size).toBe(0);
    await form(page).getByRole('button',{name:'Edit a new request',exact:true}).click();await review(page);
    await expect(form(page).getByRole('button',{name:'Start implementation',exact:true})).toBeDisabled();
    await section(page).getByRole('button',{name:'Use this reviewed project version',exact:true}).click();await form(page).getByRole('button',{name:'Start implementation',exact:true}).click();
    await expect(section(page)).toContainText('Implementation was started');expect(f.state.writes[1].body.preconditions.expected_project_version).toBe(4);
    expect(f.state.writes[1].idempotency_key).not.toBe(f.state.writes[0].idempotency_key);
  });
  test('disabled handoff capability keeps the engineering package read-only',async({page})=>{
    const f=handoffFixture();f.state.enabled=false;await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_handoff');await select(page);
    await expect(page.locator('[data-pm-wp-detail]')).toContainText('Private requirement');await expect(section(page)).toHaveCount(0);
    expect(f.state.reads.some(read=>read.path==='/repository-previews')).toBe(false);expect(f.state.writes).toHaveLength(0);
  });
  test('read-only members and disabled BAT hosts cannot start',async({page})=>{
    const f=handoffFixture();f.fixture.base.state.role='reader';await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);
    await expect(section(page).getByRole('button',{name:'Review fixed implementation base',exact:true})).toBeDisabled();
    f.fixture.base.state.role='contributor';f.state.hostEnabled=false;await page.reload();await openPmSection(page,'requirements','ui_handoff');await select(page);await open(page);await expect(section(page).getByRole('button',{name:'Review fixed implementation base',exact:true})).toBeDisabled();expect(f.state.writes).toHaveLength(0);
  });
});
for(const locale of ['en-US','zh-TW'])for(const width of [390,768,1440])test(`handoff ${locale} ${width}px`,async({browser,baseURL},testInfo)=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:1000}}),page=await context.newPage(),f=handoffFixture();
  await mountConversation(page,false,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_packages');await page.locator('[data-pm-work-packages]').getByRole('button',{name:/Restore member access/}).click();await open(page);
  await section(page).getByRole('button',{name:locale==='zh-TW'?'核對固定實作基底':'Review fixed implementation base',exact:true}).click();
  await expect(section(page).getByRole('heading',{name:locale==='zh-TW'?'已核對實作基底':'Reviewed implementation base',exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);const folder=process.env.PM_SCREENSHOTS||testInfo.outputPath('screenshots');mkdirSync(folder,{recursive:true});
  const size=await section(page).boundingBox();await page.setViewportSize({width,height:Math.ceil(size!.height)+220});
  await section(page).evaluate(element=>window.scrollTo(0,element.getBoundingClientRect().top+scrollY-160));
  await section(page).screenshot({path:`${folder}/handoff-${locale}-${width}.png`});await context.close();
});

for(const native of [false,true])test(`${native?'Native':'Web'} Chinese fixed package start`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale:'zh-TW'}),page=await context.newPage(),f=handoffFixture();
  await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'requirements','ui_packages');await page.locator('[data-pm-work-packages]').getByRole('button',{name:/Restore member access/}).click();await open(page);
  await section(page).getByRole('button',{name:'核對固定實作基底',exact:true}).click();
  await form(page).getByLabel('為何使用這個實作基底？',{exact:true}).fill('沿用既有登入模組，實作固定規格');
  await form(page).getByLabel('這次實作的指示',{exact:true}).fill('完成固定工作包的驗證與實作');
  await form(page).getByRole('button',{name:'開始實作',exact:true}).click();await expect(section(page)).toContainText('已開始實作');
  expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].body.params.pm_code_basis.rationale).toBe('沿用既有登入模組，實作固定規格');await context.close();
});
