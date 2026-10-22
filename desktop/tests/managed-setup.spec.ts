import {test, expect, type Page} from '@playwright/test';
import {managedSetupFixture, openSetupAdvanced, applySetupReview} from './managed-setup-fixture.ts';
import {STRINGS} from '../src/i18n.js';
import {readFileSync} from 'node:fs';
const setupEvidence = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-setup-evidence.json', import.meta.url), 'utf8'));
for (const locale of ['en', 'zh-TW']) test.describe(locale + ' managed setup', () => {
  test.use({locale: locale === 'en' ? 'en-US' : locale});
  const fixture = (page: Page, native: boolean) => managedSetupFixture(page, native, locale);
  const copy = (key: string, vars: Record<string, string> = {}) => STRINGS[locale][key].replace(/\{(\w+)\}/g, (_, name) => vars[name] || '');

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
      const posts: string[] = [];
      page.on('request', request => {if (request.method() === 'POST') posts.push(request.url());});
      const state = await managedSetupFixture(page, false, locale, {
        state: setupEvidence.states[condition], operation: setupEvidence.operation});
      await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
      await expectSetupOperation(page);
      const panel = page.locator('[data-managed-setup]'), progress = panel.locator('[data-setup-progress]');
      const host = panel.locator(`[data-setup-host-state="${condition}"]`);
      await expect(host).toContainText(copy(`setup_connection_${condition}`));
      if (condition === 'connected') await expect(progress).toHaveText(copy('setup_ready'));
      else {
        // Assert the original contradiction first, before checking new evidence copy.
        await expect(progress).not.toHaveText(copy('setup_ready'));
        await expect(panel).not.toContainText(copy('setup_ready'));
        await expect(progress).toContainText(locale === 'zh-TW' ? 'Connector 已驗證連線並儲存設定' : 'Connector verified the connection and saved the configuration');
        await expect(progress.locator('time')).toHaveAttribute('datetime', new Date(setupEvidence.operation.updated_at * 1000).toISOString());
        await expect(progress.locator('time')).not.toHaveText('');
        await expect(host).toContainText(copy(condition === 'unreachable' ? 'setup_connection_action' : 'setup_connection_checking_help'));
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
      await form.getByRole('button',{name:copy('setup_save_host')}).click(); await applySetupReview(page);
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
      await form.getByRole('button',{name:copy('setup_save_host')}).click(); await applySetupReview(page);
      await expect(panel).toContainText(copy('error_code_CONFIGURATION_CHANGED'));
      await expect(page.locator('[data-setup-operation]')).toHaveCount(0);
      state.revision='d'.repeat(64);state.refused=false;
      await page.locator('#main > .ui-page-header').getByRole('button', {name: copy('pm_refresh'), exact: true}).click();
      await form.getByRole('button',{name:copy('setup_save_host')}).click(); await applySetupReview(page);
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
      await form.getByLabel(copy('setup_host_name'), {exact: true}).fill('demo'); await form.getByRole('button', {name: copy('setup_save_host'), exact: true}).click(); await applySetupReview(page);
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
