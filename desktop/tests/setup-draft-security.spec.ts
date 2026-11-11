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

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) test.describe(`${locale} ${native ? 'IPC' : 'HTTP'} setup draft credentials`, () => {
  const originalCheck = (page: Page) => page.getByRole('button', {name: locale === 'en' ? 'Check original request' : '查回原請求', exact: true});
  const discardDraft = (page: Page) => page.getByRole('button', {name: locale === 'en' ? 'Discard saved draft' : '捨棄儲存的草稿', exact: true});
  async function legacyDraft(page: Page, known = false, key = 'legacy-original-key') {
    await page.setViewportSize({width: 390, height: 844});
    const state = await managedSetupFixture(page, native, locale);
    await repositoryForm(page);
    const request = {action: 'setup.repository', target: {repository: 'o/r'}, params: {
      host: 'demo', workspace_id: 'ws-1', remote_url: unsafeRemotes[0]}, preconditions: {config_revision: 'a'.repeat(64)}};
    state.operation = {...request, actor: 'reader', operation_id: 'op_' + 'c'.repeat(32), idempotency_key: key, status: 'succeeded'};
    await page.evaluate(({request, known, key}) => {
      const storageKey = Object.keys(localStorage).find(key => key.startsWith('batc.managed.setup.'))!;
      const saved = JSON.parse(localStorage.getItem(storageKey)!);
      saved.intent = {key, request, ...(known ? {operation_id: 'op_' + 'c'.repeat(32)} : {})};
      localStorage.setItem(storageKey, JSON.stringify(saved));
    }, {request, known, key});
    return state;
  }
  async function assertReadOnly(page: Page, state: Awaited<ReturnType<typeof managedSetupFixture>>) {
    expect(state.posts).toHaveLength(0); expect(state.staged).toHaveLength(0);
    expect(await storage(page)).not.toContain('synthetic-remote-secret');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }

  test('non-secret refusal recovery releases the form lock', async ({page}) => {
    const state = await legacyDraft(page); state.refused = true;
    await page.evaluate(() => {
      const key = Object.keys(localStorage).find(key => key.startsWith('batc.managed.setup.'))!;
      const saved = JSON.parse(localStorage.getItem(key)!);
      saved.intent.request.params.remote_url = 'https://github.com/o/r';
      localStorage.setItem(key, JSON.stringify(saved));
    });
    await page.reload(); await openSetupAdvanced(page);
    await page.getByRole('button', {name: locale === 'en' ? 'Check request' : '查回請求', exact: true}).click();
    await expect(field(page, 'repository')).toBeEnabled();
    expect((await draft(page)).intent).toBeUndefined();
    expect(state.posts).toHaveLength(1); expect(state.staged).toHaveLength(0);
    expect(state.posts[0].idempotency_key).toBe('legacy-original-key');
  });

  for (const status of ['succeeded', 'failed', 'cancelled', 'accepted', 'running', 'uncertain', 'needs_attention', 'idle', 'ACK'])
    test(`scrubbed original ${status} reconciles read-only and survives reload`, async ({page}) => {
      const state = await legacyDraft(page); state.operation.status = status;
      await page.reload(); await openSetupAdvanced(page); await originalCheck(page).click();
      await expect(page.locator('[data-setup-operation]')).toHaveAttribute('href', '#/op/' + state.operation.operation_id);
      const terminal = ['succeeded', 'failed', 'cancelled'].includes(status);
      const next = page.getByRole('button', {name: locale === 'en' ? 'Change settings' : '修改設定', exact: true});
      if (terminal) await expect(next).toBeEnabled(); else await expect(originalCheck(page)).toBeEnabled();
      const receipt = page.locator('[data-setup-operation]');
      if (status !== 'succeeded') await expect(receipt).not.toContainText(locale === 'en' ? 'Completed' : '已完成');
      await expect(field(page, 'repository')).toBeDisabled(); await expect(discardDraft(page)).toBeHidden();
      expect((await draft(page)).intent.key).toBe('legacy-original-key');
      await page.reload(); await openSetupAdvanced(page);
      await expect(receipt).toHaveCount(1);
      expect(state.operationReads.at(-1).path).toBe('/operations/' + state.operation.operation_id);
      if (terminal) {await next.click(); await expect(field(page, 'repository')).toBeEnabled();}
      else {await originalCheck(page).click(); await expect(field(page, 'repository')).toBeDisabled();}
      await assertReadOnly(page, state);
    });

  test('scrubbed missing request requires a fresh complete scan after reload and deliberate discard, then a new key', async ({page}) => {
    const state = await legacyDraft(page); state.operation = null;
    await page.reload(); await openSetupAdvanced(page); await originalCheck(page).click();
    await expect(discardDraft(page)).toBeEnabled();
    await page.reload(); await openSetupAdvanced(page); await expect(discardDraft(page)).toBeHidden();
    state.operationPages = [{operations: [], next_before: 123, next_before_id: 'op_' + 'd'.repeat(32)},
      {operations: [], next_before: null, next_before_id: null}];
    await originalCheck(page).click(); await expect(discardDraft(page)).toBeEnabled();
    expect(state.operationReads.at(-1).path).toContain('before=123&before_id=op_');
    await assertReadOnly(page, state);
    await discardDraft(page).click(); await expect(field(page, 'repository')).toBeEnabled();
    await page.reload(); await openSetupAdvanced(page);
    expect((await draft(page)).intent).toBeUndefined(); await expect(originalCheck(page)).toBeHidden();
    await assertReadOnly(page, state);
    const form = page.locator('[data-setup-repository]'); await form.locator('..').locator(':scope > summary').click();
    await field(page, 'remote_url').fill('https://github.com/o/r');
    await form.locator('button[type=submit]').click();
    await expect(page.locator('[data-setup-operation]')).toHaveCount(1);
    expect(state.posts).toHaveLength(1); expect(state.staged).toHaveLength(0);
    expect(state.posts[0].idempotency_key).not.toBe('legacy-original-key');
  });

  for (const problem of ['page-cap', 'offline', 'error', 'malformed', 'cursor', 'actor', 'key', 'duplicate'])
    test(`scrubbed ${problem} stays locked without discard and can check again`, async ({page}) => {
      const known = problem === 'key'; const state = await legacyDraft(page, known);
      if (problem === 'key') state.operation.idempotency_key = 'other-key';
      if (problem === 'actor') state.operation.actor = 'other-actor';
      if (problem === 'offline' || problem === 'error') state.operationReadStatus = problem === 'offline' ? 0 : 503;
      if (problem === 'malformed') state.operationPages = [{operations: []}];
      if (problem === 'cursor') state.operationPages = [{operations: [], next_before: 123, next_before_id: 'invalid'}];
      if (problem === 'duplicate') state.operationPages = [{operations: [state.operation, state.operation], next_before: null, next_before_id: null}];
      if (problem === 'page-cap') state.operationPages = Array.from({length: 20}, (_, index) => ({operations: [], next_before: 100 - index, next_before_id: 'op_' + 'd'.repeat(32)}));
      await page.reload(); await openSetupAdvanced(page);
      if (!known) await originalCheck(page).click();
      await expect(originalCheck(page)).toBeEnabled(); await expect(discardDraft(page)).toBeHidden();
      await expect(field(page, 'repository')).toBeDisabled();
      expect((await draft(page)).intent.key).toBe('legacy-original-key');
      if (problem !== 'page-cap') {
        await page.reload(); await openSetupAdvanced(page);
        await expect(originalCheck(page)).toBeEnabled(); await expect(discardDraft(page)).toBeHidden();
        await expect(field(page, 'repository')).toBeDisabled();
        expect((await draft(page)).intent.key).toBe('legacy-original-key');
      }
      state.operationReadStatus = 200; state.operation.actor = 'reader'; state.operation.idempotency_key = 'legacy-original-key';
      await originalCheck(page).click(); await expect(page.locator('[data-setup-operation]')).toHaveCount(1);
      if (problem === 'page-cap') expect(state.operationReads[20].path).toContain('before=81&before_id=op_');
      await page.reload(); await openSetupAdvanced(page); await expect(page.locator('[data-setup-operation]')).toHaveCount(1);
      await assertReadOnly(page, state);
    });

  test('scrubbed capped scan restarts after reload without claiming an absent request', async ({page}) => {
    const state = await legacyDraft(page);
    state.operationPages = Array.from({length: 20}, (_, index) => ({operations: [], next_before: 100 - index, next_before_id: 'op_' + 'd'.repeat(32)}));
    await page.reload(); await openSetupAdvanced(page); await originalCheck(page).click();
    await expect(originalCheck(page)).toBeEnabled(); await expect(discardDraft(page)).toBeHidden();
    await page.reload(); await openSetupAdvanced(page); await expect(discardDraft(page)).toBeHidden();
    await originalCheck(page).click(); await expect(page.locator('[data-setup-operation]')).toHaveCount(1);
    expect(state.operationReads[20].path).not.toContain('before='); await assertReadOnly(page, state);
  });

  test('scrubbed missing key gives actionable locked guidance across reload', async ({page}) => {
    const state = await legacyDraft(page, false, '');
    for (let reload = 0; reload < 2; reload++) {
      await page.reload(); await openSetupAdvanced(page);
      await expect(page.locator('[data-managed-setup]')).toContainText(locale === 'en' ? 'No usable original request key was retained' : '未保留可用的原請求代號');
      await expect(originalCheck(page)).toBeHidden(); await expect(discardDraft(page)).toBeHidden();
      await expect(field(page, 'repository')).toBeDisabled();
    }
    expect(state.operationReads).toHaveLength(0); await assertReadOnly(page, state);
  });

  for (const switchTo of ['discard', 'account', 'connection']) test(`late scrubbed check after ${switchTo} cannot change the draft`, async ({page}) => {
    const state = await legacyDraft(page), operation = state.operation;
    state.operation = null;
    await page.reload(); await openSetupAdvanced(page); await originalCheck(page).click();
    await expect(discardDraft(page)).toBeEnabled();
    state.operation = operation; state.holdOperation = true;
    await originalCheck(page).click(); await expect.poll(() => !!state.releaseOperation).toBe(true);
    if (switchTo === 'discard') {
      await discardDraft(page).click();
      await page.locator('[data-setup-repository]').locator('..').locator(':scope > summary').click();
      await field(page, 'workspace_id').fill('ws-new-after-discard');
    }
    else {
      if (switchTo === 'account') {
        state.actor = 'other-person'; state.principalId = 'other-person-id';
        // Managed native identity changes on verified relaunch. Browser token
        // switching can exercise a live old callback without reloading the page.
        if (native) await page.reload();
        else {
          await page.locator('#live').click();
          await page.getByRole('button', {name: locale === 'en' ? 'Disconnect' : '中斷', exact: true}).click();
          await page.getByLabel(locale === 'en' ? 'API token' : 'API 權杖', {exact: true}).fill('synthetic-new-account-token');
          await page.getByRole('button', {name: locale === 'en' ? 'Connect' : '連線', exact: true}).click();
          await page.evaluate(() => {location.hash = '#/settings';});
        }
      } else {state.serverId = 'other-central'; await page.reload();}
      await openSetupAdvanced(page);
    }
    const before = await storage(page);
    state.releaseOperation!(); state.holdOperation = false;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect(await storage(page)).toBe(before);
    await expect(page.locator('[data-setup-operation]')).toHaveCount(0);
    await expect(field(page, 'repository')).toBeEnabled(); await assertReadOnly(page, state);
  });
  for (const [index, remote] of unsafeRemotes.entries()) test(`credential remote ${index} never enters storage or admission`, async ({page}) => {
    const state = await managedSetupFixture(page, native, locale);
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
      const state = await managedSetupFixture(page, native, locale);
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
    const state = await managedSetupFixture(page, native, locale);
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
      ? 'The saved request text was removed for safety.' : '已為安全起見移除儲存的請求內容。');
    await expect(page.locator('[data-managed-setup]')).toContainText('legacy-original-key');
    expect(await storage(page)).not.toContain('synthetic-remote-secret');
    expect((await draft(page)).intent.key).toBe('legacy-original-key');
    expect((await draft(page)).intent.request.params).toBeUndefined();
    expect(state.posts).toEqual([]); expect(state.staged).toEqual([]);
    await expect(field(page, 'repository')).toBeDisabled();
    const check = page.getByRole('button', {name: locale === 'en' ? 'Check original request' : '查回原請求', exact: true});
    await expect(check).toBeEnabled(); await check.click();
    const discard = page.getByRole('button', {name: locale === 'en' ? 'Discard saved draft' : '捨棄儲存的草稿', exact: true});
    await expect(discard).toBeEnabled();
    await expect(page.locator('[data-managed-setup]')).toContainText(locale === 'en'
      ? 'No request with this key is visible to this account on this server.' : '此帳號在此伺服器上看不到具有這個代號的請求。');
    await discard.click(); await expect(field(page, 'repository')).toBeEnabled();
    expect((await draft(page)).intent).toBeUndefined();
    expect(state.posts).toEqual([]); expect(state.staged).toEqual([]);
  });

  test('paste, staging, lost reply and expiry recover the original operation without storing or reposting its handle', async ({page}) => {
    await page.clock.install();
    const state = await managedSetupFixture(page, native, locale); state.lost = true;
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
    const {secret_ref, ...params} = state.posts[0].body.params;
    expect(original.intent.lookup_request).toEqual({...state.posts[0].body, params});
    expect(original.intent.request).toEqual({action: state.posts[0].body.action});
    expect(original.intent.lookup_only).toBe(true);
    expect(await storage(page)).not.toContain(secret_ref);
    expect(await storage(page)).not.toContain('synthetic-bat-secret');
    await page.clock.fastForward(3600_001);
    expect((await draft(page)).intent).toEqual(original.intent);
    expect(await storage(page)).not.toContain('synthetic-bat-secret');
    await page.reload(); await openSetupAdvanced(page);
    await expect(recover).toBeEnabled();
    expect(state.posts).toHaveLength(1); // Reload itself never submits.
    await recover.click();
    await expect(page.locator('[data-setup-operation]')).toHaveAttribute('href', '#/op/' + 'op_' + 'c'.repeat(32));
    expect(state.posts).toHaveLength(1);
    expect(state.operation.idempotency_key).toBe(original.intent.key);
    expect(state.operationReads.some(read => read.path.startsWith('/operations?'))).toBe(true);
    expect(state.staged).toHaveLength(1);
    expect(await storage(page)).not.toContain('synthetic-bat-secret');
    await page.reload(); await openSetupAdvanced(page);
    await expect(page.locator('[data-setup-operation]')).toHaveAttribute('href', '#/op/' + 'op_' + 'c'.repeat(32));
    expect(state.posts).toHaveLength(1); // A known operation is recovered by GET.
    expect(await storage(page)).not.toContain(secret_ref);
  });

  async function lostRepository(page: Page) {
    const state = await managedSetupFixture(page, native, locale); state.lost = true;
    const form = await repositoryForm(page);
    await field(page, 'remote_url').fill('https://github.com/o/r.git');
    await field(page, 'github_secret').fill('synthetic-github-secret');
    await form.locator('button[type=submit]').click();
    const recover = page.getByRole('button', {name: locale === 'en' ? 'Check request' : '查回請求', exact: true});
    await expect(recover).toBeEnabled();
    expect(state.posts).toHaveLength(1);
    return {state, recover, original: await draft(page)};
  }

  test('legacy staged request is scrubbed and recovered by its original key across filtered pages', async ({page}) => {
    const {state, recover, original} = await lostRepository(page);
    await page.evaluate(request => {
      const key = Object.keys(localStorage).find(key => key.startsWith('batc.managed.setup.'))!;
      const saved = JSON.parse(localStorage.getItem(key)!);
      saved.intent.request = request; delete saved.intent.lookup_only;
      saved.intent.unexpected = {token: 'synthetic-legacy-secret'};
      localStorage.setItem(key, JSON.stringify(saved));
    }, state.posts[0].body);
    state.operationPages = [{operations: [], next_before: 123, next_before_id: 'op_' + 'd'.repeat(32)},
      {operations: [state.operation], next_before: null, next_before_id: null}];
    await page.reload(); await openSetupAdvanced(page);
    expect(await storage(page)).not.toContain('setup_' + 'b'.repeat(32));
    expect(await storage(page)).not.toContain('synthetic-legacy-secret');
    await recover.click();
    await expect(page.locator('[data-setup-operation]')).toHaveAttribute('href', '#/op/' + state.operation.operation_id);
    expect(state.operationReads[1].path).toContain('before=123&before_id=op_' + 'd'.repeat(32));
    expect((await draft(page)).intent.key).toBe(original.intent.key);
    expect(state.posts).toHaveLength(1); expect(state.staged).toHaveLength(1);
  });

  test('missing original operation stays locked and read-only at 390px', async ({page}) => {
    await page.setViewportSize({width: 390, height: 844});
    const {state, recover, original} = await lostRepository(page);
    state.operation = null;
    await page.reload(); await openSetupAdvanced(page); await recover.click();
    await expect(page.locator('[data-managed-setup]')).toContainText(locale === 'en'
      ? 'The original setup operation has not been found.' : '尚未找到原設定操作。');
    await expect(field(page, 'repository')).toBeDisabled();
    await recover.click();
    expect((await draft(page)).intent).toEqual(original.intent);
    expect(state.posts).toHaveLength(1); expect(state.staged).toHaveLength(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });

  test('bounded lookup continues from its cursor without creating a replacement', async ({page}) => {
    const {state, recover, original} = await lostRepository(page);
    state.operationPages = Array.from({length: 20}, (_, index) => ({operations: [], next_before: 100 - index, next_before_id: 'op_' + 'd'.repeat(32)}));
    await page.reload(); await openSetupAdvanced(page); await recover.click();
    await expect(page.locator('[data-managed-setup]')).toContainText(locale === 'en'
      ? 'The original setup operation has not been found.' : '尚未找到原設定操作。');
    expect(state.operationReads).toHaveLength(20);
    expect((await draft(page)).intent).toEqual(original.intent);
    await recover.click();
    await expect(page.locator('[data-setup-operation]')).toHaveAttribute('href', '#/op/' + state.operation.operation_id);
    expect(state.operationReads[20].path).toContain('before=81&before_id=op_');
    expect(state.posts).toHaveLength(1);
  });

  test('withdrawn observation permission cannot cause a new admission', async ({page}) => {
    const {state, recover, original} = await lostRepository(page);
    state.scopes = ['manage'];
    await page.reload(); await openSetupAdvanced(page); await recover.click();
    await expect(recover).toBeEnabled();
    expect((await draft(page)).intent).toEqual(original.intent);
    expect(state.posts).toHaveLength(1); expect(state.staged).toHaveLength(1);
    await expect(field(page, 'repository')).toBeDisabled();
  });

  test('late operation read after leaving setup cannot change its retained intent', async ({page}) => {
    const {state, recover, original} = await lostRepository(page);
    await page.reload(); await openSetupAdvanced(page);
    state.holdOperation = true;
    await recover.click();
    await expect.poll(() => !!state.releaseOperation).toBe(true);
    await page.evaluate(() => {location.hash = '#/sessions';});
    await expect(page.locator('[data-managed-setup]')).toHaveCount(0);
    state.releaseOperation!(); state.holdOperation = false;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect((await draft(page)).intent).toEqual(original.intent);
    expect(state.posts).toHaveLength(1); expect(state.staged).toHaveLength(1);
  });

  for (const identity of ['account', 'connection']) test(`${identity} switch isolates the original staged intent`, async ({page}) => {
    const {state, recover, original} = await lostRepository(page);
    if (identity === 'account') {state.actor = 'other-person'; state.principalId = 'other-person-id';}
    else state.serverId = 'other-central';
    await page.reload(); await openSetupAdvanced(page);
    await expect(field(page, 'repository')).toBeEditable();
    await expect(recover).toBeHidden();
    expect(state.operationReads).toEqual([]);
    state.actor = null; state.principalId = 'setup-person'; state.serverId = 'setup-central';
    await page.reload(); await openSetupAdvanced(page); await recover.click();
    await expect(page.locator('[data-setup-operation]')).toHaveAttribute('href', '#/op/' + state.operation.operation_id);
    expect((await draft(page)).intent.key).toBe(original.intent.key);
    expect(state.posts).toHaveLength(1); expect(state.staged).toHaveLength(1);
  });

  for (const mismatch of ['actor', 'key', 'request', 'duplicate', 'cursor']) test(`lookup rejects ${mismatch} without resubmitting`, async ({page}) => {
    const {state, recover, original} = await lostRepository(page);
    await page.reload(); await openSetupAdvanced(page);
    if (mismatch === 'actor') state.operation.actor = 'other-person';
    if (mismatch === 'key') state.operation.idempotency_key = 'other-key';
    if (mismatch === 'request') state.operation.params.remote_url = 'https://github.com/o/other.git';
    if (mismatch === 'duplicate') state.operationPages = [{operations: [state.operation, state.operation]}];
    if (mismatch === 'cursor') state.operationPages = [{operations: [], next_before: 123, next_before_id: 'invalid'}];
    await recover.click();
    await expect(recover).toBeEnabled();
    await expect(page.locator('[data-setup-operation]')).toHaveCount(0);
    expect((await draft(page)).intent).toEqual(original.intent);
    expect(state.posts).toHaveLength(1); expect(state.staged).toHaveLength(1);
  });
});
