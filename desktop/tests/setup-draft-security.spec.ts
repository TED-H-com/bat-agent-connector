import {test, expect, type Page} from '@playwright/test';
import {managedSetupFixture, openSetupAdvanced, reviewSetupConnection, applySetupReview} from './managed-setup-fixture.ts';

const field = (page: Page, name: string) => page.locator(`[data-setup-field="${name}"]`);
const storage = (page: Page) => page.evaluate(() => JSON.stringify({...localStorage}));
const draft = (page: Page) => page.evaluate(() => JSON.parse(localStorage.getItem(
  Object.keys(localStorage).find(key => key.startsWith('batc.managed.setup.'))!)!));
const unsafeRemotes = [
  'https://fixture-user:synthetic-remote-secret@github.com/o/r.git',
  'https://synthetic-remote-secret@github.com/o/r.git',
  'https://github.com/o/r.git?token=synthetic-remote-secret',
  'ssh://git:synthetic-remote-secret@github.com/o/r.git',
];

async function repositoryForm(page: Page) {
  await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
  const form = page.locator('[data-setup-repository]');
  await form.locator('..').locator(':scope > summary').click();
  await field(page, 'repository').fill('o/r');
  await field(page, 'repository_host').fill('demo');
  await field(page, 'workspace_id').fill('ws-1');
  return form;
}

for (const locale of ['en', 'zh-TW']) test.describe(`${locale} setup draft credentials`, () => {
  for (const [index, remote] of unsafeRemotes.entries()) test(`credential remote ${index} never enters storage or admission`, async ({page}) => {
    const state = await managedSetupFixture(page, false, locale);
    const form = await repositoryForm(page);
    await field(page, 'remote_url').fill(remote);
    await field(page, 'github_secret').fill('synthetic-github-secret');
    // Input handlers have completed; a different persisted field proves this is a real draft.
    expect(await storage(page)).toContain('ws-1');
    expect(await storage(page)).not.toContain('synthetic-remote-secret');
    expect(await storage(page)).not.toContain('synthetic-github-secret');
    await form.locator('button[type=submit]').click();
    await expect(field(page, 'remote_url')).toHaveAttribute('aria-invalid', 'true');
    expect(state.posts).toEqual([]); expect(state.staged).toEqual([]);
    expect(await storage(page)).not.toContain('synthetic-remote-secret');
    await page.reload(); await openSetupAdvanced(page);
    await expect(field(page, 'remote_url')).toHaveValue('');
    expect(await storage(page)).not.toContain('synthetic-remote-secret');
  });

  for (const remote of ['https://github.com/o/r.git', 'git@github.com:o/r.git', 'ssh://git@github.com/o/r.git'])
    test(`safe remote survives reload: ${remote}`, async ({page}) => {
      const state = await managedSetupFixture(page, false, locale);
      const form = await repositoryForm(page);
      await field(page, 'remote_url').fill(remote);
      await field(page, 'github_secret').fill('synthetic-github-secret');
      state.refused = true; state.refusalCode = 'INVALID_SECRET';
      await form.locator('button[type=submit]').click();
      await expect(field(page, 'github_secret')).toHaveAttribute('aria-invalid', 'true');
      expect(state.staged).toHaveLength(1); expect(state.posts).toHaveLength(1);
      expect(state.posts[0].body.params.remote_url).toBe(remote);
      expect((await draft(page)).intent).toBeUndefined();
      expect(await storage(page)).not.toContain('synthetic-github-secret');
      await page.reload(); await openSetupAdvanced(page);
      await expect(field(page, 'remote_url')).toHaveValue(remote);
      expect(await storage(page)).not.toContain('synthetic-github-secret');
    });

  test('legacy unsafe intent is scrubbed without replacing its original key or replaying', async ({page}) => {
    const state = await managedSetupFixture(page, false, locale);
    await repositoryForm(page);
    // This is the exact pre-admission shape the older UI wrote, not a valid server receipt.
    await page.evaluate(remote => {
      const key = Object.keys(localStorage).find(key => key.startsWith('batc.managed.setup.'))!;
      const saved = JSON.parse(localStorage.getItem(key)!);
      saved.remote_url = remote;
      saved.intent = {key: 'legacy-original-key', request: {action: 'setup.repository', target: {repository: 'o/r'},
        params: {host: 'demo', workspace_id: 'ws-1', remote_url: remote}, preconditions: {config_revision: 'a'.repeat(64)}}};
      localStorage.setItem(key, JSON.stringify(saved));
    }, unsafeRemotes[0]);
    await page.reload(); await openSetupAdvanced(page);
    await expect(page.locator('[data-managed-setup]')).toContainText(locale === 'en'
      ? 'The original request is kept.' : '原設定請求仍保留。');
    expect(await storage(page)).not.toContain('synthetic-remote-secret');
    expect((await draft(page)).intent.key).toBe('legacy-original-key');
    expect((await draft(page)).intent.request.params).toBeUndefined();
    expect(state.posts).toEqual([]); expect(state.staged).toEqual([]);
    await expect(field(page, 'repository')).toBeDisabled();
  });

  test('paste, staging, submit, expiry and reload keep only the original staged-reference request', async ({page}) => {
    await page.clock.install();
    const state = await managedSetupFixture(page, false, locale); state.lost = true;
    await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
    const link = `wss://demo.example:9876?token=synthetic-bat-secret&fp=${'ab'.repeat(32)}`;
    await field(page, 'connection_url').fill(link);
    await expect(field(page, 'connection_url')).toHaveValue('');
    expect(await storage(page)).not.toContain('synthetic-bat-secret');
    expect((await draft(page)).intent).toBeUndefined();
    await reviewSetupConnection(page, locale);
    await expect(page.locator('[data-setup-review]')).toBeVisible();
    expect(state.staged).toHaveLength(1);
    expect(await storage(page)).not.toContain('synthetic-bat-secret');
    expect(await storage(page)).not.toContain('secret_ref');
    await applySetupReview(page);
    const recover = page.getByRole('button', {name: locale === 'en' ? 'Check request' : '查回請求', exact: true});
    await expect(recover).toBeEnabled();
    expect(state.posts).toHaveLength(1);
    const original = await draft(page);
    expect(original.intent.request).toEqual(state.posts[0].body);
    expect(original.intent.request.params.secret_ref).toBe('setup_' + 'b'.repeat(32));
    expect(await storage(page)).not.toContain('synthetic-bat-secret');
    await page.clock.fastForward(3600_001);
    expect((await draft(page)).intent).toEqual(original.intent);
    expect(await storage(page)).not.toContain('synthetic-bat-secret');
    await page.reload(); await openSetupAdvanced(page);
    await expect(recover).toBeEnabled();
    expect(state.posts).toHaveLength(1); // Reload itself never submits.
    await recover.click();
    await expect(page.locator('[data-setup-operation]')).toHaveAttribute('href', '#/op/' + 'op_' + 'c'.repeat(32));
    expect(state.posts).toHaveLength(2);
    expect(state.posts[1].idempotency_key).toBe(original.intent.key);
    expect(state.posts[1].body).toEqual(original.intent.request);
    expect(state.staged).toHaveLength(1);
    expect(await storage(page)).not.toContain('synthetic-bat-secret');
    await page.reload(); await openSetupAdvanced(page);
    await expect(page.locator('[data-setup-operation]')).toHaveAttribute('href', '#/op/' + 'op_' + 'c'.repeat(32));
    expect(state.posts).toHaveLength(2); // A known operation is recovered by GET.
  });
});
