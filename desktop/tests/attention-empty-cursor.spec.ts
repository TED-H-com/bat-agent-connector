import {test, expect} from '@playwright/test';
import {attentionFixture, mountAttention} from './attention-fixture.ts';

for (const locale of ['en-US', 'zh-TW']) test(`empty operation page continues by keyboard in ${locale}`, async ({page}) => {
  const fixture = attentionFixture(), cursorId = 'op_' + 'f'.repeat(32);
  const reads: string[] = [];
  await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
  await mountAttention(page, false, fixture, async input => {
    const url = new URL(input.path, 'http://fixture');
    if (url.pathname === '/operations') {
      reads.push(input.path);
      const next = url.searchParams.get('before') === '42.5' && url.searchParams.get('before_id') === cursorId;
      return {status: 200, data: {operations: next ? [fixture.data.operations[0]] : [],
        next_before: next ? null : 42.5, next_before_id: next ? null : cursorId}};
    }
    return fixture.dispatch(input);
  });
  await page.goto('/dashboard/');
  const section = page.locator('[data-inbox-group="problems"]');
  await expect(section).toBeVisible();
  await expect(section).toContainText(locale === 'zh-TW' ? '可能還有更多項目，請載入下一頁。' : 'More items may exist. Load the next page.');
  const more = section.getByRole('button', {name: locale === 'zh-TW' ? '載入更多' : 'Load more', exact: true});
  await expect(more).toBeVisible();
  // Traverse the actual tab order rather than focusing the button programmatically.
  for (let i = 0; i < 80 && !await more.evaluate(node => node === document.activeElement); i++) await page.keyboard.press('Tab');
  await expect(more).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(section.locator(`[data-inbox-key="${fixture.data.operations[0].operation_id}"]`)).toBeVisible();
  await expect(more).toBeHidden();
  expect(reads.some(path => path.includes(`before=42.5&before_id=${cursorId}`))).toBe(true);
  expect(fixture.data.writes).toEqual([]);
});
