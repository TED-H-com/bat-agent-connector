// Real delivery daemon + fake providers + generated shared frontend browser checks.
// BATC_PLAYWRIGHT_MODULE may name an installation of Playwright outside the repository.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)(process.env.BATC_PLAYWRIGHT_MODULE || 'playwright');
// Serve canonical generated assets over the real delivery daemon fixture. A separate backend worktree
// may be supplied while this portable frontend commit waits for the delivery merge.
const backend = path.resolve(process.env.BATC_DELIVERY_ROOT || '..');
const assets = path.resolve('../src/bat_agent_connector/dashboard');
const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'desktop-delivery-'));
const fixtureFile = path.join(folder, 'fixture.json');
const output = process.env.BATC_BROWSER_OUTPUT || path.join(folder, 'screenshots');
await fs.mkdir(output, {recursive: true});
const {spawn} = await import('node:child_process');
const {once} = await import('node:events');
const python = path.join(backend, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const child = spawn(python, [path.join(backend, 'tests/manual/delivery_dashboard_fixture.py')], {
  cwd: backend, env: {...process.env, BATC_BROWSER_FIXTURE: fixtureFile, PYTHONPATH: backend},
  stdio: ['ignore', 'pipe', 'inherit'],
});
await new Promise((resolve, reject) => {
  const timeout = setTimeout(() => { child.kill(); reject(new Error('Delivery fixture timed out')); }, 30000);
  child.once('error', err => { clearTimeout(timeout); reject(err); });
  child.once('exit', code => { clearTimeout(timeout); reject(new Error(`Delivery fixture exited: ${code}`)); });
  child.stdout.on('data', data => { if (data.toString().includes('fixture ready')) { clearTimeout(timeout); resolve(); } });
});
const fixture = JSON.parse(await fs.readFile(fixtureFile, 'utf8'));
const browser = await chromium.launch({headless: true});
async function generatedUI(context) {
  await context.route('**/dashboard/**', async route => {
    const name = new URL(route.request().url()).pathname.split('/').at(-1) || 'index.html';
    if (!['index.html', 'app.js', 'app.css', 'i18n.js'].includes(name)) return route.abort();
    return route.fulfill({path: path.join(assets, name), contentType: name.endsWith('.html') ? 'text/html' : name.endsWith('.css') ? 'text/css' : 'text/javascript'});
  });
}
const matrix = [];
const label = (lang, en, zh) => lang === 'en' ? en : zh;
const control = async name => (await fetch(fixture.control + '/' + name)).json();
const waitFor = async (fn, message) => {
  for (let i = 0; i < 100; i++) { if (await fn()) return; await new Promise(resolve => setTimeout(resolve, 100)); }
  throw new Error(message);
};
async function capture(page, filename) {
  try { await page.screenshot({ path: filename, fullPage: true }); }
  catch (err) {
    if (!err.message.includes('Unable to capture screenshot')) throw err;
    await page.waitForTimeout(500);
    await page.screenshot({ path: filename, fullPage: true });
  }
}
async function clean(page) {
  const facts = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth, text: document.body.innerText }));
  assert.ok(facts.scroll <= facts.width, `horizontal overflow: ${facts.scroll}/${facts.width}`);
  assert.doesNotMatch(facts.text, /\b(?:null|undefined)\b|\[object/);
  assert.doesNotMatch(facts.text, /dep_[A-Za-z_]+/); // translated labels, not their keys
}
try {
  for (const lang of ['en', 'zh-TW']) for (const width of [390, 768, 1440]) {
    await control('reset');
    const context = await browser.newContext({ viewport: { width, height: 900 }, locale: lang === 'en' ? 'en-US' : lang });
    await generatedUI(context);
    await context.addInitScript(token => sessionStorage.setItem('batc.dashboard.token', token), fixture.token);
    const page = await context.newPage();
    const errors = [], requests = [], historyRequests = [];
    let losingReply = false;
    page.on('pageerror', err => errors.push(err.message));
    page.on('console', msg => {
      // This operator-run fixture predates optional browser-cookie handoff and bootstrap.
      const optionalRead = ['/api/v1/bootstrap', '/api/v1/browser-session'].some(path => msg.location().url.endsWith(path));
      if (msg.type() === 'error' && !(optionalRead && msg.text().includes('404')) && !(losingReply && msg.text().includes('net::ERR_FAILED'))) errors.push(`${msg.text()} (${msg.location().url})`);
    });
    page.on('request', req => {
      if (req.method() === 'POST' && req.url().includes('/api/v1/operations?')) requests.push({ body: req.postDataJSON(), key: req.headers()['idempotency-key'] });
      if (req.url().includes('/deployment-environments/history?')) historyRequests.push(new URL(req.url()));
    });
    await page.goto(fixture.url + '#/delivery');
    const card = page.locator('[data-environment="production"]');
    await card.locator('[data-testid="deployment-history"] > [data-deployment]').first().waitFor();
    assert.equal(await page.locator('.environment-card').count(), 2);
    await clean(page);
    await capture(page, path.join(output, `${lang}-${width}.png`));
    // dashboard_environment_versions: desired != observed, drift, verified identity, superseded receipts.
    assert.match(await card.locator('.deployment-versions').innerText(), /9{40}/);
    assert.match(await card.locator('.deployment-versions').innerText(), /1{40}/);
    assert.match(await card.innerText(), new RegExp(label(lang, 'Needs attention', '需要處理')));
    assert.match(await card.innerText(), /database migrations/);
    assert.equal(await card.getByRole('link', { name: label(lang, 'View new selection', '查看新的選定部署'), exact: true }).count(), 1);
    assert.equal(await card.getByRole('link', { name: label(lang, 'Original provider run', '原始執行紀錄'), exact: true }).count(), 1);
    if (lang === 'zh-TW') {
      assert.match(await card.innerText(), /環境第 2 代/);
      assert.doesNotMatch(await card.innerText(), /generation|Provider|provider run|runtime|Artifact/);
    }
    const verified = card.locator('.deployment-last-verified');
    assert.match(await verified.innerText(), /9{40}/);
    // dashboard_history_cursor: five rows per page, an API cursor, no full-history fetch.
    const history = card.locator('[data-testid="deployment-history"]');
    // A manual PR load must render while another card's receipt stays open.
    const heldReceipt = history.locator('details').first();
    await heldReceipt.locator(':scope > summary').click();
    await page.getByPlaceholder('123', { exact: true }).fill('7');
    await page.getByRole('button', { name: label(lang, 'Load PR', '讀取 PR'), exact: true }).click();
    await page.locator('.delivery-card:not(.environment-card) h2 a').first().waitFor();
    assert.equal(await heldReceipt.evaluate(el => el.open), true);
    await heldReceipt.locator(':scope > summary').click();
    assert.equal(await history.locator(':scope > [data-deployment]').count(), 5);
    const first = await history.locator(':scope > [data-deployment]').first().getAttribute('data-deployment');
    await card.getByTestId('history-next').click();
    await waitFor(async () => await history.locator(':scope > [data-deployment]').first().getAttribute('data-deployment') !== first, 'next page did not load');
    assert.equal(await history.locator(':scope > [data-deployment]').count(), 5);
    await clean(page);
    assert.ok(historyRequests.some(url => url.searchParams.get('cursor')));
    assert.ok(historyRequests.every(url => url.searchParams.get('limit') === '5'));
    assert.match(await history.innerText(), new RegExp(label(lang, 'is unavailable', '無法取得')));
    await card.getByTestId('history-previous').click();
    await waitFor(async () => await history.locator(':scope > [data-deployment]').first().getAttribute('data-deployment') === first, 'previous page did not load');
    // dashboard_rollback_readiness: unverified, expired, unavailable and unsupported records.
    assert.match(await history.innerText(), new RegExp(label(lang, 'unverified', '未經驗證')));
    assert.match(await history.innerText(), new RegExp(label(lang, 'has expired', '已過期')));
    const unsupported = page.locator('[data-environment="preview"]');
    assert.match(await unsupported.innerText(), new RegExp(label(lang, 'does not support rollback', '不支援回退')));
    assert.equal(await unsupported.getByTestId('deployment-rollback').count(), 0);
    // dashboard_confirmation_polling: fixed selection and open confirmation survive a real journal event delivered by polling.
    const rollback = history.getByTestId('deployment-rollback').first();
    await rollback.click();
    const drawer = card.locator('.drawer:not([hidden])');
    await drawer.getByTestId('deployment-preview-generation').waitFor();
    const selected = await drawer.locator('.deployment-identity').innerText();
    await control('event');
    await page.waitForTimeout(900);
    assert.equal(await drawer.count(), 1);
    assert.equal(await drawer.locator('.deployment-identity').innerText(), selected);
    await clean(page);
    await capture(page, path.join(output, `${lang}-${width}-confirm.png`));
    // dashboard_stale_preview: one refused intent, fresh preview, no automatic resubmit.
    const previousGeneration = await drawer.getByTestId('deployment-preview-generation').innerText();
    await control('bump');
    const initialRequests = requests.length;
    await drawer.getByTestId('deployment-rollback-confirm').click();
    await waitFor(async () => (await drawer.innerText()).includes(label(lang, 'Review the fresh preview', '請檢視下方最新預覽')), 'stale preview refusal not shown');
    await waitFor(async () => await drawer.getByTestId('deployment-preview-generation').innerText() !== previousGeneration, 'fresh preview not shown');
    await page.waitForTimeout(600);
    assert.equal(requests.length, initialRequests + 1);
    // dashboard_double_click: the new preview needs a new click; synchronous double-click sends one operation.
    const baseline = (await control('count')).operations;
    const submitCount = requests.length;
    await drawer.getByTestId('deployment-rollback-confirm').evaluate(el => { el.click(); el.click(); });
    await waitFor(async () => (await control('count')).operations === baseline + 1, 'new rollback not admitted');
    await page.waitForTimeout(600);
    assert.equal((await control('count')).operations, baseline + 1);
    assert.equal(requests.length, submitCount + 1);
    const req = requests.at(-1);
    assert.equal(req.body.action, 'deployment.rollback');
    assert.ok(req.body.params.deployment_id);
    assert.ok(req.body.preconditions.expected_recipe_digest);
    assert.equal(typeof req.body.preconditions.expected_environment_generation, 'number');
    assert.ok(req.key);
    // The accepted response is asynchronous. Until it renders, the drawer can still
    // contain the previous refused intent's receipt; wait for this operation first.
    const acceptedOperation = drawer.locator('[aria-live] > p > a[href^="#/op/"]');
    await acceptedOperation.waitFor({state: 'visible'});
    const operationId = (await acceptedOperation.getAttribute('href')).split('/').at(-1);
    const receipt = drawer.locator('details').last();
    await receipt.locator(':scope > summary').click();
    await waitFor(async () => (await receipt.innerText()).includes(operationId), 'accepted operation receipt missing');
    await clean(page);
    await drawer.getByRole('button', { name: label(lang, 'Close', '關閉'), exact: true }).click();
    // dashboard_retry_fixed_identity: deploy-only intent, with saved identity and preview preconditions.
    await control('reset');
    await page.reload();
    await card.getByTestId('deployment-retry').first().waitFor();
    await card.getByTestId('deployment-retry').first().click();
    const retryDrawer = card.locator('.drawer:not([hidden])');
    await retryDrawer.getByTestId('deployment-preview-generation').waitFor();
    await retryDrawer.getByTestId('deployment-retry-confirm').click();
    await waitFor(async () => requests.at(-1)?.body.action === 'deployment.start', 'retry did not start deploy');
    assert.equal(requests.at(-1).body.params.source_sha, '9'.repeat(40));
    assert.ok(requests.at(-1).body.params.retry_of);
    assert.ok(requests.at(-1).body.preconditions.expected_recipe_digest);
    await clean(page);
    // Only absolute HTTPS provider URLs become links; malformed/relative/active schemes are omitted.
    await control('reset');
    const providerPattern = '**/api/v1/deployment-environments/history?*';
    await page.route(providerPattern, async route => {
      const response = await route.fetch();
      const body = await response.json();
      if (new URL(route.request().url()).searchParams.get('recipe') === 'prod') {
        const template = body.items.find(dep => dep.state === 'superseded');
        body.items = ['javascript:alert(1)', 'data:text/html,bad', 'http://github.example/run',
          '/relative/run', '//github.example/run', 'https://', 'HTTPS://github.example/o/r/actions/runs/1001']
          .map((provider_url, n) => ({ ...template, deployment_id: `provider-fixture-${n}`, provider_url }));
      }
      await route.fulfill({ response, json: body });
    });
    await page.reload();
    await card.locator('[data-deployment="provider-fixture-6"]').waitFor();
    const providerLinks = card.getByRole('link', { name: label(lang, 'Original provider run', '原始執行紀錄'), exact: true });
    assert.equal(await providerLinks.count(), 1);
    assert.equal(await providerLinks.first().getAttribute('href'), 'https://github.example/o/r/actions/runs/1001');
    await page.unroute(providerPattern);
    // Lost acknowledgement before execution: close, live refresh and reload keep the exact intent's key.
    for (const kind of ['rollback', 'retry']) {
      await control('reset');
      await control('pause-operations');
      await page.reload();
      // Fixture reset rewinds generations; unrelated earlier scenarios must not share their draft storage.
      await page.evaluate(() => {
        for (const key of Object.keys(localStorage)) if (key.startsWith('batc.key.')) localStorage.removeItem(key);
      });
      const openIntent = async () => {
        await card.getByTestId(`deployment-${kind}`).first().click();
        const intent = card.locator('.drawer:not([hidden])');
        await intent.getByTestId('deployment-preview-generation').waitFor();
        return intent;
      };
      let intent = await openIntent();
      const beforeCount = (await control('count')).browser_operations;
      const beforeRequests = requests.length;
      let originalOperation;
      losingReply = true;
      await page.route('**/api/v1/operations?*', async route => {
        const response = await route.fetch();
        const accepted = (await response.json()).operation;
        assert.equal(accepted.status, 'accepted');
        originalOperation = accepted.operation_id;
        await route.abort('failed');
      }, { times: 1 });
      await intent.getByTestId(`deployment-${kind}-confirm`).click();
      await waitFor(async () => (await intent.innerText()).includes(label(lang,
        'The deployment request could not proceed', '部署請求未能繼續')), 'lost reply was not shown');
      losingReply = false;
      assert.equal((await control('count')).browser_operations, beforeCount + 1);
      await intent.getByRole('button', { name: label(lang, 'Close', '關閉'), exact: true }).click();
      await control('event');
      await page.waitForTimeout(900);
      await page.reload();
      intent = await openIntent();
      const replay = page.waitForResponse(response => response.request().method() === 'POST'
        && response.url().includes('/api/v1/operations?'));
      await intent.getByTestId(`deployment-${kind}-confirm`).click();
      assert.equal((await (await replay).json()).operation.operation_id, originalOperation);
      assert.equal((await control('count')).browser_operations, beforeCount + 1);
      assert.equal(requests.length, beforeRequests + 2);
      assert.deepEqual(requests.at(-1), requests.at(-2));
      await intent.getByRole('button', { name: label(lang, 'Close', '關閉'), exact: true }).click();
    }
    assert.deepEqual(errors, []);
    await context.close();
    // dashboard_scope_disabled: observe-only principal sees the same history with actionable reasons.
    await control('reset');
    const viewer = await browser.newContext({ viewport: { width, height: 900 }, locale: lang === 'en' ? 'en-US' : lang });
    await generatedUI(viewer);
    await viewer.addInitScript(token => sessionStorage.setItem('batc.dashboard.token', token), fixture.viewer);
    const readonly = await viewer.newPage();
    readonly.on('pageerror', err => errors.push(err.message));
    readonly.on('console', msg => {
      const optionalRead = ['/api/v1/bootstrap', '/api/v1/browser-session'].some(path => msg.location().url.endsWith(path));
      if (msg.type() === 'error' && !(optionalRead && msg.text().includes('404'))) errors.push(`${msg.text()} (${msg.location().url})`);
    });
    await readonly.goto(fixture.url + '#/delivery');
    await readonly.getByTestId('deployment-rollback').first().waitFor();
    assert.ok(await readonly.getByTestId('deployment-rollback').first().isDisabled());
    assert.ok(await readonly.getByTestId('deployment-retry').first().isDisabled());
    assert.match(await readonly.locator('[data-environment="production"]').innerText(), new RegExp(label(lang, 'Needs Deploy access', '需要部署權限')));
    await clean(readonly);
    assert.deepEqual(errors, []);
    await viewer.close();
    matrix.push({ language: lang, width, result: 'passed', cases: [
      'dashboard_environment_versions', 'dashboard_history_cursor', 'dashboard_rollback_readiness',
      'dashboard_confirmation_polling', 'dashboard_stale_preview', 'dashboard_double_click',
      'dashboard_retry_fixed_identity', 'dashboard_scope_disabled', 'dashboard_dom_console_overflow',
      'dashboard_load_pr_with_history_open', 'dashboard_https_provider_links', 'dashboard_lost_reply_reopen',
    ] });
    console.log(`${lang} ${width}: passed`);
  }
  await fs.writeFile(path.join(output, 'matrix.json'), JSON.stringify(matrix, null, 2));
  console.log(`Generated delivery UI matrix and screenshots: ${output}`);
} finally {
  await browser.close();
  child.kill('SIGTERM');
  await once(child, 'exit');
}
