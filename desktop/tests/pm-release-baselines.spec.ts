import {openPmSection, expectPmWithdrawn} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {mountConversation} from './conversation-fixture.ts';
import {releaseFixture,releaseRoute,repositoryName,bindingId} from './pm-release-baselines-fixture.ts';
const section=(page:any)=>page.locator('[data-pm-release-baselines]');
const register=(page:any)=>page.locator('[data-pm-action="pm.release_baseline.register"]');
async function selectSource(page:any,kind='github_release'){
  await page.locator('[data-release-register] > summary').click();
  await page.getByLabel('Bound project and repository',{exact:true}).selectOption(JSON.stringify([bindingId,repositoryName]));
  await page.getByLabel('Published source',{exact:true}).selectOption(kind);
  await page.getByRole('button',{name:'Read published sources',exact:true}).click();
  await page.locator('[data-release-choice] input').first().check();
}
async function preview(page:any){await page.getByRole('button',{name:'Read fixed source evidence',exact:true}).click();await expect(section(page).getByRole('heading',{name:'Reviewed source',exact:true})).toBeVisible();}
for(const native of [false,true])test.describe(native?'Native release baselines':'Web release baselines',()=>{
  test('shared home reads no external sources until requested and registers exact reviewed proof',async({page})=>{
    const f=releaseFixture();await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');
    await expect(section(page)).toContainText('No release baseline has been selected');
    expect(f.state.reads.some(path=>path.includes('github-releases')||path.includes('preview'))).toBe(false);
    await selectSource(page);await preview(page);
    const original=f.registration();
    await register(page).getByLabel('Reason for selecting this baseline',{exact:true}).fill('Use the public 1.4 line');
    await register(page).getByLabel('Known limitations (one per line)',{exact:true}).fill('Offline recovery has not been tested\nLiteral <script>claim</script>');
    await register(page).getByRole('button',{name:'Record release baseline',exact:true}).click();
    await expect(section(page).locator('[data-release-current]')).toContainText('v1.4.0');
    await expect(section(page)).toContainText('Package registry publication: not assessed');
    await expect(section(page)).toContainText('Functional acceptance and quality remain unassessed');
    expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].body).toEqual({...original,
      params:{...original.params,rationale:'Use the public 1.4 line',known_limitations:['Offline recovery has not been tested','Literal <script>claim</script>']}});
    await expect(section(page).locator('script')).toHaveCount(0);expect(f.state.baselines[0].document.maintenance_authorized).toBe(false);
    await page.getByRole('button',{name:'Read published sources',exact:true}).click();await page.locator('[data-release-choice] input').check();await preview(page);
    await expect(register(page).getByLabel('Reason for selecting this baseline',{exact:true})).toBeEnabled();
  });
  test('deployment proof stays distinct from GitHub, package and current environment verification',async({page})=>{
    const f=releaseFixture();f.addBaseline('deployment');await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');
    await expect(section(page)).toContainText('Deployment: fixed version verified');await expect(section(page)).toContainText('GitHub Release: not assessed');
    await expect(section(page)).toContainText('current environment has not been rechecked');
    await selectSource(page,'deployment');await page.getByLabel('Maintenance branch (optional)',{exact:true}).fill('release/1.x');await preview(page);
    await section(page).getByText('Original evidence and versions',{exact:true}).last().click();await expect(section(page)).toContainText('refs/heads/release/1.x');
    await page.getByLabel('Maintenance branch (optional)',{exact:true}).fill('release/2.x');
    await expect(section(page).getByRole('heading',{name:'Reviewed source',exact:true})).toHaveCount(0);
    await expect(register(page).getByRole('button',{name:'Record release baseline',exact:true})).toBeDisabled();expect(f.state.writes).toHaveLength(0);
  });
  test('unknown reply preserves original key and CAS across refresh and later baseline changes',async({page})=>{
    const f=releaseFixture();await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');await selectSource(page);await preview(page);
    await register(page).getByLabel('Reason for selecting this baseline',{exact:true}).fill('Original reason');f.state.lost=true;
    await register(page).getByRole('button',{name:'Record release baseline',exact:true}).click();await expect(register(page)).toContainText('Reply unavailable');
    f.addBaseline('deployment');await page.reload();await openPmSection(page, 'dependencies' , 'ui_baselines');await page.locator('[data-release-register] > summary').click();
    await expect(register(page).getByRole('button',{name:'Check original request',exact:true})).toBeEnabled();
    await register(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(register(page).getByLabel('Reason for selecting this baseline',{exact:true})).toHaveValue('');
    expect(f.state.writes).toHaveLength(2);expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);expect(f.state.baselines).toHaveLength(2);
  });
  test('draft CAS is not rebased by refresh or another baseline without explicit review',async({page})=>{
    const f=releaseFixture();await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');await selectSource(page);await preview(page);
    await register(page).getByLabel('Reason for selecting this baseline',{exact:true}).fill('Keep my text');f.addBaseline('deployment');
    await page.getByRole('button',{name:'Refresh release baseline',exact:true}).click();
    await expect(register(page).getByLabel('Reason for selecting this baseline',{exact:true})).toHaveValue('Keep my text');
    await expect(register(page).getByRole('button',{name:'Record release baseline',exact:true})).toBeDisabled();
    await page.getByRole('button',{name:'Read published sources',exact:true}).click();await page.locator('[data-release-choice] input').check();await preview(page);
    await expect(register(page).getByRole('button',{name:'Record release baseline',exact:true})).toBeDisabled();
    await page.getByRole('button',{name:'Use this reviewed source',exact:true}).click();
    await register(page).getByRole('button',{name:'Record release baseline',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
    expect(f.state.writes[0].body.preconditions.expected_baseline_version).toBe(1);expect(f.state.writes[0].body.params.rationale).toBe('Keep my text');
  });
  test('filtered empty pages stay pageable for repositories, sources and history',async({page})=>{
    const f=releaseFixture();f.addBaseline();f.state.emptyBindingPage=f.state.emptySourcePage=f.state.emptyHistoryPage=true;
    await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');await page.locator('[data-release-register] > summary').click();
    await page.getByRole('button',{name:'Load more repositories',exact:true}).click();
    await page.getByLabel('Bound project and repository',{exact:true}).selectOption(JSON.stringify([bindingId,repositoryName]));
    await page.getByRole('button',{name:'Read published sources',exact:true}).click();await expect(section(page)).toContainText('No readable published sources on this page');
    await page.getByRole('button',{name:'Read more sources',exact:true}).click();await page.locator('[data-release-choice] input').check();
    await page.getByText('Previous selections',{exact:true}).click();await page.getByRole('button',{name:'Load more selections',exact:true}).click();
    await expect(section(page).locator('[data-release-baseline]')).toHaveCount(2);await expect(section(page)).toContainText('partial history');
  });
  test('source access withdrawal clears facts and proof but retains the local draft',async({page})=>{
    const f=releaseFixture();f.addBaseline();await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');await selectSource(page);await preview(page);
    await register(page).getByLabel('Reason for selecting this baseline',{exact:true}).fill('Keep local reason');f.state.fail=403;
    await page.getByRole('button',{name:'Refresh release baseline',exact:true}).click();await expect(section(page)).toContainText('Source access could not be verified');
    await expect(section(page).locator('[data-release-baseline]')).toHaveCount(0);await expect(section(page).getByRole('heading',{name:'Reviewed source',exact:true})).toHaveCount(0);
    await expect(register(page).getByLabel('Reason for selecting this baseline',{exact:true})).toHaveValue('Keep local reason');
    await expect(register(page).getByRole('button',{name:'Record release baseline',exact:true})).toBeDisabled();
    f.state.fail=null;await page.getByRole('button',{name:'Refresh release baseline',exact:true}).click();await expect(section(page).locator('[data-release-baseline]')).toHaveCount(1);
    expect(f.state.reads.filter(path=>path.includes('/github-releases'))).toHaveLength(1);
  });
  test('late preview after parent read withdrawal cannot restore private source facts',async({page})=>{
    const f=releaseFixture();await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');await selectSource(page);
    let release!:()=>void;f.state.deferPreview=new Promise(resolve=>{release=resolve;});await page.getByRole('button',{name:'Read fixed source evidence',exact:true}).click();
    await expect.poll(()=>f.state.reads.some(path=>path.includes('preview'))).toBe(true);
    f.base.state.failRead=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(page.locator('.pm-status')).toContainText('PM read unavailable');
    release();await expect(section(page)).toHaveJSProperty('hidden',true);await expectPmWithdrawn(page,'dependencies','[data-pm-release-baselines]','ui_baselines');await expect(section(page).getByRole('heading',{name:'Reviewed source',exact:true})).toHaveCount(0);
    f.state.deferPreview=null;f.base.state.failRead=false;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(section(page)).toBeVisible();
    await expect(section(page).getByRole('heading',{name:'Reviewed source',exact:true})).toHaveCount(0);
  });
  test('readers and non-owner administrators cannot register; hidden head is not empty',async({page})=>{
    const f=releaseFixture();f.addBaseline();f.state.hidden=true;f.base.state.role='reader';
    const dispatch=async(input:any)=>{const result=await f.dispatch(input);if(input.path==='/capabilities'||input.path==='/bootstrap'){
      const caps=input.path==='/bootstrap'?result.data.capabilities:result.data;caps.scopes.push('admin');}return result;};
    await mountConversation(page,native,dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');
    await expect(section(page)).toContainText('original source is outside your access');await expect(page.locator('[data-release-register]')).toBeHidden();
    await expect(section(page)).not.toContainText('No release baseline has been selected');expect(f.state.writes).toHaveLength(0);
  });
  test('mismatched preview is withdrawn and does not enable registration',async({page})=>{
    const f=releaseFixture();f.state.previewMismatch=true;await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');await selectSource(page);
    await page.getByRole('button',{name:'Read fixed source evidence',exact:true}).click();await expect(section(page)).toContainText('does not match');
    await expect(register(page).getByRole('button',{name:'Record release baseline',exact:true})).toBeDisabled();expect(f.state.writes).toHaveLength(0);
  });
  test('older baseline readers keep current facts without unsupported discovery reads',async({page})=>{
    const f=releaseFixture();f.state.discovery=false;f.addBaseline();await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');
    await expect(section(page)).toContainText('v1.4.0');await expect(section(page)).toContainText('does not provide source selection previews yet');
    expect(f.state.reads.some(path=>path.includes('release-baseline-sources'))).toBe(false);await expect(page.locator('[data-release-register]')).toBeHidden();
  });
  test('late acknowledged registration after parent refresh is recovered by its original operation ID',async({page})=>{
    const f=releaseFixture();await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');await selectSource(page);await preview(page);
    await register(page).getByLabel('Reason for selecting this baseline',{exact:true}).fill('Save once across parent refresh');
    let release!:()=>void;f.state.deferWrite=new Promise(resolve=>{release=resolve;});
    await register(page).getByRole('button',{name:'Record release baseline',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
    await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(section(page).locator('[data-release-current]')).toContainText('v1.4.0');
    release();await expect(register(page).getByLabel('Reason for selecting this baseline',{exact:true})).toHaveValue('');
    expect(f.state.writes).toHaveLength(1);expect(f.state.ops.size).toBe(1);
  });
  test('request-side source rejection clears displayed facts without forgetting the original request',async({page})=>{
    const f=releaseFixture();f.addBaseline();await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');await selectSource(page);await preview(page);
    await register(page).getByLabel('Reason for selecting this baseline',{exact:true}).fill('Preserved after rejection');f.state.rejectWrite=true;
    await register(page).getByRole('button',{name:'Record release baseline',exact:true}).click();await expect(section(page)).toContainText('Source access could not be verified');
    await expect(section(page).locator('[data-release-baseline]')).toHaveCount(0);await expect(section(page).getByRole('heading',{name:'Reviewed source',exact:true})).toHaveCount(0);
    await expect(register(page).getByLabel('Reason for selecting this baseline',{exact:true})).toHaveValue('Preserved after rejection');
    await expect(register(page).getByLabel('Reason for selecting this baseline',{exact:true})).toBeDisabled();expect(f.state.writes).toHaveLength(1);
  });
  test('current membership change cancels a late preview and removes registration controls',async({page})=>{
    const f=releaseFixture();await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');await selectSource(page);
    let release!:()=>void;f.state.deferPreview=new Promise(resolve=>{release=resolve;});await page.getByRole('button',{name:'Read fixed source evidence',exact:true}).click();
    await expect.poll(()=>f.state.reads.some(path=>path.includes('preview'))).toBe(true);f.base.state.role='reader';
    await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(page.locator('[data-release-register]')).toBeHidden();release();
    await expect(section(page).getByRole('heading',{name:'Reviewed source',exact:true})).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
  });
  test('feature gate omits baseline controls on older central',async({page})=>{
    const f=releaseFixture();f.state.enabled=false;await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');await expect(page.locator('.shell-workspace-block')).toContainText('Library refresh');
    await expect(section(page)).toHaveCount(0);expect(f.state.reads).toHaveLength(0);
  });
});
for(const language of ['en','zh-TW'])for(const width of [390,768,1440])test(`${language} release baseline at ${width}px`,async({browser,baseURL},testInfo)=>{
  const context=await browser.newContext({locale:language,baseURL,viewport:{width,height:1000}}),page=await context.newPage();
  const f=releaseFixture();f.addBaseline();
  await mountConversation(page,false,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');await expect(section(page).locator('[data-release-baseline]')).toBeVisible();
  await expect(section(page)).toContainText(language==='en'?'Functional acceptance and quality remain unassessed':'功能驗收與品質仍未評估');
  await expect(section(page).locator('.pm-heading [data-status="not_assessed"]')).toHaveCount(1);
  await expect(section(page).locator('.pm-heading')).not.toContainText(language==='en'?'GitHub Release':'GitHub 版本發布');
  await expect(section(page).locator('[data-release-current] .pm-baseline-facts [data-status]')).toHaveCount(4);
  const header=await section(page).locator('.pm-heading').evaluate(node=>{
    const title=node.querySelector('h3').getBoundingClientRect(),button=node.querySelector('button').getBoundingClientRect();
    const facts=node.parentElement.querySelector('[data-release-current] .pm-baseline-facts');
    const version=node.parentElement.querySelector('[data-release-current] strong');
    const tops=facts?[...facts.children].map(item=>item.getBoundingClientRect().top):[];
    return {sameRow:button.top<title.bottom&&button.bottom>title.top&&button.left>title.left,
      center:Math.abs((title.top+title.bottom)/2-(button.top+button.bottom)/2),
      currentBorder:getComputedStyle(node.parentElement.querySelector('[data-release-current] > article')).borderTopWidth,
      factsBeforeVersion:Boolean(facts&&version&&(facts.compareDocumentPosition(version)&Node.DOCUMENT_POSITION_FOLLOWING)),
      factSpread:tops.length?Math.max(...tops)-Math.min(...tops):99};
  });
  expect(header.sameRow).toBe(true);expect(header.center).toBeLessThan(2);expect(header.currentBorder).toBe('0px');expect(header.factsBeforeVersion).toBe(true);
  if(width===1440)expect(header.factSpread).toBeLessThan(4);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  const folder=process.env.PM_SCREENSHOTS||testInfo.outputPath('screenshots');mkdirSync(folder,{recursive:true});
  await section(page).screenshot({path:`${folder}/${language}-${width}.png`});await context.close();
});

for(const native of [false,true])test(`${native?'Native':'Web'} Chinese release registration uses the same fixed request`,async({browser,baseURL})=>{
  const context=await browser.newContext({locale:'zh-TW',baseURL}),page=await context.newPage(),f=releaseFixture();
  await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page, 'dependencies' , 'ui_baselines');await page.locator('[data-release-register] > summary').click();
  await page.getByLabel('已關聯的專案與儲存庫',{exact:true}).selectOption(JSON.stringify([bindingId,repositoryName]));
  await page.getByRole('button',{name:'讀取已發布來源',exact:true}).click();await page.locator('[data-release-choice] input').check();
  await page.getByRole('button',{name:'讀取固定來源證據',exact:true}).click();
  await register(page).getByLabel('選定此基準的理由',{exact:true}).fill('先維持這個已發布版本');
  await register(page).getByRole('button',{name:'登記發布基準',exact:true}).click();await expect(section(page).locator('[data-release-current]')).toContainText('v1.4.0');
  expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].body.params.rationale).toBe('先維持這個已發布版本');await context.close();
});

for(const language of ['en','zh-TW'])for(const native of [false,true])test(`${native?'Native':'Web'} ${language} preview arriving during rationale entry preserves keyboard input`,async({browser,baseURL})=>{
  const context=await browser.newContext({locale:language,baseURL}),page=await context.newPage(),f=releaseFixture();
  const chinese=language==='zh-TW',reason=chinese?'先維持這個已發布版本':'Keep the published version';
  let release!:()=>void;
  f.state.deferPreview=new Promise(resolve=>{release=resolve;});
  try{
    await mountConversation(page,native,f.dispatch);await page.goto(releaseRoute);await openPmSection(page,'dependencies','ui_baselines');
    await page.locator('[data-release-register] > summary').click();
    await page.getByLabel(chinese?'已關聯的專案與儲存庫':'Bound project and repository',{exact:true}).selectOption(JSON.stringify([bindingId,repositoryName]));
    await page.getByRole('button',{name:chinese?'讀取已發布來源':'Read published sources',exact:true}).click();await page.locator('[data-release-choice] input').check();
    await page.getByRole('button',{name:chinese?'讀取固定來源證據':'Read fixed source evidence',exact:true}).click();
    await expect.poll(()=>f.state.reads.some(path=>path.includes('preview'))).toBe(true);
    const rationale=register(page).getByLabel(chinese?'選定此基準的理由':'Reason for selecting this baseline',{exact:true});
    const save=register(page).getByRole('button',{name:chinese?'登記發布基準':'Record release baseline',exact:true});
    // A fill focuses the field before inserting text. Deliver the response at that boundary.
    await expect(save).toBeDisabled();await rationale.focus();
    release();await expect(section(page).getByRole('heading',{name:chinese?'已檢視來源':'Reviewed source',exact:true})).toBeVisible();
    await expect(rationale).toBeFocused();
    const original=f.registration();
    await page.keyboard.insertText(reason);
    await expect(rationale).toHaveValue(reason);expect(f.state.writes).toHaveLength(0);
    await save.click();await expect(section(page).locator('[data-release-current]')).toContainText('v1.4.0');
    expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].body).toEqual({...original,params:{...original.params,rationale:reason}});
  }finally{release();await context.close();}
});
