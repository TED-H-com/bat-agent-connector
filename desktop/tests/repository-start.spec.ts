import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {publishedFixture, openPublished, previewPublished, publishedId, publishedSha, bindingDigest, selected} from './repository-start-fixture';
const apply = (form: any) => form.getByRole('button', {name: 'Start from this version', exact: true});
const instructions = (form: any) => form.getByRole('textbox', {name: 'Original instructions', exact: true});
for (const native of [false, true]) {
  const mode = native ? 'native' : 'browser';
  test(`${mode} published: exact preview/key survive lost reply, moving ref and accepted reload`, async ({page}) => {
    const state = await publishedFixture(page, native, {lost: true}), form = await openPublished(page);
    await expect(apply(form)).toBeDisabled(); await previewPublished(page);
    await form.getByRole('combobox', {name: 'Agent', exact: true}).selectOption('codex'); await apply(form).click();
    await expect(form).toContainText('Lost published start reply'); const original = state.posts[0];
    expect(original.body).toEqual({action: 'repository.continue', target: selected, params: {source_ref: 'refs/heads/main', source_sha: publishedSha,
      agent: 'codex', prompt: '  Work from this version.\nPreserve these instructions.  '}, preconditions: {repository_id: 123, binding_digest: bindingDigest}});
    const stored = await page.evaluate(() => JSON.parse(Object.entries(localStorage).find(([k]) => k.startsWith('batc.published.'))![1]).intent);
    expect(stored.request).toEqual(original.body); expect(stored.key).toBe(original.idempotency_key);
    state.writes = false; state.unbound = true; state.sha = 'd'.repeat(40); await page.reload();
    await expect(instructions(form)).toBeDisabled(); expect(state.posts).toHaveLength(1);
    await form.getByRole('button', {name: 'Retry original request'}).click(); await expect(form).toContainText('Initial instructions were accepted');
    expect(state.posts[1]).toEqual(original); expect(state.previews).toHaveLength(1);
    await page.reload(); await expect(form).toContainText('Start confirmed'); expect(state.posts).toHaveLength(2); expect(state.previews).toHaveLength(1);
    await form.getByRole('button', {name: 'Prepare another published version'}).click(); await expect(instructions(form)).toHaveValue('');
    await expect(apply(form)).toBeDisabled(); expect(state.errors).toEqual([]);
  });
  test(`${mode} published: late preview and edited ref cannot authorize a different selection`, async ({page}) => {
    const state = await publishedFixture(page, native, {delayPreview: true}), form = await openPublished(page);
    await form.locator('select').first().selectOption(JSON.stringify(selected)); await form.locator('input').first().fill('refs/heads/main');
    await form.getByRole('button', {name: 'Preview published version'}).click(); await expect.poll(() => Boolean(state.holdPreview)).toBe(true);
    await form.locator('input').first().fill('refs/heads/next'); state.holdPreview(); await instructions(form).fill('work');
    await expect(form.locator('[data-published-preview]')).toBeEmpty(); await expect(apply(form)).toBeDisabled();
    state.delayPreview = false; await form.getByRole('button', {name: 'Preview published version'}).click(); await expect(form.locator('[data-published-preview]')).toContainText(publishedSha);
    await expect(apply(form)).toBeEnabled(); await page.reload(); await expect(apply(form)).toBeDisabled(); expect(state.posts).toEqual([]);
  });
  for (const bad of ['key', 'actor', 'workspace', 'prompt', 'preconditions', 'result', 'refs', 'message']) test(`${mode} published: rejects mismatched ${bad}`, async ({page}) => {
    const state = await publishedFixture(page, native, {bad}), form = await openPublished(page); await previewPublished(page); await apply(form).click();
    await expect(form).toContainText('does not match'); await expect(form.getByRole('link', {name: 'View operation and step receipts'})).toHaveCount(0);
    await expect(form.getByRole('button', {name: 'Prepare another published version'})).toBeHidden();
    state.bad = null; await form.getByRole('button', {name: 'Retry original request'}).click(); await expect(form).toContainText('Start confirmed');
    expect(state.posts[1]).toEqual(state.posts[0]);
  });
  test(`${mode} published: unknown prompt and failed event read retain the original operation`, async ({page}) => {
    const state = await publishedFixture(page, native, {status: 'uncertain', delayPost: true}), form = await openPublished(page); await previewPublished(page); await apply(form).click();
    await expect.poll(() => Boolean(state.holdPost)).toBe(true); state.failRead = true; state.events = [{seq: 1, kind: 'operation.uncertain', resource_type: 'operation', resource_id: publishedId}];
    await expect.poll(() => state.delivered).toBe(true); expect(state.after).toBe(0); state.holdPost();
    await expect(form).toContainText('Published read failed'); expect(state.after).toBe(0); state.failRead = false;
    await expect.poll(() => state.after).toBe(1); await expect(form).toContainText('Started, but acceptance');
    await expect(form.getByRole('button', {name: 'Prepare another published version'})).toBeHidden();
    await page.reload(); await expect(form).toContainText('Started, but acceptance'); expect(state.posts).toHaveLength(1); expect(state.previews).toHaveLength(1);
  });
  test(`${mode} published: only a proven admission refusal allows explicit replacement`, async ({page}) => {
    const state = await publishedFixture(page, native, {refuse: 'REPOSITORY_NOT_BOUND'}), form = await openPublished(page); await previewPublished(page); await apply(form).click();
    await expect(form).toContainText('REPOSITORY_NOT_BOUND'); await page.reload(); await form.getByRole('button', {name: 'Prepare another published version'}).click();
    state.refuse = 'FORBIDDEN'; await previewPublished(page); await apply(form).click(); await expect(form).toContainText('Your account does not have the required scope');
    expect(state.posts[0].idempotency_key).not.toBe(state.posts[1].idempotency_key); await expect(form.getByRole('button', {name: 'Prepare another published version'})).toBeHidden();
    state.refuse = 'IDEMPOTENCY_CONFLICT'; await form.getByRole('button', {name: 'Retry original request'}).click(); await expect(form.locator('.error')).toBeVisible(); expect(state.posts[2]).toEqual(state.posts[1]);
  });
  test(`${mode} published: credential/server namespaces preserve private frozen drafts`, async ({page}) => {
    const state = await publishedFixture(page, native, {lost: true}), form = await openPublished(page); await previewPublished(page); await apply(form).click(); await expect(form).toContainText('Lost published start reply');
    state.principal = 'another-principal'; await page.reload(); await expect(instructions(form)).toHaveValue('');
    state.principal = 'published-principal'; state.server = 'another-server'; await page.reload(); await expect(instructions(form)).toHaveValue('');
    state.server = 'published-server'; await page.reload(); await expect(instructions(form)).toHaveValue(state.posts[0].body.params.prompt); expect(state.posts).toHaveLength(1);
  });
  test(`${mode} published: missing scopes, capability and malformed previews cannot start`, async ({page}) => {
    const state = await publishedFixture(page, native, {scopes: ['observe']}), form = await openPublished(page); await previewPublished(page); await expect(apply(form)).toBeDisabled();
    state.scopes = ['observe', 'start']; state.absent = true; await page.reload(); await previewPublished(page); await expect(apply(form)).toBeDisabled(); state.absent = false;
    for (const badPreview of ['target', 'ref', 'sha', 'preconditions']) {state.badPreview = badPreview; await page.reload(); await form.getByRole('button', {name: 'Preview published version'}).click(); await expect(form).toContainText('version preview does not match'); await expect(apply(form)).toBeDisabled();}
    expect(state.posts).toEqual([]);
  });
}
test('published: storage refusal prevents operation submission', async ({page}) => {
  const state = await publishedFixture(page, false), form = await openPublished(page); await previewPublished(page);
  await page.evaluate(() => {const original = Storage.prototype.setItem; Storage.prototype.setItem = function(k, v) {if (k.startsWith('batc.published.')) throw new Error('Storage unavailable'); return original.call(this, k, v);};});
  await apply(form).click(); await expect(form).toContainText('Storage unavailable'); expect(state.posts).toEqual([]);
});
for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) test(`published form fits ${locale} ${width}`, async ({browser}) => {
  const context = await browser.newContext({locale, viewport: {width, height: width === 390 ? 844 : 900}}), page = await context.newPage();
  const state = await publishedFixture(page, false); await openPublished(page); const form = page.locator('[data-published-start]');
  await form.locator('select').first().selectOption(JSON.stringify(selected)); await form.locator('input').first().fill('refs/heads/main'); await form.locator('button').first().click();
  await expect(form.locator('[data-published-preview]')).toContainText(publishedSha); await form.locator('textarea').fill(locale === 'zh-TW' ? '請在此固定版本新增測試，保留既有人工工作。' : 'Add tests on this fixed version. Keep existing manual work unchanged.'); await form.locator('textarea').blur();
  await page.evaluate(() => window.scrollTo(0, 0)); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await mkdir('/tmp/bac-published-ui', {recursive: true}); await page.screenshot({path: `/tmp/bac-published-ui/${locale}-${width}.png`, fullPage: true}); expect(state.errors).toEqual([]); await context.close();
});

for (const native of [false, true]) test(`short branch names preserve fixed preview and replay (${native ? 'IPC' : 'HTTP'})`, async ({page}) => {
  const state = await publishedFixture(page, native, {lost: true}), form = await openPublished(page);
  await form.getByRole('combobox', {name: 'Repository · host · workspace ID'}).selectOption(JSON.stringify(selected));
  const branch = form.getByRole('textbox', {name: 'Published branch ref'});
  await branch.fill('refs/tags/v1');
  await expect(form.getByRole('button', {name: 'Preview published version'})).toBeDisabled();
  await expect(form).toContainText('Enter a valid branch');
  await branch.fill('main');
  await form.getByRole('button', {name: 'Preview published version'}).click();
  await expect(form.locator('[data-published-preview]')).toContainText(publishedSha);
  expect(state.previews[0].body.source_ref).toBe('refs/heads/main');
  await instructions(form).fill('Keep the exact selected commit.');
  await apply(form).click(); await expect(form).toContainText('Lost published start reply');
  await page.reload();
  await form.getByRole('button', {name: 'Retry original request'}).click();
  await expect(form).toContainText('Start confirmed');
  await expect(form.locator('.status-succeeded')).toHaveText('Started');
  expect(state.posts[1]).toEqual(state.posts[0]);
  expect(state.posts[0].body.params.source_ref).toBe('refs/heads/main');
  expect(state.posts[0].body.params.source_sha).toBe(publishedSha);
});
