// Owner journey against TaskDaemon HTTP, never a demo API fixture.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {delimiter, resolve} from 'node:path';
import {mkdir, mkdtemp, rm} from 'node:fs/promises';
import {homedir} from 'node:os';
import assert from 'node:assert/strict';
import {performance} from 'node:perf_hooks';
import {chromium, expect as baseExpect} from '@playwright/test';
import {openPmSection, recoverOriginalAdoption} from './pm-navigation.ts';

const expect = baseExpect.configure({timeout: 30000});
const backend = resolve(process.env.BATC_PM_BACKEND || '..');
const python = process.env.BATC_PM_PYTHON || resolve(backend, '.venv/bin/python');
const scratch = resolve(homedir(), 'agent-work/tmp/pytest');
const shots = resolve(process.env.BATC_WALKTHROUGH_SHOTS
  || (process.env.TMPDIR ? resolve(process.env.TMPDIR, 'shots') : 'test-results/walkthrough'));
await mkdir(scratch, {recursive: true}); await mkdir(shots, {recursive: true});

async function bounded(promise, milliseconds, message) {
  let timer;
  try {return await Promise.race([promise, new Promise((_, reject) => {timer = setTimeout(() => reject(Error(message)), milliseconds);})]);}
  finally {clearTimeout(timer);}
}
const labels = {
  en: {send: 'Send', ask: 'Ask PM', retry: 'Check original request', adopted: 'Adopted',
    resolve: 'Resolve issue', resolutionRationale: 'Reason for resolving', resolved: 'Reported resolved', unassessed: 'Not assessed',
    recently: 'Recently decided', resolution: 'The reviewed expiry decision removes the recovery implementation blocker.',
    accepted: 'Open revision', rationale: 'Reason for adopting this update', adopt: 'Adopt',
    progress: '0 of 2 accepted',
    message: 'Agree a 15-minute reset-link expiry; keep sign-in and recovery, and exclude referral rewards.'},
  'zh-TW': {send: '傳送', ask: '詢問 PM', retry: '查回原請求', adopted: '已採用',
    resolve: '回報解決', resolutionRationale: '回報解決的理由', resolved: '回報已解決', unassessed: '未評估',
    recently: '近期決策', resolution: '已審閱的效期決策解除了密碼重設實作的阻礙。',
    accepted: '檢視版本', rationale: '採用這組更新的理由', adopt: '採用',
    progress: '2 項中 0 項已驗收',
    message: '確認重設連結效期為 15 分鐘；保留登入與密碼重設，推薦獎勵不在本輪範圍。'},
};
// The bearer-only service has no managed browser session; this fixture has no
// maintenance observation. These exact optional reads are expected to be 404.
const unexpectedResponse = row => row.status >= 400 && !(row.method === 'GET' && row.status === 404
  && (row.path === '/api/v1/browser-session' || row.path.endsWith('/maintenance-findings/current')));

async function walkthrough(locale, width) {
  const started = performance.now(), temporary = await mkdtemp(resolve(scratch, 'pm-walkthrough-run-'));
  let child, stopped, spawnError, output, browser, page, fixture, step = 'startup';
  const errors = [], failures = [], requests = [], responses = [], operationReplies = new Map(), reads = new Map(), pending = new Set();
  const dropped = new Set();
  let lostAcknowledgements = 0;
  const watch = promise => {pending.add(promise); promise.finally(() => pending.delete(promise)).catch(() => {}); return promise;};
  const screenshot = async (number, name) => {
    step = name;
    const path = resolve(shots, `${locale}-${width}-${String(number).padStart(2, '0')}-${name}.png`);
    await page.screenshot({path, fullPage: true, animations: 'disabled'});
    console.log(JSON.stringify({locale, width, step: name, screenshot: path}));
  };
  try {
    child = spawn(python, [resolve('tests/pm-walkthrough-fixture.py')], {cwd: backend,
      env: {...process.env, BATC_PM_FIXTURE_TEMP: temporary, BATC_WALKTHROUGH_LOCALE: locale,
        PYTHONPATH: [resolve(backend, 'src'), backend].join(delimiter)}, stdio: ['pipe', 'pipe', 'inherit']});
    stopped = new Promise(resolve => {child.once('exit', resolve); child.once('error', error => {spawnError = error; resolve();});});
    child.stdin.on('error', () => {});
    output = createInterface({input: child.stdout});
    const lines = output[Symbol.asyncIterator]();
    const next = async () => {
      const row = await bounded(Promise.race([lines.next(), stopped.then(() => {throw spawnError || Error('Walkthrough central stopped');})]),
        60000, 'Timed out waiting for walkthrough central');
      if (row.done) throw spawnError || Error('Walkthrough central stopped');
      return JSON.parse(row.value);
    };
    fixture = await next();
    const origin = `http://127.0.0.1:${fixture.port}`, copy = fixture.copy, l = labels[locale];
    browser = await chromium.launch();
    const context = await browser.newContext({locale: locale === 'en' ? 'en-US' : locale,
      viewport: {width, height: width === 390 ? 844 : 900}, reducedMotion: 'reduce'});
    page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/v1/operations' && request.method() === 'POST') {
        requests.push({key: request.headers()['idempotency-key'], body: request.postDataJSON()});
      }
    });
    page.on('requestfailed', request => {
      if (new URL(request.url()).pathname.startsWith('/api/v1/')) failures.push({path: new URL(request.url()).pathname,
        action: request.postDataJSON()?.action, error: request.failure()?.errorText});
    });
    page.on('response', response => {
      const path = new URL(response.url()).pathname;
      if (!path.startsWith('/api/v1/')) return;
      responses.push({method: response.request().method(), path, status: response.status()});
      if (path !== '/api/v1/operations' && !path.endsWith('/overview')) return;
      watch((async () => {
        const data = await response.json(); reads.set(path, data);
        if (path === '/api/v1/operations' && response.request().method() === 'POST') {
          operationReplies.set(response.request().headers()['idempotency-key'], data.operation);
        }
      })().catch(error => errors.push(`Response ${path}: ${error.message}`)));
    });
    // Lose each first adoption/resolution acknowledgement after central admission.
    // Each UI recovery click must reuse its original durable key and envelope.
    await page.route(url => url.pathname === '/api/v1/operations', async route => {
      const request = route.request();
      const action = request.method() === 'POST' ? request.postDataJSON().action : null;
      if (!['pm.conversation.adopt', 'pm.governance.revise'].includes(action) || dropped.has(action)) {
        await route.continue(); return;
      }
      dropped.add(action);
      const response = await route.fetch();
      assert.equal(response.status(), 202);
      operationReplies.set(request.headers()['idempotency-key'], (await response.json()).operation);
      lostAcknowledgements++;
      await route.abort('failed');
    });
    await page.addInitScript(token => sessionStorage.setItem('batc.dashboard.token', token), fixture.token);
    step = 'understand';
    await page.goto(`${origin}/dashboard/#/pm/${fixture.workspace_id}/overview`);
    const metric = key => page.locator(`[data-overview-metric="${key}"]`);
    await expect(metric('goal')).toContainText(copy.goal);
    await expect(metric('progress')).toContainText(l.progress);
    await expect(metric('blocker')).toContainText(copy.issue);
    await expect(metric('blocker').locator('.ui-metric-value')).toHaveText('1');
    await expect(metric('decision').locator('.ui-metric-value')).toHaveText('0');
    await expect(page.locator('[data-pm-charter] [data-charter-section]')).toHaveCount(6);
    const attention = page.locator('[data-overview-attention] [data-overview-row]').filter({hasText: copy.issue});
    await expect(attention).toBeVisible();
    await expect(page.locator('.pm-main > .ui-page-header').getByRole('button', {name: /^(Refresh|重新整理)$/})).toBeEnabled();
    await screenshot(1, 'understand');

    step = 'problem';
    await attention.locator('[data-overview-open]').click();
    await expect(page).toHaveURL(/\/issues(?:\?|$)/);
    const record = page.locator('[data-reg-inspector]');
    await expect(record.locator('h2')).toHaveText(copy.issue);
    await expect(record).toContainText(copy.response);
    await expect(record.locator('[data-reg-requirement="member.recovery"]')).toBeVisible();
    await screenshot(2, 'problem');
    await record.locator('[data-reg-discuss]').click();
    await expect(page).toHaveURL(/\/discussions\/new\?/);
    const composer = page.locator('[data-discussion-composer]');
    await expect(composer).toBeFocused();
    await expect(page.locator('[data-discussion-title]')).toHaveText(copy.issue);
    await expect(page.locator('[data-discussion-context="governance"]:visible')).toContainText(copy.issue);
    await expect(page.locator('[data-discussion-context="governance"]:visible')).toContainText(locale === 'en' ? 'Issue ·' : '問題 ·');
    await expect(page.locator('[data-discussion-context="governance"]:visible')).not.toContainText(/pmgov_|pmrev_/);
    assert.ok(!copy.issue.includes(fixture.issue_id));
    await composer.fill(l.message);
    await screenshot(3, 'discussion-draft');
    step = 'draft-reload';
    await page.reload();
    await expect(composer).toHaveValue(l.message);
    await expect(composer).toBeEnabled();
    await expect(page.locator('[data-discussion-title]')).toHaveText(copy.issue);
    await expect(page.locator('[data-discussion-context="governance"]:visible')).toContainText(copy.issue);
    await expect(page.locator('[data-discussion-context="governance"]:visible')).toContainText(locale === 'en' ? 'Issue ·' : '問題 ·');
    assert.equal(requests.length, 0, 'Opening and restoring the draft must create no operations');
    await screenshot(4, 'draft-reload');
    await page.getByRole('button', {name: l.send, exact: true}).click();
    await expect(page).toHaveURL(/\/discussions\/pmt_[a-f0-9]{32}$/);
    await expect(page.locator('.pm-notes')).toContainText(l.message);
    await expect(page.locator('[data-discussion-context="governance"]:visible').first()).toContainText(locale === 'en' ? 'Issue ·' : '問題 ·');
    await expect(composer).toHaveValue('');
    await screenshot(5, 'message-sent');

    step = 'proposal';
    await page.locator('[data-discussion-ask]').click();
    const proposal = page.locator('[data-pm-plan-proposals]');
    await expect(proposal).toContainText(copy.summary);
    if (width < 1200) await page.keyboard.press('Escape');
    const excludedSummary = proposal.locator('[data-proposal-exclusion="referral"]');
    await expect(excludedSummary).toBeVisible();
    await expect(excludedSummary).toContainText(copy.exclusion);
    await proposal.locator('[data-proposal-review]').click();
    await expect(proposal.locator('[data-proposal-candidate]')).toHaveCount(6);
    const exclusion = proposal.locator('[data-proposal-candidate="referral"]').locator('..');
    await expect(exclusion).toContainText(copy.exclusion);
    await expect(exclusion.getByRole('checkbox')).not.toBeChecked();
    await expect(proposal.locator('[data-proposal-candidate="recovery_test"]')).toContainText(copy.recovery_test);
    await exclusion.scrollIntoViewIfNeeded();
    await expect(exclusion).toBeInViewport();
    await screenshot(6, 'proposal');
    await proposal.getByLabel(l.rationale, {exact: true}).fill(l.message);
    await expect(proposal.getByRole('button', {name: l.adopt, exact: true})).toBeEnabled();
    await screenshot(7, 'review');

    step = 'adoption-retry';
    await proposal.getByRole('button', {name: l.adopt, exact: true}).click();
    const retry = proposal.getByRole('button', {name: l.retry, exact: true});
    await expect(retry).toBeVisible();
    await retry.click();
    // Completion is proved by the UI's own receipt/readback, not an API poll.
    const originalReceipt = proposal.getByRole('button', {name: l.retry, exact: true, includeHidden: true});
    const adopted = proposal.locator('[data-proposal-adopted]');
    const savedReceipt = proposal.locator('[data-pm-action="pm.conversation.adopt"] [data-pm-saved]');
    await expect.poll(async () => {
      // Readback may show adoption before the original receipt finishes. Its
      // recovery form can remain in Details after saving; require terminal UI
      // evidence when it remains, rather than assuming DOM removal is success.
      if (await adopted.isVisible() && (!await originalReceipt.count() || await savedReceipt.count())) return true;
      // The child pipeline may outlast automatic bounded checking. An owner can
      // check again in the UI, always reading the acknowledged original ID.
      if (!await originalReceipt.count()) return false;
      await recoverOriginalAdoption(proposal, l.retry);
      return false;
    }, {intervals: [1000, 1500, 2000]}).toBe(true);
    if (await originalReceipt.count()) await expect(savedReceipt).toHaveCount(1);
    await expect(adopted).toContainText(l.adopted);
    await screenshot(8, 'adopted');
    const adoptionRequests = requests.filter(row => row.body.action === 'pm.conversation.adopt');
    assert.equal(adoptionRequests.length, 2);
    assert.deepEqual(adoptionRequests[1], adoptionRequests[0]);
    await expect(proposal.locator('[data-proposal-adopted-revision]')).toHaveText(l.accepted);
    const revisionLink = await proposal.locator('[data-proposal-adopted-revision]').getAttribute('href');
    const revision = new URLSearchParams(revisionLink.split('?')[1]).get('revision');
    assert.match(revision, /^pmrev_[a-f0-9]{32}$/);
    assert.notEqual(revision, fixture.revision_id);
    await expect(page.locator('[data-pm-run-purpose]').filter({hasText: copy.updated})).toContainText(locale === 'en' ? 'Revision' : '版本');
    assert.doesNotMatch(await proposal.innerText(), /\b(?:pmrev|pmgov|op|pmt)_[a-f0-9]{32}\b/);
    await proposal.locator('[data-proposal-adopted-revision]').click();
    await expect(page.locator(`[data-pm-revision="${revision}"] [data-pm-revision-open]`)).toBeFocused();
    if (width < 800) await page.keyboard.press('Escape');

    step = 'resolve-issue';
    await proposal.locator('[data-issue-resolve-open]').click();
    const resolutionForm = proposal.locator('[data-pm-action="pm.governance.revise"]');
    const resolutionReason = resolutionForm.getByLabel(l.resolutionRationale, {exact: true});
    await expect(resolutionReason).toHaveValue(new RegExp(copy.updated));
    assert.doesNotMatch(await resolutionReason.inputValue(), /pmrev_[a-f0-9]{32}/);
    await resolutionReason.fill(l.resolution);
    await resolutionForm.getByRole('button', {name: l.resolve, exact: true}).scrollIntoViewIfNeeded();
    await screenshot(9, 'resolution-review');
    await resolutionForm.getByRole('button', {name: l.resolve, exact: true}).click();
    await resolutionForm.getByRole('button', {name: l.retry, exact: true}).click();
    await expect(proposal.locator('[data-issue-resolved]')).toContainText(l.resolved);
    await expect(proposal.locator('[data-issue-resolved]')).toContainText(l.unassessed);
    await expect(proposal.locator('[data-issue-resolve-open]')).toHaveCount(0);
    const resolutionRequests = requests.filter(row => row.body.action === 'pm.governance.revise');
    assert.equal(resolutionRequests.length, 2);
    assert.deepEqual(resolutionRequests[1], resolutionRequests[0]);
    assert.equal(resolutionRequests[0].body.params.status, 'reported_resolved');
    await screenshot(10, 'issue-resolved');
    await bounded(Promise.all([...pending]), 30000, 'Response diagnostics did not finish');
    assert.deepEqual(errors, []);
    assert.deepEqual(responses.filter(unexpectedResponse), []);
    assert.equal(lostAcknowledgements, 2);
    assert.deepEqual(failures.map(row => row.action), ['pm.conversation.adopt', 'pm.governance.revise']);
    for (const row of requests) assert.match(row.key, /^[A-Za-z0-9._:-]{8,200}$/, 'Every UI operation needs a stable key');
    child.stdin.write(JSON.stringify({action: 'verify', requests: requests.map(row => ({
      key: row.key, action: row.body.action, operation_id: operationReplies.get(row.key)?.operation_id}))}) + '\n');
    assert.equal((await next()).verified, true);
    console.log(JSON.stringify({locale, width, centralSafetyVerified: true, browserOperations: 5, adoptionAttempts: 2, resolutionAttempts: 2}));
    step = 'decision-overview';
    await openPmSection(page, 'overview');
    await expect(page.locator('[data-overview-meta]')).toContainText(copy.updated);
    await expect(page.locator('.pm-main > .ui-page-header').getByRole('button', {name: /^(Refresh|重新整理)$/})).toBeEnabled();
    await expect(metric('goal')).toContainText(copy.goal);
    await expect(metric('blocker').locator('.ui-metric-value')).toHaveText('0');
    await expect(metric('blocker')).not.toContainText(copy.issue);
    await expect(attention).toHaveCount(0);
    await expect(metric('decision').locator('.ui-metric-value')).toHaveText('0');
    const recent = metric('decision').locator('[data-overview-recent-decisions]');
    await expect(recent).toContainText(l.recently);
    const recentDecision = recent.locator(`[data-overview-decided="plan_revision.${revision}"]`);
    await expect(recentDecision).toContainText(copy.updated);
    await expect(recentDecision).toContainText('walkthrough-owner');
    await screenshot(11, 'decision-overview');
    await bounded(Promise.all([...pending]), 30000, 'Response diagnostics did not finish');
    assert.deepEqual(errors, []);
    assert.deepEqual(responses.filter(unexpectedResponse), []);
    assert.equal(reads.get(`/api/v1/pm/workspaces/${fixture.workspace_id}/overview`).plan.revision_id, revision);
    step = 'decision-register';
    await recentDecision.getByRole('button').click();
    await expect(page.locator('[data-reg-inspector]')).toContainText(copy.updated);
    await page.keyboard.press('Escape');
    await page.locator('[data-reg-type="decision"]').click();
    const decision = page.locator(`[data-row-key="plan_revision.${revision}"]`);
    await expect(decision).toContainText(copy.updated);
    await expect(decision).toContainText(l.adopted);
    await decision.click();
    await expect(page.locator('[data-reg-inspector]')).toContainText(copy.updated);
    await screenshot(12, 'decision-register');
    await page.keyboard.press('Escape');
    await page.locator('[data-reg-type="issue"]').click();
    const issue = page.locator(`[data-row-key="governance.${fixture.issue_id}"]`);
    await expect(issue).toContainText(l.resolved);
    await issue.click();
    await expect(page.locator('[data-reg-inspector]')).toContainText(l.resolved);
    await screenshot(13, 'resolved-register');
    await bounded(Promise.all([...pending]), 30000, 'Response diagnostics did not finish');
    assert.deepEqual(errors, []);
    assert.deepEqual(responses.filter(unexpectedResponse), []);
    assert.equal(lostAcknowledgements, 2);
    assert.deepEqual(failures.map(row => row.action), ['pm.conversation.adopt', 'pm.governance.revise']);
    for (const row of requests) assert.match(row.key, /^[A-Za-z0-9._:-]{8,200}$/, 'Every UI operation needs a stable key');
    console.log(JSON.stringify({locale, width, passed: true, operations: 5, adoptionAttempts: 2, resolutionAttempts: 2,
      seconds: Number(((performance.now() - started) / 1000).toFixed(2))}));
  } catch (error) {
    if (page) {
      await page.screenshot({path: resolve(shots, `${locale}-${width}-failure-${step}.png`), fullPage: true, animations: 'disabled'}).catch(() => {});
      console.error('Walkthrough failure:', JSON.stringify({locale, width, step, url: page.url(), errors, failures,
        seconds: Number(((performance.now() - started) / 1000).toFixed(2)),
        requests: requests.map(row => ({key: row.key, action: row.body.action})), responses: responses.slice(-20),
        overview: fixture && reads.get(`/api/v1/pm/workspaces/${fixture.workspace_id}/overview`),
        forms: await page.locator('[data-pm-action]').evaluateAll(forms => forms.map(form => ({
          action: form.dataset.pmAction, status: form.querySelector('[role=status]')?.textContent,
          disabled: form.querySelector('button[type=submit]')?.disabled})))}));
    }
    throw error;
  } finally {
    try {await browser?.close();}
    finally {
      try {
        if (child) {
          if (!spawnError && child.exitCode === null && child.signalCode === null) child.stdin.end(JSON.stringify({action: 'stop'}) + '\n');
          try {await bounded(stopped, 3000, 'Stop timeout');}
          catch {child.kill('SIGTERM'); try {await bounded(stopped, 5000, 'Terminate timeout');}
            catch {child.kill('SIGKILL'); await bounded(stopped, 10000, 'Walkthrough fixture did not exit');}}
        }
        await rm(temporary, {recursive: true, force: true});
      } finally {output?.close();}
    }
  }
}

const failures = [];
for (const [locale, width] of [['en', 1440], ['zh-TW', 390]]) {
  try {await walkthrough(locale, width);} catch (error) {failures.push(error); console.error(`${locale} walkthrough failed:`, error);}
}
if (failures.length) throw new AggregateError(failures, `${failures.length} PM walkthrough(s) failed`);
