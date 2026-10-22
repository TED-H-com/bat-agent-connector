import {managedStatusFixture} from './managed-status-fixture.ts';
import {test, expect, type Page} from '@playwright/test';
import {expectErrorDetails, expectClientError} from './api-error-assertions.ts';
import {mkdir, readFile} from 'node:fs/promises';
import {STRINGS} from '../src/i18n.js';

for (const locale of ['en-US', 'zh-TW']) test(`successful polling preserves a refused native replacement message ${locale}`, async ({browser}) => {
  const context = await browser.newContext({locale}), page = await context.newPage();
  const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
  try {
    await setup(page, {available: true, saved: true, fail: true});
    await page.getByRole('button', {name: copy.desktop_replace_credential, exact: true}).click();
    await expectClientError(page, 'Fixture identity mismatch; previous credential retained');
    const polls = () => page.evaluate(() => (window as any).__credentialFixture.calls
      .filter((call: any) => call.command === 'connector_request' && call.args.input.path.startsWith('/events')).length);
    const before = await polls();
    await expect.poll(polls).toBeGreaterThan(before);
    await page.evaluate(() => {location.hash = '#/home';});
    await expect(page.locator('.native-connection')).toHaveCount(0);
    await page.evaluate(() => {location.hash = '#/settings';});
    await expectClientError(page, 'Fixture identity mismatch; previous credential retained');
    await expectConnectedIdentity(page);
  } finally {await context.close();}
});

async function expectConnectedIdentity(page: Page) {
  const access = page.locator('[data-connection-access]');
  await expect(access).toBeVisible();
  await expect(access).toHaveText(/View · Operate|檢視 · 操作/);
  await expect(access.locator('xpath=preceding-sibling::dt[1]')).toHaveText(/Access|權限/);
  await expect(access).not.toContainText('fixture-operator');
  await expect(page.locator('.native-connection > details code').filter({hasText: 'fixture-operator'})).toHaveText('fixture-operator');
  await expect(page.locator('.native-connection > details code').filter({hasText: 'observe, operate'})).toHaveText('observe, operate');
}

for (const locale of ['en-US', 'zh-TW']) for (const width of [1440, 390]) {
  test(`Connector connection wording ${locale} ${width}`, async ({browser}, testInfo) => {
    const context = await browser.newContext({locale, colorScheme: 'light', viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage(), errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await setup(page, {setup: true});
    const zh = locale === 'zh-TW', connection = page.getByRole('region', {name: zh ? '桌面 Connector 連線' : 'Desktop Connector connection', exact: true});
    await expect(connection.getByLabel(zh ? 'Connector 位址' : 'Connector address', {exact: true})).toBeVisible();
    await expect(connection).toContainText(zh
      ? 'Connector 位址與預期身分由本機設定指定；憑證保留在原生程式，不存入此畫面。'
      : 'The Connector address and expected identity come from local configuration. Credentials stay in the native app, outside this page.');
    expect(await connection.innerText()).not.toMatch(/\bcentral\b|中央/i);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const folder = process.env.POLISH_COPY_SCREENSHOTS || testInfo.outputPath('screenshots');
    await mkdir(folder, {recursive: true});
    await page.screenshot({path: `${folder}/connection-${locale}-${width}.png`, fullPage: true});
    expect(await page.evaluate(() => (window as any).__credentialFixture.calls.filter((call: any) =>
      ['connector_setup_configuration', 'connector_enroll', 'connector_connect'].includes(call.command)))).toEqual([]);
    expect(errors).toEqual([]);
    await context.close();
  });
}

for (const locale of ['en-US', 'zh-TW']) test(`newer journal startup refusal ${locale}`, async ({browser}) => {
  const context = await browser.newContext({locale, viewport: {width: 390, height: 844}});
  const page = await context.newPage();
  const diagnostic = 'JOURNAL_NEWER_DATA_STEP: journal records data step 4; this Connector supports up to 3; reopen with the newer Connector';
  await setup(page, {setup: true, configurationSource: 'managed', startupError: diagnostic});
  const error = page.locator('.native-connection [data-api-error]');
  await expect(error.locator('[data-error-summary]')).toHaveText(locale === 'zh-TW'
    ? '這份紀錄需要較新的 Connector。請使用較新的 Connector 重新開啟，既有資料已保留。'
    : 'These records need a newer Connector. Reopen with the newer Connector; existing data is preserved.');
  await error.locator('summary').click();
  await expect(error.locator('[data-error-details]')).toContainText(diagnostic);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await context.close();
});

async function setup(page: any, options: any = {}) {
  await page.addInitScript((options: any) => {
    const caps = (principal: string) => ({actor: 'fixture-operator', scopes: ['observe', 'operate'], api_version: 1,
      contract_version: '2026-10-08', hosts: [], features: {}, actions: [], desktop_identity: {server_id: 'fixture-server', principal_id: principal}});
    const fixture = {available: false, saved: false, supported: true, calls: [] as any[], principal: 'original-principal',
      connected: false, cancel: false, fail: false, delay: false, release: null as any, wrongBootstrap: false, ...options};
    Object.assign(window, {__credentialFixture: fixture, isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      fixture.calls.push({command, args: args ?? null});
      if (command === 'native_status') return {endpoint: fixture.setup ? null : fixture.endpoint ?? 'https://central.example/', expected_actor: fixture.setup ? null : 'fixture-operator',
        configuration_setup: !!fixture.setup, configuration_source: fixture.configurationSource, error: fixture.startupError,
        credential_available: fixture.available, credential_saved: fixture.saved, enrollment_supported: fixture.supported,
        configuration_reload: true, configuration_file: fixture.configPath ?? 'C:\\Users\\fixture\\AppData\\Roaming\\io.betteragent.dashboard\\central.json',
        credential_source: fixture.available ? fixture.saved ? fixture.store ?? 'windows_credential_manager' : 'launch_environment' : null,
        connected: fixture.connected};
      if (command === 'managed_control' && args.input.action === 'status') return fixture.managedStatus;
      if (command === 'connector_connect') {if (fixture.delayConnect) await new Promise(resolve => {fixture.releaseConnect = resolve;}); if (!fixture.available) throw new Error('Fixture credential unavailable'); fixture.connected = true; return caps(fixture.principal);}
      if (command === 'connector_setup_configuration') {
        if (fixture.cancel) return null;
        if (fixture.fail) throw new Error('Fixture configuration appeared during review');
        fixture.setup = false; fixture.endpoint = args.config.endpoint + '/'; return {endpoint: fixture.endpoint};
      }
      if (command === 'connector_enroll') {
        if (fixture.delay) await new Promise(resolve => {fixture.release = resolve;});
        if (fixture.cancel) return null;
        if (fixture.fail) throw new Error('Fixture identity mismatch; previous credential retained');
        fixture.principal = fixture.nextPrincipal ?? 'replacement-principal'; fixture.available = true; fixture.saved = true; fixture.connected = true;
        return caps(fixture.principal);
      }
      if (command === 'connector_disconnect') {fixture.connected = false; return;}
      if (command === 'connector_reload_configuration') {fixture.available = false; fixture.connected = false; fixture.endpoint = 'https://replacement.example/'; return {};}
      if (command === 'connector_forget_credential') {fixture.available = false; fixture.saved = false; fixture.connected = false; return;}
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      if (command === 'connector_request') {
        const path = args.input.path;
        if (args.input.method === 'POST') {
          if (fixture.loseReply) {fixture.loseReply = false; throw new Error('Fixture lost operation reply');}
          return {status: 202, data: {operation: {operation_id: 'op_' + 'a'.repeat(32), status: 'accepted'}}};
        }
        if (path === '/sessions/h1/fixture-session') return {status: 200, data: {session: {host: 'h1', session_id: 'fixture-session',
          title: 'Fixture session', api_access: 'managed', provenance: 'connector', streaming: false}}};
        if (path.includes('/messages')) return {status: 200, data: {messages: []}};
        if (path.includes('/history')) return {status: 200, data: {events: [], next_cursor: null}};
        if (path.includes('/relations')) return {status: 200, data: {relations: [], next_cursor: null}};
        if (path.startsWith('/checkpoints')) return {status: 200, data: {checkpoints: []}};
        const checkpoint = {cursor: 0, token: 'synthetic-checkpoint'};
        if (path === '/bootstrap' && fixture.missingBootstrap) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Fixture bootstrap unavailable'}}};
        if (path === '/bootstrap') return {status: 200, data: {capabilities: caps(fixture.principal), sync: {version: 1,
          server_id: 'fixture-server', principal_id: fixture.wrongBootstrap ? 'wrong-principal' : fixture.principal, checkpoint}}};
        if (path.startsWith('/events')) return {status: 200, data: {events: [], next_cursor: 0, head_cursor: 0, sync: {checkpoint}}};
        return {status: 200, data: {sessions: [], hosts: [], projects: [], work_items: [], operations: []}};
      }
      throw new Error('Unexpected native command ' + command);
    }}});
  }, {...options, managedStatus: managedStatusFixture(options.configurationSource === 'managed')});
  await page.goto('/dashboard/#/settings');
  await expect(page.getByRole('region', {name: /Desktop Connector connection|桌面 Connector 連線/})).toBeVisible();
}

test('managed startup failure remains visible beside advanced external setup', async ({page}) => {
  await setup(page, {setup: true, configurationSource: 'managed', startupError: 'MANAGED_RUNTIME_START_FAILED'});
  await expect(page.getByRole('heading', {name: 'Local background service is not ready'})).toBeVisible();
  await expectErrorDetails(page, 'MANAGED_RUNTIME_START_FAILED');
  await expect(page.locator('[data-native-setup] input')).toHaveCount(2);
  await expect(page.locator('[data-native-setup] input').first()).toBeHidden();
  await page.locator('[data-native-setup] details > summary').filter({hasText: 'Advanced'}).click();
  await expect(page.locator('[data-native-setup] input').first()).toBeVisible();
  const calls = await page.evaluate(() => (window as any).__credentialFixture.calls);
  expect(calls.some((call: any) => ['connector_setup_configuration', 'connector_enroll', 'connector_connect'].includes(call.command))).toBe(false);
});

for (const locale of ['en','zh-TW']) test(`native status error keeps diagnostics behind a localized summary ${locale}`,async({page})=>{
  await page.addInitScript(locale=>Object.defineProperty(navigator,'language',{value:locale}),locale);
  await setup(page,{startupError:'FIXTURE_NATIVE_START_FAILED'});
  const error=page.locator('[data-api-error]');
  await expect(error.locator('[data-error-summary]')).toHaveText(locale==='en'
    ? 'The local Connector connection is unavailable. Check its settings.'
    : '無法使用本機 Connector 連線，請檢查設定。');
  expect(await error.innerText()).not.toContain('FIXTURE_NATIVE_START_FAILED');
  await error.locator('summary').click();await expect(error.locator('[data-error-details]')).toContainText('FIXTURE_NATIVE_START_FAILED');
  await expect(error.locator('code')).toHaveCount(0);
  expect((await page.evaluate(()=>(window as any).__credentialFixture.calls)).some((call:any)=>call.command==='connector_connect')).toBe(false);
});

for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) {
  test(`first connection preserves native-reviewed settings ${locale} ${width}`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}});
    const page = await context.newPage(); const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
    if (process.env.SETUP_BASELINE_ASSET) await page.route('**/dashboard/app.js', async route => route.fulfill({
      contentType: 'application/javascript', body: await readFile(process.env.SETUP_BASELINE_ASSET!, 'utf8')}));
    await setup(page, {setup: true, cancel: true});
    const shots = process.env.SETUP_SCREENSHOTS || 'test-results/first-connection';
    await mkdir(shots, {recursive: true});
    await page.screenshot({path: `${shots}/initial-${locale}-${width}.png`, fullPage: true});
    if (process.env.SETUP_BASELINE_ASSET) {await context.close(); return;}
    const form = page.locator('[data-native-setup]');
    await expect(form).toBeVisible();
    await form.locator('input').nth(0).fill('https://first.example');
    await form.locator('input').nth(1).fill('fixture-operator');
    await form.getByRole('button').click();
    await expect(page.getByText(locale === 'en-US' ? 'Saving cancelled; your inputs are preserved.' : '已取消儲存；輸入內容仍保留。')).toBeVisible();
    await expect(form.locator('input').nth(0)).toHaveValue('https://first.example');
    await expect(form.locator('input').nth(1)).toHaveValue('fixture-operator');
    await page.evaluate(() => {const f = (window as any).__credentialFixture; f.cancel = false; f.fail = true;});
    await form.getByRole('button').click();
    await expectClientError(page, 'Fixture configuration appeared during review');
    await expect(form.locator('input').nth(0)).toHaveValue('https://first.example');
    await page.screenshot({path: `${shots}/refused-${locale}-${width}.png`, fullPage: true});
    await page.evaluate(() => {(window as any).__credentialFixture.fail = false;});
    await form.getByRole('button').click();
    await expect(form).toBeHidden();
    await expect(page.getByRole('button', {name: locale === 'en-US' ? 'Connect' : '連線', exact: true})).toBeDisabled();
    const calls = await page.evaluate(() => (window as any).__credentialFixture.calls);
    expect(calls.filter((c: any) => c.command === 'connector_setup_configuration')).toEqual(Array(3).fill({
      command: 'connector_setup_configuration', args: {locale, config: {endpoint: 'https://first.example', expected_actor: 'fixture-operator', contract_version: '2026-10-08'}}}));
    expect(calls.some((c: any) => ['connector_connect', 'connector_enroll', 'connector_request'].includes(c.command))).toBe(false);
    await page.getByRole('button', {name: locale === 'en-US' ? 'Add credential' : '新增憑證', exact: true}).click();
    await expectConnectedIdentity(page);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(errors).toEqual([]); await context.close();
  });
}

for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) {
  test(`native credential recovery fits ${locale} ${width}`, async ({browser}, testInfo) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}});
    const page = await context.newPage(); const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    await setup(page);
    const add = page.getByRole('button', {name: locale === 'en-US' ? 'Add credential' : '新增憑證', exact: true});
    await expect(add).toBeEnabled();
    await expect(page.locator('input[type=password]')).toHaveCount(0);
    await expect(page.getByRole('button', {name: locale === 'en-US' ? 'Connect' : '連線', exact: true})).toBeDisabled();
    expect(await page.evaluate(() => (window as any).__credentialFixture.calls.filter((c: any) => c.command === 'connector_connect'))).toEqual([]);
    await page.screenshot({path: testInfo.outputPath(`settings-${locale}-${width}.png`), fullPage: true});
    await add.click();
    await expectConnectedIdentity(page);
    expect(await page.evaluate(() => (window as any).__credentialFixture.calls.filter((c: any) => c.command === 'connector_enroll')))
      .toEqual([{command: 'connector_enroll', args: {locale}}]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.evaluate(() => [...Object.keys(localStorage), ...Object.keys(sessionStorage)].some(k => k === 'batc.dashboard.token'))).toBe(false);
    expect(errors).toEqual([]); await context.close();
  });
}

test('cancelled or refused replacement preserves the current account and saved drafts', async ({page}) => {
  await setup(page, {available: true, saved: true, cancel: true});
  await page.evaluate(() => localStorage.setItem('batc.draft.synthetic-original', JSON.stringify({key: 'original-key', operation_id: 'op_original', text: 'keep this'})));
  await page.getByRole('button', {name: 'Replace credential', exact: true}).click();
  await expect(page.getByText('Credential entry cancelled. The existing connection is unchanged.')).toBeVisible();
  await expectConnectedIdentity(page);
  await page.evaluate(() => {const f = (window as any).__credentialFixture; f.cancel = false; f.fail = true;});
  await page.getByRole('button', {name: 'Replace credential', exact: true}).click();
  await expectClientError(page, 'Fixture identity mismatch; previous credential retained');
  await expectConnectedIdentity(page);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('batc.draft.synthetic-original')!)))
    .toEqual({key: 'original-key', operation_id: 'op_original', text: 'keep this'});
  expect(await page.evaluate(() => (window as any).__credentialFixture.calls.filter((c: any) => c.command === 'connector_disconnect'))).toEqual([]);
});

test('replacement isolates checkpoint namespace and never replays prior-account writes', async ({page}) => {
  await setup(page, {available: true, saved: true});
  await expectConnectedIdentity(page);
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('batc.sync.')).length)).toBe(1);
  const original = await page.evaluate(() => Object.fromEntries(Object.entries(localStorage)));
  await page.getByRole('button', {name: 'Replace credential', exact: true}).click();
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('batc.sync.')).length)).toBe(2);
  for (const [key, value] of Object.entries(original)) expect(await page.evaluate(k => localStorage.getItem(k), key)).toBe(value);
  expect(await page.evaluate(() => (window as any).__credentialFixture.calls.some((c: any) => c.command === 'connector_request' && c.args.input.method === 'POST'))).toBe(false);
});

test('identity changes between native verification and mounted bootstrap leave the client disconnected', async ({page}) => {
  await setup(page, {wrongBootstrap: true});
  await page.getByRole('button', {name: 'Add credential', exact: true}).click();
  await expectClientError(page, 'Invalid central bootstrap identity');
  await expect(page.locator('[data-connection-access]')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__credentialFixture.connected)).toBe(false);
  expect(await page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('batc.sync.')))).toEqual([]);
});

test('configuration reload disconnects and waits for explicit verification without clearing drafts', async ({page}) => {
  await setup(page, {available: true});
  await page.evaluate(() => localStorage.setItem('batc.draft.original', 'fixed-intent'));
  await page.getByRole('button', {name: 'Reload configuration', exact: true}).click();
  await expect(page.getByText('https://replacement.example/', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Connect', exact: true})).toBeDisabled();
  await expect(page.locator('[data-connection-access]')).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem('batc.draft.original'))).toBe('fixed-intent');
  expect(await page.evaluate(() => (window as any).__credentialFixture.calls.filter((c: any) => c.command === 'connector_reload_configuration')))
    .toEqual([{command: 'connector_reload_configuration', args: {}}]);
});

for (const kind of ['connect', 'enroll']) {
  test(`disconnect stays available during manual ${kind} and ignores a late successful response`, async ({page}) => {
    await setup(page, {available: true, saved: true});
    await expectConnectedIdentity(page);
    await page.evaluate(kind => {
      const fixture = (window as any).__credentialFixture;
      if (kind === 'connect') fixture.delayConnect = true; else fixture.delay = true;
    }, kind);
    await page.getByRole('button', {name: kind === 'connect' ? 'Connect' : 'Replace credential', exact: true}).click();
    await expect.poll(() => page.evaluate(kind => typeof (window as any).__credentialFixture[kind === 'connect' ? 'releaseConnect' : 'release'], kind)).toBe('function');
    const disconnect = page.getByRole('button', {name: 'Disconnect', exact: true});
    await expect(disconnect).toBeEnabled(); // no navigation or rerender is needed to cancel
    await disconnect.click();
    await expect(page.getByText('Disconnected · Connector work continues')).toBeVisible();
    await expect(page.locator('[data-connection-access]')).toHaveCount(0);
    await page.evaluate(kind => (window as any).__credentialFixture[kind === 'connect' ? 'releaseConnect' : 'release'](), kind);
    await expect(page.getByText('Disconnected · Connector work continues')).toBeVisible();
    await expect(page.locator('[data-connection-access]')).toHaveCount(0);
    expect(await page.evaluate(() => (window as any).__credentialFixture.calls.filter((c: any) => c.command === 'connector_request' && c.args.input.path === '/bootstrap').length)).toBe(1);
    expect(await page.evaluate(() => (window as any).__credentialFixture.calls.filter((c: any) => c.command === 'connector_disconnect').length)).toBe(1);
  });
}

for (const entry of ['startup', 'connect', 'enroll']) {
  test(`native ${entry} refuses missing mounted bootstrap and recovers without a legacy namespace`, async ({page}) => {
    await setup(page, {available: entry !== 'enroll', saved: entry !== 'enroll', missingBootstrap: entry === 'startup'});
    let original: Record<string, string> = {};
    if (entry === 'connect') {
      await expectConnectedIdentity(page);
      await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('batc.sync.')).length)).toBe(1);
      original = await page.evaluate(() => Object.fromEntries(Object.entries(localStorage)));
    }
    if (entry !== 'startup') {
      await page.evaluate(() => {(window as any).__credentialFixture.missingBootstrap = true;});
      await page.getByRole('button', {name: entry === 'connect' ? 'Connect' : 'Add credential', exact: true}).click();
    }
    await expectErrorDetails(page, 'Fixture bootstrap unavailable');
    await expect(page.locator('[data-connection-access]')).toHaveCount(0);
    expect(await page.evaluate(() => (window as any).__credentialFixture.connected)).toBe(false);
    expect(await page.evaluate(() => (window as any).__credentialFixture.calls.filter((c: any) => c.command === 'connector_disconnect').length)).toBe(1);
    expect(await page.evaluate(() => Object.keys(localStorage).some(k => k.includes(':legacy:')))).toBe(false);
    expect(await page.evaluate(() => (window as any).__credentialFixture.calls.some((c: any) => c.command === 'connector_request' && c.args.input.method === 'POST'))).toBe(false);
    for (const [key, value] of Object.entries(original)) expect(await page.evaluate(k => localStorage.getItem(k), key)).toBe(value);
    await page.evaluate(() => {(window as any).__credentialFixture.missingBootstrap = false;});
    await page.getByRole('button', {name: 'Connect', exact: true}).click();
    await expect(page).toHaveURL(/#\/home$/);
    await page.locator('#live').click();
    await expectConnectedIdentity(page);
    await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('batc.sync.')).length)).toBe(1);
    expect(await page.evaluate(() => Object.keys(localStorage).some(k => k.includes(':legacy:')))).toBe(false);
  });
}

test('forget removes only the local credential and never calls a central mutation', async ({page}) => {
  await setup(page, {available: true, saved: true});
  await page.getByRole('button', {name: 'Forget saved credential', exact: true}).click();
  await expect(page.getByText('Saved credential removed and disconnected.')).toBeVisible();
  await expect(page.getByRole('button', {name: 'Connect', exact: true})).toBeDisabled();
  await expect(page.getByRole('button', {name: 'Add credential', exact: true})).toBeEnabled();
  expect(await page.evaluate(() => (window as any).__credentialFixture.calls.some((c: any) => c.command === 'connector_request' && c.args.input.method === 'POST'))).toBe(false);
});

test('unsupported platforms retain honest memory-only connection recovery', async ({page}) => {
  await setup(page, {supported: false, available: true});
  await expect(page.getByText('Memory-only launch credential', {exact: true})).toBeVisible();
  await expect(page.getByText('Protected storage and credential entry are not available on this platform yet.', {exact: false})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Add credential', exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: 'Disconnect', exact: true}).click();
  await page.getByRole('button', {name: 'Connect', exact: true}).click();
  await expect(page).toHaveURL(/#\/home$/);
  await page.locator('#live').click();
  await expectConnectedIdentity(page);
});


test('native account replacement preserves the actual lost-reply session key without reapplying it', async ({page}) => {
  await setup(page, {available: true, saved: true, loseReply: true});
  const session = async () => {
    await page.evaluate(() => {location.hash = '#/session/h1/fixture-session';});
    await expect(page.getByRole('heading', {name: 'Fixture session', exact: true})).toBeVisible();
  };
  await session();
  const draft = page.locator('textarea').first();
  await draft.fill('Keep the original instructions');
  await page.getByRole('button', {name: 'Send', exact: true}).click();
  await page.locator('.session-error').getByText('Error details', {exact: true}).click();
  await expect(page.getByText('Fixture lost operation reply', {exact: false})).toBeVisible();
  const original = await page.evaluate(() => (window as any).__credentialFixture.calls.find((c: any) => c.command === 'connector_request' && c.args.input.method === 'POST').args.input);
  await page.locator('#live').click();
  await page.getByRole('button', {name: 'Replace credential', exact: true}).click();
  await expectConnectedIdentity(page);
  await session(); await expect(draft).toHaveValue('');
  expect(await page.evaluate(() => (window as any).__credentialFixture.calls.filter((c: any) => c.command === 'connector_request' && c.args.input.method === 'POST').length)).toBe(1);
  await page.locator('#live').click();
  await page.evaluate(() => {(window as any).__credentialFixture.nextPrincipal = 'original-principal';});
  await page.getByRole('button', {name: 'Replace credential', exact: true}).click();
  await expectConnectedIdentity(page);
  await session(); await expect(draft).toHaveValue('Keep the original instructions');
  await page.getByRole('button', {name: 'Send', exact: true}).click();
  await page.locator('.session-send-record summary').click();
  await expect(page.getByText('op_' + 'a'.repeat(32), {exact: true})).toBeVisible();
  const posts = await page.evaluate(() => (window as any).__credentialFixture.calls.filter((c: any) => c.command === 'connector_request' && c.args.input.method === 'POST'));
  expect(posts).toHaveLength(2); expect(posts[1].args.input).toEqual(original);
});


test('slow startup renders recovery controls and discards verification completed after disconnect', async ({page}) => {
  await setup(page, {available: true, saved: true, delayConnect: true});
  await expect(page.getByText('Connection in progress…', {exact: true})).toBeVisible();
  await expect.poll(() => page.evaluate(() => typeof (window as any).__credentialFixture.releaseConnect)).toBe('function');
  await page.getByRole('button', {name: 'Disconnect', exact: true}).click();
  await expect(page.getByText('Disconnected · Connector work continues')).toBeVisible();
  await page.evaluate(() => (window as any).__credentialFixture.releaseConnect());
  await expect(page.locator('[data-connection-access]')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__credentialFixture.calls.filter((c: any) => c.command === 'connector_request' && c.args.input.path === '/bootstrap'))).toEqual([]);
});

for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) {
  test(`macOS Keychain enrollment uses native input ${locale} ${width}`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}});
    const page = await context.newPage();
    await setup(page, {store: 'macos_keychain',
      configPath: '/Users/fixture/Library/Application Support/io.betteragent.dashboard/central.json'});
    await page.getByRole('button', {name: locale === 'en-US' ? 'Add credential' : '新增憑證', exact: true}).click();
    await expect(page.getByText(locale === 'en-US' ? 'macOS Keychain' : 'macOS 鑰匙圈', {exact: true})).toBeVisible();
    await expect(page.locator('input[type=password]')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.evaluate(() => (window as any).__credentialFixture.calls.filter((c: any) => c.command === 'connector_enroll')))
      .toEqual([{command: 'connector_enroll', args: {locale}}]);
    await mkdir('test-results/macos-credentials', {recursive: true});
    await page.screenshot({path: `test-results/macos-credentials/settings-${locale}-${width}.png`, fullPage: true});
    await context.close();
  });
}

test('native setup keeps its draft and localizes required-field errors after a language change', async ({page}) => {
  await setup(page, {setup: true});
  const form = page.locator('[data-native-setup] form');
  await form.locator('input').first().fill('https://central.example');
  await page.locator('[data-language-switch]').getByRole('button', {name: '繁體中文', exact: true}).click();
  await expect(form.locator('input').first()).toHaveValue('https://central.example');
  await form.locator('button[type=submit]').click();
  expect(await form.locator('input').nth(1).evaluate((node: HTMLInputElement) => node.validationMessage)).toBe('請填寫此欄位。');
  await form.locator('input').nth(1).fill('viewer');
  expect(await form.locator('input').nth(1).evaluate((node: HTMLInputElement) => node.validationMessage)).toBe('');
  const calls = await page.evaluate(() => (window as any).__credentialFixture.calls);
  expect(calls.some((call: any) => ['connector_setup_configuration', 'connector_enroll', 'connector_connect'].includes(call.command))).toBe(false);
  await form.locator('button[type=submit]').click();
  await expect.poll(() => page.evaluate(() => (window as any).__credentialFixture.calls.filter((call: any) => call.command === 'connector_setup_configuration').length)).toBe(1);
  expect(await page.evaluate(() => (window as any).__credentialFixture.calls.find((call: any) => call.command === 'connector_setup_configuration').args.locale)).toBe('zh-TW');
  await page.getByRole('button', {name: '新增憑證', exact: true}).click();
  expect(await page.evaluate(() => (window as any).__credentialFixture.calls.find((call: any) => call.command === 'connector_enroll').args.locale)).toBe('zh-TW');
  await expect(page.locator('input[type=password]')).toHaveCount(0);
});
