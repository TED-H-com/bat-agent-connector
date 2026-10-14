import {test, expect, type Page} from '@playwright/test';
import {managedSetupFixture as fixture} from './managed-setup-fixture.ts';


async function expectSetupOperation(page: Page) {
  const details = page.locator('[data-setup-details]');
  const link = details.locator('[data-setup-operation]');
  await expect(link).toHaveText('Setup operation · Done');
  if (!await details.evaluate(node => node.hasAttribute('open'))) await details.locator('summary').click();
  await expect(link).toBeVisible();
}

for (const native of [false,true]) test.describe(native?'Native setup':'Browser setup',()=>{
  test('first run guides setup, preserves non-secret draft, recovers the original key',async({page})=>{
    const state=await fixture(page,native); state.lost=true;
    await page.goto('/dashboard/');
    const panel=page.locator('[data-managed-setup]'),form=panel.locator('[data-setup-host]');
    await expect(panel).toBeVisible();
    await form.locator('[data-setup-manual] > summary').click();
    await form.getByLabel('BAT connection profile',{exact:true}).selectOption('profile-demo');
    await form.getByLabel('Host ID',{exact:true}).fill('demo');
    await form.getByLabel('BAT connection token', {exact:false}).fill('private-bat-secret');
    await page.locator('#main > .ui-page-header').getByRole('button', {name: 'Refresh', exact: true}).click();
    await expect(form.getByLabel('Host ID',{exact:true})).toHaveValue('demo');
    await form.getByRole('button',{name:'Verify and save host'}).click();
    await expect(panel).toContainText('The reply was lost.');
    expect(state.posts).toHaveLength(1);expect(state.staged).toHaveLength(1);
    expect(state.posts[0].body.params).toMatchObject({secret_ref:'setup_'+'b'.repeat(32),writes:false,orchestrate:false});
    expect(JSON.stringify(state.posts)).not.toContain('private-bat-secret');
    expect(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}))).not.toContain('private-bat-secret');
    await page.reload();
    await expect(form.getByLabel('Host ID',{exact:true})).toHaveValue('demo');
    await expect(form.getByRole('button',{name:'Verify and save host'})).toBeDisabled();
    await panel.getByRole('button',{name:'Check request'}).click();
    await expectSetupOperation(page);
    expect(state.posts).toHaveLength(2);
    expect(state.posts[1].idempotency_key).toBe(state.posts[0].idempotency_key);
    expect(state.posts[1].body).toEqual(state.posts[0].body);expect(state.staged).toHaveLength(1);
    await expect(form.locator('[data-setup-field=bat_secret]')).toHaveValue('');
  });
  test('known revision refusal allows a reviewed new intent without pretending success',async({page})=>{
    const state=await fixture(page,native);state.refused=true;
    await page.goto('/dashboard/#/settings');
    const panel=page.locator('[data-managed-setup]'),form=panel.locator('[data-setup-host]');
    await form.locator('[data-setup-manual] > summary').click();
    await form.getByLabel('BAT connection profile',{exact:true}).selectOption('profile-demo');
    await form.getByLabel('Host ID',{exact:true}).fill('demo');
    await form.getByRole('button',{name:'Verify and save host'}).click();
    await expect(panel).toContainText('Read the new configuration.');
    await expect(page.locator('[data-setup-operation]')).toHaveCount(0);
    state.revision='d'.repeat(64);state.refused=false;
    await page.locator('#main > .ui-page-header').getByRole('button', {name: 'Refresh', exact: true}).click();
    await form.getByRole('button',{name:'Verify and save host'}).click();
    await expectSetupOperation(page);
    expect(state.posts[1].idempotency_key).not.toBe(state.posts[0].idempotency_key);
    expect(state.posts[1].body.preconditions.config_revision).toBe(state.revision);
  });
});

async function verificationForm(page: Page) {
  const panel = page.locator('[data-managed-setup]');
  await panel.locator('summary').filter({hasText: '3. Configure verification commands'}).click();
  return panel.locator('[data-setup-verification]');
}
async function fillVerification(form: any) {
  await form.getByLabel('Task Service project name', {exact: true}).fill('docs-project');
  await form.getByLabel('Executable', {exact: true}).fill('python');
  await form.getByLabel('Arguments (one per line)', {exact: true}).fill('-m\ncheck project\n--output=one two\n');
  await form.getByLabel('Timeout in seconds (1–3600)', {exact: true}).fill('120');
}
for (const native of [false, true]) test.describe(native ? 'Native verification setup' : 'Browser verification setup', () => {
  test('keeps other commands and exact argv; unknown response replays the same intent after reload', async ({page}) => {
    const state = await fixture(page, native); state.lost = true; await page.goto('/dashboard/#/settings');
    const panel = page.locator('[data-managed-setup]'), form = await verificationForm(page);
    await expect(form.getByLabel('Timeout in seconds (1–3600)', {exact: true})).toHaveValue('300');
    await expect(form).toContainText('it never runs the command'); await fillVerification(form);
    await form.getByRole('button', {name: 'Save verification command', exact: true}).click();
    await expect(panel).toContainText('The reply was lost.');
    const original = structuredClone(state.posts[0]);
    expect(original.body).toEqual({action: 'setup.verification', target: {}, params: {
      commands: {other: ['uv', 'run', 'pytest', '-q'], 'docs-project': ['python', '-m', 'check project', '--output=one two']}, timeout_s: 120},
      preconditions: {config_revision: 'a'.repeat(64)}});
    expect(state.staged).toEqual([]);
    state.revision = 'b'.repeat(64); await page.reload(); await verificationForm(page);
    await expect(form.getByLabel('Arguments (one per line)', {exact: true})).toHaveValue('-m\ncheck project\n--output=one two\n');
    await expect(form.getByRole('button', {name: 'Save verification command', exact: true})).toBeDisabled();
    await panel.getByRole('button', {name: 'Check request', exact: true}).click();
    await expectSetupOperation(page);
    expect(state.posts[1]).toEqual(original); expect(state.posts.map(post => post.body.action)).toEqual(['setup.verification', 'setup.verification']);
  });
  test('known verification receipts recover by operation ID without another write', async ({page}) => {
    const state = await fixture(page, native); await page.goto('/dashboard/#/settings');
    const form = await verificationForm(page); await fillVerification(form);
    await form.getByRole('button', {name: 'Save verification command', exact: true}).click();
    await expectSetupOperation(page);
    await page.reload(); await expectSetupOperation(page);
    expect(state.posts).toHaveLength(1);
  });
  test('a receipt from another actor is not accepted as verification success', async ({page}) => {
    const state = await fixture(page, native); state.badActor = true; await page.goto('/dashboard/#/settings');
    const form = await verificationForm(page); await fillVerification(form);
    await form.getByRole('button', {name: 'Save verification command', exact: true}).click();
    await expect(page.locator('[data-managed-setup]')).toContainText('The receipt does not match');
    await expect(page.locator('[data-setup-operation]')).toHaveCount(0);
    await expect(form.getByRole('button', {name: 'Save verification command', exact: true})).toBeDisabled();
  });
  test('refresh preserves the reviewed base until an explicit reload adopts newer configuration', async ({page}) => {
    const state = await fixture(page, native); await page.goto('/dashboard/#/settings');
    const panel = page.locator('[data-managed-setup]'), form = await verificationForm(page); await fillVerification(form);
    state.revision = 'b'.repeat(64); state.verification.commands.other = ['newer-command'];
    await page.locator('#main > .ui-page-header').getByRole('button', {name: 'Refresh', exact: true}).click();
    await expect(form).toContainText('Central configuration changed'); await expect(form.getByLabel('Executable', {exact: true})).toHaveValue('python');
    state.refused = true; await form.getByRole('button', {name: 'Save verification command', exact: true}).click();
    await expect(panel).toContainText('Read the new configuration.');
    expect(state.posts[0].body.params.commands.other).toEqual(['uv', 'run', 'pytest', '-q']);
    expect(state.posts[0].body.preconditions.config_revision).toBe('a'.repeat(64));
    state.refused = false; await page.locator('#main > .ui-page-header').getByRole('button', {name: 'Refresh', exact: true}).click();
    await form.getByRole('button', {name: 'Reload saved verification settings', exact: true}).click(); await fillVerification(form);
    await form.getByRole('button', {name: 'Save verification command', exact: true}).click();
    await expectSetupOperation(page);
    expect(state.posts[1].body.params.commands.other).toEqual(['newer-command']);
    expect(state.posts[1].body.preconditions.config_revision).toBe('b'.repeat(64));
    expect(state.posts[1].idempotency_key).not.toBe(state.posts[0].idempotency_key);
  });
  test('verification setup requires both manage scope and an allowed central action', async ({page}) => {
    const state = await fixture(page, native); state.scopes = ['observe']; await page.goto('/dashboard/#/settings');
    let form = await verificationForm(page);
    await expect(form).toContainText('Your central identity is not permitted');
    await expect(form.getByRole('button', {name: 'Save verification command', exact: true})).toBeDisabled();
    await form.evaluate(node => node.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})));
    state.scopes = ['observe', 'manage']; state.allowed = false; await page.reload(); form = await verificationForm(page);
    await expect(form.getByRole('button', {name: 'Save verification command', exact: true})).toBeDisabled();
    expect(state.posts).toEqual([]); expect(state.staged).toEqual([]);
  });
  test('shared clone is an explicit host choice and preserves manual read-only status', async ({page}) => {
    const state = await fixture(page, native); await page.goto('/dashboard/#/settings');
    const form = page.locator('[data-setup-host]');
    await form.locator('summary').filter({hasText: 'Allow managed work'}).click();
    const shared = form.getByRole('checkbox', {name: 'Allow Git worktrees sharing a clone', exact: true});
    await expect(shared).not.toBeChecked(); await shared.check(); await expect(form).toContainText('manual sessions and working directories remain read-only');
    await form.locator('[data-setup-manual] > summary').click();
    await form.getByLabel('BAT connection profile', {exact: true}).selectOption('profile-demo');
    await form.getByLabel('Host ID', {exact: true}).fill('demo'); await form.getByRole('button', {name: 'Verify and save host', exact: true}).click();
    await expectSetupOperation(page);
    expect(state.posts[0].body.params).toMatchObject({shared_clone_worktrees: true, writes: false, orchestrate: false});
  });
  test('navigating away during setup initialization quietly retires the old view', async ({page}) => {
    const state = await fixture(page, native), errors: string[] = []; state.hold = true;
    page.on('pageerror', error => errors.push(error.message)); await page.goto('/dashboard/#/settings');
    await expect.poll(() => Boolean(state.release)).toBe(true);
    await page.evaluate(() => {location.hash = '#/sessions';});
    await expect(page.locator('[data-managed-setup]')).toHaveCount(0);
    state.hold = false; state.release();
    await expect(page.locator('#main h1')).toContainText('Sessions');
    await page.waitForTimeout(100); expect(errors).toEqual([]); expect(state.posts).toEqual([]);
  });
});
