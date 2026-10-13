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
// A withdrawn workspace removes the sidebar context. Exercise its bookmarked view
// route anyway, so a hidden ancestor cannot make a source-withdrawal assertion pass.
export async function expectPmWithdrawn(page: Page, view: string, selector: string, section?: string, facts: string[] = []) {
  await page.evaluate(view => {const parts = location.hash.split('/'); const target = parts.slice(0, 3).join('/') + '/' + view; if (location.hash === target) window.dispatchEvent(new HashChangeEvent('hashchange')); else location.hash = target;}, view);
  await expect(page.locator(`[data-pm-region="${view}"]`)).toHaveJSProperty('hidden', false);
  if (section) {
    const fold = page.locator(`[data-pm-section="${section}"] > details`);
    if (!await fold.evaluate(node => (node as HTMLDetailsElement).open)) await fold.locator(':scope > summary').click();
  }
  await expect(page.locator(selector)).toHaveJSProperty('hidden', true);
  await expect(page.locator(selector)).toBeHidden();
  for (const fact of facts) await expect(page.locator('#main')).not.toContainText(fact, {useInnerText: true});
}

export async function openThreadTab(page: Page, tab: 'context' | 'inputs' | 'plan') {
  await expect(page.locator('[data-discussion-panel]')).toBeAttached();
  if (!await page.locator('[data-discussion-panel]').isVisible()) await page.locator('[data-discussion-panel-toggle]').click();
  const labels = {context: /^(Context|脈絡)$/, inputs: /^(Inputs|資訊)$/, plan: /^(Plan|計畫)$/};
  await page.getByRole('tab', {name: labels[tab]}).click();
}
export async function openThreadAnalysis(page: Page) {
  await openThreadTab(page, 'context');
  const panel = page.locator('[data-pm-native-analysis]');
  if (!await panel.count()) return;
  const disclosure = panel.locator('..');
  if (!await disclosure.evaluate(node => (node as HTMLDetailsElement).open)) await disclosure.locator(':scope > summary').click();
}
export async function openThreadBrief(page: Page) {
  await openThreadTab(page, 'plan');
  const fold = page.locator('[data-pm-section=pm_brief_title] > details');
  if (!await fold.evaluate(node => (node as HTMLDetailsElement).open)) await fold.locator(':scope > summary').click();
}
