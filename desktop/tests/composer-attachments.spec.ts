import {test, expect, type Page} from '@playwright/test';
import {createHash} from 'node:crypto';
import {publishedFixture, selected, dispatchProject, uploadId, aid, publishedSha} from './repository-start-fixture';

const form = (page: Page) => page.locator('[data-published-start]');
const prompt = (page: Page) => form(page).getByRole('textbox', {name: 'Original instructions', exact: true});
const apply = (page: Page) => form(page).getByRole('button', {name: 'Start from this version', exact: true});

// 1x1 valid PNG
const pngBuffer = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');
const pngDigest = createHash('sha256').update(pngBuffer).digest('hex');

// 1x1 valid JPEG
const jpegBuffer = Buffer.from('/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=', 'base64');

// Unsupported SVG
const svgBuffer = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><circle cx="5" cy="5" r="5"/></svg>', 'utf8');

async function openAndPreview(page: Page) {
  await page.goto('/dashboard/#/dispatch/' + dispatchProject);
  await expect(form(page)).toBeVisible();
  const binding = form(page).getByRole('combobox', {name: 'Repository · host · workspace ID'});
  await binding.selectOption(JSON.stringify(selected));
  await form(page).getByRole('textbox', {name: 'Published branch ref'}).fill('refs/heads/main');
  await form(page).getByRole('button', {name: 'Preview published version'}).click();
  await expect(form(page).locator('[data-published-preview]')).toContainText(publishedSha);
}

async function pasteFile(page: Page, selector: string, name: string, type: string, buffer: Buffer) {
  await page.evaluate(({selector, name, type, bytes}) => {
    const el = document.querySelector(selector);
    if (!el) throw new Error('Element not found: ' + selector);
    const dt = new DataTransfer();
    const file = new File([new Uint8Array(bytes)], name, {type});
    dt.items.add(file);
    const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
    el.dispatchEvent(ev);
  }, {selector, name, type, bytes: Array.from(buffer)});
}

async function dropFile(page: Page, selector: string, name: string, type: string, buffer: Buffer) {
  await page.evaluate(({selector, name, type, bytes}) => {
    const el = document.querySelector(selector);
    if (!el) throw new Error('Element not found: ' + selector);
    const dt = new DataTransfer();
    const file = new File([new Uint8Array(bytes)], name, {type});
    dt.items.add(file);
    const ev = new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true});
    el.dispatchEvent(ev);
  }, {selector, name, type, bytes: Array.from(buffer)});
}

async function pasteText(page: Page, selector: string, text: string) {
  return page.evaluate(({selector, text}) => {
    const el = document.querySelector(selector) as HTMLTextAreaElement;
    if (!el) throw new Error('Element not found: ' + selector);
    const dt = new DataTransfer();
    dt.setData('text/plain', text);
    const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true});
    el.dispatchEvent(ev);
    if (!ev.defaultPrevented) {
      el.value += text;
      el.dispatchEvent(new Event('input', {bubbles: true}));
    }
    return {defaultPrevented: ev.defaultPrevented, value: el.value};
  }, {selector, text});
}

for (const native of [false, true]) {
  const mode = native ? 'IPC' : 'Browser';

  test(`${mode}: unavailable durable draft storage prevents pasted upload`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected]});
    await openAndPreview(page);
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (key.startsWith('batc.draft.') && key.includes('.dispatch.')) throw new Error('Draft storage unavailable');
        return original.call(this, key, value);
      };
    });
    await pasteFile(page, '[data-published-start] textarea[aria-label="Original instructions"]', 'screenshot.png', 'image/png', pngBuffer);
    await expect(form(page).locator('.attachment-list')).toContainText('Draft storage unavailable');
    await expect(apply(page)).toBeDisabled();
    expect(state.uploaded).toBeFalsy();
    expect(state.posts).toHaveLength(0);
    expect(state.errors).toEqual([]);
  });

  test(`${mode}: written prompt keeps its exact whitespace with pasted images`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected]});
    await openAndPreview(page);
    const original = '  Review the illustration.\nKeep these instructions exactly.  ';
    await prompt(page).fill(original);
    await pasteFile(page, '[data-published-start] textarea[aria-label="Original instructions"]', 'screenshot.png', 'image/png', pngBuffer);
    await expect(form(page).locator('.attachment-list')).toContainText(aid);
    await apply(page).click();
    await expect.poll(() => state.posts.filter(post => post.body.action === 'repository.continue').length).toBe(1);
    expect(state.posts.find(post => post.body.action === 'repository.continue').body.params.prompt).toBe(original);
    expect(state.errors).toEqual([]);
  });

  test(`${mode}: pasted image uploads to stable artifact ref with thumbnail and can be removed`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected]});
    await openAndPreview(page);

    // Paste PNG into textarea
    await pasteFile(page, '[data-published-start] textarea[aria-label="Original instructions"]', 'screenshot.png', 'image/png', pngBuffer);

    // Verify thumbnail is displayed
    const thumb = form(page).locator('img.attachment-thumb');
    await expect(thumb).toBeVisible();

    // Verify upload completes and shows stable artifact ref
    await expect(form(page).locator('.attachment-list')).toContainText(aid);
    await expect(form(page).locator('.attachment-list')).toContainText(pngDigest.slice(0, 12));
    expect(state.uploaded).toBe(true);
    expect(state.bytes).toEqual([...pngBuffer]);

    // Removal
    const removeBtn = form(page).locator('.attachment-list').getByRole('button', {name: 'Remove', exact: true});
    await expect(removeBtn).toBeVisible();
    await removeBtn.click();
    await expect(form(page).locator('.attachment-list')).toBeEmpty();
    await expect(apply(page)).toBeDisabled();
    expect(state.errors).toEqual([]);
  });

  test(`${mode}: image-only dispatch uses explicit localized default request`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected]});
    await openAndPreview(page);

    // Ensure prompt is empty
    await expect(prompt(page)).toHaveValue('');
    await expect(apply(page)).toBeDisabled();

    // Paste image into composer
    await pasteFile(page, '[data-published-start] textarea[aria-label="Original instructions"]', 'diagram.png', 'image/png', pngBuffer);
    await expect(form(page).locator('.attachment-list')).toContainText(aid);

    // Image-only dispatch button is now enabled
    await expect(apply(page)).toBeEnabled();
    await apply(page).click();
    await expect(form(page)).toContainText('Start confirmed');

    // Verify dispatched request params
    const continuePost = state.posts.find(p => p.body.action === 'repository.continue');
    expect(continuePost).toBeDefined();
    expect(continuePost.body.params.prompt).toBe('Review attached files');
    expect(continuePost.body.params.artifacts).toEqual([{artifact_id: aid, revision: 1, digest: pngDigest}]);
    expect(state.errors).toEqual([]);
  });

  test(`${mode}: image-only dispatch uses zh-TW localized default when locale is zh-TW`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected]});
    await page.addInitScript(() => Object.defineProperty(navigator, 'language', {value: 'zh-TW'}));
    await page.goto('/dashboard/#/dispatch/' + dispatchProject);
    await expect(form(page)).toBeVisible();
    const binding = form(page).getByRole('combobox', {name: '儲存庫 · 主機 · 工作區 ID'});
    await binding.selectOption(JSON.stringify(selected));
    await form(page).getByRole('textbox', {name: '已發佈分支 ref'}).fill('refs/heads/main');
    await form(page).getByRole('button', {name: '預覽已發佈版本'}).click();
    await expect(form(page).locator('[data-published-preview]')).toContainText(publishedSha);

    // Paste image
    await pasteFile(page, '[data-published-start] textarea', 'photo.jpg', 'image/jpeg', jpegBuffer);
    await expect(form(page).locator('.attachment-list')).toContainText(aid);

    const applyZh = form(page).getByRole('button', {name: '從此版本開始', exact: true});
    await expect(applyZh).toBeEnabled();
    await applyZh.click();
    await expect(form(page)).toContainText('已確認啟動');

    const continuePost = state.posts.find(p => p.body.action === 'repository.continue');
    expect(continuePost.body.params.prompt).toBe('請檢視所附檔案');
    expect(state.errors).toEqual([]);
  });

  test(`${mode}: failed or uncertain upload does not dispatch`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected], failUpload: true});
    await openAndPreview(page);

    await pasteFile(page, '[data-published-start] textarea[aria-label="Original instructions"]', 'bad.png', 'image/png', pngBuffer);
    await expect(form(page).locator('.attachment-list')).toContainText('Upload failed');
    await expect(apply(page)).toBeDisabled();

    // Verify no repository.continue operation was dispatched
    const dispatches = state.posts.filter(p => p.body.action === 'repository.continue');
    expect(dispatches).toHaveLength(0);
    expect(state.errors).toEqual([]);
  });

  test(`${mode}: page reload preserves durable artifact refs`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected]});
    await openAndPreview(page);

    await pasteFile(page, '[data-published-start] textarea[aria-label="Original instructions"]', 'persisted.png', 'image/png', pngBuffer);
    await expect(form(page).locator('.attachment-list')).toContainText(aid);

    // Reload page
    await page.reload();
    await expect(form(page)).toBeVisible();
    await openAndPreview(page);

    // Durable ref is preserved from localStorage draft
    await expect(form(page).locator('.attachment-list')).toContainText(aid);
    await expect(apply(page)).toBeEnabled();
    await apply(page).click();
    await expect(form(page)).toContainText('Start confirmed');

    const continuePost = state.posts.find(p => p.body.action === 'repository.continue');
    expect(continuePost.body.params.artifacts).toEqual([{artifact_id: aid, revision: 1, digest: pngDigest}]);
    expect(state.errors).toEqual([]);
  });

  test(`${mode}: plain text paste is unchanged and does not create attachments`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected]});
    await openAndPreview(page);

    const result = await pasteText(page, '[data-published-start] textarea[aria-label="Original instructions"]', 'Plain text paste should remain unchanged.');
    expect(result.defaultPrevented).toBe(false);
    expect(result.value).toContain('Plain text paste should remain unchanged.');
    await expect(prompt(page)).toHaveValue('Plain text paste should remain unchanged.');
    await expect(form(page).locator('.attachment-list')).toBeEmpty();
    expect(state.uploaded).toBe(false);
    expect(state.errors).toEqual([]);
  });

  test(`${mode}: oversized and unsupported image types are rejected`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected]});
    await openAndPreview(page);

    // Unsupported format (SVG)
    await pasteFile(page, '[data-published-start] textarea[aria-label="Original instructions"]', 'vector.svg', 'image/svg+xml', svgBuffer);
    await expect(form(page).locator('.attachments [role="status"]')).toContainText('Only PNG, JPEG, and WebP images are supported.');
    await expect(form(page).locator('.attachment-list')).toBeEmpty();

    // Oversized image (> 1048576 bytes limit set in fixture)
    const largeBuffer = Buffer.alloc(1048576 + 1024, 0);
    await pasteFile(page, '[data-published-start] textarea[aria-label="Original instructions"]', 'large.png', 'image/png', largeBuffer);
    const error = form(page).locator('.attachments [data-api-error]');
    await expect(error.locator('[data-error-summary]')).toHaveText('This file is too large. Choose a smaller file.');
    expect(await error.innerText()).not.toContain('ARTIFACT_TOO_LARGE');
    await error.locator('summary').click();
    await expect(error.locator('[data-error-details] code')).toHaveText('ARTIFACT_TOO_LARGE');
    await expect(error.locator('[data-error-details]')).toContainText('1048576');
    await expect(form(page).locator('.attachment-list')).toBeEmpty();

    expect(state.uploaded).toBe(false);
    expect(state.errors).toEqual([]);
  });

  test(`${mode}: browser drag and drop uploads image`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected]});
    await openAndPreview(page);

    // Drop PNG onto the attachments box
    await dropFile(page, '[data-published-start] .attachments', 'dropped.png', 'image/png', pngBuffer);

    // Verify thumbnail and upload
    await expect(form(page).locator('img.attachment-thumb')).toBeVisible();
    await expect(form(page).locator('.attachment-list')).toContainText(aid);
    expect(state.uploaded).toBe(true);
    expect(state.bytes).toEqual([...pngBuffer]);
    expect(state.errors).toEqual([]);
  });
}
