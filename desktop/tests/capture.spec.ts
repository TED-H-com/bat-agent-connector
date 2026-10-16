import {expectErrorDetails} from './api-error-assertions.ts';
import {test, expect, type Page} from '@playwright/test';
const op = 'op_' + 'a'.repeat(32), aid = 'art_' + 'b'.repeat(32), wid = 'wi_' + 'c'.repeat(20);
const ref = {artifact_id: aid, revision: 1, digest: 'd'.repeat(64)};
async function fixture(page: Page, native: boolean, options: any = {}) {
  const state = {actor: 'capture-person', server: 'capture-server', provenance: 'manual', scopes: ['observe', 'manage'],
    writes: [] as any[], previews: [] as any[], reads: [] as string[], errors: [] as string[], lost: false,
    expires: Date.now()/1000 + 600, status: 'succeeded', operationReads: 0, failRead: false,
    events: [] as any[], after: 0, holdPreview: null as any, delayPreview: false, ...options};
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    const caps = {actor: state.actor, scopes: state.scopes, api_version: 1, contract_version: '2026-10-08',
      hosts: [{host: 'demo', writes: false}], actions: [{action: 'artifact.capture', allowed: state.scopes.includes('manage')}],
      features: {checkpoints: []}, artifacts: {capture: {manual_single_file: true, snapshot: false}, limits: {max_file_bytes: 1024}}};
    const row = {host: 'demo', session_id: 'manual-session-1', title: 'Manual source', provenance: state.provenance, api_access: 'read_only'};
    const operation = {operation_id: op, action: state.badAction ? 'artifact.upload' : 'artifact.capture',
      target: {preview_id: 'acpv_'+(state.badTarget ? '0' : 'e').repeat(32)}, status: state.status,
      result: state.badDigest ? {...ref, digest: '0'.repeat(64)} : ref};
    if (input.method === 'POST') {
      if (path === '/artifact-capture-previews') {
        state.previews.push(input);
        if (state.delayPreview) await new Promise(resolve => {state.holdPreview = resolve;});
        return {status: 200, data: {preview: {preview_id: 'acpv_'+'e'.repeat(32), preview_token: 'cap1.fixture.signature', // gitleaks:allow (unsigned mock preview)
          fingerprint: 'f'.repeat(64), issued_at: Date.now()/1000, expires_at: state.expires, snapshot: false,
          relative_path: input.body.relative_path, source: {host: input.body.host, session_id: input.body.session_id,
            provenance: 'manual', root: '/fixture/manual', repository_root: '/fixture/manual'},
          evidence: {size_bytes: 10, digest: ref.digest, head_sha: '1'.repeat(40)}}}};
      }
      state.writes.push(input);
      if (state.lost) {state.lost = false; return {status: 503, data: {error: {code: 'LOST', message: 'Lost capture reply'}}};}
      if (state.holdOperation) await new Promise(resolve => {state.releaseOperation = resolve;});
      if (state.operationError) {
        const error = state.operationError;
        return {status: error.status || 409, data: {error: {code: error.code, message: error.message || error.code}}};
      }
      return {status: 200, data: {operation}};
    }
    state.reads.push(input.path);
    if (path === '/operations/'+op) {
      state.operationReads++;
      if (state.delayRead) {state.delayRead = false; await new Promise(resolve => {state.holdRead = resolve;});}
      if (state.holdOperationRead) {
        if (!state.operationReadGate || state.operationReadReleased) {
          state.operationReadReleased = false;
          state.operationReadEpoch = (state.operationReadEpoch || 0) + 1;
          state.operationReadGate = new Promise(resolve => {
            state.releaseOperationRead = () => {state.operationReadReleased = true; resolve();};
          });
        }
        await state.operationReadGate;
      }
      if (state.operationNote) operation.status_reason = state.operationNote;
      if (state.failRead) return {status: 503, data: {error: {code: 'READ_FAILED', message: 'Capture read failed'}}};
      return {status: 200, data: {operation}};
    }
    if (path === '/events') state.after = Number(url.searchParams.get('after'));
    const events = state.events.filter((event: any) => event.seq > state.after), cursor = events.at(-1)?.seq || state.after;
    if (path === '/events' && events.length) state.delivered = true;
    return {status: 200, data: path === '/capabilities' ? caps : path === '/bootstrap' ? {capabilities: caps,
      sync: {version: 1, server_id: state.server, principal_id: state.actor, checkpoint: {cursor: 0, token: 'proof-0'}}}
      : path === '/events' ? {events, head_cursor: cursor, next_cursor: cursor, sync: {checkpoint: {cursor, token: 'proof-'+cursor}}}
      : path === '/sessions/demo/manual-session-1' ? {session: row}
      : path === '/artifacts' ? {artifacts: []}
      : path.startsWith('/artifacts/') ? {artifact: {...ref, state: 'ready', source: {kind: 'manual_capture', operation_id: op}}}
      : path === '/work-items/'+wid ? {work_item: {work_item_id: wid, title: 'Capture attachment fixture', version: 1, state: 'todo',
        request: 'Keep this request', goal: '', acceptance: '', steps: [], attachments: [], completion: {display_state: 'todo', fingerprint: 'fixture'}},
        project: {project_id: 'prj_fixture', name: 'Fixture'}, links: [], path: [], children: [], derived: [], events: []}
      : {messages: [], sessions: [], operations: [], hosts: [], work_items: [], checkpoints: []}};
  };
  page.on('pageerror', error => state.errors.push(error.message));
  if (native) {
    await page.exposeFunction('captureFixture', dispatch);
    await page.addInitScript(() => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === 'native_status') return {endpoint: 'https://fixture.example', credential_available: true};
      if (command === 'connector_connect') return (await (window as any).captureFixture({method: 'GET', path: '/capabilities'})).data;
      if (command === 'connector_disconnect') return;
      if (command === 'connector_request') return (window as any).captureFixture(args.input);
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      throw new Error(command);
    }}}));
  } else await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
  await page.route('**/api/v1/**', async route => {
    if (native) throw new Error('Native capture cannot use browser HTTP');
    const request = route.request(), url = new URL(request.url());
    const response = await dispatch({method: request.method(), path: url.pathname.slice('/api/v1'.length)+url.search,
      body: request.postDataJSON(), idempotency_key: request.headers()['idempotency-key']});
    await route.fulfill({status: response.status, json: response.data});
  });
  return state;
}
async function form(page: Page, attachment = false, locale = 'en-US') {
  await page.goto('/dashboard/#/'+(attachment ? 'item/'+wid : 'session/demo/manual-session-1'));
  if (attachment) await page.getByRole('button', {name: locale === 'en-US' ? 'More' : '更多', exact: true}).click();
  const box = page.locator('[data-capture]');
  await box.locator('summary').click();
  await box.locator('select').selectOption('demo');
  await box.locator('input').nth(0).fill('manual-session-1');
  await box.locator('input').nth(1).fill('notes/input.txt');
  return box;
}
for (const native of [false, true]) {
  const mode = native ? 'native' : 'browser';
  test(`manual capture freezes original intent across lost reply, expiry and reload (${mode})`, async ({page}) => {
    const state = await fixture(page, native, {lost: true});
    let box = await form(page);
    await box.getByRole('button', {name: 'Preview source file', exact: true}).click();
    await box.getByRole('checkbox').check();
    await box.getByRole('button', {name: 'Save reviewed file', exact: true}).click();
    await expectErrorDetails(box, 'Lost capture reply', 'LOST');
    const original = structuredClone(state.writes[0]);
    expect(original.body).toEqual({action: 'artifact.capture', target: {preview_id: 'acpv_'+'e'.repeat(32)},
      params: {preview_token: 'cap1.fixture.signature'}, preconditions: {expected_fingerprint: 'f'.repeat(64)}}); // gitleaks:allow (unsigned mock preview)
    await page.evaluate(() => {for (let i=0;i<localStorage.length;i++) {const key=localStorage.key(i)!;if(key.startsWith('batc.capture.')) {
      const draft=JSON.parse(localStorage.getItem(key)!); draft.preview.expires_at=1; localStorage.setItem(key,JSON.stringify(draft));}}});
    await page.reload(); box = page.locator('[data-capture]'); await box.locator('summary').click();
    await expect(box.locator('input').nth(1)).toHaveValue('notes/input.txt');
    await expect(box.locator('input').nth(1)).toBeDisabled();
    await box.getByRole('button', {name: 'Check original capture', exact: true}).click();
    await expect(box).toContainText(aid);
    expect(state.writes).toHaveLength(2);
    expect(state.writes[1]).toEqual(original);
    expect(state.previews).toHaveLength(1);
    expect(state.errors).toEqual([]);
  });
  test(`accepted capture reads back after reload and attaches only by explicit selection (${mode})`, async ({page}) => {
    const state = await fixture(page, native, {status: 'running'});
    let box = await form(page, true);
    await box.getByRole('button', {name: 'Preview source file', exact: true}).click();
    await box.getByRole('checkbox').check();
    await box.getByRole('button', {name: 'Save reviewed file', exact: true}).click();
    await expect(box.getByRole('link', {name: op, exact: true})).toBeVisible();
    state.status = 'succeeded';
    await page.reload(); await page.getByRole('button', {name: 'More', exact: true}).click();
    box = page.locator('[data-capture]'); await box.locator('summary').click();
    await expect(box).toContainText(aid);
    await expect(page.locator('.attachment-list')).not.toContainText(aid);
    await box.getByRole('button', {name: 'Add to attachment draft', exact: true}).click();
    await expect(page.locator('.attachment-list')).toContainText(aid);
    expect(state.writes).toHaveLength(1);
    await page.reload(); await page.getByRole('button', {name: 'More', exact: true}).click();
    await expect(page.locator('.attachment-list')).toContainText(aid);
    expect(state.errors).toEqual([]);
  });
  test(`expired preview and unknown source cannot submit capture (${mode})`, async ({page}) => {
    const state = await fixture(page, native, {expires: 1});
    const box = await form(page);
    await box.getByRole('button', {name: 'Preview source file', exact: true}).click();
    await expect(box).toContainText('Preview expired.');
    await expect(box.getByRole('button', {name: 'Save reviewed file', exact: true})).toBeDisabled();
    await box.locator('input').nth(1).fill('../escape');
    await expect(box.getByRole('button', {name: 'Preview source file', exact: true})).toBeDisabled();
    state.provenance = 'unknown'; await box.locator('input').nth(1).fill('new.txt');
    await box.getByRole('button', {name: 'Preview source file', exact: true}).click();
    await expect(box).toContainText('explicitly observed as manually created');
    expect(state.previews).toHaveLength(1); expect(state.writes).toEqual([]);
  });
  test(`backend identity change during preview preserves old draft without submitting (${mode})`, async ({page}) => {
    const state = await fixture(page, native, {delayPreview: true});
    const box = await form(page);
    await box.getByRole('button', {name: 'Preview source file', exact: true}).click();
    await expect.poll(() => typeof state.holdPreview).toBe('function');
    state.server = 'other-server'; state.actor = 'other-person';
    await page.goto('/dashboard/#/settings');
    await page.getByRole('button', {name: 'Disconnect', exact: true}).click();
    if (!native) await page.getByPlaceholder('batc_…').fill('other-fixture-token');
    await page.getByRole('button', {name: 'Connect', exact: true}).click();
    await expect(page).toHaveURL(/#\/home$/);
    state.holdPreview();
    await page.goto('/dashboard/#/session/demo/manual-session-1');
    const next = page.locator('[data-capture]'); await next.locator('summary').click();
    await expect(next.locator('input').nth(1)).toHaveValue('');
    expect(state.writes).toEqual([]);
    expect(await page.evaluate(() => Object.entries(localStorage).filter(([k])=>k.startsWith('batc.capture.')).some(([,v])=>v.includes('notes/input.txt')))).toBe(true);
    expect(state.errors).toEqual([]);
  });
  test(`failed capture event read cannot acknowledge its cursor (${mode})`, async ({page}) => {
    const state = await fixture(page, native, {status: 'running'});
    const box = await form(page);
    await box.getByRole('button', {name: 'Preview source file', exact: true}).click();
    await box.getByRole('checkbox').check();
    await box.getByRole('button', {name: 'Save reviewed file', exact: true}).click();
    await expect(box.getByRole('link', {name: op, exact: true})).toBeVisible();
    state.failRead = true; state.events = [{seq: 1, kind: 'operation.updated', resource_type: 'operation', resource_id: op}];
    await expect(page.locator('.live')).toHaveAttribute('data-connection','connected');await expect(page.locator('.live')).toHaveAttribute('title',/paused/);
    expect(state.after).toBe(0);
    state.failRead = false; state.status = 'succeeded';
    await expect(box).toContainText(aid);
    await expect.poll(() => state.after).toBe(1);
    expect(state.writes).toHaveLength(1); expect(state.errors).toEqual([]);
  });
  test(`damaged preview preserves accepted intent and recovers its exact result (${mode})`, async ({page}) => {
    const state = await fixture(page, native, {status: 'running'});
    let box = await form(page, true);
    await box.getByRole('button', {name: 'Preview source file', exact: true}).click();
    await box.getByRole('checkbox').check();
    await box.getByRole('button', {name: 'Save reviewed file', exact: true}).click();
    await expect(box.getByRole('link', {name: op, exact: true})).toBeVisible();
    const original = await page.evaluate(() => {
      const key = Object.keys(localStorage).find(k=>k.startsWith('batc.capture.'))!, draft=JSON.parse(localStorage.getItem(key)!);
      draft.preview={evidence:null}; localStorage.setItem(key,JSON.stringify(draft)); return draft.intent;
    });
    state.status = 'succeeded'; await page.reload(); await page.getByRole('button', {name: 'More', exact: true}).click();
    box = page.locator('[data-capture]'); await box.locator('summary').click();
    await expect(box).toContainText(aid);
    await box.getByRole('button', {name: 'Add to attachment draft', exact: true}).click();
    await expect(page.locator('.attachment-list')).toContainText(aid);
    const restored = await page.evaluate(() => JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k=>k.startsWith('batc.capture.'))!)!).intent);
    expect(restored.key).toBe(original.key); expect(restored.request).toEqual(original.request);
    expect(state.writes).toHaveLength(1); expect(state.errors).toEqual([]);
  });
  test(`capture event waits for an older poll then reads fresh evidence (${mode})`, async ({page}) => {
    const state = await fixture(page, native, {status: 'running'});
    const box = await form(page);
    await box.getByRole('button', {name: 'Preview source file', exact: true}).click();
    await box.getByRole('checkbox').check();
    await box.getByRole('button', {name: 'Save reviewed file', exact: true}).click();
    await expect(box.getByRole('link', {name: op, exact: true})).toBeVisible();
    state.delayRead = true;
    await expect.poll(() => typeof state.holdRead).toBe('function');
    const reads = state.operationReads;
    state.status = 'succeeded'; state.events = [{seq: 1, kind: 'operation.updated', resource_type: 'operation', resource_id: op}];
    // The event request must have arrived while the older read is still blocked.
    await expect.poll(() => state.delivered).toBe(true);
    expect(state.after).toBe(0);
    state.holdRead();
    await expect(box).toContainText(aid);
    await expect.poll(() => state.after).toBe(1);
    expect(state.operationReads).toBeGreaterThan(reads);
    expect(state.errors).toEqual([]);
  });
  for (const mismatch of ['badAction','badTarget','badDigest']) test(`mismatched capture response ${mismatch} cannot select an artifact (${mode})`, async ({page}) => {
    const state = await fixture(page, native, {[mismatch]: true});
    const box = await form(page, true);
    await box.getByRole('button', {name: 'Preview source file', exact: true}).click();
    await box.getByRole('checkbox').check();
    await box.getByRole('button', {name: 'Save reviewed file', exact: true}).click();
    await expect(box).toContainText('Unable to verify the saved attachment revision and capture source.');
    await expect(box.getByRole('button', {name: 'Add to attachment draft', exact: true})).toBeHidden();
    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k=>k.startsWith('batc.capture.'))!)!));
    if (mismatch !== 'badDigest') expect(stored.intent.operation_id).toBeUndefined();
    expect(stored.result).toBeUndefined(); expect(state.errors).toEqual([]);
  });
}
async function reviewed(page: Page, options: any = {}, attachment = false) {
  const state = await fixture(page, false, options);
  const box = await form(page, attachment);
  await box.getByRole('button', {name: 'Preview source file', exact: true}).click();
  await expect(box.locator('[data-capture-evidence]')).toContainText(ref.digest);
  await box.getByRole('checkbox', {name: 'I reviewed this file and its source evidence', exact: true}).check();
  return {state, box};
}
function release(state: any) {
  state.holdOperation = false; state.holdOperationRead = false; state.delayPreview = false;
  state.holdPreview?.(); state.releaseOperation?.(); state.releaseOperationRead?.();
}
const previewButton = (box: ReturnType<Page['locator']>) => box.getByRole('button', {name: 'Preview source file', exact: true});
const saveButton = (box: ReturnType<Page['locator']>) => box.getByRole('button', {name: 'Save reviewed file', exact: true});
const checkButton = (box: ReturnType<Page['locator']>) => box.getByRole('button', {name: 'Check original capture', exact: true});
const anotherButton = (box: ReturnType<Page['locator']>) => box.getByRole('button', {name: 'Capture another file', exact: true});
const attachButton = (box: ReturnType<Page['locator']>) => box.getByRole('button', {name: 'Add to attachment draft', exact: true});
const captureNotice = (box: ReturnType<Page['locator']>) => box.getByRole('status');
test('preview capture keeps keyboard focus while its read is in flight', async ({page}) => {
  const state = await fixture(page, false, {delayPreview: true});
  try {
    const box = await form(page);
    const preview = previewButton(box);
    const notice = captureNotice(box);
    await preview.focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => typeof state.holdPreview).toBe('function');
    await expect(preview).toBeFocused();
    await expect(preview).toHaveAttribute('aria-disabled', 'true');
    await expect.poll(() => preview.evaluate(node => (node as HTMLButtonElement).disabled)).toBe(false);
    await expect(notice).toHaveAttribute('aria-busy', 'true');
    expect(state.previews).toHaveLength(1);
    await page.keyboard.press('Enter');
    await preview.click({force: true});
    expect(state.previews).toHaveLength(1);
    expect(state.writes).toEqual([]);
    state.delayPreview = false; state.holdPreview();
    await expect(preview).toBeFocused();
    await expect(preview).not.toHaveAttribute('aria-disabled', 'true');
    await expect(notice).not.toHaveAttribute('aria-busy', 'true');
    await expect(box.locator('[data-capture-evidence]')).toContainText(ref.digest);
    expect(state.previews).toHaveLength(1);
    expect(state.errors).toEqual([]);
  } finally {release(state);}
});
test('save capture keeps keyboard focus and moves to attach when the result can be added', async ({page}) => {
  const {state, box} = await reviewed(page, {holdOperation: true}, true);
  try {
    const notice = captureNotice(box);
    await saveButton(box).focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => typeof state.releaseOperation).toBe('function');
    const apply = checkButton(box);
    await expect(apply).toBeFocused();
    await expect(apply).toHaveAttribute('aria-disabled', 'true');
    await expect.poll(() => apply.evaluate(node => (node as HTMLButtonElement).disabled)).toBe(false);
    await expect(notice).toHaveAttribute('aria-busy', 'true');
    expect(state.writes).toHaveLength(1);
    await page.keyboard.press('Enter');
    await apply.click({force: true});
    expect(state.writes).toHaveLength(1);
    state.holdOperation = false; state.releaseOperation();
    await expect(attachButton(box)).toBeFocused();
    await expect(apply).toBeHidden();
    await expect(notice).not.toHaveAttribute('aria-busy', 'true');
    expect(state.writes).toHaveLength(1);
    expect(state.errors).toEqual([]);
  } finally {release(state);}
});
test('save capture keeps keyboard focus and moves to capture another file without attach', async ({page}) => {
  const {state, box} = await reviewed(page, {holdOperation: true});
  try {
    const notice = captureNotice(box);
    await saveButton(box).focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => typeof state.releaseOperation).toBe('function');
    const apply = checkButton(box);
    await expect(apply).toBeFocused();
    await expect(apply).toHaveAttribute('aria-disabled', 'true');
    await expect(notice).toHaveAttribute('aria-busy', 'true');
    state.holdOperation = false; state.releaseOperation();
    await expect(anotherButton(box)).toBeFocused();
    await expect(attachButton(box)).toHaveCount(0);
    await expect(apply).toBeHidden();
    expect(state.writes).toHaveLength(1);
    expect(state.errors).toEqual([]);
  } finally {release(state);}
});
test('a refused capture moves focus to capture another file', async ({page}) => {
  const {state, box} = await reviewed(page, {holdOperation: true, operationError: {code: 'PREVIEW_EXPIRED', message: 'Preview expired'}});
  try {
    await saveButton(box).focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => typeof state.releaseOperation).toBe('function');
    const apply = checkButton(box);
    await expect(apply).toBeFocused();
    state.holdOperation = false; state.releaseOperation();
    await expect(anotherButton(box)).toBeFocused();
    await expect.poll(() => apply.evaluate(node => (node as HTMLButtonElement).disabled)).toBe(true);
    await expect(apply).not.toHaveAttribute('aria-disabled', 'true');
    expect(state.writes).toHaveLength(1);
    expect(state.errors).toEqual([]);
  } finally {release(state);}
});
test('a failed capture moves focus to capture another file', async ({page}) => {
  const {state, box} = await reviewed(page, {holdOperation: true, status: 'failed'});
  try {
    await saveButton(box).focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => typeof state.releaseOperation).toBe('function');
    const apply = checkButton(box);
    state.holdOperation = false; state.releaseOperation();
    await expect(anotherButton(box)).toBeFocused();
    await expect(apply).toBeHidden();
    await expect(box).toContainText('failed');
    expect(state.writes).toHaveLength(1);
    expect(state.errors).toEqual([]);
  } finally {release(state);}
});
test('a refresh keeps focus on capture another file and ignores a second press', async ({page}) => {
  const {state, box} = await reviewed(page);
  await saveButton(box).click();
  await expect(box).toContainText(aid);
  const again = anotherButton(box);
  await expect(again).toBeVisible();
  await again.focus();
  state.holdOperationRead = true;
  state.events = [{seq: 1, kind: 'operation.updated', resource_type: 'operation', resource_id: op}];
  try {
    await expect.poll(() => typeof state.releaseOperationRead).toBe('function');
    await expect(again).toBeFocused();
    await expect(again).toHaveAttribute('aria-disabled', 'true');
    await expect.poll(() => again.evaluate(node => (node as HTMLButtonElement).disabled)).toBe(false);
    await expect(captureNotice(box)).toHaveAttribute('aria-busy', 'true');
    const writes = state.writes.length, previews = state.previews.length, reads = state.operationReads;
    await page.keyboard.press('Enter');
    await again.click({force: true});
    expect(state.writes).toHaveLength(writes);
    expect(state.previews).toHaveLength(previews);
    expect(state.operationReads).toBe(reads);
    await expect(again).toBeVisible();
    await expect(box).toContainText(aid);
    state.holdOperationRead = false; state.releaseOperationRead();
    await expect(again).toBeFocused();
    await expect(again).not.toHaveAttribute('aria-disabled', 'true');
    await expect(captureNotice(box)).not.toHaveAttribute('aria-busy', 'true');
    expect(state.writes).toHaveLength(writes);
    expect(state.previews).toHaveLength(previews);
    expect(state.operationReads).toBe(reads);
    expect(state.errors).toEqual([]);
  } finally {release(state);}
});
test('new capture moves focus to preview when the source is still valid', async ({page}) => {
  const {state, box} = await reviewed(page);
  await saveButton(box).click();
  await expect(box).toContainText(aid);
  const again = anotherButton(box);
  await expect(again).toBeVisible();
  await again.focus();
  await page.keyboard.press('Enter');
  await expect(previewButton(box)).toBeFocused();
  await expect(again).toBeHidden();
  expect(state.writes).toHaveLength(1);
  expect(state.errors).toEqual([]);
});
test('new capture moves focus to the host when the source is no longer valid', async ({page}) => {
  const {state, box} = await reviewed(page);
  await saveButton(box).click();
  await expect(box).toContainText(aid);
  const again = anotherButton(box);
  const host = box.getByRole('combobox', {name: 'Source host', exact: true});
  await expect(again).toBeVisible();
  await host.evaluate(node => {(node as HTMLSelectElement).value = '';});
  await again.focus();
  await page.keyboard.press('Enter');
  await expect(host).toBeFocused();
  await expect(previewButton(box)).toBeDisabled();
  await expect(again).toBeHidden();
  expect(state.writes).toHaveLength(1);
  expect(state.errors).toEqual([]);
});
test('shift+tab during a save returns to check and leaves preview disabled', async ({page}) => {
  const {state, box} = await reviewed(page, {holdOperation: true});
  try {
    await saveButton(box).focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => typeof state.releaseOperation).toBe('function');
    const apply = checkButton(box);
    const preview = previewButton(box);
    await expect(apply).toBeFocused();
    await expect(apply).toHaveAttribute('aria-disabled', 'true');
    await expect.poll(() => preview.evaluate(node => (node as HTMLButtonElement).disabled)).toBe(true);
    await expect(preview).not.toHaveAttribute('aria-disabled', 'true');
    await page.keyboard.press('Shift+Tab');
    await page.keyboard.press('Tab');
    await expect(apply).toBeFocused();
    await expect(apply).toHaveAttribute('aria-disabled', 'true');
    expect(state.writes).toHaveLength(1);
    expect(state.errors).toEqual([]);
  } finally {release(state);}
});
test('a running capture poll leaves capture another file disabled', async ({page}) => {
  await page.clock.install();
  const {state, box} = await reviewed(page, {status: 'running'});
  await saveButton(box).click();
  await expect(box.getByRole('link', {name: op, exact: true})).toBeVisible();
  const busyState = () => box.evaluate(root => {
    const button = [...root.querySelectorAll('button')].find(item => item.textContent === 'Capture another file');
    const notice = root.querySelector('[role="status"]');
    return {disabled: Boolean(button && (button as HTMLButtonElement).disabled),
      ariaDisabled: button ? button.getAttribute('aria-disabled') : null,
      ariaBusy: notice ? notice.getAttribute('aria-busy') : null};
  });
  state.holdOperationRead = true;
  try {
    await page.clock.fastForward(1000);
    await expect.poll(() => state.operationReadEpoch).toBe(1);
    await expect.poll(busyState).toEqual({disabled: true, ariaDisabled: null, ariaBusy: null});
    state.operationNote = 'still-running';
    state.releaseOperationRead();
    await expect(box).toContainText('still-running');
    await page.clock.fastForward(1000);
    await expect.poll(() => state.operationReadEpoch).toBe(2);
    await expect.poll(busyState).toEqual({disabled: true, ariaDisabled: null, ariaBusy: null});
    expect(state.writes).toHaveLength(1);
    expect(state.errors).toEqual([]);
  } finally {release(state);}
});
test('observe-only capture preview remains read-only', async ({page}) => {
  const state = await fixture(page, false, {scopes: ['observe']});
  const box = await form(page);
  await box.getByRole('button', {name: 'Preview source file', exact: true}).click();
  await expect(box).toContainText('Saving needs View and Manage access');
  await box.getByRole('checkbox').check();
  await expect(box.getByRole('button', {name: 'Save reviewed file', exact: true})).toBeDisabled();
  expect(state.writes).toEqual([]);
});
for (const locale of ['en-US', 'zh-TW']) for (const width of [390,768,1440]) {
  test(`capture review ${locale} ${width}`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}}), page = await context.newPage();
    const state = await fixture(page, false), box = await form(page, true, locale);
    await box.getByRole('button', {name: locale === 'en-US' ? 'Preview source file' : '預覽來源檔案', exact: true}).click();
    await expect(box.locator('[data-capture-evidence]')).toContainText(ref.digest);
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({path: `test-results/capture-${locale}-${width}.png`, fullPage: true});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(state.errors).toEqual([]); await context.close();
  });
}
