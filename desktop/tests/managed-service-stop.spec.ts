import {test, expect} from '@playwright/test';
import {managedSetupFixture, openSetupAdvanced} from './managed-setup-fixture.ts';
import {managedStatusFixture} from './managed-status-fixture.ts';
for (const locale of ['en', 'zh-TW']) {
  const zh = locale === 'zh-TW';
  const stop = zh ? '停止背景服務' : 'Stop background service';
  const start = zh ? '啟動背景服務' : 'Start background service';
  test(`managed lifecycle preview stop start ${locale}`, async ({page}) => {
    const fixture = await managedSetupFixture(page, true, locale);
    await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
    await page.getByRole('button', {name: stop, exact: true}).click();
    const preview = page.locator('[data-managed-stop-preview]');
    await expect(preview).toContainText(zh ? '本機 Connector 會停止。' : 'The local Connector will stop.');
    await expect(preview).toContainText(zh ? '在你選擇啟動之前，開啟程式、登入電腦、自動恢復或在瀏覽器開啟都不會重新啟動。' : 'App open, sign-in, recovery and Open in browser will not restart it until you choose Start.');
    await expect(preview).toContainText(zh ? '尚未完成或核對的工作會阻擋停止。' : 'Unsettled work blocks stopping.');
    await expect(preview).toContainText(zh ? '外部連線不受影響。' : 'External connections are unaffected.');
    expect(fixture.managedCalls.filter(action => action === 'stop')).toEqual([]);
    await preview.getByRole('button', {name: zh ? '確認停止' : 'Confirm stop', exact: true}).click();
    await expect(page.locator('[data-managed-lifecycle]')).toHaveText(zh ? '背景服務已停止' : 'Background service stopped');
    await page.getByRole('button', {name: start, exact: true}).click();
    await expect(page.locator('[data-managed-lifecycle]')).toHaveText(zh ? '背景服務運作中' : 'Background service running');
    expect(fixture.managedCalls.filter(action => ['stop', 'start'].includes(action))).toEqual(['stop', 'start']);
  });
  test(`managed lifecycle serial contention on Start ${locale}`, async ({page}) => {
    const fixture = await managedSetupFixture(page, true, locale);
    fixture.managed.lifecycle = {state: 'stopped', requested_at: 1800000000, stopped_at: 1800000001};
    fixture.startBusy = true;
    await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
    await page.getByRole('button', {name: start, exact: true}).click();
    const service = page.locator('[data-managed-service]');
    await expect(service).toContainText(zh ? '另一個背景服務操作仍在進行中，請等它完成後再試。' : 'Another background service action is still running. Try again when it finishes.');
    await expect(service).not.toContainText(zh ? '背景服務目前忙碌，請先完成或核對目前工作，再停止服務。' : 'The background service is busy. Finish or reconcile current work before stopping.');
    await expect(page.locator('[data-managed-lifecycle]')).toHaveText(zh ? '背景服務已停止' : 'Background service stopped');
    expect(fixture.managedCalls.filter(action => action === 'start')).toEqual(['start']);
  });
  for (const kind of ['busy', 'unknown_busy', 'pending', 'client']) test(`managed lifecycle ${kind} ${locale}`, async ({page}) => {
    const fixture = await managedSetupFixture(page, true, locale);
    if (kind === 'client') fixture.managed = managedStatusFixture(false);
    fixture.stopBusy = kind === 'busy'; fixture.stopBusyGeneric = kind === 'unknown_busy'; fixture.stopPending = kind === 'pending';
    await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
    if (kind === 'client') {
      await expect(page.getByRole('button', {name: stop, exact: true})).toHaveCount(0);
      await expect(page.getByRole('button', {name: start, exact: true})).toHaveCount(0);
      return;
    }
    await page.getByRole('button', {name: stop, exact: true}).click();
    await page.locator('[data-managed-stop-preview]').getByRole('button', {name: zh ? '確認停止' : 'Confirm stop'}).click();
    if (kind === 'unknown_busy') {
      await expect(page.locator('[data-managed-service]')).toContainText(zh ? '背景服務目前忙碌，請先完成或核對目前工作，再停止服務。' : 'The background service is busy. Finish or reconcile current work before stopping.');
      await expect(page.locator('[data-managed-lifecycle]')).toHaveText(zh ? '背景服務運作中' : 'Background service running');
      await expect(page.locator('[data-managed-service]')).not.toContainText('MANAGED_RUNTIME_START_FAILED');
    } else if (kind === 'busy') {
      await expect(page.locator('[data-managed-service]')).toContainText('finish or reconcile current operations before stopping');
      await expect(page.locator('[data-managed-lifecycle]')).toHaveText(zh ? '背景服務運作中' : 'Background service running');
    } else {
      await expect(page.locator('[data-managed-lifecycle]')).toHaveText(zh ? '背景服務停止中' : 'Background service stopping');
      await expect(page.getByRole('button', {name: start, exact: true})).toBeVisible();
      fixture.startStillStopping = true;
      await page.getByRole('button', {name: start, exact: true}).click();
      await expect(page.locator('[data-managed-service]')).toContainText(zh ? '背景服務仍在停止中。你可以等待，或再次選擇停止服務。' : 'The background service is still stopping. You can wait or stop it again.');
      await expect(page.locator('[data-managed-lifecycle]')).toHaveText(zh ? '背景服務停止中' : 'Background service stopping');
      await expect(page.getByRole('button', {name: stop, exact: true})).toBeVisible();
      await page.getByRole('button', {name: stop, exact: true}).click();
      const preview = page.locator('[data-managed-stop-preview]');
      await expect(preview).toContainText(zh ? '本機 Connector 會停止。' : 'The local Connector will stop.');
      expect(fixture.managedCalls.filter(action => action === 'stop')).toHaveLength(1);
      fixture.stopPending = false;
      await preview.getByRole('button', {name: zh ? '確認停止' : 'Confirm stop'}).click();
      await expect(page.locator('[data-managed-lifecycle]')).toHaveText(zh ? '背景服務已停止' : 'Background service stopped');
      expect(fixture.managedCalls.filter(action => action === 'stop')).toHaveLength(2);
    }
  });
}

for (const locale of ['en', 'zh-TW']) test(`stopped native startup opens Settings normally ${locale}`, async ({page}) => {
  const fixture = await managedSetupFixture(page, true, locale);
  fixture.managed.ready = false;
  fixture.managed.lifecycle = {state: 'stopped', requested_at: 1800000000, stopped_at: 1800000001};
  await page.addInitScript(() => {
    const invoke = (window as any).__TAURI_INTERNALS__.invoke;
    (window as any).__TAURI_INTERNALS__.invoke = (command: string, args: any) => command === 'native_status' ? {
      endpoint: null, expected_actor: null, error: null, credential_available: false, credential_source: null,
      credential_saved: false, enrollment_supported: true, configuration_reload: false, configuration_setup: true,
      configuration_source: 'managed', configuration_file: null, connected: false, managed_recovery: null, managed_rebind: 0,
      startup_role: {role: 'provider', generation: 1, installation_ref: '00000000-0000-4000-8000-000000000001'},
      updates: false, file_transfers: true, brief_exports: true, diagnostics_save: true,
      app_build: {version: '0.1.9', commit: null},
    } : invoke(command, args);
  });
  await page.goto('/dashboard/#/settings');
  await expect(page.locator('[data-managed-lifecycle]')).toHaveText(locale === 'en' ? 'Background service stopped' : '背景服務已停止');
  await expect(page.getByRole('button', {name: locale === 'en' ? 'Start background service' : '啟動背景服務', exact: true})).toBeVisible();
  await expect(page.locator('[data-native-setup]')).toBeHidden();
  expect(fixture.managedCalls.every(action => action === 'status')).toBe(true);
});
