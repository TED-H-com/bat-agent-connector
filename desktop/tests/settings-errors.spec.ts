import {test, expect, type Page} from '@playwright/test';
import {expectNoNestedErrorDetails} from './api-error-assertions.ts';
import {managedSetupFixture, openSetupAdvanced, applySetupReview} from './managed-setup-fixture.ts';
const cases = [
  [404,'NOT_FOUND','This record is unavailable. Refresh the view.','找不到這筆資料，請重新整理。'],
  [409,'VERSION_CONFLICT','The record changed. Refresh before trying again.','資料版本已變更，請重新整理後再試。'],
  [413,'SOURCE_TOO_LARGE','This source is too large. Narrow the selection and try again.','來源資料過大，請縮小範圍後再試。'],
  [422,'INVALID_PARAMS','Check the required fields and their format.','輸入內容不符合要求，請檢查欄位。'],
  [504,'TIMEOUT','The connection timed out. Check the original request.','連線逾時，請查回原請求。'],
  [422,'UNKNOWN_FIXTURE_CODE','This request could not be completed. Check Details.','無法完成此要求，請查看詳細資料。'],
] as const;
function trackSetupApplies(page: Page) {
  const posts: string[] = [];
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && ['/api/v1/managed/setup', '/api/v1/operations'].includes(path)) posts.push(path);
  });
  return posts;
}
for(const locale of ['en','zh-TW'])test.describe(`${locale} Settings`,()=>{
  test.use({locale});
  for(const [status,code,en,zh] of cases)test(`readable ${status} ${code} with technical details hidden`,async({page})=>{
    const state=await managedSetupFixture(page,false,locale);state.refused=true;state.refusalCode=code;state.refusalStatus=status;
    const applies=trackSetupApplies(page);
    await page.goto('/dashboard/'); await openSetupAdvanced(page);
    const form=page.locator('[data-setup-host]');await form.locator('[data-setup-field=connection_url]').fill(`wss://localhost?token=synthetic-bat-secret&fp=${'ab'.repeat(32)}`);
    await form.locator('button[type=submit]').click();
    await expect(page.locator('[data-setup-review]')).toBeVisible();
    expect(state.previews).toHaveLength(1); expect(state.posts).toEqual([]); expect(applies).toEqual([]);
    await applySetupReview(page);const error=page.locator('[data-api-error]');
    await expect(error.locator('[data-error-summary]')).toHaveText(locale==='en'?en:zh);
    expect(await error.innerText()).not.toContain(code);await expect(error.locator('details')).not.toHaveAttribute('open','');
    await error.locator('summary').click();await expect(error.locator('[data-error-details] code')).toHaveText(code);
    await expect(error.locator('[data-error-details]')).toContainText('Read the new configuration.');
    await expectNoNestedErrorDetails(error);
    expect(state.posts).toHaveLength(1); expect(state.posts[0].body.action).toBe('setup.host');
    expect(applies).toEqual(['/api/v1/operations']);
  });
  test('preview refusal is readable without applying a connection',async({page})=>{
    const state=await managedSetupFixture(page,false,locale);state.previewRefused=true;
    state.refusalMessage='Configuration changed; review setup again';
    const applies=trackSetupApplies(page);
    await page.goto('/dashboard/'); await openSetupAdvanced(page);
    const form=page.locator('[data-setup-host]');
    await form.locator('[data-setup-field=connection_url]').fill(`wss://host-a.example.invalid?token=synthetic-bat-secret&fp=${'ab'.repeat(32)}`);
    await form.locator('button[type=submit]').click();
    const error=page.locator('[data-managed-setup] [data-api-error]');
    await expect(error.locator('[data-error-summary]')).toHaveText(locale==='en'
      ? 'Configuration changed elsewhere. Refresh before trying again.' : '設定已在別處變更，請重新整理後再試。');
    expect(await error.innerText()).not.toContain(state.refusalCode);
    expect(await error.innerText()).not.toContain(state.refusalMessage);
    await expect(error.locator('details')).not.toHaveAttribute('open','');
    await expect(error.locator('[data-error-details]')).toBeHidden();
    await expect(form).toBeVisible(); await expect(page.locator('[data-setup-review]')).toBeHidden();
    await expect(page.getByRole('button',{name:locale==='en'?'Apply connection':'套用連線',exact:true})).toBeHidden();
    expect(state.previews).toHaveLength(1); expect(state.posts).toEqual([]); expect(applies).toEqual([]); expect(state.saved).toBe(false);
    await error.locator('summary').click();
    await expect(error.locator('[data-error-details] code')).toHaveText(state.refusalCode);
    await expect(error.locator('[data-error-details]')).toContainText(state.refusalMessage);
    await expectNoNestedErrorDetails(error);
    expect(state.posts).toEqual([]); expect(applies).toEqual([]);
  });
  for(const native of [false,true])test(`quiet connection access and translated setup ${native?'native':'web'}`,async({page})=>{
    const state=await managedSetupFixture(page,native,locale);state.scopes=['observe','operate','manage','start','approve','cleanup','cleanup_discard','deploy','integrate','merge'];
    await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);const card=page.locator('[data-connection-card]');await expect(card).toBeVisible();
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
    const state=await managedSetupFixture(page,false,locale);state.verification.timeout_s=0;await page.goto('/dashboard/'); await openSetupAdvanced(page);
    const error=page.locator('[data-api-error]');
    await expect(error.locator('[data-error-summary]')).toHaveText(locale==='en'
      ? 'Enter a valid project, executable, at most 63 nonempty arguments and a 1–3600 second timeout.'
      : '請輸入有效專案、執行程式、最多 63 個非空參數，以及 1–3600 秒逾時。');
    await expect(error.locator('code, [data-error-details] p')).toHaveCount(0);
    await error.locator('summary').click();
    await expectNoNestedErrorDetails(error);
    await expect(error.locator('[data-export-diagnostics]')).toHaveText(locale==='en'?'Export diagnostics':'匯出診斷資料');
    await expect(page.locator('[data-setup-host] button[type=submit]')).toBeDisabled();
    expect(state.posts).toEqual([]);expect(state.staged).toEqual([]);
  });
  for (const unavailable of [false,true]) test(`empty BAT profiles ${unavailable?'unreadable':'without error'}`,async({page})=>{
    const state=await managedSetupFixture(page,false,locale);state.hasProfiles=false;
    state.profilesError=unavailable?'Fixture directory cannot be read':null;await page.goto('/dashboard/'); await openSetupAdvanced(page);
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
    await page.setViewportSize({width,height:900});await managedSetupFixture(page,false,locale);await page.goto('/dashboard/'); await openSetupAdvanced(page);
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
