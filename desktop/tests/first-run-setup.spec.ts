import {test, expect, type Page} from '@playwright/test';
import {managedSetupFixture, reviewSetupConnection, openSetupAdvanced, applySetupReview} from './managed-setup-fixture.ts';
const url = `ws://127.0.0.1?token=synthetic-bat-secret&fp=${'ab'.repeat(32)}`;
const formFor = (page: Page) => page.locator('[data-setup-host]');
const field = (page: Page, name: string) => formFor(page).locator(`[data-setup-field=${name}]`);
const storage = (page: Page) => page.evaluate(() => JSON.stringify({...localStorage,...sessionStorage}));
const draft = (page: Page) => page.evaluate(() => JSON.parse(localStorage.getItem(Object.keys(localStorage).find(key=>key.startsWith('batc.managed.setup.'))!) || '{}'));
for (const locale of ['en','zh-TW']) for (const native of [false,true]) test.describe(`${locale} ${native?'native':'web'} first run`,()=>{
  test.use({locale});
  test('one masked URL paste fills trusted details locally and leads to sessions',async({page})=>{
    const state = await managedSetupFixture(page,native,locale); await page.goto('/dashboard/'); await openSetupAdvanced(page);
    await expect(field(page,'connection_url')).toHaveAttribute('type','password');
    await field(page,'connection_url').evaluate((node,value)=>{
      const event = new Event('paste',{bubbles:true,cancelable:true});
      Object.defineProperty(event,'clipboardData',{value:{getData:()=>value}}); node.dispatchEvent(event);
    },url);
    await expect(field(page,'connection_url')).toHaveValue(''); await expect(field(page,'url')).toHaveValue('wss://127.0.0.1:9876');
    await expect(field(page,'fingerprint')).toHaveValue('AB'.repeat(32)); await expect(field(page,'bat_secret')).toHaveValue('synthetic-bat-secret');
    await expect(field(page,'host')).toHaveValue('local'); await expect(formFor(page).locator('[data-setup-manual]')).not.toHaveAttribute('open','');
    expect(state.staged).toEqual([]);expect(state.posts).toEqual([]);
    expect(await storage(page)).not.toMatch(/synthetic-bat-secret|token=/);
    await reviewSetupConnection(page,locale); await applySetupReview(page);
    await expect(page.locator('[data-setup-sessions]')).toBeVisible();
    await expect(page.locator('[data-setup-connect]')).not.toHaveAttribute('open', '');
    expect(state.posts).toHaveLength(1);expect(state.staged).toHaveLength(1);
    expect(state.posts[0].body.params.url).toBe('wss://127.0.0.1:9876');
    expect(JSON.stringify(state.posts)).not.toMatch(/synthetic-bat-secret|token=/);
    expect(await storage(page)).not.toMatch(/synthetic-bat-secret|token=/);
    await expect(field(page,'bat_secret')).toHaveValue('');
    await page.locator('[data-setup-sessions]').click(); await expect(page).toHaveURL(/#\/sessions$/);
    await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
    await expect(page.locator('[data-setup-connect]')).not.toHaveAttribute('open', '');
    await page.locator('[data-setup-connect]>summary').click(); await expect(field(page,'connection_url')).toBeVisible();
  });
  test('malformed masked URL is localized and never saved; edits can clear it',async({page})=>{
    const state = await managedSetupFixture(page,native,locale); await page.goto('/dashboard/'); await openSetupAdvanced(page);
    await field(page,'connection_url').fill('wss://localhost?token=synthetic-bat-secret');
    await expect(page.locator('[data-setup-error=connection_url]')).toBeHidden();
    await field(page,'connection_url').blur();
    await expect(page.locator('[data-setup-error=connection_url]')).toHaveText(locale==='en'?'This is not a BAT connection URL':'這不是 BAT 連線網址');
    await expect(field(page,'connection_url')).toHaveValue('wss://localhost?token=synthetic-bat-secret');
    expect(await storage(page)).not.toMatch(/synthetic-bat-secret|token=/);expect(state.staged).toEqual([]);expect(state.posts).toEqual([]);
    await field(page,'connection_url').fill('');await expect(page.locator('[data-setup-error=connection_url]')).toBeHidden();
  });
  test('URL errors wait for paste, change, blur or submit rather than typing',async({page})=>{
    const state = await managedSetupFixture(page,native,locale); await page.goto('/dashboard/'); await openSetupAdvanced(page);
    const input=field(page,'connection_url'), error=page.locator('[data-setup-error=connection_url]');
    await input.pressSequentially('wss'); await expect(error).toBeHidden();
    await input.dispatchEvent('change'); await expect(error).toBeVisible();
    await input.fill('ws'); await expect(error).toBeHidden();
    await formFor(page).locator('[data-setup-discover]').click(); await expect(error).toBeVisible();
    await input.fill('');
    await input.evaluate(node=>{
      const event=new Event('paste',{bubbles:true,cancelable:true});
      Object.defineProperty(event,'clipboardData',{value:{getData:()=> 'malformed BAT URL'}});node.dispatchEvent(event);
    });
    await expect(error).toHaveText(locale==='en'?'This is not a BAT connection URL':'這不是 BAT 連線網址');
    expect(state.posts).toEqual([]); expect(state.staged).toEqual([]);
  });
  test('completed connection collapses, and Change settings reopens its editable form',async({page})=>{
    const state=await managedSetupFixture(page,native,locale); await page.goto('/dashboard/'); await openSetupAdvanced(page);await field(page,'connection_url').fill(url);
    await reviewSetupConnection(page,locale); await applySetupReview(page);await expect(page.locator('[data-setup-sessions]')).toBeVisible();
    await expect(page.locator('[data-setup-connect]')).not.toHaveAttribute('open','');
    await page.getByRole('button',{name:locale==='en'?'Change settings':'修改設定',exact:true}).click();
    await expect(field(page,'host')).toBeEditable();await expect(field(page,'connection_url')).toBeVisible();
    expect(state.posts).toHaveLength(1);expect(state.staged).toHaveLength(1);
  });
  test('Chinese host ID fails inline before staging and preserves the token through IME',async({page})=>{
    const state = await managedSetupFixture(page,native,locale); await page.goto('/dashboard/'); await openSetupAdvanced(page);await field(page,'connection_url').fill(url);
    await field(page,'host').dispatchEvent('compositionstart');await field(page,'host').fill('本機');
    await expect(page.locator('[data-setup-error=host]')).toBeHidden();
    await field(page,'host').dispatchEvent('compositionend');await expect(page.locator('[data-setup-error=host]')).toBeVisible();
    await formFor(page).locator('[data-setup-discover]').click();
    await expect(field(page,'host')).toBeFocused();await expect(field(page,'host')).toBeEditable();
    await expect(field(page,'bat_secret')).toHaveValue('synthetic-bat-secret');expect(state.posts).toEqual([]);expect(state.staged).toEqual([]);
    await expect(page.locator('[data-setup-error=host]')).toContainText(locale==='en'?'Host ID must':'主機代號須');
    expect(await field(page,'host').evaluate((node: HTMLInputElement)=>node.validationMessage)).toBeTruthy();
    await expect(field(page,'host')).toHaveAttribute('aria-describedby',/setup-host-error/);
  });
  test('proven refusal unlocks immediately, focuses the fault and retries with only a memory reference',async({page})=>{
    const state = await managedSetupFixture(page,native,locale);state.refused=true;state.refusalCode='INVALID_TARGET';state.refusalStatus=422;
    await page.goto('/dashboard/'); await openSetupAdvanced(page);await field(page,'connection_url').fill(url);await reviewSetupConnection(page,locale); await applySetupReview(page);
    await expect(field(page,'host')).toBeEditable();await expect(field(page,'host')).toBeFocused();
    await expect(page.locator('[data-error-summary]')).toHaveText(locale==='en'?'Setup was not saved. Correct the marked field.':'設定未儲存，請修正標示的欄位。');
    await expect(page.locator('[data-setup-error=host]')).toContainText(locale==='en'?'Host ID must':'主機代號須');
    const recover=page.getByRole('button',{name:locale==='en'?'Check request':'查回請求',exact:true});await expect(recover).toBeHidden();
    expect((await draft(page)).intent).toBeUndefined();
    await expect(page.locator('[data-managed-setup]')).not.toContainText(locale==='en'?'request is unconfirmed':'原設定請求尚未確認');
    state.refused=false;await field(page,'host').fill('local-retry');await reviewSetupConnection(page,locale); await applySetupReview(page);
    await expect(page.locator('[data-setup-sessions]')).toBeVisible();expect(state.posts).toHaveLength(2);expect(state.staged).toHaveLength(1);
    expect(state.posts[1].idempotency_key).not.toBe(state.posts[0].idempotency_key);
    expect(state.posts[1].body.params.secret_ref).toBe(state.posts[0].body.params.secret_ref);
  });
  test('a reload after proven refusal stays editable with no stored intent or token',async({page})=>{
    const state = await managedSetupFixture(page,native,locale);state.refused=true;state.refusalCode='INVALID_TARGET';state.refusalStatus=422;
    await page.goto('/dashboard/'); await openSetupAdvanced(page);await field(page,'connection_url').fill(url);await reviewSetupConnection(page,locale); await applySetupReview(page);
    await expect(field(page,'host')).toBeEditable();await page.reload(); await openSetupAdvanced(page);await expect(field(page,'host')).toBeEditable();
    expect((await draft(page)).intent).toBeUndefined();expect(await storage(page)).not.toMatch(/synthetic-bat-secret|token=/);
    await expect(field(page,'bat_secret')).toHaveValue('');expect(state.posts).toHaveLength(1);
  });
  test('422 without admission proof retains the intent and recovers with the original key',async({page})=>{
    const state = await managedSetupFixture(page,native,locale);state.refused=true;state.admissionRefused=false;state.refusalStatus=422;
    await page.goto('/dashboard/'); await openSetupAdvanced(page);await field(page,'connection_url').fill(url);await reviewSetupConnection(page,locale); await applySetupReview(page);
    await expect(formFor(page).locator('button[type=submit]')).toBeDisabled();
    await expect(page.locator('[data-error-summary]')).toContainText(locale==='en'?'Configuration changed elsewhere':'設定已在別處變更');
    await expect(page.locator('[data-managed-setup]')).not.toContainText(locale==='en'?'request is unconfirmed':'原設定請求尚未確認');
    const original = structuredClone(state.posts[0]);await page.reload(); await openSetupAdvanced(page);
    await expect(formFor(page).locator('button[type=submit]')).toBeDisabled();
    state.refused=false;await page.getByRole('button',{name:locale==='en'?'Check request':'查回請求',exact:true}).click();
    await expect(page.locator('[data-setup-sessions]')).toBeVisible();expect(state.posts[1]).toEqual(original);expect(state.staged).toHaveLength(1);
  });
});

test('a dropped network reply retains the exact request and key after reload',async({page})=>{
  const state=await managedSetupFixture(page,false);state.drop=true;await page.goto('/dashboard/'); await openSetupAdvanced(page);await field(page,'connection_url').fill(url);
  await reviewSetupConnection(page,'en'); await applySetupReview(page);await expect(page.locator('[data-error-summary]')).toHaveText('Connection failed. Check your network and the original request.');
  const original=structuredClone(state.posts[0]);await page.reload(); await openSetupAdvanced(page);await expect(formFor(page).locator('button[type=submit]')).toBeDisabled();
  await page.getByRole('button',{name:'Check request',exact:true}).click();await expect(page.locator('[data-setup-sessions]')).toBeVisible();
  expect(state.posts[1]).toEqual(original);expect(state.staged).toHaveLength(1);
});
for(const locale of ['en','zh-TW']) test(`older Connector validates required fields, endpoint and fingerprint in ${locale}`,async({page})=>{
  // Override navigator before modules load, since this test uses the shared page fixture.
  await page.addInitScript(value=>Object.defineProperty(navigator,'language',{get:()=>value}),locale);
  const state=await managedSetupFixture(page,false,locale);state.discoveryVersion=0;await page.goto('/dashboard/'); await openSetupAdvanced(page);
  await formFor(page).locator('button[type=submit]').click();await expect(page.locator('[data-setup-error=host]')).toBeVisible();
  await field(page,'connection_url').fill(url);await formFor(page).locator('[data-setup-manual]>summary').click();
  await expect(field(page,'profile_id')).toBeVisible();
  for(const [name,value] of [['url','ws://localhost:9876'],['url','wss://localhost:9876?token=never-save-this'],['fingerprint','bad']]) {
    await field(page,name).fill(value);await formFor(page).locator('button[type=submit]').click();await expect(page.locator(`[data-setup-error=${name}]`)).toBeVisible();
    if(name==='url')await field(page,'url').fill('wss://localhost:9876');
  }
  expect(state.posts).toEqual([]);expect(state.staged).toEqual([]);await expect(field(page,'bat_secret')).toHaveValue('synthetic-bat-secret');
  expect(await storage(page)).not.toMatch(/token=|never-save-this/);
});

for(const status of [403,404])test(`setup withdrawal ${status} clears host rows, counts and next action; late success cannot restore them`,async({page})=>{
  const state=await managedSetupFixture(page,false);state.saved=true;await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
  const panel=page.locator('[data-managed-setup]'),refresh=page.locator('#main > .ui-page-header').getByRole('button',{name:'Refresh',exact:true});
  await expect(panel).toContainText('Configured: 1 host');await expect(panel.locator('[data-setup-sessions]')).toBeVisible();
  state.hold=true;await refresh.click();await expect.poll(()=>Boolean(state.release)).toBe(true);
  state.hold=false;state.setupStatus=status;await refresh.click();await expect(panel.locator('[data-error-summary]')).toBeVisible();
  await expect(panel).not.toContainText('Configured:');await expect(panel).not.toContainText('wss://demo.example');await expect(panel.locator('[data-setup-sessions]')).toBeHidden();
  state.release();await page.waitForTimeout(100);await expect(panel).not.toContainText('Configured:');await expect(formFor(page).locator('button[type=submit]')).toBeDisabled();
  expect(state.posts).toEqual([]);expect(state.staged).toEqual([]);
});
test('an expired or rejected staged credential requires a fresh paste while keeping non-secret drafts',async({page})=>{
  const state=await managedSetupFixture(page,false);state.refused=true;state.refusalCode='INVALID_SECRET';state.refusalStatus=422;
  await page.goto('/dashboard/'); await openSetupAdvanced(page);await field(page,'connection_url').fill(url);await reviewSetupConnection(page,'en'); await applySetupReview(page);
  await expect(field(page,'bat_secret')).toBeEditable();await expect(field(page,'bat_secret')).toBeFocused();await expect(field(page,'bat_secret')).toHaveValue('');
  await expect(page.locator('#setup-bat_secret-hint')).toContainText('Paste the connection URL or token again');
  expect((await draft(page)).intent).toBeUndefined();
  state.refused=false;await field(page,'bat_secret').fill('fresh-synthetic-secret');await reviewSetupConnection(page,'en'); await applySetupReview(page);await expect(page.locator('[data-setup-sessions]')).toBeVisible();
  expect(state.staged).toHaveLength(2);expect(state.posts[1].body.params.secret_ref).not.toBe(state.posts[0].body.params.secret_ref);
  expect(await storage(page)).not.toContain('fresh-synthetic-secret');
});

test('a malformed setup refresh cannot enable a new submission',async({page})=>{
  const state=await managedSetupFixture(page,false);await page.goto('/dashboard/'); await openSetupAdvanced(page);await field(page,'connection_url').fill(url);
  state.verification.timeout_s=0;
  await page.locator('#main > .ui-page-header').getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(page.locator('[data-error-summary]')).toBeVisible();
  await expect(formFor(page).locator('button[type=submit]')).toBeDisabled();
  expect(state.posts).toEqual([]);expect(state.staged).toEqual([]);
  state.verification.timeout_s=300;
  await page.locator('#main > .ui-page-header').getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(formFor(page).locator('[data-setup-discover]')).toBeEnabled();
  await expect(field(page,'bat_secret')).toHaveValue('synthetic-bat-secret');
  expect(state.posts).toEqual([]);expect(state.staged).toEqual([]);
});
