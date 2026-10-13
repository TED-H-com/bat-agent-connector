import {openPmPanel, openThreadTab, openThreadBrief} from './pm-navigation.ts';
// One browser flow against actual PM HTTP actions; the central has no BAT hosts.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {delimiter, resolve} from 'node:path';
import {mkdir, mkdtemp, readFile, rm} from 'node:fs/promises';
import {homedir} from 'node:os';
import assert from 'node:assert/strict';
import {chromium, expect as baseExpect} from '@playwright/test';
const expect = baseExpect.configure({timeout: 30000});
// Open a disclosure idempotently; saved preferences can already have it open.
async function openDisclosure(summary) {
  const details = summary.locator('xpath=ancestor-or-self::details[1]');
  await expect(summary).toBeVisible();
  if (!await details.evaluate(node => node.open)) await summary.click();
  await expect(details).toHaveJSProperty('open', true);
}
const source = resolve('..'), backend = resolve(process.env.BATC_PM_BACKEND || source);
const python = process.env.BATC_PM_PYTHON || resolve(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const scratch = resolve(homedir(), 'agent-work/tmp/pytest');
await mkdir(scratch, {recursive:true});
const temporary = await mkdtemp(resolve(scratch, 'pm-central-run-'));
let child, browser, output, stopped, spawnError, failureDetails=async()=>({});
async function bounded(promise, milliseconds, message) {
  let timer;
  try {return await Promise.race([promise, new Promise((_, reject) => {timer = setTimeout(() => reject(Error(message)), milliseconds);})]);}
  finally {clearTimeout(timer);}
}
async function exitedWithin(milliseconds) {
  let timer;
  try {return await Promise.race([stopped.then(() => true), new Promise(resolve => {timer = setTimeout(() => resolve(false), milliseconds);})]);}
  finally {clearTimeout(timer);}
}
try {
  child = spawn(python, [resolve('tests/pm-central-fixture.py')], {cwd: backend,
    env: {...process.env, BATC_PM_FIXTURE_TEMP: temporary, PYTHONPATH: [resolve(backend, 'src'), backend].join(delimiter)}, stdio: ['pipe', 'pipe', 'inherit']});
  // A spawn error has no exit event; handle it immediately instead of leaving a rejected promise.
  stopped = new Promise(resolve => {
    child.once('exit', resolve);
    child.once('error', error => {if (!child.pid) {spawnError = error; resolve();}});
  });
  child.stdin.on('error', () => {}); // An exiting child may close stdin before the stop line arrives.
  output = createInterface({input: child.stdout});
  const lines = output[Symbol.asyncIterator]();
  const next = async () => {
    const line = await bounded(Promise.race([lines.next(), stopped.then(() => {throw spawnError || Error('PM fixture stopped');})]),
      30000, 'Timed out waiting for PM fixture output');
    if (line.done) throw spawnError || Error('PM fixture stopped');
    return JSON.parse(line.value);
  };
  const fixture = await next();
  browser = await chromium.launch();
  const origin = `http://127.0.0.1:${fixture.port}`, page = await browser.newPage({locale: 'en-US'}), errors = [];
  const requests=[];
  page.on('response',response=>{const url=new URL(response.url());if(url.pathname.startsWith('/api/v1/')){
    requests.push({method:response.request().method(),path:url.pathname,status:response.status()});if(requests.length>30)requests.shift();
  }});
  failureDetails=async()=>({requests,errors,forms:await page.locator('[data-pm-action]').evaluateAll(forms=>forms.map(form=>({
    action:form.getAttribute('data-pm-action'),status:form.querySelector('[role=status]')?.textContent,
    disabled:form.querySelector('button[type=submit]')?.disabled}))),status:await page.locator('.pm-status').allTextContents()});
  page.on('pageerror', error => errors.push(error.message));
  // Test this checkout's exact generated frontend even when the backend is supplied by an integration worktree.
  for (const file of ['app.js', 'app.css']) await page.route(`**/dashboard/${file}`, route =>
    readFile(resolve(source, 'src/bat_agent_connector/dashboard', file)).then(body => route.fulfill({body, contentType:file.endsWith('css')?'text/css':'text/javascript'})));
  await page.addInitScript(token => sessionStorage.setItem('batc.dashboard.token', token), fixture.token);
  await page.goto(origin + '/dashboard/#/pm');
  await page.getByLabel('Workspace name', {exact:true}).fill('PM contract fixture');
  await page.getByRole('button', {name:'Create workspace', exact:true}).click();
  await expect(page).toHaveURL(/#\/pm\/ws_[a-f0-9]{32}$/);
  await page.getByRole('link', {name:'Discuss with PM', exact:true}).click();
  await expect(page.locator('[data-discussion-composer]')).toBeFocused();
  await page.getByRole('button', {name:'Rename',exact:true}).click();
  await page.getByLabel('Discussion title', {exact:true}).fill('Choose a first improvement');
  await page.getByRole('button', {name:'Save',exact:true}).click();
  await page.locator('[data-discussion-composer]').fill('Keep the first action visible; retain a guided tour as an alternative.');
  await page.getByRole('button', {name:'Send', exact:true}).click();
  await expect(page).toHaveURL(/#\/pm\/ws_[a-f0-9]{32}\/discussions\/pmt_[a-f0-9]{32}$/);
  const [workspaceId, , threadId] = page.url().split('/').slice(-3);
  const read = async () => {
    const response = await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/threads/${threadId}`, {headers:{Authorization:'Bearer '+fixture.token}});
    assert.equal(response.status, 200); return response.json();
  };
  const note = page.locator('[data-pm-action="pm.message.append"]');
  await expect(page.locator('.pm-notes')).toContainText('Keep the first action visible');
  await page.getByRole('button',{name:'Rename',exact:true}).click();
  const rename=page.locator('[data-pm-action="pm.thread.rename"]');
  await rename.getByLabel('Discussion title',{exact:true}).fill('Choose the first useful improvement');
  await rename.getByRole('button',{name:'Save',exact:true}).click();
  await expect.poll(async()=>(await read()).thread.title).toBe('Choose the first useful improvement');
  const message = (await read()).messages[0];
  assert.equal(typeof message.created_at, 'number');
  await expect(page.locator('.pm-note time')).toHaveAttribute('datetime', new Date(message.created_at * 1000).toISOString());
  await openThreadTab(page,'inputs');
  await openDisclosure(page.locator('[data-pm-add-input] > summary'));
  const input = page.locator('[data-pm-action="pm.input.record"]');
  for (const [slot, value] of [['problem', 'Make the first action visible'], ['alternatives', 'Offer a guided tour later']]) {
    await input.getByLabel('Topic', {exact:true}).selectOption(slot);
    await input.getByLabel('Statement or question', {exact:true}).fill(value);
    await input.getByLabel('Source type or saved note', {exact:true}).selectOption('message:' + message.message_id);
    await input.getByRole('button', {name:'Save input', exact:true}).click();
    await expect(page.locator('.pm-facts')).toContainText(value);
  }
  const original = (await read()).inputs.find(item => item.slot === 'problem');
  await page.locator(`[data-input-id="${original.input_id}"] input[type=checkbox]`).check();
  await openDisclosure(page.locator(`[data-input-id="${original.input_id}"] summary`));
  const confirm = page.locator(`[data-input-id="${original.input_id}"] [data-pm-action="pm.input.confirm"]`);
  await confirm.getByLabel('Why this statement is confirmed', {exact:true}).fill('The owner selected this outcome');
  await confirm.getByRole('button', {name:'Confirm statement', exact:true}).click();
  await expect(page.locator('[data-pm-missing-selection]')).toBeVisible();
  const confirmed = (await read()).inputs.find(item => item.slot === 'problem');
  assert.notEqual(confirmed.input_id, original.input_id); assert.equal(confirmed.supersedes_input_id, original.input_id);
  await page.getByRole('checkbox', {name:'Selected input outside this view', exact:true}).click();
  await page.locator(`[data-input-id="${confirmed.input_id}"] input[type=checkbox]`).check();
  await openThreadTab(page,'plan');
  const plan = page.locator('[data-pm-action="pm.thread.plan"]');
  await plan.getByLabel('Why this direction', {exact:true}).fill('Prepare one change and retain the alternative');
  await plan.getByRole('button', {name:'Save Planning draft', exact:true}).click();
  await expect(page.locator('.pm-draft')).toContainText('Offer a guided tour later');
  await page.reload(); await openThreadTab(page,'plan'); await expect(page.locator('.pm-draft')).toContainText('Make the first action visible');
  const data = await read(); assert.equal(data.thread.mode, 'planning'); assert.equal(data.draft.execution_authorized, false);
  assert.equal(data.draft.adopted, false); assert.deepEqual(data.draft.selected_input_ids, [confirmed.input_id]);
  assert.equal(data.draft.candidate_inputs.length, 1); assert.equal(data.messages.length, 1);
  await openDisclosure(page.locator('[data-pm-revision-editor] > summary'));
  await page.getByLabel('Plan title', {exact:true}).fill('First useful action');
  await page.getByRole('button', {name:'Save plan revision', exact:true}).click();
  await expect(page.locator('[data-pm-revision]')).toHaveCount(1);
  await page.locator('[data-pm-revision-open]').click();
  await expect(page.locator('.pm-plan-document')).toContainText('First useful action');
  await expect(page.locator('[data-pm-action="pm.plan.adopt"] button[type="submit"]')).toBeDisabled();
  const plansResponse = await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/plans`, {headers:{Authorization:'Bearer '+fixture.token}});
  assert.equal(plansResponse.status, 200); const plans = await plansResponse.json();
  assert.equal(plans.revisions.length, 1); assert.equal(plans.head.accepted_revision_id, null);
  const revisionResponse = await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/plans/${plans.revisions[0].revision_id}`, {headers:{Authorization:'Bearer '+fixture.token}});
  const revision = await revisionResponse.json();
  assert.deepEqual(revision.plan.document.selected_inputs.map(input=>input.input_id), [confirmed.input_id]);
  assert.equal(revision.plan.adoption, null); assert.equal(revision.plan.document.execution_authorized, false);
  // Inputs retain their own tab while the plan and immutable revisions remain selected.
  await openThreadTab(page,'inputs');
  await openDisclosure(page.locator('[data-pm-information] > summary'));
  await openDisclosure(page.locator('[data-pm-add-input] > summary'));
  await input.getByLabel('Topic', {exact:true}).selectOption('finance');
  await input.getByLabel('Statement or question', {exact:true}).fill('Budget still needs an owner decision');
  await input.getByRole('button', {name:'Save input', exact:true}).click();
  await expect(page.locator('.pm-facts')).toContainText('Budget still needs an owner decision');
  const later = (await read()).inputs.find(item=>item.slot==='finance');
  await page.locator(`[data-input-id="${later.input_id}"] input[type=checkbox]`).check();
  await openThreadTab(page,'plan');
  await openDisclosure(page.locator('[data-pm-planning-revision] > summary'));
  const revise = page.locator('[data-pm-action="pm.thread.plan.revise"]');
  await revise.getByLabel('Why update this direction?', {exact:true}).fill('Include the new financial question without treating it as settled.');
  await revise.getByRole('button', {name:'Save updated Planning draft', exact:true}).click();
  await expect(page.locator('.pm-draft')).toContainText('Budget still needs an owner decision');
  await page.getByLabel('Plan title', {exact:true}).fill('First action with financial unknown');
  await page.getByRole('button', {name:'Save plan revision', exact:true}).click();
  await expect(page.locator('[data-pm-revision]')).toHaveCount(2);
  const updatedPlans = await (await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/plans`, {headers:{Authorization:'Bearer '+fixture.token}})).json();
  const newRevision = await (await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/plans/${updatedPlans.head.latest_revision_id}`, {headers:{Authorization:'Bearer '+fixture.token}})).json();
  assert.equal(newRevision.plan.document.sections.finance.statements[0].status, 'unresolved');
  assert.equal(newRevision.plan.document.sections.scope.statements[0].input_id, confirmed.input_id);
  const unchanged = await (await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/plans/${plans.revisions[0].revision_id}`, {headers:{Authorization:'Bearer '+fixture.token}})).json();
  assert.deepEqual(unchanged.plan.document, revision.plan.document);
  const submit = async (action, target, params, preconditions={}) => {
    const response = await fetch(`${origin}/api/v1/operations?wait=3`, {method:'POST',
      headers:{Authorization:'Bearer '+fixture.token, 'Content-Type':'application/json', 'Idempotency-Key':crypto.randomUUID()},
      body:JSON.stringify({action,target,params,preconditions})});
    assert.ok([200,202].includes(response.status)); let {operation} = await response.json();
    for (let attempt=0; attempt<100 && ['queued','running','pending'].includes(operation.status); attempt++) {
      await new Promise(resolve=>setTimeout(resolve,100));
      ({operation}=await (await fetch(`${origin}/api/v1/operations/${operation.operation_id}`, {headers:{Authorization:'Bearer '+fixture.token}})).json());
    }
    assert.equal(operation.status,'succeeded',JSON.stringify(operation)); return operation.result;
  };
  const project = await submit('project.create', {}, {name:'Reader experience'});
  const work = await submit('work_item.create', {project_id:project.project_id}, {title:'First useful action'});
  await page.reload();
  // The real service finishes the workspace read after the header is mounted.
  // Wait for that read before opening a menu which a remount would retire.
  await expect(page.locator('.pm-main > .ui-page-header').getByRole('button', {name:'Refresh', exact:true})).toBeEnabled();
  await openPmPanel(page,'ui_workspace_settings');
  await page.getByLabel('Connector project',{exact:true}).selectOption(project.project_id);
  const projectForm=page.locator('[data-pm-action="pm.project.bind"]');
  await projectForm.getByLabel('Why associate this work?',{exact:true}).fill('Track this existing project in the PM workspace');
  // Force a real PM event to start the background refresh before the click.
  // Waiting for a disabled Refresh button before releasing this request would
  // deadlock the fixture. Observe the read gate independently of click completion.
  let releaseCatalog, markCatalogRead;
  const catalogBlocked=new Promise(resolve=>{releaseCatalog=resolve;}),catalogRead=new Promise(resolve=>{markCatalogRead=resolve;});
  const catalogUrl=origin+'/api/v1/projects';
  const blockCatalog=async route=>{markCatalogRead();await catalogBlocked;await route.continue();};
  // Hold every overlapping refresh until this gate is released. A one-shot
  // route lets a later load enable Refresh while the first read is still held.
  await page.route(catalogUrl,blockCatalog);
  let refreshClick;
  try {
    await submit('pm.message.append', {workspace_id:workspaceId,thread_id:threadId},
      {content:'A second client retained this note while the first client was editing the project association.'},
      {expected_version:(await read()).thread.version});
    await bounded(catalogRead,30000,'Project catalog refresh was not requested');
    const refreshButton=page.getByRole('button',{name:'Refresh',exact:true});
    await expect(refreshButton).toBeDisabled();
    refreshClick=refreshButton.click().then(()=>null,error=>error);
    await expect(projectForm.getByRole('button',{name:'Associate project',exact:true})).toBeDisabled();
  }finally{releaseCatalog();await page.unroute(catalogUrl,blockCatalog);}
  const refreshFailure=await refreshClick;
  if(refreshFailure)throw refreshFailure;
  console.log('PM fixture recovered the Refresh click after a deliberately earlier background catalog read.');
  await expect(projectForm.getByLabel('Why associate this work?',{exact:true})).toHaveValue('Track this existing project in the PM workspace');
  await projectForm.getByRole('button',{name:'Associate project',exact:true}).click();
  await page.locator('[data-pm-work] > summary').click();
  await page.getByText('Track this work',{exact:true}).first().click();
  const workForm=page.locator('[data-pm-action="pm.work.bind"]');
  await workForm.getByLabel('Why associate this work?',{exact:true}).fill('Preserve the actual work identity');
  await workForm.getByRole('button',{name:'Track this work',exact:true}).click();
  await expect(page.locator('[data-pm-work] > summary')).toContainText('Tracked here');
  await page.locator('[data-pm-work] > summary').click();
  await page.locator('[data-pm-action="pm.evidence.capture"] button[type="submit"]').click();
  await expect(page.locator('[data-pm-evidence]')).toContainText('Definition of Done has not been assessed.');
  await page.locator('[data-pm-fact] > summary').click();
  await expect(page.locator('[data-pm-fact]')).toContainText('To do');
  const captured=await (await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/work/${work.work_item_id}/capture`, {headers:{Authorization:'Bearer '+fixture.token}})).json();
  assert.equal(captured.capture.work_item_id,work.work_item_id); assert.equal(captured.capture.dod,'unassessed');
  assert.equal(captured.capture.evidence.length,1); assert.equal(captured.capture.evidence[0].source_kind,'work_claim');
  assert.equal(captured.capture.execution_authorized,false);
  await page.keyboard.press('Escape'); await openThreadTab(page,'plan');
  const comparison = page.locator('[data-pm-plan-diff]');
  await expect(comparison).toHaveCount(1);
  await page.locator('[data-pm-plan-comparison] > summary').click();
  await expect(comparison.getByRole('button',{name:'Compare versions',exact:true})).toBeDisabled();
  await comparison.getByLabel('From version',{exact:true}).selectOption(plans.revisions[0].revision_id);
  await comparison.getByLabel('To version',{exact:true}).selectOption(updatedPlans.head.latest_revision_id);
  await comparison.locator('[data-pm-diff-bindings] > summary').click();
  const associations = await (await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/work`, {headers:{Authorization:'Bearer '+fixture.token}})).json();
  const association = associations.bindings.find(binding=>binding.work_item_id===work.work_item_id);
  assert.ok(association);
  await comparison.locator(`[data-pm-diff-binding="${association.binding_id}"]`).check();
  const comparisonResponse = () => page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/plan-comparison') && response.request().method()==='GET');
  const firstComparison = comparisonResponse();
  await comparison.getByRole('button',{name:'Compare versions',exact:true}).click();
  const comparisonReply = await firstComparison;
  assert.equal(comparisonReply.status(),200);
  const fixedComparison = await comparisonReply.json();
  assert.equal(fixedComparison.comparison.from_revision.revision_id,plans.revisions[0].revision_id);
  assert.equal(fixedComparison.comparison.to_revision.revision_id,updatedPlans.head.latest_revision_id);
  assert.deepEqual(fixedComparison.comparison.context.binding_ids,[association.binding_id]);
  assert.equal(fixedComparison.comparison.changes.domains.finance.after.statements[0].status,'unresolved');
  assert.equal(fixedComparison.comparison.scope.prose_scope_impact,'unknown');
  assert.equal(fixedComparison.comparison.impact_candidates[0].binding.work_state,'unknown');
  assert.equal(fixedComparison.comparison.impact_candidates[0].work_update_authorized,false);
  assert.equal(fixedComparison.comparison.execution_authorized,false);
  const comparisonResult = comparison.locator('[data-pm-diff-result]');
  await comparisonResult.getByText('Eight management domains',{exact:true}).click();
  await comparisonResult.getByText('Finance · Changed',{exact:true}).click();
  await expect(comparisonResult).toContainText('Budget still needs an owner decision');
  await comparisonResult.getByText('Title and Markdown',{exact:true}).click();
  await expect(comparisonResult).toContainText('effect on scope has not been classified');
  await comparisonResult.locator('summary').filter({hasText:'Review candidate'}).click();
  await expect(comparisonResult).toContainText('Title and work state were not saved');
  await openThreadBrief(page);
  await page.locator('[data-pm-action="pm.snapshot.create"] button[type="submit"]').click();
  await expect(page.locator('[data-pm-brief-section]')).toHaveCount(5);
  await expect(page.locator('[data-pm-brief-section="delivery"]')).toContainText('quality unassessed');
  const brief=await (await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/snapshots/latest`, {headers:{Authorization:'Bearer '+fixture.token}})).json();
  assert.equal(brief.snapshot.baseline.status,'unavailable');
  assert.equal(brief.snapshot.candidate_document.revision_id,updatedPlans.head.latest_revision_id);
  assert.equal(brief.snapshot.work[0].capture.capture_id,captured.capture.capture_id);
  assert.equal(brief.snapshot.brief.deviations.status,'unassessed');
  const exports = page.locator('[data-pm-brief-export]');
  await exports.locator('summary').click();
  await exports.getByLabel('Report language',{exact:true}).selectOption('en');
  for (const format of ['markdown','html','svg']) {
    await exports.getByLabel('File format',{exact:true}).selectOption(format);
    const exportResponse=page.waitForResponse(response=>new URL(response.url()).pathname.endsWith(`/${brief.snapshot.snapshot_id}/brief-export`));
    const fileResponse=page.waitForEvent('download');
    await exports.getByRole('button',{name:'Download report',exact:true}).click();
    const response=await exportResponse;assert.equal(response.status(),200);
    const report=await response.json(), file=await fileResponse, bytes=await readFile(await file.path());
    assert.equal(report.snapshot_id,brief.snapshot.snapshot_id);assert.equal(report.format,format);
    assert.equal(report.snapshot_digest,brief.snapshot.snapshot_digest);assert.equal(report.view_digest,brief.snapshot.view_digest);
    assert.equal(bytes.toString('utf8'),report.content);assert.equal(bytes.length,report.byte_length);
    assert.equal(Buffer.from(await crypto.subtle.digest('SHA-256',bytes)).toString('hex'),report.output_digest);
    assert.equal(report.review_completed,false);assert.equal(report.execution_authorized,false);assert.equal(report.published,false);
  }
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(comparison.getByLabel('From version',{exact:true})).toHaveValue(plans.revisions[0].revision_id);
  await expect(comparison.getByLabel('To version',{exact:true})).toHaveValue(updatedPlans.head.latest_revision_id);
  await expect(comparison.locator(`[data-pm-diff-binding="${association.binding_id}"]`)).toBeChecked();
  const repeatedComparison = comparisonResponse();
  await comparison.getByRole('button',{name:'Recheck this comparison',exact:true}).click();
  const repeatedResponse = await repeatedComparison;
  assert.equal(repeatedResponse.status(),200);
  assert.equal(new URL(repeatedResponse.url()).searchParams.get('journal_cursor'),String(fixedComparison.comparison.context.journal_cursor));
  assert.deepEqual(await repeatedResponse.json(),fixedComparison);
  await page.reload();
  await expect(page.locator('[data-pm-brief-section]')).toHaveCount(5);
  await openPmPanel(page, 'ui_change_phase');
  const activity=page.locator('[data-pm-lifecycle]');
  await expect(activity.locator('.pm-lifecycle-stage')).toHaveText('Idea');
  // Discussion planning did not change the independent workspace management stage.
  await activity.locator('[data-pm-lifecycle-controls]>summary').click();
  for(const [kind,label,reason,stage] of [
    ['pause','Pause new work','Keep the selected project context without new investment.','Paused'],
    ['close','Close this management workspace','Retain the completed discussion and original work.','Closed'],
    ['reopen','Reopen workspace','Resume selected development without replaying old requests.','Development'],
  ]){
    await activity.locator(`[data-pm-lifecycle-intent="${kind}"]>summary`).click();
    const editor=activity.locator(`[data-pm-action="pm.lifecycle.${kind}"]`);
    if(kind==='reopen')await editor.getByLabel('Management stage',{exact:true}).selectOption('development');
    await editor.getByLabel('Reason and retained context',{exact:true}).fill(reason);
    await editor.getByRole('button',{name:label,exact:true}).click();
    await expect(activity.locator('.pm-lifecycle-stage')).toHaveText(stage);
    if(kind==='pause'){
      await expect(page.locator('[data-pm-action="pm.run.prepare"] button[type=submit]')).toBeDisabled();
      await expect(page.locator('[data-pm-action="pm.message.append"] button[type=submit]')).toBeEnabled();
    }
  }
  const lifecycle=await (await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/lifecycle`,{headers:{Authorization:'Bearer '+fixture.token}})).json();
  assert.equal(lifecycle.lifecycle.stage,'development');assert.equal(lifecycle.lifecycle.execution_epoch,3);
  const history=await (await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/lifecycle/transitions`,{headers:{Authorization:'Bearer '+fixture.token}})).json();
  assert.deepEqual(history.transitions.map(row=>row.to_stage),['paused','closed','development']);
  // Live Overview reports the missing adopted baseline without manufacturing progress.
  await page.goto(origin + '/dashboard/#/pm/' + workspaceId + '/overview');
  await expect(page.locator('[data-pm-charter] [data-charter-section]')).toHaveCount(6);
  await expect(page.locator('[data-overview-metric=progress]')).toContainText('—Unknown');
  await expect(page.locator('[data-overview-metric=progress]')).toContainText('No adopted plan');
  child.stdin.write(JSON.stringify({action:'connect_usage',workspace_id:workspaceId})+'\n');
  assert.equal((await next()).usage_connected,true);
  await page.locator('[data-pm-view="agents"]').click();
  await expect(page.locator('[data-pm-resources]')).toContainText('1,000');
  await page.locator('[data-resource-select="Implementation"]').click();
  await expect(page.locator('[data-resource-detail]')).toContainText('fixture-model');
  await expect(page.locator('[data-resource-detail]')).toContainText('Input includes cache');
  await expect(page.locator('[data-resource-quota]')).toContainText('No current quota balance');
  await page.locator('[data-pm-view="requirements"]').click();
  await expect(page.locator('[data-pm-rtm]')).toContainText('First useful action');
  await expect(page.locator('[data-pm-rtm]')).toContainText('Unassigned / other plan versions');
  child.stdin.write(JSON.stringify({action:'verify'})+'\n'); const result = await next();
  assert.deepEqual(result.actions.sort(), ['pm.workspace.create','pm.thread.create','pm.message.append','pm.thread.rename','pm.input.record','pm.input.record','pm.input.confirm','pm.thread.plan','pm.plan.draft','pm.input.record','pm.thread.plan.revise','pm.plan.draft','project.create','work_item.create','pm.message.append','pm.project.bind','pm.work.bind','pm.evidence.capture','pm.snapshot.create','pm.lifecycle.pause','pm.lifecycle.close','pm.lifecycle.reopen'].sort());
  assert.deepEqual(errors, []);
  console.log('Actual central PM browser flow passed: workspace, Idea, source confirmation, retained candidate, Planning and immutable professional plan draft, explicit work association, fixed plan comparison with unchanged work, evidence, five-section Brief and exact Markdown/HTML/SVG downloads, independent pause/close/reopen, fixed Charter, unassigned RTM work and recurring local usage through the central resource API; zero hosts or tasks.');
} catch(error) {
  console.error('Synthetic PM fixture state:',await failureDetails().catch(()=>({unavailable:true})));
  throw error;
} finally {
  try {await browser?.close();}
  finally {
    try {
      if (child) {
        if (!spawnError && child.exitCode === null && child.signalCode === null) child.stdin.end(JSON.stringify({action:'stop'})+'\n');
        if (!await exitedWithin(3000)) {
          child.kill('SIGTERM');
          if (!await exitedWithin(5000)) child.kill('SIGKILL');
          await bounded(stopped, 10000, 'PM fixture did not exit after termination');
        }
      }
      // Only remove this harness-owned directory after the process is proven stopped.
      await rm(temporary, {recursive:true, force:true});
    } finally {output?.close();}
  }
}
