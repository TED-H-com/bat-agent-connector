import {httpEventDelivery} from './event-delivery.mjs';
// User actions use the real UI/API; IPC also coordinates the independent MCP client.
import assert from 'node:assert/strict';
import {createInterface} from 'node:readline';
import {resolve} from 'node:path';
import {readFileSync, writeFileSync} from 'node:fs';
import {chromium, expect as baseExpect} from '@playwright/test';
import {uiStrings} from '../src/ui/ui-strings.js';
import {workbenchStrings} from '../src/workbench-strings.js';

const expect = baseExpect.configure({timeout: 30000});
const lines = createInterface({input: process.stdin})[Symbol.asyncIterator]();
const receive = async () => JSON.parse((await lines.next()).value);
const send = data => process.stdout.write(JSON.stringify(data) + '\n');
const f = await receive(), origin = `http://127.0.0.1:${f.port}`;
const zh = f.locale === 'zh-TW';
const errors = [], operations = [], requests = [], responses = [], pending = new Map(), results = new Map();
const resumedOperations = [];
const allRequests = [], requestRecords = new Map();
let browser, context, page, eventWrites, step = 'start', closeRestart = null;
let observedBootstrap;
const launch = async () => {
  const options = {locale: zh ? 'zh-TW' : 'en-US',
    viewport: {width: f.width, height: f.width === 390 ? 844 : 900}, reducedMotion: 'reduce'};
  if (f.browser_profile) {
    // Closing the app keeps its origin's storage on disk, as an ordinary
    // phone/browser close does. Do not copy or reconstruct application state.
    context = await chromium.launchPersistentContext(f.browser_profile, options);
    browser = context.browser();
  } else {
    browser = await chromium.launch();
    context = await browser.newContext(options);
  }
  await context.addInitScript(token => sessionStorage.setItem('batc.dashboard.token', token), f.token);
  context.on('request', request => {
    const row = {id: allRequests.length + 1, start: Date.now(), step, method: request.method(), url: request.url(),
      key: request.headers()['idempotency-key'], ...(request.postData() === null ? {} : {body: request.postData()})};
    allRequests.push(row); requestRecords.set(request, row);
  });
  context.on('response', response => {requestRecords.get(response.request()).status = response.status();});
  context.on('requestfinished', request => Object.assign(requestRecords.get(request), {end: Date.now(), end_step: step}));
  context.on('requestfailed', request => Object.assign(requestRecords.get(request), {
    end: Date.now(), end_step: step, failure: request.failure()?.errorText}));
  page = context.pages()[0] || await context.newPage();
  eventWrites = await httpEventDelivery(page);
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {if (message.type() === 'error') console.error(message.text());});
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/')) pending.set(request, request.url());
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/operations') {
      requests.push({key: request.headers()['idempotency-key'], body: request.postDataJSON()});
    }
    const resumed = new URL(request.url()).pathname.match(/^\/api\/v1\/operations\/(op_[0-9a-f]{32})\/resume$/);
    if (request.method() === 'POST' && resumed) resumedOperations.push(resumed[1]);
  });
  page.on('response', response => {
    pending.delete(response.request());
    if (new URL(response.url()).pathname.startsWith('/api/')) responses.push({path: new URL(response.url()).pathname, status: response.status()});
    if (response.ok() && new URL(response.url()).pathname === '/api/v1/bootstrap')
      response.json().then(data => {observedBootstrap = data;}).catch(() => {});
    if (response.ok() && /\/work-attempts\/[^/]+\/result$/.test(new URL(response.url()).pathname)) {
      response.json().then(result => results.set(result.attempt_id, result)).catch(() => {});
    }
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
const clickOperation = async (action, button) => {
  // Keep a sequence of normal writes quiet; stage handoffs below explicitly
  // release real event delivery when their server-side changes must be read.
  const [response] = await Promise.all([post(action), eventWrites.post(button, {resume:false})]);
  return operation(response);
};
const shot = name => page.screenshot({path: resolve(f.shots, name + '.png'), animations: 'disabled'});
const deferred = () => {
  let release;
  const promise = new Promise(resolve => {release = resolve;});
  return {promise, release};
};
async function refusedAccess(active, workUrl) {
  // The principal, revocation and all HTTP bodies are real. Only BAT/model
  // are doubles; this journey does not probe a separate Fleet credential.
  const copy = uiStrings[zh ? 'zh-TW' : 'en'];
  // Cross-check the real reply's contract; this golden never answers a request.
  const golden = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));
  await expect.poll(() => Boolean(observedBootstrap)).toBe(true);
  for (const key of ['api_version', 'contract_version'])
    assert.equal(observedBootstrap.capabilities[key], golden.capabilities[key]);
  assert.deepEqual(observedBootstrap.capabilities.features.pm_workspaces.work_follow_up.routes,
    golden.capabilities.features.pm_workspaces.work_follow_up.routes);
  const surface = f.refusal_surface || 'work';
  let field = page.locator('[data-follow-up-input]');
  let work = page.locator('[data-work-section="next-turn"]');
  let submit = work.getByRole('button', {name: zh ? '送出下一步' : 'Send next', exact: true});
  const draft = '  尚未送出 📝\nKeep this instruction unsent.\n  ';
  await field.fill(draft);
  await expect(submit).toBeEnabled();
  await page.getByRole('button', {name: zh ? '成果' : 'Results', exact: true}).click();
  const resultHref = await page.locator('[data-work-result-focus="live-output"]').getAttribute('href');
  assert.ok(resultHref.includes(`attempt=${active.attempt}`));
  await page.keyboard.press('Escape');
  const sessionRoute = resultHref.split('?')[0];
  const [, , host, sid] = sessionRoute.split('/');
  let target = {workspace_id: f.workspace, work_item_id: f.work};
  let suffix = `.session.send.${JSON.stringify(target)}`;
  if (surface !== 'work') step = 'refusal-setup-' + surface;
  if (surface === 'pm-form') {
    target = {workspace_id: f.workspace, thread_id: f.thread};
    suffix = `.pm.message.append.${JSON.stringify(target)}`;
    await page.evaluate(hash => {location.hash = hash;}, `#/pm/${f.workspace}/discussions/${f.thread}`);
    work = page.locator('form[data-pm-action="pm.message.append"]');
    field = work.locator('[data-discussion-composer]');
    submit = work.locator('button[type=submit]');
  } else if (surface === 'session-toolbar') {
    // The Result supplies this managed session's existing route. A plain session
    // route has no Work selection; Classic exposes its original Send/Interrupt toolbar.
    await page.goto(`${origin}/dashboard/${sessionRoute}`);
    target = {host, session_id: sid};
    suffix = `.send.${host}.${sid}`;
    await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
    work = page.locator('.workspace-composer');
    field = page.locator('#workspace-message-input > textarea');
    submit = page.locator('#workspace-message-input .composer-row button.primary');
    const queue = work.locator('input[type=checkbox]');
    await expect(field).toBeVisible();
    await queue.setChecked(!await queue.isChecked());
    await expect(page.locator('.workspace-session-heading button.danger')).toBeEnabled();
  }
  await field.fill(draft);
  await expect(submit).toBeEnabled();
  const surfaceUrl = page.url();
  const originalField = await field.elementHandle();
  const draftEntries = () => page.evaluate(({suffix, surface}) => Object.entries(localStorage)
    .filter(([key]) => surface === 'work' ? key.includes('.session.send.')
      : key.endsWith(suffix) || key.endsWith(suffix + '.queue')).sort(([a], [b]) => a.localeCompare(b)), {suffix, surface});
  const saved = await draftEntries();
  assert.equal(saved.length, surface === 'session-toolbar' ? 2 : 1);
  const [draftKey, raw] = saved.find(([key]) => key.endsWith(suffix));
  assert.ok(draftKey.endsWith(suffix));
  if (surface === 'session-toolbar') assert.deepEqual(Buffer.from(raw), Buffer.from(draft));
  else {
    assert.deepEqual(Buffer.from(JSON.parse(raw).values[surface === 'pm-form' ? 'content' : 'text']), Buffer.from(draft));
    assert.equal(Object.hasOwn(JSON.parse(raw), 'intent'), false);
  }
  const originalContext = context, originalPage = page;
  const since = allRequests.length;
  const refusedResults = [];
  const resultPath = `/api/v1/pm/workspaces/${f.workspace}/work-attempts/${active.attempt}/result`;
  const observeResultRefusal = async response => {
    if (new URL(response.url()).pathname === resultPath && response.status() === 401)
      refusedResults.push({path: resultPath, status: response.status(), body: await response.json()});
  };
  context.on('response', observeResultRefusal);
  const unchanged = async () => {
    assert.equal(context, originalContext); assert.equal(page, originalPage);
    assert.deepEqual(await draftEntries(), saved);
    assert.deepEqual(allRequests.slice(since).filter(row => row.method === 'POST'
      && new URL(row.url).pathname.startsWith('/api/v1/operations')), []);
    assert.equal(requests.length, 1, 'Only the initial Work start may submit an operation');
    for (const row of allRequests.slice(since).filter(row => row.method === 'GET')) {
      const url = new URL(row.url);
      if (url.pathname.endsWith('/inputs')) assert.equal(url.searchParams.get('attempt_id'), active.attempt);
      if (/\/work-attempts\//.test(url.pathname)) assert.ok(url.pathname.includes('/' + active.attempt));
    }
    for (const href of await page.locator('#main a[href*="attempt="]').evaluateAll(nodes => nodes.map(node => node.getAttribute('href'))))
      assert.equal(new URLSearchParams(href.split('?')[1]).get('attempt'), active.attempt);
    await expect(page.locator('[data-answer-action]:enabled')).toHaveCount(0);
  };
  const header = page.locator('#live');
  const scoped = copy.ui_connector_status.replace('{status}', copy.ui_connection_refused);
  const headerRefused = async () => {
    await expect(header).toHaveText(copy.ui_connection_refused);
    await expect(header).toHaveAccessibleName(scoped);
    await expect(header).toHaveAttribute('title', `${scoped} — ${copy.error_code_CLIENT_ACTION_REFUSED}`);
    await expect(header).toHaveAttribute('data-connection', 'reconnecting');
    await expect(page.locator('.central-service-scope')).toHaveText(copy.ui_connector_scope);
  };
  step = 'revoke-access';
  await expect(header).toHaveText(copy.ui_connected);
  // A checkpoint is committed only after its listener refreshes settle. It
  // establishes the retained-source case without assuming Connected means idle.
  send({stage: 'refusal-checkpoint'});
  const {cursor} = await receive();
  await expect.poll(() => page.evaluate(cursor => Object.entries(localStorage)
    .some(([key, value]) => key.startsWith('batc.sync.') && JSON.parse(value).cursor >= cursor), cursor)).toBe(true);
  let refreshRace = null, debug;
  if (f.late_refusal_refresh) {
    // Pause JavaScript, not HTTP: a real inventory event starts a source refresh,
    // and the debugger stops its read before fetch sends it. ApiV1 alone
    // authenticates and produces every reply after the real principal is revoked.
    debug = await context.newCDPSession(page);
    await debug.send('Debugger.enable');
    const sourcePath = surface === 'work' ? resultPath : surface === 'pm-form'
      ? `/api/v1/pm/workspaces/${f.workspace}/threads/${f.thread}` : `/api/v1/sessions/${host}/${sid}`;
    const caller = surface === 'work' ? 'setAttempt' : surface === 'pm-form' ? 'refreshDiscussionSources' : 'loadObservation';
    let paused = null;
    const eventPages = [];
    const observePage = async response => {
      if (new URL(response.url()).pathname === '/api/v1/events' && response.status() === 200)
        eventPages.push(await response.json());
    };
    context.on('response', observePage);
    debug.on('Debugger.paused', async event => {
      if (event.callFrames.some(frame => frame.functionName === caller)) paused = event;
      else await debug.send('Debugger.resume');
    });
    await debug.send('DOMDebugger.setXHRBreakpoint', {url: sourcePath});
    step = 'refusal-refresh';
    send({stage: 'refusal-refresh'});
    const {event} = await receive();
    await expect.poll(() => Boolean(paused)).toBe(true);
    assert.ok(eventPages.some(page => page.events.some(row => row.seq === event.seq)));
    refreshRace = {event, paused_at: Date.now(), frames: paused.callFrames.map(frame => ({
      name: frame.functionName, location: frame.location})), source_path: sourcePath};
    context.off('response', observePage);
  }
  // The first refused read pauses the bearer. A source refresh can be that
  // read, so a subsequent event poll must not be required to reach the server.
  const refusalPath = refreshRace?.source_path || '/api/v1/events';
  const refusal = page.waitForResponse(response => new URL(response.url()).pathname === refusalPath
    && response.status() === 401);
  send({stage: 'revoke-access'});
  assert.equal((await receive()).revoked, true);
  if (debug) {
    const refusedSource = page.waitForResponse(response => new URL(response.url()).pathname === refreshRace.source_path
      && response.status() === 401);
    refreshRace.revoked_at = Date.now();
    await debug.send('DOMDebugger.removeXHRBreakpoint', {url: refreshRace.source_path});
    await debug.send('Debugger.resume');
    await debug.detach();
    const source = await refusedSource;
    refreshRace.source = {path: new URL(source.url()).pathname, status: source.status(), body: await source.json()};
    assert.equal(refreshRace.source.body.error.code, 'UNAUTHORIZED');
    writeFileSync(resolve(f.shots, 'refusal-refresh.json'), JSON.stringify(refreshRace, null, 2) + '\n');
  }
  const response = await refusal;
  const body = await response.json();
  assert.equal(body.error.code, 'UNAUTHORIZED');
  step = 'refused-' + surface;
  await headerRefused();
  await expect(submit).toBeDisabled();
  await expect(field).toHaveValue(draft);
  assert.equal(await field.evaluate((node, old) => node === old, originalField), true);
  assert.equal(page.url(), surfaceUrl);
  let resultOutcome = null;
  if (surface === 'session-toolbar') {
    const reason = page.locator('.session-send-reason');
    await expect(reason).toHaveText(copy.error_code_CLIENT_ACTION_REFUSED);
    await expect(reason).toHaveAttribute('data-client-action-code', 'CLIENT_ACTION_REFUSED');
    await expect(page.locator('.workspace-session-heading button.danger')).toBeDisabled();
  } else {
    if (surface === 'pm-form') {
      const reason = work.locator('[data-pm-client-gate]');
      await expect(reason).toHaveAttribute('data-client-action-code', 'CLIENT_ACTION_REFUSED');
      await expect(reason).toHaveText(copy.error_code_CLIENT_ACTION_REFUSED);
      await expect(reason.locator('[data-api-error], [data-error-summary], [aria-live]')).toHaveCount(0);
      // A submit queued before the disabled state must explain the same gate,
      // without generating an intent, a key or an operation POST.
      await work.evaluate(node => node.requestSubmit());
      await expect(work).toContainText(copy.error_code_CLIENT_ACTION_REFUSED);
    } else {
      await expect(work.locator('[data-api-error]')).toContainText('CLIENT_ACTION_REFUSED');
      await expect(work.getByText(copy.error_code_CLIENT_ACTION_REFUSED, {exact: true})).toBeVisible();
      // Revoking during a refresh can withdraw a previously validated Result.
      // Preserve its target, not the presence of a link that is now unreadable.
      const resultState = () => page.evaluate(() => ({
        hrefs: [...document.querySelectorAll('[data-work-result-focus="live-output"]')].map(node => node.getAttribute('href')),
        summary: document.querySelector('[data-workbench-summary]')?.textContent,
        refused: Boolean(document.querySelector('.session-status [data-session-error="UNAUTHORIZED"]')),
      }));
      const classify = async () => {
        const source = await resultState();
        if (source.hrefs.length === 1 && source.hrefs[0] === resultHref) return 'retained';
        if (source.hrefs.length === 0 && source.summary === workbenchStrings[zh ? 'zh-TW' : 'en'].wc_unavailable && source.refused
          && refusedResults.some(row => row.body.error.code === 'UNAUTHORIZED')) return 'refused';
        return 'unsettled';
      };
      await expect.poll(async () => resultOutcome = await classify())
        .toMatch(f.late_refusal_refresh ? /^refused$/ : /^(retained|refused)$/);
    }
  }
  await unchanged();
  await shot('02-refused-work');
  step = 'refused-settings';
  await page.evaluate(() => {location.hash = '#/settings?section=connection';});
  await expect(page.locator('.global-connection-facts .ui-pill')).toHaveText(await header.innerText());
  const settingsReason = await page.locator('.global-connection-facts .ui-pill').innerText();
  await headerRefused();
  await unchanged();
  await shot('03-refused-settings');
  if (surface === 'work') {
    // Navigation must not re-present a paused credential. Only this explicit
    // retry may verify it again; the principal remains revoked in this journey.
    const retry = page.waitForResponse(response => response.status() === 401
      && new URL(response.url()).pathname === '/api/v1/capabilities');
    await page.getByRole('button', {name: zh ? '重試已記住的權杖' : 'Retry remembered token', exact: true}).click();
    await retry;
    await unchanged();
    step = 'refused-work-return';
    const beforeReturn = allRequests.length;
    await page.evaluate(hash => {location.hash = hash;}, new URL(workUrl).hash);
    await expect(page).toHaveURL(workUrl);
    // Refused source reads cannot recreate actionable controls from cached data.
    await expect(page.locator('#main [data-api-error]')).toContainText('UNAUTHORIZED');
    await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
    await headerRefused();
    await unchanged();
    assert.equal(allRequests.length, beforeReturn, 'Paused navigation must not send the remembered bearer');
  }
  assert.deepEqual(errors, []);
  await shot('04-refused-work-return');
  const proof = {surface, target, draft_key: draftKey, draft_unchanged: true, attempt: active.attempt, result_href: resultHref,
    result_outcome: resultOutcome, late_refresh: refreshRace,
    refusal_path: refusalPath, refusal_status: response.status(), server_code: body.error.code,
    header_reason: await header.innerText(), settings_reason: settingsReason, draft_has_intent: false,
    reason_key: 'ui_connection_refused', gate_code: 'CLIENT_ACTION_REFUSED', operation_posts: 0};
  writeFileSync(resolve(f.shots, 'refused-access.json'), JSON.stringify({proof, requests: allRequests.slice(since)}, null, 2) + '\n');
  context.off('response', observeResultRefusal);
  send({stage: 'refused-done', operations, browser: browser.version(), refusal: proof});
}

async function foregroundRecovery(active, workUrl) {
  // HTTP, the bundle and storage are real. BAT/model and OS lifecycle events
  // are doubles; this sequence does not establish physical-phone acceptance.
  const originalPage = page, originalContext = context;
  const field = page.locator('[data-follow-up-input]');
  const submit = page.getByRole('button', {name: zh ? '送出下一步' : 'Send next', exact: true});
  const draft = '  尚未送出 📝 — keep this draft\n' + Array.from({length: 36}, (_, i) =>
    `第 ${i + 1} 行：登入修正 / unchanged text 🙂`).join('\n') + '\n  ';
  const frames = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const visibility = value => page.evaluate(value => {
    Object.defineProperty(document, 'visibilityState', {configurable: true, value});
    document.dispatchEvent(new Event('visibilitychange'));
  }, value);
  const snapshot = () => field.evaluate(node => ({
    focused: document.activeElement === node, value: node.value,
    selection: [node.selectionStart, node.selectionEnd, node.selectionDirection],
    scrollY, scrollTop: node.scrollTop, readerTop: document.querySelector('main.session-view').scrollTop,
    stored: Object.entries(localStorage).sort(([a], [b]) => a.localeCompare(b)),
  }));
  const events = deferred(), sources = deferred(), admission = deferred(), preResume = deferred(), preparationEvents = deferred();
  const heldEvents = [], heldSources = [], eventPages = [], preResumeReads = [], preparationPolls = [];
  let holdEvents = false, holdSources = false, holdAdmission = false, holdPreResume = false, holdPreparation = false;
  let before, after, syncKey, intent, changedKeys, recoveryStart, recoveryEnd;
  const operationPosts = since => allRequests.slice(since).filter(row => row.method === 'POST'
    && new URL(row.url).pathname.startsWith('/api/v1/operations'));
  const readTargets = since => {
    const reads = allRequests.slice(since).filter(row => row.method === 'GET');
    const inputs = reads.filter(row => new URL(row.url).pathname.endsWith('/inputs'));
    const results = reads.filter(row => /\/work-attempts\/[^/]+\/result$/.test(new URL(row.url).pathname));
    assert.ok(inputs.length && results.length, 'Recovery must read real inputs and the original Result');
    assert.ok(inputs.every(row => new URL(row.url).searchParams.get('attempt_id') === active.attempt));
    assert.ok(results.every(row => new URL(row.url).pathname.endsWith(`/${active.attempt}/result`)));
  };
  await context.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (holdPreparation && request.method() === 'GET' && path === '/api/v1/events') {
      preparationPolls.push(request.url()); await preparationEvents.promise;
    } else if (holdPreResume && request.method() === 'GET' && /^\/api\/v1\/sessions\/[^/]+\/[^/]+(?:\/messages)?$/.test(path)) {
      preResumeReads.push({at: Date.now(), path, online: await page.evaluate(() => navigator.onLine)});
      await preResume.promise;
    } else if (holdEvents && request.method() === 'GET' && path === '/api/v1/events') {
      heldEvents.push(request.url()); await events.promise;
    } else if (holdSources && request.method() === 'GET'
      && (path.includes('/sessions/') || path.endsWith('/inputs') || path.endsWith('/result'))) {
      heldSources.push(request.url()); await sources.promise;
    } else if (holdAdmission && request.method() === 'POST' && path.startsWith('/api/v1/operations')) {
      await admission.promise;
    }
    // Hold the actual request, then let ApiV1 produce its response unchanged.
    await route.continue();
  });
  context.on('response', response => {
    if (response.ok() && new URL(response.url()).pathname === '/api/v1/events') {
      response.json().then(body => eventPages.push(body)).catch(() => {});
    }
  });
  try {
    step = 'foreground-setup';
    await field.fill(draft);
    await expect(submit).toBeEnabled();
    await page.getByRole('button', {name: zh ? '成果' : 'Results', exact: true}).click();
    const selectedResult = page.locator('[data-work-result-focus="live-output"]');
    const originalHref = await selectedResult.getAttribute('href');
    assert.ok(originalHref.includes(`attempt=${active.attempt}`));
    await page.keyboard.press('Escape');
    await field.fill(draft);
    const original = await field.elementHandle();
    await field.evaluate(node => {
      node.setSelectionRange(3, 19, 'backward');
      node.scrollTop = 45;
      document.querySelector('main.session-view').scrollTop = 75;
    });
    await frames();
    const drafts = await page.evaluate(() => Object.entries(localStorage)
      .filter(([key]) => key.startsWith('batc.pm.') && key.includes('.session.send.')));
    assert.equal(drafts.length, 1);
    const [draftKey, stored] = drafts[0];
    assert.ok(draftKey.endsWith(`.session.send.${JSON.stringify({workspace_id: f.workspace, work_item_id: f.work})}`));
    assert.equal(JSON.parse(stored).values.text, draft);
    assert.equal(Object.hasOwn(JSON.parse(stored), 'intent'), false);
    // app.js saveCursor writes this server checkpoint only after the events page
    // and its source refreshes settle. No draft or other storage key is exempt.
    syncKey = 'batc.sync.' + draftKey.slice('batc.pm.'.length, draftKey.indexOf('.session.send.'));
    // The mounted composer can precede the reader's first saved checkpoint.
    // Compare storage only after that real page has settled.
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), syncKey)).not.toBeNull();
    before = await snapshot();
    assert.equal(before.value, draft);
    assert.equal(before.focused, true);
    assert.deepEqual(before.selection, [3, 19, 'backward']);
    assert.ok(before.scrollTop > 0 && before.readerTop > 0, JSON.stringify(before));
    assert.ok(before.stored.some(([key]) => key === syncKey));
    const unchanged = async () => {
      assert.equal(page, originalPage); assert.equal(context, originalContext);
      assert.equal(await field.evaluate((node, old) => node === old, original), true);
      const current = await snapshot();
      assert.deepEqual(Buffer.from(current.value, 'utf8'), Buffer.from(draft, 'utf8'));
      const withoutCheckpoint = state => ({...state, stored: state.stored.filter(([key]) => key !== syncKey)});
      assert.deepEqual(withoutCheckpoint(current), withoutCheckpoint(before));
      assert.equal(Object.hasOwn(JSON.parse(current.stored.find(([key]) => key === draftKey)[1]), 'intent'), false);
      assert.equal(page.url(), workUrl);
      await expect(selectedResult).toHaveAttribute('href', originalHref);
      assert.deepEqual(operationPosts(recoveryStart), []);
      await expect(page.locator('[data-follow-up-receipt], [data-follow-up-ack]')).toHaveCount(0);
    };
    await expect(submit).toBeEnabled();
    recoveryStart = allRequests.length;
    step = 'pre-resume-refresh';
    holdPreparation = true;
    send({stage: 'prepare-refresh'});
    const preparation = await receive();
    await expect.poll(() => preparationPolls.length).toBeGreaterThan(0);
    holdPreResume = true; holdPreparation = false; preparationEvents.release();
    await expect.poll(() => preResumeReads.length).toBe(2);
    assert.ok(preResumeReads.every(row => row.online), 'Both unanswered reads must start before going offline');
    await expect.poll(() => eventPages.some(body => body.events.some(row => row.seq === preparation.refresh_event.seq))).toBe(true);
    holdPreResume = false;
    step = 'hidden';
    await visibility('hidden');
    await expect(submit).toBeDisabled();
    step = 'offline';
    await context.setOffline(true);
    assert.equal(await page.evaluate(() => navigator.onLine), false);
    assert.equal(await page.evaluate(async () => {
      try {await fetch('/dashboard/index.html?foreground-offline', {cache: 'no-store'}); return false;}
      catch {return true;}
    }), true, 'Chromium must fail a real HTTP fetch while offline');
    await unchanged();
    send({stage: 'background-offline'});
    const changed = await receive();
    assert.equal(changed.event.kind, 'session.updated');
    step = 'resume-events';
    await context.setOffline(false);
    holdEvents = true; holdSources = true;
    await visibility('visible');
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true})));
    await expect(submit).toBeDisabled();
    await expect.poll(() => heldEvents.length).toBeGreaterThan(0);
    await unchanged();
    step = 'resume-sources';
    events.release();
    await expect.poll(() => heldSources.filter(url => new URL(url).pathname.endsWith('/inputs')).length).toBeGreaterThan(0);
    await expect(submit).toBeDisabled();
    await unchanged();
    sources.release();
    await expect(submit).toBeEnabled();
    await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected');
    await expect.poll(() => eventPages.some(body => body.events.some(row => row.seq === changed.event.seq
      && row.resource_id === changed.event.resource_id && row.kind === changed.event.kind))).toBe(true);
    await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key)).cursor, syncKey)).toBeGreaterThanOrEqual(changed.event.seq);
    await frames();
    await unchanged();
    after = await snapshot();
    changedKeys = [...new Set([...before.stored, ...after.stored].map(([key]) => key))]
      .filter(key => new Map(before.stored).get(key) !== new Map(after.stored).get(key));
    assert.deepEqual(changedKeys, [syncKey]);
    recoveryEnd = allRequests.length;
    readTargets(recoveryStart);
    step = 'recovered';
    await shot('02-foreground-recovered');
    holdAdmission = true;
    step = 'explicit-send';
    const sending = Promise.all([post('session.send'), eventWrites.post(submit, {resume:false})]);
    await expect.poll(() => operationPosts(recoveryStart).length).toBe(1);
    intent = await page.evaluate(key => JSON.parse(localStorage.getItem(key)).intent, draftKey);
    const request = operationPosts(recoveryStart)[0];
    assert.equal(request.key, intent.key);
    assert.deepEqual(JSON.parse(request.body), intent.request);
    assert.deepEqual(intent.request.target, {workspace_id: f.workspace, work_item_id: f.work});
    assert.deepEqual(intent.request.preconditions, {expected_attempt_id: active.attempt});
    assert.deepEqual(intent.request.params, {text: draft.trim(), schedule: 'after_current'});
    admission.release();
    const [response] = await sending;
    const sent = await operation(response);
    send({stage: 'sent-after-recovery', operation_id: sent.operation_id, intent, draft});
    assert.equal((await receive()).sent, sent.operation_id);
    step = 'receipt';
    await expect(page.locator(`[data-follow-up-receipt="${sent.operation_id}"]`)).toContainText(zh ? '已送出' : 'Sent');
    assert.equal(operationPosts(recoveryStart).length, 1);
    readTargets(recoveryStart);
    assert.deepEqual(errors, []);
    assert.equal(requests.length, 2);
    assert.equal(new Set(requests.map(row => row.key)).size, 2);
    send({stage: 'foreground-done', operations, browser: browser.version(), recovery: {
      changed_storage_keys: changedKeys, sync_key: syncKey, recovery_posts: 0, explicit_posts: 1,
      event: changed.event, original_result: originalHref, same_context: true, same_node: true,
    }});
  } finally {
    step = 'cleanup';
    events.release(); sources.release(); admission.release(); preResume.release(); preparationEvents.release();
    await context.setOffline(false);
    const ledger = {};
    for (const row of allRequests) {
      const key = `${row.method} ${new URL(row.url).pathname}`;
      ledger[row.step] ||= {};
      ledger[row.step][key] = (ledger[row.step][key] || 0) + 1;
    }
    writeFileSync(resolve(f.shots, 'foreground-recovery.json'), JSON.stringify({before, after, syncKey,
      changedKeys, intent, recoveryStart, recoveryEnd, heldEvents, heldSources, preResumeReads, preparationPolls, ledger, requests: allRequests}, null, 2) + '\n');
  }
}

try {
  await launch();
  let start;
  let savedReviewId=null;
  if (f.create_work) {
    step = 'create-work';
    await page.goto(`${origin}/dashboard/?workbench=1#/project/${f.project}`);
    await page.locator('[data-project-work-create]').click();
    const form = page.locator('[data-create-record="work_item"]');
    const title = zh ? '修正登入重複送出' : 'Fix duplicate login submission';
    await form.getByLabel(zh ? '新工作項目' : 'New work item', {exact: true}).fill(title);
    const created = await clickOperation('work_item.create', form.locator('button[type="submit"]'));
    const link = page.locator('[data-project-items]').getByRole('link', {name: title, exact: true});
    await expect(link).toBeVisible();
    const match = (await link.getAttribute('href')).match(/^#\/item\/(wi_[0-9a-f]{20})$/);
    assert.ok(match, 'The created Work must have its own visible canonical link');
    assert.notEqual(match[1], f.work);
    f.work = match[1];
    await shot('00-work-created');
    send({stage: 'created', operation_id: created.operation_id, work_item_id: f.work});
    assert.equal((await receive()).verified, true);
    step = 'adopt';
  }
  if (f.mode === 'adopt') {
    await page.goto(`${origin}/dashboard/?workbench=1#/pm/${f.workspace}/discussions/${f.thread}`);
    const composer = page.locator('[data-discussion-composer]');
    await composer.fill(zh ? '會員用 Email 登入與忘記密碼。第一版不要推薦碼。請整理成計畫。' : 'Plan member email sign-in and password recovery, excluding referrals.');
    await clickOperation('pm.message.append', page.getByRole('button', {name: zh ? '只存筆記' : 'Save note only', exact: true}));
    await expect(composer).toHaveValue('');
    await clickOperation('pm.conversation.propose', page.locator('[data-discussion-ask]'));
    await page.locator('[data-proposal-review]').click();
    await page.locator('[data-proposal-adopt-panel] textarea').last().fill(zh ? '採用登入與密碼重設，先開始登入工作。' : 'Adopt sign-in and recovery; start the sign-in work first.');
    const row = page.locator(`[data-adopt-work="${f.work}"]`);
    await row.locator('.pm-adopt-choice > input').first().check();
    await row.getByLabel('付費會員可使用 Email 登入', {exact: true}).check();
    await eventWrites.post(page.getByRole('button', {name: zh ? '檢查就緒' : 'Check readiness', exact: true}), {resume:false});
    const submit = page.getByRole('button', {name: zh ? '採用並開始' : 'Adopt and start', exact: true});
    await expect(submit).toBeEnabled();
    await submit.scrollIntoViewIfNeeded();
    await shot('00-plan-review');
    start = await clickOperation('work.adopt_and_start', submit);
  } else {
    await page.goto(`${origin}/dashboard/?workbench=1#/pm/${f.workspace}/overview`);
    await page.locator('[data-workbench-management]').selectOption('board');
    await page.locator(`[data-pm-kanban-work="${f.work}"] [data-board-open]`).click();
    const instruction = page.locator('[data-work-start-instruction]');
    await expect(instruction).toBeVisible();
    await instruction.fill(zh ? '修正登入重複送出，補測試，先開 PR。' : 'Fix duplicate login submission, add tests and prepare a PR.');
    await eventWrites.post(page.locator('[data-work-start-check]'), {resume:false});
    const submit = page.locator('[data-pm-action="work.start"] button[type="submit"]');
    await expect(submit).toBeEnabled();
    start = await clickOperation('work.start', submit);
  }
  if (f.mode !== 'adopt') {
    await expect(page.locator('[data-work-start-item]')).toBeVisible();
    await shot('01-started');
  }
  eventWrites.resume();
  send({stage: 'started', operation_id: start.operation_id});
  let active = await receive();
  if (f.lost_start_reply) {
    step = 'unknown-start';
    assert.equal(active.ready, false);
    const recovery = active.recovery;
    const landing = page.locator('[data-adopt-start-landing]');
    await expect(landing.getByRole('link', {name: 'Resume reconciliation', exact: true}))
      .toHaveAttribute('href', '#/op/' + recovery.handoff);
    const boardUrl = page.url();
    eventWrites.resume(); await page.reload();
    await expect(landing.getByRole('link', {name: 'Resume reconciliation', exact: true})).toBeVisible();
    await shot('00-unconfirmed-start');
    send({stage: 'unknown-shown'});
    assert.equal((await receive()).readback_restored, true);
    // Leaving the landing correctly withdraws the original submission's tab
    // intent. Returning to Resume is a new explicit request to follow this Work.
    await eventWrites.pause();
    const following = () => page.evaluate(() => Object.entries(sessionStorage)
      .filter(([key]) => key.includes('.adopt-start-live.')));
    assert.equal((await following()).length, 1);
    await page.evaluate(workspace => {location.hash = `#/pm/${workspace}/overview`;}, f.workspace);
    await expect(page.locator('[data-pm-region="overview"]')).toBeVisible();
    await eventWrites.pause();
    assert.deepEqual(await following(), []);
    for (const kind of ['parent']) {
      step = 'recover-' + kind;
      eventWrites.resume();
      await page.goto(boardUrl);
      await expect(landing.getByRole('button', {name: 'Resume', exact: true})).toBeVisible();
      // Hold the landing's post-Resume read as well as event delivery. The
      // server handshake settles the real parent before either can observe it.
      const receipt = deferred(), releaseReceipt = deferred();
      const receiptRoute = async route => {
        receipt.release(); await releaseReceipt.promise;
        await route.fallback().catch(() => {});
      };
      await eventWrites.pause();
      await page.route(`**/api/v1/operations/${recovery[kind]}`, receiptRoute);
      const [resumed] = await Promise.all([
        page.waitForResponse(response => response.request().method() === 'POST'
          && new URL(response.url()).pathname === `/api/v1/operations/${recovery[kind]}/resume`),
        eventWrites.post(page.getByRole('button', {name: 'Resume', exact: true}), {resume:false}),
      ]);
      assert.ok([200, 202].includes(resumed.status()), await resumed.text());
      assert.equal((await resumed.json()).operation.operation_id, recovery[kind]);
      send({stage: 'resumed-' + kind, operation_id: recovery[kind]});
      assert.equal((await receive()).settled, true);
      await receipt.promise;
      // Reload with the settled receipt still unread: the new view must make
      // its decision from state, without seeing a completion event transition.
      await page.reload({waitUntil:'domcontentloaded'});
      await page.unroute(`**/api/v1/operations/${recovery[kind]}`, receiptRoute);
      releaseReceipt.release();
      eventWrites.resume();
    }
    active = await receive();
  }
  assert.equal(active.ready, true);
  step = 'queue';
  if (f.mode === 'adopt') {
    // Adoption must lead to its own live Work without another navigation click.
    await expect(page).toHaveURL(new RegExp(`/session/.+[?&]work=${f.work}&attempt=${active.attempt}`));
  } else await page.locator('[data-work-start-item]').click();
  await expect(page.locator('[data-follow-up-input]')).toBeVisible();
  const navigation = () => page.locator('.workspace-tree-scroll > .workspace-tree');
  await expect(navigation().locator(`[data-work-item="${f.work}"]`)).toHaveAttribute('href', new RegExp(`work=${f.work}&attempt=${active.attempt}$`));
  await expect(navigation().locator(`a[href$="/execution/${active.execution}"]`)).toHaveCount(0);
  if (f.mode === 'adopt') await shot('01-started');
  const workUrl = page.url();
  assert.equal(new URL(workUrl).hash.includes(active.attempt), true);
  if (f.refused_access) {
    await refusedAccess(active, workUrl);
  } else if (f.foreground_recovery) {
    await foregroundRecovery(active, workUrl);
  } else {
  const text = zh ? '再補一個失敗後重新送出的案例。' : 'Add a retry-after-failure case.';
  await page.locator('[data-follow-up-input]').fill(text);
  const queued = await clickOperation('session.send', page.locator('[data-work-section="next-turn"]').getByRole('button', {name: zh ? '送出下一步' : 'Send next', exact: true}));
  await expect(page.locator('[data-follow-up-receipt]')).toContainText(zh ? '排隊中' : 'Queued');
  const draft = f.browser_profile
    ? (zh ? '  保留這段尚未送出的草稿。\n還不要送出。📝  ' : '  Keep this unsent draft.\nDo not send yet. 📝  ')
    : (zh ? '保留這段尚未送出的草稿。' : 'Keep this unsent draft.');
  let savedDraft;
  if (f.browser_profile) {
    send({stage: 'awaiting-admission', operation_id: queued.operation_id});
    assert.equal((await receive()).admitted, queued.operation_id);
    await expect(page.locator('[data-follow-up-input]')).toHaveValue('');
    await page.locator('[data-follow-up-input]').fill(draft);
    savedDraft = await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.includes('.session.send.')));
    assert.equal(savedDraft.length, 1);
    assert.equal(JSON.parse(savedDraft[0][1]).values.text, draft);
    assert.equal(JSON.parse(savedDraft[0][1]).intent, undefined);
  }
  await shot('02-queued');
  const layout = await page.locator('.workspace-conversation').evaluate(lane => ({
    readingBottom: Math.max(...[...lane.querySelectorAll('.conversation > .actions > *')]
      .map(node => node.getBoundingClientRect().bottom)),
    nextTop: lane.querySelector('[data-work-section="next-turn"]').getBoundingClientRect().top,
    overflow: document.documentElement.scrollWidth - innerWidth,
  }));
  assert.ok(layout.readingBottom <= layout.nextTop, `Reading controls overlap the next step: ${JSON.stringify(layout)}`);
  assert.ok(layout.overflow <= 1, `Page overflows: ${JSON.stringify(layout)}`);
  if (f.browser_profile) await context.close();
  else await browser.close();
  browser = null;
  send({stage: 'closed', operation_id: queued.operation_id, instruction: text, draft});
  const fixed = await receive();
  assert.equal(fixed.attempt, active.attempt);
  step = 'reconnect';
  const reconnectStart = allRequests.length;
  await launch();
  await page.goto(workUrl);
  await expect(page.locator('[data-follow-up-receipt]')).toContainText(text);
  await expect(page.locator('[data-follow-up-receipt]')).not.toContainText(zh ? '排隊中' : 'Queued');
  if (f.browser_profile) {
    const receipt = page.locator(`[data-follow-up-receipt="${queued.operation_id}"]`);
    await expect(receipt).toHaveCount(1);
    await expect(receipt).toHaveAttribute('data-follow-up-state', 'sent');
    await expect(receipt).toContainText(zh ? '已送出' : 'Sent');
    await expect(page.locator('[data-follow-up-input]')).toHaveValue(draft);
    assert.deepEqual(await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.includes('.session.send.'))), savedDraft);
    const receiptPath = `/api/v1/operations/${queued.operation_id}`;
    const [reply] = await Promise.all([
      page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === receiptPath),
      receipt.locator(`a[href="#/op/${queued.operation_id}"]`).click(),
    ]);
    assert.equal(reply.status(), 200);
    const original = (await reply.json()).operation;
    assert.equal(original.operation_id, queued.operation_id);
    assert.equal(original.input_receipt.operation_id, queued.operation_id);
    assert.equal(original.input_receipt.state, 'sent');
    await page.goto(workUrl);
    await expect(page.locator(`[data-follow-up-receipt="${queued.operation_id}"]`)).toHaveAttribute('data-follow-up-state', 'sent');
    await expect(page.locator('[data-follow-up-input]')).toHaveValue(draft);
    assert.deepEqual(Buffer.from(await page.locator('[data-follow-up-input]').inputValue()), Buffer.from(draft));
    const reconnectRequests = allRequests.slice(reconnectStart);
    const posts = reconnectRequests.filter(row => row.method === 'POST' && new URL(row.url).pathname.startsWith('/api/v1/operations'));
    assert.deepEqual(posts, [], 'Reconnect reads the original receipt; it must never submit or resume an operation');
    assert.ok(reconnectRequests.some(row => row.method === 'GET' && new URL(row.url).pathname === receiptPath));
    writeFileSync(resolve(f.shots, 'reconnect-requests.json'), JSON.stringify(reconnectRequests, null, 2) + '\n');
    closeRestart = {operation_id: queued.operation_id, receipt_state: original.input_receipt.state,
      operation_posts: posts.length, draft_utf8: Buffer.from(draft).toString('hex'), same_storage: true};
    await shot('02-reconnected-draft');
  }
  if (f.withdraw_writer) {
    step = 'withdrawn-writer';
    await expect(page.locator('[data-follow-up-receipt]')).toContainText(zh ? '目前權限不允許送出此指令；指令尚未送出。' : 'Your current permissions do not allow sending this instruction. It was not sent.');
    await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
    await expect(page.locator('.workspace-composer')).toBeHidden();
    await page.getByRole('button', {name: zh ? '成果' : 'Results', exact: true}).click();
    await expect(page.locator('[data-work-result]')).toContainText('Fixed double submission.');
    await expect.poll(() => results.get(active.attempt)?.changes?.fixed_result_commit?.commit_sha).toBe(fixed.candidate);
    await expect(page.locator('[data-work-publish-submit]')).toHaveCount(0);
    eventWrites.resume(); await page.reload();
    await expect(page.locator('[data-follow-up-receipt]')).toContainText(text);
    await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
    await expect(page.locator('.workspace-composer')).toBeHidden();
    await expect(page.locator('.workspace-conversation .conversation')).toContainText('Fixed double submission.');
    await shot('03-withdrawn-reader');
    await page.getByRole('button', {name: zh ? '成果' : 'Results', exact: true}).click();
    await expect(page.locator('[data-work-result]')).toContainText('Fixed double submission.');
    await expect(page.locator('[data-work-publish-submit]')).toHaveCount(0);
    await shot('04-withdrawn-result');
    assert.deepEqual(errors, []);
    assert.equal(requests.length, 2, 'Read-only reconnection must not dispatch, retry or publish');
    assert.equal(new Set(requests.map(row => row.key)).size, 2);
    send({stage: 'withdrawn', operations, browser: browser.version()});
  } else {
  if (!f.browser_profile) await page.locator('[data-follow-up-input]').fill(draft);
  const views = page.locator('[data-workbench-views]');
  for (const name of zh ? ['關聯', '現場', '工作'] : ['Lineage', 'Live', 'Work']) {
    await views.getByRole('tab', {name, exact: true}).click();
    assert.equal(page.url(), workUrl);
  }
  await expect(page.locator('[data-follow-up-input]')).toHaveValue(draft);
  const expectedOperations = (f.mode === 'adopt' ? 5 : 3) + (f.create_work ? 1 : 0);
  assert.equal(requests.length, expectedOperations - 1, 'Changing views must not dispatch work');
  step = 'discussion-return';
  await page.getByRole('button', {name: zh ? '更多操作' : 'More actions', exact: true}).click();
  await page.locator('[data-menu-key="discuss"]').click();
  const discussion = new URLSearchParams(new URL(page.url()).hash.split('?')[1]);
  assert.deepEqual(JSON.parse(discussion.getAll('ref')[0]), {kind: 'work_item', id: f.work});
  const returnHref = discussion.get('from');
  assert.ok(returnHref?.startsWith('#/session/'));
  const selection = Object.fromEntries(new URLSearchParams(returnHref.split('?')[1]));
  assert.equal(selection.workspace, f.workspace);
  assert.equal(selection.work, f.work);
  assert.equal(selection.attempt, active.attempt);
  const back = page.locator('[data-discussion-back]');
  await expect(back).toHaveAttribute('href', returnHref);
  await expect(back).toBeVisible();
  await shot('03-discussion');
  eventWrites.resume(); await page.reload();
  await expect(back).toHaveAttribute('href', returnHref);
  await back.click();
  await expect(page.locator('[data-follow-up-input]')).toHaveValue(draft);
  await expect(page.locator('[data-follow-up-receipt]')).toContainText(text);
  assert.equal(requests.length, expectedOperations - 1, 'Discuss and return must not create or dispatch an operation');
  // Opening this drawer holds the linked Work's deferred render. Fence
  // delivery before opening it, not while that render is waiting for close.
  await eventWrites.pause();
  await page.getByRole('button', {name: zh ? '成果' : 'Results', exact: true}).click();
  await expect(page.locator('[data-work-result]')).toContainText('Added the retry-after-failure case.');
  await expect(page.locator('[data-work-publish-commit]')).toHaveAttribute('title', fixed.candidate);
  const supportingTools=page.locator('[data-work-result-tools]');
  await expect(supportingTools).not.toHaveAttribute('open');
  await expect(page.locator('[data-session-sources]')).toBeHidden();
  await supportingTools.locator(':scope > summary').click();
  await expect(page.locator('[data-session-sources]')).toBeVisible();
  await supportingTools.locator(':scope > summary').click();
  await shot('03-reconnected-result');
  step = 'review-result';
  await page.getByRole('button', {name: zh ? '審閱這份成果' : 'Review this result', exact: true}).click();
  const review = page.locator('[data-work-quality] [data-pm-action="pm.quality.review.create"]');
  await expect(review.getByLabel(zh ? '候選提交' : 'Candidate commit', {exact: true})).toHaveValue(fixed.candidate);
  await expect(review.getByLabel(zh ? '候選提交' : 'Candidate commit', {exact: true})).toBeDisabled();
  assert.equal(requests.length, expectedOperations - 1, 'Opening the fixed Result review must not create an operation');
  await shot('03-result-review');
  if (f.verify_result) {
    step = 'verify-fixed-result';
    const verifier=page.locator('[data-work-quality] [data-pm-execution-verification]');
    await verifier.locator(':scope > summary').click();
    const form=verifier.locator('[data-pm-action="pm.execution.verify"]');
    await expect(form.locator('button[type=submit]')).toBeEnabled();
    await shot('03-before-verification');
    const verified=await clickOperation('pm.execution.verify',form.locator('button[type=submit]'));
    send({stage:'result-verified',operation_id:verified.operation_id});
    assert.equal((await receive()).result_verified,true);
    await expect(verifier).toContainText(fixed.candidate);
    await expect(page.locator('[data-work-quality]')).not.toContainText(zh?'需求驗收: 已接受':'Requirement acceptance: Accepted');
    const conclusion=page.locator('[data-work-result] [data-check=verification]');
    await expect(conclusion).toHaveText(zh?'已驗證':'Verified');
    await expect(conclusion).toBeFocused();await expect(conclusion).toBeInViewport();
    await shot('03-verified-result');
  }
  if (f.review_result) {
    step = 'appoint-review';
    const quality = page.locator('[data-work-quality]');
    await quality.locator('[data-quality-create]>summary').click();
    await review.getByLabel(zh ? '負責審查者' : 'Responsible reviewer', {exact: true}).selectOption(f.reviewer_actor);
    const policies = await review.getByLabel(zh ? '指定驗證' : 'Required verification', {exact: true}).all();
    assert.ok(policies.length > 0);
    for (const policy of policies) await policy.selectOption('github');
    await review.getByLabel(zh ? '審查政策理由' : 'Reason for this review policy', {exact: true})
      .fill('Review the fixed result against its original criteria and independent GitHub checks.');
    const created = await clickOperation('pm.quality.review.create', review.locator('button[type=submit]'));
    send({stage: 'review-created', operation_id: created.operation_id});
    assert.equal((await receive()).review_created, true);
    step = 'record-review';
    const record = quality.locator('[data-pm-action="pm.quality.review.record"]');
    await expect(record.locator('button[type=submit]')).toBeEnabled();
    for (const rationale of await record.getByLabel(zh ? '條件判斷理由' : 'Criterion rationale', {exact: true}).all())
      await rationale.fill('Independent verification has not been supplied for this fixed result.');
    await record.getByLabel(zh ? '這次評估的理由' : 'Reason for this assessment', {exact: true})
      .fill('Keep the original requirement unassessed until its evidence is available.');
    const saved = await clickOperation('pm.quality.review.record', record.locator('button[type=submit]'));
    send({stage: 'review-recorded', operation_id: saved.operation_id});
    assert.equal((await receive()).review_recorded, true);
    await expect(record.locator('[data-pm-saved]')).toBeVisible();
    const conclusion=quality.getByRole('heading', {level:3});
    await expect(conclusion).toBeFocused();
    await expect(conclusion).toBeInViewport();
    await expect(quality).toContainText(zh ? '需求驗收: 未評估' : 'Requirement acceptance: Unassessed');
    savedReviewId=await quality.getByLabel(zh ? '已保存審查' : 'Saved review', {exact:true}).inputValue();
    assert.match(savedReviewId, /^pmqr_[0-9a-f]{32}$/);
    await shot('03-reviewed-unassessed');
    step = 'discuss-review';
    const reviewReturn=new URL(page.url()).hash;
    await quality.getByRole('link', {name:zh ? '與 PM 討論這次評估' : 'Discuss this assessment with PM', exact:true}).click();
    await expect(page.locator('.pd-context [data-discussion-context="quality_assessment"]')).toBeVisible();
    const firstNote=page.locator('[data-pm-action="pm.thread.create"]');
    await firstNote.locator('[data-discussion-composer]').fill(zh
      ? '保留這份原始評估，稍後討論還缺哪些證據。'
      : 'Keep this original assessment for a later discussion of the missing evidence.');
    const [discussion,note]=await Promise.all([
      clickOperation('pm.thread.create', firstNote.locator('button[type=submit]')),
      post('pm.message.append').then(operation),
    ]);
    send({stage:'review-discussed',thread_operation_id:discussion.operation_id,message_operation_id:note.operation_id});
    assert.equal((await receive()).review_discussed,true);
    step = 'analyze-review';
    await page.locator('[data-discussion-composer]').fill(zh
      ? '這份成果距離原本的驗收條件還缺什麼？請根據這次評估說明下一步。'
      : 'What is missing for the original acceptance criteria? Explain the next step from this assessment.');
    const analysis=await clickOperation('pm.run.analyze',page.locator('[data-discussion-reply]'));
    send({stage:'review-analyzed',operation_id:analysis.operation_id});
    assert.equal((await receive()).review_analyzed,true);
    const answer=page.locator('[data-pm-role="assistant"]');
    await expect(answer).toContainText(fixed.candidate.slice(0,12));
    await expect(answer).toContainText(zh ? '還缺固定版本的驗證證據' : 'Fixed-revision verification evidence is missing');
    eventWrites.resume(); await page.reload();
    await expect(page.locator('[data-pm-role="assistant"]')).toHaveCount(1);
    await expect(page.locator('[data-pm-role="assistant"]')).toContainText(fixed.candidate.slice(0,12));
    await expect(page.locator('.pd-context [data-discussion-context="quality_assessment"]')).toBeVisible();
    await expect(page.locator('[data-discussion-back]')).toHaveAttribute('href',reviewReturn);
    await shot('03-discussed-assessment');
    await page.locator('.pd-context [data-discussion-context="quality_assessment"]').click();
    const source=page.locator('[data-discussion-assessment]');
    await expect(source).toContainText(fixed.candidate.slice(0,12));
    await expect(source).toContainText('Keep the original requirement unassessed until its evidence is available.');
    const criterion=source.locator('summary').filter({hasText:zh ? '未評估' : 'Unassessed'}).first();
    await criterion.click();
    await expect(source).toContainText('Independent verification has not been supplied for this fixed result.');
    await shot('03-original-assessment');
    await page.keyboard.press('Escape');
    await page.locator('[data-discussion-back]').click();
    await expect(page.locator('[data-follow-up-input]')).toHaveValue(draft);
    await eventWrites.pause();
    await page.getByRole('button', {name:zh ? '成果' : 'Results',exact:true}).click();
    await page.getByRole('button', {name:zh ? '審閱這份成果' : 'Review this result',exact:true}).click();
    await expect(page.locator('[data-work-quality]').getByLabel(zh ? '已保存審查' : 'Saved review',{exact:true})).toHaveValue(savedReviewId);
  }
  step = 'publish';
  await page.locator('[data-work-publish-title]').fill(zh ? '修正登入重複送出' : 'Fix duplicate login submission');
  await page.locator('[data-work-publish-body]').fill(zh ? '修正與重試案例已完成，等待審閱。' : 'Login repair and retry case ready for review.');
  await page.locator('[data-work-publish-draft="true"]').check();
  const published = await clickOperation('github.pr.publish', page.locator('[data-work-publish-submit]'));
  send({stage: 'published', operation_id: published.operation_id});
  assert.equal((await receive()).verified, true);
  eventWrites.resume(); await page.reload();
  await expect(page.locator('[data-follow-up-input]')).toHaveValue(draft);
  await page.getByRole('button', {name: zh ? '成果' : 'Results', exact: true}).click();
  await expect(page.locator('[data-work-publish-pr]')).toContainText('21');
  if (f.review_result) {
    await page.getByRole('button', {name: zh ? '審閱這份成果' : 'Review this result', exact:true}).click();
    const quality=page.locator('[data-work-quality]');
    await expect(quality.getByLabel(zh ? '已保存審查' : 'Saved review', {exact:true})).toHaveValue(savedReviewId);
    await expect(quality).toContainText(zh ? '需求驗收: 未評估' : 'Requirement acceptance: Unassessed');
  }
  await shot('04-published');
  if (f.peer_agent) {
    step = 'mcp-peer';
    send({stage: 'peer-ready'});
    const peer = await receive();
    // Follow the MCP Result's own Work link, then the existing UI link to its Attempt.
    await page.goto(`${origin}/dashboard/?workbench=1${peer.work_hash}`);
    await page.locator('[data-work-start-item]').click();
    assert.ok(page.url().includes(`work=${peer.work_item_id}&attempt=${peer.attempt_id}`));
    await expect(page.locator('[data-follow-up-note]')).toContainText("You are not this work's controller.");
    await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
    await expect(page.locator('.workspace-composer')).toBeHidden();
    await page.getByRole('button', {name: 'Results', exact: true}).click();
    await expect(page.locator('[data-work-result]')).toContainText('Checked keyboard focus after login failure.');
    await expect.poll(() => results.get(peer.attempt_id)?.changes?.fixed_result_commit?.commit_sha).toBe(peer.candidate);
    assert.equal(results.get(peer.attempt_id).identity.work_item_id, peer.work_item_id);
    await expect(page.locator('[data-work-publish-submit]')).toHaveCount(0);
    await expect(navigation().locator(`[data-work-item="${peer.work_item_id}"]`)).toHaveAttribute('href', new RegExp(`work=${peer.work_item_id}&attempt=${peer.attempt_id}$`));
    await expect(navigation().locator(`a[href$="/execution/${peer.execution_operation_id}"]`)).toHaveCount(0);
    await shot('05-mcp-peer-result');
    await page.goto(workUrl);
    await expect(page.locator('[data-follow-up-input]')).toHaveValue(draft);
    await page.getByRole('button', {name: 'Results', exact: true}).click();
    await expect(page.locator('[data-work-publish-pr]')).toContainText('21');
  }
  assert.deepEqual(errors, []);
  assert.equal(requests.length, expectedOperations + (f.review_result ? 5 : 0) + (f.verify_result ? 1 : 0));
  assert.equal(new Set(requests.map(r => r.key)).size, expectedOperations + (f.review_result ? 5 : 0) + (f.verify_result ? 1 : 0));
  assert.ok(requests.every(r => r.key));
  const follow = requests.find(row => row.body.action === 'session.send');
  assert.deepEqual(follow.body.target, {workspace_id: f.workspace, work_item_id: f.work});
  assert.deepEqual(follow.body.params, {text, schedule: 'after_current'});
  assert.deepEqual(follow.body.preconditions, {expected_attempt_id: active.attempt});
  assert.equal(requests.at(-1).body.params.candidate_sha, fixed.candidate);
  assert.equal(requests.at(-1).body.params.draft, true);
  send({stage: 'done', operations, resumed_operations: resumedOperations, browser: browser.version(), discussion_return: selection, close_restart: closeRestart});
  }
  }
} catch (error) {
  console.error(JSON.stringify({step, url: page?.url(), errors, requests, responses, pending: [...pending.values()]}));
  if (page && !page.isClosed()) {
    console.error(await page.locator('body').innerText());
    await shot('failure').catch(() => {});
  }
  console.error(error);
  process.exitCode = 1;
} finally {
  writeFileSync(resolve(f.shots, 'browser-requests.json'), JSON.stringify(allRequests, null, 2) + '\n');
  if (f.browser_profile) await context?.close();
  else await browser?.close();
  process.stdin.destroy();
}
