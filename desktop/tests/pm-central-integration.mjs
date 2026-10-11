// One browser flow against actual PM HTTP actions; the central has no BAT hosts.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {delimiter, resolve} from 'node:path';
import {mkdir, mkdtemp, readFile, rm} from 'node:fs/promises';
import {homedir} from 'node:os';
import assert from 'node:assert/strict';
import {chromium, expect as baseExpect} from '@playwright/test';
const expect = baseExpect.configure({timeout: 30000});
const source = resolve('..'), backend = resolve(process.env.BATC_PM_BACKEND || source);
const python = process.env.BATC_PM_PYTHON || resolve(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const scratch = resolve(homedir(), 'agent-work/tmp/pytest');
await mkdir(scratch, {recursive:true});
const temporary = await mkdtemp(resolve(scratch, 'pm-central-run-'));
let child, browser, output, stopped, spawnError;
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
  browser = await chromium.launch();
  const fixture = await next(), origin = `http://127.0.0.1:${fixture.port}`, page = await browser.newPage({locale: 'en-US'}), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  // Test this checkout's exact generated frontend even when the backend is supplied by an integration worktree.
  for (const file of ['app.js', 'app.css']) await page.route(`**/dashboard/${file}`, route =>
    readFile(resolve(source, 'src/bat_agent_connector/dashboard', file)).then(body => route.fulfill({body, contentType:file.endsWith('css')?'text/css':'text/javascript'})));
  await page.addInitScript(token => sessionStorage.setItem('batc.dashboard.token', token), fixture.token);
  await page.goto(origin + '/dashboard/#/pm');
  await page.getByLabel('Workspace name', {exact:true}).fill('PM contract fixture');
  await page.getByRole('button', {name:'Create workspace', exact:true}).click();
  await expect(page).toHaveURL(/#\/pm\/ws_[a-f0-9]{32}$/);
  await page.getByLabel('Discussion title', {exact:true}).fill('Choose a first improvement');
  await page.getByRole('button', {name:'Start discussion', exact:true}).click();
  await expect(page).toHaveURL(/#\/pm\/ws_[a-f0-9]{32}\/pmt_[a-f0-9]{32}$/);
  const [workspaceId, threadId] = page.url().split('/').slice(-2);
  const read = async () => {
    const response = await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/threads/${threadId}`, {headers:{Authorization:'Bearer '+fixture.token}});
    assert.equal(response.status, 200); return response.json();
  };
  const note = page.locator('[data-pm-action="pm.message.append"]');
  await note.getByLabel('Add a discussion note', {exact:true}).fill('Keep the first action visible; retain a guided tour as an alternative.');
  await note.getByRole('button', {name:'Save note', exact:true}).click();
  await expect(page.locator('.pm-notes')).toContainText('Keep the first action visible');
  const message = (await read()).messages[0];
  assert.equal(typeof message.created_at, 'number');
  await expect(page.locator('.pm-note time')).toHaveAttribute('datetime', new Date(message.created_at * 1000).toISOString());
  await page.locator('[data-pm-add-input] summary').click();
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
  await page.locator(`[data-input-id="${original.input_id}"] summary`).click();
  const confirm = page.locator(`[data-input-id="${original.input_id}"] [data-pm-action="pm.input.confirm"]`);
  await confirm.getByLabel('Why this statement is confirmed', {exact:true}).fill('The owner selected this outcome');
  await confirm.getByRole('button', {name:'Confirm statement', exact:true}).click();
  await expect(page.locator('[data-pm-missing-selection]')).toBeVisible();
  const confirmed = (await read()).inputs.find(item => item.slot === 'problem');
  assert.notEqual(confirmed.input_id, original.input_id); assert.equal(confirmed.supersedes_input_id, original.input_id);
  await page.getByRole('checkbox', {name:'Selected input outside this view', exact:true}).click();
  await page.locator(`[data-input-id="${confirmed.input_id}"] input[type=checkbox]`).check();
  const plan = page.locator('[data-pm-action="pm.thread.plan"]');
  await plan.getByLabel('Why this direction', {exact:true}).fill('Prepare one change and retain the alternative');
  await plan.getByRole('button', {name:'Save Planning draft', exact:true}).click();
  await expect(page.locator('.pm-draft')).toContainText('Offer a guided tour later');
  await page.reload(); await expect(page.locator('.pm-draft')).toContainText('Make the first action visible');
  const data = await read(); assert.equal(data.thread.mode, 'planning'); assert.equal(data.draft.execution_authorized, false);
  assert.equal(data.draft.adopted, false); assert.deepEqual(data.draft.selected_input_ids, [confirmed.input_id]);
  assert.equal(data.draft.candidate_inputs.length, 1); assert.equal(data.messages.length, 1);
  await page.locator('[data-pm-revision-editor] > summary').click();
  await page.getByLabel('Plan title', {exact:true}).fill('First useful action');
  await page.getByRole('button', {name:'Save plan revision', exact:true}).click();
  await expect(page.locator('[data-pm-revision]')).toHaveCount(1);
  await page.locator('[data-pm-revision] > summary').click();
  await expect(page.locator('.pm-plan-document')).toContainText('First useful action');
  await expect(page.locator('[data-pm-action="pm.plan.adopt"] button[type="submit"]')).toBeDisabled();
  const plansResponse = await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/plans`, {headers:{Authorization:'Bearer '+fixture.token}});
  assert.equal(plansResponse.status, 200); const plans = await plansResponse.json();
  assert.equal(plans.revisions.length, 1); assert.equal(plans.head.accepted_revision_id, null);
  const revisionResponse = await fetch(`${origin}/api/v1/pm/workspaces/${workspaceId}/plans/${plans.revisions[0].revision_id}`, {headers:{Authorization:'Bearer '+fixture.token}});
  const revision = await revisionResponse.json();
  assert.deepEqual(revision.plan.document.selected_inputs.map(input=>input.input_id), [confirmed.input_id]);
  assert.equal(revision.plan.adoption, null); assert.equal(revision.plan.document.execution_authorized, false);
  await page.locator('[data-pm-add-input] > summary').click();
  await input.getByLabel('Topic', {exact:true}).selectOption('finance');
  await input.getByLabel('Statement or question', {exact:true}).fill('Budget still needs an owner decision');
  await input.getByRole('button', {name:'Save input', exact:true}).click();
  await expect(page.locator('.pm-facts')).toContainText('Budget still needs an owner decision');
  const later = (await read()).inputs.find(item=>item.slot==='finance');
  await page.locator(`[data-input-id="${later.input_id}"] input[type=checkbox]`).check();
  await page.locator('[data-pm-planning-revision] > summary').click();
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
  const submit = async (action, target, params) => {
    const response = await fetch(`${origin}/api/v1/operations?wait=3`, {method:'POST',
      headers:{Authorization:'Bearer '+fixture.token, 'Content-Type':'application/json', 'Idempotency-Key':crypto.randomUUID()},
      body:JSON.stringify({action,target,params,preconditions:{}})});
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
  await page.getByLabel('Connector project',{exact:true}).selectOption(project.project_id);
  const projectForm=page.locator('[data-pm-action="pm.project.bind"]');
  await projectForm.getByLabel('Why associate this work?',{exact:true}).fill('Track this existing project in the PM workspace');
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
  child.stdin.write(JSON.stringify({action:'verify'})+'\n'); const result = await next();
  assert.deepEqual(result.actions.sort(), ['pm.workspace.create','pm.thread.create','pm.message.append','pm.input.record','pm.input.record','pm.input.confirm','pm.thread.plan','pm.plan.draft','pm.input.record','pm.thread.plan.revise','pm.plan.draft','project.create','work_item.create','pm.project.bind','pm.work.bind','pm.evidence.capture'].sort());
  assert.deepEqual(errors, []);
  console.log('Actual central PM browser flow passed: workspace, Idea, source confirmation, retained candidate, Planning and immutable professional plan draft, explicit work association and fixed evidence; zero hosts or tasks.');
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
