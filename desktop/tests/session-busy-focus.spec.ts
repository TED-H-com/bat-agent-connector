// Keyboard focus stays on a session control while its own request is in flight.
// Buttons use aria-disabled, so Enter and click() still arrive and the handler has to ignore them.
import {test, expect, type Browser, type Page} from '@playwright/test';
import {checkpointHost, consoleNoise, fromRequirements, openSession} from './session-fixture.ts';

const wid = 'wi_' + 'd'.repeat(20);
const cp = 'cp_' + 'e'.repeat(32);
const aid = 'art_' + 'a'.repeat(32);
const uploadId = 'op_' + 'b'.repeat(32);
const startId = 'op_' + 'f'.repeat(32);
const oid = 'op_' + 'c'.repeat(32);
const handoffId = 'op_' + '9'.repeat(32);
const digest = 'ab'.repeat(32);
const ref = {artifact_id: aid, revision: 1, digest};
const caps = {actor: 'fixture-person', scopes: ['observe', 'manage', 'start', 'operate'], api_version: 1,
  contract_version: '2026-10-08', features: {checkpoints: ['demo']}, hosts: [{host: 'demo'}],
  actions: [{action: 'checkpoint.create', allowed: true}, {action: 'checkpoint.continue', allowed: true}],
  artifacts: {limits: {max_file_bytes: 1024 * 1024}}};
const proof = {cursor: 0, token: 'busy-proof'};
const item = {work_item_id: wid, title: 'Ship the notes', version: 1, state: 'todo', goal: '', request: '', acceptance: '',
  steps: [], attachments: [], completion: {display_state: 'todo', fingerprint: 'fixture-fingerprint'}};
const link = {kind: 'checkpoint', ref: cp, linked_by: 'fixture', linked_at: 1,
  target: {found: true, host: 'demo', source_session_id: 'session-1', commit_sha: 'a'.repeat(40), branch: 'main'}};

type Snap = {active: string, ariaDisabled: string | null, ariaBusy: string | null, disabled: boolean | null};

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => {release = resolve;});
  return {promise, release};
}

async function snap(page: Page, name: string, regionSelector: string, root = 'body'): Promise<Snap> {
  return page.evaluate(({name, regionSelector, root}) => {
    const scope = document.querySelector(root) || document.body;
    const button = [...scope.querySelectorAll('button')].find(item => (item.textContent || '').trim() === name) as HTMLButtonElement | undefined;
    const region = document.querySelector(regionSelector);
    const active = document.activeElement;
    return {
      active: !button ? 'missing' : active === button ? 'button' : active === document.body ? 'BODY' : (active?.tagName || ''),
      ariaDisabled: button?.getAttribute('aria-disabled') ?? null,
      ariaBusy: region?.getAttribute('aria-busy') ?? null,
      disabled: button ? button.disabled : null,
    };
  }, {name, regionSelector, root});
}

async function press(page: Page, name: string, root = 'body') {
  const button = page.locator(root).getByRole('button', {name, exact: true});
  await button.focus();
  await page.keyboard.press('Enter');
  return button;
}

test('Send keeps focus while the message is held', async ({browser}) => {
  const s = await openSession(browser);
  const held = gate();
  try {
    s.overrides.unshift(async input => {
      if ((input.body as {action?: string} | undefined)?.action !== 'session.send') return;
      await held.promise;
      return {status: 500, data: {error: {code: 'HELD', message: 'Held send failed'}}};
    });
    await fromRequirements(s.page, s.ids, 'implement');
    const {page} = s;
    const send = page.getByRole('button', {name: 'Send', exact: true});
    await expect(send).toBeEnabled();
    await page.locator('#workspace-message-input textarea').fill('Hold this send');
    await press(page, 'Send', '.composer-row');
    const sent = () => s.writes.filter(row => row.body?.action === 'session.send').length;
    await expect.poll(async () => ({...await snap(page, 'Send', '.session-status', '.composer-row'), posts: sent()})).toEqual({
      active: 'button', ariaDisabled: 'true', ariaBusy: 'true', disabled: false, posts: 1});
    await page.keyboard.press('Enter');
    await send.click({force: true});
    expect(sent()).toBe(1);
    held.release();
    await expect.poll(async () => ({...await snap(page, 'Send', '.session-status', '.composer-row'), posts: sent()})).toEqual({
      active: 'button', ariaDisabled: null, ariaBusy: null, disabled: false, posts: 1});
    expect(s.errors.filter(line => !consoleNoise(line))).toEqual([]);
  } finally {held.release(); await s.context.close();}
});

test('a failed send line cannot start a second send', async ({browser}) => {
  const s = await openSession(browser);
  const held = gate();
  let hold = false;
  try {
    s.overrides.unshift(async input => {
      if ((input.body as {action?: string} | undefined)?.action !== 'session.send') return;
      if (!hold) return {status: 200, data: {operation: {operation_id: 'op_' + '1'.repeat(32), action: 'session.send', status: 'failed'}}};
      await held.promise;
      return {status: 500, data: {error: {code: 'HELD', message: 'Held send failed'}}};
    });
    await fromRequirements(s.page, s.ids, 'implement');
    const {page} = s;
    const send = page.getByRole('button', {name: 'Send', exact: true});
    await expect(send).toBeEnabled();
    await page.locator('#workspace-message-input textarea').fill('First failure, then hold');
    await send.click();
    await expect(page.locator('.session-retry')).toBeVisible();
    hold = true;
    await press(page, 'Send', '.composer-row');
    const sent = () => s.writes.filter(row => row.body?.action === 'session.send').length;
    await expect.poll(async () => ({...await snap(page, 'Send', '.session-status', '.composer-row'), posts: sent()})).toEqual({
      active: 'button', ariaDisabled: 'true', ariaBusy: 'true', disabled: false, posts: 2});
    await page.locator('.session-retry').click({force: true});
    expect(sent()).toBe(2);
    held.release();
    await expect.poll(() => sent()).toBe(2);
    expect(s.errors.filter(line => !consoleNoise(line))).toEqual([]);
  } finally {held.release(); await s.context.close();}
});

test('checkpoint Start agent work keeps focus while it is held', async ({browser}) => {
  const s = await openSession(browser);
  const held = gate();
  try {
    s.overrides.push(checkpointHost(s, s.ids.sessions.manual));
    s.overrides.unshift(async input => {
      if ((input.body as {action?: string} | undefined)?.action !== 'checkpoint.continue') return;
      await held.promise;
      return undefined;
    });
    await fromRequirements(s.page, s.ids, 'manual');
    const {page} = s;
    const section = page.locator('.session-checkpoints');
    await section.getByRole('button', {name: 'Start from here', exact: true}).click();
    await section.locator('[data-checkpoint-form] textarea').fill('Keep going from this checkpoint.');
    const go = await press(page, 'Start agent work', '[data-checkpoint-form]');
    const posts = () => s.writes.filter(row => row.body?.action === 'checkpoint.continue').length;
    await expect.poll(async () => ({...await snap(page, 'Start agent work', '.session-checkpoint-result', '[data-checkpoint-form]'), posts: posts()})).toEqual({
      active: 'button', ariaDisabled: 'true', ariaBusy: 'true', disabled: false, posts: 1});
    await page.keyboard.press('Enter');
    await go.click({force: true});
    expect(posts()).toBe(1);
    held.release();
    await expect.poll(async () => ({...await snap(page, 'Start agent work', '.session-checkpoint-result', '[data-checkpoint-form]'), posts: posts()})).toEqual({
      active: 'button', ariaDisabled: null, ariaBusy: null, disabled: false, posts: 1});
    expect(s.errors.filter(line => !consoleNoise(line))).toEqual([]);
  } finally {held.release(); await s.context.close();}
});

test('Record checkpoint keeps focus while it is held', async ({browser}) => {
  const s = await openSession(browser);
  const held = gate();
  try {
    s.overrides.push(checkpointHost(s, s.ids.sessions.manual));
    s.overrides.unshift(async input => {
      if ((input.body as {action?: string} | undefined)?.action !== 'checkpoint.create') return;
      await held.promise;
      return undefined;
    });
    await fromRequirements(s.page, s.ids, 'manual');
    const {page} = s;
    const section = page.locator('.session-section').filter({has: page.locator('.session-checkpoints')});
    await section.getByRole('button', {name: 'Record this version', exact: true}).click();
    await expect(section.getByRole('combobox', {name: 'Commit'})).toBeVisible();
    const record = await press(page, 'Record', '.session-checkpoint-record');
    const posts = () => s.writes.filter(row => row.body?.action === 'checkpoint.create').length;
    await expect.poll(async () => ({...await snap(page, 'Record', '[data-checkpoint-status]', '.session-checkpoint-record'), posts: posts()})).toEqual({
      active: 'button', ariaDisabled: 'true', ariaBusy: 'true', disabled: false, posts: 1});
    await page.keyboard.press('Enter');
    await record.click({force: true});
    expect(posts()).toBe(1);
    held.release();
    await expect.poll(async () => ({...await snap(page, 'Record', '[data-checkpoint-status]', '.session-checkpoint-record'), posts: posts()})).toEqual({
      active: 'button', ariaDisabled: null, ariaBusy: null, disabled: false, posts: 1});
    expect(s.errors.filter(line => !consoleNoise(line))).toEqual([]);
  } finally {held.release(); await s.context.close();}
});

async function operationPage(browser: Browser, respond: (input: any, url: URL) => any) {
  const errors: string[] = [];
  const context = await browser.newContext({viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
  const operation = {operation_id: oid, action: 'integration.apply', status: 'needs_attention', error_code: 'INTEGRATION_CONFLICT',
    target: {host: 'demo'}, steps: [], actor: 'fixture', entry: 'user', created_at: 1};
  const identity = {...caps, scopes: ['observe', 'operate', 'start']};
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url());
    const input = {method: request.method(), path: url.pathname.slice('/api/v1'.length) + url.search, body: request.postDataJSON()};
    const extra = await respond(input, url);
    if (extra) return route.fulfill({status: extra.status, json: extra.data});
    const path = url.pathname.slice('/api/v1'.length);
    const data = path === '/capabilities' ? identity : path === '/bootstrap' ? {capabilities: identity,
      sync: {version: 1, server_id: 'busy-server', principal_id: 'fixture-person', checkpoint: proof}}
      : path === '/events' ? {events: [], next_cursor: 0, head_cursor: 0, sync: {checkpoint: proof}}
      : path === '/operations/' + oid ? {operation} : path === '/integrations/' + oid ? {receipts: []}
      : {sessions: [], work_items: [], operations: [], hosts: [], messages: [], checkpoints: []};
    return route.fulfill({status: 200, json: data});
  });
  return {context, page, errors};
}

test('handoff Start agent work keeps focus while it is held', async ({browser}) => {
  const held = gate();
  let posts = 0;
  const harness = await operationPage(browser, async input => {
    if (input.method === 'POST' && input.body?.action === 'integration.handoff') {
      posts += 1;
      await held.promise;
      return {status: 200, data: {operation: {operation_id: handoffId, action: 'integration.handoff', status: 'accepted'}}};
    }
  });
  try {
    const {page} = harness;
    await page.goto('/dashboard/#/op/' + oid);
    await page.getByRole('button', {name: 'Ask an agent to resolve', exact: true}).click();
    const go = await press(page, 'Start agent work');
    await expect.poll(async () => ({...await snap(page, 'Start agent work', '[data-handoff-status]'), posts})).toEqual({
      active: 'button', ariaDisabled: 'true', ariaBusy: 'true', disabled: false, posts: 1});
    await page.keyboard.press('Enter');
    await go.click({force: true});
    expect(posts).toBe(1);
    held.release();
    await expect.poll(async () => ({...await snap(page, 'Start agent work', '[data-handoff-status]'), posts})).toEqual({
      active: 'button', ariaDisabled: null, ariaBusy: null, disabled: false, posts: 1});
    expect(harness.errors).toEqual([]);
  } finally {held.release(); await harness.context.close();}
});

async function itemPage(browser: Browser, respond: (input: any, url: URL, state: {uploads: number, sourceReads: number, catalog: number}) => any) {
  const errors: string[] = [];
  const state = {uploads: 0, sourceReads: 0, catalog: 0};
  const context = await browser.newContext({viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url());
    const input = {method: request.method(), path: url.pathname.slice('/api/v1'.length) + url.search,
      body: request.postDataJSON(), bytes: url.pathname.endsWith('/content') ? [...request.postDataBuffer()!] : undefined};
    const extra = await respond(input, url, state);
    if (extra) return route.fulfill({status: extra.status, json: extra.data});
    const path = url.pathname.slice('/api/v1'.length);
    if (path === '/checkpoints/' + cp) {return route.fulfill({status: 200, json: {checkpoint: {host: 'demo', checkpoint_id: cp}, source: {head: 'a'.repeat(40)}}});}
    const data = path === '/capabilities' ? caps : path === '/bootstrap' ? {capabilities: caps,
      sync: {version: 1, server_id: 'busy-server', principal_id: 'fixture-person', checkpoint: proof}}
      : path === '/events' ? {events: [], next_cursor: 0, head_cursor: 0, sync: {checkpoint: proof}}
      : path === '/work-items/' + wid ? {work_item: item, project: {project_id: 'prj_fixture', name: 'Fixture', archived: false},
        links: [link], path: [], children: [], derived: [], events: []}
      : path === '/artifacts' ? {artifacts: [], next_cursor: null}
      : {messages: [], sessions: [], operations: [], hosts: [], work_items: [], checkpoints: []};
    return route.fulfill({status: 200, json: data});
  });
  await page.goto('/dashboard/#/item/' + wid);
  return {context, page, errors, state};
}

test('work item Start agent work moves focus to the operation', async ({browser}) => {
  const held = gate();
  let posts = 0;
  const harness = await itemPage(browser, async (input, url, state) => {
    if (url.pathname.slice('/api/v1'.length) === '/checkpoints/' + cp && url.searchParams.get('live') === 'true') state.sourceReads += 1;
    if (input.method === 'POST' && input.body?.action === 'checkpoint.continue') {
      posts += 1;
      await held.promise;
      return {status: 200, data: {operation: {operation_id: startId, action: 'checkpoint.continue', status: 'succeeded'}}};
    }
  });
  try {
    const {page, state} = harness;
    const live = page.waitForResponse(response => response.url().includes('/checkpoints/') && response.url().includes('live=true'));
    await page.getByRole('button', {name: 'Start agent work from this checkpoint', exact: true}).click();
    await live;
    await expect.poll(() => state.sourceReads).toBe(1);
    await page.locator('.drawer:not([hidden]) textarea').fill('Ship the notes from this checkpoint.');
    const go = await press(page, 'Start agent work');
    await expect.poll(async () => ({...await snap(page, 'Start agent work', '[data-work-start-status]'), posts})).toEqual({
      active: 'button', ariaDisabled: 'true', ariaBusy: 'true', disabled: false, posts: 1});
    await page.keyboard.press('Enter');
    await go.click({force: true});
    expect(posts).toBe(1);
    held.release();
    await expect.poll(async () => page.evaluate(id => {
      const button = [...document.querySelectorAll('button')].find(item => (item.textContent || '').trim() === 'Start agent work') as HTMLButtonElement | undefined;
      const region = document.querySelector('[data-work-start-status]');
      const active = document.activeElement;
      return {
        href: active instanceof HTMLAnchorElement ? active.getAttribute('href') : '',
        ariaDisabled: button?.getAttribute('aria-disabled') ?? null,
        ariaBusy: region?.getAttribute('aria-busy') ?? null,
        disabled: button ? button.disabled : null,
      };
    }, startId)).toEqual({href: '#/op/' + startId, ariaDisabled: null, ariaBusy: null, disabled: true});
    expect(posts).toBe(1);
    expect(harness.errors).toEqual([]);
  } finally {held.release(); await harness.context.close();}
});

test('attachment Retry keeps focus, and Remove waits until the upload finishes', async ({browser}) => {
  const held = gate();
  const harness = await itemPage(browser, async (input, _url, state) => {
    if (input.method === 'POST' && input.body?.action === 'artifact.upload') {
      state.uploads += 1;
      if (state.uploads === 1) return {status: 500, data: {error: {code: 'UPLOAD_FAILED', message: 'Upload failed'}}};
      await held.promise;
      return {status: 200, data: {operation: {operation_id: uploadId, status: 'succeeded', result: ref}}};
    }
  });
  try {
    const {page, state} = harness;
    await page.getByRole('button', {name: 'More', exact: true}).click();
    const drawer = page.locator('.drawer:not([hidden])');
    await drawer.locator('input[type="file"]').setInputFiles({name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('hello')});
    const row = drawer.locator('.attachment-row', {hasText: 'notes.txt'});
    const retry = row.getByRole('button', {name: 'Retry', exact: true});
    await expect.poll(() => state.uploads).toBe(1);
    await expect(retry).toBeVisible();
    // The first upload hides Retry until it fails. Wait until that failure has put an enabled Retry back.
    await expect.poll(() => retry.getAttribute('aria-disabled')).toBeNull();
    await retry.focus();
    await page.keyboard.press('Enter');
    await expect.poll(async () => ({...await snap(page, 'Retry', '.drawer:not([hidden]) .attachments [role=status]', '.drawer:not([hidden])'), uploads: state.uploads})).toEqual({
      active: 'button', ariaDisabled: 'true', ariaBusy: 'true', disabled: false, uploads: 2});
    await row.getByRole('button', {name: 'Remove', exact: true}).click({force: true});
    await expect(row).toContainText('notes.txt');
    expect(state.uploads).toBe(2);
    held.release();
    await expect.poll(async () => page.evaluate(() => {
      const active = document.activeElement as HTMLElement | null;
      const control = active?.getAttribute('data-attachment-control') || '';
      return {
        landed: control === 'role' || control === 'remove',
        inRow: Boolean(active?.closest('.attachment-row')?.textContent?.includes('notes.txt')),
        retries: [...document.querySelectorAll('.attachment-row button')].filter(item => (item.textContent || '').trim() === 'Retry').length,
      };
    })).toEqual({landed: true, inRow: true, retries: 0});
    expect(state.uploads).toBe(2);
    expect(harness.errors).toEqual([]);
  } finally {held.release(); await harness.context.close();}
});

test('a file\'s first upload shows no Retry', async ({browser}) => {
  const held = gate();
  const harness = await itemPage(browser, async (input, _url, state) => {
    if (input.method === 'POST' && input.body?.action === 'artifact.upload') {
      state.uploads += 1;
      await held.promise;
      return {status: 200, data: {operation: {operation_id: uploadId, status: 'succeeded', result: ref}}};
    }
  });
  try {
    const {page, state} = harness;
    await page.getByRole('button', {name: 'More', exact: true}).click();
    const drawer = page.locator('.drawer:not([hidden])');
    await drawer.locator('input[type="file"]').setInputFiles({name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('hello')});
    const row = drawer.locator('.attachment-row', {hasText: 'notes.txt'});
    await expect.poll(() => state.uploads).toBe(1);
    await expect.poll(() => row.evaluate(node => {
      const buttons = [...node.querySelectorAll('button')];
      const remove = buttons.find(item => (item.textContent || '').trim() === 'Remove') as HTMLButtonElement | undefined;
      return {
        retries: buttons.filter(item => (item.textContent || '').trim() === 'Retry').length,
        removeAriaDisabled: remove?.getAttribute('aria-disabled') ?? null,
      };
    })).toEqual({retries: 0, removeAriaDisabled: 'true'});
    held.release();
    await expect(row).toContainText(aid);
    expect(state.uploads).toBe(1);
    expect(harness.errors).toEqual([]);
  } finally {held.release(); await harness.context.close();}
});

test('attachment More keeps focus while the next page is held', async ({browser}) => {
  const held = gate();
  const ready = {revision: {...ref, state: 'ready', display_name: 'Existing fixture'}};
  const harness = await itemPage(browser, async (_input, url, state) => {
    if (url.pathname.slice('/api/v1'.length) !== '/artifacts') return;
    if (!url.searchParams.get('cursor')) return {status: 200, data: {artifacts: [ready], next_cursor: 'page-2'}};
    state.catalog += 1;
    await held.promise;
    return {status: 200, data: {artifacts: [], next_cursor: 'page-3'}};
  });
  try {
    const {page, state} = harness;
    await page.getByRole('button', {name: 'More', exact: true}).click();
    const more = page.locator('.drawer:not([hidden]) .attachments').getByRole('button', {name: 'More', exact: true});
    await expect(more).toBeVisible();
    await more.focus();
    await page.keyboard.press('Enter');
    await expect.poll(async () => ({...await snap(page, 'More', '.drawer:not([hidden]) .attachments [role=status]', '.drawer:not([hidden])'), catalog: state.catalog})).toEqual({
      active: 'button', ariaDisabled: 'true', ariaBusy: 'true', disabled: false, catalog: 1});
    await page.keyboard.press('Enter');
    await more.click({force: true});
    expect(state.catalog).toBe(1);
    held.release();
    await expect.poll(async () => ({...await snap(page, 'More', '.drawer:not([hidden]) .attachments [role=status]', '.drawer:not([hidden])'), catalog: state.catalog})).toEqual({
      active: 'button', ariaDisabled: null, ariaBusy: null, disabled: false, catalog: 1});
    expect(harness.errors).toEqual([]);
  } finally {held.release(); await harness.context.close();}
});
