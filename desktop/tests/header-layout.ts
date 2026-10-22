import {test, expect, type Page, type TestInfo} from '@playwright/test';
import {writeFileSync} from 'node:fs';

export const headerFonts = ['DejaVu Sans', 'Noto Sans'] as const;

export function headerStatusLabels(copy: Record<string, string>) {
  return ['ui_not_connected', 'ui_connecting', 'ui_connected', 'ui_reconnecting',
    'ui_connection_refused', 'ui_service_error', 'ui_invalid_response',
    'managed_service_recovering', 'managed_service_retry', 'managed_service_identity'].map(key => copy[key]);
}

export async function useHeaderFont(page: Page, family: string) {
  // fonts.check() also succeeds for missing families. Loading a local face proves
  // the requested font exists rather than silently accepting a narrow fallback.
  const installed = await page.evaluate(async family => {
    try {
      const face = await new FontFace('Header layout font',
        `local("${family}"), local("${family} Regular")`).load();
      document.fonts.add(face);
      // CSSOM keeps the production style-src policy intact; inline style tags
      // are deliberately blocked by the shared application's CSP.
      document.documentElement.style.setProperty('--font-ui', '"Header layout font", sans-serif');
      return true;
    }
    catch { return false; }
  }, family);
  test.skip(!installed, `Header layout requires the installed ${family} font; no fallback measurement.`);
  await page.evaluate(() => document.fonts.ready);
}

export async function expectHeaderFits(page: Page, info: TestInfo, labels?: string[]) {
  await expect(page.locator('.central-service-scope')).toBeVisible();
  const measurements = await page.locator('.central-service-state').evaluate((state, labels) => {
    const original = state.textContent;
    const bounds = (selector: string) => {
      const element = document.querySelector(selector), rect = element?.getBoundingClientRect();
      return rect ? {x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height} : null;
    };
    try {
      // Authentication loss leaves the workspace route. Measure each canonical
      // label in its real mounted header synchronously, restoring it before the
      // browser paints or polling can run. State transitions have separate tests.
      return (labels || [original!]).map(label => {
        state.textContent = label;
        return {label, stateWidth: state.clientWidth, stateScrollWidth: state.scrollWidth,
          font: getComputedStyle(state).fontFamily, central: bounds('.central-service-status'),
          scope: bounds('.central-service-scope'), nav: bounds('#nav'), top: bounds('.top'),
          menu: bounds('#workspace-menu'), pending: bounds('.shell-pending'),
          documentWidth: document.documentElement.scrollWidth, viewport: innerWidth};
      });
    } finally { state.textContent = original; }
  }, labels);
  writeFileSync(info.outputPath('header-layout.json'), JSON.stringify(measurements, null, 2));
  for (const size of measurements) {
    expect(size.stateWidth, size.label).toBeGreaterThan(0);
    expect(size.stateScrollWidth, size.label).toBeLessThanOrEqual(size.stateWidth + 1);
    expect(size.scope!.width).toBeGreaterThan(0);
    expect(size.scope!.x).toBeGreaterThanOrEqual(0);
    expect(size.scope!.right).toBeLessThanOrEqual(size.viewport);
    expect(size.central!.y).toBeGreaterThanOrEqual(size.top!.y);
    expect(size.central!.bottom).toBeLessThanOrEqual(size.top!.bottom);
    expect(size.documentWidth, size.label).toBeLessThanOrEqual(size.viewport);
    for (const target of [size.menu, size.pending]) if (target?.width) {
      expect(target.width).toBeGreaterThanOrEqual(44);
      expect(target.height).toBeGreaterThanOrEqual(44);
    }
  }
}
