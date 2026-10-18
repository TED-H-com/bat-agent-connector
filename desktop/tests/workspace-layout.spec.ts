import {installViewport, resizeViewport} from './viewport-fixture.ts';
import { test, expect } from '@playwright/test';
import { workspaceFixture, mountWorkspace, workspaceRoute } from './workspace-fixture.ts';

test.describe('Workspace Layout Controls', () => {
  test('sidebar pointer resizing, persistence, and cancel drag', async ({ page }) => {
    const f = workspaceFixture('en');
    await mountWorkspace(page, false, f);
    await page.setViewportSize({ width: 1200, height: 900 });
    await page.goto('/dashboard/' + workspaceRoute);

    const resizer = page.locator('.workspace-resizer');
    await expect(resizer).toBeVisible();
    await expect(resizer).toHaveAttribute('role', 'separator');
    await expect(resizer).toHaveAttribute('aria-orientation', 'vertical');

    const initialVal = await resizer.getAttribute('aria-valuenow');
    expect(Number(initialVal)).toBe(248);

    // 1. Pointer drag to resize sidebar
    const box = await resizer.boundingBox();
    expect(box).not.toBeNull();
    const startX = box!.x + box!.width / 2;
    const startY = box!.y + 100;

    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX + 60, startY);
    await page.mouse.up();

    const resizedVal = Number(await resizer.getAttribute('aria-valuenow'));
    expect(resizedVal).toBeGreaterThanOrEqual(Number(initialVal) + 50);
    expect(resizedVal).toBeLessThanOrEqual(Number(initialVal) + 70);
    await page.screenshot({ path: 'test-results/workspace-desktop-resized-sidebar-1200.png', fullPage: true });

    // Verify persisted in localStorage across reload
    await page.reload();
    await expect(resizer).toBeVisible();
    const reloadedVal = Number(await resizer.getAttribute('aria-valuenow'));
    expect(reloadedVal).toBe(resizedVal);

    // 2. Cancel drag on Escape
    const reloadedBox = (await resizer.boundingBox())!;
    const rx = reloadedBox.x + reloadedBox.width / 2;
    const ry = reloadedBox.y + 100;
    await page.mouse.move(rx, ry);
    await page.mouse.down();
    await page.mouse.move(rx + 80, ry);
    // Press Escape during drag
    await page.keyboard.press('Escape');
    await page.mouse.up();

    const afterCancelVal = Number(await resizer.getAttribute('aria-valuenow'));
    expect(afterCancelVal).toBe(reloadedVal);
  });

  test('sidebar keyboard resizing, limits, and reset', async ({ page }) => {
    const f = workspaceFixture('en');
    await mountWorkspace(page, false, f);
    await page.setViewportSize({ width: 1200, height: 900 });
    await page.goto('/dashboard/' + workspaceRoute);

    const resizer = page.locator('.workspace-resizer');
    await resizer.focus();

    // ArrowRight increases width
    const beforeRight = Number(await resizer.getAttribute('aria-valuenow'));
    await page.keyboard.press('ArrowRight');
    const afterRight = Number(await resizer.getAttribute('aria-valuenow'));
    expect(afterRight).toBe(beforeRight + 16);

    // ArrowLeft decreases width
    await page.keyboard.press('ArrowLeft');
    const afterLeft = Number(await resizer.getAttribute('aria-valuenow'));
    expect(afterLeft).toBe(beforeRight);

    // Home jumps to minimum (200px)
    await page.keyboard.press('Home');
    expect(Number(await resizer.getAttribute('aria-valuenow'))).toBe(200);

    // End jumps to maximum bounded by desktop main minimum size
    await page.keyboard.press('End');
    const maxVal = Number(await resizer.getAttribute('aria-valuenow'));
    expect(maxVal).toBeLessThanOrEqual(500);
    // Desktop main min size (360px): 1200 - 360 = 840, clamped to max 500
    expect(maxVal).toBe(500);

    // Double-click resets to default 248px
    await resizer.dblclick();
    expect(Number(await resizer.getAttribute('aria-valuenow'))).toBe(248);
  });

  test('sidebar mobile disabled at 390px width', async ({ page }) => {
    const f = workspaceFixture('en');
    await mountWorkspace(page, false, f);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/dashboard/' + workspaceRoute);

    const resizer = page.locator('.workspace-resizer');
    await expect(resizer).toBeHidden();
  });

  test('mobile conversation info independent collapse', async ({ page }) => {
    const f = workspaceFixture('zh-TW');
    await mountWorkspace(page, false, f);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/dashboard/' + workspaceRoute);

    const infoToggle = page.locator('.workspace-info-toggle');
    await expect(infoToggle).toBeVisible();
    expect((await infoToggle.boundingBox())!.height).toBeGreaterThanOrEqual(44);

    const meta = page.locator('.workspace-session-meta');
    await expect(meta).toBeVisible();

    // Collapse conversation info
    await infoToggle.click();
    await expect(page.locator('.workspace-session-heading')).toHaveClass(/workspace-info-collapsed/);
    await expect(meta).toBeHidden();
    await page.screenshot({ path: 'test-results/workspace-mobile-info-collapsed-390.png', fullPage: true });

    // Expand conversation info
    await infoToggle.click();
    await expect(page.locator('.workspace-session-heading')).not.toHaveClass(/workspace-info-collapsed/);
    await expect(meta).toBeVisible();
  });

  test('mobile composer independent collapse, draft indicator, and draft preservation', async ({ page }) => {
    const f = workspaceFixture('en');
    await mountWorkspace(page, false, f);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/dashboard/' + workspaceRoute);

    const composerToggle = page.locator('.workspace-composer-toggle');
    await expect(composerToggle).toBeVisible();
    expect((await composerToggle.boundingBox())!.height).toBeGreaterThanOrEqual(44);

    const textarea = page.getByPlaceholder('Message for this managed session…');
    await expect(textarea).toBeVisible();

    // Type a draft
    await textarea.fill('Testing draft preservation and indicator');

    // Collapse composer
    await composerToggle.click();
    await expect(page.locator('.workspace-composer')).toHaveClass(/workspace-composer-collapsed/);
    await expect(textarea).toBeHidden();

    // Check draft indicator
    await expect(composerToggle).toHaveClass(/has-draft/);
    await expect(composerToggle).toHaveAttribute('aria-label', /Draft/);
    await expect(composerToggle).not.toContainText('Collapse');
    await page.screenshot({ path: 'test-results/workspace-mobile-composer-collapsed-draft-390.png', fullPage: true });

    // Expand composer again
    await composerToggle.click();
    await expect(page.locator('.workspace-composer')).not.toHaveClass(/workspace-composer-collapsed/);
    await expect(textarea).toBeVisible();
    await expect(textarea).toHaveValue('Testing draft preservation and indicator');
    await expect(textarea).toBeFocused();

    // Clearing draft updates indicator
    await textarea.fill('');
    await composerToggle.click();
    await expect(composerToggle).not.toHaveClass(/has-draft/);
    await expect(composerToggle).not.toHaveAttribute('aria-label', /Draft/);
  });

  test('visualViewport keyboard adaptation without treating pinch zoom as keyboard', async ({ page }) => {
    const f = workspaceFixture('en');
    await installViewport(page);
    await mountWorkspace(page, false, f);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/dashboard/' + workspaceRoute);
    await expect(page.locator('#workspace-message-input textarea')).toBeVisible();
    await expect(page.locator('body')).not.toHaveClass(/mobile-keyboard/);
    await resizeViewport(page, {height: 450});
    await expect(page.locator('body')).toHaveClass(/mobile-keyboard/);
    await resizeViewport(page, {height: 450, scale: 1.6});
    await expect(page.locator('body')).not.toHaveClass(/mobile-keyboard/);
    expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--workspace-visible-height').trim())).toBe('');
  });
});
