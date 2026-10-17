// IPC controls only external BAT evidence. Every user action uses the real UI/API.
import assert from 'node:assert/strict';
import {createInterface} from 'node:readline';
import {resolve} from 'node:path';
import {chromium, expect as baseExpect} from '@playwright/test';

const expect = baseExpect.configure({timeout: 30000});
const lines = createInterface({input: process.stdin})[Symbol.asyncIterator]();
const receive = async () => JSON.parse((await lines.next()).value);
const send = data => process.stdout.write(JSON.stringify(data) + '\n');
const f = await receive(), origin = `http://127.0.0.1:${f.port}`;
const zh = f.locale === 'zh-TW';
const errors = [], operations = [], requests = [], responses = [], pending = new Map();
let browser, page, step = 'start';
const launch = async () => {
  browser = await chromium.launch();
  const context = await browser.newContext({locale: zh ? 'zh-TW' : 'en-US',
    viewport: {width: f.width, height: f.width === 390 ? 844 : 900}, reducedMotion: 'reduce'});
  await context.addInitScript(token => sessionStorage.setItem('batc.dashboard.token', token), f.token);
  page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {if (message.type() === 'error') console.error(message.text());});
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/')) pending.set(request, request.url());
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/operations') {
      requests.push({key: request.headers()['idempotency-key'], body: request.postDataJSON()});
    }
  });
  page.on('response', response => {
    pending.delete(response.request());
    if (new URL(response.url()).pathname.startsWith('/api/')) responses.push({path: new URL(response.url()).pathname, status: response.status()});
    if (response.status() >= 400) response.text().then(body => console.error(response.url(), response.status(), body)).catch(() => {});
  });
  page.on('requestfailed', request => {pending.delete(request); console.error('Request failed', request.url(), request.failure());});
};
const post = action => page.waitForResponse(response => response.request().method() === 'POST'
  && new URL(response.url()).pathname === '/api/v1/operations'
  && response.request().postDataJSON()?.action === action);
const operation = async response => {
  assert.ok([200, 202].includes(response.status()), await response.text());
  const data = await response.json(); operations.push(data.operation.operation_id); return data.operation;
};
const shot = name => page.screenshot({path: resolve(f.shots, name + '.png'), animations: 'disabled'});
try {
  await launch();
  let start;
  if (f.mode === 'adopt') {
    await page.goto(`${origin}/dashboard/?workbench=1#/pm/${f.workspace}/discussions/${f.thread}`);
    const composer = page.locator('[data-discussion-composer]');
    await composer.fill(zh ? '會員用 Email 登入與忘記密碼。第一版不要推薦碼。請整理成計畫。' : 'Plan member email sign-in and password recovery, excluding referrals.');
    const saving = post('pm.message.append');
    await page.getByRole('button', {name: zh ? '只存筆記' : 'Save note only', exact: true}).click();
    await operation(await saving);
    await expect(composer).toHaveValue('');
    const proposing = post('pm.conversation.propose');
    await page.locator('[data-discussion-ask]').click();
    await operation(await proposing);
    await page.locator('[data-proposal-review]').click();
    await page.locator('[data-proposal-adopt-panel] textarea').last().fill(zh ? '採用登入與密碼重設，先開始登入工作。' : 'Adopt sign-in and recovery; start the sign-in work first.');
    const row = page.locator(`[data-adopt-work="${f.work}"]`);
    await row.locator('.pm-adopt-choice > input').first().check();
    await row.getByLabel('付費會員可使用 Email 登入', {exact: true}).check();
    await page.getByRole('button', {name: zh ? '檢查就緒' : 'Check readiness', exact: true}).click();
    const submit = page.getByRole('button', {name: zh ? '採用並開始' : 'Adopt and start', exact: true});
    await expect(submit).toBeEnabled();
    await shot('00-plan-review');
    const starting = post('work.adopt_and_start');
    await submit.click();
    start = await operation(await starting);
    await expect(page).toHaveURL(new RegExp(`/board\\?select=${f.work}`));
    await expect(page.locator('[data-adopt-start-landing]')).toBeVisible();
  } else {
    await page.goto(`${origin}/dashboard/?workbench=1#/pm/${f.workspace}/overview`);
    await page.locator('[data-workbench-management]').selectOption('board');
    await page.locator(`[data-pm-kanban-work="${f.work}"] [data-board-open]`).click();
    const instruction = page.locator('[data-work-start-instruction]');
    await expect(instruction).toBeVisible();
    await instruction.fill(zh ? '修正登入重複送出，補測試，先開 PR。' : 'Fix duplicate login submission, add tests and prepare a PR.');
    await page.locator('[data-work-start-check]').click();
    const submit = page.locator('[data-pm-action="work.start"] button[type="submit"]');
    await expect(submit).toBeEnabled();
    const starting = post('work.start');
    await submit.click();
    start = await operation(await starting);
  }
  await expect(page.locator('[data-work-start-item]')).toBeVisible();
  await shot('01-started');
  send({stage: 'started', operation_id: start.operation_id});
  const active = await receive();
  assert.equal(active.ready, true);
  step = 'queue';
  await page.locator('[data-work-start-item]').click();
  await expect(page.locator('[data-follow-up-input]')).toBeVisible();
  const workUrl = page.url();
  assert.equal(new URL(workUrl).hash.includes(active.attempt), true);
  const text = zh ? '再補一個失敗後重新送出的案例。' : 'Add a retry-after-failure case.';
  await page.locator('[data-follow-up-input]').fill(text);
  const sending = post('session.send');
  await page.locator('[data-work-section="next-turn"]').getByRole('button', {name: zh ? '送出下一步' : 'Send next', exact: true}).click();
  const queued = await operation(await sending);
  await expect(page.locator('[data-follow-up-receipt]')).toContainText(zh ? '排隊中' : 'Queued');
  await shot('02-queued');
  const layout = await page.locator('.workspace-conversation').evaluate(lane => ({
    readingBottom: Math.max(...[...lane.querySelectorAll('.conversation > .actions > *')]
      .map(node => node.getBoundingClientRect().bottom)),
    nextTop: lane.querySelector('[data-work-section="next-turn"]').getBoundingClientRect().top,
    overflow: document.documentElement.scrollWidth - innerWidth,
  }));
  assert.ok(layout.readingBottom <= layout.nextTop, `Reading controls overlap the next step: ${JSON.stringify(layout)}`);
  assert.ok(layout.overflow <= 1, `Page overflows: ${JSON.stringify(layout)}`);
  await browser.close(); browser = null;
  send({stage: 'closed', operation_id: queued.operation_id, instruction: text});
  const fixed = await receive();
  assert.equal(fixed.attempt, active.attempt);
  step = 'reconnect';
  await launch();
  await page.goto(workUrl);
  await expect(page.locator('[data-follow-up-receipt]')).toContainText(text);
  await expect(page.locator('[data-follow-up-receipt]')).not.toContainText(zh ? '排隊中' : 'Queued');
  const draft = zh ? '保留這段尚未送出的草稿。' : 'Keep this unsent draft.';
  await page.locator('[data-follow-up-input]').fill(draft);
  const views = page.locator('[data-workbench-views]');
  for (const name of zh ? ['關聯', '現場', '工作'] : ['Lineage', 'Live', 'Work']) {
    await views.getByRole('tab', {name, exact: true}).click();
    assert.equal(page.url(), workUrl);
  }
  await expect(page.locator('[data-follow-up-input]')).toHaveValue(draft);
  const expectedOperations = f.mode === 'adopt' ? 5 : 3;
  assert.equal(requests.length, expectedOperations - 1, 'Changing views must not dispatch work');
  await page.getByRole('button', {name: zh ? '成果' : 'Results', exact: true}).click();
  await expect(page.locator('[data-work-result]')).toContainText('Added the retry-after-failure case.');
  await expect(page.locator('[data-work-publish-commit]')).toHaveAttribute('title', fixed.candidate);
  await shot('03-reconnected-result');
  step = 'publish';
  await page.locator('[data-work-publish-title]').fill(zh ? '修正登入重複送出' : 'Fix duplicate login submission');
  await page.locator('[data-work-publish-body]').fill(zh ? '修正與重試案例已完成，等待審閱。' : 'Login repair and retry case ready for review.');
  await page.locator('[data-work-publish-draft="true"]').check();
  const publishing = post('github.pr.publish');
  await page.locator('[data-work-publish-submit]').click();
  const published = await operation(await publishing);
  send({stage: 'published', operation_id: published.operation_id});
  assert.equal((await receive()).verified, true);
  await page.reload();
  await expect(page.locator('[data-follow-up-input]')).toHaveValue(draft);
  await page.getByRole('button', {name: zh ? '成果' : 'Results', exact: true}).click();
  await expect(page.locator('[data-work-publish-pr]')).toContainText('21');
  await shot('04-published');
  assert.deepEqual(errors, []);
  assert.equal(requests.length, expectedOperations);
  assert.equal(new Set(requests.map(r => r.key)).size, expectedOperations);
  assert.ok(requests.every(r => r.key));
  const follow = requests.find(row => row.body.action === 'session.send');
  assert.deepEqual(follow.body.target, {workspace_id: f.workspace, work_item_id: f.work});
  assert.deepEqual(follow.body.params, {text, schedule: 'after_current'});
  assert.deepEqual(follow.body.preconditions, {expected_attempt_id: active.attempt});
  assert.equal(requests.at(-1).body.params.candidate_sha, fixed.candidate);
  assert.equal(requests.at(-1).body.params.draft, true);
  send({stage: 'done', operations, browser: browser.version()});
} catch (error) {
  console.error(JSON.stringify({step, url: page?.url(), errors, requests, responses, pending: [...pending.values()]}));
  if (page && !page.isClosed()) {
    console.error(await page.locator('body').innerText());
    await shot('failure').catch(() => {});
  }
  console.error(error);
  process.exitCode = 1;
} finally {
  await browser?.close();
  process.stdin.destroy();
}
