import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {mountConversation} from './conversation-fixture';
import {pmFixture, pmRoute, threadId, workspaceId} from './pm-fixture';

const runId = 'pmr_' + 'd'.repeat(32), messageId = 'pmm_' + 'e'.repeat(32);
const nativeModel = 'fixture-native-model';
const answer = 'The saved sources support simplifying the first action. The tour is still a proposal. <script>never execute</script>';
function nativeFixture() {
  const f = pmFixture(); f.state.start = true;
  const native = {available: true, eligible: true, reason: null as string|null, pending: false};
  const capability = () => ({adapter: 'codex-native-linux', contract: 'codex-pm-linux-v1', execution_scope: 'frozen_context_only',
    eligible_for_preflight: native.eligible, reason: native.reason, options_confirmed: false, auth_validity: 'unverified', model_execution_verified: false});
  const complete = () => {
    const run = f.state.runs.find(run => run.run_id === runId);
    if (!run || run.state === 'completed') return;
    const provenance = {run_id: runId, native_turn_id: 'native-turn', native_message_id: 'native-answer',
      sdk_session_id: 'native-thread', model: nativeModel, completeness: 'complete', context_digest: 'c'.repeat(64),
      source_refs: {adapter: 'codex-native-linux', native_thread_id: 'native-thread'}};
    f.state.messages.push({message_id: messageId, sequence: 2, actor: 'pm:' + workspaceId,
      role: 'assistant', provenance, content: answer, created_at: Date.parse('2026-10-11T12:02:00Z') / 1000} as any);
    run.state = 'completed'; run.outcome = {message_id: messageId, actual_model: nativeModel, completeness: 'complete'};
    const op = [...f.state.ops.values()].find(op => op.operation_id === run.operation_id);
    op.status = 'succeeded'; op.result = {run_id: runId, workspace_id: workspaceId, thread_id: threadId,
      state: run.state, context_digest: 'c'.repeat(64), adapter: run.adapter, execution_authorized: false, outcome: run.outcome};
    f.state.thread.version++; f.state.cursor++;
  };
  const dispatch = async(input:any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (input.method === 'POST' && path === '/operations' && input.body.action === 'pm.run.analyze') {
      f.state.writes.push(structuredClone(input));
      if (f.state.reject) return {status: f.state.reject.status, data: {error: {code: f.state.reject.code, message: 'Rejected request', admission_refused: true}}};
      let op = f.state.ops.get(input.idempotency_key);
      if (!op) {
        const operation_id = 'op_' + 'f'.repeat(32);
        op = {...structuredClone(input.body), actor: f.state.actor, idempotency_key: input.idempotency_key,
          operation_id, status: 'running', result: null};
        f.state.ops.set(input.idempotency_key, op);
        f.state.runs.push({run_id: runId, workspace_id: workspaceId, thread_id: threadId,
          purpose: input.body.params.purpose, state: 'running', adapter: 'codex-native-linux', operation_id,
          created_at: Date.parse('2026-10-11T12:01:00Z') / 1000,
          context: {thread_version: f.state.thread.version, messages: structuredClone(f.state.messages), inputs_by_status: {}, coverage: {}}});
        if (!native.pending) complete();
      }
      if (f.state.lost) {f.state.lost = false; return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status: 200, data: {operation: op}};
    }
    const response = await f.dispatch(input);
    if (path === '/capabilities' || path === '/bootstrap') {
      const caps = path === '/bootstrap' ? response.data.capabilities : response.data;
      if (native.available) {
        caps.features.pm_workspaces.native_analysis = true;
        caps.actions.push({action:'pm.run.analyze',allowed:f.state.manage && f.state.start});
      }
    }
    if (path.endsWith('/capabilities') && path.startsWith('/pm/')) response.data.native_analysis = capability();
    return response;
  };
  return {...f, native, dispatch, complete};
}
const form = (page:any) => page.locator('[data-pm-action="pm.run.analyze"]');

for (const nativeTransport of [false, true]) test.describe(nativeTransport ? 'native PM bridge' : 'web PM bridge', () => {
  test('one bounded analysis preserves original source roles and model provenance', async({page}) => {
    const f = nativeFixture(); await mountConversation(page, nativeTransport, f.dispatch); await page.goto(pmRoute);
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
    await mountConversation(page,nativeTransport,f.dispatch); await page.goto(pmRoute);
    await form(page).getByLabel('What should the PM help you work through?',{exact:true}).fill('Analyze once, preserve this request');
    await form(page).getByRole('button',{name:'Request PM analysis',exact:true}).click();
    await expect(form(page)).toContainText('Reply unavailable');
    f.state.reject={status:403,code:'FORBIDDEN'};
    await form(page).getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(form(page).getByRole('button',{name:'Edit a new request',exact:true})).toBeHidden();
    f.native.eligible=false; f.native.reason='PM_NATIVE_EXECUTABLE_UNAVAILABLE'; f.state.reject=null;
    await page.reload();
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
    await mountConversation(page,nativeTransport,f.dispatch); await page.goto(pmRoute);
    await form(page).getByLabel('What should the PM help you work through?',{exact:true}).fill('Compare two directions');
    await form(page).getByRole('button',{name:'Request PM analysis',exact:true}).click();
    await expect(form(page)).toContainText('original operation is still pending');
    await page.reload();
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
    await mountConversation(page,nativeTransport,f.dispatch); await page.goto(pmRoute);
    await expect(form(page).getByRole('button',{name:'Request PM analysis',exact:true})).toBeDisabled();
    await expect(page.locator('[data-pm-native-eligibility]')).toContainText('requires a Linux workspace service');
    f.native.eligible=true;f.state.start=false;await page.reload();
    await expect(form(page).getByRole('button',{name:'Request PM analysis',exact:true})).toBeDisabled();
    await expect(page.locator('[data-pm-native-eligibility]')).toContainText('permission to start work');
    f.native.available=false;await page.reload();
    await expect(page.locator('[data-pm-native-analysis]')).toHaveCount(0);
    expect(f.state.writes).toHaveLength(0);
  });

  test('assistant role without native provenance never implies completion', async({page}) => {
    const f=nativeFixture();
    f.state.messages.push({message_id:messageId,sequence:2,actor:'unknown-agent',role:'assistant',content:'Unverified draft',created_at:1} as any);
    await mountConversation(page,nativeTransport,f.dispatch);await page.goto(pmRoute);
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
      await page.setViewportSize({width,height});await page.goto(pmRoute);
      await expect(page.locator('[data-pm-native-analysis]')).toBeVisible();
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await page.screenshot({path:`${folder}/native-analysis-${locale}-${width}.png`,fullPage:true});
    }
    await form(page).locator('textarea').fill('Which source claims should we verify before choosing a direction?');
    await form(page).getByRole('button',{name:locale==='en'?'Request PM analysis':'請 PM 分析',exact:true}).click();
    await expect(page.locator('[data-pm-role="assistant"]')).toContainText(answer);
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
