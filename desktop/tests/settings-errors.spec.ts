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
    await expect(card.locator('[data-connection-access]')).toHaveJSProperty('tagName','DD');
    await expect(card.locator('[data-connection-access]').locator('xpath=preceding-sibling::dt[1]')).toHaveText(locale==='en'?'Access':'權限');
    const visible=await card.innerText();expect(visible).not.toContain('cleanup_discard');expect(visible).not.toContain('fixture-person');
    if(!native)expect(visible).not.toContain('Tailscale');
    await card.locator('summary').click();await expect(card).toContainText(state.scopes.join(', '));
    const form=page.locator('[data-setup-host]');await form.locator('[data-setup-manual]>summary').click();await form.locator('summary').filter({hasText:locale==='en'?'Allow managed work':'允許受管工作'}).click();
    if(locale==='zh-TW')expect(await page.locator('[data-settings-panel=connection]').innerText()).not.toMatch(/\bprofile\b|\btoken\b|Workspace profile ID|\bposix\b|\bsession\b|\bmetadata\b|\bclone\b/);
    await expect(page.getByRole('heading',{name:locale==='en'?'Connect BAT':'連線至 BAT',exact:true})).toHaveCount(0); // one disclosure heading, no repeated H3
    await expect(page.locator('[data-setup-host]').locator('input[style]')).toHaveCount(0);
    expect(state.posts).toEqual([]);expect(state.staged).toEqual([]);
  });
  test('client setup validation keeps its specific sentence and offers diagnostics in Details',async({page})=>{
    const state=await managedSetupFixture(page,false,locale);state.verification.timeout_s=0;await page.goto('/dashboard/');
    const error=page.locator('[data-api-error]');
    await expect(error.locator('[data-error-summary]')).toHaveText(locale==='en'
      ? 'Enter a valid project, executable, at most 63 nonempty arguments and a 1–3600 second timeout.'
      : '請輸入有效專案、執行程式、最多 63 個非空參數，以及 1–3600 秒逾時。');
    await expect(error.locator('code, [data-error-details] p')).toHaveCount(0);
    await error.locator('summary').click();
    await expect(error.locator('[data-export-diagnostics]')).toHaveText(locale==='en'?'Export diagnostics':'匯出診斷資料');
    await expect(page.locator('[data-setup-host] button[type=submit]')).toBeDisabled();
    expect(state.posts).toEqual([]);expect(state.staged).toEqual([]);
  });
  for (const unavailable of [false,true]) test(`empty BAT profiles ${unavailable?'unreadable':'without error'}`,async({page})=>{
    const state=await managedSetupFixture(page,false,locale);state.hasProfiles=false;
    state.profilesError=unavailable?'Fixture directory cannot be read':null;await page.goto('/dashboard/');
    await page.locator('[data-setup-manual]>summary').click();
    if(unavailable) {
      await expect(page.locator('[data-setup-profile] select')).toBeVisible();
      await expect(page.locator('[data-setup-profiles-error]')).toHaveText(locale==='en'
        ? 'BAT connection profiles could not be read. Paste a connection URL or enter the values manually.'
        : '無法讀取 BAT 連線設定檔，請貼上連線網址或手動輸入。');
    } else await expect(page.locator('[data-setup-profile]')).toBeHidden();
    expect(state.posts).toEqual([]);expect(state.staged).toEqual([]);
  });
  for (const width of [1440,1000,390]) test(`setup fields stay aligned with one page Refresh at ${width}`,async({page})=>{
    await page.setViewportSize({width,height:900});await managedSetupFixture(page,false,locale);await page.goto('/dashboard/');
    const geometry=await page.locator('[data-managed-setup]').evaluate(panel=>{
      const rect=(selector:string)=>panel.querySelector(selector)!.getBoundingClientRect();
      const input=rect('#setup-connection_url'), hint=rect('#setup-connection_url-hint'), label=rect('label[for=setup-host]');
      return {hintGap:hint.top-input.bottom,fieldGap:label.top-hint.bottom};
    });
    await expect(page.locator('#main').getByRole('button',{name:locale==='en'?'Refresh':'重新整理',exact:true})).toHaveCount(1);
    await expect(page.locator('[data-managed-setup] header button')).toHaveCount(0);
    expect(geometry.hintGap).toBe(4);expect(geometry.fieldGap).toBeGreaterThanOrEqual(12);expect(geometry.fieldGap).toBeLessThanOrEqual(16);
    await page.locator('[data-setup-manual]>summary').click();
    const heights=await page.locator('[data-setup-manual] input').evaluateAll(inputs=>inputs.map(node=>node.getBoundingClientRect().height));
    expect(new Set(heights).size).toBe(1);
  });
});
