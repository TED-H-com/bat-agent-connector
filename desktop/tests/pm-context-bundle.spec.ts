import {openThreadAnalysis} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {mountConversation} from './conversation-fixture';
import {pmRoute,workspaceId,threadId} from './pm-fixture';
import {nativeFixture} from './pm-analysis-fixture';
import {expectClientError, expectErrorDetails, expectNoNestedErrorDetails} from './api-error-assertions.ts';
function fixture(){
  const f=nativeFixture();f.bat.available=true;
  const state={available:true,partial:false,revision:'1',denied:false,stale:false,invalid:false,reads:0};
  const preview=()=>({schema:'pm_context_bundle_preview_v1',workspace_id:workspaceId,thread_id:threadId,thread_version:f.state.thread.version+(state.stale?1:0),
    context_bundle_ref:{schema:'pm_context_bundle_v1',revision_id:'pmrev_'+state.revision.repeat(32),document_digest:'a'.repeat(64),adoption_operation_id:'op_'+'b'.repeat(32),git_receipt_digest:'c'.repeat(64)},
    manifest:[{path:'docs/pm/accepted/plan.json',digest:'d'.repeat(64),utf8_bytes:1450,included:true},{path:'docs/pm/accepted/plan.md',digest:'e'.repeat(64),utf8_bytes:state.partial?300000:3200,included:!state.partial}],
    coverage:{accepted_document_complete:!state.partial,acceptance_coverage:state.partial?'incomplete_original_document':'complete_original_document',adoption_authorized:false,execution_authorized:false,max_utf8_bytes:262144},authority:'context_only',execution_authorized:state.invalid});
  const dispatch=async(input:any)=>{
    if(input.path.endsWith('/context-bundle')){state.reads++;return state.denied?{status:404,data:{error:{code:'NOT_FOUND',message:'Original source unavailable'}}}:{status:200,data:preview()};}
    const response=await f.dispatch(input),path=new URL(input.path,'http://fixture').pathname;
    if(path==='/capabilities'||path==='/bootstrap'){
      const caps=path==='/bootstrap'?response.data.capabilities:response.data;
      if(state.available)caps.features.pm_workspaces.accepted_context_bundle={schema:'pm_context_bundle_v1',context_version:2,max_utf8_bytes:262144};
    }return response;
  };return {...f,bundle:state,preview,dispatch};
}
const form=(page:any,bat=false)=>page.locator(`[data-pm-action="${bat?'pm.run.bat.analyze':'pm.run.analyze'}"]`);
async function inspect(page:any,bat=false){const box=form(page,bat).locator('[data-pm-context-bundle]');await box.locator('summary').click();await box.getByRole('button',{name:'Preview accepted plan',exact:true}).click();return box;}
for(const native of [false,true])for(const bat of [false,true])test(`${native?'native':'web'} ${bat?'BAT':'local'} explicit selected context`,async({page})=>{
  const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadAnalysis(page);if(bat)await page.locator('[data-pm-analysis-adapter]').selectOption('bat_analysis');
  const request=form(page,bat),box=await inspect(page,bat),original=f.preview().context_bundle_ref;
  await expect(box).toContainText('All accepted document files included');expect(f.state.writes).toHaveLength(0);
  await box.getByRole('button',{name:'Include this accepted version',exact:true}).click();
  await request.locator('textarea').fill('Compare the accepted requirements with the discussion.');await request.getByRole('button',{name:'Request PM analysis',exact:true}).click();
  await expect.poll(() => f.state.writes.length).toBe(1);
  expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].body.params.context_bundle_ref).toEqual(original);expect(Object.keys(f.state.writes[0].body.params)).toEqual(['purpose','context_bundle_ref']);
});
test('partial coverage is explicit; preview alone selects nothing and discussion-only remains unchanged',async({page})=>{
  const f=fixture();f.bundle.partial=true;await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openThreadAnalysis(page);const box=await inspect(page);
  await expect(box).toContainText('some files are omitted');await expect(box.locator('[data-pm-context-selection]')).toBeEmpty();
  await box.getByRole('button',{name:'Include this partial document',exact:true}).click();await expect(box.locator('[data-pm-context-selection]')).toContainText('some files are omitted');
  await box.getByRole('button',{name:'Use discussion only',exact:true}).click();await form(page).locator('textarea').fill('Discussion only');await form(page).getByRole('button',{name:'Request PM analysis',exact:true}).click();
  await expect.poll(() => f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params).toEqual({purpose:'Discussion only'});
});
test('restored draft rechecks sources and never substitutes a new accepted version',async({page})=>{
  const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openThreadAnalysis(page);let box=await inspect(page);
  await box.getByRole('button',{name:'Include this accepted version',exact:true}).click();await form(page).locator('textarea').fill('A durable draft');await page.reload();await openThreadAnalysis(page);box=form(page).locator('[data-pm-context-bundle]');await box.locator('summary').click();
  await expect(form(page).getByRole('button',{name:'Request PM analysis',exact:true})).toBeDisabled();f.bundle.revision='2';await box.getByRole('button',{name:'Preview accepted plan',exact:true}).click();
  await expect(box.locator('[data-pm-context-selection]')).toContainText('pmrev_'+'1'.repeat(32));await expect(form(page).getByRole('button',{name:'Request PM analysis',exact:true})).toBeDisabled();
  await box.getByRole('button',{name:'Include this accepted version',exact:true}).click();await form(page).getByRole('button',{name:'Request PM analysis',exact:true}).click();
  await expect.poll(() => f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params.context_bundle_ref.revision_id).toBe('pmrev_'+'2'.repeat(32));
});
test('unknown request recovers original selected version and key after source loss',async({page})=>{
  const f=fixture();f.state.lost=true;await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openThreadAnalysis(page);const box=await inspect(page);
  await box.getByRole('button',{name:'Include this accepted version',exact:true}).click();await form(page).locator('textarea').fill('One fixed request');await form(page).getByRole('button',{name:'Request PM analysis',exact:true}).click();await expect(form(page)).toContainText('Reply unavailable');
  const first=structuredClone(f.state.writes[0]);f.bundle.denied=true;f.bundle.available=false;f.bundle.revision='2';await page.reload();await openThreadAnalysis(page);const restored=form(page).locator('[data-pm-context-bundle]');await restored.locator('summary').click();
  await expect(restored).toContainText('pmrev_'+'1'.repeat(32));await expect(restored.getByRole('button',{name:'Use discussion only',exact:true})).toBeDisabled();await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
  await expect.poll(() => f.state.writes.length).toBe(2);
  expect(f.state.writes[1].body).toEqual(first.body);expect(f.state.writes[1].idempotency_key).toBe(first.idempotency_key);expect(f.bundle.reads).toBe(1);
});
test('denied preview removes stale options and blocks saved selection',async({page})=>{
  const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openThreadAnalysis(page);const box=await inspect(page);await box.getByRole('button',{name:'Include this accepted version',exact:true}).click();
  f.bundle.denied=true;await box.getByRole('button',{name:'Preview accepted plan',exact:true}).click();await expect(box).toContainText('Original source unavailable');await expect(box.locator('[data-pm-context-preview]')).toBeEmpty();await expect(form(page).getByRole('button',{name:'Request PM analysis',exact:true})).toBeDisabled();expect(f.state.writes).toHaveLength(0);
});

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) for (const failure of ['stale', 'invalid', 'api', 'transport']) test(`preview preserves its localized ${failure} reason ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
  try {
    const page = await context.newPage(), f = fixture(), errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await mountConversation(page, false, f.dispatch); await page.goto(pmRoute); await openThreadAnalysis(page);
    const request = form(page), box = request.locator('[data-pm-context-bundle]'); await box.locator('summary').click();
    const preview = box.getByRole('button', {name: locale === 'en' ? 'Preview accepted plan' : '預覽已採用計畫', exact: true});
    await preview.click(); await box.getByRole('button', {name: locale === 'en' ? 'Include this accepted version' : '納入這個已採用版本', exact: true}).click();
    await request.locator('textarea').fill('Retain this analysis draft');
    const original = await box.locator('input[type=hidden]').inputValue();
    if (failure === 'stale') f.bundle.stale = true;
    else if (failure === 'invalid') f.bundle.invalid = true;
    else if (failure === 'api') f.bundle.denied = true;
    else await page.route('**/context-bundle', route => route.abort('failed'));
    await preview.click();
    const sentence = failure === 'stale'
      ? locale === 'en' ? 'The discussion changed. Preview the accepted plan again.' : '討論已變更，請重新預覽已採用計畫。'
      : failure === 'invalid'
        ? locale === 'en' ? 'The accepted context preview is invalid or does not match this discussion.' : '採用文件預覽無效，或不屬於目前討論。'
        : locale === 'en' ? 'The accepted plan or its original sources are unavailable.' : '無法讀取已採用計畫或其原始來源。';
    await expect(box.locator('[data-error-summary]')).toHaveText(sentence);
    await expect(box.locator('[data-api-error]')).toHaveCount(1);
    const error = box.locator('[data-api-error]');
    await expect(error.getByText(sentence, {exact: true})).toHaveCount(1);
    await expectNoNestedErrorDetails(error);
    await expect(error.locator('details')).toHaveCount(0);
    if (failure === 'stale' || failure === 'invalid') await expectClientError(box, sentence);
    else {
      const code = failure === 'api' ? 'NOT_FOUND' : 'NETWORK_ERROR';
      await expectErrorDetails(box, failure === 'api' ? 'Original source unavailable' : code, code);
    }
    const exportAction = error.locator('[data-export-diagnostics]');
    await expect(exportAction).toBeVisible();
    await expect(exportAction).toHaveText(locale === 'en' ? 'Export diagnostics' : '匯出診斷資料');
    // The panel owns the disclosure: its sentence and diagnostics close together.
    await box.locator(':scope > summary').click();
    await expect(error.locator('[data-error-summary]')).toBeHidden();
    await expect(error.locator('[data-error-details]')).toBeHidden();
    await expect(exportAction).toBeHidden();
    await box.locator(':scope > summary').click();
    await expect(error.locator('[data-error-summary]')).toHaveText(sentence);
    await expect(exportAction).toBeVisible();
    await expect(box.locator('[data-pm-context-preview]')).toBeEmpty();
    await expect(box.locator('input[type=hidden]')).toHaveValue(original);
    await expect(request.locator('textarea')).toHaveValue('Retain this analysis draft');
    await expect(request.locator('button[type=submit]')).toBeDisabled(); expect(f.state.writes).toEqual([]);
    const folder = process.env.PM_CONTEXT_ERROR_SCREENSHOTS || (failure === 'stale' ? process.env.BATC_FIX1_SHOTS : undefined);
    if (folder && (failure === 'stale' || failure === 'api')) {
      mkdirSync(folder, {recursive: true}); await box.locator('[data-api-error]').scrollIntoViewIfNeeded();
      await page.screenshot({animations: 'disabled', path: `${folder}/context-${failure}-${locale}-${width}.png`});
    }
    expect(errors).toEqual([]); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally {await context.close();}
});
test('coverage and selected revision fit both locales and all widths',async({browser,baseURL},testInfo)=>{
  const folder=process.env.PM_CONTEXT_SCREENSHOTS||testInfo.outputPath('screenshots');mkdirSync(folder,{recursive:true});
  for(const locale of ['en','zh-TW']){const context=await browser.newContext({baseURL,locale}),page=await context.newPage(),f=fixture();f.bundle.partial=true;await mountConversation(page,false,f.dispatch);
    for(const width of [1440,768,390]){await page.setViewportSize({width,height:900});await page.goto(pmRoute);await openThreadAnalysis(page);const box=form(page).locator('[data-pm-context-bundle]');if(!await box.evaluate((el:HTMLDetailsElement)=>el.open))await box.locator('summary').click();
      await box.getByRole('button',{name:locale==='en'?'Preview accepted plan':'預覽已採用計畫',exact:true}).click();await box.getByRole('button',{name:locale==='en'?'Include this partial document':'納入這份不完整文件',exact:true}).click();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:`${folder}/context-${locale}-${width}.png`,fullPage:true});}await context.close();}
});
