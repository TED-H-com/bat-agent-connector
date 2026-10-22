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
  for (const kind of ['busy', 'pending', 'client']) test(`managed lifecycle ${kind} ${locale}`, async ({page}) => {
    const fixture = await managedSetupFixture(page, true, locale);
    if (kind === 'client') fixture.managed = managedStatusFixture(false);
    fixture.stopBusy = kind === 'busy'; fixture.stopPending = kind === 'pending';
    await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
    if (kind === 'client') {
      await expect(page.getByRole('button', {name: stop, exact: true})).toHaveCount(0);
      await expect(page.getByRole('button', {name: start, exact: true})).toHaveCount(0);
      return;
    }
    await page.getByRole('button', {name: stop, exact: true}).click();
    await page.locator('[data-managed-stop-preview]').getByRole('button', {name: zh ? '確認停止' : 'Confirm stop'}).click();
    if (kind === 'busy') {
      await expect(page.locator('[data-managed-service]')).toContainText('finish or reconcile current operations before stopping');
      await expect(page.locator('[data-managed-lifecycle]')).toHaveText(zh ? '背景服務運作中' : 'Background service running');
    } else {
      await expect(page.locator('[data-managed-lifecycle]')).toHaveText(zh ? '背景服務停止中' : 'Background service stopping');
      await expect(page.getByRole('button', {name: start, exact: true})).toBeVisible();
    }
  });
}
