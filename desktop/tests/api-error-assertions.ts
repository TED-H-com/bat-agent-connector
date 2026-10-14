import {expect, type Locator, type Page} from '@playwright/test';

// Shared errors expose a localized summary and keep server diagnostics in Details.
// Preserve the original diagnostic assertion while checking the disclosure UI.
export async function expectErrorDetails(root: Page | Locator, message: string | RegExp, code?: string) {
  const error = root.locator('[data-api-error]').filter({hasText: message}).first();
  await expect(error.locator('[data-error-summary]')).toBeVisible();
  const details = error.locator('details');
  if (await details.getAttribute('open') === null) await details.locator('summary').click();
  await expect(error.locator('[data-error-details]')).toBeVisible();
  await expect(error.locator('[data-error-details]')).toContainText(message);
  if (code) await expect(error.locator('[data-error-details] code')).toHaveText(code);
}

export async function expectClientError(root: Page | Locator, message: string | RegExp) {
  const error = root.locator('[data-api-error]').filter({hasText: message}).first();
  await expect(error.locator('[data-error-summary]')).toBeVisible();
  await expect(error.locator('[data-error-summary]')).toContainText(message);
  await expect(error.locator('details, code')).toHaveCount(0);
}
