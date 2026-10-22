import assert from 'node:assert/strict';
import type {Page} from '@playwright/test';

// Compare every entry, including globals, other namespaces and empty namespaces.
export async function storageSnapshot(page: Page) {
  return page.evaluate(() => ({localStorage: Object.entries(localStorage).sort(), sessionStorage: Object.entries(sessionStorage).sort()}));
}
export function assertStoragePreserved(before: Awaited<ReturnType<typeof storageSnapshot>>, after: typeof before, checkpointKey: string) {
  for (const area of ['localStorage', 'sessionStorage'] as const)
    assert.deepEqual(after[area].filter(([key]) => key !== checkpointKey), before[area].filter(([key]) => key !== checkpointKey), `${area}: every byte outside the current checkpoint must survive`);
}
