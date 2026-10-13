import type {Page} from '@playwright/test';

// datetime-local inputs are interpreted in the browser's zone, which can differ
// from the Node runner (for example when a spec pins timezoneId).
export function futureLocalTime(page: Page) {
  return page.evaluate(() => {
    const deadline = new Date(Date.now() + 3600000);
    return new Date(deadline.getTime() - deadline.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  });
}
