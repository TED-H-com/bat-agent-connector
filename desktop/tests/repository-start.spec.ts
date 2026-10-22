import {test, expect} from '@playwright/test';
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
for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) test(`published form fits ${locale} ${width}`, async ({browser}, testInfo) => {
  const context = await browser.newContext({locale, viewport: {width, height: width === 390 ? 844 : 900}}), page = await context.newPage();
  const state = await publishedFixture(page, false); await openPublished(page); const form = page.locator('[data-published-start]');
  await form.locator('select').first().selectOption(JSON.stringify(selected)); await form.locator('input').first().fill('refs/heads/main'); await form.locator('button').first().click();
  await expect(form.locator('[data-published-preview]')).toContainText(publishedSha); await form.locator('textarea').fill(locale === 'zh-TW' ? '請在此固定版本新增測試，保留既有人工工作。' : 'Add tests on this fixed version. Keep existing manual work unchanged.'); await form.locator('textarea').blur();
  await page.evaluate(() => window.scrollTo(0, 0)); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: testInfo.outputPath(`${locale}-${width}.png`), fullPage: true}); expect(state.errors).toEqual([]); await context.close();
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

const publishedStatus = (form: any) => form.locator('[data-published-result] + [role="status"]');
const startButton = (form: any) => form.locator('button.primary');
test('busy focus: Start with Enter keeps focus while the request is held and then moves to Check', async ({page}) => {
  const state = await publishedFixture(page, false, {delayPost: true});
  await openPublished(page);
  const form = await previewPublished(page);
  const start = startButton(form);
  await expect(start).toHaveAccessibleName('Start from this version');
  await start.focus();
  await start.press('Enter');
  await expect.poll(() => Boolean(state.holdPost)).toBe(true);
  await expect(start).toBeFocused();
  await expect(start).toHaveAttribute('aria-disabled', 'true');
  await expect(publishedStatus(form)).toHaveAttribute('aria-busy', 'true');
  await start.press('Enter', {force: true});
  await start.click({force: true});
  expect(state.posts).toHaveLength(1);
  state.delayPost = false; state.holdPost();
  await expect(form.getByRole('button', {name: 'Check original operation', exact: true})).toBeFocused();
  expect(state.posts).toHaveLength(1);
});
test('busy focus: composer shortcut moves focus to Start and sends one request', async ({page}) => {
  const state = await publishedFixture(page, false, {delayPost: true});
  await openPublished(page);
  const form = await previewPublished(page);
  const prompt = instructions(form), start = startButton(form);
  await prompt.focus();
  await prompt.press('Control+Enter');
  await expect.poll(() => Boolean(state.holdPost)).toBe(true);
  await expect(start).toBeFocused();
  await expect(start).toHaveAttribute('aria-disabled', 'true');
  expect(state.posts).toHaveLength(1);
  state.delayPost = false; state.holdPost();
  await expect(form.getByRole('button', {name: 'Check original operation', exact: true})).toBeFocused();
  expect(state.posts).toHaveLength(1);
});
test('busy focus: a refused start moves focus to Prepare another', async ({page}) => {
  await publishedFixture(page, false, {refuse: 'REPOSITORY_NOT_BOUND'});
  await openPublished(page);
  const form = await previewPublished(page);
  await startButton(form).focus();
  await startButton(form).press('Enter');
  await expect(form.getByRole('button', {name: 'Prepare another published version', exact: true})).toBeFocused();
});
test('busy focus: Prepare another moves focus to the repository', async ({page}) => {
  await publishedFixture(page, false, {refuse: 'REPOSITORY_NOT_BOUND'});
  await openPublished(page);
  const form = await previewPublished(page);
  await apply(form).click();
  const another = form.getByRole('button', {name: 'Prepare another published version', exact: true});
  await expect(another).toBeVisible();
  await another.focus();
  await another.press('Enter');
  await expect(form.getByRole('combobox', {name: 'Repository · host · workspace ID'})).toBeFocused();
});
test('busy focus: an event read keeps focus on Check', async ({page}) => {
  const state = await publishedFixture(page, false);
  await openPublished(page);
  const form = await previewPublished(page);
  await apply(form).click();
  const check = form.getByRole('button', {name: 'Check original operation', exact: true});
  await expect(check).toBeVisible();
  await check.focus();
  state.delayRead = true;
  state.events = [{seq: 1, kind: 'operation.updated', resource_type: 'operation', resource_id: publishedId}];
  await expect.poll(() => Boolean(state.holdRead)).toBe(true);
  await expect(check).toBeFocused();
  await expect(check).toHaveAttribute('aria-disabled', 'true');
  await expect(publishedStatus(form)).toHaveAttribute('aria-busy', 'true');
  const operationReads = () => state.reads.filter((path: string) => path.startsWith('/operations/' + publishedId)).length;
  expect(operationReads()).toBe(1);
  await check.press('Enter', {force: true});
  await check.click({force: true});
  expect(operationReads()).toBe(1);
  state.delayRead = false; state.holdRead();
  await expect(check).not.toHaveAttribute('aria-disabled');
  await expect(check).toBeFocused();
  await expect.poll(() => state.after).toBe(1);
  expect(operationReads()).toBe(1);
});
test('busy focus: Preview keeps focus while its request is held', async ({page}) => {
  const state = await publishedFixture(page, false, {delayPreview: true}), form = await openPublished(page);
  await form.getByRole('combobox', {name: 'Repository · host · workspace ID'}).selectOption(JSON.stringify(selected));
  await form.getByRole('textbox', {name: 'Published branch ref'}).fill('refs/heads/main');
  const inspect = form.locator('.dispatch-destination .actions button');
  await inspect.focus();
  await inspect.press('Enter');
  await expect.poll(() => Boolean(state.holdPreview)).toBe(true);
  await expect(inspect).toBeFocused();
  await expect(inspect).toHaveAttribute('aria-disabled', 'true');
  await expect(publishedStatus(form)).toHaveAttribute('aria-busy', 'true');
  await inspect.press('Enter', {force: true});
  expect(state.previews).toHaveLength(1);
  state.delayPreview = false; state.holdPreview();
  await expect(form.locator('[data-published-preview]')).toContainText(publishedSha);
  await expect(inspect).not.toHaveAttribute('aria-disabled');
  await expect(inspect).toBeFocused();
  expect(state.previews).toHaveLength(1);
});
test('busy focus: static block keeps Prepare another disabled during an event reread', async ({page}) => {
  const state = await publishedFixture(page, false);
  await openPublished(page);
  const form = await previewPublished(page);
  await apply(form).click();
  const another = form.getByRole('button', {name: 'Prepare another published version', exact: true});
  await expect(another).toBeEnabled();
  state.failRead = true;
  state.events = [{seq: 1, kind: 'operation.updated', resource_type: 'operation', resource_id: publishedId}];
  await expect(form).toContainText('Published read failed');
  expect(state.after).toBe(0);
  state.failRead = false; state.delayRead = true;
  await expect.poll(() => Boolean(state.holdRead)).toBe(true);
  await expect(another).toBeVisible();
  await expect(another).toHaveJSProperty('disabled', true);
  await expect(another).not.toHaveAttribute('aria-disabled');
  state.delayRead = false; state.holdRead();
  await expect.poll(() => state.after).toBe(1);
});
