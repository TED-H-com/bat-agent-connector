import {test, expect} from '@playwright/test';
import {taskFixture} from './task-controls-fixture';
import {STRINGS} from '../src/i18n.js';

const id = (n: number) => 'op_' + n.toString(16).padStart(32, '0');
const operations = (count = 5) => Array.from({length: count}, (_, i) => ({
  operation_id: id(9 - i), action: 'setup.connection.apply', actor: 'history-person',
  status: 'needs_attention', created_at: 100, status_reason: `Original connection ${9 - i}`,
  target: {installation_ref: 'history-fixture'}, external_refs: {native_effects_sent: true},
  steps: [{name: 'native_effects', status: 'uncertain'}],
}));

for (const native of [false, true]) for (const locale of ['en', 'zh-TW']) {
  test.describe(`operation cursors ${native ? 'IPC' : 'HTTP'} ${locale}`, () => {
    test.use({locale, viewport: {width: locale === 'en' ? 1440 : 390, height: 900}});
    const copy = STRINGS[locale];

    test('equal-time history reaches every original operation without writes', async ({page}) => {
      const state = await taskFixture(page, native, {list: operations()});
      await page.clock.install();
      await page.goto('/dashboard/#/operations');
      const rows = page.locator('[data-operation-list] [data-row-key]');
      await expect(rows).toHaveCount(2);
      const more = page.getByRole('button', {name: copy.operations_more, exact: true});
      await more.click();
      await expect(rows).toHaveCount(4);
      await more.click();
      await expect(rows).toHaveCount(5);
      await expect(more).toBeHidden();
      expect(await rows.evaluateAll(nodes => nodes.map(node => (node as HTMLElement).dataset.rowKey)))
        .toEqual([9, 8, 7, 6, 5].map(id));
      const cursors = state.reads.filter((path: string) => path.startsWith('/operations?'))
        .map((path: string) => new URL(path, 'http://fixture').searchParams);
      expect(cursors.slice(1).map((p: URLSearchParams) => [p.get('before'), p.get('before_id')]))
        .toEqual([['100', id(8)], ['100', id(6)]]);
      await page.locator(`[data-row-key="${id(5)}"]`).click();
      await page.getByRole('link', {name: copy.global_open_operation, exact: true}).click();
      await expect(page.locator('[data-connection-recovery]')).toHaveAttribute('data-connection-recovery', 'unknown');
      expect(state.reads).toContain('/operations/' + id(5));
      expect(state.posts).toEqual([]);
      expect(state.errors).toEqual([]);
    });

    test('empty visible pages retain bounded explicit continuation', async ({page}) => {
      const state = await taskFixture(page, native, {list: operations(), hiddenIds: [9, 8, 7, 6].map(id)});
      await page.clock.install();
      await page.goto('/dashboard/#/operations');
      const more = page.getByRole('button', {name: copy.operations_more, exact: true});
      await expect(page.getByText(copy.operations_empty_page, {exact: true})).toBeVisible();
      await expect(page.getByText(copy.operations_empty, {exact: true})).toHaveCount(0);
      expect(state.reads.filter((p: string) => p.startsWith('/operations?'))).toHaveLength(1);
      await more.click();
      await expect(more).toBeEnabled();
      await expect(more).toBeFocused();
      await expect(page.locator('[data-operation-list] [data-row-key]')).toHaveCount(0);
      expect(state.reads.filter((p: string) => p.startsWith('/operations?'))).toHaveLength(2);
      await more.click();
      await expect(page.locator('[data-operation-list] [data-row-key]')).toHaveCount(1);
      await expect(more).toBeHidden();
      await expect(page.locator('[data-error-summary]')).toHaveCount(0);
      for (const hidden of [9, 8, 7, 6]) await expect(page.locator('main')).not.toContainText(`Original connection ${hidden}`);
      expect(state.posts).toEqual([]);
      expect(state.errors).toEqual([]);
    });

    test('a repeated cursor retains the previous window and permits an explicit refresh', async ({page}) => {
      const state = await taskFixture(page, native, {list: operations(), listResponse: (data: any, url: URL) =>
        url.searchParams.has('before') ? {...data, next_before: 100, next_before_id: id(8)} : data});
      await page.clock.install();
      await page.goto('/dashboard/#/operations');
      const rows = page.locator('[data-operation-list] [data-row-key]');
      await expect(rows).toHaveCount(2);
      const more = page.getByRole('button', {name: copy.operations_more, exact: true});
      await more.click();
      await expect(page.locator('[data-error-summary]')).toBeVisible();
      await expect(more).toBeDisabled();
      await expect(rows).toHaveCount(2);
      await expect(page.locator(`[data-row-key="${id(7)}"]`)).toHaveCount(0);
      state.listResponse = null;
      await page.getByRole('button', {name: copy.global_refresh, exact: true}).click();
      await expect(page.locator('[data-error-summary]')).toHaveCount(0);
      await more.click();
      await expect(rows).toHaveCount(4);
      expect(state.posts).toEqual([]);
      expect(state.errors).toEqual([]);
    });

    test('refresh rebuilds paired cursors while preserving search and selected original', async ({page}) => {
      const state = await taskFixture(page, native, {list: operations(6)});
      await page.goto('/dashboard/#/operations/attention');
      const more = page.getByRole('button', {name: copy.operations_more, exact: true});
      await more.click();
      await expect(page.locator('[data-operation-list] [data-row-key]')).toHaveCount(4);
      const search = page.getByRole('searchbox', {name: copy.global_search_operations, exact: true});
      await search.fill('setup.connection.apply');
      await page.locator(`[data-row-key="${id(7)}"]`).click();
      await expect(page.locator('.ui-inspector')).toHaveAttribute('data-operation-id', id(7));
      state.list.unshift({...state.list[0], operation_id: id(11), status_reason: 'New original connection'});
      state.reads.length = 0;
      // Event refresh remains available while the mobile inspector makes the list inert.
      state.events.push({seq: 1, resource_type: 'operation', resource_id: id(11), kind: 'operation.accepted'});
      await expect.poll(() => state.after).toBe(1);
      await expect(page.locator(`[data-row-key="${id(11)}"]`)).toBeVisible();
      await expect(search).toHaveValue('setup.connection.apply');
      await expect(page.locator('.ui-inspector')).toHaveAttribute('data-operation-id', id(7));
      const queries = state.reads.filter((p: string) => p.startsWith('/operations?'))
        .map((p: string) => new URL(p, 'http://fixture').searchParams);
      expect(queries.map((p: URLSearchParams) => p.get('before_id'))).toEqual([null, id(9)]);
      expect(queries.every((p: URLSearchParams) => p.get('status') === 'needs_attention,uncertain')).toBe(true);
      // Close the mobile inspector before continuing through the retained list.
      await page.keyboard.press('Escape');
      await more.click();
      await expect(page.locator('[data-operation-list] [data-row-key]')).toHaveCount(6);
      expect(state.posts).toEqual([]);
      expect(state.errors).toEqual([]);
    });
  });
}

for (const native of [false, true]) test(`legacy timestamp history still loads ${native ? 'IPC' : 'HTTP'}`, async ({page}) => {
  const list = operations().map((op, i) => ({...op, created_at: 100 - i}));
  const state = await taskFixture(page, native, {list, legacyCursors: true});
  await page.clock.install();
  await page.goto('/dashboard/#/operations');
  const more = page.getByRole('button', {name: STRINGS.en.operations_more, exact: true});
  await more.click();
  await expect(page.locator('[data-operation-list] [data-row-key]')).toHaveCount(4);
  expect(state.reads.some((p: string) => p.includes('before=99'))).toBe(true);
  expect(state.reads.some((p: string) => p.includes('before_id='))).toBe(false);
  expect(state.posts).toEqual([]);
});
