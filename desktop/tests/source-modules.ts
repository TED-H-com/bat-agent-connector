import type {Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import {stripTypeScriptTypes} from 'node:module';

// Serve the real source graph, retaining each module's original directory.
// Unknown package imports fail here instead of becoming an opaque browser 404.
export async function sourceModules(page: Page, prefix: string) {
  await page.route(`**/${prefix}/**`, async route => {
    let file = new URL(route.request().url()).pathname.slice(prefix.length + 2);
    if (file === 'transport.js') file = 'transport/index.ts';
    const packageFile = file.startsWith('@tauri-apps/api/');
    if (!/^[a-zA-Z0-9@/_.-]+\.(js|ts)$/.test(file)) throw Error('Unsupported source module: ' + file);
    let source = await readFile(new URL((packageFile ? '../node_modules/' : '../src/') + file, import.meta.url), 'utf8');
    if (file.endsWith('.ts')) source = stripTypeScriptTypes(source);
    source = source.replace(/(\bfrom\s*|\bimport\s*(?:\(\s*)?)(['"])([^'"]+)\2/g, (match, before, quote, specifier) => {
      let target: string;
      if (specifier === '@tauri-apps/api/core') target = `/${prefix}/@tauri-apps/api/core.js`;
      else if (specifier.startsWith('.')) target = new URL(specifier, `http://fixture/${prefix}/${file}`).pathname;
      else throw Error('Unmapped source import: ' + specifier);
      if (!target.startsWith(`/${prefix}/`)) throw Error('Source import escapes its module graph');
      return before + quote + target + quote;
    });
    if (file === 'pm-workspace.js') source = source.replace('function durableForm(', 'export function durableForm(');
    await route.fulfill({contentType: 'text/javascript', body: source});
  });
}
