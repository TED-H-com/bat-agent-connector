import {openThreadAnalysis} from './pm-navigation.ts';
import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {mountConversation} from './conversation-fixture';
import {pmRoute, threadId, workspaceId} from './pm-fixture';

import {answer, messageId, nativeFixture, nativeModel} from './pm-analysis-fixture';

const form = (page:any) => page.locator('[data-pm-action="pm.run.analyze"]');

for (const nativeTransport of [false, true]) test.describe(nativeTransport ? 'native PM bridge' : 'web PM bridge', () => {
  test('one bounded analysis preserves original source roles and model provenance', async({page}) => {
    const f = nativeFixture(); await mountConversation(page, nativeTransport, f.dispatch); await page.goto(pmRoute);await openThreadAnalysis(page);
    const request = form(page);
    await expect(request).toBeVisible();
    await expect(page.locator('[data-pm-native-eligibility]')).toContainText('Sign-in and model availability are not yet verified');
    await expect(page.locator('[data-pm-native-analysis]')).toContainText('files and live sessions are not opened');
    await request.getByLabel('What should the PM help you work through?', {exact:true}).fill('Which known assumptions matter before planning?');
    await request.getByLabel('Model (optional)', {exact:true}).fill(nativeModel);
    await request.getByRole('button',{name:'Request PM analysis',exact:true}).click();
    await expect(page.locator('[data-pm-role="assistant"]')).toContainText(answer);
    await expect(page.locator('[data-pm-role="assistant"]')).toContainText(nativeModel);
    await expect(page.locator('[data-pm-role="assistant"]')).toContainText('Original native reply · completion recorded');
    await page.locator('[data-pm-role="assistant"] summary').click();
    await expect(page.locator('[data-pm-role="assistant"]')).toContainText('native-thread');
    await expect(page.locator('[data-pm-role="assistant"]')).toContainText('native-turn');
    await expect(page.locator('[data-pm-role="assistant"]')).toContainText('c'.repeat(64));
    await expect(page.locator('.pm-notes script')).toHaveCount(0);
    expect(f.state.writes).toHaveLength(1);
    expect(f.state.writes[0].body).toEqual({action:'pm.run.analyze',target:{workspace_id:workspaceId,thread_id:threadId},
      params:{purpose:'Which known assumptions matter before planning?',model:nativeModel},preconditions:{expected_version:1}});
    expect(f.state.thread.mode).toBe('idea'); expect(f.state.draft).toBeNull();
    expect(f.state.inputs[1].status).toBe('agent_assumption');
    expect(f.state.messages[0].actor).toBe('reader');
  });

  test('lost response and later unavailable runtime preserve exact intent and one reply', async({page}) => {
    const f=nativeFixture(); f.state.lost=true;
    await mountConversation(page,nativeTransport,f.dispatch); await page.goto(pmRoute);await openThreadAnalysis(page);
    await form(page).getByLabel('What should the PM help you work through?',{exact:true}).fill('Analyze once, preserve this request');
    await form(page).getByRole('button',{name:'Request PM analysis',exact:true}).click();
    await expect(form(page)).toContainText('Reply unavailable');
    f.state.reject={status:403,code:'FORBIDDEN'};
    await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(form(page).getByRole('button',{name:'Edit a new request',exact:true})).toBeHidden();
    f.native.eligible=false; f.native.reason='PM_NATIVE_EXECUTABLE_UNAVAILABLE'; f.state.reject=null;
    await page.reload();await openThreadAnalysis(page);
    await expect(form(page).getByLabel('What should the PM help you work through?',{exact:true})).toBeDisabled();
    await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(form(page).getByLabel('What should the PM help you work through?',{exact:true})).toHaveValue('');
    expect(f.state.ops.size).toBe(1); expect(f.state.messages.filter(message=>message.message_id===messageId)).toHaveLength(1);
    expect(f.state.writes).toHaveLength(3);
    for(const entry of f.state.writes.slice(1)) {
      expect(entry.idempotency_key).toBe(f.state.writes[0].idempotency_key);
      expect(entry.body).toEqual(f.state.writes[0].body);
    }
  });

  test('pending operation checks its known ID and never creates another turn', async({page}) => {
    const f=nativeFixture(); f.native.pending=true;
    await mountConversation(page,nativeTransport,f.dispatch); await page.goto(pmRoute);await openThreadAnalysis(page);
    await form(page).getByLabel('What should the PM help you work through?',{exact:true}).fill('Compare two directions');
    await form(page).getByRole('button',{name:'Request PM analysis',exact:true}).click();
    await expect(form(page)).toContainText('original operation is still pending');
    await page.reload();await openThreadAnalysis(page);
    await expect(page.locator('[data-pm-native-runs]')).toContainText('Analysis running');
    await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(page.locator('[data-pm-role="assistant"]')).toHaveCount(0);
    f.complete();
    await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(page.locator('[data-pm-native-runs]')).toContainText('Analysis completed');
    await expect(page.locator('[data-pm-role="assistant"]')).toHaveCount(1);
    expect(f.state.writes).toHaveLength(1);
    expect(f.state.writes[0].body.params).toEqual({purpose:'Compare two directions'});
  });

  test('scope and installation eligibility do not invent execution capability', async({page}) => {
    const f=nativeFixture(); f.native.eligible=false;f.native.reason='PM_NATIVE_PLATFORM_UNSUPPORTED';
    await mountConversation(page,nativeTransport,f.dispatch); await page.goto(pmRoute);await openThreadAnalysis(page);
    await expect(form(page).getByRole('button',{name:'Request PM analysis',exact:true})).toBeDisabled();
    await expect(page.locator('[data-pm-native-eligibility]')).toContainText('requires a Linux workspace service');
    f.native.eligible=true;f.state.start=false;await page.reload();await openThreadAnalysis(page);
    await expect(form(page).getByRole('button',{name:'Request PM analysis',exact:true})).toBeDisabled();
    await expect(page.locator('[data-pm-native-eligibility]')).toContainText('permission to start work');
    f.native.available=false;await page.reload();await openThreadAnalysis(page);
    await expect(page.locator('[data-pm-native-analysis]')).toHaveCount(0);
    expect(f.state.writes).toHaveLength(0);
  });

  test('assistant role without native provenance never implies completion', async({page}) => {
    const f=nativeFixture();
    f.state.messages.push({message_id:messageId,sequence:2,actor:'unknown-agent',role:'assistant',content:'Unverified draft',created_at:1} as any);
    await mountConversation(page,nativeTransport,f.dispatch);await page.goto(pmRoute);await openThreadAnalysis(page);
    await expect(page.locator('[data-pm-role="assistant"]')).toContainText('Completion evidence is unavailable');
    await expect(page.getByText('Original native reply · completion recorded',{exact:true})).toHaveCount(0);
    expect(f.state.writes).toHaveLength(0);
  });
});

test('native PM discussion stays legible in both languages and all shared widths',async({browser,baseURL},testInfo)=>{
  const folder=process.env.PM_NATIVE_SCREENSHOTS||testInfo.outputPath('screenshots');mkdirSync(folder,{recursive:true});
  for(const locale of ['en','zh-TW']) {
    const context=await browser.newContext({locale,baseURL});const page=await context.newPage();const f=nativeFixture();
    await mountConversation(page,false,f.dispatch);
    for(const [width,height] of [[1440,900],[768,900],[390,844]]) {
      await page.setViewportSize({width,height});await page.goto(pmRoute);await openThreadAnalysis(page);
      await expect(page.locator('[data-pm-native-analysis]')).toBeVisible();
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await page.screenshot({path:`${folder}/native-analysis-${locale}-${width}.png`,fullPage:true});
    }
    await form(page).locator('textarea').fill('Which source claims should we verify before choosing a direction?');
    await form(page).getByRole('button',{name:locale==='en'?'Request PM analysis':'請 PM 分析',exact:true}).click();
    await expect(page.locator('[data-pm-role="assistant"]')).toContainText(answer);
    if (await page.locator('.pd-panel-overlay').count()) await page.keyboard.press('Escape');
    await page.locator('[data-pm-role="assistant"] summary').click();
    for(const [width,height] of [[1440,900],[390,844]]) {
      await page.setViewportSize({width,height});
      await page.evaluate(()=>window.scrollTo(0,0));
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await page.screenshot({path:`${folder}/native-reply-${locale}-${width}.png`,fullPage:true});
    }
    await context.close();
  }
});
