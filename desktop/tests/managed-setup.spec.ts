import {test, expect, type Page} from '@playwright/test';
import {managedSetupFixture, reviewSetupConnection, expectLegacyProfileFallback, openSetupAdvanced, applySetupReview} from './managed-setup-fixture.ts';
import {readFileSync} from 'node:fs';
import {mountSetupObservation} from './setup-observation-fixture.ts';
// Literal expectations remain independent of the runtime translation producer.
const labels: Record<string, Record<string, string>> = {
  en: {
    setup_operation_receipt: 'Setup operation · {status}', setup_operation_done: 'Done',
    setup_ready: 'Setup is complete. You’re ready to go.', setup_profile: 'BAT connection profile',
    setup_host_name: 'Host ID', setup_bat_token: 'BAT connection token (optional with a profile)', pm_refresh: 'Refresh',
    error_generic: 'This request could not be completed. Check Details.', setup_save_host: 'Review connection', setup_recover: 'Check request',
    error_code_CONFIGURATION_CHANGED: 'Configuration changed elsewhere. Refresh before trying again.',
    setup_verification_title: '3. Configure verification commands', setup_verification_project: 'Task Service project name',
    setup_verification_executable: 'Executable', setup_verification_arguments: 'Arguments (one per line)',
    setup_verification_timeout: 'Timeout in seconds (1–3600)',
    setup_verification_help: 'Configure an explicit command by Task Service project name. Saving changes configuration only; it never runs the command. Other project commands are preserved. The timeout applies to every project.',
    setup_save_verification: 'Save verification command', setup_receipt_mismatch: 'The receipt does not match the original configuration request.',
    setup_verification_changed: 'Connector configuration changed; your draft is preserved. Reload saved verification settings, review, and save again.',
    setup_verification_reload: 'Reload saved verification settings', setup_scope_required: 'Your Connector identity is not permitted to change this setting.',
    setup_managed_work: 'Allow managed work', setup_shared_clone: 'Allow Git worktrees sharing a clone',
    setup_shared_clone_help: 'New worktrees share Git metadata with the source clone. This requires an explicit choice; manual sessions and working directories remain read-only.',
    sessions_title: 'Sessions',
  },
  'zh-TW': {
    setup_operation_receipt: '設定操作 · {status}', setup_operation_done: '完成', setup_ready: '設定完成，可以開始使用了。',
    setup_profile: 'BAT 連線設定檔', setup_host_name: '主機代號', setup_bat_token: 'BAT 連線權杖（使用設定檔時可留白）', pm_refresh: '重新整理',
    error_generic: '無法完成此要求，請查看詳細資料。', setup_save_host: '預覽連線', setup_recover: '查回請求',
    error_code_CONFIGURATION_CHANGED: '設定已在別處變更，請重新整理後再試。', setup_verification_title: '3. 設定驗證命令',
    setup_verification_project: '任務服務專案名稱', setup_verification_executable: '執行程式', setup_verification_arguments: '參數（每行一個）',
    setup_verification_timeout: '逾時秒數（1–3600）', setup_verification_help: '依任務服務的專案名稱設定明確命令。儲存只變更設定，不執行命令；其他專案的命令會保留。逾時秒數套用到所有專案。',
    setup_save_verification: '儲存驗證命令', setup_receipt_mismatch: '回執與原設定請求不一致。',
    setup_verification_changed: 'Connector 設定已變更，草稿已保留；請重新載入已存的驗證設定，再檢視並儲存。',
    setup_verification_reload: '重新載入已存驗證設定', setup_scope_required: '目前 Connector 帳號無權修改此設定。',
    setup_managed_work: '允許受管工作', setup_shared_clone: '允許共用 Git 複本的獨立工作目錄',
    setup_shared_clone_help: '新的獨立工作目錄會與來源複本共用 Git 中繼資料。只有明確開啟才允許；人工工作階段和工作目錄仍保持唯讀。',
    sessions_title: '工作階段',
  },
};
const setupEvidence = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-setup-evidence.json', import.meta.url), 'utf8'));
for(const locale of ['en','zh-TW'])for(const width of [390,1440])for(const outcome of ['unchanged','503'])
  test(`${locale} multiple hosts share one ${outcome} deadline GET at ${width}px`,async({page})=>{
    await page.setViewportSize({width,height:900});
    await page.clock.install({time:new Date(1800000600000)});await page.clock.pauseAt(1800000600000);
    const body=setupEvidence.observations.connected.multiple_hosts;
    const f=await mountSetupObservation(page,body,setupEvidence.operation,locale,async(route,n)=>{
      await route.fulfill(n<=2||outcome==='unchanged'?{status:200,json:body}:{status:503,json:{}});
    });
    const count=()=>f.reads.filter(path=>path.endsWith('/managed/setup')).length;
    expect(count()).toBe(2);
    await page.clock.runFor(180000);await expect.poll(count).toBe(3);
    const expected=locale==='en'?`Ready · 3 min ago · Could not confirm (${outcome==='503'?'503':'OBSERVATION_NOT_RENEWED'})`:
      `已就緒 · 3 分鐘前 · 無法確認（${outcome==='503'?'503':'OBSERVATION_NOT_RENEWED'}）`;
    for(const name of ['fixture','fixture-second'])await expect(f.panel.locator(`[data-host="${name}"][data-setup-facet="connection"] [data-setup-fact-value]`)).toHaveText(expected);
    await page.clock.runFor(1000);expect(count()).toBe(3);
    await page.clock.fastForward(3600000);expect(count()).toBe(3);
    expect(f.posts).toEqual([]);
  });

for (const locale of ['en', 'zh-TW']) for (const width of [390,1440])
  for (const outcome of ['renewed','unchanged','503','refused']) test(`${locale} Settings ${outcome} observation renewal at ${width}px`, async ({page}) => {
    await page.setViewportSize({width,height:900});
    await page.clock.install({time:new Date(1800000600000)}); await page.clock.pauseAt(1800000600000);
    let release:()=>void;
    const held=new Promise<void>(resolve=>release=resolve);
    const body=structuredClone(setupEvidence.observations.connected.setup);
    const f=await mountSetupObservation(page,body,setupEvidence.operation,locale,async(route,n)=>{
      if(n<=2){await route.fulfill({status:200,json:body});return;}
      await held;
      const refusal=setupEvidence.observations.connected.read_refusal;
      await route.fulfill(outcome==='refused'?{status:refusal.status,json:refusal.body}:outcome==='503'?{status:503,json:{}}:
        {status:200,json:outcome==='renewed'?setupEvidence.observations.renewed.setup:body});
    });
    const count=()=>f.reads.filter(path=>path.endsWith('/managed/setup')).length;
    const link=f.panel.locator('[data-setup-operation]'),draft=f.panel.locator('[data-setup-field="host"]');
    await expect(f.fact('connection')).toHaveText(locale==='en'?'Ready':'已就緒');
    await expect(draft).toHaveValue('retained-draft'); await link.focus();
    const original=await link.elementHandle();
    const stored=await page.evaluate(()=>localStorage.getItem('batc.managed.setup.observation-fixture'));
    expect(count()).toBe(2);
    try {
      await page.clock.runFor(179999); expect(count()).toBe(2);
      await page.clock.runFor(1); await expect.poll(count).toBe(3);
      await expect(f.fact('connection')).toHaveText(locale==='en'?'Ready · 3 min ago · Checking':'已就緒 · 3 分鐘前 · 檢查中');
      await page.clock.runFor(1000);expect(count()).toBe(3);
      await expect(f.fact('connection')).toHaveText(locale==='en'?'Ready · 3 min ago · Checking':'已就緒 · 3 分鐘前 · 檢查中');
      await expect(link).toBeFocused();
      const source=f.panel.locator('[data-setup-facet="connection"] details summary');
      await source.focus(); release!();
      const expected=outcome==='renewed'?(locale==='en'?'Ready':'已就緒'):
        (locale==='en'?`Ready · 3 min ago · Could not confirm (${outcome==='unchanged'?'OBSERVATION_NOT_RENEWED':outcome==='refused'?'FORBIDDEN':'503'})`:
          `已就緒 · 3 分鐘前 · 無法確認（${outcome==='unchanged'?'OBSERVATION_NOT_RENEWED':outcome==='refused'?'FORBIDDEN':'503'}）`);
      await expect(f.fact('connection')).toHaveText(expected);
      await expect(source).toBeFocused();
      await expect(source.locator('..')).toHaveAttribute('open','');
      await link.focus(); await expect(draft).toHaveValue('retained-draft');
      expect(await original!.evaluate(node=>node===document.activeElement)).toBe(true);
      expect(await page.evaluate(()=>localStorage.getItem('batc.managed.setup.observation-fixture'))).toBe(stored);
      const progress=f.panel.locator('[data-setup-progress]');
      if(outcome!=='renewed'){
        await expect(progress).toContainText(locale==='en'?'Connector verified the connection and saved the configuration':'Connector 已驗證連線並儲存設定');
        await expect(progress.locator('time')).toHaveAttribute('datetime',new Date(setupEvidence.operation.updated_at*1000).toISOString());
        await page.clock.fastForward(3600000); expect(count()).toBe(3);
        await expect(f.fact('connection')).not.toContainText(locale==='en'?'Checking':'檢查中');
        if(outcome==='unchanged'){
          body.revision='c'.repeat(64); // Unrelated config changes do not renew a source observation.
          await page.evaluate(()=>(window as any).__setupObservation.reload());
          expect(count()).toBe(4);await page.clock.fastForward(60000);expect(count()).toBe(4);
        }
      } else {
        await page.clock.runFor(180000); await expect.poll(count).toBe(4);
        await page.clock.fastForward(3600000); expect(count()).toBe(4);
      }
      expect(f.posts).toEqual([]);
      expect(f.reads.every(path=>path.endsWith('/managed/setup')||path.includes('/operations/'))).toBe(true);
    } finally {release!();}
  });

for (const locale of ['en', 'zh-TW']) test.describe(locale + ' managed setup', () => {
  test.use({locale: locale === 'en' ? 'en-US' : locale});
  const fixture = (page: Page, native: boolean) => managedSetupFixture(page, native, locale);
  const copy = (key: string, vars: Record<string, string> = {}) => labels[locale][key].replace(/\{(\w+)\}/g, (_, name) => vars[name] || '');

  async function expectSetupOperation(page: Page) {
    const details = page.locator('[data-setup-details]');
    const link = details.locator('[data-setup-operation]');
    await expect(link).toHaveText(copy('setup_operation_receipt', {status: copy('setup_operation_done')}));
    if (!await details.evaluate(node => node.hasAttribute('open'))) await details.locator('summary').click();
    await expect(link).toBeVisible();
  }

  for (const width of [390, 1440]) for (const condition of ['unreachable', 'checking', 'connected'])
    test(`past setup evidence and current ${condition} at ${width}px`, async ({page}) => {
      await page.setViewportSize({width, height: 900});
      await page.clock.install({time:new Date(1800000600000)}); await page.clock.pauseAt(1800000600000);
      const posts: string[] = [];
      page.on('request', request => {if (request.method() === 'POST') posts.push(request.url());});
      const state = await managedSetupFixture(page, false, locale, {
        state: setupEvidence.states[condition], operation: setupEvidence.operation});
      await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
      await expectSetupOperation(page);
      const panel = page.locator('[data-managed-setup]'), progress = panel.locator('[data-setup-progress]');
      const host = panel.locator('[data-setup-facet="connection"]');
      await expect(host.locator('[data-setup-fact-value]')).toHaveText(condition === 'connected'
        ? locale === 'en' ? 'Ready' : '已就緒'
        : locale === 'en' ? `Unavailable · Last attempt: ${condition === 'checking' ? '15 min ago' : 'just now'}; host unreachable, last successful observation is stale`
        : `不可用 · 上次嘗試：${condition === 'checking' ? '15 分鐘前' : '剛剛'}；無法連至主機，上次成功觀察已過期`);
      if (condition === 'connected') await expect(progress).toHaveText(copy('setup_ready'));
      else {
        // Assert the original contradiction first, before checking new evidence copy.
        await expect(progress).not.toHaveText(copy('setup_ready'));
        await expect(panel).not.toContainText(copy('setup_ready'));
        await expect(progress).toContainText(locale === 'zh-TW' ? 'Connector 已驗證連線並儲存設定' : 'Connector verified the connection and saved the configuration');
        await expect(progress.locator('time')).toHaveAttribute('datetime', new Date(setupEvidence.operation.updated_at * 1000).toISOString());
        await expect(progress.locator('time')).not.toHaveText('');
        await expect(host).toContainText(locale === 'en' ? 'The Inventory read failed; the last successful observation is stale.' : 'Inventory 讀取失敗；上次成功觀察已過期。');
      }
      await expect(host.locator('time')).toHaveAttribute('datetime', new Date(setupEvidence.states[condition].hosts[0].connection_checked_at * 1000).toISOString());
      await expect(host.locator('time')).not.toHaveText('');
      await expect(panel.locator('[data-setup-operation]')).toHaveAttribute('href', `#/op/${setupEvidence.operation.operation_id}`);
      expect(state.posts).toEqual([]); expect(state.staged).toEqual([]); expect(posts).toEqual([]);
    });

  for (const native of [false,true]) test.describe(native?'Native setup':'Browser setup',()=>{
    test('first run guides setup, preserves non-secret draft, recovers the original key',async({page})=>{
      const state=await fixture(page,native); state.lost=true;
      await page.goto('/dashboard/'); await openSetupAdvanced(page);
      const panel=page.locator('[data-managed-setup]'),form=panel.locator('[data-setup-host]');
      await expect(panel).toBeVisible();
      await form.locator('[data-setup-manual] > summary').click();
      await form.getByLabel(copy('setup_profile'),{exact:true}).selectOption('profile-demo');
      await form.getByLabel(copy('setup_host_name'),{exact:true}).fill('demo');
      await form.getByLabel(copy('setup_bat_token'), {exact:false}).fill('private-bat-secret');
      await page.locator('#main > .ui-page-header').getByRole('button', {name: copy('pm_refresh'), exact: true}).click();
      await expect(form.getByLabel(copy('setup_host_name'),{exact:true})).toHaveValue('demo');
      await reviewSetupConnection(page,locale); await applySetupReview(page);
      await expect(panel).toContainText(copy('error_generic'));
      expect(state.posts).toHaveLength(1);expect(state.staged).toHaveLength(1);
      expect(state.posts[0].body.params).toMatchObject({secret_ref:'setup_'+'b'.repeat(32),writes:false,orchestrate:false});
      expect(JSON.stringify(state.posts)).not.toContain('private-bat-secret');
      expect(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}))).not.toContain('private-bat-secret');
      await page.reload(); await openSetupAdvanced(page);
      await expect(form.getByLabel(copy('setup_host_name'),{exact:true})).toHaveValue('demo');
      await expect(form.getByRole('button',{name:copy('setup_save_host')})).toBeDisabled();
      await panel.getByRole('button',{name:copy('setup_recover')}).click();
      await expectSetupOperation(page);
      expect(state.posts).toHaveLength(2);
      expect(state.posts[1].idempotency_key).toBe(state.posts[0].idempotency_key);
      expect(state.posts[1].body).toEqual(state.posts[0].body);expect(state.staged).toHaveLength(1);
      await expect(form.locator('[data-setup-field=bat_secret]')).toHaveValue('');
    });
    test('known revision refusal allows a reviewed new intent without pretending success',async({page})=>{
      const state=await fixture(page,native);state.refused=true;
      await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
      const panel=page.locator('[data-managed-setup]'),form=panel.locator('[data-setup-host]');
      await form.locator('[data-setup-manual] > summary').click();
      await form.getByLabel(copy('setup_profile'),{exact:true}).selectOption('profile-demo');
      await form.getByLabel(copy('setup_host_name'),{exact:true}).fill('demo');
      await reviewSetupConnection(page,locale); await applySetupReview(page);
      await expect(panel).toContainText(copy('error_code_CONFIGURATION_CHANGED'));
      await expect(page.locator('[data-setup-operation]')).toHaveCount(0);
      state.revision='d'.repeat(64);state.refused=false;
      await page.locator('#main > .ui-page-header').getByRole('button', {name: copy('pm_refresh'), exact: true}).click();
      await expect(page.locator('[data-setup-discovery]')).toBeHidden();
      await reviewSetupConnection(page,locale); await applySetupReview(page);
      await expectSetupOperation(page);
      expect(state.posts[1].idempotency_key).not.toBe(state.posts[0].idempotency_key);
      expect(state.posts[1].body.preconditions.config_revision).toBe(state.revision);
    });
  });

  async function verificationForm(page: Page) {
    const panel = page.locator('[data-managed-setup]');
    await panel.locator('summary').filter({hasText: copy('setup_verification_title')}).click();
    return panel.locator('[data-setup-verification]');
  }
  async function fillVerification(form: any) {
    await form.getByLabel(copy('setup_verification_project'), {exact: true}).fill('docs-project');
    await form.getByLabel(copy('setup_verification_executable'), {exact: true}).fill('python');
    await form.getByLabel(copy('setup_verification_arguments'), {exact: true}).fill('-m\ncheck project\n--output=one two\n');
    await form.getByLabel(copy('setup_verification_timeout'), {exact: true}).fill('120');
  }
  for (const native of [false, true]) test.describe(native ? 'Native verification setup' : 'Browser verification setup', () => {
    test('keeps other commands and exact argv; unknown response replays the same intent after reload', async ({page}) => {
      const state = await fixture(page, native); state.lost = true; await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
      const panel = page.locator('[data-managed-setup]'), form = await verificationForm(page);
      await expect(form.getByLabel(copy('setup_verification_timeout'), {exact: true})).toHaveValue('300');
      await expect(form).toContainText(copy('setup_verification_help')); await fillVerification(form);
      await form.getByRole('button', {name: copy('setup_save_verification'), exact: true}).click();
      await expect(panel).toContainText(copy('error_generic'));
      const original = structuredClone(state.posts[0]);
      expect(original.body).toEqual({action: 'setup.verification', target: {}, params: {
        commands: {other: ['uv', 'run', 'pytest', '-q'], 'docs-project': ['python', '-m', 'check project', '--output=one two']}, timeout_s: 120},
        preconditions: {config_revision: 'a'.repeat(64)}});
      expect(state.staged).toEqual([]);
      state.revision = 'b'.repeat(64); await page.reload(); await openSetupAdvanced(page); await verificationForm(page);
      await expect(form.getByLabel(copy('setup_verification_arguments'), {exact: true})).toHaveValue('-m\ncheck project\n--output=one two\n');
      await expect(form.getByRole('button', {name: copy('setup_save_verification'), exact: true})).toBeDisabled();
      await panel.getByRole('button', {name: copy('setup_recover'), exact: true}).click();
      await expectSetupOperation(page);
      expect(state.posts[1]).toEqual(original); expect(state.posts.map(post => post.body.action)).toEqual(['setup.verification', 'setup.verification']);
    });
    test('known verification receipts recover by operation ID without another write', async ({page}) => {
      const state = await fixture(page, native); await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
      const form = await verificationForm(page); await fillVerification(form);
      await form.getByRole('button', {name: copy('setup_save_verification'), exact: true}).click();
      await expectSetupOperation(page);
      await page.reload(); await openSetupAdvanced(page); await expectSetupOperation(page);
      expect(state.posts).toHaveLength(1);
    });
    test('a receipt from another actor is not accepted as verification success', async ({page}) => {
      const state = await fixture(page, native); state.badActor = true; await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
      const form = await verificationForm(page); await fillVerification(form);
      await form.getByRole('button', {name: copy('setup_save_verification'), exact: true}).click();
      await expect(page.locator('[data-managed-setup]')).toContainText(copy('setup_receipt_mismatch'));
      await expect(page.locator('[data-setup-operation]')).toHaveCount(0);
      await expect(form.getByRole('button', {name: copy('setup_save_verification'), exact: true})).toBeDisabled();
    });
    test('refresh preserves the reviewed base until an explicit reload adopts newer configuration', async ({page}) => {
      const state = await fixture(page, native); await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
      const panel = page.locator('[data-managed-setup]'), form = await verificationForm(page); await fillVerification(form);
      state.revision = 'b'.repeat(64); state.verification.commands.other = ['newer-command'];
      await page.locator('#main > .ui-page-header').getByRole('button', {name: copy('pm_refresh'), exact: true}).click();
      await expect(form).toContainText(copy('setup_verification_changed')); await expect(form.getByLabel(copy('setup_verification_executable'), {exact: true})).toHaveValue('python');
      state.refused = true; await form.getByRole('button', {name: copy('setup_save_verification'), exact: true}).click();
      await expect(panel).toContainText(copy('error_code_CONFIGURATION_CHANGED'));
      expect(state.posts[0].body.params.commands.other).toEqual(['uv', 'run', 'pytest', '-q']);
      expect(state.posts[0].body.preconditions.config_revision).toBe('a'.repeat(64));
      state.refused = false; await page.locator('#main > .ui-page-header').getByRole('button', {name: copy('pm_refresh'), exact: true}).click();
      await form.getByRole('button', {name: copy('setup_verification_reload'), exact: true}).click(); await fillVerification(form);
      await form.getByRole('button', {name: copy('setup_save_verification'), exact: true}).click();
      await expectSetupOperation(page);
      expect(state.posts[1].body.params.commands.other).toEqual(['newer-command']);
      expect(state.posts[1].body.preconditions.config_revision).toBe('b'.repeat(64));
      expect(state.posts[1].idempotency_key).not.toBe(state.posts[0].idempotency_key);
    });
    test('verification setup requires both manage scope and an allowed central action', async ({page}) => {
      const state = await fixture(page, native); state.scopes = ['observe']; await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
      let form = await verificationForm(page);
      await expect(form).toContainText(copy('setup_scope_required'));
      await expect(form.getByRole('button', {name: copy('setup_save_verification'), exact: true})).toBeDisabled();
      await form.evaluate(node => node.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})));
      state.scopes = ['observe', 'manage']; state.allowed = false; await page.reload(); await openSetupAdvanced(page); form = await verificationForm(page);
      await expect(form.getByRole('button', {name: copy('setup_save_verification'), exact: true})).toBeDisabled();
      expect(state.posts).toEqual([]); expect(state.staged).toEqual([]);
    });
    test('shared clone is an explicit host choice and preserves manual read-only status', async ({page}) => {
      const state = await fixture(page, native); await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
      const form = page.locator('[data-setup-host]');
      await form.locator('summary').filter({hasText: copy('setup_managed_work')}).click();
      const shared = form.getByRole('checkbox', {name: copy('setup_shared_clone'), exact: true});
      await expect(shared).not.toBeChecked(); await shared.check(); await expect(form).toContainText(copy('setup_shared_clone_help'));
      await form.locator('[data-setup-manual] > summary').click();
      await form.getByLabel(copy('setup_profile'), {exact: true}).selectOption('profile-demo');
      await form.getByLabel(copy('setup_host_name'), {exact: true}).fill('demo'); await reviewSetupConnection(page,locale); await applySetupReview(page);
      await expectSetupOperation(page);
      expect(state.posts[0].body.params).toMatchObject({shared_clone_worktrees: true, writes: false, orchestrate: false});
    });
    test('navigating away during setup initialization quietly retires the old view', async ({page}) => {
      const state = await fixture(page, native), errors: string[] = []; state.hold = true;
      page.on('pageerror', error => errors.push(error.message)); await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
      await expect.poll(() => Boolean(state.release)).toBe(true);
      await page.evaluate(() => {location.hash = '#/sessions';});
      await expect(page.locator('[data-managed-setup]')).toHaveCount(0);
      state.hold = false; state.release();
      await expect(page.locator('#main h1')).toContainText(copy('sessions_title'));
      await page.waitForTimeout(100); expect(errors).toEqual([]); expect(state.posts).toEqual([]);
    });
  });

});

for (const locale of ['en', 'zh-TW']) test(`${locale} older Connector keeps the typed profile fallback`, async ({page}) => {
  await expectLegacyProfileFallback(page, locale);
});
