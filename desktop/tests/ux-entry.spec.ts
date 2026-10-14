import {expectClientError} from './api-error-assertions.ts';
import {test, expect} from '@playwright/test';
import {publishedFixture, selected, dispatchProject} from './repository-start-fixture';

for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) test(`cold web connection remains readable ${locale} ${width}`, async ({browser}) => {
  const context = await browser.newContext({locale, viewport: {width, height: 900}});
  const page = await context.newPage();
  await page.goto('/dashboard/#/projects');
  const token = page.getByLabel(locale === 'zh-TW' ? 'API 權杖' : 'API token', {exact: true});
  await expect(token).toBeVisible(); await token.fill('fixture-only');
  await expect(page.locator('#nav a')).toHaveCount(1);
  await expect(page.locator('#nav a.on')).toHaveAttribute('href', '#/settings');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: `test-results/ux-entry-${locale}-${width}.png`, fullPage: true});
  await context.close();
});

for (const native of [false, true]) test(`new project can select its configured dispatch repository (${native ? 'IPC' : 'HTTP'})`, async ({page}) => {
  const state = await publishedFixture(page, native, {dispatch: true, scopes: ['observe', 'start', 'manage'],
    bindings: [selected, {...selected, repository: 'another/project'}]});
  await page.goto('/dashboard/#/projects');
  const repository = page.getByRole('combobox', {name: 'Repository for dispatch (optional)'});
  await expect(repository).toHaveValue(''); // Even a configured repository needs the user's choice.
  await repository.selectOption(selected.repository);
  await page.getByRole('textbox', {name: 'New project name'}).fill('First project');
  await page.getByRole('textbox', {name: 'New project name'}).press('Enter');
  await expect(page).toHaveURL(new RegExp('/project/'+dispatchProject+'$'));
  expect(state.posts[0].body.params).toEqual({name: 'First project', repositories: [selected.repository]});
  await page.getByRole('link', {name: 'Quick project dispatch'}).click();
  const destination = page.getByRole('combobox', {name: 'Repository · host · workspace ID'});
  await expect(destination).toHaveValue(JSON.stringify(selected));
  await expect(destination.locator('option')).toHaveCount(2);
  expect(state.posts).toHaveLength(1); expect(state.errors).toEqual([]);
});

for (const native of [false, true]) test(`delivery accepts pasted PR links and reports unavailable data (${native ? 'IPC' : 'HTTP'})`, async ({page}) => {
  const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected]});
  await page.goto('/dashboard/#/delivery');
  await page.getByRole('textbox', {name: 'Repository or GitHub PR URL'}).fill('https://github.com/example/project/pull/24#discussion');
  await page.getByRole('textbox', {name: 'Repository or GitHub PR URL'}).press('Tab');
  await expect(page.getByRole('textbox', {name: 'Repository or GitHub PR URL'})).toHaveValue('example/project');
  await expect(page.getByRole('textbox', {name: 'PR number', exact: true})).toHaveValue('24');
  await page.getByRole('button', {name: 'Load PR', exact: true}).click();
  await expectClientError(page, 'The full details of this PR are unavailable.');
  expect(state.reads.some(path => path.replace(/\?$/, '') === '/repositories/example/project/pulls/24')).toBe(true);
  expect(state.posts).toEqual([]);
  const before = state.reads.length;
  await page.getByRole('textbox', {name: 'Repository or GitHub PR URL'}).fill('https://untrusted.example/example/project/pull/24');
  await page.getByRole('button', {name: 'Load PR', exact: true}).click();
  await expect(page.getByText('Enter a repository and PR number', {exact: false})).toBeVisible();
  expect(state.reads.slice(before).some(p => p.startsWith('/repositories/'))).toBe(false);
  await page.goto('/dashboard/#/cleanup');
  await expect(page.locator('body')).not.toContainText(/\bnull\b/);
});
