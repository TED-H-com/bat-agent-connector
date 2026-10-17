import type {Page} from '@playwright/test';

/**
 * Installs window.__bodyCompletedUrls tracking on the page via addInitScript.
 * Must be called before navigating or reloading the page.
 */
export async function installBodyCompletedCounter(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    (window as any).__bodyCompletedUrls = [];
    window.fetch = async function(...args: any[]) {
      const response = await originalFetch.apply(this, args as [any, any]);
      const url = typeof args[0] === 'string' ? args[0] : (args[0] && (args[0] as any).url) || '';
      const originalJson = response.json.bind(response);
      response.json = async function() {
        const data = await originalJson();
        (window as any).__bodyCompletedUrls.push(url);
        return data;
      };
      return response;
    };
  });
}

/**
 * Counts how many response bodies with the given path prefix have completed JSON parsing.
 */
export async function countCompletedBodies(page: Page, prefix: string): Promise<number> {
  return await page.evaluate(p => {
    const urls = (window as any).__bodyCompletedUrls || [];
    return urls.filter((u: string) => u.includes(p)).length;
  }, prefix);
}

/**
 * Waits until the completed body count for holdPath is exactly one higher than beforeCount,
 * then waits one animation frame.
 *
 * Assumption: no other read of holdPath completes between the count and the release of the held read.
 */
export async function syncHeldBodyCompletion(page: Page, holdPath: string, beforeCount: number): Promise<void> {
  await page.waitForFunction(
    ({prefix, target}) => {
      const urls = (window as any).__bodyCompletedUrls || [];
      return urls.filter((u: string) => u.includes(prefix)).length === target;
    },
    {prefix: holdPath, target: beforeCount + 1}
  );
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
}

/**
 * Waits until at least targetCount completed bodies matching prefix have been recorded,
 * then waits one animation frame.
 */
export async function syncCompletedBodies(page: Page, prefix: string, targetCount: number): Promise<void> {
  await page.waitForFunction(
    ({p, target}) => {
      const urls = (window as any).__bodyCompletedUrls || [];
      return urls.filter((u: string) => u.includes(p)).length >= target;
    },
    {p: prefix, target: targetCount}
  );
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
}
