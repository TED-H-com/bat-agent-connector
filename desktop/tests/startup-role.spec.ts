import {managedStatusFixture} from './managed-status-fixture.ts';
import {test, expect, type Page} from '@playwright/test';

async function pending(page: Page, role: string | null = null) {
  await page.addInitScript(({role: initialRole, managed, external}) => {
    let role = initialRole;
    role = sessionStorage.getItem('native-role-fixture') || role;
    const fixture = {role, generation: role ? 1 : 0, calls: [] as any[], busy: false, fail: false};
    const status = () => ({endpoint: fixture.role && fixture.role !== 'client' ? 'http://127.0.0.1:41001/' : null,
      expected_actor: fixture.role && fixture.role !== 'client' ? 'fixture-user' : null,
      configuration_source: fixture.role && fixture.role !== 'client' ? 'managed' : 'external',
      error: fixture.role ? null : 'STARTUP_ROLE_PENDING', credential_available: !!fixture.role && fixture.role !== 'client',
      credential_saved: false, enrollment_supported: true, configuration_reload: fixture.role === 'client',
      configuration_setup: !!fixture.role, configuration_file: 'example/central.json', connected: !!fixture.role && fixture.role !== 'client',
      managed_rebind: fixture.role && fixture.role !== 'client' ? 1 : null, managed_recovery: null,
      startup_role: {role: fixture.role, generation: fixture.generation,
        installation_ref: fixture.role ? '00000000-0000-4000-8000-000000000001' : null}});
    Object.assign(window, {isTauri: true, __startupRole: fixture, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      fixture.calls.push({command, args});
      if (command === 'managed_control' && args.input.action === 'status') return fixture.role && fixture.role !== 'client' ? managed : external;
      if (command === 'native_status') return status();
      if (command === 'managed_select_role') {
        if (fixture.fail) throw 'MANAGED_CONFIGURATION_UNREADABLE';
        if (args.input.expected_generation !== fixture.generation || fixture.role) throw 'STARTUP_ROLE_CHANGED';
        fixture.role = args.input.role; fixture.generation++;
        sessionStorage.setItem('native-role-fixture', fixture.role!);
        return status();
      }
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      if (command === 'connector_connect') throw 'Fixture credential not yet enrolled';
      if (command === 'connector_disconnect') return;
      if (command === 'connector_setup_configuration') return null; // Existing native confirmation cancelled.
      throw new Error('Unexpected command ' + command);
    }}});
  }, {role, managed: managedStatusFixture(true), external: managedStatusFixture(false)});
  await page.goto('/dashboard/');
}

for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 1440]) {
  test(`native role choice precedes external setup (${locale}, ${width})`, async ({browser}, testInfo) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}}), page = await context.newPage();
    await pending(page);
    const choice = page.locator('[data-startup-role]');
    await expect(choice).toBeVisible();
    await expect(choice.getByRole('heading')).toHaveText(locale === 'zh-TW' ? '這個安裝要如何使用？' : 'How will you use this installation?');
    await expect(page.locator('[data-native-setup]')).toHaveCount(0);
    await expect(choice.getByRole('button')).toHaveCount(3);
    expect(await page.evaluate(() => (window as any).__startupRole.calls.filter((c: any) =>
      ['managed_select_role', 'connector_setup_configuration', 'connector_connect'].includes(c.command)))).toEqual([]);
    await page.screenshot({path: testInfo.outputPath(`role-${locale}-${width}.png`), fullPage: true});
    await choice.getByRole('button', {name: locale === 'zh-TW' ? '使用既有服務' : 'Use existing services', exact: true}).click();
    await expect(choice).toHaveCount(0);
    await expect(page.locator('[data-native-setup]')).toBeVisible();
    await expect(page.getByRole('heading', {name: locale === 'zh-TW' ? '加入既有的 Connector' : 'Join another Connector'})).toBeVisible();
    const calls = await page.evaluate(() => (window as any).__startupRole.calls.filter((c: any) => c.command === 'managed_select_role'));
    expect(calls).toEqual([{command: 'managed_select_role', args: {input: {role: 'client', expected_generation: 0}}}]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.reload();
    await expect(choice).toHaveCount(0);
    await context.close();
  });
  for (const role of ['provider', 'both']) test(`native ${role} prepares via fixed command (${locale}, ${width})`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}}), page = await context.newPage();
    await pending(page);
    const button = page.locator(`[data-startup-role] [data-role=${role}]`);
    await button.click();
    await expect(page.locator('[data-startup-role]')).toHaveCount(0);
    expect(await page.evaluate(() => (window as any).__startupRole.calls.filter((c: any) => c.command === 'managed_select_role')))
      .toEqual([{command: 'managed_select_role', args: {input: {role, expected_generation: 0}}}]);
    await context.close();
  });
}

for (const locale of ['en-US', 'zh-TW']) test(`selection refusal keeps the pending role and sends no setup (${locale})`, async ({browser}) => {
  const context = await browser.newContext({locale}), page = await context.newPage();
  await pending(page);
  await page.evaluate(() => {(window as any).__startupRole.fail = true;});
  await page.locator('[data-role=client]').click();
  await expect(page.locator('[data-startup-role] [data-api-error]')).toBeVisible();
  await expect(page.locator('[data-role=provider]')).toBeEnabled();
  expect(await page.evaluate(() => (window as any).__startupRole.calls.filter((c: any) =>
    c.command === 'connector_setup_configuration' || c.command === 'connector_enroll'))).toEqual([]);
  await context.close();
});

for (const locale of ['en-US', 'zh-TW']) for (const roleShape of ['missing', 'null'])
  test(`legacy native entry keeps bootstrap isolation (${locale}, ${roleShape})`, async ({browser}, testInfo) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(({roleShape, managed}) => {
      const trace: any[] = [], requests: string[] = [];
      let phase = 'starting';
      Object.assign(window, {isTauri: true, __entryTrace: trace, __entryRequests: requests,
        __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
          trace.push({command, phase, mounted: !!document.querySelector('.native-connection')});
          if (command === 'managed_control' && args.input.action === 'status') return managed;
          if (command === 'native_status') return {endpoint: 'https://central.example/', credential_available: true,
            error: null, ...(roleShape === 'null' ? {startup_role: null} : {})};
          if (command === 'connector_connect') {phase = 'connecting'; return {actor: 'expected-actor', scopes: ['observe']};}
          if (command === 'connector_disconnect') {phase = 'disconnected'; return;}
          if (command === 'fleet_availability') return {configured: false, platform_supported: false};
          if (command === 'connector_request') {
            requests.push(args.input.path);
            trace.push({path: args.input.path, phase, stack: new Error().stack});
            phase = 'validating-bootstrap';
            return {status: 200, data: {sync: {version: 1, server_id: 'server', principal_id: 'principal', checkpoint: {cursor: 0, token: 'proof'}},
              capabilities: {actor: 'different-actor', scopes: ['observe']}}};
          }
          throw new Error('Unexpected native command ' + command);
        }}});
    }, {roleShape, managed: managedStatusFixture(false)});
    try {
      await page.goto('/dashboard/');
      await expect(page.locator('.native-connection')).toBeVisible();
      await expect(page.locator('[data-error-summary]').filter({hasText: 'Invalid central bootstrap identity'})).toBeVisible();
      await expect(page.locator('[data-startup-role]')).toHaveCount(0);
      expect(await page.evaluate(() => (window as any).__entryRequests)).toEqual(['/bootstrap']);
      expect(errors).toEqual([]);
    } finally {
      await testInfo.attach('native-entry-trace', {body: JSON.stringify(await page.evaluate(() => (window as any).__entryTrace), null, 2), contentType: 'application/json'});
      await context.close();
    }
  });
