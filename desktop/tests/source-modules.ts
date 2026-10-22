import type {Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import {stripTypeScriptTypes} from 'node:module';
import {parse} from 'acorn';

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
    const imports: {start: number; end: number; value: string}[] = [];
    function visit(node: any) {
      if (!node || typeof node !== 'object') return;
      if (['ImportDeclaration', 'ExportNamedDeclaration', 'ExportAllDeclaration', 'ImportExpression'].includes(node.type) && node.source) {
        if (typeof node.source.value !== 'string') throw Error('Source imports must have a literal module path');
        imports.push(node.source);
      }
      for (const child of Object.values(node)) {
        if (Array.isArray(child)) child.forEach(visit);
        else if (child && typeof child === 'object') visit(child);
      }
    }
    visit(parse(source, {ecmaVersion: 'latest', sourceType: 'module'}));
    for (const {start, end, value: specifier} of imports.sort((a, b) => b.start - a.start)) {
      let target: string;
      if (specifier === '@tauri-apps/api/core') target = `/${prefix}/@tauri-apps/api/core.js`;
      else if (specifier.startsWith('.')) target = new URL(specifier, `http://fixture/${prefix}/${file}`).pathname;
      else throw Error('Unmapped source import: ' + specifier);
      if (!target.startsWith(`/${prefix}/`)) throw Error('Source import escapes its module graph');
      source = source.slice(0, start) + JSON.stringify(target) + source.slice(end);
    }
    if (file === 'pm-workspace.js') source = source.replace('function durableForm(', 'export function durableForm(');
    await route.fulfill({contentType: 'text/javascript', body: source});
  });
}
