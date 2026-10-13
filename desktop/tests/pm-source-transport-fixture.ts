import type {Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';

// Source-only component harnesses import the shared durable form and its module
// graph, but do not mount Brief exports. Keep that optional native dependency
// explicit; shared-home tests still exercise the real browser/native transport.
export async function unavailableBriefTransport(page:Page,sourcePrefix:string) {
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
