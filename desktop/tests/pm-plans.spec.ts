import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture, pmRoute, workspaceId, threadId, inputA} from './pm-fixture.ts';

function planning() {
  const fixture = pmFixture(), s = fixture.state;
  s.plans = true; s.start = true; s.thread.mode = 'planning';
  s.draft = {title: s.thread.title, rationale: 'Adopt the focused first visit direction', selected_inputs: [s.inputs[0]], candidate_inputs: [s.inputs[1]], unknowns: [{slot:'finance'}]};
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
}

for (const native of [false,true]) test.describe(native ? 'native plans' : 'web plans', () => {
  test('fixed plan draft and adoption stay in the discussion and do not dispatch', async({page}) => {
    const f = planning(); f.state.bound = true;
    await mountConversation(page,native,f.dispatch); await page.goto(pmRoute);
    await savePlan(page);
    await expect(page.locator('.pm-plan-document')).toContainText('<script>literal text</script>');
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
    await expect(action(page,'pm.plan.adopt').getByRole('button',{name:'Adopt this plan',exact:true})).toBeEnabled();
    expect(f.state.writes.at(-1).body.params.repository_binding).toEqual({...target,source_ref:'refs/heads/main',source_sha:'a'.repeat(40),repository_id:7,binding_digest:'b'.repeat(64)});
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
  await expect(page.locator('.pm-plans h3')).toHaveText(locale==='zh-TW'?'專案計畫':'Project plan');
  expect(await page.locator('.pm-content').innerText()).not.toMatch(/\b(null|undefined)\b/);
  const out=process.env.BATC_PM_PLAN_SCREENSHOTS;
  if(out){await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/plan-${locale}-${width}.png`,fullPage:true});}
  expect(f.state.revisions[0].document.sections.scope.source_input_ids).toEqual([inputA]);
  await context.close();
});
