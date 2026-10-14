import {openThreadAnalysis} from './pm-navigation.ts';
import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {mountConversation} from './conversation-fixture';
import {pmRoute, threadId, workspaceId} from './pm-fixture';
import {answer, nativeFixture} from './pm-analysis-fixture';

const form = (page:any, bat=true) => page.locator(`[data-pm-action="${bat ? 'pm.run.bat.analyze' : 'pm.run.analyze'}"]`);
const selector = (page:any) => page.locator('[data-pm-analysis-adapter]');
const purpose = (page:any, bat=true) => form(page,bat).getByLabel('What should the PM help you work through?', {exact:true});
const submit = (page:any, bat=true) => form(page,bat).getByRole('button',{name:'Request PM analysis',exact:true});

for (const native of [false,true]) test.describe(native ? 'native BAT analysis' : 'web BAT analysis',()=>{
  test('explicit adapter choice preserves independent drafts and submits the exact BAT request',async({page})=>{
    const f=nativeFixture();f.bat.available=true;
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadAnalysis(page);
    await purpose(page,false).fill('Keep this central draft');
    await selector(page).selectOption('bat_analysis');
    await expect(page.locator('[data-pm-analysis-binding]')).toContainText('fixture-bat · workspace-source · demo/library');
    await expect(page.locator('[data-pm-native-eligibility]')).toContainText('one new, isolated Codex session');
    await purpose(page).fill('Analyze the saved discussion on the selected BAT workspace');
    await form(page).getByLabel('Model (optional)',{exact:true}).fill('fixture-bat-model');
    await selector(page).selectOption('native_analysis');
    await expect(purpose(page,false)).toHaveValue('Keep this central draft');
    await selector(page).selectOption('bat_analysis');
    await submit(page).click();
    await expect(page.locator('[data-pm-role="assistant"]')).toContainText(answer);
    await expect(page.locator('[data-pm-native-runs]')).toContainText('Selected BAT workspace');
    expect(f.state.writes).toHaveLength(1);
    expect(f.state.writes[0].body).toEqual({action:'pm.run.bat.analyze',target:{workspace_id:workspaceId,thread_id:threadId},
      params:{purpose:'Analyze the saved discussion on the selected BAT workspace',model:'fixture-bat-model'},preconditions:{expected_version:1}});
    await selector(page).selectOption('native_analysis');
    await expect(purpose(page,false)).toHaveValue('Keep this central draft');
    expect(f.state.thread.mode).toBe('idea');expect(f.state.draft).toBeNull();
  });
  test('unknown BAT receipt survives switching, reload and changed eligibility with its original key and CAS',async({page})=>{
    const f=nativeFixture();f.bat.available=true;f.state.lost=true;
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadAnalysis(page);
    await selector(page).selectOption('bat_analysis');await purpose(page).fill('Original BAT context only');await submit(page).click();
    await expect(form(page)).toContainText('Reply unavailable');
    await selector(page).selectOption('native_analysis');
    await expect(submit(page,false)).toBeDisabled();
    await expect(page.locator('[data-pm-analysis-recovery]')).toContainText('Check the original request');
    f.bat.eligible=false;f.bat.reason='REPOSITORY_BINDING_CHANGED';f.bat.binding=null;f.state.thread.version=9;
    await page.reload();await openThreadAnalysis(page);
    await expect(selector(page)).toHaveValue('native_analysis');await expect(submit(page,false)).toBeDisabled();
    await selector(page).selectOption('bat_analysis');
    await expect(purpose(page)).toBeDisabled();await expect(purpose(page)).toHaveValue('Original BAT context only');
    await expect(page.locator('[data-pm-analysis-binding]')).toBeHidden();
    await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(purpose(page)).toHaveValue('');
    expect(f.state.writes).toHaveLength(2);expect(f.state.ops.size).toBe(1);
    expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
    expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
    expect(f.state.writes[1].body.preconditions.expected_version).toBe(1);
  });
  test('unknown central request remains recoverable after choosing BAT',async({page})=>{
    const f=nativeFixture();f.bat.available=true;f.state.lost=true;
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadAnalysis(page);
    await purpose(page,false).fill('Original central request');await submit(page,false).click();
    await expect(form(page,false)).toContainText('Reply unavailable');
    await selector(page).selectOption('bat_analysis');await expect(submit(page)).toBeDisabled();await page.reload();await openThreadAnalysis(page);
    await expect(selector(page)).toHaveValue('bat_analysis');await expect(submit(page)).toBeDisabled();
    await selector(page).selectOption('native_analysis');
    await form(page,false).getByRole('button',{name:'Check original request',exact:true}).click();
    expect(f.state.writes).toHaveLength(2);expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
    expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
  });
  test('known pending BAT operation is read by original ID after switching and never resent',async({page})=>{
    const f=nativeFixture();f.bat.available=true;f.bat.pending=true;
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadAnalysis(page);
    await selector(page).selectOption('bat_analysis');await purpose(page).fill('Wait for the original BAT completion');await submit(page).click();
    await expect(form(page)).toContainText('original operation is still pending');
    await selector(page).selectOption('native_analysis');await expect(submit(page,false)).toBeDisabled();
    await page.reload();await openThreadAnalysis(page);await selector(page).selectOption('bat_analysis');
    await expect(page.locator('[data-pm-native-runs]')).toContainText('Analysis running');
    f.complete('bat-pm-linux-v1');
    await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(page.locator('[data-pm-role="assistant"]')).toHaveCount(1);
    await expect(page.locator('[data-pm-native-runs]')).toContainText('Analysis completed');
    expect(f.state.writes).toHaveLength(1);expect(f.state.ops.size).toBe(1);
  });
  test('BAT-only capability requires the prepared binding and does not claim model readiness',async({page})=>{
    const f=nativeFixture();f.native.available=false;f.bat.available=true;f.bat.eligible=false;f.bat.reason='PM_WORKTREE_REQUIRED';f.bat.binding=null;
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadAnalysis(page);
    await expect(selector(page)).toHaveValue('bat_analysis');await expect(form(page)).toBeVisible();
    await expect(form(page,false)).toBeHidden();await expect(submit(page)).toBeDisabled();
    await expect(page.locator('[data-pm-native-eligibility]')).toContainText('Prepare discussion context');
    f.bat.eligible=true;f.bat.reason=null;await page.reload();await openThreadAnalysis(page);
    await expect(page.locator('[data-pm-native-eligibility]')).toContainText('sign-in and model availability are not yet verified');
    f.state.start=false;await page.reload();await openThreadAnalysis(page);await expect(submit(page)).toBeDisabled();expect(f.state.writes).toHaveLength(0);
  });
  test('read withdrawal clears displayed binding and source text while retaining exact recovery',async({page})=>{
    const f=nativeFixture();f.bat.available=true;f.state.lost=true;
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadAnalysis(page);
    await selector(page).selectOption('bat_analysis');await purpose(page).fill('Private original analysis');await submit(page).click();
    await expect(form(page)).toContainText('Reply unavailable');
    f.state.failRead=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect(page.locator('[data-pm-analysis-binding]')).toBeHidden();
    await expect(page.locator('[data-pm-analysis-binding]')).toHaveText('');
    await expect(page.locator('[data-pm-native-analysis]')).toBeHidden();
    f.state.failRead=false;await page.reload();await openThreadAnalysis(page);
    await expect(purpose(page)).toHaveValue('Private original analysis');
    await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    expect(f.state.ops.size).toBe(1);expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
  });
});

test('BAT selector and source provenance fit both languages and all shared widths',async({browser,baseURL},testInfo)=>{
  const folder=process.env.PM_BAT_SCREENSHOTS||testInfo.outputPath('screenshots');mkdirSync(folder,{recursive:true});
  for(const locale of ['en','zh-TW']){
    const context=await browser.newContext({locale,baseURL});const page=await context.newPage();const f=nativeFixture();f.bat.available=true;
    await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openThreadAnalysis(page);await selector(page).selectOption('bat_analysis');
    for(const [width,height] of [[1440,900],[768,900],[390,844]]){
      await page.setViewportSize({width,height});
      // Wait for the open dock to move before the helper considers its toggle.
      if(await page.locator('[data-discussion-panel-toggle]').getAttribute('aria-expanded')==='true')await expect(page.locator('[data-discussion-panel]')).toBeVisible();
      await openThreadAnalysis(page);
      await expect(form(page)).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await page.screenshot({path:`${folder}/bat-analysis-${locale}-${width}.png`,fullPage:true});
    }
    await context.close();
  }
});
