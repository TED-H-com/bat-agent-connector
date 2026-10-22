import {readFileSync} from 'node:fs';
import type {Page} from '@playwright/test';

export async function serveGateBaseline(page: Page) {
  const directory = process.env.ACTION_GATE_BASELINE;
  if (!directory) return;
  for (const [path, file] of [['**/dashboard/app.js', 'app.js'], ['**/src/central-service-status.js', 'central-service-status.js']])
    await page.route(path, route => route.fulfill({body: readFileSync(`${directory}/${file}`), contentType: 'text/javascript'}));
}
