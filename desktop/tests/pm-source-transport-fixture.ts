import type {Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import {transformWithOxc} from 'vite';

// Source-only component harnesses import the shared durable form and its module
// graph, but do not mount Brief exports. Keep that optional native dependency
// explicit; shared-home tests still exercise the real browser/native transport.
export async function unavailableBriefTransport(page:Page,sourcePrefix:string) {
  // The workspace uses the real event settlement helper. Source harnesses do
  // not run Vite, so compile this nested TypeScript dependency before serving it.
  await page.route(`**/${sourcePrefix}/state/events.ts`,async route=>{
    const source=await readFile(new URL('../src/state/events.ts',import.meta.url),'utf8');
    const {code}=await transformWithOxc(source,'events.ts');
    await route.fulfill({contentType:'text/javascript',body:code});
  });
  // The production durable form now imports the nested shared UI module graph.
  // Serve its real source while keeping each harness's existing API mocks intact.
  await page.route(`**/${sourcePrefix}/ui/*.js`,async route=>{
    const file=new URL(route.request().url()).pathname.slice(`/${sourcePrefix}/`.length);
    if(!/^ui\/[a-z0-9-]+\.js$/.test(file)){await route.abort();return;}
    await route.fulfill({contentType:'text/javascript',body:await readFile(new URL('../src/'+file,import.meta.url),'utf8')});
  });
  await page.route(`**/${sourcePrefix}/transport/index.ts`,route=>route.fulfill({contentType:'text/javascript',body:
    "export const nativeDesktop=false,nativeBriefExportSupport=false;export function nativeBriefSave(){throw Error('Brief saving is not available in this component harness');}"}));
}
