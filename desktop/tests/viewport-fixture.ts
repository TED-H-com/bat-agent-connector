import type {Page} from '@playwright/test';

// Simulated browser API only. The product owns all classes, styles and layout.
export async function installViewport(page: Page) {
  await page.addInitScript(() => {
    const viewport = Object.assign(new EventTarget(), {width: innerWidth, height: innerHeight, offsetTop: 0, offsetLeft: 0, scale: 1});
    Object.defineProperty(window, 'visualViewport', {configurable: true, value: viewport});
    (window as any).resizeVisualViewport = (value: Record<string, number>) => {
      Object.assign(viewport, value);
      viewport.dispatchEvent(new Event('resize'));
      viewport.dispatchEvent(new Event('scroll'));
    };
  });
}
export async function resizeViewport(page: Page, value: {height: number, offsetTop?: number, scale?: number}) {
  await page.evaluate(value => (window as any).resizeVisualViewport({offsetTop: 0, scale: 1, ...value}), value);
}
