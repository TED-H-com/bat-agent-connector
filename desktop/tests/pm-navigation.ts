import {expect, type Page} from '@playwright/test';
// Exercise the real shell and disclosures; never force clicks into a hidden view.
export async function openPmSection(page: Page, view: string, section?: string) {
  await expect(page.locator('.pm-main > .ui-page-header')).toBeVisible();
  await expect.poll(() => page.locator('[data-pm-view]').count()).toBeGreaterThan(0);
  const entry = page.locator(`[data-pm-view="${view}"]`);
  if (!await entry.count()) return; // Capability-absence tests retain their zero-read assertions.
  if (!await entry.isVisible()) await page.locator('#workspace-menu').click();
  await entry.click();
  // A thread route remounts the workspace asynchronously before its sections exist.
  await expect(page.locator(`[data-pm-region="${view}"]`)).toBeVisible();
  if (section) {
    const details = page.locator(`[data-pm-section="${section}"] > details`);
    if (await details.count() && !await details.evaluate(node => (node as HTMLDetailsElement).open)) await details.locator(':scope > summary').click();
  }
}
export async function openPmPanel(page: Page, item: 'ui_change_phase' | 'ui_report' | 'ui_workspace_settings') {
  await expect(page.locator('.pm-main > .ui-page-header')).toBeVisible();
  if (item === 'ui_report') {
    await page.locator('[data-pm-report-menu]').click();
    await page.locator('[data-menu-key=versions]').click();
  } else {
    await page.locator('[data-pm-overflow]').click();
    await page.locator(`[data-menu-key=${item === 'ui_change_phase' ? 'phase' : 'settings'}]`).click();
  }
}
