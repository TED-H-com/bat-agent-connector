import {test, expect} from '@playwright/test';
import {managedSetupFixture} from './managed-setup-fixture.ts';
const cases = [
  [404,'NOT_FOUND','This record is unavailable. Refresh the view.','找不到這筆資料，請重新整理。'],
  [409,'VERSION_CONFLICT','The record changed. Refresh before trying again.','資料版本已變更，請重新整理後再試。'],
  [413,'SOURCE_TOO_LARGE','This source is too large. Narrow the selection and try again.','來源資料過大，請縮小範圍後再試。'],
  [422,'INVALID_PARAMS','Check the required fields and their format.','輸入內容不符合要求，請檢查欄位。'],
  [504,'TIMEOUT','The connection timed out. Check the original request.','連線逾時，請查回原請求。'],
  [422,'UNKNOWN_FIXTURE_CODE','This request could not be completed. Check Details.','無法完成此要求，請查看詳細資料。'],
] as const;
for(const locale of ['en','zh-TW'])test.describe(`${locale} Settings`,()=>{
  test.use({locale});
  for(const [status,code,en,zh] of cases)test(`readable ${status} ${code} with technical details hidden`,async({page})=>{
    const state=await managedSetupFixture(page,false,locale);state.refused=true;state.refusalCode=code;state.refusalStatus=status;
    await page.goto('/dashboard/');
    const form=page.locator('[data-setup-host]');await form.locator('[data-setup-field=connection_url]').fill(`wss://localhost?token=synthetic-bat-secret&fp=${'ab'.repeat(32)}`);
    await form.locator('button[type=submit]').click();const error=page.locator('[data-api-error]');
    await expect(error.locator('[data-error-summary]')).toHaveText(locale==='en'?en:zh);
    expect(await error.innerText()).not.toContain(code);await expect(error.locator('details')).not.toHaveAttribute('open','');
    await error.locator('summary').click();await expect(error.locator('[data-error-details] code')).toHaveText(code);
    await expect(error.locator('[data-error-details]')).toContainText('Read the new configuration.');
  });
  for(const native of [false,true])test(`quiet connection access and translated setup ${native?'native':'web'}`,async({page})=>{
    const state=await managedSetupFixture(page,native,locale);state.scopes=['observe','operate','manage','start','approve','cleanup','cleanup_discard','deploy','integrate','merge'];
    await page.goto('/dashboard/#/settings');const card=page.locator('[data-connection-card]');await expect(card).toBeVisible();
    await expect(card.locator('details')).toHaveCount(1);await expect(card.locator('[data-connection-access]')).toContainText(locale==='en'?'View':'檢視');
    const visible=await card.innerText();expect(visible).not.toContain('cleanup_discard');expect(visible).not.toContain('fixture-person');
    if(!native)expect(visible).not.toContain('Tailscale');
    await card.locator('summary').click();await expect(card).toContainText(state.scopes.join(', '));
    const form=page.locator('[data-setup-host]');await form.locator('[data-setup-manual]>summary').click();await form.locator('summary').filter({hasText:locale==='en'?'Allow managed work':'允許受管工作'}).click();
    if(locale==='zh-TW')expect(await page.locator('[data-settings-panel=connection]').innerText()).not.toMatch(/\bprofile\b|\btoken\b|Workspace profile ID|\bposix\b|\bsession\b|\bmetadata\b|\bclone\b/);
    await expect(page.getByRole('heading',{name:locale==='en'?'Connect BAT':'連線至 BAT',exact:true})).toHaveCount(0); // one disclosure heading, no repeated H3
    await expect(page.locator('[data-setup-host]').locator('input[style]')).toHaveCount(0);
    expect(state.posts).toEqual([]);expect(state.staged).toEqual([]);
  });
});
