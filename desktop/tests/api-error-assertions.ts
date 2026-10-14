import {expect, type Locator, type Page} from '@playwright/test';

// Shared errors expose a localized summary and keep server diagnostics in Details.
// Preserve the original diagnostic assertion while checking the disclosure UI.
export async function expectErrorDetails(root: Page | Locator, message: string | RegExp, code?: string) {
  const error = root.locator('[data-api-error]').filter({hasText: message}).first();
  await expect(error.locator('[data-error-summary]')).toBeVisible();
  await expectNoNestedErrorDetails(error);
  const ownDetails = error.locator('details');
  const details = await ownDetails.count() ? ownDetails : error.locator('xpath=ancestor::details[1]');
  if (await details.getAttribute('open') === null) await details.locator(':scope > summary').click();
  await expect(error.locator('[data-error-details]')).toBeVisible();
  await expect(error.locator('[data-error-details]')).toContainText(message);
  if (code) await expect(error.locator('[data-error-details] code')).toHaveText(code);
}

export async function expectClientError(root: Page | Locator, message: string | RegExp) {
  const error = root.locator('[data-api-error]').filter({hasText: message}).first();
  await expect(error.locator('[data-error-summary]')).toBeVisible();
  await expect(error.locator('[data-error-summary]')).toContainText(message);
  await expectNoNestedErrorDetails(error);
  // The export action has Details; client errors still have no duplicate technical text.
  await expect(error.locator('code, [data-error-details] p')).toHaveCount(0);
}

export async function expectNoNestedErrorDetails(error: Locator) {
  // A caller already in Details reuses it, including the export action.
  expect(await error.evaluate(node => [...node.querySelectorAll('details')].every(details => !details.parentElement?.closest('details')))).toBe(true);
  await expect(error.locator('details details')).toHaveCount(0);
  await expect(error.locator('[data-export-diagnostics]')).toHaveClass(/\bghost\b/);
  expect(await error.locator('[data-export-diagnostics]').evaluate(node => Boolean(node.closest('details')))).toBe(true);
}
