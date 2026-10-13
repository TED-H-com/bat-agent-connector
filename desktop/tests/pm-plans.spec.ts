import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture, pmRoute, workspaceId, threadId, inputA} from './pm-fixture.ts';

function planning() {
  const fixture = pmFixture(), s = fixture.state;
  s.plans = true; s.start = true; s.thread.mode = 'planning';
  s.thread.draft_id='pmd_'+'6'.repeat(32);
  s.draft = {draft_id:s.thread.draft_id,title: s.thread.title, rationale: 'Adopt the focused first visit direction', selected_inputs: [s.inputs[0]], candidate_inputs: [s.inputs[1]], unknowns: [{slot:'finance'}]};
  return fixture;
}
const action = (page:any, name:string) => page.locator(`[data-pm-action="${name}"]`);
async function savePlan(page:any, text = '# First visit\n\nKeep <script>literal text</script> safe.') {
  await page.locator('[data-pm-revision-editor] > summary').click();
  await page.getByLabel('Plan title', {exact:true}).fill('A focused first visit');
  await page.getByLabel('Plan document (Markdown)', {exact:true}).fill(text);
  await page.getByRole('button',{name:'Save plan revision',exact:true}).click();
  await expect(page.locator('[data-pm-revision]')).toHaveCount(1);
  await page.locator('[data-pm-revision] > summary').click();
  await expect(page.locator('[data-pm-revision] .pm-plan-document')).toBeVisible();
  const adoption = page.locator('[data-pm-plan-adopt]');
  if (await adoption.count()) await adoption.locator(':scope > summary').click();
}

for (const native of [false,true]) test.describe(native ? 'native plans' : 'web plans', () => {
  test('Planning can select later inputs while keeping saved document revisions fixed', async({page}) => {
    const f=planning(); await mountConversation(page,native,f.dispatch); await page.goto(pmRoute); await savePlan(page);
    const original=structuredClone(f.state.revisions[0]);
    await expect(page.locator('[data-pm-information]')).not.toHaveAttribute('open', '');
    await page.locator('[data-pm-information] > summary').click();
    await page.locator('[data-pm-add-input] > summary').click();
    const record=action(page,'pm.input.record');
    await record.getByLabel('Topic',{exact:true}).selectOption('finance');
    await record.getByLabel('Statement or question',{exact:true}).fill('Budget still needs a decision');
    await record.getByRole('button',{name:'Save input',exact:true}).click();
    const last=f.state.inputs.at(-1);
    await page.locator(`[data-input-id="${last.input_id}"] input[type=checkbox]`).check();
    await page.locator('[data-pm-planning-revision] > summary').click();
    const revise=action(page,'pm.thread.plan.revise');
    await revise.getByLabel('Why update this direction?',{exact:true}).fill('Include the budget question in this planning draft.');
    f.state.lost=true;
    await revise.getByRole('button',{name:'Save updated Planning draft',exact:true}).click();
    await expect(revise).toContainText('Reply unavailable');
    await page.reload(); await expect(page.locator('[data-pm-information]')).toHaveAttribute('open',''); await page.locator('[data-pm-planning-revision] > summary').click();
    await revise.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(4);
    expect(f.state.writes[2].body.params.selected_input_ids).toEqual([inputA,last.input_id]);
    expect(f.state.writes[2].body.preconditions.expected_draft_id).toBe('pmd_'+'6'.repeat(32));
    expect(f.state.writes[3].body).toEqual(f.state.writes[2].body);
    expect(f.state.writes[3].idempotency_key).toBe(f.state.writes[2].idempotency_key);
    expect(f.state.revisions[0]).toEqual(original); expect(f.state.planHead.accepted_revision_id).toBeNull();
  });
  test('fixed plan draft and adoption stay in the discussion and do not dispatch', async({page}) => {
    const f = planning(); f.state.bound = true;
    await mountConversation(page,native,f.dispatch); await page.goto(pmRoute);
    await savePlan(page);
    await expect(page.locator('.pm-plan-document')).toContainText('<script>literal text</script>');
    await expect(page.locator('.pm-plan-document').getByRole('heading', {name:'First visit',exact:true})).toBeVisible();
    await expect(page.locator('.pm-plans script')).toHaveCount(0);
    await page.getByText('Management domains and source status',{exact:true}).click();
    await page.locator('.pm-plan-domains summary').filter({hasText:'Scope'}).click();
    await expect(page.locator('.pm-plan-domains')).toContainText('User confirmed');
    const adopt = action(page,'pm.plan.adopt');
    await adopt.getByLabel('Why adopt this version?',{exact:true}).fill('Use this scope for the next planning discussion.');
    await adopt.getByRole('button',{name:'Adopt this plan',exact:true}).click();
    await expect(page.locator('[data-pm-accepted-plan]')).toContainText('A focused first visit');
    expect(f.state.writes.map(row=>row.body.action)).toEqual(['pm.plan.draft','pm.plan.adopt']);
    expect(f.state.writes[1].body.preconditions).toEqual({expected_accepted_revision_id:null});
    expect(f.state.messages).toHaveLength(1);
    expect([...f.state.ops.values()].at(-1).result.execution_authorized).toBe(false);
  });
  test('lost adoption reply recovers the original key even when the plan is already accepted', async({page}) => {
    const f = planning(); f.state.bound = true;
    await mountConversation(page,native,f.dispatch); await page.goto(pmRoute); await savePlan(page);
    const adopt = action(page,'pm.plan.adopt');
    await adopt.getByLabel('Why adopt this version?',{exact:true}).fill('Keep the original adoption.'); f.state.lost = true;
    await adopt.getByRole('button',{name:'Adopt this plan',exact:true}).click(); await expect(adopt).toContainText('Reply unavailable');
    await page.reload(); await page.locator('[data-pm-revision] > summary').click();
    await expect(adopt.getByRole('button',{name:'Check original request',exact:true})).toBeVisible();
    await adopt.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(3);
    expect(f.state.writes[2].idempotency_key).toBe(f.state.writes[1].idempotency_key);
    expect(f.state.writes[2].body).toEqual(f.state.writes[1].body);
    expect(f.state.ops.size).toBe(2);
  });
  test('repository preview prepares an owned worktree before plan adoption becomes available', async({page}) => {
    const f = planning(); await mountConversation(page,native,f.dispatch); await page.goto(pmRoute); await savePlan(page);
    await expect(action(page,'pm.plan.adopt').getByRole('button',{name:'Adopt this plan',exact:true})).toBeDisabled();
    await page.getByText('Prepare discussion context',{exact:true}).click();
    const prepare = action(page,'pm.run.prepare');
    await prepare.getByLabel('What should this discussion help decide?',{exact:true}).fill('Prepare the plan worktree');
    await page.locator('[data-pm-worktree-choice] > summary').click();
    const target = {repository:'example/library',host:'fixture-host',workspace_id:'fixture-workspace'};
    await prepare.getByRole('combobox',{name:'Save in a PM worktree',exact:true}).selectOption(JSON.stringify(target));
    await prepare.getByRole('button',{name:'Preview published revision',exact:true}).click();
    await expect(page.locator('[data-pm-worktree-choice]')).toContainText('a'.repeat(40));
    await prepare.getByRole('button',{name:'Save context package',exact:true}).click();
    await expect.poll(()=>f.state.bound).toBe(true);
    await page.locator('[data-pm-revision] > summary').click();
    await page.locator('[data-pm-plan-adopt] > summary').click();
    await expect(action(page,'pm.plan.adopt').getByRole('button',{name:'Adopt this plan',exact:true})).toBeEnabled();
    expect(f.state.writes.at(-1).body.params.repository_binding).toEqual({...target,source_ref:'refs/heads/main',source_sha:'a'.repeat(40),repository_id:7,binding_digest:'b'.repeat(64)});
  });
  for(const changedPrincipal of [false,true])test(`access remount ${changedPrincipal?'isolates another principal':'preserves the open plan editor'} while rereading sources`,async({page})=>{
    const f=planning();let reset=false,bootstraps=0,held=false,release:()=>void=()=>{},reads=0;
    const gate=new Promise<void>(resolve=>release=resolve);
    await mountConversation(page,native,async(input:any)=>{
      const path=new URL(input.path,'http://fixture').pathname;
      if(path==='/bootstrap'&&++bootstraps===2){held=true;await gate;}
      if(path==='/events'&&reset){reset=false;return {status:409,data:{error:{code:'EVENT_CURSOR_RESET',reason:'access_changed'}}};}
      if(path===`/pm/workspaces/${workspaceId}/threads/${threadId}`)reads++;
      return f.dispatch(input);
    });
    await page.goto(pmRoute);const editor=page.locator('[data-pm-revision-editor]');
    await editor.locator(':scope > summary').click();
    await editor.getByLabel('Plan title',{exact:true}).fill('Original private plan draft');
    await editor.getByLabel('Plan document (Markdown)',{exact:true}).fill('Keep the same reviewed text.');
    if(changedPrincipal)f.state.principal='person-2';
    reset=true;
    try{
      await expect.poll(()=>held).toBe(true);
      await expect(page.locator('.pm-notes')).toHaveCount(0);
      await expect(editor).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
    }finally{release();}
    await expect.poll(()=>reads).toBe(2);
    await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
    if(changedPrincipal){
      await expect(editor).not.toHaveAttribute('open','');
      await editor.locator(':scope > summary').click();
      await expect(editor.getByLabel('Plan title',{exact:true})).not.toHaveValue('Original private plan draft');
      await expect(editor.getByLabel('Plan document (Markdown)',{exact:true})).toHaveValue('');
      expect(f.state.writes).toHaveLength(0);
    }else{
      await expect(editor).toHaveAttribute('open','');
      await expect(editor.getByLabel('Plan title',{exact:true})).toHaveValue('Original private plan draft');
      await expect(editor.getByLabel('Plan document (Markdown)',{exact:true})).toHaveValue('Keep the same reviewed text.');
      await editor.getByRole('button',{name:'Save plan revision',exact:true}).click();
      await expect(page.locator('[data-pm-revision]')).toHaveCount(1);
      expect(f.state.writes).toHaveLength(1);
      expect(f.state.writes[0].body.params.title).toBe('Original private plan draft');
    }
  });
  test('planning refresh keeps an opened information disclosure and does not expand one the reader closed', async({page}) => {
    const f = planning();
    let reads = 0;
    await mountConversation(page, native, async input => {
      if (new URL(input.path, 'http://fixture').pathname === `/pm/workspaces/${workspaceId}/threads/${threadId}`) reads += 1;
      return f.dispatch(input);
    });
    await page.goto(pmRoute);
    const information = page.locator('[data-pm-information]');
    const addInput = page.locator('[data-pm-add-input]');
    const refresh = page.getByRole('button', {name: 'Refresh', exact: true});
    const refreshThread = async () => {
      const before = reads;
      await refresh.click();
      await expect.poll(() => reads).toBeGreaterThan(before);
      await expect(refresh).toBeEnabled();
    };
    await expect(information).not.toHaveAttribute('open', '');
    await information.locator(':scope > summary').click();
    await addInput.locator(':scope > summary').click();
    await expect(addInput.getByLabel('Statement or question', {exact: true})).toBeVisible();
    await refreshThread();
    await expect(information).toHaveAttribute('open', '');
    await expect(addInput).toHaveAttribute('open', '');
    await expect(addInput.getByLabel('Statement or question', {exact: true})).toBeVisible();
    await addInput.locator(':scope > summary').click();
    await expect(addInput).not.toHaveAttribute('open', '');
    await refreshThread();
    await expect(information).toHaveAttribute('open', '');
    await expect(addInput).not.toHaveAttribute('open', '');
    const revise = action(page, 'pm.thread.plan.revise');
    await page.locator('[data-pm-planning-revision] > summary').click();
    await revise.getByLabel('Why update this direction?', {exact: true}).fill('Keep this pending request without reopening a closed section.');
    f.state.lost = true;
    await revise.getByRole('button', {name: 'Save updated Planning draft', exact: true}).click();
    await expect(revise).toContainText('Reply unavailable');
    await information.locator(':scope > summary').click();
    await expect(information).not.toHaveAttribute('open', '');
    await refreshThread();
    await expect(information).not.toHaveAttribute('open', '');
    await information.locator(':scope > summary').click();
    await expect(revise.getByRole('button', {name: 'Check original request', exact: true})).toBeVisible();
    expect(f.state.writes).toHaveLength(1);
  });
  test('unfinished plan edits survive refresh and readers cannot create or adopt revisions', async({page}) => {
    const f = planning(); await mountConversation(page,native,f.dispatch); await page.goto(pmRoute);
    await page.locator('[data-pm-revision-editor] > summary').click();
    await page.getByLabel('Plan document (Markdown)',{exact:true}).fill('A scope choice still being considered.');
    f.state.cursor++; await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect(page.getByLabel('Plan document (Markdown)',{exact:true})).toHaveValue('A scope choice still being considered.');
    f.state.role='reader'; await page.reload(); await page.locator('[data-pm-revision-editor] > summary').click();
    await expect(page.getByRole('button',{name:'Save plan revision',exact:true})).toBeDisabled();
    expect(f.state.writes).toHaveLength(0);
  });
});

for (const locale of ['en','zh-TW']) for (const width of [390,768,1440]) test(`plan layout ${locale} ${width}`, async({browser,baseURL})=>{
  const context=await browser.newContext({locale,baseURL,viewport:{width,height:1050}}),page=await context.newPage();
  const f = planning(); f.state.bound = true;
  await f.dispatch({method:'POST',path:'/operations',idempotency_key:'visual-plan',body:{action:'pm.plan.draft',target:{workspace_id:workspaceId,thread_id:threadId},
    params:{title:'First visit / 首次使用',markdown:'A clear first action.\n\nScope: preserve the conversation and its sources.\n\n第一個操作清楚可見；討論與來源保留在同一個畫面。',previous_revision_id:null},preconditions:{expected_version:1,expected_plan_version:0}}});
  await mountConversation(page,false,f.dispatch); await page.goto(pmRoute);
  await page.locator('[data-pm-revision] > summary').click();
  await expect(page.locator('.pm-plan-document')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await expect(page.locator('.pm-plans > h3')).toHaveText(locale==='zh-TW'?'專案計畫':'Project plan');
  expect(await page.locator('.pm-content').innerText()).not.toMatch(/\b(null|undefined)\b/);
  const out=process.env.BATC_PM_PLAN_SCREENSHOTS;
  if(out){await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/plan-${locale}-${width}.png`,fullPage:true});}
  expect(f.state.revisions[0].document.sections.scope.source_input_ids).toEqual([inputA]);
  await context.close();
});
