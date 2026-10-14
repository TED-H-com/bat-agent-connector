import {test, expect} from '@playwright/test';
import {publishedFixture, selected, dispatchProject, dispatchArtifact, publishedSha} from './repository-start-fixture';
const form = (page: any) => page.locator('[data-published-start]');
const prompt = (page: any) => form(page).getByRole('textbox', {name: 'Original instructions', exact: true});
const apply = (page: any) => form(page).getByRole('button', {name: 'Start from this version', exact: true});
async function open(page: any) {await page.goto('/dashboard/#/dispatch/'+dispatchProject); await expect(form(page)).toBeVisible();}
async function preview(page: any) {
  await form(page).getByRole('textbox', {name: 'Published branch ref'}).fill('refs/heads/main');
  await form(page).getByRole('button', {name: 'Preview published version'}).click();
  await expect(form(page).locator('[data-published-preview]')).toContainText(publishedSha);
}
async function attach(page: any) {
  const select = form(page).getByRole('combobox', {name: 'Uploaded attachment'});
  await expect(select.locator('option')).toHaveCount(2);
  await select.selectOption(`${dispatchArtifact.artifact_id}:1`);
  await form(page).getByRole('button', {name: 'Add attachment', exact: true}).click();
}
for (const native of [false, true]) {
  const mode = native ? 'IPC' : 'HTTP';
  test(`${mode}: project entry scopes destinations and freezes prompt, inputs and advanced settings`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, lost: true, bindings: [selected, {...selected, repository: 'unrelated/project'}]});
    await page.goto('/dashboard/#/project/'+dispatchProject);
    await page.getByRole('link', {name: 'Quick project dispatch'}).click();
    const binding = form(page).getByRole('combobox', {name: 'Repository · host · workspace ID'});
    await expect(binding).toHaveValue(JSON.stringify(selected)); await expect(binding.locator('option')).toHaveCount(2);
    await preview(page); await prompt(page).fill('  Exact project request.\nPreserve every word.  ');
    await form(page).getByText('Advanced settings', {exact: true}).click();
    await form(page).getByRole('textbox', {name: 'Model (optional)'}).fill('selected-model');
    await form(page).getByRole('textbox', {name: 'Title (optional)'}).fill('Project work');
    await attach(page); await expect(form(page).locator('.attachment-list')).toContainText('notes.txt');
    await page.reload(); await expect(prompt(page)).toHaveValue('  Exact project request.\nPreserve every word.  ');
    await expect(form(page).getByRole('textbox', {name: 'Model (optional)'})).toHaveValue('selected-model');
    await expect(form(page).locator('.attachment-list')).toContainText('notes.txt');
    await preview(page); await apply(page).click(); await expect(form(page)).toContainText('Lost published start reply');
    const original = state.posts[0];
    expect(original.body.params).toMatchObject({project_id: dispatchProject, artifacts: [dispatchArtifact], model: 'selected-model', title: 'Project work'});
    expect(original.body.preconditions.expected_project_version).toBe(1);
    state.project.archived = true; state.project.version++; state.writes = false; state.unbound = true;
    await page.reload(); await expect(form(page)).toContainText('This project is archived');
    await expect(form(page).locator('.dispatch-fixed-inputs')).toContainText(dispatchArtifact.digest);
    await form(page).getByRole('button', {name: 'Retry original request'}).click(); await expect(form(page)).toContainText('Start confirmed');
    expect(state.posts[1]).toEqual(original); await page.reload(); await expect(form(page)).toContainText('Start confirmed');
    expect(state.posts).toHaveLength(2); expect(state.errors).toEqual([]);
  });
  test(`${mode}: ambiguous and absent project bindings never guess a destination`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true}); await open(page);
    const binding = form(page).getByRole('combobox', {name: 'Repository · host · workspace ID'});
    await expect(binding).toHaveValue(''); await expect(apply(page)).toBeDisabled();
    await binding.selectOption(JSON.stringify(selected)); await preview(page); await prompt(page).fill('Keep draft');
    state.project.repositories = ['unbound/repository']; state.project.version++;
    state.events.push({seq: 1, resource_type: 'project', resource_id: dispatchProject, kind: 'project.updated'});
    await expect(form(page)).toContainText('This project has no available destination');
    await expect(form(page).locator('[data-published-preview]')).toBeEmpty(); await expect(apply(page)).toBeDisabled();
    await expect(prompt(page)).toHaveValue('Keep draft'); expect(state.posts).toEqual([]);
  });
  test(`${mode}: failed project refresh holds drafts and event checkpoint`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected]}); await open(page); await preview(page); await prompt(page).fill('Draft');
    await prompt(page).focus(); state.failProject = true;
    state.events.push({seq: 1, resource_type: 'project', resource_id: dispatchProject, kind: 'project.updated'});
    await expect(form(page)).toContainText('Project read failed'); await expect(apply(page)).toBeDisabled(); expect(state.after).toBe(0);
    await expect(prompt(page)).toHaveValue('Draft'); await expect(prompt(page)).toBeFocused();
    state.failProject = false; await expect.poll(() => state.after).toBe(1); await expect(apply(page)).toBeEnabled();
  });
  test(`${mode}: pending or unready attachment selection cannot dispatch`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected], delayArtifact: true});
    await open(page); await preview(page); await prompt(page).fill('Work'); await attach(page);
    await expect.poll(() => Boolean(state.holdArtifact)).toBe(true); await expect(apply(page)).toBeDisabled();
    state.unreadyArtifact = true; state.holdArtifact(); await expect(form(page).locator('.attachments')).toContainText('Finish uploading');
    expect(state.posts).toEqual([]);
    state.delayArtifact = false; state.unreadyArtifact = false; await attach(page);
    await expect(form(page).locator('.attachment-list')).toContainText('notes.txt'); await expect(apply(page)).toBeEnabled();
    state.bad = 'artifacts'; await apply(page).click(); await expect(form(page)).toContainText('does not match');
    await expect(form(page).getByRole('button', {name: 'Prepare another published version'})).toBeHidden();
  });
  test(`${mode}: principal and project changes isolate drafts and old central cannot dispatch`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected]}); await open(page); await prompt(page).fill('Private draft');
    await page.goto('/dashboard/#/dispatch/prj_'+'4'.repeat(20)); await expect(prompt(page)).toHaveValue('');
    await open(page); await expect(prompt(page)).toHaveValue('Private draft');
    state.principal = 'other-person'; await page.reload(); await expect(prompt(page)).toHaveValue('');
    state.oldCentral = true; await page.reload(); await expect(form(page)).toContainText('Update Connector to use project dispatch.');
    await expect(apply(page)).toBeDisabled(); expect(state.posts).toEqual([]);
  });
  test(`${mode}: a receipt for a different project cannot replace the fixed request`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected], bad: 'project'});
    await open(page); await preview(page); await prompt(page).fill('Keep this project'); await apply(page).click();
    await expect(form(page)).toContainText('does not match');
    await expect(form(page).getByRole('button', {name: 'Prepare another published version'})).toBeHidden();
    state.bad = null; await form(page).getByRole('button', {name: 'Retry original request'}).click();
    await expect(form(page)).toContainText('Start confirmed'); expect(state.posts[1]).toEqual(state.posts[0]);
  });
  for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) test(`${mode}: project dispatch layout ${locale} ${width}`, async ({page}) => {
    const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected]});
    await page.setViewportSize({width, height: 900});
    await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
    await open(page); await form(page).locator('input').first().fill('refs/heads/main');
    await form(page).locator('textarea').fill(locale === 'zh-TW' ? '請保留原始指示與附件，在選定版本開工。' : 'Keep the exact request and attachments on this version.');
    await form(page).locator('summary').click(); await form(page).getByRole('textbox', {name: locale === 'zh-TW' ? '模型（選填）' : 'Model (optional)'}).fill('model-choice');
    await page.evaluate(async () => {
      (document.activeElement as HTMLElement)?.blur(); window.scrollTo(0, 0);
      await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path: `test-results/dispatch-${mode}-${locale}-${width}.png`, fullPage: true});
    expect(state.posts).toEqual([]); expect(state.errors).toEqual([]);
  });
}
